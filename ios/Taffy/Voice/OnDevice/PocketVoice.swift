import AVFoundation
import CryptoKit
import FluidAudio
import Foundation

func voiceClock() -> Double { Double(DispatchTime.now().uptimeNanoseconds) / 1e9 }

/// The two voices, George by default (Colin rated him 10 of 10) and Alba second (9 of 10).
enum OnDeviceVoiceName: String, CaseIterable, Identifiable {
    case george, alba
    var id: String { rawValue }
    var label: String { self == .george ? "George" : "Alba" }
}

/// What a speed check found. The real-time factor is seconds of speech made per second of work.
struct VoiceSpeedCheck {
    var firstMs: Int
    var audioSeconds: Double
    var workSeconds: Double
    var realTimeFactor: Double { workSeconds > 0 ? audioSeconds / workSeconds : 0 }
}

/// Owns the Pocket TTS model: downloaded on first use into Application Support, loaded for calls, removable.
final class PocketVoiceEngine: @unchecked Sendable {
    static let temperature: Float = 0.3
    /// The int8 English pack plus the two voices (455 MB, as measured in the call test) and Smart Turn (17 MB).
    static let expectedBytes: Double = 472_000_000
    static let sampleRate = 24_000

    let root: URL
    private var manager: PocketTtsManager?
    /// iOS lets no app use the GPU in the background, so a call that goes on with the screen locked speaks with a
    /// second copy of the voice on the CPU: the same files, loaded the first time it is needed and dropped after the
    /// call (docs/qa/2026-10-09-fluid-voice.md, #28).
    static let backgroundUnits = PocketTtsComputeUnits.uniform(.cpuOnly)
    private let cpuLock = NSLock()
    private var cpuManager: PocketTtsManager?
    private var cpuLoad: Task<PocketTtsManager, Error>?
    private var cpuWanted = false
    /// Replies are made on the CPU copy while this is on (the app is in the background during a call).
    var useCPU: Bool {
        get { cpuLock.withLock { cpuWanted } }
        set { cpuLock.withLock { cpuWanted = newValue } }
    }
    private(set) var cpuLoadSeconds: Double?
    private(set) var ready = false
    private(set) var speedCheck: VoiceSpeedCheck?
    private(set) var loadSeconds = 0.0
    private(set) var firstRun = false

    init(root: URL = PocketVoiceEngine.defaultRoot) { self.root = root }

    static var defaultRoot: URL {
        FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("OnDeviceVoice", isDirectory: true)
    }

    static func bytes(_ url: URL) -> Double {
        guard let e = FileManager.default.enumerator(at: url, includingPropertiesForKeys: [.fileSizeKey]) else { return 0 }
        var total = 0
        for case let f as URL in e { total += (try? f.resourceValues(forKeys: [.fileSizeKey]).fileSize) ?? 0 }
        return Double(total)
    }

    /// The files are on this device (a later load only compiles or opens them).
    var isDownloaded: Bool { Self.bytes(root) >= Self.expectedBytes * 0.9 }

    /// Downloads (first time), loads and checks the speed. `progress` gets 0 to 1 and a plain status line.
    func load(progress: @escaping (Double, String) -> Void) async throws {
        let t0 = voiceClock()
        try? FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        var values = URLResourceValues()
        values.isExcludedFromBackup = true
        var dir = root
        try? dir.setResourceValues(values)
        let expected = Self.expectedBytes
        firstRun = Self.bytes(root) < expected * 0.9
        let first = firstRun
        let root = self.root
        let poll = Task {
            while !Task.isCancelled {
                let b = Self.bytes(root)
                if first && b < expected * 0.97 {
                    progress(min(0.95, b / expected) * 0.8,
                             "Downloading the voice, \(Int(b / 1_000_000)) of about \(Int(expected / 1_000_000)) MB.")
                } else if first {
                    let waited = voiceClock() - t0
                    progress(0.8 + 0.12 * (1 - exp(-waited / 40)), "Getting the voice ready. The first time takes about a minute.")
                } else {
                    progress(min(0.8, (voiceClock() - t0) / 3 * 0.8), "Loading the voice.")
                }
                try? await Task.sleep(nanoseconds: 300_000_000)
            }
        }
        defer { poll.cancel() }
        try await SmartTurnFiles.ensure(in: root)
        let m = PocketTtsManager(defaultVoice: OnDeviceVoiceName.george.rawValue, language: .english, directory: root,
                                 precision: .int8, placement: .gpu)
        do {
            try await m.initialize()
        } catch {
            try await Task.sleep(nanoseconds: 2_000_000_000)
            try await m.initialize()
        }
        manager = m
        progress(0.93, "Getting the voice ready.")
        for v in OnDeviceVoiceName.allCases.reversed() {
            let s = try await m.makeSession(voice: v.rawValue, temperature: Self.temperature, seed: 7)
            _ = try await Self.render(s, text: "Hello there.")
        }
        progress(0.97, "Checking the speed.")
        // Twice, keeping the second: the first run on a new phone includes one-time work.
        _ = try await measure(voice: .george)
        speedCheck = try await measure(voice: .george)
        loadSeconds = voiceClock() - t0
        ready = true
        progress(1, "Ready.")
    }

    private func measure(voice: OnDeviceVoiceName) async throws -> VoiceSpeedCheck {
        let s = try await session(voice: voice, seed: 7)
        let t = voiceClock()
        var firstAt: Double?
        var n = 0
        s.enqueue("Your dentist appointment is Thursday at two thirty. I moved the team call to four.")
        s.finish()
        for try await f in s.frames {
            if firstAt == nil, !f.samples.isEmpty { firstAt = voiceClock() - t }
            n += f.samples.count
        }
        return VoiceSpeedCheck(firstMs: Int((firstAt ?? 0) * 1000), audioSeconds: Double(n) / Double(Self.sampleRate),
                               workSeconds: voiceClock() - t)
    }

    /// One session per reply: the voice and the decoder state carry from one sentence to the next. On the CPU
    /// copy while `useCPU` is on.
    func session(voice: OnDeviceVoiceName, seed: UInt64? = nil) async throws -> PocketTtsSession {
        if useCPU {
            let m = try await cpuVoice()
            return try await m.makeSession(voice: voice.rawValue, temperature: Self.temperature, seed: seed)
        }
        guard let manager else {
            throw NSError(domain: "OnDeviceVoice", code: 1, userInfo: [NSLocalizedDescriptionKey: "The voice is not loaded."])
        }
        return try await manager.makeSession(voice: voice.rawValue, temperature: Self.temperature, seed: seed)
    }

    /// The CPU copy of the voice, loading it the first time (from the files already on the device).
    @discardableResult
    func cpuVoice() async throws -> PocketTtsManager {
        let task: Task<PocketTtsManager, Error> = cpuLock.withLock {
            if let m = cpuManager { return Task { () throws -> PocketTtsManager in m } }
            if let t = cpuLoad { return t }
            let root = self.root
            let t = Task { () throws -> PocketTtsManager in
                let start = voiceClock()
                let m = PocketTtsManager(defaultVoice: OnDeviceVoiceName.george.rawValue, language: .english,
                                         directory: root, precision: .int8, placement: .gpu,
                                         computeUnits: PocketVoiceEngine.backgroundUnits)
                try await m.initialize()
                self.cpuLock.withLock {
                    self.cpuManager = m
                    self.cpuLoadSeconds = voiceClock() - start
                }
                return m
            }
            cpuLoad = t
            return t
        }
        do {
            return try await task.value
        } catch {
            cpuLock.withLock { cpuLoad = nil }
            throw error
        }
    }

    /// Drops the CPU copy (after a call).
    func unloadCPU() async {
        let m: PocketTtsManager? = cpuLock.withLock {
            let m = cpuManager
            cpuManager = nil
            cpuLoad = nil
            cpuWanted = false
            return m
        }
        await m?.cleanup()
    }

    func unload() async {
        ready = false
        await manager?.cleanup()
        manager = nil
        await unloadCPU()
    }

    /// Deletes the downloaded files. The voice downloads again the next time it is needed.
    func removeFiles() throws {
        if FileManager.default.fileExists(atPath: root.path) { try FileManager.default.removeItem(at: root) }
    }

    /// Speaks `text` in a session that is finished afterwards, and returns all its samples.
    static func render(_ s: PocketTtsSession, text: String) async throws -> [Float] {
        s.enqueue(text)
        s.finish()
        var out: [Float] = []
        for try await f in s.frames { out += f.samples }
        return out
    }
}

/// Cuts the silence at the start of one streamed sentence (George starts each with 0.5 to 1.2 s of it), keeping
/// 20 ms of room before the first sound, and tracks the silence the sentence ends with.
struct LeadGate {
    static let threshold: Float = 0.003
    private(set) var open = false
    private(set) var trailMs = 0
    private(set) var cutMs = 0
    private var carry: [Float] = []
    private var margin: [[Float]] = []

    /// Returns the samples to pass on: nothing until the first sound, then everything.
    mutating func feed(_ s: [Float]) -> [Float] {
        let block = 240
        carry += s
        var out: [Float] = []
        var i = 0
        while i + block <= carry.count {
            let b = Array(carry[i..<(i + block)])
            i += block
            var sum: Float = 0
            for x in b { sum += x * x }
            let loud = (sum / Float(block)).squareRoot() > Self.threshold
            if !open {
                if loud {
                    open = true
                    var lead = margin.flatMap { $0 } + b
                    let fade = min(lead.count, 120)
                    for k in 0..<fade { lead[k] *= Float(k) / Float(fade) }
                    out += lead
                    margin = []
                } else {
                    margin.append(b)
                    if margin.count > 2 {
                        margin.removeFirst()
                        cutMs += 10
                    }
                }
            } else {
                out += b
            }
            if open { trailMs = loud ? 0 : trailMs + 10 }
        }
        carry.removeFirst(i)
        if open && !carry.isEmpty {
            out += carry
            carry = []
        }
        return out
    }
}

/// The call's greeting, made ahead of time: several takes with different seeds, scored, and the best one kept,
/// so a call opens at once with a checked take (docs/research/2026-10-09-call-test-hearing.md).
enum GreetingTakes {
    static let count = 8

    struct Take {
        var samples: [Float]
        var seed: Int
        var maxGapMs: Int
        var seconds: Double
        var rise: Double
        var score = 0.0
    }

    static func render(_ engine: PocketVoiceEngine, text: String, voice: OnDeviceVoiceName, takes: Int = count,
                       cancelled: () -> Bool = { false }) async throws -> [Take] {
        var out: [Take] = []
        for seed in 1...takes {
            if cancelled() { break }
            let s = try await engine.session(voice: voice, seed: UInt64(seed))
            var gate = LeadGate()
            let raw = try await PocketVoiceEngine.render(s, text: text)
            let samples = trim(gate.feed(raw))
            guard !samples.isEmpty else { continue }
            let a = analyze(samples)
            out.append(Take(samples: samples, seed: seed, maxGapMs: a.maxGapMs, seconds: a.seconds, rise: a.rise))
        }
        return score(out)
    }

    /// Best first. A gap between words over 150 ms costs the most, then distance from the typical length, and a
    /// rising end on the question earns a little.
    static func score(_ takes: [Take]) -> [Take] {
        let clean = takes.filter { $0.maxGapMs <= 150 }.map(\.seconds).sorted()
        let all = takes.map(\.seconds).sorted()
        let pool = clean.isEmpty ? all : clean
        let typical = pool.isEmpty ? 0 : pool[pool.count / 2]
        return takes.map { t in
            var t = t
            var s = 0.0
            if t.maxGapMs > 150 { s -= 10 + Double(t.maxGapMs - 150) / 50 }
            s -= abs(t.seconds - typical) / 0.1
            s += 4 * max(-0.3, min(0.3, t.rise - 1))
            t.score = s
            return t
        }.sorted { $0.score > $1.score }
    }

    private static func frameRMS(_ s: [Float], frame: Int = 240) -> [Float] {
        (0..<(s.count / frame)).map { fi in
            var sum: Float = 0
            for i in (fi * frame)..<((fi + 1) * frame) { sum += s[i] * s[i] }
            return (sum / Float(frame)).squareRoot()
        }
    }

    private static func threshold(_ r: [Float]) -> Float { max(0.0015, (r.max() ?? 0) * 0.02) }

    static func trim(_ s: [Float]) -> [Float] {
        let r = frameRMS(s)
        let thr = threshold(r)
        guard let a = r.firstIndex(where: { $0 > thr }), let b = r.lastIndex(where: { $0 > thr }) else { return [] }
        let start = max(0, a * 240 - 480)
        let end = min(s.count, (b + 1) * 240 + 1920)
        var out = Array(s[start..<end])
        for k in 0..<min(120, out.count) { out[k] *= Float(k) / 120 }
        let fade = min(480, out.count)
        for k in 0..<fade { out[out.count - 1 - k] *= Float(k) / Float(fade) }
        return out
    }

    static func analyze(_ s: [Float]) -> (maxGapMs: Int, seconds: Double, rise: Double) {
        let r = frameRMS(s)
        let thr = threshold(r)
        guard let a = r.firstIndex(where: { $0 > thr }), let b = r.lastIndex(where: { $0 > thr }) else { return (0, 0, 1) }
        var best = 0, run = 0
        for i in a...b { if r[i] <= thr { run += 1; best = max(best, run) } else { run = 0 } }
        var f0: [Double] = []
        let win = 960, minLag = 24000 / 350, maxLag = 24000 / 70
        var i = a * 240
        while i + win + maxLag < s.count && i < (b + 1) * 240 {
            let fi = i / 240
            if fi < r.count, r[fi] > thr * 4 {
                var e0: Float = 0
                for k in 0..<win { e0 += s[i + k] * s[i + k] }
                var bestLag = 0
                var bestC: Float = 0
                for lag in stride(from: minLag, through: maxLag, by: 1) {
                    var c: Float = 0, e1: Float = 0
                    for k in 0..<win {
                        c += s[i + k] * s[i + k + lag]
                        e1 += s[i + k + lag] * s[i + k + lag]
                    }
                    let n = c / max(1e-9, (e0 * e1).squareRoot())
                    if n > bestC { bestC = n; bestLag = lag }
                }
                if bestC > 0.6 { f0.append(24000 / Double(bestLag)) }
            }
            i += 240
        }
        var rise = 1.0
        if f0.count >= 16 {
            let tail = Array(f0.suffix(8)).sorted()
            let body = Array(f0.dropLast(8)).sorted()
            rise = tail[tail.count / 2] / body[body.count / 2]
        }
        return (best * 10, Double(b - a + 1) * 0.01, rise)
    }
}

/// Pipecat's Smart Turn v3.2 end-of-turn model (BSD-2-Clause), downloaded with the voice instead of shipping in the
/// app. The files come from a Core ML conversion pinned to one commit, and each one is checked against the hash of
/// the copy the call test ran on Colin's iPhone (byte for byte the same files).
enum SmartTurnFiles {
    static let repo = "https://huggingface.co/aufklarer/Smart-Turn-v3.2-CoreML/resolve/75f069717f500947195fa1945c46ddb472559cff/"
    static let files: [(path: String, sha256: String)] = [
        ("smart_turn.mlmodelc/model.mil", "41a6d9ac0f7d01d738e56ecdf4435e55f139591541b7cf1a4dba6b9ce2f1c9ef"),
        ("smart_turn.mlmodelc/coremldata.bin", "9245ac99889c9d738cba6c89da9e851fd16488bf8effbc15a109d06b401c9383"),
        ("smart_turn.mlmodelc/analytics/coremldata.bin", "1646746d0029e78d26be065e62602d4f89e51013ec238b653adf7d7d21057f89"),
        ("smart_turn.mlmodelc/weights/weight.bin", "b6295316741a0149e1fcab7cdf030e1b9bfb0eef778207accee91a2a9703a39c"),
    ]

    static func folder(_ root: URL) -> URL { root.appendingPathComponent("SmartTurn", isDirectory: true) }

    /// The compiled model, once every file is here and checked.
    static func model(in root: URL) -> URL? {
        let url = folder(root).appendingPathComponent("smart_turn.mlmodelc")
        let done = folder(root).appendingPathComponent(".checked")
        return FileManager.default.fileExists(atPath: done.path) ? url : nil
    }

    static func ensure(in root: URL) async throws {
        if model(in: root) != nil { return }
        let base = folder(root)
        for f in files {
            let dest = base.appendingPathComponent(f.path)
            if let have = try? Data(contentsOf: dest), hex(SHA256.hash(data: have)) == f.sha256 { continue }
            let (tmp, resp) = try await URLSession.shared.download(from: URL(string: repo + f.path)!)
            guard (resp as? HTTPURLResponse)?.statusCode == 200 else {
                throw NSError(domain: "OnDeviceVoice", code: 2, userInfo: [NSLocalizedDescriptionKey: "Smart Turn download failed."])
            }
            let data = try Data(contentsOf: tmp)
            try? FileManager.default.removeItem(at: tmp)
            guard hex(SHA256.hash(data: data)) == f.sha256 else {
                throw NSError(domain: "OnDeviceVoice", code: 3, userInfo: [NSLocalizedDescriptionKey: "Smart Turn file did not match."])
            }
            try FileManager.default.createDirectory(at: dest.deletingLastPathComponent(), withIntermediateDirectories: true)
            try data.write(to: dest, options: .atomic)
        }
        FileManager.default.createFile(atPath: base.appendingPathComponent(".checked").path, contents: Data())
    }

    private static func hex(_ d: SHA256.Digest) -> String { d.map { String(format: "%02x", $0) }.joined() }
}
