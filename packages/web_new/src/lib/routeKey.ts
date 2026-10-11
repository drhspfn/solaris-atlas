/** Convert a URL segment to the canonical ID before passing it to API clients. */
export function decodeRouteKey(value: string): string {
  try {
    return decodeURIComponent(value);
  } catch {
    // Keep malformed escapes as data; encodeURIComponent still handles them
    // safely when building the API URL.
    return value;
  }
}
