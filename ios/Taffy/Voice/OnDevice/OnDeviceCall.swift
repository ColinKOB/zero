import AVFoundation
import Foundation

@MainActor
final class OnDeviceCall {
    enum Phase: String { case off, starting, listening, hearing, thinking, speaking }

    /// One reply: asked for, maybe before the turn is certain, and spoken once it is.
    @MainActor private final class Attempt {
        let n: Int
        let text: String
        let news: Int?
        let startHost = hostNow()
        var speculative: Bool
        var confirmed = false
        var firstTokenHost: Double?
        var firstAudioHost: Double?
        var replyText = ""
        var textDone = false
        var handoff: String?
        var error: String?
        var task: Task<Void, Never>?
        let speaker: ReplySpeaker
        var endOfSpeech: Double = 0
        var confirmHost: Double = 0
        var record: [String: Any] = [:]

        init(n: Int, text: String, news: Int?, speculative: Bool, speaker: ReplySpeaker) {
            self.n = n
            self.text = text
            self.news = news
            self.speculative = speculative
            self.speaker = speaker
        }

        func cancel() {
            task?.cancel()
            speaker.cancel()
        }
    }

    private static let fillerWait = 1.8
    private static let bargeMin: Float = 0.03
    /// How long before the planned confirmation the one finalize is asked for (35 to 140 ms on the phone).
    private static let finalizeLead = 0.1

    private let client: HubClient
    let callID: String
    private let botName: String
    private let greeting: String
    private let greetingTake: [Float]?
    private let voice: OnDeviceVoiceName
    private let engine: PocketVoiceEngine
    private let sink: (String, [String: Any]) -> Void
    /// How long the person waited from tapping Call to the greeting (the hub saves it with the hello).
    private let waitedMs: Int
    /// Work sounds from the hub, played through the call's own player into silence (CallCues.swift).
    private var cues = CueGate()
    private var earcons: [Earcon: [Float]] = [:]
    /// When the voice last asked the person to say something again because no words came through.
    private var askedAgainHost = -Double.infinity

    /// The person's words ("You") and the voice's (the bot's name), for the call screen's transcript.
    var onTranscript: ((String, String) -> Void)?
    /// Something broke that ends the call, in a sentence for the call screen.
    var onFailed: ((String) -> Void)?

    private(set) var phase: Phase = .off
    private let mic = MicPipeline()
    private let smart = SmartTurn(url: SmartTurnFiles.model(in: PocketVoiceEngine.defaultRoot))
    private let audio = CallAudio()
    private let listener = CallListener()
    private var tickTask: Task<Void, Never>?
    private var attempt: Attempt?
    private var n = 1
    private var newsQueue: [Int] = []
    private var lastActivity = hostNow()

    private var turnSeq = 0
    private var turnStartAudio: Double = 0
    private var smart1: Float?
    private var smart2: Float?
    private var asked1 = false
    private var asked2 = false
    private var finalizeAsked = false
    private var finalizeDone = false
    private var finalizeToken = 0

    private var talkingSent = false
    private var talkingSentAt = 0.0

    private struct Candidate {
        let onset: Double
        let info: BargeInfo
        let started: Double
        let source: String
        let baseline: [String]
        var finalized = 0
    }
    private var candidate: Candidate?
    private var routeKind = "none"

    var muted = false { didSet { mic.muted = muted || held } }
    var held = false {
        didSet {
            mic.muted = muted || held
            if held, let a = attempt, a.confirmed { a.cancel(); finish(a, barged: true, stopHost: hostNow()) }
            if !held && oldValue { resumeAudio() }
        }
    }

    init(client: HubClient, callID: String, botName: String, greeting: String, greetingTake: [Float]?,
         voice: OnDeviceVoiceName, engine: PocketVoiceEngine, waitedMs: Int = 0,
         note: @escaping (String, [String: Any]) -> Void) {
        self.client = client
        self.callID = callID
        self.botName = botName
        self.greeting = greeting
        self.greetingTake = greetingTake
        self.voice = voice
        self.engine = engine
        self.waitedMs = waitedMs
        self.sink = note
    }

    /// Adds an event to the saved call. Only strings, numbers and booleans go (nil values are left out), since
    /// the diagnostics are sent as JSON.
    private func note(_ event: String, _ fields: [String: Any]) {
        sink(event, Self.clean(fields))
    }

    static func clean(_ fields: [String: Any]) -> [String: Any] {
        var out: [String: Any] = [:]
        for (k, v) in fields {
            let m = Mirror(reflecting: v)
            var value: Any = v
            if m.displayStyle == .optional {
                guard let inner = m.children.first?.value else { continue }
                value = inner
            }
            switch value {
            case let b as Bool: out[k] = b
            case let i as Int: out[k] = i
            case let d as Double: out[k] = d.isFinite ? d : nil
            case let f as Float: out[k] = f.isFinite ? Double(f) : nil
            case let s as String: out[k] = String(s.prefix(400))
            default: out[k] = String(String(describing: value).prefix(400))
            }
        }
        return out
    }

    // MARK: Start and stop

    /// `sessionReady`: CallKit or the call's fallback already activated the audio session.
    func start(sessionReady: Bool) async throws {
        phase = .starting
        onDeviceNote = { [weak self] event, fields in
            let safe = OnDeviceCall.clean(fields)
            Task { @MainActor in self?.sink(event, safe) }
        }
        let audio = self.audio, listener = self.listener, mic = self.mic
        mic.bargeMin = Self.bargeMin
        mic.isVoicePlaying = { audio.isPlaying }
        mic.stopVoice = { audio.stopPlayback() }
        mic.feedASR = { buf, sec in listener.feed(buf, audioSec: sec) }
        mic.onSpeechStart = { host in DispatchQueue.main.async { [weak self] in self?.userStartedSpeaking(host) } }
        mic.onBargeIn = { onset, info in
            let stopped = hostNow()
            DispatchQueue.main.async { [weak self] in
                self?.bargedIn(onset: onset, stopped: stopped, trigger: "energy", info: info, extra: [:])
            }
        }
        mic.onBargeCandidate = { onset, info in
            DispatchQueue.main.async { [weak self] in self?.bargeCandidate(onset: onset, info: info, source: "mic") }
        }
        audio.onMic = { [mic] s, host, rate in mic.ingest(s, host: host, rate: rate) }
        listener.onUpdate = { [weak self] in self?.transcriptChanged() }
        try audio.start(sessionReady: sessionReady)
        updateEchoGuard()
        await listener.start()
        newTurn(at: mic.audioNow)
        phase = .listening
        note("on_device_start", ["asr": listener.kind, "voice": voice.rawValue, "route": routeKind,
                                 "smart_turn": smart.available, "mic_mode": audio.micMode,
                                 "voice_check_rtf": engine.speedCheck.map { ($0.realTimeFactor * 100).rounded() / 100 } ?? 0,
                                 "greeting_take": greetingTake != nil])
        tickTask = Task { [weak self] in
            while !Task.isCancelled {
                self?.tick()
                try? await Task.sleep(nanoseconds: 20_000_000)
            }
        }
        speakGreeting()
    }

    /// Stops the sound and the microphone at once (the audio session is handed back right after this), then
    /// the recognizer.
    func stop() {
        guard phase != .off else { return }
        tickTask?.cancel()
        clearCandidate()
        if let a = attempt {
            a.cancel()
            if a.confirmed { finish(a, barged: a.firstAudioHost != nil && audio.isPlaying, stopHost: hostNow()) }
        }
        attempt = nil
        audio.stop()
        let l = listener
        Task { await l.stop() }
        phase = .off
        onDeviceNote = { _, _ in }
    }

    /// After a hold (a phone call, Siri) the audio engine may have stopped with the session.
    private func resumeAudio() {
        guard phase != .off, !audio.engine.isRunning else { return }
        do {
            try audio.engine.start()
            audio.player.play()
            note("audio_resumed", [:])
        } catch {
            note("audio_resume_failed", ["error": "\(error)"])
        }
    }

    /// News from the Zip's work (a hub `news` event): said at the next pause.
    func news(id: Int) {
        newsQueue.append(id)
    }

    func routeChanged() { updateEchoGuard() }

    private func updateEchoGuard() {
        routeKind = CallAudio.routeKind()
        mic.echoGuard = routeKind != "earbuds"
    }

    // MARK: Greeting

    private func speakGreeting() {
        let sp: ReplySpeaker
        if let take = greetingTake {
            sp = ReplySpeaker(output: audio, voice: voice, prerendered: take, text: greeting)
        } else {
            sp = ReplySpeaker(engine: engine, output: audio, voice: voice)
            sp.add(greeting)
            sp.finishText()
        }
        let a = Attempt(n: 0, text: "", news: nil, speculative: false, speaker: sp)
        a.replyText = greeting
        a.confirmed = true
        a.textDone = true
        sp.onFirstAudio = { [weak a] host in a?.firstAudioHost = host }
        sp.release()
        attempt = a
        phase = .thinking
        onTranscript?(botName, greeting)
        let client = self.client, id = callID, waited = waitedMs
        Task { await client.voiceReady(call: id, waitedMs: waited) }
    }

    /// Says a short line of the phone's own (not from the hub), such as asking for words that did not come through.
    private func speakLocal(_ text: String) {
        let sp = ReplySpeaker(engine: engine, output: audio, voice: voice)
        sp.add(text)
        sp.finishText()
        let a = Attempt(n: 0, text: "", news: nil, speculative: false, speaker: sp)
        a.replyText = text
        a.confirmed = true
        a.textDone = true
        sp.onFirstAudio = { [weak a] host in a?.firstAudioHost = host }
        sp.release()
        attempt = a
        phase = .thinking
        onTranscript?(botName, text)
    }

    // MARK: Work sounds

    /// A work sound from the hub (`cue` event): started, working or done, for task `task`.
    func cue(_ kind: Earcon, task: Int) {
        cues.add(kind, task: task, now: hostNow())
    }

    /// Plays a waiting work sound when nobody is talking and nothing is playing. Through the call's own player,
    /// so the echo cancellation hears it and it never counts as the person.
    private func playCue() {
        let quiet = phase == .listening && attempt == nil && !held && !mic.speaking && !audio.isPlaying
            && hostNow() - mic.lastVoiceHost > 0.5
        guard let e = cues.next(now: hostNow(), quiet: quiet) else { return }
        let samples = earcons[e] ?? e.samples(rate: 24_000)
        earcons[e] = samples
        audio.schedule(samples)
        note("cue_played", ["cue": e.rawValue])
    }

    /// Whether anyone is talking or anything is playing (an ending after goodbye waits for quiet).
    var busy: Bool { phase != .listening || attempt != nil || mic.speaking || audio.isPlaying }

    /// Whether the person is talking now.
    var personTalking: Bool { mic.speaking || phase == .hearing }

    // MARK: Turn detection

    private func newTurn(at audioSec: Double) {
        turnSeq += 1
        turnStartAudio = audioSec
        resetSmart()
    }

    private func resetSmart() {
        smart1 = nil
        smart2 = nil
        asked1 = false
        asked2 = false
        finalizeAsked = false
        finalizeDone = false
        finalizeToken += 1
    }

    private func transcriptChanged() {}

    private func userStartedSpeaking(_ host: Double) {
        lastActivity = hostNow()
        switch phase {
        case .listening:
            phase = .hearing
        case .hearing:
            if let a = attempt, a.speculative { cancelAttempt(reason: "spec_cancelled") }
            resetSmart()
        case .thinking:
            if let a = attempt, a.firstAudioHost == nil, !a.text.isEmpty {
                note("resumed_before_audio", ["after_confirm_ms": ms(host - a.confirmHost) ?? -1])
                cancelAttempt(reason: nil)
                resetSmart()
                phase = .hearing
            }
        case .speaking:
            if attempt?.firstAudioHost != nil, mic.level >= Self.bargeMin {
                let info = BargeInfo(rms: mic.level, threshold: Self.bargeMin, echoPeak: 0, noiseFloor: mic.floor)
                if mic.echoGuard {
                    bargeCandidate(onset: host, info: info, source: "gap")
                } else {
                    audio.stopPlayback()
                    bargedIn(onset: host, stopped: hostNow(), trigger: "energy_gap", info: info, extra: [:])
                }
            }
        default:
            break
        }
    }

    private func tick() {
        switch phase {
        case .hearing: evaluateTurn()
        case .thinking, .speaking:
            checkCandidate()
            checkReply()
        case .listening:
            if mic.speaking { userStartedSpeaking(hostNow()) }
            else if !newsQueue.isEmpty, attempt == nil, !held, !audio.isPlaying,
                    hostNow() - max(lastActivity, mic.lastVoiceHost) > 0.8 {
                startNews(newsQueue.removeFirst())
            } else {
                playCue()
            }
        default: break
        }
        if phase != .listening { cues.clearProgress() }
        updateTalking()
    }

    /// Tells the hub when the person is talking, repeated every 2 s while it lasts (the hub forgets after 3 s).
    private func updateTalking() {
        let talking = !muted && !held && (phase == .hearing || mic.speaking)
        let now = hostNow()
        guard talking != talkingSent || (talking && now - talkingSentAt > 2) else { return }
        talkingSent = talking
        talkingSentAt = now
        let client = self.client, id = callID
        Task { await client.voiceTalking(call: id, talking: talking) }
    }

    private func evaluateTurn() {
        let now = hostNow()
        let text = listener.text(since: turnStartAudio)
        if mic.speaking {
            if let a = attempt, a.speculative { cancelAttempt(reason: "spec_cancelled") }
            if smart1 != nil || asked1 { resetSmart() }
            return
        }
        let lastVoice = mic.lastVoiceHost
        let silence = now - lastVoice
        let (shape, why) = TurnRules.shape(text)

        if silence >= 0.2 && !asked1 { asked1 = true; askSmart(slot: 1) }
        if silence >= 0.5 && !asked2 { asked2 = true; askSmart(slot: 2) }

        var need: Double?
        switch shape {
        case .empty:
            if silence > 1.6 {
                let voiced = mic.hostToAudio(lastVoice) - turnStartAudio
                note("turn_discarded", ["reason": "no words", "silence_ms": ms(silence) ?? 0,
                                        "voiced_ms": ms(voiced) ?? 0])
                if let a = attempt, a.speculative { cancelAttempt(reason: "spec_cancelled") }
                phase = .listening
                newTurn(at: mic.audioNow)
                if attempt == nil, !muted, CallRules.askAgain(voicedSeconds: voiced, sinceLastAsk: now - askedAgainHost) {
                    askedAgainHost = now
                    note("asked_again", ["voiced_ms": ms(voiced) ?? 0])
                    speakLocal("Sorry, I didn't catch that.")
                }
                return
            }
        case .incomplete:
            need = Self.fillerWait
        case .weakIncomplete:
            if let p = smart2 ?? smart1, p >= 0.85 { need = 0.5 } else if smart2 != nil || silence > 0.7 { need = Self.fillerWait }
        case .complete:
            if !smart.available {
                need = 0.5
            } else if let p = smart1, p >= 0.5 {
                need = 0.3
            } else if smart1 != nil, let p2 = smart2 {
                need = p2 >= 0.5 ? 0.55 : 1.0
            } else if silence > 0.8 {
                need = 1.0
            }
        }
        let cap = 2.6
        let wait: Double? = need.map { min($0, cap) } ?? (text.isEmpty ? nil : cap)

        // One finalize per turn, just before it is confirmed, so the last words come without the recognizer's
        // one-second delay. Finalizing at every pause garbled the words that followed (Oct 9).
        if let wait, !finalizeAsked, silence >= max(0.2, wait - Self.finalizeLead) {
            finalizeAsked = true
            let token = finalizeToken
            let l = listener
            let through = mic.audioNow
            Task { [weak self] in
                await l.finalize(through: through)
                guard let self, self.finalizeToken == token else { return }
                self.finalizeDone = true
            }
        }

        if attempt == nil && !text.isEmpty {
            let specAt: Double
            switch shape {
            case .complete: specAt = 0.2
            case .weakIncomplete: specAt = 0.45
            default: specAt = max(0.25, Self.fillerWait - 0.6)
            }
            let behind = mic.hostToAudio(lastVoice) - listener.coveredUntil
            let textReady = finalizeDone || (!finalizeAsked && behind <= 0)
            if silence >= specAt && textReady && now - listener.lastUpdateHost >= 0.05 {
                startAttempt(text: text, speculative: true)
            }
        }

        guard let wait, silence >= wait else { return }
        if !finalizeDone && silence < wait + 0.4 { return }
        let finalText = listener.text(since: turnStartAudio)
        guard !finalText.isEmpty else { return }
        confirmTurn(text: finalText, lastVoice: lastVoice, shape: shape,
                    why: (need ?? .infinity) > cap ? why + ", hit the cap" : why, wait: wait)
    }

    private func askSmart(slot: Int) {
        guard smart.available else { return }
        let seq = turnSeq
        smart.predict(mic.audio(from: turnStartAudio)) { [weak self] p, _ in
            guard let self, self.turnSeq == seq else { return }
            if slot == 1 { self.smart1 = p } else { self.smart2 = p }
        }
    }

    private func confirmTurn(text: String, lastVoice: Double, shape: TextShape, why: String, wait: Double) {
        let now = hostNow()
        var rec: [String: Any] = ["n": n, "shape": shape.rawValue, "why": why, "wait_ms": Int(wait * 1000),
                                  "endpoint_ms": ms(now - lastVoice) ?? 0]
        if let s = smart1 { rec["smart_1"] = (Double(s) * 100).rounded() / 100 }
        if let s = smart2 { rec["smart_2"] = (Double(s) * 100).rounded() / 100 }
        if let a = attempt {
            if HeardText.norm(a.text) == HeardText.norm(text) {
                rec["spec"] = "used"
            } else {
                note("spec_restarted", ["n": n])
                cancelAttempt(reason: nil)
                rec["spec"] = "restarted"
                startAttempt(text: text, speculative: false)
            }
        } else {
            rec["spec"] = "none"
            startAttempt(text: text, speculative: false)
        }
        guard let a = attempt else { return }
        if a.speculative {
            let client = self.client, id = callID, num = a.n
            Task { await client.voiceConfirm(call: id, n: num) }
        }
        a.speculative = false
        a.confirmed = true
        a.endOfSpeech = lastVoice
        a.confirmHost = now
        a.record = rec
        a.speaker.release()
        onTranscript?("You", text)
        phase = .thinking
        lastActivity = now
    }

    // MARK: Replies

    private func startAttempt(text: String, speculative: Bool) {
        startReply(Attempt(n: n, text: text, news: nil, speculative: speculative,
                           speaker: ReplySpeaker(engine: engine, output: audio, voice: voice)))
    }

    private func startNews(_ id: Int) {
        let a = Attempt(n: n, text: "", news: id, speculative: false, speaker: ReplySpeaker(engine: engine, output: audio, voice: voice))
        a.confirmed = true
        a.confirmHost = hostNow()
        a.endOfSpeech = hostNow()
        a.record = ["n": n, "news": true]
        startReply(a)
        a.speaker.release()
        phase = .thinking
    }

    private func startReply(_ a: Attempt) {
        a.speaker.onFirstAudio = { [weak a] host in a?.firstAudioHost = host }
        a.speaker.onError = { [weak self] e in self?.note("voice_error", ["error": String(e.prefix(300))]) }
        attempt = a
        let stream = client.voiceTurn(call: callID, n: a.n, text: a.news == nil ? a.text : nil, news: a.news,
                                      speculative: a.speculative)
        a.task = Task { [weak self, weak a] in
            do {
                for try await ev in stream {
                    guard let a, !Task.isCancelled else { return }
                    if ev.type == "text", let t = ev.text {
                        if a.firstTokenHost == nil { a.firstTokenHost = hostNow() }
                        a.replyText += t
                        a.speaker.add(t)
                    } else if ev.type == "done" {
                        a.handoff = ev.handoff
                        if ev.error == true { a.error = "reply" }
                    }
                }
            } catch {
                guard let a, !Task.isCancelled, !a.speaker.cancelled else { return }
                if (error as? URLError)?.code == .cancelled || error is CancellationError { return }
                a.error = String(describing: error).prefix(200).description
                self?.note("reply_failed", ["n": a.n, "error": a.error ?? ""])
                let line = " Sorry, I lost the connection for a moment. Can you say that again?"
                a.replyText += line
                a.speaker.add(line)
            }
            guard let a, !Task.isCancelled else { return }
            a.textDone = true
            a.speaker.finishText()
        }
    }

    private func cancelAttempt(reason: String?) {
        guard let a = attempt else { return }
        if let reason { note(reason, ["n": a.n, "age_ms": ms(hostNow() - a.startHost) ?? 0, "had_text": a.firstTokenHost != nil]) }
        a.cancel()
        attempt = nil
    }

    private func checkReply() {
        guard let a = attempt else {
            phase = .listening
            return
        }
        if a.firstAudioHost != nil && phase == .thinking { phase = .speaking }
        if phase == .thinking, a.firstAudioHost == nil, !a.text.isEmpty, a.news == nil {
            let latest = listener.text(since: turnStartAudio)
            if latest.count > a.text.count && HeardText.norm(latest) != HeardText.norm(a.text) {
                note("late_words", ["n": a.n, "after_confirm_ms": ms(hostNow() - a.confirmHost) ?? -1])
                let (e, c, rec) = (a.endOfSpeech, a.confirmHost, a.record)
                a.cancel()
                attempt = nil
                startAttempt(text: latest, speculative: false)
                guard let b = attempt else { return }
                b.confirmed = true
                b.endOfSpeech = e
                b.confirmHost = c
                b.record = rec.merging(["spec": "late_words"]) { $1 }
                b.speaker.release()
                return
            }
        }
        if a.textDone && a.speaker.finished {
            finish(a, barged: false, stopHost: nil)
        }
    }

    private func finish(_ a: Attempt, barged: Bool, stopHost: Double?) {
        if candidate != nil && !barged {
            note("barge_rejected", ["reason": "reply ended"])
            clearCandidate()
        }
        var spoken = a.speaker.spokenText(before: stopHost ?? .infinity)
        if !barged { spoken = a.speaker.played.map(\.text).joined(separator: " ") }
        if !spoken.isEmpty, a.n > 0 { onTranscript?(botName, spoken) }
        let firstAudioMs = a.firstAudioHost.map { Int(($0 - a.endOfSpeech) * 1000) }
        if a.n > 0 {
            var rec = a.record
            rec["first_text_ms"] = a.firstTokenHost.map { Int(($0 - a.endOfSpeech) * 1000) }
            rec["first_audio_ms"] = firstAudioMs
            rec["voice_first_ms"] = a.speaker.firstSynthMs.map { Int($0) }
            rec["voice_rtf"] = a.speaker.realTimeFactor.map { ($0 * 100).rounded() / 100 }
            rec["stalls_ms"] = a.speaker.stallsMs.reduce(0, +)
            rec["barged"] = barged
            rec["handoff"] = a.handoff
            rec["route"] = routeKind
            rec["finalize_ms"] = Int(listener.finalizeMs)
            rec["opening_cut"] = a.speaker.strippedOpening
            note("turn_timing", rec)
            let client = self.client, id = callID, num = a.n
            Task { await client.voiceSaid(call: id, n: num, spoken: spoken, cutOff: barged, firstAudioMs: firstAudioMs) }
            n += 1
        }
        attempt = nil
        lastActivity = hostNow()
        if !barged {
            phase = .listening
            newTurn(at: mic.audioNow)
            listener.newTurn()
        }
    }

    // MARK: Interruptions

    private func candidateHeard() -> String {
        guard let c = candidate else { return "" }
        return listener.text(since: max(0, mic.hostToAudio(c.onset) - 0.25))
    }

    /// A loud sound while the voice talks on the speaker: lower the voice and wait for words that are not its own.
    private func bargeCandidate(onset: Double, info: BargeInfo, source: String) {
        guard candidate == nil else { return }
        guard let a = attempt, a.confirmed, phase == .speaking || phase == .thinking else {
            mic.clearCandidate()
            return
        }
        let base = HeardText.words(listener.text(since: max(0, mic.hostToAudio(onset) - 0.25)))
        candidate = Candidate(onset: onset, info: info, started: hostNow(), source: source, baseline: base)
        audio.duck(true)
        note("barge_candidate", ["source": source, "route": routeKind].merging(info.fields) { $1 })
    }

    private func checkCandidate() {
        guard var c = candidate, let a = attempt else { return }
        let elapsed = hostNow() - c.started
        if (elapsed > 0.35 && c.finalized == 0) || (elapsed > 0.85 && c.finalized == 1) {
            c.finalized += 1
            candidate = c
            let l = listener
            let through = mic.audioNow
            Task { await l.finalize(through: through) }
        }
        let heardText = candidateHeard()
        let voiceWords = Set(HeardText.words(a.replyText + " " + a.speaker.scheduledText + " " + greeting))
        var heardWords = HeardText.words(heardText)
        for w in c.baseline { if let i = heardWords.firstIndex(of: w) { heardWords.remove(at: i) } }
        let (confirmed, foreign) = HeardText.interruption(heard: heardWords, voiceWords: voiceWords)
        if confirmed {
            audio.stopPlayback()
            mic.markSpeaking()
            mic.clearCandidate()
            candidate = nil
            bargedIn(onset: c.onset, stopped: hostNow(), trigger: "words", info: c.info,
                     extra: ["foreign_words": foreign.count, "decide_ms": ms(elapsed) ?? 0, "source": c.source])
        } else if elapsed > 1.6 || !audio.isPlaying {
            note("barge_rejected", ["reason": heardWords.isEmpty ? "no words" : "only the voice's words or go-ahead sounds",
                                    "wait_ms": ms(elapsed) ?? 0, "source": c.source])
            audio.duck(false)
            mic.clearCandidate(echoLevel: c.info.rms)
            candidate = nil
        }
    }

    private func clearCandidate() {
        guard candidate != nil else { return }
        candidate = nil
        audio.duck(false)
        mic.clearCandidate()
    }

    private func bargedIn(onset: Double, stopped: Double, trigger: String, info: BargeInfo, extra: [String: Any]) {
        guard let a = attempt, a.confirmed, phase == .speaking || phase == .thinking else { return }
        let stopMs = Int((stopped - onset) * 1000)
        a.cancel()
        finish(a, barged: true, stopHost: onset)
        note("barge_in", ["stop_ms": stopMs, "trigger": trigger, "route": routeKind].merging(info.fields) { $1 }.merging(extra) { $1 })
        newTurn(at: max(0, mic.hostToAudio(onset) - 0.3))
        listener.newTurn()
        phase = .hearing
    }
}

private func ms(_ seconds: Double?) -> Int? {
    guard let seconds, seconds.isFinite else { return nil }
    return Int((seconds * 1000).rounded())
}
