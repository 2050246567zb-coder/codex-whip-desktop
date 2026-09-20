# 001 — 修正传感手柄映射、绳索惯性与动态命中点

- **Status**: DONE
- **Commit**: unborn repository; `git rev-parse --short HEAD` unavailable
- **Severity**: HIGH
- **Category**: Physicality & origin / Interruptibility
- **Estimated scope**: 7 files, about 180 lines including tests and version notes

## Problem

物理手柄驱动屏幕鞭子时有三个相互关联的问题。

`desktop/src/codex_whip/sensor_pose.py:54-56` 把相对位移限制在很小的范围：

```python
offset_x=_clamp(self._pitch * 1.35 + self._offset_x, -82.0, 82.0),
offset_y=_clamp(self._roll * 1.05 + self._offset_y, -58.0, 58.0),
angle_degrees=_clamp(self._yaw * 0.48 + self._roll * 0.16, -32.0, 32.0),
```

`desktop/src/codex_whip/effects.py:117-118` 保留了过多 Verlet 隐式速度；同时 `effects.py:2105-2110` 在 IMU 进入静止门限后直接冻结整条绳索：

```python
INERTIAL_DAMPING = 0.94
VELOCITY_SLEEP_THRESHOLD = 0.045

pose = self._sensor_physics.step(
    target_handle,
    dt_seconds,
    aim_offset_degrees=sensor.angle_degrees,
    freeze=not sensor.moving,
)
```

这会产生两个相反但都不自然的状态：移动时残余速度持续过久，静止门限一触发又会瞬间停止所有绳段。

`desktop/src/codex_whip/effects.py:1648-1651` 的自动抽打命中点始终来自固定 `STRIKE` 姿态：

```python
self._pending_impact_screen = (
    self._visual_origin[0] + self.STRIKE.cord[-1][0],
    self._visual_origin[1] + self.STRIKE.cord[-1][1],
)
```

因此 IMU 朝向不会改变击打方向；小范围的 origin 偏移也是唯一的位置变化。

事实边界：XIAO nRF52840 Sense 的六轴 IMU 没有外部空间参考，不能可靠提供房间内绝对位置。本次只扩大旋转、倾斜与短时加速度到屏幕的相对映射，不宣称实现 1:1 空间追踪。

## Target

1. `SensorPoseTracker` 使用命名常量，目标值必须为：
   - X 位移范围 `[-190.0, 190.0]` px；Y 位移范围 `[-130.0, 130.0]` px。
   - 角度范围 `[-42.0, 42.0]` degrees。
   - pitch→X 系数 `2.30`；roll→Y 系数 `1.75`；yaw→angle 系数 `0.62`；roll→angle 系数 `0.20`。
   - 移动时 acceleration impulse：X `115.0`，Y `92.0`；velocity retention `0.58`；offset retention `0.88`。
   - 保留 3 秒后回正和现有微动门限，不把亚门限噪声当作移动。
2. `CartoonWhipPhysics.INERTIAL_DAMPING = 0.84`，`VELOCITY_SLEEP_THRESHOLD = 0.065`。传感器驱动路径不再传入 `freeze=True`；锚点静止后继续经过重力、约束和阻尼自然收敛，不能永久摆动，也不能在静止门限触发的那一帧锁死。
3. 新增纯函数，将任意 `WhipPose` 围绕自身 `handle_start` 旋转后平移到指定屏幕手柄锚点。函数必须保持每一段相对长度不变。
4. 自动 `play()`：
   - 起始姿态使用当前 `_current_pose`，避免跳回固定 IDLE。
   - WINDUP/STRIKE/RECOIL/SETTLE/IDLE 都围绕当前手柄锚点，旋转角使用当前 `SensorPose.angle_degrees`。
   - `_pending_impact_screen` 和冲击闪光使用动态 STRIKE 的鞭梢，而不是类常量 `STRIKE.cord[-1]`。
   - 伤口仍由 `_record_damage` 限制在 Codex 窗口内。
   - 鼠标 `play_at()` 保持精确命中鼠标坐标，不受 IMU 动态姿态影响。
5. 桌面版本升级到 `1.3.4`，不改固件和 BLE 协议。

## Repo conventions to follow

- 动态绳索继续使用 `desktop/src/codex_whip/effects.py:109-348` 的 Verlet 点链、约束迭代和 Catmull-Rom 渲染，不添加物理库。
- 屏幕姿态继续由 `SensorPoseTracker` 输出不可变 `SensorPose`，不把 UI/窗口依赖放进跟踪器。
- 快速可逆的抽打阶段继续使用现有 `cubic_bezier_ease_in_out()` 与 Tk `after()` 驱动，所有阶段总长保持 280 ms。
- 每一个行为变更先由 `desktop/tests/test_sensor_pose.py` 或 `desktop/tests/test_effects.py` 中的确定性测试覆盖。

## Steps

1. 在 `desktop/src/codex_whip/sensor_pose.py` 提取上述映射、冲量和衰减常量，替换现有硬编码值；保留静止微动门限与三秒回正逻辑。
2. 在 `desktop/src/codex_whip/effects.py` 把绳段惯性参数改为 `0.84 / 0.065`；传感器 `_sync_position()` 始终运行普通 `step()`，不要传 `freeze=not sensor.moving`。
3. 在 `effects.py` 新增纯姿态旋转/平移函数，并增加当前自动抽打阶段姿态字段。`play()` 捕获当前手柄锚点和 IMU 角度生成动态五阶段姿态；`_animate_whip()` 和 reduced-motion 分支统一使用这些实例姿态与动态鞭梢。
4. `play_at()` 显式恢复固定鼠标阶段姿态，继续令 STRIKE 鞭梢精确落到传入的 screen point。
5. 更新 `desktop/tests/test_sensor_pose.py`：断言同一组 gyro 动作比旧范围明显更大、最大值受 `190/130/42` 限制、静止与噪声测试继续通过。
6. 更新 `desktop/tests/test_effects.py`：断言 `0.84/0.065`；锚点停止后绳梢不是当帧锁死但最终收敛；姿态变换保持锚点和段长；两个不同 IMU 角度生成不同动态击打鞭梢；鼠标命中测试继续通过。
7. 将 `desktop/pyproject.toml` 和 `desktop/src/codex_whip/__init__.py` 升级为 `1.3.4`，并在 `desktop/README.md` 与根 `README.md` 简述相对映射边界和本次修复。

## Boundaries

- Do NOT change firmware, BLE protocol, gesture classification thresholds, voice module, message sending, wound appearance, healing timing, sound, or window shake.
- Do NOT add dependencies or replace Tk/Verlet rendering.
- Do NOT claim absolute 3D position tracking.
- Do NOT modify mouse manual-whip semantics.
- If current code does not expose `_current_pose`, `_sensor_pose_current`, or the five static phase poses described above, STOP and report drift instead of improvising.

## Verification

- **Mechanical**:
  - Run `.\.venv\Scripts\python.exe -m pytest .\desktop\tests -q`; expected: all tests pass.
  - Run `.\scripts\build-desktop.ps1 -Name CodexWhip-1.3.4`; expected: `dist\CodexWhip-1.3.4.exe` exists and PyInstaller reports success.
  - Verify the EXE SHA-256 with `Get-FileHash -Algorithm SHA256`.
- **Feel check**:
  - Connect the physical handle and move/tilt it through the same comfortable range used before. The on-screen handle should cover roughly twice the old span without continuing to slide after the real motion stops.
  - Stop the handle after a fast movement. The rope should visibly settle for several frames under gravity and damping, not freeze instantly and not keep drifting indefinitely.
  - Hold the handle at left/right orientations and trigger a physical whip. Damage/impact must land at visibly different horizontal positions tied to the dynamic strike tip.
  - Leave the handle below the micro-motion gate for three seconds. The screen handle should recenter while the rope keeps solving naturally.
- **Done when**: tests and package build pass; automatic impact uses a dynamic tip; source contains no sensor-path `freeze=not sensor.moving`; real-device visual feel remains explicitly marked for user verification.

## Completion evidence

- Completed as desktop `1.3.4` on 2026-09-02.
- Independent full suite: 105 tests passed.
- Dynamic strike-tip separation at `-24°` versus `+24°`: 539.58 px in the deterministic check.
- PyInstaller package: `dist/CodexWhip-1.3.4.exe`, SHA-256 `A5E8BACB40C8482C70DE42407424F6FF6386FD654F8BA0014B230061D057DA2F`.
- Windows process launch verified responsive; BLE status text and real-device motion feel remain user-visible verification items.
