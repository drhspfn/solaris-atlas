"use client";

import { createContext, useContext } from "react";
import type { QuestMediaEvent } from "./QuestMediaReferences";

export type SequenceVideoContextValue = {
  questKey: string;
  version: string | undefined;
  onChange: (occurrence: string, version: string, recording: QuestMediaEvent["youtube_fallback"] | null) => void;
};

export const SequenceVideoContext = createContext<SequenceVideoContextValue | null>(null);
export const useSequenceVideoContext = () => useContext(SequenceVideoContext);
