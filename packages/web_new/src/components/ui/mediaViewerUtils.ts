export function mediaViewerIndex(
  index: number,
  delta: number,
  count: number,
): number {
  return Math.max(0, Math.min(count - 1, index + delta));
}

export function mediaViewerZoom(value: number): number {
  return Math.max(1, Math.min(4, Math.round(value * 100) / 100));
}

export function mediaViewerUrl(src: string): string | undefined {
  try {
    const url = new URL(src, "https://local.invalid");
    return url.protocol === "https:" || url.protocol === "http:"
      ? src
      : undefined;
  } catch {
    return undefined;
  }
}
