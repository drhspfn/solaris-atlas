/** Only exported accessory anchors with their authored manifest can be controlled. */
export function declaredAccessories(scene, previous = {}) {
  const candidates = new Map();
  scene.traverse(node => {
    const data = node.userData || {};
    if (typeof data.accessory_name !== 'string' || !data.accessory_name ||
        typeof data.default_visible !== 'boolean' || typeof data.attachment_manifest !== 'string') return;
    let manifest;
    try { manifest = JSON.parse(data.attachment_manifest); } catch { return; }
    if (!manifest || manifest.name !== data.accessory_name || typeof manifest.visible !== 'boolean') return;
    if (manifest.kind === 'bone_visibility' && (!node.isBone || typeof manifest.bone !== 'string' || !manifest.bone)) return;
    const list = candidates.get(data.accessory_name) || [];
    list.push({ node, bone: manifest.kind === 'bone_visibility' }); candidates.set(data.accessory_name, list);
  });
  const nodes = new Map();
  const bones = new Map();
  const visibility = new Map();
  const options = [];
  for (const [name, matches] of candidates) {
    if (matches.length !== 1) continue;
    const { node, bone } = matches[0];
    const visible = Object.hasOwn(previous, name) ? previous[name] : node.userData.default_visible;
    visibility.set(name, visible);
    if (bone) bones.set(name, node.scale.clone());
    else node.visible = visible;
    nodes.set(name, node);
    options.push({ name, visible });
  }
  function restore() {
    for (const [name, scale] of bones) if (!visibility.get(name)) nodes.get(name).scale.copy(scale);
  }
  function apply() {
    for (const name of bones.keys()) if (!visibility.get(name)) nodes.get(name).scale.set(0, 0, 0);
  }
  function set(name, visible) {
    const node = nodes.get(name);
    if (!node || typeof visible !== 'boolean') return false;
    visibility.set(name, visible);
    if (bones.has(name)) {
      if (visible) node.scale.copy(bones.get(name));
      else node.scale.set(0, 0, 0);
    } else node.visible = visible;
    return true;
  }
  apply();
  return { nodes, options, set, restore, apply };
}
