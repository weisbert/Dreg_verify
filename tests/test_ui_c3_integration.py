# -*- coding: utf-8 -*-
"""GUI v2 C3-int：真值表接进组合根之后的**跨模块**行为（单块的契约测试在 test_ui_truth_*.py）。

这里只放「拆开看谁都对、接起来才可能错」的八件事：

  · 占位全清             —— `app.PLACEHOLDER_AREAS` 空了，窗口里一块 `placeholder` 都没有；
  · C-106                —— 手填进度一处算、标签条与标题栏两处同步；
  · C-297                —— 放大按钮 ↔ 容器双向，且**不回环**（去掉反向那根线当场红）；
  · C-261 truthH         —— 拖分割 → settings → 重新起窗恢复；
  · C-109 / C-258        —— Ctrl+D 只在网格有焦点时触发（清单有焦点时不误触）；
  · C-110 / C-112        —— 选 mux 信号 → 设置整表数据值 → state 桶变 + 整表同步 + .sv 预览跟着变；
  · C-295                —— 换信号后冻结列与网格行数一致；
  · I-05 / C-172         —— 改一格 → `state.put_edit` 恰好一次 + .sv 预览变。

口径与 `test_ui_c1_integration.py` / `test_ui_c2_integration.py` 一致：真组合根 + 真 state
（→ 真 provider / 真引擎）+ 真 worker，夹具只用 `tests/` 里的两张 mirror 镜像表
（公开仓，**绝不出现真实信号名**）。后台信号一律 `H.wait_for` 等，不写死毫秒。
"""
import os

import pytest

import ui_harness as H

pytest.importorskip("PySide6")

from PySide6 import QtCore, QtWidgets                          # noqa: E402

from dreg_verify.ui import app as A                            # noqa: E402
from dreg_verify.ui import contracts, names, persist, terms    # noqa: E402
from dreg_verify.ui.truth.panel import TruthPanel              # noqa: E402

LC = contracts.ListCol
LR = contracts.ListRole
TR = contracts.TruthRole


@pytest.fixture
def qapp():
    return H.app()


@pytest.fixture
def win(qapp, monkeypatch, tmp_path):
    """真组合根 + 真 state + 真 worker，settings/edits 隔离到 tmp。"""
    H.isolate_settings(monkeypatch, tmp_path)
    H.auto_dialogs(monkeypatch)
    w = A.build_window([])
    w.resize(1600, 900)
    w.show()
    yield w
    w.close()
    qapp.processEvents()


def _load(w, kind="wl"):
    """载表并等这一趟分析跑完。"""
    ended = []
    w.analysisEnded.connect(lambda vid, ok: ended.append((vid, ok)))
    w.load_path(H.mirror_path(kind))
    assert H.wait_for(lambda: bool(ended)), "worker 没跑完：%s" % (ended,)
    H.app().processEvents()
    return w.state.models()


def _select(w, name):
    """真点清单里那一行（不直接调 state.set_current）。"""
    panel = w.list_panel
    row = next((r for r in range(panel.proxy.rowCount())
                if panel.proxy.index(r, int(LC.NAME)).data(int(LR.NAME)) == name), None)
    assert row is not None, "清单里没有 %r（可见的：%s）" % (name, panel.visible_names())
    H.click_cell(panel.view, row, int(LC.NAME))
    H.app().processEvents()
    assert w.state.current_name == name
    return row


def _first_editable(w):
    """挑一个真值表**有行有列且可编辑**的信号（不同 mirror 上具体是哪条不写死）。"""
    for m in w.state.models():
        name = m["name"]
        an = w.state.analyze(name)
        if an and an.get("editable"):
            return name
    raise AssertionError("mirror 上一条可编辑信号都没有：%s"
                         % [m["name"] for m in w.state.models()])


def _first_mux(w):
    for m in w.state.models():
        name = m["name"]
        an = w.state.analyze(name)
        if an and an.get("editable") == "mux":
            return name
    raise AssertionError("mirror 上一条 mux 信号都没有")


# ═══════════════ ① 占位全清 ═══════════════
def test_c3_no_placeholders_left(win, qapp):
    """C3-int 的验收线：真值表换成真件，窗口里**一块占位都不剩**。"""
    w = win
    _load(w)
    assert A.PLACEHOLDER_AREAS == {}, A.PLACEHOLDER_AREAS
    assert isinstance(H.find(w, names.TRUTH_PANEL), TruthPanel)
    assert w.truth_panel is w.main_view.truth_widget, "注进 MainView 的不是组合根手上那一块"
    left = sorted(x.objectName() for x in w.findChildren(QtWidgets.QWidget)
                  if x.property("placeholder"))
    assert not left, "还有占位块没换：%s" % left
    # 面板确实挂上了 state 与总线（两条都断了的话下面七条全是空跑）
    assert w.truth_panel.state is w.state
    assert w.truth_panel.bus is w.bus
    assert os.path.exists(H.shot(w, "c3_wired_workbench"))


# ═══════════════ ② C-106 手填进度三处同步 ═══════════════
@pytest.mark.contract("C-106")
def test_c106_truth_progress_syncs_tab_and_header(win, qapp):
    """C-106：真值表 model 算一次 (n, m, k) → ⑤ 标签条与 ④ 标题栏**同时**刷新。

    变异验证（报告里记着）：把 `_connect_views` 里 `progressChanged` 那一行去掉，
    这条当场红 —— 标签停在「真值表 + 电路图」不带数，标题栏进度条一直是 0/0。
    """
    w = win
    _load(w)
    name = _first_editable(w)
    _select(w, name)
    tp, m = w.truth_panel, w.truth_panel.model
    assert m.columnCount() > 0, "真值表一列都没有，这条验不到东西"

    def _expect():
        n, mm, k = m.fill_progress()
        assert H.find(w, names.TABS_TRUTH).text() == \
            terms.TAB_TRUTH_FMT.format(ncols=m.columnCount(), n=n, m=mm), "⑤ 标签条没跟上"
        assert w.detail_header.progress()[:2] == (n, mm), "④ 标题栏进度条没跟上"
        assert w.detail_header.progress()[2] == (
            terms.HDR_PROGRESS_DIFF_FMT.format(k=k) if k > 0 else "")
        return n, mm, k

    n0, m0, _k0 = _expect()
    # 手填一格期望 → 三个数一起变（同一次 progressChanged）
    exp_r = m.rowCount() - 1
    c = next((j for j in range(m.columnCount())
              if m.flags(m.index(exp_r, j)) & QtCore.Qt.ItemIsEditable), None)
    assert c is not None, "一条可填期望的列都没有"
    assert m.setData(m.index(exp_r, c), "1", QtCore.Qt.EditRole)
    qapp.processEvents()
    n1, m1, _k1 = _expect()
    assert (n1, m1) != (n0, m0) or n1 == n0 + 1, "填了一格，手填数一点没动"
    # 加一条列 → 标签上的列数也跟着（列数不在信号里，是槽现问 model 的）
    n_cols = m.columnCount()
    tp.buttons["add_col"].click()
    qapp.processEvents()
    assert m.columnCount() == n_cols + 1
    _expect()


# ═══════════════ ③ C-297 放大双向不回环 ═══════════════
@pytest.mark.contract("C-297")
def test_c297_truth_maximize_two_way_without_feedback_loop(win, qapp):
    """C-297：放大按钮 → 容器收起电路图 + 组合根收起右栏；Esc / 容器反向 → 按钮弹回来。

    **不回环**：一次点击只经过一趟 `truthMaximizedChanged`（面板的 `set_maximized`
    blockSignals，不再发 `maximizeRequested`）。变异验证：去掉
    `mv.truthMaximizedChanged → tp.set_maximized` 那一行，Esc 之后按钮仍是按下态 → 红。
    """
    w = win
    _load(w)
    _select(w, _first_editable(w))
    tp, mv = w.truth_panel, w.main_view
    seen = []
    mv.truthMaximizedChanged.connect(lambda on: seen.append(on))

    assert not mv.truth_maximized() and not tp.max_btn.isChecked()
    H.click(tp.max_btn)                                  # 真点按钮
    qapp.processEvents()
    assert seen == [True], "一次点击发了 %d 趟 truthMaximizedChanged（回环了）" % len(seen)
    assert mv.truth_maximized() and w.is_fullscreen()
    assert not mv.flow_widget.isVisibleTo(w), "电路图没收起来"
    assert not H.find(w, names.SIDE_PANEL).isVisibleTo(w), "右栏没收起来（组合根那半边）"
    assert tp.max_btn.isChecked() and tp.max_btn.text() == terms.TRUTH_BTN["restore"]

    del seen[:]
    w.exit_fullscreen()                                  # Esc 的落点
    qapp.processEvents()
    assert seen == [False], "退出放大发了 %d 趟（回环了）" % len(seen)
    assert not mv.truth_maximized() and not w.is_fullscreen()
    assert mv.flow_widget.isVisibleTo(w)
    assert not tp.max_btn.isChecked(), "容器反向同步没接上：按钮还按着"
    assert tp.max_btn.text() == terms.TRUTH_BTN["maximize"]

    # 反过来：电路图全屏时真值表放大按钮也要弹回来（两种独占互斥）
    H.click(tp.max_btn)
    qapp.processEvents()
    w.on_flow_fullscreen(True)
    qapp.processEvents()
    assert not tp.max_btn.isChecked(), "电路图全屏了，真值表的放大按钮还按着"
    w.exit_fullscreen()


# ═══════════════ ④ truthH 往返 ═══════════════
@pytest.mark.contract("C-261")
def test_c261_truth_height_round_trips_through_settings(win, qapp, monkeypatch, tmp_path):
    """C-261：真值表区高度拖过之后进 settings 的 `truthH`，下次起窗恢复。"""
    w = win
    _load(w)
    _select(w, _first_editable(w))
    assert "truthH" in A.SIZE_KEYS
    lo, hi = w.main_view.truth_widget.minimumHeight(), 900
    want = min(hi, lo + 137)                       # 一个不等于出厂值的高度
    w.main_view.set_truth_height(want)
    w.main_view.splitter.splitterMoved.emit(0, 0)  # = 用户松开分割条
    qapp.processEvents()
    got = int(w.state.settings().get("truthH") or 0)
    assert got > 0 and abs(got - w.main_view.truth_height()) <= 1, \
        "拖过的高度没进 settings：%s / %s" % (got, w.main_view.truth_height())
    assert w.preferred_sizes()["truthH"] == got

    w2 = A.build_window([])                        # 同一份 settings（isolate_settings 指到 tmp）
    try:
        w2.resize(1600, 900)
        w2.show()                                  # 不 show 的话分割器还没有真实高度
        assert w2.preferred_sizes()["truthH"] == got
        assert abs(w2.main_view.truth_height() - got) <= 1, "起窗没按 settings 恢复真值表高度"
    finally:
        w2.close()
        qapp.processEvents()


# ═══════════════ ⑤ C-109 Ctrl+D 焦点判定 ═══════════════
@pytest.mark.contract("C-109", "C-258")
def test_c109_ctrl_d_duplicates_column_only_when_grid_has_focus(win, qapp):
    """C-109 / C-258：Ctrl+D 是**视图级**键位 —— 网格有焦点时复制列，别处按不误触。

    组合根故意不把它绑成 WindowShortcut（见 `app.APP_SHORTCUTS` 的注释）：绑了的话
    在清单里按 Ctrl+D 会去动真值表的列，而且「单元格正在编辑时不触发」这条也实现不了。
    """
    w = win
    _load(w)
    _select(w, _first_editable(w))
    m = w.truth_panel.model
    assert m.editable_kind() and m.columnCount() > 0

    # ① 清单有焦点：按 Ctrl+D **什么都不该发生**
    w.list_panel.view.setFocus(QtCore.Qt.OtherFocusReason)
    qapp.processEvents()
    n0 = m.columnCount()
    H.keys(w.list_panel.view, "Ctrl+D")
    qapp.processEvents()
    assert m.columnCount() == n0, "清单里按 Ctrl+D 也去复制真值表的列了"

    # ② 网格有焦点：复制当前列
    H.click_cell(w.truth_panel.grid, 0, 0)
    w.truth_panel.grid.setFocus(QtCore.Qt.OtherFocusReason)
    qapp.processEvents()
    assert w.truth_panel.grid.hasFocus()
    H.keys(w.truth_panel.grid, "Ctrl+D")
    qapp.processEvents()
    assert m.columnCount() == n0 + 1, "网格有焦点时 Ctrl+D 没复制列"
    assert "copy_col" not in getattr(w, "shortcuts", {}), \
        "copy_col 又被绑成窗口级快捷键了（APP_SHORTCUTS 的注释写了为什么不能）"


# ═══════════════ ⑥ C-110 / C-112 mux 数据值端到端 ═══════════════
@pytest.mark.contract("C-110", "C-112")
def test_c110_c112_mux_data_value_end_to_end(win, qapp, monkeypatch):
    """C-110 / C-112：工具条「设置 mux 数据值」→ `state.mux_data()` 桶变 + 整表同步 + .sv 预览跟着变。

    这条是 C3-int 唯一真正跨三层的回路：面板 → `model.set_reanalyzer` → `edits.set_mux_data_value`
    写会话档 → `state.analyze` 重分析 → model 整表重装；同一份会话档又被 `.sv` 预览
    （`exports.render_sv(edited=state.compute_edited())`）吃进去，所以屏幕值与导出值不许分家。
    """
    w = win
    _load(w)
    name = _first_mux(w)
    _select(w, name)
    tp, m = w.truth_panel, w.truth_panel.model
    rows = tp._mux_data_rows()
    assert rows, "这条 mux 没有可手填的物理数据寄存器行（换夹具）"
    base, width = rows[0]["base"], rows[0]["width"]
    low = name.lower()
    assert base not in (w.state.mux_data().get(low) or {}).get("data", {})

    w.main_view.set_tab("sv")                     # .sv 预览先算一遍（当基准）
    qapp.processEvents()
    sv_before = w.sv_preview.text()
    assert sv_before.strip(), ".sv 预览是空的，比不出变化"
    w.main_view.set_tab("truth")
    qapp.processEvents()

    new = 0b101 & ((1 << int(width or 1)) - 1)
    H.auto_dialogs(monkeypatch, answers={"MuxDataDialog.ask": {base: new}})
    H.click(tp.buttons["mux_data"])
    qapp.processEvents()

    # ① 会话档（生成器 / 导出吃的就是它）
    got = (w.state.mux_data().get(low) or {}).get("data", {})
    assert got.get(base) == new, "整表同步没落进 state.mux_data()：%s" % got
    # ② 表整体同步（不是只改一格）
    data_rows = [i for i, e in enumerate(tp._e_inputs) if (e["mux_data_base"] or "") == base]
    assert data_rows
    seen = {m.data(m.index(i, c), int(TR.RAW_VALUE))
            for i in data_rows for c in range(m.columnCount())}
    assert new in seen, "整表一条列都没取到手填值 = 所见非所得（C-112）"
    # ③ .sv 预览跟着变（同一份 edited 喂进 exports.render_sv）
    w.main_view.set_tab("sv")
    qapp.processEvents()
    sv_after = w.sv_preview.text()
    assert sv_after != sv_before, "改了 mux 数据值，.sv 预览一个字节都没变（屏幕与导出分家了）"


# ═══════════════ ⑦ C-295 换信号后冻结列与网格对齐 ═══════════════
@pytest.mark.contract("C-295")
def test_c295_frozen_names_track_grid_across_signal_switch(win, qapp):
    """C-295：冻结列的行数 / 行高 / 当前行始终跟着网格走 —— 换信号之后也一样。

    面板在每次 `model.load()` 之后都要再 `frozen.set_source(...)`（`truth/view.py` 明写）。
    漏了这一步的现象是「左边还是上一个信号的名字」——行数先对不上，这条就红。
    """
    w = win
    _load(w)
    seen = 0
    for mdl in w.state.models():
        name = mdl["name"]
        an = w.state.analyze(name)
        if not an:
            continue
        _select(w, name)
        tp = w.truth_panel
        fm, gm = tp.frozen.model(), tp.grid.model()
        assert fm.rowCount() == gm.rowCount(), \
            "%s：冻结列 %d 行、网格 %d 行" % (name, fm.rowCount(), gm.rowCount())
        if gm.rowCount():
            assert tp.frozen.rowHeight(0) == tp.grid.rowHeight(0)
            first = fm.data(fm.index(0, 0), QtCore.Qt.DisplayRole)
            assert first and str(first).strip(), "%s：冻结列首行是空的" % name
        seen += 1
    assert seen >= 3, "只换了 %d 个信号，覆盖太窄" % seen


# ═══════════════ ⑧ I-05 + C-172 改一格 → 落盘一次 + .sv 预览变 ═══════════════
@pytest.mark.contract("C-172")
def test_i05_c172_edit_one_cell_puts_edit_once_and_sv_preview_follows(win, qapp, monkeypatch):
    """I-05 + C-172：改一格期望 → `state.put_edit` **恰好一次** + .sv 预览随之变。

    面板的 `_persist` 只在 `colsChanged` / `dataChanged` 上写一次，`state.suspend_persist()`
    负责把批量动作收成一次 —— 单格编辑本来就只该有一次。
    """
    w = win
    _load(w)
    name = _first_editable(w)
    _select(w, name)
    tp, m = w.truth_panel, w.truth_panel.model
    w.main_view.set_tab("sv")
    qapp.processEvents()
    sv_before = w.sv_preview.text()
    w.main_view.set_tab("truth")
    qapp.processEvents()

    calls = []
    real = type(w.state).put_edit
    monkeypatch.setattr(type(w.state), "put_edit",
                        lambda self, nm, rec, view_id=None: (
                            calls.append(nm), real(self, nm, rec, view_id))[1])

    exp_r = m.rowCount() - 1
    c = next((j for j in range(m.columnCount())
              if m.flags(m.index(exp_r, j)) & QtCore.Qt.ItemIsEditable), None)
    assert c is not None
    cur = m.data(m.index(exp_r, c), int(TR.RAW_VALUE))
    w_bits = int((m.cols()[c].get("auto_w") or 1))
    new = (int(cur or 0) + 1) & ((1 << w_bits) - 1)
    assert m.setData(m.index(exp_r, c), "%d" % new, QtCore.Qt.EditRole)
    qapp.processEvents()

    assert calls == [name], "改一格写了 %d 次编辑记录（I-05 要求 1 次）：%s" % (len(calls), calls)
    assert (w.state.edit_of(name) or {}).get("cols"), "编辑记录里没有列模型"

    w.main_view.set_tab("sv")
    qapp.processEvents()
    assert w.sv_preview.text() != sv_before, "改了期望，.sv 预览没跟着变（C-172）"
    assert tp.model.fill_progress()[0] >= 1
