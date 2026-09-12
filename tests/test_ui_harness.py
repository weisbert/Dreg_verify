# -*- coding: utf-8 -*-
"""ui_harness 自测（offscreen）。

验的是 harness 本身能用，不动任何现有测试的断言：
起窗 → 载 mirror → 找到信号表 → 表格取值/表头/勾选行 → 截图 → auto_dialogs 拦下
一次「导出 .sv 选项」并记下文案（这正是 §2 L3「跳过项点名」契约要用的能力）。
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import ui_harness as H                                   # noqa: E402


@pytest.fixture(scope="module")
def gui_app():
    pytest.importorskip("PySide6")
    return H.app()


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    pytest.importorskip("PySide6")
    H.isolate_settings(monkeypatch, tmp_path)


@pytest.fixture()
def win(gui_app):
    w = H.make_window()
    yield w
    w.close()


# ───────────── app / 起窗 ─────────────
def test_app_is_singleton(gui_app):
    from PySide6 import QtWidgets
    assert H.app() is gui_app is QtWidgets.QApplication.instance()
    assert os.environ["QT_QPA_PLATFORM"] == "offscreen"


def test_mirror_fixtures_exist():
    """harness 负责保证两张 mirror 夹具在盘上（*.xlsx 已 gitignore）。"""
    for kind in ("btlp", "wl"):
        p = H.mirror_path(kind)
        assert os.path.isfile(p) and os.path.getsize(p) > 0
        assert os.path.dirname(p) == os.path.dirname(os.path.abspath(__file__))


def test_make_window_loads_mirror(win):
    assert win.wb is not None
    assert win.topo_table.rowCount() > 0


def test_make_window_without_load(gui_app):
    """load=False → 只填路径不载表，用来测「未载表」态。"""
    w = H.make_window(excel_path=H.mirror_path("btlp"), load=False)
    try:
        assert w.path_edit.text().endswith(".xlsx")
        assert w.topo_table.rowCount() == 0
    finally:
        w.close()


def test_window_factory_is_replaceable(gui_app):
    """v2 上线只需换工厂 —— 这里证明换得掉、还得回来。"""
    from PySide6 import QtWidgets
    old = H.set_window_factory(lambda: QtWidgets.QMainWindow())
    try:
        w = H.make_window(load=False)
        assert type(w) is QtWidgets.QMainWindow
        w.close()
    finally:
        H.set_window_factory(old)
    assert H.WINDOW_FACTORY is old


# ───────────── find / find_all ─────────────
def test_find_falls_back_to_attribute_and_reports_candidates(win):
    from PySide6 import QtWidgets
    # v1 的控件还没 setObjectName → 退回属性名（过渡期兼容）
    assert H.find(win, "topo_table") is win.topo_table
    # 起了名就按 objectName 找（v2 的常规路径）
    win.topo_table.setObjectName("signal_table")
    assert H.find(win, "signal_table", QtWidgets.QTableWidget) is win.topo_table
    assert "signal_table" in H.object_names(win)
    with pytest.raises(AssertionError) as ei:
        H.find(win, "no_such_widget", QtWidgets.QTableWidget)
    msg = str(ei.value)
    assert "no_such_widget" in msg and "signal_table" in msg     # 列出候选帮助定位
    win.topo_table.setObjectName("")


def test_find_all_by_class(win):
    from PySide6 import QtWidgets
    tables = H.find_all(win, QtWidgets.QTableWidget)
    assert win.topo_table in tables and len(tables) >= 2


# ───────────── 表格取值 ─────────────
def test_table_texts_and_header_and_checked(win):
    from dreg_verify import gui as G
    t = H.find(win, "topo_table")
    rows = H.table_texts(t)
    assert len(rows) == t.rowCount() and len(rows[0]) == t.columnCount()
    assert H.header_texts(t)[G.TOPO_NAME] == "信号"
    names = [r[G.TOPO_NAME] for r in rows]
    assert any(n.startswith("d_logic_bt_lp_rx_en") for n in names)
    # 勾选列：全勾 → 全部行；取消第一行 → 少一行
    from PySide6 import QtCore
    all_rows = list(range(t.rowCount()))
    for r in all_rows:
        t.item(r, G.TOPO_SEL).setCheckState(QtCore.Qt.Checked)
    assert H.checked_rows(t, G.TOPO_SEL) == all_rows
    t.item(0, G.TOPO_SEL).setCheckState(QtCore.Qt.Unchecked)
    assert H.checked_rows(t, G.TOPO_SEL) == all_rows[1:]


def test_table_texts_works_on_qtableview(gui_app):
    """QTableView（无 QTableWidgetItem，纯 model）也要通。"""
    from PySide6 import QtCore, QtGui, QtWidgets
    m = QtGui.QStandardItemModel(2, 2)
    m.setHorizontalHeaderLabels(["甲", "乙"])
    m.setItem(0, 0, QtGui.QStandardItem("a0"))
    m.setItem(0, 1, QtGui.QStandardItem("a1"))
    chk = QtGui.QStandardItem("b0")
    chk.setCheckable(True)
    chk.setCheckState(QtCore.Qt.Checked)
    m.setItem(1, 0, chk)
    v = QtWidgets.QTableView()
    v.setModel(m)
    assert H.table_texts(v) == [["a0", "a1"], ["b0", ""]]
    assert H.header_texts(v) == ["甲", "乙"]
    assert H.checked_rows(v, 0) == [1]
    assert H.table_texts(v, cols=[1]) == [["a1"], [""]]
    assert H.row_of(v, "a1") == 0
    with pytest.raises(AssertionError):
        H.row_of(v, "不存在")


# ───────────── 真实事件 ─────────────
def test_keys_type_text_paste_and_click(gui_app):
    from PySide6 import QtWidgets
    e = QtWidgets.QLineEdit()
    e.show()
    H.type_text(e, "abc")
    assert e.text() == "abc"
    H.keys(e, "Ctrl+A")
    H.keys(e, "Delete")
    assert e.text() == ""
    H.paste(e, "粘贴内容")
    assert e.text() == "粘贴内容"
    hits = []
    b = QtWidgets.QPushButton("x")
    b.clicked.connect(lambda: hits.append(1))
    b.show()
    H.click(b)                     # 真实鼠标事件，不是 b.click()
    assert hits == [1]


# ───────────── 截图 ─────────────
def test_shot_writes_png(win):
    p = H.shot(win, "ui_harness_selftest")
    assert p.endswith("ui_harness_selftest.png") and os.path.getsize(p) > 0
    assert os.path.normpath(H.SHOT_DIR).endswith(os.path.join("refactor_notes", "gui_v2_shots"))


def test_shot_dir_is_gitignored():
    """截图可能含真实信号名 —— 落点必须在 .gitignore 覆盖范围内。"""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(root, ".gitignore"), encoding="utf-8") as f:
        lines = [ln.strip() for ln in f]
    assert "refactor_notes/" in lines


# ───────────── auto_dialogs ─────────────
def test_auto_dialogs_patches_every_known_modal(monkeypatch, gui_app):
    rec = H.auto_dialogs(monkeypatch)
    for full in ("QMessageBox.question", "QMessageBox.information", "QMessageBox.warning",
                 "QMessageBox.critical", "QMessageBox.about",
                 "QFileDialog.getOpenFileName", "QFileDialog.getOpenFileNames",
                 "QFileDialog.getSaveFileName", "QFileDialog.getExistingDirectory",
                 "QInputDialog.getText", "QInputDialog.getItem", "QInputDialog.getInt",
                 "QMessageBox.exec", "QDialog.exec",
                 "SignalView._ask_export_options", "MainWindow._ask_export_options",
                 "MainWindow._ask_nets_pages", "MainWindow._confirm_dup_labels"):
        assert full in rec.patched, "没拦到 %s（剩余 missing=%s）" % (full, rec.missing)


def test_auto_dialogs_patches_v2_dialog_ask_entrypoints(monkeypatch, gui_app):
    """C2-int：`ui/dialogs.py` 的 11 个 `ask*` 入口也在拦截范围里，且能按名字 / 标题给答案。"""
    from dreg_verify.ui import dialogs as D
    rec = H.auto_dialogs(monkeypatch)
    wanted = ["%s.%s" % (cls, meth) for mod, cls, meth, _fn in H.CUSTOM_MODALS
              if mod == "dreg_verify.ui.dialogs"]
    assert len(wanted) == 11, wanted
    for full in wanted:
        assert full in rec.patched, "没拦到 %s（剩余 missing=%s）" % (full, rec.missing)
    # 默认答案 = 「确定、一个默认值都没改」
    assert D.ConfirmDialog.ask(D.CONFIRM_CLEAR) is True
    assert D.RenameColumnDialog.ask("U0", ["T1"]) == "U0"
    assert D.PresetsDialog.ask_save(["x"]) == ""
    assert rec.count("ConfirmDialog.ask") == 1

    # 按入口全名给答案
    rec2 = H.auto_dialogs(monkeypatch, answers={"ConfirmDialog.ask": False})
    assert D.ConfirmDialog.ask(D.CONFIRM_CLEAR) is False
    assert rec2.count("ConfirmDialog.ask") == 1
    # 按标题给答案（标题取自 dialogs.TITLES）
    rec3 = H.auto_dialogs(monkeypatch, answers={D.TITLES["presets_save"]: "weekly"})
    assert D.PresetsDialog.ask_save(["x"]) == "weekly"
    assert rec3.saw(D.TITLES["presets_save"])


def test_auto_dialogs_ask_stub_takes_all_keyword_calls(monkeypatch, gui_app):
    """C3-int：`类.ask` 的 stub 收**全关键字**调用也不炸（以前 self/cls 没人接 → TypeError）。

    `ui/dialogs.py` 的 `ask*` 都是 `@classmethod`。stub 以前一律换成裸函数，绑定就没了，
    `ConfirmDialog.ask(kind=...)` 这种写法第一个形参没人接 —— 调用方只好为了测试改成位置传参
    （C3-c 的 `_do_batch_fill` 就这么绕过的）。现在 stub 照原样包成 classmethod。
    """
    from dreg_verify.ui import dialogs as D
    rec = H.auto_dialogs(monkeypatch)
    assert D.ConfirmDialog.ask(kind=D.CONFIRM_CLEAR, n=0, names=(), parent=None) is True
    assert D.RenameColumnDialog.ask(current="U0", others=["T1"], parent=None) == "U0"
    assert D.BatchFillDialog.ask(n_selected=2, n_total=5, parent=None) == {
        "mode": "const", "value": None, "scope": "selected"}
    assert D.ExportOptionsDialog.ask(defaults={"comments": True}) == {"comments": True}
    assert D.ColumnsDialog.ask(visible=(), parent=None) == {}
    assert rec.count("ConfirmDialog.ask") == 1
    # 位置传参与关键字传参给出同一个答案
    assert D.RenameColumnDialog.ask("U9") == "U9"
    assert D.ExportOptionsDialog.ask({"comments": False}) == {"comments": False}


def test_auto_dialogs_defaults_are_affirmative(monkeypatch, gui_app):
    """默认答案按实际给的按钮挑「肯定」：Yes|No → Yes（固定 Ok 会把导出静默取消掉）。"""
    from PySide6 import QtWidgets
    rec = H.auto_dialogs(monkeypatch)
    QMB = QtWidgets.QMessageBox
    assert QMB.question(None, "确认", "要继续吗", QMB.Yes | QMB.No, QMB.No) == QMB.Yes
    assert QMB.warning(None, "警告", "重复标号", QMB.Yes | QMB.No, QMB.No) == QMB.Yes
    assert QMB.information(None, "完成", "写好了") == QMB.Ok
    assert rec.count("QMessageBox.question") == 1
    assert rec.saw("重复标号")
    assert rec.last("information").title == "完成"
    assert "完成" in rec.dump()


def test_auto_dialogs_file_and_input_defaults(monkeypatch, gui_app):
    from PySide6 import QtWidgets
    rec = H.auto_dialogs(monkeypatch)
    p, _ = QtWidgets.QFileDialog.getSaveFileName(None, "保存", "wr_rf_tc.sv", "SystemVerilog (*.sv)")
    assert p.endswith("wr_rf_tc.sv") and p == rec.last_save_path
    p2, _ = QtWidgets.QFileDialog.getSaveFileName(None, "保存", "", "报告 (*.html)")
    assert p2.endswith(".html")                                   # 没给默认名 → 从过滤器猜扩展名
    assert QtWidgets.QFileDialog.getOpenFileName(None, "打开")[0] == ""   # 默认「取消」
    assert QtWidgets.QFileDialog.getExistingDirectory(None, "选目录") == ""
    assert QtWidgets.QInputDialog.getText(None, "改名", "新名字") == ("", False)
    assert rec.count("QFileDialog") == 4 and rec.count() == 5


def test_auto_dialogs_answers_by_name_and_title(monkeypatch, gui_app):
    from PySide6 import QtWidgets
    rec = H.auto_dialogs(monkeypatch, answers={
        "QMessageBox.question": QtWidgets.QMessageBox.No,       # 按入口全名
        "getText": ("新名", True),                               # 按短名
        "选目录": "D:/somewhere",                                 # 按标题子串
    })
    QMB = QtWidgets.QMessageBox
    assert QMB.question(None, "确认", "?", QMB.Yes | QMB.No) == QMB.No
    assert QtWidgets.QInputDialog.getText(None, "改名", "新名字") == ("新名", True)
    assert QtWidgets.QFileDialog.getExistingDirectory(None, "选目录") == "D:/somewhere"
    assert [c.result for c in rec.of("getText")] == [("新名", True)]


def test_auto_dialogs_intercepts_custom_qdialog_exec(monkeypatch, gui_app):
    """gui.py 的 6 个自建 QDialog 走 `dlg.exec()` —— 按 windowTitle 记录，默认 Accepted。"""
    from PySide6 import QtWidgets
    rec = H.auto_dialogs(monkeypatch)
    d = QtWidgets.QDialog()
    d.setWindowTitle("探针前缀映射")
    assert d.exec() == QtWidgets.QDialog.Accepted
    assert rec.last("QDialog.exec").title == "探针前缀映射"
    box = QtWidgets.QMessageBox(QtWidgets.QMessageBox.Information, "完成",
                               "已写出：a.sv", QtWidgets.QMessageBox.Ok)
    box.setDetailedText("跳过原因：以下输入在 ENV_RF 层探不到")
    box.exec()
    c = rec.last("QMessageBox.exec")
    assert c.title == "完成" and "已写出" in c.text
    assert rec.saw("探不到")                       # DetailedText 也进 blob → 跳过项点名可断言


def test_auto_dialogs_catches_export_sv_options(win, monkeypatch, tmp_path):
    """端到端：导出 .sv 时 auto_dialogs 拦下「导出 .sv 选项」框 + 保存路径 + 完成提示，
    产物落盘且含断言 —— 一行 patch 顶掉旧测试里的三行 monkeypatch。"""
    out = tmp_path / "harness_out.sv"
    rec = H.auto_dialogs(monkeypatch, answers={"getSaveFileName": (str(out), "")})
    win.on_topo_export_sv()
    assert rec.count("SignalView._ask_export_options") == 1
    assert rec.saw("导出 .sv 选项")                      # 点名弹了哪个框
    assert rec.count("QFileDialog.getSaveFileName") == 1
    assert out.exists() and out.read_text(encoding="utf-8").count("assert (") > 0
    assert rec.saw("已导出") and rec.saw(str(out))       # 完成提示的标题 + 落盘路径


def test_auto_dialogs_custom_answer_changes_product(win, monkeypatch, tmp_path):
    """自建对话框的答案可改 → 产物跟着变（证明拦截点是真入口，不是摆设）。"""
    out = tmp_path / "with_comments.sv"
    H.auto_dialogs(monkeypatch, answers={
        "_ask_export_options": {"scope": "all", "comments": True,
                                "sv_summary": True, "owner_in_msg": False},
        "getSaveFileName": (str(out), ""),
    })
    win.on_topo_export_sv()
    text = out.read_text(encoding="utf-8")
    assert text.startswith("// auto-generated") and "dreg_n_real_fail" in text


def test_auto_dialogs_nets_pages_default_is_all_categories(win, monkeypatch, tmp_path):
    """_ask_nets_pages 默认答「全勾」→ 导出 nets.txt 不会因对话框被取消而空转。"""
    out = tmp_path / "nets.txt"
    rec = H.auto_dialogs(monkeypatch, answers={"getSaveFileName": (str(out), "")})
    win.on_export_nets()
    assert rec.count("MainWindow._ask_nets_pages") == 1
    assert rec.last("_ask_nets_pages").result == set(win._nets_categories())
    assert out.exists() and out.read_text(encoding="utf-8").strip()
