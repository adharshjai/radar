"""Draw the Radar app icon (a radar sweep) and write an .icns."""
from __future__ import annotations

import math
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter


def draw(size: int) -> Image.Image:
    s = size
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    # rounded-square background, macOS style (about 22% radius), deep blue gradient
    pad = int(s * 0.06)
    r = int(s * 0.22)
    bg = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    bd = ImageDraw.Draw(bg)
    bd.rounded_rectangle((pad, pad, s - pad, s - pad), radius=r, fill=(19, 56, 120, 255))
    grad = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    gd = ImageDraw.Draw(grad)
    for y in range(s):
        t = y / s
        gd.line([(0, y), (s, y)], fill=(int(30 + 25 * (1 - t)), int(96 + 60 * (1 - t)), int(200 + 40 * (1 - t)), 255))
    bg = Image.composite(grad, bg, bg.split()[3])
    img.alpha_composite(bg)
    d = ImageDraw.Draw(img)
    cx = cy = s / 2
    R = s * 0.30
    # sweep wedge (soft)
    sweep = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    sd = ImageDraw.Draw(sweep)
    for i in range(60):
        a0 = -90 + i * 1.2
        alpha = int(150 * (i / 60))
        sd.pieslice((cx - R, cy - R, cx + R, cy + R), start=a0 - 50, end=a0 - 48.5, fill=(255, 255, 255, alpha))
    sweep = sweep.filter(ImageFilter.GaussianBlur(s * 0.006))
    img.alpha_composite(sweep)
    d = ImageDraw.Draw(img)
    w = max(2, int(s * 0.022))
    for k in (1.0, 0.66, 0.33):
        rr = R * k
        d.ellipse((cx - rr, cy - rr, cx + rr, cy + rr), outline=(255, 255, 255, 230), width=w)
    d.line([(cx - R, cy), (cx + R, cy)], fill=(255, 255, 255, 120), width=max(1, w // 2))
    d.line([(cx, cy - R), (cx, cy + R)], fill=(255, 255, 255, 120), width=max(1, w // 2))
    ang = math.radians(-90 - 48)
    d.line([(cx, cy), (cx + R * math.cos(ang), cy + R * math.sin(ang))], fill=(255, 255, 255, 255), width=w)
    # blips
    for (fx, fy, rad) in ((0.62, 0.38, 0.045), (0.40, 0.58, 0.032), (0.66, 0.60, 0.028)):
        bx, by, br = s * fx, s * fy, s * rad
        d.ellipse((bx - br, by - br, bx + br, by + br), fill=(255, 196, 61, 255))
    d.ellipse((cx - w * 1.6, cy - w * 1.6, cx + w * 1.6, cy + w * 1.6), fill=(255, 255, 255, 255))
    return img


def main(out: str):
    with tempfile.TemporaryDirectory() as td:
        iconset = Path(td) / "AppIcon.iconset"
        iconset.mkdir()
        base = draw(1024)
        for px, name in ((16, "16x16"), (32, "16x16@2x"), (32, "32x32"), (64, "32x32@2x"), (128, "128x128"), (256, "128x128@2x"),
                         (256, "256x256"), (512, "256x256@2x"), (512, "512x512"), (1024, "512x512@2x")):
            base.resize((px, px), Image.LANCZOS).save(iconset / f"icon_{name}.png")
        subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", out], check=True)
        base.resize((256, 256), Image.LANCZOS).save(Path(out).with_suffix(".png"))
    print("wrote", out)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "AppIcon.icns")
