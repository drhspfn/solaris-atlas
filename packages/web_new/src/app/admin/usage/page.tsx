import type { Metadata } from 'next';
import { Suspense } from 'react';

import { AgentOperationsClient } from '@/components/admin/AgentOperationsClient';
import { PageLoader } from '@/components/ui/Feedback';

export const metadata: Metadata = {
  title: 'Usage & Costs — Admin — Solaris Atlas',
  robots: { index: false, follow: false },
};

export default function AdminUsagePage() {
  return (
    <Suspense fallback={<PageLoader />}>
      <AgentOperationsClient kind="usage" />
    </Suspense>
  );
}
