# -*- coding: utf-8 -*-
"""make_mirror_excel.py — 镜像真实 Hi1108 WL_RFTRX 表的开发夹具 Excel。

目标（第二十三轮重建，2026-06-08）：用【真实信号名 + 真实地址 + 真实 mux/regmap/dft 结构】
复现公司机真表 `excel/Hi1108V100_WL_RFTRX_C0C1_DREG_to_dig_95P.xlsx` 上用户报的三个问题，
不依赖公司机即可在本地跑生成器复现。

数据来源（第二十三轮 inspect_excel --signal / --account 真抽）：
  · temp_code 上游 mux56(行113/114)：mode? local(RW) : tsensor(RO)，输出 d_wl_rf_temp_code，后缀 to_mux。
  · mixer2g_trim mux157(行1085-1097)：控制 = temp_code[3:1] 切片，8 选 1，**有重复行**(t0-t4 出现两遍)，后缀 to_dft。
  · mixer5g_trim mux158(行1098-1105)：控制 = temp_code[3:1]，干净 8 选 1，后缀 to_dft。
  · slna_1st_bias_trim_gain_cal mux152(行855-872)：18 行挤一个输出名 —— 前 9 行选 1st_bias 源、
    后 9 行选 2nd_bias 源，**case 完全相同**(gtune[3:0])→ 同 case 不同源【冲突】(designer 复制漏改输出名)。
  · lp5g_rxrf_lna_lctune mux156(行893-1084)：3 控制 8-bit case {rx5g_en, band_5g_sel[3:0], c_code[2:0]}，
    rx5g_off 高 band 段(b8+)的 case 值【错写成】跟 rx5g_on 低 band 段(b0+)一样 → 同 case 不同源【冲突】。
  · dft 页(行188/192/193/194)：上面 4 个输出都被 d_wl_rf_trx_reg_dft_iddq_mode 门控，表达式 B?0:A。
  真实地址：temp_code@d60(bit0=mode,7:4=local)；mixer2g_trim@d388；mixer5g_trim@d389/d390。

它严格按 excel_model 的列约定造 5 页(logic/regmap/total_memory_map/mux/dft)。
for_test 页【留空】——真表这份文件里 for_test 也是空的(designer 真值表在另一个 .xlsx)。

用法:  python make_mirror_excel.py [输出路径=mirror_wl_dreg.xlsx]
"""

import sys

import openpyxl
from openpyxl.utils import column_index_from_string


def _set(ws, row, mapping):
    for col, val in mapping.items():
        ws.cell(row=row, column=column_index_from_string(col), value=val)


# ───────────── 地址（十进制；regmap 用 d<n>，tmm 用 h<X> 十六进制，两边一致）─────────────
ADDR_TEMP = 60           # temp_code: bit0=mode, 7:4=local
ADDR_TSENSOR = 160       # linectrl_tsensor RO（mode=0 line 路）
ADDR_M2G = 388           # mixer2g_trim t0..t7（2bit 打包进一个寄存器）
ADDR_M5G_A = 389         # mixer5g_trim t0..t3（3bit，4位对齐）
ADDR_M5G_B = 390         # mixer5g_trim t4..t7
ADDR_IDDQ = 500          # dft iddq_mode RO（门控）


def _d(n):
    return "d%d" % n


def _h(n):
    return "h%X" % n


# ───────────── regmap：D=Reg_Name F=Reg Type G=Signal_Name H=地址 J..Y=bit15..bit0(Y=bit0) AE=owner ─────────────
def _regmap(ws, r, reg, typ, signal, bit_msb, bit_lsb, addr_dec, owner="WL"):
    _set(ws, r, {"D": reg, "F": typ, "G": signal, "H": _d(addr_dec), "AE": owner})
    for b in range(bit_lsb, bit_msb + 1):
        col = column_index_from_string("Y") - b      # Y=bit0
        ws.cell(row=r, column=col, value=1)
    return r + 1


# ───────────── total_memory_map：A=名 B=bit F=地址 D=DIG_TOP_PIN H=类型 ─────────────
def _tmm_reg(ws, r, name, addr_dec):
    _set(ws, r, {"A": name, "B": _h(addr_dec), "C": "0", "D": "RW", "E": "register def"})
    return r + 1


def _tmm_field(ws, r, name, bit, addr_dec, dig, typ, desc=""):
    _set(ws, r, {"A": name, "B": bit, "C": "0", "D": dig, "E": desc or "field",
                 "F": _h(addr_dec), "H": typ})
    return r + 1


# ───────────── mux：A=input B~E=ctrl F=case G=out H=去向 I=top L=owner N=组号 ─────────────
def _mrow(ws, r, mux_input, ctrls, case, out, group_no, dest="to_mux", owner="WL", top=0):
    """ctrls: 单个控制名(str) 或 控制名列表(写入 B/C/D/E)。"""
    if isinstance(ctrls, str):
        ctrls = [ctrls]
    m = {"A": mux_input, "F": case, "G": out, "H": dest, "I": top, "L": owner, "N": group_no}
    for i, c in enumerate(ctrls):
        m[chr(ord("B") + i)] = c          # B,C,D,E
    _set(ws, r, m)
    return r + 1


def build(path):
    wb = openpyxl.Workbook()

    # ========================= logic 页（4 个问题信号都是 mux 输出，logic 仅占位表头）=========================
    ws = wb.active
    ws.title = "logic"
    _set(ws, 1, {"A": "<<输入信号(列字母=表达式变量)>>", "K": "<<输出>>", "L": "<<真值表达式>>"})
    _set(ws, 2, {"A": "in_A", "B": "in_B", "K": "logic输出名", "L": "表达式",
                 "M": "type", "N": "top_output", "O": "Notes", "P": "owner", "R": "no"})

    # ========================= mux 页 =========================
    mx = wb.create_sheet("mux")
    _set(mx, 2, {"A": "mux_input", "B": "ctrl1", "C": "ctrl2", "D": "ctrl3", "F": "case",
                 "G": "mux_out", "H": "dest", "I": "top", "L": "owner", "N": "组号"})
    r = 3

    # ── mux56：temp_code[3:0] = case(temp_code_mode){0:线控 tsensor(RO); 1:temp_code_local(RW)}，后缀 to_mux ──
    r = _mrow(mx, r, "d_wl_rf_linectrl_tsensor_to_mux[3:0]", "d_wl_rf_temp_code_mode_to_mux",
              "1'b0", "d_wl_rf_temp_code[3:0]", 56, dest="to_mux", owner="yangteng")
    r = _mrow(mx, r, "d_wl_rf_temp_code_local_to_mux[3:0]", "d_wl_rf_temp_code_mode_to_mux",
              "1'b1", "d_wl_rf_temp_code[3:0]", 56, dest="to_mux", owner="yangteng")

    # ── mux152：slna_1st_bias_trim_gain_cal —— 18 行挤一个输出名（同 case 不同源【冲突】）──
    #    前 9 行选 1st_bias g8..g0，后 9 行选 2nd_bias g8..g0，gtune[3:0] case 完全相同。
    def _gtune_case(k):              # g8→1xxx；g7..g0→0111..0000
        return "4'b1xxx" if k == 8 else "4'b" + format(k, "04b")
    for fam in ("1st", "2nd"):
        for k in (8, 7, 6, 5, 4, 3, 2, 1, 0):
            r = _mrow(mx, r,
                      "d_bt_rx_slna_%s_bias_trim_gain_cal_g%d_wl_to_mux[3:0]" % (fam, k),
                      "d_bt_slna_gtune_wl_to_mux[3:0]", _gtune_case(k),
                      "d_bt_rx_slna_1st_bias_trim_gain_cal_wl[3:0]", 152,
                      dest="to_dft", owner="Konglingshan")

    # ── mux156：lp5g_rxrf_lna_lctune —— 3 控制 8-bit case，rx5g_off 高 band 段 case 错写成 on 低 band 段（冲突）──
    #    case 编码 = {rx5g_en[7], band_5g_sel[6:3], c_code[2:0]}；缩小版：band {0} on + {0} off + {8} off(buggy)。
    lctune_ctrls = ["d_wl_rf_rx5g_en_to_mux",
                    "d_wl_rf_band_5g_sel_to_mux[3:0]",
                    "d_wl_rf_c_code_to_mux[2:0]"]

    def _lctune_row(band, corner, onoff, buggy=False):
        nonlocal r
        rx5g_en = 1 if onoff == "on" else 0
        case_band = (band - 8) if buggy else band         # buggy: off 高 band 复用 on 低 band 的 case 空间
        case_en = 1 if buggy else rx5g_en                 # buggy: bit7 错写成 1（=on）
        val = (case_en << 7) | ((case_band & 0xF) << 3) | (corner & 0x7)
        case = "8'b" + format(val, "08b")
        src = "d_wl_rf_lp5g_rx_lna_lctune_lut_b%d_c%d_rx5g_%s_to_mux[5:0]" % (band, corner, onoff)
        r = _mrow(mx, r, src, lctune_ctrls, case,
                  "d_wl_rf_lp5g_rxrf_lna_lctune[5:0]", 156, dest="to_dft", owner="shenzheng")
    for corner in range(5):                       # b0 rx5g_on（case bit7=1, band=0）
        _lctune_row(0, corner, "on")
    for corner in range(5):                       # b0 rx5g_off（case bit7=0, band=0）—— 正确
        _lctune_row(0, corner, "off")
    for corner in range(5):                       # b8 rx5g_off（case 错写成 bit7=1, band=0）→ 撞 b0_on
        _lctune_row(8, corner, "off", buggy=True)

    # ── mux157：mixer2g_trim[1:0] = case(temp_code[3:1]) 8 选 1，**含重复行**(t0-t4 出现两遍)，后缀 to_dft ──
    def _m2g(k):
        nonlocal r
        r = _mrow(mx, r, "d_wl_rf_lo2g5g_mixer2g_trim_t%d_to_mux[1:0]" % k,
                  "d_wl_rf_temp_code_to_mux[3:1]", "3'b" + format(k, "03b"),
                  "d_wl_rf_lo2g5g_mixer2g_trim[1:0]", 157, dest="to_dft", owner="law/chenhao")
    for k in range(5):    # t0..t4
        _m2g(k)
    for k in range(5):    # t0..t4 再来一遍（真表的重复行 → 死分支去重）
        _m2g(k)
    for k in (5, 6, 7):   # t5..t7
        _m2g(k)

    # ── mux158：mixer5g_trim[2:0] = case(temp_code[3:1]) 干净 8 选 1，后缀 to_dft ──
    for k in range(8):
        r = _mrow(mx, r, "d_wl_rf_lo2g5g_mixer5g_trim_t%d_to_mux[2:0]" % k,
                  "d_wl_rf_temp_code_to_mux[3:1]", "3'b" + format(k, "03b"),
                  "d_wl_rf_lo2g5g_mixer5g_trim[2:0]", 158, dest="to_dft", owner="law/chenhao")

    # ========================= dft 页（4 个输出被 iddq_mode 门控，B?0:A）=========================
    dft = wb.create_sheet("dft")
    _set(dft, 2, {"A": "A", "B": "gate(to_dft)", "C": "C", "D": "observed_out", "E": "gate_expr"})
    r = 3
    for out_sig in ("d_bt_rx_slna_1st_bias_trim_gain_cal_wl[3:0]",
                    "d_wl_rf_lp5g_rxrf_lna_lctune[5:0]",
                    "d_wl_rf_lo2g5g_mixer2g_trim[1:0]",
                    "d_wl_rf_lo2g5g_mixer5g_trim[2:0]"):
        _set(dft, r, {"A": out_sig.split("[")[0] + "_to_dft",
                      "B": "d_wl_rf_trx_reg_dft_iddq_mode_to_dft",
                      "D": out_sig, "E": "B?0:A"})
        r += 1

    # ========================= regmap 页 =========================
    rm = wb.create_sheet("regmap")
    _set(rm, 2, {"D": "Reg_Name", "F": "Reg Type", "G": "Signal_Name",
                 "H": "addr", "I": "default", "J": "b15", "Y": "b0", "AE": "owner"})
    r = 3
    r = _regmap(rm, r, "WL_TEMP_ctrl", "RW", "d_wl_rf_temp_code_mode", 0, 0, ADDR_TEMP, "yangteng")
    r = _regmap(rm, r, "WL_TEMP_ctrl", "RW", "d_wl_rf_temp_code_local[3:0]", 7, 4, ADDR_TEMP, "yangteng")
    r = _regmap(rm, r, "TSENSOR_LINE", "RO", "d_wl_rf_linectrl_tsensor", 3, 0, ADDR_TSENSOR, "yangteng")
    # mixer2g_trim 8 源打包进 d388（bit1:0,3:2,...,15:14）
    for k in range(8):
        r = _regmap(rm, r, "MIXER_CTRL1", "RW", "d_wl_rf_lo2g5g_mixer2g_trim_t%d[1:0]" % k,
                    2 * k + 1, 2 * k, ADDR_M2G, "law/chenhao")
    # mixer5g_trim 8 源 3bit、4位对齐：t0..t3@d389 / t4..t7@d390
    for k in range(4):
        r = _regmap(rm, r, "MIXER_CTRL2", "RW", "d_wl_rf_lo2g5g_mixer5g_trim_t%d[2:0]" % k,
                    4 * k + 2, 4 * k, ADDR_M5G_A, "law/chenhao")
    for k in range(4, 8):
        r = _regmap(rm, r, "MIXER_CTRL3", "RW", "d_wl_rf_lo2g5g_mixer5g_trim_t%d[2:0]" % k,
                    4 * (k - 4) + 2, 4 * (k - 4), ADDR_M5G_B, "law/chenhao")
    r = _regmap(rm, r, "DFT_IDDQ", "RO", "d_wl_rf_trx_reg_dft_iddq_mode", 0, 0, ADDR_IDDQ, "yangteng")

    # ========================= total_memory_map 页 =========================
    tmm = wb.create_sheet("total_memory_map")
    r = 1
    r = _tmm_reg(tmm, r, "WL_TEMP_ctrl", ADDR_TEMP)
    r = _tmm_field(tmm, r, "d_wl_rf_temp_code_mode", "0", ADDR_TEMP, "N", "RW", "Tsensor mode select(1:RF reg 0:line)")
    r = _tmm_field(tmm, r, "d_wl_rf_temp_code_local[3:0]", "7:4", ADDR_TEMP, "N", "RW", "Temperature Code local 4bit")
    r = _tmm_reg(tmm, r, "TSENSOR_LINE", ADDR_TSENSOR)
    r = _tmm_field(tmm, r, "d_wl_rf_linectrl_tsensor", "3:0", ADDR_TSENSOR, "Y", "RO", "温度码线控(管脚,只读,force)")
    r = _tmm_reg(tmm, r, "MIXER_CTRL1", ADDR_M2G)
    for k in range(8):
        r = _tmm_field(tmm, r, "d_wl_rf_lo2g5g_mixer2g_trim_t%d" % k,
                       "%d:%d" % (2 * k + 1, 2 * k), ADDR_M2G, "N", "RW", "lo2g5g mixer2g gain trim t%d" % k)
    r = _tmm_reg(tmm, r, "MIXER_CTRL2", ADDR_M5G_A)
    for k in range(4):
        r = _tmm_field(tmm, r, "d_wl_rf_lo2g5g_mixer5g_trim_t%d" % k,
                       "%d:%d" % (4 * k + 2, 4 * k), ADDR_M5G_A, "N", "RW", "lo2g5g mixer5g gain trim t%d" % k)
    r = _tmm_reg(tmm, r, "MIXER_CTRL3", ADDR_M5G_B)
    for k in range(4, 8):
        r = _tmm_field(tmm, r, "d_wl_rf_lo2g5g_mixer5g_trim_t%d" % k,
                       "%d:%d" % (4 * (k - 4) + 2, 4 * (k - 4)), ADDR_M5G_B, "N", "RW",
                       "lo2g5g mixer5g gain trim t%d" % k)
    r = _tmm_reg(tmm, r, "DFT_IDDQ", ADDR_IDDQ)
    r = _tmm_field(tmm, r, "d_wl_rf_trx_reg_dft_iddq_mode", "0", ADDR_IDDQ, "Y", "RO", "IDDQ DFT 门控(管脚,只读,force)")

    # ========================= for_test 页（留空：真表这份文件 for_test 也是空的）=========================
    ft = wb.create_sheet("for_test")
    _set(ft, 1, {"A": "case", "B": "输入寄存器地址", "C": "value", "D": "需要验证的顶层信号",
                 "E": "预计输出", "F": "验证信号的输入信号"})
    ft.cell(row=2, column=1, value="（本文件 for_test 为空 —— designer 真值表在另一个 .xlsx；§4 回填对照目标）")

    # ========================= 其余代表性 stub（工具不读）=========================
    _stub_sheet(wb, "iddq",
                ["A", "B", "C", "out put signal", "logic expression", "abserve", "dft_input"],
                [["", "", "", "(本表问题信号的门控在 dft 页，不在 iddq 页)", "", "", ""]])
    _stub_sheet(wb, "level_shift",
                ["level_shift_input", "level", "level_shift_output_name", "suffix", "top_out", "Notes", "Owner"],
                [["d_wl_rf_temp_code_mode", "down", "d_wl_rf_temp_code_mode_ls", "ls", "0", "stub", "WL"]])
    _stub_sheet(wb, "Topout",
                ["Owner", "TOP Out Signal", "Reg Name", "Offset", "Reg Description", "RegAddress"],
                [["WL", "(本表 4 个问题信号 top_out=0，非顶层输出)", "", "", "", ""]])

    wb.save(path)
    return path


def _stub_sheet(wb, name, headers, samples):
    ws = wb.create_sheet(name)
    ws.cell(row=1, column=1, value="【STUB】%s —— 工具不读此页；代表性示意" % name)
    for c, h in enumerate(headers, start=1):
        ws.cell(row=2, column=c, value=h)
    for ri, row in enumerate(samples, start=3):
        for c, v in enumerate(row, start=1):
            ws.cell(row=ri, column=c, value=v)
    return ws


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else "mirror_wl_dreg.xlsx"
    build(out)
    print("已生成镜像 Excel:", out)
