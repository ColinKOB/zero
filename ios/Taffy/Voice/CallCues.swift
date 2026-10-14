import AVFoundation
import Foundation

enum Earcon: String, CaseIterable {
    case started
    case working
    case done

    var peak: Float {
        switch self {
        case .started: return 0.12
        case .working: return 0.055
        case .done: return 0.13
        }
    }

    var seconds: Double { Double(samples(rate: 24_000).count) / 24_000 }

    func samples(rate: Double) -> [Float] {
        var out: [Float]
        switch self {
        case .started:
            out = Self.glide(from: 520, to: 780, glideSeconds: 0.11, seconds: 0.21, rate: rate)
        case .working:
            let tap = Self.note(freq: 640, seconds: 0.06, decay: 0.02, partials: [(2, 0.1)], rate: rate)
            out = tap + [Float](repeating: 0, count: Int(0.08 * rate)) + tap
        case .done:
            let low = Self.note(freq: 660, seconds: 0.1, decay: 0.05, partials: [(2, 0.2), (3, 0.06)], rate: rate)
            let high = Self.note(freq: 990, seconds: 0.22, decay: 0.08, partials: [(2, 0.25), (3, 0.08)], rate: rate)
            let offset = Int(0.085 * rate)
            out = [Float](repeating: 0, count: offset + high.count)
            for (i, x) in low.enumerated() { out[i] += x }
            for (i, x) in high.enumerated() { out[offset + i] += x }
        }
        return Self.normalized(out, peak: peak)
    }

    static func note(freq: Double, seconds: Double, decay: Double, partials: [(Double, Double)], rate: Double) -> [Float] {
        let n = Int(seconds * rate)
        return (0..<n).map { i in
            let t = Double(i) / rate
            var v = sin(2 * .pi * freq * t)
            for (mult, level) in partials { v += level * sin(2 * .pi * freq * mult * t) * exp(-t / (decay * 0.6)) }
            return Float(v * envelope(t, seconds: seconds, decay: decay))
        }
    }

    static func glide(from f0: Double, to f1: Double, glideSeconds: Double, seconds: Double, rate: Double) -> [Float] {
        let n = Int(seconds * rate)
        var phase = 0.0
        return (0..<n).map { i in
            let t = Double(i) / rate
            let k = min(1, t / glideSeconds)
            let f = f0 + (f1 - f0) * (1 - pow(1 - k, 2))
            phase += 2 * .pi * f / rate
            let v = sin(phase) + 0.12 * sin(2 * phase)
            return Float(v * envelope(t, seconds: seconds, decay: 0.07, hold: glideSeconds * 0.6))
        }
    }

    private static func envelope(_ t: Double, seconds: Double, decay: Double, hold: Double = 0) -> Double {
        let attack = 0.006, release = 0.015
        var e = t < attack ? 0.5 - 0.5 * cos(.pi * t / attack) : 1
        if t > hold { e *= exp(-(t - hold) / decay) }
        let left = seconds - t
        if left < release { e *= max(0, left / release) }
        return e
    }

    private static func normalized(_ s: [Float], peak: Float) -> [Float] {
        let top = s.map(abs).max() ?? 0
        guard top > 0 else { return s }
        var out = s.map { $0 / top * peak }
        if !out.isEmpty { out[out.count - 1] = 0 }
        return out
    }

    func wav(rate: Int = 24_000) -> Data {
        let pcm = samples(rate: Double(rate)).map { Int16(max(-1, min(1, $0)) * Float(Int16.max)) }
        var d = Data()
        func put<T>(_ v: T) { withUnsafeBytes(of: v) { d.append(contentsOf: $0) } }
        let bytes = pcm.count * 2
        d.append(contentsOf: Array("RIFF".utf8)); put(UInt32(36 + bytes).littleEndian)
        d.append(contentsOf: Array("WAVEfmt ".utf8)); put(UInt32(16).littleEndian)
        put(UInt16(1).littleEndian); put(UInt16(1).littleEndian)
        put(UInt32(rate).littleEndian); put(UInt32(rate * 2).littleEndian)
        put(UInt16(2).littleEndian); put(UInt16(16).littleEndian)
        d.append(contentsOf: Array("data".utf8)); put(UInt32(bytes).littleEndian)
        pcm.withUnsafeBytes { d.append(contentsOf: $0) }
        return d
    }
}

struct CueGate {
    struct Pending: Equatable {
        let kind: Earcon
        let task: Int
        let arrived: Double
    }

    static func window(_ kind: Earcon) -> Double {
        switch kind {
        case .started: return 3
        case .working: return 2
        case .done: return 1.5
        }
    }

    static let minGap = 1.2

    private(set) var pending: [Pending] = []
    private(set) var heard: Set<Int> = []
    private var lastPlayed = -Double.infinity

    mutating func add(_ kind: Earcon, task: Int, now: Double) {
        if kind == .done {
            pending.removeAll { $0.task == task }
        } else {
            pending.removeAll { $0.task == task && $0.kind == kind }
        }
        pending.append(Pending(kind: kind, task: task, arrived: now))
    }

    mutating func next(now: Double, quiet: Bool) -> Earcon? {
        pending.removeAll { now - $0.arrived > Self.window($0.kind) }
        pending.removeAll { $0.kind == .done && !heard.contains($0.task) }
        guard quiet, now - lastPlayed >= Self.minGap, !pending.isEmpty else { return nil }
        let p = pending.removeFirst()
        lastPlayed = now
        if p.kind != .done { heard.insert(p.task) }
        return p.kind
    }

    mutating func clearProgress() {
        pending.removeAll { $0.kind != .done }
    }
}

enum CallRules {
    static let lateGreetingMs = 6000

    static let connectingFirst = 0.6
    static let connectingEvery = 6.0
    static let connectingMax = 6

    static let missedWordsSeconds = 1.0
    static let missedWordsEvery = 20.0

    static func askAgain(voicedSeconds: Double, sinceLastAsk: Double) -> Bool {
        voicedSeconds >= missedWordsSeconds && sinceLastAsk >= missedWordsEvery
    }

    static let feminineChatGPTVoices: Set<String> = ["juniper", "maple", "sol", "vale"]

    static func onDeviceVoice(matching chatGPTVoice: String) -> String {
        feminineChatGPTVoices.contains(chatGPTVoice) ? "alba" : "george"
    }

    static func voiceChosen(current: String?, reported: String?) -> Bool {
        guard let current else { return false }
        return current != reported
    }
}

/// Plays work sounds through their own player, for the ChatGPT voice (its audio comes over WebRTC) and while a
/// call connects. The On-Device Voice plays them through the call's own player instead, so its echo
/// cancellation hears them.
final class CuePlayer {
    private var players: [Earcon: AVAudioPlayer] = [:]
    private var playing: AVAudioPlayer?

    func play(_ e: Earcon) {
        let p: AVAudioPlayer
        if let cached = players[e] {
            p = cached
        } else {
            guard let made = try? AVAudioPlayer(data: e.wav()) else { return }
            players[e] = made
            p = made
        }
        p.currentTime = 0
        p.volume = 1
        p.play()
        playing = p
    }

    var isPlaying: Bool { playing?.isPlaying ?? false }

    func stop() {
        playing?.stop()
        playing = nil
    }
}
