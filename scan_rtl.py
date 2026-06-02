# -*- coding: utf-8 -*-
"""
scan_rtl.py — 扫描 RTL 一次，自动生成探针前缀映射 + 不可验证清单。

解决"生成 → 仿真 → CUVUNF → grep → 配前缀 → 重新生成"的低效循环：
不再逐个信号试错，直接静态扫描 RTL，把 Excel 里每个信号的层级一次找全。

用法（在公司机上）:
    python scan_rtl.py --top  $dreg_dir/lpbt_dig_top.v \
                       --rtl-dirs $Hi1108V100_RF_ROOT/digital/pll,$Hi1108V100_RF_ROOT/digital/common \
                       --excel 真表.xlsx \
                       --out probe_prefixes.txt

产物 probe_prefixes.txt:
    - 每行 信号名=层级路径, 可直接被 GUI『设置探针前缀 → 导入…』或 CLI --probe-prefix-file 使用
    - 顶层就能探到的信号、RTL 找不到的信号以注释列出
"""

import argparse
import os
import sys

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dreg_verify import excel_model, rtl_scan   # noqa: E402


def main():
    ap = argparse.ArgumentParser(description="扫描 RTL 自动生成探针前缀映射")
    ap.add_argument("--top", required=True, help="DUT 顶层 .v 文件（即 ENV_RF 指向的模块）")
    ap.add_argument("--top-module", default=None,
                    help="顶层模块名（默认取 --top 文件里的第一个 module）")
    ap.add_argument("--rtl-dirs", required=True,
                    help="RTL 目录（逗号分隔），扫描其下所有 .v/.sv 以解析子模块")
    ap.add_argument("--excel", required=True, help="Dreg 核心 Excel (.xlsx)")
    ap.add_argument("--out", default="probe_prefixes.txt", help="输出映射文件 (默认 probe_prefixes.txt)")
    ap.add_argument("--max-depth", type=int, default=4, help="层级展开深度 (默认 4)")
    args = ap.parse_args()

    # 1. 收集 RTL 文件（顶层 + 各目录）
    dirs = [d.strip() for d in args.rtl_dirs.split(",") if d.strip()]
    files = rtl_scan.find_verilog_files(dirs)
    if args.top not in files:
        files.append(args.top)
    print("RTL 文件: %d 个" % len(files))

    # 2. 解析所有模块
    modules = rtl_scan.scan_files(files)
    print("解析到模块: %d 个" % len(modules))

    # 3. 确定顶层模块名
    top_module = args.top_module
    if not top_module:
        with open(args.top, "r", encoding="utf-8", errors="replace") as f:
            parsed = rtl_scan.parse_modules(f.read())
        if not parsed:
            sys.exit("⛔ 在 %s 里找不到 module 定义" % args.top)
        top_module = list(parsed)[0]
    if top_module not in modules:
        sys.exit("⛔ 顶层模块 %r 不在已解析模块里（检查 --rtl-dirs 是否含顶层文件）" % top_module)
    print("DUT 顶层模块: %s" % top_module)

    # 4. 层级展开 → 信号位置表
    sigmap = rtl_scan.build_signal_map(modules, top_module, max_depth=args.max_depth)
    print("RTL 信号总数: %d" % len(sigmap))

    # 5. 对照 Excel
    print("装载 Excel: %s ..." % args.excel)
    wb = excel_model.load_workbook(args.excel)
    prefixes, at_top, missing = rtl_scan.match_excel(wb, sigmap)

    # 6. 写出映射文件
    text = rtl_scan.render_prefix_file(prefixes, at_top, missing, top_module=top_module)
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(text)

    print("")
    print("✅ 顶层可直接探到 : %4d 个（无需配置）" % len(at_top))
    print("🔧 需要探针前缀   : %4d 个（已写入映射）" % len(prefixes))
    print("⚠ RTL 中找不到   : %4d 个（不可验证，见文件尾部注释）" % len(missing))
    print("")
    print("映射文件已写出: %s" % args.out)
    print("用法: GUI『设置探针前缀 → 导入…』选择此文件；或 CLI 加 --probe-prefix-file %s" % args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
