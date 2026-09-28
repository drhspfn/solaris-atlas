import { useEffect, useState } from "react";
export function useLocale() {
  const [locale, setLocale] = useState(localStorage.getItem("wuwa-locale") || "en");
  useEffect(() => {
    const update = () => setLocale(localStorage.getItem("wuwa-locale") || "en");
    window.addEventListener("locale-change", update);
    return () => window.removeEventListener("locale-change", update);
  }, []);
  return locale;
}
