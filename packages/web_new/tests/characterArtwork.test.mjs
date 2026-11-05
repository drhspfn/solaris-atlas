import assert from "node:assert/strict";
import test from "node:test";
import { characterPortrait } from "../src/data/characterArtwork.ts";

test("borrowed Yangyang portraits never replace a personal NPC image", () => {
  assert.equal(
    characterPortrait([
      { kind: "RoleHeadIconLarge", url: "yangyang", scope: "shared_resource" },
      { kind: "RoleHeadIcon", url: "npc", scope: "character" },
    ]),
    "npc",
  );
  assert.equal(
    characterPortrait([
      { kind: "RoleHeadIconLarge", url: "yangyang", scope: "shared_resource" },
    ]),
    null,
  );
});
