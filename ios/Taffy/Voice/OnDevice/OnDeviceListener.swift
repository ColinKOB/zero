// On-Device Voice: hearing the person. Apple's on-device speech recognition (SpeechAnalyzer with SpeechTranscriber
// on iOS 26 and later, SFSpeechRecognizer on device before that) and Pipecat's Smart Turn v3.2 end-of-turn model
// (Core ML, BSD-2-Clause, bundled). Ported from the call test with the hearing fixes of Oct 9
// (docs/research/2026-10-09-call-test-hearing.md): finalize once at the end of a turn, not at every pause; keep the
// volatile words when a final result lost some; remove punctuation left standing alone.
import AVFoundation
import CoreMedia
import CoreML
import Foundation
import Speech

final class CallListener: @unchecked Sendable {
    struct Segment { var start: Double; var end: Double; var text: String }

    private(set) var kind = "none"
    /// Called on the main queue whenever the words change.
    var onUpdate: (() -> Void)?

    private let lock = NSLock()
    private var finals: [Segment] = []
    private var volatileSeg: Segment?
    private var volatileHistory: [Segment] = []
    private var lastUpdate: Double = 0
    private var repaired = 0
    private var lastFinalizeMs: Double = 0

    // SpeechAnalyzer (iOS 26 and later), kept type-erased so the app still runs on iOS 17.
    private var analyzerBox: AnyObject?

    private var sfRec: SFSpeechRecognizer?
    private var sfReq: SFSpeechAudioBufferRecognitionRequest?
    private var sfTask: SFSpeechRecognitionTask?
    private var sfText = ""

    var lastUpdateHost: Double { lock.withLock { lastUpdate } }
    var repairedCount: Int { lock.withLock { repaired } }
    var finalizeMs: Double { lock.withLock { lastFinalizeMs } }

    func start() async {
        if #available(iOS 26.0, macCatalyst 26.0, *), SpeechTranscriber.isAvailable {
            do {
                let box = try await AnalyzerBox.start(locale: Locale(identifier: "en-US")) { [weak self] text, s, e, final in
                    self?.got(text, start: s, end: e, final: final)
                }
                lock.withLock { analyzerBox = box }
                kind = "SpeechAnalyzer"
                onDeviceNote("speech_started", ["kind": kind])
                return
            } catch {
                onDeviceNote("speech_start_failed", ["kind": "SpeechAnalyzer", "error": "\(error)"])
            }
        }
        startSF()
    }

    private func got(_ text: String, start: Double, end: Double, final: Bool) {
        lock.lock()
        var seg = Segment(start: start, end: end, text: text.trimmingCharacters(in: .whitespacesAndNewlines))
        var repairedFrom: String?
        if final {
            // The final pass can drop words the volatile pass heard, mostly at the start of a segment. Keep the
            // volatile words then, from the last hypothesis that does not reach past this final.
            if let v = volatileHistory.last(where: { $0.end <= end + 0.05 }),
                HeardText.wordCount(v.text) > HeardText.wordCount(seg.text)
            {
                repairedFrom = seg.text
                var t = v.text
                if let p = seg.text.last, ".?!".contains(p), !(t.last.map { ".?!".contains($0) } ?? false) { t.append(p) }
                seg.text = t
                repaired += 1
            }
            volatileHistory.removeAll()
            if !seg.text.isEmpty { finals.append(seg) }
            if let v = volatileSeg, v.start < end + 0.05 { volatileSeg = nil }
            if finals.count > 200 { finals.removeFirst(finals.count - 200) }
        } else {
            volatileSeg = seg.text.isEmpty ? nil : seg
            if !seg.text.isEmpty {
                volatileHistory.append(seg)
                if volatileHistory.count > 400 { volatileHistory.removeFirst(200) }
            }
        }
        lastUpdate = hostNow()
        lock.unlock()
        if let repairedFrom { onDeviceNote("asr_final_repaired", ["final": repairedFrom, "kept": seg.text]) }
        DispatchQueue.main.async { [weak self] in self?.onUpdate?() }
    }

    /// Words heard since audio-timeline second `since`.
    func text(since: Double) -> String {
        lock.lock(); defer { lock.unlock() }
        if sfReq != nil { return HeardText.tidy(sfText) }
        var parts = finals.filter { $0.end > since + 0.05 }.map(\.text)
        if let v = volatileSeg, v.end > since + 0.05 { parts.append(v.text) }
        return HeardText.tidy(parts.joined(separator: " "))
    }

    /// The audio second the words reach (SpeechAnalyzer only; infinity for the older recognizer).
    var coveredUntil: Double {
        lock.lock(); defer { lock.unlock() }
        if sfReq != nil || analyzerBox == nil { return .infinity }
        return max(finals.last?.end ?? 0, volatileSeg?.end ?? 0)
    }

    /// Asks the recognizer to finish everything up to this audio second now (once per turn, just before it ends).
    func finalize(through audioSec: Double) async {
        guard #available(iOS 26.0, macCatalyst 26.0, *), let box = lock.withLock({ analyzerBox }) as? AnalyzerBox else { return }
        let t = hostNow()
        await box.finalize(through: audioSec)
        lock.withLock { lastFinalizeMs = (hostNow() - t) * 1000 }
    }

    /// A new turn starts. SpeechAnalyzer uses time ranges, so only the older recognizer restarts.
    func newTurn() {
        guard sfRec != nil else { return }
        restartSF()
    }

    /// 16 kHz audio from the mic queue.
    func feed(_ buf: AVAudioPCMBuffer, audioSec: Double) {
        lock.lock()
        let sf = sfReq
        let box = analyzerBox
        lock.unlock()
        if let sf { sf.append(buf); return }
        if #available(iOS 26.0, macCatalyst 26.0, *), let box = box as? AnalyzerBox { box.feed(buf, audioSec: audioSec) }
    }

    private func startSF() {
        guard let rec = SFSpeechRecognizer(locale: Locale(identifier: "en-US")) else {
            kind = "none"
            onDeviceNote("speech_start_failed", ["kind": "SFSpeechRecognizer"])
            return
        }
        sfRec = rec
        kind = "SFSpeechRecognizer (on device: \(rec.supportsOnDeviceRecognition))"
        onDeviceNote("speech_started", ["kind": kind])
        restartSF()
    }

    private func restartSF() {
        guard let rec = sfRec else { return }
        sfTask?.cancel()
        let req = SFSpeechAudioBufferRecognitionRequest()
        req.shouldReportPartialResults = true
        req.requiresOnDeviceRecognition = rec.supportsOnDeviceRecognition
        req.addsPunctuation = true
        lock.lock(); sfReq?.endAudio(); sfReq = req; sfText = ""; lock.unlock()
        sfTask = rec.recognitionTask(with: req) { [weak self] res, err in
            guard let self else { return }
            if let res {
                self.lock.lock()
                if self.sfReq === req { self.sfText = res.bestTranscription.formattedString }
                self.lastUpdate = hostNow()
                self.lock.unlock()
                DispatchQueue.main.async { self.onUpdate?() }
            }
            if err != nil || (res?.isFinal ?? false) {
                DispatchQueue.main.asyncAfter(deadline: .now() + 0.2) {
                    let same = self.lock.withLock { self.sfReq === req }
                    if same { self.restartSF() }
                }
            }
        }
    }

    func stop() async {
        let box = lock.withLock { () -> AnyObject? in
            let b = analyzerBox
            analyzerBox = nil
            return b
        }
        if #available(iOS 26.0, macCatalyst 26.0, *), let box = box as? AnalyzerBox { await box.stop() }
        sfTask?.cancel()
        lock.withLock { sfReq?.endAudio(); sfReq = nil }
        sfRec = nil
    }
}

@available(iOS 26.0, macCatalyst 26.0, *)
private final class AnalyzerBox: @unchecked Sendable {
    let analyzer: SpeechAnalyzer
    let input: AsyncStream<AnalyzerInput>.Continuation
    let format: AVAudioFormat
    var converter: AVAudioConverter?
    var results: Task<Void, Never>?

    init(analyzer: SpeechAnalyzer, input: AsyncStream<AnalyzerInput>.Continuation, format: AVAudioFormat) {
        self.analyzer = analyzer
        self.input = input
        self.format = format
    }

    static func start(locale: Locale, got: @escaping (String, Double, Double, Bool) -> Void) async throws -> AnalyzerBox {
        let resolved = await SpeechTranscriber.supportedLocale(equivalentTo: locale) ?? locale
        let t = SpeechTranscriber(locale: resolved, transcriptionOptions: [], reportingOptions: [.volatileResults, .fastResults],
                                  attributeOptions: [])
        if let req = try await AssetInventory.assetInstallationRequest(supporting: [t]) { try await req.downloadAndInstall() }
        let analyzer = SpeechAnalyzer(modules: [t])
        guard let fmt = await SpeechAnalyzer.bestAvailableAudioFormat(compatibleWith: [t]) else {
            throw NSError(domain: "CallListener", code: 1, userInfo: [NSLocalizedDescriptionKey: "no audio format"])
        }
        let (stream, c) = AsyncStream<AnalyzerInput>.makeStream()
        try await analyzer.prepareToAnalyze(in: fmt)
        try await analyzer.start(inputSequence: stream)
        let box = AnalyzerBox(analyzer: analyzer, input: c, format: fmt)
        box.results = Task.detached {
            do {
                for try await res in t.results {
                    got(String(res.text.characters), res.range.start.seconds, CMTimeRangeGetEnd(res.range).seconds, res.isFinal)
                }
            } catch {
                onDeviceNote("speech_error", ["error": "\(error)"])
            }
        }
        return box
    }

    func feed(_ buf: AVAudioPCMBuffer, audioSec: Double) {
        var out = buf
        if format != buf.format {
            if converter == nil || converter?.inputFormat != buf.format { converter = AVAudioConverter(from: buf.format, to: format) }
            guard let converter, let o = AVAudioPCMBuffer(pcmFormat: format,
                frameCapacity: AVAudioFrameCount(Double(buf.frameLength) * format.sampleRate / buf.format.sampleRate + 32))
            else { return }
            var fed = false
            var err: NSError?
            converter.convert(to: o, error: &err) { _, status in
                if fed { status.pointee = .noDataNow; return nil }
                fed = true
                status.pointee = .haveData
                return buf
            }
            out = o
        }
        // An exact sample index: CMTime(seconds:) can round down by one sample, which the analyzer rejects.
        input.yield(AnalyzerInput(buffer: out, bufferStartTime: CMTime(value: CMTimeValue((audioSec * 16000).rounded()), timescale: 16000)))
    }

    func finalize(through audioSec: Double) async {
        do {
            try await analyzer.finalize(through: CMTime(value: CMTimeValue((audioSec * 16000).rounded()), timescale: 16000))
        } catch {
            onDeviceNote("finalize_failed", ["error": "\(error)"])
        }
    }

    func stop() async {
        input.finish()
        await analyzer.cancelAndFinishNow()
        results?.cancel()
    }
}

/// Pipecat's Smart Turn v3.2: the probability that the person finished, from the last 8 s of their audio.
final class SmartTurn: @unchecked Sendable {
    private var model: MLModel?
    private let queue = DispatchQueue(label: "ondevice.smartturn", qos: .userInitiated)
    var available: Bool { queue.sync { model != nil } }

    /// `url`: the compiled model, downloaded with the voice (SmartTurnFiles). Without it, turns end on the words
    /// and the silence alone.
    init(url: URL?) {
        queue.async { [weak self] in
            guard let self, let url else { return }
            let cfg = MLModelConfiguration()
            cfg.computeUnits = .cpuAndNeuralEngine
            let t = hostNow()
            do {
                self.model = try MLModel(contentsOf: url, configuration: cfg)
                _ = self.run([Float](repeating: 0, count: 16000))
                onDeviceNote("smart_turn_loaded", ["load_ms": Int((hostNow() - t) * 1000)])
            } catch {
                onDeviceNote("smart_turn_failed", ["error": "\(error)"])
            }
        }
    }

    private func run(_ samples: [Float]) -> Float? {
        guard let model, let w = try? MLMultiArray(shape: [1, 128000], dataType: .float32) else { return nil }
        let p = w.dataPointer.bindMemory(to: Float.self, capacity: 128000)
        p.initialize(repeating: 0, count: 128000)
        let tail = samples.suffix(128000)
        let off = 128000 - tail.count
        for (i, x) in tail.enumerated() { p[off + i] = x }
        guard let out = try? model.prediction(from: MLDictionaryFeatureProvider(dictionary: ["audio": w])),
            let arr = out.featureValue(for: "probability")?.multiArrayValue
        else { return nil }
        return arr[0].floatValue
    }

    /// Completion on the main queue with the probability and the milliseconds it took.
    func predict(_ samples: [Float], completion: @escaping (Float?, Double) -> Void) {
        queue.async { [weak self] in
            let t = hostNow()
            let p = self?.run(samples)
            let elapsed = (hostNow() - t) * 1000
            DispatchQueue.main.async { completion(p, elapsed) }
        }
    }
}
