"""Manual isolated presentation preview: no BLE, target discovery or sending."""
import tkinter as tk
import time
from types import SimpleNamespace
from codex_whip.effects import CodexWhipEffects,WhipPose
from codex_whip.overlay_presentation import OverlayPresentation

root=tk.Tk()
root.title('Whip presentation preview - isolated')
root.geometry('850x760')
toolbar=tk.Frame(root)
toolbar.pack()
canvas=tk.Canvas(root,width=850,height=710,bg='#F5F5F7',highlightthickness=0)
canvas.pack()
raw=CodexWhipEffects.IDLE
def move(p): return p[0]-180,p[1]-440
pose=WhipPose(move(raw.handle_start),move(raw.handle_end),tuple(map(move,raw.cord)))
effects=SimpleNamespace(window=root,canvas=canvas,IDLE=pose,_preview_pose=pose,_visual_origin=(0,0))
p=OverlayPresentation(effects)
def state(mode,title,subtitle='',deadline=None):
    p.update(mode=mode,title=title,subtitle=subtitle,deadline=deadline,clock_enabled=mode=='whip' and not subtitle,
             reduce_motion=False,level=.5)
for name,callback in [('Whip',lambda:state('whip','just beat it')),
                      ('Clock',p.toggle_clock),('Record',lambda:state('recording','recording')),
                      ('Recognize',lambda:state('recognizing','recognizing voice')),
                      ('Text',lambda:state('whip','继续完成当前任务','beat it, then send',time.monotonic()+10))]:
    tk.Button(toolbar,text=name,command=callback).pack(side='left')
canvas.bind('<Button-3>',lambda e:p.toggle_clock())
def tick():
    p.render()
    root.after(16,tick)
state('whip','just beat it')
root.after(50,tick)
def close():
    p.close()
    root.destroy()
root.protocol('WM_DELETE_WINDOW',close)
root.mainloop()
