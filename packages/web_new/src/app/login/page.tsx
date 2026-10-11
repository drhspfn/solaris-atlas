import type { Metadata } from 'next';
import { Suspense } from 'react';

import { LoginPageClient } from '@/components/auth/AuthPagesClient';
import { PageLoader } from '@/components/ui/Feedback';

export const metadata: Metadata = {
  title: 'Sign In — Solaris Atlas',
  description: 'Sign in to Solaris Atlas to keep your story research and preferences synced.',
};

export default function LoginPage() {
  return (
    <Suspense fallback={<PageLoader />}>
      <LoginPageClient />
    </Suspense>
  );
}
