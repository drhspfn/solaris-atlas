import type { Metadata } from 'next';
import { connection } from 'next/server';

import { serverApi } from '@/api/serverApi';
import { EventsPage as EventsClient } from '@/components/events/EventsClient';

export const metadata: Metadata = {
  title: 'Events Archive — Solaris Atlas',
  description:
    'Explore event windows, limited convenes, recurring challenges and permanent game modes across versions in Solaris Atlas.',
  openGraph: {
    title: 'Events Archive — Solaris Atlas',
    description:
      'Explore event windows, limited convenes, recurring challenges and permanent game modes across versions in Solaris Atlas.',
    type: 'website',
  },
  twitter: {
    card: 'summary_large_image',
    title: 'Events Archive — Solaris Atlas',
    description:
      'Explore event windows, limited convenes, recurring challenges and permanent game modes.',
  },
};

export default async function EventsPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  await connection();
  const params = await searchParams;
  const values = Array.isArray(params.event) ? params.event : [params.event ?? ''];
  const keys = values.flatMap((value) => value.split(','));
  const initialData = await serverApi<any>('/events?server=europe&summary=true', { revalidate: 0 });
  const ids: number[] = [...new Set<number>((initialData?.events ?? [])
    .filter((event: any) => keys.includes(event.slug) || keys.includes(String(event.id)))
    .map((event: any) => event.id))];

  return <EventsClient key={ids.join(',')} initialData={initialData} initialEventIds={ids} />;
}
