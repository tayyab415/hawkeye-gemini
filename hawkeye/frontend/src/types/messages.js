/**
 * WebSocket Message Types for HawkEye
 * Defines all message types passed between frontend and backend
 */

// Message types from backend to frontend
export const SERVER_MESSAGE_TYPES = {
  // Transcription events
  TRANSCRIPT: 'transcript',           // { type: 'transcript', speaker: 'agent'|'user', text: string, timestamp: number }
  
  // UI update events
  MAP_UPDATE: 'map_update',           // { type: 'map_update', layer_type: 'flood'|'route'|'marker'|'zone', geojson: object, label: string, style: object }
                                      // Extended action-based dispatch (new):
                                      //   action: 'toggle_layer'       → { layer_id: string, enabled?: bool }
                                      //   action: 'camera_mode'        → { mode: 'ORBIT'|'BIRD_EYE'|'STREET_LEVEL'|'OVERVIEW'|null }
                                      //   action: 'deploy_entity'      → { entity_id, entity_type: 'helicopter'|'boat'|'command_post', lat, lng, altitude?, label? }
                                      //   action: 'move_entity'        → { entity_id, lat, lng, altitude?, duration_ms? }
                                      //   action: 'add_flood_overlay'  → { overlay_id, geojson, level_m? }
                                      //   action: 'update_flood_level' → { overlay_id, level_m, duration_ms? }
                                      //   action: 'add_measurement'    → { line_id, lat1, lng1, lat2, lng2 }
                                      //   action: 'remove_measurement' → { line_id }
                                      //   action: 'set_atmosphere'     → { mode: 'clear'|'haze'|'night'|'thermal'|'storm' }
                                      //   action: 'toggle_scanlines'   → { enabled?: bool }
                                      //   action: 'toggle_pip'         → { enabled?: bool }
  INCIDENT_LOG_ENTRY: 'incident_log_entry', // { type: 'incident_log_entry', severity: 'low'|'medium'|'high'|'critical', message: string, timestamp: number }
  STATUS_UPDATE: 'status_update',     // { type: 'status_update', water_level_m: number, population_at_risk: number, mode: string }
  FEED_UPDATE: 'feed_update',         // { type: 'feed_update', mode: 'DRONE'|'SAR'|'PREDICTION', data: object }
  EE_UPDATE: 'ee_update',             // { type: 'ee_update', area_sqkm, growth_rate_pct, metadata, ee_runtime, runtime_layers, temporal_frames, temporal_playback, temporal_summary, multisensor_fusion, runtime_status, runtime_mode, runtime_state, live_analysis_task }
  CHART_UPDATE: 'chart_update',       // { type: 'chart_update', chart: 'flood_analytics'|'cascade_analysis'|'vulnerability_ranking'|'hotspot_density', ...data }
  USAGE_UPDATE: 'usage_update',       // { type: 'usage_update', usage: { input_tokens, output_tokens, total_tokens, cached_tokens }, context: { context_tokens, trigger_tokens, target_tokens, utilization_ratio }, session_health: { pressure, pressure_score }, timestamp }
  GROUNDING_UPDATE: 'grounding_update', // { type: 'grounding_update', tool, label, grounded, source_count, citations: [{ id, title, url?, source?, snippet? }], query?, search_queries?, summary?, timestamp }
  
  // Tool events
  TOOL_CALL: 'tool_call',             // { type: 'tool_call', tool: string, args: object, timestamp: number }
  TOOL_STATUS: 'tool_status',         // { type: 'tool_status', tool: string, state: 'pending'|'running'|'complete'|'error', call_id: string, duration_ms?: number, error?: string }
  
  // Session events
  TURN_COMPLETE: 'turn_complete',     // { type: 'turn_complete', timestamp: number } // End-of-turn marker; frontend may finalize turn-local UI/audio state.
  INTERRUPTED: 'interrupted',         // { type: 'interrupted', timestamp: number } // Barge-in marker; frontend should clear stale playback state safely.
  
  // Connection events
  CONNECTED: 'connected',             // { type: 'connected', session_id: string }
  DISCONNECTED: 'disconnected',       // { type: 'disconnected', reason: string }
  ERROR: 'error',                     // { type: 'error', message: string }
};

// Message types from frontend to backend
export const CLIENT_MESSAGE_TYPES = {
  // Input types
  TEXT: 'text',                       // { type: 'text', content: string }
  VIDEO: 'video',                     // { type: 'video', data: base64_string, caption: string, frame_id?: string, captured_at_ms?: number, cadence_fps?: number, source?: string, mime_type?: string }
  SCREENSHOT_RESPONSE: 'screenshot_response', // { type: 'screenshot_response', request_id: string, image_base64: string }
  CONTEXT_UPDATE: 'context_update',   // { type: 'context_update', lat: number, lng: number, source?: 'click'|'viewport'|'camera', label?: string, radius_km?: number, timestamp?: number }
  
  // Control types
  MODE_CHANGE: 'mode_change',         // { type: 'mode_change', mode: 'SILENT'|'ALERT'|'BRIEF'|'ACTION' }
  ACTIVITY_START: 'activity_start',   // { type: 'activity_start' } // Optional explicit VAD speech-start marker
  ACTIVITY_END: 'activity_end',       // { type: 'activity_end' } // Optional explicit VAD speech-end marker
  AUDIO_STREAM_END: 'audio_stream_end', // { type: 'audio_stream_end' } // Explicit end-of-audio-stream marker
  PING: 'ping',                       // { type: 'ping', timestamp: number }
};

// Operational modes
export const OPERATIONAL_MODES = {
  SILENT: 'SILENT',
  ALERT: 'ALERT',
  BRIEF: 'BRIEF',
  ACTION: 'ACTION',
};

// Recon feed modes
export const RECON_FEED_MODES = {
  DRONE: 'DRONE',
  SAR: 'SAR',
  PREDICTION: 'PREDICTION',
};

// Connection status
export const CONNECTION_STATUS = {
  CONNECTING: 'connecting',
  CONNECTED: 'connected',
  DISCONNECTED: 'disconnected',
  RECONNECTING: 'reconnecting',
  ERROR: 'error',
};

// Speaker types for transcripts
export const SPEAKER_TYPES = {
  AGENT: 'agent',
  USER: 'user',
  SYSTEM: 'system',
};

// Severity levels
export const SEVERITY_LEVELS = {
  LOW: 'low',
  MEDIUM: 'medium',
  HIGH: 'high',
  CRITICAL: 'critical',
};

// MAP_UPDATE action types (used with action field)
export const MAP_ACTIONS = {
  TOGGLE_LAYER: 'toggle_layer',
  CAMERA_MODE: 'camera_mode',
  DEPLOY_ENTITY: 'deploy_entity',
  MOVE_ENTITY: 'move_entity',
  ADD_FLOOD_OVERLAY: 'add_flood_overlay',
  UPDATE_FLOOD_LEVEL: 'update_flood_level',
  ADD_MEASUREMENT: 'add_measurement',
  REMOVE_MEASUREMENT: 'remove_measurement',
  SET_ATMOSPHERE: 'set_atmosphere',
  CAPTURE_SCREENSHOT: 'capture_screenshot',
  TOGGLE_SCANLINES: 'toggle_scanlines',
  TOGGLE_PIP: 'toggle_pip',
};

// Camera modes
export const CAMERA_MODES = {
  ORBIT: 'ORBIT',
  BIRD_EYE: 'BIRD_EYE',
  STREET_LEVEL: 'STREET_LEVEL',
  OVERVIEW: 'OVERVIEW',
};

// Data layer identifiers
export const DATA_LAYERS = {
  FLOOD_EXTENT: 'FLOOD_EXTENT',
  TRIAGE_ZONES: 'TRIAGE_ZONES',
  INFRASTRUCTURE: 'INFRASTRUCTURE',
  EVACUATION_ROUTES: 'EVACUATION_ROUTES',
  POPULATION_DENSITY: 'POPULATION_DENSITY',
  HISTORICAL_FLOODS: 'HISTORICAL_FLOODS',
  THREAT_RADIUS: 'THREAT_RADIUS',
  // Effects (separate group — do not mix with data layer toggles)
  STORM_EFFECT: 'STORM_EFFECT',
  FLOOD_REPLAY: 'FLOOD_REPLAY',
};

// Atmosphere modes
export const ATMOSPHERE_MODES = {
  CLEAR: 'clear',
  HAZE: 'haze',
  NIGHT: 'night',
  THERMAL: 'thermal',
  STORM: 'storm',
};
