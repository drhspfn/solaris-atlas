import * as THREE from 'three';

// Experimental PS 152039 fragment. B must already include MainTex parallax and EM highlight.
// UV convention, texture color space/sampler, time, vertex green and runtime overrides
// cannot be inferred from the exported material. This is not the final lit eye color.
const scalarNames = `ScaleTransition_Speed EyeLight_TransitionScale UseEyeScaleTransition
LightDifferentScale LightShakeSpeed LightShakeScale LightShakPositionX LightShakPositionY
ShapePosition01X ShapePosition01Y RotationAnlgle01 ShapeScaleX ShapeScaleY LightNormlScale
LightPositionX LightPositionY EdgeWidth UseProLight SecondLight_WidthX SecondLight_WidthY
SecondLight_PositionX SecondLight_PositionY SecondLight_Position_02X SecondLight_Position_02Y
RotateAngle LightDisable LightEmissiveInt`.split(/\s+/);
const colorNames = ['EdgeColor', 'HeightLightColor', 'OutLineColor'];
const sat = x => Math.min(1, Math.max(0, x));
const mix = (a, b, t) => a + (b - a) * t;

function validInputs(p, colors) {
  return scalarNames.every(k => Number.isFinite(p?.[k]))
    && colorNames.every(k => ['R', 'G', 'B', 'A'].every(c => Number.isFinite(colors?.[k]?.[c])))
    && p.EdgeWidth !== 2;
}

// Numerical reference. sampleHeightMap receives UE UV and must return linear RGB.
export function evaluateEyeSm5({ uv, vertexGreen, time, base, scalars: p, colors, sampleHeightMap }) {
  if (!validInputs(p, colors) || ![uv, base].every((v, i) => Array.isArray(v)
    && v.length === (i ? 3 : 2) && v.every(Number.isFinite)) || !Number.isFinite(vertexGreen)
    || !Number.isFinite(time) || typeof sampleHeightMap !== 'function') return null;
  const phase = (Math.sin(time * p.ScaleTransition_Speed * 6.283185) + 1) * .5;
  const shake = Math.sin(time * p.LightShakeSpeed * 6.283185) * p.LightShakeScale;
  const origin = [p.ShapePosition01X + p.LightShakPositionX * shake + p.LightPositionX,
    p.ShapePosition01Y + p.LightShakPositionY * shake + p.LightPositionY];
  const angle = p.RotationAnlgle01 * 6.283185, c = Math.cos(angle), s = Math.sin(angle);
  const dx = uv[0] - origin[0], dy = uv[1] - origin[1];
  const transition = p.UseEyeScaleTransition >= .5 ? 1 - phase * p.EyeLight_TransitionScale * .75 : 1;
  const radius = Math.hypot((c * dx - s * dy) * (p.ShapeScaleX * transition + p.LightNormlScale * 10) * p.LightDifferentScale,
    (s * dx + c * dy) * (p.ShapeScaleY * transition + p.LightNormlScale * 20) * p.LightDifferentScale);
  const core = sat((1 - 2 * radius) * 100000);
  const triangle = 1 - Math.abs(2 * sat((1 - radius * 1.538462) * 1.886792) - 1);
  const z = sat((triangle - (p.EdgeWidth * 1.22 - .22)) / (p.EdgeWidth + .22 - (p.EdgeWidth * 1.22 - .22)));
  const edgeMask = z * z * (3 - 2 * z);
  const procedural = base.map((b, i) => mix(b, mix(b + core, colors.EdgeColor['RGB'[i]], edgeMask), p.UseProLight));
  const mapScale = p.UseEyeScaleTransition >= .5 ? 1 + phase * p.EyeLight_TransitionScale : 1;
  const widths = [p.SecondLight_WidthX, p.SecondLight_WidthY].map(w => (.225 * w * mapScale - p.LightNormlScale) * p.LightDifferentScale);
  if (widths.some(w => w === 0 || !Number.isFinite(w))) return null;
  const pos = [p.SecondLight_PositionX + vertexGreen * p.SecondLight_Position_02X + p.LightShakPositionX * shake + p.LightPositionX + .05,
    p.SecondLight_PositionY + vertexGreen * p.SecondLight_Position_02Y + p.LightShakPositionY * shake + p.LightPositionY];
  const x = (uv[0] - pos[0]) / widths[0], y = (uv[1] - pos[1]) / widths[1];
  const mc = Math.cos(p.RotateAngle), ms = Math.sin(p.RotateAngle);
  const heightUv = [mc * x - ms * y + .5, ms * x + mc * y + .5];
  const h = sampleHeightMap(heightUv);
  if (!Array.isArray(h) || h.length < 3 || !h.slice(0, 3).every(Number.isFinite)) return null;
  const color = procedural.map((v, i) => mix(mix(mix(v, colors.HeightLightColor['RGB'[i]], h[0]),
    colors.OutLineColor['RGB'[i]] * colors.OutLineColor.A, h[1]), base[i], p.LightDisable));
  return { procedural, color, blueTerm: h[2] * p.LightEmissiveInt * (1 - p.LightDisable), heightUv, radius, edgeMask };
}

// Explicit opt-in diagnostic shader; does not modify a viewer, an existing material, or a texture.
// uvFlipV converts geometry UV to UE UV; textureFlipV maps UE UV to this texture's sampler convention.
export function createEyeSm5Experiment({ geometry, texture, base, scalars, colors, time, mipBias, uvFlipV, textureFlipV, output = 'color' }) {
  const uv = geometry?.getAttribute('uv'), color = geometry?.getAttribute('color_1');
  if (!validInputs(scalars, colors) || !uv || !color || color.itemSize < 3 || color.count !== uv.count
    || (!(color.array instanceof Float32Array) && !color.normalized) || !texture?.isTexture
    || !Array.isArray(base) || base.length !== 3 || !base.every(Number.isFinite)
    || !Number.isFinite(time) || !Number.isFinite(mipBias) || typeof uvFlipV !== 'boolean' || typeof textureFlipV !== 'boolean'
    || !['color', 'blueTerm'].includes(output)) return null;
  // Reject degenerate map widths at the requested time, rather than sample undefined coordinates.
  const check = evaluateEyeSm5({ uv: [0, 0], vertexGreen: 0, time, base, scalars, colors, sampleHeightMap: () => [0, 0, 0] });
  if (!check) return null;
  const uniforms = { eyeMap: { value: texture }, base: { value: new THREE.Vector3(...base) }, time: { value: time }, mipBias: { value: mipBias },
    uvFlipV: { value: uvFlipV }, textureFlipV: { value: textureFlipV } };
  for (const k of scalarNames) uniforms[k] = { value: scalars[k] };
  for (const k of colorNames) { const c = colors[k]; uniforms[k] = { value: new THREE.Vector4(c.R, c.G, c.B, c.A) }; }
  return new THREE.ShaderMaterial({ uniforms, toneMapped: false,
    vertexShader: `attribute vec4 color_1; varying vec2 eyeUV; varying float green;
      void main(){ eyeUV=uv; green=color_1.g; gl_Position=projectionMatrix*modelViewMatrix*vec4(position,1.0); }`,
    fragmentShader: `uniform sampler2D eyeMap; uniform vec3 base; uniform float time, mipBias;
      uniform bool uvFlipV, textureFlipV;
      uniform float ${scalarNames.join(', ')}; uniform vec4 ${colorNames.join(', ')};
      varying vec2 eyeUV; varying float green;
      void main(){
        vec2 u=vec2(eyeUV.x,uvFlipV ? 1.0-eyeUV.y : eyeUV.y);
        float phase=(sin(time*ScaleTransition_Speed*6.283185)+1.0)*0.5;
        float shake=sin(time*LightShakeSpeed*6.283185)*LightShakeScale;
        vec2 d=u-vec2(ShapePosition01X,ShapePosition01Y)-vec2(LightShakPositionX,LightShakPositionY)*shake-vec2(LightPositionX,LightPositionY);
        float a=RotationAnlgle01*6.283185; vec2 rot=vec2(cos(a)*d.x-sin(a)*d.y,sin(a)*d.x+cos(a)*d.y);
        float transition=UseEyeScaleTransition>=0.5 ? 1.0-phase*EyeLight_TransitionScale*0.75 : 1.0;
        float r=length(rot*(vec2(ShapeScaleX,ShapeScaleY)*transition+LightNormlScale*vec2(10.0,20.0))*LightDifferentScale);
        float core=clamp((1.0-2.0*r)*100000.0,0.0,1.0);
        float triangle=1.0-abs(2.0*clamp((1.0-r*1.538462)*1.886792,0.0,1.0)-1.0);
        float z=clamp((triangle-(EdgeWidth*1.22-0.22))/(EdgeWidth+0.22-(EdgeWidth*1.22-0.22)),0.0,1.0);
        float edge=z*z*(3.0-2.0*z);
        vec3 procedural=mix(base,mix(base+vec3(core),EdgeColor.rgb,edge),UseProLight);
        float mapScale=UseEyeScaleTransition>=0.5 ? 1.0+phase*EyeLight_TransitionScale : 1.0;
        vec2 width=(0.225*vec2(SecondLight_WidthX,SecondLight_WidthY)*mapScale-vec2(LightNormlScale))*LightDifferentScale;
        vec2 pos=vec2(SecondLight_PositionX,SecondLight_PositionY)+green*vec2(SecondLight_Position_02X,SecondLight_Position_02Y)
          +vec2(LightShakPositionX,LightShakPositionY)*shake+vec2(LightPositionX+0.05,LightPositionY);
        vec2 q=(u-pos)/width; float c=cos(RotateAngle), s=sin(RotateAngle);
        vec2 mapUV=vec2(c*q.x-s*q.y,s*q.x+c*q.y)+0.5;
        if(textureFlipV) mapUV.y=1.0-mapUV.y;
        vec3 h=texture2D(eyeMap,mapUV,mipBias).rgb;
        vec3 C3=mix(mix(mix(procedural,HeightLightColor.rgb,h.r),OutLineColor.rgb*OutLineColor.a,h.g),base,LightDisable);
        float blue=h.b*LightEmissiveInt*(1.0-LightDisable);
        gl_FragColor=vec4(${output === 'blueTerm' ? 'vec3(blue)' : 'C3'},1.0);
      }` });
}
