import AVFoundation
import Foundation
import Observation

/// Everything the onboarding collects, kept on the phone as it goes (so a closed app picks up where it was)
/// and sent to the hub once the phone is connected.
@MainActor
@Observable
final class OnboardingModel {
    /// The computer Zero will live on, picked on Where Should I Live?. Each has its own setup prompt.
    enum Computer: String, Codable { case alwaysOnMac, everydayMac, linux }

    enum Step: String, CaseIterable, Codable {
        case meet, name, talk, account, mac, guide, noMac, code, plan, consent, why, mail, ask, google, voice, look, found
    }

    /// The about-you questions, in the order the agent asks them. The first five keys match the hub's onboarding.py.
    /// "sleep" fills the quiet hours on How Your Zips Should Ask, and "email" is offered to Sign In with Google and the
    /// mailbox form; neither goes into the About notes.
    static let questions = ["name", "work", "home", "age", "family", "sleep", "email"]
    /// The answers the hub keeps in every Zip's About notes (onboarding.py ABOUT).
    static let hubAbout: Set<String> = ["work", "home", "age", "family"]

    struct Draft: Codable {
        var step: Step = .meet
        var look: AgentLook = .zero
        var agentName = ""
        var personName = ""
        var about: [String: String] = [:]
        var sources: [String: Bool] = Dictionary(uniqueKeysWithValues: LookSource.all.map { ($0.id, true) })
        var voice = AgentVoice.all[0].id
        /// Chose "I Don't Have a Computer": no code, permissions or look-around, and project ideas at the end.
        var noComputer = false
        /// The computer picked on Where Should I Live?, for the setup guide after it.
        var computer: Computer? = nil
        var saved = false
        /// What the person chose on the AI data screen: "agreed" or "declined". A rehearsal keeps its own answer here
        /// without changing the one the device really has.
        var consent: String? = nil
        /// How Your Zips Should Ask. Optional, so a draft saved by an earlier build still opens where it was.
        var quiet: HomeSettings.QuietHours? = nil
        var locale: HomeSettings.Locale? = nil
        var questionsOn: Bool? = nil
        /// The email step came back after the Zips were picked, because one of them reads mail or a calendar.
        var mailAfterPicks: Bool? = nil
        /// The account being made: its username, the sign-up job while the home is built, and whether it is ready.
        /// The password is never kept here.
        var username: String? = nil
        var signUpJob: String? = nil
        var signUpReady: Bool? = nil
    }

    var draft: Draft { didSet { persist() } }
    /// Opened from Settings on a device that is already set up: nothing is kept or sent.
    let rehearsal: Bool
    var templates: [ZipTemplate] = []
    var picked: Set<String> = []
    /// Picked templates already sent to the lead Zip, so going back from Connect Google doesn't ask twice.
    var sentPicks: Set<String> = []
    /// Answers to each picked template's own questions ("Which email accounts to watch"), by template id and question.
    var needsAnswers: [String: [String: String]] = [:]

    /// The picked templates whose Zips use Gmail, Calendar or Drive. Connect Google shows only when there are some.
    var pickedGoogle: [ZipTemplate] { templates.filter { picked.contains($0.id) && $0.usesGoogle } }
    var templatesError: String?

    private static let key = "onboardingDraft"

    init(start: Step? = nil, rehearsal: Bool = false) {
        self.rehearsal = rehearsal
        if rehearsal {
            draft = Draft(step: start ?? .meet)
        } else if let start {
            draft = Draft(step: start)
            #if DEBUG
            if Step.allCases.firstIndex(of: start)! > Step.allCases.firstIndex(of: .name)! { draft.agentName = "Zero" }
            if Step.allCases.firstIndex(of: start)! > Step.allCases.firstIndex(of: .talk)! { draft.personName = "Sam" }
            let args = CommandLine.arguments
            if let i = args.firstIndex(of: "-look"), i + 1 < args.count {
                let parts = args[i + 1].split(separator: ",").map(String.init)
                draft.look = AgentLook(body: parts[0], outfit: parts.count > 1 ? parts[1] : "plain")
            }
            if args.contains("-noComputer") || start == .noMac { draft.noComputer = true }
            #endif
        } else if let data = UserDefaults.standard.data(forKey: Self.key),
                  let saved = try? JSONDecoder().decode(Draft.self, from: data) {
            draft = saved
        } else {
            draft = Draft()
        }
    }

    /// The name the agent goes by: what the person typed, or Zero.
    var agentName: String {
        let n = draft.agentName.trimmingCharacters(in: .whitespaces)
        return n.isEmpty ? "Zero" : n
    }

    var personName: String { draft.personName.trimmingCharacters(in: .whitespaces) }

    /// The address the person said they use most, if it looks like one.
    var emailHint: String? {
        let e = (draft.about["email"] ?? "").trimmingCharacters(in: .whitespaces)
        return e.contains("@") && !e.contains(" ") ? e : nil
    }

    /// Templates whose Zips read email or a calendar.
    static func readsMail(_ t: ZipTemplate) -> Bool {
        !(t.services ?? []).filter { ["gmail", "calendar", "email", "mail"].contains($0) }.isEmpty
    }

    private func persist() {
        guard !rehearsal else { return }
        if let data = try? JSONEncoder().encode(draft) { UserDefaults.standard.set(data, forKey: Self.key) }
    }

    static func clearSaved() { UserDefaults.standard.removeObject(forKey: key) }

    /// Agent names follow the hub's rename rule: up to 24 letters, numbers and spaces.
    static func validName(_ name: String) -> Bool {
        let n = name.trimmingCharacters(in: .whitespaces)
        return n.isEmpty || n.range(of: #"^[A-Za-z0-9][A-Za-z0-9 .'\-]{0,23}$"#, options: .regularExpression) != nil
    }

    var answers: HubClient.OnboardingAnswers {
        HubClient.OnboardingAnswers(
            agent_name: agentName, person_name: personName,
            about: draft.about.filter { Self.hubAbout.contains($0.key) && !$0.value.trimmingCharacters(in: .whitespaces).isEmpty },
            sources: draft.sources, voice: draft.voice, no_computer: draft.noComputer,
            look: .init(body: draft.look.body, outfit: AgentBody.named(draft.look.body).hasOutfits ? draft.look.outfit : ""))
    }
}

/// A place the agent may look around on the Mac, each with its own switch. Ids match the hub's onboarding.py.
struct LookSource: Identifiable {
    let id: String
    let title: String
    let detail: String
    let symbol: String

    static let all: [LookSource] = [
        .init(id: "files", title: "Names of files and folders",
              detail: "I read the names only, to see what you work on.", symbol: "folder"),
        .init(id: "apps", title: "Your apps", detail: "Knowing your apps tells me which tools you use.",
              symbol: "square.grid.2x2"),
        .init(id: "contact", title: "Your contact card",
              detail: "Your own card tells me your name, birthday and the people you list.", symbol: "person.crop.square"),
        .init(id: "calendar", title: "Your calendar", detail: "Your events show me what your weeks look like.",
              symbol: "calendar"),
        .init(id: "email", title: "Recent email subjects and senders",
              detail: "I see who writes to you and about what, but not the messages.", symbol: "envelope"),
        .init(id: "picked_files", title: "A few files I pick by name",
              detail: "I open a handful of files whose names look useful, like a résumé.", symbol: "doc.text.magnifyingglass"),
    ]
}

/// A voice for the lead Zip. Play uses a recording in that voice (Resources/VoiceSamples/<id>.m4a); Apple's
/// speech, pitched to hint at it, is only a fallback for a missing file. Calls run on ChatGPT voices until
/// Gemini calls exist, so each voice names the closest ChatGPT voice, and picking one sets the call voice
/// (Oct 8 QA, M6: the person heard Kore here and Cove on the first call).
struct AgentVoice: Identifiable {
    let id: String
    let name: String
    let detail: String
    let female: Bool
    let pitch: Float
    let rate: Float
    /// The ChatGPT voice calls use for this pick (one of CallEngine.chatGPTVoices).
    let callVoice: String

    static let all: [AgentVoice] = [
        .init(id: "kore", name: "Kore", detail: "Firm and clear.", female: true, pitch: 1.0, rate: 0.5, callVoice: "sol"),
        .init(id: "puck", name: "Puck", detail: "Upbeat and quick.", female: false, pitch: 1.15, rate: 0.54, callVoice: "breeze"),
        .init(id: "charon", name: "Charon", detail: "Calm and low.", female: false, pitch: 0.8, rate: 0.46, callVoice: "cove"),
        .init(id: "aoede", name: "Aoede", detail: "Light and breezy.", female: true, pitch: 1.18, rate: 0.52, callVoice: "juniper"),
        .init(id: "zephyr", name: "Zephyr", detail: "Bright and warm.", female: true, pitch: 1.08, rate: 0.5, callVoice: "maple"),
    ]

    func utterance(_ text: String) -> AVSpeechUtterance {
        let u = AVSpeechUtterance(string: text)
        let english = AVSpeechSynthesisVoice.speechVoices().filter { $0.language.hasPrefix("en") }
        let gender: AVSpeechSynthesisVoiceGender = female ? .female : .male
        let matching = english.filter { $0.gender == gender }
        u.voice = matching.max { $0.quality.rawValue < $1.quality.rawValue }
            ?? AVSpeechSynthesisVoice(language: "en-US")
        u.pitchMultiplier = pitch
        u.rate = rate
        return u
    }
}
