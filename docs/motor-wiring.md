# Motor Wiring

Default motor GPIO pins are configured through `.env`.

```env
LEFT_IN1=17
LEFT_IN2=27
LEFT_PWM=12
RIGHT_IN1=22
RIGHT_IN2=23
RIGHT_PWM=13
DEFAULT_SPEED=0.65
```

The current code expects an L298N motor driver with left-side and right-side motor channels.

## Safety

Before testing:

- Lift the robot wheels off the ground.
- Test at low speed first.
- Verify forward, backward, left, right, rotate, and stop.
- If a side runs backward, swap the motor wires or update the GPIO direction mapping.
