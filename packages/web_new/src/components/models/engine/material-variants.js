/** Runtime KHR_materials_variants extraction and switching for Three.js GLTF scenes. */

export function extractMaterialVariants(loaded) {
  const variants =
    loaded?.parser?.json?.extensions?.KHR_materials_variants?.variants;
  if (!Array.isArray(variants) || variants.length <= 1) return [];
  return variants.map((item, index) => {
    const name =
      typeof item?.name === "string" && item.name.trim()
        ? item.name.trim()
        : `Form ${index + 1}`;
    return { value: name, label: name };
  });
}

function meshMappings(node, parser) {
  const direct =
    node.userData?.gltfExtensions?.KHR_materials_variants?.mappings;
  if (Array.isArray(direct)) return direct;
  const assoc = parser?.associations?.get(node);
  if (
    assoc &&
    typeof assoc.meshes === "number" &&
    typeof assoc.primitives === "number"
  ) {
    const prim =
      parser.json?.meshes?.[assoc.meshes]?.primitives?.[assoc.primitives];
    const mappings = prim?.extensions?.KHR_materials_variants?.mappings;
    if (Array.isArray(mappings)) return mappings;
  }
  return null;
}

export async function applyMaterialVariant(
  loaded,
  variantName,
  onMaterialApplied,
) {
  if (!loaded?.scene || !loaded?.parser) return false;
  const variants =
    loaded.parser.json?.extensions?.KHR_materials_variants?.variants;
  if (!Array.isArray(variants)) return false;
  const variantIndex = variants.findIndex((item, index) => {
    const name =
      typeof item?.name === "string" && item.name.trim()
        ? item.name.trim()
        : `Form ${index + 1}`;
    return name === variantName;
  });
  if (variantIndex < 0) return false;

  const targets = [];
  loaded.scene.traverse((node) => {
    if (!node.isMesh) return;
    const mappings = meshMappings(node, loaded.parser);
    if (!mappings) return;
    const hit = mappings.find(
      (entry) =>
        Array.isArray(entry.variants) && entry.variants.includes(variantIndex),
    );
    if (hit && typeof hit.material === "number") {
      targets.push([node, hit.material]);
    }
  });
  if (!targets.length) return false;

  const uniqueMaterials = [...new Set(targets.map(([, matIndex]) => matIndex))];
  const resolved = new Map(
    await Promise.all(
      uniqueMaterials.map(async (matIndex) => [
        matIndex,
        await loaded.parser.getDependency("material", matIndex),
      ]),
    ),
  );
  for (const [node, matIndex] of targets) {
    const nextMaterial = resolved.get(matIndex);
    if (nextMaterial && node.material !== nextMaterial) {
      node.material = nextMaterial;
      onMaterialApplied?.(node);
    }
  }
  return true;
}
