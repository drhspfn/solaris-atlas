import test from "node:test";
import assert from "node:assert/strict";

import {
  extractMaterialVariants,
  applyMaterialVariant,
} from "../src/components/models/engine/material-variants.js";

test("extractMaterialVariants and applyMaterialVariant switch mesh materials", async () => {
  const mat0 = { name: "Mat01" };
  const mat1 = { name: "Mat02" };
  const mesh = {
    isMesh: true,
    material: mat0,
    userData: {
      gltfExtensions: {
        KHR_materials_variants: {
          mappings: [
            { material: 0, variants: [0] },
            { material: 1, variants: [1] },
          ],
        },
      },
    },
  };
  const loaded = {
    scene: {
      traverse(fn) {
        fn(mesh);
      },
    },
    parser: {
      json: {
        extensions: {
          KHR_materials_variants: {
            variants: [{ name: "Form 1" }, { name: "Form 2" }],
          },
        },
      },
      async getDependency(kind, index) {
        assert.equal(kind, "material");
        return index === 1 ? mat1 : mat0;
      },
    },
  };

  assert.deepEqual(extractMaterialVariants(loaded), [
    { value: "Form 1", label: "Form 1" },
    { value: "Form 2", label: "Form 2" },
  ]);

  let appliedCount = 0;
  const ok = await applyMaterialVariant(loaded, "Form 2", () => {
    appliedCount += 1;
  });
  assert.equal(ok, true);
  assert.equal(mesh.material, mat1);
  assert.equal(appliedCount, 1);
});
