# Gemini Live API - Comprehensive Research Report for AEGIS Project

## Executive Summary

This report provides comprehensive technical research on Gemini Live and Gemini Live API for the AEGIS predictive digital risk twin project. AEGIS requires real-time voice and video streaming for anomaly detection in disaster response and infrastructure surveillance scenarios.

**Key Findings:**
- Gemini Live API supports real-time multimodal streaming (audio, video, text)
- Native audio models provide barge-in, proactive audio, and affective dialogue
- Video processing at 1 FPS for analysis (not suitable for high-speed detection)
- Session limits: 10 minutes connection, 15 minutes audio-only, 2 minutes video+audio
- Pricing: $3 per 1M input tokens (audio/video), $12 per 1M output tokens (audio)
- Rate limits: 3 concurrent sessions (free tier), up to 1,000 (Vertex AI paid)

---

## 1. Gemini Live API Complete Capabilities

### 1.1 Core Features

| Feature | Description | AEGIS Relevance |
|---------|-------------|-----------------|
| **Multimodal Streaming** | Simultaneous audio, video, and text input/output | Critical for drone footage + voice commands |
| **Native Audio** | Direct audio processing (no STT/TTS pipeline) | Low-latency voice interaction |
| **Barge-in** | Users can interrupt model at any time | Essential for emergency override |
| **Proactive Audio** | Model decides when to respond | Prevents unnecessary interruptions |
| **Affective Dialogue** | Detects and responds to emotion | Useful for stress detection in emergencies |
| **Function Calling** | Real-time tool execution | Trigger alerts, log data, dispatch |
| **Transcriptions** | Real-time text transcripts | Audit logs, documentation |

### 1.2 Technical Specifications

```
Input Modalities:
  - Audio: Raw 16-bit PCM, 16kHz, little-endian
  - Video: JPEG frames, ≤1 FPS, max 7MB per frame
  - Text: UTF-8 strings
  - Supported video formats: MP4, WebM, AVI, MOV, FLV, MPEG, WMV, 3GP

Output Modalities:
  - Audio: Raw 16-bit PCM, 24kHz, little-endian
  - Text: Transcriptions and structured output

Protocol: Stateful WebSocket (WSS)
Video Resolution: Standard 768x768 (configurable)
```

### 1.3 How It Works

```
Traditional Pipeline (High Latency):
User Audio → STT → Text → LLM → Text → TTS → Output Audio
     (4 conversion steps, ~2-3s latency)

Gemini Native Audio (Low Latency):
User Audio → Gemini Native Audio Model → Output Audio
     (Direct processing, ~200-500ms latency)
```

---

## 2. Voice Input/Output with Barge-in Support

### 2.1 Barge-in Configuration

Barge-in allows users to interrupt the model mid-response. This is critical for AEGIS emergency scenarios.

**Configuration Options:**

```python
from google.genai import types

# Configure realtime input with activity detection
realtime_config = types.RealtimeInputConfig(
    automatic_activity_detection=types.AutomaticActivityDetection(
        disabled=False,  # Enable automatic VAD
        start_of_speech_sensitivity='LOW',  # Sensitivity levels: LOW, MEDIUM, HIGH
        end_of_speech_sensitivity='HIGH',
        prefix_padding_ms=0,  # Padding before speech detection
    ),
    activity_handling='START_OF_ACTIVITY_INTERRUPTS',  # Barge-in behavior
    turn_coverage='TURN_INCLUDES_ONLY_ACTIVITY'
)
```

**Activity Handling Options:**
- `START_OF_ACTIVITY_INTERRUPTS`: New speech interrupts model output
- `START_OF_ACTIVITY_DOES_NOT_INTERRUPT`: Model continues speaking

### 2.2 Voice Activity Detection (VAD)

```python
# Disable automatic VAD for manual control
realtime_config = types.RealtimeInputConfig(
    automatic_activity_detection=types.AutomaticActivityDetection(
        disabled=True  # Manual control
    )
)

# Manual activity control
# Send ActivityStart when user begins speaking
# Send ActivityEnd when user stops speaking
```

### 2.3 Audio Format Requirements

```python
# Input audio format
INPUT_AUDIO_FORMAT = {
    'encoding': 'PCM',
    'sample_rate': 16000,  # 16kHz
    'bits_per_sample': 16,
    'endianness': 'little',
    'channels': 1  # Mono
}

# Output audio format
OUTPUT_AUDIO_FORMAT = {
    'encoding': 'PCM',
    'sample_rate': 24000,  # 24kHz
    'bits_per_sample': 16,
    'endianness': 'little',
    'channels': 1  # Mono
}
```

### 2.4 Available Voices

Gemini Live API supports 30 HD voices:
- `Puck` (default)
- `Charon`
- `Kore`
- `Fenrir`
- `Aoede`
- And 25+ more...

```python
speech_config = types.SpeechConfig(
    voice_config=types.VoiceConfig(
        prebuilt_voice_config=types.PrebuiltVoiceConfig(
            voice_name='Puck'  # Change as needed
        )
    )
)
```

---

## 3. Video Streaming and Analysis Capabilities

### 3.1 Video Processing Specifications

```
Frame Rate: 1 FPS maximum (model processes at 1 frame/second)
Resolution: 768x768 (standard), up to 1024x1024
Format: JPEG encoding
Max File Size: 7MB per frame
Max Frames: Unlimited (within session limits)
```

**⚠️ Critical Limitation for AEGIS:**
The 1 FPS processing rate means Gemini Live API is **NOT suitable** for:
- High-speed object tracking
- Rapid anomaly detection
- Fast-moving drone footage analysis

It IS suitable for:
- Scene understanding
- Static/d slow-changing infrastructure monitoring
- Object identification and classification
- Text/OCR from video frames

### 3.2 Video Streaming Code Example

```python
import asyncio
import base64
from google import genai
from google.genai import types

async def stream_video_frame(session, frame_bytes: bytes, frame_id: int):
    """Stream a video frame to Gemini Live API."""
    # Encode frame as base64
    encoded_frame = base64.b64encode(frame_bytes).decode('utf-8')
    
    # Send frame with correlation ID
    await session.send_realtime_input(
        types.LiveRealtimeInput(
            media=types.Blob(
                mime_type='image/jpeg',
                data=encoded_frame
            ),
            text=f'Analyze frame_id={frame_id} for anomalies'
        )
    )

# Continuous video streaming loop
async def video_stream_loop(session, video_source):
    frame_id = 0
    while True:
        # Capture frame (1 FPS)
        frame = await video_source.capture_frame()
        
        # Send to Gemini
        await stream_video_frame(session, frame, frame_id)
        
        frame_id += 1
        await asyncio.sleep(1.0)  # Maintain 1 FPS
```

### 3.3 Drone Footage Analysis Pattern

```python
# AEGIS-specific drone surveillance configuration
DRONE_SURVEILLANCE_CONFIG = {
    'system_instruction': '''
    You are AEGIS, an AI-powered disaster response and infrastructure 
    surveillance system. Analyze drone footage for:
    
    1. Structural damage (cracks, collapses, deformations)
    2. Environmental hazards (fires, floods, chemical spills)
    3. Human activity (crowds, distress signals, evacuation status)
    4. Infrastructure status (power lines, bridges, roads)
    5. Anomalous objects or behaviors
    
    Respond with structured JSON including:
    - frame_id: The frame being analyzed
    - threat_level: LOW, MEDIUM, HIGH, CRITICAL
    - detected_objects: List of objects with confidence scores
    - description: Detailed scene description
    - recommendations: Suggested actions
    ''',
    'response_modalities': ['TEXT', 'AUDIO'],  # Text for structured data, audio for alerts
    'enable_affective_dialog': False,  # Not needed for surveillance
}
```

---

## 4. Proactive Audio Configuration

### 4.1 What is Proactive Audio?

Proactive audio allows the model to intelligently decide when to respond, rather than responding to every input. This prevents:
- Background noise triggering responses
- Unnecessary interruptions during monitoring
- False alerts from ambient conversation

### 4.2 Configuration

```python
from google.genai import types

# Enable proactive audio
config = types.LiveConnectConfig(
    response_modalities=['AUDIO'],
    proactivity=types.ProactivityConfig(
        proactive_audio=True
    ),
    system_instruction='''
    You are a surveillance monitoring assistant. 
    ONLY respond when:
    1. A threat is detected (HIGH or CRITICAL level)
    2. The user explicitly asks a question
    3. An anomaly requires immediate attention
    
    Stay silent during normal operations and routine monitoring.
    '''
)
```

### 4.3 Proactive Audio Use Cases for AEGIS

```python
# Scenario 1: Silent monitoring mode
SILENT_MONITORING_INSTRUCTION = '''
Monitor the video feed silently. Only speak when:
- A person is detected in a restricted area
- Structural damage is visible
- Fire, smoke, or flooding is detected
- The user says "AEGIS, report" or asks a direct question
'''

# Scenario 2: Alert-only mode
ALERT_ONLY_INSTRUCTION = '''
You are in alert-only mode. Remain completely silent unless:
- Threat level is HIGH or CRITICAL
- Multiple anomalies detected simultaneously
- User explicitly requests a status report
'''
```

---

## 5. ADK (Agent Development Kit) Integration

### 5.1 ADK Overview

ADK provides a high-level abstraction over the Gemini Live API, handling:
- Connection lifecycle management
- Message routing
- Session state persistence
- Tool execution
- Automatic reconnection

### 5.2 ADK vs Raw WebSocket

| Capability | Raw Live API | ADK Streaming |
|------------|--------------|---------------|
| Agent Framework | Build from scratch | Single/multi-agent with tools |
| Tool Execution | Manual handling | Automatic parallel execution |
| Connection Management | Manual reconnection | Transparent session resumption |
| Event Model | Custom structures | Unified, typed Event objects |
| Async Framework | Manual coordination | LiveRequestQueue + run_live() |
| Session Persistence | Manual implementation | Built-in SQL, Vertex AI, in-memory |

### 5.3 ADK Installation and Setup

```bash
# Install ADK
pip install google-adk

# Set environment variables
export GOOGLE_API_KEY="your-api-key"
export GOOGLE_GENAI_USE_VERTEXAI=FALSE  # For AI Studio
```

### 5.4 ADK Streaming Implementation

```python
from google.adk.agents import LlmAgent
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.adk.artifacts import InMemoryArtifactService
from google.genai import types

# Create AEGIS surveillance agent
aegis_agent = LlmAgent(
    model='gemini-2.5-flash-native-audio-preview-12-2025',
    name='aegis_surveillance_agent',
    description='AI-powered disaster response and infrastructure surveillance',
    instruction='''
    You are AEGIS, a predictive digital risk twin for disaster response.
    Analyze incoming video streams and audio for:
    - Infrastructure anomalies
    - Environmental hazards  
    - Human safety concerns
    - Disaster indicators
    
    Provide structured analysis and verbal alerts for critical situations.
    ''',
    tools=[alert_dispatch_tool, log_anomaly_tool, generate_report_tool]
)

# Configure streaming
run_config = types.RunConfig(
    streaming_mode=types.StreamingMode.BIDI,
    response_modalities=['AUDIO', 'TEXT'],
    enable_affective_dialog=False,
    proactivity=types.ProactivityConfig(proactive_audio=True),
    session_resumption=types.SessionResumptionConfig(),  # Enable session resumption
    context_window_compression=types.ContextWindowCompressionConfig(
        trigger_tokens=50000,
        sliding_window=types.SlidingWindow(target_tokens=25000)
    ),
    realtime_input_config=types.RealtimeInputConfig(
        automatic_activity_detection=types.AutomaticActivityDetection(
            start_of_speech_sensitivity='LOW',
            end_of_speech_sensitivity='HIGH'
        )
    )
)

# Create runner
runner = Runner(
    agent=aegis_agent,
    session_service=InMemorySessionService(),
    artifact_service=InMemoryArtifactService()
)

# Start streaming session
async def start_surveillance_session():
    session = await runner.run_live(
        user_id='aegis_operator',
        session_id='surveillance_session_001',
        run_config=run_config
    )
    return session
```

### 5.5 ADK with Function Calling

```python
from google.adk.tools import tool

# Define surveillance tools
@tool
def dispatch_alert(threat_level: str, location: str, description: str) -> dict:
    """Dispatch emergency alert to response teams."""
    return {
        'status': 'dispatched',
        'alert_id': f'ALERT-{int(time.time())}',
        'threat_level': threat_level,
        'location': location
    }

@tool
def log_anomaly(frame_id: int, anomaly_type: str, confidence: float) -> dict:
    """Log detected anomaly to database."""
    return {
        'logged': True,
        'frame_id': frame_id,
        'timestamp': time.time()
    }

# Add tools to agent
aegis_agent = LlmAgent(
    model='gemini-2.5-flash-native-audio-preview-12-2025',
    tools=[dispatch_alert, log_anomaly]
)
```

---

## 6. Supported Models and Their Differences

### 6.1 Live API Compatible Models

| Model | API Provider | Stage | Context Window | Best For |
|-------|--------------|-------|----------------|----------|
| `gemini-2.5-flash-native-audio-preview-12-2025` | AI Studio | Preview | 128K | Latest features, best performance |
| `gemini-2.5-flash-native-audio-preview-09-2025` | AI Studio | Preview | 128K | Initial preview version |
| `gemini-live-2.5-flash-native-audio` | Vertex AI | Stable | 128K | Production workloads |
| `gemini-live-2.5-flash-preview-native-audio-09-2025` | Vertex AI | Preview | 128K | Testing on Vertex AI |

### 6.2 Model Capabilities Matrix

| Feature | 2.5 Flash Native Audio (Preview) | 2.5 Flash Native Audio (Stable) |
|---------|----------------------------------|--------------------------------|
| Native Audio I/O | ✅ | ✅ |
| Barge-in | ✅ | ✅ |
| Proactive Audio | ✅ | ✅ |
| Affective Dialogue | ✅ | ✅ |
| Function Calling | ✅ | ✅ |
| Google Search Grounding | ✅ | ✅ |
| Thinking Mode | ✅ | ❌ |
| Session Resumption | ✅ | ✅ |
| Context Window Compression | ✅ | ✅ |

### 6.3 Model Selection for AEGIS

**Recommended for AEGIS:**

```python
# For development/testing (AI Studio)
MODEL_DEV = 'gemini-2.5-flash-native-audio-preview-12-2025'

# For production (Vertex AI with GCP credits)
MODEL_PROD = 'gemini-live-2.5-flash-native-audio'
```

**Why 2.5 Flash Native Audio for AEGIS:**
- ✅ Native audio (low latency)
- ✅ Video analysis capability
- ✅ Function calling for alerts
- ✅ Proactive audio for silent monitoring
- ✅ Barge-in for emergency override
- ✅ 128K context window for long sessions

---

## 7. Pricing Details

### 7.1 Live API Pricing (Per 1 Million Tokens)

| Model | Input Type | Free Tier | Paid Tier |
|-------|------------|-----------|-----------|
| **Gemini 2.5 Flash Native Audio** | Text | Free | $0.50 |
| | Audio/Video | Free | $3.00 |
| | Output (Text) | Free | $2.00 |
| | Output (Audio) | Free | $12.00 |

### 7.2 Cost Estimation for AEGIS

**Scenario: 24/7 Surveillance with 4 Concurrent Streams**

```
Assumptions:
- 4 concurrent monitoring sessions
- Each session: video + audio input
- Average session duration: 10 minutes (with reconnection)
- Audio output only for alerts (10% of time)
- 1,440 minutes/day total streaming

Input Tokens (Video/Audio):
- Video: ~258 tokens/second
- Audio: ~25 tokens/second
- Total: ~283 tokens/second per session
- Daily: 283 × 1,440 × 4 sessions = 1,630,080 tokens
- Cost: 1.63 × $3.00 = $4.89/day

Output Tokens (Audio Alerts - 10%):
- Alert audio: ~50 tokens/second when active
- Daily: 50 × 144 × 4 = 28,800 tokens
- Cost: 0.029 × $12.00 = $0.35/day

Total Daily Cost: ~$5.24
Monthly Cost: ~$157
Annual Cost: ~$1,884
```

**With $4,000 GCP Credits:**
- Estimated runtime: ~2 years at full 4-stream capacity
- Or ~4 years at 2-stream capacity

### 7.3 Cost Optimization Strategies

```python
# 1. Use proactive audio to reduce output tokens
config = types.LiveConnectConfig(
    proactivity=types.ProactivityConfig(proactive_audio=True)
)

# 2. Enable context window compression for long sessions
config.context_window_compression = types.ContextWindowCompressionConfig(
    trigger_tokens=50000,
    sliding_window=types.SlidingWindow(target_tokens=25000)
)

# 3. Use text output for routine monitoring, audio only for alerts
config.response_modalities = ['TEXT']  # Switch to ['AUDIO'] only for alerts
```

---

## 8. Rate Limits and Quotas

### 8.1 Live API Rate Limits

| Tier | Concurrent Sessions | TPM | RPM |
|------|---------------------|-----|-----|
| **Free Tier** | 3 | 1,000,000 | Varies |
| **Vertex AI PayGo** | 1,000 | 4,000,000 | N/A |
| **Tier 1** | 1,000+ | 4,000,000 | N/A |

### 8.2 Session Limits

| Limit | Value | Notes |
|-------|-------|-------|
| Connection Duration | ~10 minutes | Hard limit |
| Audio-only Session | 15 minutes | Without compression |
| Video+Audio Session | 2 minutes | Without compression |
| Context Window | 128K tokens | For native audio models |
| Default Context | 32K tokens | Can be upgraded |

### 8.3 Handling Session Limits

```python
# Session resumption for extending beyond 10 minutes
config = types.LiveConnectConfig(
    session_resumption=types.SessionResumptionConfig()
)

# Listen for goAway notification
async for response in session.receive():
    if response.go_away is not None:
        # Connection will terminate soon
        time_left = response.go_away.time_left
        print(f'Session ending in {time_left}s, reconnecting...')
        # Reconnect with session resumption
```

### 8.4 Context Window Compression

```python
# Enable for theoretically infinite sessions
config = types.LiveConnectConfig(
    context_window_compression=types.ContextWindowCompressionConfig(
        sliding_window=types.SlidingWindow(),
        trigger_tokens=60000  # Compress when exceeding 60K tokens
    )
)
```

---

## 9. Python Code Examples

### 9.1 Basic WebSocket Connection

```python
import asyncio
import websockets
import json
import base64

API_KEY = 'YOUR_API_KEY'
MODEL_NAME = 'gemini-2.5-flash-native-audio-preview-12-2025'
WS_URL = f'wss://generativelanguage.googleapis.com/ws/google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContent?key={API_KEY}'

async def connect_gemini_live():
    async with websockets.connect(WS_URL) as websocket:
        # 1. Send configuration
        config_message = {
            'config': {
                'model': f'models/{MODEL_NAME}',
                'responseModalities': ['AUDIO', 'TEXT'],
                'systemInstruction': {
                    'parts': [{'text': 'You are AEGIS, a surveillance AI.'}]
                },
                'speechConfig': {
                    'voiceConfig': {
                        'prebuiltVoiceConfig': {'voiceName': 'Puck'}
                    }
                }
            }
        }
        await websocket.send(json.dumps(config_message))
        print('Configuration sent')
        
        # 2. Start receive loop
        await receive_loop(websocket)

async def receive_loop(websocket):
    async for message in websocket:
        response = json.loads(message)
        
        if 'serverContent' in response:
            content = response['serverContent']
            
            # Handle audio output
            if 'modelTurn' in content and 'parts' in content['modelTurn']:
                for part in content['modelTurn']['parts']:
                    if 'inlineData' in part:
                        audio_data = base64.b64decode(part['inlineData']['data'])
                        play_audio(audio_data)
            
            # Handle transcriptions
            if 'inputTranscription' in content:
                print(f'User: {content["inputTranscription"]["text"]}')
            if 'outputTranscription' in content:
                print(f'AEGIS: {content["outputTranscription"]["text"]}')
            
            # Handle turn complete
            if content.get('turnComplete'):
                print('Response complete')

if __name__ == '__main__':
    asyncio.run(connect_gemini_live())
```

### 9.2 GenAI SDK Implementation (Recommended)

```python
import asyncio
from google import genai
from google.genai import types

client = genai.Client(api_key='YOUR_API_KEY')

async def aegis_surveillance_session():
    model = 'gemini-2.5-flash-native-audio-preview-12-2025'
    
    config = types.LiveConnectConfig(
        response_modalities=['AUDIO', 'TEXT'],
        system_instruction=types.Content(
            parts=[types.Part(text='''
                You are AEGIS, an AI-powered disaster response surveillance system.
                Analyze video feeds for anomalies and provide structured alerts.
            ''')]
        ),
        speech_config=types.SpeechConfig(
            voice_config=types.VoiceConfig(
                prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name='Puck')
            )
        ),
        proactivity=types.ProactivityConfig(proactive_audio=True),
        realtime_input_config=types.RealtimeInputConfig(
            automatic_activity_detection=types.AutomaticActivityDetection(
                start_of_speech_sensitivity='LOW',
                end_of_speech_sensitivity='HIGH'
            )
        )
    )
    
    async with client.aio.live.connect(model=model, config=config) as session:
        # Send video frame
        with open('drone_frame.jpg', 'rb') as f:
            frame_data = f.read()
        
        await session.send_realtime_input(
            types.LiveRealtimeInput(
                media=types.Blob(mime_type='image/jpeg', data=frame_data),
                text='Analyze this frame for structural damage'
            )
        )
        
        # Receive response
        async for response in session.receive():
            if response.server_content:
                # Handle response
                pass
```

### 9.3 Complete AEGIS Surveillance Implementation

```python
"""
AEGIS - AI-Powered Disaster Response Surveillance System
Using Gemini Live API for real-time multimodal analysis
"""

import asyncio
import base64
import json
from typing import Optional, Callable
from dataclasses import dataclass
from google import genai
from google.genai import types

@dataclass
class AnomalyDetection:
    frame_id: int
    threat_level: str  # LOW, MEDIUM, HIGH, CRITICAL
    detected_objects: list
    description: str
    recommendations: list

class AEGISSurveillance:
    def __init__(self, api_key: str):
        self.client = genai.Client(api_key=api_key)
        self.session = None
        self.frame_counter = 0
        self.on_anomaly_detected: Optional[Callable] = None
        
    async def initialize(self):
        """Initialize surveillance session with Gemini Live API."""
        model = 'gemini-2.5-flash-native-audio-preview-12-2025'
        
        config = types.LiveConnectConfig(
            response_modalities=['TEXT'],  # Text for structured data
            system_instruction=types.Content(
                parts=[types.Part(text=self._get_system_prompt())]
            ),
            proactivity=types.ProactivityConfig(proactive_audio=True),
            context_window_compression=types.ContextWindowCompressionConfig(
                trigger_tokens=50000,
                sliding_window=types.SlidingWindow(target_tokens=25000)
            ),
            session_resumption=types.SessionResumptionConfig()
        )
        
        self.session = await self.client.aio.live.connect(
            model=model, 
            config=config
        ).__aenter__()
        
        # Start response handler
        asyncio.create_task(self._handle_responses())
    
    def _get_system_prompt(self) -> str:
        return '''
        You are AEGIS, an AI-powered disaster response and infrastructure 
        surveillance system. Analyze video frames and provide structured output.
        
        ALWAYS respond with valid JSON in this format:
        {
            "threat_level": "LOW|MEDIUM|HIGH|CRITICAL",
            "detected_objects": [{"type": "string", "confidence": 0.95}],
            "description": "Detailed scene description",
            "recommendations": ["action1", "action2"]
        }
        
        Threat Levels:
        - CRITICAL: Immediate danger (fire, structural collapse, injured people)
        - HIGH: Significant concern (smoke, large crowds, visible damage)
        - MEDIUM: Potential issue (minor damage, unusual activity)
        - LOW: Normal conditions
        
        Only include objects with confidence > 0.7.
        '''
    
    async def analyze_frame(self, frame_bytes: bytes) -> None:
        """Send video frame for analysis."""
        self.frame_counter += 1
        
        await self.session.send_realtime_input(
            types.LiveRealtimeInput(
                media=types.Blob(
                    mime_type='image/jpeg',
                    data=frame_bytes
                ),
                text=f'Analyze frame {self.frame_counter}'
            )
        )
    
    async def _handle_responses(self):
        """Handle incoming responses from Gemini."""
        async for response in self.session.receive():
            if response.server_content and response.server_content.model_turn:
                for part in response.server_content.model_turn.parts:
                    if part.text:
                        try:
                            analysis = json.loads(part.text)
                            await self._process_analysis(analysis)
                        except json.JSONDecodeError:
                            print(f'Non-JSON response: {part.text}')
    
    async def _process_analysis(self, analysis: dict):
        """Process anomaly detection results."""
        threat_level = analysis.get('threat_level', 'LOW')
        
        if threat_level in ['HIGH', 'CRITICAL']:
            # Trigger alert
            if self.on_anomaly_detected:
                self.on_anomaly_detected(analysis)
            
            # Log to console
            print(f'\n🚨 {threat_level} THREAT DETECTED!')
            print(f'Description: {analysis.get("description")}')
            print(f'Recommendations: {analysis.get("recommendations")}')
    
    async def send_voice_command(self, audio_bytes: bytes):
        """Process voice command from operator."""
        await self.session.send_realtime_input(
            types.LiveRealtimeInput(
                audio=types.Blob(
                    mime_type='audio/pcm;rate=16000',
                    data=audio_bytes
                )
            )
        )
    
    async def close(self):
        """Close surveillance session."""
        if self.session:
            await self.session.__aexit__(None, None, None)

# Usage Example
async def main():
    aegis = AEGISSurveillance(api_key='YOUR_API_KEY')
    
    # Set anomaly callback
    def on_anomaly(analysis):
        print(f'Alert dispatched: {analysis}')
    
    aegis.on_anomaly_detected = on_anomaly
    
    # Initialize
    await aegis.initialize()
    
    # Simulate video stream (1 FPS)
    try:
        while True:
            # In real implementation, capture from drone camera
            frame = capture_drone_frame()
            await aegis.analyze_frame(frame)
            await asyncio.sleep(1.0)  # 1 FPS
    except KeyboardInterrupt:
        await aegis.close()

if __name__ == '__main__':
    asyncio.run(main())
```

---

## 10. WebSocket Protocols for Custom Frontends

### 10.1 WebSocket vs WebRTC Comparison

| Aspect | WebSocket | WebRTC |
|--------|-----------|--------|
| **Protocol** | TCP | UDP (primarily) |
| **Latency** | Higher (~100-300ms) | Lower (~50-150ms) |
| **Complexity** | Simple | Complex (ICE, SDP, NAT traversal) |
| **Browser Support** | Universal | Modern browsers |
| **Audio Quality** | Good | Excellent (Opus codec) |
| **Network Resilience** | Poor (TCP head-of-line blocking) | Excellent (packet loss handling) |
| **Server Implementation** | Easy | Harder |
| **Production Ready** | Prototyping, server-to-server | Client-facing production |

### 10.2 WebSocket Endpoint

```
WSS Endpoint: wss://generativelanguage.googleapis.com/ws/google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContent?key={API_KEY}
```

### 10.3 Message Types

**Client → Server:**
```json
// Configuration message (first message)
{
  "config": {
    "model": "models/gemini-2.5-flash-native-audio-preview-12-2025",
    "responseModalities": ["AUDIO", "TEXT"],
    "systemInstruction": {"parts": [{"text": "..."}]}
  }
}

// Real-time input - text
{
  "realtimeInput": {"text": "Analyze this frame"}
}

// Real-time input - audio
{
  "realtimeInput": {
    "audio": {
      "data": "base64_encoded_pcm_audio",
      "mimeType": "audio/pcm;rate=16000"
    }
  }
}

// Real-time input - video
{
  "realtimeInput": {
    "video": {
      "data": "base64_encoded_jpeg",
      "mimeType": "image/jpeg"
    }
  }
}

// Tool response
{
  "toolResponse": {
    "functionResponses": [{
      "name": "function_name",
      "response": {"result": "..."}
    }]
  }
}
```

**Server → Client:**
```json
// Setup complete
{"setupComplete": {}}

// Server content (model response)
{
  "serverContent": {
    "modelTurn": {
      "parts": [
        {"inlineData": {"data": "base64_audio", "mimeType": "audio/pcm"}},
        {"text": "Transcription or analysis"}
      ]
    },
    "turnComplete": true,
    "inputTranscription": {"text": "User speech transcription"},
    "outputTranscription": {"text": "Model speech transcription"}
  }
}

// Tool call
{
  "toolCall": {
    "functionCalls": [{
      "name": "function_name",
      "args": {"param": "value"}
    }]
  }
}

// GoAway (session ending)
{
  "goAway": {
    "timeLeft": "60s"
  }
}
```

### 10.4 FastAPI WebSocket Frontend Example

```python
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
import asyncio
import json
import base64
from google import genai
from google.genai import types

app = FastAPI()
client = genai.Client(api_key='YOUR_API_KEY')

class GeminiLiveSession:
    def __init__(self):
        self.session = None
        self.websocket = None
        
    async def connect(self, websocket: WebSocket):
        self.websocket = websocket
        config = types.LiveConnectConfig(
            response_modalities=['AUDIO', 'TEXT'],
            system_instruction=types.Content(
                parts=[types.Part(text='AEGIS surveillance system')]
            )
        )
        
        self.session = await self.client.aio.live.connect(
            model='gemini-2.5-flash-native-audio-preview-12-2025',
            config=config
        ).__aenter__()
        
        # Start response forwarding
        asyncio.create_task(self._forward_responses())
    
    async def _forward_responses(self):
        async for response in self.session.receive():
            if response.server_content:
                # Forward to WebSocket client
                await self.websocket.send_json({
                    'type': 'gemini_response',
                    'data': response.server_content.model_dump()
                })
    
    async def handle_client_message(self, message: dict):
        if message.get('type') == 'audio':
            audio_data = base64.b64decode(message['data'])
            await self.session.send_realtime_input(
                types.LiveRealtimeInput(
                    audio=types.Blob(
                        mime_type='audio/pcm;rate=16000',
                        data=audio_data
                    )
                )
            )
        elif message.get('type') == 'video':
            video_data = base64.b64decode(message['data'])
            await self.session.send_realtime_input(
                types.LiveRealtimeInput(
                    media=types.Blob(
                        mime_type='image/jpeg',
                        data=video_data
                    )
                )
            )

@app.websocket('/ws/aegis')
async def aegis_websocket(websocket: WebSocket):
    await websocket.accept()
    session = GeminiLiveSession()
    await session.connect(websocket)
    
    try:
        while True:
            message = await websocket.receive_json()
            await session.handle_client_message(message)
    except WebSocketDisconnect:
        await session.session.__aexit__(None, None, None)

# HTML Frontend
html_frontend = '''
<!DOCTYPE html>
<html>
<head>
    <title>AEGIS Surveillance</title>
</head>
<body>
    <h1>AEGIS Real-Time Surveillance</h1>
    <video id="camera" autoplay></video>
    <div id="status">Disconnected</div>
    <div id="transcript"></div>
    
    <script>
        const ws = new WebSocket('ws://localhost:8000/ws/aegis');
        const video = document.getElementById('camera');
        const status = document.getElementById('status');
        const transcript = document.getElementById('transcript');
        
        // Get camera access
        navigator.mediaDevices.getUserMedia({ video: true, audio: true })
            .then(stream => {
                video.srcObject = stream;
            });
        
        ws.onopen = () => {
            status.textContent = 'Connected';
            // Start sending frames at 1 FPS
            setInterval(sendFrame, 1000);
        };
        
        ws.onmessage = (event) => {
            const msg = JSON.parse(event.data);
            if (msg.type === 'gemini_response') {
                transcript.innerHTML += '<p>' + JSON.stringify(msg.data) + '</p>';
            }
        };
        
        function sendFrame() {
            const canvas = document.createElement('canvas');
            canvas.width = video.videoWidth;
            canvas.height = video.videoHeight;
            canvas.getContext('2d').drawImage(video, 0, 0);
            
            canvas.toBlob(blob => {
                const reader = new FileReader();
                reader.onloadend = () => {
                    const base64 = reader.result.split(',')[1];
                    ws.send(JSON.stringify({
                        type: 'video',
                        data: base64
                    }));
                };
                reader.readAsDataURL(blob);
            }, 'image/jpeg');
        }
    </script>
</body>
</html>
'''

@app.get('/')
async def get_frontend():
    return HTMLResponse(html_frontend)
```

---

## 11. Recommendations for AEGIS Project

### 11.1 Architecture Recommendation

```
┌─────────────────────────────────────────────────────────────────┐
│                         AEGIS SYSTEM                            │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  ┌─────────────┐    ┌─────────────┐    ┌─────────────────────┐ │
│  │   Drone 1   │    │   Drone 2   │    │   Ground Camera     │ │
│  │  Camera +   │    │  Camera +   │    │      Station        │ │
│  │  Microphone │    │  Microphone │    │                     │ │
│  └──────┬──────┘    └──────┬──────┘    └──────────┬──────────┘ │
│         │                  │                      │            │
│         └──────────────────┼──────────────────────┘            │
│                            │                                   │
│              ┌─────────────▼─────────────┐                    │
│              │   AEGIS Gateway Server    │                    │
│              │  (FastAPI + WebSockets)   │                    │
│              │                           │                    │
│              │ - Frame capture at 1 FPS  │                    │
│              │ - Audio stream handling   │                    │
│              │ - Session management      │                    │
│              │ - Alert dispatch          │                    │
│              └─────────────┬─────────────┘                    │
│                            │                                   │
│         ┌──────────────────┼──────────────────┐              │
│         │                  │                  │              │
│  ┌──────▼──────┐   ┌──────▼──────┐   ┌──────▼──────┐       │
│  │ Gemini Live │   │ Gemini Live │   │ Gemini Live │       │
│  │  Session 1  │   │  Session 2  │   │  Session 3  │       │
│  │ (Drone 1)   │   │  (Drone 2)  │   │ (Ground)    │       │
│  └─────────────┘   └─────────────┘   └─────────────┘       │
│                                                                 │
│  ┌─────────────────────────────────────────────────────────┐  │
│  │              Alert & Reporting System                   │  │
│  │  - Dispatch emergency services                          │  │
│  │  - Log to BigQuery                                      │  │
│  │  - Real-time dashboard                                  │  │
│  └─────────────────────────────────────────────────────────┘  │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

### 11.2 Implementation Checklist

**Phase 1: MVP (Weeks 1-2)**
- [ ] Set up Gemini Live API connection
- [ ] Implement basic video frame streaming (1 FPS)
- [ ] Configure proactive audio for silent monitoring
- [ ] Set up anomaly detection with structured output
- [ ] Implement alert dispatch system

**Phase 2: Production (Weeks 3-4)**
- [ ] Add session resumption for 24/7 operation
- [ ] Implement context window compression
- [ ] Add multi-drone support (up to 3 concurrent for free tier)
- [ ] Build operator dashboard
- [ ] Integrate with emergency dispatch systems

**Phase 3: Scale (Weeks 5-6)**
- [ ] Migrate to Vertex AI for higher limits (1,000 concurrent sessions)
- [ ] Implement load balancing across multiple Gemini sessions
- [ ] Add historical analysis and reporting
- [ ] Optimize costs with proactive audio and compression

### 11.3 Critical Considerations

1. **Video Frame Rate Limitation (1 FPS)**
   - Gemini Live API processes video at 1 FPS
   - NOT suitable for high-speed anomaly detection
   - Consider using traditional CV for fast detection + Gemini for analysis

2. **Session Duration Limits**
   - 10-minute connection limit requires reconnection logic
   - Use session resumption to maintain context
   - Implement `goAway` notification handling

3. **Cost Optimization**
   - Use proactive audio to minimize output tokens
   - Enable context window compression
   - Use text output for routine monitoring
   - Reserve audio output for critical alerts

4. **Rate Limits**
   - Free tier: 3 concurrent sessions
   - Vertex AI: 1,000 concurrent sessions
   - Plan for session pooling and queueing

---

## 12. References

1. [Gemini Live API Overview](https://ai.google.dev/gemini-api/docs/live-api)
2. [Gemini Live API WebSocket Guide](https://ai.google.dev/gemini-api/docs/live-api/get-started-websocket)
3. [Gemini Live API Capabilities Guide](https://ai.google.dev/gemini-api/docs/live-guide)
4. [ADK Streaming Documentation](https://google.github.io/adk-docs/streaming/)
5. [Gemini API Cookbook](https://github.com/google-gemini/cookbook)
6. [Vertex AI Live API Documentation](https://cloud.google.com/vertex-ai/generative-ai/docs/live-api)
7. [Gemini API Pricing](https://ai.google.dev/gemini-api/docs/pricing)
8. [Gemini API Rate Limits](https://ai.google.dev/gemini-api/docs/rate-limits)

---

*Report generated for AEGIS Project - Predictive Digital Risk Twin*
*Budget: $4,000 GCP Credits*
*Target Model: gemini-live-2.5-flash-native-audio*
