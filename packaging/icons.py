"""วาดไอคอนของโปรแกรม (สี่เหลี่ยมสีน้ำเงิน + ลูกศรดาวน์โหลด)

สร้างไอคอนของส่วนเสริมเบราว์เซอร์ใหม่:  python packaging/icons.py
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXTENSION_ICON_SIZES = (16, 32, 48, 128)


def draw_icon(size: int = 256):
    from PIL import Image, ImageDraw

    big = 1024  # วาดใหญ่แล้วย่อ เพื่อให้ขอบเรียบ
    scale = big / 256
    img = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle([8 * scale, 8 * scale, 248 * scale, 248 * scale], radius=56 * scale,
                           fill=(37, 99, 235, 255))
    white = (255, 255, 255, 255)
    width = round(24 * scale)
    strokes = [
        [(128, 52), (128, 158)],              # ก้านลูกศร
        [(82, 114), (128, 160), (174, 114)],  # หัวลูกศร
        [(68, 202), (188, 202)],              # เส้นฐาน
    ]
    for points in strokes:
        pts = [(x * scale, y * scale) for x, y in points]
        draw.line(pts, fill=white, width=width, joint="curve")
        r = width / 2
        for x, y in (pts[0], pts[-1]):
            draw.ellipse([x - r, y - r, x + r, y + r], fill=white)
    return img.resize((size, size), Image.LANCZOS)


def make_ico(path: Path) -> None:
    draw_icon(256).save(path, sizes=[(s, s) for s in (16, 24, 32, 48, 64, 128, 256)])


def make_extension_icons(folder: Path = ROOT / "browser-extension" / "icons") -> None:
    folder.mkdir(parents=True, exist_ok=True)
    for size in EXTENSION_ICON_SIZES:
        draw_icon(size).save(folder / f"icon{size}.png", optimize=True)


if __name__ == "__main__":
    make_extension_icons()
    print("สร้างไอคอนส่วนเสริมแล้ว")
