# -*- coding: utf-8 -*-
"""make_mirror_btlp.py — 镜像【新模型】Hi1108_Pilot_BT_LP_DREG 表的开发夹具 Excel。

⭐ Topout-rooted 重构(2026-06-23)用的测试夹具。与老的 make_mirror_excel.py(WL_RFTRX、
logic-rooted、复现 R23/24/38 历史 bug、喂 mirror_wl_dreg.xlsx 给 test_cross_boundary_mux_expand)
**并行存在、互不影响**——那张别动，这张是新模型。

数据来源：用户 2026-06-23 inspect 真表 Hi1108_Pilot_BT_LP_DREG_95P_20260623.xlsx 两半 dump
(消化见 refactor_notes/inspect_dump_20260623.md、memory refactor-topout-rooted.md)。地址用真表 d-号
为主、缺的取代表值；信号名取真名(BT_LP)。**这是【小而全】代表性切片，不是整表**——目的是覆盖
重构 cone 引擎要处理的每一类路径，方便写解析器/真值表时对着跑。

新模型要点(夹具忠实体现)：
  · 要验信号 = Topout 页 B 列(TOP Out Signal)，无后缀真名/带 _ls；**B 列与 C–G 寄存器图两套独立清单同行并列**。
  · logic N(top_output) 全=0、mux I(top_out) 全=0 —— "要验什么"不由它们定。
  · 流水线：regmap 字段(RW 主输入) →(_to_logic/_to_mux/_to_dft/_to_ls 路由)→ logic/mux/dft/ls 组合 → Topout。
  · iddq 页空、ISO 页空(只 schema)；dft 多为恒等观测(E=A)；level_shift X_to_ls→X_ls。
  · SignalPath = VBA 一次性查询工具(开发期对照 oracle，不进产物)。

夹具里的 5 类代表性 cone(对应 Topout B 列 6 个信号)：
  ① logic 选路(mode?local:line & ~iddq)   d_logic_bt_lp_rx_en  ← 4 寄存器 (for_test 金标准就是它)
  ② mux 选路(控制=另一 logic 输出)        d_bt_lp_lna_itrim[3:0] ← mux(控制 d_logic_bt_lp_tsensor) + 8 trim 寄存器
  ②' 既是输出又是 mux 控制               d_logic_bt_lp_tsensor[3:0]
  ③ 直连寄存器(dft 恒等观测)             clk_force_on  ← 寄存器本身
  ④ 电平移位输出(_ls)                    d_en_refbuf_ls ← level_shift ← logic ← 5 寄存器
  ⑤ RO 回读(非寄存器组合函数,验法待定)   pll_lock_indicator ← 无 cone(FSM/模拟状态)

用法:  python make_mirror_btlp.py [输出路径=mirror_btlp_dreg.xlsx]
"""

import sys

import openpyxl
from openpyxl.utils import column_index_from_string


def _set(ws, row, mapping):
    for col, val in mapping.items():
        ws.cell(row=row, column=column_index_from_string(col), value=val)


def _d(n):
    return "d%d" % n


# ───────────── 地址(十进制 d-号，与真表对齐处取真值)─────────────
ADDR_TOP_EN = 1       # en_pll[0] / en_dig_clk[8] / clk_force_on[9]
ADDR_TESTMODE = 9     # testmode[15] / d_en_refbuf[14]
ADDR_PLL_LINE = 23    # 0x17: d_pll_line_ctrl_mode[14]
ADDR_RO41 = 41        # 0x29 readro_reg_41: bt_mode_sel[0]/linectrl_rx_en[1]/iddq_mode[2]/pll_en[3] (虚拟/线控 RO)
ADDR_RX_LOCAL = 45    # 0x2D: linelocal_mode_ctrl[0]/linelocal_tsensor_ctrl[2]/rx_en_local[4] (RW)
ADDR_TSENS_RO = 42    # 0x2A readro: linectrl_tsensor[3:0] (线控 RO)
ADDR_TSENS_LOCAL = 50 # 0x32: tsensor_local[3:0] (RW)
ADDR_ITRIM_A = 96     # lna_itrim t1..t4 打包 (RW, _to_mux 数据)
ADDR_ITRIM_B = 97     # lna_itrim t5..t8 打包
ADDR_RO38 = 56        # READro_reg_38: pll_lock_indicator[15] 等 (RO 回读)


# ───────────── regmap：D=Reg_Name F=Type G=Signal_Name H=addr I=default J..Y=bit15..bit0(Y=bit0)
#               Z=suffix(去向) AA=suffix2 AB=top_output AE=Owner ─────────────
def _regmap(ws, r, reg, typ, signal, bit_msb, bit_lsb, addr_dec,
            suffix="to_logic", suffix2="", owner="Yao Wang"):
    _set(ws, r, {"D": reg, "F": typ, "G": signal, "H": _d(addr_dec),
                 "Z": suffix, "AA": suffix2, "AB": 0, "AE": owner})
    for b in range(bit_lsb, bit_msb + 1):
        col = column_index_from_string("Y") - b      # Y=bit0
        ws.cell(row=r, column=col, value=1)
    return r + 1


# ───────────── total_memory_map：块结构 A=名 B=bit C=reset D=DIG_TOP_PIN E=desc F=addr ─────────────
def _tmm_reg(ws, r, name, addr_dec, reset="h0"):
    _set(ws, r, {"A": name, "B": "h%X" % addr_dec, "C": reset, "D": "RW", "E": name})
    r += 1
    _set(ws, r, {"A": "Field Name", "B": "Bit Field", "C": "Reset", "D": "DIG TOP PIN", "E": "Function"})
    return r + 1


def _tmm_field(ws, r, name, bit, typ="N", desc=""):
    _set(ws, r, {"A": name, "B": bit, "C": "d0", "D": typ, "E": desc or name})
    return r + 1


# ───────────── logic：A..J=输入变量(列字母=表达式变量) K=输出 L=表达式 M=suffix N=top_output(全0) P=owner R=no ─────────────
def _logic(ws, r, out, expr, inputs, suffix="to_dft", owner="Yao Wang", note=""):
    """inputs: 按 A,B,C... 顺序的输入网名列表(已带 _to_logic 与位宽)。"""
    m = {"K": out, "L": expr, "M": suffix, "N": 0, "O": note or out, "P": owner, "R": r - 2}
    for i, name in enumerate(inputs):
        m[chr(ord("A") + i)] = name
    _set(ws, r, m)
    return r + 1


# ───────────── mux：A=input B..E=ctrl F=case G=out H=suffix I=top_out(全0) L=owner N=组号 ─────────────
def _mux(ws, r, mux_input, ctrls, case, out, group_no, dest="to_dft", owner="Jiao Yexiang"):
    if isinstance(ctrls, str):
        ctrls = [ctrls]
    m = {"A": mux_input, "F": case, "G": out, "H": dest, "I": 0, "L": owner, "N": group_no}
    for i, c in enumerate(ctrls):
        m[chr(ord("B") + i)] = c
    _set(ws, r, m)
    return r + 1


# ───────────── dft：A=input(_to_dft) D=out put signal E=logic expression(多为 A 恒等) F=observe ─────────────
def _dft(ws, r, out, in_net, expr="A", observe="16", owner="Yao Wang"):
    _set(ws, r, {"A": in_net, "D": out, "E": expr, "F": observe, "H": owner, "I": in_net})
    return r + 1


# ───────────── level_shift：A=input(_to_ls) B=level C=output_name E=top_out=1 F=owner ─────────────
def _ls(ws, r, in_net, out_name, owner="Yao Wang"):
    _set(ws, r, {"A": in_net, "B": "STD_SR_L2H", "C": out_name, "E": 1, "F": owner, "G": in_net})
    return r + 1


def build(path):
    wb = openpyxl.Workbook()

    # ========================= logic 页 =========================
    ws = wb.active
    ws.title = "logic"
    _set(ws, 1, {"A": "A", "K": "<<输出>>", "L": "<<表达式>>"})
    _set(ws, 2, {"A": "A", "B": "B", "C": "C", "D": "D", "E": "E",
                 "K": "logic_out_signal_name", "L": "logic expression", "M": "suffix",
                 "N": "top_output", "O": "Notes", "P": "Owner", "Q": "export_from_regmap", "R": "no"})
    r = 3
    # ① 选路 + iddq 门：d_logic_bt_lp_rx_en = (A?C:B)&(~D)  (for_test 金标准；SignalPath logic:109)
    r = _logic(ws, r, "d_logic_bt_lp_rx_en", "(A?C:B)&(~D)",
               ["d_bt_lp_linelocal_mode_ctrl_to_logic", "d_bt_lp_linectrl_rx_en_to_logic",
                "d_bt_lp_rx_en_local_to_logic", "d_bt_lp_pll_dig_dft_iddq_mode_to_logic"],
               suffix="to_dft", owner="Wan Xu", note="RX en 选路&iddq门")
    # ②' tsensor 选路：既是 Topout 输出、又当 lna_itrim mux 的控制(suffix 含 to_mux)
    r = _logic(ws, r, "d_logic_bt_lp_tsensor[3:0]", "A?C:B",
               ["d_bt_lp_linelocal_tsensor_ctrl_to_logic", "d_bt_lp_linectrl_tsensor_to_logic[3:0]",
                "d_bt_lp_tsensor_local_to_logic[3:0]"],
               suffix="to_mux,to_dft", owner="Jiao Yexiang", note="温度码选路→喂 lna_itrim mux 当控制")
    # ④ d_en_refbuf = D?(B?A:(A&C)):E  → 经 level_shift 出 d_en_refbuf_ls
    r = _logic(ws, r, "d_en_refbuf", "D?(B?A:(A&C)):E",
               ["d_en_refbuf_to_logic", "testmode_to_logic", "en_pll_to_logic",
                "d_pll_line_ctrl_mode_to_logic", "d_bt_lp_pll_en_to_logic"],
               suffix="to_dft", owner="Yao Wang", note="refbuf 使能(经 ls 出顶层 _ls)")

    # ========================= mux 页 =========================
    mx = wb.create_sheet("mux")
    _set(mx, 2, {"A": "mux_input", "B": "mux_ctrl_sig1", "C": "mux_ctrl_sig2", "F": "case",
                 "G": "mux_out", "H": "suffix", "I": "top_out", "J": "Notes",
                 "K": "输入是否Input端口", "L": "Owner", "M": "export_from_regmap", "N": "组号"})
    r = 3
    # ② d_bt_lp_lna_itrim[3:0] = case(d_logic_bt_lp_tsensor[3:0]) → t1..t8，控制本身是 logic 输出(_to_mux)
    cases = ["4'b000x", "4'b001x", "4'b010x", "4'b011x", "4'b100x", "4'b101x", "4'b110x", "4'b111x"]
    for k, case in enumerate(cases, start=1):
        r = _mux(mx, r, "d_bt_lp_lna_itrim_t%d_to_mux[3:0]" % k,
                 "d_logic_bt_lp_tsensor_to_mux[3:0]", case,
                 "d_bt_lp_lna_itrim[3:0]", 6, dest="to_dft", owner="Jiao Yexiang")

    # ========================= dft 页(③ 恒等观测 + 选路输出门控观测)=========================
    dft = wb.create_sheet("dft")
    _set(dft, 2, {"A": "A", "B": "B", "C": "C", "D": "out put signal", "E": "logic expression",
                  "F": "observe", "G": "suffix", "H": "owner", "I": "Exported Signals",
                  "J": "Owner of exported Signals"})
    r = 3
    r = _dft(dft, r, "clk_force_on", "clk_force_on_to_dft")          # ③ 直连寄存器→恒等观测
    r = _dft(dft, r, "en_dig_clk", "en_dig_clk_to_dft")
    r = _dft(dft, r, "d_logic_bt_lp_rx_en", "d_logic_bt_lp_rx_en_to_dft")
    r = _dft(dft, r, "d_bt_lp_lna_itrim[3:0]", "d_bt_lp_lna_itrim_to_dft[3:0]")

    # ========================= regmap 页(RW 主输入 + 线控/RO)=========================
    rm = wb.create_sheet("regmap")
    _set(rm, 2, {"A": "BLOCK", "D": "Reg_Name", "F": "Reg Type", "G": "Signal_Name", "H": "Address",
                 "I": "default", "J": "15", "Y": "0", "Z": "suffix", "AA": "suffix2",
                 "AB": "top_output", "AE": "Owner"})
    r = 3
    # TOP_EN @ d1
    r = _regmap(rm, r, "TOP_EN", "RW", "en_pll", 0, 0, ADDR_TOP_EN, "to_logic,to_dft", "to_pll_ctrl")
    r = _regmap(rm, r, "TOP_EN", "RW", "en_dig_clk", 8, 8, ADDR_TOP_EN, "to_logic", "to_logic")
    r = _regmap(rm, r, "TOP_EN", "RW", "clk_force_on", 9, 9, ADDR_TOP_EN, "to_dft", "to_pll_crg")
    # TESTMODE1 @ d9
    r = _regmap(rm, r, "TESTMODE1", "RW", "testmode", 15, 15, ADDR_TESTMODE, "to_logic", "to_logic")
    r = _regmap(rm, r, "TESTMODE1", "RW", "d_en_refbuf", 14, 14, ADDR_TESTMODE, "to_logic", "to_logic")
    # PLL line ctrl mode @ d23
    r = _regmap(rm, r, "REG_0x17", "RW", "d_pll_line_ctrl_mode", 14, 14, ADDR_PLL_LINE, "to_logic", "to_logic")
    # readro_reg_41 @ d41 (虚拟/线控 RO)
    r = _regmap(rm, r, "readro_reg_41", "RO", "d_bt_lp_bt_mode_sel", 0, 0, ADDR_RO41, "to_logic", "", "Yao Wang")
    r = _regmap(rm, r, "readro_reg_41", "RO", "d_bt_lp_linectrl_rx_en", 1, 1, ADDR_RO41, "to_logic", "", "Yao Wang")
    r = _regmap(rm, r, "readro_reg_41", "RO", "d_bt_lp_pll_dig_dft_iddq_mode", 2, 2, ADDR_RO41, "to_logic", "")
    r = _regmap(rm, r, "readro_reg_41", "RO", "d_bt_lp_pll_en", 3, 3, ADDR_RO41, "to_logic", "")
    # RX local / mode ctrl @ d45 (RW)
    r = _regmap(rm, r, "RX_LOCAL", "RW", "d_bt_lp_linelocal_mode_ctrl", 0, 0, ADDR_RX_LOCAL, "to_logic", "", "Wan Xu")
    r = _regmap(rm, r, "RX_LOCAL", "RW", "d_bt_lp_linelocal_tsensor_ctrl", 2, 2, ADDR_RX_LOCAL, "to_logic", "", "Wan Xu")
    r = _regmap(rm, r, "RX_LOCAL", "RW", "d_bt_lp_rx_en_local", 4, 4, ADDR_RX_LOCAL, "to_logic", "", "Wan Xu")
    # tsensor 线控(RO) + local(RW)
    r = _regmap(rm, r, "readro_reg_42", "RO", "d_bt_lp_linectrl_tsensor[3:0]", 3, 0, ADDR_TSENS_RO, "to_logic", "")
    r = _regmap(rm, r, "TSENSOR_LOCAL", "RW", "d_bt_lp_tsensor_local[3:0]", 3, 0, ADDR_TSENS_LOCAL, "to_logic", "", "Jiao Yexiang")
    # lna_itrim 8 个 trim 数据源(RW, _to_mux)
    for k in range(1, 5):
        r = _regmap(rm, r, "ITRIM_A", "RW", "d_bt_lp_lna_itrim_t%d[3:0]" % k,
                    4 * (k - 1) + 3, 4 * (k - 1), ADDR_ITRIM_A, "to_mux", "", "Jiao Yexiang")
    for k in range(5, 9):
        r = _regmap(rm, r, "ITRIM_B", "RW", "d_bt_lp_lna_itrim_t%d[3:0]" % k,
                    4 * (k - 5) + 3, 4 * (k - 5), ADDR_ITRIM_B, "to_mux", "", "Jiao Yexiang")
    # READro_reg_38 @ d56：pll_lock_indicator(RO 回读，⑤ 无 cone)
    r = _regmap(rm, r, "READro_reg_38", "RO", "pll_lock_indicator", 15, 15, ADDR_RO38, "to_dft", "", "Yao Wang")

    # ========================= total_memory_map 页 =========================
    tmm = wb.create_sheet("total_memory_map")
    r = 1
    r = _tmm_reg(tmm, r, "TOP_EN", ADDR_TOP_EN, "h0200")
    r = _tmm_field(tmm, r, "en_dig_clk", "8")
    r = _tmm_field(tmm, r, "clk_force_on", "9")
    r = _tmm_field(tmm, r, "en_pll", "0")
    r = _tmm_reg(tmm, r, "TESTMODE1", ADDR_TESTMODE, "h4000")
    r = _tmm_field(tmm, r, "testmode", "15")
    r = _tmm_field(tmm, r, "d_en_refbuf", "14")
    r = _tmm_reg(tmm, r, "REG_0x17", ADDR_PLL_LINE)
    r = _tmm_field(tmm, r, "d_pll_line_ctrl_mode", "14")
    r = _tmm_reg(tmm, r, "readro_reg_41", ADDR_RO41)
    r = _tmm_field(tmm, r, "d_bt_lp_bt_mode_sel", "0", "Y", "线控/回读")
    r = _tmm_field(tmm, r, "d_bt_lp_linectrl_rx_en", "1", "Y", "线控/回读")
    r = _tmm_field(tmm, r, "d_bt_lp_pll_dig_dft_iddq_mode", "2", "Y", "iddq 门")
    r = _tmm_field(tmm, r, "d_bt_lp_pll_en", "3", "Y")
    r = _tmm_reg(tmm, r, "RX_LOCAL", ADDR_RX_LOCAL)
    r = _tmm_field(tmm, r, "d_bt_lp_linelocal_mode_ctrl", "0")
    r = _tmm_field(tmm, r, "d_bt_lp_linelocal_tsensor_ctrl", "2")
    r = _tmm_field(tmm, r, "d_bt_lp_rx_en_local", "4")
    r = _tmm_reg(tmm, r, "readro_reg_42", ADDR_TSENS_RO)
    r = _tmm_field(tmm, r, "d_bt_lp_linectrl_tsensor[3:0]", "3:0", "Y", "线控/回读")
    r = _tmm_reg(tmm, r, "TSENSOR_LOCAL", ADDR_TSENS_LOCAL)
    r = _tmm_field(tmm, r, "d_bt_lp_tsensor_local[3:0]", "3:0")
    r = _tmm_reg(tmm, r, "ITRIM_A", ADDR_ITRIM_A)
    for k in range(1, 5):
        r = _tmm_field(tmm, r, "d_bt_lp_lna_itrim_t%d[3:0]" % k, "%d:%d" % (4 * (k - 1) + 3, 4 * (k - 1)))
    r = _tmm_reg(tmm, r, "ITRIM_B", ADDR_ITRIM_B)
    for k in range(5, 9):
        r = _tmm_field(tmm, r, "d_bt_lp_lna_itrim_t%d[3:0]" % k, "%d:%d" % (4 * (k - 5) + 3, 4 * (k - 5)))
    r = _tmm_reg(tmm, r, "READro_reg_38", ADDR_RO38)
    r = _tmm_field(tmm, r, "pll_lock_indicator", "15", "N", "RO 回读(FSM/模拟状态,无 cone)")

    # ========================= level_shift 页(④ X_to_ls → X_ls)=========================
    lvl = wb.create_sheet("level_shift")
    _set(lvl, 2, {"A": "level_shift_input", "B": "level", "C": "level_shift_output_name",
                  "D": "suffix", "E": "top_out", "F": "Owner", "G": "Exported Signals",
                  "H": "Owner of exported Signals"})
    r = 3
    r = _ls(lvl, r, "d_en_refbuf_to_ls", "d_en_refbuf_ls")
    r = _ls(lvl, r, "d_en_pfd_to_ls", "d_en_pfd_ls")

    # ========================= for_test 页(① 的金标准真值表)=========================
    ft = wb.create_sheet("for_test")
    _fill_fortest(ft)
    # for_test_gen_tc：同结构(生成版)，本夹具放同一个 rx_en case 作占位
    ftg = wb.create_sheet("for_test_gen_tc")
    _fill_fortest(ftg)

    # ========================= Topout 页(要验清单 B 列 + 独立寄存器图 C–G)=========================
    top = wb.create_sheet("Topout")
    _set(top, 2, {"A": "Owner", "B": "TOP Out Signal", "C": "Reg Name", "D": "Offset",
                  "E": "Reg Description", "F": "Address", "G": "ResetValue"})
    # B 列(字母序，无后缀真名/带 _ls)= 要验的信号；C–G(独立)=寄存器图(故意不对齐 B)
    sigs = ["clk_force_on", "d_bt_lp_lna_itrim[3:0]", "d_en_refbuf_ls",
            "d_logic_bt_lp_rx_en", "d_logic_bt_lp_tsensor[3:0]", "en_dig_clk", "pll_lock_indicator"]
    regs = [("TOPrw_reg_1", "10'h1", "[8] en_dig_clk\n[9] clk_force_on\n[0] en_pll", "16'h0200"),
            ("TESTrw_reg_9", "10'h9", "[15] testmode\n[14] d_en_refbuf", "16'h4000"),
            ("READro_reg_38", "ld_dig_done", "[15] pll_lock_indicator ...", "16'd1")]
    r = 3
    for i, s in enumerate(sigs):
        m = {"A": "Yao Wang", "B": s}
        if i < len(regs):
            rn, off, desc, rst = regs[i]
            m.update({"C": rn, "D": off, "E": desc, "G": rst})
        _set(top, r, m)
        r += 1

    # ========================= SignalPath 页(VBA 一次性查询；开发期对照 oracle)=========================
    sp = wb.create_sheet("SignalPath")
    # d_logic_bt_lp_rx_en 的全解析块(忠实真表不规整布局的简化版)
    _set(sp, 4, {"B": "Reg:d41", "C": "d_bt_lp_pll_dig_dft_iddq_mode", "D": "A",
                 "E": "d_logic_bt_lp_rx_en_to_logic", "F": "logic:1", "G": "(A?C:B)&(~D)"})
    _set(sp, 5, {"F": "A", "G": "d_bt_lp_linelocal_mode_ctrl_to_logic", "H": "Reg:d45",
                 "I": "d_bt_lp_linelocal_mode_ctrl", "J": "10'h2D", "K": "[0:0]", "L": "d0"})
    _set(sp, 6, {"F": "B", "G": "d_bt_lp_linectrl_rx_en_to_logic", "H": "Reg:d41",
                 "I": "d_bt_lp_linectrl_rx_en", "J": "d_bt_lp_linectrl_rx_en", "K": "[1:1]", "L": "d0"})
    _set(sp, 7, {"F": "C", "G": "d_bt_lp_rx_en_local_to_logic", "H": "Reg:d45",
                 "I": "d_bt_lp_rx_en_local", "J": "10'h2D", "K": "[4:4]", "L": "d0"})
    _set(sp, 8, {"F": "D", "G": "d_bt_lp_pll_dig_dft_iddq_mode_to_logic", "H": "Reg:d41",
                 "I": "d_bt_lp_pll_dig_dft_iddq_mode", "K": "[2:2]", "L": "d0"})

    # ========================= main 页(top_module_name + 版本史金句)=========================
    mn = wb.create_sheet("main")
    _set(mn, 7, {"A": "项目名称", "D": "Hi1108 Pilot", "F": "项目版本", "G": "1.0"})
    _set(mn, 10, {"A": "top_module_name", "D": "BT_LP_DREG"})
    _set(mn, 23, {"A": "更新描述", "G": "更新日期", "H": "修改人员"})
    _set(mn, 34, {"A": "1）Top out 输出不带后缀，suffix 支持两个，topout 默认的把后缀去掉。",
                  "G": "2022-10-20", "H": "张立国"})
    _set(mn, 37, {"A": "Topout 更新，增加了地址列的两个标志模式 TopVar / VaVar",
                  "G": "2022-11-26", "H": "张立国"})

    # ========================= NamingRule 页(命名约定，精简)=========================
    nr = wb.create_sheet("NamingRule")
    _set(nr, 5, {"A": "A_Prefix", "B": "说明"})
    _set(nr, 6, {"A": "d", "B": "dig2ana/不区分方向"})
    _set(nr, 7, {"A": "rb", "B": "ana2dig/readback"})
    _set(nr, 19, {"A": "Signal_Type"})
    _set(nr, 21, {"A": "rw", "B": "一般读写寄存器(默认)"})
    _set(nr, 22, {"A": "ro", "B": "只读"})
    _set(nr, 23, {"A": "linectrl", "B": "线控"})
    _set(nr, 24, {"A": "local", "B": "本地控制"})
    _set(nr, 25, {"A": "mode", "B": "线控/寄存器控选择"})
    _set(nr, 35, {"A": "Suffix", "B": "说明"})
    _set(nr, 36, {"A": "to_logic"})
    _set(nr, 37, {"A": "to_mux"})
    _set(nr, 38, {"A": "to_mux,to_logic"})

    # ========================= iddq / ISO 页(本表空，只 schema)=========================
    iddq = wb.create_sheet("iddq")
    _set(iddq, 2, {"A": "A", "B": "B", "C": "C", "D": "out put signal", "E": "logic expression",
                   "F": "abserve", "G": "suffix", "H": "owner", "I": "Exported Signals",
                   "J": "Owner of exported Signals"})
    iso = wb.create_sheet("ISO")
    _set(iso, 2, {"A": "iso_input", "B": "iso_en", "C": "output_name", "D": "path of input/ iso /output",
                  "E": "power", "F": "Cell_name", "G": "Owner", "H": "Exported Signals",
                  "I": "Owner of exported Signals", "L": "ISO_top_in", "O": "RO"})

    wb.save(path)
    return path


def _fill_fortest(ft):
    """① d_logic_bt_lp_rx_en 的金标准真值表块(忠实真表竖向布局，T0..T4=Line1/Line0/Local1/Local0/iddq)。

    expr=(A?C:B)&(~D)：A=linelocal_mode_ctrl B=linectrl_rx_en C=rx_en_local D=iddq_mode
      T0 A0 B1 C0 D0 →1  T1 A0 B0 C1 D0 →0  T2 A1 B0 C1 D0 →1  T3 A1 B1 C0 D0 →0  T4 A0 B1 C0 D1 →0
    """
    _set(ft, 2, {"A": "case", "B": "输入寄存器地址", "C": "value", "D": "待验证输出信号", "E": "预计输出",
                 "F": "验证信号的输入信号", "G": "Signal Value(bin)", "H": "Reg(dec)", "I": "Addr",
                 "J": "RO", "K": "Bits", "L": "Bit Addr", "M": "Bit ADDR", "N": "1",
                 "O": "T0", "P": "T1", "Q": "T2", "R": "T3", "S": "T4"})
    # 输入行
    _set(ft, 4, {"A": "d_logic_bt_lp_rx_en", "B": "d_bt_lp_linectrl_rx_en", "C": "16'h1", "E": "1'b0",
                 "F": "d_bt_lp_linectrl_rx_en", "G": "1", "H": "1", "I": "d_bt_lp_linectrl_rx_en",
                 "J": "True", "K": "1", "N": "3", "O": 1, "P": 0, "Q": 0, "R": 1, "S": 1})
    _set(ft, 5, {"B": "d_bt_lp_pll_dig_dft_iddq_mode", "C": "16'h1", "F": "d_bt_lp_pll_dig_dft_iddq_mode",
                 "G": "1", "H": "1", "I": "d_bt_lp_pll_dig_dft_iddq_mode", "J": "True", "K": "2",
                 "N": "3", "O": 0, "P": 0, "Q": 0, "R": 0, "S": 1})
    _set(ft, 6, {"F": "d_bt_lp_linelocal_mode_ctrl", "G": "0", "H": "0", "I": "10'h2D", "J": "False",
                 "K": "0", "N": "3", "O": 0, "P": 0, "Q": 1, "R": 1, "S": 0})
    _set(ft, 7, {"B": "10'h2D", "C": "16'h0", "F": "d_bt_lp_rx_en_local", "G": "0", "H": "0",
                 "I": "10'h2D", "J": "False", "K": "4", "N": "3", "O": 0, "P": 1, "Q": 1, "R": 0, "S": 0})
    # 输出行 + 标签行
    _set(ft, 8, {"D": "d_logic_bt_lp_rx_en", "E": "1'b0", "G": "1'b1", "N": "3",
                 "O": "1'b1", "P": "1'b0", "Q": "1'b1", "R": "1'b0", "S": "1'b0"})
    _set(ft, 9, {"G": "Line1", "N": "3", "O": "Line1", "P": "Line0", "Q": "Local1", "R": "Local0", "S": "iddq"})


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else "mirror_btlp_dreg.xlsx"
    build(out)
    print("已生成 BT_LP / Topout-rooted 镜像 Excel:", out)
