import Speech
import SwiftUI

/// A conversation with one bot, laid out like Grok Bot: floating back button, a pill with the
/// bot's name, its computer one tap away, gray bubbles for the bot and black ones for Colin, and a
/// composer with a plus button, dictation and voice chat.
struct ChatScreen: View {
    @Environment(AppState.self) private var state
    @Environment(\.dismiss) private var dismiss
    let botID: String
    var onCall: () -> Void
    /// On the Mac the chat sits beside the sidebar: no back button, and the name pill centered.
    var embedded = false
    var onRemoved: (() -> Void)?
    @State private var showScreen = false
    @State private var showProfile = false
    /// The profile opens half height; dragged up it shows the notification kinds in full.
    @State private var profileDetent: PresentationDetent = .medium

    private var bot: Bot? { state.bot(botID) }

    /// The Zip's screen. For screenshots, `-screenSite <address>` opens it as a sign-in take-over.
    private var screenView: ScreenView {
        #if DEBUG
        let a = CommandLine.arguments
        if let i = a.firstIndex(of: "-screenSite"), i + 1 < a.count, let site = URL(string: a[i + 1]) {
            return ScreenView(botID: botID, takeOver: true, site: site, reason: "Sign in to \((site.host() ?? "the site").replacingOccurrences(of: "www.", with: ""))", requestID: 1)
        }
        #endif
        return ScreenView(botID: botID)
    }

    var body: some View {
        ChatView(botID: botID, onCall: onCall, onComputer: { showScreen = true })
            .safeAreaInset(edge: .top) {
                HStack(spacing: 10) {
                    if embedded {
                        Spacer()
                    } else {
                        CircleButton(action: { dismiss() }) { Image(systemName: "chevron.left") }
                            .accessibilityLabel("Back")
                    }
                    Button { showProfile = true } label: { NamePill(bot: bot) }
                        .buttonStyle(PressStyle())
                        .accessibilityHint("Shows what \(bot?.name ?? "this Zip") does")
                    Spacer()
                    if !embedded {
                        CircleButton(action: onCall) { Image(systemName: "phone") }
                            .accessibilityLabel("Call \(bot?.name ?? "Zip")")
                        CircleButton(action: { showScreen = true }) { Image(systemName: "macwindow") }
                            .accessibilityLabel("\((bot?.name ?? "").possessive) Computer")
                    }
                }
                // On the Mac the pill stays centered and the call and computer buttons sit at the right edge.
                .overlay(alignment: .trailing) {
                    if embedded {
                        HStack(spacing: 8) {
                            CircleButton(size: 36, action: onCall) { Image(systemName: "phone") }
                                .accessibilityLabel("Call \(bot?.name ?? "Zip")")
                            CircleButton(size: 36, action: { showScreen = true }) { Image(systemName: "macwindow") }
                                .accessibilityLabel("\((bot?.name ?? "").possessive) Computer")
                        }
                    }
                }
                .padding(.horizontal, 20)
                .padding(.top, embedded ? 11 : 4)
                .padding(.bottom, 8)
                .modifier(TopBar(solid: !embedded))
            }
            // On the Mac the window has no title bar, so the pill sits in that space like Grok Bot's.
            .ignoresSafeArea(.container, edges: embedded ? .top : [])
            .toolbar(.hidden, for: .navigationBar)
            .fullScreenCover(isPresented: $showScreen) {
                if state.demoMode { DemoScreenNote() } else { screenView.environment(state) }
            }
            .sheet(isPresented: $showProfile) {
                BotProfile(botID: botID,
                           onComputer: { showProfile = false; showScreen = true },
                           onCall: { showProfile = false; onCall() },
                           onRemoved: { showProfile = false; if let onRemoved { onRemoved() } else { dismiss() } })
                    .environment(state)
                    .presentationDetents([.medium, .large], selection: $profileDetent)
                    .presentationDragIndicator(.visible)
            }
            #if DEBUG
            .task {
                let a = CommandLine.arguments
                if let i = a.firstIndex(of: "-screen"), i + 1 < a.count, a[i + 1] == botID { showScreen = true }
                if let i = a.firstIndex(of: "-profile"), i + 1 < a.count, a[i + 1] == botID {   // the bot info sheet
                    if a.contains("-profileLarge") { profileDetent = .large }
                    try? await Task.sleep(for: .milliseconds(500))
                    showProfile = true
                }
            }
            #endif
    }
}

struct ChatView: View {
    @Environment(AppState.self) private var state
    let botID: String
    var onCall: () -> Void = {}
    var onComputer: () -> Void = {}

    @State private var draft = ""
    /// The message just sent and when. Tapping Send while the keyboard holds an autocorrection or prediction
    /// makes iOS commit it after the box was cleared, which puts the whole message back; that echo is cleared.
    @State private var justSent: (text: String, at: Date)?
    @FocusState private var composing: Bool
    @State private var dictation = Dictation()
    /// Whether the newest message is on screen; news only pulls the chat down when Colin is already there.
    @State private var atBottom = true
    /// He is reading the newest messages: true until he scrolls up himself. Unlike `atBottom`, a chat that got shorter
    /// (a card folding, a call ending, a reload while the app was away) does not clear it, so the chat follows the
    /// bottom back instead of showing blank space past the last message (Colin, Oct 10, iPhone and Mac).
    @State private var followsBottom = true
    @State private var contentHeight: CGFloat = 0
    @State private var lastShrink: Date?
    @Environment(\.scenePhase) private var scenePhase
    @State private var newBelow = false
    /// The message box's height. It sits over the chat as an inset, so the scroll view's own size never changes
    /// when it grows to a second line; the chat follows it here instead.
    @State private var composerHeight: CGFloat = 0
    /// Zero's first tasks after the onboarding (Model/Fluid.swift), until started or put away.
    @State private var firstTasks: FirstTasks? = FirstTasks.load()
    /// A choice card's "Type your own answer" slides from the card into the message box in this space (OwnAnswerSlot).
    @Namespace private var composerSpace
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    /// Bumped to put the Mac's message field in focus (the iPhone's uses `composing`).
    @State private var macFocus = 0
    /// The message lit up for a moment after its quote above a reply was tapped.
    @State private var flashID: String?

    private var bot: Bot? { state.bot(botID) }
    /// The conversation in time order. Open sign-in and account cards are not in it: they wait at the bottom of the
    /// chat until answered, like the live Computer card, so a card asked for at 11:54 is still where Colin looks at
    /// 12:59 (Oct 9).
    private var messages: [ChatMessage] { timeline.messages }
    /// The conversation as drawn: a choice card he answered stands for the message that carried his answer, which is
    /// not drawn again (ChatTimeline).
    private var timeline: ChatTimeline { ChatTimeline((state.chats[botID] ?? []).filter { !Self.waitsAtBottom($0) }) }
    private var openAsks: [ChatMessage] { (state.chats[botID] ?? []).filter(Self.waitsAtBottom) }
    static func waitsAtBottom(_ m: ChatMessage) -> Bool { m.kind == .login || m.kind == .account }
    private var pending: [TaskItem] { state.needsYou(for: botID) }
    private var workingText: String? { state.workingText(for: botID) }

    var body: some View {
        let timeline = self.timeline
        let messages = timeline.messages
        ScrollViewReader { proxy in
            ScrollView {
                LazyVStack(alignment: .leading, spacing: Theme.onMac ? 0 : 10) {
                    if state.offline { OfflineNote() }
                    if messages.isEmpty && openAsks.isEmpty && pending.isEmpty && state.signInRequests(for: botID).isEmpty
                        && workingText == nil && (botID != "default" || firstTasks == nil) {
                        EmptyChat(bot: bot) { draft = $0; composing = true }.padding(.top, 48)
                    }
                    ForEach(Array(messages.enumerated()), id: \.element.id) { i, m in
                        if let stamp = separator(before: i, in: messages) {
                            Text(stamp).font(Theme.text(15)).foregroundStyle(Theme.inkSoft)
                                .frame(maxWidth: .infinity).padding(.vertical, 8)
                        }
                        MessageRow(message: m, botID: botID).id(m.id)
                            .padding(.top, Theme.onMac ? macGap(before: i, in: messages) : phoneGap(before: i, in: messages))
                    }
                    // A Zip's open sign-in or account card, below everything said so far.
                    ForEach(openAsks) { m in
                        MessageRow(message: m, botID: botID).id(m.id)
                            .padding(.top, Theme.onMac ? 16 : 6)
                    }
                    // The Zip's own computer, while it uses it or Colin holds it. A sign-in card has its own.
                    if let live = state.computers[botID], !openAsks.contains(where: { $0.kind == .login }) {
                        ComputerCard(botID: botID, live: live == "control" ? .control : .working)
                            .macBubbleWidth().id("computer")
                    }
                    ForEach(pending) { t in ApprovalCard(task: t).macBubbleWidth().id("task-\(t.id)") }
                    ForEach(state.signInRequests(for: botID)) { r in
                        SignInRequestCard(request: r).macBubbleWidth().id("signin-\(r.id)")
                    }
                    ForEach(state.pinging.filter { $0.from == botID }, id: \.self) { p in
                        PingChip(ping: p).macBubbleWidth().id("ping-\(p.to.joined())")
                    }
                    // Zero's first tasks after the onboarding, then what the Zip is doing, waiting on or stuck on.
                    if botID == "default", firstTasks != nil {
                        FirstTasksCard(tasks: $firstTasks).macBubbleWidth().id("first-tasks")
                    }
                    if workingText != nil || state.problem(for: botID) != nil {
                        ChatStatusArea(botID: botID).macBubbleWidth().id("working")
                    }
                    Color.clear.frame(height: 6).id("bottom")
                        .onAppear { atBottom = true; followsBottom = true; newBelow = false }
                        .onDisappear {
                            atBottom = false
                            // Gone from view because he scrolled up (a trackpad scroll is not a drag), not because the
                            // chat got shorter under him.
                            let gone = Date()
                            Task { @MainActor in
                                try? await Task.sleep(for: .milliseconds(300))
                                if !atBottom, lastShrink.map({ abs($0.timeIntervalSince(gone)) < 0.6 }) != true {
                                    followsBottom = false
                                }
                            }
                        }
                }
                .environment(\.cardAnswers, timeline.answers)
                .padding(.horizontal, 20)
                .padding(.top, 8)
                .frame(maxWidth: Theme.onMac ? Theme.chatColumn + 40 : .infinity)
                .frame(maxWidth: .infinity)
                .onGeometryChange(for: CGFloat.self) { $0.size.height } action: { h in
                    // The chat got shorter while he was reading the newest messages: go back to the bottom.
                    let shrank = h < contentHeight - 1
                    contentHeight = h
                    if shrank { lastShrink = Date() }
                    if shrank && followsBottom { proxy.scrollTo("bottom", anchor: .bottom) }
                }
            }
            .scrollDismissesKeyboard(.interactively)
            .modifier(FollowsBottom(follows: $followsBottom, scenePhase: scenePhase,
                                    ids: messages.map(\.id), proxy: proxy))
            .environment(\.flashedMessage, flashID)
            .environment(\.showOriginal, ShowOriginal { quote, replyID in showOriginal(quote, before: replyID, proxy: proxy) })
            .defaultScrollAnchor(messages.isEmpty ? .top : .bottom)
            .onChange(of: messages.count) { old, new in
                // His own message always takes him to the bottom; anything else only if he is already there.
                if atBottom || messages.last?.kind == .colin { withAnimation { proxy.scrollTo("bottom", anchor: .bottom) } }
                else if new > old { newBelow = true }
            }
            .onChange(of: workingText) { _, _ in if atBottom { withAnimation { proxy.scrollTo("bottom", anchor: .bottom) } } }
            .onChange(of: state.problem(for: botID)) { _, p in if p != nil && atBottom { withAnimation { proxy.scrollTo("bottom", anchor: .bottom) } } }
            .onChange(of: openAsks.map(\.id)) { old, new in
                // A new card: in view if he is at the bottom, otherwise the New Messages pill points to it.
                if atBottom { withAnimation { proxy.scrollTo("bottom", anchor: .bottom) } }
                else if new.contains(where: { !old.contains($0) }) { newBelow = true }
            }
            .onChange(of: state.computers[botID]) { _, _ in if atBottom { withAnimation { proxy.scrollTo("bottom", anchor: .bottom) } } }
            .onChange(of: state.pinging) { _, _ in if atBottom { withAnimation { proxy.scrollTo("bottom", anchor: .bottom) } } }
            .onChange(of: composing) { _, on in if on { withAnimation { proxy.scrollTo("bottom", anchor: .bottom) } } }
            // A longer message box keeps the newest message just above it, moving with the box, not under it.
            .onChange(of: composerHeight) { _, _ in if atBottom { proxy.scrollTo("bottom", anchor: .bottom) } }
            .onAppear { proxy.scrollTo("bottom", anchor: .bottom) }
            .overlay(alignment: .bottom) {
                if newBelow && !atBottom {
                    Button { withAnimation { proxy.scrollTo("bottom", anchor: .bottom) }; newBelow = false } label: {
                        Label("New Messages", systemImage: "arrow.down")
                            .font(Theme.text(14, .medium)).foregroundStyle(Theme.ink)
                            .padding(.horizontal, 14).padding(.vertical, 8)
                            .floating(Capsule())
                    }
                    .buttonStyle(PressStyle())
                    .padding(.bottom, 10)
                    .transition(.opacity.combined(with: .move(edge: .bottom)))
                }
            }
        }
        .background(Theme.canvas)
        .safeAreaInset(edge: .bottom) {
            composer.onGeometryChange(for: CGFloat.self) { $0.size.height } action: { composerHeight = $0 }
        }
        .environment(\.composerSpace, composerSpace)
        // A card's own answer slid into the box: the keyboard comes up with it.
        .onChange(of: state.answering[botID]?.id) { _, id in
            guard id != nil else { return }
            if Theme.onMac { macFocus &+= 1 } else { composing = true }
        }
        // Swipe or Reply on a bubble: the keyboard comes up under the reply header.
        .onChange(of: state.replying[botID]?.messageID) { _, id in
            guard id != nil else { return }
            if Theme.onMac { macFocus &+= 1 } else { composing = true }
        }
        .overlay {
            if let t = state.tapback, t.botID == botID {
                TapbackLayer(target: t)
            }
        }
        .onDisappear { state.tapback = nil; if state.viewingBot == botID { state.viewingBot = nil } }
        .task { await state.loadChat(botID) }
        .refreshable { await state.loadChat(botID) }
        .onAppear { draft = state.drafts[botID] ?? ""; state.viewingBot = botID; state.away.removeAll { $0.bot_id == botID } }
        .onDisappear { dictation.stop() }
        .onChange(of: draft) { _, v in
            if let sent = justSent, Date().timeIntervalSince(sent.at) < 2, Self.isEcho(v, of: sent.text) {
                draft = ""
                return
            }
            state.drafts[botID] = v
        }
        .onChange(of: state.drafts[botID]) { _, v in   // a message that couldn't be sent comes back to the box
            if let v, v != draft { justSent = nil; draft = v }
        }
        .onChange(of: dictation.text) { _, v in if dictation.active { draft = v } }
        .alert("Dictation", isPresented: Binding(get: { dictation.problem != nil }, set: { if !$0 { dictation.problem = nil } })) {
            if dictation.problemNeedsSettings {
                Button("Open Settings") {
                    if let url = URL(string: UIApplication.openSettingsURLString) { UIApplication.shared.open(url) }
                }
            }
            Button("OK", role: .cancel) {}
        } message: { Text(dictation.problem ?? "") }
    }

    /// Grok Bot's rhythm on the Mac: one sender's messages stack close, a new sender gets room.
    private func macGap(before i: Int, in messages: [ChatMessage]) -> CGFloat {
        guard i > 0 else { return 0 }
        if folded(messages[i]) && folded(messages[i - 1]) { return 3 }
        // A card he answered ends in his own bubble, and its caption starts it like one of his messages.
        func mine(_ m: ChatMessage) -> Bool { m.kind == .colin || (m.kind == .choices && m.answer != nil) }
        return mine(messages[i]) == mine(messages[i - 1]) ? 5 : 16
    }

    /// Closed and dismissed questions from Zero fold into compact rows; several in a row sit close together.
    private func phoneGap(before i: Int, in messages: [ChatMessage]) -> CGFloat {
        i > 0 && folded(messages[i]) && folded(messages[i - 1]) ? -5 : 0
    }

    private func folded(_ m: ChatMessage) -> Bool {
        guard let q = m.question else { return false }
        return !q.isOpen && m.answer == nil
    }

    /// A centered time stamp when an hour or more passed since the previous message.
    private func separator(before i: Int, in messages: [ChatMessage]) -> String? {
        guard let d = messages[i].date else { return nil }
        if i == 0 { return d.separatorStamp }
        guard let prev = messages[i - 1].date else { return nil }
        return d.timeIntervalSince(prev) > 3600 ? d.separatorStamp : nil
    }

    @ViewBuilder private var composer: some View {
        if Theme.onMac { macComposer } else { phoneComposer }
    }

    private var moreMenu: some View {
        Menu {
            Button { onComputer() } label: { Label("\((bot?.name ?? "Zip").possessive) Computer", systemImage: "macwindow") }
            Button { startCall() } label: { Label("Call", systemImage: "phone") }
            Button { Task { await state.loadChat(botID) } } label: { Label("Refresh", systemImage: "arrow.clockwise") }
        } label: {
            Image(systemName: "plus")
                .font(.system(size: 15, weight: .regular))
                .foregroundStyle(Theme.ink)
                .frame(width: 30, height: 30)
                .overlay(Circle().stroke(Theme.rule))
                .frame(width: 36, height: 36)
                .contentShape(Circle())
        }
        .menuStyle(.button)
        .buttonStyle(.plain)
        .menuIndicator(.hidden)
        .accessibilityLabel("More")
    }

    /// Grok Bot for Mac's composer: one capsule with the plus, the field, dictation and voice inside.
    private var macComposer: some View {
        VStack(alignment: .leading, spacing: 0) {
            answeringHeader
            replyingHeader
            HStack(alignment: .bottom, spacing: 4) {
                moreMenu
                MacComposerField(text: $draft, placeholder: placeholder, focusRequest: macFocus,
                                 onEscape: { guard state.replying[botID] != nil else { return false }; cancelReply(); return true }) { text in
                    guard !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { return false }
                    send(text)
                    return true
                }
                trailingButton
            }
            .padding(.horizontal, 4)
            .padding(.vertical, 3)
        }
        .background(Theme.composer, in: RoundedRectangle(cornerRadius: 22, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: 22, style: .continuous).stroke(Theme.composerEdge, lineWidth: Theme.onMac ? 0.5 : 1))
        .frame(maxWidth: Theme.chatColumn)
        .padding(.horizontal, 20)
        .padding(.top, 6)
        .padding(.bottom, 16)
        .frame(maxWidth: .infinity)
        .barFade(.bottom)
    }

    private var phoneComposer: some View {
        HStack(alignment: .bottom, spacing: 10) {
            Menu {
                Button { onComputer() } label: { Label("\((bot?.name ?? "Zip").possessive) Computer", systemImage: "macwindow") }
                Button { startCall() } label: { Label("Call", systemImage: "phone") }
                Button { Task { await state.loadChat(botID) } } label: { Label("Refresh", systemImage: "arrow.clockwise") }
            } label: {
                Image(systemName: "plus")
                    .font(.system(size: 18, weight: .medium))
                    .foregroundStyle(Theme.ink)
                    .frame(width: 44, height: 44)
                    .floating(Circle())
            }
            .menuStyle(.button)
            .buttonStyle(.plain)
            .menuIndicator(.hidden)
            .accessibilityLabel("More")
            .padding(.bottom, 2)
            VStack(alignment: .leading, spacing: 0) {
                answeringHeader
                replyingHeader
                HStack(alignment: .bottom, spacing: 0) {
                    TextField(placeholder, text: $draft, axis: .vertical)
                        .font(Theme.body)
                        .lineLimit(1...6)
                        .focused($composing)
                        .padding(.leading, 18)
                        .padding(.vertical, 13)
                    trailingButton
                        .padding(.trailing, 4)
                        .padding(.bottom, 2)
                }
            }
            .floating(RoundedRectangle(cornerRadius: 24, style: .continuous), fill: Theme.composer)
        }
        .padding(.horizontal, 16)
        .padding(.top, 6)
        .padding(.bottom, 8)
        .barFade(.bottom)
    }

    private var busy: Bool { state.working[botID] != nil }

    private var placeholder: String {
        state.answering[botID] != nil ? "Your answer" : "Message \(bot?.name ?? "Zero")"
    }

    /// The card being answered, as the message box's header: the card's "Type your own answer" field slid down and
    /// became this, so the box reads as the card's answer field. The X puts it back on the card.
    @ViewBuilder private var answeringHeader: some View {
        if let m = state.answering[botID] {
            HStack(spacing: 8) {
                Image(systemName: "arrowshape.turn.up.left").font(.system(size: 12, weight: .semibold))
                Text(m.text).lineLimit(1)
                Spacer(minLength: 4)
                Button { stopAnswering() } label: {
                    Image(systemName: "xmark").font(.system(size: 11, weight: .semibold))
                        .frame(width: 26, height: 26).contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .accessibilityLabel("Stop Answering")
            }
            .font(Theme.text(Theme.onMac ? 13 : 14, .medium)).foregroundStyle(Theme.inkSoft)
            .padding(.leading, 12).padding(.trailing, 4).padding(.vertical, 3)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(Theme.field, in: RoundedRectangle(cornerRadius: 14, style: .continuous))
            .modifier(Matched(id: OwnAnswerSlot.matchID(m), space: composerSpace, on: !reduceMotion))
            .padding(.horizontal, 6).padding(.top, 6)
            .transition(.opacity)
            .accessibilityElement(children: .contain)
            .accessibilityLabel("Answering: \(m.text)")
        }
    }

    private func stopAnswering() {
        withAnimation(OwnAnswerSlot.motion(reduceMotion)) { state.answering[botID] = nil }
    }

    /// The message being replied to (Replies.swift). A card's answer header and this one replace each other.
    @ViewBuilder private var replyingHeader: some View {
        if let target = state.replying[botID], state.answering[botID] == nil {
            ReplyingHeader(target: target, onCancel: cancelReply)
                .transition(reduceMotion ? .opacity : .opacity.combined(with: .move(edge: .bottom)))
        }
    }

    private func cancelReply() {
        withAnimation(ReplyMotion.header(reduceMotion)) { state.replying[botID] = nil }
    }

    /// Scrolls to the message a quote points at, when it is loaded, and lights it up for a moment.
    private func showOriginal(_ quote: ReplyQuote, before replyID: String, proxy: ScrollViewProxy) {
        let timeline = self.timeline
        let all = timeline.messages
        let end = all.firstIndex { $0.id == replyID } ?? all.count
        let quotable: Set<ChatMessage.Kind> = [.colin, .bot, .boardUpdate, .checkin, .team, .call]
        var lines: [ChatSync.Line] = []
        // A reply to a card's answer bubble points at the message behind it, which the card stands for.
        var quote = quote
        if let id = quote.messageID, let card = timeline.answers.first(where: { $0.value.id == id })?.key {
            quote.messageID = card
        }
        for m in all[..<end] {
            if m.kind == .choices, let a = m.answer {
                lines.append(.init(id: m.id, mine: true, text: a.text, date: m.date))
                continue
            }
            guard quotable.contains(m.kind) else { continue }
            lines.append(.init(id: m.id, mine: m.kind == .colin, text: AppState.quotedWords(m, full: false), date: m.date))
            if m.full != nil {
                lines.append(.init(id: m.id, mine: false, text: AppState.quotedWords(m, full: true), date: m.date))
            }
        }
        guard let id = ReplyLogic.original(of: quote, in: lines) else { return }
        withAnimation(reduceMotion ? nil : .easeInOut(duration: 0.35)) { proxy.scrollTo(id, anchor: .center) }
        Task {
            try? await Task.sleep(for: .milliseconds(reduceMotion ? 50 : 320))
            flashID = id
            try? await Task.sleep(for: .milliseconds(1100))
            if flashID == id { flashID = nil }
        }
    }

    @ViewBuilder private var trailingButton: some View {
        let empty = draft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
        HStack(spacing: 0) {
            Button {
                if Theme.onMac { composing = true }   // Wispr Flow types into the focused field
                Task { await dictation.toggle(appendingTo: draft) }
            } label: {   // problems show in an alert below
                Image(systemName: dictation.active ? "stop.circle.fill" : "mic")
                    .font(.system(size: dictation.active ? 22 : (Theme.onMac ? 15 : 18), weight: .medium))
                    .foregroundStyle(dictation.active ? Theme.danger : (Theme.onMac ? Theme.ink : Theme.inkSoft))
                    .frame(width: Theme.onMac ? 30 : 44, height: Theme.onMac ? 30 : 44)
                    .background(Theme.onMac && !dictation.active ? Theme.bubble : .clear, in: Circle())
                    .frame(width: Theme.onMac ? 36 : 44, height: Theme.onMac ? 36 : 44)
                    .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .accessibilityLabel(dictation.active ? "Stop Dictation" : "Dictate")
            let paused = state.isPaused(botID)
            if empty && !dictation.active && busy && !paused {
                // The Zip is working: Stop interrupts it, and nothing queued starts a new turn behind it (#55, #56).
                StopButton { Task { await state.stop(botID) } }
            } else if empty && !dictation.active {
                roundAction("phone.fill", label: "Call \(bot?.name ?? "Zip")", action: startCall)
            } else if !empty {
                // Sending while the bot works is fine: the message waits its turn, and the chat says so (#57). A Zip
                // held on a card is not working: the message goes straight through (hub turns.send).
                roundAction("arrow.up", label: state.answering[botID] != nil ? "Send Answer"
                            : busy && !paused ? "Send After This Reply"
                            : state.replying[botID] != nil ? "Send Reply" : "Send", action: { send() })
            }
        }
    }

    private func roundAction(_ symbol: String, label: String, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Image(systemName: symbol)
                .font(.system(size: Theme.onMac ? 13 : 15, weight: .semibold))
                .foregroundStyle(Theme.actionGlyph)
                .frame(width: Theme.onMac ? 30 : 36, height: Theme.onMac ? 30 : 36)
                .background(Theme.action, in: Circle())
                .frame(width: Theme.onMac ? 36 : 44, height: Theme.onMac ? 36 : 44)
                .contentShape(Circle())
        }
        .buttonStyle(PressStyle(scale: 0.9))
        .accessibilityLabel(label)
    }

    private func startCall() {
        dictation.stop()
        onCall()
    }

    /// Whether text that appeared in the box is the keyboard putting back the message just sent (possibly with
    /// its last word autocorrected), rather than something new.
    static func isEcho(_ text: String, of sent: String) -> Bool {
        func words(_ s: String) -> [String] {
            s.lowercased().split { !$0.isLetter && !$0.isNumber }.map(String.init)
        }
        let a = words(text), b = words(sent)
        guard !a.isEmpty, !b.isEmpty else { return false }
        if a == b { return true }
        // Everything but the last word matches, and it is about as long: an autocorrected last word.
        return a.count == b.count && a.count >= 3 && Array(a.dropLast()) == Array(b.dropLast())
    }

    private func send(_ typed: String? = nil) {
        if dictation.active { dictation.stop() }
        let text = typed ?? draft
        if typed == nil { justSent = (text, Date()) }
        draft = ""
        state.drafts[botID] = nil
        if let card = state.answering[botID] {
            // The box was the card's answer field: this answers the card, which closes it on the hub, and the card
            // turns into his answer.
            let words = text.trimmingCharacters(in: .whitespacesAndNewlines)
            state.sentAnswers.insert(card.id)
            withAnimation(CardAnswerView.motion(reduceMotion)) {
                state.answering[botID] = nil
                state.markAnswered(card, bot: botID, text: words)
            }
            Task {
                await state.answerChoice(card, bot: botID, picked: words, custom: true)
                state.sentAnswers.remove(card.id)
            }
            return
        }
        let target = state.replying[botID]
        if target != nil { withAnimation(ReplyMotion.header(reduceMotion)) { state.replying[botID] = nil } }
        Task { await state.send(text, to: botID, replyingTo: target) }
    }
}

private struct EmptyChat: View {
    let bot: Bot?
    var suggest: (String) -> Void = { _ in }

    private var ideas: [String] {
        switch bot?.id {
        case "school": return ["What is due this week?", "Help me plan my study time", "Find my next class reading"]
        case "builder": return ["What are you working on?", "Check the build status", "Start on the next Trello card"]
        case "clients": return ["Any client email I should answer?", "Draft a follow-up for me to review"]
        case "money": return ["How much did I spend this week?", "Any bills coming up?"]
        case "career": return ["Any new jobs worth a look?", "Update my resume notes"]
        case "default", nil: return ["What needs me today?", "What is the team working on?", "Plan my afternoon"]
        default: return ["What can you help me with?", "What are you working on?"]
        }
    }

    var body: some View {
        VStack(spacing: 14) {
            BotAvatar(bot: bot, size: 88)
            Text(bot?.name ?? "Zero").font(Theme.title).foregroundStyle(Theme.ink)
            Text(bot?.isLead == false ? bot?.role ?? "" : "Tell me what is on your mind. I will sort out who should handle it.")
                .font(Theme.secondary).foregroundStyle(Theme.inkSoft).multilineTextAlignment(.center)
                .padding(.horizontal, 30)
            VStack(spacing: 8) {
                ForEach(ideas, id: \.self) { idea in
                    Button { suggest(idea) } label: {
                        Text(idea).font(Theme.secondary).foregroundStyle(Theme.ink)
                            .padding(.horizontal, 16).frame(minHeight: 40)
                            .background(Theme.canvas, in: Capsule())
                            .overlay(Capsule().stroke(Theme.rule, lineWidth: 1))
                    }
                    .buttonStyle(PressStyle())
                }
            }
            .padding(.top, 10)
        }
        .frame(maxWidth: .infinity)
    }
}

struct MessageRow: View {
    @Environment(AppState.self) private var state
    let message: ChatMessage
    var botID: String = "default"
    @State private var showCall = false
    /// Empty space beside a bubble; wider on the Mac so lines stay readable in a big window.
    static let gap: CGFloat = Theme.onMac ? 40 : 50
    static let size: CGFloat = Theme.onMac ? 15 : 17
    static let hPad: CGFloat = Theme.onMac ? 12 : 16
    static let vPad: CGFloat = Theme.onMac ? 7 : 11
    static let radius: CGFloat = Theme.onMac ? 18 : 22

    var body: some View {
        row.modifier(RowChrome(id: message.id))
    }

    @ViewBuilder private var row: some View {
        switch message.kind {
        case .colin:
            VStack(alignment: .trailing, spacing: 4) {
                if let quote = message.replyTo {
                    // The message this one replies to, small and faded above it (Replies.swift).
                    HStack {
                        Spacer(minLength: Self.gap + 10)
                        CapWidth(max: Theme.onMac ? Theme.bubbleMax * 0.8 : .infinity) {
                            ReplyQuoteView(quote: quote, replyID: message.id, botName: state.bot(botID)?.name ?? "Zero")
                        }
                    }
                    .padding(.bottom, -1)
                }
                MineBubble(message: message, botID: botID)
                if state.queuedIDs.contains(message.id) { QueuedCaption() }
            }
        case .bot where expiredCard == nil && ReplyBubbles.parts(message.full.flatMap { ShortReplies.shared.opened.contains(message.id) ? $0 : nil } ?? message.text) != nil:
            // A reply of a few paragraphs shows as a few small bubbles, like a teammate's texts.
            HStack {
                CapWidth(max: Theme.onMac ? Theme.bubbleMax : .infinity) {
                    ReplyBubbles(message: message, size: Self.size, hPad: Self.hPad, vPad: Self.vPad, radius: Self.radius)
                        .modifier(BotTapback(message: message, botID: botID))
                }
                Spacer(minLength: Self.gap)
            }
        case .bot, .boardUpdate, .checkin:
            HStack {
                CapWidth(max: Theme.onMac ? Theme.bubbleMax : .infinity) {
                VStack(alignment: .leading, spacing: 6) {
                    if message.kind != .bot {
                        HStack(spacing: 6) {
                            if let id = message.botID, let b = state.bot(id) { BotAvatar(bot: b, size: 16) }
                            Text(message.label ?? "Team update")
                                .font(Theme.text(13, .medium)).foregroundStyle(Theme.inkSoft)
                        }
                    }
                    ReplyText(message: message, size: Self.size)   // short with Show Full Reply (ShortReplies.swift)
                    if let what = expiredCard {
                        // A card that closed with no answer: one tap asks the Zip to raise it again (#38, #39).
                        RowActionButton(action: RowAction(title: "Ask Again") { await $0.askAgain(message.botID ?? botID, what: what) })
                    }
                }
                .padding(.horizontal, Self.hPad).padding(.vertical, Self.vPad)
                .background(Theme.bubble, in: RoundedRectangle(cornerRadius: Self.radius, style: .continuous))
                .modifier(BotTapback(message: message, botID: botID))
                }
                Spacer(minLength: Self.gap)
            }
        case .call:
            HStack {
                Spacer(minLength: Self.gap + 10)
                Button { showCall = true } label: { callBubble }
                    .buttonStyle(PressStyle(scale: 0.97))
                    .accessibilityLabel("Voice call, \(message.label ?? ""). Shows a summary.")
                    .replyable(message, botID: botID, isMine: true, state: state)
                    // A popover closes when Colin clicks or taps anywhere else; on the iPhone it shows as a sheet
                    // he can swipe away. Both have an X.
                    .popover(isPresented: $showCall, arrowEdge: .trailing) {
                        callSummary
                            .frame(idealWidth: 460, idealHeight: 420)
                            .presentationDetents([.medium, .large])
                    }
            }
        case .note:
            NoteLine(message: message)
        case .event:
            EventLine(message: message)
        case .choices:
            ChoiceCardView(message: message, botID: botID, width: Theme.onMac ? Theme.bubbleMax : .infinity,
                           gap: Self.gap)
        case .login:
            SignInAskCard(message: message, botID: botID, gap: Self.gap)
        case .account:
            AccountAskCard(message: message, botID: botID, gap: Self.gap)
        case .macAsk:
            MacAskCard(message: message, botID: botID, gap: Self.gap)
        case .team:
            TeamMessage(message: message, size: Self.size, hPad: Self.hPad, vPad: Self.vPad, radius: Self.radius,
                        gap: Self.gap, botID: botID)
        case .workLog:
            WorkLogRow(message: message)
        case .step:
            // Steps are folded into a work log (ChatTimeline); one on its own still shows as a quiet row.
            WorkLogRow(message: {
                var m = message
                m.steps = [WorkStep.from(title: message.label, text: message.text)]
                m.text = WorkLog.header(nil)
                return m
            }())
        case .image:
            HStack {
                SharedPictureView(message: message)
                Spacer(minLength: 60)
            }
        case .status:
            Text(message.text).font(Theme.text(14)).foregroundStyle(Theme.inkSoft)
        case .error:
            Text(message.text).font(Theme.text(15)).foregroundStyle(Theme.danger)
                .padding(.horizontal, 16).padding(.vertical, 10)
                .background(Theme.danger.opacity(0.08), in: RoundedRectangle(cornerRadius: 18, style: .continuous))
        }
    }

    // MARK: Calls

    /// A call in the chat: a hang-up symbol on red, "Voice Call" and how long it lasted, in the bot's color.
    private var callBubble: some View {
        let color = state.bot(message.botID ?? botID).map { Color.deepForWhite($0.color) } ?? Theme.taffyDeep
        return HStack(spacing: 10) {
            Image(systemName: "phone.down.fill")
                .font(.system(size: 13, weight: .semibold))
                .foregroundStyle(.white)
                .frame(width: 30, height: 30)
                .background(Theme.danger, in: Circle())
            VStack(alignment: .leading, spacing: 1) {
                Text("Voice Call").font(Theme.text(Self.size - 1, .semibold))
                Text(message.label ?? "").font(Theme.text(13)).monospacedDigit().opacity(0.85)
            }
            .foregroundStyle(.white)
        }
        .padding(.leading, 8).padding(.trailing, 16).padding(.vertical, 8)
        .background(color, in: RoundedRectangle(cornerRadius: Self.radius, style: .continuous))
    }

    private var callSummary: some View {
        let bot = state.bot(message.botID ?? botID)
        return VStack(alignment: .leading, spacing: 0) {
            HStack(alignment: .top, spacing: 12) {
                BotAvatar(bot: bot, size: 40)
                VStack(alignment: .leading, spacing: 2) {
                    Text("Call with \(bot?.name ?? "Zero")").font(Theme.headline).foregroundStyle(Theme.ink)
                    Text([message.date?.formatted(date: .abbreviated, time: .shortened), message.label]
                            .compactMap { $0 }.joined(separator: " · "))
                        .font(Theme.secondary).foregroundStyle(Theme.inkSoft)
                }
                Spacer()
                Button { showCall = false } label: {
                    Image(systemName: "xmark")
                        .font(.system(size: 13, weight: .semibold))
                        .foregroundStyle(Theme.inkSoft)
                        .frame(width: 30, height: 30)
                        .background(Theme.bubble, in: Circle())
                }
                .buttonStyle(.plain)
                .keyboardShortcut(.cancelAction)
                .accessibilityLabel("Close")
            }
            .padding(20)
            ScrollView {
                VStack(alignment: .leading, spacing: 18) {
                    Text(markdown(message.text))
                        .font(Theme.text(16))
                        .lineSpacing(4)
                        .foregroundStyle(Theme.ink)
                        .textSelection(.enabled)
                    // The bot's own answers during the call, word for word, since the summary can leave things out.
                    if !message.details.isEmpty {
                        DisclosureGroup("What \(state.bot(message.botID ?? botID)?.name ?? "Zero") found on this call") {
                            VStack(alignment: .leading, spacing: 12) {
                                ForEach(Array(message.details.enumerated()), id: \.offset) { _, found in
                                    Text(markdown(found)).font(Theme.text(15)).foregroundStyle(Theme.ink)
                                        .textSelection(.enabled)
                                        .frame(maxWidth: .infinity, alignment: .leading)
                                }
                            }
                            .padding(.top, 8)
                        }
                        .font(Theme.text(15, .medium)).tint(Theme.inkSoft)
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(.horizontal, 20)
                .padding(.bottom, 20)
            }
        }
        .background(Theme.canvas)
    }

    private func markdown(_ raw: String) -> AttributedString { ChatText.render(raw) }

    /// A check-in saying a card closed with no answer, and it is the newest thing in the chat: what the card asked.
    private var expiredCard: String? {
        guard message.kind == .checkin, message.text.hasPrefix("No answer in time"),
              state.chats[botID]?.last(where: { $0.kind != .event && $0.kind != .status })?.id == message.id else { return nil }
        let what = (message.label ?? "").trimmingCharacters(in: .whitespaces)
        return what.isEmpty ? "the card that closed" : what
    }
}

/// The conversation as drawn (Oct 10, card answers): a choice card he answered shows his answer itself, so the message
/// that carried that answer to the Zip is drawn only there, never again under the card. The pairing is
/// CardAnswers.pairs (Model/ChatSync.swift), checked in ios/LogicTests.
struct ChatTimeline {
    /// What the chat draws, in order.
    let messages: [ChatMessage]
    /// Answered card id to the message of his that carried the answer. Its tapbacks and replies are the answer
    /// bubble's.
    let answers: [String: ChatMessage]

    init(_ all: [ChatMessage]) {
        let all = Self.foldingWork(all)
        let lines = all.map { m in
            CardAnswers.Line(id: m.id, mine: m.kind == .colin, text: m.text, date: m.date,
                             answer: m.kind == .choices ? m.answer.map {
                                 CardAnswers.Answer(text: $0.text, sent: $0.sent, at: $0.at, loose: $0.loose)
                             } : nil)
        }
        let pairs = CardAnswers.pairs(lines)
        guard !pairs.isEmpty else { messages = all; answers = [:]; return }
        let byID = Dictionary(all.map { ($0.id, $0) }, uniquingKeysWith: { first, _ in first })
        let hidden = Set(pairs.values)
        messages = all.filter { !hidden.contains($0.id) }
        answers = pairs.compactMapValues { byID[$0] }
    }
}

extension ChatTimeline {
    /// The steps a Zip took to answer a teammate, folded into one work log row between the question and the answer
    /// (WorkLog.fold). Repeated steps (a sign-in and its "Done" line) show once.
    static func foldingWork(_ all: [ChatMessage]) -> [ChatMessage] {
        guard all.contains(where: { $0.kind == .step || $0.team?.msg != nil }) else { return all }
        let items = all.map { m in
            WorkLog.Item(id: m.id, date: m.date, stepOf: m.kind == .step ? (m.stepOf ?? -1) : nil,
                         teamMsg: m.team?.msg, teamKind: m.team?.kind)
        }
        let byID = Dictionary(all.map { ($0.id, $0) }, uniquingKeysWith: { first, _ in first })
        return WorkLog.fold(items).compactMap { entry -> ChatMessage? in
            switch entry {
            case .keep(let id): return byID[id]
            case .log(let id, let stepIDs, let seconds):
                var rows: [WorkStep] = []
                for sid in stepIDs {
                    guard let m = byID[sid] else { continue }
                    var row = WorkStep.from(title: m.label, text: m.text)
                    if let plain = m.plainLabel?.trimmingCharacters(in: .whitespaces), !plain.isEmpty, plain != row.label {
                        row.raw = row.label
                        row.label = plain
                    }
                    if !rows.contains(where: { $0.label.lowercased() == row.label.lowercased() }) { rows.append(row) }
                }
                let date = stepIDs.last.flatMap { byID[$0]?.date }
                var m = ChatMessage(id: id, kind: .workLog, text: WorkLog.header(seconds), date: date,
                                    botID: stepIDs.first.flatMap { byID[$0]?.botID })
                m.steps = rows
                m.workedFor = seconds
                return m
            }
        }
    }
}

private struct CardAnswersKey: EnvironmentKey { static let defaultValue: [String: ChatMessage] = [:] }

extension EnvironmentValues {
    /// Answered card id to the message of his that carried the answer (ChatTimeline).
    var cardAnswers: [String: ChatMessage] {
        get { self[CardAnswersKey.self] }
        set { self[CardAnswersKey.self] = newValue }
    }
}

/// Colin's own bubble on the right, in his message color. A choice card he answered shows its answer in one of these
/// (CardAnswerView), whose background and words glide there from the option he tapped (`match`).
struct MineBubble: View {
    @Environment(AppState.self) private var state
    let message: ChatMessage
    let botID: String
    /// The matched-geometry ids and space the tapped option shares with this bubble, while a card turns into it.
    var match: CardMatch? = nil
    var extra: BubbleAction? = nil

    var body: some View {
        HStack {
            Spacer(minLength: MessageRow.gap + 10)
            CapWidth(max: Theme.onMac ? Theme.bubbleMax : .infinity) {
                Text(ChatText.render(message.text, markdown: false))   // his words as typed, links tappable
                    .font(Theme.text(MessageRow.size))
                    .lineSpacing(Theme.onMac ? 3 : 0)
                    .foregroundStyle(Theme.mineText)
                    .tint(Theme.mineText)
                    .modifier(MatchedIf(match: match, part: .words))
                    .padding(.horizontal, MessageRow.hPad).padding(.vertical, MessageRow.vPad)
                    .background {
                        RoundedRectangle(cornerRadius: MessageRow.radius, style: .continuous).fill(Theme.mine)
                            .modifier(MatchedIf(match: match, part: .shape))
                    }
                    .opacity(state.queuedIDs.contains(message.id) ? 0.6 : 1)
                    .bubbleSelection()
                    .tapbackable(message, botID: botID, isMine: true, extra: extra)
            }
        }
    }
}

/// Grok Bot's draft card, for anything a bot needs Colin to approve or answer: the content in a
/// white inner box, a black primary button and a gray secondary one. The note field makes the
/// card editable, which Grok Bot reviewers asked for.
struct ApprovalCard: View {
    @Environment(AppState.self) private var state
    let task: TaskItem
    /// In a chat the card leaves a gutter like a bot bubble; on the task screen it fills the width.
    var inChat = true
    @State private var note = ""
    @State private var busy = false
    @State private var editing = false
    @State private var savingLogin = false
    @State private var signingIn = false
    @State private var actionError: String?

    var body: some View {
        if let site = task.signInURL { signInCard(site) } else { decisionCard }
    }

    @ViewBuilder private var errorLine: some View {
        if let actionError {
            Text(actionError).font(Theme.caption).foregroundStyle(Theme.danger)
        }
    }

    /// A bot needs a login: save it to the bot's vault, or sign in on its computer yourself.
    private func signInCard(_ site: URL) -> some View {
        let bot = state.bot(task.assignee ?? "default")
        return HStack {
            VStack(alignment: .leading, spacing: 12) {
                HStack(spacing: 6) {
                    BotAvatar(bot: bot, size: 18)
                    Text("\(bot?.name ?? "A Zip") needs a login").font(Theme.text(15, .medium)).foregroundStyle(Theme.ink)
                }
                HStack(spacing: 12) {
                    Image(systemName: "key.fill")
                        .font(.system(size: 18, weight: .medium)).foregroundStyle(Theme.ink)
                        .frame(width: 40, height: 40).background(Theme.field, in: RoundedRectangle(cornerRadius: 10, style: .continuous))
                    VStack(alignment: .leading, spacing: 2) {
                        Text(site.host() ?? site.absoluteString).font(Theme.text(16, .semibold)).foregroundStyle(Theme.ink)
                        Text("Once saved, \(bot?.name ?? "the Zip") signs in by itself. It never sees your password.")
                            .font(Theme.text(14)).foregroundStyle(Theme.inkSoft)
                    }
                }
                .padding(14)
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(Theme.canvas, in: RoundedRectangle(cornerRadius: 14, style: .continuous))
                HStack(spacing: 10) {
                    primary("Save Login") { savingLogin = true }
                    secondary("Sign In Myself") { signingIn = true }
                }
                errorLine
            }
            .padding(14)
            .background(Theme.bubble, in: RoundedRectangle(cornerRadius: 24, style: .continuous))
            if inChat { Spacer(minLength: 48) }
        }
        .sheet(isPresented: $savingLogin) {
            SaveLoginSheet(siteURL: site.absoluteString, botID: task.assignee ?? "default", taskID: task.id)
                .environment(state)
        }
        .fullScreenCover(isPresented: $signingIn) {
            SignInScreen(botID: task.assignee ?? "default", site: site, taskID: task.id)
                .environment(state)
        }
    }

    private var decisionCard: some View {
        HStack {
            VStack(alignment: .leading, spacing: 12) {
                HStack(spacing: 6) {
                    BotAvatar(bot: state.bot(task.assignee ?? "default"), size: 18)
                    Text(task.status == "blocked" ? "Needs your answer" : "Ready for your review")
                        .font(Theme.text(15, .medium)).foregroundStyle(Theme.ink)
                }
                VStack(alignment: .leading, spacing: 8) {
                    Text(task.title).font(Theme.text(16, .semibold)).foregroundStyle(Theme.ink)
                    Text(task.status == "blocked" ? (task.block_reason ?? "") : (task.summary ?? ""))
                        .font(Theme.text(16)).foregroundStyle(Theme.ink).textSelection(.enabled)
                    if editing || task.status == "blocked" {
                        TextField(task.status == "blocked" ? "Your answer" : "What should change?", text: $note, axis: .vertical)
                            .lineLimit(1...5)
                            .font(Theme.text(16))
                            .padding(10)
                            .background(Theme.field, in: RoundedRectangle(cornerRadius: 12, style: .continuous))
                    }
                }
                .padding(14)
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(Theme.canvas, in: RoundedRectangle(cornerRadius: 14, style: .continuous))
                HStack(spacing: 10) {
                    if task.status == "blocked" {
                        primary("Send Answer", disabled: note.isEmpty) { await run("answer") }
                        secondary("I'll Do It") { await run("done") }
                    } else if editing {
                        primary("Send Changes", disabled: note.isEmpty) { await run("changes") }
                        secondary("Cancel") { editing = false }
                    } else {
                        primary("Approve") { await run("approve") }
                        secondary("Edit") { editing = true }
                    }
                }
                errorLine
            }
            .padding(14)
            .background(Theme.bubble, in: RoundedRectangle(cornerRadius: 24, style: .continuous))
            if inChat { Spacer(minLength: 48) }
        }
    }

    private func primary(_ title: String, disabled: Bool = false, _ action: @escaping () async -> Void) -> some View {
        Button { Task { busy = true; await action(); busy = false } } label: {
            Text(title).font(Theme.text(17, .medium)).foregroundStyle(disabled ? Theme.inkSoft : Theme.mineText)
                .frame(maxWidth: .infinity, minHeight: 48)
                .background(disabled ? Theme.field : Theme.mine, in: RoundedRectangle(cornerRadius: 14, style: .continuous))
        }
        .buttonStyle(PressStyle())
        .disabled(busy || disabled || state.offline)
    }

    private func secondary(_ title: String, _ action: @escaping () async -> Void) -> some View {
        Button { Task { busy = true; await action(); busy = false } } label: {
            Text(title).font(Theme.text(17, .medium)).foregroundStyle(Theme.ink)
                .frame(maxWidth: .infinity, minHeight: 48)
                .background(Theme.canvas, in: RoundedRectangle(cornerRadius: 14, style: .continuous))
                .overlay(RoundedRectangle(cornerRadius: 14, style: .continuous).stroke(Theme.rule, lineWidth: 1))
        }
        .buttonStyle(PressStyle())
        .disabled(busy || state.offline)
    }

    private func run(_ action: String) async {
        actionError = await state.act(on: task, action, note: note.isEmpty ? nil : note)
    }
}

/// What the bot is doing right now: its candy, working, beside the live status line.
/// The bot is working: a gray bubble with three pulsing dots, and what it is doing underneath.
struct TypingRow: View {
    let text: String
    var bot: Bot?

    private var detail: String? {
        ["Thinking", "Working", ""].contains(text) ? nil : text
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            TypingDots(dot: 8)
                .padding(.horizontal, 16).frame(height: 40)
                .background(Theme.bubble, in: RoundedRectangle(cornerRadius: 20, style: .continuous))
            if let detail {
                Text(detail).font(Theme.caption).foregroundStyle(Theme.inkSoft)
                    .lineLimit(2)
                    .padding(.leading, 4)
                    .contentTransition(.opacity)
                    .animation(.easeOut(duration: 0.2), value: detail)
            }
        }
        .accessibilityElement(children: .combine)
        .accessibilityLabel("\(bot?.name ?? "The Zip") is working. \(detail ?? "")")
    }
}

/// Dictation into the composer. You read and edit before sending. On the iPhone it is Apple's speech
/// recognition; on the Mac the button starts and stops Wispr Flow's hands-free mode, which types into the
/// message box itself.
@Observable
final class Dictation {
    var active = false
    private var viaFlow = false
    var text = ""
    /// Why dictation couldn't start, for an alert; nil when fine.
    var problem: String?
    var problemNeedsSettings = false
    private let engine = AVAudioEngine()
    private let recognizer = SFSpeechRecognizer(locale: Locale(identifier: "en-US"))
    private var request: SFSpeechAudioBufferRecognitionRequest?
    private var task: SFSpeechRecognitionTask?

    private var starting = false
    private var prefix = ""

    /// Starts dictation that adds to whatever is already typed, or stops it.
    @MainActor
    func toggle(appendingTo draft: String = "") async {
        if active { stop(); return }
        guard !starting else { return }
        starting = true
        defer { starting = false }
        if ProcessInfo.processInfo.isMacCatalystApp {
            if let error = await MacLink.shared.wisprFlow(start: true) {
                problem = error
                problemNeedsSettings = false
                return
            }
            viaFlow = true
            active = true
            return
        }
        let ok = await withCheckedContinuation { c in SFSpeechRecognizer.requestAuthorization { c.resume(returning: $0 == .authorized) } }
        guard ok, await AVAudioApplication.requestRecordPermission() else {
            problem = "Turn on Speech Recognition and the Microphone for Zero in Settings to dictate."
            problemNeedsSettings = true
            return
        }
        guard let recognizer, recognizer.isAvailable else {
            problem = "Dictation isn't available right now. Try again in a moment."
            problemNeedsSettings = false
            return
        }
        try? AVAudioSession.sharedInstance().setCategory(.record, mode: .measurement, options: .duckOthers)
        try? AVAudioSession.sharedInstance().setActive(true)
        let req = SFSpeechAudioBufferRecognitionRequest()
        req.shouldReportPartialResults = true
        if recognizer.supportsOnDeviceRecognition { req.requiresOnDeviceRecognition = true }
        request = req
        let input = engine.inputNode
        input.removeTap(onBus: 0)
        input.installTap(onBus: 0, bufferSize: 1024, format: input.outputFormat(forBus: 0)) { [weak self] b, _ in
            self?.request?.append(b)
        }
        engine.prepare()
        guard (try? engine.start()) != nil else {
            // Leave nothing running: no tap, no active session ducking other audio.
            input.removeTap(onBus: 0)
            request = nil
            try? AVAudioSession.sharedInstance().setActive(false, options: .notifyOthersOnDeactivation)
            problem = "Dictation didn't start. Try again."
            problemNeedsSettings = false
            return
        }
        let base = draft.trimmingCharacters(in: .whitespacesAndNewlines)
        prefix = base.isEmpty ? "" : base + " "
        text = draft
        active = true
        task = recognizer.recognitionTask(with: req) { [weak self] result, error in
            if let result {
                let said = result.bestTranscription.formattedString
                DispatchQueue.main.async { guard let self, self.active else { return }; self.text = self.prefix + said }
            }
            if error != nil || result?.isFinal == true { DispatchQueue.main.async { self?.stop() } }
        }
    }

    func stop() {
        guard active else { return }
        active = false
        if viaFlow {
            viaFlow = false
            Task { @MainActor in _ = await MacLink.shared.wisprFlow(start: false) }
            return
        }
        task?.finish()
        request?.endAudio()
        if engine.isRunning { engine.stop() }
        engine.inputNode.removeTap(onBus: 0)
        try? AVAudioSession.sharedInstance().setActive(false, options: .notifyOthersOnDeactivation)
    }
}

/// Who a bot is and what it can reach, opened by tapping its name.
struct BotProfile: View {
    @Environment(AppState.self) private var state
    let botID: String
    var onComputer: () -> Void
    var onCall: () -> Void
    var onRemoved: () -> Void = {}
    @State private var snoozing: Bot?
    @State private var deleting: Bot?
    @State private var renaming = false
    @State private var changingLook = false
    @State private var showingRules = false
    @State private var newName = ""
    @State private var renameError: String?

    var body: some View {
        let bot = state.bot(botID)
        ScrollView {
        VStack(spacing: 14) {
            BotAvatar(bot: bot, size: 88)
                .padding(.top, 28)
            Text(bot?.name ?? "Zero").font(Theme.title).foregroundStyle(Theme.ink)
            Text(bot?.role ?? "").font(Theme.secondary).foregroundStyle(Theme.inkSoft)
                .multilineTextAlignment(.center).padding(.horizontal, 32)
            HStack(spacing: 12) {
                tile("macwindow", "Computer", action: onComputer)
                tile("phone", "Call", action: onCall)
                tile("moon.zzz", bot?.snoozedUntil == nil ? "Snooze" : "Snoozed") { snoozing = bot }
                tile(state.mutedBots.contains(botID) ? "bell.slash" : "bell",
                     state.mutedBots.contains(botID) ? "Unmute" : "Mute") {
                    state.setMuted(botID, !state.mutedBots.contains(botID))
                }
            }
            .padding(.horizontal, 20)
            .padding(.top, 8)
            VStack(alignment: .leading, spacing: 6) {
                Toggle("Notify Me When It Finishes or Needs Me", isOn: Binding(
                    get: { bot?.notifies ?? true }, set: { state.setNotify(botID, $0) }))
                    .font(Theme.body)
                    .tint(Theme.taffyDeep)
                Text("With this off, \((bot?.name ?? "this Zip").possessive) finished work and requests wait in the app without a notification.")
                    .font(Theme.caption).foregroundStyle(Theme.inkSoft)
                if bot?.notifies ?? true {
                    NotifyKindsSection(botID: botID).padding(.top, 6)
                    MessageToneRow(botID: botID).padding(.top, 4)
                }
            }
            .padding(.horizontal, 24)
            .padding(.top, 8)
            HStack(spacing: 28) {
                Button("Change Look") { changingLook = true }
                Button("Rename") {
                    newName = bot?.name ?? ""
                    renaming = true
                }
                if !state.demoMode {
                    Button("Custom Rules") { showingRules = true }
                }
            }
            .font(Theme.secondary.weight(.medium))
            .foregroundStyle(Theme.ink)
            .frame(minHeight: 44)
            .padding(.top, 8)
            if let bot, !bot.isLead {
                Button("Delete \(bot.name)", role: .destructive) { deleting = bot }
                    .font(Theme.secondary.weight(.medium))
                    .frame(minHeight: 44)
                    .padding(.top, 8)
            }
        }
        .frame(maxWidth: .infinity)
        .padding(.bottom, 20)
        }
        .background(Theme.canvas)
        .botActions(snoozing: $snoozing, deleting: $deleting, onDeleted: onRemoved)
        .sheet(isPresented: $changingLook) {
            if let bot { LookEditor(bot: bot).environment(state) }
        }
        .sheet(isPresented: $showingRules) {
            NavigationStack {
                CustomRulesView(botID: botID)
                    .toolbar {
                        ToolbarItem(placement: .confirmationAction) { Button("Done") { showingRules = false } }
                    }
            }
            .environment(state)
        }
        .alert("Rename \(bot?.name ?? "Zip")", isPresented: $renaming) {
            TextField("Name", text: $newName)
                .textInputAutocapitalization(.words)
            Button("Save") {
                let name = newName.trimmingCharacters(in: .whitespacesAndNewlines)
                guard !name.isEmpty else { return }
                Task { renameError = await state.renameBot(botID, to: name) }
            }
            Button("Cancel", role: .cancel) {}
        } message: {
            Text("The Zip will use its new name from its next reply.")
        }
        .alert("That Did Not Work", isPresented: Binding(get: { renameError != nil }, set: { if !$0 { renameError = nil } })) {
            Button("OK", role: .cancel) {}
        } message: {
            Text(renameError ?? "")
        }
    }

    private func tile(_ symbol: String, _ label: String, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            VStack(spacing: 8) {
                Image(systemName: symbol).font(.system(size: 20, weight: .medium))
                Text(label).font(Theme.caption.weight(.medium))
            }
            .foregroundStyle(Theme.ink)
            .frame(maxWidth: .infinity, minHeight: 76)
            .background(Theme.bubble, in: RoundedRectangle(cornerRadius: 14, style: .continuous))
        }
        .buttonStyle(PressStyle())
    }
}

/// A bot asking to sign in to a site. Allow Once covers what it is doing now; Always Allow sets the
/// site to Anytime for that bot; Not Now tells it to carry on without the site.
struct SignInRequestCard: View {
    @Environment(AppState.self) private var state
    let request: SignInRequest
    var inChat = true
    @State private var busy = false
    @State private var error: String?
    @State private var confirmAlways = false

    private var bot: Bot? { state.bot(request.bot_id) }
    private var why: String {
        if let w = request.why, !w.isEmpty { return w }
        return request.reason == "Scheduled or background work" || request.reason.isEmpty ? "scheduled" : ""
    }

    /// What the card says about the request, in Colin's terms.
    private var explanation: String {
        let asked = request.reason.trimmingCharacters(in: .whitespacesAndNewlines)
        switch why {
        case "mismatch":
            return "This doesn't look needed for what you asked: \u{201C}\(asked)\u{201D}"
        case "scheduled":
            if let o = request.origin, !o.isEmpty { return o }
            return "This came up during scheduled work, not something you asked for."
        case "background":
            // Nothing Colin said is behind this card, so no words are quoted as if they were his or the bot's. The
            // hub says where it came from (Oct 8: "working on its own" read as if the Zip had started it by itself).
            if let o = request.origin, !o.isEmpty { return o }
            return "This came up outside your chat with \(bot?.name ?? "the Zip")."
        case "ask":
            return asked.isEmpty ? "You set this site to ask every time." : "You set this site to ask every time. You asked: \(asked)"
        case "content":
            // Only say he did not ask when no request of his is behind the card (Oct 8 QA round 2, H1).
            let name = bot?.name ?? "the Zip"
            return asked.isEmpty
                ? "This came up after \(name) read something someone else wrote, not from something you asked."
                : "You asked: \u{201C}\(asked)\u{201D} \(name) had just read something someone else wrote, so check this is what you meant."
        case "sensitive":
            let base = "This site involves money, health or accounts, so I ask first."
            return asked.isEmpty ? base : "\(base) You asked: \(asked)"
        default:
            return "You asked: \(asked)"
        }
    }

    /// Always Allow lets the bot sign in at any time, scheduled work included, so it is not offered for
    /// sensitive sites or for requests that don't match what Colin asked, and it asks once more otherwise.
    private var offerAlways: Bool { request.sensitive != true && why != "mismatch" }

    var body: some View {
        if request.isAction { actionBody } else { signInBody }
    }

    /// The app a card's action goes through, for its small icon: Gmail, Google Calendar, Google Drive or Trello.
    static func appIcon(for site: String?) -> String? {
        switch (site ?? "").lowercased() {
        case "gmail.com", "mail.google.com": return "AppGmail"
        case "calendar.google.com": return "AppGoogleCalendar"
        case "drive.google.com", "docs.google.com": return "AppGoogleDrive"
        case "trello.com": return "TablerBrandTrello"
        default: return nil
        }
    }

    static func appName(for site: String?) -> String? {
        switch (site ?? "").lowercased() {
        case "gmail.com", "mail.google.com": return "Gmail"
        case "calendar.google.com": return "Google Calendar"
        case "drive.google.com", "docs.google.com": return "Google Drive"
        case "trello.com": return "Trello"
        default: return nil
        }
    }

    /// What the Zip wants to do, in Colin's words, for the card's title.
    private var actionTitle: String {
        let name = bot?.name ?? "A Zip"
        switch request.kind {
        case "send": return "\(name) wants to send this"
        case "publish": return "\(name) wants to publish this"
        case "delete": return "\(name) wants to delete this"
        case "submit": return "\(name) wants to submit this"
        case "book": return "\(name) wants to book this"
        case "account": return "\(name) wants to change an account"
        case "command": return "\(name) wants to run a command"
        case "system": return "\(name) wants to change the computer"
        case "schedule": return "\(name) wants to schedule a job"
        default: return "\(name) wants to make this change"
        }
    }

    /// How long the Zip waits on a command card before it stops and waits for Colin (Hermes's own limit).
    private static let commandWait: TimeInterval = 5 * 60

    private func actionWhy(at now: Date) -> String {
        if request.kind == "command" {
            // The Zip is paused on this command right now (hub approvals.py). After five minutes Hermes tells it to
            // stop and wait, and a later answer picks the chat back up (round 4, L5). Once that time has passed the
            // card stops promising five minutes (round 5, L3).
            let name = bot?.name ?? "The Zip"
            if now.timeIntervalSince1970 - request.created_at >= Self.commandWait {
                return "\(name) stopped and is waiting for you. If you allow it, it picks this back up."
            }
            return "\(name) is paused on this. If you don't answer in five minutes, it stops and tells you."
        }
        if let rule = request.paused_by, !rule.isEmpty {
            // Something the Zip would normally just do, paused because Colin asked to be asked (Custom Rules).
            return "Paused by your Ask First rule: \(rule)"
        }
        switch why {
        case "content": return "This came right after \(bot?.name ?? "the Zip") read something someone else wrote. Check it is what you want before you allow it."
        case "uncovered": return "This came up during scheduled work, and the job's own instructions don't clearly cover it. If you allow it once, the job does it on its next run."
        case "scheduled": return request.origin.flatMap { $0.isEmpty ? nil : $0 }
            ?? "This came up during scheduled work, not something you asked for."
        case "background": return request.origin.flatMap { $0.isEmpty ? nil : $0 }
            ?? "This came up outside your chat with \(bot?.name ?? "the Zip")."
        default: return "Nothing goes out until you allow it."
        }
    }

    /// A send, publish, delete or other change that needs Colin's yes: exactly what will happen, then Allow Once
    /// (Send for messages), Not Now, and Always Allow only where a standing rule makes sense.
    private var actionBody: some View {
        HStack {
            VStack(alignment: .leading, spacing: 12) {
                HStack(spacing: 6) {
                    BotAvatar(bot: bot, size: 18)
                    Text(actionTitle).font(Theme.text(15, .medium)).foregroundStyle(Theme.ink)
                    Spacer(minLength: 4)
                    // The app it will use, small, level with the Zip (Colin, Oct 10).
                    if let icon = Self.appIcon(for: request.site) {
                        Image(icon).resizable().scaledToFit().frame(width: 18, height: 18)
                            .accessibilityLabel(Self.appName(for: request.site) ?? "")
                    }
                }
                VStack(alignment: .leading, spacing: 8) {
                    Text(request.reason.prefix(1).uppercased() + request.reason.dropFirst())
                        .font(Theme.text(16, .semibold)).foregroundStyle(Theme.ink)
                    if let job = request.routine_name, !job.isEmpty {
                        Text("From your scheduled job \(job)").font(Theme.caption).foregroundStyle(Theme.inkSoft)
                    }
                    if let target = request.target, !target.isEmpty, target != request.site {
                        Text(request.kind == "send" ? "To: \(target)" : target).font(Theme.secondary).foregroundStyle(Theme.ink)
                    }
                    if let inputs = request.inputs, !inputs.isEmpty {
                        // Sized to the text, scrolling only when it is long (Oct 8 QA round 3, L6: a one-line
                        // request sat in a tall empty box).
                        let text = Text(inputs).font(Theme.secondary).foregroundStyle(Theme.ink)
                            .textSelection(.enabled)
                            .frame(maxWidth: .infinity, alignment: .leading)
                        ViewThatFits(in: .vertical) {
                            text.fixedSize(horizontal: false, vertical: true)
                            ScrollView { text }
                        }
                        .frame(maxHeight: 220)
                        .padding(10)
                        .background(Theme.field, in: RoundedRectangle(cornerRadius: 10, style: .continuous))
                    }
                    if let details = request.details, !details.isEmpty {
                        // The exact command, for anyone who wants it; the card leads with what it does (L1).
                        DisclosureGroup("Details") {
                            ScrollView {
                                Text(details).font(.system(size: 13, design: .monospaced)).foregroundStyle(Theme.ink)
                                    .textSelection(.enabled)
                                    .frame(maxWidth: .infinity, alignment: .leading)
                            }
                            .frame(maxHeight: 180)
                        }
                        .font(Theme.caption.weight(.medium)).foregroundStyle(Theme.inkSoft)
                    }
                    if let excerpt = request.excerpt, !excerpt.isEmpty {
                        Text("What it had just read:").font(Theme.caption.weight(.medium)).foregroundStyle(Theme.inkSoft)
                        Text(excerpt).font(Theme.caption).foregroundStyle(Theme.inkSoft).lineLimit(8).textSelection(.enabled)
                    }
                    Label {
                        // Redrawn every half minute, so a command card's wording changes when the wait is over.
                        TimelineView(.periodic(from: .now, by: 30)) { context in
                            Text(actionWhy(at: context.date))
                        }
                    } icon: {
                        if why == "content" { Image(systemName: "exclamationmark.triangle").foregroundStyle(Theme.taffyDeep) }
                        else if request.paused_by?.isEmpty == false { Image(systemName: "hand.raised").foregroundStyle(Theme.ink) }
                    }
                    .font(Theme.secondary).foregroundStyle(why == "content" ? Theme.ink : Theme.inkSoft)
                    .fixedSize(horizontal: false, vertical: true)
                }
                .padding(14)
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(Theme.canvas, in: RoundedRectangle(cornerRadius: 14, style: .continuous))
                if alwaysFirst {
                    // A scheduled job's change: the useful answer is usually a standing yes for that job (M4).
                    button("Always Allow This Job", primary: true) { confirmAlways = true }
                        .confirmationDialog("Always allow \(bot?.name ?? "this Zip") to make changes like this in \(request.routine_name ?? "this job")?",
                                            isPresented: $confirmAlways, titleVisibility: .visible) {
                            Button("Confirm") { Task { await decide("always") } }
                            Button("I Changed My Mind", role: .cancel) {}
                        } message: {
                            Text("From now on \(bot?.name ?? "this Zip") can make changes like this during \(request.routine_name ?? "this job") without checking with you first. You can undo this in Settings, Custom Rules.")
                        }
                }
                HStack(spacing: 10) {
                    button(request.kind == "send" ? "Send" : "Allow Once", primary: !alwaysFirst) { await decide("once") }
                    button("Not Now", primary: false) { await decide("deny") }
                }
                if request.can_always == true, let scope = request.scope, !scope.isEmpty, !alwaysFirst {
                    // The same button as Not Now, so it reads as the third choice (Colin, Oct 10).
                    button(request.kind == "send" ? "Always Allow This Address" : "Always Allow This", primary: false) { confirmAlways = true }
                    .confirmationDialog(request.kind == "send" ? "Always allow \(bot?.name ?? "this Zip") to send to \(Self.shortScope(scope))?"
                                                               : "Always allow \(bot?.name ?? "this Zip") to do this?",
                                        isPresented: $confirmAlways, titleVisibility: .visible) {
                        Button("Confirm") { Task { await decide("always") } }
                        Button("I Changed My Mind", role: .cancel) {}
                    } message: {
                        Text(request.kind == "send"
                             ? "From now on \(bot?.name ?? "this Zip") can send messages to \(Self.shortScope(scope)) without checking with you first. Sending to anyone else still asks. You can undo this in Settings, Custom Rules."
                             : "From now on \(bot?.name ?? "this Zip") can do this without checking with you first. Anything different still asks. You can undo this in Settings, Custom Rules.")
                    }
                }
                if let error { Text(error).font(Theme.caption).foregroundStyle(Theme.danger) }
            }
            .padding(14)
            .background(Theme.bubble, in: RoundedRectangle(cornerRadius: 24, style: .continuous))
            if inChat { Spacer(minLength: 48) }
        }
    }

    private var alwaysFirst: Bool {
        why == "uncovered" && request.can_always == true && (request.scope ?? "").hasPrefix("routine:")
    }

    static func shortScope(_ scope: String) -> String {
        scope.hasPrefix("routine:") ? "This Job" : scope
    }

    private var signInBody: some View {
        HStack {
            VStack(alignment: .leading, spacing: 12) {
                HStack(spacing: 6) {
                    BotAvatar(bot: bot, size: 18)
                    Text("\(bot?.name ?? "A Zip") wants to \(request.kind == "navigate" ? "open a site" : "sign in")")
                        .font(Theme.text(15, .medium)).foregroundStyle(Theme.ink)
                }
                HStack(alignment: .top, spacing: 12) {
                    Image(systemName: "person.badge.key")
                        .font(.system(size: 18, weight: .medium)).foregroundStyle(Theme.ink)
                        .frame(width: 40, height: 40)
                        .background(Theme.field, in: RoundedRectangle(cornerRadius: 10, style: .continuous))
                    VStack(alignment: .leading, spacing: 4) {
                        Text(request.site).font(Theme.text(16, .semibold)).foregroundStyle(Theme.ink)
                        Label {
                            Text(explanation)
                        } icon: {
                            if why == "mismatch" { Image(systemName: "exclamationmark.triangle").foregroundStyle(Theme.taffyDeep) }
                        }
                        .font(Theme.secondary).foregroundStyle(why == "mismatch" ? Theme.ink : Theme.inkSoft)
                        if request.sensitive == true && why != "sensitive" {
                            Text("This site can involve money, health or account settings, so it always asks first.")
                                .font(Theme.caption).foregroundStyle(Theme.inkSoft)
                        }
                        if let excerpt = request.excerpt, !excerpt.isEmpty {
                            Text("What it had just read: \(excerpt)").font(Theme.caption).foregroundStyle(Theme.inkSoft)
                                .lineLimit(6).textSelection(.enabled)
                        }
                    }
                }
                .padding(14)
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(Theme.canvas, in: RoundedRectangle(cornerRadius: 14, style: .continuous))
                HStack(spacing: 10) {
                    button("Allow Once", primary: true) { await decide("once") }
                    button("Not Now", primary: false) { await decide("deny") }
                }
                if offerAlways {
                    // The same button as Not Now, so it reads as the third choice (Colin, Oct 10).
                    button("Always Allow This Site", primary: false) { confirmAlways = true }
                    .confirmationDialog("Always allow \(bot?.name ?? "this Zip") to sign in to \(request.site)?",
                                        isPresented: $confirmAlways, titleVisibility: .visible) {
                        Button("Confirm") { Task { await decide("always") } }
                        Button("I Changed My Mind", role: .cancel) {}
                    } message: {
                        Text("From now on \(bot?.name ?? "this Zip") can sign in to \(request.site) at any time, including during scheduled work, without checking with you first. You can undo this in Settings.")
                    }
                }
                if let error { Text(error).font(Theme.caption).foregroundStyle(Theme.danger) }
            }
            .padding(14)
            .background(Theme.bubble, in: RoundedRectangle(cornerRadius: 24, style: .continuous))
            if inChat { Spacer(minLength: 48) }
        }
    }

    private func decide(_ d: String) async {
        busy = true
        error = await state.decide(request, d)
        busy = false
    }

    private func button(_ title: String, primary: Bool, _ action: @escaping () async -> Void) -> some View {
        Button { Task { await action() } } label: {
            Text(title).font(Theme.text(17, .medium)).foregroundStyle(primary ? Theme.mineText : Theme.ink)
                .frame(maxWidth: .infinity, minHeight: 48)
                .background(primary ? Theme.mine : Theme.canvas, in: RoundedRectangle(cornerRadius: 14, style: .continuous))
                .overlay {
                    if !primary { RoundedRectangle(cornerRadius: 14, style: .continuous).stroke(Theme.rule, lineWidth: 1) }
                }
        }
        .buttonStyle(PressStyle())
        .disabled(busy || state.offline)
    }
}

/// One line at the top of Work and each chat while the hub can't be reached; the cards' buttons wait for it
/// (Oct 8 QA, L2: offline cards showed live-looking buttons that failed on tap).
struct OfflineNote: View {
    @Environment(AppState.self) private var state

    var body: some View {
        Label(state.updatedAt.map { "Can't reach Zero right now. Showing what you had at \($0.formatted(date: .omitted, time: .shortened))." }
              ?? "Can't reach Zero right now. Trying again.", systemImage: "wifi.slash")
            .font(Theme.caption).foregroundStyle(Theme.inkSoft)
            .frame(maxWidth: .infinity)
            .padding(.vertical, 6)
    }
}

/// A picture a bot shared: its screen or an image it made, with its caption. Tap to view full screen.
struct SharedPictureView: View {
    @Environment(AppState.self) private var state
    let message: ChatMessage
    @State private var image: UIImage?
    @State private var failed = false
    @State private var viewing = false

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Button { if image != nil { viewing = true } } label: {
                ZStack {
                    Theme.bubble
                    if let image {
                        Image(uiImage: image).resizable().scaledToFit()
                    } else if failed {
                        Label("This picture couldn't load.", systemImage: "photo").font(Theme.caption).foregroundStyle(Theme.inkSoft)
                    } else {
                        ProgressView()
                    }
                }
                .frame(width: 260, height: 260 * min(max(message.aspect ?? 0.625, 0.4), 1.6))
                .clipShape(RoundedRectangle(cornerRadius: 18, style: .continuous))
            }
            .buttonStyle(PressStyle(scale: 0.98))
            .accessibilityLabel("Picture: \(message.text)")
            if !message.text.isEmpty {
                Text(message.text).font(Theme.secondary).foregroundStyle(Theme.inkSoft)
                    .frame(maxWidth: 260, alignment: .leading)
            }
        }
        .task {
            guard image == nil, let id = message.mediaID else { return }
            do { image = try await state.client.picture(id) } catch { failed = true }
        }
        .fullScreenCover(isPresented: $viewing) {
            if let image { PictureViewer(image: image, caption: message.text) }
        }
    }
}

/// Full-screen picture with pinch to zoom, double tap to zoom, and Share.
struct PictureViewer: View {
    @Environment(\.dismiss) private var dismiss
    let image: UIImage
    let caption: String
    @State private var scale: CGFloat = 1
    @State private var base: CGFloat = 1
    @State private var offset: CGSize = .zero
    @State private var dragBase: CGSize = .zero

    var body: some View {
        ZStack {
            Color.black.ignoresSafeArea()
            Image(uiImage: image).resizable().scaledToFit()
                .scaleEffect(scale)
                .offset(offset)
                .gesture(MagnifyGesture()
                    .onChanged { scale = min(max(base * $0.magnification, 1), 5) }
                    .onEnded { _ in base = scale; if scale == 1 { offset = .zero; dragBase = .zero } })
                .simultaneousGesture(DragGesture()
                    .onChanged { v in
                        if scale > 1 {
                            offset = CGSize(width: dragBase.width + v.translation.width, height: dragBase.height + v.translation.height)
                        } else if v.translation.height > 0 {
                            offset = CGSize(width: 0, height: v.translation.height)   // pull down to close, like Photos
                        }
                    }
                    .onEnded { v in
                        if scale <= 1 {
                            if v.translation.height > 120 { dismiss() } else { withAnimation(.spring(duration: 0.25)) { offset = .zero } }
                        } else { dragBase = offset }
                    })
                .onTapGesture(count: 2) {
                    withAnimation(.spring(duration: 0.3)) {
                        if scale > 1 { scale = 1; base = 1; offset = .zero; dragBase = .zero } else { scale = 2.5; base = 2.5 }
                    }
                }
        }
        .safeAreaInset(edge: .top) {
            HStack {
                CircleButton(dark: true, action: { dismiss() }) { Image(systemName: "xmark") }
                    .accessibilityLabel("Close")
                Spacer()
                ShareLink(item: Image(uiImage: image), preview: SharePreview(caption.isEmpty ? "Picture" : caption,
                                                                             image: Image(uiImage: image))) {
                    Image(systemName: "square.and.arrow.up")
                        .font(.system(size: 18, weight: .medium)).foregroundStyle(.white)
                        .frame(width: 44, height: 44)
                        .floating(Circle(), dark: true)
                }
                .accessibilityLabel("Share")
            }
            .padding(.horizontal, 20).padding(.top, 4)
        }
        .safeAreaInset(edge: .bottom) {
            if !caption.isEmpty {
                Text(caption).font(Theme.secondary).foregroundStyle(.white.opacity(0.85))
                    .multilineTextAlignment(.center).padding(.horizontal, 24).padding(.bottom, 12)
            }
        }
    }
}


extension View {
    /// On the Mac, cards in the chat stay as wide as the widest bubble instead of the whole column.
    @ViewBuilder func macBubbleWidth() -> some View {
        if Theme.onMac {
            HStack(spacing: 0) {
                CapWidth(max: Theme.bubbleMax) { self }
                Spacer(minLength: 0)
            }
            .padding(.top, 20)
        } else {
            self
        }
    }
}


/// The iPhone's top bar sits on a solid strip that fades into the chat. On the Mac the name pill floats
/// over the transcript like Grok Bot's, with the text fading out beneath it over about 60 pt (measured
/// from Grok Bot's window) so the two never collide.
private struct TopBar: ViewModifier {
    let solid: Bool
    func body(content: Content) -> some View {
        if solid {
            content.barFade(.top)
        } else {
            content.background(alignment: .top) {
                LinearGradient(stops: [.init(color: Theme.canvas, location: 0), .init(color: Theme.canvas, location: 0.35),
                                       .init(color: Theme.canvas.opacity(0), location: 1)],
                               startPoint: .top, endPoint: .bottom)
                    .frame(height: 64)
                    .allowsHitTesting(false)
            }
        }
    }
}


/// Only real bot replies take tapbacks; team updates and check-ins do not. All of them take replies.
private struct BotTapback: ViewModifier {
    @Environment(AppState.self) private var state
    let message: ChatMessage
    let botID: String
    func body(content: Content) -> some View {
        if message.kind == .bot {
            content.tapbackable(message, botID: botID, isMine: false)
        } else {
            content.replyable(message, botID: botID, isMine: false, state: state)
        }
    }
}


/// Keeps a chat on its newest message while he is reading there (Colin, Oct 10: after a card folded or a call ended,
/// coming back to the app showed a blank chat until he scrolled). Scrolling up himself (the bottom leaving view without the chat getting shorter) is the only thing that stops it.
/// No drag gesture here: one on the whole chat kept iOS's swipe-back from starting (Oct 10).
private struct FollowsBottom: ViewModifier {
    @Binding var follows: Bool
    let scenePhase: ScenePhase
    let ids: [String]
    let proxy: ScrollViewProxy

    func body(content: Content) -> some View {
        content
            .onChange(of: scenePhase) { _, phase in
                guard phase == .active, follows else { return }
                // Back in the app: the chat may have reloaded while away. Once now, once after the reload settles.
                proxy.scrollTo("bottom", anchor: .bottom)
                Task { try? await Task.sleep(for: .milliseconds(600)); proxy.scrollTo("bottom", anchor: .bottom) }
            }
            .onChange(of: ids) { _, _ in if follows { proxy.scrollTo("bottom", anchor: .bottom) } }
    }
}
