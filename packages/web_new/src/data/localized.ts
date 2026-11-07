export function localizedText(value: unknown, fallback = ''): string {
  if (typeof value === 'string') {
    const text = value.trim();
    return text && !isSourcePlaceholder(text) ? text : fallback;
  }
  if (value && typeof value === 'object') {
    const record = value as { content?: unknown; inline_text?: unknown };
    for (const content of [record.content, record.inline_text]) {
      if (typeof content === 'string' && content.trim() && !isSourcePlaceholder(content)) return content;
    }
  }
  return fallback;
}

function isSourcePlaceholder(value: string): boolean {
  return value.toLocaleLowerCase().includes('please contact our customer service');
}
