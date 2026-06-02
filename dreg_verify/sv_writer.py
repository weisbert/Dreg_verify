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

# ── Scaffolding constants (tweak to match your environment). Generated .sv is ASCII-only. ──
ENV = "ENV_RF"          # register env macro prefix: `ENV_RF.xxx
RF_WRITE = "RF_WRITE"   # register-write macro name
BODY_INDENT = "    "    # indent inside assert begin/end blocks (4 spaces)
ADDR_WIDTH_HEX = 10     # RF_WRITE address width: 10'h
DATA_WIDTH_HEX = 16     # data / force width: 16'h
UVM_INFO = "uvm_report_info"     # function: uvm_report_info(id, msg, verbosity)
UVM_ERROR = "uvm_report_error"   # function: uvm_report_error(id, msg, verbosity)
DRIVE_ID = "rf_test"                  # uvm id for input-drive messages
ASSERT_ID = "write assert rf_test"    # uvm id for assert messages
DRIVE_WIRE_MSG = "input wire name:%s, wire data:%0h"
DRIVE_REG_MSG = "input reg addr:%0h, reg data:%0h"
ASSERT_MSG = "assert_%s: %s, sim out:0x%0h, you set:0x%0h"


# ───────────────────────────── 值格式化（最小位数、大写，不补零） ─────────────────────────────
def fmt_hex(value, hex_width=DATA_WIDTH_HEX):
    return "%d'h%X" % (hex_width, value & E.mask(hex_width))


def fmt_addr(addr):
    return "%d'h%X" % (ADDR_WIDTH_HEX, addr & E.mask(ADDR_WIDTH_HEX))


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
    rw_by_addr = {}      # addr -> {字段基名: {"b": binding, "val": 拼好的字段值}}
    seen_ro = set()
    for ltr in used_vars:
        b = bindings.get(ltr)
        if b is None:
            unresolved.append((ltr, "", "变量无绑定"))
            continue
        val = vec.assignments.get(ltr, 0)
        if not b.resolved:
            unresolved.append((ltr, b.base, b.note or "未解析"))
            continue
        # 同一物理信号的不同 bit 切片(A=x[1]、B=x[0])各自带值；slice_lsb 决定它落在字段哪一位
        slice_lsb = b.slice_lsb or 0
        if b.kind == "RO":
            key = (b.wire, b.slice_msb, b.slice_lsb)
            if key in seen_ro:
                continue
            seen_ro.add(key)
            ro.append((b, val))
        else:  # RW：同名字段的多个切片拼成一个字段值，再合入寄存器
            ent = rw_by_addr.setdefault(b.address, {}).setdefault(
                b.base.lower(), {"b": b, "val": 0})
            ent["val"] |= (val & E.mask(b.width)) << slice_lsb

    for b, val in ro:
        hw = max(DATA_WIDTH_HEX, b.width)
        forces.append({"wire": b.wire_lhs, "hex": fmt_hex(val, hw),
                       "width": b.width, "base": b.base})   # wire_lhs：多位带 [msb:lsb] 切片

    for addr in sorted(rw_by_addr.keys()):
        regval = 0
        fields = []
        for base_low in rw_by_addr[addr]:
            ent = rw_by_addr[addr][base_low]
            b = ent["b"]
            lsb = b.reg_lsb or 0
            # ⭐先按字段位宽裁剪字段值，再左移——否则溢出位会侵入同地址相邻字段
            fw = (b.reg_msb - b.reg_lsb + 1) if (b.reg_msb is not None and b.reg_lsb is not None) else 16
            fval = ent["val"] & E.mask(fw)
            regval |= (fval << lsb)
            fields.append({"base": b.base, "lsb": lsb, "hex": fmt_hex(fval)})
        regval &= E.mask(16)
        writes.append({"addr": fmt_addr(addr), "hex": fmt_hex(regval), "fields": fields})

    return forces, writes, unresolved


# ───────────────────────────── 驱动行（朴素格式，对齐真实 VBA 输出，纯 ASCII） ─────────────────────────────
def _build_drive_lines(vec, bindings, used_vars):
    """返回 (lines, unresolved_strs)。lines 为顶格的 force/`RF_WRITE/uvm_report_info 语句。"""
    lines = []
    forces, writes, unresolved = compute_drives(vec, bindings, used_vars)

    for (ltr, base, note) in unresolved:
        lines.append("// TODO: unresolved input %s=%s -- check name/type/addr" % (ltr, base))

    # 顺序对齐真实 for_test：先所有 `RF_WRITE(按地址排序)，再所有 force。
    # RW -> 同地址合并成一条 `RF_WRITE
    for w in writes:
        lines.append("`%s(%s,%s);" % (RF_WRITE, w["addr"], w["hex"]))
        lines.append('%s("%s",$sformatf("%s",%s, %s),UVM_LOW);'
                     % (UVM_INFO, DRIVE_ID, DRIVE_REG_MSG, w["addr"], w["hex"]))

    # RO -> force wire（force 字面量按 wire 位宽自适应，>16bit 不截断）
    for f in forces:
        lines.append("force `%s.%s=%s;" % (ENV, f["wire"], f["hex"]))
        lines.append('%s("%s",$sformatf("%s","%s", %s),UVM_LOW);'
                     % (UVM_INFO, DRIVE_ID, DRIVE_WIRE_MSG, f["wire"], f["hex"]))

    unresolved_strs = ["%s=%s" % (l, b) for (l, b, n) in unresolved]   # ASCII only(中文诊断走 CLI)
    return lines, unresolved_strs


def test_label(vec):
    """测试名：自定义 name 优先，否则 T<index>；负向保证以 _NEG 结尾(便于日志一眼辨认)。
    .sv 断言 id、人读报告、GUI 列头都用它，保持一致。"""
    base = vec.name if getattr(vec, "name", None) else ("T%d" % vec.index)
    if vec.is_negative and not base.upper().endswith("NEG"):
        base += "_NEG"
    return base


def _s(v):
    return "" if v is None else str(v)


# ───────────────────────────── 单个信号块 ─────────────────────────────
def render_signal_block(sig, bindings, vectors, meta, comments=False, node=None,
                        probe_prefix=""):
    """
    返回 (lines:list[str], stats:dict)。comments=True 时每信号加 1 行 // <名> 便于导航(默认零注释)。
    node: 已解析(或 cone 展开后)的表达式 AST；None 则从 sig.expr 解析。
    probe_prefix: 探针层级前缀(如 "U_BT_LP_PLL_DIG")——输出网不在 ENV_RF 顶层、
                  而在其子模块里时用：`ENV_RF.<prefix>.<rtl_name>。
    """
    lines = []
    used_vars = E.collect_vars(node if node is not None else E.parse(sig.expr))
    aid = sig.assert_id or "X"
    # 探针名用 RTL 真实网名（ls 行 = K 列名 + "_ls" 后缀），不是 K 列原文
    rtl_name = getattr(sig, "rtl_name", sig.out_name)
    if probe_prefix:
        rtl_name = "%s.%s" % (probe_prefix.strip("."), rtl_name)
    lhs = "`%s.%s" % (ENV, rtl_name)

    if comments:
        lines.append("// %s" % sig.out_name)

    block_unresolved = set()
    n_neg = 0
    for vec in vectors:
        drive, unresolved = _build_drive_lines(vec, bindings, used_vars)
        lines.extend(drive)
        for u in unresolved:
            block_unresolved.add(u)
        if vec.is_negative:
            n_neg += 1
        lines.append("#1ps;")
        exp = fmt_bin(vec.asserted_value, vec.exp_width)
        # 断言 id = <R>_<测试名>；测试名见 test_label(自定义名/T<index>，负向带 _NEG)
        aid_str = "%s_%s" % (aid, test_label(vec))   # e.g. 8_T0 / 8_T1_NEG / 8_my_case
        if comments and vec.is_negative:
            lines.append("// NEG: 故意填错期望值，此断言预期应 FAIL(用于自检 checker 能抓错)")
        lines.append("assert_%s:" % aid_str)
        lines.append("")
        msg = ('$sformatf("%s","%s","%s",%s, %s)'
               % (ASSERT_MSG, aid_str, rtl_name, lhs, exp))
        lines.append("assert (%s==%s)begin" % (lhs, exp))
        lines.append('%s%s("%s",%s,UVM_LOW);' % (BODY_INDENT, UVM_INFO, ASSERT_ID, msg))
        lines.append("end")
        lines.append("else begin")
        lines.append('%s%s("%s",%s,UVM_LOW);' % (BODY_INDENT, UVM_ERROR, ASSERT_ID, msg))
        lines.append("end")
        lines.append("")

    stats = {
        "out_name": sig.out_name, "rtl_name": rtl_name, "assert_id": aid, "owner": sig.owner,
        "n_vectors": len(vectors), "n_negative": n_neg,
        "unresolved": sorted(block_unresolved),
        "truncated": meta.get("truncated", False),
    }
    return lines, stats


# ───────────────────────────── 整个文件 ─────────────────────────────
def render_file(blocks, header_info=None, comments=False):
    """blocks: list[(lines, stats)]。comments=True 才加文件头注释(默认无，纯语句体)。"""
    out = ["// auto-generated by Dreg_verify -- do not edit", ""] if comments else []
    total_unresolved = []
    for lines, stats in blocks:
        out.extend(lines)
        if stats["unresolved"]:
            total_unresolved.append((stats["out_name"], stats["unresolved"]))
    if total_unresolved:
        out.append("// unresolved inputs (check name/type/addr or use --force-signals/--rfwrite-signals):")
        for name, us in total_unresolved:
            out.append("//   %s: %s" % (name, "; ".join(us)))
    return "\n".join(out) + "\n"
