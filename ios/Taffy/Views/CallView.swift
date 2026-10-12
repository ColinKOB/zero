import AVKit
import SwiftUI

/// A voice chat with one bot, styled like an iPhone call to a contact: the bot's picture and name at
/// the top with the call timer, round controls at the bottom, and the red End button. The words of
/// the call stay behind the Transcript button.
struct CallView: View {
    @Environment(AppState.self) private var state
    @Environment(\.dismiss) private var dismiss
    let botID: String

    @State private var engine: CallEngine?
    @State private var connectedAt: Date?
    @State private var showTranscript = false

    private var bot: Bot? { state.bot(botID) }
    private static let onMac = ProcessInfo.processInfo.isMacCatalystApp

    var body: some View {
        VStack(spacing: 0) {
            VStack(spacing: 10) {
                BotAvatar(bot: bot, size: 76)
                    .frame(width: 104, height: 104)
                    .background(Color.white.opacity(0.12), in: Circle())
                Text(bot?.name ?? "Zero")
                    .font(Theme.text(34))
                    .foregroundStyle(.white)
                    .lineLimit(1)
                subtitle
                if state.demoMode {
                    Text("Sample Home. This call is pretend.")
                        .font(Theme.text(13, .semibold)).foregroundStyle(.white.opacity(0.7))
                        .padding(.horizontal, 12).frame(height: 28)
                        .background(Color.white.opacity(0.12), in: Capsule())
                }
            }
            .padding(.top, 56)

            Spacer()

            // A Zip on this call asking to use the Mac: Allow (Face ID) and Not Now right here (MacAskViews.swift).
            MacCallPrompt(botID: botID)
                .padding(.bottom, 16)

            if case .failed(let msg) = engine?.phase {
                Text(msg)
                    .font(Theme.text(16)).foregroundStyle(.white.opacity(0.8))
                    .multilineTextAlignment(.center)
                    .padding(.horizontal, 40)
                Spacer()
            }

            HStack(alignment: .top, spacing: 0) {
                if !Self.onMac {
                    if let device = engine?.externalRoute {
                        // AirPods, the car or another device: the system's audio menu, named for where it plays.
                        VStack(spacing: 8) {
                            RoutePicker()
                                .frame(width: 78, height: 78)
                                .background(Color.white, in: Circle())
                            Text(device).font(Theme.text(13, .medium)).foregroundStyle(.white).lineLimit(1)
                        }
                        .frame(maxWidth: .infinity)
                        .accessibilityElement(children: .combine)
                        .accessibilityLabel("Audio, playing on \(device)")
                    } else {
                        control(engine?.speaker == true ? "speaker.wave.3.fill" : "speaker.wave.2.fill", "Speaker",
                                on: engine?.speaker == true) { engine?.speaker.toggle() }
                    }
                }
                control(engine?.muted == true ? "mic.slash.fill" : "mic.slash", "Mute",
                        on: engine?.muted == true) { engine?.muted.toggle() }
                control("text.bubble.fill", "Transcript", on: showTranscript) { showTranscript = true }
            }
            .padding(.horizontal, 36)
            .opacity(callFailed ? 0.35 : 1)       // after a failure only End matters
            .disabled(callFailed)

            Button(action: hangUp) {
                Image(systemName: "phone.down.fill")
                    .font(.system(size: 30, weight: .medium)).foregroundStyle(.white)
                    .frame(width: 78, height: 78)
                    .background(Color(hex: "#EB4E3D"), in: Circle())
            }
            .buttonStyle(PressStyle(scale: 0.92))
            .accessibilityLabel("End Call")
            .padding(.top, 34)
            .padding(.bottom, 44)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(Color(hex: "#1C1C1E").ignoresSafeArea())
        .preferredColorScheme(.dark)
        .sheet(isPresented: $showTranscript) {
            if let engine { CallTranscript(engine: engine).presentationDetents([.medium, .large]) }
        }
        .task {
            // A call sends the person's voice to the voice company. It starts only after they agreed (the sample home
            // never starts one).
            guard state.demoMode || state.aiConsent == .agreed || state.fixtureMode else { dismiss(); return }
            if let running = CallEngine.current, running.botID == botID, !running.isOver {
                // This screen was built again for the same tap: it shows the call already starting.
                running.screens += 1
                running.note("screen", ["joined": running.screens])
                engine = running
                connectedAt = running.connectedAt
                return
            }
            if let other = CallEngine.current, !other.isOver { other.end() }   // a new call replaces the old one
            let e = CallEngine(state: state, botID: botID)
            e.screens = 1
            engine = e
            if state.demoMode {   // the sample home: a pretend call that connects to nothing
                e.transcript = [(who: "You", text: "Did my flight get booked?"),
                                (who: bot?.name ?? "Zero", text: "Yes. You're on the 7:40 a.m. flight on Friday. The confirmation is in your calendar.")]
                e.phase = .listening
                connectedAt = Date().addingTimeInterval(-25)
                return
            }
            #if DEBUG
            if state.fixtureMode {   // simulator QA: show a state without starting a call
                let a = CommandLine.arguments
                let phase = a.firstIndex(of: "-callphase").flatMap { $0 + 1 < a.count ? a[$0 + 1] : nil } ?? "connected"
                e.transcript = [(who: "You", text: "Can you build my Walmart cart?"),
                                (who: "Zero", text: "On it. I'll tell you when it's ready.")]
                switch phase {
                case "calling": e.phase = .calling
                case "failed": e.phase = .failed("The call couldn't connect. Hang up and try again.")
                default: e.phase = .listening; connectedAt = Date().addingTimeInterval(-83)
                }
                return
            }
            #endif
            UIApplication.shared.isIdleTimerDisabled = true
            CallEngine.current = e
            // Not tied to this screen's task, so a screen rebuilt while the call starts does not cancel it.
            Task { await e.start() }
        }
        .onChange(of: engine?.phase) { _, p in
            // The timer starts only when the call really connects.
            if connectedAt == nil, p == .listening { connectedAt = Date(); engine?.connectedAt = connectedAt }
            if p == .listening { UIAccessibility.post(notification: .announcement, argument: "Connected") }
            if case .failed = p { UIAccessibility.post(notification: .announcement, argument: "Call failed") }
            // Ended from the lock screen, CarPlay or AirPods: the screen closes itself, like the Phone app.
            if p == .ended { Task { try? await Task.sleep(for: .seconds(1)); dismiss() } }
        }
        .onDisappear {
            guard let e = engine else { UIApplication.shared.isIdleTimerDisabled = false; return }
            e.screens -= 1
            // A screen built again for the same call joins it a moment later; the call ends only when none does.
            Task { @MainActor in
                if !e.isOver { try? await Task.sleep(for: .milliseconds(700)) }
                guard e.screens <= 0 else { return }
                e.end()
                UIApplication.shared.isIdleTimerDisabled = false
            }
        }
    }

    /// "Connecting..." until the call connects, then the running time, like the Phone app.
    private var callFailed: Bool {
        if case .failed = engine?.phase { return true }
        return false
    }

    @ViewBuilder private var subtitle: some View {
        Group {
            if callFailed {
                Text("Call Failed")
            } else if engine?.held == true {
                Text("On Hold")
            } else if let connectedAt, engine?.phase != .ended {
                TimelineView(.periodic(from: connectedAt, by: 1)) { ctx in
                    Text(elapsed(since: connectedAt, ctx.date)).monospacedDigit()
                        .accessibilityLabel("Connected, \(spoken(since: connectedAt, ctx.date))")
                }
            } else if engine?.phase == .ended {
                Text("Call Ended")
            } else if let p = engine?.preparing {
                // The On-Device Voice is loading for this call (downloading the first time).
                VStack(spacing: 10) {
                    Text(p.detail).multilineTextAlignment(.center)
                    ProgressView(value: p.progress)
                        .tint(.white)
                        .frame(width: 180)
                }
                .padding(.horizontal, 40)
                .accessibilityElement(children: .combine)
                .accessibilityLabel("\(p.detail) \(Int(p.progress * 100)) percent.")
            } else {
                // Not "calling...": nothing rings. The call connects in a moment, or says why it can't (#16 to #18).
                Text("Connecting...")
            }
        }
        .font(Theme.text(17))
        .foregroundStyle(.white.opacity(0.6))
    }

    private func hangUp() {
        engine?.end()
        dismiss()
    }

    private func control(_ symbol: String, _ label: String, on: Bool, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            VStack(spacing: 8) {
                Image(systemName: symbol)
                    .font(.system(size: 26, weight: .regular))
                    .foregroundStyle(on ? .black : .white)
                    .frame(width: 78, height: 78)
                    .background(on ? Color.white : Color.white.opacity(0.16), in: Circle())
                Text(label).font(Theme.text(13, .medium)).foregroundStyle(.white)
            }
            .frame(maxWidth: .infinity)
        }
        .buttonStyle(PressStyle(scale: 0.92))
        .accessibilityLabel(label)
        .accessibilityAddTraits(on ? .isSelected : [])
    }

    private func spoken(since started: Date, _ now: Date) -> String {
        let s = max(0, Int(now.timeIntervalSince(started)))
        let m = s / 60, r = s % 60
        return (m > 0 ? "\(m) minute\(m == 1 ? "" : "s") " : "") + "\(r) second\(r == 1 ? "" : "s")"
    }

    private func elapsed(since started: Date, _ now: Date) -> String {
        let s = max(0, Int(now.timeIntervalSince(started)))
        return String(format: "%d:%02d", s / 60, s % 60)
    }
}


/// The words of the call so far. Its own view, so only new lines redraw it, and it follows the newest
/// line the way Messages does.
private struct CallTranscript: View {
    let engine: CallEngine
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            ScrollViewReader { proxy in
                ScrollView {
                    LazyVStack(alignment: .leading, spacing: 14) {
                        if engine.transcript.isEmpty {
                            Text("Nothing has been said yet.").foregroundStyle(Theme.inkSoft)
                        }
                        ForEach(Array(engine.transcript.enumerated()), id: \.offset) { i, line in
                            VStack(alignment: .leading, spacing: 3) {
                                Text(line.who).font(Theme.text(13, .medium)).foregroundStyle(Theme.inkSoft)
                                Text(line.text).font(Theme.text(17)).foregroundStyle(Theme.ink).textSelection(.enabled)
                            }
                            .id(i)
                        }
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(20)
                }
                .onAppear { proxy.scrollTo(engine.transcript.count - 1, anchor: .bottom) }
                .onChange(of: engine.transcript.count) { _, n in
                    withAnimation { proxy.scrollTo(n - 1, anchor: .bottom) }
                }
            }
            .safeAreaInset(edge: .top, spacing: 0) {
                SheetBar(title: "Transcript", cancel: nil, confirm: "Done", onConfirm: { dismiss() })
            }
            .toolbar(.hidden, for: .navigationBar)
        }
    }
}


/// The system's audio output menu (iPhone, Speaker, AirPods, the car), like the Phone app's Audio button.
private struct RoutePicker: UIViewRepresentable {
    func makeUIView(context: Context) -> AVRoutePickerView {
        let v = AVRoutePickerView()
        v.tintColor = .black
        v.activeTintColor = .black
        v.prioritizesVideoDevices = false
        return v
    }
    func updateUIView(_ uiView: AVRoutePickerView, context: Context) {}
}
