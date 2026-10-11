import type { Metadata } from 'next';
import { Suspense } from 'react';

import { DataOperationsClient } from '@/components/admin/DataOperationsClient';
import { PageLoader } from '@/components/ui/Feedback';

export const metadata: Metadata = {
  title: 'Game Data Operations — Admin — Solaris Atlas',
  robots: { index: false, follow: false },
};

export default function AdminDataOperationsPage() {
  return (
    <Suspense fallback={<PageLoader />}>
      <DataOperationsClient />
    </Suspense>
  );
}
