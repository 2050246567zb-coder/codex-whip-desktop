# 云端语音识别（产品版 2.2.37）

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
| 豆包 API Key | bigmodel | https://openspeech.bytedance.com/api/v3/auc/bigmodel/recognize/flash |
| 豆包 App ID + Token | bigmodel，同一接口，旧版控制台鉴权 | 同上 |

## 豆包语音

使用语音控制台凭据，不是方舟聊天模型 API Key。

- 新版控制台：选择“豆包 · 录音极速识别（API Key）”，填写 API Key。
- 旧版控制台：选择“豆包 · 录音极速识别（App ID + Token）”，填写 App ID 和 Access Token；两项同时填写，全部留空可继续使用已经保存的一组凭据。
- 需开通资源 `volc.bigasr.auc_turbo`。接口、资源 ID、模型和请求参数已预设。
- 新版使用 X-Api-Key；旧版使用 X-Api-App-Key 与 X-Api-Access-Key。
- Base64 WAV 一次请求返回，校验 X-Api-Status-Code，读取 result.text。
- 静音码 20000003 返回空文字，HTTP 200 内的业务失败不会被当作识别成功。
- 旧版双字段凭据作为一个整体保存到系统密钥库，不写入普通配置或录音文件。
- 本次支持 V3 极速识别的新旧鉴权，不宣称兼容历史 V1 submit/query 接口。

官方依据（2026-09-20 核对）：https://www.volcengine.com/docs/6561/1631584?lang=zh

## 录音增益

识别服务卡片增加 1–8 倍连续滑条，默认 2 倍。点击“保存”，下一次录音生效。
1 倍保持原样；其余倍率在整段录音上使用固定增益，依据峰值限制实际增幅，
避免新增削波。不使用持续追随静音的自动增益，不对峰值低于 16 的近零信号放大。
回放缓存与送给本地/云端转写器的 PCM 完全相同，不会重复增幅。

数字增益同时提高人声和底噪，不能恢复已经失真或丢失的信号，也不改变开发板的
录音起止与静音检测门限。若录音在板端提前结束，需要另行核对硬件/固件。

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
