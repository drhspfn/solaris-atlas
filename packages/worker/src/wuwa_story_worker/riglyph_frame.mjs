// Use the installed Riglyph evaluator to measure zero-bounds authoring exports.
// No texture/raster allocation: the same draw packets used by the renderer define the envelope.
import { readFile } from 'node:fs/promises';
import { createRequire } from 'node:module';
import { resolve } from 'node:path';
import { pathToFileURL } from 'node:url';

const require = createRequire(pathToFileURL(resolve(process.argv[2])));
const load = async (name) => import(pathToFileURL(require.resolve(name)).href);
const { deserializeIR } = await load('@riglyph/core');
const { SkeletonInstance } = await load('@riglyph/runtime');
const { prepareDrawPackets, drawBounds } = await load('@riglyph/renderer-webgl2');
const ir = deserializeIR(await readFile(process.argv[3], 'utf8'));
const fps = Number(process.argv[4]);
const maximum = Number(process.argv[5]);
let bounds = null, frames = 0;
for (const clip of ir.skeleton.animations) {
  const count = Math.max(1, Math.ceil(clip.durationSec * fps));
  frames += count;
  if (frames > maximum) throw new Error('Framing sample budget exceeded');
  const instance = new SkeletonInstance(ir);
  for (let index = 0; index < count; index++) {
    instance.evaluate([{ clipId: clip.id, timeSec: index / fps, loop: false }]);
    const current = drawBounds(prepareDrawPackets(instance));
    if (!current) continue;
    if (!bounds) bounds = [...current];
    else {
      const x = Math.min(bounds[0], current[0]), y = Math.min(bounds[1], current[1]);
      bounds = [x, y, Math.max(bounds[0] + bounds[2], current[0] + current[2]) - x,
        Math.max(bounds[1] + bounds[3], current[1] + current[3]) - y];
    }
  }
}
if (!bounds || bounds.some((value) => !Number.isFinite(value)) || bounds[2] <= 0 || bounds[3] <= 0)
  throw new Error('Animation has no measurable canvas');
process.stdout.write(JSON.stringify({ bounds }));
