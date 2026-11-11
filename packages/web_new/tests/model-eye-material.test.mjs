import { test } from 'node:test';
import assert from 'node:assert/strict';
import * as THREE from 'three';
import { eyeHighlightParameters, installEyeMaterials } from '../src/components/models/engine/eye-material.js';

const payload = { format: 'solaris-eye-material-v1', textures: { EM: { data_base64: 'YWJj' } },
  scalars: { HeightLight_PositionX: .5, HeightLight_PositionY: .55, HeightLight_WidthX: 1.5,
    HeightLight_WidthY: .95, HeightLightScale: .392856 },
  colors: { HeightLight_Color: { R: .1, G: .15, B: .55, A: .3 } } };

test('installs the authored EM highlight only on eligible eye materials', () => {
  const root = new THREE.Group(), eye = new THREE.MeshStandardMaterial(), skin = new THREE.MeshStandardMaterial();
  eye.userData.solaris_eye_material = JSON.stringify(payload);
  root.add(new THREE.Mesh(new THREE.BufferGeometry(), eye), new THREE.Mesh(new THREE.BufferGeometry(), skin));
  const texture = new THREE.Texture();
  installEyeMaterials(root, () => texture);
  assert.equal(eye.solarisEyeTexture, texture);
  assert.equal(skin.solarisEyeTexture, undefined);
  const shader = { uniforms: {}, vertexShader: '#include <uv_vertex>', fragmentShader: '#include <map_fragment>' };
  eye.onBeforeCompile(shader, {});
  assert.match(shader.fragmentShader, /1\.0 - eyeControl\.a/);
  assert.match(shader.fragmentShader, /eyeHighlightUv/);
  assert.deepEqual(shader.uniforms.solarisEyePosition.value.toArray(), [.5, .55]);
});

test('rejects absent or invalid parameters instead of inventing them', () => {
  assert.equal(eyeHighlightParameters({ ...payload, scalars: {} }), null);
  assert.equal(eyeHighlightParameters({ ...payload, scalars: { ...payload.scalars, HeightLightScale: 0 } }), null);
  assert.equal(eyeHighlightParameters({ ...payload, textures: { EM: { data_base64: 'bad!' } } }), null);
});
