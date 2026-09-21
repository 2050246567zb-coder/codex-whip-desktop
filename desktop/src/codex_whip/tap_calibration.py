"""Direction-independent impulse detection and a two-pair calibration draft."""
from __future__ import annotations

import math
import copy
from collections import deque
from dataclasses import replace

from .models import RawMotionFrame
from .voice import DoubleTapDetector, VoiceSettings


class ForceTapDetector(DoubleTapDetector):
    """Remove a slowly tracked gravity vector, rather than abs(|a|-1).

    Thus horizontal and vertical table impacts have comparable thresholds.
    No trajectory template or upper strength bound is used.
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


class TapImpactMeter:
    """Measure individual table impacts without deciding whether they are a double tap.

    Calibration must not wait for the runtime gesture recognizer to accept a
    complete pair.  This meter only reports pulse strength for UI feedback;
    the user explicitly confirms the pair with the record button.
    """

    START_G = .18
    RELEASE_G = .09
    MAX_PULSE_MS = 180
    MIN_PEAK_GAP_MS = 90

    def __init__(self, interval_ms: int):
        self.interval_ms = interval_ms
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
        if peak_ms - self._last_peak_ms < self.MIN_PEAK_GAP_MS:
            return False
        if self._recent and peak_ms - self._recent[-1][0] > self.interval_ms:
            self._recent.clear()
        self._recent.append((peak_ms, peak))
        self._last_peak_ms = peak_ms
        return True


class TapCalibration:
    def __init__(self, settings: VoiceSettings, interval_ms: int | None = None,
                 after_timestamp_ms: int = -1):
        self.stage = 'light'
        self.light = None
        self.heavy = None
        self.draft = None
        self.tests = 0
        self.accepted = 0
        self.base = settings
        self.after_timestamp_ms = after_timestamp_ms
        self.interval_ms = max(200, min(1000, settings.max_interval_ms)) if interval_ms is None else interval_ms
        if not 200 <= self.interval_ms <= 1000:
            raise ValueError('双敲时间段须在 0.2–1 秒')
        self.detector = ForceTapDetector(replace(settings,
            impact_dynamic_accel_g=.25, max_tap_gyro_dps=2000,
            pre_still_ms=120, settle_ms=50, max_pulse_ms=180,
            min_interval_ms=150, max_interval_ms=self.interval_ms))
        self.meter = TapImpactMeter(self.interval_ms)

    def set_interval(self, interval_ms):
        if not 200 <= interval_ms <= 1000:
            raise ValueError('双敲时间段须在 0.2–1 秒')
        self.interval_ms = interval_ms
        self.meter.set_interval(interval_ms)
        self.detector.update_settings(replace(self.detector.settings,
            min_interval_ms=150, max_interval_ms=interval_ms))
        if self.draft is not None:
            self.draft = replace(self.draft, min_interval_ms=150, max_interval_ms=interval_ms).validated()
            self.test_detector.update_settings(self.draft)
            self.tests = self.accepted = 0

    def snapshot(self, detail=''):
        strengths = self.meter.strengths
        return dict(session=id(self), stage=self.stage, detail=detail, tests=self.tests,
                    accepted=self.accepted, interval_ms=self.interval_ms,
                    live_strengths=strengths,
                    threshold=self.draft.impact_dynamic_accel_g if self.draft else None,
                    light=min(self.light.first_peak_dynamic_accel_g,
                              self.light.second_peak_dynamic_accel_g) if self.light else None,
                    heavy=max(self.heavy.first_peak_dynamic_accel_g,
                              self.heavy.second_peak_dynamic_accel_g) if self.heavy else None)

    def feed(self, frame):
        if not self.meter.feed(frame):
            return None
        return self.snapshot('敲击力度已更新；完成两下后点击“录入本次”。')

    def record(self, event):
        """Accept the pair the user explicitly confirmed in the UI."""
        if event.second_at_ms <= self.after_timestamp_ms:
            raise ValueError('没有新的双敲动作，请敲完两下后再录入')
        self.after_timestamp_ms = event.second_at_ms
        if self.stage == 'light':
            self.light = event
            self.stage = 'heavy'
            self.meter.reset()
            return self.snapshot('轻敲已录入。现在完成一次较重双敲，再点击“录入本次”。')
        if self.stage == 'heavy':
            low = min(self.light.first_peak_dynamic_accel_g, self.light.second_peak_dynamic_accel_g)
            high = max(event.first_peak_dynamic_accel_g, event.second_peak_dynamic_accel_g)
            if high < max(self.light.first_peak_dynamic_accel_g, self.light.second_peak_dynamic_accel_g):
                return self.snapshot('这次力度比轻敲还小，请稍重一些再双敲；不需要猛砸。')
            self.heavy = event
            self.draft = replace(self.base, tap_force_calibrated=True,
                tap_light_g=low, tap_heavy_g=high,
                impact_dynamic_accel_g=max(.25, min(12, low*.65)),
                max_tap_gyro_dps=2000, pre_still_ms=120, settle_ms=50,
                max_pulse_ms=180,
                min_interval_ms=150, max_interval_ms=self.interval_ms).validated()
            # Keep the permissive detector in test mode to report below-threshold
            # pairs too. Accepted events use exactly the final settings detector.
            self.test_detector = copy.deepcopy(self.detector)
            self.test_detector.settings = self.draft
            self._last_test_ms = -10000
            self.stage = 'test'
            self.meter.reset()
            return self.snapshot('自由测试：轻敲或重敲均可。这里只显示结果，不录音、不发送。')
        raise ValueError('力度录入已经完成；可自由测试或重新校准')

    def feed_test(self, frame):
        was_waiting = self.detector._first is not None
        accepted = self.test_detector._feed_frame(frame)
        candidate = self.detector._feed_frame(frame)
        if accepted is not None:
            self._last_test_ms = self.test_detector._clock
            self.tests += 1
            self.accepted += 1
            return self.snapshot(f'✓ 识别成功 · {accepted.first_peak_dynamic_accel_g:.2f} / '
                                 f'{accepted.second_peak_dynamic_accel_g:.2f} g')
        if candidate is not None and self.detector._clock-self._last_test_ms > 200:
            self.tests += 1
            return self.snapshot('未通过当前阈值或双敲节奏，请再试一次，或重新校准。')
        if not was_waiting and self.detector._first is not None:
            return self.snapshot('收到第一次敲击，等待第二次。')
        if was_waiting and self.detector._first is None and candidate is None:
            return self.snapshot('未组成有效双敲，请再敲两下。')
        return None
