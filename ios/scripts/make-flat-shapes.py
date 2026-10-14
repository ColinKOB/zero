"""Traces the flat Zip references into vector outlines for the app (ios/Taffy/Views/Agent/FlatArt.swift).

Each reference (docs/design/zip-concepts/flat/flat-<id>.png, gitignored) is a flat shape on white with two
white eye capsules. This cuts the shape out, follows its edge, resamples the edge into evenly spaced points
(SwiftUI draws smooth curves through them, so the shapes are sharp at any size), takes the flat color, and
finds where the eyes sit. Every body is framed like the plush pictures: the longer side is 90% of the square and
the feet touch 97% of the way down. The app draws the eyes itself, the same size on every body.

Fixes made here rather than in the references: the arch is drawn as a real rainbow arch (the reference came out
as a tombstone), the mushroom's eyes go on its red cap where white reads clearly, and the bolt and moon get the
same eye size as the rest (the app's eyes are one size everywhere).

    python3 -m venv /tmp/flat && /tmp/flat/bin/pip install opencv-python-headless numpy pillow
    /tmp/flat/bin/python ios/scripts/make-flat-shapes.py /Users/jdoe/Projects/taffy/docs/design/zip-concepts/flat
"""

import math
import sys
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

FILL, FLOOR = 0.9, 0.97
EYE_W, EYE_H, EYE_GAP = 0.044, 0.098, 0.092
OUT = Path(__file__).resolve().parent.parent / "Taffy" / "Views" / "Agent" / "FlatArt.swift"

BODIES = ["o", "star", "moon", "bolt", "drop", "pill", "hex", "gem", "squircle", "mushroom", "bean", "arch",
          "flower", "dome", "leaf", "bell", "plus", "pebble", "acorn", "kite",
          "book", "calendar", "briefcase", "coin", "suitcase", "receipt"]
NAMES = {"o": "O", "star": "Star", "moon": "Moon", "bolt": "Bolt", "drop": "Drop", "pill": "Pill", "hex": "Hexagon",
         "gem": "Gem", "squircle": "Square", "mushroom": "Mushroom", "bean": "Bean", "arch": "Arch",
         "flower": "Flower", "dome": "Dome", "leaf": "Leaf", "bell": "Bell", "plus": "Plus", "pebble": "Pebble",
         "acorn": "Acorn", "kite": "Kite", "book": "Book", "calendar": "Calendar", "briefcase": "Briefcase",
         "coin": "Coin", "suitcase": "Suitcase", "receipt": "Receipt"}
DETAILED = {"star", "flower", "bolt", "acorn", "leaf", "bell", "plus", "moon", "receipt", "calendar", "briefcase", "suitcase"}


def hexcolor(c) -> str:
    return "#%02X%02X%02X" % tuple(int(round(v)) for v in c[:3])


def smooth_mask(mask: np.ndarray) -> np.ndarray:
    m = cv2.GaussianBlur(mask.astype(np.float32), (0, 0), 3.0)
    return (m > 0.5).astype(np.uint8)


def resample(contour: np.ndarray, n: int) -> np.ndarray:
    """n points spaced evenly along a closed outline, starting at its topmost point."""
    pts = contour.reshape(-1, 2).astype(float)
    pts = np.roll(pts, -int(np.argmin(pts[:, 1] * 10000 + pts[:, 0])), axis=0)
    seg = np.linalg.norm(np.diff(np.vstack([pts, pts[:1]]), axis=0), axis=1)
    cum = np.concatenate([[0], np.cumsum(seg)])
    targets = np.linspace(0, cum[-1], n, endpoint=False)
    closed = np.vstack([pts, pts[:1]])
    xs = np.interp(targets, cum, closed[:, 0])
    ys = np.interp(targets, cum, closed[:, 1])
    out = np.stack([xs, ys], axis=1)
    if cv2.contourArea(out.astype(np.float32), oriented=True) < 0:
        out = out[::-1]
    return out


def fill_small_holes(mask: np.ndarray, frac: float) -> np.ndarray:
    """Fills the eyes (and any speck) before smoothing, so an eye near the edge can't dent the outline."""
    inv = (1 - mask).astype(np.uint8)
    num, lab, stats, _ = cv2.connectedComponentsWithStats(inv, 4)
    edge = set(lab[0, :]) | set(lab[-1, :]) | set(lab[:, 0]) | set(lab[:, -1])
    out = mask.copy()
    for i in range(1, num):
        if i not in edge and stats[i, cv2.CC_STAT_AREA] < frac * mask.sum():
            out[lab == i] = 1
    return out


def trace(mask: np.ndarray, n: int, min_hole: float):
    """The outer edge and any real holes (like the O's), skipping eye-sized holes."""
    contours, hier = cv2.findContours(smooth_mask(fill_small_holes(mask, min(min_hole, 0.04))),
                                      cv2.RETR_CCOMP, cv2.CHAIN_APPROX_NONE)
    hier = hier[0]
    outer_i = max((i for i in range(len(contours)) if hier[i][3] == -1), key=lambda i: cv2.contourArea(contours[i]))
    area = cv2.contourArea(contours[outer_i])
    holes = [resample(contours[i], n // 2) for i in range(len(contours))
             if hier[i][3] == outer_i and cv2.contourArea(contours[i]) > min_hole * area]
    return resample(contours[outer_i], n), holes


def eye_holes(mask: np.ndarray):
    """Centers of the two eye-shaped white holes inside the shape."""
    inv = (1 - mask).astype(np.uint8)
    num, lab, stats, cent = cv2.connectedComponentsWithStats(inv, 4)
    edge = set(lab[0, :]) | set(lab[-1, :]) | set(lab[:, 0]) | set(lab[:, -1])
    body = mask.sum()
    cands = [(stats[i, cv2.CC_STAT_AREA], cent[i]) for i in range(1, num)
             if i not in edge and 0.002 * body < stats[i, cv2.CC_STAT_AREA] < 0.03 * body
             and stats[i, cv2.CC_STAT_HEIGHT] > stats[i, cv2.CC_STAT_WIDTH]]
    cands.sort(key=lambda c: -c[0])
    if len(cands) < 2:
        raise SystemExit("could not find the eyes")
    (_, a), (_, b) = cands[:2]
    return (a + b) / 2


class Frame:
    """Maps reference pixels into the shared square."""

    def __init__(self, pts: np.ndarray):
        x0, y0 = pts.min(axis=0)
        x1, y1 = pts.max(axis=0)
        self.s = FILL / max(x1 - x0, y1 - y0)
        self.dx = 0.5 - (x0 + x1) / 2 * self.s
        self.dy = FLOOR - y1 * self.s

    def __call__(self, p):
        p = np.asarray(p, dtype=float)
        return np.stack([p[..., 0] * self.s + self.dx, p[..., 1] * self.s + self.dy], axis=-1)


def arch_parts():
    """A rainbow arch: a thick half ring on two short legs, with the opening at the bottom."""
    R, r, legs, cx = 0.45, 0.27, 0.10, 0.5
    cy = FLOOR - legs
    pts = []
    for k in range(121):
        a = math.pi + math.pi * k / 120
        pts.append((cx + R * math.cos(a), cy + R * math.sin(a)))
    pts.append((cx + R, FLOOR))
    pts.append((cx + r, FLOOR))
    for k in range(81):
        a = 2 * math.pi - math.pi * k / 80
        pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
    pts.append((cx - r, FLOOR))
    pts.append((cx - R, FLOOR))
    outline = densify(np.array(pts), 200)
    mid = (R + r) / 2
    ang = math.radians(-58)
    return [("#F812A2", outline, [])], (cx + mid * math.cos(ang), cy + mid * math.sin(ang))


def densify(pts: np.ndarray, n: int) -> np.ndarray:
    return resample(np.round(pts * 4000).astype(np.int32), n) / 4000


def flat_parts(src: Path, body: str):
    a = np.asarray(Image.open(src / f"flat-{body}.png").convert("RGB")).astype(int)
    mask = ((255 - a).max(axis=2) > 28).astype(np.uint8)
    n = 220 if body in DETAILED else 160
    if body == "mushroom":
        red = ((a[..., 0] > 150) & (a[..., 1] < 110) & (a[..., 2] < 110)).astype(np.uint8)
        stem = (mask & (1 - cv2.dilate(red, np.ones((9, 9), np.uint8)))).astype(np.uint8)
        cap_o, _ = trace(red, n, 1)
        stem_o, _ = trace(stem, 140, 1)
        frame = Frame(np.vstack([cap_o, stem_o]))
        cap_c = np.median(a[red.astype(bool)], axis=0)
        stem_c = np.median(a[stem.astype(bool)], axis=0)
        ys, xs = np.nonzero(red)
        eye = frame(np.array([xs.mean() + 0.05 * (xs.max() - xs.min()), ys.mean() + 0.08 * (ys.max() - ys.min())]))
        return [(hexcolor(stem_c), frame(stem_o), []), (hexcolor(cap_c), frame(cap_o), [])], tuple(eye)
    outline, holes = trace(mask, n, 0.04)
    frame = Frame(outline)
    color = np.median(a[mask.astype(bool)], axis=0)
    eye = frame(eye_holes(mask))
    return [(hexcolor(color), frame(outline), [frame(h) for h in holes])], tuple(eye)


def inside(parts, p) -> bool:
    for _, outline, holes in parts:
        if cv2.pointPolygonTest(outline.astype(np.float32), tuple(map(float, p)), False) > 0 and \
                not any(cv2.pointPolygonTest(h.astype(np.float32), tuple(map(float, p)), False) > 0 for h in holes):
            return True
    return False


def check_eyes(body, parts, eye):
    """Both eyes, with a little margin, sit on the shape."""
    ok = True
    for dx in (-EYE_GAP / 2, EYE_GAP / 2):
        cx, cy = eye[0] + dx, eye[1]
        for px, py in [(cx, cy - EYE_H * 0.6), (cx, cy + EYE_H * 0.6), (cx - EYE_W * 0.7, cy), (cx + EYE_W * 0.7, cy)]:
            if not inside(parts, (px, py)):
                ok = False
    print(f"{body:9s} eyes ({eye[0]:.3f}, {eye[1]:.3f}) {'fit' if ok else 'DO NOT FIT'}")


def swift_points(p: np.ndarray) -> str:
    return "[" + ", ".join(f"{x:.4f}, {y:.4f}" for x, y in p) + "]"


def main():
    src = Path(sys.argv[1] if len(sys.argv) > 1 else "/Users/jdoe/Projects/taffy/docs/design/zip-concepts/flat")
    lines = ["// Generated by ios/scripts/make-flat-shapes.py from the flat Zip references. Do not edit by hand.",
             "import CoreGraphics", "", "extension FlatArt {",
             "    /// Each flat body: its filled parts (color, outline, holes) and where the eyes sit, in shares of the square.",
             "    static let all: [String: FlatArt] = ["]
    for body in BODIES:
        parts, eye = arch_parts() if body == "arch" else flat_parts(src, body)
        check_eyes(body, parts, eye)
        lines.append(f'        "{body}": FlatArt(name: "{NAMES[body]}", eyes: CGPoint(x: {eye[0]:.4f}, y: {eye[1]:.4f}), parts: [')
        for color, outline, holes in parts:
            hs = "[" + ", ".join(swift_points(h) for h in holes) + "]"
            lines.append(f'            FlatPart(color: "{color}", outline: {swift_points(outline)}, holes: {hs}),')
        lines.append("        ]),")
    lines += ["    ]", "", f"    static let eyeSize = CGSize(width: {EYE_W}, height: {EYE_H})",
              f"    static let eyeGap: CGFloat = {EYE_GAP}", "}", ""]
    OUT.write_text("\n".join(lines))
    print(f"wrote {OUT} ({OUT.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
