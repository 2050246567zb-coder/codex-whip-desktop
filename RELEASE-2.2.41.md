# 产品版 2.2.41 — 修复 Windows 虚拟音频安装

- 修复 2.2.40 中点击安装后只有窗口一闪而过、不产生虚拟音频端点的问题。
- Windows 改用 VB-Audio 官方 VB-CABLE Pack 45 图形安装器，支持 Windows 11 x64。
- 运行前校验固定包的 SHA-256、压缩包结构、VB-Audio 安装器签名和 Microsoft 驱动目录签名。
- 用户确认管理员权限后会看到持续显示的 VB-CABLE 安装界面，点击 `Install Driver` 完成安装。
- 官方要求安装后重启 Windows；重启后软件可检测 `CABLE Input`，并将声音送到 `CABLE Output`。
