/** Atlas mapping follows Spinal MIT geometry contracts; see THIRD_PARTY_NOTICES.md. */
export type SceneRegion = {
  page: string;
  size: number[];
  bounds: number[];
  offsets?: number[];
  rotate?: number;
  pma?: boolean;
};
export function atlasUVs(values: number[], region: SceneRegion): number[] {
  const [x, y, w, h] = region.bounds,
    [pw, ph] = region.size;
  const [left, bottom, ow, oh] = region.offsets || [0, 0, w, h];
  if (
    ![x, y, w, h, pw, ph, left, bottom, ow, oh].every(Number.isFinite) ||
    Math.min(w, h, pw, ph, ow, oh) <= 0
  )
    throw new Error('Invalid scene atlas dimensions');
  const rotation = region.rotate || 0;
  if (![0, 90, 180, 270].includes(rotation)) throw new Error('Unsupported scene atlas rotation');
  const rw = rotation % 180 ? h : w,
    rh = rotation % 180 ? w : h;
  const corners = [
    [x / pw, (y + rh) / ph],
    [x / pw, y / ph],
    [(x + rw) / pw, y / ph],
    [(x + rw) / pw, (y + rh) / ph],
  ];
  const shift = rotation / 90;
  const [bl, tl, tr, br] = corners.map((_, i) => corners[(i + 4 - shift) % 4]);
  const result = [];
  for (let i = 0; i < values.length; i += 2) {
    const u = (values[i] * ow - left) / w,
      v = (values[i + 1] * oh - (oh - bottom - h)) / h;
    const top = [tl[0] + (tr[0] - tl[0]) * u, tl[1] + (tr[1] - tl[1]) * u];
    const down = [bl[0] + (br[0] - bl[0]) * u, bl[1] + (br[1] - bl[1]) * u];
    result.push(top[0] + (down[0] - top[0]) * v, top[1] + (down[1] - top[1]) * v);
  }
  return result;
}
