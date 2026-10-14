import Foundation

enum ChatSync {
    struct Line: Equatable {
        let id: String
        let mine: Bool
        let text: String
        let date: Date?
    }

    static let slack: TimeInterval = 300

    static func normalized(_ s: String) -> String {
        s.split(whereSeparator: { $0.isWhitespace }).joined(separator: " ")
    }

    static func confirmed(local: [Line], history: [Line]) -> [String: String] {
        var used = Set<String>()
        var out: [String: String] = [:]
        for l in local.sorted(by: { ($0.date ?? .distantPast) < ($1.date ?? .distantPast) }) {
            let words = normalized(l.text)
            guard !words.isEmpty else { continue }
            let match = history.first { h in
                !used.contains(h.id) && h.mine == l.mine && normalized(h.text) == words
                    && (l.date == nil || h.date == nil || h.date! >= l.date!.addingTimeInterval(-slack))
            }
            if let match {
                used.insert(match.id)
                out[l.id] = match.id
            }
        }
        return out
    }

    static func live(card: Date?, hubWaiting: Bool?, lastSaid: Date?) -> Bool {
        if hubWaiting == false { return false }
        guard let lastSaid, let card else { return true }
        return card >= lastSaid.addingTimeInterval(-1)
    }

    static func paused(turnHeld: Bool?, cards: () -> Bool) -> Bool {
        if let turnHeld { return turnHeld }
        return cards()
    }

    static func streamIsStale(idleLooks: Int, sentAt: Date, now: Date = Date(), grace: TimeInterval = 10) -> Bool {
        idleLooks >= 2 && now.timeIntervalSince(sentAt) >= grace
    }
}

// MARK: Replies (Oct 10)

struct ReplyQuote: Codable, Hashable {
    var text: String
    var who: String
    var messageID: String? = nil
}

enum ReplyLogic {
    static let excerptMax = 300

    static func excerpt(_ text: String) -> String {
        let t = ChatSync.normalized(text)
        guard t.count > excerptMax else { return t }
        var cut = String(t.prefix(excerptMax))
        if let space = cut.lastIndex(of: " ") { cut = String(cut[..<space]) }
        while let last = cut.last, " .,;:".contains(last) { cut.removeLast() }
        return cut + "..."
    }

    static func original(of quote: ReplyQuote, in lines: [ChatSync.Line]) -> String? {
        if let id = quote.messageID, lines.contains(where: { $0.id == id }) { return id }
        var core = ChatSync.normalized(quote.text)
        if core.hasSuffix("...") { core = String(core.dropLast(3)).trimmingCharacters(in: .whitespaces) }
        guard !core.isEmpty else { return nil }
        let mine: Bool? = quote.who == "person" ? true : quote.who == "bot" ? false : nil
        return lines.last { l in
            (mine == nil || l.mine == mine!) && ChatSync.normalized(l.text).hasPrefix(core)
        }?.id
    }
}

// MARK: Card answers (Oct 10)

enum CardAnswers {
    struct Answer: Equatable {
        var text: String
        var sent: String? = nil
        var at: Date? = nil
        var loose = false
    }

    struct Line: Equatable {
        let id: String
        let mine: Bool
        let text: String
        let date: Date?
        var answer: Answer? = nil
    }

    static let lateNote = " (This answers a closed question."
    static let aboutPrefix = "About your question \""

    static func pairs(_ lines: [Line]) -> [String: String] {
        var used = Set<String>()
        var out: [String: String] = [:]
        for (i, card) in lines.enumerated() {
            guard let a = card.answer else { continue }
            for l in lines[(i + 1)...] where l.mine && l.answer == nil && !used.contains(l.id) {
                if let at = a.at, let d = l.date, d < at.addingTimeInterval(-ChatSync.slack) { continue }
                if a.loose && a.sent == nil {
                    if let at = a.at, let d = l.date, d > at.addingTimeInterval(ChatSync.slack) { break }
                } else if !matches(l.text, a) {
                    continue
                }
                used.insert(l.id)
                out[card.id] = l.id
                break
            }
        }
        return out
    }

    static func matches(_ message: String, _ a: Answer) -> Bool {
        let m = ChatSync.normalized(message)
        if let sent = a.sent, !ChatSync.normalized(sent).isEmpty { return m == ChatSync.normalized(sent) }
        let pick = ChatSync.normalized(a.text)
        guard !pick.isEmpty else { return false }
        if m == pick || m.hasPrefix(pick + lateNote) { return true }
        if pick.count >= 500 && m.hasPrefix(pick) { return true }
        if m.hasPrefix(aboutPrefix) && (m.hasSuffix("\": " + pick) || m.contains("\": " + pick + lateNote)) { return true }
        return false
    }
}
