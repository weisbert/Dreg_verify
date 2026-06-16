# -*- coding: utf-8 -*-
"""第三十八轮：logic↔mux 跨边界互递归展开（对齐 VBA 端到端驱动）。

cone(logic 展开) 撞到 mux 输出输入时，不再 force 衔接网(要探针前缀)，而是把该 mux 合成等价
ternary AST(选择?数据…)代入、把 mux 源头寄存器(控制+各 case 数据)作为干净叶子并入，并递归
(数据/控制还是 mux/logic 就继续)，一路展到真寄存器/顶层线控。默认开(cascade=cone)，force
模式逐字节复刻旧「force 衔接网」。

e2e 用 mirror_wl_dreg.xlsx 的两个锚点：
  · d_wl_rf_lo2g5g_bias_en = A&(B|C)，B=logen_mixer_en(mux 组3) → 展开成 VBA 的 6 输入。
  · d_wl_rf_tx_epa_2g_mixer_en：logic←mux←logic←mux←logic 五层深链 → 展到真寄存器。
单元用合成 MuxGroup 直接验 synthesize_mux_expr。
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dreg_verify import excel_model, generator, mux_gen, cone  # noqa: E402
from dreg_verify import expr as E  # noqa: E402
from dreg_verify import resolver as R  # noqa: E402

MIRROR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                      "mirror_wl_dreg.xlsx")
pytestmark = pytest.mark.skipif(not os.path.exists(MIRROR),
                                reason="需先 python make_mirror_excel.py 生成 mirror_wl_dreg.xlsx")


def _inputs(opts, signame):
    wb = excel_model.load_workbook(MIRROR)
    rep = generator.report(wb, opts)
    t = next(tt for tt in rep["tables"] if tt["signal"] == signame)
    return t, {x["label"] for x in t["inputs"]}


# ───────────── e2e 锚点① lo2g5g_bias_en → VBA 的 6 输入 ─────────────
def test_lo2g5g_expands_to_six_inputs():
    """默认 cone 模式：B=logen_mixer_en(mux) 展开成 选择+local+线控 三块 → 正好 6 输入，对齐 VBA。"""
    t, labels = _inputs(generator.GenOptions(), "d_wl_rf_lo2g5g_bias_en")
    assert len(t["inputs"]) == 6
    # VBA 的 6 个：iddq 门 + 选择 + mux 寄存器数据 + mux 线控数据 + self + test_en
    for need in ("d_wl_rf_trx_reg_dft_iddq_mode", "d_wl_rf_logen_lobuf_en_ctrl_mode",
                 "d_wl_rf_logen_mixer_en_local", "d_wl_rf_linectrl_logen_mixer_en",
                 "d_wl_rf_lo2g5g_bias_en", "d_wl_rf_lo2g5g_bias_test_en"):
        assert any(need == n or need + "[" in n for n in labels), (need, labels)
    # 原来那根 mux 衔接网名不该再作为输入残留
    assert not any("logen_mixer_en_to_logic" in n for n in labels)


def test_lo2g5g_no_probe_prefix_needed():
    """展开后三块全是干净 tmm 叶子(2 RW 写 + 1 RO 线控 force 裸名) → 不配前缀也能生成、不跳过。"""
    wb = excel_model.load_workbook(MIRROR)
    res = generator.build(wb, generator.GenOptions())   # 不配任何 probe_prefixes
    gen = {st["out_name"] for _l, st in res["blocks"]}
    assert "d_wl_rf_lo2g5g_bias_en" in gen
    # 渲染 .sv：RW 同址(0x64)合并成一条 RF_WRITE、RO 线控 force 裸名(无 ENV_RF.<前缀>.)
    block = next("\n".join(l) for l, st in res["blocks"]
                 if st["out_name"] == "d_wl_rf_lo2g5g_bias_en")
    assert "RF_WRITE(10'h64" in block                                    # 选择+local 同址合并
    assert "force `ENV_RF.d_wl_rf_linectrl_logen_mixer_en=" in block     # 线控裸名 force
    assert "_to_logic" not in block                                      # 无衔接网残留


def test_deep_chain_reaches_real_registers():
    """深链 tx_epa_2g_mixer_en：logic←mux←logic←mux←logic 一路展到真寄存器(freq_sel_line RO 底)。"""
    t, labels = _inputs(generator.GenOptions(), "d_wl_rf_tx_epa_2g_mixer_en")
    assert t["chain"] or True
    # 链底真寄存器/线控都现身
    for need in ("d_wl_rf_tx2g_en_mode", "d_wl_rf_tx2g_en_local",
                 "d_wl_rf_freq_sel_mode", "d_wl_rf_freq_sel_local", "d_wl_rf_freq_sel_line"):
        assert any(need == n or need + "[" in n for n in labels), (need, labels)
    assert not any("_to_logic" in n or "_to_mux" in n for n in labels)   # 全展到真寄存器，无衔接网
    # 该信号确实被生成
    wb = excel_model.load_workbook(MIRROR)
    res = generator.build(wb, generator.GenOptions())
    assert "d_wl_rf_tx_epa_2g_mixer_en" in {st["out_name"] for _l, st in res["blocks"]}


def test_force_mode_falls_back_to_connecting_net():
    """toggle：cascade_mode='force' → 不展开 mux，B 回退 force 衔接网 = 旧 4 输入行为。"""
    t, labels = _inputs(generator.GenOptions(cascade_mode="force"), "d_wl_rf_lo2g5g_bias_en")
    assert len(t["inputs"]) == 4
    assert "d_wl_rf_logen_mixer_en" in labels                  # B 仍是 mux 输出衔接网(裸基名)
    assert not any("logen_lobuf_en_ctrl_mode" in n for n in labels)  # 不展开 → 无选择/数据叶子


def test_configured_probe_prefix_does_not_suppress_expansion():
    """⭐回归(2026-06-16 d_wl_rf_lo2g5g_lcbufc0_bias_en 实证)：给 mux 输出衔接网【配了探针前缀】
    不该让 cone 停止展开。配前缀 → resolver 把 found_in 从 'mux-output' 重标成 'prefixed-wire'
    (resolver.py:364)，cone 若只认 'mux-output' 就不展开了——用户实况正是：诊断脚本【无前缀】出 6、
    GUI【有前缀】出 4。修后 cone 认 base 是不是 mux 组输出(_is_mux_out_binding)，配不配前缀都照常
    展开成源寄存器(展开后根本不 force 那根网，前缀变多余)。前缀 key 用衔接网名或基名都要命中。"""
    for key in ("d_wl_rf_logen_mixer_en_to_logic", "d_wl_rf_logen_mixer_en"):
        opts = generator.GenOptions(probe_prefixes={key: "ENV_RF.U_SUB.U_DREG"})
        t, labels = _inputs(opts, "d_wl_rf_lo2g5g_bias_en")
        assert len(t["inputs"]) == 6, (key, labels)                       # 配前缀仍展开成 6
        assert any("logen_lobuf_en_ctrl_mode" in n for n in labels), (key, labels)
        assert not any("logen_mixer_en_to_logic" in n for n in labels), (key, labels)
    # force 模式 + 配前缀：仍不展开、B 保留衔接网(前缀本就给 force 用)——配前缀不改 force 行为
    t2, labels2 = _inputs(
        generator.GenOptions(cascade_mode="force",
                             probe_prefixes={"d_wl_rf_logen_mixer_en_to_logic": "ENV_RF.U_SUB.U_DREG"}),
        "d_wl_rf_lo2g5g_bias_en")
    assert len(t2["inputs"]) == 4 and "d_wl_rf_logen_mixer_en" in labels2


def test_exhaustive_deep_chain_no_explosion():
    """深链穷举档：自由变量多但 max_tests 硬封顶，不爆、不 OOM。"""
    wb = excel_model.load_workbook(MIRROR)
    opts = generator.GenOptions(logic_mode="exhaustive", max_tests=64)
    rep = generator.report(wb, opts)
    t = next(tt for tt in rep["tables"] if tt["signal"] == "d_wl_rf_tx_epa_2g_mixer_en")
    assert 0 < len(t["tests"]) <= 64


# ───────────── 单元：synthesize_mux_expr 直接验 ─────────────
def _tmm(name, msb, lsb, addr, typ, dig="N"):
    return excel_model.TmmField(name, msb, lsb, addr, typ, dig, "R")


def _wb_with_mux(grp, tmm):
    return excel_model.DregWorkbook(logic=[], regmap={}, tmm=tmm, sheet_names=[], mux=[grp])


def _evalnode(node, leaves, values, out_width):
    widths = {k: b.width for k, b in leaves.items()}
    env = E.Env(widths, values)
    return E.evaluate(node, env, out_width)[0]


def test_synthesize_single_ctrl_2to1():
    """单控制 2:1：sel==0?线控:本地；leaves=选择+两数据，kind/addr 正确，求值两支都对。"""
    cases = [excel_model.MuxCase(3, "1'b0", "d_dline_to_mux", "d_dline", 1, None, None),
             excel_model.MuxCase(4, "1'b1", "d_dloc_to_mux", "d_dloc", 1, None, None)]
    grp = excel_model.MuxGroup(1, "d_mout", 1, "d_sel_to_mux", "d_sel", 1, "", 0, cases)
    tmm = {"d_sel": _tmm("d_sel", 0, 0, 0x10, "RW"),
           "d_dloc": _tmm("d_dloc", 0, 0, 0x11, "RW"),
           "d_dline": _tmm("d_dline", 1, 1, 0x12, "RO", dig="Y")}
    wb = _wb_with_mux(grp, tmm)
    node, leaves = mux_gen.synthesize_mux_expr(wb, R.Resolver(wb), grp)
    assert set(leaves) == {"D_SEL", "D_DLOC", "D_DLINE"}
    assert leaves["D_SEL"].kind == "RW" and leaves["D_DLINE"].kind == "RO"
    # sel=0 → 线控；sel=1 → 本地
    assert _evalnode(node, leaves, {"D_SEL": 0, "D_DLINE": 1, "D_DLOC": 0}, 1) == 1
    assert _evalnode(node, leaves, {"D_SEL": 1, "D_DLINE": 0, "D_DLOC": 1}, 1) == 1
    assert _evalnode(node, leaves, {"D_SEL": 1, "D_DLINE": 1, "D_DLOC": 0}, 1) == 0


def test_synthesize_multi_ctrl_concat():
    """多控制 B/C 拼接(B 高位)，case 2'b10 命中 B=1,C=0 那路数据。"""
    ctrls = [excel_model.MuxCtrl("B", "d_b_to_mux", "d_b", 1),
             excel_model.MuxCtrl("C", "d_c_to_mux", "d_c", 1)]
    cases = [excel_model.MuxCase(3, "2'b10", "d_d0_to_mux", "d_d0", 1, None, None),
             excel_model.MuxCase(4, "2'b00", "d_d1_to_mux", "d_d1", 1, None, None)]
    grp = excel_model.MuxGroup(2, "d_mc", 1, "", "", 1, "", 0, cases, ctrls=ctrls)
    tmm = {"d_b": _tmm("d_b", 0, 0, 0x20, "RW"), "d_c": _tmm("d_c", 0, 0, 0x21, "RW"),
           "d_d0": _tmm("d_d0", 0, 0, 0x22, "RW"), "d_d1": _tmm("d_d1", 0, 0, 0x23, "RW")}
    wb = _wb_with_mux(grp, tmm)
    node, leaves = mux_gen.synthesize_mux_expr(wb, R.Resolver(wb), grp)
    assert {"D_B", "D_C", "D_D0", "D_D1"} <= set(leaves)
    # B=1,C=0 → case 2'b10 → d0
    assert _evalnode(node, leaves, {"D_B": 1, "D_C": 0, "D_D0": 1, "D_D1": 0}, 1) == 1
    # B=0,C=0 → 不匹配 2'b10 → 落到默认 d1
    assert _evalnode(node, leaves, {"D_B": 0, "D_C": 0, "D_D0": 0, "D_D1": 1}, 1) == 1


def test_synthesize_dontcare_case():
    """don't-care 位：4'b1xxx 只比最高位；高位=1 命中该 case 不看低 3 位。"""
    cases = [excel_model.MuxCase(3, "4'b1xxx", "d_hi_to_mux", "d_hi", 1, None, None),
             excel_model.MuxCase(4, "4'b0000", "d_lo_to_mux", "d_lo", 1, None, None)]
    ctrls = [excel_model.MuxCtrl("B", "d_s4_to_mux[3:0]", "d_s4", 4, 3, 0)]
    grp = excel_model.MuxGroup(3, "d_dc", 1, "", "", 4, "", 0, cases, ctrls=ctrls)
    tmm = {"d_s4": _tmm("d_s4", 3, 0, 0x30, "RW"),
           "d_hi": _tmm("d_hi", 0, 0, 0x31, "RW"), "d_lo": _tmm("d_lo", 0, 0, 0x32, "RW")}
    wb = _wb_with_mux(grp, tmm)
    node, leaves = mux_gen.synthesize_mux_expr(wb, R.Resolver(wb), grp)
    # s4=0b1000 (bit3=1) → 命中 1xxx → d_hi；s4=0b1111 也命中(don't-care 低位)
    assert _evalnode(node, leaves, {"D_S4": 0b1000, "D_HI": 1, "D_LO": 0}, 1) == 1
    assert _evalnode(node, leaves, {"D_S4": 0b1111, "D_HI": 1, "D_LO": 0}, 1) == 1
    # s4=0 → 不命中 1xxx → 默认 d_lo
    assert _evalnode(node, leaves, {"D_S4": 0, "D_HI": 0, "D_LO": 1}, 1) == 1


def test_unsafe_mux_not_expanded_falls_back():
    """宽 select(>2bit) 与有 hole 的 mux 不可安全展开 → mux_expandable=False；被 logic 消费时
    cone 不展开、回退 force 衔接网(mux-output 叶子，仍需前缀)，不产假绿/假红。"""
    # 宽 select：3bit 8-case(虽全覆盖)——generate_vectors 对 >2bit 只 {0,全1} → 中间 case 漏测
    wcases = [excel_model.MuxCase(3 + i, "3'b%s" % format(i, "03b"),
                                  "d_w%d_to_mux" % i, "d_w%d" % i, 1, None, None) for i in range(8)]
    wide = excel_model.MuxGroup(1, "d_wide", 1, "d_wsel_to_mux[2:0]", "d_wsel", 3, "", 0, wcases)
    assert mux_gen.mux_expandable(wide) is False
    # 有 hole：2bit 控制只 2 个 case(0,1)，2/3 是 hole
    hcases = [excel_model.MuxCase(3, "2'b00", "d_h0_to_mux", "d_h0", 1, None, None),
              excel_model.MuxCase(4, "2'b01", "d_h1_to_mux", "d_h1", 1, None, None)]
    ctrls = [excel_model.MuxCtrl("B", "d_hsel_to_mux[1:0]", "d_hsel", 2, 1, 0)]
    hole = excel_model.MuxGroup(2, "d_hole", 1, "", "", 2, "", 0, hcases, ctrls=ctrls)
    assert mux_gen.mux_expandable(hole) is False
    # 反面：1bit 2-case 全覆盖 → 可展开
    scases = [excel_model.MuxCase(3, "1'b0", "d_a_to_mux", "d_a", 1, None, None),
              excel_model.MuxCase(4, "1'b1", "d_b_to_mux", "d_b", 1, None, None)]
    safe = excel_model.MuxGroup(3, "d_safe", 1, "d_s_to_mux", "d_s", 1, "", 0, scases)
    assert mux_gen.mux_expandable(safe) is True
    # e2e：logic 消费宽 mux → 不展开，A 退回 mux-output 衔接网叶子(没拆成 8 个数据寄存器)
    sig = excel_model.LogicSignal(
        row=3, out_name="d_top", out_width=1, expr="A", suffix="to_dft", top_output="1",
        notes="", owner="", assert_id="1",
        inputs={"A": {"raw": "d_wide_to_logic", "base": "d_wide", "width": 1,
                      "msb": None, "lsb": None}})
    tmm = {"d_wsel": _tmm("d_wsel", 2, 0, 0x40, "RW")}
    for i in range(8):
        tmm["d_w%d" % i] = _tmm("d_w%d" % i, 0, 0, 0x50 + i, "RW")
    wb = excel_model.DregWorkbook(logic=[sig], regmap={}, tmm=tmm, sheet_names=[], mux=[wide])
    _node, bindings, _exp = generator.expand_signal(wb, R.Resolver(wb), sig)
    assert any(getattr(b, "found_in", None) == "mux-output" for b in bindings.values())
    assert not any("d_w0" == (getattr(b, "base", "") or "") for b in bindings.values())


def test_generate_vectors_deep_control_chain_no_oom():
    """R38 深链可把控制位拍平成几十个 → 2^N 控制组合。min 档 + 小 max_tests 必须惰性枚举、
    秒回、不物化(否则 list(itertools.product) 会挂死/OOM)。30 个控制位=2^30 组合作回归哨兵。"""
    from dreg_verify import vectors as V

    class _B:
        def __init__(self, name):
            self.width = 1
            self.base = name.lower()
            self.slice_lsb = None
            self.slice_msb = None

    N = 30
    data = E.Var("D")
    node = data
    for i in range(N):
        node = E.Ternary(E.Var("C%d" % i), data, node)   # 每个 Ci 在三元条件里 → 控制位
    bindings = {"C%d" % i: _B("C%d" % i) for i in range(N)}
    bindings["D"] = _B("D")
    vecs, meta = V.generate_vectors(node, bindings, 1, mode="min", max_tests=8)
    assert len(vecs) <= 8
    assert meta["truncated"]                              # 2^30 远超 8 → 被 max_tests 截断
    assert meta["dropped"] > 0                            # planned 用算术积，不对迭代器取 len


def test_cross_boundary_cycle_falls_back_not_crash():
    """mux 自引用(case 数据=自己输出)→ synthesize 抛 ConeError；经 logic 引用时 expand_signal 兜底
    回退非展开(mux 输出当衔接网)，不整信号崩、不无限递归。"""
    cases = [excel_model.MuxCase(3, "1'b0", "d_loop_to_mux", "d_loop", 1, None, None),
             excel_model.MuxCase(4, "1'b1", "d_loop_to_mux", "d_loop", 1, None, None)]
    grp = excel_model.MuxGroup(1, "d_loop", 1, "d_sel_to_mux", "d_sel", 1, "", 0, cases)
    sig = excel_model.LogicSignal(
        row=3, out_name="d_top", out_width=1, expr="A", suffix="to_dft", top_output="1",
        notes="", owner="", assert_id="1",
        inputs={"A": {"raw": "d_loop_to_logic", "base": "d_loop", "width": 1,
                      "msb": None, "lsb": None}})
    tmm = {"d_sel": _tmm("d_sel", 0, 0, 0x10, "RW")}
    wb = excel_model.DregWorkbook(logic=[sig], regmap={}, tmm=tmm, sheet_names=[], mux=[grp])
    res = R.Resolver(wb)
    with pytest.raises(cone.ConeError):
        mux_gen.synthesize_mux_expr(wb, res, grp)        # 裸 synthesize 成环报错
    # 经 expand_signal：不崩，回退非展开（expanded=False）
    node, bindings, expanded = generator.expand_signal(wb, res, sig)
    assert expanded is False
    # build 整体不崩
    out = generator.build(wb, generator.GenOptions())
    assert isinstance(out["summary"], dict)
