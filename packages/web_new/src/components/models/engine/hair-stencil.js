import * as THREE from 'three';

const basename = path => typeof path === 'string' ? path.split('/').pop().split('.')[0] : '';

// Game masks are painted on the face and eye UVs, not the hair UVs. Write
// them in screen space before drawing the authored hair pass in two parts.
export function installHairStencil(root) {
  let payload;
  root.traverse(node => {
    if (payload || typeof node.userData?.solaris_hair_stencil !== 'string') return;
    try { payload = JSON.parse(node.userData.solaris_hair_stencil); } catch { /* Ignore malformed extras. */ }
  });
  if (payload?.format !== 'solaris-hair-stencil-v1' || !Array.isArray(payload.masks)
      || !Array.isArray(payload.readers) || !payload.readers.length) return false;

  const meshes = [];
  root.traverse(node => { if (node.isSkinnedMesh && !Array.isArray(node.material)) meshes.push(node); });
  const find = ref => {
    const name = basename(ref);
    const matches = meshes.filter(mesh => mesh.material?.name === name);
    return name && matches.length === 1 ? matches[0] : null;
  };
  const writers = payload.masks.map(mask => ({ mask, mesh: find(mask.target_material) }))
    .filter(({ mask, mesh }) => mesh && mask.mime_type === 'image/png'
      && typeof mask.data_base64 === 'string' && /^[A-Za-z0-9+/=]+$/.test(mask.data_base64));
  const readers = [...new Set(payload.readers.map(row => find(row.material)).filter(Boolean))];
  if (!writers.length || !readers.length) return false;

  for (const { mask, mesh } of writers) {
    const texture = new THREE.TextureLoader().load(`data:image/png;base64,${mask.data_base64}`);
    texture.flipY = false;
    const writer = mesh.clone(false);
    writer.morphTargetInfluences = mesh.morphTargetInfluences;
    writer.material = new THREE.MeshBasicMaterial({ alphaMap: texture, alphaTest: 0.5,
      side: THREE.DoubleSide, colorWrite: false, depthWrite: false, depthTest: false,
      stencilWrite: true, stencilRef: 1, stencilFunc: THREE.AlwaysStencilFunc,
      stencilZPass: THREE.ReplaceStencilOp });
    writer.renderOrder = -1;
    mesh.parent.add(writer);
  }
  for (const mesh of readers) {
    Object.assign(mesh.material, { stencilWrite: true, stencilRef: 1,
      stencilFunc: THREE.NotEqualStencilFunc, stencilWriteMask: 0 });
    const overlay = mesh.clone(false);
    overlay.morphTargetInfluences = mesh.morphTargetInfluences;
    overlay.material = mesh.material.clone();
    Object.assign(overlay.material, { stencilFunc: THREE.EqualStencilFunc,
      transparent: true, opacity: 0.38, depthWrite: false });
    overlay.renderOrder = 1;
    mesh.parent.add(overlay);
  }
  return true;
}
