import { readFile, writeFile, mkdir, copyFile } from "node:fs/promises";
import { createHash } from "node:crypto";
import { parseArgs } from "node:util";
import {
  dirname,
  basename,
  resolve,
  join,
  relative,
  isAbsolute,
} from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

export function physicsProfile(rows, source, sha256) {
  const defaults = rows.find((row) => row.Name?.startsWith("Default__"));
  if (!defaults) throw new Error("Blueprint has no default properties");
  const groups = [];
  for (const [name, node] of Object.entries(defaults.Properties)) {
    if (!node?.RootBones?.length || !node.PhysicsSettings) continue;
    if (node.BoneForwardAxis !== "EBoneForwardAxis::X_Positive") {
      throw new Error(`Unsupported dummy-bone axis in ${name}`);
    }
    const settings = node.PhysicsSettings;
    for (const key of ["Damping", "Stiffness", "LimitAngle"]) {
      if (!Number.isFinite(settings[key]) || settings[key] < 0)
        throw new Error(`Invalid ${key} in ${name}`);
    }
    if (!Number.isFinite(node.DummyBoneLength) || node.DummyBoneLength <= 0)
      throw new Error(`Invalid dummy length in ${name}`);
    groups.push({
      name,
      roots: node.RootBones.map((bone) => bone.BoneName),
      excluded: (node.ExcludeBones ?? []).map((bone) => bone.BoneName),
      damping: settings.Damping,
      stiffness: settings.Stiffness,
      limitAngle: Math.min(settings.LimitAngle, 90),
      dummyLength: node.DummyBoneLength / 100,
    });
  }
  if (!groups.length)
    throw new Error("No supported KawaiiPhysics groups found");
  return {
    format: "solaris-spring-preview-v1",
    source: basename(source),
    sha256,
    approximation:
      "Animation-relative springs. No KawaiiPhysics collision, wind or constraint solver.",
    groups,
  };
}

async function stage() {
  const { values } = parseArgs({
    options: {
      model: { type: "string" },
      blueprint: { type: "string" },
      output: { type: "string" },
      "assets-only": { type: "boolean", default: false },
    },
  });
  if (
    !values.output ||
    (!values["assets-only"] && (!values.model || !values.blueprint))
  ) {
    throw new Error(
      "Usage: npm run stage -- --model path.glb --blueprint ABP_PC.json --output preview-directory",
    );
  }
  const root = dirname(fileURLToPath(import.meta.url));
  const output = resolve(values.output);
  const relativeOutput = relative(root, output);
  if (
    !relativeOutput ||
    (!relativeOutput.startsWith("..") && !isAbsolute(relativeOutput))
  ) {
    throw new Error("Keep generated artifacts outside the source directory");
  }
  await mkdir(output, { recursive: true });
  for (const name of [
    "index.html",
    "viewer.css",
    "viewer.js",
    "spring-bones.js",
    "animations.js",
    "facial.js",
    "viewer-ui.js",
    "camera-rig.js",
    "root-motion.js",
    "fonts/DMSans.ttf",
    "fonts/Manrope.ttf",
    "fonts/DMSans-OFL.txt",
    "fonts/Manrope-OFL.txt",
  ]) {
    await mkdir(dirname(join(output, name)), { recursive: true });
    await copyFile(join(root, name), join(output, name));
  }
  const vendor = join(root, "node_modules/three");
  for (const name of [
    "build/three.module.js",
    "build/three.core.js",
    "LICENSE",
    "examples/jsm/controls/OrbitControls.js",
    "examples/jsm/loaders/GLTFLoader.js",
    "examples/jsm/utils/BufferGeometryUtils.js",
    "examples/jsm/utils/SkeletonUtils.js",
  ]) {
    await mkdir(dirname(join(output, "vendor", name)), { recursive: true });
    await copyFile(join(vendor, name), join(output, "vendor", name));
  }
  if (values["assets-only"]) {
    console.log(JSON.stringify({ output, viewerAssets: true }));
    return;
  }
  const source = await readFile(values.blueprint);
  const profile = physicsProfile(
    JSON.parse(source.toString("utf8").replace(/^\uFEFF/, "")),
    values.blueprint,
    createHash("sha256").update(source).digest("hex"),
  );
  if (resolve(values.model) !== join(output, "character.glb")) {
    await copyFile(values.model, join(output, "character.glb"));
  }
  await writeFile(
    join(output, "physics.json"),
    JSON.stringify(profile, null, 2) + "\n",
  );
  console.log(
    JSON.stringify({
      output,
      physicsGroups: profile.groups.length,
      roots: profile.groups.flatMap((group) => group.roots).length,
    }),
  );
}

if (
  process.argv[1] &&
  import.meta.url === pathToFileURL(resolve(process.argv[1])).href
) {
  stage().catch((error) => {
    console.error(error.message);
    process.exitCode = 1;
  });
}
