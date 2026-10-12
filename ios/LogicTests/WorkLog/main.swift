import Foundation

// Logic tests for Model/WorkLog.swift: a Zip-to-Zip thread shows the question, one work log, and the answer.
var failures = 0
func check(_ ok: Bool, _ name: String) {
    print((ok ? "ok   " : "FAIL ") + name)
    if !ok { failures += 1 }
}

let t0 = Date(timeIntervalSince1970: 1_791_800_000)
typealias I = WorkLog.Item
func at(_ s: Double) -> Date { t0.addingTimeInterval(s) }

// School's chat: Zero's question, two steps, School's answer.
do {
    let items = [I(id: "you", date: at(-60)),
                 I(id: "ask", date: at(0), teamMsg: 41, teamKind: "ask"),
                 I(id: "signin", date: at(20), stepOf: 41),
                 I(id: "done", date: at(21), stepOf: 41),
                 I(id: "answer", date: at(85), teamMsg: 41, teamKind: "answer")]
    check(WorkLog.fold(items) == [.keep("you"), .keep("ask"),
                                  .log(id: "worklog-41", steps: ["signin", "done"], seconds: 85), .keep("answer")],
          "the steps fold into one log between the question and the answer")
    check(WorkLog.header(85) == "Worked for 1m 25s", "the header reads Worked for 1m 25s")
}

// Still working: no answer yet, so the log sits where the first step was.
do {
    let items = [I(id: "ask", date: at(0), teamMsg: 7, teamKind: "ask"),
                 I(id: "s1", date: at(10), stepOf: 7),
                 I(id: "mine", date: at(12)),
                 I(id: "s2", date: at(30), stepOf: 7)]
    check(WorkLog.fold(items) == [.keep("ask"), .log(id: "worklog-7", steps: ["s1", "s2"], seconds: 30), .keep("mine")],
          "with no answer yet the log sits at its first step")
}

// A question answered with no steps still says how long it took; the asker's chat (a reply, no question) is unchanged.
do {
    let items = [I(id: "ask", date: at(0), teamMsg: 9, teamKind: "ask"),
                 I(id: "answer", date: at(40), teamMsg: 9, teamKind: "answer")]
    check(WorkLog.fold(items) == [.keep("ask"), .log(id: "worklog-9", steps: [], seconds: 40), .keep("answer")],
          "an answer with no steps gets a header line")
    let asker = [I(id: "a", date: at(0)), I(id: "reply", date: at(40), teamMsg: 9, teamKind: "reply")]
    check(WorkLog.fold(asker) == [.keep("a"), .keep("reply")], "the asker's chat keeps its reply as it was")
}

// An ordinary chat with no team messages is left exactly as it was.
do {
    let items = [I(id: "1", date: at(0)), I(id: "2", date: at(5))]
    check(WorkLog.fold(items) == [.keep("1"), .keep("2")], "a normal chat is unchanged")
    check(WorkLog.header(nil) == "Worked on it", "an unknown time says Worked on it")
    check(WorkLog.header(40) == "Worked for 40s", "under a minute shows seconds")
    check(WorkLog.header(120) == "Worked for 2m", "whole minutes drop the seconds")
}

if failures > 0 { print("\(failures) failed"); exit(1) }
print("all WorkLog checks passed")
