// Facial curves retain authored names/times and never carry character geometry.
export class FacialAnimation {
  constructor(pack, model) {
    if (pack?.format !== "solaris-facial-v1" || !Array.isArray(pack.clips))
      throw new Error("Unsupported facial animation pack");
    this.targets = new Map();
    model.traverse((mesh) => {
      for (const [name, index] of Object.entries(mesh.morphTargetDictionary || {})) {
        const key = name.toLowerCase();
        if (!this.targets.has(key)) this.targets.set(key, []);
        this.targets.get(key).push({ mesh, index, rest: mesh.morphTargetInfluences[index] });
      }
    });
    if (!this.targets.size) throw new Error("Load the full model to enable facial animation");
    this.clips = new Map();
    this.bodyClips = new Map();
    for (const clip of pack.clips) {
      if (typeof clip.name !== "string" || !clip.name || this.clips.has(clip.name)
        || !Number.isFinite(clip.seconds) || clip.seconds < 0 || !Array.isArray(clip.tracks))
        throw new Error("Invalid facial animation clip");
      const targets = new Set();
      for (const track of clip.tracks) {
        if (typeof track.target !== "string" || !track.target || targets.has(track.target.toLowerCase())
          || !Array.isArray(track.keys) || !track.keys.length)
          throw new Error("Invalid facial animation track");
        targets.add(track.target.toLowerCase());
        let previous = -1;
        for (const key of track.keys) {
          if (!Array.isArray(key) || key.length !== 2 || !key.every(Number.isFinite)
            || key[0] < 0 || key[0] <= previous || key[0] > clip.seconds)
            throw new Error("Invalid facial animation samples");
          previous = key[0];
        }
      }
      if (clip.tracks.some((track) => this.targets.has(track.target.toLowerCase()))) {
        this.clips.set(clip.name, clip);
        if (clip.body_clip) {
          if (this.bodyClips.has(clip.body_clip)) throw new Error("Ambiguous facial body clip");
          this.bodyClips.set(clip.body_clip, clip);
        }
      }
    }
    if (!this.clips.size) throw new Error("No facial curves match this model");
    this.selected = "auto";
    this.time = 0;
    this.active = new Set();
  }

  select(name) {
    if (!["auto", "off"].includes(name) && !this.clips.has(name))
      throw new Error("Unknown facial animation");
    this.selected = name;
    this.time = 0;
  }

  reset() {
    for (const key of this.active)
      for (const target of this.targets.get(key))
        target.mesh.morphTargetInfluences[target.index] = target.rest;
    this.active.clear();
  }

  update(bodyName, bodyTime, delta = 0) {
    this.reset();
    const clip = this.selected === "auto" ? this.bodyClips.get(bodyName) : this.clips.get(this.selected);
    if (!clip) return;
    this.time += Math.max(0, delta);
    const time = this.selected === "auto" ? bodyTime : clip.seconds ? this.time % clip.seconds : 0;
    for (const track of clip.tracks) {
      const key = track.target.toLowerCase(), targets = this.targets.get(key);
      if (!targets) continue;
      const keys = track.keys;
      // Binary search matters for long clips with per-frame facial samples.
      let low = 0, high = keys.length - 1;
      while (low < high) {
        const mid = Math.ceil((low + high) / 2);
        if (keys[mid][0] <= time) low = mid; else high = mid - 1;
      }
      const a = keys[low], b = keys[Math.min(low + 1, keys.length - 1)];
      const ratio = b[0] > a[0] ? Math.max(0, Math.min(1, (time - a[0]) / (b[0] - a[0]))) : 0;
      const value = a[1] + (b[1] - a[1]) * ratio;
      for (const { mesh, index } of targets) mesh.morphTargetInfluences[index] = value;
      this.active.add(key);
    }
  }
}
