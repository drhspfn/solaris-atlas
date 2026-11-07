import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import vm from "node:vm";
import ts from "typescript";
const require = createRequire(import.meta.url),
  exports = {};
vm.runInNewContext(
  ts.transpileModule(
    readFileSync(
      new URL("../src/components/ui/Skeleton.tsx", import.meta.url),
      "utf8",
    ),
    {
      compilerOptions: {
        module: ts.ModuleKind.CommonJS,
        jsx: ts.JsxEmit.ReactJSX,
      },
    },
  ).outputText,
  { exports, require },
);
export const skeletonComponents = exports;
