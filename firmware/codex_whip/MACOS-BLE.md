# 0.8.2：macOS / Windows 蓝牙与语音流控

适用硬件：**Seeed XIAO nRF52840 Sense（LSM6DS3TR-C）**。不是 ESP32-C3 固件。
基于 GitHub `codex/product-macos` 的 `5bc18e7` / 桌面 2.2.58，保留本地 Mac 原生叠层、鼠标交互和姿态连续显示修复。

## 自动选择方案

固件对 `PING` 回应 `PONG,0.8.2`、`CAPS,HOST_PROFILE,1` 与
`CAPS,VOICE_FLOW,1`。
更新后的桌面端仅在收到这一能力声明时，按运行系统发送一次 `HOST,MACOS`、`HOST,WINDOWS` 或 `HOST,LINUX`。
这是自动握手，不要求用户选择。仅靠 BLE 地址、MTU 或连接节奏无法可靠判断操作系统，固件不作此类猜测。

| 客户端 | 请求的活跃连接间隔 | 每批运动样本 | 标称采样周期 |
|---|---|---|---|
| 更新后的 macOS 客户端 | 15–30 ms | 2–4，自适应 | 10 ms |
| 更新后的 Windows 客户端 | 7.5–15 ms | 4 | 10 ms |
| 更新后的 Linux 客户端 | 15–30 ms | 2–4，自适应 | 10 ms |
| 旧客户端 / 未识别系统 | 15–30 ms | 4 | 10 ms |

实际连接间隔由电脑协商决定，不保证达到请求值。Mac/Linux 按最终间隔调整打包：15 ms 时每批 2 帧，30 ms 时每批 4 帧，避免发包频率超过链路服务能力。Mac/兼容模式使用 latency 0、supervision timeout 4 s；休眠请求 120–135 ms，唤醒恢复当前主机方案。
旧桌面程序仍可连接，但不会声明系统，采用兼容模式。Windows 也需要使用包含此次 `ble_client.py` 改动的桌面程序，才能自动启用 Windows 方案。仓库中的旧发布包不会凭空获得这个能力。
旧固件不会收到新桌面端的 `HOST` / `LINK` 命令，避免未知命令错误。

## 发送与采样分离

旧实现的 `bleUart.write()` 会在通知缓冲区不足时等待信号量（所用 Seeeduino 1.1.13 库超时为 100 ms），另有上层重试；这些等待曾发生在 IMU 主循环内。
新实现使用独立 FreeRTOS TX 任务，主循环只把完整记录入队，不调用阻塞蓝牙写入。

- 可靠消息队列：48 条，每条最多 384 字节；状态、控制应答和语音保持顺序。
- 运动待发槽：只保存最新的完整运动记录，拥堵时替换未开始发送的旧运动批次，序号缺口能反映替换。不会在主机端播放越来越旧的数据。
- 同一条记录的所有分片连续发送，才允许切换到另一条记录；每四条可靠消息给运动记录一次发送机会。
- 可靠队列溢出或分片发送失败时断开连接，清空旧会话。不能把下一条记录接到已经发送一半的 CSV 行上。
- 发送截止时间约 650 ms，底层单次写入可能额外等待。等待只占 TX 任务，不占 IMU 主循环。
- USB 诊断仅在串口缓冲区足够时写入，未读取串口时丢弃诊断文本，不阻塞采样。
- 断开及重新连接会清理旧的运动/语音会话；桌面每次连接重新握手。

## 验证连接

固件回应 `HOST,OK,MACOS,2,REQUESTED` 其中 2 表示最低打包帧数；这条回应表示已选择主机方案并提交连接参数请求，**不代表中心设备已接受**。
`REQUEST_FAILED` 表示参数请求未成功提交，仍可通过兼容连接传输。
新桌面端在握手约两秒后自动请求 `LINK`，将响应记录在 `~/Library/Application Support/CodexWhip/runtime.log`。

`LINK,<host>,<actual_interval_ms>,<mtu>,<max_sample_gap_ms>,<max_write_wait_ms>,<replaced_motion_batches>,<actual_batch_samples>`

采样间隔统计在重新连接时重置；发送最长等待和替换次数为本次固件运行期间累计。需要继续测量时可通过现有命令入口发送 `LINK`。对比实际间隔、采样空档及待发替换次数，可以区分采样阻塞与无线拥堵。

这次修改没有改变姿态解算、安装方向标定、抽打检测算法、语音编解码、BLE UUID 或配对身份。展示层短程预测仍不参与校准/手势识别。真实无线丢包无法由平滑准确还原。

## 在 Mac 构建与刷写

```sh
scripts/setup-firmware-macos.sh
scripts/compile-firmware-macos.sh
scripts/upload-firmware-macos.sh
```

脚本固定 Arduino CLI 1.5.1、Seeeduino:nrf52 1.1.13、Seeed Arduino LSM6DS3 2.0.5。
CLI 下载核验官方 SHA256；第一次安装需要网络。可按环境配置 HTTP(S) 代理，脚本不内置某个代理端口。
刷写脚本只自动选择唯一的匹配 XIAO 板；必要时手工传入已确认的 `/dev/cu.usbmodem…`。
先停止桌面端蓝牙连接、关闭串口监视器；使用支持数据传输的 USB 线。
成功必须看到 `Device programmed.`，随后重新连接桌面端确认 `PONG,0.8.2` 和 `HOST,OK,MACOS`。
无需刷写 bootloader；不要将此包刷到 ESP32 或其他型号。

## 软件验证

```sh
c++ -std=c++11 -Wall -Wextra -Werror \
  -Ifirmware/tests/fakes -Ifirmware/codex_whip \
  firmware/tests/ble_transport_test.cpp -o /tmp/whip-ble-transport-test
/tmp/whip-ble-transport-test
macos/.venv-macos/bin/python -m pytest desktop/tests --ignore=desktop/tests/test_windows_scoring.py
```

C++ 测试用模拟 RTOS/BLE 验证整个发送控制流程：完整记录分片、最新运动替换、公平调度、重连清理、可靠队列满、部分发送失败后断开。它不验证真实芯片的线程调度或无线质量。
Mac 不能执行 Windows 专用测试文件；对应平台需要单独运行。真实手柄的移动、重连、语音和 Windows 对照仍需刷写后验证。

参考：[Apple 连接参数建议](https://developer.apple.com/library/archive/qa/qa1931/_index.html)、[Seeed Bluefruit 1.1.13 源码](https://github.com/Seeed-Studio/Adafruit_nRF52_Arduino/tree/1.1.13/libraries/Bluefruit52Lib/src)。
