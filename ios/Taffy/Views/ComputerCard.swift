import SwiftUI

/// What a Zip's computer is doing, as the Computer card says it.
enum ComputerMode {
    case working   // the Zip is using it
    case control   // Colin holds it
    case waiting   // the Zip needs Colin on it
    case earlier   // a sign-in the Zip asked for before the person's latest message: still there to use, not waited on

    var pill: String {
        switch self {
        case .working: "Working"
        case .control: "You're in control"
        case .waiting: "Waiting for You"
        case .earlier: "Asked Earlier"
        }
    }
}

/// A sign-in the Zip asked for, which the Computer card answers.
struct SignInPrompt {
    let site: URL
    let requestID: Int
    let why: String
    /// The Zip is waiting on this sign-in now. An older card stays to use but says "Asked Earlier" (Oct 9).
    var waiting: Bool = true

    /// "salesforce.com", the way a person says the site.
    var siteName: String { (site.host() ?? "the site").replacingOccurrences(of: "www.", with: "") }
}

private struct ScreenOpen: Identifiable {
    let takeOver: Bool
    var id: Bool { takeOver }
}

/// Grok Bot's inline "Computer" card: what the Zip is doing on its own computer in one line, a small live picture of
/// the screen, and Take Over or I'm Done. It shows when a Zip asks Colin to sign in (with Save Login still there)
/// and while a Zip is using its computer in the chat, or Colin holds it. The picture is a still that refreshes every
/// couple of seconds while the card is on screen.
struct ComputerCard: View {
    @Environment(AppState.self) private var state
    @Environment(\.scenePhase) private var scenePhase
    let botID: String
    /// What the feed says when this card is not answering a sign-in.
    var live: ComputerMode = .working
    var signIn: SignInPrompt? = nil
    var gap: CGFloat = 48

    @State private var held: Bool?          // nil until the first look at who holds the computer
    @State private var picture: UIImage?
    @State private var asleep = false
    @State private var opened: ScreenOpen?
    @State private var savingLogin = false
    @State private var busy = false
    @State private var error: String?

    private var bot: Bot? { state.bot(botID) }
    private var name: String { bot?.name ?? "The Zip" }

    private var mode: ComputerMode {
        if held == true { return .control }
        if held == nil && live == .control && signIn == nil { return .control }
        if let signIn { return signIn.waiting ? .waiting : .earlier }
        return .working
    }

    /// One plain line on why the Zip is on its computer.
    private var reason: String {
        if let signIn {
            var why = signIn.why.trimmingCharacters(in: .whitespacesAndNewlines)
            if why.lowercased().hasPrefix("to ") { why.removeFirst(3) }
            while why.hasSuffix(".") { why.removeLast() }
            return "Sign in to \(signIn.siteName) so \(name) can \(why.isEmpty ? "carry on" : why)."
        }
        switch mode {
        case .control: return "\(name) is paused while you use its computer."
        default: return "\(name) is working on its computer."
        }
    }

    var body: some View {
        HStack {
            VStack(alignment: .leading, spacing: 10) {
                HStack(spacing: 8) {
                    Text("Computer").font(Theme.text(Theme.onMac ? 15 : 17, .semibold)).foregroundStyle(Theme.ink)
                    Spacer(minLength: 8)
                    StatusPill(mode: mode)
                    if signIn != nil { closeButton }
                }
                Text(reason).font(Theme.text(Theme.onMac ? 15 : 17)).foregroundStyle(Theme.ink)
                    .fixedSize(horizontal: false, vertical: true)
                ScreenPicture(picture: picture, asleep: asleep, bot: bot)
                    .onTapGesture { opened = ScreenOpen(takeOver: false) }
                    .accessibilityElement(children: .ignore)
                    .accessibilityLabel("Show \(name.possessive) screen")
                    .accessibilityAddTraits(.isButton)
                if let error {
                    Text(error).font(Theme.caption).foregroundStyle(Theme.danger)
                }
                buttons
            }
            .padding(Theme.onMac ? 12 : 14)
            .background(Theme.bubble, in: RoundedRectangle(cornerRadius: Theme.onMac ? 18 : 24, style: .continuous))
            .frame(maxWidth: Theme.onMac ? Theme.bubbleMax : .infinity)
            Spacer(minLength: gap)
        }
        .task(id: scenePhase == .active) {
            guard scenePhase == .active else { return }
            while !Task.isCancelled {
                await look()
                try? await Task.sleep(for: .seconds(2.5))
            }
        }
        .sheet(isPresented: $savingLogin, onDismiss: { Task { await state.loadChat(botID) } }) {
            SaveLoginSheet(siteURL: signIn?.site.absoluteString ?? "", botID: botID, requestID: signIn?.requestID)
                .environment(state)
        }
        .fullScreenCover(item: $opened, onDismiss: { Task { await state.loadChat(botID); await look() } }) { open in
            ScreenView(botID: botID, takeOver: open.takeOver, site: open.takeOver ? signIn?.site : nil,
                       reason: signIn.map { "Sign in to \($0.siteName)" }, requestID: open.takeOver ? signIn?.requestID : nil)
                .environment(state)
        }
    }

    /// On the iPhone a sign-in leads with Save Login: the phone's saved passwords fill the Zip's vault, which is far
    /// easier than driving its screen with a finger (Colin, Oct 9). The Mac, with a mouse and keyboard, leads with
    /// Take Over. The other way stays available as a quieter text button on each.
    private var phoneSignIn: Bool { signIn != nil && !Theme.onMac }

    private var buttons: some View {
        HStack(spacing: 8) {
            if phoneSignIn {
                pill("Save Login", dark: true) { savingLogin = true }
            } else {
                pill("Take Over", dark: true) { opened = ScreenOpen(takeOver: true) }
            }
            // On the phone, I'm Done only means something after Colin took the screen himself.
            if mode == .control || (mode != .working && !phoneSignIn) {
                pill("I'm Done", dark: false) { Task { await done() } }
                    .disabled(busy)
            }
            Spacer(minLength: 0)
            if signIn != nil {
                Button {
                    if phoneSignIn { opened = ScreenOpen(takeOver: true) } else { savingLogin = true }
                } label: {
                    Text(phoneSignIn ? "Use Its Screen" : "Save Login").font(Theme.text(14, .medium))
                        .foregroundStyle(Theme.inkSoft)
                        .lineLimit(1).fixedSize()
                        .padding(.horizontal, 6).frame(minHeight: 44)
                        .contentShape(Rectangle())
                }
                .buttonStyle(PressStyle())
            }
        }
    }

    private func pill(_ title: String, dark: Bool, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Text(title).font(Theme.text(Theme.onMac ? 15 : 17, .medium))
                .foregroundStyle(dark ? Theme.mineText : Theme.ink)
                .lineLimit(1).fixedSize()
                .padding(.horizontal, 16).frame(minHeight: Theme.onMac ? 38 : 44)
                .background(dark ? Theme.mine : Theme.canvas, in: Capsule())
                .overlay(Capsule().stroke(dark ? Color.clear : Theme.rule, lineWidth: 1))
        }
        .buttonStyle(PressStyle())
    }

    private var closeButton: some View {
        Button {
            guard let id = signIn?.requestID else { return }
            Task {
                do {
                    try await state.client.closeSignInCard(bot: botID, feedID: id)
                    error = nil
                } catch {
                    self.error = ScreenTrouble.report(error, screen: "computer_card_close", bot: botID, state: state)
                }
                await state.loadChat(botID)
            }
        } label: {
            Image(systemName: "xmark").font(.system(size: 13, weight: .semibold)).foregroundStyle(Theme.inkSoft)
                .frame(width: 30, height: 30).contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .accessibilityLabel("Close")
    }

    /// The Zip's screen and who holds it, every couple of seconds while the card is on screen.
    private func look() async {
        #if DEBUG
        if state.fixtureMode {
            held = signIn == nil && live == .control || CommandLine.arguments.contains("-computerHeld")
            return
        }
        #endif
        async let status = try? state.client.screenStatus(bot: botID)
        async let still = try? state.client.screenSnapshot(bot: botID)
        let (s, data) = await (status, still)
        if let s { held = s.you_have_control }
        if let data, let image = UIImage(data: data) {
            picture = image
            asleep = false
        } else if let s, !s.running {
            asleep = true
        }
    }

    /// I'm Done: a sign-in tells the Zip he finished; otherwise the computer just goes back to the Zip.
    private func done() async {
        busy = true
        error = nil
        defer { busy = false }
        do {
            if let signIn {
                try await state.client.finishSignIn(bot: botID, taskID: nil, site: signIn.site.host(), declined: false,
                                                    requestID: signIn.requestID)
            } else {
                try await state.client.releaseScreen(bot: botID)
                state.computers[botID] = nil
            }
            held = false
            await state.loadChat(botID)
            await state.refreshBoard()
        } catch {
            self.error = ScreenTrouble.report(error, screen: "computer_card_done", bot: botID, state: state)
        }
    }
}

/// "Working" with a small turning spinner, "You're in control" or "Waiting for You", in amber; "Asked Earlier" in gray.
struct StatusPill: View {
    let mode: ComputerMode
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        HStack(spacing: 5) {
            if mode == .working {
                TimelineView(.animation(minimumInterval: 1 / 30, paused: reduceMotion)) { ctx in
                    Image(systemName: "sun.min").font(.system(size: 12, weight: .semibold))
                        .rotationEffect(.degrees(reduceMotion ? 0 : ctx.date.timeIntervalSinceReferenceDate * 120))
                }
            } else {
                Circle().frame(width: 6, height: 6)
            }
            Text(mode.pill).font(Theme.text(14, .medium)).lineLimit(1)
        }
        // A card asked for earlier is not waited on: quiet gray instead of amber.
        .foregroundStyle(mode == .earlier ? Theme.inkSoft : Theme.amber)
        .padding(.horizontal, 10).padding(.vertical, 5)
        .background(mode == .earlier ? Theme.inkSoft.opacity(0.12) : Theme.amberFill, in: Capsule())
        .accessibilityElement(children: .combine)
    }
}

/// The still of the Zip's screen, or a quiet stand-in while there is none.
private struct ScreenPicture: View {
    @Environment(AppState.self) private var state
    let picture: UIImage?
    let asleep: Bool
    let bot: Bot?

    var body: some View {
        Color.clear
            .aspectRatio(1.6, contentMode: .fit)
            .frame(maxWidth: .infinity)
            .background(Color.black.opacity(0.9))
            .overlay {
                if let picture {
                    Image(uiImage: picture).resizable().scaledToFill()
                } else {
                    #if DEBUG
                    if state.fixtureMode { DemoScreenArt() } else { standIn }
                    #else
                    standIn
                    #endif
                }
            }
            .clipShape(RoundedRectangle(cornerRadius: 14, style: .continuous))
            .overlay(RoundedRectangle(cornerRadius: 14, style: .continuous).stroke(Theme.rule, lineWidth: 0.5))
            .contentShape(RoundedRectangle(cornerRadius: 14, style: .continuous))
    }

    private var standIn: some View {
        VStack(spacing: 8) {
            BotAvatar(bot: bot, size: 36, working: !asleep)
            Text(asleep ? "\((bot?.name ?? "The Zip").possessive) computer is asleep." : "Getting the picture")
                .font(Theme.caption).foregroundStyle(.white.opacity(0.7))
        }
    }
}

#if DEBUG
/// A made-up screen (wallpaper and a sign-in window) for screenshots in the simulator. Nothing here is real.
struct DemoScreenArt: View {
    var body: some View {
        Canvas { ctx, size in
            ctx.fill(Path(CGRect(origin: .zero, size: size)), with: .linearGradient(
                Gradient(colors: [Color(hex: "#4A7FB0"), Color(hex: "#E3A08F"), Color(hex: "#D9822B"), Color(hex: "#3C5A33")]),
                startPoint: .zero, endPoint: CGPoint(x: 0, y: size.height)))
            let win = CGRect(x: size.width * 0.25, y: size.height * 0.14, width: size.width * 0.5, height: size.height * 0.72)
            ctx.fill(Path(roundedRect: win, cornerRadius: size.height * 0.03), with: .color(.white))
            let pad = size.width * 0.05
            let field = size.height * 0.07
            ctx.fill(Path(ellipseIn: CGRect(x: size.width / 2 - size.height * 0.05, y: win.minY + size.height * 0.08,
                                            width: size.height * 0.1, height: size.height * 0.1)), with: .color(.gray.opacity(0.25)))
            for i in 0..<2 {
                let r = CGRect(x: win.minX + pad, y: win.minY + size.height * (0.26 + 0.11 * Double(i)), width: win.width - pad * 2, height: field)
                ctx.stroke(Path(roundedRect: r, cornerRadius: 3), with: .color(.gray.opacity(0.35)), lineWidth: 1)
            }
            let button = CGRect(x: win.minX + pad, y: win.minY + size.height * 0.5, width: win.width - pad * 2, height: field)
            ctx.fill(Path(roundedRect: button, cornerRadius: 3), with: .color(.black.opacity(0.75)))
        }
    }
}
#endif
