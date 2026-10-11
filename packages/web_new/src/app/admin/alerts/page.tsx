import type { Metadata } from 'next';
import { Suspense } from 'react';

import { AgentOperationsClient } from '@/components/admin/AgentOperationsClient';
import { PageLoader } from '@/components/ui/Feedback';

export const metadata: Metadata = {
  title: 'Alerts — Admin — Solaris Atlas',
  robots: { index: false, follow: false },
};

export default function AdminAlertsPage() {
  return (
    <Suspense fallback={<PageLoader />}>
      <AgentOperationsClient kind="alerts" />
    </Suspense>
  );
}
