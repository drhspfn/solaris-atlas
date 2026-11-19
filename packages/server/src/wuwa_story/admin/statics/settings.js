(() => {
  const root = document.getElementById("sa-app-settings");
  if (!root) return;

  const apiPrefix = () =>
    location.hostname.startsWith("api.") || location.port === "8000"
      ? ""
      : "/api";

  let state = { categories: [], settings: [] };
  try {
    state = JSON.parse(
      document.getElementById("as-initial-data")?.textContent ||
        '{"categories":[],"settings":[]}',
    );
  } catch {
    state = { categories: [], settings: [] };
  }

  let activeCategory = "all";
  let filterText = "";
  let csrfToken = null;

  const banner = document.getElementById("as-banner");
  const tabsEl = document.getElementById("as-category-tabs");
  const containerEl = document.getElementById("as-categories-root");
  const filterInput = document.getElementById("as-filter-input");

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
        payload?.detail?.message ||
        payload?.detail ||
        payload?.message ||
        (typeof payload === "string" ? payload : "") ||
        `HTTP ${res.status}`;
      throw new Error(
        typeof detail === "string" ? detail : JSON.stringify(detail),
      );
    }
    return payload;
  }

  function formatValidation(s) {
    const parts = [];
    const v = s.validation || {};
    if (v.min !== undefined) parts.push(`min: ${v.min}`);
    if (v.max !== undefined) parts.push(`max: ${v.max}`);
    if (v.min_length !== undefined) parts.push(`min_len: ${v.min_length}`);
    if (v.max_length !== undefined) parts.push(`max_len: ${v.max_length}`);
    if (v.min_items !== undefined) parts.push(`min_items: ${v.min_items}`);
    if (v.max_items !== undefined) parts.push(`max_items: ${v.max_items}`);
    if (v.pattern) parts.push(`pattern: ${v.pattern}`);
    if (v.timezone) parts.push("IANA timezone");
    if (s.multi) parts.push("multi");
    if (s.is_secret) parts.push("secret");
    return parts.join(" · ");
  }

  function renderInput(s) {
    const effective = s.effective_value;
    const v = s.validation || {};
    const id = `as-input-${CSS.escape(s.key)}`;

    if (s.value_type === "boolean") {
      const checked = Boolean(effective);
      return `<label class="form-check form-switch mb-0 d-inline-flex align-items-center gap-2">
        <input type="checkbox" class="form-check-input as-input" data-key="${esc(s.key)}" ${checked ? "checked" : ""} />
        <span class="small text-secondary">${checked ? "true" : "false"}</span>
      </label>`;
    }

    if (s.value_type === "enum" && Array.isArray(s.choices)) {
      const options = s.choices
        .map(
          (choice) =>
            `<option value="${esc(choice)}" ${String(effective) === String(choice) ? "selected" : ""}>${esc(choice)}</option>`,
        )
        .join("");
      return `<select class="form-select form-select-sm as-input" data-key="${esc(s.key)}" style="min-width: 14rem">${options}</select>`;
    }

    if (
      (s.value_type === "list" || s.multi) &&
      Array.isArray(s.choices) &&
      s.choices.length
    ) {
      const currentSet = new Set(
        Array.isArray(effective) ? effective.map(String) : [],
      );
      const boxes = s.choices
        .map(
          (choice) => `<label class="form-check form-check-inline mb-1 small">
            <input type="checkbox" class="form-check-input as-multi-choice" data-key="${esc(s.key)}" value="${esc(choice)}" ${currentSet.has(String(choice)) ? "checked" : ""} />
            <span class="form-check-label">${esc(choice)}</span>
          </label>`,
        )
        .join("");
      return `<div class="d-flex flex-wrap gap-2">${boxes}</div>`;
    }

    if (s.value_type === "list" || s.multi) {
      const textVal = Array.isArray(effective)
        ? effective.join(", ")
        : String(effective ?? "");
      return `<input type="text" class="form-control form-control-sm as-input" data-key="${esc(s.key)}" value="${esc(textVal)}" placeholder="Comma-separated items" style="min-width: 16rem" />`;
    }

    if (s.value_type === "secret" || s.is_secret) {
      const placeholder = s.is_configured
        ? "Configured (enter new value to replace)"
        : "Not set";
      return `<input type="password" autocomplete="new-password" class="form-control form-control-sm as-input" data-key="${esc(s.key)}" value="" placeholder="${esc(placeholder)}" style="min-width: 16rem" />`;
    }

    if (["integer", "number", "decimal"].includes(s.value_type)) {
      const step = s.value_type === "integer" ? "1" : "any";
      const minAttr = v.min !== undefined ? `min="${esc(v.min)}"` : "";
      const maxAttr = v.max !== undefined ? `max="${esc(v.max)}"` : "";
      return `<input type="number" step="${step}" ${minAttr} ${maxAttr} id="${id}" class="form-control form-control-sm as-input" data-key="${esc(s.key)}" value="${esc(effective ?? "")}" style="min-width: 12rem" />`;
    }

    return `<input type="text" class="form-control form-control-sm as-input" data-key="${esc(s.key)}" value="${esc(effective ?? "")}" style="min-width: 16rem" />`;
  }

  function render() {
    const categories = state.categories || [];
    tabsEl.innerHTML = [
      `<button type="button" class="btn btn-sm ${activeCategory === "all" ? "btn-primary" : "btn-outline-secondary"} as-cat-btn" data-cat="all">All</button>`,
      ...categories.map(
        (cat) =>
          `<button type="button" class="btn btn-sm ${activeCategory === cat ? "btn-primary" : "btn-outline-secondary"} as-cat-btn" data-cat="${esc(cat)}">${esc(cat)}</button>`,
      ),
    ].join("");

    const needle = filterText.trim().toLowerCase();
    const visibleCategories =
      activeCategory === "all"
        ? categories
        : categories.filter((c) => c === activeCategory);

    const sections = [];
    for (const category of visibleCategories) {
      const rows = (state.settings || []).filter((s) => {
        if (s.category !== category) return false;
        if (!needle) return true;
        return (
          s.key.toLowerCase().includes(needle) ||
          s.name.toLowerCase().includes(needle) ||
          (s.description || "").toLowerCase().includes(needle)
        );
      });
      if (!rows.length) continue;

      sections.push(`<div class="sa-panel mb-3">
        <div class="sa-panel-head">
          <h2 class="sa-panel-title">${esc(category)}</h2>
          <span class="sa-muted">${rows.length} setting(s)</span>
        </div>
        <div class="sa-table-wrap">
          <table class="sa-table">
            <thead>
              <tr>
                <th>Setting</th>
                <th>Value</th>
                <th>Source / Default</th>
                <th class="text-end">Actions</th>
              </tr>
            </thead>
            <tbody>
              ${rows
                .map((s) => {
                  const rules = formatValidation(s);
                  const defaultLabel = s.is_secret
                    ? s.default_value
                      ? "configured in env"
                      : "not set"
                    : Array.isArray(s.default_value)
                      ? s.default_value.join(", ")
                      : String(s.default_value ?? "—");
                  return `<tr data-setting-row="${esc(s.key)}">
                    <td style="max-width: 24rem">
                      <div class="fw-semibold">${esc(s.name)}</div>
                      <div class="small sa-mono text-secondary">${esc(s.key)} · <span class="badge bg-secondary-lt">${esc(s.value_type)}</span></div>
                      ${s.description ? `<div class="small text-secondary mt-1">${esc(s.description)}</div>` : ""}
                      ${rules ? `<div class="small text-info mt-1">${esc(rules)}</div>` : ""}
                    </td>
                    <td>${renderInput(s)}</td>
                    <td class="small">
                      <span class="sa-badge ${s.source === "database" ? "sa-badge--running" : "sa-badge--neutral"}">${s.source === "database" ? "DB override" : "Default"}</span>
                      <div class="text-secondary mt-1">Default: <span class="sa-mono">${esc(defaultLabel)}</span></div>
                    </td>
                    <td class="text-end text-nowrap">
                      <button type="button" class="btn btn-sm btn-primary as-save-btn" data-key="${esc(s.key)}">Save</button>
                      ${s.source === "database" ? `<button type="button" class="btn btn-sm btn-outline-secondary ms-1 as-reset-btn" data-key="${esc(s.key)}">Reset</button>` : ""}
                    </td>
                  </tr>`;
                })
                .join("")}
            </tbody>
          </table>
        </div>
      </div>`);
    }

    containerEl.innerHTML = sections.length
      ? sections.join("")
      : '<div class="sa-panel p-4 text-center text-secondary">No settings matched the current filter.</div>';
  }

  function readInputValue(spec, rowEl) {
    if (spec.value_type === "boolean") {
      return Boolean(rowEl.querySelector(".as-input")?.checked);
    }
    if (
      (spec.value_type === "list" || spec.multi) &&
      Array.isArray(spec.choices) &&
      spec.choices.length
    ) {
      return Array.from(rowEl.querySelectorAll(".as-multi-choice:checked")).map(
        (el) => el.value,
      );
    }
    const input = rowEl.querySelector(".as-input");
    const raw = input ? input.value : "";
    if (spec.value_type === "secret" || spec.is_secret) {
      if (!raw)
        throw new Error(
          "Enter a secret value or click Reset to revert to env default.",
        );
      return raw;
    }
    return raw;
  }

  async function reload() {
    const data = await apiFetch("/admin/settings");
    state = data;
    render();
  }

  tabsEl.addEventListener("click", (e) => {
    const btn = e.target.closest(".as-cat-btn");
    if (!btn) return;
    activeCategory = btn.dataset.cat || "all";
    render();
  });

  filterInput?.addEventListener("input", () => {
    filterText = filterInput.value || "";
    render();
  });

  document.getElementById("as-refresh-btn")?.addEventListener("click", () => {
    void reload()
      .then(() => showBanner(""))
      .catch((err) => showBanner(err.message, "danger"));
  });

  containerEl.addEventListener("click", async (e) => {
    const saveBtn = e.target.closest(".as-save-btn");
    const resetBtn = e.target.closest(".as-reset-btn");
    if (!saveBtn && !resetBtn) return;

    const key = (saveBtn || resetBtn).dataset.key;
    const spec = (state.settings || []).find((s) => s.key === key);
    const rowEl = containerEl.querySelector(
      `tr[data-setting-row="${CSS.escape(key)}"]`,
    );
    if (!spec || !rowEl) return;

    try {
      const payload = resetBtn
        ? { reset: true, value: null }
        : { reset: false, value: readInputValue(spec, rowEl) };
      const res = await apiFetch(`/admin/settings/${encodeURIComponent(key)}`, {
        method: "PUT",
        body: payload,
      });
      const updated = res.setting;
      state.settings = (state.settings || []).map((s) =>
        s.key === key ? updated : s,
      );
      render();
      showBanner(
        resetBtn ? `Reset ${key} to default.` : `Saved ${key}.`,
        "success",
      );
    } catch (err) {
      showBanner(`${key}: ${err.message}`, "danger");
    }
  });

  render();
})();
