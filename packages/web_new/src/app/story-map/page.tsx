import type { Metadata } from 'next';
import { connection } from 'next/server';

import { serverApi } from '@/api/serverApi';
import { StoryMapClient } from '@/components/story/StoryMapClient';
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
  // Search-parameter controls must render on the server for the real request,
  // rather than making the whole story map a client-only prerender fallback.
  await connection();
  // Server pre-fetch main story path
  const initialMap = await serverApi<StoryMap>('/story-map?locale=en&quest_type_id=1');

  return <StoryMapClient initialMap={initialMap} />;
}
