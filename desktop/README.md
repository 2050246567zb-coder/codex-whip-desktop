# Codex Whip desktop companion

Windows/macOS companion for the XIAO nRF52840 Sense firmware in this repository.

The default behavior is deliberately `dry-run`: BLE events are parsed and the
selected prompt is printed, but no window receives keyboard input. Pass
`--live` only after `doctor` reports exactly one Codex desktop window.

For ordinary use, launch `codex-whip-gui`. The GUI starts BLE listening
automatically and remains in safe-listening mode until the user explicitly
checks the live-send arm control. A packaged build can be created from the
repository root with `scripts\build-desktop.ps1`.

## Current product branch: desktop 2.2.60 / shared firmware 0.7.4

The 2.2.60 Windows/macOS product uses the XIAO nRF52840 Sense LSM6DS3TR-C hardware
Shock/Quiet/Duration state machine for double taps. The desktop exposes only a
minimum-impact setting, maps it to `TAP_THS`, and accepts two impacts anywhere
inside the fixed one-second hardware window. The old raw-motion custom detector
is not used as a fallback. See the repository-level `DEVELOPMENT.md` and
`RELEASE-2.2.60.md` for current build instructions and validation evidence.

New installations are seeded once with the public factory calibration in
`assets/factory-calibration`. Existing user profiles always win, so later
recalibration is never overwritten. API keys, recordings, messages and
device-specific sensor bias are not part of the factory profile.

### Historical: desktop 2.2.6 interval control (superseded)

2.2.6 adds a 0.20–1.00 second double-tap maximum-interval slider to Calibration.
Steps are 0.05 seconds with five labeled ticks (0.2, 0.4, 0.6, 0.8, 1.0).
The selection applies to both capture rounds, free testing and saved live
recognition. Adjusting it clears incomplete pulse state and test counters, not
the captured strength samples; cancellation still leaves saved settings intact.
The 150 ms minimum separation remains a rebound guard. Interval is measured
peak-to-peak, including the selected endpoint; an already-started second pulse
is not prematurely discarded while waiting for its release.

### Slider design convention

New settings sliders should use `tick_slider.TickSlider`: black rounded filled
track, light-gray remaining track, white circular thumb, and tick labels below.
It supports immediate drag feedback, track clicking, arrows, Home/End and focus
indication. The supplied HTML reference is reimplemented natively for Tk, not
embedded in a web view. Existing unrelated sliders are not migrated in this change.

### 2.2.5 double-tap calibration

2.2.5 adds Settings > Calibration with one light double-tap, one heavier
double-tap, then a free test page. Samples are captured automatically from raw
IMU data using permissive defaults, not the previous learned threshold/template.
Test mode and acquisition suppress recording and all prompt-sending strike
sources. Save explicitly commits one voice settings file; cancel, restart, page
exit or settings close retain the old saved settings. BLE interruption retains
the draft but clears incomplete pulse state.

Saved force calibration uses gravity-subtracted three-axis impulse magnitude
and a short-pulse/double-tap state machine, with quiet time between impacts,
continuous-turn rejection, cooldown, and sample gap validation. The light pair
sets a lower threshold with margin; the heavier pair documents tested strength
and broadens the interval envelope. Heavy strength is NOT an upper cutoff.
This is measured acceleration at the sensor, not mechanical force in newtons.
Old trajectory files stay on disk but are bypassed only after force calibration
is saved. Existing voice enable state and unrelated settings are retained.
The 2.2.4 pointing/auto-neutral algorithm and whip recognizer are unchanged.

### 2.2.4 pointing calibration

2.2.4 upgrades tolerant three-second idle centering to grip-frame calibration.
Point the handle at the screen and hold approximately steady for three seconds.
The final half-second mean acceleration defines up; the saved PCB-to-handle
mounting profile defines right. The current fused attitude becomes neutral.
Only screen mapping changes: fusion attitude, gyro bias, mounting file, raw
samples, whip/tap thresholds and rope physics are not reset or rewritten.
Near-vertical grips silently retain the previous axes and center the display.
Direction onboarding still disables automatic centering. Initial mounting
calibration remains necessary for arbitrary sensor installation orientations.
This desktop change works with the existing raw-motion firmware; it does not
implement the planned ESP32-C3/MPU6050 hardware port.

### Historical: 2.1.7 recentering behavior

2.1.7 fixes the 2.1.6 recentering regression. It still recenters after 3 seconds of tolerant idle:
filtered gyro <=12 dps, acceleration magnitude within 0.20g of 1g, and total
orientation departure <=3 degrees. Cumulative angle prevents continuous slow
turns from being mistaken for rest. Gaps over 120ms reset the idle countdown.
Automatic recentering now changes only an unclipped display-angle offset, not
the calibrated quaternion frame or its axes. Manual calibration clears that
offset and remains the explicit way to redefine the frame. The visual return
transforms current and previous rope positions together, preserving velocity
without adding a synthetic pull; gravity and damping continue normally. The
return transport expires even if fresh real motion arrives. Gyro bias, learned
profiles, strike thresholds and raw detector input are unchanged. A 30-second
gentle-motion BLE capture contained no board WHIP events; a true detector
misfire has not been reproduced. Mouse-held mode is not taken over, and the
direction wizard disables automatic recentering.

Manual direction onboarding now has no capture deadline or terminal stillness
gate. Each gesture starts from its own local pose. Screen-right sign is learned
from the demonstration rather than assumed positive; mixed-axis motion and
partial corrections are allowed if the net direction remains observable.
Stream loss preserves completed steps but invalidates an in-flight capture and
requires recentering before saving. Schema-1 mounting files remain readable;
new schema-2 files include the learned yaw sign. Only explicit review/save
replaces a mounting profile. Recent capture/error traces contain at most 6000
IMU frames in two local files, with no audio or messages.

2.1.1 fixes mouse strikes by animating the entire live cord and putting its tip
at the frozen click coordinate, including an explicit contact frame when the
GUI skips past the 155 ms impact time. The 280 ms stroke returns its final shape
to the physics solver; mouse movement and reduced-motion behavior are retained.
Gravity increases from 0.48 to 1.60 per 60 Hz step (before the existing weight
factor), while inertia retention drops from 0.84 to 0.80. A fixed-step accumulator
replaces variable Verlet step sizes; root-to-tip length projection prevents the
stronger gravity from stretching the rope.

Relative pointing now projects a continuous quaternion rotation vector onto the
calibrated axes instead of ray/Euler angles. This removes the reproduced 90-degree
false twist and 180-degree horizontal flip without increasing sensitivity or
changing the center-third bounds. A 250 ms–3 s sample gap holds the last pose and
skips missing motion; a longer disconnection or reboot rebases the attitude.

Reuses the XIAO nRF52840 Sense. RAW5 carries timed int16 samples (0.1 dps and
0.001 g per LSB); legacy RAW3/RAW4 is still accepted. This improves transport
resolution, not the IMU's intrinsic accuracy. Position is derived from relative
quaternion attitude, never double-integrated linear acceleration. Click
**Calibrate hand-held zero** to rebase the current attitude without estimating
gyro bias from hand movement. Stable gravity correction is optional, not a
required hold. Learned mounting direction and yaw sign define the pointing
frame (legacy PCB-axis fallback before onboarding); verify the installed directions.

Holding an angle preserves the handle location until the 3-second idle timeout.
Missing sensor data for three seconds also retains the visual return fallback;
a shorter gap holds the attitude without
inventing motion. Six-axis yaw is relative and can still drift. The
sensor-controlled grip is kinematic and cannot be dragged by rope inertia; the
rope continues settling while the grip is stationary. Long render frames are
subdivided to improve stability without changing the appearance.

Firmware accepts wrist-led motion after sustained rotation, angular travel,
shape checks and a braking/quiet tail, without requiring a large acceleration
peak. Personalized DTW retains saved templates, now with quiet-tail and minimum
rotation-process checks. Existing thresholds, messages, voice settings and
profiles are preserved; physical sensitivity still requires user testing.

The centered one-third movement bounds remain unchanged. Voice recording still
temporarily pauses the motion stream. `SELFTEST` is status-only and does not
trigger Codex messages; run `scripts/verify-motion-v21.py` with other desktop
listeners closed for firmware readback, synthetic detector tests and BLE metrics.

## Earlier changes (historical behavior, superseded where noted above)

Desktop 2.0.1 maps the physical sensor's full horizontal and vertical control
range into the centered one-third of the Codex window on each axis. The handle
can follow motion inside that central rectangle but is clamped at its four
boundaries. Mouse dragging and explicit mouse-strike coordinates are unchanged.

Desktop 1.5.4 turns both pre-eye flashes into thin fluorescent red horizontal
lines. After two synchronized flashes, those exact lines expand vertically over
320 ms into the minimal eyes, which then remain steady for three seconds.

Desktop 1.5.3 changes the red-eye timeline to two seconds of solid black,
followed by one or two randomly selected 140 ms flashes, then three seconds of
steady eyes. Schema 1–2 visual settings migrate to the new 2000/3000 ms
defaults; both durations remain adjustable in the settings page.

Desktop 1.5.2 replaces the detailed red-eye artwork with an ultra-minimal pair
of flat crimson geometric eyes on black. The existing slit-open, hold, and
close timing remains unchanged.

Desktop 1.5.1 removes the off-white torn-paper rim and loose fibers from PCB
damage. The rendered damage layer now contains only the fixed PCB backplane
inside the merged tear mask; every pixel outside that mask remains transparent.

Desktop 1.5.0 adds an optional system-wide hotkey (default
`Ctrl+Alt+Shift+X`) that covers the current Codex window with black, waits one
second, then opens a pair of restrained cartoon red eyes from a narrow slit.
Press the hotkey again or `Esc` to dismiss it. Enablement, hotkey, blackout
delay, and eye duration are persisted in the `Visual effects` settings page.
The overlay is click-through and does not activate or resize Codex.

Desktop 1.4.1 adds a persisted `Visual effects` setting for PCB wound
frequency. A value of `N` opens a new tear on the Nth, 2Nth, and 3Nth rendered
strike, from either the sensor or mouse path; `1` preserves the previous
every-strike behavior. The supported range is 1–100. Strikes that do not open a
new wound still postpone healing of existing wounds. The setting is saved to
`%LOCALAPPDATA%\CodexWhip\visual-settings.json` and applies immediately.

Desktop 1.4.0 adds a main-window `Calibrate hand-held zero` action. It accepts
only a recent raw IMU sample, treats the latest gravity-derived roll and pitch
as the new neutral orientation, clears integrated rotation and transient
translation, and immediately maps that zero pose to the Codex-window center.
Calibration also persists the center as the neutral overlay position. Without a
manually saved override, the default parked handle position is now the window
center rather than its upper-right area. This is a relative pose calibration,
not absolute 3D position tracking or a hardware gyroscope bias calibration.

Desktop 1.3.9 derives each tear's long axis from the physical handle's recent
screen-space movement trajectory, independently of the fixed visual strike
path. It accumulates the trajectory covariance and uses its principal axis, so
late braking or recoil samples do not easily reverse the result. Mouse strikes
use the mouse's recent motion direction. The displayed tip still determines the
impact coordinate, and nearby directional tears still merge through the
existing window-level union logic.

Desktop 1.3.8 inverts the sensor Y axis at the final screen-mapping boundary so
raising/lowering the physical handle matches upward/downward Windows movement.

Desktop 1.3.7 maps the fixed `±190 × ±130 px` IMU pose domain onto the
complete padded Codex-window handle area instead of adding it as a small pixel
offset around the parked top-right position. Automatic strikes start from the
current live handle/rope pose, rotate with its sensor angle, and retarget an
out-of-bounds tip back inside Codex; the exact displayed tip is also the damage
coordinate. The dark synthetic drop shadow outside PCB tears has been removed,
while the light torn-paper fibers remain.

Desktop 1.3.6 lowers the cord's apparent inertia without extending its slow
settling tail. Forty percent of each physical handle translation is carried into
the current and previous rope positions, so the cord follows the handle instead
of storing the complete relative displacement as a delayed swing. This does not
inject new Verlet velocity, and the existing 0.84 damping curve is retained.

Desktop 1.3.5 adds a matching Silero VAD stage before Whisper decoding and asks
whisper.cpp to suppress non-speech tokens. Silence, music tags, speaker labels,
and a small exact list of common silence hallucinations now produce an empty
candidate and are never sent. Normal commands containing words such as
"follow" are preserved. A confirmed second double tap starts a fresh recording,
clears the previous candidate, and leaves the candidate empty if no real speech
is found.

Desktop 1.3.4 expands the relative IMU mapping to 190 px horizontally, 130 px
vertically, and 42 degrees of handle rotation. It also reduces retained rope
inertia and keeps the Verlet solver running after the handle crosses the
stillness gate, so the cord settles under damping and gravity instead of either
drifting or freezing on one frame. Automatic strikes now begin from the live
rope pose and rotate every animation phase around the current sensor-driven
handle anchor; the dynamic strike tip determines the impact and damage point.
Mouse strikes retain their exact requested screen coordinate. These values are
relative visual controls only: the six-axis IMU has no external reference and
does not provide absolute room-scale position tracking.

Desktop 1.3.3 and firmware 0.5.1 provide an optional voice module. With the module
enabled, two short, settled handle impacts detected from the existing RAW4 IMU
stream request a 16 kHz PDM recording. Firmware pauses motion events, sends
independently decodable IMA ADPCM blocks with sequence numbers, and ends on
silence or the configured time limit. The desktop rejects incomplete sessions,
transcribes complete audio locally with the bundled whisper.cpp 1.8.1 runtime,
and retains editable text as a one-shot prompt until Codex reports a successful
send. The multilingual small-q5_1 model is downloaded once, SHA-256 checked, and
stored under an ASCII ProgramData path for compatibility with Chinese usernames.
The module is off by default and exposes its switch, tap thresholds, timing, and
manual five-sample calibration in Settings > Voice Input. Calibration caches the
latest raw IMU window without applying the old tap threshold: start learning,
perform one double tap, then click Record Last Double Tap. Only that click counts
a sample, and the process repeats until five valid samples have been stored. Those
five complete gyro/acceleration/jerk trajectories are persisted as a V2 double-tap
profile. Runtime detection uses broad two-peak candidate gates followed by the same
DTW trajectory feature pipeline used by whip V3; the candidate fields remain
editable, but no single peak threshold is the final decision.

Firmware 0.5.1 hardens voice transport against intermittent Windows BLE
backpressure. It requests MTU 247, sizes each ADPCM record to fit one 244-byte
notification when negotiated, extends the per-notification retry window from
150 to 650 ms, and buffers about 800 ms of microphone PCM during a stall. Desktop
1.3.3 drains queued notification bursts before allocating new wait tasks. A
single short TX queue stall therefore no longer aborts the complete recording.

Desktop 1.2.3 removes the previous five-wound display cap. Every strike remains
visible during the shared three-second quiet period; completed wounds are still
discarded after healing so inactive image data does not accumulate permanently.

Desktop 1.2.2 narrows the torn-paper boundary around PCB wounds: the generated
rim is constrained to at most 8 pixels in the reference mask (previously about
16), and loose fibers are now 2-7 pixels long at one-pixel width. PCB exposure,
nearby-wound merging, the three-second quiet period, and healing timing are
unchanged.

Desktop 1.2.1 freezes every rope point as soon as the IMU crosses the existing
stillness gate. Sub-pixel filter updates are ignored during the three-second hold;
new real motion interrupts the freeze, while the intentional three-second recenter
translates and rotates the frozen shape without restarting rope integration. Damage
now remains fully open for three seconds after the latest strike anywhere, then
closes over 0.85 seconds. Any new strike restarts that global quiet timer. Screen
motion also requires two consecutive above-threshold IMU frames, so one isolated
sensor spike cannot wake the rope.

Desktop 1.2.0 made the entire whip near-black, replaced the decorated grip with
a thin black rod, lengthened the cord by exactly 50%, and raised cord gravity by
20%. Stronger damping plus a sub-pixel velocity sleep threshold prevents a held
cord from fluttering indefinitely. PCB damage now uses one centered overscanned
backplane fixed to Codex-window coordinates. Nearby tear masks are unioned before
the torn-paper edge is rendered, so repeated strikes expand one opening without
stacking white paper rims. A nearby new strike restarts the three-second healing
clock for the complete merged wound.

Desktop 1.1.0 restored the cartoon whip and adopted the useful motion principles
from VibeWhip/badclaude: a tapered constrained chain, high rigidity near the
handle, low rigidity at the tip, bend limits, and Catmull-Rom smoothing. Mouse
and sensor motion now drive the same physical cord. Sensor noise below 12 dps
and 0.035 g dynamic acceleration counts as quiet; recentering begins only after
three continuous quiet seconds. PCB damage now uses a layered torn-paper rim
with small fibers instead of a metallic wound edge.

Desktop 1.0.0 introduced raw six-axis screen-pose routing and the photorealistic
generated PCB reveal. Relative pose remains intentionally distinct from absolute
position tracking.

Desktop 0.9.0 added live message cards with drag ordering and sequential/random
delivery. Message settings persist in
`%LOCALAPPDATA%\CodexWhip\message-profile.json`.

Desktop 0.8.0 adds a personalized V3 classifier. Firmware 0.4.0 streams a
compact six-axis trace; the settings window records the most recent motion on
explicit click, learns representative positive/negative DTW templates, and
exposes a persisted match-tolerance control. The strike sound now layers a
licensed bullwhip recording with a public-domain impact recording; attribution
is in `assets/THIRD_PARTY_NOTICES.md`.

Desktop 0.8.1 fixes V3 settings-window construction and adds a regression test
that verifies the learning, recording, tolerance, and threshold controls are
all created before the window is shown.

Desktop 0.7.1 adds an interactive mouse-whip layer. Click the visible handle to
pick it up, left-click over Codex to strike, and right-click or press Escape to
put it back. Dragging the handle by at least six pixels moves and persists its
parking position. While picked up, the capture layer consumes mouse clicks so
they do not operate the underlying Codex UI; prompt delivery still respects the
existing safe/live arm switch. Mouse-whip mode remains active outside Codex and
captures the complete virtual desktop until right-click or Escape. Overlay and
impact placement use absolute Win32 coordinates, including negative multi-monitor
coordinates, and the parked whip follows Codex window movement every 50 ms.
