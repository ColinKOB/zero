import XCTest

/// How a run is set up. tools/zero-e2e.sh passes these as TEST_RUNNER_<NAME> environment variables, which xcodebuild
/// hands to the test runner without the prefix:
///
///   ZERO_E2E_MODE      fixture (no hub: the app's -fixture mode, any code connects) or hub (a real hub)
///   ZERO_E2E_SERVER    the hub's address, e.g. http://127.0.0.1:8652 (the ssh tunnel to the test home, or the
///                      dry-mode fixture hub on the Mac)
///   ZERO_E2E_CODE      a pairing code for that hub
///   ZERO_E2E_PERSONA   the stranger's answers as JSON (hub/tests/harness/persona.json); built-in defaults otherwise
///   ZERO_E2E_SHOTS     a folder on the Mac for one PNG per screen (also attached to the result bundle)
///   ZERO_E2E_MAILBOX   1 when the hub can check the persona's mailbox (the test home's GreenMail)
struct E2EConfig {
    enum Mode: String { case fixture, hub, account }

    let mode: Mode
    let server: String
    let code: String
    let persona: Persona
    let shotsDir: URL?
    let addMailbox: Bool
    /// account mode (AccountsE2ETests): the front door to sign up at, and the new account's details.
    let frontDoor: String
    let username: String
    let password: String

    static let current = E2EConfig(environment: ProcessInfo.processInfo.environment)

    init(environment env: [String: String]) {
        func value(_ key: String) -> String? { env[key].flatMap { $0.isEmpty ? nil : $0 } }
        mode = Mode(rawValue: value("ZERO_E2E_MODE") ?? "") ?? .fixture
        server = value("ZERO_E2E_SERVER") ?? "http://127.0.0.1:8652"
        code = value("ZERO_E2E_CODE") ?? "TEST2345"
        persona = env["ZERO_E2E_PERSONA"].flatMap { try? JSONDecoder().decode(Persona.self, from: Data($0.utf8)) } ?? .sam
        shotsDir = env["ZERO_E2E_SHOTS"].flatMap { $0.isEmpty ? nil : URL(fileURLWithPath: $0, isDirectory: true) }
        addMailbox = env["ZERO_E2E_MAILBOX"] == "1"
        frontDoor = value("ZERO_E2E_FRONTDOOR") ?? "http://127.0.0.1:18671"
        username = value("ZERO_E2E_USERNAME") ?? "qa-sam\(Int(Date().timeIntervalSince1970) % 100000)"
        password = value("ZERO_E2E_PASSWORD") ?? "zero test password"
    }
}

/// The stranger. Mirrors hub/tests/harness/persona.json, which zero-e2e.sh passes in so the two never drift.
struct Persona: Codable {
    struct Mailbox: Codable {
        var provider: String
        var address: String
        var password: String
        var imap_host: String?
        var imap_port: Int?
        var smtp_host: String?
        var smtp_port: Int?
    }

    var person_name: String
    var agent_name: String
    var about: [String: String]
    var extra_answers: [String: String]?
    var voice: String
    var picks: [String]
    var mailbox: Mailbox?

    static let sam = Persona(
        person_name: "Sam Rivera", agent_name: "Nova",
        about: ["work": "I run a small bakery", "home": "Portland, Oregon", "age": "34",
                "family": "My partner Alex and our dog Pepper"],
        extra_answers: ["sleep": "10 p.m. to 6 a.m.", "email": "sam@zero.test"],
        voice: "Puck", picks: ["inbox", "todo"],
        mailbox: .init(provider: "Other Email", address: "sam@zero.test", password: "zero-test-mail",
                       imap_host: "127.0.0.1", imap_port: 18551, smtp_host: "127.0.0.1", smtp_port: 18550))

    /// The template's display name for a pick ("inbox" -> "Inbox", "todo" -> "To-Do").
    static func templateName(_ id: String) -> String {
        ["todo": "To-Do", "brief": "Daily Brief", "saver": "Money Saver"][id] ?? id.prefix(1).uppercased() + id.dropFirst()
    }
}

/// Screenshots, one per screen, numbered in the order they were seen.
final class Shots {
    private var count = 0
    private let dir: URL?
    private weak var test: XCTestCase?

    init(dir: URL?, test: XCTestCase) {
        self.dir = dir
        self.test = test
        if let dir { try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true) }
    }

    func take(_ name: String) {
        count += 1
        let shot = XCUIScreen.main.screenshot()
        let label = String(format: "%02d-%@", count, name)
        let attachment = XCTAttachment(screenshot: shot)
        attachment.name = label
        attachment.lifetime = .keepAlways
        test?.add(attachment)
        if let dir { try? shot.pngRepresentation.write(to: dir.appendingPathComponent(label + ".png")) }
    }
}

extension XCUIApplication {
    /// A static text, button or other element whose label contains `text` (case-insensitive).
    func anyLabel(containing text: String) -> XCUIElement {
        descendants(matching: .any).matching(NSPredicate(format: "label CONTAINS[c] %@", text)).firstMatch
    }

    /// The button with exactly this label. Not `buttons[label]`, which also matches identifiers: the keyboard's
    /// return key has the identifier "Send" when the field's submit label is .send.
    func labeledButton(_ label: String) -> XCUIElement {
        buttons.matching(NSPredicate(format: "label == %@", label)).firstMatch
    }

    func anyIdentifier(_ id: String) -> XCUIElement {
        descendants(matching: .any).matching(identifier: id).firstMatch
    }

    /// A button whose label starts with `prefix` ("Inbox." for the template card "Inbox. Gets your email...").
    func button(startingWith prefix: String) -> XCUIElement {
        buttons.matching(NSPredicate(format: "label BEGINSWITH[c] %@", prefix)).firstMatch
    }

    func field(matching words: [String], secure: Bool = false) -> XCUIElement? {
        let query = secure ? secureTextFields : textFields
        for w in words {
            let p = NSPredicate(format: "label CONTAINS[c] %@ OR placeholderValue CONTAINS[c] %@ OR identifier CONTAINS[c] %@", w, w, w)
            let f = query.matching(p).firstMatch
            if f.exists { return f }
        }
        return nil
    }
}

extension XCUIElement {
    @discardableResult
    func waitHittable(_ timeout: TimeInterval = 10) -> Bool {
        let deadline = Date().addingTimeInterval(timeout)
        while Date() < deadline {
            if exists && isHittable { return true }
            RunLoop.current.run(until: Date().addingTimeInterval(0.2))
        }
        return exists && isHittable
    }

    func waitEnabled(_ timeout: TimeInterval = 15) -> Bool {
        let deadline = Date().addingTimeInterval(timeout)
        while Date() < deadline {
            if exists && isEnabled && isHittable { return true }
            RunLoop.current.run(until: Date().addingTimeInterval(0.2))
        }
        return false
    }

    /// Fills a field that may sit under the keyboard or below the fold: scrolls until it can be tapped first.
    func fill(_ text: String, in app: XCUIApplication) {
        var n = 0
        while exists && !isHittable && n < 4 {
            app.swipeUp(velocity: .slow)
            n += 1
        }
        replaceText(text)
    }

    /// Taps after scrolling it into view (SwiftUI scroll views do not always do this on their own).
    func tapScrolling(in app: XCUIApplication, tries: Int = 6) {
        var n = 0
        while !(exists && isHittable) && n < tries {
            app.swipeUp(velocity: .slow)
            n += 1
        }
        tap()
    }

    /// Replaces whatever the field holds with `text`.
    /// Replaces whatever the field holds with `text`, and checks it took (a field that moves when the keyboard
    /// comes up can miss the first keystrokes).
    func replaceText(_ text: String) {
        for _ in 0..<3 {
            tap()
            pause(0.4)
            if let current = value as? String, !current.isEmpty, current != placeholderValue, current != label {
                typeText(String(repeating: XCUIKeyboardKey.delete.rawValue, count: current.count))
            }
            typeText(text)
            if let now = value as? String, now.replacingOccurrences(of: " ", with: "").lowercased()
                .hasSuffix(text.replacingOccurrences(of: " ", with: "").lowercased()) { return }
        }
    }
}

/// Answers a system alert (the notification prompt the home asks for) through SpringBoard, so it does not cover
/// the screenshots. The interruption monitor only fires on the next interaction, which a screenshot is not.
func dismissSystemAlerts(timeout: TimeInterval = 3) {
    let springboard = XCUIApplication(bundleIdentifier: "com.apple.springboard")
    let deadline = Date().addingTimeInterval(timeout)
    while Date() < deadline {
        let alert = springboard.alerts.firstMatch
        if alert.exists {
            for label in ["Allow", "OK", "Allow While Using App"] where alert.buttons[label].exists {
                alert.buttons[label].tap()
                pause(0.8)
                break
            }
        }
        pause(0.3)
    }
}

func pause(_ seconds: TimeInterval) {
    RunLoop.current.run(until: Date().addingTimeInterval(seconds))
}
