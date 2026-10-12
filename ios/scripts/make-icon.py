#!/usr/bin/env python3
"""Zero's app icon: a sunflower yellow Ø on deep charcoal, drawn as exact geometry (Colin picked this design
on 2026-10-08 from the generated options; this redraw keeps its proportions with clean edges).
Writes the light, dark and tinted 1024 px icons into the asset catalog. Run: python3 ios/scripts/make-icon.py"""
from pathlib import Path
from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parents[1] / "Taffy/Resources/Assets.xcassets"
SS = 4                     # draw at 4x, then scale down for smooth edges
U = 1024 / 1254 * SS       # the measured design was on a 1254 px canvas

def P(x, y): return (x * U, y * U)

def draw(fg, bg, size=1024):
    im = Image.new("RGB", (size * SS, size * SS), bg)
    d = ImageDraw.Draw(im)
    # The ring: wider strokes at the sides than at the top and bottom, like a typeface.
    d.ellipse([*P(275, 272), *P(978, 970)], fill=fg)
    d.ellipse([*P(396, 367), *P(857, 883)], fill=bg)
    # The slash: an even band through the center, its ends cut level.
    half = 59
    d.polygon([P(937 - half, 232), P(937 + half, 232), P(309 + half, 986), P(309 - half, 986)], fill=fg)
    return im.resize((size, size), Image.LANCZOS)

YELLOW, CHARCOAL = (249, 196, 16), (24, 24, 27)
draw(YELLOW, CHARCOAL).save(OUT / "AppIcon.appiconset/icon-1024.png")
draw(YELLOW, (14, 14, 16)).save(OUT / "AppIcon.appiconset/icon-1024-dark.png")
draw((236, 236, 236), (0, 0, 0)).save(OUT / "AppIcon.appiconset/icon-1024-tinted.png")
draw(YELLOW, CHARCOAL).resize((180, 180), Image.LANCZOS).save(OUT / "IconPreview-Main.imageset/preview.png")
print("icons written")
