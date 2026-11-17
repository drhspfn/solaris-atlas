import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";
import ts from "typescript";
import { requestCredentials } from "../src/api/requestPolicy.ts";

test("binary model chunks preserve bytes, credentials and CSRF while JSON requests stay unchanged", async () => {
  const calls = [], exports = {};
  const fetch = async (url, options) => {
    if (url.endsWith("/auth/csrf")) return { ok: true, json: async () => ({ csrf_token: "csrf" }) };
    calls.push({ url, options });
    return { ok: true, json: async () => ({ received: true }) };
  };
  const mocks = {
    "../config/settings": { APP_SETTINGS: { api: { defaultBase: "/api" } } },
    "./requestPolicy": { requestCredentials: () => "include" },
    "./resolveApiUrl": { resolveApiUrl() {} },
    "./retryableFetch": { retryableFetch: fetch },
  };
  const code = ts.transpileModule(readFileSync(new URL("../src/api/client.ts", import.meta.url), "utf8"),
    { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText;
  vm.runInNewContext(code, { exports, require: id => mocks[id], process: { env: {} }, window: {},
    Headers, Blob, fetch, Date, Map, AbortSignal });
  const chunk = new Blob([new Uint8Array([0, 255, 1])]);
  await exports.api("/admin/character-models/uploads/1/parts/0", { method: "PUT", body: chunk });
  assert.equal(calls[0].options.body, chunk);
  assert.equal(calls[0].options.headers.get("Content-Type"), "application/octet-stream");
  assert.equal(calls[0].options.headers.get("X-CSRF-Token"), "csrf");
  assert.equal(calls[0].options.credentials, "include");
  await exports.api("/admin/character-models", { method: "POST", body: { character_key: "character:1" } });
  assert.equal(calls[1].options.body, '{"character_key":"character:1"}');
  assert.equal(calls[1].options.headers.get("Content-Type"), "application/json");
});

test("sequence recording writes send session credentials and CSRF for save and delete", async () => {
  const calls = [], exports = {};
  const fetch = async (url, options) => {
    if (url.endsWith("/auth/csrf")) {
      assert.equal(options.credentials, "include");
      return { ok: true, json: async () => ({ csrf_token: "csrf" }) };
    }
    calls.push({ url, options });
    return { ok: true, json: async () => ({}) };
  };
  const mocks = {
    "../config/settings": { APP_SETTINGS: { api: { defaultBase: "/api" } } },
    "./requestPolicy": { requestCredentials },
    "./resolveApiUrl": { resolveApiUrl() {} },
    "./retryableFetch": { retryableFetch: fetch },
  };
  const code = ts.transpileModule(readFileSync(new URL("../src/api/client.ts", import.meta.url), "utf8"),
    { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText;
  vm.runInNewContext(code, { exports, require: id => mocks[id], process: { env: {} }, window: {},
    Headers, Blob, fetch, Date, Map, AbortSignal });
  const path = "/admin/quests/1/media/occurrence/youtube?game_version=3.7";
  await exports.api(path, { method: "PUT", body: { url: "https://youtu.be/abcdefghijk", start_seconds: 5 } });
  await exports.api(path, { method: "DELETE" });
  for (const call of calls) {
    assert.equal(call.options.credentials, "include");
    assert.equal(call.options.headers.get("X-CSRF-Token"), "csrf");
  }
  assert.equal(calls[0].options.method, "PUT");
  assert.equal(calls[1].options.method, "DELETE");
});
