(() => {
  const root = document.getElementById("cutscene-player");
  if (!root) return;
  const form = root.querySelector("form");
  const status = root.querySelector(".sa-cutscene-status");
  const stage = root.querySelector(".sa-cutscene-stage");
  const video = stage.querySelector("video");
  const caption = stage.querySelector(".sa-cutscene-caption");
  const choice = stage.querySelector(".sa-cutscene-choice");
  const controls = root.querySelector(".sa-cutscene-controls");
  const context = root.querySelector(".sa-cutscene-context");
  const timelineInput = root.querySelector("[name=timeline]");
  const decisions = root.querySelector(".sa-cutscene-decisions");
  const subtitles = form.elements.language;
  let flow,
    captions,
    node,
    source,
    tracks = [],
    request,
    selectedRover = null;
  let selections = {},
    durations = {},
    timeline = { clips: [], decisions: [], total: 0, complete: false };

  function stopTracks() {
    tracks.forEach(({ audio }) => {
      audio.pause();
      audio.removeAttribute("src");
      audio.load();
    });
    tracks = [];
  }
  function updateVolumes() {
    tracks.forEach(({ audio, role }) => {
      audio.volume = video.muted
        ? 0
        : video.volume *
          (role === "music"
            ? Number(root.querySelector("[name=music]").value)
            : 1);
    });
  }
  function alignTracks(force = false) {
    const target = video.currentTime + (source?.timeline_offset || 0);
    tracks.forEach(({ audio }) => {
      if (
        audio.readyState >= 1 &&
        (force || Math.abs(audio.currentTime - target) > 0.15)
      ) {
        audio.currentTime = Math.max(
          0,
          Math.min(
            target,
            Number.isFinite(audio.duration) ? audio.duration : target,
          ),
        );
      }
      audio.playbackRate = video.playbackRate;
    });
  }
  function playTracks() {
    if (video.paused || video.seeking) return;
    alignTracks();
    tracks.forEach(({ audio }) =>
      audio.play().catch((error) => {
        if (error.name !== "AbortError") {
          video.pause();
          status.textContent =
            "Audio could not play. Try restarting the scene.";
        }
      }),
    );
  }
  function pauseTracks() {
    tracks.forEach(({ audio }) => audio.pause());
  }
  function updateCaption() {
    const time = video.currentTime + (source?.timeline_offset || 0);
    const cues = captions?.[subtitles.value] || [];
    caption.textContent = root.querySelector("[name=captions]").checked
      ? cues
          .filter((cue) => cue.start <= time && time < cue.end)
          .map((cue) => cue.text)
          .join("\n")
      : "";
  }
  function updateTimeline() {
    if (!flow) return;
    const clips = [],
      points = [],
      visited = new Set();
    let id = flow.entry,
      total = 0,
      complete = true;
    while (id && !visited.has(id)) {
      visited.add(id);
      const entry = flow.nodes.find((item) => item.id === id);
      if (!entry) {
        complete = false;
        break;
      }
      if (entry.kind === "choice") {
        points.push({
          id: entry.id,
          time: total,
          unresolved: !selections[entry.id],
        });
        id = selections[entry.id]?.next || entry.options[0]?.next || null;
      } else {
        const key = entry.segment || entry.asset;
        const end =
          entry.end ?? durations[key] ?? flow.media[key]?.duration_seconds;
        if (!Number.isFinite(end) || end <= entry.start) {
          complete = false;
          break;
        }
        clips.push({
          id: entry.id,
          start: total,
          duration: end - entry.start,
          mediaStart: entry.start,
        });
        total += end - entry.start;
        id = entry.next;
      }
    }
    if (id) complete = false;
    timeline = { clips, decisions: points, total, complete };
    timelineInput.max = total || 1;
    timelineInput.disabled = !complete || !total;
    decisions.replaceChildren();
    points.forEach((point, index) => {
      const button = document.createElement("button");
      button.className = "btn btn-secondary";
      button.type = "button";
      button.textContent = `${point.unresolved ? "Choose" : "Change"} branch ${index + 1}`;
      button.addEventListener("click", () =>
        advance(point.id, false, null, null, true),
      );
      decisions.append(button);
    });
  }
  function advance(
    id,
    autoplay = false,
    rover = null,
    seek = null,
    forceChoice = false,
  ) {
    video.pause();
    stopTracks();
    node = flow.nodes.find((item) => item.id === id);
    caption.textContent = "";
    if (!node) {
      status.textContent = id
        ? "The next part is missing."
        : "End of cutscene.";
      return;
    }
    if (node.kind === "choice") {
      if (!forceChoice && selections[node.id]) {
        const option = selections[node.id];
        advance(option.next, autoplay, option.rover || rover, seek);
        return;
      }
      choice.hidden = false;
      choice.querySelector("p").textContent = node.prompt || "Choose a variant";
      const buttons = choice.querySelector("div");
      buttons.replaceChildren();
      node.options.forEach((option) => {
        const button = document.createElement("button");
        button.type = "button";
        button.className = "btn btn-secondary";
        button.textContent = option.label;
        button.addEventListener("click", () => {
          selections[node.id] = option;
          updateTimeline();
          advance(option.next, true, option.rover || rover);
        });
        buttons.append(button);
      });
      status.textContent = "Choose a variant to continue.";
      return;
    }
    choice.hidden = true;
    source = flow.media[node.segment || node.asset];
    if (!source?.url) {
      status.textContent = "Video for this part is unavailable.";
      return;
    }
    selectedRover = node.rover || rover || selectedRover;
    tracks = (source.audio_tracks || [])
      .filter(
        (track) =>
          (!track.language || track.language === subtitles.value) &&
          (!track.rover || track.rover === selectedRover),
      )
      .map((track) => {
        const audio = new Audio(track.url);
        audio.preload = "metadata";
        audio.addEventListener("loadedmetadata", () => alignTracks(true));
        audio.addEventListener("error", () => {
          video.pause();
          status.textContent = "An audio track could not load.";
        });
        return { audio, role: track.role };
      });
    updateVolumes();
    video.src = source.url;
    video.onloadedmetadata = () => {
      if (Number.isFinite(video.duration))
        durations[node.segment || node.asset] = video.duration;
      updateTimeline();
      video.currentTime = seek ?? node.start ?? 0;
      alignTracks(true);
      if (autoplay)
        video.play().catch(() => {
          status.textContent = "Press play to continue.";
        });
    };
    status.textContent = "";
  }
  video.addEventListener("play", playTracks);
  video.addEventListener("playing", playTracks);
  video.addEventListener("pause", pauseTracks);
  video.addEventListener("waiting", pauseTracks);
  video.addEventListener("seeking", () => {
    pauseTracks();
    alignTracks(true);
  });
  video.addEventListener("seeked", playTracks);
  video.addEventListener("ratechange", () => alignTracks());
  video.addEventListener("volumechange", updateVolumes);
  video.addEventListener("timeupdate", () => {
    alignTracks();
    updateCaption();
    const clip = timeline.clips.find((item) => item.id === node?.id);
    if (clip)
      timelineInput.value =
        clip.start + Math.max(0, video.currentTime - clip.mediaStart);
    if (
      node?.kind === "clip" &&
      node.end != null &&
      video.currentTime >= node.end
    )
      advance(node.next, !video.paused, node.rover);
  });
  video.addEventListener("ended", () => {
    if (node?.kind === "clip") advance(node.next, true, node.rover);
  });
  video.addEventListener("error", () => {
    status.textContent = "Video could not load.";
  });
  root.querySelector("[name=music]").addEventListener("input", updateVolumes);
  root
    .querySelector("[name=captions]")
    .addEventListener("change", updateCaption);
  timelineInput.addEventListener("input", () => {
    const time = Number(timelineInput.value);
    const unresolved = timeline.decisions.find(
      (item) => item.unresolved && time >= item.time,
    );
    if (unresolved) {
      advance(unresolved.id);
      return;
    }
    const clip =
      timeline.clips.find((item) => time < item.start + item.duration) ||
      timeline.clips.at(-1);
    if (clip)
      advance(
        clip.id,
        !video.paused,
        selectedRover,
        clip.mediaStart + Math.min(clip.duration, time - clip.start),
      );
  });
  root.querySelector("[data-action=restart]").addEventListener("click", () => {
    selectedRover = null;
    selections = {};
    if (flow) {
      updateTimeline();
      advance(flow.entry);
    }
  });
  root
    .querySelector("[data-action=fullscreen]")
    .addEventListener("click", () => {
      const action = document.fullscreenElement
        ? document.exitFullscreen()
        : stage.requestFullscreen();
      action.catch(() => {
        status.textContent = "Fullscreen is unavailable.";
      });
    });
  async function load() {
    request?.abort();
    request = new AbortController();
    const controller = request;
    video.pause();
    stopTracks();
    stage.hidden = controls.hidden = true;
    context.replaceChildren();
    status.textContent = "Loading cutscene…";
    const params = new URLSearchParams({ locale: subtitles.value });
    if (form.elements.version.value.trim())
      params.set("game_version", form.elements.version.value.trim());
    try {
      const response = await fetch(
        `${root.dataset.url}?${params}`,
        { signal: controller.signal },
      );
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const data = await response.json();
      if (controller.signal.aborted) return;
      flow = data.event?.playback;
      captions = data.event?.captions;
      if (!flow?.nodes?.length) {
        status.textContent =
          "No playable export is available for this version.";
        return;
      }
      stage.hidden = controls.hidden = false;
      status.textContent = `Snapshot ${data.game_version}`;
      for (const passage of data.passages || []) {
        const p = document.createElement("p");
        p.textContent = `${passage.name || `Quest ${passage.game_quest_id}`} · ${passage.flow_state || "related scene"}`;
        context.append(p);
      }
      selectedRover = null;
      selections = {};
      durations = {};
      updateTimeline();
      advance(flow.entry);
    } catch (error) {
      if (error.name !== "AbortError")
        status.textContent = `Could not load cutscene: ${error.message}`;
    }
  }
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    load();
  });
  subtitles.addEventListener("change", load);
  load();
})();
