import type { Metadata } from 'next';

import { serverApi } from '@/api/serverApi';
import { decodeRouteKey, publicPath } from '@/lib/routeKey';
import { canonicalRoute, type RouteQuery } from '@/lib/canonicalRoute';
import { NodeExplorerPage as NodeExplorerClient } from '@/components/nodes/NodeExplorerClient';
import { APP_SETTINGS } from '@/config/settings';

type Props = {
  params: Promise<{ key: string }>;
  searchParams?: Promise<RouteQuery>;
};

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const key = decodeRouteKey((await params).key);
  const detail = await serverApi<any>(`/nodes/${encodeURIComponent(key)}`);
  const title = detail?.canonical_key || key;
  const description = `Inspect graph node, relations, and narrative connections for ${title} in Solaris Atlas.`;

  return {
    title: `${title} — Node Explorer`,
    description,
    alternates: { canonical: publicPath("nodes", detail || {}, key) },
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

export default async function NodePage({ params, searchParams }: Props) {
  const key = decodeRouteKey((await params).key);

  // Server pre-fetch node details
  const [detail, related, narrative] = await Promise.all([
    serverApi<any>(`/nodes/${encodeURIComponent(key)}`),
    serverApi<any>(
      `/nodes/${encodeURIComponent(key)}/related?locale=en&limit=${APP_SETTINGS.limits.relatedNodes}`,
    ),
    serverApi<any>(`/nodes/${encodeURIComponent(key)}/narrative-context?locale=en`),
  ]);

  await canonicalRoute("nodes", key, detail, searchParams);

  return (
    <NodeExplorerClient
      nodeKey={key}
      initialDetail={detail}
      initialRelated={related?.results}
      initialNarrative={narrative}
    />
  );
}
