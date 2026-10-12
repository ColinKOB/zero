import Foundation
import SwiftUI

struct Bot: Codable, Identifiable, Hashable {
    let id: String
    let name: String
    let role: String
    let color: String
    let symbol: String
    var working: Int
    var needs_you: Int
    var busy: Bool
    var preview: String?
    var last_at: Double?
    /// Candy shape: taffy, round, square, bean, hex or gumdrop. Bots Taffy creates pick one.
    var shape: String? = nil
    /// True for bots Taffy created; those can be removed from the app.
    var custom: Bool? = nil
    /// The look Colin chose in Change Look; nil gives the Zip a body from its id or color (Bot.agentLook).
    var look: ZipLook? = nil
    /// Alerts from this bot are held until then.
    var snoozed_until: Double? = nil
    var snoozedUntil: Date? {
        guard let s = snoozed_until, s > Date().timeIntervalSince1970 else { return nil }
        return Date(timeIntervalSince1970: s)
    }

    /// Whether this Zip's finished work and requests send a notification (Notify Me When It Finishes or Needs Me).
    /// The hub keeps it so the iPhone and the Mac agree; nil, from a hub that predates the switch, counts as on.
    var notify: Bool? = nil
    var notifies: Bool { notify ?? true }
    /// Which kinds of news notify when the switch is on (Model/Fluid.swift); nil from an older hub means all.
    var notify_kinds: NotifyKinds? = nil
    /// The Zip's message tone (Views/MessageTones.swift); nil from an older hub.
    var tone: String? = nil
    /// One honest line for the row: paused, blocked, a failed reply or a card that closed (Model/Fluid.swift).
    var status: BotStatus? = nil

    var lastDate: Date? { last_at.map { Date(timeIntervalSince1970: $0) } }

    var tint: Color { Color(hex: color) }
    var isLead: Bool { id == "default" }
}

struct ChatMessage: Identifiable, Hashable, Codable {
    enum Kind: String, Hashable, Codable { case colin, bot, boardUpdate, checkin, status, error, image, call, team, note, choices, login, account, event, macAsk, step, workLog }
    let id: String
    var kind: Kind
    var text: String
    var date: Date?
    /// For check-ins: the job's name and which bot ran it.
    var label: String? = nil
    var botID: String? = nil
    /// For pictures a bot shared: the hub's media id and the picture's shape.
    var mediaID: String? = nil
    var aspect: CGFloat? = nil
    /// Tapbacks on this message, from Colin and from the bot.
    var reactions: [Reaction] = []
    /// For messages between bots: who sent it to whom, and how.
    var team: TeamItem? = nil
    /// A step (a sign-in or action) a Zip took while answering a teammate: the team_msgs id of that question
    /// (feed step_of). ChatTimeline folds steps into one work log row between the question and its answer.
    var stepOf: Int? = nil
    /// A work log row: the steps it lists and how long the answer took (ChatTimeline).
    var steps: [WorkStep]? = nil
    var workedFor: TimeInterval? = nil
    /// A step's short label from the hub ("Signed in to eLearning"); its raw title stays in `label` for Show Original.
    var plainLabel: String? = nil
    /// For quiet notes with more to show: each step, listed when Colin taps the note.
    var details: [String] = []
    /// For a choice card or a sign-in card: the hub's feed id, to answer it.
    var feedID: Int? = nil
    /// For a question Zero raised on its own: what it found, and what came of Colin's answer.
    var question: QuestionCard? = nil
    /// For an open sign-in or account card: whether the Zip is waiting on it now (the hub's word; nil from older
    /// hubs). An older card stays in the chat to use, but does not mark the Zip as paused.
    var waiting: Bool? = nil
    /// A reply the hub shortened (Keep Replies Short): `text` is the short version and this is the reply as written.
    var full: String? = nil
    /// A reply the backup model gave (the Zip's own model was out of reach): the chat says so in a quiet line.
    var backup: Bool? = nil
    /// The person's reply to a specific message: the quote shown above their bubble (ChatSync.swift, Replies).
    var replyTo: ReplyQuote? = nil
    /// For a Zip's request to use the Mac: the request as the Mac shows it, to allow or decline from here.
    var macAsk: MacRequest? = nil
    /// For an answered choice card: the person's answer. The card shows as that answer in his own bubble, with the
    /// question as a small caption above it (Views/TeamMessage.swift, CardAnswerView).
    var answer: CardAnswer? = nil
}

/// What the person answered on a choice card (Oct 10). Colin: "for it to click that option and for it to not leave
/// the card, but the card transform." The message that carried the answer to the Zip is the same words, so the chat
/// shows it once, as the card's answer bubble (ChatSync.swift, CardAnswers).
struct CardAnswer: Codable, Hashable {
    /// The option he tapped or the words he typed, as his answer bubble shows them.
    var text: String
    /// The exact words that went to the Zip as his message, when known (the hub's `sent` on a question from Zero).
    var sent: String? = nil
    /// When he answered.
    var at: Date? = nil
    /// Words went to the Zip, but the card does not say which (a question from Zero answered before the hub kept them).
    var loose = false
}

/// A question Zero raised on its own (the hub's notices.py), like Grok Bot's proactive messages: what was found,
/// one short question with tap answers, and a follow-through line once Colin answers. Unlike other choice cards it
/// stays in the chat after he answers, with his answer checked.
struct QuestionCard: Codable, Hashable {
    let notice: Int
    var evidence: String
    var state: String              // open | picked | dismissed | expired (shown as Closed; still answerable)
    var picked: String? = nil
    var follow: String? = nil
    var detail: QuestionDetail? = nil
    /// A Zip already prepared the exact send (the hub's prepared.py): who prepared it and who it goes to. The first
    /// option approves that one message once; the detail shows exactly what will be sent.
    var prepared: PreparedSend? = nil

    var isOpen: Bool { state == "open" }
}

struct PreparedSend: Codable, Hashable {
    let by: String
    let to: String
}

/// What a question shows under it on request: the draft that would be sent, or the summary Approve covers.
struct QuestionDetail: Codable, Hashable {
    let label: String
    let text: String
}

/// Settings, Questions From Zero: proactivity on or off, and each kind of question (the hub's /api/proactivity).
struct ProactivityStatus: Decodable {
    var enabled: Bool
    var noticers: [Noticer]
    var stopped: [Stopped]
    let limits: Limits
    let today: Today
    let halved: Bool

    struct Noticer: Decodable, Identifiable, Hashable {
        let id: String
        let name: String
        let detail: String
        var mode: String           // on | digest | off
        let why: String
        let asked: Int
        let answered: Int
        let last: String?
        var state: String? = nil       // Asking, Morning brief only, Off
        var rate: Int? = nil           // percent of asked that he answered, two weeks
        var last_at: Double? = nil
    }

    /// Where the questions read from: the hourly calendar and Trello copy.
    struct Source: Decodable, Identifiable, Hashable {
        let id: String
        let name: String
        let state: String              // ok, waiting, failed, not_connected, chatgpt
        let at: Double?
        let count: Int?
    }

    struct Busy: Decodable, Hashable {
        let title: String
        let until: Double
    }

    struct Answered: Decodable, Hashable {
        let pushed: Int
        let answered: Int
        let rate: Int?
    }

    var sources: [Source] = []
    var busy: Busy? = nil
    var answered: Answered? = nil
    /// The home's time zone ("America/Chicago"), which the quiet hours in `limits` are in. Older hubs leave it out.
    var timezone: String? = nil

    enum CodingKeys: String, CodingKey {
        case enabled, noticers, stopped, limits, today, halved, sources, busy, answered, timezone
    }

    init(enabled: Bool, noticers: [Noticer], stopped: [Stopped], limits: Limits, today: Today, halved: Bool,
         sources: [Source] = [], busy: Busy? = nil, answered: Answered? = nil) {
        self.enabled = enabled; self.noticers = noticers; self.stopped = stopped; self.limits = limits
        self.today = today; self.halved = halved; self.sources = sources; self.busy = busy; self.answered = answered
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        enabled = try c.decode(Bool.self, forKey: .enabled)
        noticers = try c.decode([Noticer].self, forKey: .noticers)
        stopped = try c.decode([Stopped].self, forKey: .stopped)
        limits = try c.decode(Limits.self, forKey: .limits)
        today = try c.decode(Today.self, forKey: .today)
        halved = try c.decode(Bool.self, forKey: .halved)
        sources = (try? c.decodeIfPresent([Source].self, forKey: .sources)) ?? []
        busy = try? c.decodeIfPresent(Busy.self, forKey: .busy)
        answered = try? c.decodeIfPresent(Answered.self, forKey: .answered)
        timezone = try? c.decodeIfPresent(String.self, forKey: .timezone)
    }

    struct Stopped: Decodable, Identifiable, Hashable {
        let id: Int
        let label: String
        let at: Double
    }

    struct Limits: Decodable {
        let daily: Int
        let weekly: Int
        let spacing_hours: Double
        let quiet_start: Int
        let quiet_end: Int
    }

    struct Today: Decodable {
        let pushed: Int
        let checks: Int
        let checks_cap: Int
        var prepared: Int? = nil
        var prepared_cap: Int? = nil
        var quiet_looks: Int? = nil
        var quiet_looks_cap: Int? = nil
    }
}

/// The body of an "event" feed item: a small line in a Zip's chat. "routine" names a scheduled job the Zip just
/// created; "memory" says the Zip saved something to its memory during a turn.
struct ChatEvent: Decodable {
    let event: String
    var name: String? = nil
    var when: String? = nil
}

/// A Zip waiting on teammates' answers right now (the hub's feed): its chat shows "Pinging ..." until they reply.
struct PingInfo: Decodable, Hashable {
    let from: String
    let to: [String]
}

/// The body of a "team" feed item: one bot's message to another, shown in Colin's chats.
struct TeamItem: Codable, Hashable {
    let from: String
    let to: String
    let kind: String        // ask | reply | answer | tell | note
    let text: String
    var question: String? = nil
    /// The same message as two coworkers would say it (hub teamvoice.py); `text` stays the exact original.
    var plain: String? = nil
    /// The question's id, shared by the question, its answer and the steps taken to answer it.
    var msg: Int? = nil
}

/// One row of a work log: what a Zip did while answering a teammate, like "Signed in to elearning.lakeview.test".
struct WorkStep: Codable, Hashable {
    let symbol: String
    var label: String
    /// The raw line, when a short label stands in for it (Show Original).
    var raw: String? = nil

    /// A short label and an SF Symbol for a step, from the feed item's title and text. The action line repeats the
    /// sign-in ("Signed in to x.com to answer Zero"), so its tail is dropped and the two read the same.
    static func from(title: String?, text: String) -> WorkStep {
        var label = (title ?? "").hasPrefix("Done") || (title ?? "").isEmpty ? text : (title ?? text)
        if let r = label.range(of: " to answer ", options: .backwards) { label = String(label[..<r.lowerBound]) }
        label = label.replacingOccurrences(of: "://www.", with: "://").replacingOccurrences(of: " www.", with: " ")
        label = label.trimmingCharacters(in: CharacterSet(charactersIn: " ."))
        let low = label.lowercased()
        let symbol: String
        if low.hasPrefix("signed in") { symbol = "key" }
        else if low.hasPrefix("opened") || low.contains("website") { symbol = "safari" }
        else if low.contains("mail") || low.contains("inbox") { symbol = "envelope" }
        else if low.hasPrefix("sent") { symbol = "paperplane" }
        else if low.hasPrefix("ran") || low.contains("command") { symbol = "terminal" }
        else if low.contains("calendar") { symbol = "calendar" }
        else if low.contains("file") || low.hasPrefix("saved") { symbol = "doc" }
        else { symbol = "checkmark.circle" }
        return WorkStep(symbol: symbol, label: label)
    }
}

/// A tapback, like in Apple Messages.
struct Reaction: Codable, Hashable {
    let who: String     // "colin" or "bot"
    let kind: String    // heart, like, dislike, laugh, emphasize, question

    static let kinds = ["heart", "like", "dislike", "laugh", "emphasize", "question"]

    static func symbol(_ kind: String) -> String {
        switch kind {
        case "heart": return "heart.fill"
        case "like": return "hand.thumbsup.fill"
        case "dislike": return "hand.thumbsdown.fill"
        case "emphasize": return "exclamationmark.2"
        case "question": return "questionmark"
        default: return ""    // laugh is drawn as "HA HA", like Messages
        }
    }

    static func name(_ kind: String) -> String {
        ["heart": "Heart", "like": "Like", "dislike": "Dislike", "laugh": "Laugh", "emphasize": "Emphasize",
         "question": "Question"][kind] ?? kind.capitalized
    }
}

struct HermesMessage: Decodable {
    let id: AnyCodableID?
    let role: String
    let text: String
    let ts: Double?
    /// What the message is, decided by the hub for every screen alike: colin, bot, board_update, board_reply,
    /// hub_note, wrap_up, wrap_up_reply, call_turn, call_reply, team, team_reply.
    let cls: String?
    /// The text as Colin should see it (the hub's quiet notes taken off).
    let display: String?
    /// For a reply the hub shortened: the reply as written (`display` is then the short version).
    let full: String?
    /// For the person's reply to a specific message: the quote ({"text", "who"}) the hub took off `display`.
    let replyTo: ReplyQuote?
    /// For a reply the backup model gave: true (hermes.mark_backup). Older hubs leave it out.
    let backup: Bool?

    enum CodingKeys: String, CodingKey {
        case id, role, text, ts, cls = "class", display, full, replyTo = "reply_to", backup
    }

    /// Tolerant: a timestamp that is not a number becomes nil instead of failing the whole chat.
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try? c.decode(AnyCodableID.self, forKey: .id)
        role = (try? c.decode(String.self, forKey: .role)) ?? "assistant"
        text = (try? c.decode(String.self, forKey: .text)) ?? ""
        cls = try? c.decode(String.self, forKey: .cls)
        display = try? c.decode(String.self, forKey: .display)
        full = try? c.decode(String.self, forKey: .full)
        replyTo = try? c.decode(ReplyQuote.self, forKey: .replyTo)
        backup = try? c.decode(Bool.self, forKey: .backup)
        if let d = try? c.decode(Double.self, forKey: .ts) { ts = d }
        else if let s = try? c.decode(String.self, forKey: .ts), let d = ISO8601DateFormatter().date(from: s) { ts = d.timeIntervalSince1970 }
        else { ts = nil }
    }
}

/// Hermes message ids can be numbers or strings.
struct AnyCodableID: Codable, Hashable {
    let value: String
    init(from decoder: Decoder) throws {
        let c = try decoder.singleValueContainer()
        if let i = try? c.decode(Int.self) { value = String(i) } else { value = (try? c.decode(String.self)) ?? UUID().uuidString }
    }
    func encode(to encoder: Encoder) throws { var c = encoder.singleValueContainer(); try c.encode(value) }
}

struct Attachment: Codable, Hashable, Identifiable {
    let id: Int
    let filename: String
    let size: Int?
    let content_type: String?
}

struct TaskItem: Codable, Identifiable, Hashable {
    let id: String
    let title: String
    let body: String?
    let assignee: String?
    let status: String
    let created_by: String?
    let created_at: Double?
    let started_at: Double?
    let completed_at: Double?
    let summary: String?
    let error: String?
    let block_reason: String?
    let attachments: [Attachment]

    var created: Date? { created_at.map { Date(timeIntervalSince1970: $0) } }
    var completed: Date? { completed_at.map { Date(timeIntervalSince1970: $0) } }
    var needsYou: Bool { status == "blocked" || status == "review" }

    /// A request to sign in to a site: "Sign in to …" with a `URL: https://…` line.
    var signInURL: URL? {
        guard status == "blocked", title.lowercased().hasPrefix("sign in") else { return nil }
        let text = [block_reason, body].compactMap { $0 }.joined(separator: "\n")
        guard let r = text.range(of: #"https?://[^\s)]+"#, options: .regularExpression) else { return nil }
        return URL(string: String(text[r]))
    }

    /// The summary only when it adds something beyond the question itself.
    var distinctSummary: String? {
        guard let s = summary?.trimmingCharacters(in: .whitespacesAndNewlines), !s.isEmpty else { return nil }
        return s == block_reason?.trimmingCharacters(in: .whitespacesAndNewlines) ? nil : s
    }
}

struct Board: Codable {
    var needs_you: [TaskItem]
    var working: [TaskItem]
    var done: [TaskItem]
    /// Bots waiting for Colin's okay to sign in to a site.
    var requests: [SignInRequest]? = nil
    static let empty = Board(needs_you: [], working: [], done: [])
}

/// A bot asking to sign in to a site with a saved login.
struct SignInRequest: Codable, Identifiable, Hashable {
    let id: Int
    let bot_id: String
    let site: String
    let reason: String
    let created_at: Double
    let sensitive: Bool?
    /// Why the card came up: mismatch, ask, sensitive, scheduled or background (empty on older cards).
    let why: String?
    /// What the Zip was about to do: signin, navigate (open a site where it may still be signed in), or an
    /// action that needs Colin's yes: send, publish, delete, submit, book, account, write.
    var kind: String? = nil
    /// Action cards: who or what the action is aimed at, and exactly what will be sent or run.
    var target: String? = nil
    var inputs: String? = nil
    /// What the Zip had just read, when the idea may have come from there.
    var excerpt: String? = nil
    /// Whether Always Allow is offered, and what it would cover (one person, one routine).
    var can_always: Bool? = nil
    var scope: String? = nil
    /// The exact command behind an action card, shown under Details.
    var details: String? = nil
    /// The scheduled job a card came from, when it came from one.
    var routine_name: String? = nil
    /// Where a card from outside Colin's chat came from, in plain words: a scheduled job, a board task, a
    /// teammate's question, or "outside your chat with <Zip>" (hub's card_origin; older hubs leave it out).
    var origin: String? = nil
    /// Set when one of Colin's Ask First rules paused something the Zip would just do: the rule in a sentence.
    var paused_by: String? = nil

    var isAction: Bool { !["signin", "navigate", ""].contains(kind ?? "signin") }
}

/// One of Colin's Custom Rules. An Allow rule comes from Always Allow on a card: one Zip, one kind of action, one
/// recipient or routine. An Ask First rule is his own: a Zip (or every Zip, "*") asks before something it would
/// otherwise just do. Older hubs send only the Allow rules, without `effect` or `sentence`.
struct PermissionRule: Codable, Identifiable, Hashable {
    let id: Int
    let bot_id: String
    let kind: String
    let scope: String
    let created_at: Double
    let last_used: Double?
    let uses: Int
    let stale: Bool?
    var effect: String? = nil
    var sentence: String? = nil
    /// Ask First rules: what it covers (push, change, send) and where ("*" for anywhere).
    var what: String? = nil
    var `where`: String? = nil

    var isAskFirst: Bool { effect == "ask" }
}

/// What the Ask First form offers, in the hub's words.
struct RuleChoices: Codable, Hashable {
    struct Option: Codable, Hashable, Identifiable {
        let id: String
        let title: String
    }
    let what: [Option]
    let places: [Option]
}

/// Something a Zip did and told Colin about, or did with his okay.
struct ActionRecord: Codable, Identifiable, Hashable {
    let id: Int
    let bot_id: String
    let kind: String
    let summary: String
    let how: String
    let at: Double
    var date: Date { Date(timeIntervalSince1970: at) }
}

/// Colin's standing choice for one bot on one site.
struct SignInPermission: Codable, Identifiable, Hashable {
    let bot_id: String
    let site: String
    var mode: String
    let sensitive: Bool?
    var id: String { bot_id + "|" + site }

    static let modes: [(id: String, title: String)] = [
        ("when_asked", "When I Ask"), ("ask", "Ask Me First"), ("anytime", "Anytime")
    ]
}

/// One sign-in that happened, for the history list.
struct SignInRecord: Codable, Identifiable, Hashable {
    let id: Int
    let bot_id: String
    let site: String
    let reason: String
    let how: String
    let at: Double
    var date: Date { Date(timeIntervalSince1970: at) }
}

struct FeedItem: Codable, Identifiable, Hashable {
    let id: Int
    let kind: String
    let bot_id: String?
    let task_id: String?
    let title: String?
    let body: String?
    let created_at: Double
    /// A sign-in or action taken to answer a teammate's question: that question's id (hub feed.step_of).
    var step_of: Int? = nil
    /// That step's short label (hub teamvoice.py); `title` keeps the raw one.
    var plain_title: String? = nil

    var date: Date { Date(timeIntervalSince1970: created_at) }
}

/// One event from a streamed turn.
struct TurnEvent: Decodable {
    let type: String
    let text: String?
    /// For the first event, "turn": the hub's id for this message's turn, to reattach to after a drop.
    let id: String?
    /// "stopped": true when Stop handed this queued message back unsent.
    var returned: Bool? = nil
}


/// What Taffy already knows and what is already set up, for the welcome flow.
struct AboutMe: Decodable {
    struct Note: Decodable, Hashable { let title: String; let text: String }
    struct Login: Decodable, Hashable { let bot_id: String; let site: String }
    struct Checkin: Decodable, Hashable { let bot_id: String; let name: String; let when: String }
    let name: String
    let notes: [Note]
    let connectors: [String]
    let alerts: String
    let logins: [Login]
    let checkins: [Checkin]
}


/// A Zip's saved look (hub looks.py): a rendered body and, for Zero's O, an outfit. Looks saved before the rendered
/// characters have a drawn shape and a color instead; those are read only to pick the closest body.
struct ZipLook: Codable, Hashable {
    var body: String? = nil
    var outfit: String? = nil
    var shape: String? = nil
    var color: String? = nil
}
