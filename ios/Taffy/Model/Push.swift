import SwiftUI
import UIKit
import UserNotifications

/// Notifications from the hub through Apple's push service (they replaced Telegram on Oct 8). The app asks
/// once for permission, sends its device token to the hub, and opens the bot's chat when one is tapped.
@MainActor
final class Push: NSObject, UNUserNotificationCenterDelegate {
    static let shared = Push()
    weak var state: AppState? {
        didSet {   // a tap that arrived before the state existed
            if let state, let bot = waitingBot { state.openBotRequest = bot; waitingBot = nil }
        }
    }

    /// Every install from install-iphone.sh is a debug build, which Apple's sandbox serves.
    #if DEBUG
    private let environment = "development"
    #else
    private let environment = "production"
    #endif

    // MARK: Answering Zero's questions from the notification

    /// The hub picks one per question (notices.category). Yes is the question's first answer, only where the
    /// notification's line says what it does. A send, a money or health question, or several questions at once get
    /// Open instead: the lock screen can't show the recipient and the text, so a send is never approved from here.
    static let questionCategory = "zero.question"
    static let openCategory = "zero.open"
    /// A Zip asking to use the Mac (hub mac.py). Allow opens Zero, which asks for Face ID before it answers.
    static let macCategory = "zero.mac"
    private static let pendingKey = "pendingLockAnswers"

    /// Every category the app knows, in one set (the system keeps one set and each call replaces it): Zero's question
    /// buttons, the Mac request, each Zip's Reply ("zero.msg.<zip>") and the cards the notification extension
    /// registered from a payload ("zero.card.<feed id>", PushShared/ZipNotify.swift). Each says who it is from when
    /// previews are hidden ("From Money").
    static func registerCategories() {
        let lead = ZipNotify.name(of: "default")
        let yes = UNNotificationAction(identifier: "yes", title: "Yes", options: [])
        let notNow = UNNotificationAction(identifier: "not_now", title: "Not Now", options: [])
        let reply = UNTextInputNotificationAction(identifier: "reply", title: "Reply", options: [],
                                                  textInputButtonTitle: "Send", textInputPlaceholder: "Your answer")
        let open = UNNotificationAction(identifier: "open", title: "Open", options: [.foreground])
        let question = UNNotificationCategory(identifier: questionCategory, actions: [yes, notNow, reply],
                                              intentIdentifiers: [],
                                              hiddenPreviewsBodyPlaceholder: ZipNotify.placeholder(lead), options: [])
        let openOnly = UNNotificationCategory(identifier: openCategory, actions: [open, notNow],
                                              intentIdentifiers: [],
                                              hiddenPreviewsBodyPlaceholder: ZipNotify.placeholder(lead), options: [])
        let macAllow = UNNotificationAction(identifier: "mac_allow", title: "Allow",
                                            options: [.foreground, .authenticationRequired])
        let macNotNow = UNNotificationAction(identifier: "mac_not_now", title: "Not Now", options: [])
        let mac = UNNotificationCategory(identifier: macCategory, actions: [macAllow, macNotNow],
                                         intentIdentifiers: [],
                                         hiddenPreviewsBodyPlaceholder: ZipNotify.placeholder(lead), options: [])
        var all: Set<UNNotificationCategory> = [question, openOnly, mac]
        var zips = ZipNotify.zips()
        if zips["default"] == nil { zips["default"] = ZipNotify.Zip(id: "default", name: lead, color: "#F2711C") }
        for zip in zips.values { all.insert(ZipNotify.messageCategory(bot: zip.id, name: zip.name)) }
        for card in ZipNotify.savedCards() { all.insert(card) }
        UNUserNotificationCenter.current().setNotificationCategories(all)
    }

    /// Answers the hub couldn't take (the phone was off the tailnet) wait here and go at the next launch.
    private struct PendingAnswer: Codable {
        let feedID: Int
        let action: String
        let text: String?
        var unlocked: Bool? = nil
    }

    /// `unlocked`: the button asked for Face ID before it ran (a money or health question's answers).
    private func answerFromLockScreen(feedID: Int, action: String, text: String?, unlocked: Bool = false) async {
        guard let state else { return }
        do {
            try await state.client.lockAnswer(bot: "default", feedID: feedID, action: action, text: text,
                                              unlocked: unlocked)
        } catch let e as HubClient.HubError where e.code >= 400 && e.code < 500 {
            return   // already answered elsewhere, expired, or no longer paired: nothing to retry
        } catch {
            var pending = Self.pending()
            pending.append(PendingAnswer(feedID: feedID, action: action, text: text, unlocked: unlocked))
            UserDefaults.standard.set(try? JSONEncoder().encode(pending), forKey: Self.pendingKey)
            let note = UNMutableNotificationContent()
            note.title = "Zero"
            note.body = "Your answer will go to Zero when you open the app."
            try? await UNUserNotificationCenter.current().add(
                UNNotificationRequest(identifier: "lock-retry-\(feedID)", content: note, trigger: nil))
        }
    }

    private static func pending() -> [PendingAnswer] {
        guard let data = UserDefaults.standard.data(forKey: pendingKey) else { return [] }
        return (try? JSONDecoder().decode([PendingAnswer].self, from: data)) ?? []
    }

    /// At launch: send any answers from the lock screen that couldn't reach the hub.
    func retryPending() async {
        let pending = Self.pending()
        guard !pending.isEmpty, let state, state.paired, !state.fixtureMode else { return }
        UserDefaults.standard.removeObject(forKey: Self.pendingKey)
        for p in pending {
            await answerFromLockScreen(feedID: p.feedID, action: p.action, text: p.text, unlocked: p.unlocked ?? false)
        }
    }

    func register() async {
        guard let state, state.paired, !state.fixtureMode else { return }
        let center = UNUserNotificationCenter.current()
        center.delegate = self
        Self.registerCategories()
        await retryPending()
        // This phone's key for allowing Mac use from afar; the Mac asks Colin once whether to trust it.
        await MacApprover.shared.register(client: state.client, name: UIDevice.current.name)
        let granted = (try? await center.requestAuthorization(options: [.alert, .sound, .badge])) ?? false
        if granted { UIApplication.shared.registerForRemoteNotifications() }
    }

    /// Asks for permission where the onboarding explains why, and registers when this device is connected.
    @discardableResult
    func requestPermission() async -> Bool {
        let center = UNUserNotificationCenter.current()
        center.delegate = self
        let granted = (try? await center.requestAuthorization(options: [.alert, .sound, .badge])) ?? false
        if granted, let state, state.paired, !state.fixtureMode { UIApplication.shared.registerForRemoteNotifications() }
        return granted
    }

    func gotToken(_ data: Data) {
        let token = data.map { String(format: "%02x", $0) }.joined()
        let platform = ProcessInfo.processInfo.isMacCatalystApp ? "mac" : "iphone"
        guard let state else { return }
        Task { try? await state.client.savePushToken(token, environment: environment, platform: platform) }
    }

    // MARK: Delegate callbacks
    //
    // These are the completion-handler variants on purpose. With the async variants, Swift's @objc thunk ran the body
    // on the cooperative thread pool and called UIKit's completion handler there; UIKit asserts that handler runs on
    // the main thread (-[UIApplication _performBlockAfterCATransactionCommitSynchronizes:]), so every tap on a
    // notification while Zero was in the background or closed ended in SIGABRT (docs/qa/2026-10-09-notification-crash.md).
    // Each callback hops to the main actor and calls its completion handler there.

    // A notification for the chat Colin is already reading doesn't need a banner.
    nonisolated func userNotificationCenter(_ center: UNUserNotificationCenter, willPresent notification: UNNotification,
                                            withCompletionHandler completionHandler: @escaping (UNNotificationPresentationOptions) -> Void) {
        let bot = notification.request.content.userInfo["bot_id"] as? String
        Self.onMain {
            let reading = self.state?.viewingBot
            completionHandler(bot != nil && bot == reading ? [] : [.banner, .sound, .list])
        }
    }

    nonisolated func userNotificationCenter(_ center: UNUserNotificationCenter, didReceive response: UNNotificationResponse,
                                            withCompletionHandler completionHandler: @escaping () -> Void) {
        let info = response.notification.request.content.userInfo
        let bot = info["bot_id"] as? String
        let feedID = (info["feed_id"] as? Int) ?? (info["feed_id"] as? NSNumber)?.intValue
        let action = response.actionIdentifier
        let text = (response as? UNTextInputNotificationResponse)?.userText
        let approvalID = info["approval_id"] as? String
        let mode = ZipNotify.specs(info).first { $0.id == action }?.mode
        let said = response.notification.request.content.body
        let itemID = (info["item_id"] as? Int) ?? (info["item_id"] as? NSNumber)?.intValue
        Self.onMain {
            if action == ZipNotify.messageReply, let bot, let text, !text.trimmingCharacters(in: .whitespaces).isEmpty {
                // Reply on a Zip's message: the words go to that Zip; its answer comes back as a notification.
                Task {
                    await self.replyFromLockScreen(bot: bot, text: text, line: said, itemID: itemID)
                    completionHandler()
                }
                return
            }
            if let feedID, action.hasPrefix("option:") {
                if mode == "open" {   // a send or an approval: the card, never an answer from the lock screen
                    self.openChat(bot ?? "default")
                    completionHandler()
                    return
                }
                Task {
                    await self.answerFromLockScreen(feedID: feedID, action: action, text: nil, unlocked: mode == "auth")
                    completionHandler()
                }
                return
            }
            if let approvalID, ["mac_allow", "mac_not_now"].contains(action) {
                Task {
                    await self.answerMac(approvalID, allow: action == "mac_allow")
                    if action == "mac_allow" { self.openChat(bot ?? "default") }
                    completionHandler()
                }
                return
            }
            if let feedID, ["yes", "not_now", "reply"].contains(action) {
                // Runs on the main actor, so the completion handler is called on the main thread after the answer.
                Task {
                    await self.answerFromLockScreen(feedID: feedID, action: action, text: text)
                    completionHandler()
                }
                return
            }
            self.openChat(bot ?? "default")
            completionHandler()
        }
    }

    /// Words typed on a Zip's notification. If the home can't be reached, a notification says so; the words are not
    /// kept for later, since an old reply could arrive after the moment passed.
    private func replyFromLockScreen(bot: String, text: String, line: String, itemID: Int?) async {
        guard let state, state.paired else { return }
        do {
            try await state.client.notificationReply(bot: bot, text: text, line: line, itemID: itemID)
        } catch {
            let note = UNMutableNotificationContent()
            note.title = ZipNotify.name(of: bot)
            note.body = "Your reply didn't reach me. Open Zero and send it again."
            note.threadIdentifier = bot
            try? await UNUserNotificationCenter.current().add(
                UNNotificationRequest(identifier: "reply-retry-\(bot)", content: note, trigger: nil))
        }
    }

    /// Allow or Not Now on a Zip's request to use the Mac, from the notification. Allow opened Zero; Face ID or the
    /// passcode comes up before the answer goes. The chat card shows how it went.
    private func answerMac(_ id: String, allow: Bool) async {
        guard let state, state.paired else { return }
        if allow {
            guard let request = try? await state.client.macState().approvals.first(where: { $0.id == id }) else { return }
            _ = try? await MacApprover.shared.allow(request, client: state.client)
        } else {
            _ = try? await state.client.answerMac(id, allow: false)
        }
    }

    /// The tapped notification's chat. On a cold launch the app state may not exist yet when the tap arrives (the
    /// delegate is set in didFinishLaunching); the request is kept and the home screen opens it once the bots load.
    func openChat(_ bot: String) {
        if let state {
            state.openBotRequest = bot
        } else {
            waitingBot = bot
        }
    }

    /// A tap that arrived before `state` was set; it moves to the state when TaffyApp sets it.
    private var waitingBot: String?

    /// Runs `body` on the main actor: right away when the system called on the main thread (it normally does), and
    /// otherwise on the main queue's next turn.
    private nonisolated static func onMain(_ body: @escaping @MainActor () -> Void) {
        if Thread.isMainThread {
            MainActor.assumeIsolated(body)
        } else {
            DispatchQueue.main.async { MainActor.assumeIsolated(body) }
        }
    }
}

#if DEBUG
extension Push {
    /// For the simulator and the notification UI tests (TaffyUITests/NotificationTapTests.swift):
    /// `-askNotifications` asks for permission at launch, even in -fixture mode, and `-testNotification <kind>[:seconds]`
    /// schedules a local notification with the hub's own payload (hub/taffy_hub/apns.py), so a test can tap it on the
    /// lock screen or as a banner. Kinds: `checkin` (Money's "Daily spending update", watcher.scan_cron) and `notice`
    /// (a question from Zero with feed_id and the zero.question category, notices.send_push).
    func runLaunchHooks() async {
        let args = CommandLine.arguments
        if args.contains("-askNotifications") { _ = await requestPermission() }
        // The simulator check of Oct 10's notifications: the fixture Zips' avatars and categories, as a real home's
        // refreshBots would save them, so `xcrun simctl push` shows a payload as a message from a Zip.
        if args.contains("-notificationZips"), let state {
            UNUserNotificationCenter.current().delegate = self
            for _ in 0..<50 where state.bots.isEmpty { try? await Task.sleep(for: .milliseconds(100)) }
            NotificationZips.update(state.bots)
        }
        guard let i = args.firstIndex(of: "-testNotification"), i + 1 < args.count else { return }
        if args.contains("-fixture") { UserDefaults.standard.set(Date().timeIntervalSince1970, forKey: Self.coldFixtureKey) }
        UNUserNotificationCenter.current().removeAllDeliveredNotifications()   // earlier runs' banners would match first
        let parts = args[i + 1].split(separator: ":")
        let delay = parts.count > 1 ? Double(parts[1]) ?? 5 : 5
        let note = UNMutableNotificationContent()
        note.sound = .default
        switch parts.first {
        case "notice":
            note.title = "Zero"
            note.body = "Want me to remind you about the dentist bill on Friday?"
            note.threadIdentifier = "default"
            note.categoryIdentifier = Self.questionCategory
            note.userInfo = ["bot_id": "default", "feed_id": 181]
        default:
            note.title = "Daily spending update"
            note.body = "Spending this week is $412 across 23 transactions. Dining is $16 over the cap."
            note.threadIdentifier = "money"
            note.userInfo = ["bot_id": "money"]
        }
        let trigger = UNTimeIntervalNotificationTrigger(timeInterval: max(1, delay), repeats: false)
        try? await UNUserNotificationCenter.current().add(
            UNNotificationRequest(identifier: "test-\(parts.first ?? "checkin")", content: note, trigger: trigger))
    }

    /// A launch with no arguments within two minutes of a `-fixture -testNotification` run is the test's notification
    /// opening the closed app (the system passes no arguments), so it opens the fixture home too. Read once per launch.
    nonisolated static let coldFixture: Bool = {
        let at = UserDefaults.standard.double(forKey: coldFixtureKey)
        UserDefaults.standard.removeObject(forKey: coldFixtureKey)
        return CommandLine.arguments.count == 1 && at > 0 && Date().timeIntervalSince1970 - at < 120
    }()
    private nonisolated static let coldFixtureKey = "testNotificationColdFixture"
}
#endif

@MainActor
final class AppDelegate: NSObject, UIApplicationDelegate {
    func application(_ application: UIApplication,
                     didFinishLaunchingWithOptions launchOptions: [UIApplication.LaunchOptionsKey: Any]? = nil) -> Bool {
        // Set before launch finishes, so a tap that opened the app is delivered.
        UNUserNotificationCenter.current().delegate = Push.shared
        Push.registerCategories()   // Yes, Not Now and Reply on Zero's questions (notices.category on the hub)
        return true
    }

    /// Portrait on the iPhone except on a Zip's screen (OrientationLock in ScreenView.swift).
    func application(_ application: UIApplication, supportedInterfaceOrientationsFor window: UIWindow?) -> UIInterfaceOrientationMask {
        OrientationLock.supported
    }

    func application(_ application: UIApplication, didRegisterForRemoteNotificationsWithDeviceToken deviceToken: Data) {
        Push.shared.gotToken(deviceToken)
    }

    func application(_ application: UIApplication, didFailToRegisterForRemoteNotificationsWithError error: Error) {
        print("push registration failed: \(error.localizedDescription)")
    }
}
