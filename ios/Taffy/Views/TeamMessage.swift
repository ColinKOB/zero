import SwiftUI

struct TeamMessage: View {
    @Environment(AppState.self) private var state
    let message: ChatMessage
    let size: CGFloat
    let hPad: CGFloat
    let vPad: CGFloat
    let radius: CGFloat
    let gap: CGFloat
    var botID: String? = nil
    @State private var expanded = false
    @State private var original = false

    private var item: TeamItem? { message.team }
    private var plain: String? {
        guard let p = item?.plain?.trimmingCharacters(in: .whitespacesAndNewlines), !p.isEmpty, p != message.text else { return nil }
        return p
    }
    private var shown: String { original ? message.text : (plain ?? message.text) }
    private var fromSide: Bool { item?.kind == "ask" || item?.kind == "tell" }
    private static let longText = 420

    var body: some View {
        if item?.kind == "note" {
            Button { withAnimation(.snappy(duration: 0.25)) { expanded.toggle() } } label: {
                VStack(alignment: .leading, spacing: 4) {
                    HStack(spacing: 6) {
                        Image(systemName: "pin.fill").font(.system(size: 10, weight: .semibold))
                        Text("\(state.bot(message.botID ?? "")?.name ?? "A Zip") saved a note")
                        Image(systemName: expanded ? "chevron.up" : "chevron.down").font(.system(size: 9, weight: .semibold))
                    }
                    if expanded {
                        Text(message.text).multilineTextAlignment(.leading).bubbleSelection()
                    }
                }
                .font(Theme.text(13)).foregroundStyle(Theme.inkSoft)
                .frame(maxWidth: .infinity, alignment: expanded ? .leading : .center)
                .padding(.horizontal, 4)
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .accessibilityHint(expanded ? "Hides the note" : "Shows the note")
        } else {
            HStack {
                if fromSide { Spacer(minLength: gap + 10) }
                CapWidth(max: Theme.onMac ? Theme.bubbleMax : .infinity) {
                    VStack(alignment: fromSide ? .trailing : .leading, spacing: 5) {
                        caption
                        if let botID {
                            box.replyable(message, botID: botID, isMine: fromSide, state: state)
                        } else {
                            box
                        }
                    }
                }
                if !fromSide { Spacer(minLength: gap) }
            }
        }
    }

    private var caption: some View {
        let who = state.bot(item?.kind == "answer" ? (item?.to ?? "") : (item?.from ?? ""))
        return HStack(spacing: 6) {
            if let who { BotAvatar(bot: who, size: 16) }
            Text(message.label ?? "Team message")
                .font(Theme.text(13, .medium)).foregroundStyle(Theme.inkSoft)
        }
        .padding(fromSide ? .trailing : .leading, 4)
        .accessibilityElement(children: .combine)
    }

    private var box: some View {
        let sender = state.bot(item?.from ?? "")
        let tint = sender.map { Color(hex: $0.color) } ?? Theme.taffy
        let isAnswer = item?.kind == "answer"
        let long = shown.count > Self.longText
        return VStack(alignment: .leading, spacing: 6) {
            if let q = item?.question, !q.isEmpty {
                Text("Asked: \u{201C}\(q)\u{201D}")
                    .font(Theme.text(13)).foregroundStyle(Theme.inkSoft).lineLimit(2)
            }
            Text(markdown(shown))
                .font(Theme.text(size))
                .lineSpacing(Theme.onMac ? 3 : 0)
                .foregroundStyle(Theme.ink)
                .lineLimit(long && !expanded ? 7 : nil)
                .bubbleSelection()
                .contentShape(Rectangle())
                .onTapGesture { if plain != nil { withAnimation(.easeOut(duration: 0.2)) { original.toggle() } } }
            if long {
                Button(expanded ? "Show less" : "Show more") {
                    withAnimation(.easeOut(duration: 0.2)) { expanded.toggle() }
                }
                .font(Theme.text(14, .medium)).foregroundStyle(isAnswer ? Theme.inkSoft : tint)
                .buttonStyle(.plain)
            }
            if plain != nil {
                Button(original ? "Show Plain Version" : "Show Original") {
                    withAnimation(.easeOut(duration: 0.2)) { original.toggle() }
                }
                .font(Theme.text(13, .medium)).foregroundStyle(Theme.inkSoft)
                .buttonStyle(.plain)
                .accessibilityIdentifier("teamShowOriginal")
            }
        }
        .padding(.horizontal, hPad).padding(.vertical, vPad)
        .background(isAnswer ? Theme.bubble : tint.opacity(0.12),
                    in: RoundedRectangle(cornerRadius: radius, style: .continuous))
        .overlay {
            if !isAnswer {
                RoundedRectangle(cornerRadius: radius, style: .continuous).strokeBorder(tint.opacity(0.22), lineWidth: 1)
            }
        }
    }

    private func markdown(_ raw: String) -> AttributedString { ChatText.render(raw) }
}


struct WorkLogRow: View {
    let message: ChatMessage
    @State private var open: Bool = {
        #if DEBUG
        return CommandLine.arguments.contains("-workLogOpen")
        #else
        return false
        #endif
    }()

    private var steps: [WorkStep] { message.steps ?? [] }
    @State private var raw = false

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Button {
                if !steps.isEmpty { withAnimation(.snappy(duration: 0.25)) { open.toggle() } }
            } label: {
                HStack(spacing: 5) {
                    Text(message.text)
                    if !steps.isEmpty {
                        Image(systemName: "chevron.right")
                            .font(.system(size: 10, weight: .semibold))
                            .rotationEffect(.degrees(open ? 90 : 0))
                    }
                }
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .accessibilityLabel(message.text)
            .accessibilityHint(steps.isEmpty ? "" : (open ? "Hides the steps" : "Shows each step"))
            .accessibilityIdentifier("workLog")
            if open {
                VStack(alignment: .leading, spacing: 7) {
                    ForEach(steps, id: \.self) { step in
                        HStack(alignment: .firstTextBaseline, spacing: 8) {
                            Image(systemName: step.symbol)
                                .font(.system(size: 12, weight: .medium))
                                .frame(width: 16)
                            Text(raw ? (step.raw ?? step.label) : step.label)
                                .fixedSize(horizontal: false, vertical: true)
                                .textSelection(.enabled)
                        }
                    }
                    if steps.contains(where: { $0.raw != nil }) {
                        Button(raw ? "Show Short Labels" : "Show Original") {
                            withAnimation(.easeOut(duration: 0.2)) { raw.toggle() }
                        }
                        .font(Theme.text(13, .medium))
                        .buttonStyle(.plain)
                        .padding(.leading, 24)
                    }
                }
                .padding(.leading, 2)
                .transition(.opacity)
            }
        }
        .font(Theme.text(14))
        .foregroundStyle(Theme.inkSoft)
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.horizontal, 6)
        .padding(.vertical, 2)
    }
}


struct NoteLine: View {
    let message: ChatMessage
    @State private var open = false

    var body: some View {
        VStack(spacing: 6) {
            Button {
                if !message.details.isEmpty { withAnimation(.easeOut(duration: 0.2)) { open.toggle() } }
            } label: {
                HStack(alignment: .firstTextBaseline, spacing: 6) {
                    Image(systemName: message.label ?? "info.circle").font(.system(size: 11, weight: .semibold))
                    Text(message.text).multilineTextAlignment(.center)
                    if !message.details.isEmpty {
                        Image(systemName: open ? "chevron.up" : "chevron.down").font(.system(size: 9, weight: .semibold))
                    }
                }
            }
            .buttonStyle(.plain)
            .accessibilityHint(message.details.isEmpty ? "" : (open ? "Hides the steps" : "Shows each step"))
            if open {
                VStack(alignment: .leading, spacing: 3) {
                    ForEach(Array(message.details.enumerated()), id: \.offset) { _, line in
                        Text(line).font(Theme.text(12)).foregroundStyle(Theme.inkSoft).textSelection(.enabled)
                    }
                }
                .padding(.horizontal, 12).padding(.vertical, 8)
                .background(Theme.bubble.opacity(0.6), in: RoundedRectangle(cornerRadius: 10, style: .continuous))
                .transition(.opacity)
            }
        }
        .font(Theme.text(13)).foregroundStyle(Theme.inkSoft)
        .frame(maxWidth: .infinity)
        .padding(.horizontal, 24)
    }
}


struct ChoiceCardView: View {
    @Environment(AppState.self) private var state
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    let message: ChatMessage
    let botID: String
    let width: CGFloat
    let gap: CGFloat
    @State private var busy = false
    @State private var own = ""
    @State private var chosen: String?
    @State private var reopened = CommandLine.arguments.contains("-expandQuestions")
    @Namespace private var space

    var body: some View {
        if let q = message.question {
            QuestionCardView(message: message, card: q, botID: botID, width: width, gap: gap)
        } else if let answer = message.answer {
            VStack(alignment: .leading, spacing: 8) {
                if reopened {
                    card(answer: answer)
                        .transition(.opacity)
                }
                CardAnswerView(card: message, answer: answer, botID: botID, caption: !reopened,
                               match: CardMatch(card: message.id, space: space, on: !reduceMotion)) {
                    withAnimation(.easeOut(duration: 0.2)) { reopened.toggle() }
                }
            }
        } else {
            card(answer: nil)
                .transition(.opacity)
                .task {
                    guard let option = CardAnswerView.autoPick(message) else { return }
                    try? await Task.sleep(for: .seconds(2.5))
                    pick(option)
                }
        }
    }

    private func card(answer: CardAnswer?) -> some View {
        HStack {
            CapWidth(max: width) {
                VStack(alignment: .leading, spacing: 12) {
                    HStack(alignment: .top) {
                        Text(message.text).font(Theme.text(Theme.onMac ? 15 : 17, .medium)).foregroundStyle(Theme.ink)
                            .fixedSize(horizontal: false, vertical: true)
                        Spacer(minLength: 12)
                        if answer == nil {
                            Button { pick(nil) } label: {
                                Image(systemName: "xmark").font(.system(size: 12, weight: .semibold))
                                    .foregroundStyle(Theme.inkSoft).frame(width: 24, height: 24).contentShape(Rectangle())
                            }
                            .buttonStyle(.plain)
                            .accessibilityLabel("Dismiss")
                        } else {
                            Button { withAnimation(.easeOut(duration: 0.2)) { reopened = false } } label: {
                                Image(systemName: "chevron.up").font(.system(size: 12, weight: .semibold))
                                    .foregroundStyle(Theme.inkSoft).frame(width: 24, height: 24).contentShape(Rectangle())
                            }
                            .buttonStyle(.plain)
                            .accessibilityLabel("Fold")
                        }
                    }
                    VStack(spacing: 0) {
                        ForEach(Array(message.details.enumerated()), id: \.offset) { i, option in
                            if i > 0 { Rectangle().fill(Theme.rule).frame(height: 0.5) }
                            CardOptionRow(index: i, option: option, lit: chosen == option && answer == nil,
                                          checked: answer?.text == option, faded: answer != nil && answer?.text != option,
                                          match: chosen == option && answer == nil
                                            ? CardMatch(card: message.id, space: space, on: !reduceMotion) : nil) {
                                pick(option)
                            }
                            // Not .disabled while busy: that grays the lit option. pick() ignores a second tap.
                            .allowsHitTesting(answer == nil)
                        }
                    }
                    .background(Theme.composer, in: RoundedRectangle(cornerRadius: 10, style: .continuous))
                    .overlay(RoundedRectangle(cornerRadius: 10, style: .continuous).stroke(Theme.rule, lineWidth: 0.5))
                    if answer == nil {
                        OwnAnswerSlot(message: message, botID: botID, own: $own, busy: busy) { text in
                            busy = true
                            withAnimation(CardAnswerView.motion(reduceMotion)) { state.markAnswered(message, bot: botID, text: text) }
                            Task { await state.answerChoice(message, bot: botID, picked: text, custom: true); busy = false }
                        }
                    }
                }
                .padding(Theme.onMac ? 12 : 14)
                .background(Theme.bubble, in: RoundedRectangle(cornerRadius: Theme.onMac ? 18 : 22, style: .continuous))
            }
            Spacer(minLength: gap)
        }
    }

    private func pick(_ option: String?) {
        guard !busy else { return }
        busy = true
        guard let option else {
            Task { await state.answerChoice(message, bot: botID, picked: nil); busy = false }
            return
        }
        CardAnswerView.answer(option, card: message, bot: botID, state: state, reduceMotion: reduceMotion,
                              light: { chosen = $0 }) { busy = false }
    }
}


struct CardMatch {
    let card: String
    let space: Namespace.ID
    let on: Bool

    enum Part: String { case shape, words }
    func id(_ part: Part) -> String { "card-answer-\(part.rawValue)-\(card)" }
}

struct MatchedIf: ViewModifier {
    let match: CardMatch?
    let part: CardMatch.Part

    func body(content: Content) -> some View {
        if let match, match.on {
            content.matchedGeometryEffect(id: match.id(part), in: match.space,
                                          properties: part == .words ? .position : .frame,
                                          anchor: part == .words ? .leading : .center)
        } else {
            content
        }
    }
}

struct CardOptionRow: View {
    let index: Int
    let option: String
    var lit = false
    var checked = false
    var faded = false
    var match: CardMatch? = nil
    let tap: () -> Void

    private var big: CGFloat { Theme.onMac ? 15 : 17 }

    var body: some View {
        Button(action: tap) {
            HStack(spacing: 8) {
                Text(String(UnicodeScalar(65 + index)!))
                    .font(Theme.text(11, .medium)).foregroundStyle(lit ? Theme.mineText : Theme.inkSoft)
                    .frame(width: 18, height: 18)
                    .background((lit ? Theme.mineText : Theme.inkSoft).opacity(0.18), in: RoundedRectangle(cornerRadius: 4))
                Text(option).font(Theme.text(big, checked ? .medium : .regular))
                    .foregroundStyle(lit ? Theme.mineText : Theme.ink)
                    .multilineTextAlignment(.leading)
                    .modifier(MatchedIf(match: match, part: .words))
                Spacer(minLength: 0)
                if checked {
                    TablerIcon(name: "TablerCheck", size: 18).foregroundStyle(Theme.ink)
                }
            }
            .padding(.horizontal, 10).padding(.vertical, Theme.onMac ? 9 : 12)
            .background {
                if lit {
                    RoundedRectangle(cornerRadius: 10, style: .continuous).fill(Theme.mine)
                        .modifier(MatchedIf(match: match, part: .shape))
                }
            }
            .contentShape(Rectangle())
            .opacity(faded ? 0.45 : 1)
        }
        .buttonStyle(.plain)
        .accessibilityLabel(checked ? "\(option), your answer" : option)
    }
}


struct CardAnswerView: View {
    @Environment(AppState.self) private var state
    @Environment(\.cardAnswers) private var answers
    let card: ChatMessage
    let answer: CardAnswer
    let botID: String
    var caption = true
    var follow: String? = nil
    var match: CardMatch? = nil
    let showCard: () -> Void

    private var shown: ChatMessage {
        if var m = answers[card.id] {
            m.text = answer.text
            m.replyTo = nil
            return m
        }
        return ChatMessage(id: card.id, kind: .colin, text: answer.text, date: answer.at ?? card.date)
    }

    var body: some View {
        let shown = shown
        VStack(alignment: .trailing, spacing: 4) {
            if caption {
                Button(action: showCard) {
                    HStack(alignment: .firstTextBaseline, spacing: 5) {
                        Text(card.text).lineLimit(2).multilineTextAlignment(.trailing)
                        Image(systemName: "chevron.down").font(.system(size: 9, weight: .semibold))
                    }
                    .font(Theme.text(Theme.onMac ? 12 : 13)).foregroundStyle(Theme.inkSoft)
                    .padding(.horizontal, 6)
                    .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .padding(.leading, MessageRow.gap + 10)
                .transition(.opacity)
                .accessibilityLabel("Question: \(card.text)")
                .accessibilityHint("Shows the question with its answers")
            }
            MineBubble(message: shown, botID: botID, match: match,
                       extra: BubbleAction(title: caption ? "Show Question" : "Hide Question",
                                           symbol: "list.bullet.rectangle", run: showCard))
            if state.queuedIDs.contains(shown.id) { QueuedCaption() }
            if let follow, !follow.isEmpty {
                Text(follow)
                    .font(Theme.text(12)).foregroundStyle(Theme.inkSoft)
                    .multilineTextAlignment(.trailing)
                    .padding(.leading, MessageRow.gap + 10).padding(.horizontal, 6)
                    .transition(.opacity)
            }
        }
        .frame(maxWidth: .infinity, alignment: .trailing)
    }

    @MainActor
    static func autoPick(_ card: ChatMessage) -> String? {
        #if DEBUG
        let a = CommandLine.arguments
        guard let i = a.firstIndex(of: "-pickOption"), i + 1 < a.count, let n = Int(a[i + 1]),
              n >= 0, n < card.details.count else { return nil }
        return card.details[n]
        #else
        return nil
        #endif
    }

    static func motion(_ reduceMotion: Bool) -> Animation {
        reduceMotion ? .easeInOut(duration: 0.2) : .spring(response: 0.45, dampingFraction: 0.86)
    }

    @MainActor
    static func answer(_ option: String, card: ChatMessage, bot: String, state: AppState, reduceMotion: Bool,
                       light: @escaping (String?) -> Void, willAnswer: @escaping () -> Void = {},
                       done: @escaping () -> Void) {
        UIImpactFeedbackGenerator(style: .light).impactOccurred()
        withAnimation(.easeOut(duration: 0.14)) { light(option) }
        Task { @MainActor in
            try? await Task.sleep(for: .milliseconds(reduceMotion ? 120 : 300))
            withAnimation(motion(reduceMotion)) {
                willAnswer()
                state.markAnswered(card, bot: bot, text: option)
            }
            await state.answerChoice(card, bot: bot, picked: option)
            if state.chats[bot]?.first(where: { $0.id == card.id })?.answer == nil { light(nil) }
            done()
        }
    }
}


private struct ComposerSpaceKey: EnvironmentKey {
    static let defaultValue: Namespace.ID? = nil
}

extension EnvironmentValues {
    var composerSpace: Namespace.ID? {
        get { self[ComposerSpaceKey.self] }
        set { self[ComposerSpaceKey.self] = newValue }
    }
}


struct OwnAnswerSlot: View {
    @Environment(AppState.self) private var state
    @Environment(\.composerSpace) private var space
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    let message: ChatMessage
    let botID: String
    @Binding var own: String
    let busy: Bool
    let submit: (String) -> Void

    static func matchID(_ m: ChatMessage) -> String { "own-answer-\(m.id)" }
    private var big: CGFloat { Theme.onMac ? 15 : 17 }
    private var here: Bool { state.answering[botID]?.id == message.id || state.sentAnswers.contains(message.id) }

    var body: some View {
        if let space {
            ZStack(alignment: .leading) {
                label("Type your own answer").hidden()
                if here {
                    HStack(spacing: 6) {
                        Image(systemName: state.sentAnswers.contains(message.id) ? "checkmark" : "arrow.down")
                            .font(.system(size: 12, weight: .semibold))
                        Text(state.sentAnswers.contains(message.id) ? "Sending your answer" : "Type your answer below")
                    }
                    .font(Theme.text(Theme.onMac ? 13 : 14, .medium)).foregroundStyle(Theme.inkSoft)
                    .padding(.horizontal, 10)
                    .transition(.opacity)
                    .accessibilityElement(children: .combine)
                } else {
                    Button { start() } label: {
                        label("Type your own answer")
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .background(Theme.field, in: RoundedRectangle(cornerRadius: 10, style: .continuous))
                            .overlay(RoundedRectangle(cornerRadius: 10, style: .continuous).stroke(Theme.rule, lineWidth: 0.5))
                            .modifier(Matched(id: Self.matchID(message), space: space, on: !reduceMotion))
                            .contentShape(Rectangle())
                    }
                    .buttonStyle(.plain)
                    .disabled(busy)
                    .transition(.opacity)
                    .accessibilityLabel("Type your own answer")
                    .accessibilityHint("Answers this question from the message box")
                }
            }
        } else {
            TextField("Type your own answer", text: $own)
                .font(Theme.text(big))
                .textFieldStyle(.plain)
                .padding(.horizontal, 10).padding(.vertical, Theme.onMac ? 8 : 11)
                .background(Theme.field, in: RoundedRectangle(cornerRadius: 10, style: .continuous))
                .overlay(RoundedRectangle(cornerRadius: 10, style: .continuous).stroke(Theme.rule, lineWidth: 0.5))
                .submitLabel(.send)
                .onSubmit {
                    let text = own.trimmingCharacters(in: .whitespacesAndNewlines)
                    guard !text.isEmpty, !busy else { return }
                    submit(text)
                }
                .disabled(busy)
        }
    }

    private func label(_ text: String) -> some View {
        Text(text).font(Theme.text(big)).foregroundStyle(Theme.inkSoft)
            .padding(.horizontal, 10).padding(.vertical, Theme.onMac ? 8 : 11)
    }

    private func start() {
        UIImpactFeedbackGenerator(style: .light).impactOccurred()
        withAnimation(OwnAnswerSlot.motion(reduceMotion)) {
            state.answering[botID] = message
            state.replying[botID] = nil
        }
    }

    static func motion(_ reduceMotion: Bool) -> Animation {
        reduceMotion ? .easeInOut(duration: 0.18) : .spring(response: 0.42, dampingFraction: 0.88)
    }
}

struct Matched: ViewModifier {
    let id: String
    let space: Namespace.ID
    let on: Bool

    func body(content: Content) -> some View {
        if on { content.matchedGeometryEffect(id: id, in: space) } else { content }
    }
}


struct TablerIcon: View {
    let name: String
    var size: CGFloat = 16

    var body: some View {
        Image(name).renderingMode(.template).resizable().scaledToFit().frame(width: size, height: size)
            .accessibilityHidden(true)
    }
}


struct QuestionCardView: View {
    @Environment(AppState.self) private var state
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    let message: ChatMessage
    let card: QuestionCard
    let botID: String
    let width: CGFloat
    let gap: CGFloat
    @State private var busy = false
    @State private var own = ""
    @State private var showDetail = CommandLine.arguments.contains("-showDraft")
    @State private var confirmStopKind = false
    @State private var expanded = CommandLine.arguments.contains("-expandQuestions")
    @State private var chosen: String?
    @Namespace private var space

    private var closed: Bool { card.state == "expired" }
    private var open: Bool { card.isOpen || (closed && expanded) }
    private var folded: Bool { !card.isOpen && !expanded }
    private var big: CGFloat { Theme.onMac ? 15 : 17 }

    var body: some View {
        if let answer = message.answer {
            VStack(alignment: .leading, spacing: 8) {
                if expanded {
                    HStack {
                        CapWidth(max: width) { bubble }
                        Spacer(minLength: gap)
                    }
                    .transition(.opacity)
                }
                CardAnswerView(card: message, answer: answer, botID: botID, caption: !expanded, follow: quietFollow,
                               match: CardMatch(card: message.id, space: space, on: !reduceMotion)) {
                    withAnimation(.easeOut(duration: 0.2)) { expanded.toggle() }
                }
            }
        } else {
            HStack {
                CapWidth(max: width) {
                    if folded { compactRow } else { fullCard }
                }
                Spacer(minLength: gap)
            }
            .transition(.opacity)
            .task {
                guard open, let option = CardAnswerView.autoPick(message) else { return }
                try? await Task.sleep(for: .seconds(2.5))
                pick(option)
            }
        }
    }

    private var quietFollow: String? {
        guard let f = card.follow?.trimmingCharacters(in: .whitespacesAndNewlines), !f.isEmpty else { return nil }
        return f.hasPrefix("Sent to \(state.bot("default")?.name ?? "Zero")") ? nil : f
    }

    private var compactRow: some View {
        Button {
            withAnimation(.easeOut(duration: 0.2)) { expanded = true }
        } label: {
            HStack(alignment: .center, spacing: 10) {
                if card.state == "picked" {
                    Text(message.text).font(Theme.text(Theme.onMac ? 13 : 15)).foregroundStyle(Theme.inkSoft)
                        .lineLimit(1)
                    Spacer(minLength: 4)
                    HStack(spacing: 4) {
                        TablerIcon(name: "TablerCheck", size: 13)
                        Text(card.picked ?? "Answered").lineLimit(1)
                    }
                    .font(Theme.text(Theme.onMac ? 13 : 15, .medium)).foregroundStyle(Theme.ink)
                    .layoutPriority(1)
                } else {
                    Text(message.text).font(Theme.text(Theme.onMac ? 13 : 15)).foregroundStyle(Theme.ink)
                        .lineLimit(2).multilineTextAlignment(.leading)
                    Spacer(minLength: 4)
                    stateLabel.layoutPriority(1)
                }
            }
            .padding(.horizontal, 12).padding(.vertical, Theme.onMac ? 7 : 9)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(Theme.bubble.opacity(0.7), in: RoundedRectangle(cornerRadius: 14, style: .continuous))
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .accessibilityElement(children: .combine)
        .accessibilityHint(closed ? "Opens the question so you can still answer it" : "Shows the whole question")
    }

    private var stateLabel: some View {
        HStack(spacing: 4) {
            TablerIcon(name: "TablerClockX", size: 13)
            Text(closed ? "Closed" : "Dismissed")
        }
        .font(Theme.text(12, .medium)).foregroundStyle(Theme.inkSoft)
    }

    private var fullCard: some View {
        VStack(alignment: .leading, spacing: 8) {
            bubble
            if let follow = card.follow, !follow.isEmpty {
                HStack(alignment: .firstTextBaseline, spacing: 6) {
                    TablerIcon(name: "TablerCornerDownRight", size: 13)
                        .alignmentGuide(.firstTextBaseline) { $0[.bottom] - 2 }
                    Text(follow)
                }
                .font(Theme.text(13)).foregroundStyle(Theme.inkSoft)
                .padding(.horizontal, 6)
                .accessibilityElement(children: .combine)
            }
        }
    }

    private var fold: some View {
        Button { withAnimation(.easeOut(duration: 0.2)) { expanded = false } } label: {
            TablerIcon(name: "TablerChevronUp", size: 16)
                .foregroundStyle(Theme.inkSoft).frame(width: 24, height: 24).contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .accessibilityLabel("Fold")
    }

    private var bubble: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(alignment: .top, spacing: 8) {
                VStack(alignment: .leading, spacing: 6) {
                    if !card.evidence.isEmpty {
                        Text(card.evidence).font(Theme.text(Theme.onMac ? 14 : 16)).foregroundStyle(Theme.ink)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                    Text(message.text).font(Theme.text(big, .medium)).foregroundStyle(Theme.ink)
                        .fixedSize(horizontal: false, vertical: true)
                }
                Spacer(minLength: 8)
                if card.isOpen { menu; dismiss } else { fold }
            }
            if let detail = card.detail, !detail.text.isEmpty { detailBox(detail) }
            if let p = card.prepared, open {
                HStack(alignment: .firstTextBaseline, spacing: 6) {
                    TablerIcon(name: "TablerMailCheck", size: 14)
                        .alignmentGuide(.firstTextBaseline) { $0[.bottom] - 2 }
                    Text("\(p.by) prepared this for \(p.to). The first answer sends exactly what is shown, once.")
                        .fixedSize(horizontal: false, vertical: true)
                }
                .font(Theme.text(13)).foregroundStyle(Theme.inkSoft)
                .accessibilityElement(children: .combine)
            }
            options
            if open {
                OwnAnswerSlot(message: message, botID: botID, own: $own, busy: busy) { text in
                    busy = true
                    withAnimation(CardAnswerView.motion(reduceMotion)) {
                        expanded = false
                        state.markAnswered(message, bot: botID, text: text)
                    }
                    Task { await state.answerChoice(message, bot: botID, picked: text, custom: true); busy = false }
                }
            }
            if closed {
                HStack(spacing: 6) {
                    TablerIcon(name: "TablerClockX", size: 14)
                    Text("Closed. You can still answer it.")
                }
                .font(Theme.text(13, .medium)).foregroundStyle(Theme.inkSoft)
                .accessibilityElement(children: .combine)
            } else if card.state == "dismissed" {
                stateLabel.accessibilityElement(children: .combine)
            }
        }
        .padding(Theme.onMac ? 12 : 14)
        .background(Theme.bubble, in: RoundedRectangle(cornerRadius: Theme.onMac ? 18 : 22, style: .continuous))
    }

    private var menu: some View {
        Menu {
            Button("Don't Ask About This Again") {
                Task { await state.stopQuestion(message, bot: botID, kind: false) }
            }
            Button("Stop This Kind of Question") { confirmStopKind = true }
        } label: {
            TablerIcon(name: "TablerDots", size: 18)
                .foregroundStyle(Theme.inkSoft).frame(width: 24, height: 24).contentShape(Rectangle())
        }
        .menuStyle(.borderlessButton)
        .accessibilityLabel("More")
        .confirmationDialog("Stop this kind of question?", isPresented: $confirmStopKind, titleVisibility: .visible) {
            Button("Stop Asking") { Task { await state.stopQuestion(message, bot: botID, kind: true) } }
        } message: {
            Text("Zero won't ask questions like this one. You can turn them back on in Settings.")
        }
    }

    private var dismiss: some View {
        Button { pick(nil) } label: {
            Image(systemName: "xmark").font(.system(size: 12, weight: .semibold))
                .foregroundStyle(Theme.inkSoft).frame(width: 24, height: 24).contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .accessibilityLabel("Dismiss")
    }

    private func detailBox(_ detail: QuestionDetail) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            Button {
                withAnimation(.easeOut(duration: 0.2)) { showDetail.toggle() }
            } label: {
                HStack(spacing: 6) {
                    Text(showDetail ? "Hide \(detail.label)" : "Show \(detail.label)")
                    TablerIcon(name: showDetail ? "TablerChevronUp" : "TablerChevronDown", size: 13)
                }
                .font(Theme.text(14, .medium)).foregroundStyle(Theme.inkSoft)
            }
            .buttonStyle(.plain)
            if showDetail {
                Text(detail.text)
                    .font(Theme.text(Theme.onMac ? 13 : 15)).foregroundStyle(Theme.ink)
                    .fixedSize(horizontal: false, vertical: true)
                    .textSelection(.enabled)
                    .padding(.horizontal, 12).padding(.vertical, 10)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .background(Theme.composer, in: RoundedRectangle(cornerRadius: 10, style: .continuous))
                    .overlay(RoundedRectangle(cornerRadius: 10, style: .continuous).stroke(Theme.rule, lineWidth: 0.5))
                    .transition(.opacity)
            }
        }
    }

    private var options: some View {
        VStack(spacing: 0) {
            ForEach(Array(message.details.enumerated()), id: \.offset) { i, option in
                if i > 0 { Rectangle().fill(Theme.rule).frame(height: 0.5) }
                let answered = message.answer != nil
                let mine = answered && card.picked == option
                let lit = chosen == option && !answered
                CardOptionRow(index: i, option: option, lit: lit, checked: mine, faded: !open && !mine,
                              match: lit ? CardMatch(card: message.id, space: space, on: !reduceMotion) : nil) {
                    pick(option)
                }
                .disabled(!open)
            }
        }
        .background(Theme.composer, in: RoundedRectangle(cornerRadius: 10, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: 10, style: .continuous).stroke(Theme.rule, lineWidth: 0.5))
    }

    private func pick(_ option: String?) {
        guard !busy, open else { return }
        busy = true
        state.answering[botID] = state.answering[botID]?.id == message.id ? nil : state.answering[botID]
        guard let option else {
            Task { await state.answerChoice(message, bot: botID, picked: nil); busy = false }
            return
        }
        CardAnswerView.answer(option, card: message, bot: botID, state: state, reduceMotion: reduceMotion,
                              light: { chosen = $0 }, willAnswer: { expanded = false }) { busy = false }
    }
}
