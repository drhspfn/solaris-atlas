export type LoopRegion = { start_sample: number; end_sample: number; sample_rate: number };

export function validLoopRegion(value: unknown): value is LoopRegion {
  if (!value || typeof value !== "object") return false;
  const { start_sample, end_sample, sample_rate } = value as LoopRegion;
  return Number.isSafeInteger(start_sample) && Number.isSafeInteger(end_sample) &&
    Number.isSafeInteger(sample_rate) && sample_rate > 0 && start_sample >= 0 && end_sample > start_sample;
}

// AudioBufferSourceNode loops at the audio engine's sample clock, unlike timeupdate seeks.
export class RegionLoop {
  private context = new AudioContext();
  private gain = this.context.createGain();
  private source?: AudioBufferSourceNode;
  private buffer?: AudioBuffer;
  private loading?: Promise<AudioBuffer>;
  private offset = 0;
  private startedAt = 0;
  private generation = 0;
  private abort = new AbortController();
  paused = true;

  constructor(private url: string, private region: LoopRegion) {
    this.gain.connect(this.context.destination);
  }

  setVolume(volume: number) { this.gain.gain.value = volume; }

  async play() {
    if (!this.paused) return;
    this.paused = false;
    const generation = ++this.generation;
    try {
      const resume = this.context.resume();
      this.loading ??= fetch(this.url, { signal: this.abort.signal }).then(response => {
        if (!response.ok) throw new Error("Music download failed");
        return response.arrayBuffer();
      }).then(bytes => this.context.decodeAudioData(bytes));
      const [buffer] = await Promise.all([this.loading, resume]);
      if (this.paused || generation !== this.generation) return;
      const start = this.region.start_sample / this.region.sample_rate;
      const end = this.region.end_sample / this.region.sample_rate;
      if (end > buffer.duration + 1 / this.region.sample_rate) throw new Error("Music loop exceeds decoded audio");
      this.buffer = buffer;
      const source = this.context.createBufferSource();
      source.buffer = buffer;
      source.loop = true;
      source.loopStart = start;
      source.loopEnd = end;
      source.connect(this.gain);
      source.start(0, this.offset);
      this.startedAt = this.context.currentTime;
      this.source = source;
    } catch (error) {
      if (generation !== this.generation) return;
      this.paused = true;
      this.loading = undefined;
      throw error;
    }
  }

  pause() {
    if (this.paused) return;
    this.paused = true;
    this.generation++;
    if (!this.source || !this.buffer) return;
    const start = this.region.start_sample / this.region.sample_rate;
    const end = this.region.end_sample / this.region.sample_rate;
    const position = this.offset + this.context.currentTime - this.startedAt;
    this.offset = position < end ? position : start + (position - start) % (end - start);
    this.source.stop();
    this.source.disconnect();
    this.source = undefined;
  }

  close() {
    this.pause();
    this.abort.abort();
    this.gain.disconnect();
    void this.context.close();
  }
}
