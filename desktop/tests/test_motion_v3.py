import math

from codex_whip.models import RawMotionBatch, RawMotionFrame
from codex_whip.motion_v3 import (
    MotionEngine,
    dtw_distance,
    extract_template,
    load_motion_profile,
    train_motion_profile,
)


def motion_frames(
    *, amplitude: float = 900.0, width: float = 150.0, impact_first: bool = False
) -> tuple[RawMotionFrame, ...]:
    frames = []
    for timestamp in range(0, 1600, 10):
        phase = (timestamp - 800) / width
        pulse = math.exp(-(phase**2))
        gyro = amplitude * pulse
        impact = math.exp(-(((timestamp - (650 if impact_first else 850)) / 45) ** 2))
        frames.append(
            RawMotionFrame(
                timestamp,
                gyro,
                gyro * 0.28,
                -gyro * 0.12,
                0.15 + impact * (2.0 if impact_first else 0.7),
                0.0,
                0.99,
            )
        )
    return tuple(frames)


def test_dtw_accepts_similar_motion_and_rejects_impact_shape() -> None:
    positives = [
        extract_template(motion_frames(amplitude=amplitude, width=width), "positive")
        for amplitude, width in ((820, 135), (900, 150), (980, 165), (870, 175))
    ]
    negative = extract_template(
        motion_frames(amplitude=380, width=55, impact_first=True), "negative"
    )
    profile = train_motion_profile(positives, [negative])
    engine = MotionEngine()
    engine.profile = profile
    similar = extract_template(motion_frames(amplitude=930, width=158), "candidate")
    assert engine.classify(similar)
    assert not engine.classify(negative)
    assert dtw_distance(similar.features, positives[1].features) < dtw_distance(
        similar.features, negative.features
    )


def test_motion_profile_round_trip_and_ring_capture(tmp_path) -> None:
    path = tmp_path / "motion.json"
    engine = MotionEngine(path)
    frames = motion_frames()
    for sequence, offset in enumerate(range(0, len(frames), 4)):
        batch_frames = frames[offset : offset + 4]
        engine.feed_batch(
            RawMotionBatch(sequence, batch_frames[0].timestamp_ms, batch_frames)
        )
    captured = engine.capture_recent("positive")
    engine.train([captured, captured, captured], [])
    restored = load_motion_profile(path)
    assert restored is not None
    assert restored.trained
    assert restored.positive_templates[0].peak_gyro_dps > 800


def trained_engine(tmp_path):
    engine = MotionEngine(tmp_path / 'motion.json')
    examples = [extract_template(motion_frames(amplitude=a), 'positive') for a in (820, 900, 980)]
    engine.train(examples, [])
    return engine


def feed_events(engine, frames):
    events = []
    for i in range(0, len(frames), 4):
        part = tuple(frames[i:i+4])
        event = engine.feed_batch(RawMotionBatch(i, part[0].timestamp_ms, part))
        if event:
            events.append(event)
    return events


def test_streamed_whip_requires_complete_rotation_and_braking(tmp_path):
    engine = trained_engine(tmp_path)
    events = feed_events(engine, motion_frames(amplitude=910))
    assert len(events) == 1
    assert events[0].angular_travel_deg > 35


def test_endless_rotation_is_rejected_even_if_template_classifier_accepts(tmp_path):
    engine = trained_engine(tmp_path)
    engine.classify = lambda template: True
    frames = [RawMotionFrame(t, 900, 0, 0, 0, 0, 1) for t in range(0, 3000, 10)]
    assert not feed_events(engine, frames)
    assert engine.last_rejection == 'no_braking_tail'


def test_table_impact_or_single_gyro_spike_cannot_pass_template_alone(tmp_path):
    engine = trained_engine(tmp_path)
    engine.classify = lambda template: True
    frames = [RawMotionFrame(t, 700 if t == 800 else 0, 0, 0, 0, 0, 4 if t == 800 else 1)
              for t in range(0, 1600, 10)]
    assert not feed_events(engine, frames)


def test_timeout_shake_tail_cannot_rearm_until_still_then_new_whip_works(tmp_path):
    engine = trained_engine(tmp_path)
    engine.classify = lambda template: True
    frames = [RawMotionFrame(t, 900 if t < 2400 else 0, 0, 0, 0, 0, 1)
              for t in range(0, 3500, 10)]
    assert not feed_events(engine, frames)
    assert not engine._await_quiet
    new_whip = [RawMotionFrame(f.timestamp_ms + 4000, f.gyro_x_dps, f.gyro_y_dps,
                              f.gyro_z_dps, f.accel_x_g, f.accel_y_g, f.accel_z_g)
                for f in motion_frames()]
    assert len(feed_events(engine, new_whip)) == 1


def test_reboot_and_gap_discard_partial_gesture_without_erasing_training(tmp_path):
    engine = trained_engine(tmp_path)
    feed_events(engine, [RawMotionFrame(t, 900, 0, 0, 0, 0, 1) for t in range(2000, 2150, 10)])
    assert engine._active_since is not None
    feed_events(engine, [RawMotionFrame(t, 0, 0, 0, 0, 0, 1) for t in range(0, 500, 10)])
    assert engine._active_since is None
    assert engine.trained
