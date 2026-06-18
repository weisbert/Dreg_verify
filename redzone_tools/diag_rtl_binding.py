# -*- coding: utf-8 -*-
"""
diag_rtl_binding.py — 红区只读诊断（goal-redzone-binder 里程碑 M1）

拿 dreg 工具导出的 claims.json + 真 RTL，逐【探针】报告：
  · 存在性/层级：探针网在不在 RTL、在顶层还是子模块、和 claim 里配的前缀一致否
  · 输出 vs 输入：探针网是【被驱动的输出】(assign 左边) 还是【只被读的输入】(出现在右边)——
    后者 = 探到了自己的输入网 = 假绿嫌疑（R32/R40 那一类的 headline）
  · 真输出建议：疑似输入时，给出本信号 assign 的左边网名（= 应该探的真输出），
    多个同前缀候选用 claim 的 input_nets 做指纹择优

【纯只读、不改任何东西、零第三方依赖】。RTL 解析复用同目录的 scan_rtl.py。

⚠ 这是【验证用】诊断：assign 解析本身易错（拼接/generate/跨层重名/端口连接驱动）。
   判不准的一律标 UNKNOWN、绝不当成"没问题"——先在真 RTL 上人工核对它判得对不对，
   再谈让 binder 据此自动改 .sv（那是 M3）。本脚本不输出、不导出、不联网，可放心在红区跑。

═══ 用法（红区，已 source dreg 环境时全自动）═══
    python3 diag_rtl_binding.py --claims claims.json
  或手动指定 RTL：
    python3 diag_rtl_binding.py --claims claims.json --top <顶层.v> --rtl-dirs <目录>
"""

import argparse
import json
import os
import re
import sys

try:                                  # 复用 scan_rtl 的 RTL 解析（同目录）
    import scan_rtl
except ImportError:                   # 纯函数单测不依赖 scan_rtl 也能跑
    scan_rtl = None

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass


# ───────────────────────────── assign / always 解析（纯标准库） ─────────────────────────────
_IDENT = r"[A-Za-z_][\w$]*"
_IDENT_RE = re.compile(_IDENT)
# Verilog 数字字面量（4'd3 / 8'hFF / 'b0 / 纯十进制）——抽 RHS 标识符前先抹掉，否则 'd3 的 d3 会被当网名
_NUM_RE = re.compile(r"\b\d+'[sS]?[bBoOdDhH][0-9a-fA-F_xXzZ?]+|'[sS]?[bBoOdDhH][0-9a-fA-F_xXzZ?]+|\b\d+\b")
# assign 语句：assign <LHS> = <RHS> ;  （非贪婪到第一个分号；DOTALL 容多行）
_ASSIGN_RE = re.compile(r"\bassign\b\s+(.+?);", re.S)
# always 块里的 LHS：行首/分号/begin/) 之后的  名字(可带位选/拼接) <= 或 =（排除 ==、<=、>=、!= 的右半）
_ALWAYS_LHS_RE = re.compile(
    r"(?:^|[;)]|\bbegin\b)\s*(\{[^}]*\}|%s(?:\s*\[[^\]]*\])?)\s*(?:<=|=)(?![=])" % _IDENT, re.M)
# 端口方向声明（带方向，用于"显式 input 端口"判据）
_PORTDIR_RE = re.compile(r"\b(input|output|inout)\b\s*(?:reg\s+|wire\s+|signed\s+)?(?:\[[^\]]*\]\s*)?(%s)" % _IDENT)

_KW = (scan_rtl.KEYWORDS if scan_rtl else {
    "module", "endmodule", "input", "output", "inout", "wire", "reg", "logic",
    "assign", "always", "initial", "begin", "end", "if", "else", "case", "casez",
    "casex", "endcase", "for", "while", "parameter", "localparam", "generate",
    "endgenerate", "genvar", "function", "endfunction", "task", "endtask",
    "supply0", "supply1", "integer", "real", "signed", "unsigned", "posedge",
    "negedge", "or", "and", "not", "default", "defparam", "specify", "endspecify"})


def _strip_comments(text):
    if scan_rtl:
        return scan_rtl.strip_comments(text)
    text = re.sub(r"/\*.*?\*/", " ", text, flags=re.S)
    return re.sub(r"//[^\n]*", " ", text)


def _name(tok):
    """name[3:0] / name -> name（取首个标识符）。"""
    m = _IDENT_RE.match(tok.strip())
    return m.group(0) if m else ""


def _lhs_bases(lhs):
    """assign/always 的 LHS -> 被驱动的基名列表。处理拼接 {a, b[1:0], c}。"""
    lhs = lhs.strip()
    inner = lhs[1:-1] if lhs.startswith("{") and lhs.endswith("}") else lhs
    bases = []
    for part in inner.split(","):
        b = _name(part)
        if b and b not in _KW:
            bases.append(b)
    return bases


def _rhs_names(rhs):
    """RHS 表达式 -> 引用到的标识符基名集合（去掉关键字与数字字面量）。"""
    rhs = _NUM_RE.sub(" ", rhs)
    return {m.group(0) for m in _IDENT_RE.finditer(rhs) if m.group(0) not in _KW}


def parse_assigns(text):
    """整文件/模块文本 -> [{lhs_bases, lhs_raw, rhs_names, rhs_raw}]（仅 assign，高可信）。"""
    text = _strip_comments(text)
    out = []
    for m in _ASSIGN_RE.finditer(text):
        body = m.group(1)
        if "=" not in body:
            continue
        lhs_raw, rhs_raw = body.split("=", 1)      # assign 的 '=' 必是第一个（LHS 无比较运算）
        bases = _lhs_bases(lhs_raw)
        if not bases:
            continue
        out.append({"lhs_bases": bases, "lhs_raw": lhs_raw.strip(),
                    "rhs_names": _rhs_names(rhs_raw), "rhs_raw": rhs_raw.strip()})
    return out


def parse_always_lhs(text):
    """always 块里被赋值的基名集合（启发式、次可信）——只用于"是否曾被驱动"兜底、降误报。"""
    text = _strip_comments(text)
    bases = set()
    for m in _ALWAYS_LHS_RE.finditer(text):
        bases.update(_lhs_bases(m.group(1)))
    return bases


def parse_port_dirs(text):
    """文本 -> ({显式 input 端口名}, {显式 output 端口名})。判"探针探到纯输入端口"用。"""
    ins, outs = set(), set()
    for m in _PORTDIR_RE.finditer(_strip_comments(text)):
        d, n = m.group(1), m.group(2)
        if n in _KW:
            continue
        (ins if d == "input" else outs).add(n)
    return ins, outs


# ───────────────────────────── 诊断上下文 + 单信号诊断 ─────────────────────────────
def _sig_base(name):
    """信号名去掉位宽 [..] -> 基名（小写）。"""
    return re.split(r"\[", str(name or ""), maxsplit=1)[0].strip().lower()


def build_context(texts, sigmap=None):
    """从若干 RTL 文本（+可选 scan_rtl 的 sigmap）建诊断上下文。sigmap: {网名: [层级路径]}。"""
    assigns, always_lhs, inputs, outputs = [], set(), set(), set()
    for t in texts:
        assigns.extend(parse_assigns(t))
        always_lhs |= parse_always_lhs(t)
        i, o = parse_port_dirs(t)
        inputs |= i
        outputs |= o
    all_lhs = {b.lower() for a in assigns for b in a["lhs_bases"]} | {b.lower() for b in always_lhs}
    pure_inputs = {n.lower() for n in inputs} - all_lhs - {n.lower() for n in outputs}
    # 真实网域(小写→原大小写)：sigmap(声明+层级)的网 ∪ assign 左边网。
    # 给"裸名 missing 但带 _to_logic/_to_mux/_bN 后缀的真网其实存在"搜候选用。
    universe_real = {k.lower(): k for k in (sigmap or {})}
    for a in assigns:
        for b in a["lhs_bases"]:
            universe_real.setdefault(b.lower(), b)
    return {"assigns": assigns, "all_lhs": all_lhs, "pure_inputs": pure_inputs,
            "sigmap_lc": {k.lower(): v for k, v in (sigmap or {}).items()},
            "sigmap_real": {k.lower(): k for k in (sigmap or {})},
            "universe_real": universe_real}


# 已知输出尾缀（优先级从高到低）：规范真输出 = <信号基名>+其中之一，压过带 _tN/_gN 中缀的子变体。
_KNOWN_SUF = ("_to_mux", "_to_logic", "_ls")


def diagnose(claim, ctx):
    """单条 probe claim 的诊断结果。"""
    base = (claim.get("net_base") or "").lower()
    sig_base = _sig_base(claim.get("signal"))
    prefix = (claim.get("prefix") or "").strip()
    in_nets = {n.lower() for n in (claim.get("input_nets") or [])}
    r = {"signal": claim.get("signal"), "probe_net": claim.get("net_base"),
         "prefix": prefix, "on_missing": claim.get("on_missing") or "warn",
         "exists": None, "location": None, "prefix_check": None,
         "verdict": "unknown", "evidence": "", "real_output": None,
         "candidates": []}

    # ① 存在性 / 层级（复用 scan_rtl 的 sigmap = 声明 + 实例层级）。
    #    被 assign/always 驱动的网必然存在，即使没显式 wire 声明、sigmap 漏掉 → 不误报 missing。
    paths = ctx["sigmap_lc"].get(base)
    driven = base in ctx["all_lhs"]
    if ctx["sigmap_lc"] or driven:
        if paths:
            r["exists"] = True
            r["location"] = "top" if "" in paths else ("sub:" + paths[0])
            # 配了前缀就核它和真层级一致否（前缀末段应能在真路径里找到）
            if prefix:
                want = prefix.split(".")[-1].lower()
                hit = any(want in p.lower().split(".") for p in paths if p)
                r["prefix_check"] = "ok" if (hit or "" in paths) else "mismatch"
        elif driven:
            r["exists"], r["location"] = True, "driven(层级未定位)"
        else:
            r["exists"], r["location"] = False, "missing"

    # 全网域候选：RTL 里以信号基名打头的真实网（含 _to_logic/_to_mux/_bN 等后缀真名）。
    # 裸名 missing 时这是"真网其实在这儿"的关键线索。
    r["candidates"] = sorted(v for k, v in ctx.get("universe_real", {}).items()
                             if sig_base and k.startswith(sig_base) and k != base)

    # 规范真输出：RTL 里恰为 <信号基名>+<已知尾缀> 的那根（如 X_to_mux），压过 X_tN_to_mux 等子变体。
    canon = None
    for _suf in _KNOWN_SUF:
        hit = ctx.get("universe_real", {}).get(sig_base + _suf) if sig_base else None
        if hit:
            canon = hit
            break

    # ② 输出 vs 输入：先找"本信号"的 assign（LHS 基名以信号基名打头，含 _to_xxx 真名）
    cand = [a for a in ctx["assigns"]
            if any(lb.lower().startswith(sig_base) for lb in a["lhs_bases"])] if sig_base else []
    best = None
    if cand:
        if in_nets and len(cand) > 1:                     # 指纹：RHS 命中 input_nets 最多者
            cand.sort(key=lambda a: len(in_nets & {n.lower() for n in a["rhs_names"]}), reverse=True)
        best = cand[0]

    if best is not None:
        lhs_lc = [b.lower() for b in best["lhs_bases"]]
        rhs_lc = {n.lower() for n in best["rhs_names"]}
        if base in lhs_lc:
            r["verdict"], r["evidence"] = "output", "探针网 = 本信号 assign 的左边（真输出）"
        elif base in rhs_lc:
            r["verdict"] = "input-suspect"
            r["evidence"] = "探针网出现在本信号 assign 的【右边】= 它是输入 → 假绿嫌疑"
            r["real_output"] = next((b for b in best["lhs_bases"]
                                     if b.lower().startswith(sig_base)), best["lhs_bases"][0])
        else:
            r["verdict"] = "unknown"
            if canon:
                r["evidence"] = ("探针探裸名(RTL 无此网)，真网带尾缀 → 应改探 %s"
                                 "（开 mux/logic 尾缀或配前缀到该网）" % canon)
                r["real_output"] = canon
            else:
                r["evidence"] = ("找到本信号 assign（左边 %s），但探针网既不在左也不在右 → 人工核对"
                                 % "/".join(best["lhs_bases"]))
                r["real_output"] = next((b for b in best["lhs_bases"]
                                         if b.lower().startswith(sig_base)), best["lhs_bases"][0])
    else:
        # 没找到本信号 assign：保守兜底，只在有【正面证据】时下结论
        if base in ctx["all_lhs"]:
            r["verdict"], r["evidence"] = "output", "探针网是某 assign/always 的左边（但没匹到本信号 assign，建议核对）"
        elif base in ctx["pure_inputs"]:
            r["verdict"], r["evidence"] = "input-suspect", "探针网被声明为 input 端口且从不被驱动 → 输入网嫌疑"
        else:
            r["verdict"] = "unknown"
            if canon:
                r["evidence"] = ("探针探裸名(RTL 无此网)，真网带尾缀 → 应改探 %s"
                                 "（开 mux/logic 尾缀或配前缀到该网）" % canon)
                r["real_output"] = canon
            else:
                r["evidence"] = ("找不到本信号 assign、探针网也非显式 input；"
                                 "驱动可能来自端口连接（本诊断未解析）→ 人工核对")
    return r


def diagnose_claims(claims, ctx):
    return [diagnose(c, ctx) for c in claims if c.get("kind") == "probe"]


# ───────────────────────────── 报告 ─────────────────────────────
_MARK = {"output": "✓ OUTPUT", "input-suspect": "⚠ INPUT-suspect(假绿嫌疑)", "unknown": "? UNKNOWN"}


def _detail_lines(r):
    out = ["[%s] %s" % (_MARK.get(r["verdict"], r["verdict"]), r["signal"])]
    if r["location"] is not None:
        ex = "存在" if r["exists"] else "❌ RTL 中找不到"
        pc = {"ok": "前缀一致", "mismatch": "⚠ 前缀对不上真层级"}.get(r["prefix_check"], "")
        out.append("    探针网=%s  位置=%s(%s) %s" % (r["probe_net"], r["location"], ex, pc))
    out.append("    判定：%s" % r["evidence"])
    if r["real_output"] and r["verdict"] != "output":
        out.append("    → 真输出建议探：%s" % r["real_output"])
    if r["candidates"]:
        cs = r["candidates"]
        shown = ", ".join(cs[:12]) + ("  …(共 %d)" % len(cs) if len(cs) > 12 else "")
        out.append("    RTL 同基名真实网：%s" % shown)
    return out


def format_report(results, only=None):
    """摘要置顶 + 按判定分组：问题项(suspect/unknown)详列、OUTPUT 仅列名（它们是好的）。
    only=判定集合时只渲染这些组（摘要计数仍含全部）。"""
    by = {"input-suspect": [], "unknown": [], "output": []}
    for r in results:
        by.setdefault(r["verdict"], []).append(r)
    show = set(only) if only else {"input-suspect", "unknown", "output"}
    lines = ["=" * 64,
             "汇总：⚠INPUT-suspect %d   ?UNKNOWN %d   ✓OUTPUT %d   （共 %d 探针）"
             % (len(by["input-suspect"]), len(by["unknown"]), len(by["output"]), len(results)),
             "=" * 64]
    for v in ("input-suspect", "unknown"):
        if v not in show or not by[v]:
            continue
        lines += ["", "──── %s （%d）────" % (_MARK[v], len(by[v]))]
        for r in sorted(by[v], key=lambda x: x["signal"] or ""):
            lines += _detail_lines(r)
            lines.append("")
    if "output" in show and by["output"]:
        lines += ["", "──── ✓ OUTPUT （%d，已认定探到真输出，仅列名）────" % len(by["output"])]
        lines += ["    %s" % r["signal"] for r in sorted(by["output"], key=lambda x: x["signal"] or "")]
    lines += ["", "⚠ 只读诊断：INPUT-suspect / UNKNOWN 请逐条对真 RTL 人工核对再处理。"]
    return "\n".join(lines)


# ───────────────────────────── CLI ─────────────────────────────
def main():
    ap = argparse.ArgumentParser(
        description="红区只读诊断：拿 claims.json + 真 RTL，判每根探针是真输出还是探到了自己输入(假绿)。"
                    "已 source dreg 环境时 RTL 参数自动推断。")
    ap.add_argument("--claims", required=True, help="dreg 工具 --export-claims 导出的 claims.json")
    ap.add_argument("--top", help="DUT 顶层 .v（默认 $dreg_dir/$dreg_file.v）")
    ap.add_argument("--top-module", default=None, help="顶层模块名（默认 $dreg_top）")
    ap.add_argument("--rtl-dirs", help="RTL 目录（逗号分隔；默认 $dreg_dir 上的 digital/）")
    ap.add_argument("--max-depth", type=int, default=16)
    ap.add_argument("--out", help="把完整报告写到文件（留在红区里 grep/翻页用，不往外导）")
    ap.add_argument("--only", help="只看某些判定：逗号分隔 suspect,unknown,output（缺省全看；摘要计数始终含全部）")
    ap.add_argument("--nets", help=argparse.SUPPRESS)  # 占位，与 scan_rtl 自动推断兼容
    ap.add_argument("--excel", help=argparse.SUPPRESS)
    args = ap.parse_args()

    if scan_rtl is None:
        sys.exit("⛔ 需要同目录的 scan_rtl.py（复用其 RTL 解析）")
    with open(args.claims, "r", encoding="utf-8-sig") as f:   # 容 BOM（Windows 编辑/传输可能带）
        data = json.load(f)
    claims = data.get("claims", data if isinstance(data, list) else [])
    probes = [c for c in claims if c.get("kind") == "probe"]
    print("claims: %s（%d 探针）" % (os.path.basename(args.claims), len(probes)))

    scan_rtl._infer_from_dreg_env(args)
    if not args.top or not args.rtl_dirs:
        sys.exit("⛔ 缺 --top / --rtl-dirs（先 source dreg 环境，或手动指定）")
    dirs = [d.strip() for d in args.rtl_dirs.split(",") if d.strip()]
    files = scan_rtl.find_verilog_files(dirs)
    if args.top not in files:
        files.append(args.top)
    print("RTL 文件: %d 个" % len(files))

    texts = []
    for p in files:
        try:
            with open(p, "r", encoding="utf-8", errors="replace") as f:
                texts.append(f.read())
        except OSError:
            pass
    modules = scan_rtl.scan_files(files)
    top_module = scan_rtl.resolve_top_module(
        args.top_module or (list(scan_rtl.parse_modules(texts[-1]))[0] if texts else ""),
        modules, args.top)
    sigmap = scan_rtl.build_signal_map(modules, top_module, max_depth=args.max_depth)
    print("DUT 顶层: %s  RTL 网总数: %d  assign 语句: %d\n"
          % (top_module, len(sigmap), sum(len(parse_assigns(t)) for t in texts)))

    ctx = build_context(texts, sigmap=sigmap)
    only = None
    if args.only:
        _m = {"suspect": "input-suspect", "input-suspect": "input-suspect",
              "unknown": "unknown", "output": "output"}
        only = {_m.get(x.strip().lower()) for x in args.only.split(",")} - {None}
    report = format_report(diagnose_claims(probes, ctx), only=only)
    print(report)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(report + "\n")
        print("\n报告已写出: %s（留在红区，grep/翻页用）" % args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
