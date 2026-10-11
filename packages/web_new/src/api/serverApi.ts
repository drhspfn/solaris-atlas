import { ApiError } from './client';

const serverBackendBase = (
  process.env.INTERNAL_API_BASE ||
  process.env.API_PROXY_TARGET ||
  'http://localhost:8000'
).replace(/\/$/, '');

/**
 * Fetch data on the Next.js server directly from the backend API.
 * Defaults to ISR caching (revalidate: 60) for maximum speed and SEO prerendering.
 */
export async function serverApi<T>(
  path: string,
  options: {
    revalidate?: number | false;
    tags?: string[];
    headers?: Record<string, string>;
  } = {},
): Promise<T | null> {
  const normalizedPath = path.startsWith('/') ? path : `/${path}`;
  const cleanPath = normalizedPath.startsWith('/api')
    ? normalizedPath.replace(/^\/api/, '')
    : normalizedPath;
  const url = `${serverBackendBase}${cleanPath}`;

  try {
    const fetchOptions: RequestInit & { next?: { revalidate?: number | false; tags?: string[] } } = {
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
    return (await res.json()) as T;
  } catch (error) {
    console.error(`serverApi failed for ${url}:`, error);
    return null;
  }
}
