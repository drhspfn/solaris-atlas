import type { Metadata } from 'next';
import { Suspense } from 'react';

import { serverApi } from '@/api/serverApi';
import { StoryEventClient } from '@/components/story-analysis/StoryEventClient';
import { PageLoader } from '@/components/ui/Feedback';

type Props = {
  params: Promise<{ key: string }>;
};

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { key } = await params;
  const event = await serverApi<any>(`/story-analysis/events/${encodeURIComponent(key)}?locale=en`);
  const title = event?.title || 'Story Event';
  const description = event?.description || 'Story analysis event interpretation in Solaris Atlas.';

  return {
    title: `${title} — Story Analysis`,
    description,
    openGraph: {
      title: `${title} — Solaris Atlas`,
      description,
      type: 'article',
    },
    twitter: {
      card: 'summary',
      title: `${title} — Solaris Atlas`,
      description,
    },
  };
}

export default async function StoryEventPage({ params }: Props) {
  const { key } = await params;
  const initialEvent = await serverApi<any>(
    `/story-analysis/events/${encodeURIComponent(key)}?locale=en`,
  );

  return (
    <Suspense fallback={<PageLoader />}>
      <StoryEventClient eventKey={key} initialEvent={initialEvent} />
    </Suspense>
  );
}
