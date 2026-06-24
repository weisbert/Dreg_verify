# -*- coding: utf-8 -*-
"""
inspect_vba.py — 从 .xlsm/.xlsb/.xls 抽取 VBA 宏源码，导出供分析（尤其是生成 wr_rf_tc.sv 的过程）。

背景：原始核心文件是带宏的 Excel(.xlsm)，旧的 .sv 是其中 VBA 宏生成的。要 1:1 复刻生成逻辑，
读 VBA 源码最直接。本脚本在公司机上跑，把 VBA 模块源码 dump 成文本，按需关键词定位相关过程。

用法:
    python tools/inspect_vba.py 核心文件.xlsm
        [--out vba.txt]            输出文件(默认 <名字>_vba.txt)
        [--find kw1,kw2]           只打印含关键词的行(带模块名/行号/上下文)，用于快速定位
        [--context N]              --find 时每个匹配前后各带 N 行(默认 3)
        [--modules A,B]            只导出这些模块
        [--list]                   只列出模块名+大小，不导出源码

定位 .sv 生成逻辑的常用关键词(默认 --list 时会自动提示命中这些的模块):
    Print #, FreeFile, Open, Output, ENV_RF, RF_WRITE, assert, pll_n, top_output, .sv, sformatf

依赖: 优先用 oletools(pip install oletools)，更稳；缺失则用内置纯 Python 兜底(OLE2+MS-OVBA 解压)。
"""

import argparse
import os
import re
import struct
import sys
import zipfile

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

# 指向 .sv 生成逻辑的关键词
SV_HINT_KEYWORDS = ["Print #", "FreeFile", "Output", "ENV_RF", "RF_WRITE", "assert",
                    "pll_n", "top_output", ".sv", "sformatf", "uvm_report", "force"]


# ───────────────────────────── 抽取：oletools 优先 ─────────────────────────────
def extract_with_oletools(path):
    try:
        from oletools.olevba import VBA_Parser
    except Exception:  # noqa: BLE001
        return None                       # 未装 oletools → 触发纯 Python 兜底
    vp = None
    try:
        vp = VBA_Parser(path)
        if not vp.detect_vba_macros():
            return {}                     # 解析成功但确无宏
        mods = {}
        for (_fname, _spath, vba_filename, vba_code) in vp.extract_macros():
            name = vba_filename or ("module_%d" % len(mods))
            if name in mods:              # 同名追加
                name = "%s_%d" % (name, len(mods))
            mods[name] = vba_code or ""
        return mods
    except Exception as ex:               # noqa: BLE001  oletools 解析失败 → 回退兜底
        print("(oletools 解析出错：%s；改用纯 Python 兜底)" % ex)
        return None
    finally:
        if vp is not None:
            try:
                vp.close()
            except Exception:  # noqa: BLE001
                pass


# ───────────────────────────── 抽取：纯 Python 兜底 ─────────────────────────────
ENDOFCHAIN = 0xFFFFFFFE
FREESECT = 0xFFFFFFFF


def _parse_ole_streams(data):
    """解析 OLE2 复合文档(MS-CFB)，返回 {流名: bytes}。"""
    if data[:8] != b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1":
        raise ValueError("不是 OLE2 复合文档")
    if len(data) < 512:
        raise ValueError("OLE2 头部不足 512 字节（文件损坏或过短）")
    major = struct.unpack_from("<H", data, 26)[0]   # 主版本：v3 时 Stream Size 高 32 位须忽略
    sector_size = 1 << struct.unpack_from("<H", data, 30)[0]
    mini_sector_size = 1 << struct.unpack_from("<H", data, 32)[0]
    num_fat = struct.unpack_from("<I", data, 44)[0]
    first_dir = struct.unpack_from("<I", data, 48)[0]
    mini_cutoff = struct.unpack_from("<I", data, 56)[0]
    first_minifat = struct.unpack_from("<I", data, 60)[0]
    num_minifat = struct.unpack_from("<I", data, 64)[0]
    first_difat = struct.unpack_from("<I", data, 68)[0]
    num_difat = struct.unpack_from("<I", data, 72)[0]
    per = sector_size // 4

    def sect_off(sid):
        return (sid + 1) * sector_size

    # DIFAT → FAT 扇区号列表
    difat = list(struct.unpack_from("<109I", data, 76))
    sid, cnt = first_difat, 0
    while sid not in (ENDOFCHAIN, FREESECT) and cnt < num_difat and sect_off(sid) + sector_size <= len(data):
        vals = struct.unpack_from("<%dI" % per, data, sect_off(sid))
        difat.extend(vals[:-1])
        sid = vals[-1]
        cnt += 1
    difat = [s for s in difat if s not in (FREESECT, ENDOFCHAIN)]
    if num_fat:
        difat = difat[:num_fat]

    fat = []
    for fs in difat:
        if sect_off(fs) + sector_size <= len(data):
            fat.extend(struct.unpack_from("<%dI" % per, data, sect_off(fs)))

    def read_chain(start):
        out, sid, seen = bytearray(), start, set()
        while sid not in (ENDOFCHAIN, FREESECT) and sid < len(fat) and sid not in seen:
            seen.add(sid)
            off = sect_off(sid)
            out += data[off:off + sector_size]
            sid = fat[sid]
        return bytes(out)

    dir_data = read_chain(first_dir)
    entries = []
    for i in range(0, len(dir_data) - 127, 128):
        ent = dir_data[i:i + 128]
        otype = ent[66]
        if otype not in (1, 2, 5):
            continue
        nlen = struct.unpack_from("<H", ent, 64)[0]
        name = ent[:max(0, nlen - 2)].decode("utf-16-le", "replace")
        start = struct.unpack_from("<I", ent, 116)[0]
        size = struct.unpack_from("<Q", ent, 120)[0]
        if major == 3:                       # v3 须忽略高 32 位（旧写入器可能遗留脏值）
            size &= 0xFFFFFFFF
        entries.append({"name": name, "type": otype, "start": start, "size": size})

    roots = [e for e in entries if e["type"] == 5]
    mini_stream = read_chain(roots[0]["start"])[:roots[0]["size"]] if roots else b""

    minifat, msid, cnt = [], first_minifat, 0
    while (msid not in (ENDOFCHAIN, FREESECT) and cnt < num_minifat
           and msid < len(fat) and sect_off(msid) + sector_size <= len(data)):
        minifat.extend(struct.unpack_from("<%dI" % per, data, sect_off(msid)))
        msid = fat[msid]
        cnt += 1

    def read_mini(start, size):
        out, sid, seen = bytearray(), start, set()
        while sid not in (ENDOFCHAIN, FREESECT) and sid < len(minifat) and sid not in seen:
            seen.add(sid)
            off = sid * mini_sector_size
            out += mini_stream[off:off + mini_sector_size]
            sid = minifat[sid]
        return bytes(out)[:size]

    streams = {}
    for e in entries:
        if e["type"] != 2:
            continue
        if e["size"] < mini_cutoff:
            streams[e["name"]] = read_mini(e["start"], e["size"])
        else:
            streams[e["name"]] = read_chain(e["start"])[:e["size"]]
    return streams


def _ovba_decompress(data, start):
    """MS-OVBA 2.4.1 解压：data[start] 必须是 0x01 签名。返回解压字节。"""
    if start >= len(data) or data[start] != 0x01:
        raise ValueError("CompressedContainer 签名(0x01)缺失")
    out = bytearray()
    i = start + 1
    n = len(data)
    while i + 1 < n:
        header = struct.unpack_from("<H", data, i)[0]
        i += 2
        if ((header >> 12) & 0x7) != 0b011:      # CompressedChunkSignature 必须为 0b011
            raise ValueError("bad CompressedChunkSignature")
        chunk_size = (header & 0x0FFF) + 3       # 含 2 字节头
        flag = (header >> 15) & 1
        chunk_end = min(i + chunk_size - 2, n)
        if flag == 0:                            # 未压缩：原样 4096 字节
            out += data[i:i + 4096]
            i += 4096
            continue
        chunk_out_start = len(out)
        while i < chunk_end:
            flagbyte = data[i]
            i += 1
            for bit in range(8):
                if i >= chunk_end:
                    break
                if not (flagbyte >> bit) & 1:    # 字面量
                    out.append(data[i])
                    i += 1
                else:                            # CopyToken（2 字节，必须完整落在本 chunk 内）
                    if i + 2 > chunk_end:
                        i = chunk_end
                        break
                    token = struct.unpack_from("<H", data, i)[0]
                    i += 2
                    diff = len(out) - chunk_out_start
                    bit_count = max((diff - 1).bit_length(), 4)
                    length_mask = 0xFFFF >> bit_count
                    offset_mask = (~length_mask) & 0xFFFF
                    length = (token & length_mask) + 3
                    offset = ((token & offset_mask) >> (16 - bit_count)) + 1
                    if offset > len(out):
                        i = chunk_end
                        break
                    for _ in range(length):
                        out.append(out[len(out) - offset])
    return bytes(out)


def _codepage_to_encoding(cp):
    import codecs
    name = {65001: "utf-8", 1200: "utf-16-le"}.get(cp, "cp%d" % cp)
    try:
        codecs.lookup(name)
        return name
    except Exception:  # noqa: BLE001
        return None


def _detect_codepage(streams):
    """从 dir 流读 PROJECTCODEPAGE(Id=0x0003, Size=2, CodePage UInt16)，返回 Python 编码名。"""
    dir_raw = next((v for k, v in streams.items() if k.lower() == "dir"), None)
    if not dir_raw:
        return None
    for pos in (m.start() for m in re.finditer(b"\x01", dir_raw)):
        try:
            d = _ovba_decompress(dir_raw, pos)
        except Exception:  # noqa: BLE001
            continue
        idx = d.find(b"\x03\x00\x02\x00\x00\x00")     # PROJECTCODEPAGE 记录前缀
        if idx >= 0 and idx + 8 <= len(d):
            return _codepage_to_encoding(struct.unpack_from("<H", d, idx + 6)[0])
    return None


def extract_pure_python(path):
    """无 oletools 时的兜底：解 zip → vbaProject.bin → OLE2 流 → 扫 0x01 解压模块源码。"""
    if not zipfile.is_zipfile(path):
        raise SystemExit("纯 Python 兜底仅支持 .xlsm/.xlsb(zip 容器)。.xls 请装 oletools。")
    with zipfile.ZipFile(path) as z:
        binname = next((n for n in z.namelist() if n.lower().endswith("vbaproject.bin")), None)
        if not binname:
            return {}
        vba_bin = z.read(binname)
    try:
        streams = _parse_ole_streams(vba_bin)
    except (ValueError, struct.error, IndexError) as ex:
        raise SystemExit("vbaProject.bin 解析失败（可能损坏或为加密工程）：%s；建议 pip install oletools 后重试。" % ex)

    codepage = _detect_codepage(streams)
    encodings = [e for e in (codepage, "utf-8", "cp936", "latin-1") if e]
    print("(纯 Python 兜底：源码代码页=%s；中文解码可能不完美，需精确请 pip install oletools)"
          % (codepage or "未检出, 试 utf-8/cp936"))

    def _decode(b):
        for enc in encodings:
            try:
                return b.decode(enc)
            except Exception:  # noqa: BLE001
                continue
        return b.decode("latin-1", "replace")

    mods = {}
    for sname, content in streams.items():
        if sname.lower() in ("dir", "_vba_project", "project", "projectwm"):
            continue
        # 扫描所有 0x01 试解压；要求结果以 'Attribute VB_' 开头；多候选取解压最长者(最可能是完整源码)
        best = None
        for pos in (m.start() for m in re.finditer(b"\x01", content)):
            try:
                text = _ovba_decompress(content, pos)
            except Exception:  # noqa: BLE001
                continue
            if text.lstrip()[:13] != b"Attribute VB_":
                continue
            if best is None or len(text) > len(best):
                best = text
        if best is None:
            continue
        src = _decode(best)
        m = re.search(r'Attribute VB_Name = "([^"]+)"', src)
        name = m.group(1) if m else sname
        if name in mods:
            name = "%s_%d" % (name, len(mods))
        mods[name] = src
    return mods


# ───────────────────────────── 输出 ─────────────────────────────
def module_hits(src):
    return [kw for kw in SV_HINT_KEYWORDS if kw.lower() in src.lower()]


def do_list(mods):
    print("VBA 模块 %d 个：" % len(mods))
    for name, src in sorted(mods.items(), key=lambda kv: -len(kv[1])):
        hits = module_hits(src)
        flag = "  ← 疑似生成 .sv 相关 [%s]" % ",".join(hits) if hits else ""
        print("  %-28s %6d 字符 %2d 行%s"
              % (name, len(src), src.count("\n") + 1, flag))


def do_find(mods, keywords, context):
    kws = [k.strip().lower() for k in keywords.split(",") if k.strip()]
    out = []
    total = 0
    for name in sorted(mods):
        lines = mods[name].splitlines()
        hits = [i for i, ln in enumerate(lines) if any(k in ln.lower() for k in kws)]
        if not hits:
            continue
        out.append("=" * 70)
        out.append("==== 模块 %s （命中 %d 行，关键词 %s）====" % (name, len(hits), kws))
        shown = set()
        for h in hits:
            lo, hi = max(0, h - context), min(len(lines), h + context + 1)
            for j in range(lo, hi):
                if j in shown:
                    continue
                shown.add(j)
                mark = ">>" if j == h or any(k in lines[j].lower() for k in kws) else "  "
                out.append("%s %5d | %s" % (mark, j + 1, lines[j]))
            out.append("  ----")
        total += len(hits)
    nmods = sum(1 for ln in out if ln.startswith("==== 模块"))
    out.insert(0, "关键词定位：共命中 %d 行（跨 %d 个模块）" % (total, nmods))
    return "\n".join(out), total


def do_dump(mods, only):
    out = []
    norm = {o.strip().lower() for o in only} if only else None
    names = [n for n in sorted(mods) if (not norm or n.strip().lower() in norm)]
    for name in names:
        out.append("=" * 70)
        out.append("==== 模块 %s （%d 字符）====" % (name, len(mods[name])))
        out.append(mods[name])
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("path", help="带宏的 Excel (.xlsm/.xlsb/.xls)")
    ap.add_argument("--out", default=None)
    ap.add_argument("--find", default=None, help="逗号分隔关键词，只导出含关键词的行")
    ap.add_argument("--context", type=int, default=3)
    ap.add_argument("--modules", default=None, help="逗号分隔，只导出这些模块")
    ap.add_argument("--list", action="store_true")
    args = ap.parse_args()

    if not os.path.isfile(args.path):
        sys.exit("找不到文件: %s" % args.path)

    mods = extract_with_oletools(args.path)
    used = "oletools"
    if mods is None:
        print("(未装 oletools，使用内置纯 Python 兜底；如解压异常请 pip install oletools 后重试)")
        mods = extract_pure_python(args.path)
        used = "纯Python兜底"

    if not mods:
        sys.exit("未在该文件中找到 VBA 宏（或为加密工程）。")

    print("抽取方式: %s；VBA 模块 %d 个。" % (used, len(mods)))

    if args.list:
        do_list(mods)
        return

    base = os.path.splitext(args.path)[0]
    if args.find:
        text, total = do_find(mods, args.find, args.context)
        if total == 0:
            print("未命中任何关键词「%s」，未生成文件。可用 --list 查看模块名。" % args.find)
            return
        out_path = args.out or (base + "_vba_find.txt")
    else:
        only = set(s.strip() for s in args.modules.split(",") if s.strip()) if args.modules else None
        if only:
            have = {n.strip().lower() for n in mods}
            missing = [o for o in only if o.strip().lower() not in have]
            if missing:
                print("⚠ 未找到模块: %s（可用: %s）" % (", ".join(missing), ", ".join(sorted(mods))))
        text = do_dump(mods, only)
        if not text.strip():
            print("没有匹配的模块可导出，未生成文件。")
            return
        out_path = args.out or (base + "_vba.txt")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(text + "\n")
    print("已导出: %s （%d 字符）" % (out_path, len(text)))
    do_list(mods)
    print("\n提示：先看上面带「← 疑似生成 .sv 相关」的模块；或用 --find \"Print #,RF_WRITE,pll_n\" 定位。")


if __name__ == "__main__":
    main()
