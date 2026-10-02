import { useEffect, useState } from 'react';

import { normalizeLocale } from '../data/locales';

export function useLocale() {
  const [locale, setLocale] = useState(() => normalizeLocale(localStorage.getItem('wuwa-locale')));
  useEffect(() => {
    const update = () => setLocale(normalizeLocale(localStorage.getItem('wuwa-locale')));
    const stored = localStorage.getItem('wuwa-locale');
    const normalized = normalizeLocale(stored);
    if (stored !== normalized) localStorage.setItem('wuwa-locale', normalized);
    window.addEventListener('locale-change', update);
    return () => window.removeEventListener('locale-change', update);
  }, []);
  return locale;
}
