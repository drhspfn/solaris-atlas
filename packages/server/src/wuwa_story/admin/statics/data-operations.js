(() => {
  const root = document.getElementById("sa-data-ops");
  if (!root) return;

  const esc = (v) =>
    String(v ?? "").replace(
      /[&<>"']/g,
      (c) =>
        ({
          "&": "&amp;",
          "<": "&lt;",
          ">": "&gt;",
          '"': "&quot;",
          "'": "&#39;",
        })[c],
    );
  const apiPrefix = () =>
    location.hostname.startsWith("api.") || location.port === "8000"
      ? ""
      : "/api";

  const noticeEl = document.getElementById("ops-notice");
  const errorEl = document.getElementById("ops-error");
  const showMsg = (msg, isError = false) => {
    if (isError) {
      errorEl.textContent = msg;
      errorEl.hidden = false;
      noticeEl.hidden = true;
    } else {
      noticeEl.textContent = msg;
      noticeEl.hidden = false;
      errorEl.hidden = true;
    }
  };

  async function apiFetch(path, body = undefined, method = "GET") {
    const opts = { method, credentials: "same-origin" };
    if (body !== undefined) {
      const csrfResp = await fetch(`${apiPrefix()}/auth/csrf`, {
        credentials: "same-origin",
      });
      if (!csrfResp.ok)
        throw new Error("Could not verify CSRF token. Refresh and sign in.");
      const { csrf_token } = await csrfResp.json();
      opts.headers = {
        "Content-Type": "application/json",
        "X-CSRF-Token": csrf_token,
      };
      opts.body = JSON.stringify(body);
    }
    const res = await fetch(`${apiPrefix()}${path}`, opts);
    if (!res.ok) {
      let detail = "";
      try {
        detail = (await res.json()).detail;
      } catch {}
      throw new Error(
        typeof detail === "string" && detail
          ? detail
          : `Request failed (${res.status})`,
      );
    }
    return res.json();
  }

  // Tabs
  root.querySelectorAll(".sa-tab").forEach((btn) => {
    btn.addEventListener("click", () => {
      const target = btn.dataset.tab;
      root
        .querySelectorAll(".sa-tab")
        .forEach((b) => b.classList.toggle("is-active", b === btn));
      root.querySelectorAll(".sa-tab-pane").forEach((p) => {
        p.hidden = p.dataset.pane !== target;
      });
    });
  });

  // Queue rendering
  const LABELS = {
    "wuwa.release-media.v1": "Release media",
    "wuwa.event-media.v1": "Event artwork",
    "wuwa.entity-media.v1": "Icons & character audio",
    "wuwa.asset-download.v1": "Game downloads",
    "wuwa.asset-extract.v1": "Maps & asset extraction",
    "wuwa.snapshot-build.v1": "Patch imports",
    "wuwa.story-agent.v1": "Story analysis",
    "wuwa.cutscene-vision.v1": "Cutscene analysis",
  };
  const MANAGED = new Set(
    Object.keys(LABELS).filter(
      (k) => !["wuwa.story-agent.v1", "wuwa.cutscene-vision.v1"].includes(k),
    ),
  );

  let currentQueues = [];
  try {
    currentQueues =
      JSON.parse(
        document.getElementById("ops-initial-queues")?.textContent || "{}",
      ).queues || [];
  } catch {}

  const queuesBody = document.getElementById("ops-queues-body");
  const showIdleEl = document.getElementById("ops-show-idle");

  function renderQueues(queuesData) {
    if (queuesData && Array.isArray(queuesData.queues)) {
      currentQueues = queuesData.queues;
    }
    const countMap = new Map();
    for (const q of currentQueues) countMap.set(q.name, q.ready || 0);
    const bases = currentQueues.filter(
      (q) => !q.name.endsWith(".failed") && !q.name.endsWith(".waiting"),
    );
    const totalReady = bases.reduce(
      (acc, q) =>
        acc + (q.ready || 0) + (countMap.get(`${q.name}.waiting`) || 0),
      0,
    );
    const totalActive = bases.reduce((acc, q) => acc + (q.active || 0), 0);
    document.getElementById("kpi-queue-count").textContent = String(
      bases.length,
    );
    document.getElementById("kpi-queue-totals").textContent =
      `${totalReady} waiting · ${totalActive} delivered`;

    const showIdle = showIdleEl.checked;
    const visible = bases.filter(
      (q) =>
        showIdle ||
        (q.ready || 0) +
          (q.active || 0) +
          (countMap.get(`${q.name}.waiting`) || 0) +
          (countMap.get(`${q.name}.failed`) || 0) >
          0,
    );
    if (!visible.length) {
      queuesBody.innerHTML = `<tr><td colspan="6" class="sa-muted">All queues are idle. Toggle "Show idle queues" to inspect connected workers.</td></tr>`;
      return;
    }
    queuesBody.innerHTML = visible
      .map((q) => {
        const waiting =
          (q.ready || 0) + (countMap.get(`${q.name}.waiting`) || 0);
        const failed = countMap.get(`${q.name}.failed`) || 0;
        const workersHtml = (q.workers || [])
          .map(
            (w) =>
              `<div class="sa-muted sa-mono">${esc(w.name)} · prefetch ${esc(w.prefetch)}</div>`,
          )
          .join("");
        const manageHtml = MANAGED.has(q.name)
          ? `<div class="sa-row">
             <button type="button" class="btn btn-secondary js-q-clear" data-name="${esc(q.name)}" data-scope="waiting" ${waiting === 0 ? "disabled" : ""}>Clear waiting</button>
             <button type="button" class="btn btn-secondary js-q-clear" data-name="${esc(q.name)}" data-scope="failed" ${failed === 0 ? "disabled" : ""}>Clear failed</button>
           </div>`
          : '<span class="sa-muted">—</span>';
        return `<tr>
        <td><strong>${esc(LABELS[q.name] || q.name)}</strong><div class="sa-muted sa-mono">${esc(q.name)}</div></td>
        <td class="sa-mono">${waiting}</td>
        <td class="sa-mono">${q.active || 0}</td>
        <td class="sa-mono">${failed}</td>
        <td>
          <details class="sa-details">
            <summary>${q.consumers || 0} connected</summary>
            ${workersHtml || '<div class="sa-muted">No active consumers</div>'}
          </details>
        </td>
        <td>${manageHtml}</td>
      </tr>`;
      })
      .join("");
  }

  showIdleEl.addEventListener("change", () => renderQueues());
  renderQueues();

  // Tasks rendering
  const tasksBody = document.getElementById("ops-tasks-body");
  const taskStatusEl = document.getElementById("ops-task-status");
  const loadMoreBtn = document.getElementById("ops-tasks-more");
  let tasksList = [];
  let nextBefore = null;

  function badgeClass(status) {
    if (status === "succeeded" || status === "completed") return "sa-badge--ok";
    if (status === "running") return "sa-badge--run";
    if (status === "failed") return "sa-badge--err";
    return "sa-badge--warn";
  }

  function renderTasks() {
    if (!tasksList.length) {
      tasksBody.innerHTML =
        '<tr><td colspan="4" class="sa-muted">No matching processing tasks.</td></tr>';
      return;
    }
    tasksBody.innerHTML = tasksList
      .map((t) => {
        const req = t.request || {};
        const title = (req.kind || t.processor || "task").replaceAll("_", " ");
        const meta = [
          req.game_version ? `v${req.game_version}` : "",
          req.parent_id ? `parent #${req.parent_id}` : "",
        ]
          .filter(Boolean)
          .join(" · ");
        const hasDetails =
          Boolean(t.error) || (t.result && Object.keys(t.result).length > 0);
        const detailsHtml = hasDetails
          ? `<details class="sa-details"><summary>${t.error ? "View error" : "View result"}</summary><pre class="sa-pre">${esc(t.error || JSON.stringify(t.result, null, 2))}</pre></details>`
          : "";
        const reportHtml = t.media_report_available
          ? `<button type="button" class="btn btn-secondary js-media-report" data-id="${t.id}" style="margin-top:.3rem">Media report</button>`
          : "";
        return `<tr>
        <td class="sa-mono">#${t.id}</td>
        <td><strong>${esc(title)}</strong>${meta ? `<div class="sa-muted sa-mono">${esc(meta)}</div>` : ""}</td>
        <td><span class="sa-badge ${badgeClass(t.status)}">${esc(String(t.status).replaceAll("_", " "))}</span></td>
        <td>${detailsHtml}${reportHtml}</td>
      </tr>`;
      })
      .join("");
    loadMoreBtn.hidden = !nextBefore;
  }

  async function fetchTasks(append = false) {
    if (!append) {
      tasksBody.innerHTML =
        '<tr><td colspan="4"><span class="sa-skeleton"></span></td></tr><tr><td colspan="4"><span class="sa-skeleton" style="width:75%"></span></td></tr>';
    }
    const qs = new URLSearchParams({ limit: "30" });
    if (taskStatusEl.value) qs.set("status", taskStatusEl.value);
    if (append && nextBefore) qs.set("before", String(nextBefore));
    const data = await apiFetch(
      `/admin/data-operations/tasks?${qs.toString()}`,
    );
    nextBefore = data.next_before;
    const incoming = data.tasks || [];
    tasksList = append ? [...tasksList, ...incoming] : incoming;
    const activeCount = (data.running || []).length;
    if (!taskStatusEl.value && !append) {
      document.getElementById("kpi-active-tasks").textContent =
        String(activeCount);
    }
    renderTasks();
  }

  async function refreshAll() {
    try {
      const [qData] = await Promise.all([
        apiFetch("/admin/data-operations/queues"),
        fetchTasks(false),
      ]);
      renderQueues(qData);
    } catch (e) {
      showMsg(e.message || "Refresh failed", true);
    }
  }

  taskStatusEl.addEventListener("change", () => {
    nextBefore = null;
    void fetchTasks(false);
  });
  loadMoreBtn.addEventListener("click", () => {
    void fetchTasks(true);
  });
  document.getElementById("ops-refresh-btn").addEventListener("click", () => {
    void refreshAll();
  });

  // Auto-polling toggle
  let pollTimer = null;
  let polling = false;
  const pollBtn = document.getElementById("ops-poll-toggle");
  const pollDot = document.getElementById("ops-poll-dot");
  const pollLabel = document.getElementById("ops-poll-label");
  const pollIntervalEl = document.getElementById("ops-poll-interval");

  function setPolling(enabled) {
    polling = enabled;
    if (pollTimer) {
      clearInterval(pollTimer);
      pollTimer = null;
    }
    pollDot.classList.toggle("sa-dot--live", enabled);
    pollLabel.textContent = enabled
      ? `Auto-refresh: ${pollIntervalEl.value}s`
      : "Auto-refresh: Off";
    if (enabled) {
      const ms = Number(pollIntervalEl.value || 10) * 1000;
      pollTimer = setInterval(() => {
        if (document.visibilityState === "visible") void refreshAll();
      }, ms);
    }
  }

  pollBtn.addEventListener("click", () => setPolling(!polling));
  pollIntervalEl.addEventListener("change", () => {
    if (polling) setPolling(true);
  });

  // Operations actions
  document
    .getElementById("form-snapshot")
    .addEventListener("submit", async (e) => {
      e.preventDefault();
      const version = e.target.version.value.trim();
      try {
        const res = await apiFetch(
          "/admin/data-operations/snapshots",
          { version },
          "POST",
        );
        showMsg(
          `Enqueued snapshot import for v${version} (task #${res.task_id || res.id || "ok"}).`,
        );
        void refreshAll();
      } catch (err) {
        showMsg(err.message, true);
      }
    });

  document
    .getElementById("form-client-download")
    .addEventListener("submit", async (e) => {
      e.preventDefault();
      const version = e.target.version.value.trim() || null;
      const tier = e.target.tier.value;
      try {
        await apiFetch(
          "/admin/data-operations/client-download",
          { version, tier },
          "POST",
        );
        showMsg(
          `Client download queued (${version || "live"} · ${tier.toUpperCase()}).`,
        );
        void refreshAll();
      } catch (err) {
        showMsg(err.message, true);
      }
    });

  document
    .getElementById("btn-client-check")
    .addEventListener("click", async () => {
      try {
        await apiFetch("/admin/data-operations/client-update", {}, "POST");
        showMsg("Client update check queued.");
        void refreshAll();
      } catch (err) {
        showMsg(err.message, true);
      }
    });

  document.getElementById("form-maps").addEventListener("submit", async (e) => {
    e.preventDefault();
    const version = e.target.version.value.trim() || null;
    const tier = e.target.tier.value;
    try {
      await apiFetch(
        "/admin/data-operations/maps/build",
        { version, tier },
        "POST",
      );
      showMsg(
        `Map build queued (${version || "latest"} · ${tier.toUpperCase()}).`,
      );
      void refreshAll();
    } catch (err) {
      showMsg(err.message, true);
    }
  });

  document
    .getElementById("btn-events-import")
    .addEventListener("click", async () => {
      try {
        await apiFetch("/admin/data-operations/events/import", {}, "POST");
        showMsg("Events catalog import queued.");
        void refreshAll();
      } catch (err) {
        showMsg(err.message, true);
      }
    });

  document
    .getElementById("btn-notices-import")
    .addEventListener("click", async () => {
      try {
        await apiFetch("/admin/data-operations/notices/import", {}, "POST");
        showMsg("Notices import queued.");
        void refreshAll();
      } catch (err) {
        showMsg(err.message, true);
      }
    });

  root.addEventListener("click", async (e) => {
    const mediaBtn = e.target.closest(".js-release-media");
    if (mediaBtn) {
      const id = mediaBtn.dataset.releaseId;
      const ver = mediaBtn.dataset.version;
      try {
        await apiFetch(
          `/admin/data-operations/releases/${id}/media`,
          {},
          "POST",
        );
        showMsg(`Media import queued for release v${ver}.`);
        void refreshAll();
      } catch (err) {
        showMsg(err.message, true);
      }
      return;
    }

    const qBtn = e.target.closest(".js-q-clear");
    if (qBtn) {
      const { name, scope } = qBtn.dataset;
      if (
        !confirm(
          `Clear ${scope} tasks in ${LABELS[name] || name}? Running tasks and history will be kept.`,
        )
      )
        return;
      try {
        const res = await apiFetch(
          "/admin/data-operations/queues/clear",
          { name, scope },
          "POST",
        );
        showMsg(
          `${LABELS[name] || name}: ${res.cancelled} cancelled, ${res.removed} removed.`,
        );
        void refreshAll();
      } catch (err) {
        showMsg(err.message, true);
      }
      return;
    }

    const repBtn = e.target.closest(".js-media-report");
    if (repBtn) {
      const id = repBtn.dataset.id;
      const modal = document.getElementById("ops-modal");
      const body = document.getElementById("ops-modal-body");
      document.getElementById("ops-modal-title").textContent =
        `Media Import Report · Task #${id}`;
      body.innerHTML =
        '<span class="sa-skeleton" style="margin-bottom:.5rem"></span><span class="sa-skeleton" style="width:65%"></span>';
      modal.showModal();
      try {
        const rep = await apiFetch(
          `/admin/data-operations/tasks/${id}/media-report`,
        );
        const p = apiPrefix();
        body.innerHTML = `
          <div class="sa-row" style="margin-bottom:.75rem">
            <a class="btn btn-secondary" href="${p}/admin/data-operations/tasks/${id}/media-inventory?format=csv" download>Download CSV</a>
            <a class="btn btn-secondary" href="${p}/admin/data-operations/tasks/${id}/media-inventory?format=json" download>Download JSON</a>
          </div>
          <pre class="sa-pre" style="max-height:420px">${esc(JSON.stringify(rep, null, 2))}</pre>`;
      } catch (err) {
        body.innerHTML = `<div class="sa-msg sa-msg--err">${esc(err.message)}</div>`;
      }
    }
  });

  document.getElementById("ops-modal-close").addEventListener("click", () => {
    document.getElementById("ops-modal").close();
  });

  // Initial tasks load
  void fetchTasks(false);
})();
