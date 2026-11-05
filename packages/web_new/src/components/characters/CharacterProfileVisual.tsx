"use client";

import { useState } from "react";
import { Tabs } from "@/components/ui/Tabs";
import { CharacterMediaTrigger } from "./CharacterMedia";
import { CharacterModel } from "./CharacterModel";
import type { CharacterModelData } from "@/components/models/types";
import { apiUrl } from "@/api/client";

export function CharacterProfileVisual({
  characterKey,
  name,
  portraitUrl,
  initialModel,
  refreshKey = 0,
}: {
  characterKey: string;
  name: string;
  portraitUrl?: string | null;
  initialModel?: CharacterModelData | null;
  refreshKey?: number;
}) {
  const [view, setView] = useState("portrait");
  const modelAvailable = initialModel?.status !== "missing" || refreshKey > 0;
  const showModel = modelAvailable && (view === "model" || !portraitUrl);
  return (
    <div className="character-profile-visual">
      {portraitUrl && modelAvailable && (
        <Tabs
          aria-label="Character preview"
          value={showModel ? "model" : "portrait"}
          onValueChange={setView}
          variant="underline"
          fullWidth
          items={[
            { value: "portrait", label: "Portrait" },
            { value: "model", label: "3D model" },
          ]}
        />
      )}
      {showModel ? (
        <div className="character-profile-model">
          <CharacterModel
            characterKey={characterKey}
            name={name}
            initialModel={initialModel}
            refreshKey={refreshKey}
          />
        </div>
      ) : (
        <div className="profile-portrait art-character">
          {portraitUrl ? (
            <CharacterMediaTrigger
              src={portraitUrl}
              label={`Open ${name} media gallery`}
              className="character-portrait-open"
            >
              <img
                className="character-portrait-image"
                src={apiUrl(portraitUrl.replace(/^\/api/, ""))}
                alt={`${name} portrait`}
              />
            </CharacterMediaTrigger>
          ) : (
            <span aria-label="No portrait available">✳</span>
          )}
        </div>
      )}
    </div>
  );
}
