import type { Metadata } from 'next';
import { Suspense } from 'react';

import { GoogleExistingLinkPageClient } from '@/components/auth/AuthPagesClient';
import { PageLoader } from '@/components/ui/Feedback';

export const metadata: Metadata = {
  title: 'Link Google Account — Solaris Atlas',
  robots: { index: false, follow: false },
};

export default function GoogleExistingLinkPage() {
  return (
    <Suspense fallback={<PageLoader />}>
      <GoogleExistingLinkPageClient />
    </Suspense>
  );
}
