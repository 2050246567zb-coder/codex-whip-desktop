# Animation plans

| # | Title | Severity | Status |
|---|---|---|---|
| 001 | 修正传感手柄映射、绳索惯性与动态命中点 | HIGH | DONE |

## Recommended execution order

1. Execute plan 001 as one unit because the mapping, physics damping, and impact coordinate share the same sensor-to-overlay path.

## Dependencies

- Plan 001 has no external dependency and must not change firmware or BLE protocol.
