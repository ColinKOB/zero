import Intents
import SwiftUI
import UserNotifications

struct RootView: View {
    @Environment(AppState.self) private var state

    var body: some View {
        VStack(spacing: 0) {
            // A sample home says so on every screen.
            if state.demoMode { DemoBanner() }
            if state.paired && !state.onboarding && !state.demoMode { NeedsPlanBanner() }
            content
                // Text grows with the reader's setting, up to a size every card and bubble still lays out at.
                .dynamicTypeSize(...DynamicTypeSize.accessibility2)
        }
        // "Allowed from now on: ..." after Always Allow on a card, wherever the card was.
        .confirmationBanner(Binding(get: { state.confirmation }, set: { state.confirmation = $0 }))
        // Asked when something needs to be sent and the person has not agreed (Model/AIConsent.swift).
        .sheet(isPresented: Binding(get: { state.aiConsentSheet }, set: { state.aiConsentSheet = $0 })) {
            AIConsentSheet().environment(state)
        }
        #if DEBUG
        .task { await Push.shared.runLaunchHooks() }   // -askNotifications, -testNotification (Push.swift)
        .task {   // -openURL zero://pair?...: the same path a tapped link takes, for the simulator
            let args = CommandLine.arguments
            if let i = args.firstIndex(of: "-openURL"), i + 1 < args.count, let url = URL(string: args[i + 1]) {
                try? await Task.sleep(for: .seconds(1.5))
                state.open(url)
            }
        }
        #endif
    }

    @ViewBuilder private var content: some View {
        #if DEBUG
        let _ = VoiceSetupView.keychainSelfTest
        if let screen = voiceSetupScreen {
            screen
        } else {
            main
        }
        #else
        main
        #endif
    }

    @ViewBuilder private var main: some View {
        if state.rehearsingOnboarding {
            OnboardingView(rehearsal: true)
        } else if Theme.onMac && !state.paired && !state.demoMode && MacEngine.shared.setupShowing
                    && !fixtureRequested {
            // A Mac with no home yet: set Zero up on this Mac first (Views/MacSetupView.swift).
            MacSetupView()
        } else if state.onboarding {
            OnboardingView()
                #if DEBUG
                .task { if fixtureRequested { state.fixtureMode = true } }
                #endif
        } else if state.paired {
            if ProcessInfo.processInfo.isMacCatalystApp { MacHomeView() } else { HomeView() }
        } else if fixtureRequested {
            Color.clear.task {
                #if DEBUG
                state.loadFixture()
                #endif
            }
        } else {
            SignInView()
                #if DEBUG
                .task {
                    // -autopair <code> -pairServer <address>, for the simulator; the address falls back to the saved one.
                    let args = CommandLine.arguments
                    if let i = args.firstIndex(of: "-autopair"), i + 1 < args.count {
                        let host = args.firstIndex(of: "-pairServer").flatMap { $0 + 1 < args.count ? args[$0 + 1] : nil }
                        await state.pair(code: args[i + 1], server: host ?? HubClient.savedServer)
                        if state.paired { state.markSetUp() }
                    }
                }
                #endif
        }
    }
}

#if DEBUG
/// `-voiceSetup [intro|lost|guiding|card|paste|checking|done|saved|notSaved|rejected|limited|offline|refused|location|onMac]` opens the guided
/// voice setup on its own, for screenshots. `-voiceSetupPractice 127.0.0.1:8790` points the guide at the local
/// practice page (tools/voice-setup-probe/practice) instead of Google, `-voiceSetupStart <url>` picks the page it
/// opens, and `-voiceSetupBrowser <path to .app>` opens it in a test browser instead of the default one.
private var voiceSetupScreen: VoiceSetupView? {
    let args = CommandLine.arguments
    guard let i = args.firstIndex(of: "-voiceSetup") else { return nil }
    let name = i + 1 < args.count ? args[i + 1] : ""
    var practice: (studio: String, start: String)?
    if let p = args.firstIndex(of: "-voiceSetupPractice"), p + 1 < args.count {
        let start = args.firstIndex(of: "-voiceSetupStart").flatMap { $0 + 1 < args.count ? args[$0 + 1] : nil }
        practice = (args[p + 1], start ?? "http://\(args[p + 1])/?scenario=new")
    }
    let phase: VoiceSetupView.Phase? = switch name {
    case "guiding", "card": .guiding
    case "paste": .paste
    case "checking": .checking
    case "done": .done(GeminiKeyPattern.ending("AQ.Zero-Practice-Key-Not-Real-00000000000000000000000"))
    case "rejected": .failed(.rejected)
    case "limited": .failed(.limited)
    case "offline": .failed(.offline)
    case "refused": .failed(.refused("Permission denied."))
    case "location": .failed(.refused("User location is not supported for the API use."))
    case "notSaved": .notSaved
    case "saved": .saved(GeminiKeyPattern.ending("AQ.Zero-Practice-Key-Not-Real-00000000000000000000000"))
    case "onMac": .onMac
    case "intro", "lost": .intro
    default: nil
    }
    let lost = name == "lost" ? "Your browser closed, so Zero stopped the guide. Choose Get My Free Key when you're ready to try again." : nil
    var view = VoiceSetupView(phase: phase, stepTitle: name == "guiding" ? "Accept Google's Terms" : nil,
                              cardMode: name == "card", notice: lost, practice: practice)
    if let b = args.firstIndex(of: "-voiceSetupBrowser"), b + 1 < args.count { view.testBrowser = args[b + 1] }
    return view
}
#endif

private var fixtureRequested: Bool {
    #if DEBUG && (targetEnvironment(simulator) || targetEnvironment(macCatalyst))
    return CommandLine.arguments.contains("-fixture") || Push.coldFixture
    #else
    return false
    #endif
}

enum Route: Hashable { case bot(String), work, settings, proactivity, notices, connect(String), mail, ask, apps }

/// The home screen, laid out like Grok Bot's: the bots Colin pinned up top, then every conversation
/// as a row with its latest line and time.
struct HomeView: View {
    @Environment(AppState.self) private var state
    @Environment(\.scenePhase) private var scenePhase
    @State private var order: [String] = []
    @State private var pendingCall: String?
    @State private var path: [Route] = []
    @State private var searching = false
    @State private var newZip = false
    @State private var lookingAt: Bot?
    @State private var callingBot: String?
    @State private var snoozing: Bot?
    @State private var deleting: Bot?
    @State private var pinsFull = false
    @State private var savingLogin = false

    var body: some View {
        NavigationStack(path: $path) {
            List {
                Group {
                    if state.offline {
                        Label(state.updatedAt.map { "Can't reach Zero right now. Showing what you had at \($0.formatted(date: .omitted, time: .shortened))." }
                              ?? "Can't reach Zero right now. Trying again.", systemImage: "wifi.slash")
                            .font(Theme.caption).foregroundStyle(Theme.inkSoft)
                            .frame(maxWidth: .infinity)
                            .padding(.bottom, 10)
                    } else if state.bots.isEmpty, let err = state.lastError {
                        VStack(spacing: 10) {
                            Text(err).font(Theme.secondary).foregroundStyle(Theme.inkSoft).multilineTextAlignment(.center)
                            Button("Try Again") { Task { await state.refreshAll() } }.font(Theme.secondary.weight(.medium))
                        }
                        .frame(maxWidth: .infinity).padding(.vertical, 40).padding(.horizontal, 30)
                    } else if state.bots.isEmpty {
                        HStack(spacing: 10) { ProgressView(); Text("Loading your team").font(Theme.secondary) }
                            .foregroundStyle(Theme.inkSoft)
                            .frame(maxWidth: .infinity).padding(.vertical, 40)
                    }
                    if !featuredBots.isEmpty {
                        featured
                            .padding(.top, 8)
                            .padding(.bottom, 18)
                    }
                    if state.needsYouCount > 0 && !state.bots.isEmpty {
                        NeedsYouRow(count: state.needsYouCount) { path.append(.work) }
                            .padding(.horizontal, 20)
                            .padding(.bottom, 6)
                    }
                    if !state.awayShown.isEmpty {
                        AwayCard { id in path.append(.bot(id)); state.away = [] }
                            .padding(.horizontal, 20)
                            .padding(.bottom, 10)
                    }
                    ForEach(listBots) { bot in
                        Button { path.append(.bot(bot.id)) } label: { BotRow(bot: bot) }
                            .buttonStyle(PressStyle(scale: 0.98))
                            .overlay(alignment: .bottomTrailing) {
                                // Ask Again or Retry, right from the list (FluidViews.swift).
                                if let action = RowAction.for(bot, in: state) {
                                    RowActionButton(action: action).padding(.trailing, 20).padding(.bottom, 2)
                                }
                            }
                            // Swipe from right to left: Snooze, and Delete for every bot but Taffy.
                            .swipeActions(edge: .trailing, allowsFullSwipe: false) {
                                if !bot.isLead {
                                    Button { deleting = bot } label: { Label("Delete", systemImage: "trash") }
                                        .tint(Theme.danger)
                                }
                                Button { snoozing = bot } label: { Label("Snooze", systemImage: "moon.zzz") }
                                    .tint(Color(hex: "#5856D6"))
                            }
                            // Swipe from left to right: pin to the top row.
                            .swipeActions(edge: .leading, allowsFullSwipe: false) {
                                Button { pin(bot) } label: { Label("Pin", systemImage: "pin.fill") }
                                    .tint(Color(hex: "#F2A100"))
                            }
                    }
                }
                .listRowInsets(EdgeInsets())
                .listRowSeparator(.hidden)
                .listRowBackground(Theme.canvas)
            }
            .listStyle(.plain)
            .scrollContentBackground(.hidden)
            .background(Theme.canvas)
            .scrollIndicators(.hidden)
            .botActions(snoozing: $snoozing, deleting: $deleting)
            .alert("Top Row Is Full", isPresented: $pinsFull) {
                Button("OK", role: .cancel) {}
            } message: {
                Text("Three Zips are already pinned. Long-press one at the top and choose Unpin first.")
            }
            .refreshable { await state.refreshAll(); order = state.botsByActivity.map(\.id) }
            .sensoryFeedback(.success, trigger: state.pinned)
            .sensoryFeedback(.impact(weight: .light), trigger: snoozing?.id)
            .onAppear { order = state.botsByActivity.map(\.id) }
            .onChange(of: state.bots.count) { _, _ in if order.isEmpty { order = state.botsByActivity.map(\.id) } }
            .safeAreaInset(edge: .top) { topBar }
            .toolbar(.hidden, for: .navigationBar)
            .navigationDestination(for: Route.self) { route in
                switch route {
                case .bot(let id): ChatScreen(botID: id, onCall: { beginCall(id) })
                case .work: WorkView()
                case .settings: SettingsView()
                case .proactivity: ProactivityView()
                case .notices: NoticesView()
                case .connect(let group): ConnectAccountsView(group: AccountGroup.of(service: group == "google" ? "gmail" : group,
                                                                                    name: AccountsModel.shared.status?.more.first { $0.id == group }?.name))
                case .mail: MailAndCalendarView()
                case .ask: AskSettingsView()
                case .apps: ConnectAppsView()
                }
            }
        }
        .tint(Theme.ink)
        #if DEBUG
        .sheet(item: $lookingAt) { LookEditor(bot: $0).environment(state) }
        #endif
        .sheet(isPresented: $newZip) {
            NewZipView { message in
                if let message {
                    Task { _ = await state.send(message, to: "default") }
                } else if (state.drafts["default"] ?? "").isEmpty {
                    state.drafts["default"] = "I'd like a new Zip for "
                }
                path = [.bot("default")]
            }
            .environment(state)
        }
        .sheet(isPresented: $searching) {
            SearchView(onOpen: { path.append(.bot($0)) }, onWork: { path.append(.work) }).environment(state)
        }
        .fullScreenCover(isPresented: Binding(get: { state.showWelcome }, set: { state.showWelcome = $0 })) {
            WelcomeView().environment(state)
        }
        .sheet(isPresented: $savingLogin) { SaveLoginSheet().environment(state) }
        .onChange(of: state.showWelcome) { _, showing in
            guard !showing else { return }
            switch state.welcomeNext {
            case .newBot:
                if (state.drafts["default"] ?? "").isEmpty { state.drafts["default"] = "I'd like a new Zip for " }
                path = [.bot("default")]
            case .correctNotes:
                state.drafts["default"] = "Please correct your notes about me: "
                path = [.bot("default")]
            case .saveLogin:
                savingLogin = true
            case .none:
                break
            }
            state.welcomeNext = .none
        }
        .fullScreenCover(item: Binding(get: { callingBot.map(CallTarget.init) }, set: { callingBot = $0?.id })) {
            CallView(botID: $0.id).environment(state)
        }
        // Tapping a Taffy call in the Phone app's Recents (or "Call Taffy" with Siri) calls that bot again.
        .onContinueUserActivity("INStartCallIntent") { activity in
            let intent = activity.interaction?.intent as? INStartCallIntent
            pendingCall = intent?.contacts?.first?.personHandle?.value ?? intent?.contacts?.first?.displayName
            callPending()
        }
        // On a cold launch the bots aren't loaded yet when the request arrives; it waits for them.
        .onChange(of: state.bots.count) { _, _ in callPending(); openRequestedChat() }
        // A notification tapped before this screen was up (a cold launch) set the request before .onChange could see it.
        .onAppear { openRequestedChat() }
        .task {
            #if DEBUG
            let args = CommandLine.arguments
            if args.contains("-open") { try? await Task.sleep(for: .milliseconds(800)) }
            if let i = args.firstIndex(of: "-open"), i + 1 < args.count {
                switch args[i + 1] {
                case "work": path.append(.work)
                case "settings": path.append(.settings)
                case "proactivity": path.append(.settings); path.append(.proactivity)
                case "notices": path.append(.settings); path.append(.proactivity); path.append(.notices)
                case "call": callingBot = "default"
                case "search": searching = true
                case "look": lookingAt = state.bot(args.count > i + 2 ? args[i + 2] : "default")
                case "newzip": newZip = true
                case "savelogin": savingLogin = true
                case "connect": path.append(.connect(args.count > i + 2 && !args[i + 2].hasPrefix("-") ? args[i + 2] : "google"))
                case "mail": path.append(.settings); path.append(.mail)
                case "ask": path.append(.settings); path.append(.ask)
                case "apps": path.append(.settings); path.append(.apps)
                default: path.append(.bot(args[i + 1]))
                }
            }
            #endif
            if FirstTasks.openChatOnce {   // just finished the onboarding: Zero introduces itself in its chat
                FirstTasks.openChatOnce = false
                path = [.bot("default")]
            }
            await state.refreshAll()
            openRequestedChat(refreshed: true)
            state.startPolling()
            await state.checkAway()
            await Push.shared.register()
            #if DEBUG
            if let i = args.firstIndex(of: "-say"), i + 1 < args.count {
                await state.send(args[i + 1], to: "default")
            }
            #endif
        }
        .onChange(of: state.paired) { _, paired in
            if paired { Task { await Push.shared.register() } }
        }
        .onChange(of: state.openBotRequest) { _, _ in openRequestedChat() }
        .onChange(of: scenePhase) { _, phase in
            if phase == .active {
                state.startPolling()
                Task { await state.refreshAll(); await state.checkAway() }
            } else if phase == .background {
                state.stopPolling()
                state.noteLeaving()   // for While You Were Away
                OnDeviceVoice.shared.unloadIfIdle()   // about 780 MB of memory; it loads again in a few seconds
            }
            if phase == .active, state.callEngine == AppState.onDeviceEngine, OnDeviceVoice.supported,
               OnDeviceVoice.shared.engine.isDownloaded {
                Task { await OnDeviceVoice.shared.prepare() }   // ready before the next call, not at its start
            }
        }
    }

    /// The pinned bots, up top.
    private var featuredBots: [Bot] { state.topRow }

    /// A tapped notification's chat (Push.swift), once the bots are loaded.
    private func openRequestedChat(refreshed: Bool = false) {
        guard let bot = state.takeOpenBotRequest(refreshed: refreshed) else { return }
        path = [.bot(bot)]
    }

    /// A call asked for from Recents or Siri: the handle is the bot's id (or, for older entries, its name).
    private func callPending() {
        guard let want = pendingCall, !state.bots.isEmpty else { return }
        if let bot = state.bots.first(where: { $0.id == want || $0.name.caseInsensitiveCompare(want) == .orderedSame }) {
            beginCall(bot.id)
        }
        pendingCall = nil
    }

    /// A call sends the person's voice to the voice company, so it starts only after they have agreed.
    private func beginCall(_ id: String) {
        state.startCallIfAllowed { callingBot = id }
    }

    private func pin(_ bot: Bot) {
        if !state.togglePin(bot.id) { pinsFull = true }
    }

    /// The list keeps the order it had when Colin opened Home (or pulled to refresh), so rows never move
    /// under his finger; bots that are new since then go at the top.
    private var listBots: [Bot] {
        let featuredIDs = Set(featuredBots.map(\.id))
        let live = state.botsByActivity.filter { !featuredIDs.contains($0.id) }
        guard !order.isEmpty else { return live }
        let rank = Dictionary(uniqueKeysWithValues: order.enumerated().map { ($1, $0) })
        return live.sorted { (rank[$0.id] ?? -1) < (rank[$1.id] ?? -1) }
    }

    private var topBar: some View {
        HStack(spacing: 10) {
            CircleButton(action: { path.append(.settings) }) {
                Text(state.initials).font(.system(size: 15, weight: .semibold, design: .rounded)).tracking(0.3)
            }
            .accessibilityLabel("Settings")
            Spacer()
            CircleButton(action: { searching = true }) { Image(systemName: "magnifyingglass") }
                .accessibilityLabel("Search")
            Button { newZip = true } label: {
                Image(systemName: "plus")
                    .font(.system(size: 18, weight: .medium))
                    .foregroundStyle(Theme.ink)
                    .frame(width: 44, height: 44)
                    .background(Theme.bubble, in: Circle())   // flat, like the other top bar circles
            }
            .accessibilityLabel("New Zip")
        }
        .padding(.horizontal, 20)
        .padding(.top, 4)
        .padding(.bottom, 8)
        .barFade(.top)
    }

    private var featured: some View {
        HStack(alignment: .top, spacing: 0) {
            ForEach(featuredBots) { bot in
                Button { path.append(.bot(bot.id)) } label: {
                    VStack(spacing: 10) {
                        BotAvatar(bot: bot, size: 96, working: isWorking(bot))
                            .overlay(alignment: .topTrailing) { Badge(count: bot.needs_you, size: 96) }
                        Text(bot.name).font(Theme.secondary.weight(.medium)).foregroundStyle(Theme.inkSoft).lineLimit(1)
                    }
                    .frame(maxWidth: .infinity)
                    .contentShape(Rectangle())
                }
                .buttonStyle(PressStyle())
                .accessibilityLabel(bot.needs_you > 0 ? "\(bot.name), \(bot.needs_you) waiting on you" : bot.name)
                .contextMenu {
                    Button { pin(bot) } label: {
                        state.isPinned(bot.id) ? Label("Unpin", systemImage: "pin.slash") : Label("Pin", systemImage: "pin")
                    }
                    Button { snoozing = bot } label: { Label("Snooze", systemImage: "moon.zzz") }
                    if !bot.isLead {
                        Button(role: .destructive) { deleting = bot } label: { Label("Delete", systemImage: "trash") }
                    }
                }
            }
        }
        .padding(.horizontal, 12)
    }

    private func isWorking(_ bot: Bot) -> Bool { bot.working > 0 || bot.busy || state.working[bot.id] != nil }
}

private struct CallTarget: Identifiable { let id: String }

struct BotRow: View {
    @Environment(AppState.self) private var state
    let bot: Bot

    var body: some View {
        let working = bot.working > 0 || bot.busy || state.working[bot.id] != nil
        HStack(spacing: 16) {
            BotAvatar(bot: bot, size: 56, working: working)
                .overlay(alignment: .topTrailing) { Badge(count: bot.needs_you, size: 56) }
            VStack(alignment: .leading, spacing: 4) {
                HStack(alignment: .firstTextBaseline) {
                    Text(bot.name).font(Theme.headline).foregroundStyle(Theme.ink)
                    if bot.snoozedUntil != nil {
                        Image(systemName: "moon.zzz.fill").font(.system(size: 12, weight: .semibold))
                            .foregroundStyle(Theme.inkSoft)
                            .accessibilityLabel("Snoozed")
                    }
                    Spacer()
                    if let d = bot.lastDate {
                        Text(d.listStamp).font(Theme.caption).foregroundStyle(Theme.inkSoft)
                    }
                }
                // Paused, stuck, working, blocked or a card that closed, else the latest line (FluidViews.swift).
                RowStatusLine(bot: bot)
                    .padding(.trailing, RowAction.for(bot, in: state) == nil ? 0 : 96)
            }
        }
        .padding(.horizontal, 20)
        .padding(.vertical, 12)
        .contentShape(Rectangle())
    }
}

/// The red count on an avatar. A capsule so two digits fit, pinned to the candy's shoulder
/// rather than the corner of its square frame.
struct Badge: View {
    let count: Int
    var size: CGFloat = 56

    var body: some View {
        if count > 0 {
            Text(count > 9 ? "9+" : "\(count)")
                .font(Theme.text(12, .bold)).monospacedDigit()
                .foregroundStyle(.white)
                .padding(.horizontal, 6)
                .frame(minWidth: 20, minHeight: 20)
                .background(Theme.danger, in: Capsule())
                .overlay(Capsule().stroke(Theme.canvas, lineWidth: 2))
                .offset(x: -size * 0.04, y: size * 0.06)
        }
    }
}

private struct NeedsYouRow: View {
    let count: Int
    let action: () -> Void
    var body: some View {
        Button(action: action) {
            HStack(spacing: 12) {
                Image(systemName: "tray.full.fill")
                    .font(.system(size: 15, weight: .semibold)).foregroundStyle(.white)
                    .frame(width: 32, height: 32)
                    .background(Theme.taffyDeep, in: RoundedRectangle(cornerRadius: 9, style: .continuous))
                Text(count == 1 ? "1 Thing Needs You" : "\(count) Things Need You")
                    .font(Theme.headline).foregroundStyle(Theme.ink)
                Spacer()
                Image(systemName: "chevron.right").font(.system(size: 13, weight: .semibold)).foregroundStyle(Theme.inkSoft)
            }
            .padding(.horizontal, 12).frame(minHeight: 56)
            .background(Theme.bubble, in: RoundedRectangle(cornerRadius: 18, style: .continuous))
        }
        .buttonStyle(PressStyle(scale: 0.98))
    }
}

// MARK: Pairing

struct PairingView: View {
    @Environment(AppState.self) private var state
    /// The sign-in screen already offers the sample home, so the pairing screen it opens leaves it out.
    var showSample = true
    @State private var code = ""
    /// The zero://pair link that filled this screen; its fingerprint goes with the pairing for that address only.
    @State private var link: PairLink?
    @State private var server = HubClient.savedServer
    @State private var busy = false
    @State private var showServer = HubClient.savedServer.isEmpty

    private let scattered: [(String, CGFloat, CGFloat, CGFloat, Double)] = [
        ("career", 0.62, 0.11, 48, 12), ("school", 0.2, 0.19, 60, -10), ("money", 0.86, 0.25, 50, 8),
        ("builder", 0.12, 0.4, 46, -14), ("clients", 0.88, 0.62, 58, 10), ("default", 0.24, 0.78, 64, -8)
    ]

    var body: some View {
        GeometryReader { geo in
            ZStack {
                Theme.canvas.ignoresSafeArea()
                ForEach(scattered, id: \.0) { item in
                    BotAvatar(bot: placeholder(item.0), size: item.3)
                        .rotationEffect(.degrees(item.4))
                        .position(x: geo.size.width * item.1, y: geo.size.height * item.2)
                }
                VStack(spacing: 14) {
                    Text("Zero").font(.system(size: 52, weight: .semibold)).foregroundStyle(Theme.ink)
                    Text("Your team keeps your\nlife and work moving.")
                        .font(Theme.text(18)).foregroundStyle(Theme.ink).multilineTextAlignment(.center)
                    TextField("Pairing Code", text: $code)
                        .font(.system(size: 20, weight: .medium, design: .monospaced))
                        .multilineTextAlignment(.center)
                        .textInputAutocapitalization(.characters)
                        .autocorrectionDisabled()
                        .padding(.vertical, 12)
                        .frame(width: 220)
                        .background(Theme.field, in: Capsule())
                        .padding(.top, 6)
                    Button {
                        Task { busy = true; await state.pair(code: code, server: server, fingerprint: self.link?.server == server ? self.link?.fingerprint : nil); if state.paired { state.markSetUp() }; busy = false }
                    } label: {
                        HStack(spacing: 8) {
                            Text(busy ? "Connecting" : "Connect")
                            Image(systemName: "arrow.right")
                        }
                        .font(Theme.text(18, .medium))
                        .foregroundStyle(Theme.mineText)
                        .padding(.horizontal, 26).frame(minHeight: 50)
                        .background(Theme.mine, in: Capsule())
                    }
                    .buttonStyle(PressStyle())
                    .disabled(code.count < 8 || busy || server.trimmingCharacters(in: .whitespaces).isEmpty)
                    .opacity(code.count < 8 || server.trimmingCharacters(in: .whitespaces).isEmpty ? 0.5 : 1)
                    if let e = state.lastError {
                        Text(e).font(Theme.text(14)).foregroundStyle(Theme.danger).multilineTextAlignment(.center)
                            .padding(.horizontal, 40)
                        if state.pairUnreachable {
                            Text("Check that your phone is on the same Wi-Fi as the computer running Zero, or that your private network app is on, and try again.")
                                .font(Theme.text(14)).foregroundStyle(Theme.inkSoft).multilineTextAlignment(.center)
                                .padding(.horizontal, 40)
                        }
                    }
                    PairLinkButton { link in
                        fill(link)
                        Task { busy = true; await state.pair(code: code, server: server, fingerprint: self.link?.server == server ? self.link?.fingerprint : nil); if state.paired { state.markSetUp() }; busy = false }
                    }
                    .frame(width: 220)
                    Button(showServer ? "Hide Connection Address" : "Connection Address") { showServer.toggle() }
                        .font(Theme.caption).foregroundStyle(Theme.inkSoft)
                        .frame(minHeight: 44)
                    if showServer {
                        TextField("Connection address", text: $server)
                            .font(Theme.text(13)).multilineTextAlignment(.center)
                            .textInputAutocapitalization(.never).autocorrectionDisabled()
                            .keyboardType(.URL)
                            .padding(.horizontal, 40)
                    }
                    if showSample { SampleHomeLink() }
                }
            }
        }
        .onAppear {
            if let link = state.pendingPair { fill(link) }
            #if DEBUG
            let args = CommandLine.arguments
            if let i = args.firstIndex(of: "-server"), i + 1 < args.count { server = args[i + 1]; showServer = true }
            if let i = args.firstIndex(of: "-code"), i + 1 < args.count { code = args[i + 1] }
            #endif
        }
        .onChange(of: state.pendingPair) { _, link in if let link { fill(link) } }
    }

    private func fill(_ link: PairLink) {
        self.link = link
        if !link.server.isEmpty { server = link.server; showServer = true }
        if !link.code.isEmpty { code = link.code }
        state.pendingPair = nil
    }

    private func placeholder(_ id: String) -> Bot {
        let colors = ["default": "#F2711C", "school": "#3B82F6", "builder": "#8B5CF6",
                      "clients": "#10B981", "money": "#EAB308", "career": "#EC4899"]
        return Bot(id: id, name: id, role: "", color: colors[id] ?? "#888888", symbol: "", working: 0,
                   needs_you: 0, busy: false, preview: nil, last_at: nil)
    }
}

// MARK: Account

/// Over every screen while the home's own plan has no sign-in: the Zips cannot answer until it does, so the person
/// learns why before a message goes unanswered, with the way to fix it one tap away.
struct NeedsPlanBanner: View {
    @Environment(AppState.self) private var state
    @State private var signingIn = false

    private var needed: Bool { state.planMissing }

    var body: some View {
        // A zero-height base, so the check below runs even while the banner is hidden.
        VStack(spacing: 0) {
            Color.clear.frame(height: 0)
            if needed {
                Button { signingIn = true } label: {
                    HStack(spacing: 10) {
                        Image(systemName: "sparkles").font(.system(size: 15, weight: .semibold))
                        Text("Your Zips can't answer yet. Sign in to Claude to start.")
                            .font(Theme.text(14, .medium))
                            .multilineTextAlignment(.leading)
                        Spacer(minLength: 4)
                        Text("Sign In").font(Theme.text(14, .semibold))
                    }
                    .foregroundStyle(Theme.ink)
                    .padding(.horizontal, 16).padding(.vertical, 10)
                    .frame(maxWidth: .infinity)
                    .background(Color(hex: "#FFF4D6"))
                }
                .buttonStyle(.plain)
                .accessibilityIdentifier("needsPlanBanner")
            }
        }
        .task { await AccountsModel.shared.refresh(state) }
        .sheet(isPresented: $signingIn) {
            NavigationStack {
                ScrollView {
                    VStack(alignment: .leading, spacing: 16) {
                        Text("Your Zips think with your own Claude plan, such as Pro or Max. Sign in once, and every Zip can use it.")
                            .font(Theme.text(17)).foregroundStyle(Theme.ink)
                            .fixedSize(horizontal: false, vertical: true)
                        ClaudeSignInPanel { signingIn = false }
                    }
                    .padding(24)
                }
                .navigationTitle("Sign In to Claude")
                .navigationBarTitleDisplayMode(.inline)
                .toolbar { ToolbarItem(placement: .cancellationAction) { Button("Cancel") { signingIn = false } } }
            }
            .environment(state)
        }
    }
}

/// Settings: who this device is signed in as, and Sign In to Claude when the home's own plan has no sign-in yet.
struct AccountSection: View {
    @Environment(AppState.self) private var state
    @Binding var signingIn: Bool

    private var needsClaude: Bool { state.planMissing }

    var body: some View {
        Group {
        if !AppState.accountUsername.isEmpty || needsClaude {
            Section {
                if !AppState.accountUsername.isEmpty {
                    LabeledContent("Signed In As", value: AppState.accountUsername)
                }
                if needsClaude {
                    Button { signingIn = true } label: {
                        Label("Sign In to Claude", systemImage: "sparkles").foregroundStyle(Theme.ink)
                    }
                }
            } header: { Text("Account") } footer: {
                if needsClaude { Text("Your Zips can't answer until your own Claude plan is connected.") }
            }
        }
        }
    }
}

// MARK: Search

struct SearchView: View {
    @Environment(AppState.self) private var state
    @Environment(\.dismiss) private var dismiss
    let onOpen: (String) -> Void
    var onWork: () -> Void = {}
    @State private var query = ""

    var body: some View {
        NavigationStack {
            List {
                if !botMatches.isEmpty {
                    Section("Zips") {
                        ForEach(botMatches) { b in
                            Button { open(b.id) } label: {
                                HStack(spacing: 12) {
                                    BotAvatar(bot: b, size: 34)
                                    VStack(alignment: .leading) {
                                        Text(b.name).font(Theme.text(16, .medium)).foregroundStyle(Theme.ink)
                                        Text(b.role).font(Theme.text(14)).foregroundStyle(Theme.inkSoft).lineLimit(1)
                                    }
                                }
                            }
                        }
                    }
                }
                if !messageMatches.isEmpty {
                    Section("Messages") {
                        ForEach(messageMatches, id: \.1.id) { botID, m in
                            Button { open(botID) } label: {
                                VStack(alignment: .leading, spacing: 3) {
                                    Text(state.bot(botID)?.name ?? "").font(Theme.text(13, .medium)).foregroundStyle(Theme.inkSoft)
                                    Text(m.text).font(Theme.text(15)).foregroundStyle(Theme.ink).lineLimit(2)
                                }
                            }
                        }
                    }
                }
                if !taskMatches.isEmpty {
                    Section("Work") {
                        ForEach(taskMatches) { t in
                            Button { dismiss(); onWork() } label: {
                                HStack(spacing: 12) {
                                    BotAvatar(bot: state.bot(t.assignee ?? "default"), size: 28)
                                    Text(t.title).font(Theme.secondary).foregroundStyle(Theme.ink).lineLimit(2)
                                }
                            }
                        }
                    }
                }
            }
            .overlay {
                if !q.isEmpty && botMatches.isEmpty && messageMatches.isEmpty && taskMatches.isEmpty {
                    ContentUnavailableView.search(text: query)
                }
            }
            .listStyle(.insetGrouped)
            .scrollContentBackground(.hidden)
            .background(Theme.canvas)
            .searchable(text: $query, placement: .navigationBarDrawer(displayMode: .always), prompt: "Search Zips, messages and work")
            .navigationTitle("Search")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .topBarTrailing) { Button("Done") { dismiss() } } }
            .task {
                // All chats at once, not one after another (slow on cellular).
                await withTaskGroup(of: Void.self) { group in
                    for b in state.bots where state.chats[b.id] == nil {
                        group.addTask { await state.loadChat(b.id) }
                    }
                }
            }
        }
    }

    private var q: String { query.trimmingCharacters(in: .whitespaces).lowercased() }
    private var botMatches: [Bot] {
        q.isEmpty ? state.bots : state.bots.filter { $0.name.lowercased().contains(q) || $0.role.lowercased().contains(q) }
    }
    private var messageMatches: [(String, ChatMessage)] {
        guard !q.isEmpty else { return [] }
        return Array(state.chats.flatMap { id, msgs in msgs.filter { $0.text.lowercased().contains(q) }.map { (id, $0) } }
            .suffix(30).reversed())
    }
    private var taskMatches: [TaskItem] {
        guard !q.isEmpty else { return [] }
        let all = state.board.needs_you + state.board.working + state.board.done
        return all.filter { $0.title.lowercased().contains(q) || ($0.summary ?? "").lowercased().contains(q) }
    }
    private func open(_ id: String) { dismiss(); onOpen(id) }
}

// MARK: Settings

struct SettingsView: View {
    /// "0.1.0 (412)": which build is on this device.
    static let version: String = {
        let i = Bundle.main.infoDictionary ?? [:]
        return "\(i["CFBundleShortVersionString"] as? String ?? "?") (\(i["CFBundleVersion"] as? String ?? "?"))"
    }()

    @Environment(AppState.self) private var state
    @AppStorage(ThreadPanel.settingKey) private var besideChat = true
    @AppStorage(SimpleLook.key) private var simpleLook = false
    @State private var confirmDisconnect = false
    @State private var signingInToClaude = false
    /// Which of the Mac permissions Zips need are on (PermissionGuide.status), on the Mac only.
    @State private var macPermissions: [String: Bool]?
    /// This iPhone's approval key fingerprint (MacApprover), on the iPhone only.
    @State private var macApprovalCode: String? = MacApprover.shared.fingerprint()
    @State private var settingUpVoice = false
    @State private var confirmDelete = false
    @State private var deleting = false
    @State private var deleteProblem: DeleteEverythingResult?
    /// Show Previews for Zero's notifications, for the one-time Lock Screen Privacy hint (NotificationZips.swift).
    @State private var previews: UNShowPreviewsSetting?
    @Environment(\.scenePhase) private var scenePhase

    var body: some View {
        @Bindable var s = state
        ScrollViewReader { scroller in
        List {
            Section {
                ForEach(state.bots) { b in
                    Toggle(isOn: Binding(get: { !state.mutedBots.contains(b.id) },
                                         set: { state.setMuted(b.id, !$0) })) {
                        HStack(spacing: 12) {
                            BotAvatar(bot: b, size: 28)
                            Text(b.name).font(Theme.body)
                        }
                    }
                    .tint(Theme.taffyDeep)
                }
            } header: { Text("Alerts") } footer: {
                Text("Muted Zips still work and still show up here. They stop sending alerts. Between \(HomeWording.quiet(quietHours)), alerts wait until morning unless something needs you right away.")
            }
            if !state.demoMode {
                LockScreenPrivacySection(previews: previews)
            }
            Section {
                NavigationLink { ProactivityView() } label: {
                    LabeledContent {
                        Text(state.proactivity?.enabled == true ? "On" : "Off").foregroundStyle(Theme.inkSoft)
                    } label: {
                        Label {
                            Text("Questions From Zero").foregroundStyle(Theme.ink)
                        } icon: {
                            TablerIcon(name: "TablerMessageQuestion", size: 20).foregroundStyle(Theme.ink)
                        }
                    }
                }
            } footer: {
                Text("Zero can notice things that need you, like an email waiting on your reply, and ask you a short question about each one.")
            }
            .task { await state.loadProactivity() }
            if !state.demoMode {
                Section {
                    NavigationLink { KnownFactsView() } label: {
                        Label("What I Know About You", systemImage: "brain.head.profile")
                    }
                } footer: {
                    Text("What your Zips remember about you, like the coffee you buy or a plan you made. Change or delete anything.")
                }
            }
            if !state.demoMode {
                ConnectAppsSection()
                    .id("apps")
            }
            if !state.demoMode, let home = HomeModel.shared.settings {
                Section {
                    if home.nativeAccounts {
                        NavigationLink { MailAndCalendarView() } label: {
                            LabeledContent {
                                Text(mailSummary(home)).foregroundStyle(Theme.inkSoft)
                            } label: {
                                Label("Email and Calendars", systemImage: "envelope")
                            }
                        }
                    }
                    NavigationLink { AskSettingsView() } label: {
                        LabeledContent {
                            Text(home.person?.quiet_hours.map(HomeWording.quiet) ?? "").foregroundStyle(Theme.inkSoft)
                        } label: {
                            Label("How Your Zips Ask", systemImage: "moon")
                        }
                    }
                } header: { Text("Your Home") } footer: {
                    Text(home.nativeAccounts
                         ? "Add the email and calendars your Zips read, and choose your quiet hours and units."
                         : "Choose your quiet hours, units and spelling.")
                }
                .id("home")
            }
            // Connectors come from the person's own Claude or ChatGPT plan; a home without them adds email above.
            if !state.demoMode && !HomeModel.shared.nativeAccounts {
                ConnectedAccountsSection()
                    .id("accounts")
            }
            if !state.demoMode {
                Section {
                    NavigationLink { LoginsView() } label: {
                        Label("Saved Logins", systemImage: "key")
                    }
                    NavigationLink { SignInPermissionsView() } label: {
                        Label("Permissions", systemImage: "person.badge.key")
                    }
                } header: { Text("Passwords & Logins") } footer: {
                    Text("Zips sign in with saved logins without ever seeing the password, and only when you allow it.")
                }
                Section {
                    NavigationLink { CustomRulesView() } label: {
                        Label("Custom Rules", systemImage: "checklist")
                    }
                } footer: {
                    Text("See what your Zips may do without asking, and choose what they should ask you about first.")
                }
            }
            Section {
                if !state.demoMode {
                    Button { settingUpVoice = true } label: {
                        LabeledContent {
                            Text(GeminiKeyStore.isSet ? (GeminiKeyStore.callsUseKey ? "On" : "Key Saved") : "")
                                .foregroundStyle(Theme.inkSoft)
                        } label: {
                            Label("Set Up Voice", systemImage: "waveform").foregroundStyle(Theme.ink)
                        }
                    }
                }
                if OnDeviceVoice.supported && !state.demoMode {
                    Picker("Voice", selection: $s.callEngine) {
                        Text("ChatGPT Voice").tag(AppState.chatGPTEngine)
                        Text("On-Device Voice (Free)").tag(AppState.onDeviceEngine)
                    }
                }
                Picker(OnDeviceVoice.supported && !state.demoMode ? "ChatGPT Voice" : "Voice", selection: $s.callVoice) {
                    ForEach(CallEngine.chatGPTVoices, id: \.self) { v in
                        Text(v.capitalized).tag(v)
                    }
                }
                if OnDeviceVoice.supported && !state.demoMode {
                    Picker("On-Device Voice", selection: $s.onDeviceVoice) {
                        ForEach(OnDeviceVoiceName.allCases) { v in
                            Text(v.label).tag(v.rawValue)
                        }
                    }
                    OnDeviceVoiceRow()
                }
            } header: { Text("Voice Chat") } footer: {
                // The same first sentence as Pick My Voice and Set Up Voice (voice setup QA, L2).
                if OnDeviceVoice.supported && !state.demoMode {
                    Text((HomeModel.shared.isShared
                          ? "ChatGPT Voice uses the plan shared with you, and what you say on those calls is sent to ChatGPT."
                          : "ChatGPT Voice needs Codex on your computer, signed in with a ChatGPT plan. What you say on those calls is sent to ChatGPT.")
                         + " On-Device Voice listens and speaks on this \(Theme.device), so its calls cost nothing per minute. Zero switches to it on its own when ChatGPT Voice isn't available.")
                } else {
                    Text(VoiceWording.chatGPTPlan + " What you say on calls is sent to ChatGPT.")
                }
            }
            .id("voice")
            Section {
                Toggle(isOn: $besideChat) {
                    Label {
                        Text("Open Threads Beside the Main Chat")
                    } icon: {
                        TablerIcon(name: "TablerLayoutSidebarRight", size: 20).foregroundStyle(Theme.ink)
                    }
                }
                .tint(Theme.taffyDeep)
            } footer: {
                Text("When the window is wide enough, a task opens next to the list instead of covering it.")
            }
            Section {
                Toggle("Save Call Transcripts", isOn: Binding(get: { state.saveCallTranscripts },
                                                              set: { state.setSaveCallTranscripts($0) }))
                    .tint(Theme.taffyDeep)
            } footer: {
                Text("Transcripts of your calls stay on your own computer for 30 days, and then they are deleted.")
            }
            KeepRepliesShortSection()
            Section {
                NavigationLink { AIDataSharingView() } label: {
                    LabeledContent {
                        Text(state.aiConsent == .agreed ? "On" : "Off").foregroundStyle(Theme.inkSoft)
                    } label: {
                        Label {
                            Text("AI Data Sharing").foregroundStyle(Theme.ink)
                        } icon: {
                            TablerIcon(name: "TablerShieldLock", size: 20).foregroundStyle(Theme.ink)
                        }
                    }
                }
                if !state.demoMode {
                    Button(role: .destructive) { confirmDelete = true } label: {
                        Label {
                            Text(deleting ? "Deleting" : "Delete Everything")
                        } icon: {
                            TablerIcon(name: "TablerTrash", size: 20)
                        }
                    }
                    .disabled(deleting)
                }
            } header: { Text("Privacy") } footer: {
                Text(state.demoMode
                     ? "Review who gets what you say. In a real home, Delete Everything removes your chats, saved calls and setup answers."
                     : "Review who gets what you say, or remove your chats, saved calls, pictures and setup answers from this \(Theme.device) and from your computer.")
            }
            .id("privacy")
            Section {
                Toggle(isOn: $simpleLook) {
                    Label("Simple Look", systemImage: "square.on.circle")
                }
                .tint(Theme.taffyDeep)
            } header: { Text("Appearance") } footer: {
                Text("Shows every Zip as a flat shape instead of a character.")
            }
            Section {
                AppIconPicker()
            } header: { Text("App Icon") }
            if !state.demoMode {
                Section {
                    Button("Replay Welcome") { state.showWelcome = true }
                        .foregroundStyle(Theme.ink)
                    Button("Try Onboarding") { state.rehearsingOnboarding = true }
                        .foregroundStyle(Theme.ink)
                }
                if Theme.onMac && MacEngine.shared.isLocalHome {
                    MacHomeSection()
                }
                Section {
                    AddDeviceRow()
                } header: { Text("Devices") } footer: {
                    Text(AppState.accountUsername.isEmpty
                         ? "Makes a link to sign in on another iPhone or Mac. It works once and expires in 15 minutes."
                         : "On another iPhone or Mac, sign in as \(AppState.accountUsername). Or make a link here that signs it in once and expires in 15 minutes.")
                }
            }
            Section {
                LabeledContent("Version", value: Self.version).font(Theme.secondary)
            }
            if state.demoMode {
                Section {
                    Button("Leave the Sample Home") { state.leaveDemo() }
                        .foregroundStyle(Theme.ink)
                } header: { Text("Sample Home") } footer: {
                    Text("Everything here is made up. Nothing you do in the sample home reaches a real account or computer.")
                }
            } else {
                AccountSection(signingIn: $signingInToClaude)
                Section("Connection") {
                    NavigationLink { DevicesView() } label: { Label("Paired Devices", systemImage: Theme.onMac ? "laptopcomputer" : "iphone") }
                    if !Theme.onMac, let code = macApprovalCode {
                        // The key this iPhone allows Mac use with. The Mac shows the same code once, on its trust card,
                        // so Colin can see the request really came from this phone (MacApprover, MacGrant.fingerprint).
                        VStack(alignment: .leading, spacing: 4) {
                            Label("Allow Mac Use From This iPhone", systemImage: "lock.laptopcomputer").foregroundStyle(Theme.ink)
                            Text("When your Mac asks whether to trust this iPhone, check that it shows this code.")
                                .font(Theme.secondary).foregroundStyle(Theme.inkSoft)
                            Text(code).font(.system(.title3, design: .monospaced).weight(.semibold)).foregroundStyle(Theme.ink)
                                .accessibilityLabel("Code \(code)")
                        }
                        .padding(.vertical, 4)
                    }
                    if Theme.onMac {
                        // The guided setup for Accessibility, Screen Recording and Full Disk Access (PermissionGuide.swift).
                        Button { MacLink.shared.permissionGuide(["op": "start"]) { macPermissions = $0["status"] as? [String: Bool] ?? macPermissions } } label: {
                            HStack {
                                Label("Let Zips Use This Mac", systemImage: "lock.open.laptopcomputer").foregroundStyle(Theme.ink)
                                Spacer()
                                if let allowed = macPermissions?.values.filter({ $0 }).count, let all = macPermissions?.count, all > 0 {
                                    Text(allowed == all ? "All Set" : "\(allowed) of \(all) Allowed")
                                        .font(Theme.secondary).foregroundStyle(Theme.inkSoft)
                                }
                            }
                        }
                        .task { MacLink.shared.permissionGuide(["op": "status"]) { macPermissions = $0["status"] as? [String: Bool] ?? macPermissions } }
                    }
                    Button("Log Out", role: .destructive) { confirmDisconnect = true }
                        .accessibilityIdentifier("logOut")
                }
            }
        }
        .scrollContentBackground(.hidden)
        #if DEBUG
        .task {   // -open settings voice (or accounts): that section in the middle of the screen, for screenshots
            let args = CommandLine.arguments
            if args.contains("-confirmDelete") { try? await Task.sleep(for: .milliseconds(900)); confirmDelete = true }
            if let i = args.firstIndex(of: "-open"), i + 2 < args.count, ["voice", "accounts", "privacy"].contains(args[i + 2]) {
                try? await Task.sleep(for: .milliseconds(400))
                scroller.scrollTo(args[i + 2], anchor: .center)
            }
        }
        #endif
        }
        .background(Theme.canvas)
        .navigationTitle("Settings")
        .task { await HomeModel.shared.load(state) }
        .task { await AccountsModel.shared.refresh(state) }   // Account: Sign In to Claude when it is needed
        .task(id: scenePhase) { previews = await LockScreenPrivacySection.previewsSetting() }
        .sheet(isPresented: $settingUpVoice) { VoiceSetupView() }
        .confirmationDialog("Delete everything?", isPresented: $confirmDelete, titleVisibility: .visible) {
            Button("Delete Everything", role: .destructive) { Task { await runDelete(thisDeviceOnly: false) } }
        } message: {
            Text("This removes your chats and cards, saved calls, pictures, setup answers and saved logins from this \(Theme.device) and from your computer. Zero starts fresh and signs this \(Theme.device) out. Your Zips and your Claude and ChatGPT accounts are not touched, and notes your Zips wrote for themselves stay on your computer. This can't be undone.")
        }
        .alert(deleteProblemTitle, isPresented: Binding(get: { deleteProblem != nil }, set: { if !$0 { deleteProblem = nil } })) {
            if deleteProblem == .computerUnreachable {
                Button("Delete From This \(Theme.device) Only", role: .destructive) { Task { await runDelete(thisDeviceOnly: true) } }
            }
            Button("Cancel", role: .cancel) {}
        } message: {
            Text(deleteProblemMessage)
        }
        .confirmationDialog("Log out of Zero on this \(Theme.device)?", isPresented: $confirmDisconnect, titleVisibility: .visible) {
            Button("Log Out", role: .destructive) { Task { await state.logOut() } }
        } message: {
            Text(AppState.accountUsername.isEmpty
                 ? "Your Zips, chats and settings stay as they are. To come back, sign in with a pairing code."
                 : "Your Zips, chats and settings stay as they are. To come back, sign in as \(AppState.accountUsername).")
        }
        .sheet(isPresented: $signingInToClaude) {
            NavigationStack {
                ScrollView {
                    VStack(alignment: .leading, spacing: 16) {
                        Text("Your Zips think with your own Claude plan, such as Pro or Max. Sign in once, and every Zip can use it.")
                            .font(Theme.text(17)).foregroundStyle(Theme.ink)
                            .fixedSize(horizontal: false, vertical: true)
                        ClaudeSignInPanel { signingInToClaude = false }
                    }
                    .padding(24)
                }
                .navigationTitle("Sign In to Claude")
                .navigationBarTitleDisplayMode(.inline)
                .toolbar { ToolbarItem(placement: .cancellationAction) { Button("Cancel") { signingInToClaude = false } } }
            }
        }
    }

    /// The home's quiet hours, else Zero's question limits, else 11 PM to 7 AM.
    private var quietHours: HomeSettings.QuietHours {
        HomeModel.shared.quietHours
            ?? state.proactivity.map { .init(start: $0.limits.quiet_start, end: $0.limits.quiet_end) }
            ?? .init(start: 23, end: 7)
    }

    private func mailSummary(_ home: HomeSettings) -> String {
        let n = home.googleAccounts.count + home.mailboxes.count + home.otherCalendars.count
        if home.googleAccounts.contains(where: \.needsSignIn) { return "Sign In Again" }
        return n == 0 ? "None Yet" : n == 1 ? "1 Account" : "\(n) Accounts"
    }

    private func runDelete(thisDeviceOnly: Bool) async {
        deleting = true
        let result = await state.deleteEverything(thisDeviceOnly: thisDeviceOnly)
        deleting = false
        if result != .done { deleteProblem = result }
    }

    private var deleteProblemTitle: String {
        if case .refused = deleteProblem { return "Zero Is Busy" }
        return "Can't Reach Your Computer"
    }

    private var deleteProblemMessage: String {
        switch deleteProblem {
        case .refused(let why): return why.isEmpty ? "A Zip is answering right now. Try again when it finishes." : why
        default: return "Nothing was deleted. Try again when your computer is on, or delete only what is on this \(Theme.device) now. Your computer will keep its copy until you delete it from there."
        }
    }
}


/// Settings, Questions From Zero: turn proactivity on or off, see each kind of question Zero asks and how often it
/// asked, move a kind to the morning brief only or off, and undo questions stopped from a card's menu.
struct ProactivityView: View {
    @Environment(AppState.self) private var state

    var body: some View {
        List {
            if let p = state.proactivity {
                Section {
                    Toggle(isOn: Binding(get: { p.enabled }, set: { on in Task { await state.setProactivity(enabled: on) } })) {
                        Text("Let Zero Ask Questions").font(Theme.body)
                    }
                    .tint(Theme.taffyDeep)
                } footer: {
                    Text("Zero looks over your email, your Zips' work and your chats, and asks one short question when something needs you. Questions with a deadline in the next day can reach your lock screen. Everything else waits in Zero's chat and your morning brief.")
                }
                Section {
                    NavigationLink { NoticesView() } label: {
                        LabeledContent {
                            Text("\(p.noticers.filter { $0.mode == "on" }.count) of \(p.noticers.count) asking")
                                .foregroundStyle(Theme.inkSoft)
                        } label: {
                            Label {
                                Text("Notices").foregroundStyle(Theme.ink)
                            } icon: {
                                TablerIcon(name: "TablerListCheck", size: 20).foregroundStyle(Theme.ink)
                            }
                        }
                    }
                } footer: {
                    Text("Each kind of question Zero asks, how often you answered it, and what Zero reads to notice things.")
                }
                if !p.stopped.isEmpty {
                    Section {
                        ForEach(p.stopped) { s in
                            HStack(spacing: 12) {
                                Text(s.label).font(Theme.secondary).foregroundStyle(Theme.ink)
                                Spacer()
                                Button("Undo") { Task { await state.undoStoppedQuestion(s.id) } }
                                    .font(Theme.secondary.weight(.medium)).foregroundStyle(Theme.taffyDeep)
                                    .buttonStyle(.borderless)
                            }
                        }
                    } header: { Text("Questions You Stopped") }
                }
                Section {
                    LabeledContent("Lock Screen Questions", value: "Up to \(p.limits.daily) a day")
                    LabeledContent("Time Between Them", value: hours(p.limits.spacing_hours))
                    LabeledContent("Quiet Hours", value: "\(clock(p.limits.quiet_start)) to \(clock(p.limits.quiet_end))")
                    LabeledContent("Sent Today", value: "\(p.today.pushed)")
                } header: { Text("Limits") } footer: {
                    Text(p.halved ? "Fewer questions got answers lately, so Zero is sending half as many to your lock screen."
                                  : "A tap on a question counts as your own words to Zero. Anything Zero would send for you still waits for your okay on its card.")
                }
            } else {
                Section {
                    Text("Loading").foregroundStyle(Theme.inkSoft)
                }
            }
        }
        .scrollContentBackground(.hidden)
        .background(Theme.canvas)
        .navigationTitle("Questions From Zero")
        .task { await state.loadProactivity() }
    }

    private func hours(_ h: Double) -> String {
        h == 1 ? "1 hour" : (h == h.rounded() ? "\(Int(h)) hours" : "\(Int(h * 60)) minutes")
    }

    private func clock(_ hour: Int) -> String {
        hour == 0 ? "12 AM" : hour < 12 ? "\(hour) AM" : hour == 12 ? "12 PM" : "\(hour - 12) PM"
    }
}


/// Settings, Notices: each kind of question with its state, how often Colin answered it and the last one asked, what
/// Zero reads (the hourly calendar and Trello copy), and today's prepared drafts and quiet looks.
struct NoticesView: View {
    @Environment(AppState.self) private var state

    var body: some View {
        List {
            if let p = state.proactivity {
                Section {
                    if let a = p.answered, a.pushed > 0, let rate = a.rate {
                        rateRow(title: "Answered on Your Lock Screen", answered: a.answered, asked: a.pushed, rate: rate)
                    } else {
                        Text("No questions have reached your lock screen in the last two weeks.")
                            .font(Theme.secondary).foregroundStyle(Theme.inkSoft)
                    }
                    if let busy = p.busy {
                        Text("Notifications are waiting until \(busy.title) ends at \(time(busy.until)).")
                            .font(Theme.secondary).foregroundStyle(Theme.ink)
                    }
                } footer: {
                    Text(p.halved ? "Fewer questions got answers lately, so Zero is sending half as many to your lock screen."
                                  : "If fewer than 4 in 10 get an answer, Zero sends half as many until that changes.")
                }
                Section {
                    ForEach(p.noticers) { n in noticerRow(n) }
                } header: { Text("Kinds of Questions") } footer: {
                    Text("Morning Brief Only keeps a kind of question off your lock screen. Zero does this by itself when you dismiss two of one kind in two weeks, and turns it off after a third.")
                }
                if !p.sources.isEmpty {
                    Section {
                        ForEach(p.sources) { src in sourceRow(src) }
                    } header: { Text("Calendar and Trello") } footer: {
                        Text("Zero reads your calendar and Trello once an hour from 7 AM to 11 PM. Notifications wait while you're at work, in class, at church or in a meeting.")
                    }
                }
                Section {
                    if let n = p.today.prepared, let cap = p.today.prepared_cap {
                        LabeledContent("Drafts Prepared", value: "\(n) of \(cap)")
                    }
                    if let n = p.today.quiet_looks, let cap = p.today.quiet_looks_cap {
                        LabeledContent("Quiet Looks", value: "\(n) of \(cap)")
                    }
                    LabeledContent("Lock Screen Questions", value: "\(p.today.pushed) of \(p.limits.daily)")
                } header: { Text("Today") } footer: {
                    Text("A Zip prepares a draft before Zero asks about a reply. When your chat is quiet for two hours, Zero takes a look of its own and may ask about something new.")
                }
            } else {
                Section { Text("Loading").foregroundStyle(Theme.inkSoft) }
            }
        }
        .scrollContentBackground(.hidden)
        .background(Theme.canvas)
        .navigationTitle("Notices")
        .task { await state.loadProactivity() }
    }

    private func noticerRow(_ n: ProactivityStatus.Noticer) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(alignment: .firstTextBaseline) {
                Text(n.name).font(Theme.body).foregroundStyle(Theme.ink)
                Spacer(minLength: 8)
                Picker(n.name, selection: Binding(get: { n.mode },
                                                  set: { m in Task { await state.setProactivity(mode: (n.id, m)) } })) {
                    Text("Ask Me").tag("on")
                    Text("Morning Brief Only").tag("digest")
                    Text("Off").tag("off")
                }
                .labelsHidden()
                .pickerStyle(.menu)
                .tint(Theme.inkSoft)
                .fixedSize()
            }
            Text(n.detail).font(Theme.caption).foregroundStyle(Theme.inkSoft)
            if n.asked > 0, let rate = n.rate {
                ProgressView(value: Double(rate), total: 100).tint(Theme.taffyDeep)
                    .accessibilityLabel("Answered \(rate) percent")
                Text("Answered \(n.answered) of \(n.asked) in the last two weeks.")
                    .font(Theme.caption).foregroundStyle(Theme.inkSoft)
            } else {
                Text("Not asked in the last two weeks.").font(Theme.caption).foregroundStyle(Theme.inkSoft)
            }
            if let last = n.last, !last.isEmpty {
                Text("Last asked: \(last)").font(Theme.caption).foregroundStyle(Theme.ink).lineLimit(2)
            }
            if !n.why.isEmpty {
                Text(n.why).font(Theme.caption).foregroundStyle(Theme.inkSoft)
            }
        }
        .padding(.vertical, 2)
    }

    private func rateRow(title: String, answered: Int, asked: Int, rate: Int) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            LabeledContent(title, value: "\(rate) percent")
            ProgressView(value: Double(rate), total: 100).tint(Theme.taffyDeep)
            Text("\(answered) of \(asked) answered in the last two weeks.").font(Theme.caption).foregroundStyle(Theme.inkSoft)
        }
    }

    private func sourceRow(_ src: ProactivityStatus.Source) -> some View {
        HStack(alignment: .top, spacing: 12) {
            TablerIcon(name: src.id == "trello" ? "TablerBrandTrello" : "TablerCalendarEvent", size: 20)
                .foregroundStyle(Theme.ink)
            VStack(alignment: .leading, spacing: 2) {
                Text(src.name).font(Theme.body).foregroundStyle(Theme.ink)
                Text(sourceLine(src)).font(Theme.caption).foregroundStyle(Theme.inkSoft)
            }
        }
    }

    private func sourceLine(_ src: ProactivityStatus.Source) -> String {
        let what = src.id == "trello" ? "cards" : "events"
        switch src.state {
        case "ok":
            let n = src.count.map { " \($0) \($0 == 1 ? String(what.dropLast()) : what)." } ?? ""
            return src.at.map { "Read at \(time($0)).\(n)" } ?? "Read."
        case "failed": return "Zero couldn't read it last time. It will try again within the hour."
        case "not_connected": return "Not connected. Connect it in Connected Accounts to use it here."
        case "chatgpt": return "Connected through ChatGPT, which Zero can't read on its own yet."
        default: return "Not read yet."
        }
    }

    private func time(_ ts: Double) -> String {
        Date(timeIntervalSince1970: ts).formatted(date: .omitted, time: .shortened)
    }
}


/// Choose the home screen icon: Taffy (light and dark), Clay Face or The Twist.
struct AppIconPicker: View {
    private struct Choice: Identifiable {
        let id: String?       // alternate icon name; nil is the main icon
        let title: String
        let detail: String
        let preview: String
    }
    private let choices = [
        Choice(id: nil, title: "Zero", detail: "Switches to the dark version in dark mode.", preview: "IconPreview-Main"),
        Choice(id: "AppIcon-ClayFace", title: "Clay Face", detail: "Warm clay, cut-paper style.", preview: "IconPreview-ClayFace"),
        Choice(id: "AppIcon-Twist", title: "The Twist", detail: "Orange with a striped taffy mark.", preview: "IconPreview-Twist"),
    ]
    @State private var current: String? = UIApplication.shared.alternateIconName
    @State private var error: String?

    var body: some View {
        ForEach(choices) { c in
            Button {
                guard c.id != current else { return }
                UIApplication.shared.setAlternateIconName(c.id) { err in
                    Task { @MainActor in
                        if err != nil { error = "That icon couldn't be set. Try again." } else { current = c.id; error = nil }
                    }
                }
            } label: {
                HStack(spacing: 14) {
                    Image(c.preview).resizable().frame(width: 44, height: 44)
                        .clipShape(RoundedRectangle(cornerRadius: 10, style: .continuous))
                        .overlay(RoundedRectangle(cornerRadius: 10, style: .continuous).stroke(Theme.rule, lineWidth: 0.5))
                    VStack(alignment: .leading, spacing: 2) {
                        Text(c.title).font(Theme.body).foregroundStyle(Theme.ink)
                        Text(c.detail).font(Theme.caption).foregroundStyle(Theme.inkSoft)
                    }
                    Spacer()
                    if c.id == current {
                        Image(systemName: "checkmark").font(.system(size: 15, weight: .semibold)).foregroundStyle(Theme.ink)
                            .accessibilityLabel("Current icon")
                    }
                }
            }
        }
        if let error { Text(error).font(Theme.caption).foregroundStyle(Theme.danger) }
    }
}


/// A pairing code made from a device that's already paired, so a new phone never needs the server.
private struct AddDeviceRow: View {
    @Environment(AppState.self) private var state
    @State private var code: String?
    @State private var expires: Date?
    @State private var error: String?
    @State private var busy = false

    var body: some View {
        if let code, let expires, expires > Date() {
            VStack(alignment: .leading, spacing: 6) {
                Text(code).font(.system(size: 28, weight: .semibold, design: .monospaced)).textSelection(.enabled)
                TimelineView(.periodic(from: .now, by: 1)) { ctx in
                    let left = max(0, Int(expires.timeIntervalSince(ctx.date)))
                    Text(left > 0 ? "Send the link to the new device and open it there. It works once and expires in \(left / 60):\(String(format: "%02d", left % 60))."
                                  : "This code expired.")
                        .font(Theme.caption).foregroundStyle(Theme.inkSoft)
                }
                // The code alone is not enough on a new device: the link carries the home's address too.
                if let url = PairLink(server: state.client.server, code: code).url {
                    ShareLink(item: url, subject: Text("Sign in to Zero"),
                              message: Text("Open this on your other device to sign in to Zero.")) {
                        Label("Send the Link", systemImage: "square.and.arrow.up")
                    }
                }
            }
        } else {
            Button(busy ? "Making a Code" : "Add a Device") { Task { await make() } }.disabled(busy)
            if let error { Text(error).font(Theme.caption).foregroundStyle(Theme.danger) }
        }
    }

    private func make() async {
        busy = true
        defer { busy = false }
        do {
            let c = try await state.client.newPairingCode()
            code = c.code
            expires = Date().addingTimeInterval(Double(c.expires_in))
            error = nil
        } catch { self.error = HubClient.describe(error) }
    }
}

#if DEBUG

#endif

/// Settings: the On-Device Voice's files. Download them (with progress), see that they are ready, or remove them.
private struct OnDeviceVoiceRow: View {
    @State private var voice = OnDeviceVoice.shared
    @State private var confirmRemove = false

    var body: some View {
        switch voice.status {
        case .notDownloaded, .failed:
            VStack(alignment: .leading, spacing: 4) {
                Button { Task { await voice.prepare() } } label: {
                    LabeledContent {
                        Text("\(OnDeviceVoice.downloadMB) MB").foregroundStyle(Theme.inkSoft)
                    } label: {
                        Label(voice.status == .failed ? "Try the Download Again" : "Download On-Device Voice",
                              systemImage: "arrow.down.circle").foregroundStyle(Theme.ink)
                    }
                }
                if voice.status == .failed {
                    Text(voice.detail).font(.footnote).foregroundStyle(Theme.inkSoft)
                }
            }
        case .working:
            VStack(alignment: .leading, spacing: 6) {
                ProgressView(value: voice.progress)
                    .tint(Theme.taffyDeep)
                Text(voice.detail).font(.footnote).foregroundStyle(Theme.inkSoft)
            }
            .accessibilityElement(children: .combine)
            .accessibilityLabel("On-Device Voice, \(Int(voice.progress * 100)) percent. \(voice.detail)")
        case .downloaded, .ready:
            Button(role: .destructive) { confirmRemove = true } label: {
                LabeledContent {
                    Text("\(Int(voice.bytesOnDisk / 1_000_000)) MB").foregroundStyle(Theme.inkSoft)
                } label: {
                    Label("Remove On-Device Voice", systemImage: "trash")
                }
            }
            .confirmationDialog("Remove the On-Device Voice?", isPresented: $confirmRemove, titleVisibility: .visible) {
                Button("Remove", role: .destructive) { Task { await voice.remove() } }
            } message: {
                Text("It downloads again the next time a call needs it.")
            }
        }
    }
}
