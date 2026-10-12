import Foundation

// The guided setup's step script. Shared by the app (which shows the steps in its own window) and the Mac
// bridge (which finds each target in the person's browser and draws the spotlight over it).
//
// Targets are found by what a person can see: the button's words, its accessible label, or a stable page id.
// Every target lists several wordings, so a renamed button ("Get API key" to "API keys") still matches.
// Nothing here clicks for the person. Zero only points.

/// One thing on the page to point at.
struct GuideTarget: Sendable {
    /// Words on the element or its accessible label, normalized and compared case-insensitively.
    var text: [String] = []
    /// How `text` must match the element's name.
    var match: Match = .exact
    /// Accessibility roles the element may have, such as AXButton or AXLink. Empty means any.
    var roles: [String] = []
    /// A DOM id, used when a page has a stable one.
    var domID: String?
    /// Extra room around the element inside the highlight, in points.
    var pad: CGFloat = 6

    enum Match: Sendable { case exact, prefix, contains }
}

/// A fact about the page, checked several times a second.
indirect enum GuideCondition: Sendable {
    case urlContains([String])
    /// Any element on the page shows one of these words (same matching as targets, `contains`).
    case textPresent([String])
    case targetPresent
    /// Some other element is on the page, for steps that depend on what is already there.
    case present(GuideTarget)
    /// The step's target is a checkbox and it is checked.
    case targetChecked
    case keyFound
    case not(GuideCondition)
    case all([GuideCondition])
    case any([GuideCondition])
}

struct GuideStep: Sendable, Identifiable {
    var id: String
    /// Callout heading, title case.
    var title: String
    /// Callout text: where to click and why, in plain sentences.
    var body: String
    /// Named in the "can't find it" message, as the person would see it on the page.
    var lookFor: String
    var targets: [GuideTarget]
    /// The step only applies on these pages (URL substrings). Empty means any page of the script.
    var pages: [String] = []
    /// Moves on by itself when this becomes true. Next always works too.
    var doneWhen: GuideCondition
    /// Passed over when this is true, for steps only some people see (sign-in, the terms, a first key).
    var skipWhen: GuideCondition?
    /// Where the callout goes relative to the highlight. `.auto` puts it beside targets in a left sidebar and
    /// under everything else.
    var placement: Placement = .auto

    enum Placement: Sendable { case auto, below, above, beside }
}

struct GuideScript: Sendable {
    var id: String
    var startURL: URL
    /// Pages where Zero may look for a key on the page or the clipboard. Never the sign-in pages.
    var keyHosts: [String]
    var steps: [GuideStep]
    /// The whole task in one card, for when Zero can't see the page (no Accessibility permission).
    var cardTitle: String
    var cardBody: String
}

extension GuideScript {
    /// Getting a free Gemini key from Google AI Studio, as of October 2026.
    ///
    /// New Google accounts get a default project and key once they accept AI Studio's terms, so most people
    /// only sign in, accept, open the keys page and copy. People who already use Google Cloud get no default
    /// key and press Create API key. Signed-in labels come from Google's docs (updated 2026-10-06) and
    /// published walk-throughs; each target lists the likely wordings. Confirm them with one signed-in run
    /// (see `docs/specs/2026-10-08-guided-voice-setup-design.md`, "Calibrating the Script").
    ///
    /// `studio` is the host to treat as AI Studio, so the same script runs against the local practice page.
    static func geminiKey(studio: String = "aistudio.google.com", startURL: URL? = nil) -> GuideScript {
        let signIn = ["accounts.google.com"]
        let studioPages = [studio]
        let onKeysPage = GuideCondition.urlContains(["\(studio)/apikey", "\(studio)/api-keys", "\(studio)/app/apikey"])
        let copyTarget = GuideTarget(text: ["Copy API key", "Copy key", "Copy"], match: .exact, roles: ["AXButton"])
        let createTarget = GuideTarget(text: ["Create API key", "Create an API key", "Create key"], match: .exact,
                                       roles: ["AXButton", "AXLink"])
        return GuideScript(
            id: "gemini-key",
            startURL: startURL ?? URL(string: "https://\(studio)/apikey")!,
            keyHosts: [studio],
            steps: [
                GuideStep(
                    id: "sign-in",
                    title: "Sign In to Google",
                    body: "Sign in with the Google account you want Zero's voice to use. A personal account works best. Zero can't see what you type here.",
                    lookFor: "Email or phone",
                    targets: [GuideTarget(text: ["Email or phone", "Email", "Choose an account"], match: .prefix,
                                          roles: ["AXTextField", "AXHeading", "AXStaticText"], domID: "identifierId", pad: 8)],
                    pages: signIn,
                    doneWhen: .urlContains(studioPages),
                    skipWhen: .urlContains(studioPages),
                    // Above the field, so Google's own Next button stays in view.
                    placement: .above),
                GuideStep(
                    id: "terms",
                    title: "Accept Google's Terms",
                    body: "Check this box to accept Google's terms. Google asks everyone to do this once before making a key. The box about emails from Google is up to you.",
                    lookFor: "the box that mentions the Terms of Service",
                    targets: [GuideTarget(text: ["Terms of Service", "Gemini API Additional Terms", "I acknowledge", "I consent"],
                                          match: .contains, roles: ["AXCheckBox"], pad: 8)],
                    pages: studioPages,
                    doneWhen: .targetChecked,
                    skipWhen: .all([.not(.targetPresent), .textPresent(["API key", "API Keys", "Dashboard"])]),
                    placement: .above),
                GuideStep(
                    id: "terms-continue",
                    title: "Continue",
                    body: "Click Continue. This saves your answer and opens AI Studio. You only do this once.",
                    lookFor: "Continue",
                    targets: [GuideTarget(text: ["Continue", "Accept", "I agree", "Agree", "Got it"], match: .exact,
                                          roles: ["AXButton"])],
                    pages: studioPages,
                    doneWhen: .not(.targetPresent),
                    skipWhen: .not(.textPresent(["Terms of Service", "Additional Terms"]))),
                GuideStep(
                    id: "keys-page",
                    title: "Open Your Keys",
                    body: "Click Get API key. It opens the page that lists your keys. If you are new to AI Studio, Google has already made one for you.",
                    lookFor: "Get API key",
                    targets: [GuideTarget(text: ["Get API key", "API keys", "API Keys"], match: .exact,
                                          roles: ["AXLink", "AXButton", "AXTab", "AXMenuItem"])],
                    pages: studioPages,
                    doneWhen: onKeysPage,
                    skipWhen: onKeysPage),
                GuideStep(
                    id: "create",
                    title: "Make a Key",
                    body: "Click Create API key. You don't have a key yet, so this makes one. It doesn't change anything else on your account.",
                    lookFor: "Create API key",
                    targets: [createTarget],
                    pages: studioPages,
                    doneWhen: .any([.textPresent(["Name your key", "Create a new key", "Choose a project", "Choose an imported project"]), .keyFound]),
                    // A key is already listed, so there is nothing to make.
                    skipWhen: .any([.present(copyTarget), .textPresent(["Default Gemini API Key"])])),
                GuideStep(
                    id: "create-confirm",
                    title: "Name It and Create",
                    body: "Click Create key. You can type Zero as the name first, so you know what the key is for later. If Google asks for a project, keep the one it suggests or choose Create project.",
                    lookFor: "Create key",
                    targets: [GuideTarget(text: ["Create key", "Create API key", "Create"], match: .exact, roles: ["AXButton"])],
                    pages: studioPages,
                    doneWhen: .any([.keyFound, .not(.textPresent(["Name your key", "Create a new key", "Choose a project", "Choose an imported project"]))]),
                    skipWhen: .not(.textPresent(["Name your key", "Create a new key", "Choose a project", "Choose an imported project"]))),
                GuideStep(
                    id: "copy",
                    title: "Copy Your Key",
                    body: "Click the Copy button next to your key. Zero picks the key up from your clipboard, checks it with Google, and keeps it on this Mac. You won't need to paste it anywhere.",
                    lookFor: "the Copy button",
                    targets: [copyTarget],
                    pages: studioPages,
                    doneWhen: .keyFound),
            ],
            cardTitle: "Get Your Free Key",
            cardBody: """
                1. If Google asks, sign in and accept its terms.
                2. Click Get API key on the left.
                3. If no key is listed, click Create API key, then Create key.
                4. Click the Copy button next to your key. Zero picks it up from your clipboard.
                """)
    }
}

/// Recognizes a Gemini key in text. Since May 28, 2026 AI Studio makes auth keys, which start with "AQ." and
/// are 53 characters; older standard keys start with "AIza" and are 39. The key itself is never logged.
enum GeminiKeyPattern {
    private static let regex = try! NSRegularExpression(
        pattern: #"(?<![A-Za-z0-9_\-.])(AQ\.[A-Za-z0-9_\-]{40,80}|AIza[A-Za-z0-9_\-]{35})(?![A-Za-z0-9_\-])"#)

    /// The first key-shaped run in `text`, or nil.
    static func find(in text: String) -> String? {
        let ns = text as NSString
        guard let m = regex.firstMatch(in: text, range: NSRange(location: 0, length: ns.length)) else { return nil }
        return ns.substring(with: m.range(at: 1))
    }

    /// "ending in 3fQx", for showing which key was saved without showing the key.
    static func ending(_ key: String) -> String { "ending in " + String(key.suffix(4)) }
}

/// Text comparison shared by the bridge and tests: lowercased, straight quotes, single spaces.
enum GuideText {
    static func normalize(_ s: String) -> String {
        var t = s.lowercased()
        for (a, b) in [("\u{2019}", "'"), ("\u{2018}", "'"), ("\u{201C}", "\""), ("\u{201D}", "\""), ("\u{00A0}", " ")] {
            t = t.replacingOccurrences(of: a, with: b)
        }
        return t.split(whereSeparator: { $0 == " " || $0 == "\n" || $0 == "\t" }).joined(separator: " ")
    }

    static func matches(_ name: String, _ wanted: String, _ mode: GuideTarget.Match) -> Bool {
        let n = normalize(name), w = normalize(wanted)
        guard !n.isEmpty, !w.isEmpty else { return false }
        switch mode {
        case .exact: return n == w
        case .prefix: return n.hasPrefix(w)
        case .contains: return n.contains(w)
        }
    }
}

/// What the callout is saying right now.
struct CalloutContent: Equatable {
    enum Status: Equatable {
        /// The target is highlighted.
        case pointing
        /// Still looking; shown for the first few seconds on a new page.
        case searching
        /// Looked for a while and it isn't there.
        case notFound
        /// The browser is on some other page or tab.
        case offPage
        /// I Copied It was pressed, but the clipboard holds no key.
        case noKey
    }

    var title: String
    var body: String
    var lookFor: String
    var status: Status = .pointing
    var canGoBack = true
    var nextTitle = "Next"

    var heading: String {
        switch status {
        case .pointing, .searching: title
        case .notFound: "Zero Can't Find This Step"
        case .offPage: "Back to Google's Key Page"
        case .noKey: "Zero Doesn't See a Key Yet"
        }
    }

    var message: String {
        switch status {
        case .pointing: body
        case .searching: "Looking for \u{201C}\(lookFor)\u{201D} on this page."
        case .notFound:
            "Zero can't find \u{201C}\(lookFor)\u{201D} on this page. Google may have changed it. If you can see it, click it and Zero will catch up. If not, choose I'm Stuck."
        case .offPage:
            "Zero is waiting for Google AI Studio. Switch back to its tab, or choose I'm Stuck to open it again."
        case .noKey:
            "Your clipboard doesn't hold a key. Click the Copy button next to your key, then choose I Copied It again. If that doesn't work, choose I'm Stuck and paste it instead."
        }
    }
}

/// The ways out offered under I'm Stuck.
enum StuckChoice: CaseIterable, Identifiable {
    case findAgain, skipStep, reopen, paste, stop
    var id: Self { self }
    var title: String {
        switch self {
        case .findAgain: "Find It Again"
        case .skipStep: "Skip This Step"
        case .reopen: "Open the Key Page Again"
        case .paste: "Paste My Key Instead"
        case .stop: "Stop Setup"
        }
    }
}

/// Where the card's pointer sits: on its top or bottom edge at an x offset, or on its left edge at a y offset.
enum CalloutArrow: Equatable { case none, up(CGFloat), down(CGFloat), left(CGFloat) }
