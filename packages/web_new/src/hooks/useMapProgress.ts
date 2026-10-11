import { useEffect, useSyncExternalStore } from 'react';

import { APP_SETTINGS } from '../config/settings';
import { createMapProgressStore } from '../state/mapProgress';

let store: ReturnType<typeof createMapProgressStore> | undefined;
function getStore() {
  if (!store) {
    let storage: Storage | null = null;
    try {
      storage = window.localStorage;
    } catch {
      /* Progress remains usable for this session. */
    }
    store = createMapProgressStore(storage, APP_SETTINGS.storage.mapProgress);
  }
  return store;
}

export function useMapProgress() {
  const progress = getStore();
  const snapshot = useSyncExternalStore(progress.subscribe, progress.getSnapshot);
  useEffect(() => {
    const refresh = (event: StorageEvent) => {
      if (event.key === APP_SETTINGS.storage.mapProgress || event.key === null) progress.refresh();
    };
    window.addEventListener('storage', refresh);
    return () => window.removeEventListener('storage', refresh);
  }, [progress]);
  return { ...snapshot, setFound: progress.setFound, setShowFound: progress.setShowFound };
}
