"""Compatibility entry point for old systemd voice service.

Zoro 2026 now keeps all AI, STT, TTS, and decision-making on the laptop.
On the Pi this process only streams microphone and camera data to the laptop.
"""

from .body_node import main


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
