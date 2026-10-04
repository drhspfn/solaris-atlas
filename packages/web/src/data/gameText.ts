/** Remove supported game presentation wrappers, preserving their spoken text.
 * Unknown tags stay literal: this is not HTML and must never be injected as HTML.
 */
export function gameText(value: string): string {
  return value.replace(
    /<(?:color|ano)(?:\s*=\s*(?:"[^"]*"|'[^']*'|[^<>]*))?\s*>|<\/(?:color|ano)\s*>/gi,
    '',
  );
}
