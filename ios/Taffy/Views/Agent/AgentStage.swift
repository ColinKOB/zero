import SwiftUI

struct AgentBody: Identifiable, Hashable {
    let id: String
    let name: String

    var hasOutfits: Bool { !isFlat }
    var isFlat: Bool { id.hasPrefix("flat-") }
    var picturePrefix: String { id == "o" ? "zero" : id }
    var thumbnail: String { "\(picturePrefix)-plain" }

    static let plush: [AgentBody] = [
        .init(id: "o", name: "O"), .init(id: "star", name: "Star"), .init(id: "moon", name: "Moon"),
        .init(id: "bolt", name: "Bolt"), .init(id: "drop", name: "Drop"), .init(id: "pebble", name: "Pebble"),
        .init(id: "pill", name: "Pill"), .init(id: "hex", name: "Hexagon"), .init(id: "gem", name: "Gem"),
        .init(id: "squircle", name: "Square"), .init(id: "mushroom", name: "Mushroom"), .init(id: "bean", name: "Bean"),
        .init(id: "arch", name: "Arch"), .init(id: "flower", name: "Flower"), .init(id: "dome", name: "Dome"),
        .init(id: "leaf", name: "Leaf"), .init(id: "bell", name: "Bell"), .init(id: "plus", name: "Plus"),
        .init(id: "cube", name: "Cube"), .init(id: "gumdrop", name: "Gumdrop"),
    ]

    static let flat: [AgentBody] = ["o", "star", "moon", "bolt", "drop", "pill", "hex", "gem", "squircle", "mushroom",
                                    "bean", "arch", "flower", "dome", "leaf", "bell", "plus", "pebble", "acorn", "kite",
                                    "book", "calendar", "briefcase", "coin", "suitcase", "receipt"]
        .compactMap { k in FlatArt.all[k].map { AgentBody(id: "flat-" + k, name: $0.name) } }

    static let all: [AgentBody] = plush + flat

    static func named(_ id: String) -> AgentBody { all.first { $0.id == id } ?? all[0] }
}

struct AgentOutfit: Identifiable, Hashable {
    let id: String
    let name: String
    let items: [String]
    let itemNames: [String]

    static let all: [AgentOutfit] = [
        .init(id: "classic", name: "Classic", items: ["glasses-round", "scarf"], itemNames: ["round glasses", "a scarf"]),
        .init(id: "cozy", name: "Cozy", items: ["beanie"], itemNames: ["a beanie"]),
        .init(id: "studio", name: "Studio", items: ["headphones"], itemNames: ["headphones"]),
        .init(id: "sporty", name: "Sporty", items: ["cap", "glasses-square"], itemNames: ["a cap", "square glasses"]),
        .init(id: "plain", name: "Plain", items: [], itemNames: []),
    ]

    static func named(_ id: String) -> AgentOutfit { all.first { $0.id == id } ?? all[0] }

    var description: String {
        items.isEmpty ? "\(name), nothing on" : "\(name): \(ListFormatter.localizedString(byJoining: itemNames))"
    }
}

struct AgentLook: Codable, Hashable {
    var body: String
    var outfit: String

    static let zero = AgentLook(body: "o", outfit: "classic")

    var picture: String {
        let b = AgentBody.named(body)
        if b.isFlat { return b.id }
        return "\(b.picturePrefix)-\(AgentOutfit.named(outfit).id)"
    }

    var description: String {
        let b = AgentBody.named(body)
        if b.isFlat { return "A flat \(b.name)" }
        guard b.hasOutfits else { return "A plush \(b.name)" }
        let o = AgentOutfit.named(outfit)
        return o.items.isEmpty ? "A plush \(b.name)"
                               : "A plush \(b.name) wearing \(ListFormatter.localizedString(byJoining: o.itemNames))"
    }
}

enum AgentMood: Equatable {
    case idle, happy, talking, watching, appear
}

struct AgentStage: View {
    let given: AgentLook
    var mood: AgentMood = .idle
    var size: CGFloat = 280
    var exact = false
    @AppStorage(SimpleLook.key) private var simpleLook = false
    private var look: AgentLook { simpleLook && !exact ? given.simple : given }

    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @Environment(\.scenePhase) private var scenePhase
    @State private var start = Date()
    @State private var appearAt: Date?
    @State private var gaze = Gaze.center
    @State private var blinking = false
    @State private var mouthOpen = false
    @State private var swaps = 0

    private enum Gaze: String, CaseIterable { case center, left = "-look-left", right = "-look-right", up = "-look-up" }

    private static let floor: CGFloat = 0.97
    private static let appear: TimeInterval = 0.6
    private func cut(_ change: () -> Void) {
        var t = Transaction()
        t.disablesAnimations = true
        withTransaction(t, change)
    }

    init(look: AgentLook, mood: AgentMood = .idle, size: CGFloat = 280, exact: Bool = false) {
        self.given = look
        self.mood = mood
        self.size = size
        self.exact = exact
        _appearAt = State(initialValue: mood == .appear ? Date() : nil)
    }

    private var hasFrames: Bool { AgentBody.named(look.body).hasOutfits }
    private var flatArt: FlatArt? { FlatArt.named(look.body) }
    private var hasFace: Bool { hasFrames || flatArt != nil }

    private var live: Bool { scenePhase == .active }
    private var breathes: Bool { size >= 80 && !reduceMotion }

    var body: some View {
        Group {
            if breathes || appearAt != nil {
                TimelineView(.animation(paused: !live)) { ctx in staged(pose(at: ctx.date)) }
            } else {
                staged(Pose())
            }
        }
        .frame(width: size, height: size)
        .onChange(of: look.picture) { _, _ in swaps += 1 }
        .onChange(of: mood) { _, m in
            if m == .appear { appearAt = Date() }
        }
        .task(id: Run(mood: mood, live: live)) { if live { await direct(mood) } }
        .accessibilityElement()
        .accessibilityLabel(look.description)
    }

    private struct Run: Equatable { let mood: AgentMood; let live: Bool }

    private func staged(_ pose: Pose) -> some View {
        ZStack {
            figure
                .scaleEffect(x: pose.sx, y: pose.sy, anchor: UnitPoint(x: 0.5, y: Self.floor))
        }
        .opacity(pose.opacity)
        .frame(width: size, height: size)
    }

    // MARK: Drawing

    private var face: String {
        guard hasFrames else { return "" }
        switch mood {
        case .happy: return "-happy"
        case .talking: return blinking ? "-happy" : (mouthOpen ? "-talk" : "")
        default: return blinking ? "-happy" : (gaze == .center ? "" : gaze.rawValue)
        }
    }

    private var figure: some View {
        ZStack {
            Group {
                if let art = flatArt {
                    FlatFigure(art: art, size: size, eyes: flatEyes)
                        .transaction { $0.animation = nil }
                } else {
                    frames(look.picture)
                }
            }
                .id(look.picture)
                .zIndex(Double(swaps))
                .transition(.asymmetric(insertion: .opacity.animation(.easeInOut(duration: 0.2)),
                                        removal: .opacity.animation(.easeIn(duration: 0.1).delay(0.2))))
        }
        .frame(width: size, height: size)
    }

    private var flatEyes: FlatEyesState {
        var e = FlatEyesState()
        e.closed = mood == .happy || blinking
        switch gaze {
        case .left: e.gaze = CGSize(width: -0.022, height: 0)
        case .right: e.gaze = CGSize(width: 0.022, height: 0)
        case .up: e.gaze = CGSize(width: 0, height: -0.02)
        case .center: break
        }
        if mood == .talking && mouthOpen { e.squash = 0.72 }
        return e
    }

    private func frames(_ base: String) -> some View {
        let faces = hasFrames ? ["", "-happy", "-talk"] + Gaze.allCases.filter { $0 != .center }.map(\.rawValue) : [""]
        return ZStack {
            ForEach(faces, id: \.self) { f in
                Image(base + f)
                    .resizable()
                    .interpolation(.high)
                    .frame(width: size, height: size)
                    .opacity(f.isEmpty || f == face ? 1 : 0)
                    .animation(nil, value: face)
            }
        }
    }

    // MARK: The face's timing

    private func direct(_ mood: AgentMood) async {
        cut { gaze = .center; mouthOpen = false; blinking = false }
        guard hasFace else { return }
        await withTaskGroup(of: Void.self) { group in
            if mood != .happy { group.addTask { await blinks() } }
            if mood == .talking {
                group.addTask { await talk() }
            } else if mood != .happy && !reduceMotion {
                group.addTask { await glances() }
            }
        }
    }

    private func pause(_ range: ClosedRange<Double>) async -> Bool {
        try? await Task.sleep(for: .seconds(Double.random(in: range)))
        return !Task.isCancelled
    }

    private func blinks() async {
        while await pause(3...6) {
            let times = Double.random(in: 0...1) < 0.2 ? 2 : 1
            for i in 0..<times {
                if i > 0, !(await pause(0.12...0.18)) { return }
                cut { blinking = true }
                try? await Task.sleep(for: .milliseconds(110))
                cut { blinking = false }
                if Task.isCancelled { return }
            }
        }
    }

    private func glances() async {
        while await pause(2.5...6) {
            var last = Gaze.center
            let count = Double.random(in: 0...1) < 0.3 ? 2 : 1
            for i in 0..<count {
                if i > 0, !(await pause(0.25...0.5)) { return }
                let to = [Gaze.left, .right, .up].filter { $0 != last }.randomElement()!
                last = to
                cut { gaze = to }
                guard await pause(0.8...1.8) else { return }
            }
            cut { gaze = .center }
        }
    }

    private func talk() async {
        while !Task.isCancelled {
            cut { mouthOpen = true }
            guard await pause(0.07...0.17) else { return }
            cut { mouthOpen = false }
            let gap = Double.random(in: 0...1) < 0.18 ? 0.22...0.42 : 0.05...0.15
            guard await pause(gap) else { return }
        }
    }

    // MARK: Motion

    private struct Pose {
        var opacity: Double = 1
        var sx: CGFloat = 1
        var sy: CGFloat = 1
    }

    private func pose(at date: Date) -> Pose {
        var p = Pose()
        if let a = appearAt, date.timeIntervalSince(a) < Self.appear {
            p.opacity = min(1, date.timeIntervalSince(a) / Self.appear)
        }
        if !breathes { return p }
        let breath = sin(date.timeIntervalSince(start) * 2 * .pi * 0.25)
        p.sy = 1 + 0.008 * breath
        p.sx = 1 - 0.003 * breath
        return p
    }
}

#Preview {
    AgentStage(look: .zero, mood: .appear)
}
