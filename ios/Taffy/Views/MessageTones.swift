import AVFoundation
import AudioToolbox
import SwiftUI
import UniformTypeIdentifiers

/// Each Zip's message tone (Colin, Oct 11: "can we make personalized message tones for each of the agents?" and "can
/// we give the user the ability to change the sounds if they'd prefer a different one?"). The hub keeps the choice so
/// the iPhone and the Mac agree, and sends it with every notification (hub tones.py). Zero's tones are bundled
/// (ios/scripts/make-tones.py); a sound of the person's own is saved on this device in Library/Sounds, where iOS
/// looks for notification sounds.
enum MessageTones {
    static let bundled: [(id: String, title: String)] = [
        ("chime", "Chime"), ("marimba", "Marimba"), ("pluck", "Pluck"), ("bell", "Bell"), ("glass", "Glass"),
        ("harp", "Harp"), ("ding", "Ding"), ("knock", "Knock"), ("bubble", "Bubble"), ("pop", "Pop"),
    ]
    /// Longest own sound kept; iOS plays the phone's own sound instead of one over 30 seconds.
    static let maxSeconds = 25.0

    static func title(_ tone: String?) -> String {
        switch tone {
        case "default": return "iPhone Default"
        case "none": return "None"
        case "custom": return "Your Own Sound"
        case let t?: return bundled.first { $0.id == t }?.title ?? "Chime"
        case nil: return "Chime"
        }
    }

    static func customURL(_ bot: String) -> URL? {
        guard let lib = FileManager.default.urls(for: .libraryDirectory, in: .userDomainMask).first else { return nil }
        return lib.appendingPathComponent("Sounds/zero-custom-\(bot).caf")
    }

    static func hasCustom(_ bot: String) -> Bool {
        customURL(bot).map { FileManager.default.fileExists(atPath: $0.path) } ?? false
    }

    /// Copies the person's sound into Library/Sounds as 16-bit PCM, cut to `maxSeconds` with a short fade so it never
    /// stops on a click.
    static func saveCustom(from source: URL, for bot: String) throws {
        let reading = source.startAccessingSecurityScopedResource()
        defer { if reading { source.stopAccessingSecurityScopedResource() } }
        guard let dest = customURL(bot) else { throw CocoaError(.fileWriteUnknown) }
        try FileManager.default.createDirectory(at: dest.deletingLastPathComponent(), withIntermediateDirectories: true)
        let input = try AVAudioFile(forReading: source)
        let format = input.processingFormat
        let frames = AVAudioFrameCount(min(Double(input.length), maxSeconds * format.sampleRate))
        guard frames > 0, let buffer = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: frames) else {
            throw CocoaError(.fileReadCorruptFile)
        }
        try input.read(into: buffer, frameCount: frames)
        if let channels = buffer.floatChannelData {
            let n = Int(buffer.frameLength)
            let fade = min(n, Int(0.3 * format.sampleRate))
            for c in 0..<Int(format.channelCount) {
                for i in 0..<fade { channels[c][n - fade + i] *= Float(fade - i) / Float(fade) }
            }
        }
        try? FileManager.default.removeItem(at: dest)
        let settings: [String: Any] = [AVFormatIDKey: kAudioFormatLinearPCM, AVSampleRateKey: format.sampleRate,
                                       AVNumberOfChannelsKey: format.channelCount, AVLinearPCMBitDepthKey: 16,
                                       AVLinearPCMIsFloatKey: false, AVLinearPCMIsBigEndianKey: false]
        let output = try AVAudioFile(forWriting: dest, settings: settings, commonFormat: format.commonFormat,
                                     interleaved: format.isInterleaved)
        try output.write(from: buffer)
    }
}

/// Plays a tone once, for choosing.
@MainActor
final class TonePreview {
    static let shared = TonePreview()
    private var player: AVAudioPlayer?

    func play(_ tone: String, bot: String) {
        player?.stop()
        switch tone {
        case "none":
            return
        case "default":
            AudioServicesPlaySystemSound(1007)   // the phone's own notification sound
            return
        default:
            let url = tone == "custom" ? MessageTones.customURL(bot)
                : Bundle.main.url(forResource: "zero-\(tone)", withExtension: "caf")
            guard let url else { return }
            try? AVAudioSession.sharedInstance().setCategory(.ambient)
            player = try? AVAudioPlayer(contentsOf: url)
            player?.play()
        }
    }

    func stop() { player?.stop() }
}

/// A Zip's Message Tone row, under its notification settings.
struct MessageToneRow: View {
    @Environment(AppState.self) private var state
    let botID: String
    @State private var choosing = false

    var body: some View {
        Button { choosing = true } label: {
            HStack {
                Text("Message Tone").font(Theme.body).foregroundStyle(Theme.ink)
                Spacer()
                Text(MessageTones.title(state.bot(botID)?.tone)).font(Theme.secondary).foregroundStyle(Theme.inkSoft)
                Image(systemName: "chevron.right").font(.footnote.weight(.semibold)).foregroundStyle(Theme.inkSoft)
            }
            .frame(minHeight: 44)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .sheet(isPresented: $choosing) {
            MessageTonePicker(botID: botID).environment(state)
        }
    }
}

struct MessageTonePicker: View {
    @Environment(AppState.self) private var state
    @Environment(\.dismiss) private var dismiss
    let botID: String
    @State private var importing = false
    @State private var hasCustom = false
    @State private var importError: String?

    var body: some View {
        let current = state.bot(botID)?.tone ?? "chime"
        NavigationStack {
            List {
                Section {
                    ForEach(MessageTones.bundled, id: \.id) { tone in
                        row(tone.title, id: tone.id, current: current)
                    }
                } header: {
                    Text("Zero's Tones")
                }
                Section {
                    if hasCustom { row("Your Own Sound", id: "custom", current: current) }
                    Button(hasCustom ? "Choose a Different Sound File" : "Choose a Sound File") { importing = true }
                        .foregroundStyle(Theme.taffyDeep)
                } header: {
                    Text("Your Own")
                } footer: {
                    Text("Pick any audio file, such as a voice memo you saved to Files. It plays on this device, and the first \(Int(MessageTones.maxSeconds)) seconds are kept.")
                }
                Section {
                    row("iPhone Default", id: "default", current: current)
                    row("None", id: "none", current: current)
                } footer: {
                    Text("With None, notifications from \(state.bot(botID)?.name ?? "this Zip") still show, without a sound.")
                }
            }
            .navigationTitle("Message Tone")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .confirmationAction) { Button("Done") { dismiss() } }
            }
            .fileImporter(isPresented: $importing, allowedContentTypes: [.audio]) { result in
                switch result {
                case .success(let url):
                    do {
                        try MessageTones.saveCustom(from: url, for: botID)
                        hasCustom = true
                        state.setTone(botID, "custom")
                        TonePreview.shared.play("custom", bot: botID)
                    } catch {
                        importError = "Zero couldn't use that file. Try a different sound."
                    }
                case .failure:
                    break
                }
            }
            .alert("That Did Not Work", isPresented: Binding(get: { importError != nil },
                                                             set: { if !$0 { importError = nil } })) {
                Button("OK", role: .cancel) {}
            } message: {
                Text(importError ?? "")
            }
        }
        .onAppear { hasCustom = MessageTones.hasCustom(botID) }
        .onDisappear { TonePreview.shared.stop() }
    }

    private func row(_ title: String, id: String, current: String) -> some View {
        Button {
            state.setTone(botID, id)
            TonePreview.shared.play(id, bot: botID)
        } label: {
            HStack {
                Text(title).foregroundStyle(Theme.ink)
                Spacer()
                if current == id {
                    Image(systemName: "checkmark").font(.body.weight(.semibold)).foregroundStyle(Theme.taffyDeep)
                }
            }
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
    }
}
