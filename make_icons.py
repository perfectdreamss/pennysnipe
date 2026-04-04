"""Generate extension icons: 16, 48, 128px PNG files."""
from PIL import Image, ImageDraw
import os

OUT = os.path.join(os.path.dirname(__file__), "extension", "icons")
os.makedirs(OUT, exist_ok=True)

def make_icon(size):
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    pad = max(1, size // 10)
    # Dark background circle
    d.ellipse([0, 0, size - 1, size - 1], fill=(22, 22, 30, 255))
    # Green tag shape (rounded rect)
    inner = pad * 2
    d.rounded_rectangle(
        [inner, inner, size - inner, size - inner],
        radius=max(2, size // 8),
        fill=(74, 222, 128, 255),
    )
    # White % symbol
    cx, cy = size // 2, size // 2
    r = max(1, size // 9)
    dot = max(1, size // 14)
    # top-left dot
    d.ellipse([cx - r - dot, cy - r - dot, cx - r + dot, cy - r + dot], fill=(22, 22, 30, 255))
    # bottom-right dot
    d.ellipse([cx + r - dot, cy + r - dot, cx + r + dot, cy + r + dot], fill=(22, 22, 30, 255))
    # diagonal line
    lw = max(1, size // 16)
    d.line([cx - r, cy + r, cx + r, cy - r], fill=(22, 22, 30, 255), width=lw)
    return img

for size in (16, 48, 128):
    img = make_icon(size)
    path = os.path.join(OUT, f"icon{size}.png")
    img.save(path)
    print(f"  Saved {path}")

print("Icons generated.")
