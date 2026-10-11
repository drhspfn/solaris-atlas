import type { Metadata } from 'next';
import { Suspense } from 'react';

import { serverApi } from '@/api/serverApi';
import { EntityProfileClient } from '@/components/profile/EntityProfileClient';
import { PageLoader } from '@/components/ui/Feedback';
import { localizedText } from '@/data/localized';

type Props = {
  params: Promise<{ key: string }>;
};

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { key } = await params;
  const data = await serverApi<any>(`/characters/${encodeURIComponent(key)}/profile?locale=en`);
  const character = data?.character;
  const entityId = character?.canonical_key?.split(':').at(-1) || key;
  const name =
    localizedText(character?.name) ||
    localizedText(character?.canonical_name) ||
    `Character · ${entityId}`;
  const description =
    localizedText(character?.description) ||
    `Lore, story appearances, voice lines and relations for ${name} in Solaris Atlas.`;
  const image = data?.media?.image_url;
  const siteUrl = process.env.NEXT_PUBLIC_SITE_URL || 'https://solarisatlas.fun';
  const imageUrl = image ? (image.startsWith('http') ? image : `${siteUrl}${image}`) : undefined;

  return {
    title: `${name} — Character Profile`,
    description,
    openGraph: {
      title: `${name} — Characters | Solaris Atlas`,
      description,
      type: 'profile',
      url: `${siteUrl}/characters/${encodeURIComponent(key)}`,
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

export default async function CharacterProfilePage({ params }: Props) {
  const { key } = await params;
  const initialData = await serverApi<any>(
    `/characters/${encodeURIComponent(key)}/profile?locale=en`,
  );

  return (
    <Suspense fallback={<PageLoader />}>
      <EntityProfileClient kind="character" entityKey={key} initialData={initialData} />
    </Suspense>
  );
}
