/**
 * PCM Player AudioWorklet Processor
 * Plays back PCM 16-bit little-endian audio data
 */

const IDLE_HOLD_FRAMES = 48;

class PCMPlayerProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this.queue = [];
    this.currentChunk = null;
    this.currentChunkIndex = 0;
    this.isActive = false;
    this.idleFrames = IDLE_HOLD_FRAMES;

    this.port.onmessage = (event) => {
      if (event.data?.type !== 'pcm_data' || !event.data.buffer) {
        return;
      }

      if (event.data.buffer instanceof ArrayBuffer) {
        this.queue.push(new Int16Array(event.data.buffer));
        this.idleFrames = 0;
        return;
      }

      if (ArrayBuffer.isView(event.data.buffer)) {
        this.queue.push(
          new Int16Array(
            event.data.buffer.buffer.slice(
              event.data.buffer.byteOffset,
              event.data.buffer.byteOffset + event.data.buffer.byteLength,
            ),
          ),
        );
        this.idleFrames = 0;
      }
    };
  }

  nextSample() {
    while (true) {
      if (this.currentChunk && this.currentChunkIndex < this.currentChunk.length) {
        const sample = this.currentChunk[this.currentChunkIndex++];
        return sample / 32768;
      }

      if (this.queue.length === 0) {
        this.currentChunk = null;
        this.currentChunkIndex = 0;
        return 0;
      }

      this.currentChunk = this.queue.shift();
      this.currentChunkIndex = 0;
    }
  }

  process(inputs, outputs, parameters) {
    const outputChannels = outputs[0];
    const frameCount = outputChannels[0]?.length || 0;
    let usedQueuedAudio = false;

    for (let i = 0; i < frameCount; i++) {
      const hadQueuedAudio =
        (this.currentChunk && this.currentChunkIndex < this.currentChunk.length) ||
        this.queue.length > 0;
      const sample = this.nextSample();
      if (hadQueuedAudio) {
        usedQueuedAudio = true;
      }
      for (let channel = 0; channel < outputChannels.length; channel++) {
        outputChannels[channel][i] = sample;
      }
    }

    if (usedQueuedAudio) {
      this.idleFrames = 0;
      if (!this.isActive) {
        this.isActive = true;
        this.port.postMessage({ type: 'playback_state', active: true });
      }
    } else if (this.isActive) {
      this.idleFrames += 1;
      if (this.idleFrames >= IDLE_HOLD_FRAMES) {
        this.isActive = false;
        this.port.postMessage({ type: 'playback_state', active: false });
      }
    }

    return true;
  }
}

registerProcessor('pcm-player-processor', PCMPlayerProcessor);
