import Foundation
import Observation
import SwiftUI

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
        var units: String? = nil
        var spelling: String? = nil
        var clock: String? = nil
    }

    struct Proactivity: Codable, Equatable {
        var enabled: Bool? = nil
    }

    struct Host: Codable, Equatable {
        var kind: String? = nil
        var name: String? = nil
    }

    struct Mailbox: Codable, Equatable, Identifiable {
        let id: String
        let address: String
        var label: String? = nil
        var provider: String? = nil
        var state: String? = nil
        var detail: String? = nil
        var `default`: Bool? = nil
    }

    struct Calendar: Codable, Equatable, Identifiable {
        let id: String
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
        var client_id: String? = nil
        var accounts: [GoogleAccount]? = nil
    }

    var person: Person? = nil
    var locale: Locale? = nil
    var proactivity: Proactivity? = nil
    var plan: String? = nil
    var plan_owner: String? = nil
    var connectors: String? = nil
    var host: Host? = nil
    var mail: Mail? = nil
    var calendars: [Calendar]? = nil
    var google: Google? = nil

    var shared: Bool { plan == "shared" }
    var serverHome: Bool { host?.kind == "server" }
    var nativeAccounts: Bool { connectors == "none" }
    var googleAccounts: [GoogleAccount] { google?.accounts ?? [] }
    var mailboxes: [Mailbox] { (mail?.accounts ?? []).filter { $0.provider != "google" } }
    var otherCalendars: [Calendar] { (calendars ?? []).filter { $0.provider != "google" } }
    var hasMail: Bool { !googleAccounts.isEmpty || !mailboxes.isEmpty }
    var hasCalendar: Bool { !googleAccounts.isEmpty || !otherCalendars.isEmpty }
}

struct HomeSettingsUpdate: Encodable {
    var person: HomeSettings.Person? = nil
    var locale: HomeSettings.Locale? = nil
    var proactivity: HomeSettings.Proactivity? = nil
}

struct AccountCheck: Decodable {
    let ok: Bool
    var checked: Bool? = nil
    var detail: String? = nil
    var id: String? = nil
    var address: String? = nil
}

@MainActor
@Observable
final class HomeModel {
    static let shared = HomeModel()

    var settings: HomeSettings?
    var unsupported = false

    var isShared: Bool { settings?.shared ?? false }
    var serverHome: Bool { settings?.serverHome ?? false }
    var nativeAccounts: Bool { settings?.nativeAccounts ?? false }
    var planOwner: String? {
        guard isShared, let o = settings?.plan_owner?.trimmingCharacters(in: .whitespaces), !o.isEmpty else { return nil }
        return o
    }

    var quietHours: HomeSettings.QuietHours? { settings?.person?.quiet_hours }

    var googleClientID: String? {
        if let id = settings?.google?.client_id?.trimmingCharacters(in: .whitespaces), !id.isEmpty { return id }
        #if DEBUG
        if CommandLine.arguments.contains("-noGoogleClient") { return nil }
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
        }
    }

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
            return nil
        } catch {
            return HubClient.describe(error)
        }
    }

    func clear() {
        settings = nil
        unsupported = false
    }

    #if DEBUG
    static var fixtureVariant: String {
        let a = CommandLine.arguments
        if let i = a.firstIndex(of: "-plan"), i + 1 < a.count { return a[i + 1] }
        return "own"
    }
    #endif
}

#if DEBUG
extension HomeSettings {
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
        r.timeoutInterval = 60
        return try await send(r, as: AccountCheck.self)
    }

    func removeMailbox(_ id: String) async throws {
        _ = try await send(try request("/api/accounts/mailboxes/\(Self.path(id))", method: "DELETE"), as: AccountCheck.self)
    }

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

    struct GoogleBody: Encodable {
        let code: String
        let code_verifier: String
        let redirect_uri: String
        let client_id: String
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
                if night { return h == 12 ? 0 : (h >= 6 ? h + 12 : h) }
                return h == 12 ? 12 : h
            }
        }
        let start = clock(first, night: true)
        let end = times.count > 1 ? clock(times[1], night: false) : (start + 8) % 24
        guard start != end else { return nil }
        return .init(start: start, end: end)
    }
}

// MARK: Pairing links and codes

extension AppState {
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
