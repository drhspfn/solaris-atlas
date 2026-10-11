import type { Metadata } from 'next';
import { Suspense } from 'react';

import { GoogleSuccessPageClient } from '@/components/auth/AuthPagesClient';
import { PageLoader } from '@/components/ui/Feedback';

export const metadata: Metadata = {
  title: 'Signing In — Solaris Atlas',
  robots: { index: false, follow: false },
};

export default function GoogleSuccessPage() {
  return (
    <Suspense fallback={<PageLoader />}>
      <GoogleSuccessPageClient />
    </Suspense>
  );
}
