import { PropertyBinding } from 'three';

export function resourceUrl(value, base) {
  const url = new URL(value, base);
  if (url.origin !== new URL(base).origin || !['http:', 'https:'].includes(url.protocol)) {
    throw new Error('Model resources must be served from the viewer origin.');
  }
  return url.href;
}

export function animationLoadLabel(bytes) {
  const size = /^\d+$/.test(bytes ?? '') ? Number(bytes) : 0;
  return Number.isSafeInteger(size) && size > 0
    ? `Load animations (~${(size / 1048576).toFixed(1)} MiB)` : 'Load animations';
}

// Loader-generated names can differ when meshes claim the same name as a bone.
// Match authored node names, then bind directly to the existing preview bones.
export function bindAnimationClips(pack, preview) {
  if (!pack.animations.length) throw new Error('Animation pack contains no clips.');
  const targets = new Map();
  preview.scene.traverse(node => {
    if (!node.isBone) return;
    const index = preview.parser.associations.get(node)?.nodes;
    const name = preview.parser.json.nodes[index]?.name;
    if (name) targets.set(name, targets.has(name) ? null : node);
  });
  return pack.animations.map(clip => {
    const bound = clip.clone();
    for (const track of bound.tracks) {
      const path = PropertyBinding.parseTrackName(track.name);
      const source = PropertyBinding.findNode(pack.scene, path.nodeName);
      const index = pack.parser.associations.get(source)?.nodes;
      const name = pack.parser.json.nodes[index]?.name;
      const target = targets.get(name);
      if (!target || path.objectName || !['position', 'quaternion', 'scale'].includes(path.propertyName)) {
        throw new Error(`Animation target is missing, ambiguous or unsupported: ${name || track.name}`);
      }
      track.name = `${target.uuid}.${path.propertyName}`;
    }
    return bound;
  });
}

// Construction is inert: only an explicit call may start a request. Failed
// attempts clear the guard so a later click can retry; concurrent clicks share it.
export function lazyAnimationPack(url, load, apply) {
  let request;
  return () => {
    if (!request) {
      request = Promise.resolve().then(() => load(url)).then(apply).catch(error => {
        request = undefined;
        throw error;
      });
    }
    return request;
  };
}
