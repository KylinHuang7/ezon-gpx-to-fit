# EZON GPX → FIT：踩坑记录与 FIT 编码速查

本文件记录实测得到、但无法从通用文档查到的结论。转换前**必须**读一遍，
否则极易产出"能打开但心率全丢"或"被判 corrupt"的文件。

## 1. EZON GPX 的心率标签

EZON 手表（GPX creator 为 `http://www.ezon.cn`，文件名常为 `yyyymmddHHMMSS.gpx`）
把心率放在**自定义标签**里，不是 Garmin 标准扩展：

```xml
<trkpt lat="31.162427" lon="121.456119">
  <ele>12.000000</ele>
  <time>2026-10-21T09:47:41Z</time>
  <extensions>
    <heartrate>113</heartrate>
  </extensions>
</trkpt>
```

- 标签名是 **`heartrate`**，不是 `hr`、也不是 `gpxtpx:hr`。
- 用 `hr` 去匹配会**静默漏掉全部心率**（得到 0/N 点），很容易误判成"这个 GPX 没心率"。
- 解析时按**本地标签名**匹配（忽略命名空间），并同时兼容 `heartrate` / `hr`，最稳。
- 海拔字段 `<ele>` 在平地的起伏多为 GPS 噪点（如上海平地出现"最高 35m"），不可尽信。

## 2. gpsbabel 的读写不对称（关键实测结论）

| 能力 | 结论 |
|---|---|
| 读 GPX 心率 | ❌ 不识别 `<heartrate>`；即便预处理成标准 `gpxtpx:hr`，转出的 FIT **字节数与不预处理完全相同**，说明心率根本没进去 |
| 读 FIT 心率 | ✅ 可以（FIT → TCX 能拿到心率） |
| 写 FIT 心率 | ❌ 会丢弃。用 gpsbabel 把含心率的 FIT 再封装一次，心率归零 |
| gpx 输出写心率 | ❌ 不支持，见第 4 节 |

**结论**：任何需要保留心率的 GPX→FIT 转换，都不能走 gpsbabel，
必须用 `scripts/gpx_to_fit.py`。gpsbabel 只适合做**校验器**。

## 3. 两个会让文件损坏的编码陷阱

### 陷阱 A：定义消息的字段字节顺序

FIT 定义消息里每个字段的三元组，磁盘顺序是：

```
字段定义号(1B) → 大小(1B) → 基础类型(1B)
```

**不是** `大小 → 字段号 → 类型`。写反了解析器会把"长度"当成"字段号"，
后续消息全部错位，典型报错：

```
garmin_fit: Message 14 hasn't been defined before being used at file position 0x49.
```

看到 `Message N hasn't been defined` 这类报错，第一嫌疑就是字段顺序写反。

### 陷阱 B：CRC 是 CRC-16/ARC，不是常见的 CCITT 变体

| 项 | 值 |
|---|---|
| 算法 | **CRC-16/ARC**（等价 Garmin FIT SDK `fit_crc.cpp` 的 nibble 查表实现） |
| 多项式 | 反射 `0xA001` |
| 初值 | `0x0000` |
| 校验值 | `crc16(b'123456789') == 0xBB3D` |

写错成 `0x1021`（非反射）或 `0x8408`（KERMIT，check `0x2189`）都会被判 corrupt。

覆盖范围：
- 文件尾 CRC = `crc16(header + body)`（body 即 header 之后到数据结束）
- 若 header_size = 14，header 末 2 字节 = `crc16(header 前 12 字节)`

**自检技巧**：CRC 有自反性质——把 CRC 值追加到数据后再算 CRC 会得 0。
因此 `crc16(header + body)` 与 `crc16(body)` 相等（因为 header 自带 CRC 使整体归零），
这个现象可以用来快速确认算法是否正确。

## 4. 验证方法：必须用 TCX，不能用 GPX

```bash
# 正确：转成 Garmin Training Center (TCX)，它支持心率
gpsbabel -i garmin_fit -f out.fit -o gtrnctr -F check.tcx
grep -c HeartRateBpm check.tcx
```

**不要用 `-o gpx` 反查心率**：gpsbabel 的 gpx 输出本身就不写心率字段，
无论 FIT 里有没有心率，结果都是 0，会得出"心率丢失"的错误结论。

判定标准：gpsbabel 读取**无任何报错**（无 CRC mismatch、无 `Message N hasn't been defined`），
且 TCX 里心率点数与轨迹点数一致、均值/峰值与源 GPX 吻合。

## 5. FIT 字段编码速查（本脚本用到的部分）

| 字段 | 所属消息(global) | 字段号 | 基础类型 | 编码公式 |
|---|---|---|---|---|
| timestamp | record(20) | 253 | uint32 | 秒数，自 1989-12-31 00:00:00 UTC |
| position_lat/long | record(20) | 0 / 1 | sint32 | `round(度 × 2^31 / 180)`（semicircles） |
| altitude | record(20) | 2 | uint16 | `round((米 + 500) × 5)` |
| heart_rate | record(20) | 3 | uint8 | bpm 原值 |
| speed | record(20) | 6 | uint16 | `round(m/s × 1000)` |
| distance | record(20) | 5 | uint32 | `round(米 × 100)`（累计） |
| type | file_id(0) | 0 | uint8 | `4` = activity |
| manufacturer | file_id(0) | 1 | uint16 | `255` = development |

基础类型值：enum/uint8 `0x02`、sint16 `0x83`、uint16 `0x84`、
sint32 `0x85`、uint32 `0x86`、uint32z `0x8E`、float32 `0x88`、string `0x07`。

文件头（14 字节）：`header_size(14)`、`protocol(0x20)`、`profile_version(uint16)`、
`data_size(uint32)`、`".FIT"`、header CRC(uint16)。

## 6. 排查清单

产出 FIT 后按此顺序排查：

1. gpsbabel 读取有无报错 → 有 `Message N hasn't been defined` 查陷阱 A；有 CRC mismatch 查陷阱 B
2. TCX 回读心率点数是否为 0 → 0 则检查 GPX 心率标签名（第 1 节）
3. 均值/峰值是否与源数据吻合 → 不吻合检查 scale/offset 编码（第 5 节）
4. 距离/时长是否与源 GPX 一致 → 用于确认轨迹本身没被破坏
