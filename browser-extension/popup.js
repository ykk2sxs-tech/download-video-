import {
  APP_URL, AppOfflineError, INSTALLER_URL, PLATFORMS, QUALITIES, api, detectPlatform, fmtBytes,
  getSettings, getStatus, parseUrl, saveSettings, shortError, startDownload, videoPageHint,
} from "./common.js";

const $ = (id) => document.getElementById(id);
const ACTIVE = new Set(["queued", "starting", "downloading", "processing"]);
const STATUS_TEXT = {
  queued: "รอคิว", starting: "กำลังเตรียม", downloading: "กำลังโหลด", processing: "กำลังประมวลผล",
  done: "เสร็จแล้ว", error: "ไม่สำเร็จ", cancelled: "ยกเลิกแล้ว",
};

let activeTab = null;
let online = null;
let busy = false;

function el(tag, attrs = {}, children = []) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") node.className = v;
    else if (k === "text") node.textContent = v;
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else node.setAttribute(k, v);
  }
  for (const c of [].concat(children)) if (c) node.append(c);
  return node;
}

function cleanTitle(title) {
  return (title || "")
    .replace(/^\(\d+\+?\)\s*/, "")
    .replace(/\s*[-|]\s*(YouTube|Facebook|Zoom)\s*$/i, "")
    .trim();
}

function showMsg(text, kind = "error") {
  const box = $("formMsg");
  box.textContent = text || "";
  box.className = `msg ${kind}`;
  box.classList.toggle("hidden", !text);
}

function selectedQuality() {
  return ($("qualities").querySelector("input:checked") || {}).value || "1080";
}

function renderQualities(current) {
  $("qualities").replaceChildren(...QUALITIES.map((q) => el("label", { class: "q", title: `${q.label} ${q.note}` }, [
    el("input", { type: "radio", name: "quality", value: q.value, ...(q.value === current ? { checked: "" } : {}),
      onchange: () => saveSettings({ quality: q.value }) }),
    el("span", {}, [q.label, el("small", { text: q.note })]),
  ])));
}

function updateUrlState() {
  const url = parseUrl($("url").value);
  const platform = url ? detectPlatform(url) : null;
  const chip = $("platformChip");
  chip.textContent = platform ? PLATFORMS[platform].name : (url ? "เว็บไซต์อื่น" : "");
  chip.classList.toggle("hidden", !url);
  $("zoomBox").classList.toggle("hidden", platform !== "zoom");
  const hint = url ? videoPageHint(url) : null;
  $("pageHint").textContent = hint || "";
  $("pageHint").classList.toggle("hidden", !hint);
  $("pageTitle").textContent = activeTab && url && url.href === activeTab.url ? cleanTitle(activeTab.title) : "";
  $("pageRow").classList.toggle("hidden", !url);
  $("downloadBtn").disabled = busy || !url || !!hint || online === false;
}

function setOnline(isOnline) {
  if (online === isOnline) return;
  online = isOnline;
  const status = $("status");
  status.className = `status ${isOnline ? "online" : "offline"}`;
  status.querySelector("span").textContent = isOnline ? "เชื่อมต่อแอปแล้ว" : "แอปยังไม่เปิด";
  $("offline").classList.toggle("hidden", isOnline);
  $("main").classList.toggle("hidden", !isOnline);
  if (isOnline) {
    $("launchBtn").disabled = false;
    $("launchBtn").textContent = "เปิดแอป";
  }
  updateUrlState();
}

function renderJobs(jobs) {
  const recent = jobs.slice(0, 4);
  $("jobsSection").classList.toggle("hidden", !recent.length);
  $("jobs").replaceChildren(...recent.map((job) => {
    const active = ACTIVE.has(job.status);
    const badgeCls = job.status === "done" ? "done" : job.status === "error" ? "error" : active ? "active" : "";
    const right = job.status === "downloading"
      ? [job.speed ? `${fmtBytes(job.speed)}/s` : "", `${Math.floor(job.percent || 0)}%`].filter(Boolean).join(" · ")
      : job.status === "done" ? fmtBytes(job.filesize) : "";
    const actions = [];
    if (active) {
      actions.push(el("button", { type: "button", class: "small", text: "ยกเลิก",
        onclick: () => api(`/api/jobs/${job.id}/cancel`, {}).then(refresh).catch(() => {}) }));
    }
    if (job.status === "done") {
      actions.push(el("button", { type: "button", class: "small primary", text: "เปิดไฟล์",
        onclick: () => api(`/api/jobs/${job.id}/open`, { reveal: false }).catch((e) => showMsg(e.message)) }));
      actions.push(el("button", { type: "button", class: "small", text: "แสดงในโฟลเดอร์",
        onclick: () => api(`/api/jobs/${job.id}/open`, { reveal: true }).catch((e) => showMsg(e.message)) }));
    }
    if (job.status === "error" || job.status === "cancelled") {
      actions.push(el("button", { type: "button", class: "small", text: "ลองใหม่",
        onclick: () => { $("url").value = job.url; updateUrlState(); $("downloadBtn").focus(); } }));
    }
    return el("article", { class: `card job ${job.status}` }, [
      el("div", { class: "job-top" }, [
        el("div", { class: "job-title", text: job.filename || job.title || job.url, title: job.url }),
        el("span", { class: `badge ${badgeCls}`, text: STATUS_TEXT[job.status] || job.status }),
      ]),
      job.status === "error" ? null : el("div", { class: "bar" },
        el("div", { style: `width:${job.status === "cancelled" ? 0 : job.percent || 0}%` })),
      el("div", { class: "job-line" }, [
        el("span", { text: `${job.quality_label} · ${job.message || ""}` }),
        el("span", { text: right }),
      ]),
      job.error ? el("div", { class: "job-error", text: shortError(job.error) }) : null,
      actions.length ? el("div", { class: "job-actions" }, actions) : null,
    ]);
  }));
}

async function refresh() {
  const status = await getStatus();
  setOnline(!!status);
  if (!status) return;
  try {
    renderJobs(await api("/api/jobs", undefined, 3000));
  } catch (_) { /* ignore */ }
}

async function download() {
  const url = parseUrl($("url").value);
  if (!url) { showMsg("กรุณาวางลิงก์วิดีโอ"); return; }
  const hint = videoPageHint(url);
  if (hint) { showMsg(hint); return; }
  busy = true;
  updateUrlState();
  $("downloadBtn").querySelector("span").textContent = "กำลังส่งไปที่แอป…";
  showMsg("");
  try {
    const title = activeTab && activeTab.url === url.href ? cleanTitle(activeTab.title) : "";
    await startDownload({
      url: url.href, quality: selectedQuality(), password: $("password").value,
      useLogin: $("useLogin").checked, title,
    });
    showMsg("เริ่มดาวน์โหลดแล้ว — ปิดหน้าต่างนี้ได้เลย ระบบจะแจ้งเตือนเมื่อเสร็จ", "ok");
    chrome.runtime.sendMessage({ type: "jobs-changed" }).catch(() => {});
    refresh();
  } catch (err) {
    if (err instanceof AppOfflineError) setOnline(false);
    else showMsg(shortError(err.message));
  } finally {
    busy = false;
    $("downloadBtn").querySelector("span").textContent = "ดาวน์โหลด";
    updateUrlState();
  }
}

async function init() {
  const settings = await getSettings();
  renderQualities(settings.quality);
  $("useLogin").checked = !!settings.useLogin;

  [activeTab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (activeTab && activeTab.url && detectPlatform(activeTab.url)) {
    $("url").value = activeTab.url;
  }
  updateUrlState();

  $("url").addEventListener("input", () => { showMsg(""); updateUrlState(); });
  $("url").addEventListener("keydown", (e) => { if (e.key === "Enter") download(); });
  $("useLogin").addEventListener("change", (e) => saveSettings({ useLogin: e.target.checked }));
  $("downloadBtn").addEventListener("click", download);
  $("launchBtn").addEventListener("click", () => {
    $("launchBtn").disabled = true;
    $("launchBtn").textContent = "กำลังเปิดแอป… (ถ้าเบราว์เซอร์ถาม ให้กด เปิด/Open)";
    chrome.runtime.sendMessage({ type: "launch-app", tabId: activeTab ? activeTab.id : undefined }).catch(() => {});
  });
  $("installLink").addEventListener("click", (e) => { e.preventDefault(); chrome.tabs.create({ url: INSTALLER_URL }); });
  $("openApp").addEventListener("click", (e) => { e.preventDefault(); chrome.tabs.create({ url: APP_URL }); });
  $("openFolder").addEventListener("click", (e) => {
    e.preventDefault();
    api("/api/open-folder", {}).catch((err) => showMsg(err instanceof AppOfflineError ? err.message : err.message));
  });

  await refresh();
  if (online && !$("downloadBtn").disabled) $("downloadBtn").focus();
  else $("url").focus();
  setInterval(refresh, 1000);
}

init();
