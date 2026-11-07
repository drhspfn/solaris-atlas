import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import test from "node:test";
import vm from "node:vm";
import ts from "typescript";

const require = createRequire(import.meta.url);

function harness(hasVoice = true) {
  const states = [],
    refs = [],
    effects = [],
    timers = new Map();
  let stateIndex,
    refIndex,
    effectIndex,
    timerId = 0;
  const audios = [0, 1].map(() => ({
    plays: 0,
    pauses: 0,
    ended: false,
    listeners: new Map(),
    play() {
      this.plays++;
      return Promise.resolve();
    },
    pause() {
      this.pauses++;
    },
    closest() {
      return null;
    },
    addEventListener(name, handler) {
      this.listeners.set(name, handler);
    },
    removeEventListener(name, handler) {
      if (this.listeners.get(name) === handler) this.listeners.delete(name);
    },
    finish() {
      this.listeners.get("ended")?.();
    },
  }));
  const root = {
    querySelector: () => ({
      querySelector: () => (hasVoice ? audios[states[0] || 0] : null),
    }),
    querySelectorAll: () => audios,
    contains: () => false,
  };
  const react = {
    useState(initial) {
      const i = stateIndex++;
      if (!(i in states)) states[i] = initial;
      return [
        states[i],
        (value) => {
          states[i] = typeof value === "function" ? value(states[i]) : value;
        },
      ];
    },
    useRef(initial) {
      const i = refIndex++;
      return (refs[i] ??= { current: initial });
    },
    useEffect(run, deps) {
      const i = effectIndex++;
      const old = effects[i];
      if (!old || deps.some((value, index) => value !== old.deps[index])) {
        old?.cleanup?.();
        effects[i] = { deps, run };
      }
    },
  };
  const source = readFileSync(
    new URL(
      "../src/components/story/QuestIllustratedScene.tsx",
      import.meta.url,
    ),
    "utf8",
  );
  const code = ts.transpileModule(source, {
    compilerOptions: {
      module: ts.ModuleKind.CommonJS,
      jsx: ts.JsxEmit.ReactJSX,
    },
  }).outputText;
  const exports = {};
  const mocks = {
    react,
    "lucide-react": {},
    "./QuestIllustration": {},
    "./sequenceReader": {
      sequenceFrameForLine: () => undefined,
      readingDelay: () => 3000,
    },
    "../ui/mediaViewerUtils": { mediaViewerUrl: (value) => value },
  };
  vm.runInNewContext(code, {
    exports,
    require: (id) => mocks[id] ?? require(id),
    location: { hash: "" },
    window: { addEventListener() {}, removeEventListener() {} },
    requestAnimationFrame: () => 1,
    cancelAnimationFrame() {},
    setTimeout: (fn) => {
      const id = ++timerId;
      timers.set(id, fn);
      return id;
    },
    clearTimeout: (id) => timers.delete(id),
  });
  const lines = [0, 1].map((id) => ({
    id: String(id),
    illustration: { id: String(id), kind: "image", images: [] },
    text: { content: "Text" },
  }));
  const props = { lines, anchors: new Map(), renderLine: () => null };
  function render() {
    stateIndex = refIndex = effectIndex = 0;
    const tree = exports.QuestIllustratedScene(props);
    tree.props.ref.current = root;
    for (const effect of effects)
      if (effect.run) {
        const run = effect.run;
        effect.run = null;
        effect.cleanup = run();
      }
    return tree;
  }
  function nodes(tree) {
    return tree && typeof tree === "object"
      ? [tree, ...[tree.props?.children].flat(Infinity).flatMap(nodes)]
      : [];
  }
  function button(tree, label) {
    return nodes(tree).find(
      (item) =>
        item.type === "button" &&
        (item.props["aria-label"] === label || item.props.children === label),
    );
  }
  return { states, audios, timers, render, button };
}

test("manual next plays the new voice, ends in place, and hash reveal does not autoplay", () => {
  const h = harness();
  let tree = h.render();
  assert.equal(h.audios[0].plays, 0);
  h.button(tree, "Next dialogue line").props.onClick();
  tree = h.render();
  assert.equal(h.audios[1].plays, 1);
  h.audios[1].finish();
  h.render();
  assert.equal(h.states[0], 1);
  assert.equal(h.timers.size, 0);
});

test("auto advances after voice ends; disabling auto does not replay it", () => {
  const h = harness();
  let tree = h.render();
  h.button(tree, "Auto off").props.onClick();
  tree = h.render();
  assert.equal(h.audios[0].plays, 1);
  h.audios[0].finish();
  tree = h.render();
  assert.equal(h.states[0], 1);
  assert.equal(h.audios[1].plays, 1);
  h.button(tree, "Auto on").props.onClick();
  h.render();
  assert.equal(h.audios[1].plays, 1);
  assert.equal(h.audios[1].listeners.has("ended"), false);
});

test("unvoiced manual lines stay in place and auto alone starts a reading timer", () => {
  const h = harness(false);
  let tree = h.render();
  h.button(tree, "Next dialogue line").props.onClick();
  tree = h.render();
  assert.equal(h.timers.size, 0);
  h.button(tree, "Auto off").props.onClick();
  h.render();
  assert.equal(h.timers.size, 1);
});
