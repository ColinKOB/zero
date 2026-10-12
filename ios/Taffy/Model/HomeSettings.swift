import Foundation
import Observation
import SwiftUI

/// The home's own settings as the hub reports them (GET /api/home-settings, built from the home's home.json): who the
/// person is, their quiet hours and units, whose plan the Zips think with, what kind of computer the home runs on, and
/// the mailboxes, calendars and Google accounts the Zips can read without a Claude or ChatGPT connector
/// (docs/plans/2026-10-09-zero-for-anyone-plan.md, sections 4 and 6). Every field is optional: an older hub has no
/// such endpoint, and then the app behaves exactly as it did before.
struct HomeSettings: Codable, Equatable {
    struct QuietHours: Codable, Equatable {
        var start: Int
        var end: Int
    }

    struct Person: Codable, Equatable {
        var name: String? = nil
        var first_name: String? = nil
        var timezone: String? = nil
        var quiet_hours: QuietHours? = nil
    }

    struct Locale: Codable, Equatable {
        /// us, metric or uk
        var units: String? = nil
        /// american or british
        var spelling: String? = nil
        /// 12h or 24h
        var clock: String? = nil
    }

    struct Proactivity: Codable, Equatable {
        var enabled: Bool? = nil
    }

    struct Host: Codable, Equatable {
        /// "server" (a home computer such as a Linux box the person never sits at) or "mac" (Zero for Mac).
        var kind: String? = nil
        var name: String? = nil
    }

    /// A mailbox the Zips read with an app password (iCloud, another IMAP account) or through Google sign-in.
    struct Mailbox: Codable, Equatable, Identifiable {
        let id: String
        let address: String
        var label: String? = nil
        /// icloud, gmail, google or other
        var provider: String? = nil
        /// connected, needs_sign_in or trouble. Older hubs leave it out (connected).
        var state: String? = nil
        var detail: String? = nil
        var `default`: Bool? = nil
    }

    struct Calendar: Codable, Equatable, Identifiable {
        let id: String
        /// caldav or ical
        var kind: String? = nil
        var label: String? = nil
        var username: String? = nil
        var provider: String? = nil
        var state: String? = nil
        var detail: String? = nil
    }

    struct Mail: Codable, Equatable {
        var accounts: [Mailbox]? = nil
    }

    /// One Google account connected with Sign In with Google: Gmail and Google Calendar for the Zips.
    struct GoogleAccount: Codable, Equatable, Identifiable {
        let id: String
        let address: String
        /// connected, or needs_sign_in (also read as "expired" or "reauth") when Google's sign-in ran out. While Zero's
        /// Google app is in Testing, that happens about once a week.
        var state: String? = nil
        var detail: String? = nil

        var needsSignIn: Bool { ["needs_sign_in", "expired", "reauth", "invalid_grant"].contains(state ?? "") }
    }

    struct Google: Codable, Equatable {
        /// The iOS OAuth client id the app signs in with. Nil when the home has no Google sign-in set up.
        var client_id: String? = nil
        var accounts: [GoogleAccount]? = nil
    }

    var person: Person? = nil
    var locale: Locale? = nil
    var proactivity: Proactivity? = nil
    /// own or shared: whether the Claude and ChatGPT plans are the person's own or someone else's shared with them.
    var plan: String? = nil
    /// The first name of the person who shares the plan, for the consent screen. Nil when the plan is the person's own.
    var plan_owner: String? = nil
    /// claude, chatgpt or none: where connectors such as Gmail come from.
    var connectors: String? = nil
    var host: Host? = nil
    var mail: Mail? = nil
    var calendars: [Calendar]? = nil
    var google: Google? = nil

    var shared: Bool { plan == "shared" }
    var serverHome: Bool { host?.kind == "server" }
    /// No Claude or ChatGPT connectors: mail and calendars are added in Zero itself.
    var nativeAccounts: Bool { connectors == "none" }
    var googleAccounts: [GoogleAccount] { google?.accounts ?? [] }
    /// App-password mailboxes; Google's are listed with the Google account.
    var mailboxes: [Mailbox] { (mail?.accounts ?? []).filter { $0.provider != "google" } }
    var otherCalendars: [Calendar] { (calendars ?? []).filter { $0.provider != "google" } }
    var hasMail: Bool { !googleAccounts.isEmpty || !mailboxes.isEmpty }
    var hasCalendar: Bool { !googleAccounts.isEmpty || !otherCalendars.isEmpty }
}

/// What the app changes with PUT /api/home-settings. Only the parts that are set are sent.
struct HomeSettingsUpdate: Encodable {
    var person: HomeSettings.Person? = nil
    var locale: HomeSettings.Locale? = nil
    var proactivity: HomeSettings.Proactivity? = nil
}

/// The answer to adding or checking a mailbox, a calendar or a Google account.
struct AccountCheck: Decodable {
    let ok: Bool
    var checked: Bool? = nil
    /// One plain sentence: what worked, or what to fix.
    var detail: String? = nil
    var id: String? = nil
    var address: String? = nil
}

/// The last home settings and the calls that change them, shared by the onboarding and Settings.
@MainActor
@Observable
final class HomeModel {
    static let shared = HomeModel()

    var settings: HomeSettings?
    /// The hub has no home settings endpoint (an older hub): every screen keeps its earlier behavior.
    var unsupported = false

    var isShared: Bool { settings?.shared ?? false }
    var serverHome: Bool { settings?.serverHome ?? false }
    var nativeAccounts: Bool { settings?.nativeAccounts ?? false }
    /// "Jordan", or nil when nobody shares a plan or the hub didn't say who.
    var planOwner: String? {
        guard isShared, let o = settings?.plan_owner?.trimmingCharacters(in: .whitespaces), !o.isEmpty else { return nil }
        return o
    }

    var quietHours: HomeSettings.QuietHours? { settings?.person?.quiet_hours }

    /// The Google client id to sign in with: the home's, else the app's own (GIDClientID in Info.plist, the Zero iOS
    /// client in CONFIGURE.md). Nil only when neither exists.
    var googleClientID: String? {
        if let id = settings?.google?.client_id?.trimmingCharacters(in: .whitespaces), !id.isEmpty { return id }
        #if DEBUG
        if CommandLine.arguments.contains("-noGoogleClient") { return nil }   // screenshots of the not-set-up state
        #endif
        let bundled = (Bundle.main.object(forInfoDictionaryKey: "GIDClientID") as? String ?? "").trimmingCharacters(in: .whitespaces)
        return bundled.isEmpty ? nil : bundled
    }
    var timeZone: TimeZone? { settings?.person?.timezone.flatMap(TimeZone.init(identifier:)) }

    func load(_ state: AppState) async {
        #if DEBUG
        if state.fixtureMode {
            if settings == nil { settings = .fixture(Self.fixtureVariant) }
            return
        }
        #endif
        guard state.paired, !state.demoMode else { return }
        do {
            settings = try await state.client.homeSettings()
            unsupported = false
        } catch HubClient.HubError.http(let code, _) where code == 404 || code == 405 {
            unsupported = true
        } catch {
            // Kept as it was; screens that need it say so.
        }
    }

    /// Saves the change. Returns nil on success, or a plain sentence.
    func save(_ update: HomeSettingsUpdate, _ state: AppState) async -> String? {
        #if DEBUG
        if state.fixtureMode {
            var s = settings ?? HomeSettings()
            if let p = update.person {
                var person = s.person ?? .init()
                if let q = p.quiet_hours { person.quiet_hours = q }
                if let t = p.timezone { person.timezone = t }
                s.person = person
            }
            if let l = update.locale { s.locale = l }
            if let p = update.proactivity { s.proactivity = p }
            settings = s
            return nil
        }
        #endif
        do {
            settings = try await state.client.saveHomeSettings(update)
            return nil
        } catch HubClient.HubError.http(let code, _) where code == 404 || code == 405 {
            unsupported = true
            return nil   // an older hub: quiet hours and units stay as they were
        } catch {
            return HubClient.describe(error)
        }
    }

    func clear() {
        settings = nil
        unsupported = false
    }

    #if DEBUG
    /// -plan shared | own | shared-google | shared-expired | shared-nogoogle picks the fixture home.
    static var fixtureVariant: String {
        let a = CommandLine.arguments
        if let i = a.firstIndex(of: "-plan"), i + 1 < a.count { return a[i + 1] }
        return "own"
    }
    #endif
}

#if DEBUG
extension HomeSettings {
    /// A fixture home. "shared": a server home on someone else's plan with no connectors and nothing added yet.
    /// "shared-google": the same with a Gmail account connected; "shared-expired": Google asked to sign in again.
    /// "own": a Mac home on the person's own plan. Add `-noGoogleClient` for a build with no Google client id.
    static func fixture(_ variant: String) -> HomeSettings {
        let shared = variant.hasPrefix("shared")
        var s = HomeSettings(
            person: .init(name: "Sam Rivera", first_name: "Sam", timezone: "America/Chicago",
                          quiet_hours: .init(start: 22, end: 6)),
            locale: .init(units: "us", spelling: "american", clock: "12h"),
            proactivity: .init(enabled: true),
            plan: shared ? "shared" : "own",
            plan_owner: shared ? "Jordan" : nil,
            connectors: shared ? "none" : "claude",
            host: .init(kind: shared ? "server" : "mac", name: shared ? "home server" : "Mac"),
            mail: .init(accounts: []), calendars: [],
            google: .init(client_id: nil, accounts: []))
        switch variant {
        case "shared-google":
            s.google?.accounts = [.init(id: "g1", address: "sam.rivera@gmail.com", state: "connected")]
        case "shared-expired":
            s.google?.accounts = [.init(id: "g1", address: "sam.rivera@gmail.com", state: "expired")]
            s.mail?.accounts = [.init(id: "icloud", address: "sam@icloud.com", label: "iCloud", provider: "icloud", state: "connected")]
            s.calendars = [.init(id: "icloud-cal", kind: "caldav", label: "iCloud Calendar", username: "sam@icloud.com",
                                 provider: "icloud", state: "connected")]
        default: break
        }
        return s
    }
}
#endif

// MARK: Hub calls

extension HubClient {
    func homeSettings() async throws -> HomeSettings {
        try await send(try request("/api/home-settings"), as: HomeSettings.self)
    }

    func saveHomeSettings(_ update: HomeSettingsUpdate) async throws -> HomeSettings {
        try await send(try request("/api/home-settings", method: "PUT", body: update), as: HomeSettings.self)
    }

    /// A mailbox with an app password. `imap` and `smtp` are only for "other"; iCloud and Gmail use their known servers.
    struct MailboxBody: Encodable {
        struct Server: Encodable { let host: String; let port: Int }
        let provider: String
        let address: String
        let password: String
        var label: String? = nil
        var imap: Server? = nil
        var smtp: Server? = nil
    }

    func addMailbox(_ body: MailboxBody) async throws -> AccountCheck {
        var r = try request("/api/accounts/mailboxes", method: "POST", body: body)
        r.timeoutInterval = 60   // the hub signs in to the mailbox to check it
        return try await send(r, as: AccountCheck.self)
    }

    func removeMailbox(_ id: String) async throws {
        _ = try await send(try request("/api/accounts/mailboxes/\(Self.path(id))", method: "DELETE"), as: AccountCheck.self)
    }

    /// A calendar: CalDAV with a username and an app password (iCloud or another server), or a read-only iCal address.
    struct CalendarBody: Encodable {
        let kind: String
        let provider: String
        let url: String
        var username: String? = nil
        var password: String? = nil
        var label: String? = nil
    }

    func addCalendar(_ body: CalendarBody) async throws -> AccountCheck {
        var r = try request("/api/accounts/calendars", method: "POST", body: body)
        r.timeoutInterval = 60
        return try await send(r, as: AccountCheck.self)
    }

    func removeCalendar(_ id: String) async throws {
        _ = try await send(try request("/api/accounts/calendars/\(Self.path(id))", method: "DELETE"), as: AccountCheck.self)
    }

    /// Sign In with Google: the hub trades the code for tokens with the same client id, redirect address and verifier,
    /// keeps them in the home, and answers with the account's address.
    struct GoogleBody: Encodable {
        let code: String
        let code_verifier: String
        let redirect_uri: String
        let client_id: String
        /// nil: Gmail and Google Calendar. ["drive"]: Connect Apps' Google Drive, with no mailbox or calendar added.
        var use: [String]? = nil
    }

    func connectGoogle(_ body: GoogleBody) async throws -> AccountCheck {
        var r = try request("/api/accounts/google", method: "POST", body: body)
        r.timeoutInterval = 60
        return try await send(r, as: AccountCheck.self)
    }

    func disconnectGoogle(_ id: String) async throws {
        _ = try await send(try request("/api/accounts/google/\(Self.path(id))", method: "DELETE"), as: AccountCheck.self)
    }

    private static func path(_ id: String) -> String {
        id.addingPercentEncoding(withAllowedCharacters: .urlPathAllowed.subtracting(CharacterSet(charactersIn: "/"))) ?? id
    }
}

// MARK: Quiet hours, units and the clock

enum HomeWording {
    /// "11 PM", "7 AM", "12 AM" in the person's clock.
    static func hour(_ h: Int) -> String {
        var c = DateComponents()
        c.hour = ((h % 24) + 24) % 24
        c.minute = 0
        let date = Foundation.Calendar(identifier: .gregorian).date(from: c) ?? Date()
        let f = DateFormatter()
        f.locale = .current
        f.setLocalizedDateFormatFromTemplate("j")
        return f.string(from: date)
    }

    static func quiet(_ q: HomeSettings.QuietHours) -> String { "\(hour(q.start)) to \(hour(q.end))" }

    static func units(_ id: String?) -> String {
        switch id {
        case "metric": return "Metric units"
        case "uk": return "UK units"
        default: return "US units"
        }
    }

    static func spelling(_ id: String?) -> String { id == "british" ? "British spelling" : "American spelling" }

    /// What this phone's settings suggest, for a home that has none yet.
    static var phoneLocale: HomeSettings.Locale {
        let l = Foundation.Locale.current
        let units: String = switch l.measurementSystem {
        case .us: "us"
        case .uk: "uk"
        default: "metric"
        }
        let region = l.region?.identifier ?? "US"
        let american = ["US", "PR", "GU", "VI", "AS", "MP", "UM", "PH", "LR"].contains(region)
        let format = DateFormatter.dateFormat(fromTemplate: "j", options: 0, locale: l) ?? "h a"
        return .init(units: units, spelling: american ? "american" : "british", clock: format.contains("a") ? "12h" : "24h")
    }
}

/// Reads "11pm", "around 10:30", "midnight to 6", "11 to 7" or "I go to bed at 10 and get up at 6" as quiet hours.
/// The first time is when sleep starts, the second when it ends; with only one, sleep lasts eight hours.
enum SleepAnswer {
    static func quietHours(from text: String) -> HomeSettings.QuietHours? {
        let s = text.lowercased()
            .replacingOccurrences(of: "a.m.", with: "am").replacingOccurrences(of: "p.m.", with: "pm")
            .replacingOccurrences(of: "midnight", with: "12am").replacingOccurrences(of: "noon", with: "12pm")
        guard let re = try? NSRegularExpression(pattern: #"(\d{1,2})(?::(\d{2}))?\s*(am|pm)?"#) else { return nil }
        let ns = s as NSString
        var times: [(hour: Int, meridiem: String?)] = []
        for m in re.matches(in: s, range: NSRange(location: 0, length: ns.length)) {
            guard let h = Int(ns.substring(with: m.range(at: 1))), h <= 24 else { continue }
            let mer = m.range(at: 3).location != NSNotFound ? ns.substring(with: m.range(at: 3)) : nil
            times.append((h, mer))
            if times.count == 2 { break }
        }
        guard let first = times.first else { return nil }
        func clock(_ t: (hour: Int, meridiem: String?), night: Bool) -> Int {
            let h = t.hour % 24
            switch t.meridiem {
            case "am": return h == 12 ? 0 : h
            case "pm": return h == 12 ? 12 : (h + 12) % 24
            default:
                if h > 12 || h == 0 { return h }
                // Bedtime without am or pm: 7 to 11 is evening, 12 is midnight, 1 to 5 is after midnight.
                if night { return h == 12 ? 0 : (h >= 6 ? h + 12 : h) }
                return h == 12 ? 12 : h   // waking up: morning
            }
        }
        let start = clock(first, night: true)
        let end = times.count > 1 ? clock(times[1], night: false) : (start + 8) % 24
        guard start != end else { return nil }
        return .init(start: start, end: end)
    }
}

// MARK: Pairing links and codes

/// `zero://pair?server=https://host:port&code=ABCD2345`, from a QR code or a link in a message. A Mac home's link
/// adds `fp`, its certificate's SHA-256 fingerprint, which is pinned for that address as soon as the link is read
/// (Model/HubTrust.swift).
struct PairLink: Equatable {
    let server: String
    let code: String
    var fingerprint: String? = nil

    static func parse(_ text: String) -> PairLink? {
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        // A link pasted with words around it.
        let candidate: String = {
            guard let r = trimmed.range(of: "zero://", options: .caseInsensitive) else { return trimmed }
            return String(trimmed[r.lowerBound...].prefix { !$0.isWhitespace })
        }()
        guard let comps = URLComponents(string: candidate), comps.scheme?.lowercased() == "zero",
              (comps.host?.lowercased() == "pair" || comps.path.lowercased().hasPrefix("pair")) else { return nil }
        let items = comps.queryItems ?? []
        let server = items.first { $0.name == "server" }?.value?.trimmingCharacters(in: .whitespaces) ?? ""
        let code = (items.first { $0.name == "code" }?.value ?? "").uppercased().filter { $0.isLetter || $0.isNumber }
        guard !server.isEmpty || !code.isEmpty else { return nil }
        let fp = (items.first { $0.name == "fp" }?.value).flatMap(HubTrust.normalized)
        // Any home kind whose link carries fp (a Mac home, or a home server made by the server installer) is pinned.
        // A link without fp leaves the address to the system's normal checks (alien behind Tailscale).
        if let fp, let url = URL(string: HubClient.normalizedAddress(server)), url.scheme?.lowercased() == "https" {
            HubTrust.setPin(fp, for: url.absoluteString)
        }
        return PairLink(server: server, code: code, fingerprint: fp)
    }

    /// The link for a code made on this device, so the new device gets the address with it.
    var url: URL? {
        var c = URLComponents()
        c.scheme = "zero"
        c.host = "pair"
        c.queryItems = [URLQueryItem(name: "server", value: server), URLQueryItem(name: "code", value: code)]
        if let fp = fingerprint ?? URL(string: server).flatMap(HubTrust.pin(for:)) {
            c.queryItems?.append(URLQueryItem(name: "fp", value: fp))
        }
        return c.url
    }
}

extension AppState {
    /// A zero:// link opened the app (a QR code read by the Camera app, or a link in a message).
    func open(_ url: URL) {
        guard let link = PairLink.parse(url.absoluteString) else { return }
        if paired && !onboarding && !rehearsingOnboarding {
            confirmation = Confirmation(title: "Already Connected",
                                        text: "This \(Theme.device) is already signed in. To use that code, log out in Settings first.")
            return
        }
        pendingPair = link
    }
}
