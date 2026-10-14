import FluidAudio
import Foundation

private let timebase: mach_timebase_info_data_t = {
    var info = mach_timebase_info_data_t()
    mach_timebase_info(&info)
    return info
}()

/// Seconds on the host clock, the clock audio timestamps use.
func hostSeconds(_ t: UInt64) -> Double { Double(t) * Double(timebase.numer) / Double(timebase.denom) / 1e9 }
func hostNow() -> Double { hostSeconds(mach_absolute_time()) }

/// Where a reply's audio goes: the call's player, or the bench's stand-in.
protocol VoiceOutput: AnyObject {
    /// Queues samples (24 kHz mono) after whatever is queued. Returns when they start and end on the host clock.
    func schedule(_ samples: [Float]) -> (start: Double, end: Double)
    var playEndHost: Double { get }
    var outputDelay: Double { get }
    var isPlaying: Bool { get }
}

@MainActor
final class ReplySpeaker {
    struct Played { let text: String; let start: Double; var end: Double }

    private struct Segment {
        let sentence: SpokenSentence
        let spoken: String
        let enqueuedHost: Double
        var gate = LeadGate()
        var held: [Float] = []
        var buffer: [Float] = []
        var started = false
        var done = false
        var rawSamples = 0
        var genStart: Double?
        var genEnd: Double?
        var trail: Double { Double(gate.trailMs) / 1000 }
    }

    /// Playback of a sentence starts once this much of it is made, so one slow frame does not stop the sound.
    static let prebuffer = 0.3

    private let engine: PocketVoiceEngine?
    private let output: VoiceOutput
    let voice: OnDeviceVoiceName
    private var plan = ReplySentences()
    private var session: PocketTtsSession?
    private var task: Task<Void, Never>?
    private var segments: [Segment] = []
    private var enqueued = 0
    private var current = -1
    private var finishedText = false
    private var sessionFinished = false
    private(set) var released = false
    private(set) var cancelled = false
    private(set) var synthDone = false
    private(set) var played: [Played] = []
    private(set) var firstAudioHost: Double?
    private(set) var firstSynthMs: Double?
    private(set) var stallsMs: [Int] = []
    private(set) var trimmedLeadMs: [Int] = []
    private(set) var text = ""
    private let createdHost = hostNow()
    var onFirstAudio: ((Double) -> Void)?
    var onError: ((String) -> Void)?

    init(engine: PocketVoiceEngine, output: VoiceOutput, voice: OnDeviceVoiceName) {
        self.engine = engine
        self.output = output
        self.voice = voice
        task = Task { [weak self] in await self?.run() }
    }

    /// Audio made earlier (the greeting take): it plays on `release()`.
    init(output: VoiceOutput, voice: OnDeviceVoiceName, prerendered samples: [Float], text: String) {
        engine = nil
        self.output = output
        self.voice = voice
        self.text = text
        var seg = Segment(sentence: SpokenSentence(text: text, paragraphEnd: true), spoken: text, enqueuedHost: hostNow())
        seg.held = samples
        seg.done = true
        segments = [seg]
        plan.finish()
        finishedText = true
        firstSynthMs = 0
        synthDone = true
    }

    private func run() async {
        guard let engine else { return }
        do {
            let s = try await engine.session(voice: voice)
            if cancelled {
                await s.cancel()
                synthDone = true
                return
            }
            session = s
            feedSession()
            for try await f in s.frames {
                if cancelled { break }
                let idx = f.utteranceIndex ?? max(current, 0)
                if idx != current {
                    if current >= 0 { endSegment(current) }
                    current = idx
                    if idx < segments.count {
                        segments[idx].genStart = max(segments[idx].enqueuedHost, idx > 0 ? (segments[idx - 1].genEnd ?? 0) : 0)
                    }
                }
                guard idx < segments.count else { continue }
                receive(f.samples, idx)
            }
            if current >= 0, current < segments.count { endSegment(current) }
        } catch {
            if !cancelled && !(error is CancellationError) { onError?("\(error)") }
        }
        synthDone = true
    }

    // MARK: Text in

    func add(_ delta: String) {
        text += delta
        plan.push(delta)
        drain()
    }

    func finishText() {
        plan.finish()
        drain()
        finishedText = true
        feedSession()
    }

    var strippedOpening: String? { plan.strippedOpening }

    private func drain() {
        while let s = plan.next() {
            let spoken = speakableText(s.text)
            guard spoken.contains(where: { $0.isLetter || $0.isNumber }) else { continue }
            segments.append(Segment(sentence: s, spoken: spoken, enqueuedHost: hostNow()))
        }
        feedSession()
    }

    private func feedSession() {
        guard let session, !cancelled else { return }
        while enqueued < segments.count {
            session.enqueue(segments[enqueued].spoken)
            enqueued += 1
        }
        if finishedText && plan.finished && !sessionFinished {
            sessionFinished = true
            session.finish()
        }
    }

    // MARK: Audio out

    private func receive(_ samples: [Float], _ idx: Int) {
        let now = hostNow()
        if firstSynthMs == nil, !samples.isEmpty { firstSynthMs = (now - createdHost) * 1000 }
        segments[idx].rawSamples += samples.count
        segments[idx].genEnd = now
        let out = segments[idx].gate.feed(samples)
        guard !out.isEmpty else { return }
        if !released {
            segments[idx].held += out
        } else if segments[idx].started {
            let prevEnd = output.playEndHost
            let (s, e) = output.schedule(out)
            if prevEnd > 0 && s - prevEnd > 0.02 { stallsMs.append(Int((s - prevEnd) * 1000)) }
            if !played.isEmpty { played[played.count - 1].end = e }
        } else {
            segments[idx].buffer += out
            if Double(segments[idx].buffer.count) / Double(PocketVoiceEngine.sampleRate) >= Self.prebuffer {
                start(idx, segments[idx].buffer)
                segments[idx].buffer = []
            }
        }
    }

    private func endSegment(_ idx: Int) {
        guard idx < segments.count else { return }
        segments[idx].done = true
        segments[idx].genEnd = segments[idx].genEnd ?? hostNow()
        trimmedLeadMs.append(segments[idx].gate.cutMs)
        if released && !segments[idx].started && !segments[idx].buffer.isEmpty {
            start(idx, segments[idx].buffer)
            segments[idx].buffer = []
        }
    }

    /// Schedules a sentence's first audio after the matched pause.
    private func start(_ idx: Int, _ samples: [Float]) {
        var pad = 0.0
        if idx > 0, segments[idx - 1].started {
            let prev = segments[idx - 1]
            let late = max(0, hostNow() + output.outputDelay - output.playEndHost)
            if late > 0.02 { stallsMs.append(Int(late * 1000)) }
            pad = SpokenPause.pad(previous: prev.sentence.pauseAfter, trailing: prev.done ? prev.trail : 0, late: late)
        }
        let out = pad > 0 ? [Float](repeating: 0, count: Int(pad * Double(PocketVoiceEngine.sampleRate))) + samples : samples
        let (s, e) = output.schedule(out)
        segments[idx].started = true
        let speechStart = s + pad
        played.append(Played(text: segments[idx].sentence.text, start: speechStart, end: e))
        if firstAudioHost == nil {
            firstAudioHost = speechStart
            onFirstAudio?(speechStart)
        }
    }

    /// The turn is confirmed: everything made so far plays, and the rest plays as it is made.
    func release() {
        guard !released, !cancelled else { return }
        released = true
        for i in segments.indices where !segments[i].held.isEmpty {
            let audio = segments[i].held
            segments[i].held = []
            start(i, audio)
        }
    }

    func cancel() {
        cancelled = true
        task?.cancel()
        for i in segments.indices { segments[i].held = []; segments[i].buffer = [] }
        if let s = session { Task { await s.cancel() } }
    }

    // MARK: State

    /// Everything has been made and has finished playing.
    var finished: Bool {
        synthDone && !output.isPlaying && segments.allSatisfy { $0.held.isEmpty && $0.buffer.isEmpty }
    }

    /// The words the person actually heard before `t`, for the history after an interruption.
    func spokenText(before t: Double) -> String {
        played.filter { $0.start < t }.map(\.text).joined(separator: " ")
    }

    /// Every word the reply has so far, for telling the voice's own echo from the person.
    var scheduledText: String { segments.map(\.sentence.text).joined(separator: " ") }

    /// Seconds of speech made per second of work. Under 1 is slower than real time.
    var realTimeFactor: Double? {
        let work = segments.reduce(0.0) { acc, s in
            guard let a = s.genStart, let b = s.genEnd, b > a else { return acc }
            return acc + (b - a)
        }
        let audio = Double(segments.reduce(0) { $0 + $1.rawSamples }) / Double(PocketVoiceEngine.sampleRate)
        return work > 0.05 && audio > 0.2 ? audio / work : nil
    }
}
