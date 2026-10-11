import type { Metadata } from 'next';
import { Suspense } from 'react';

import { GoogleCompletePageClient } from '@/components/auth/AuthPagesClient';
import { PageLoader } from '@/components/ui/Feedback';

export const metadata: Metadata = {
  title: 'Complete Google Registration — Solaris Atlas',
  robots: { index: false, follow: false },
};

export default function GoogleCompletePage() {
  return (
    <Suspense fallback={<PageLoader />}>
      <GoogleCompletePageClient />
    </Suspense>
  );
}
