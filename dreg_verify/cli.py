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
    p.add_argument("--owner-in-msg", action="store_true",
                   help="断言消息尾部追加 ', owner:<P列>'(英文)：仿真 log 里直接看出 fail 的是谁的信号。"
                        "消息前半段格式不变")
    p.add_argument("--sv-summary", action="store_true",
                   help="产物末尾加测试汇总：统计断言总数/正反例数 + 运行时真 FAIL 数。"
                        "会把整个语句体包进一层命名 begin/end 块(任何 task/initial 里贴入都合法)")
    p.add_argument("--diagnose", action="store_true",
                   help="覆盖诊断: 实测各输入被解析成 force(RO)/RF_WRITE(RW)/未知, "
                        "类型列有哪些写法, 有无 >16bit 输入(驱动会截断), 不生成")

    g = p.add_argument_group("信号筛选")
    g.add_argument("--owner", help="按 owner 筛选(逗号分隔，匹配 logic P 列)")
    g.add_argument("--signals", help="按信号名筛选(逗号分隔，K 全名或去位宽基名)")
    g.add_argument("--regex", help="按信号名正则筛选")
    g.add_argument("--exclude", help="排除这些信号(逗号分隔，K 全名或去位宽基名)")
    g.add_argument("--exclude-regex", help="按正则排除信号(如 'pll_n|_to_dsm' 排掉 datapath 中间信号)")
    g.add_argument("--type", help="按 type/suffix(M 列)筛选，如 to_mux,ls；mux 页的组类型固定为 mux"
                                  "（--type mux 即只出 mux 验证）")
    g.add_argument("--include-internal", action="store_true",
                   help="连 top_output=0 的内部信号也生成（默认只生成 top_output=1 的可验证输出；"
                        "内部信号在 RTL/ENV_RF 层探不到，会导致 elaboration 层级查找失败）")
    g.add_argument("--top-output-only", action="store_true",
                   help="（已是默认行为，保留兼容）只取 top_output=1")
    g.add_argument("--no-mux", action="store_true",
                   help="不生成 mux 页验证（默认 logic+mux 都生成；Excel 无 mux 页时此开关无意义）")
    g.add_argument("--mux-only", action="store_true",
                   help="只生成 mux 页验证（等价 --type mux）；与 --no-mux 互斥")

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
    g4.add_argument("--cascade-mode", choices=["cone", "force"], default="cone",
                    help="级联(输入引用上游 logic 计算网)的驱动模式，详见 级联模式说明.md："
                         "cone(默认)=展开上游表达式、驱动其源头寄存器(纯 Excel，不需要探针前缀)；"
                         "force=直接 force 字面 _to_logic 网(每行 logic 隔离验证；需要 scan_rtl 前缀)")
    g4.add_argument("--probe-prefix", action="append", default=[], metavar="信号=层级路径",
                    help="信号网在 ENV_RF 子模块里时的探针前缀，可多次。"
                         "如 --probe-prefix pll_n=U_BT_LP_PLL_DIG → 断言写 "
                         "`ENV_RF.U_BT_LP_PLL_DIG.pll_n[31:0]；force 输入 wire 同理")
    g4.add_argument("--probe-prefix-file", default=None, metavar="映射.txt",
                    help="从映射文件读探针前缀（每行 信号名=层级路径，# 为注释）。"
                         "与 GUI『探针前缀映射→导出』的文件格式一致，可直接复用")
    return p


def _parse_probe_prefixes(items, prefix_file=None):
    """['pll_n=U_BT_LP_PLL_DIG', ...] + 映射文件 → {'pll_n': 'U_BT_LP_PLL_DIG'}。
    命令行与文件同名时命令行优先。格式错给出明确报错。"""
    out = {}
    if prefix_file:
        if not os.path.isfile(prefix_file):
            sys.exit("--probe-prefix-file 找不到文件: %s" % prefix_file)
        with open(prefix_file, "r", encoding="utf-8") as f:
            out.update(generator.parse_probe_prefix_lines(f.read()))
    for it in items or []:
        if "=" not in it:
            sys.exit("--probe-prefix 格式应为 信号名=层级路径，收到: %r" % it)
        name, prefix = it.split("=", 1)
        if not name.strip() or not prefix.strip():
            sys.exit("--probe-prefix 信号名与层级路径都不能为空: %r" % it)
        out[name.strip()] = prefix.strip()
    return out


def cmd_list(wb, opts):
    sigs = generator.select_signals(wb, opts)
    muxes = generator.select_mux_groups(wb, opts)
    print("可生成信号 %d (logic %d + mux %d) / logic 总行 %d, mux 组 %d (tmm字段=%d, regmap信号=%d)"
          % (len(sigs) + len(muxes), len(sigs), len(muxes),
             len(wb.logic), len(wb.mux), len(wb.tmm), len(wb.regmap)))
    print("%-5s %-40s %-12s %-12s %-3s %s" % ("R", "输出名(K)", "owner", "type", "top", "表达式"))
    print("-" * 100)
    for s in sigs:
        print("%-5s %-40s %-12s %-12s %-3s %s"
              % (s.assert_id, s.out_name[:40], (s.owner or "")[:12],
                 (s.suffix or "")[:12], s.top_output, s.expr[:50]))
    for g in muxes:
        expr_text = "case(%s) %d 选 1" % (g.ctrl_base, len(g.cases))
        print("%-5s %-40s %-12s %-12s %-3s %s"
              % (g.assert_id, g.out_name[:40], (g.owner or "")[:12],
                 "mux", g.top_output, expr_text[:50]))


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
    print("   force - 级联/自引用前级信号(logic 输出): %d" % c["force_chained"])
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
               ("test", "用例"), ("neg", "负向"), ("auto_out", "auto_out(表达式计算)"),
               ("expected", "期望(断言对比值)"), ("exp_src", "期望来源"),
               ("force", "force 驱动"),
               ("rfwrite", "RF_WRITE 驱动"), ("expr", "表达式"), ("note", "备注")]
VERIF_COLS = [("R", "R"), ("signal", "信号(K)"), ("owner", "owner"), ("type", "类型"),
              ("top", "top_output"), ("status_label", "可验证性"),
              ("detail", "风险输入 / 原因"), ("out_net", "断言输出 net")]
# 可验证性状态 → 给人看的标签 + 颜色等级(用于 HTML 高亮/CSV 文字)
VERIF_STATUS = {
    "clean":         ("✅ 可验证", "ok"),
    "wire-fallback": ("⚠ 存疑(按名 force，elaboration 最易 CUVUNF)", "warn"),
    "unresolved":    ("✗ 不可验证(输入未解析)", "bad"),
    "parse-err":     ("✗ 表达式解析失败", "bad"),
}


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
    written = [detail_path, summary_path]
    verif = rep.get("verifiability")
    if verif and verif.get("signals"):
        verif_path = base + "_verifiability.csv"
        with open(verif_path, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f)
            w.writerow([h for _k, h in VERIF_COLS])
            for r in verif["signals"]:
                lbl = VERIF_STATUS.get(r.get("status", ""), (r.get("status", ""), ""))[0]
                row = dict(r, status_label=lbl)
                w.writerow([row.get(k, "") for k, _h in VERIF_COLS])
        written.append(verif_path)
    return written


_REPORT_CSS = """
:root{--bd:#ccc;--hd:#f0f3f7}
*{box-sizing:border-box}
body{font-family:"Segoe UI",Microsoft YaHei,sans-serif;margin:0;color:#222}
header{position:sticky;top:0;background:#fff;border-bottom:1px solid var(--bd);
 padding:14px 24px 0;z-index:10;box-shadow:0 2px 6px rgba(0,0,0,.04)}
h1{font-size:19px;margin:0 0 4px} h3{font-size:13px;margin:22px 0 0}
.sum{color:#555;margin:2px 0;font-size:12px}
.toolbar{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin:10px 0}
.toolbar input[type=text]{padding:5px 9px;border:1px solid var(--bd);border-radius:5px;
 width:300px;font-size:13px}
.toolbar select{padding:5px;border:1px solid var(--bd);border-radius:5px}
.toolbar label{font-size:13px;color:#444;user-select:none}
#count{color:#888;font-size:12px;margin-left:auto}
.tabs{display:flex;gap:4px;margin-top:8px}
.tabbtn{border:1px solid var(--bd);border-bottom:none;background:#f4f6f9;cursor:pointer;
 padding:7px 16px;font-size:13px;border-radius:6px 6px 0 0;color:#555}
.tabbtn.active{background:#fff;color:#1558d6;font-weight:600;box-shadow:0 -2px 0 #1558d6 inset}
main{padding:6px 24px 40px}
.tab{display:none} .tab.active{display:block}
table{border-collapse:collapse;font-size:12px;margin-top:8px}
table.full,table.sum{width:100%}
th,td{border:1px solid var(--bd);padding:4px 6px;text-align:left;vertical-align:top}
th{background:var(--hd)} thead th{position:sticky;top:128px}
tbody tr:nth-child(even){background:#fafbfc}
tr.neg{background:#fff3f3} tr.err{background:#ffe9e9}
.tt td,.tt th{text-align:center} .tt th.rowhdr{text-align:left;background:#eef1f5;white-space:nowrap}
.tt td.neg,.tt th.negh{background:#ffe3e3;color:#a40000}
.tt tr.exprow th,.tt tr.exprow td{font-weight:600;border-top:2px solid #999}
.tt td.drv{font-family:Consolas,monospace;font-size:11px;text-align:left;color:#555}
.ttblock{margin-bottom:6px} .ex{color:#888} code{background:#f5f5f5;padding:0 3px}
pre.chain{background:#f4f8ff;border:1px solid #d4e0f5;border-radius:4px;padding:8px 10px;
 font-family:Consolas,monospace;font-size:12px;color:#234;margin:6px 0 2px;overflow-x:auto}
.empty{color:#999;padding:20px}
/* auto_out 行(表达式计算值，只读参考) 与 期望 行(进 .sv 的对比值) */
.tt tr.autorow th,.tt tr.autorow td{color:#666;font-style:italic;border-top:2px solid #999}
.tt tr.exprow{border-top:none}
.tt td.dsgn{background:#e2f4e2}        /* designer 手填期望(且非负向) */
.tt td.fb{color:#999}                  /* 未手填 -> auto_out 兜底(灰) */
.tt td.dsgndiff{background:#ffd9d9}    /* 手填期望 != auto_out: 表达式可能与 designer 意图不符 */
/* ── 真值表检查 tab：遮盖/填空/判定 ── */
.chkbar{display:flex;gap:14px;align-items:center;margin:10px 0;padding:10px;
 background:#f4f7ff;border:1px solid #c8d6f5;border-radius:6px;flex-wrap:wrap}
.chkbar button{padding:7px 18px;font-size:13px;cursor:pointer;border:1px solid #1558d6;
 border-radius:5px;background:#1558d6;color:#fff;font-weight:600}
.chkbar button.off{background:#fff;color:#1558d6}
#chkscore{font-size:13px;color:#333}
#chkscore b.ok{color:#1a7f37} #chkscore b.bad{color:#c00}
.chkhint{font-size:12px;color:#666;flex-basis:100%}
.tt td.masked{color:transparent;background:#e3e3e3 !important;text-shadow:none;user-select:none}
.tt td.masked::before{content:"?";color:#aaa;float:left;width:100%;margin-right:-100%}
.cin{width:76px;font-family:Consolas,monospace;font-size:12px;padding:2px 5px;
 border:1px solid #1558d6;border-radius:3px;text-align:right}
.cin.okin{border-color:#1a7f37;background:#e2f4e2}
.cin.badin{border-color:#c00;background:#ffd9d9}
.tt td.okc{background:#d9f0d9 !important}
.tt td.badc{background:#ffd2d2 !important}
.tt th.okh{background:#1a7f37;color:#fff}
.tt th.badh{background:#c00;color:#fff}
.vstat{font-weight:600;white-space:nowrap}
.vstat.ok{color:#1a7f37} .vstat.warn{color:#b26a00} .vstat.bad{color:#c00}
.vsum{margin:10px 0;font-size:13px} .vsum b{margin-right:14px}
tr.vrow.warn{background:#fff8ec} tr.vrow.bad{background:#ffecec}
"""

_REPORT_JS = """
(function(){
  var q=document.getElementById('q'),ow=document.getElementById('owner'),
      no=document.getElementById('negonly'),cnt=document.getElementById('count');
  function tab(name){
    var bs=document.querySelectorAll('.tabbtn'),ss=document.querySelectorAll('.tab'),i;
    for(i=0;i<bs.length;i++)bs[i].classList.toggle('active',bs[i].getAttribute('data-tab')===name);
    for(i=0;i<ss.length;i++)ss[i].classList.toggle('active',ss[i].id===name);
  }
  var btns=document.querySelectorAll('.tabbtn');
  for(var i=0;i<btns.length;i++)(function(b){b.onclick=function(){tab(b.getAttribute('data-tab'));};})(btns[i]);
  function ok(el){
    var t=el.getAttribute('data-text')||'';
    if(q.value && t.indexOf(q.value.toLowerCase())<0)return false;
    if(ow.value && el.getAttribute('data-owner')!==ow.value)return false;
    if(no.checked && el.getAttribute('data-neg')!=='1')return false;
    return true;
  }
  function apply(){
    var els=document.querySelectorAll('.filt'),vis=0,sig=0,j;
    for(j=0;j<els.length;j++){var m=ok(els[j]);els[j].style.display=m?'':'none';
      if(m){vis++;if(els[j].classList.contains('srow'))sig++;}}
    cnt.textContent='匹配信号 '+sig;
  }
  q.oninput=apply;ow.onchange=apply;no.onchange=apply;apply();

  /* ===== 真值表检查（designer 自测：遮 auto_out、期望变填空、回车判定） ===== */
  var chkbtn=document.getElementById('chkbtn'),chkscore=document.getElementById('chkscore');
  var chkon=false,okn=0,badn=0,answered=0;
  /* 宽容解析 designer 输入的数值：0x../0b../16'h../'b../'d../十进制/裸hex（与 GUI 同语义，十进制优先于裸hex） */
  function parseVal(s){
    s=String(s||'').trim().toLowerCase().replace(/[_\\s]/g,'');
    if(!s)return null;
    var m;
    if(m=s.match(/^(?:\\d+)?'h([0-9a-f]+)$/))return parseInt(m[1],16);
    if(m=s.match(/^(?:\\d+)?'b([01]+)$/))return parseInt(m[1],2);
    if(m=s.match(/^(?:\\d+)?'d(\\d+)$/))return parseInt(m[1],10);
    if(/^0x[0-9a-f]+$/.test(s))return parseInt(s.slice(2),16);
    if(/^0b[01]+$/.test(s))return parseInt(s.slice(2),2);
    if(/^\\d+$/.test(s))return parseInt(s,10);
    if(/^[0-9a-f]+$/.test(s))return parseInt(s,16);
    return NaN;
  }
  function updScore(){
    var total=document.querySelectorAll('#chk td.cquiz').length;
    chkscore.innerHTML='已检查 '+answered+'/'+total+' · <b class="ok">一致 '+okn+'</b> · <b class="bad">不一致 '+badn+'</b>';
  }
  function setChk(on){
    chkon=on;okn=0;badn=0;answered=0;
    chkbtn.textContent=on?'结束检查（显示全部 auto_out）':'开始检查（遮盖所有 auto_out）';
    chkbtn.classList.toggle('off',on);
    var autos=document.querySelectorAll('#chk td.cauto'),i;
    for(i=0;i<autos.length;i++)autos[i].classList.toggle('masked',on);
    var qz=document.querySelectorAll('#chk td.cquiz');
    for(i=0;i<qz.length;i++){
      var sp=qz[i].querySelector('.cval'),inp=qz[i].querySelector('.cin');
      sp.style.display=on?'none':'';
      inp.style.display=on?'':'none';
      inp.value='';inp.className='cin';inp.readOnly=false;
      inp.removeAttribute('data-done');
    }
    /* 清掉上次的判定颜色（先收集再删，避免边遍历边改 classList 影响 NodeList） */
    var marked=document.querySelectorAll('#chk .okc,#chk .badc,#chk .okh,#chk .badh'),mlist=[],k;
    for(i=0;i<marked.length;i++)mlist.push(marked[i]);
    for(k=0;k<mlist.length;k++)mlist[k].classList.remove('okc','badc','okh','badh');
    if(on)updScore();else chkscore.textContent='';
  }
  if(chkbtn){
    chkbtn.onclick=function(){setChk(!chkon);};
    document.addEventListener('keydown',function(ev){
      if(!chkon||ev.key!=='Enter'||!ev.target.classList||!ev.target.classList.contains('cin'))return;
      var inp=ev.target,td=inp.parentNode;
      if(inp.getAttribute('data-done'))return;          /* 已判定的列不重答（auto_out 已揭晓） */
      var v=parseVal(inp.value);
      if(v===null)return;                               /* 空着回车不判 */
      var want=parseInt(td.getAttribute('data-v'),10);
      var hit=!isNaN(v)&&v===want;
      /* 揭示同列 auto_out + 整列染色 + 列头染色 */
      var table=td.closest('table'),ci=td.cellIndex,rows=table.tBodies[0].rows,j;
      for(j=0;j<rows.length;j++){
        var cell=rows[j].cells[ci];
        if(!cell)continue;
        if(cell.classList.contains('cauto'))cell.classList.remove('masked');
        cell.classList.add(hit?'okc':'badc');
      }
      var hdr=table.tHead.rows[0].cells[ci];
      if(hdr)hdr.classList.add(hit?'okh':'badh');
      inp.setAttribute('data-done','1');inp.readOnly=true;
      inp.className='cin '+(hit?'okin':'badin');
      answered++;if(hit)okn++;else badn++;
      updScore();
    });
  }
})();
"""


def _write_report_html(path, rep, excel):
    import html

    def esc(x):
        return html.escape(str(x))

    def attr(signal, owner, neg, text):
        return ' data-signal="%s" data-owner="%s" data-neg="%s" data-text="%s"' % (
            esc(signal), esc(owner or ""), "1" if neg else "0", esc((text or "").lower()))

    def flat_table(rows, cols, rowcls, cls_attr, kind):
        """① 汇总 / ④ 明细：可过滤的横表。rowcls=srow/drow；每行带 data-* 供 JS 过滤。"""
        if not rows:
            return '<p class="empty">（无数据）</p>'
        th = "".join("<th>%s</th>" % esc(h) for _k, h in cols)
        trs = []
        for r in rows:
            if kind == "sum":
                neg = bool(r.get("n_neg"))
                text = "%s %s %s" % (r.get("signal", ""), r.get("owner", ""), r.get("expr", ""))
                extra = " err" if r.get("error") else (" neg" if neg else "")
            else:
                neg = r.get("neg") == "是"
                text = "%s %s %s %s" % (r.get("signal", ""), r.get("owner", ""),
                                        r.get("test", ""), r.get("expr", ""))
                extra = " neg" if neg else ""
            tds = "".join("<td>%s</td>" % esc(r.get(k, "")) for k, _h in cols)
            trs.append('<tr class="filt %s%s"%s>%s</tr>'
                       % (rowcls, extra, attr(r.get("signal", ""), r.get("owner", ""), neg, text), tds))
        return ('<table class="%s"><thead><tr>%s</tr></thead><tbody>%s</tbody></table>'
                % (cls_attr, th, "".join(trs)))

    def _exp_cell(tc, check):
        """期望行单元格：负向=红(错值)；手填=绿/红(与 auto_out 一致/不一致)；未填=灰(auto_out 兜底)。
        check=True 时正向格变成『可填空自测』结构(span 显示值 + 隐藏的 input，JS 切换)。"""
        if tc["neg"]:
            title = ' title="负向：故意填错(预期 FAIL，自检 checker)。正确(auto_out)应为 %s"' % esc(tc["correct"])
            return '<td class="neg"%s>%s</td>' % (title, esc(tc["expected"]))
        filled = tc.get("designer_filled")
        same = tc.get("expected") == tc.get("auto_out", tc.get("correct"))
        if filled:
            cls = "dsgn" if same else "dsgndiff"
            title = (' title="designer 手填期望，与 auto_out 一致"' if same else
                     ' title="⚠ designer 手填期望与 auto_out 不一致——表达式可能与 designer 意图不符，'
                     '仿真该测试预期 FAIL(这正是 Dreg 要抓的 bug)"')
            shown = esc(tc["expected"])
        else:
            cls = "fb"
            title = ' title="期望未手填，生成 .sv 时用 auto_out 兜底(未经 designer 人工核对)"'
            shown = esc(tc["expected"])
        if not check:
            return '<td class="%s"%s>%s</td>' % (cls, title, shown)
        # 检查模式格：data-v=auto_out 数值(回车后 JS 比对)；input 平时隐藏
        return ('<td class="cquiz %s" data-v="%d"%s><span class="cval">%s</span>'
                '<input class="cin" style="display:none" placeholder="?"></td>'
                % (cls, tc.get("auto_num", 0), title, shown))

    def truth_tables(tabs, check=False):
        """②真值表 / ③真值表检查：输入(带位宽)做行、各测试做列；auto_out 与 期望 分两行。
        check=True 时 auto_out 格可被 JS 遮盖、期望格可填空自测(designer 防自证检查)。"""
        if not tabs:
            return '<p class="empty">（无数据）</p>'
        out = []
        for t in tabs:
            tests = t["tests"]
            hdr = ['<th class="rowhdr">信号\\测试</th>']
            for tc in tests:
                hdr.append('<th class="%s">%s</th>' % ("negh" if tc["neg"] else "", esc(tc["name"])))
            body = []
            for ri, inp in enumerate(t["inputs"]):
                ltr = inp.get("letters") or ""               # 表达式变量(A/B/C…) → 物理信号
                rh = ("%s → %s" % (ltr, inp["label"])) if ltr else inp["label"]
                cells = ['<th class="rowhdr">%s</th>' % esc(rh)]
                for tc in tests:
                    cells.append('<td class="%s">%s</td>'
                                 % ("neg" if tc["neg"] else "", esc(tc["values"][ri])))
                body.append("<tr>%s</tr>" % "".join(cells))
            # ── auto_out 行（表达式计算值，只读参考）。检查模式下负向列不参与遮盖(不是自测对象) ──
            auto_cells = ['<th class="rowhdr" title="程序按表达式算出的输出值(参考)。'
                          '用它当期望验证表达式有自证嫌疑，.sv 断言对比的是下面的期望">%s</th>'
                          % esc(t.get("auto_label", "auto_out"))]
            for tc in tests:
                cls = "neg" if tc["neg"] else ("cauto" if check else "")
                auto_cells.append('<td class="%s" data-v="%d">%s</td>'
                                  % (cls, tc.get("auto_num", 0),
                                     esc(tc.get("auto_out", tc.get("correct", "")))))
            body.append('<tr class="autorow">%s</tr>' % "".join(auto_cells))
            # ── 期望 行（designer 手填 > auto_out 兜底 > 负向错值；.sv 断言用这一行） ──
            exp_cells = ['<th class="rowhdr" title="designer 手填的期望，.sv 断言用它对比；'
                         '未填的列用 auto_out 兜底(灰)。绿=手填且与 auto_out 一致；红=手填但不一致">%s</th>'
                         % esc(t.get("exp_label", "期望(out)"))]
            for tc in tests:
                exp_cells.append(_exp_cell(tc, check))
            body.append('<tr class="exprow">%s</tr>' % "".join(exp_cells))
            for label, key in (("force", "force"), ("RF_WRITE", "rfwrite")):
                cells = ['<th class="rowhdr">%s</th>' % label]
                for tc in tests:
                    cells.append('<td class="drv %s">%s</td>'
                                 % ("neg" if tc["neg"] else "", esc(tc.get(key, ""))))
                body.append("<tr>%s</tr>" % "".join(cells))
            neg_block = any(tc["neg"] for tc in tests)
            text = "%s %s %s" % (t["signal"], t.get("owner", ""), t["expr"])
            # cone 展开链：本行 + 逐层代入的上游行（Excel 原式 = 代入信号名的等价形式），真值表上方
            chain = t.get("chain") or []
            chain_html = ""
            if len(chain) >= 2:
                marks = "①②③④⑤⑥⑦⑧⑨"
                cl = []
                for ci, c in enumerate(chain):
                    mk = marks[ci] if ci < len(marks) else "(%d)" % (ci + 1)
                    head = "%s %s" % (mk, c["out"])
                    cl.append("%s = %s" % (esc(head), esc(c["expr"])))
                    cl.append("%s = %s" % (" " * len(head), esc(c["subst"])))
                chain_html = ('<pre class="chain">展开链（输入引用内部信号/上游计算网，已展开上游；'
                              '①=本行，往下逐层代入）:\n%s</pre>' % "\n".join(cl))
            # 标题序号: logic 显示 "R<序号>"; mux 的 R 已是 "mux<N>"，不再加 R 前缀
            rid = str(t["R"])
            rlabel = rid if rid.startswith("mux") else ("R" + rid)
            out.append('<div class="filt ttblock"%s>'
                       '<h3>%s　<code>%s</code>　<span class="ex">%s</span></h3>%s'
                       '<table class="tt"><thead><tr>%s</tr></thead><tbody>%s</tbody></table></div>'
                       % (attr(t["signal"], t.get("owner", ""), neg_block, text),
                          esc(rlabel), esc(t["signal"]), esc(t["expr"]), chain_html,
                          "".join(hdr), "".join(body)))
        return "\n".join(out)

    def verif_table(verif):
        """⑤ 可验证性：逐信号健康度 + 风险输入说明（取代旧 GUI'覆盖诊断'按钮的整页 dump）。"""
        if not verif or not verif.get("signals"):
            return '<p class="empty">（无数据）</p>'
        c = verif["counts"]
        head = ('<p class="vsum">'
                '<b class="vstat ok">✅ 可验证 %d</b>'
                '<b class="vstat warn">⚠ 存疑(按名 force) %d</b>'
                '<b class="vstat bad">✗ 不可验证 %d</b>'
                '<b class="vstat bad">✗ 表达式错误 %d</b></p>'
                '<p class="ex">⚠/✗ 的信号最可能在 elaboration 阶段 CUVUNF 失败：'
                '"存疑"=表里查无、按信号名直接 force；"不可验证"=输入未能解析到 force/RF_WRITE。</p>'
                % (c.get("clean", 0), c.get("wire-fallback", 0),
                   c.get("unresolved", 0), c.get("parse-err", 0)))
        th = "".join("<th>%s</th>" % esc(h) for _k, h in VERIF_COLS)
        trs = []
        for r in verif["signals"]:
            lbl, lvl = VERIF_STATUS.get(r.get("status", ""), (r.get("status", ""), ""))
            text = "%s %s %s" % (r.get("signal", ""), r.get("owner", ""), r.get("detail", ""))
            cells = []
            for k, _h in VERIF_COLS:
                if k == "status_label":
                    cells.append('<td><span class="vstat %s">%s</span></td>' % (lvl, esc(lbl)))
                else:
                    cells.append("<td>%s</td>" % esc(r.get(k, "")))
            trs.append('<tr class="filt vrow %s"%s>%s</tr>'
                       % (lvl, attr(r.get("signal", ""), r.get("owner", ""), False, text),
                          "".join(cells)))
        return head + ('<table class="full"><thead><tr>%s</tr></thead><tbody>%s</tbody></table>'
                       % (th, "".join(trs)))

    owners = sorted({(r.get("owner") or "") for r in rep["summary"] if r.get("owner")})
    owner_opts = '<option value="">全部 owner</option>' + "".join(
        '<option value="%s">%s</option>' % (esc(o), esc(o)) for o in owners)
    n_sig = len(rep["summary"])
    n_tc = len(rep["detail"])
    n_neg = sum(1 for r in rep["detail"] if r.get("neg") == "是")

    # 期望来源统计：手填(designer 审过) vs auto_out 兜底(有自证嫌疑)
    n_designer = sum(1 for t in rep.get("tables", []) for tc in t["tests"]
                     if tc.get("designer_filled"))
    n_pos_tc = sum(1 for t in rep.get("tables", []) for tc in t["tests"] if not tc["neg"])

    # 真值表检查 tab：全局按钮(遮盖 auto_out) + 计分 + 使用说明 + 可填空的真值表
    chk_section = (
        '<div class="chkbar">'
        '<button id="chkbtn">开始检查（遮盖所有 auto_out）</button>'
        '<span id="chkscore"></span>'
        '<span class="chkhint">这是 designer 的防自证自测：auto_out 是表达式算出来的——用它当期望验证表达式'
        '等于自己证明自己。点「开始检查」后 auto_out 全部遮住、期望变成空格：只看上面的输入，'
        '自己算输出填进去按回车 → 立即揭晓 auto_out 并比对。'
        '<b style="color:#1a7f37">绿=一致</b>；<b style="color:#c00">红=不一致</b>'
        '（要么你算错了，要么表达式有 bug——后者正是 Dreg 要抓的）。'
        '检查结果只在本页面，不回写 .sv；负向列(_NEG)是故意填错的自检用例，不参与检查。</span>'
        '</div>'
    ) + truth_tables(rep.get("tables", []), check=True)

    # 仅对 body 模板做 % 替换；CSS/JS 含字面 % 与 {}，单独拼接(不参与格式化)。
    body = (
        '<header>'
        '<h1>Dreg 测试用例报告</h1>'
        '<p class="sum">源 Excel: <code>%s</code>　信号 %d 个　用例 %d 条（其中负向 %d 条）　'
        '正向期望: designer 手填 %d / auto_out 兜底 %d　'
        '负向(红/_NEG)=故意填错期望, 预期应 FAIL。</p>'
        '<p class="sum">auto_out=程序按表达式算的值(参考)；期望=designer 手填、.sv 断言用它对比'
        '（未填→auto_out 兜底）。用「③ 真值表检查」可自测期望而不被 auto_out 影响。</p>'
        '<div class="toolbar">'
        '<input type="text" id="q" placeholder="搜索 信号名 / owner / 表达式…">'
        '<select id="owner">%s</select>'
        '<label><input type="checkbox" id="negonly"> 只看负向</label>'
        '<span id="count"></span>'
        '</div>'
        '<div class="tabs">'
        '<button class="tabbtn active" data-tab="sum">① 汇总</button>'
        '<button class="tabbtn" data-tab="tt">② 真值表</button>'
        '<button class="tabbtn" data-tab="chk">③ 真值表检查</button>'
        '<button class="tabbtn" data-tab="det">④ 明细</button>'
        '<button class="tabbtn" data-tab="ver">⑤ 可验证性</button>'
        '</div></header>'
        '<main>'
        '<section id="sum" class="tab active">%s</section>'
        '<section id="tt" class="tab">%s</section>'
        '<section id="chk" class="tab">%s</section>'
        '<section id="det" class="tab">%s</section>'
        '<section id="ver" class="tab">%s</section>'
        '</main>'
    ) % (
        esc(os.path.basename(excel)), n_sig, n_tc, n_neg, n_designer, n_pos_tc - n_designer,
        owner_opts,
        flat_table(rep["summary"], SUMMARY_COLS, "srow", "sum", "sum"),
        truth_tables(rep.get("tables", [])),
        chk_section,
        flat_table(rep["detail"], DETAIL_COLS, "drow", "full", "det"),
        verif_table(rep.get("verifiability")),
    )
    doc = ('<!doctype html><html lang="zh"><head><meta charset="utf-8">'
           '<title>Dreg 测试用例报告</title><style>' + _REPORT_CSS + '</style></head><body>'
           + body + '<script>' + _REPORT_JS + '</script></body></html>')
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

    # mux 范围旗标（2026-06-03 第九轮）
    if args.no_mux and args.mux_only:
        sys.exit("--no-mux 与 --mux-only 互斥")
    arg_types = _split(args.type)
    if args.mux_only:
        arg_types = {"mux"}

    opts = generator.GenOptions(
        owners=_split(args.owner),
        signals=_split(args.signals),
        signal_regex=args.regex,
        exclude=_split(args.exclude),
        exclude_regex=args.exclude_regex,
        types=arg_types,
        top_output_only=not args.include_internal,   # 默认只生成 top_output=1（可验证输出）
        gen_mux=not args.no_mux,                     # 默认 logic+mux 都生成（用户拍板）
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
        probe_prefixes=_parse_probe_prefixes(args.probe_prefix, args.probe_prefix_file),
        owner_in_msg=args.owner_in_msg,
        sv_summary=args.sv_summary,
        cascade_mode=args.cascade_mode,
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
        vc = rep.get("verifiability", {}).get("counts", {})
        if vc:
            print("  可验证性：可验证 %d / 存疑(按名force) %d / 不可验证 %d / 表达式错误 %d"
                  % (vc.get("clean", 0), vc.get("wire-fallback", 0),
                     vc.get("unresolved", 0), vc.get("parse-err", 0)))
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
        # 负向文件：真·仅负向(每信号只留负向向量，无负向的信号不出现)；
        # 汇总命名块加 _neg 后缀 → 与主文件贴进同一作用域也不重名
        neg_path = _neg_path(out)
        neg_res = generator.build(wb, _copy_opts(opts, negative_vectors_only=True))
        _write(neg_path, generator.render(neg_res, comments=opts.comments, block_suffix="_neg"))
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
    mux_part = ""
    if s.get("n_mux_groups"):
        mux_part = ("（logic %d + mux %d，mux 组共 %d 个）"
                    % (s["n_generated"] - s.get("n_mux_generated", 0),
                       s.get("n_mux_generated", 0), s["n_mux_groups"]))
    print("  选中信号: %d / logic总行 %d；生成块: %d%s；跳过: %d；向量: %d（负向 %d）"
          % (s["n_selected"], s["n_logic_rows"], s["n_generated"], mux_part,
             s.get("n_skipped", 0), s["n_vectors"], s["n_negative"]))
    if s["n_parse_errors"]:
        print("  ⚠ 表达式解析失败 %d 个:" % s["n_parse_errors"])
        for name, aid, msg in res["errors"]:
            print("    - [R=%s] %s: %s" % (aid, name, msg))
    if s.get("n_dup_labels"):
        print("  ⛔ 发现 %d 处重复 assert 标号（同一作用域内重复=非法 SV，会 elaboration 失败）:"
              % s["n_dup_labels"])
        for lbl, sig1, sig2 in res.get("dup_labels", [])[:20]:
            print("    - assert_%s: 同时来自 %s 与 %s" % (lbl, sig1, sig2))
        print("    多因两信号共用同一序号；请核对 logic R 列 / mux 页 N 列唯一性，或改掉自定义测试名。")
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
