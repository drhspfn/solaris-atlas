import * as THREE from 'three';

// The EM highlight is one confirmed part of the game's SM5 eye material.
// Parallax and the lighting-dependent SDF pass require further reconstruction.
export function eyeHighlightParameters(payload) {
  if (payload?.format !== 'solaris-eye-material-v1') return null;
  const data = payload.textures?.EM?.data_base64;
  const s = payload.scalars || {}, c = payload.colors?.HeightLight_Color;
  const values = [s.HeightLight_PositionX, s.HeightLight_PositionY,
    s.HeightLight_WidthX, s.HeightLight_WidthY, s.HeightLightScale,
    c?.R, c?.G, c?.B, c?.A];
  if (typeof data !== 'string' || !/^[A-Za-z0-9+/=]+$/.test(data)
      || !values.every(Number.isFinite) || values[2] === 0 || values[3] === 0 || values[4] <= 0) return null;
  return { data, values };
}

export function installEyeMaterials(root, makeTexture = url => new THREE.TextureLoader().load(url)) {
  const seen = new Set();
  root.traverse(node => {
    for (const material of [node.material || []].flat()) {
      if (!material || seen.has(material) || material.solarisEyeTexture || !material.isMeshStandardMaterial) continue;
      seen.add(material);
      let payload;
      try { payload = JSON.parse(material.userData?.solaris_eye_material); } catch { continue; }
      const input = eyeHighlightParameters(payload);
      if (!input) continue;
      const texture = makeTexture(`data:image/png;base64,${input.data}`);
      texture.flipY = false;
      texture.colorSpace = THREE.NoColorSpace;
      material.solarisEyeTexture = texture; // Also disposed with the other material textures.
      const [x, y, width, height, scale, r, g, b, a] = input.values;
      const previous = material.onBeforeCompile;
      material.onBeforeCompile = function(shader, renderer) {
        previous.call(this, shader, renderer);
        Object.assign(shader.uniforms, {
          solarisEyeMap: { value: texture }, solarisEyePosition: { value: new THREE.Vector2(x, y) },
          solarisEyeWidth: { value: new THREE.Vector2(width, height) }, solarisEyeScale: { value: scale },
          solarisEyeColor: { value: new THREE.Vector4(r, g, b, a) },
        });
        shader.vertexShader = 'varying vec2 solarisEyeUv;\n' + shader.vertexShader.replace(
          '#include <uv_vertex>', '#include <uv_vertex>\nsolarisEyeUv = uv;');
        shader.fragmentShader = 'varying vec2 solarisEyeUv;\nuniform sampler2D solarisEyeMap;\nuniform vec2 solarisEyePosition, solarisEyeWidth;\nuniform float solarisEyeScale;\nuniform vec4 solarisEyeColor;\n' + shader.fragmentShader.replace(
          '#include <map_fragment>', `#include <map_fragment>
          vec4 eyeControl = texture2D(solarisEyeMap, solarisEyeUv);
          vec2 eyeHighlightUv = (solarisEyeUv - solarisEyePosition) / solarisEyeWidth + vec2(0.5);
          float eyeFade = smoothstep(0.0, 1.0, clamp((1.0 - eyeControl.a) / solarisEyeScale, 0.0, 1.0));
          diffuseColor.rgb += texture2D(solarisEyeMap, eyeHighlightUv).r * eyeFade * solarisEyeColor.rgb * solarisEyeColor.a;`);
      };
      material.customProgramCacheKey = () => 'solaris-eye-sm5-em-v1';
      material.needsUpdate = true;
    }
  });
}
