import { Vector3 } from "three";

/** Cancel root translation in viewer/world axes without changing the authored clip. */
export class RootMotionLock {
  constructor(stage, model) {
    this.stage = stage;
    this.base = stage.position.clone();
    this.enabled = false;
    this.axes = { x: true, y: false, z: true };
    const roots = new Set();
    model.traverse((object) => {
      if (!object.isSkinnedMesh) return;
      for (let bone of object.skeleton.bones) {
        while (bone.parent?.isBone) bone = bone.parent;
        roots.add(bone);
      }
    });
    // Do not invent a pelvis/landmark when the model has unrelated skeletons.
    this.root = roots.size === 1 ? [...roots][0] : null;
    this.reference = new Vector3();
    this.current = new Vector3();
    if (this.root) this.root.getWorldPosition(this.reference);
    this.reference.x = this.reference.z = 0;
  }

  restore() {
    this.stage.position.copy(this.base);
    this.stage.updateMatrixWorld(true);
  }

  apply() {
    // Always measure the uncorrected pose, including after seeks and toggles.
    this.restore();
    if (!this.enabled || !this.root) return;
    this.root.getWorldPosition(this.current);
    for (const axis of ["x", "y", "z"]) {
      if (this.axes[axis])
        this.stage.position[axis] += this.reference[axis] - this.current[axis];
    }
    this.stage.updateMatrixWorld(true);
  }
}
