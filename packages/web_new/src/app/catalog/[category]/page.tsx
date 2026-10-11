import type { Metadata } from 'next';
import { Suspense } from 'react';

import { serverApi } from '@/api/serverApi';
import { CatalogClient } from '@/components/catalog/CatalogClient';
import { PageLoader } from '@/components/ui/Feedback';
import { APP_SETTINGS } from '@/config/settings';
import { categoryTitle, type Entity } from '@/data/entities';

type Props = {
  params: Promise<{ category: string }>;
};

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { category } = await params;
  const title = categoryTitle[category] ?? 'Archive';
  const description = `Browse source-linked ${title.toLowerCase()} from the current Wuthering Waves game archive.`;

  return {
    title: `${title} — Collection`,
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

export default async function CatalogPage({ params }: Props) {
  const { category } = await params;

  // Server pre-fetch initial entries for SSR and SEO
  const initialData = await serverApi<{ results: Entity[]; total: number }>(
    `/catalog?category=${encodeURIComponent(category)}&locale=en&limit=${APP_SETTINGS.limits.catalogPage}&offset=0`,
  );

  return (
    <Suspense fallback={<PageLoader />}>
      <CatalogClient
        category={category}
        initialItems={initialData?.results ?? []}
        initialTotal={initialData?.total ?? 0}
      />
    </Suspense>
  );
}
