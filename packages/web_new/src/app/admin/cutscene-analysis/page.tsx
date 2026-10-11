import type { Metadata } from 'next';
import { Suspense } from 'react';

import { CutsceneAnalysisClient } from '@/components/admin/CutsceneAnalysisClient';
import { PageLoader } from '@/components/ui/Feedback';

export const metadata: Metadata = {
  title: 'Cutscene Analysis — Admin — Solaris Atlas',
  robots: { index: false, follow: false },
};

export default function AdminCutsceneAnalysisPage() {
  return (
    <Suspense fallback={<PageLoader />}>
      <CutsceneAnalysisClient />
    </Suspense>
  );
}
