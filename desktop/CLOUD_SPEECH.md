# 云端语音识别（2.2.28）

设置 → 输入 → 识别服务。默认本地识别，不上传录音。
选择预设，填 API Key，点击保存并确认上传提示。密钥由 Windows 凭据管理器或 macOS 钥匙串保存；普通配置文件、日志和迁移数据不包含密钥。

## 预设与官方接口

| 预设 | 模型 | 接口 |
| --- | --- | --- |
| OpenAI Mini | gpt-4o-mini-transcribe | https://api.openai.com/v1/audio/transcriptions |
| OpenAI | gpt-4o-transcribe | 同上 |
| 百炼北京 | qwen3-asr-flash | https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions |
| 百炼新加坡 | qwen3-asr-flash | https://dashscope-intl.aliyuncs.com/compatible-mode/v1/chat/completions |
| 硅基流动 | FunAudioLLM/SenseVoiceSmall | https://api.siliconflow.cn/v1/audio/transcriptions |

北京与新加坡密钥按地区分别存储。百炼文档推荐工作空间专用域名，但明确说明上述旧域名仍可用，因此采用无需用户另填 Workspace ID 的预设。

核对日期：2026-09-18。参考：
- https://platform.openai.com/docs/guides/speech-to-text
- https://help.aliyun.com/en/model-studio/qwen-asr-api-reference
- https://docs.siliconflow.cn/docs/api/audio-transcriptions-post

录音为 16kHz、单声道 PCM，封装 WAV 后上传。OpenAI/硅基流动使用 multipart，百炼使用 Base64 音频。只做转写，不追加聊天润色。延续现有转写清理规则，数字静音在本地拦截；不能保证所有环境噪声均不会产生幻觉。

一次录音固定使用开始时选择的服务，设置更改从下一次录音生效。超时为 30 秒；无自动重试，无跨服务回退。认证、限流、网络失败提供简短提示，不显示服务原始错误正文（避免泄漏密钥）。拒绝 HTTP 重定向。

## 验证边界

请求格式、响应解析、错误脱敏、静音拦截、离线分流、设置切换、密钥遮罩和上传确认均有隔离测试。Windows 系统凭据已用专用随机测试项完成写入/读回/清除验证。
未使用真实 API Key 调用收费接口；用户账号额度、地区可用性、识别质量与延迟需要实际验证。macOS 凭据和新版本安装包尚未实机验证。
