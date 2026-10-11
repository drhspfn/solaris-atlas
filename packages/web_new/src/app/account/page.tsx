import type { Metadata } from 'next';
import { Suspense } from 'react';

import { AccountPageClient } from '@/components/auth/AuthPagesClient';
import { PageLoader } from '@/components/ui/Feedback';

export const metadata: Metadata = {
  title: 'Account Settings — Solaris Atlas',
  description: 'Manage your Solaris Atlas archive account profile and connected services.',
};

export default function AccountPage() {
  return (
    <Suspense fallback={<PageLoader />}>
      <AccountPageClient />
    </Suspense>
  );
}
