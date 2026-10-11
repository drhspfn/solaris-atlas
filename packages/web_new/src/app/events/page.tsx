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
    description: 'Explore event windows, limited convenes, recurring challenges and permanent game modes.',
  },
};

export default async function EventsPage() {
  await connection();
  const initialData = await serverApi<any>('/events?server=europe');

  return <EventsClient initialData={initialData} />;
}
