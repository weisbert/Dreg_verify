# -*- coding: utf-8 -*-
"""truth/view.py —— 真值表的**视图层**（C3-b，架构 §6.13，拍板 #2 = QTableView 原生）。

三个类，各自只做一件事：

    TruthTableView(QTableView)   网格。键位 / 选区 / 当前列 / 列宽 / 列头 tooltip；
                                 一切"动作"只发信号，由 `truth/panel.py`（C3-c）转 model。
    NameModel(QAbstractTableModel) 冻结左列的一列只读 model（行标签 / 猜名 / 控制行 / tooltip）。
    FrozenNamesView(QTableView)  冻结左列视图；`bind_frozen(frozen, grid)` 做滚动 / 行高 /
                                 当前行三项同步（C-295）。

本模块**不认识 model 的实现类**——只按 `contracts.TruthModelProto` 的方法名与
`contracts.TruthRole` 的 role 取值（`TruthModel` 换成别的实现也照跑）。颜色一律经
`delegate.py` 的查表拿，视图自己一个十六进制都不写。

两条 C3-b 必须守住的线（`tests/test_ui_truth_view.py` 钉死）：

1. **视图不改数据**。Ctrl+D 不调 `duplicate_column`，只发 `copyColumnRequested(c)`；
   右键不弹菜单，只发 `contextMenuRequested`；双击列头不弹改名框，只发 `renameRequested(c)`。
   菜单与对话框都在面板——视图里一旦开始"顺手做掉"，面板的撤销/持久化就会被绕过。
2. **列宽只在两处动**（C-108）：新插入的列 `columnsInserted` 自适应一次、换信号
   `modelReset` 全表自适应一次。除此之外任何刷新都不许碰列宽（用户拖过的宽度被弹回是
   v1 `_populate_truth()` 的老毛病，根子在那句 `tbl.clear()`——v2 的 model 不 reset，
   视图这边也不许自己补一刀）。
"""

from PySide6 import QtCore, QtGui, QtWidgets

from ... import inputs_table as IT
from .. import contracts as CT
from .. import names as N
from .. import terms as T
from .. import theme as TH
from ..widgets import mono_font as _mono_font, ui_font as _ui_font
from .delegate import TruthDelegate, header_colors

Qt = QtCore.Qt
TR = CT.TruthRole
RK = CT.TruthRowKind

__all__ = ["TruthTableView", "TruthHeaderView", "NameModel", "FrozenNamesView",
           "bind_frozen", "ROLE_GUESSED", "ROLE_NEEDS_PREFIX",
           "PENDING_NAMES", "PENDING_TERMS", "MODEL_REQUIREMENTS"]


#: `ui/names.py` 里还没有、本波要用的 objectName（C3-int 搬进 `names.py` 并删掉这张表）。
PENDING_NAMES = {
    "TRUTH_GRID_HEADER": "truth_grid_header",      # 网格列头（自绘：当前列 / 反例 / iddq 三种底）
    "TRUTH_NAMES_HEADER": "truth_names_header",    # 冻结列表头
}

#: `ui/terms.py` 里还没有、本波要用的文案（C3-int 搬进 `terms.py` 并删掉这张表）。
#: 取值一律经 `_t()`：`terms` 里有就用 `terms` 的，搬完自动一致。
PENDING_TERMS = {
    # C-063 列头悬停：这一列实际要下的 force / RF_WRITE
    "TRUTH_HEADER_TIP_FMT": "{col}\n{drives}",
    "TRUTH_HEADER_TIP_NONE": "这一列不用下 force / RF_WRITE",
    # C-075 行标签悬停：位宽 + 怎么把值下进去（角色 · 端口已经内联在行标签里了）
    "TRUTH_ROW_TIP_FMT": "{label}\n位宽 {width} 位 · {rw}",
    "TRUTH_ROW_TIP_AUTO": "程序按表达式算的值，只读参考",
    "TRUTH_ROW_TIP_EXP": "designer 手填的期望，进 .sv 断言",
    # 撤销栈上这两步的名字（面板的撤销菜单会显示）
    "TRUTH_UNDO_CLEAR_EXP": "清空期望",
}

#: 冻结列自己的 role（`contracts.TruthRole` 是网格 model 的，两边不重叠）
ROLE_GUESSED = int(Qt.UserRole) + 21        # C-058/C-059：名字是按命名约定猜的
ROLE_NEEDS_PREFIX = int(Qt.UserRole) + 22   # 网存在但埋在子模块里，要层级前缀才 force 得到

#: 本模块对 `TruthModelProto` 的全部依赖（C3-int 接线时逐条核对；缺一个就是静默少一块）。
MODEL_REQUIREMENTS = {
    "methods": ("rowCount", "columnCount", "row_kind", "row_label", "flags", "data",
                "setData", "headerData", "set_current_col", "copy_tsv", "paste_tsv",
                "undo_stack", "column_drives"),
    "roles": ("CELL_STATE", "COL_STATE", "COL_NAME", "ROW_KIND",
              "IS_CURRENT_COL", "IS_FALLBACK", "DRIVE_TIP"),
    #: `NameModel` 另外要 `an` 与 `e_inputs`（model 没暴露，面板本来就有，见 set_source）
    "extra": ("an", "e_inputs"),
}


def _t(name, **fmt):
    """文案取值：`terms` 里有就用 `terms` 的，没有退回 `PENDING_TERMS`。"""
    s = getattr(T, name, None)
    if s is None:
        s = PENDING_TERMS[name]
    return s.format(**fmt) if fmt else s


def _nm(name):
    """objectName 取值：`names` 里有就用 `names` 的，没有退回 `PENDING_NAMES`。"""
    return getattr(N, name, None) or PENDING_NAMES[name]


# ═════════════════════════════ 列头（自绘三种底）═════════════════════════════
class TruthHeaderView(QtWidgets.QHeaderView):
    """网格列头：按 `COL_STATE` + `IS_CURRENT_COL` 查 `delegate.HEADER_COLORS` 上色（C-107），
    悬停给该列的 force / RF_WRITE 明细（C-063）。

    tooltip 走 `viewportEvent`（`QHeaderView` 自己也是在这里问 model 要 `ToolTipRole` 的）：
    model 给了 `ToolTipRole` 就用 model 的，没给就按 `DRIVE_TIP` 现拼——两种 model 都接得上。
    """

    def __init__(self, parent=None):
        QtWidgets.QHeaderView.__init__(self, Qt.Horizontal, parent)
        self.setObjectName(_nm("TRUTH_GRID_HEADER"))
        self.setFixedHeight(TH.TRUTH_HEADER_H)
        self.setHighlightSections(False)
        self.setSectionsClickable(True)
        self.setSectionResizeMode(QtWidgets.QHeaderView.Interactive)
        self.setDefaultSectionSize(TH.TRUTH_COL_W)
        self.setDefaultAlignment(Qt.AlignCenter)
        self.setFont(_mono_font(TH.FS_MONO))

    def header_tooltip(self, section):
        """列头 tooltip 文本（C-063）。没有列 / 没有驱动也给得出一句，绝不返回 None。"""
        m = self.model()
        if m is None or not (0 <= int(section) < m.columnCount()):
            return ""
        tip = m.headerData(section, Qt.Horizontal, Qt.ToolTipRole)
        if tip:
            return T.scrub(str(tip))
        name = str(m.headerData(section, Qt.Horizontal, int(TR.COL_NAME))
                   or m.headerData(section, Qt.Horizontal, Qt.DisplayRole) or "")
        drives = m.headerData(section, Qt.Horizontal, int(TR.DRIVE_TIP)) or []
        if isinstance(drives, str):
            drives = [drives]
        body = "\n".join(T.scrub(str(x)) for x in drives if str(x).strip())
        return _t("TRUTH_HEADER_TIP_FMT", col=name,
                  drives=body or _t("TRUTH_HEADER_TIP_NONE"))

    def paintSection(self, painter, rect, logicalIndex):
        m = self.model()
        if m is None:
            QtWidgets.QHeaderView.paintSection(self, painter, rect, logicalIndex)
            return
        cur = bool(m.headerData(logicalIndex, Qt.Horizontal, int(TR.IS_CURRENT_COL)))
        bg, fg = header_colors(m.headerData(logicalIndex, Qt.Horizontal, int(TR.COL_STATE)), cur)
        painter.save()
        painter.fillRect(rect, QtGui.QColor(bg))
        painter.setPen(QtGui.QPen(QtGui.QColor(TH.BORDER), 1))
        painter.drawLine(rect.bottomLeft(), rect.bottomRight())
        painter.drawLine(rect.topRight(), rect.bottomRight())
        painter.setFont(_mono_font(TH.FS_MONO, bold=cur))
        painter.setPen(QtGui.QColor(fg))
        painter.drawText(rect, int(Qt.AlignCenter),
                         str(m.headerData(logicalIndex, Qt.Horizontal, Qt.DisplayRole) or ""))
        painter.restore()

    def viewportEvent(self, event):
        if event.type() == QtCore.QEvent.ToolTip:
            section = self.logicalIndexAt(event.pos())
            text = self.header_tooltip(section)
            if text:
                QtWidgets.QToolTip.showText(event.globalPos(), text, self)
                return True
        return QtWidgets.QHeaderView.viewportEvent(self, event)


# ═════════════════════════════ 网格 ═════════════════════════════
class TruthTableView(QtWidgets.QTableView):
    """真值表网格（C-107 / C-108 / C-109 / C-258 / C-293 / C-294 / C-296 / C-299）。

    键位用 `keyPressEvent` 而不是 `QShortcut`：一是 offscreen 下真键鼠打得进来（`QShortcut`
    要走应用级的快捷键派发，测试里不稳），二是"编辑中不触发 Ctrl+D"（C-109）这条只有在
    `keyPressEvent` 里看得到 `state() == EditingState`。产品里菜单项照样挂 `QAction` 显示
    角标，但真正处理的是这里。
    """

    #: 动作只发信号，由面板转 model（视图不写数据，见模块 docstring 第 1 条）
    copyColumnRequested = QtCore.Signal(int)                 # C-109/C-258 Ctrl+D
    renameRequested = QtCore.Signal(int)                     # C-091 双击列头
    contextMenuRequested = QtCore.Signal(QtCore.QPoint, int, int)   # C-296 (全局坐标, 行, 列)
    selectionSummaryChanged = QtCore.Signal(str, int)        # C-293 (列名, 选中几格)

    def __init__(self, parent=None):
        QtWidgets.QTableView.__init__(self, parent)
        self.setObjectName(N.TRUTH_GRID_VIEW)
        self.setItemDelegate(TruthDelegate(self))
        self.setHorizontalHeader(TruthHeaderView(self))
        self.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectItems)
        self.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)   # C-293 拖矩形选区
        self.setEditTriggers(QtWidgets.QAbstractItemView.DoubleClicked
                             | QtWidgets.QAbstractItemView.EditKeyPressed
                             | QtWidgets.QAbstractItemView.AnyKeyPressed)
        self.setDragEnabled(False)
        self.setShowGrid(False)                  # 格子的边框由 delegate 画（虚线/2px outline）
        self.setWordWrap(False)
        self.setCornerButtonEnabled(False)
        self.setHorizontalScrollMode(QtWidgets.QAbstractItemView.ScrollPerPixel)
        self.setVerticalScrollMode(QtWidgets.QAbstractItemView.ScrollPerPixel)
        self.setFrameShape(QtWidgets.QFrame.NoFrame)
        self.viewport().installEventFilter(self)          # C-296 右键（见 eventFilter）
        vh = self.verticalHeader()
        vh.setVisible(False)                     # 行标签在冻结列里（C-295）
        vh.setDefaultSectionSize(TH.TRUTH_ROW_H)
        vh.setSectionResizeMode(QtWidgets.QHeaderView.Fixed)
        self.setStyleSheet("QTableView{background:%s;color:%s;}" % (TH.WHITE, TH.INK))
        self.horizontalHeader().sectionDoubleClicked.connect(self.renameRequested)

    # ── 绑定 model ──
    def setModel(self, model):
        old = self.model()
        if old is not None:
            for sig, slot in ((old.columnsInserted, self._on_columns_inserted),
                              (old.modelReset, self._fit_all_columns)):
                try:
                    sig.disconnect(slot)
                except (RuntimeError, TypeError):       # pragma: no cover - 没连上过
                    pass
        QtWidgets.QTableView.setModel(self, model)
        if model is None:
            return
        model.columnsInserted.connect(self._on_columns_inserted)   # C-108 只新列自适应
        model.modelReset.connect(self._fit_all_columns)            # C-108 换信号才全表自适应
        sm = self.selectionModel()
        if sm is not None:
            sm.selectionChanged.connect(self._emit_selection_summary)
        self._fit_all_columns()

    # ── 当前列高亮（C-107）──
    def currentChanged(self, current, previous):
        QtWidgets.QTableView.currentChanged(self, current, previous)
        m = self.model()
        if m is None or not hasattr(m, "set_current_col"):
            return
        m.set_current_col(current.column() if current.isValid() else -1)

    # ── 列宽（C-108）──
    def _fit_width(self, col):
        """一列的自适应宽度：格子内容、列头标号、`TRUTH_COL_W` 三者取大。

        列头也要算进去——`T10_NEG` 比格子里的 `0b0101` 长，只看格子会把标号截掉。
        """
        return max(TH.TRUTH_COL_W,
                   self.sizeHintForColumn(col),
                   self.horizontalHeader().sectionSizeHint(col))

    def _fit_all_columns(self):
        m = self.model()
        if m is None:
            return
        for c in range(m.columnCount()):
            self.setColumnWidth(c, self._fit_width(c))

    def _on_columns_inserted(self, _parent, first, last):
        for c in range(int(first), int(last) + 1):
            self.setColumnWidth(c, self._fit_width(c))

    # ── 选区摘要（C-293）──
    def _emit_selection_summary(self, *_a):
        ixs = self.selectedIndexes()
        if not ixs:
            self.selectionSummaryChanged.emit("", 0)
            return
        cols = {ix.column() for ix in ixs}
        name = ""
        if len(cols) == 1:
            name = str(ixs[0].data(int(TR.COL_NAME)) or "")
        self.selectionSummaryChanged.emit(name, len(ixs))

    # ── 右键（C-296）──
    def eventFilter(self, obj, event):
        """右键（C-296）：菜单本身在面板，这里只把「在哪一格按的」翻译成 (行, 列) 发出去。

        为什么用 viewport 的事件过滤器而不是 `contextMenuEvent`：右键先落在 `viewport()`
        上，往父级传的时候坐标要不要换算**跟着 Qt 版本走**（实测 6.11 传上来的还是 viewport
        坐标），照着换算会整整错一行。在 viewport 上拦，`event.pos()` 的坐标系没有歧义。
        """
        if obj is self.viewport() and event.type() == QtCore.QEvent.ContextMenu:
            ix = self.indexAt(event.pos())
            self.contextMenuRequested.emit(event.globalPos(),
                                           ix.row() if ix.isValid() else -1,
                                           ix.column() if ix.isValid() else -1)
            event.accept()
            return True
        return QtWidgets.QTableView.eventFilter(self, obj, event)

    # ── 键位 ──
    def keyPressEvent(self, event):
        m = self.model()
        editing = self.state() == QtWidgets.QAbstractItemView.EditingState
        key = event.key()
        mod = event.modifiers()
        ctrl = bool(mod & Qt.ControlModifier)

        if ctrl and key == Qt.Key_D:
            # C-109/C-258：单元格正在编辑时**不触发**——打断输入比少一个快捷键糟得多
            if not editing:
                c = self.currentIndex().column()
                if c >= 0:
                    self.copyColumnRequested.emit(c)
            event.accept()
            return
        if m is not None and not editing:
            if ctrl and key == Qt.Key_C:
                self._copy()
                event.accept()
                return
            if ctrl and key == Qt.Key_V:
                self._paste()
                event.accept()
                return
            if ctrl and key in (Qt.Key_Z, Qt.Key_Y):
                self._undo_redo(key == Qt.Key_Z)
                event.accept()
                return
            if key in (Qt.Key_Delete, Qt.Key_Backspace):
                self._clear_expected()
                event.accept()
                return
        QtWidgets.QTableView.keyPressEvent(self, event)

    def _copy(self):
        m = self.model()
        if not hasattr(m, "copy_tsv"):
            return
        QtWidgets.QApplication.clipboard().setText(m.copy_tsv(self.selectedIndexes()) or "")

    def _paste(self):
        """C-294：TSV 落在**活动格**；超列自动追加列；整次一步撤销（都在 model 里，这里只转发）。"""
        m = self.model()
        ix = self.currentIndex()
        if not hasattr(m, "paste_tsv") or not ix.isValid():
            return
        m.paste_tsv(QtWidgets.QApplication.clipboard().text(), ix.row(), ix.column())

    def _undo_redo(self, undo):
        m = self.model()
        stack = m.undo_stack() if hasattr(m, "undo_stack") else None
        if stack is None:
            return
        stack.undo() if undo else stack.redo()

    def _clear_expected(self):
        """Delete = 清空选中的**期望**格（C-081 的键位面）。多格一步撤销。

        只清"真的填过"的格子（`IS_FALLBACK` 为假）：兜底显示的格子本来就是空的，
        把它们也塞进宏里等于凭空多一步撤不掉的空操作。
        """
        m = self.model()
        if m is None:
            return
        ixs = [ix for ix in self.selectedIndexes()
               if ix.data(int(TR.ROW_KIND)) == RK.EXP
               and not ix.data(int(TR.IS_FALLBACK))
               and bool(m.flags(ix) & Qt.ItemIsEditable)]
        if not ixs:
            return
        stack = m.undo_stack() if hasattr(m, "undo_stack") else None
        macro = stack is not None and len(ixs) > 1
        if macro:
            stack.beginMacro(_t("TRUTH_UNDO_CLEAR_EXP"))
        for ix in ixs:
            m.setData(ix, "", Qt.EditRole)
        if macro:
            stack.endMacro()


# ═════════════════════════════ 冻结左列 ═════════════════════════════
class NameModel(QtCore.QAbstractTableModel):
    """冻结左列的一列只读 model：行 = `truth_model.row_label(r)`（C-074）。

    猜名 / 需前缀 / 类型 / 位宽这些**输入行的元信息** `TruthModelProto` 没有暴露
    （`e_inputs` 的九个键里没有 `guessed`），所以这里 additive 地自己取：
    面板调 `set_source(truth_model, an, e_inputs)` 时，按 `e_inputs[i]["key"]` 到
    `inputs_table.input_rows(an)` 里查同一根输入的行 dict，用它的
    `guessed` / `needs_prefix` / `rw` / `width` / `found_in_text` / `note`。
    两张表的 `key` 同源（`truth/rows.py` 与 `inputs_table.input_rows` 明写"同口径"），
    查不到就退回空 dict —— 少一块 tooltip，绝不崩、绝不错标猜名。
    """

    def __init__(self, parent=None):
        QtCore.QAbstractTableModel.__init__(self, parent)
        self._truth = None
        self._meta = []          # 与输入行一一对应的元信息（缺的补空 dict）

    # ── 绑定 ──
    def set_source(self, truth_model, an=None, e_inputs=None):
        """绑定网格 model（取行标签 / 行语义）+ `an`/`e_inputs`（取猜名与 tooltip）。

        面板每次 `truth_model.load(an, cols, e_inputs)` 之后都要再调一次这个
        —— `load` 换的是信号，元信息跟着换。
        """
        self.beginResetModel()
        old = self._truth
        if old is not None and old is not truth_model:
            try:
                old.modelReset.disconnect(self._on_truth_reset)
            except (RuntimeError, TypeError):            # pragma: no cover
                pass
        self._truth = truth_model
        if truth_model is not None and truth_model is not old:
            truth_model.modelReset.connect(self._on_truth_reset)
        self._meta = self._build_meta(an, e_inputs)
        self.endResetModel()

    @staticmethod
    def _build_meta(an, e_inputs):
        if not e_inputs:
            return []
        by_key = {}
        for row in (IT.input_rows(an) if an else []):
            by_key.setdefault(row.get("key"), row)
        return [dict(by_key.get(e.get("key")) or {}) for e in e_inputs]

    def _on_truth_reset(self):
        """网格换了形状（`load`）→ 行数跟着走。元信息等面板的 `set_source` 补。"""
        self.beginResetModel()
        self.endResetModel()

    def truth_model(self):
        return self._truth

    def meta_at(self, row):
        """输入行 r 的元信息 dict（auto/期望行与越界都给空 dict）。"""
        i = int(row)
        return dict(self._meta[i]) if 0 <= i < len(self._meta) else {}

    # ── QAbstractTableModel ──
    def rowCount(self, parent=QtCore.QModelIndex()):
        if parent.isValid() or self._truth is None:
            return 0
        return self._truth.rowCount()

    def columnCount(self, parent=QtCore.QModelIndex()):
        return 0 if parent.isValid() else 1

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if orientation == Qt.Horizontal and role == Qt.DisplayRole and section == 0:
            return T.TRUTH_FROZEN_HEADER
        return None

    def flags(self, index):
        if not index.isValid():
            return Qt.NoItemFlags
        return Qt.ItemIsEnabled | Qt.ItemIsSelectable        # 信号名只看不编辑

    def _kind(self, row):
        return self._truth.row_kind(row) if self._truth is not None else RK.INPUT

    def _tip(self, row):
        """C-075：位宽 · 类型 · 来源，都过 `terms.scrub`（不变量 I-12）。"""
        kind = self._kind(row)
        if kind == RK.AUTO:
            return _t("TRUTH_ROW_TIP_AUTO")
        if kind == RK.EXP:
            return _t("TRUTH_ROW_TIP_EXP")
        meta = self._meta[row] if 0 <= row < len(self._meta) else {}
        label = str(self._truth.row_label(row))
        parts = [_t("TRUTH_ROW_TIP_FMT", label=label,
                    width=int(meta.get("width") or 1),
                    rw=str(meta.get("rw") or "?"))]
        for key in ("found_in_text", "note"):
            txt = T.scrub(str(meta.get(key) or "")).strip()
            if txt:
                parts.append(txt)
        if meta.get("guessed"):
            parts.append(T.SIDE_INPUTS_GUESS_BADGE)
        if meta.get("needs_prefix"):
            parts.append(T.SIDE_NEEDS_PREFIX_MARK)
        return "\n".join(parts)

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid() or self._truth is None:
            return None
        r = index.row()
        if not (0 <= r < self.rowCount()):
            return None
        meta = self._meta[r] if 0 <= r < len(self._meta) else {}
        kind = self._kind(r)

        if role == ROLE_GUESSED:
            return bool(meta.get("guessed"))
        if role == ROLE_NEEDS_PREFIX:
            return bool(meta.get("needs_prefix"))
        if role == int(TR.ROW_KIND):
            return kind
        if role == Qt.DisplayRole:
            return str(self._truth.row_label(r))
        if role == Qt.ToolTipRole:
            return self._tip(r) or None
        if role == Qt.TextAlignmentRole:
            return int(Qt.AlignLeft | Qt.AlignVCenter)
        if role == Qt.FontRole:
            if kind == RK.INPUT:
                # C-074：控制 / 选择位加粗（"哪几根在选路"要一眼看出来）
                return _mono_font(TH.FS_MONO, bold=bool(meta.get("is_control")
                                                        or meta.get("bold")))
            return _ui_font(TH.FS_UI_SMALL, bold=(kind == RK.EXP))
        if role == Qt.ForegroundRole:
            if kind != RK.INPUT:
                return QtGui.QColor(TH.MUTE)
            if meta.get("guessed") or meta.get("needs_prefix"):
                return QtGui.QColor(TH.AMBER_FG)       # 猜名 / 需前缀行整行琥珀
            if meta.get("is_dft_gate"):
                return QtGui.QColor(TH.BLUE_DARK)      # C-128 iddq 门行
            return QtGui.QColor(TH.INK)
        if role == Qt.BackgroundRole:
            if kind == RK.INPUT and meta.get("guessed"):
                return QtGui.QColor(TH.GUESS_BG)
            if kind != RK.INPUT:
                return QtGui.QColor(TH.HINT_BG)
            return QtGui.QColor(TH.WHITE)
        return None


class FrozenNamesView(QtWidgets.QTableView):
    """冻结左列视图（C-295）。

    用「两个视图并排 + 滚动条同步」而不是 Qt 官方那种 overlay 冻结列：并排不用管
    `stackUnder` 与几何跟随，代价是选区不跨越信号名列——而信号名本来就不该被选中编辑。
    实际拖宽度的 `QSplitter` 在面板；这里只声明宽度与 clamp（`theme.CLAMP_FROZEN`）。
    """

    def __init__(self, parent=None):
        QtWidgets.QTableView.__init__(self, parent)
        self.setObjectName(N.TRUTH_NAMES_VIEW)
        self.setModel(NameModel(self))
        self.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.setShowGrid(False)
        self.setWordWrap(False)
        self.setFrameShape(QtWidgets.QFrame.NoFrame)
        self.setCornerButtonEnabled(False)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)   # 滚动由网格那边带（bind_frozen）
        self.setVerticalScrollMode(QtWidgets.QAbstractItemView.ScrollPerPixel)
        self.setMinimumWidth(TH.CLAMP_FROZEN[0])
        self.setMaximumWidth(TH.CLAMP_FROZEN[1])
        hh = self.horizontalHeader()
        hh.setObjectName(_nm("TRUTH_NAMES_HEADER"))
        hh.setFixedHeight(TH.TRUTH_HEADER_H)
        hh.setHighlightSections(False)
        hh.setFont(_ui_font(TH.FS_UI_SMALL))
        hh.setSectionResizeMode(0, QtWidgets.QHeaderView.Stretch)
        vh = self.verticalHeader()
        vh.setVisible(False)
        vh.setDefaultSectionSize(TH.TRUTH_ROW_H)
        self.setStyleSheet(
            "QTableView{background:%s;color:%s;selection-background-color:%s;selection-color:%s;}"
            "QHeaderView::section{background:%s;color:%s;border:none;"
            "border-bottom:1px solid %s;border-right:1px solid %s;padding-left:6px;}"
            % (TH.WHITE, TH.INK, TH.SELECTED_BG, TH.INK,
               TH.PANEL_BG, TH.MUTE, TH.BORDER, TH.BORDER))

    def set_source(self, truth_model, an=None, e_inputs=None):
        self.model().set_source(truth_model, an, e_inputs)

    def sizeHint(self):
        return QtCore.QSize(TH.FROZEN_W, QtWidgets.QTableView.sizeHint(self).height())


def bind_frozen(frozen, grid):
    """冻结列 ↔ 网格三项同步（C-295）：纵向滚动、行高、当前行。

    **在两边都 `setModel` 之后调**（当前行同步要 grid 的 `selectionModel`，它随 setModel 换）。
    互连用一个 `busy` 闸门防回环——两条 `valueChanged` 互相 `setValue` 会来回弹到栈溢出。
    返回这个闸门 dict，面板要临时断开时用得上。
    """
    busy = {"on": False}
    fb, gb = frozen.verticalScrollBar(), grid.verticalScrollBar()

    def _adopt_range():
        """左列的纵向范围一律**借用网格的**，不自己算。

        网格底下有横向滚动条（60 列是常态），它的视口就比左列矮一条；两边各算各的范围
        就会差这么多——滚到底时左列的最后一行比网格高半行，读串行是迟早的事。让左列直接
        跟着网格的范围走（底下多出一条空白），行才始终对得齐。
        """
        if ((fb.minimum(), fb.maximum(), fb.pageStep())
                == (gb.minimum(), gb.maximum(), gb.pageStep())):
            return
        fb.setPageStep(gb.pageStep())
        fb.setRange(gb.minimum(), gb.maximum())

    def _guarded(fn):
        def _slot(*a):
            if busy["on"]:
                return
            busy["on"] = True
            try:
                fn(*a)
            finally:
                busy["on"] = False
        return _slot

    def _grid_to_frozen(value):
        _adopt_range()
        fb.setValue(value)

    # 任一边的范围变了（换信号 / 改行高 / 窗口缩放）都重新对一次，顺手把位置也摆正
    _resync = _guarded(lambda *_a: _grid_to_frozen(gb.value()))
    gb.valueChanged.connect(_guarded(_grid_to_frozen))
    fb.valueChanged.connect(_guarded(gb.setValue))
    gb.rangeChanged.connect(_resync)
    fb.rangeChanged.connect(_resync)
    _resync()

    def _rows(dst):
        def _slot(idx, _old, new):
            if busy["on"]:
                return
            busy["on"] = True
            try:
                dst.setRowHeight(idx, new)
            finally:
                busy["on"] = False
        return _slot

    frozen.verticalHeader().sectionResized.connect(_rows(grid))
    grid.verticalHeader().sectionResized.connect(_rows(frozen))

    sm = grid.selectionModel()
    if sm is not None:
        def _row_follow(current, _prev):
            if current.isValid() and frozen.model() is not None:
                frozen.selectRow(current.row())
        sm.currentChanged.connect(_row_follow)
    return busy
