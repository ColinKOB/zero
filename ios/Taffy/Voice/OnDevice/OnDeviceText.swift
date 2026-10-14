import Foundation

/// One sentence to speak, and the pause that should follow it when another comes after.
struct SpokenSentence: Equatable {
    let text: String
    let paragraphEnd: Bool

    var pauseAfter: Double { SpokenPause.after(text, paragraphEnd: paragraphEnd) }
}

/// The pauses the app puts between sentences (measured from the voices themselves on the Mac).
enum SpokenPause {
    static let sentence = 0.25
    static let comma = 0.24
    static let paragraphExtra = 0.15
    /// Pocket TTS pauses a little less between sentences than these (0.12 to 0.19 s measured).
    static let pocketShare = 0.8

    static func after(_ text: String, paragraphEnd: Bool) -> Double {
        guard let last = text.trimmingCharacters(in: .whitespacesAndNewlines)
            .last(where: { !"\"'\u{201D}\u{2019})".contains($0) })
        else { return sentence }
        let base: Double
        switch last {
        case ".", "!", "?", "\u{2026}": base = sentence
        case ",", ";", ":", "\u{2014}": base = comma
        default: base = 0.08
        }
        return base + (paragraphEnd ? paragraphExtra : 0)
    }

    /// Silence to put before a sentence that follows another. `trailing` is the silence the previous sentence
    /// already ended with, and `late` is time already lost waiting for this one.
    static func pad(previous: Double, trailing: Double, late: Double) -> Double {
        max(0, previous * pocketShare - trailing - late)
    }
}

/// Splits streamed text into whole sentences. It cuts at commas only for a runaway sentence of over 60 words.
struct SentenceSplitter {
    private(set) var buffer = ""
    static let capWords = 60
    static let abbreviations: Set<String> = [
        "mr", "mrs", "ms", "dr", "st", "vs", "jr", "sr", "etc", "e.g", "i.e", "u.s", "a.m", "p.m", "approx", "dept", "mt", "ft",
    ]

    mutating func push(_ s: String) -> [SpokenSentence] {
        buffer += s
        var out: [SpokenSentence] = []
        while let (cut, para) = nextCut() {
            let idx = buffer.index(buffer.startIndex, offsetBy: cut)
            let text = String(buffer[..<idx]).trimmingCharacters(in: .whitespacesAndNewlines)
            buffer = String(buffer[idx...])
            if !text.isEmpty { out += QuestionSplit.sentences(text, paragraphEnd: para) }
        }
        return out
    }

    mutating func flush() -> [SpokenSentence] {
        let rest = buffer.trimmingCharacters(in: .whitespacesAndNewlines)
        buffer = ""
        return rest.isEmpty ? [] : QuestionSplit.sentences(rest, paragraphEnd: true)
    }

    private func nextCut() -> (Int, Bool)? {
        let chars = Array(buffer)
        var words = 0
        var inWord = false
        var lastComma: Int?
        var i = 0
        while i < chars.count {
            let ch = chars[i]
            if ch.isWhitespace {
                if inWord {
                    words += 1
                    inWord = false
                    if words >= Self.capWords { return (lastComma ?? i, false) }
                }
                if ch.isNewline && words > 0 { return (i, true) }
                i += 1
                continue
            }
            inWord = true
            switch ch {
            case ".", "?", "!", "\u{2026}":
                var j = i + 1
                while j < chars.count, "\"'\u{201D}\u{2019})".contains(chars[j]) { j += 1 }
                guard j < chars.count else { return nil }
                guard chars[j].isWhitespace else { break }
                if ch == ".", isAbbreviation(chars, endingBefore: i) { break }
                var k = j
                var para = false
                while k < chars.count, chars[k].isWhitespace {
                    if chars[k].isNewline { para = true }
                    k += 1
                }
                return (j, para)
            case ",", ";", ":":
                if i + 1 < chars.count, chars[i + 1].isWhitespace { lastComma = i + 1 }
            default:
                break
            }
            i += 1
        }
        return nil
    }

    private func isAbbreviation(_ chars: [Character], endingBefore i: Int) -> Bool {
        var s = i
        while s > 0, !chars[s - 1].isWhitespace { s -= 1 }
        let word = String(chars[s..<i]).lowercased().trimmingCharacters(in: CharacterSet(charactersIn: "(\"'"))
        if word.count == 1, word.first?.isLetter == true { return true }
        return Self.abbreviations.contains(word)
    }
}

/// Hands out the sentences of one reply in order, and removes an opening interjection from the first one.
struct ReplySentences {
    private var splitter = SentenceSplitter()
    private var ready: [SpokenSentence] = []
    private(set) var done = false
    private(set) var emitted = 0
    /// The interjection removed from the start of the reply, if any (for the call's saved events).
    private(set) var strippedOpening: String?

    mutating func push(_ s: String) { ready += splitter.push(s) }

    mutating func finish() {
        guard !done else { return }
        ready += splitter.flush()
        done = true
    }

    /// True when all text has arrived and every sentence has been handed out.
    var finished: Bool { done && ready.isEmpty }

    mutating func next() -> SpokenSentence? {
        // Safety net for the opening: drop "Oh, man." and similar, which the voice reads with a long gap.
        while emitted == 0, let first = ready.first {
            let (rest, cut) = stripLeadingInterjection(first.text)
            if let cut { strippedOpening = (strippedOpening.map { $0 + " " } ?? "") + cut }
            if rest.isEmpty { ready.removeFirst(); continue }
            ready[0] = SpokenSentence(text: rest, paragraphEnd: first.paragraphEnd)
            break
        }
        guard !ready.isEmpty else { return nil }
        emitted += 1
        return ready.removeFirst()
    }
}

/// Two questions run together with a comma ("How about you, how is your day going?") are spoken as one long
/// question with the rise only at the very end. A short question lead-in before a comma becomes its own question
/// when what follows is a question (Colin, Oct 9).
enum QuestionSplit {
    static let leads: Set<String> = ["how", "what", "and", "you", "do", "does", "did", "is", "are", "was", "were", "can",
                                     "could", "would", "will", "should", "have", "has", "any", "want", "ready", "sound",
                                     "sounds", "right", "okay", "ok", "remember", "know", "heard", "seen", "got"]
    static let starts: Set<String> = ["how", "what", "when", "where", "who", "why", "which", "do", "does", "did", "is",
                                      "are", "was", "were", "can", "could", "would", "will", "should", "have", "has",
                                      "any", "want", "anything", "shall"]

    static func sentences(_ text: String, paragraphEnd: Bool) -> [SpokenSentence] {
        let parts = split(text)
        return parts.enumerated().map { SpokenSentence(text: $0.element, paragraphEnd: paragraphEnd && $0.offset == parts.count - 1) }
    }

    static func split(_ text: String) -> [String] {
        guard text.hasSuffix("?"), let comma = text.firstIndex(of: ",") else { return [text] }
        let left = text[..<comma].trimmingCharacters(in: .whitespaces)
        let right = text[text.index(after: comma)...].trimmingCharacters(in: .whitespaces)
        func first(_ s: String) -> String {
            let w = s.split(whereSeparator: { $0.isWhitespace }).first.map(String.init) ?? ""
            return w.lowercased().split(whereSeparator: { $0 == "'" || $0 == "\u{2019}" }).first.map(String.init) ?? ""
        }
        let leftWords = left.split(whereSeparator: { $0.isWhitespace }).count
        let rightWords = right.split(whereSeparator: { $0.isWhitespace }).count
        guard (2...6).contains(leftWords), rightWords >= 3, leads.contains(first(left)), starts.contains(first(right))
        else { return [text] }
        let rest = right.prefix(1).uppercased() + right.dropFirst()
        return [left + "?"] + split(rest)
    }
}

/// Removes an opening interjection such as "Oh, man." or "Hmm," and returns the rest and what was cut.
func stripLeadingInterjection(_ text: String) -> (String, String?) {
    let pattern = #"^\s*(?:(?:oh|ah|aw|ooh|whoa|wow|hmm+|mm+|um+|uh+|well|so|man)(?:,?\s*(?:man|boy|wow|yeah|okay))?\s*[,.!…]+|oh,?\s+man\b[,.!…]*)\s*"#
    guard let re = try? NSRegularExpression(pattern: pattern, options: [.caseInsensitive]),
        let m = re.firstMatch(in: text, range: NSRange(text.startIndex..., in: text)),
        let r = Range(m.range, in: text)
    else { return (text, nil) }
    let cut = String(text[r]).trimmingCharacters(in: .whitespaces)
    var rest = String(text[r.upperBound...]).trimmingCharacters(in: .whitespaces)
    if let f = rest.first, f.isLowercase { rest = f.uppercased() + rest.dropFirst() }
    return (rest, cut)
}

/// Text cleanup before the voice: no markup, no emojis, no bracketed notes, plain apostrophes and quotes.
func speakableText(_ s: String) -> String {
    var t = s.replacingOccurrences(of: #"\[[^\]]*\]"#, with: "", options: .regularExpression)
    t = t.replacingOccurrences(of: "[*_#`~>|]", with: "", options: .regularExpression)
    for (a, b) in [("\u{2019}", "'"), ("\u{2018}", "'"), ("\u{201C}", "\""), ("\u{201D}", "\""), ("\u{2014}", ", "), ("\u{2013}", " to ")] {
        t = t.replacingOccurrences(of: a, with: b)
    }
    t = t.unicodeScalars.filter { !($0.properties.isEmojiPresentation || ($0.properties.isEmoji && $0.value > 0x2000)) }
        .map(String.init).joined()
    return t.replacingOccurrences(of: #"\s+"#, with: " ", options: .regularExpression)
        .replacingOccurrences(of: #" ([.,!?;:])"#, with: "$1", options: .regularExpression)
        .trimmingCharacters(in: .whitespacesAndNewlines)
}

// MARK: - The person's words

enum TextShape: String {
    case empty
    case complete
    case weakIncomplete = "maybe_incomplete"
    case incomplete
}

/// Whether the words so far sound finished. "um" and "uh" mean the person is still thinking.
enum TurnRules {
    static let fillers: Set<String> = ["um", "umm", "ummm", "uh", "uhh", "uhm", "er", "erm", "hmm", "hm", "mm", "ah", "eh", "mhm"]
    static let strong: Set<String> = [
        "and", "but", "or", "because", "cause", "'cause", "then", "if", "which", "who", "whose", "when", "where", "while",
        "the", "a", "an", "my", "your", "his", "her", "their", "our", "its", "i", "i'm", "i've", "i'd", "it's", "we're",
        "they're", "you're", "he's", "she's", "there's", "that's", "is", "are", "was", "were", "am", "gonna", "wanna",
        "gotta", "let's", "also", "maybe", "actually", "basically", "kinda", "sorta", "than", "whether", "very",
        "although", "though", "unless", "until", "since", "plus", "with", "and so", "could", "would", "should", "might",
        "what's", "how's", "where's", "um,", "some", "any", "every", "each",
    ]
    static let weak: Set<String> = ["to", "of", "for", "in", "on", "at", "from", "about", "into", "by", "like", "so", "well",
                                    "just", "really", "that", "this", "can", "will", "do", "does", "did"]
    static let fillerPhrases = ["you know", "i mean", "kind of", "sort of", "and then", "so like", "or like", "and like",
                                "i guess", "let me think", "let me see", "hold on", "wait"]

    static func shape(_ raw: String) -> (TextShape, String) {
        let t = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        if t.isEmpty { return (.empty, "no words yet") }
        if t.hasSuffix(",") || t.hasSuffix("...") || t.hasSuffix("\u{2026}") || t.hasSuffix("-") || t.hasSuffix(";") || t.hasSuffix(":") {
            return (.incomplete, "trailing punctuation")
        }
        let lower = t.lowercased()
        let words = lower.split(whereSeparator: { $0.isWhitespace })
            .map { $0.trimmingCharacters(in: CharacterSet.punctuationCharacters.subtracting(CharacterSet(charactersIn: "'"))) }
            .filter { !$0.isEmpty }
        guard let last = words.last else { return (.empty, "no words yet") }
        let tail2 = words.suffix(2).joined(separator: " ")
        let endsWithStop = t.hasSuffix(".") || t.hasSuffix("?") || t.hasSuffix("!")
        if fillers.contains(last) { return (.incomplete, "filler \"\(last)\"") }
        for p in fillerPhrases where tail2.hasSuffix(p) || lower.hasSuffix(p) {
            return (.incomplete, "filler phrase \"\(p)\"")
        }
        if words.count == 1 && (last == "so" || last == "and" || last == "but") {
            return (.incomplete, "lead-in \"\(last)\"")
        }
        if strong.contains(last) && !(endsWithStop && t.hasSuffix("?")) { return (.incomplete, "ends with \"\(last)\"") }
        if weak.contains(last) { return (.weakIncomplete, "ends with \"\(last)\"") }
        return (.complete, endsWithStop ? "sentence end" : "complete words")
    }
}

/// Cleaning up what speech recognition returns.
enum HeardText {
    static func wordCount(_ s: String) -> Int {
        s.split(whereSeparator: \.isWhitespace).filter { $0.contains(where: { $0.isLetter || $0.isNumber }) }.count
    }

    /// Removes punctuation that stands alone (". , ..." between words, left where a segment held no words) and
    /// shortens runs of marks ("for.." to "for.", "of.........." to "of...").
    static func tidy(_ s: String) -> String {
        var out: [String] = []
        for tok in s.split(whereSeparator: \.isWhitespace) {
            guard tok.contains(where: { $0.isLetter || $0.isNumber }) else { continue }
            var t = String(tok)
            let core = t.reversed().drop(while: { $0.isPunctuation && $0 != "'" && $0 != ")" && $0 != "\"" })
            let tailCount = t.count - core.count
            if tailCount > 1 {
                let tail = t.suffix(tailCount)
                let head = String(t.prefix(t.count - tailCount))
                t = tail.filter({ $0 == "." }).count >= 3 ? head + "..." : head + String(tail.first!)
            }
            out.append(t)
        }
        return out.joined(separator: " ")
    }

    /// Lowercased words without punctuation, for comparing two versions of the same words.
    static func norm(_ s: String) -> String {
        s.lowercased().unicodeScalars.filter { CharacterSet.alphanumerics.contains($0) || $0 == " " }
            .map(String.init).joined().split(separator: " ").joined(separator: " ")
    }

    static func words(_ s: String) -> [String] {
        s.lowercased().split { !($0.isLetter || $0.isNumber || $0 == "'") }.map { $0.replacingOccurrences(of: "'", with: "") }
    }

    /// Short sounds that mean "go on" while the voice talks; they never stop it.
    static let backchannel: Set<String> = [
        "yeah", "yep", "yup", "yes", "uh", "huh", "uhhuh", "mhm", "mm", "mmm", "hmm", "um", "okay", "ok", "right", "sure",
        "cool", "nice", "wow", "oh", "ah",
    ]
    /// Words that do stop it, even alone.
    static let commands: Set<String> = ["stop", "wait", "hold", "no", "nope", "zero", "hey", "actually", "sorry", "pause", "quiet"]

    /// Whether words heard while the voice talks on the speaker are really the person (not the voice's own echo).
    /// Returns the words that count.
    static func interruption(heard: [String], voiceWords: Set<String>) -> (confirmed: Bool, foreign: [String]) {
        let foreign = heard.filter { !voiceWords.contains($0) && !backchannel.contains($0) }
        let confirmed = foreign.count >= 2 || foreign.contains { $0.count >= 4 || commands.contains($0) }
        return (confirmed, foreign)
    }
}
