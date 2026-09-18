from PIL import Image, ImageDraw
from codex_whip.sand_countdown import sand_surface
from codex_whip.voice import VoiceModule, VoiceSettingsStore


def test_candidate_expires_at_ten_seconds_even_without_ui_polling(tmp_path, monkeypatch):
    clock = [100.]
    monkeypatch.setattr('codex_whip.voice.time.monotonic',lambda:clock[0])
    events = []
    voice = VoiceModule(VoiceSettingsStore(tmp_path/'voice.json'),lambda *args:events.append(args))
    voice.set_pending('first')
    clock[0] = 109.999
    assert voice.pending_text == 'first'
    clock[0] = 110.
    assert voice.pending_text is None
    assert events[-1] == ('voice_pending',None)
    count = len(events)
    assert not voice.expire_pending()
    assert len(events) == count


def test_new_candidate_gets_own_deadline_and_success_cancels_expiry(tmp_path,monkeypatch):
    clock = [100.]
    monkeypatch.setattr('codex_whip.voice.time.monotonic',lambda:clock[0])
    voice = VoiceModule(VoiceSettingsStore(tmp_path/'voice.json'),lambda *args:None)
    voice.set_pending('old')
    clock[0] = 109.
    voice.set_pending('new')
    clock[0] = 110.
    assert voice.pending_text == 'new'
    voice.mark_sent('new')
    assert voice.pending_text is None
    assert voice._pending_until == 0


def test_sand_wipes_right_to_left_and_finishes_gray():
    mask = Image.new('L',(100,60))
    ImageDraw.Draw(mask).rectangle((10,10,90,25),fill=255)
    beginning = sand_surface(mask,0,'white',particles=False)
    halfway = sand_surface(mask,.5,'white',particles=False)
    end = sand_surface(mask,1,'white',particles=False)
    assert beginning.getpixel((80,15)) == (29,29,31)
    assert halfway.getpixel((20,15)) == (29,29,31)
    assert halfway.getpixel((80,15)) == (182,182,186)
    assert end.getpixel((20,15)) == (182,182,186)
    assert sand_surface(mask,.5,'white').tobytes() != halfway.tobytes()
