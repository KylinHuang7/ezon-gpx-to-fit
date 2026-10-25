---
name: ezon-gpx-to-fit
display_name: EZON运动数据转FIT
display_name_en: EZON GPX to FIT Converter
description_zh: 将 EZON 运动手表导出的 GPX 轨迹转换为 Garmin FIT 活动文件，并完整保留心率数据。EZON 把心率存放在自定义的 heartrate 扩展标签中，gpsbabel 等通用工具识别不到，直接转换会让心率全部静默丢失。本 skill 用纯标准库自行编码 FIT，保留时间戳、经纬度、海拔、心率、瞬时速度与累计距离，并可自动调用 gpsbabel 转 TCX 校验心率是否完整写入。适用于把 EZON 手表数据导入 Garmin Connect、Strava，或解析 EZON 心率做训练分析。
description_en: Convert GPX tracks exported from EZON sports watches into Garmin FIT activity files while fully preserving heart rate data. EZON stores heart rate in a custom heartrate extension tag that gpsbabel and other generic tools cannot read, so a plain conversion silently drops every HR value. This skill encodes FIT directly using only the Python standard library, keeping timestamps, coordinates, altitude, heart rate, speed and cumulative distance, and can automatically verify the result by round-tripping through gpsbabel to TCX. Use it when importing EZON watch data into Garmin Connect or Strava, or when parsing EZON heart rate for training analysis.
description: 将 EZON 运动手表导出的 GPX 轨迹转换为 Garmin FIT 活动文件并保留心率数据。EZON 的 GPX 把心率存放在自定义的 heartrate 扩展标签中，gpsbabel 等通用工具识别不到，直接转换会导致心率全部丢失。当需要把 EZON 手表 GPX 转成 FIT、导入 Garmin Connect 或 Strava、解析 EZON 心率做训练分析，或发现 gpsbabel 转换后心率丢失时，应使用本 skill。
version: 1.0.0
category: 生活服务
agent_created: true
---

# EZON GPX → FIT 转换（保留心率）

## Overview

把 EZON 运动手表导出的 GPX 轨迹转换为 Garmin FIT 活动文件，并**完整保留心率**，
同时保留时间戳、经纬度、海拔、瞬时速度与累计距离。

EZON 的 GPX 使用自定义 `<extensions><heartrate>` 标签存心率，通用工具（含 gpsbabel）
识别不到，转换后心率会静默丢失——这是本 skill 存在的原因。

## 何时使用

- 要求把 EZON（GPX creator 为 `http://www.ezon.cn`）的 GPX 转成 FIT
- 转换后要导入 Garmin Connect / Strava / 训练分析平台
- 用户反馈"用 gpsbabel 或其他工具转完，心率没了"
- 需要解析 EZON GPX 的心率做训练分析

如果是**标准** GPX（心率已在 `gpxtpx:hr` 里）转 FIT 且不在乎心率，
直接用 gpsbabel 即可，无需本 skill。

## 快速使用

核心脚本 `scripts/gpx_to_fit.py` 为纯标准库实现，无第三方依赖：

```bash
# 转换（默认在同目录输出同名 .fit）
python3 scripts/gpx_to_fit.py <input.gpx>

# 转换并自动校验心率（推荐；需要系统已装 gpsbabel）
python3 scripts/gpx_to_fit.py --verify <input.gpx> [output.fit]

# 只产出含标准 gpxtpx:hr 的 GPX（供 Strava/Garmin 直接上传，不生成 FIT）
python3 scripts/gpx_to_fit.py --prepare <input.gpx> [output.gpx]
```

脚本会打印轨迹点数、时间范围、心率点数与均值/峰值/谷值、累计距离，便于核对。

## 关键约束（转换前必读）

两条会导致文件损坏或心率丢失的硬约束，细节与排查方法见
`references/fit-format-gotchas.md`。要点：

1. **不要用 gpsbabel 做转换**。实测它读不进 EZON 的 `<heartrate>`（即便预处理成
   标准 `gpxtpx:hr` 也无效），且写 FIT 时会丢弃心率。gpsbabel 只适合当**校验器**。
2. **若自行修改编码脚本**，注意 FIT 定义消息的字段三元组磁盘顺序是
   `字段号 → 大小 → 基础类型`（不是"大小 → 字段号 → 类型"），且 CRC 必须是
   **CRC-16/ARC**（反射多项式 `0xA001`、初值 0，校验值 `crc16(b'123456789') == 0xBB3D`）。
   这两处写错会分别导致解析错位和文件被判 corrupt。

解析 GPX 心率时按**本地标签名**匹配并同时兼容 `heartrate` / `hr`；
只匹配 `hr` 会静默漏掉全部心率，容易误判成"这个 GPX 本来就没心率"。

## 校验流程

产出 FIT 后必须校验，默认用 `--verify` 自动完成。手动等价命令：

```bash
gpsbabel -i garmin_fit -f out.fit -o gtrnctr -F check.tcx
grep -c HeartRateBpm check.tcx
```

判定标准：gpsbabel 读取**无任何报错**，且 TCX 中心率点数与轨迹点数一致、
均值/峰值与源 GPX 吻合。

**不要用 `-o gpx` 反查心率**——gpsbabel 的 gpx 输出本身不写心率，
结果恒为 0，会得出错误结论。

## 工作流

1. 确认输入是 EZON 导出的 GPX（检查 creator 或文件名格式 `yyyymmddHHMMSS.gpx`）
2. 运行 `python3 scripts/gpx_to_fit.py --verify <input.gpx> [output.fit]`
3. 核对输出统计：心率点数应等于轨迹点数，均值/峰值与源数据一致
4. 若校验失败，按 `references/fit-format-gotchas.md` 第 6 节排查清单定位
5. 向用户报告文件大小、轨迹点数、距离、心率区间与校验结论

## 边界说明

- 生成的 FIT 含 `file_id` 与 `record` 两类消息，未包含 session/lap/device_info。
  足以被主流平台识别为一次活动；若某平台强制要求 session 消息，需另行扩展。
- 海拔直接沿用 GPX 的 `<ele>`，平地场景可能含 GPS 噪点，不做平滑处理。
- 速度为相邻点差分计算，末点沿用上一帧速度。
- 每公里配速/速度由 GPX 坐标推算，与 EZON App 的逐公里配速可能不一致：EZON GPX 只导出 `<heartrate>`（无里程字段），累计距离由坐标重算，慢速/热身段 GPS 抖动会虚增距离；而 App 逐公里配速来自手表内部融合里程计（未导出）。总距离、总时长、心率均准确且与 App 吻合。若需原生逐公里分段，请用手表原生 FIT/TCX，不要用 GPX 重建；对坐标做平滑也无法还原手表里程计，故本转换不做平滑。
