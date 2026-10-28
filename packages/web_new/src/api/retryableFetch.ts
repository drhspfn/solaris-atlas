/** Retry only server-declared temporary overloads on reads, never mutations. */
export async function retryableFetch(url: string, options: RequestInit): Promise<Response> {
  for (let attempt = 0; ; attempt++) {
    options.signal?.throwIfAborted();
    const response = await fetch(url, options);
    const retryAfter = response.headers.get('Retry-After');
    if (
      (options.method ?? 'GET').toUpperCase() !== 'GET' ||
      response.status !== 503 || attempt >= 2 ||
      retryAfter === null || !/^\d+(?:\.\d+)?$/.test(retryAfter) ||
      Number(retryAfter) > 5
    ) return response;

    await response.body?.cancel();
    await new Promise<void>((resolve, reject) => {
      const signal = options.signal;
      const abort = () => {
        clearTimeout(timer);
        signal?.removeEventListener('abort', abort);
        reject(signal?.reason);
      };
      const timer = setTimeout(() => {
        signal?.removeEventListener('abort', abort);
        resolve();
      }, Number(retryAfter) * 1000 + Math.random() * 250);
      signal?.addEventListener('abort', abort, { once: true });
      if (signal?.aborted) abort();
    });
  }
}
