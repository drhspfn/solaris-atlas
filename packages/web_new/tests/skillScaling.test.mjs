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

function load(react = React, playerText = ({ value, fallback = "" }) => String(value || fallback).replace(/<[^>]*>/g, ""), gameText = { gameTextPlainText: value => value.replace(/<[^>]*>/g, "") }) {
  const mocks = {
    '@/components/dialogue/PlayerText': { PlayerText: playerText },
    '@/data/gameText': gameText,
    react,
    "@/lib/router": { Link: "a" },
    "../../api/client": { apiUrl: (url) => url },
    "../../data/entities": {},
    "../../data/locales": {
      availableLocales: [{ code: "en", label: "English" }],
    },
    "@/components/ui/Select": { Select: () => null },
    "@/components/ui/TrackPlayer": { TrackPlayer: () => null },
    "@/components/characters/CharacterMedia": { CharacterMediaGallery: () => null, useCharacterMedia: () => ({ items: [] }) },
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
        .find((node) => node.props?.type === "range")
        .props.onChange({ currentTarget: { valueAsNumber: level } });
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
  const slider = nodes(h.render()).find((node) => node.props?.type === "range");
  assert.equal(slider.props.value, 1);
  assert.equal(slider.props["aria-label"], "Resonance talent level");
  assert.equal(slider.props.min, 1);
  assert.equal(slider.props.max, 10);
  assert.equal(slider.props.step, 1);
  assert.match(h.markup(), /<output>1 \/ 10<\/output>/);
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
    assert.match(markup, /Original skill description —/);
    assert.doesNotMatch(markup, /character-skill-scaling|Talent level/);
  }
});

test("single-level skills display stats without a slider", () => {
  for (const max_level of [1, null, undefined]) {
    const h = harness(skill({ max_level }));
    assert(!nodes(h.render()).some((node) => node.props?.type === "range"));
    assert.match(h.markup(), /<td>12\.30%<\/td>/);
  }
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

function cardHarness(data = skill(), overflow = false) {
  const states = [];
  let index = 0;
  const { SkillCard, SkillScaling } = load({
    ...React,
    useId: () => "talent-test",
    useRef: () => ({ current: null }),
    useEffect: () => {},
    useState(initial) {
      const slot = index++;
      if (!(slot in states)) states[slot] = slot === 1 ? overflow : initial;
      return [
        states[slot],
        (value) => {
          states[slot] = value;
        },
      ];
    },
  });
  function expand(node) {
    if (!node || typeof node !== "object") return node;
    if (node.type === SkillScaling) return expand(SkillScaling(node.props));
    if (!node.props?.children) return node;
    return React.cloneElement(
      node,
      {},
      ...[node.props.children].flat(Infinity).map(expand),
    );
  }
  return {
    render() {
      index = 0;
      return expand(SkillCard({ skill: data }));
    },
    tab(name) {
      nodes(this.render())
        .find((n) => n.props?.role === "tab" && n.props.children === name)
        .props.onClick();
    },
    select(level) {
      nodes(this.render())
        .find((n) => n.props?.type === "range")
        .props.onChange({ currentTarget: { valueAsNumber: level } });
    },
    markup() {
      return renderToStaticMarkup(this.render());
    },
  };
}

test("cards have linked accessible tabs and retain an independent slider across toggles", () => {
  const first = cardHarness(),
    second = cardHarness(skill({ id: 11 }));
  const description = nodes(first.render()).find(
    (n) => n.props?.role === "tab" && n.props.children === "Description",
  );
  assert.equal(description.props["aria-selected"], true);
  assert.equal(description.props.tabIndex, 0);
  const panel = nodes(first.render()).find(
    (n) => n.props?.id === description.props["aria-controls"],
  );
  assert.equal(panel.props["aria-labelledby"], description.props.id);
  assert.equal(panel.props.hidden, false);
  first.tab("Stats");
  first.select(2);
  first.tab("Description");
  first.tab("Stats");
  assert.match(first.markup(), /<td>24\.60%<\/td>/);
  assert.match(first.markup(), /<output>2 \/ 10<\/output>/);
  assert.match(second.markup(), /<td>12\.30%<\/td>/);
  assert.doesNotMatch(
    first.markup(),
    /Original skill description \{0\}|Source: Skill|Skill #/,
  );
});

test("tabs support arrow, Home and End keys and move focus", () => {
  const h = cardHarness();
  let focused = null,
    prevented = 0;
  for (const [key, expected] of [
    ["End", 1],
    ["Home", 0],
    ["ArrowRight", 1],
    ["ArrowLeft", 0],
  ]) {
    const tabs = nodes(h.render()).find((n) => n.props?.role === "tablist");
    tabs.props.onKeyDown({
      key,
      nativeEvent: {},
      preventDefault() {
        prevented++;
      },
      currentTarget: {
        querySelectorAll: () =>
          [0, 1].map((i) => ({
            focus() {
              focused = i;
            },
          })),
      },
    });
    assert.equal(focused, expected);
    assert.equal(
      nodes(h.render()).filter((n) => n.props?.role === "tab")[expected].props[
        "aria-selected"
      ],
      true,
    );
  }
  assert.equal(prevented, 4);
});

test("long descriptions use native disclosure without deriving text from stats", () => {
  const markup = cardHarness(
    skill({
      description: "Authored text {0}",
      attributes: [attribute({ values: ["999%"] })],
    }),
    true,
  ).markup();
  assert.match(markup, /character-talent-preview/);
  assert.match(markup, /<details><summary>/);
  assert.match(markup, /Read more/);
  assert.match(markup, /Show less/);
  assert.match(markup, /Authored text —/);
  assert.doesNotMatch(markup, /Authored text 999%|Authored text \{0\}/);
});

test("public talents omit raw tree, technical labels and empty cards but retain named passives", () => {
  const { CharacterArchive } = load();
  const markup = renderToStaticMarkup(
    React.createElement(CharacterArchive, {
      locale: "en",
      characterKey: "character:1",
      materials: [],
      archive: {
        profile: { voice_actors: {} },
        artwork: [],
        skills: [
          skill(),
          skill({
            id: 1003808,
            name: null,
            description: "Has chance produce special dishes when cooking.",
            attributes: [],
            max_level: 1,
          }),
          skill({
            id: 1003809,
            name: null,
            description: null,
            attributes: [],
            max_level: 1,
          }),
          skill({
            id: 12,
            name: "Named passive",
            description: null,
            attributes: [],
            max_level: 1,
          }),
        ],
        skill_tree: [
          {
            id: 999,
            title: "Raw tree requirement",
            description: "Hidden raw node",
            costs: [],
          },
        ],
        resonance_chain: [],
        voice_lines: [],
        other_voice_events: [],
      },
    }),
  );
  assert.match(markup, />Talents</);
  assert.match(markup, /Named passive/);
  assert.equal(
    (markup.match(/character-skill-card character-talent-card/g) || []).length,
    3,
  );
  assert.match(markup, /Has chance produce special dishes when cooking\./);
  assert(
    markup.indexOf("Original skill description") <
      markup.indexOf("Has chance produce"),
  );
  assert(
    markup.indexOf("Has chance produce") < markup.indexOf("Named passive"),
  );
  assert.doesNotMatch(
    markup,
    /Raw tree requirement|Hidden raw node|Skill tree requirements| nodes|Source: Skill|Skill 01|1003808/,
  );
});

test("MoonSteps preserves authored level 1 and level 10 display values", () => {
  const h = harness(
    skill({
      name: "MoonSteps",
      attributes: [
        attribute({
          values: ["44.10%", "", "", "", "", "", "", "", "", "87.68%"],
        }),
      ],
    }),
  );
  assert.match(h.markup(), /<td>44\.10%<\/td>/);
  h.select(10);
  assert.match(h.markup(), /<td>87\.68%<\/td>/);
  assert.match(h.markup(), /aria-label="MoonSteps talent level"/);
});

test("description binds only explicit backend parameters and masks unresolved tokens", () => {
  const h = cardHarness(
    skill({
      description: "Authored {0}, missing {1}",
      description_params: ["3.0s"],
      attributes: [attribute({ values: ["999%"] })],
    }),
  );
  assert.match(h.markup(), /Authored 3\.0s, missing —/);
  h.tab("Stats");
  h.select(2);
  h.tab("Description");
  assert.match(h.markup(), /Authored 3\.0s, missing —/);
  assert.doesNotMatch(h.markup(), /Authored 999%|missing \{1\}/);
});


test("Iuno confirmed SkillDetailNum fills source descriptions including named passives", () => {
  const cases = [
  {
    "id": 1003804,
    "name": "Waxing Ascent",
    "description": "Every time Iuno casts Basic Attack, Heavy Attack, Dodge Counter, Resonance Skill, Resonance Liberation, or Intro Skill, she gains {0} Shield equal to {1} of her ATK for {2}s. This Shield will not be passed on to the incoming Resonator.",
    "description_params": [
      "1",
      "32%",
      "15"
    ]
  },
  {
    "id": 1003805,
    "name": "Derivation",
    "description": "When Iuno casts Intro Skill or Resonance Liberation, she immediately gains {0} stacks of <color=Highlight><te href=141002>Blessing of the Wan Light</te></color>.",
    "description_params": [
      "5"
    ]
  },
  {
    "id": 1003809,
    "name": "From Gloom to Gleam",
    "description": "Attack the target to deal {0} <color=Wind>Aero DMG</color>. The incoming Resonator gains {1} Heavy Attack DMG Amplification for {2}s. This effect ends early if they are switched off the field.\nCasting <color=Highlight>Outro Skill</color> won't interrupt <color=Highlight>Heavy Attack - Absolute Fullness</color>, and the Outro Skill effect still applies.",
    "description_params": [
      "100%",
      "50%",
      "14"
    ]
  }
];
  for (const data of cases) {
    const markup = cardHarness(skill({ ...data, max_level: 1, attributes: [] })).markup();
    for (const value of data.description_params) assert(markup.includes(value));
    assert.doesNotMatch(markup, /\{\d+[^}]*\}|type="range"|role="tablist"/);
    if (data.id === 1003804) assert.match(markup, /gains 1 Shield equal to 32% of her ATK for 15s/);
    if (data.id === 1003805) assert.match(markup, /gains 5 stacks of Blessing of the Wan Light/);
    if (data.id === 1003809) assert.match(markup, /deal 100% Aero DMG/);
  }
});


test("Iuno input hints use authored PC labels and leave unrelated text unchanged", () => {
  const description = "{Cus:Ipt,Touch=Tap PC=Press Gamepad=Press} Basic Attack; " +
    "then {Cus:Ipt,Touch=tap PC=press Gamepad=press} again. " +
    "Localized текст stays. {Cus:Other,PC=Keep}";
  const markup = cardHarness(skill({ description, attributes: [] })).markup();
  assert.match(markup, /Press Basic Attack; then press again\. Localized текст stays\. \{Cus:Other,PC=Keep\}/);
  assert.doesNotMatch(markup, /Cus:Ipt|Touch=|Gamepad=/);
});

test("profile biography appears only when it adds to the header introduction", () => {
  const { CharacterArchive } = load();
  const archive = { biography: "<b>Guardian</b> of the Black Shores.", profile: { voice_actors: {} }, artwork: [], skills: [], skill_tree: [], resonance_chain: [], voice_lines: [], other_voice_events: [] };
  const render = introduction => renderToStaticMarkup(React.createElement(CharacterArchive, { archive, introduction, materials: [], locale: 'en', characterKey: 'character:1505' }));
  assert.doesNotMatch(render('Guardian  of the Black Shores.'), /character-biography/);
  assert.match(render('A different summary.'), /character-biography/);
  assert.match(render(undefined), /character-biography/);
});

test("empty archive hides progression, talents, chain and voice sections", () => {
  const { CharacterArchive } = load();
  const archive = { profile: { voice_actors: {}, max_level: 90 }, artwork: [],
    skills: [skill({ name: null, description: null, attributes: [] })],
    resonance_chain: [{ id: 1, name: null, description: null }], voice_lines: [], other_voice_events: [] };
  const markup = renderToStaticMarkup(React.createElement(CharacterArchive, {
    archive, materials: [], locale: 'en', characterKey: 'character:1',
  }));
  assert.doesNotMatch(markup, /Profile &amp; progression|Max level|Upgrade materials|>Talents<|Resonance chain|Voice lines/);
});

test("available materials, chain and voice events retain their sections", () => {
  const { CharacterArchive } = load();
  const archive = { profile: { voice_actors: {}, max_level: 90 }, artwork: [], skills: [],
    resonance_chain: [{ id: 1, order: 1, name: 'First sequence', description: null }],
    voice_lines: [], other_voice_events: [{ category: 'Battle', event: 'Attack' }] };
  const markup = renderToStaticMarkup(React.createElement(CharacterArchive, {
    archive, materials: [{ item_id: 5, kind: 'character_ascension' }], locale: 'en', characterKey: 'character:1',
  }));
  for (const label of ['Profile &amp; progression', 'Max level 90', 'Upgrade materials', 'First sequence', 'Voice lines', 'Attack'])
    assert.ok(markup.includes(label), label);
});

test("archive passes complete authored markup and display to the shared renderer", () => {
  const calls = [];
  const PlayerText = (props) => {
    calls.push(props);
    return "rendered";
  };
  const { CharacterArchive } = load(React, PlayerText);
  const display = {
    name: "Custom Rover",
    colorMode: "accent",
    customColor: "",
  };
  const archive = {
    biography: "<color=Highlight>Meet {PlayerName}</color>",
    profile: { voice_actors: {} },
    artwork: [],
    skills: [
      skill({
        name: "Talent",
        description: "<color=Wind>Damage {0}</color> {PlayerName}",
        description_params: ["12.30%"],
        attributes: [],
      }),
    ],
    skill_tree: [],
    resonance_chain: [
      {
        id: 1,
        order: 1,
        name: "Sequence",
        description: "<i>Power {0}</i>",
        description_params: ["3"],
      },
    ],
    voice_lines: [
      {
        id: 3,
        order: 1,
        title: "Greeting",
        text: "<color=Highlight>Hello {PlayerName}</color>",
      },
    ],
    other_voice_events: [],
  };
  renderToStaticMarkup(
    React.createElement(CharacterArchive, {
      archive,
      locale: "en",
      characterKey: "character:1",
      materials: [],
      display,
    }),
  );
  for (const value of [
    "<color=Highlight>Meet {PlayerName}</color>",
    "<color=Wind>Damage 12.30%</color> {PlayerName}",
    "<i>Power 3</i>",
    "<color=Highlight>Hello {PlayerName}</color>",
  ]) {
    assert(
      calls.some((call) => call.value === value && call.display === display),
      value,
    );
  }
});


test("talent renders real game text with formatted parameters and selected Rover", () => {
  const compile = path => ts.transpileModule(readFileSync(new URL(path, import.meta.url), "utf8"), {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX },
  }).outputText;
  function module(path, mocks = {}) {
    const exports = {};
    vm.runInNewContext(compile(path), { exports, require: id => mocks[id] ?? require(id) });
    return exports;
  }
  const colors = module("../src/data/gameTextColors.ts");
  const localized = module("../src/data/localized.ts");
  const gameText = module("../src/data/gameText.ts", { "./gameTextColors": colors, "./localized": localized });
  const { PlayerText } = module("../src/components/dialogue/PlayerText.tsx", {
    "../../data/gameText": gameText, "../../data/localized": localized,
  });
  const { CharacterArchive } = load(React, PlayerText, gameText);
  const markup = renderToStaticMarkup(React.createElement(CharacterArchive, {
    locale: "en", characterKey: "character:1", materials: [],
    display: { name: "Astra", gender: "female", colorMode: "accent", customColor: "" },
    archive: { profile: { voice_actors: {} }, artwork: [], skill_tree: [], resonance_chain: [],
      voice_lines: [], other_voice_events: [], skills: [skill({ attributes: [],
        description: "<color=Highlight>{PlayerName} {Male=he;Female=she} gains {0}</color>",
        description_params: ["12.30%"] })] },
  }));
  assert.match(markup, /Astra/);
  assert.match(markup, /she gains 12\.30%/);
  assert.match(markup, /#F7EBA6FF/i);
  assert.doesNotMatch(markup, /&lt;color|\{PlayerName\}|Male=/);
});
