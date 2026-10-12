import SwiftUI

// Flat Zips: exact vector shapes (traced from the references by ios/scripts/make-flat-shapes.py into FlatArt.swift)
// with eyes drawn in code, so they are sharp at any size and need no pictures. No outline, texture, gradient or
// accessories. They move only through their eyes: blinks and the happy look are upward arcs, glances shift the
// eyes a little, and talking squashes them in rhythm (flat Zips have no mouth).

/// One flat body: its filled parts (most have one; the mushroom has a stem and a cap) and where its eyes sit.
struct FlatArt {
    let name: String
    let eyes: CGPoint
    let parts: [FlatPart]

    static func named(_ body: String) -> FlatArt? {
        body.hasPrefix("flat-") ? all[String(body.dropFirst(5))] : nil
    }
}

/// A filled region: outline and holes as flat x, y lists in shares of the square.
struct FlatPart {
    let color: String
    let outline: [CGFloat]
    let holes: [[CGFloat]]
}

/// A closed outline drawn as a smooth curve through the traced points (holes cut out with the even-odd rule).
struct FlatOutline: Shape {
    let part: FlatPart

    /// How far each curve reaches toward its neighbors: a little under a Catmull-Rom spline's 0.5, so tight corners
    /// (the plus, the gem) stay crisp instead of overshooting.
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

/// How a flat Zip's eyes look right now.
struct FlatEyesState: Equatable {
    var closed = false              // blinking or happy: upward arcs
    var gaze = CGSize.zero          // a glance, in shares of the square
    var squash: CGFloat = 1         // talking: the eyes squash a little in rhythm
}

/// A flat Zip at `size` points square: its shape and its eyes.
struct FlatFigure: View {
    let art: FlatArt
    let size: CGFloat
    var eyes = FlatEyesState()

    /// Small flat Zips get eyes a little larger than their drawn share, so a list row or header still has a
    /// readable face. The floor grows with the Zip (about 6 points tall at 56, 4.6 at 34, 3.6 at 18), so a smaller
    /// Zip still has smaller eyes (Colin, Oct 9: the list and the header looked the same).
    private var wantedScale: CGFloat { max(1, (2.4 + 0.065 * size) / (FlatArt.eyeSize.height * size)) }
    /// Where the eyes sit and how much they are enlarged, worked out once per body and size (FlatEyeFit).
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
            // An upward arc, the happy squint: the same width as an open eye's stroke, about as wide as two eyes.
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

/// A gentle upward curve (like ^, rounded), for closed happy eyes.
private struct UpwardArc: Shape {
    func path(in r: CGRect) -> Path {
        var p = Path()
        p.move(to: CGPoint(x: r.minX, y: r.maxY))
        p.addQuadCurve(to: CGPoint(x: r.maxX, y: r.maxY), control: CGPoint(x: r.midX, y: r.minY - r.height * 0.9))
        return p
    }
}

/// A body's small picture for the carousel: the plush picture, or the flat shape drawn.
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

/// Enlarged eyes at small sizes poked past the edge of bodies whose eyes sit near it (the bean's top in the chat
/// header, Oct 9). This finds the spot nearest the drawn eyes where both enlarged eyes fit inside the body with a
/// little room, and if none does, enlarges them a little less until they fit. Results are kept per body and size.
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
        let room = max(0.8 / size, 0.01)   // about a point of body around each eye
        // Spots near the drawn eyes first, nearest first.
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
        // Staying near the drawn spot matters more than size: a smaller pair of eyes in place reads better than a
        // big pair slid down the body (Colin, Oct 9: "too low"). So try a short move at every size first.
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
