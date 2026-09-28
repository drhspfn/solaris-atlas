import { useState } from "react";
import { ChevronDown, Globe2 } from "lucide-react";

export function LocaleSwitcher() {
  const [locale, setLocale] = useState(
    localStorage.getItem("wuwa-locale") || "en",
  );
  return (
    <label className="locale">
      <Globe2 size={15} />
      <select
        value={locale}
        onChange={(e) => {
          setLocale(e.target.value);
          localStorage.setItem("wuwa-locale", e.target.value);
          window.dispatchEvent(new Event("locale-change"));
        }}
      >
        <option value="en">EN</option>
        <option value="ru">RU</option>
        <option value="zh">中文</option>
      </select>
      <ChevronDown size={12} />
    </label>
  );
}
