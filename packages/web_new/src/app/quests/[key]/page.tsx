import type { Metadata } from 'next';

import { serverApi } from '@/api/serverApi';
import { decodeRouteKey, publicPath } from '@/lib/routeKey';
import { canonicalRoute, type RouteQuery } from '@/lib/canonicalRoute';
import { QuestPageClient } from '@/components/quest/QuestPageClient';
import { APP_SETTINGS } from '@/config/settings';
import type { QuestContinuity as QuestContinuityData } from '@/data/story';
import type { QuestMediaManifest } from '@/components/story/QuestMediaReferences';

type Props = {
  params: Promise<{ key: string }>;
  searchParams?: Promise<RouteQuery>;
};

function questSelection(query: RouteQuery) {
  const first = (value: string | string[] | undefined) => Array.isArray(value) ? value[0] : value;
  return {
    locale: first(query.locale) || 'en',
    gameVersion: first(query.game_version) || '',
    citedLine: first(query.line) || '',
  };
}

function selectionQuery(selection: ReturnType<typeof questSelection>) {
  const query = new URLSearchParams({ locale: selection.locale });
  if (selection.gameVersion) query.set('game_version', selection.gameVersion);
  return query;
}

export async function generateMetadata({ params, searchParams }: Props): Promise<Metadata> {
  const key = decodeRouteKey((await params).key);
  const selection = selectionQuery(questSelection((await searchParams) ?? {}));
  const profile = await serverApi<any>(`/quests/${encodeURIComponent(key)}/profile?${selection}`);
  const quest = profile?.quest;
  const title = quest?.name?.content || `Quest ${key}`;
  const description = `Story transcript, flow states, player choices and audio records for ${title} in Solaris Atlas.`;
  const siteUrl = process.env.NEXT_PUBLIC_SITE_URL || 'https://solarisatlas.fun';

  return {
    title: `${title} — Story Record`,
    description,
    alternates: { canonical: publicPath("quests", quest || {}, key) },
    openGraph: {
      title: `${title} — Story Transcript | Solaris Atlas`,
      description,
      type: 'article',
      url: `${siteUrl}${publicPath("quests", quest || {}, key)}`,
    },
    twitter: {
      card: 'summary',
      title: `${title} — Story Transcript | Solaris Atlas`,
      description,
    },
  };
}

export default async function QuestTranscriptPage({ params, searchParams }: Props) {
  const key = decodeRouteKey((await params).key);
  const initialSelection = questSelection((await searchParams) ?? {});
  const selection = selectionQuery(initialSelection);
  const transcriptSelection = new URLSearchParams(selection);
  if (initialSelection.citedLine) transcriptSelection.set('focus_line', initialSelection.citedLine);

  // Server pre-fetch initial quest data for instant SSR
  const [profile, transcript, continuity, media] = await Promise.all([
    serverApi<any>(`/quests/${encodeURIComponent(key)}/profile?${selection}`),
    serverApi<any>(
      `/quests/${encodeURIComponent(key)}/transcript?${transcriptSelection}&limit=${APP_SETTINGS.limits.transcript}&include_raw=false`,
    ),
    serverApi<QuestContinuityData>(`/quests/${encodeURIComponent(key)}/continuity?${selection}`),
    serverApi<QuestMediaManifest>(`/quests/${encodeURIComponent(key)}/media?${selection}`),
  ]);

  await canonicalRoute("quests", key, profile?.quest, searchParams);

  return (
    <QuestPageClient
      key={JSON.stringify([key, initialSelection])}
      questKey={key}
      initialSelection={initialSelection}
      initialProfile={profile}
      initialTranscript={transcript}
      initialContinuity={continuity}
      initialMedia={media}
    />
  );
}
