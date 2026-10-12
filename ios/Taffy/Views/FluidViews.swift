import SwiftUI

// The fluid app's views (Model/Fluid.swift has the why, with the research rows): the bottom of a chat while a Zip
// works or waits, the queued caption, the Stop button, a row's honest line, While You Were Away, Zero's first
// tasks and the notification kinds.

// MARK: The bottom of a chat

/// What sits under the conversation: a failed or stuck reply with a way forward, "Paused for your okay" while a card
/// waits, or what the Zip is doing in plain words. Nothing when the Zip is idle.
struct ChatStatusArea: View {
    @Environment(AppState.self) private var state
    let botID: String

    var body: some View {
        let bot = state.bot(botID)
        if let p = state.problem(for: botID) {
            TurnProblemRow(problem: p, botID: botID)
        } else if state.isPaused(botID) && state.workingText(for: botID) != nil {
            PausedRow(bot: bot)
        } else if let status = state.workingText(for: botID) {
            ActivityRow(steps: state.steps(for: botID), fallback: status, bot: bot)
        }
    }
}

/// The Zip is working: three dots, and one compact line of its steps in plain words (#4). Tap to see every step, so a
/// person can spot it going off track and stop it, without a tool log.
struct ActivityRow: View {
    let steps: [String]
    var fallback: String = ""
    var bot: Bot?
    @State private var open = Self.startOpen

    private static var startOpen: Bool {
        #if DEBUG
        return CommandLine.arguments.contains("-expandActivity")   // for screenshots
        #else
        return false
        #endif
    }

    /// "Opening elearning.lakeside.edu, signing in, reading a file": the newest three steps as one line.
    static func line(_ steps: [String]) -> String {
        let recent = Array(steps.suffix(3))
        return recent.enumerated().map { i, s in
            i == 0 ? s : (s.prefix(1).lowercased() + s.dropFirst())
        }.joined(separator: ", ")
    }

    private var shown: [String] {
        if !steps.isEmpty { return steps }
        return ["Thinking", "Working", ""].contains(fallback) ? [] : [fallback]
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            TypingDots(dot: 8)
                .padding(.horizontal, 16).frame(height: 40)
                .background(Theme.bubble, in: RoundedRectangle(cornerRadius: 20, style: .continuous))
            if !shown.isEmpty {
                Button { withAnimation(.easeOut(duration: 0.2)) { open.toggle() } } label: {
                    HStack(alignment: .firstTextBaseline, spacing: 5) {
                        Text(open ? "What \(bot?.name ?? "it") has done so far" : Self.line(shown))
                            .lineLimit(open ? 1 : 2)
                            .multilineTextAlignment(.leading)
                            .contentTransition(.opacity)
                        if shown.count > 1 || open {
                            Image(systemName: open ? "chevron.up" : "chevron.down")
                                .font(.system(size: 10, weight: .semibold))
                        }
                    }
                    .font(Theme.caption).foregroundStyle(Theme.inkSoft)
                    .padding(.leading, 4)
                    .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .disabled(shown.count < 2 && !open)
                .accessibilityHint(open ? "Hides the steps" : "Shows every step so far")
                if open {
                    VStack(alignment: .leading, spacing: 6) {
                        ForEach(Array(shown.enumerated()), id: \.offset) { i, step in
                            HStack(spacing: 8) {
                                Image(systemName: i == shown.count - 1 ? "circle.dotted" : "checkmark")
                                    .font(.system(size: 11, weight: .semibold))
                                    .foregroundStyle(i == shown.count - 1 ? Theme.inkSoft : Theme.ink)
                                    .frame(width: 14)
                                Text(step).font(Theme.caption).foregroundStyle(Theme.ink)
                            }
                        }
                    }
                    .padding(.leading, 6)
                    .transition(.opacity)
                }
            }
        }
        .animation(.easeOut(duration: 0.2), value: shown)
        .accessibilityElement(children: .contain)
        .accessibilityLabel("\(bot?.name ?? "The Zip") is working. \(Self.line(shown))")
    }
}

/// A card is waiting for the person: the Zip is paused on it, not working (#43: "make it clear that the task is
/// paused").
struct PausedRow: View {
    var bot: Bot?

    var body: some View {
        Label {
            Text("\(bot?.name ?? "The Zip") is paused for your okay.")
        } icon: {
            Image(systemName: "hand.raised.fill").foregroundStyle(Theme.taffyDeep)
        }
        .font(Theme.secondary).foregroundStyle(Theme.ink)
        .padding(.horizontal, 14).padding(.vertical, 10)
        .background(Theme.bubble, in: Capsule())
        .accessibilityElement(children: .combine)
    }
}

/// A reply that failed, or a turn that went quiet too long: one plain sentence and a way forward, never an endless
/// typing indicator (#50 to #52).
struct TurnProblemRow: View {
    @Environment(AppState.self) private var state
    let problem: TurnProblem
    let botID: String
    @State private var busy = false

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(alignment: .firstTextBaseline, spacing: 8) {
                Image(systemName: problem.kind == .failed ? "exclamationmark.circle.fill" : "hourglass")
                    .foregroundStyle(problem.kind == .failed ? Theme.danger : Theme.inkSoft)
                VStack(alignment: .leading, spacing: 3) {
                    Text(problem.line).font(Theme.text(15, .medium)).foregroundStyle(Theme.ink)
                    if let d = problem.detail, !d.isEmpty, d != problem.line {
                        Text(d).font(Theme.caption).foregroundStyle(Theme.inkSoft)
                    }
                }
            }
            HStack(spacing: 10) {
                if let words = problem.words, !words.isEmpty {
                    pill("Retry", primary: true) { await state.retry(botID, words: words) }
                }
                if problem.kind == .stalled {
                    pill("Stop", primary: problem.words == nil) { await state.stop(botID) }
                }
            }
        }
        .padding(14)
        .background(Theme.bubble, in: RoundedRectangle(cornerRadius: 20, style: .continuous))
        .accessibilityElement(children: .contain)
    }

    private func pill(_ title: String, primary: Bool, _ action: @escaping () async -> Void) -> some View {
        Button { Task { busy = true; await action(); busy = false } } label: {
            Text(title).font(Theme.text(15, .medium))
                .foregroundStyle(primary ? Theme.mineText : Theme.ink)
                .padding(.horizontal, 18).frame(minHeight: 38)
                .background(primary ? Theme.mine : Theme.canvas, in: Capsule())
                .overlay { if !primary { Capsule().stroke(Theme.rule) } }
        }
        .buttonStyle(PressStyle())
        .disabled(busy || state.offline)
    }
}

/// Under the person's own message while it waits behind the Zip's current work (#57).
struct QueuedCaption: View {
    var body: some View {
        Label("Queued, it will see this next.", systemImage: "clock")
            .font(Theme.caption).foregroundStyle(Theme.inkSoft)
            .labelStyle(.titleAndIcon)
            .padding(.trailing, 6)
            .frame(maxWidth: .infinity, alignment: .trailing)
            .accessibilityLabel("Queued. It will see this next.")
    }
}

/// The square Stop button in the message box while the Zip works and the box is empty (#56).
struct StopButton: View {
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            Image(systemName: "stop.fill")
                .font(.system(size: Theme.onMac ? 11 : 13, weight: .bold))
                .foregroundStyle(Theme.actionGlyph)
                .frame(width: Theme.onMac ? 30 : 36, height: Theme.onMac ? 30 : 36)
                .background(Theme.action, in: Circle())
                .frame(width: Theme.onMac ? 36 : 44, height: Theme.onMac ? 36 : 44)
                .contentShape(Circle())
        }
        .buttonStyle(PressStyle(scale: 0.9))
        .accessibilityLabel("Stop")
        .keyboardShortcut(".", modifiers: .command)
    }
}

// MARK: Rows

/// A Zip row's second line, most pressing first: paused for an okay, a stuck or failed reply, what it is doing, then
/// blocked or a card that closed, else its latest message (#43, #50 to #53).
struct RowStatusLine: View {
    @Environment(AppState.self) private var state
    let bot: Bot
    var font: Font = Theme.secondary
    var dots: CGFloat = 6

    var body: some View {
        let working = bot.working > 0 || bot.busy || state.working[bot.id] != nil
        Group {
            if state.isPaused(bot.id), let s = bot.status, s.state == "paused" {
                icon("hand.raised.fill", s.line, tint: Theme.taffyDeep)
            } else if state.isPaused(bot.id) {
                icon("hand.raised.fill", "Paused for your okay.", tint: Theme.taffyDeep)
            } else if let p = state.problem(for: bot.id) {
                // The row already names the Zip, so its line is short.
                icon(p.kind == .failed ? "exclamationmark.circle.fill" : "hourglass",
                     p.kind == .failed ? "Its last reply didn't finish." : "Taking longer than usual.",
                     tint: p.kind == .failed ? Theme.danger : Theme.inkSoft)
            } else if working {
                HStack(spacing: 8) {
                    TypingDots(dot: dots)
                    Text(state.workingText(for: bot.id) ?? "Working").foregroundStyle(Theme.inkSoft).lineLimit(1)
                }
            } else if let s = bot.status, s.state != "paused" {
                icon(s.state == "blocked" ? "exclamationmark.circle" : "clock.badge.xmark", s.line, tint: Theme.inkSoft)
            } else {
                Text(bot.preview ?? bot.role).foregroundStyle(Theme.inkSoft).lineLimit(1)
            }
        }
        .font(font)
    }

    private func icon(_ symbol: String, _ text: String, tint: Color) -> some View {
        HStack(spacing: 6) {
            Image(systemName: symbol).font(.system(size: 12, weight: .semibold)).foregroundStyle(tint)
            Text(text).foregroundStyle(Theme.ink).lineLimit(1)
        }
    }
}

/// The one action a row offers beside its line: Ask Again for a card that closed (#38, #39), Retry for a failed or
/// stuck reply (#52). Nil when there is nothing to do from the list.
struct RowAction {
    let title: String
    let run: (AppState) async -> Void

    @MainActor
    static func `for`(_ bot: Bot, in state: AppState) -> RowAction? {
        if state.isPaused(bot.id) { return nil }
        if let p = state.problem(for: bot.id), let words = p.words, !words.isEmpty {
            return RowAction(title: "Retry") { await $0.retry(bot.id, words: words) }
        }
        if state.working[bot.id] == nil, let s = bot.status, s.state == "expired" {
            let what = s.what ?? ""
            return RowAction(title: "Ask Again") { await $0.askAgain(bot.id, what: what) }
        }
        return nil
    }
}

struct RowActionButton: View {
    @Environment(AppState.self) private var state
    let action: RowAction
    @State private var busy = false

    var body: some View {
        Button {
            Task { busy = true; await action.run(state); busy = false }
        } label: {
            Text(action.title)
                .font(Theme.text(14, .medium)).foregroundStyle(Theme.ink)
                .padding(.horizontal, 12).frame(minHeight: 30)
                .background(Theme.bubble, in: Capsule())
                .overlay(Capsule().stroke(Theme.rule))
                .frame(minHeight: 44)
                .contentShape(Rectangle())
        }
        .buttonStyle(PressStyle())
        .disabled(busy || state.offline)
    }
}

// MARK: While You Were Away

/// Back after an hour or more, and only when there is something to say: one line per Zip, what needs the person
/// first (#75, #76). Got It puts it away.
struct AwayCard: View {
    @Environment(AppState.self) private var state
    var open: (String) -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                Text("While You Were Away").font(Theme.headline).foregroundStyle(Theme.ink)
                Spacer()
                Button("Got It") { withAnimation { state.away = [] } }
                    .font(Theme.secondary.weight(.medium)).foregroundStyle(Theme.ink)
                    .frame(minHeight: 36)
            }
            ForEach(state.awayShown) { item in
                Button { open(item.bot_id) } label: {
                    HStack(alignment: .top, spacing: 10) {
                        BotAvatar(bot: state.bot(item.bot_id), size: 28)
                        VStack(alignment: .leading, spacing: 2) {
                            Text(item.name).font(Theme.text(15, .semibold)).foregroundStyle(Theme.ink)
                            Text(item.line).font(Theme.secondary).foregroundStyle(Theme.inkSoft)
                                .multilineTextAlignment(.leading)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                        Spacer(minLength: 0)
                        Image(systemName: "chevron.right").font(.system(size: 12, weight: .semibold))
                            .foregroundStyle(Theme.inkSoft).padding(.top, 4)
                    }
                    .contentShape(Rectangle())
                }
                .buttonStyle(PressStyle(scale: 0.98))
            }
        }
        .padding(16)
        .background(Theme.bubble, in: RoundedRectangle(cornerRadius: 20, style: .continuous))
    }
}

// MARK: First run

/// Zero meets the person in its chat right after the onboarding: who it is, and two or three first tasks made from
/// what the onboarding learned, each one tap to start (#6, principle 12).
struct FirstTasksCard: View {
    @Environment(AppState.self) private var state
    @Binding var tasks: FirstTasks?
    @State private var busy = false

    var body: some View {
        if let t = tasks, !t.tasks.isEmpty {
            HStack {
                VStack(alignment: .leading, spacing: 12) {
                    HStack(spacing: 8) {
                        BotAvatar(bot: state.bot("default"), size: 22)
                        Text("First Things I Can Do").font(Theme.text(15, .medium)).foregroundStyle(Theme.ink)
                    }
                    Text(t.intro).font(Theme.text(16)).foregroundStyle(Theme.ink)
                        .fixedSize(horizontal: false, vertical: true)
                    VStack(spacing: 8) {
                        ForEach(t.tasks) { task in row(task) }
                    }
                    Button("Not Now") { dismiss() }
                        .font(Theme.secondary.weight(.medium)).foregroundStyle(Theme.inkSoft)
                        .frame(maxWidth: .infinity, minHeight: 36)
                }
                .padding(14)
                .background(Theme.bubble, in: RoundedRectangle(cornerRadius: 24, style: .continuous))
                Spacer(minLength: Theme.onMac ? 40 : 48)
            }
        }
    }

    private func row(_ task: FirstTasks.Task) -> some View {
        HStack(alignment: .center, spacing: 12) {
            VStack(alignment: .leading, spacing: 2) {
                Text(task.title).font(Theme.text(16, .semibold)).foregroundStyle(Theme.ink)
                Text(task.detail).font(Theme.caption).foregroundStyle(Theme.inkSoft)
                    .fixedSize(horizontal: false, vertical: true)
            }
            Spacer(minLength: 0)
            Button {
                start(task)
            } label: {
                Text("Start").font(Theme.text(15, .medium)).foregroundStyle(Theme.mineText)
                    .padding(.horizontal, 16).frame(minHeight: 36)
                    .background(Theme.mine, in: Capsule())
            }
            .buttonStyle(PressStyle())
            .disabled(busy || state.offline)
            .accessibilityLabel("Start: \(task.title)")
        }
        .padding(12)
        .background(Theme.canvas, in: RoundedRectangle(cornerRadius: 14, style: .continuous))
    }

    private func start(_ task: FirstTasks.Task) {
        guard var t = tasks else { return }
        t.tasks.removeAll { $0.id == task.id }
        tasks = t.tasks.isEmpty ? nil : t
        FirstTasks.save(tasks)
        busy = true
        Task { _ = await state.send(task.message, to: "default"); busy = false }
    }

    private func dismiss() {
        withAnimation { tasks = nil }
        FirstTasks.save(nil)
    }
}

// MARK: Notification kinds

/// Under a Zip's notify switch: which kinds reach the phone (#75 to #77).
struct NotifyKindsSection: View {
    @Environment(AppState.self) private var state
    let botID: String

    var body: some View {
        let bot = state.bot(botID)
        let kinds = bot?.notify_kinds ?? NotifyKinds()
        VStack(alignment: .leading, spacing: 8) {
            toggle("Decisions and Blocks", "Cards that need your okay, and work that is stuck.", kinds.needs) {
                var k = kinds; k.needs = $0; state.setNotifyKinds(botID, k)
            }
            toggle("Finished Work", "When something it was doing for you is done.", kinds.done) {
                var k = kinds; k.done = $0; state.setNotifyKinds(botID, k)
            }
            toggle("Routine Progress", "Updates from scheduled jobs that ask nothing of you.", kinds.progress) {
                var k = kinds; k.progress = $0; state.setNotifyKinds(botID, k)
            }
        }
        .padding(.leading, 12)
        .overlay(alignment: .leading) { Rectangle().fill(Theme.rule).frame(width: 2) }
    }

    private func toggle(_ title: String, _ detail: String, _ on: Bool, _ set: @escaping (Bool) -> Void) -> some View {
        Toggle(isOn: Binding(get: { on }, set: set)) {
            VStack(alignment: .leading, spacing: 1) {
                Text(title).font(Theme.secondary.weight(.medium)).foregroundStyle(Theme.ink)
                Text(detail).font(Theme.caption).foregroundStyle(Theme.inkSoft)
            }
        }
        .tint(Theme.taffyDeep)
    }
}
