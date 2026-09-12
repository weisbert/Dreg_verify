# -*- coding: utf-8 -*-
"""dreg_verify.ui —— GUI v2 工作台（PySide6）。

包结构与依赖方向见 docs/GUI_v2_架构_20260912.md §1：
    views（app / signal_list / truth / sigflow_view / …）
      → ui 状态层（state / persist / bus / worker）
        → Qt-free 层（session / edits / inputs_table / analysis_norm / exports / providers）
          → 引擎（topout / pageviews / sigflow / generator …）
views 之间不互相 import，只经 state 的信号与 bus 通信。

本包里 Qt-free 的四个模块（contracts / names / theme / terms）不 import PySide6，
tools/ 与测试可以在无 Qt 环境下读它们。
"""
