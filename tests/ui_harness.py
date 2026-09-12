# -*- coding: utf-8 -*-
"""ui_harness —— offscreen GUI 测试的公共工具（GUI v2 执行计划 §2 L3/L4/L5 + Phase A6）。

把三份现有 GUI 测试（test_topout_gui / test_pageviews_gui / test_mux_wl_gui）里反复出现的
四段样板收进来，v2 的测试直接用，旧测试也能立刻改用（本文件只新增，不动任何旧断言）：

  ① 起窗      —— QT_QPA_PLATFORM=offscreen + 进程级 QApplication 单例 + MainWindow + 载 mirror
  ② 找控件    —— 按 objectName（v2 会给控件起名）；找不到时列出候选名，不让人对着空断言猜
  ③ 表格取值  —— QTableWidget / QTableView 统一走 model()，文本/表头/勾选行
  ④ 模态拦截  —— 一次性 patch 掉所有会在 offscreen 下阻塞的入口，并【记录】弹了什么框、
                 文案是什么 —— 「跳过项必须点名」这类契约就是靠 recorder 验的

v2 上线后只需改一处：`set_window_factory(...)` 换成 `dreg_verify.ui.app` 的入口，
其余 API 与测试都不用动。

用法：

    import ui_harness as H                     # tests/ 在 sys.path 上（pytest rootdir 插入；本模块也自插）

    def test_xxx(monkeypatch, tmp_path):
        H.isolate_settings(monkeypatch, tmp_path)
        rec = H.auto_dialogs(monkeypatch)
        w = H.make_window()                    # 默认载 mirror_btlp
        assert H.table_texts(H.find(w, "topo_table"))
        w.on_topo_export_sv()
        assert rec.saw("导出 .sv 选项")
        H.shot(w, "topout_default")
"""

import os
import re
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _p in (_ROOT, _HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

#: 截图落点（`.gitignore` 里 `refactor_notes/` 整目录已忽略 —— 产物不入库）
SHOT_DIR = os.path.join(_ROOT, "refactor_notes", "gui_v2_shots")

#: mirror 夹具（`*.xlsx` 已 gitignore）。值 = (文件名, 生成模块, 生成函数)
MIRRORS = {
    "btlp": ("mirror_btlp_dreg.xlsx", "make_mirror_btlp", "build"),
    "wl": ("mirror_wl_dreg.xlsx", "make_mirror_excel", "build"),
}


# ─────────────────────────── ① QApplication / 起窗 ───────────────────────────

def app():
    """进程级 QApplication 单例（offscreen）。任何 Qt 对象构造前先调它。

    PySide6 缺失时 `pytest.importorskip` 跳过（与现有 GUI 测试的行为一致）。"""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    import pytest
    pytest.importorskip("PySide6")
    from PySide6 import QtWidgets
    inst = QtWidgets.QApplication.instance()
    if inst is None:
        inst = QtWidgets.QApplication([])
    return inst


def _default_window_factory():
    """v1 的主窗口。v2 上线后用 `set_window_factory` 换成 `dreg_verify.ui.app` 的入口。"""
    from dreg_verify import gui as G
    return G.MainWindow()


#: 可替换的窗口工厂 —— v2 切换点。
WINDOW_FACTORY = _default_window_factory


def set_window_factory(fn):
    """替换窗口工厂（v2 上线时调一次）。返回旧工厂，便于临时替换后还原。"""
    global WINDOW_FACTORY
    old = WINDOW_FACTORY
    WINDOW_FACTORY = fn
    return old


def mirror_path(kind="btlp", force=False):
    """保证 mirror 夹具存在并返回绝对路径。

    落在 tests/ 下（`*.xlsx` 已 gitignore）；生成脚本比 xlsx 新时自动重建。
    kind: "btlp"（make_mirror_btlp.build）/ "wl"（make_mirror_excel.build）。"""
    try:
        fname, mod_name, fn_name = MIRRORS[kind]
    except KeyError:
        raise AssertionError("未知 mirror 夹具 %r；可选：%s" % (kind, ", ".join(sorted(MIRRORS))))
    path = os.path.join(_HERE, fname)
    mod = __import__(mod_name)
    src = getattr(mod, "__file__", None)
    stale = False
    if os.path.exists(path) and src and os.path.exists(src):
        stale = os.path.getmtime(src) > os.path.getmtime(path)
    if force or stale or not os.path.exists(path):
        getattr(mod, fn_name)(path)
    return path


def make_window(excel_path=None, load=True, size=(1320, 840), kind="btlp"):
    """起主窗口。

    excel_path=None 且 load=True → 用 mirror 夹具（kind 选 btlp/wl）。
    excel_path 给了但 load=False → 只把路径填进去不载，用来测「未载表」态。
    返回窗口对象；测试结束请自行 `w.close()`（或用 pytest fixture 收尾）。"""
    app()
    w = WINDOW_FACTORY()
    if size:
        w.resize(*size)
    if excel_path is None and load:
        excel_path = mirror_path(kind)
    if excel_path is not None:
        _set_excel_path(w, str(excel_path))
        if load:
            _do_load(w)
    return w


def _set_excel_path(w, path):
    edit = getattr(w, "path_edit", None) or _find_opt(w, "excel_path_edit")
    if edit is None:
        raise AssertionError("窗口上找不到 Excel 路径输入框（试过属性 path_edit / objectName "
                             "excel_path_edit）；v2 换名了就在 ui_harness._set_excel_path 补一条")
    edit.setText(path)


def _do_load(w):
    for attr in ("on_load", "load_workbook", "load"):
        fn = getattr(w, attr, None)
        if callable(fn):
            fn()
            return
    raise AssertionError("窗口上找不到载表入口（试过 on_load / load_workbook / load）")


def isolate_settings(monkeypatch, tmp_path):
    """把 GUI 持久化文件指到临时目录 —— 三份现有 GUI 测试都在做的同一件事。

    不隔离的话 `_load_settings` 会读用户真实 ~/.dreg_verify_gui.json（级联偏好等），
    使默认值断言非确定性地失败。"""
    import pytest
    pytest.importorskip("PySide6")
    from dreg_verify import gui as G
    for attr, fname in (("SETTINGS_PATH", "gui_settings.json"), ("EDITS_PATH", "edits.json")):
        if hasattr(G, attr):
            monkeypatch.setattr(G, attr, str(tmp_path / fname))


# ─────────────────────────── ② 找控件 ───────────────────────────

def find(root, name, cls=None):
    """按 objectName 找后代控件（root 自身也算）。

    找不到时抛 AssertionError 并列出【同类型】所有非空 objectName + 同类型的属性名，
    让人一眼看出是名字写错了还是控件压根没起名。
    过渡期兼容：objectName 没命中时退回 `getattr(root, name)`（v1 的控件没有 objectName）。"""
    from PySide6 import QtWidgets
    cls = cls or QtWidgets.QWidget
    if getattr(root, "objectName", None) and root.objectName() == name and isinstance(root, cls):
        return root
    for w in root.findChildren(cls):
        if w.objectName() == name:
            return w
    attr = getattr(root, name, None)
    if attr is not None and isinstance(attr, cls):
        return attr
    named = sorted({w.objectName() for w in root.findChildren(cls) if w.objectName()})
    try:
        attrs = sorted(k for k, v in vars(root).items() if isinstance(v, cls))
    except TypeError:                     # 纯 C++ 侧对象没有 __dict__
        attrs = []
    raise AssertionError(
        "找不到控件 objectName=%r（类型 %s）。\n"
        "  该类型已起名的 objectName：%s\n"
        "  该类型的窗口属性名：%s"
        % (name, cls.__name__,
           ", ".join(named) or "(一个都没有 —— 这批控件还没 setObjectName)",
           ", ".join(attrs) or "(无)"))


def _find_opt(root, name, cls=None):
    try:
        return find(root, name, cls)
    except AssertionError:
        return None


def find_all(root, cls):
    """按类型列出所有后代控件（root 自身在内），按 objectName 再按创建序稳定排序。"""
    out = list(root.findChildren(cls))
    if isinstance(root, cls):
        out.insert(0, root)
    return out


def object_names(root, cls=None):
    """列出 root 下所有非空 objectName —— 给 v2 起名时自查用。"""
    from PySide6 import QtWidgets
    cls = cls or QtWidgets.QWidget
    return sorted({w.objectName() for w in root.findChildren(cls) if w.objectName()})


# ─────────────────────────── ③ 表格取值 ───────────────────────────

def _model(table):
    m = table.model() if hasattr(table, "model") else None
    if m is None:
        raise AssertionError("对象 %r 没有 model()，不是 QTableWidget/QTableView" % (table,))
    return m


def _cell_text(table, m, r, c):
    idx = m.index(r, c)
    v = m.data(idx)                    # Qt.DisplayRole
    if v is None:
        item = table.item(r, c) if hasattr(table, "item") else None
        v = item.text() if item is not None else ""
    return "" if v is None else str(v)


def table_texts(table, rows=None, cols=None):
    """QTableWidget / QTableView 通吃 → list[list[str]]（空单元格为 ""）。

    rows/cols 可给可迭代的下标子集（只取关心的列，断言更短）。"""
    m = _model(table)
    rr = range(m.rowCount()) if rows is None else list(rows)
    cc = range(m.columnCount()) if cols is None else list(cols)
    return [[_cell_text(table, m, r, c) for c in cc] for r in rr]


def header_texts(table, orientation="h"):
    """表头文本。orientation: "h" 横表头 / "v" 竖表头。"""
    from PySide6 import QtCore
    m = _model(table)
    if str(orientation).lower().startswith("v"):
        o, n = QtCore.Qt.Vertical, m.rowCount()
    else:
        o, n = QtCore.Qt.Horizontal, m.columnCount()
    out = []
    for i in range(n):
        v = m.headerData(i, o, QtCore.Qt.DisplayRole)
        out.append("" if v is None else str(v))
    return out


def checked_rows(table, col):
    """勾选列 col 里处于 Checked 的行下标（QTableWidget/QTableView 通吃）。"""
    from PySide6 import QtCore
    m = _model(table)
    want = int(QtCore.Qt.Checked.value if hasattr(QtCore.Qt.Checked, "value") else QtCore.Qt.Checked)
    out = []
    for r in range(m.rowCount()):
        v = m.data(m.index(r, col), QtCore.Qt.CheckStateRole)
        if v is None and hasattr(table, "item"):
            item = table.item(r, col)
            v = item.checkState() if item is not None else None
        if v is None:
            continue
        iv = int(v.value) if hasattr(v, "value") else int(v)
        if iv == want:
            out.append(r)
    return out


def row_of(table, text, col=None):
    """按单元格文本找行下标；col=None 时任一列命中即可。找不到抛 AssertionError 并附全表。"""
    texts = table_texts(table)
    for r, row in enumerate(texts):
        cells = row if col is None else [row[col]] if col < len(row) else []
        if any(c == text for c in cells):
            return r
    raise AssertionError("表里找不到 %r（col=%s）。现有内容：\n%s"
                         % (text, col, "\n".join(" | ".join(x) for x in texts[:40])))


# ─────────────────────────── ④ 截图 ───────────────────────────

def shot(widget, name):
    """截 PNG 到 refactor_notes/gui_v2_shots/<name>.png，返回绝对路径。

    目录已被 .gitignore 的 `refactor_notes/` 一行覆盖 —— 产物不入库（截图里可能有真信号名）。"""
    a = app()
    os.makedirs(SHOT_DIR, exist_ok=True)
    fn = name if str(name).lower().endswith(".png") else "%s.png" % name
    path = os.path.join(SHOT_DIR, fn)
    if hasattr(widget, "show"):
        widget.show()
    a.processEvents()
    ok = widget.grab().save(path)
    assert ok and os.path.getsize(path) > 0, "截图失败：%s" % path
    return path


# ─────────────────────────── ⑤ 真实事件（QTest 封装） ───────────────────────────

def _target(widget):
    """QAbstractScrollArea（表格/列表）的鼠标事件要发到 viewport 上。"""
    from PySide6 import QtWidgets
    if isinstance(widget, QtWidgets.QAbstractScrollArea):
        return widget.viewport()
    return widget


def click(widget, pos=None, button=None, modifier=None):
    """真实鼠标左键点击（不直接 connect/emit，也不直接调槽）。"""
    from PySide6 import QtCore
    from PySide6.QtTest import QTest
    app()
    t = _target(widget)
    button = QtCore.Qt.LeftButton if button is None else button
    modifier = QtCore.Qt.NoModifier if modifier is None else modifier
    if pos is None:
        QTest.mouseClick(t, button, modifier)
    else:
        QTest.mouseClick(t, button, modifier, QtCore.QPoint(*pos) if isinstance(pos, tuple) else pos)


def click_cell(table, row, col, button=None, modifier=None):
    """点表格某格（先滚到可见，再按 visualRect 中心点 viewport）—— 真实点击而非 setCurrentCell。"""
    m = _model(table)
    idx = m.index(row, col)
    table.scrollTo(idx)
    app().processEvents()
    click(table, table.visualRect(idx).center(), button, modifier)


def keys(widget, seq):
    """按【键序列】。seq 为空格分隔的记号：`"Ctrl+A"` / `"Enter"` / `"Down Down Space"`，
    也可直接给 Qt.Key_*。走 QTest 真实按键事件。

    要输入字面文本用 `type_text`（避免 "Tab" 到底是字符还是按键的歧义）。"""
    from PySide6 import QtCore, QtGui
    from PySide6.QtTest import QTest
    app()
    widget.setFocus()
    toks = seq.split() if isinstance(seq, str) else [seq]
    for tok in toks:
        if not isinstance(tok, str):
            QTest.keyClick(widget, tok)
            continue
        ks = QtGui.QKeySequence(tok)
        if ks.count() == 0:
            raise AssertionError("无法解析按键记号 %r（想输字面文本请用 type_text）" % tok)
        kc = ks[0]
        key = QtCore.Qt.Key(kc.key()) if hasattr(kc, "key") else QtCore.Qt.Key(int(kc) & ~0xFE000000)
        mod = kc.keyboardModifiers() if hasattr(kc, "keyboardModifiers") else QtCore.Qt.NoModifier
        QTest.keyClick(widget, key, mod)


def type_text(widget, text):
    """逐字符真实按键输入（QTest.keyClicks）。"""
    from PySide6.QtTest import QTest
    app()
    widget.setFocus()
    QTest.keyClicks(widget, text)


def paste(widget, text):
    """把 text 放进剪贴板，再往 widget 发真实的 Ctrl+V。"""
    a = app()
    a.clipboard().setText(text)
    keys(widget, "Ctrl+V")


# ─────────────────────────── ⑥ 模态对话框自动拦截 ───────────────────────────

class REAL(object):
    """`answers` 里的哨兵：这一项不要拦，走真实实现（配合 QDialog.exec 拦截仍不会阻塞）。"""


class DialogCall(object):
    """一次模态调用的记录。"""

    __slots__ = ("kind", "title", "text", "detail", "args", "kwargs", "result")

    def __init__(self, kind, title="", text="", detail="", args=(), kwargs=None, result=None):
        self.kind = kind
        self.title = title or ""
        self.text = text or ""
        self.detail = detail or ""
        self.args = args
        self.kwargs = kwargs or {}
        self.result = result

    @property
    def blob(self):
        """标题 + 正文 + 详情拼起来，给 `saw()` 做文案断言用。"""
        return "\n".join(x for x in (self.title, self.text, self.detail) if x)

    def __repr__(self):
        return "<DialogCall %s title=%r text=%.60r>" % (self.kind, self.title, self.text)


class DialogRecorder(object):
    """auto_dialogs 的返回值：断言「弹了哪个框、文案是什么」。"""

    def __init__(self, save_dir):
        self.calls = []
        self.save_dir = save_dir
        self.save_paths = []
        self.missing = []          # 本版 GUI 上不存在的自建对话框入口（v2 改名后可见）
        self.patched = []          # 实际拦下来的入口全名

    # —— 查询 ——
    def __len__(self):
        return len(self.calls)

    def __iter__(self):
        return iter(self.calls)

    @property
    def kinds(self):
        return [c.kind for c in self.calls]

    @property
    def titles(self):
        return [c.title for c in self.calls]

    @property
    def texts(self):
        return [c.text for c in self.calls]

    def of(self, key):
        """按入口全名/短名/标题（精确或子串）筛选调用。"""
        k = str(key)
        out = [c for c in self.calls
               if c.kind == k or c.kind.rsplit(".", 1)[-1] == k or c.title == k]
        return out or [c for c in self.calls if k in c.kind or k in c.title]

    def last(self, key=None):
        got = self.of(key) if key is not None else self.calls
        return got[-1] if got else None

    def count(self, key=None):
        return len(self.of(key)) if key is not None else len(self.calls)

    def saw(self, needle, key=None):
        """任何弹出框的标题/正文/详情里出现过 needle → True。
        「跳过项必须点名」这类契约就是靠它验的。"""
        pool = self.of(key) if key is not None else self.calls
        return any(str(needle) in c.blob for c in pool)

    def dump(self):
        """人读的调用流水（断言失败时贴出来定位）。"""
        return "\n".join("%2d. %-34s %s | %s" % (i, c.kind, c.title, c.text.replace("\n", " ⏎ ")[:120])
                         for i, c in enumerate(self.calls, 1)) or "(没有任何模态调用)"

    @property
    def last_save_path(self):
        return self.save_paths[-1] if self.save_paths else None

    # —— 内部 ——
    def _add(self, kind, title="", text="", detail="", args=(), kwargs=None, result=None):
        c = DialogCall(kind, title, text, detail, args, kwargs, result)
        self.calls.append(c)
        return c


def _affirmative(buttons):
    """按 `buttons` 里实际提供的按钮挑一个「肯定」答案：Yes > Ok > Save > Apply。

    —— `_confirm_dup_labels` 用 `== QMessageBox.Yes` 判定，固定返回 Ok 会把导出静默取消掉。"""
    from PySide6 import QtWidgets
    QMB = QtWidgets.QMessageBox
    if buttons is None:
        return QMB.Ok
    try:
        mask = int(buttons)
    except (TypeError, ValueError):
        return QMB.Ok
    for cand in (QMB.Yes, QMB.Ok, QMB.Save, QMB.Apply, QMB.YesToAll):
        if mask & int(cand):
            return cand
    return QMB.Ok


def _arg(args, kwargs, idx, name, default=None):
    if name in kwargs:
        return kwargs[name]
    return args[idx] if len(args) > idx else default


def _auto_save_path(rec, args, kwargs):
    """getSaveFileName 的默认答案：按对话框自己给的默认文件名/过滤器选扩展名，落到临时目录。"""
    cand = _arg(args, kwargs, 2, "dir", "")
    name = os.path.basename(str(cand).strip()) if cand else ""
    if not name or "." not in name:
        filt = str(_arg(args, kwargs, 3, "filter", "") or "")
        m = re.search(r"\*(\.[A-Za-z0-9]+)", filt)
        name = "auto_save_%d%s" % (len(rec.calls), m.group(1) if m else ".txt")
    return os.path.join(rec.save_dir, name)


# 自建对话框入口（gui.py）：(模块, 类名, 方法名, 默认答案工厂)。
# 默认答案 = 「用户按了确定、用默认选项」，保证任何导出流程都能一路跑到底。
def _def_export_options(self, *a, **k):
    return {"scope": "all", "comments": False, "sv_summary": False, "owner_in_msg": False}


def _def_nets_pages(self, *a, **k):
    cats = getattr(self, "_nets_categories", None)
    return set(cats()) if callable(cats) else {"topout"}


def _def_confirm_dup(self, *a, **k):
    return True


CUSTOM_MODALS = [
    ("dreg_verify.gui", "SignalView", "_ask_export_options", _def_export_options),
    ("dreg_verify.gui", "MainWindow", "_ask_export_options", _def_export_options),
    ("dreg_verify.gui", "MainWindow", "_ask_nets_pages", _def_nets_pages),
    ("dreg_verify.gui", "MainWindow", "_confirm_dup_labels", _def_confirm_dup),
]

#: 自建对话框方法 → 它弹出的窗口标题（这样 `answers` 也能按标题写，与 exec 拦截口径一致）
CUSTOM_TITLES = {
    "SignalView._ask_export_options": "导出 .sv 选项",
    "MainWindow._ask_export_options": "生成 .sv — 导出选项",
    "MainWindow._ask_nets_pages": "导出 nets.txt — 选类别",
    "MainWindow._confirm_dup_labels": "重复 assert 标号（非法 SV）",
}

#: 会阻塞 offscreen 的标准模态入口全名（auto_dialogs 全拦）。
STATIC_MODALS = [
    ("QMessageBox", "question"), ("QMessageBox", "information"), ("QMessageBox", "warning"),
    ("QMessageBox", "critical"), ("QMessageBox", "about"),
    ("QFileDialog", "getOpenFileName"), ("QFileDialog", "getOpenFileNames"),
    ("QFileDialog", "getSaveFileName"), ("QFileDialog", "getExistingDirectory"),
    ("QInputDialog", "getText"), ("QInputDialog", "getItem"), ("QInputDialog", "getInt"),
]
EXEC_MODALS = [("QMessageBox", "exec"), ("QMessageBox", "exec_"),
               ("QDialog", "exec"), ("QDialog", "exec_")]


def auto_dialogs(monkeypatch, answers=None, save_dir=None):
    """一次性把所有会在 offscreen 下阻塞的模态入口 patch 掉，并记录调用。返回 DialogRecorder。

    拦截范围（共 4 类）：
      · QMessageBox.question / information / warning / critical / about —— 默认返回按钮列表里的
        「肯定」答案（Yes 优先，其次 Ok），不是一律 Ok（见 `_affirmative`）
      · QFileDialog.getOpenFileName / getSaveFileName / getExistingDirectory / getOpenFileNames
        —— getSaveFileName 默认给一个临时目录里的真路径（导出流程能一路跑完并落盘），
           其余默认「取消」（凭空编一个不存在的输入文件只会更难查）
      · QInputDialog.getText / getItem / getInt —— 默认「取消」
      · 实例 exec：QMessageBox.exec / QDialog.exec —— 默认 QDialog.Accepted，
        并按 `windowTitle()` 记录（gui.py 里 6 个自建 QDialog 全落在这条上）
      · gui.py 自建的 `_ask_export_options`(SignalView / MainWindow)、`_ask_nets_pages`、
        `_confirm_dup_labels` —— 直接替换成默认答案（见 CUSTOM_MODALS）

    answers: dict，键可以是
        入口全名 "QMessageBox.question" / 短名 "question" / 方法名 "_ask_nets_pages"
        / 对话框标题 "导出 .sv 选项"（精确或子串，用于 exec 拦下来的自建 QDialog）
      值可以是常量、可调用对象（收到 (call, *args, **kwargs)），或 `ui_harness.REAL`（不拦）。
    """
    from PySide6 import QtWidgets
    app()
    answers = dict(answers or {})
    rec = DialogRecorder(save_dir or tempfile.mkdtemp(prefix="ui_harness_save_"))

    def _lookup(cands, call_obj, args, kwargs, default):
        """按候选键找 answers；找不到时用 default（可调用）。"""
        for key in cands:
            if key and key in answers:
                val = answers[key]
                if val is not REAL:
                    return val(call_obj, *args, **kwargs) if callable(val) else val
        title = call_obj.title
        if title:
            for key, val in answers.items():
                if key and key in title and val is not REAL:
                    return val(call_obj, *args, **kwargs) if callable(val) else val
        return default(call_obj, args, kwargs)

    # ── 1/2/3：静态入口 ──
    def _mk_static(cls_name, meth, default_fn, title_i, text_i):
        full = "%s.%s" % (cls_name, meth)

        def _patched(*args, **kwargs):
            call = rec._add(full,
                            title=str(_arg(args, kwargs, title_i, "title", "") or ""),
                            text=str(_arg(args, kwargs, text_i, "text", "") or ""),
                            args=args, kwargs=kwargs)
            res = _lookup((full, meth), call, args, kwargs, default_fn)
            call.result = res
            return res
        return _patched

    def _def_msgbox(call, args, kwargs):
        return _affirmative(_arg(args, kwargs, 3, "buttons"))

    def _def_save(call, args, kwargs):
        p = _auto_save_path(rec, args, kwargs)
        rec.save_paths.append(p)
        return (p, "")

    _DEFAULTS = {
        "QMessageBox.question": (_def_msgbox, 1, 2),
        "QMessageBox.information": (_def_msgbox, 1, 2),
        "QMessageBox.warning": (_def_msgbox, 1, 2),
        "QMessageBox.critical": (_def_msgbox, 1, 2),
        "QMessageBox.about": (lambda c, a, k: None, 1, 2),
        "QFileDialog.getOpenFileName": (lambda c, a, k: ("", ""), 1, 2),
        "QFileDialog.getOpenFileNames": (lambda c, a, k: ([], ""), 1, 2),
        "QFileDialog.getSaveFileName": (_def_save, 1, 2),
        "QFileDialog.getExistingDirectory": (lambda c, a, k: "", 1, 2),
        "QInputDialog.getText": (lambda c, a, k: ("", False), 1, 2),
        "QInputDialog.getItem": (lambda c, a, k: ("", False), 1, 2),
        "QInputDialog.getInt": (lambda c, a, k: (0, False), 1, 2),
    }
    for cls_name, meth in STATIC_MODALS:
        cls = getattr(QtWidgets, cls_name, None)
        if cls is None or not hasattr(cls, meth):
            rec.missing.append("%s.%s" % (cls_name, meth))
            continue
        full = "%s.%s" % (cls_name, meth)
        default_fn, ti, xi = _DEFAULTS[full]
        if answers.get(full) is REAL or answers.get(meth) is REAL:
            continue
        monkeypatch.setattr(cls, meth, staticmethod(_mk_static(cls_name, meth, default_fn, ti, xi)))
        rec.patched.append(full)

    # ── 4：实例 exec（自建 QDialog / 带 DetailedText 的 QMessageBox 实例） ──
    def _mk_exec(cls_name, meth):
        full = "%s.%s" % (cls_name, meth)

        def _patched(self, *args, **kwargs):
            title = self.windowTitle() if hasattr(self, "windowTitle") else ""
            text = self.text() if hasattr(self, "text") else ""
            detail = self.detailedText() if hasattr(self, "detailedText") else ""
            info = self.informativeText() if hasattr(self, "informativeText") else ""
            call = rec._add(full, title=title, text="\n".join(x for x in (text, info) if x),
                            detail=detail, args=(self,) + args, kwargs=kwargs)

            def _default(c, a, k):
                if hasattr(self, "standardButtons"):
                    return _affirmative(self.standardButtons())
                return QtWidgets.QDialog.Accepted
            res = _lookup((full, meth, title), call, args, kwargs, _default)
            call.result = res
            return res
        return _patched

    for cls_name, meth in EXEC_MODALS:
        cls = getattr(QtWidgets, cls_name, None)
        if cls is None or not hasattr(cls, meth):
            rec.missing.append("%s.%s" % (cls_name, meth))
            continue
        full = "%s.%s" % (cls_name, meth)
        if answers.get(full) is REAL or answers.get(meth) is REAL:
            continue
        monkeypatch.setattr(cls, meth, _mk_exec(cls_name, meth))
        rec.patched.append(full)

    # ── 5：gui.py 自建对话框函数 ──
    for mod_name, cls_name, meth, default_fn in CUSTOM_MODALS:
        try:
            mod = __import__(mod_name, fromlist=[cls_name])
            cls = getattr(mod, cls_name)
        except (ImportError, AttributeError):
            rec.missing.append("%s.%s.%s" % (mod_name, cls_name, meth))
            continue
        if not hasattr(cls, meth):
            rec.missing.append("%s.%s" % (cls_name, meth))
            continue
        full = "%s.%s" % (cls_name, meth)
        if answers.get(full) is REAL or answers.get(meth) is REAL:
            continue

        def _mk_custom(full=full, meth=meth, default_fn=default_fn):
            def _patched(self, *args, **kwargs):
                call = rec._add(full, title=CUSTOM_TITLES.get(full, ""),
                                args=(self,) + args, kwargs=kwargs)
                res = _lookup((full, meth, call.title), call, args, kwargs,
                              lambda c, a, k: default_fn(self, *a, **k))
                call.result = res
                return res
            return _patched
        monkeypatch.setattr(cls, meth, _mk_custom())
        rec.patched.append(full)

    return rec
