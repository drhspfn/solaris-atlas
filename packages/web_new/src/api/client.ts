import { APP_SETTINGS } from '../config/settings';
import { requestCredentials } from './requestPolicy';
import { resolveApiUrl } from './resolveApiUrl';
import { retryableFetch } from './retryableFetch';

const isBrowser = typeof window !== 'undefined';
const defaultClientBase = (process.env.NEXT_PUBLIC_API_BASE ?? APP_SETTINGS.api.defaultBase).replace(/\/$/, '');
const serverBackendBase = (process.env.INTERNAL_API_BASE || process.env.API_PROXY_TARGET || 'http://localhost:8000').replace(/\/$/, '');

export const apiBase = isBrowser ? defaultClientBase : serverBackendBase;

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly code?: string,
  ) {
    super(message);
  }
}

// Simple memory cache for GET requests
const requestCache = new Map<string, { data: any; expiry: number; promise?: Promise<any> }>();

export async function api<T>(
  path: string,
  options: {
    method?: string;
    body?: unknown;
    csrf?: boolean;
    signal?: AbortSignal;
    cacheTtl?: number;
    next?: { revalidate?: number | false; tags?: string[] };
  } = {},
): Promise<T> {
  const method = options.method ?? 'GET';
  const cacheKey = `${method}:${path}`;

  if (method === 'GET' && isBrowser) {
    const cached = requestCache.get(cacheKey);
    if (cached && cached.expiry > Date.now()) {
      if (cached.promise) return cached.promise;
      return cached.data;
    }
  }

  const fetchPromise = (async () => {
    const headers = new Headers({ Accept: 'application/json' });
    const binaryBody = typeof Blob !== 'undefined' && options.body instanceof Blob;
    if (options.body !== undefined) headers.set('Content-Type', binaryBody ? 'application/octet-stream' : 'application/json');
    if (isBrowser && (options.csrf ?? !['GET', 'HEAD', 'OPTIONS'].includes(method))) {
      try {
        const csrf = await fetch(`${defaultClientBase}/auth/csrf`, { credentials: 'include' });
        if (csrf.ok) {
          const token = (await csrf.json()).csrf_token as string;
          headers.set('X-CSRF-Token', token);
        }
      } catch {
        // Ignore CSRF fetch error if auth not configured
      }
    }

    const requestBody =
      options.body === undefined
        ? undefined
        : binaryBody ? options.body as Blob
        : typeof options.body === 'string'
          ? options.body
          : JSON.stringify(options.body);

    // On server, resolve directly to server backend; on client, use apiBase
    const normalizedPath = path.startsWith('/') ? path : `/${path}`;
    const url = isBrowser
      ? `${defaultClientBase}${normalizedPath}`
      : `${serverBackendBase}${normalizedPath.startsWith('/api') ? normalizedPath.replace(/^\/api/, '') : normalizedPath}`;

    const fetchOptions: RequestInit & { next?: any } = {
      method,
      signal: options.signal,
      headers,
      body: requestBody,
    };

    if (isBrowser) {
      fetchOptions.credentials = requestCredentials(path, method);
    }

    if (options.next) {
      fetchOptions.next = options.next;
    }

    const response = await (isBrowser ? retryableFetch : fetch)(url, fetchOptions);
    const result = await response.json().catch(() => null);
    if (!response.ok) {
      const detail = result?.detail;
      throw new ApiError(
        typeof detail === 'string'
          ? detail
          : (detail?.message ?? `Request failed (${response.status})`),
        response.status,
        typeof detail === 'object' ? detail?.code : undefined,
      );
    }
    return result as T;
  })();

  if (method === 'GET' && isBrowser) {
    const ttl = options.cacheTtl ?? (path.startsWith('/admin/') ? 0 : 300000);
    requestCache.set(cacheKey, { data: null, expiry: Date.now() + ttl, promise: fetchPromise });
    try {
      const data = await fetchPromise;
      requestCache.set(cacheKey, { data, expiry: Date.now() + ttl });
      return data;
    } catch (e) {
      requestCache.delete(cacheKey);
      throw e;
    }
  }

  return fetchPromise;
}

export function apiUrl(path: string): string {
  return resolveApiUrl(defaultClientBase, path);
}
