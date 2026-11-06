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
    const list = candidates.get(data.accessory_name) || [];
    list.push(node); candidates.set(data.accessory_name, list);
  });
  const nodes = new Map();
  const options = [];
  for (const [name, matches] of candidates) {
    if (matches.length !== 1) continue;
    const node = matches[0];
    node.visible = Object.hasOwn(previous, name) ? previous[name] : node.userData.default_visible;
    nodes.set(name, node);
    options.push({ name, visible: node.visible });
  }
  return { nodes, options };
}
