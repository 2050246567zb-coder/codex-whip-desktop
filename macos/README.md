# Codex Whip 产品版 2.2.39 for macOS

macOS 与 Windows 共用动作识别、校准、设置、界面动画、鞭绳物理、伤口、消息和
语音状态机；窗口控制使用 macOS Accessibility/AppKit/Quartz，BLE 使用
CoreBluetooth。平台实现可以不同，但用户可见功能与配置格式保持一致。

## 构建（Apple Silicon）

```bash
brew install python@3.12 python-tk@3.12 cmake
export CODEX_WHIP_PYTHON="$(brew --prefix python@3.12)/bin/python3.12"
bash macos/build-macos.sh
```

产物在 `macos/dist/`。未配置开发者证书时是 ad-hoc 签名测试包；它通过结构和
签名自检，不等于已公证发行。

首次使用需在“系统设置 > 隐私与安全性”授予蓝牙和辅助功能权限。辅助功能权限
用于查找唯一的 Codex 窗口、附着鞭子层、启动 Codex 听写和发送已确认的草稿。

## 两种语音输入

- **文字识别**：保持原有本地 Whisper、豆包和其它预置云端转写，再由下一鞭发送。
- **Codex 原生听写**：安装 [BlackHole 2ch](https://github.com/ExistentialAudio/BlackHole)，
  在设置 > 输入 > 语音输入中选择该模式。双敲后，产品版把手柄的 16 kHz 音频
  实时转换成 48 kHz 并送入 BlackHole，同时通过 macOS Accessibility 启停
  Codex 的听写按钮；下一鞭只提交 Codex 已生成的草稿，不再重复插入文字。
  使用前还需在 Codex 或 macOS 输入设置中把 `BlackHole 2ch` 选为麦克风输入。

程序只接受明确命名为 `BlackHole` 或 `Codex Whip` 的输出端点，找不到时会拒绝
启动，不会回退到扬声器。BlackHole 的安装与许可独立于本项目，安装包不内置驱动。

## 验证

```bash
bash macos/verify-macos.sh
```

GitHub Actions 可验证 Apple Silicon 构建、单元测试和合成 UI 冒烟；真实蓝牙、
BlackHole、当前 Codex AX 元素、屏幕叠层和听写仍须在目标 Mac 按
`MACOS_ACCEPTANCE.md` 实测。
