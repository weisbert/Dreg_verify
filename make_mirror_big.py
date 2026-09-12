# -*- coding: utf-8 -*-
"""make_mirror_big.py — 合成【大表】镜像：把 make_mirror_btlp 的 12 条信号按整族复制到 ≈N 条。

只为**性能冒烟**存在（GUI v2 D「载表→清单可点」「后台逐信号分析跑完」两个数），不是新夹具：
  · 一个字都不碰 `make_mirror_btlp.py`——先 `M.build(path)` 造出原镜像，再用它自己的行写入器
    (`M._logic/_mux/_dft/_regmap/_ls`) 往同一张表**追加副本**，命名全走镜像名 + `_d<k>` 后缀。
  · 用户真表 211 条 Topout 信号只能在公司机验；这里要的是**数量级 + 不冻结**的证据。

一个「副本」= 原镜像那 12 条要验信号的整族拷贝（结构、cone 深度、类型配比全同）：
    7 条 logic 选路族 + 1 条 `_ls`(logic→level_shift) + 2 条直连寄存器(dft 恒等观测)
  + 1 条 mux(控制=本副本的 tsensor) + 1 条 RO 回读(跳过+记账)
改名是**成套**的：输出名、Topout 名、logic 输入名、regmap 字段名、level_shift/dft 边名一起加
`_d<k>`（位宽括号前插入：`x[2:0]` → `x_d1[2:0]`），所以每一份的 cone 都能独立解析到源寄存器，
status 分布与原镜像一致（ok 11 : skip 1 per 12）。寄存器地址按副本整块错开，不撞地址不撞 bit。

为什么不做成「一张表里 N 个孤立信号」：孤立信号解析不出 cone，清单会整片 unresolved，
量出来的是「跳过 N 次」的时间，不是真表那种「每条都要展 cone 出向量」的时间——白量。

用法:
    python make_mirror_big.py [输出路径=mirror_big_dreg.xlsx] [信号数=200]
    from make_mirror_big import build; build("big.xlsx", n_signals=200)
"""

import sys

import openpyxl

import make_mirror_btlp as M

#: 一个副本贡献的 Topout 信号数（= 原镜像 B 列条数）
BLOCK = 12

#: 副本内 Topout 行的**排布次序** —— 尾巴那个不满的副本也要保住类型配比，
#: 所以 logic / register / mux / ro 是交错的，不是先排完 8 条 logic 再说。
#: 取值：("fam", i) = FAMILY 第 i 条；其余是四个特例键。
_TOPOUT_ORDER = [("fam", 0), ("fam", 1), "reg_clk", ("fam", 2), "mux", ("fam", 3),
                 ("fam", 4), "reg_dig", ("fam", 5), ("fam", 6), "ls", "ro"]

#: 副本寄存器地址分配：每副本一块连续地址，块内按这些键排（与原镜像的 14 个地址一一对应）。
_ADDR_KEYS = ["TOP_EN", "TESTMODE", "PLL_LINE", "RO41", "RO42", "RO43", "RO44",
              "RX_LOCAL", "DCOC_LOCAL", "AGC_LOCAL", "TSENS_LOCAL", "ITRIM_A", "ITRIM_B", "RO38"]

#: regmap 行的 Owner（与原镜像同分布 —— 清单的 owner 下拉要有得筛，不能全一个人）。
_OWNER = {"RX_LOCAL": "Wan Xu", "DCOC_LOCAL": "Wan Xu", "AGC_LOCAL": "Wan Xu",
          "TSENSOR_LOCAL": "Jiao Yexiang", "ITRIM_A": "Jiao Yexiang", "ITRIM_B": "Jiao Yexiang"}


def _tag(name, tag):
    """把 `_d<k>` 插到位宽括号**之前**：`x[2:0]` + `_d1` → `x_d1[2:0]`；无括号就直接接尾。

    这一步错了整族就散架：regmap 字段名带括号、logic 输入名带括号，两边插的位置必须一样，
    否则 `X_to_logic` 找不到 regmap 里的 `X`，cone 当场断在第一层。"""
    if not tag:
        return name
    i = name.find("[")
    return (name + tag) if i < 0 else (name[:i] + tag + name[i:])


def _addrs(d):
    """第 d 个副本的地址表 {键: 十进制地址}。原镜像占 1..97，副本从 100 起每块 16 个。"""
    base = 100 + d * 16
    return dict((k, base + i) for i, k in enumerate(_ADDR_KEYS))


def _replica_names(d):
    """第 d 个副本的 12 个 Topout 名，按 `_TOPOUT_ORDER` 排（含位宽括号，与 Topout B 列同形）。"""
    tag = "_d%d" % d
    out = []
    for key in _TOPOUT_ORDER:
        if isinstance(key, tuple):
            mem = M.FAMILY[key[1]]
            out.append(_tag(mem["out"] + M._ws(mem["w"]), tag))
        elif key == "reg_clk":
            out.append("clk_force_on" + tag)
        elif key == "reg_dig":
            out.append("en_dig_clk" + tag)
        elif key == "mux":
            out.append(_tag("d_bt_lp_lna_itrim[3:0]", tag))
        elif key == "ls":
            out.append("d_en_refbuf" + tag + "_ls")
        else:                                   # "ro"
            out.append("pll_lock_indicator" + tag)
    return out


def _append_support(wb, d):
    """把第 d 个副本的**支撑行**（logic/mux/dft/regmap/level_shift）全部追加进去。

    Topout 行另发（见 `_append_topout`）：尾巴那个副本只挂一部分 Topout 信号，但支撑行照样全写
    ——多出来的行不在 B 列就不是「要验信号」，与原镜像里那条 `d_en_pfd_ls` 同理（存在但不验）。"""
    tag = "_d%d" % d
    A = _addrs(d)
    lg, mx, dft = wb["logic"], wb["mux"], wb["dft"]
    rm, lvl = wb["regmap"], wb["level_shift"]

    # ── logic：7 条选路族 + refbuf ──
    r = lg.max_row + 1
    for mem in M.FAMILY:
        out = _tag(mem["out"] + M._ws(mem["w"]), tag)
        ins = [inp["base"] + tag + "_to_logic" + M._ws(inp["w"]) for inp in mem["ins"]]
        suffix = "to_mux,to_dft" if mem["out"].endswith("tsensor") else "to_dft"
        r = M._logic(lg, r, out, mem["expr"], ins, suffix=suffix, note=mem["note"])
    r = M._logic(lg, r, "d_en_refbuf" + tag, "D?(B?A:(A&C)):E",
                 ["d_en_refbuf%s_to_logic" % tag, "testmode%s_to_logic" % tag,
                  "en_pll%s_to_logic" % tag, "d_pll_line_ctrl_mode%s_to_logic" % tag,
                  "d_bt_lp_pll_en%s_to_logic" % tag],
                 suffix="to_dft", owner="Yao Wang", note="refbuf 使能(经 ls 出顶层 _ls)")

    # ── mux：lna_itrim（控制 = 本副本的 tsensor，不跨副本借控制）──
    r = mx.max_row + 1
    for k, case in enumerate(["4'b000x", "4'b001x", "4'b010x", "4'b011x",
                              "4'b100x", "4'b101x", "4'b110x", "4'b111x"], start=1):
        r = M._mux(mx, r, "d_bt_lp_lna_itrim_t%d%s_to_mux[3:0]" % (k, tag),
                   "d_logic_bt_lp_tsensor%s_to_mux[3:0]" % tag, case,
                   "d_bt_lp_lna_itrim%s[3:0]" % tag, 6 + d, dest="to_dft")

    # ── dft：两条直连寄存器的恒等观测 + 7 条 logic 的观测边 ──
    r = dft.max_row + 1
    r = M._dft(dft, r, "clk_force_on" + tag, "clk_force_on%s_to_dft" % tag)
    r = M._dft(dft, r, "en_dig_clk" + tag, "en_dig_clk%s_to_dft" % tag)
    for mem in M.FAMILY:
        r = M._dft(dft, r, _tag(mem["out"] + M._ws(mem["w"]), tag),
                   _tag(mem["out"] + tag + "_to_dft" + M._ws(mem["w"]), ""))

    # ── regmap：与原镜像逐行同构，只换名 + 换地址块 ──
    rows = [
            ("TOP_EN", "RW", "en_pll", 0, 0, "TOP_EN", "to_logic,to_dft", "to_pll_ctrl"),
            ("TOP_EN", "RW", "en_dig_clk", 8, 8, "TOP_EN", "to_logic", "to_logic"),
            ("TOP_EN", "RW", "clk_force_on", 9, 9, "TOP_EN", "to_dft", "to_pll_crg"),
            ("TESTMODE1", "RW", "testmode", 15, 15, "TESTMODE", "to_logic", "to_logic"),
            ("TESTMODE1", "RW", "d_en_refbuf", 14, 14, "TESTMODE", "to_logic", "to_logic"),
            ("REG_0x17", "RW", "d_pll_line_ctrl_mode", 14, 14, "PLL_LINE", "to_logic", "to_logic"),
            ("readro_reg_41", "RO", "d_bt_lp_bt_mode_sel", 0, 0, "RO41", "to_logic", ""),
            ("readro_reg_41", "RO", "d_bt_lp_linectrl_rx_en", 1, 1, "RO41", "to_logic", ""),
            ("readro_reg_41", "RO", "d_bt_lp_pll_dig_dft_iddq_mode", 2, 2, "RO41", "to_logic", ""),
            ("readro_reg_41", "RO", "d_bt_lp_pll_en", 3, 3, "RO41", "to_logic", ""),
            ("readro_reg_42", "RO", "d_bt_lp_linectrl_lna_agc[2:0]", 2, 0, "RO42", "to_logic", ""),
            ("readro_reg_42", "RO", "d_bt_lp_linectrl_lpf_agc[2:0]", 6, 4, "RO42", "to_logic", ""),
            ("readro_reg_43", "RO", "d_bt_lp_linectrl_rx_dcoc_i[6:0]", 6, 0, "RO43", "to_logic", ""),
            ("readro_reg_43", "RO", "d_bt_lp_linectrl_rx_dcoc_q[6:0]", 14, 8, "RO43", "to_logic", ""),
            ("readro_reg_44", "RO", "d_bt_lp_linectrl_tsensor[3:0]", 3, 0, "RO44", "to_logic", ""),
            ("RX_LOCAL", "RW", "d_bt_lp_linelocal_mode_ctrl", 0, 0, "RX_LOCAL", "to_logic", ""),
            ("RX_LOCAL", "RW", "d_bt_lp_bt_mode_sel_local", 1, 1, "RX_LOCAL", "to_logic", ""),
            ("RX_LOCAL", "RW", "d_bt_lp_linelocal_tsensor_ctrl", 2, 2, "RX_LOCAL", "to_logic", ""),
            ("RX_LOCAL", "RW", "d_bt_lp_rx_en_local", 4, 4, "RX_LOCAL", "to_logic", ""),
            ("DCOC_LOCAL", "RW", "d_bt_lp_local_rx_dcoc_i[6:0]", 6, 0, "DCOC_LOCAL", "to_logic", ""),
            ("DCOC_LOCAL", "RW", "d_bt_lp_local_rx_dcoc_q[6:0]", 14, 8, "DCOC_LOCAL", "to_logic", ""),
            ("AGC_LOCAL", "RW", "d_bt_lp_local_lna_agc[2:0]", 2, 0, "AGC_LOCAL", "to_logic", ""),
            ("AGC_LOCAL", "RW", "d_bt_lp_local_lpf_agc[2:0]", 6, 4, "AGC_LOCAL", "to_logic", ""),
            ("TSENSOR_LOCAL", "RW", "d_bt_lp_tsensor_local[3:0]", 3, 0, "TSENS_LOCAL", "to_logic", ""),
            ("READro_reg_38", "RO", "pll_lock_indicator", 15, 15, "RO38", "to_dft", "")]
    rows += [("ITRIM_A", "RW", "d_bt_lp_lna_itrim_t%d[3:0]" % k, 4 * (k - 1) + 3, 4 * (k - 1),
              "ITRIM_A", "to_mux", "") for k in range(1, 5)]
    rows += [("ITRIM_B", "RW", "d_bt_lp_lna_itrim_t%d[3:0]" % k, 4 * (k - 5) + 3, 4 * (k - 5),
              "ITRIM_B", "to_mux", "") for k in range(5, 9)]
    r = rm.max_row + 1
    for name, typ, signal, msb, lsb, akey, sfx, sfx2 in rows:
        r = M._regmap(rm, r, name + tag.upper(), typ, _tag(signal, tag),
                      msb, lsb, A[akey], sfx, sfx2, _OWNER.get(name, "Yao Wang"))

    # ── level_shift：refbuf 出顶层 `_ls` ──
    M._ls(lvl, lvl.max_row + 1, "d_en_refbuf%s_to_ls" % tag, "d_en_refbuf%s_ls" % tag)


def _append_topout(wb, names):
    """把一批 Topout 名追加到 B 列（其余列留空 = 原镜像里第 4 行之后那些行的样子）。"""
    top = wb["Topout"]
    r = top.max_row + 1
    for nm in names:
        M._set(top, r, {"A": "Yao Wang", "B": nm})
        r += 1


def build(path, n_signals=200):
    """造一张 ≈`n_signals` 条 Topout 信号的镜像表，返回 path。

    原镜像那 12 条是第 0 个副本（**原样保留**，含 for_test 7 条金标准）；不够的用整族副本补，
    最后一个副本按 `_TOPOUT_ORDER` 只挂需要的那几条，所以条数是**精确**的 n_signals
    （n_signals<12 时向上取到 12 —— 原镜像本身不拆）。"""
    n = max(int(n_signals), BLOCK)
    M.build(path)                                   # 第 0 个副本 = 原镜像，一字不改
    wb = openpyxl.load_workbook(path)
    need = n - BLOCK
    d = 0
    while need > 0:
        d += 1
        _append_support(wb, d)
        _append_topout(wb, _replica_names(d)[:min(need, BLOCK)])
        need -= BLOCK
    wb.save(path)
    return path


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else "mirror_big_dreg.xlsx"
    cnt = int(sys.argv[2]) if len(sys.argv) > 2 else 200
    build(out, cnt)
    print("已生成合成大表镜像: %s（Topout 信号 %d 条）" % (out, max(cnt, BLOCK)))
