# -*- coding: utf-8 -*-
"""test_ui_truth_view.py —— 真值表视图层（C3-b）：`ui/truth/view.py` + `delegate.py`。

**真键鼠**：一律走 `ui_harness` 的 `click_cell / keys / paste / type_text`，或自己造
`QMouseEvent` / `QContextMenuEvent` 往 `viewport()` 发（offscreen 下 viewport 才是事件
目标）——不直接调槽、不直接 `emit`。夹具走 `ui_harness.mirror_path`（公开仓，**不出现
真实信号名**）。

覆盖 TRUTH_VIEW 9 条：C-063 C-074 C-075 C-107 C-108 C-109 C-141 C-258 C-293 C-294 C-295
（+ 视图顺带经手的 C-083 编辑器红框 / C-091 双击列头改名 / C-296 右键 / C-299 一步撤销）。

PySide6 6.11 的两个坑（踩过，别回退）：
  · `QTest.keyClicks` 发 `\\n` 或非 ASCII 会**整进程退出 127** → 回车用 `H.keys(w, "Return")`，
    多行文本用 `H.paste`（走剪贴板）；
  · 打开编辑器后要 `QTest.qWait` 一拍，`app.focusWidget()` 才是那个 `QLineEdit`。
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import ui_harness as H                                   # noqa: E402
from dreg_verify import edits as ED                      # noqa: E402
from dreg_verify import excel_model as M                 # noqa: E402
from dreg_verify import providers as PV                  # noqa: E402

pytest.importorskip("PySide6")

from PySide6 import QtCore, QtGui, QtWidgets             # noqa: E402
from PySide6.QtTest import QTest                         # noqa: E402

from dreg_verify.ui import contracts as CT               # noqa: E402
from dreg_verify.ui import names as N                    # noqa: E402
from dreg_verify.ui import terms as T                    # noqa: E402
from dreg_verify.ui import theme as TH                   # noqa: E402
from dreg_verify.ui.truth import TruthModel, e_inputs_from_an   # noqa: E402
from dreg_verify.ui.truth import delegate as D           # noqa: E402
from dreg_verify.ui.truth import view as V               # noqa: E402

Qt = QtCore.Qt
TR = CT.TruthRole

#: mirror 上的对照信号（与 `test_ui_truth_model.py` 同一批，probe 过）
LOGIC_SIG = "d_logic_bt_lp_rx_en"          # btlp：4 条可编辑输入行 + 4 列，最小够用
GUESS_SIG = "d_bt_rx_slna_1st_bias_trim_gain_cal_wl"   # wl：19 行猜名 + 1 行 iddq 门 + dft 拍列


class _Cfg(object):
    """`ConfigSourceProto` 的最小实现（与 `test_ui_truth_model.py` 的同名替身同形）。"""

    def __init__(self, wb):
        self.wb = wb
        self.probe_prefixes = {}
        self.force_signals = set()
        self.logic_overrides = {}
        self.include_risky = True


@pytest.fixture(scope="module")
def qapp():
    return H.app()


@pytest.fixture(scope="module")
def btlp(qapp):
    return M.load_workbook(H.mirror_path("btlp"))


@pytest.fixture(scope="module")
def wl(qapp):
    return M.load_workbook(H.mirror_path("wl"))


def _fresh(wb, name):
    """(model, an, e_inputs) —— 载好一个信号的真值表（列 = 出厂默认）。"""
    an = PV.TopoutProvider(_Cfg(wb)).analyze(name, "min", 64, False)
    assert an is not None, "mirror 上没有信号 %s（夹具挑错了）" % name
    ei = e_inputs_from_an(an)
    m = TruthModel()
    m.load(an, ED.cols_from_vectors(an, ei), ei)
    return m, an, ei


class _Rig(object):
    """冻结列 + 网格并排的最小宿主（面板 C3-c 里会是 splitter，这里只要能收真事件）。"""

    def __init__(self, model, an, e_inputs, size=(980, 320)):
        self.model = model
        self.host = QtWidgets.QWidget()
        lay = QtWidgets.QHBoxLayout(self.host)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.frozen = V.FrozenNamesView(self.host)
        self.grid = V.TruthTableView(self.host)
        self.grid.setModel(model)
        self.frozen.set_source(model, an, e_inputs)
        lay.addWidget(self.frozen)
        lay.addWidget(self.grid, 1)
        self.host.resize(*size)
        self.host.show()
        H.app().processEvents()
        self.guard = V.bind_frozen(self.frozen, self.grid)

    def close(self):
        self.host.close()
        H.app().processEvents()


@pytest.fixture
def rig(btlp):
    m, an, ei = _fresh(btlp, LOGIC_SIG)
    r = _Rig(m, an, ei)
    yield r
    r.close()


def _dump(m):
    """整表逐格快照（撤销后逐格回到原样的比对基准）。"""
    return ([[m.data(m.index(r, c)) for c in range(m.columnCount())]
             for r in range(m.rowCount())], m.all_names())


def _exp_row(m):
    return m.rowCount() - 1


def _drag(view, r0, c0, r1, c1):
    """真鼠标拖：按下 (r0,c0) → 移到 (r1,c1) → 松开。

    `QTest.mouseMove` 在 Qt6 里**不带 buttons 状态**，拖不出橡皮筋选区（A5 spike 踩过），
    所以自己造 `QMouseEvent` —— 仍然走 widget 的公共事件入口，不是调内部方法。
    """
    vp = view.viewport()
    m = view.model()
    p0 = QtCore.QPointF(view.visualRect(m.index(r0, c0)).center())
    p1 = QtCore.QPointF(view.visualRect(m.index(r1, c1)).center())
    g0 = QtCore.QPointF(vp.mapToGlobal(p0.toPoint()))
    g1 = QtCore.QPointF(vp.mapToGlobal(p1.toPoint()))
    app = H.app()
    for typ, pos, gpos, btn, btns in (
            (QtCore.QEvent.MouseButtonPress, p0, g0, Qt.LeftButton, Qt.LeftButton),
            (QtCore.QEvent.MouseMove, p1, g1, Qt.NoButton, Qt.LeftButton),
            (QtCore.QEvent.MouseButtonRelease, p1, g1, Qt.LeftButton, Qt.NoButton)):
        app.sendEvent(vp, QtGui.QMouseEvent(typ, pos, gpos, btn, btns, Qt.NoModifier))
        app.processEvents()


def _key(widget, key, mod=Qt.NoModifier):
    """往 widget 发一记真按键，**不动焦点**。

    `H.keys` 会先 `setFocus()`——编辑器开着的时候那一下就把编辑器关了，
    "编辑中不触发 Ctrl+D"（C-109）根本测不到（状态早就不是 EditingState 了）。
    """
    QTest.keyClick(widget, key, mod)
    H.app().processEvents()


def _open_editor(view, row, col):
    """真键鼠打开一格的编辑器，返回那个 `QLineEdit`（等一拍 focusWidget 才换过去）。"""
    H.click_cell(view, row, col)
    H.keys(view, "F2")
    QTest.qWait(20)
    ed = H.app().focusWidget()
    assert isinstance(ed, QtWidgets.QLineEdit), "F2 没打开编辑器，拿到的是 %r" % (ed,)
    return ed


def _pix(view, row, col):
    """把网格 viewport 渲染成图，取 (row,col) 格左侧的底色（避开右对齐的数字与边框）。"""
    img = view.viewport().grab().toImage()
    rect = view.visualRect(view.model().index(row, col))
    assert rect.isValid() and rect.width() > 8, "格子 (%d,%d) 不可见，先滚到可见" % (row, col)
    return QtGui.QColor(img.pixel(rect.left() + 5, rect.center().y())).name()


# ═══════════════════ C-141 实现路线 ═══════════════════
@pytest.mark.contract("C-141")
def test_c141_native_qtableview_no_webengine(rig):
    """C-141：真值表走 QTableView 原生路线（拍板 #2）——进程里连 WebEngine 都没 import。"""
    assert "PySide6.QtWebEngineWidgets" not in sys.modules
    assert "PySide6.QtWebEngineCore" not in sys.modules
    assert issubclass(V.TruthTableView, QtWidgets.QTableView)
    assert issubclass(V.FrozenNamesView, QtWidgets.QTableView)
    assert issubclass(V.NameModel, QtCore.QAbstractTableModel)
    assert issubclass(D.TruthDelegate, QtWidgets.QStyledItemDelegate)
    assert isinstance(rig.grid.itemDelegate(), D.TruthDelegate)
    assert rig.grid.objectName() == N.TRUTH_GRID_VIEW
    assert rig.frozen.objectName() == N.TRUTH_NAMES_VIEW
    for mod in (V, D):
        src = open(mod.__file__, encoding="utf-8").read()
        assert "WebEngine" not in src, "%s 里出现了 WebEngine" % mod.__name__


# ═══════════════════ C-293 矩形选区 ═══════════════════
@pytest.mark.contract("C-293")
def test_c293_drag_rect_selection(rig):
    """C-293：鼠标拖出来的是一片**矩形**多格选区（像 Excel），并汇报给提示条。"""
    assert rig.grid.selectionBehavior() == QtWidgets.QAbstractItemView.SelectItems
    assert rig.grid.selectionMode() == QtWidgets.QAbstractItemView.ExtendedSelection

    seen = []
    rig.grid.selectionSummaryChanged.connect(lambda col, n: seen.append((col, n)))

    _drag(rig.grid, 0, 0, 2, 2)
    got = sorted((ix.row(), ix.column()) for ix in rig.grid.selectedIndexes())
    assert got == [(r, c) for r in range(3) for c in range(3)], got
    assert seen and seen[-1][1] == 9          # 提示条拿到的格数
    assert seen[-1][0] == ""                  # 跨了三列 → 不报单列名

    _drag(rig.grid, 0, 1, 3, 1)               # 竖着拖一列 → 列名报得出来
    got = sorted((ix.row(), ix.column()) for ix in rig.grid.selectedIndexes())
    assert got == [(r, 1) for r in range(4)], got
    assert seen[-1] == (rig.model.all_names()[1], 4)


# ═══════════════════ C-294 粘贴 ═══════════════════
@pytest.mark.contract("C-294", "C-299")
def test_c294_paste_lands_on_active_cell_appends_cols_one_undo(rig):
    """C-294：TSV 落在**活动格**；超出列数自动追加列；整次粘贴**一步**撤销（C-299）。"""
    m = rig.model
    before_cols = m.columnCount()
    before = _dump(m)
    before_undo = m.undo_stack().count()

    H.click_cell(rig.grid, 0, before_cols - 1)            # 活动格 = 最后一列
    assert rig.grid.currentIndex().column() == before_cols - 1
    H.paste(rig.grid, "1\t0\n0\t1\n1\t1")                 # 3 行 × 2 列 → 要多一列

    assert m.columnCount() == before_cols + 1
    assert m.undo_stack().count() == before_undo + 1       # 追加列 + 落值 = 一步
    assert m.data(m.index(0, before_cols - 1)) == "1"      # 真落在活动格上
    assert m.data(m.index(2, before_cols)) == "1"

    m.undo_stack().undo()
    assert m.columnCount() == before_cols
    assert _dump(m) == before                              # 逐格回到原样


# ═══════════════════ C-295 冻结列同步 ═══════════════════
@pytest.mark.contract("C-295")
def test_c295_frozen_names_scroll_sync(btlp):
    """C-295：冻结信号名列与网格三项同步——纵向滚动、行高、当前行。60 列横滚也不丢左列。"""
    m, an, ei = _fresh(btlp, LOGIC_SIG)
    m.append_test_column(60 - m.columnCount())
    assert m.columnCount() == 60
    rig = _Rig(m, an, ei, size=(700, 120))
    try:
        rig.frozen.setFixedHeight(110)
        rig.grid.setFixedHeight(110)
        H.app().processEvents()

        gb = rig.grid.verticalScrollBar()
        fb = rig.frozen.verticalScrollBar()
        assert gb.maximum() > 0, "没滚起来，这条测不到同步（把宿主再压矮点）"

        gb.setValue(gb.maximum())
        H.app().processEvents()
        assert fb.value() == gb.value()                    # 网格滚 → 左列跟

        gb.setValue(0)
        H.app().processEvents()
        assert fb.value() == gb.value() == 0               # 回环也不会弹住

        # 行高同步：拖网格的行高，左列跟着变（两边行必须一直对齐，差一格就读错信号）
        assert [rig.grid.rowHeight(r) for r in range(m.rowCount())] \
            == [rig.frozen.rowHeight(r) for r in range(m.rowCount())]
        rig.grid.verticalHeader().resizeSection(1, 44)
        H.app().processEvents()
        assert rig.frozen.rowHeight(1) == 44

        # 当前行同步：点网格第 3 行 → 左列选中第 3 行
        H.click_cell(rig.grid, 3, 0)
        H.app().processEvents()
        assert rig.frozen.currentIndex().row() == 3

        # 横滚 60 列不动左列（冻结列没有横向滚动条）
        assert rig.frozen.horizontalScrollBarPolicy() == Qt.ScrollBarAlwaysOff
        hb = rig.grid.horizontalScrollBar()
        hb.setValue(hb.maximum())
        H.app().processEvents()
        assert rig.frozen.horizontalScrollBar().value() == 0
        assert rig.frozen.model().data(rig.frozen.model().index(0, 0)) == m.row_label(0)
    finally:
        rig.close()


# ═══════════════════ C-107 当前列高亮 ═══════════════════
@pytest.mark.contract("C-107")
def test_c107_current_col_highlight_roles(rig):
    """C-107：点一格 → 该列 `IS_CURRENT_COL` 为真、别列为假；delegate 真的画成了淡蓝。

    像素比对是为了钉住「视图不算色」：底色必须**等于** theme 的常量，不是差不多的蓝。
    """
    m = rig.model
    H.click_cell(rig.grid, 0, 1)
    H.app().processEvents()

    for r in range(m.rowCount()):
        assert m.data(m.index(r, 1), int(TR.IS_CURRENT_COL)) is True
        assert m.data(m.index(r, 0), int(TR.IS_CURRENT_COL)) is False
    assert m.headerData(1, Qt.Horizontal, int(TR.IS_CURRENT_COL)) is True

    # 取一个没被选中的输入格（选区底色是另一件事，别把两者混在一格上比）
    assert _pix(rig.grid, 2, 1) == TH.COL_BG_CURRENT
    assert _pix(rig.grid, 2, 0) == TH.CELL_STATES["editable"][0]

    # 反例列被选中 → 更深的琥珀（C-107 后半句）
    n_made, _skipped = m.add_negatives([0])
    assert n_made == 1
    neg_c = m.columnCount() - 1
    assert m.col_state(neg_c) == CT.TruthColState.NEG
    H.click_cell(rig.grid, 0, neg_c)
    H.app().processEvents()
    assert _pix(rig.grid, 2, neg_c) == D.NEG_CURRENT_BG
    assert D.NEG_CURRENT_BG != TH.COL_BG_CURRENT

    H.click_cell(rig.grid, 0, 1)                      # 换回正向列 → 反例列退回浅琥珀
    H.app().processEvents()
    assert _pix(rig.grid, 2, neg_c) == TH.COL_BG_NEG
    H.shot(rig.host, "truth_view_current_col")


# ═══════════════════ C-108 列宽 ═══════════════════
@pytest.mark.contract("C-108")
def test_c108_column_width_kept_after_edit_and_insert(rig):
    """C-108：手拖过的列宽，编辑一格不弹回、插新列也不弹回；只有**新列**自己适应一次。"""
    m = rig.model
    n0 = m.columnCount()
    for c in range(n0):
        rig.grid.setColumnWidth(c, 120)
    H.app().processEvents()

    # 真键鼠改一格期望值 → 老列宽一个都不许动
    exp_r = _exp_row(m)
    ed = _open_editor(rig.grid, exp_r, 0)
    H.type_text(ed, "1")
    H.keys(ed, "Return")
    H.app().processEvents()
    assert m.data(m.index(exp_r, 0), int(TR.IS_FALLBACK)) is False, "这一格没改成功，测不到列宽"
    assert [rig.grid.columnWidth(c) for c in range(n0)] == [120] * n0

    # 插一列：老列不动，新列自适应
    m.append_test_column(1)
    H.app().processEvents()
    assert m.columnCount() == n0 + 1
    assert [rig.grid.columnWidth(c) for c in range(n0)] == [120] * n0
    new_w = rig.grid.columnWidth(n0)
    assert new_w != 120 and new_w >= TH.TRUTH_COL_W


@pytest.mark.contract("C-108")
def test_c108_reload_signal_restores_auto_fit(btlp, rig):
    """C-108 后半句：**换信号**（`load` = 唯一 reset）才把列宽恢复成自适应。"""
    m = rig.model
    for c in range(m.columnCount()):
        rig.grid.setColumnWidth(c, 120)
    H.app().processEvents()

    an2 = PV.TopoutProvider(_Cfg(btlp)).analyze(LOGIC_SIG, "max", 64, False)
    ei2 = e_inputs_from_an(an2)
    m.load(an2, ED.cols_from_vectors(an2, ei2), ei2)
    H.app().processEvents()
    widths = [rig.grid.columnWidth(c) for c in range(m.columnCount())]
    assert widths and 120 not in widths, "换信号后列宽该回到自适应，实测 %r" % widths
    assert min(widths) >= TH.TRUTH_COL_W


# ═══════════════════ C-109 / C-258 Ctrl+D ═══════════════════
@pytest.mark.contract("C-109", "C-258")
def test_c109_c258_ctrl_d_not_while_editing(rig):
    """C-109/C-258：Ctrl+D 复制当前列；**单元格正在编辑时不触发**（不打断输入）。"""
    hits = []
    rig.grid.copyColumnRequested.connect(hits.append)

    H.click_cell(rig.grid, 0, 1)
    _key(rig.grid, Qt.Key_D, Qt.ControlModifier)
    assert hits == [1], "非编辑态 Ctrl+D 没发出 copyColumnRequested：%r" % (hits,)

    ed = _open_editor(rig.grid, _exp_row(rig.model), 2)
    H.type_text(ed, "1")                       # 正在输入：这一下更不能被打断
    assert rig.grid.state() == QtWidgets.QAbstractItemView.EditingState
    _key(rig.grid, Qt.Key_D, Qt.ControlModifier)
    assert hits == [1], "编辑中 Ctrl+D 不该触发（会打断输入）：%r" % (hits,)
    assert rig.grid.state() == QtWidgets.QAbstractItemView.EditingState
    assert ed.text() == "1", "编辑器里的内容被动过了"

    _key(ed, Qt.Key_Escape)
    assert rig.grid.state() != QtWidgets.QAbstractItemView.EditingState
    _key(rig.grid, Qt.Key_D, Qt.ControlModifier)
    assert hits == [1, 2], "退出编辑后又该正常触发了：%r" % (hits,)

    # 视图**不自己复制列**（C3-c 的面板才调 model），列数一个没变
    assert rig.model.columnCount() == 4


# ═══════════════════ C-074 / C-075 冻结列行标签 ═══════════════════
@pytest.mark.contract("C-074", "C-075")
def test_c074_c075_frozen_labels_and_tooltips(wl):
    """C-074：左列写**真实信号名**、控制位加粗、猜名行整行琥珀；C-075：悬停给类型/位宽/来源。"""
    m, an, ei = _fresh(wl, GUESS_SIG)
    rig = _Rig(m, an, ei)
    try:
        nm = rig.frozen.model()
        assert nm.rowCount() == m.rowCount()
        assert nm.headerData(0, Qt.Horizontal, Qt.DisplayRole) == T.TRUTH_FROZEN_HEADER

        guessed, control = [], []
        for r in range(m.rowCount()):
            ix = nm.index(r, 0)
            assert nm.data(ix, Qt.DisplayRole) == m.row_label(r)        # C-074 逐行同一份标签
            assert not (nm.flags(ix) & Qt.ItemIsEditable)               # 信号名只读
            tip = nm.data(ix, Qt.ToolTipRole)
            assert tip and T.scrub(tip) == tip, "C-075 tooltip 要么空、要么没过 scrub：%r" % tip
            if nm.data(ix, V.ROLE_GUESSED):
                guessed.append(r)
            if nm.data(ix, Qt.FontRole).bold() and nm.data(ix, int(TR.ROW_KIND)) == \
                    CT.TruthRowKind.INPUT:
                control.append(r)

        assert guessed, "这个夹具应当有猜名输入行（挑错信号了）"
        for r in guessed:                                               # 猜名行整行琥珀
            assert nm.data(nm.index(r, 0), Qt.ForegroundRole).name() == TH.AMBER_FG
        assert control, "这个夹具应当有控制/选择位行（挑错信号了）"

        # 非猜名、非控制的普通输入行既不琥珀也不加粗（否则「整行琥珀」就是恒真）
        plain = [r for r in range(len(ei)) if r not in guessed and r not in control]
        for r in plain:
            assert nm.data(nm.index(r, 0), Qt.ForegroundRole).name() != TH.AMBER_FG

        # C-075 的四样：位宽、类型、来源说明、猜名徽标
        tip0 = nm.data(nm.index(guessed[0], 0), Qt.ToolTipRole)
        assert m.row_label(guessed[0]).split("　")[0] in tip0
        assert T.SIDE_INPUTS_GUESS_BADGE in tip0
        assert str(nm.meta_at(guessed[0])["width"]) in tip0

        H.shot(rig.host, "truth_view_frozen_names")
    finally:
        rig.close()


# ═══════════════════ C-063 列头 tooltip ═══════════════════
@pytest.mark.contract("C-063")
def test_c063_header_tooltip_is_drive_tip(rig):
    """C-063：列头悬停给这一列实际要下的 force / RF_WRITE 明细（`TruthRole.DRIVE_TIP`）。"""
    hdr = rig.grid.horizontalHeader()
    assert isinstance(hdr, V.TruthHeaderView)

    for c in range(rig.model.columnCount()):
        tip = hdr.header_tooltip(c)
        drives = rig.model.column_drives(c)
        assert drives, "这个夹具的第 %d 列一条驱动都没有（挑错信号了）" % c
        assert tip.startswith(rig.model.all_names()[c])
        for line in drives:
            assert T.scrub(line) in tip
        assert T.scrub(tip) == tip                         # I-12：后端文本过 scrub

    assert hdr.header_tooltip(999) == ""                   # 越界不崩、不给假 tooltip

    # 真悬停事件走得通（QHeaderView 问 model 要 ToolTipRole 的那条路被我们接管了）
    x = hdr.sectionViewportPosition(1) + hdr.sectionSize(1) // 2
    pos = QtCore.QPoint(x, hdr.height() // 2)
    ev = QtGui.QHelpEvent(QtCore.QEvent.ToolTip, pos, hdr.viewport().mapToGlobal(pos))
    assert H.app().sendEvent(hdr.viewport(), ev) is True
    assert ev.isAccepted()


# ═══════════════════ C-083 编辑器红框 ═══════════════════
@pytest.mark.contract("C-083")
def test_c083_editor_red_frame_but_commit_restores(rig):
    """C-083：写法认不出来 → 编辑器**当场标红**但不拦提交；提交后 model 还原并发 `parseFailed`。"""
    m = rig.model
    failed = []
    m.parseFailed.connect(failed.append)
    exp_r = _exp_row(m)
    before = m.data(m.index(exp_r, 0))
    before_undo = m.undo_stack().count()

    ed = _open_editor(rig.grid, exp_r, 0)
    assert ed.objectName() == N.TRUTH_CELL_EDITOR
    assert not ed.is_invalid(), "刚打开就标红了？"

    H.type_text(ed, "zz")                       # 非 ASCII / 换行会崩进程，这里只打 ASCII
    H.app().processEvents()
    assert ed.is_invalid() is True
    assert TH.BAD_FG in ed.styleSheet()         # 红框（颜色取 theme，不是裸十六进制）

    H.keys(ed, "Return")                        # **不拦提交**：让 model 去判、去还原
    H.app().processEvents()
    assert failed, "认不出来的写法必须发 parseFailed（绝不静默吞成 0/未填）"
    assert m.data(m.index(exp_r, 0)) == before  # 这一格已还原
    assert m.undo_stack().count() == before_undo

    # 合法写法照常落值，而且不标红
    ed = _open_editor(rig.grid, exp_r, 0)
    H.type_text(ed, "0x1")
    assert ed.is_invalid() is False
    H.keys(ed, "Return")
    H.app().processEvents()
    assert m.data(m.index(exp_r, 0), int(TR.IS_FALLBACK)) is False
    assert m.undo_stack().count() == before_undo + 1


# ═══════════════════ C-296 / C-091 右键与改名（动作只发信号）═══════════════════
@pytest.mark.contract("C-296", "C-091")
def test_c296_context_menu_and_c091_header_double_click_only_emit(rig):
    """C-296 右键行/列、C-091 双击列头改名：视图只发信号，菜单与对话框都在面板。"""
    menus, renames = [], []
    rig.grid.contextMenuRequested.connect(lambda p, r, c: menus.append((r, c)))
    rig.grid.renameRequested.connect(renames.append)

    vp = rig.grid.viewport()
    pos = rig.grid.visualRect(rig.model.index(2, 1)).center()     # 真右键落在 viewport 上
    H.app().sendEvent(vp, QtGui.QContextMenuEvent(QtGui.QContextMenuEvent.Mouse, pos,
                                                  vp.mapToGlobal(pos)))
    assert menus == [(2, 1)], menus
    assert not rig.grid.findChildren(QtWidgets.QMenu), "菜单归面板，视图里不许弹"

    far = QtCore.QPoint(vp.width() - 2, vp.height() - 2)          # 空白处右键 → (-1, -1)
    if not rig.model.index(0, 0).isValid() or not rig.grid.indexAt(far).isValid():
        H.app().sendEvent(vp, QtGui.QContextMenuEvent(QtGui.QContextMenuEvent.Mouse, far,
                                                      vp.mapToGlobal(far)))
        assert menus[-1] == (-1, -1), menus

    hdr = rig.grid.horizontalHeader()
    x = hdr.sectionViewportPosition(2) + hdr.sectionSize(2) // 2
    QTest.mouseDClick(hdr.viewport(), Qt.LeftButton, Qt.NoModifier,
                      QtCore.QPoint(x, hdr.height() // 2))
    H.app().processEvents()
    assert renames == [2], renames
    assert rig.model.all_names() == ["T0", "T1", "T2", "T3"], "视图不许自己改名"


# ═══════════════════ Delete 清空期望（C-081 的键位面，一步撤销）═══════════════════
@pytest.mark.contract("C-081", "C-299")
def test_delete_clears_selected_expected_cells_in_one_undo(rig):
    """Delete 清空选中的期望格：只清真填过的、只清期望行，整批一步撤销。"""
    m = rig.model
    exp_r = _exp_row(m)
    assert m.fill_expected() > 0                  # 先把期望填满，才有东西可清
    before = _dump(m)
    before_undo = m.undo_stack().count()

    _drag(rig.grid, exp_r, 0, exp_r, 2)           # 真拖出三格期望
    assert len(rig.grid.selectedIndexes()) == 3
    H.keys(rig.grid, "Delete")
    H.app().processEvents()

    for c in range(3):
        assert m.data(m.index(exp_r, c), int(TR.IS_FALLBACK)) is True
    assert m.data(m.index(exp_r, 3), int(TR.IS_FALLBACK)) is False   # 没选中的不动
    assert m.undo_stack().count() == before_undo + 1

    m.undo_stack().undo()
    assert _dump(m) == before

    # 选的是只读的 auto 行 → 什么都不该发生（也不许占一步撤销）
    n = m.undo_stack().count()
    _drag(rig.grid, m.rowCount() - 2, 0, m.rowCount() - 2, 2)
    H.keys(rig.grid, "Delete")
    H.app().processEvents()
    assert m.undo_stack().count() == n
    assert _dump(m) == before


# ═══════════════════ Ctrl+C / Ctrl+Z / Ctrl+Y ═══════════════════
@pytest.mark.contract("C-293", "C-299")
def test_ctrl_c_copies_selection_and_ctrl_z_y_walk_undo_stack(rig):
    """Ctrl+C 把选区按外接矩形写进剪贴板；Ctrl+Z/Y 走 model 自己的撤销栈。"""
    m = rig.model
    _drag(rig.grid, 0, 0, 1, 1)
    H.keys(rig.grid, "Ctrl+C")
    H.app().processEvents()
    text = QtWidgets.QApplication.clipboard().text()
    assert text == m.copy_tsv(rig.grid.selectedIndexes())
    assert text.count("\n") == 1 and text.count("\t") == 2

    before = _dump(m)
    assert m.append_test_column(1)
    assert m.columnCount() == 5
    H.keys(rig.grid, "Ctrl+Z")
    H.app().processEvents()
    assert m.columnCount() == 4 and _dump(m) == before
    H.keys(rig.grid, "Ctrl+Y")
    H.app().processEvents()
    assert m.columnCount() == 5
    H.shot(rig.host, "truth_view_plain")
