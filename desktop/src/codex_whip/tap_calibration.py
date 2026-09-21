"""Direction-independent impact detection and one-pair force-range setup."""
from __future__ import annotations

import math
from collections import deque
from dataclasses import replace

from .models import RawMotionFrame
from .voice import DoubleTapDetector, DoubleTapEvent, VoiceSettings


# A deliberate handle-bottom impact creates a sharp impulse well above ordinary
# hand translation. Keep this floor independent of saved/user ranges so a stale
# low setting cannot turn casual movement into a tap.
MIN_TAP_IMPACT_G = 0.75


class ForceTapDetector(DoubleTapDetector):
    """Remove a slowly tracked gravity vector, rather than abs(|a|-1).

    Thus horizontal and vertical table impacts have comparable thresholds.
    Runtime matching uses the user-selected lower and upper strength bounds.
    """

    def __init__(self, settings):
        super().__init__(settings)
        self._gravity = None
        self._timestamp = None
        self._clock = 0
        self._turn_ms = 0
        self.last_strength = 0.0
        self._accels = deque(maxlen=16)

    def reset(self):
        super().reset()
        self._gravity = None
        self._timestamp = None
        self._turn_ms = 0
        self._accels.clear()

    def _magnitudes(self, frame):
        accel = (frame.accel_x_g, frame.accel_y_g, frame.accel_z_g)
        if self._gravity is None:
            self._gravity = accel
        dynamic = math.dist(accel, self._gravity)
        gyro = math.hypot(frame.gyro_x_dps, frame.gyro_y_dps, frame.gyro_z_dps)
        if dynamic < .25 and gyro < self.STILL_GYRO_DPS:
            self._gravity = tuple(a*.08+b*.92 for a, b in zip(accel, self._gravity))
        self.last_strength = dynamic
        return dynamic, gyro

    def _feed_frame(self, frame):
        values = (frame.accel_x_g, frame.accel_y_g, frame.accel_z_g,
                  frame.gyro_x_dps, frame.gyro_y_dps, frame.gyro_z_dps)
        if not all(math.isfinite(v) for v in values):
            self.reset()
            return None
        if self._timestamp is None:
            self._timestamp = frame.timestamp_ms
            self._gravity = values[:3]
            return None
        delta = (frame.timestamp_ms-self._timestamp) & 0xFFFFFFFF
        if delta == 0 or delta > 0x80000000:
            return None
        self._timestamp = frame.timestamp_ms
        self._clock += delta
        if delta > 120:
            self.reset()
            self._timestamp = frame.timestamp_ms
            self._gravity = values[:3]
            return None
        # A prolonged wrist swing is not a tap; brief impact-induced rotation is.
        gyro = math.hypot(*values[3:])
        self._accels.append(values[:3])
        if len(self._accels) == 16 and gyro < self.STILL_GYRO_DPS:
            mean = tuple(sum(a[i] for a in self._accels)/16 for i in range(3))
            if max(math.dist(a, mean) for a in self._accels) < .08:
                self._gravity = mean
        self._turn_ms = self._turn_ms+delta if gyro > 180 else 0
        if self._turn_ms >= 100:
            super().reset()
            self._cooldown_until_ms = self._clock+250
            return None
        return super()._feed_frame(replace(frame, timestamp_ms=self._clock))

    def _force_range(self):
        settings = self.settings
        low = (settings.tap_light_g if settings.tap_force_calibrated
               and settings.tap_light_g >= .25 else settings.impact_dynamic_accel_g)
        high = (settings.tap_heavy_g if settings.tap_force_calibrated
                and settings.tap_heavy_g > low else 12.0)
        low = max(MIN_TAP_IMPACT_G, low)
        return low, max(low + .05, high)

    def _finish_pulse(self, pulse):
        """Accept two short impacts by timing and force range only."""
        low, high = self._force_range()
        if not low <= pulse.peak_dynamic_accel_g <= high:
            self._first = None
            self._settled_since_ms = None
            self._settled_after_first = False
            return None
        if self._first is None:
            self._first = pulse
            return None
        interval = pulse.peak_at_ms - self._first.peak_at_ms
        if not self.settings.min_interval_ms <= interval <= self.settings.max_interval_ms:
            self._first = pulse if interval > self.settings.max_interval_ms else self._first
            return None
        first = self._first
        self._first = None
        self._settled_since_ms = None
        self._settled_after_first = False
        self._cooldown_until_ms = pulse.started_at_ms + self.COOLDOWN_MS
        return DoubleTapEvent(
            first.peak_dynamic_accel_g,
            pulse.peak_dynamic_accel_g,
            max(first.peak_gyro_dps, pulse.peak_gyro_dps),
            interval,
            first.peak_at_ms,
            pulse.peak_at_ms,
        )


class TapImpactMeter:
    """Measure individual table impacts without deciding whether they are a double tap.

    Calibration must not wait for the runtime gesture recognizer to accept a
    complete pair.  This meter only reports pulse strength for UI feedback;
    the user explicitly confirms the pair with the record button.
    """

    START_G = MIN_TAP_IMPACT_G
    RELEASE_G = .09
    MAX_PULSE_MS = 180
    MIN_PEAK_GAP_MS = 90

    def __init__(self, interval_ms: int, min_peak_gap_ms: int = 90):
        self.interval_ms = interval_ms
        self.min_peak_gap_ms = max(self.MIN_PEAK_GAP_MS, min_peak_gap_ms)
        self._gravity = None
        self._pulse_started_ms = None
        self._pulse_peak = 0.0
        self._pulse_peak_ms = 0
        self._last_peak_ms = -10000
        self._recent = deque(maxlen=2)

    def reset(self):
        self._gravity = None
        self._pulse_started_ms = None
        self._pulse_peak = 0.0
        self._recent.clear()

    def set_interval(self, interval_ms: int):
        self.interval_ms = interval_ms

    @property
    def strengths(self):
        return tuple(strength for _timestamp, strength in self._recent)

    @property
    def peaks(self):
        return tuple(self._recent)

    def feed(self, frame):
        accel = (frame.accel_x_g, frame.accel_y_g, frame.accel_z_g)
        if not all(math.isfinite(value) for value in accel):
            self.reset()
            return False
        if self._gravity is None:
            self._gravity = accel
        dynamic = math.dist(accel, self._gravity)
        if self._pulse_started_ms is None:
            if dynamic < .14:
                self._gravity = tuple(current*.04 + baseline*.96
                                      for current, baseline in zip(accel, self._gravity))
            if dynamic >= self.START_G:
                self._pulse_started_ms = frame.timestamp_ms
                self._pulse_peak_ms = frame.timestamp_ms
                self._pulse_peak = dynamic
            return False

        if dynamic > self._pulse_peak:
            self._pulse_peak = dynamic
            self._pulse_peak_ms = frame.timestamp_ms
        elapsed = frame.timestamp_ms - self._pulse_started_ms
        if dynamic > self.RELEASE_G and elapsed <= self.MAX_PULSE_MS:
            return False

        peak_ms, peak = self._pulse_peak_ms, self._pulse_peak
        self._pulse_started_ms = None
        self._pulse_peak = 0.0
        if peak_ms - self._last_peak_ms < self.min_peak_gap_ms:
            return False
        if self._recent and peak_ms - self._recent[-1][0] > self.interval_ms:
            self._recent.clear()
        self._recent.append((peak_ms, peak))
        self._last_peak_ms = peak_ms
        return True


class TapRangeCapture:
    """One-shot helper that suggests a force range from a single pair."""

    MIN_G = MIN_TAP_IMPACT_G
    MAX_G = 12.0

    def __init__(self, settings: VoiceSettings):
        self.stage = 'capture'
        self.done = False
        self.meter = TapImpactMeter(
            settings.max_interval_ms,
            min_peak_gap_ms=settings.min_interval_ms,
        )

    def snapshot(self, detail=''):
        peaks = self.meter.peaks
        strengths = tuple(value for _timestamp, value in peaks)
        result = dict(
            session=id(self),
            stage='done' if self.done else 'capture',
            detail=detail,
            live_strengths=strengths,
        )
        if self.done and len(strengths) == 2:
            average = sum(strengths) / 2
            minimum = max(self.MIN_G, min(self.MAX_G - .05, average * .70))
            maximum = min(self.MAX_G, max(minimum + .05, average * 1.30))
            result.update(
                average_g=average,
                suggested_min_g=round(minimum, 2),
                suggested_max_g=round(maximum, 2),
            )
        return result

    def feed(self, frame):
        if self.done or not self.meter.feed(frame):
            return None
        if len(self.meter.peaks) < 2:
            return self.snapshot('已测到第一下，请再敲一次。')
        self.done = True
        return self.snapshot('已根据这次双敲自动生成力度范围。')
