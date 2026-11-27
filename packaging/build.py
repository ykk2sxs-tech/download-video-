"""สร้างโปรแกรม VideoDownloader แบบไม่ต้องติดตั้ง Python ด้วย PyInstaller

ใช้:   python packaging/build.py
ผลลัพธ์: dist/VideoDownloader/VideoDownloader(.exe)  (โฟลเดอร์นี้นำไปใช้ได้ทันที / ใช้ทำตัวติดตั้ง)

ต้องติดตั้งก่อน:  pip install -r requirements.txt -r packaging/requirements-build.txt
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUILD = ROOT / "build"
DIST = ROOT / "dist"
NAME = "VideoDownloader"
EXE = ".exe" if sys.platform.startswith("win") else ""


def make_icon(path: Path) -> None:
    """วาดไอคอน (สี่เหลี่ยมสีน้ำเงิน + ลูกศรดาวน์โหลด) เป็นไฟล์ .ico"""
    from PIL import Image, ImageDraw

    size = 256
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle([8, 8, 248, 248], radius=56, fill=(37, 99, 235, 255))
    white = (255, 255, 255, 255)
    width = 24
    strokes = [
        [(128, 52), (128, 158)],              # ก้านลูกศร
        [(82, 114), (128, 160), (174, 114)],  # หัวลูกศร
        [(68, 202), (188, 202)],              # เส้นฐาน
    ]
    for points in strokes:
        draw.line(points, fill=white, width=width, joint="curve")
        for x, y in (points[0], points[-1]):
            r = width // 2
            draw.ellipse([x - r, y - r, x + r, y + r], fill=white)
    img.save(path, sizes=[(s, s) for s in (16, 24, 32, 48, 64, 128, 256)])


def prepare_binaries(bin_dir: Path) -> list[Path]:
    """คัดลอก deno และ ffmpeg มาไว้ในชื่อมาตรฐาน เพื่อแนบไปกับโปรแกรม"""
    import deno
    import imageio_ffmpeg

    bin_dir.mkdir(parents=True, exist_ok=True)
    files = []
    for name, src in (("deno", deno.find_deno_bin()), ("ffmpeg", imageio_ffmpeg.get_ffmpeg_exe())):
        dst = bin_dir / (name + EXE)
        shutil.copy2(src, dst)
        dst.chmod(0o755)
        files.append(dst)
        print(f"  + {dst.name}: {dst.stat().st_size / 1e6:.0f} MB  (จาก {src})")
    return files


def main() -> None:
    import PyInstaller.__main__

    shutil.rmtree(BUILD, ignore_errors=True)
    shutil.rmtree(DIST / NAME, ignore_errors=True)
    BUILD.mkdir(parents=True)

    print("เตรียมไฟล์ประกอบ…")
    icon = BUILD / "icon.ico"
    make_icon(icon)
    binaries = prepare_binaries(BUILD / "bin")

    sep = os.pathsep
    args = [
        "--noconfirm",
        "--clean",
        "--onedir",
        "--console",  # หน้าต่างสีดำแสดงสถานะ ปิดหน้าต่าง = ปิดแอป
        "--name", NAME,
        "--icon", str(icon),
        "--distpath", str(DIST),
        "--workpath", str(BUILD / "pyinstaller"),
        "--specpath", str(BUILD),
        "--add-data", f"{ROOT / 'static'}{sep}static",
        # deno/ffmpeg แนบเป็นไฟล์ใน bin/ แล้ว ไม่ต้องเอาแพ็กเกจ Python ของมันมาซ้ำ (ประหยัด ~100 MB)
        "--exclude-module", "imageio_ffmpeg",
        "--exclude-module", "deno",
        "--exclude-module", "tkinter",
        "--exclude-module", "pytest",
    ]
    for binary in binaries:
        args += ["--add-binary", f"{binary}{sep}bin"]
    args.append(str(ROOT / "app.py"))

    print("กำลัง build ด้วย PyInstaller…")
    PyInstaller.__main__.run(args)

    exe = DIST / NAME / (NAME + EXE)
    if not exe.is_file():
        sys.exit(f"build ไม่สำเร็จ: ไม่พบ {exe}")
    total = sum(f.stat().st_size for f in (DIST / NAME).rglob("*") if f.is_file())
    print(f"\nเสร็จแล้ว: {exe}  (ทั้งโฟลเดอร์ {total / 1e6:.0f} MB)")


if __name__ == "__main__":
    main()
