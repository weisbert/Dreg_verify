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
from .commands import AddCols, MuxData, Regenerate, RemoveCols, RenameCol, SetCells
from .rows import e_inputs_from_an

__all__ = ["TruthModel", "fmt_cell", "norm_col", "COL_SCHEMA"]


#: ⚠ C3-int 起本模块**不再有 `PENDING_TERMS` 影子表**：文案只在 `ui/terms.py`
#: （`terms.TRUTH_*`），粘贴 / 导入期望的**结果说明**整句在 `truth/io.py` 拼
#: （`paste_report_text` / `import_report_text`），model 与面板共用同一份。


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


#: 列 dict 的 schema（键 → 缺省值）。`edits.cols_from_vectors` 九个键全给，但 `edits.add_col`
#: 与 `edits.copy_cols` **不给 `dft`**（它们造的列本来就不是 iddq 自检拍）——两种来源的列混在
#: 一张表里，`cols() == cols()` 这种逐字段比较、`state.put_edit` 存下来再读回就会各差一个键。
#: 进 model 的列一律先 `norm_col` 补齐（就地补，不换对象：`AddCols.undo` 靠的是同一批 dict 的身份）。
COL_SCHEMA = {"name": "", "neg": False, "vals": None, "exp": None, "auto": 0,
              "auto_w": 1, "user": False, "vec": None, "dft": False}


def norm_col(col):
    """就地补齐一个列 dict 的 schema 键并返回它本身（不拷贝、不换对象）。"""
    for k, v in COL_SCHEMA.items():
        if k not in col:
            col[k] = {} if (k == "vals" and v is None) else v
    if col["vals"] is None:
        col["vals"] = {}
    return col


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
        self._reanalyze = None      # set_reanalyzer 接进来的「写 state + 重分析」回路（C-110）
        self._mux_text = {}         # (信号名低, 物理基名) → 本会话最后填进去的规范化文本（撤销要）
        self._vecs = None           # column_drives 的向量快照缓存（每次写后作废，C-063）

    # ═════════════════ 形状 ═════════════════
    def load(self, an, cols, e_inputs):
        """换信号 / 重新生成：**唯一允许 reset 的地方**（I-15）。

        `cols` 按引用收下（列 dict 与其中的 `vec` 与调用方共享，与 v1 `SignalView` 同——
        `state.put_edit` 存的就是这份），只把外层 list 拷一份，免得改列顺序反噬调用方；
        列 dict 就地补齐 `COL_SCHEMA`（见那张表的注释）。
        """
        self.beginResetModel()
        self._an = an or {}
        self._e_inputs = list(e_inputs or [])
        self._cols = [norm_col(c) for c in (cols or [])]
        self._cur_col = -1
        self._vecs = None
        self._undo.clear()          # 本类唯一的 .clear()：撤销栈，不是表格（I-15 说的是后者）
        self.endResetModel()
        self.colsChanged.emit()
        self._emit_progress()

    def set_reanalyzer(self, fn):
        """接上「改 mux 数据值 → 写 `state.mux_data` 桶 → 重分析本信号」这条回路（C-110/C-112）。

        契约见 `contracts.TruthModelProto.set_reanalyzer`：`fn(base_low, text) -> an | None`，
        空 text = 恢复自动分配；text 已按该数据行位宽校验+掩码+`fmt_cell` 规范化过。
        没接（`None`）时 `set_mux_data_value` 只发一条 `parseFailed` 说明并返回 False，不动表。
        """
        self._reanalyze = fn

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
        """该列实际下的每条 force / RF_WRITE（C-063 列头 tooltip / 常驻面板）。

        **不能用 `inputs_table.column_drives(an, c)`**：它按 `an["vectors"][c]` 取向量，
        而 `an["vectors"]` 是【出厂】那一批；加列 / 删列 / 复制列之后第 c 列早就不是第 c 条
        向量了，tooltip 会串号说别的列的 force（C3-a0 报告 #1）。这里按【当前列】现算。
        """
        if not (0 <= c < len(self._cols)):
            return []
        bindings, used = IT.drive_ctx(self._an)
        if used is None:
            return []
        vec = self._col_vectors()[c]
        if vec is None:
            return []
        return IT.vector_drives(vec, bindings, used)

    def _col_vectors(self):
        """与【当前列】一一对应的测试向量（`column_drives` 的原料，写操作后作废重算）。

        logic 根按当前取值重建（`edits.cols_to_vectors` —— 与 `edits.compute_edited` 同一条
        路，所以 tooltip 说的驱动就是真会写进 .sv 的那几条）；mux 根与只读信号直接用列自己的
        `vec`（mux 的取值归 `vec.assignments` 管，重建反而丢 case 身份）。

        `vectors.make_vector_from_base_values` 不建模 iddq 门，重建出来的向量没有门那条
        `extra_forces`；门行只读、值只能来自原向量，所以从原列的 `vec` 上原样搬过来。
        新加的列没有出厂向量，但门值就在它自己的 `vals` 里（门行只读，加列时按 0 起），
        照它补一条——不补的话 tooltip 会少说一条 force，而生成器 `pin_dft_gate` 出片时是会补的。
        """
        if self._vecs is not None:
            return self._vecs
        if self.editable_kind() == "logic":
            gate = self._an.get("dft_gate")
            vecs = ED.cols_to_vectors(self._an, self._cols)
            for col, v in zip(self._cols, vecs):
                ef = list(getattr(col.get("vec"), "extra_forces", None) or [])
                if not ef and gate:
                    ef = [(gate["wire_lhs"],
                           int((col.get("vals") or {}).get(gate["key"], gate["transp"])),
                           int(gate.get("width") or 1))]
                if ef:
                    v.extra_forces = ef
        else:
            vecs = [c.get("vec") for c in self._cols]
        self._vecs = vecs
        return vecs

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
        self._vecs = None
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
        self._cols[at:at] = [norm_col(c) for c in cols]
        self.endInsertColumns()
        self._vecs = None
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
        self._vecs = None
        self.colsChanged.emit()
        self._emit_progress()
        return taken

    def _set_col_name(self, c, name):
        if not (0 <= c < len(self._cols)):
            return
        self._cols[c]["name"] = name
        self._vecs = None                      # 列名进 vec.name → 驱动明细的原料也变了
        self.headerDataChanged.emit(Qt.Horizontal, c, c)
        self.colsChanged.emit()
        self._emit_progress()

    def _apply_shape(self, an, e_inputs, cols):
        """整体换 `an` + 输入行 + 列集（`Regenerate` / `MuxData` 的落地口）。**行数必须不变**。

        I-15：不 reset。列数差多少就发多少条 insert / remove，剩下的靠整表 `dataChanged`。
        """
        self._an = an or {}
        self._e_inputs = list(e_inputs or [])
        old_n, new_n = len(self._cols), len(cols)
        if new_n < old_n:
            self.beginRemoveColumns(QModelIndex(), new_n, old_n - 1)
            self._cols = [norm_col(c) for c in cols]
            self.endRemoveColumns()
        elif new_n > old_n:
            self.beginInsertColumns(QModelIndex(), old_n, new_n - 1)
            self._cols = [norm_col(c) for c in cols]
            self.endInsertColumns()
        else:
            self._cols = [norm_col(c) for c in cols]
        self._vecs = None
        if new_n:
            self.dataChanged.emit(self.index(0, 0),
                                  self.index(self.rowCount() - 1, new_n - 1))
            self.headerDataChanged.emit(Qt.Horizontal, 0, new_n - 1)
        self.colsChanged.emit()
        self._emit_progress()

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
                    self.parseFailed.emit(terms.TRUTH_PARSE_FAILED_FMT.format(text=txt))
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
            # 内部/导出值不一致 = 假的所见即所得）。与 v1 `_on_truth_item` 同：这一格的编辑
            # 直接触发整表同步；回路没接上时 set_mux_data_value 自己会说明并返回 False。
            return self.set_mux_data_value(e["mux_data_base"], text)
        try:
            val = TE.parse_int(text)             # 输入格空 = 0（没有「未填」这一档）
        except ValueError:
            self.parseFailed.emit(terms.TRUTH_PARSE_FAILED_FMT.format(text=text))
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
            return False, terms.TRUTH_COL_OUT_OF_RANGE
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
        """加反例（C-096…C-101 / mux 的 C-121·C-122）。返回 (造了几条, 跳过几条)。一步撤销。

        挑列与去重全在 `truth_edit.plan_negatives`（只对正向列造、同一组输入取值不叠第二条、
        列名对全集唯一），错值防撞在 `edits.add_negatives`（同时避开 auto_out 与手填期望，
        C-100）——logic 与 mux 走的是**同一条**路：mux 的「逐 case 加反例」（C-121）就是
        「逐正向列加反例」，因为 mux 的一列 = 一条 case；`all_positive=True` 即清单底部的
        「全部加反例」（C-122）。造出来的反例列 `user=True` 且 `vec` 是 case 的克隆，
        `edits.mux_derive` 会把它们收成带 `is_negative` 的 user_vecs。
        """
        if not self.editable_kind():
            return 0, 0
        new, skipped = ED.add_negatives(self._cols, list(cs or ()), bool(all_positive))
        if new:
            self._undo.push(AddCols(self, len(self._cols), new, "加反例"))
        return len(new), skipped

    def del_negatives(self):
        """删掉全部反例、保留正向（C-102 / mux 的 C-123）。返回删了几条。一步撤销。

        mux 的「手编反例列 + 整信号反例标记一起清」（C-123）在本层就是「把所有 `neg` 列删掉」：
        v2 没有第二份「整信号反例标记」——`state._sync_negs` 是从 `any(c["neg"] for c in cols)`
        推出来的，删空了它自然就落下去（这正是 v1 两份标记会不同步的那个洞被堵上的地方）。
        """
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
        self._undo.push(Regenerate(self,
                                   (self._an, self._e_inputs, list(self._cols)),
                                   (an or {}, e_inputs, new_cols)))

    # ═════════════════ mux 专用写入口 ═════════════════
    def set_mux_data_value(self, base_low, text):
        """整表 by_base 同步 mux 数据值（C-110/C-112/C-113/C-114）。一步撤销。返回是否落下去了。

        路径：按该数据行的位宽 `parse_int` 校验（空串 = 恢复自动分配）→ `set_reanalyzer` 接进来的
        回路写 `state.mux_data` 桶 + 重分析 → `edits.mux_resync_cols` 把【冻结的编辑列】贴回新
        分析（手填期望 / 反例标记按列名保住 = C-114；用户删过的自动列不复活；手编列原样留着）。
        撞值（`an["status_detail"] == "false-green"`）时补发一条 `muxCollision`（C-113）。

        只改一格是做不到「所见即所得」的（C-112）：数据值按【物理寄存器】整表分配，改一格而
        别的列不动，屏幕值与内部/导出值就会各说各话——所以这里要么整表同步，要么原样不动。
        """
        base_low = str(base_low or "").lower()
        if self.editable_kind() != "mux":
            return False
        e = next((x for x in self._e_inputs
                  if (x["mux_data_base"] or "") == base_low), None)
        if e is None:
            self.parseFailed.emit(terms.TRUTH_MUX_DATA_NO_BASE_FMT.format(base=base_low))
            return False
        txt = "" if text is None else str(text).strip()
        if txt == "":
            norm = ""                             # 空 = 清掉该基名、恢复引擎的自动互异分配
        else:
            try:
                norm = fmt_cell(TE.parse_int(txt), e["width"])
            except ValueError:
                self.parseFailed.emit(terms.TRUTH_PARSE_FAILED_FMT.format(text=txt))
                return False
        if self._reanalyze is None:
            self.parseFailed.emit(terms.TRUTH_MUX_DATA_NO_REANALYZER)
            return False
        old = self._mux_text.get(self._mux_key(base_low), "")
        if old == norm:
            return True                           # 没变 → 不占一步撤销，也不白重分析一遍
        self._undo.push(MuxData(self, base_low, old, norm))
        return True

    def _mux_key(self, base_low):
        return (str(self._an.get("name") or "").lower(), str(base_low or "").lower())

    def _apply_mux_data(self, base_low, text):
        """`MuxData` 的落地：经 reanalyzer 拿新 an → 贴回编辑列 → 换表（做和撤销同一条路）。"""
        if self._reanalyze is None:
            return
        an = self._reanalyze(base_low, text)
        if an is None:                            # 重分析失败：state 已由回路自己兜底，表不动
            return
        cols = ED.mux_resync_cols(an, self._cols, self._e_inputs)
        self._mux_text[self._mux_key(base_low)] = text
        self._apply_shape(an, self._e_inputs, cols)
        if text:
            self.pasteReport.emit(terms.TRUTH_MUX_DATA_DONE_FMT.format(base=base_low))
        # C-113：≥2 条数据路取到相同值 = 选错路也测不出。撞值判据在引擎的 meta
        # （`mux_gen` 的 value_collision / override_collision），`an` 里它已经归到
        # `status_detail == "false-green"` 这一档（见「引擎层发现」：an 不带 expansion["meta"]）。
        if an.get("status_detail") == "false-green":
            self.muxCollision.emit(terms.TRUTH_MUX_COLLISION)

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
            self.parseFailed.emit(terms.TRUTH_PARSE_FAILED_FMT.format(text=text))
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

        「规划」（`_paste_plan`，纯函数、不改表）与「落格」（`_paste_land`）刻意拆开：
        C3-d 会在 `truth/io.paste_plan` 写一份同规则的纯函数给视图预览用，C3-int 去重时
        直接把 `_paste_plan` 换成它就行，落格这半边不用动。
        """
        plan = self._paste_plan(text, r0, c0)
        if not plan["ok"]:
            self.pasteReport.emit(plan["report"])
            return False, plan["report"]
        if plan["rows"] is None:                       # 空剪贴板：照实报一句，不占撤销步
            self.pasteReport.emit(plan["report"])
            return True, plan["report"]
        msg = self._paste_land(plan)
        self.pasteReport.emit(msg)
        return True, msg

    def _paste_plan(self, text, r0, c0):
        """TSV + 落点 → 规划（**只读，不改任何东西**）。

        返回 `{"ok", "report", "rows", "r0", "c0", "need"}`：`ok=False` 时 `report` 就是拒绝
        原因（目前只有超行一种）；`rows is None` = 剪贴板是空的。`need` = 还差几列要追加。
        """
        rows = [ln.split("\t") for ln in str(text or "").replace("\r\n", "\n")
                .replace("\r", "\n").split("\n")]
        while rows and rows[-1] == [""]:
            rows.pop()
        r0, c0 = max(0, int(r0)), max(0, int(c0))
        if not rows:
            return {"ok": True, "rows": None, "r0": r0, "c0": c0, "need": 0,
                    "report": terms.TRUTH_PASTE_REPORT_FMT.format(n=0, added="", skipped="")}
        if r0 + len(rows) > self.rowCount():
            return {"ok": False, "rows": rows, "r0": r0, "c0": c0, "need": 0,
                    "report": terms.TRUTH_PASTE_OVERFLOW_ROWS_FMT.format(rows=len(rows), max=self.rowCount())}
        need = max(0, c0 + max(len(x) for x in rows) - len(self._cols))
        return {"ok": True, "rows": rows, "r0": r0, "c0": c0, "need": need, "report": ""}

    def _paste_land(self, plan):
        """把规划落进表里（追加列 + 一批格子），整次一步撤销（C-294）。返回结果说明。"""
        rows, r0, c0, need = plan["rows"], plan["r0"], plan["c0"], plan["need"]
        # 加不出列时**别开宏**：`beginMacro` 一调就压进撤销栈了，空宏照样占一步
        # （用户看到的是「什么都没发生，但 Ctrl+Z 得按两下」）。
        if need > 0 and self._can_append():   # 追加列 + 落值 = 两个命令 → 裹成一步（C-294）
            self._undo.beginMacro("粘贴")
            added = self.append_test_column(int(need))
            cells, tally = self._paste_cells(rows, r0, c0)
            if cells:
                self._undo.push(SetCells(self, cells, "粘贴取值"))
            self._undo.endMacro()
        else:                              # 只落值 = 本来就一个命令，不用空宏占一步撤销
            added = []
            cells, tally = self._paste_cells(rows, r0, c0)
            if cells:
                self._undo.push(SetCells(self, cells, "粘贴取值"))
        # 加不出那么多列时说清**为什么**（mux 清零过就没有 case 可克隆了，不是「只读」）
        why = ("TRUTH_PASTE_NO_NEW_COL_MUX" if self.editable_kind() == "mux"
               else "TRUTH_PASTE_NO_NEW_COL")
        skipped = ""
        if tally["ro"]:
            skipped += terms.TRUTH_PASTE_SKIPPED_FMT.format(n=tally["ro"])
        if tally["no_col"]:
            skipped += terms.TRUTH_PASTE_NO_COL_FMT.format(n=tally["no_col"], why=getattr(terms, why))
        if tally["mux_whole"]:
            skipped += terms.TRUTH_PASTE_MUX_WHOLE_FMT.format(n=tally["mux_whole"])
        if tally["bad"]:
            names = tally["bad"][:_BAD_NAMES_MAX]
            skipped += terms.TRUTH_PASTE_BAD_FMT.format(
                n=len(tally["bad"]),
                names="、".join(names) + ("…" if len(tally["bad"]) > _BAD_NAMES_MAX else ""))
        return terms.TRUTH_PASTE_REPORT_FMT.format(
            n=tally["ok"],
            added=(terms.TRUTH_PASTE_ADDED_FMT.format(names=", ".join(added)) if added else ""),
            skipped=skipped)

    def _can_append(self):
        """现在还加得出测试列吗（`append_test_column` 会不会白跑一趟）。

        mux 的「加列」= 克隆一条 case（C-115，不能凭空造输入），所以清零过 = 零用例时
        一条都克隆不出来；logic 的 `edits.add_col` 任何时候都造得出（输入全 0）。
        """
        kind = self.editable_kind()
        if not kind:
            return False
        return bool(self._cols) if kind == "mux" else True

    def _is_mux_auto_data_cell(self, r, c):
        """这一格是不是【自动生成列】的 mux 数据值格（C-110：只能整表一起改）。

        `flags()` 上它照旧可编辑 —— 单格编辑由 `setData` 路由去整表同步（与 v1 同）。但
        **粘贴不能走那条路**：一片格子逐个触发整表重分析，后一格把前一格覆盖掉，落下来的是
        「所有自动列都等于最后一格」这种胡来的结果，而且屏幕值与导出值会当场分家（C-112）。
        所以粘贴按「跳过并点名原因」办。
        """
        if self.editable_kind() != "mux":
            return False
        if not (0 <= c < len(self._cols)) or not (0 <= r < len(self._e_inputs)):
            return False
        return bool(self._e_inputs[r]["mux_data_base"]) and not self._cols[c].get("user")

    def _paste_cells(self, rows, r0, c0):
        """把 TSV 二维表折算成 `SetCells` 的清单。返回 `(cells, tally)`。

        `tally` = `{"ok", "ro", "no_col", "mux_whole", "bad": [格名…]}` —— 跳过的格子按
        **原因分桶**，结果说明逐条点名（护栏：跳过必有名字 + 原因）。C-083 的粘贴面：
        认不出写法的格子报行标签×列名，不是只报个数——一次粘 150 格、报「跳过 3 格」
        等于让人自己去找那三格在哪。
        """
        cells = []
        tally = {"ok": 0, "ro": 0, "no_col": 0, "mux_whole": 0, "bad": []}
        for dr, line in enumerate(rows):
            for dc, txt in enumerate(line):
                r, c = r0 + dr, c0 + dc
                if not (0 <= c < len(self._cols)):
                    tally["no_col"] += 1
                    continue
                if self._is_mux_auto_data_cell(r, c):
                    tally["mux_whole"] += 1
                    continue
                if not self._is_editable(r, c):
                    tally["ro"] += 1
                    continue
                v = self._parse_for_cell(r, c, txt)
                if v is _BAD:
                    tally["bad"].append(terms.TRUTH_PASTE_BAD_CELL_FMT.format(row=self.row_label(r),
                                           col=self.all_names()[c]))
                    continue
                old = self._get_cell(r, c)
                if old != v:
                    cells.append((r, c, old, v))
                tally["ok"] += 1
        return cells, tally

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
        """从 CSV / xlsx 按列名导入期望（C-298）。返回 `(落了几列, 没对上的列名, 结果说明)`。

        读文件那半边在 `truth/io.py`（C3-d：openpyxl 惰性加载 + 列名模糊匹配提示）——**惰性
        import**，io 还没落地时抛 `NotImplementedError` 而不是 `ImportError`（面板据此置灰按钮，
        也免得 model 在 C3-d 之前就 import 不动）。model 只负责把结果交给 `apply_expectations`。

        ⚠ 结果说明那一句由 `io.import_report_text` 拼（C3-int 去重）：面板的「导入期望…」
        走的是同一份，所以 model 与界面上看到的一字不差，且都是 **I-20 的名字在前**。
        """
        try:
            from . import io as TIO           # noqa: PLC0415  惰性：C3-d 还没落地时也不炸
        except ImportError:
            TIO = None
        read = getattr(TIO, "read_expectations", None) if TIO is not None else None
        if read is None:
            raise NotImplementedError(
                "C3-d io：读 CSV/xlsx 在 truth/io.py，读完调 apply_expectations")
        try:
            got = read(path)
        except OSError as ex:              # 不存在 / 没权限 / 被 Excel 占用 —— 照实说，别崩
            msg = terms.TRUTH_IMPORT_READ_FAILED_FMT.format(err=ex)
            self.parseFailed.emit(msg)
            return 0, [], msg
        # io 回的是 `(by_name, notes)`：by_name 已经是**解析好的 int**（表里写的是 8 种写法，
        # `int()` 一个都不认，文本→值的翻译归读文件那一侧）；notes 是「这一列写法没认出来 /
        # 列名重了取了哪一处 / 没找到期望行」之类的逐条提示，原样接进结果说明（跳过必有原因）。
        by_name, notes = got if isinstance(got, tuple) else (got, [])
        n, missing = self.apply_expectations(by_name)
        return n, missing, TIO.import_report_text(n, missing, notes)

    def batch_fill(self, cs, text):
        """把同一个值批量填进选中列的期望格（C-298 的「批量填…」）。返回 (落了几列, 提示文本)。"""
        try:
            val = TE.parse_int(text)
        except ValueError:
            msg = terms.TRUTH_PARSE_FAILED_FMT.format(text=text)
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
        return len(cells), terms.TRUTH_BATCH_FILL_REPORT_FMT.format(n=len(cells))


class _Bad(object):
    """「这一格解析不出来」的哨兵（`None` 是期望格的合法值，不能拿它当失败标记）。"""

    def __repr__(self):
        return "<parse-failed>"


_BAD = _Bad()

#: 粘贴结果说明里最多点名几个「没认出写法」的格子（再多就 …，一行提示条塞不下）
_BAD_NAMES_MAX = 6
