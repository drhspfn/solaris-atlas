'use client';

import React from 'react';
import { AuthProvider } from '@/auth/AuthProvider';
import { NarrativePreferencesProvider } from '@/preferences/NarrativePreferences';

export function Providers({ children }: { children: React.ReactNode }) {
  return (
    <AuthProvider>
      <NarrativePreferencesProvider>
        {children}
      </NarrativePreferencesProvider>
    </AuthProvider>
  );
}
