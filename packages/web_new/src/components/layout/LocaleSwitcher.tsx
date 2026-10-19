'use client';

import { Globe2 } from 'lucide-react';
import { Select } from '../ui/Select';

import { APP_SETTINGS } from '../../config/settings';
import { availableLocales } from '../../data/locales';
import { useLocale } from '../../hooks/useLocale';

export function LocaleSwitcher() {
  const locale = useLocale();
  return (
    <div className="site-locale">
      <Globe2 size={17} aria-hidden="true" />
      <Select
        label="Archive language"
        value={locale}
        options={availableLocales.map(({ code, label }) => ({ value: code, label }))}
        onChange={(value) => {
          localStorage.setItem(APP_SETTINGS.storage.locale, value);
          window.dispatchEvent(new Event('locale-change'));
        }}
      />
    </div>
  );
}
