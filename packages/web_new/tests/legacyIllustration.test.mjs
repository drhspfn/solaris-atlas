import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import test from "node:test";
import vm from "node:vm";
import ts from "typescript";

const require = createRequire(import.meta.url);
test("legacy WebM background loops without animation controls", () => {
  const source = readFileSync(new URL("../src/components/story/QuestIllustration.tsx", import.meta.url), "utf8");
  const code = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX } }).outputText;
  const exports = {};
  const mocks = {
    react: { useState: value => [value, () => {}], useRef: () => ({ current: null }), useEffect() {} },
    "lucide-react": {}, "../../preferences/NarrativePreferences": { useNarrativePreferences: () => ({ preferredRover: "ask" }) },
    "../ui/Select": {}, "./QuestAnimatedBackdrop": {}, "../ui/RenderedAnimation": { RenderedAnimation: "animation" },
    "../ui/animationPreview": { selectAnimationClip: () => ({ url: "https://cdn.test/a.webm" }) },
  };
  vm.runInNewContext(code, { exports, require: id => mocks[id] ?? require(id) });
  const illustration = { id: "legacy", kind: "spine", loop: false, images: [], bundle: {} };
  const tree = exports.QuestIllustration({ illustration, presentation: "background" });
  const animation = tree.props.children[0];
  assert.equal(animation.type, "animation");
  assert.equal(animation.props.compact, false);
  assert.equal(animation.props.loop, true);
  assert.equal(animation.props.controls, undefined);
});
