# 语音识别（产品版 2.2.60）

产品只保留一条识别链路：**豆包录音文件识别 2.0 优先，本地 Whisper 自动备用**。
旧的豆包极速版、OpenAI、百炼、硅基流动、原生听写和虚拟麦克风入口均不再向产品暴露。

## 云端接口

- 提交：`https://openspeech.bytedance.com/api/v3/auc/bigmodel/submit`
- 查询：`https://openspeech.bytedance.com/api/v3/auc/bigmodel/query`
- 资源 ID：`volc.seedasr.auc`
- 音频：16 kHz、单声道、16-bit PCM，封装为 WAV 后 Base64 提交
- 结果：使用同一个请求 ID 轮询，成功后读取 `result.text`

手柄当前交互是“录完一小段，再开始识别”，因此录音文件识别比流式 WebSocket
更贴合现有流程。数字静音会在本地拦截；云端无密钥、断网、超时、无权限或额度不足时，
同一段录音自动交给本地 Whisper，不要求用户切换选项。

## 安装包内置 API Key

CI 或本地构建时设置环境变量 `CODEX_WHIP_DOUBAO_API_KEY`。构建脚本会把它写入
忽略版本控制的 `desktop/assets/private/doubao-api-key.txt`，然后随应用资源打包。
也可以手动从 `doubao-api-key.example.txt` 创建该文件。源码、日志、Git 提交和示例文件
都不能包含真实 Key。

Windows 与 macOS 使用相同识别代码和相同环境变量。GitHub Actions 需要在两个产品分支
可访问的仓库 Secret 中配置 `CODEX_WHIP_DOUBAO_API_KEY`。

## 识别灵敏度

界面的“识别灵敏度”映射为 1–8 倍固定数字增益。增益依据录音峰值限制实际放大量，
避免新增削波；它会同时放大人声和底噪，不能恢复硬件端已经丢失或失真的信号。

## 验证边界

自动测试覆盖提交/查询请求、资源 ID、请求 ID 复用、WAV 封装、静音拦截、错误脱敏、
云端失败转本地、旧配置迁移和安装包 Key 读取。没有真实 API Key 时不会调用收费接口；
账号权限、额度、真实识别质量和目标 Mac 实机表现仍需带 Key 验收。

官方资料：

- https://www.volcengine.com/docs/6561/1840838?lang=zh
- https://docs.volcengine.com/docs/DataAgentPrivate/Settinguplargemodelcalls?lang=zh
