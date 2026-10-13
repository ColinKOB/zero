"""Draws the Zero disk image's window background: "Drag Zero to Applications" over an arrow between the two icons.

Writes background.tiff (1x and 2x in one file, so Retina screens get the sharp one) beside this script. The arrow is
Tabler's arrow-narrow-right; the words use Geist, the website's font. Run again only to change the look:

    python3 ios/scripts/dmg/make-background.py <Geist-Variable.woff2> <tabler arrow-narrow-right.svg>

The icon positions in dmg-settings.py match the layout here (window 640 by 400, icons at x 170 and 470, y 215).
"""
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

W, H = 640, 400
INK = (26, 26, 26)
SOFT = (176, 176, 176)
HERE = Path(__file__).resolve().parent


def draw(scale: int, font_path: str, arrow_svg: str, tmp: Path) -> Path:
    img = Image.new("RGB", (W * scale, H * scale), "white")
    d = ImageDraw.Draw(img)
    font = ImageFont.truetype(font_path, 22 * scale)
    font.set_variation_by_name("SemiBold")
    text = "Drag Zero to Applications"
    box = d.textbbox((0, 0), text, font=font)
    d.text(((W * scale - (box[2] - box[0])) / 2, 78 * scale), text, font=font, fill=INK)
    # The arrow between the icons, recolored and drawn at this scale by rsvg-convert.
    size = 72 * scale
    svg = Path(arrow_svg).read_text().replace('stroke="currentColor"', 'stroke="#B0B0B0"')
    src = tmp / "arrow.svg"
    src.write_text(svg)
    png = tmp / f"arrow{scale}.png"
    subprocess.run(["rsvg-convert", "-w", str(size), "-h", str(size), "-o", str(png), str(src)], check=True)
    arrow = Image.open(png).convert("RGBA")
    img.paste(arrow, ((W * scale - size) // 2, 215 * scale - size // 2 - 8 * scale), arrow)
    out = tmp / f"bg{scale}.png"
    img.save(out, dpi=(72 * scale, 72 * scale))
    return out


def main() -> None:
    font_path, arrow_svg = sys.argv[1], sys.argv[2]
    with tempfile.TemporaryDirectory() as t:
        tmp = Path(t)
        one, two = draw(1, font_path, arrow_svg, tmp), draw(2, font_path, arrow_svg, tmp)
        out = HERE / "background.tiff"
        subprocess.run(["tiffutil", "-cathidpicheck", str(one), str(two), "-out", str(out)], check=True)
        print(f"Wrote {out}")


if __name__ == "__main__":
    main()
