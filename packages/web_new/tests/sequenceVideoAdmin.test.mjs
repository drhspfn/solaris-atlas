import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";
import ts from "typescript";

const event = {
  kind: "sequence", occurrence_id: "a".repeat(64), reference: "asset:ue:scene",
  flow_state: "Flow1", action_index: 3, source: { file: "flow.json", raw_path: "$.Actions[3]" },
};

function harness({ role = "admin", initial = event, response, fail } = {}) {
  let user = role ? { role } : null;
  let states = [], refs = [], stateIndex = 0, refIndex = 0, refreshes = 0;
  const requests = [], changes = [];
  const jsx = (type, props) => ({ type, props: props ?? {} });
  class ApiError extends Error { constructor(message, status) { super(message); this.status = status; } }
  const api = async (path, options) => {
    requests.push({ path, options });
    if (fail) throw new ApiError("Denied", fail);
    return response ?? { video_id: "abcdefghijk", start_seconds: 5, end_seconds: 12 };
  };
  const mocks = {
    react: {
      useState: (initialValue) => {
        const index = stateIndex++;
        if (!(index in states)) states[index] = initialValue;
        return [states[index], (value) => { states[index] = value; }];
      },
      useRef: (value) => {
        const index = refIndex++;
        if (!(index in refs)) refs[index] = { current: value };
        return refs[index];
      },
      useEffect: () => {},
      useId: () => "form-id",
    },
    "react/jsx-runtime": { jsx, jsxs: jsx },
    "@/api/client": { api, ApiError },
    "@/auth/AuthProvider": { useAuth: () => ({ user, refresh: async () => { refreshes++; } }) },
    "@/components/ui/Button": { Button: "button" },
    "./SequenceVideoAdmin.module.css": { default: {} },
  };
  const source = readFileSync(new URL("../src/components/story/SequenceVideoAdmin.tsx", import.meta.url), "utf8");
  const code = ts.transpileModule(source, { compilerOptions: {
    module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true,
  } }).outputText;
  const exports = {};
  vm.runInNewContext(code, { exports, require: (name) => mocks[name], AbortController, URLSearchParams, Error });
  const props = { questKey: "123-quest", version: "3.7", event: initial,
    onChange: (...args) => changes.push(args) };
  return {
    admin: (manifest) => exports.SequenceVideoAdmin({ questKey: props.questKey, manifest, onChange: props.onChange }),
    render: () => { stateIndex = 0; refIndex = 0; return exports.SequenceVideoForm(props); },
    setRole: (role) => { user = role ? { role } : null; },
    requests, changes, get refreshes() { return refreshes; },
  };
}

function all(node) {
  if (!node || typeof node !== "object") return [];
  const children = node.props?.children;
  return [node, ...[children].flat(Infinity).flatMap(all)];
}
const input = (tree, suffix) => all(tree).find((node) => node.type === "input" && node.props.id.endsWith(suffix));
const button = (tree, label) => all(tree).find((node) => node.type === "button" && node.props.children === label);
const flush = () => new Promise((resolve) => setImmediate(resolve));

test("only an administrator sees editable occurrences for a resolved snapshot", () => {
  const h = harness({ role: "user" });
  const manifest = { game_version: "3.7", events: [event] };
  assert.equal(h.admin(manifest), null);
  h.setRole(null);
  assert.equal(h.admin(manifest), null);
  h.setRole("admin");
  assert.equal(h.admin({ events: [event] }), null);
  assert.ok(h.admin(manifest));
  const ambiguous = all(h.admin({ game_version: "3.7", events: [{ ...event, occurrence_id: undefined }] }));
  assert.ok(ambiguous.some((node) => typeof node.props.children === "string"
    && node.props.children.includes("cannot be edited")));
});

test("save sends the exact occurrence and updates the displayed recording", async () => {
  const h = harness();
  let tree = h.render();
  input(tree, "-url").props.onChange({ target: { value: "https://youtu.be/abcdefghijk" } });
  input(tree, "-start").props.onChange({ target: { value: "5" } });
  input(tree, "-end").props.onChange({ target: { value: "12" } });
  tree = h.render();
  tree.props.onSubmit({ preventDefault() {} });
  await flush();
  assert.equal(h.requests.length, 1);
  assert.match(h.requests[0].path, /\/admin\/quests\/123-quest\/media\/a{64}\/youtube\?game_version=3.7/);
  assert.equal(h.requests[0].options.method, "PUT");
  assert.deepEqual(JSON.parse(JSON.stringify(h.requests[0].options.body)),
    { url: "https://youtu.be/abcdefghijk", start_seconds: 5, end_seconds: 12 });
  assert.deepEqual(JSON.parse(JSON.stringify(h.changes[0])), ["a".repeat(64), "3.7",
    { video_id: "abcdefghijk", start_seconds: 5, end_seconds: 12 }]);
  assert.ok(all(h.render()).some((node) => node.props.role === "status" && node.props.children === "Recording saved."));
});

test("invalid ranges do not submit; delete uses the protected client", async () => {
  const h = harness({ initial: { ...event, youtube_fallback: {
    video_id: "abcdefghijk", start_seconds: 5, end_seconds: 12,
  } } });
  let tree = h.render();
  input(tree, "-end").props.onChange({ target: { value: "4" } });
  tree = h.render();
  tree.props.onSubmit({ preventDefault() {} });
  assert.equal(h.requests.length, 0);
  assert.ok(all(h.render()).some((node) => node.props.role === "alert"));
  button(h.render(), "Remove recording").props.onClick();
  await flush();
  assert.equal(h.requests[0].options.method, "DELETE");
  assert.equal(h.requests[0].options.body, undefined);
  assert.equal(h.changes[0][2], null);
});

test("expired administrator session is reported and refreshed", async () => {
  const h = harness({ fail: 403, initial: { ...event, youtube_fallback: {
    video_id: "abcdefghijk", start_seconds: 5, end_seconds: 12,
  } } });
  h.render().props.onSubmit({ preventDefault() {} });
  await flush();
  assert.equal(h.refreshes, 1);
  assert.equal(h.changes.length, 0);
  assert.ok(all(h.render()).some((node) => node.props.role === "alert" && node.props.children === "Denied"));
});
