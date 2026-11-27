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
sys.path.insert(0, str(ROOT / "packaging"))

from icons import make_ico  # noqa: E402

BUILD = ROOT / "build"
DIST = ROOT / "dist"
NAME = "VideoDownloader"
EXE = ".exe" if sys.platform.startswith("win") else ""


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
    make_ico(icon)
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

    # ส่วนเสริมเบราว์เซอร์: วางไว้ข้างโปรแกรม (ผู้ใช้เลือกโฟลเดอร์นี้ตอน Load unpacked) + ทำไฟล์ zip แยก
    extension_src = ROOT / "browser-extension"
    shutil.copytree(extension_src, DIST / NAME / "extension")
    staged = BUILD / "VideoDownloader-Extension"
    shutil.copytree(extension_src, staged)
    zip_path = shutil.make_archive(str(DIST / "VideoDownloader-Extension"), "zip",
                                   root_dir=BUILD, base_dir=staged.name)
    print(f"ส่วนเสริมเบราว์เซอร์: {DIST / NAME / 'extension'}  และ  {zip_path}")
    total = sum(f.stat().st_size for f in (DIST / NAME).rglob("*") if f.is_file())
    print(f"\nเสร็จแล้ว: {exe}  (ทั้งโฟลเดอร์ {total / 1e6:.0f} MB)")


if __name__ == "__main__":
    main()
