# -*- coding: utf-8 -*-
"""
scan_rtl.py — 扫描 RTL 一次，自动生成探针前缀映射 + 不可验证清单。

【单文件、零依赖（纯 Python 标准库）——可直接拷到仿真服务器上运行】

解决"生成 → 仿真 → CUVUNF → grep → 配前缀 → 重新生成"的低效循环：
不再逐个信号试错，直接静态扫描 RTL，把 Excel 里每个信号的层级一次找全。

═══ 推荐工作流（Excel 在 Windows、RTL 在 Linux 服务器） ═══

① Windows（dreg 工具机，有 openpyxl）—— 从 Excel 抽出需要定位的信号清单:
       python scan_rtl.py --excel 真表.xlsx --export-nets nets.txt

② 上传 2 个小文件到服务器:  scan_rtl.py  +  nets.txt

③ Linux 服务器（只要有 python3，无需任何第三方库）—— 扫描 RTL:
       python3 scan_rtl.py --nets nets.txt \\
               --top $dreg_dir/lpbt_dig_top.v \\
               --rtl-dirs $RF_ROOT/digital/pll,$RF_ROOT/digital/common \\
               --out probe_prefixes.txt

④ 把 probe_prefixes.txt 拷回 Windows → GUI『设置探针前缀 → 导入…』→ 生成 .sv → 仿真一次过

═══ 单机工作流（Excel 和 RTL 在同一台机器） ═══

       python scan_rtl.py --excel 真表.xlsx --top lpbt_dig_top.v --rtl-dirs rtl目录 --out probe_prefixes.txt
"""

import argparse
import os
import re
import sys

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass


# ───────────────────────────── RTL 解析（纯标准库） ─────────────────────────────
# 不参与解析的关键字（不是模块名也不是信号名）
KEYWORDS = {
    "module", "endmodule", "input", "output", "inout", "wire", "reg", "logic",
    "assign", "always", "initial", "begin", "end", "if", "else", "case", "casez",
    "casex", "endcase", "for", "while", "parameter", "localparam", "generate",
    "endgenerate", "genvar", "function", "endfunction", "task", "endtask",
    "supply0", "supply1", "integer", "real", "signed", "unsigned", "posedge",
    "negedge", "or", "and", "not", "default", "defparam", "specify", "endspecify",
}

# 声明：input/output/inout/wire/reg/logic [位宽] 信号名
_DECL_RE = re.compile(
    r"\b(input|output|inout|wire|reg|logic)\b\s*(?:signed\s+)?(?:\[[^\]]*\]\s*)?([A-Za-z_][\w$]*)")
# 实例化：模块名 实例名 (   —— 行首两个标识符 + 左括号
_INST_RE = re.compile(r"^\s*([A-Za-z_]\w*)\s+([A-Za-z_]\w*)\s*\(", re.M)
# module 块
_MODULE_RE = re.compile(r"\bmodule\s+([A-Za-z_]\w*)(.*?)\bendmodule\b", re.S)


def strip_comments(text):
    text = re.sub(r"/\*.*?\*/", " ", text, flags=re.S)
    return re.sub(r"//[^\n]*", " ", text)


def parse_modules(text):
    """一个 .v 文件文本 → {模块名: {"signals": set(信号名), "instances": [(实例名, 模块名)]}}。"""
    out = {}
    for m in _MODULE_RE.finditer(strip_comments(text)):
        name, body = m.group(1), m.group(2)
        signals = {dm.group(2) for dm in _DECL_RE.finditer(body)
                   if dm.group(2) not in KEYWORDS}
        instances = [(im.group(2), im.group(1)) for im in _INST_RE.finditer(body)
                     if im.group(1) not in KEYWORDS and im.group(2) not in KEYWORDS]
        out[name] = {"signals": signals, "instances": instances}
    return out


def find_verilog_files(dirs):
    """递归收集目录下所有 .v/.sv 文件（跳过 .svn/.git）。"""
    files = []
    for d in dirs:
        for root, subdirs, names in os.walk(d):
            subdirs[:] = [s for s in subdirs if s not in (".svn", ".git")]
            files.extend(os.path.join(root, n) for n in names
                         if n.lower().endswith((".v", ".sv")))
    return files


def scan_files(paths):
    """多个 .v 文件 → 合并的 {模块名: {...}}（重名模块保留首个）。"""
    modules = {}
    unreadable = 0
    for p in paths:
        try:
            with open(p, "r", encoding="utf-8", errors="replace") as f:
                text = f.read()
        except OSError:
            unreadable += 1
            continue
        for name, info in parse_modules(text).items():
            modules.setdefault(name, info)
    if unreadable:
        print("⚠ %d 个文件读不了（权限/坏链接），已跳过" % unreadable)
    return modules


def build_signal_map(modules, top, max_depth=16, report=None):
    """从顶层模块递归展开实例，返回 {信号名: [层级路径...]}；"" = 顶层。

    report（可选 dict）填入诊断，回答「为什么有些网找不到」——这是定位 missing 的关键：
      report["unresolved"] = {被实例化但没解析到定义的模块名: 出现次数}
                             多半是该模块 .v 不在 --rtl-dirs 范围 → 整棵子树的网全丢。
      report["depth_cut"]  = 因超过 max_depth 而没继续展开的实例数（层级太深被截断）。
    不传 report 时行为与旧版一致（只返回 sigmap）。默认深度 16（旧版 4 对芯片级 dreg 太浅）。
    """
    sigmap = {}
    unresolved = {}
    depth_cut = [0]

    def walk(mod_name, path, depth):
        info = modules.get(mod_name)
        if info is None:
            unresolved[mod_name] = unresolved.get(mod_name, 0) + 1   # 子树整片丢——多半范围漏了
            return
        if depth > max_depth:
            depth_cut[0] += 1                                        # 层级太深被截断
            return
        for s in info["signals"]:
            paths = sigmap.setdefault(s, [])
            if path not in paths:
                paths.append(path)
        for inst, child in info["instances"]:
            walk(child, path + ("." if path else "") + inst, depth + 1)

    walk(top, "", 0)
    if report is not None:
        report["unresolved"] = unresolved
        report["depth_cut"] = depth_cut[0]
    return sigmap


def resolve_top_module(top_module, modules, top_path):
    """顶层模块名容错。

    $dreg_top 是环境配置，RTL 实际声明才是真相，两者大小写/名字可能不一致
    （Verilog 模块名大小写敏感；老项目惯例=文件名小写、模块名大写，新项目未必沿用）。
    精确命中直接返回；否则按「大小写不同 → 顶层文件实际定义」两级自救，
    都救不回来时给出能直接行动的诊断，而不是只让用户去查 --rtl-dirs。
    """
    if top_module in modules:
        return top_module
    # 自救①：RTL 里有大小写不同的同名模块
    ci = {}
    for name in modules:
        ci.setdefault(name.lower(), name)
    hit = ci.get(top_module.lower())
    if hit:
        print("⚠ 顶层模块 %r 不在 RTL 里，但找到大小写不同的 %r —— 按后者继续"
              "（$dreg_top 的大小写与 RTL 实际声明不一致）" % (top_module, hit))
        return hit
    # 自救②：顶层文件本身定义了什么模块
    if not top_path or not os.path.isfile(top_path):
        sys.exit("⛔ 顶层模块 %r 不在已解析模块里，且顶层文件不存在: %s\n"
                 "   （$dreg_dir/$dreg_file 可能没指向真实 RTL，先核对这两个环境变量）"
                 % (top_module, top_path))
    try:
        with open(top_path, "r", encoding="utf-8", errors="replace") as f:
            in_top = list(parse_modules(f.read()))
    except OSError as ex:
        sys.exit("⛔ 顶层模块 %r 不在已解析模块里，且顶层文件读不了: %s（%s）"
                 % (top_module, top_path, ex))
    if in_top:
        print("⚠ 顶层模块 %r 不在 RTL 里；顶层文件 %s 实际定义的是: %s —— 按 %r 继续"
              "（$dreg_top 与 RTL 不一致，建议反馈环境维护人）"
              % (top_module, top_path, ", ".join(in_top), in_top[0]))
        return in_top[0]
    sys.exit("⛔ 顶层模块 %r 不在已解析模块里，顶层文件 %s 里也解析不出任何 module\n"
             "   （可能是宏/转义名等本脚本识别不了的写法——"
             "grep -n 'module' 该文件人工确认后，用 --top-module 指定真实模块名）"
             % (top_module, top_path))


# ───────────────────────────── 信号清单（nets.txt）读写 ─────────────────────────────
def parse_nets_text(text):
    """nets.txt → {网名: 用途说明}。每行: 网名<TAB或空格>用途说明；# 为注释。"""
    nets = {}
    for ln in (text or "").splitlines():
        ln = ln.strip()
        if not ln or ln.startswith("#"):
            continue
        parts = ln.split(None, 1)
        nets[parts[0]] = parts[1] if len(parts) > 1 else ""
    return nets


def render_nets_text(nets):
    """{网名: 用途} → nets.txt 内容。"""
    lines = ["# Dreg 验证需要在 ENV_RF 层级存在的网（由 scan_rtl.py --export-nets 生成）",
             "# 网名<TAB>用途", ""]
    lines += ["%s\t%s" % (n, why) for n, why in sorted(nets.items())]
    return "\n".join(lines) + "\n"


# ───────────────────────────── 对照与输出 ─────────────────────────────
def match_nets(nets, sigmap):
    """对照：需要的网 vs RTL 层级。

    返回 (prefixes, at_top, missing)：
      prefixes: {网名: 层级路径} —— 在子模块里，需要前缀
      at_top:   [网名]           —— 顶层就有，不用配
      missing:  [(网名, 用途)]    —— RTL 里找不到，不可验证
    """
    prefixes, at_top, missing = {}, [], []
    for name in sorted(nets):
        paths = sigmap.get(name)
        if not paths:
            missing.append((name, nets[name]))
        elif "" in paths:
            at_top.append(name)
        else:
            prefixes[name] = paths[0]      # 多处出现时取最浅层级
    return prefixes, at_top, missing


def render_prefix_file(prefixes, at_top, missing, top_module=""):
    """输出 probe_prefixes.txt 内容：可直接被 GUI『导入…』/ CLI --probe-prefix-file 使用。

    用【合并格式】——按层级路径分组（『路径:』一行，其下每行一个信号名）。
    芯片级 dreg 上千个网常挤在同一路径下，分组后路径只写一次，远比逐行『名=路径』短、好读、好填。
    解析端（generator.parse_probe_prefix_lines）两种格式都认，旧扁平文件继续可用。"""
    lines = ["# 由 scan_rtl.py 自动生成 (DUT top: %s)" % top_module,
             "# 合并格式：『层级路径:』单独一行，其下每行一个信号名（均在该路径下）。",
             "# 也兼容旧格式『信号名=路径』。GUI『导入…』/ CLI --probe-prefix-file 都能读。", ""]
    groups = {}
    for name, path in prefixes.items():
        groups.setdefault(path, []).append(name)
    for path in sorted(groups):
        lines.append("%s:" % path)
        lines += ["    %s" % n for n in sorted(groups[path])]
        lines.append("")
    if at_top:
        lines += ["# ── 以下信号在 DUT 顶层就能探到，无需前缀 ──"]
        lines += ["# %s" % n for n in at_top]
        lines.append("")
    if missing:
        lines += ["# ── ⚠ 以下信号在 RTL 中找不到（不可验证，建议反馈 Dreg 团队核对）──"]
        lines += ["# %s    (%s)" % (n, why) for n, why in missing]
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


# ───────────────────────────── CLI ─────────────────────────────
def _load_excel_nets(excel_path):
    """从 Excel 抽取需要定位的网（需要 dreg_verify 包 + openpyxl，只在 Windows 工具机用）。"""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    try:
        from dreg_verify import excel_model, rtl_scan
    except ImportError as ex:
        sys.exit("⛔ --excel 需要 dreg_verify 包和 openpyxl（服务器上请改用 --nets 模式）：%s" % ex)
    print("装载 Excel: %s ..." % excel_path)
    wb = excel_model.load_workbook(excel_path)
    nets = rtl_scan.collect_excel_nets(wb)
    # mux 页的网（2026-06-03：mux 验证环境核查）——从已装载的 wb 取，mux 页不存在时为空，纯 logic 流程不受影响
    mux_nets = rtl_scan.collect_mux_nets(wb)
    added = 0
    for name, why in mux_nets.items():
        if name not in nets:
            nets[name] = why
            added += 1
    if mux_nets:
        print("✓ mux 页: 发现 %d 个相关网，新增导出 %d 个（输出探针 + 控制衔接网 + 数据网）"
              % (len(mux_nets), added))
    # dft 页的 iddq 门网（2026-06-10）——IDDQ 漏电态拍的 force 目标，漏导则门埋子模块时 CUVUNF
    dft_nets = rtl_scan.collect_dft_nets(wb)
    added_dft = 0
    for name, why in dft_nets.items():
        if name not in nets:
            nets[name] = why
            added_dft += 1
    if dft_nets:
        print("✓ dft 页: 发现 %d 个 iddq 门网，新增导出 %d 个（IDDQ 漏电态拍 force 目标）"
              % (len(dft_nets), added_dft))
    return nets


def _infer_from_dreg_env(args):
    """从 dreg 验证环境变量自动推断参数（服务器上用户已 source 过 sourceme，这些变量现成）。

      $dreg_dir  = .../digital/pll/LPBT_DIG_TOP   → DUT 顶层目录
      $dreg_file = lpbt_dig_top                    → 顶层文件名（不含 .v）
      $dreg_top  = LPBT_DIG_TOP                    → 顶层模块名
    缺省 RTL 扫描范围 = $dreg_dir 上两级（即整个 digital/）；信号清单 = ./nets.txt。
    """
    dreg_dir = os.environ.get("dreg_dir")
    dreg_file = os.environ.get("dreg_file")
    dreg_top = os.environ.get("dreg_top")
    if not args.top and dreg_dir and dreg_file:
        args.top = os.path.join(dreg_dir, dreg_file + ".v")
        print("✓ 自动推断 DUT 顶层 ($dreg_dir/$dreg_file.v): %s" % args.top)
    if not args.top_module and dreg_top:
        args.top_module = dreg_top
        print("✓ 自动推断顶层模块 ($dreg_top): %s" % dreg_top)
    if not args.rtl_dirs and dreg_dir:
        # 目标=整个 digital/（dreg 模块常分散在 sub_dreg / 各子块目录，子块 RTL 可能在 sub_dreg 之外）。
        # 旧逻辑固定 $dreg_dir/../.. 只对 digital/pll/TOP 这种两级布局正好=digital；
        # digital/sub_dreg/TOP/src 这种更深布局只会到 sub_dreg → 漏扫同级子块(整片 missing)。
        # 改为：路径里有名为 digital 的祖先就用它（最靠叶子的那个）；没有才退回 ../..。
        norm = os.path.normpath(dreg_dir)
        parts = norm.split(os.sep)
        if "digital" in parts:
            idx = len(parts) - 1 - parts[::-1].index("digital")     # 最靠近叶子的 digital 祖先
            args.rtl_dirs = os.sep.join(parts[:idx + 1]) or os.sep
            print("✓ 自动推断 RTL 扫描范围 (digital/ 祖先): %s" % args.rtl_dirs)
        else:
            args.rtl_dirs = os.path.normpath(os.path.join(dreg_dir, "..", ".."))
            print("✓ 自动推断 RTL 扫描范围 ($dreg_dir/../..): %s" % args.rtl_dirs)
    if not args.nets and not args.excel and os.path.isfile("nets.txt"):
        args.nets = "nets.txt"
        print("✓ 使用当前目录的 nets.txt")


def main():
    ap = argparse.ArgumentParser(
        description="扫描 RTL 自动生成探针前缀映射。服务器上(已 source dreg 环境 + 当前目录有 nets.txt)"
                    "直接运行 python3 scan_rtl.py 即可，全部参数自动推断。",
        epilog="跨机器两段式: ① Windows: scan_rtl.py --excel 真表.xlsx --export-nets nets.txt "
               "② 传 scan_rtl.py + nets.txt 到服务器 ③ 服务器: python3 scan_rtl.py")
    ap.add_argument("--excel", help="Dreg 核心 Excel (.xlsx)（需要 openpyxl；服务器上用 --nets 代替）")
    ap.add_argument("--export-nets", metavar="nets.txt",
                    help="只导出信号清单（在 Windows 跑，产物上传服务器配合 --nets 用）")
    ap.add_argument("--nets", metavar="nets.txt",
                    help="信号清单文件（默认: 当前目录的 nets.txt）")
    ap.add_argument("--top", help="DUT 顶层 .v 文件（默认: $dreg_dir/$dreg_file.v）")
    ap.add_argument("--top-module", default=None,
                    help="顶层模块名（默认: $dreg_top 或 --top 文件里的第一个 module）")
    ap.add_argument("--rtl-dirs", help="RTL 目录（逗号分隔；默认: $dreg_dir 上两级 = 整个 digital/）")
    ap.add_argument("--out", default="probe_prefixes.txt",
                    help="输出映射文件 (默认 probe_prefixes.txt)")
    ap.add_argument("--max-depth", type=int, default=16,
                    help="层级展开深度 (默认 16；芯片级 dreg 层级很深，调小会漏扫深层网)")
    args = ap.parse_args()

    # ── 模式①：仅导出信号清单（Windows，有 Excel）──
    if args.export_nets:
        if not args.excel:
            sys.exit("⛔ --export-nets 需要配合 --excel")
        nets = _load_excel_nets(args.excel)
        with open(args.export_nets, "w", encoding="utf-8") as f:
            f.write(render_nets_text(nets))
        print("信号清单已写出: %s（共 %d 个网）" % (args.export_nets, len(nets)))
        print("下一步: 把 scan_rtl.py + %s 上传到服务器(放运行目录)，source dreg 环境后直接跑:" % args.export_nets)
        print("  python3 scan_rtl.py")
        return 0

    # ── 模式②/③：扫描 RTL（--nets 零依赖 / --excel 单机）──
    _infer_from_dreg_env(args)      # 服务器：从 $dreg_dir/$dreg_file/$dreg_top 自动推断
    if not args.top or not args.rtl_dirs:
        sys.exit("⛔ 缺少 --top / --rtl-dirs。两种解决方式：\n"
                 "  ① 先 source dreg 环境(设置 $dreg_dir/$dreg_file/$dreg_top)再运行（推荐）\n"
                 "  ② 手动指定: --top <DUT顶层.v> --rtl-dirs <RTL目录>")
    if args.nets:
        with open(args.nets, "r", encoding="utf-8") as f:
            nets = parse_nets_text(f.read())
        print("信号清单: %s（%d 个网）" % (args.nets, len(nets)))
    elif args.excel:
        nets = _load_excel_nets(args.excel)
    else:
        sys.exit("⛔ 找不到信号清单。把 nets.txt 放到当前目录（或用 --nets 指定路径）；"
                 "单机模式用 --excel 指定真表。")

    dirs = [d.strip() for d in args.rtl_dirs.split(",") if d.strip()]
    files = find_verilog_files(dirs)
    if args.top not in files:
        files.append(args.top)
    print("RTL 文件: %d 个" % len(files))

    modules = scan_files(files)
    print("解析到模块: %d 个" % len(modules))

    top_module = args.top_module
    if not top_module:
        with open(args.top, "r", encoding="utf-8", errors="replace") as f:
            parsed = parse_modules(f.read())
        if not parsed:
            sys.exit("⛔ 在 %s 里找不到 module 定义" % args.top)
        top_module = list(parsed)[0]
    top_module = resolve_top_module(top_module, modules, args.top)
    print("DUT 顶层模块: %s" % top_module)

    report = {}
    sigmap = build_signal_map(modules, top_module, max_depth=args.max_depth, report=report)
    print("RTL 信号总数: %d" % len(sigmap))
    # 诊断「为什么有些网 missing」：①被实例化但没定义的模块=范围漏了 ②深度截断=层级太深
    unresolved = report.get("unresolved") or {}
    if unresolved:
        print("⚠ %d 个被实例化的模块【找不到定义】——它们整棵子树的网都无法定位"
              "（多半 .v 不在 --rtl-dirs 内，按出现次数排序，前 20）:" % len(unresolved))
        for m, c in sorted(unresolved.items(), key=lambda kv: (-kv[1], kv[0]))[:20]:
            print("    %s（被实例化 %d 次）" % (m, c))
        if len(unresolved) > 20:
            print("    …… 还有 %d 个未列出" % (len(unresolved) - 20))
        print("  → 若你要的网就埋在这些模块里：把它们的 RTL 目录加进 --rtl-dirs 重扫。")
    if report.get("depth_cut"):
        print("⚠ %d 处实例因超过 --max-depth=%d 未继续展开（层级太深被截断）"
              "——missing 偏多时先把 --max-depth 调大重扫。" % (report["depth_cut"], args.max_depth))

    prefixes, at_top, missing = match_nets(nets, sigmap)
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(render_prefix_file(prefixes, at_top, missing, top_module=top_module))

    print("")
    print("✅ 顶层可直接探到 : %4d 个（无需配置）" % len(at_top))
    print("🔧 需要探针前缀   : %4d 个（已写入映射）" % len(prefixes))
    print("⚠ RTL 中找不到   : %4d 个（不可验证，见文件尾部注释）" % len(missing))
    print("")
    print("映射文件已写出: %s" % args.out)
    print("用法: 拷回 Windows → GUI『设置探针前缀 → 导入…』；或 CLI 加 --probe-prefix-file %s" % args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
