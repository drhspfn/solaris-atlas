import { cache } from 'react';

const SERVER_READ_TIMEOUT_MS = 15_000;

const serverBackendBase = (
  process.env.INTERNAL_API_BASE ||
  process.env.API_PROXY_TARGET ||
  'http://localhost:8000'
).replace(/\/$/, '');

/**
 * Fetch data on the Next.js server directly from the backend API.
 * Defaults to ISR caching (revalidate: 60) for maximum speed and SEO prerendering.
 */
type ServerApiOptions = {
  revalidate?: number | false;
  tags?: string[];
  headers?: Record<string, string>;
};

export function serverApi<T>(path: string, options: ServerApiOptions = {}): Promise<T | null> {
  // Primitive arguments share a read across metadata and page, even with fresh option objects.
  return readServerApi(path, JSON.stringify(options)) as Promise<T | null>;
}

const readServerApi = cache(async function readServerApi(
  path: string,
  serializedOptions: string,
): Promise<unknown | null> {
  const options: ServerApiOptions = JSON.parse(serializedOptions);
  const normalizedPath = path.startsWith('/') ? path : `/${path}`;
  const cleanPath = normalizedPath.startsWith('/api')
    ? normalizedPath.replace(/^\/api/, '')
    : normalizedPath;
  const url = `${serverBackendBase}${cleanPath}`;

  try {
    const fetchOptions: RequestInit & { next?: { revalidate?: number | false; tags?: string[] } } = {
      signal: AbortSignal.timeout(SERVER_READ_TIMEOUT_MS),
      headers: {
        Accept: 'application/json',
        ...(options.headers || {}),
      },
    };

    if (options.revalidate !== undefined || options.tags) {
      fetchOptions.next = {
        revalidate: options.revalidate,
        tags: options.tags,
      };
    } else {
      // Default to 60 seconds revalidation for public data
      fetchOptions.next = { revalidate: 60 };
    }

    const res = await fetch(url, fetchOptions);
    if (!res.ok) {
      return null;
    }
    return await res.json();
  } catch (error) {
    console.error(`serverApi failed for ${url}:`, error);
    return null;
  }
});
