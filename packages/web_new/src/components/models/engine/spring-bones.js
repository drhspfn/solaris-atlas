import { Quaternion, Vector3, MathUtils } from 'three';

// An animation-relative spring preview, not a port of Kuro's KawaiiPhysics.
// Source groups/limits identify the rig; capsule/cloth collisions are not simulated.
export class SpringBones {
  constructor(root, profile) {
    this.root = root;
    this.joints = [];
    this.missing = [];
    const bones = new Map();
    root.traverse(node => { if (node.isBone) bones.set(node.name, node); });
    const owners = new Map();
    const visited = new Set();
    for (const group of profile.groups) {
      for (const name of group.roots) owners.set(name, group);
    }
    const visit = (bone, group) => {
      if (group.excluded.includes(bone.name)) return;
      if (owners.has(bone.name) && owners.get(bone.name) !== group) return;
      if (visited.has(bone)) return;
      visited.add(bone);
      const child = bone.children.find(node => node.isBone && node.position.lengthSq() > 1e-10);
      const offset = child ? child.position.clone() : new Vector3(group.dummyLength, 0, 0);
      if (offset.lengthSq() > 1e-10) {
        this.joints.push({ bone, offset, group, base: bone.quaternion.clone(),
          tail: new Vector3(), velocity: new Vector3(), anchor: new Vector3(), ready: false });
      }
      for (const node of bone.children) if (node.isBone) visit(node, group);
    };
    for (const [name, group] of owners) {
      const bone = bones.get(name);
      if (bone) visit(bone, group);
      else this.missing.push(name);
    }
    // Explicit nested roots can precede their parent groups in the source blueprint.
    const depth = bone => { let n = 0; for (; bone.parent; bone = bone.parent) n++; return n; };
    this.joints.sort((a, b) => depth(a.bone) - depth(b.bone));
    this.origin = new Vector3();
    this.target = new Vector3();
    this.restDirection = new Vector3();
    this.direction = new Vector3();
    this.acceleration = new Vector3();
    this.rotation = new Quaternion();
    this.worldRotation = new Quaternion();
    this.parentRotation = new Quaternion();
  }

  restore() {
    for (const joint of this.joints) joint.bone.quaternion.copy(joint.base);
  }

  reset() {
    for (const joint of this.joints) { joint.ready = false; joint.velocity.set(0, 0, 0); }
  }

  update(dt, strength = 1) {
    if (!Number.isFinite(dt) || dt <= 0) return;
    this.root.updateMatrixWorld(true);
    for (const joint of this.joints) {
      const { bone, offset, group } = joint;
      joint.base.copy(bone.quaternion);
      bone.updateWorldMatrix(true, false);
      bone.getWorldPosition(this.origin);
      this.target.copy(offset).applyMatrix4(bone.matrixWorld);
      const length = this.restDirection.subVectors(this.target, this.origin).length();
      if (length < 1e-8) continue;
      this.restDirection.divideScalar(length);
      if (!joint.ready || dt > 0.15 || joint.anchor.distanceTo(this.origin) > length * 4) {
        joint.tail.copy(this.target);
        joint.velocity.set(0, 0, 0);
        joint.ready = true;
      }
      joint.anchor.copy(this.origin);
      const steps = Math.ceil(Math.min(dt, 0.05) * 120);
      const h = Math.min(dt, 0.05) / steps;
      // Source stiffness/damping are dimensionless; these rates are an explicit
      // browser approximation. Keep the original animated pose as the equilibrium.
      const frequency = 12 + group.stiffness * 30;
      const damping = 2 * (0.3 + group.damping * 0.9) * frequency;
      for (let i = 0; i < steps; i++) {
        this.acceleration.subVectors(this.target, joint.tail).multiplyScalar(frequency ** 2)
          .addScaledVector(joint.velocity, -damping);
        joint.velocity.addScaledVector(this.acceleration, h);
        joint.tail.addScaledVector(joint.velocity, h);
      }
      this.direction.subVectors(joint.tail, this.origin).normalize();
      if (this.direction.lengthSq() < 0.5) this.direction.copy(this.restDirection);
      const angle = this.restDirection.angleTo(this.direction);
      const limit = MathUtils.degToRad(group.limitAngle);
      this.rotation.setFromUnitVectors(this.restDirection, this.direction);
      if (angle > limit) this.rotation.identity().slerp(
        this.worldRotation.setFromUnitVectors(this.restDirection, this.direction), limit / angle);
      this.direction.copy(this.restDirection).applyQuaternion(this.rotation);
      joint.tail.copy(this.origin).addScaledVector(this.direction, length);
      // Remove radial velocity, which cannot contribute to rotation.
      joint.velocity.addScaledVector(this.direction, -joint.velocity.dot(this.direction));
      this.rotation.identity().slerp(
        this.worldRotation.setFromUnitVectors(this.restDirection, this.direction),
        MathUtils.clamp(strength, 0, 1));
      bone.getWorldQuaternion(this.worldRotation);
      this.worldRotation.premultiply(this.rotation);
      bone.parent.getWorldQuaternion(this.parentRotation).invert();
      bone.quaternion.copy(this.parentRotation.multiply(this.worldRotation)).normalize();
      bone.updateMatrix();
    }
  }
}
