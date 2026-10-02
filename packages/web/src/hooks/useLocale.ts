import { useEffect, useState } from 'react';

import { APP_SETTINGS } from '../config/settings';
import { normalizeLocale } from '../data/locales';

export function useLocale() {
  const [locale, setLocale] = useState(() =>
    normalizeLocale(localStorage.getItem(APP_SETTINGS.storage.locale)),
  );
  useEffect(() => {
    const update = () =>
      setLocale(normalizeLocale(localStorage.getItem(APP_SETTINGS.storage.locale)));
    const stored = localStorage.getItem(APP_SETTINGS.storage.locale);
    const normalized = normalizeLocale(stored);
    if (stored !== normalized) localStorage.setItem(APP_SETTINGS.storage.locale, normalized);
    window.addEventListener('locale-change', update);
    return () => window.removeEventListener('locale-change', update);
  }, []);
  return locale;
}
