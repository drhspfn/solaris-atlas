import type { Metadata } from 'next';
import { Suspense } from 'react';

import { serverApi } from '@/api/serverApi';
import { QuestPageClient } from '@/components/quest/QuestPageClient';
import { PageLoader } from '@/components/ui/Feedback';
import { APP_SETTINGS } from '@/config/settings';
import type { QuestContinuity as QuestContinuityData } from '@/data/story';
import type { QuestMediaManifest } from '@/components/story/QuestMediaReferences';

type Props = {
  params: Promise<{ key: string }>;
};

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { key } = await params;
  const profile = await serverApi<any>(`/quests/${encodeURIComponent(key)}/profile?locale=en`);
  const quest = profile?.quest;
  const title = quest?.name?.content || `Quest ${key}`;
  const description = `Story transcript, flow states, player choices and audio records for ${title} in Solaris Atlas.`;
  const siteUrl = process.env.NEXT_PUBLIC_SITE_URL || 'https://solarisatlas.fun';

  return {
    title: `${title} — Story Record`,
    description,
    openGraph: {
      title: `${title} — Story Transcript | Solaris Atlas`,
      description,
      type: 'article',
      url: `${siteUrl}/quests/${encodeURIComponent(key)}`,
    },
    twitter: {
      card: 'summary',
      title: `${title} — Story Transcript | Solaris Atlas`,
      description,
    },
  };
}

export default async function QuestTranscriptPage({ params }: Props) {
  const { key } = await params;

  // Server pre-fetch initial quest data for instant SSR
  const [profile, transcript, continuity, media] = await Promise.all([
    serverApi<any>(`/quests/${encodeURIComponent(key)}/profile?locale=en`),
    serverApi<any>(
      `/quests/${encodeURIComponent(key)}/transcript?locale=en&limit=${APP_SETTINGS.limits.transcript}`,
    ),
    serverApi<QuestContinuityData>(`/quests/${encodeURIComponent(key)}/continuity?locale=en`),
    serverApi<QuestMediaManifest>(`/quests/${encodeURIComponent(key)}/media?locale=en`),
  ]);

  return (
    <Suspense fallback={<PageLoader />}>
      <QuestPageClient
        questKey={key}
        initialProfile={profile}
        initialTranscript={transcript}
        initialContinuity={continuity}
        initialMedia={media}
      />
    </Suspense>
  );
}
