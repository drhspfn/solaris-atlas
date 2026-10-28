import { adsenseClient, adsTxt } from '@/lib/adsense';

export const dynamic = 'force-static';

export function GET() {
  const content = adsTxt(adsenseClient);
  if (!content) return new Response('Not configured\n', { status: 404 });
  return new Response(content, { headers: { 'Content-Type': 'text/plain; charset=utf-8' } });
}
