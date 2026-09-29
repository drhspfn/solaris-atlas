import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from "react";

const STORAGE_KEY = "solaris-atlas:narrative-preferences:v1";
export type PlayerNameMode = "nickname" | "rover_title";
export type PlayerNameColorMode = "accent" | "custom";

type Preferences = {
  nameMode: PlayerNameMode;
  colorMode: PlayerNameColorMode;
  customColor: string;
};
type PreferenceContextValue = Preferences & {
  setNameMode: (value: PlayerNameMode) => void;
  setColorMode: (value: PlayerNameColorMode) => void;
  setCustomColor: (value: string) => void;
};

const defaults: Preferences = {
  nameMode: "nickname",
  colorMode: "accent",
  customColor: "#9BD9C0",
};
const PreferencesContext = createContext<PreferenceContextValue | null>(null);

function loadPreferences(): Preferences {
  try {
    const saved = localStorage.getItem(STORAGE_KEY);
    if (!saved) return defaults;
    const value = JSON.parse(saved) as Partial<Preferences>;
    return {
      nameMode: value.nameMode === "rover_title" ? "rover_title" : "nickname",
      colorMode: value.colorMode === "custom" ? "custom" : "accent",
      customColor: /^#[\da-f]{6}$/i.test(value.customColor || "")
        ? value.customColor!
        : defaults.customColor,
    };
  } catch {
    return defaults;
  }
}

export function NarrativePreferencesProvider({ children }: { children: ReactNode }) {
  const [preferences, setPreferences] = useState(loadPreferences);
  useEffect(() => {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(preferences));
    } catch {
      // Settings remain active for this page session if storage is unavailable.
    }
  }, [preferences]);
  function update(patch: Partial<Preferences>) {
    setPreferences((current) => ({ ...current, ...patch }));
  }
  const value = useMemo<PreferenceContextValue>(() => ({
    ...preferences,
    setNameMode: (nameMode) => update({ nameMode }),
    setColorMode: (colorMode) => update({ colorMode }),
    setCustomColor: (customColor) => update({ customColor }),
  }), [preferences]);
  return <PreferencesContext.Provider value={value}>{children}</PreferencesContext.Provider>;
}

export function useNarrativePreferences(): PreferenceContextValue {
  const value = useContext(PreferencesContext);
  if (!value) throw new Error("useNarrativePreferences must be used inside its provider");
  return value;
}
