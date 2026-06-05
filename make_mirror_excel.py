# -*- coding: utf-8 -*-
"""make_mirror_excel.py — 造一份尽量 mirror 真实 WL_RFTRX 表的开发夹具 Excel。

目的：第二十二轮 ultracode 实现 item③(iddq DFT 拍) / item④(级联两分支) 时，
有一个【真实信号名 + 真实地址 + 真实结构】的镜像表可跑，而不必依赖公司机的真表。

数据来源（第二十一轮情报）：
  cas.txt  → N=8 mixer2g_trim 的级联真值表（temp_code_mode 选 tsensor(RO)/local(RW)，
             再用 temp_code[3:1] 选 8 个 trim 源 t0..t7；slice_lsb=1）。
  iddq.txt → N=59 trxbuf_en_c0 / N=4 mixer2g_en：iddq_mode(RO,bit0) 门控，
             门 = `d_wl_rf_trx_reg_dft_iddq_mode`，表达式 iddq?0:func（dft 页）。
  真实地址：temp_code_local[3:0]@h1E bit7:4、trim 源打包@h157、iddq bit0、trxbuf_en@h19C bit11。

它严格按 excel_model 的列约定造 5 页(logic/regmap/total_memory_map/mux/dft)。
注意：本工具【不写】for_test 页——真表里 for_test 是 designer 产物 / 我们的回填目标，
解析器从不读它（excel_model 只读上述 5 页）。

用法:  python make_mirror_excel.py [输出路径=mirror_wl_dreg.xlsx]
"""

import sys

import openpyxl
from openpyxl.utils import column_index_from_string


def _set(ws, row, mapping):
    for col, val in mapping.items():
        ws.cell(row=row, column=column_index_from_string(col), value=val)


# ───────────── total_memory_map：寄存器块 + 字段（A=名 B=bit C=复位 D=DIG_TOP_PIN F=地址 H=类型）─────────────
def _tmm_reg(ws, r, name, addr):
    _set(ws, r, {"A": name, "B": addr, "C": "0", "D": "RW", "E": "register def"})
    return r + 1


def _tmm_field(ws, r, name, bit, addr, dig, typ, desc=""):
    _set(ws, r, {"A": name, "B": bit, "C": "0", "D": dig, "E": desc or "field",
                 "F": addr, "H": typ})
    return r + 1


def _regmap(ws, r, reg, typ, signal, bit_msb, bit_lsb, addr, owner):
    # regmap：D=Reg_Name F=Reg Type G=Signal_Name H=地址 J..Y=bit15..bit0(Y=bit0) AE=owner
    _set(ws, r, {"D": reg, "F": typ, "G": signal, "H": addr, "AE": owner})
    for b in range(bit_lsb, bit_msb + 1):
        col = column_index_from_string("Y") - b      # Y=bit0
        ws.cell(row=r, column=col, value=1)
    return r + 1


def _mrow(ws, r, mux_input, ctrl, case, out, group_no, dest="to_mux", owner="WL", top=1):
    # mux：A=mux_input(_to_mux) B=ctrl(_to_mux) F=case G=out H=去向 I=top L=owner N=组号
    _set(ws, r, {"A": mux_input, "B": ctrl, "F": case, "G": out,
                 "H": dest, "I": top, "L": owner, "N": group_no})
    return r + 1


# ───────────── for_test 页（工具不读，是 §4 回填目标；按真表列布局精确建两旗舰组）─────────────
# 列：A=case标记 B=寄存器地址 C=写值 D=待验证输出 E=预计输出(稀疏) F=cone输入逐行
#     G=Signal Value(bin) H=Reg(dec) I=Addr J=RO K=Bits L=msb M=lsb N=组号 O列起=T1,T2,...
def _ft_group(ws, start_row, group_no, output, expected, inputs):
    """inputs: list[(signal, ro_bool, bits, [T值...])]；每输入一行，组首行带 case/D/E。"""
    r = start_row
    for idx, (sig, ro, bits, tvals) in enumerate(inputs):
        if idx == 0:
            _set(ws, r, {"A": "case", "D": output, "E": expected})
        _set(ws, r, {"F": sig, "J": str(ro), "K": bits, "N": group_no})
        for ti, tv in enumerate(tvals):
            ws.cell(row=r, column=column_index_from_string("O") + ti, value=tv)
        r += 1
    return r + 1   # 组间留一空行


def _tk_vec(k, on, n=16):
    """trim 源 t_k 真值：mode=0 半在 T(k+1)、mode=1 半在 T(k+9) 处被选中(设满)，其余 00。"""
    return [on if (i == k or i == k + 8) else "0" * len(on) for i in range(n)]


def _add_fortest(wb):
    ws = wb.create_sheet("for_test")
    # 表头示意（真表 1..3 行为标题；工具不读，仅供人看）
    _set(ws, 2, {"A": "case", "B": "reg_addr", "C": "value", "D": "待验证输出", "E": "预计输出",
                 "F": "cone输入", "G": "SigVal(bin)", "H": "Reg(dec)", "I": "Addr",
                 "J": "RO", "K": "Bits", "L": "msb", "M": "lsb", "N": "组号"})
    ws.cell(row=2, column=column_index_from_string("O"), value="T1")
    ws.cell(row=2, column=column_index_from_string("O") + 1, value="T2")
    ws.cell(row=1, column=1,
            value="for_test（designer 真值表 / dreg_verify §4 回填目标；工具不读此页）")
    # 组 N=8：mixer2g_trim 级联 16 拍（mode=0 force tsensor + mode=1 写 local），源各 2 位
    mode = [0] * 8 + [1] * 8
    local = ["1111", "0000", "1101", "1100", "1011", "0000", "0000", "0000",
             "0000", "0010", "0100", "0110", "1000", "1010", "1100", "1110"]
    tsensor = ["0000", "0010", "0100", "0110", "1000", "1010", "1100", "1110",
               "1111", "0000", "1101", "1100", "1011", "0000", "0000", "0000"]
    g8 = [("d_wl_rf_temp_code_mode", False, "0", mode),
          ("d_wl_rf_temp_code_local[3:0]", False, "7:4", local),
          ("d_wl_rf_linectrl_tsensor[3:0]", True, "3:0", tsensor)]
    g8 += [("d_wl_logen_mixer2g_trim_t%d[1:0]" % k, False,
            "%d:%d" % (2 * k + 1, 2 * k), _tk_vec(k, "11")) for k in range(8)]
    r = _ft_group(ws, 4, 8, "d_wl_logen_mixer2g_trim[1:0]", "3", g8)
    # 组 N=59：trxbuf_en_c0 iddq 门控 3 拍（T1: iddq=1&功能=1→压0）
    g59 = [("d_wl_rf_trx_reg_dft_iddq_mode", True, "0", [1, 0, 0]),
           ("d_wl_logen_trxbuf_en_c0", False, "11", [1, 1, 0])]
    _ft_group(ws, r, 59, "d_wl_logen_trxbuf_en_c0", "0", g59)
    return ws


# ───────────── 其余 8 页：工具不读，代表性 stub（表头+样例行，真实列结构未抽取）─────────────
def _stub_sheet(wb, name, headers, samples, ncols=None):
    ws = wb.create_sheet(name)
    ws.cell(row=1, column=1, value="【STUB】%s —— 工具不读此页；表头/样例为代表性示意，非真表精确列结构" % name)
    for c, h in enumerate(headers, start=1):
        ws.cell(row=2, column=c, value=h)
    for ri, row in enumerate(samples, start=3):
        for c, v in enumerate(row, start=1):
            ws.cell(row=ri, column=c, value=v)
    if ncols and ncols > len(headers):
        ws.cell(row=2, column=ncols, value="…(stub: 真表共~%d 列)" % ncols)
    return ws


def _add_other_stubs(wb):
    _stub_sheet(wb, "main",
                ["项目", "模块", "版本", "日期", "负责人", "状态", "备注", "", ""],
                [["Hi1107C", "WL_RFTRX", "V100", "2026-06-05", "WL", "release", "镜像 stub", "", ""]], 9)
    _stub_sheet(wb, "NamingRule",
                ["前缀", "域", "方向", "类型", "示例", "说明"],
                [["d_wl_rf_", "WL", "in", "reg", "d_wl_rf_xxx", "WL RF 寄存器命名"],
                 ["d_wl_logen_", "WL", "out", "net", "d_wl_logen_xxx", "logen 输出网命名"]], 41)
    _stub_sheet(wb, "RegMapDesign",
                ["Reg_Name", "Field", "Reg Type", "Signal_Name", "Addr", "Bit", "Default", "Owner"],
                [["TEMP_CODE", "mode", "RW", "d_wl_rf_temp_code_mode", "h1E", "0", "0", "WL"],
                 ["TEMP_CODE", "local", "RW", "d_wl_rf_temp_code_local", "h1E", "7:4", "0", "WL"]], 247)
    _stub_sheet(wb, "level_shift",
                ["Signal", "From_Domain", "To_Domain", "Direction", "Cell", "Enable", "Location", "Note"],
                [["d_wl_rf_temp_code_mode", "AON", "WL_RF", "down", "LS_CELL", "iso_en", "top", "stub"]], 8)
    _stub_sheet(wb, "ISO",
                ["Signal", "Domain", "Iso_Type", "Clamp", "Enable", "Cell", "Location", "Note"],
                [["d_wl_logen_bias_en", "WL_RF", "clamp0", "0", "iso_en", "ISO_CELL", "top", "stub"]], 8)
    _stub_sheet(wb, "SignalPath",
                ["Signal", "Source", "Dest", "Through", "Type", "Width", "Domain", "Note"],
                [["d_wl_rf_temp_code", "mux56", "mux157/158", "temp_code[3:1]", "mux", "4", "WL_RF", "stub"]], 8)
    _stub_sheet(wb, "default_value",
                ["Signal", "Default", "Note"],
                [["d_wl_rf_temp_code_mode", "0", "复位默认"],
                 ["d_wl_rf_trx_reg_dft_iddq_mode", "0", "iddq 默认透传"]], 3)
    _stub_sheet(wb, "Topout",
                ["Top_Output", "Internal_Net", "Direction", "Width", "Pad", "Domain"],
                [["d_wl_logen_trxbuf_en_c0", "trxbuf_en_c0", "out", "1", "PAD_x", "WL_RF"]], 64)


def build(path):
    wb = openpyxl.Workbook()

    # ========================= logic 页 =========================
    # 头行 2；A..J=输入变量(列字母=表达式变量)，K=输出，L=表达式，M=type，N=top，P=owner，R=no
    ws = wb.active
    ws.title = "logic"
    _set(ws, 1, {"A": "<<输入信号(列字母=表达式变量)>>", "K": "<<输出>>", "L": "<<真值表达式>>"})
    _set(ws, 2, {"A": "in_A", "B": "in_B", "C": "in_C", "K": "logic输出名",
                 "L": "表达式", "M": "type", "N": "top_output", "O": "Notes",
                 "P": "owner", "R": "no"})
    logic_rows = [
        # trxbuf_en_c0：纯直通使能（功能值 = 寄存器使能位），被 dft 页 iddq 门控（item③ 最小镜像）。
        {"A": "d_wl_rf_logen_trxbuf_en_c0_local_to_logic",
         "K": "d_wl_logen_trxbuf_en_c0", "L": "A",
         "M": "ls", "N": 1, "O": "iddq 门控直通使能(N=59 镜像)", "P": "WL", "R": 1},
    ]
    for i, rd in enumerate(logic_rows, start=3):
        _set(ws, i, rd)

    # ========================= mux 页 =========================
    # 头行 2；A=mux_input(_to_mux) B~E=ctrl F=case G=out H=去向 I=top L=owner N=组号
    mx = wb.create_sheet("mux")
    _set(mx, 2, {"A": "mux_input", "B": "ctrl1", "C": "ctrl2", "F": "case",
                 "G": "mux_out", "H": "dest", "I": "top", "L": "owner", "N": "组号"})
    r = 3
    # ── 上游组 mux56：temp_code[3:0] = case(temp_code_mode){0:线控 tsensor(RO); 1:temp_code_local(RW)} ──
    r = _mrow(mx, r, "d_wl_rf_linectrl_tsensor_to_mux[3:0]", "d_wl_rf_temp_code_mode_to_mux",
              "1'b0", "d_wl_rf_temp_code[3:0]", 56)
    r = _mrow(mx, r, "d_wl_rf_temp_code_local_to_mux[3:0]", "d_wl_rf_temp_code_mode_to_mux",
              "1'b1", "d_wl_rf_temp_code[3:0]", 56)
    # ── 下游组 mux157：mixer2g_trim[1:0] = case(temp_code[3:1]){8 选 1: t0..t7} ──
    #    控制 = 上游输出的【切片 [3:1]】(slice_lsb=1) → 这就是 A1 切片级联。
    for k in range(8):
        r = _mrow(mx, r,
                  "d_wl_logen_mixer2g_trim_t%d_to_mux[1:0]" % k,
                  "d_wl_rf_temp_code_to_mux[3:1]",
                  "3'b%03d" % int(format(k, "b")),     # 3'b000..3'b111
                  "d_wl_logen_mixer2g_trim[1:0]", 157)
    # ── 下游组 mux158：mixer5g_trim[2:0] = case(temp_code[3:1]){8 选 1: t0..t7}（3-bit 宽度变体，跨两寄存器打包）──
    #    与 mixer2g_trim 共享同一上游 temp_code mux；验证 alt 分支 + slice 对 3-bit 数据/两寄存器源同样成立。
    for k in range(8):
        r = _mrow(mx, r,
                  "d_wl_logen_mixer5g_trim_t%d_to_mux[2:0]" % k,
                  "d_wl_rf_temp_code_to_mux[3:1]",
                  "3'b%03d" % int(format(k, "b")),
                  "d_wl_logen_mixer5g_trim[2:0]", 158)
    # ── mixer2g_en：mode 选 line(RO)/local(RW)，被 dft 页 iddq 门控（item③ mode-mux 镜像，N=4）──
    r = _mrow(mx, r, "d_wl_rf_linectrl_logen_ssb_en_to_mux", "d_wl_rf_lobuf_en_ctrl_mode_to_mux",
              "1'b0", "d_wl_logen_mixer2g_en", 4)
    r = _mrow(mx, r, "d_wl_rf_logen_ssb_en_ctrl_local_to_mux", "d_wl_rf_lobuf_en_ctrl_mode_to_mux",
              "1'b1", "d_wl_logen_mixer2g_en", 4)

    # ========================= dft 页 =========================
    # 头行 2；B=门信号(iddq_mode) D=被观测输出 E=门控表达式 B?0:A（iddq=0 透传 / iddq=1 压 0）
    dft = wb.create_sheet("dft")
    _set(dft, 2, {"B": "gate(to_dft)", "D": "observed_out", "E": "gate_expr"})
    r = 3
    _set(dft, r, {"B": "d_wl_rf_trx_reg_dft_iddq_mode", "D": "d_wl_logen_trxbuf_en_c0",
                  "E": "B?0:A"}); r += 1
    _set(dft, r, {"B": "d_wl_rf_trx_reg_dft_iddq_mode", "D": "d_wl_logen_mixer2g_en",
                  "E": "B?0:A"}); r += 1

    # ========================= regmap 页 =========================
    rm = wb.create_sheet("regmap")
    _set(rm, 2, {"D": "Reg_Name", "F": "Reg Type", "G": "Signal_Name",
                 "H": "addr", "I": "default", "J": "b15", "Y": "b0", "AE": "owner"})
    r = 3
    r = _regmap(rm, r, "TEMP_CODE", "RW", "d_wl_rf_temp_code_mode", 0, 0, "d1E", "WL")
    r = _regmap(rm, r, "TEMP_CODE", "RW", "d_wl_rf_temp_code_local", 7, 4, "d1E", "WL")
    r = _regmap(rm, r, "TSENSOR", "RO", "d_wl_rf_linectrl_tsensor", 3, 0, "d99", "WL")
    r = _regmap(rm, r, "MIXER2G_TRIM_SRC", "RW", "d_wl_logen_mixer2g_trim_t0", 1, 0, "d343", "WL")
    r = _regmap(rm, r, "MIXER5G_TRIM_SRC0", "RW", "d_wl_logen_mixer5g_trim_t0", 2, 0, "d344", "WL")
    r = _regmap(rm, r, "LOBUF_MODE", "RW", "d_wl_rf_lobuf_en_ctrl_mode", 0, 0, "d20", "WL")
    r = _regmap(rm, r, "SSB_LOCAL", "RW", "d_wl_rf_logen_ssb_en_ctrl_local", 3, 3, "d20", "WL")
    r = _regmap(rm, r, "SSB_LINE", "RO", "d_wl_rf_linectrl_logen_ssb_en", 2, 2, "d98", "WL")
    r = _regmap(rm, r, "TRXBUF_EN", "RW", "d_wl_rf_logen_trxbuf_en_c0_local", 11, 11, "d412", "WL")
    r = _regmap(rm, r, "DFT_IDDQ", "RO", "d_wl_rf_trx_reg_dft_iddq_mode", 0, 0, "d500", "WL")

    # ========================= total_memory_map 页 =========================
    tmm = wb.create_sheet("total_memory_map")
    r = 1
    r = _tmm_reg(tmm, r, "TEMP_CODE", "h1E")
    r = _tmm_field(tmm, r, "d_wl_rf_temp_code_mode", "0", "h1E", "N", "RW", "temp_code 模式选择(0:线控tsensor 1:local)")
    r = _tmm_field(tmm, r, "d_wl_rf_temp_code_local", "7:4", "h1E", "N", "RW", "temp_code 本地 4 位")
    r = _tmm_reg(tmm, r, "TSENSOR_LINE", "h99")
    r = _tmm_field(tmm, r, "d_wl_rf_linectrl_tsensor", "3:0", "h99", "Y", "RO", "温度码线控(管脚,只读,force)")
    # mixer2g_trim 8 个源 t0..t7 打包进 h157（bit1:0,3:2,...,15:14）
    r = _tmm_reg(tmm, r, "MIXER2G_TRIM_SRC", "h157")
    for k in range(8):
        r = _tmm_field(tmm, r, "d_wl_logen_mixer2g_trim_t%d" % k,
                       "%d:%d" % (2 * k + 1, 2 * k), "h157", "N", "RW",
                       "mixer2g_trim 源 t%d (2 位)" % k)
    # mixer5g_trim 8 个 3-bit 源：4 位对齐、每寄存器 4 个，t0..t3@h158 / t4..t7@h159（真表实证打包）
    r = _tmm_reg(tmm, r, "MIXER5G_TRIM_SRC0", "h158")
    for k in range(4):
        r = _tmm_field(tmm, r, "d_wl_logen_mixer5g_trim_t%d" % k,
                       "%d:%d" % (4 * k + 2, 4 * k), "h158", "N", "RW",
                       "mixer5g_trim 源 t%d (3 位)" % k)
    r = _tmm_reg(tmm, r, "MIXER5G_TRIM_SRC1", "h159")
    for k in range(4, 8):
        r = _tmm_field(tmm, r, "d_wl_logen_mixer5g_trim_t%d" % k,
                       "%d:%d" % (4 * (k - 4) + 2, 4 * (k - 4)), "h159", "N", "RW",
                       "mixer5g_trim 源 t%d (3 位)" % k)
    r = _tmm_reg(tmm, r, "LOBUF_EN_MODE", "h20")
    r = _tmm_field(tmm, r, "d_wl_rf_lobuf_en_ctrl_mode", "0", "h20", "N", "RW", "lobuf 使能 mode(line/local)")
    r = _tmm_field(tmm, r, "d_wl_rf_logen_ssb_en_ctrl_local", "3", "h20", "N", "RW", "ssb_en 本地")
    r = _tmm_reg(tmm, r, "SSB_EN_LINE", "h98")
    r = _tmm_field(tmm, r, "d_wl_rf_linectrl_logen_ssb_en", "2", "h98", "Y", "RO", "ssb_en 线控(管脚,只读,force)")
    r = _tmm_reg(tmm, r, "TRXBUF_EN", "h19C")
    r = _tmm_field(tmm, r, "d_wl_rf_logen_trxbuf_en_c0_local", "11", "h19C", "N", "RW", "trxbuf 使能(功能值)")
    r = _tmm_reg(tmm, r, "DFT_IDDQ", "h1F4")
    r = _tmm_field(tmm, r, "d_wl_rf_trx_reg_dft_iddq_mode", "0", "h1F4", "Y", "RO", "IDDQ DFT 门控(管脚,只读,force)")

    # ========================= 其余 9 页（工具不读；for_test 精确，其余 stub）=========================
    _add_fortest(wb)        # for_test：真值表精确(§4 回填目标)
    _add_other_stubs(wb)    # main/NamingRule/RegMapDesign/level_shift/ISO/SignalPath/default_value/Topout

    wb.save(path)
    return path


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else "mirror_wl_dreg.xlsx"
    build(out)
    print("已生成镜像 Excel:", out)
