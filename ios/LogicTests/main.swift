import Foundation

// Logic tests for Model/ChatSync.swift, with no simulator: ios/LogicTests/run.sh compiles this file (main.swift) with ChatSync.swift
// into one program and runs it. Each check prints its name; any failure exits non-zero.

var failures = 0
func check(_ ok: Bool, _ name: String) {
    print((ok ? "ok   " : "FAIL ") + name)
    if !ok { failures += 1 }
}

let t0 = Date(timeIntervalSince1970: 1_791_602_322)   // 11:18:42 p.m. ET, Oct 9
typealias L = ChatSync.Line

// The incident: the message went out from the phone, the hub delivered it, the stream never said so.
do {
    let local = [L(id: "local-1", mine: true, text: "I think I actually bought them over the phone.", date: t0)]
    let history = [
        L(id: "354", mine: false, text: "I've set it up again.", date: t0.addingTimeInterval(-37_000)),
        L(id: "361", mine: true, text: "I think I actually bought them over the phone.", date: t0.addingTimeInterval(1)),
        L(id: "364", mine: false, text: "That explains why there are no emails.", date: t0.addingTimeInterval(19)),
    ]
    check(ChatSync.confirmed(local: local, history: history) == ["local-1": "361"],
          "a history load confirms the sent message")
}

// Not yet saved: nothing confirmed, so the message stays as sent.
do {
    let local = [L(id: "local-1", mine: true, text: "Also check ENG 102", date: t0)]
    let history = [L(id: "9", mine: true, text: "Check eLearning", date: t0.addingTimeInterval(-60))]
    check(ChatSync.confirmed(local: local, history: history).isEmpty, "a message the history lacks stays pending")
}

// The same words sent yesterday are not this message.
do {
    let local = [L(id: "local-1", mine: true, text: "ok", date: t0)]
    let history = [L(id: "5", mine: true, text: "ok", date: t0.addingTimeInterval(-86_400))]
    check(ChatSync.confirmed(local: local, history: history).isEmpty, "old identical words do not confirm a new message")
}

// Two identical messages need two saved copies; whitespace differences do not matter.
do {
    let local = [L(id: "local-a", mine: true, text: "yes", date: t0), L(id: "local-b", mine: true, text: "yes ", date: t0.addingTimeInterval(2))]
    let one = [L(id: "7", mine: true, text: "yes", date: t0.addingTimeInterval(1))]
    check(ChatSync.confirmed(local: local, history: one) == ["local-a": "7"], "one saved copy confirms one message")
    let two = one + [L(id: "8", mine: true, text: "yes", date: t0.addingTimeInterval(3))]
    check(ChatSync.confirmed(local: local, history: two) == ["local-a": "7", "local-b": "8"], "two saved copies confirm both")
}

// A streamed reply placeholder matches the Zip's saved reply, never the person's words.
do {
    let local = [L(id: "pending-1", mine: false, text: "That explains why there are no emails.", date: t0.addingTimeInterval(20))]
    let history = [L(id: "364", mine: false, text: "That explains why there are no emails.", date: t0.addingTimeInterval(19))]
    check(ChatSync.confirmed(local: local, history: history) == ["pending-1": "364"], "a streamed reply matches its saved copy")
    let theirs = [L(id: "1", mine: true, text: "That explains why there are no emails.", date: t0)]
    check(ChatSync.confirmed(local: local, history: theirs).isEmpty, "a reply never matches the person's own words")
}

// Paused: the 11:54 a.m. card, with Colin writing at 11:18 p.m.
do {
    let card = t0.addingTimeInterval(-41_064)
    check(!ChatSync.live(card: card, hubWaiting: nil, lastSaid: t0), "an old card does not pause the Zip (older hub)")
    check(!ChatSync.live(card: card, hubWaiting: false, lastSaid: nil), "the hub's word that nothing waits wins")
    check(ChatSync.live(card: t0.addingTimeInterval(30), hubWaiting: true, lastSaid: t0), "a card from this turn pauses it")
    check(!ChatSync.live(card: t0.addingTimeInterval(30), hubWaiting: true, lastSaid: t0.addingTimeInterval(90)),
          "writing again ends the pause at once, before the hub says so")
    check(ChatSync.live(card: card, hubWaiting: true, lastSaid: nil), "with no message yet, the hub decides")
    check(ChatSync.live(card: t0, hubWaiting: nil, lastSaid: t0.addingTimeInterval(0.5)), "the same second counts as this turn")
}

// A running chat turn: the hub's word on it decides, whatever cards wait for other work (night review 2). A scheduled
// job's card (up to a day) or a board relay's card kept "Zero is paused for your okay" over the person's own reply in
// progress, with no typing dots and no Stop, the Oct 9 symptom again.
do {
    check(!ChatSync.paused(turnHeld: false, cards: { true }), "a working turn is not paused by another work's card")
    check(ChatSync.paused(turnHeld: true, cards: { false }), "a turn held on a card is paused")
    check(ChatSync.paused(turnHeld: nil, cards: { true }), "with no word from the hub, the open cards decide")
    check(!ChatSync.paused(turnHeld: nil, cards: { false }), "no turn and no live card: not paused")
}

// A stream that went quiet while the hub says the Zip is idle.
do {
    check(!ChatSync.streamIsStale(idleLooks: 1, sentAt: t0, now: t0.addingTimeInterval(60)), "one idle look is not enough")
    check(!ChatSync.streamIsStale(idleLooks: 3, sentAt: t0, now: t0.addingTimeInterval(4)), "a fresh send gets its grace")
    check(ChatSync.streamIsStale(idleLooks: 2, sentAt: t0, now: t0.addingTimeInterval(30)), "two idle looks after the grace")
}

// Replies: the quote is one line, cut near 300 characters, and finds its original after a reload.
do {
    check(ReplyLogic.excerpt("  The cart\n\nis ready.  ") == "The cart is ready.", "a quote is one line")
    let long = ReplyLogic.excerpt(String(repeating: "word ", count: 120))
    check(long.hasSuffix("...") && long.count <= ReplyLogic.excerptMax + 3 && !long.contains("  "), "a long quote is cut at a word")
    let lines = [
        L(id: "1", mine: false, text: "The cart is ready now.", date: t0),
        L(id: "2", mine: true, text: "The cart is ready now.", date: t0.addingTimeInterval(5)),
        L(id: "3", mine: false, text: "Pickup is free after two hours.", date: t0.addingTimeInterval(9)),
    ]
    check(ReplyLogic.original(of: ReplyQuote(text: "The cart is ready now.", who: "bot"), in: lines) == "1",
          "a quote of the Zip finds the Zip's message")
    check(ReplyLogic.original(of: ReplyQuote(text: "The cart is ready now.", who: "person"), in: lines) == "2",
          "a quote of the person finds their own message")
    check(ReplyLogic.original(of: ReplyQuote(text: "Pickup is free...", who: "bot"), in: lines) == "3",
          "a cut quote finds the whole message")
    check(ReplyLogic.original(of: ReplyQuote(text: "anything", who: "bot", messageID: "2"), in: lines) == "2",
          "the id wins on the device that sent it")
    check(ReplyLogic.original(of: ReplyQuote(text: "Not loaded", who: "bot"), in: lines) == nil,
          "an original that is not loaded is not guessed")
}

// Card answers (Oct 10): an answered card and the message that carried its answer show once, as the card's answer.
do {
    typealias C = CardAnswers.Line
    typealias A = CardAnswers.Answer
    let q = "Want me to put these somewhere so they come back up?"
    // The incident: "Add both to Trello" tapped, then the same words as his message, then the Zip's "Will do".
    let tapped = [
        C(id: "feed-7", mine: false, text: q, date: t0, answer: A(text: "Add both to Trello", at: t0.addingTimeInterval(30))),
        C(id: "401", mine: true, text: "Add both to Trello", date: t0.addingTimeInterval(31)),
        C(id: "402", mine: false, text: "Will do.", date: t0.addingTimeInterval(40)),
    ]
    check(CardAnswers.pairs(tapped) == ["feed-7": "401"], "a tapped option pairs with the message that carried it")
    // Live: the message is still on its way (a local id at the end of the chat), after the Zip's earlier reply.
    let live = [
        C(id: "feed-7", mine: false, text: q, date: t0, answer: A(text: "Add both to Trello", at: t0.addingTimeInterval(30))),
        C(id: "local-1", mine: true, text: "Add both to Trello ", date: t0.addingTimeInterval(30)),
    ]
    check(CardAnswers.pairs(live) == ["feed-7": "local-1"], "a message on its way pairs too, whatever its spacing")
    // A message before the card, or one with other words, is never hidden.
    let before = [
        C(id: "400", mine: true, text: "Add both to Trello", date: t0.addingTimeInterval(-60)),
        C(id: "feed-7", mine: false, text: q, date: t0, answer: A(text: "Add both to Trello", at: t0.addingTimeInterval(30))),
        C(id: "401", mine: true, text: "Actually, just the first one", date: t0.addingTimeInterval(31)),
    ]
    check(CardAnswers.pairs(before).isEmpty, "only a later message with the same words pairs")
    // He typed other words first, then tapped: the answer pairs past them.
    let later = [
        C(id: "feed-7", mine: false, text: q, date: t0, answer: A(text: "Yes", at: t0.addingTimeInterval(120))),
        C(id: "401", mine: true, text: "What would that look like?", date: t0.addingTimeInterval(30)),
        C(id: "402", mine: false, text: "Two cards on Next.", date: t0.addingTimeInterval(40)),
        C(id: "403", mine: true, text: "Yes", date: t0.addingTimeInterval(121)),
    ]
    check(CardAnswers.pairs(later) == ["feed-7": "403"], "his other words before the answer stay")
    // The same words long before the answer (an older "Yes") are not the answer.
    let old = [
        C(id: "feed-7", mine: false, text: q, date: t0, answer: A(text: "Yes", at: t0.addingTimeInterval(3600))),
        C(id: "401", mine: true, text: "Yes", date: t0.addingTimeInterval(30)),
        C(id: "402", mine: true, text: "Yes", date: t0.addingTimeInterval(3601)),
    ]
    check(CardAnswers.pairs(old) == ["feed-7": "402"], "an earlier message with the same words is not the answer")
    // Two cards answered the same way: each takes its own message.
    let two = [
        C(id: "feed-1", mine: false, text: "First?", date: t0, answer: A(text: "Yes", at: t0.addingTimeInterval(10))),
        C(id: "a", mine: true, text: "Yes", date: t0.addingTimeInterval(11)),
        C(id: "feed-2", mine: false, text: "Second?", date: t0.addingTimeInterval(20), answer: A(text: "Yes", at: t0.addingTimeInterval(30))),
        C(id: "b", mine: true, text: "Yes", date: t0.addingTimeInterval(31)),
    ]
    check(CardAnswers.pairs(two) == ["feed-1": "a", "feed-2": "b"], "two cards with the same answer take one message each")
    // A question from Zero: the exact words the hub sent win, even when they differ from the option.
    let say = "Please put a 30-minute block on my calendar today at 7 p.m. to work on ENG 102."
    let zero = [
        C(id: "feed-95", mine: false, text: "Want a block?", date: t0,
          answer: A(text: "Block today at 7 p.m.", sent: say, at: t0.addingTimeInterval(5))),
        C(id: "9", mine: true, text: say, date: t0.addingTimeInterval(6)),
    ]
    check(CardAnswers.pairs(zero) == ["feed-95": "9"], "a question's own wording for the answer pairs by the words sent")
    // A typed answer to a question from Zero, as the hub hands it over, with and without the closed-question note.
    let about = "About your question \"Is it yours?\": It was my sister's"
    check(CardAnswers.matches(about, A(text: "It was my sister's")), "a typed answer to Zero's question matches its own words")
    check(CardAnswers.matches(about + " (This answers a closed question. Its date, Sunday at 4 p.m., has passed.)",
                              A(text: "It was my sister's")), "with the closed-question note too")
    check(CardAnswers.matches("Keep it. (This answers a closed question. Its date has passed.)", A(text: "Keep it.")),
          "an option with the closed-question note matches")
    check(!CardAnswers.matches("About your question \"Is it yours?\": No", A(text: "It was my sister's")),
          "another typed answer does not match")
    check(!CardAnswers.matches("Add both to Trello", A(text: "Add both to Trello", sent: "Please add both cards.")),
          "when the words sent are known, only they match")
    // A question from Zero answered before the hub kept the words: his first message within minutes of the answer.
    let loose = [
        C(id: "feed-91", mine: false, text: "Send a nudge?", date: t0,
          answer: A(text: "Draft a nudge", at: t0.addingTimeInterval(10), loose: true)),
        C(id: "1", mine: true, text: "Please draft a short nudge to Dana.", date: t0.addingTimeInterval(11)),
        C(id: "2", mine: true, text: "Draft a nudge", date: t0.addingTimeInterval(12)),
    ]
    check(CardAnswers.pairs(loose) == ["feed-91": "1"], "an answer whose words are unknown takes the next message")
    let tooLate = [
        C(id: "feed-91", mine: false, text: "Send a nudge?", date: t0,
          answer: A(text: "Draft a nudge", at: t0.addingTimeInterval(10), loose: true)),
        C(id: "1", mine: true, text: "Something else entirely", date: t0.addingTimeInterval(3600)),
    ]
    check(CardAnswers.pairs(tooLate).isEmpty, "but never his new words an hour later")
    // A card answered with no message (the hub handled it) pairs with nothing.
    let handled = [
        C(id: "feed-92", mine: false, text: "Keep it?", date: t0, answer: A(text: "Already canceled", at: t0.addingTimeInterval(5))),
        C(id: "1", mine: true, text: "Thanks", date: t0.addingTimeInterval(60)),
    ]
    check(CardAnswers.pairs(handled).isEmpty, "an answer the hub handled hides nothing")
}

print(failures == 0 ? "all ChatSync checks passed" : "\(failures) ChatSync check(s) failed")
exit(failures == 0 ? 0 : 1)
