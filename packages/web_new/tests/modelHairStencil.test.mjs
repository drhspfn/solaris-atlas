import { test } from 'node:test';
import assert from 'node:assert/strict';
import * as THREE from 'three';
import { installHairStencil } from '../src/components/models/engine/hair-stencil.js';

test('uses authored face masks only with an explicitly linked hair pass', () => {
  const original = THREE.TextureLoader.prototype.load;
  THREE.TextureLoader.prototype.load = () => new THREE.Texture();
  try {
    const root = new THREE.Group();
    const face = new THREE.SkinnedMesh(new THREE.BufferGeometry(), new THREE.MeshBasicMaterial());
    const hair = new THREE.SkinnedMesh(new THREE.BufferGeometry(), new THREE.MeshBasicMaterial());
    face.material.name = 'Face'; hair.material.name = 'Bangs';
    root.add(face, hair);
    root.userData.solaris_hair_stencil = JSON.stringify({ format: 'solaris-hair-stencil-v1',
      masks: [{ target_material: '/Game/Face', mime_type: 'image/png', data_base64: 'YWJj' }],
      readers: [{ material: '/Game/Bangs' }] });
    assert.equal(installHairStencil(root), true);
    assert.equal(root.children.length, 4);
    assert.equal(hair.material.stencilFunc, THREE.NotEqualStencilFunc);
    assert.equal(root.children[3].material.stencilFunc, THREE.EqualStencilFunc);
    assert.equal(root.children[3].material.opacity, 0.38);
  } finally { THREE.TextureLoader.prototype.load = original; }
});

test('leaves old models unchanged when the hair reader is unknown', () => {
  const root = new THREE.Group();
  root.userData.solaris_hair_stencil = JSON.stringify({ format: 'solaris-hair-stencil-v1', masks: [] });
  assert.equal(installHairStencil(root), false);
  assert.equal(root.children.length, 0);
});
