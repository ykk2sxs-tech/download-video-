// Service worker: เมนูคลิกขวา, แจ้งเตือนเมื่อดาวน์โหลดเสร็จ, ตัวเลขบนไอคอน, สั่งเปิดแอป
import {
  AppOfflineError, PROTOCOL_URL, INSTALLER_URL, URL_PATTERNS, api, detectPlatform, getSettings,
  getStatus, shortError, startDownload, videoPageHint,
} from "./common.js";

const ICON = "icons/icon128.png";
const ACTIVE = new Set(["queued", "starting", "downloading", "processing"]);

chrome.runtime.onInstalled.addListener(() => {
  chrome.contextMenus.removeAll(() => {
    chrome.contextMenus.create({
      id: "download-link",
      title: "ดาวน์โหลดวิดีโอจากลิงก์นี้ (โหลดวิดีโอ)",
      contexts: ["link"],
      targetUrlPatterns: URL_PATTERNS,
    });
    chrome.contextMenus.create({
      id: "download-page",
      title: "ดาวน์โหลดวิดีโอในหน้านี้ (โหลดวิดีโอ)",
      contexts: ["page", "video"],
      documentUrlPatterns: URL_PATTERNS,
    });
  });
});

function notify(id, title, message, buttons) {
  chrome.notifications.create(id, {
    type: "basic", iconUrl: ICON, title, message, priority: 1,
    ...(buttons ? { buttons: buttons.map((t) => ({ title: t })) } : {}),
  });
}

chrome.contextMenus.onClicked.addListener(async (info, tab) => {
  const url = info.menuItemId === "download-link" ? info.linkUrl : (info.pageUrl || (tab && tab.url));
  if (!url || !detectPlatform(url)) return;
  const hint = videoPageHint(url);
  if (hint && info.menuItemId !== "download-link") {
    notify(`hint-${Date.now()}`, "ยังไม่พบวิดีโอในหน้านี้", hint);
    return;
  }
  const settings = await getSettings();
  try {
    const title = info.menuItemId === "download-page" && tab ? tab.title : "";
    await startDownload({ url, quality: settings.quality, useLogin: settings.useLogin, title });
    notify(`started-${Date.now()}`, "เริ่มดาวน์โหลดแล้ว", title || url);
    updateBadge();
  } catch (err) {
    if (err instanceof AppOfflineError) {
      notify("offline", "ยังไม่ได้เปิดแอป โหลดวิดีโอ",
        "ดับเบิลคลิกไอคอน \"โหลดวิดีโอ\" บนหน้าจอก่อน แล้วลองอีกครั้ง", ["ดาวน์โหลดตัวติดตั้งแอป"]);
    } else {
      notify(`error-${Date.now()}`, "เริ่มดาวน์โหลดไม่ได้", shortError(err.message));
    }
  }
});

// ---------- ติดตามงานที่สั่งจากส่วนเสริม ----------

async function updateBadge(jobs) {
  try {
    jobs = jobs || await api("/api/jobs", undefined, 3000);
    const active = jobs.filter((j) => ACTIVE.has(j.status)).length;
    await chrome.action.setBadgeBackgroundColor({ color: "#2563eb" });
    await chrome.action.setBadgeText({ text: active ? String(active) : "" });
  } catch (_) {
    await chrome.action.setBadgeText({ text: "" });
  }
}

async function checkWatchedJobs() {
  const { watched = [] } = await chrome.storage.session.get("watched");
  if (!watched.length) {
    await chrome.alarms.clear("watch-jobs");
    return;
  }
  let jobs;
  try {
    jobs = await api("/api/jobs", undefined, 5000);
  } catch (_) {
    return; // แอปปิดอยู่ ลองใหม่รอบหน้า
  }
  const byId = Object.fromEntries(jobs.map((j) => [j.id, j]));
  const still = [];
  for (const id of watched) {
    const job = byId[id];
    if (!job) continue; // แอปถูกเปิดใหม่ งานเก่าหายไปแล้ว
    if (ACTIVE.has(job.status)) { still.push(id); continue; }
    if (job.status === "done") {
      notify(`done-${id}`, "ดาวน์โหลดเสร็จแล้ว ✓", job.filename || job.title || "", ["เปิดไฟล์", "แสดงในโฟลเดอร์"]);
    } else if (job.status === "error") {
      notify(`error-${id}`, "ดาวน์โหลดไม่สำเร็จ", shortError(job.error) || job.message || "");
    }
  }
  await chrome.storage.session.set({ watched: still });
  if (!still.length) await chrome.alarms.clear("watch-jobs");
  updateBadge(jobs);
}

chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === "watch-jobs") checkWatchedJobs();
});

chrome.notifications.onButtonClicked.addListener(async (notificationId, buttonIndex) => {
  if (notificationId === "offline") {
    chrome.tabs.create({ url: INSTALLER_URL });
  } else if (notificationId.startsWith("done-")) {
    const id = notificationId.slice(5);
    api(`/api/jobs/${id}/open`, { reveal: buttonIndex === 1 }).catch(() => {});
  }
  chrome.notifications.clear(notificationId);
});

chrome.notifications.onClicked.addListener((notificationId) => {
  if (notificationId.startsWith("done-")) {
    api(`/api/jobs/${notificationId.slice(5)}/open`, { reveal: true }).catch(() => {});
  }
  chrome.notifications.clear(notificationId);
});

// ---------- สั่งเปิดแอปผ่าน videodownloader:// (ตัวติดตั้งลงทะเบียนไว้ใน Windows) ----------

let launching = null;

async function launchApp(tabId) {
  if (await getStatus()) return true;
  if (tabId !== undefined) {
    try { await chrome.tabs.update(tabId, { url: PROTOCOL_URL }); } catch (_) { /* ignore */ }
  }
  for (let i = 0; i < 45; i++) {
    await new Promise((r) => setTimeout(r, 1000));
    await chrome.runtime.getPlatformInfo(); // ให้ service worker ทำงานต่อระหว่างรอ
    if (await getStatus()) {
      notify("ready", "แอปโหลดวิดีโอพร้อมใช้งาน", "คลิกไอคอนส่วนเสริมอีกครั้งเพื่อดาวน์โหลด");
      updateBadge();
      return true;
    }
  }
  notify("offline", "เปิดแอปไม่สำเร็จ",
    "ดับเบิลคลิกไอคอน \"โหลดวิดีโอ\" บนหน้าจอแทน (ถ้ายังไม่ได้ติดตั้ง กดปุ่มด้านล่าง)", ["ดาวน์โหลดตัวติดตั้งแอป"]);
  return false;
}

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (msg && msg.type === "launch-app") {
    launching = launching || launchApp(msg.tabId).finally(() => { launching = null; });
    launching.then(sendResponse);
    return true; // ตอบกลับแบบ async
  }
  if (msg && msg.type === "jobs-changed") {
    updateBadge();
  }
  return false;
});
