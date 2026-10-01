export const availableLocales = [
  { code: "en", label: "English" },
  { code: "ja", label: "日本語" },
  { code: "zh-Hans", label: "简体中文" },
  { code: "zh-Hant", label: "繁體中文" },
  { code: "ko", label: "한국어" },
  { code: "de", label: "Deutsch" },
  { code: "es", label: "Español" },
  { code: "fr", label: "Français" },
] as const;

export function normalizeLocale(value: string | null): string {
  if (value === "zh" || value === "zh-CN" || value === "cn") return "zh-Hans";
  if (value === "zh-TW") return "zh-Hant";
  if (value === "jp") return "ja";
  if (value && availableLocales.some((locale) => locale.code === value)) return value;
  return "en";
}
