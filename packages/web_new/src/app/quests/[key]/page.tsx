import type { Metadata } from 'next';
import { Suspense } from 'react';

import { serverApi } from '@/api/serverApi';
import { APP_SETTINGS } from '@/config/settings';
import { PageLoader } from '@/components/ui/Feedback';
import { decodeRouteKey, publicPath } from '@/lib/routeKey';
import { canonicalRoute, type RouteQuery } from '@/lib/canonicalRoute';
import { QuestPageClient } from '@/components/quest/QuestPageClient';

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
  const profile = await serverApi<any>(`/quests/${encodeURIComponent(key)}/profile?${selection}&view=reader`);
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
  // Render the selected snapshot's profile without waiting for transcript media.
  const profile = await serverApi<any>(`/quests/${encodeURIComponent(key)}/profile?${selection}&view=reader`);

  await canonicalRoute("quests", key, profile?.quest, searchParams);

  return (
    <Suspense key={JSON.stringify([key, initialSelection])} fallback={
      <div className="page-container">
        <h1>{profile?.quest?.name?.content || 'Story transcript'}</h1>
        <PageLoader />
      </div>
    }>
      <QuestReader keyValue={key} selection={initialSelection} profile={profile} />
    </Suspense>
  );
}

async function QuestReader({ keyValue, selection, profile }: {
  keyValue: string;
  selection: ReturnType<typeof questSelection>;
  profile: any;
}) {
  const query = selectionQuery(selection);
  if (selection.citedLine) query.set('focus_line', selection.citedLine);
  const transcript = await serverApi<any>(
    `/quests/${encodeURIComponent(keyValue)}/transcript?${query}&limit=${APP_SETTINGS.limits.transcript}&include_raw=false&include_media=false`,
    // The API owns transcript caching, including responses above Next's cache size limit.
    { cache: 'no-store' },
  );
  return (
    <QuestPageClient
      questKey={keyValue}
      initialSelection={selection}
      initialProfile={profile}
      initialTranscript={transcript}
    />
  );
}
