from codex_whip.presentation_timeline import PresentationFrame, PresentationTimeline


WHIP = PresentationFrame('whip', 'whip', '')
RECORDING = PresentationFrame('recording', 'recording', 'recording')
RECOGNIZING = PresentationFrame('recognizing', 'recognizing', 'recognizing voice')
PENDING = PresentationFrame('pending', 'whip', '听到的文字', 'beat it, then send', 20.)


def test_double_tap_preempts_idle_without_waiting_a_second():
    timeline = PresentationTimeline()
    assert timeline.resolve(WHIP, 0.) == WHIP
    timeline.note('recording', .1)
    assert timeline.resolve(RECORDING, .1) == RECORDING
    assert timeline.entered_at == .1
    assert timeline.presented_at is None


def test_recording_hold_starts_at_first_painted_frame_and_waits_for_completion():
    timeline = PresentationTimeline()
    timeline.resolve(WHIP, 0.)
    timeline.note('recording', .1)
    timeline.note('recognizing', .3)
    assert timeline.resolve(RECOGNIZING, 2.) == RECORDING  # Not painted yet.
    timeline.mark_presented('recording', 2.)
    assert timeline.resolve(RECOGNIZING, 2.9) == RECORDING
    assert timeline.resolve(RECOGNIZING, 3., visual_complete=False) == RECORDING
    assert timeline.resolve(RECOGNIZING, 3., visual_complete=True) == RECOGNIZING
    timeline.mark_presented('recognizing', 3.1)
    assert timeline.resolve(PENDING, 4.11, visual_complete=False) == RECOGNIZING
    assert timeline.resolve(PENDING, 4.11, visual_complete=True) == PENDING


def test_fast_recognition_still_gets_its_own_presented_transition():
    timeline = PresentationTimeline()
    timeline.resolve(WHIP, 0.)
    timeline.note('recording', .1)
    timeline.mark_presented('recording', .12)
    timeline.note('recognizing', .3)
    assert timeline.resolve(PENDING, .5) == RECORDING
    assert timeline.resolve(PENDING, 1.12) == RECOGNIZING
    assert timeline.resolve(PENDING, 2.5) == RECOGNIZING  # Wait for its first frame.
    timeline.mark_presented('recognizing', 2.5)
    assert timeline.resolve(PENDING, 3.5, visual_complete=False) == RECOGNIZING
    assert timeline.resolve(PENDING, 3.5, visual_complete=True) == PENDING


def test_new_recording_discards_old_pending_state():
    timeline = PresentationTimeline()
    timeline.resolve(WHIP, 0.)
    timeline.note('recording', .1)
    timeline.mark_presented('recording', .12)
    timeline.note('recognizing', .3)
    timeline.note('recording', .4)
    assert timeline.resolve(RECORDING, .4) == RECORDING
    assert timeline.presented_at is None
    timeline.mark_presented('recording', .5)
    assert timeline.resolve(WHIP, 1.49) == RECORDING
    assert timeline.resolve(WHIP, 1.5) == WHIP


def test_sleep_and_connecting_do_not_delay_real_actions():
    timeline = PresentationTimeline()
    connecting = PresentationFrame('connecting', 'connecting', 'Connecting')
    sleep = PresentationFrame('sleep', 'sleep', 'deep sleep.')
    assert timeline.resolve(connecting, 0.) == connecting
    assert timeline.resolve(WHIP, .2) == WHIP
    assert timeline.resolve(sleep, .3) == sleep
    assert timeline.resolve(WHIP, .4) == WHIP


def test_pending_clears_immediately_after_send():
    timeline = PresentationTimeline()
    assert timeline.resolve(PENDING, 0.) == PENDING
    assert timeline.resolve(WHIP, .1) == WHIP
