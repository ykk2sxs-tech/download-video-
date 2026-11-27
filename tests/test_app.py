import functools
import http.server
import subprocess
import threading
import time

import pytest

import app as app_module
from app import build_format, detect_platform, extract_url, friendly_error


# ---------------------------------------------------------------------------
# unit tests
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "text, expected",
    [
        ("https://youtu.be/dQw4w9WgXcQ", "https://youtu.be/dQw4w9WgXcQ"),
        ("  https://www.youtube.com/watch?v=abc&t=10s  ", "https://www.youtube.com/watch?v=abc&t=10s"),
        ("ดูคลิปนี้ https://fb.watch/abcd/ สนุกมาก", "https://fb.watch/abcd/"),
        ("(https://www.facebook.com/reel/123).", "https://www.facebook.com/reel/123"),
        ("www.facebook.com/watch/?v=999", "https://www.facebook.com/watch/?v=999"),
        ("us02web.zoom.us/rec/share/AbC.dEf?startTime=1", "https://us02web.zoom.us/rec/share/AbC.dEf?startTime=1"),
        ("ไม่มีลิงก์", None),
        ("", None),
        (None, None),
    ],
)
def test_extract_url(text, expected):
    assert extract_url(text) == expected


@pytest.mark.parametrize(
    "url, expected",
    [
        ("https://www.youtube.com/watch?v=x", "youtube"),
        ("https://m.youtube.com/shorts/x", "youtube"),
        ("https://youtu.be/x", "youtube"),
        ("https://web.facebook.com/watch?v=1", "facebook"),
        ("https://fb.watch/x/", "facebook"),
        ("https://us06web.zoom.us/rec/play/x", "zoom"),
        ("https://zoom.us/clips/share/x", "zoom"),
        ("https://notyoutube.com/x", None),
        ("https://example.com/video.mp4", None),
    ],
)
def test_detect_platform(url, expected):
    assert detect_platform(url) == expected


def test_build_format():
    assert build_format("audio") == "ba/b"
    assert build_format("best") == "bv*+ba/b"
    fmt = build_format("720")
    assert fmt.startswith("bv*[height<=?720][vcodec^=avc1]+ba[ext=m4a]/")
    assert fmt.endswith("/b")


def test_friendly_error_messages():
    assert "รหัสผ่าน" in friendly_error(
        "ERROR: [Zoom] x: This video is protected by a passcode, use the --video-password option", "zoom")
    assert "ไม่ถูกต้อง" in friendly_error("ERROR: [Zoom] x: Wrong passcode", "zoom")
    assert "ล็อกอิน" in friendly_error("ERROR: Sign in to confirm you're not a bot", "youtube")
    assert "ไม่รองรับ" in friendly_error("ERROR: Unsupported URL: https://x", None)
    assert "\x1b" not in friendly_error("\x1b[0;31mERROR:\x1b[0m boom", None)


class QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


# ---------------------------------------------------------------------------
# integration tests (ใช้ไฟล์วิดีโอทดสอบที่สร้างด้วย ffmpeg และเสิร์ฟจากเครื่องตัวเอง)
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def media_server(tmp_path_factory):
    if not app_module.FFMPEG_PATH:
        pytest.skip("ไม่มี ffmpeg")
    root = tmp_path_factory.mktemp("media")
    subprocess.run(
        [app_module.FFMPEG_PATH, "-y", "-loglevel", "error",
         "-f", "lavfi", "-i", "testsrc=size=640x360:rate=25:duration=3",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest",
         str(root / "sample.mp4")],
        check=True,
    )
    handler = functools.partial(QuietHandler, directory=str(root))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}/sample.mp4"
    server.shutdown()


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, "DOWNLOAD_DIR", tmp_path)
    monkeypatch.setattr(app_module, "TEMP_DIR", tmp_path / ".tmp")
    app_module.jobs.clear()
    return app_module.app.test_client()


def wait_for_job(client, job_id, timeout=60):
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = client.get(f"/api/jobs/{job_id}").get_json()
        if job["status"] in ("done", "error", "cancelled"):
            return job
        time.sleep(0.2)
    raise AssertionError("job did not finish in time")


def test_index_and_status(client):
    assert client.get("/").status_code == 200
    status = client.get("/api/status").get_json()
    assert status["ok"] is True
    assert status["tools"]["youtube_solver"] is True


def test_rejects_foreign_host_header(client):
    res = client.get("/api/status", headers={"Host": "evil.example.com"})
    assert res.status_code == 403


def test_requires_json(client):
    res = client.post("/api/info", data="url=https://youtu.be/x")
    assert res.status_code == 400


def test_info_rejects_text_without_link(client):
    res = client.post("/api/info", json={"url": "สวัสดี"})
    assert res.status_code == 400
    assert "ไม่พบลิงก์" in res.get_json()["error"]


def test_info_and_video_download(client, media_server, tmp_path):
    info = client.post("/api/info", json={"url": f"ลิงก์ {media_server}"}).get_json()
    assert info["url"] == media_server
    assert info["title"] == "sample"

    res = client.post("/api/download", json={"url": media_server, "quality": "720", "title": info["title"]})
    assert res.status_code == 202
    job = wait_for_job(client, res.get_json()["id"])
    assert job["status"] == "done", job.get("error")
    assert job["filename"].endswith(".mp4")
    assert (tmp_path / job["filename"]).is_file()
    assert not (tmp_path / ".tmp" / job["id"]).exists()

    file_res = client.get(f"/api/jobs/{job['id']}/file")
    assert file_res.status_code == 200
    assert len(file_res.data) == job["filesize"]


def test_audio_download_converts_to_mp3(client, media_server, tmp_path):
    res = client.post("/api/download", json={"url": media_server, "quality": "audio"})
    job = wait_for_job(client, res.get_json()["id"])
    assert job["status"] == "done", job.get("error")
    assert job["filename"].endswith(".mp3")
    assert (tmp_path / job["filename"]).stat().st_size > 1000


def test_invalid_quality(client, media_server):
    res = client.post("/api/download", json={"url": media_server, "quality": "9999"})
    assert res.status_code == 400


class SlowHandler(http.server.BaseHTTPRequestHandler):
    """เสิร์ฟไฟล์ช้า ๆ เพื่อให้ทดสอบปุ่มยกเลิกได้"""
    payload = b""

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "video/mp4")
        self.send_header("Content-Length", str(len(self.payload)))
        self.end_headers()
        for i in range(0, len(self.payload), 32 * 1024):
            try:
                self.wfile.write(self.payload[i:i + 32 * 1024])
            except (BrokenPipeError, ConnectionResetError):
                return
            time.sleep(0.05)

    def log_message(self, *args):
        pass


def test_cancel_download_cleans_temp(client, media_server, tmp_path):
    import urllib.request

    SlowHandler.payload = urllib.request.urlopen(media_server).read() * 40
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), SlowHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        url = f"http://127.0.0.1:{server.server_port}/slow.mp4"
        job_id = client.post("/api/download", json={"url": url, "quality": "best"}).get_json()["id"]
        deadline = time.time() + 30
        while client.get(f"/api/jobs/{job_id}").get_json()["status"] != "downloading":
            assert time.time() < deadline
            time.sleep(0.05)
        client.post(f"/api/jobs/{job_id}/cancel", json={})
        job = wait_for_job(client, job_id)
        assert job["status"] == "cancelled"
        assert not (tmp_path / ".tmp" / job_id).exists()
        assert not any(p.suffix == ".mp4" for p in tmp_path.iterdir())
    finally:
        server.shutdown()


def test_missing_browser_cookies_gives_thai_hint(client, media_server, monkeypatch, tmp_path):
    for var in ("HOME", "APPDATA", "LOCALAPPDATA", "USERPROFILE"):
        monkeypatch.setenv(var, str(tmp_path))  # ไม่มีโปรไฟล์ Firefox ในโฟลเดอร์นี้
    res = client.post("/api/info", json={"url": media_server, "cookies_browser": "firefox"})
    assert res.status_code == 422
    assert "ไม่พบข้อมูลการล็อกอิน" in res.get_json()["error"]


def test_rejects_unknown_browser(client, media_server):
    res = client.post("/api/info", json={"url": media_server, "cookies_browser": "netscape"})
    assert res.status_code == 400


class CookieGateHandler(http.server.BaseHTTPRequestHandler):
    """เสิร์ฟวิดีโอเฉพาะเมื่อมี cookie ที่ถูกต้อง (จำลองวิดีโอที่ต้องล็อกอิน)"""
    payload = b""

    def do_GET(self):
        if "session=secret123" not in (self.headers.get("Cookie") or ""):
            self.send_response(403)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", "video/mp4")
        self.send_header("Content-Length", str(len(self.payload)))
        self.end_headers()
        self.wfile.write(self.payload)

    def log_message(self, *args):
        pass


@pytest.fixture()
def cookie_gate(media_server):
    import urllib.request

    CookieGateHandler.payload = urllib.request.urlopen(media_server).read()
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), CookieGateHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}/private.mp4"
    server.shutdown()


BROWSER_COOKIE = {  # รูปแบบเดียวกับที่ chrome.cookies.getAll() คืนมา
    "domain": "127.0.0.1", "hostOnly": True, "name": "session", "value": "secret123",
    "path": "/", "secure": False, "httpOnly": True, "session": True,
}


def test_download_with_browser_cookies(client, cookie_gate, tmp_path):
    without = client.post("/api/download", json={"url": cookie_gate, "quality": "720"}).get_json()
    assert wait_for_job(client, without["id"])["status"] == "error"

    res = client.post("/api/download", json={"url": cookie_gate, "quality": "720", "cookies": [BROWSER_COOKIE]})
    job = wait_for_job(client, res.get_json()["id"])
    assert job["status"] == "done", job.get("error")
    assert (tmp_path / job["filename"]).is_file()


def test_info_with_browser_cookies(client, cookie_gate):
    assert client.post("/api/info", json={"url": cookie_gate}).status_code == 422
    res = client.post("/api/info", json={"url": cookie_gate, "cookies": [BROWSER_COOKIE]})
    assert res.status_code == 200, res.get_json()


@pytest.mark.parametrize("bad", [
    "not-a-list",
    [{"name": "a"}],
    [{"domain": "x.com", "name": "", "value": "v"}],
    [{"domain": "x.com", "name": "a", "value": "v" * 9000}],
    [BROWSER_COOKIE] * 401,
])
def test_rejects_malformed_cookies(client, bad):
    res = client.post("/api/info", json={"url": "https://youtu.be/x", "cookies": bad})
    assert res.status_code == 400


def test_parse_cookies_domain_rules():
    host_only, shared = app_module.parse_cookies([
        BROWSER_COOKIE,
        {"domain": ".facebook.com", "hostOnly": False, "name": "c_user", "value": "1",
         "path": "/", "secure": True, "expirationDate": 1893456000.5},
    ])
    assert host_only.domain == "127.0.0.1" and not host_only.domain_specified
    assert host_only.has_nonstandard_attr("HttpOnly")
    assert shared.domain == ".facebook.com" and shared.domain_specified and shared.secure
    assert shared.expires == 1893456000


def test_open_job_file_requires_finished_job(client):
    assert client.post("/api/jobs/nope/open", json={}).status_code == 404


def test_status_reports_extension_dir(client):
    status = client.get("/api/status").get_json()
    assert status["extension_dir"] is None or status["extension_dir"].endswith("browser-extension")


def test_protocol_launch_does_not_open_browser_tab(monkeypatch):
    monkeypatch.delenv("NO_BROWSER", raising=False)
    monkeypatch.setattr(app_module.sys, "argv", ["app.py"])
    monkeypatch.setattr(app_module, "LAUNCHED_BY_PROTOCOL", False)
    assert app_module.open_browser_enabled() is True
    monkeypatch.setattr(app_module, "LAUNCHED_BY_PROTOCOL", True)
    assert app_module.open_browser_enabled() is False
