# 后续待办（用户确认记录，尚未实施）

1. 主界面语音功能的 UI 与动画还未按新版风格调整；需要覆盖录音、识别中、识别为空、候选文字及重新录音状态。
2. 设置页面仍有大量 UI 和功能交互需要逐项调整；2.2.22 的重排不代表产品设计已完成。
3. macOS 支持需要重新建立目标机验收与发布流程，不再把 Windows 测试/打包检查作为 Mac 可用的证据。

## 本次只读检查与研究

- 当前桌面版本 2.2.22；macos/build-macos.sh 仍生成 2.0.1 名称 DMG，仅接受 arm64，默认 ad-hoc 签名，没有 notarytool 公证步骤。
- macos/MACOS_ACCEPTANCE.md 仍包含旧固件 0.5.1 与已移除红眼，必须更新。
- 已存在 macos_api.py、macOS Accessibility sender 与 PyObjC/CoreBluetooth 依赖，不能简单认定全部需要重写。
- 未取得当前目标 Mac 型号、macOS 版本、故障日志，因此不把上述风险直接定性为历史故障原因。
- 建议：先在目标 Apple Silicon Mac 直接调试现有版本，再验证完整 .app（包含 Python/Tk、BLE、语音二进制及资源），正式分发使用 Developer ID 签名、Hardened Runtime、公证；固定 bundle ID；依次验证连接、识别/方向、语音、发送安全、浮层和窗口效果。
- 云 Mac 构建可自动化打包，但不能替代真实手柄、用户权限及桌面窗口行为验收。不要通过关闭 Gatekeeper/SIP 等方式解决分发问题。

## 官方参考

- https://github.com/pyinstaller/pyinstaller/blob/develop/README.rst
- https://www.pyinstaller.org/en/stable/usage.html
- https://developer.apple.com/documentation/security/notarizing-macos-software-before-distribution
- https://bleak.readthedocs.io/en/develop/backends/macos.html
- https://doc.qt.io/qtforpython-6/overviews/qtdoc-macos.html
