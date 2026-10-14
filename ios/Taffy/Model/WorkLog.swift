import Foundation

enum WorkLog {
    struct Item: Equatable {
        let id: String
        let date: Date?
        var stepOf: Int? = nil
        var teamMsg: Int? = nil
        var teamKind: String? = nil
    }

    enum Entry: Equatable {
        case keep(String)
        case log(id: String, steps: [String], seconds: TimeInterval?)
    }

    static func logID(_ msg: Int) -> String { "worklog-\(msg)" }

    static func fold(_ items: [Item]) -> [Entry] {
        var steps: [Int: [Item]] = [:]
        var order: [Int] = []
        var asks: [Int: Item] = [:]
        var answers: [Int: Item] = [:]
        for it in items {
            if let of = it.stepOf {
                if steps[of] == nil { order.append(of) }
                steps[of, default: []].append(it)
            } else if let msg = it.teamMsg, let kind = it.teamKind {
                if kind == "ask", asks[msg] == nil { asks[msg] = it }
                if kind == "answer" || kind == "reply", answers[msg] == nil { answers[msg] = it }
            }
        }
        guard !steps.isEmpty || !answers.keys.filter({ asks[$0] != nil }).isEmpty else { return items.map { .keep($0.id) } }

        func seconds(_ msg: Int) -> TimeInterval? {
            let start = asks[msg]?.date ?? steps[msg]?.first?.date
            let end = answers[msg]?.date ?? steps[msg]?.last?.date
            guard let s = start, let e = end, e > s else { return nil }
            return e.timeIntervalSince(s)
        }
        func log(_ msg: Int) -> Entry {
            .log(id: logID(msg), steps: (steps[msg] ?? []).map(\.id), seconds: seconds(msg))
        }

        var out: [Entry] = []
        var placed = Set<Int>()
        for it in items {
            if let of = it.stepOf {
                if answers[of] == nil, !placed.contains(of) {
                    out.append(log(of))
                    placed.insert(of)
                }
                continue
            }
            if let msg = it.teamMsg, answers[msg]?.id == it.id, !placed.contains(msg),
               steps[msg] != nil || asks[msg] != nil {
                out.append(log(msg))
                placed.insert(msg)
            }
            out.append(.keep(it.id))
        }
        return out
    }

    static func header(_ seconds: TimeInterval?) -> String {
        guard let seconds, seconds >= 1 else { return "Worked on it" }
        let s = Int(seconds.rounded())
        if s < 60 { return "Worked for \(s)s" }
        if s < 3600 { return s % 60 == 0 ? "Worked for \(s / 60)m" : "Worked for \(s / 60)m \(s % 60)s" }
        return "Worked for \(s / 3600)h \(s / 60 % 60)m"
    }
}
