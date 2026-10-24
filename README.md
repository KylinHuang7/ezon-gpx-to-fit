# ezon-gpx-to-fit

Convert GPX tracks exported by **EZON sports watches** into Garmin **FIT** activity files — **with heart rate fully preserved**.

[中文说明](#中文说明)

## Why this exists

EZON watches store heart rate in a **custom `<extensions><heartrate>` tag** inside their GPX files. Generic tools — including `gpsbabel` — do not recognize this tag, so a straightforward conversion **silently drops every heart-rate sample**:

| Step | gpsbabel behavior (tested with 1.10.0) |
|---|---|
| Read GPX (original, EZON `<heartrate>`) | ❌ heart rate not recognized |
| Read GPX (pre-processed to standard `gpxtpx:hr`) | ❌ output FIT is byte-identical — HR still missing |
| Read heart rate from FIT | ✅ works |
| Write heart rate into FIT | ❌ HR dropped on re-encode |

This script encodes FIT **directly, using only the Python standard library**, preserving:

- timestamps
- latitude / longitude
- altitude
- **heart rate** (every sample)
- instantaneous speed
- cumulative distance

## Requirements

- Python 3.8+ (standard library only — no third-party packages)
- `gpsbabel` (optional, used only for **verification**, not conversion)

## Usage

```bash
# Convert (writes <input>.fit next to the input file)
python3 scripts/gpx_to_fit.py <input.gpx>

# Convert and verify heart rate round-trip via gpsbabel/TCX (recommended)
python3 scripts/gpx_to_fit.py --verify <input.gpx> [output.fit]

# Only produce a GPX with standard gpxtpx:hr (for direct upload to Strava/Garmin, no FIT)
python3 scripts/gpx_to_fit.py --prepare <input.gpx> [output.gpx]
```

The script prints track point count, time range, HR sample count and mean/max/min, and total distance for quick sanity checks.

## Verification

`--verify` round-trips the generated FIT through `gpsbabel` to TCX and reports heart-rate statistics. Manual equivalent:

```bash
gpsbabel -i garmin_fit -f out.fit -o gtrnctr -F check.tcx
grep -c HeartRateBpm check.tcx
```

Pass criteria: `gpsbabel` reads the FIT **without any error**, and the TCX heart-rate sample count matches the track point count with mean/max consistent with the source GPX.

> ⚠️ Do **not** verify heart rate with `-o gpx` — gpsbabel's GPX writer does not emit heart rate at all, so the count is always 0 regardless of the FIT content. Use TCX (`-o gtrnctr`).

## FIT encoding gotchas

If you modify the encoder, two details will silently break your output (both learned the hard way — see [references/fit-format-gotchas.md](references/fit-format-gotchas.md)):

1. **Definition-message field triple order** on disk is `field number → size → base type` (writing `size → field number → type` causes parsers to misread and report `Message N hasn't been defined`).
2. **FIT CRC is CRC-16/ARC** (reflected polynomial `0xA001`, init `0x0000`, check value `crc16(b'123456789') == 0xBB3D`) — not the `0x1021`/`0x8408` variants that dominate search results. The wrong polynomial makes every parser report the file as corrupt.

## Scope & limits

- The generated FIT contains `file_id` + `record` messages only (no session/lap/device_info). Mainstream platforms recognize it as an activity; if a platform strictly requires a session message, the encoder needs extending.
- Altitude is taken from the GPX `<ele>` as-is; flat-land GPS noise is not smoothed.
- Speed is computed from consecutive-point differences; the last point reuses the previous speed.

## License

[MIT](LICENSE)

---

<a id="中文说明"></a>
# 中文说明

把宜准（EZON）运动手表导出的 GPX 轨迹转换为 Garmin FIT 活动文件，**完整保留心率**。

## 背景

EZON 的 GPX 把心率存在**自定义的 `<extensions><heartrate>` 标签**里。gpsbabel 等通用工具不认识这个标签，直接转换会把心率**全部静默丢掉**。本脚本只用 Python 标准库直接编码 FIT，完整保留：时间戳、经纬度、海拔、**心率（每个采样点）**、瞬时速度、累计距离。

## 使用

```bash
# 转换（默认在源文件同目录输出同名 .fit）
python3 scripts/gpx_to_fit.py <input.gpx>

# 转换并自动校验心率（推荐；需要系统装有 gpsbabel）
python3 scripts/gpx_to_fit.py --verify <input.gpx> [output.fit]

# 只产出带标准 gpxtpx:hr 心率的 GPX（供 Strava/Garmin 直接上传，不生成 FIT）
python3 scripts/gpx_to_fit.py --prepare <input.gpx> [output.gpx]
```

脚本会打印轨迹点数、时间范围、心率点数与均值/峰/谷、总距离，便于核对。

## 依赖

- Python 3.8+（纯标准库，无第三方依赖）
- `gpsbabel`（可选，仅用于转换后的**校验**，不参与转换）

## 校验

`--verify` 会把生成的 FIT 经 gpsbabel 转成 TCX 并报告心率统计。手动等价命令：

```bash
gpsbabel -i garmin_fit -f out.fit -o gtrnctr -F check.tcx
grep -c HeartRateBpm check.tcx
```

判定标准：gpsbabel 读取**无任何报错**，且 TCX 中心率点数等于轨迹点数、均值/峰值与源 GPX 一致。

> ⚠️ **不要**用 `-o gpx` 反查心率——gpsbabel 的 GPX 输出本身不写心率，无论 FIT 里有没有都会显示 0。必须用 TCX（`-o gtrnctr`）。

## 已知边界

- 生成的 FIT 只含 `file_id` 与 `record` 两类消息（无 session/lap/device_info）。主流平台能识别为一次活动；若某平台强制要求 session 消息，需扩展编码器。
- 海拔直接沿用 GPX 的 `<ele>`，平地 GPS 噪点不做平滑。
- 速度由相邻点差分计算，末点沿用上一帧速度。

## 许可

[MIT](LICENSE)
