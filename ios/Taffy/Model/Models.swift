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
    var shape: String? = nil
    var custom: Bool? = nil
    var look: ZipLook? = nil
    var snoozed_until: Double? = nil
    var snoozedUntil: Date? {
        guard let s = snoozed_until, s > Date().timeIntervalSince1970 else { return nil }
        return Date(timeIntervalSince1970: s)
    }

    var notify: Bool? = nil
    var notifies: Bool { notify ?? true }
    var notify_kinds: NotifyKinds? = nil
    var tone: String? = nil
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
    var label: String? = nil
    var botID: String? = nil
    var mediaID: String? = nil
    var aspect: CGFloat? = nil
    var reactions: [Reaction] = []
    var team: TeamItem? = nil
    var stepOf: Int? = nil
    var steps: [WorkStep]? = nil
    var workedFor: TimeInterval? = nil
    var plainLabel: String? = nil
    var details: [String] = []
    var feedID: Int? = nil
    var question: QuestionCard? = nil
    var waiting: Bool? = nil
    var full: String? = nil
    var backup: Bool? = nil
    var replyTo: ReplyQuote? = nil
    var macAsk: MacRequest? = nil
    var answer: CardAnswer? = nil
}

struct CardAnswer: Codable, Hashable {
    var text: String
    var sent: String? = nil
    var at: Date? = nil
    var loose = false
}

struct QuestionCard: Codable, Hashable {
    let notice: Int
    var evidence: String
    var state: String
    var picked: String? = nil
    var follow: String? = nil
    var detail: QuestionDetail? = nil
    var prepared: PreparedSend? = nil

    var isOpen: Bool { state == "open" }
}

struct PreparedSend: Codable, Hashable {
    let by: String
    let to: String
}

struct QuestionDetail: Codable, Hashable {
    let label: String
    let text: String
}

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
        var mode: String
        let why: String
        let asked: Int
        let answered: Int
        let last: String?
        var state: String? = nil
        var rate: Int? = nil
        var last_at: Double? = nil
    }

    struct Source: Decodable, Identifiable, Hashable {
        let id: String
        let name: String
        let state: String
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

struct ChatEvent: Decodable {
    let event: String
    var name: String? = nil
    var when: String? = nil
}

struct PingInfo: Decodable, Hashable {
    let from: String
    let to: [String]
}

struct TeamItem: Codable, Hashable {
    let from: String
    let to: String
    let kind: String
    let text: String
    var question: String? = nil
    var plain: String? = nil
    var msg: Int? = nil
}

struct WorkStep: Codable, Hashable {
    let symbol: String
    var label: String
    var raw: String? = nil

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

struct Reaction: Codable, Hashable {
    let who: String
    let kind: String

    static let kinds = ["heart", "like", "dislike", "laugh", "emphasize", "question"]

    static func symbol(_ kind: String) -> String {
        switch kind {
        case "heart": return "heart.fill"
        case "like": return "hand.thumbsup.fill"
        case "dislike": return "hand.thumbsdown.fill"
        case "emphasize": return "exclamationmark.2"
        case "question": return "questionmark"
        default: return ""
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
    let cls: String?
    let display: String?
    let full: String?
    let replyTo: ReplyQuote?
    let backup: Bool?

    enum CodingKeys: String, CodingKey {
        case id, role, text, ts, cls = "class", display, full, replyTo = "reply_to", backup
    }

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

    var signInURL: URL? {
        guard status == "blocked", title.lowercased().hasPrefix("sign in") else { return nil }
        let text = [block_reason, body].compactMap { $0 }.joined(separator: "\n")
        guard let r = text.range(of: #"https?://[^\s)]+"#, options: .regularExpression) else { return nil }
        return URL(string: String(text[r]))
    }

    var distinctSummary: String? {
        guard let s = summary?.trimmingCharacters(in: .whitespacesAndNewlines), !s.isEmpty else { return nil }
        return s == block_reason?.trimmingCharacters(in: .whitespacesAndNewlines) ? nil : s
    }
}

struct Board: Codable {
    var needs_you: [TaskItem]
    var working: [TaskItem]
    var done: [TaskItem]
    var requests: [SignInRequest]? = nil
    static let empty = Board(needs_you: [], working: [], done: [])
}

struct SignInRequest: Codable, Identifiable, Hashable {
    let id: Int
    let bot_id: String
    let site: String
    let reason: String
    let created_at: Double
    let sensitive: Bool?
    let why: String?
    var kind: String? = nil
    var target: String? = nil
    var inputs: String? = nil
    var excerpt: String? = nil
    var can_always: Bool? = nil
    var scope: String? = nil
    var details: String? = nil
    var routine_name: String? = nil
    var origin: String? = nil
    var paused_by: String? = nil

    var isAction: Bool { !["signin", "navigate", ""].contains(kind ?? "signin") }
}

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
    var what: String? = nil
    var `where`: String? = nil

    var isAskFirst: Bool { effect == "ask" }
}

struct RuleChoices: Codable, Hashable {
    struct Option: Codable, Hashable, Identifiable {
        let id: String
        let title: String
    }
    let what: [Option]
    let places: [Option]
}

struct ActionRecord: Codable, Identifiable, Hashable {
    let id: Int
    let bot_id: String
    let kind: String
    let summary: String
    let how: String
    let at: Double
    var date: Date { Date(timeIntervalSince1970: at) }
}

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
    var step_of: Int? = nil
    var plain_title: String? = nil

    var date: Date { Date(timeIntervalSince1970: created_at) }
}

struct TurnEvent: Decodable {
    let type: String
    let text: String?
    let id: String?
    var returned: Bool? = nil
}


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


struct ZipLook: Codable, Hashable {
    var body: String? = nil
    var outfit: String? = nil
    var shape: String? = nil
    var color: String? = nil
}
