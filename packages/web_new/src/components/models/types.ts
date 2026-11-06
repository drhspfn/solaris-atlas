export type ModelAsset = { url: string; size_bytes: number };
export type ModelComponent = {
  id: string;
  parent_form_id: string;
  parent_component?: string | null;
  socket?: string | null;
  transform?: Record<string, Record<string, number>> | null;
  socket_transform?: { bone?: string; location?: Record<string, number>; rotation?: Record<string, number>; scale?: Record<string, number> } | null;
  default_visible?: boolean | null;
  baked?: boolean;
  kind?: "component" | "effect";
  bindings?: { parent_clip: string | null; child_clip: string | null; kind?: string; source?: { looping?: boolean | null } }[];
  cues?: unknown[];
};
export type ModelAssets = {
  id?: string;
  preview: ModelAsset | null;
  download: ModelAsset | null;
  physics: ModelAsset | null;
  animations?: ModelAsset | null;
  facial?: ModelAsset | null;
  linked_models?: LinkedModel[] | null;
  scene?: { format: string; components: string[]; cues: unknown[] } | null;
};
export type LinkedModel = ModelAssets & { id: string; label: string; parent_id: string; component: ModelComponent };
export type ModelForm = ModelAssets & { id: string; label: string };
export type CharacterModelData = ModelAssets & {
  status: "ready" | "missing";
  game_version: string | null;
  forms?: ModelForm[] | null;
};
export type ViewerSettings = {
  playing: boolean;
  speed: number;
  turntable: boolean;
  floor: boolean;
  physics: boolean;
  strength: number;
  lock: boolean;
  axes: { x: boolean; y: boolean; z: boolean };
  bone: string;
  follow: boolean;
  space: "world" | "local";
  offset: [number, number, number];
};
export const defaultSettings: ViewerSettings = {
  playing: false,
  speed: 1,
  turntable: false,
  floor: true,
  physics: true,
  strength: 1,
  lock: false,
  axes: { x: true, y: false, z: true },
  bone: "",
  follow: false,
  space: "world",
  offset: [0, 0, 0],
};
export type ViewerOption = {
  value: string;
  label: string;
  group?: string;
  path?: string[];
};
export type ViewerState = {
  components: { id: string; name: string; visible: boolean; override: boolean | null; loading: boolean; error: string }[];
  accessories: { name: string; visible: boolean }[];
  ready: boolean;
  error: string;
  status: string;
  busy: boolean;
  loading: Record<"quality" | "animations" | "facial", boolean>;
  loadErrors: Record<"quality" | "animations" | "facial", string>;
  fullQuality: boolean;
  animationsLoaded: boolean;
  facialLoaded: boolean;
  animations: ViewerOption[];
  bones: ViewerOption[];
  faces: ViewerOption[];
  animation: string;
  face: string;
  time: number;
  duration: number;
  physicsBones: number;
  physicsStatus: string;
  rootAvailable: boolean;
  preferredBone: string;
};
export const initialViewerState: ViewerState = {
  components: [],
  accessories: [],
  ready: false,
  error: "",
  status: "Loading model…",
  busy: false,
  loading: { quality: false, animations: false, facial: false },
  loadErrors: { quality: "", animations: "", facial: "" },
  fullQuality: false,
  animationsLoaded: false,
  facialLoaded: false,
  animations: [],
  bones: [],
  faces: [],
  animation: "",
  face: "auto",
  time: 0,
  duration: 0,
  physicsBones: 0,
  physicsStatus: "No physics profile loaded",
  rootAvailable: false,
  preferredBone: "",
};
export type ViewerCommand =
  | "reset"
  | "left"
  | "right"
  | "closer"
  | "farther"
  | "focus"
  | "quality"
  | "animations"
  | "facial";
export type ViewerController = {
  component: (id: string, visible: boolean | null) => void;
  accessory: (name: string, visible: boolean) => void;
  settings: (settings: ViewerSettings) => void;
  command: (command: ViewerCommand) => Promise<void>;
  animation: (name: string) => void;
  face: (name: string) => void;
  seek: (time: number) => void;
  dispose: () => void;
};
