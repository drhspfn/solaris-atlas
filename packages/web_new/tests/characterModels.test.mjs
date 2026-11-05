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
  let nextChildId = 0;
  const focused = { isConnected: true, focusCount: 0, focus() { this.focusCount++; } };
  const document = { visibilityState: "visible", body: { style: { overflow: "auto" } }, activeElement: focused };
  const listeners = new Set();
  const window = {
    innerWidth: 360, innerHeight: 720,
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
      style: {}, scrollTop: 0, clientHeight: 300,
      showPopover() {}, hidePopover() {}, querySelector() { return null; },
      getBoundingClientRect() { return { left: 12, top: 12, bottom: 56, width: 240 }; },
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
      useId: () => bucket ? `model-${bucket.id}` : "model",
      useCallback: (fn) => fn,
      useRef(value) {
        const i = refIndex++;
        const target = bucket?.refs ?? refs;
        return (target[i] ??= { current: value });
      },
    },
    "@/components/ui/Button": { Button: "button" },
    "@/components/models/ModelViewer": { ModelViewer: "NativeModelViewer" },
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
    crypto: { randomUUID: () => "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa" },
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
  const selectExports = {};
  vm.runInNewContext(ts.transpileModule(
    readFileSync(new URL("../src/components/ui/Select.tsx", import.meta.url), "utf8"),
    { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX } },
  ).outputText, {
    exports: selectExports, require: (id) => mocks[id] ?? require(id), window,
  });
  mocks["@/components/ui/Select"].Select = selectExports.Select;
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
        if (typeof node.type === "function" && ["ModelFrame", "Select"].includes(node.type.name)) {
          const identity = `${path}:${node.key}`;
          used.add(identity);
          if (!children.has(identity)) children.set(identity, { id: nextChildId++, states: [], effects: [], refs: [] });
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
  facial: { url: "https://cdn.example/facial.json", size_bytes: 500 },
};
const forms = [
  { ...model, id: "default", label: "Default" },
  {
    id: "alternate", label: "Alternate · authored form",
    preview: { url: "/api/media/alternate-preview.glb", size_bytes: 2048 },
    download: { url: "/api/media/alternate-full.glb", size_bytes: 8192 },
    physics: { url: "/api/media/alternate-physics.json", size_bytes: 200 },
    animations: { url: "/api/media/alternate-animations.glb", size_bytes: 16000 },
    facial: { url: "/api/media/alternate-facial.json", size_bytes: 700 },
    viewer_url: "https://alternate.example/model-viewer/old/index.html",
  },
];
const formPickers = (tree) => nodes(tree).filter((n) => n.props.role === "listbox" && n.props["aria-label"] === "Model form");
function chooseForm(tree, label, picker = 0) {
  const option = nodes(formPickers(tree)[picker]).find((n) => n.props.role === "option" && nodes(n).some((child) => child.props.children === label));
  assert(option, `Missing form option: ${label}`);
  option.props.onClick();
}

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

test("an archive in the existing import form uploads bounded chunks then queues publication", async () => {
  const h = harness(true);
  const file = { size: 9, slice(start, end) { return { start, bytes: Math.min(end, 9) - start }; } };
  nodes(h.render()).find(node => node.type === "input" && node.props.type === "file")
    .props.onChange({ target: { files: [file] } });
  let tree = h.render();
  assert.equal(nodes(tree).find(node => node.type === "input" && node.props.type === "checkbox").props.disabled, true);
  nodes(tree).find(node => node.type === "form").props.onSubmit({ preventDefault() {} });
  assert.equal(h.requests[0].url, "/admin/character-models/uploads");
  h.requests[0].resolve({ id: 8, status: "uploading", chunk_bytes: 4 });
  await flush();
  for (let part = 0; part < 3; part++) {
    const request = h.requests[part + 1];
    assert.equal(request.url, `/admin/character-models/uploads/8/parts/${part}`);
    assert.equal(request.options.body.bytes, part === 2 ? 1 : 4);
    request.resolve({ id: 8 }); await flush();
  }
  assert.equal(h.requests[4].url, "/admin/character-models/uploads/8/complete");
  h.requests[4].resolve({ id: 8, status: "completed" });
  await flush(); tree = h.render();
  assert(nodes(tree).some(node => node.props.role === "status"));
  assert(!h.requests.some(request => request.url === "/admin/character-models"));
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



test("native model uses manifest props, defers the canvas and ignores legacy runtime URLs", async () => {
  const h = harness(); h.render();
  h.requests[0].resolve({ ...model, viewer_url: "https://evil.example/viewer.js", forms });
  await flush(); let tree = h.render();
  let viewers = nodes(tree).filter(node => node.type === "NativeModelViewer");
  assert.equal(viewers.length, 2);
  assert.equal(viewers[0].props.preview, true);
  assert.equal(viewers[0].props.active, false);
  assert.equal(viewers[0].props.assets.preview.url, forms[0].preview.url);
  assert.equal(nodes(tree).filter(node => node.type === "iframe").length, 0);
  h.observers.at(-1).callback([{ isIntersecting: true }]);
  tree = h.render();
  assert.equal(nodes(tree).find(node => node.type === "NativeModelViewer").props.active, true);
  nodes(tree).find(node => node.props["aria-haspopup"] === "dialog").props.onClick();
  tree = h.render();
  viewers = nodes(tree).filter(node => node.type === "NativeModelViewer");
  assert.equal(viewers.length, 1, "preview is released while full viewer is open");
  assert.equal(viewers[0].props.preview, undefined);
  chooseForm(tree, forms[1].label);
  tree = h.render();
  const next = nodes(tree).find(node => node.type === "NativeModelViewer");
  assert.equal(next.props.assets.preview.url, forms[1].preview.url);
  assert.notEqual(next.key, viewers[0].key);
  assert.equal(next.props.assets.physics, forms[1].physics);
  nodes(tree).find(node => node.type === "dialog").props.onCancel({ preventDefault() {} });
  h.render();
  assert.equal(h.document.body.style.overflow, "auto");
  assert.equal(h.focused.focusCount, 1);
  h.close();
});

test("server-provided model metadata renders immediately without a duplicate browser request", () => {
  const h = harness(); h.props.initialModel = model;
  const tree = h.render();
  assert.equal(h.requests.length, 0);
  assert.equal(nodes(tree).filter(node => node.type === "NativeModelViewer").length, 2);
  h.close();
});

test("model manifest retries and stale request cancellation remain available", async () => {
  const h = harness(); h.render();
  h.requests[0].reject(new Error("offline")); await flush();
  let tree = h.render();
  nodes(tree).find(node => node.type === "button" && node.props.children === "Retry model availability").props.onClick();
  h.render(); assert.equal(h.requests.length, 2);
  h.props.characterKey = "character:1211"; h.render();
  assert.equal(h.requests[1].options.signal.aborted, true);
  h.requests[1].resolve(model); await flush();
  h.requests[2].resolve({ ...model, status: "missing" }); await flush();
  tree = h.render();
  assert.equal(nodes(tree).filter(node => node.type === "NativeModelViewer").length, 0);
  h.close();
});


test("closing or cancelling nested settings does not close the model dialog", async () => {
  const h = harness(); h.render(); h.requests[0].resolve(model); await flush();
  let tree = h.render();
  nodes(tree).find(node => node.props["aria-haspopup"] === "dialog").props.onClick();
  tree = h.render(); const dialog = nodes(tree).find(node => node.type === "dialog");
  const target = {}, currentTarget = {};
  dialog.props.onClose({ target, currentTarget });
  dialog.props.onCancel({ target, currentTarget, preventDefault() { assert.fail("nested Escape must retain native dismissal"); } });
  tree = h.render();
  assert.equal(nodes(tree).find(node => node.type === "dialog").props.ref.current.open, true);
  dialog.props.onClose({ target: currentTarget, currentTarget }); h.render();
  assert.equal(h.document.body.style.overflow, "auto"); h.close();
});


test("profile model stage has one corner icon opener and no download links", () => {
  const h = harness(); h.props.initialModel = model;
  const tree = h.render();
  const opener = nodes(tree).find(node => node.props["aria-haspopup"] === "dialog");
  assert(opener);
  assert.equal(opener.props.size, "icon");
  assert(opener.props.icon, "opener has an icon supplied through the shared Button");
  assert.equal(opener.props.children, undefined, "no loose Explore model text in the stage");
  assert.equal(nodes(tree).filter(node => node.type === "a" && node.props.download).length, 0);
  h.close();
});
