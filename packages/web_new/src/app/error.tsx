'use client';

import { ErrorPanel } from '@/components/ui/Feedback';

export default function Error({ reset }: { reset: () => void }) {
  return (
    <div className="page-container">
      <ErrorPanel message="This page could not be loaded." onRetry={reset} />
    </div>
  );
}
