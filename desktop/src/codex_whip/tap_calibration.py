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


class TapCalibration:
    def __init__(self, settings: VoiceSettings, interval_ms: int | None = None):
        self.stage = 'light'
        self.light = None
        self.heavy = None
        self.draft = None
        self.tests = 0
        self.accepted = 0
        self.base = settings
        self.interval_ms = max(200, min(1000, settings.max_interval_ms)) if interval_ms is None else interval_ms
        if not 200 <= self.interval_ms <= 1000:
            raise ValueError('双敲时间段须在 0.2–1 秒')
        self.detector = ForceTapDetector(replace(settings,
            impact_dynamic_accel_g=.25, max_tap_gyro_dps=2000,
            pre_still_ms=120, settle_ms=50, max_pulse_ms=180,
            min_interval_ms=150, max_interval_ms=self.interval_ms))

    def set_interval(self, interval_ms):
        if not 200 <= interval_ms <= 1000:
            raise ValueError('双敲时间段须在 0.2–1 秒')
        self.interval_ms = interval_ms
        self.detector.update_settings(replace(self.detector.settings,
            min_interval_ms=150, max_interval_ms=interval_ms))
        if self.draft is not None:
            self.draft = replace(self.draft, min_interval_ms=150, max_interval_ms=interval_ms).validated()
            self.test_detector.update_settings(self.draft)
            self.tests = self.accepted = 0

    def snapshot(self, detail=''):
        return dict(session=id(self), stage=self.stage, detail=detail, tests=self.tests,
                    accepted=self.accepted, interval_ms=self.interval_ms,
                    threshold=self.draft.impact_dynamic_accel_g if self.draft else None,
                    light=min(self.light.first_peak_dynamic_accel_g,
                              self.light.second_peak_dynamic_accel_g) if self.light else None,
                    heavy=max(self.heavy.first_peak_dynamic_accel_g,
                              self.heavy.second_peak_dynamic_accel_g) if self.heavy else None)

    def feed(self, frame):
        event = self.detector._feed_frame(frame)
        if event is None:
            return None
        if self.stage == 'light':
            self.light = event
            self.stage = 'heavy'
            return self.snapshot('轻敲已记录。现在用日常较重力度，底部双敲一次。')
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
            return self.snapshot('自由测试：轻敲或重敲均可。这里只显示结果，不录音、不发送。')
        return event

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
