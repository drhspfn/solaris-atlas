import type { Metadata } from 'next';
import { Suspense } from 'react';

import { SearchClient } from '@/components/search/SearchClient';
import { PageLoader } from '@/components/ui/Feedback';

export const metadata: Metadata = {
  title: 'Archive Search — Solaris Atlas',
  description:
    'Search characters, story quests, locations, items, dialogue and lore explanations across Solaris-3.',
  openGraph: {
    title: 'Archive Search — Solaris Atlas',
    description:
      'Search characters, story quests, locations, items, dialogue and lore explanations across Solaris-3.',
    type: 'website',
  },
  twitter: {
    card: 'summary',
    title: 'Archive Search — Solaris Atlas',
    description: 'Search characters, story quests, locations, items and lore explanations.',
  },
};

export default function SearchPage() {
  return (
    <Suspense fallback={<PageLoader />}>
      <SearchClient />
    </Suspense>
  );
}
