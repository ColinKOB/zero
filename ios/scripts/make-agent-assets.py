"""Placeholder agent art for the onboarding: cuts the concept stills out of their white backgrounds, downscales
them and writes them into the asset catalog.

The stills stand in until the Blender characters land (see docs/specs/2026-10-08-zero-release-onboarding-design.md).
The concept art is gitignored, so this reads it from a checkout that has it:

    python3 ios/scripts/make-agent-assets.py /Users/jdoe/Projects/taffy/docs/design/zip-concepts

Writes ios/Taffy/Resources/Assets.xcassets/Agent/ and ios/Taffy/Views/Agent/AgentArt.swift.
Needs Pillow and numpy, and macOS 14 or later for the subject masks (subject-mask.swift).

- Every plush body comes in outfits, each rendered whole (layered add-ons looked like stickers):
  <prefix>-<outfit><face>, where the prefix is "zero" for Zero's O and the body id for the rest, with faces "",
  -happy, -talk, -look-left, -look-right, -look-up. A body's outfits share one crop and scale, so it stays the
  same size when the outfit changes. Blinks use the -happy frame (upward arcs, Colin's call).
- Only the plain frame ("") of each outfit is a whole picture. The five expression frames are stored as overlays:
  the small area around the eyes and mouth where that frame differs from the plain one, soft at its edges and
  transparent everywhere else. AgentStage already stacks every frame over the plain one and cuts between them by
  opacity, so a face change is still a hard cut and looks the same as the whole frames did. (The separately rendered
  frames also differ in fur grain all over the body; the overlays keep one grain, so the fur no longer shimmers
  at a cut.) The script prints how closely each overlay on its base matches the original frame.
- addon-<name> are small icons for the outfit boxes.
"""

import json
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image
from scipy import ndimage

SIZE = 512            # every body sits in a SIZE x SIZE canvas, centered, feet on the same line
FILL = 0.9            # the picture's longer side, as a share of the canvas
FLOOR = 0.97          # where the feet touch, as a share of the canvas height
ICON = 160            # longest side of an outfit box icon
CHANGED = 40          # a pixel belongs to the face when a color channel differs from the plain frame by more than this
ALPHA_STEPS = 16      # the overlay's soft edge uses this many opacity levels, which keeps its palette small

HERE = Path(__file__).resolve().parent
IOS = HERE.parent
CATALOG = IOS / "Taffy" / "Resources" / "Assets.xcassets" / "Agent"
SWIFT = IOS / "Taffy" / "Views" / "Agent" / "AgentArt.swift"

BODIES = ["star", "moon", "bolt", "drop", "squircle", "pill", "hex", "gem", "cube", "mushroom", "bean",
          "arch", "flower", "dome", "leaf", "bell", "plus", "pebble", "gumdrop"]
FACES = ["", "-happy", "-talk", "-look-left", "-look-right", "-look-up"]
OUTFITS = ["plain", "classic", "cozy", "studio", "sporty"]
ICONS = ["glasses-round", "scarf", "beanie", "headphones", "cap", "glasses-square"]


def frame(src: Path, body: str, outfit: str, face: str) -> Path:
    if body == "o":
        if outfit == "plain":   # the original expression frames are the plain O
            return src / "frames" / f"{'base' if not face else face[1:]}.png"
        return src / "outfits" / "zero" / f"{outfit}{face}.png"
    return src / "outfits" / body / f"{outfit}{face}.png"


def prefix(body: str) -> str:
    return "zero" if body == "o" else body


def masks(pairs: list[tuple[Path, Path]]) -> None:
    args = [str(p) for pair in pairs for p in pair]
    subprocess.run(["swift", str(HERE / "subject-mask.swift"), *args], check=True, capture_output=True)


def imageset(name: str, img: Image.Image) -> None:
    d = CATALOG / f"{name}.imageset"
    d.mkdir(parents=True, exist_ok=True)
    # 256 colors with dithering: indistinguishable on the fur at this size, and about a fifth of the bytes.
    img.quantize(256, method=Image.Quantize.FASTOCTREE, dither=Image.Dither.FLOYDSTEINBERG).save(
        d / f"{name}.png", optimize=True)
    write_contents(d, name)


def write_contents(d: Path, name: str) -> None:
    (d / "Contents.json").write_text(json.dumps({
        "images": [{"filename": f"{name}.png", "idiom": "universal"}],
        "info": {"author": "xcode", "version": 1},
    }, indent=2) + "\n")


def disc(r: int) -> np.ndarray:
    y, x = np.ogrid[-r:r + 1, -r:r + 1]
    return x * x + y * y <= r * r


def overlay(base: Image.Image, face: Image.Image) -> tuple[Image.Image, float]:
    """The part of `face` that differs from `base` (the eyes and the mouth), as an RGBA picture the size of the
    canvas that is transparent elsewhere, plus the biggest color error of base-with-overlay against `face`.

    The region is every pixel that changed a lot, grown to join the eye and its highlights and to cover the old
    eye as well as the new one, then softened by a few pixels. In that soft ring the two frames differ only by fur
    grain, so the blend does not show."""
    b = np.asarray(base).astype(float)
    f = np.asarray(face).astype(float)
    core = np.abs(f[..., :3] - b[..., :3]).max(axis=2) > CHANGED
    core &= b[..., 3] >= 250                                   # never at the silhouette
    grown = ndimage.binary_dilation(core, disc(3))
    grown = ndimage.binary_fill_holes(ndimage.binary_closing(grown, disc(4)))
    labels, n = ndimage.label(grown)
    sizes = ndimage.sum(core, labels, range(1, n + 1))        # drop specks of grain that passed the threshold
    grown = np.isin(labels, [i + 1 for i, v in enumerate(sizes) if v >= 20])
    grown = ndimage.binary_dilation(grown, disc(4))
    a = ndimage.gaussian_filter(grown.astype(float), 2.0)
    a = np.where(b[..., 3] >= 250, a, 0)
    a = np.round(a * ALPHA_STEPS) / ALPHA_STEPS
    out = np.zeros(f.shape, dtype=np.uint8)
    out[..., :3] = np.where(a[..., None] > 0, f[..., :3], 0)
    out[..., 3] = np.round(a * 255)
    both = a[..., None] * f[..., :3] + (1 - a[..., None]) * b[..., :3]
    return Image.fromarray(out, "RGBA"), float(np.abs(both - f[..., :3]).max())


def place(rgb: Image.Image, mask: Image.Image, box: tuple[int, int, int, int]) -> Image.Image:
    """The cut-out picture, scaled and set on the shared canvas so every body stands on the same floor."""
    rgba = rgb.convert("RGB").copy()
    rgba.putalpha(mask)
    body = rgba.crop(box)
    scale = SIZE * FILL / max(body.size)
    body = body.resize((max(1, round(body.width * scale)), max(1, round(body.height * scale))), Image.LANCZOS)
    canvas = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    canvas.paste(body, ((SIZE - body.width) // 2, round(SIZE * FLOOR) - body.height), body)
    return canvas


def bbox(mask: Image.Image) -> tuple[int, int, int, int]:
    return mask.point(lambda v: 255 if v > 8 else 0).getbbox()


def color(img: Image.Image) -> str:
    """The body's main color: the average of its opaque, not-dark pixels (so the eyes don't count)."""
    a = np.asarray(img).astype(float)
    keep = (a[..., 3] > 200) & (a[..., :3].max(axis=2) > 60)
    r, g, b = (a[..., i][keep].mean() for i in range(3))
    return f"#{round(r):02X}{round(g):02X}{round(b):02X}"


def width(img: Image.Image) -> float:
    """The body's width at its widest, for the ground shadow."""
    cols = np.nonzero((np.asarray(img)[..., 3] > 128).any(axis=0))[0]
    return round(float(cols.max() - cols.min()) / SIZE, 4)


def main() -> None:
    src = Path(sys.argv[1] if len(sys.argv) > 1 else "/Users/jdoe/Projects/taffy/docs/design/zip-concepts")
    CATALOG.mkdir(parents=True, exist_ok=True)
    (CATALOG / "Contents.json").write_text(json.dumps({"info": {"author": "xcode", "version": 1}}, indent=2) + "\n")
    tmp = Path(tempfile.mkdtemp())
    widths, colors = {}, {}
    worst = 0.0   # the biggest color error of any overlay on its plain frame
    only = set(sys.argv[2].split(",")) if len(sys.argv) > 2 else None   # for trying out: just these bodies

    # Each body's outfits. One mask per outfit (from its base frame, used for all its faces so edges never
    # jitter), and one crop box for all of a body's outfits together.
    for b in ["o"] + BODIES:
        if only and b not in only:
            continue
        masks([(frame(src, b, o, ""), tmp / f"{b}-{o}.png") for o in OUTFITS])
        loaded = {o: Image.open(tmp / f"{b}-{o}.png").convert("L").resize((1024, 1024)) for o in OUTFITS}
        boxes = [bbox(m) for m in loaded.values()]
        union = (min(x[0] for x in boxes), min(x[1] for x in boxes), max(x[2] for x in boxes), max(x[3] for x in boxes))
        for o in OUTFITS:
            plain = None
            for face in FACES:
                rgb = Image.open(frame(src, b, o, face)).convert("RGB").resize((1024, 1024), Image.LANCZOS)
                img = place(rgb, loaded[o], union)
                if not face:
                    plain = img
                    if o == "plain":
                        widths[b] = width(img)
                        colors[b] = color(img)
                else:
                    img, err = overlay(plain, img)
                    worst = max(worst, err)
                imageset(f"{prefix(b)}-{o}{face}", img)
        print("outfits", b)

    # Icons for the outfit boxes: already transparent, trimmed to what is drawn.
    for name in ICONS:
        img = Image.open(src / "addons" / f"{name}.png").convert("RGBA")
        img = img.crop(bbox(img.getchannel("A")))
        img.thumbnail((ICON, ICON), Image.LANCZOS)
        imageset(f"addon-{name}", img)

    lines = ["// Generated by ios/scripts/make-agent-assets.py from the concept stills. Do not edit by hand.",
             "import CoreGraphics", "", "extension AgentBody {",
             "    /// Each body's width at its widest, as a share of its square picture (for the ground shadow).",
             "    static let widths: [String: CGFloat] = ["]
    lines += [f'        "{b}": {w},' for b, w in widths.items()]
    lines += ["    ]", "",
              "    /// Each body's main color, for giving a Zip that has only a color the closest body.",
              "    static let colors: [String: String] = ["]
    lines += [f'        "{b}": "{c}",' for b, c in colors.items()]
    lines += ["    ]", "}", ""]
    SWIFT.write_text("\n".join(lines))
    total = sum(p.stat().st_size for p in CATALOG.rglob("*.png"))
    print(f"wrote {len(BODIES) + 1} bodies x {len(OUTFITS)} outfits x {len(FACES)} faces, {len(ICONS)} icons, "
          f"{total / 1e6:.1f} MB; worst overlay color error {worst:.0f} of 255")


if __name__ == "__main__":
    main()
