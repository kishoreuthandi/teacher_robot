import asyncio
import sys

import httpx
from pynput import keyboard

from .config import settings


KEY_TO_DIRECTION = {
    "w": "forward",
    "s": "backward",
    "a": "left",
    "d": "right",
}


async def send(direction: str, speed: float = 0.65) -> None:
    url = "http://127.0.0.1:8000/robot/stop" if direction == "stop" else "http://127.0.0.1:8000/robot/move"
    payload = None if direction == "stop" else {"direction": direction, "speed": speed}
    async with httpx.AsyncClient(timeout=3.0) as client:
        if payload is None:
            await client.post(url)
        else:
            await client.post(url, json=payload)


def main() -> None:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    speed = float(getattr(settings, "default_speed", 0.65) or 0.65)

    print("Keyboard control ready: W/A/S/D move, Space stop, Q quit.")

    def on_press(key):
        try:
            char = key.char.lower()
        except AttributeError:
            char = ""
        if char in KEY_TO_DIRECTION:
            loop.run_until_complete(send(KEY_TO_DIRECTION[char], speed))
            print(KEY_TO_DIRECTION[char])
        elif key == keyboard.Key.space:
            loop.run_until_complete(send("stop", speed))
            print("stop")
        elif char == "q":
            loop.run_until_complete(send("stop", speed))
            return False
        sys.stdout.flush()
        return None

    with keyboard.Listener(on_press=on_press) as listener:
        listener.join()


if __name__ == "__main__":
    main()

