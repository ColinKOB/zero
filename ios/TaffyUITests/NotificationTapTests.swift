import XCTest

final class NotificationTapTests: XCTestCase {
    private var app: XCUIApplication!
    private let springboard = XCUIApplication(bundleIdentifier: "com.apple.springboard")

    private static let checkinTitle = "Daily spending update"
    private static let moneyChatLine = "How much did we spend at Walmart and Target this month?"
    private static let noticeLine = "dentist bill"

    override func setUp() {
        continueAfterFailure = false
        app = XCUIApplication()
        app.launchArguments = ["-fixture", "-light", "-askNotifications", "-AppleLanguages", "(en)", "-AppleLocale", "en_US"]
        addUIInterruptionMonitor(withDescription: "Notification permission") { alert in
            for label in ["Allow", "OK"] where alert.buttons[label].exists {
                alert.buttons[label].tap()
                return true
            }
            return false
        }
    }

    // MARK: The check-in from tonight (feed 181's kind)

    func testWarmBannerTapOpensMoney() {
        launch(notification: "checkin", in: 6)
        XCUIDevice.shared.press(.home)
        tapNotification(containing: Self.checkinTitle)
        assertChatOpen(showing: Self.moneyChatLine)
    }

    func testColdBannerTapOpensMoney() {
        launch(notification: "checkin", in: 8)
        app.terminate()
        tapNotification(containing: Self.checkinTitle)
        assertChatOpen(showing: Self.moneyChatLine)
    }

    func testWarmLockScreenTapOpensMoney() {
        launch(notification: "checkin", in: 8)
        lock()
        tapNotification(containing: Self.checkinTitle)
        assertChatOpen(showing: Self.moneyChatLine)
    }

    func testColdLockScreenTapOpensMoney() {
        launch(notification: "checkin", in: 10)
        app.terminate()
        lock()
        tapNotification(containing: Self.checkinTitle)
        assertChatOpen(showing: Self.moneyChatLine)
    }

    func testForegroundBannerTapOpensMoney() {
        launch(notification: "checkin", in: 6)
        tapNotification(containing: Self.checkinTitle)
        assertChatOpen(showing: Self.moneyChatLine)
    }

    // MARK: A question from Zero (Yes, Not Now and Reply on a long press; a plain tap opens Zero's chat)

    func testColdNoticeTapOpensZero() {
        launch(notification: "notice", in: 8)
        app.terminate()
        tapNotification(containing: Self.noticeLine)
        assertChatOpen(showing: "Zero")
    }

    // MARK: Helpers

    private func launch(notification kind: String, in seconds: Int) {
        if ProcessInfo.processInfo.environment["ZERO_EXTERNAL_PUSH"] != "1" {
            app.launchArguments += ["-testNotification", "\(kind):\(seconds)"]
        }
        app.launch()
        XCTAssertTrue(app.staticTexts["Money"].waitForExistence(timeout: 20), "the fixture home did not open")
        // The permission alert is a springboard alert; a tap on the app lets the interruption monitor answer it.
        acceptPermissionIfAsked()
    }

    private func acceptPermissionIfAsked() {
        let alert = springboard.alerts.firstMatch
        if alert.waitForExistence(timeout: 3) {
            for label in ["Allow", "OK"] where alert.buttons[label].exists {
                alert.buttons[label].tap()
                break
            }
        }
    }

    private func lock() {
        XCUIDevice.shared.perform(NSSelectorFromString("pressLockButton"))
        Thread.sleep(forTimeInterval: 1.5)
    }

    private func tapNotification(containing text: String, file: StaticString = #filePath, line: UInt = #line) {
        let note = springboard.descendants(matching: .any)
            .matching(NSPredicate(format: "label CONTAINS[c] %@", text)).firstMatch
        XCTAssertTrue(note.waitForExistence(timeout: 30), "no notification with \"\(text)\" appeared", file: file, line: line)
        Thread.sleep(forTimeInterval: 0.8)
        note.tap()
        // On the lock screen the tap asks to unlock first ("Swipe up to open"); the simulator has no passcode, so a
        // swipe up from the bottom edge unlocks it and the tapped notification's app opens.
        if !XCUIApplication().wait(for: .runningForeground, timeout: 4) {
            if note.exists && note.isHittable { note.swipeRight() }   // the lock screen's other way to open it
            Thread.sleep(forTimeInterval: 1.5)
        }
        if !XCUIApplication().wait(for: .runningForeground, timeout: 2) {
            let bottom = springboard.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.995))
            bottom.press(forDuration: 0.1, thenDragTo: springboard.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.3)))
        }
    }

    private func assertChatOpen(showing text: String, file: StaticString = #filePath, line: UInt = #line) {
        let app = XCUIApplication()
        XCTAssertTrue(app.wait(for: .runningForeground, timeout: 20), "the app is not running after the tap",
                      file: file, line: line)
        XCTAssertTrue(app.buttons["Back"].waitForExistence(timeout: 20), "no chat opened", file: file, line: line)
        let shown = app.descendants(matching: .any).matching(NSPredicate(format: "label CONTAINS[c] %@", text)).firstMatch
        XCTAssertTrue(shown.waitForExistence(timeout: 10), "the chat with \"\(text)\" did not open", file: file, line: line)
        Thread.sleep(forTimeInterval: 3)
        XCTAssertEqual(app.state, .runningForeground, "the app stopped after opening the chat", file: file, line: line)
    }
}
