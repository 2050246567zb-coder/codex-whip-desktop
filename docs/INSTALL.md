# Codex Whip 自动安装说明（给用户及 Codex）

安装页面：https://2050246567zb-coder.github.io/codex-whip-desktop/

请先读完本说明，再执行安装。用户把安装页面中的提示词发给 Codex，代表授权下载、校验、安装及启动对应平台的软件；不代表授权发送测试消息、刷固件、重置已有设置或修改系统安全策略。

## 先确认平台，再选择版本

- macOS：先确认 Apple Silicon / Intel。目前发布的 Mac 包仅支持 Apple Silicon；若终端运行在 Rosetta 下，可用 `sysctl -n hw.optional.arm64` 辅助判断。Intel 不要安装 Apple Silicon 包。
- Windows：选择 Windows EXE。
- 从 https://github.com/2050246567zb-coder/codex-whip-desktop/releases 选择本平台最新的正式发布版。不要直接使用 `/releases/latest` 或只按版本号大小选择：它可能指向另一个平台。
- 不要下载 GitHub 自动生成的 Source code 作为软件安装包。使用同版 SHA256SUMS 文件核对下载包；校验失败就停止。
- 已有软件正在运行时，先提示用户退出，避免争抢蓝牙。保留用户设置、录音及日志。

## macOS：完成安装，不只打开 DMG

推荐安装至 `/Applications/CodexWhip.app`，没有写入权限且该位置没有旧版时可安装到 `~/Applications/CodexWhip.app`。桌面可放快捷方式，不需要把应用本体安装在桌面。

必须先将应用完整复制到固定安装位置，再从该位置启动。不要直接从 DMG、临时解压目录运行后就宣布安装完成。

### 自动安装脚本

脚本地址：https://2050246567zb-coder.github.io/codex-whip-desktop/install-macos.sh

Codex 应先下载并检查脚本内容，再执行；不要直接 `curl | bash`。脚本不需要 Homebrew 或额外安装 Python，使用 macOS 自带的 Bash、JavaScript for Automation 和系统工具。

示例（在同一个终端会话中执行）：

```bash
installer_dir="$(mktemp -d)"
curl --fail --location --proto '=https' --proto-redir '=https' \
  'https://2050246567zb-coder.github.io/codex-whip-desktop/install-macos.sh' \
  --output "$installer_dir/install-macos.sh"
# Codex 先读取并检查上面下载的脚本，然后再执行：
bash "$installer_dir/install-macos.sh" --dry-run
bash "$installer_dir/install-macos.sh" --desktop-link
```

`--dry-run` 只检查平台、选择版本并展示安装位置，不下载应用、不安装、不启动。`--desktop-link` 会在桌面空闲位置创建指向已安装软件的快捷方式，不覆盖同名文件。不需要桌面快捷方式时省略此参数。

正式安装会核对包的 SHA-256、应用身份、版本及代码签名完整性，将应用完整复制后再替换旧版；旧版软件本体留有备份。它不覆盖用户设置，不重签名、不移除安全隔离标记、不修改权限数据库或关闭 SIP/Gatekeeper。

如果脚本失败，请报告准确的失败步骤；不要为了继续安装而绕过校验或安全措施。已有 `/Applications` 版本但无更新权限时，不另装第二个副本，由 Codex 引导用户选择管理员确认或手动复制。

### 可以自动打开设置，不能自动授予权限

安装后会从固定位置启动软件，并打开辅助功能设置。用户需亲自开启 `CodexWhip` 的权限，并允许蓝牙访问。

- 列表里没有应用：点击 `+`，按 `Command+Shift+G`，输入实际安装路径，选择 `CodexWhip.app` 添加，再开启。
- 系统要求密码、Touch ID 或安全确认时，由用户亲自确认；Codex 不应索取或代填密码。
- 首次启动若被安全策略阻止，按系统的“隐私与安全性”提示及 [Apple 官方说明](https://support.apple.com/zh-cn/102445) 处理。当前安装包尚未完成 Developer ID 公证，不要把复制文件成功误报成已经正常启动。
- 授权后按需退出并从固定安装位置重新启动，核对软件显示的授权及连接状态。不要只检查 Codex 或终端自身的辅助功能权限，那不代表鞭子软件已授权。
- 不要用 `sudo`、修改 TCC 数据库或关闭系统保护来强制授权；正常个人 Mac 上授权需要用户确认。

脚本本身失败时，Codex 可使用 `hdiutil` 挂载、`ditto` 复制、`codesign` 检查、`open` 启动，或由已有电脑操控工具进行等效文件安装。无论哪种方式，都遵守固定路径、完整性校验和用户确认权限的要求。

## Windows

下载本平台最新版 EXE 和同版 SHA256SUMS。核对后，将 EXE 保存到固定的用户程序目录（例如 `%LOCALAPPDATA%\Programs\CodexWhip`），按用户需要创建桌面快捷方式，再启动。Windows 包为自包含 EXE，不需要另装 Python；内置语音模型的版本首次本地识别不需要额外下载模型。

不要覆盖正在运行的文件；更新前提示用户退出，保留旧版软件及本地配置。遇到 SmartScreen 等安全确认时，提示用户核实发布来源，不关闭系统保护。

## 安装后的验收边界

分别确认：文件已安装、从正确路径启动、系统权限已允许、手柄蓝牙已连接、Codex 主窗口已识别。没有真实设备或用户确认的环节，明确写“尚未验证”。未经用户明确确认，不挥鞭发送测试消息，不刷写固件。
