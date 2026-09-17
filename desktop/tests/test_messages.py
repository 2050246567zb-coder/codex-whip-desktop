import random

import pytest

from codex_whip.messages import (
    MessageProfile,
    MessageProfileStore,
    PromptSelector,
    load_message_profile,
    reorder_messages,
    strength_label,
)
from codex_whip.models import WhipEvent


def test_strength_labels() -> None:
    assert strength_label(WhipEvent(1, 700, 2, 80)) == "轻"
    assert strength_label(WhipEvent(2, 1000, 2, 80)) == "正常"
    assert strength_label(WhipEvent(3, 1600, 2, 80)) == "猛"


def test_prompt_template_fields() -> None:
    selector = PromptSelector(
        ("{strength}:{peak_gyro}:{peak_accel}",), random.Random(1)
    )
    assert selector.choose(WhipEvent(1, 980.1, 3.21, 120)) == "正常:980:3.21"


def test_sequential_messages_are_live_and_persisted(tmp_path) -> None:
    path = tmp_path / "messages.json"
    store = MessageProfileStore(("fallback",), path, random.Random(2))
    store.update(MessageProfile("sequential", ("one", "two")))
    selector = PromptSelector(store)

    event = WhipEvent(1, 980, 3.2, 120)
    assert [selector.choose(event) for _ in range(3)] == ["one", "two", "one"]
    assert load_message_profile(path, ("fallback",)).messages == ("one", "two")


def test_random_messages_do_not_repeat_back_to_back(tmp_path) -> None:
    store = MessageProfileStore(("a", "b", "c"), tmp_path / "messages.json", random.Random(5))
    store.update(MessageProfile("random", ("a", "b", "c")))
    selected = [store.choose_template() for _ in range(20)]

    assert all(first != second for first, second in zip(selected, selected[1:]))


def test_message_reordering_and_template_validation() -> None:
    assert reorder_messages(("a", "b", "c"), 2, 0) == ("c", "a", "b")
    with pytest.raises(ValueError, match="不支持"):
        MessageProfile("sequential", ("{unknown}",)).validated()
