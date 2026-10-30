import * as THREE from 'three';
import { evaluateScene, sceneDuration, type SkeletalScene, type PosedDraw } from './openScenePose';
import { atlasUVs, type SceneRegion } from './openSceneTexture';
export type OpenScene = SkeletalScene & {
  regions: Record<string, SceneRegion>;
  viewport?: number[];
};

export function createSceneRenderer(
  canvas: HTMLCanvasElement,
  data: OpenScene,
  images: Record<string, ImageBitmap>,
  animation: string,
  loop: boolean,
  onEnd: () => void,
  onError: (error: unknown) => void,
) {
  const renderer = new THREE.WebGLRenderer({
    canvas,
    alpha: true,
    antialias: true,
    premultipliedAlpha: true,
  });
  renderer.setClearColor(0x000000, 0);
  renderer.sortObjects = false;
  const scene = new THREE.Scene(),
    camera = new THREE.OrthographicCamera();
  camera.position.z = 1;
  camera.near = 0.1;
  camera.far = 10;
  const textures: Record<string, THREE.Texture> = {};
  for (const [name, image] of Object.entries(images)) {
    const texture = new THREE.Texture(image);
    texture.flipY = false;
    texture.generateMipmaps = false;
    texture.minFilter = texture.magFilter = THREE.LinearFilter;
    texture.needsUpdate = true;
    textures[name] = texture;
  }
  const meshes = new Map<number, THREE.Mesh<THREE.BufferGeometry, THREE.ShaderMaterial>>();
  const bindings = new Map<number, unknown>();
  const duration = sceneDuration(data, animation);
  let playing = false,
    disposed = false,
    elapsed = 0,
    frame = 0,
    last = 0,
    painted = 0;
  function meshFor(draw: PosedDraw) {
    const existing = meshes.get(draw.slot);
    if (existing && bindings.get(draw.slot) === draw.sourceAttachment) return existing;
    if (existing) {
      existing.geometry.dispose();
      existing.material.dispose();
      scene.remove(existing);
    }
    const region = data.regions[draw.path || draw.sourceAttachment.name];
    if (!region || !textures[region.page]) throw new Error('Scene texture binding is missing');
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute(
      'position',
      new THREE.BufferAttribute(new Float32Array((draw.vertices.length / 2) * 3), 3),
    );
    geometry.setAttribute('uv', new THREE.Float32BufferAttribute(atlasUVs(draw.uvs, region), 2));
    geometry.setIndex(draw.triangles);
    const material = new THREE.ShaderMaterial({
      uniforms: {
        image: { value: textures[region.page] },
        tint: { value: new THREE.Vector4() },
        sourcePma: { value: !!region.pma },
      },
      vertexShader:
        'varying vec2 texCoord; void main(){texCoord=uv;gl_Position=projectionMatrix*modelViewMatrix*vec4(position,1.0);}',
      fragmentShader:
        'uniform sampler2D image; uniform vec4 tint; uniform bool sourcePma; varying vec2 texCoord; void main(){vec4 pixel=texture2D(image,texCoord);vec3 rgb=(sourcePma?pixel.rgb:pixel.rgb*pixel.a)*tint.rgb*tint.a;gl_FragColor=vec4(rgb,pixel.a*tint.a);}',
      side: THREE.DoubleSide,
      transparent: true,
      depthTest: false,
      depthWrite: false,
      toneMapped: false,
      blending: THREE.CustomBlending,
      blendEquation: THREE.AddEquation,
      blendSrc: draw.blend === 2 ? THREE.DstColorFactor : THREE.OneFactor,
      blendDst:
        draw.blend === 1
          ? THREE.OneFactor
          : draw.blend === 3
            ? THREE.OneMinusSrcColorFactor
            : THREE.OneMinusSrcAlphaFactor,
      blendSrcAlpha: THREE.OneFactor,
      blendDstAlpha: THREE.OneMinusSrcAlphaFactor,
    });
    const mesh = new THREE.Mesh(geometry, material);
    mesh.frustumCulled = false;
    mesh.renderOrder = draw.slot;
    meshes.set(draw.slot, mesh);
    bindings.set(draw.slot, draw.sourceAttachment);
    scene.add(mesh);
    // Explicit slot order is stable even when an attachment is replaced.
    scene.children.sort((a, b) => a.renderOrder - b.renderOrder);
    return mesh;
  }
  function draw(time: number) {
    for (const mesh of meshes.values()) mesh.visible = false;
    for (const draw of evaluateScene(data, animation, time, { loop }).draws) {
      const mesh = meshFor(draw);
      mesh.visible = draw.color[3] > 0;
      const positions = mesh.geometry.getAttribute('position') as THREE.BufferAttribute;
      for (let i = 0; i < draw.vertices.length; i += 2)
        positions.setXYZ(i / 2, draw.vertices[i], draw.vertices[i + 1], 0);
      positions.needsUpdate = true;
      mesh.material.uniforms.tint.value.fromArray(draw.color);
    }
    renderer.render(scene, camera);
  }
  function resize() {
    if (disposed) return;
    const width = Math.max(1, canvas.clientWidth),
      height = Math.max(1, canvas.clientHeight);
    renderer.setPixelRatio(
      Math.min(devicePixelRatio || 1, 1.5, Math.sqrt(4000000 / (width * height))),
    );
    renderer.setSize(width, height, false);
    const [x, y, w, h] = data.viewport || data.bounds;
    // Keep the central illustration legible above the dialogue on narrow readers.
    const scale = width < 600 ? Math.min(w / width, h / 230) : w / width,
      cx = x + w / 2;
    camera.left = cx - (width * scale) / 2;
    camera.right = cx + (width * scale) / 2;
    camera.top = y + h + (width < 600 ? 64 * scale : 0);
    camera.bottom = camera.top - height * scale;
    camera.updateProjectionMatrix();
    draw(elapsed);
  }
  function tick(now: number) {
    if (disposed || !playing) return;
    elapsed += Math.min((now - last) / 1000, 0.1);
    last = now;
    if (now - painted >= 1000 / 30) {
      try {
        draw(elapsed);
      } catch (error) {
        playing = false;
        onError(error);
        return;
      }
      painted = now;
    }
    if (!loop && elapsed >= duration) {
      playing = false;
      onEnd();
      return;
    }
    frame = requestAnimationFrame(tick);
  }
  const observer = new ResizeObserver(resize);
  function dispose() {
    disposed = true;
    cancelAnimationFrame(frame);
    observer.disconnect();
    for (const mesh of meshes.values()) {
      mesh.geometry.dispose();
      mesh.material.dispose();
    }
    for (const texture of Object.values(textures)) texture.dispose();
    renderer.dispose();
    renderer.forceContextLoss();
  }
  observer.observe(canvas);
  try {
    resize();
  } catch (error) {
    dispose();
    throw error;
  }
  return {
    setPlaying(value: boolean) {
      if (disposed || playing === value) return;
      playing = value;
      cancelAnimationFrame(frame);
      if (playing) {
        if (!loop && elapsed >= duration) elapsed = 0;
        last = performance.now();
        frame = requestAnimationFrame(tick);
      }
    },
    drawAt(time: number) {
      elapsed = time;
      draw(time);
    },
    getTime() {
      return elapsed;
    },
    dispose,
  };
}
