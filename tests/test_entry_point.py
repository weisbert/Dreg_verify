# -*- coding: utf-8 -*-
"""入口两条（GUI v2 C5-c1，架构 §3 I-17）：

  · C-251 `python -m dreg_verify.gui` 入口不变 —— 但落点已经是 **v2 组合根**：
    薄壳 `dreg_verify/gui.py` 的 `main` 就是 `dreg_verify.ui.app.main` 这个**同一个对象**，
    且 import 薄壳（不是当 `__main__` 跑）**不起任何窗口**。
    退役的 v1 门面搬到 `dreg_verify.legacy_gui`，验收期内照样能 import、能起窗。
  · C-004 命令行带一张 .xlsx → 直接进工作台（不停在空态），清单**当场**有行。

与已有用例的分工（同 ID 不重复造）：
  · `test_ui_app.py::test_c251_module_entry_provides_main` 盯的是 `ui.app` 自己有 main；
    这里盯的是**薄壳转发**那一跳（C5-c1 新增的唯一活件）。
  · `test_ui_app.py::test_c004_argv_xlsx_skips_empty_state` 走 `FakeState` + 空 .xlsx 文件，
    只验「命令行优先于 last_excel」；这里走**真 state / 真表**，验清单真的出了行。
"""
import importlib
import runpy
import warnings

import pytest

import ui_harness as H                    # noqa: F401  （顺带把 tests/ 与仓库根插进 sys.path）

pytest.importorskip("PySide6")

from PySide6 import QtWidgets                          # noqa: E402

from dreg_verify.ui import app as A                    # noqa: E402


@pytest.fixture
def qapp():
    return H.app()


def test_c251_module_entry_points_to_v2(qapp):
    """薄壳 `dreg_verify.gui` 的三个名字 = `ui.app` 的同一批对象；import 它不起窗。

    v1 门面没被删，只是改名 `legacy_gui`（验收期保留，删除是 C5-c⑦）。"""
    shell = importlib.import_module("dreg_verify.gui")
    assert shell.main is A.main
    assert shell.MainWindow is A.MainWindow
    assert shell.build_window is A.build_window
    assert sorted(shell.__all__) == ["MainWindow", "build_window", "main"]

    before = len(QtWidgets.QApplication.topLevelWidgets())
    with warnings.catch_warnings():                    # runpy 对「已在 sys.modules」提醒一句
        warnings.simplefilter("ignore", RuntimeWarning)
        ns = runpy.run_module("dreg_verify.gui", run_name="not_main")
    assert ns["main"] is A.main
    qapp.processEvents()
    assert len(QtWidgets.QApplication.topLevelWidgets()) == before, "薄壳被 import 就起了窗"

    legacy = importlib.import_module("dreg_verify.legacy_gui")
    assert callable(getattr(legacy, "MainWindow", None)) and callable(legacy.main)
    assert legacy.MainWindow is not A.MainWindow       # 两台窗口是两件东西，没被互相顶掉


def test_c004_cli_xlsx_arg_opens_workbench(qapp, monkeypatch, tmp_path):
    """`build_window([<表>])` → 不停在空态，清单**立刻**有行（骨架先出，N8 第一趟）。"""
    H.isolate_settings(monkeypatch, tmp_path)
    H.auto_dialogs(monkeypatch)
    boom = H.slot_error_gate(monkeypatch)
    path = H.mirror_path("btlp")

    w = A.build_window([path])
    w.resize(1600, 900)
    w.show()
    qapp.processEvents()
    try:
        assert not w.is_empty_state()                  # C-004：跳过空态直接进工作台
        assert w.state.loaded_path == path
        n_rows = w.list_panel.proxy.rowCount()
        assert n_rows > 0, "命令行带表进来，清单却是空的"
        assert len(w.state.models()) == n_rows

        # 骨架先出（status=pending），后台 worker 逐行补齐 —— 等它跑完，别把线程留给下条用例
        def _settled():
            return all(m.get("status") != "pending" for m in w.state.models())

        assert H.wait_for(_settled), "分析没跑完：%s" % sorted(
            {m.get("status") for m in w.state.models()})
        assert w.list_panel.proxy.rowCount() == n_rows  # 分析只更新行，不重建清单
    finally:
        w.close()
    assert not boom, "槽里抛了异常：%s" % "；".join(boom)
