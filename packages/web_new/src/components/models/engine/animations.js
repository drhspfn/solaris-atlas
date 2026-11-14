import { PropertyBinding } from "three";

/** One character, a handful of parts: stale bind-pose bounds must not hide limbs/eyes. */
export function prepareAnimatedMeshes(model) {
  model.traverse(object => {
    if (object.isSkinnedMesh) object.frustumCulled = false;
  });
}

export function resourceUrl(value, base) {
  const url = new URL(value, base);
  if (
    url.username ||
    url.password ||
    !["http:", "https:"].includes(url.protocol) ||
    url.origin !== new URL(base).origin ||
    (new URL(base).protocol === "https:" && url.protocol !== "https:")
  ) {
    throw new Error(
      "Model resources must use the viewer origin without credentials.",
    );
  }
  return url.href;
}

export function animationLoadLabel(bytes) {
  const size = /^\d+$/.test(bytes ?? "") ? Number(bytes) : 0;
  return Number.isSafeInteger(size) && size > 0
    ? `Load animations (~${(size / 1048576).toFixed(1)} MiB)`
    : "Load animations";
}

// Same ordered table as the worker catalog; parity is checked in worker tests.
export const ANIMATION_CATALOG_RULES = [
  ["^[DM][0-9]+_Cam", "Technical", "Cinematics", "Camera"],
  ["^Ui_", "Technical", "Presentation", "UI"],
  [
    "^(?:Cos_Pose|Team_Stand)(?:[0-9]|_|$)",
    "Idle & poses",
    "Poses",
    "Presentation",
  ],
  ["^AirAttack(?:[0-9]|_|$)", "Combat", "Attacks", "Aerial"],
  ["^Attack(?:L)?(?:[0-9]|_|$)", "Combat", "Attacks", "Ground"],
  ["^AirSkill(?:[0-9]|_|$)", "Combat", "Skills", "Aerial"],
  ["^SkillQte(?:[0-9]|_|$)", "Combat", "Skills", "QTE"],
  ["^Skill(?:[0-9]|_|$)", "Combat", "Skills", "Ground"],
  ["^Burst(?:[0-9]|_|$)", "Combat", "Skills", "Burst"],
  ["^Execute(?:[0-9]|_|$)", "Combat", "Attacks", "Execution"],
  ["^Death(?:[0-9]|_|$)", "Combat", "Reactions", "Death"],
  ["^(?:Hit|Hurt)(?:[0-9]|_|$)", "Combat", "Reactions", "Hit"],
  ["^Dodge(?:[0-9]|_|$)", "Combat", "Evasion", "Dodge"],
  ["^Walk(?:[0-9]|_|$)", "Movement", "Locomotion", "Walk"],
  ["^Run(?:[0-9]|_|$)", "Movement", "Locomotion", "Run"],
  ["^Sprint(?:[0-9]|_|$)", "Movement", "Locomotion", "Sprint"],
  ["^Swim(?:[0-9]|_|$)", "Movement", "Locomotion", "Swim"],
  ["^Move(?:[0-9]|_|$)", "Movement", "Locomotion", "Move"],
  ["^Turn(?:[0-9]|_|$)", "Movement", "Locomotion", "Turn"],
  ["^Jump(?:[0-9]|_|$)", "Traversal", "Jumping", "Jump"],
  ["^(?:Fall|Land)(?:[0-9]|_|$)", "Traversal", "Jumping", "Fall & land"],
  ["^Climb(?:[0-9]|_|$)", "Traversal", "Climbing", "Climb"],
  ["^(?:Glide|Fly)(?:[0-9]|_|$)", "Traversal", "Flight", "Glide"],
  ["^FixHook(?:[0-9]|_|$)", "Traversal", "Grappling", "Hook"],
  ["^(?:Stand|Idle)(?:[0-9]|_|$|Change)", "Idle & poses", "Idle", "Standing"],
  ["^Sit(?:[0-9]|_|$|Promise|Push|Talk)", "Interactions", "Posture", "Sitting"],
  ["^Sleeping(?:[0-9]|_|$)", "Idle & poses", "Rest", "Sleeping"],
  ["^Squat(?:[0-9]|_|$)", "Interactions", "Posture", "Squatting"],
  ["^(?:Playpiano|Tanqin)(?:[0-9]|_|$)", "Interactions", "Activities", "Music"],
  ["^Drink(?:[0-9]|_|$)", "Interactions", "Activities", "Drinking"],
  [
    "^(?:Talk|Greetings)(?:[0-9]|_|$)",
    "Interactions",
    "Social",
    "Conversation",
  ],
  [
    "^(?:Awareness|Elevate|Expect|Fool|Promise|Shy|Touch)(?:[0-9]|_|$)",
    "Interactions",
    "Social",
    "Gestures",
  ],
];

export function classifyAnimation(name) {
  const label =
    typeof name === "string" && name.trim() ? name : "Unnamed animation";
  const family = label.replace(/^AM_/, "").replace(/^Rogue_/, "");
  for (const [pattern, category, group, subgroup] of ANIMATION_CATALOG_RULES) {
    if (new RegExp(pattern).test(family))
      return { category, group, subgroup, label };
  }
  return { category: "Other", group: "Other", subgroup: "Other", label };
}

// Also usable for embedded clips: attachAnimationCatalog(clip, gltf, index).
// Metadata is indexed by glTF animation order, never by display name.
export function attachAnimationCatalog(clip, pack, index) {
  const imported =
    pack?.parser?.json?.animations?.[index]?.extras?.solaris_atlas;
  const keys = ["category", "group", "subgroup", "label"];
  const valid =
    imported &&
    !Array.isArray(imported) &&
    keys.every(
      (key) => typeof imported[key] === "string" && imported[key].trim(),
    );
  clip.userData = {
    ...clip.userData,
    sourcePackage: typeof imported?.source === "string" ? imported.source : undefined,
    catalog: valid
      ? Object.fromEntries(keys.map((key) => [key, imported[key]]))
      : classifyAnimation(clip.name),
  };
  return clip;
}

// Loader-generated names can differ when meshes claim the same name as a bone.
// Match authored node names, then bind directly to the existing preview bones.
export function bindAnimationClips(pack, preview, whitelist) {
  if (!pack.animations.length)
    throw new Error("Animation pack contains no clips.");
  const targets = new Map();
  preview.scene.traverse((node) => {
    if (!node.isBone) return;
    const index = preview.parser.associations.get(node)?.nodes;
    const name = preview.parser.json.nodes[index]?.name;
    if (name) targets.set(name, targets.has(name) ? null : node);
  });
  return pack.animations.flatMap((clip, clipIndex) => {
    if (whitelist && !whitelist.has(clip.name)) return [];
    const bound = clip.clone();
    bound.userData = { ...clip.userData };
    attachAnimationCatalog(bound, pack, clipIndex);
    for (const track of bound.tracks) {
      const path = PropertyBinding.parseTrackName(track.name);
      const source = PropertyBinding.findNode(pack.scene, path.nodeName);
      const index = pack.parser.associations.get(source)?.nodes;
      const name = pack.parser.json.nodes[index]?.name;
      const target = targets.get(name);
      if (
        !target ||
        path.objectName ||
        !["position", "quaternion", "scale"].includes(path.propertyName)
      ) {
        throw new Error(
          `Animation target is missing, ambiguous or unsupported: ${name || track.name}`,
        );
      }
      track.name = `${target.uuid}.${path.propertyName}`;
    }
    return [bound];
  });
}

export function animationPackSources(assets) {
  const packs = assets.animation_packs;
  if (packs != null && !Array.isArray(packs)) throw new Error('Invalid animation pack list.');
  if (!packs?.length) return assets.animations ? [{ ...assets.animations, clips: null }] : [];
  const names = new Set(), ids = new Set();
  for (const pack of packs) {
    if (!pack || typeof pack.id !== 'string' || !pack.id.trim() || ids.has(pack.id) ||
        typeof pack.url !== 'string' || !pack.url.trim() || !Array.isArray(pack.clips) || !pack.clips.length)
      throw new Error('Invalid animation pack list.');
    ids.add(pack.id);
    for (const name of pack.clips) {
      if (typeof name !== 'string' || !name.trim() || names.has(name))
        throw new Error('Missing or duplicate animation action name.');
      names.add(name);
    }
  }
  return packs;
}

export async function loadAnimationPacks(assets, load, dispose) {
  const sources = animationPackSources(assets), loaded = new Map();
  if (!sources.length) throw new Error('Animation pack is unavailable');
  try {
    for (const source of sources) {
      if (!loaded.has(source.url)) loaded.set(source.url, await load(source.url));
    }
    return sources.map(source => ({ source, pack: loaded.get(source.url) }));
  } catch (error) {
    for (const pack of loaded.values()) dispose(pack.scene);
    throw error;
  }
}

export function bindAnimationPacks(packs, rig) {
  return packs.flatMap(({ source, pack }) => {
    if (!source.clips) return bindAnimationClips(pack, rig);
    const names = new Set();
    for (const clip of pack.animations) {
      if (typeof clip.name !== 'string' || !clip.name.trim() || names.has(clip.name))
        throw new Error('Missing or duplicate animation action name in pack.');
      names.add(clip.name);
    }
    for (const name of source.clips) if (!names.has(name))
      throw new Error(`Animation action missing from pack: ${name}`);
    return bindAnimationClips(pack, rig, new Set(source.clips));
  });
}

export function disposeAnimationPacks(packs, dispose) {
  for (const pack of new Set(packs.map(entry => entry.pack))) dispose(pack.scene);
}

// Construction is inert: only an explicit call may start a request. Failed
// attempts clear the guard so a later click can retry; concurrent clicks share it.
export function lazyAnimationPack(url, load, apply) {
  let request;
  return () => {
    if (!request) {
      request = Promise.resolve()
        .then(() => load(url))
        .then(apply)
        .catch((error) => {
          request = undefined;
          throw error;
        });
    }
    return request;
  };
}
