type CollectionMarker = {
  entity_id: number;
  category: string;
  metadata: { names?: Record<string, string> };
};

/** Collectible is also used for respawning plants in older map imports. */
export function canMarkFound(marker: CollectionMarker): boolean {
  if (marker.category === 'chest') return true;
  if (marker.category !== 'collectible') return false;
  const name = marker.metadata.names?.en?.trim() ?? '';
  return /^(?:sonance casket(?:\s*[:(].*)?|windchimer|unclaimed rafter kite)$/i.test(name);
}

/** Source placement identity survives new asset jobs and database marker IDs. */
export function mapProgressKey(
  gameMapId: number,
  marker: Pick<CollectionMarker, 'entity_id'>,
): string {
  return `${gameMapId}:${marker.entity_id}`;
}

export type MapProgress = {
  found: ReadonlySet<string>;
  showFound: boolean;
  storageError: boolean;
};
type ProgressStorage = Pick<Storage, 'getItem' | 'setItem'>;
const validKey = (key: unknown): key is string =>
  typeof key === 'string' &&
  /^\d+:\d+$/.test(key) &&
  key.split(':').every((part) => Number.isSafeInteger(Number(part)));

export function readMapProgress(saved: string | null): MapProgress {
  const empty = { found: new Set<string>(), showFound: false, storageError: false };
  try {
    const value = JSON.parse(saved ?? 'null');
    if (!value || value.version !== 1 || !Array.isArray(value.found)) return empty;
    return {
      found: new Set<string>(value.found.filter(validKey)),
      showFound: value.showFound === true,
      storageError: false,
    };
  } catch {
    return empty;
  }
}

export function createMapProgressStore(storage: ProgressStorage | null, storageKey: string) {
  const listeners = new Set<() => void>();
  function load(): MapProgress {
    try {
      if (!storage) throw new Error('Storage unavailable');
      return readMapProgress(storage.getItem(storageKey));
    } catch {
      return { ...readMapProgress(null), storageError: true };
    }
  }
  let snapshot = load();
  const notify = () => listeners.forEach((listener) => listener());
  function update(change: (current: MapProgress) => MapProgress) {
    // Read before writing so another tab's unrelated marks are retained.
    const latest = snapshot.storageError ? snapshot : load();
    snapshot = change(latest.storageError ? snapshot : latest);
    try {
      if (!storage) throw new Error('Storage unavailable');
      storage.setItem(
        storageKey,
        JSON.stringify({ version: 1, found: [...snapshot.found], showFound: snapshot.showFound }),
      );
      snapshot = { ...snapshot, storageError: false };
    } catch {
      snapshot = { ...snapshot, storageError: true };
    }
    notify();
  }
  return {
    getSnapshot: () => snapshot,
    subscribe: (listener: () => void) => {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    refresh: () => {
      if (snapshot.storageError) return;
      snapshot = load();
      notify();
    },
    setFound: (key: string, found: boolean) => {
      if (!validKey(key)) return;
      update((current) => {
        const next = new Set(current.found);
        if (found) next.add(key);
        else next.delete(key);
        return { ...current, found: next };
      });
    },
    setShowFound: (showFound: boolean) => update((current) => ({ ...current, showFound })),
  };
}
