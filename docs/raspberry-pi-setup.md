# Raspberry Pi Setup

The Pi is used as an edge device. It does not run the AI model. It streams microphone and camera data to the laptop backend and receives speaker/motor/display commands.

## Services

The project uses two Pi services:

- `zoro2026-agent.service`: FastAPI hardware API on port `8000`
- `zoro2026-body.service`: camera and microphone websocket streamer

Template files are in `deploy/`.

## Hardware

- USB camera through powered hub
- USB microphone through powered hub
- USB speaker through powered hub
- HDMI display for robot face
- L298N motor driver connected to GPIO pins

## Notes

Keep API keys on the laptop `.env` file. The Pi should only need hardware and laptop connection settings.
