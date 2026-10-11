const filterKeys = ['map', 'q', 'floor', 'area', 'hide', 'opacity', 'unknown', 'hidden'] as const;
export type MapPreferences = Partial<Record<(typeof filterKeys)[number], string>>;

export function readMapPreferences(
  saved: string | null,
  legacy: URLSearchParams,
  hiddenCategories: string,
): MapPreferences {
  const result: MapPreferences = { hide: hiddenCategories };
  try {
    const value: unknown = JSON.parse(saved ?? '{}');
    if (value && typeof value === 'object' && !Array.isArray(value)) {
      for (const key of filterKeys) {
        const item = (value as Record<string, unknown>)[key];
        if (typeof item === 'string') result[key] = item;
      }
    }
  } catch {
    /* Ignore invalid saved preferences. */
  }
  for (const key of filterKeys) if (legacy.has(key)) result[key] = legacy.get(key)!;
  return result;
}

export function compactMapLink(params: URLSearchParams): URLSearchParams {
  const result = new URLSearchParams();
  for (const key of ['map', 'marker', 'item', 'source']) {
    const value = params.get(key);
    if (value && /^\d{1,16}$/.test(value)) result.set(key, value);
  }
  return result;
}
