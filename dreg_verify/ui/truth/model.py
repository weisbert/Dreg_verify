# -*- coding: utf-8 -*-
"""truth/model.py —— 真值表 `QAbstractTableModel`（`contracts.TruthModelProto` 的实现）。

架构 §2.4 / §6.12，契约 TRUTH_MODEL 33 条 + C-299，不变量 **I-15 的唯一保证方**。

形状：行 = `e_inputs`（`truth/rows.e_inputs_from_an`）+ `auto_out` 行 + `期望` 行；
列 = `edits.py` 的列模型（一列 = 一条测试）。行数只跟信号走、不跟列走，所以**加列/删列/清零
都不用 reset**——这正是 I-15 能成立的前提。

三条硬规矩（都由 `tests/test_ui_truth_model.py` 钉死）：

1. **I-15：`load()` 是唯一 `beginResetModel` 的地方**。刷新一律只发 `dataChanged` /
   `headerDataChanged` / `begin(End)Insert(Remove)Columns`。整个类里唯一的 `.clear()` 是
   `self._undo.clear()`（换信号时清撤销栈——不清的话 Ctrl+Z 会撤到上一个信号的编辑上去）；
   v1 `_populate_truth()` 那句 `tbl.clear()` 是列宽被弹回、编辑时闪的根因，这里一个字都不许有。
2. **唯一写入口**：对外的 `setData` 与各列操作方法自己不改 `cols`，只造 `commands.py` 的
   `QUndoCommand` 往 `undo_stack()` 里 push；批量动作用 `beginMacro/endMacro` 裹成一步
   （C-299：每种写操作后 `undo_stack().count()` 恰好 +1）。
3. **本模块不 import theme**：底色/字色/字体不在 model 里做，`data()` 只回
   `TruthRole.CELL_STATE`（`theme.CELL_STATES` 的键名字符串），delegate 自己查色。
   同理不 import topout / pageviews / generator / gui / sigflow（I-19，`test_ui_layering` 扫）。

`generator._fmt_cell` 不许 import（它是**报告**的格式：多位一律 0xN）。编辑器格式按 C-084 =
v1 `gui.MainWindow._cell_text`：1 位 `0/1`、2–4 位 `0bXXXX` 零填充、更宽 `0xNN`——
这个格式的意义是「能被 `truth_edit.parse_int` 原样读回」，见 `fmt_cell` 与它的 roundtrip 测试。
"""

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt, Signal
from PySide6.QtGui import QUndoStack

from ... import edits as ED
from ... import inputs_table as IT
from ... import truth_edit as TE
from .. import contracts
from .. import terms
from .commands import AddCols, RemoveCols, RenameCol, SetCells
from .rows import e_inputs_from_an

__all__ = ["TruthModel", "fmt_cell", "PENDING_TERMS"]


#: `ui/terms.py` 里还没有、但本模块要用的文案（C2-int 正在并行改 terms.py，这波不碰它）。
#: C3-int 负责搬进 `terms.py` 并删掉这张表；取值一律经 `_t()`（terms 有就用 terms 的）。
PENDING_TERMS = {
    # 粘贴结果说明的两截尾巴（`terms.TRUTH_PASTE_REPORT_FMT` 的 {added} / {skipped} 占位）
    "TRUTH_PASTE_ADDED_FMT": "，新增列 {names}",
    "TRUTH_PASTE_SKIPPED_FMT": "，跳过 {n} 格（只读行/列）",
    # 批量填（C-298 的另一半，对话框在 C3-c）
    "TRUTH_BATCH_FILL_REPORT_FMT": "批量填了 {n} 列的期望",
    # 按列名回填期望的结果（C-298；读文件那半边在 C3-d 的 truth/io.py）
    "TRUTH_APPLY_EXP_MISSING_FMT": "这些列名在本信号里没有：{names}",
    # 列下标越界（按钮/右键菜单在没选中列时也可能点进来，不许崩）
    "TRUTH_COL_OUT_OF_RANGE": "没有选中的测试列",
    # mux 自动生成列的数据值要整表一起改（C-110），不能只改这一格
    "TRUTH_MUX_DATA_WHOLE_TABLE_ONLY":
        "自动生成列的 mux 数据值要整表一起改：用工具条的「设置 mux 数据值」；"
        "只改这一列请先「复制列」得到一条手编列",
}


def _t(name, **fmt):
    """文案取值：`terms` 里有就用 `terms` 的，没有退回 `PENDING_TERMS`（C3-int 搬完即一致）。"""
    s = getattr(terms, name, None)
    if s is None:
        s = PENDING_TERMS[name]
    return s.format(**fmt) if fmt else s


def _mask(width):
    """位宽掩码（`expr.mask` 同式；ui/ 不 import 引擎的 expr）。"""
    w = int(width or 0)
    return (1 << w) - 1 if w > 0 else 0


def fmt_cell(val, width):
    """单元格取值显示格式（C-084，= v1 `gui.MainWindow._cell_text`）：
    1 位→`0/1`；2–4 位→`0bXXXX` 零填充（真值表里一眼看清每个控制/数据位）；更宽→`0xNN`。

    这个格式的**约束**不是好看，是「`truth_edit.parse_int(fmt_cell(v, w)) == v & mask(w)`」——
    复制出去的一片 TSV 必须能原样粘回来。别改成别的写法（报告那套在 `generator._fmt_cell`，
    两者故意不同：报告不需要被读回，真值表需要）。
    """
    w = int(width or 1)
    m = _mask(w)
    v = int(val or 0) & m
    if w <= 1:
        return str(v & 1)
    if w <= 4:
        return "0b" + format(v, "0%db" % w)
    return "0x%X" % v


class TruthModel(QAbstractTableModel):
    """真值表 model。信号见 `contracts.TRUTH_SIGNALS`（五个全在，名字/签名一字不差）。"""

    parseFailed = Signal(str)                    # C-083 数值写法没认出来（已还原）
    pasteReport = Signal(str)                    # C-294 粘贴结果说明
    progressChanged = Signal(int, int, int)      # C-106 (手填 n, 正向 m, 不一致 k)
    colsChanged = Signal()                       # 列集合变了 → state.put_edit + .sv 预览重算
    muxCollision = Signal(str)                   # C-113 mux 撞值提示

    def __init__(self, parent=None):
        QAbstractTableModel.__init__(self, parent)
        self._an = {}
        self._cols = []
        self._e_inputs = []
        self._cur_col = -1
        self._undo = QUndoStack(self)

    # ═════════════════ 形状 ═════════════════
    def load(self, an, cols, e_inputs):
        """换信号 / 重新生成：**唯一允许 reset 的地方**（I-15）。

        `cols` 按引用收下（列 dict 与其中的 `vec` 与调用方共享，与 v1 `SignalView` 同——
        `state.put_edit` 存的就是这份），只把外层 list 拷一份，免得改列顺序反噬调用方。
        """
        self.beginResetModel()
        self._an = an or {}
        self._e_inputs = list(e_inputs or [])
        self._cols = list(cols or [])
        self._cur_col = -1
        self._undo.clear()          # 本类唯一的 .clear()：撤销栈，不是表格（I-15 说的是后者）
        self.endResetModel()
        self.colsChanged.emit()
        self._emit_progress()

    def rowCount(self, parent=QModelIndex()):
        if parent.isValid():
            return 0
        return len(self._e_inputs) + 2          # 输入行 + auto_out 行 + 期望行

    def columnCount(self, parent=QModelIndex()):
        if parent.isValid():
            return 0
        return len(self._cols)

    def row_kind(self, r):
        """行语义（`contracts.TruthRowKind`）——视图不要自己按下标判断。"""
        ni = len(self._e_inputs)
        if 0 <= r < ni:
            return contracts.TruthRowKind.INPUT
        if r == ni:
            return contracts.TruthRowKind.AUTO
        return contracts.TruthRowKind.EXP

    def row_label(self, r):
        """冻结列的行标签：输入行 = 真名　(角色 · 端口)；auto/期望行 = `terms` 的两句。"""
        k = self.row_kind(r)
        if k == contracts.TruthRowKind.INPUT:
            return self._e_inputs[r]["label"]
        if k == contracts.TruthRowKind.AUTO:
            return terms.TRUTH_ROW_AUTO
        return terms.TRUTH_ROW_EXP

    def all_names(self):
        """全部测试列的【最终标号】（负向带 _NEG）——.sv 标号查重、导出列名都用它。"""
        return [TE.final_col_name(c["name"], bool(c.get("neg"))) for c in self._cols]

    def cols(self):
        """当前列模型快照（`edits.py` schema）——`state.put_edit` / `edits.compute_edited` 吃它。

        列 dict 与 `vals` 是拷贝（调用方改快照不会反噬 model）；`vec` 是引擎对象，按引用带走
        （`edits.mux_derive` 本来就会 `clone_vector`，在这里深拷会丢它的身份）。
        """
        out = []
        for c in self._cols:
            d = dict(c)
            d["vals"] = dict(c.get("vals") or {})
            out.append(d)
        return out

    def editable_kind(self):
        """`""` / `"logic"` / `"mux"`（`an["editable"]`）。`""` = 全表只读（C-134）。"""
        return self._an.get("editable") or ""

    def undo_stack(self):
        return self._undo

    # ═════════════════ 只读派生量 ═════════════════
    def col_state(self, c):
        """列状态（`contracts.TruthColState`）：dft 拍 > 反例 > 手编 > 自动。"""
        if not (0 <= c < len(self._cols)):
            return contracts.TruthColState.AUTO
        col = self._cols[c]
        if ED.is_dft_pitch_col(self._an, col):
            return contracts.TruthColState.DFT
        if col.get("neg"):
            return contracts.TruthColState.NEG
        if col.get("user"):
            return contracts.TruthColState.USER
        return contracts.TruthColState.AUTO

    def cell_state(self, r, c):
        """单元格状态 = `theme.CELL_STATES` 的键（model 不 import theme，只给键名）。

        期望行 → `edits.expected_cell_state`（它的 EXP_* 取值本来就是这几个键名）；
        auto 行恒 `readonly`；输入行：DFT 拍列一律 `dft`（C-130 那列不参与表达式重算，
        改它没有意义），其余按 `edits.input_cell_state` 映射（mux 数据角色行 = 可填 = editable）。
        """
        if not (0 <= c < len(self._cols)):
            return "readonly"
        col = self._cols[c]
        kind = self.row_kind(r)
        if kind == contracts.TruthRowKind.EXP:
            return ED.expected_cell_state(self._an, col)
        if kind == contracts.TruthRowKind.AUTO:
            return "readonly"
        if ED.is_dft_pitch_col(self._an, col):
            return "dft"
        st = ED.input_cell_state(self._an, self._e_inputs[r])
        if st == ED.CELL_EDITABLE or st == ED.CELL_MUX_DATA:
            return "editable" if self.editable_kind() else "readonly"
        return "readonly"                      # CELL_DFT_GATE / CELL_READONLY

    def fill_progress(self):
        """(手填 n, 正向 m, 不一致 k)。k = 期望格判成 `EXP_DIFF` 的列数（C-106）。"""
        n, m = ED.fill_progress(self._cols)
        k = sum(1 for c in self._cols
                if ED.expected_cell_state(self._an, c) == ED.EXP_DIFF)
        return n, m, k

    def column_drives(self, c):
        """该列实际下的每条 force / RF_WRITE（C-063 列头 tooltip / 常驻面板）。"""
        return IT.column_drives(self._an, c)

    def protected_negatives(self):
        """"值得保护"的反例列（自定义命名或手填过错值）——删反例前要先问一声（C-103）。"""
        return ED.protected_negatives(self._cols)

    # ═════════════════ Qt 读接口 ═════════════════
    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if orientation == Qt.Vertical:
            if role in (Qt.DisplayRole, Qt.ToolTipRole):
                return self.row_label(section)
            if role == int(contracts.TruthRole.ROW_KIND):
                return self.row_kind(section)
            return None
        if not (0 <= section < len(self._cols)):
            return None
        if role in (Qt.DisplayRole, Qt.EditRole, int(contracts.TruthRole.COL_NAME)):
            return self.all_names()[section]
        if role == int(contracts.TruthRole.COL_STATE):
            return self.col_state(section)
        if role == int(contracts.TruthRole.IS_CURRENT_COL):
            return section == self._cur_col
        if role == int(contracts.TruthRole.DRIVE_TIP):
            return self.column_drives(section)
        return None

    def flags(self, index):
        base = Qt.ItemIsEnabled | Qt.ItemIsSelectable
        if not index.isValid():
            return Qt.NoItemFlags
        if self._is_editable(index.row(), index.column()):
            return base | Qt.ItemIsEditable
        return base

    def _is_editable(self, r, c):
        """这一格能不能改（`flags` 与 `setData` 共用同一判据，免得两处各判一次）。"""
        if not (0 <= c < len(self._cols)) or not (0 <= r < self.rowCount()):
            return False
        if not self.editable_kind():                 # C-134：不可建信号 → 全表只读
            return False
        col = self._cols[c]
        kind = self.row_kind(r)
        if kind == contracts.TruthRowKind.AUTO:
            return False                              # auto_out 行恒只读（C-077）
        if ED.is_dft_pitch_col(self._an, col):
            return False                              # iddq 自检拍列整列只读（C-129/C-130）
        if kind == contracts.TruthRowKind.EXP:
            return True
        return ED.input_cell_editable(self._an, self._e_inputs[r])

    def _raw(self, r, c):
        """这一格的 int 原值（期望格未手填时 = auto_out 兜底值）。"""
        col = self._cols[c]
        kind = self.row_kind(r)
        if kind == contracts.TruthRowKind.AUTO:
            return int(col.get("auto") or 0)
        if kind == contracts.TruthRowKind.EXP:
            return int(col["auto"] if col.get("exp") is None else col["exp"])
        return int((col.get("vals") or {}).get(self._e_inputs[r]["key"], 0))

    def _width(self, r, c):
        kind = self.row_kind(r)
        if kind == contracts.TruthRowKind.INPUT:
            return self._e_inputs[r]["width"]
        return self._cols[c].get("auto_w") or 1

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        r, c = index.row(), index.column()
        if not (0 <= c < len(self._cols)) or not (0 <= r < self.rowCount()):
            return None
        if role in (Qt.DisplayRole, Qt.EditRole):
            return fmt_cell(self._raw(r, c), self._width(r, c))
        if role == Qt.TextAlignmentRole:
            return int(Qt.AlignRight | Qt.AlignVCenter)
        # 底色 / 字色 / 字体不在 model 里做（delegate 查 CELL_STATE，架构 §2.4）
        if role == int(contracts.TruthRole.IS_FALLBACK):
            return (self.row_kind(r) == contracts.TruthRowKind.EXP
                    and self._cols[c].get("exp") is None)
        if role == int(contracts.TruthRole.ROW_KIND):
            return self.row_kind(r)
        if role == int(contracts.TruthRole.COL_STATE):
            return self.col_state(c)
        if role == int(contracts.TruthRole.CELL_STATE):
            return self.cell_state(r, c)
        if role == int(contracts.TruthRole.DRIVE_TIP):
            return self.column_drives(c)
        if role == int(contracts.TruthRole.RAW_VALUE):
            return self._raw(r, c)
        if role == int(contracts.TruthRole.COL_NAME):
            return TE.final_col_name(self._cols[c]["name"], bool(self._cols[c].get("neg")))
        if role == int(contracts.TruthRole.IS_CURRENT_COL):
            return c == self._cur_col
        return None

    # ═════════════════ 内部写函数（只给 commands.py 调）═════════════════
    def _get_cell(self, r, c):
        """单元格的内部值（`SetCells` 的 old/new）：输入行 = int；期望行 = (exp, neg)。

        期望行带上 `neg` 是因为「清空期望」同时清负向标记（C-081 + v1 `_on_truth_item`），
        两件事必须一起撤销——否则 Ctrl+Z 之后列还挂着反例身份、错值却没了（静默 NEG-BROKEN）。
        """
        col = self._cols[c]
        if self.row_kind(r) == contracts.TruthRowKind.EXP:
            return (col.get("exp"), bool(col.get("neg")))
        return int((col.get("vals") or {}).get(self._e_inputs[r]["key"], 0))

    def _set_cell(self, r, c, v):
        col = self._cols[c]
        kind = self.row_kind(r)
        if kind == contracts.TruthRowKind.AUTO:
            return
        if kind == contracts.TruthRowKind.EXP:
            exp, neg = v
            col["exp"] = exp
            col["neg"] = bool(neg)
            return
        e = self._e_inputs[r]
        val = int(v)
        col["vals"][e["key"]] = val
        vec = col.get("vec")
        if self.editable_kind() == "mux" and col.get("user") and vec is not None:
            # C-111 mux 手编列：只改本列该数据源 + 按路由 case 的源重算 auto_out
            # （三步与 `edits.set_mux_user_data` 同，拆开是为了 redo/undo 走同一条路）
            vec.assignments[e["key"]] = val
            ED.recompute_mux_user_auto(self._an, col)
        else:
            ED.recompute_col_an(self._an, col)   # C-080 改输入 → auto_out 当场重算（DFT 拍早退）

    def _apply_cells(self, items):
        """写一批格子并只发 `dataChanged`（I-15：绝不 reset）。items = [(r, c, 值)]。"""
        touched = set()
        for r, c, v in items:
            if not (0 <= c < len(self._cols)) or not (0 <= r < self.rowCount()):
                continue
            self._set_cell(r, c, v)
            touched.add(c)
        if not touched:
            return
        last = self.rowCount() - 1
        for c in sorted(touched):
            # 整列重发：改一个输入格会连带 auto_out 行、期望格的兜底显示与状态色一起变
            self.dataChanged.emit(self.index(0, c), self.index(last, c))
            self.headerDataChanged.emit(Qt.Horizontal, c, c)   # 列状态（反例↔正向）可能翻
        self._emit_progress()

    def _insert_cols(self, at, cols):
        at = max(0, min(int(at), len(self._cols)))
        n = len(cols)
        if n <= 0:
            return
        self.beginInsertColumns(QModelIndex(), at, at + n - 1)
        self._cols[at:at] = list(cols)
        self.endInsertColumns()
        self.colsChanged.emit()
        self._emit_progress()

    def _take_cols(self, idxs):
        """按下标摘走若干列，返回【按原表升序】的列 dict 列表（供 undo 插回去）。"""
        idxs = sorted({int(i) for i in idxs if 0 <= int(i) < len(self._cols)})
        if not idxs:
            return []
        taken = [self._cols[i] for i in idxs]
        for i in reversed(idxs):               # 倒着删：前面的下标不会被后面的删除搅乱
            self.beginRemoveColumns(QModelIndex(), i, i)
            del self._cols[i]
            self.endRemoveColumns()
        self.colsChanged.emit()
        self._emit_progress()
        return taken

    def _set_col_name(self, c, name):
        if not (0 <= c < len(self._cols)):
            return
        self._cols[c]["name"] = name
        self.headerDataChanged.emit(Qt.Horizontal, c, c)
        self.colsChanged.emit()

    def _emit_progress(self):
        n, m, k = self.fill_progress()
        self.progressChanged.emit(n, m, k)

    # ═════════════════ 写入口：setData ═════════════════
    def setData(self, index, value, role=None):
        """**唯一的单格写入口**（I-15）。返回 False = 没写进去、这一格已还原（Qt 自动回显旧值）。

        C-082/C-083：`truth_edit.parse_int` 认 8 种写法；认不出来发 `parseFailed` 并 return
        False，**绝不静默吞成 0 / 未填**（那会悄悄改掉验证意图）。
        """
        if role is None:
            role = Qt.EditRole
        if not index.isValid() or role not in (Qt.EditRole, Qt.DisplayRole):
            return False
        r, c = index.row(), index.column()
        if not self._is_editable(r, c):
            return False
        text = "" if value is None else str(value)
        kind = self.row_kind(r)
        col = self._cols[c]

        if kind == contracts.TruthRowKind.EXP:
            txt = text.strip()
            if txt == "":
                new = (None, False)              # C-081 清空 = 回到「待手填」（负向标记一并清）
            else:
                try:
                    val = TE.parse_int(txt)
                except ValueError:
                    self.parseFailed.emit(_t("TRUTH_PARSE_FAILED_FMT", text=txt))
                    return False
                new = (val & _mask(col.get("auto_w") or 1), bool(col.get("neg")))
            old = self._get_cell(r, c)
            if old == new:
                return True
            self._undo.push(SetCells(self, [(r, c, old, new)], "填期望"))
            return True

        # ── 输入行 ──
        e = self._e_inputs[r]
        if self.editable_kind() == "mux" and e["mux_data_base"] and not col.get("user"):
            # C-110：自动生成列的数据值是【整表 by_base】的事，不能只改这一格（改了会与
            # 内部/导出值不一致 = 假的所见即所得）。本波 set_mux_data_value 还没接 state，
            # 走到这里就照实说一句并还原。
            try:
                self.set_mux_data_value(e["mux_data_base"], text)
            except NotImplementedError:
                self.parseFailed.emit(_t("TRUTH_MUX_DATA_WHOLE_TABLE_ONLY"))
                return False
            return True
        try:
            val = TE.parse_int(text)             # 输入格空 = 0（没有「未填」这一档）
        except ValueError:
            self.parseFailed.emit(_t("TRUTH_PARSE_FAILED_FMT", text=text))
            return False
        new = val & _mask(e["width"])
        old = self._get_cell(r, c)
        if old == new:
            return True
        self._undo.push(SetCells(self, [(r, c, old, new)], "改输入值"))
        return True

    # ═════════════════ 列操作（每个都是一步撤销，C-299）═════════════════
    def append_test_column(self, n=1, src_idx=None):
        """加 n 条测试列（C-086）。mux 时 = 克隆选中列的 case 成手编列（C-115）。返回新列名。"""
        kind = self.editable_kind()
        if not kind or n <= 0:
            return []
        new = []
        for _ in range(int(n)):
            if kind == "mux":
                got = ED.copy_cols(self._cols + new,
                                   [src_idx] if src_idx is not None else None)
                new.extend(got)
            else:
                new.append(ED.add_col(self._an, self._cols + new, self._e_inputs))
        if not new:
            return []
        self._undo.push(AddCols(self, len(self._cols), new, "加列"))
        return [TE.final_col_name(c["name"], bool(c.get("neg"))) for c in new]

    def duplicate_column(self, c):
        """复制一列（C-087/C-116：取值与期望一并带走，自动换新名）。返回 (新下标, 新列名)。"""
        if not self.editable_kind() or not (0 <= c < len(self._cols)):
            return -1, ""
        new = ED.copy_cols(self._cols, [c])
        if not new:
            return -1, ""
        at = len(self._cols)
        self._undo.push(AddCols(self, at, new, "复制列"))
        return at, TE.final_col_name(new[0]["name"], bool(new[0].get("neg")))

    def remove_columns(self, cs):
        """删列（C-088，支持多选）。返回被删的列 dict 列表。

        mux 的自动生成列只是从 `cols` 里移走——`edits.mux_derive` 会据「现在还剩哪些自动列」
        算出 `dropped` 交给生成器（C-117），model 这边不用另记一份。
        """
        idxs = sorted({int(i) for i in (cs or ()) if 0 <= int(i) < len(self._cols)})
        if not idxs:
            return []
        items = [(i, self._cols[i]) for i in idxs]
        self._undo.push(RemoveCols(self, items, "删列"))
        return [c for (_i, c) in items]

    def rename_column(self, c, new_name):
        """改列名（C-091/C-092/C-093）。返回 (True, 最终名) 或 (False, 给用户看的原因)。"""
        if not (0 <= c < len(self._cols)):
            return False, _t("TRUTH_COL_OUT_OF_RANGE")
        col = self._cols[c]
        if not col.get("user"):
            return False, terms.TRUTH_RENAME_AUTO_REFUSED      # C-093 自动 T 列拒改名
        others = [x["name"] for i, x in enumerate(self._cols) if i != c]
        ok, info = TE.check_col_name(new_name, others, negative=bool(col.get("neg")))
        if not ok:
            return False, info
        if info == col["name"]:
            return True, info                                  # 没变 → 不占一步撤销
        self._undo.push(RenameCol(self, c, col["name"], info))
        return True, info

    def add_negatives(self, cs, all_positive=False):
        """加反例（C-096…C-101）。挑列与去重全在 `truth_edit.plan_negatives`。返回 (造了几条, 跳过几条)。"""
        if not self.editable_kind():
            return 0, 0
        new, skipped = ED.add_negatives(self._cols, list(cs or ()), bool(all_positive))
        if new:
            self._undo.push(AddCols(self, len(self._cols), new, "加反例"))
        return len(new), skipped

    def del_negatives(self):
        """删掉全部反例、保留正向（C-102）。返回删了几条。"""
        items = [(i, c) for i, c in enumerate(self._cols) if c.get("neg")]
        if not items:
            return 0
        self._undo.push(RemoveCols(self, items, "删反例"))
        return len(items)

    def fill_expected(self):
        """auto_out → 期望：一次填进所有未手填的正向列，已填的不动（C-094）。返回填了几条。"""
        want = {id(c) for c in ED.fill_targets(self._cols)}
        cells = []
        exp_r = len(self._e_inputs) + 1
        for i, col in enumerate(self._cols):
            if id(col) in want:
                cells.append((exp_r, i, self._get_cell(exp_r, i),
                              (int(col.get("auto") or 0), bool(col.get("neg")))))
        if not cells:
            return 0
        self._undo.push(SetCells(self, cells, "auto→期望"))
        return len(cells)

    def clear_all(self):
        """清零本信号 = 零用例（C-089/C-118）。列全删，一步撤销；行不动（I-15 不 reset）。"""
        if not self._cols:
            return
        items = list(enumerate(self._cols))
        self._undo.push(RemoveCols(self, items, "清零"))

    def regenerate(self, an, e_inputs):
        """重新生成：丢弃自定义、按当前覆盖度重出真值表（C-085/C-119）。一步撤销。

        输入行数变了（换了信号 / 覆盖度改变了 mux 的 used_vars）就只能走 `load()` —— 那是
        I-15 允许 reset 的唯一出口，撤销栈随之清空（此时也没有「撤回上一个信号」这回事）。
        """
        e_inputs = list(e_inputs or [])
        new_cols = ED.cols_from_vectors(an, e_inputs)
        if len(e_inputs) != len(self._e_inputs):
            self.load(an, new_cols, e_inputs)
            return
        self._an = an or {}
        self._e_inputs = e_inputs
        self._undo.beginMacro("重新生成")
        if self._cols:
            self._undo.push(RemoveCols(self, list(enumerate(self._cols)), "清空旧列"))
        if new_cols:
            self._undo.push(AddCols(self, 0, new_cols, "按覆盖度重出"))
        self._undo.endMacro()

    # ═════════════════ mux 专用写入口 ═════════════════
    def set_mux_data_value(self, base_low, text):
        """整表 by_base 同步 mux 数据值（C-110/C-112）。

        **C3-a 收尾实现**：它要经 `state.mux_data` 存会话档 → `provider.analyze` 重分析 →
        `edits.mux_resync_cols` 把冻结的编辑列贴回新分析，model 手上没有 state/provider，
        这一波接不上（接上之前宁可不做，也不做个只改一格的假同步）。
        """
        raise NotImplementedError("C3-a：整表 mux 数据值同步要 state.mux_data + provider 重分析")

    def set_mux_user_data(self, c, key, text):
        """mux【手编列】数据值：只改本列该数据源 + 按路由 case 重算 auto_out（C-111）。一步撤销。"""
        if not (0 <= c < len(self._cols)) or self.editable_kind() != "mux":
            return False
        col = self._cols[c]
        if col.get("vec") is None:
            return False
        r = next((i for i, e in enumerate(self._e_inputs) if e["key"] == key), None)
        if r is None:
            return False
        try:
            val = TE.parse_int(text)
        except ValueError:
            self.parseFailed.emit(_t("TRUTH_PARSE_FAILED_FMT", text=text))
            return False
        new = val & _mask(self._e_inputs[r]["width"])
        old = self._get_cell(r, c)
        if old != new:
            self._undo.push(SetCells(self, [(r, c, old, new)], "改本列 mux 数据值"))
        return True

    # ═════════════════ 当前列 / 选区 / 剪贴板 ═════════════════
    def set_current_col(self, c):
        """当前列高亮（C-107）：只重绘一进一出两列，不动别的（列宽不弹回靠的就是这个）。"""
        c = int(c)
        if c == self._cur_col:
            return
        old, self._cur_col = self._cur_col, c
        last = self.rowCount() - 1
        for x in (old, c):
            if 0 <= x < len(self._cols):
                self.dataChanged.emit(self.index(0, x), self.index(last, x),
                                      [int(contracts.TruthRole.IS_CURRENT_COL)])
                self.headerDataChanged.emit(Qt.Horizontal, x, x)

    def copy_tsv(self, indexes):
        """选区 → TSV（C-293/C-294 的复制半边）：按选区的行列**外接矩形**出，空格子留空串。"""
        cells = {}
        for ix in (indexes or ()):
            if ix is None or not ix.isValid():
                continue
            cells[(ix.row(), ix.column())] = self.data(ix, Qt.DisplayRole) or ""
        if not cells:
            return ""
        rs = [r for (r, _c) in cells]
        cs = [c for (_r, c) in cells]
        lines = []
        for r in range(min(rs), max(rs) + 1):
            lines.append("\t".join(cells.get((r, c), "")
                                   for c in range(min(cs), max(cs) + 1)))
        return "\n".join(lines)

    def paste_tsv(self, text, r0, c0):
        """TSV 粘贴（C-294）：落在活动格 (r0, c0)；超列自动追加列；**超行拒绝**；整次一步撤销。

        超行为什么拒绝而不是追加：真值表的行是【输入信号】，凭空加一行等于凭空多一根输入，
        那是 Excel 表的事，不是这里能补的。只读格（auto 行 / 只读输入行 / DFT 拍列）跳过不写。
        """
        rows = [ln.split("\t") for ln in str(text or "").replace("\r\n", "\n")
                .replace("\r", "\n").split("\n")]
        while rows and rows[-1] == [""]:
            rows.pop()
        empty = _t("TRUTH_PASTE_REPORT_FMT", n=0, added="", skipped="")
        if not rows:
            self.pasteReport.emit(empty)
            return True, empty
        r0, c0 = max(0, int(r0)), max(0, int(c0))
        if r0 + len(rows) > self.rowCount():
            msg = _t("TRUTH_PASTE_OVERFLOW_ROWS_FMT", rows=len(rows), max=self.rowCount())
            self.pasteReport.emit(msg)
            return False, msg

        need = c0 + max(len(x) for x in rows) - len(self._cols)
        if need > 0:                       # 追加列 + 落值 = 两个命令 → 必须裹成一步（C-294）
            self._undo.beginMacro("粘贴")
            added = self.append_test_column(int(need))
            n_ok, n_skip, cells = self._paste_cells(rows, r0, c0)
            if cells:
                self._undo.push(SetCells(self, cells, "粘贴取值"))
            self._undo.endMacro()
        else:                              # 只落值 = 本来就一个命令，不用空宏占一步撤销
            added = []
            n_ok, n_skip, cells = self._paste_cells(rows, r0, c0)
            if cells:
                self._undo.push(SetCells(self, cells, "粘贴取值"))

        msg = _t("TRUTH_PASTE_REPORT_FMT", n=n_ok,
                 added=(_t("TRUTH_PASTE_ADDED_FMT", names=", ".join(added)) if added else ""),
                 skipped=(_t("TRUTH_PASTE_SKIPPED_FMT", n=n_skip) if n_skip else ""))
        self.pasteReport.emit(msg)
        return True, msg

    def _paste_cells(self, rows, r0, c0):
        """把 TSV 二维表折算成 `SetCells` 的清单。返回 (落了几格, 跳过几格, cells)。"""
        n_ok, n_skip, cells = 0, 0, []
        for dr, line in enumerate(rows):
            for dc, txt in enumerate(line):
                r, c = r0 + dr, c0 + dc
                if not self._is_editable(r, c):
                    n_skip += 1
                    continue
                v = self._parse_for_cell(r, c, txt)
                if v is _BAD:
                    n_skip += 1
                    continue
                old = self._get_cell(r, c)
                if old != v:
                    cells.append((r, c, old, v))
                n_ok += 1
        return n_ok, n_skip, cells

    def _parse_for_cell(self, r, c, txt):
        """粘贴用的单格解析：认不出来 → `_BAD`（那一格跳过，整次粘贴照常落别的格）。"""
        col = self._cols[c]
        if self.row_kind(r) == contracts.TruthRowKind.EXP:
            if str(txt).strip() == "":
                return (None, False)
            try:
                return (TE.parse_int(txt) & _mask(col.get("auto_w") or 1),
                        bool(col.get("neg")))
            except ValueError:
                return _BAD
        try:
            return TE.parse_int(txt) & _mask(self._e_inputs[r]["width"])
        except ValueError:
            return _BAD

    # ═════════════════ 批量填期望（C-298 的 model 面）═════════════════
    def apply_expectations(self, by_name):
        """按【列名】回填期望（C-298 的内部形式）。返回 (落了几列, 没对上的列名)。一步撤销。

        读 CSV / xlsx 那半边在 `truth/io.py`（C3-d），本方法只收 `{列名: int}`——
        model 不碰文件，io 不碰 undo 栈。列名大小写无关，按【最终标号】对位。
        """
        idx_of = {}
        for i, nm in enumerate(self.all_names()):
            idx_of.setdefault(str(nm).strip().upper(), i)
        exp_r = len(self._e_inputs) + 1
        cells, missing = [], []
        for nm, val in (by_name or {}).items():
            i = idx_of.get(str(nm).strip().upper())
            if i is None:
                missing.append(str(nm))
                continue
            if not self._is_editable(exp_r, i):
                missing.append(str(nm))
                continue
            col = self._cols[i]
            new = (int(val) & _mask(col.get("auto_w") or 1), bool(col.get("neg")))
            old = self._get_cell(exp_r, i)
            if old != new:
                cells.append((exp_r, i, old, new))
        if cells:
            self._undo.push(SetCells(self, cells, "导入期望"))
        return len(cells), missing

    def import_expectations(self, path):
        """从 CSV / xlsx 按列名导入期望（C-298）。**C3-d 的 `truth/io.py` 实现**：
        读文件要 openpyxl 惰性加载 + 列名模糊匹配提示，那是 io 的事；读完调 `apply_expectations`。"""
        raise NotImplementedError("C3-d io：读 CSV/xlsx 在 truth/io.py，读完调 apply_expectations")

    def batch_fill(self, cs, text):
        """把同一个值批量填进选中列的期望格（C-298 的「批量填…」）。返回 (落了几列, 提示文本)。"""
        try:
            val = TE.parse_int(text)
        except ValueError:
            msg = _t("TRUTH_PARSE_FAILED_FMT", text=text)
            self.parseFailed.emit(msg)
            return 0, msg
        exp_r = len(self._e_inputs) + 1
        cells = []
        for i in sorted({int(x) for x in (cs or ()) if 0 <= int(x) < len(self._cols)}):
            if not self._is_editable(exp_r, i):
                continue
            col = self._cols[i]
            new = (val & _mask(col.get("auto_w") or 1), bool(col.get("neg")))
            old = self._get_cell(exp_r, i)
            if old != new:
                cells.append((exp_r, i, old, new))
        if cells:
            self._undo.push(SetCells(self, cells, "批量填期望"))
        return len(cells), _t("TRUTH_BATCH_FILL_REPORT_FMT", n=len(cells))


class _Bad(object):
    """「这一格解析不出来」的哨兵（`None` 是期望格的合法值，不能拿它当失败标记）。"""

    def __repr__(self):
        return "<parse-failed>"


_BAD = _Bad()
