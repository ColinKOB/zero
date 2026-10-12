import Foundation
import UserNotifications

/// What the app and its Notification Service Extension (ios/NotificationService) share about notifications (Oct 10,
/// docs/qa/2026-10-10-notifications.md):
///
/// - Each Zip's name, color and avatar picture, kept in the App Group container by the app whenever the Zips load, so
///   the extension can show a notification as a message from that Zip (iOS Communication Notifications) without the
///   app running.
/// - The notification categories. A Zip's message gets Reply ("zero.msg.<zip>"); a card from Zero gets the card's own
///   answers ("zero.card.<feed id>"), which the extension registers from the payload before the notification shows.
///   Every category's hidden-preview placeholder says who it is from ("From Money"), so with Show Previews set to When
///   Unlocked the lock screen still says which Zip wrote before Face ID.
enum ZipNotify {
    static let appGroup = "group.com.example.Taffy"
    static let messagePrefix = "zero.msg."
    static let cardPrefix = "zero.card."
    /// Reply on a Zip's message: the words go to that Zip (hub: /api/bots/<zip>/notification-reply).
    static let messageReply = "msg_reply"

    static var container: URL? {
        FileManager.default.containerURL(forSecurityApplicationGroupIdentifier: appGroup)
    }

    private static var shared: UserDefaults? { UserDefaults(suiteName: appGroup) }

    // MARK: Zips

    struct Zip: Codable, Equatable {
        let id: String
        let name: String
        let color: String
    }

    private static func safe(_ id: String) -> String {
        String(id.unicodeScalars.filter { CharacterSet.alphanumerics.contains($0) || $0 == "-" || $0 == "_" })
    }

    static func avatarURL(_ bot: String) -> URL? {
        container?.appendingPathComponent("avatars", isDirectory: true).appendingPathComponent("\(safe(bot)).png")
    }

    static func saveZips(_ zips: [Zip]) {
        guard let data = try? JSONEncoder().encode(zips) else { return }
        shared?.set(data, forKey: "zips")
    }

    static func zips() -> [String: Zip] {
        guard let data = shared?.data(forKey: "zips"), let list = try? JSONDecoder().decode([Zip].self, from: data)
        else { return [:] }
        return Dictionary(list.map { ($0.id, $0) }, uniquingKeysWith: { a, _ in a })
    }

    static func name(of bot: String) -> String {
        zips()[bot]?.name ?? (bot == "default" ? "Zero" : bot.capitalized)
    }

    static func placeholder(_ name: String) -> String { "From \(name)" }

    // MARK: Categories

    static func messageCategory(bot: String, name: String) -> UNNotificationCategory {
        let reply = UNTextInputNotificationAction(identifier: messageReply, title: "Reply", options: [],
                                                  textInputButtonTitle: "Send", textInputPlaceholder: "Message \(name)")
        return UNNotificationCategory(identifier: messagePrefix + bot, actions: [reply], intentIdentifiers: [],
                                      hiddenPreviewsBodyPlaceholder: placeholder(name), options: [])
    }

    /// A card's answer as the hub sends it (notices.card_actions): "open" opens Zero to the card (a send or an
    /// approval), "auth" asks for Face ID first (money and health), "background" answers right there.
    struct ActionSpec: Codable, Equatable {
        let id: String
        let title: String
        let mode: String
    }

    static func specs(_ info: [AnyHashable: Any]) -> [ActionSpec] {
        guard let raw = info["actions"] as? [[String: Any]] else { return [] }
        return raw.compactMap { a in
            guard let id = a["id"] as? String, let title = a["title"] as? String, !title.isEmpty else { return nil }
            return ActionSpec(id: id, title: title, mode: a["mode"] as? String ?? "background")
        }
    }

    static func cardCategory(id: String, from name: String, actions: [ActionSpec]) -> UNNotificationCategory {
        var list: [UNNotificationAction] = actions.prefix(3).map { spec in
            let options: UNNotificationActionOptions = spec.mode == "open" ? [.foreground]
                : spec.mode == "auth" ? [.authenticationRequired] : []
            return UNNotificationAction(identifier: spec.id, title: spec.title, options: options)
        }
        list.append(UNTextInputNotificationAction(identifier: "reply", title: "Reply", options: [],
                                                  textInputButtonTitle: "Send", textInputPlaceholder: "Your answer"))
        return UNNotificationCategory(identifier: id, actions: list, intentIdentifiers: [],
                                      hiddenPreviewsBodyPlaceholder: placeholder(name), options: [])
    }

    private struct SavedCard: Codable {
        let id: String
        let name: String
        let actions: [ActionSpec]
    }

    /// Cards the extension registered, so the app keeps them when it registers its own categories (the system keeps
    /// one set, and every call replaces it).
    static func rememberCard(id: String, name: String, actions: [ActionSpec]) {
        var cards = savedCardList().filter { $0.id != id }
        cards.append(SavedCard(id: id, name: name, actions: actions))
        if let data = try? JSONEncoder().encode(Array(cards.suffix(24))) { shared?.set(data, forKey: "cards") }
    }

    private static func savedCardList() -> [SavedCard] {
        guard let data = shared?.data(forKey: "cards") else { return [] }
        return (try? JSONDecoder().decode([SavedCard].self, from: data)) ?? []
    }

    static func savedCards() -> [UNNotificationCategory] {
        savedCardList().map { cardCategory(id: $0.id, from: $0.name, actions: $0.actions) }
    }

    /// Adds `categories` to the registered set and waits until the system has them, at most about a second.
    /// Returns whether they are registered.
    static func add(_ categories: [UNNotificationCategory]) async -> Bool {
        let center = UNUserNotificationCenter.current()
        var current = await center.notificationCategories()
        let ids = Set(categories.map(\.identifier))
        current = current.filter { !ids.contains($0.identifier) }.union(categories)
        center.setNotificationCategories(current)
        for _ in 0..<10 {
            let now = await center.notificationCategories()
            if ids.isSubset(of: Set(now.map(\.identifier))) { return true }
            try? await Task.sleep(nanoseconds: 100_000_000)
        }
        return false
    }
}
