import SwiftUI
import UIKit

/// The Mac window, laid out like Grok Bot for Mac: a light gray sidebar with Search and New at the
/// top, one row per bot, and Settings and Work at the bottom; the open chat fills the rest.
struct MacHomeView: View {
    @Environment(AppState.self) private var state
    @Environment(\.scenePhase) private var scenePhase
    @State private var detail: MacDetail = .bot("default")
    @State private var searching = false
    @State private var newZip = false
    @State private var callingBot: String?
    @State private var snoozing: Bot?
    @State private var deleting: Bot?
    @State private var savingLogin = false
    @State private var pairingPhone = false
    @State private var connectingGoogle = false
    /// Grok Bot's sidebar button hides the list so the chat gets the whole window; remembered between launches.
    @AppStorage("macSidebarShown") private var sidebarSaved = true
    /// What the window shows. An @AppStorage write reaches the view without the animation (Oct 10: the sidebar still
    /// jumped), so the slide runs on this and the choice is saved after.
    @State private var sidebarShown = UserDefaults.standard.object(forKey: "macSidebarShown") as? Bool ?? true
    /// In full screen macOS draws the title bar as a solid strip over the top of the window, so content that runs
    /// up under the title bar (as it does in a normal window, Grok Bot style) would be hidden behind it.
    @State private var fullScreen = false
    @State private var styled = false
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    private var topEdge: Edge.Set { fullScreen ? [] : .top }
    private static let sidebarWidth: CGFloat = 279
    /// Where the one sidebar button sits: in the sidebar's header, left of Search and New, or, with the list hidden,
    /// just right of the window's traffic lights. It slides between the two with the sidebar.
    private var toggleLeading: CGFloat {
        sidebarShown ? Self.sidebarWidth - 17 - 36 - 8 - 36 - 8 - 34 : (fullScreen ? 17 : 84)
    }

    var body: some View {
        HStack(spacing: 0) {
            // The sidebar stays in the window and its column narrows to nothing, pinned at its right edge, so it
            // slides out to the left as the chat widens, and back in the same way (Oct 10: it used to jump).
            sidebar
                .frame(width: Self.sidebarWidth)
                .background(Theme.sidebar)
                .overlay(alignment: .trailing) { Rectangle().fill(Theme.rule).frame(width: 1) }
                .frame(width: sidebarShown ? Self.sidebarWidth : 0, alignment: .trailing)
                .clipped()
                .ignoresSafeArea(.container, edges: topEdge)   // runs up under the title bar, clipped and all
                .allowsHitTesting(sidebarShown)
                .accessibilityHidden(!sidebarShown)
            NavigationStack { detailView }
                .id(detail)
                .frame(maxWidth: .infinity)
                .ignoresSafeArea(.container, edges: topEdge)
                .background(Theme.canvas.ignoresSafeArea())
        }
        .overlay(alignment: .topLeading) {
            sidebarToggle
                .padding(.leading, toggleLeading).padding(.top, 12)
                .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
                .ignoresSafeArea(.container, edges: topEdge)   // the same height as the sidebar's header
        }
        .tint(Theme.ink)
        .botActions(snoozing: $snoozing, deleting: $deleting, onDeleted: { detail = .bot("default") })
        .sheet(isPresented: $newZip) {
            NewZipView { message in
                if let message {
                    Task { _ = await state.send(message, to: "default") }
                } else if (state.drafts["default"] ?? "").isEmpty {
                    state.drafts["default"] = "I'd like a new Zip for "
                }
                detail = .bot("default")
            }
            .environment(state)
        }
        .sheet(isPresented: $searching) {
            SearchView(onOpen: { detail = .bot($0) }, onWork: { detail = .work }).environment(state)
        }
        .fullScreenCover(isPresented: Binding(get: { state.showWelcome }, set: { state.showWelcome = $0 })) {
            WelcomeView().environment(state)
        }
        .sheet(isPresented: $savingLogin) { SaveLoginSheet().environment(state) }
        // Once, after the first run on a Mac home: Pair Your Phone (Views/MacSetupView.swift).
        .sheet(isPresented: $pairingPhone) { PairPhoneView() }
        .task {
            try? await Task.sleep(for: .seconds(1.5))
            if MacEngine.shared.isLocalHome && MacEngine.shared.phonePending && !state.onboarding { pairingPhone = true }
        }
        .sheet(isPresented: $connectingGoogle) {
            NavigationStack { ConnectAccountsView(group: .google, modal: true) }
                .frame(minWidth: 560, minHeight: 640)
                .environment(state)
        }
        .fullScreenCover(item: Binding(get: { callingBot.map(MacCallTarget.init) }, set: { callingBot = $0?.id })) {
            CallView(botID: $0.id).environment(state)
        }
        .onChange(of: state.showWelcome) { _, showing in
            guard !showing else { return }
            switch state.welcomeNext {
            case .newBot:
                if (state.drafts["default"] ?? "").isEmpty { state.drafts["default"] = "I'd like a new Zip for " }
                detail = .bot("default")
            case .correctNotes:
                state.drafts["default"] = "Please correct your notes about me: "
                detail = .bot("default")
            case .saveLogin:
                savingLogin = true
            case .none:
                break
            }
            state.welcomeNext = .none
        }
        .onAppear {
            checkFullScreen()
            if let bot = state.takeOpenBotRequest() { detail = .bot(bot) }   // a notification clicked at launch
        }
        // A clicked notification opens its Zip's chat (Push.swift).
        .onChange(of: state.openBotRequest) { _, _ in if let bot = state.takeOpenBotRequest() { detail = .bot(bot) } }
        .onChange(of: state.bots.count) { _, _ in if let bot = state.takeOpenBotRequest() { detail = .bot(bot) } }
        // A window restored straight into full screen never sends the notification below, so the window's size is
        // checked too, at launch and whenever it changes.
        .onGeometryChange(for: CGSize.self, of: { $0.size }) { _ in checkFullScreen() }
        .onReceive(NotificationCenter.default.publisher(for: Self.enteredFullScreen)) { _ in
            fullScreen = true
            Self.styleWindow(fullScreen: true)
        }
        .onReceive(NotificationCenter.default.publisher(for: Self.exitedFullScreen)) { _ in
            fullScreen = false
            Self.styleWindow(fullScreen: false)
        }
        .task {
            MacLink.shared.onRefused = { [state] in
                state.macLinkNote = "Zips can't use this Mac yet: another paired device is set as the Mac they use. "
                    + "Remove it in Settings, Paired Devices, then reopen Zero."
            }
            if !state.demoMode {
                MacLink.shared.start(client: state.client)   // lets bots use this Mac when Colin allows it (never in the sample home)
                // After macOS's "Quit & Reopen" for Screen Recording, or Restart Zero: the permission guide picks up where it was.
                MacLink.shared.permissionGuide(["op": "resume"])
            }
            #if DEBUG
            if let i = CommandLine.arguments.firstIndex(of: "-open"), i + 1 < CommandLine.arguments.count {
                switch CommandLine.arguments[i + 1] {   // for screenshots
                case "savelogin": savingLogin = true
                case "search": searching = true
                case "newzip": newZip = true
                case "connect": connectingGoogle = true
                case "settings": detail = .settings
                case "work": detail = .work
                default: break
                }
            }
            #endif
            await state.refreshAll()
            if let bot = state.takeOpenBotRequest(refreshed: true) { detail = .bot(bot) }
            let first = HomeModel.shared.settings?.person?.first_name
                ?? UserDefaults.standard.string(forKey: "personName")?.split(separator: " ").first.map(String.init)
            if let first, !state.demoMode { MacLink.shared.setPerson(first) }
            state.startPolling()
            await state.checkAway()
        }
        .onChange(of: scenePhase) { _, phase in
            if phase == .active {
                state.startPolling()
                Task { await state.refreshAll(); await state.checkAway() }
            } else if phase == .background {
                state.stopPolling()
                state.noteLeaving()   // for While You Were Away
            }
        }
    }

    // MARK: Sidebar

    /// A plain icon, not a round button, like Grok Bot's.
    private var sidebarToggle: some View {
        Button {
            // A smooth slide; with Reduce Motion the sidebar simply appears or goes.
            withAnimation(reduceMotion ? nil : .smooth(duration: 0.32)) { sidebarShown.toggle() }
            sidebarSaved = sidebarShown
        } label: {
            Image(systemName: "sidebar.left")
                .font(.system(size: 17, weight: .regular))
                .foregroundStyle(Theme.inkSoft)
                .frame(width: 34, height: 40)
                .contentShape(Rectangle())
        }
        .buttonStyle(PressStyle(scale: 0.94))
        .accessibilityLabel(sidebarShown ? "Hide Sidebar" : "Show Sidebar")
        .keyboardShortcut("s", modifiers: [.command, .control])
    }

    private var sidebar: some View {
        VStack(spacing: 0) {
            HStack(spacing: 8) {
                Spacer()
                Color.clear.frame(width: 34, height: 40)   // the sidebar button's place; it is drawn over the window
                Button { searching = true } label: { roundIcon("magnifyingglass") }
                    .buttonStyle(PressStyle(scale: 0.94))
                    .accessibilityLabel("Search")
                    .keyboardShortcut("f", modifiers: .command)
                newMenu
            }
            .padding(.horizontal, 17)
            .padding(.top, 12)
            .padding(.bottom, 16)

            ScrollView {
                VStack(spacing: 4) {
                    if let note = state.macLinkNote {
                        Label(note, systemImage: "laptopcomputer.trianglebadge.exclamationmark")
                            .font(Theme.caption).foregroundStyle(Theme.inkSoft)
                            .padding(.vertical, 8)
                    }
                    if state.offline {
                        Label("Can't reach Zero right now. Trying again.", systemImage: "wifi.slash")
                            .font(Theme.caption).foregroundStyle(Theme.inkSoft)
                            .padding(.vertical, 8)
                    }
                    if !state.awayShown.isEmpty {
                        AwayCard { id in detail = .bot(id); state.away = [] }
                            .padding(.bottom, 8)
                    }
                    ForEach(sidebarBots) { bot in
                        row(bot)
                    }
                }
                .padding(.horizontal, 12)
            }
            .scrollIndicators(.hidden)

            HStack(spacing: 8) {
                Button { detail = .settings } label: {
                    Text(state.initials)
                        .font(Theme.text(15))
                        .foregroundStyle(Theme.profileText)
                        .frame(width: 36, height: 36)
                        .background(detail == .settings ? Theme.sidebarSelected : Theme.profileCircle, in: Circle())
                }
                .buttonStyle(PressStyle())
                .accessibilityLabel("Settings")
                .keyboardShortcut(",", modifiers: .command)
                Button { detail = .work } label: {
                    HStack(spacing: 8) {
                        Text("Work")
                        if state.needsYouCount > 0 {
                            Text("\(state.needsYouCount)")
                                .font(Theme.text(12, .semibold)).foregroundStyle(.white)
                                .padding(.horizontal, 7).padding(.vertical, 2)
                                .background(Theme.taffyDeep, in: Capsule())
                        }
                    }
                    .font(Theme.text(15))
                    .foregroundStyle(Theme.ink)
                    .frame(maxWidth: .infinity, minHeight: 36)
                    .background(detail == .work ? Theme.sidebarSelected : Theme.canvas, in: Capsule())
                    .overlay(Capsule().stroke(Theme.rule))
                }
                .buttonStyle(PressStyle(scale: 0.98))
                .accessibilityLabel(state.needsYouCount > 0 ? "Work, \(state.needsYouCount) waiting on you" : "Work")
            }
            .padding(.horizontal, 16)
            .padding(.top, 10)
            .padding(.bottom, 16)
        }
    }

    /// Grok Bot's round toolbar buttons: white, a hairline edge and a faint shadow.
    /// A call sends the person's voice to the voice company, so it starts only after they have agreed.
    private func beginCall(_ id: String) {
        state.startCallIfAllowed { callingBot = id }
    }

    private func roundIcon(_ symbol: String) -> some View {
        Image(systemName: symbol)
            .font(.system(size: 15, weight: .regular))
            .foregroundStyle(Theme.ink)
            .frame(width: 36, height: 36)
            .background(Theme.canvas, in: Circle())
            .overlay(Circle().stroke(Theme.rule))
            .shadow(color: .black.opacity(0.05), radius: 2, y: 1)
    }

    /// Only a new Zip: messaging, calling Zero and Work each have their own place already (Colin, Oct 10).
    private var newMenu: some View {
        Button { newZip = true } label: { roundIcon("plus") }
            .buttonStyle(.plain)
            .accessibilityLabel("New Zip")
    }

    private func row(_ bot: Bot) -> some View {
        let working = bot.working > 0 || bot.busy || state.working[bot.id] != nil
        let selected = detail == .bot(bot.id)
        return Button { detail = .bot(bot.id) } label: {
            HStack(spacing: 10) {
                BotAvatar(bot: bot, size: 34, working: working)
                    .overlay(alignment: .topTrailing) { Badge(count: bot.needs_you, size: 34) }
                VStack(alignment: .leading, spacing: 2) {
                    HStack(spacing: 5) {
                        Text(bot.name).font(Theme.text(15)).foregroundStyle(Theme.ink).lineLimit(1)
                        if bot.snoozedUntil != nil {
                            Image(systemName: "moon.zzz.fill").font(.system(size: 11, weight: .semibold))
                                .foregroundStyle(Theme.inkSoft)
                                .accessibilityLabel("Snoozed")
                        }
                    }
                    // Paused, stuck, working, blocked or a card that closed, else the latest line (FluidViews.swift).
                    RowStatusLine(bot: bot, font: Theme.text(14), dots: 5)
                }
                Spacer(minLength: 0)
            }
            .padding(.horizontal, 9)
            .frame(height: 54)
            .background(selected ? Theme.sidebarSelected : .clear, in: RoundedRectangle(cornerRadius: 10, style: .continuous))
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .contextMenu {
            Button { beginCall(bot.id) } label: { Label("Call", systemImage: "phone") }
            Button { snoozing = bot } label: { Label("Snooze", systemImage: "moon.zzz") }
            if !bot.isLead {
                Button(role: .destructive) { deleting = bot } label: { Label("Delete", systemImage: "trash") }
            }
        }
    }

    /// Taffy first, then everyone else by latest activity.
    private var sidebarBots: [Bot] {
        let all = state.botsByActivity
        return all.filter(\.isLead) + all.filter { !$0.isLead }
    }

    // MARK: Detail

    @ViewBuilder private var detailView: some View {
        switch detail {
        case .bot(let id):
            if state.bot(id) != nil {
                ChatScreen(botID: id, onCall: { beginCall(id) }, embedded: true,
                           onRemoved: { detail = .bot("default") })
            } else {
                Color.clear.onAppear { detail = .bot("default") }
            }
        case .work: WorkView()
        case .settings: SettingsView()
        }
    }

    // MARK: Window


    /// Content runs under the title bar like Grok Bot's window, and the window can't shrink so far
    /// that the sidebar and chat collide.
    /// AppKit's window notifications reach a Catalyst app's own notification center by name.
    private static let enteredFullScreen = Notification.Name("NSWindowDidEnterFullScreenNotification")
    private static let exitedFullScreen = Notification.Name("NSWindowDidExitFullScreenNotification")

    /// Full screen, or a window covering the whole screen (the menu bar may take a strip at the top): both show the
    /// title bar as a solid strip, so content starts below it.
    private func checkFullScreen() {
        guard ProcessInfo.processInfo.isMacCatalystApp else { return }
        let scene = UIApplication.shared.connectedScenes.compactMap { $0 as? UIWindowScene }.first
        guard let scene else { return }
        let window = scene.coordinateSpace.bounds.size, screen = scene.screen.bounds.size
        var now = abs(window.width - screen.width) < 2 && window.height > screen.height - 60   // also a window filling the screen
        #if targetEnvironment(macCatalyst)
        now = now || scene.isFullScreen
        #endif
        if now != fullScreen || !styled {
            fullScreen = now
            styled = true
            Self.styleWindow(fullScreen: now)
        }
    }

    private static func styleWindow(fullScreen: Bool) {
        for case let scene as UIWindowScene in UIApplication.shared.connectedScenes {
            #if targetEnvironment(macCatalyst)
            scene.titlebar?.titleVisibility = .hidden
            // In a window, an empty unified toolbar gives the taller title bar, so the window buttons sit where
            // Grok Bot's do. In full screen it would only be an empty strip, so it goes.
            scene.titlebar?.toolbar = fullScreen ? nil : NSToolbar(identifier: "taffy.main")
            scene.titlebar?.toolbarStyle = .unified
            #endif
            scene.sizeRestrictions?.minimumSize = CGSize(width: 900, height: 600)
        }
    }
}

enum MacDetail: Hashable {
    case bot(String), work, settings
}

private struct MacCallTarget: Identifiable { let id: String }
