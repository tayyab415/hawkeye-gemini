/**
 * PCM Recorder AudioWorklet Processor
 * Records audio from microphone and outputs PCM 16-bit 16kHz mono.
 *
 * The AudioContext may run at the browser's native sample rate (typically
 * 48 kHz on macOS).  This worklet resamples from `sampleRate` (the global
 * AudioWorklet variable reflecting the context's actual rate) down to
 * TARGET_SAMPLE_RATE (16 kHz) using nearest-neighbour decimation — the
 * same approach Google's ADK audio-processor.js uses.
 */

const TARGET_SAMPLE_RATE = 16000; // Gemini input requirement

class PCMRecorderProcessor extends AudioWorkletProcessor {
  constructor(options) {
    super();

    // sampleRate is a global provided by AudioWorkletGlobalScope and
    // reflects the *actual* context sample rate (could be 44100 or 48000).
    this.inputSampleRate = sampleRate;
    this.resampleRatio = this.inputSampleRate / TARGET_SAMPLE_RATE;

    // Buffer size in *output* (16 kHz) samples.
    this.bufferSize = options.processorOptions?.bufferSize || 4096;
    this.buffer = new Float32Array(this.bufferSize);
    this.bufferIndex = 0;

    // Track fractional position for accurate resampling across process() calls
    this.resampleOffset = 0;

    this.port.onmessage = (event) => {
      if (event.data?.type === 'flush' && this.bufferIndex > 0) {
        this.flushBuffer(this.bufferIndex);
        this.bufferIndex = 0;
      }
    };
  }

  process(inputs, outputs, parameters) {
    const input = inputs[0];
    if (!input || !input[0]) return true;

    const inputChannel = input[0]; // Float32Array, typically 128 samples

    if (this.resampleRatio <= 1.001) {
      // No resampling needed — context is already at or below 16 kHz
      for (let i = 0; i < inputChannel.length; i++) {
        this.buffer[this.bufferIndex++] = inputChannel[i];
        if (this.bufferIndex >= this.bufferSize) {
          this.flushBuffer(this.bufferSize);
          this.bufferIndex = 0;
        }
      }
    } else {
      // Resample: pick every Nth sample (nearest-neighbour decimation)
      // Use fractional offset for sample-accurate positioning across calls.
      let pos = this.resampleOffset;
      while (pos < inputChannel.length) {
        this.buffer[this.bufferIndex++] = inputChannel[Math.floor(pos)];
        pos += this.resampleRatio;

        if (this.bufferIndex >= this.bufferSize) {
          this.flushBuffer(this.bufferSize);
          this.bufferIndex = 0;
        }
      }
      // Keep the fractional remainder for the next call
      this.resampleOffset = pos - inputChannel.length;
    }

    return true;
  }

  flushBuffer(length) {
    if (!length) return;

    // Convert Float32 (-1.0 to 1.0) to Int16 (-32768 to 32767)
    const int16Buffer = new Int16Array(length);
    for (let i = 0; i < length; i++) {
      const sample = Math.max(-1, Math.min(1, this.buffer[i]));
      int16Buffer[i] = sample < 0 ? sample * 32768 : sample * 32767;
    }

    // Send as ArrayBuffer (raw PCM 16-bit 16kHz mono)
    this.port.postMessage(
      { type: 'pcm_data', buffer: int16Buffer.buffer },
      [int16Buffer.buffer]
    );
  }
}

registerProcessor('pcm-recorder-processor', PCMRecorderProcessor);
