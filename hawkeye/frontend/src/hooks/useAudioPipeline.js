/**
 * useAudioPipeline Hook
 * Manages AudioWorklet for recording and playback
 * PCM 16-bit 16kHz recording, PCM 24kHz playback
 */

import { useCallback, useEffect, useRef, useState } from 'react';

// Audio configuration
// NOTE: We intentionally do NOT force the recording AudioContext to 16 kHz.
// macOS Chrome may report 16 kHz but still feed 48 kHz data through
// createMediaStreamSource, causing garbled transcription (random languages).
// Instead the AudioWorklet resamples from the native rate down to 16 kHz.
const PLAYBACK_SAMPLE_RATE = 24000;   // 24kHz from Gemini output
const CHANNELS = 1;
const BUFFER_SIZE = 4096;

function resolveBooleanFlag(value, defaultValue = false) {
  if (typeof value === 'boolean') {
    return value;
  }

  if (typeof value !== 'string') {
    return defaultValue;
  }

  const normalized = value.trim().toLowerCase();
  if (['1', 'true', 'yes', 'on'].includes(normalized)) {
    return true;
  }
  if (['0', 'false', 'no', 'off'].includes(normalized)) {
    return false;
  }

  return defaultValue;
}

function normalizeToArrayBuffer(audioBuffer) {
  if (audioBuffer instanceof ArrayBuffer) {
    return audioBuffer.slice(0);
  }

  if (ArrayBuffer.isView(audioBuffer)) {
    return audioBuffer.buffer.slice(
      audioBuffer.byteOffset,
      audioBuffer.byteOffset + audioBuffer.byteLength,
    );
  }

  return null;
}

function describeConnectionIssue(connectionStatus) {
  if (connectionStatus === 'connecting') {
    return 'Connecting to backend... mic not ready yet.';
  }
  if (connectionStatus === 'reconnecting') {
    return 'Backend reconnecting... voice uplink paused.';
  }
  if (connectionStatus === 'error' || connectionStatus === 'disconnected') {
    return 'Backend connection unavailable. Check server link before using mic.';
  }
  return null;
}

function describeAudioDeliveryIssue(delivery) {
  const reason = delivery?.reason || 'unknown';
  if (reason === 'retry_window_buffered') {
    return 'Voice uplink unstable — buffering microphone audio.';
  }
  if (reason === 'socket_not_open') {
    return 'Voice uplink unavailable — backend socket is not open.';
  }
  if (reason === 'audio_queue_overflow') {
    return 'Voice uplink overloaded — buffered microphone audio was dropped.';
  }
  if (reason === 'invalid_audio_payload') {
    return 'Microphone capture failed — invalid audio payload.';
  }
  return 'Voice uplink error — microphone audio was not delivered.';
}

export function useAudioPipeline(socket, options = {}) {
  const [isRecording, setIsRecording] = useState(false);
  const [isPlaying, setIsPlaying] = useState(false);
  const [isInputMuted, setIsInputMuted] = useState(false);
  const [error, setError] = useState(null);
  const [micStatus, setMicStatus] = useState(() => ({
    tone: 'idle',
    message: 'Mic disarmed',
  }));

  const manualActivitySignalsEnabledRef = useRef(
    resolveBooleanFlag(
      options?.manualActivitySignalsEnabled,
      resolveBooleanFlag(import.meta.env.VITE_ENABLE_MANUAL_ACTIVITY_SIGNALS, false),
    ),
  );
  const connectionStatusRef = useRef(options?.connectionStatus || null);
  const sendAudioRef = useRef(socket?.sendAudio || null);
  const sendAudioWithStatusRef = useRef(socket?.sendAudioWithStatus || null);
  const sendActivityStartRef = useRef(socket?.sendActivityStart || null);
  const sendActivityEndRef = useRef(socket?.sendActivityEnd || null);
  const sendAudioStreamEndRef = useRef(socket?.sendAudioStreamEnd || null);
  const recordingContextRef = useRef(null);
  const playbackContextRef = useRef(null);
  const recorderNodeRef = useRef(null);
  const sourceNodeRef = useRef(null);
  const monitorGainRef = useRef(null);
  const playerNodeRef = useRef(null);
  const recorderWorkletLoadedRef = useRef(false);
  const playerWorkletLoadedRef = useRef(false);
  const playerInitPromiseRef = useRef(null);
  const streamRef = useRef(null);
  const playbackQueueRef = useRef([]);
  const isRecordingRef = useRef(false);
  const isPlayingRef = useRef(false);
  const recorderConnectedRef = useRef(false);
  const playbackGenerationRef = useRef(0);
  const activitySignalOpenRef = useRef(false);
  const lastDeliverySignatureRef = useRef(null);
  const hasTransportErrorRef = useRef(false);

  useEffect(() => {
    sendAudioRef.current = socket?.sendAudio || null;
    sendAudioWithStatusRef.current = socket?.sendAudioWithStatus || null;
    sendActivityStartRef.current = socket?.sendActivityStart || null;
    sendActivityEndRef.current = socket?.sendActivityEnd || null;
    sendAudioStreamEndRef.current = socket?.sendAudioStreamEnd || null;
  }, [socket]);

  const updateMicStatus = useCallback((message, tone = 'info') => {
    if (typeof message !== 'string' || message.trim().length === 0) {
      return;
    }
    const normalizedMessage = message.trim();
    setMicStatus((prev) => (
      prev?.message === normalizedMessage && prev?.tone === tone
        ? prev
        : {
          tone,
          message: normalizedMessage,
        }
    ));
  }, []);

  useEffect(() => {
    manualActivitySignalsEnabledRef.current = resolveBooleanFlag(
      options?.manualActivitySignalsEnabled,
      resolveBooleanFlag(import.meta.env.VITE_ENABLE_MANUAL_ACTIVITY_SIGNALS, false),
    );
    if (!manualActivitySignalsEnabledRef.current) {
      activitySignalOpenRef.current = false;
    }
  }, [options?.manualActivitySignalsEnabled]);

  useEffect(() => {
    const nextStatus = options?.connectionStatus || null;
    connectionStatusRef.current = nextStatus;
    if (isRecordingRef.current) {
      return;
    }

    const issueMessage = describeConnectionIssue(nextStatus);
    if (issueMessage) {
      updateMicStatus(issueMessage, 'warning');
      return;
    }

    updateMicStatus('Mic disarmed', 'idle');
  }, [options?.connectionStatus, updateMicStatus]);

  const getRecordingContext = useCallback(() => {
    if (!recordingContextRef.current) {
      // Use the browser's default (native) sample rate.
      // The AudioWorklet will resample from native rate → 16 kHz internally.
      // Forcing 16 kHz via AudioContext options is unreliable on macOS —
      // Chrome may accept the value but still deliver 48 kHz samples,
      // producing garbled Gemini transcription.
      recordingContextRef.current = new AudioContext();
      console.log(
        `[AUDIO] Recording AudioContext created at native ${recordingContextRef.current.sampleRate}Hz (worklet will resample to 16kHz)`,
      );
    }

    return recordingContextRef.current;
  }, []);

  const getPlaybackContext = useCallback(() => {
    if (!playbackContextRef.current) {
      playbackContextRef.current = new AudioContext({
        sampleRate: PLAYBACK_SAMPLE_RATE,
      });
      if (playbackContextRef.current.sampleRate !== PLAYBACK_SAMPLE_RATE) {
        console.warn(
          `[AUDIO] Playback sample rate clamped to ${playbackContextRef.current.sampleRate}Hz (requested ${PLAYBACK_SAMPLE_RATE}Hz)`,
        );
      }
    }

    return playbackContextRef.current;
  }, []);

  const drainPlaybackQueue = useCallback(() => {
    if (!playerNodeRef.current) return;

    while (playbackQueueRef.current.length > 0) {
      const chunk = playbackQueueRef.current.shift();
      if (!(chunk instanceof ArrayBuffer) || chunk.byteLength === 0) {
        continue;
      }

      playerNodeRef.current.port.postMessage(
        {
          type: 'pcm_data',
          buffer: chunk,
        },
        [chunk],
      );
    }
  }, []);

  const ensurePlayerNode = useCallback(async () => {
    if (playerNodeRef.current) {
      return playerNodeRef.current;
    }

    if (playerInitPromiseRef.current) {
      return playerInitPromiseRef.current;
    }

    playerInitPromiseRef.current = (async () => {
      const initGeneration = playbackGenerationRef.current;
      const playbackContext = getPlaybackContext();

      if (!playerWorkletLoadedRef.current) {
        await playbackContext.audioWorklet.addModule('/pcm-player-processor.js?v=2');
        playerWorkletLoadedRef.current = true;
      }

      const playerNode = new AudioWorkletNode(
        playbackContext,
        'pcm-player-processor',
      );
      playerNode.port.onmessage = (event) => {
        if (event.data?.type !== 'playback_state') {
          return;
        }

        const active = Boolean(event.data.active);
        isPlayingRef.current = active;
        setIsPlaying(active);
      };
      if (initGeneration !== playbackGenerationRef.current) {
        playerNode.port.onmessage = null;
        return null;
      }
      playerNode.connect(playbackContext.destination);
      playerNodeRef.current = playerNode;
      return playerNode;
    })();

    try {
      return await playerInitPromiseRef.current;
    } finally {
      playerInitPromiseRef.current = null;
    }
  }, [getPlaybackContext]);

  const emitActivityStart = useCallback((reason = 'mic_gate_open') => {
    if (
      !manualActivitySignalsEnabledRef.current
      || !isRecordingRef.current
      || activitySignalOpenRef.current
    ) {
      return;
    }

    const sendActivityStart = sendActivityStartRef.current;
    if (typeof sendActivityStart !== 'function') {
      return;
    }

    const sent = sendActivityStart({ reason });
    if (sent) {
      activitySignalOpenRef.current = true;
      console.log(`[AUDIO] Sent activity_start (${reason})`);
    }
  }, []);

  const emitActivityEnd = useCallback((reason = 'mic_gate_closed', { includeStreamEnd = false } = {}) => {
    const manualActivitySignalsEnabled = manualActivitySignalsEnabledRef.current;
    const sendActivityEnd = sendActivityEndRef.current;
    if (manualActivitySignalsEnabled && activitySignalOpenRef.current && typeof sendActivityEnd === 'function') {
      const sent = sendActivityEnd({ reason });
      if (sent) {
        console.log(`[AUDIO] Sent activity_end (${reason})`);
      }
    }
    activitySignalOpenRef.current = false;

    if (!includeStreamEnd) {
      return;
    }

    const sendAudioStreamEnd = sendAudioStreamEndRef.current;
    if (typeof sendAudioStreamEnd === 'function') {
      const sent = sendAudioStreamEnd({ reason });
      if (sent) {
        console.log(`[AUDIO] Sent audio_stream_end (${reason})`);
      }
    }
  }, []);

  const connectRecorderInput = useCallback(() => {
    const sourceNode = sourceNodeRef.current;
    const recorderNode = recorderNodeRef.current;

    if (!sourceNode || !recorderNode || recorderConnectedRef.current) {
      return;
    }

    sourceNode.connect(recorderNode);
    recorderConnectedRef.current = true;
    setIsInputMuted(false);
    if (!isPlayingRef.current && !hasTransportErrorRef.current) {
      updateMicStatus('Mic armed and listening', 'active');
    }
    console.log('[AUDIO] Mic input gate opened');
    emitActivityStart('mic_gate_open');
  }, [emitActivityStart, updateMicStatus]);

  const disconnectRecorderInput = useCallback((reason = 'playback') => {
    const sourceNode = sourceNodeRef.current;
    const recorderNode = recorderNodeRef.current;

    if (!sourceNode || !recorderNode || !recorderConnectedRef.current) {
      if (isRecordingRef.current) {
        setIsInputMuted(true);
      }
      return;
    }

    recorderNode.port.postMessage({ type: 'flush' });
    sourceNode.disconnect();
    recorderConnectedRef.current = false;
    if (isRecordingRef.current) {
      setIsInputMuted(true);
    }
    emitActivityEnd(reason);
    console.log(`[AUDIO] Mic input gate closed (${reason})`);
  }, [emitActivityEnd]);

  const syncRecorderInputGate = useCallback((reason = 'state_change') => {
    if (!sourceNodeRef.current || !recorderNodeRef.current) {
      return;
    }

    if (!isRecordingRef.current) {
      disconnectRecorderInput(reason);
      return;
    }

    if (isPlayingRef.current) {
      disconnectRecorderInput(reason);
      return;
    }

    connectRecorderInput();
  }, [connectRecorderInput, disconnectRecorderInput]);

  const resetPlaybackState = useCallback((reason = 'reset') => {
    playbackGenerationRef.current += 1;
    playbackQueueRef.current = [];
    const playerNode = playerNodeRef.current;
    if (playerNode) {
      playerNode.port.onmessage = null;
      try {
        playerNode.disconnect();
      } catch (err) {
        console.warn('[AUDIO] Failed to disconnect player node during reset:', err);
      }
      playerNodeRef.current = null;
    }

    isPlayingRef.current = false;
    setIsPlaying(false);
    syncRecorderInputGate(reason);
    console.log(`[AUDIO] Playback state reset (${reason})`);
  }, [syncRecorderInputGate]);

  const prepareAudio = useCallback(async () => {
    try {
      console.log('[AUDIO] prepareAudio called');
      const playbackContext = getPlaybackContext();
      console.log('[AUDIO] Playback AudioContext created/retrieved, sampleRate:', playbackContext.sampleRate, 'state:', playbackContext.state);
      await ensurePlayerNode();
      console.log('[AUDIO] Player worklet node ensured');
      if (playbackContext.state === 'suspended') {
        await playbackContext.resume();
        console.log('[AUDIO] Playback AudioContext resumed, state:', playbackContext.state);
      }
      drainPlaybackQueue();
      console.log('[AUDIO] prepareAudio complete');
      return true;
    } catch (err) {
      console.error('[AudioPipeline] Audio preparation failed:', err);
      setError('Audio playback initialization failed');
      return false;
    }
  }, [drainPlaybackQueue, ensurePlayerNode, getPlaybackContext]);

  // Start recording
  const startRecording = useCallback(async () => {
    console.log('[AUDIO] startRecording called, isRecording:', isRecordingRef.current, 'hasRecorderNode:', !!recorderNodeRef.current);
    if (isRecordingRef.current || recorderNodeRef.current) return;

    const connectionIssue = describeConnectionIssue(connectionStatusRef.current);
    if (connectionIssue) {
      hasTransportErrorRef.current = true;
      setError(connectionIssue);
      updateMicStatus(connectionIssue, 'error');
      return;
    }

    if (
      typeof sendAudioWithStatusRef.current !== 'function'
      && typeof sendAudioRef.current !== 'function'
    ) {
      const uplinkUnavailableMessage = 'Voice uplink unavailable. Reconnect and retry.';
      hasTransportErrorRef.current = true;
      setError(uplinkUnavailableMessage);
      updateMicStatus(uplinkUnavailableMessage, 'error');
      return;
    }

    try {
      activitySignalOpenRef.current = false;
      const recordingContext = getRecordingContext();
      console.log('[AUDIO] Recording AudioContext created/retrieved, sampleRate:', recordingContext.sampleRate, 'state:', recordingContext.state);
      if (recordingContext.state === 'suspended') {
        await recordingContext.resume();
        console.log('[AUDIO] Recording AudioContext resumed, state:', recordingContext.state);
      }

      if (!recorderWorkletLoadedRef.current) {
        console.log('[AUDIO] Loading recorder worklet from /pcm-recorder-processor.js ...');
        await recordingContext.audioWorklet.addModule('/pcm-recorder-processor.js?v=2');
        recorderWorkletLoadedRef.current = true;
        console.log('[AUDIO] Recorder worklet loaded successfully');
      }

      console.log('[AUDIO] Requesting microphone access...');
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          channelCount: CHANNELS,
          echoCancellation: true,
          noiseSuppression: true,
        },
      });
      console.log('[AUDIO] Microphone access granted, tracks:', stream.getAudioTracks().length, 'track settings:', JSON.stringify(stream.getAudioTracks()[0]?.getSettings()));
      streamRef.current = stream;

      const sourceNode = recordingContext.createMediaStreamSource(stream);
      sourceNodeRef.current = sourceNode;

      const recorderNode = new AudioWorkletNode(
        recordingContext,
        'pcm-recorder-processor',
        {
          processorOptions: {
            bufferSize: BUFFER_SIZE,
          },
        },
      );

      let chunkCount = 0;
      recorderNode.port.onmessage = (event) => {
        if (event.data?.type !== 'pcm_data' || !(event.data.buffer instanceof ArrayBuffer)) {
          return;
        }

        chunkCount++;
        if (chunkCount <= 5 || chunkCount % 50 === 0) {
          console.log('[AUDIO] Recorder chunk #' + chunkCount + ', bytes:', event.data.buffer.byteLength);
        }

        const sendAudioWithStatus = sendAudioWithStatusRef.current;
        const sendAudio = sendAudioRef.current;
        let delivery = null;
        if (typeof sendAudioWithStatus === 'function') {
          delivery = sendAudioWithStatus(event.data.buffer);
        } else if (typeof sendAudio === 'function') {
          const sent = sendAudio(event.data.buffer);
          delivery = sent
            ? { ok: true, status: 'sent', reason: 'legacy_send_audio' }
            : { ok: false, status: 'dropped', reason: 'socket_not_open' };
        }

        if (!delivery) {
          const uplinkUnavailableMessage = 'Voice uplink unavailable. Reconnect and retry.';
          if (chunkCount <= 3) {
            console.warn('[AUDIO] No audio sender available — audio chunk dropped');
          }
          if (lastDeliverySignatureRef.current !== 'dropped:no_sender') {
            lastDeliverySignatureRef.current = 'dropped:no_sender';
            hasTransportErrorRef.current = true;
            setError(uplinkUnavailableMessage);
            updateMicStatus(uplinkUnavailableMessage, 'error');
          }
          return;
        }

        const deliverySignature = `${delivery.status}:${delivery.reason || 'unknown'}`;

        if (delivery.status === 'queued') {
          if (lastDeliverySignatureRef.current !== deliverySignature) {
            lastDeliverySignatureRef.current = deliverySignature;
            const queuedMessageBase = describeAudioDeliveryIssue(delivery);
            const queueDepth = Number(delivery.queueDepth);
            const queuedMessage = Number.isFinite(queueDepth) && queueDepth > 0
              ? `${queuedMessageBase} (${queueDepth} queued)`
              : queuedMessageBase;
            updateMicStatus(queuedMessage, 'warning');
          }
          return;
        }

        if (!delivery.ok || delivery.status === 'dropped') {
          if (chunkCount <= 3) {
            console.warn('[AUDIO] Failed to send audio chunk, reason:', delivery.reason);
          }
          if (lastDeliverySignatureRef.current !== deliverySignature) {
            lastDeliverySignatureRef.current = deliverySignature;
            const droppedMessage = describeAudioDeliveryIssue(delivery);
            hasTransportErrorRef.current = true;
            setError(droppedMessage);
            updateMicStatus(droppedMessage, 'error');
          }
          return;
        }

        if (lastDeliverySignatureRef.current !== null) {
          lastDeliverySignatureRef.current = null;
          if (hasTransportErrorRef.current) {
            hasTransportErrorRef.current = false;
            setError(null);
          }
          updateMicStatus('Voice uplink restored — mic listening.', 'active');
        }
      };

      // Keep recorder alive without monitoring mic to speakers.
      const monitorGain = recordingContext.createGain();
      monitorGain.gain.value = 0;
      recorderNode.connect(monitorGain);
      monitorGain.connect(recordingContext.destination);
      console.log('[AUDIO] RecorderNode -> MonitorGain(0) -> Destination connected');

      monitorGainRef.current = monitorGain;
      recorderNodeRef.current = recorderNode;

      setError(null);
      hasTransportErrorRef.current = false;
      lastDeliverySignatureRef.current = null;
      isRecordingRef.current = true;
      setIsRecording(true);
      syncRecorderInputGate('arm');
      updateMicStatus('Mic armed and listening', 'active');
      console.log('[AUDIO] Recording armed — persistent mic session active');
    } catch (err) {
      console.error('[AudioPipeline] Recorder init error:', err);
      const recorderErrorMessage = err?.name === 'NotAllowedError'
        ? 'Microphone permission denied. Allow access and retry.'
        : 'Microphone access denied or unavailable.';
      hasTransportErrorRef.current = true;
      setError(recorderErrorMessage);
      updateMicStatus(recorderErrorMessage, 'error');
    }
  }, [getRecordingContext, syncRecorderInputGate, updateMicStatus]);

  // Stop recording
  const stopRecording = useCallback(() => {
    emitActivityEnd('recording_stopped', { includeStreamEnd: true });
    isRecordingRef.current = false;

    if (recorderNodeRef.current) {
      recorderNodeRef.current.port.postMessage({ type: 'flush' });
      recorderNodeRef.current.disconnect();
      recorderNodeRef.current = null;
    }

    if (sourceNodeRef.current && recorderConnectedRef.current) {
      sourceNodeRef.current.disconnect();
      recorderConnectedRef.current = false;
    }

    if (sourceNodeRef.current) {
      sourceNodeRef.current = null;
    }

    if (monitorGainRef.current) {
      monitorGainRef.current.disconnect();
      monitorGainRef.current = null;
    }

    if (streamRef.current) {
      streamRef.current.getTracks().forEach((track) => track.stop());
      streamRef.current = null;
    }

    setIsInputMuted(false);
    setIsRecording(false);
    lastDeliverySignatureRef.current = null;
    hasTransportErrorRef.current = false;
    updateMicStatus('Mic disarmed', 'idle');
    console.log('[AUDIO] Recording disarmed');
  }, [emitActivityEnd, updateMicStatus]);

  // Toggle recording
  const toggleRecording = useCallback(() => {
    console.log('[AUDIO] toggleRecording called, isRecording:', isRecordingRef.current);
    if (isRecordingRef.current) {
      stopRecording();
      return;
    } else {
      const connectionIssue = describeConnectionIssue(connectionStatusRef.current);
      if (connectionIssue) {
        hasTransportErrorRef.current = true;
        setError(connectionIssue);
        updateMicStatus(connectionIssue, 'error');
        return;
      }
      void (async () => {
        const audioReady = await prepareAudio();
        if (!audioReady) {
          updateMicStatus('Audio playback initialization failed.', 'error');
          return;
        }
        await startRecording();
      })();
    }
  }, [prepareAudio, startRecording, stopRecording, updateMicStatus]);

  // Play audio data (from WebSocket)
  const playAudio = useCallback((audioBuffer) => {
    const normalizedChunk = normalizeToArrayBuffer(audioBuffer);
    if (!normalizedChunk || normalizedChunk.byteLength === 0) {
      console.warn('[AUDIO] playAudio called with empty/invalid buffer');
      return;
    }

    console.log('[AUDIO] playAudio called, bytes:', normalizedChunk.byteLength, 'playerReady:', !!playerNodeRef.current);

    isPlayingRef.current = true;
    setIsPlaying(true);
    syncRecorderInputGate('playback');

    const playbackContext = getPlaybackContext();

    if (!playerNodeRef.current) {
      playbackQueueRef.current.push(normalizedChunk);
      void ensurePlayerNode()
        .then(async () => {
          if (playbackContext.state === 'suspended') {
            await playbackContext.resume();
          }
          drainPlaybackQueue();
        })
        .catch((err) => {
          console.error('[AudioPipeline] Player init error:', err);
          setError('Audio playback initialization failed');
        });
      return;
    }

    if (playbackContext.state === 'suspended') {
      playbackQueueRef.current.push(normalizedChunk);
      void playbackContext.resume()
        .then(() => {
          drainPlaybackQueue();
        })
        .catch((err) => {
          console.error('[AudioPipeline] Playback resume failed:', err);
          setError('Audio playback blocked by browser policy');
        });
      return;
    }

    playerNodeRef.current.port.postMessage(
      {
        type: 'pcm_data',
        buffer: normalizedChunk,
      },
      [normalizedChunk],
    );
  }, [drainPlaybackQueue, ensurePlayerNode, getPlaybackContext, syncRecorderInputGate]);

  const handleInterrupted = useCallback(() => {
    resetPlaybackState('interrupted');
  }, [resetPlaybackState]);

  const handleTurnComplete = useCallback(() => {
    if (
      playbackQueueRef.current.length > 0
      && !playerNodeRef.current
      && !playerInitPromiseRef.current
    ) {
      resetPlaybackState('turn_complete_no_player');
      return;
    }

    if (!isPlayingRef.current) {
      syncRecorderInputGate('turn_complete');
    }
  }, [resetPlaybackState, syncRecorderInputGate]);

  useEffect(() => {
    if (!isPlaying) {
      syncRecorderInputGate('playback_complete');
    }
  }, [isPlaying, syncRecorderInputGate]);

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      stopRecording();

      if (playerNodeRef.current) {
        playerNodeRef.current.disconnect();
      }

      if (recordingContextRef.current) {
        recordingContextRef.current.close();
      }

      if (playbackContextRef.current) {
        playbackContextRef.current.close();
      }
    };
  }, [stopRecording]);

  return {
    isRecording,
    isPlaying,
    isInputMuted,
    error,
    micStatus,
    startRecording,
    stopRecording,
    toggleRecording,
    playAudio,
    prepareAudio,
    handleTurnComplete,
    handleInterrupted,
  };
}
