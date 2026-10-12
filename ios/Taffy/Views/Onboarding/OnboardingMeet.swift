import SwiftUI

// Screens 1 to 3: meet the agent, name it, and its first words.

// MARK: 1. Meet your agent

struct MeetScreen: View {
    @Environment(AppState.self) private var state
    @Environment(OnboardingModel.self) private var model
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var arrived = false

    var body: some View {
        @Bindable var model = model
        OnboardingPage {
            GeometryReader { geo in
                let stage = max(160, min(geo.size.height * 0.52, geo.size.width - 112, 420))
                VStack(spacing: 0) {
                    OnboardingHeadline("Create Your First Zip")
                        .padding(.horizontal, 24)
                        .opacity(arrived ? 1 : 0)
                        .offset(y: arrived ? 0 : 8)
                    Spacer(minLength: 8)
                    HStack(alignment: .center, spacing: 0) {
                        OutfitColumn(look: $model.draft.look)
                            .frame(width: 84, height: min(stage + 140, geo.size.height - 190))
                        AgentStage(look: model.draft.look, mood: .appear, size: stage)
                            .frame(maxWidth: .infinity)
                            .padding(.trailing, 12)
                    }
                    Spacer(minLength: 8)
                    BodyCarousel(selected: $model.draft.look.body)
                }
            }
        } footer: {
            OnboardingButton("Continue") { model.go(.name) }
            if !model.rehearsal && !state.paired {
                Button("I Already Have an Account") { state.onboarding = false }
                    .font(Theme.text(15, .semibold)).foregroundStyle(Theme.ink)
                    .frame(minHeight: 40)
                SampleHomeLink()
            }
        }
        .task {
            if !reduceMotion { try? await Task.sleep(for: .seconds(0.35)) }
            withAnimation(.easeOut(duration: 0.5)) { arrived = true }
        }
    }
}

// MARK: 2. Name it

struct NameScreen: View {
    @Environment(OnboardingModel.self) private var model
    @State private var name = ""
    @State private var done = false
    @FocusState private var typing: Bool

    var body: some View {
        OnboardingPage {
            // The headline and the badge keep their size; the agent gets whatever room is left, so with the
            // keyboard up the whole badge and the typed name stay visible above Done.
            VStack(spacing: 0) {
                OnboardingHeadline("What Do You Want to Name It?")
                    .padding(.horizontal, 24)
                GeometryReader { geo in
                    let stage = min(geo.size.height, geo.size.width, 280)
                    AgentStage(look: model.draft.look, mood: done ? .happy : (typing ? .watching : .idle), size: stage)
                        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .bottom)
                        .opacity(stage < 72 ? 0 : 1)
                }
                NameBadge(text: $name, focused: $typing, compact: typing, onDone: finish)
                    .padding(.top, 4)
                Text(OnboardingModel.validName(name) ? " " : "Use up to 24 letters, numbers and spaces.")
                    .font(Theme.caption).foregroundStyle(Theme.danger)
                    .padding(.top, typing ? 6 : 10)
                if !typing { Spacer(minLength: 0).frame(maxHeight: 40) }
            }
            .animation(.easeOut(duration: 0.2), value: typing)
        } footer: {
            OnboardingButton("Done", enabled: OnboardingModel.validName(name) && !done) { finish() }
        }
        .onAppear { name = model.draft.agentName }
        #if DEBUG
        .task {   // -typing: the keyboard up, for screenshots
            if CommandLine.arguments.contains("-typing") { try? await Task.sleep(for: .seconds(0.6)); typing = true }
        }
        #endif
        .sensoryFeedback(.success, trigger: done)
    }

    private func finish() {
        guard OnboardingModel.validName(name), !done else { return }
        model.draft.agentName = name.trimmingCharacters(in: .whitespaces)
        typing = false
        done = true
        Task {
            try? await Task.sleep(for: .seconds(1.3))
            model.go(.talk)
        }
    }
}

/// A "HELLO my name is" sticker: a red badge with a white writing area.
private struct NameBadge: View {
    @Binding var text: String
    var focused: FocusState<Bool>.Binding
    /// Smaller while the keyboard is up, so the badge fits above it on the smallest iPhones.
    var compact = false
    let onDone: () -> Void

    var body: some View {
        VStack(spacing: 0) {
            Text("HELLO")
                .font(Theme.text(compact ? 30 : 38, .black))
                .tracking(3)
                .padding(.top, compact ? 8 : 12)
            Text("my name is")
                .font(Theme.text(compact ? 14 : 16, .semibold))
                .padding(.bottom, compact ? 6 : 10)
            TextField("", text: $text, prompt: Text("Zero").foregroundStyle(Color.black.opacity(0.22)))
                .font(Theme.text(compact ? 30 : 34, .semibold))
                .foregroundStyle(Color(hex: "#1C1C1E"))
                .multilineTextAlignment(.center)
                .textInputAutocapitalization(.words)
                .autocorrectionDisabled()
                .submitLabel(.done)
                .focused(focused)
                .onSubmit(onDone)
                .onChange(of: text) { _, new in if new.count > 24 { text = String(new.prefix(24)) } }
                .frame(height: compact ? 62 : 78)
                .frame(maxWidth: .infinity)
                .background(Color.white, in: RoundedRectangle(cornerRadius: 6, style: .continuous))
                .padding(.horizontal, 10)
                .accessibilityLabel("Agent name")
            Color.clear.frame(height: compact ? 10 : 14)
        }
        .foregroundStyle(.white)
        .frame(width: compact ? 260 : 290)
        .background(Color(hex: "#D7262E"), in: RoundedRectangle(cornerRadius: 18, style: .continuous))
        .shadow(color: .black.opacity(0.08), radius: 2, y: 1)
        .shadow(color: .black.opacity(0.1), radius: 14, y: 8)
        .rotationEffect(.degrees(-2))
        .onTapGesture { focused.wrappedValue = true }
    }
}

// MARK: 3. Its first words

/// Scripted, game-style dialogue: the agent asks a few questions about you, one at a time, with a typewriter.
/// No AI is available until the Mac is set up, so the lines are fixed.
struct TalkScreen: View {
    @Environment(AppState.self) private var state
    @Environment(OnboardingModel.self) private var model
    @State private var index = 0
    @State private var shown = ""
    @State private var speaking = false
    @State private var answer = ""
    @State private var typer: Task<Void, Never>?
    @FocusState private var answering: Bool

    private let questions = OnboardingModel.questions
    private var finished: Bool { index >= questions.count }

    var body: some View {
        OnboardingPage {
            GeometryReader { geo in
                let stage = max(110, min(geo.size.height * 0.45, 260))
                VStack(spacing: 0) {
                    Spacer(minLength: 0)
                    AgentStage(look: model.draft.look, mood: speaking ? .talking : .idle, size: stage)
                    SpeechBubble(speaker: model.agentName, shown: shown, full: line(index))
                        .padding(.horizontal, 24)
                        .padding(.top, 20)
                        .onTapGesture { finishLine() }
                    Spacer(minLength: 0)
                }
            }
        } footer: {
            // The answer row stays on screen while the agent speaks (Send and Skip wait for it), so the keyboard
            // stays up between questions instead of dropping and rising again.
            if finished {
                if !speaking { OnboardingButton("Continue") {
                    // A Mac connected to its own engine checks the plan next. Anywhere else the person sets Zero up on their
                    // Mac (or pairs with a code); a new account on the front door only where sign-up is open.
                    model.go(model.rehearsal ? .code
                             : state.paired && Theme.onMac ? .plan
                             : FrontDoor.signUpOpen ? .account : .mac)
                } }
            } else {
                answerRow
                Button("Skip") { advance(with: "") }
                    .font(Theme.text(16, .medium)).foregroundStyle(Theme.inkSoft)
                    .frame(minHeight: 40)
                    .disabled(speaking)
                    .opacity(speaking ? 0.4 : 1)
            }
        }
        .onAppear {
            say(line(index))
            answering = true
        }
        .onDisappear { typer?.cancel() }
    }

    private var answerRow: some View {
        HStack(spacing: 10) {
            TextField(placeholder, text: $answer)
                .font(Theme.text(17))
                .foregroundStyle(Theme.ink)
                .keyboardType(questions[index] == "age" || questions[index] == "sleep" ? .numbersAndPunctuation
                              : questions[index] == "email" ? .emailAddress : .default)
                .textInputAutocapitalization(questions[index] == "email" ? .never : (index == 0 ? .words : .sentences))
                .autocorrectionDisabled(questions[index] == "email")
                .submitLabel(.send)
                .focused($answering)
                .onSubmit { send() }
                .padding(.horizontal, 20)
                .frame(minHeight: 52)
                .background(Color(hex: "#F2F2F2"), in: Capsule())
            Button(action: send) {
                Image(systemName: "arrow.up")
                    .font(.system(size: 18, weight: .semibold))
                    .foregroundStyle(Theme.mineText)
                    .frame(width: 52, height: 52)
                    .background(Theme.mine, in: Circle())
                    .opacity(answer.trimmingCharacters(in: .whitespaces).isEmpty || speaking ? 0.3 : 1)
            }
            .buttonStyle(PressStyle(scale: 0.92))
            .disabled(answer.trimmingCharacters(in: .whitespaces).isEmpty || speaking)
            .accessibilityLabel("Send")
        }
    }

    private var placeholder: String {
        switch questions[index] {
        case "name": return "Your name"
        case "work": return "What you do"
        case "home": return "Your city or town"
        case "age": return "Your age"
        case "sleep": return "For example, 11 p.m. to 7 a.m."
        case "email": return "you@example.com"
        default: return "For example, my wife and two kids"
        }
    }

    private func value(_ key: String) -> String {
        (key == "name" ? model.draft.personName : model.draft.about[key] ?? "").trimmingCharacters(in: .whitespaces)
    }

    /// What the agent says before question i, reacting to whether the last one was answered.
    private func line(_ i: Int) -> String {
        let person = model.personName
        let answered = i > 0 && i <= questions.count && !value(questions[i - 1]).isEmpty
        switch i {
        // Colin's tone: about a 4 out of 10 for warmth, like a friend catching up rather than an introduction.
        case 0: return "Hey, I'm \(model.agentName). What should I call you?"
        case 1: return (person.isEmpty ? "That's fine." : "Nice, \(person).") + " So what's keeping you busy these days?"
        case 2: return (answered ? "Got it." : "No problem.") + " Where are you living right now?"
        case 3: return (answered ? "That's cool." : "That's okay.") + " How old are you, if you don't mind me asking?"
        case 4: return (answered ? "Thanks for sharing." : "That's fine.") + " Who are your people? It could be family, a partner, roommates or anyone else."
        case 5: return "Almost done. When do you usually sleep? I'll keep quiet then unless something can't wait."
        case 6: return (answered ? "Got it." : "No problem.") + " Last one. Which email address do you use the most?"
        default: return (person.isEmpty ? "Thanks." : "Thanks, \(person).") + " That gives me a good start."
        }
    }

    private func send() {
        let a = answer.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !a.isEmpty, !speaking else { return }
        advance(with: a)
    }

    private func advance(with a: String) {
        guard !finished else { return }
        let key = questions[index]
        if key == "name" { model.draft.personName = a } else { model.draft.about[key] = a }
        answer = ""
        index += 1
        if finished { answering = false }
        say(line(index))
    }

    private func say(_ text: String) {
        typer?.cancel()
        shown = ""
        speaking = true
        typer = Task {
            for ch in text {
                try? await Task.sleep(for: .milliseconds(20))
                if Task.isCancelled { return }
                shown.append(ch)
                if ".!?".contains(ch) { try? await Task.sleep(for: .milliseconds(147)) }
                if ch == "," { try? await Task.sleep(for: .milliseconds(73)) }
            }
            speaking = false
            if !finished { answering = true }
        }
    }

    private func finishLine() {
        guard speaking else { return }
        typer?.cancel()
        shown = line(index)
        speaking = false
        if !finished { answering = true }
    }
}

/// A game-style dialogue box: the speaker's name on a tab, a tail pointing up at the agent, and the line
/// typing out. The full line is laid out invisibly so the box never changes size as it types.
private struct SpeechBubble: View {
    let speaker: String
    let shown: String
    let full: String

    var body: some View {
        // One Text holding the whole line, the untyped part drawn clear, so every word sits on its final line from
        // the start and nothing reflows as it types.
        (Text(shown) + Text(full.hasPrefix(shown) ? String(full.dropFirst(shown.count)) : "").foregroundColor(.clear))
        .font(Theme.text(21, .medium))
        .foregroundStyle(Theme.ink)
        .fixedSize(horizontal: false, vertical: true)
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.horizontal, 20)
        .padding(.top, 24)
        .padding(.bottom, 20)
        .background(Color.white, in: RoundedRectangle(cornerRadius: 22, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: 22, style: .continuous).stroke(Theme.ink, lineWidth: 2.5))
        .overlay(alignment: .top) {
            BubbleTail()
                .fill(Color.white)
                .overlay(BubbleTail().stroke(Theme.ink, style: StrokeStyle(lineWidth: 2.5, lineJoin: .round)))
                .frame(width: 26, height: 14)
                .offset(y: -12)
                // Covers the box's border where the tail meets it.
                .overlay(alignment: .bottom) { Rectangle().fill(Color.white).frame(width: 21, height: 4).offset(y: -10.5) }
        }
        .overlay(alignment: .topLeading) {
            Text(speaker)
                .font(Theme.text(14, .bold))
                .foregroundStyle(Theme.mineText)
                .padding(.horizontal, 12)
                .frame(height: 26)
                .background(Theme.mine, in: Capsule())
                .offset(x: 18, y: -13)
        }
        .accessibilityElement()
        .accessibilityLabel("\(speaker) says: \(full)")
    }
}

private struct BubbleTail: Shape {
    func path(in r: CGRect) -> Path {
        var p = Path()
        p.move(to: CGPoint(x: r.minX, y: r.maxY))
        p.addLine(to: CGPoint(x: r.midX, y: r.minY))
        p.addLine(to: CGPoint(x: r.maxX, y: r.maxY))
        return p
    }
}
