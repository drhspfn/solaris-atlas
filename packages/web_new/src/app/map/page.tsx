import type { Metadata } from 'next';
import { Suspense } from 'react';

import { MapClientWrapper } from '@/components/map/MapClientWrapper';
import { PageLoader } from '@/components/ui/Feedback';

export const metadata: Metadata = {
  title: 'Interactive World Map — Solaris Atlas',
  description:
    'Explore the interactive world map of Solaris-3 with fast travel, chests, collectibles, enemies, holograms, and resources.',
  openGraph: {
    title: 'Interactive World Map — Solaris Atlas',
    description:
      'Explore the interactive world map of Solaris-3 with fast travel, chests, collectibles, enemies, holograms, and resources.',
    type: 'website',
  },
  twitter: {
    card: 'summary_large_image',
    title: 'Interactive World Map — Solaris Atlas',
    description:
      'Explore the interactive world map of Solaris-3 with fast travel, chests, collectibles, enemies, holograms, and resources.',
  },
};

export default function MapPage() {
  return (
    <Suspense fallback={<PageLoader />}>
      <MapClientWrapper />
    </Suspense>
  );
}

