import type { Metadata } from 'next';
import { Suspense } from 'react';

import { serverApi } from '@/api/serverApi';
import { StoryConnectionClient } from '@/components/story-analysis/StoryConnectionClient';
import { PageLoader } from '@/components/ui/Feedback';

type Props = {
  params: Promise<{ documentId: string; index: string }>;
};

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { documentId, index } = await params;
  const data = await serverApi<any>(
    `/story-analysis/connections/${encodeURIComponent(documentId)}/${encodeURIComponent(index)}?locale=en`,
  );
  const title = data?.title || 'Story Connection';
  const description = data?.explanation || 'AI-assisted narrative connection in Solaris Atlas.';

  return {
    title: `${title} — Story Connection`,
    description,
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

export default async function StoryConnectionPage({ params }: Props) {
  const { documentId, index } = await params;
  const initialData = await serverApi<any>(
    `/story-analysis/connections/${encodeURIComponent(documentId)}/${encodeURIComponent(index)}?locale=en`,
  );

  return (
    <Suspense fallback={<PageLoader />}>
      <StoryConnectionClient documentId={documentId} index={index} initialData={initialData} />
    </Suspense>
  );
}
