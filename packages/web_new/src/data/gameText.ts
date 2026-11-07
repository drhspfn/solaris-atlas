import { GAME_TEXT_COLORS } from './gameTextColors';
import { localizedText } from './localized';

export type PlayerDisplay = {
  name: string;
  gender?: 'male' | 'female' | 'ask';
  colorMode: 'accent' | 'custom';
  customColor: string;
};
export type TextRun = { text: string; color?: string; italic?: boolean; bold?: boolean; underline?: boolean; strike?: boolean };

function resolveColor(value: string): string | undefined {
  const name = value.trim().replace(/^(["'])(.*)\1$/, '$2');
  if (/^#(?:[\da-f]{3}|[\da-f]{4}|[\da-f]{6}|[\da-f]{8})$/i.test(name)) return name;
  return Object.hasOwn(GAME_TEXT_COLORS, name) ? GAME_TEXT_COLORS[name] : undefined;
}

/** Select authored alternatives before formatting; nested player-name tokens stay intact. */
export function genderText(value: string, gender: PlayerDisplay['gender'] = 'male', nesting = 0): string {
  if (nesting >= 32) return value;
  let result = '', cursor = 0;
  while (cursor < value.length) {
    const start = value.indexOf('{', cursor);
    if (start < 0) return result + value.slice(cursor);
    result += value.slice(cursor, start);
    let end = start + 1, depth = 1;
    for (; end < value.length && depth; end++) {
      if (value[end] === '{') depth++;
      else if (value[end] === '}') depth--;
    }
    if (depth) return result + value.slice(start);
    const body = value.slice(start + 1, end - 1);
    const first = /^\s*(Male|Female)\s*=/i.exec(body);
    const choices = new Map<string, string>();
    if (first) {
      let field = first[1].toLowerCase(), offset = first[0].length, inner = 0;
      for (let i = offset; i < body.length; i++) {
        if (body[i] === '{') inner++;
        else if (body[i] === '}') inner--;
        else if (body[i] === ';' && inner === 0) {
          const next = /^;\s*(Male|Female)\s*=/i.exec(body.slice(i));
          if (next) {
            choices.set(field, body.slice(offset, i));
            field = next[1].toLowerCase(); offset = i + next[0].length; i = offset - 1;
          }
        }
      }
      choices.set(field, body.slice(offset));
    }
    if (choices.has('male') && choices.has('female')) {
      // Until a Rover is selected, use the authored male alternative consistently.
      result += genderText(choices.get(gender === 'female' ? 'female' : 'male')!.trim(), gender, nesting + 1);
    } else result += `{${genderText(body, gender, nesting + 1)}}`;
    cursor = end;
  }
  return result;
}

/** Interpret only supported game wrappers. Unknown markup remains escaped text. */
export function gameTextRuns(value: string, gender?: PlayerDisplay['gender']): TextRun[] {
  const source = genderText(value, gender);
  const runs: TextRun[] = [], colors: (string | undefined)[] = [];
  const marks = { i: 0, b: 0, u: 0, s: 0 };
  const tags = /<(\/?)(color|ano|i|b|u|s|br)(?:\s*=\s*("[^"]*"|'[^']*'|[^<>]*))?\s*\/?>/gi;
  let offset = 0;
  const append = (text: string) => {
    if (text) runs.push({ text, color: colors.at(-1), ...(marks.i ? { italic: true } : {}),
      ...(marks.b ? { bold: true } : {}), ...(marks.u ? { underline: true } : {}), ...(marks.s ? { strike: true } : {}) });
  };
  for (const match of source.matchAll(tags)) {
    append(source.slice(offset, match.index));
    const closing = !!match[1], tag = match[2].toLowerCase();
    if (tag === 'color') {
      if (closing) colors.pop(); else colors.push(resolveColor(match[3] || '') ?? colors.at(-1));
    } else if (tag === 'br' && !closing) append('\n');
    else if (tag in marks) {
      const key = tag as keyof typeof marks;
      marks[key] = Math.max(0, marks[key] + (closing ? -1 : 1));
    }
    offset = match.index + match[0].length;
  }
  append(source.slice(offset));
  return runs;
}

/** The same presentation as PlayerText for search, titles and accessible labels. */
export function gameTextPlainText(value: unknown, display?: PlayerDisplay, fallback = ''): string {
  return gameTextRuns(localizedText(value, fallback), display?.gender).map(run => run.text).join('')
    .replace(/\{PlayerName\}/gi, () => display?.name || 'Rover');
}
