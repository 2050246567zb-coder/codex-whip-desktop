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
- [ ] 已配置语音 API 时优先使用 API；未配置时自动使用本地识别。
- [ ] 云端识别失败且本地模型已就绪时能完成本地回退，不重复改变录音状态。
- [ ] 设置页不显示虚拟麦克风、BlackHole 或 Codex 原生听写入口。
- [ ] Relaunch retains settings, calibration, messages, and overlay position.
