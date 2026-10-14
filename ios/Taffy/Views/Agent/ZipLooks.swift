import SwiftUI

extension Bot {
    var agentLook: AgentLook { AgentLook.forZip(id: id, look: look, color: color) }
}

extension AgentLook {
    static let starters: [String: AgentLook] = [
        "default": .zero, "school": .init(body: "drop", outfit: "plain"), "builder": .init(body: "moon", outfit: "plain"),
        "clients": .init(body: "leaf", outfit: "plain"), "money": .init(body: "hex", outfit: "plain"),
        "career": .init(body: "gumdrop", outfit: "plain"),
    ]

    static func forZip(id: String, look: ZipLook?, color: String?) -> AgentLook {
        if let body = look?.body, AgentBody.all.contains(where: { $0.id == body }) {
            let outfit = look?.outfit.flatMap { o in AgentOutfit.all.first { $0.id == o }?.id } ?? "plain"
            return AgentLook(body: body, outfit: outfit)
        }
        if let s = starters[id] { return s }
        return AgentLook(body: closestBody(to: look?.color ?? color ?? "#F2711C"), outfit: "plain")
    }

    static func closestBody(to hex: String) -> String {
        func rgb(_ h: String) -> (Double, Double, Double) {
            var v: UInt64 = 0
            Scanner(string: h.trimmingCharacters(in: CharacterSet(charactersIn: "# "))).scanHexInt64(&v)
            return (Double((v >> 16) & 0xFF), Double((v >> 8) & 0xFF), Double(v & 0xFF))
        }
        let (r1, g1, b1) = rgb(hex)
        return AgentBody.colors.min { a, b in
            func d(_ c: String) -> Double {
                let (r2, g2, b2) = rgb(c)
                let rm = (r1 + r2) / 2
                return (2 + rm / 256) * pow(r1 - r2, 2) + 4 * pow(g1 - g2, 2) + (2 + (255 - rm) / 256) * pow(b1 - b2, 2)
            }
            return d(a.value) < d(b.value)
        }?.key ?? "o"
    }

    var zipLook: ZipLook {
        ZipLook(body: body, outfit: AgentBody.named(body).hasOutfits ? outfit : "none")
    }
}

struct BotAvatar: View {
    let bot: Bot?
    var size: CGFloat = 56
    var working = false

    @State private var happy = false

    var body: some View {
        AgentStage(look: bot?.agentLook ?? .zero, mood: happy ? .happy : .idle, size: size)
            .onChange(of: working) { was, now in
                guard was && !now else { return }
                happy = true
                Task {
                    try? await Task.sleep(for: .seconds(1.6))
                    happy = false
                }
            }
            .accessibilityHidden(true)
    }
}
