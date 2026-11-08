import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import test from "node:test";
import vm from "node:vm";
import ts from "typescript";

const require = createRequire(import.meta.url);
const source = readFileSync(
  new URL("../src/components/story/QuestMediaReferences.tsx", import.meta.url),
  "utf8",
);
const code = ts.transpileModule(source, {
  compilerOptions: {
    module: ts.ModuleKind.CommonJS,
    jsx: ts.JsxEmit.ReactJSX,
    esModuleInterop: true,
  },
}).outputText;
const exports = {};
const mocks = {
  "../ui/Select": { Select: () => null },
  "lucide-react": new Proxy({}, { get: () => "icon" }),
  "@/lib/router": { Link: "atlas-link" },
  "../../preferences/NarrativePreferences": {},
  "./cutsceneAnchor": {
    cutsceneAnchor: () => "scene",
    packageCutsceneAnchor: (name) => `package-${name}`,
  },
};
vm.runInNewContext(code, {
  exports,
  require: (id) => mocks[id] ?? require(id),
});

test("scene navigation does not report an intentional audio interruption as a failed track", async () => {
  for (const name of ["AbortError", "NotSupportedError"]) {
    const updates = [];
    const audio = { paused: true, play: () => Promise.reject({ name }) };
    const components = {};
    vm.runInNewContext(code, {
      exports: components,
      document: { querySelectorAll: () => [] },
      require: (id) =>
        id === "react"
          ? {
              useRef: () => ({ current: audio }),
              useState: (initial) => [initial, (value) => updates.push(value)],
            }
          : id === "../../preferences/NarrativePreferences"
            ? {
                useNarrativePreferences: () => ({
                  voiceLanguage: "en",
                  preferredRover: "ask",
                }),
              }
            : (mocks[id] ?? require(id)),
    });
    const tree = components.DialogueAudioReference({
      media: {
        voice_references: [
          {
            source: { file: "PlotAudio" },
            tracks: [{ language: "en", rover: null, url: "voice.wav" }],
          },
        ],
      },
    });
    let play;
    function visit(node) {
      if (!node || typeof node !== "object") return;
      if (node.props?.["aria-label"] === "Play voice") play = node;
      for (const child of [node.props?.children].flat(Infinity)) visit(child);
    }
    visit(tree);
    assert.ok(play);
    play.props.onClick();
    await Promise.resolve();
    assert.deepEqual(updates, name === "AbortError" ? [] : ["voice.wav"]);
  }
});

test("package-linked scenes remain navigable without a claimed transcript position", () => {
  const tree = exports.QuestMediaReferences({
    manifest: {
      game_version: "3.3.0",
      events: [],
      video_packages: [],
      related_cutscenes: [
        { name: "C0028", playable: false, context_inferred: true },
      ],
    },
    stateAnchors: new Map(),
  });
  const links = [];
  const text = [];
  function visit(node) {
    if (typeof node === "string") text.push(node);
    if (!node || typeof node !== "object") return;
    if (node.type === "a") links.push(node.props.href);
    for (const child of [node.props?.children].flat(Infinity)) visit(child);
  }
  visit(tree);
  assert.deepEqual(links, ["#package-C0028"]);
  assert.ok(
    text.some((value) => value.includes("story position is not yet mapped")),
  );
  assert.ok(text.some((value) => value.includes("Video not available yet")));
});

test("a contextual scene appears once and jumps to the scene inside the quest", () => {
  const event = {
    kind: "cutscene",
    reference: "cutscene:M0366",
    flow_state: "Cave_1_39",
    action_index: 1,
    context_inferred: true,
  };
  const tree = exports.QuestMediaReferences({
    manifest: {
      events: [event],
      video_packages: [],
      related_cutscenes: [
        { name: "M0366", playable: false, context_inferred: true },
      ],
    },
    stateAnchors: new Map(),
    visibleCutscenes: [event],
  });
  const links = [];
  function visit(node) {
    if (!node || typeof node !== "object") return;
    if (node.type === "a") links.push(node.props.href);
    for (const child of [node.props?.children].flat(Infinity)) visit(child);
  }
  visit(tree);
  assert.deepEqual(links, ["#scene"]);
});

test("unavailable and package-linked scenes have actual in-page targets", () => {
  const componentSource = readFileSync(
    new URL("../src/components/story/QuestCutscenes.tsx", import.meta.url),
    "utf8",
  );
  const componentCode = ts.transpileModule(componentSource, {
    compilerOptions: {
      module: ts.ModuleKind.CommonJS,
      jsx: ts.JsxEmit.ReactJSX,
      esModuleInterop: true,
    },
  }).outputText;
  const components = {};
  vm.runInNewContext(componentCode, {
    exports: components,
    require: (id) => {
      if (id === "../../data/gameText") return { gameTextPlainText: (value) => value?.content || value || '' };
      if (id === "./cutsceneAnchor") return mocks[id];
      if (id === "@/lib/router") return { Link: ({ to, children }) => React.createElement('a', { href: to }, children) };
      if (id === "../dialogue/PlayerText") return { PlayerText: ({ value, fallback }) => value?.content || value || fallback };
      if (id.startsWith(".") || id.startsWith("@/")) return {};
      return require(id);
    },
  });
  const React = require("react");
  const { renderToStaticMarkup } = require("react-dom/server");
  const related = renderToStaticMarkup(
    React.createElement(components.RelatedQuestCutscenes, {
      manifest: {
        game_version: "3.3.0",
        events: [],
        video_packages: [],
        related_cutscenes: [
          { name: "C0028", playable: false, context_inferred: true },
        ],
      },
    }),
  );
  assert.match(related, /id="package-C0028"/);
  assert.match(related, /Video not available yet/);
  const contextual = renderToStaticMarkup(
    React.createElement(components.Cutscene, {
      event: {
        kind: "cutscene",
        reference: "cutscene:M0366",
        flow_state: "Cave_1_39",
        action_index: 1,
        context_inferred: true,
      },
    }),
  );
  assert.match(contextual, /id="scene"/);
  assert.match(contextual, /M0366.*Video not available yet/);
  assert.match(
    contextual,
    /Possible quest connection; occurrence and position in the story are unconfirmed/,
  );
  const directEvent = {
    kind: "cutscene",
    reference: "cutscene:M0366",
    flow_state: "Cave_1_39",
    action_index: 1,
    context_inferred: false,
    story_order_mapped: false,
  };
  const direct = renderToStaticMarkup(
    React.createElement(components.Cutscene, { event: directEvent }),
  );
  assert.match(direct, /Position in the story is not mapped/);
  assert.doesNotMatch(direct, /Possible quest connection/);
  const positioned = renderToStaticMarkup(
    React.createElement(components.Cutscene, {
      event: { ...directEvent, story_position: [1], story_order_mapped: true },
    }),
  );
  assert.doesNotMatch(positioned, /Position in the story is not mapped/);
  const review = renderToStaticMarkup(React.createElement(components.Cutscene, {
    event: { ...directEvent, review_position: [48, 6, -1] },
  }));
  assert.match(review, /Shown in archive review order/);
  assert.doesNotMatch(review, /Position in the story is not mapped/);
  const continuation = renderToStaticMarkup(React.createElement(components.RelatedQuestCutscenes, {
    manifest: { events: [], related_cutscenes: [{ name: 'M0391', playable: false,
      continuations: [{ game_quest_id: 200, quest_href: '/quests/200-next', title: { content: 'After the battle' } }] }] },
  }));
  assert.match(continuation, /Continue the story/);
  assert.match(continuation, /\/quests\/200-next/);
  const repeatedStage = renderToStaticMarkup(React.createElement(components.RelatedQuestCutscenes, {
    currentQuestIds: [886000002, 6616725], currentQuestTitle: { content: 'Simulacrum of the Heart' },
    manifest: { events: [], related_cutscenes: [{ name: 'M0391', playable: false,
      continuations: [{ game_quest_id: 168000003, title: { content: 'Simulacrum of the Heart' } }] }] },
  }));
  assert.doesNotMatch(repeatedStage, /href="\/quests\//);
  assert.match(repeatedStage, /M0391/);
  const self = { game_quest_id: 168000003, quest_href: '/quests/6616725-simulacrum-of-the-heart', title: 'Simulacrum of the Heart' };
  for (const currentQuestIds of [[168000003], ['6616725']]) {
    const html = renderToStaticMarkup(React.createElement(components.RelatedQuestCutscenes, {
      currentQuestIds,
      manifest: { events: [], related_cutscenes: [{ name: 'M0391', playable: false,
        continuations: [self, { game_quest_id: 200, quest_href: '/quests/200-next', title: 'Next quest' }] }] },
    }));
    assert.doesNotMatch(html, /href="\/quests\/6616725/);
    assert.match(html, /href="\/quests\/200-next/);
    assert.match(html, /M0391/); // Removing a self-link must not remove the scene.
    assert.match(html, /Linked to this quest/);
  }
});
