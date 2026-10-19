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
  const ids = values
    .flatMap((value) => value.split(','))
    .filter((value) => /^\d+$/.test(value))
    .map(Number)
    .filter(Number.isSafeInteger);
  const initialData = await serverApi<any>('/events?server=europe', { revalidate: 0 });

  return <EventsClient key={ids.join(',')} initialData={initialData} initialEventIds={ids} />;
}
