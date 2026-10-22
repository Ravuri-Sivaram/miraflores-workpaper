/* Miraflores workpaper - vanilla JS front end. Every number is clickable; the drawer shows its trace. */
"use strict";
const S = {
  user: localStorage.getItem("wp.user") || "st-01",
  asOf: localStorage.getItem("wp.asof") || null,
  boot: null, cache: {},
};
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

// ------------------------------------------------------------------ api
async function api(path, opts = {}) {
  const r = await fetch(path, {
    ...opts,
    headers: { "Content-Type": "application/json", "X-User": S.user, "X-As-Of": S.asOf || "", ...(opts.headers || {}) },
  });
  if (!r.ok) {
    let msg = r.statusText;
    try { msg = (await r.json()).detail || msg; } catch (e) {}
    throw new Error(msg);
  }
  return r.json();
}
async function act(action, okMsg) {
  try {
    const res = await api("/api/action", { method: "POST", body: JSON.stringify(action) });
    toast(okMsg || "Saved", { undo: res.batch });
    await refresh();
    return res;
  } catch (e) { toast(e.message, { err: true }); return null; }
}
function toast(msg, o = {}) {
  const t = document.createElement("div");
  t.className = "t" + (o.err ? " err" : "");
  t.innerHTML = `<span>${esc(msg)}</span>`;
  if (o.undo) {
    const b = document.createElement("button"); b.textContent = "Undo";
    b.onclick = async () => { await api("/api/action", { method: "POST", body: JSON.stringify({ type: "undo", batch: o.undo }) }); t.remove(); toast("Undone"); refresh(); };
    t.appendChild(b);
  }
  $("#toast").appendChild(t);
  setTimeout(() => t.remove(), o.err ? 7000 : 6000);
}

// ------------------------------------------------------------------ formatting
function fmt(v, dec) {
  if (v === null || v === undefined || v === "") return '<span class="muted">-</span>';
  if (typeof v === "number") {
    const d = dec ?? (Math.abs(v) < 1 && v !== 0 && !Number.isInteger(v) ? 4 : Number.isInteger(v) ? 0 : 2);
    const s = Math.abs(v).toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d });
    return `<span class="num">${v < 0 ? "(" + s + ")" : s}</span>`;
  }
  if (typeof v === "boolean") return v ? "Yes" : "No";
  if (typeof v === "object") return `<span class="small muted">${esc(JSON.stringify(v).slice(0, 160))}</span>`;
  return esc(v);
}
const n0 = (v) => (typeof v === "number" ? v : 0);
function delta(a, b) {
  if (typeof a !== "number" || typeof b !== "number") return "";
  const d = a - b; if (!d) return '<span class="muted small">no change</span>';
  return `<span class="small ${d > 0 ? "" : ""}">${d > 0 ? "+" : "−"}${Math.abs(d).toLocaleString()}</span>`;
}
const sev = (s) => `<span class="pill ${s}">${esc(s)}</span>`;
const statusPill = (s) => `<span class="pill ${s === "resolved" || s === "accepted" ? "ok" : s === "open" ? "open" : "blue"}">${esc(s.replace("_", " "))}</span>`;
function refKind(r) {
  if (/^ev-/.test(r)) return "value"; if (/^D\d\d$/.test(r) || /^doc:/.test(r)) return "doc";
  if (/^(qa|msg|call)-/.test(r)) return "client"; if (/^py:/.test(r)) return "2024"; if (/^agency:/.test(r)) return "agency";
  if (/^asset:/.test(r)) return "asset"; if (/^household:/.test(r)) return "person"; if (/^ref:/.test(r)) return "param";
  if (/^F-|^DIAG-/.test(r)) return "finding"; if (/^R-/.test(r)) return "check"; if (/^timeline:/.test(r)) return "event";
  return "line";
}
const chip = (r, label) => `<span class="chip" data-ref="${esc(r)}"><span class="t">${refKind(r)}</span>${esc(label || r)}</span>`;
const chips = (arr) => (arr || []).map((r) => chip(r)).join(" ");
const who = (id) => (S.boot?.users.find((u) => u.id === id) || { name: id || "system" }).name;
const when = (iso) => (iso ? esc(iso.replace("T", " ").replace("Z", "").slice(0, 16)) : "");
const areaName = (a) => S.boot?.areas[a] || a;
function effectText(e) {
  if (!e || !Object.keys(e).length) return "";
  const p = [];
  if (e.federal_tax) p.push(`federal tax <b>${e.federal_tax > 0 ? "+" : "−"}${Math.abs(e.federal_tax).toLocaleString()}</b>`);
  if (e.california_tax) p.push(`CA tax <b>${e.california_tax > 0 ? "+" : "−"}${Math.abs(e.california_tax).toLocaleString()}</b>`);
  if (e.federal_balance && e.federal_balance !== e.federal_tax) p.push(`federal balance due <b>${e.federal_balance > 0 ? "+" : "−"}${Math.abs(e.federal_balance).toLocaleString()}</b>`);
  if (e.california_balance && e.california_balance !== e.california_tax) p.push(`CA balance <b>${e.california_balance > 0 ? "+" : "−"}${Math.abs(e.california_balance).toLocaleString()}</b>`);
  return p.length ? `<span class="effect">Est. effect: ${p.join(" · ")} <span class="muted">(rate method)</span></span>` : '<span class="effect muted">No tax effect</span>';
}

// ------------------------------------------------------------------ navigation
const STAFF_NAV = [
  ["overview", "Overview"], ["return", "Return"], ["documents", "Documents"], ["issues", "Findings & checks"],
  ["-", "Schedules"], ["sched/sales", "Sales detail", 1], ["sched/assets", "Depreciation", 1], ["sched/books", "Books → tax", 1],
  ["sched/state", "State split", 1], ["sched/k1", "K-1s", 1], ["sched/basis", "Basis by year", 1], ["sched/carryovers", "Carryovers", 1],
  ["return?area=rentals_and_sale", "Rentals & passive", 1], ["return?area=deductions_and_credits", "Deductions & credits", 1],
  ["sched/payments", "Payments", 1], ["sched/prior", "Prior preparer", 1], ["sched/whatif", "What-ifs", 1],
  ["-", "Work"], ["review", "Review & sign-off"], ["activity", "Activity & versions"], ["filing", "Filing & after"],
  ["client", "Client portal"], ["firm", "Firm"],
];
const CLIENT_NAV = [["client", "My return"], ["documents", "My documents"]];
function renderNav() {
  const me = S.boot.me;
  const nav = me.role === "client" ? CLIENT_NAV : STAFF_NAV;
  const cur = location.hash.slice(2) || "overview";
  $("#nav").innerHTML = nav.map(([id, label, sub]) => id === "-" ? `<div class="grp">${label}</div>` :
    `<a href="#/${id}" class="${sub ? "sub " : ""}${cur === id || cur.startsWith(id + "/") || (!id.includes("?") && cur.split("?")[0] === id && !cur.includes("area=")) ? "on" : ""}">${label}</a>`).join("");
}

const ROUTES = {
  overview: viewOverview, return: viewReturn, documents: viewDocuments, doc: viewDoc, issues: viewIssues, sched: viewSchedule,
  review: viewReview, activity: viewActivity, filing: viewFiling, notice: viewNotice, client: viewClient, firm: viewFirm, line: viewLinePage,
};
async function render() {
  const [route, ...rest] = (location.hash.slice(2).split("?")[0] || (S.boot.me.role === "client" ? "client" : "overview")).split("/");
  renderNav();
  const fn = ROUTES[route] || viewOverview;
  try { await fn(...rest.map(decodeURIComponent)); }
  catch (e) { $("#main").innerHTML = `<div class="card"><b>Could not load:</b> ${esc(e.message)}</div>`; console.error(e); }
}
async function refresh() {
  await render();
  const d = $("#drawer");
  if (d.classList.contains("open") && d.dataset.kind) openDrawer(d.dataset.kind, d.dataset.id, true);
}
window.addEventListener("hashchange", () => { $("#drawer").classList.remove("open"); $("#drawer").dataset.kind = ""; window.scrollTo(0, 0); render(); });
document.addEventListener("click", (e) => {
  const c = e.target.closest("[data-ref]");
  if (c) { e.preventDefault(); openRef(c.dataset.ref); return; }
  const l = e.target.closest("[data-line]");
  if (l && !e.target.closest("input,button,select,textarea,a")) { openDrawer("line", l.dataset.line); return; }
  const f = e.target.closest("[data-finding]");
  if (f && !e.target.closest("input,button,select,textarea,a,.chip")) { openDrawer("finding", f.dataset.finding); }
});

function openRef(ref) {
  const k = refKind(ref);
  if (k === "line") return openDrawer("line", ref);
  if (k === "finding") return openDrawer("finding", ref);
  if (k === "value") { return api(`/api/resolve?ref=${encodeURIComponent(ref)}`).then((r) => { location.hash = `#/doc/${r.doc}/${ref}`; }); }
  if (k === "doc") { const m = ref.match(/(D\d\d)(?:#page(\d+))?/); location.hash = `#/doc/${m[1]}${m[2] ? "/p" + m[2] : ""}`; return; }
  if (k === "check") { location.hash = "#/issues/checks"; return; }
  return openDrawer("ref", ref);
}

// ------------------------------------------------------------------ drawer
async function openDrawer(kind, id, keep) {
  const d = $("#drawer");
  d.dataset.kind = kind; d.dataset.id = id;
  d.classList.add("open"); d.setAttribute("aria-hidden", "false");
  if (!keep) $("#drawer-body").innerHTML = '<div class="loading">Loading…</div>';
  try {
    if (kind === "line") await drawLine(id);
    else if (kind === "finding") await drawFinding(id);
    else await drawRef(id);
  } catch (e) { $("#drawer-body").innerHTML = `<div class="card">${esc(e.message)}</div>`; }
}
$("#drawer-close").onclick = () => { $("#drawer").classList.remove("open"); $("#drawer").dataset.kind = ""; };
document.addEventListener("keydown", (e) => { if (e.key === "Escape") $("#drawer-close").click(); });

function formulaOf(t) {
  if (!t.inputs.length) return "";
  return `<div class="formula">= ${t.inputs.map((i) => esc(i.key)).join(" ⊕ ")}</div>`;
}

async function drawLine(key) {
  const t = await api(`/api/trace/${encodeURIComponent(key)}`);
  $("#drawer-crumbs").textContent = `${t.form} · line ${t.line} · ${areaName(t.area)}`;
  const adjusted = (t.adjusted ? `<div class="small">Engine v3: <span class="strike">${fmt(t.engine)}</span> → workpaper <span class="adj">${fmt(t.value)}</span>${t.rolled_up ? " <span class=\"pill blue\">rolled up from adjusted inputs</span>" : ""}</div>` : "")
    + (t.needs_engine ? `<div class="small" style="color:var(--warn)">⚠ An input changed, but this line uses a non-linear rule (${esc(t.rule)}). It is not recomputed here - the engine must re-run. Totals use the rate-based estimate.</div>` : "");
  const prior = t.prior !== null && t.prior !== undefined ? `<div class="small muted">2024: ${fmt(t.prior)} ${delta(t.value, t.prior)} ${t.prior_source ? chip(t.prior_source, "2024 source") : ""}</div>` : "";
  const src = t.sources.length ? t.sources.map(srcCard).join("") : '<div class="empty">No direct source - built from the lines below.</div>';
  const inputs = t.inputs.length ? `<table>${t.inputs.map((i) => `<tr class="click" data-line="${esc(i.key)}"><td class="num small">${esc(i.key)}</td><td>${esc(i.label)}${i.findings.length ? ' <span class="pill error">finding</span>' : ""}</td><td class="r">${i.adjusted ? `<span class="adj">${fmt(i.value)}</span>` : fmt(i.value)}</td></tr>`).join("")}</table>` : "";
  const parents = t.parents.length ? t.parents.map((p) => chip(p.key, `${p.key}`)).join(" ") : '<span class="muted small">Not used by another line (top of a chain).</span>';
  const hist = t.history.map((h) => `<tr><td>${h.version}</td><td class="small muted">${when(h.at)}</td><td class="r">${fmt(h.value)}</td><td class="small">${h.change ? esc(h.change.change) : ""} <span class="muted">${esc(h.trigger)}</span></td></tr>`).join("");
  $("#drawer-body").innerHTML = `
    <div class="small muted num">${esc(t.key)} · rule ${esc(t.rule)}</div>
    <div style="font-weight:650;font-size:16px">${esc(t.label)}</div>
    <div class="big">${fmt(t.value)}</div>${adjusted}${prior}
    ${t.note ? `<div class="small" style="margin-top:6px">📝 Engine note: ${esc(t.note)}</div>` : ""}
    ${t.findings_full.length ? `<div class="sec"><h3>Findings on this line</h3>${t.findings_full.map((f) => findingCard(f, true)).join("")}</div>` : ""}
    <div class="sec"><h3>Where it came from</h3>${src}</div>
    <div class="sec"><h3>How it was built</h3><div class="small"><b>${esc(t.rule)}</b> - ${esc(t.rule_text)}</div>${formulaOf(t)}${inputs}</div>
    <div class="sec"><h3>Where it goes</h3>${parents}</div>
    <div class="sec"><h3>History across engine versions</h3><table>${hist}</table></div>
    ${adjustBlock(t)}
    ${verifyBlock(key, t.verifications)}
    ${notesBlock(key, t.notes)}
    ${attachBlock(key, t.attachments)}
    ${t.judgments.length ? `<div class="sec"><h3>Judgment calls</h3>${t.judgments.map(judgmentRow).join("")}</div>` : ""}
    ${aiBlock(key)}`;
  wireForms(key, t);
}

function srcCard(s) {
  const head = `${chip(s.id)} <b>${esc(s.label)}</b>`;
  if (s.kind === "value") {
    const sn = s.snippet || {};
    const conf = s.confidence < 0.8 ? `<span class="pill warning">confidence ${s.confidence}</span>` : `<span class="small muted">confidence ${s.confidence}</span>`;
    return `<div class="src">${head}<div class="small">${esc(s.doc)} · ${esc(s.doc_title)} · page ${s.page} · field <span class="num">${esc(s.field)}</span> ${conf}
      ${(s.verified || []).map((v) => `<span class="pill ok">${esc(v.method.replaceAll("_", " "))}</span>`).join("")}
      ${s.findings.length ? `<span class="pill error">${s.findings.length} finding</span>` : ""}</div>
      <div class="snippet">${sn.clip_start ? "…" : ""}${sn.before || ""}<mark>${sn.hit || ""}</mark>${sn.after || ""}${sn.clip_end ? "…" : ""}${sn.not_located ? '\n<span style="color:var(--err)">⚠ value not found in the printed text</span>' : ""}</div>
      <div class="small">Read value: <b>${fmt(s.value)}</b> ${s.text_as_read !== undefined && s.text_as_read !== null ? `(text as read “${esc(s.text_as_read)}”)` : ""} · <a data-ref="${esc(s.id)}">open document at this field →</a></div></div>`;
  }
  return `<div class="src">${head}<div class="small">${typeof s.value === "object" ? `<span class="muted">${esc(JSON.stringify(s.value).slice(0, 300))}</span>` : fmt(s.value)}</div>${s.missing ? '<div class="pill error">unresolved reference</div>' : ""}</div>`;
}

function adjustBlock(t) {
  const rows = t.adjustments.map((a) => `<div class="src"><div class="row"><b>${fmt(a.from)} → ${fmt(a.to)}</b> ${statusPill(a.status)} ${a.needs_partner ? '<span class="pill warning">partner sign-off</span>' : ""}<span class="spacer"></span>
    ${a.status === "proposed" ? `<button class="small" data-approve="${a.id}">Approve</button><button class="small" data-reject="${a.id}">Reject</button>` : ""}
    ${a.status === "approved" ? `<button class="small" data-revert="${a.id}">Revert</button>` : ""}</div>
    <div class="small">${esc(a.reason)}</div><div class="small muted">Proposed by ${esc(who(a.proposed_by))} ${when(a.at)}${a.approved_by ? ` · approved by ${esc(who(a.approved_by))}` : ""}${a.finding ? " · " + chip(a.finding) : ""}</div>${effectText(a.effect)}</div>`).join("");
  const cats = Object.keys(t.rates).concat(["payment_federal", "payment_california", "none"]);
  return `<div class="sec"><h3>Adjustments (original is never overwritten)</h3>${rows || '<div class="empty">None.</div>'}
    <details><summary class="small"><a>Propose an adjustment</a></summary><div class="form" id="f-adj">
      <div class="row"><label class="small">New value<input id="adj-to" type="number" step="any" value="${typeof t.value === "number" ? t.value : ""}"></label>
      <label class="small">Tax-effect category<select id="adj-cat">${cats.map((c) => `<option>${c}</option>`).join("")}</select></label></div>
      <textarea id="adj-why" placeholder="Why (required) - cite the evidence"></textarea>
      <div class="row"><span class="small muted" id="adj-est"></span><span class="spacer"></span><button class="primary" id="adj-go">Propose</button></div></div></details></div>`;
}
function verifyBlock(target, list) {
  return `<div class="sec"><h3>Verification</h3>${(list || []).map((v) => `<div class="small">✔ <b>${esc(v.method.replaceAll("_", " "))}</b> by ${esc(who(v.by))} ${when(v.at)} - ${esc(v.note)} ${chips(v.evidence)}</div>`).join("") || '<div class="empty">Not yet verified.</div>'}
    <div class="row form"><select id="ver-m">${S.boot.verification_types.map((v) => `<option value="${v}">${v.replaceAll("_", " ")}</option>`).join("")}</select>
    <input id="ver-note" placeholder="How (e.g. agreed to D09 box 1)"><button id="ver-go">Record</button></div></div>`;
}
function notesBlock(target, notes) {
  return `<div class="sec"><h3>Notes, review notes & client questions</h3>${(notes || []).map(noteRow).join("") || '<div class="empty">None.</div>'}
    <div class="form"><textarea id="note-text" placeholder="Write a note…"></textarea><div class="row">
    <select id="note-kind"><option value="comment">Comment</option><option value="review_note">Review note (blocks filing)</option><option value="client_question">Question to client (becomes evidence)</option></select>
    <span class="spacer"></span><button id="note-go">Add</button></div></div></div>`;
}
function noteRow(n) {
  return `<div class="src"><div class="row">${n.kind === "review_note" ? '<span class="pill error">review note</span>' : n.kind === "client_question" ? '<span class="pill blue">client question</span>' : '<span class="pill info">comment</span>'}
    ${statusPill(n.status)} <span class="small muted">${esc(who(n.by))} ${when(n.at)}</span><span class="spacer"></span>
    ${n.status !== "cleared" ? `<button class="small" data-clear="${n.id}">Clear</button>` : ""}</div>
    <div>${esc(n.text)}</div>${n.thread.map((r) => `<div class="small" style="margin-left:14px">↳ <b>${esc(who(r.by))}</b>: ${esc(r.text)} <span class="muted">${when(r.at)}</span></div>`).join("")}
    ${n.status !== "cleared" ? `<div class="row" style="margin-top:4px"><input data-reply-text="${n.id}" placeholder="Reply…" style="flex:1"><button class="small" data-reply="${n.id}">Reply</button></div>` : ""}</div>`;
}
function attachBlock(target, list) {
  return `<div class="sec"><h3>Attached evidence</h3>${(list || []).map((a) => `<div class="small">📎 ${a.file ? `<a href="/api/files/${a.file.id}" target="_blank">${esc(a.file.name)}</a> (${Math.round(a.file.size / 1024)} KB)` : ""} ${esc(a.note)} <span class="muted">${esc(who(a.by))} ${when(a.at)}</span></div>`).join("") || '<div class="empty">None.</div>'}
    <div class="row form"><input type="file" id="att-file"><input id="att-note" placeholder="Note or message to attach"><button id="att-go">Attach</button></div></div>`;
}
function judgmentRow(j) {
  return `<div class="src"><b>${esc(j.topic || "Judgment")}</b>: ${esc(j.decision)}<div class="small">Authority: ${esc(j.authority)}</div><div class="small muted">${esc(who(j.by))} ${when(j.at)}</div></div>`;
}
function aiBlock(target) {
  return `<div class="sec"><h3>Explain</h3><button id="ai-go" ${S.boot.ai ? "" : "disabled"}>Explain with AI</button>
    <span class="small muted">${S.boot.ai ? `Model ${esc(S.boot.model)} - cites evidence ids, never produces numbers.` : "AI is off (no API key). The trace above is the full, deterministic explanation."}</span><div id="ai-out"></div></div>`;
}
function renderAI(res) {
  if (res.mode !== "ai") return `<div class="ai">${esc(res.message)}</div>`;
  const html = esc(res.text).replace(/\[\[([^\]]+)\]\]/g, (_, r) => chip(r));
  return `<div class="ai">${html}</div>${res.rejected_citations?.length ? `<div class="small" style="color:var(--err)">Removed ${res.rejected_citations.length} citation(s) that do not exist in the packet: ${esc(res.rejected_citations.join(", "))}</div>` : ""}`;
}

function wireForms(target, t) {
  const on = (id, fn) => { const el = document.getElementById(id); if (el) el.onclick = fn; };
  const est = () => {
    if (!t || !$("#adj-to")) return;
    const to = parseFloat($("#adj-to").value), cat = $("#adj-cat").value, r = t.rates[cat];
    const d = to - (typeof t.value === "number" ? t.value : 0);
    $("#adj-est").textContent = r && !isNaN(d) ? `≈ federal ${Math.round(d * r.federal_per_dollar).toLocaleString()} · CA ${Math.round(d * r.california_per_dollar).toLocaleString()}` : "";
  };
  if ($("#adj-to")) { $("#adj-to").oninput = est; $("#adj-cat").onchange = est; }
  on("adj-go", () => act({ type: "adjustment.propose", target, data: { to: parseFloat($("#adj-to").value), category: $("#adj-cat").value, reason: $("#adj-why").value } }, "Adjustment proposed"));
  on("ver-go", () => act({ type: "verify", target, data: { method: $("#ver-m").value, note: $("#ver-note").value } }, "Verification recorded"));
  on("note-go", () => act({ type: "note.add", target, data: { text: $("#note-text").value, kind: $("#note-kind").value, to: $("#note-kind").value === "client_question" ? "cu-01" : null } }, "Note added"));
  on("att-go", async () => {
    const f = $("#att-file").files[0];
    let file = null;
    if (f) { const b64 = await new Promise((res) => { const r = new FileReader(); r.onload = () => res(r.result.split(",")[1]); r.readAsDataURL(f); }); file = { name: f.name, mime: f.type, b64 }; }
    act({ type: "attach", target, data: { note: $("#att-note").value, file } }, "Attached");
  });
  on("ai-go", async () => { $("#ai-out").innerHTML = '<div class="loading">Asking…</div>'; const r = await api("/api/ai/explain", { method: "POST", body: JSON.stringify({ target }) }); $("#ai-out").innerHTML = renderAI(r); });
  wireRowButtons($("#drawer-body"));
}
function wireRowButtons(root) {
  $$("[data-approve]", root).forEach((b) => (b.onclick = () => act({ type: "adjustment.approve", target: b.dataset.approve }, "Approved")));
  $$("[data-reject]", root).forEach((b) => (b.onclick = () => act({ type: "adjustment.reject", target: b.dataset.reject, data: { note: prompt("Reason for rejecting?") || "" } }, "Rejected")));
  $$("[data-revert]", root).forEach((b) => (b.onclick = () => act({ type: "adjustment.revert", target: b.dataset.revert }, "Reverted to engine value")));
  $$("[data-clear]", root).forEach((b) => (b.onclick = () => act({ type: "note.clear", target: b.dataset.clear }, "Cleared")));
  $$("[data-reply]", root).forEach((b) => (b.onclick = () => act({ type: "note.reply", target: b.dataset.reply, data: { text: $(`[data-reply-text="${b.dataset.reply}"]`, root).value } }, "Reply saved")));
  $$("[data-propose]", root).forEach((b) => (b.onclick = () => proposeFinding(b.dataset.propose)));
  $$("[data-fset]", root).forEach((b) => (b.onclick = () => {
    const [fid, st] = b.dataset.fset.split("|");
    const note = st === "resolved" ? "" : prompt(st === "accepted" ? "Why accept without change? (required)" : "Note") || "";
    act({ type: "finding.set", target: fid, data: { status: st, note } }, "Finding updated");
  }));
  $$("[data-signoff]", root).forEach((b) => (b.onclick = () => { const [area, level] = b.dataset.signoff.split("|"); act({ type: "signoff", target: area, data: { level } }, "Signed off"); }));
}

// ------------------------------------------------------------------ findings
function findingCard(f, compact) {
  const st = f.state || { status: "open" };
  const adj = f.adjustments.length ? `<div class="small" style="margin-top:4px">${f.adjustments.map((a) => `${chip(a.line)} ${fmt(a.from)} → <b>${fmt(a.to)}</b> <span class="muted">${esc(a.note || "")}</span>`).join("<br>")}</div>` : "";
  return `<div class="card finding ${f.severity}" data-finding="${esc(f.id)}">
    <div class="row">${sev(f.severity)} ${statusPill(st.status)} <span class="small muted">${esc(f.kind.replaceAll("_", " "))} · ${esc(areaName(f.area))}</span><span class="spacer"></span><span class="small num muted">${esc(f.id)}</span></div>
    <div class="title">${esc(f.title)}</div>
    ${compact ? "" : `<div class="small">${esc(f.summary)}</div>`}
    ${adj}<div>${effectText(f.effect)}</div>
    ${f.judgment ? `<div class="small"><b>Judgment:</b> ${esc(f.judgment)}</div>` : ""}
    ${compact ? "" : `<div class="small">Evidence: ${chips(f.evidence)}</div><div class="how">How it surfaced: ${esc(f.how_found)}</div>`}
  </div>`;
}
async function drawFinding(id) {
  const all = await getFindings();
  const f = all.findings.find((x) => x.id === id);
  $("#drawer-crumbs").textContent = `Finding · ${areaName(f.area)}`;
  const st = f.state;
  $("#drawer-body").innerHTML = `${findingCard(f)}
    ${f.recommendation ? `<div class="card"><b>Recommendation:</b> ${esc(f.recommendation)}</div>` : ""}
    ${f.what_if ? `<div class="card small">Engine what-if <b>${esc(f.what_if.id)}</b>: federal ${fmt(f.what_if.federal)} · California ${fmt(f.what_if.california)}</div>` : ""}
    <div class="sec"><h3>Status</h3><div>${statusPill(st.status)} ${st.by ? `by ${esc(who(st.by))} ${when(st.at)}` : ""} ${esc(st.note || "")}</div>
    <div class="row" style="margin-top:6px">
      ${f.adjustments.length ? `<button class="primary" data-propose="${esc(f.id)}">Propose these adjustments</button>` : ""}
      <button data-fset="${esc(f.id)}|resolved">Mark resolved</button><button data-fset="${esc(f.id)}|accepted">Accept without change…</button><button data-fset="${esc(f.id)}|open">Reopen</button></div></div>
    <div class="sec"><h3>Evidence</h3><div id="f-ev"></div></div>
    ${f.judgment ? `<div class="sec"><h3>Record the judgment</h3><div class="form"><input id="j-topic" value="${esc(f.title)}"><textarea id="j-dec" placeholder="Decision">${esc(f.judgment)}</textarea><input id="j-auth" placeholder="Authority relied on (required by firm rule)"><button id="j-go">Record judgment</button></div></div>` : ""}
    <div class="sec"><h3>Ask someone</h3><div class="row"><button id="dq" ${S.boot.ai ? "" : "disabled"}>Draft client question (AI)</button><button id="dr" ${S.boot.ai ? "" : "disabled"}>Draft review note (AI)</button></div><div id="ai-out"></div>
      <div class="form"><textarea id="note-text" placeholder="Question or note"></textarea><div class="row"><select id="note-kind"><option value="client_question">Question to client</option><option value="review_note">Review note (blocks filing)</option><option value="comment">Comment</option></select><span class="spacer"></span><button id="note-go">Send</button></div></div></div>
    ${aiBlock(id)}`;
  const evs = await Promise.all(f.evidence.slice(0, 12).map((r) => api(`/api/resolve?ref=${encodeURIComponent(r)}`).catch(() => ({ id: r, label: r, kind: "unknown" }))));
  $("#f-ev").innerHTML = evs.map(srcCard).join("");
  const target = f.lines[0] || f.evidence[0] || id;
  wireForms(target, null);
  if ($("#j-go")) $("#j-go").onclick = () => act({ type: "judgment.record", target: f.lines[0] || id, data: { topic: $("#j-topic").value, decision: $("#j-dec").value, authority: $("#j-auth").value } }, "Judgment recorded");
  const draft = (kind) => async () => { $("#ai-out").innerHTML = '<div class="loading">Drafting…</div>'; const r = await api("/api/ai/draft", { method: "POST", body: JSON.stringify({ finding: id, kind }) }); $("#ai-out").innerHTML = renderAI(r); if (r.text) $("#note-text").value = r.text.replace(/\[\[|\]\]/g, ""); };
  $("#dq").onclick = draft("client_question"); $("#dr").onclick = draft("review_note");
  $("#ai-go").onclick = async () => { $$("#ai-out")[1] && ($$("#ai-out")[1].innerHTML = "…"); const r = await api("/api/ai/explain", { method: "POST", body: JSON.stringify({ target: id }) }); $$("#ai-out").slice(-1)[0].innerHTML = renderAI(r); };
}
async function proposeFinding(fid, quiet) {
  const all = await getFindings();
  const f = all.findings.find((x) => x.id === fid);
  return act({ type: "bulk", actions: f.adjustments.map((a) => ({ type: "adjustment.propose", target: a.line, data: { from: a.from, to: a.to, category: a.category, rates: a.rates, reason: `${f.title}: ${a.note || ""}`, finding: fid } })) },
    quiet || `${f.adjustments.length} adjustment(s) proposed from ${fid}`);
}
async function getFindings() { return api("/api/findings"); }

async function drawRef(ref) {
  const r = await api(`/api/resolve?ref=${encodeURIComponent(ref)}`);
  $("#drawer-crumbs").textContent = r.kind;
  let reveal = "";
  if (r.kind === "household") reveal = `<button id="rv">Reveal full SSN / PIN (logged)</button><pre id="rv-out" class="snippet"></pre>`;
  $("#drawer-body").innerHTML = `${srcCard(r)}${reveal}${r.findings?.length ? `<h3>Findings citing this</h3>${chips(r.findings)}` : ""}${notesBlock(ref, [])}${attachBlock(ref, [])}`;
  if ($("#rv")) $("#rv").onclick = async () => { try { const v = await api("/api/reveal", { method: "POST", body: JSON.stringify({ ref, reason: "verify identity" }) }); $("#rv-out").textContent = JSON.stringify(v, null, 1); toast("Unmasked view logged"); } catch (e) { toast(e.message, { err: true }); } };
  wireForms(ref, null);
}

// ------------------------------------------------------------------ overview
async function viewOverview() {
  const o = await api("/api/overview");
  $("#stage").textContent = o.stage;
  const w = o.summary.workpaper, e = o.summary.engine, p = o.summary.pending;
  const kpi = (label, eng, wp, pend, neg) => `<div class="card kpi"><div class="k">${label}</div><div class="v">${fmt(wp)}</div>
    <div class="d muted">engine v3 ${fmt(eng)} ${wp !== eng ? `· approved changes ${delta(wp, eng)}` : ""}</div>${pend ? `<div class="d" style="color:var(--warn)">pending ${pend > 0 ? "+" : "−"}${Math.abs(Math.round(pend)).toLocaleString()}</div>` : ""}</div>`;
  const sevc = o.severity;
  $("#main").innerHTML = `
    <h1>Okafor-Lindqvist · 2025 · MFJ</h1>
    <p class="lede">Moved California → Texas on 07/15/2025. The return below is engine version v3 (06/23/2026) plus every adjustment the team has approved. Pending changes are shown separately. Click any number to see where it came from.</p>
    <div class="cards">
      ${kpi("Federal total tax", e.federal.total_tax, w.federal_total_tax, p.federal_tax)}
      ${kpi("Federal balance due (refund)", e.federal.balance_due, w.federal_balance_due, p.federal_balance)}
      ${kpi("California total tax", e.california.total_tax, w.california_total_tax, p.california_tax)}
      ${kpi("California balance due (refund)", e.california.balance_due, w.california_balance_due, p.california_balance)}
    </div>
    <div class="grid2">
      <div><h2>Return summary vs 2024</h2><table><tr><th>Line</th><th class="r">2024</th><th class="r">2025</th><th class="r">Change</th></tr>
        ${o.lines.map((l) => `<tr class="click" data-line="${esc(l.key)}"><td>${esc(l.label)} <span class="small muted num">${esc(l.key)}</span>${l.findings.length ? ' <span class="pill error">!</span>' : ""}</td><td class="r">${fmt(l.prior)}</td><td class="r">${l.adjusted ? `<span class="adj">${fmt(l.value)}</span>` : fmt(l.value)}</td><td class="r">${delta(l.value, l.prior)}</td></tr>`).join("")}
      </table></div>
      <div><h2>What blocks filing</h2><div class="card">${o.gates.map(gateRow).join("")}</div>
        <h2>Open findings</h2><div class="row">${["error", "warning", "judgment", "info"].map((s) => `<a href="#/issues" class="pill ${s}">${sevc[s] || 0} ${s}</a>`).join(" ")}</div>
        ${o.since_last_look.since ? `<p class="small">Since you last looked (${when(o.since_last_look.since)}): <a href="#/activity">${o.since_last_look.count} change(s) by others</a></p>` : `<p class="small muted">First visit - <a href="#/activity">activity</a> will show what changes after you mark it seen.</p>`}
        <h2>Related returns & years</h2><div class="card small">${S.boot.client.related_returns.map((r) => `<div>• <b>${esc(r.who)}</b> - ${esc(r.return)} <span class="muted">(${esc(r.prepared_by)}; in packet: ${esc(String(r.in_this_packet))})</span></div>`).join("")}
          <div>• <b>2024 return</b> - ${esc(S.boot.client.prior_preparer)} ${chip("D06")} ${chip("D07")} · <a href="#/sched/prior">what the prior preparer did vs what we changed</a></div>
          <div>• <b>2026</b> - <a href="#/filing">carry-forward and estimates plan</a></div></div>
        <h2>My items</h2>${o.my_items.length ? o.my_items.map((m) => `<div class="small">• ${m.target ? chip(m.target) : ""} ${esc(m.text)}</div>`).join("") : '<div class="empty">Nothing waiting on you.</div>'}
      </div>
    </div>
    <h2>Areas</h2>
    <div class="cards">${o.areas.map((a) => `<div class="card click" onclick="location.hash='#/return?area=${a.id}'">
      <div class="row"><b>${esc(a.label)}</b><span class="spacer"></span>${a.errors ? `<span class="pill error">${a.errors} error</span>` : a.open_findings ? `<span class="pill warning">${a.open_findings} open</span>` : '<span class="pill ok">clear</span>'}</div>
      <div class="small">${["preparer", "reviewer", "partner"].map((l) => soText(a.signoff[l], l)).join(" · ")}</div>
      ${a.budget ? `<div class="row small"><div class="bar ${a.hours > a.budget ? "over" : ""}" style="flex:1"><i style="width:${Math.min(100, (a.hours / a.budget) * 100)}%"></i></div>${a.hours}/${a.budget} h</div>` : ""}</div>`).join("")}</div>`;
}
const gateRow = (g) => `<div class="gate"><span class="i">${g.ok ? "✅" : "⛔"}</span><div><div>${esc(g.label)}</div>${!g.ok && g.detail ? `<div class="small muted">${esc(g.detail)}</div>` : ""}</div></div>`;
function soText(s, level) {
  if (!s) return `<span class="so muted">${level}: -</span>`;
  return s.valid ? `<span class="so valid">${level}: ✔ ${esc(who(s.by).split(" ")[0])}</span>` : `<span class="so reopened" title="Changed since sign-off: ${esc((s.changed || []).join(", "))}">${level}: reopened</span>`;
}

// ------------------------------------------------------------------ return
async function viewReturn() {
  const params = new URLSearchParams(location.hash.split("?")[1] || "");
  const form = params.get("form") || "", area = params.get("area") || "";
  const rows = await api(`/api/lines?${form ? "form=" + encodeURIComponent(form) : ""}${area ? "&area=" + area : ""}`);
  const forms = S.boot.forms;
  let cur = "";
  const body = rows.map((l) => {
    let g = "";
    if (l.form !== cur) { cur = l.form; g = `<tr class="grp"><td colspan="8">${esc(l.form)} ${l.jurisdiction === "california" ? "(California)" : ""}</td></tr>`; }
    return g + `<tr class="click" data-line="${esc(l.key)}"><td class="num small">${esc(l.line)}</td><td>${esc(l.label)}
      ${l.findings.length ? ` <span class="pill error">${l.findings.length} finding</span>` : ""}${l.pending ? ' <span class="pill warning">pending</span>' : ""}${l.needs_engine ? ' <span class="pill warning" title="Inputs changed; engine re-run needed">re-run</span>' : ""}${l.verified.length ? ' <span class="pill ok">✔</span>' : ""}${l.notes ? ` <span class="pill blue">${l.notes} note</span>` : ""}</td>
      <td class="r">${fmt(l.prior)}</td><td class="r small muted">${fmt(l.versions.v1)}</td><td class="r small muted">${fmt(l.versions.v2)}</td><td class="r">${fmt(l.engine)}</td>
      <td class="r">${l.adjusted ? `<span class="adj">${fmt(l.value)}</span>` : ""}</td><td class="small muted">${esc(areaName(l.area))}</td></tr>`;
  }).join("");
  $("#main").innerHTML = `<h1>Return</h1><p class="lede">Every line of engine v3 with 2024, v1 and v2 alongside. Blue = changed by an approved adjustment. Click a line for its full trace.</p>
    <div class="row noprint"><select id="ff"><option value="">All forms</option>${forms.map((f) => `<option ${f === form ? "selected" : ""}>${f}</option>`).join("")}</select>
    <select id="fa"><option value="">All areas</option>${Object.entries(S.boot.areas).map(([k, v]) => `<option value="${k}" ${k === area ? "selected" : ""}>${v}</option>`).join("")}</select>
    <span class="small muted">${rows.length} lines${!form ? " (260 direct-indexing lots summarized under Sales detail)" : ""}</span></div><br>
    <table><tr><th>Line</th><th>Description</th><th class="r">2024</th><th class="r">v1</th><th class="r">v2</th><th class="r">Engine v3</th><th class="r">Workpaper</th><th>Area</th></tr>${body}</table>`;
  $("#ff").onchange = $("#fa").onchange = () => { location.hash = `#/return?form=${$("#ff").value}&area=${$("#fa").value}`; };
}
async function viewLinePage(key) { await viewReturn(); openDrawer("line", key); }

// ------------------------------------------------------------------ documents
async function viewDocuments() {
  const docs = await api("/api/documents");
  const cnt = docs.reduce((m, d) => ((m[d.disposition.status] = (m[d.disposition.status] || 0) + 1), m), {});
  $("#main").innerHTML = `<h1>Document index</h1><p class="lede">All ${docs.length} documents with where each one landed - or why it didn't. Completeness requires every document to have a disposition.</p>
    <div class="row">${Object.entries(cnt).map(([k, v]) => `<span class="pill ${k === "needs_reason" ? "warning" : k === "used" ? "ok" : "info"}">${v} ${k.replaceAll("_", " ")}</span>`).join(" ")}
    <span class="spacer"></span>${S.boot.me.role !== "client" ? '<button id="acc-all">Confirm all suggested reasons</button>' : ""}</div><br>
    <table><tr><th>Doc</th><th>Title</th><th>Received</th><th>Values</th><th>Where it landed / why not</th><th>Flags</th></tr>
    ${docs.map((d) => `<tr class="click" onclick="location.hash='#/doc/${d.id}'"><td class="num">${d.id}</td><td>${esc(d.title)}<div class="small muted">${esc(d.issuer)} · ${esc(d.belongs_to)}</div></td>
      <td class="small">${when(d.received_at).slice(0, 10)}</td><td class="r">${d.values}${d.low_conf ? ` <span class="pill warning">${d.low_conf} low</span>` : ""}</td>
      <td class="small"><span class="pill ${d.disposition.status === "needs_reason" ? "warning" : d.disposition.status === "used" ? "ok" : "info"}">${esc(d.disposition.status.replaceAll("_", " "))}</span> ${esc(d.disposition.reason || "")}${d.disposition.suggested ? ' <span class="muted">(suggested)</span>' : ""}</td>
      <td>${d.findings.length ? `<span class="pill error">${d.findings.length} finding</span>` : ""}${d.corrected ? ' <span class="pill blue">CORRECTED</span>' : ""}${d.extraction.status !== "extracted" ? ` <span class="pill warning">${esc(d.extraction.status.replace("_", " "))}</span>` : ""}${d.verified ? ' <span class="pill ok">✔</span>' : ""}</td></tr>`).join("")}</table>`;
  if ($("#acc-all")) $("#acc-all").onclick = () => act({ type: "bulk", actions: docs.filter((d) => d.disposition.status === "needs_reason" && d.disposition.reason).map((d) => ({ type: "doc.disposition", target: d.id, data: { reason: d.disposition.reason } })) }, "Dispositions confirmed (bulk)");
}

async function viewDoc(id, focus) {
  const d = await api(`/api/documents/${id}`);
  const pageFocus = focus && focus.startsWith("p") ? parseInt(focus.slice(1)) : null;
  const evFocus = focus && focus.startsWith("ev-") ? focus : null;
  const pages = d.pages.map((pg) => {
    const spans = pg.anchors.filter((a) => a.span).sort((a, b) => a.span[0] - b.span[0]);
    let html = "", pos = 0;
    for (const a of spans) {
      if (a.span[0] < pos) continue;
      const cls = a.findings.length ? "bad" : a.confidence < 0.8 ? "low" : !a.lines.length ? "unused" : "";
      html += esc(pg.text.slice(pos, a.span[0])) + `<span class="a ${cls} ${a.ev === evFocus ? "sel" : ""}" data-ev="${a.ev}" title="${esc(a.label)} · ${a.ev} · conf ${a.confidence}">${esc(pg.text.slice(a.span[0], a.span[1]))}</span>`;
      pos = a.span[1];
    }
    html += esc(pg.text.slice(pos));
    const unread = d.extraction.status === "partial" && !pg.anchors.length && /\d/.test(pg.text) && !/no further transactions|no amounts/.test(pg.text);
    const blank = /no further transactions|instructions and supplemental information/.test(pg.text) && !pg.anchors.length;
    if (blank && !pageFocus) return `<div class="small muted" style="margin:-8px 0 8px">Page ${pg.page}: ${esc(pg.text.slice(0, 80))}</div>`;
    return `<div class="paper" id="pg${pg.page}"><span class="pno">${esc(d.id)} · page ${pg.page}</span>${unread ? '<div class="unread">⚠ Not read by the pipeline - values on this page are not on the return</div>' : ""}${html}</div>`;
  }).join("");
  const disp = d.disposition;
  $("#main").innerHTML = `<div class="row"><a href="#/documents">← Documents</a></div>
    <h1>${esc(d.id)} · ${esc(d.title)}</h1>
    <p class="lede small">${esc(d.meta.issuer)} · ${esc(d.meta.document_type)} · received ${when(d.meta.received_at)} by ${esc(d.meta.uploaded_by)} · file ${esc(d.meta.file_name)} · ${d.meta.page_count} page(s)
      ${d.meta.marked_corrected ? '<span class="pill blue">CORRECTED</span>' : ""} · extraction <b>${esc(d.extraction.status)}</b> ${d.extraction.reason ? "- " + esc(d.extraction.reason) : ""}
      ${d.masked ? '<br><span class="pill warning">Sensitive values masked for your role</span>' : ""}</p>
    <div class="legend small"><span><i style="background:var(--accent)"></i>on the return</span><span><i style="background:#999"></i>extracted, not used</span><span><i style="background:var(--warn)"></i>low confidence</span><span><i style="background:var(--err)"></i>finding</span>
      <span class="muted">Simulated page rendered from printed_content - ${esc(d.source.note)}</span></div><br>
    <div class="docwrap"><div>${pages}</div>
      <div class="fieldpanel"><div class="card" id="fp"><b>Where this document landed</b>
        <div class="small"><span class="pill ${disp.status === "needs_reason" ? "warning" : "ok"}">${esc(disp.status.replaceAll("_", " "))}</span> ${esc(disp.reason || "")}</div>
        <div style="margin-top:6px">${disp.used_by.map((k) => chip(k)).join(" ") || '<span class="small muted">No return lines.</span>'}</div>
        ${S.boot.me.role !== "client" ? `<div class="row form"><input id="disp" placeholder="Why it isn't on the return" value="${esc(disp.suggested ? disp.reason || "" : "")}"><button id="disp-go">Save</button></div>` : ""}
        <div class="small muted" style="margin-top:6px">Click a highlighted value for its details.</div></div>
        <div class="card" id="fdetail" style="margin-top:10px"><div class="empty">No field selected.</div></div>
        ${d.findings.length ? `<h3>Findings</h3>${d.findings.map((f) => findingCard(f, true)).join("")}` : ""}
        <div class="card" style="margin-top:10px">${verifyBlock(id, d.verifications)}${notesBlock(id, d.notes)}${attachBlock(id, d.attachments)}</div>
      </div></div>`;
  const byEv = Object.fromEntries(d.pages.flatMap((p) => p.anchors.map((a) => [a.ev, a])));
  const unloc = d.pages.flatMap((p) => p.anchors.filter((a) => !a.span));
  if (unloc.length) $("#fp").insertAdjacentHTML("beforeend", `<div class="small" style="margin-top:6px;color:var(--warn)">${unloc.length} extracted value(s) not found in the printed text: ${unloc.map((a) => `<a data-show="${a.ev}">${a.ev}</a>`).join(", ")}</div>`);
  const show = (ev) => {
    const a = byEv[ev]; if (!a) return;
    $$(".paper span.a").forEach((s) => s.classList.toggle("sel", s.dataset.ev === ev));
    $("#fdetail").innerHTML = `<div class="small muted num">${a.ev} · field ${esc(a.field)}</div><div><b>${esc(a.label)}</b></div><div class="big" style="font-size:20px">${fmt(a.value)}</div>
      <div class="small">Confidence ${a.confidence} ${a.confidence < 0.8 ? '<span class="pill warning">low</span>' : ""} · type ${esc(a.type)} · position ${esc(JSON.stringify(a.position))}</div>
      <h3>Lands on</h3>${a.lines.map((k) => chip(k)).join(" ") || '<span class="small muted">Not used on the return.</span>'}
      ${a.findings.length ? `<h3>Findings</h3>${chips(a.findings)}` : ""}
      ${a.verified.length ? `<div class="small">✔ ${a.verified.join(", ")}</div>` : ""}
      ${S.boot.me.role !== "client" ? `<div class="row" style="margin-top:6px"><button class="small" id="v-src">Mark agreed to source</button></div>` : ""}`;
    if ($("#v-src")) $("#v-src").onclick = () => act({ type: "verify", target: ev, data: { method: "agreed_to_source_document", note: `Agreed to ${id} page ${a.span ? "" : "(not located) "}` } }, "Verified");
    wireRowButtons($("#fdetail"));
  };
  $$(".paper span.a").forEach((s) => (s.onclick = () => show(s.dataset.ev)));
  $$("[data-show]").forEach((s) => (s.onclick = () => show(s.dataset.show)));
  if ($("#disp-go")) $("#disp-go").onclick = () => act({ type: "doc.disposition", target: id, data: { reason: $("#disp").value } }, "Disposition saved");
  wireForms(id, null);
  if (evFocus) { show(evFocus); const el = $(`.paper span.a[data-ev="${evFocus}"]`); el && el.scrollIntoView({ block: "center" }); }
  if (pageFocus) { const el = $(`#pg${pageFocus}`); el && el.scrollIntoView(); }
}

// ------------------------------------------------------------------ issues
async function viewIssues(tab) {
  tab = tab || "findings";
  const data = await getFindings();
  const tabs = [["findings", `Findings (${data.findings.filter((f) => f.kind !== "engine_diagnostic").length})`], ["checks", `Independent checks (${data.reconciliations.length})`], ["diagnostics", "Engine diagnostics"], ["variance", `Swings vs 2024 (${data.variances.length})`]];
  let body = "";
  if (tab === "findings" || tab === "diagnostics") {
    const list = data.findings.filter((f) => (tab === "diagnostics") === (f.kind === "engine_diagnostic"));
    const order = { error: 0, judgment: 1, warning: 2, info: 3 };
    list.sort((a, b) => (order[a.severity] - order[b.severity]) || (Math.abs(n0(b.effect.federal_balance)) + Math.abs(n0(b.effect.california_balance))) - (Math.abs(n0(a.effect.federal_balance)) + Math.abs(n0(a.effect.california_balance))));
    const totF = list.reduce((s, f) => s + n0(f.effect.federal_balance), 0), totC = list.reduce((s, f) => s + n0(f.effect.california_balance), 0);
    body = `<div class="row noprint"><label><input type="checkbox" id="selall"> select all</label><button id="b-prop">Propose adjustments for selected</button><button id="b-res">Mark selected resolved</button>
      <select id="flt"><option value="">All statuses</option><option>open</option><option>in_progress</option><option>resolved</option><option>accepted</option></select><span class="spacer"></span>
      <span class="small">If every proposed fix is booked: federal balance ${totF > 0 ? "+" : "−"}${Math.abs(totF).toLocaleString()} · California ${totC > 0 ? "+" : "−"}${Math.abs(totC).toLocaleString()}</span></div>
      ${list.map((f) => `<div class="row" data-st="${f.state.status}" style="align-items:flex-start"><input type="checkbox" class="sel" value="${esc(f.id)}" style="margin-top:20px"><div style="flex:1">${findingCard(f)}</div></div>`).join("")}`;
  } else if (tab === "checks") {
    body = `<p class="lede">Recomputed from source evidence, independently of the engine. Differences become findings.</p><table><tr><th></th><th>Check</th><th class="r">Left</th><th class="r">Right</th><th class="r">Difference</th><th>Evidence</th></tr>
      ${data.reconciliations.map((r) => `<tr><td>${r.status === "agrees" ? '<span class="pill ok">agrees</span>' : '<span class="pill error">differs</span>'}</td><td><b>${esc(r.title)}</b>${r.note ? `<div class="small muted">${esc(r.note)}</div>` : ""}</td>
        <td class="r"><div class="small muted">${esc(r.left_label)}</div>${fmt(r.left)}</td><td class="r"><div class="small muted">${esc(r.right_label)}</div>${fmt(r.right)}</td><td class="r">${fmt(r.diff)}</td><td>${chips(r.refs.filter((x) => x !== "R-SUM"))}</td></tr>`).join("")}</table>`;
  } else {
    body = `<p class="lede">Firm standard: flag lines that changed more than ${data.variances.length ? "" : ""}10% and more than 1,000 from 2024 (administrator can change this). Each needs an explanation - a note or verification on the line.</p>
      <table><tr><th>Line</th><th class="r">2024</th><th class="r">2025</th><th class="r">Change</th><th>Explained?</th></tr>
      ${data.variances.map((v) => `<tr class="click" data-line="${esc(v.key)}"><td>${esc(v.label)} <span class="small muted num">${esc(v.key)}</span></td><td class="r">${fmt(v.prior)}</td><td class="r">${fmt(v.current)}</td><td class="r">${fmt(v.change)} ${v.pct !== null ? `<span class="small muted">${Math.round(v.pct * 100)}%</span>` : ""}</td><td>${data.variance_explained[v.key] ? '<span class="pill ok">explained</span>' : '<span class="pill warning">explain</span>'}</td></tr>`).join("")}</table>`;
  }
  $("#main").innerHTML = `<h1>Findings & independent checks</h1><p class="lede">What the workpaper found that the engine and the extraction got wrong, with the evidence and the estimated tax effect. Sorted by severity, then dollars.</p>
    <div class="tabs">${tabs.map(([k, l]) => `<a href="#/issues/${k}" class="${k === tab ? "on" : ""}">${l}</a>`).join("")}</div>${body}`;
  if ($("#selall")) {
    $("#selall").onchange = (e) => $$(".sel").forEach((c) => (c.checked = e.target.checked));
    $("#flt").onchange = (e) => $$("[data-st]").forEach((r) => (r.style.display = !e.target.value || r.dataset.st === e.target.value ? "" : "none"));
    const sel = () => $$(".sel").filter((c) => c.checked).map((c) => c.value);
    $("#b-res").onclick = () => act({ type: "bulk", actions: sel().map((id) => ({ type: "finding.set", target: id, data: { status: "resolved" } })) }, `${sel().length} finding(s) resolved`);
    $("#b-prop").onclick = () => {
      const acts = sel().flatMap((fid) => { const f = data.findings.find((x) => x.id === fid); return f.adjustments.map((a) => ({ type: "adjustment.propose", target: a.line, data: { from: a.from, to: a.to, category: a.category, rates: a.rates, reason: `${f.title}: ${a.note || ""}`, finding: fid } })); });
      if (!acts.length) return toast("Selected findings have no proposed adjustments", { err: true });
      act({ type: "bulk", actions: acts }, `${acts.length} adjustment(s) proposed`);
    };
  }
}

// ------------------------------------------------------------------ schedules
async function viewSchedule(name, arg) {
  const params = new URLSearchParams(location.hash.split("?")[1] || "");
  const box = params.get("box");
  const d = await api(`/api/schedules/${name}${box ? "?box=" + box : ""}`);
  const recs = (rs) => rs && rs.length ? `<h2>Checks</h2><table>${rs.map((r) => `<tr><td>${r.status === "agrees" ? '<span class="pill ok">agrees</span>' : '<span class="pill error">differs</span>'}</td><td>${esc(r.title)}</td><td class="r">${fmt(r.left)}</td><td class="r">${fmt(r.right)}</td><td class="r">${fmt(r.diff)}</td></tr>`).join("")}</table>` : "";
  const lineTable = (rows) => `<table><tr><th>Line</th><th>Description</th><th class="r">Engine</th><th class="r">Workpaper</th></tr>${rows.map((l) => `<tr class="click" data-line="${esc(l.key)}"><td class="num small">${esc(l.key)}</td><td>${esc(l.label)}${l.findings.length ? ' <span class="pill error">finding</span>' : ""}</td><td class="r">${fmt(l.engine)}</td><td class="r">${l.adjusted ? `<span class="adj">${fmt(l.value)}</span>` : ""}</td></tr>`).join("")}</table>`;
  let h = "";
  if (name === "sales") {
    const tot = d.summary.reduce((s, r) => ({ c: s.c + r.count, p: s.p + r.proceeds }), { c: 0, p: 0 });
    h = `<h1>Sales detail</h1><p class="lede">${tot.c} dispositions, ${Math.round(tot.p).toLocaleString()} proceeds. Reviewed by exception: totals by box and broker first, then only the lots that need a look (zero basis, wash sales, findings). Pick a box to see every lot.</p>
      <table><tr><th>Box</th><th>Source</th><th class="r">Lots</th><th class="r">Proceeds</th><th class="r">Basis</th><th class="r">Adj.</th><th class="r">Gain (loss)</th><th class="r">Exceptions</th></tr>
      ${d.summary.map((r) => `<tr class="click" onclick="location.hash='#/sched/sales?box=${r.box}'"><td><b>${r.box}</b></td><td class="small">${esc(r.doc)} · ${esc(r.doc_title)}</td><td class="r">${r.count}</td><td class="r">${fmt(r.proceeds, 2)}</td><td class="r">${fmt(r.basis, 2)}</td><td class="r">${fmt(r.adjustments, 2)}</td><td class="r">${fmt(r.gain, 2)}</td><td class="r">${r.exceptions ? `<span class="pill warning">${r.exceptions}</span>` : ""}</td></tr>`).join("")}</table>
      ${recs(d.reconciliations)}
      <h2>${box ? `All lots in box ${box}` : "Lots needing attention"} <span class="small muted">(${d.lots.length})</span> ${box ? '<a class="small" href="#/sched/sales">show exceptions only</a>' : ""}</h2>
      <table><tr><th>Lot</th><th>Description</th><th>Acquired</th><th>Sold</th><th class="r">Proceeds</th><th class="r">Basis</th><th class="r">Gain</th><th>Exceptions</th></tr>
      ${d.lots.map((l) => `<tr class="click" data-line="${esc(l.key)}"><td class="num small">${esc(l.box)}.${esc(l.lot)}</td><td>${esc(l.desc)}</td><td class="small">${esc(l.acquired || "")}</td><td class="small">${esc(l.sold || "")}</td><td class="r">${fmt(l.proceeds, 2)}</td><td class="r">${fmt(l.basis, 2)}</td><td class="r">${l.effective !== l.gain ? `<span class="strike">${fmt(l.gain, 2)}</span> <span class="adj">${fmt(l.effective, 2)}</span>` : fmt(l.gain, 2)}</td><td>${l.exceptions.map((e) => `<span class="pill ${e === "finding" ? "error" : "warning"}">${e}</span>`).join(" ")}</td></tr>`).join("")}</table>`;
  } else if (name === "assets") {
    h = `<h1>Depreciation by asset</h1><p class="lede">Federal, California and AMT side by side, with accumulated depreciation and disposals. California differences carry forward.</p>
      <table><tr><th>Asset</th><th>In service</th><th>Method / life</th><th class="r">Cost</th><th class="r">Fed 2025</th><th class="r">CA 2025</th><th class="r">AMT 2025</th><th class="r">Fed accum.</th><th class="r">CA accum.</th><th>Disposal</th></tr>
      ${d.assets.map((a) => `<tr class="click" data-ref="asset:${esc(a.asset_id)}"><td><b>${esc(a.asset_id)}</b><div class="small">${esc(a.description)}</div><div class="small muted">${esc(a.activity)}</div></td><td class="small">${esc(a.placed_in_service)}</td><td class="small">${esc(a.method)} · ${a.life_years}y</td><td class="r">${fmt(a.cost, 2)}${a.land_value_not_depreciated ? `<div class="small muted">land ${fmt(a.land_value_not_depreciated)}</div>` : ""}</td>
        <td class="r">${fmt(a.federal.depreciation_2025, 2)}</td><td class="r">${fmt(a.california.depreciation_2025, 2)}</td><td class="r">${fmt(a.amt.depreciation_2025, 2)}</td><td class="r">${fmt(a.federal.accumulated_end, 2)}</td><td class="r">${fmt(a.california.accumulated_end, 2)}</td>
        <td class="small">${a.disposition ? `Sold ${esc(a.disposition.date)} · unrecaptured §1250 ${fmt(a.disposition.unrecaptured_1250_depreciation, 2)} · §1250 recapture ${fmt(a.disposition.section_1250_ordinary_recapture)}` : ""}</td></tr>`).join("")}</table>
      <h2>California basis differences carried forward</h2><div class="card small">${Object.entries(d.ca_differences).map(([k, v]) => `${esc(k)}: ${fmt(v, 2)}`).join(" · ")}</div>
      ${recs(d.reconciliations)}<h2>Lines</h2>${lineTable(d.lines)}`;
  } else if (name === "books") {
    h = `<h1>Books to tax - Lindqvist Studio</h1><p class="lede">QuickBooks P&L (cash basis, D14) to Schedule C, line by line, with the reason for every difference.</p>
      <table><tr><th>Books account</th><th class="r">Books</th><th>Return line</th><th class="r">Engine</th><th class="r">Workpaper</th><th class="r">Difference</th><th>Why</th></tr>
      ${d.rows.map((r) => `<tr class="click" data-line="${esc(r.line)}"><td>${esc(r.book_label)} ${chips(r.evidence)}</td><td class="r">${fmt(r.book)}</td><td class="small">${esc(r.line)} ${esc(r.line_label)}</td><td class="r">${fmt(r.engine)}</td><td class="r">${r.value !== r.engine ? `<span class="adj">${fmt(r.value)}</span>` : ""}</td><td class="r">${fmt(r.difference)}</td><td class="small">${esc(r.reason)} ${r.findings.length ? chips(r.findings) : ""}</td></tr>`).join("")}
      <tr class="grp"><td>Net income (books) → net profit</td><td class="r">${fmt(d.book_net)}</td><td>SchC.31</td><td class="r">${fmt(d.engine_net)}</td><td class="r">${fmt(d.net_value)}</td><td></td><td class="small muted">Downstream lines are not re-run; see estimated effects</td></tr></table>${recs(d.reconciliations)}`;
  } else if (name === "state") {
    h = `<h1>State split - California 540NR</h1><p class="lede">Part-year resident: California through ${esc(d.residency[0].to)} (${d.ratio_days[0]} of ${d.ratio_days[1]} days), Texas after. Column E is California-source.</p>
      <table><tr><th>Line</th><th>Description</th><th class="r">A Federal</th><th class="r">B Subtract</th><th class="r">C Add</th><th class="r">D CA law</th><th class="r">E California</th></tr>
      ${d.table.map((r) => `<tr><td>${esc(r.line)}</td><td>${esc(r.label)}</td>${["A", "B", "C", "D", "E"].map((c) => `<td class="r ${r[c + "_key"] ? "click" : ""}" ${r[c + "_key"] ? `data-line="${r[c + "_key"]}"` : ""}>${r[c] !== undefined ? fmt(r[c]) : ""}</td>`).join("")}</tr>`).join("")}</table>
      <h2>540NR</h2>${lineTable(d.lines)}<h2>Ridgeline state apportionment (D38)</h2><div class="card small">${Object.entries(d.ridgeline).map(([k, v]) => `${esc(k)} ${fmt(v)}`).join(" · ")}</div>`;
  } else if (name === "k1") {
    h = `<h1>K-1s</h1><p class="lede">Every K-1 box and where it landed on the return.</p>${Object.entries(d.docs).map(([doc, rows]) => `<h2>${chip(doc)}</h2><table>${rows.map((r) => `<tr><td>${chip(r.id)}</td><td>${esc(r.label)}</td><td class="r">${fmt(r.value)}</td><td>${r.lines.map((k) => chip(k)).join(" ") || '<span class="small muted">not on return</span>'}</td></tr>`).join("")}</table>`).join("")}
      <h2>Return lines from K-1s</h2>${lineTable(d.lines)}`;
  } else if (name === "basis") {
    const sc = d.records.scorp_lindqvist_family_woodworks;
    h = `<h1>Basis by year</h1><h2>Lindqvist Family Woodworks (S corp) stock basis</h2><table><tr><th>Year</th><th class="r">Ending basis</th><th>Source</th></tr>
      ${sc.map((r) => `<tr><td>${r.year}</td><td class="r">${fmt(r.ending_stock_basis)}</td><td class="small">${esc(r.source)}${r.k1_income ? ` · income ${r.k1_income.toLocaleString()}, distributions ${r.distributions.toLocaleString()}` : ""}</td></tr>`).join("")}
      <tr><td>2023 (from D40)</td><td class="r">${fmt(21300 + 14100 - 20000)}</td><td class="small">${chip("D40")} income 14,100 − distributions 20,000 - received 06/05/2026</td></tr>
      <tr><td>2024 (recomputed)</td><td class="r">${fmt(15400 + 16900 - 30000)}</td><td class="small">2024 K-1 income 16,900 − distributions 30,000</td></tr></table>
      ${lineTable(d.f7203)}<h2>Ridgeline capital account</h2><div class="card small">Beginning ${fmt(d.ridgeline.cap_begin)} · distributions ${fmt(d.ridgeline.box19a)} · ending ${fmt(d.ridgeline.cap_end)} · outside basis never computed by the prior preparer</div>
      <h2>IRA basis (Form 8606)</h2>${lineTable(d.f8606)}${recs(d.reconciliations)}`;
  } else if (name === "carryovers") {
    const into = d.into_2025, out = d.out_2025.federal;
    h = `<h1>Carryovers</h1><div class="grid2"><div><h2>Into 2025 (from 2024)</h2><table>${Object.entries(into).filter(([k, v]) => typeof v !== "object").map(([k, v]) => `<tr><td>${esc(k.replaceAll("_", " "))}</td><td class="r">${fmt(v)}</td></tr>`).join("")}</table></div>
      <div><h2>Out of 2025 (engine v3)</h2><table>${Object.entries(out).map(([k, v]) => `<tr><td>${esc(k.replaceAll("_", " "))}</td><td class="r">${fmt(v)}</td></tr>`).join("")}</table>
      <div class="card small">California: ${esc(JSON.stringify(d.out_2025.california))}</div></div></div>${recs(d.reconciliations)}`;
  } else if (name === "payments") {
    h = `<h1>Payments</h1><div class="grid2"><div><h2>Federal</h2>${lineTable(d.federal)}</div><div><h2>California</h2>${lineTable(d.california)}</div></div>${recs(d.reconciliations)}
      <h2>IRS account transcript</h2>${d.agency.irs_account_transcript_2025.map((s) => `<h3>as of ${esc(s.as_of)}</h3><table>${s.transactions.map((t) => `<tr><td>${esc(t.date)}</td><td>${esc(t.code)}</td><td>${esc(t.description)}</td><td class="r">${fmt(t.amount)}</td></tr>`).join("")}</table>`).join("")}
      <h2>FTB account (as of ${esc(d.agency.ftb_account.as_of)})</h2><table>${d.agency.ftb_account.tax_year_2025_payments.map((t) => `<tr><td>${esc(t.date)}</td><td>2025</td><td class="r">${fmt(t.amount)}</td></tr>`).join("")}${d.agency.ftb_account.tax_year_2024_payments_after_filing.map((t) => `<tr><td>${esc(t.date)}</td><td>2024 credit</td><td class="r">${fmt(t.amount)}</td><td class="small">${esc(t.note)}</td></tr>`).join("")}</table>`;
  } else if (name === "prior") {
    h = `<h1>Prior preparer vs what we changed</h1><div class="card"><b>Delta River memo (D07):</b> ${esc(d.memo)}</div>
      <h2>Open items left by the prior preparer</h2><table>${d.open_items.map((o) => `<tr><td>${esc(o.item)}</td><td class="small">${esc(o.resolution)}</td><td>${chips(o.refs)}</td></tr>`).join("")}</table>
      <h2>Prior preparer notes</h2>${d.notes.map((n) => `<div class="small">• ${esc(n)}</div>`).join("")}
      <h2>Swings vs 2024 (explain each)</h2><table><tr><th>Line</th><th class="r">2024</th><th class="r">2025</th><th class="r">Change</th></tr>${d.variances.map((v) => `<tr class="click" data-line="${esc(v.key)}"><td>${esc(v.label)}</td><td class="r">${fmt(v.prior)}</td><td class="r">${fmt(v.current)}</td><td class="r">${fmt(v.change)}</td></tr>`).join("")}</table>`;
  } else if (name === "whatif") {
    h = `<h1>What-ifs</h1><p class="lede">Engine results for specific alternatives (base v3). Choosing one is a judgment call - record it with its authority.</p>
      <table><tr><th>Alternative</th><th class="r">Federal tax change</th><th class="r">California change</th><th>Lines changed</th><th></th></tr>
      ${d.alternatives.map((a) => `<tr><td><b>${esc(a.id)}</b><div class="small">${esc(a.description)}</div></td><td class="r">${fmt(a.federal.total_tax_change)}</td><td class="r">${fmt(a.california.total_tax_change)}</td><td class="small">${a.changed_lines.length}</td><td><button class="small" data-wi="${esc(a.id)}">Record as election…</button></td></tr>`).join("")}</table>
      <h2>Extension estimate (04/14/2026, from v2)</h2><div class="card small">Federal estimated tax ${fmt(d.extension.federal.estimated_total_tax)} · paid with 4868 ${fmt(d.extension.federal.paid_with_form_4868)} · California paid with 3519 ${fmt(d.extension.california.paid_with_ftb_3519)} · rule: ${esc(d.extension.federal.rule)}</div>`;
  }
  $("#main").innerHTML = h;
  $$("[data-wi]").forEach((b) => (b.onclick = () => {
    const auth = prompt(`Authority for electing ${b.dataset.wi}?`); if (!auth) return;
    act({ type: "judgment.record", target: b.dataset.wi, data: { topic: "Election: " + b.dataset.wi, decision: "Elected " + b.dataset.wi, authority: auth, kind: "election" } }, "Election recorded");
  }));
}

// ------------------------------------------------------------------ review
async function viewReview() {
  const r = await api("/api/review");
  const lv = r.levels;
  $("#main").innerHTML = `<h1>Review & sign-off</h1><p class="lede">Sign-offs are tied to a fingerprint of the numbers they cover. If anything in an area changes afterwards, the sign-off reopens automatically and shows what changed. Nobody can review an area they prepared or adjusted.</p>
    <div class="grid2"><div><h2>Sign-off matrix</h2><table class="matrix"><tr><th>Area</th>${lv.map((l) => `<th>${l}</th>`).join("")}</tr>
      ${r.areas.map((a) => `<tr><td>${esc(a.label)} <span class="small muted">${a.lines ? a.lines + " lines" : ""}</span></td>${lv.map((l) => {
        const s = r.signoffs[a.id][l];
        if (a.id === "partner_review" && l !== "partner") return "<td></td>";
        if (a.id !== "partner_review" && l === "partner") return '<td class="small muted">via whole return</td>';
        return `<td>${s ? (s.valid ? `<span class="so valid">✔ ${esc(who(s.by))}<br><span class="muted">${when(s.at)}</span></span>` : `<span class="so reopened" title="${esc(s.changed.join(", "))}">reopened</span><div class="small muted">changed: ${s.changed.slice(0, 3).map((k) => chip(k)).join(" ") || "adjustments / findings"}</div><button class="small" data-signoff="${a.id}|${l}">Re-sign</button>`) : `<button class="small" data-signoff="${a.id}|${l}">Sign off</button>`}</td>`;
      }).join("")}</tr>`).join("")}</table></div>
      <div><h2>Filing gate</h2><div class="card">${r.gates.map(gateRow).join("")}</div>
      <h2>Firm required steps</h2><div class="card">${r.steps.map((s) => gateRow({ ok: s.ok, label: s.step, detail: s.detail })).join("")}</div></div></div>
    <h2>Adjustments</h2>${r.adjustments.length ? `<table><tr><th>Line</th><th class="r">From</th><th class="r">To</th><th>Why</th><th>Effect</th><th>Status</th><th></th></tr>${r.adjustments.map((a) => `<tr><td>${chip(a.line)}${a.amendment ? ' <span class="pill blue">amendment</span>' : ""}</td><td class="r">${fmt(a.from)}</td><td class="r">${fmt(a.to)}</td><td class="small">${esc(a.reason)}<div class="muted">${esc(who(a.proposed_by))} ${when(a.at)}</div></td><td>${effectText(a.effect)}</td><td>${statusPill(a.status)}${a.needs_partner ? '<div><span class="pill warning">partner</span></div>' : ""}${a.approved_by ? `<div class="small muted">by ${esc(who(a.approved_by))}</div>` : ""}</td>
      <td>${a.status === "proposed" ? `<button class="small" data-approve="${a.id}">Approve</button> <button class="small" data-reject="${a.id}">Reject</button>` : ""}</td></tr>`).join("")}</table>` : '<div class="empty">No adjustments yet.</div>'}
    <h2>Review notes & questions</h2>${r.notes.length ? r.notes.map((n) => `<div class="row" style="align-items:flex-start"><div style="width:140px">${chip(n.target)}</div><div style="flex:1">${noteRow(n)}</div></div>`).join("") : '<div class="empty">None.</div>'}
    <h2>Judgment calls, elections & penalty decisions</h2>${r.judgments.length ? r.judgments.map(judgmentRow).join("") : '<div class="empty">None recorded.</div>'}
    <div class="card form"><b>Record a judgment / election / due-diligence / penalty decision</b><div class="row"><input id="jt" placeholder="Topic (e.g. Form 2210 method)"><select id="jk"><option>judgment</option><option>election</option><option>due_diligence</option><option>penalty</option></select></div><textarea id="jd" placeholder="Decision"></textarea><input id="ja" placeholder="Authority relied on (required)"><button id="jgo">Record</button></div>`;
  wireRowButtons($("#main"));
  $("#jgo").onclick = () => act({ type: "judgment.record", target: $("#jt").value || "general", data: { topic: $("#jt").value, decision: $("#jd").value, authority: $("#ja").value, kind: $("#jk").value } }, "Recorded");
}

// ------------------------------------------------------------------ activity
async function viewActivity(tab) {
  tab = tab || "feed";
  const a = await api("/api/activity");
  let body = "";
  if (tab === "feed") {
    body = `<div class="row"><span class="small">${a.since ? `Highlighting changes since you last looked (${when(a.since)}).` : "You have not marked this file as seen yet."}</span><span class="spacer"></span><button id="seen">Mark all as seen</button></div>
      <div class="feed card">${a.events.map((e) => `<div class="ev ${e.voided ? "void" : ""} ${a.since && e.at > a.since && e.actor !== S.user ? "new" : ""}"><div class="when">${when(e.at)}</div><div><b>${esc(e.actor_name)}</b> ${esc(e.type.replace(".", " "))} ${e.target ? chip(e.target) : ""}
        <span class="small muted">${esc(describe(e))}</span>${e.voided ? ' <span class="pill info">undone</span>' : ""}</div></div>`).join("") || '<div class="empty">No activity yet.</div>'}</div>`;
  } else if (tab === "timeline") {
    body = `<p class="lede">The season as scripted in the packet. Move the “As of” date in the top bar to replay it - filing status, agency records and notices follow.</p><div class="feed card">${a.timeline.map((t) => `<div class="ev" style="${t.future ? "opacity:.4" : ""}"><div class="when">${when(t.at)}</div><div><b>${esc(t.event.replaceAll("_", " "))}</b> <span class="small muted">${esc(t.actor_name)}</span> ${chips(t.refs)}<div class="small">${esc(t.detail || "")}</div></div></div>`).join("")}</div>`;
  } else {
    body = `<table><tr><th>Version</th><th>Computed</th><th>Trigger</th><th class="r">Fed AGI</th><th class="r">Fed tax</th><th class="r">Fed balance</th><th class="r">CA tax</th><th class="r">Changes</th></tr>
      ${a.versions.map((v) => `<tr><td><b>${v.version}</b></td><td class="small">${when(v.at)}</td><td class="small">${esc(v.trigger)}</td><td class="r">${fmt(v.summary.federal.agi)}</td><td class="r">${fmt(v.summary.federal.total_tax)}</td><td class="r">${fmt(v.summary.federal.balance_due)}</td><td class="r">${fmt(v.summary.california.total_tax)}</td><td class="r">${v.changes}</td></tr>`).join("")}</table>
      <div class="row" style="margin-top:12px">Compare <select id="va"><option>v1</option><option selected>v2</option><option>v3</option></select> with <select id="vb"><option>v1</option><option>v2</option><option selected>v3</option></select><button id="vgo">Compare</button></div><div id="vout"></div>`;
  }
  $("#main").innerHTML = `<h1>Activity & versions</h1><div class="tabs">${[["feed", "Workpaper activity"], ["timeline", "Season timeline"], ["versions", "Engine versions"]].map(([k, l]) => `<a href="#/activity/${k}" class="${k === tab ? "on" : ""}">${l}</a>`).join("")}</div>${body}`;
  if ($("#seen")) $("#seen").onclick = () => act({ type: "seen" }, "Marked as seen");
  if ($("#vgo")) {
    const go = async () => {
      const c = await api(`/api/versions/compare?a=${$("#va").value}&b=${$("#vb").value}`);
      $("#vout").innerHTML = `<p class="small">${esc(c.trigger)}</p><table><tr><th>Line</th><th class="r">${c.a}</th><th class="r">${c.b}</th><th class="r">Change</th></tr>${c.changes.map((x) => `<tr class="click" data-line="${esc(x.key)}"><td>${esc(x.label)} <span class="small muted num">${esc(x.key)}</span></td><td class="r">${fmt(x.a)}</td><td class="r">${fmt(x.b)}</td><td class="r">${delta(x.b, x.a)}</td></tr>`).join("")}</table>`;
    };
    $("#vgo").onclick = go; go();
  }
}
function describe(e) {
  const d = e.data || {};
  if (e.type === "adjustment.propose") return `${d.from} → ${d.to}: ${d.reason || ""}`;
  if (e.type === "verify") return `${d.method} ${d.note || ""}`;
  if (e.type.startsWith("note.")) return d.text || "";
  if (e.type === "signoff") return `${d.level} (fingerprint ${d.hash})`;
  if (e.type === "finding.set") return `${d.status} ${d.note || ""}`;
  if (e.type === "judgment.record") return `${d.topic}: ${d.decision} [${d.authority}]`;
  if (e.type === "time.log") return `${d.hours} h ${d.note || ""}`;
  if (e.type === "view.unmasked") return "sensitive data viewed";
  if (e.type === "undo") return `${(d.events || []).length} event(s)`;
  return d.reason || d.note || "";
}

// ------------------------------------------------------------------ filing
async function viewFiling() {
  const f = await api("/api/filing");
  const fl = f.filing, w = f.totals.workpaper;
  $("#main").innerHTML = `<h1>Filing & after</h1>
    <div class="grid2"><div>
      <h2>1 · Gate</h2><div class="card">${f.gates.map(gateRow).join("")}</div>
      <h2>2 · Extension</h2><div class="card small">Filed ${chip("timeline:extension-2026-04-14", "04/14/2026")} from v2: Form 4868 paid ${fmt(f.extension.federal.paid_with_form_4868)} (estimated balance ${fmt(f.extension.federal.estimated_balance)}); FTB 3519 paid ${fmt(f.extension.california.paid_with_ftb_3519)}. Extended due date 10/15/2026.</div>
      <h2>3 · Client approval & e-file signature</h2><div class="card">${fl.client_approved ? `✅ Approved by ${esc(who(fl.client_approved.by))} ${when(fl.client_approved.at)} on fingerprint <span class="num">${esc(fl.client_approved.hash)}</span>` : "Waiting for the client to approve in the client portal (needs every gate green)."}</div>
      <h2>4 · Transmit & freeze</h2><div class="card">${fl.filed ? `🔒 Filed by ${esc(who(fl.filed.by))} ${when(fl.filed.at)}. The return is frozen - further changes go into an amendment layered on top.${fl.amendment ? `<div>Amendment opened ${when(fl.amendment.at)}: ${esc(fl.amendment.reason)}</div>` : `<div><button id="amend">Open amendment…</button></div>`}` : `<button class="primary" id="file" ${fl.client_approved ? "" : "disabled"}>Record e-file transmission (signing partner)</button>`}</div>
      <h3>Agency e-file record (as of ${esc(S.asOf || S.boot.as_of)})</h3>${f.efile.length ? `<table>${f.efile.map((e) => `<tr><td class="small">${when(e.date)}</td><td>${esc(e.jurisdiction)}</td><td>${e.status === "accepted" ? '<span class="pill ok">accepted</span>' : `<span class="pill error">${esc(e.status)}</span>`}</td><td class="small">${(e.errors || []).map((x) => `${esc(x.code)}: ${esc(x.message)}`).join("<br>")}${e.status === "rejected" ? `<div>Known before filing: ${chip("F-THEO-SSN")}</div>` : ""}</td></tr>`).join("")}</table>` : '<div class="empty">No submissions yet at this date.</div>'}
    </div><div>
      <h2>Client copy</h2><div class="card"><table>
        <tr><td>Federal total tax</td><td class="r">${fmt(w.federal_total_tax)}</td></tr><tr><td>Federal balance due (refund)</td><td class="r">${fmt(w.federal_balance_due)}</td></tr>
        <tr><td>California total tax</td><td class="r">${fmt(w.california_total_tax)}</td></tr><tr><td>California balance due (refund)</td><td class="r">${fmt(w.california_balance_due)}</td></tr></table>
        <button class="noprint" onclick="window.print()">Print client copy</button></div>
      <h2>Notices</h2>${f.notices.length ? f.notices.map((n) => `<div class="card"><b>${esc(n.type)}</b> · ${esc(n.agency)} · tax year ${n.tax_year} · dated ${esc(n.dated)}${n.response_due ? ` · <b>response due ${esc(n.response_due)}</b>` : ""}<div class="small">${esc(n.summary || (n.items_under_examination || []).join(" / "))}</div><div class="small">${esc(n.status || "")}</div>${n.items_under_examination ? `<a href="#/notice/${n.id}">Build the self-contained response record →</a>` : ""}</div>`).join("") : '<div class="empty">None at this date. (Move “As of” to 2028-02 to replay the examination letter.)</div>'}
      <h2>Carry forward & plan 2026</h2><div class="card small">
        2026 federal safe harbor (110% of 2025 tax, AGI > 150k): <b>${fmt(f.estimates_2026.federal_safe_harbor_total)}</b> → ${fmt(f.estimates_2026.federal_quarterly)} per quarter on ${f.planning.federal_estimate_due_dates.join(", ")}.<br>
        ${f.planning.known_2026_changes_for_this_household.map((x) => "• " + esc(x)).join("<br>")}<br>
        Carryovers out: minimum tax credit ${fmt(f.carryovers_out.federal.minimum_tax_credit_from_2025_amt)} · Ridgeline capital ${fmt(f.carryovers_out.federal.ridgeline_capital_account_end)} · SEP excess ${fmt(f.carryovers_out.federal.sep_contribution_not_deductible_for_2025)} · CA depreciation differences ${esc(Object.keys(f.carryovers_out.california.depreciation_basis_differences).join(", "))}.<br>
        Every finding, judgment and verification in this file carries into 2026 with its history.</div>
    </div></div>`;
  if ($("#file")) $("#file").onclick = () => act({ type: "filing.filed" }, "Return filed and frozen");
  if ($("#amend")) $("#amend").onclick = () => { const r = prompt("Reason for amendment?"); if (r) act({ type: "filing.amend", data: { reason: r } }, "Amendment opened"); };
}
async function viewNotice(id) {
  const n = await api(`/api/notice/${id}`);
  $("#main").innerHTML = `<div class="row noprint"><a href="#/filing">← Filing</a><span class="spacer"></span><button onclick="window.print()">Print / save as PDF</button></div>
    <h1>Response record - ${esc(n.notice.type)}</h1><p class="lede">Tax year ${n.notice.tax_year} · dated ${esc(n.notice.dated)} · response due ${esc(n.notice.response_due)}. Everything an examiner needs, pulled from the frozen workpaper with its evidence and who verified it.</p>
    ${n.items.map((it, i) => `<div class="card" style="margin-bottom:12px"><h2 style="margin-top:0">Item ${i + 1}: ${esc(it.item)}</h2>${it.evidence.map((e) => `<div class="src">${chip(e.id)} <b>${esc(e.label)}</b>${e.value === undefined || e.value === null || e.value === "" ? "" : `<div class="small">${typeof e.value === "number" ? fmt(e.value) : esc(typeof e.value === "string" ? e.value : JSON.stringify(e.value)).slice(0, 500)}</div>`}${(e.verifications || e.verified || []).map((v) => `<div class="small">✔ ${esc(v.method)} - ${esc(who(v.by))} ${when(v.at)}</div>`).join("")}${(e.adjustments || []).map((a) => `<div class="small">Adjusted ${fmt(a.from)} → ${fmt(a.to)} (${esc(a.status)}) - ${esc(a.reason)}</div>`).join("")}${e.state ? statusPill(e.state.status) : ""}</div>`).join("")}</div>`).join("")}
    <h2>Judgments on file</h2>${n.judgments.map(judgmentRow).join("") || '<div class="empty">None.</div>'}`;
}

// ------------------------------------------------------------------ client portal
async function viewClient() {
  const c = await api("/api/client");
  const me = S.boot.me, isClient = me.role === "client";
  $("#main").innerHTML = `<h1>${isClient ? "Your 2025 return" : "Client portal (what the client sees)"}</h1>
    <p class="lede">Questions from your tax team, your documents, and approval of the final return.</p>
    <div class="grid2"><div><h2>Questions for you</h2>${c.questions.length ? c.questions.map(noteRow).join("") : '<div class="empty">No questions right now.</div>'}
      <h2>Send a document or message</h2><div class="card">${attachBlock("client-upload", [])}</div></div>
    <div><h2>Summary</h2><table>${c.summary_lines.map((l) => `<tr><td>${l.jurisdiction === "california" ? "California - " : "Federal - "}${esc(l.label)}</td><td class="r">${fmt(l.value)}</td></tr>`).join("")}</table>
      <p class="small muted">Shown as computed by the engine; your preparer's approved adjustments are reflected in the totals below.</p>
      <div class="card"><b>Totals after the team's adjustments</b><div>Federal balance due (refund): ${fmt(c.totals.workpaper.federal_balance_due)}</div><div>California balance due (refund): ${fmt(c.totals.workpaper.california_balance_due)}</div></div>
      <h2>Approve & sign (Forms 8879 / FTB 8879)</h2><div class="card">${c.filing.client_approved ? `✅ Signed ${when(c.filing.client_approved.at)}` : c.ready ? `<button class="primary" id="approve" ${isClient ? "" : "disabled"}>I have reviewed my return - approve and sign</button>` : `<div class="small">Your team is still finishing the review:</div>${c.gates.filter((g) => !g.ok).map((g) => `<div class="small">• ${esc(g.label)}</div>`).join("")}`}</div>
      ${c.outside ? `<h2>Other filings to know about</h2><div class="card small">${esc(c.outside.summary)}</div>` : ""}</div></div>`;
  wireRowButtons($("#main"));
  wireForms("client-upload", null);
  if ($("#approve")) $("#approve").onclick = () => act({ type: "filing.client_approved" }, "Approved and signed");
}

// ------------------------------------------------------------------ firm
async function viewFirm() {
  const f = await api("/api/firm");
  const admin = S.boot.me.role === "firm_administrator";
  $("#main").innerHTML = `<h1>Firm</h1><div class="grid2"><div>
    <h2>People & enforced roles</h2><table><tr><th>Name</th><th>Role</th><th>Clearance</th><th>Active from</th></tr>${f.staff.map((s) => `<tr><td>${esc(s.name)} ${s.credential ? `<span class="small muted">${esc(s.credential)}</span>` : ""}</td><td>${esc(s.role.replace("_", " "))}</td><td>${s.clearance ? "✔" : '<span class="pill warning">masked</span>'}</td><td class="small">${esc(s.active_from)}${s.note ? `<div class="muted">${esc(s.note)}</div>` : ""}</td></tr>`).join("")}</table>
    <h2>Standards</h2><div class="card small"><b>Variance review:</b> flag changes over ${f.standards.variance_review.flag_if_change_over_percent}% and over ${f.standards.variance_review.and_over_amount.toLocaleString()}
      ${admin ? `<div class="row form"><input id="sp" type="number" value="${f.standards.variance_review.flag_if_change_over_percent}"> % and <input id="sa" type="number" value="${f.standards.variance_review.and_over_amount}"><button id="sgo">Update firm standard</button></div>` : '<div class="muted">Only the firm administrator can change standards.</div>'}
      <div style="margin-top:8px"><b>Rules enforced in this app:</b>${f.standards.rules.map((r) => `<div>• ${esc(r)}</div>`).join("")}</div></div>
    <h2>Deadlines</h2><table>${f.deadlines.map((d) => `<tr><td>${esc(d.what)}</td><td>${esc(d.date)}</td></tr>`).join("")}</table>
  </div><div>
    <h2>Time against budget</h2><table><tr><th>Area</th><th class="r">Budget</th><th class="r">Actual</th><th></th></tr>${f.budget.map((b) => `<tr><td>${esc(b.label)}</td><td class="r">${b.budget}</td><td class="r">${b.actual}</td><td><div class="bar ${b.actual > b.budget ? "over" : ""}"><i style="width:${Math.min(100, (b.actual / b.budget) * 100)}%"></i></div></td></tr>`).join("")}</table>
    <div class="row form"><select id="ta">${f.budget.map((b) => `<option value="${b.area}">${esc(b.label)}</option>`).join("")}</select><input id="th" type="number" step="0.25" placeholder="hours"><button id="tgo">Log time</button></div>
    <h2>Assign work</h2><div class="row form"><select id="aa">${Object.entries(S.boot.areas).map(([k, v]) => `<option value="${k}">${v}</option>`).join("")}</select><select id="au">${f.staff.filter((s) => s.role !== "client").map((s) => `<option value="${s.id}">${esc(s.name)}</option>`).join("")}</select><input id="ad" type="date"><button id="ago">Assign</button></div>
    ${Object.entries(f.assignments).map(([a, x]) => `<div class="small">• ${esc(areaName(a))} → ${esc(who(x.user))} due ${esc(x.due || "-")}</div>`).join("")}
    <h2>Sensitive-data view log</h2><div class="card small" style="max-height:260px;overflow:auto">${f.views.map((v) => `<div>${when(v.at)} · <b>${esc(who(v.by))}</b> viewed ${esc(v.what)} unmasked</div>`).join("") || '<span class="muted">No unmasked views yet.</span>'}</div>
    <h2>Demo data</h2><div class="card small">Load a realistic week of activity (proposals, approvals, notes, a client answer) or wipe all saved work. Partner or administrator only.
      <div class="row" style="margin-top:6px"><button id="seed">Load demo activity</button><button id="reset">Reset saved work</button></div></div>
  </div></div>`;
  if ($("#sgo")) $("#sgo").onclick = () => act({ type: "standards.set", data: { flag_if_change_over_percent: +$("#sp").value, and_over_amount: +$("#sa").value } }, "Standard updated");
  $("#tgo").onclick = () => act({ type: "time.log", target: $("#ta").value, data: { hours: +$("#th").value } }, "Time logged");
  $("#ago").onclick = () => act({ type: "assign", target: $("#aa").value, data: { user: $("#au").value, due: $("#ad").value } }, "Assigned");
  $("#seed").onclick = async () => { try { await api("/api/admin/seed", { method: "POST" }); toast("Demo activity loaded"); refresh(); } catch (e) { toast(e.message, { err: true }); } };
  $("#reset").onclick = async () => { if (!confirm("Delete all saved workpaper activity?")) return; try { await api("/api/admin/reset", { method: "POST" }); toast("Reset"); refresh(); } catch (e) { toast(e.message, { err: true }); } };
}

// ------------------------------------------------------------------ search
$("#searchform").onsubmit = async (e) => {
  e.preventDefault();
  const q = $("#q").value.trim(); if (!q) return;
  const hits = await api(`/api/search?q=${encodeURIComponent(q)}`);
  const d = $("#drawer"); d.dataset.kind = ""; d.classList.add("open");
  $("#drawer-crumbs").textContent = `Search: ${q}`;
  $("#drawer-body").innerHTML = `${S.boot.ai ? `<button id="askai">Ask AI about “${esc(q)}”</button><div id="ai-out"></div>` : ""}<h3>${hits.length} match(es)</h3>${hits.map((h) => `<div class="src">${chip(h.id)} ${esc(h.label)} ${h.value !== undefined ? "· " + fmt(h.value) : ""}</div>`).join("") || '<div class="empty">Nothing found.</div>'}`;
  if ($("#askai")) $("#askai").onclick = async () => { $("#ai-out").innerHTML = '<div class="loading">Thinking…</div>'; const r = await api("/api/ai/ask", { method: "POST", body: JSON.stringify({ q }) }); $("#ai-out").innerHTML = renderAI(r); };
};

// ------------------------------------------------------------------ live updates (several people at once)
let lastCount = null;
setInterval(async () => {
  try {
    const h = await fetch("/api/pulse").then((r) => r.json());
    if (lastCount !== null && h.count !== lastCount && h.last_actor !== S.user) {
      const typing = ["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement?.tagName);
      if (!typing) { await refresh(); toast("Updated with changes from your team"); }
    }
    lastCount = h.count;
  } catch (e) {}
}, 8000);

// ------------------------------------------------------------------ boot
async function boot() {
  S.boot = await api("/api/bootstrap");
  if (!S.asOf) S.asOf = S.boot.default_as_of;
  $("#asof").value = S.asOf;
  $("#clientline").textContent = `${S.boot.client.name} · ${S.boot.client.tax_year} · ${S.boot.client.id}${S.boot.ai ? " · AI on" : ""}`;
  const groups = {};
  S.boot.users.forEach((u) => (groups[u.role] = groups[u.role] || []).push(u));
  $("#who").innerHTML = Object.entries(groups).map(([r, us]) => `<optgroup label="${esc(r.replace("_", " "))}">${us.map((u) => `<option value="${u.id}" ${u.id === S.user ? "selected" : ""}>${esc(u.name)}${u.clearance ? "" : " (no clearance)"}</option>`).join("")}</optgroup>`).join("");
  $("#who").onchange = async () => { S.user = $("#who").value; localStorage.setItem("wp.user", S.user); lastCount = null; S.boot = await api("/api/bootstrap"); location.hash = S.boot.me.role === "client" ? "#/client" : location.hash; render(); toast(`Now acting as ${S.boot.me.name} (${S.boot.me.role.replace("_", " ")})`); };
  $("#asof").onchange = () => { S.asOf = $("#asof").value; localStorage.setItem("wp.asof", S.asOf); refresh(); };
  const o = await api("/api/overview").catch(() => null);
  if (o) $("#stage").textContent = o.stage;
  render();
}
boot();
