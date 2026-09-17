import pytest

from codex_whip.hotkeys import (
    MOD_ALT,
    MOD_CONTROL,
    MOD_NOREPEAT,
    MOD_SHIFT,
    parse_hotkey,
)


def test_parse_default_hotkey() -> None:
    parsed = parse_hotkey("Ctrl + Alt + Shift + X")

    assert parsed.canonical == "ctrl+alt+shift+x"
    assert parsed.virtual_key == ord("X")
    assert parsed.modifiers == MOD_CONTROL | MOD_ALT | MOD_SHIFT | MOD_NOREPEAT


def test_parse_function_key_and_alias() -> None:
    parsed = parse_hotkey("control+F8")

    assert parsed.canonical == "ctrl+f8"
    assert parsed.virtual_key == 0x77


def test_parse_macos_command_alias_without_changing_saved_format() -> None:
    parsed = parse_hotkey("command+shift+x")

    assert parsed.canonical == "shift+win+x"


@pytest.mark.parametrize("value", ("x", "ctrl", "ctrl+space", "ctrl+x+y"))
def test_parse_hotkey_rejects_unsafe_or_unknown_values(value: str) -> None:
    with pytest.raises(ValueError):
        parse_hotkey(value)
