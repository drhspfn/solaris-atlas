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
  const states = [],
    effects = [],
    refs = [],
    requests = [],
    timers = new Map();
  let nextTimer = 0;
  const document = { visibilityState: "visible" };
  const refresh = () => {};
  const mocks = {
    react: {
      useState(value) {
        const i = stateIndex++;
        if (!(i in states)) states[i] = value;
        return [
          states[i],
          (next) => {
            states[i] = typeof next === "function" ? next(states[i]) : next;
          },
        ];
      },
      useEffect(run, deps = []) {
        const i = effectIndex++,
          previous = effects[i];
        if (!previous || deps.some((d, j) => d !== previous.deps[j])) {
          previous?.cleanup?.();
          effects[i] = { deps, run };
        }
      },
      useId: () => "model",
      useCallback: (fn) => fn,
      useRef(value) {
        const i = refIndex++;
        return (refs[i] ??= { current: value });
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
    "./CharacterModelImport": { CharacterModelImport: () => null },
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
    AbortController,
    URLSearchParams,
    document,
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
    render() {
      stateIndex = effectIndex = refIndex = 0;
      let tree = exports[file](props);
      if (importing && tree) tree = tree.type(tree.props);
      for (const effect of effects) {
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
    },
  };
}
const model = {
  status: "ready",
  game_version: "3.7.0",
  preview: { url: "/api/media/preview.glb?token=a&b=c", size_bytes: 1024 },
  download: { url: "https://cdn.example/full.glb", size_bytes: 4096 },
  physics: { url: "/api/media/physics.json", size_bytes: 100 },
};

test("metadata is the only initial request; explicit View 3D mounts preview iframe and Close unloads it", async () => {
  const h = harness();
  assert(!nodes(h.render()).some((n) => n.type === "iframe"));
  assert.equal(h.requests.length, 1);
  assert.equal(
    h.requests[0].url,
    "/characters/character%3A1505/model?game_version=3.7.0",
  );
  h.requests[0].resolve(model);
  await flush();
  let tree = h.render();
  assert(!nodes(tree).some((n) => n.type === "iframe"));
  assert.equal(
    nodes(tree).find((n) => n.type === "a").props.href,
    model.download.url,
  );
  nodes(tree)
    .find((n) => n.type === "button" && n.props["aria-expanded"] === false)
    .props.onClick();
  tree = h.render();
  const frame = nodes(tree).find((n) => n.type === "iframe");
  assert(frame.props.title.includes(h.props.name));
  const url = new URL(frame.props.src, "https://atlas.example");
  assert.equal(url.searchParams.get("model"), model.preview.url);
  assert.equal(url.searchParams.get("physics"), model.physics.url);
  assert.equal(url.searchParams.get("name"), h.props.name);
  nodes(tree)
    .find((n) => n.type === "button" && n.props["aria-expanded"] === true)
    .props.onClick();
  assert(!nodes(h.render()).some((n) => n.type === "iframe"));
  assert.equal(h.requests.length, 1);
  h.close();
});

test("CDN viewer URLs retain their origin and query, while invalid metadata falls back locally", async () => {
  for (const viewer_url of [
    "https://cdn.example/viewer/index.html?v=pinned",
    "javascript:alert(1)",
    "invalid-url",
  ]) {
    const h = harness();
    h.render();
    h.requests[0].resolve({ ...model, viewer_url });
    await flush();
    nodes(h.render())
      .find((n) => n.type === "button" && n.props["aria-expanded"] === false)
      .props.onClick();
    const frame = nodes(h.render()).find((n) => n.type === "iframe");
    const url = new URL(frame.props.src, "https://atlas.example");
    assert.equal(
      url.origin,
      viewer_url.startsWith("https:")
        ? "https://cdn.example"
        : "https://atlas.example",
    );
    if (viewer_url.startsWith("https:"))
      assert.equal(url.searchParams.get("v"), "pinned");
    assert.equal(url.searchParams.get("model"), model.preview.url);
    assert.equal(url.searchParams.get("physics"), model.physics.url);
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
    JSON.stringify({ character_key: "character:1505", force: false }),
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
