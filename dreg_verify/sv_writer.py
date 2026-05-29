# -*- coding: utf-8 -*-
"""
sv_writer.py — 把 logic 信号 + 测试向量渲染为 wr_rf_tc.sv 的过程语句体。

格式（generation-rules）：
  - RO 输入 → force `ENV_RF.<wire> = 16'h<值>;
  - 同地址 RW 输入合并一条 `RF_WRITE(10'h<addr>, 16'h<寄存器值>);  寄存器值=OR(字段值<<lsb)
  - 每条驱动后跟 uvm_report_info(函数式)；全部驱动后 #1ps；
  - 断言对齐 for_test 模板（无标号，id 作字符串进 $sformatf）：
      assert (`ENV_RF.<输出K原文> == <宽>'b<二进制>) begin
        uvm_report_info("write assert rf_test",
          $sformatf("assert_%s: %s, sim out:0x%0h, you set:0x%0h", "<R>_T<n>", "<out>", `ENV_RF.<out>, <exp>), UVM_LOW);
      end else begin uvm_report_error(... 同上 ...); end
  - 负向用例 id 加 _NEG，期望值故意填错，注释说明。

UVM 消息字符串集中在下方常量，按需调整以贴合你们的脚手架。
"""

from . import expr as E

# ───────────────────────────── 可调脚手架常量 ─────────────────────────────
ENV = "ENV_RF"          # 寄存器环境宏前缀（`ENV_RF.xxx）
RF_WRITE = "RF_WRITE"   # 寄存器写宏名
INDENT = "  "
ADDR_WIDTH_HEX = 10     # RF_WRITE 地址用 10'h
DATA_WIDTH_HEX = 16     # 数据/force 用 16'h
# 对齐 for_test 模板：用函数式 uvm_report_info/uvm_report_error(不带反引号)，消息走 $sformatf
UVM_INFO = "uvm_report_info"     # 函数：uvm_report_info(id, msg, verbosity)
UVM_ERROR = "uvm_report_error"   # 函数：uvm_report_error(id, msg, verbosity)
UVM_MSG_ID = "write assert rf_test"   # 消息 id 字段（for_test 用此串）


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


# ───────────────────────────── 驱动计算（结构化，.sv 与人读报告共用） ─────────────────────────────
def compute_drives(vec, bindings, used_vars):
    """
    把一个测试向量解析为结构化驱动信息（不渲染 SV 文本）。
    返回 (forces, writes, unresolved):
      forces: [{wire, hex, width, base, src}]                  —— 逐个 force
      writes: [{addr, hex, fields:[{base,lsb,hex}]}]           —— 同地址合并的 RF_WRITE
      unresolved: [(letter, base, note)]
    同名输入(同一物理信号占多个变量)只驱动一次。
    """
    forces, writes, unresolved = [], [], []
    ro = []
    rw_by_addr = {}
    seen_ro, seen_rw = set(), set()
    for ltr in used_vars:
        b = bindings.get(ltr)
        if b is None:
            unresolved.append((ltr, "", "变量无绑定"))
            continue
        val = vec.assignments.get(ltr, 0)
        if not b.resolved:
            unresolved.append((ltr, b.base, b.note or "未解析"))
            continue
        if b.kind == "RO":
            if b.wire in seen_ro:
                continue
            seen_ro.add(b.wire)
            ro.append((b, val))
        else:  # RW
            key = (b.address, b.base.lower(), b.reg_lsb)
            if key in seen_rw:
                continue
            seen_rw.add(key)
            rw_by_addr.setdefault(b.address, []).append((b, val))

    for b, val in ro:
        hw = max(DATA_WIDTH_HEX, b.width)
        src = {"tmm": "RO", "regmap": "RO", "logic": "级联wire", "wire": "wire"}.get(b.found_in, "RO")
        forces.append({"wire": b.wire, "hex": fmt_hex(val, hw),
                       "width": b.width, "base": b.base, "src": src})

    for addr in sorted(rw_by_addr.keys()):
        regval = 0
        fields = []
        for b, val in rw_by_addr[addr]:
            lsb = b.reg_lsb or 0
            # ⭐先按字段位宽裁剪字段值，再左移——否则溢出位会侵入同地址相邻字段
            fw = (b.reg_msb - b.reg_lsb + 1) if (b.reg_msb is not None and b.reg_lsb is not None) else 16
            fval = val & E.mask(fw)
            regval |= (fval << lsb)
            fields.append({"base": b.base, "lsb": lsb, "hex": fmt_hex(fval)})
        regval &= E.mask(16)
        writes.append({"addr": fmt_addr(addr), "hex": fmt_hex(regval), "fields": fields})

    return forces, writes, unresolved


# ───────────────────────────── 驱动行 ─────────────────────────────
def _build_drive_lines(vec, bindings, used_vars):
    """
    根据向量为本 tc 生成驱动语句行 + 诊断。
    返回 (lines:list[str], unresolved:list[str])
    """
    lines = []
    forces, writes, unresolved = compute_drives(vec, bindings, used_vars)

    for (ltr, base, note) in unresolved:
        lines.append("%s// TODO 未解析输入 %s=%s（%s），请核对名称/类型/地址"
                     % (INDENT, ltr, base, note))

    # RO：逐个 force（force 字面量按 wire 位宽自适应，>16bit 不截断）
    for f in forces:
        lines.append("%sforce `%s.%s = %s;   // %s (%s, 位宽%d)"
                     % (INDENT, ENV, f["wire"], f["hex"], f["base"], f["src"], f["width"]))
        lines.append('%s%s("%s", "drive %s = %s", UVM_LOW);'
                     % (INDENT, UVM_INFO, UVM_MSG_ID, f["wire"], f["hex"]))

    # RW：同地址合并成一条 RF_WRITE
    for w in writes:
        desc = ", ".join("%s<<%d=%s" % (fl["base"], fl["lsb"], fl["hex"]) for fl in w["fields"])
        lines.append("%s`%s(%s, %s);   // %s"
                     % (INDENT, RF_WRITE, w["addr"], w["hex"], desc))
        lines.append('%s%s("%s", "RF_WRITE %s = %s", UVM_LOW);'
                     % (INDENT, UVM_INFO, UVM_MSG_ID, w["addr"], w["hex"]))

    unresolved_strs = ["%s(%s): %s" % (l, b, n) for (l, b, n) in unresolved]
    return lines, unresolved_strs


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
        if vec.is_negative:
            n_neg += 1
        lines.append("%s#1ps;" % INDENT)
        exp = fmt_bin(vec.asserted_value, vec.exp_width)
        aid_str = "%s_%s" % (aid, tag)              # 如 160_T0 / 160_T0_NEG
        lhs = "`%s.%s" % (ENV, sig.out_name)
        msg = ('$sformatf("assert_%%s: %%s, sim out:0x%%0h, you set:0x%%0h", '
               '"%s", "%s", %s, %s)' % (aid_str, sig.out_name, lhs, exp))
        # 对齐 for_test：assert(cond) begin uvm_report_info(..) end else begin uvm_report_error(..) end
        lines.append("%sassert (%s == %s) begin" % (INDENT, lhs, exp))
        lines.append('%s%s%s("%s", %s, UVM_LOW);' % (INDENT, INDENT, UVM_INFO, UVM_MSG_ID, msg))
        lines.append("%send" % INDENT)
        lines.append("%selse begin" % INDENT)
        lines.append('%s%s%s("%s", %s, UVM_LOW);' % (INDENT, INDENT, UVM_ERROR, UVM_MSG_ID, msg))
        lines.append("%send" % INDENT)
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
