import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import test from "node:test";
import vm from "node:vm";
import ts from "typescript";

const require = createRequire(import.meta.url);
const tick = () => new Promise((resolve) => setImmediate(resolve));
function loadData(path) {
  const exports = {};
  const code = ts.transpileModule(readFileSync(path, "utf8"), {
    compilerOptions: {
      target: ts.ScriptTarget.ES2022,
      module: ts.ModuleKind.CommonJS,
    },
  }).outputText;
  vm.runInNewContext(code, {
    exports,
    require: (id) =>
      id.startsWith(".") ? loadData(new URL(`${id}.ts`, path)) : require(id),
  });
  return exports;
}
const gameText = loadData(new URL("../src/data/gameText.ts", import.meta.url));

// Follow the existing quest/node effect harness: execute real component lifecycles,
// including late responses from transports that ignore cancellation.
function harness(component, props) {
  let locale = "en",
    cursor = 0;
  const slots = [],
    pending = [],
    calls = [],
    effects = [];
  const dummy = new Proxy({}, { get: () => () => null });
  const react = {
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
      const i = cursor++;
      const old = slots[i];
      if (!old || deps.some((v, j) => v !== old.deps[j])) {
        old?.cleanup?.();
        const effect = (slots[i] = { deps, run });
        effects.push(effect);
      }
    },
  };
  const mocks = {
    react,
    "@/components/ui/Input": { Input: "input" },
    "@/components/ui/Button": { Button: "button" },
    "@/components/ui/SearchInput": {
      SearchInput: ({
        value,
        onChange,
        onSubmit,
        onClear,
        placeholder,
        "aria-label": ariaLabel,
      }) => {
        return {
          type: "form",
          props: {
            onSubmit: (e) => onSubmit?.(e),
            children: [
              {
                type: "input",
                props: {
                  value,
                  placeholder,
                  "aria-label": ariaLabel,
                  onChange: (e) => onChange?.(e.target.value),
                },
              },
              value
                ? {
                    type: "button",
                    props: {
                      type: "button",
                      "aria-label": "Clear catalog search",
                      onClick: () => onClear?.(),
                    },
                  }
                : null,
              {
                type: "button",
                props: {
                  type: "submit",
                  "aria-label": "Search catalog",
                  size: "icon",
                  variant: "ghost",
                  className: "search-action",
                },
              },
            ].filter(Boolean),
          },
        };
      },
    },
    "@/components/ui/Pagination": { Pagination: "Pagination" },
    "@/components/ui/Pill": { Pill: "Pill" },
    "@/components/ui/Skeleton": { SkeletonGroup: "SkeletonGroup" },
    "@/components/cards/EntityCard": {
      EntityCard: "EntityCard",
      EntityCardSkeleton: "EntityCardSkeleton",
    },
    "@/components/ui/Feedback": {
      EmptyState: "EmptyState",
      ErrorPanel: "ErrorPanel",
    },
    "@/api/client": {
      api: (path, options) =>
        new Promise((resolve, reject) => {
          calls.push({ path, signal: options.signal });
          pending.push({ resolve, reject });
        }),
      apiUrl: (path) => path,
    },
    "@/hooks/useLocale": { useLocale: () => locale },
    "@/hooks/usePlayerDisplay": {
      usePlayerDisplay: () => ({ name: "Rover", gender: "ask" }),
    },
    "@/components/dialogue/PlayerText": { PlayerText: "PlayerText" },
    "@/data/gameText": gameText,
    "@/lib/router": { Link: "a" },
    "@/data/entities": {
      categories: [],
      categoryTitle: { character: "Characters", item: "Items" },
    },
    "@/data/localized": {
      localizedText: (value, fallback = "") => value || fallback,
    },
    "@/config/settings": {
      APP_SETTINGS: {
        limits: { catalogPage: 24, relatedPreview: 5 },
        presentation: { skeletonCount: 2 },
      },
    },
  };
  const source = readFileSync(
    new URL(`../src/components/${component}.tsx`, import.meta.url),
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
  vm.runInNewContext(code, {
    exports,
    AbortController,
    require: (id) =>
      mocks[id] ?? (id === "react/jsx-runtime" ? require(id) : dummy),
  });
  const renderComponent = Object.values(exports)[0];
  return {
    calls,
    pending,
    locale(value) {
      locale = value;
    },
    render() {
      cursor = 0;
      const tree = renderComponent(props);
      for (const effect of effects.splice(0)) effect.cleanup = effect.run();
      return tree;
    },
  };
}

function find(tree, predicate) {
  if (!tree || typeof tree !== "object") return;
  if (predicate(tree)) return tree;
  if (typeof tree.type === "function") {
    const rendered = tree.type(tree.props);
    const match = find(rendered, predicate);
    if (match) return match;
  }
  for (const child of [tree.props?.children].flat(Infinity)) {
    const match = find(child, predicate);
    if (match) return match;
  }
}
const button = (tree, label) =>
  find(tree, (node) => node.type === "button" && node.props.children === label);
const initialItems = [{ id: 1, name: "Original" }];

test("catalog SSR success avoids a duplicate read; paging back restores its entries", async () => {
  const h = harness("catalog/CatalogClient", {
    category: "character",
    initialItems,
    initialTotal: 30,
    initialLoaded: true,
  });
  h.render();
  assert.equal(h.calls.length, 0);
  find(h.render(), (node) => node.type === "Pagination").props.onPageChange(2);
  h.render();
  assert.match(h.calls[0].path, /offset=24/);
  h.pending[0].resolve({ results: [{ id: 25 }], total: 30 });
  await tick();
  find(h.render(), (node) => node.type === "Pagination").props.onPageChange(1);
  h.render();
  assert.equal(h.calls.length, 1);
  const card = find(h.render(), (node) => node.props?.item);
  assert.equal(
    find(h.render(), (node) => node.type === "SkeletonGroup"),
    undefined,
  );
  assert.equal(card.props.item, initialItems[0]);
});

test("failed catalog SSR loads on the client and exposes an effective retry", async () => {
  const h = harness("catalog/CatalogClient", {
    category: "item",
    initialLoaded: false,
  });
  h.render();
  assert.equal(h.calls.length, 1);
  assert.ok(
    find(
      h.render(),
      (node) =>
        node.type === "SkeletonGroup" && node.props.label === "Loading catalog",
    ),
  );
  assert.ok(find(h.render(), (node) => node.type === "EntityCardSkeleton"));
  h.pending[0].reject(new Error("Offline"));
  await tick();
  const error = find(h.render(), (node) => node.props?.message === "Offline");
  assert.ok(error);
  error.props.onRetry();
  h.render();
  assert.equal(h.calls.length, 2);
  h.pending[1].resolve({ results: [], total: 0 });
  await tick();
  assert.equal(
    find(h.render(), (node) => node.props?.message),
    undefined,
  );
  assert.ok(find(h.render(), (node) => node.type === "EmptyState"));
  assert.equal(
    find(h.render(), (node) => node.type === "SkeletonGroup"),
    undefined,
  );
});

test("clearing a pending catalog search cancels it and ignores its late response", async () => {
  const h = harness("catalog/CatalogClient", {
    category: "character",
    initialItems,
    initialLoaded: true,
  });
  h.render();
  find(h.render(), (node) => node.type === "input").props.onChange({
    target: { value: "Rover" },
  });
  find(h.render(), (node) => node.type === "form").props.onSubmit({
    preventDefault() {},
  });
  h.render();
  assert.match(h.calls[0].path, /\/search\?q=Rover/);
  find(
    h.render(),
    (node) => node.props?.["aria-label"] === "Clear catalog search",
  ).props.onClick();
  h.render();
  assert.equal(h.calls[0].signal.aborted, true);
  h.pending[0].resolve({ results: [{ id: 999 }] });
  h.pending[1].resolve({ results: initialItems, total: 1 });
  await tick();
  assert.equal(
    find(h.render(), (node) => node.props?.item)?.props.item,
    initialItems[0],
  );
});

test("profile and archive load in parallel; restoring English rejects late locale responses", async () => {
  const initialData = { character: { name: "Original" } };
  const initialArchive = { artwork: [] };
  const h = harness("profile/EntityProfileClient", {
    kind: "character",
    entityKey: "1",
    initialData,
    initialArchive,
  });
  h.render();
  assert.equal(h.calls.length, 0);
  h.locale("ru");
  h.render();
  assert.equal(h.calls.length, 2);
  assert.ok(h.calls.every((call) => call.path.endsWith("locale=ru")));
  h.locale("en");
  h.render();
  assert.ok(h.calls.every((call) => call.signal.aborted));
  h.pending[0].resolve({ character: { name: "Late" } });
  h.pending[1].resolve({ artwork: [{ url: "/late.png" }] });
  await tick();
  const tree = h.render();
  const heading = find(tree, (node) => node.type === "h1").props.children[0];
  assert.equal(heading.type, "PlayerText");
  assert.equal(heading.props.value, "Original");
  assert.equal(
    find(tree, (node) => node.props?.archive)?.props.archive,
    initialArchive,
  );
});

test("changing role filter resets the page and aborts a superseded read", async () => {
  const h = harness("catalog/CatalogClient", {
    category: "character",
    initialItems,
    initialTotal: 57,
    initialLoaded: true,
  });
  h.render();
  find(h.render(), (node) => node.type === "Pagination").props.onPageChange(2);
  h.render();
  assert.match(h.calls[0].path, /offset=24/);
  find(
    h.render(),
    (node) =>
      node.type === "Pill" && node.props.children === "Story & event roles",
  ).props.onClick();
  h.render();
  assert.equal(h.calls[0].signal.aborted, true);
  assert.match(h.calls[1].path, /offset=0.*character_kind=story/);
  h.pending[0].resolve({ results: [{ id: 999 }], total: 57 });
  h.pending[1].resolve({ results: [{ id: 50 }], total: 1 });
  await tick();
  assert.equal(find(h.render(), (node) => node.props?.item)?.props.item.id, 50);
  assert.equal(
    find(
      h.render(),
      (node) =>
        node.type === "Pill" && node.props.children === "Story & event roles",
    ).props.selected,
    true,
  );
});

test("catalog search submits from a compact named icon rather than a framed text button", () => {
  const h = harness("catalog/CatalogClient", {
    category: "character",
    initialItems,
    initialLoaded: true,
  });
  const action = find(
    h.render(),
    (node) =>
      node.type === "button" && node.props?.["aria-label"] === "Search catalog",
  );
  assert.equal(action.props.size, "icon");
  assert.equal(action.props.variant, "ghost");
  assert.equal(action.props.children, undefined);
  assert.equal(action.props.className, "search-action");
});
