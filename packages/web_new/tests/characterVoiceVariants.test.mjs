import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import vm from "node:vm";
import test from "node:test";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import ts from "typescript";

const require = createRequire(import.meta.url);
const code = ts.transpileModule(
  readFileSync(
    new URL(
      "../src/components/characters/CharacterArchive.tsx",
      import.meta.url,
    ),
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
const locales = [
  ["en", "English"],
  ["ja", "日本語"],
  ["ko", "한국어"],
  ["zh-Hans", "简体中文"],
].map(([code, label]) => ({ code, label }));
const Select = () => null;
const TrackPlayer = () => null;
function setup() {
  let selected = "";
  const mocks = {
    "@/components/dialogue/PlayerText": {
      PlayerText: ({ value, fallback = "" }) =>
        String(value || fallback).replace(/<[^>]*>/g, ""),
    },
    "@/data/gameText": {
      gameTextPlainText: (value) => value.replace(/<[^>]*>/g, ""),
    },
    react: {
      ...React,
      useState: () => [
        selected,
        (value) => {
          selected = value;
        },
      ],
    },
    "@/lib/router": {},
    "../../api/client": { apiUrl: (url) => url },
    "../../data/entities": {},
    "../../data/locales": { availableLocales: locales },
    "@/components/ui/Select": { Select },
    "@/components/ui/TrackPlayer": { TrackPlayer },
    "@/components/characters/CharacterMedia": {
      useCharacterMedia: () => ({ items: [] }),
    },
  };
  const exports = {};
  vm.runInNewContext(code, {
    exports,
    require: (id) => (id.endsWith(".css") ? {} : (mocks[id] ?? require(id))),
  });
  return (line, textLocale = "en") =>
    exports.CharacterVoicePlayback({ line, textLocale });
}
const variants = locales.flatMap(({ code }) =>
  [2, 1].map((n) => ({
    id: `child${n}`,
    source_name: `${code}:hirc1:child${n}`,
    language: code,
    url: `https://cdn.test/${code}/${n}.ogg`,
    status: "available",
    selector_branches: [{ random: [{ mode: "random", weight: 50000 }] }],
  })),
);
const line = {
  id: 160843,
  title: "Fulu",
  text: "English text",
  audio_url: null,
  audio_tracks: {},
  audio_variants: variants,
};
const children = (tree) => React.Children.toArray(tree.props.children);

test("eight authored alternatives require explicit choice and never infer gender or canonical audio", () => {
  const render = setup(),
    tree = render(line),
    [selector] = children(tree);
  assert.equal(selector.type, Select);
  assert.equal(selector.props.value, "");
  assert.equal(
    children(tree).some((child) => child.type === TrackPlayer),
    false,
  );
  assert.equal(selector.props.options.length, 8);
  for (const locale of locales) {
    const options = selector.props.options.filter((option) =>
      option.value.startsWith(`${locale.code}:`),
    );
    assert.equal(options[0].label, `${locale.label} · Variant 1`);
    assert.equal(options[1].label, `${locale.label} · Variant 2`);
    assert.match(options[0].value, /child1$/);
  }
  assert.doesNotMatch(
    JSON.stringify(selector.props.options),
    /Male|Female|Rover|canonical|default/,
  );
  assert.equal(line.text, "English text");
});

test("selection delegates to generic player and survives text-language changes and reordered variants", () => {
  const render = setup();
  children(render(line))[0].props.onChange("ja:hirc1:child2");
  const tree = render(
    { ...line, audio_variants: [...variants].reverse() },
    "ko",
  );
  const [selector, player] = children(tree);
  assert.equal(selector.props.value, "ja:hirc1:child2");
  assert.equal(player.type, TrackPlayer);
  assert.equal(player.props.src, "https://cdn.test/ja/2.ogg");
  assert.equal(player.props.label, "Fulu · 日本語 · Variant 2");
  assert.equal(player.props.autoPlay, undefined);
  const changed = children(
    render({
      ...line,
      audio_variants: variants.filter(
        (v) => v.source_name !== "ja:hirc1:child2",
      ),
    }),
  );
  assert.equal(changed[0].props.value, "");
  assert.equal(
    changed.some((child) => child.type === TrackPlayer),
    false,
  );
});

test("missing variants stay disabled, suppress ambiguous legacy fallback and retain other languages", () => {
  const render = setup();
  const missing = {
    ...line,
    audio_variants: [{ ...variants[0], url: null, status: "missing" }],
    audio_url: "https://cdn.test/guessed.ogg",
    audio_tracks: {
      en: "https://cdn.test/ambiguous.ogg",
      ja: "https://cdn.test/single.ogg",
    },
  };
  const tree = render(missing),
    [selector] = children(tree);
  assert.equal(selector.props.options.length, 2);
  assert.equal(selector.props.options[0].disabled, true);
  assert.match(selector.props.options[0].label, /unavailable/);
  selector.props.onChange(selector.props.options[0].value);
  assert.equal(
    children(render(missing)).some((child) => child.type === TrackPlayer),
    false,
  );
  selector.props.onChange("track:ja");
  assert.equal(
    children(render(missing))[1].props.src,
    "https://cdn.test/single.ogg",
  );
});

test("legacy single-track player keeps its multilingual contract", () => {
  const render = setup();
  const tree = render({
    ...line,
    audio_variants: [],
    audio_url: "/api/voice/en",
    audio_tracks: { en: "/api/voice/en", ja: "/api/voice/ja" },
  });
  assert.equal(tree.type, TrackPlayer);
  assert.equal(tree.props.src, "/voice/en");
  assert.equal(tree.props.tracks.English, "/voice/en");
  assert.equal(tree.props.tracks["日本語"], "/voice/ja");
  assert.equal(tree.props.defaultTrack, "English");
});

test("selected variant uses the unchanged generic player with no preload or autoplay", () => {
  function load(name, mocks) {
    const exports = {};
    const output = ts.transpileModule(
      readFileSync(
        new URL(`../src/components/ui/${name}.tsx`, import.meta.url),
        "utf8",
      ),
      {
        compilerOptions: {
          module: ts.ModuleKind.CommonJS,
          jsx: ts.JsxEmit.ReactJSX,
          esModuleInterop: true,
        },
      },
    ).outputText;
    vm.runInNewContext(output, {
      exports,
      require: (id) => (id.endsWith(".css") ? {} : (mocks[id] ?? require(id))),
    });
    return exports;
  }
  const audio = load("AudioPlayer", { "./voicePlayback": {} });
  const generic = load("TrackPlayer", { "./AudioPlayer": audio });
  const render = setup();
  children(render(line))[0].props.onChange("en:hirc1:child1");
  const player = children(render(line))[1];
  const html = renderToStaticMarkup(
    React.createElement(generic.TrackPlayer, player.props),
  );
  assert.match(
    html,
    /<audio[^>]+src="https:\/\/cdn.test\/en\/1.ogg"[^>]+preload="none"/,
  );
  assert.doesNotMatch(html, /autoplay|<audio[^>]*\bcontrols=/i);
});

for (const gameVersion of ["3.5.0", undefined]) {
  test(`text-language reload preserves snapshot ${gameVersion ?? "current"} and selected audio variant`, async () => {
    const archive = {
      profile: { voice_actors: {} },
      artwork: [],
      skills: [],
      skill_tree: [],
      resonance_chain: [],
      voice_lines: [line],
      other_voice_events: [],
    };
    const states = [],
      effects = [],
      requests = [];
    let stateIndex = 0,
      effectIndex = 0,
      pending = [];
    const mocks = {
      "@/components/dialogue/PlayerText": {
        PlayerText: ({ value, fallback = "" }) =>
          String(value || fallback).replace(/<[^>]*>/g, ""),
      },
      "@/data/gameText": {
        gameTextPlainText: (value) => value.replace(/<[^>]*>/g, ""),
      },
      react: {
        ...React,
        useState(initial) {
          const i = stateIndex++;
          if (!(i in states)) states[i] = initial;
          return [
            states[i],
            (value) => {
              states[i] = value;
            },
          ];
        },
        useEffect(run, deps) {
          const i = effectIndex++;
          if (
            !effects[i] ||
            deps.some((value, j) => value !== effects[i].deps[j])
          ) {
            pending.push(() => {
              effects[i]?.cleanup?.();
              effects[i] = { deps, cleanup: run() };
            });
          }
        },
      },
      "@/lib/router": {},
      "../../data/entities": {},
      "../../data/locales": { availableLocales: locales },
      "@/components/ui/Select": { Select },
      "@/components/ui/TrackPlayer": { TrackPlayer },
      "@/components/characters/CharacterMedia": {
        useCharacterMedia: () => ({ items: [] }),
      },
      "../../api/client": {
        apiUrl: (url) => url,
        api: async (url) => {
          requests.push(url);
          const params = new URL(url, "https://example.test").searchParams;
          return {
            ...archive,
            voice_lines: [
              {
                ...line,
                text: "Japanese text",
                audio_variants:
                  params.get("game_version") === (gameVersion ?? null)
                    ? variants
                    : [],
              },
            ],
          };
        },
      },
    };
    const exports = {};
    vm.runInNewContext(code, {
      exports,
      AbortController,
      require: (id) => (id.endsWith(".css") ? {} : (mocks[id] ?? require(id))),
    });
    const render = () => {
      stateIndex = 0;
      effectIndex = 0;
      pending = [];
      const tree = exports.CharacterArchive({
        archive,
        materials: [],
        locale: "en",
        characterKey: "character:1608",
        gameVersion,
      });
      for (const run of pending) run();
      return tree;
    };
    function nodes(tree) {
      if (!tree || typeof tree !== "object") return [];
      return [
        tree,
        ...React.Children.toArray(tree.props?.children).flatMap(nodes),
      ];
    }
    const audio = setup();
    children(audio(line))[0].props.onChange("ja:hirc1:child2");
    const initial = render();
    assert.equal(requests.length, 0);
    nodes(initial)
      .find(
        (node) =>
          node.type === Select &&
          node.props.label === "Voice line text language",
      )
      .props.onChange("ja");
    render();
    await Promise.resolve();
    await Promise.resolve();
    const updated = nodes(render()).find(
      (node) => node.type === exports.CharacterVoicePlayback,
    );
    const params = new URL(requests[0], "https://example.test").searchParams;
    assert.equal(params.get("game_version"), gameVersion ?? null);
    assert.equal(params.get("locale"), "ja");
    assert.equal(updated.props.line.text, "Japanese text");
    const selected = children(
      audio(updated.props.line, updated.props.textLocale),
    );
    assert.equal(selected[0].props.value, "ja:hirc1:child2");
    assert.equal(selected[1].props.src, "https://cdn.test/ja/2.ogg");
  });
}
