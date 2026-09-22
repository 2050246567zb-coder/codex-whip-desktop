# Codex Whip 产品版 2.2.58 for macOS

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
用于查找唯一的 Codex 窗口、附着鞭子层和发送已确认的文字。

首次启动会导入公开的出厂校准，已有的本机校准不会被覆盖。出厂校准不包含
API Key、录音、消息、设备地址或单块开发板专属的陀螺仪零偏。

## 语音输入

双敲后开始录音，软件优先使用已配置的语音 API，否则使用本地 Whisper。
识别文字保留到下一次挥鞭发送。产品界面不再包含 BlackHole 或 Codex 原生听写入口。

## 验证

```bash
bash macos/verify-macos.sh
```

GitHub Actions 可验证 Apple Silicon 构建、单元测试和合成 UI 冒烟；真实蓝牙、
BLE、当前 Codex AX 元素、屏幕叠层和语音识别仍须在目标 Mac 按
`MACOS_ACCEPTANCE.md` 实测。
