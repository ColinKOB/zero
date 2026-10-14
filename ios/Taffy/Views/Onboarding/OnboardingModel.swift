import AVFoundation
import Foundation
import Observation

@MainActor
@Observable
final class OnboardingModel {
    enum Computer: String, Codable { case alwaysOnMac, everydayMac, linux }

    enum Step: String, CaseIterable, Codable {
        case meet, name, talk, account, mac, guide, noMac, code, plan, consent, why, mail, ask, google, voice, look, found
    }

    static let questions = ["name", "work", "home", "age", "family", "sleep", "email"]
    static let hubAbout: Set<String> = ["work", "home", "age", "family"]

    struct Draft: Codable {
        var step: Step = .meet
        var look: AgentLook = .zero
        var agentName = ""
        var personName = ""
        var about: [String: String] = [:]
        var sources: [String: Bool] = Dictionary(uniqueKeysWithValues: LookSource.all.map { ($0.id, true) })
        var voice = AgentVoice.all[0].id
        var noComputer = false
        var computer: Computer? = nil
        var saved = false
        var consent: String? = nil
        var quiet: HomeSettings.QuietHours? = nil
        var locale: HomeSettings.Locale? = nil
        var questionsOn: Bool? = nil
        var mailAfterPicks: Bool? = nil
        var username: String? = nil
        var signUpJob: String? = nil
        var signUpReady: Bool? = nil
    }

    var draft: Draft { didSet { persist() } }
    let rehearsal: Bool
    var templates: [ZipTemplate] = []
    var picked: Set<String> = []
    var sentPicks: Set<String> = []
    var needsAnswers: [String: [String: String]] = [:]

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

    var agentName: String {
        let n = draft.agentName.trimmingCharacters(in: .whitespaces)
        return n.isEmpty ? "Zero" : n
    }

    var personName: String { draft.personName.trimmingCharacters(in: .whitespaces) }

    var emailHint: String? {
        let e = (draft.about["email"] ?? "").trimmingCharacters(in: .whitespaces)
        return e.contains("@") && !e.contains(" ") ? e : nil
    }

    static func readsMail(_ t: ZipTemplate) -> Bool {
        !(t.services ?? []).filter { ["gmail", "calendar", "email", "mail"].contains($0) }.isEmpty
    }

    private func persist() {
        guard !rehearsal else { return }
        if let data = try? JSONEncoder().encode(draft) { UserDefaults.standard.set(data, forKey: Self.key) }
    }

    static func clearSaved() { UserDefaults.standard.removeObject(forKey: key) }

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

struct AgentVoice: Identifiable {
    let id: String
    let name: String
    let detail: String
    let female: Bool
    let pitch: Float
    let rate: Float
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
