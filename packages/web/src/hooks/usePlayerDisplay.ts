import { useMemo } from 'react';

import { useAuth } from '../auth/AuthProvider';
import { playerName } from '../data/playerName';
import { useNarrativePreferences } from '../preferences/NarrativePreferences';

/** Resolve personalized dialogue presentation once in the owning page/component. */
export function usePlayerDisplay() {
  const { user } = useAuth();
  const { nameMode, colorMode, customColor } = useNarrativePreferences();
  return useMemo(
    () => ({
      name: playerName(user?.nickname, nameMode),
      colorMode,
      customColor,
    }),
    [user?.nickname, nameMode, colorMode, customColor],
  );
}
