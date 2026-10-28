import { Quaternion, Vector3 } from 'three';

const names = [
  [/nose/i, 'Nose', 'Face'], [/head/i, 'Head', 'Body'], [/neck/i, 'Neck', 'Body'],
  [/chest/i, 'Chest', 'Body'], [/pelvis|hips/i, 'Pelvis', 'Body'], [/spine/i, 'Spine', 'Body'],
  [/hand|finger|arm|clavicle|shoulder|elbow/i, null, 'Arms & hands'],
  [/thigh|calf|foot|toe|knee|leg/i, null, 'Legs & feet'],
  [/hair/i, null, 'Hair'], [/skirt|piao/i, null, 'Clothing'],
];

/** Only real exported bones are offered; anatomical shortcuts never invent joints. */
export function cameraBones(model, parser) {
  const bones = new Map(), options = [];
  model.traverse(bone => {
    if (!bone.isBone) return;
    const index = parser?.associations.get(bone)?.nodes;
    const name = parser?.json.nodes[index]?.name || bone.name;
    const rule = names.find(([pattern]) => pattern.test(name));
    bones.set(bone.uuid, bone);
    options.push({ value: bone.uuid, label: rule?.[1] ? `${rule[1]} · ${name}` : name,
      group: rule?.[2] || 'Other bones', name });
  });
  options.sort((a, b) => a.group.localeCompare(b.group) || a.label.localeCompare(b.label, undefined, { numeric: true }));
  const preferred = options.find(option => /^(Bip\d*)?Head$/i.test(option.name)) || options[0];
  return { bones, options, preferred: preferred?.value };
}

/** Translate the orbit frame with a bone, optionally following its orientation too. */
export class BoneCamera {
  constructor(camera, controls) {
    this.camera = camera;
    this.controls = controls;
    this.bone = null;
    this.following = false;
    this.space = 'world';
    this.offset = new Vector3();
    this.anchor = new Vector3();
    this.rotation = new Quaternion();
    this.previousAnchor = new Vector3();
    this.previousRotation = new Quaternion();
    this.delta = new Quaternion();
    this.shift = new Vector3();
  }

  sample() {
    this.bone.updateWorldMatrix(true, false);
    this.bone.getWorldPosition(this.anchor);
    this.bone.getWorldQuaternion(this.rotation);
    this.shift.copy(this.offset);
    if (this.space === 'local') this.shift.applyQuaternion(this.rotation);
    this.anchor.add(this.shift);
  }

  frame(bone, offset = this.offset) {
    if (!bone?.isBone) return false;
    this.bone = bone;
    this.offset.copy(offset);
    this.sample();
    this.shift.subVectors(this.anchor, this.controls.target);
    this.camera.position.add(this.shift);
    this.controls.target.copy(this.anchor);
    this.remember();
    this.controls.update();
    return true;
  }

  attach(bone, { space = this.space, offset = this.offset } = {}) {
    this.space = space === 'local' ? 'local' : 'world';
    this.following = this.frame(bone, offset);
    return this.following;
  }

  detach() { this.following = false; }

  setOffset(x, y, z) {
    if (![x, y, z].every(Number.isFinite)) return;
    this.offset.set(...[x, y, z].map(value => Math.max(-10, Math.min(10, value))));
    if (this.following) this.frame(this.bone);
  }

  setSpace(space) {
    this.space = space === 'local' ? 'local' : 'world';
    if (this.bone) { this.sample(); this.remember(); }
  }

  remember() {
    this.previousAnchor.copy(this.anchor);
    this.previousRotation.copy(this.rotation);
  }

  update() {
    if (!this.following || !this.bone) return;
    this.sample();
    this.delta.copy(this.previousRotation).invert().premultiply(this.rotation);
    for (const point of [this.camera.position, this.controls.target]) {
      point.sub(this.previousAnchor);
      if (this.space === 'local') point.applyQuaternion(this.delta);
      point.add(this.anchor);
    }
    this.remember();
  }
}
