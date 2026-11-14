(() => {
  "use strict";
  globalThis.createVisualMemoryVideoReview = ({
    root,
    api,
    escape: esc,
    message,
    chooseLinks,
    dialog,
    closeDialog,
    isBusy = () => false,
  }) => {
    const $ = (selector) => root.querySelector(selector);
    const state = {
      page: 0,
      size: 20,
      total: 0,
      catalog: [],
      sources: [],
      source: null,
      scan: null,
      generation: 0,
      catalogGeneration: 0,
      frameOffset: 0,
      frameLimit: 12,
      requestSequence: 0,
      timer: null,
      busy: false,
      opened: false,
    };
    const video = $("video");
    const drafts = new Map();
    const saving = new Set();
    const errors = new Map();
    const renderedFrames = new Map();
    let editorDirty = () => false;
    const storageKey = "solaris.visual-video-drafts.v1";
    try {
      for (const [key, value] of JSON.parse(
        sessionStorage.getItem(storageKey) || "[]",
      ))
        if (Array.isArray(value.links)) drafts.set(key, value);
    } catch {
      /* Storage can be disabled in private browsing. */
    }
    function persistDrafts() {
      try {
        sessionStorage.setItem(storageKey, JSON.stringify([...drafts]));
      } catch {
        /* In-memory drafts still survive polling. */
      }
    }
    const reviewable = (scan) =>
      scan &&
      !scan.reviewed &&
      (scan.can_review_frames ??
        [
          "pending",
          "running",
          "failed",
          "succeeded",
          "completed",
          "success",
        ].includes(scan.status));
    const observationKey = (scan, frame, index) =>
      `${scan.source?.source_sha256 || scan.source?.sha256 || state.source?.source_sha256}:${scan.id}:${frame}:${index}`;
    const baseline = (d) =>
      JSON.stringify([d.bbox, d.decision, d.reference_id, d.links]);
    const linkKey = (links) =>
      JSON.stringify(
        (links || [])
          .map((link) => [link.identity_id, link.appearance_id || null])
          .sort((a, b) => a[0] - b[0] || (a[1] || 0) - (b[1] || 0)),
      );
    function displayedDetection(scan, frame, d, index) {
      const key = observationKey(scan, frame, index),
        draft = drafts.get(key);
      return {
        ...d,
        links: draft?.links || d.links || [],
        dirty: !!draft,
        conflict: !!draft && draft.baseline !== baseline(d),
        saving: saving.has(key),
        error: errors.get(key),
      };
    }
    globalThis.addEventListener?.("beforeunload", (event) => {
      if (!drafts.size && !saving.size && !editorDirty()) return;
      event.preventDefault();
      event.returnValue = "";
    });
    const status = (text) => {
      $("#vm-video-status").textContent = text;
    };
    const active = (scan) =>
      ["queued", "pending", "running", "processing"].includes(scan?.status);
    const completed = (scan) =>
      ["completed", "succeeded", "success"].includes(scan?.status);
    const time = (value) => {
      const milliseconds = Math.round(Math.max(0, Number(value) || 0) * 1000);
      const seconds = Math.floor(milliseconds / 1000),
        fraction = milliseconds % 1000;
      return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}${fraction ? "." + String(fraction).padStart(3, "0") : ""}`;
    };
    function parseTime(value) {
      const text = value.trim();
      if (/^\d+(?:\.\d+)?$/.test(text)) {
        const seconds = Number(text);
        if (Number.isFinite(seconds)) return seconds;
      }
      const match = /^(\d+):([0-5]\d(?:\.\d+)?)$/.exec(text);
      if (match) {
        const seconds = Number(match[1]) * 60 + Number(match[2]);
        if (Number.isFinite(seconds)) return seconds;
      }
      throw new Error(
        "Enter a valid non-negative time in seconds or minutes:seconds.",
      );
    }
    const safeURL = (value) => {
      if (!value) return "";
      try {
        const url = new URL(value, location.href);
        return ["http:", "https:"].includes(url.protocol) ? url.href : "";
      } catch {
        return "";
      }
    };
    const sourceKey = (source) =>
      String(
        source?.source_reference_id ??
          source?.file_reference_id ??
          source?.id ??
          "",
      );
    function controls() {
      const scan = state.scan;
      const pending = scan?.unresolved_count;
      const prefix = scan ? observationKey(scan, "", "").slice(0, -1) : "";
      const hasDrafts =
        scan && [...drafts.keys()].some((key) => key.startsWith(prefix));
      $("#vm-video-start").disabled =
        state.busy ||
        !state.source ||
        !sourceKey(state.source) ||
        !state.source.source_sha256 ||
        !state.source.game_version ||
        !state.source.asset_version ||
        !state.source.cutscene_key;
      $("#vm-video-start").textContent = "Start / open scan";
      $("#vm-video-reviewed").disabled =
        state.busy ||
        saving.size > 0 ||
        hasDrafts ||
        !completed(scan) ||
        !!scan.reviewed ||
        pending !== 0;
      $("#vm-video-reject-all").disabled =
        state.busy ||
        saving.size > 0 ||
        hasDrafts ||
        !completed(scan) ||
        !(pending > 0) ||
        !!scan.reviewed;
      $("#vm-video-variant").disabled = state.busy || !state.sources.length;
      $("#vm-frame-prev").disabled =
        state.busy || !scan || state.frameOffset === 0;
      $("#vm-frame-next").disabled =
        state.busy || !scan || scan.next_frame_offset == null;
    }
    async function run(button, action) {
      if (state.busy) return;
      state.busy = true;
      controls();
      button?.setAttribute("aria-busy", "true");
      try {
        await action();
      } catch (e) {
        message(e.message || "Request failed. Please retry.", true);
      } finally {
        state.busy = false;
        button?.removeAttribute("aria-busy");
        controls();
      }
    }
    async function catalog() {
      const generation = ++state.catalogGeneration;
      const params = new URLSearchParams({
        q: $("#vm-video-search [name=q]").value,
        limit: String(state.size),
        offset: String(state.page * state.size),
      });
      $("#vm-video-catalog").innerHTML =
        '<div class="vm-skeleton" role="status" aria-label="Loading cutscenes"></div>'.repeat(
          3,
        );
      try {
        const data = await api("/videos/catalog?" + params);
        if (generation !== state.catalogGeneration) return;
        state.catalog = data.items || [];
        state.total = data.total ?? state.catalog.length;
        $("#vm-video-catalog").innerHTML =
          state.catalog
            .map(
              (item, i) =>
                `<article class="vm-card"><h3>${esc(item.name || item.title || item.cg_name || item.cutscene_key || "Cutscene")}</h3><p class="vm-meta">${esc(item.game_version || "")}</p><button class="btn btn-secondary" type="button" data-video-item="${i}">View sources</button></article>`,
            )
            .join("") ||
          '<p class="vm-empty">No cutscenes match this search.</p>';
        $("#vm-video-page").textContent =
          `${state.total ? state.page * state.size + 1 : 0}–${Math.min((state.page + 1) * state.size, state.total)} of ${state.total}`;
        $("#vm-video-prev").disabled = state.page === 0;
        $("#vm-video-next").disabled =
          (state.page + 1) * state.size >= state.total;
      } catch (e) {
        if (generation === state.catalogGeneration) {
          $("#vm-video-catalog").innerHTML =
            '<p class="vm-empty">Could not load cutscenes. Search to retry.</p>';
          message(e.message, true);
        }
      }
    }
    function resetSource() {
      ++state.generation;
      ++state.requestSequence;
      state.frameOffset = 0;
      clearTimeout(state.timer);
      state.scan = null;
      state.source = null;
      video.pause();
      video.removeAttribute("src");
      video.load();
      $("#vm-video-frames").replaceChildren();
      renderedFrames.clear();
      controls();
    }
    async function selectItem(item) {
      resetSource();
      const generation = state.generation;
      $("#vm-video-review").hidden = false;
      $("#vm-video-title").textContent =
        item.name ||
        item.title ||
        item.cg_name ||
        item.cutscene_key ||
        "Cutscene";
      status("Loading published sources…");
      $("#vm-video-variant").innerHTML = "";
      const params = new URLSearchParams();
      params.set("cutscene_key", item.cutscene_key || item.name);
      if (item.game_version) params.set("game_version", item.game_version);
      const data = await api("/videos/sources?" + params);
      if (generation !== state.generation) return;
      state.sources = (data.sources || data.items || []).map((source) => ({
        ...source,
        game_version:
          source.game_version || data.game_version || item.game_version,
      }));
      $("#vm-video-variant").innerHTML = state.sources
        .map(
          (source, i) =>
            `<option value="${i}">${esc(source.label || `Asset ${source.asset_node_id}`)} · ${esc(source.asset_version || "unknown recording version")}</option>`,
        )
        .join("");
      if (!state.sources.length) {
        status("No published video sources are available.");
        controls();
        return;
      }
      await selectSource();
    }
    async function selectSource() {
      resetSource();
      const generation = state.generation;
      state.source = state.sources[Number($("#vm-video-variant").value)];
      if (!state.source) return;
      const source = state.source;
      $("#vm-video-source").textContent =
        `Published source ${sourceKey(source)}${source.source_sha256 ? " · SHA-256 " + source.source_sha256 : ""}`;
      if (
        sourceKey(source) &&
        source.game_version &&
        source.asset_version &&
        source.cutscene_key
      )
        video.src = `/admin/visual-memory/videos/sources/${encodeURIComponent(sourceKey(source))}/video?${new URLSearchParams({ game_version: source.game_version, asset_version: source.asset_version, cutscene_key: source.cutscene_key })}`;
      else {
        status(
          "This source is missing its cutscene or version context. Reload the source list before scanning.",
        );
        controls();
        return;
      }
      if (!source.source_sha256) {
        status(
          "This source has no recorded hash. Reload the source list before scanning.",
        );
        controls();
        return;
      }
      status("Checking previous scans…");
      controls();
      if (source.scan?.id) await refresh(source.scan.id, generation);
      else {
        status("No scan has been selected for this source.");
        controls();
      }
    }
    function render(scan) {
      state.scan = scan;
      $("#vm-video-drafts").textContent = drafts.size
        ? `${drafts.size} unsaved selection(s), kept in this browser tab. Save pending or approve each object. Ready frames can be reviewed while scanning continues.`
        : "Ready frames can be reviewed while scanning continues. Matching scans reuse saved frames and decisions.";
      const frameCount = scan.frame_count ?? 0;
      $("#vm-frame-page").textContent =
        `${frameCount ? state.frameOffset + 1 : 0}–${Math.min(state.frameOffset + (scan.frames || []).length, frameCount)} of ${frameCount} frames · ${scan.unresolved_count ?? "Unknown"} unresolved`;
      const progress = scan.progress
        ? ` · ${typeof scan.progress === "string" ? scan.progress : scan.progress.total == null ? `${scan.progress.completed ?? 0} frames sampled` : `${scan.progress.completed ?? 0} / ${scan.progress.total} frames`}`
        : "";
      const sampling =
        scan.sampling_mode === "segment"
          ? `Segment ${scan.start_seconds}–${scan.end_seconds}s`
          : scan.sampling_mode === "frame"
            ? `Frame at ${scan.timestamp_seconds}s · offset ${scan.sampling_frame_offset ?? 0}`
            : "Full video";
      status(
        `${scan.reviewed ? "Reviewed scan" : "Scan " + (scan.status || "unknown")}${progress} · ${sampling}${scan.error ? " · " + scan.error : ""} · SHA-256 ${scan.source?.source_sha256 || scan.source?.sha256 || "not available"}`,
      );
      const frameHTML = (scan.frames || []).map(
        (frame) =>
          `<article class="vm-box vm-video-frame" data-frame-index="${esc(frame.index)}"><div><button type="button" class="vm-video-thumb" data-seek="${esc(frame.time)}"><img src="${esc(safeURL(frame.image_url))}" alt="Sample at ${esc(time(frame.time))}" loading="lazy"><span>Seek to ${esc(time(frame.time))}</span></button></div><div><h3>Sample at ${esc(time(frame.time))}</h3><button type="button" class="btn btn-secondary" data-edit-frame="${esc(frame.index)}" ${!reviewable(scan) ? "disabled" : ""}>Edit frame boxes</button>${
            (frame.detections || [])
              .map((raw, i) => {
                const d = displayedDetection(
                  scan,
                  frame.index,
                  raw,
                  raw.index ?? i,
                );
                return `<div class="vm-candidate"><strong>Object ${i + 1} · ${esc(d.decision || "unresolved")}</strong><canvas class="vm-video-crop" data-frame-crop="${esc(frame.index)}" data-crop-index="${i}" aria-label="Crop for object ${i + 1}"></canvas><p class="vm-meta">${esc(d.status || "Unknown")} · ${esc((d.candidates || []).map((c) => c.name || "Unnamed").join(", ") || "No candidate evidence")}</p><p>Selected: ${esc((d.links || []).map((l) => [l.name || l.identity_name || `Identity ${l.identity_id}`, l.appearance_name || (l.appearance_id ? `appearance ${l.appearance_id}` : "")].filter(Boolean).join(" · ")).join(", ") || "None — unresolved")}</p>${d.dirty ? '<p class="vm-meta">Unsaved selection</p>' : ""}${d.conflict ? '<p class="vm-error">Object changed on the server. Choose its identity again.</p>' : ""}${d.error ? `<p class="vm-error">${esc(d.error)}</p>` : ""}${d.saving ? '<p role="status">Saving this object…</p>' : ""}<div class="vm-actions"><button class="btn btn-secondary" type="button" data-choose-frame="${esc(frame.index)}" data-detection="${esc(d.index ?? i)}" ${!reviewable(scan) || d.saving ? "disabled" : ""}>Choose identities / appearances</button><button class="btn btn-secondary" type="button" data-decision="reject" data-frame="${esc(frame.index)}" data-detection="${esc(d.index ?? i)}" ${!reviewable(scan) || d.saving ? "disabled" : ""}>Reject</button><button class="btn btn-secondary" type="button" data-decision="pending" data-frame="${esc(frame.index)}" data-detection="${esc(d.index ?? i)}" ${!reviewable(scan) || d.saving || d.conflict || !(d.links || []).length ? "disabled" : ""}>Save pending</button><button class="btn btn-primary" type="button" data-decision="approve" data-frame="${esc(frame.index)}" data-detection="${esc(d.index ?? i)}" ${!reviewable(scan) || d.saving || d.conflict || !(d.links || []).length ? "disabled" : ""}>Approve</button></div></div>`;
              })
              .join("") ||
            '<p class="vm-meta">No objects detected in this sample.</p>'
          }</div></article>`,
      );
      const container = $("#vm-video-frames");
      const visible = new Set(
        (scan.frames || []).map((frame) => String(frame.index)),
      );
      for (const index of renderedFrames.keys()) {
        if (!visible.has(index)) {
          container.querySelector(`[data-frame-index="${index}"]`)?.remove();
          renderedFrames.delete(index);
        }
      }
      if (!renderedFrames.size) container.replaceChildren();
      (scan.frames || []).forEach((frame, i) => {
        const index = String(frame.index),
          html = frameHTML[i];
        if (renderedFrames.get(index) === html) return;
        const existing = container.querySelector(
          `[data-frame-index="${index}"]`,
        );
        if (existing) existing.outerHTML = html;
        else container.insertAdjacentHTML("beforeend", html);
        renderedFrames.set(index, html);
      });
      if (!visible.size && completed(scan))
        container.innerHTML = '<p class="vm-empty">No frames were sampled.</p>';
      for (const canvas of root.querySelectorAll("[data-frame-crop]")) {
        const frame = scan.frames.find(
          (f) => String(f.index) === canvas.dataset.frameCrop,
        );
        const bbox = frame?.detections?.[+canvas.dataset.cropIndex]?.bbox;
        if (
          !Array.isArray(bbox) ||
          bbox.length !== 4 ||
          !bbox.every(Number.isFinite) ||
          bbox[2] <= bbox[0] ||
          bbox[3] <= bbox[1]
        )
          continue;
        if (canvas.dataset.loaded) continue;
        canvas.dataset.loaded = "true";
        const image = new Image();
        image.onload = () => {
          if (!canvas.isConnected) return;
          const scale = Math.min(
            1,
            180 / (bbox[2] - bbox[0]),
            120 / (bbox[3] - bbox[1]),
          );
          canvas.width = Math.max(1, Math.round((bbox[2] - bbox[0]) * scale));
          canvas.height = Math.max(1, Math.round((bbox[3] - bbox[1]) * scale));
          canvas
            .getContext("2d")
            .drawImage(
              image,
              bbox[0],
              bbox[1],
              bbox[2] - bbox[0],
              bbox[3] - bbox[1],
              0,
              0,
              canvas.width,
              canvas.height,
            );
        };
        image.src = safeURL(frame.image_url);
      }
      controls();
    }
    function scheduleRefresh(id, generation, delay = 3000) {
      clearTimeout(state.timer);
      state.timer = setTimeout(async () => {
        try {
          await refresh(id, generation);
        } catch (e) {
          if (generation === state.generation) {
            status("Could not refresh scan: " + e.message + ". Retrying…");
            scheduleRefresh(id, generation, 5000);
          }
        }
      }, delay);
    }
    async function refresh(id, generation = state.generation) {
      const sequence = ++state.requestSequence;
      const params = new URLSearchParams({
        frame_offset: String(state.frameOffset),
        frame_limit: String(state.frameLimit),
      });
      const scan = await api(
        "/videos/scans/" + encodeURIComponent(id) + "?" + params,
      );
      if (generation !== state.generation || sequence !== state.requestSequence)
        return;
      const currentHash = state.source?.source_sha256 || state.source?.sha256;
      const scanHash =
        scan.source?.source_sha256 || scan.source?.sha256 || scan.source_sha256;
      if (currentHash && scanHash && currentHash !== scanHash) {
        state.scan = null;
        status(
          "The published source has changed since this scan. Start a new scan for the current source.",
        );
        controls();
        return;
      }
      render(scan);
      clearTimeout(state.timer);
      if (active(scan)) scheduleRefresh(id, generation);
    }
    function editFrame(frame) {
      const scan = state.scan,
        generation = state.generation;
      if (!scan || !reviewable(scan)) return;
      const boxes = (frame.detections || [])
        .map((d, i) => ({
          bbox: [...(d.bbox || [])],
          original: [...(d.bbox || [])],
          index: d.index ?? i,
          readonly:
            !!d.reference_id ||
            (!!d.decision && d.decision !== "unresolved") ||
            ["approved", "saved"].includes(d.status),
        }))
        .filter((b) =>
          visualMemoryBbox.valid(b.bbox, frame.width, frame.height),
        );
      const removed = [];
      const originalBoxes = JSON.stringify(boxes);
      const dirtyBoxes = () =>
        removed.length > 0 || JSON.stringify(boxes) !== originalBoxes;
      editorDirty = dirtyBoxes;
      dialog(
        "Edit frame boxes",
        `<p>Detector boxes are proposals. Reject non-character false positives and add missed boxes manually. Drag on the image to add a box; drag a lower-right handle to resize. Saved or approved crops are read-only: add a new box to correct them.</p><div class="vm-stage"><img id="vm-frame-edit-image" src="${esc(safeURL(frame.image_url))}" alt="Frame at ${esc(time(frame.time))}"><div id="vm-frame-edit-overlay" class="vm-annotation-overlay"></div></div><div id="vm-frame-edit-boxes"></div>`,
        async () => {
          if (
            generation !== state.generation ||
            state.scan?.id !== scan.id ||
            !reviewable(state.scan)
          )
            throw new Error(
              "The scan changed. Reopen the frame before saving.",
            );
          try {
            for (const b of removed)
              await api(
                `/videos/scans/${encodeURIComponent(scan.id)}/observations/${frame.index}/${b.index}/resolve`,
                { decision: "reject", links: [] },
              );
            for (const b of boxes) {
              if (b.readonly) continue;
              if (b.index == null)
                await api(
                  `/videos/scans/${encodeURIComponent(scan.id)}/frames/${frame.index}/observations`,
                  { bbox: b.bbox },
                );
              else if (b.bbox.some((value, i) => value !== b.original[i]))
                await api(
                  `/videos/scans/${encodeURIComponent(scan.id)}/observations/${frame.index}/${b.index}/bbox`,
                  { bbox: b.bbox },
                  "PATCH",
                );
            }
            await refresh(scan.id, generation);
            editorDirty = () => false;
          } catch (error) {
            let reloaded = true;
            try {
              await refresh(scan.id, generation);
            } catch {
              reloaded = false;
              state.scan = null;
              $("#vm-video-frames").replaceChildren();
              controls();
            }
            closeDialog();
            throw new Error(
              `Could not save all boxes: ${error.message}. ${reloaded ? "Reopen the frame to review saved changes." : "Reload the source to review saved changes."}`,
            );
          }
        },
        "Save",
        {
          isDirty: dirtyBoxes,
          onClose: () => {
            editorDirty = () => false;
          },
        },
      );
      const body = document.querySelector("#vm-dialog-body"),
        overlay = body.querySelector("#vm-frame-edit-overlay"),
        image = body.querySelector("#vm-frame-edit-image"),
        list = body.querySelector("#vm-frame-edit-boxes");
      function renderBoxes() {
        overlay.innerHTML = visualMemoryBbox.bounds(
          boxes,
          frame.width,
          frame.height,
        );
        list.innerHTML = boxes
          .map(
            (b, i) =>
              `<div class="vm-box"><strong>Object ${i + 1}${b.readonly ? " · read-only" : ""}</strong>${b.readonly ? "" : `<button type="button" class="btn btn-secondary" data-remove-box="${i}">Delete box</button><div class="vm-coordinates">${["Left x", "Top y", "Right x", "Bottom y"].map((label, n) => `<label>${label}<input class="form-control" type="number" min="0" step="1" data-box-coordinate="${n}" data-box-index="${i}" value="${b.bbox[n]}"></label>`).join("")}</div>`}</div>`,
          )
          .join("");
      }
      visualMemoryBbox.bind({
        overlay,
        image,
        dimensions: () => ({ width: frame.width, height: frame.height }),
        boxes: () => boxes,
        disabled: () => state.busy || isBusy(),
        onAdd: (bbox) =>
          boxes.push({ bbox, original: null, index: null, readonly: false }),
        onResize: (i, bbox) => {
          boxes[i].bbox = bbox;
        },
        render: renderBoxes,
      });
      list.addEventListener("click", (e) => {
        if (state.busy || isBusy()) return;
        const button = e.target.closest("[data-remove-box]");
        if (!button) return;
        const [box] = boxes.splice(+button.dataset.removeBox, 1);
        if (box.index != null) removed.push(box);
        renderBoxes();
      });
      list.addEventListener("change", (e) => {
        const input = e.target.closest("[data-box-coordinate]");
        if (!input) return;
        const box = boxes[+input.dataset.boxIndex],
          next = box.bbox.slice();
        if (state.busy || isBusy()) {
          input.value = box.bbox[+input.dataset.boxCoordinate];
          return;
        }
        next[+input.dataset.boxCoordinate] = Number(input.value);
        if (
          !input.value ||
          !visualMemoryBbox.valid(next, frame.width, frame.height)
        ) {
          input.value = box.bbox[+input.dataset.boxCoordinate];
          message(
            "Box coordinates must fit the image and have positive width and height.",
            true,
          );
          return;
        }
        box.bbox = next;
        renderBoxes();
      });
      renderBoxes();
    }
    $("#vm-video-search").addEventListener("submit", (e) => {
      e.preventDefault();
      state.page = 0;
      catalog();
    });
    $("#vm-video-prev").addEventListener("click", () => {
      if (state.page) {
        state.page--;
        catalog();
      }
    });
    $("#vm-video-next").addEventListener("click", () => {
      state.page++;
      catalog();
    });
    $("#vm-video-catalog").addEventListener("click", (e) => {
      const button = e.target.closest("[data-video-item]");
      if (button)
        run(button, () => selectItem(state.catalog[+button.dataset.videoItem]));
    });
    $("#vm-video-variant").addEventListener("change", (e) =>
      run(e.currentTarget, selectSource),
    );
    const scanForm = $("#vm-video-scan");
    function samplingControls() {
      const mode = scanForm.elements.sampling_mode.value;
      $("#vm-sampling-interval").hidden = mode === "frame";
      scanForm.elements.interval_seconds.disabled = mode === "frame";
      for (const name of ["start", "end"])
        $("#vm-sampling-" + name).hidden = mode !== "segment";
      for (const name of ["timestamp", "frame-offset"])
        $("#vm-sampling-" + name).hidden = mode !== "frame";
      $("#vm-use-video-position").hidden = mode !== "frame";
    }
    scanForm.elements.sampling_mode.addEventListener(
      "change",
      samplingControls,
    );
    samplingControls();
    $("#vm-use-video-position").addEventListener("click", () => {
      if (!Number.isFinite(video.currentTime) || video.currentTime < 0) {
        message("The current video position is unavailable.", true);
        return;
      }
      scanForm.elements.timestamp_seconds.value = String(video.currentTime);
    });
    scanForm.addEventListener("submit", (e) => {
      e.preventDefault();
      const form = e.currentTarget;
      if ($("#vm-video-start").disabled) return;
      run($("#vm-video-start"), async () => {
        const source = state.source,
          generation = state.generation;
        const mode = form.elements.sampling_mode.value,
          sampling = { sampling_mode: mode };
        if (mode === "segment") {
          sampling.start_seconds = parseTime(form.elements.start_seconds.value);
          sampling.end_seconds = parseTime(form.elements.end_seconds.value);
          if (sampling.end_seconds <= sampling.start_seconds)
            throw new Error("End time must be after start time.");
        } else if (mode === "frame") {
          sampling.timestamp_seconds = parseTime(
            form.elements.timestamp_seconds.value,
          );
          const offset = form.elements.frame_offset.value.trim();
          if (!/^\d+$/.test(offset) || !Number.isSafeInteger(Number(offset)))
            throw new Error(
              "Frame offset must be a non-negative whole number.",
            );
          sampling.frame_offset = Number(offset);
        } else if (mode !== "full") throw new Error("Choose a sampling mode.");
        const previous = state.scan;
        const interval =
          mode === "frame" ? 3 : Number(form.elements.interval_seconds.value);
        const samePlan =
          (previous?.sampling_mode || "full") === mode &&
          (mode === "frame" ||
            (previous?.interval_seconds ?? 3) === interval) &&
          (mode !== "segment" ||
            (previous.start_seconds === sampling.start_seconds &&
              previous.end_seconds === sampling.end_seconds)) &&
          (mode !== "frame" ||
            (previous.timestamp_seconds === sampling.timestamp_seconds &&
              (previous.sampling_frame_offset ?? 0) === sampling.frame_offset));
        const scan = await api("/videos/scans", {
          asset_node_id: source.asset_node_id,
          asset_version: source.asset_version,
          game_version: source.game_version,
          cutscene_key: source.cutscene_key,
          source_reference_id:
            source.source_reference_id ?? source.file_reference_id,
          source_sha256: source.source_sha256,
          ...sampling,
          ...(mode === "frame"
            ? {}
            : {
                interval_seconds: interval,
              }),
          frame_batch_size: state.frameLimit,
          force:
            previous?.status === "failed" && !previous.reviewed && samePlan,
        });
        if (generation === state.generation) {
          const previousOffset = state.frameOffset;
          state.frameOffset = 0;
          if (scan.duplicate)
            message(
              "Opened cached scan: saved frames and decisions are reused.",
            );
          try {
            await refresh(scan.id, generation);
          } catch (error) {
            state.frameOffset = previousOffset;
            throw error;
          }
        }
      });
    });
    $("#vm-frame-prev").addEventListener("click", (e) =>
      run(e.currentTarget, async () => {
        const previous = state.frameOffset;
        state.frameOffset = Math.max(0, previous - state.frameLimit);
        try {
          await refresh(state.scan.id);
        } catch (e) {
          state.frameOffset = previous;
          throw e;
        }
      }),
    );
    $("#vm-frame-next").addEventListener("click", (e) =>
      run(e.currentTarget, async () => {
        const previous = state.frameOffset;
        state.frameOffset = state.scan.next_frame_offset;
        try {
          await refresh(state.scan.id);
        } catch (e) {
          state.frameOffset = previous;
          throw e;
        }
      }),
    );
    $("#vm-video-frames").addEventListener("click", (e) => {
      const button = e.target.closest("button");
      if (!button) return;
      if (button.dataset.seek !== undefined) {
        video.currentTime = Number(button.dataset.seek);
        video.focus();
        return;
      }
      if (button.dataset.editFrame !== undefined) {
        const frame = state.scan?.frames?.find(
          (f) => Number(f.index) === Number(button.dataset.editFrame),
        );
        if (frame && !state.busy) editFrame(frame);
        return;
      }
      const frameIndex = Number(
          button.dataset.chooseFrame ?? button.dataset.frame,
        ),
        detectionIndex = Number(button.dataset.detection);
      const detection = state.scan?.frames
        ?.find((f) => Number(f.index) === frameIndex)
        ?.detections?.find((d, i) => Number(d.index ?? i) === detectionIndex);
      if (!detection || !reviewable(state.scan)) return;
      const scan = state.scan,
        generation = state.generation;
      const key = observationKey(scan, frameIndex, detectionIndex);
      if (saving.has(key)) return;
      const displayed = displayedDetection(
        scan,
        frameIndex,
        detection,
        detectionIndex,
      );
      if (button.dataset.chooseFrame !== undefined) {
        const box = {
          links: displayed.links.map(({ identity_id, appearance_id }) => ({
            identity_id,
            appearance_id: appearance_id || null,
          })),
          names: {},
        };
        for (const link of displayed.links)
          box.names[link.identity_id] =
            link.name || link.identity_name || `Identity ${link.identity_id}`;
        chooseLinks(box, async (links) => {
          if (linkKey(links) === linkKey(detection.links)) drafts.delete(key);
          else
            drafts.set(key, {
              baseline: baseline(detection),
              revision: (drafts.get(key)?.revision || 0) + 1,
              links: links.map((link) => ({
                ...link,
                name: box.names[link.identity_id],
              })),
            });
          persistDrafts();
          if (generation === state.generation && state.scan?.id === scan.id)
            render(state.scan);
        });
      } else if (button.dataset.decision) {
        if (displayed.conflict && button.dataset.decision !== "reject") {
          message(
            "Object changed on the server. Choose its identity again before saving.",
            true,
          );
          return;
        }
        const revision = drafts.get(key)?.revision;
        saving.add(key);
        errors.delete(key);
        render(state.scan);
        (async () => {
          try {
            await api(
              `/videos/scans/${encodeURIComponent(scan.id)}/observations/${frameIndex}/${detectionIndex}/resolve`,
              {
                decision: button.dataset.decision,
                links:
                  button.dataset.decision === "reject"
                    ? []
                    : displayed.links.map(({ identity_id, appearance_id }) => ({
                        identity_id,
                        appearance_id,
                      })),
              },
            );
            if (drafts.get(key)?.revision === revision) {
              drafts.delete(key);
              persistDrafts();
            }
            if (generation === state.generation && state.scan?.id === scan.id)
              await refresh(scan.id, generation);
          } catch (error) {
            errors.set(
              key,
              error.message ||
                "Could not save. Your selection is retained; retry.",
            );
            message(errors.get(key), true);
          } finally {
            saving.delete(key);
            if (generation === state.generation && state.scan?.id === scan.id)
              render(state.scan);
          }
        })();
      }
    });
    $("#vm-video-reviewed").addEventListener("click", (e) =>
      run(e.currentTarget, async () => {
        const id = state.scan.id,
          generation = state.generation;
        await api(`/videos/scans/${encodeURIComponent(id)}/reviewed`, {});
        if (generation === state.generation) await refresh(id, generation);
      }),
    );
    $("#vm-video-reject-all").addEventListener("click", (e) =>
      run(e.currentTarget, async () => {
        const id = state.scan.id,
          generation = state.generation;
        await api(`/videos/scans/${encodeURIComponent(id)}/reject-all`, {});
        if (generation === state.generation) await refresh(id, generation);
      }),
    );
    return {
      open() {
        if (!state.opened) {
          state.opened = true;
          const q = new URL(location.href).searchParams.get("cutscene");
          if (q) $("#vm-video-search [name=q]").value = q;
          catalog();
        }
      },
    };
  };
})();
