# -*- coding: utf-8 -*-
"""truth/commands.py —— 真值表的四个 `QUndoCommand`（I-15 / C-299）。

**这四个是 `TruthModel` 改数据的唯一途径**：model 对外的写入口（`setData` 与各列操作方法）
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

__all__ = ["SetCells", "AddCols", "RemoveCols", "RenameCol"]


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
        self._cols = self._m._take_cols(range(self._at, self._at + len(self._cols)))


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
