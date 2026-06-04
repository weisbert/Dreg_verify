# -*- coding: utf-8 -*-
"""
audit_unresolved.py — 一把抓出 Excel 里全部"未解析 / 跳过 / 错误"的 logic 信号与 mux 组，
按【原因类别 × owner】归类，并把"公式(L列)解析失败"单独拎成一类。

为什么要它：一个一个 inspect 太慢，且看不清"到底是几种根因"。这个脚本跑一遍 build，
把 compose_account 的完整去向（reason 不截断）按类别聚合，让你一眼看清：
  · 还有哪些没解析、各属哪个 owner、各是什么原因；
  · "公式列无法解析"到底占多少（错误·公式 这一类）——直接回答你的猜测。

不需要探针前缀；纯 Excel 解析。

用法：
    python audit_unresolved.py 真表.xlsx
    python audit_unresolved.py 真表.xlsx --out audit.txt      # 完整报告写文件(UTF-8 BOM，防乱码)
"""
import argparse
from collections import OrderedDict, defaultdict

from dreg_verify import excel_model, generator


def _owner_map(wb):
    """信号/组名 -> owner。"""
    m = {}
    for s in wb.logic:
        m[s.out_name] = (getattr(s, "owner", "") or "").strip()
    for g in wb.mux:
        m[g.out_name] = (getattr(g, "owner", "") or "").strip()
    return m


def _category(disp, reason):
    """把 (去向, 原因) 归成一个可读的根因类别。关键词取自 generator.build/mux_gen 的真实措辞。"""
    r = reason or ""
    if disp == "错误":
        if "表达式解析" in r:
            return "错误·公式(L列)无法解析"        # ← 用户猜测的那类
        if "cone" in r:
            return "错误·cone展开失败(组合环/超深)"
        return "错误·其它"
    if disp == "跳过":
        if "未解析" in r:
            return "跳过·未解析(输入查无/内部信号)"
        if "前缀" in r:
            return "跳过·缺探针前缀(needs-prefix)"
        if "假绿" in r:
            return "跳过·假绿(数据字段太窄)"
        if "成环" in r or "深度" in r:
            return "跳过·mux级联成环/超深"
        if "位宽" in r:
            return "跳过·case位宽不一致"
        if "续行" in r:
            return "跳过·续行控制列不一致"
        if "透传" in r:
            return "跳过·控制无透传驱动路径"
        if "wire" in r:
            return "跳过·wire兜底(表里查无)"
        return "跳过·其它"
    if disp == "过滤":
        return "过滤(内部节点/被筛选)"
    if disp.startswith("生成"):
        return "生成"
    return disp


# 哪些类别需要逐项详列（名字 + kind + 完整原因）。"生成/过滤"只给 owner 分布。
_DETAIL = (
    "错误·公式(L列)无法解析",
    "错误·cone展开失败(组合环/超深)",
    "错误·其它",
    "跳过·未解析(输入查无/内部信号)",
    "跳过·控制无透传驱动路径",
    "跳过·mux级联成环/超深",
    "跳过·case位宽不一致",
    "跳过·续行控制列不一致",
    "跳过·wire兜底(表里查无)",
    "跳过·其它",
)


def main():
    ap = argparse.ArgumentParser(description="一把抓出全部未解析/跳过/错误，按原因×owner 归类")
    ap.add_argument("excel")
    ap.add_argument("--out", default=None, help="完整报告写到此文件(UTF-8 带 BOM，防 PowerShell 乱码)")
    args = ap.parse_args()

    wb = excel_model.load_workbook(args.excel)
    owners = _owner_map(wb)
    opts = generator.GenOptions()
    res = generator.build(wb, opts)
    items = generator.compose_account(wb, opts, res)

    cats = OrderedDict()
    for it in items:
        cats.setdefault(_category(it["disposition"], it.get("reason")), []).append(it)

    lines = []
    def out(s=""):
        lines.append(s)

    out("=== 未解析 / 跳过 / 错误 审计 (audit_unresolved.py) ===")
    out("文件: %s" % args.excel)
    out("总计: %d 个 logic 信号 + %d 个 mux 组 = %d 项" % (len(wb.logic), len(wb.mux), len(items)))
    out("")
    out("【按根因类别小结（多→少）】")
    for cat, its in sorted(cats.items(), key=lambda kv: -len(kv[1])):
        byo = defaultdict(int)
        for it in its:
            byo[owners.get(it["name"], "") or "(空)"] += 1
        top = " | ".join("%s=%d" % (o, c) for o, c in sorted(byo.items(), key=lambda kv: -kv[1])[:6])
        out("  %-34s %4d    [owner: %s]" % (cat, len(its), top))
    out("")

    for cat in _DETAIL:
        its = cats.get(cat)
        if not its:
            continue
        out("=" * 96)
        out("【%s】 共 %d 项" % (cat, len(its)))
        out("  名字 | kind | 完整原因:")
        for it in its:
            out("    %-44s %-5s %s" % (it["name"][:44], it["kind"], (it.get("reason") or "")))
        out("")

    text = "\n".join(lines)
    if args.out:
        with open(args.out, "w", encoding="utf-8-sig", newline="") as f:
            f.write(text)
        print(text if len(text) < 6000 else text[:6000] + "\n...(详见文件)")
        print("\n完整报告已写入: %s (UTF-8 带 BOM，记事本/Excel 直接打开不乱码)" % args.out)
    else:
        print(text)


if __name__ == "__main__":
    main()
