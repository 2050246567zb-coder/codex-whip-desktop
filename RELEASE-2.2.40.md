# 产品版 2.2.40 — 设置内安装虚拟音频驱动

- “设置 > 输入 > 语音输入 > Codex 原生听写”新增“安装音频驱动”和“重新检测”。
- Windows 下载固定的 Virtual Audio Driver 官方签名版，同时校验 SHA-256、压缩包结构与
  Authenticode 签名，通过系统 `pnputil` 完成安装。
- macOS 检测到 Homebrew 时，在可见终端中启动 BlackHole 2ch 官方 cask 安装；没有 Homebrew
  时打开 BlackHole 官方安装页。
- 安装仍保留 Windows 管理员确认或 macOS 系统安装确认，不静默跳过操作系统安全提示。
- 安装启动后自动重试检测，也可由用户手动点击“重新检测”确认设备是否已出现。

云端 macOS 构建可验证安装启动代码与 UI，但系统驱动真正装载、BLE 手柄和 Codex 听写链路仍需在
目标 Mac 上实测。
