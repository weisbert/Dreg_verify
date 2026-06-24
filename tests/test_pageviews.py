# -*- coding: utf-8 -*-
"""pageviews.py 测试：logic/mux/dft/iddq 页本地（不 cone）视图模型（2026-06-24，additive）。

验证夹具 = mirror_btlp_dreg.xlsx（7 真族金标准）。核心断言：
  · dft/iddq 页按行读成 LogicSignal；
  · 各页 page_view_models 出真值表（inputs/tests/auto_label/exp_label），永不抛；
  · 页本地 = force 级联（不跨页 cone）：logic 行输入数 == 本行声明输入数（无 cone 叶子膨胀）；
  · 引擎复用正确：shallow logic 行(rx_en)页本地真值表期望值 == cone 引擎金标准。
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import make_mirror_btlp                              # noqa: E402
from dreg_verify import excel_model as M             # noqa: E402
from dreg_verify import pageviews as P               # noqa: E402
from dreg_verify import resolver as R                # noqa: E402
from dreg_verify import topout as T                  # noqa: E402


@pytest.fixture(scope="module")
def mirror(tmp_path_factory):
    p = tmp_path_factory.mktemp("pv") / "mirror_btlp_dreg.xlsx"
    make_mirror_btlp.build(str(p))
    return str(p)


@pytest.fixture(scope="module")
def wb(mirror):
    return M.load_workbook(mirror)


# ───────────── dft/iddq 页按行读 ─────────────
def test_dft_rows_read_as_logic_signals(wb):
    names = [s.out_name for s in wb.dft_rows]
    assert "clk_force_on" in names
    assert "d_logic_bt_lp_rx_en" in names
    # 每行有表达式 + 输入
    s = next(s for s in wb.dft_rows if s.out_base == "clk_force_on")
    assert s.expr and s.inputs


def test_iddq_rows_empty_in_mirror(wb):
    """mirror 的 iddq 页只有表头（无数据行）→ 空清单，不崩。"""
    assert wb.iddq_rows == []


# ───────────── page_signals / 可用性 ─────────────
def test_page_signals_counts(wb):
    assert len(P.page_signals(wb, "logic")) == len(wb.logic) > 0
    assert len(P.page_signals(wb, "mux")) == len(wb.mux) >= 1
    assert len(P.page_signals(wb, "dft")) == len(wb.dft_rows) > 0
    assert P.page_signals(wb, "iddq") == []
    assert P.page_available(wb, "logic") and not P.page_available(wb, "iddq")


# ───────────── logic 视图 ─────────────
def test_logic_view_models(wb):
    models = P.page_view_models(wb, "logic", mode="max", max_tests=64)
    by = {m["name"]: m for m in models}
    rx = by["d_logic_bt_lp_rx_en"]
    assert rx["kind"] == "logic" and rx["status"] == "ok"
    assert rx["expr"] == "(A?C:B)&(~D)"
    # 页本地不 cone：输入数 == 本行声明输入(A/B/C/D 各一物理网)，无 cone 叶子膨胀
    assert len(rx["inputs"]) == 4
    assert rx["n_vectors"] > 0 and len(rx["tests"]) == rx["n_vectors"]
    assert rx["auto_label"].startswith("auto_out")


def test_logic_view_shallow_matches_cone_engine(wb):
    """rx_en 输入全是寄存器(无上游 logic)→页本地真值表与 cone 引擎金标准应一致(引擎复用正确)。"""
    pm = next(m for m in P.page_view_models(wb, "logic", mode="max", max_tests=64)
              if m["name"] == "d_logic_bt_lp_rx_en")
    tm = next(m for m in T.topout_view_models(wb, mode="max", max_tests=64)
              if m["name"] == "d_logic_bt_lp_rx_en")
    # 同样的输入集合、同样的期望列（顺序可能不同，比集合）
    assert {i["base"] for i in pm["inputs"]} == {i["base"] for i in tm["inputs"]}
    assert len(pm["tests"]) == len(tm["tests"])


# ───────────── mux 视图 ─────────────
def test_mux_view_models(wb):
    models = P.page_view_models(wb, "mux", mode="max", max_tests=64)
    assert len(models) == 1
    m = models[0]
    assert m["name"].startswith("d_bt_lp_lna_itrim")
    assert m["kind"] == "mux" and m["status"] == "ok"
    assert m["n_vectors"] > 0
    # 控制 + 8 个数据寄存器
    assert any("ctrl" in (i.get("letters") or "") for i in m["inputs"])
    assert any("data" in (i.get("letters") or "") for i in m["inputs"])


# ───────────── dft 视图 ─────────────
def test_dft_view_passthrough(wb):
    models = P.page_view_models(wb, "dft", mode="max", max_tests=64)
    m = next(m for m in models if m["name"] == "clk_force_on")
    assert m["kind"] == "logic" and m["status"] == "ok"
    assert len(m["inputs"]) == 1            # 单输入(DFT 接线网) 透传
    assert m["n_vectors"] >= 1


# ───────────── 页本地 = force 级联（不跨页 cone） ─────────────
def test_page_resolver_is_force_cascade(wb):
    res = P._page_resolver(wb)
    assert res.cascade_mode == "force"


# ───────────── 护栏3：永不抛、坏表达式记账 ─────────────
def test_bad_expr_accounted_not_raised(wb):
    bad = M.LogicSignal(row=99, out_name="x_bad", out_width=1, expr="A ? (",
                        suffix="dft", top_output="", notes="", owner="t",
                        assert_id="D9", inputs={"A": {"raw": "a_to_dft", "base": "a_to_dft",
                                                      "width": 1, "msb": None, "lsb": None}})
    res = P.analyze_logiclike(wb, P._page_resolver(wb), bad)
    assert res.status == "error" and res.issues
    m = P.result_to_model(res)
    assert m["status"] == "error" and m["tests"] == []


def test_page_view_models_never_raises_for_all_pages(wb):
    for page in P.PAGES:
        models = P.page_view_models(wb, page, mode="max", max_tests=64)
        assert isinstance(models, list)


def test_page_sv_applies_probe_prefix_to_force_leaf(wb):
    """⭐页视图(logic/mux/dft)接入探针前缀(2026-06-24)：force 衔接网/叶子埋子模块时按 probe_prefixes
    加层级前缀，否则 force `ENV_RF.<裸名> CUVUNF。之前 pageviews 全程没传 probe_prefixes=缺这功能。"""
    pp = {"d_bt_lp_linectrl_rx_en": "U_SUB"}
    text, _ = P.build_page_sv(wb, "logic", mode="max", max_tests=8, probe_prefixes=pp)
    assert "`ENV_RF.U_SUB.d_bt_lp_linectrl_rx_en" in text     # RO force 叶子加前缀
    bare, _ = P.build_page_sv(wb, "logic", mode="max", max_tests=8)
    assert "d_bt_lp_linectrl_rx_en" in bare and "U_SUB" not in bare   # 默认无前缀=裸名(逐字节旧行为)
