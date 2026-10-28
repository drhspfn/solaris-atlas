/** Public publisher ID, embedded when the web application is built. */
export function publisherId(value: string | undefined): string | null {
  const id = value?.trim() || '';
  return /^ca-pub-\d{16}$/.test(id) ? id : null;
}

export const adsenseClient = publisherId(process.env.NEXT_PUBLIC_GOOGLE_ADSENSE_CLIENT);

export function adsTxt(client: string | null): string | null {
  return client ? `google.com, ${client.slice(3)}, DIRECT, f08c47fec0942fa0\n` : null;
}
