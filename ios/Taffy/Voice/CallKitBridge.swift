import AVFoundation
import Foundation
#if !targetEnvironment(macCatalyst)
import CallKit
import WebRTC

/// Taffy calls as real iPhone calls: they show on the lock screen, in the Dynamic Island and in CarPlay,
/// steering-wheel and AirPods buttons can end them, an incoming phone call puts them on hold, and the
/// screen turns off at the ear. CallKit owns the audio session; WebRTC's audio runs on the session it
/// activates (manual audio). The app needs the Voice over IP background mode (`voip` in UIBackgroundModes):
/// without it CallKit can refuse the call as unentitled, and the call then runs without the Dynamic Island
/// (Oct 9; the saved call's `system_call` line shows the error code).
@MainActor
final class CallKitBridge: NSObject, CXProviderDelegate, CXCallObserverDelegate {
    static let shared = CallKitBridge()

    private let provider: CXProvider
    private let controller = CXCallController()
    private(set) var current: UUID?
    /// Calls the app already ended, so their late end actions are recognized and never touch a newer call.
    private var ended: Set<UUID> = []
    /// Every call Zero has asked the system for and that the system may still list: the only calls Zero may close,
    /// and the only ones that are not a phone call. A CXCall carries just a uuid and a state, so ownership has to
    /// be remembered here; it is also kept on disk so a call left behind by a crash is still recognized as ours.
    private var owned: Set<UUID>
    private static let ownedKey = "zero.callkit.ownedCalls"
    /// Calls asked for since the app launched. The system may not list a call yet in the first moments after it was
    /// asked for, so these are never forgotten as gone (Oct 10: a second start forgot the first call, then took it
    /// for a phone call).
    private var askedThisLaunch: Set<UUID> = []
    /// Whether CallKit handed over the audio session for the current call.
    private var activated = false
    /// Whether the app took the audio session itself (the fallback, or CallKit never handed it over), so the
    /// app has to hand it back; CallKit deactivates the session it activated.
    private var appOwnsSession = false
    /// True while a call runs without CallKit (the system refused one): interruptions are handled by the app.
    private(set) var usingFallback = false
    /// Counts audio sessions taken, so a delayed hand-back never touches the session of a newer call.
    private var sessionGeneration = 0
    /// The call screen's engine, told about actions taken from the system UI.
    weak var engine: CallEngine?
    /// Why the system refused the last call, for the call's saved diagnostics (nil when it was accepted).
    private(set) var lastRefusal: NSError?
    /// Leftover Zero calls closed before the last start.
    private(set) var ghostsClosed = 0
    /// Whether the last start had to ask the system twice because the provider was not registered yet.
    private(set) var retriedProvider = false

    override init() {
        owned = Set((UserDefaults.standard.stringArray(forKey: Self.ownedKey) ?? []).compactMap(UUID.init(uuidString:)))
        let config = CXProviderConfiguration()
        config.supportsVideo = false
        config.maximumCallsPerCallGroup = 1
        config.maximumCallGroups = 1
        config.supportedHandleTypes = [.generic]
        config.includesCallsInRecents = true
        provider = CXProvider(configuration: config)
        super.init()
        provider.setDelegate(self, queue: nil)
        controller.callObserver.setDelegate(self, queue: nil)
        // WebRTC waits for CallKit to hand it an active audio session.
        RTCAudioSession.sharedInstance().useManualAudio = true
        RTCAudioSession.sharedInstance().isAudioEnabled = false
    }

    /// Starts the system call. Returns false if the system refused (for example during a phone call).
    func start(botName: String, botID: String) async -> Bool {
        // A call the system still lists for Zero (left behind by a crash or a reinstall) counts against the one
        // call Zero may have and makes the next start fail with "another call is going" (Oct 8). Close any such
        // call first. Only calls Zero started are closed: a CXCall does not say who owns it, so every other live
        // call is a real phone, FaceTime or other app's call and is left alone (QA round 10, L1).
        lastRefusal = nil
        ghostsClosed = 0
        retriedProvider = false
        let listed = controller.callObserver.calls
        owned = owned.filter { listed.map(\.uuid).contains($0) || askedThisLaunch.contains($0) }
        for ghost in listed where !ghost.hasEnded && ghost.uuid != current && owned.contains(ghost.uuid) {
            provider.reportCall(with: ghost.uuid, endedAt: Date(), reason: .remoteEnded)
            ended.insert(ghost.uuid)
            ghostsClosed += 1
        }
        saveOwned()
        let id = UUID()
        func startAction() -> CXStartCallAction {
            let action = CXStartCallAction(call: id, handle: CXHandle(type: .generic, value: botID))
            action.contactIdentifier = botName
            return action
        }
        current = id
        owned.insert(id)
        askedThisLaunch.insert(id)
        saveOwned()
        activated = false
        appOwnsSession = false
        usingFallback = false
        sessionGeneration += 1
        do {
            do {
                try await controller.request(CXTransaction(action: startAction()))
            } catch let e as NSError where e.domain == CXErrorDomainRequestTransaction
                        && e.code == CXErrorCodeRequestTransactionError.Code.unknownCallProvider.rawValue {
                // The system had not finished registering the provider yet (it is made at launch, but a call from
                // Siri or Recents can come first). Once is enough to tell that apart from a real refusal.
                retriedProvider = true
                try await Task.sleep(for: .milliseconds(500))
                try await controller.request(CXTransaction(action: startAction()))
            }
            let update = CXCallUpdate()
            update.localizedCallerName = botName
            update.remoteHandle = CXHandle(type: .generic, value: botID)
            update.hasVideo = false
            update.supportsHolding = true
            update.supportsGrouping = false
            update.supportsUngrouping = false
            update.supportsDTMF = false
            provider.reportCall(with: id, updated: update)
            // If CallKit never hands over the audio, take it ourselves rather than run a silent call.
            Task { @MainActor [weak self] in
                try? await Task.sleep(for: .seconds(4))
                guard let self, self.current == id, !self.activated else { return }
                let session = AVAudioSession.sharedInstance()
                try? session.setActive(true)
                let rtc = RTCAudioSession.sharedInstance()
                rtc.audioSessionDidActivate(session)
                rtc.isAudioEnabled = true
                self.activated = true
                self.appOwnsSession = true     // CallKit never activated it, so it may never deactivate it either
                self.engine?.note("system_audio", ["source": "app_after_timeout"])
                self.engine?.audioReady()
            }
            return true
        } catch {
            if current == id { current = nil }
            owned.remove(id)
            saveOwned()
            let ns = error as NSError
            lastRefusal = ns
            NSLog("Zero call: CallKit refused the call: domain %@ code %ld: %@ (%@)", ns.domain, ns.code,
                  ns.localizedDescription, String(describing: error))
            return false
        }
    }

    /// How many live calls the system lists that Zero did not start: a phone call, a FaceTime call or another
    /// app's call. Only these block a Zero call.
    var foreignCallCount: Int {
        controller.callObserver.calls.filter { !$0.hasEnded && !owned.contains($0.uuid) }.count
    }

    /// Whether the phone is on a real phone or FaceTime call right now (not one of Zero's).
    var phoneCallActive: Bool { foreignCallCount > 0 }

    private func saveOwned() {
        UserDefaults.standard.set(owned.map(\.uuidString), forKey: Self.ownedKey)
    }

    /// Runs a call without CallKit when the system refuses one: the app takes the audio session itself, so the
    /// call still works, only without the lock-screen call controls. Like a CallKit call it starts at the ear, or
    /// on whatever the audio is already going to (AirPods, the car); the Speaker button moves it to the
    /// loudspeaker. Returns false when the session could not be activated, so the call is not run in silence.
    func startWithoutSystemCall() -> Bool {
        current = nil
        sessionGeneration += 1
        usingFallback = true
        let session = AVAudioSession.sharedInstance()
        do {
            try session.setCategory(.playAndRecord, mode: .voiceChat,
                                    options: [.allowBluetoothHFP, .allowBluetoothA2DP])
            try session.setActive(true)
        } catch {
            usingFallback = false
            engine?.note("system_audio", ["source": "fallback_failed", "error": error.localizedDescription])
            return false
        }
        try? session.overrideOutputAudioPort(.none)
        let rtc = RTCAudioSession.sharedInstance()
        rtc.audioSessionDidActivate(session)
        rtc.isAudioEnabled = true
        activated = true
        appOwnsSession = true
        engine?.note("system_audio", ["source": "fallback"])
        engine?.audioReady()
        return true
    }

    /// An interruption began (a phone call, Siri, an alarm) during a call without CallKit: the audio stops, as it
    /// does when CallKit deactivates the session.
    func pauseForInterruption() {
        guard usingFallback, activated else { return }
        let rtc = RTCAudioSession.sharedInstance()
        rtc.audioSessionDidDeactivate(AVAudioSession.sharedInstance())
        rtc.isAudioEnabled = false
    }

    /// The interruption is over: take the audio session back. Returns false if the system will not give it back.
    func resumeAfterInterruption() -> Bool {
        guard usingFallback, activated else { return false }
        let session = AVAudioSession.sharedInstance()
        do {
            try session.setActive(true)
        } catch {
            engine?.note("system_audio", ["source": "resume_failed", "error": error.localizedDescription])
            return false
        }
        let rtc = RTCAudioSession.sharedInstance()
        rtc.audioSessionDidActivate(session)
        rtc.isAudioEnabled = true
        return true
    }

    /// Gives the audio session back to the system, so other apps' music resumes and Bluetooth leaves the call
    /// profile. The engine calls this after the call's audio has stopped. A deactivation fails with "busy" while
    /// any audio is still running, and the WebRTC unit stops a moment after isAudioEnabled changes, so it is
    /// retried a few times.
    private func handBackAudioSession() {
        activated = false
        appOwnsSession = false
        usingFallback = false
        let rtc = RTCAudioSession.sharedInstance()
        rtc.isAudioEnabled = false
        let generation = sessionGeneration
        Task { @MainActor [weak self] in
            for attempt in 0..<5 {
                guard let self, self.sessionGeneration == generation else { return }
                do {
                    try AVAudioSession.sharedInstance().setActive(false, options: .notifyOthersOnDeactivation)
                    rtc.audioSessionDidDeactivate(AVAudioSession.sharedInstance())
                    self.engine?.note("system_audio_off", ["source": "app", "attempts": attempt + 1])
                    return
                } catch {
                    if attempt == 4 {
                        self.engine?.note("system_audio_off", ["source": "app", "failed": error.localizedDescription])
                        return
                    }
                    try? await Task.sleep(for: .milliseconds(300))
                }
            }
        }
    }

    /// Mute from the call screen, so the lock screen and CarPlay show the same.
    func requestMute(_ muted: Bool) {
        guard let id = current else { return }
        Task { try? await controller.request(CXTransaction(action: CXSetMutedCallAction(call: id, muted: muted))) }
    }

    func connected() {
        guard let id = current else { return }
        provider.reportOutgoingCall(with: id, connectedAt: Date())
    }

    /// Ends the system call from the app (End button, a failure, the far side hanging up). The engine stops the
    /// call's audio first, so the audio session can be handed back at once.
    func end(failed: Bool = false) {
        guard let id = current else {
            if activated && appOwnsSession { handBackAudioSession() }
            return
        }
        current = nil
        ended.insert(id)
        // CallKit deactivates the session it activated. When it never did (the app took the session itself after
        // four seconds), nobody else will, so the app hands it back.
        if appOwnsSession { handBackAudioSession() }
        if failed {
            provider.reportCall(with: id, endedAt: Date(), reason: .failed)
        } else {
            Task { try? await controller.request(CXTransaction(action: CXEndCallAction(call: id))) }
        }
    }

    // MARK: CXCallObserverDelegate

    nonisolated func callObserver(_ callObserver: CXCallObserver, callChanged call: CXCall) {
        let id = call.uuid
        let ringing = !call.isOutgoing && !call.hasConnected && !call.hasEnded
        Task { @MainActor in
            guard !self.owned.contains(id), self.engine != nil, self.current != nil || self.usingFallback else { return }
            self.engine?.phoneCallRinging(id, ringing: ringing)
        }
    }

    // MARK: CXProviderDelegate

    nonisolated func providerDidReset(_ provider: CXProvider) {
        Task { @MainActor in
            self.engine?.note("system_call_reset")
            self.engine?.end()
            self.current = nil
        }
    }

    nonisolated func provider(_ provider: CXProvider, perform action: CXStartCallAction) {
        // The session's shape is set before CallKit activates it.
        let session = RTCAudioSession.sharedInstance()
        session.lockForConfiguration()
        try? session.setCategory(.playAndRecord, mode: .voiceChat,
                                 options: [.allowBluetoothHFP, .allowBluetoothA2DP])
        session.unlockForConfiguration()
        provider.reportOutgoingCall(with: action.callUUID, startedConnectingAt: Date())
        action.fulfill()
    }

    nonisolated func provider(_ provider: CXProvider, perform action: CXEndCallAction) {
        let id = action.callUUID
        Task { @MainActor in
            if self.current == id {
                self.current = nil
                self.engine?.endFromSystem(by: "system")
                // CallKit deactivates a session it activated; one the app took itself is the app's to hand back.
                if self.appOwnsSession { self.handBackAudioSession() }
            }
            self.ended.remove(id)
        }
        action.fulfill()
    }

    nonisolated func provider(_ provider: CXProvider, perform action: CXSetMutedCallAction) {
        let muted = action.isMuted, id = action.callUUID
        Task { @MainActor in if self.current == id { self.engine?.setMutedFromSystem(muted) } }
        action.fulfill()
    }

    nonisolated func provider(_ provider: CXProvider, perform action: CXSetHeldCallAction) {
        let held = action.isOnHold, id = action.callUUID
        Task { @MainActor in if self.current == id { self.engine?.setHeld(held) } }
        action.fulfill()
    }

    nonisolated func provider(_ provider: CXProvider, didActivate audioSession: AVAudioSession) {
        let rtc = RTCAudioSession.sharedInstance()
        rtc.audioSessionDidActivate(audioSession)
        rtc.isAudioEnabled = true
        Task { @MainActor in
            self.activated = true
            self.engine?.note("system_audio", ["source": "callkit"])
            self.engine?.audioReady()
        }
    }

    nonisolated func provider(_ provider: CXProvider, didDeactivate audioSession: AVAudioSession) {
        let rtc = RTCAudioSession.sharedInstance()
        rtc.audioSessionDidDeactivate(audioSession)
        rtc.isAudioEnabled = false
        Task { @MainActor in self.engine?.note("system_audio_off") }
    }
}
#endif
