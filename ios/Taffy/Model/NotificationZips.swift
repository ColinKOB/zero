import SwiftUI
import UserNotifications

/// Keeps what the Notification Service Extension needs to show a notification as a message from a Zip (Oct 10): each
/// Zip's name and color, and its avatar drawn once as a picture, in the App Group container (PushShared/ZipNotify.swift).
/// Runs whenever the Zips load; a picture is drawn again only when that Zip's look, color or the Simple Look setting
/// changed. Also registers each Zip's Reply category, whose hidden preview says who it is from.
@MainActor
enum NotificationZips {
    private static var drawn: [String: String] = [:]
    private static var registered = false

    static func update(_ bots: [Bot]) {
        guard !bots.isEmpty, let container = ZipNotify.container else { return }
        let zips = bots.map { ZipNotify.Zip(id: $0.id, name: $0.name, color: $0.color) }
        let before = ZipNotify.zips()
        let changed = before.count != zips.count || zips.contains { before[$0.id] != $0 }
        if changed { ZipNotify.saveZips(zips) }
        let dir = container.appendingPathComponent("avatars", isDirectory: true)
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        let simple = UserDefaults.standard.bool(forKey: SimpleLook.key)
        for bot in bots {
            guard let url = ZipNotify.avatarURL(bot.id) else { continue }
            let key = "\(bot.agentLook.picture)|\(simple)|\(bot.color)|1"
            if drawn[bot.id] == key && FileManager.default.fileExists(atPath: url.path) { continue }
            let renderer = ImageRenderer(content: AvatarTile(bot: bot))
            renderer.scale = 3
            if let data = renderer.uiImage?.pngData() {
                try? data.write(to: url, options: .atomic)
                drawn[bot.id] = key
            }
        }
        if changed || !registered {
            registered = true
            Push.registerCategories()
        }
    }
}

/// A Zip as its notification avatar: the character on a soft circle of its color. iOS crops it to a circle.
struct AvatarTile: View {
    let bot: Bot

    var body: some View {
        ZStack {
            Circle().fill(Color.white)
            Circle().fill(Color(hex: bot.color).opacity(0.28))
            AgentStage(look: bot.agentLook, size: 66)
                .offset(y: 2)
        }
        .frame(width: 80, height: 80)
        .environment(\.colorScheme, .light)
    }
}

/// Settings, under Alerts: a one-time hint while notification previews show on a locked iPhone. Notifications now
/// name things like a bill and its amount (Colin, Oct 10: "Bill names and amounts should be on the lock screen once
/// you sign in with Face ID"), so this says how to keep them hidden until Face ID, with a button to the setting.
struct LockScreenPrivacySection: View {
    /// The app's Show Previews setting, read by the screen that holds this section (`previewsSetting`); an empty
    /// section has no view to run a task on.
    let previews: UNShowPreviewsSetting?
    @AppStorage("lockScreenPrivacyHintDone") private var done = false

    var body: some View {
        if !Theme.onMac && !done && previews == .always {
            Section {
                VStack(alignment: .leading, spacing: 10) {
                    Text("Your notifications can name things like a bill and its amount. To keep them hidden until you unlock your iPhone with Face ID, set Show Previews to When Unlocked.")
                        .font(Theme.body).foregroundStyle(Theme.ink)
                    HStack(spacing: 16) {
                        Button("Open Notification Settings") {
                            if let url = URL(string: UIApplication.openNotificationSettingsURLString) {
                                UIApplication.shared.open(url)
                            }
                        }
                        .buttonStyle(.borderless)
                        .foregroundStyle(Theme.taffyDeep)
                        Spacer()
                        Button("Not Now") { done = true }
                            .buttonStyle(.borderless)
                            .foregroundStyle(Theme.inkSoft)
                    }
                }
                .padding(.vertical, 4)
            } header: { Text("Lock Screen Privacy") }
        }
    }

    static func previewsSetting() async -> UNShowPreviewsSetting {
        #if DEBUG
        if CommandLine.arguments.contains("-previewsAlways") { return .always }
        #endif
        return await UNUserNotificationCenter.current().notificationSettings().showPreviewsSetting
    }
}
