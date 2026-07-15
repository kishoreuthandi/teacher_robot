from dataclasses import dataclass

try:
    from gpiozero import Motor, PWMOutputDevice
except ImportError:  # Allows development on laptop without GPIO hardware.
    Motor = None
    PWMOutputDevice = None

from .config import settings


@dataclass
class MotorState:
    direction: str = "stop"
    speed: float = 0.0


class DriveBase:
    def __init__(self) -> None:
        self.state = MotorState()
        self.simulated = Motor is None or PWMOutputDevice is None
        self.left = None
        self.right = None
        self.left_pwm = None
        self.right_pwm = None

        if not self.simulated:
            self.left = Motor(forward=settings.left_in1, backward=settings.left_in2)
            self.right = Motor(forward=settings.right_in1, backward=settings.right_in2)
            self.left_pwm = PWMOutputDevice(settings.left_pwm)
            self.right_pwm = PWMOutputDevice(settings.right_pwm)
            self._set_speed(0.0)

    def _set_speed(self, speed: float) -> None:
        speed = max(0.0, min(1.0, speed))
        if self.left_pwm and self.right_pwm:
            self.left_pwm.value = speed
            self.right_pwm.value = speed

    def _run(self, left: str, right: str, speed: float, direction: str) -> dict:
        speed = max(0.0, min(1.0, speed))
        self.state = MotorState(direction=direction, speed=speed)
        if self.simulated:
            return self.status()

        self._set_speed(speed)
        for motor, action in ((self.left, left), (self.right, right)):
            if action == "forward":
                motor.forward()
            elif action == "backward":
                motor.backward()
            else:
                motor.stop()
        return self.status()

    def forward(self, speed: float) -> dict:
        return self._run("forward", "forward", speed, "forward")

    def backward(self, speed: float) -> dict:
        return self._run("backward", "backward", speed, "backward")

    def left_turn(self, speed: float) -> dict:
        return self._run("backward", "forward", speed, "left")

    def right_turn(self, speed: float) -> dict:
        return self._run("forward", "backward", speed, "right")

    def stop(self) -> dict:
        self.state = MotorState()
        if not self.simulated:
            self.left.stop()
            self.right.stop()
            self._set_speed(0.0)
        return self.status()

    def move(self, direction: str, speed: float) -> dict:
        # Physical wiring/orientation remap for Zoro chassis:
        # old forward -> robot turns left, old backward -> turns right,
        # old left turn -> robot moves backward, old right turn -> moves forward.
        if direction == "forward":
            return self._run("forward", "backward", speed, "forward")
        if direction == "backward":
            return self._run("backward", "forward", speed, "backward")
        if direction == "left":
            return self._run("forward", "forward", speed, "left")
        if direction == "right":
            return self._run("backward", "backward", speed, "right")
        if direction == "rotate":
            return self.right_turn(speed)
        return self.stop()

    def status(self) -> dict:
        return {
            "ok": True,
            "direction": self.state.direction,
            "speed": self.state.speed,
            "simulated": self.simulated,
        }
