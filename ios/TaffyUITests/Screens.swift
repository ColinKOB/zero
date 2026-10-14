import XCTest

/// One page object per onboarding screen. The driver (OnboardingRun.walk) asks each which one is up, takes its
/// screenshot, and lets it do what the stranger would do there, until the home screen opens. The order of screens is
/// not hard-coded, so the flow can change (WP4 adds the mailbox, calendar and "how your Zips should ask" screens and
/// skips the look-around on a server home) without changing the driver.
///
/// A screen is recognized by an accessibility identifier "onboarding.<step>" when the app sets one (preferred, and
/// stable under copy edits), otherwise by its headline or by a control only it has. Labels come from
/// ios/Taffy/Views/Onboarding/*.swift and, for the screens WP4 adds, from the plan's wording (section 6); those are
/// marked "plan wording" and are the first place to look if a run stops on an unknown screen.
protocol OnboardingScreen {
    var name: String { get }
    /// OnboardingModel.Step's raw value, for the identifier "onboarding.<step>".
    var step: String { get }
    var headlines: [String] { get }
    func recognizes(_ app: XCUIApplication, _ run: OnboardingRun) -> Bool
    func complete(_ app: XCUIApplication, _ run: OnboardingRun)
}

extension OnboardingScreen {
    func isShown(_ app: XCUIApplication, _ run: OnboardingRun) -> Bool {
        if app.anyIdentifier("onboarding.\(step)").exists { return true }
        if headlines.contains(where: { app.staticTexts[$0].exists }) { return true }
        return recognizes(app, run)
    }

    func recognizes(_ app: XCUIApplication, _ run: OnboardingRun) -> Bool { false }

    /// Taps the first of these buttons that is there and can be tapped.
    @discardableResult
    func tapFirst(_ app: XCUIApplication, _ labels: [String], timeout: TimeInterval = 10) -> Bool {
        let deadline = Date().addingTimeInterval(timeout)
        repeat {
            for l in labels {
                let b = app.labeledButton(l)
                if b.exists && b.isHittable && b.isEnabled { b.tap(); return true }
            }
            pause(0.25)
        } while Date() < deadline
        return false
    }
}

// MARK: 1 to 3: meet, name, talk

struct MeetScreen: OnboardingScreen {
    let name = "meet", step = "meet", headlines = ["Create Your First Zip"]
    func complete(_ app: XCUIApplication, _ run: OnboardingRun) {
        run.require(tapFirst(app, ["Continue"]), "Create Your First Zip: no Continue button")
    }
}

struct NameScreen: OnboardingScreen {
    let name = "name", step = "name", headlines = ["What Do You Want to Name It?"]
    func complete(_ app: XCUIApplication, _ run: OnboardingRun) {
        let field = app.textFields["Agent name"]
        run.require(field.waitForExistence(timeout: 10), "Name It: no name field")
        field.replaceText(run.persona.agent_name)
        run.shots.take("name-typed")
        app.typeText("\n")
        _ = app.anyLabel(containing: " says: ").waitForExistence(timeout: 8)
    }
}

struct TalkScreen: OnboardingScreen {
    let name = "talk", step = "talk", headlines: [String] = []

    func recognizes(_ app: XCUIApplication, _ run: OnboardingRun) -> Bool {
        app.anyLabel(containing: " says: ").exists && !app.staticTexts["Create Your First Zip"].exists
    }

    /// The persona's answer for the question on screen, from the field's placeholder or the agent's line.
    func answer(placeholder: String, line: String, persona: Persona) -> String? {
        let p = placeholder.lowercased(), l = line.lowercased()
        if p.contains("your name") || l.contains("what should i call you") { return persona.person_name }
        if p.contains("what you do") || l.contains("keeping you busy") { return persona.about["work"] }
        if p.contains("city") || l.contains("where are you living") { return persona.about["home"] }
        if p.contains("age") || l.contains("how old") { return persona.about["age"] }
        if p.contains("wife") || l.contains("who are your people") { return persona.about["family"] }
        if l.contains("sleep") || p.contains("sleep") { return persona.extra_answers?["sleep"] }
        if l.contains("email") || p.contains("email") { return persona.extra_answers?["email"] }
        return nil
    }

    func complete(_ app: XCUIApplication, _ run: OnboardingRun) {
        for _ in 0..<12 {
            if app.labeledButton("Continue").exists && app.labeledButton("Continue").isHittable {
                run.shots.take("talk-done")
                app.labeledButton("Continue").tap()
                return
            }
            let field = app.textFields.firstMatch
            guard field.waitForExistence(timeout: 8) else { continue }
            let bubble = app.anyLabel(containing: " says: ")
            let line = bubble.exists ? bubble.label : ""
            if bubble.exists && bubble.isHittable { bubble.tap() }
            guard let text = answer(placeholder: field.placeholderValue ?? "", line: line, persona: run.persona) else {
                run.note("Talk: no answer for \"\(line)\"; skipping it")
                if app.labeledButton("Skip").waitEnabled() { app.labeledButton("Skip").tap() }
                pause(0.6)
                continue
            }
            field.tap()
            field.typeText(text)
            let send = app.labeledButton("Send")
            run.require(send.waitEnabled(15), "Talk: Send never enabled for \"\(line)\"")
            send.tap()
            let deadline = Date().addingTimeInterval(8)
            while Date() < deadline && app.anyLabel(containing: " says: ").label == line { pause(0.2) }
        }
        run.fail("Talk: the questions never finished")
    }
}

// MARK: 4: the account (a new home), and 5: the person's own plan

struct AccountScreen: OnboardingScreen {
    let name = "account", step = "account", headlines = ["Create Your Account", "You're All Set"]
    func recognizes(_ app: XCUIApplication, _ run: OnboardingRun) -> Bool { app.anyLabel(containing: "Setting Up ").exists }
    func complete(_ app: XCUIApplication, _ run: OnboardingRun) {
        if app.textFields["signUpUsername"].exists {
            app.textFields["signUpUsername"].replaceText(run.config.username)
            let pw = app.secureTextFields["signUpPassword"]
            pw.tap()
            pw.typeText(run.config.password)
            run.shots.take("account-typed")
            run.require(tapFirst(app, ["Create Account"]), "Create Your Account: Create Account never enabled")
        }
        if app.anyLabel(containing: "Setting Up ").waitForExistence(timeout: 20) {
            pause(4)
            run.shots.take("account-building")
        }
        let deadline = Date().addingTimeInterval(480)
        var shotLate = false
        while Date() < deadline && isShown(app, run) {
            if app.staticTexts["Create Your Account"].exists, let problem = app.staticTexts.allElementsBoundByIndex
                .map(\.label).first(where: { $0.contains("Try ") || $0.contains("taken") }) {
                run.fail("Create Your Account: \(problem)")
                return
            }
            if !shotLate && app.anyLabel(containing: "min ").exists { run.shots.take("account-still-building"); shotLate = true }
            pause(1)
        }
        if isShown(app, run) { run.fail("Create Your Account: the new home was not ready in eight minutes") }
    }
}

struct PlanScreen: OnboardingScreen {
    let name = "plan", step = "plan", headlines = ["Bring Your Own Claude", "Claude Is Connected"]
    func complete(_ app: XCUIApplication, _ run: OnboardingRun) {
        if app.anyIdentifier("claudeSignIn").waitForExistence(timeout: 20), run.config.mode == .account {
            app.anyIdentifier("claudeSignIn").tap()
            pause(6)
            let safari = XCUIApplication(bundleIdentifier: "com.apple.mobilesafari")
            if safari.wait(for: .runningForeground, timeout: 10) {
                pause(3)
                run.shots.take("plan-claude-page")
                app.activate()
                pause(1)
                run.shots.take("plan-paste-code")
            } else {
                run.note("Bring Your Own Claude: Safari did not open the Claude page")
            }
        }
        run.require(tapFirst(app, ["Skip for Now", "Continue"], timeout: 30), "Bring Your Own Claude: no Skip for Now")
    }
}

// MARK: 4 and 5: the computer and the code

struct MacScreen: OnboardingScreen {
    let name = "mac", step = "mac", headlines = ["Where Should I Live?"]
    func recognizes(_ app: XCUIApplication, _ run: OnboardingRun) -> Bool {
        app.labeledButton("I Have My Code").exists && app.labeledButton("I Don't Have a Computer").exists
    }
    func complete(_ app: XCUIApplication, _ run: OnboardingRun) {
        run.require(tapFirst(app, ["A Mac That Stays On"]), "Where Should I Live?: no A Mac That Stays On")
    }
}

struct GuideScreen: OnboardingScreen {
    let name = "guide", step = "guide", headlines = ["Set Me Up on the Mac That Stays On"]
    func recognizes(_ app: XCUIApplication, _ run: OnboardingRun) -> Bool { app.labeledButton("Copy the Prompt").exists }
    func complete(_ app: XCUIApplication, _ run: OnboardingRun) {
        run.require(tapFirst(app, ["I Have My Code"]), "Setup guide: no I Have My Code button")
    }
}

/// WP4: I Have My Code may open the camera for a zero:// QR code. The simulator has no camera, so the run types the
/// code instead (plan wording: a button to type it).
struct ScanScreen: OnboardingScreen {
    let name = "scan", step = "scan", headlines: [String] = []
    func recognizes(_ app: XCUIApplication, _ run: OnboardingRun) -> Bool {
        app.anyLabel(containing: "Scan").exists && app.anyLabel(containing: "code").exists
            && !app.textFields["Access code"].exists
    }
    func complete(_ app: XCUIApplication, _ run: OnboardingRun) {
        let typeIt = app.buttons.matching(NSPredicate(format: "label CONTAINS[c] 'type' OR label CONTAINS[c] 'enter'")).firstMatch
        run.require(typeIt.waitHittable(), "Scan: no way to type the code instead")
        typeIt.tap()
    }
}

struct CodeScreen: OnboardingScreen {
    let name = "code", step = "code", headlines = ["Type Your Pairing Code", "Type the Code from Your Mac"]
    func recognizes(_ app: XCUIApplication, _ run: OnboardingRun) -> Bool { app.textFields["Access code"].exists }
    func complete(_ app: XCUIApplication, _ run: OnboardingRun) {
        let field = app.textFields["Access code"]
        run.require(field.waitForExistence(timeout: 10), "Code: no code field")
        let code = run.config.mode == .hub ? run.config.code : "TEST2345"
        if (field.value as? String)?.filter({ $0.isLetter || $0.isNumber }).uppercased() != code.uppercased() {
            field.replaceText(code)
        }
        let address = app.textFields["Connection address"]
        if address.exists, address.isHittable, (address.value as? String) != run.config.server {
            address.replaceText(run.config.server)
            run.note("Code: typed the address; the field now holds \((address.value as? String) ?? "nothing")")
        }
        run.shots.take("code-typed")
        run.require(tapFirst(app, ["Connect"]), "Code: Connect never enabled")
        let deadline = Date().addingTimeInterval(45)
        while Date() < deadline && field.exists {
            if app.staticTexts["We're connected."].exists { break }
            pause(0.3)
        }
        if !app.staticTexts["We're connected."].exists && field.exists {
            let shown = app.staticTexts.allElementsBoundByIndex.map(\.label).filter { !$0.isEmpty }.joined(separator: " | ")
            run.fail("Code: did not connect to \(run.config.server). On screen: \(shown)")
        }
        _ = app.staticTexts["Who Gets What You Say?"].waitForExistence(timeout: 10)
    }
}

// MARK: 6 to 8: consent, why, and the new screens

struct ConsentScreen: OnboardingScreen {
    let name = "consent", step = "consent", headlines = ["Who Gets What You Say?"]
    func complete(_ app: XCUIApplication, _ run: OnboardingRun) {
        run.require(tapFirst(app, ["Agree"]), "Consent: no Agree button")
    }
}

struct WhyScreen: OnboardingScreen {
    let name = "why", step = "why", headlines = ["Why We Ask"]
    func complete(_ app: XCUIApplication, _ run: OnboardingRun) {
        run.require(tapFirst(app, ["Continue"]), "Why We Ask: no Continue button")
    }
}

/// The email step (WP4, Views/Onboarding/OnboardingHome.swift and Views/MailAndCalendarViews.swift): "Which Email
/// Should Your Zips Read?" before the picks, or "Your Zips Need Your Email" after them. Sign In with Google is not
/// something a simulator run can do, so the stranger takes Use Another Email Provider, picks Other Email, and types
/// the test home's GreenMail mailbox (its IMAP and SMTP as the hub sees them, on alien's loopback). Check and Add has
/// the hub sign in before it adds the mailbox.
struct MailboxScreen: OnboardingScreen {
    let name = "mail", step = "mail", headlines = ["Which Email Should Your Zips Read?", "Your Zips Need Your Email"]
    func recognizes(_ app: XCUIApplication, _ run: OnboardingRun) -> Bool {
        app.labeledButton("Use Another Email Provider").exists && !app.staticTexts["Add a Mailbox"].exists
    }
    func complete(_ app: XCUIApplication, _ run: OnboardingRun) {
        let onward = ["Continue", "Get Started"]
        if tapFirst(app, onward, timeout: 1) { return }
        guard run.config.addMailbox, let mb = run.persona.mailbox else {
            run.note("Email: this run adds no mailbox; skipping")
            run.require(tapFirst(app, ["Skip for Now"]), "Email: no Skip for Now")
            return
        }
        run.require(tapFirst(app, ["Use Another Email Provider"]), "Email: no Use Another Email Provider")
        run.require(app.staticTexts["Add a Mailbox"].waitForExistence(timeout: 10), "Email: the Add a Mailbox sheet did not open")
        _ = tapFirst(app, [mb.provider, "Other Email"], timeout: 3)
        pause(0.5)
        func text(_ placeholder: String, _ value: String?, index: Int = 0) {
            guard let value else { return }
            let f = app.textFields.matching(NSPredicate(format: "placeholderValue == %@", placeholder)).element(boundBy: index)
            if f.waitForExistence(timeout: 3) { f.fill(value, in: app) } else { run.note("Email: no \(placeholder) field") }
        }
        text("Email address", mb.address)
        let pw = app.secureTextFields.firstMatch
        run.require(pw.waitForExistence(timeout: 5), "Email: no password field")
        pw.fill(mb.password, in: app)
        text("Incoming server (IMAP)", mb.imap_host)
        text("Port", mb.imap_port.map(String.init), index: 0)
        text("Outgoing server (SMTP)", mb.smtp_host)
        text("Port", mb.smtp_port.map(String.init), index: 1)
        run.shots.take("mail-filled")
        run.require(tapFirst(app, ["Check and Add"]), "Email: Check and Add never enabled")
        let done = app.labeledButton("Done")
        if !done.waitForExistence(timeout: 90) {
            let shown = app.staticTexts.allElementsBoundByIndex.map(\.label).filter { !$0.isEmpty }.suffix(6)
            run.fail("Email: the hub did not add the mailbox. On screen: \(shown.joined(separator: " | "))")
            return
        }
        run.shots.take("mail-added")
        done.tap()
        _ = app.staticTexts["Add a Mailbox"].waitForNonExistence(timeout: 10)
        run.shots.take("mail-connected")
        run.require(tapFirst(app, onward, timeout: 20), "Email: no Continue after the mailbox was added")
    }
}

/// WP4: the calendar half of "Your email and calendar". The stranger skips it (plan: iCloud, Google's private
/// address, or Skip).
struct CalendarScreen: OnboardingScreen {
    let name = "calendar", step = "calendar", headlines = ["Add a Calendar", "Your Calendar", "Which Calendar Should Your Zips Read?"]
    func recognizes(_ app: XCUIApplication, _ run: OnboardingRun) -> Bool {
        app.anyLabel(containing: "calendar should your Zips").exists
    }
    func complete(_ app: XCUIApplication, _ run: OnboardingRun) {
        run.require(tapFirst(app, ["Skip", "Skip for Now", "Not Now"]), "Calendar: no Skip")
    }
}

/// WP4: "How your Zips should ask", three rows with defaults. The stranger keeps the defaults.
struct AskScreen: OnboardingScreen {
    let name = "ask", step = "ask", headlines = ["How Your Zips Should Ask", "How Should Your Zips Ask?"]
    func recognizes(_ app: XCUIApplication, _ run: OnboardingRun) -> Bool { app.anyLabel(containing: "Should Ask").exists }
    func complete(_ app: XCUIApplication, _ run: OnboardingRun) {
        run.require(tapFirst(app, ["Continue", "Looks Good", "Next", "Done"]), "How Zips Ask: no Continue")
    }
}

// MARK: Voice, look-around, what I found, Google

struct VoiceScreen: OnboardingScreen {
    let name = "voice", step = "voice", headlines = ["Pick My Voice"]
    func complete(_ app: XCUIApplication, _ run: OnboardingRun) {
        let row = app.staticTexts[run.persona.voice]
        if row.waitForExistence(timeout: 5) { row.tapScrolling(in: app) }
        run.shots.take("voice-picked")
        let use = app.labeledButton("Use \(run.persona.voice)").exists ? app.labeledButton("Use \(run.persona.voice)")
            : app.button(startingWith: "Use ")
        run.require(use.waitHittable(), "Pick My Voice: no Use button")
        use.tap()
    }
}

struct LookScreen: OnboardingScreen {
    let name = "look", step = "look", headlines = ["Let Me Take a Look at Your Mac"]
    func complete(_ app: XCUIApplication, _ run: OnboardingRun) {
        if app.labeledButton("I'll Stay").exists { app.labeledButton("I'll Stay").tap() }
        run.require(tapFirst(app, ["See What I Found"], timeout: 30), "Looking: never finished")
    }
}

struct FoundScreen: OnboardingScreen {
    let name = "found", step = "found", headlines = ["Here's What I Found", "Where Should We Start?"]
    func complete(_ app: XCUIApplication, _ run: OnboardingRun) {
        var picked: [String] = []
        for id in run.persona.picks {
            let card = app.button(startingWith: Persona.templateName(id) + ".")
            var found = card.waitForExistence(timeout: picked.isEmpty ? 20 : 3)
            var swipes = 0
            while !found && swipes < 6 {   // the list is lazy: a card below the fold does not exist until scrolled to
                app.swipeUp(velocity: .slow)
                swipes += 1
                found = card.waitForExistence(timeout: 1)
            }
            guard found else {
                let shown = app.buttons.allElementsBoundByIndex.map(\.label).filter { $0.contains(".") }
                if run.config.mode == .fixture {
                    run.note("Where Should We Start: the fixture offers no \(Persona.templateName(id)) card")
                    continue
                }
                run.fail("Where Should We Start: no \(Persona.templateName(id)) card. Cards: \(shown.joined(separator: " | "))")
                continue
            }
            if !card.isSelected {
                card.tapScrolling(in: app)
                pause(0.4)
            }
            if !card.isSelected { run.note("Where Should We Start: \(Persona.templateName(id)) did not show as picked") }
            picked.append(id)
        }
        run.picked = picked
        run.shots.take("found-picked")
        let go = app.buttons.matching(NSPredicate(format: "label BEGINSWITH 'Add ' OR label == 'Get Started'")).firstMatch
        for attempt in 0..<3 {
            run.require(go.waitHittable(), "Where Should We Start: no Add or Get Started button")
            go.tap()
            let deadline = Date().addingTimeInterval(8)
            while Date() < deadline && isShown(app, run) { pause(0.3) }
            if !isShown(app, run) { return }
            run.note("Where Should We Start: still up after tapping \(go.label) (try \(attempt + 1))")
        }
    }
}

struct GoogleScreen: OnboardingScreen {
    let name = "google", step = "google", headlines = ["Connect Google", "Google Is Connected"]
    func complete(_ app: XCUIApplication, _ run: OnboardingRun) {
        run.require(tapFirst(app, ["Skip for Now", "Get Started"]), "Connect Google: no Skip for Now")
    }
}

let allScreens: [OnboardingScreen] = [
    MeetScreen(), NameScreen(), TalkScreen(), AccountScreen(), PlanScreen(), MacScreen(), GuideScreen(), ScanScreen(), CodeScreen(), ConsentScreen(), WhyScreen(),
    MailboxScreen(), CalendarScreen(), AskScreen(), VoiceScreen(), LookScreen(), FoundScreen(), GoogleScreen(),
]
