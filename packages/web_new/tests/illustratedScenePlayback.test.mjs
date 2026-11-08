import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import test from "node:test";
import vm from "node:vm";
import ts from "typescript";
const require = createRequire(import.meta.url);
function harness(hasVoice = true, content = "Text", hasMusic = false) {
  const states = [],
    refs = [],
    effects = [],
    timers = new Map();
  let si,
    ri,
    ei,
    timer = 0;
  const audio = {
    plays: 0,
    pauses: 0,
    paused: true,
    play() {
      this.plays++;
      this.paused = false;
      return Promise.resolve();
    },
    pause() {
      this.pauses++;
      this.paused = true;
    },
  };
  const musicAudio = { ...audio };
  const voiceAudio = hasVoice ? audio : { ...audio };
  const react = {
    useState(v) {
      const i = si++;
      if (!(i in states)) states[i] = v;
      return [
        states[i],
        (x) => (states[i] = typeof x === "function" ? x(states[i]) : x),
      ];
    },
    useRef(v) {
      const i = ri++;
      return (refs[i] ??= { current: v });
    },
    useEffect(run, deps) {
      const i = ei++,
        old = effects[i];
      if (!old || deps.some((v, n) => v !== old.deps[n])) {
        old?.cleanup?.();
        effects[i] = { deps, run };
      }
    },
  };
  const code = ts.transpileModule(
    readFileSync(
      new URL(
        "../src/components/story/QuestIllustratedScene.tsx",
        import.meta.url,
      ),
      "utf8",
    ),
    {
      compilerOptions: {
        module: ts.ModuleKind.CommonJS,
        jsx: ts.JsxEmit.ReactJSX,
      },
    },
  ).outputText;
  const exports = {};
  const mocks = {
    react,
    "lucide-react": {},
    "./QuestIllustration": {},
    "./IllustratedDialogueLine": {},
    "@/data/gameText": { gameTextPlainText: (v) => v?.content || "" },
    "@/data/dialogueUiAssets": { GAME_DIALOGUE_UI: {} },
    "@/preferences/NarrativePreferences": {
      useNarrativePreferences: () => ({
        dialogueStyle: "game",
        voiceLanguage: "en",
        preferredRover: "ask",
      }),
    },
    "./sequenceReader": { sequenceFrameForLine: () => undefined },
    "../ui/mediaViewerUtils": { mediaViewerUrl: (v) => v },
  };
  const win = {
    addEventListener() {},
    removeEventListener() {},
    setTimeout(fn, ms) {
      const id = ++timer;
      timers.set(id, { fn, ms });
      return id;
    },
    clearTimeout(id) {
      timers.delete(id);
    },
  };
  vm.runInNewContext(code, {
    exports,
    require: (id) => mocks[id] ?? require(id),
    location: { hash: "" },
    window: win,
    document: { addEventListener() {}, removeEventListener() {} },
    requestAnimationFrame: () => 1,
    cancelAnimationFrame() {},
  });
  const lines = [0, 1].map((id) => ({
    id: String(id),
    illustration: { id: String(id), kind: "image", images: [] },
    text: { content },
    media: hasVoice
      ? {
          voice_references: [
            { tracks: [{ language: "en", url: `/voice-${id}.ogg` }] },
          ],
        }
      : undefined,
  }));
  const props = {
    lines,
    anchors: new Map(),
    display: { name: "Rover", colorMode: "accent", customColor: "#fff" },
    backgroundMusic: hasMusic ? { url: "/music.ogg", loop: true } : null,
    renderLine: () => null,
  };
  const nodes = (t) =>
    t && typeof t === "object"
      ? [t, ...[t.props?.children].flat(Infinity).flatMap(nodes)]
      : [];
  function render() {
    si = ri = ei = 0;
    const tree = exports.QuestIllustratedScene(props);
    tree.props.ref.current = { scrollIntoView() {} };
    for (const x of nodes(tree))
      if (x.type === "audio" && x.props.ref)
        x.props.ref.current = x.props["data-scene-background-music"]
          ? hasVoice
            ? musicAudio
            : audio
          : voiceAudio;
    for (const e of effects)
      if (e.run) {
        const run = e.run;
        e.run = null;
        e.cleanup = run();
      }
    return tree;
  }
  return {
    lines,
    states,
    audio,
    musicAudio: hasVoice ? musicAudio : audio,
    voiceAudio,
    timers,
    render,
    nodes,
    button: (tree, label) =>
      nodes(tree).find(
        (x) => x.type === "button" && x.props["aria-label"] === label,
      ),
  };
}
test("scene starts first voice and waits for text and audio before advancing", () => {
  const h = harness();
  let tree = h.render();
  assert.equal(h.audio.plays, 0);
  h.button(tree, "Enable auto advance").props.onClick();
  tree = h.render();
  assert.ok(h.audio.plays > 0);
  h.nodes(tree)
    .find((x) => x.type === "audio")
    .props.onEnded();
  h.render();
  assert.equal(h.states[0], 0);
  [...h.timers.values()].find((t) => t.ms === 800).fn();
  h.render();
  h.render();
  assert.equal(h.states[0], 1);
});
test("unvoiced auto playback respects text time and can be stopped", () => {
  const h = harness(false);
  let tree = h.render();
  h.button(tree, "Enable auto advance").props.onClick();
  tree = h.render();
  tree = h.render();
  assert.ok([...h.timers.values()].some((t) => t.ms === 800));
  h.button(tree, "Disable auto advance").props.onClick();
  h.render();
  [...h.timers.values()].find((t) => t.ms === 800).fn();
  h.render();
  h.render();
  assert.equal(h.states[0], 0);
});
test("manual navigation does not enable auto and native audio controls are absent", () => {
  const h = harness();
  let tree = h.render();
  h.button(tree, "Next dialogue line").props.onClick();
  tree = h.render();
  assert.equal(h.states[0], 1);
  assert.equal(h.states[3], false);
  assert.ok(
    h
      .nodes(tree)
      .filter((x) => x.type === "audio")
      .every((x) => !x.props.controls),
  );
});

test("clicking during reveal shows the complete line before advancing", () => {
  const h = harness(false, "x".repeat(40));
  let tree = h.render();
  h.button(tree, "Enable auto advance").props.onClick();
  tree = h.render();
  h.render();
  [...h.timers.values()].find((t) => t.ms === 300).fn();
  tree = h.render();
  h.button(tree, "Next dialogue line").props.onClick();
  tree = h.render();
  assert.equal(h.states[0], 0);
  assert.equal(h.states[6], true);
});

test("background audio stops at the end of the last line", () => {
  const h = harness(false, "Text", true);
  let tree = h.render();
  h.button(tree, "Enable auto advance").props.onClick();
  h.render();
  h.render();
  [...h.timers.values()].find((t) => t.ms === 800).fn();
  h.render();
  h.render();
  h.render();
  [...h.timers.values()].find((t) => t.ms === 800).fn();
  h.render();
  h.render();
  assert.equal(h.states[0], 1);
  assert.equal(h.states[3], false);
  assert.ok(h.audio.pauses > 0);
});

test("indicator appears during a long reveal and resets for the next line", () => {
  const h = harness(false, "x".repeat(40));
  let tree = h.render();
  h.button(tree, "Enable auto advance").props.onClick();
  tree = h.render();
  const dialogue = () =>
    h.nodes(tree).find((x) => x.props?.showContinue !== undefined);
  assert.equal(dialogue().props.showContinue, false);
  [...h.timers.values()].find((t) => t.ms === 300).fn();
  tree = h.render();
  assert.equal(dialogue().props.showContinue, true);
  assert.equal(dialogue().props.animate, true);
  h.button(tree, "Next dialogue line").props.onClick();
  tree = h.render();
  h.button(tree, "Next dialogue line").props.onClick();
  tree = h.render();
  assert.equal(h.states[0], 1);
  assert.equal(dialogue().props.showContinue, false);
});

test("choices take over after reveal and prevent automatic advancement", () => {
  const h = harness(false, "x".repeat(40));
  h.lines[0].player_choices = [{ id: "choice" }];
  let tree = h.render();
  h.button(tree, "Enable auto advance").props.onClick();
  h.render();
  [...h.timers.values()].find((t) => t.ms === 300).fn();
  tree = h.render();
  h.button(tree, "Next dialogue line").props.onClick();
  tree = h.render();
  assert.equal(h.button(tree, "Next dialogue line").props.disabled, true);
  [...h.timers.values()].find((t) => t.ms === 1700).fn();
  h.render();
  h.render();
  assert.equal(h.states[0], 0);
});

test("voice and music volume are independent and voice volume survives line changes", () => {
  const h = harness(true, "Text", true);
  let tree = h.render();
  const slider = (label) =>
    h
      .nodes(tree)
      .find((x) => x.type === "input" && x.props["aria-label"] === label);
  assert.equal(h.musicAudio.volume, 0.35);
  assert.equal(h.voiceAudio.volume, 1);
  slider("Music volume").props.onChange({ target: { value: "0.12" } });
  slider("Voice volume").props.onChange({ target: { value: "0.6" } });
  tree = h.render();
  assert.equal(h.musicAudio.volume, 0.12);
  assert.equal(h.voiceAudio.volume, 0.6);
  h.button(tree, "Next dialogue line").props.onClick();
  tree = h.render();
  assert.equal(h.voiceAudio.volume, 0.6);
  assert.equal(h.musicAudio.volume, 0.12);
  slider("Voice volume").props.onChange({ target: { value: "0" } });
  h.render();
  assert.equal(h.voiceAudio.volume, 0);
  assert.equal(h.musicAudio.volume, 0.12);
});

test("music controls are omitted when a scene has no background track", () => {
  const h = harness();
  const tree = h.render();
  assert.ok(
    h.nodes(tree).some((x) => x.props?.["aria-label"] === "Voice volume"),
  );
  assert.ok(
    !h.nodes(tree).some((x) => x.props?.["aria-label"] === "Music volume"),
  );
});

test("whole-scene clicks advance, controls and transcript do not", () => {
  const h = harness(false, "Long dialogue");
  let tree = h.render();
  const click = (blocked = false) =>
    tree.props.onClick({ target: { closest: () => (blocked ? {} : null) } });
  click(); // Background image starts the scene.
  tree = h.render();
  [...h.timers.values()].find((t) => t.ms === 300).fn();
  tree = h.render();
  click(); // First click reveals the text.
  tree = h.render();
  assert.equal(h.states[0], 0);
  click(true); // Settings and other controls do not advance.
  h.render();
  assert.equal(h.states[0], 0);
  click();
  tree = h.render();
  assert.equal(h.states[0], 1);
  const transcriptButton = h
    .nodes(tree)
    .find(
      (x) =>
        x.type === "button" &&
        [x.props.children].flat(Infinity).includes("Read transcript"),
    );
  transcriptButton.props.onClick();
  tree = h.render();
  click();
  assert.equal(h.states[0], 1);
  let prevented = false;
  tree.props.onContextMenu({
    preventDefault: () => {
      prevented = true;
    },
  });
  assert.equal(prevented, false);
});
