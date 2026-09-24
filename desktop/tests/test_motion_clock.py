from codex_whip.motion_clock import MotionFrameTrace, RenderClock


def test_morph_starts_on_first_frame_and_does_not_jump_after_ui_stall():
    clock = RenderClock(.28)
    assert clock.sample(10.) == 0.
    assert 0 < clock.sample(10.016) < .1
    before = clock.elapsed
    assert clock.sample(10.516) < 1.
    assert clock.elapsed - before <= 1 / 30 + 1e-9


def test_morph_eventually_finishes_after_enough_painted_frames():
    clock = RenderClock(.28)
    for index in range(21):
        clock.sample(index * .016)
    assert clock.complete
    assert clock.sample(1.) == 1.


def test_frame_report_has_no_text_or_pose_payload():
    trace = MotionFrameTrace('recording', requested_at=.9)
    trace.frame(1., 2.)
    trace.frame(1.016, 3.)
    trace.frame(1.050, 4.)
    report = trace.report()
    assert report.mode == 'recording'
    assert report.frames == 3
    assert report.duration_ms == 50
    assert report.first_frame_wait_ms == 100
    assert report.interval_max_ms == 34
    assert report.draw_p95_ms == 4
