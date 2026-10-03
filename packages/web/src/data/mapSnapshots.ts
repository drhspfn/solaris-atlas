type MapSnapshot = {
  id: number;
  game_map_id: number;
  game_version: string;
  layer: string;
};

/** One latest available dataset per region/layer; floors belong to its asset job. */
export function latestMapRoots<T extends MapSnapshot>(maps: readonly T[]): T[] {
  const latest = new Map<string, T>();
  const candidates = maps
    .filter((map) => !map.layer.startsWith('floor:'))
    .sort(
      (a, b) =>
        b.game_version.localeCompare(a.game_version, undefined, { numeric: true }) || b.id - a.id,
    );
  for (const map of candidates) {
    const key = `${map.game_map_id}:${map.layer}`;
    if (!latest.has(key)) latest.set(key, map);
  }
  return [...latest.values()].sort(
    (a, b) =>
      a.game_map_id - b.game_map_id || a.layer.localeCompare(b.layer, undefined, { numeric: true }),
  );
}
