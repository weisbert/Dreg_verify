# -*- coding: utf-8 -*-
"""truth/commands.py —— 真值表的六个 `QUndoCommand`（I-15 / C-299）。

**这六个是 `TruthModel` 改数据的唯一途径**：model 对外的写入口（`setData` 与各列操作方法）
一律只造命令 `push` 进 `undo_stack()`，自己不直接改 `cols`。`redo/undo` 两边都调 model 的
内部写函数（`_apply_cells` / `_insert_cols` / `_take_cols` / `_set_col_name`），所以「做」和
「撤销」走的是同一条代码路径——不会出现「撤销撤了个半拉」。

一次用户动作 = 一个命令（多格/多列的批量在命令内部就是一张清单）；跨方法的组合动作
（粘贴、重新生成）由 model 用 `beginMacro/endMacro` 裹成一个。两种写法都保证
`undo_stack().count()` 恰好 +1（`test_c299_every_write_is_one_undo_step` 逐操作钉死）。

单元格值的内部表示（`_apply_cells` 的 `v`，`model._get_cell/_set_cell` 是它的唯一出入口）：
  · 输入行 → `int`（已按该行位宽 mask 过）
  · 期望行 → `(exp, neg)` 二元组——期望清空时 v1 同时把负向标记也清掉（C-081），
              两件事必须一起撤销，否则 Ctrl+Z 之后列还留着「反例」身份但错值没了。
"""

from PySide6.QtGui import QUndoCommand

__all__ = ["SetCells", "AddCols", "RemoveCols", "RenameCol", "MuxData", "Regenerate"]


class SetCells(QUndoCommand):
    """改一批单元格的值。`cells` = [(row, col, old, new)]，值的表示见模块 docstring。"""

    def __init__(self, model, cells, text="设置单元格"):
        QUndoCommand.__init__(self, text)
        self._m = model
        self._cells = [(int(r), int(c), o, n) for (r, c, o, n) in cells]

    def redo(self):
        self._m._apply_cells([(r, c, n) for (r, c, _o, n) in self._cells])

    def undo(self):
        self._m._apply_cells([(r, c, o) for (r, c, o, _n) in self._cells])


class AddCols(QUndoCommand):
    """在 `at` 处插入若干列（加列 / 复制列 / 加反例 / 粘贴追加列共用）。"""

    def __init__(self, model, at, cols, text="加列"):
        QUndoCommand.__init__(self, text)
        self._m = model
        self._at = int(at)
        self._cols = list(cols)

    def redo(self):
        self._m._insert_cols(self._at, self._cols)

    def undo(self):
        # 收回的就是刚插进去的那几个对象本身（同一批 dict，vec 也是同一个）——
        # 重做时再插回去，用户的手填期望/负向错值原样还在。
        #
        # R2-10：按**对象身份**找，不按下标。中间来过一次 `MuxData`（整表换列）之后
        # 列数可能已经缩了，`range(at, at+n)` 越界 → `_take_cols` 把越界的过滤掉 →
        # 返回空 → `self._cols` 变成 []，这一步撤销**静默变成空操作**，而且重做也没得插了
        # （fuzz seed=5 N=194 抓到的就是这条）。找不到（列集被整个换过）就保住手上这份，
        # 宁可撤销这一步没动静，也不能把用户那几列弄丢。
        taken = self._m._take_cols(self._m._indexes_of(self._cols))
        if taken:
            self._cols = taken


class RemoveCols(QUndoCommand):
    """删列（删列 / 删反例 / 清零共用）。`items` = [(下标, 列 dict)]，下标按原表升序。"""

    def __init__(self, model, items, text="删列"):
        QUndoCommand.__init__(self, text)
        self._m = model
        self._items = sorted(((int(i), c) for (i, c) in items), key=lambda x: x[0])

    def redo(self):
        self._m._take_cols([i for (i, _c) in self._items])

    def undo(self):
        for i, c in self._items:            # 升序插回：每次插入都让后面的下标复位
            self._m._insert_cols(i, [c])


class RenameCol(QUndoCommand):
    """改列名（C-091/C-092/C-093）。`old`/`new` 都是【最终标号】（负向列已带 _NEG）。"""

    def __init__(self, model, c, old, new, text="重命名列"):
        QUndoCommand.__init__(self, text)
        self._m = model
        self._c = int(c)
        self._old = old
        self._new = new

    def redo(self):
        self._m._set_col_name(self._c, self._new)

    def undo(self):
        self._m._set_col_name(self._c, self._old)


class MuxData(QUndoCommand):
    """整表 mux 数据值同步（C-110/C-112）：一次动作 = 写 state 桶 + 重分析 + 整表换列，一步撤销。

    **做和撤销都只记『那一格该填什么文本』**（`old`/`new`，空串 = 恢复自动分配），两边都调
    `model._apply_mux_data` 经同一个 reanalyzer 走一遍——所以 undo 之后 `state.mux_data` 里
    存的值与表上显示的值不会各说各话（只把列 dict 塞回去的话，state 还留着新值 = 屏幕 0x3 /
    导出 9 的老毛病，正是 C-112「所见即所得」要堵的那个洞）。

    ⚠ R2-09：光有「那一格该填什么文本」**撤不回来**。`_apply_mux_data` 的落地要经
    `edits.mux_resync_cols`，而那一步是**有损**的：老模型里有、新分析里没有的自动列
    （换过覆盖度档、冻结下来的那些）会被丢掉，撤销时再 resync 一遍也变不回来 ——
    实测「全面档手填 5 条期望共 25 列 → 换精简档 → 改一次数据值」之后 25 列变 9 列、
    期望剩 2 条，Ctrl+Z 撤不回，而那时已经落了盘。所以这里**连做之前的完整列集一起存**
    （与 `Regenerate` 同一个办法），撤销时把它原样装回去；`state.mux_data` 那一格
    仍然经 reanalyzer 改回旧文本，两边不会各说各话。
    """

    def __init__(self, model, base_low, old, new, text="设置 mux 数据值"):
        QUndoCommand.__init__(self, text)
        self._m = model
        self._base = str(base_low)
        self._old = str(old or "")
        self._new = str(new or "")
        self._before = None          # (an, e_inputs, cols)：做之前的完整形状

    def redo(self):
        self._before = self._m._shape_snapshot()
        self._m._apply_mux_data(self._base, self._new)

    def undo(self):
        self._m._apply_mux_data(self._base, self._old, shape=self._before)


class Regenerate(QUndoCommand):
    """重新生成（C-085/C-119）：`an` + 输入行 + 整个列集一起换，一步撤销。

    撤销必须连 `an` 一起还原——`cell_state` / `col_state` / `column_drives` / auto_out 重算
    全看 `an`，只把列 dict 塞回去的话，撤销后的表会拿【新 an】去解释【旧列】（DFT 拍列认不出、
    驱动明细算错），静默不一致。行数变了不走这里（那要 reset，是 `load()` 的活，I-15）。
    """

    def __init__(self, model, old, new, text="重新生成"):
        QUndoCommand.__init__(self, text)
        self._m = model
        self._old = old          # (an, e_inputs, cols)
        self._new = new

    def redo(self):
        self._m._apply_shape(*self._new)

    def undo(self):
        self._m._apply_shape(*self._old)
