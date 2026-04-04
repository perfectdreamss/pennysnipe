"""
make_icons.py — generates Pennysnipe icons (extension + favicon)
Design: copper penny with a sniper scope crosshair overlay.
Requires: pip install Pillow
"""

from PIL import Image, ImageDraw, ImageFont
import os, struct

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
EXT_DIR    = os.path.join(SCRIPT_DIR, "extension", "icons")
STATIC_DIR = os.path.join(SCRIPT_DIR, "static")
os.makedirs(EXT_DIR,    exist_ok=True)
os.makedirs(STATIC_DIR, exist_ok=True)

# ── Palette ────────────────────────────────────────────────────────────────────
PENNY_BASE  = (184, 115,  51, 255)   # copper
PENNY_LIGHT = (218, 158,  80, 255)   # highlight
PENNY_DARK  = ( 90,  50,  10, 255)   # shadow / engraving
SCOPE_WHITE = (255, 255, 255, 230)   # crosshair & rings
SCOPE_DIM   = (255, 255, 255, 100)   # inner ring (subtle)
BG          = ( 17,  19,  24, 255)   # app dark background


def draw_icon(size: int) -> Image.Image:
    img  = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    cx, cy = size / 2, size / 2

    # ── 1. Penny circle ────────────────────────────────────────────────────────
    pr = size * 0.38   # penny radius
    # soft drop shadow
    draw.ellipse(
        [cx - pr + size*0.03, cy - pr + size*0.04,
         cx + pr + size*0.03, cy + pr + size*0.04],
        fill=(0, 0, 0, 60)
    )
    # main penny body
    draw.ellipse([cx - pr, cy - pr, cx + pr, cy + pr], fill=PENNY_BASE)
    # highlight arc (top-left quadrant lighter)
    hl = pr * 0.75
    draw.ellipse([cx - hl, cy - hl, cx, cy], fill=PENNY_LIGHT)
    # re-draw penny on top to clip highlight
    draw.ellipse([cx - pr, cy - pr, cx + pr, cy + pr], fill=None,
                 outline=PENNY_BASE, width=max(1, int(size * 0.04)))

    # ── 2. "¢" engraving ──────────────────────────────────────────────────────
    if size >= 32:
        fs = max(8, int(size * 0.32))
        font = None
        for name in ("arialbd.ttf", "arial.ttf", "DejaVuSans-Bold.ttf",
                     "DejaVuSans.ttf"):
            try:
                font = ImageFont.truetype(name, fs)
                break
            except Exception:
                pass
        if font is None:
            font = ImageFont.load_default()
        text = "¢"
        bb   = draw.textbbox((0, 0), text, font=font)
        tw, th = bb[2] - bb[0], bb[3] - bb[1]
        draw.text(
            (cx - tw / 2 - bb[0], cy - th / 2 - bb[1] - size * 0.02),
            text, fill=PENNY_DARK, font=font
        )

    # ── 3. Sniper scope ────────────────────────────────────────────────────────
    lw      = max(1, int(size * 0.025))   # line width
    scope_r = size * 0.46                  # outer scope ring
    mid_r   = size * 0.30                  # mid ring
    gap     = size * 0.10                  # crosshair gap around centre

    # outer ring
    draw.ellipse(
        [cx - scope_r, cy - scope_r, cx + scope_r, cy + scope_r],
        outline=SCOPE_WHITE, width=lw
    )
    # mid ring (subtle)
    draw.ellipse(
        [cx - mid_r, cy - mid_r, cx + mid_r, cy + mid_r],
        outline=SCOPE_DIM, width=max(1, lw - 1)
    )

    # crosshair — four segments with a gap in the centre
    segs = [
        # horizontal left
        (cx - scope_r, cy, cx - gap, cy),
        # horizontal right
        (cx + gap,     cy, cx + scope_r, cy),
        # vertical top
        (cx, cy - scope_r, cx, cy - gap),
        # vertical bottom
        (cx, cy + gap,     cx, cy + scope_r),
    ]
    for x0, y0, x1, y1 in segs:
        draw.line([(x0, y0), (x1, y1)], fill=SCOPE_WHITE, width=lw)

    # small tick marks at the four compass points on the outer ring
    tick = size * 0.06
    for tx, ty, dx, dy in [
        (cx, cy - scope_r, 0, -tick),
        (cx, cy + scope_r, 0,  tick),
        (cx - scope_r, cy, -tick, 0),
        (cx + scope_r, cy,  tick, 0),
    ]:
        draw.line([(tx, ty), (tx + dx, ty + dy)], fill=SCOPE_WHITE, width=lw)

    return img


# ── Extension icons (PNG) ──────────────────────────────────────────────────────
for size in (16, 48, 128):
    path = os.path.join(EXT_DIR, f"icon{size}.png")
    draw_icon(size).save(path, format="PNG")
    print(f"  Saved {path}")

# ── Web favicons ───────────────────────────────────────────────────────────────
# favicon.ico (multi-resolution: 16, 32, 48)
ico_sizes = [16, 32, 48]
ico_imgs  = [draw_icon(s) for s in ico_sizes]
ico_path  = os.path.join(STATIC_DIR, "favicon.ico")
ico_imgs[0].save(
    ico_path, format="ICO",
    sizes=[(s, s) for s in ico_sizes],
    append_images=ico_imgs[1:]
)
print(f"  Saved {ico_path}")

# favicon-16x16.png, favicon-32x32.png, apple-touch-icon.png (180px)
for size, name in [(16, "favicon-16x16.png"), (32, "favicon-32x32.png"),
                   (180, "apple-touch-icon.png")]:
    # apple-touch-icon needs a solid background (no transparency)
    img = draw_icon(size)
    if name == "apple-touch-icon.png":
        bg = Image.new("RGBA", (size, size), BG)
        bg.paste(img, mask=img)
        img = bg.convert("RGB")
    path = os.path.join(STATIC_DIR, name)
    img.save(path, format="PNG")
    print(f"  Saved {path}")

print("\nAll icons generated.")
