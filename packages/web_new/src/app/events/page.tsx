import type { Metadata } from 'next';
import { Suspense } from 'react';

import { serverApi } from '@/api/serverApi';
import { EventsPage as EventsClient } from '@/components/events/EventsClient';
import { PageLoader } from '@/components/ui/Feedback';

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
  const initialData = await serverApi<any>('/events?server=europe');

  return (
    <Suspense fallback={<PageLoader />}>
      <EventsClient initialData={initialData} />
    </Suspense>
  );
}
