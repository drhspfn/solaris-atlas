export type ModelAsset = { url: string; size_bytes: number };
export type ModelAssets = {
  preview: ModelAsset | null;
  download: ModelAsset | null;
  physics: ModelAsset | null;
  animations?: ModelAsset | null;
  facial?: ModelAsset | null;
};
export type ModelForm = ModelAssets & { id: string; label: string };
export type CharacterModelData = ModelAssets & {
  status: 'ready' | 'missing';
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
  space: 'world' | 'local';
  offset: [number, number, number];
};
export const defaultSettings: ViewerSettings = {
  playing: false, speed: 1, turntable: false, floor: true,
  physics: true, strength: 1, lock: false, axes: { x: true, y: false, z: true },
  bone: '', follow: false, space: 'world', offset: [0, 0, 0],
};
export type ViewerOption = { value: string; label: string; group?: string; path?: string[] };
export type ViewerState = {
  ready: boolean;
  error: string;
  status: string;
  busy: boolean;
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
  ready: false, error: '', status: 'Loading model…', busy: false,
  fullQuality: false, animationsLoaded: false, facialLoaded: false,
  animations: [], bones: [], faces: [], animation: '', face: 'auto',
  time: 0, duration: 0, physicsBones: 0, physicsStatus: 'No physics profile loaded', rootAvailable: false, preferredBone: '',
};
export type ViewerCommand =
  | 'reset' | 'left' | 'right' | 'closer' | 'farther' | 'focus'
  | 'quality' | 'animations' | 'facial';
export type ViewerController = {
  settings: (settings: ViewerSettings) => void;
  command: (command: ViewerCommand) => Promise<void>;
  animation: (name: string) => void;
  face: (name: string) => void;
  seek: (time: number) => void;
  dispose: () => void;
};
