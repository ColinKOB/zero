import AVFoundation

/// The ringing you hear while the call voice is loaded with what it knows about Colin: the North
/// American ringback tone (440 Hz and 480 Hz together, two seconds on, four off), made in code.
final class Ringback {
    private var player: AVAudioPlayer?

    func start() {
        guard player == nil else { return }
        let rate = 22_050.0, onSec = 2.0, cycleSec = 6.0
        let frames = Int(rate * cycleSec)
        var samples = [Int16](repeating: 0, count: frames)
        for i in 0..<Int(rate * onSec) {
            let t = Double(i) / rate
            // A short fade at each end keeps the tone from clicking.
            let fade = min(1, min(t, onSec - t) / 0.02)
            let v = (sin(2 * .pi * 440 * t) + sin(2 * .pi * 480 * t)) * 0.18 * fade
            samples[i] = Int16(v * Double(Int16.max))
        }
        guard let data = Self.wav(samples, rate: Int(rate)), let p = try? AVAudioPlayer(data: data) else { return }
        p.numberOfLoops = -1
        p.volume = 0.8
        p.play()
        player = p
    }

    func stop() {
        player?.stop()
        player = nil
    }

    private static func wav(_ samples: [Int16], rate: Int) -> Data? {
        var d = Data()
        func put<T>(_ v: T) { withUnsafeBytes(of: v) { d.append(contentsOf: $0) } }
        let bytes = samples.count * 2
        d.append(contentsOf: Array("RIFF".utf8)); put(UInt32(36 + bytes).littleEndian)
        d.append(contentsOf: Array("WAVEfmt ".utf8)); put(UInt32(16).littleEndian)
        put(UInt16(1).littleEndian); put(UInt16(1).littleEndian)
        put(UInt32(rate).littleEndian); put(UInt32(rate * 2).littleEndian)
        put(UInt16(2).littleEndian); put(UInt16(16).littleEndian)
        d.append(contentsOf: Array("data".utf8)); put(UInt32(bytes).littleEndian)
        samples.withUnsafeBytes { d.append(contentsOf: $0) }
        return d
    }
}
