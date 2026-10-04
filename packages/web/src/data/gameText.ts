import { GAME_TEXT_COLORS } from './gameTextColors';

type TextRun = { text: string; color?: string };

function resolveColor(value: string): string | undefined {
  const name = value.trim().replace(/^(["'])(.*)\1$/, '$2');
  if (/^#(?:[\da-f]{3}|[\da-f]{4}|[\da-f]{6}|[\da-f]{8})$/i.test(name)) return name;
  return Object.hasOwn(GAME_TEXT_COLORS, name) ? GAME_TEXT_COLORS[name] : undefined;
}

/** Parse only game color/annotation wrappers. Unknown markup remains escaped text. */
export function gameTextRuns(value: string): TextRun[] {
  const runs: TextRun[] = [];
  const colors: (string | undefined)[] = [];
  const tags = /<(color|ano)(?:\s*=\s*("[^"]*"|'[^']*'|[^<>]*))?\s*>|<\/(color|ano)\s*>/gi;
  let offset = 0;
  const append = (text: string) => {
    if (text) runs.push({ text, color: colors.at(-1) });
  };
  for (const match of value.matchAll(tags)) {
    append(value.slice(offset, match.index));
    if (match[1]?.toLowerCase() === 'color')
      colors.push(resolveColor(match[2] || '') ?? colors.at(-1));
    else if (match[3]?.toLowerCase() === 'color') colors.pop();
    offset = match.index + match[0].length;
  }
  append(value.slice(offset));
  return runs;
}
