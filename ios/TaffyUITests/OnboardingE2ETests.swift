import XCTest

final class OnboardingE2ETests: XCTestCase {
    var app: XCUIApplication!
    var run: OnboardingRun!

    override func setUp() {
        continueAfterFailure = false
        app = XCUIApplication()
        let config = E2EConfig.current
        app.launchArguments = ["-onboarding", "-light", "-AppleLanguages", "(en)", "-AppleLocale", "en_US", "-noAutoFill"]
        if config.mode == .fixture {
            app.launchArguments.append("-fixture")
        } else {
            // "-server" also lands in UserDefaults' argument domain, which is where the app keeps its address, so the
            // code screen opens with it filled in; "-code" is for the pre-fill WP4 adds.
            app.launchArguments += ["-server", config.server, "-code", config.code]
        }
        addUIInterruptionMonitor(withDescription: "System alerts") { alert in
            for label in ["Allow", "OK", "Allow While Using App", "Don't Allow"] where alert.buttons[label].exists {
                alert.buttons[label].tap()
                return true
            }
            return false
        }
    }

    override func tearDown() {
        run?.writeResult(test: name, passed: (testRun?.totalFailureCount ?? 1) == 0)
        super.tearDown()
    }

    func testStrangerOnboarding() {
        let config = E2EConfig.current
        run = OnboardingRun(app: app, config: config, test: self, shotsSubdir: config.mode == .hub ? "hub" : "fixture")
        app.launch()
        run.walk()
        if config.mode == .hub {
            XCTAssertTrue(app.staticTexts[run.persona.agent_name].waitForExistence(timeout: 30),
                          "the home does not show the lead as \(run.persona.agent_name)")
            for id in run.picked {
                let name = Persona.templateName(id)
                if !app.staticTexts[name].waitForExistence(timeout: 120) {
                    run.note("the home does not list \(name) yet; assert_state.py checks the hub")
                }
            }
            dismissSystemAlerts(timeout: 1)
            run.shots.take("home-with-zips")
        }
    }

    func testSampleHome() {
        let config = E2EConfig.current
        run = OnboardingRun(app: app, config: config, test: self, shotsSubdir: "sample-home")
        app.launchArguments.removeAll { $0 == "-fixture" }
        app.launch()
        XCTAssertTrue(app.staticTexts["Create Your First Zip"].waitForExistence(timeout: 20))
        run.seen.append("meet")
        run.shots.take("meet")
        let link = app.labeledButton("Explore a Sample Home")
        XCTAssertTrue(link.waitHittable(), "no Explore a Sample Home link on the first screen")
        link.tap()
        XCTAssertTrue(app.anyLabel(containing: "Sample Home").waitForExistence(timeout: 15), "the sample home did not open")
        pause(1.5)
        run.seen.append("sample home")
        run.shots.take("sample-home")
        let leave = app.labeledButton("Leave")
        if leave.waitHittable(5) {
            leave.tap()
            XCTAssertTrue(app.staticTexts["Create Your First Zip"].waitForExistence(timeout: 15),
                          "leaving the sample home did not return to the onboarding")
            run.seen.append("meet again")
            run.shots.take("sample-home-left")
        } else {
            run.note("no Leave button on the sample home banner")
        }
    }
}

final class OnboardingRun {
    let app: XCUIApplication
    let config: E2EConfig
    let persona: Persona
    let shots: Shots
    weak var test: XCTestCase?
    var seen: [String] = []
    var notes: [String] = []
    var picked: [String] = []
    private let resultDir: URL?

    init(app: XCUIApplication, config: E2EConfig, test: XCTestCase, shotsSubdir: String) {
        self.app = app
        self.config = config
        self.persona = config.persona
        self.test = test
        let dir = config.shotsDir?.appendingPathComponent(shotsSubdir, isDirectory: true)
        if let dir { try? FileManager.default.removeItem(at: dir) }
        shots = Shots(dir: dir, test: test)
        resultDir = dir
    }

    func note(_ text: String) {
        notes.append(text)
        print("[e2e] \(text)")
    }

    func fail(_ text: String, file: StaticString = #filePath, line: UInt = #line) {
        notes.append("FAIL: \(text)")
        shots.take("failure")
        let tree = XCTAttachment(string: app.debugDescription)
        tree.name = "element tree at failure"
        tree.lifetime = .keepAlways
        test?.add(tree)
        writeResult(test: test?.name ?? "", passed: false)
        XCTFail(text, file: file, line: line)
    }

    func require(_ condition: Bool, _ text: String, file: StaticString = #filePath, line: UInt = #line) {
        if !condition { fail(text, file: file, line: line) }
    }

    func walk(timeout: TimeInterval = 600) {
        let deadline = Date().addingTimeInterval(timeout)
        var unknown = 0
        var repeats = 0
        while Date() < deadline {
            if let screen = allScreens.first(where: { $0.isShown(app, self) }) {
                unknown = 0
                if seen.last == screen.name {
                    repeats += 1
                    note("the \(screen.name) screen is still up; acting on it again")
                    if repeats > 3 { fail("stuck on the \(screen.name) screen"); return }
                } else {
                    repeats = 0
                    seen.append(screen.name)
                    pause(0.6)
                    shots.take(screen.name)
                }
                screen.complete(app, self)
                let gone = Date().addingTimeInterval(20)
                while Date() < gone && screen.isShown(app, self) { pause(0.3) }
                continue
            }
            if isHome() {
                seen.append("home")
                dismissSystemAlerts()
                shots.take("home")
                return
            }
            unknown += 1
            if unknown >= 8 {
                let texts = app.staticTexts.allElementsBoundByIndex.prefix(20).map(\.label).joined(separator: " | ")
                fail("an onboarding screen the harness does not know. Texts on it: \(texts)")
                return
            }
            pause(1)
        }
        fail("the onboarding did not finish in \(Int(timeout)) seconds; screens seen: \(seen.joined(separator: ", "))")
    }

    func isHome() -> Bool {
        app.collectionViews.firstMatch.exists || app.staticTexts["Loading your team"].exists
            || app.anyLabel(containing: "Sample Home").exists
            || app.staticTexts["First Things I Can Do"].exists
    }

    func writeResult(test: String, passed: Bool) {
        guard let dir = resultDir else { return }
        let result: [String: Any] = ["test": test, "passed": passed, "mode": config.mode.rawValue, "screens": seen,
                                     "picked": picked, "notes": notes]
        if let data = try? JSONSerialization.data(withJSONObject: result, options: [.prettyPrinted, .sortedKeys]) {
            try? data.write(to: dir.appendingPathComponent("ui-result.json"))
        }
    }
}
