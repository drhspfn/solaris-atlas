import { test } from 'node:test';
import assert from 'node:assert/strict';
import * as THREE from 'three';
import { createEyeSm5Experiment, evaluateEyeSm5 } from '../src/components/models/engine/eye-sm5-experiment.js';

const scalars = { ScaleTransition_Speed: 5, EyeLight_TransitionScale: .5, UseEyeScaleTransition: 0,
  LightDifferentScale: 1, LightShakeSpeed: 10, LightShakeScale: .01, LightShakPositionX: 0, LightShakPositionY: 0,
  ShapePosition01X: .5, ShapePosition01Y: .5, RotationAnlgle01: 0, ShapeScaleX: 1, ShapeScaleY: 1,
  LightNormlScale: 0, LightPositionX: 0, LightPositionY: 0, EdgeWidth: .6, UseProLight: 12,
  SecondLight_WidthX: 1, SecondLight_WidthY: 1, SecondLight_PositionX: .45, SecondLight_PositionY: .5,
  SecondLight_Position_02X: .1, SecondLight_Position_02Y: 0, RotateAngle: 0, LightDisable: 0, LightEmissiveInt: .8 };
const colors = { EdgeColor: { R: 0, G: 0, B: 0, A: 1 },
  HeightLightColor: { R: .2, G: .4, B: .6, A: 1 }, OutLineColor: { R: .8, G: .4, B: .2, A: .5 } };
const input = { uv: [.5, .5], base: [.1, .2, .3], vertexGreen: 0, time: 0, scalars, colors,
  sampleHeightMap: () => [0, 0, 0] };
const close = (a, b) => assert.ok(Math.abs(a - b) < 1e-10, `${a} != ${b}`);

test('procedural factor is not clamped and texture channels are ordered', () => {
  const p = evaluateEyeSm5(input);
  p.procedural.forEach((v, i) => close(v, input.base[i] + 12));
  const r = evaluateEyeSm5({ ...input, sampleHeightMap: () => [1, 0, .5] });
  r.color.forEach((v, i) => close(v, [.2, .4, .6][i]));
  close(r.blueTerm, .4);
  const g = evaluateEyeSm5({ ...input, sampleHeightMap: () => [1, 1, 1] });
  g.color.forEach((v, i) => close(v, [.4, .2, .1][i]));
  const disabled = evaluateEyeSm5({ ...input, scalars: { ...scalars, LightDisable: 1 }, sampleHeightMap: () => [1, 1, 1] });
  disabled.color.forEach((v, i) => close(v, input.base[i]));
  assert.equal(disabled.blueTerm, 0);
});

test('UV positioning, rotation, vertex green and independent widths', () => {
  const p = evaluateEyeSm5({ ...input, uv: [.725, .5] });
  close(p.heightUv[0], 1.5); close(p.heightUv[1], .5);
  const q = evaluateEyeSm5({ ...input, uv: [.725, .5], vertexGreen: 1,
    scalars: { ...scalars, RotateAngle: Math.PI / 2, SecondLight_WidthX: 2 } });
  close(q.heightUv[0], .5); close(q.heightUv[1], .5 + .125 / .45);
  const negative = evaluateEyeSm5({ ...input, uv: [.725, .5], scalars: { ...scalars, SecondLight_WidthX: -1 } });
  close(negative.heightUv[0], -.5);
  const animated = evaluateEyeSm5({ ...input, uv: [.725, .5], scalars: { ...scalars, UseEyeScaleTransition: .5 } });
  close(animated.heightUv[0], .5 + .225 / (.225 * 1.25));
  const singularAtTime = evaluateEyeSm5({ ...input, scalars: { ...scalars, UseEyeScaleTransition: 1,
    LightNormlScale: .225 * 1.25 } });
  assert.equal(singularAtTime, null);
});

test('Hsin and Denia authored map centers respond to original vertex green', () => {
  for (const [x, y, dx, dy, wx, wy, angle] of [
    [.431905, .382857, .038095, -.002857, .644574, .415261, 0],
    [.47881, .340952, -.049999, 0, .866215, .664286, .042938],
    [.459762, .321904, -.021428, 0, .313834, .42691, .042938],
  ]) {
    const p = { ...scalars, SecondLight_PositionX: x, SecondLight_PositionY: y,
      SecondLight_Position_02X: dx, SecondLight_Position_02Y: dy,
      SecondLight_WidthX: wx, SecondLight_WidthY: wy, RotateAngle: angle };
    const result = evaluateEyeSm5({ ...input, uv: [x + .05 + dx * .25, y + dy * .25],
      vertexGreen: .25, scalars: p });
    close(result.heightUv[0], .5); close(result.heightUv[1], .5);
  }
});

test('missing data, singular widths and edge denominator reject the experiment', () => {
  assert.equal(evaluateEyeSm5({ ...input, colors: {} }), null);
  assert.equal(evaluateEyeSm5({ ...input, sampleHeightMap: () => null }), null);
  assert.equal(evaluateEyeSm5({ ...input, scalars: { ...scalars, SecondLight_WidthY: 0 } }), null);
  assert.equal(evaluateEyeSm5({ ...input, scalars: { ...scalars, EdgeWidth: 2 } }), null);
  const geometry = new THREE.BufferGeometry();
  const options = { geometry, texture: new THREE.Texture(), base: input.base, scalars, colors, time: 0,
    uvFlipV: false, textureFlipV: false, mipBias: 0 };
  assert.equal(createEyeSm5Experiment(options), null);
  geometry.setAttribute('uv', new THREE.Float32BufferAttribute([0, 0], 2));
  geometry.setAttribute('color', new THREE.Float32BufferAttribute([1, 1, 1, 1], 4));
  assert.equal(createEyeSm5Experiment(options), null); // Synthetic COLOR_0 is not the game color.
  geometry.setAttribute('color_1', new THREE.BufferAttribute(new Uint16Array([0, 32768, 0, 65535]), 4, true));
  const material = createEyeSm5Experiment(options);
  assert.ok(material.isShaderMaterial);
  assert.equal(material.uniforms.UseProLight.value, 12);
  assert.match(material.vertexShader, /green=color_1\.g/);
  geometry.getAttribute('color_1').normalized = false;
  assert.equal(createEyeSm5Experiment(options), null);
  assert.match(material.fragmentShader, /mix\(base,mix\(base\+vec3\(core\),EdgeColor\.rgb,edge\),UseProLight\)/);
  assert.equal(createEyeSm5Experiment({ ...options, uvFlipV: undefined }), null);
  material.dispose();
});
