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
  const data = await serverApi<any>(`/locations/${encodeURIComponent(key)}/profile?locale=en`);
  const location = data?.location;
  const entityId = location?.canonical_key?.split(':').at(-1) || key;
  const name =
    localizedText(location?.name) ||
    localizedText(location?.canonical_name) ||
    `Area · ${entityId}`;
  const description =
    localizedText(location?.description) ||
    `Places, sub-areas, and story links for ${name} across Solaris-3.`;
  const image = data?.media?.image_url;
  const siteUrl = process.env.NEXT_PUBLIC_SITE_URL || 'https://solarisatlas.fun';
  const imageUrl = image ? (image.startsWith('http') ? image : `${siteUrl}${image}`) : undefined;

  return {
    title: `${name} — Location Profile`,
    description,
    alternates: { canonical: publicPath("locations", location || {}, key) },
    openGraph: {
      title: `${name} — Locations | Solaris Atlas`,
      description,
      type: 'website',
      url: `${siteUrl}${publicPath("locations", location || {}, key)}`,
      images: imageUrl ? [{ url: imageUrl, alt: name }] : undefined,
    },
    twitter: {
      card: imageUrl ? 'summary_large_image' : 'summary',
      title: `${name} — Locations | Solaris Atlas`,
      description,
      images: imageUrl ? [imageUrl] : undefined,
    },
  };
}

export default async function LocationProfilePage({ params, searchParams }: Props) {
  const key = decodeRouteKey((await params).key);
  const initialData = await serverApi<any>(
    `/locations/${encodeURIComponent(key)}/profile?locale=en`,
  );

  await canonicalRoute("locations", key, initialData?.location, searchParams);

  return <EntityProfileClient kind="location" entityKey={key} initialData={initialData} />;
}
