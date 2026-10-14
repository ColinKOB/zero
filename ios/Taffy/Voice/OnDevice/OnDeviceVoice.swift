import Foundation
import Observation

/// The On-Device Voice on this device: whether its files are here, loading them (downloading the first time,
/// with progress for Settings and the call screen), the greeting takes made ahead of time, and removing it all.
/// The voice is loaded ahead of a call, never at the start of one when that can be helped: the first load after
/// an install takes about 50 s while the phone prepares the model.
@MainActor
@Observable
final class OnDeviceVoice {
    static let shared = OnDeviceVoice()

    enum Status: Equatable { case notDownloaded, downloaded, working, ready, failed }

    private(set) var status: Status = .notDownloaded
    private(set) var progress: Double = 0
    private(set) var detail = ""
    private(set) var bytesOnDisk: Double = 0

    @ObservationIgnored let engine = PocketVoiceEngine()
    @ObservationIgnored private var loadTask: Task<Bool, Never>?
    @ObservationIgnored private var greetingTask: Task<Void, Never>?
    @ObservationIgnored private var takes: [String: [Float]] = [:]
    @ObservationIgnored var inCall = false

    /// Whether this device can run it: iPhone, iPad and the Mac app. The simulator runs Core ML on the CPU only,
    /// where the voice is about half as fast as speech, so it is left out there.
    static var supported: Bool {
        #if targetEnvironment(simulator)
        return false
        #else
        return true
        #endif
    }

    static let downloadMB = Int(PocketVoiceEngine.expectedBytes / 1_000_000)

    private init() {
        refreshDisk()
    }

    func refreshDisk() {
        bytesOnDisk = PocketVoiceEngine.bytes(engine.root)
        if status != .working && status != .ready {
            status = engine.isDownloaded ? .downloaded : (status == .failed ? .failed : .notDownloaded)
        }
    }

    @discardableResult
    func prepare() async -> Bool {
        guard Self.supported else { return false }
        if engine.ready {
            status = .ready
            return true
        }
        if let t = loadTask { return await t.value }
        status = .working
        progress = 0
        detail = engine.isDownloaded ? "Loading the voice." : "Starting the download."
        let engine = self.engine
        let t = Task { @MainActor [weak self] () -> Bool in
            do {
                try await engine.load { p, s in
                    Task { @MainActor in
                        guard let self, self.status == .working else { return }
                        self.progress = p
                        self.detail = s
                    }
                }
                return true
            } catch {
                self?.detail = "The voice couldn't be downloaded. Check your connection and try again."
                return false
            }
        }
        loadTask = t
        let ok = await t.value
        loadTask = nil
        status = ok ? .ready : .failed
        refreshDisk()
        if ok { prepareGreeting() }
        return ok
    }

    func unloadIfIdle() {
        guard !inCall, loadTask == nil, engine.ready else { return }
        greetingTask?.cancel()
        Task {
            await engine.unload()
            refreshDisk()
            if status == .ready { status = engine.isDownloaded ? .downloaded : .notDownloaded }
        }
    }

    func remove() async {
        guard !inCall else { return }
        loadTask?.cancel()
        greetingTask?.cancel()
        await engine.unload()
        try? engine.removeFiles()
        takes = [:]
        status = .notDownloaded
        progress = 0
        detail = ""
        refreshDisk()
    }

    // MARK: Greeting takes

    var greetingText: String {
        get { UserDefaults.standard.string(forKey: "onDeviceGreeting") ?? "" }
        set { UserDefaults.standard.set(newValue, forKey: "onDeviceGreeting") }
    }

    var greetingLateText: String {
        get { UserDefaults.standard.string(forKey: "onDeviceGreetingLate") ?? "" }
        set { UserDefaults.standard.set(newValue, forKey: "onDeviceGreetingLate") }
    }

    var voice: OnDeviceVoiceName {
        OnDeviceVoiceName(rawValue: UserDefaults.standard.string(forKey: "onDeviceVoice") ?? "") ?? .george
    }

    private func key(_ voice: OnDeviceVoiceName, _ text: String) -> String { "\(voice.rawValue)|\(text)" }

    private var takesFolder: URL { engine.root.appendingPathComponent("greetings", isDirectory: true) }

    private func file(_ voice: OnDeviceVoiceName, _ text: String) -> URL {
        let safe = String(text.unicodeScalars.map { CharacterSet.alphanumerics.contains($0) ? Character($0) : "-" }).prefix(60)
        return takesFolder.appendingPathComponent("\(voice.rawValue)-\(safe).f32")
    }

    func greetingTake(voice: OnDeviceVoiceName, text: String) -> [Float]? {
        if let t = takes[key(voice, text)] { return t }
        guard let data = try? Data(contentsOf: file(voice, text)), data.count >= 4 else { return nil }
        let samples = data.withUnsafeBytes { Array($0.bindMemory(to: Float.self)) }
        takes[key(voice, text)] = samples
        return samples
    }

    func prepareGreeting() {
        let voice = self.voice
        let texts = [greetingText, greetingLateText].filter { !$0.isEmpty && greetingTake(voice: voice, text: $0) == nil }
        guard engine.ready, !inCall, !texts.isEmpty else { return }
        greetingTask?.cancel()
        let engine = self.engine, folder = takesFolder
        let jobs = texts.map { ($0, file(voice, $0)) }
        greetingTask = Task { [weak self] in
            for (text, url) in jobs {
                let ranked = try? await GreetingTakes.render(engine, text: text, voice: voice) {
                    Task.isCancelled || (self?.inCall ?? true)
                }
                guard let best = ranked?.first, !Task.isCancelled, let self else { return }
                self.takes[self.key(voice, text)] = best.samples
                try? FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
                let data = best.samples.withUnsafeBufferPointer { Data(buffer: $0) }
                try? data.write(to: url, options: .atomic)
            }
        }
    }
}
