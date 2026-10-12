// On-Device Voice: the call's audio. The voice-processed microphone (so the person can talk over the voice), the
// 24 kHz player for the voice, and the microphone pipeline: 16 kHz audio for speech recognition and the end-of-turn
// model, a voice detector, and the interruption detector. Ported from the call test (AudioIO.swift,
// MicPipeline.swift), with its fixes: the player is reset after the audio route changes at call start, and
// playback is timed by the clock because voice chat mode can skip completion callbacks.
import AVFoundation
import AudioToolbox
import CoreAudio
import Foundation

/// Where an event goes in the saved call (CallEngine.note). Set by the call; silent otherwise.
nonisolated(unsafe) var onDeviceNote: (String, [String: Any]) -> Void = { _, _ in }

/// The microphone reported no usable format (a sample rate of 0 or no channels), which happens on an input route
/// with no microphone yet. The call ends with an error instead of crashing in the audio engine.
struct NoMicrophoneFormat: LocalizedError {
    let rate: Double
    var errorDescription: String? { "The microphone is not ready. Try the call again in a moment." }
}

/// A sample rate the audio engine can use: finite and above zero.
func usableRate(_ rate: Double) -> Bool { rate.isFinite && rate > 0 }

final class CallAudio: VoiceOutput {
    let engine = AVAudioEngine()
    let player = AVAudioPlayerNode()
    let outFormat = AVAudioFormat(standardFormatWithSampleRate: 24000, channels: 1)!

    /// Mono microphone samples, the host time of the first one, and the rate. Called on an audio thread.
    var onMic: (([Float], Double, Double) -> Void)?

    private let lock = NSLock()
    private var playEnd: Double = 0
    private var sink: AVAudioSinkNode?
    private var sinkCallbacks = 0
    private var observers: [NSObjectProtocol] = []
    private(set) var micMode = "none"
    private(set) var inRate: Double = 48000

    /// `sessionReady`: CallKit (or the call's fallback) already set up and activated the audio session.
    func start(sessionReady: Bool) throws {
        let s = AVAudioSession.sharedInstance()
        if !sessionReady {
            try s.setCategory(.playAndRecord, mode: .voiceChat, options: [.allowBluetoothHFP, .allowBluetoothA2DP])
            try s.setActive(true)
        }
        try? s.setPreferredIOBufferDuration(0.01)
        let input = engine.inputNode
        do {
            try input.setVoiceProcessingEnabled(true)
        } catch {
            onDeviceNote("voice_processing_failed", ["error": "\(error)"])
        }
        let fmt = input.outputFormat(forBus: 0)
        guard usableRate(fmt.sampleRate), fmt.channelCount > 0 else {
            onDeviceNote("mic_format_unusable", ["in_rate": fmt.sampleRate, "channels": Int(fmt.channelCount),
                                                 "stage": "start"])
            throw NoMicrophoneFormat(rate: fmt.sampleRate)
        }
        inRate = fmt.sampleRate
        engine.attach(player)
        engine.connect(player, to: engine.mainMixerNode, format: outFormat)
        let sink = AVAudioSinkNode { [weak self] ts, frames, abl in
            self?.render(ts, frames, abl)
            return noErr
        }
        engine.attach(sink)
        engine.connect(input, to: sink, format: fmt)
        self.sink = sink
        micMode = "sink"
        engine.prepare()
        try engine.start()
        player.play()
        onDeviceNote("audio_started", ["in_rate": fmt.sampleRate, "io_buffer_ms": Int(s.ioBufferDuration * 1000),
                                       "output_latency_ms": Int(s.outputLatency * 1000)])
        observers.append(NotificationCenter.default.addObserver(
            forName: .AVAudioEngineConfigurationChange, object: engine, queue: .main
        ) { [weak self] _ in self?.resetPlayer() })
        // Some routes never deliver input to the sink node; a tap works there.
        DispatchQueue.main.asyncAfter(deadline: .now() + 1.2) { [weak self] in self?.checkSink() }
    }

    /// The route change at the start of a call left the player silent until something restarted it (call test,
    /// Oct 9: no sound for 30 to 60 s). Reset the player and its connection whenever the engine's setup changes.
    private func resetPlayer() {
        lock.lock(); playEnd = 0; lock.unlock()
        player.stop()
        engine.disconnectNodeOutput(player)
        engine.connect(player, to: engine.mainMixerNode, format: outFormat)
        engine.prepare()
        if !engine.isRunning {
            do { try engine.start() } catch { onDeviceNote("audio_restart_failed", ["error": "\(error)"]) }
        }
        player.volume = 1.0
        player.play()
        onDeviceNote("player_reset", ["running": engine.isRunning, "playing": player.isPlaying])
    }

    private func checkSink() {
        lock.lock(); let n = sinkCallbacks; lock.unlock()
        guard micMode == "sink", n == 0, let sink else { return }
        let fmt = engine.inputNode.outputFormat(forBus: 0)
        guard usableRate(fmt.sampleRate), fmt.channelCount > 0 else {
            // A tap with this format would crash the engine; keep the sink and say so in the saved call.
            onDeviceNote("mic_format_unusable", ["in_rate": fmt.sampleRate, "channels": Int(fmt.channelCount),
                                                 "stage": "fallback_tap"])
            return
        }
        onDeviceNote("mic_fallback_tap", [:])
        engine.stop()
        engine.disconnectNodeOutput(engine.inputNode)
        engine.detach(sink)
        self.sink = nil
        inRate = fmt.sampleRate
        engine.inputNode.installTap(onBus: 0, bufferSize: 480, format: fmt) { [weak self] buf, when in
            guard let self, let ch = buf.floatChannelData else { return }
            let n = Int(buf.frameLength)
            let out = Array(UnsafeBufferPointer(start: ch[0], count: n))
            let host = when.isHostTimeValid ? hostSeconds(when.hostTime) : hostNow() - Double(n) / self.inRate
            self.onMic?(out, host, self.inRate)
        }
        micMode = "tap"
        try? engine.start()
        player.play()
    }

    private func render(_ ts: UnsafePointer<AudioTimeStamp>, _ frames: AVAudioFrameCount, _ abl: UnsafePointer<AudioBufferList>) {
        let list = UnsafeMutableAudioBufferListPointer(UnsafeMutablePointer(mutating: abl))
        guard let first = list.first, let data = first.mData else { return }
        let n = Int(frames)
        let stride = max(1, Int(first.mNumberChannels))
        let ptr = data.assumingMemoryBound(to: Float.self)
        var out = [Float](repeating: 0, count: n)
        for i in 0..<n { out[i] = ptr[i * stride] }
        let t = ts.pointee
        let host = t.mFlags.contains(.hostTimeValid) ? hostSeconds(t.mHostTime)
            : hostNow() - (usableRate(inRate) ? Double(n) / inRate : 0)
        lock.lock(); sinkCallbacks += 1; lock.unlock()
        onMic?(out, host, inRate)
    }

    var outputDelay: Double {
        let s = AVAudioSession.sharedInstance()
        return s.outputLatency + s.ioBufferDuration
    }

    @discardableResult
    func schedule(_ samples: [Float]) -> (start: Double, end: Double) {
        guard !samples.isEmpty,
            let buf = AVAudioPCMBuffer(pcmFormat: outFormat, frameCapacity: AVAudioFrameCount(samples.count))
        else { return (hostNow(), hostNow()) }
        buf.frameLength = AVAudioFrameCount(samples.count)
        samples.withUnsafeBufferPointer { buf.floatChannelData![0].update(from: $0.baseAddress!, count: samples.count) }
        let dur = Double(samples.count) / 24000
        lock.lock()
        let start = max(playEnd, hostNow() + outputDelay)
        playEnd = start + dur
        lock.unlock()
        player.scheduleBuffer(buf, completionHandler: nil)
        if !player.isPlaying { player.play() }
        return (start, start + dur)
    }

    /// True while queued audio has not finished (by the clock: voice chat mode can skip callbacks).
    var isPlaying: Bool { lock.lock(); defer { lock.unlock() }; return hostNow() < playEnd }
    var playEndHost: Double { lock.lock(); defer { lock.unlock() }; return playEnd }

    /// Stops at once and drops everything queued.
    func stopPlayback() {
        lock.lock(); playEnd = 0; lock.unlock()
        player.stop()
        player.volume = 1.0
        player.play()
    }

    /// Lowers the voice while the app checks whether a sound on the speaker was the person.
    func duck(_ on: Bool) { player.volume = on ? 0.3 : 1.0 }

    /// Earbuds and headphones get the fast interruption; the speaker, the earpiece, a car or another Bluetooth
    /// speaker get the echo-safe one.
    static func routeKind() -> String {
        guard let out = AVAudioSession.sharedInstance().currentRoute.outputs.first else { return "none" }
        switch out.portType {
        case .builtInSpeaker: return "speaker"
        case .builtInReceiver: return "receiver"
        case .headphones, .usbAudio: return "earbuds"
        case .carAudio: return "car"
        case .bluetoothA2DP, .bluetoothHFP, .bluetoothLE:
            let n = out.portName.lowercased()
            let ear = ["airpods", "beats", "buds", "pods", "headphone", "earphone", "jabra", "sony wf", "sony wh"].contains { n.contains($0) }
            return ear ? "earbuds" : "bluetooth"
        default: return "other"
        }
    }

    func stop() {
        for o in observers { NotificationCenter.default.removeObserver(o) }
        observers = []
        onMic = nil
        player.stop()
        if micMode == "tap" { engine.inputNode.removeTap(onBus: 0) }
        engine.stop()
        try? engine.inputNode.setVoiceProcessingEnabled(false)
    }
}

/// What the mic heard when an interruption fired, for the saved call.
struct BargeInfo {
    let rms: Float
    let threshold: Float
    let echoPeak: Float
    let noiseFloor: Float
    var fields: [String: Any] {
        ["rms": Double(rms), "threshold": Double(threshold), "echo_peak": Double(echoPeak), "noise_floor": Double(noiseFloor)]
    }
}

/// Resamples the mic to 16 kHz, keeps recent audio for the end-of-turn model, feeds speech recognition, and runs
/// an energy voice detector with a stricter interruption detector while the voice talks.
final class MicPipeline: @unchecked Sendable {
    let queue = DispatchQueue(label: "ondevice.mic", qos: .userInteractive)
    let fmt16 = AVAudioFormat(commonFormat: .pcmFormatFloat32, sampleRate: 16000, channels: 1, interleaved: false)!

    var feedASR: ((AVAudioPCMBuffer, Double) -> Void)?
    var onSpeechStart: ((Double) -> Void)?
    var onBargeIn: ((Double, BargeInfo) -> Void)?
    var onBargeCandidate: ((Double, BargeInfo) -> Void)?
    var isVoicePlaying: () -> Bool = { false }
    var stopVoice: () -> Void = {}

    private let lock = NSLock()
    private var converter: AVAudioConverter?
    private var srcRate: Double = 0
    private let ringSize = 16000 * 20
    private var ring = [Float](repeating: 0, count: 16000 * 20)
    private var total = 0
    private var hostOffset: Double = 0
    private var frameAcc: [Float] = []
    private var noiseFloor: Float = 0.002
    private var run = 0
    private var bargeRun = 0
    private var voicedStreak = 0
    private var inSpeech = false
    private var lastVoice: Double = 0
    private var lastRMS: Float = 0
    private var silenced = false
    private var echoGuardOn = true
    private var echoPeak: Float = 0
    private var candidateOpen = false

    var bargeMin: Float = 0.03
    var bargeFrames = 4
    var speechMin: Float = 0.008

    var echoGuard: Bool {
        get { lock.lock(); defer { lock.unlock() }; return echoGuardOn }
        set { lock.lock(); echoGuardOn = newValue; lock.unlock() }
    }

    /// Muted or on hold: the mic is ignored (silence goes to recognition so its clock keeps running).
    var muted: Bool {
        get { lock.lock(); defer { lock.unlock() }; return silenced }
        set { lock.lock(); silenced = newValue; lock.unlock() }
    }

    /// A rejected candidate was the voice's own echo, so its level raises the learned echo peak.
    func clearCandidate(echoLevel: Float? = nil) {
        queue.async { [weak self] in
            guard let self else { return }
            self.candidateOpen = false
            self.bargeRun = 0
            if let echoLevel { self.echoPeak = max(self.echoPeak, echoLevel) }
        }
    }

    /// After a confirmed echo-safe interruption: the person is talking now.
    func markSpeaking() {
        queue.async { [weak self] in
            guard let self else { return }
            self.lock.lock(); self.inSpeech = true; self.lastVoice = hostNow(); self.lock.unlock()
            self.run = 3
        }
    }

    var lastVoiceHost: Double { lock.lock(); defer { lock.unlock() }; return lastVoice }
    var speaking: Bool { lock.lock(); defer { lock.unlock() }; return inSpeech }
    var level: Float { lock.lock(); defer { lock.unlock() }; return lastRMS }
    var floor: Float { lock.lock(); defer { lock.unlock() }; return noiseFloor }
    func hostToAudio(_ h: Double) -> Double { lock.lock(); defer { lock.unlock() }; return h - hostOffset }
    var audioNow: Double { lock.lock(); defer { lock.unlock() }; return Double(total) / 16000 }

    func ingest(_ samples: [Float], host: Double, rate: Double) {
        queue.async { [weak self] in
            guard let self, var out = self.resample(samples, rate: rate) else { return }
            if self.muted { out = [Float](repeating: 0, count: out.count) }
            self.process16k(out, host: host)
        }
    }

    private var badRateNoted = false

    private func resample(_ samples: [Float], rate: Double) -> [Float]? {
        if rate == 16000 { return samples }
        guard !samples.isEmpty else { return nil }
        if converter == nil || srcRate != rate {
            // A rate of 0 (an input route with no microphone yet) has no format: drop the audio and note it once,
            // instead of crashing. Audio at a usable rate resumes normally.
            guard usableRate(rate), let from = AVAudioFormat(standardFormatWithSampleRate: rate, channels: 1) else {
                if !badRateNoted {
                    badRateNoted = true
                    onDeviceNote("mic_rate_unusable", ["rate": rate, "frames": samples.count])
                }
                converter = nil
                return nil
            }
            badRateNoted = false
            srcRate = rate
            converter = AVAudioConverter(from: from, to: fmt16)
        }
        guard let converter, let inBuf = AVAudioPCMBuffer(pcmFormat: converter.inputFormat, frameCapacity: AVAudioFrameCount(samples.count))
        else { return nil }
        inBuf.frameLength = AVAudioFrameCount(samples.count)
        samples.withUnsafeBufferPointer { inBuf.floatChannelData![0].update(from: $0.baseAddress!, count: samples.count) }
        let cap = AVAudioFrameCount(Double(samples.count) * 16000 / rate + 32)
        guard let out = AVAudioPCMBuffer(pcmFormat: fmt16, frameCapacity: cap) else { return nil }
        var fed = false
        var err: NSError?
        converter.convert(to: out, error: &err) { _, status in
            if fed { status.pointee = .noDataNow; return nil }
            fed = true
            status.pointee = .haveData
            return inBuf
        }
        return Array(UnsafeBufferPointer(start: out.floatChannelData![0], count: Int(out.frameLength)))
    }

    private func process16k(_ s: [Float], host: Double) {
        guard !s.isEmpty else { return }
        lock.lock()
        let startAudio = Double(total) / 16000
        hostOffset = host - startAudio
        for x in s {
            ring[total % ringSize] = x
            total += 1
        }
        lock.unlock()
        if let feedASR, let buf = AVAudioPCMBuffer(pcmFormat: fmt16, frameCapacity: AVAudioFrameCount(s.count)) {
            buf.frameLength = AVAudioFrameCount(s.count)
            s.withUnsafeBufferPointer { buf.floatChannelData![0].update(from: $0.baseAddress!, count: s.count) }
            feedASR(buf, startAudio)
        }
        let accStart = Double(total - s.count - frameAcc.count) / 16000
        frameAcc.append(contentsOf: s)
        var idx = 0
        while frameAcc.count - idx >= 160 {
            var sum: Float = 0
            for i in idx..<(idx + 160) { sum += frameAcc[i] * frameAcc[i] }
            let rms = (sum / 160).squareRoot()
            vad(rms, at: hostOffset + accStart + Double(idx + 160) / 16000)
            idx += 160
        }
        frameAcc.removeFirst(idx)
    }

    private func vad(_ rms: Float, at t: Double) {
        lock.lock(); lastRMS = rms; lock.unlock()
        if isVoicePlaying() {
            lock.lock(); let guarded = echoGuardOn; lock.unlock()
            // Only a clear, sustained voice counts while the voice talks; on the speaker the bar is twice as high and
            // sits above the loudest recent echo.
            let thr = guarded ? max(noiseFloor * 6, bargeMin * 2, echoPeak * 1.5) : max(noiseFloor * 6, bargeMin)
            if rms > thr {
                bargeRun += 1
                if bargeRun >= bargeFrames && !candidateOpen {
                    bargeRun = 0
                    let onset = t - Double(bargeFrames) * 0.01
                    let info = BargeInfo(rms: rms, threshold: thr, echoPeak: echoPeak, noiseFloor: noiseFloor)
                    if guarded {
                        candidateOpen = true
                        onBargeCandidate?(onset, info)
                    } else {
                        stopVoice()
                        lock.lock(); inSpeech = true; lastVoice = t; lock.unlock()
                        run = 3
                        onBargeIn?(onset, info)
                    }
                }
            } else {
                bargeRun = 0
                if guarded && !candidateOpen { echoPeak = max(echoPeak * 0.998, rms) }   // about a 3 s half-life
            }
            return
        }
        echoPeak *= 0.999
        bargeRun = 0
        let thr = max(noiseFloor * 3.5, speechMin)
        if rms > thr {
            run += 1
            voicedStreak += 1
            if voicedStreak > 500 { noiseFloor = min(noiseFloor * 1.01, 0.05) }   // a loud steady room
            lock.lock()
            if run >= 3 && !inSpeech {
                inSpeech = true
                lastVoice = t
                lock.unlock()
                onSpeechStart?(t - 0.03)
            } else {
                if inSpeech { lastVoice = t }
                lock.unlock()
            }
        } else {
            run = 0
            voicedStreak = 0
            noiseFloor = max(0.0003, min(0.05, noiseFloor * 0.95 + rms * 0.05))
            lock.lock()
            if inSpeech && t - lastVoice > 0.12 { inSpeech = false }
            lock.unlock()
        }
    }

    /// Recent audio from audio-timeline second `from`, at most `maxSeconds`, at 16 kHz.
    func audio(from: Double, maxSeconds: Double = 8) -> [Float] {
        lock.lock(); defer { lock.unlock() }
        let end = total
        var start = max(0, Int(from * 16000))
        start = max(start, end - Int(maxSeconds * 16000), end - ringSize + 1)
        guard end > start else { return [] }
        var out = [Float](repeating: 0, count: end - start)
        for i in start..<end { out[i - start] = ring[i % ringSize] }
        return out
    }
}
