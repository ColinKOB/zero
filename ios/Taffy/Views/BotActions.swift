import SwiftUI

/// How long a snooze lasts.
struct SnoozeChoice: Identifiable {
    let title: String
    let until: () -> Date
    var id: String { title }

    /// The choices, with "Until 7 AM" at the end of the home's quiet hours in the home's time zone.
    static func all(quietEnd: Int, timeZone: TimeZone) -> [SnoozeChoice] {
        [
            SnoozeChoice(title: "1 Hour") { Date().addingTimeInterval(3600) },
            SnoozeChoice(title: "2 Hours") { Date().addingTimeInterval(2 * 3600) },
            SnoozeChoice(title: "5 Hours") { Date().addingTimeInterval(5 * 3600) },
            SnoozeChoice(title: "Until \(HomeWording.hour(quietEnd))") { nextHour(quietEnd, in: timeZone) },
            SnoozeChoice(title: "1 Week") { Date().addingTimeInterval(7 * 86400) },
        ]
    }

    /// The next time the clock reads `hour` in that time zone: today's if it is still ahead, else tomorrow's.
    static func nextHour(_ hour: Int, in timeZone: TimeZone, after now: Date = Date()) -> Date {
        var cal = Calendar(identifier: .gregorian)
        cal.timeZone = timeZone
        let today = cal.date(bySettingHour: hour, minute: 0, second: 0, of: now) ?? now
        return today > now ? today : (cal.date(byAdding: .day, value: 1, to: today) ?? today)
    }
}

/// Snooze choices and the two-step delete for a bot, shared by the home list and the bot's profile.
/// Delete asks twice: once to choose it, once more to confirm it cannot be undone.
struct BotActions: ViewModifier {
    @Environment(AppState.self) private var state
    @Binding var snoozing: Bot?
    @Binding var deleting: Bot?
    var onDeleted: () -> Void = {}

    @State private var finalCheck: Bot?
    @State private var error: String?

    func body(content: Content) -> some View {
        content
            .confirmationDialog(snoozing.map { "Snooze \($0.name)" } ?? "", isPresented: present($snoozing),
                                titleVisibility: .visible, presenting: snoozing) { bot in
                ForEach(snoozeChoices) { choice in
                    Button(choice.title) { Task { error = await state.snooze(bot.id, until: choice.until()) } }
                }
                if bot.snoozedUntil != nil {
                    Button("End Snooze") { Task { error = await state.snooze(bot.id, until: nil) } }
                }
                Button("Cancel", role: .cancel) {}
            } message: { bot in
                Text("\(bot.name) keeps working. You will not get alerts from it until the snooze ends.")
            }
            .confirmationDialog(deleting.map { "Delete \($0.name)?" } ?? "", isPresented: present($deleting),
                                titleVisibility: .visible, presenting: deleting) { bot in
                Button("Delete \(bot.name)", role: .destructive) {
                    // Let the first sheet close before the second confirmation appears.
                    Task {
                        try? await Task.sleep(for: .milliseconds(450))
                        finalCheck = bot
                    }
                }
                Button("Cancel", role: .cancel) {}
            } message: { bot in
                Text("This deletes \(bot.name)'s chat history, memory and saved logins.")
            }
            .alert(finalCheck.map { "Delete \($0.name) for Good?" } ?? "", isPresented: present($finalCheck),
                   presenting: finalCheck) { bot in
                Button("Delete Forever", role: .destructive) {
                    Task {
                        error = await state.removeBot(bot.id)
                        if error == nil { onDeleted() }
                    }
                }
                Button("Keep \(bot.name)", role: .cancel) {}
            } message: { _ in
                Text("This cannot be undone. Files it saved in its work folder stay on your home computer.")
            }
            .alert("That Didn't Work", isPresented: Binding(get: { error != nil }, set: { if !$0 { error = nil } })) {
                Button("OK", role: .cancel) {}
            } message: {
                Text(error ?? "")
            }
    }

    /// When quiet hours end, from the home (its settings, else Zero's question limits), else 7 AM on this phone's clock.
    private var snoozeChoices: [SnoozeChoice] {
        let home = HomeModel.shared
        let end = home.quietHours?.end ?? state.proactivity?.limits.quiet_end ?? 7
        let zone = home.timeZone ?? state.proactivity?.timezone.flatMap(TimeZone.init(identifier:)) ?? .current
        return SnoozeChoice.all(quietEnd: end, timeZone: zone)
    }

    private func present(_ item: Binding<Bot?>) -> Binding<Bool> {
        Binding(get: { item.wrappedValue != nil }, set: { if !$0 { item.wrappedValue = nil } })
    }
}

extension View {
    func botActions(snoozing: Binding<Bot?>, deleting: Binding<Bot?>, onDeleted: @escaping () -> Void = {}) -> some View {
        modifier(BotActions(snoozing: snoozing, deleting: deleting, onDeleted: onDeleted))
    }
}
