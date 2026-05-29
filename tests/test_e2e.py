# -*- coding: utf-8 -*-
"""端到端测试：合成 Excel → 全管线 → 校验 .sv 关键产物（含 rule4 同地址 RW 合并的已知值）。"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dreg_verify import excel_model, generator  # noqa: E402
from dreg_verify import sv_writer as W          # noqa: E402
from dreg_verify import vectors as V            # noqa: E402
from dreg_verify import resolver as R           # noqa: E402
import fixtures                                 # noqa: E402


@pytest.fixture(scope="module")
def wb(tmp_path_factory):
    path = tmp_path_factory.mktemp("xl") / "synthetic_dreg.xlsx"
    fixtures.build_workbook(str(path))
    return excel_model.load_workbook(str(path))


# ───────────── 读取 ─────────────
def test_load_logic(wb):
    assert len(wb.logic) == 3
    names = {s.out_name for s in wb.logic}
    assert "d_logic_bt_lp_reserve" in names
    assert "d_logic_bt_lp_lna_agc[2:0]" in names
    assert "d_en_refbuf" in names


def test_reserve_inputs_parsed(wb):
    sig = next(s for s in wb.logic if s.out_name == "d_logic_bt_lp_reserve")
    assert sig.assert_id == "106"
    assert set(sig.inputs) == {"A", "B", "C", "J"}
    assert sig.inputs["A"]["base"] == "d_bt_lp_linelocal_mode_ctrl"
    assert sig.inputs["J"]["base"] == "d_bt_lp_iddq"


def test_tmm_addresses(wb):
    assert wb.tmm["d_bt_lp_linelocal_mode_ctrl"].address == 0x2D
    assert wb.tmm["d_bt_lp_linelocal_mode_ctrl"].bit_lsb == 0
    assert wb.tmm["d_bt_lp_bt_mode_sel_local"].address == 0x2D
    assert wb.tmm["d_bt_lp_bt_mode_sel_local"].bit_lsb == 1
    assert wb.tmm["d_bt_lp_iddq"].reg_type == "RO"


# ───────────── 解析 RO/RW ─────────────
def test_resolve_kinds(wb):
    res = R.Resolver(wb)
    sig = next(s for s in wb.logic if s.out_name == "d_logic_bt_lp_reserve")
    b = res.resolve_signal_inputs(sig)
    assert b["A"].kind == "RW" and b["A"].address == 0x2D and b["A"].reg_lsb == 0
    assert b["C"].kind == "RW" and b["C"].address == 0x2D and b["C"].reg_lsb == 1
    assert b["B"].kind == "RW" and b["B"].address == 0x2C
    assert b["J"].kind == "RO"          # iddq DIG TOP PIN=Y / type RO → force


# ───────────── rule4：同地址 RW 合并成一条 RF_WRITE，值与记忆里 .sv 一致 ─────────────
def test_rule4_merge_rf_write(wb):
    res = R.Resolver(wb)
    sig = next(s for s in wb.logic if s.out_name == "d_logic_bt_lp_reserve")
    bindings = res.resolve_signal_inputs(sig)
    used = set("ABCJ")

    # A=linelocal_mode_ctrl(bit0), C=bt_mode_sel_local(bit1) 都在 0x2D
    # 构造 A=1,C=1 → 寄存器值 = (1<<0)|(1<<1) = 0x3
    vec = V.TestVector(0, {"A": 1, "B": 0, "C": 1, "J": 0}, exp_value=1, exp_width=1)
    lines, unresolved = W._build_drive_lines(vec, bindings, used)
    text = "\n".join(lines)
    assert not unresolved
    assert "`RF_WRITE(10'h02D, 16'h0003)" in text       # 记忆: T2 → 16'h3
    assert "`RF_WRITE(10'h02C, 16'h0000)" in text        # bt_mode_sel=0 在 0x2C
    assert "force `ENV_RF.d_bt_lp_iddq = 16'h0000" in text  # J=iddq RO → force

    # A=0,C=1 → 0x2 ；A=0,C=0 → 0x0
    for c, expect in [(1, "16'h0002"), (0, "16'h0000")]:
        v = V.TestVector(0, {"A": 0, "B": 0, "C": c, "J": 0}, 0, 1)
        ls, _ = W._build_drive_lines(v, bindings, used)
        assert "`RF_WRITE(10'h02D, %s)" % expect in "\n".join(ls)


# ───────────── 期望值求值正确 + 标签 ─────────────
def test_full_build_reserve(wb):
    opts = generator.GenOptions(signals=["d_logic_bt_lp_reserve"], mode="min")
    res = generator.build(wb, opts)
    assert res["summary"]["n_generated"] == 1
    assert res["summary"]["n_unresolved_signals"] == 0
    text = generator.render(res)
    assert "assert_106_T0:" in text
    assert "`ENV_RF.d_logic_bt_lp_reserve ==" in text


def test_lna_agc_mux_3bit(wb):
    opts = generator.GenOptions(signals=["d_logic_bt_lp_lna_agc"], mode="min")
    res = generator.build(wb, opts)
    assert res["summary"]["n_generated"] == 1
    text = generator.render(res)
    assert "assert_108_T0:" in text
    # 输出 3 位 → 期望值用 3'bxxx
    assert "`ENV_RF.d_logic_bt_lp_lna_agc[2:0] == 3'b" in text
    # lna_agc_line 是 RO(DIG PIN Y) → force；lna_agc_local 是 RW → RF_WRITE
    assert "force `ENV_RF.d_bt_lp_lna_agc_line" in text
    assert "`RF_WRITE(10'h032" in text


def test_ls_passthrough_name(wb):
    opts = generator.GenOptions(signals=["d_en_refbuf"], mode="min")
    res = generator.build(wb, opts)
    text = generator.render(res)
    # ls 信号输出名无 d_logic_ 前缀，原样
    assert "`ENV_RF.d_en_refbuf ==" in text
    assert "assert_50_T0:" in text


# ───────────── owner 筛选 ─────────────
def test_owner_filter(wb):
    opts = generator.GenOptions(owners=["Alice"])
    sigs = generator.select_signals(wb, opts)
    names = {s.out_name for s in sigs}
    assert names == {"d_logic_bt_lp_reserve", "d_en_refbuf"}   # Alice 拥有这两个
    opts2 = generator.GenOptions(owners=["Bob"])
    assert {s.out_name for s in generator.select_signals(wb, opts2)} == {"d_logic_bt_lp_lna_agc[2:0]"}


# ───────────── 负向用例 ─────────────
def test_negative_cases(wb):
    opts = generator.GenOptions(signals=["d_logic_bt_lp_reserve"],
                                neg_signals=["d_logic_bt_lp_reserve"],
                                neg_mode="invert", neg_which="first")
    res = generator.build(wb, opts)
    assert res["summary"]["n_negative"] == 1
    text = generator.render(res)
    assert "_NEG:" in text
    assert "故意填错" in text


# ───────────── 覆盖诊断 ─────────────
def test_diagnose(wb):
    d = generator.diagnose(wb, generator.GenOptions())
    assert d["input_kinds"]["UNKNOWN"] == 0
    assert d["input_kinds"]["RO"] >= 1        # iddq / lna_agc_line 是 RO(force)
    assert d["input_kinds"]["RW"] >= 1        # 多数字段是 RW(RF_WRITE)
    assert d["wide_inputs"] == []             # 合成表无 >16bit 输入
    assert d["unresolved"] == []              # 全部干净归类
    assert "RW" in d["tmm_type_raw"] and "RO" in d["tmm_type_raw"]


# ───────────── 全部生成 + 无解析错误 ─────────────
def test_generate_all(wb):
    opts = generator.GenOptions()
    res = generator.build(wb, opts)
    assert res["summary"]["n_generated"] == 3
    assert res["summary"]["n_parse_errors"] == 0
    assert res["summary"]["n_unresolved_signals"] == 0
