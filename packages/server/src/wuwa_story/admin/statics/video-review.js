(() => {
  "use strict";
  globalThis.createVisualMemoryVideoReview = ({
    root,
    api,
    escape: esc,
    message,
    chooseLinks,
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
    const status = (text) => {
      $("#vm-video-status").textContent = text;
    };
    const active = (scan) =>
      ["queued", "pending", "running", "processing"].includes(scan?.status);
    const completed = (scan) =>
      ["completed", "succeeded", "success"].includes(scan?.status);
    const time = (value) => {
      const n = Math.max(0, Math.floor(Number(value) || 0));
      return `${Math.floor(n / 60)}:${String(n % 60).padStart(2, "0")}`;
    };
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
      $("#vm-video-scan button").disabled =
        state.busy ||
        !state.source || !sourceKey(state.source) || !state.source.source_sha256 || !state.source.game_version || !state.source.asset_version || !state.source.cutscene_key ||
        active(scan) ||
        !!scan?.reviewed;
      $("#vm-video-scan button").textContent = scan?.status === "failed" ? "Retry scan" : "Start scan";
      $("#vm-video-reviewed").disabled =
        state.busy || !completed(scan) || !!scan.reviewed || pending !== 0;
      $("#vm-video-reject-all").disabled =
        state.busy || !completed(scan) || !(pending > 0) || !!scan.reviewed;
      $("#vm-video-variant").disabled = state.busy || !state.sources.length;
      $("#vm-frame-prev").disabled = state.busy || !scan || state.frameOffset === 0;
      $("#vm-frame-next").disabled = state.busy || !scan || scan.next_frame_offset == null;
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
      if (sourceKey(source) && source.game_version && source.asset_version && source.cutscene_key)
        video.src = `/admin/visual-memory/videos/sources/${encodeURIComponent(sourceKey(source))}/video?${new URLSearchParams({ game_version: source.game_version, asset_version: source.asset_version, cutscene_key: source.cutscene_key })}`;
      else {
        status("This source is missing its cutscene or version context. Reload the source list before scanning.");
        controls();
        return;
      }
      if (!source.source_sha256) {
        status("This source has no recorded hash. Reload the source list before scanning.");
        controls();
        return;
      }
      status("Checking previous scans…");
      controls();
      if (source.scan?.id) await refresh(source.scan.id, generation);
      else {
        status("This published source has not been reviewed.");
        controls();
      }
    }
    function render(scan) {
      state.scan = scan;
      const frameCount = scan.frame_count ?? 0;
      $("#vm-frame-page").textContent = `${frameCount ? state.frameOffset + 1 : 0}–${Math.min(state.frameOffset + (scan.frames || []).length, frameCount)} of ${frameCount} frames · ${scan.unresolved_count ?? "Unknown"} unresolved`;
      const progress = scan.progress
        ? ` · ${typeof scan.progress === "string" ? scan.progress : scan.progress.total == null ? `${scan.progress.completed ?? 0} frames sampled` : `${scan.progress.completed ?? 0} / ${scan.progress.total} frames`}`
        : "";
      status(
        `${scan.reviewed ? "Reviewed source" : "Scan " + (scan.status || "unknown")}${progress}${scan.error ? " · " + scan.error : ""} · SHA-256 ${scan.source?.source_sha256 || scan.source?.sha256 || "not available"}`,
      );
      $("#vm-video-frames").innerHTML =
        (scan.frames || [])
          .map(
            (frame) =>
              `<article class="vm-box vm-video-frame"><div><button type="button" class="vm-video-thumb" data-seek="${esc(frame.time)}"><img src="${esc(safeURL(frame.image_url))}" alt="Sample at ${esc(time(frame.time))}" loading="lazy"><span>Seek to ${esc(time(frame.time))}</span></button></div><div><h3>Sample at ${esc(time(frame.time))}</h3>${(frame.detections || []).map((d, i) => `<div class="vm-candidate"><strong>Object ${i + 1} · ${esc(d.decision || "unresolved")}</strong><canvas class="vm-video-crop" data-frame-crop="${esc(frame.index)}" data-crop-index="${i}" aria-label="Crop for object ${i + 1}"></canvas><p class="vm-meta">${esc(d.status || "Unknown")} · ${esc((d.candidates || []).map((c) => c.name || "Unnamed").join(", ") || "No candidate evidence")}</p><p>Selected: ${esc((d.links || []).map((l) => [l.name || l.identity_name || `Identity ${l.identity_id}`, l.appearance_name || (l.appearance_id ? `appearance ${l.appearance_id}` : "")].filter(Boolean).join(" · ")).join(", ") || "None — unresolved")}</p><div class="vm-actions"><button class="btn btn-secondary" type="button" data-choose-frame="${esc(frame.index)}" data-detection="${esc(d.index ?? i)}" ${scan.reviewed || !completed(scan) ? "disabled" : ""}>Choose identities / appearances</button><button class="btn btn-secondary" type="button" data-decision="reject" data-frame="${esc(frame.index)}" data-detection="${esc(d.index ?? i)}" ${scan.reviewed || !completed(scan) ? "disabled" : ""}>Reject</button><button class="btn btn-secondary" type="button" data-decision="pending" data-frame="${esc(frame.index)}" data-detection="${esc(d.index ?? i)}" ${scan.reviewed || !completed(scan) || !(d.links || []).length ? "disabled" : ""}>Save pending</button><button class="btn btn-primary" type="button" data-decision="approve" data-frame="${esc(frame.index)}" data-detection="${esc(d.index ?? i)}" ${scan.reviewed || !completed(scan) || !(d.links || []).length ? "disabled" : ""}>Approve</button></div></div>`).join("") || '<p class="vm-meta">No objects detected in this sample.</p>'}</div></article>`,
          )
          .join("") ||
        (completed(scan)
          ? '<p class="vm-empty">No frames were sampled.</p>'
          : "");
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
        const image = new Image();
        image.onload = () => {
          if (state.scan !== scan) return;
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
      const params = new URLSearchParams({frame_offset: String(state.frameOffset), frame_limit: String(state.frameLimit)});
      const scan = await api("/videos/scans/" + encodeURIComponent(id) + "?" + params);
      if (generation !== state.generation || sequence !== state.requestSequence) return;
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
    $("#vm-video-scan").addEventListener("submit", (e) => {
      e.preventDefault();
      const form = e.currentTarget;
      if (form.querySelector("button").disabled) return;
      run(form.querySelector("button"), async () => {
        const source = state.source,
          generation = state.generation;
        const scan = await api("/videos/scans", {
          asset_node_id: source.asset_node_id,
          asset_version: source.asset_version,
          game_version: source.game_version,
          cutscene_key: source.cutscene_key,
          source_reference_id:
            source.source_reference_id ?? source.file_reference_id,
          source_sha256: source.source_sha256,
          interval_seconds: Number(form.elements.interval_seconds.value),
          frame_batch_size: state.frameLimit,
          force: state.scan?.status === "failed",
        });
        if (generation === state.generation) await refresh(scan.id, generation);
      });
    });
    $("#vm-frame-prev").addEventListener("click", e => run(e.currentTarget, async () => {
      const previous = state.frameOffset;
      state.frameOffset = Math.max(0, previous - state.frameLimit);
      try { await refresh(state.scan.id); } catch (e) { state.frameOffset = previous; throw e; }
    }));
    $("#vm-frame-next").addEventListener("click", e => run(e.currentTarget, async () => {
      const previous = state.frameOffset;
      state.frameOffset = state.scan.next_frame_offset;
      try { await refresh(state.scan.id); } catch (e) { state.frameOffset = previous; throw e; }
    }));
    $("#vm-video-frames").addEventListener("click", (e) => {
      const button = e.target.closest("button");
      if (!button) return;
      if (button.dataset.seek !== undefined) {
        video.currentTime = Number(button.dataset.seek);
        video.focus();
        return;
      }
      const frameIndex = Number(
          button.dataset.chooseFrame ?? button.dataset.frame,
        ),
        detectionIndex = Number(button.dataset.detection);
      const detection = state.scan?.frames
        ?.find((f) => Number(f.index) === frameIndex)
        ?.detections?.find((d, i) => Number(d.index ?? i) === detectionIndex);
      if (!detection) return;
      if (button.dataset.chooseFrame !== undefined) {
        const box = {
          links: (detection.links || []).map((l) => ({
            identity_id: l.identity_id,
            appearance_id: l.appearance_id || null,
          })),
          names: {},
        };
        for (const link of detection.links || [])
          box.names[link.identity_id] =
            link.name || link.identity_name || `Identity ${link.identity_id}`;
        chooseLinks(box, async (links) => {
          detection.links = links.map((l) => ({
            ...l,
            name: box.names[l.identity_id],
          }));
          render(state.scan);
        });
      } else if (button.dataset.decision)
        run(button, async () => {
          const generation = state.generation,
            id = state.scan.id;
          await api(
            `/videos/scans/${encodeURIComponent(id)}/observations/${frameIndex}/${detectionIndex}/resolve`,
            {
              decision: button.dataset.decision,
              links:
                button.dataset.decision === "reject"
                  ? []
                  : detection.links.map(({ identity_id, appearance_id }) => ({
                      identity_id,
                      appearance_id,
                    })),
            },
          );
          if (generation === state.generation) await refresh(id, generation);
        });
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
