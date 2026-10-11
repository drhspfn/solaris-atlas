import type { Metadata } from 'next';
import { Suspense } from 'react';

import { RegisterPageClient } from '@/components/auth/AuthPagesClient';
import { PageLoader } from '@/components/ui/Feedback';

export const metadata: Metadata = {
  title: 'Create Account — Solaris Atlas',
  description: 'Create your Solaris Atlas account for exploring Solaris-3 and tracking your archive place.',
};

export default function RegisterPage() {
  return (
    <Suspense fallback={<PageLoader />}>
      <RegisterPageClient />
    </Suspense>
  );
}
