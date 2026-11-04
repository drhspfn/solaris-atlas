"use client";

import { Component, lazy, Suspense, useCallback, useEffect, useRef, useState, type ReactNode } from 'react';
import { Select } from '@/components/ui/Select';
import { apiUrl } from '@/api/client';
import { defaultSettings, initialViewerState, type ModelAssets, type ViewerCommand, type ViewerController,
  type ViewerOption, type ViewerSettings, type ViewerState } from './types';
import '@/styles/model-viewer.css';

// Only this chunk imports Three.js. Controls can render on the server without WebGL.
const ModelCanvas = lazy(() => import('./ModelCanvas'));
const stamp = (value: number) => `${Math.floor(value / 60)}:${String(Math.floor(value % 60)).padStart(2, '0')}`;
const label = (name: string, bytes?: number) => bytes && bytes > 0 ? `${name} · ${(bytes / 1048576).toFixed(1)} MiB` : name;

class CanvasBoundary extends Component<{ children: ReactNode; retry: () => void }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() { return { failed: true }; }
  render() {
    return this.state.failed ? <div className="character-model-feedback" role="alert">
      <p>Viewer code could not be loaded.</p><button type="button" onClick={this.props.retry}>Retry viewer</button>
    </div> : this.props.children;
  }
}

/** Shared Select owns keyboard/popover behavior; this component narrows catalog branches. */
function CatalogPicker({ options, value, onChange, label, disabled }: {
  options: ViewerOption[]; value: string; onChange: (value: string) => void; label: string; disabled: boolean;
}) {
  const [path, setPath] = useState<string[]>([]);
  const [query, setQuery] = useState('');
  const selected = options.find(option => option.value === value);
  useEffect(() => { setPath(selected?.path || []); }, [value]);
  const matching = options.filter(option => query ? option.label.toLowerCase().includes(query.toLowerCase())
    : path.every((part, index) => option.path?.[index] === part));
  const branches = [...new Set(matching.flatMap(option => option.path?.[path.length] ? [option.path[path.length]] : []))];
  const leaves = matching.filter(option => query || (option.path?.length || 0) <= path.length);
  return <div className="model-catalog-picker">
    <label>{label} search<input type="search" value={query} disabled={disabled} onChange={event => setQuery(event.target.value)} /></label>
    {query && <button type="button" onClick={() => setQuery('')}>Clear search</button>}
    {!query && path.length > 0 && <button type="button" onClick={() => setPath(path.slice(0, -1))}>Back · {path.at(-1)}</button>}
    {!query && branches.length > 0 && <Select label={`${label} category`} value="" disabled={disabled}
      placeholder={path.at(-1) || 'Browse categories'} options={branches.map(branch => ({ value: branch, label: branch }))}
      onChange={branch => setPath([...path, branch])} />}
    <Select label={label} value={value} options={leaves} disabled={disabled || !leaves.length}
      placeholder={selected?.label || 'Select'} onChange={next => {
        onChange(next); setPath(options.find(option => option.value === next)?.path || []);
      }} />
    {!matching.length && <span>No matching entries.</span>}
  </div>;
}

export function ModelViewer({ assets: source, name, preview = false, active = true }: {
  assets: ModelAssets; name: string; preview?: boolean; active?: boolean;
}) {
  const [assets] = useState<ModelAssets>(() => Object.fromEntries(Object.entries(source).map(([key, asset]) =>
    [key, asset && typeof asset === 'object' && 'url' in asset
      ? { ...asset, url: apiUrl(asset.url.replace(/^\/api(?=\/)/, '')) } : asset])) as ModelAssets);
  const [settings, setSettings] = useState<ViewerSettings>(defaultSettings);
  const [state, setState] = useState<ViewerState>(initialViewerState);
  const [attempt, setAttempt] = useState(0);
  const [panel, setPanel] = useState<'scene' | 'physics' | 'camera'>('scene');
  const controller = useRef<ViewerController | null>(null);
  const drawer = useRef<HTMLDialogElement>(null);
  const root = useRef<HTMLDivElement>(null);
  const report = useCallback((patch: Partial<ViewerState>) => {
    setState(current => ({ ...current, ...patch }));
    if (patch.bones) setSettings(current => ({ ...current,
      bone: patch.bones?.some(bone => bone.value === current.bone) ? current.bone : patch.preferredBone || '',
    }));
  }, []);
  const connect = useCallback((value: ViewerController | null) => { controller.current = value; }, []);
  const update = <K extends keyof ViewerSettings>(key: K, value: ViewerSettings[K]) => setSettings(current => ({ ...current, [key]: value }));
  const retry = () => { setState(initialViewerState); setAttempt(value => value + 1); };
  useEffect(() => {
    if (!preview && !matchMedia('(prefers-reduced-motion: reduce)').matches) update('playing', true);
  }, [preview]);
  useEffect(() => {
    if (!active || state.ready || state.error) return;
    const timer = setTimeout(() => report({ error: 'Model loading timed out. Retry the viewer.' }), 45000);
    return () => clearTimeout(timer);
  }, [active, state.ready, state.error, attempt, report]);
  const command = (value: ViewerCommand) => { void controller.current?.command(value); };
  const disabled = !state.ready || state.busy;
  const clipIndex = state.animations.findIndex(item => item.value === state.animation);
  const action = (text: string, value: ViewerCommand, unavailable = false) => <button type="button" disabled={disabled || unavailable}
    onClick={() => command(value)}>{text}</button>;
  const checkbox = (text: string, key: 'playing' | 'floor' | 'turntable' | 'physics' | 'lock' | 'follow', unavailable = false) =>
    <label className="model-toggle"><input type="checkbox" checked={settings[key]} disabled={disabled || unavailable}
      onChange={event => update(key, event.target.checked)} />{text}</label>;
  return <div ref={root} className={`native-model-viewer${preview ? ' native-model-preview' : ''}`} aria-label={`3D ${name}`}>
    <div className="model-stage" aria-busy={active && !state.ready && !state.error}>
      {active && !(state.error && !state.ready) && <CanvasBoundary key={attempt} retry={retry}>
        <Suspense fallback={<p className="character-model-feedback" role="status">Loading viewer…</p>}>
          <ModelCanvas assets={assets} preview={preview} settings={settings} onState={report} onController={connect} />
        </Suspense>
      </CanvasBoundary>}
      {!state.ready && !state.error && <p className="model-stage-status" role="status">{active ? state.status : 'Preview loads when visible'}</p>}
      {state.error && <div className="model-stage-status" role="alert"><p>{state.error}</p>
        {!state.ready && <button type="button" onClick={retry}>Retry viewer</button>}</div>}
    </div>
    {!preview && <>
      <div className="model-controls">
      <div className="model-toolbar" aria-label="Camera controls">
        {action('Rotate left', 'left')}{action('Rotate right', 'right')}{action('Zoom in', 'closer')}{action('Zoom out', 'farther')}
        <button type="button" disabled={disabled} onClick={() => { update('follow', false); command('reset'); }}>Reset view</button>
        <button type="button" onClick={() => drawer.current?.showModal()}>Settings</button>
        <button type="button" onClick={async () => {
          try { if (document.fullscreenElement) await document.exitFullscreen(); else await root.current?.requestFullscreen(); }
          catch { report({ error: 'Fullscreen is unavailable in this browser.' }); }
        }}>Fullscreen</button>
      </div>
      <div className="model-transport">
        <button type="button" disabled={disabled || (!state.animations.length && !state.facialLoaded)} onClick={() => update('playing', !settings.playing)}>{settings.playing ? 'Pause' : 'Play'}</button>
        <label className="model-timeline">Animation position<input type="range" min={0} max={state.duration || 0.001} step="0.01"
          disabled={disabled || !state.duration} value={state.time} onChange={event => controller.current?.seek(Number(event.target.value))} /></label>
        <output>{stamp(state.time)} / {stamp(state.duration)}</output>
        <Select label="Playback speed" value={settings.speed} disabled={disabled} options={[0.25, 0.5, 1, 1.5, 2].map(value => ({ value, label: `${value}×` }))} onChange={value => update('speed', Number(value))} />
      </div>
      <div className="model-load-actions" aria-busy={state.busy}>
        {assets.download && !state.fullQuality && action(label('Load full quality', assets.download.size_bytes), 'quality')}
        {assets.animations && !state.animationsLoaded && action(label('Load animations', assets.animations.size_bytes + (assets.facial && !state.fullQuality ? (assets.download?.size_bytes || 0) + assets.facial.size_bytes : 0)), 'animations')}
        {assets.facial && !state.facialLoaded && action(label('Load face + full model', assets.facial.size_bytes + (state.fullQuality ? 0 : assets.download?.size_bytes || 0)), 'facial')}
        <span role="status">{state.busy ? state.status : state.fullQuality ? 'Full quality' : 'Compact model'}</span>
      </div>
      {state.animations.length > 0 && <div className="model-animation-controls">
        <button type="button" disabled={disabled || clipIndex <= 0} onClick={() => controller.current?.animation(state.animations[clipIndex - 1].value)}>Previous animation</button>
        <CatalogPicker label="Animation" options={state.animations} value={state.animation} disabled={disabled} onChange={value => controller.current?.animation(value)} />
        <button type="button" disabled={disabled || clipIndex < 0 || clipIndex >= state.animations.length - 1} onClick={() => controller.current?.animation(state.animations[clipIndex + 1].value)}>Next animation</button>
      </div>}
      {state.facialLoaded && <Select label="Facial animation" value={state.face} options={state.faces} disabled={disabled} onChange={value => controller.current?.face(value)} />}
      </div>
      <dialog ref={drawer} className="model-settings" aria-label="Model settings" onClick={event => { if (event.target === event.currentTarget) drawer.current?.close(); }}>
        <header><h3>Model settings</h3><button type="button" autoFocus onClick={() => drawer.current?.close()}>Close settings</button></header>
        <nav aria-label="Settings sections">{(['scene', 'physics', 'camera'] as const).map(tab =>
          <button type="button" key={tab} aria-pressed={panel === tab} onClick={() => setPanel(tab)}>{tab}</button>)}</nav>
        <div className="model-settings-content">
          {panel === 'scene' && <>
            {checkbox('Show floor and world axes', 'floor')}{checkbox('Turntable', 'turntable')}
            {checkbox('Lock root position', 'lock', !state.rootAvailable)}
            <fieldset disabled={disabled || !settings.lock}><legend>Lock world axes</legend>{(['x', 'y', 'z'] as const).map(axis =>
              <label className="model-toggle" key={axis}><input type="checkbox" checked={settings.axes[axis]} onChange={event => update('axes', { ...settings.axes, [axis]: event.target.checked })} />{axis.toUpperCase()}</label>)}</fieldset>
            <p>Axis locks change the preview only. Y remains free by default for jumps.</p>
          </>}
          {panel === 'physics' && <>{checkbox('Spring-bone physics', 'physics', !state.physicsBones)}
            <label>Strength · {Math.round(settings.strength * 100)}%<input type="range" min="0" max="2" step="0.05" value={settings.strength} disabled={disabled || !settings.physics || !state.physicsBones} onChange={event => update('strength', Number(event.target.value))} /></label>
            <p>{state.physicsStatus}</p></>}
          {panel === 'camera' && <>
            <CatalogPicker label="Camera bone" options={state.bones.map(option => ({ ...option, path: [option.group || 'Other bones'] }))} value={settings.bone} disabled={disabled || !state.bones.length} onChange={value => update('bone', value)} />
            {checkbox('Follow selected bone', 'follow', !state.bones.length)}{action('Focus bone', 'focus', !state.bones.length)}
            <Select label="Offset space" value={settings.space} options={[{ value: 'world', label: 'World' }, { value: 'local', label: 'Bone local' }]} onChange={value => update('space', value as 'world' | 'local')} />
            {(['X', 'Y', 'Z'] as const).map((axis, index) => <label key={axis}>Target offset {axis}<input type="number" min="-10" max="10" step="0.01" value={settings.offset[index]} onChange={event => {
              const value = Number(event.target.value); if (!Number.isFinite(value)) return;
              const offset = [...settings.offset] as [number, number, number]; offset[index] = Math.max(-10, Math.min(10, value)); update('offset', offset);
            }} /></label>)}
            <button type="button" onClick={() => update('playing', false)}>Pause animation for inspection</button>
          </>}
        </div>
      </dialog>
    </>}
  </div>;
}
