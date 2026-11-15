(() => {
  const root = document.getElementById("sa-cutscene-analysis");
  if (!root) return;

  const apiPrefix = () =>
    location.hostname.startsWith("api.") || location.port === "8000"
      ? ""
      : "/api";

  let initial = {};
  try {
    initial = JSON.parse(
      document.getElementById("ca-initial-data")?.textContent || "{}",
    );
  } catch {
    initial = {};
  }

  let csrfToken = null;
  let query = initial.query || "";
  let selectedRunId = initial.selected_run_id || null;
  let visionEnabled = Boolean(initial.assets_data?.enabled);
  let assetsList = Array.isArray(initial.assets_data?.assets)
    ? initial.assets_data.assets
    : [];
  let assetsNextBefore = initial.assets_data?.next_before || null;
  let jobsList = Array.isArray(initial.jobs_data?.jobs)
    ? initial.jobs_data.jobs
    : [];
  let jobsNextBefore = initial.jobs_data?.next_before || null;
  let pollTimer = null;
  let busy = false;

  const banner = document.getElementById("ca-banner");
  const assetsBody = document.getElementById("ca-assets-body");
  const jobsBody = document.getElementById("ca-jobs-body");
  const previewBox = document.getElementById("ca-preview-box");
  const loadMoreAssetsBtn = document.getElementById("ca-load-more-assets");
  const loadMoreJobsBtn = document.getElementById("ca-load-more-jobs");
  const pollToggleBtn = document.getElementById("ca-poll-toggle");
  const pollSelect = document.getElementById("ca-poll-select");
  const pollDot = document.getElementById("ca-poll-dot");
  const pollLabel = document.getElementById("ca-poll-label");

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

  function badgeClass(status) {
    const s = String(status || "").toLowerCase();
    if (["completed", "done"].includes(s)) return "sa-badge--completed";
    if (["running", "queued", "pending"].includes(s))
      return "sa-badge--running";
    if (["failed", "error"].includes(s)) return "sa-badge--failed";
    if (["paused", "budget_exceeded"].includes(s)) return "sa-badge--paused";
    return "sa-badge--neutral";
  }

  function renderAssetStatus(a) {
    if ((a.reports_count || 0) > 0) {
      return '<span class="sa-badge sa-badge--ok">Analyzed</span>';
    }
    if (a.latest_run_status) {
      return `<span class="sa-badge ${badgeClass(a.latest_run_status)}">${esc(a.latest_run_status)}</span>`;
    }
    if ((a.previews_count || 0) > 0 || (a.atlases_count || 0) > 0) {
      return '<span class="sa-badge sa-badge--warn">Artifacts only</span>';
    }
    return '<span class="sa-badge sa-badge--neutral">Not analyzed</span>';
  }

  function renderAssets() {
    document.getElementById("ca-kpi-enabled").textContent = visionEnabled
      ? "Enabled"
      : "Disabled";
    document.getElementById("ca-kpi-assets").textContent = String(
      assetsList.length,
    );
    document.getElementById("ca-kpi-query").textContent = query || "All";
    document.getElementById("ca-assets-meta").textContent =
      `${assetsList.length} shown`;

    assetsBody.innerHTML = assetsList.length
      ? assetsList
          .map(
            (a) => `<tr>
 <td class="text-break">
 <div class="fw-semibold">${esc(a.reference)}</div>
 <div class="text-secondary small">Node #${a.id}</div>
 </td>
 <td>
 <div class="d-flex flex-wrap gap-1 mb-1">
 <span class="sa-badge sa-badge--neutral">${esc(a.game_version)}</span>
 ${renderAssetStatus(a)}
 </div>
 <div class="text-secondary small">${a.reports_count || 0} reports · ${a.runs_count || 0} runs · ${a.previews_count || 0} previews</div>
 </td>
 <td class="text-end">
 <div class="d-flex flex-wrap justify-content-end gap-1">
 <button type="button" class="btn btn-outline-secondary ca-preview-btn" data-asset-id="${a.id}" data-version="${esc(a.game_version)}" data-ref="${esc(a.reference)}">Preview</button>
 <button type="button" class="btn btn-primary ca-analyze-btn" data-asset-id="${a.id}" data-version="${esc(a.game_version)}" ${visionEnabled ? "" : "disabled"}>Analyze</button>
 ${a.has_artifacts ? `<button type="button" class="btn btn-outline-danger ca-clear-btn" data-asset-id="${a.id}" data-version="${esc(a.game_version)}" data-ref="${esc(a.reference)}">Clear</button>` : ""}
 </div>
 </td>
 </tr>`,
          )
          .join("")
      : '<tr><td colspan="3" class="text-secondary text-center py-3">No cutscene assets matched.</td></tr>';

    if (assetsNextBefore) {
      loadMoreAssetsBtn.classList.remove("d-none");
    } else {
      loadMoreAssetsBtn.classList.add("d-none");
    }
  }

  function renderJobs() {
    document.getElementById("ca-kpi-jobs").textContent = String(
      jobsList.length,
    );
    const active = jobsList.filter((j) =>
      ["queued", "running"].includes(j.status),
    ).length;
    document.getElementById("ca-kpi-active-jobs").textContent =
      `Running: ${active}`;
    document.getElementById("ca-jobs-meta").textContent =
      `${jobsList.length} shown`;

    jobsBody.innerHTML = jobsList.length
      ? jobsList
          .map(
            (j) => `<tr>
 <td>
 <div class="fw-semibold">#${j.id} · ${esc(j.reference || `Node #${j.asset_node_id}`)}</div>
 <div class="text-secondary small">${esc(j.game_version || "")}${j.title_only ? " · title-only" : ""}</div>
 ${j.error ? `<div class="text-danger small">${esc(j.error)}</div>` : ""}
 </td>
 <td><span class="sa-badge ${badgeClass(j.status)}">${esc(j.status)}</span></td>
 <td class="small">Step ${j.step ?? 0} · ${j.frames_done ?? 0} done / ${j.frames_remaining ?? 0} left</td>
 <td class="small">
 $${Number(j.cost_usd || 0).toFixed(4)}
 <div class="text-secondary">${Number(j.tokens_input || 0).toLocaleString()} / ${Number(j.tokens_output || 0).toLocaleString()}</div>
 </td>
 <td class="text-end">
 ${!["queued", "running", "completed"].includes(j.status) ? `<button type="button" class="btn btn-outline-primary ca-resume-btn" data-run-id="${j.id}" data-output-tokens="${j.output_tokens || ""}" ${visionEnabled ? "" : "disabled"}>Resume</button>` : '<span class="text-secondary small">—</span>'}
 </td>
 </tr>`,
          )
          .join("")
      : '<tr><td colspan="5" class="text-secondary text-center py-3">No vision jobs recorded.</td></tr>';

    if (jobsNextBefore) {
      loadMoreJobsBtn.classList.remove("d-none");
    } else {
      loadMoreJobsBtn.classList.add("d-none");
    }
  }

  async function refreshAll({ keepBanner = false } = {}) {
    try {
      const analyzedOnly = Boolean(
        document.getElementById("ca-analyzed-only")?.checked,
      );
      const qsAssets = new URLSearchParams({ q: query, limit: "30" });
      if (analyzedOnly) qsAssets.set("analyzed_only", "true");
      const qsJobs = new URLSearchParams({ limit: "30" });
      if (selectedRunId) qsJobs.set("run_id", String(selectedRunId));
      const [assetsRes, jobsRes] = await Promise.all([
        apiFetch(`/admin/story-agent/cutscenes/assets?${qsAssets}`),
        apiFetch(`/admin/story-agent/cutscenes/jobs?${qsJobs}`),
      ]);
      visionEnabled = Boolean(assetsRes.enabled);
      assetsList = assetsRes.assets || [];
      assetsNextBefore = assetsRes.next_before || null;
      jobsList = jobsRes.jobs || [];
      jobsNextBefore = jobsRes.next_before || null;
      renderAssets();
      renderJobs();
      document.getElementById("ca-updated-at").textContent =
        `Updated ${new Date().toLocaleTimeString()}`;
      if (!keepBanner) showBanner("");
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
          void refreshAll({ keepBanner: true });
        }
      }, interval);
    } else {
      pollDot.classList.remove("is-live");
      pollLabel.textContent = "Auto: Off";
    }
  }

  document
    .getElementById("ca-analyzed-only")
    ?.addEventListener("change", () => void refreshAll());

  document.getElementById("ca-search-form")?.addEventListener("submit", (e) => {
    e.preventDefault();
    query = document.getElementById("ca-search-input").value.trim();
    const url = new URL(location.href);
    if (query) url.searchParams.set("q", query);
    else url.searchParams.delete("q");
    history.replaceState(null, "", url);
    void refreshAll();
  });

  document
    .getElementById("ca-clear-run-filter")
    ?.addEventListener("click", (e) => {
      selectedRunId = null;
      const url = new URL(location.href);
      url.searchParams.delete("job");
      history.replaceState(null, "", url);
      e.currentTarget.remove();
      void refreshAll();
    });

  root.addEventListener("click", async (e) => {
    const previewBtn = e.target.closest(".ca-preview-btn");
    if (previewBtn) {
      const id = previewBtn.dataset.assetId;
      const version = previewBtn.dataset.version;
      const ref = previewBtn.dataset.ref;
      previewBox.classList.remove("d-none");
      previewBox.innerHTML = `<div class="sa-skeleton" style="width:100%;aspect-ratio:16/9;"></div>`;
      try {
        const data = await apiFetch(
          `/admin/story-agent/cutscenes/assets/${id}/playback?game_version=${encodeURIComponent(version)}`,
        );
        const media = data.media?.[ref] || Object.values(data.media || {})[0];
        if (!media?.url) {
          previewBox.innerHTML =
            '<div class="text-warning small">No video URL available for this asset.</div>';
          return;
        }
        const tracks = Array.isArray(media.audio_tracks)
          ? media.audio_tracks
          : [];
        previewBox.innerHTML = `
 <div class="d-flex justify-content-between align-items-center mb-2">
 <div class="small fw-semibold">${esc(ref)} (${esc(version)})</div>
 <button type="button" class="btn btn-outline-secondary" id="ca-close-preview">Close</button>
 </div>
 <video controls playsinline preload="metadata" class="w-100 rounded bg-black" style="max-height:320px;" src="${esc(media.url)}"></video>
 ${tracks.length ? `<div class="small text-secondary mt-1">Audio tracks: ${tracks.map((t) => esc(t.language || t.label || "track")).join(", ")}</div>` : ""}
 `;
      } catch (err) {
        previewBox.innerHTML = `<div class="alert alert-danger py-2 px-3 small mb-0">${esc(err.message)}</div>`;
      }
      return;
    }

    if (e.target.id === "ca-close-preview") {
      previewBox.classList.add("d-none");
      previewBox.innerHTML = "";
      return;
    }

    const analyzeBtn = e.target.closest(".ca-analyze-btn");
    if (analyzeBtn && !busy) {
      busy = true;
      try {
        const res = await apiFetch("/admin/story-agent/cutscenes/jobs", {
          method: "POST",
          body: {
            asset_node_ids: [Number(analyzeBtn.dataset.assetId)],
            game_version: analyzeBtn.dataset.version,
          },
        });
        const job = res.jobs?.[0];
        showBanner(
          job
            ? `Queued vision job #${job.id} (${job.status})`
            : "Queued vision job",
          "success",
        );
        await refreshAll({ keepBanner: true });
      } catch (err) {
        showBanner(err.message, "danger");
      } finally {
        busy = false;
      }
      return;
    }

    const clearBtn = e.target.closest(".ca-clear-btn");
    if (clearBtn && !busy) {
      const assetId = Number(clearBtn.dataset.assetId);
      const version = clearBtn.dataset.version;
      const ref = clearBtn.dataset.ref || `Node #${assetId}`;
      if (
        !window.confirm(
          `Delete analysis, previews, atlases, and R2 files for ${ref} (${version})?`,
        )
      ) {
        return;
      }
      busy = true;
      try {
        const res = await apiFetch(
          `/admin/story-agent/cutscenes/assets/${assetId}/analysis?game_version=${encodeURIComponent(version)}`,
          { method: "DELETE" },
        );
        showBanner(
          `Cleared ${ref} (${version}): ${res.deleted_runs} runs, ${res.deleted_references} refs, ${res.deleted_s3_objects} R2 objects.`,
          "success",
        );
        await refreshAll({ keepBanner: true });
      } catch (err) {
        showBanner(err.message, "danger");
      } finally {
        busy = false;
      }
      return;
    }

    const resumeBtn = e.target.closest(".ca-resume-btn");
    if (resumeBtn && !busy) {
      busy = true;
      const runId = resumeBtn.dataset.runId;
      const out = resumeBtn.dataset.outputTokens;
      try {
        const res = await apiFetch(
          `/admin/story-agent/cutscenes/jobs/${runId}/resume`,
          {
            method: "POST",
            body: out ? { output_tokens: Number(out) } : {},
          },
        );
        showBanner(`Resumed vision job #${res.id}: ${res.status}`, "success");
        await refreshAll({ keepBanner: true });
      } catch (err) {
        showBanner(err.message, "danger");
      } finally {
        busy = false;
      }
    }
  });

  loadMoreAssetsBtn?.addEventListener("click", async () => {
    if (!assetsNextBefore) return;
    try {
      const qs = new URLSearchParams({
        q: query,
        limit: "30",
        before: String(assetsNextBefore),
      });
      if (document.getElementById("ca-analyzed-only")?.checked) {
        qs.set("analyzed_only", "true");
      }
      const res = await apiFetch(`/admin/story-agent/cutscenes/assets?${qs}`);
      assetsList = assetsList.concat(res.assets || []);
      assetsNextBefore = res.next_before || null;
      renderAssets();
    } catch (err) {
      showBanner(err.message, "danger");
    }
  });

  loadMoreJobsBtn?.addEventListener("click", async () => {
    if (!jobsNextBefore) return;
    try {
      const qs = new URLSearchParams({
        limit: "30",
        before: String(jobsNextBefore),
      });
      if (selectedRunId) qs.set("run_id", String(selectedRunId));
      const res = await apiFetch(`/admin/story-agent/cutscenes/jobs?${qs}`);
      jobsList = jobsList.concat(res.jobs || []);
      jobsNextBefore = res.next_before || null;
      renderJobs();
    } catch (err) {
      showBanner(err.message, "danger");
    }
  });

  document
    .getElementById("ca-refresh-btn")
    ?.addEventListener("click", () => void refreshAll());
  pollToggleBtn?.addEventListener("click", () => {
    const current = Number(pollToggleBtn.dataset.interval || 0);
    setPolling(current > 0 ? 0 : Number(pollSelect.value || 10000) || 10000);
  });
  pollSelect?.addEventListener("change", () => setPolling(pollSelect.value));
})();
