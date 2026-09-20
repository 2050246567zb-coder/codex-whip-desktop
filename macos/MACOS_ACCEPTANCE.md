# macOS acceptance checklist

- [ ] `doctor` finds exactly one visible Codex window and one empty composer.
- [ ] BLE connects to `CodexWhip`, reports the current supported firmware, and survives reconnect.
- [ ] The migrated whip profile and double-tap trajectory work without relearning.
- [ ] All migrated messages appear in their original order and random mode.
- [ ] Physical motion stays inside the centered one-third on both axes; up/down are not inverted.
- [ ] Physical and mouse strikes land at the rendered whip-tip position.
- [ ] The black handle and 1.5x cord move with low inertia and settle naturally.
- [ ] Sound contains the sharp crack and body impact without UI blocking.
- [ ] Damage reveals the fixed PCB, nearby tears merge, and healing starts only
      after three seconds without another strike.
- [ ] The overlay follows, hides, and restores with the Codex window.
- [ ] Left click picks up/strikes, drag moves, and right click releases the whip.
- [ ] A normal Codex window shakes and returns to its exact original position.
- [ ] Full-screen Codex is not moved or resized.
- [ ] A non-empty or ambiguous composer causes a visible refusal and no typing.
- [ ] Double-tap recording, silence handling, transcript filtering, re-recording,
      and next-whip voice submission all pass.
- [ ] “文字识别”和“Codex 原生听写”互斥切换，保存后下一次双敲立即使用新模式。
- [ ] BlackHole 2ch 存在时，双敲启停 Codex 听写，声音不会从扬声器播放；
      找不到已知虚拟设备时明确拒绝且不回退。
- [ ] 原生听写结束后，下一鞭仅提交 Codex 输入框现有草稿；空草稿不发送。
- [ ] Relaunch retains settings, calibration, messages, and overlay position.
