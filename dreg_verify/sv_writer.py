# -*- coding: utf-8 -*-
"""
sv_writer.py — 把 logic 信号 + 测试向量渲染为 wr_rf_tc.sv 的过程语句体。

格式（generation-rules）：
  - RO 输入 → force `ENV_RF.<wire> = 16'h<值>;
  - 同地址 RW 输入合并一条 `RF_WRITE(10'h<addr>, 16'h<寄存器值>);  寄存器值=OR(字段值<<lsb)
  - 每条驱动后跟 `uvm_report_info；全部驱动后 #1ps；
  - assert_<R>_T<n>: assert(`ENV_RF.<输出K原文> == <宽>'b<二进制>) else `uvm_error; `uvm_report_info
  - 负向用例标签加 _NEG，期望值故意填错，注释说明。

UVM 消息字符串集中在下方常量，按需调整以贴合你们的脚手架。
"""

from . import expr as E

# ───────────────────────────── 可调脚手架常量 ─────────────────────────────
ENV = "ENV_RF"          # 寄存器环境宏前缀（`ENV_RF.xxx）
RF_WRITE = "RF_WRITE"   # 寄存器写宏名
INDENT = "  "
ADDR_WIDTH_HEX = 10     # RF_WRITE 地址用 10'h
DATA_WIDTH_HEX = 16     # 数据/force 用 16'h
UVM_COMP = "uvm_test_top"   # uvm_report_info 的 id 字段（按需改）


# ───────────────────────────── 值格式化 ─────────────────────────────
def fmt_hex(value, hex_width=DATA_WIDTH_HEX):
    bits = (hex_width)
    hexdigits = (bits + 3) // 4
    return "%d'h%0*X" % (bits, hexdigits, value & E.mask(bits))


def fmt_addr(addr):
    hexdigits = (ADDR_WIDTH_HEX + 3) // 4
    return "%d'h%0*X" % (ADDR_WIDTH_HEX, hexdigits, addr & E.mask(ADDR_WIDTH_HEX))


def fmt_bin(value, width):
    if width <= 0:
        width = 1
    return "%d'b%s" % (width, format(value & E.mask(width), "0%db" % width))


# ───────────────────────────── 驱动行 ─────────────────────────────
def _build_drive_lines(vec, bindings, used_vars):
    """
    根据向量为本 tc 生成驱动语句行 + 诊断。
    返回 (lines:list[str], unresolved:list[str])
    """
    lines = []
    unresolved = []

    # 分 RO / RW；RW 按地址聚合合并
    ro = []
    rw_by_addr = {}   # addr -> list[(binding, fieldval)]
    for ltr in used_vars:
        b = bindings.get(ltr)
        if b is None:
            unresolved.append("变量 %s 无绑定" % ltr)
            continue
        val = vec.assignments.get(ltr, 0)
        if not b.resolved:
            unresolved.append("%s(%s): %s" % (ltr, b.base, b.note or "未解析"))
            lines.append("%s// TODO 未解析输入 %s=%s（%s），请核对名称/类型/地址"
                         % (INDENT, ltr, b.base, b.note or b.kind))
            continue
        if b.kind == "RO":
            ro.append((b, val))
        else:  # RW
            rw_by_addr.setdefault(b.address, []).append((b, val))

    # RO：逐个 force（force 字面量按 wire 位宽自适应，>16bit 不截断）
    for b, val in ro:
        hw = max(DATA_WIDTH_HEX, b.width)
        src = {"tmm": "RO", "regmap": "RO", "logic": "级联wire", "wire": "wire"}.get(b.found_in, "RO")
        lines.append("%sforce `%s.%s = %s;   // %s (%s, 位宽%d)"
                     % (INDENT, ENV, b.wire, fmt_hex(val, hw), b.base, src, b.width))
        lines.append('%s`uvm_report_info("%s", "drive %s = %s", UVM_LOW);'
                     % (INDENT, UVM_COMP, b.wire, fmt_hex(val, hw)))

    # RW：同地址合并
    for addr in sorted(rw_by_addr.keys()):
        regval = 0
        members = rw_by_addr[addr]
        desc = []
        for b, val in members:
            lsb = b.reg_lsb or 0
            # ⭐先按字段位宽裁剪字段值，再左移——否则溢出位会侵入同地址相邻字段
            if b.reg_msb is not None and b.reg_lsb is not None:
                fw = b.reg_msb - b.reg_lsb + 1
            else:
                fw = 16
            fval = val & E.mask(fw)
            regval |= (fval << lsb)
            desc.append("%s<<%d=%s" % (b.base, lsb, fmt_hex(fval)))
        regval &= E.mask(16)
        lines.append("%s`%s(%s, %s);   // %s"
                     % (INDENT, RF_WRITE, fmt_addr(addr), fmt_hex(regval), ", ".join(desc)))
        lines.append('%s`uvm_report_info("%s", "RF_WRITE %s = %s", UVM_LOW);'
                     % (INDENT, UVM_COMP, fmt_addr(addr), fmt_hex(regval)))

    return lines, unresolved


def _s(v):
    return "" if v is None else str(v)


# ───────────────────────────── 单个信号块 ─────────────────────────────
def render_signal_block(sig, bindings, vectors, meta):
    """
    返回 (lines:list[str], stats:dict)
    """
    lines = []
    used_vars = E.collect_vars(E.parse(sig.expr))
    aid = sig.assert_id or "X"

    lines.append("// " + "─" * 70)
    lines.append("// 信号 %s   (assert_id=%s, owner=%s, type=%s, top_output=%s, Excel行=%s)"
                 % (sig.out_name, aid, sig.owner, sig.suffix, sig.top_output, sig.row))
    lines.append("//   表达式: %s" % sig.expr)
    in_desc = ", ".join("%s=%s[%d]%s" % (
        ltr, bindings[ltr].base, bindings[ltr].width,
        "" if bindings[ltr].resolved else "(?未解析)")
        for ltr in used_vars if ltr in bindings)
    lines.append("//   输入: %s" % in_desc)
    extra = ""
    if meta.get("truncated"):
        extra += "  ⚠已截断(丢弃%d个计划组合)" % meta["dropped"]
    if meta.get("deduped"):
        extra += "  (去重%d)" % meta["deduped"]
    lines.append("//   控制位=%s  数据位=%s  向量数=%d%s"
                 % (meta.get("control"), meta.get("data"), len(vectors), extra))
    if meta.get("missing_vars"):
        lines.append("//   ⚠ 表达式引用但 logic 行无对应输入列: %s" % meta["missing_vars"])

    block_unresolved = set()
    n_neg = 0
    for vec in vectors:
        tag = "T%d%s" % (vec.index, "_NEG" if vec.is_negative else "")
        lines.append("%s// --- %s ---%s" % (INDENT, tag,
                     "  " + vec.note if vec.is_negative else ""))
        drive, unresolved = _build_drive_lines(vec, bindings, used_vars)
        lines.extend(drive)
        for u in unresolved:
            block_unresolved.add(u)
        lines.append("%s#1ps;" % INDENT)
        exp = fmt_bin(vec.asserted_value, vec.exp_width)
        label = "assert_%s_%s" % (aid, tag)
        lines.append("%s%s: assert(`%s.%s == %s)" % (INDENT, label, ENV, sig.out_name, exp))
        if vec.is_negative:
            n_neg += 1
            lines.append('%s%selse `uvm_error("%s", "NEG-OK: checker 抓到注入错误 %s 期望应为 %s");'
                         % (INDENT, INDENT, UVM_COMP, exp, fmt_bin(vec.exp_value, vec.exp_width)))
            lines.append('%s%s`uvm_report_info("%s", "%s 负向(故意错): 若此处未报错说明 checker 失效", UVM_LOW);'
                         % (INDENT, INDENT, UVM_COMP, label))
        else:
            lines.append('%s%selse `uvm_error("%s", "断言失败: %s 期望 %s");'
                         % (INDENT, INDENT, UVM_COMP, sig.out_name, exp))
            lines.append('%s%s`uvm_report_info("%s", "%s 通过, %s == %s", UVM_LOW);'
                         % (INDENT, INDENT, UVM_COMP, label, sig.out_name, exp))
    lines.append("")

    stats = {
        "out_name": sig.out_name, "assert_id": aid, "owner": sig.owner,
        "n_vectors": len(vectors), "n_negative": n_neg,
        "unresolved": sorted(block_unresolved),
        "truncated": meta.get("truncated", False),
    }
    return lines, stats


# ───────────────────────────── 整个文件 ─────────────────────────────
def render_file(blocks, header_info=None):
    """
    blocks: list[(lines, stats)]
    header_info: dict 放到文件头注释
    返回完整文本。
    """
    out = []
    out.append("// " + "=" * 70)
    out.append("// wr_rf_tc.sv — 由 Dreg_verify 自动生成，请勿手改（改 Excel/参数后重生成）")
    if header_info:
        for k, v in header_info.items():
            out.append("// %s: %s" % (k, v))
    out.append("// 说明: 以下为过程语句体，置入对应 UVM sequence/test 的 task 内执行。")
    out.append("//   ENV_RF / RF_WRITE / uvm_* 为固定脚手架宏。")
    out.append("// " + "=" * 70)
    out.append("")
    total_unresolved = []
    for lines, stats in blocks:
        out.extend(lines)
        if stats["unresolved"]:
            total_unresolved.append((stats["out_name"], stats["unresolved"]))
    if total_unresolved:
        out.append("// " + "=" * 70)
        out.append("// ⚠ 未解析输入汇总（需人工核对名称/类型/地址，或用 --force-signals/--rfwrite-signals）:")
        for name, us in total_unresolved:
            out.append("//   %s:" % name)
            for u in us:
                out.append("//     - %s" % u)
        out.append("// " + "=" * 70)
    return "\n".join(out) + "\n"
