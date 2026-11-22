import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import test from "node:test";
import vm from "node:vm";
import ts from "typescript";

const require = createRequire(import.meta.url);
function load(relativePath, mocks, globals = {}) {
  const source = readFileSync(new URL(relativePath, import.meta.url), "utf8");
  const code = ts.transpileModule(source, {
    compilerOptions: {
      module: ts.ModuleKind.CommonJS,
      jsx: ts.JsxEmit.ReactJSX,
      esModuleInterop: true,
    },
  }).outputText;
  const exports = {};
  vm.runInNewContext(code, {
    exports,
    require: (id) => mocks[id] ?? require(id),
    URLSearchParams,
    ...globals,
  });
  return exports;
}

// Exercise the real component's effects without a browser or another test dependency.
function questHarness({
  missingTranscript = false,
  failRequests = false,
  mediaDeferred = false,
  failEnrichment = false,
} = {}) {
  let query = new URLSearchParams(
    "game_version=2.0.0&locale=ru&line=dialogue%3A999",
  );
  let storedLocale = "en";
  const calls = [];
  const states = [];
  const effects = [];
  const frames = [];
  const focused = [];
  let stateIndex;
  let effectIndex;
  const initialProfile = { quest: { name: { content: "SSR snapshot" } } };
  const initialTranscript = { lines: [], ...(mediaDeferred && { media_deferred: true }) };
  const props = {
    questKey: "662686-the-sacred-breeze-so-often-breathes",
    initialProfile,
    initialTranscript: missingTranscript ? null : initialTranscript,
    initialSelection: {
      locale: "ru",
      gameVersion: "2.0.0",
      citedLine: "dialogue:999",
    },
  };
  const dummy = new Proxy({}, { get: () => () => null });
  const mocks = Object.fromEntries(
    [
      "lucide-react",
      "./QuestSidebar",
      "@/components/dialogue/PlayerText",
      "@/components/story/AnalysisActions",
      "@/components/story/QuestIllustration",
      "@/components/story/QuestIllustratedScene",
      "@/components/story/QuestFork",
      "@/components/story/QuestClassification",
      "@/components/story/QuestContinuity",
      "@/components/story/QuestCutscenes",
      "@/components/story/QuestExplanation",
      "@/components/story/QuestMediaReferences",
      "@/components/story/sequenceVideo",
      "@/components/story/SequenceVideoAdmin",
      "@/components/story/SequenceRecording",
      "@/components/ui/Feedback",
      "@/components/ui/Breadcrumbs",
      "@/data/entities",
      "@/components/ui/Select",
      "../dialogue/speakerName",
      "@/components/dialogue/speakerName",
      "../story/sequenceIllustrations",
      "@/components/story/sequenceIllustrations",
    ].map((id) => [id, dummy]),
  );
  Object.assign(mocks, {
    "@/components/story/SequenceVideoContext": { SequenceVideoContext: { Provider: ({ children }) => children } },
    "./questSidebarModel": load(
      "../src/components/quest/questSidebarModel.ts",
      {},
    ),
    "@/data/gameText": {
      gameTextPlainText: (value, _display, fallback = "") =>
        typeof value === "string" ? value : value?.content || fallback,
    },
    "@/data/localized": load("../src/data/localized.ts", {}),
    "@/components/story/cutsceneAnchor": load(
      "../src/components/story/cutsceneAnchor.ts",
      {},
    ),
    "@/components/story/runtimeActivity": load(
      "../src/components/story/runtimeActivity.ts",
      {},
    ),
    "@/components/story/encounterTransitions": { encounterFlows: () => [] },
    "@/styles/quest-reader.css": {},
    "@/components/story/questReading": {
      questForks: () => [],
      illustratedRuns: () => [],
    },
    "@/components/story/sequenceReader": {
      enrichSequenceLines: (lines) => lines,
      sequenceBackgroundMusic: () => undefined,
      flowBackgroundMusic: () => undefined,
      sequenceFrameForLine: () => undefined,
    },
    "@/components/story/QuestRuntimeBlock": {
      runtimeBlockSlots: () => new Map(),
      encounterFlowOwners: () => new Map(),
      encounterOwnsMedia: () => false,
      runtimeBlockAnchor: (id) => id,
    },
    react: {
      useState(value) {
        const index = stateIndex++;
        if (!(index in states)) states[index] = value;
        return [
          states[index],
          (next) => {
            states[index] =
              typeof next === "function" ? next(states[index]) : next;
          },
        ];
      },
      useEffect(run, deps) {
        const index = effectIndex++;
        const previous = effects[index];
        if (!previous || deps.some((value, i) => value !== previous.deps[i])) {
          previous?.cleanup?.();
          effects[index] = { deps, run };
        }
      },
    },
    "@/api/client": {
      api: async (path) => {
        calls.push(path);
        if (failRequests) throw new Error("Quest unavailable");
        if (path.includes("/profile?"))
          return { quest: { name: { content: path } } };
        if (path.includes("/transcript?")) {
          if (mediaDeferred && path.includes("include_media=false")) return { lines: [], media_deferred: true };
          if (mediaDeferred && failEnrichment) throw new Error("Media unavailable");
          return { lines: [], enriched: mediaDeferred };
        }
        return null;
      },
    },
    "@/lib/router": {
      useSearchParams: () => query,
      useLocation: () => ({ key: query.toString() }),
      Link: "a",
    },
    "@/hooks/useLocale": { useLocale: () => storedLocale },
    "@/hooks/usePlayerDisplay": { usePlayerDisplay: () => ({}) },
    "@/preferences/NarrativePreferences": {
      useNarrativePreferences: () => ({}),
    },
    "@/components/story/cutscenePlacement": {
      cutsceneSlots: () => new Map(),
      visibleCutsceneEntries: (_, events) => events,
    },
    "@/components/story/QuestSequenceFragments": {
      QuestSequenceFragments: () => null,
      playableEncounterFragments: () => [],
      hasSequenceComponents: () => false,
    },
    "@/config/settings": { APP_SETTINGS: { limits: { transcript: 2000 } } },
  });
  const { QuestPageClient } = load(
    "../src/components/quest/QuestPageClient.tsx",
    mocks,
    {
      AbortController,
      window: {
        scrollY: 0,
        innerHeight: 800,
        addEventListener() {},
        removeEventListener() {},
      },
      document: {
        querySelectorAll: () => [
          {
            dataset: { dialogueKey: query.get("line") },
            scrollIntoView() {},
            focus() {
              focused.push(query.get("line"));
            },
          },
        ],
      },
      requestAnimationFrame: (callback) => {
        frames.push(callback);
        return frames.length;
      },
      cancelAnimationFrame() {},
    },
  );
  return {
    calls,
    states,
    initialProfile,
    props,
    focused,
    recover() {
      failRequests = false;
    },
    setQuery(value) {
      query = new URLSearchParams(value);
    },
    setLocale(value) {
      storedLocale = value;
    },
    async render() {
      stateIndex = effectIndex = 0;
      const tree = QuestPageClient(props);
      for (const effect of effects) {
        if (effect.run) {
          const run = effect.run;
          effect.run = null;
          effect.cleanup = run();
        }
      }
      for (const callback of frames.splice(0)) callback();
      await new Promise((resolve) => setImmediate(resolve));
      return tree;
    },
  };
}

test("failed initial quest read retries the same selection instead of remaining on an error", async () => {
  const h = questHarness({ missingTranscript: true, failRequests: true });
  await h.render();
  const errorTree = await h.render();
  const error = errorTree.props.children.props.children[0];
  assert.equal(error.props.message, "Quest unavailable");
  h.recover();
  error.props.onRetry();
  await h.render();
  const recoveredTree = await h.render();
  assert.equal(h.calls.length, 5);
  assert.equal(recoveredTree.props.children.props.className, "page-container quest-container");
});

test("matching version, locale and focus reuse SSR; changed selections fetch and returning restores SSR", async () => {
  const h = questHarness();
  await h.render();
  await h.render();
  assert.equal(h.calls.length, 0);
  assert.deepEqual(h.focused, ["dialogue:999"]);
  h.setQuery("game_version=1.4.0&locale=uk&line=dialogue%3A8");
  await h.render();
  assert.equal(h.calls.length, 4);
  for (const path of h.calls) {
    const { searchParams } = new URL(path, "http://test");
    assert.equal(searchParams.get("game_version"), "1.4.0");
    assert.equal(searchParams.get("locale"), "uk");
    assert.equal(
      searchParams.get("focus_line"),
      path.includes("/transcript?") ? "dialogue:8" : null,
    );
    if (path.includes("/transcript?")) {
      assert.equal(searchParams.get("include_raw"), "false");
      assert.equal(searchParams.get("limit"), "2000");
    }
    if (path.includes("/profile?")) assert.equal(searchParams.get("view"), "reader");
  }
  assert.notEqual(h.states[0], h.initialProfile);
  h.setQuery("game_version=2.0.0&locale=ru&line=dialogue%3A999");
  await h.render();
  assert.equal(h.calls.length, 4);
  assert.equal(h.states[0], h.initialProfile);
  h.setQuery("game_version=2.0.0&locale=ru&line=dialogue%3A1000");
  await h.render();
  assert.equal(
    h.calls.length,
    8,
    "a different focus must not reuse a truncated SSR transcript",
  );
});

test("missing SSR data retries and stored locale changes still fetch", async () => {
  const missing = questHarness({ missingTranscript: true });
  await missing.render();
  assert.equal(missing.calls.length, 3);
  const h = questHarness();
  h.setQuery("game_version=2.0.0&line=dialogue%3A999");
  h.setLocale("ru");
  await h.render();
  assert.equal(h.calls.length, 0);
  h.setLocale("zh");
  await h.render();
  assert.equal(h.calls.length, 4);
  assert.ok(h.calls.every((path) => path.includes("locale=zh")));
});

test("deferred transcript media loads separately and keeps text on failure", async () => {
  const h = questHarness({ missingTranscript: true, mediaDeferred: true, failEnrichment: true });
  await h.render();
  await h.render();
  assert.equal(h.states[1].media_deferred, true);
  assert.equal(h.states[7], "Audio and illustrations could not be loaded. Try again.");
  const transcriptCalls = h.calls.filter((path) => path.includes("/transcript?"));
  assert.equal(transcriptCalls.length, 2);
  assert.ok(transcriptCalls[0].includes("include_media=false"));
  assert.ok(!transcriptCalls[1].includes("include_media=false"));
});

test("deferred transcript is enriched after the first text response", async () => {
  const h = questHarness({ missingTranscript: true, mediaDeferred: true });
  await h.render();
  await h.render();
  assert.equal(h.states[1].enriched, true);
  assert.equal(h.states[7], "");
  assert.equal(h.calls.filter((path) => path.includes("/transcript?")).length, 2);
});

test("SSR dialogue loads its media without requesting the text again", async () => {
  const h = questHarness({ mediaDeferred: true, failEnrichment: true });
  await h.render();
  await h.render();
  assert.equal(h.states[1].media_deferred, true);
  assert.equal(h.calls.filter((path) => path.includes("/transcript?")).length, 1);
  assert.ok(!h.calls.some((path) => path.includes("include_media=false") || path.includes("/profile?")));
});

test("shared links avoid automatic prefetch and retain explicit opt-in and navigation props", () => {
  const React = { forwardRef: (render) => (props) => render(props, null) };
  const { Link, NavLink } = load("../src/lib/router.tsx", {
    react: React,
    "next/link": { __esModule: true, default: "next-link" },
    "next/navigation": { usePathname: () => "/quests/1" },
  });
  for (const Component of [Link, NavLink]) {
    const link = Component({
      to: "/quests/1?game_version=2.0.0",
      replace: true,
      scroll: false,
    });
    assert.equal(link.props.prefetch, false);
    assert.equal(link.props.href, "/quests/1?game_version=2.0.0");
    assert.equal(link.props.replace, true);
    assert.equal(link.props.scroll, false);
    assert.equal(
      Component({ to: "/quests/1", prefetch: true }).props.prefetch,
      true,
    );
  }
});
