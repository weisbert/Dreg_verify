# -*- coding: utf-8 -*-
"""forms.classify 形态分类器（重构 S0）——把展开后 AST 按结构分 F0..F4，覆盖据此派发。
在金标准 fixtures 上实测每种形态的代表信号判对。纯 additive，不接产出路径(byte-identical 天然成立)。"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import make_mirror_btlp  # noqa: E402
from dreg_verify import excel_model as M  # noqa: E402
from dreg_verify import expr as E  # noqa: E402
from dreg_verify import forms as F  # noqa: E402
from dreg_verify import resolver as R  # noqa: E402
from dreg_verify import topout as T  # noqa: E402

import pytest  # noqa: E402


@pytest.fixture(scope="module")
def wb():
    return M.load_workbook("mirror_btlp_dreg.xlsx")


@pytest.fixture(scope="module")
def gated_wb(tmp_path_factory):
    p = tmp_path_factory.mktemp("g") / "gated.xlsx"
    make_mirror_btlp.build_dft_gated_ls(str(p))
    return M.load_workbook(str(p))


def _shape(wb, name):
    res = R.Resolver(wb)
    topo = next(t for t in wb.topout if t.name == name)
    root = T.resolve_root(wb, name)
    r = T.analyze_signal(wb, res, topo, root=root)
    ob = getattr(root.obj, "out_base", None) if root.obj else None
    return F.classify(r.node, r.bindings, gate=F.dft_gate_info(wb, ob))


def test_classify_register_is_F0(wb):
    s = _shape(wb, "clk_force_on")
    assert s.kind == F.REGISTER and not s.is_gated


def test_classify_boolean_is_F1(wb):
    # d_logic_bt_lp_reserve = (A?C:B)&(~D)，根是 Binary → 布尔(iddq=D 是显式输入、非 dft 页门)
    s = _shape(wb, "d_logic_bt_lp_reserve")
    assert s.kind == F.BOOLEAN
    assert s.control and s.data            # 有控制位(A/D)和数据(C/B)


def test_classify_mux_is_F2(wb):
    s = _shape(wb, "d_bt_lp_lna_itrim")
    assert s.kind == F.SELECT


def test_classify_gated_select_is_F4(gated_wb):
    # d_en_vco_fc_ls = iddq ? 0 : (fc_sel? faston : en_vco_fc) → 门控套选路
    s = _shape(gated_wb, "d_en_vco_fc_ls")
    assert s.kind == F.GATED and s.is_gated
    assert s.inner is not None and s.inner.kind == F.SELECT     # 内层=选路(F4)
    assert s.base_kind == F.SELECT
    assert s.gate and s.gate.get("gate_base") == "d_bt_lp_pll_dig_dft_iddq_mode"


def test_classify_pure_structural_units():
    # 纯结构单测：不依赖 wb，验四条判定边界
    assert F.classify(E.Var("A")).kind == F.REGISTER          # 单 Var → F0
    assert F.classify(E.Binary("&", E.Var("A"), E.Var("B"))).kind == F.BOOLEAN
    assert F.classify(E.Ternary(E.Var("A"), E.Var("C"), E.Var("B"))).kind == F.SELECT
    assert F.classify(None, expandable=False).kind == F.SELECT  # mux 根(无 AST)
    # gate 优先：同一布尔 AST 套门 → GATED，inner=布尔
    g = F.classify(E.Binary("&", E.Var("A"), E.Var("B")), gate={"gate_base": "x", "transparent": 0})
    assert g.kind == F.GATED and g.inner.kind == F.BOOLEAN


def test_dft_gate_info(wb, gated_wb):
    assert F.dft_gate_info(wb, "clk_force_on") is None         # mirror 无 dft 门
    g = F.dft_gate_info(gated_wb, "d_en_vco_fc")
    assert g and g["gate_base"] == "d_bt_lp_pll_dig_dft_iddq_mode"
    assert F.dft_gate_info(gated_wb, None) is None
