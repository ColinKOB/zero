import Foundation
import SwiftUI

/// One honest line for a Zip's row, from the hub (`status` in GET /api/bots). Nil when nothing needs saying.
struct BotStatus: Codable, Hashable {
    /// paused (a card waits for an okay or a sign-in), blocked, failed (its last reply), expired (a card closed)
    let state: String
    let line: String
    /// failed: the person's words, for Retry.
    var words: String? = nil
    /// expired: what the card asked, for Ask Again.
    var what: String? = nil
    var detail: String? = nil
    var task_id: String? = nil
    var feed_id: Int? = nil
    /// paused: when the card it waits on came up, so a message the person sent since then ends the pause at once.
    var at: Double? = nil
}

/// Which kinds of news from a Zip reach the phone (PUT /api/bots/{id}/notify-kinds). The Zip's notify switch stays
/// the master.
struct NotifyKinds: Codable, Hashable {
    var needs: Bool = true
    var done: Bool = true
    var progress: Bool = true
}

/// What a Zip is doing in its chat right now (the feed's `activity`): plain-word steps, and whether it has gone quiet
/// for too long.
struct LiveActivity: Codable, Hashable {
    var since: Double
    var last_at: Double
    var steps: [String]
    var stalled: Bool
    var source: String? = nil
    /// The person's own words, when the turn is theirs (for Retry).
    var words: String? = nil
    /// The turn is held on a card the person has to answer: paused, not working. Older hubs leave it out.
    var waiting: Bool? = nil
}

/// While You Were Away: one line per Zip, from GET /api/away.
struct AwayItem: Codable, Hashable, Identifiable {
    let bot_id: String
    let name: String
    let line: String
    let needs: Int
    var id: String { bot_id }
}

/// Something wrong with a Zip's current or last turn, shown in its chat and on its row with a way forward.
struct TurnProblem: Hashable {
    enum Kind { case failed, stalled }
    let kind: Kind
    let line: String
    var detail: String? = nil
    /// The person's words to send again. Without them, a stalled turn can only be stopped.
    var words: String? = nil
}

extension HubClient {
    struct StopResult: Decodable { let ok: Bool; let stopped: Bool; let returned: [String] }

    func stop(bot: String) async throws -> StopResult {
        try await send(try request("/api/bots/\(bot)/stop", method: "POST"), as: StopResult.self)
    }

    func setNotifyKinds(_ bot: String, _ kinds: NotifyKinds) async throws -> NotifyKinds {
        struct Resp: Decodable { let notify_kinds: NotifyKinds }
        return try await send(try request("/api/bots/\(bot)/notify-kinds", method: "PUT", body: kinds), as: Resp.self)
            .notify_kinds
    }

    func setTone(_ bot: String, _ tone: String) async throws -> String {
        struct Body: Encodable { let tone: String }
        struct Resp: Decodable { let tone: String }
        return try await send(try request("/api/bots/\(bot)/tone", method: "PUT", body: Body(tone: tone)), as: Resp.self)
            .tone
    }

    func away(since: Double) async throws -> [AwayItem] {
        struct Resp: Decodable { let items: [AwayItem] }
        return try await send(try request("/api/away?since=\(Int(since))"), as: Resp.self).items
    }
}

extension AppState {
    // MARK: Stop and Retry

    /// Stop (#55, #56): the Zip stops what it is doing in this chat. Messages the person queued behind it come back to
    /// the message box instead of being sent, and the hub holds its own work in this chat until the person talks again.
    func stop(_ bot: String) async {
        let name = self.bot(bot)?.name ?? "The Zip"
        working[bot] = nil
        activity[bot] = nil
        problems[bot] = nil
        if !fixtureMode {
            do {
                let r = try await client.stop(bot: bot)
                for text in r.returned { putBackQuietly(text, to: bot) }
            } catch {
                lastError = HubClient.describe(error)
                return
            }
        }
        chats[bot, default: []].append(ChatMessage(id: "stopped-\(UUID().uuidString)", kind: .event,
                                                    text: "\(name) stopped.", date: Date(), label: "stopped", botID: bot))
    }

    /// Sends the person's words again after a failed or stuck reply. A stuck turn is stopped first.
    func retry(_ bot: String, words: String) async {
        let stuck = problems[bot]?.kind == .stalled || activity[bot]?.stalled == true
        problems[bot] = nil
        if stuck, !fixtureMode { _ = try? await client.stop(bot: bot) }
        activity[bot] = nil
        _ = await send(words, to: bot)
    }

    /// Ask Again on a card that closed with no answer (#38, #39): the person asks the Zip, in their own words, to
    /// raise it again, and the Zip puts a fresh card in front of them.
    func askAgain(_ bot: String, what: String) async {
        let thing = what.trimmingCharacters(in: .whitespacesAndNewlines).trimmingCharacters(in: CharacterSet(charactersIn: "."))
        _ = await send(thing.isEmpty ? "Please ask me again about the card that closed."
                                     : "Please ask me again about this: \(thing).", to: bot)
    }

    /// Text handed back by Stop goes after anything already in the box, once.
    func putBackQuietly(_ text: String, to bot: String) {
        let t = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !t.isEmpty else { return }
        let current = (drafts[bot] ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
        if !current.contains(t) { drafts[bot] = current.isEmpty ? t : current + "\n\n" + t }
    }

    // MARK: What the chat and the row show

    /// The Zip is waiting on the person: its running turn is held on a card, or a card it raised in the person's
    /// latest turn (or after it) is still open (#43). An older card stays in the chat to use but pauses nothing, and
    /// a message from the person ends the pause at once (Oct 9, 11:18 p.m.: an 11:54 a.m. sign-in card kept Zero
    /// "paused for your okay" over the reply to a new message). See ChatSync.live.
    func isPaused(_ bot: String) -> Bool {
        ChatSync.paused(turnHeld: activity[bot]?.waiting) { cardsWait(bot) }
    }

    /// With no turn running: a card the Zip raised in the person's latest turn or after it, a card holding other
    /// work, or the hub's paused status newer than the person's latest message.
    private func cardsWait(_ bot: String) -> Bool {
        let said = lastSaid(bot)
        let cards = (board.requests ?? []).filter { $0.bot_id == bot }
        if cards.contains(where: { r in
            r.why == "background" || r.why == "scheduled"
                || ChatSync.live(card: Date(timeIntervalSince1970: r.created_at), hubWaiting: nil, lastSaid: said)
        }) { return true }
        if (chats[bot] ?? []).contains(where: { m in (m.kind == .login || m.kind == .account) && cardIsLive(m, bot: bot) }) {
            return true
        }
        if let s = self.bot(bot)?.status, s.state == "paused" {
            return ChatSync.live(card: s.at.map { Date(timeIntervalSince1970: $0) }, hubWaiting: nil, lastSaid: said)
        }
        return false
    }

    /// Whether the Zip waits on this open sign-in or account card now (an older one stays, marked Asked Earlier).
    func cardIsLive(_ m: ChatMessage, bot: String) -> Bool {
        ChatSync.live(card: m.date, hubWaiting: m.waiting, lastSaid: lastSaid(bot))
    }

    /// When the person last wrote in this chat, from what the phone shows (their own messages, sent or saved).
    func lastSaid(_ bot: String) -> Date? {
        (chats[bot] ?? []).last(where: { $0.kind == .colin })?.date
    }

    /// A failed reply, or a turn that has gone quiet for too long (#50 to #52).
    func problem(for bot: String) -> TurnProblem? {
        if let a = activity[bot], a.stalled, !isPaused(bot) {
            return TurnProblem(kind: .stalled, line: "This is taking longer than usual.", words: a.words)
        }
        if let p = problems[bot] { return p }
        if let s = self.bot(bot)?.status, s.state == "failed", working[bot] == nil {
            return TurnProblem(kind: .failed, line: s.line, detail: s.detail, words: s.words)
        }
        return nil
    }

    /// The plain steps so far, newest last.
    func steps(for bot: String) -> [String] { activity[bot]?.steps ?? [] }

    /// A status line from the turn's own stream also counts as a step, so the line moves at once.
    func noteStep(_ text: String, bot: String) {
        let now = Date().timeIntervalSince1970
        var a = activity[bot] ?? LiveActivity(since: now, last_at: now, steps: [], stalled: false)
        a.last_at = now
        a.stalled = false
        if a.steps.last != text { a.steps.append(text); a.steps = Array(a.steps.suffix(8)) }
        activity[bot] = a
    }

    func setNotifyKinds(_ bot: String, _ kinds: NotifyKinds) {
        guard let i = bots.firstIndex(where: { $0.id == bot }) else { return }
        let before = bots[i].notify_kinds
        bots[i].notify_kinds = kinds
        if demoMode || fixtureMode { return }
        Task {
            do {
                let saved = try await client.setNotifyKinds(bot, kinds)
                if let j = bots.firstIndex(where: { $0.id == bot }) { bots[j].notify_kinds = saved }
            } catch {
                if let j = bots.firstIndex(where: { $0.id == bot }) { bots[j].notify_kinds = before }
                lastError = "That change didn't save. Try again."
            }
        }
    }

    func setTone(_ bot: String, _ tone: String) {
        guard let i = bots.firstIndex(where: { $0.id == bot }) else { return }
        let before = bots[i].tone
        bots[i].tone = tone
        if demoMode || fixtureMode { return }
        Task {
            do {
                let saved = try await client.setTone(bot, tone)
                if let j = bots.firstIndex(where: { $0.id == bot }) { bots[j].tone = saved }
            } catch {
                if let j = bots.firstIndex(where: { $0.id == bot }) { bots[j].tone = before }
                lastError = "That change didn't save. Try again."
            }
        }
    }

    // MARK: While You Were Away

    static let awayAfter: TimeInterval = 60 * 60
    private static let lastSeenKey = "lastSeenAt"

    /// The app went to the background: remember when the person last looked.
    func noteLeaving() {
        UserDefaults.standard.set(Date().timeIntervalSince1970, forKey: Self.lastSeenKey)
    }

    /// Back after an hour or more: a short summary, only when there is something to say (#75, #76).
    func checkAway() async {
        let last = UserDefaults.standard.double(forKey: Self.lastSeenKey)
        UserDefaults.standard.set(Date().timeIntervalSince1970, forKey: Self.lastSeenKey)
        guard !fixtureMode, paired, last > 0, Date().timeIntervalSince1970 - last >= Self.awayAfter else { return }
        if let items = try? await client.away(since: last), !items.isEmpty { away = items }
    }
}

// MARK: First run

/// What Zero proposes after the onboarding (#6: "it recommends tasks then pulls it up so your only input is to
/// authorize"). Made from what the onboarding learned, kept on the device until the person starts or dismisses them.
struct FirstTasks: Codable, Equatable {
    struct Task: Codable, Equatable, Identifiable {
        let id: String
        let title: String
        let detail: String
        /// Sent to Zero as the person's own message when they tap Start.
        let message: String
    }
    var intro: String
    var tasks: [Task]

    private static let key = "firstTasks"

    /// Set when the onboarding ends: Home opens Zero's chat once, where Zero introduces itself.
    static var openChatOnce: Bool {
        get { UserDefaults.standard.bool(forKey: "firstTasksOpenChat") }
        set { UserDefaults.standard.set(newValue, forKey: "firstTasksOpenChat") }
    }

    static func load() -> FirstTasks? {
        guard let d = UserDefaults.standard.data(forKey: key) else { return nil }
        return try? JSONDecoder().decode(FirstTasks.self, from: d)
    }

    static func save(_ value: FirstTasks?) {
        if let value, !value.tasks.isEmpty, let d = try? JSONEncoder().encode(value) {
            UserDefaults.standard.set(d, forKey: key)
        } else {
            UserDefaults.standard.removeObject(forKey: key)
        }
    }

    /// Two or three first tasks from the onboarding's answers, in this order: the Zips the person picked, their email
    /// and calendar when those are connected, then what they said about their work. Only things the team can do now.
    @MainActor
    static func make(agent: String, person: String, picked: [ZipTemplate], projects: [ProjectIdea],
                     about: [String: String], home: HomeSettings?) -> FirstTasks {
        var tasks: [Task] = []
        for t in picked.prefix(2) {
            tasks.append(Task(id: "zip-\(t.id)", title: "Start \(t.name)", detail: t.pitch,
                              message: "Please have \(t.name) start on its first job now, and show me what it finds before anything goes out."))
        }
        for p in projects.prefix(2) where tasks.count < 2 {
            tasks.append(Task(id: "project-\(p.id)", title: "Start \(p.name)", detail: p.pitch,
                              message: "Let's start on this: \(p.name). \(p.pitch) Ask me what you need first."))
        }
        let readsMail = picked.contains { OnboardingModel.readsMail($0) }
        if home?.hasMail == true, !readsMail {
            tasks.append(Task(id: "mail", title: "Check My Email", detail: "I'll read the last two days and tell you what needs an answer.",
                              message: "Look through my email from the last two days and tell me what needs an answer from me."))
        }
        if home?.hasCalendar == true, tasks.count < 3 {
            tasks.append(Task(id: "week", title: "Look at My Week", detail: "I'll tell you anything on your calendar that needs planning.",
                              message: "Look at my calendar for this week and tell me anything that needs planning."))
        }
        let work = (about["work"] ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
        if tasks.count < 3, !work.isEmpty {
            tasks.append(Task(id: "work", title: "Help With My Work", detail: "You said: \(work)",
                              message: "You know a bit about my work: \(work). What could you take off my plate this week? Suggest two things and wait for my okay."))
        }
        if tasks.count < 2 {
            tasks.append(Task(id: "today", title: "Plan My Day", detail: "Tell me what is on today, and I'll suggest an order.",
                              message: "Help me plan today. Ask me what is on it, then suggest an order."))
        }
        let hi = person.isEmpty ? "Hi." : "Hi \(person.split(separator: " ").first.map(String.init) ?? person)."
        let intro = "\(hi) I'm \(agent), your lead Zip. I hand work to the team and tell you when something needs you. "
            + "Here are a few things I can start on now. Pick any you like, and I'll begin."
        return FirstTasks(intro: intro, tasks: Array(tasks.prefix(3)))
    }
}

// MARK: Simple Look

/// Settings, Simple Look (#91: "many people will get tired of an animated clippy character... good call on having a
/// simple minimal option"): every Zip is drawn as its flat shape.
enum SimpleLook {
    static let key = "simpleLook"
}

extension AgentLook {
    /// The flat body closest to this one: the same shape where a flat one exists.
    var simple: AgentLook {
        if AgentBody.named(body).isFlat { return self }
        let near = ["gumdrop": "drop", "cube": "squircle"][body] ?? body
        let flat = "flat-" + near
        return AgentLook(body: AgentBody.all.contains { $0.id == flat } ? flat : "flat-o", outfit: "none")
    }
}

#if DEBUG
extension AppState {
    /// Screens for the fluid app (`-fixture fluid`, `fluid-stalled`, `fluid-failed`, `fluid-paused`, `first-tasks`).
    /// Open a chat with `-open school` (or clients, money, default); `-expandActivity` opens the steps.
    func loadFluidFixture(_ variant: String, at: (Double) -> Date) {
        let now = Date().timeIntervalSince1970
        func status(_ id: String, _ s: BotStatus?) {
            if let i = bots.firstIndex(where: { $0.id == id }) { bots[i].status = s }
        }
        func busy(_ id: String, _ steps: [String], stalled: Bool = false, words: String? = nil) {
            working[id] = steps.last ?? "Working"
            activity[id] = LiveActivity(since: now - 95, last_at: stalled ? now - 400 : now - 3, steps: steps,
                                        stalled: stalled, source: "app", words: words)
            if let i = bots.firstIndex(where: { $0.id == id }) { bots[i].busy = true }
        }
        board = .empty
        status("money", BotStatus(state: "paused", line: "Paused for your okay: email the landlord about the lease."))
        status("builder", BotStatus(state: "blocked", line: "Blocked: I need you to sign in to Netlify once."))
        status("career", BotStatus(state: "expired", line: "Its card closed with no answer.",
                                   what: "Send the cover letter to Harbor Homes"))
        status("clients", BotStatus(state: "failed", line: "Its last reply didn't finish.",
                                    words: "Draft a follow-up to Priya about the banner."))
        busy("default", ["Checking the board", "Asking Money", "Reading your transactions"])
        busy("school", ["Checking the board", "Opening elearning.lakeside.edu", "Signing in", "Reading a file"],
             stalled: variant == "fluid-stalled", words: "Check eLearning and tell me what is due in POL 101.")
        away = [AwayItem(bot_id: "default", name: "Zero", line: "Needs you: Book the dentist for next week.", needs: 1),
                AwayItem(bot_id: "builder", name: "Builder", line: "Finished: TrailNote crash report.", needs: 0)]
        chats["school"] = [
            ChatMessage(id: "f1", kind: .colin, text: "Check eLearning and tell me what is due in POL 101.", date: at(3)),
            ChatMessage(id: "f2", kind: .bot, text: "On it. I'll sign in and read the syllabus.", date: at(2.5)),
            ChatMessage(id: "local-f3", kind: .colin, text: "Also check ENG 102 while you're in there.", date: at(0.5)),
        ]
        queuedIDs = ["local-f3"]
        chats["clients"] = [
            ChatMessage(id: "c1", kind: .colin, text: "Draft a follow-up to Priya about the banner.", date: at(4)),
        ]
        problems["clients"] = TurnProblem(kind: .failed, line: "Clients couldn't finish that reply.",
                                          detail: "That reply ran into a problem. Try again in a moment.",
                                          words: "Draft a follow-up to Priya about the banner.")
        chats["career"] = [
            ChatMessage(id: "r1", kind: .colin, text: "Send the cover letter to Harbor Homes when it's ready.", date: at(200)),
            ChatMessage(id: "r2", kind: .bot, text: "It's ready. The card to send it is below.", date: at(190)),
            ChatMessage(id: "feed-77", kind: .checkin, text: "No answer in time, so Career didn't do this.", date: at(60),
                        label: "Send the cover letter to Harbor Homes", botID: "career"),
        ]
        chats["money"] = [
            ChatMessage(id: "m1", kind: .colin, text: "Tell the landlord the lease renewal is fine.", date: at(3)),
        ]
        if variant == "fluid-paused" {
            busy("money", ["Checking your email", "Writing an email"])
            board.requests = [SignInRequest(id: 31, bot_id: "money", site: "landlord@lakeside-homes.example",
                                            reason: "Email the landlord about the lease", created_at: now - 40,
                                            sensitive: false, why: "asked", kind: "send",
                                            target: "landlord@lakeside-homes.example",
                                            inputs: "To: landlord@lakeside-homes.example\nSubject: Lease renewal\n\nHi Pat, the renewal for next year is fine with me. Thanks, Sam",
                                            can_always: true, scope: "landlord@lakeside-homes.example")]
        }
        if variant == "first-tasks" {
            for i in bots.indices { bots[i].status = nil; bots[i].busy = false }
            working = [:]; activity = [:]; away = []
            chats["default"] = [
                ChatMessage(id: "o1", kind: .colin, text: "Please make me these Zips from their templates: Inbox (the inbox template).", date: at(1)),
                ChatMessage(id: "o2", kind: .bot, text: "Inbox is set up. It will sort your email each weekday morning.", date: at(0.5)),
            ]
            FirstTasks.save(FirstTasks(
                intro: "Hi Sam. I'm Zero, your lead Zip. I hand work to the team and tell you when something needs you. Here are a few things I can start on now. Pick any you like, and I'll begin.",
                tasks: [.init(id: "zip-inbox", title: "Start Inbox", detail: "Gets your email to zero: clears the noise, batches the quick reads and drafts replies in your voice.",
                              message: "Please have Inbox start on its first job now, and show me what it finds before anything goes out."),
                        .init(id: "week", title: "Look at My Week", detail: "I'll tell you anything on your calendar that needs planning.",
                              message: "Look at my calendar for this week and tell me anything that needs planning."),
                        .init(id: "work", title: "Help With My Work", detail: "You said: I run a small bakery",
                              message: "You know a bit about my work: I run a small bakery. What could you take off my plate this week? Suggest two things and wait for my okay.")]))
        }
    }
}
#endif
