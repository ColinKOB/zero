import FluidAudio
import Foundation

final class TimelineOutput: VoiceOutput {
    let t0: Double
    private(set) var playEnd = 0.0
    private(set) var timeline: [Float] = []
    init(t0: Double) { self.t0 = t0 }
    var outputDelay: Double { 0.02 }
    var playEndHost: Double { playEnd }
    var isPlaying: Bool { hostNow() < playEnd }

    func schedule(_ s: [Float]) -> (start: Double, end: Double) {
        let start = max(playEnd, hostNow() + outputDelay)
        let end = start + Double(s.count) / 24000
        playEnd = end
        let at = max(0, Int((start - t0) * 24000))
        if timeline.count < at { timeline += [Float](repeating: 0, count: at - timeline.count) }
        for (i, x) in s.enumerated() {
            let k = at + i
            if k < timeline.count { timeline[k] = x } else { timeline.append(x) }
        }
        return (start, end)
    }
}

func wav(_ s: [Float], rate: Int = 24000) -> Data {
    var d = Data()
    func le<T: FixedWidthInteger>(_ v: T) { var x = v.littleEndian; d.append(Data(bytes: &x, count: MemoryLayout<T>.size)) }
    let n = s.count * 2
    d.append("RIFF".data(using: .ascii)!); le(UInt32(36 + n)); d.append("WAVEfmt ".data(using: .ascii)!)
    le(UInt32(16)); le(UInt16(1)); le(UInt16(1)); le(UInt32(rate)); le(UInt32(rate * 2)); le(UInt16(2)); le(UInt16(16))
    d.append("data".data(using: .ascii)!); le(UInt32(n))
    for v in s { le(Int16(max(-1, min(1, v)) * 32767)) }
    return d
}

struct Stream: Decodable {
    let question: String
    let warm: Bool
    let first_text_s: Double?
    let first_sentence_s: Double?
    let text: String
    let pieces: [[Piece]]
    enum Piece: Decodable {
        case time(Double), text(String)
        init(from d: Decoder) throws {
            let c = try d.singleValueContainer()
            if let t = try? c.decode(Double.self) { self = .time(t) } else { self = .text(try c.decode(String.self)) }
        }
    }
    var timed: [(Double, String)] {
        pieces.compactMap { p in
            guard p.count == 2, case .time(let t) = p[0], case .text(let s) = p[1] else { return nil }
            return (t, s)
        }
    }
}

final class Box<T> { var value: T; init(_ v: T) { value = v } }

func median(_ a: [Double]) -> Double {
    let s = a.sorted()
    guard !s.isEmpty else { return .nan }
    return s.count % 2 == 1 ? s[s.count / 2] : (s[s.count / 2 - 1] + s[s.count / 2]) / 2
}

func f2(_ x: Double) -> String { String(format: "%.2f", x) }

func silences(_ s: [Float]) -> [Double] {
    let frame = 240
    let rms: [Float] = (0..<(s.count / frame)).map { f in
        var sum: Float = 0
        for i in (f * frame)..<((f + 1) * frame) { sum += s[i] * s[i] }
        return (sum / Float(frame)).squareRoot()
    }
    let thr = max(0.0015, (rms.max() ?? 0) * 0.02)
    guard let a = rms.firstIndex(where: { $0 > thr }), let b = rms.lastIndex(where: { $0 > thr }) else { return [] }
    var out: [Double] = []
    var run = 0
    for i in a...b {
        if rms[i] <= thr { run += 1 } else {
            if run >= 10 { out.append(Double(run) / 100) }
            run = 0
        }
    }
    return out
}

@MainActor
func speak(_ engine: PocketVoiceEngine, voice: OnDeviceVoiceName, pieces: [(Double, String)], releaseAt: Double = 0)
    async -> (out: TimelineOutput, sp: ReplySpeaker, t0: Double)
{
    let t0 = hostNow()
    let out = TimelineOutput(t0: t0)
    let sp = ReplySpeaker(engine: engine, output: out, voice: voice)
    var released = false
    for (t, s) in pieces {
        while hostNow() - t0 < t {
            if !released && hostNow() - t0 >= releaseAt { sp.release(); released = true }
            try? await Task.sleep(nanoseconds: 5_000_000)
        }
        sp.add(s)
    }
    sp.finishText()
    while !sp.finished {
        if !released && hostNow() - t0 >= releaseAt { sp.release(); released = true }
        try? await Task.sleep(nanoseconds: 10_000_000)
    }
    return (out, sp, t0)
}

@MainActor
func voiceBench(_ voices: [OnDeviceVoiceName]) async throws {
    let env = ProcessInfo.processInfo.environment
    let models = URL(fileURLWithPath: env["OD_MODELS"] ?? "/tmp/fv-models")
    let outDir = URL(fileURLWithPath: env["OD_OUT"] ?? "/tmp/fv-bench")
    try FileManager.default.createDirectory(at: outDir, withIntermediateDirectories: true)
    let engine = PocketVoiceEngine(root: models)
    let last = Box("")
    let t = voiceClock()
    try await engine.load { _, s in
        if s != last.value, !s.hasPrefix("Downloading") || s.contains("00 of") { last.value = s; print("  load: \(s)") }
    }
    print("Loaded in \(f2(voiceClock() - t)) s (first run: \(engine.firstRun)). Speed check: first audio "
          + "\(engine.speedCheck?.firstMs ?? -1) ms, \(f2(engine.speedCheck?.realTimeFactor ?? 0)) times real time.")
    print("Model folder: \(Int(PocketVoiceEngine.bytes(models) / 1_000_000)) MB")

    let streamsURL = outDir.appendingPathComponent("streams.json")
    let streams = ((try? Data(contentsOf: streamsURL)).flatMap { try? JSONDecoder().decode([Stream].self, from: $0) } ?? [])
        .filter { $0.warm }
    if streams.isEmpty { print("No recorded streams at \(streamsURL.path); run hub/scripts/ondevice_latency.py first.") }

    for voice in voices {
        print("\n== \(voice.label)")
        let tg = voiceClock()
        let takes = try await GreetingTakes.render(engine, text: "Hey Colin, what's up?", voice: voice)
        if let best = takes.first {
            print("Greeting: \(takes.count) takes in \(f2(voiceClock() - tg)) s. Best: seed \(best.seed), \(f2(best.seconds)) s, "
                  + "longest gap \(best.maxGapMs) ms, end rise \(f2(best.rise)). Clean takes: \(takes.filter { $0.maxGapMs <= 150 }.count) of \(takes.count).")
            try wav(best.samples).write(to: outDir.appendingPathComponent("greeting-\(voice.rawValue).wav"))
        }
        var afterSentence: [Double] = [], fromAsk: [Double] = [], rtfs: [Double] = [], joins: [Double] = [], stalls: [Double] = []
        for (i, s) in streams.enumerated() {
            let (out, sp, t0) = await speak(engine, voice: voice, pieces: s.timed)
            guard let fa = sp.firstAudioHost else { continue }
            let first = fa - t0
            let sentence = s.first_sentence_s ?? s.first_text_s ?? 0
            fromAsk.append(first)
            afterSentence.append(first - sentence)
            if let r = sp.realTimeFactor { rtfs.append(r) }
            let gaps = silences(out.timeline)
            joins += gaps
            stalls.append(Double(sp.stallsMs.reduce(0, +)))
            print("  \(i + 1). first sound \(f2(first)) s after the ask (\(Int((first - sentence) * 1000)) ms after the first sentence was "
                  + "written), \(f2(sp.realTimeFactor ?? 0))x real time, stalls \(sp.stallsMs.reduce(0, +)) ms, lead cut \(sp.trimmedLeadMs) ms, "
                  + "silences \(gaps.map { f2($0) }): \(s.text.prefix(50))")
            try wav(out.timeline).write(to: outDir.appendingPathComponent("reply-\(voice.rawValue)-\(i + 1).wav"))
        }
        if !fromAsk.isEmpty {
            print("Median first sound: \(f2(median(fromAsk))) s after the ask, \(Int(median(afterSentence) * 1000)) ms after the "
                  + "first sentence. Real time \(f2(median(rtfs)))x. Silences inside replies \(f2(joins.min() ?? 0)) to "
                  + "\(f2(joins.max() ?? 0)) s (median \(f2(median(joins)))). Stalls: \(stalls.filter { $0 > 0 }.count) of \(stalls.count) replies.")
        }
        if let s = streams.first {
            let (_, sp, t0) = await speak(engine, voice: voice, pieces: s.timed, releaseAt: 0.5)
            if let fa = sp.firstAudioHost {
                print("Held then released at 0.5 s: first sound \(Int((fa - t0 - 0.5) * 1000)) ms after the release.")
            }
        }
    }
    await engine.unload()
}

let args = CommandLine.arguments
switch args.count > 1 ? args[1] : "voice" {
case "voice":
    let which = args.count > 2 ? args[2] : "both"
    let voices: [OnDeviceVoiceName] = which == "both" ? [.george, .alba] : [OnDeviceVoiceName(rawValue: which) ?? .george]
    do {
        try await voiceBench(voices)
    } catch {
        print("Bench failed: \(error)")
        exit(1)
    }
default:
    print("Usage: OnDeviceBench voice [george|alba|both]")
}
