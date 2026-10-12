import Foundation

/// The chat's bookkeeping between what the phone sent and what the hub's history says (Oct 9, 11:18 p.m.). Colin
/// wrote "I think I actually bought them over the phone." while an old sign-in card was open. The hub delivered it
/// and Zero answered in 19 seconds, but the app kept his message as sent-and-waiting, never showed the reply, and kept
/// "Zero is paused for your okay" under the card. Two rules, kept here with no UI so they can be tested on their own
/// (ios/LogicTests):
///
/// - `confirmed`: the history is the truth. A message the person sent counts as delivered the moment a history load
///   shows it, whether or not the reply stream ever said so (a dropped stream, a hub restart, a phone that slept).
/// - `live`: a card marks a Zip as paused only while the conversation has not moved past it. Once the person writes
///   again, an older card stays in the chat to use, but nothing waits on it.
enum ChatSync {
    /// One line of a chat, as much as these rules need.
    struct Line: Equatable {
        let id: String
        /// The person's own message.
        let mine: Bool
        let text: String
        let date: Date?
    }

    /// How far apart the phone's clock and the hub's may be when matching a message to its saved copy.
    static let slack: TimeInterval = 300

    static func normalized(_ s: String) -> String {
        s.split(whereSeparator: { $0.isWhitespace }).joined(separator: " ")
    }

    /// The lines on the phone that the history already holds: phone id to history id. A message sent from the phone
    /// matches the person's message in the history with the same words, sent no earlier than a few minutes before
    /// it; a reply shown from the stream matches the Zip's message with the same words. Each history line matches
    /// at most one phone line, oldest first, so the same words sent twice need two saved copies.
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

    /// Whether a card still marks its Zip as paused. `hubWaiting` is the hub's own word on that card (nil from a hub
    /// that does not say); `lastSaid` is when the person last wrote in this chat. A card raised before the person's
    /// latest message is one the conversation moved past.
    static func live(card: Date?, hubWaiting: Bool?, lastSaid: Date?) -> Bool {
        if hubWaiting == false { return false }
        guard let lastSaid, let card else { return true }
        return card >= lastSaid.addingTimeInterval(-1)   // the hub keeps whole seconds
    }

    /// Whether the Zip shows as paused for the person's okay. While one of its chat turns runs, the hub says whether
    /// that turn is held on a card (`turnHeld`, the live activity's `waiting`): held is paused, and anything else is
    /// working, whatever cards wait for other work. Otherwise a scheduled job's card (open up to a day) or one from a
    /// board relay drew "paused for your okay" over the person's own reply in progress, with no typing dots and no
    /// Stop (night review 2). With no word (no turn running, or a hub from before Oct 10), the open cards decide.
    static func paused(turnHeld: Bool?, cards: () -> Bool) -> Bool {
        if let turnHeld { return turnHeld }
        return cards()
    }

    /// Whether the person's message is still on its way after the reply stream stopped telling us anything: the hub
    /// says the Zip is idle on two looks in a row, and the message went out more than `grace` seconds ago. Then the
    /// history decides, instead of a stream that may never speak again.
    static func streamIsStale(idleLooks: Int, sentAt: Date, now: Date = Date(), grace: TimeInterval = 10) -> Bool {
        idleLooks >= 2 && now.timeIntervalSince(sentAt) >= grace
    }
}

// MARK: Replies (Oct 10)
//
// Colin: "i should also be able to reply to specific messages like you can on iphone with the messages app". The
// person swipes a bubble (or picks Reply) and the message they send carries a short quote of the one they meant. The
// hub hands the quote to the Zip on a quiet first line and returns it as `reply_to` in the history, so every device
// shows it above the person's bubble.

/// The message a reply points at: a one-line excerpt and whose it was. `who` is "bot" (the Zip's message), "person"
/// (the person's own earlier message) or "other" (a team update, a call or a note). `messageID` is known on the
/// device that sent the reply; after a reload the excerpt finds the original.
struct ReplyQuote: Codable, Hashable {
    var text: String
    var who: String
    var messageID: String? = nil
}

enum ReplyLogic {
    /// The hub cuts the quote it hands the Zip at about this many characters (history.REPLY_EXCERPT_MAX).
    static let excerptMax = 300

    /// The quoted message on one line, cut at a word near `excerptMax` characters.
    static func excerpt(_ text: String) -> String {
        let t = ChatSync.normalized(text)
        guard t.count > excerptMax else { return t }
        var cut = String(t.prefix(excerptMax))
        if let space = cut.lastIndex(of: " ") { cut = String(cut[..<space]) }
        while let last = cut.last, " .,;:".contains(last) { cut.removeLast() }
        return cut + "..."
    }

    /// The message a quote points at, among the lines before the reply (oldest first): the one with its id, or else
    /// the latest whose words begin with the quote, from the same side when the quote says whose it was.
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
//
// Colin tapped "Add both to Trello" on a Zip's card and got a second black "Add both to Trello" bubble under it. Now
// the card itself turns into his answer bubble, and the message that carried the answer to the Zip (the same words,
// sent right after the card) is not shown again. The pairing is done when the chat is drawn, so it holds for a message
// still on its way, after the history reloads, and on every device.

enum CardAnswers {
    /// An answered card, as much as the pairing needs.
    struct Answer: Equatable {
        /// The option he tapped or the words he typed.
        var text: String
        /// The exact words that went to the Zip, when known.
        var sent: String? = nil
        /// When he answered (the hub's clock, or the phone's for an answer on its way).
        var at: Date? = nil
        /// Words went to the Zip, but not which (a question from Zero answered before the hub kept them): his first
        /// message within a few minutes of the answer is the one.
        var loose = false
    }

    /// One line of a chat: an answered card, one of the person's own messages, or anything else.
    struct Line: Equatable {
        let id: String
        let mine: Bool
        let text: String
        let date: Date?
        var answer: Answer? = nil
    }

    /// The hub's note after an answer to a closed question whose date has passed (notices._late_say).
    static let lateNote = " (This answers a closed question."
    /// How the hub hands Zero a typed answer to one of its own questions (notices._custom).
    static let aboutPrefix = "About your question \""

    /// Answered card id to the id of the person's message that carried the answer. Each card takes the first message
    /// of his after it, sent no earlier than a few minutes before the answer, whose words are the answer; each message
    /// pairs with one card at most.
    static func pairs(_ lines: [Line]) -> [String: String] {
        var used = Set<String>()
        var out: [String: String] = [:]
        for (i, card) in lines.enumerated() {
            guard let a = card.answer else { continue }
            for l in lines[(i + 1)...] where l.mine && l.answer == nil && !used.contains(l.id) {
                if let at = a.at, let d = l.date, d < at.addingTimeInterval(-ChatSync.slack) { continue }
                if a.loose && a.sent == nil {
                    // Only a message sent within a few minutes of the answer; anything later is his own new words.
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

    /// Whether a message of his carries this answer: the exact words sent when the card says them; otherwise the
    /// answer itself, the answer with the hub's note about a closed question, or a typed answer as the hub hands it
    /// to Zero ("About your question "...": his words"). Spacing never matters.
    static func matches(_ message: String, _ a: Answer) -> Bool {
        let m = ChatSync.normalized(message)
        if let sent = a.sent, !ChatSync.normalized(sent).isEmpty { return m == ChatSync.normalized(sent) }
        let pick = ChatSync.normalized(a.text)
        guard !pick.isEmpty else { return false }
        if m == pick || m.hasPrefix(pick + lateNote) { return true }
        // The hub keeps a Zip card's answer to 500 characters; the message has all of it.
        if pick.count >= 500 && m.hasPrefix(pick) { return true }
        if m.hasPrefix(aboutPrefix) && (m.hasSuffix("\": " + pick) || m.contains("\": " + pick + lateNote)) { return true }
        return false
    }
}
