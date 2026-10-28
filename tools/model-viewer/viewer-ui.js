// DOM-only adaptation of the maintained Select.tsx. Scene/camera state belongs to viewer.js.
let instance;

/** Idempotent: index.html initializes chrome; the scene imports this same instance.
 * Selection setters never invoke callbacks. Only user choices invoke onSelect.
 * Animation names and bone values must be unique, as in the scene's source lists.
 */
export function createViewerUI() {
  if (instance) return instance;
  const $ = (id) => document.getElementById(id);
  const dialog = $("viewer-settings");
  document.addEventListener("fullscreenchange", () => {
    const label = document.fullscreenElement ? "Exit fullscreen" : "Enter fullscreen";
    $("fullscreen").setAttribute("aria-label", label);
    $("fullscreen").title = label;
  });

  function picker({ triggerId, selectionId, listId, label, empty, inputId, searchable = false, rememberPath = false }) {
    const trigger = $(triggerId), selection = $(selectionId), input = inputId && $(inputId);
    const popup = document.createElement("div");
    popup.className = "picker-popup";
    popup.popover = "auto";
    trigger.setAttribute("aria-haspopup", "menu");
    const header = document.createElement("div");
    header.className = "picker-header";
    const backButton = document.createElement("button");
    backButton.type = "button";
    backButton.className = "picker-back";
    backButton.textContent = "‹ Back";
    const title = document.createElement("span");
    title.className = "picker-title";
    const searchButton = document.createElement("button");
    searchButton.type = "button";
    searchButton.className = "picker-search-toggle";
    searchButton.setAttribute("aria-label", `Search ${label.toLowerCase()}`);
    searchButton.innerHTML = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" aria-hidden="true"><circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 4 4"/></svg>';
    header.append(backButton, title, searchButton);
    popup.append(header);
    const searchRow = document.createElement("div");
    searchRow.className = "picker-search";
    searchRow.hidden = true;
    const search = document.createElement("input");
    search.type = "search";
    search.placeholder = `Search all ${label.toLowerCase()}…`;
    search.autocomplete = "off";
    search.setAttribute("aria-label", `Search all ${label.toLowerCase()}`);
    search.setAttribute("aria-controls", listId);
    const clear = document.createElement("button");
    clear.type = "button";
    clear.className = "search-clear";
    clear.textContent = "×";
    clear.setAttribute("aria-label", `Clear ${label.toLowerCase()} search`);
    clear.hidden = true;
    searchRow.append(search, clear);
    popup.append(searchRow);
    const list = document.createElement("div");
    list.id = listId;
    list.className = "picker-list";
    list.setAttribute("role", "menu");
    list.setAttribute("aria-label", label);
    list.tabIndex = -1;
    popup.append(list);
    // Stay beside the invoker for Tab order, including inside the settings modal.
    trigger.parentElement.append(popup);
    let options = [], entries = [], path = [], value = "", active = 0, callback;
    let searching = false, updating = false, composing = false, typeahead = "", typedAt = 0;
    const isOpen = () => popup.matches(":popover-open");
    const currentTitle = () => path.at(-1) || label;
    const optionPath = option => option.path || (option.group ? [option.group] : []);

    function close(restore = true) {
      if (!isOpen()) return;
      popup.hidePopover();
      trigger.setAttribute("aria-expanded", "false");
      if (restore) trigger.focus({ preventScroll: true });
    }
    function position() {
      if (!isOpen()) return;
      const rect = trigger.getBoundingClientRect();
      const height = window.visualViewport?.height ?? window.innerHeight;
      const widthLimit = window.visualViewport?.width ?? window.innerWidth;
      if (rect.bottom < 0 || rect.top > height || trigger.matches(":disabled")) return close(false);
      const below = height - rect.bottom - 12;
      const above = below < 220 && rect.top > below;
      const width = Math.min(Math.max(rect.width, searchable ? 300 : 240), widthLimit - 24);
      Object.assign(popup.style, {
        width: `${width}px`, left: `${Math.max(12, Math.min(rect.left, widthLimit - width - 12))}px`,
        top: `${above ? rect.top - 6 : rect.bottom + 6}px`,
        maxHeight: `${Math.max(44, Math.min(380, above ? rect.top - 18 : below))}px`,
        transform: above ? "translateY(-100%)" : "none",
      });
    }
    function highlight(focus = false) {
      const nodes = [...list.querySelectorAll("button")];
      nodes.forEach((node, index) => {
        node.tabIndex = index === active ? 0 : -1;
        node.classList.toggle("is-focused", index === active);
      });
      if (focus) (nodes[active] || list).focus({ preventScroll: true });
      if (isOpen()) nodes[active]?.scrollIntoView({ block: "nearest" });
    }
    function render(focus = false) {
      updating = true;
      const query = searching ? search.value.trim().toLocaleLowerCase() : "";
      const branches = new Map();
      entries = [];
      for (const option of options) {
        const levels = optionPath(option);
        if (searching) {
          if (`${option.label} ${option.value} ${levels.join(" ")}`.toLocaleLowerCase().includes(query)) entries.push({ option, label: option.label });
        } else if (path.every((part, index) => levels[index] === part)) {
          if (levels.length > path.length) {
            const name = levels[path.length];
            if (!branches.has(name)) {
              const branch = { label: name, branch: true, count: 0 };
              branches.set(name, branch);
              entries.push(branch);
            }
            branches.get(name).count++;
          } else entries.push({ option, label: option.label });
        }
      }
      backButton.hidden = !searching && !path.length;
      backButton.setAttribute("aria-label", searching ? `Back to ${currentTitle()}` : `Back to ${path.at(-2) || label}`);
      title.textContent = searching ? "Search results" : currentTitle();
      title.title = path.join(" / ") || label;
      searchButton.hidden = !searchable || searching;
      searchRow.hidden = !searching;
      clear.hidden = !search.value;
      active = Math.max(0, Math.min(active, entries.length - 1));
      list.replaceChildren();
      entries.forEach((entry, index) => {
        const node = document.createElement("button");
        node.type = "button";
        node.id = `${listId}-option-${index}`;
        node.className = `picker-option${entry.branch ? " picker-branch" : ""}`;
        node.setAttribute("role", entry.branch ? "menuitem" : "menuitemradio");
        if (!entry.branch) {
          node.setAttribute("aria-checked", String(entry.option.value === value));
          node.setAttribute("aria-label", entry.label);
        }
        const text = document.createElement("span");
        text.className = "picker-option-label";
        text.textContent = entry.label;
        node.append(text);
        if (entry.branch) {
          const detail = document.createElement("span");
          detail.className = "picker-branch-detail";
          detail.setAttribute("aria-hidden", "true");
          detail.textContent = `${entry.count}  ›`;
          node.append(detail);
          node.setAttribute("aria-label", `${entry.label}, ${entry.count} options`);
        } else if (searching && optionPath(entry.option).length) {
          const context = document.createElement("small");
          context.id = `${listId}-context-${index}`;
          context.textContent = optionPath(entry.option).join(" / ");
          node.setAttribute("aria-describedby", context.id);
          text.append(context);
        }
        node.addEventListener("focus", () => { active = index; highlight(); });
        node.addEventListener("pointermove", () => { active = index; highlight(); });
        node.addEventListener("click", () => choose(index));
        list.append(node);
      });
      if (!entries.length) {
        const message = document.createElement("div");
        message.className = "picker-empty";
        message.setAttribute("role", "status");
        message.textContent = options.length ? "No matches. Try another name or clear search." : empty;
        list.append(message);
      }
      highlight(focus);
      updating = false;
    }
    function select(next, followPath = false) {
      const option = options.find(item => item.value === String(next));
      value = option?.value ?? "";
      trigger.value = value;
      if (input) input.value = value;
      selection.textContent = option?.label ?? (options.length ? `Choose ${label.toLowerCase()}` : empty);
      if (rememberPath) trigger.title = option?.value || "";
      if (rememberPath && followPath && option) path = [...optionPath(option)];
      render(isOpen() && list.contains(document.activeElement));
    }
    function choose(index) {
      const entry = entries[index];
      if (!entry || trigger.matches(":disabled")) return;
      if (entry.branch) {
        path.push(entry.label); active = 0; render(true);
      } else {
        const next = entry.option.value;
        close();
        select(next, true);
        if (input) input.dispatchEvent(new Event("change", { bubbles: true }));
        callback?.(next);
      }
    }
    function back() {
      if (searching) {
        searching = false; search.value = ""; active = 0; render(true);
      } else if (path.length) {
        const previous = path.pop();
        active = 0; render();
        active = Math.max(0, entries.findIndex(entry => entry.branch && entry.label === previous));
        highlight(true);
      } else close();
    }
    function show() {
      if (trigger.matches(":disabled") || !options.length) return;
      if (!rememberPath) path = [];
      searching = false; search.value = ""; typeahead = ""; active = 0;
      popup.showPopover();
      trigger.setAttribute("aria-expanded", "true");
      render();
      active = Math.max(0, entries.findIndex(entry => entry.option?.value === value));
      highlight(true); position();
    }
    backButton.addEventListener("click", back);
    searchButton.addEventListener("click", () => {
      searching = true; active = 0; render(); search.focus({ preventScroll: true });
    });
    search.addEventListener("compositionstart", () => { composing = true; });
    search.addEventListener("compositionend", () => { composing = false; active = 0; render(); });
    search.addEventListener("input", event => { if (!composing && !event.isComposing) { active = 0; render(); } });
    clear.addEventListener("click", () => { search.value = ""; active = 0; search.focus(); render(); });
    trigger.addEventListener("click", () => isOpen() ? close() : show());
    trigger.addEventListener("keydown", event => {
      if (!event.isComposing && ["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) {
        event.preventDefault(); show();
        if (event.key === "End" || event.key === "ArrowUp") { active = entries.length - 1; highlight(true); }
      }
    });
    popup.addEventListener("keydown", event => {
      if (event.isComposing || composing || event.keyCode === 229) return;
      if (event.key === "Escape" || (event.target !== search && ["ArrowLeft", "Backspace"].includes(event.key))) {
        event.preventDefault(); event.stopPropagation(); back(); return;
      }
      if (event.target === backButton || event.target === searchButton || event.target === clear) return;
      if (["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) {
        // Home/End in the search field retain normal text editing behavior.
        if (event.target === search && ["Home", "End"].includes(event.key)) return;
        event.preventDefault();
        if (event.key === "Home") active = 0;
        else if (event.key === "End") active = entries.length - 1;
        else if (event.target === search) active = event.key === "ArrowDown" ? 0 : entries.length - 1;
        else active = (active + (event.key === "ArrowDown" ? 1 : -1) + entries.length) % (entries.length || 1);
        highlight(true);
      } else if (event.key === "Enter" || (event.key === " " && event.target !== search) || (event.key === "ArrowRight" && entries[active]?.branch && event.target !== search)) {
        event.preventDefault(); choose(active);
      } else if (event.target !== search && event.key.length === 1 && !event.ctrlKey && !event.metaKey && !event.altKey) {
        const now = Date.now();
        typeahead = (now - typedAt < 700 ? typeahead : "") + event.key;
        typedAt = now;
        const match = entries.findIndex(entry => entry.label.toLocaleLowerCase().startsWith(typeahead.toLocaleLowerCase()));
        if (match >= 0) { active = match; highlight(true); }
      }
    });
    popup.addEventListener("focusout", event => {
      if (!updating && !popup.contains(event.relatedTarget)) close(false);
    });
    popup.addEventListener("toggle", () => {
      const open = isOpen();
      trigger.setAttribute("aria-expanded", String(open));
      if (open) {
        window.addEventListener("resize", position);
        window.addEventListener("scroll", position, true);
        window.visualViewport?.addEventListener("resize", position);
      } else {
        window.removeEventListener("resize", position);
        window.removeEventListener("scroll", position, true);
        window.visualViewport?.removeEventListener("resize", position);
      }
    });
    input?.addEventListener("change", () => { if (input.value !== value) select(input.value); });
    return {
      select,
      set(items, onSelect, initial) {
        close(false);
        options = items.map(option => ({ ...option, value: String(option.value), label: String(option.label) }));
        callback = onSelect; path = []; searching = false; search.value = "";
        trigger.disabled = !options.length;
        select(initial ?? (options.some(option => option.value === value) ? value : options[0]?.value));
      },
    };
  }
  const animations = picker({
    triggerId: "animation-picker",
    selectionId: "animation-selection",
    listId: "animation-list",
    inputId: "animations",
    label: "Animations",
    empty: "No animations loaded",
    searchable: true,
    rememberPath: true,
  });
  const bones = picker({
    triggerId: "camera-bone",
    selectionId: "bone-selection",
    listId: "bone-list",
    label: "Bones",
    empty: "No bones available",
    searchable: true,
  });
  const speed = picker({
    triggerId: "speed-picker",
    selectionId: "speed-selection",
    listId: "speed-list",
    inputId: "speed",
    label: "Playback speed",
    empty: "Choose speed",
  });
  speed.set(
    ["0.25", "0.5", "0.75", "1", "1.5", "2"].map((value) => ({
      value,
      label: `${value}×`,
    })),
    undefined,
    $("speed").value,
  );
  const space = picker({
    triggerId: "space-picker",
    selectionId: "space-selection",
    listId: "space-list",
    inputId: "camera-space",
    label: "Follow space",
    empty: "Choose space",
  });
  space.set(
    [
      { value: "world", label: "World · translation" },
      { value: "local", label: "Local · full transform" },
    ],
    undefined,
    $("camera-space").value,
  );

  $("settings-open").addEventListener("click", () => dialog.showModal());
  $("settings-close").addEventListener("click", () => dialog.close());
  dialog.addEventListener("close", () =>
    $("settings-open").focus({ preventScroll: true }),
  );
  let backdropDown = false;
  dialog.addEventListener("pointerdown", (event) => {
    backdropDown = event.target === dialog && outside(event);
  });
  dialog.addEventListener("click", (event) => {
    if (backdropDown && event.target === dialog && outside(event))
      dialog.close();
    backdropDown = false;
  });
  function outside(event) {
    const rect = dialog.getBoundingClientRect();
    return (
      event.clientX < rect.left ||
      event.clientX > rect.right ||
      event.clientY < rect.top ||
      event.clientY > rect.bottom
    );
  }
  const tabs = [...dialog.querySelectorAll("[role=tab]")];
  function activate(tab) {
    tabs.forEach((item) => {
      const selected = item === tab;
      item.setAttribute("aria-selected", String(selected));
      item.tabIndex = selected ? 0 : -1;
      $(item.getAttribute("aria-controls")).hidden = !selected;
    });
  }
  tabs.forEach((tab, index) => {
    tab.addEventListener("click", () => activate(tab));
    tab.addEventListener("keydown", (event) => {
      if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key))
        return;
      event.preventDefault();
      const next =
        event.key === "Home"
          ? 0
          : event.key === "End"
            ? tabs.length - 1
            : (index + (event.key === "ArrowRight" ? 1 : -1) + tabs.length) %
              tabs.length;
      activate(tabs[next]);
      tabs[next].focus();
    });
  });

  let animationNames = [], animationCallback;
  function updateAnimationNavigation() {
    const index = animationNames.indexOf($("animations").value);
    $("animation-previous").disabled = index <= 0;
    $("animation-next").disabled = index < 0 || index >= animationNames.length - 1;
  }
  function selectAnimation(name) {
    animations.select(name, true);
    updateAnimationNavigation();
  }
  for (const [id, direction] of [["animation-previous", -1], ["animation-next", 1]]) {
    $(id).addEventListener("click", () => {
      const index = animationNames.indexOf($("animations").value);
      const name = index < 0 ? undefined : animationNames[index + direction];
      if (!name) return;
      selectAnimation(name);
      animationCallback?.(name);
    });
  }
  instance = {
    setAnimations(clips, onSelect, initialName) {
      animationNames = clips.map(clip => clip.name);
      animationCallback = onSelect;
      animations.set(
        clips.map((clip) => {
          const catalog = clip.userData?.catalog || {};
          return {
            value: clip.name,
            label: catalog.label || clip.name,
            path: [catalog.category || "Other animations", catalog.group, catalog.subgroup].filter(Boolean),
          };
        }),
        name => { updateAnimationNavigation(); onSelect?.(name); },
        initialName,
      );
      updateAnimationNavigation();
    },
    selectAnimation,
    setBones(options, onSelect) {
      bones.set(options.map(option => ({ ...option, group: option.group || "Other bones" })), onSelect);
      $("camera-focus").disabled = !options.length;
      $("camera-attach").disabled = !options.length;
    },
    selectBone: bones.select,
  };
  return instance;
}

