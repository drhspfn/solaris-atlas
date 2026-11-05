"use client";

import {
  Component,
  lazy,
  Suspense,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import {
  ArrowDownToLine,
  Box,
  ChevronLeft,
  ChevronRight,
  Focus,
  Maximize2,
  Pause,
  Play,
  RotateCcw,
  RotateCw,
  Settings2,
  Scan,
  SlidersHorizontal,
  X,
  ZoomIn,
  ZoomOut,
} from "lucide-react";
import { Button } from "@/components/ui/Button";
import { Checkbox } from "@/components/ui/Checkbox";
import { Input } from "@/components/ui/Input";
import { Slider } from "@/components/ui/Slider";
import { Tabs } from '@/components/ui/Tabs';
import { Select } from "@/components/ui/Select";
import { apiUrl } from "@/api/client";
import {
  defaultSettings,
  initialViewerState,
  type ModelAssets,
  type ViewerCommand,
  type ViewerController,
  type ViewerSettings,
  type ViewerState,
} from "./types";
import "@/styles/model-viewer.css";

const stamp = (value: number) =>
  `${Math.floor(value / 60)}:${String(Math.floor(value % 60)).padStart(2, "0")}`;
const label = (name: string, bytes?: number) =>
  bytes && bytes > 0 ? `${name} · ${(bytes / 1048576).toFixed(1)} MiB` : name;

class CanvasBoundary extends Component<
  { children: ReactNode; retry: () => void },
  { failed: boolean }
> {
  state = { failed: false };
  static getDerivedStateFromError() {
    return { failed: true };
  }
  render() {
    return this.state.failed ? (
      <div className="character-model-feedback" role="alert">
        <p>Viewer code could not be loaded.</p>
        <Button onClick={this.props.retry}>Retry viewer</Button>
      </div>
    ) : (
      this.props.children
    );
  }
}

export function ModelViewer({
  assets: source,
  name,
  preview = false,
  active = true,
  titleId,
  formSelector,
  onClose,
}: {
  assets: ModelAssets;
  name: string;
  preview?: boolean;
  active?: boolean;
  titleId?: string;
  formSelector?: ReactNode;
  onClose?: () => void;
}) {
  const [assets] = useState<ModelAssets>(
    () =>
      Object.fromEntries(
        Object.entries(source).map(([key, asset]) => [
          key,
          asset && typeof asset === "object" && "url" in asset
            ? { ...asset, url: apiUrl(asset.url.replace(/^\/api(?=\/)/, "")) }
            : asset,
        ]),
      ) as ModelAssets,
  );
  const [settings, setSettings] = useState<ViewerSettings>(defaultSettings);
  const [state, setState] = useState<ViewerState>(initialViewerState);
  const [attempt, setAttempt] = useState(0);
  // A new lazy boundary retries failed chunk downloads instead of caching the rejection.
  const ModelCanvas = useMemo(
    () => lazy(() => import("./ModelCanvas")),
    [attempt],
  );
  const [panel, setPanel] = useState<"scene" | "physics" | "camera">("scene");
  const controller = useRef<ViewerController | null>(null);
  const drawer = useRef<HTMLDialogElement>(null);
  const root = useRef<HTMLDivElement>(null);
  const report = useCallback((patch: Partial<ViewerState>) => {
    setState((current) => ({ ...current, ...patch }));
    if (patch.bones)
      setSettings((current) => ({
        ...current,
        bone: patch.bones?.some((bone) => bone.value === current.bone)
          ? current.bone
          : patch.preferredBone || "",
      }));
  }, []);
  const connect = useCallback((value: ViewerController | null) => {
    controller.current = value;
  }, []);
  const update = <K extends keyof ViewerSettings>(
    key: K,
    value: ViewerSettings[K],
  ) => setSettings((current) => ({ ...current, [key]: value }));
  const retry = () => {
    setState(initialViewerState);
    setAttempt((value) => value + 1);
  };
  useEffect(() => {
    if (!preview && !matchMedia("(prefers-reduced-motion: reduce)").matches)
      update("playing", true);
  }, [preview]);
  useEffect(() => {
    if (!active || state.ready || state.error) return;
    const timer = setTimeout(
      () => report({ error: "Model loading timed out. Retry the viewer." }),
      45000,
    );
    return () => clearTimeout(timer);
  }, [active, state.ready, state.error, attempt, report]);
  const command = (value: ViewerCommand) => {
    void controller.current?.command(value);
  };
  const disabled = !state.ready || state.busy;
  const clipIndex = state.animations.findIndex(
    (item) => item.value === state.animation,
  );
  const action = (
    text: string,
    value: ViewerCommand,
    unavailable = false,
    icon: ReactNode = <Focus size={18} />,
  ) => (
    <Button
      size="icon"
      variant="ghost"
      aria-label={text}
      title={text}
      icon={icon}
      disabled={disabled || unavailable}
      onClick={() => command(value)}
    />
  );
  const checkbox = (
    text: string,
    key: "playing" | "floor" | "turntable" | "physics" | "lock" | "follow",
    unavailable = false,
  ) => (
    <Checkbox
      label={text}
      checked={settings[key]}
      disabled={disabled || unavailable}
      onChange={(event) => update(key, event.target.checked)}
    />
  );
  return (
    <div
      ref={root}
      className={`native-model-viewer${preview ? " native-model-preview" : ""}`}
      aria-label={`3D ${name}`}
    >
      {!preview && (
        <header className="model-studio-header">
          <h2 id={titleId}>
            {name} <span>· 3D model</span>
          </h2>
          <div className="model-studio-actions">
            {formSelector}
            <Button
              size="icon"
              variant="ghost"
              aria-label="Model settings"
              title="Model settings"
              icon={<Settings2 size={19} />}
              onClick={() => drawer.current?.showModal()}
            />
            {onClose && (
              <Button
                className="model-studio-close"
                size="icon"
                variant="ghost"
                autoFocus
                aria-label="Close 3D viewer"
                title="Close viewer"
                icon={<X size={20} />}
                onClick={onClose}
              />
            )}
          </div>
        </header>
      )}
      <div
        className="model-stage"
        aria-busy={active && !state.ready && !state.error}
      >
        {active && !(state.error && !state.ready) && (
          <CanvasBoundary key={attempt} retry={retry}>
            <Suspense
              fallback={
                <p className="character-model-feedback" role="status">
                  Loading viewer…
                </p>
              }
            >
              <ModelCanvas
                assets={assets}
                preview={preview}
                settings={settings}
                onState={report}
                onController={connect}
              />
            </Suspense>
          </CanvasBoundary>
        )}
        {!state.ready && !state.error && (
          <p className="model-stage-status" role="status">
            {active ? state.status : "Preview loads when visible"}
          </p>
        )}
        {state.error && (
          <div className="model-stage-status" role="alert">
            <p>{state.error}</p>
            {!state.ready && <Button onClick={retry}>Retry viewer</Button>}
          </div>
        )}
        {!preview && <div className="model-stage-tools">
              <div className="model-camera-tools" aria-label="Camera controls">
                {action("Rotate left", "left", false, <RotateCcw size={18} />)}
                {action("Rotate right", "right", false, <RotateCw size={18} />)}
                {action("Zoom in", "closer", false, <ZoomIn size={18} />)}
                {action("Zoom out", "farther", false, <ZoomOut size={18} />)}
                <Button
                  size="icon"
                  variant="ghost"
                  aria-label="Reset view"
                  title="Reset view"
                  icon={<Scan size={18} />}
                  disabled={disabled}
                  onClick={() => {
                    update("follow", false);
                    command("reset");
                  }}
                />
              </div>
          <div className="model-stage-fullscreen">
            <Button
              size="icon"
              variant="ghost"
              aria-label="Fullscreen"
              title="Fullscreen"
              icon={<Maximize2 size={19} />}
              onClick={async () => {
                try {
                  if (document.fullscreenElement)
                    await document.exitFullscreen();
                  else await root.current?.requestFullscreen();
                } catch {
                  report({
                    error: "Fullscreen is unavailable in this browser.",
                  });
                }
              }}
            />
          </div>
        </div>}
      </div>
      {!preview && (
        <>
          <div className="model-controls">
            <div className="model-transport">
              <div className="model-playback">
                <Button
                  size="icon"
                  variant="ghost"
                  aria-label={
                    settings.playing ? "Pause animation" : "Play animation"
                  }
                  title={
                    settings.playing ? "Pause animation" : "Play animation"
                  }
                  icon={
                    settings.playing ? <Pause size={19} /> : <Play size={19} />
                  }
                  disabled={
                    disabled ||
                    (!state.animations.length && !state.facialLoaded)
                  }
                  onClick={() => update("playing", !settings.playing)}
                />
                <Slider
                  className="model-timeline"
                  aria-label="Animation position"
                  min={0}
                  max={state.duration || 0.001}
                  step="0.01"
                  disabled={disabled || !state.duration}
                  value={state.time}
                  onChange={(event) =>
                    controller.current?.seek(Number(event.target.value))
                  }
                />
                <output className="model-duration" aria-label="Animation time">
                  {stamp(state.time)} <span>/ {stamp(state.duration)}</span>
                </output>
                <Select
                  className="model-speed"
                  label="Playback speed"
                  value={settings.speed}
                  disabled={disabled}
                  options={[0.25, 0.5, 1, 1.5, 2].map((value) => ({
                    value,
                    label: `${value}×`,
                  }))}
                  onChange={(value) => update("speed", Number(value))}
                />
              </div>
            </div>
            <div className="model-load-actions" aria-busy={state.busy}>
              {(
                [
                  [
                    "quality",
                    assets.download && !state.fullQuality,
                    label("Load full quality", assets.download?.size_bytes),
                    <Box size={16} />,
                  ],
                  [
                    "animations",
                    assets.animations && !state.animationsLoaded,
                    label("Load animations", assets.animations?.size_bytes),
                    <ArrowDownToLine size={16} />,
                  ],
                  [
                    "facial",
                    assets.facial && !state.facialLoaded,
                    label(
                      "Load face + full model",
                      (assets.facial?.size_bytes || 0) +
                        (state.fullQuality
                          ? 0
                          : assets.download?.size_bytes || 0),
                    ),
                    <ArrowDownToLine size={16} />,
                  ],
                ] as const
              ).map(
                ([kind, visible, text, icon]) =>
                  visible && (
                    <div className="model-load-action" key={kind}>
                      <Button
                        variant="default"
                        size="sm"
                        icon={icon}
                        loading={state.loading[kind]}
                        disabled={!state.ready}
                        onClick={() => command(kind)}
                      >
                        {state.loadErrors[kind] ? `Retry: ${text}` : text}
                      </Button>
                      {state.loadErrors[kind] && (
                        <span role="alert">{state.loadErrors[kind]}</span>
                      )}
                    </div>
                  ),
              )}
              <span role="status">
                {state.fullQuality ? "Full quality" : "Compact model"}
              </span>
            </div>
            {state.animations.length > 0 && (
              <div className="model-animation-controls">
                <Button
                  size="icon"
                  variant="ghost"
                  aria-label="Previous animation"
                  title="Previous animation"
                  icon={<ChevronLeft size={19} />}
                  disabled={disabled || clipIndex <= 0}
                  onClick={() =>
                    controller.current?.animation(
                      state.animations[clipIndex - 1].value,
                    )
                  }
                />
                <Select
                  className="model-animation-select"
                  width="compact"
                  searchable
                  categorized
                  rememberPosition
                  label="Animation"
                  options={state.animations}
                  value={state.animation}
                  disabled={disabled}
                  onChange={(value) => controller.current?.animation(value)}
                />
                <Button
                  size="icon"
                  variant="ghost"
                  aria-label="Next animation"
                  title="Next animation"
                  icon={<ChevronRight size={19} />}
                  disabled={
                    disabled ||
                    clipIndex < 0 ||
                    clipIndex >= state.animations.length - 1
                  }
                  onClick={() =>
                    controller.current?.animation(
                      state.animations[clipIndex + 1].value,
                    )
                  }
                />
              </div>
            )}
            {state.facialLoaded && (
              <Select
                searchable
                rememberPosition
                label="Facial animation"
                value={state.face}
                options={state.faces}
                disabled={disabled}
                onChange={(value) => controller.current?.face(value)}
              />
            )}
          </div>
          <dialog
            ref={drawer}
            className="model-settings"
            aria-label="Model settings"
            onClick={(event) => {
              if (event.target === event.currentTarget) drawer.current?.close();
            }}
          >
            <header>
              <h3>
                <SlidersHorizontal size={18} /> Model settings
              </h3>
              <Button
                size="icon"
                variant="ghost"
                autoFocus
                aria-label="Close settings"
                title="Close settings"
                icon={<X size={20} />}
                onClick={() => drawer.current?.close()}
              />
            </header>
            <Tabs value={panel} onValueChange={setPanel} variant="underline" fullWidth aria-label="Model settings sections"
              items={[{ value: 'scene', label: 'Scene' }, { value: 'physics', label: 'Physics' }, { value: 'camera', label: 'Camera' }]} />
            <div className="model-settings-content">
              {panel === "scene" && (
                <>
                  {checkbox("Show floor and world axes", "floor")}
                  {checkbox("Turntable", "turntable")}
                  {checkbox("Lock root position", "lock", !state.rootAvailable)}
                  <fieldset disabled={disabled || !settings.lock}>
                    <legend>Lock world axes</legend>
                    {(["x", "y", "z"] as const).map((axis) => (
                      <Checkbox
                        key={axis}
                        label={axis.toUpperCase()}
                        checked={settings.axes[axis]}
                        onChange={(event) =>
                          update("axes", {
                            ...settings.axes,
                            [axis]: event.target.checked,
                          })
                        }
                      />
                    ))}
                  </fieldset>
                  <p>
                    Axis locks change the preview only. Y remains free by
                    default for jumps.
                  </p>
                </>
              )}
              {panel === "physics" && (
                <>
                  {checkbox(
                    "Spring-bone physics",
                    "physics",
                    !state.physicsBones,
                  )}
                  <label>
                    Strength · {Math.round(settings.strength * 100)}%
                    <Slider
                      min="0"
                      max="2"
                      step="0.05"
                      value={settings.strength}
                      disabled={
                        disabled || !settings.physics || !state.physicsBones
                      }
                      onChange={(event) =>
                        update("strength", Number(event.target.value))
                      }
                    />
                  </label>
                  <p>{state.physicsStatus}</p>
                </>
              )}
              {panel === "camera" && (
                <>
                  <Select
                    searchable
                    categorized
                    rememberPosition
                    label="Camera bone"
                    options={state.bones.map((option) => ({
                      ...option,
                      path: [option.group || "Other bones"],
                    }))}
                    value={settings.bone}
                    disabled={disabled || !state.bones.length}
                    onChange={(value) => update("bone", value)}
                  />
                  {checkbox(
                    "Follow selected bone",
                    "follow",
                    !state.bones.length,
                  )}
                  <Button icon={<Focus size={18} />} disabled={disabled || !state.bones.length} onClick={() => command("focus")}>Focus camera on bone</Button>
                  <Select
                    label="Target offset coordinates"
                    value={settings.space}
                    options={[
                      { value: "world", label: "World coordinates" },
                      { value: "local", label: "Relative to bone" },
                    ]}
                    onChange={(value) =>
                      update("space", value as "world" | "local")
                    }
                  />
                  {(["X", "Y", "Z"] as const).map((axis, index) => (
                    <label key={axis}>
                      Target offset {axis} (m)
                      <Input
                        type="number"
                        min="-10"
                        max="10"
                        step="0.01"
                        value={settings.offset[index]}
                        onChange={(event) => {
                          const value = Number(event.target.value);
                          if (!Number.isFinite(value)) return;
                          const offset = [...settings.offset] as [
                            number,
                            number,
                            number,
                          ];
                          offset[index] = Math.max(-10, Math.min(10, value));
                          update("offset", offset);
                        }}
                      />
                    </label>
                  ))}
                  <p>Offsets are measured in metres from the selected exported bone. World coordinates stay aligned to the scene; bone-relative offsets rotate with the bone.</p>
                  <Button
                    variant="default"
                    icon={<Pause size={16} />}
                    onClick={() => update("playing", false)}
                  >
                    Pause for inspection
                  </Button>
                </>
              )}
            </div>
          </dialog>
        </>
      )}
    </div>
  );
}
