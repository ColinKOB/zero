import Intents
import UIKit
import UserNotifications

final class NotificationService: UNNotificationServiceExtension {
    private var handler: ((UNNotificationContent) -> Void)?
    private var fallback: UNNotificationContent?
    private let lock = NSLock()

    override func didReceive(_ request: UNNotificationRequest,
                             withContentHandler contentHandler: @escaping (UNNotificationContent) -> Void) {
        handler = contentHandler
        fallback = request.content
        guard let content = request.content.mutableCopy() as? UNMutableNotificationContent else {
            finish(request.content)
            return
        }
        Task {
            let info = content.userInfo
            let sender = info["sender"] as? [String: Any]
            let bot = sender?["id"] as? String ?? info["bot_id"] as? String ?? "default"
            let name = sender?["name"] as? String ?? (content.title.isEmpty ? ZipNotify.name(of: bot) : content.title)
            let specs = ZipNotify.specs(info)
            let feedID = (info["feed_id"] as? Int) ?? (info["feed_id"] as? NSNumber)?.intValue
            if !specs.isEmpty, let feedID {
                let id = ZipNotify.cardPrefix + "\(feedID)"
                ZipNotify.rememberCard(id: id, name: name, actions: specs)
                if await ZipNotify.add([ZipNotify.cardCategory(id: id, from: name, actions: specs)]) {
                    content.categoryIdentifier = id
                }
            }
            fallback = content
            let shown = await Self.fromZip(content, bot: bot, name: name)
            #if DEBUG
            if let url = ZipNotify.container?.appendingPathComponent("last-notification.json") {
                let note: [String: Any] = ["bot": bot, "name": name, "category": content.categoryIdentifier,
                                           "from_zip": shown != nil, "at": Date().timeIntervalSince1970]
                try? JSONSerialization.data(withJSONObject: note).write(to: url)
            }
            #endif
            finish(shown ?? content)
        }
    }

    override func serviceExtensionTimeWillExpire() {
        finish(fallback ?? UNNotificationContent())
    }

    private func finish(_ content: UNNotificationContent) {
        lock.lock()
        let h = handler
        handler = nil
        lock.unlock()
        h?(content)
    }

    // MARK: From the Zip

    static func fromZip(_ content: UNMutableNotificationContent, bot: String, name: String) async -> UNNotificationContent? {
        let image = INImage(imageData: avatar(bot: bot, name: name))
        let handle = INPersonHandle(value: "zero-\(bot)", type: .unknown)
        let person = INPerson(personHandle: handle, nameComponents: nil, displayName: name, image: image,
                              contactIdentifier: nil, customIdentifier: "zero-\(bot)", isMe: false,
                              suggestionType: .none)
        let intent = INSendMessageIntent(recipients: nil, outgoingMessageType: .outgoingMessageText,
                                         content: content.body, speakableGroupName: nil,
                                         conversationIdentifier: "zero-\(bot)", serviceName: nil, sender: person,
                                         attachments: nil)
        intent.setImage(image, forParameterNamed: \.sender)
        let interaction = INInteraction(intent: intent, response: nil)
        interaction.direction = .incoming
        try? await interaction.donate()
        return try? content.updating(from: intent)
    }

    static func avatar(bot: String, name: String) -> Data {
        if let url = ZipNotify.avatarURL(bot), let data = try? Data(contentsOf: url), !data.isEmpty {
            return data
        }
        let color = UIColor(hex: ZipNotify.zips()[bot]?.color ?? "#F2711C")
        let size = CGSize(width: 180, height: 180)
        let picture = UIGraphicsImageRenderer(size: size).image { ctx in
            color.setFill()
            ctx.cgContext.fillEllipse(in: CGRect(origin: .zero, size: size))
            let letter = String(name.prefix(1)).uppercased() as NSString
            let attrs: [NSAttributedString.Key: Any] = [.font: UIFont.systemFont(ofSize: 88, weight: .bold),
                                                        .foregroundColor: UIColor.white]
            let box = letter.size(withAttributes: attrs)
            letter.draw(at: CGPoint(x: (size.width - box.width) / 2, y: (size.height - box.height) / 2),
                        withAttributes: attrs)
        }
        return picture.pngData() ?? Data()
    }
}

private extension UIColor {
    convenience init(hex: String) {
        var v: UInt64 = 0
        Scanner(string: hex.trimmingCharacters(in: CharacterSet(charactersIn: "# "))).scanHexInt64(&v)
        self.init(red: CGFloat((v >> 16) & 0xFF) / 255, green: CGFloat((v >> 8) & 0xFF) / 255,
                  blue: CGFloat(v & 0xFF) / 255, alpha: 1)
    }
}
