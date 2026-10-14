import XCTest

final class SentenceTests: XCTestCase {
    private func split(_ pieces: [String]) -> [String] {
        var s = SentenceSplitter()
        var out: [SpokenSentence] = []
        for p in pieces { out += s.push(p) }
        out += s.flush()
        return out.map(\.text)
    }

    func testWholeSentencesFromAStream() {
        XCTAssertEqual(split(["Sure ", "thing. Tacos at ", "six sound great. Want ", "a recipe?"]),
                       ["Sure thing.", "Tacos at six sound great.", "Want a recipe?"])
    }

    func testDecimalsAbbreviationsAndInitialsAreNotSentenceEnds() {
        XCTAssertEqual(split(["It costs 3.5 dollars. Dr. Smith said so. J. Smith agreed."]),
                       ["It costs 3.5 dollars.", "Dr. Smith said so.", "J. Smith agreed."])
    }

    func testHalfStreamedEndWaitsForTheNextCharacter() {
        var s = SentenceSplitter()
        XCTAssertEqual(s.push("Call Dr."), [])
        XCTAssertEqual(s.push(" Lee at noon. Then").map(\.text), ["Call Dr. Lee at noon."])
    }

    func testNeverCutsAtCommasUnlessARunawaySentence() {
        XCTAssertEqual(split(["Well, first, second, and third, all in one go."]).count, 1)
        let long = (1...70).map { "word\($0)" }.joined(separator: " ")
        XCTAssertGreaterThan(split([long.replacingOccurrences(of: "word30 ", with: "word30, ") + "."]).count, 1)
    }

    func testTwoQuestionsJoinedByACommaBecomeTwo() {
        XCTAssertEqual(QuestionSplit.split("How about you, how is your day going so far?"),
                       ["How about you?", "How is your day going so far?"])
        XCTAssertEqual(QuestionSplit.split("If it rains, should we stay in?"), ["If it rains, should we stay in?"])
        XCTAssertEqual(QuestionSplit.split("Okay, sounds good."), ["Okay, sounds good."])
    }

    func testOpeningInterjectionsAreDropped() {
        var r = ReplySentences()
        r.push("Oh, man. That sounds rough. ")
        r.finish()
        XCTAssertEqual(r.next()?.text, "That sounds rough.")
        XCTAssertEqual(r.strippedOpening, "Oh, man.")
        var w = ReplySentences()
        w.push("Well, here's the deal. ")
        w.finish()
        XCTAssertEqual(w.next()?.text, "Here's the deal.")
        var o = ReplySentences()
        o.push("Ohio is nice. ")
        o.finish()
        XCTAssertEqual(o.next()?.text, "Ohio is nice.")
    }

    func testSpeakableTextDropsMarkupEmojisAndBracketNotes() {
        XCTAssertEqual(speakableText("**Sure** thing \u{1F600}. [You handed this off.] It\u{2019}s done\u{2014}really."),
                       "Sure thing. It's done, really.")
    }

    func testPausesBetweenSentences() {
        XCTAssertEqual(SpokenSentence(text: "Done.", paragraphEnd: false).pauseAfter, 0.25, accuracy: 0.001)
        XCTAssertEqual(SpokenSentence(text: "Done.", paragraphEnd: true).pauseAfter, 0.40, accuracy: 0.001)
        XCTAssertEqual(SpokenPause.pad(previous: 0.25, trailing: 0.05, late: 0.0), 0.15, accuracy: 0.001)
        XCTAssertEqual(SpokenPause.pad(previous: 0.25, trailing: 0.3, late: 0.0), 0)
    }
}

final class HearingTests: XCTestCase {
    func testFillersAndUnfinishedClausesHoldTheTurn() {
        XCTAssertEqual(TurnRules.shape("I was thinking um").0, .incomplete)
        XCTAssertEqual(TurnRules.shape("Can you check uh").0, .incomplete)
        XCTAssertEqual(TurnRules.shape("I need to call my").0, .incomplete)
        XCTAssertEqual(TurnRules.shape("What should I cook for").0, .weakIncomplete)
        XCTAssertEqual(TurnRules.shape("What should I cook tonight?").0, .complete)
        XCTAssertEqual(TurnRules.shape("").0, .empty)
    }

    func testLonePunctuationIsRemoved() {
        XCTAssertEqual(HeardText.tidy("people. , of.......... controlled by AI."), "people. of... controlled by AI.")
        XCTAssertEqual(HeardText.tidy(". , ..."), "")
    }

    func testOnlyForeignWordsInterruptOnTheSpeaker() {
        let voice = Set(HeardText.words("Tacos at six sound great. Want a recipe?"))
        XCTAssertFalse(HeardText.interruption(heard: ["yeah"], voiceWords: voice).confirmed)
        XCTAssertFalse(HeardText.interruption(heard: ["tacos", "six"], voiceWords: voice).confirmed)
        XCTAssertTrue(HeardText.interruption(heard: ["stop"], voiceWords: voice).confirmed)
        XCTAssertTrue(HeardText.interruption(heard: ["actually", "pizza"], voiceWords: voice).confirmed)
        XCTAssertTrue(HeardText.interruption(heard: ["burgers"], voiceWords: voice).confirmed)
    }

    func testNormalizedWordsCompareAcrossPunctuation() {
        XCTAssertEqual(HeardText.norm("What's up, Zero?"), HeardText.norm("whats up zero"))
    }
}
