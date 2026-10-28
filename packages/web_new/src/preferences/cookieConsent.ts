export const COOKIE_CHOICE_KEY = "solaris-atlas:cookie-choice:v1";
export const OPEN_COOKIE_SETTINGS = "solaris:open-cookie-settings";

export type CookieChoice = "essential" | "all";

export function readCookieChoice(
  storage: Pick<Storage, "getItem">,
): CookieChoice | null {
  try {
    const value = storage.getItem(COOKIE_CHOICE_KEY);
    return value === "essential" || value === "all" ? value : null;
  } catch {
    return null;
  }
}

export function saveCookieChoice(
  storage: Pick<Storage, "setItem">,
  choice: CookieChoice,
): boolean {
  try {
    storage.setItem(COOKIE_CHOICE_KEY, choice);
    return true;
  } catch {
    return false;
  }
}
