import type { Metadata } from 'next';

import { serverApi } from '@/api/serverApi';
import { decodeRouteKey, publicPath } from '@/lib/routeKey';
import { canonicalRoute, type RouteQuery } from '@/lib/canonicalRoute';
import { EntityProfileClient } from '@/components/profile/EntityProfileClient';
import type { CharacterArchiveData } from '@/components/characters/CharacterArchive';
import { localizedText } from '@/data/localized';

type Props = {
  params: Promise<{ key: string }>;
  searchParams?: Promise<RouteQuery>;
};

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const key = decodeRouteKey((await params).key);
  const [data, archive] = await Promise.all([
    serverApi<any>(`/characters/${encodeURIComponent(key)}/profile?locale=en`),
    serverApi<CharacterArchiveData>(`/characters/${encodeURIComponent(key)}/archive?locale=en`),
  ]);
  const character = data?.character;
  const entityId = character?.canonical_key?.split(':').at(-1) || key;
  const name =
    localizedText(character?.name) ||
    localizedText(character?.canonical_name) ||
    `Character · ${entityId}`;
  const description = (
    archive?.biography ||
    localizedText(character?.description) ||
    `Lore, story appearances, voice lines and relations for ${name} in Solaris Atlas.`
  )
    .replace(/<[^>]*>/g, '')
    .replace(/\s+/g, ' ')
    .trim()
    .slice(0, 300);
  const image =
    archive?.artwork?.find((art) => art.kind === 'RoleHeadIconLarge')?.url ||
    archive?.artwork?.find((art) => art.url)?.url ||
    data?.media?.image_url;
  const siteUrl = process.env.NEXT_PUBLIC_SITE_URL || 'https://solarisatlas.fun';
  const imageUrl = image ? (image.startsWith('http') ? image : `${siteUrl}${image}`) : undefined;

  return {
    title: `${name} — Character Profile`,
    description,
    alternates: { canonical: publicPath("characters", character || {}, key) },
    openGraph: {
      title: `${name} — Characters | Solaris Atlas`,
      description,
      type: 'profile',
      url: `${siteUrl}${publicPath("characters", character || {}, key)}`,
      images: imageUrl ? [{ url: imageUrl, alt: name }] : undefined,
    },
    twitter: {
      card: imageUrl ? 'summary_large_image' : 'summary',
      title: `${name} — Characters | Solaris Atlas`,
      description,
      images: imageUrl ? [imageUrl] : undefined,
    },
  };
}

export default async function CharacterProfilePage({ params, searchParams }: Props) {
  const key = decodeRouteKey((await params).key);
  const [initialData, initialArchive] = await Promise.all([
    serverApi<any>(`/characters/${encodeURIComponent(key)}/profile?locale=en`),
    serverApi<CharacterArchiveData>(`/characters/${encodeURIComponent(key)}/archive?locale=en`),
  ]);

  await canonicalRoute("characters", key, initialData?.character, searchParams);

  return (
    <EntityProfileClient
      kind="character"
      entityKey={key}
      initialData={initialData}
      initialArchive={initialArchive}
    />
  );
}
