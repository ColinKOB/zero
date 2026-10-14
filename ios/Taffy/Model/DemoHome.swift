import Foundation
import SwiftUI

enum DemoHome {
    static let cannotChange = "This is a sample home, so that can't be changed here."
    static let person = "Alex"
    static let initials = "AL"

    static func reply(to text: String, bot: Bot?) -> String {
        let who = bot?.name ?? "Zero"
        return "This is a sample home, so \(who) can't do real work here. In your own Zero, I would take that from here and tell you when it's done."
    }
}

extension AppState {
    func enterDemo() {
        demoReturnsToOnboarding = onboarding
        demoMode = true
        fixtureMode = true
        client.demo = true
        loadDemoData()
        paired = true
        showWelcome = false
        onboarding = false
    }

    func leaveDemo() {
        demoMode = false
        fixtureMode = false
        client.demo = false
        bots = []; board = .empty; chats = [:]; working = [:]; mutedBots = []; proactivity = nil
        drafts = [:]; pinned = ["default"]
        lastError = nil
        paired = false
        onboarding = demoReturnsToOnboarding
    }

    func demoSend(_ text: String, to bot: String, replyTo: ReplyQuote? = nil) async -> String? {
        chats[bot, default: []].append(ChatMessage(id: "demo-\(UUID().uuidString)", kind: .colin, text: text, date: Date(),
                                                   replyTo: replyTo))
        working[bot] = "Thinking"
        try? await Task.sleep(for: .milliseconds(900))
        let answer = demoMode ? DemoHome.reply(to: text, bot: self.bot(bot)) : "Will do."
        working[bot] = nil
        chats[bot, default: []].append(ChatMessage(id: "demo-\(UUID().uuidString)", kind: .bot, text: answer, date: Date()))
        return answer
    }

    func demoAnswer(_ message: ChatMessage, bot: String, picked: String?) async {
        if message.question != nil, let i = chats[bot]?.firstIndex(where: { $0.id == message.id }) {
            chats[bot]?[i].question?.state = picked == nil ? "dismissed" : "picked"
            chats[bot]?[i].question?.picked = picked
            chats[bot]?[i].question?.follow = picked == nil ? nil : "Noted. In your own Zero I would take care of that now."
            return
        }
        if let picked { _ = await demoSend(picked, to: bot) } else { chats[bot]?.removeAll { $0.id == message.id } }
    }

    func demoAct(on task: TaskItem, _ action: String) {
        var open = board.needs_you
        guard let i = open.firstIndex(where: { $0.id == task.id }) else { return }
        let t = open.remove(at: i)
        board.needs_you = open
        if action == "approve" || action == "done" {
            board.done.insert(TaskItem(id: t.id, title: t.title, body: t.body, assignee: t.assignee, status: "done",
                                       created_by: t.created_by, created_at: t.created_at, started_at: t.started_at,
                                       completed_at: Date().timeIntervalSince1970, summary: t.summary, error: nil,
                                       block_reason: nil, attachments: []), at: 0)
        }
        refreshDemoCounts()
    }

    func demoDecide(_ request: SignInRequest, _ decision: String) {
        board.requests = (board.requests ?? []).filter { $0.id != request.id }
        refreshDemoCounts()
    }

    private func refreshDemoCounts() {
        for i in bots.indices {
            let id = bots[i].id
            bots[i].needs_you = board.needs_you.filter { $0.assignee == id }.count
                + (board.requests ?? []).filter { $0.bot_id == id }.count
        }
    }

    private func loadDemoData() {
        let now = Date()
        func at(_ minutesAgo: Double) -> Date { now.addingTimeInterval(-minutesAgo * 60) }
        func bot(_ id: String, _ name: String, _ role: String, _ color: String, _ symbol: String, _ shape: String,
                 _ preview: String, _ ago: Double, needs: Int = 0) -> Bot {
            Bot(id: id, name: name, role: role, color: color, symbol: symbol, working: 0, needs_you: needs, busy: false,
                preview: preview, last_at: now.timeIntervalSince1970 - ago * 60, shape: shape)
        }
        bots = [
            bot("default", "Zero", "Leads the team and talks with you.", "#F2711C", "sparkles", "egg",
                "Your flight is booked. I added it to your calendar.", 3),
            bot("bills", "Bills", "Keeps track of what is due and paid.", "#EAB308", "dollarsign.circle", "hexagon",
                "The electric bill is due Friday.", 20, needs: 3),
            bot("trips", "Trips", "Plans flights, hotels and days away.", "#3B82F6", "airplane", "cloud",
                "Hotel options for Chicago are ready.", 95, needs: 0),
            bot("study", "Study", "Keeps your classes and deadlines in order.", "#8B5CF6", "graduationcap", "droplet",
                "Your biology quiz is tomorrow at 9.", 600),
        ]
        let t = now.timeIntervalSince1970
        func task(_ id: String, _ title: String, _ who: String, _ status: String, summary: String? = nil,
                  ago: Double) -> TaskItem {
            TaskItem(id: id, title: title, body: nil, assignee: who, status: status, created_by: "default",
                     created_at: t - ago - 600, started_at: t - ago - 300, completed_at: status == "done" ? t - ago : nil,
                     summary: summary, error: nil, block_reason: nil, attachments: [])
        }
        board = Board(
            needs_you: [task("t_demo1", "Draft a reply to Jordan about the lease", "bills", "review",
                             summary: "Hi Jordan, thanks for sending the lease. I can sign it this week. Could you send the final copy by Thursday?",
                             ago: 40)],
            working: [task("t_demo2", "Compare hotels near the conference", "trips", "running", ago: 6)],
            done: [task("t_demo3", "Pay the water bill", "bills", "done", summary: "Paid $42.10 on Monday.", ago: 2880)],
            requests: [
                SignInRequest(id: 1, bot_id: "bills", site: "example-bank.test", reason: "check this month's balance",
                              created_at: t - 600, sensitive: true, why: "asked"),
                SignInRequest(id: 2, bot_id: "bills", site: "jordan@example.com",
                              reason: "Email jordan@example.com about \"The lease\"", created_at: t - 300, sensitive: false,
                              why: "asked", kind: "send", target: "jordan@example.com",
                              inputs: "To: jordan@example.com\nSubject: The lease\n\nHi Jordan, I can sign the lease this week. Could you send the final copy by Thursday? Thanks, Alex",
                              can_always: false),
            ])
        var call = ChatMessage(id: "demo-call", kind: .call, text: "We picked your flight.\n\n- **Flight:** Friday at 7:40 a.m., $186.\n- **Next:** You tell me when to book it.",
                               date: at(110), label: "2:14", botID: "default")
        call.details = ["The morning flight is $186 and has a seat by the window. I'll hold it until you say go."]
        var team = ChatMessage(id: "demo-team", kind: .team, text: "Your checking balance covers the trip with room to spare.",
                               date: at(100), label: "Bills replied to Zero", botID: "bills")
        team.team = TeamItem(from: "bills", to: "default", kind: "reply", text: team.text,
                             question: "Is there room in the budget for a $186 flight?")
        var choices = ChatMessage(id: "demo-choice", kind: .choices, text: "Want a ride to the airport?", date: at(4), botID: "default")
        choices.details = ["Yes, book one", "I'll drive", "Not now"]
        choices.feedID = 1
        var question = ChatMessage(id: "demo-question", kind: .choices, text: "Do you want me to renew it, or let it go?", date: at(30), botID: "default")
        question.details = ["Renew it", "Let it go"]
        question.feedID = 2
        question.question = QuestionCard(notice: 2, evidence: "Your library book is due Saturday, and nobody has asked for it.", state: "open")
        chats["default"] = [
            ChatMessage(id: "demo-1", kind: .colin, text: "Can you book my flight to Chicago for next Friday?", date: at(125)),
            ChatMessage(id: "demo-2", kind: .bot, text: "Yes. I found a morning flight for $186 and I'll hold it until you say go.",
                        date: at(124), reactions: [Reaction(who: "colin", kind: "heart")]),
            call, team,
            ChatMessage(id: "demo-3", kind: .colin, text: "Go ahead and book it.", date: at(60)),
            ChatMessage(id: "demo-4", kind: .bot, text: "Booked. The confirmation is in your calendar for Friday at **7:40 a.m.**", date: at(59)),
            question, choices,
        ]
        chats["bills"] = [
            ChatMessage(id: "demo-b1", kind: .colin, text: "What's due this week?", date: at(25)),
            ChatMessage(id: "demo-b2", kind: .bot, text: "Two things. The electric bill is **$88.20** and is due Friday. Your phone bill is already paid.", date: at(24)),
        ]
        chats["trips"] = [
            ChatMessage(id: "demo-t1", kind: .colin, text: "Find me a hotel near the conference.", date: at(100)),
            ChatMessage(id: "demo-t2", kind: .bot, text: "I'm comparing three. I'll put the best two in your chat when I'm done.", date: at(95)),
        ]
        chats["study"] = [
            ChatMessage(id: "demo-s1", kind: .bot, text: "Your biology quiz is tomorrow at 9. I made a one-page review from your notes.", date: at(600)),
        ]
        func noticer(_ id: String, _ name: String, _ detail: String, mode: String = "on") -> ProactivityStatus.Noticer {
            ProactivityStatus.Noticer(id: id, name: name, detail: detail, mode: mode, why: "", asked: 0, answered: 0, last: nil)
        }
        proactivity = ProactivityStatus(
            enabled: false,
            noticers: [noticer("reply_waiting", "Replies Waiting on You", "Someone asked you something by email two days ago and you haven't answered."),
                       noticer("renewal", "Renewals and Expiries", "A renewal, trial or expiry with a date in the next week."),
                       noticer("promise", "Things You Said You Would Do", "Something you told a Zip you would do, checked on the day.")],
            stopped: [],
            limits: .init(daily: 3, weekly: 12, spacing_hours: 2, quiet_start: 23, quiet_end: 7),
            today: .init(pushed: 0, checks: 0, checks_cap: 20), halved: false)
        saveCallTranscripts = true
        pinned = ["default"]
    }

    var initials: String {
        if demoMode { return DemoHome.initials }
        let saved = UserDefaults.standard.string(forKey: "personName") ?? ""
        let name = saved.trimmingCharacters(in: .whitespaces).isEmpty ? (HomeModel.shared.settings?.person?.name ?? "") : saved
        let letters = name.split(separator: " ").prefix(2).compactMap(\.first).map { String($0).uppercased() }.joined()
        return letters.isEmpty ? "Me" : letters
    }

    func savePersonName(_ name: String) {
        UserDefaults.standard.set(name.trimmingCharacters(in: .whitespaces), forKey: "personName")
    }
}

struct DemoBanner: View {
    @Environment(AppState.self) private var state

    var body: some View {
        HStack(spacing: 8) {
            TablerIcon(name: "TablerHome", size: 15)
            Text("Sample Home. Everything here is made up.")
                .font(Theme.text(13, .semibold))
                .lineLimit(1).minimumScaleFactor(0.8)
            Spacer(minLength: 4)
            Button("Leave") { state.leaveDemo() }
                .font(Theme.text(13, .bold))
                .frame(minHeight: 30)
        }
        .foregroundStyle(Theme.ink)
        .padding(.horizontal, 14)
        .frame(maxWidth: .infinity, minHeight: 34)
        .background(Color(hex: "#FBDCC8").ignoresSafeArea(edges: .top))
        .accessibilityElement(children: .contain)
    }
}

struct DemoScreenNote: View {
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        VStack(spacing: 16) {
            Spacer()
            TablerIcon(name: "TablerHome", size: 34).foregroundStyle(Theme.inkSoft)
            Text("No Screen in the Sample Home")
                .font(Theme.text(22, .bold)).foregroundStyle(Theme.ink)
            Text("In your own Zero, this shows the screen your Zip is working on, and lets you step in. The sample home has no computer behind it.")
                .font(Theme.text(16)).foregroundStyle(Theme.inkSoft)
                .multilineTextAlignment(.center)
                .padding(.horizontal, 32)
            Spacer()
            OnboardingButton("Close") { dismiss() }
                .padding(.horizontal, 24).padding(.bottom, 24)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(Theme.canvas.ignoresSafeArea())
    }
}
