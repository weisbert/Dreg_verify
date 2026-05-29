# -*- coding: utf-8 -*-
"""
cli.py — 命令行入口：从 Dreg 核心 Excel 生成 wr_rf_tc.sv。

用法示例:
  # 生成全部信号
  python -m dreg_verify.cli --excel core.xlsx --out wr_rf_tc.sv

  # 只生成某 owner 的信号
  python -m dreg_verify.cli --excel core.xlsx --owner Alice --out wr_rf_tc.sv

  # 给某些信号加负向(异常)用例，取反造错，单独出文件
  python -m dreg_verify.cli --excel core.xlsx --neg-signals d_logic_bt_lp_reserve \
      --neg-mode invert --neg-file separate --out wr_rf_tc.sv

  # 列出信号清单(不生成)
  python -m dreg_verify.cli --excel core.xlsx --list
"""

import argparse
import os
import sys

# Windows 控制台常是 GBK，打印中文会崩；统一改 UTF-8（文件输出本就 UTF-8）
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dreg_verify import excel_model, generator  # noqa: E402


def _split(s):
    if not s:
        return None
    return [x.strip() for x in s.replace(";", ",").split(",") if x.strip()]


def build_argparser():
    p = argparse.ArgumentParser(
        prog="dreg_verify",
        description="从 Dreg 核心 Excel 的 logic 真值表达式生成 wr_rf_tc.sv 验证文件。")
    p.add_argument("--excel", required=True, help="核心 Excel (.xlsx) 路径")
    p.add_argument("--out", default=None, help="输出 .sv 路径 (默认 wr_rf_tc.sv；只想出报告时不传它)")
    p.add_argument("--report", default=None,
                   help="导出'给人看'的测试用例表格(按扩展名: .csv 用 Excel 打开 / .html 网页)。"
                        "可与 --out 同时用(同时出 .sv 和报告)；只传 --report 则只出报告")
    p.add_argument("--list", action="store_true", help="只列出可生成信号清单，不生成")
    p.add_argument("--comments", action="store_true",
                   help="在 .sv 里加少量导航注释(文件头 + 每信号 1 行 // 名)；默认零注释(对齐真实模板)")
    p.add_argument("--diagnose", action="store_true",
                   help="覆盖诊断: 实测各输入被解析成 force(RO)/RF_WRITE(RW)/未知, "
                        "类型列有哪些写法, 有无 >16bit 输入(驱动会截断), 不生成")

    g = p.add_argument_group("信号筛选")
    g.add_argument("--owner", help="按 owner 筛选(逗号分隔，匹配 logic P 列)")
    g.add_argument("--signals", help="按信号名筛选(逗号分隔，K 全名或去位宽基名)")
    g.add_argument("--regex", help="按信号名正则筛选")
    g.add_argument("--exclude", help="排除这些信号(逗号分隔，K 全名或去位宽基名)")
    g.add_argument("--exclude-regex", help="按正则排除信号(如 'pll_n|_to_dsm' 排掉 datapath 中间信号)")
    g.add_argument("--type", help="按 type/suffix(M 列)筛选，如 to_mux,ls")
    g.add_argument("--include-internal", action="store_true",
                   help="连 top_output=0 的内部信号也生成（默认只生成 top_output=1 的可验证输出；"
                        "内部信号在 RTL/ENV_RF 层探不到，会导致 elaboration 层级查找失败）")
    g.add_argument("--top-output-only", action="store_true",
                   help="（已是默认行为，保留兼容）只取 top_output=1")

    g2 = p.add_argument_group("测试向量")
    g2.add_argument("--mode", choices=["min", "max"], default="min",
                    help="向量密度: min(默认)=控制全组合×1数据特征; max=多数据模式")
    g2.add_argument("--max-tests", type=int, default=256, help="单信号向量上限(默认256)")
    g2.add_argument("--exhaustive", action="store_true",
                    help="总输入位很少时做真·全穷举")

    g3 = p.add_argument_group("负向(异常)用例")
    g3.add_argument("--neg-signals", help="对这些信号追加负向用例(逗号分隔)")
    g3.add_argument("--neg-all", action="store_true", help="对所有选中信号加负向用例")
    g3.add_argument("--neg-mode", choices=["invert", "inc", "value"], default="invert",
                    help="造错方式: invert(按位取反,默认)/inc(+1)/value(固定值)")
    g3.add_argument("--neg-value", type=lambda x: int(x, 0), default=None,
                    help="--neg-mode value 时的固定错误值(支持0x前缀)")
    g3.add_argument("--neg-which", choices=["first", "all"], default="first",
                    help="对每个选中信号: first(仅第一个向量,默认)/all(每个向量)")
    g3.add_argument("--neg-file", choices=["inline", "separate"], default="inline",
                    help="负向用例放同文件(inline,默认)还是单独 *_neg.sv(separate)")

    g4 = p.add_argument_group("类型覆盖(解决名称/RO-RW判定问题)")
    g4.add_argument("--force-signals", help="强制按 RO(force) 处理的基名(逗号分隔)")
    g4.add_argument("--rfwrite-signals", help="强制按 RW(RF_WRITE) 处理的基名(逗号分隔)")
    g4.add_argument("--default-kind", choices=["RO", "RW"], default=None,
                    help="类型判不出时的兜底(默认保持未解析并报告)")
    g4.add_argument("--no-wire-fallback", action="store_true",
                    help="关闭 wire 兜底：非 RW 寄存器且查不到的输入不再默认 force，而是标 UNKNOWN 交人工")
    g4.add_argument("--include-risky", action="store_true",
                    help="强制生成含'不可驱动输入'(wire兜底/未解析)的信号（默认跳过，因为 force 不存在的 net "
                         "会导致 elaboration CUVUNF 失败；与 VBA 一致默认跳过这类信号）")
    g4.add_argument("--match-fortest", action="store_true",
                    help="复刻 for_test 覆盖面: = --include-internal + --include-risky, 不跳过任何信号"
                         "(含 top_output=0 内部信号、含 close_ready_flag 等不可驱动 wire 的信号)。"
                         "用于和 for_test 逐信号对照；产物可能 CUVUNF 跑不起来(预期内)。")
    return p


def cmd_list(wb, opts):
    sigs = generator.select_signals(wb, opts)
    print("可生成信号 %d / logic 总行 %d (tmm字段=%d, regmap信号=%d)"
          % (len(sigs), len(wb.logic), len(wb.tmm), len(wb.regmap)))
    print("%-5s %-40s %-12s %-12s %-3s %s" % ("R", "输出名(K)", "owner", "type", "top", "表达式"))
    print("-" * 100)
    for s in sigs:
        print("%-5s %-40s %-12s %-12s %-3s %s"
              % (s.assert_id, s.out_name[:40], (s.owner or "")[:12],
                 (s.suffix or "")[:12], s.top_output, s.expr[:50]))


def cmd_diagnose(wb, opts):
    d = generator.diagnose(wb, opts)
    print("\n===== 覆盖诊断 =====")
    print("参与诊断信号: %d" % d["n_signals"])
    print("\n[total_memory_map H 列(类型)原文分布]  ← 看是否只有 RO/RW 系，有无没覆盖的写法")
    for k, v in d["tmm_type_raw"].items():
        print("   %-16s x %d" % (k, v))
    print("[regmap F 列(Reg Type)原文分布]")
    for k, v in d["regmap_type_raw"].items():
        print("   %-16s x %d" % (k, v))
    print("[total_memory_map D 列(DIG TOP PIN)分布]")
    for k, v in d["tmm_dig_top_pin"].items():
        print("   %-16s x %d" % (k, v))

    c = d["cats"]
    total = sum(c.values())
    print("\n[输入驱动方式分类]")
    print("   RF_WRITE (RW 寄存器)       : %d" % c["rfwrite"])
    print("   force - RO 寄存器/管脚      : %d" % c["force_ro"])
    print("   force - 级联中间信号(logic 输出): %d" % c["force_chained"])
    print("   force - wire 兜底(表中查无)  : %d  ← 需你确认这些确实是 wire" % c["force_wire"])
    print("   UNKNOWN (仍无法处理)        : %d" % c["unknown"])
    if total:
        ok = total - c["unknown"]
        print("   → 可生成(force/RF_WRITE)覆盖率: %.1f%% (%d/%d)" % (100.0 * ok / total, ok, total))

    if d["wide_inputs"]:
        print("\n[>16bit 输入] force 会按位宽自适应(如 32'h)不截断；但若它是 RW 寄存器，RF_WRITE 仍 16'h 受限:")
        for name, ltr, base, w, kind, src in d["wide_inputs"][:30]:
            print("   %s 的 %s=%s (%dbit, %s, %s)" % (name, ltr, base, w, kind, src))
        if len(d["wide_inputs"]) > 30:
            print("   ...(共 %d 条)" % len(d["wide_inputs"]))
    else:
        print("\n（无 >16bit 输入）")

    if d["fallback_wires"]:
        print("\n⚠ [wire 兜底] 表中查无、按 wire 直接 force 的输入——请确认它们确实是 wire/管脚/中间信号；")
        print("   若其实是寄存器，用 --rfwrite-signals 指定，否则会 force 而非 RF_WRITE:")
        for name, ltr, base, w in d["fallback_wires"][:40]:
            print("   %s 的 %s=%s (%dbit)" % (name, ltr, base, w))
        if len(d["fallback_wires"]) > 40:
            print("   ...(共 %d 条)" % len(d["fallback_wires"]))

    if d["unknown"]:
        print("\n⚠ [UNKNOWN] 仍无法处理(多为命名歧义)，需人工核对:")
        for name, ltr, base, note in d["unknown"][:40]:
            print("   %s 的 %s=%s: %s" % (name, ltr, base, note))
        if len(d["unknown"]) > 40:
            print("   ...(共 %d 条)" % len(d["unknown"]))
    else:
        print("\n✅ 无 UNKNOWN：所有被引用输入都已归类为 force 或 RF_WRITE。")


SUMMARY_COLS = [("R", "R/序号"), ("signal", "信号(K)"), ("owner", "owner"), ("type", "类型"),
                ("top", "top_output"), ("n_tests", "用例数"), ("n_neg", "负向数"),
                ("control", "控制位"), ("data", "数据位"), ("expr", "表达式"),
                ("unresolved", "未解析输入"), ("error", "错误")]
DETAIL_COLS = [("R", "R"), ("signal", "信号(K)"), ("owner", "owner"), ("type", "类型"),
               ("test", "用例"), ("neg", "负向"), ("expected", "断言期望值"),
               ("correct", "正确值(负向时)"), ("force", "force 驱动"),
               ("rfwrite", "RF_WRITE 驱动"), ("expr", "表达式"), ("note", "备注")]


def write_report(path, rep, excel):
    base, ext = os.path.splitext(path)
    if ext.lower() in (".html", ".htm"):
        _write_report_html(path, rep, excel)
        return [path]
    # CSV：明细写到给定路径，汇总写到 *_summary.csv
    import csv
    detail_path = path if ext.lower() == ".csv" else base + ".csv"
    summary_path = base + "_summary.csv"
    with open(summary_path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow([h for _k, h in SUMMARY_COLS])
        for r in rep["summary"]:
            w.writerow([r.get(k, "") for k, _h in SUMMARY_COLS])
    with open(detail_path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow([h for _k, h in DETAIL_COLS])
        for r in rep["detail"]:
            w.writerow([r.get(k, "") for k, _h in DETAIL_COLS])
    return [detail_path, summary_path]


def _write_report_html(path, rep, excel):
    import html

    def table(rows, cols):
        if not rows:
            return "<p>（无数据）</p>"
        th = "".join("<th>%s</th>" % html.escape(h) for _k, h in cols)
        trs = []
        for r in rows:
            tds = "".join("<td>%s</td>" % html.escape(str(r.get(k, ""))) for k, _h in cols)
            cls = ' class="neg"' if r.get("neg") == "是" else (' class="err"' if r.get("error") else "")
            trs.append("<tr%s>%s</tr>" % (cls, tds))
        return "<table><thead><tr>%s</tr></thead><tbody>%s</tbody></table>" % (th, "".join(trs))

    n_sig = len(rep["summary"])
    n_tc = len(rep["detail"])
    n_neg = sum(1 for r in rep["detail"] if r.get("neg") == "是")
    doc = """<!doctype html><html lang="zh"><head><meta charset="utf-8">
<title>Dreg 测试用例报告</title><style>
body{{font-family:"Segoe UI",Microsoft YaHei,sans-serif;margin:24px;color:#222}}
h1{{font-size:20px}} h2{{font-size:16px;margin-top:28px}}
table{{border-collapse:collapse;width:100%;font-size:12px;margin-top:8px}}
th,td{{border:1px solid #ccc;padding:4px 6px;text-align:left;vertical-align:top}}
th{{background:#f0f3f7;position:sticky;top:0}}
tr:nth-child(even){{background:#fafbfc}}
tr.neg{{background:#fff3f3}} tr.err{{background:#ffe9e9}}
.sum{{color:#555;margin:6px 0 0}} code{{background:#f5f5f5;padding:0 3px}}
</style></head><body>
<h1>Dreg 测试用例报告</h1>
<p class="sum">源 Excel: <code>{excel}</code>　信号 {n_sig} 个　用例 {n_tc} 条（其中负向 {n_neg} 条）</p>
<p class="sum">负向用例(红底)=故意填错期望值, 预期应 FAIL, 用于自检 checker。</p>
<h2>① 每信号汇总</h2>{tbl_sum}
<h2>② 每条用例明细</h2>{tbl_det}
</body></html>""".format(
        excel=html.escape(os.path.basename(excel)), n_sig=n_sig, n_tc=n_tc, n_neg=n_neg,
        tbl_sum=table(rep["summary"], SUMMARY_COLS), tbl_det=table(rep["detail"], DETAIL_COLS))
    with open(path, "w", encoding="utf-8") as f:
        f.write(doc)


def main(argv=None):
    args = build_argparser().parse_args(argv)
    if not os.path.isfile(args.excel):
        sys.exit("找不到 Excel: %s" % args.excel)

    # --match-fortest: 复刻 for_test 覆盖面——不跳过任何信号(含内部 top_output=0 与含不可驱动
    # wire输入的)，与 VBA for_test 一样照单全收。注意产物可能 CUVUNF 跑不起来(预期内)。
    if args.match_fortest:
        args.include_internal = True
        args.include_risky = True

    opts = generator.GenOptions(
        owners=_split(args.owner),
        signals=_split(args.signals),
        signal_regex=args.regex,
        exclude=_split(args.exclude),
        exclude_regex=args.exclude_regex,
        types=_split(args.type),
        top_output_only=not args.include_internal,   # 默认只生成 top_output=1（可验证输出）
        mode=args.mode,
        max_tests=args.max_tests,
        exhaustive=args.exhaustive,
        neg_signals=_split(args.neg_signals),
        neg_all=args.neg_all,
        neg_mode=args.neg_mode,
        neg_which=args.neg_which,
        neg_value=args.neg_value,
        force_overrides=_split(args.force_signals),
        rfwrite_overrides=_split(args.rfwrite_signals),
        default_kind=args.default_kind,
        wire_fallback=not args.no_wire_fallback,
        comments=args.comments,
        include_risky=args.include_risky,
    )

    print("装载 Excel: %s ..." % args.excel)
    wb = excel_model.load_workbook(args.excel)
    print("  sheets: %s" % wb.sheet_names)

    if args.list:
        cmd_list(wb, opts)
        return 0

    if args.diagnose:
        cmd_diagnose(wb, opts)
        return 0

    # 给人看的报告（可与 .sv 生成并存；只传 --report 不传 --out 则只出报告）
    if args.report:
        rep = generator.report(wb, opts)
        written = write_report(args.report, rep, args.excel)
        print("测试用例报告已写出: %s" % "  ".join(written))
        print("  信号 %d 个，用例 %d 条（负向 %d 条）"
              % (len(rep["summary"]), len(rep["detail"]),
                 sum(1 for r in rep["detail"] if r.get("neg") == "是")))
        if args.out is None:
            return 0   # 只要报告

    out = args.out or "wr_rf_tc.sv"

    # 负向用例单独出文件：分两次生成
    if args.neg_file == "separate" and (opts.neg_all or opts.neg_signals):
        # 正常文件：不含负向
        pos_opts = _copy_opts(opts, neg_all=False, neg_signals=None)
        pos_res = generator.build(wb, pos_opts)
        _write(out, generator.render(pos_res, comments=opts.comments))
        _report(pos_res, out)
        # 负向文件：只含被选信号、仅负向
        neg_path = _neg_path(out)
        neg_res = generator.build(wb, _copy_opts(opts, neg_which=opts.neg_which))
        neg_only = _filter_negative_only(neg_res)
        _write(neg_path, generator.render(neg_only, comments=opts.comments))
        print("负向用例已单独写入: %s" % neg_path)
        return 0

    res = generator.build(wb, opts)
    text = generator.render(res, comments=opts.comments)
    _write(out, text)
    _report(res, out)
    return 0


def _copy_opts(opts, **overrides):
    import copy
    o = copy.copy(opts)
    for k, v in overrides.items():
        setattr(o, k, v)
    return o


def _filter_negative_only(res):
    """只保留含负向用例的信号块，并在渲染前删除其中的正常向量行——
    简化处理：直接复用块（含正常+负向），实际项目里如需纯负向可再细化。"""
    blocks = [(l, s) for (l, s) in res["blocks"] if s["n_negative"] > 0]
    return {"blocks": blocks, "selected": res["selected"],
            "errors": res["errors"], "summary": res["summary"]}


def _neg_path(out):
    root, ext = os.path.splitext(out)
    return root + "_neg" + (ext or ".sv")


def _header(excel, opts, kind):
    return {
        "源Excel": os.path.basename(excel),
        "类型": kind,
        "向量模式": opts.mode,
        "筛选owner": sorted(opts.owners) if opts.owners else "全部",
    }


def _write(path, text):
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def _report(res, out):
    s = res["summary"]
    print("已写出: %s" % out)
    print("  选中信号: %d / logic总行 %d；生成块: %d；跳过: %d；向量: %d（负向 %d）"
          % (s["n_selected"], s["n_logic_rows"], s["n_generated"],
             s.get("n_skipped", 0), s["n_vectors"], s["n_negative"]))
    if s["n_parse_errors"]:
        print("  ⚠ 表达式解析失败 %d 个:" % s["n_parse_errors"])
        for name, aid, msg in res["errors"]:
            print("    - [R=%s] %s: %s" % (aid, name, msg))
    if s.get("n_skipped"):
        print("  ↷ 跳过 %d 个含'不可驱动输入'的信号（force 不存在的 net 会 elaboration 失败；VBA 也跳过这类）:"
              % s["n_skipped"])
        for name, aid, risky in res.get("skipped", [])[:30]:
            rs = ", ".join("%s=%s(%s)" % (l, b, why) for (l, b, why) in risky)
            print("    - [R=%s] %s ← %s" % (aid, name, rs))
        if len(res.get("skipped", [])) > 30:
            print("    ...(共 %d 个)" % len(res["skipped"]))
        print("    如确认这些 net 可驱动，用 --include-risky 强制生成，或 --force-signals/--rfwrite-signals 指定。")


if __name__ == "__main__":
    sys.exit(main())
