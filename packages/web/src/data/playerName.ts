import { APP_SETTINGS } from '../config/settings';
const STORAGE_KEY = APP_SETTINGS.storage.guestPlayerTitle;

// Rover's in-world titles and epithets, catalogued by the community wiki.
// Keep this list explicit: it is presentation-only and never changes source data.
export const ROVER_TITLES = [
  'Arbiter',
  'Astral Modulator',
  'Black Lamb',
  'Chief Steward of the Black Shores',
  'Laureate',
  "Cat's Eye",
  'Mighty God-Killer',
  'Righteous One',
  'Champion of the Great Agon',
  'Hero of Heroes',
  'The Unwritten One',
] as const;

function guestTitle(): string {
  if (typeof window === 'undefined') return 'Rover';
  try {
    const saved = window.localStorage.getItem(STORAGE_KEY);
    if (saved && ROVER_TITLES.includes(saved as (typeof ROVER_TITLES)[number])) {
      return saved;
    }
    const title = ROVER_TITLES[Math.floor(Math.random() * ROVER_TITLES.length)];
    window.localStorage.setItem(STORAGE_KEY, title);
    return title;
  } catch {
    return 'Rover';
  }
}

export function playerName(
  accountNickname?: string | null,
  mode: 'nickname' | 'rover_title' = 'nickname',
): string {
  if (mode === 'rover_title') return guestTitle();
  return accountNickname?.trim() || guestTitle();
}

export function interpolatePlayerName(text: string, name: string): string {
  return text.replace(/\{PlayerName\}/gi, name);
}
