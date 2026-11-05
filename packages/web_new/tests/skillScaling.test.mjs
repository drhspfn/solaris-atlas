import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import test from "node:test";
import vm from "node:vm";
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

function load(react = React) {
  const mocks = {
    react,
    "@/lib/router": { Link: "a" },
    "../../api/client": { apiUrl: (url) => url },
    "../../data/entities": {},
    "../../data/locales": {
      availableLocales: [{ code: "en", label: "English" }],
    },
    "@/components/ui/Select": { Select: () => null },
    "@/components/ui/TrackPlayer": { TrackPlayer: () => null },
    "@/components/ui/RenderedAnimation": { RenderedAnimation: () => null },
    "@/components/ui/animationPreview": { selectAnimationClip: () => null },
  };
  const exports = {};
  vm.runInNewContext(code, {
    exports,
    require: (id) => (id.endsWith(".css") ? {} : (mocks[id] ?? require(id))),
  });
  return exports;
}

const attribute = (overrides = {}) => ({
  id: 1,
  order: 1,
  name: "Damage",
  description: null,
  parameters: [],
  values: ["12.30%", "24.60%"],
  source: {},
  ...overrides,
});
const skill = (overrides = {}) => ({
  id: 10,
  order: 1,
  type: 1,
  name: "Resonance",
  description: "<b>Original skill description {0}</b>",
  max_level: 10,
  icon: { url: null, engine_path: null },
  attributes: [attribute()],
  ...overrides,
});

function nodes(tree) {
  if (!tree || typeof tree !== "object") return [];
  return [tree, ...[tree.props?.children].flat(Infinity).flatMap(nodes)];
}

function harness(data = skill()) {
  let selectedLevel = 1;
  const { SkillScaling } = load({
    ...React,
    useState: (initial) => [
      selectedLevel ?? initial,
      (value) => {
        selectedLevel = value;
      },
    ],
  });
  return {
    render: () => SkillScaling({ skill: data }),
    select(level) {
      nodes(this.render())
        .find((node) => node.props?.label?.endsWith("talent level"))
        .props.onChange(String(level));
    },
    markup() {
      return renderToStaticMarkup(this.render());
    },
  };
}

test("defaults to level 1 and uses the skill cap rather than the raw array length", () => {
  const data = skill({
    attributes: [
      attribute({
        values: Array.from({ length: 20 }, (_, index) => `${index + 1}.00%`),
      }),
    ],
  });
  const h = harness(data);
  const select = nodes(h.render()).find((node) => node.props?.options);
  assert.equal(select.props.value, 1);
  assert.equal(select.props.label, "Resonance talent level");
  assert.equal(select.props.options.length, 10);
  assert.equal(select.props.options.at(-1).value, 10);
  h.select(10);
  assert.match(h.markup(), /<td>10\.00%<\/td>/);
  assert.equal(
    data.attributes[0].values.length,
    20,
    "raw source data stays intact",
  );
});

test("selection is per skill, exact authored strings survive and singleton values stay constant", () => {
  const data = skill({
    attributes: [
      attribute({ values: ["12.30% + 2 × 4", "24.60% × 3"] }),
      attribute({ id: 2, order: 2, name: "Cooldown", values: ["6.0s"] }),
    ],
  });
  const first = harness(data),
    second = harness(skill({ id: 11 }));
  first.select(2);
  assert.match(first.markup(), /<td>24\.60% × 3<\/td>/);
  assert.match(first.markup(), /<td>6\.0s<\/td>/);
  assert.match(second.markup(), /<td>12\.30%<\/td>/);
  first.select(10);
  assert.match(first.markup(), /<td>—<\/td>/);
  assert.match(first.markup(), /<td>6\.0s<\/td>/);
});

test("empty and missing level entries render a dash without extrapolation", () => {
  const h = harness(
    skill({
      attributes: [
        attribute({ values: [] }),
        attribute({ id: 2, values: ["12%", ""] }),
      ],
    }),
  );
  h.select(2);
  assert.equal((h.markup().match(/<td>—<\/td>/g) || []).length, 2);
});

test("rows retain source order and do not expose unresolved parameter templates", () => {
  const data = skill({
    name: "<b>Resonance</b>",
    attributes: [
      attribute({ id: 2, order: 2, name: null, description: "<i>Healing</i>" }),
      attribute({ name: "<b>Damage</b>", description: "Hits {1}" }),
    ],
  });
  const markup = harness(data).markup();
  assert.match(markup, /aria-label="Resonance values at level 1"/);
  assert.match(markup, /scope="row">Damage<\/th>/);
  assert.doesNotMatch(markup, /Hits \{1\}/);
  assert.match(markup, /scope="row">Healing<\/th>/);
  assert(markup.indexOf("Damage") < markup.indexOf("Healing"));
  assert.equal(
    data.attributes[0].id,
    2,
    "rendering must not reorder API arrays",
  );
});

test("absent or empty attributes hide scaling and preserve existing skill descriptions", () => {
  const { SkillScaling, CharacterArchive } = load();
  for (const attributes of [undefined, []]) {
    const data = skill({ attributes });
    assert.equal(
      renderToStaticMarkup(React.createElement(SkillScaling, { skill: data })),
      "",
    );
    const markup = renderToStaticMarkup(
      React.createElement(CharacterArchive, {
        locale: "en",
        characterKey: "character:1",
        materials: [],
        archive: {
          profile: { voice_actors: {} },
          artwork: [],
          skills: [data],
          skill_tree: [],
          resonance_chain: [],
          voice_lines: [],
          other_voice_events: [],
        },
      }),
    );
    assert.match(markup, /Original skill description \{0\}/);
    assert.doesNotMatch(markup, /character-skill-scaling|Talent level/);
  }
});

test("single-level skills offer one level even when source arrays contain more", () => {
  const h = harness(skill({ max_level: 1 }));
  const select = nodes(h.render()).find((node) => node.props?.options);
  assert.equal(select.props.options.length, 1);
  assert.equal(select.props.options[0].value, 1);
});

test("ambiguous templates retain separate raw parameters at the selected level", () => {
  const h = harness(
    skill({
      attributes: [
        attribute({
          values: [],
          description: "Hits {0} × {1}",
          parameters: [["12.30%", "24.60%"], ["3"], []],
        }),
      ],
    }),
  );
  h.select(2);
  assert.match(h.markup(), /Parameter 1: <\/small>24\.60%/);
  assert.match(h.markup(), /Parameter 2: <\/small>3/);
  assert.match(h.markup(), /Parameter 3: <\/small>—/);
  h.select(10);
  assert.match(h.markup(), /Parameter 1: <\/small>—/);
  assert.match(h.markup(), /Parameter 2: <\/small>3/);
});
