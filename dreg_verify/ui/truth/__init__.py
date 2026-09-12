# -*- coding: utf-8 -*-
"""dreg_verify.ui.truth —— 真值表编辑器（GUI v2 的 C3 波）。

模块分工（架构 §6.12–§6.15）：
    rows.py      输入行构造 `e_inputs_from_an`（Qt-free）
    commands.py  四个 `QUndoCommand`：SetCells / AddCols / RemoveCols / RenameCol
    model.py     `TruthModel`（`contracts.TruthModelProto`），I-15 的唯一保证方
    view.py      `TruthTableView` + `FrozenNamesView` + `TruthDelegate`   （C3-b）
    panel.py     `TruthPanel`：工具条 / 提示条 / mux 头部 / 右键菜单        （C3-c）
    io.py        复制粘贴解析 / CSV·xlsx 导入期望 / 单信号 CSV 导出        （C3-d）

本包只许 import：PySide6、Qt-free 层（edits / truth_edit / inputs_table / analysis_norm）、
`ui.contracts / names / terms / theme`（theme 只给 view 与 delegate 用，model 不碰）。
引擎模块（topout / pageviews / generator / sigflow / mux_gen / vectors …）与旧门面 gui.py
一概不许 import —— `tests/test_ui_c1_integration.py::test_ui_layering` 会扫。
"""

from .model import TruthModel
from .panel import TruthPanel
from .rows import e_inputs_from_an

__all__ = ["TruthModel", "TruthPanel", "e_inputs_from_an"]
