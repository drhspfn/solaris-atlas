import type { Metadata } from 'next';

import { serverApi } from '@/api/serverApi';
import { decodeRouteKey, publicPath } from '@/lib/routeKey';
import { canonicalRoute, type RouteQuery } from '@/lib/canonicalRoute';
import { EntityProfileClient } from '@/components/profile/EntityProfileClient';
import { localizedText } from '@/data/localized';

type Props = {
  params: Promise<{ key: string }>;
  searchParams?: Promise<RouteQuery>;
};

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const key = decodeRouteKey((await params).key);
  const data = await serverApi<any>(`/items/${encodeURIComponent(key)}/profile?locale=en`);
  const item = data?.item;
  const entityId = item?.canonical_key?.split(':').at(-1) || key;
  const name =
    localizedText(item?.name) ||
    localizedText(item?.canonical_name) ||
    `Item · ${entityId}`;
  const description =
    localizedText(item?.description) ||
    `Where to obtain, quest uses, and progression details for ${name} in Solaris Atlas.`;
  const image = data?.media?.image_url;
  const siteUrl = process.env.NEXT_PUBLIC_SITE_URL || 'https://solarisatlas.fun';
  const imageUrl = image ? (image.startsWith('http') ? image : `${siteUrl}${image}`) : undefined;

  return {
    title: `${name} — Item Profile`,
    description,
    alternates: { canonical: publicPath("items", item || {}, key) },
    openGraph: {
      title: `${name} — Items | Solaris Atlas`,
      description,
      type: 'website',
      url: `${siteUrl}${publicPath("items", item || {}, key)}`,
      images: imageUrl ? [{ url: imageUrl, alt: name }] : undefined,
    },
    twitter: {
      card: imageUrl ? 'summary_large_image' : 'summary',
      title: `${name} — Items | Solaris Atlas`,
      description,
      images: imageUrl ? [imageUrl] : undefined,
    },
  };
}

export default async function ItemProfilePage({ params, searchParams }: Props) {
  const key = decodeRouteKey((await params).key);
  const initialData = await serverApi<any>(`/items/${encodeURIComponent(key)}/profile?locale=en`);

  await canonicalRoute("items", key, initialData?.item, searchParams);

  return <EntityProfileClient key={key} kind="item" entityKey={key} initialData={initialData} />;
}
