"""ทดสอบโปรแกรมที่ build แล้ว (ไม่ต้องใช้อินเทอร์เน็ต)

ใช้:  python packaging/smoke_test.py dist/VideoDownloader/VideoDownloader.exe

ตรวจว่า: เปิดโปรแกรมได้, หน้าเว็บโหลดได้, ffmpeg/deno/สคริปต์ YouTube ที่แนบมาใช้งานได้,
ดาวน์โหลดวิดีโอ (MP4) และแปลง MP3 ได้จริงจากไฟล์ทดสอบที่เสิร์ฟในเครื่อง
"""

from __future__ import annotations

import functools
import http.server
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

PORT = 8799
BASE = f"http://127.0.0.1:{PORT}"


class QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


def call(path: str, body: dict | None = None, timeout: float = 10):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(BASE + path, data=data, method="POST" if body is not None else "GET",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as res:
        raw = res.read()
    return json.loads(raw) if raw[:1] in (b"{", b"[") else raw.decode("utf-8")


def wait_for_job(job_id: str, timeout: float = 120) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = call(f"/api/jobs/{job_id}")
        if job["status"] in ("done", "error", "cancelled"):
            return job
        time.sleep(0.5)
    raise AssertionError(f"งาน {job_id} ไม่เสร็จภายใน {timeout} วินาที")


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)
    print(f"  ✓ {message}")


def main() -> None:
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8", errors="replace")
    exe = Path(sys.argv[1]).resolve()
    if not exe.is_file():
        sys.exit(f"ไม่พบไฟล์ {exe}")
    work = Path(tempfile.mkdtemp(prefix="vd-smoke-"))
    media, downloads = work / "media", work / "downloads"
    media.mkdir()

    env = dict(os.environ, NO_BROWSER="1", PORT=str(PORT), DOWNLOAD_DIR=str(downloads),
               PYTHONIOENCODING="utf-8")
    # ไม่ให้เจอ ffmpeg/deno/node ของเครื่อง build — ต้องใช้ตัวที่แนบมาเท่านั้น
    env["PATH"] = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32") \
        if sys.platform.startswith("win") else "/nonexistent"

    log_path = work / "app.log"
    log = open(log_path, "wb")
    proc = subprocess.Popen([str(exe)], env=env, stdout=log, stderr=subprocess.STDOUT,
                            stdin=subprocess.DEVNULL, cwd=str(work))
    server = None
    try:
        deadline = time.time() + 90
        status = None
        while time.time() < deadline:
            if proc.poll() is not None:
                raise AssertionError(f"โปรแกรมปิดตัวเอง (exit code {proc.returncode})")
            try:
                status = call("/api/status", timeout=3)
                break
            except Exception:
                time.sleep(1)
        check(status is not None, "โปรแกรมเปิดและตอบ /api/status")
        print("   ", json.dumps(status, ensure_ascii=False))
        tools = status["tools"]
        bundle_dir = str(exe.parent)
        check(status["frozen"] is True, "รันในโหมด .exe (frozen)")
        check(bool(tools["ffmpeg"]) and tools["ffmpeg"].startswith(bundle_dir), "ใช้ ffmpeg ที่แนบมากับโปรแกรม")
        check(bool(tools["deno"]) and tools["deno"].startswith(bundle_dir), "ใช้ deno ที่แนบมากับโปรแกรม")
        check(tools["youtube_solver"] is True, "สคริปต์ถอดรหัส YouTube (yt-dlp-ejs) อยู่ครบ")

        deno_ver = subprocess.run([tools["deno"], "--version"], capture_output=True, text=True, timeout=60)
        check(deno_ver.returncode == 0 and "deno" in deno_ver.stdout, f"deno ทำงานได้ ({deno_ver.stdout.split()[1]})")
        deno_js = subprocess.run([tools["deno"], "eval", "console.log(6*7)"], capture_output=True,
                                 text=True, timeout=60, env=env)
        check(deno_js.stdout.strip() == "42", "deno รัน JavaScript ได้")

        html = call("/")
        check("โหลดวิดีโอ" in html, "หน้าเว็บโหลดได้ (ภาษาไทยถูกต้อง)")

        subprocess.run(
            [tools["ffmpeg"], "-y", "-loglevel", "error",
             "-f", "lavfi", "-i", "testsrc=size=1280x720:rate=25:duration=4",
             "-f", "lavfi", "-i", "sine=frequency=440:duration=4",
             "-c:v", "mpeg4", "-q:v", "5", "-c:a", "aac", "-shortest", str(media / "sample.mp4")],
            check=True, timeout=120,
        )
        handler = functools.partial(QuietHandler, directory=str(media))
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        video_url = f"http://127.0.0.1:{server.server_port}/sample.mp4"

        info = call("/api/info", {"url": f"วางลิงก์ {video_url}"}, timeout=60)
        check(info.get("url") == video_url, "ตรวจสอบลิงก์ (/api/info) ได้")

        for quality, ext in (("1080", ".mp4"), ("audio", ".mp3")):
            job = call("/api/download", {"url": video_url, "quality": quality}, timeout=30)
            job = wait_for_job(job["id"])
            check(job["status"] == "done", f"ดาวน์โหลดแบบ {quality} สำเร็จ {job.get('error') or ''}".strip())
            path = downloads / job["filename"]
            check(path.suffix == ext and path.stat().st_size > 1000,
                  f"ได้ไฟล์ {path.name} ({path.stat().st_size / 1024:.0f} KB)")
        print("\nผ่านทุกข้อ ✔")
    except BaseException:
        log.flush()
        print("\n----- log ของโปรแกรม -----")
        print(log_path.read_text(encoding="utf-8", errors="replace"))
        raise
    finally:
        if server:
            server.shutdown()
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
        log.close()


if __name__ == "__main__":
    main()
