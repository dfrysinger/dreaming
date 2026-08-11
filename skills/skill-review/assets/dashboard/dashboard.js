"use strict";

const state = {
  token: null,
  route: "overview",
  cursors: {},
  pages: {},
  query: {},
};

const view = document.getElementById("view");
const errorBox = document.getElementById("error");
const authNote = document.getElementById("auth-note");

function readToken() {
  const fragment = new URLSearchParams(location.hash.replace(/^#/, ""));
  const token = fragment.get("access_token");
  if (token) {
    sessionStorage.setItem("dreaming-access-token", token);
    history.replaceState(null, "", `${location.pathname}#overview`);
  }
  return sessionStorage.getItem("dreaming-access-token");
}

function text(value, fallback = "Unknown") {
  return value === null || value === undefined || value === "" ? fallback : String(value);
}

function esc(value) {
  return text(value, "").replace(/[&<>"']/g, character => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
  })[character]);
}

function number(value) {
  return value === null || value === undefined ? "Unknown" : Number(value).toLocaleString();
}

function bytes(value) {
  if (value === null || value === undefined) return "Unknown";
  const units = ["B", "KB", "MB", "GB"];
  let amount = Number(value);
  let unit = 0;
  while (amount >= 1024 && unit < units.length - 1) { amount /= 1024; unit += 1; }
  return `${amount.toFixed(unit ? 1 : 0)} ${units[unit]}`;
}

function percent(value) {
  return value === null || value === undefined ? "Unknown" : `${value}%`;
}

function relative(value) {
  const timestamp = typeof value === "number" ? value * 1000 : Date.parse(value);
  if (!Number.isFinite(timestamp)) return "Unknown";
  const seconds = Math.max(0, Math.floor((Date.now() - timestamp) / 1000));
  if (seconds < 60) return "Now";
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h`;
  return `${Math.floor(seconds / 86400)}d`;
}

function fullTime(value) {
  if (value === null || value === undefined || value === "") return "Time unavailable";
  const date = new Date(typeof value === "number" ? value * 1000 : value);
  if (Number.isNaN(date.valueOf())) return "Unknown time";
  return date.toLocaleString(undefined, {
    weekday: "long", month: "long", day: "numeric",
    hour: "numeric", minute: "2-digit"
  }).replace(" at ", " at ");
}

function badge(value) {
  const normalized = text(value).toLowerCase();
  const className = ["healthy", "ok", "pass", "current", "completed", "reviewed"].some(word => normalized.includes(word))
    ? "ok" : ["failed", "error", "regression", "unhealthy", "invalid"].some(word => normalized.includes(word))
    ? "bad" : "warn";
  return `<span class="badge ${className}">${esc(value)}</span>`;
}

async function api(path) {
  if (!state.token) throw new Error("Open the dashboard with the dashboard-open installer command.");
  const response = await fetch(path, {
    headers: { Authorization: `Bearer ${state.token}` },
    cache: "no-store",
  });
  const payload = await response.json();
  if (!response.ok) {
    if (response.status === 401) sessionStorage.removeItem("dreaming-access-token");
    throw new Error(payload.error?.message || `Request failed (${response.status})`);
  }
  return payload.data;
}

function setError(error) {
  errorBox.textContent = error.message || String(error);
  errorBox.hidden = false;
}

function clearError() {
  errorBox.hidden = true;
  errorBox.textContent = "";
}

function header(title, subtitle, status = "") {
  return `<div class="header"><div><h1>${esc(title)}</h1><p class="subtitle">${esc(subtitle)}</p></div>${status}</div>`;
}

function lineChart(series, fields, colors) {
  if (!series?.length) return `<div class="chart empty">Historical data unavailable</div>`;
  const values = series.flatMap(item => fields.map(field => Number(item[field])).filter(Number.isFinite));
  const max = Math.max(...values, 1);
  const min = Math.min(...values, 0);
  const span = Math.max(1, max - min);
  const paths = fields.map((field, fieldIndex) => {
    const points = series.map((item, index) => {
      const x = series.length === 1 ? 50 : 4 + (92 * index / (series.length - 1));
      const value = Number(item[field]);
      const y = 94 - 86 * ((value - min) / span);
      return `${x},${Number.isFinite(y) ? y : 94}`;
    }).join(" ");
    return `<polyline points="${points}" fill="none" stroke="${colors[fieldIndex]}" stroke-width="2.5" vector-effect="non-scaling-stroke"/>`;
  }).join("");
  return `<div class="chart"><svg viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden="true"><path d="M4 25H96M4 50H96M4 75H96" stroke="#273040" stroke-width=".4"/>${paths}</svg></div>`;
}

function overlayChart(lines) {
  const visible = lines.filter(line => line.series?.some(item => Number.isFinite(Number(item[line.field]))));
  if (!visible.length) return `<div class="chart empty">Historical data unavailable</div>`;
  const paths = visible.map(line => {
    const pointsWithValues = line.series.filter(item => Number.isFinite(Number(item[line.field])));
    const values = pointsWithValues.map(item => Number(item[line.field]));
    const max = Math.max(...values, 1);
    const min = Math.min(...values, 0);
    const span = Math.max(1, max - min);
    const points = pointsWithValues.map((item, index) => {
      const x = pointsWithValues.length === 1 ? 50 : 4 + (92 * index / (pointsWithValues.length - 1));
      const y = 94 - 86 * ((Number(item[line.field]) - min) / span);
      return `${x},${y}`;
    }).join(" ");
    return `<polyline points="${points}" fill="none" stroke="${line.color}" stroke-width="2.5" vector-effect="non-scaling-stroke"/>`;
  }).join("");
  return `<div class="chart"><svg viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden="true"><path d="M4 25H96M4 50H96M4 75H96" stroke="#273040" stroke-width=".4"/>${paths}</svg></div>`;
}

async function renderOverview() {
  const data = await api("/api/v1/overview");
  const runtime = data.runtime.status;
  document.getElementById("runtime").innerHTML = `<strong>${esc(runtime)}</strong><br>${data.runtime.halted ? "Mutation halt active" : "Scheduled runtime available"}`;
  const capability = data.evaluations;
  view.innerHTML = `
    ${header("Overview", "Reliability, backlog burn-down, learned skills, and measured capability.", badge(runtime))}
    <div class="grid metrics">
      <article class="card"><div class="label">Scheduled reliability</div><div class="metric">${esc(runtime)}</div><div class="submetric">${data.runtime.halted ? "Halt switch active" : "Latest retained run status"}</div></article>
      <article class="card"><div class="label">Dreams remaining</div><div class="metric">${number(data.dreams.remaining)}</div><div class="submetric">${number(data.dreams.completed)} completed</div></article>
      <article class="card"><div class="label">Learned skills</div><div class="metric">${number(data.skills.count)}</div><div class="submetric">Git-backed agent-created skills</div></article>
      <article class="card"><div class="label">Shadow candidates</div><div class="metric">${number(data.candidates.total)}</div><div class="submetric"><a class="link" href="#candidates">${number(data.candidates.valid)} valid · ${number(data.candidates.invalid)} invalid · never active</a></div></article>
      <article class="card"><div class="label">Capability improvement</div><div class="metric">${percent(capability.candidate_percent)}</div><div class="submetric">Control ${percent(capability.control_percent)} · ${capability.comparable_skills} comparable skills</div></article>
    </div>
    <div class="grid charts">
      <article class="panel"><div class="panel-head"><h2>Skill count and capability completion</h2></div>
        ${overlayChart([
          {series:data.skills.history, field:"count", color:"#70a5ff"},
          {series:capability.history, field:"candidate_percent", color:"#66d9a6"},
          {series:capability.history, field:"control_percent", color:"#f2c66d"}
        ])}
        <div class="legend"><span class="blue">Skill count</span><span class="green">Candidate ${percent(capability.candidate_percent)}</span><span class="amber">Control ${percent(capability.control_percent)}</span></div>
      </article>
      <article class="panel"><div class="panel-head"><h2>Dream backlog burn-down</h2></div>
        ${lineChart(data.dreams.history, ["remaining"], ["#a98bff"])}
        <div class="legend"><span>Dreams remaining</span></div>
      </article>
    </div>
    <div class="grid split mt">
      <article class="panel"><div class="panel-head"><h2>Latest learned skills</h2><a class="link" href="#skills">View all ${number(data.skills.count)}</a></div>
        ${skillTable(data.skills.latest)}
      </article>
      <article class="panel"><div class="panel-head"><h2>Evaluation coverage</h2></div>
        <dl class="definition">
          <dt>Comparable capability</dt><dd>${number(capability.comparable_skills)}</dd>
          <dt>Preference passes</dt><dd>${number(capability.preference.pass)}</dd>
          <dt>Regressions</dt><dd>${number(capability.preference.regression)}</dd>
          <dt>Inconclusive</dt><dd>${number(capability.preference.inconclusive)}</dd>
        </dl>
      </article>
    </div>`;
}

function skillTable(items) {
  if (!items?.length) return `<div class="empty">No learned skills retained.</div>`;
  return `<table><thead><tr><th>Skill</th><th>Created</th><th>Evidence</th><th>Evaluation</th></tr></thead><tbody>${items.map(item => `
    <tr><td><a class="link" href="#skill/${encodeURIComponent(item.name)}">${esc(item.name)}</a></td><td>${relative(item.created_at)}</td><td>${number(item.evidence_count)}</td><td>${badge(item.evaluation_status || item.status)}</td></tr>`).join("")}</tbody></table>`;
}

async function renderActivity() {
  const data = await api("/api/v1/activity?limit=50");
  view.innerHTML = `${header("Activity", "Scheduled executions contain their ordered passes and attributed reviews; older unlinked work remains separate.")}
    <div class="run-stack">${data.items.length ? data.items.map(run => `
      <article class="run"><header><div><div class="run-title"><h2>${run.kind === "scheduled" ? "Scheduled Dreaming run" : run.kind === "evaluation" ? "Skill evaluation" : run.parent_run_id ? "Scheduled dream review" : "Unattributed dream review"}</h2><time>${fullTime(run.started_at)}</time></div><div class="muted">${esc(run.id)}${run.parent_run_id ? ` · Parent ${esc(run.parent_run_id)}` : ""}</div></div>${badge(run.status)}</header>
      ${run.kind === "scheduled" ? `<div class="steps">${["consolidate","roll","prune"].map((name,index) => {
        const step = (run.passes || []).find(item => item.name === name) || {status:"not recorded"};
        return `<div class="step"><span>${index + 1}</span><strong>${name[0].toUpperCase() + name.slice(1)}</strong><span>${esc(step.reason || "")}</span>${badge(step.status)}</div>`;
      }).join("")}${(run.reviews || []).map(review => `<div class="step"><span>↳</span><strong>Dream review</strong><span>${esc(review.source || "Unknown CLI")} · ${esc(review.session_id || "Unknown dream")}</span>${badge(review.status)}</div>`).join("")}${run.maintenance ? `<div class="notice">${run.maintenance.days_until_due === null ? "Weekly maintenance is not due; the last successful run is unavailable." : `Weekly maintenance not due for ${run.maintenance.days_until_due} days (Last run ${fullTime(run.maintenance.last_run_at)})`}</div>` : ""}</div>` : ""}</article>`).join("") : `<div class="empty">No retained activity.</div>`}</div>`;
}

function toolbar(kind) {
  return `<div class="toolbar">
    <input class="control" id="${kind}-query" placeholder="Search">
    <select class="control" id="${kind}-status"><option value="">All statuses</option><option>remaining</option><option>completed</option><option>active</option><option>current</option><option>unhealthy</option></select>
    <select class="control" id="${kind}-sort"><option value="">Default sort</option><option value="name">Name</option><option value="oldest">Oldest</option><option value="created">Created</option><option value="evidence">Evidence</option></select>
  </div>`;
}

function pager(kind, data) {
  return `<div class="pager"><span>${number(data.total)} total</span><span><button class="control" data-page="${kind}" data-direction="reset">First</button><button class="control" data-page="${kind}" data-cursor="${esc(data.next_cursor || "")}" ${data.next_cursor ? "" : "disabled"}>Next</button></span></div>`;
}

async function renderDreams(cursor = "") {
  const query = state.query.dreams || {};
  const params = new URLSearchParams({limit:"25", ...query});
  if (cursor) params.set("cursor", cursor);
  const data = await api(`/api/v1/dreams?${params}`);
  view.innerHTML = `${header("Dreams", "Every known session revision, with honest backlog and learning status.")}
    ${toolbar("dreams")}
    <article class="panel"><table><thead><tr><th>Status</th><th>CLI</th><th>Dream</th><th>Updated</th><th>Activity</th><th>Learning result</th></tr></thead><tbody>
    ${data.items.map(item => `<tr><td>${badge(item.raw_status)}</td><td>${esc(item.source)}</td><td>${esc(item.name)}</td><td>${relative(item.updated_at)}</td><td>${number(item.activity.user_turns)} user · ${number(item.activity.tool_calls)} tools</td><td>${esc(item.learning_result || "Not reviewed")}</td></tr>`).join("")}
    </tbody></table>${pager("dreams",data)}</article>`;
  bindCatalog("dreams", renderDreams);
}

async function renderSkills(cursor = "") {
  const query = state.query.skills || {};
  const params = new URLSearchParams({limit:"25", ...query});
  if (cursor) params.set("cursor", cursor);
  const data = await api(`/api/v1/skills?${params}`);
  view.innerHTML = `${header("Learned skills", "Search and compare every learned skill, then inspect its text, evidence, usage, and evaluations.")}
    ${toolbar("skills")}
    <article class="panel"><table><thead><tr><th>Skill</th><th>Status</th><th>Created</th><th>Length</th><th>Usage</th><th>Evaluation</th><th>Evidence</th><th>Published to</th></tr></thead><tbody>
    ${data.items.map(item => `<tr><td><a class="link" href="#skill/${encodeURIComponent(item.name)}">${esc(item.name)}</a></td><td>${badge(item.status)}</td><td>${relative(item.created_at)}</td><td>${item.words === undefined ? "Unknown" : `${number(item.words)} words`}</td><td>${item.usage?.known ? number(item.usage.count) : "Unknown"}</td><td>${badge(item.evaluation_status || "unavailable")}</td><td>${number(item.evidence_count)}</td><td>${item.publication_targets?.length ? esc(item.publication_targets.join(", ")) : "Not published"}</td></tr>`).join("")}
    </tbody></table>${pager("skills",data)}</article>`;
  bindCatalog("skills", renderSkills);
}

function candidateAuthority(data) {
  return `<div class="notice authority-banner"><strong>${esc(data.label)}</strong>${esc(data.notice)}</div>`;
}

function candidateToolbar() {
  return `<div class="toolbar">
    <input class="control" id="candidates-query" placeholder="Search proposed name or lifecycle ID">
    <select class="control" id="candidates-status"><option value="">All record statuses</option><option value="shadow">Valid shadow records</option><option value="invalid">Invalid records</option></select>
    <select class="control" id="candidates-state"><option value="">All lifecycle states</option><option value="collecting">Collecting</option><option value="ready_for_draft">Ready for draft</option><option value="evaluating">Evaluating</option><option value="expired">Expired</option><option value="rejected">Rejected</option><option value="absorbed">Absorbed</option></select>
    <select class="control" id="candidates-sort"><option value="">Updated</option><option value="name">Name</option><option value="state">State</option></select>
  </div>`;
}

async function renderCandidates(cursor = "") {
  const query = state.query.candidates || {};
  const params = new URLSearchParams({limit:"25", ...query});
  if (cursor) params.set("cursor", cursor);
  const data = await api(`/api/v1/candidates?${params}`);
  view.innerHTML = `${header("Shadow candidates", "Read-only evidence packages awaiting conservative lifecycle decisions.", badge(data.authority))}
    ${candidateAuthority(data)}
    ${candidateToolbar()}
    <article class="panel"><table><thead><tr><th>Candidate</th><th>Lifecycle</th><th>Recurrence</th><th>Freshness</th><th>Evaluation</th><th>Exact candidate identity</th></tr></thead><tbody>
    ${data.items.length ? data.items.map(item => `<tr>
      <td>${item.status === "shadow" ? `<a class="link" href="#candidate/${encodeURIComponent(item.lifecycle_id)}">${esc(item.proposed_name)}</a>` : `<strong>${esc(item.source || "Invalid record")}</strong>`}<div class="submetric">${badge(item.status)}</div></td>
      <td>${esc(item.state_label)}<div class="submetric">${esc(item.state_reason)}</div></td>
      <td>${item.evidence ? `${number(item.evidence.verified)} verified · ${number(item.evidence.distinct_tasks)} tasks · ${number(item.evidence.distinct_sessions)} sessions` : "Unavailable"}</td>
      <td>${item.freshness ? `${item.freshness.fresh_evidence ? "Fresh evidence" : "No fresh evidence"} · ${item.freshness.days_until_expiry === null ? "No expiry" : `${number(item.freshness.days_until_expiry)}d to expiry`}` : "Unavailable"}</td>
      <td>${item.evaluation ? badge(item.evaluation.status_label) : badge("unavailable")}</td>
      <td class="mono">${esc(item.current_candidate_id)}</td>
    </tr>`).join("") : `<tr><td colspan="6"><div class="empty">No shadow candidate records.</div></td></tr>`}
    </tbody></table>${pager("candidates",data)}</article>`;
  bindCandidates();
}

function bindCandidates() {
  const apply = () => {
    state.query.candidates = {
      query: document.getElementById("candidates-query").value,
      status: document.getElementById("candidates-status").value,
      state: document.getElementById("candidates-state").value,
      sort: document.getElementById("candidates-sort").value,
    };
    renderCandidates().catch(setError);
  };
  ["query", "status", "state", "sort"].forEach(field => document.getElementById(`candidates-${field}`).addEventListener("change", apply));
  document.querySelectorAll('[data-page="candidates"]').forEach(button => button.addEventListener("click", () => {
    renderCandidates(button.dataset.direction === "reset" ? "" : button.dataset.cursor).catch(setError);
  }));
}

async function renderCandidate(lifecycleId) {
  const data = await api(`/api/v1/candidates/${encodeURIComponent(lifecycleId)}`);
  const freshness = data.freshness;
  view.innerHTML = `<a class="link back" href="#candidates">← Back to shadow candidates</a>
    ${header(data.proposed_name, "Candidate lifecycle, recurrence, freshness, and evaluation evidence.", badge(data.state_label))}
    ${candidateAuthority(data)}
    <div class="grid split">
      <article class="panel"><div class="panel-head"><h2>Lifecycle and recurrence</h2></div><dl class="definition">
        <dt>Lifecycle state</dt><dd>${esc(data.state_label)}</dd>
        <dt>State reason</dt><dd>${esc(data.state_reason)}</dd>
        <dt>State changed</dt><dd>${fullTime(data.state_changed_at)}</dd>
        <dt>Verified evidence</dt><dd>${number(data.evidence.verified)} of ${number(data.evidence.total)}</dd>
        <dt>Distinct tasks</dt><dd>${number(data.evidence.distinct_tasks)}</dd>
        <dt>Distinct sessions</dt><dd>${number(data.evidence.distinct_sessions)}</dd>
        <dt>Newest verified</dt><dd>${fullTime(freshness.newest_verified_evidence_at)}</dd>
        <dt>Freshness</dt><dd>${freshness.fresh_evidence ? "Fresh evidence present" : "No fresh verified evidence"}${freshness.past_expiry ? " · past expiry" : freshness.days_until_expiry === null ? "" : ` · ${number(freshness.days_until_expiry)} days until expiry`}</dd>
      </dl></article>
      <article class="panel"><div class="panel-head"><h2>Identity and authority</h2></div><dl class="definition">
        <dt>Lifecycle ID</dt><dd class="mono">${esc(data.lifecycle_id)}</dd>
        <dt>Candidate ID</dt><dd class="mono">${esc(data.current_candidate_id)}</dd>
        <dt>Revision</dt><dd>${number(data.candidate_revision_count)} immutable package revision(s)</dd>
        <dt>Package</dt><dd>${number(data.candidate_revision.file_count)} files · ${bytes(data.candidate_revision.bytes)}</dd>
        <dt>Recommendation</dt><dd>${esc(data.recommendation.label)}${data.recommendation.stale ? " · stale" : ""}</dd>
        <dt>Publication</dt><dd>Not published</dd>
        <dt>Activation</dt><dd>Not active</dd>
        <dt>CLI discovery</dt><dd>Not discoverable</dd>
      </dl></article>
      <article class="panel full-span"><div class="panel-head"><h2>Evaluation gates</h2><span>${badge(data.evaluation.status_label)}</span></div>
        <div class="gate-grid">${data.evaluation.gates.map(gate => `<div class="gate"><strong>${esc(gate.label || gate.name)}</strong>${badge(gate.status)}<p>${esc((gate.reasons || []).join(", ") || "No recorded gate reasons.")}</p></div>`).join("")}</div>
      </article>
      <article class="panel full-span"><div class="panel-head"><h2>Proposed procedure</h2></div><dl class="definition">
        <dt>Trigger</dt><dd>${esc(data.procedure.trigger)}</dd>
        <dt>Outcome</dt><dd>${esc(data.procedure.outcome)}</dd>
        <dt>Actions</dt><dd>${data.procedure.actions.map(esc).join("<br>")}</dd>
        <dt>Exclusions</dt><dd>${data.procedure.exclusions.map(esc).join("<br>")}</dd>
      </dl></article>
    </div>`;
}

function bindCatalog(kind, renderer) {
  const query = document.getElementById(`${kind}-query`);
  const status = document.getElementById(`${kind}-status`);
  const sort = document.getElementById(`${kind}-sort`);
  const apply = () => {
    state.query[kind] = {
      query: query.value,
      status: status.value,
      sort: sort.value,
    };
    renderer().catch(setError);
  };
  query.addEventListener("change", apply);
  status.addEventListener("change", apply);
  sort.addEventListener("change", apply);
  document.querySelectorAll(`[data-page="${kind}"]`).forEach(button => button.addEventListener("click", () => {
    renderer(button.dataset.direction === "reset" ? "" : button.dataset.cursor).catch(setError);
  }));
}

async function renderSkill(name) {
  const data = await api(`/api/v1/skills/${encodeURIComponent(name)}`);
  view.innerHTML = `<a class="link back" href="#skills">← Back to all skills</a>
    ${header(data.name, "Read the skill, inspect its evidence, and review its current authority.", badge(data.status))}
    <div class="grid split">
      <article class="panel"><div class="panel-head"><h2>Skill text</h2><span>${number(data.words)} words</span></div><pre class="skill-text">${esc(data.text)}</pre></article>
      <article class="panel"><div class="panel-head"><h2>Details</h2></div><dl class="definition">
        <dt>Created</dt><dd>${fullTime(data.created_at)}</dd><dt>Candidate</dt><dd>${esc(data.candidate_id)}</dd>
        <dt>Evidence</dt><dd>${number(data.evidence_count)}</dd><dt>Verified tasks</dt><dd>${number(data.verified_task_count)}</dd>
        <dt>Usage</dt><dd>${data.usage?.known ? number(data.usage.count) : "Unknown · no retained load records"}</dd>
        <dt>Evaluation</dt><dd>${badge(data.evaluation_status)}</dd>
        <dt>Published to</dt><dd>${data.publication_targets?.length ? esc(data.publication_targets.join(", ")) : "Not published"}</dd>
      </dl></article>
      <article class="panel full-span"><div class="panel-head"><h2>Evidence</h2><a class="link" href="#evidence/${encodeURIComponent(name)}">View all ${number(data.evidence_count)} summaries →</a></div>
        <table><thead><tr><th>Observed</th><th>Dream</th><th>CLI</th><th>Kind</th><th>Saved summary preview</th><th>Task</th></tr></thead><tbody>
        ${data.evidence.slice(0,5).map(item => `<tr><td>${relative(item.observed_at)}</td><td>${esc(item.dream_name)}</td><td>${esc(item.source)}</td><td>${esc(item.evidence_kind)}</td><td><a class="link" href="#evidence/${encodeURIComponent(name)}/${item.id}">${esc(item.summary)}</a></td><td>${esc(item.independence)}</td></tr>`).join("")}
        </tbody></table></article>
    </div>`;
}

async function renderEvidence(name, anchor) {
  const data = await api(`/api/v1/skills/${encodeURIComponent(name)}/evidence?limit=100`);
  view.innerHTML = `<a class="link back" href="#skill/${encodeURIComponent(name)}">← Back to ${esc(name)}</a>
    ${header(`${name} evidence`, "Saved findings with the exact retained transcript context when an anchor exists.")}
    <div class="evidence-stack">${data.items.map(item => `
      <article class="evidence" id="${esc(item.id)}"><header><div><h2>${esc(item.summary)}</h2><div class="muted">Dream: ${esc(item.dream_name)} · ${relative(item.observed_at)} · ${esc(item.evidence_kind)}</div></div><div>${badge(item.source || "Unknown CLI")} ${badge(item.independence)}</div></header>
      ${item.anchor_status !== "exact" ? `<div class="notice">${item.anchor_status === "historical-unanchored" ? "Historical evidence · no exact event anchor is retained." : "The retained evidence anchor is invalid or unavailable."}</div>` : ""}
      <div class="events">${item.events.map(event => `<div class="event ${event.highlighted ? "focus" : ""}"><small>${esc(event.kind)} · ${esc(event.source_event_id)}</small><p>${esc(event.text)}</p></div>`).join("")}</div>
      ${item.snapshot_sha256 ? `<div class="action-pad"><button class="control" data-transcript="${esc(item.snapshot_sha256)}">Open transcript</button></div>` : ""}
      </article>`).join("")}</div>`;
  document.querySelectorAll("[data-transcript]").forEach(button => button.addEventListener("click", () => {
    location.hash = `transcript/${button.dataset.transcript}`;
  }));
  if (anchor) requestAnimationFrame(() => document.getElementById(anchor)?.scrollIntoView({block:"start"}));
}

async function renderTranscript(digest) {
  const data = await api(`/api/v1/transcripts/${digest}`);
  const events = data.events || [];
  view.innerHTML = `<button class="link back" id="transcript-back">← Back to evidence</button>
    ${header("Retained transcript", "The exact bounded normalized snapshot Dreaming reviewed.")}
    <article class="panel"><div class="events">${events.map(event => `<div class="event"><small>${esc(event.kind)} · ${esc(event.source_event_id)}</small><p>${esc(event.text)}</p></div>`).join("")}</div></article>`;
  document.getElementById("transcript-back").addEventListener("click", () => history.back());
}

async function renderSystem() {
  const data = await api("/api/v1/system");
  view.innerHTML = `${header("System", "Installed roots, health, measured storage, and limits that actually exist.", badge(data.health.status))}
    <div class="grid split"><article class="panel"><div class="panel-head"><h2>Storage categories</h2></div><div class="list">${data.categories.map(item => `<div class="card"><div class="label">${esc(item.name)}</div><div class="metric">${bytes(item.bytes)}</div><div class="submetric">${number(item.items)} files · no category quota</div></div>`).join("")}</div></article>
    <article class="panel"><div class="panel-head"><h2>Filesystem capacity</h2></div>${data.filesystems.map(item => `<div class="card"><div class="label">${esc(item.path)}</div><div class="metric">${bytes(item.free)} free</div><div class="submetric">${bytes(item.used)} used of ${bytes(item.total)}</div></div>`).join("")}
      <div class="notice mt">Snapshots are limited to ${bytes(data.limits.snapshot_bytes)} each. There is no aggregate retention quota or automatic cleanup.</div>
    </article></div>`;
}

async function route() {
  clearError();
  const raw = location.hash.replace(/^#/, "") || "overview";
  const [name, firstPart, secondPart] = raw.split("/");
  state.route = name;
  document.querySelectorAll("[data-route]").forEach(link => link.classList.toggle("active", link.dataset.route === name || (name === "skill" || name === "evidence" || name === "transcript") && link.dataset.route === "skills" || name === "candidate" && link.dataset.route === "candidates"));
  try {
    if (name === "overview") await renderOverview();
    else if (name === "activity") await renderActivity();
    else if (name === "dreams") await renderDreams();
    else if (name === "candidates") await renderCandidates();
    else if (name === "candidate" && firstPart) await renderCandidate(decodeURIComponent(firstPart));
    else if (name === "skills") await renderSkills();
    else if (name === "skill" && firstPart) await renderSkill(decodeURIComponent(firstPart));
    else if (name === "evidence" && firstPart) await renderEvidence(decodeURIComponent(firstPart), secondPart);
    else if (name === "transcript" && firstPart) await renderTranscript(firstPart);
    else if (name === "system") await renderSystem();
    else { location.hash = "overview"; }
  } catch (error) {
    setError(error);
    view.innerHTML = `<div class="empty">This view is unavailable.</div>`;
  }
}

state.token = readToken();
authNote.hidden = Boolean(state.token);
window.addEventListener("hashchange", route);
route();
