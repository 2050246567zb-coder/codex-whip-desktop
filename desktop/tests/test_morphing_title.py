from PIL import Image, ImageDraw, ImageChops, ImageStat
from codex_whip.morphing_title import blend_masks


def test_visible_change_spans_full_duration():
    old,new = Image.new('L',(200,60)),Image.new('L',(200,60))
    ImageDraw.Draw(old).rectangle((10,10,60,50),fill=255)
    ImageDraw.Draw(new).rectangle((130,10,180,50),fill=255)
    frames = [blend_masks(old,new,i/8) for i in range(9)]
    assert frames[0].tobytes() == old.tobytes()
    assert frames[-1].tobytes() == new.tobytes()
    differences = [sum(ImageStat.Stat(ImageChops.difference(a,b)).sum)
                   for a,b in zip(frames,frames[1:])]
    assert min(differences)>0
    assert blend_masks(old,new,.02).tobytes() != old.tobytes()


def test_fusion_has_solid_ink_not_a_translucent_crossfade():
    old,new = Image.new('L',(200,60)),Image.new('L',(200,60))
    ImageDraw.Draw(old).rectangle((10,10,85,50),fill=255)
    ImageDraw.Draw(new).rectangle((95,10,180,50),fill=255)
    frame = blend_masks(old,new,.5)
    assert frame.getpixel((45,30)) > 240
    assert Image.blend(old,new,.5).getpixel((45,30)) < 130


def test_blank_endpoint_morph_still_builds_and_dissolves_text():
    blank, text = Image.new('L',(200,60)), Image.new('L',(200,60))
    ImageDraw.Draw(text).rectangle((35,10,165,50),fill=255)
    assert blend_masks(blank,text,0).getbbox() is None
    assert blend_masks(blank,text,.5).getbbox() is not None
    assert blend_masks(blank,text,1).tobytes() == text.tobytes()
    assert blend_masks(text,blank,.5).getbbox() is not None
    assert blend_masks(text,blank,1).getbbox() is None
    assert blend_masks(blank,blank,.5).getbbox() is None
