import { useMemo } from 'react';

import { useAuth } from '../auth/AuthProvider';
import { playerName } from '../data/playerName';
import { useNarrativePreferences } from '../preferences/NarrativePreferences';

/** Resolve personalized dialogue presentation once in the owning page/component. */
export function usePlayerDisplay() {
  const { user, loading } = useAuth();
  const { nameMode, colorMode, customColor, preferredRover } = useNarrativePreferences();
  return useMemo(
    () => ({
      name: loading ? 'Rover' : playerName(user?.nickname, nameMode),
      gender: preferredRover,
      colorMode,
      customColor,
    }),
    [user?.nickname, loading, nameMode, colorMode, customColor, preferredRover],
  );
}
