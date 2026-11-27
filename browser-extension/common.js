// ฟังก์ชันที่ใช้ร่วมกันระหว่าง popup และ background
// ส่วนเสริมนี้ไม่ได้ดาวน์โหลดเอง แต่ส่งลิงก์ไปให้แอป "โหลดวิดีโอ" ที่รันอยู่บนเครื่อง (127.0.0.1:8765)

export const APP_URL = "http://127.0.0.1:8765";
export const PROTOCOL_URL = "videodownloader://start";
export const INSTALLER_URL = "https://github.com/ykk2sxs-tech/download-video-/releases/latest";

export const QUALITIES = [
  { value: "1080", label: "1080p", note: "Full HD" },
  { value: "720", label: "720p", note: "HD" },
  { value: "480", label: "480p", note: "ไฟล์เล็ก" },
  { value: "best", label: "สูงสุด", note: "4K" },
  { value: "audio", label: "MP3", note: "เสียง" },
];

export const PLATFORMS = {
  youtube: { name: "YouTube", hosts: ["youtube.com", "youtu.be", "youtube-nocookie.com"], cookieDomains: ["youtube.com"] },
  facebook: { name: "Facebook", hosts: ["facebook.com", "fb.watch", "fb.com"], cookieDomains: ["facebook.com"] },
  zoom: { name: "Zoom", hosts: ["zoom.us", "zoom.com"], cookieDomains: ["zoom.us"] },
};

// ใช้กับเมนูคลิกขวา
export const URL_PATTERNS = [
  "*://*.youtube.com/*", "*://youtu.be/*", "*://*.facebook.com/*", "*://fb.watch/*", "*://*.zoom.us/*",
];

const DEFAULT_SETTINGS = { quality: "1080", useLogin: false };

export function parseUrl(text) {
  const match = (text || "").match(/https?:\/\/[^\s<>"'`]+/i);
  if (!match) return null;
  try { return new URL(match[0]); } catch (_) { return null; }
}

export function detectPlatform(url) {
  const u = url instanceof URL ? url : parseUrl(url);
  if (!u) return null;
  const host = u.hostname.toLowerCase();
  for (const [key, p] of Object.entries(PLATFORMS)) {
    if (p.hosts.some((d) => host === d || host.endsWith("." + d))) return key;
  }
  return null;
}

/** ตรวจว่าลิงก์น่าจะเป็นหน้าวิดีโอ (ไม่ใช่หน้าแรก / ฟีด) — คืนข้อความแนะนำถ้าไม่ใช่ */
export function videoPageHint(url) {
  const u = url instanceof URL ? url : parseUrl(url);
  const platform = detectPlatform(u);
  if (!u || !platform) return null;
  const path = u.pathname.replace(/\/+$/, "") || "/";
  if (platform === "youtube") {
    const ok = u.hostname.endsWith("youtu.be") ? path !== "/"
      : (path === "/watch" && u.searchParams.get("v")) || /^\/(shorts|live|embed|clip)\/[^/]+/.test(path);
    return ok ? null : "เปิดวิดีโอที่ต้องการก่อน (หน้านี้เป็นหน้ารวม ไม่ใช่หน้าวิดีโอ)";
  }
  if (platform === "facebook") {
    if (u.hostname.endsWith("fb.watch")) return null;
    const feed = ["/", "/home.php", "/watch", "/reel", "/groups", "/marketplace"].includes(path) && !u.searchParams.get("v");
    return feed
      ? "นี่คือหน้าฟีด — คลิกขวาที่ \"เวลาโพสต์\" ของวิดีโอแล้วเลือก \"ดาวน์โหลดวิดีโอจากลิงก์นี้\" หรือกดเปิดวิดีโอให้เป็นหน้าเดี่ยวก่อน"
      : null;
  }
  if (platform === "zoom") {
    return /\/(rec|recording|clips)\//.test(path + "/") ? null
      : "ต้องเป็นลิงก์ \"บันทึกการประชุม\" (มี /rec/ ในลิงก์) ไม่ใช่ลิงก์เข้าประชุม";
  }
  return null;
}

export async function getSettings() {
  const stored = await chrome.storage.local.get(DEFAULT_SETTINGS);
  return { ...DEFAULT_SETTINGS, ...stored };
}

export async function saveSettings(patch) {
  await chrome.storage.local.set(patch);
}

export class AppOfflineError extends Error {
  constructor() {
    super("ยังไม่ได้เปิดแอป โหลดวิดีโอ");
    this.name = "AppOfflineError";
  }
}

export async function api(path, body, timeoutMs = 15000) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  let res;
  try {
    res = await fetch(APP_URL + path, body === undefined ? { signal: controller.signal } : {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      signal: controller.signal,
    });
  } catch (_) {
    throw new AppOfflineError();
  } finally {
    clearTimeout(timer);
  }
  let data = null;
  try { data = await res.json(); } catch (_) { /* ignore */ }
  if (!res.ok) throw new Error((data && data.error) || `เกิดข้อผิดพลาด (${res.status})`);
  return data;
}

export async function getStatus() {
  try {
    const status = await api("/api/status", undefined, 2500);
    return status && status.ok ? status : null;
  } catch (_) {
    return null;
  }
}

/** อ่าน cookies ของเว็บนั้นจากเบราว์เซอร์ เพื่อให้แอปโหลดวิดีโอที่ต้องล็อกอินได้ (ส่งไปที่ 127.0.0.1 เท่านั้น) */
export async function collectCookies(url) {
  const platform = detectPlatform(url);
  if (!platform || !chrome.cookies) return [];
  const all = [];
  for (const domain of PLATFORMS[platform].cookieDomains) {
    try {
      all.push(...await chrome.cookies.getAll({ domain }));
    } catch (_) { /* ไม่มีสิทธิ์อ่าน cookies ของเว็บนี้ */ }
  }
  return all.slice(0, 400).map((c) => ({
    domain: c.domain, hostOnly: c.hostOnly, name: c.name, value: c.value, path: c.path,
    secure: c.secure, httpOnly: c.httpOnly, session: c.session, expirationDate: c.expirationDate,
  }));
}

export async function startDownload({ url, quality, password = "", useLogin = false, title = "" }) {
  const body = { url, quality, password, title };
  if (useLogin) body.cookies = await collectCookies(url);
  const job = await api("/api/download", body, 20000);
  await watchJob(job.id);
  return job;
}

// ---------- ติดตามงาน เพื่อแจ้งเตือนเมื่อเสร็จ (แม้ปิด popup ไปแล้ว) ----------

export async function watchJob(jobId) {
  const { watched = [] } = await chrome.storage.session.get("watched");
  if (!watched.includes(jobId)) watched.push(jobId);
  await chrome.storage.session.set({ watched });
  await chrome.alarms.create("watch-jobs", { periodInMinutes: 0.5 });
}

export function shortError(error) {
  return (error || "").split("\n\nรายละเอียด:")[0].trim();
}

export function fmtBytes(b) {
  if (!b) return "";
  const units = ["B", "KB", "MB", "GB"];
  let i = 0;
  while (b >= 1024 && i < units.length - 1) { b /= 1024; i++; }
  return `${b.toFixed(i >= 2 ? 1 : 0)} ${units[i]}`;
}
