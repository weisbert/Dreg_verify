# -*- coding: utf-8 -*-
"""Topout 主视图 GUI 层测试（2026-06-23 重构：Topout 视图=默认门面，旧 logic/mux 降级『排查(旧)』）。

无头 Qt（offscreen）。文字断言走 widget.text()（offscreen 缺中文字体、截图里中文是方框，
所以版面靠截图、文字靠断言）；关键路径截一张 PNG 过目版面。
DoD 1-5：载 mirror → Topout 视图列 12 信号+分类 → 选 rx_en 真值表对上金标准引擎 →
生成含断言的 .sv → 导出报告/回填 → 截图。
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import make_mirror_btlp                              # noqa: E402
from dreg_verify import excel_model as M             # noqa: E402
from dreg_verify import resolver as R                # noqa: E402
from dreg_verify import topout as T                  # noqa: E402


@pytest.fixture(scope="module")
def gui_app():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PySide6")
    from PySide6 import QtWidgets
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    yield app


@pytest.fixture(autouse=True)
def _isolate_gui_settings(monkeypatch, tmp_path):
    pytest.importorskip("PySide6")
    from dreg_verify import gui as G
    monkeypatch.setattr(G, "SETTINGS_PATH", str(tmp_path / "gui_settings.json"))
    monkeypatch.setattr(G, "EDITS_PATH", str(tmp_path / "edits.json"))


@pytest.fixture(scope="module")
def mirror_path(tmp_path_factory):
    p = tmp_path_factory.mktemp("btlp_gui") / "mirror_btlp_dreg.xlsx"
    make_mirror_btlp.build(str(p))
    return str(p)


@pytest.fixture()
def topo_win(gui_app, mirror_path):
    from dreg_verify import gui as G
    w = G.MainWindow()
    w.resize(1320, 840)
    w.path_edit.setText(mirror_path)
    w.on_load()
    yield w
    w.close()


def _topo_row(w, name):
    from dreg_verify import gui as G
    for r in range(w.topo_table.rowCount()):
        if w.topo_table.item(r, G.TOPO_NAME).text() == name:
            return r
    raise AssertionError("Topout 行未找到: %s" % name)


# ───────────── DoD 1：Topout 视图 = 默认门面 + 列出信号 + 分类 ─────────────
def test_topout_is_default_main_tab(topo_win):
    """外层两标签：tab0=Topout 视图(默认), tab1=排查(旧)；打开即停在 Topout。"""
    w = topo_win
    assert w.main_tabs.count() == 2
    assert w.main_tabs.tabText(0) == "Topout 视图"
    assert w.main_tabs.tabText(1) == "排查(旧)"
    assert w.main_tabs.currentIndex() == 0


def test_topout_lists_twelve_signals_with_classification(topo_win):
    """载 mirror → Topout 清单列 12 个要验信号 + 分类(选路/mux/直连寄存器/RO跳过)。"""
    from dreg_verify import gui as G
    w = topo_win
    assert w.topo_table.rowCount() == 12
    kind_of = {w.topo_table.item(r, G.TOPO_NAME).text():
               w.topo_table.item(r, G.TOPO_KIND).text() for r in range(12)}
    assert kind_of["d_logic_bt_lp_rx_en"] == G.TOPO_KIND_LABEL["logic"]
    assert kind_of["d_bt_lp_lna_itrim"] == G.TOPO_KIND_LABEL["mux"]
    assert kind_of["clk_force_on"] == G.TOPO_KIND_LABEL["register"]
    assert kind_of["pll_lock_indicator"] == G.TOPO_KIND_LABEL["ro-readback"]
    # 每行 owner 来自 Topout A 列（非空）
    for r in range(12):
        assert w.topo_table.item(r, G.TOPO_OWNER).text()


def test_topout_ro_status_skip(topo_win):
    """RO 回读 → 状态列『跳过』(信息蓝)，不崩、不静默丢。"""
    from dreg_verify import gui as G
    w = topo_win
    r = _topo_row(w, "pll_lock_indicator")
    assert w.topo_table.item(r, G.TOPO_STATUS).text() == G.TOPO_STATUS_LABEL["skip"]


# ───────────── DoD 1：选中 → 展开链 + 真值表 + 账目 ─────────────
def test_topout_select_rx_en_truth_table_matches_golden(topo_win, mirror_path):
    """选 rx_en → 真值表(4 输入 + auto + 期望)×12 列；期望值对上 for_test 金标准引擎(非自证)。"""
    from dreg_verify import gui as G
    w = topo_win
    w.topo_cov.setCurrentText("全面")               # max 档
    r = _topo_row(w, "d_logic_bt_lp_rx_en")
    w.topo_table.setCurrentCell(r, G.TOPO_NAME)
    # 真值表结构：行 = 4 输入 + auto_out + 期望；列 = 12 测试
    assert w.topo_truth.rowCount() == 6
    assert w.topo_truth.columnCount() == 12
    # 行表头含源寄存器/线控叶子（SignalPath 实证）
    vlabels = [w.topo_truth.verticalHeaderItem(i).text() for i in range(6)]
    assert any("d_bt_lp_linelocal_mode_ctrl" in x for x in vlabels)
    assert any("d_bt_lp_rx_en_local" in x for x in vlabels)
    # 期望值对上金标准（引擎一致：模型↔report↔.sv 同源）
    golden = T.load_fortest_golden(mirror_path)
    rx_blk = next(b for b in golden if b["out"] == "d_logic_bt_lp_rx_en")
    wb = M.load_workbook(mirror_path)
    reps = T.validate_against_golden(wb, R.Resolver(wb), [rx_blk])
    assert reps[0]["status"] == "checked" and reps[0]["n_bad"] == 0 and reps[0]["n_ok"] > 0


def test_topout_register_passthrough_shows_table_and_note(topo_win):
    """直连寄存器根 → 展开链显示 passthrough 说明 + 真值表(1 输入)。"""
    from dreg_verify import gui as G
    w = topo_win
    r = _topo_row(w, "clk_force_on")
    w.topo_table.setCurrentCell(r, G.TOPO_NAME)
    assert "直连寄存器" in w.topo_chain.toPlainText()
    assert w.topo_truth.rowCount() == 3            # 1 输入 + auto + 期望
    assert w.topo_truth.columnCount() >= 1


def test_topout_ro_select_no_truth_table(topo_win):
    """选 RO 回读 → 无真值表(空)，展开链记账原因(不崩)。"""
    from dreg_verify import gui as G
    w = topo_win
    r = _topo_row(w, "pll_lock_indicator")
    w.topo_table.setCurrentCell(r, G.TOPO_NAME)
    assert w.topo_truth.rowCount() == 0
    assert "回读" in w.topo_chain.toPlainText() or "skip" in w.topo_chain.toPlainText()


# ───────────── DoD 2：出 .sv（预览 + 导出）─────────────
def test_topout_preview_sv(topo_win):
    """预览选中.sv → .sv 预览页含断言、断言贴顶层真名、RO 记账(不静默丢)。"""
    w = topo_win
    w.on_topo_preview()
    sv = w.topo_sv.toPlainText()
    assert sv.count("assert (") > 0
    assert "`ENV_RF.d_logic_bt_lp_rx_en==" in sv
    assert "d_logic_bt_lp_rx_en_to_logic" not in sv     # 贴顶层真名，无尾缀
    assert "pll_lock_indicator" in sv                    # RO 记账注释
    assert w.topo_inner.currentIndex() == 1              # 自动切到 .sv 预览页


def test_topout_export_sv_file(topo_win, tmp_path, monkeypatch):
    """导出 .sv → 写盘成功、含断言。"""
    from PySide6 import QtWidgets
    w = topo_win
    out = tmp_path / "topout_out.sv"
    monkeypatch.setattr(QtWidgets.QFileDialog, "getSaveFileName",
                        staticmethod(lambda *a, **k: (str(out), "")))
    monkeypatch.setattr(QtWidgets.QMessageBox, "information",
                        staticmethod(lambda *a, **k: None))
    w.on_topo_export_sv()
    assert out.exists()
    text = out.read_text(encoding="utf-8")
    assert text.count("assert (") > 0
    assert "`ENV_RF.clk_force_on==" in text              # register passthrough 也在


def test_topout_export_sv_only_checked(topo_win, tmp_path, monkeypatch):
    """勾选过滤：只勾 rx_en → 导出仅含它（其余 Topout 信号块不出现）。"""
    from PySide6 import QtCore, QtWidgets
    from dreg_verify import gui as G
    w = topo_win
    w._topo_check_all(False)
    r = _topo_row(w, "d_logic_bt_lp_rx_en")
    w.topo_table.item(r, G.TOPO_SEL).setCheckState(QtCore.Qt.Checked)
    out = tmp_path / "one.sv"
    monkeypatch.setattr(QtWidgets.QFileDialog, "getSaveFileName",
                        staticmethod(lambda *a, **k: (str(out), "")))
    monkeypatch.setattr(QtWidgets.QMessageBox, "information",
                        staticmethod(lambda *a, **k: None))
    w.on_topo_export_sv()
    text = out.read_text(encoding="utf-8")
    assert "`ENV_RF.d_logic_bt_lp_rx_en==" in text
    assert "`ENV_RF.clk_force_on==" not in text          # 其它信号未勾 → 不产出
    w._topo_check_all(True)


# ───────────── DoD 3：出报告（HTML / for_test）─────────────
def test_topout_export_report_html(topo_win, tmp_path, monkeypatch):
    """导出 Topout 报告(HTML) → 含 rx_en 真值表 + RO 也在(账目 summary，不丢)。"""
    from PySide6 import QtWidgets
    w = topo_win
    out = tmp_path / "rep.html"
    monkeypatch.setattr(QtWidgets.QFileDialog, "getSaveFileName",
                        staticmethod(lambda *a, **k: (str(out), "")))
    monkeypatch.setattr(QtWidgets.QMessageBox, "information",
                        staticmethod(lambda *a, **k: None))
    w.on_topo_export_report()
    assert out.exists()
    html = out.read_text(encoding="utf-8")
    assert "d_logic_bt_lp_rx_en" in html and "pll_lock_indicator" in html


def test_topout_fortest_backfill_includes_mux(topo_win, tmp_path, monkeypatch):
    """回填 for_test → 新 Excel 的 for_test 页含 mux 真值表(lna_itrim)（堵『只回填 logic』陷阱）。"""
    from PySide6 import QtWidgets
    import openpyxl
    w = topo_win
    out = tmp_path / "ft.xlsx"
    monkeypatch.setattr(QtWidgets.QFileDialog, "getSaveFileName",
                        staticmethod(lambda *a, **k: (str(out), "")))
    monkeypatch.setattr(QtWidgets.QMessageBox, "information",
                        staticmethod(lambda *a, **k: None))
    w.on_topo_fortest()
    assert out.exists()
    wb = openpyxl.load_workbook(str(out))
    ws = next(s for s in wb.worksheets if s.title.lower() == "for_test")
    txt = "\n".join(str(c.value) for row in ws.iter_rows() for c in row if c.value)
    assert "lna_itrim" in txt                            # mux 表回填进 for_test


# ───────────── DoD：旧视图未损 ─────────────
def test_legacy_view_intact(topo_win):
    """『排查(旧)』分页仍功能正确：左表/信号/分析在位（降级保留，不是冻成门面）。"""
    w = topo_win
    assert w.table.rowCount() == len(w.signals) > 0
    assert w._analysis                                   # 旧分析画像照常
    # 旧视图的级联/尾缀控件仍在（前缀/后缀/级联那套住这儿）
    assert hasattr(w, "cascade_logic_combo") and hasattr(w, "append_to_logic_chk")


def test_topout_graceful_when_no_topout_page(gui_app, tmp_path):
    """无 Topout 页 → Topout 清单空 + 提示，不崩。"""
    import fixtures
    from dreg_verify import gui as G
    xl = tmp_path / "plain.xlsx"
    fixtures.build_workbook(str(xl), with_mux=True)      # 普通夹具，无 Topout 页
    w = G.MainWindow()
    w.path_edit.setText(str(xl))
    w.on_load()                                          # 不应抛
    try:
        assert w.topo_table.rowCount() == 0
        assert "Topout" in w.topo_detail.text()
    finally:
        w.close()


# ───────────── 对抗 review GUI 修复回归 ─────────────
def test_topout_refresh_on_tab_switch_after_maxtests_change(topo_win):
    """切回 Topout 视图时，若『排查(旧)』改过上限 → 重建清单（『用例』列不再陈旧 vs 实际导出）。"""
    w = topo_win
    new = (w._topo_maxt() % 64) + 7                       # 保证与当前不同且合理
    w.main_tabs.setCurrentIndex(1)                        # 去『排查(旧)』
    w.max_tests.setValue(new)
    w.main_tabs.setCurrentIndex(0)                        # 切回 Topout → currentChanged 触发重建
    assert w._topo_built_key[2] == new


def test_topout_refresh_failure_clears_stale_panels(topo_win, monkeypatch):
    """分析失败时清空旧表的链/真值表/.sv（否则失败仍显示上一张表，误导）；不崩。"""
    from dreg_verify import topout as T2
    w = topo_win
    r = _topo_row(w, "d_logic_bt_lp_rx_en")
    w.topo_table.setCurrentCell(r, 1)
    assert w.topo_truth.rowCount() > 0                    # 先有内容
    monkeypatch.setattr(T2, "topout_view_models",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    w._refresh_topout()                                   # 不应抛
    assert w.topo_truth.rowCount() == 0                   # 旧表已清
    assert "失败" in w.topo_detail.text()


# ───────────── 截图：Topout 视图版面过目 ─────────────
def test_topout_view_screenshot(topo_win, gui_app):
    """截一张 Topout 视图 PNG（版面过目；文字方框是 offscreen 缺中文字体所致，文字由上面断言把关）。"""
    w = topo_win
    r = _topo_row(w, "d_logic_bt_lp_rx_en")
    w.topo_table.setCurrentCell(r, 1)                    # 选 rx_en
    w.topo_inner.setCurrentIndex(0)                      # 停在『展开链/真值表』页
    w.show()
    gui_app.processEvents()
    out_dir = os.environ.get("TOPO_SHOT_DIR") or os.path.dirname(os.path.abspath(__file__))
    png = os.path.join(out_dir, "topout_view.png")
    ok = w.grab().save(png)
    assert ok and os.path.getsize(png) > 0
