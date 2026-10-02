import { useEffect, useState } from 'react';

import { type MapPreferences, readMapPreferences } from '../state/mapPreferences';

export function useMapPreferences(legacy: URLSearchParams, hiddenCategories: string) {
  const [preferences, setPreferences] = useState<MapPreferences>(() => {
    let saved: string | null = null;
    try {
      saved = localStorage.getItem('atlas-map-filters');
    } catch {
      /* Storage may be unavailable. */
    }
    return readMapPreferences(saved, legacy, hiddenCategories);
  });
  useEffect(() => {
    try {
      localStorage.setItem('atlas-map-filters', JSON.stringify(preferences));
    } catch {
      /* Keep in-memory preferences. */
    }
  }, [preferences]);
  return [preferences, setPreferences] as const;
}
