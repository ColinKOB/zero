import SwiftUI

enum ChatText {
    static func render(_ raw: String, markdown: Bool = true) -> AttributedString {
        var out: AttributedString
        if markdown {
            let s = raw.replacingOccurrences(of: #"(?m)^(\s*)[-*] "#, with: "$1•  ", options: .regularExpression)
            out = (try? AttributedString(markdown: s, options: .init(interpretedSyntax: .inlineOnlyPreservingWhitespace)))
                ?? AttributedString(s)
        } else {
            out = AttributedString(raw)
        }
        linkify(&out)
        for run in out.runs where run.link != nil {
            out[run.range].underlineStyle = .single
        }
        return out
    }

    private static let detector = try? NSDataDetector(types: NSTextCheckingResult.CheckingType.link.rawValue
                                                       | NSTextCheckingResult.CheckingType.phoneNumber.rawValue)

    private static func linkify(_ text: inout AttributedString) {
        guard let detector else { return }
        let plain = String(text.characters)
        let ns = plain as NSString
        for match in detector.matches(in: plain, range: NSRange(location: 0, length: ns.length)) {
            let url: URL?
            if match.resultType == .phoneNumber, let number = match.phoneNumber {
                url = URL(string: "tel:" + number.filter { $0.isNumber || $0 == "+" })
            } else {
                url = match.url
            }
            guard let url, let r = Range(match.range, in: plain),
                  let lower = AttributedString.Index(r.lowerBound, within: text),
                  let upper = AttributedString.Index(r.upperBound, within: text) else { continue }
            if text[lower..<upper].runs.contains(where: { $0.link != nil }) { continue }
            text[lower..<upper].link = url
        }
    }
}
