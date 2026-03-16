/**
 * useHawkEyeSocket Hook
 * Manages WebSocket connection to ADK backend
 * Handles binary audio frames and text JSON messages
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import { SERVER_MESSAGE_TYPES, CLIENT_MESSAGE_TYPES, CONNECTION_STATUS } from '../types/messages';

function resolveDefaultWsUrl() {
  if (typeof window === 'undefined') return 'ws://localhost:8000/ws';
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  const hostname = window.location.hostname || 'localhost';
  return `${protocol}//${hostname}:8000/ws`;
}

function normalizeWsBaseUrl(rawUrl) {
  if (typeof rawUrl !== 'string') return null;
  const trimmed = rawUrl.trim();
  if (!trimmed) return null;
  return trimmed.replace(/\/+$/, '');
}

function appendWithRollingCap(previousEntries, nextEntry, cap) {
  const nextEntries = [...previousEntries, nextEntry];
  if (nextEntries.length <= cap) {
    return nextEntries;
  }
  return nextEntries.slice(nextEntries.length - cap);
}

function computeReconnectDelayMs(attemptNumber) {
  const normalizedAttempt = Math.max(1, attemptNumber);
  const exponentialDelay = Math.min(
    RECONNECT_MAX_DELAY_MS,
    RECONNECT_BASE_DELAY_MS * (2 ** (normalizedAttempt - 1)),
  );
  const jitterRange = Math.round(exponentialDelay * RECONNECT_JITTER_RATIO);
  const jitterOffset = Math.floor(Math.random() * ((jitterRange * 2) + 1)) - jitterRange;
  const jitteredDelay = exponentialDelay + jitterOffset;
  return Math.max(RECONNECT_BASE_DELAY_MS, Math.min(RECONNECT_MAX_DELAY_MS, jitteredDelay));
}

const WS_URL = normalizeWsBaseUrl(import.meta.env.VITE_WS_URL) || resolveDefaultWsUrl();
const RECONNECT_BASE_DELAY_MS = 1000;
const RECONNECT_MAX_DELAY_MS = 30000;
const RECONNECT_JITTER_RATIO = 0.2;
const MAX_RECONNECT_ATTEMPTS = 12;
const HEARTBEAT_CHECK_INTERVAL_MS = 10000;
const HEARTBEAT_IDLE_BEFORE_PING_MS = 45000;
const HEARTBEAT_PING_INTERVAL_MS = 60000;
const HEARTBEAT_ACK_TIMEOUT_MS = 18000;
const HEARTBEAT_MISSES_BEFORE_FORCE_RECONNECT_ACK = 2;
const HEARTBEAT_MISSES_BEFORE_FORCE_RECONNECT_PASSIVE = 5;
const MAX_SOCKET_EVENTS = 400;
const MAX_BUFFERED_JSON_MESSAGES = 120;
const MAX_BUFFERED_JSON_BYTES = 1_500_000;
const MAX_BUFFERED_AUDIO_MESSAGES = 32;
const MAX_BUFFERED_AUDIO_BYTES = 512_000;
const MAX_BUFFERED_AUDIO_AGE_MS = 12_000;

const CONNECT_DEBOUNCE_MS = 350;

function buildSendOutcome(status, reason, extra = {}) {
  const normalizedStatus = typeof status === 'string' ? status : 'dropped';
  return {
    ok: normalizedStatus === 'sent' || normalizedStatus === 'queued',
    status: normalizedStatus,
    reason: reason || null,
    ...extra,
  };
}

export function useHawkEyeSocket(userId = 'user1', sessionId = 'session1', audioCallbackRef = null) {
  const [connectionStatus, setConnectionStatus] = useState(CONNECTION_STATUS.DISCONNECTED);
  const [events, setEvents] = useState([]);
  const [connectionHealth, setConnectionHealth] = useState(() => ({
    reconnectAttempt: 0,
    maxReconnectAttempts: MAX_RECONNECT_ATTEMPTS,
    nextRetryDelayMs: null,
    staleReconnects: 0,
    heartbeatAckSupported: false,
    outboundQueueDepth: 0,
    outboundAudioBytes: 0,
  }));
  const wsRef = useRef(null);
  const reconnectAttemptsRef = useRef(0);
  const reconnectTimeoutRef = useRef(null);
  const isIntentionalCloseRef = useRef(false);
  const heartbeatIntervalRef = useRef(null);
  const heartbeatProbeTimeoutRef = useRef(null);
  const heartbeatProbeInFlightRef = useRef(false);
  const heartbeatAckSupportedRef = useRef(false);
  const heartbeatMissesRef = useRef(0);
  const lastHeartbeatSentAtRef = useRef(0);
  const lastSocketActivityAtRef = useRef(0);
  const staleReconnectCountRef = useRef(0);
  const nextEventSequenceRef = useRef(0);
  const nextQueueSequenceRef = useRef(0);
  const connectDebounceRef = useRef(null);
  const mountedRef = useRef(false);
  const connectionStatusRef = useRef(CONNECTION_STATUS.DISCONNECTED);
  const outboundQueueRef = useRef([]);
  const outboundJsonBytesRef = useRef(0);
  const outboundAudioBytesRef = useRef(0);

  const clearReconnectTimer = useCallback(() => {
    if (reconnectTimeoutRef.current) {
      clearTimeout(reconnectTimeoutRef.current);
      reconnectTimeoutRef.current = null;
    }
  }, []);

  const clearHeartbeatTimers = useCallback(() => {
    if (heartbeatIntervalRef.current) {
      clearInterval(heartbeatIntervalRef.current);
      heartbeatIntervalRef.current = null;
    }
    if (heartbeatProbeTimeoutRef.current) {
      clearTimeout(heartbeatProbeTimeoutRef.current);
      heartbeatProbeTimeoutRef.current = null;
    }
    heartbeatProbeInFlightRef.current = false;
  }, []);

  const markSocketActivity = useCallback((timestamp = Date.now()) => {
    lastSocketActivityAtRef.current = timestamp;
    heartbeatProbeInFlightRef.current = false;
    heartbeatMissesRef.current = 0;
    if (heartbeatProbeTimeoutRef.current) {
      clearTimeout(heartbeatProbeTimeoutRef.current);
      heartbeatProbeTimeoutRef.current = null;
    }
  }, []);

  const appendEvent = useCallback((payload) => {
    const timestamp = typeof payload?.timestamp === 'number' ? payload.timestamp : Date.now();
    const sequence = nextEventSequenceRef.current + 1;
    nextEventSequenceRef.current = sequence;

    setEvents((prev) => appendWithRollingCap(prev, {
      ...payload,
      timestamp,
      _seq: sequence,
    }, MAX_SOCKET_EVENTS));
  }, []);

  const setConnectionStatusTracked = useCallback((nextStatus) => {
    connectionStatusRef.current = nextStatus;
    setConnectionStatus(nextStatus);
  }, []);

  const refreshQueueHealth = useCallback(() => {
    let jsonBytes = 0;
    let audioBytes = 0;

    outboundQueueRef.current.forEach((entry) => {
      if (entry.kind === 'audio') {
        audioBytes += Number(entry.bytes) || 0;
      } else {
        jsonBytes += Number(entry.bytes) || 0;
      }
    });

    outboundJsonBytesRef.current = jsonBytes;
    outboundAudioBytesRef.current = audioBytes;
    const nextDepth = outboundQueueRef.current.length;

    setConnectionHealth((prev) => {
      if (
        prev.outboundQueueDepth === nextDepth
        && prev.outboundAudioBytes === audioBytes
      ) {
        return prev;
      }
      return {
        ...prev,
        outboundQueueDepth: nextDepth,
        outboundAudioBytes: audioBytes,
      };
    });
  }, []);

  const isRetryWindowOpen = useCallback(() => {
    if (!mountedRef.current || isIntentionalCloseRef.current) {
      return false;
    }

    if (wsRef.current?.readyState === WebSocket.CONNECTING) {
      return true;
    }

    if (connectDebounceRef.current) {
      return true;
    }

    return (
      connectionStatusRef.current === CONNECTION_STATUS.CONNECTING
      || connectionStatusRef.current === CONNECTION_STATUS.RECONNECTING
    );
  }, []);

  const canQueueReliableMessage = useCallback(() => {
    if (!mountedRef.current || isIntentionalCloseRef.current) {
      return false;
    }

    if (connectionStatusRef.current === CONNECTION_STATUS.ERROR) {
      return false;
    }

    if (
      connectionStatusRef.current === CONNECTION_STATUS.DISCONNECTED
      && reconnectAttemptsRef.current >= MAX_RECONNECT_ATTEMPTS
    ) {
      return false;
    }

    return true;
  }, []);

  const dropStaleQueuedAudio = useCallback((now = Date.now()) => {
    if (outboundQueueRef.current.length === 0) {
      return 0;
    }

    let droppedCount = 0;
    outboundQueueRef.current = outboundQueueRef.current.filter((entry) => {
      if (entry.kind !== 'audio') {
        return true;
      }
      const maxAgeMs = Number(entry.maxAgeMs);
      if (!Number.isFinite(maxAgeMs) || maxAgeMs <= 0) {
        return true;
      }
      if ((now - entry.enqueuedAt) <= maxAgeMs) {
        return true;
      }
      droppedCount += 1;
      return false;
    });

    if (droppedCount > 0) {
      refreshQueueHealth();
    }
    return droppedCount;
  }, [refreshQueueHealth]);

  const enqueueOutboundPayload = useCallback((entry) => {
    const nextSequence = nextQueueSequenceRef.current + 1;
    nextQueueSequenceRef.current = nextSequence;
    const queuedEntry = {
      ...entry,
      sequence: nextSequence,
      enqueuedAt: Date.now(),
    };

    const queue = outboundQueueRef.current;
    queue.push(queuedEntry);

    const removedEntries = [];
    const removeAtIndex = (index) => {
      if (index < 0 || index >= queue.length) return;
      const [removed] = queue.splice(index, 1);
      if (removed) {
        removedEntries.push(removed);
      }
    };

    const countKind = (kind) => queue.reduce(
      (count, candidate) => (candidate.kind === kind ? count + 1 : count),
      0,
    );
    const bytesKind = (kind) => queue.reduce(
      (total, candidate) => (candidate.kind === kind ? total + (Number(candidate.bytes) || 0) : total),
      0,
    );
    const findOldestKindIndex = (kind) => queue.findIndex((candidate) => candidate.kind === kind);

    while (countKind('json') > MAX_BUFFERED_JSON_MESSAGES) {
      removeAtIndex(findOldestKindIndex('json'));
    }
    while (bytesKind('json') > MAX_BUFFERED_JSON_BYTES) {
      removeAtIndex(findOldestKindIndex('json'));
    }
    while (countKind('audio') > MAX_BUFFERED_AUDIO_MESSAGES) {
      removeAtIndex(findOldestKindIndex('audio'));
    }
    while (bytesKind('audio') > MAX_BUFFERED_AUDIO_BYTES) {
      removeAtIndex(findOldestKindIndex('audio'));
    }

    refreshQueueHealth();

    const queued = queue.includes(queuedEntry);
    return {
      queued,
      queueDepth: queue.length,
      droppedEntries: removedEntries,
    };
  }, [refreshQueueHealth]);

  const flushOutboundQueue = useCallback((ws) => {
    if (!ws || ws.readyState !== WebSocket.OPEN || outboundQueueRef.current.length === 0) {
      return;
    }

    const currentQueue = outboundQueueRef.current;
    const nextQueue = [];
    const now = Date.now();
    let blocked = false;
    let flushedCount = 0;
    let retriedCommandCount = 0;
    let droppedStaleAudio = 0;

    currentQueue.forEach((entry) => {
      if (blocked) {
        nextQueue.push(entry);
        return;
      }

      if (
        entry.kind === 'audio'
        && Number.isFinite(entry.maxAgeMs)
        && entry.maxAgeMs > 0
        && (now - entry.enqueuedAt) > entry.maxAgeMs
      ) {
        droppedStaleAudio += 1;
        return;
      }

      try {
        ws.send(entry.payload);
        flushedCount += 1;
        if (entry.messageType === CLIENT_MESSAGE_TYPES.TEXT) {
          retriedCommandCount += 1;
        }
      } catch (error) {
        blocked = true;
        nextQueue.push(entry);
      }
    });

    outboundQueueRef.current = nextQueue;
    refreshQueueHealth();

    if (retriedCommandCount > 0) {
      appendEvent({
        type: SERVER_MESSAGE_TYPES.INCIDENT_LOG_ENTRY,
        severity: 'INFO',
        message: `Retried ${retriedCommandCount} queued command${retriedCommandCount === 1 ? '' : 's'} after reconnect.`,
        timestamp: now,
      });
    } else if (flushedCount > 0) {
      appendEvent({
        type: SERVER_MESSAGE_TYPES.INCIDENT_LOG_ENTRY,
        severity: 'INFO',
        message: `Recovered link and flushed ${flushedCount} queued outbound message${flushedCount === 1 ? '' : 's'}.`,
        timestamp: now,
      });
    }

    if (droppedStaleAudio > 0) {
      appendEvent({
        type: SERVER_MESSAGE_TYPES.INCIDENT_LOG_ENTRY,
        severity: 'WARNING',
        message: `Dropped ${droppedStaleAudio} stale audio chunk${droppedStaleAudio === 1 ? '' : 's'} during reconnect recovery.`,
        timestamp: now,
      });
    }
  }, [appendEvent, refreshQueueHealth]);

  const markHeartbeatAckSupported = useCallback(() => {
    if (heartbeatAckSupportedRef.current) {
      return;
    }
    heartbeatAckSupportedRef.current = true;
    setConnectionHealth((prev) => (
      prev.heartbeatAckSupported
        ? prev
        : { ...prev, heartbeatAckSupported: true }
    ));
  }, []);

  const resetHeartbeatAckSupport = useCallback(() => {
    heartbeatAckSupportedRef.current = false;
    setConnectionHealth((prev) => (
      prev.heartbeatAckSupported
        ? { ...prev, heartbeatAckSupported: false }
        : prev
    ));
  }, []);

  const sendHeartbeatPong = useCallback((ws, sourceTimestamp) => {
    if (ws.readyState !== WebSocket.OPEN) return;
    try {
      ws.send(JSON.stringify({
        type: 'pong',
        timestamp: typeof sourceTimestamp === 'number' ? sourceTimestamp : Date.now(),
      }));
    } catch {
      // No-op: stale sockets will close via normal lifecycle paths.
    }
  }, []);

  const startHeartbeat = useCallback((ws) => {
    clearHeartbeatTimers();
    resetHeartbeatAckSupport();
    markSocketActivity();
    lastHeartbeatSentAtRef.current = 0;
    heartbeatMissesRef.current = 0;

    heartbeatIntervalRef.current = setInterval(() => {
      if (wsRef.current !== ws || ws.readyState !== WebSocket.OPEN) {
        return;
      }

      const now = Date.now();
      const idleMs = now - (lastSocketActivityAtRef.current || now);
      const enoughIdle = idleMs >= HEARTBEAT_IDLE_BEFORE_PING_MS;
      const pingIntervalElapsed = (now - lastHeartbeatSentAtRef.current) >= HEARTBEAT_PING_INTERVAL_MS;

      if (!enoughIdle || !pingIntervalElapsed || heartbeatProbeInFlightRef.current) {
        return;
      }

      heartbeatProbeInFlightRef.current = true;
      lastHeartbeatSentAtRef.current = now;

      try {
        ws.send(JSON.stringify({
          type: CLIENT_MESSAGE_TYPES.PING,
          timestamp: now,
        }));
      } catch {
        heartbeatProbeInFlightRef.current = false;
        try {
          ws.close(4001, 'Heartbeat send failure');
        } catch {
          // Ignore close failures on already-closed sockets.
        }
        return;
      }

      if (heartbeatProbeTimeoutRef.current) {
        clearTimeout(heartbeatProbeTimeoutRef.current);
      }

      heartbeatProbeTimeoutRef.current = setTimeout(() => {
        if (wsRef.current !== ws || ws.readyState !== WebSocket.OPEN) {
          return;
        }
        if (!heartbeatProbeInFlightRef.current) {
          return;
        }

        heartbeatProbeInFlightRef.current = false;

        const heartbeatAckSupported = heartbeatAckSupportedRef.current;
        const bufferedAmount = Number(ws.bufferedAmount) || 0;
        const missesBeforeReconnect = heartbeatAckSupported
          ? HEARTBEAT_MISSES_BEFORE_FORCE_RECONNECT_ACK
          : HEARTBEAT_MISSES_BEFORE_FORCE_RECONNECT_PASSIVE;

        if (!heartbeatAckSupported && bufferedAmount <= 0) {
          heartbeatMissesRef.current = 0;
          return;
        }

        heartbeatMissesRef.current += 1;
        if (heartbeatMissesRef.current < missesBeforeReconnect) {
          return;
        }

        staleReconnectCountRef.current += 1;
        setConnectionHealth((prev) => ({
          ...prev,
          staleReconnects: staleReconnectCountRef.current,
        }));
        console.warn('[HawkEye] Heartbeat detected stale connection, reconnecting');
        try {
          ws.close(4001, 'Heartbeat timeout');
        } catch {
          // Ignore close failures on already-closed sockets.
        }
      }, HEARTBEAT_ACK_TIMEOUT_MS);
    }, HEARTBEAT_CHECK_INTERVAL_MS);
  }, [clearHeartbeatTimers, markSocketActivity, resetHeartbeatAckSupport]);

  // Close and discard any existing socket — used before opening a new one.
  const teardownSocket = useCallback(() => {
    clearHeartbeatTimers();
    const ws = wsRef.current;
    wsRef.current = null;
    if (ws) {
      // Prevent this socket's onclose from triggering reconnect logic.
      ws.onopen = null;
      ws.onmessage = null;
      ws.onerror = null;
      ws.onclose = null;
      try { ws.close(); } catch { /* ignore */ }
    }
  }, [clearHeartbeatTimers]);

  // Internal connect — creates the WebSocket immediately, no debounce.
  const connectImmediate = useCallback(() => {
    // Bail if component is unmounted (React StrictMode teardown).
    if (!mountedRef.current) return;

    // If there's already an OPEN socket, don't duplicate.
    if (wsRef.current?.readyState === WebSocket.OPEN) return;

    // Tear down any previous socket (including CONNECTING ones) to avoid
    // orphan sockets that race with the new one.
    teardownSocket();

    isIntentionalCloseRef.current = false;

    setConnectionStatusTracked(CONNECTION_STATUS.CONNECTING);
    setConnectionHealth((prev) => ({
      ...prev,
      nextRetryDelayMs: null,
    }));
    
    const wsUrl = `${WS_URL}/${userId}/${sessionId}`;
    const ws = new WebSocket(wsUrl);
    wsRef.current = ws;

    ws.onopen = () => {
      if (wsRef.current !== ws) {
        try { ws.close(); } catch { /* ignore */ }
        return;
      }

      console.log('[HawkEye] WebSocket connected');
      setConnectionStatusTracked(CONNECTION_STATUS.CONNECTED);
      reconnectAttemptsRef.current = 0;
      setConnectionHealth((prev) => ({
        ...prev,
        reconnectAttempt: 0,
        nextRetryDelayMs: null,
      }));
      startHeartbeat(ws);
      flushOutboundQueue(ws);
      
      // Emit connected event
      appendEvent({
        type: SERVER_MESSAGE_TYPES.CONNECTED,
        session_id: sessionId,
      });
    };

    ws.onmessage = (event) => {
      if (wsRef.current !== ws) {
        return;
      }

      markSocketActivity();

      if (event.data instanceof Blob) {
        // Binary audio data - forward to audio pipeline for playback
        event.data.arrayBuffer()
          .then((buffer) => {
            console.log('[WS] Received audio frame from server, bytes:', buffer.byteLength);
            if (audioCallbackRef?.current) {
              audioCallbackRef.current(buffer);
            } else {
              console.warn('[WS] audioCallbackRef.current is null — received audio but no playback callback');
            }
          })
          .catch((err) => {
            console.error('[WS] Failed to decode audio blob:', err);
          });
        return;
      }

      // Text JSON message
      try {
        const data = JSON.parse(event.data);
        const messageType = typeof data?.type === 'string' ? data.type.toLowerCase() : '';

        if (messageType === 'pong') {
          markHeartbeatAckSupported();
          return;
        }
        if (messageType === 'ping') {
          markHeartbeatAckSupported();
          sendHeartbeatPong(ws, data?.timestamp);
          return;
        }

        console.log('[HawkEye] Received:', data.type || 'audio');
        
        appendEvent(data);
      } catch (err) {
        console.error('[HawkEye] Failed to parse message:', err);
      }
    };

    ws.onerror = (error) => {
      if (wsRef.current !== ws) {
        return;
      }

      console.error('[HawkEye] WebSocket error:', error);
      setConnectionStatusTracked(CONNECTION_STATUS.ERROR);
    };

    ws.onclose = (event) => {
      if (wsRef.current !== ws) {
        return;
      }

      clearHeartbeatTimers();
      console.log('[HawkEye] WebSocket closed:', event.code, event.reason);
      wsRef.current = null;
      
      if (isIntentionalCloseRef.current || !mountedRef.current) {
        setConnectionStatusTracked(CONNECTION_STATUS.DISCONNECTED);
        setConnectionHealth((prev) => ({
          ...prev,
          reconnectAttempt: 0,
          nextRetryDelayMs: null,
          heartbeatAckSupported: false,
        }));
        if (isIntentionalCloseRef.current) {
          appendEvent({
            type: SERVER_MESSAGE_TYPES.DISCONNECTED,
            reason: 'Intentional close',
          });
        }
        return;
      }

      // Attempt reconnection with exponential backoff
      if (reconnectAttemptsRef.current < MAX_RECONNECT_ATTEMPTS) {
        setConnectionStatusTracked(CONNECTION_STATUS.RECONNECTING);
        const nextAttempt = reconnectAttemptsRef.current + 1;
        reconnectAttemptsRef.current = nextAttempt;
        const reconnectDelayMs = computeReconnectDelayMs(nextAttempt);
        setConnectionHealth((prev) => ({
          ...prev,
          reconnectAttempt: nextAttempt,
          nextRetryDelayMs: reconnectDelayMs,
        }));
        
        console.log(
          `[HawkEye] Reconnecting in ${Math.round(reconnectDelayMs / 1000)}s ` +
          `(attempt ${nextAttempt}/${MAX_RECONNECT_ATTEMPTS})`,
        );
        
        clearReconnectTimer();
        reconnectTimeoutRef.current = setTimeout(() => {
          reconnectTimeoutRef.current = null;
          connectImmediate();
        }, reconnectDelayMs);
      } else {
        setConnectionStatusTracked(CONNECTION_STATUS.ERROR);
        setConnectionHealth((prev) => ({
          ...prev,
          reconnectAttempt: reconnectAttemptsRef.current,
          nextRetryDelayMs: null,
        }));
        appendEvent({
          type: SERVER_MESSAGE_TYPES.ERROR,
          message: 'Max reconnection attempts reached',
        });
      }
    };
  }, [
    appendEvent,
    clearHeartbeatTimers,
    clearReconnectTimer,
    flushOutboundQueue,
    markHeartbeatAckSupported,
    markSocketActivity,
    sendHeartbeatPong,
    sessionId,
    setConnectionStatusTracked,
    startHeartbeat,
    teardownSocket,
    userId,
  ]);

  // Public connect — debounced to coalesce rapid mount/unmount cycles
  // (e.g. React StrictMode double-mount, HMR, fast server restarts).
  const connect = useCallback(() => {
    clearReconnectTimer();
    if (connectDebounceRef.current) {
      clearTimeout(connectDebounceRef.current);
    }
    connectDebounceRef.current = setTimeout(() => {
      connectDebounceRef.current = null;
      connectImmediate();
    }, CONNECT_DEBOUNCE_MS);
  }, [clearReconnectTimer, connectImmediate]);

  // Disconnect from WebSocket
  const disconnect = useCallback(() => {
    isIntentionalCloseRef.current = true;
    clearReconnectTimer();
    if (connectDebounceRef.current) {
      clearTimeout(connectDebounceRef.current);
      connectDebounceRef.current = null;
    }
    outboundQueueRef.current = [];
    refreshQueueHealth();
    teardownSocket();
    setConnectionStatusTracked(CONNECTION_STATUS.DISCONNECTED);
    setConnectionHealth((prev) => ({
      ...prev,
      reconnectAttempt: 0,
      nextRetryDelayMs: null,
      heartbeatAckSupported: false,
    }));
  }, [clearReconnectTimer, refreshQueueHealth, setConnectionStatusTracked, teardownSocket]);

  const sendAudioWithStatus = useCallback((audioBuffer) => {
    let payload = null;
    if (audioBuffer instanceof ArrayBuffer) {
      payload = audioBuffer;
    } else if (ArrayBuffer.isView(audioBuffer)) {
      payload = audioBuffer.buffer.slice(
        audioBuffer.byteOffset,
        audioBuffer.byteOffset + audioBuffer.byteLength,
      );
    }

    if (!(payload instanceof ArrayBuffer) || payload.byteLength === 0) {
      console.warn('[WS] sendAudio called with invalid payload');
      return buildSendOutcome('dropped', 'invalid_audio_payload');
    }

    const ws = wsRef.current;
    if (ws?.readyState === WebSocket.OPEN) {
      try {
        ws.send(payload);
        return buildSendOutcome('sent', 'open_socket');
      } catch (error) {
        console.warn('[WS] sendAudio failed on open socket, attempting queue fallback');
      }
    }

    if (!isRetryWindowOpen()) {
      return buildSendOutcome('dropped', 'socket_not_open');
    }

    const droppedStaleAudio = dropStaleQueuedAudio(Date.now());
    const enqueueResult = enqueueOutboundPayload({
      kind: 'audio',
      payload,
      bytes: payload.byteLength,
      maxAgeMs: MAX_BUFFERED_AUDIO_AGE_MS,
      messageType: 'audio',
    });

    if (!enqueueResult.queued) {
      return buildSendOutcome('dropped', 'audio_queue_overflow', {
        queueDepth: enqueueResult.queueDepth,
      });
    }

    return buildSendOutcome('queued', 'retry_window_buffered', {
      queueDepth: enqueueResult.queueDepth,
      droppedStaleAudio,
    });
  }, [dropStaleQueuedAudio, enqueueOutboundPayload, isRetryWindowOpen]);

  const sendAudio = useCallback(
    (audioBuffer) => sendAudioWithStatus(audioBuffer).ok,
    [sendAudioWithStatus],
  );

  const sendJsonMessageWithStatus = useCallback((payload, options = {}) => {
    if (!payload || typeof payload !== 'object') {
      return buildSendOutcome('dropped', 'invalid_payload');
    }

    let serializedPayload;
    try {
      serializedPayload = JSON.stringify(payload);
    } catch {
      return buildSendOutcome('dropped', 'serialization_failed');
    }

    const ws = wsRef.current;
    if (ws?.readyState === WebSocket.OPEN) {
      try {
        ws.send(serializedPayload);
        return buildSendOutcome('sent', 'open_socket');
      } catch {
        // Fall through to queue policy checks below.
      }
    }

    const queuePolicy = options.queuePolicy || 'none';
    const shouldQueue = queuePolicy === 'always'
      ? canQueueReliableMessage()
      : queuePolicy === 'retry_window'
        ? isRetryWindowOpen()
        : false;

    if (!shouldQueue) {
      return buildSendOutcome('dropped', 'socket_not_open');
    }

    const enqueueResult = enqueueOutboundPayload({
      kind: 'json',
      payload: serializedPayload,
      bytes: serializedPayload.length,
      messageType: typeof payload.type === 'string' ? payload.type : (options.messageType || 'json'),
    });

    if (!enqueueResult.queued) {
      return buildSendOutcome('dropped', 'queue_overflow', {
        queueDepth: enqueueResult.queueDepth,
      });
    }

    return buildSendOutcome('queued', 'buffered_for_retry', {
      queueDepth: enqueueResult.queueDepth,
    });
  }, [canQueueReliableMessage, enqueueOutboundPayload, isRetryWindowOpen]);

  const sendJsonMessage = useCallback(
    (payload, options = {}) => sendJsonMessageWithStatus(payload, options).ok,
    [sendJsonMessageWithStatus],
  );

  const normalizeControlMetadata = useCallback((metadata) => (
    metadata && typeof metadata === 'object' ? metadata : {}
  ), []);

  const normalizeVideoMetadata = useCallback((metadata) => {
    if (!metadata || typeof metadata !== 'object') {
      return {};
    }

    const normalized = {};
    const allowedKeys = [
      'frame_id',
      'captured_at_ms',
      'cadence_fps',
      'source',
      'stream_id',
      'mime_type',
      'active_layers',
      'camera_mode',
    ];
    allowedKeys.forEach((key) => {
      const value = metadata[key];
      if (typeof value === 'string' && value.trim().length > 0) {
        normalized[key] = value.trim();
      } else if (typeof value === 'number' && Number.isFinite(value)) {
        normalized[key] = value;
      }
    });
    return normalized;
  }, []);

  const sendTextWithStatus = useCallback((text) => {
    if (typeof text !== 'string' || text.trim().length === 0) {
      return buildSendOutcome('dropped', 'empty_text');
    }

    return sendJsonMessageWithStatus({
      type: CLIENT_MESSAGE_TYPES.TEXT,
      content: text,
    }, {
      queuePolicy: 'always',
      messageType: CLIENT_MESSAGE_TYPES.TEXT,
    });
  }, [sendJsonMessageWithStatus]);

  // Send text command
  const sendText = useCallback(
    (text) => sendTextWithStatus(text).ok,
    [sendTextWithStatus],
  );

  // Send video frame
  const sendVideoFrame = useCallback((base64Image, caption = '', metadata = {}) => {
    if (typeof base64Image !== 'string' || base64Image.trim().length === 0) {
      return false;
    }

    const normalizedVideoMetadata = normalizeVideoMetadata(metadata);
    return sendJsonMessage({
      ...normalizedVideoMetadata,
      type: CLIENT_MESSAGE_TYPES.VIDEO,
      data: base64Image,
      caption,
    }, {
      queuePolicy: 'none',
      messageType: CLIENT_MESSAGE_TYPES.VIDEO,
    });
  }, [normalizeVideoMetadata, sendJsonMessage]);

  // Send mode change
  const sendModeChange = useCallback((mode) => {
    return sendJsonMessage({
      type: CLIENT_MESSAGE_TYPES.MODE_CHANGE,
      mode,
    }, {
      queuePolicy: 'always',
      messageType: CLIENT_MESSAGE_TYPES.MODE_CHANGE,
    });
  }, [sendJsonMessage]);

  const sendContextUpdate = useCallback((context = {}) => {
    if (!context || typeof context !== 'object') return false;
    const lat = Number(context.lat);
    const lng = Number(context.lng);
    if (!Number.isFinite(lat) || !Number.isFinite(lng)) {
      return false;
    }

    const payload = {
      type: CLIENT_MESSAGE_TYPES.CONTEXT_UPDATE,
      lat,
      lng,
      source: typeof context.source === 'string' ? context.source : 'unknown',
      timestamp: Number.isFinite(context.timestamp) ? context.timestamp : Date.now(),
    };

    if (typeof context.label === 'string' && context.label.trim()) {
      payload.label = context.label.trim();
    }
    if (Number.isFinite(context.radius_km)) {
      payload.radius_km = Number(context.radius_km);
    }

    return sendJsonMessage(payload, {
      queuePolicy: 'none',
      messageType: CLIENT_MESSAGE_TYPES.CONTEXT_UPDATE,
    });
  }, [sendJsonMessage]);

  const sendActivityStart = useCallback((metadata = {}) => {
    const controlMetadata = normalizeControlMetadata(metadata);
    return sendJsonMessage({
      ...controlMetadata,
      type: CLIENT_MESSAGE_TYPES.ACTIVITY_START,
    }, {
      queuePolicy: 'always',
      messageType: CLIENT_MESSAGE_TYPES.ACTIVITY_START,
    });
  }, [normalizeControlMetadata, sendJsonMessage]);

  const sendActivityEnd = useCallback((metadata = {}) => {
    const controlMetadata = normalizeControlMetadata(metadata);
    return sendJsonMessage({
      ...controlMetadata,
      type: CLIENT_MESSAGE_TYPES.ACTIVITY_END,
    }, {
      queuePolicy: 'always',
      messageType: CLIENT_MESSAGE_TYPES.ACTIVITY_END,
    });
  }, [normalizeControlMetadata, sendJsonMessage]);

  const sendAudioStreamEnd = useCallback((metadata = {}) => {
    const controlMetadata = normalizeControlMetadata(metadata);
    return sendJsonMessage({
      ...controlMetadata,
      type: CLIENT_MESSAGE_TYPES.AUDIO_STREAM_END,
    }, {
      queuePolicy: 'always',
      messageType: CLIENT_MESSAGE_TYPES.AUDIO_STREAM_END,
    });
  }, [normalizeControlMetadata, sendJsonMessage]);

  // Send screenshot response payload
  const sendScreenshotResponse = useCallback((requestId, imageBase64) => {
    return sendJsonMessage({
      type: CLIENT_MESSAGE_TYPES.SCREENSHOT_RESPONSE,
      request_id: requestId,
      image_base64: imageBase64,
    }, {
      queuePolicy: 'always',
      messageType: CLIENT_MESSAGE_TYPES.SCREENSHOT_RESPONSE,
    });
  }, [sendJsonMessage]);

  // Clear events
  const clearEvents = useCallback(() => {
    setEvents([]);
  }, []);

  // Connect on mount, disconnect on unmount.
  // The mountedRef gate prevents orphan sockets from StrictMode double-mount.
  useEffect(() => {
    mountedRef.current = true;
    connect();
    
    return () => {
      mountedRef.current = false;
      disconnect();
    };
  }, [connect, disconnect]);

  return {
    connectionStatus,
    connectionHealth,
    events,
    sendAudio,
    sendAudioWithStatus,
    sendText,
    sendTextWithStatus,
    sendVideoFrame,
    sendModeChange,
    sendContextUpdate,
    sendActivityStart,
    sendActivityEnd,
    sendAudioStreamEnd,
    sendScreenshotResponse,
    clearEvents,
    connect,
    disconnect,
  };
}
