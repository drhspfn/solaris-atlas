export function localizedText(value: unknown, fallback = ''): string {
  if (typeof value === 'string') {
    const text = value.trim();
    return text && !isSourcePlaceholder(text) ? text : fallback;
  }
  if (value && typeof value === 'object' && 'content' in value) {
    const content = (value as { content?: unknown }).content;
    if (typeof content === 'string' && content.trim() && !isSourcePlaceholder(content)) {
      return content;
    }
  }
  return fallback;
}

function isSourcePlaceholder(value: string): boolean {
  return value.toLocaleLowerCase().includes('please contact our customer service');
}
