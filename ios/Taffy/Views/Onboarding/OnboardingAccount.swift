import SwiftUI

struct AccountScreen: View {
    @Environment(AppState.self) private var state
    @Environment(OnboardingModel.self) private var model
    @Environment(\.scenePhase) private var scenePhase
    @State private var username = ""
    @State private var password = ""
    @State private var problem: String?
    @State private var busy = false
    @State private var message = ""
    @State private var step = ""
    @State private var started = Date()
    @State private var done = false
    @FocusState private var field: Field?

    enum Field { case username, password }

    /// A home is being made for this sign-up.
    private var building: Bool { model.draft.signUpJob != nil || done }
    private var formReady: Bool {
        username.trimmingCharacters(in: .whitespaces).count >= 3 && password.count >= 8
    }

    var body: some View {
        OnboardingPage {
            ScrollView {
                VStack(alignment: .leading, spacing: 16) {
                    AgentStage(look: model.draft.look, mood: done ? .happy : (field == nil ? .idle : .watching),
                               size: building ? 200 : 120)
                        .frame(maxWidth: .infinity)
                    if building { progress } else { form }
                }
                .padding(.horizontal, 24)
                .padding(.bottom, 12)
            }
            .scrollIndicators(.hidden)
            .scrollDismissesKeyboard(.interactively)
        } footer: {
            if building {
                if needsPassword {
                    OnboardingButton(busy ? "Signing In" : "Sign In", busy: busy, enabled: password.count >= 8) {
                        Task { await finish() }
                    }
                }
            } else {
                OnboardingButton(busy ? "Creating Your Account" : "Create Account", busy: busy, enabled: formReady) {
                    create()
                }
            }
        }
        .onAppear {
            if username.isEmpty { username = model.draft.username ?? "" }
            if !building { field = username.isEmpty ? .username : .password }
        }
        .task(id: model.draft.signUpJob) { await watch() }
        .sensoryFeedback(.success, trigger: done)
    }

    // MARK: The form

    private var form: some View {
        VStack(alignment: .leading, spacing: 16) {
            OnboardingHeadline("Create Your Account")
            Text("Choose a username and a password. You'll use them to sign in to \(model.agentName) on your iPhone and your Mac.")
                .font(Theme.text(17)).foregroundStyle(Theme.ink)
                .fixedSize(horizontal: false, vertical: true)
            VStack(spacing: 10) {
                TextField("Username", text: $username)
                    .textContentType(FrontDoor.autoFill ? .username : .oneTimeCode)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                    .submitLabel(.next)
                    .focused($field, equals: .username)
                    .onSubmit { field = .password }
                    .accessibilityIdentifier("signUpUsername")
                    .padding(.horizontal, 16).frame(height: 52)
                    .onboardingCard(selected: field == .username, radius: 14)
                SecureField("Password", text: $password)
                    .textContentType(FrontDoor.autoFill ? .newPassword : .oneTimeCode)
                    .submitLabel(.go)
                    .focused($field, equals: .password)
                    .onSubmit(create)
                    .accessibilityIdentifier("signUpPassword")
                    .padding(.horizontal, 16).frame(height: 52)
                    .onboardingCard(selected: field == .password, radius: 14)
            }
            .font(Theme.text(17))
            Text(problem ?? "Use at least 8 characters for your password.")
                .font(Theme.text(15, problem == nil ? .regular : .medium))
                .foregroundStyle(problem == nil ? Theme.inkSoft : Theme.danger)
                .fixedSize(horizontal: false, vertical: true)
            HStack(spacing: 20) {
                Button("I Already Have an Account") { state.onboarding = false }
                Button("I Have a Pairing Code") { model.go(.code) }
            }
            .font(Theme.text(15, .medium)).foregroundStyle(Theme.ink)
            .frame(minHeight: 40)
            .padding(.top, 4)
        }
    }

    // MARK: While the home is made

    private var needsPassword: Bool { building && model.draft.signUpReady == true && passwordMissing && !done }
    /// The app was closed while the home was made, so the password is gone from memory and is asked for again.
    @State private var passwordMissing = false

    private var progress: some View {
        VStack(alignment: .leading, spacing: 14) {
            OnboardingHeadline(done ? "You're All Set" : "Setting Up \(model.agentName)")
            if passwordMissing && model.draft.signUpReady == true && !done {
                Text("\(model.agentName) is ready. Type your password to finish signing in.")
                    .font(Theme.text(17)).foregroundStyle(Theme.ink)
                    .fixedSize(horizontal: false, vertical: true)
                SecureField("Password", text: $password)
                    .textContentType(.password)
                    .focused($field, equals: .password)
                    .onSubmit { Task { await finish() } }
                    .padding(.horizontal, 16).frame(height: 52)
                    .onboardingCard(selected: field == .password, radius: 14)
            } else {
                Text(stepText)
                    .font(Theme.text(17)).foregroundStyle(Theme.ink)
                    .fixedSize(horizontal: false, vertical: true)
                if !done {
                    TimelineView(.periodic(from: started, by: 1)) { context in
                        HStack(spacing: 10) {
                            ProgressView()
                            Text(elapsed(context.date))
                                .font(Theme.text(15)).foregroundStyle(Theme.inkSoft)
                                .monospacedDigit()
                        }
                    }
                    Text("You can leave this screen. \(model.agentName) keeps going and is ready when you come back.")
                        .font(Theme.text(15)).foregroundStyle(Theme.inkSoft)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
            if let problem {
                Text(problem).font(Theme.text(15, .medium)).foregroundStyle(Theme.danger)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }

    private func elapsed(_ now: Date) -> String {
        let s = max(0, Int(now.timeIntervalSince(started)))
        return s < 60 ? "Working on it" : "\(s / 60) min \(s % 60) s so far"
    }

    /// What is happening, in the agent's name. The service's own sentence is used only for a step this build does not know.
    private var stepText: String {
        let name = model.agentName
        switch step {
        case "", "user": return "\(name) is getting a place of its own. This takes about two minutes."
        case "home": return "\(name) is moving in and setting up its desk. This takes about two minutes."
        case "done": return "\(name) is ready."
        case "waiting": return "Another new account is being set up first. Yours starts in a minute or two."
        default: return message.isEmpty ? "\(name) is getting ready." : message
        }
    }

    // MARK: Actions

    private func create() {
        guard formReady, !busy else { return }
        field = nil
        problem = nil
        busy = true
        let name = username.trimmingCharacters(in: .whitespaces)
        Task {
            defer { busy = false }
            if state.fixtureMode {
                model.draft.username = name
                model.draft.signUpJob = "fixture"
                return
            }
            do {
                let job = try await FrontDoor.signUp(username: name, password: password, name: model.personName)
                model.draft.username = name
                model.draft.signUpReady = false
                started = Date()
                message = job.message
                model.draft.signUpJob = job.job
            } catch {
                problem = FrontDoor.describe(error)
            }
        }
    }

    /// Checks the sign-up every few seconds until the home is ready, then signs in.
    private func watch() async {
        guard let job = model.draft.signUpJob else { return }
        if password.isEmpty { passwordMissing = true }
        if job == "fixture" {
            try? await Task.sleep(for: .seconds(2))
            model.draft.signUpReady = true
            await finish()
            return
        }
        while !Task.isCancelled, model.draft.signUpJob == job, model.draft.signUpReady != true {
            do {
                let s = try await FrontDoor.status(job)
                if let seconds = s.seconds { started = Date().addingTimeInterval(-Double(seconds)) }
                message = s.message
                step = s.state == "waiting" ? "waiting" : (s.step ?? "")
                problem = nil
                switch s.state {
                case "ready":
                    model.draft.signUpReady = true
                    if !password.isEmpty { await finish() }
                    return
                case "failed":
                    problem = s.message
                    model.draft.signUpJob = nil
                    return
                default: break
                }
            } catch let e as FrontDoor.Problem where e.code == 404 {
                problem = e.errorDescription
                model.draft.signUpJob = nil
                return
            } catch {
                message = "Still working. \(FrontDoor.describe(error))"
            }
            try? await Task.sleep(for: .seconds(3))
        }
    }

    private func finish() async {
        guard !busy, !done else { return }
        busy = true
        defer { busy = false }
        if state.fixtureMode {
            done = true
        } else if let error = await state.signIn(username: model.draft.username ?? username, password: password,
                                                 existing: false) {
            problem = error
            return
        } else {
            done = true
        }
        message = "Your account is ready."
        password = ""
        try? await Task.sleep(for: .seconds(1.2))
        var next = model.draft
        next.signUpJob = nil
        next.signUpReady = nil
        next.step = .plan
        withAnimation(.smooth(duration: 0.4)) { model.draft = next }
    }
}
