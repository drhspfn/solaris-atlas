import type { Metadata } from 'next';
import { connection } from 'next/server';

import { serverApi } from '@/api/serverApi';
import { StoryMapClient } from '@/components/story/StoryMapClient';
import type { StoryMap } from '@/data/story';
import { storyPageNumber } from '@/components/story/storyMapPaging';

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

export default async function StoryMapPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  // Search-parameter controls must render on the server for the real request,
  // rather than making the whole story map a client-only prerender fallback.
  await connection();
  const params = await searchParams;
  const requestedType = typeof params.quest_type === 'string' ? Number(params.quest_type) : 0;
  const questType = [0, 1, 2, 3, 9].includes(requestedType) ? requestedType : 0;
  const query = new URLSearchParams({ locale: 'en', quest_type_id: String(questType) });
  if (typeof params.game_version === 'string') query.set('game_version', params.game_version);
  // Render the requested patch immediately; never flash the 1.0 overview first.
  const initialMap = await serverApi<StoryMap>(`/story-map?${query}`);

  return <StoryMapClient initialMap={initialMap}
    initialPage={storyPageNumber(typeof params.page === 'string' ? params.page : undefined)}
    initialQuery={typeof params.q === 'string' ? params.q : ''} />;
}
