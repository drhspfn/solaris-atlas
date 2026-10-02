import { ChevronDown, Globe2 } from 'lucide-react';

import { APP_SETTINGS } from '../../config/settings';
import { availableLocales } from '../../data/locales';
import { useLocale } from '../../hooks/useLocale';

export function LocaleSwitcher() {
  const locale = useLocale();
  return (
    <label className="locale">
      <Globe2 size={15} />
      <select
        value={locale}
        onChange={(e) => {
          localStorage.setItem(APP_SETTINGS.storage.locale, e.target.value);
          window.dispatchEvent(new Event('locale-change'));
        }}
      >
        {availableLocales.map(({ code, label }) => (
          <option key={code} value={code}>
            {label}
          </option>
        ))}
      </select>
      <ChevronDown size={12} />
    </label>
  );
}
