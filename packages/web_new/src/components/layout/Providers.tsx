"use client";

import React from "react";
import { usePathname } from "next/navigation";
import { MediaViewerProvider } from "@/components/ui/MediaViewerProvider";
import { AuthProvider } from "@/auth/AuthProvider";
import { NarrativePreferencesProvider } from "@/preferences/NarrativePreferences";

export function Providers({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  return (
    <AuthProvider>
      <NarrativePreferencesProvider>
        <MediaViewerProvider resetKey={pathname}>
          {children}
        </MediaViewerProvider>
      </NarrativePreferencesProvider>
    </AuthProvider>
  );
}
