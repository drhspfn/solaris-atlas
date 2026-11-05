import type { CharacterModelData } from '@/components/models/types';
import type { Metadata } from 'next';

import { serverApi } from '@/api/serverApi';
import { decodeRouteKey, publicPath } from '@/lib/routeKey';
import { canonicalRoute, type RouteQuery } from '@/lib/canonicalRoute';
import { EntityProfileClient } from '@/components/profile/EntityProfileClient';
import type { CharacterArchiveData } from '@/components/characters/CharacterArchive';
import { characterPortrait } from '@/data/characterArtwork';
import { localizedText } from '@/data/localized';

type Props = {
  params: Promise<{ key: string }>;
  searchParams?: Promise<RouteQuery>;
};

export async function generateMetadata({ params, searchParams }: Props): Promise<Metadata> {
  const key = decodeRouteKey((await params).key);
  const requestedVersion = (await searchParams)?.game_version;
  const gameVersion = Array.isArray(requestedVersion) ? requestedVersion[0] : requestedVersion;
  const versionQuery = gameVersion ? `game_version=${encodeURIComponent(gameVersion)}&` : '';
  const [data, archive] = await Promise.all([
    serverApi<any>(`/characters/${encodeURIComponent(key)}/profile?connections_limit=12&${versionQuery}locale=en`),
    serverApi<CharacterArchiveData>(`/characters/${encodeURIComponent(key)}/archive?${versionQuery}locale=en`),
  ]);
  const character = data?.character;
  const entityId = character?.canonical_key?.split(':').at(-1) || key;
  const name =
    data?.family?.name ||
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
    archive ? characterPortrait(archive.artwork) : data?.media?.image_url;
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
  const requestedVersion = (await searchParams)?.game_version;
  const gameVersion = Array.isArray(requestedVersion) ? requestedVersion[0] : requestedVersion;
  const versionQuery = gameVersion ? `game_version=${encodeURIComponent(gameVersion)}&` : '';
  const [initialData, initialArchive, initialModel] = await Promise.all([
    serverApi<any>(`/characters/${encodeURIComponent(key)}/profile?connections_limit=12&${versionQuery}locale=en`),
    serverApi<CharacterArchiveData>(`/characters/${encodeURIComponent(key)}/archive?${versionQuery}locale=en`),
    serverApi<CharacterModelData>(`/characters/${encodeURIComponent(key)}/model`, { revalidate: 60 }),
  ]);

  await canonicalRoute("characters", key, initialData?.character, searchParams);

  return (
    <EntityProfileClient
      key={key}
      kind="character"
      entityKey={key}
      initialData={initialData}
      initialArchive={initialArchive}
      initialModel={initialModel}
      gameVersion={gameVersion}
    />
  );
}
