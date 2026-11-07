import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import test from "node:test";
import vm from "node:vm";
import ts from "typescript";

const require = createRequire(import.meta.url);
const tick = () => new Promise((resolve) => setImmediate(resolve));
function load(path, mocks, globals = {}) {
  const code = ts.transpileModule(
    readFileSync(new URL(path, import.meta.url), "utf8"),
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
    Error,
    AbortController,
    URLSearchParams,
    require: (id) => (id.endsWith(".css") ? {} : (mocks[id] ?? require(id))),
    ...globals,
  });
  return exports;
}
const textValue = (value, fallback = "") =>
  typeof value === "string" ? value : value?.content || fallback;
const row = (id) => ({
  canonical_key: `quest:${id}`,
  game_quest_id: id,
  name: `Story ${id}`,
  href: `/quests/${id}-story`,
  classification: { kind: "main", label: "Main story" },
});
const initialRows = Array.from({ length: 12 }, (_, i) => row(i + 1));
const summary = { total: 392, has_more: true, next_cursor: "opaque:first" };
function harness(overrides = {}) {
  let cursor = 0,
    timerId = 0;
  const slots = [],
    effects = [],
    calls = [],
    timers = new Map();
  const props = {
    characterKey: "character:1501",
    section: "quests",
    locale: "en",
    gameVersion: "3.7.0",
    initialRows,
    summary,
    playerDisplay: { name: "Rover" },
    ...overrides,
  };
  const { CharacterConnections } = load(
    "../src/components/characters/CharacterConnections.tsx",
    {
      react: {
        useState(initial) {
          const i = cursor++;
          if (!(i in slots)) slots[i] = initial;
          return [
            slots[i],
            (next) => {
              slots[i] = typeof next === "function" ? next(slots[i]) : next;
            },
          ];
        },
        useRef(initial) {
          const i = cursor++;
          return (slots[i] ??= { current: initial });
        },
        useEffect(run, deps) {
          const i = cursor++,
            previous = slots[i];
          if (!previous || deps.some((v, j) => v !== previous.deps[j])) {
            previous?.cleanup?.();
            const entry = (slots[i] = { deps, run });
            effects.push(entry);
          }
        },
      },
      "lucide-react": { ArrowRight: "i", Search: "i", X: "i" },
      "@/api/client": {
        api: (path, options) =>
          new Promise((resolve, reject) =>
            calls.push({ path, signal: options.signal, resolve, reject }),
          ),
      },
      "@/components/ui/Input": { Input: "input" },
      "@/components/ui/Button": { Button: "button" },
      "@/components/ui/Pagination": { Pagination: "Pagination" },
      "@/components/ui/Skeleton": { Skeleton: "Skeleton", SkeletonGroup: "SkeletonGroup", TextSkeleton: "TextSkeleton" },
      "@/components/ui/Pill": { Pill: "span" },
      "@/components/ui/Feedback": {
        EmptyInline: "EmptyInline",
        ErrorPanel: "ErrorPanel",
      },
      "@/components/dialogue/PlayerText": { PlayerText: "PlayerText" },
      "@/data/entities": {
        entityPath: (x) =>
          x.href || `/characters/${encodeURIComponent(x.canonical_key)}`,
      },
      "@/data/localized": { localizedText: textValue },
      "@/lib/router": { Link: "a" },
      "@/lib/routeKey": {
        questPath: (q) => q.href || `/quests/${q.game_quest_id}`,
      },
    },
    {
      setTimeout: (fn) => {
        timers.set(++timerId, fn);
        return timerId;
      },
      clearTimeout: (id) => timers.delete(id),
    },
  );
  return {
    calls,
    props,
    render() {
      cursor = 0;
      const tree = CharacterConnections(props);
      for (const entry of effects.splice(0)) entry.cleanup = entry.run();
      return tree;
    },
    debounce() {
      for (const [id, fn] of [...timers]) {
        timers.delete(id);
        fn();
      }
      return this.render();
    },
    input(value) {
      find(this.render(), (n) => n.type === "input").props.onChange({
        target: { value },
      });
      return this.render();
    },
    pagination() {
      return find(this.render(), (n) => n.props?.mode === "cursor").props;
    },
    clear() {
      find(this.render(), (n) =>
        n.props?.["aria-label"]?.startsWith("Clear "),
      ).props.onClick();
      return this.render();
    },
  };
}
function nodes(tree) {
  if (!tree || typeof tree !== "object") return [];
  return [tree, ...[tree.props?.children].flat(Infinity).flatMap(nodes)];
}
const find = (tree, predicate) => nodes(tree).find(predicate);
const questLinks = (tree) =>
  nodes(tree).filter(
    (n) => n.type === "a" && n.props.to?.startsWith("/quests/"),
  );

test("compact SSR previews render 12 of 392 with no redundant initial GET", () => {
  const h = harness();
  const tree = h.render();
  assert.equal(h.calls.length, 0);
  assert.equal(questLinks(tree).length, 12);
  assert.equal(h.pagination().hasNext, true);
  assert.equal(h.pagination().hasPrevious, false);
  assert.match(h.pagination().label, /392/);
  assert.equal(
    find(tree, (n) => n.type === "input").props["aria-label"],
    "Search story appearances",
  );
});

test("cursor paging preserves scope/version and previous restores bounded SSR preview", async () => {
  const h = harness();
  h.render();
  h.pagination().onNext();
  const pending = h.render();
  assert.equal(find(pending, (n) => n.type?.name === "CharacterConnectionsSkeleton").props.kind, "quests");
  assert.equal(questLinks(pending).length, 0);
  assert.equal(find(pending, (n) => n.type === "EmptyInline"), undefined);
  assert.equal(h.calls.length, 1);
  const url = new URL(h.calls[0].path, "http://test");
  assert.equal(url.pathname, "/characters/character%3A1501/connections");
  for (const [k, v] of Object.entries({
    section: "quests",
    limit: "12",
    locale: "en",
    cursor: "opaque:first",
    game_version: "3.7.0",
  }))
    assert.equal(url.searchParams.get(k), v);
  h.calls[0].resolve({
    results: [row(13)],
    total: 392,
    has_more: true,
    next_cursor: "opaque:second",
  });
  await tick();
  assert.equal(questLinks(h.render())[0].props.to, "/quests/13-story");
  h.pagination().onPrevious();
  h.render();
  assert.equal(h.calls.length, 1);
  assert.equal(questLinks(h.render())[0].props.to, "/quests/1-story");
});

test("search debounces, aborts superseded reads and clear rejects late responses", async () => {
  const h = harness();
  h.render();
  h.input("Moon & Sun");
  assert.equal(h.calls.length, 0);
  const pending = h.debounce();
  assert.equal(find(pending, (n) => n.type?.name === "CharacterConnectionsSkeleton").props.kind, "quests");
  assert.equal(find(pending, (n) => n.type === "EmptyInline"), undefined);
  assert.equal(h.calls.length, 1);
  assert.equal(
    new URL(h.calls[0].path, "http://test").searchParams.get("q"),
    "Moon & Sun",
  );
  h.input("Changed");
  assert.equal(h.calls[0].signal.aborted, true);
  h.debounce();
  assert.equal(h.calls.length, 2);
  h.clear();
  assert.equal(h.calls[1].signal.aborted, true);
  h.calls[0].resolve({
    results: [row(999)],
    total: 1,
    has_more: false,
    next_cursor: null,
  });
  h.calls[1].resolve({
    results: [row(998)],
    total: 1,
    has_more: false,
    next_cursor: null,
  });
  await tick();
  assert.equal(questLinks(h.render())[0].props.to, "/quests/1-story");
  assert.equal(h.pagination().hasPrevious, false);
});

test("a reverted query restarts its aborted in-flight read", () => {
  const h = harness();
  h.render();
  h.input("Moon");
  h.debounce();
  h.input("Moons");
  h.input("Moon");
  h.debounce();
  assert.equal(h.calls[0].signal.aborted, true);
  assert.equal(h.calls.length, 2);
});

test("failure exposes retry for the same selection and a successful empty state", async () => {
  const h = harness();
  h.render();
  h.input("Missing");
  h.debounce();
  assert.equal(find(h.render(), (n) => n.type?.name === "CharacterConnectionsSkeleton").props.kind, "quests");
  h.calls[0].reject(new Error("Offline"));
  await tick();
  const failed = h.render();
  const error = find(failed, (n) => n.type === "ErrorPanel");
  assert.equal(error.props.message, "Offline");
  assert.equal(find(failed, (n) => n.type?.name === "CharacterConnectionsSkeleton"), undefined);
  error.props.onRetry();
  h.render();
  assert.equal(h.calls[0].path, h.calls[1].path);
  h.calls[1].resolve({
    results: [],
    total: 0,
    has_more: false,
    next_cursor: null,
  });
  await tick();
  assert.equal(
    find(h.render(), (n) => n.type === "ErrorPanel"),
    undefined,
  );
  assert.match(
    find(h.render(), (n) => n.type === "EmptyInline").props.text,
    /No matching/,
  );
});

test("409 stale cursor returns to a freshly fetched first page, keeping the query", async () => {
  const h = harness();
  h.render();
  h.input("Moon");
  h.debounce();
  h.calls[0].resolve({
    results: [row(20)],
    total: 24,
    has_more: true,
    next_cursor: "old-generation",
  });
  await tick();
  h.pagination().onNext();
  h.render();
  h.calls[1].reject(Object.assign(new Error("Stale cursor"), { status: 409 }));
  await tick();
  h.render();
  assert.equal(h.calls.length, 3);
  const url = new URL(h.calls[2].path, "http://test");
  assert.equal(url.searchParams.get("q"), "Moon");
  assert.equal(url.searchParams.has("cursor"), false);
  h.calls[2].resolve({
    results: [row(21)],
    total: 13,
    has_more: true,
    next_cursor: "new-generation",
  });
  await tick();
  assert.equal(h.pagination().hasPrevious, false);
  assert.equal(questLinks(h.render())[0].props.to, "/quests/21-story");
  assert.equal(
    find(h.render(), (n) => n.type === "ErrorPanel"),
    undefined,
  );
});

test("legacy snapshots page/search locally without rendering every appearance", () => {
  const h = harness({
    summary: undefined,
    initialRows: Array.from({ length: 392 }, (_, i) => row(i + 1)),
  });
  assert.equal(questLinks(h.render()).length, 12);
  h.pagination().onNext();
  assert.equal(questLinks(h.render())[0].props.to, "/quests/13-story");
  h.input("Story 392");
  h.debounce();
  assert.equal(questLinks(h.render()).length, 1);
  assert.equal(questLinks(h.render())[0].props.to, "/quests/392-story");
  assert.equal(h.calls.length, 0);
});

test("shared scenes keep co-presence distinct from explicit links and cap nested previews", () => {
  const scenes = Array.from({ length: 10 }, () => ({
    flow_state: "TECHNICAL_FLOW",
    quest_refs: initialRows,
    dialogue: Array.from({ length: 80 }, () => ({
      text: { content: "Translated line preview" },
    })),
  }));
  const h = harness({
    section: "characters",
    initialRows: [
      {
        target: {
          canonical_key: "character:2",
          label: "Yangyang",
          href: "/characters/2-yangyang",
        },
        basis: "shared_flow_state_dialogue",
        shared_scenes: scenes,
      },
      {
        target: { canonical_key: "character:3", label: "Baizhi" },
        relation: "member_of",
        flow_states: [],
        shared_scenes: [],
      },
    ],
    summary: { total: 2, has_more: false, next_cursor: null },
  });
  const tree = h.render();
  assert.equal(nodes(tree).filter((n) => n.type === "PlayerText").length, 2);
  assert.equal(questLinks(tree).length, 6);
  assert(nodes(tree).some((n) => n.props?.children === "Co-presence"));
  assert(nodes(tree).some((n) => n.props?.children === "member of"));
  assert(!JSON.stringify(tree).includes("TECHNICAL_FLOW"));
  assert.equal(h.calls.length, 0);
});

test("IME composition does not dispatch partial search", () => {
  const h = harness();
  h.render();
  find(h.render(), (n) => n.type === "input").props.onCompositionStart();
  h.render();
  h.input("月");
  h.debounce();
  assert.equal(h.calls.length, 0);
  find(h.render(), (n) => n.type === "input").props.onCompositionEnd({
    currentTarget: { value: "月" },
  });
  h.render();
  h.debounce();
  assert.equal(h.calls.length, 1);
});

test("character SSR and metadata opt into compact previews and preserve a requested version", async () => {
  const calls = [];
  const { default: Page, generateMetadata } = load(
    "../src/app/characters/[key]/page.tsx",
    {
      "@/api/serverApi": {
        serverApi: async (path) => {
          calls.push(path);
          return path.includes("/profile?")
            ? { character: { canonical_key: "character:1501", name: "Rover" } }
            : { artwork: [] };
        },
      },
      "@/lib/routeKey": {
        decodeRouteKey: decodeURIComponent,
        publicPath: () => "/characters/rover",
      },
      "@/lib/canonicalRoute": { canonicalRoute: async () => {} },
      "@/components/profile/EntityProfileClient": {
        EntityProfileClient: "Profile",
      },
      "@/data/characterArtwork": { characterPortrait: () => null },
      "@/data/localized": { localizedText: textValue },
    },
    { process: { env: {} } },
  );
  const props = {
    params: Promise.resolve({ key: "character%3A1501" }),
    searchParams: Promise.resolve({ game_version: "3.7.0" }),
  };
  const tree = await Page(props);
  await generateMetadata(props);
  const profiles = calls.filter((path) => path.includes("/profile?"));
  assert.equal(profiles.length, 2);
  assert(
    profiles.every((path) =>
      path.includes("connections_limit=12&game_version=3.7.0&locale=en"),
    ),
  );
  assert.equal(tree.props.gameVersion, "3.7.0");
});


test('personalized scene previews hydrate with the server fallback before applying guest preference', () => {
  const h = harness({ section: 'characters', playerDisplay: { name: 'Astral Modulator', colorMode: 'accent', customColor: '' },
    initialRows: [{ target: { canonical_key: 'character:2', label: 'Yangyang' },
      shared_scenes: [{ dialogue: [{ text: '{PlayerName}, follow me.' }] }] }],
    summary: { total: 1, has_more: false, next_cursor: null } });
  const first = h.render();
  assert.equal(find(first, n => n.type === 'PlayerText').props.display.name, 'Rover');
  assert.equal(find(h.render(), n => n.type === 'PlayerText').props.display.name, 'Astral Modulator');
});
