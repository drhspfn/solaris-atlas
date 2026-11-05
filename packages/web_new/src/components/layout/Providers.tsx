'use client';

import React from 'react';
import { MediaViewerProvider } from '@/components/ui/MediaViewerProvider';
import { AuthProvider } from '@/auth/AuthProvider';
import { NarrativePreferencesProvider } from '@/preferences/NarrativePreferences';

export function Providers({ children }: { children: React.ReactNode }) {
  return (
    <AuthProvider>
      <NarrativePreferencesProvider>
        <MediaViewerProvider>{children}</MediaViewerProvider>
      </NarrativePreferencesProvider>
    </AuthProvider>
  );
}
