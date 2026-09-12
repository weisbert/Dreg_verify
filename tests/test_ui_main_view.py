# -*- coding: utf-8 -*-
"""test_ui_main_view.py —— ⑤⑥⑦⑧ 主视图容器（`dreg_verify/ui/main_view.py`，C2-c）。

契约 ID（附录 A「MAINVIEW」1 条）：
    C-280 图在 GUI 详情页显示：真值表下方常驻电路图区（truthH 分割可拖、flowFull 可全屏）
配合验：C-255（Ctrl+P 切 .sv 预览标签，快捷键由组合根绑）、C-297（真值表放大隐藏电路图）。

本模块是**容器**：真值表（C3）与电路图（C2-b）在别的 worktree 并行造，所以三块一律注入；
不注入时放带 objectName 的占位（沿用 `ui/app.py` 的 `PLACEHOLDER_AREAS` 名字），
C2-int / C3-int 把真件传进来即可。两条路都在这里验。
"""

import os

import pytest

import ui_harness as H

pytest.importorskip("PySide6")

from PySide6 import QtCore, QtGui, QtWidgets                       # noqa: E402

from dreg_verify.ui import app as APP                              # noqa: E402
from dreg_verify.ui import contracts, names, terms, theme          # noqa: E402
from dreg_verify.ui.main_view import MainView                      # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return H.app()


def _host(mv, w=1100, h=860):
    host = QtWidgets.QWidget()
    host.setObjectName("mainview_host")
    host.resize(w, h)
    lay = QtWidgets.QVBoxLayout(host)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.addWidget(mv)
    host.show()
    H.app().processEvents()
    return host


@pytest.fixture
def mv(qapp):
    view = MainView()
    host = _host(view)
    yield view, host
    host.close()


def _marker(object_name, text):
    """冒充真件的一块牌子（C2-b / C3 交付的 SigFlowPanel / TruthPanel 的位置）。"""
    w = QtWidgets.QLabel(text)
    w.setObjectName(object_name)
    w.setMinimumHeight(40)
    return w


# ═════════════════════════ C-280 真值表上 / 电路图下 ═════════════════════════

def test_c280_flow_below_truth_and_fullscreen_hides_truth(mv):
    """C-280：电路图常驻真值表**下方**（同一个垂直分割），全屏时收起真值表、图占满。"""
    view, host = mv
    split = H.find(view, names.WIN_SPLIT_TRUTH_FLOW, QtWidgets.QSplitter)
    assert split.orientation() == QtCore.Qt.Vertical
    assert split.count() == 2
    assert split.widget(0) is view.truth_widget
    assert split.widget(1) is view.flow_widget
    assert view.truth_widget.objectName() == names.TRUTH_PANEL
    assert view.flow_widget.objectName() == names.FLOW_PANEL
    # 几何上真值表在上面（不只是 index 顺序）
    assert (view.truth_widget.mapTo(host, QtCore.QPoint(0, 0)).y()
            < view.flow_widget.mapTo(host, QtCore.QPoint(0, 0)).y())
    assert view.truth_widget.isVisible() and view.flow_widget.isVisible()
    H.shot(host, "mainview_truth_and_flow")

    seen = []
    view.flowFullscreenChanged.connect(seen.append)
    view.set_flow_fullscreen(True)
    H.app().processEvents()
    assert seen == [True] and view.flow_fullscreen()
    assert not view.truth_widget.isVisible(), "全屏时真值表该收起（拍板 #8）"
    assert view.flow_widget.isVisible()
    assert H.find(view, names.TABS_BAR).isVisible(), "标签条要留着，随时切回"
    H.shot(host, "mainview_flow_fullscreen")

    view.set_flow_fullscreen(False)
    H.app().processEvents()
    assert seen == [True, False]
    assert view.truth_widget.isVisible() and view.flow_widget.isVisible()
    assert view.truth_height() == theme.TRUTH_H


def test_c280_truth_height_default_and_clamp(mv):
    """C-280 / C-262：`truthH` 默认 420、可拖、clamp 在 `theme.CLAMP_TRUTH`，两边都拖不没。"""
    view, host = mv
    split = H.find(view, names.WIN_SPLIT_TRUTH_FLOW, QtWidgets.QSplitter)
    assert view.truth_height() == theme.TRUTH_H == 420
    assert not split.childrenCollapsible()
    assert split.handleWidth() == theme.HANDLE_W

    view.set_truth_height(300)
    H.app().processEvents()
    assert view.truth_height() == 300
    lo, hi = theme.CLAMP_TRUTH
    view.set_truth_height(lo - 100)
    assert view.truth_height() >= lo
    view.set_truth_height(hi + 500)
    assert view.truth_height() <= hi


def test_c297_truth_maximized_hides_flow(mv):
    """C-297：真值表放大 = 收起电路图（右栏由组合根同步收起，本模块只发信号）。"""
    view, host = mv
    seen = []
    view.truthMaximizedChanged.connect(seen.append)
    view.set_truth_maximized(True)
    H.app().processEvents()
    assert seen == [True] and view.truth_maximized()
    assert view.truth_widget.isVisible() and not view.flow_widget.isVisible()
    H.shot(host, "mainview_truth_maximized")
    view.set_truth_maximized(False)
    H.app().processEvents()
    assert seen == [True, False]
    assert view.flow_widget.isVisible()


# ═════════════════════════ ⑤ 标签条 ═════════════════════════

def test_c255_ctrl_p_switches_to_sv_tab(mv):
    """C-255：Ctrl+P 切到「.sv 预览」标签（快捷键由组合根绑，这里验绑上去之后真能切）。"""
    view, host = mv
    stack = H.find(view, names.MAIN_VIEW, QtWidgets.QStackedWidget)
    assert stack.currentIndex() == 0 and view.tab() == "truth"

    sc = QtGui.QShortcut(QtGui.QKeySequence(contracts.SHORTCUTS["sv_preview"]), host)
    sc.setContext(QtCore.Qt.WindowShortcut)
    sc.activated.connect(lambda: view.set_tab("sv"))

    seen = []
    view.tabChanged.connect(seen.append)
    H.keys(host, contracts.SHORTCUTS["sv_preview"])                # 真按键
    H.app().processEvents()
    assert stack.currentIndex() == 1
    assert view.tab() == "sv" and seen == ["sv"]
    assert H.find(view, names.TABS_SV).isChecked()
    assert not H.find(view, names.TABS_TRUTH).isChecked()
    H.shot(host, "mainview_sv_tab")


def test_mainview_tabs_are_two_and_clickable(mv):
    """⑤ 标签条只有两个（Design tabDef），真点击就能切。"""
    view, host = mv
    bar = H.find(view, names.TABS_BAR)
    btns = [b for b in bar.findChildren(QtWidgets.QToolButton)]
    assert len(btns) == 2
    assert {b.objectName() for b in btns} == {names.TABS_TRUTH, names.TABS_SV}
    assert H.find(view, names.TABS_SV).text() == terms.TAB_SV

    H.click(H.find(view, names.TABS_SV))
    assert view.tab() == "sv"
    H.click(H.find(view, names.TABS_TRUTH))
    assert view.tab() == "truth"


def test_mainview_truth_tab_shows_counts(mv):
    """真值表标签带「25 列 · 手填 7/25」（C-106 的标签面）；数不全时回到干净文案。"""
    view, host = mv
    btn = H.find(view, names.TABS_TRUTH)
    assert btn.text() == terms.TAB_TRUTH_PLAIN
    view.set_truth_counts(25, 7, 25)
    assert btn.text() == terms.TAB_TRUTH_FMT.format(ncols=25, n=7, m=25)
    assert "25 列" in btn.text() and "7/25" in btn.text()
    view.set_truth_counts(None, None, None)
    assert btn.text() == terms.TAB_TRUTH_PLAIN


# ═════════════════════════ 注入点 ═════════════════════════

def test_mainview_injects_real_widgets(qapp):
    """三块一律注入：给了真件就用真件，一个占位都不留。"""
    truth = _marker(names.TRUTH_PANEL, "truth")
    flow = _marker(names.FLOW_PANEL, "flow")
    sv = _marker(names.SV_PANEL, "sv")
    view = MainView(truth_widget=truth, flow_widget=flow, sv_widget=sv)
    host = _host(view)
    assert view.truth_widget is truth
    assert view.flow_widget is flow
    assert view.sv_widget is sv
    assert not any(w.property("placeholder") for w in view.findChildren(QtWidgets.QWidget))
    view.set_tab("sv")
    assert H.find(view, names.MAIN_VIEW, QtWidgets.QStackedWidget).currentWidget() is sv
    host.close()


def test_mainview_placeholder_names_match_app(mv):
    """不注入真件时的占位名仍然只从 `names.py` 取。

    C2-int 已把电路图 / .sv 预览接进组合根，`app.PLACEHOLDER_AREAS` 里只剩真值表（C3）；
    本模块的占位块是「单独起 MainView 不注入任何东西」时的兑底，三个名字照旧。"""
    view, host = mv
    assert set(APP.PLACEHOLDER_AREAS) == {names.TRUTH_PANEL}, APP.PLACEHOLDER_AREAS
    for nm in (names.TRUTH_PANEL, names.FLOW_PANEL, names.SV_PANEL):
        w = H.find(view, nm)
        assert w.property("placeholder") is True
    # MAIN_VIEW 仍是那个堆叠（test_ui_app 按 `.currentIndex()` 认页，换真件不能换类型）
    assert isinstance(H.find(view, names.MAIN_VIEW), QtWidgets.QStackedWidget)
    assert view.objectName() == names.MAIN_VIEW_PANEL
    assert os.path.exists(H.shot(host, "mainview_placeholders"))
