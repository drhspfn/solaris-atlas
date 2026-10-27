import { copyFile, mkdir, readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const root = fileURLToPath(new URL('../', import.meta.url));
const source = path.resolve(root, '../../tools/model-viewer');
const destination = path.join(root, 'public/model-viewer');
const three = path.join(root, 'node_modules/three');
const { version } = JSON.parse(await readFile(path.join(three, 'package.json'), 'utf8'));
if (version !== '0.183.2') throw new Error(`Expected Three.js 0.183.2, found ${version}`);

// Stage only the viewer and its pinned runtime, never local character/game assets.
const files = ['index.html', 'viewer.js', 'viewer.css', 'spring-bones.js'];
const vendor = [
  'build/three.module.js', 'build/three.core.js',
  'examples/jsm/controls/OrbitControls.js',
  'examples/jsm/loaders/GLTFLoader.js',
  'examples/jsm/utils/BufferGeometryUtils.js',
  'examples/jsm/utils/SkeletonUtils.js', 'LICENSE',
];
for (const [base, names, prefix] of [[source, files, ''], [three, vendor, 'vendor']]) {
  for (const name of names) {
    const target = path.join(destination, prefix, name);
    await mkdir(path.dirname(target), { recursive: true });
    await copyFile(path.join(base, name), target);
  }
}
