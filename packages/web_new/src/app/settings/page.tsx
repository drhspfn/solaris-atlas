import type { Metadata } from 'next';
import { Suspense } from 'react';

import { SettingsClient } from '@/components/settings/SettingsClient';
import { PageLoader } from '@/components/ui/Feedback';

export const metadata: Metadata = {
  title: 'Settings — Solaris Atlas',
  description: 'Customize your archive reading experience, Rover gender preference, and dialogue player name.',
  openGraph: {
    title: 'Settings — Solaris Atlas',
    description: 'Customize your archive reading experience, Rover gender preference, and dialogue player name.',
    type: 'website',
  },
  twitter: {
    card: 'summary',
    title: 'Settings — Solaris Atlas',
    description: 'Customize your archive reading experience and dialogue preferences.',
  },
};

export default function SettingsPage() {
  return (
    <Suspense fallback={<PageLoader />}>
      <SettingsClient />
    </Suspense>
  );
}
