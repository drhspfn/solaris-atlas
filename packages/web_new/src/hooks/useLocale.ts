'use client';

import { useEffect, useState } from 'react';

import { APP_SETTINGS } from '../config/settings';
import { normalizeLocale } from '../data/locales';

export function useLocale() {
  const [locale, setLocale] = useState<string>(() => {
    if (typeof window === 'undefined') return 'en';
    try {
      return normalizeLocale(localStorage.getItem(APP_SETTINGS.storage.locale));
    } catch {
      return 'en';
    }
  });

  useEffect(() => {
    const update = () => {
      try {
        setLocale(normalizeLocale(localStorage.getItem(APP_SETTINGS.storage.locale)));
      } catch {
        // fallback
      }
    };
    try {
      const stored = localStorage.getItem(APP_SETTINGS.storage.locale);
      const normalized = normalizeLocale(stored);
      if (stored !== normalized) localStorage.setItem(APP_SETTINGS.storage.locale, normalized);
      setLocale(normalized);
    } catch {
      // storage unavailable
    }
    window.addEventListener('locale-change', update);
    return () => window.removeEventListener('locale-change', update);
  }, []);

  return locale;
}
