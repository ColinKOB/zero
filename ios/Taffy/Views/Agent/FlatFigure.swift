import SwiftUI

struct FlatArt {
    let name: String
    let eyes: CGPoint
    let parts: [FlatPart]

    static func named(_ body: String) -> FlatArt? {
        body.hasPrefix("flat-") ? all[String(body.dropFirst(5))] : nil
    }
}

struct FlatPart {
    let color: String
    let outline: [CGFloat]
    let holes: [[CGFloat]]
}

struct FlatOutline: Shape {
    let part: FlatPart

    private static let tension: CGFloat = 0.36

    func path(in rect: CGRect) -> Path {
        var p = Path()
        Self.add(part.outline, to: &p, in: rect)
        for hole in part.holes { Self.add(hole, to: &p, in: rect) }
        return p
    }

    private static func add(_ flat: [CGFloat], to path: inout Path, in r: CGRect) {
        let n = flat.count / 2
        guard n > 2 else { return }
        func pt(_ i: Int) -> CGPoint {
            let k = ((i % n) + n) % n
            return CGPoint(x: r.minX + flat[2 * k] * r.width, y: r.minY + flat[2 * k + 1] * r.height)
        }
        path.move(to: pt(0))
        for i in 0..<n {
            let p0 = pt(i - 1), p1 = pt(i), p2 = pt(i + 1), p3 = pt(i + 2)
            let c1 = CGPoint(x: p1.x + (p2.x - p0.x) * tension / 3, y: p1.y + (p2.y - p0.y) * tension / 3)
            let c2 = CGPoint(x: p2.x - (p3.x - p1.x) * tension / 3, y: p2.y - (p3.y - p1.y) * tension / 3)
            path.addCurve(to: p2, control1: c1, control2: c2)
        }
        path.closeSubpath()
    }
}

struct FlatEyesState: Equatable {
    var closed = false
    var gaze = CGSize.zero
    var squash: CGFloat = 1
}

struct FlatFigure: View {
    let art: FlatArt
    let size: CGFloat
    var eyes = FlatEyesState()

    private var wantedScale: CGFloat { max(1, (2.4 + 0.065 * size) / (FlatArt.eyeSize.height * size)) }
    private var fit: FlatEyeFit.Result { FlatEyeFit.result(for: art, size: size, wanted: wantedScale) }
    private var eyeScale: CGFloat { fit.scale }

    var body: some View {
        ZStack(alignment: .topLeading) {
            ForEach(art.parts.indices, id: \.self) { i in
                FlatOutline(part: art.parts[i])
                    .fill(Color(hex: art.parts[i].color), style: FillStyle(eoFill: true, antialiased: true))
            }
            let at = fit.center
            ForEach([-1, 1], id: \.self) { side in
                eye
                    .position(x: (at.x + eyes.gaze.width + CGFloat(side) * FlatArt.eyeGap * eyeScale / 2) * size,
                              y: (at.y + eyes.gaze.height) * size)
            }
        }
        .frame(width: size, height: size)
    }

    @ViewBuilder private var eye: some View {
        let w = FlatArt.eyeSize.width * size * eyeScale, h = FlatArt.eyeSize.height * size * eyeScale
        if eyes.closed {
            UpwardArc()
                .stroke(Color.white, style: StrokeStyle(lineWidth: w * 0.85, lineCap: .round))
                .frame(width: w * 1.9, height: h * 0.42)
        } else {
            Capsule()
                .fill(Color.white)
                .frame(width: w, height: h * eyes.squash)
        }
    }
}

private struct UpwardArc: Shape {
    func path(in r: CGRect) -> Path {
        var p = Path()
        p.move(to: CGPoint(x: r.minX, y: r.maxY))
        p.addQuadCurve(to: CGPoint(x: r.maxX, y: r.maxY), control: CGPoint(x: r.midX, y: r.minY - r.height * 0.9))
        return p
    }
}

struct BodyThumb: View {
    let body_: AgentBody
    let size: CGFloat

    init(_ body: AgentBody, size: CGFloat) {
        self.body_ = body
        self.size = size
    }

    var body: some View {
        if let art = FlatArt.named(body_.id) {
            FlatFigure(art: art, size: size)
        } else {
            Image(body_.thumbnail).resizable().scaledToFit().frame(width: size, height: size)
        }
    }
}

@MainActor
enum FlatEyeFit {
    struct Result { let center: CGPoint; let scale: CGFloat }
    private static var cache: [String: Result] = [:]

    static func result(for art: FlatArt, size: CGFloat, wanted: CGFloat) -> Result {
        guard wanted > 1.01 else { return Result(center: art.eyes, scale: 1) }
        let key = "\(art.name)|\(Int((size * 4).rounded()))"
        if let r = cache[key] { return r }
        let r = search(art, size: size, wanted: wanted)
        cache[key] = r
        return r
    }

    private static func search(_ art: FlatArt, size: CGFloat, wanted: CGFloat) -> Result {
        let unit = CGRect(x: 0, y: 0, width: 1, height: 1)
        let shapes = art.parts.map { FlatOutline(part: $0).path(in: unit) }
        func inside(_ p: CGPoint) -> Bool { shapes.contains { $0.contains(p, eoFill: true) } }
        let room = max(0.8 / size, 0.01)
        var spots: [CGPoint] = []
        for dy in stride(from: -0.2, through: 0.3, by: 0.01) {
            for dx in stride(from: -0.3, through: 0.3, by: 0.01) {
                spots.append(CGPoint(x: art.eyes.x + dx, y: art.eyes.y + dy))
            }
        }
        spots.sort { hypot($0.x - art.eyes.x, $0.y - art.eyes.y) < hypot($1.x - art.eyes.x, $1.y - art.eyes.y) }
        func fitting(_ scale: CGFloat, _ candidates: [CGPoint]) -> CGPoint? {
            let w = FlatArt.eyeSize.width * scale, h = FlatArt.eyeSize.height * scale
            let half: CGFloat = FlatArt.eyeGap * scale / 2
            let edge: [CGPoint] = [
                CGPoint(x: 0, y: -h / 2 - room), CGPoint(x: 0, y: h / 2 + room),
                CGPoint(x: -w / 2 - room, y: 0), CGPoint(x: w / 2 + room, y: 0),
                CGPoint(x: -w / 2 - room * 0.7, y: -h / 2 + w / 2 - room * 0.7),
                CGPoint(x: w / 2 + room * 0.7, y: -h / 2 + w / 2 - room * 0.7),
                CGPoint(x: -w / 2 - room * 0.7, y: h / 2 - w / 2 + room * 0.7),
                CGPoint(x: w / 2 + room * 0.7, y: h / 2 - w / 2 + room * 0.7),
            ]
            return candidates.first { c in
                for side: CGFloat in [-1, 1] {
                    let x = c.x + side * half
                    for d in edge where !inside(CGPoint(x: x + d.x, y: c.y + d.y)) { return false }
                }
                return true
            }
        }
        for reach: CGFloat in [0.045, 0.09, 0.4] {
            let near = spots.filter { hypot($0.x - art.eyes.x, $0.y - art.eyes.y) <= reach }
            var scale = wanted
            while scale >= 1 {
                if let c = fitting(scale, near) { return Result(center: c, scale: scale) }
                scale -= 0.1
            }
        }
        return Result(center: art.eyes, scale: 1)
    }
}
