"use strict";
const $ = (id) => document.getElementById(id);
const BUSY = ["logging_in", "listing", "scraping", "paused", "stopping"];
const selected = new Set();   // nodeids of checked titles
let lastTitles = [];
let lastBusy = false;
let lastPubUrl = null;

async function post(path, body) {
  try {
    const r = await fetch("/api" + path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {}),
    });
    if (r.status === 409) { const j = await r.json(); flash(`busy (${j.activity}) — try again when idle`); }
    return r.ok;
  } catch (e) { return false; }
}

let _flash = "";
function flash(msg) { _flash = msg; setTimeout(() => { if (_flash === msg) _flash = ""; }, 4000); }

// --- wire buttons -----------------------------------------------------------
$("login").onclick = () => post("/login");
$("confirm").onclick = () => post("/login/confirm");
$("go").onclick = () => { const u = $("puburl").value.trim(); if (u) post("/publication", { url: u }); };
$("scrapeAll").onclick = () => post("/scrape", { scope: "all" });
$("scrapeSel").onclick = () => {
  const ids = [...selected];
  if (ids.length) post("/scrape", { scope: "selected", nodeids: ids });
};
$("selAll").onchange = (e) => {
  if (e.target.checked) lastTitles.forEach((t) => selected.add(t.nodeid));
  else selected.clear();
  _rowsKey = "";
  renderTitles(lastTitles, lastBusy);
  updateSelUi();
};
$("pause").onclick = () => post("/pause");
$("resume").onclick = () => post("/resume");
$("stop").onclick = () => post("/stop");
$("retryAll").onclick = () => post("/retry", {});
$("build").onclick = () => post("/build");
$("applyPacing").onclick = () =>
  post("/pacing", { min: parseFloat($("pmin").value), max: parseFloat($("pmax").value) });
$("puburl").addEventListener("keydown", (e) => { if (e.key === "Enter") $("go").click(); });

// --- render -----------------------------------------------------------------
function focused(el) { return document.activeElement === el; }

function render(s) {
  const busy = BUSY.includes(s.activity);
  const havePub = !!s.publication_url;
  lastBusy = busy;
  if (s.publication_url !== lastPubUrl) { selected.clear(); lastPubUrl = s.publication_url; }

  $("loginDot").className = "dot" + (s.logged_in ? " on" : "");
  $("activity").textContent = _flash || s.activity;
  $("activity").className = "pill " + s.activity;
  $("publication").textContent = s.publication
    ? s.publication : "no publication loaded";

  $("login").disabled = busy;
  $("confirm").hidden = !s.can_confirm_login;
  $("go").disabled = busy;
  $("scrapeAll").disabled = busy || !havePub;
  $("retryAll").disabled = busy || !havePub;
  $("build").disabled = busy;
  $("pause").hidden = s.activity === "paused";
  $("resume").hidden = s.activity !== "paused";
  $("pause").disabled = s.activity !== "scraping";
  $("stop").disabled = !(s.activity === "scraping" || s.activity === "paused");

  if (!focused($("pmin"))) $("pmin").value = s.pacing.min;
  if (!focused($("pmax"))) $("pmax").value = s.pacing.max;

  // overall progress
  const ov = s.overall || {};
  const showOv = s.activity === "scraping" || s.activity === "paused" || (ov.total > 0);
  $("overall").hidden = !showOv;
  if (showOv) {
    const denom = ov.total || 1, n = (ov.done || 0) + (ov.failed || 0);
    $("overallBar").style.width = Math.min(100, (n / denom) * 100) + "%";
    $("overallText").textContent =
      `${ov.title ? ov.title + " — " : ""}${ov.done || 0} done · ${ov.failed || 0} failed · of ${ov.total || 0}`;
    $("current").textContent = s.current ? "→ " + s.current : "";
  }

  renderTitles(s.titles || [], busy);
  updateSelUi();

  const log = $("log");
  const atBottom = log.scrollTop + log.clientHeight >= log.scrollHeight - 20;
  log.textContent = (s.log || []).join("\n");
  if (atBottom) log.scrollTop = log.scrollHeight;
}

let _rowsKey = "";
function renderTitles(titles, busy) {
  const tbody = $("titleRows");
  lastTitles = titles;
  if (!titles.length) return;
  // rebuild only when counts/status/selection change (avoids clobbering clicks)
  const key = JSON.stringify(titles.map((t) => [t.done, t.failed, t.total, t.status]))
    + busy + "|" + [...selected].join(",");
  if (key === _rowsKey) return;
  _rowsKey = key;

  tbody.innerHTML = titles.map((t, i) => {
    const total = t.total == null ? "—" : t.total;
    const pct = t.total ? Math.min(100, (t.done / t.total) * 100) : 0;
    const cls = "badge " + String(t.status).replace(/ /g, "_");
    const canRetry = t.failed > 0 && !busy;
    return `<tr>
      <td class="sel"><input type="checkbox" class="rowsel" data-id="${t.nodeid}" ${selected.has(t.nodeid) ? "checked" : ""}></td>
      <td class="muted">${i + 1}</td>
      <td>${escapeHtml(t.name)}</td>
      <td class="num">${t.done}/${total}${t.failed ? ` · <span style="color:#e57">${t.failed}✗</span>` : ""}
        <div class="minibar"><div style="width:${pct}%"></div></div></td>
      <td><span class="${cls}">${escapeHtml(t.status)}</span></td>
      <td class="act">
        <button class="act-btn" ${busy ? "disabled" : ""} data-act="scrape" data-id="${t.nodeid}">Scrape</button>
        <button class="act-btn" ${canRetry ? "" : "disabled"} data-act="retry" data-id="${t.nodeid}">Retry</button>
      </td></tr>`;
  }).join("");

  tbody.querySelectorAll("button[data-act]").forEach((b) => {
    b.onclick = () => {
      const id = b.dataset.id;
      if (b.dataset.act === "scrape") post("/scrape", { scope: "title", nodeid: id });
      else post("/retry", { nodeid: id });
    };
  });
  tbody.querySelectorAll("input.rowsel").forEach((c) => {
    c.onchange = () => {
      if (c.checked) selected.add(c.dataset.id); else selected.delete(c.dataset.id);
      updateSelUi();
    };
  });
}

function updateSelUi() {
  const n = selected.size, totalT = lastTitles.length;
  const sel = $("scrapeSel");
  if (sel) {
    sel.textContent = n ? `▶ Scrape selected (${n})` : "▶ Scrape selected";
    sel.disabled = lastBusy || n === 0;
  }
  const all = $("selAll");
  if (all) { all.checked = n > 0 && n >= totalT; all.indeterminate = n > 0 && n < totalT; }
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"]/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
}

async function refresh() {
  try {
    const r = await fetch("/api/state");
    if (r.ok) render(await r.json());
  } catch (e) {
    $("activity").textContent = "offline (server stopped?)";
  }
}
refresh();
setInterval(refresh, 1000);
