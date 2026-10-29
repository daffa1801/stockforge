const $ = (id) => document.getElementById(id);
let config = null;
let lastState = null;
let currentView = "gallery";

async function api(path, body) {
  const opts = body
    ? { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }
    : {};
  const res = await fetch(path, opts);
  return res.json();
}

function escapeHtml(s) {
  return String(s || "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function fmtStatus(status) {
  return (status || "idle").replace(/_/g, " ");
}

/* ---------------- galeri ---------------- */

function judgeChips(judgement) {
  if (!judgement || !judgement.judges) return '<span class="chip">belum dinilai</span>';
  return judgement.judges
    .map((j) => {
      const cls = !j.ok ? "chip fail" : j.pass ? "chip pass" : "chip fail";
      return `<span class="${cls}" title="${escapeHtml(j.reason || "")}">${j.id} ${j.ok ? j.score : "err"}</span>`;
    })
    .join("");
}

function card(item, threshold) {
  const j = item.judgement;
  const votes = j ? j.votes : 0;
  const votesCls = j && j.passed ? "votes pass" : "votes";
  const kw = (item.keywords || "").split(",").filter((k) => k.trim()).length;
  const meta = item.metadata;
  const metaText = meta ? `meta ${meta.ok ? "ok" : "gagal"}` : "meta -";
  const showActions = ["shortlisted", "approved", "rejected"].includes(item.status);
  return `
  <div class="card ${item.status}">
    <img class="thumb" loading="lazy" src="/api/image?thumb=1&name=${encodeURIComponent(item.filename)}" alt="" />
    <div class="card-body">
      <div class="filename">${escapeHtml(item.filename)}</div>
      <div class="title" title="${escapeHtml(item.title)}">${escapeHtml(item.title)}</div>
      <div class="meta-row">
        <span>cat ${escapeHtml(item.category || "-")}</span><span>${kw} keyword</span><span>${metaText}</span>
      </div>
      <div class="chips">${judgeChips(j)}</div>
      <div class="${votesCls}">${j ? `${votes}/${j.answered} setuju (butuh ${threshold}) · ${j.avg_score}` : "menunggu juri"}</div>
      ${item.error ? `<div class="votes fail">${escapeHtml(item.error)}</div>` : ""}
      ${showActions ? `<div class="actions">
        <button data-act="approve" data-file="${escapeHtml(item.filename)}">Setujui</button>
        <button data-act="reject" data-file="${escapeHtml(item.filename)}">Buang</button>
        <button data-act="regenerate" data-file="${escapeHtml(item.filename)}">Ulangi</button>
      </div>` : ""}
    </div>
  </div>`;
}

function renderGallery(state) {
  const run = state.run || {};
  const p = state.progress || {};

  $("statusPill").textContent = fmtStatus(run.status);
  $("statusPill").className = "pill " + (run.status || "idle");
  $("profileBadge").textContent = run.profile || "Akun -";
  $("runMessage").textContent = run.message || "";
  $("runCurrent").textContent = run.current || "";
  $("cTotal").textContent = p.total || 0;
  $("cGen").textContent = p.generated || 0;
  $("cJudge").textContent = p.judged || 0;
  $("cShort").textContent = p.shortlisted || 0;
  $("cRej").textContent = p.rejected || 0;

  const waiting = ["waiting_profile", "waiting_login"].includes(run.status);
  $("notice").classList.toggle("hidden", !waiting);
  if (waiting) $("noticeText").textContent = run.message;

  const onlyShort = $("onlyShort").checked;
  const threshold = (config && config.vote_threshold) || 3;
  const concepts = state.concepts || [];
  const host = $("concepts");

  if (!concepts.length) {
    host.innerHTML = '<div class="empty">Belum ada rencana. Tempel link spreadsheet lalu tekan Mulai.</div>';
    return;
  }

  host.innerHTML = concepts
    .map((c) => {
      const items = (c.items || []).filter((i) => !onlyShort || ["shortlisted", "approved"].includes(i.status));
      if (!items.length) return "";
      const passed = items.filter((i) => ["shortlisted", "approved"].includes(i.status)).length;
      return `
      <section class="concept">
        <div class="concept-head">
          <h2>${escapeHtml(c.label || c.key)}</h2>
          <span class="tag">${passed} lolos / ${items.length} variasi</span>
          ${c.low_confidence ? '<span class="tag warn">low confidence</span>' : ""}
        </div>
        <div class="grid">${items.map((i) => card(i, threshold)).join("")}</div>
      </section>`;
    })
    .join("");
}

/* ---------------- log pekerjaan ---------------- */

const LOG_LABELS = {
  run_start: "Run dimulai",
  sheet_loaded: "Spreadsheet dibaca",
  resume: "Lanjut dari sebelumnya",
  browser_open: "Chrome dibuka",
  login_needed: "Butuh login",
  waiting_user: "Menunggu kamu",
  profile_switch: "Ganti akun",
  rate_limit: "Kena limit akun",
  generate_ok: "Gambar selesai",
  generate_fail: "Generate gagal",
  metadata_ok: "Metadata tertanam",
  metadata_warn: "Metadata kurang",
  judged: "Dinilai panel",
  concept_ranked: "Peringkat konsep",
  user_approve: "Kamu setujui",
  user_reject: "Kamu buang",
  user_regenerate: "Kamu ulangi",
  pause: "Dijeda",
  resume_run: "Dilanjutkan",
  stop: "Dihentikan",
  run_end: "Run selesai",
  error: "Error",
};

const LOG_CATEGORY = {
  generate_ok: "generate",
  generate_fail: "generate",
  metadata_ok: "generate",
  metadata_warn: "generate",
  judged: "panel",
  concept_ranked: "panel",
  user_approve: "keputusan",
  user_reject: "keputusan",
  user_regenerate: "keputusan",
};

const CATEGORIES = [
  ["all", "Semua"],
  ["generate", "Generate"],
  ["panel", "Panel juri"],
  ["keputusan", "Keputusanmu"],
  ["sistem", "Sistem"],
];

const LOG = { day: "", category: "all", search: "", entries: [] };

const categoryOf = (event) => LOG_CATEGORY[event] || "sistem";
const labelOf = (event) => LOG_LABELS[event] || String(event || "").replace(/_/g, " ");

function buildRuns(entries) {
  const runs = [];
  let current = null;
  for (const e of entries) {
    if (e.event === "run_start") {
      current = { start: e, end: null, entries: [e] };
      runs.push(current);
      continue;
    }
    if (!current) {
      current = { start: null, end: null, entries: [] };
      runs.push(current);
    }
    current.entries.push(e);
    if (e.event === "run_end") current.end = e;
  }
  return runs;
}

function runMeta(run) {
  const end = run.end || {};
  const parts = [];
  if (end.minutes !== undefined) parts.push(`${end.minutes} menit`);
  parts.push(`${end.generated !== undefined ? end.generated : run.entries.filter((e) => e.event === "generate_ok").length} gambar`);
  if (end.shortlisted !== undefined) parts.push(`${end.shortlisted} lolos`);
  if (end.rejected !== undefined) parts.push(`${end.rejected} dibuang`);
  return parts.join(" · ");
}

function runStatus(run) {
  if (!run.end) return { cls: "jalan", text: "belum selesai" };
  if (/STOP/i.test(run.end.message || "")) return { cls: "stop", text: "dihentikan" };
  if (run.entries.some((e) => e.event === "error")) return { cls: "err", text: "error" };
  return { cls: "ok", text: "selesai" };
}

function rowHtml(e) {
  const cat = categoryOf(e.event);
  const label = labelOf(e.event);
  let msg = (e.message || "").trim();
  if (msg.toLowerCase().startsWith(label.toLowerCase())) {
    msg = msg.slice(label.length).replace(/^[\s:·–-]+/, "");
  }
  return `<div class="lrow c-${cat} ev-${escapeHtml(e.event)}">
    <span class="ltime">${escapeHtml(e.time || "")}</span>
    <span class="lbadge">${escapeHtml(label)}</span>
    <span class="lmsg">${escapeHtml(msg)}</span>
  </div>`;
}

function filteredEntries() {
  const q = LOG.search.trim().toLowerCase();
  return LOG.entries.filter((e) => {
    if (LOG.category !== "all" && categoryOf(e.event) !== LOG.category) return false;
    if (!q) return true;
    return `${e.message || ""} ${labelOf(e.event)} ${e.filename || ""}`.toLowerCase().includes(q);
  });
}

function renderStats(all) {
  const count = (name) => all.filter((e) => e.event === name).length;
  const ends = all.filter((e) => e.event === "run_end");
  const sum = (key) => ends.reduce((acc, e) => acc + (Number(e[key]) || 0), 0);

  const stats = [
    ["Run", count("run_start"), ""],
    ["Gambar jadi", Math.max(count("generate_ok"), sum("generated")), "ok"],
    ["Lolos panel", sum("shortlisted"), "ok"],
    ["Dibuang panel", sum("rejected"), ""],
    ["Dinilai panel", count("judged"), ""],
    ["Kamu setujui", count("user_approve"), ""],
    ["Generate gagal", count("generate_fail"), count("generate_fail") ? "bad" : ""],
    ["Kena limit", count("rate_limit"), count("rate_limit") ? "warnc" : ""],
  ];
  $("logStats").innerHTML = stats
    .map(([label, value, tone]) => `<div class="stat ${tone}"><b>${value}</b><span>${escapeHtml(label)}</span></div>`)
    .join("");
}

function renderFilters() {
  $("logFilters").innerHTML = CATEGORIES.map(
    ([key, label]) =>
      `<button class="fchip ${LOG.category === key ? "active" : ""}" data-cat="${key}">${escapeHtml(label)}</button>`
  ).join("");
}

function renderLog() {
  const shown = filteredEntries();
  const runs = buildRuns([...shown].reverse()).reverse();

  const bits = [`${shown.length} dari ${LOG.entries.length} kejadian`];
  if (LOG.search.trim()) bits.push(`cari "${LOG.search.trim()}"`);
  $("logSubtitle").textContent = bits.join(" · ");

  if (!runs.length) {
    $("logBody").innerHTML = '<div class="empty">Tidak ada catatan yang cocok.</div>';
    return;
  }

  $("logBody").innerHTML = runs
    .map((run, idx) => {
      const status = runStatus(run);
      const title = run.start ? `Run ${run.start.time || ""}` : "Di luar run";
      return `<article class="runblock${idx === 0 ? "" : " closed"}">
        <button class="runhead" type="button">
          <span class="runcaret">›</span>
          <span class="runtime">${escapeHtml(title)}</span>
          <span class="runmeta">${escapeHtml(runMeta(run))}</span>
          <span class="runstatus ${status.cls}">${escapeHtml(status.text)}</span>
        </button>
        <div class="runrows">${run.entries.map(rowHtml).join("")}</div>
      </article>`;
    })
    .join("");

  $("logBody").querySelectorAll(".runhead").forEach((btn) => {
    btn.onclick = () => btn.parentElement.classList.toggle("closed");
  });
}

async function loadLog() {
  const q = LOG.day ? `?day=${encodeURIComponent(LOG.day)}` : "";
  const data = await api("/api/log" + q);
  const summary = data.summary || {};
  const days = summary.days || [];
  const sel = $("logDay");

  if (sel.dataset.signature !== days.join("|")) {
    sel.innerHTML = days.length
      ? days.map((d) => `<option value="${d}">${d}</option>`).join("")
      : '<option value="">hari ini</option>';
    sel.dataset.signature = days.join("|");
  }
  if (summary.day) sel.value = summary.day;

  LOG.entries = data.entries || [];
  $("logDownload").style.display = LOG.entries.length ? "" : "none";
  $("logDownload").href = "/api/log/download" + (summary.day ? `?day=${encodeURIComponent(summary.day)}` : "");

  renderStats(LOG.entries);
  renderFilters();
  renderLog();
}

/* ---------------- tampilan ---------------- */

function setView(view) {
  currentView = view;
  document.body.className = `view-${view}`;
  document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("active", t.dataset.view === view));
  $("concepts").classList.toggle("hidden", view !== "gallery");
  $("viewLog").classList.toggle("hidden", view !== "log");
  if (view === "log") loadLog().catch(() => {});
}

document.querySelectorAll(".tab").forEach((t) => {
  t.onclick = () => setView(t.dataset.view);
});

/* ---------------- poll ---------------- */

async function poll() {
  try {
    const state = await api("/api/state");
    lastState = state;
    renderGallery(state);
  } catch (e) {
    $("runMessage").textContent = "Server tidak merespons.";
  }
  if (currentView === "log") loadLog().catch(() => {});
}

/* ---------------- init ---------------- */

async function init() {
  config = await api("/api/config");
  $("sheetUrl").value = config.sheet_url || "";
  $("profileSelect").innerHTML = (config.profiles || [])
    .map((name, i) => `<option value="${i}" ${i === config.active_profile ? "selected" : ""}>${escapeHtml(name)}</option>`)
    .join("");
  poll();
  setInterval(poll, 2500);
}

$("btnStart").onclick = async () => {
  const r = await api("/api/start", { sheet_url: $("sheetUrl").value.trim() });
  if (!r.ok) alert(r.error || "Gagal memulai.");
  poll();
};
$("btnPause").onclick = () => api("/api/pause", {}).then(poll);
$("btnResume").onclick = () => api("/api/resume", {}).then(poll);
$("btnStop").onclick = () => api("/api/stop", {}).then(poll);
$("btnProfile").onclick = () => api("/api/profile", { index: parseInt($("profileSelect").value, 10) }).then(poll);
$("noticeAction").onclick = () => api("/api/resume", {}).then(poll);
$("onlyShort").onchange = () => lastState && renderGallery(lastState);

$("logDay").onchange = () => {
  LOG.day = $("logDay").value;
  loadLog().catch(() => {});
};
$("logSearch").oninput = () => {
  LOG.search = $("logSearch").value;
  renderLog();
};
$("logFilters").onclick = (ev) => {
  const chip = ev.target.closest("[data-cat]");
  if (!chip) return;
  LOG.category = chip.dataset.cat;
  renderFilters();
  renderLog();
};

document.addEventListener("click", (ev) => {
  const btn = ev.target.closest("button[data-act]");
  if (!btn) return;
  api("/api/decide", { filename: btn.dataset.file, action: btn.dataset.act }).then(poll);
});

init();
