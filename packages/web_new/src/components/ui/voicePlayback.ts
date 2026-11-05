/** Coordinate voice players without interrupting a track for a failed play request. */
let requestSequence = 0;
let latestStarted = 0;
const requests = new WeakMap<HTMLAudioElement, number>();
const started = new WeakSet<HTMLAudioElement>();

export function requestVoicePlayback(audio: HTMLAudioElement) {
  requests.set(audio, ++requestSequence);
  started.delete(audio);
}

export function startVoicePlayback(audio: HTMLAudioElement) {
  if (audio.paused) return;
  const request = requests.get(audio) ?? ++requestSequence;
  if (request < latestStarted) {
    audio.pause();
    return;
  }
  latestStarted = request;
  started.add(audio);
  for (const other of document.querySelectorAll<HTMLAudioElement>('.audio-player audio')) {
    if (other !== audio && started.has(other) && !other.paused) other.pause();
  }
}
