"""Servo pan/tilt controller.

Stub implementation — no GPIO is touched until ``servo.enabled = true`` in
settings.yaml.  When the hardware arrives, fill in _init_gpio() and _apply()
and flip the flag; everything else stays the same.

Wiring convention (confirm pin numbers with your specific rig):
    Pan servo signal  → BCM GPIO pin defined by servo.pan_pin  (default 18)
    Tilt servo signal → BCM GPIO pin defined by servo.tilt_pin (default 19)
    Both servos share a 5V supply and a common GND with the Pi.

Coordinate convention:
    pan  -1.0 = full left,   0.0 = centre,  +1.0 = full right
    tilt -1.0 = full down,   0.0 = centre,  +1.0 = full up
"""

import logging
import threading
import time

from .config import SERVO_ENABLED, SERVO_PAN_PIN, SERVO_TILT_PIN, SERVO_SPEED

log = logging.getLogger(__name__)

_TICK_HZ  = 20              # position-update rate while moving
_TICK_S   = 1.0 / _TICK_HZ
_STEP_MAX = 0.05            # max position change per tick at velocity=1.0


class ServoController:
    """Velocity-based pan/tilt servo controller.

    The caller sets a velocity via move() (continuously, e.g. from touch events)
    and calls stop() when the gesture ends.  An internal loop steps the servo
    position at _TICK_HZ, clamped to -1..1 on each axis.

    In stub mode (enabled=False) all calls are logged at DEBUG level and the
    update loop is not started — no GPIO is imported or touched.
    """

    def __init__(self, enabled: bool, pan_pin: int, tilt_pin: int, speed: float):
        self.enabled  = enabled
        self.pan_pin  = pan_pin
        self.tilt_pin = tilt_pin
        self.speed    = max(0.0, min(1.0, speed))

        self._lock     = threading.Lock()
        self._pan_vel  = 0.0    # commanded velocity, -1..1
        self._tilt_vel = 0.0
        self._pan_pos  = 0.0    # current position,   -1..1
        self._tilt_pos = 0.0

        if self.enabled:
            self._init_gpio()
            threading.Thread(target=self._loop, daemon=True, name="servo").start()
            log.info("Servo controller started (pan_pin=%d tilt_pin=%d speed=%.1f)",
                     pan_pin, tilt_pin, speed)
        else:
            log.info("Servo controller in stub mode — set servo.enabled=true in settings.yaml to activate")

    # ── Public API ─────────────────────────────────────────────────────────────

    def move(self, pan: float, tilt: float) -> None:
        """Set pan/tilt velocity.  Values clamped to -1.0..1.0; 0 = hold."""
        pan  = max(-1.0, min(1.0, float(pan)))
        tilt = max(-1.0, min(1.0, float(tilt)))
        with self._lock:
            self._pan_vel  = pan
            self._tilt_vel = tilt
        if not self.enabled:
            log.debug("servo.move  pan=%.2f  tilt=%.2f  [stub]", pan, tilt)

    def stop(self) -> None:
        """Stop all movement; servo holds current position."""
        with self._lock:
            self._pan_vel  = 0.0
            self._tilt_vel = 0.0
        if not self.enabled:
            log.debug("servo.stop  [stub]")

    def status(self) -> dict:
        with self._lock:
            return {
                "enabled":  self.enabled,
                "pan_vel":  round(self._pan_vel,  3),
                "tilt_vel": round(self._tilt_vel, 3),
                "pan_pos":  round(self._pan_pos,  3),
                "tilt_pos": round(self._tilt_pos, 3),
            }

    # ── GPIO initialisation ────────────────────────────────────────────────────

    def _init_gpio(self) -> None:
        """Set up PWM output on pan and tilt pins.

        TODO: replace stub with real implementation when hardware arrives.

        pigpio example (recommended — hardware PWM, sub-microsecond accuracy):
            import pigpio
            self._pi = pigpio.pi()
            self._pi.set_mode(self.pan_pin,  pigpio.OUTPUT)
            self._pi.set_mode(self.tilt_pin, pigpio.OUTPUT)

        RPi.GPIO example (software PWM — simpler but jitter at high load):
            import RPi.GPIO as GPIO
            GPIO.setmode(GPIO.BCM)
            GPIO.setup(self.pan_pin,  GPIO.OUT)
            GPIO.setup(self.tilt_pin, GPIO.OUT)
            self._pan_pwm  = GPIO.PWM(self.pan_pin,  50)   # 50 Hz
            self._tilt_pwm = GPIO.PWM(self.tilt_pin, 50)
            self._pan_pwm.start(7.5)   # 7.5 % ≈ 1500 µs = centre
            self._tilt_pwm.start(7.5)
        """
        log.warning("servo._init_gpio: stub body — add real GPIO calls here")

    # ── Position update loop ───────────────────────────────────────────────────

    def _loop(self) -> None:
        """Step servo position by velocity × speed at _TICK_HZ."""
        while True:
            with self._lock:
                pv = self._pan_vel
                tv = self._tilt_vel

            if pv != 0.0 or tv != 0.0:
                step = _STEP_MAX * self.speed
                with self._lock:
                    self._pan_pos  = max(-1.0, min(1.0, self._pan_pos  + pv * step))
                    self._tilt_pos = max(-1.0, min(1.0, self._tilt_pos + tv * step))
                    pp, tp = self._pan_pos, self._tilt_pos
                self._apply(pp, tp)

            time.sleep(_TICK_S)

    def _apply(self, pan_pos: float, tilt_pos: float) -> None:
        """Send PWM pulse widths to the servos.

        TODO: replace stub with real implementation when hardware arrives.

        pan_pos and tilt_pos are normalised -1..1.  Convert to pulse width
        with _pw_range(), then write to GPIO.

        pigpio example:
            PAN_PW_MIN,  PAN_PW_MAX  = 500, 2500   # µs — adjust for your servo
            TILT_PW_MIN, TILT_PW_MAX = 500, 2500
            pan_pw  = self._pw_range(pan_pos,  PAN_PW_MIN,  PAN_PW_MAX)
            tilt_pw = self._pw_range(tilt_pos, TILT_PW_MIN, TILT_PW_MAX)
            self._pi.set_servo_pulsewidth(self.pan_pin,  pan_pw)
            self._pi.set_servo_pulsewidth(self.tilt_pin, tilt_pw)

        RPi.GPIO example (duty cycle = pulse_width_µs / 20000 * 100):
            pan_pw  = self._pw_range(pan_pos,  500, 2500)
            tilt_pw = self._pw_range(tilt_pos, 500, 2500)
            self._pan_pwm.ChangeDutyCycle(pan_pw  / 200.0)
            self._tilt_pwm.ChangeDutyCycle(tilt_pw / 200.0)
        """
        pass  # stub

    @staticmethod
    def _pw_range(pos: float, pw_min: int, pw_max: int) -> int:
        """Map normalised -1..1 to pulse width µs between pw_min and pw_max."""
        return int(pw_min + (pos + 1.0) / 2.0 * (pw_max - pw_min))


servo = ServoController(
    enabled  = SERVO_ENABLED,
    pan_pin  = SERVO_PAN_PIN,
    tilt_pin = SERVO_TILT_PIN,
    speed    = SERVO_SPEED,
)
