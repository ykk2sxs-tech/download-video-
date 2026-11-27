"""แอปดาวน์โหลดวิดีโอจาก YouTube / Facebook / Zoom ด้วยการวางลิงก์

รันด้วย:  python app.py   แล้วเปิด http://127.0.0.1:8765
ใช้ yt-dlp เป็นตัวดาวน์โหลด และ ffmpeg สำหรับรวมไฟล์ภาพ+เสียง / แปลง MP3
"""

from __future__ import annotations

import gc
import logging
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import uuid
import webbrowser
from pathlib import Path
from urllib.parse import urlparse

from flask import Flask, abort, jsonify, request, send_file, send_from_directory

import yt_dlp
from yt_dlp.utils import DownloadCancelled

# FROZEN = รันจากไฟล์ .exe ที่สร้างด้วย PyInstaller (ดู packaging/)
FROZEN = bool(getattr(sys, "frozen", False))
BASE_DIR = Path(__file__).resolve().parent
RESOURCE_DIR = Path(getattr(sys, "_MEIPASS", BASE_DIR))  # ที่อยู่ static/ และ bin/ ภายใน .exe
EXE_SUFFIX = ".exe" if sys.platform.startswith("win") else ""


def user_downloads_folder() -> Path:
    """โฟลเดอร์ Downloads ของผู้ใช้ (รองรับกรณีย้าย Downloads ไปไดรฟ์อื่น/OneDrive บน Windows)"""
    if sys.platform.startswith("win"):
        try:
            import ctypes
            from ctypes import wintypes

            class GUID(ctypes.Structure):
                _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                            ("Data3", wintypes.WORD), ("Data4", ctypes.c_ubyte * 8)]

            # FOLDERID_Downloads {374DE290-123F-4565-9164-39C4925E467B}
            folder_id = GUID(0x374DE290, 0x123F, 0x4565,
                             (ctypes.c_ubyte * 8)(0x91, 0x64, 0x39, 0xC4, 0x92, 0x5E, 0x46, 0x7B))
            path_ptr = ctypes.c_wchar_p()
            if ctypes.windll.shell32.SHGetKnownFolderPath(
                    ctypes.byref(folder_id), 0, None, ctypes.byref(path_ptr)) == 0:
                folder = Path(path_ptr.value)
                ctypes.windll.ole32.CoTaskMemFree(path_ptr)
                return folder
        except Exception:
            pass
    return Path.home() / "Downloads"


DEFAULT_DOWNLOAD_DIR = user_downloads_folder() / "VideoDownloader" if FROZEN else BASE_DIR / "downloads"
DOWNLOAD_DIR = Path(os.environ.get("DOWNLOAD_DIR", DEFAULT_DOWNLOAD_DIR)).resolve()
TEMP_DIR = DOWNLOAD_DIR / ".tmp"
HOST = os.environ.get("HOST", "127.0.0.1")
PORT = int(os.environ.get("PORT", "8765"))
MAX_PARALLEL_DOWNLOADS = 2

PLATFORMS = {
    "youtube": ("YouTube", ("youtube.com", "youtu.be", "youtube-nocookie.com")),
    "facebook": ("Facebook", ("facebook.com", "fb.watch", "fb.com")),
    "zoom": ("Zoom", ("zoom.us", "zoom.com")),
}

QUALITY_LABELS = {
    "best": "คุณภาพสูงสุด",
    "1080": "1080p",
    "720": "720p",
    "480": "480p",
    "audio": "เสียงอย่างเดียว (MP3)",
}

SUPPORTED_BROWSERS = {"chrome", "edge", "firefox", "brave", "opera", "safari", "chromium", "vivaldi"}

URL_RE = re.compile(r"https?://[^\s<>\"'`]+", re.IGNORECASE)
BARE_URL_RE = re.compile(
    r"(?:(?:www|m|web|mobile)\.)?"
    r"(?:youtube\.com|youtu\.be|facebook\.com|fb\.watch|[\w-]+\.zoom\.us|zoom\.us)/[^\s<>\"'`]+",
    re.IGNORECASE,
)
ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


# ---------------------------------------------------------------------------
# ตัวช่วยทั่วไป
# ---------------------------------------------------------------------------

def extract_url(text: str | None) -> str | None:
    """ดึงลิงก์แรกจากข้อความที่วาง (รองรับกรณีคัดลอกมาพร้อมข้อความอื่น)"""
    if not text:
        return None
    text = text.strip()
    match = URL_RE.search(text)
    if match:
        url = match.group(0)
    else:
        match = BARE_URL_RE.search(text)
        if not match:
            return None
        url = "https://" + match.group(0)
    return url.rstrip(".,;:!?)]}>ฯ")


def detect_platform(url: str) -> str | None:
    host = (urlparse(url).hostname or "").lower()
    for key, (_, domains) in PLATFORMS.items():
        if any(host == d or host.endswith("." + d) for d in domains):
            return key
    return None


def bundled_binary(name: str) -> str | None:
    """โปรแกรมที่แนบมากับไฟล์ .exe (ffmpeg, deno) อยู่ในโฟลเดอร์ bin/"""
    path = RESOURCE_DIR / "bin" / (name + EXE_SUFFIX)
    return str(path) if path.is_file() else None


def find_ffmpeg() -> str | None:
    """ใช้ ffmpeg ที่แนบมากับ .exe ก่อน, ต่อด้วยตัวในเครื่อง, สุดท้ายตัวจากแพ็กเกจ imageio-ffmpeg"""
    bundled = bundled_binary("ffmpeg")
    if bundled:
        return bundled
    system_ffmpeg = shutil.which("ffmpeg")
    if system_ffmpeg:
        return system_ffmpeg
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


def find_js_runtimes() -> dict:
    """YouTube ต้องใช้ JavaScript runtime (deno/node) ในการถอดรหัสลิงก์วิดีโอ"""
    runtimes: dict = {}
    deno_path = bundled_binary("deno")
    if not deno_path:
        try:
            import deno

            deno_path = deno.find_deno_bin()
        except Exception:
            deno_path = shutil.which("deno")
    if deno_path:
        runtimes["deno"] = {"path": str(deno_path)}
    if shutil.which("node"):
        runtimes["node"] = {}
    return runtimes or {"deno": {}}


FFMPEG_PATH = find_ffmpeg()
JS_RUNTIMES = find_js_runtimes()


def build_format(quality: str) -> str:
    if quality == "audio":
        return "ba/b"
    if quality == "best":
        return "bv*+ba/b"
    height = int(quality)
    # เลือก H.264 + AAC ก่อน เพื่อให้เปิดได้ทุกเครื่อง (Windows Media Player, มือถือ, PowerPoint)
    return (
        f"bv*[height<=?{height}][vcodec^=avc1]+ba[ext=m4a]/"
        f"bv*[height<=?{height}]+ba/"
        f"b[height<=?{height}]/b"
    )


def clean_error(message: str) -> str:
    message = ANSI_RE.sub("", str(message)).strip()
    return re.sub(r"^ERROR:\s*", "", message)


def friendly_error(message: str, platform: str | None) -> str:
    """แปลข้อความ error ของ yt-dlp เป็นภาษาไทยที่เข้าใจง่าย"""
    raw = clean_error(message)
    low = raw.lower()
    if ("wrong" in low or "incorrect" in low or "invalid" in low) and ("passcode" in low or "password" in low):
        hint = "รหัสผ่าน Zoom ไม่ถูกต้อง — ตรวจสอบตัวพิมพ์เล็ก/ใหญ่แล้วลองอีกครั้ง"
    elif "passcode" in low or "video-password" in low or ("password" in low and platform == "zoom"):
        hint = "วิดีโอ Zoom นี้มีรหัสผ่าน (Passcode) — กรุณาใส่รหัสในช่อง \"รหัสผ่าน Zoom\" แล้วลองอีกครั้ง"
    elif "unsupported url" in low:
        hint = "ไม่รองรับลิงก์นี้ — ตรวจสอบว่าเป็นลิงก์วิดีโอจาก YouTube, Facebook หรือ Zoom"
    elif "not a bot" in low or "sign in to confirm" in low:
        hint = ("YouTube ขอให้ยืนยันตัวตน — เปิด \"ตัวเลือกเพิ่มเติม\" แล้วเลือกใช้ cookies "
                "จากเบราว์เซอร์ที่ล็อกอิน YouTube ไว้")
    elif "could not find" in low and "cookie" in low:
        hint = ("ไม่พบข้อมูลการล็อกอินของเบราว์เซอร์ที่เลือกในเครื่องนี้ — ตรวจสอบว่าติดตั้งและล็อกอินไว้แล้ว "
                "หรือเลือกเบราว์เซอร์อื่น")
    elif "cookie" in low and ("could not" in low or "failed" in low or "dpapi" in low or "decrypt" in low):
        hint = ("อ่าน cookies จากเบราว์เซอร์ไม่ได้ — ลองปิดเบราว์เซอร์นั้นก่อน หรือเปลี่ยนไปใช้ Firefox")
    elif any(k in low for k in ("private", "login", "log in", "logged-in", "registered users",
                                "cookies", "permission", "members only", "only available to")):
        hint = ("วิดีโอนี้เป็นแบบส่วนตัวหรือต้องล็อกอิน — เปิด \"ตัวเลือกเพิ่มเติม\" แล้วเลือก "
                "เบราว์เซอร์ที่ล็อกอินบัญชีไว้ เพื่อใช้ cookies")
    elif "video unavailable" in low or "removed" in low or "does not exist" in low or "404" in low:
        hint = "ไม่พบวิดีโอ หรือวิดีโอถูกลบ/ปิดการเข้าถึงแล้ว"
    elif "ffmpeg" in low:
        hint = "ไม่พบโปรแกรม ffmpeg — รัน start.bat / start.sh ใหม่เพื่อติดตั้งอีกครั้ง"
    elif "timed out" in low or "connection" in low or "network" in low or "getaddrinfo" in low:
        hint = "เชื่อมต่ออินเทอร์เน็ตไม่ได้ หรือเว็บปลายทางตอบช้า — ลองใหม่อีกครั้ง"
    else:
        hint = "ดาวน์โหลดไม่สำเร็จ"
    return f"{hint}\n\nรายละเอียด: {raw[:500]}"


class QuietLogger:
    def debug(self, msg):
        pass

    def info(self, msg):
        pass

    def warning(self, msg):
        print("[yt-dlp] WARNING:", clean_error(msg), file=sys.stderr)

    def error(self, msg):
        print("[yt-dlp]", clean_error(msg), file=sys.stderr)


def base_ydl_opts(password: str | None = None, cookies_browser: str | None = None) -> dict:
    opts = {
        "logger": QuietLogger(),
        "noplaylist": True,
        "noprogress": True,
        "js_runtimes": JS_RUNTIMES,
        "socket_timeout": 30,
        "retries": 5,
        "fragment_retries": 5,
    }
    if FFMPEG_PATH:
        opts["ffmpeg_location"] = FFMPEG_PATH
    if password:
        opts["videopassword"] = password
    if cookies_browser:
        opts["cookiesfrombrowser"] = (cookies_browser,)
    return opts


def read_common_params(data: dict) -> tuple[str, str | None, str | None]:
    url = extract_url(data.get("url"))
    if not url:
        raise ValueError("ไม่พบลิงก์ในข้อความที่วาง — กรุณาคัดลอกลิงก์วิดีโอมาวางอีกครั้ง")
    password = (data.get("password") or "").strip() or None
    browser = (data.get("cookies_browser") or "").strip().lower() or None
    if browser and browser not in SUPPORTED_BROWSERS:
        raise ValueError("ไม่รองรับเบราว์เซอร์นี้")
    return url, password, browser


# ---------------------------------------------------------------------------
# งานดาวน์โหลด (รันใน background thread)
# ---------------------------------------------------------------------------

jobs: dict[str, dict] = {}
jobs_lock = threading.Lock()
download_slots = threading.Semaphore(MAX_PARALLEL_DOWNLOADS)


def remove_dir(path: Path) -> None:
    """ลบโฟลเดอร์ชั่วคราว (บน Windows ไฟล์ที่เพิ่งถูกยกเลิกอาจยังถูกเปิดค้างอยู่ชั่วครู่ จึงลองซ้ำ)"""
    for _ in range(10):
        shutil.rmtree(path, ignore_errors=True)
        if not path.exists():
            return
        gc.collect()
        time.sleep(0.3)


def public_job(job: dict) -> dict:
    return {k: v for k, v in job.items() if not k.startswith("_")}


def run_download(job_id: str, url: str, quality: str, password: str | None, browser: str | None):
    job = jobs[job_id]
    platform = detect_platform(url)
    job_tmp = TEMP_DIR / job_id

    def progress_hook(d):
        if job["_cancel"]:
            raise DownloadCancelled("ผู้ใช้ยกเลิก")
        info = d.get("info_dict") or {}
        total_parts = len(info.get("requested_formats") or []) or 1
        if d["status"] == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            done = d.get("downloaded_bytes") or 0
            frac = (done / total) if total else 0
            if not total and d.get("fragment_count"):
                frac = (d.get("fragment_index") or 0) / d["fragment_count"]
            overall = (job["_parts_done"] + min(frac, 1)) / total_parts
            job.update(
                status="downloading",
                percent=round(min(overall, 0.99) * 100, 1),
                speed=d.get("speed"),
                eta=d.get("eta"),
                downloaded=done,
                total=total,
                message=(f"กำลังดาวน์โหลด (ส่วนที่ {job['_parts_done'] + 1}/{total_parts})"
                         if total_parts > 1 else "กำลังดาวน์โหลด"),
            )
        elif d["status"] == "finished":
            job["_parts_done"] += 1

    def postprocessor_hook(d):
        if d["status"] != "started":
            return
        name = d.get("postprocessor", "")
        if name == "Merger":
            job.update(status="processing", message="กำลังรวมไฟล์ภาพและเสียง…", percent=99)
        elif name == "ExtractAudio":
            job.update(status="processing", message="กำลังแปลงเป็น MP3…", percent=99)

    opts = base_ydl_opts(password, browser)
    opts.update(
        format=build_format(quality),
        paths={"home": str(DOWNLOAD_DIR), "temp": str(job_tmp)},
        outtmpl="%(title).150B [%(id)s]%(height& {}p|)s.%(ext)s",
        progress_hooks=[progress_hook],
        postprocessor_hooks=[postprocessor_hook],
        windowsfilenames=True,
        continuedl=True,
    )
    if quality == "audio":
        opts["postprocessors"] = [
            {"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "192"}
        ]
    else:
        opts["merge_output_format"] = "mp4"

    job.update(status="queued", message="รอคิวดาวน์โหลด…")
    with download_slots:
        if job["_cancel"]:
            job.update(status="cancelled", message="ยกเลิกแล้ว")
            return
        job.update(status="starting", message="กำลังเตรียมดาวน์โหลด…")
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=True)
            downloads = info.get("requested_downloads") or [{}]
            filepath = downloads[0].get("filepath") or info.get("filepath")
            if not filepath or not Path(filepath).exists():
                raise RuntimeError("ดาวน์โหลดเสร็จแต่ไม่พบไฟล์ผลลัพธ์")
            path = Path(filepath)
            job.update(
                status="done",
                percent=100,
                message="ดาวน์โหลดเสร็จแล้ว",
                filename=path.name,
                filesize=path.stat().st_size,
                title=info.get("title") or job.get("title"),
                _filepath=str(path),
            )
        except DownloadCancelled:
            job.update(status="cancelled", message="ยกเลิกแล้ว")
        except Exception as exc:  # noqa: BLE001 - ต้องแสดงทุก error ให้ผู้ใช้เห็น
            if job["_cancel"]:
                job.update(status="cancelled", message="ยกเลิกแล้ว")
            else:
                job.update(status="error", message="ดาวน์โหลดไม่สำเร็จ",
                           error=friendly_error(str(exc), platform))
        finally:
            remove_dir(job_tmp)
            job["finished_at"] = time.time()


# ---------------------------------------------------------------------------
# Flask app
# ---------------------------------------------------------------------------

app = Flask(__name__, static_folder=str(RESOURCE_DIR / "static"), static_url_path="/static")
app.json.ensure_ascii = False

LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


@app.before_request
def only_local_requests():
    """ป้องกันเว็บอื่นแอบสั่งงานแอปผ่านเบราว์เซอร์ (DNS rebinding)"""
    if HOST not in LOOPBACK_HOSTS:
        return None  # ผู้ใช้ตั้งใจเปิดให้เครื่องอื่นในวง LAN ใช้
    host = request.host or ""
    hostname = host[1:].split("]")[0] if host.startswith("[") else host.rsplit(":", 1)[0]
    if hostname.lower() not in LOOPBACK_HOSTS:
        abort(403)
    return None


def json_body() -> dict:
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        abort(400, description="ต้องส่งข้อมูลเป็น JSON")
    return data


@app.get("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


def youtube_solver_available() -> bool:
    """สคริปต์ถอดรหัส YouTube (yt-dlp-ejs) ต้องอยู่ครบ โดยเฉพาะในไฟล์ .exe"""
    try:
        import yt_dlp_ejs.yt.solver as solver

        return len(solver.core()) > 1000 and len(solver.lib()) > 1000
    except Exception:
        return False


@app.get("/api/status")
def status():
    return jsonify(
        ok=True,
        yt_dlp_version=yt_dlp.version.__version__,
        ffmpeg=bool(FFMPEG_PATH),
        js_runtimes=sorted(JS_RUNTIMES),
        download_dir=str(DOWNLOAD_DIR),
        frozen=FROZEN,
        tools={
            "ffmpeg": FFMPEG_PATH,
            "deno": (JS_RUNTIMES.get("deno") or {}).get("path"),
            "youtube_solver": youtube_solver_available(),
        },
    )


@app.post("/api/info")
def video_info():
    data = json_body()
    try:
        url, password, browser = read_common_params(data)
    except ValueError as exc:
        return jsonify(error=str(exc)), 400
    platform = detect_platform(url)
    opts = base_ydl_opts(password, browser)
    opts["skip_download"] = True
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as exc:  # noqa: BLE001
        return jsonify(error=friendly_error(str(exc), platform), url=url, platform=platform), 422

    if info.get("_type") == "playlist":
        entries = [e for e in (info.get("entries") or []) if e]
        if not entries:
            return jsonify(error="ไม่พบวิดีโอในลิงก์นี้", url=url), 422
        info = entries[0]

    heights = sorted(
        {f["height"] for f in info.get("formats") or []
         if f.get("height") and f.get("vcodec") != "none"},
        reverse=True,
    )
    if not heights and info.get("height"):
        heights = [info["height"]]
    return jsonify(
        url=url,
        platform=platform,
        platform_name=PLATFORMS[platform][0] if platform else "เว็บไซต์อื่น",
        title=info.get("title") or "(ไม่มีชื่อ)",
        uploader=info.get("uploader") or info.get("channel") or "",
        duration=info.get("duration"),
        thumbnail=info.get("thumbnail"),
        heights=heights,
        is_live=bool(info.get("is_live")),
    )


@app.post("/api/download")
def start_download():
    data = json_body()
    try:
        url, password, browser = read_common_params(data)
    except ValueError as exc:
        return jsonify(error=str(exc)), 400
    quality = str(data.get("quality") or "1080")
    if quality not in QUALITY_LABELS:
        return jsonify(error="ตัวเลือกคุณภาพไม่ถูกต้อง"), 400

    job_id = uuid.uuid4().hex[:12]
    job = {
        "id": job_id,
        "url": url,
        "platform": detect_platform(url),
        "title": (data.get("title") or "")[:300],
        "quality": quality,
        "quality_label": QUALITY_LABELS[quality],
        "status": "queued",
        "percent": 0,
        "message": "รอคิวดาวน์โหลด…",
        "created_at": time.time(),
        "_cancel": False,
        "_parts_done": 0,
    }
    with jobs_lock:
        jobs[job_id] = job
    threading.Thread(
        target=run_download, args=(job_id, url, quality, password, browser), daemon=True
    ).start()
    return jsonify(public_job(job)), 202


@app.get("/api/jobs")
def list_jobs():
    with jobs_lock:
        items = sorted(jobs.values(), key=lambda j: j["created_at"], reverse=True)
        return jsonify([public_job(j) for j in items])


@app.get("/api/jobs/<job_id>")
def job_status(job_id):
    job = jobs.get(job_id)
    if not job:
        abort(404)
    return jsonify(public_job(job))


@app.post("/api/jobs/<job_id>/cancel")
def cancel_job(job_id):
    job = jobs.get(job_id)
    if not job:
        abort(404)
    if job["status"] not in ("done", "error", "cancelled"):
        job["_cancel"] = True
        job["message"] = "กำลังยกเลิก…"
    return jsonify(public_job(job))


@app.get("/api/jobs/<job_id>/file")
def job_file(job_id):
    job = jobs.get(job_id)
    if not job or job.get("status") != "done":
        abort(404)
    path = Path(job["_filepath"]).resolve()
    if DOWNLOAD_DIR not in path.parents or not path.is_file():
        abort(404)
    return send_file(path, as_attachment=True, download_name=path.name)


@app.post("/api/open-folder")
def open_folder():
    """เปิดโฟลเดอร์ downloads ในเครื่อง (ใช้ได้เมื่อรันแอปบนเครื่องตัวเอง)"""
    if HOST not in LOOPBACK_HOSTS:
        return jsonify(error="ใช้ได้เฉพาะเมื่อเปิดแอปบนเครื่องตัวเอง"), 403
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    try:
        if sys.platform.startswith("win"):
            os.startfile(DOWNLOAD_DIR)  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(DOWNLOAD_DIR)])
        else:
            subprocess.Popen(["xdg-open", str(DOWNLOAD_DIR)])
    except Exception as exc:  # noqa: BLE001
        return jsonify(error=f"เปิดโฟลเดอร์ไม่ได้: {exc}", path=str(DOWNLOAD_DIR)), 500
    return jsonify(ok=True, path=str(DOWNLOAD_DIR))


@app.errorhandler(400)
@app.errorhandler(403)
@app.errorhandler(404)
def http_error(err):
    return jsonify(error=getattr(err, "description", str(err))), err.code


def setup_console():
    # กันโปรแกรมล่มเมื่อพิมพ์ภาษาไทยในหน้าต่างที่ไม่รองรับ UTF-8
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    if sys.platform.startswith("win"):
        try:
            import ctypes

            ctypes.windll.kernel32.SetConsoleTitleW("Video Downloader - โหลดวิดีโอ")
        except Exception:
            pass


def port_in_use(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET6 if ":" in host else socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind((host, port))
        except OSError:
            return True
    return False


def already_running(url: str) -> bool:
    """ตรวจว่ามีแอปนี้เปิดอยู่แล้วหรือไม่ (เช่น ดับเบิลคลิกซ้ำ)"""
    try:
        with urllib.request.urlopen(f"{url}/api/status", timeout=3) as res:
            return b'"yt_dlp_version"' in res.read()
    except Exception:
        return False


def open_browser_enabled() -> bool:
    return "--no-browser" not in sys.argv and not os.environ.get("NO_BROWSER")


def pause_before_exit():
    """ใน .exe หน้าต่างจะปิดทันทีถ้าไม่หยุดรอ ผู้ใช้จะไม่เห็นข้อความ error"""
    if FROZEN and sys.stdin and sys.stdin.isatty():
        try:
            input("\nกด Enter เพื่อปิดหน้าต่างนี้…")
        except Exception:
            pass


def main():
    setup_console()
    display_host = "127.0.0.1" if HOST in ("0.0.0.0", "::") else HOST
    url = f"http://{display_host}:{PORT}"

    if port_in_use(HOST, PORT):
        if already_running(url):
            print(f"แอปเปิดอยู่แล้ว — กำลังเปิดหน้า {url} ในเบราว์เซอร์")
            if open_browser_enabled():
                webbrowser.open(url)
            time.sleep(3)
            return
        print(f"เปิดแอปไม่ได้: พอร์ต {PORT} ถูกโปรแกรมอื่นใช้อยู่")
        print("แก้ไขโดยปิดโปรแกรมนั้น หรือตั้งค่า PORT เป็นเลขอื่น เช่น  set PORT=8766")
        pause_before_exit()
        sys.exit(1)

    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    shutil.rmtree(TEMP_DIR, ignore_errors=True)  # ล้างไฟล์ค้างจากครั้งก่อน
    print("=" * 60)
    print(f"  แอปดาวน์โหลดวิดีโอ พร้อมใช้งานที่  {url}")
    print(f"  ไฟล์จะถูกบันทึกที่  {DOWNLOAD_DIR}")
    print(f"  yt-dlp {yt_dlp.version.__version__} | ffmpeg: {'พร้อม' if FFMPEG_PATH else 'ไม่พบ!'}"
          f" | JS runtime: {', '.join(JS_RUNTIMES)}")
    print("  ปิดหน้าต่างนี้ (หรือกด Ctrl+C) เพื่อหยุดแอป")
    print("=" * 60)
    if open_browser_enabled():
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    logging.getLogger("werkzeug").setLevel(logging.ERROR)  # ไม่ต้องแสดง log ทุก request
    app.run(host=HOST, port=PORT, debug=False, threaded=True)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
    except Exception:
        import traceback

        traceback.print_exc()
        print("\nเกิดข้อผิดพลาด ทำให้แอปหยุดทำงาน")
        pause_before_exit()
        sys.exit(1)
