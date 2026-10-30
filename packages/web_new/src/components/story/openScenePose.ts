/** Independent evaluator for the neutral scene contract; no rendering or clock state. */
type Frame = { time: number; values: number[]; curve?: 0 | 1 | number[] };
type AttachmentFrame = { time: number; name: string | null };
type Influence = [number, number, number, number];
export type SceneMesh = {
  name: string;
  type: number;
  path: string;
  uvs: number[];
  triangles: number[];
  vertices: number[] | Influence[][];
  hull: number;
  color: string;
};
type Timeline =
  | { kind: 'bone'; target: number; type: number; frames: Frame[] }
  | { kind: 'slot'; target: number; type: number; frames: (Frame | AttachmentFrame)[] }
  | {
      kind: 'deform';
      skin: number;
      target: number;
      name: string;
      frames: (Frame & { offset: number })[];
    };
export type SkeletalScene = {
  format: 'solaris.skeletal-scene.v1';
  version: string;
  bounds: number[];
  bones: {
    name: string;
    parent: number | null;
    transform: number[];
    mode: number;
    skin: boolean;
  }[];
  slots: {
    name: string;
    bone: number;
    color: string;
    dark: string;
    attachment: string | null;
    blend: number;
  }[];
  skins: { name: string; slots: Record<string, Record<string, SceneMesh>> }[];
  animations: { name: string; timelines: Timeline[] }[];
};
export type PosedDraw = {
  slot: number;
  name: string;
  attachment: string;
  path: string;
  sourceSlot: SkeletalScene['slots'][number];
  sourceAttachment: SceneMesh;
  vertices: number[];
  uvs: number[];
  triangles: number[];
  hull: number;
  color: number[];
  blend: number;
};
type Matrix = [number, number, number, number, number, number];

function requireValue(condition: unknown, message: string): asserts condition {
  if (!condition) throw new Error(`Skeletal scene: ${message}`);
}
function index<T>(items: T[], i: number, label: string): T {
  requireValue(
    Number.isInteger(i) && i >= 0 && i < items.length,
    `${label} index ${i} out of range`,
  );
  return items[i];
}
function numbers(values: number[], length: number, label: string) {
  requireValue(values.length === length && values.every(Number.isFinite), `invalid ${label}`);
}
function rgba(hex: string): number[] {
  requireValue(/^[\da-f]{8}$/i.test(hex), `invalid color ${hex}`);
  return [0, 2, 4, 6].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255);
}
function animation(scene: SkeletalScene, name: string) {
  requireValue(
    scene.format === 'solaris.skeletal-scene.v1' && /^4\.1\.\d+$/.test(scene.version),
    'unsupported scene format/version',
  );
  const result = scene.animations.find((item) => item.name === name);
  requireValue(result, `unknown animation ${name}`);
  return result;
}
function durationOf(timelines: Timeline[]): number {
  let duration = 0;
  for (const timeline of timelines) {
    let previous = -1;
    for (const frame of timeline.frames) {
      requireValue(
        Number.isFinite(frame.time) && frame.time >= 0 && frame.time > previous,
        'frame times must be nonnegative and strictly increasing',
      );
      previous = frame.time;
      duration = Math.max(duration, frame.time);
    }
  }
  return duration;
}
export function sceneDuration(scene: SkeletalScene, animationName: string): number {
  return durationOf(animation(scene, animationName).timelines);
}

function cubic(a: number, b: number, c: number, d: number, t: number) {
  const s = 1 - t;
  return s * s * s * a + 3 * s * s * t * b + 3 * s * t * t * c + t * t * t * d;
}
function interpolate(a: Frame, b: Frame, channel: number, time: number): number {
  const from = a.values[channel],
    to = b.values[channel];
  if (a.curve === 1) return from;
  if (a.curve === undefined || a.curve === 0)
    return from + ((to - from) * (time - a.time)) / (b.time - a.time);
  const [x1, y1, x2, y2] = a.curve.slice(channel * 4, channel * 4 + 4);
  let low = 0,
    high = 1;
  for (let i = 0; i < 48; i++) {
    const mid = (low + high) / 2;
    if (cubic(a.time, x1, x2, b.time, mid) < time) low = mid;
    else high = mid;
  }
  return cubic(from, y1, y2, to, (low + high) / 2);
}
function validateFrames(frames: Frame[], channels: number) {
  for (const [i, frame] of frames.entries()) {
    numbers(frame.values, channels, 'frame values');
    requireValue(
      frame.curve === undefined ||
        frame.curve === 0 ||
        frame.curve === 1 ||
        Array.isArray(frame.curve),
      'unsupported curve',
    );
    if (Array.isArray(frame.curve)) {
      numbers(frame.curve, channels * 4, 'Bezier controls');
      const next = frames[i + 1];
      if (next)
        for (let c = 0; c < channels; c++) {
          const x1 = frame.curve[c * 4],
            x2 = frame.curve[c * 4 + 2];
          // Check the quadratic time derivative, allowing crossed control handles.
          const d0 = x1 - frame.time,
            d1 = x2 - x1,
            d2 = next.time - x2;
          const curvature = d0 - 2 * d1 + d2;
          const t = curvature > 0 ? Math.max(0, Math.min(1, (d0 - d1) / curvature)) : 0;
          const minimum = d0 * (1 - t) ** 2 + 2 * d1 * (1 - t) * t + d2 * t ** 2;
          requireValue(d0 >= 0 && d2 >= 0 && minimum >= 0, 'non-monotone Bezier time controls');
        }
    }
  }
}
function frameIndex(frames: { time: number }[], time: number) {
  let i = -1;
  while (i + 1 < frames.length && frames[i + 1].time <= time) i++;
  return i;
}
function sample(frames: Frame[], time: number): number[] | undefined {
  const i = frameIndex(frames, time);
  if (i < 0) return undefined;
  if (i === frames.length - 1) return frames[i].values.slice();
  return frames[i].values.map((_, c) => interpolate(frames[i], frames[i + 1], c, time));
}
function meshFor(scene: SkeletalScene, slot: number, name: string): SceneMesh {
  requireValue(scene.skins[0]?.name === 'default', 'skin 0 must be default');
  const attachments = scene.skins[0].slots[slot];
  const mesh = attachments && Object.hasOwn(attachments, name) ? attachments[name] : undefined;
  requireValue(mesh, `missing attachment ${name} in slot ${slot}`);
  requireValue(mesh.type === 2, `unsupported attachment type ${mesh.type}`);
  return mesh;
}
function weighted(mesh: SceneMesh): mesh is SceneMesh & { vertices: Influence[][] } {
  return Array.isArray(mesh.vertices[0]);
}
function deformLength(mesh: SceneMesh) {
  return weighted(mesh)
    ? mesh.vertices.reduce((n, vertex) => n + vertex.length * 2, 0)
    : mesh.vertices.length;
}
function deform(frames: (Frame & { offset: number })[], length: number, time: number) {
  const expanded = frames.map((frame) => {
    requireValue(
      Number.isInteger(frame.offset) &&
        frame.offset >= 0 &&
        frame.offset + frame.values.length <= length,
      'deform offset out of range',
    );
    requireValue(frame.values.every(Number.isFinite), 'invalid deform values');
    const values = Array<number>(length).fill(0);
    values.splice(frame.offset, frame.values.length, ...frame.values);
    return { ...frame, values };
  });
  // Deform curves interpolate a scalar progress, not individual coordinate values.
  validateFrames(
    frames.map((frame) => ({ ...frame, values: [0] })),
    1,
  );
  const i = frameIndex(expanded, time);
  if (i < 0) return Array<number>(length).fill(0);
  if (i === expanded.length - 1) return expanded[i].values;
  const progress = interpolate(
    { ...frames[i], values: [0] },
    { ...frames[i + 1], values: [1] },
    0,
    time,
  );
  return expanded[i].values.map((v, c) => v + (expanded[i + 1].values[c] - v) * progress);
}
function point(m: Matrix, x: number, y: number): [number, number] {
  return [m[0] * x + m[2] * y + m[4], m[1] * x + m[3] * y + m[5]];
}

/** Times clamp by default. Looping wraps positive-duration clips; zero-duration clips stay at zero.
 * Weighted deform coordinates address each influence's local XY pair in vertex order.
 * Returned geometry is owned by the result and never aliases mutable scene arrays.
 */
export function evaluateScene(
  scene: SkeletalScene,
  animationName: string,
  time: number,
  options: { loop?: boolean } = {},
): { draws: PosedDraw[]; duration: number; time: number } {
  const clip = animation(scene, animationName);
  const duration = durationOf(clip.timelines);
  requireValue(Number.isFinite(time), 'time must be finite');
  time =
    options.loop && duration > 0
      ? ((time % duration) + duration) % duration
      : Math.max(0, Math.min(time, duration));
  const poses = scene.bones.map((bone) => {
    requireValue(bone.mode === 0 && bone.skin === false, 'unsupported bone mode/skin requirement');
    numbers(bone.transform, 8, 'bone transform');
    return bone.transform.slice();
  });
  const slots = scene.slots.map((slot) => {
    index(poses, slot.bone, 'slot bone');
    requireValue(
      Number.isInteger(slot.blend) && slot.blend >= 0 && slot.blend <= 3,
      'unsupported blend',
    );
    requireValue(slot.dark.toLowerCase() === 'ffffffff', 'unsupported dark tint');
    return { attachment: slot.attachment, color: rgba(slot.color) };
  });
  const deforms = new Map<number, Map<string, number[]>>();
  const channels = [[0], [1, 2], [1], [2], [3, 4], [3], [4], [5, 6], [5], [6]];
  for (const timeline of clip.timelines) {
    if (timeline.kind === 'bone') {
      const pose = index(poses, timeline.target, 'bone timeline');
      const setup = scene.bones[timeline.target].transform;
      const fields = index(channels, timeline.type, 'bone timeline type');
      validateFrames(timeline.frames, fields.length);
      const values = sample(timeline.frames, time);
      if (values)
        fields.forEach((field, c) => {
          pose[field] =
            field === 3 || field === 4 ? setup[field] * values[c] : setup[field] + values[c];
        });
    } else if (timeline.kind === 'slot') {
      const slot = index(slots, timeline.target, 'slot timeline');
      if (timeline.type === 0) {
        const frames = timeline.frames as AttachmentFrame[];
        for (const frame of frames) {
          requireValue(
            frame.name === null || typeof frame.name === 'string',
            'invalid attachment frame',
          );
          if (frame.name !== null) meshFor(scene, timeline.target, frame.name);
        }
        const i = frameIndex(frames, time);
        if (i >= 0) slot.attachment = frames[i].name;
      } else {
        requireValue(timeline.type === 1, `unsupported slot timeline type ${timeline.type}`);
        const frames = timeline.frames as Frame[];
        validateFrames(frames, 4);
        requireValue(
          frames.every((frame) => frame.values.every((v) => v >= 0 && v <= 1)),
          'RGBA values must be normalized',
        );
        slot.color = sample(frames, time) ?? slot.color;
      }
    } else if (timeline.kind === 'deform') {
      index(slots, timeline.target, 'deform slot');
      requireValue(timeline.skin === 0, 'unsupported deform skin');
      const mesh = meshFor(scene, timeline.target, timeline.name);
      const byName = deforms.get(timeline.target) ?? new Map<string, number[]>();
      byName.set(timeline.name, deform(timeline.frames, deformLength(mesh), time));
      deforms.set(timeline.target, byName);
    } else {
      throw new Error('Skeletal scene: unsupported timeline kind');
    }
  }
  const world: Matrix[] = [];
  scene.bones.forEach((bone, i) => {
    const [rotation, x, y, sx, sy, shx, shy] = poses[i];
    const ax = ((rotation + shx) * Math.PI) / 180,
      ay = ((rotation + 90 + shy) * Math.PI) / 180;
    const local: Matrix = [
      Math.cos(ax) * sx,
      Math.sin(ax) * sx,
      Math.cos(ay) * sy,
      Math.sin(ay) * sy,
      x,
      y,
    ];
    if (bone.parent === null) world.push(local);
    else {
      const parent = index(world, bone.parent, 'parent (must precede child)');
      const [a, b, c, d] = local;
      world.push([
        parent[0] * a + parent[2] * b,
        parent[1] * a + parent[3] * b,
        parent[0] * c + parent[2] * d,
        parent[1] * c + parent[3] * d,
        ...point(parent, x, y),
      ]);
    }
  });
  const draws: PosedDraw[] = [];
  slots.forEach((slot, i) => {
    if (slot.attachment === null) return;
    const mesh = meshFor(scene, i, slot.attachment);
    const offsets = deforms.get(i)?.get(slot.attachment);
    const vertices: number[] = [];
    let cursor = 0;
    if (weighted(mesh)) {
      for (const influences of mesh.vertices) {
        requireValue(influences.length > 0, 'empty weighted vertex');
        let x = 0,
          y = 0;
        for (const influence of influences) {
          numbers(influence, 4, 'influence');
          const [bone, px, py, weight] = influence;
          requireValue(weight >= 0, 'negative vertex weight');
          const p = point(
            index(world, bone, 'influence bone'),
            px + (offsets?.[cursor++] ?? 0),
            py + (offsets?.[cursor++] ?? 0),
          );
          x += p[0] * weight;
          y += p[1] * weight;
        }
        vertices.push(x, y);
      }
    } else {
      const positions = mesh.vertices as number[];
      requireValue(
        positions.length % 2 === 0 && positions.every(Number.isFinite),
        'invalid XY vertices',
      );
      for (let v = 0; v < positions.length; v += 2) {
        vertices.push(
          ...point(
            world[scene.slots[i].bone],
            positions[v] + (offsets?.[v] ?? 0),
            positions[v + 1] + (offsets?.[v + 1] ?? 0),
          ),
        );
      }
    }
    numbers(mesh.uvs, vertices.length, 'UV coordinates');
    requireValue(
      mesh.triangles.length % 3 === 0 &&
        mesh.triangles.every((v) => Number.isInteger(v) && v >= 0 && v < vertices.length / 2),
      'triangle index out of range',
    );
    requireValue(
      Number.isInteger(mesh.hull) && mesh.hull >= 0 && mesh.hull <= vertices.length / 2,
      'invalid hull',
    );
    const tint = rgba(mesh.color);
    draws.push({
      slot: i,
      name: scene.slots[i].name,
      attachment: slot.attachment,
      path: mesh.path,
      sourceSlot: scene.slots[i],
      sourceAttachment: mesh,
      vertices,
      uvs: mesh.uvs.slice(),
      triangles: mesh.triangles.slice(),
      hull: mesh.hull,
      color: slot.color.map((v, c) => Math.max(0, Math.min(1, v)) * tint[c]),
      blend: scene.slots[i].blend,
    });
  });
  return { draws, duration, time };
}
