# -*- coding: utf-8 -*-
"""
rtl_scan.py — 静态扫描 RTL，找出 Excel 中每个信号的真实层级路径，自动生成探针前缀映射。

解决的工作流问题（2026-06-02 用户）：
    生成 .sv → 仿真 → CUVUNF → grep 找信号在哪 → 配前缀 → 重新生成 → … 每个信号一轮
Excel 完全没有层级信息（logic 表只有逻辑关系、tmm 只有寄存器地址），层级只存在于 RTL：
    `ENV_RF.pll_n           不存在
    `ENV_RF.U_BT_LP_PLL_DIG.pll_n   存在（在子模块里）
本模块直接扫描 RTL 文件，把所有信号的层级一次找全 → 仿真一次就过。

用法（CLI 包装见仓库根目录 scan_rtl.py）：
    modules = scan_files(verilog_files)
    sigmap  = build_signal_map(modules, top="LPBT_DIG_TOP")
    prefixes, at_top, missing = match_excel(wb, sigmap)
"""

import os
import re


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
    for p in paths:
        try:
            with open(p, "r", encoding="utf-8", errors="replace") as f:
                text = f.read()
        except OSError:
            continue
        for name, info in parse_modules(text).items():
            modules.setdefault(name, info)
    return modules


def build_signal_map(modules, top, max_depth=4):
    """从顶层模块递归展开实例，返回 {信号名: [层级路径...]}；"" = 顶层。"""
    sigmap = {}

    def walk(mod_name, path, depth):
        info = modules.get(mod_name)
        if info is None or depth > max_depth:
            return
        for s in info["signals"]:
            paths = sigmap.setdefault(s, [])
            if path not in paths:
                paths.append(path)
        for inst, child in info["instances"]:
            walk(child, path + ("." if path else "") + inst, depth + 1)

    walk(top, "", 0)
    return sigmap


def collect_excel_nets(wb):
    """Excel → 需要在 ENV_RF 层级存在的网名集合：

    ① 每个 top_output=1 输出的 RTL 网名（含 _ls 后缀规则）—— assert 探针
    ② 它们的 force 输入（RO/wire 兜底/级联）—— force 路径
    RW 寄存器输入走 RF_WRITE，不需要层级。返回 {网名: 用途说明}。
    """
    from . import expr as E
    from . import generator
    from . import resolver as R

    resolver = R.Resolver(wb)
    nets = {}
    for sig in wb.logic:
        if not generator.is_top_output(sig.top_output):
            continue
        nets.setdefault(sig.rtl_base, "输出 %s 的 assert 探针" % sig.out_name)
        try:
            node, bindings, _ = generator.expand_signal(wb, resolver, sig)
        except Exception:  # noqa: BLE001  cone 失败时退回原始绑定
            try:
                node = E.parse(sig.expr)
                bindings = resolver.resolve_signal_inputs(sig)
            except Exception:  # noqa: BLE001
                continue
        for b in bindings.values():
            if b.kind == "RO":
                base = b.wire.split(".")[-1]
                nets.setdefault(base, "输出 %s 的输入 %s (force)" % (sig.out_name, b.base))
    return nets


def match_excel(wb, sigmap):
    """对照：Excel 需要的网 vs RTL 层级。

    返回 (prefixes, at_top, missing)：
      prefixes: {网名: 层级路径} —— 在子模块里，需要前缀
      at_top:   [网名]           —— 顶层就有，不用配
      missing:  [(网名, 用途)]    —— RTL 里找不到，不可验证
    """
    nets = collect_excel_nets(wb)
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
    """输出 probe_prefixes.txt 内容：可直接被 GUI『导入…』/ CLI --probe-prefix-file 使用。"""
    lines = ["# 由 scan_rtl.py 自动生成 (DUT top: %s)" % top_module,
             "# 每行: 信号名=ENV_RF 之下的层级路径", ""]
    for name, path in sorted(prefixes.items()):
        lines.append("%s=%s" % (name, path))
    if at_top:
        lines += ["", "# ── 以下信号在 DUT 顶层就能探到，无需前缀 ──"]
        lines += ["# %s" % n for n in at_top]
    if missing:
        lines += ["", "# ── ⚠ 以下信号在 RTL 中找不到（不可验证，建议反馈 Dreg 团队核对）──"]
        lines += ["# %s    (%s)" % (n, why) for n, why in missing]
    return "\n".join(lines) + "\n"
