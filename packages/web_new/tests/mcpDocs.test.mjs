import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

test("MCP documentation lists the tools registered by the server", () => {
  const frontend = readFileSync(
    new URL("../src/components/docs/McpDocsClient.tsx", import.meta.url),
    "utf8",
  );
  const backend = readFileSync(
    new URL("../../server/src/wuwa_story/api/routes/mcp.py", import.meta.url),
    "utf8",
  );
  const list = frontend.match(/const tools = \[([\s\S]*?)\n\];/)?.[1];
  assert.ok(list, "documented tool list exists");
  const documented = [...list.matchAll(/\[\s*"([a-z_]+)"\s*,/g)].map(
    (match) => match[1],
  );
  const registered = [...backend.matchAll(/\bTool\(\s*name="([a-z_]+)"/g)].map(
    (match) => match[1],
  );
  assert.deepEqual(documented.sort(), registered.sort());
});
