from codex_whip.gate import EventGate
from codex_whip.models import WhipEvent


def event(sequence: int) -> WhipEvent:
    return WhipEvent(sequence, 900.0, 3.0, 100)


def test_gate_rejects_duplicates_and_bursts() -> None:
    now = [10.0]
    gate = EventGate(1.5, clock=lambda: now[0])

    assert gate.accept(event(1))
    assert not gate.accept(event(1))
    now[0] = 10.5
    assert not gate.accept(event(2))
    now[0] = 11.6
    assert gate.accept(event(2))


def test_gate_accepts_reused_sequence_after_reboot_window() -> None:
    now = [10.0]
    gate = EventGate(1.5, clock=lambda: now[0], duplicate_window_seconds=10.0)
    assert gate.accept(event(1))
    now[0] = 21.0
    assert gate.accept(event(1))
