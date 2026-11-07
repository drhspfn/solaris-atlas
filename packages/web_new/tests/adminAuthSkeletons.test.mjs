import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import test from "node:test";
import vm from "node:vm";
import ts from "typescript";
const require = createRequire(import.meta.url);
const tick = () => new Promise((resolve) => setImmediate(resolve));
const nodes = (tree) =>
  !tree || typeof tree !== "object"
    ? []
    : [tree, ...[tree.props?.children].flat(Infinity).flatMap(nodes)];
function harness(path, name, extra = {}) {
  let cursor = 0;
  const slots = [],
    effects = [],
    calls = [];
  const react = {
    useState(initial) {
      const i = cursor++;
      if (!(i in slots))
        slots[i] = typeof initial === "function" ? initial() : initial;
      return [
        slots[i],
        (value) => {
          slots[i] = typeof value === "function" ? value(slots[i]) : value;
        },
      ];
    },
    useEffect(fn, deps) {
      const i = cursor++,
        previous = slots[i];
      if (!previous || deps.some((value, j) => value !== previous.deps[j])) {
        previous?.cleanup?.();
        const effect = (slots[i] = { fn, deps });
        effects.push(effect);
      }
    },
    useRef(value) {
      const i = cursor++;
      return (slots[i] ??= { current: value });
    },
  };
  const mocks = {
    react,
    "react/jsx-runtime": require("react/jsx-runtime"),
    "../../api/client": {
      api: (path, options) =>
        new Promise((resolve, reject) =>
          calls.push({ path, options, resolve, reject }),
        ),
    },
    "@/components/ui/Skeleton": {
      Skeleton: "skeleton",
      SkeletonGroup: "skeleton-group",
      TextSkeleton: "text-skeleton",
    },
    ...extra,
  };
  const code = ts.transpileModule(
    readFileSync(
      new URL(`../src/components/${path}.tsx`, import.meta.url),
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
  const exports = {};
  vm.runInNewContext(code, {
    exports,
    AbortController,
    URLSearchParams,
    Error,
    window: { setInterval: () => 1, clearInterval() {} },
    document: { visibilityState: "visible" },
    require: (id) => mocks[id] ?? new Proxy({}, { get: () => () => null }),
  });
  return {
    calls,
    render() {
      cursor = 0;
      const tree = exports[name]({});
      for (const effect of effects.splice(0)) effect.cleanup = effect.fn();
      return tree;
    },
  };
}
test("processing activity shows table skeletons then empty results", async () => {
  const h = harness("admin/ProcessingActivity", "ProcessingActivity");
  let tree = h.render();
  assert.equal(
    nodes(tree).filter((node) => node.type?.name === "ProcessingTableSkeleton")
      .length,
    2,
  );
  assert.ok(
    nodes(tree).some((node) => node.props?.children === "Loading task history"),
  );
  h.calls[0].resolve({ tasks: [], running: [], next_before: null });
  h.calls[1].resolve({ available: true, error: null, queues: [] });
  await tick();
  tree = h.render();
  assert.equal(
    nodes(tree).some((node) => node.type?.name === "ProcessingTableSkeleton"),
    false,
  );
  assert.ok(
    nodes(tree).some(
      (node) => node.props?.children === "No tasks match this status.",
    ),
  );
});
test("processing failure removes pending skeletons and retry starts fresh requests", async () => {
  const h = harness("admin/ProcessingActivity", "ProcessingActivity");
  h.render();
  h.calls[0].reject(new Error("Offline"));
  h.calls[1].resolve({ available: true, error: null, queues: [] });
  await tick();
  const tree = h.render();
  assert.equal(
    nodes(tree).some((node) => node.type?.name === "ProcessingTableSkeleton"),
    false,
  );
  assert.ok(nodes(tree).some((node) => node.props?.role === "alert"));
  nodes(tree)
    .find((node) => node.type === "button" && node.props.children === "Retry")
    .props.onClick();
  h.render();
  assert.equal(h.calls.length, 4);
  assert.equal(h.calls[0].options.signal.aborted, true);
});
test("account skeleton yields to the signed-out state", () => {
  let loading = true;
  const h = harness("auth/AuthPagesClient", "AccountPageClient", {
    "@/auth/AuthProvider": {
      useAuth: () => ({ loading, user: null, refresh() {}, logout() {} }),
    },
    "@/lib/router": {
      Link: "a",
      useNavigate: () => () => {},
      useLocation: () => ({ search: "" }),
    },
  });
  assert.equal(h.render().type.name, "AccountSkeleton");
  loading = false;
  assert.equal(h.render().props.title, "Sign in required.");
});
test("processing activity replaces pending rows with loaded tasks", async () => {
  const h = harness("admin/ProcessingActivity", "ProcessingActivity");
  h.render();
  h.calls[0].resolve({
    tasks: [
      {
        id: 42,
        processor: "import",
        status: "completed",
        error: null,
        request: {},
        result: {},
      },
    ],
    running: [],
    next_before: null,
  });
  h.calls[1].resolve({ available: true, error: null, queues: [] });
  await tick();
  const tree = h.render();
  const taskRows = nodes(tree).find(
    (node) => node.type?.name === "TaskRows" && node.props.rows.length === 1,
  );
  assert.equal(taskRows.props.rows[0].id, 42);
  assert.equal(
    nodes(tree).some((node) => node.props?.children === "Loading task history"),
    false,
  );
});
