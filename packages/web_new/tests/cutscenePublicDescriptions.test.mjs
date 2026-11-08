import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import test from "node:test";
import vm from "node:vm";
import ts from "typescript";

const require = createRequire(import.meta.url);
const React = require("react");
const { renderToStaticMarkup } = require("react-dom/server");
const source = readFileSync(
  new URL("../src/components/story/CutsceneArchive.tsx", import.meta.url),
  "utf8",
);
const code = ts.transpileModule(source, {
  compilerOptions: { target: ts.ScriptTarget.ES2020, module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX },
}).outputText;
const components = {};
const mocks = {
  "@/hooks/useLocale": { useLocale: () => "ja" },
  "@/hooks/usePlayerDisplay": { usePlayerDisplay: () => ({ name: "Rover" }) },
  "@/components/dialogue/PlayerText": { PlayerText: ({ value }) => value },
  "@/lib/router": {
    Link: ({ children }) => React.createElement("a", null, children),
    useSearchParams: () => [new URLSearchParams("locale=ja")],
  },
  "./QuestCutscenes": { Cutscene: () => null },
  "./cutsceneAudioSelection": require("../src/components/story/cutsceneAudioSelection.ts"),
};
vm.runInNewContext(code, {
  exports: components,
  require: (id) =>
    mocks[id] ?? (id.startsWith(".") || id.startsWith("@/") ? {} : require(id)),
});

test("standalone descriptions use authored Rover selectors and avoid technical asset labels", () => {
  const scene = {
    name: "M001",
    game_version: "1.0",
    title: "Scene",
    title_kind: "source",
    event: { playback: {
      nodes: [
        { id: "female", kind: "clip", asset: "a", rover: "female" },
        { id: "male", kind: "clip", asset: "b" },
        { id: "choice", kind: "choice", options: [{ next: "male", rover: "male" }] },
        { id: "unknown", kind: "clip", asset: "c" },
      ],
      media: { a: { asset_node_id: 12 }, b: { asset_node_id: 13 }, c: { asset_node_id: 14 } },
    } },
    passages: [],
    public_descriptions: [
      {
        asset_node_id: 12,
        title: "First variant",
        text: "First public text",
        origin: "analysis",
      },
      {
        asset_node_id: 13,
        title: "Second variant",
        text: "Second public text",
        origin: "manual",
      },
      { asset_node_id: 14, title: "Legacy", text: "Legacy text" },
    ],
    system_description: "Private visual report",
  };
  const html = renderToStaticMarkup(
    React.createElement(components.CutsceneRecord, {
      name: scene.name,
      initial: scene,
    }),
  );
  assert.match(html, /Scene descriptions/);
  assert.match(
    html,
    /First variant.*Female Rover.*AI description.*First public text/,
  );
  assert.match(html, /Second variant.*Male Rover.*Second public text/);
  assert.match(html, /Legacy.*Version 3.*Legacy text/);
  assert.doesNotMatch(html, /Private visual report|Video asset|<details/);

  const single = renderToStaticMarkup(React.createElement(components.CutsceneRecord, {
    name: scene.name,
    initial: { ...scene, event: { playback: null }, public_descriptions: [scene.public_descriptions[2]] },
  }));
  assert.match(single, /Legacy text/);
  assert.doesNotMatch(single, /Version \d|Rover|Video asset/);

  const empty = renderToStaticMarkup(
    React.createElement(components.CutsceneRecord, {
      name: scene.name,
      initial: { ...scene, public_descriptions: undefined },
    }),
  );
  assert.doesNotMatch(empty, /Scene descriptions|Private visual report/);
});
