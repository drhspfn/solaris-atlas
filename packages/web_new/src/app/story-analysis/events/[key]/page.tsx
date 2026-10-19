import type { Metadata } from 'next';

import { serverApi } from '@/api/serverApi';
import { decodeRouteKey, publicPath } from '@/lib/routeKey';
import { canonicalRoute, type RouteQuery } from '@/lib/canonicalRoute';
import { StoryEventClient } from '@/components/story-analysis/StoryEventClient';

type Props = {
  params: Promise<{ key: string }>;
  searchParams?: Promise<RouteQuery>;
};

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const key = decodeRouteKey((await params).key);
  const event = await serverApi<any>(`/story-analysis/events/${encodeURIComponent(key)}?locale=en`);
  const title = event?.title || 'Story Event';
  const description = event?.description || 'Story analysis event interpretation in Solaris Atlas.';

  return {
    title: `${title} — Story Analysis`,
    description,
    alternates: { canonical: publicPath("story-analysis/events", event || {}, key) },
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

export default async function StoryEventPage({ params, searchParams }: Props) {
  const key = decodeRouteKey((await params).key);
  const initialEvent = await serverApi<any>(
    `/story-analysis/events/${encodeURIComponent(key)}?locale=en`,
  );

  await canonicalRoute("story-analysis/events", key, initialEvent, searchParams);

  return <StoryEventClient eventKey={key} initialEvent={initialEvent} />;
}
