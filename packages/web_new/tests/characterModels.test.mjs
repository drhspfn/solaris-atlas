import assert from "node:assert/strict";
import { readFileSync, readdirSync } from "node:fs";
import { createRequire } from "node:module";
import { execFileSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import test from "node:test";
import vm from "node:vm";
import ts from "typescript";

const require = createRequire(import.meta.url);
const flush = () => new Promise((resolve) => setImmediate(resolve));
function nodes(tree) {
  if (!tree || typeof tree !== "object") return [];
  return [tree, ...[tree.props?.children].flat(Infinity).flatMap(nodes)];
}
function harness(importing = false, role = "admin") {
  let stateIndex, effectIndex, refIndex;
  let mountedKey;
  const states = [],
    effects = [],
    refs = [],
    requests = [],
    timers = new Map();
  let nextTimer = 0;
  const focused = { isConnected: true, focusCount: 0, focus() { this.focusCount++; } };
  const document = { visibilityState: "visible", body: { style: { overflow: "auto" } }, activeElement: focused };
  const listeners = new Set();
  const window = {
    location: { origin: "https://atlas.example" },
    addEventListener(type, listener) { if (type === "message") listeners.add(listener); },
    removeEventListener(type, listener) { if (type === "message") listeners.delete(listener); },
  };
  const observers = [];
  const elements = new Map();
  const children = new Map();
  let bucket;
  function element(path) {
    if (!elements.has(path)) elements.set(path, {
      open: false, isConnected: true, contentWindow: {},
      showModal() { this.open = true; }, close() { this.open = false; }, focus() {},
    });
    return elements.get(path);
  }
  const refresh = () => {};
  const mocks = {
    react: {
      useState(value) {
        const i = stateIndex++;
        const target = bucket?.states ?? states;
        if (!(i in target)) target[i] = value;
        return [
          target[i],
          (next) => {
            target[i] = typeof next === "function" ? next(target[i]) : next;
          },
        ];
      },
      useEffect(run, deps = []) {
        const target = bucket?.effects ?? effects;
        const i = effectIndex++,
          previous = target[i];
        if (!previous || deps.some((d, j) => d !== previous.deps[j])) {
          previous?.cleanup?.();
          target[i] = { deps, run };
        }
      },
      useId: () => "model",
      useCallback: (fn) => fn,
      useRef(value) {
        const i = refIndex++;
        const target = bucket?.refs ?? refs;
        return (target[i] ??= { current: value });
      },
    },
    "@/api/client": {
      ApiError: class extends Error {},
      api: (url, options) =>
        new Promise((resolve, reject) =>
          requests.push({ url, options, resolve, reject }),
        ),
      apiUrl: (url) => (/^https?:/.test(url) ? url : `/api${url}`),
    },
    "@/auth/AuthProvider": {
      useAuth: () => ({ user: role ? { role } : null, refresh }),
    },
    "@/components/ui/Select": { Select: () => null },
    "@/components/ui/ActionMenu": { ActionMenu: () => null },
    "lucide-react": new Proxy({}, { get: (_, name) => name }),
    "@/styles/character-model.css": {},
  };
  const file = importing ? "CharacterModelImport" : "CharacterModel";
  const code = ts.transpileModule(
    readFileSync(
      new URL(`../src/components/characters/${file}.tsx`, import.meta.url),
      "utf8",
    ),
    {
      compilerOptions: {
        target: ts.ScriptTarget.ES2022,
        module: ts.ModuleKind.CommonJS,
        jsx: ts.JsxEmit.ReactJSX,
      },
    },
  ).outputText;
  const exports = {};
  vm.runInNewContext(code, {
    exports,
    require: (id) => mocks[id] ?? require(id),
    IntersectionObserver: class {
      constructor(callback, options) { this.callback = callback; this.options = options; observers.push(this); }
      observe(target) { this.target = target; }
      disconnect() { this.disconnected = true; }
    },
    AbortController,
    URLSearchParams,
    document,
    window,
    URL,
    setTimeout(fn) {
      const id = ++nextTimer;
      timers.set(id, fn);
      return id;
    },
    clearTimeout(id) {
      timers.delete(id);
    },
  });
  const props = importing
    ? { characterKey: "character:1505", versions: [], active: true }
    : {
        characterKey: "character:1505",
        name: "The Shorekeeper & Rover",
        gameVersion: "3.7.0",
      };
  return {
    props,
    requests,
    timers,
    document,
    observers,
    focused,
    listeners,
    message(frame, state, options = {}) {
      for (const listener of listeners) listener({
        origin: window.location.origin,
        source: frame.props.ref.current.contentWindow,
        data: { type: "solaris-model-state", state },
        ...options,
      });
    },
    render() {
      bucket = null;
      stateIndex = effectIndex = refIndex = 0;
      let tree = exports[file](props);
      if (importing && tree) {
        if (mountedKey !== tree.key) {
          effects.forEach((effect) => effect.cleanup?.());
          states.length = effects.length = refs.length = 0;
          mountedKey = tree.key;
        }
        tree = tree.type(tree.props);
      }
      const used = new Set();
      function expand(node, path = "root") {
        if (!node || typeof node !== "object") return node;
        if (Array.isArray(node)) return node.map((n, i) => expand(n, `${path}.${i}`));
        if (typeof node.type === "function" && node.type.name === "ModelFrame") {
          const identity = `${path}:${node.key}`;
          used.add(identity);
          if (!children.has(identity)) children.set(identity, { states: [], effects: [], refs: [] });
          bucket = children.get(identity);
          stateIndex = effectIndex = refIndex = 0;
          const result = expand(node.type(node.props), identity);
          bucket = null;
          return result;
        }
        if (node.props?.ref) node.props.ref.current = element(path);
        if (node.props?.children) return { ...node, props: { ...node.props, children: expand(node.props.children, `${path}.children`) } };
        return node;
      }
      tree = expand(tree);
      for (const [key, child] of children) {
        if (!used.has(key)) {
          child.effects.forEach((effect) => effect.cleanup?.());
          children.delete(key);
        }
      }
      for (const effect of [...effects, ...[...children.values()].flatMap((child) => child.effects)]) {
        if (effect.run) {
          const run = effect.run;
          effect.run = null;
          effect.cleanup = run();
        }
      }
      return tree;
    },
    tick() {
      const [id, fn] = timers.entries().next().value;
      timers.delete(id);
      fn();
    },
    close() {
      effects.forEach((effect) => effect.cleanup?.());
      for (const child of children.values()) child.effects.forEach((effect) => effect.cleanup?.());
    },
  };
}
const model = {
  status: "ready",
  game_version: "3.7.0",
  preview: { url: "/api/media/preview.glb?token=a&b=c", size_bytes: 1024 },
  download: { url: "https://cdn.example/full.glb", size_bytes: 4096 },
  physics: { url: "/api/media/physics.json", size_bytes: 100 },
  animations: { url: "https://cdn.example/animations.glb", size_bytes: 8000 },
};

test("near-visible preview is deferred, lightweight and opens a separate fullscreen viewer", async () => {
  const h = harness();
  assert(!nodes(h.render()).some((n) => n.type === "iframe"));
  assert.equal(h.requests.length, 1);
  assert.equal(h.requests[0].url, "/characters/character%3A1505/model?game_version=3.7.0");
  h.requests[0].resolve(model);
  await flush();
  let tree = h.render();
  assert(!nodes(tree).some((n) => n.type === "iframe"));
  assert.equal(h.observers[0].options.rootMargin, "240px");
  h.observers[0].callback([{ isIntersecting: false }]);
  assert(!nodes(h.render()).some((n) => n.type === "iframe"));
  h.observers[0].callback([{ isIntersecting: true }]);
  tree = h.render();
  const compact = nodes(tree).find((n) => n.type === "iframe");
  const previewUrl = new URL(compact.props.src, "https://atlas.example");
  assert.equal(previewUrl.searchParams.get("mode"), "preview");
  assert.equal(previewUrl.searchParams.get("model"), model.preview.url);
  assert(!previewUrl.searchParams.has("animations"));
  assert(!previewUrl.searchParams.has("physics"));
  assert.equal(compact.props.tabIndex, -1);
  assert(h.observers[0].disconnected);
  assert.equal(compact.props.onLoad, undefined);
  h.message(compact, "ready");
  nodes(tree).find((n) => n.props["aria-haspopup"] === "dialog").props.onClick();
  tree = h.render();
  const frames = nodes(tree).filter((n) => n.type === "iframe");
  assert.equal(frames.length, 2);
  assert.equal(frames[0].props.src, compact.props.src);
  const fullUrl = new URL(frames[1].props.src, "https://atlas.example");
  assert(!fullUrl.searchParams.has("mode"));
  assert.equal(fullUrl.searchParams.get("physics"), model.physics.url);
  assert.equal(fullUrl.searchParams.get("animations"), model.animations.url);
  assert.equal(fullUrl.searchParams.get("animation_bytes"), String(model.animations.size_bytes));
  assert.equal(fullUrl.searchParams.get("name"), h.props.name);
  const modal = nodes(tree).find((n) => n.type === "dialog");
  assert(modal.props.ref.current.open);
  assert.equal(h.document.body.style.overflow, "hidden");
  modal.props.onCancel({ preventDefault() {} });
  assert.equal(nodes(h.render()).filter((n) => n.type === "iframe").length, 1);
  assert.equal(h.document.body.style.overflow, "auto");
  assert.equal(h.focused.focusCount, 1);
  assert.equal(h.requests.length, 1);
  h.close();
});

test("published viewer origins use the current pointer in both modes; invalid URLs fall back locally", async () => {
  for (const viewer_url of [
    "https://cdn.solarisatlas.fun/model-viewer/old-hash/index.html?v=pinned#old",
    "http://cdn.example:8080/viewer/index.html?v=pinned",
    "http://localhost:9000/wuwa/model-viewer/old/index.html",
    "javascript:alert(1)", "data:text/html,viewer", "file:///viewer/index.html",
    "/model-viewer/legacy/index.html", "invalid-url", null,
  ]) {
    const h = harness();
    h.render();
    h.requests[0].resolve({ ...model, viewer_url });
    await flush();
    h.render();
    h.observers[0].callback([{ isIntersecting: true }]);
    nodes(h.render()).find((n) => n.props["aria-haspopup"] === "dialog").props.onClick();
    for (const frame of nodes(h.render()).filter((n) => n.type === "iframe")) {
      const url = new URL(frame.props.src, "https://atlas.example");
      const published = /^https?:/.test(viewer_url ?? "");
      assert.equal(url.origin, published ? new URL(viewer_url).origin : "https://atlas.example");
      assert.equal(url.pathname, published ? new URL("../current/index.html", viewer_url).pathname : "/model-viewer/index.html");
      assert.equal(url.searchParams.get("model"), model.preview.url);
      assert.equal(url.searchParams.get("name"), h.props.name);
      assert.equal(url.searchParams.has("v"), false);
      assert.equal(url.hash, "");
      if (frame.props.title.startsWith("3D preview")) {
        assert.equal(url.searchParams.get("mode"), "preview");
        assert(!url.searchParams.has("animations"));
        assert(!url.searchParams.has("physics"));
      } else {
        assert(!url.searchParams.has("mode"));
        assert.equal(url.searchParams.get("physics"), model.physics.url);
        assert.equal(url.searchParams.get("animations"), model.animations.url);
      }
    }
    h.close();
  }
});

test("metadata failures can retry; stale metadata is ignored and missing models have no viewer", async () => {
  const h = harness();
  h.render();
  h.props.gameVersion = "3.8.0";
  h.render();
  assert(h.requests[0].options.signal.aborted);
  h.requests[0].resolve(model);
  h.requests[1].reject(new Error("Unavailable"));
  await flush();
  const tree = h.render();
  assert(nodes(tree).some((n) => n.props.role === "alert"));
  nodes(tree)
    .find((n) => n.type === "button")
    .props.onClick();
  h.render();
  h.requests[2].resolve({
    ...model,
    status: "missing",
    preview: null,
    download: null,
  });
  await flush();
  assert(!nodes(h.render()).some((n) => n.type === "iframe" || n.type === "a"));
  h.close();
});

test("legacy rig metadata has no animation download or viewer parameter", async () => {
  const h = harness();
  h.render();
  h.requests[0].resolve({ ...model, animations: null });
  await flush();
  let tree = h.render();
  assert.equal(nodes(tree).filter((node) => node.type === "a").length, 1);
  nodes(tree).find((node) => node.type === "button").props.onClick();
  tree = h.render();
  const frame = nodes(tree).find((node) => node.type === "iframe");
  assert.equal(new URL(frame.props.src, "https://atlas.example").searchParams.has("animations"), false);
  h.close();
});

test("administrators can explicitly skip the animation pack", () => {
  const h = harness(true);
  const controls = nodes(h.render());
  const animations = controls.find((node) => node.type === "input" && node.props.type === "checkbox");
  assert.equal(animations.props.checked, true);
  animations.props.onChange({ target: { checked: false } });
  nodes(h.render()).find((node) => node.type === "form").props.onSubmit({ preventDefault() {} });
  assert.equal(h.requests[0].options.body.include_animations, false);
  h.close();
});

test("character imports use the viewed patch and reset when the initial version changes", () => {
  const form = harness(true);
  for (const version of ["2.1", "2.2", undefined]) {
    form.props.initialGameVersion = version;
    const tree = form.render();
    assert.equal(
      nodes(tree).find(
        (node) => node.type === "input" && node.props.id === "model-version",
      ).props.value,
      version ?? "",
    );
    nodes(tree)
      .find((node) => node.type === "form")
      .props.onSubmit({ preventDefault() {} });
    const request = form.requests.at(-1);
    assert.equal(request.options.body.game_version, version);
    assert.equal(request.options.body.character_key, "character:1505");
  }
  assert.equal(form.requests.length, 3);
  assert(form.requests[0].options.signal.aborted);
  assert(form.requests[1].options.signal.aborted);
  form.close();
});

test("import controls are gated; duplicate submits are blocked and polling stops on close", async () => {
  for (const role of ["user", null]) {
    const guest = harness(true, role);
    assert.equal(guest.render(), null);
    assert.equal(guest.requests.length, 0);
  }
  const h = harness(true);
  let tree = h.render();
  assert.equal(h.requests.length, 0);
  tree.props.onToggle({ currentTarget: { open: true } });
  tree = h.render();
  const form = nodes(tree).find((n) => n.type === "form");
  form.props.onSubmit({ preventDefault() {} });
  form.props.onSubmit({ preventDefault() {} });
  assert.equal(h.requests.length, 1);
  assert.equal(
    JSON.stringify(h.requests[0].options.body),
    JSON.stringify({ character_key: "character:1505", force: false, include_animations: true }),
  );
  h.requests[0].resolve({ id: 7, status: "queued" });
  await flush();
  h.render();
  assert.equal(h.requests[1].url, "/admin/character-models/7");
  tree = h.render();
  tree.props.onToggle({ currentTarget: { open: false } });
  h.render();
  assert(h.requests[1].options.signal.aborted);
  assert.equal(h.timers.size, 0);
  h.close();
});

test("poll errors retry the same job; background polling pauses and completion refreshes once", async () => {
  const h = harness(true);
  let completed = 0;
  h.props.onComplete = () => completed++;
  h.render().props.onToggle({ currentTarget: { open: true } });
  nodes(h.render())
    .find((n) => n.type === "form")
    .props.onSubmit({ preventDefault() {} });
  h.requests[0].resolve({ id: 8, status: "queued" });
  await flush();
  h.render();
  h.requests[1].reject(new Error("Network unavailable"));
  await flush();
  nodes(h.render())
    .find((n) => n.type === "button" && n.props.children === "Check status")
    .props.onClick();
  h.render();
  assert.equal(h.requests[2].url, "/admin/character-models/8");
  h.requests[2].resolve({ id: 8, status: "running" });
  await flush();
  h.render();
  h.document.visibilityState = "hidden";
  h.tick();
  assert.equal(h.requests.length, 3);
  h.document.visibilityState = "visible";
  h.tick();
  assert.equal(h.requests.length, 4);
  h.requests[3].resolve({ id: 8, status: "completed" });
  await flush();
  h.render();
  assert.equal(completed, 1);
  assert.equal(h.timers.size, 0);
  h.close();
});

test("staged viewer contains only the explicit runtime allowlist and pinned Three.js core", () => {
  execFileSync(process.execPath, [
    fileURLToPath(
      new URL("../scripts/prepare-model-viewer.mjs", import.meta.url),
    ),
  ]);
  const staged = new URL("../public/model-viewer/", import.meta.url);
  const files = readdirSync(staged, { recursive: true }).filter((name) =>
    /\.[^/\\]+$/.test(name),
  );
  assert(files.includes("index.html"));
  assert(
    files.some(
      (name) => name.replaceAll("\\", "/") === "vendor/build/three.core.js",
    ),
  );
  assert(!files.some((name) => /\.(glb|gltf|png|jpg|webp|pak)$/i.test(name)));
  assert.equal(
    JSON.parse(
      readFileSync(
        new URL("../node_modules/three/package.json", import.meta.url),
      ),
    ).version,
    "0.183.2",
  );
});

test("pending imports stop monitoring after 200 checks and inactive panels cancel requests", async () => {
  const h = harness(true);
  h.render().props.onToggle({ currentTarget: { open: true } });
  nodes(h.render())
    .find((n) => n.type === "form")
    .props.onSubmit({ preventDefault() {} });
  h.requests[0].resolve({ id: 9, status: "queued" });
  await flush();
  h.render();
  for (let i = 1; i <= 200; i++) {
    h.requests[i].resolve({ id: 9, status: "running" });
    await flush();
    h.render();
    if (i < 200) h.tick();
  }
  assert.equal(h.timers.size, 0);
  nodes(h.render())
    .find((n) => n.type === "button" && n.props.children === "Check status")
    .props.onClick();
  h.render();
  h.props.active = false;
  h.render();
  assert(h.requests.at(-1).options.signal.aborted);
  assert.equal(h.timers.size, 0);
  h.close();
});

test("profile action modal restores focus, pauses monitoring on close and resumes the same job", async () => {
  const h = harness(true);
  h.props.variant = "actions";
  let tree = h.render();
  assert.equal(h.requests.length, 0);
  const trigger = nodes(tree).find((n) => n.props["aria-haspopup"] === "dialog");
  const summary = { isConnected: true, focusCount: 0, focus() { this.focusCount++; } };
  const menu = { open: true, querySelector: () => summary };
  trigger.props.onClick({ currentTarget: { closest: () => menu } });
  tree = h.render();
  assert.equal(menu.open, false);
  assert.equal(h.document.body.style.overflow, "hidden");
  const modal = nodes(tree).find((n) => n.type === "dialog");
  assert(modal.props.ref.current.open);
  assert.equal(modal.props["aria-labelledby"], "model-title");
  nodes(tree).find((n) => n.type === "form").props.onSubmit({ preventDefault() {} });
  h.requests[0].resolve({ id: 77, status: "queued" });
  await flush();
  tree = h.render();
  assert.equal(nodes(tree).find((n) => n.type === "a").props.href, "/admin/data-operations");
  assert.equal(h.requests[1].url, "/admin/character-models/77");
  modal.props.onCancel({ preventDefault() {} });
  h.render();
  assert(h.requests[1].options.signal.aborted);
  assert.equal(h.document.body.style.overflow, "auto");
  assert.equal(summary.focusCount, 2);
  trigger.props.onClick({ currentTarget: { closest: () => menu } });
  h.render();
  assert.equal(h.requests[2].url, "/admin/character-models/77");
  assert.equal(h.requests.filter((r) => r.options.method === "POST").length, 1);
  h.close();
  assert.equal(h.document.body.style.overflow, "auto");
});

test("viewer load failure and timeout offer retries without requesting animations", async () => {
  const h = harness();
  h.render();
  h.requests[0].resolve(model);
  await flush();
  h.render();
  h.observers[0].callback([{ isIntersecting: true }]);
  let tree = h.render();
  nodes(tree).find((n) => n.type === "iframe").props.onError();
  tree = h.render();
  assert(nodes(tree).some((n) => n.props.role === "alert"));
  assert(!nodes(tree).some((n) => n.type === "iframe"));
  nodes(tree).find((n) => n.type === "button" && n.props.children === "Retry viewer").props.onClick();
  const retryFrame = nodes(h.render()).find((n) => n.type === "iframe");
  assert.equal(retryFrame.key, "1");
  assert(!new URL(retryFrame.props.src, "https://atlas.example").searchParams.has("animations"));
  h.tick();
  assert(nodes(h.render()).some((n) => n.props.role === "alert"));
  h.close();
});

test("refresh and patch changes reset the viewer and cancel superseded availability reads", async () => {
  const h = harness();
  h.render();
  h.requests[0].resolve(model);
  await flush();
  nodes(h.render()).find((n) => n.props["aria-haspopup"] === "dialog").props.onClick();
  h.render();
  h.props.refreshKey = 1;
  h.render();
  assert(!nodes(h.render()).some((n) => n.type === "iframe" || n.type === "dialog"));
  assert.equal(h.document.body.style.overflow, "auto");
  h.props.gameVersion = "3.8.0";
  h.render();
  assert(h.requests[1].options.signal.aborted);
  assert.equal(h.requests[2].url, "/characters/character%3A1505/model?game_version=3.8.0");
  h.close();
});

test("immediate import completion refreshes once and version reset closes the modal", async () => {
  const h = harness(true);
  h.props.variant = "actions";
  let complete = 0;
  h.props.onComplete = () => complete++;
  let tree = h.render();
  nodes(tree).find((n) => n.props["aria-haspopup"] === "dialog").props.onClick({ currentTarget: { closest: () => null, focus() {}, isConnected: true } });
  tree = h.render();
  nodes(tree).find((n) => n.type === "form").props.onSubmit({ preventDefault() {} });
  h.requests[0].resolve({ id: 88, status: "completed" });
  await flush();
  h.render();
  h.render();
  assert.equal(complete, 1);
  assert.equal(h.requests.length, 1);
  h.props.initialGameVersion = "3.8.0";
  tree = h.render();
  assert.equal(h.document.body.style.overflow, "auto");
  assert.equal(nodes(tree).find((n) => n.type === "input" && n.props.id === "model-version").props.value, "3.8.0");
  assert(!nodes(tree).some((n) => n.type === "a"));
  h.close();
});

test("model state messages require the current iframe and same origin; GLB errors remain retryable", async () => {
  const h = harness();
  h.render();
  h.requests[0].resolve(model);
  await flush();
  h.render();
  h.observers[0].callback([{ isIntersecting: true }]);
  let tree = h.render();
  const frame = nodes(tree).find((n) => n.type === "iframe");
  assert(nodes(tree).some((n) => n.props.role === "status"));
  h.message(frame, "ready", { origin: "https://other.example" });
  h.message(frame, "ready", { source: {} });
  h.message(frame, "ready", { data: { type: "unknown", state: "ready" } });
  h.message(frame, "unknown");
  assert(nodes(h.render()).some((n) => n.props.role === "status"));
  h.message(frame, "ready");
  tree = h.render();
  assert(!nodes(tree).some((n) => n.props.role === "status"));
  assert.equal(h.timers.size, 0);
  h.message(frame, "error", { data: { type: "solaris-model-state", state: "error", message: "GLB download failed. Retry." } });
  tree = h.render();
  assert(nodes(tree).some((n) => n.props.role === "alert"));
  assert(nodes(tree).some((n) => n.props.children === "GLB download failed. Retry."));
  nodes(tree).find((n) => n.type === "button" && n.props.children === "Retry viewer").props.onClick();
  tree = h.render();
  assert(nodes(tree).some((n) => n.props.role === "status"));
  assert(!nodes(tree).some((n) => n.props.role === "alert"));
  assert.equal(h.listeners.size, 1);
  h.close();
  assert.equal(h.listeners.size, 0);
});


test("CDN viewer messages trust only the runtime origin and the exact iframe", async () => {
  const h = harness();
  h.render();
  h.requests[0].resolve({ ...model, viewer_url: "https://cdn.solarisatlas.fun/model-viewer/old/index.html" });
  await flush();
  h.render();
  h.observers[0].callback([{ isIntersecting: true }]);
  let tree = h.render();
  const frame = nodes(tree).find((n) => n.type === "iframe");
  h.message(frame, "ready"); // The embedding page's origin is not the CDN origin.
  h.message(frame, "ready", { origin: "https://unrelated.example" });
  h.message(frame, "ready", { origin: "https://cdn.solarisatlas.fun", source: {} });
  assert(nodes(h.render()).some((n) => n.props.role === "status"));
  h.message(frame, "ready", { origin: "https://cdn.solarisatlas.fun" });
  tree = h.render();
  assert(!nodes(tree).some((n) => n.props.role === "status"));
  assert.equal(h.timers.size, 0);
  h.message(frame, "error", { origin: "https://cdn.solarisatlas.fun", data: {
    type: "solaris-model-state", state: "error", message: "Model unavailable on CDN.",
  } });
  tree = h.render();
  assert(nodes(tree).some((n) => n.props.children === "Model unavailable on CDN."));
  assert(nodes(tree).some((n) => n.props.role === "alert"));
  h.close();
  assert.equal(h.listeners.size, 0);
});
