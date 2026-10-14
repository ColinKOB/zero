import AVFoundation
import Foundation
import Observation
import Speech
import UIKit

@MainActor
@Observable
final class CallEngine: NSObject {
    enum Phase: Equatable { case calling, listening, ended, failed(String) }

    var phase: Phase = .calling
    var muted = false {
        didSet {
            link?.micEnabled = !muted && !held
            local?.muted = muted
            if muted != oldValue { note("mute", ["on": muted, "from_system": fromSystem]) }
            #if !targetEnvironment(macCatalyst)
            if !fromSystem { CallKitBridge.shared.requestMute(muted) }
            #endif
        }
    }
    var speaker = false {
        didSet { if !fromSystem { RealtimeLink.setSpeaker(speaker) } }
    }
    var held = false
    @ObservationIgnored private var ringing: Set<UUID> = []
    @ObservationIgnored private var awaySent = false
    var externalRoute: String?
    @ObservationIgnored private var fromSystem = false
    var transcript: [(who: String, text: String)] = []

    let botID: String
    private let state: AppState
    @ObservationIgnored private var link: RealtimeLink?
    @ObservationIgnored private var local: OnDeviceCall?
    var voiceEngine: String?
    var preparing: (progress: Double, detail: String)?
    static weak var current: CallEngine?
    @ObservationIgnored var screens = 0
    var connectedAt: Date?
    var isOver: Bool {
        if case .failed = phase { return true }
        return phase == .ended
    }
    @ObservationIgnored private var audioActive = false
    @ObservationIgnored private var callID: String?
    @ObservationIgnored private var eventsTask: Task<Void, Never>?
    @ObservationIgnored private var micTask: Task<Void, Never>?
    @ObservationIgnored private let cuePlayer = CuePlayer()
    @ObservationIgnored private var cueGate = CueGate()
    @ObservationIgnored private var connectingTask: Task<Void, Never>?
    @ObservationIgnored private var hangupTask: Task<Void, Never>?
    @ObservationIgnored private var yielded = false
    @ObservationIgnored private var channelTypes: [String: Int] = [:]
    @ObservationIgnored private var backgroundObservers: [NSObjectProtocol] = []
    @ObservationIgnored private var diagnostics: [[String: Any]] = []
    @ObservationIgnored private let startedAt = Date()
    @ObservationIgnored private var hubCallID: String?
    @ObservationIgnored private let localID = "app-" + UUID().uuidString.lowercased()
    @ObservationIgnored private var flushTask: Task<Void, Never>?
    @ObservationIgnored private var sawCaption = false
    @ObservationIgnored private var finished = false
    @ObservationIgnored private var awaitingHub = false

    init(state: AppState, botID: String) {
        self.state = state
        self.botID = botID
        super.init()
    }

    func start() async {
        let client = state.client
        let engines: Task<HubClient.VoiceEngines?, Never>? = OnDeviceVoice.supported && state.callEngine != AppState.onDeviceEngine
            ? Task { await client.voiceEngines(timeout: 3) } : nil
        watchBackground()
        note("app_start", ["platform": Theme.onMac ? "Mac" : "iPhone", "os": UIDevice.current.systemVersion,
                           "app": SettingsView.version, "voice": state.callVoice, "bot": botID])
        guard await AVAudioApplication.requestRecordPermission() else {
            phase = .failed("The microphone is off for Zero. Turn it on in Settings.")
            note("failed", ["stage": "microphone_permission"])
            finishDiagnostics()
            return
        }
        let link = RealtimeLink()
        let offerTask: Task<String, Error>? = state.callEngine != AppState.onDeviceEngine
            ? Task { try await link.makeOffer() } : nil
        #if targetEnvironment(macCatalyst)
        note("system_call", ["used": "none_mac"])
        startConnectingSounds()
        #else
        // A real iPhone call: lock screen, Dynamic Island, CarPlay, hold for phone calls. CallKit activates the
        // audio session, and the connecting sounds may start once it has (audioReady).
        let bridge = CallKitBridge.shared
        bridge.engine = self
        let asked = Date()
        let accepted = await bridge.start(botName: state.bot(botID)?.name ?? "Zero", botID: botID)
        var system: [String: Any] = ["used": accepted ? "callkit" : "fallback", "accepted": accepted,
                                     "ms": Int(Date().timeIntervalSince(asked) * 1000),
                                     "ghosts_closed": bridge.ghostsClosed, "retried_provider": bridge.retriedProvider]
        if !accepted { system["foreign_calls"] = bridge.foreignCallCount }
        if let e = bridge.lastRefusal {
            system["error_domain"] = e.domain
            system["error_code"] = e.code
            system["error_description"] = e.localizedDescription
            if let under = e.userInfo[NSUnderlyingErrorKey] as? NSError {
                system["underlying"] = "\(under.domain) \(under.code): \(under.localizedDescription)"
            }
        }
        if !accepted {
            // Only a real phone call should stop a Zero call. Anything else the system refuses, the call goes on
            // without the lock-screen controls (Oct 8: a leftover call blocked every new one).
            let onPhone = bridge.phoneCallActive
            system["phone_call_active"] = onPhone
            note("system_call", system)
            if onPhone {
                offerTask?.cancel()
                link.close()
                phase = .failed("Zero can't call while you're on a phone call. Try again after it ends.")
                note("failed", ["stage": "phone_call_active"])
                finishDiagnostics()
                return
            }
            if !bridge.startWithoutSystemCall() {
                offerTask?.cancel()
                link.close()
                phase = .failed("Zero couldn't get the phone's audio. Try again in a moment.")
                note("failed", ["stage": "audio_session"])
                finishDiagnostics()
                return
            }
        } else {
            note("system_call", system)
        }
        #endif
        watchRoute()
        if let reason = await onDeviceReason(engines) {
            offerTask?.cancel()
            link.close()
            await startOnDevice(reason: reason)
            return
        }
        voiceEngine = "chatgpt"
        link.onChannelMessage = { [weak self] type in self?.channelTypes[type, default: 0] += 1 }
        do {
            let offer = try await (offerTask ?? Task { try await link.makeOffer() }).value
            note("timing", ["step": "offer_ready", "ms": msSinceStart])
            let call: (callID: String, answer: String, voice: String?)
            awaitingHub = true
            let defaults = UserDefaults.standard
            let chosen = CallRules.voiceChosen(current: defaults.string(forKey: "callVoice"),
                                               reported: defaults.string(forKey: "callVoiceReported"))
            do {
                call = try await state.client.startVoiceCall(bot: botID, offer: offer, voice: state.callVoice, chosen: chosen)
                awaitingHub = false
            } catch {
                awaitingHub = false
                var info: [String: Any] = ["error": HubClient.describe(error), "ms": msSinceStart]
                if let h = error as? HubClient.HubError { info["status"] = h.code }
                if let u = error as? URLError { info["url_error"] = u.code.rawValue }
                note("hub_start_failed", info)
                throw error
            }
            hubCallID = call.callID
            note("timing", ["step": "hub_answered", "ms": msSinceStart])
            if let used = call.voice, CallEngine.chatGPTVoices.contains(used) {
                if used != state.callVoice { note("voice", ["home": used, "device": state.callVoice]) }
                if used != state.callVoice { state.callVoice = used }
                defaults.set(used, forKey: "callVoiceReported")
            }
            guard phase == .calling else {
                link.close()
                await state.client.stopVoice(call: call.callID)
                finishDiagnostics()
                return
            }
            let client = state.client, id = call.callID
            link.onConnect = { [weak self] in
                Task { @MainActor in
                    guard let self else { return }
                    let waited = self.msSinceStart
                    Task { await client.voiceReady(call: id, waitedMs: waited) }
                    guard self.phase == .calling else { return }
                    self.note("timing", ["step": "audio_connected", "ms": self.msSinceStart])
                    self.phase = .listening
                    self.stopConnectingSounds()
                    #if !targetEnvironment(macCatalyst)
                    CallKitBridge.shared.connected()
                    #endif
                    self.updateProximity()
                    self.watchMic()
                }
            }
            try await link.accept(answer: call.answer)
            self.link = link
            callID = call.callID
        } catch {
            link.close()
            if phase == .calling, hubCallID == nil, OnDeviceVoice.supported,
               case .http(let code, _)? = error as? HubClient.HubError, code == 503 {
                note("fallback", ["to": "on-device", "error": String(describing: error).prefix(200).description])
                await startOnDevice(reason: "chatgpt_failed")
                return
            }
            stopConnectingSounds()
            if phase == .calling {
                note("failed", ["stage": "connect", "error": String(describing: error).prefix(300).description])
                if case .http(503, let why)? = error as? HubClient.HubError, !why.isEmpty {
                    phase = .failed(why)
                } else {
                    phase = .failed("Couldn't reach ChatGPT's voice. Hang up and try again in a moment.")
                }
                endSystemCall(failed: true)
            }
            finishDiagnostics()
            return
        }
        link.micEnabled = !muted
        link.onDisconnect = { [weak self] in
            guard let self, self.phase != .ended else { return }
            self.note("failed", ["stage": "audio_dropped", "ms": self.msSinceStart])
            self.stopOnHub()
            self.stopConnectingSounds()
            self.link?.close()
            self.link = nil
            self.phase = .failed("The call dropped. Hang up and call again.")
            self.endSystemCall(failed: true)
        }
        followEvents()
        if phase == .listening { watchMic() }
        Task { @MainActor [weak self] in
            try? await Task.sleep(for: .seconds(30))
            guard let self, self.phase == .calling else { return }
            self.stopConnectingSounds()
            self.note("failed", ["stage": "audio_never_connected", "ms": self.msSinceStart])
            self.stopOnHub()
            self.link?.close()
            self.link = nil
            self.phase = .failed("The call couldn't connect. Hang up and try again.")
            self.endSystemCall(failed: true)
            self.finishDiagnostics()
        }
    }

    func end(by: String = "app") {
        stopAudio()
        endSystemCall(failed: false)
        endFromSystem(by: by)
    }

    private func stopAudio() {
        stopConnectingSounds()
        cuePlayer.stop()
        hangupTask?.cancel()
        hangupTask = nil
        link?.close()
        link = nil
        if let l = local {
            local = nil
            l.stop()
            let store = OnDeviceVoice.shared
            store.inCall = false
            store.engine.useCPU = false
            Task { await store.engine.unloadCPU() }
            store.prepareGreeting()
        }
    }

    func endFromSystem(by: String = "system") {
        if !finished {
            if !channelTypes.isEmpty {
                note("datachannel", ["types": channelTypes.sorted { $0.value > $1.value }.prefix(20)
                    .map { "\($0.key):\($0.value)" }.joined(separator: " ")])
            }
            note("ended", ["by": by, "phase": "\(phase)", "ms": msSinceStart])
        }
        stopAudio()
        stopOnHub()
        heldTimeout?.cancel()
        heldTimeout = nil
        if let routeObserver { NotificationCenter.default.removeObserver(routeObserver) }
        if let interruptObserver { NotificationCenter.default.removeObserver(interruptObserver) }
        for o in backgroundObservers { NotificationCenter.default.removeObserver(o) }
        backgroundObservers = []
        routeObserver = nil
        interruptObserver = nil
        UIDevice.current.isProximityMonitoringEnabled = false
        if phase != .ended { phase = phase == .calling || phase == .listening ? .ended : phase }
        finishDiagnostics()
    }

    private func fail(_ text: String, problem: Bool = true) {
        note(problem ? "failed" : "ended", ["text": text, "ms": msSinceStart])
        stopAudio()
        phase = .failed(text)
        endSystemCall(failed: true)
        stopOnHub()
        heldTimeout?.cancel()
        UIDevice.current.isProximityMonitoringEnabled = false
        finishDiagnostics()
    }

    private func endSystemCall(failed: Bool) {
        #if !targetEnvironment(macCatalyst)
        CallKitBridge.shared.end(failed: failed)
        #else
        try? AVAudioSession.sharedInstance().setActive(false, options: .notifyOthersOnDeactivation)
        #endif
    }

    // MARK: Told by CallKit

    func audioReady() {
        audioActive = true
        if phase == .calling { startConnectingSounds() }
    }

    @ObservationIgnored private let ringback = Ringback()

    private func startConnectingSounds() {
        ringback.start()
    }

    private func stopConnectingSounds() {
        connectingTask?.cancel()
        connectingTask = nil
        ringback.stop()
    }

    func setMutedFromSystem(_ m: Bool) {
        fromSystem = true
        muted = m
        fromSystem = false
    }

    func setHeld(_ h: Bool) {
        if h != held { note("hold", ["on": h]) }
        held = h
        applyPause()
        #if !targetEnvironment(macCatalyst)
        if !CallKitBridge.shared.usingFallback {
            heldTimeout?.cancel()
            heldTimeout = h ? Task { @MainActor [weak self] in
                try? await Task.sleep(for: Self.callKitHoldLimit)
                guard let self, !Task.isCancelled, self.held else { return }
                self.end(by: "hold_timeout")
            } : nil
        }
        #endif
    }

    func phoneCallRinging(_ id: UUID, ringing on: Bool) {
        let before = !ringing.isEmpty
        if on { ringing.insert(id) } else { ringing.remove(id) }
        guard before != !ringing.isEmpty else { return }
        note("phone_call_ringing", ["on": !ringing.isEmpty])
        applyPause()
    }

    private func applyPause() {
        let paused = held || !ringing.isEmpty
        link?.micEnabled = !muted && !held
        link?.voiceVolume = paused ? 0 : (yielded ? Self.yieldVolume : 1)
        local?.held = paused
        if paused { cuePlayer.stop() }
        guard paused != awaySent, let callID else { return }
        awaySent = paused
        let client = state.client
        Task { await client.voiceAway(call: callID, away: paused) }
    }

    /// The app went to the background during a call (the screen locked). The On-Device Voice cannot use the GPU
    /// there, so it speaks with its CPU copy until the app is back (#28).
    private func watchBackground() {
        #if !targetEnvironment(macCatalyst)
        let center = NotificationCenter.default
        backgroundObservers.append(center.addObserver(forName: UIApplication.didEnterBackgroundNotification, object: nil,
                                                      queue: .main) { [weak self] _ in
            Task { @MainActor in self?.movedToBackground(true) }
        })
        backgroundObservers.append(center.addObserver(forName: UIApplication.willEnterForegroundNotification, object: nil,
                                                      queue: .main) { [weak self] _ in
            Task { @MainActor in self?.movedToBackground(false) }
        })
        #endif
    }

    private func movedToBackground(_ away: Bool) {
        guard phase == .calling || phase == .listening else { return }
        guard voiceEngine == "on-device" else {
            note("background", ["on": away, "engine": voiceEngine ?? "none"])
            return
        }
        let engine = OnDeviceVoice.shared.engine
        engine.useCPU = away
        note("background", ["on": away, "engine": "on-device", "voice": away ? "cpu" : "gpu"])
        guard away else { return }
        let started = Date()
        Task { @MainActor [weak self] in
            do {
                try await engine.cpuVoice()
                self?.note("background_voice_ready", ["ms": Int(Date().timeIntervalSince(started) * 1000)])
            } catch {
                self?.note("background_voice_failed", ["error": String(describing: error).prefix(200).description])
            }
        }
    }

    // MARK: Audio route and the proximity sensor

    @ObservationIgnored private var routeObserver: NSObjectProtocol?
    @ObservationIgnored private var interruptObserver: NSObjectProtocol?

    private func watchRoute() {
        readRoute()
        noteRoute(reason: "start")
        routeObserver = NotificationCenter.default.addObserver(forName: AVAudioSession.routeChangeNotification,
                                                               object: nil, queue: .main) { [weak self] n in
            let raw = n.userInfo?[AVAudioSessionRouteChangeReasonKey] as? UInt ?? 0
            Task { @MainActor in
                self?.readRoute()
                self?.noteRoute(reason: Self.routeReason(raw))
                self?.local?.routeChanged()
            }
        }
        interruptObserver = NotificationCenter.default.addObserver(forName: AVAudioSession.interruptionNotification,
                                                                   object: nil, queue: .main) { [weak self] n in
            let raw = n.userInfo?[AVAudioSessionInterruptionTypeKey] as? UInt
            let began = raw == AVAudioSession.InterruptionType.began.rawValue
            let reason = n.userInfo?[AVAudioSessionInterruptionReasonKey] as? UInt
            let resume = (n.userInfo?[AVAudioSessionInterruptionOptionKey] as? UInt).map {
                AVAudioSession.InterruptionOptions(rawValue: $0).contains(.shouldResume) }
            Task { @MainActor in
                var info: [String: Any] = ["type": began ? "began" : "ended"]
                if let reason { info["reason"] = reason }
                if let resume { info["should_resume"] = resume }
                self?.note("interruption", info)
                self?.handleInterruption(began: began, shouldResume: resume)
            }
        }
    }

    private static let holdLimit: Duration = .seconds(180)
    private static let callKitHoldLimit: Duration = .seconds(15 * 60)
    @ObservationIgnored private var heldTimeout: Task<Void, Never>?

    /// Without CallKit (the Mac, or an iPhone call the system would not take) interruptions come straight from the
    /// audio session, and the app has to do what CallKit would: hold on `began`; on `ended` take the audio session
    /// back and carry on, or end the call cleanly if the system will not give the audio back or the interruption
    /// never ends. During a CallKit call the system's own hold actions do this (setHeld from CXSetHeldCallAction).
    private func handleInterruption(began: Bool, shouldResume: Bool?) {
        #if targetEnvironment(macCatalyst)
        setHeld(began)
        #else
        guard CallKitBridge.shared.usingFallback, phase == .calling || phase == .listening else { return }
        if began {
            CallKitBridge.shared.pauseForInterruption()
            setHeld(true)
            heldTimeout?.cancel()
            heldTimeout = Task { @MainActor [weak self] in
                try? await Task.sleep(for: Self.holdLimit)
                guard let self, !Task.isCancelled, self.held else { return }
                self.fail("The call ended while you were away.", problem: false)
            }
            return
        }
        heldTimeout?.cancel()
        heldTimeout = nil
        guard held else { return }
        if shouldResume != false, CallKitBridge.shared.resumeAfterInterruption() {
            setHeld(false)
            note("resumed")
        } else {
            fail("The call ended when it was interrupted.", problem: false)
        }
        #endif
    }

    private func noteRoute(reason: String) {
        let route = AVAudioSession.sharedInstance().currentRoute
        var info: [String: Any] = ["reason": reason]
        if let out = route.outputs.first { info["output"] = out.portType.rawValue; info["output_name"] = out.portName }
        if let inp = route.inputs.first { info["input"] = inp.portType.rawValue; info["input_name"] = inp.portName }
        info["speaker"] = speaker
        note("route", info)
    }

    private static func routeReason(_ raw: UInt) -> String {
        switch AVAudioSession.RouteChangeReason(rawValue: raw) {
        case .newDeviceAvailable: return "new_device"
        case .oldDeviceUnavailable: return "device_gone"
        case .categoryChange: return "category_change"
        case .override: return "override"
        case .wakeFromSleep: return "wake_from_sleep"
        case .noSuitableRouteForCategory: return "no_suitable_route"
        case .routeConfigurationChange: return "configuration_change"
        default: return "unknown_\(raw)"
        }
    }

    private func readRoute() {
        let out = AVAudioSession.sharedInstance().currentRoute.outputs.first
        let external: Set<AVAudioSession.Port> = [.bluetoothHFP, .bluetoothA2DP, .bluetoothLE, .carAudio, .headphones,
                                                  .airPlay, .usbAudio]
        externalRoute = out.flatMap { external.contains($0.portType) ? $0.portName : nil }
        fromSystem = true
        speaker = out?.portType == .builtInSpeaker
        fromSystem = false
        updateProximity()
    }

    private func updateProximity() {
        #if !targetEnvironment(macCatalyst)
        let atEar = phase == .listening && !speaker && externalRoute == nil
        UIDevice.current.isProximityMonitoringEnabled = atEar
        #endif
    }

    private func stopOnHub() {
        eventsTask?.cancel()
        micTask?.cancel()
        if let callID {
            let client = state.client
            Task { await client.stopVoice(call: callID) }
        }
        callID = nil
    }

    private func followEvents() {
        guard let callID else { return }
        let client = state.client
        let name = state.bot(botID)?.name ?? "Zero"
        eventsTask = Task { [weak self] in
            var failures = 0
            while !Task.isCancelled {
                do {
                    for try await ev in client.voiceEvents(call: callID) {
                        guard let self, self.phase != .ended else { return }
                        failures = 0
                        if self.handle(ev, botName: name) { return }
                    }
                } catch {}
                failures += 1
                if failures > 4 { break }
                try? await Task.sleep(for: .seconds(Double(failures)))
            }
            guard let self, !Task.isCancelled, self.phase != .ended else { return }
            if case .failed = self.phase { return }
            self.fail("Lost touch with the call. Hang up and call again.")
        }
    }

    /// The ChatGPT voice's volume while the person talks over it: it yields at once, before ChatGPT's own turn
    /// detection stops it a moment later (#32, #37).
    static let yieldVolume = 0.2
    private var talkLevel: Float { speaker ? 0.07 : 0.04 }

    private func watchMic() {
        guard micTask == nil, let link, let callID else { return }
        let client = state.client
        @MainActor final class Told { var talking = false; var at = Date.distantPast; var sending = false }
        let told = Told()
        micTask = Task { [weak self] in
            var lastLoud = Date.distantPast
            var loudRun = 0
            var voiceLoudAt = Date.distantPast
            while !Task.isCancelled {
                try? await Task.sleep(for: .milliseconds(100))
                guard let self else { return }
                let levels = await link.levels()
                let now = Date()
                var talking = false
                if levels.voice > 0.01 { voiceLoudAt = now }
                if !self.muted && !self.held && !self.cuePlayer.isPlaying {
                    if levels.mic > self.talkLevel { lastLoud = now; loudRun += 1 } else { loudRun = 0 }
                    talking = now.timeIntervalSince(lastLoud) < 0.8
                }
                let voicePlaying = now.timeIntervalSince(voiceLoudAt) < 0.3
                if loudRun >= 2, voicePlaying, !self.yielded {
                    self.yielded = true
                    self.applyPause()
                    self.cueGate.clearProgress()
                    self.note("yield", ["mic": Double(levels.mic), "voice": Double(levels.voice)])
                } else if self.yielded, now.timeIntervalSince(lastLoud) > 0.7 {
                    self.yielded = false
                    self.applyPause()
                }
                let quiet = !talking && now.timeIntervalSince(voiceLoudAt) > 0.4 && now.timeIntervalSince(lastLoud) > 0.6
                    && !self.held && self.ringing.isEmpty && self.hangupTask == nil
                if let e = self.cueGate.next(now: now.timeIntervalSince1970, quiet: quiet) {
                    self.playCue(e, on: link)
                }
                if (talking != told.talking || (talking && now.timeIntervalSince(told.at) > 2)) && !told.sending {
                    let want = talking
                    told.sending = true
                    Task { @MainActor in
                        if await client.voiceTalking(call: callID, talking: want) {
                            told.talking = want
                            told.at = Date()
                        }
                        told.sending = false
                    }
                }
            }
        }
    }

    private func playCue(_ e: Earcon, on link: RealtimeLink) {
        link.micEnabled = false
        cuePlayer.play(e)
        note("cue_played", ["cue": e.rawValue])
        Task { @MainActor [weak self] in
            try? await Task.sleep(for: .seconds(e.seconds + 0.12))
            guard let self, self.link === link else { return }
            link.micEnabled = !self.muted && !self.held
        }
    }

    private func endAfterGoodbye() {
        guard hangupTask == nil, phase == .listening else { return }
        note("goodbye_heard")
        hangupTask = Task { @MainActor [weak self] in
            var quiet = 0.0, waited = 0.0, personRun = 0
            while waited < 8 {
                try? await Task.sleep(for: .milliseconds(100))
                waited += 0.1
                guard let self, !Task.isCancelled, self.phase == .listening else { return }
                var voiceBusy = false, person = false
                if let link = self.link {
                    let l = await link.levels()
                    voiceBusy = l.voice > 0.01
                    person = !self.muted && l.mic > self.talkLevel
                } else if let local = self.local {
                    voiceBusy = local.busy
                    person = local.personTalking
                }
                personRun = person ? personRun + 1 : 0
                if personRun >= 3 {
                    self.note("goodbye_cancelled", ["after_ms": Int(waited * 1000)])
                    self.hangupTask = nil
                    return
                }
                quiet = voiceBusy || person ? 0 : quiet + 0.1
                if quiet >= 0.8 { break }
            }
            guard let self, !Task.isCancelled, self.phase == .listening else { return }
            self.hangupTask = nil
            self.end(by: "goodbye")
        }
    }

    private func handle(_ ev: HubClient.VoiceEvent, botName: String) -> Bool {
        let text = (ev.text ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
        if ev.role == "assistant" || ev.type == "error" || ev.type == "closed" { stopConnectingSounds() }
        if ev.role == "assistant", !sawCaption {
            sawCaption = true
            note("timing", ["step": "first_voice_caption", "ms": msSinceStart])
        }
        switch ev.type {
        case "news":
            if let id = ev.id { local?.news(id: id) }
        case "cue":
            if let kind = Earcon(rawValue: text), let task = ev.id {
                if let local { local.cue(kind, task: task) } else { cueGate.add(kind, task: task, now: Date().timeIntervalSince1970) }
            }
        case "hangup":
            endAfterGoodbye()
        case "said" where ev.role == "user":
            if !text.isEmpty { transcript.append((who: "You", text: text)) }
        case "said" where ev.role == "assistant":
            if !text.isEmpty { transcript.append((who: botName, text: text)) }
        case "error":
            fail(text.isEmpty ? "The call had a problem. Hang up and call again." : text)
        case "closed":
            if phase != .ended, phase != .calling {
                if case .failed = phase { return true }
                fail("The call ended.", problem: false)
            }
            return true
        default:
            break
        }
        return false
    }

    // MARK: On-Device Voice

    private func onDeviceReason(_ asked: Task<HubClient.VoiceEngines?, Never>?) async -> String? {
        guard OnDeviceVoice.supported else { return nil }
        if state.callEngine == AppState.onDeviceEngine { return "chosen" }
        let engines = await asked?.value
        if let g = engines?.greeting, !g.isEmpty { OnDeviceVoice.shared.greetingText = g }
        if let g = engines?.greeting_late, !g.isEmpty { OnDeviceVoice.shared.greetingLateText = g }
        guard let chat = engines?.chatgpt, !chat.ok else { return nil }
        return chat.reason ?? "unavailable"
    }

    private func startOnDevice(reason: String) async {
        voiceEngine = "on-device"
        let store = OnDeviceVoice.shared
        let speech = await withCheckedContinuation { c in SFSpeechRecognizer.requestAuthorization { c.resume(returning: $0) } }
        note("on_device", ["reason": reason, "voice": store.voice.rawValue, "loaded": store.engine.ready,
                           "speech_auth": speech.rawValue])
        guard speech == .authorized else {
            fail("Speech recognition is off for Zero. Turn it on in Settings to call with On-Device Voice.")
            return
        }
        store.inCall = true
        if !store.engine.ready {
            preparing = (0, "Getting the voice ready.")
            let watch = Task { @MainActor [weak self] in
                while !Task.isCancelled {
                    self?.preparing = (store.progress, store.detail)
                    try? await Task.sleep(for: .milliseconds(250))
                }
            }
            let ok = await store.prepare()
            watch.cancel()
            preparing = nil
            note("timing", ["step": "voice_loaded", "ms": msSinceStart, "ok": ok, "first_run": store.engine.firstRun])
            guard ok else {
                store.inCall = false
                fail("Zero couldn't get the voice ready. Check your connection and try again.")
                return
            }
        }
        guard phase == .calling else {
            store.inCall = false
            finishDiagnostics()
            return
        }
        // CallKit hands over the audio session a moment after the call starts (at most about four seconds).
        #if targetEnvironment(macCatalyst)
        let sessionReady = false
        #else
        let sessionReady = true
        for _ in 0..<50 where !audioActive { try? await Task.sleep(for: .milliseconds(100)) }
        #endif
        let start: HubClient.LocalCallStart
        awaitingHub = true
        let defaults = UserDefaults.standard
        let picked = defaults.string(forKey: "onDeviceVoice")
        let chosen = CallRules.voiceChosen(current: picked, reported: defaults.string(forKey: "onDeviceVoiceReported"))
        let asking = picked == nil && reason != "chosen" ? CallRules.onDeviceVoice(matching: state.callVoice) : store.voice.rawValue
        do {
            start = try await state.client.startLocalVoice(bot: botID, voice: asking, reason: reason, chosen: chosen)
            awaitingHub = false
        } catch {
            awaitingHub = false
            store.inCall = false
            note("hub_start_failed", ["error": HubClient.describe(error), "ms": msSinceStart, "engine": "on-device"])
            fail("Couldn't reach Zero. Hang up and try again in a moment.")
            return
        }
        hubCallID = start.call_id
        callID = start.call_id
        if !start.greeting.isEmpty { store.greetingText = start.greeting }
        if let late = start.greeting_late, !late.isEmpty { store.greetingLateText = late }
        let voice = OnDeviceVoiceName(rawValue: start.voice) ?? store.voice
        if picked != nil || reason == "chosen" {
            if voice.rawValue != state.onDeviceVoice { state.onDeviceVoice = voice.rawValue }
            defaults.set(voice.rawValue, forKey: "onDeviceVoiceReported")
        }
        guard phase == .calling else {
            store.inCall = false
            await state.client.stopVoice(call: start.call_id)
            finishDiagnostics()
            return
        }
        let waited = msSinceStart
        let greeting = waited >= CallRules.lateGreetingMs ? (start.greeting_late ?? start.greeting) : start.greeting
        let take = store.greetingTake(voice: voice, text: greeting)
        let call = OnDeviceCall(client: state.client, callID: start.call_id, botName: state.bot(botID)?.name ?? "Zero",
                                greeting: greeting, greetingTake: take, voice: voice, engine: store.engine,
                                waitedMs: waited, note: { [weak self] e, f in self?.note(e, f) })
        call.onTranscript = { [weak self] who, text in self?.transcript.append((who: who, text: text)) }
        call.muted = muted
        stopConnectingSounds()
        do {
            try await call.start(sessionReady: sessionReady)
        } catch {
            call.stop()
            store.inCall = false
            note("failed", ["stage": "on_device_audio", "error": String(describing: error).prefix(300).description])
            fail("Zero couldn't start the call's audio. Hang up and try again.")
            return
        }
        local = call
        note("timing", ["step": "audio_connected", "ms": msSinceStart, "engine": "on-device"])
        phase = .listening
        #if !targetEnvironment(macCatalyst)
        CallKitBridge.shared.connected()
        #endif
        updateProximity()
        followEvents()
    }

    // MARK: Diagnostics for the saved call

    private var msSinceStart: Int { Int(Date().timeIntervalSince(startedAt) * 1000) }

    func note(_ event: String, _ fields: [String: Any] = [:]) {
        guard state.saveCallTranscripts, !finished else { return }
        var e = fields
        e["event"] = event
        e["at"] = Date().timeIntervalSince1970
        diagnostics.append(e)
        if diagnostics.count > 300 { diagnostics.removeFirst(diagnostics.count - 300) }
        guard hubCallID != nil, flushTask == nil else { return }
        flushTask = Task { @MainActor [weak self] in
            try? await Task.sleep(for: .seconds(2))
            self?.flushTask = nil
            self?.flushDiagnostics(final: false)
        }
    }

    private func flushDiagnostics(final: Bool) {
        guard let target = hubCallID ?? (final ? localID : nil) else { return }
        let client = state.client
        while !diagnostics.isEmpty {
            let batch = Array(diagnostics.prefix(100))
            diagnostics.removeFirst(batch.count)
            guard let body = try? JSONSerialization.data(withJSONObject: ["events": batch]) else { continue }
            Task { await client.voiceDiagnostics(call: target, body: body) }
        }
    }

    private func finishDiagnostics() {
        guard !finished, !awaitingHub else { return }
        flushTask?.cancel()
        flushTask = nil
        flushDiagnostics(final: true)
        finished = true
    }

    nonisolated static let chatGPTVoices = ["cove", "juniper", "maple", "spruce", "ember", "vale", "breeze", "arbor", "sol"]
}
