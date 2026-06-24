# -*- coding: utf-8 -*-
"""子视图（logic/mux/dft/iddq）GUI 测试（2026-06-24，页本地·不 cone，复用 SignalView）。

无头 Qt（offscreen）。验证夹具 = mirror_btlp_dreg.xlsx。
DoD：子视图各列出本页行 + 真值表（不 cone）+ 可编辑 + 导出 .sv + iddq 空页优雅。
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import make_mirror_btlp                              # noqa: E402


@pytest.fixture(scope="module")
def gui_app():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PySide6")
    from PySide6 import QtWidgets
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    pytest.importorskip("PySide6")
    from dreg_verify import gui as G
    monkeypatch.setattr(G, "SETTINGS_PATH", str(tmp_path / "s.json"))
    monkeypatch.setattr(G, "EDITS_PATH", str(tmp_path / "e.json"))


@pytest.fixture(scope="module")
def mirror_path(tmp_path_factory):
    p = tmp_path_factory.mktemp("pv_gui") / "mirror_btlp_dreg.xlsx"
    make_mirror_btlp.build(str(p))
    return str(p)


@pytest.fixture()
def win(gui_app, mirror_path):
    from dreg_verify import gui as G
    w = G.MainWindow(); w.resize(1320, 840)
    w.path_edit.setText(mirror_path)
    w.on_load()
    yield w
    w.close()


def _row(v, name):
    from dreg_verify import gui as G
    for r in range(v.sig_table.rowCount()):
        if v.sig_table.item(r, G.TOPO_NAME).text() == name:
            return r
    raise AssertionError("行未找到: %s" % name)


# ───────────── 标签结构 ─────────────
def test_six_main_tabs(win):
    names = [win.main_tabs.tabText(i) for i in range(win.main_tabs.count())]
    assert names == ["Topout 视图", "logic 视图", "mux 视图", "dft 视图", "iddq 视图", "排查(旧)"]
    assert set(win.page_views) == {"logic", "mux", "dft", "iddq"}


# ───────────── logic 子视图：列出本页行 + 真值表（不 cone） ─────────────
def test_logic_subview_lists_rows_and_truth(win):
    from dreg_verify import gui as G
    v = win.page_views["logic"]
    assert v.sig_table.rowCount() == len(win.wb.logic) > 0
    r = _row(v, "d_logic_bt_lp_rx_en")
    v.sig_table.setCurrentCell(r, G.TOPO_NAME)
    assert v.cur_an["kind"] == "logic"
    # 不 cone：输入数 == 本行声明输入(A/B/C/D)，无 cone 叶子膨胀
    assert len(v.e_inputs) == 4
    assert v.truth.rowCount() == 6 and v.truth.columnCount() > 0


def test_logic_subview_chain_substitution(win):
    from dreg_verify import gui as G
    v = win.page_views["logic"]
    v.sig_table.setCurrentCell(_row(v, "d_logic_bt_lp_rx_en"), G.TOPO_NAME)
    txt = v.chain.toPlainText()
    assert "(A?C:B)" in txt and "d_bt_lp_linelocal_mode_ctrl" in txt


def test_logic_subview_edit_and_export(win):
    from dreg_verify import gui as G
    v = win.page_views["logic"]
    v.sig_table.setCurrentCell(_row(v, "d_logic_bt_lp_rx_en"), G.TOPO_NAME)
    base, _ = v.provider.render_sv(None, "max", 64, False, {})
    assert "assert (" in base
    n0 = len(v.cur_cols)
    v._e_add()
    assert len(v.cur_cols) == n0 + 1
    v._e_clear()
    text, _ = v.provider.render_sv(None, "max", 64, False, v._compute_edited())
    # 清零该 logic 信号 → 导出不再含它的断言
    assert "`ENV_RF.d_logic_bt_lp_rx_en==" not in text


# ───────────── mux 子视图 ─────────────
def test_mux_subview(win):
    from dreg_verify import gui as G
    v = win.page_views["mux"]
    assert v.sig_table.rowCount() == len(win.wb.mux) >= 1
    v.sig_table.setCurrentCell(0, G.TOPO_NAME)
    assert v.cur_an["kind"] == "mux"
    assert v.truth.columnCount() > 0
    text, _ = v.provider.render_sv(None, "max", 64, False, {})
    assert "assert (" in text


# ───────────── dft 子视图：passthrough ─────────────
def test_dft_subview_passthrough(win):
    from dreg_verify import gui as G
    v = win.page_views["dft"]
    assert v.sig_table.rowCount() == len(win.wb.dft_rows) > 0
    v.sig_table.setCurrentCell(_row(v, "clk_force_on"), G.TOPO_NAME)
    assert len(v.e_inputs) == 1                 # 单输入(DFT 接线网)透传
    assert v.truth.rowCount() == 3              # 1 输入 + auto + 期望


# ───────────── iddq 子视图：空页优雅 ─────────────
def test_iddq_subview_empty_graceful(win, monkeypatch):
    from PySide6 import QtWidgets
    v = win.page_views["iddq"]
    assert v.sig_table.rowCount() == 0          # mirror iddq 页空
    assert "iddq" in v.detail.text()            # 提示而非崩
    # has_page False → on_preview 弹提示并 return（不崩）；monkeypatch 防模态阻塞
    monkeypatch.setattr(QtWidgets.QMessageBox, "information", staticmethod(lambda *a, **k: None))
    v.on_preview()


# ───────────── 子视图截图过目 ─────────────
def test_subview_screenshots(win, gui_app):
    from dreg_verify import gui as G
    out_dir = os.environ.get("TOPO_SHOT_DIR") or os.path.dirname(os.path.abspath(__file__))
    for idx, pg in ((1, "logic"), (2, "mux"), (3, "dft")):
        win.main_tabs.setCurrentIndex(idx)
        v = win.page_views[pg]
        if v.sig_table.rowCount():
            v.sig_table.setCurrentCell(0, G.TOPO_NAME)
        win.show(); gui_app.processEvents()
        png = os.path.join(out_dir, "subview_%s.png" % pg)
        assert win.grab().save(png) and os.path.getsize(png) > 0
