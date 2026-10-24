#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""EZON 手表 GPX → Garmin FIT 转换（保留心率）。

背景：EZON 导出的 GPX 把心率放在自定义标签 <extensions><heartrate> 里，
gpsbabel 等通用工具读不出来，转换后心率会全部丢失（实测结论见
references/fit-format-gotchas.md）。本脚本用纯标准库直接编码 FIT，
把心率随轨迹一并写入。

用法:
    python3 gpx_to_fit.py <input.gpx> [output.fit]           # 转换（默认同名 .fit）
    python3 gpx_to_fit.py --verify <input.gpx> [output.fit]  # 转换并自动校验心率
    python3 gpx_to_fit.py --prepare <input.gpx> [output.gpx] # 只产出含标准 gpxtpx:hr 的 GPX

仅用标准库，无第三方依赖。--verify 需要系统已装 gpsbabel（未装则跳过校验）。
"""
import math
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

SEMI = 2 ** 31 / 180.0
FIT_EPOCH = datetime(1989, 12, 31, 0, 0, 0, tzinfo=timezone.utc)
GPXTPX_NS = 'http://www.garmin.com/xmlschemas/TrackPointExtension/v1'

# ---------- FIT 基础类型 ----------
U8 = 0x02      # uint8 / enum
U16 = 0x84     # uint16
S32 = 0x85     # sint32
U32 = 0x86     # uint32
U32Z = 0x8E    # uint32z

PROTO = 0x20          # protocol 2.0
PROFILE = 2156        # FIT profile version


def localname(tag: str) -> str:
    """去掉 XML 命名空间取本地标签名。"""
    return tag.split('}')[-1]


def parse_gpx(path):
    """解析 GPX 轨迹点，心率同时兼容自定义 heartrate 与标准 hr/gpxtpx:hr。"""
    if not os.path.isfile(path):
        raise SystemExit(f'找不到文件: {path}')
    root = ET.parse(path).getroot()
    pts = []
    for el in root.iter():
        if localname(el.tag) != 'trkpt':
            continue
        try:
            lat = float(el.get('lat'))
            lon = float(el.get('lon'))
        except (TypeError, ValueError):
            continue
        ele = t = hr = None
        for c in el:
            ln = localname(c.tag)
            if ln == 'ele' and c.text:
                ele = float(c.text)
            elif ln == 'time' and c.text:
                t = datetime.fromisoformat(c.text.replace('Z', '+00:00'))
            elif ln == 'extensions':
                for sub in c.iter():
                    if localname(sub.tag) in ('heartrate', 'hr') and sub.text:
                        try:
                            hr = int(float(sub.text))
                        except ValueError:
                            pass
        if t is None:
            continue
        pts.append(dict(lat=lat, lon=lon, ele=ele, t=t, hr=hr))
    pts.sort(key=lambda p: p['t'])
    return pts


def hav(a, b):
    """两点间大圆距离（米）。"""
    R = 6371000.0
    p1, p2 = math.radians(a['lat']), math.radians(b['lat'])
    dphi = math.radians(b['lat'] - a['lat'])
    dl = math.radians(b['lon'] - a['lon'])
    x = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(x))


def crc16(data: bytes, crc: int = 0) -> int:
    """FIT CRC-16，等价 Garmin FIT SDK fit_crc.cpp。

    实为 CRC-16/ARC：反射多项式 0xA001、初值 0x0000。
    不是常见的 0x1021 / 0x8408 变体，用错会被判 corrupt。
    自检：crc16(b'123456789') == 0xBB3D
    """
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = ((crc >> 1) ^ 0xA001) & 0xFFFF if (crc & 1) else (crc >> 1) & 0xFFFF
    return crc


def def_msg(local, gmsg, fields):
    """构建 FIT 定义消息。

    fields: list of (field_num, base_type, size)
    磁盘上的字段三元组顺序必须是 (字段定义号, 大小, 基础类型)；
    写成 (大小, 字段号, 类型) 会让解析器把长度当字段号而错位。
    """
    body = struct.pack('<BBH', 0, 0, gmsg) + struct.pack('<B', len(fields))
    for fn, bt, sz in fields:
        body += struct.pack('<BBB', fn, sz, bt)
    return bytes([0x40 | local]) + body


def data_msg(local, packed):
    return bytes([local]) + b''.join(packed)


def pack_u8(v):  return struct.pack('<B', int(v) & 0xFF)
def pack_u16(v): return struct.pack('<H', int(v) & 0xFFFF)
def pack_s32(v): return struct.pack('<i', int(round(v)))
def pack_u32(v): return struct.pack('<I', int(v) & 0xFFFFFFFF)


def build_fit(pts, out_path):
    if not pts:
        raise SystemExit('无有效轨迹点（GPX 里没找到带时间戳的 trkpt）')

    # 累计距离与瞬时速度
    cum = 0.0
    for i, p in enumerate(pts):
        if i > 0:
            d = hav(pts[i - 1], p)
            cum += d
            dt = (p['t'] - pts[i - 1]['t']).total_seconds()
            p['speed'] = (d / dt) if dt > 0 else 0.0
            p['dist'] = cum
        else:
            p['speed'] = p['dist'] = 0.0
    if len(pts) > 1:
        pts[-1]['speed'] = pts[-2]['speed']
        pts[-1]['dist'] = cum

    t0 = int((pts[0]['t'] - FIT_EPOCH).total_seconds())

    # record (global 20) 字段
    rec_fields = [
        (253, U32, 4),   # timestamp
        (0, S32, 4),     # position_lat   (semicircles)
        (1, S32, 4),     # position_long  (semicircles)
        (2, U16, 2),     # altitude       (scale 5, offset 500)
        (3, U8, 1),      # heart_rate     (bpm)
        (6, U16, 2),     # speed          (scale 1000, m/s)
        (5, U32, 4),     # distance       (scale 100, m)
    ]
    # file_id (global 0) 字段
    fid_fields = [
        (0, U8, 1),      # type (4 = activity)
        (1, U16, 2),     # manufacturer (255 = development)
        (2, U16, 2),     # product
        (3, U32Z, 4),    # serial_number
        (4, U32, 4),     # time_created
    ]

    body = bytearray()
    body += def_msg(0, 0, fid_fields)
    body += def_msg(1, 20, rec_fields)
    body += data_msg(0, [pack_u8(4), pack_u16(255), pack_u16(0), pack_u32(0), pack_u32(t0)])
    for p in pts:
        alt = int(round(((p['ele'] if p['ele'] is not None else 0.0) + 500) * 5))
        alt = max(0, min(0xFFFF, alt))
        hr = int(p['hr']) if p['hr'] is not None else 0
        spd = max(0, min(0xFFFF, int(round((p['speed'] or 0) * 1000))))
        body += data_msg(1, [
            pack_u32(int((p['t'] - FIT_EPOCH).total_seconds())),
            pack_s32(int(round(p['lat'] * SEMI))),
            pack_s32(int(round(p['lon'] * SEMI))),
            pack_u16(alt), pack_u8(hr), pack_u16(spd),
            pack_u32(int(round(p['dist'] * 100))),
        ])

    data_size = len(body)
    prefix = bytes([14, PROTO]) + struct.pack('<H', PROFILE) + struct.pack('<I', data_size) + b'.FIT'
    header = prefix + struct.pack('<H', crc16(prefix))
    full = header + body + struct.pack('<H', crc16(header + body))

    with open(out_path, 'wb') as f:
        f.write(full)

    hrs = [p['hr'] for p in pts if p['hr'] is not None]
    print(f'轨迹点: {len(pts)}')
    print(f'时间: {pts[0]["t"]} → {pts[-1]["t"]}')
    if hrs:
        print(f'心率: {len(hrs)}/{len(pts)} 点, 均值 {sum(hrs)/len(hrs):.0f}, 峰值 {max(hrs)}, 谷值 {min(hrs)}')
    else:
        print('心率: 无（GPX 未含心率标签）')
    print(f'累计距离: {cum/1000:.3f} km')
    print(f'输出: {out_path}  ({len(full)} bytes)')
    return out_path


def verify(out_fit):
    """用 gpsbabel 回读校验：无报错 + 心率点数。

    必须转 TCX(gtrnctr) 来判断心率；gpsbabel 的 -o gpx 输出本身不写心率，
    用它反查会恒定得到 0 而误判。
    """
    if not shutil.which('gpsbabel'):
        print('[校验跳过] 未安装 gpsbabel（可选依赖）')
        return None
    tcx = os.path.join(tempfile.gettempdir(), os.path.basename(out_fit) + '.tcx')
    r = subprocess.run(['gpsbabel', '-i', 'garmin_fit', '-f', out_fit,
                        '-o', 'gtrnctr', '-F', tcx], capture_output=True, text=True)
    msg = (r.stdout + r.stderr).strip()
    if msg:
        print('[校验失败] gpsbabel 读取报错:\n' + msg)
        return False
    txt = open(tcx, encoding='utf-8', errors='ignore').read()
    vals = [int(v) for v in re.findall(r'<HeartRateBpm>\s*<Value>(\d+)</Value>', txt)]
    if not vals:
        vals = [int(v) for v in re.findall(r'<Value>(\d+)</Value>', txt)]
    if vals:
        print(f'[校验通过] gpsbabel 无错读取；心率 {len(vals)} 点，'
              f'均值 {sum(vals)/len(vals):.1f}，峰 {max(vals)}，谷 {min(vals)}')
    else:
        print('[校验警告] 读取无错，但未检出心率')
    return True


def enhance_for_gpsbabel(src, dst):
    """把自定义 <heartrate> 复制为 gpsbabel 可识别的标准 <gpxtpx:hr>。

    注意：即便转换成标准格式，gpsbabel 写 FIT 时仍会丢弃心率，
    此输出主要用于上传到 Strava / Garmin Connect 等认 GPX 扩展的平台。
    """
    if not os.path.isfile(src):
        raise SystemExit(f'找不到文件: {src}')
    txt = open(src, encoding='utf-8').read()
    pat = re.compile(r'(<extensions>)(.*?)<heartrate>(\d+)</heartrate>(.*?)(</extensions>)', re.S)

    def repl(m):
        hr = m.group(3)
        tpx = (f'<gpxtpx:TrackPointExtension xmlns:gpxtpx="{GPXTPX_NS}">'
               f'<gpxtpx:hr>{hr}</gpxtpx:hr></gpxtpx:TrackPointExtension>')
        return m.group(1) + m.group(2) + tpx + f'<heartrate>{hr}</heartrate>' + m.group(4) + m.group(5)

    new, n = pat.subn(repl, txt)
    with open(dst, 'w', encoding='utf-8') as f:
        f.write(new)
    print(f'已为 {n} 个轨迹点写入标准 gpxtpx:hr → {dst}')


def main():
    args = sys.argv[1:]
    if not args or args[0] in ('-h', '--help'):
        print(__doc__)
        return
    do_verify = False
    if args[0] == '--verify':
        do_verify = True
        args = args[1:]
        if not args:
            print(__doc__)
            return
    if args[0] == '--prepare':
        if len(args) < 2:
            raise SystemExit('用法: gpx_to_fit.py --prepare <input.gpx> [output.gpx]')
        src = args[1]
        dst = args[2] if len(args) > 2 else src[:-4] + '_std_hr.gpx'
        enhance_for_gpsbabel(src, dst)
        return
    src = args[0]
    if len(args) > 1:
        dst = args[1]
    else:
        dst = (src[:-4] if src.lower().endswith('.gpx') else src) + '.fit'
    build_fit(parse_gpx(src), dst)
    if do_verify:
        verify(dst)


if __name__ == '__main__':
    main()
