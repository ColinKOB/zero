// The work sounds and call rules of fluid calls (docs/qa/2026-10-09-fluid-voice.md): short, soft, click-free,
// distinct sounds that play only into silence, and the small rules both voices share.
import XCTest

final class EarconTests: XCTestCase {
    func testShortSoftAndClickFree() {
        for e in Earcon.allCases {
            let s = e.samples(rate: 24_000)
            XCTAssertGreaterThan(s.count, 2_400, "\(e) is too short to hear")          // over 0.1 s
            XCTAssertLessThanOrEqual(Double(s.count) / 24_000, 0.35, "\(e) is too long")
            let peak = s.map(abs).max() ?? 0
            XCTAssertEqual(peak, e.peak, accuracy: 0.001, "\(e) peak")
            XCTAssertLessThanOrEqual(peak, 0.15, "\(e) must sit well under the voice")
            XCTAssertLessThan(abs(s.first ?? 1), 0.002, "\(e) starts with a click")
            XCTAssertLessThan(abs(s.last ?? 1), 0.002, "\(e) ends with a click")
            // No jump between neighbouring samples that a speaker would hear as a click.
            let jump = zip(s, s.dropFirst()).map { abs($1 - $0) }.max() ?? 0
            XCTAssertLessThan(jump, e.peak * 0.5, "\(e) has a sharp edge")
        }
    }

    func testTheThreeSoundsAreDifferent() {
        func centroid(_ s: [Float]) -> Double {    // a rough brightness: zero crossings per second
            let crossings = zip(s, s.dropFirst()).filter { ($0 < 0) != ($1 < 0) }.count
            return Double(crossings) / (Double(s.count) / 24_000) / 2
        }
        let started = Earcon.started.samples(rate: 24_000)
        let working = Earcon.working.samples(rate: 24_000)
        let done = Earcon.done.samples(rate: 24_000)
        XCTAssertNotEqual(started.count, working.count)
        XCTAssertNotEqual(started.count, done.count)
        XCTAssertLessThan(Earcon.working.peak, Earcon.started.peak)   // "still working" is the quietest
        XCTAssertGreaterThan(centroid(done), centroid(working))        // "done" ends higher and brighter
    }

    func testSameAtAnyRate() {
        let a = Earcon.done.samples(rate: 24_000), b = Earcon.done.samples(rate: 48_000)
        XCTAssertEqual(Double(a.count) / 24_000, Double(b.count) / 48_000, accuracy: 0.001)
    }

    func testWavHeader() {
        let d = Earcon.started.wav()
        XCTAssertEqual(String(data: d.prefix(4), encoding: .ascii), "RIFF")
        XCTAssertEqual(d.count, 44 + Earcon.started.samples(rate: 24_000).count * 2)
    }
}

final class CueGateTests: XCTestCase {
    func testPlaysOnlyIntoSilenceAndDropsWhatWaitedTooLong() {
        var g = CueGate()
        g.add(.started, task: 1, now: 0)
        XCTAssertNil(g.next(now: 0.5, quiet: false))          // the voice is still saying "Let me check that"
        XCTAssertEqual(g.next(now: 1.0, quiet: true), .started)
        g.add(.working, task: 1, now: 10)
        XCTAssertNil(g.next(now: 10.5, quiet: false))
        XCTAssertNil(g.next(now: 12.5, quiet: true))          // two seconds without a gap: dropped, never late
        XCTAssertTrue(g.pending.isEmpty)
    }

    func testDoneOnlyAfterTheStartWasHeard() {
        var g = CueGate()
        // A quick answer: the done cue arrives before the start could play. No sounds at all.
        g.add(.started, task: 1, now: 0)
        g.add(.done, task: 1, now: 0.8)
        XCTAssertNil(g.next(now: 1.0, quiet: true))
        // A longer task: started was heard, so done plays before the result.
        g.add(.started, task: 2, now: 5)
        XCTAssertEqual(g.next(now: 5.2, quiet: true), .started)
        g.add(.done, task: 2, now: 20)
        XCTAssertEqual(g.next(now: 20.1, quiet: true), .done)
    }

    func testNeverTwoSoundsTooClose() {
        var g = CueGate()
        g.add(.started, task: 1, now: 0)
        g.add(.started, task: 2, now: 0)
        XCTAssertEqual(g.next(now: 0.1, quiet: true), .started)
        XCTAssertNil(g.next(now: 0.5, quiet: true))
        XCTAssertEqual(g.next(now: 0.1 + CueGate.minGap, quiet: true), .started)
    }

    func testTheFloorClearsProgressButNotDone() {
        var g = CueGate()
        g.add(.started, task: 1, now: 0)
        XCTAssertEqual(g.next(now: 0, quiet: true), .started)
        g.add(.working, task: 1, now: 5)
        g.add(.done, task: 1, now: 5.1)
        g.clearProgress()
        XCTAssertEqual(g.pending.map(\.kind), [.done])
    }
}

final class CallRulesTests: XCTestCase {
    func testAskAgainOnlyForRealTalkAndNotOften() {
        XCTAssertTrue(CallRules.askAgain(voicedSeconds: 1.4, sinceLastAsk: .infinity))
        XCTAssertFalse(CallRules.askAgain(voicedSeconds: 0.4, sinceLastAsk: .infinity))   // a cough, a door
        XCTAssertFalse(CallRules.askAgain(voicedSeconds: 2.0, sinceLastAsk: 5))
    }

    func testFallbackKeepsTheKindOfVoice() {
        XCTAssertEqual(CallRules.onDeviceVoice(matching: "maple"), "alba")
        XCTAssertEqual(CallRules.onDeviceVoice(matching: "juniper"), "alba")
        XCTAssertEqual(CallRules.onDeviceVoice(matching: "cove"), "george")
        XCTAssertEqual(CallRules.onDeviceVoice(matching: "breeze"), "george")
    }

    func testOnlyANewChoiceIsReported() {
        XCTAssertFalse(CallRules.voiceChosen(current: nil, reported: nil))        // never picked here: the home's voice
        XCTAssertTrue(CallRules.voiceChosen(current: "maple", reported: nil))     // picked at onboarding
        XCTAssertFalse(CallRules.voiceChosen(current: "maple", reported: "maple"))
        XCTAssertTrue(CallRules.voiceChosen(current: "sol", reported: "maple"))   // picked again in Settings
    }

    func testLateHelloMatchesTheHub() {
        XCTAssertEqual(CallRules.lateGreetingMs, 6000)   // hub voice.LATE_GREETING_MS
    }
}
