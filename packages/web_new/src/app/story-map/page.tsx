import type { Metadata } from 'next';
import { Suspense } from 'react';

import { serverApi } from '@/api/serverApi';
import { StoryMapClient } from '@/components/story/StoryMapClient';
import { PageLoader } from '@/components/ui/Feedback';
import type { StoryMap } from '@/data/story';

export const metadata: Metadata = {
  title: 'Story Map — Solaris-3 Chronology',
  description:
    'Read the quest chains, chapters and authored paths recorded in the game data. Follow the connected narrative across Solaris-3.',
  openGraph: {
    title: 'Story Map — Solaris Atlas',
    description:
      'Read the quest chains, chapters and authored paths recorded in the game data. Follow the connected narrative across Solaris-3.',
    type: 'website',
  },
  twitter: {
    card: 'summary_large_image',
    title: 'Story Map — Solaris Atlas',
    description: 'Read the quest chains, chapters and authored paths recorded in the game data.',
  },
};

export default async function StoryMapPage() {
  // Server pre-fetch main story path
  const initialMap = await serverApi<StoryMap>('/story-map?locale=en&quest_type_id=1');

  return (
    <Suspense fallback={<PageLoader />}>
      <StoryMapClient initialMap={initialMap} />
    </Suspense>
  );
}
