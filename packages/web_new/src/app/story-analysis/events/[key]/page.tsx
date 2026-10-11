import type { Metadata } from 'next';

import { serverApi } from '@/api/serverApi';
import { decodeRouteKey } from '@/lib/routeKey';
import { StoryEventClient } from '@/components/story-analysis/StoryEventClient';

type Props = {
  params: Promise<{ key: string }>;
};

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const key = decodeRouteKey((await params).key);
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
  const key = decodeRouteKey((await params).key);
  const initialEvent = await serverApi<any>(
    `/story-analysis/events/${encodeURIComponent(key)}?locale=en`,
  );

  return <StoryEventClient eventKey={key} initialEvent={initialEvent} />;
}
