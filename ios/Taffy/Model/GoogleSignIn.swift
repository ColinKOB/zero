import AuthenticationServices
import CryptoKit
import Foundation
import UIKit

/// Sign In with Google for Gmail and Google Calendar, the way a native app does it: Google's own sign-in page in a
/// system web sheet (ASWebAuthenticationSession), the authorization code flow with PKCE, and the home's iOS client id
/// (`google.client_id` in the home settings). The phone never sees a token. It hands the one-time code and its
/// verifier to the person's own home over the paired connection, and the home trades them for tokens and keeps them.
///
/// The redirect address is the reversed client id (com.googleusercontent.apps.<id>:/oauth2redirect), which is what
/// Google allows for an iOS client. The web sheet catches it itself, so it works even before the scheme is listed in
/// Info.plist; the Info.plist entry ($(GOOGLE_REVERSED_CLIENT_ID)) lets the system route it as well.
@MainActor
final class GoogleSignIn: NSObject, ASWebAuthenticationPresentationContextProviding {
    nonisolated static let scopes = [
        "openid",
        "email",
        "https://www.googleapis.com/auth/gmail.modify",
        "https://www.googleapis.com/auth/gmail.send",
        "https://www.googleapis.com/auth/calendar.events",
    ]
    /// Connect Apps' Google Drive: asked for on its own, with the scopes granted before kept (include_granted_scopes).
    nonisolated static let driveScopes = ["openid", "email", "https://www.googleapis.com/auth/drive.readonly"]
    static let authorizeURL = "https://accounts.google.com/o/oauth2/v2/auth"

    struct Grant: Equatable {
        let code: String
        let verifier: String
        let redirectURI: String
        let clientID: String
    }

    enum Failure: LocalizedError {
        /// The person closed Google's page. Nothing to say.
        case cancelled
        case denied
        case broken

        var errorDescription: String? {
            switch self {
            case .cancelled: return nil
            case .denied: return "You didn't give access, so nothing was connected."
            case .broken: return "Google's sign-in didn't finish. Try again in a moment."
            }
        }
    }

    /// "com.googleusercontent.apps.1234-abc" for "1234-abc.apps.googleusercontent.com".
    static func reversedClientID(_ clientID: String) -> String {
        clientID.split(separator: ".").reversed().joined(separator: ".")
    }

    static func redirectURI(_ clientID: String) -> String { reversedClientID(clientID) + ":/oauth2redirect" }

    /// A fresh PKCE verifier: 64 characters from the unreserved set (RFC 7636 allows 43 to 128).
    static func makeVerifier() -> String {
        let chars = Array("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~")
        var rng = SystemRandomNumberGenerator()
        return String((0..<64).map { _ in chars[Int(rng.next(upperBound: UInt32(chars.count)))] })
    }

    /// S256: base64url of the SHA-256 of the verifier, without padding.
    static func challenge(for verifier: String) -> String {
        Data(SHA256.hash(data: Data(verifier.utf8))).base64URL
    }

    static func authorizationURL(clientID: String, verifier: String, state: String, loginHint: String?,
                                 scopes: [String] = GoogleSignIn.scopes) -> URL? {
        var c = URLComponents(string: authorizeURL)
        var items = [
            URLQueryItem(name: "client_id", value: clientID),
            URLQueryItem(name: "redirect_uri", value: redirectURI(clientID)),
            URLQueryItem(name: "response_type", value: "code"),
            URLQueryItem(name: "scope", value: scopes.joined(separator: " ")),
            URLQueryItem(name: "code_challenge", value: challenge(for: verifier)),
            URLQueryItem(name: "code_challenge_method", value: "S256"),
            URLQueryItem(name: "state", value: state),
            URLQueryItem(name: "include_granted_scopes", value: "true"),
        ]
        if let hint = loginHint?.trimmingCharacters(in: .whitespaces), hint.contains("@") {
            items.append(URLQueryItem(name: "login_hint", value: hint))
        }
        c?.queryItems = items
        return c?.url
    }

    private var session: ASWebAuthenticationSession?

    /// Opens Google's page and waits for the code. Throws `Failure.cancelled` when the person closes it.
    func signIn(clientID: String, loginHint: String?, scopes: [String] = GoogleSignIn.scopes) async throws -> Grant {
        let verifier = Self.makeVerifier()
        let state = UUID().uuidString
        guard let url = Self.authorizationURL(clientID: clientID, verifier: verifier, state: state, loginHint: loginHint,
                                              scopes: scopes)
        else { throw Failure.broken }
        let scheme = Self.reversedClientID(clientID)
        let callback: URL = try await withCheckedThrowingContinuation { cont in
            let s = ASWebAuthenticationSession(url: url, callbackURLScheme: scheme) { url, error in
                if let url { cont.resume(returning: url); return }
                if let e = error as? ASWebAuthenticationSessionError, e.code == .canceledLogin {
                    cont.resume(throwing: Failure.cancelled)
                } else {
                    cont.resume(throwing: Failure.broken)
                }
            }
            s.presentationContextProvider = self
            s.prefersEphemeralWebBrowserSession = false
            session = s
            if !s.start() { cont.resume(throwing: Failure.broken) }
        }
        session = nil
        let items = URLComponents(url: callback, resolvingAgainstBaseURL: false)?.queryItems ?? []
        func value(_ name: String) -> String? { items.first { $0.name == name }?.value }
        if value("error") == "access_denied" { throw Failure.denied }
        guard value("state") == state, let code = value("code"), !code.isEmpty else { throw Failure.broken }
        return Grant(code: code, verifier: verifier, redirectURI: Self.redirectURI(clientID), clientID: clientID)
    }

    nonisolated func presentationAnchor(for session: ASWebAuthenticationSession) -> ASPresentationAnchor {
        MainActor.assumeIsolated {
            let scenes = UIApplication.shared.connectedScenes.compactMap { $0 as? UIWindowScene }
            return scenes.flatMap(\.windows).first(where: \.isKeyWindow) ?? scenes.first?.windows.first ?? ASPresentationAnchor()
        }
    }
}

private extension Data {
    var base64URL: String {
        base64EncodedString().replacingOccurrences(of: "+", with: "-").replacingOccurrences(of: "/", with: "_")
            .replacingOccurrences(of: "=", with: "")
    }
}

// MARK: Adding and removing accounts

extension HomeModel {
    /// Sign In with Google, then the home connects the account. Nil on success, or a plain sentence. A closed Google
    /// page returns nil and changes nothing.
    /// `scopes` and `use` default to Gmail and Google Calendar; Connect Apps passes Drive's (`use` ["drive"]).
    func connectGoogle(_ state: AppState, loginHint: String?, scopes: [String] = GoogleSignIn.scopes,
                       use: [String]? = nil) async -> String? {
        guard let clientID = googleClientID else {
            return "Sign In with Google isn't set up for your home yet."
        }
        #if DEBUG
        if state.fixtureMode {
            try? await Task.sleep(for: .seconds(1.2))
            let address = (loginHint?.contains("@") == true ? loginHint! : "sam.rivera@gmail.com").lowercased()
            var s = settings ?? HomeSettings()
            var g = s.google ?? .init()
            var list = (g.accounts ?? []).filter { $0.address != address }
            list.append(.init(id: "g-\(list.count + 1)", address: address, state: "connected"))
            g.accounts = list
            s.google = g
            settings = s
            return nil
        }
        #endif
        let grant: GoogleSignIn.Grant
        do {
            grant = try await GoogleSignIn().signIn(clientID: clientID, loginHint: loginHint, scopes: scopes)
        } catch GoogleSignIn.Failure.cancelled {
            return nil
        } catch {
            return error.localizedDescription
        }
        do {
            let r = try await state.client.connectGoogle(.init(code: grant.code, code_verifier: grant.verifier,
                                                                redirect_uri: grant.redirectURI, client_id: grant.clientID,
                                                                use: use))
            await load(state)
            return r.ok ? nil : (r.detail ?? "Google didn't connect. Try again in a moment.")
        } catch {
            return HubClient.describe(error)
        }
    }

    func disconnectGoogle(_ id: String, _ state: AppState) async -> String? {
        await change(state) { s in s.google?.accounts?.removeAll { $0.id == id } } live: {
            try await state.client.disconnectGoogle(id)
        }
    }

    func removeMailbox(_ id: String, _ state: AppState) async -> String? {
        await change(state) { s in s.mail?.accounts?.removeAll { $0.id == id } } live: {
            try await state.client.removeMailbox(id)
        }
    }

    func removeCalendar(_ id: String, _ state: AppState) async -> String? {
        await change(state) { s in s.calendars?.removeAll { $0.id == id } } live: {
            try await state.client.removeCalendar(id)
        }
    }

    /// Adds a mailbox with an app password; the home signs in to check it. Returns the home's answer, or nil with a
    /// sentence when it couldn't be reached.
    func addMailbox(_ body: HubClient.MailboxBody, _ state: AppState) async -> (AccountCheck?, String?) {
        #if DEBUG
        if state.fixtureMode {
            try? await Task.sleep(for: .seconds(1))
            if body.password.count < 8 {
                return (AccountCheck(ok: false, checked: false,
                                     detail: "That password didn't work. Make an app-specific password and paste it here, not the password you sign in with."), nil)
            }
            var s = settings ?? HomeSettings()
            var mail = s.mail ?? .init(accounts: [])
            mail.accounts = (mail.accounts ?? []) + [.init(id: body.provider, address: body.address, label: body.label,
                                                           provider: body.provider, state: "connected")]
            s.mail = mail
            settings = s
            return (AccountCheck(ok: true, checked: true, detail: "Connected. Your Zips can read this mailbox now."), nil)
        }
        #endif
        do {
            let r = try await state.client.addMailbox(body)
            if r.ok { await load(state) }
            return (r, nil)
        } catch {
            return (nil, HubClient.describe(error))
        }
    }

    func addCalendar(_ body: HubClient.CalendarBody, _ state: AppState) async -> (AccountCheck?, String?) {
        #if DEBUG
        if state.fixtureMode {
            try? await Task.sleep(for: .seconds(1))
            var s = settings ?? HomeSettings()
            s.calendars = (s.calendars ?? []) + [.init(id: "\(body.provider)-cal", kind: body.kind, label: body.label,
                                                        username: body.username, provider: body.provider, state: "connected")]
            settings = s
            return (AccountCheck(ok: true, checked: true, detail: "Connected. Your Zips can see this calendar now."), nil)
        }
        #endif
        do {
            let r = try await state.client.addCalendar(body)
            if r.ok { await load(state) }
            return (r, nil)
        } catch {
            return (nil, HubClient.describe(error))
        }
    }

    private func change(_ state: AppState, fixture: (inout HomeSettings) -> Void,
                        live: () async throws -> Void) async -> String? {
        #if DEBUG
        if state.fixtureMode {
            if var s = settings { fixture(&s); settings = s }
            return nil
        }
        #endif
        do {
            try await live()
            await load(state)
            return nil
        } catch {
            return HubClient.describe(error)
        }
    }
}
