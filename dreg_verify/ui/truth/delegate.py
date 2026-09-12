# -*- coding: utf-8 -*-
"""truth/delegate.py —— 真值表格子的画法与编辑器（C3-b，架构 §6.13）。

**这个模块是真值表里唯一"算颜色"的地方**，而且它也不算——只按 model 给的 role 到
`theme` 里查表：

    TruthRole.CELL_STATE   → theme.CELL_STATES[state] = (底, 边框, 字, 线型)
    TruthRole.IS_CURRENT_COL → 叠 theme.COL_BG_CURRENT（C-107）
    TruthRole.COL_STATE    → 反例列被选中时换更深的一档琥珀（C-107）
    TruthRole.IS_FALLBACK  → 灰斜体（这格显示的是 auto_out 兜底值，不是 designer 填的）
    TruthRole.ROW_KIND     → 期望行在当前列上多描 2px outline（Design §1.4）

`view.py` 与 `TruthHeaderView` 都从这里取查表（`CELL_FILL` / `HEADER_COLORS`），
免得同一个颜色决定在两处各写一遍、改一处漏一处。

编辑器（C-082/C-083）：`QLineEdit` + **即时**校验。写法认不出来就把边框标红，但
**绝不阻止提交**——真正的判决在 `TruthModel.setData`：它 `parse_int` 失败就发
`parseFailed` 并 return False，Qt 自动把这一格还原成旧值。校验器只负责"边敲边告诉你
这样写读不出来"，不负责拦人（拦人 = 用户改到一半被锁死在编辑器里出不来）。
"""

from PySide6 import QtCore, QtGui, QtWidgets

from ... import truth_edit as TE
from .. import contracts as CT
from .. import names as N
from .. import theme as TH
from ..widgets import mono_font as _mono_font

Qt = QtCore.Qt
TR = CT.TruthRole
CS = CT.TruthColState
RK = CT.TruthRowKind

__all__ = ["TruthDelegate", "CELL_FILL", "HEADER_COLORS", "NEG_CURRENT_BG",
           "cell_colors", "header_colors"]


#: ⚠ C3-int 起本模块**不再有 `PENDING_NAMES` 影子表**：编辑器的 objectName 在
#: `ui/names.py`（`N.TRUTH_CELL_EDITOR`）。

#: 反例列**被选中**时的底色（C-107「更深的琥珀」）。
#: theme 的琥珀家族里 `AMBER_BG`(#fdf3e0) 是常态底、`AMBER_BORDER`(#e6c98a) 是更深的一档；
#: 视图不许自己调色（不许 `QColor.darker()` / 不许写裸十六进制），所以取 theme 已有的深一档。
NEG_CURRENT_BG = TH.AMBER_BORDER

#: **中性态**（这一格自己没有语义颜色）——只有它们才吃列级底色（当前列淡蓝 / 反例列浅琥珀）。
#: `match/diff/neg/dft` 的底色是这张表的**信息主体**（审过没有、跟表达式一致不一致、
#: 是不是反例），被列级高亮盖掉就等于丢信息，所以一律保留自己的底。
NEUTRAL_STATES = frozenset(("unfilled", "readonly", "editable"))

#: 当前列叠色（C-107）：中性态 → 淡蓝；其余 → 保持原底。
CELL_FILL = {s: (TH.COL_BG_CURRENT if s in NEUTRAL_STATES else None)
             for s in TH.CELL_STATES}

#: 列级底色（Design §1.4 colBg）：非当前列时，中性态的格子按列状态染一层很浅的底。
COL_TINT = {
    CS.AUTO: None,
    CS.USER: None,
    CS.NEG: TH.COL_BG_NEG,
    CS.DFT: TH.COL_BG_IDDQ,
}

#: 列头底 / 字（Design §1.4）。键 = (列状态, 是不是当前列)。
HEADER_COLORS = {
    (CS.AUTO, False): (TH.COL_HDR_BG, TH.COL_HDR_FG),
    (CS.USER, False): (TH.COL_HDR_BG, TH.USER_COL_HEADER_FG),
    (CS.NEG, False): (TH.COL_HDR_NEG_BG, TH.COL_HDR_NEG_FG),
    (CS.DFT, False): (TH.COL_HDR_IDDQ_BG, TH.COL_HDR_IDDQ_FG),
    (CS.AUTO, True): (TH.COL_HDR_CURRENT_BG, TH.COL_HDR_CURRENT_FG),
    (CS.USER, True): (TH.COL_HDR_CURRENT_BG, TH.USER_COL_HEADER_FG),
    (CS.NEG, True): (NEG_CURRENT_BG, TH.COL_HDR_NEG_FG),
    (CS.DFT, True): (TH.COL_HDR_CURRENT_BG, TH.COL_HDR_IDDQ_FG),
}


def cell_colors(state, is_current, col_state=None):
    """(底, 边框, 字, 线型) —— 一格最终用的四样，全部来自 `theme` 的查表。

    `state` = `theme.CELL_STATES` 的键（model 的 `TruthRole.CELL_STATE`）；
    `is_current` = 这一格在当前高亮列上；`col_state` = `contracts.TruthColState`。
    """
    bg, border, fg, line = TH.CELL_STATES.get(state, TH.CELL_STATES["readonly"])
    if is_current:
        if col_state == CS.NEG or state == "neg":
            bg = NEG_CURRENT_BG                       # C-107 反例列被选中 → 更深琥珀
        else:
            bg = CELL_FILL.get(state) or bg
    elif state in NEUTRAL_STATES:
        bg = COL_TINT.get(col_state) or bg
    return bg, border, fg, line


def header_colors(col_state, is_current):
    """列头 (底, 字)。列状态认不出来就按自动列走（永远给得出一对颜色，不让表头画空）。"""
    key = (col_state if col_state in (CS.AUTO, CS.USER, CS.NEG, CS.DFT) else CS.AUTO,
           bool(is_current))
    return HEADER_COLORS[key]


class _CellEditor(QtWidgets.QLineEdit):
    """单元格编辑器：边敲边用 `truth_edit.parse_int` 试读，读不出来就标红边框。

    **不拦提交**（没有 `QValidator`）：`parse_int` 认 8 种写法，用户敲到一半必然有一瞬间
    是非法的（`0x` / `16'h`）；拿 validator 拦会让人连退格都退不出来。红框只是"这样写我读
    不出来"的提示，最终判决在 `TruthModel.setData`（失败 → `parseFailed` + 还原，C-083）。
    """

    #: 校验失败时的边框（颜色取 theme，不写裸十六进制）
    BAD_QSS = "QLineEdit{border:2px solid %s;background:%s;}" % (TH.BAD_FG, TH.WHITE)
    OK_QSS = "QLineEdit{border:1px solid %s;background:%s;}" % (TH.BLUE, TH.WHITE)

    def __init__(self, parent=None):
        QtWidgets.QLineEdit.__init__(self, parent)
        self.setObjectName(N.TRUTH_CELL_EDITOR)
        self.setFont(_mono_font(TH.FS_MONO))
        self.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.setFrame(True)
        self._bad = False
        self.textChanged.connect(self._revalidate)
        self._revalidate(self.text())

    def is_invalid(self):
        """当前文本读不读得出来（测试与面板都按这个问，不去猜 styleSheet）。"""
        return self._bad

    def _revalidate(self, text):
        bad = False
        if str(text).strip() != "":          # 空串 = 清空期望，是合法输入，不标红
            try:
                TE.parse_int(text)
            except ValueError:
                bad = True
        if bad == self._bad and self.styleSheet():
            return
        self._bad = bad
        self.setStyleSheet(self.BAD_QSS if bad else self.OK_QSS)


class TruthDelegate(QtWidgets.QStyledItemDelegate):
    """真值表格子的画法 + 编辑器（TRUTH_VIEW / C-107 / C-083）。

    自绘而不是靠 `QStyledItemDelegate.paint` + `BackgroundRole`，是因为这张表要的三样
    默认 delegate 给不了：① `unfilled` 的**虚线**边框（Design：待手填的格子是虚框）；
    ② 当前列在期望格上的 **2px outline**；③ 兜底值的灰斜体要连同底色一起判。
    """

    def __init__(self, parent=None):
        QtWidgets.QStyledItemDelegate.__init__(self, parent)

    # ── 画 ──
    def paint(self, painter, option, index):
        state = index.data(int(TR.CELL_STATE)) or "readonly"
        cur = bool(index.data(int(TR.IS_CURRENT_COL)))
        col_state = index.data(int(TR.COL_STATE))
        bg, border, fg, line = cell_colors(state, cur, col_state)
        selected = bool(option.state & QtWidgets.QStyle.State_Selected)

        painter.save()
        painter.fillRect(option.rect, QtGui.QColor(TH.SELECTED_BG if selected else bg))

        pen = QtGui.QPen(QtGui.QColor(border), 1)
        if line == "dashed":
            pen.setStyle(Qt.DashLine)                 # 待手填 = 虚框（Design §1.4）
        painter.setPen(pen)
        painter.drawRect(option.rect.adjusted(0, 0, -1, -1))

        if cur and index.data(int(TR.ROW_KIND)) == RK.EXP:
            # C-107：当前列的期望格多描 2px —— 30 列横滚时"我在哪一列的期望上"必须一眼看见
            painter.setPen(QtGui.QPen(QtGui.QColor(TH.CURRENT_COL_OUTLINE), 2))
            painter.drawRect(option.rect.adjusted(1, 1, -2, -2))

        text = str(index.data(Qt.DisplayRole) or "")
        if text:
            fallback = bool(index.data(int(TR.IS_FALLBACK)))
            font = _mono_font(TH.FS_MONO)
            if fallback:
                font.setItalic(True)                  # 兜底值 = 灰斜体（不是 designer 填的）
            painter.setFont(font)
            painter.setPen(QtGui.QColor(TH.DISABLED_TEXT if fallback else fg))
            painter.drawText(option.rect.adjusted(4, 0, -6, 0),
                             int(Qt.AlignRight | Qt.AlignVCenter), text)
        painter.restore()

    def sizeHint(self, option, index):
        """按【等宽字体下这一格的文字】给宽度，下限 `TRUTH_COL_W`。

        1 位的列压到 34px、`0x1F3` 的列自己撑开——`TruthTableView` 的"新插列自适应"
        （C-108）靠的就是这个；固定回 `TRUTH_COL_W` 的话"自适应"就名存实亡了。
        """
        text = str(index.data(Qt.DisplayRole) or "")
        w = QtGui.QFontMetrics(_mono_font(TH.FS_MONO)).horizontalAdvance(text) + 12
        return QtCore.QSize(max(TH.TRUTH_COL_W, w), TH.TRUTH_ROW_H)

    # ── 编辑 ──
    def createEditor(self, parent, option, index):
        return _CellEditor(parent)

    def setEditorData(self, editor, index):
        if isinstance(editor, QtWidgets.QLineEdit):
            editor.setText(str(index.data(Qt.EditRole) or ""))
            editor.selectAll()
            return
        QtWidgets.QStyledItemDelegate.setEditorData(self, editor, index)   # pragma: no cover

    def setModelData(self, editor, model, index):
        """原样交给 `setData` —— 解析、还原、发 `parseFailed` 全是 model 的事（唯一写入口）。"""
        if isinstance(editor, QtWidgets.QLineEdit):
            model.setData(index, editor.text(), Qt.EditRole)
            return
        QtWidgets.QStyledItemDelegate.setModelData(self, editor, model, index)  # pragma: no cover
