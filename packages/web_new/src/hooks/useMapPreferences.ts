import { useEffect, useState } from 'react';

import { APP_SETTINGS } from '../config/settings';
import { type MapPreferences, readMapPreferences } from '../state/mapPreferences';

export function useMapPreferences(legacy: URLSearchParams, hiddenCategories: string) {
  const [preferences, setPreferences] = useState<MapPreferences>(() => {
    let saved: string | null = null;
    try {
      saved = localStorage.getItem(APP_SETTINGS.storage.mapFilters);
    } catch {
      /* Storage may be unavailable. */
    }
    return readMapPreferences(saved, legacy, hiddenCategories);
  });
  useEffect(() => {
    try {
      localStorage.setItem(APP_SETTINGS.storage.mapFilters, JSON.stringify(preferences));
    } catch {
      /* Keep in-memory preferences. */
    }
  }, [preferences]);
  return [preferences, setPreferences] as const;
}
