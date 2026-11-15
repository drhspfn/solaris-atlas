(() => {
  const root = document.getElementById("sa-story-agent");
  if (!root) return;

  const apiPrefix = () =>
    location.hostname.startsWith("api.") || location.port === "8000"
      ? ""
      : "/api";

  let initial = {};
  try {
    initial = JSON.parse(
      document.getElementById("sa-initial-data")?.textContent || "{}",
    );
  } catch {
    initial = {};
  }

  let csrfToken = null;
  let jobsList = Array.isArray(initial.jobs_data?.jobs)
    ? initial.jobs_data.jobs
    : [];
  let jobsNextBefore = initial.jobs_data?.next_before || null;
  let selectedJobId = initial.selected_job_id || null;
  let selectedDetail = initial.selected_detail || null;
  let revisitsList = Array.isArray(initial.revisits_data?.revisits)
    ? initial.revisits_data.revisits
    : [];
  let revisitsNextBefore = initial.revisits_data?.next_before || null;
  let pollTimer = null;
  let busy = false;

  const banner = document.getElementById("sa-banner");
  const runsListEl = document.getElementById("sa-runs-list");
  const detailTitleEl = document.getElementById("sa-detail-title");
  const detailBodyEl = document.getElementById("sa-detail-body");
  const detailActionsEl = document.getElementById("sa-detail-actions");
  const loadMoreRunsBtn = document.getElementById("sa-load-more-runs");
  const loadMoreRevisitsBtn = document.getElementById("sa-load-more-revisits");
  const pollToggleBtn = document.getElementById("sa-poll-toggle");
  const pollSelect = document.getElementById("sa-poll-select");
  const pollDot = document.getElementById("sa-poll-dot");
  const pollLabel = document.getElementById("sa-poll-label");

  function esc(val) {
    return String(val ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function showBanner(msg, kind = "info") {
    if (!msg) {
      banner.className = "alert d-none";
      banner.textContent = "";
      return;
    }
    banner.className = `alert alert-${kind} mb-3`;
    banner.textContent = msg;
  }

  async function ensureCsrf() {
    if (csrfToken) return csrfToken;
    const res = await fetch(`${apiPrefix()}/auth/csrf`, {
      credentials: "include",
    });
    if (!res.ok) throw new Error(`CSRF request failed (${res.status})`);
    const data = await res.json();
    csrfToken = data.csrf_token || data.token || "";
    return csrfToken;
  }

  async function apiFetch(path, opts = {}) {
    const method = (opts.method || "GET").toUpperCase();
    const headers = { ...(opts.headers || {}) };
    if (method !== "GET" && method !== "HEAD") {
      headers["X-CSRF-Token"] = await ensureCsrf();
      if (opts.body && !headers["Content-Type"]) {
        headers["Content-Type"] = "application/json";
      }
    }
    const res = await fetch(`${apiPrefix()}${path}`, {
      credentials: "include",
      ...opts,
      method,
      headers,
      body:
        opts.body && typeof opts.body !== "string"
          ? JSON.stringify(opts.body)
          : opts.body,
    });
    const text = await res.text();
    let payload = null;
    try {
      payload = text ? JSON.parse(text) : null;
    } catch {
      payload = text;
    }
    if (!res.ok) {
      if (res.status === 403) csrfToken = null;
      const detail =
        payload?.detail ||
        payload?.message ||
        (typeof payload === "string" ? payload : "") ||
        `HTTP ${res.status}`;
      throw new Error(detail);
    }
    return payload;
  }

  function skeletonHtml(lines = 4) {
    return Array.from({ length: lines }, (_, i) => {
      const w = i === 0 ? "55%" : i === lines - 1 ? "40%" : "90%";
      return `<div class="sa-skeleton sa-skeleton-line mb-2" style="width:${w}"></div>`;
    }).join("");
  }

  function badgeClass(status) {
    const s = String(status || "").toLowerCase();
    if (["completed", "done", "resolved"].includes(s))
      return "sa-badge--completed";
    if (["running", "queued", "pending", "in_progress"].includes(s))
      return "sa-badge--running";
    if (["failed", "error", "provider_rejected", "rate_limited"].includes(s))
      return "sa-badge--failed";
    if (["paused", "budget_exceeded", "step_limit"].includes(s))
      return "sa-badge--paused";
    return "sa-badge--neutral";
  }

  function renderRunsList() {
    document.getElementById("sa-runs-meta").textContent =
      `${jobsList.length} shown`;
    document.getElementById("kpi-runs-count").textContent = String(
      jobsList.length,
    );
    const active = jobsList.filter((j) =>
      ["queued", "running"].includes(j.status),
    ).length;
    document.getElementById("kpi-active-runs").textContent =
      `Running: ${active}`;

    if (!jobsList.length) {
      runsListEl.innerHTML =
        '<div class="p-3 text-secondary small">No story agent runs found.</div>';
    } else {
      runsListEl.innerHTML = jobsList
        .map(
          (job) => `
 <button type="button" class="sa-run-item ${selectedJobId === job.id ? "is-selected" : ""}" data-run-id="${job.id}">
 <div class="d-flex justify-content-between align-items-center gap-2">
 <span class="fw-semibold">#${job.id} · ${esc(job.mode || "analysis")}</span>
 <span class="sa-badge ${badgeClass(job.status)}">${esc(job.status)}</span>
 </div>
 <div class="small text-secondary mt-1">
 Step ${job.step ?? 0}/${job.max_steps ?? "?"} · ${esc(job.model || "")} · $${Number(job.cost_usd || 0).toFixed(4)}
 </div>
 ${job.error ? `<div class="small text-danger text-truncate mt-1">${esc(job.error)}</div>` : ""}
 </button>`,
        )
        .join("");
    }

    if (jobsNextBefore) {
      loadMoreRunsBtn.classList.remove("d-none");
      loadMoreRunsBtn.dataset.before = String(jobsNextBefore);
    } else {
      loadMoreRunsBtn.classList.add("d-none");
    }
  }

  function renderUsage(usage) {
    if (!usage) return;
    const todayRow = (usage.days || []).find((d) => d.day === usage.today);
    const spent = todayRow ? todayRow.spent_usd : "0.00";
    const reserved = todayRow ? todayRow.reserved_usd : "0.00";
    const tokens = todayRow
      ? Number(todayRow.spent_tokens || 0) +
        Number(todayRow.reserved_tokens || 0)
      : 0;

    document.getElementById("kpi-budget").innerHTML =
      `$${esc(spent)} <span class="text-secondary fs-6">/ $${esc(usage.daily_budget_usd)}</span>`;
    document.getElementById("kpi-reserved").textContent =
      `Reserved: $${reserved} (${usage.timezone})`;
    document.getElementById("kpi-tokens").textContent = tokens.toLocaleString();
    document.getElementById("kpi-token-limit").textContent =
      `Limit: ${Number(usage.daily_token_limit || 0).toLocaleString()}`;

    const tbody = document.getElementById("sa-usage-body");
    if (tbody && Array.isArray(usage.days)) {
      tbody.innerHTML = usage.days.length
        ? usage.days
            .map(
              (d) => `<tr>
 <td>${esc(d.day)}</td>
 <td>$${esc(d.spent_usd)}</td>
 <td>$${esc(d.reserved_usd)}</td>
 <td>${Number(d.spent_tokens || 0).toLocaleString()}</td>
 <td>${Number(d.reserved_tokens || 0).toLocaleString()}</td>
 </tr>`,
            )
            .join("")
        : '<tr><td colspan="5" class="text-secondary text-center py-3">No usage records yet.</td></tr>';
    }
  }

  function renderRevisits() {
    document.getElementById("kpi-revisits-count").textContent = String(
      revisitsList.length,
    );
    const tbody = document.getElementById("sa-revisits-body");
    if (!tbody) return;
    tbody.innerHTML = revisitsList.length
      ? revisitsList
          .map(
            (r) => `<tr>
 <td>#${r.id}</td>
 <td>${esc(r.document_id)}</td>
 <td>${esc(r.release_id)}</td>
 <td><span class="sa-badge ${badgeClass(r.status)}">${esc(r.status)}</span></td>
 <td>${r.run_id ? `<a href="#" class="sa-jump-run" data-run-id="${r.run_id}">#${r.run_id}</a>` : "—"}</td>
 <td>${esc(r.candidate_count)}</td>
 <td class="text-danger small">${esc(r.error || "")}</td>
 </tr>`,
          )
          .join("")
      : '<tr><td colspan="7" class="text-secondary text-center py-3">No revisits recorded.</td></tr>';
    if (revisitsNextBefore) {
      loadMoreRevisitsBtn.classList.remove("d-none");
    } else {
      loadMoreRevisitsBtn.classList.add("d-none");
    }
  }

  function renderDetail(detail) {
    selectedDetail = detail;
    if (!detail) {
      detailTitleEl.textContent = "Run Inspector";
      detailActionsEl.innerHTML = "";
      detailBodyEl.innerHTML =
        '<div class="text-secondary small py-4 text-center">Select a run on the left to inspect steps, calls, traces, and resume controls.</div>';
      return;
    }

    detailTitleEl.innerHTML = `Run #${detail.id} <span class="sa-badge ${badgeClass(detail.status)} ms-2">${esc(detail.status)}</span>`;
    detailActionsEl.className = "d-flex flex-wrap gap-2 align-items-center";
    detailActionsEl.innerHTML = `
 <button type="button" class="btn btn-outline-primary" data-resume-steps="5">Resume +5 steps</button>
 <button type="button" class="btn btn-outline-secondary" data-resume-steps="0" data-finalize="1">Finalize</button>
 `;

    const limits = detail.limits || {};
    const calls = Array.isArray(detail.calls) ? detail.calls : [];
    const canResume = !["queued", "running"].includes(detail.status);

    detailBodyEl.innerHTML = `
 <div class="sa-detail-stats">
   <div class="sa-detail-stat"><div class="sa-detail-stat__label">Step</div><div class="sa-detail-stat__val">${detail.step ?? 0} / ${limits.max_steps ?? "?"}</div></div>
   <div class="sa-detail-stat"><div class="sa-detail-stat__label">Model</div><div class="sa-detail-stat__val">${esc(limits.model || "—")}</div></div>
   <div class="sa-detail-stat"><div class="sa-detail-stat__label">Tokens (in / out)</div><div class="sa-detail-stat__val">${Number(detail.tokens_input || 0).toLocaleString()} / ${Number(detail.tokens_output || 0).toLocaleString()}</div></div>
   <div class="sa-detail-stat"><div class="sa-detail-stat__label">Cost</div><div class="sa-detail-stat__val">$${Number(detail.cost_usd || 0).toFixed(4)}</div></div>
 </div>
 ${detail.stage ? `<div class="sa-muted">Stage: <span class="text-light fw-semibold">${esc(detail.stage)}</span>${detail.recovery ? ` · Recovery: <span class="text-warning">${esc(detail.recovery)}</span>` : ""}</div>` : ""}
 ${detail.error ? `<div class="sa-msg sa-msg--err">${esc(detail.error)}</div>` : ""}

 <form id="sa-resume-form" class="sa-resume-box">
   <div class="sa-resume-grid">
     <div>
       <label class="form-label mb-1" for="sa-extra-steps">Extra steps</label>
       <input type="number" min="0" max="100" value="5" id="sa-extra-steps" name="extra_steps" class="form-control">
     </div>
     <div>
       <label class="form-label mb-1" for="sa-output-tokens">Output tokens</label>
       <input type="number" min="128" max="32000" id="sa-output-tokens" placeholder="${esc(limits.max_output_tokens || "")}" name="output_tokens" class="form-control">
     </div>
     <div>
       <label class="sa-check-field" for="sa-compact-ctx">
         <input type="checkbox" class="form-check-input" id="sa-compact-ctx" name="compact_context">
         <span>Compact context</span>
       </label>
     </div>
     <div>
       <button type="submit" class="btn btn-primary" ${canResume ? "" : "disabled"}>Resume</button>
     </div>
   </div>
 </form>

 <h3 class="h6 mb-2">Model Calls (${calls.length})</h3>
 <div class="sa-table-wrap mb-3">
 <table class="sa-table">
 <thead>
 <tr>
 <th>Step</th>
 <th>Kind</th>
 <th>Status</th>
 <th>Tokens</th>
 <th>Cost</th>
 <th>Errors</th>
 <th></th>
 </tr>
 </thead>
 <tbody>
 ${
   calls.length
     ? calls
         .map(
           (c) => `<tr>
 <td>${c.step}</td>
 <td>${esc(c.kind)}</td>
 <td><span class="sa-badge ${badgeClass(c.status)}">${esc(c.status)}</span></td>
 <td>${Number(c.input_tokens || 0).toLocaleString()} / ${Number(c.output_tokens || 0).toLocaleString()}</td>
 <td>${c.cost_usd != null ? `$${esc(c.cost_usd)}` : `res $${esc(c.reserved_usd)}`}</td>
 <td>${c.tool_errors ? `<span class="text-danger">${c.tool_errors} err</span>` : "0"}${c.repeated_tools ? ` · ${c.repeated_tools} rep` : ""}</td>
 <td class="text-end">
 <button type="button" class="btn btn-outline-secondary sa-trace-btn" data-call-id="${c.id}">Trace</button>
 ${["uncertain", "reserved"].includes(c.status) ? `<button type="button" class="btn btn-outline-warning sa-reconcile-btn ms-1" data-call-id="${c.id}">Reconcile</button>` : ""}
 </td>
 </tr>`,
         )
         .join("")
     : '<tr><td colspan="7" class="text-secondary text-center py-2">No calls recorded yet.</td></tr>'
 }
 </tbody>
 </table>
 </div>

 ${detail.request ? `<details class="sa-details mb-2"><summary>Request payload</summary><pre class="sa-pre mt-2">${esc(JSON.stringify(detail.request, null, 2))}</pre></details>` : ""}
 ${detail.assessment ? `<details class="sa-details mb-2"><summary>Assessment</summary><pre class="sa-pre mt-2">${esc(JSON.stringify(detail.assessment, null, 2))}</pre></details>` : ""}
 ${detail.result ? `<details class="sa-details"><summary>Raw output</summary><pre class="sa-pre mt-2">${esc(JSON.stringify(detail.result, null, 2))}</pre></details>` : ""}
 `;
  }

  function updateSelectedRunInList() {
    runsListEl.querySelectorAll(".sa-run-item").forEach((el) => {
      el.classList.toggle(
        "is-selected",
        Number(el.dataset.runId) === selectedJobId,
      );
    });
  }

  async function selectRun(id, updateUrl = true) {
    selectedJobId = Number(id);
    if (updateUrl) {
      const url = new URL(location.href);
      url.searchParams.set("job", String(selectedJobId));
      history.replaceState(null, "", url);
    }
    updateSelectedRunInList();
    detailTitleEl.textContent = `Run #${selectedJobId}`;
    detailActionsEl.innerHTML = "";
    detailBodyEl.innerHTML = skeletonHtml(6);
    try {
      const detail = await apiFetch(`/admin/story-agent/jobs/${selectedJobId}`);
      renderDetail(detail);
    } catch (err) {
      detailBodyEl.innerHTML = `<div class="alert alert-danger">${esc(err.message)}</div>`;
    }
  }

  async function refreshAll() {
    try {
      const [jobsRes, usageRes, revisitsRes] = await Promise.all([
        apiFetch("/admin/story-agent/jobs?limit=30"),
        apiFetch("/admin/story-agent/usage"),
        apiFetch("/admin/story-agent/revisits"),
      ]);
      jobsList = jobsRes.jobs || [];
      jobsNextBefore = jobsRes.next_before || null;
      revisitsList = revisitsRes.revisits || [];
      revisitsNextBefore = revisitsRes.next_before || null;
      renderRunsList();
      renderUsage(usageRes);
      renderRevisits();
      document.getElementById("sa-runs-updated").textContent =
        `Updated ${new Date().toLocaleTimeString()}`;
      if (selectedJobId) {
        const detail = await apiFetch(
          `/admin/story-agent/jobs/${selectedJobId}`,
        );
        renderDetail(detail);
      }
      showBanner("");
    } catch (err) {
      showBanner(err.message, "danger");
    }
  }

  function setPolling(ms) {
    if (pollTimer) {
      clearInterval(pollTimer);
      pollTimer = null;
    }
    const interval = Number(ms) || 0;
    pollSelect.value = String(interval);
    pollToggleBtn.dataset.interval = String(interval);
    if (interval > 0) {
      pollDot.classList.add("is-live");
      pollLabel.textContent = `Auto: ${interval / 1000}s`;
      pollTimer = setInterval(() => {
        if (!busy && document.visibilityState === "visible") {
          void refreshAll();
        }
      }, interval);
    } else {
      pollDot.classList.remove("is-live");
      pollLabel.textContent = "Auto: Off";
    }
  }

  // Tabs
  root.querySelectorAll(".sa-tab").forEach((tab) => {
    tab.addEventListener("click", () => {
      const name = tab.dataset.tab;
      root
        .querySelectorAll(".sa-tab")
        .forEach((t) => t.classList.toggle("is-active", t === tab));
      root
        .querySelectorAll(".sa-tab-pane")
        .forEach((p) => p.classList.toggle("d-none", p.id !== `tab-${name}`));
    });
  });

  // Create run
  document
    .getElementById("sa-create-form")
    ?.addEventListener("submit", async (e) => {
      e.preventDefault();
      if (busy) return;
      const form = e.currentTarget;
      const questId = Number(form.quest_id.value);
      const gameVersion = form.game_version.value.trim();
      if (!questId) return;
      busy = true;
      try {
        const body = { quest_id: questId, locale: "en" };
        if (gameVersion) body.game_version = gameVersion;
        const res = await apiFetch("/admin/story-agent/jobs", {
          method: "POST",
          body,
        });
        showBanner(`Run #${res.id}: ${res.status}`, "success");
        await refreshAll();
        await selectRun(res.id);
      } catch (err) {
        showBanner(err.message, "danger");
      } finally {
        busy = false;
      }
    });

  // Click delegation
  root.addEventListener("click", async (e) => {
    const runBtn = e.target.closest(".sa-run-item, .sa-jump-run");
    if (runBtn) {
      e.preventDefault();
      root.querySelector('.sa-tab[data-tab="runs"]')?.click();
      await selectRun(runBtn.dataset.runId);
      return;
    }

    const quickResume = e.target.closest("[data-resume-steps]");
    if (quickResume && selectedJobId && !busy) {
      busy = true;
      try {
        const res = await apiFetch(
          `/admin/story-agent/jobs/${selectedJobId}/resume`,
          {
            method: "POST",
            body: {
              extra_steps: Number(quickResume.dataset.resumeSteps || 0),
              finalize: quickResume.dataset.finalize === "1",
            },
          },
        );
        showBanner(`Run #${res.id}: ${res.status}`, "success");
        await refreshAll();
      } catch (err) {
        showBanner(err.message, "danger");
      } finally {
        busy = false;
      }
      return;
    }

    const traceBtn = e.target.closest(".sa-trace-btn");
    if (traceBtn && selectedJobId) {
      const callId = traceBtn.dataset.callId;
      document.getElementById("sa-trace-title").textContent =
        `Run #${selectedJobId} · Call #${callId}`;
      document.getElementById("sa-trace-body").innerHTML = skeletonHtml(6);
      const modalEl = document.getElementById("sa-trace-modal");
      if (window.bootstrap?.Modal)
        window.bootstrap.Modal.getOrCreateInstance(modalEl).show();
      try {
        const trace = await apiFetch(
          `/admin/story-agent/jobs/${selectedJobId}/calls/${callId}`,
        );
        const tools = Array.isArray(trace.tools) ? trace.tools : [];
        document.getElementById("sa-trace-body").innerHTML = `
 ${trace.text ? `<h6>Model Response</h6><pre class="sa-pre mb-3">${esc(trace.text)}</pre>` : ""}
 <h6>Tool Calls (${tools.length})</h6>
 ${tools.length ? `<pre class="sa-pre">${esc(JSON.stringify(tools, null, 2))}</pre>` : '<div class="text-secondary small">No tool calls recorded.</div>'}
 `;
      } catch (err) {
        document.getElementById("sa-trace-body").innerHTML =
          `<div class="alert alert-danger">${esc(err.message)}</div>`;
      }
      return;
    }

    const recBtn = e.target.closest(".sa-reconcile-btn");
    if (recBtn && !busy) {
      const callId = recBtn.dataset.callId;
      const inp = prompt("Actual input tokens (0 if unknown):", "0");
      if (inp === null) return;
      const out = prompt("Actual output tokens (0 if unknown):", "0");
      if (out === null) return;
      busy = true;
      try {
        await apiFetch(`/admin/story-agent/calls/${callId}/reconcile`, {
          method: "POST",
          body: {
            input_tokens: Number(inp) || 0,
            output_tokens: Number(out) || 0,
          },
        });
        showBanner(`Call #${callId} reconciled.`, "success");
        await refreshAll();
      } catch (err) {
        showBanner(err.message, "danger");
      } finally {
        busy = false;
      }
    }
  });

  // Resume form submit
  root.addEventListener("submit", async (e) => {
    if (e.target.id !== "sa-resume-form" || !selectedJobId || busy) return;
    e.preventDefault();
    busy = true;
    const form = e.target;
    const body = {
      extra_steps: Number(form.extra_steps.value || 0),
      compact_context: Boolean(form.compact_context.checked),
    };
    if (form.output_tokens.value.trim()) {
      body.output_tokens = Number(form.output_tokens.value);
    }
    try {
      const res = await apiFetch(
        `/admin/story-agent/jobs/${selectedJobId}/resume`,
        {
          method: "POST",
          body,
        },
      );
      showBanner(`Run #${res.id} resumed: ${res.status}`, "success");
      await refreshAll();
    } catch (err) {
      showBanner(err.message, "danger");
    } finally {
      busy = false;
    }
  });

  loadMoreRunsBtn?.addEventListener("click", async () => {
    if (!jobsNextBefore) return;
    try {
      const res = await apiFetch(
        `/admin/story-agent/jobs?limit=30&before=${jobsNextBefore}`,
      );
      jobsList = jobsList.concat(res.jobs || []);
      jobsNextBefore = res.next_before || null;
      renderRunsList();
    } catch (err) {
      showBanner(err.message, "danger");
    }
  });

  loadMoreRevisitsBtn?.addEventListener("click", async () => {
    if (!revisitsNextBefore) return;
    try {
      const res = await apiFetch(
        `/admin/story-agent/revisits?before=${revisitsNextBefore}`,
      );
      revisitsList = revisitsList.concat(res.revisits || []);
      revisitsNextBefore = res.next_before || null;
      renderRevisits();
    } catch (err) {
      showBanner(err.message, "danger");
    }
  });

  document
    .getElementById("sa-refresh-btn")
    ?.addEventListener("click", () => void refreshAll());
  pollToggleBtn?.addEventListener("click", () => {
    const current = Number(pollToggleBtn.dataset.interval || 0);
    setPolling(current > 0 ? 0 : Number(pollSelect.value || 10000) || 10000);
  });
  pollSelect?.addEventListener("change", () => setPolling(pollSelect.value));

  if (selectedDetail) {
    renderDetail(selectedDetail);
  }
})();
