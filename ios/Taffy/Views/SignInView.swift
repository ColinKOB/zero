import SwiftUI

/// The first screen when this device has no home: create an account (the onboarding), sign in with a username and a
/// password, or sign in with a pairing code. Log Out comes back here.
struct SignInView: View {
    @Environment(AppState.self) private var state
    @State private var mode: Mode = .choose
    @State private var username = AppState.accountUsername
    @State private var password = ""
    @State private var busy = false
    @State private var problem: String?
    @FocusState private var field: Field?

    enum Mode { case choose, password, code }
    enum Field { case username, password }

    var body: some View {
        switch mode {
        case .code:
            PairingView(showSample: false)
                .overlay(alignment: .topLeading) {
                    CircleButton(action: { go(.choose) }) { Image(systemName: "chevron.left") }
                        .accessibilityLabel("Back")
                        .padding(.leading, 20).padding(.top, 8)
                }
        default:
            VStack(spacing: 0) {
                HStack {
                    if mode == .password {
                        CircleButton(action: { go(.choose) }) { Image(systemName: "chevron.left") }
                            .accessibilityLabel("Back")
                    }
                    Spacer()
                }
                .frame(height: 44)
                .padding(.horizontal, 20).padding(.top, 4)
                ScrollView {
                    VStack(spacing: 18) {
                        AgentStage(look: .zero, mood: field == nil ? .idle : .watching, size: mode == .password ? 140 : 220)
                            .padding(.top, mode == .password ? 4 : 24)
                        Text(mode == .password ? "Sign In to Zero" : "Zero")
                            .font(Theme.text(mode == .password ? 32 : 48, .bold)).foregroundStyle(Theme.ink)
                            .accessibilityAddTraits(.isHeader)
                        if mode == .choose {
                            Text("Your team keeps your life and work moving.")
                                .font(Theme.text(18)).foregroundStyle(Theme.inkSoft)
                                .multilineTextAlignment(.center)
                        } else {
                            form
                        }
                    }
                    .padding(.horizontal, 24)
                    .frame(maxWidth: 480)
                    .frame(maxWidth: .infinity)
                }
                .scrollIndicators(.hidden)
                .scrollDismissesKeyboard(.interactively)
                VStack(spacing: 8) {
                    if mode == .choose {
                        if Theme.onMac {
                            // On a Mac, a new person sets Zero up on this Mac; accounts are for homes that exist.
                            OnboardingButton("Set Up Zero on This Mac") {
                                UserDefaults.standard.set(false, forKey: MacEngine.otherHomeKey)
                                MacEngine.shared.setupShowing = true
                            }
                            .accessibilityIdentifier("macSetUpHere")
                        } else {
                            OnboardingButton("Create an Account") { state.onboarding = true }
                                .accessibilityIdentifier("createAccount")
                        }
                        OnboardingSecondaryButton("Sign In") { go(.password) }
                            .accessibilityIdentifier("signInWithPassword")
                        Button("Sign In With a Pairing Code") { go(.code) }
                            .font(Theme.text(15, .medium)).foregroundStyle(Theme.inkSoft)
                            .frame(minHeight: 40)
                            .accessibilityIdentifier("signInWithCode")
                        SampleHomeLink()
                    } else {
                        OnboardingButton(busy ? "Signing In" : "Sign In", busy: busy, enabled: ready) { submit() }
                            .accessibilityIdentifier("signInSubmit")
                        Button("Sign In With a Pairing Code") { go(.code) }
                            .font(Theme.text(15, .medium)).foregroundStyle(Theme.inkSoft)
                            .frame(minHeight: 40)
                    }
                }
                .padding(.horizontal, 24).padding(.bottom, 12).padding(.top, 8)
                .frame(maxWidth: 480)
            }
            .background(Color.white.ignoresSafeArea())
            .environment(\.colorScheme, .light)
        }
    }

    private var ready: Bool { username.trimmingCharacters(in: .whitespaces).count >= 3 && !password.isEmpty }

    private var form: some View {
        VStack(alignment: .leading, spacing: 10) {
            TextField("Username", text: $username)
                .textContentType(FrontDoor.autoFill ? .username : .oneTimeCode)
                .textInputAutocapitalization(.never)
                .autocorrectionDisabled()
                .submitLabel(.next)
                .focused($field, equals: .username)
                .onSubmit { field = .password }
                .accessibilityIdentifier("signInUsername")
                .padding(.horizontal, 16).frame(height: 52)
                .onboardingCard(selected: field == .username, radius: 14)
            SecureField("Password", text: $password)
                .textContentType(FrontDoor.autoFill ? .password : .oneTimeCode)
                .submitLabel(.go)
                .focused($field, equals: .password)
                .onSubmit(submit)
                .accessibilityIdentifier("signInPassword")
                .padding(.horizontal, 16).frame(height: 52)
                .onboardingCard(selected: field == .password, radius: 14)
            if let problem {
                Text(problem).font(Theme.text(15, .medium)).foregroundStyle(Theme.danger)
                    .fixedSize(horizontal: false, vertical: true)
                    .padding(.top, 4)
            }
        }
        .font(Theme.text(17))
    }

    private func go(_ m: Mode) {
        problem = nil
        state.lastError = nil
        withAnimation(.smooth(duration: 0.3)) { mode = m }
        if m == .password { field = username.isEmpty ? .username : .password }
    }

    private func submit() {
        guard ready, !busy else { return }
        field = nil
        busy = true
        problem = nil
        Task {
            problem = await state.signIn(username: username, password: password)
            if problem == nil { password = "" }
            busy = false
        }
    }
}
