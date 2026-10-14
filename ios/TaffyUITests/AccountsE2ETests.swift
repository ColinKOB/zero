import XCTest

/// Accounts, end to end against a front door (alien/frontdoor): a stranger creates an account during the onboarding,
/// which makes a brand-new home, walks the rest of the onboarding, logs out, signs back in with the username and
/// password, and logs out again. Then the sign-in screen's pairing code option opens.
///
/// Run against the QA front door (prefix qa-, so its homes are zero-qa-*), through an ssh tunnel:
///   ssh -f -N -L 18671:127.0.0.1:8671 alien
///   TEST_RUNNER_ZERO_E2E_MODE=account TEST_RUNNER_ZERO_E2E_FRONTDOOR=http://127.0.0.1:18671 \
///   TEST_RUNNER_ZERO_E2E_SHOTS=/tmp/zero-accounts-shots xcodebuild test ... -only-testing:TaffyUITests/AccountsE2ETests
/// Delete the home afterwards: zero-account remove <username> --delete-home (on alien).
final class AccountsE2ETests: XCTestCase {
    var app: XCUIApplication!
    var run: OnboardingRun!

    override func setUp() {
        continueAfterFailure = false
        app = XCUIApplication()
        let config = E2EConfig.current
        app.launchArguments = ["-onboarding", "-light", "-AppleLanguages", "(en)", "-AppleLocale", "en_US",
                               "-frontDoor", config.frontDoor, "-noAutoFill"]
        addUIInterruptionMonitor(withDescription: "System alerts") { alert in
            for label in ["Allow", "OK", "Allow While Using App", "Don't Allow", "Not Now"] where alert.buttons[label].exists {
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

    func testSignUpLogOutSignIn() {
        let config = E2EConfig.current
        guard config.mode == .account else { return }
        run = OnboardingRun(app: app, config: config, test: self, shotsSubdir: "accounts")
        run.note("account \(config.username)")
        app.launch()
        run.walk(timeout: 1200)

        logOut("first")

        let signIn = app.buttons["signInWithPassword"]
        run.require(signIn.waitHittable(15), "the sign-in screen has no Sign In button after logging out")
        signIn.tap()
        let user = app.textFields["signInUsername"]
        run.require(user.waitForExistence(timeout: 10), "Sign In: no username field")
        user.replaceText(config.username)
        let pw = app.secureTextFields["signInPassword"]
        pw.tap()
        pw.typeText("wrong password 1")
        app.buttons["signInSubmit"].tap()
        run.require(app.anyLabel(containing: "do not match").waitForExistence(timeout: 20),
                    "Sign In: a wrong password did not say so")
        run.shots.take("sign-in-wrong-password")
        pw.tap()
        pw.typeText(String(repeating: XCUIKeyboardKey.delete.rawValue, count: 20))
        pw.typeText(config.password)
        run.shots.take("sign-in-typed")
        app.buttons["signInSubmit"].tap()
        let deadline = Date().addingTimeInterval(60)
        while Date() < deadline && !run.isHome() { pause(0.5) }
        run.require(run.isHome(), "Sign In: the home did not open")
        pause(2)
        dismissSystemAlerts(timeout: 2)
        run.shots.take("signed-in-again")
        run.seen.append("signed in again")

        logOut("second")

        let code = app.buttons["signInWithCode"]
        run.require(code.waitHittable(10), "the sign-in screen has no pairing code option")
        code.tap()
        run.require(app.textFields["Pairing Code"].waitForExistence(timeout: 10), "Sign In With a Pairing Code: no code field")
        run.shots.take("pairing-code")
        app.buttons["Back"].tap()
        run.require(app.buttons["createAccount"].waitForExistence(timeout: 10), "Back from the pairing code did not return")
        run.seen.append("pairing code")
    }

    private func logOut(_ which: String) {
        dismissSystemAlerts(timeout: 2)
        for _ in 0..<3 where !app.buttons["Settings"].exists && app.buttons["Back"].exists {
            app.buttons["Back"].firstMatch.tap()
            pause(1)
        }
        let settings = app.buttons["Settings"]
        run.require(settings.waitHittable(20), "no Settings button on the home screen")
        settings.tap()
        let logOut = app.buttons["logOut"]
        var swipes = 0
        while !(logOut.exists && logOut.isHittable) && swipes < 12 {
            app.swipeUp()
            swipes += 1
        }
        run.require(logOut.isHittable, "Settings has no Log Out")
        run.shots.take("settings-log-out-\(which)")
        logOut.tap()
        let confirm = app.buttons.matching(NSPredicate(format: "label == 'Log Out' AND identifier != 'logOut'")).firstMatch
        run.require(confirm.waitHittable(8), "Log Out: no confirmation")
        run.shots.take("log-out-confirm-\(which)")
        confirm.tap()
        run.require(app.buttons["createAccount"].waitForExistence(timeout: 20), "Log Out did not return to the sign-in screen")
        pause(1)
        run.shots.take("logged-out-\(which)")
        run.seen.append("logged out \(which)")
    }
}
