"use client";

import { useEffect, useRef } from 'react';
import { createViewer } from './engine/viewer.js';
import type { ModelAssets, ViewerController, ViewerSettings, ViewerState } from './types';

export default function ModelCanvas({ assets, preview, settings, onState, onController }: {
  assets: ModelAssets; preview: boolean; settings: ViewerSettings;
  onState: (patch: Partial<ViewerState>) => void;
  onController: (controller: ViewerController | null) => void;
}) {
  const host = useRef<HTMLDivElement>(null);
  const controller = useRef<ViewerController | null>(null);
  const currentSettings = useRef(settings);
  currentSettings.current = settings;
  useEffect(() => {
    if (!host.current) return;
    try {
      controller.current = createViewer(host.current, assets, preview, currentSettings.current, onState);
      onController(controller.current);
    } catch (error) {
      onState({ error: error instanceof Error && error.message === 'Invalid model resource URL'
        ? error.message : 'WebGL is unavailable. Enable browser graphics acceleration and retry.' });
    }
    return () => { controller.current?.dispose(); controller.current = null; onController(null); };
  }, [assets, preview, onState, onController]);
  useEffect(() => { controller.current?.settings(settings); }, [settings]);
  return <div className="model-canvas" ref={host} />;
}
