# -*- coding: utf-8 -*-
"""
sv_writer.py — 把 logic 信号 + 测试向量渲染为 wr_rf_tc.sv 的过程语句体。

格式（generation-rules）：
  - RO 输入 → force `ENV_RF.<wire> = 16'h<值>;
  - 同地址 RW 输入合并一条 `RF_WRITE(10'h<addr>, 16'h<寄存器值>);  寄存器值=OR(字段值<<lsb)
  - 每条驱动后跟 uvm_report_info(函数式)；全部驱动后 #1ps；
  - 正向断言对齐 for_test 模板（id 作字符串进 $sformatf）：
      assert (`ENV_RF.<输出K原文> == <宽>'b<二进制>) begin
        uvm_report_info("write assert rf_test",
          $sformatf("assert_%s: %s, sim out:0x%0h, you set:0x%0h", "<R>_T<n>", "<out>", `ENV_RF.<out>, <exp>), UVM_LOW);
      end else begin uvm_report_error(... 同上 ...); end
  - 负向用例（干净日志风格，2026-06-02 用户拍板）：断言条件反写为 !=，
      按设计 mismatch → info(NEG-OK 标签，不产生 UVM_ERROR)；
      输出真等于故意填错值 → error(NEG-BROKEN 标签)。
      这样 log 的 UVM_ERROR 总数 = 真问题数，回归脚本不会被反例污染。id 仍带 _NEG 后缀。
  - 可选追加（前半段格式不变，新信息全部追加在消息尾部，纯英文）：
      owner_in_msg → 消息尾部加 ", owner:<P列>"（半夜看 log 直接知道是谁的信号）
      counters     → fail 分支里加计数器++，配合 render_file(summary=True) 出末尾汇总

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
# ── 消息尾部追加段（前半段 ASSERT_MSG 保持与 VBA 格式一字不差，便于已有 log 解析脚本继续工作） ──
OWNER_FMT = ", owner:%s"                                              # owner 追加段(作 %s 实参传入)
NEG_OK_TAIL = ", NEG-OK(intentional wrong value, mismatch expected)"  # 反例按设计工作
NEG_BROKEN_TAIL = ", NEG-BROKEN(output equals the wrong value)"       # 反例出真问题
# ── 末尾汇总（render_file(summary=True) 时把语句体包进命名块 + 计数器） ──
SUMMARY_ID = "rf_test summary"        # uvm id for the end-of-test summary line
SUMMARY_BLOCK = "dreg_rf_test"        # named begin/end block wrapping the whole body
CNT_REAL_FAIL = "dreg_n_real_fail"    # counter: positive asserts that really failed
CNT_NEG_BROKEN = "dreg_n_neg_broken"  # counter: negative asserts whose output == wrong value


def _ascii(s):
    """产物必须纯 ASCII 英文，且不能破坏 SV 字符串字面量(否则 elaboration 失败)：
    - 非 ASCII → ?
    - 换行/回车/制表等空白折叠成单空格(Excel 单元格 Alt+Enter 换行很常见，SV 字符串不能跨行)
    - 其余控制字符 → ?
    - 反斜杠 → /(尾部 \\ 会把收尾引号转义掉)；双引号 → 单引号(防字符串提前断裂)
    """
    txt = str(s).encode("ascii", "replace").decode()
    txt = " ".join(txt.split())                                   # 折叠 \n\r\t 与连续空格
    txt = "".join(c if c.isprintable() else "?" for c in txt)     # 残余控制字符
    return txt.replace("\\", "/").replace('"', "'")


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
                        probe_prefix="", owner_in_msg=False, counters=False):
    """
    返回 (lines:list[str], stats:dict)。comments=True 时每信号加 1 行 // <名> 便于导航(默认零注释)。
    node: 已解析(或 cone 展开后)的表达式 AST；None 则从 sig.expr 解析。
    probe_prefix: 探针层级前缀(如 "U_BT_LP_PLL_DIG")——输出网不在 ENV_RF 顶层、
                  而在其子模块里时用：`ENV_RF.<prefix>.<rtl_name>。
    owner_in_msg: True 时断言消息尾部追加 ", owner:<logic P列>"（前半段格式不变）。
    counters: True 时 fail 分支里加计数器++（须配合 render_file(summary=True) 包裹声明，
              否则产物里是未声明变量；generator.build 保证两者同开同关）。
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

    # owner 作 $sformatf 的 %s 实参追加在最后（不嵌进格式串，防 owner 含 % 触发格式转义）
    owner_fmt, owner_arg = "", ""
    if owner_in_msg:
        owner_fmt = OWNER_FMT
        owner_arg = ',"%s"' % _ascii(sig.owner or "NA")

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
        lines.append("assert_%s:" % aid_str)
        lines.append("")
        if vec.is_negative:
            # 反例（干净日志风格）：exp 是故意填错的值 → 断言反写 !=。
            #   mismatch(条件为真) = 按设计工作 → info NEG-OK，不产生 UVM_ERROR；
            #   输出真等于错值(条件为假) = 真问题(探针/RTL/checker) → error NEG-BROKEN。
            if comments:
                lines.append("// NEG: wrong value set on purpose; mismatch (NEG-OK) is the designed result")
            cond, info_tail, err_tail = "!=", NEG_OK_TAIL, NEG_BROKEN_TAIL
            err_cnt = CNT_NEG_BROKEN
        else:
            cond, info_tail, err_tail = "==", "", ""
            err_cnt = CNT_REAL_FAIL
        msg_info = ('$sformatf("%s","%s","%s",%s, %s%s)'
                    % (ASSERT_MSG + info_tail + owner_fmt, aid_str, rtl_name, lhs, exp, owner_arg))
        msg_err = ('$sformatf("%s","%s","%s",%s, %s%s)'
                   % (ASSERT_MSG + err_tail + owner_fmt, aid_str, rtl_name, lhs, exp, owner_arg))
        lines.append("assert (%s%s%s)begin" % (lhs, cond, exp))
        lines.append('%s%s("%s",%s,UVM_LOW);' % (BODY_INDENT, UVM_INFO, ASSERT_ID, msg_info))
        lines.append("end")
        lines.append("else begin")
        lines.append('%s%s("%s",%s,UVM_LOW);' % (BODY_INDENT, UVM_ERROR, ASSERT_ID, msg_err))
        if counters:
            lines.append("%s%s++;" % (BODY_INDENT, err_cnt))
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
def render_file(blocks, header_info=None, comments=False, summary=False, block_suffix=""):
    """blocks: list[(lines, stats)]。comments=True 才加文件头注释(默认无，纯语句体)。

    summary=True 时把整个语句体包进命名 begin/end 块：块首声明计数变量、
    末尾打一行汇总(信号数/断言数/正反例数 + 运行时真 FAIL 数)。
    须与 render_signal_block(counters=True) 配套使用（generator.build 保证一致）。
    block_suffix: 命名块后缀(如 "_pos"/"_neg")——『仅正向』『仅负向』两份产物若被贴进
    同一个 task/initial 作用域，同名兄弟命名块是 elaboration 重名错误，后缀让它们可共存。
    """
    out = ["// auto-generated by Dreg_verify -- do not edit", ""] if comments else []
    body = []
    total_unresolved = []
    for lines, stats in blocks:
        body.extend(lines)
        if stats["unresolved"]:
            total_unresolved.append((stats["out_name"], stats["unresolved"]))
    if total_unresolved:
        body.append("// unresolved inputs (check name/type/addr or use --force-signals/--rfwrite-signals):")
        for name, us in total_unresolved:
            body.append("//   %s: %s" % (name, "; ".join(us)))
    if summary:
        body = _wrap_with_summary(blocks, body, block_suffix)
    out.extend(body)
    return "\n".join(out) + "\n"


def _wrap_with_summary(blocks, body, block_suffix=""):
    """汇总模式：命名 begin/end 块 + 计数变量 + 末尾汇总行。

    声明与赋 0 分开写——SV 规定静态作用域(如 module 的 initial 块)里带初始化的
    块内声明必须显式 static/automatic 限定；分开写则贴进 task 体或 initial 块都合法。
    信号数/断言数/正反例数是生成时已知的常量，直接写进格式串；
    真 FAIL 数是运行时计数器，作 $sformatf 实参。
    计数变量声明在命名块内部(作用域随块)，块名不同即可在同一父作用域共存。
    """
    n_signals = len(blocks)
    n_asserts = sum(st["n_vectors"] for _, st in blocks)
    n_neg = sum(st["n_negative"] for _, st in blocks)
    n_pos = n_asserts - n_neg
    block_name = SUMMARY_BLOCK + (block_suffix or "")
    head = [
        "begin : %s" % block_name,
        "%sint unsigned %s;" % (BODY_INDENT, CNT_REAL_FAIL),
        "%sint unsigned %s;" % (BODY_INDENT, CNT_NEG_BROKEN),
        "%s%s = 0;" % (BODY_INDENT, CNT_REAL_FAIL),
        "%s%s = 0;" % (BODY_INDENT, CNT_NEG_BROKEN),
        "",
    ]
    fmt = ("signals:%d asserts:%d (positive:%d, negative:%d), REAL FAIL:%%0d, NEG broken:%%0d"
           % (n_signals, n_asserts, n_pos, n_neg))
    tail = [
        "",
        '%s%s("%s",$sformatf("%s",%s,%s),UVM_LOW);'
        % (BODY_INDENT, UVM_INFO, SUMMARY_ID, fmt, CNT_REAL_FAIL, CNT_NEG_BROKEN),
        "end : %s" % block_name,
    ]
    return head + body + tail
