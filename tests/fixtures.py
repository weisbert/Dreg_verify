# -*- coding: utf-8 -*-
"""构造一个镜像真实 Dreg Excel 结构的合成工作簿，用于端到端测试（拿不到真表时的 oracle）。

含已对照过 .sv 的 reserve(R=106) 与 lna_agc(R=108)，以及一个 ls 直通信号。
列布局严格按 excel_model 的约定：logic A..J=输入(列字母=变量字母), K=输出, L=表达式,
M=type, N=top_output, O=notes, P=owner, R=序号。表头在第 2 行。
"""

import openpyxl


def _set_row(ws, rownum, mapping):
    """mapping: {列字母: 值}。"""
    from openpyxl.utils import column_index_from_string
    for col, val in mapping.items():
        ws.cell(row=rownum, column=column_index_from_string(col), value=val)


def build_workbook(path, with_pll_chain=False):
    """with_pll_chain=True 额外加入 pll_n←pll_n2←pll_n1 三层 cone 链
    （镜像 2026-06-02 真表 Hi1108 的 pll_n 结构，用于 cone 展开测试）。"""
    wb = openpyxl.Workbook()

    # ───────── logic 页 ─────────
    ws = wb.active
    ws.title = "logic"
    # row1 = 合并标题(随意)，row2 = 真表头
    _set_row(ws, 1, {"A": "inputs", "K": "logic"})
    _set_row(ws, 2, {
        "A": "in_A", "B": "in_B", "C": "in_C", "J": "in_J",
        "K": "logic_out_signal_name", "L": "logic expression", "M": "suffix",
        "N": "top_output", "O": "Notes", "P": "owner", "R": "no",
    })
    # reserve: (A?C:B)&(~J)  A=linelocal_mode_ctrl B=bt_mode_sel C=bt_mode_sel_local J=iddq
    _set_row(ws, 3, {
        "A": "d_bt_lp_linelocal_mode_ctrl_to_logic",
        "B": "d_bt_lp_bt_mode_sel_to_logic",
        "C": "d_bt_lp_bt_mode_sel_local_to_logic",
        "J": "d_bt_lp_iddq_to_logic",
        "K": "d_logic_bt_lp_reserve",
        "L": "(A?C:B)&(~J)",
        "M": "to_logic", "N": 0, "O": "to_pll_ctrl", "P": "Alice", "R": 106,
    })
    # lna_agc: A?C:B  [2:0]  A=lna_line_sel B=lna_agc_line(RO,3) C=lna_agc_local(RW,3)
    _set_row(ws, 4, {
        "A": "d_bt_lp_lna_line_sel_to_logic",
        "B": "d_bt_lp_lna_agc_line_to_logic[2:0]",
        "C": "d_bt_lp_lna_agc_local_to_logic[2:0]",
        "K": "d_logic_bt_lp_lna_agc[2:0]",
        "L": "A?C:B",
        "M": "to_mux", "N": 1, "O": "to_datapath", "P": "Bob", "R": 108,
    })
    # ls 直通: A  输出名无 d_logic_ 前缀 (en_refbuf)
    _set_row(ws, 5, {
        "A": "d_bt_lp_en_refbuf_cfg_to_logic",
        "K": "d_en_refbuf",
        "L": "A",
        "M": "ls", "N": 1, "O": "to_top", "P": "Alice", "R": 50,
    })
    # pll_n 三层 cone 链（镜像真表 logic row3/4/5：内部信号 pll_n1/pll_n2 逐层代入）
    if with_pll_chain:
        _set_row(ws, 6, {
            "A": "int_n_to_logic[8:0]",
            "B": "frac_n_msb_to_logic[6:0]",
            "C": "frac_n_lsb_to_logic[15:1]",
            "D": "frac_n_lsb_to_logic[0]",
            "E": "d_xo_freq_sel_to_logic",
            "K": "pll_n1[31:0]",
            "L": "E?{1'b0,A,B,C}:{A,B,C,D}",
            "M": "to_logic", "N": 0, "O": "internal", "P": "Yao Wang", "R": 1,
        })
        _set_row(ws, 7, {
            "A": "pll_n1_to_logic[31]",
            "B": "pll_n1_to_logic[30:23]",
            "C": "pll_n1_to_logic[22:16]",
            "D": "pll_n1_to_logic[15:0]",
            "E": "en_dig_clk_div2_to_logic",
            "K": "pll_n2[31:0]",
            "L": "E?{B,C,D,1'b0}:{A,B,C,D}",
            "M": "to_logic", "N": 0, "O": "internal", "P": "Yao Wang", "R": 2,
        })
        _set_row(ws, 8, {
            "A": "pll_n2_to_logic[31]",
            "B": "pll_n2_to_logic[30:23]",
            "C": "pll_n2_to_logic[22:16]",
            "D": "pll_n2_to_logic[15:0]",
            "E": "en_dig_clk_div4_to_logic",
            "K": "pll_n[31:0]",
            "L": "E?{B,C,D,1'b0}:{A,B,C,D}",
            "N": 1, "O": "to_pll_ctrl", "P": "Yao Wang", "R": 3,
        })
        # d_pfd_en_lnmode（镜像真表 row21）：输入 C=mon_active 是 PLL 控制器 FSM 输出，
        # 不在 RF 表里(wire 兜底)，且实际位于 U_BT_LP_PLL_DIG 子模块内部 → 需要探针前缀
        _set_row(ws, 9, {
            "A": "d_pfd_en_lnmode_to_logic[1]",
            "B": "d_pfd_en_lnmode_to_logic[0]",
            "C": "mon_active_to_logic",
            "F": "d_bt_lp_pll_dig_dft_iddq_mode_to_logic",
            "K": "d_pfd_en_lnmode",
            "L": "(A?B:C)&(~F)",
            "M": "ls", "N": 1, "O": "lnmode", "P": "Yao Wang", "R": 19,
        })

    # ───────── regmap 页 ─────────
    rm = wb.create_sheet("regmap")
    _set_row(rm, 2, {
        "D": "Reg_Name", "F": "Reg Type", "G": "Signal_Name", "I": "default",
        "J": "b15", "Y": "b0", "Z": "suffix", "AE": "owner",
    })
    # 仅作类型/兜底来源，bit 位置以 tmm 为准；这里给几条
    _regmap_row(rm, 3, "BT_MODE", "RW", "d_bt_lp_bt_mode_sel", bit0=True)
    _regmap_row(rm, 4, "LINELOCAL", "RW", "d_bt_lp_linelocal_mode_ctrl", bit0=True)
    _regmap_row(rm, 5, "LINELOCAL", "RW", "d_bt_lp_bt_mode_sel_local", bit1=True)
    _regmap_row(rm, 6, "IDDQ_CTRL", "RO", "d_bt_lp_iddq", bit0=True)

    # ───────── total_memory_map 页 ─────────
    tmm = wb.create_sheet("total_memory_map")
    r = 1
    # 列：A=name B=bit/addr C=reset D=DIGPIN/type E=func F=field_addr G=- H=type
    r = _tmm_reg(tmm, r, "BT_MODE", "h2C")
    r = _tmm_field(tmm, r, "d_bt_lp_bt_mode_sel", "0", addr="h2C", dig="N", typ="RW")
    r = _tmm_reg(tmm, r, "LINELOCAL_CTRL", "h2D")
    r = _tmm_field(tmm, r, "d_bt_lp_linelocal_mode_ctrl", "0", addr="h2D", dig="N", typ="RW")
    r = _tmm_field(tmm, r, "d_bt_lp_bt_mode_sel_local", "1", addr="h2D", dig="N", typ="RW")
    r = _tmm_reg(tmm, r, "IDDQ_CTRL", "h30")
    r = _tmm_field(tmm, r, "d_bt_lp_iddq", "0", addr="h30", dig="Y", typ="RO")
    r = _tmm_reg(tmm, r, "LNA_SEL", "h31")
    r = _tmm_field(tmm, r, "d_bt_lp_lna_line_sel", "0", addr="h31", dig="N", typ="RW")
    r = _tmm_reg(tmm, r, "LNA_AGC", "h32")
    r = _tmm_field(tmm, r, "d_bt_lp_lna_agc_local", "2:0", addr="h32", dig="N", typ="RW")
    r = _tmm_reg(tmm, r, "LNA_AGC_LINE", "h33")
    r = _tmm_field(tmm, r, "d_bt_lp_lna_agc_line", "2:0", addr="h33", dig="Y", typ="RO")
    r = _tmm_reg(tmm, r, "REFBUF", "h10")
    r = _tmm_field(tmm, r, "d_bt_lp_en_refbuf_cfg", "0", addr="h10", dig="N", typ="RW")

    # pll_n cone 链的叶子寄存器（地址/位段 1:1 取自真表 total_memory_map）
    if with_pll_chain:
        r = _tmm_reg(tmm, r, "TOP_EN", "h1")
        r = _tmm_field(tmm, r, "en_dig_clk_div2", "7", addr="h1", dig="N", typ="RW")
        r = _tmm_field(tmm, r, "en_dig_clk_div4", "6", addr="h1", dig="N", typ="RW")
        r = _tmm_reg(tmm, r, "INT_N", "h2")
        r = _tmm_field(tmm, r, "int_n[8:0]", "15:7", addr="h2", dig="N", typ="RW")
        r = _tmm_field(tmm, r, "frac_n_msb[6:0]", "6:0", addr="h2", dig="N", typ="RW")
        r = _tmm_reg(tmm, r, "INT_N", "h3")
        r = _tmm_field(tmm, r, "frac_n_lsb[15:0]", "15:0", addr="h3", dig="N", typ="RW")
        r = _tmm_reg(tmm, r, "REFBUF2", "hC")
        r = _tmm_field(tmm, r, "d_xo_freq_sel", "0", addr="hC", dig="N", typ="RW")
        # d_pfd_en_lnmode 用：寄存器位自身 RW + iddq RO；mon_active 故意不入表(wire 兜底)
        r = _tmm_reg(tmm, r, "ANA_CTRL", "hD")
        r = _tmm_field(tmm, r, "d_pfd_en_lnmode[1:0]", "15:14", addr="hD", dig="N", typ="RW")
        r = _tmm_reg(tmm, r, "IDDQ_REG", "h29")
        r = _tmm_field(tmm, r, "d_bt_lp_pll_dig_dft_iddq_mode", "2", addr="h29", dig="Y", typ="RO")

    wb.save(path)
    return path


def _regmap_row(ws, row, reg, typ, signal, bit0=False, bit1=False):
    from openpyxl.utils import column_index_from_string, get_column_letter
    _set_row(ws, row, {"D": reg, "F": typ, "G": signal})
    # J..Y = bit15..bit0；bit0 在 Y 列, bit1 在 X 列
    if bit0:
        ws.cell(row=row, column=column_index_from_string("Y"), value=1)
    if bit1:
        ws.cell(row=row, column=column_index_from_string("X"), value=1)


def _tmm_reg(ws, r, name, addr):
    _set_row(ws, r, {"A": name, "B": addr, "C": "0", "D": "RW", "E": "reg def"})
    return r + 1


def _tmm_field(ws, r, name, bit, addr, dig, typ):
    _set_row(ws, r, {"A": name, "B": bit, "C": "0", "D": dig, "E": "field",
                     "F": addr, "H": typ})
    return r + 1


if __name__ == "__main__":
    import sys
    build_workbook(sys.argv[1] if len(sys.argv) > 1 else "synthetic_dreg.xlsx")
    print("done")
