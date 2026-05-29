# -*- coding: utf-8 -*-
"""
inspect_excel.py — 读取 Dreg 核心 Excel，导出结构化文本，方便粘贴给 Claude 分析。

用法:
    python inspect_excel.py <excel路径>
        [--rows N]            每个 sheet 导出的样本行数 (默认 20)
        [--out 输出.txt]      输出文件 (默认在 Excel 同目录下 <名字>_inspect.txt)
        [--sheets a,b,c]      只导出这些 sheet (默认全部, 关键页自动排前面)
        [--compact]           精简模式：去冗余段落, logic 表达式列(L)完整不截断 (推荐, 输出最短)
        [--split]             每个 sheet 单独存一个 txt, 方便分批发送
        [--anon-signals]      对“信号名类”内容做“保结构”脱敏 (见下)
        [--mask-owners]       把 owner 列(P)的人名替换成 OwnerN
        [--maxlen N]          单元格内容最大显示长度 (默认 80; logic 的 L/M 列始终不截断)

想让输出最短就用:
    python inspect_excel.py "核心文件.xlsx" --compact --mask-owners --rows 10 \
        --sheets logic,regmap,NamingRule,for_test
还嫌长就再加 --split, 然后一页一页发我。

脱敏说明:
  - 默认【不脱敏】，导出原文；请你自己过目后再决定要不要手动删改。
  - --anon-signals 会把字母词元替换成 w1/w2/...，但【保留】下划线、方括号、位宽数字、
    分隔符与表达式运算符；logic 的真值表达式列(L) 与类型列(M) 始终保持原文（核心信息且不敏感）。
    注意：开了它之后，命名规则里像 "logic" 这种字面插入串也会被替换，我可能要回头跟你确认几个字面前缀/中缀。
  - 重要：请尽量保留【信号名结构】和【表达式列】，否则生成器的核心逻辑没法还原。

依赖: openpyxl  ->  pip install openpyxl
限制: 仅支持 .xlsx。若是老的 .xls，请用 Excel 另存为 .xlsx 再跑。
"""

import argparse
import os
import re
import sys

try:
    import openpyxl
    from openpyxl.utils import get_column_letter
except ImportError:
    sys.exit("缺少 openpyxl，请先运行:  pip install openpyxl")

# 关键 sheet 排在最前，其余按原顺序跟在后面
PRIORITY_SHEETS = ["logic", "regmap", "NamingRule", "for_test", "main",
                   "total_memory_map", "mux", "level_shift", "SignalPath"]

PROFILE_SCAN_CAP = 5000   # 列取值统计最多扫描多少行，防止 max_row 被撑爆

# ---------- 脱敏 ----------
_word_map = {}
_owner_map = {}


def anon_signal(text):
    """保结构脱敏：仅替换字母词元，保留 _ [] : 数字 等结构。"""
    def repl(m):
        key = m.group(0).lower()
        if key not in _word_map:
            _word_map[key] = "w%d" % (len(_word_map) + 1)
        return _word_map[key]
    return re.sub(r"[A-Za-z]+", repl, text)


def mask_owner(text):
    key = text.strip().lower()
    if not key:
        return text
    if key not in _owner_map:
        _owner_map[key] = "Owner%d" % (len(_owner_map) + 1)
    return _owner_map[key]


# ---------- 单元格取值 ----------
def cell_str(value, maxlen=80, no_trunc=False):
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        s = str(int(value))
    else:
        s = str(value)
    s = s.replace("\n", "\\n").replace("\r", "")
    if not no_trunc and maxlen and len(s) > maxlen:
        s = s[:maxlen] + "…(+%d)" % (len(s) - maxlen)
    return s


def read_rows(ws, max_row):
    """返回 [[v,v,...], ...]，按行；超出 cap 截断。"""
    cap = min(max_row or 0, PROFILE_SCAN_CAP)
    rows = []
    for r in ws.iter_rows(min_row=1, max_row=cap, values_only=True):
        rows.append(list(r))
    return rows


def is_blank_row(row):
    return all(v is None or (isinstance(v, str) and v.strip() == "") for v in row)


# ---------- 各列脱敏策略 ----------
def transform_cell(sheet_name, col_letter, value, args):
    """根据开关与列位置返回（可能脱敏后的）值。"""
    if value is None or not isinstance(value, str):
        return value
    sn = sheet_name.lower()
    # logic 的表达式列 L、类型列 M 永远不脱敏（符号逻辑/枚举，不敏感且是核心信息）
    if sn == "logic" and col_letter in ("L", "M"):
        return value
    # owner 列 P：仅在 --mask-owners 时替换
    if sn == "logic" and col_letter == "P":
        return mask_owner(value) if args.mask_owners else value
    # 其余字符串：仅在 --anon-signals 时保结构脱敏
    if args.anon_signals:
        return anon_signal(value)
    return value


def render_row_line(rows, ri, max_col, sheet_name, args):
    """把一行渲染成 '[row N] A=.. | C=..' （跳过空单元格）。logic 的 L 列不截断。"""
    is_logic = sheet_name.lower() == "logic"
    cells = []
    for c in range(max_col):
        letter = get_column_letter(c + 1)
        raw = rows[ri][c] if c < len(rows[ri]) else None
        v = transform_cell(sheet_name, letter, raw, args)
        no_trunc = is_logic and letter in ("L", "M")
        sv = cell_str(v, args.maxlen, no_trunc)
        if sv != "":
            cells.append("%s=%s" % (letter, sv))
    return "[row %d] %s" % (ri + 1, " | ".join(cells))


# ---------- 输出 ----------
def dump_sheet(out, ws, sheet_name, args):
    max_row = ws.max_row
    max_col = ws.max_column
    rows = read_rows(ws, max_row)
    is_logic = sheet_name.lower() == "logic"

    out.append("=" * 70)
    out.append("==== SHEET: %s ====" % sheet_name)
    out.append("dimensions(原始报告): %s 行 x %s 列   (实际扫描 %d 行)"
               % (max_row, max_col, len(rows)))

    if not rows:
        out.append("(空表)")
        return

    # 列字母 ↔ 表头(第1行)
    header = rows[0]
    out.append("--- 列映射 (字母 -> 第1行内容) ---")
    line = []
    for c in range(max_col):
        letter = get_column_letter(c + 1)
        h = transform_cell(sheet_name, letter,
                           header[c] if c < len(header) else None, args)
        line.append("%s=%s" % (letter, cell_str(h, args.maxlen)))
    out.append(" | ".join(line))

    # 前几行原文（非精简模式才输出，帮我判断表头是否跨多行）
    if not args.compact:
        out.append("--- 前 5 行原文 ---")
        for ri in range(min(5, len(rows))):
            out.append(render_row_line(rows, ri, max_col, sheet_name, args))

    # 样本数据行（跳过空行）
    out.append("--- 样本数据行 (最多 %d 行, 跳过空行) ---" % args.rows)
    shown = 0
    for ri in range(1, len(rows)):
        if shown >= args.rows:
            break
        if is_blank_row(rows[ri]):
            continue
        out.append(render_row_line(rows, ri, max_col, sheet_name, args))
        shown += 1

    # logic 页：列画像（精简模式也保留，很有用）；竖排仅非精简模式输出
    if is_logic:
        dump_logic_profile(out, rows, max_col, sheet_name, args)
        if not args.compact:
            dump_logic_vertical(out, rows, max_col, sheet_name, args)


def dump_logic_profile(out, rows, max_col, sheet_name, args):
    out.append("--- [logic] 列画像 (非空数, 去重数, 取值/样本) ---")
    header = rows[0]
    for c in range(max_col):
        letter = get_column_letter(c + 1)
        vals = []
        for ri in range(1, len(rows)):
            v = rows[ri][c] if c < len(rows[ri]) else None
            if v is None or (isinstance(v, str) and v.strip() == ""):
                continue
            vals.append(transform_cell(sheet_name, letter, v, args))
        nonempty = len(vals)
        if nonempty == 0:
            continue
        distinct = list(dict.fromkeys(cell_str(v, args.maxlen) for v in vals))
        hname = cell_str(header[c] if c < len(header) else None, 40)
        if len(distinct) <= 20:
            detail = "取值=%s" % distinct
        else:
            detail = "样本=%s" % distinct[:6]
        out.append("  col %s [%s]: 非空=%d, 去重=%d, %s"
                   % (letter, hname, nonempty, len(distinct), detail))


def dump_logic_vertical(out, rows, max_col, sheet_name, args):
    out.append("--- [logic] 前 %d 个数据行竖排 (L/M 列不截断) ---" % args.rows)
    header = rows[0]
    shown = 0
    for ri in range(1, len(rows)):
        if shown >= args.rows:
            break
        if is_blank_row(rows[ri]):
            continue
        out.append("[row %d]" % (ri + 1))
        for c in range(max_col):
            letter = get_column_letter(c + 1)
            raw = rows[ri][c] if c < len(rows[ri]) else None
            if raw is None or (isinstance(raw, str) and raw.strip() == ""):
                continue
            v = transform_cell(sheet_name, letter, raw, args)
            no_trunc = letter in ("L", "M")
            hname = cell_str(header[c] if c < len(header) else None, 30)
            out.append("    %-3s [%s]: %s"
                       % (letter, hname, cell_str(v, args.maxlen, no_trunc)))
        shown += 1


def safe_name(s):
    return re.sub(r"[^A-Za-z0-9_.-]", "_", s)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("excel", help="Excel 文件路径 (.xlsx)")
    ap.add_argument("--rows", type=int, default=20)
    ap.add_argument("--out", default=None)
    ap.add_argument("--sheets", default=None, help="逗号分隔, 只导出这些 sheet")
    ap.add_argument("--compact", action="store_true")
    ap.add_argument("--split", action="store_true")
    ap.add_argument("--anon-signals", action="store_true")
    ap.add_argument("--mask-owners", action="store_true")
    ap.add_argument("--maxlen", type=int, default=80)
    args = ap.parse_args()

    if not os.path.isfile(args.excel):
        sys.exit("找不到文件: %s" % args.excel)
    if not args.excel.lower().endswith(".xlsx"):
        sys.exit("只支持 .xlsx，请先在 Excel 里另存为 .xlsx")

    wb = openpyxl.load_workbook(args.excel, data_only=True, read_only=True)
    all_sheets = wb.sheetnames

    if args.sheets:
        want = [s.strip() for s in args.sheets.split(",") if s.strip()]
        sheets = [s for s in all_sheets if s in want]
        missing = [s for s in want if s not in all_sheets]
    else:
        prio = [s for s in PRIORITY_SHEETS if s in all_sheets]
        rest = [s for s in all_sheets if s not in prio]
        sheets = prio + rest
        missing = []

    header_lines = []
    header_lines.append("Dreg Excel 结构导出")
    header_lines.append("文件: %s" % os.path.basename(args.excel))
    header_lines.append("全部 sheet (%d): %s" % (len(all_sheets), all_sheets))
    header_lines.append("本次导出 sheet: %s" % sheets)
    if missing:
        header_lines.append("⚠ 指定但不存在的 sheet: %s" % missing)
    header_lines.append("开关: compact=%s, split=%s, anon_signals=%s, "
                        "mask_owners=%s, rows=%s, maxlen=%s"
                        % (args.compact, args.split, args.anon_signals,
                           args.mask_owners, args.rows, args.maxlen))

    base = os.path.splitext(args.excel)[0]

    if args.split:
        written = []
        for sn in sheets:
            out = list(header_lines)
            out.append("")
            dump_sheet(out, wb[sn], sn, args)
            path = "%s_inspect_%s.txt" % (base, safe_name(sn))
            with open(path, "w", encoding="utf-8") as f:
                f.write("\n".join(out))
            written.append((path, len(out)))
        wb.close()
        print("已分页导出 %d 个文件 (可分批发送):" % len(written))
        for p, n in written:
            print("  %s  (%d 行)" % (p, n))
        print("建议先发 *_logic.txt 给我。")
        return

    out = list(header_lines)
    for sn in sheets:
        out.append("")
        dump_sheet(out, wb[sn], sn, args)
    wb.close()

    out_path = args.out if args.out else base + "_inspect.txt"
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(out))
    print("已导出: %s" % out_path)
    print("行数: %d" % len(out))
    print("请过目内容后，把 txt 全文贴给我。")


if __name__ == "__main__":
    main()
