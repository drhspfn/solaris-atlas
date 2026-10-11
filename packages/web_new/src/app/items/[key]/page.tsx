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
    openGraph: {
      title: `${name} — Items | Solaris Atlas`,
      description,
      type: 'website',
      url: `${siteUrl}/items/${encodeURIComponent(key)}`,
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

export default async function ItemProfilePage({ params }: Props) {
  const { key } = await params;
  const initialData = await serverApi<any>(`/items/${encodeURIComponent(key)}/profile?locale=en`);

  return (
    <Suspense fallback={<PageLoader />}>
      <EntityProfileClient kind="item" entityKey={key} initialData={initialData} />
    </Suspense>
  );
}
