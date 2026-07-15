# Architecture

Zoro uses a split architecture:

- The laptop runs AI-heavy work: LLM responses, RAG, STT/TTS orchestration, perception, attendance, transcripts, and the web dashboard.
- The Raspberry Pi Zero 2 W acts as an edge hardware bridge for USB devices and GPIO motor control.

## Runtime Flow

```text
Student voice
  -> Pi microphone
  -> Laptop backend websocket
  -> Deepgram STT
  -> Classroom brain / RAG / LLM
  -> Cartesia TTS stream
  -> Pi speaker
```

```text
Pi USB camera
  -> Pi body node
  -> Laptop perception
  -> Dashboard MJPEG stream
  -> Attendance / obstacle / behavior systems
```

```text
Dashboard or voice movement command
  -> Laptop backend safety check
  -> Pi motor endpoint
  -> L298N motor driver
```
