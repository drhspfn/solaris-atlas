'use client';

import dynamic from 'next/dynamic';

import { PageLoader } from '@/components/ui/Feedback';

const DynamicWorldMap = dynamic(
  () => import('@/components/map/WorldMapClient').then((mod) => mod.WorldMapPage),
  {
    ssr: false,
    loading: () => <PageLoader />,
  },
);

export function MapClientWrapper() {
  return <DynamicWorldMap />;
}
