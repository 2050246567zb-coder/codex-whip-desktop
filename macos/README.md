# Codex Whip 产品版 2.2.64 for macOS

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

从源码运行时，先执行 `bash macos/prepare-speech.sh` 准备 Mac 原生 Whisper（需 CMake 和 Xcode Command Line Tools）。完整打包脚本也会自动执行此步骤。首次启用本地识别时，应用下载并校验语音模型；准备完成后才能双敲录音。

## 验证

```bash
bash macos/verify-macos.sh
```

GitHub Actions 可验证 Apple Silicon 构建、单元测试和合成 UI 冒烟；真实蓝牙、
BLE、当前 Codex AX 元素、屏幕叠层和语音识别仍须在目标 Mac 按
`MACOS_ACCEPTANCE.md` 实测。

## 2.2.64 同步说明

保留 AppKit 透明叠层、右键/Control-click 表盘、主页状态动画和 Mac 原生 Whisper。
CoreBluetooth 设备标识（UUID）用于记忆手柄；按广播信号强度选择附近设备。
共享固件 0.8.1 使用二进制音频、CRC16 和分片确认；桌面仍兼容旧 AUD1 固件。
源码升级不会自动刷写手柄；已安装最新固件的手柄无需重复刷写。
出厂校准 JSON 按仓库 LF 换行校验；已有本机校准不被覆盖。

2.2.64 将双敲门槛限制为 0.5–4.0 g，默认 1.0 g。语音设置升级到 schema 2；旧版语音设置会恢复默认（包括关闭语音开关），升级后请重新开启并检查灵敏度。保留 BLE 录音开始、结束和断线诊断日志。
