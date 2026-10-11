export function resolveApiUrl(base: string, path: string): string {
  if (/^https?:\/\//i.test(path) || path.startsWith('//')) return path;
  return `${base.replace(/\/$/, '')}/${path.replace(/^\//, '')}`;
}
