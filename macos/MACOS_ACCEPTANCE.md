# macOS acceptance checklist

- [ ] `doctor` finds exactly one visible Codex window and one composer; existing draft text stays intact.
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
- [ ] An ambiguous composer causes a visible refusal and no typing; recognized text appends to an existing draft.
- [ ] Double-tap recording, silence handling, transcript filtering, re-recording,
      and next-whip voice submission all pass.
- [ ] 已配置语音 API 时优先使用 API；未配置时自动使用本地识别。
- [ ] 云端识别失败且本地模型已就绪时能完成本地回退，不重复改变录音状态。
- [ ] 设置页的“原生听写”和“语音输入”互斥，关闭后重启仍保持正确模式。
- [ ] 安装 BlackHole 2ch 并在 Codex 中选为麦克风后，双敲手柄的声音进入 Codex 听写，静音后下一鞭发送文字；Mac 内置麦克风不参与。
- [ ] 缺少 BlackHole 时双敲提示安装，而 Codex 听写不误启动。
- [ ] Relaunch retains settings, calibration, messages, and overlay position.
