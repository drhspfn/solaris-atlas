import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import test from "node:test";
import vm from "node:vm";
import ts from "typescript";
const require = createRequire(import.meta.url);
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
