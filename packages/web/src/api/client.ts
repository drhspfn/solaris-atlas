import { APP_SETTINGS } from '../config/settings';
import { requestCredentials } from './requestPolicy';
import { resolveApiUrl } from './resolveApiUrl';
const apiBase = (import.meta.env.VITE_API_BASE ?? APP_SETTINGS.api.defaultBase).replace(/\/$/, '');

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly code?: string,
  ) {
    super(message);
  }
}

export async function api<T>(
  path: string,
  options: { method?: string; body?: unknown; csrf?: boolean; signal?: AbortSignal } = {},
): Promise<T> {
  const method = options.method ?? 'GET';
  const headers = new Headers({ Accept: 'application/json' });
  if (options.body !== undefined) headers.set('Content-Type', 'application/json');
  if (options.csrf ?? !['GET', 'HEAD', 'OPTIONS'].includes(method)) {
    const csrf = await fetch(`${apiBase}/auth/csrf`, { credentials: 'include' });
    if (csrf.ok) {
      const token = (await csrf.json()).csrf_token as string;
      headers.set('X-CSRF-Token', token);
    }
  }
  const requestBody =
    options.body === undefined
      ? undefined
      : typeof options.body === 'string'
        ? options.body
        : JSON.stringify(options.body);
  const response = await fetch(`${apiBase}${path}`, {
    method,
    signal: options.signal,
    headers,
    credentials: requestCredentials(path, method),
    body: requestBody,
  });
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
}

export function apiUrl(path: string): string {
  return resolveApiUrl(apiBase, path);
}
