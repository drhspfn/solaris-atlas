import type { Metadata } from 'next';
import { Suspense } from 'react';

import { StoryAgentClient } from '@/components/admin/StoryAgentClient';
import { PageLoader } from '@/components/ui/Feedback';

export const metadata: Metadata = {
  title: 'Story Agent — Admin — Solaris Atlas',
  robots: { index: false, follow: false },
};

export default function AdminStoryAgentPage() {
  return (
    <Suspense fallback={<PageLoader />}>
      <StoryAgentClient />
    </Suspense>
  );
}
