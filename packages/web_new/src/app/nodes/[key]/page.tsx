import type { Metadata } from 'next';
import { Suspense } from 'react';

import { serverApi } from '@/api/serverApi';
import { decodeRouteKey } from '@/lib/routeKey';
import { NodeExplorerPage as NodeExplorerClient } from '@/components/nodes/NodeExplorerClient';
import { PageLoader } from '@/components/ui/Feedback';
import { APP_SETTINGS } from '@/config/settings';

type Props = {
  params: Promise<{ key: string }>;
};

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const key = decodeRouteKey((await params).key);
  const detail = await serverApi<any>(`/nodes/${encodeURIComponent(key)}`);
  const title = detail?.slug || detail?.canonical_key || key;
  const description = `Inspect graph node, relations, and narrative connections for ${title} in Solaris Atlas.`;

  return {
    title: `${title} — Node Explorer`,
    description,
    openGraph: {
      title: `${title} — Solaris Atlas`,
      description,
      type: 'website',
    },
    twitter: {
      card: 'summary',
      title: `${title} — Solaris Atlas`,
      description,
    },
  };
}

export default async function NodePage({ params }: Props) {
  const key = decodeRouteKey((await params).key);

  // Server pre-fetch node details
  const [detail, related, narrative] = await Promise.all([
    serverApi<any>(`/nodes/${encodeURIComponent(key)}`),
    serverApi<any>(
      `/nodes/${encodeURIComponent(key)}/related?locale=en&limit=${APP_SETTINGS.limits.relatedNodes}`,
    ),
    serverApi<any>(`/nodes/${encodeURIComponent(key)}/narrative-context?locale=en`),
  ]);

  return (
    <Suspense fallback={<PageLoader />}>
      <NodeExplorerClient
        nodeKey={key}
        initialDetail={detail}
        initialRelated={related?.results ?? []}
        initialNarrative={narrative}
      />
    </Suspense>
  );
}
