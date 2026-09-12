# -*- coding: utf-8 -*-
"""GUI v2 C1-d：`ui/widgets.py` 共享小件 + `ui/app.py` 组合根骨架。

覆盖 APP 22 条契约（架构文档附录 A）：
  C-001 C-002 C-003 C-004 C-005 C-006 C-251 C-253 C-254 C-255 C-256 C-257
  C-259 C-260 C-261 C-262 C-263 C-264 C-266 C-267 C-269 C-272

本文件同时提供 **FakeState / FakeWorker / FakeProvider**（按 `contracts.WorkbenchStateProto`
与 `contracts.WORKER_SIGNALS` 写）。C1-a 的实物落地后，C1-int 拿这三个假件与 §2.1 / §2.2
逐条核对签名；`tests/test_ui_scenes.py` 也从这里 import 它们。
"""
import contextlib
import os

import pytest

import ui_harness as H                    # noqa: F401  （顺带把 tests/ 与仓库根插进 sys.path）

pytest.importorskip("PySide6")

from PySide6 import QtCore, QtGui, QtWidgets      # noqa: E402

from dreg_verify import session                   # noqa: E402
from dreg_verify.ui import app as A               # noqa: E402
from dreg_verify.ui import contracts, names, terms, theme   # noqa: E402
from dreg_verify.ui import widgets as W           # noqa: E402


# ══════════════════════════ 假件（按 contracts 的 Protocol 写）══════════════════════════
def make_models(n=5):
    """n 条 lite 视图模型（键集 = contracts.LITE_MODEL_KEYS）。前 3 条 ok，其余 unresolved。"""
    out = []
    for i in range(n):
        ok = i < 3
        out.append({
            "name": "sig_%02d" % i, "disp": "sig_%02d[3:0]" % i, "owner": "ow%d" % (i % 2),
            "width": 4, "kind": "mux" if i % 2 else "logic",
            "status": "ok" if ok else "unresolved",
            "status_detail": "clean" if ok else "unresolved",
            "note": "", "issues": [] if ok else ["缺一根输入"], "matched_name": "",
            "n_leaves": 1, "n_vectors": 4 + i, "form": "select", "form_label": "选路",
            "probe_net": "sig_%02d" % i, "prefix": "", "assert_id": "A%d" % i,
            "expr": "a & b", "input_names": ["a", "b"], "supplement": False,
            "normalized_note": "", "out_net": "sig_%02d" % i,
        })
    return out


def skeleton_of(models):
    """provider.skeleton_models() 的口径：status=pending、没有向量数（N8 第一趟）。"""
    out = []
    for m in models:
        s = dict(m)
        s.update({"status": "pending", "status_detail": "pending", "n_vectors": None,
                  "issues": [], "form": "", "form_label": ""})
        out.append(s)
    return out


def fake_coverage(view_id="topout", store=None):
    """真 `session.CoverageState`，只是 load/save 指到内存 dict。

    C1-int：覆盖度控件要读 `effective_label` / `effective_chain` / `sig_cov_of`，
    自己捏一个「最小面」只会漏方法——三层档的口径本来就只该有一份。"""
    st = store if store is not None else {}
    return session.CoverageState(view_id, load=lambda: st, save=lambda d: st.update(d))


class FakeProvider(object):
    """contracts.ProviderProto 的最小面。"""
    has_chain = True
    supports_sig_cov = True
    kind_label = {}

    def __init__(self, models, view_id="topout"):
        self.view_id = view_id
        self._models = list(models)

    def title(self):
        return "Topout"

    def empty_hint(self):
        return ""

    def has_page(self):
        return True

    def skeleton_models(self):
        return skeleton_of(self._models)

    def view_models(self, mode="max", max_tests=256, exhaustive=False, sig_cov=None, form_cov=None,
                    progress=None, should_cancel=None, lite=False):
        return list(self._models)

    def analyze(self, name, mode="max", max_tests=256, exhaustive=False, mux_data=None,
                want_graph=False):
        return None


class FakeState(QtCore.QObject):
    """contracts.WorkbenchStateProto 的可测实现；信号名与 contracts.STATE_SIGNALS 逐条一致。"""

    workbookChanged = QtCore.Signal()
    loadFailed = QtCore.Signal(str)
    scopeChanged = QtCore.Signal(str)
    modelsChanged = QtCore.Signal(str)
    modelUpdated = QtCore.Signal(str, str)
    currentChanged = QtCore.Signal(str)
    checksChanged = QtCore.Signal(str)
    negsChanged = QtCore.Signal(str)
    editsChanged = QtCore.Signal(str, str)
    coverageChanged = QtCore.Signal(str)
    configChanged = QtCore.Signal(str)
    settingsChanged = QtCore.Signal()
    statusMessage = QtCore.Signal(str)
    exportRecorded = QtCore.Signal(str)

    def __init__(self, models=None, recent=(), settings=None, fail="", version="abc1234"):
        super().__init__()
        self.excel_path = ""
        self.loaded_path = ""
        self.wb = None
        self.scope = contracts.DEFAULT_VIEW_ID
        self.current_name = ""
        self.probe_prefixes = {}
        self.force_signals = set()
        self.logic_overrides = {}
        self.include_risky = True
        self.engine_lock = None
        self._full = list(models if models is not None else make_models())
        self._models = {}
        self._settings = dict(settings or {})
        self._recent = list(recent)
        self._exports = {}
        self._cov = fake_coverage()
        self._fail = fail
        self._version = version
        self._fingerprint = {}          # C-265：跑完一趟记一次
        self._checks = {}               # view_id -> set | None（None = 全勾）
        self._negs = {}
        self._suspended = 0
        self.load_calls = []
        self.saved_settings = []
        self.persisted = []             # 每「写一次盘」记一条（C-244 数次数用）

    # ── 表与范围 ──
    def load(self, path):
        self.load_calls.append(str(path))
        if self._fail:
            self.loadFailed.emit(terms.STATUS_LOAD_FAILED_FMT.format(reason=self._fail))
            return False
        self.excel_path = self.loaded_path = str(path)
        self.wb = object()
        self._models = {}
        self._fingerprint = {}          # 换表 = 上一张表的一切清干净（真 state 同）
        self._checks = {}
        self._negs = {}
        self._recent.insert(0, contracts.RecentExcel(path=str(path), ts="", n_signals=len(self._full)))
        self.workbookChanged.emit()
        return True

    def set_scope(self, view_id):
        self.scope = view_id
        self.scopeChanged.emit(view_id)

    def provider(self, view_id=None):
        if self.wb is None:
            return None
        return FakeProvider(self._full, view_id or self.scope)

    def page_available(self, view_id):
        return True

    def code_version(self):
        return self._version

    # ── 清单模型 ──
    def models(self, view_id=None):
        return list(self._models.get(view_id or self.scope, []))

    def model_of(self, name, view_id=None):
        for m in self.models(view_id):
            if m.get("name") == name:
                return m
        return None

    def set_models(self, view_id, models, partial=False):
        cur = self._models.get(view_id)
        if not partial or not cur:
            self._models[view_id] = [dict(m) for m in models]
        else:
            by = {m.get("name"): m for m in models}
            self._models[view_id] = [dict(by.get(m.get("name"), m)) for m in cur]
        if not partial:
            self._fingerprint[view_id] = self.fingerprint(view_id)
        self.modelsChanged.emit(view_id)

    def update_model(self, view_id, model):
        cur = self._models.get(view_id) or []
        for i, m in enumerate(cur):
            if m.get("name") == model.get("name"):
                cur[i] = dict(model)
                self.modelUpdated.emit(view_id, str(model.get("name") or ""))
                return

    def fingerprint(self, view_id=None):
        vid = view_id or self.scope
        return "fp|%s|%s|%d" % (vid, self._cov.global_label, int(self._cov.max_tests))

    def needs_analysis(self, view_id=None):
        vid = view_id or self.scope
        return self._fingerprint.get(vid) != self.fingerprint(vid)

    def analysis_request(self, view_id=None):
        vid = view_id or self.scope
        mode, exh = self._cov.mode()
        return contracts.AnalysisRequest(view_id=vid, mode=mode, max_tests=int(self._cov.max_tests),
                                         exhaustive=bool(exh), fingerprint=self.fingerprint(vid))

    def set_current(self, name):
        self.current_name = str(name or "")
        self.currentChanged.emit(self.current_name)

    # ── 勾选 / 反例 ──
    def checked(self, view_id=None):
        return self._checks.get(view_id or self.scope)

    def is_checked(self, name, view_id=None):
        ch = self.checked(view_id)
        return True if ch is None else (str(name).lower() in ch)

    def checked_names(self, view_id=None):
        ch = self.checked(view_id)
        got = [m["name"] for m in self.models(view_id)]
        return got if ch is None else [n for n in got if n.lower() in ch]

    def set_checked(self, names, on, view_id=None):
        vid = view_id or self.scope
        all_low = {str(m["name"]).lower() for m in self.models(vid)}
        cur = self._checks.get(vid)
        cur = set(all_low) if cur is None else set(cur)
        for n in names or []:
            cur.add(str(n).lower()) if on else cur.discard(str(n).lower())
        self._checks[vid] = None if (all_low and cur >= all_low) else cur
        self.checksChanged.emit(vid)
        self.persist_edits()

    def negs(self, view_id=None):
        return self._negs.setdefault(view_id or self.scope, set())

    def has_negatives(self, name, view_id=None):
        return str(name).lower() in self.negs(view_id)

    def set_neg(self, name, on, view_id=None):
        vid = view_id or self.scope
        negs = self.negs(vid)
        negs.add(str(name).lower()) if on else negs.discard(str(name).lower())
        self.negsChanged.emit(vid)
        self.persist_edits()
        return (1, 0)

    def protected_negatives(self, names, view_id=None):
        return []

    # ── 覆盖度 / 偏好 / 存盘 ──
    def coverage(self, view_id=None):
        return self._cov

    def coverage_touched(self, view_id=None):
        self.coverageChanged.emit(view_id or self.scope)

    def settings(self):
        return dict(self._settings)

    def save_settings(self, patch):
        self._settings.update(patch or {})
        self.saved_settings.append(dict(patch or {}))
        self.settingsChanged.emit()

    @contextlib.contextmanager
    def suspend_persist(self):
        """C-244：批量操作期间不逐格写盘，退出时统一写一次。"""
        self._suspended += 1
        try:
            yield self
        finally:
            self._suspended -= 1
            if self._suspended <= 0 and self._pending:
                self._pending = False
                self.persist_edits()

    _pending = False

    def persist_edits(self):
        if self._suspended > 0:
            self._pending = True
            return False
        self.persisted.append(dict((k, sorted(v) if v else v) for k, v in self._checks.items()))
        return True

    def status(self, text):
        if text:
            self.statusMessage.emit(str(text))

    def recent_excels(self):
        return list(self._recent)

    def last_export(self, kind):
        return self._exports.get(kind)

    def record_export(self, kind, path):
        self._exports[kind] = contracts.LastExport(path=path, ts="")
        self.exportRecorded.emit(kind)


class FakeWorker(QtCore.QObject):
    """contracts.WORKER_SIGNALS 的同步实现（offscreen 测试不起线程）。

    `stop_after=k` → 推进 k 个信号后**停在第 k+1 个**不再动（场景⑧：卡在第 3 个信号），
    直到外部调 `cancel()`。"""

    started = QtCore.Signal(str, int)
    progress = QtCore.Signal(int, int, str)
    signalDone = QtCore.Signal(str, dict)
    finished = QtCore.Signal(str, list)
    cancelled = QtCore.Signal(str, list)
    failed = QtCore.Signal(str, str)

    def __init__(self, stop_after=None, fail_with=""):
        super().__init__()
        self._stop_after = stop_after
        self._fail_with = fail_with
        self._running = False
        self._vid = ""
        self._all = []
        self._done = []
        self.starts = []                            # 每开一趟记一条（C-265 数趟数用）

    def start(self, provider, request, engine_lock=None, total=0):
        """签名 = `contracts.AnalysisWorkerProto.start` 定版的四参（实物同）。"""
        self.starts.append((request.view_id, request.fingerprint, engine_lock, int(total or 0)))
        self._vid = request.view_id
        self._all = list(provider.view_models(request.mode, request.max_tests, request.exhaustive,
                                              request.sig_cov, request.form_cov, lite=True))
        self._done = []
        self._running = True
        self.started.emit(self._vid, int(total or 0) or len(self._all))
        if self._fail_with:
            self._running = False
            self.failed.emit(self._vid, self._fail_with)
            return
        for i, m in enumerate(self._all):
            if self._stop_after is not None and i >= self._stop_after:
                return                              # 卡住：worker 仍 running，等 cancel()
            self._done.append(m)
            self.progress.emit(len(self._done), len(self._all), str(m.get("name") or ""))
            self.signalDone.emit(str(m.get("name") or ""), dict(m))
        self._running = False
        self.finished.emit(self._vid, list(self._all))

    def cancel(self):
        if not self._running:
            return
        self._running = False
        self.cancelled.emit(self._vid, list(self._done))

    def is_running(self):
        return self._running


# ══════════════════════════ fixtures ══════════════════════════
@pytest.fixture
def qapp():
    return H.app()


def make_win(qapp, state=None, worker_factory=None, argv=(), show=False):
    st = state if state is not None else FakeState()
    w = A.build_window(argv, state=st, worker_factory=worker_factory or (lambda: FakeWorker()))
    if show:
        w.show()
        qapp.processEvents()
    return w


def touch_xlsx(tmp_path, name="t.xlsx"):
    p = tmp_path / name
    p.write_bytes(b"PK\x03\x04not-a-real-xlsx")
    return str(p)


# ══════════════════════════ widgets.py ══════════════════════════
def test_widgets_public_api():
    for attr in ("FlowLayout", "CheckableMenu", "SegmentedControl", "ProgressBadge",
                 "ErrorBar", "Popover", "mono_font", "ui_font"):
        assert hasattr(W, attr), attr
    assert issubclass(W.CheckableMenu, QtWidgets.QMenu)
    # I-19：widgets / app 都不准 import gui（FlowLayout / CheckableMenu 是复制过来的）
    src = open(W.__file__, encoding="utf-8").read() + open(A.__file__, encoding="utf-8").read()
    bad = [ln for ln in src.splitlines()
           if ln.lstrip().startswith(("import ", "from ")) and "gui" in ln]
    assert not bad, bad


def test_c263_flowlayout_wraps(qapp):
    host = QtWidgets.QWidget()
    lay = W.FlowLayout(host, hspacing=6, vspacing=4)
    for i in range(8):
        b = QtWidgets.QPushButton("按钮%d" % i, host)
        b.setFixedSize(100, 24)
        lay.addWidget(b)
    assert lay.count() == 8
    assert lay.row_count(2000) == 1                 # 宽屏一行放得下
    assert lay.row_count(250) > 1                   # 窄屏自动换行（C-263）
    assert lay.heightForWidth(250) > lay.heightForWidth(2000)
    assert lay.minimumSize().width() <= 110         # 面板最小宽 ≈ 单个最宽按钮 → splitter 拖得动
    host.deleteLater()


def test_c264_mono_font_on_names(qapp):
    f = W.mono_font()
    assert f.family() == theme.FONT_MONO
    assert f.pixelSize() == int(round(theme.FS_MONO))
    assert theme.FONT_MONO_FALLBACKS[1] in f.families()
    u = W.ui_font(theme.FS_UI_TITLE, bold=True)
    assert u.family() == theme.FONT_UI and u.bold()
    w = make_win(qapp)
    assert H.find(w, names.TOP_EXCEL_PATH).font().family() == theme.FONT_MONO
    w.close()


def test_widgets_segmented_control_disabled_item_and_tooltip(qapp):
    seg = W.SegmentedControl([(v, terms.SCOPE_LABELS[v], names.fmt_scope_btn(v))
                              for v in contracts.VIEW_IDS])
    assert seg.current() == "topout"
    assert seg.button("dft").objectName() == names.fmt_scope_btn("dft")
    got = []
    seg.currentChanged.connect(got.append)
    seg.set_current("mux")
    assert got == ["mux"] and seg.current() == "mux"
    seg.set_item_enabled("dft", False, terms.SCOPE_MISSING_FMT.format(page="dft"))
    assert not seg.is_item_enabled("dft")
    assert seg.button("dft").toolTip() == "本表无 dft 页"
    seg.set_current("dft")                          # 置灰项点不动
    assert seg.current() == "mux"
    seg.deleteLater()


def test_widgets_progress_badge(qapp):
    p = W.ProgressBadge()
    assert p.width() == theme.PROGRESS_W and p.height() == theme.PROGRESS_H
    assert p.ratio() == 0.0
    p.set_value(7, 25)
    assert p.value() == (7, 25) and p.text() == "7/25"
    assert abs(p.ratio() - 0.28) < 1e-9
    p.deleteLater()


def test_widgets_error_bar_scrubs_and_closes(qapp):
    bar = W.ErrorBar()
    assert not bar.isVisible()
    msg = bar.show_error("cone 展开失败：regmap 没这张表，必 CUVUNF")
    assert "cone" not in msg and "regmap" not in msg and "CUVUNF" not in msg
    assert bar.text() == msg
    seen = []
    bar.dismissed.connect(lambda: seen.append(1))
    H.find(bar, names.ERROR_CLOSE).click()
    assert seen == [1] and bar.text() == "" and not bar.isVisible()
    bar.deleteLater()


def test_widgets_popover_closes_on_outside_click(qapp):
    host = QtWidgets.QWidget()
    host.resize(900, 600)
    pop = W.Popover(host, width=theme.COV_POP_W, object_name=names.COV_POPOVER)
    pop.content_layout().addWidget(QtWidgets.QLabel("x", pop))
    host.show()
    qapp.processEvents()
    pop.open_at(QtCore.QPoint(40, 40))
    assert pop.is_open() and pop.width() == theme.COV_POP_W
    H.click(host, (800, 550))                       # 点浮层外部
    qapp.processEvents()
    assert not pop.is_open()
    host.close()
    host.deleteLater()


# ══════════════════════════ app.py：载表 ══════════════════════════
def test_c001_browse_loads_immediately(qapp, monkeypatch, tmp_path):
    p = touch_xlsx(tmp_path)
    rec = H.auto_dialogs(monkeypatch, answers={"getOpenFileName": (p, "")})
    st = FakeState()
    w = make_win(qapp, state=st)
    assert w.is_empty_state()
    assert H.find(w, names.TOP_BROWSE_BTN).text().endswith(contracts.SHORTCUTS["open"])
    H.find(w, names.TOP_BROWSE_BTN).click()         # 选完即载入，不用再点一次「载入」
    assert st.load_calls == [p]
    assert not w.is_empty_state()
    assert rec.count("getOpenFileName") == 1
    w.close()


def test_c002_reload_button_label_switches(qapp, monkeypatch, tmp_path):
    p = touch_xlsx(tmp_path)
    H.auto_dialogs(monkeypatch, answers={"getOpenFileName": (p, "")})
    st = FakeState()
    w = make_win(qapp, state=st)
    btn = H.find(w, names.TOP_LOAD_BTN)
    assert btn.text().startswith(terms.TOP_LOAD) and not btn.text().startswith(terms.TOP_RELOAD)
    assert contracts.SHORTCUTS["load"] in btn.text()
    w.load_path(p)
    assert btn.text().startswith(terms.TOP_RELOAD)
    btn.click()                                     # 重新载入同一路径
    assert st.load_calls == [p, p]
    w.close()


def test_c003_autoload_last_excel(qapp, tmp_path):
    p = touch_xlsx(tmp_path, "last.xlsx")
    st = FakeState(settings={"last_excel": p})
    w = make_win(qapp, state=st)
    assert st.load_calls == [p]                     # 启动直接进工作台
    assert not w.is_empty_state()
    assert H.find(w, names.TOP_EXCEL_PATH).text() == p
    w.close()

    st2 = FakeState(settings={"last_excel": str(tmp_path / "没有这张表.xlsx")})
    w2 = make_win(qapp, state=st2)
    assert st2.load_calls == [] and w2.is_empty_state()
    w2.close()


def test_c004_argv_xlsx_skips_empty_state(qapp, tmp_path):
    argp = touch_xlsx(tmp_path, "cmdline.xlsx")
    lastp = touch_xlsx(tmp_path, "last.xlsx")
    st = FakeState(settings={"last_excel": lastp})
    w = make_win(qapp, state=st, argv=["--x", argp])
    assert st.load_calls == [argp]                  # 命令行优先于 last_excel
    assert not w.is_empty_state()
    w.close()


def test_c005_c272_bad_excel_shows_error_bar_not_dialog(qapp, monkeypatch, tmp_path):
    rec = H.auto_dialogs(monkeypatch)
    st = FakeState(fail="Topout 页缺 owner 列（第 3 行）")
    w = make_win(qapp, state=st)
    assert not w.load_path(touch_xlsx(tmp_path, "bad.xlsx"))
    bar = H.find(w, names.ERROR_BAR)
    assert bar.isVisibleTo(w)
    text = H.find(w, names.ERROR_TEXT).text()
    assert "Topout 页缺 owner 列" in text
    assert "Traceback" not in text                  # C-272：不把 traceback 塞给用户
    assert rec.count("critical") == 0 and rec.count("QMessageBox.warning") == 0
    assert w.is_empty_state()                       # 工具不退出，仍停在空态
    H.find(w, names.ERROR_CLOSE).click()
    assert not bar.isVisible()
    w.close()


def test_load_exception_does_not_crash(qapp, monkeypatch, tmp_path):
    """载表抛异常也只写错误条（C-005 的另一半：state.load 直接抛）。

    R3-03 改口径：错误条上写的是**本工具的话**，不是异常原文 —— 以前这里断言的是
    「异常的 message 原样出现在错误条上」，等于把 `[Errno 2] No such file or directory:
    'C:\\\\…\\\\x.xlsx'` 钉成了规范（真机上就是这么显示的）。现在断言反过来：
    异常原文一个字都不进界面，界面上是 `terms.exc_text` 那一句 + 是哪个文件。"""
    rec = H.auto_dialogs(monkeypatch)
    st = FakeState()

    def boom(_p):
        raise ValueError("openpyxl can't open this file [Errno 2]")
    monkeypatch.setattr(st, "load", boom)
    w = make_win(qapp, state=st)
    p = touch_xlsx(tmp_path, "boom.xlsx")
    assert not w.load_path(p)
    text = H.find(w, names.ERROR_TEXT).text()
    assert "openpyxl" not in text and "Errno" not in text        # R3-03：不贴异常原文
    assert terms.EXC_TEXT["ValueError"] in text
    assert "boom.xlsx" in text and str(p) not in text            # 只给文件名，不给本机全路径
    assert rec.count("critical") == 0
    w.close()


# ══════════════════════════ app.py：状态栏 ══════════════════════════
def test_c006_status_left_counts(qapp, tmp_path):
    st = FakeState()
    w = make_win(qapp, state=st)
    w.load_path(touch_xlsx(tmp_path))
    txt = H.find(w, names.STATUS_LEFT).text()
    # 5 条：logic 3 + mux 2；前 3 条 clean（可建），后 2 条 unresolved（有问题）
    assert txt == terms.STATUS_LOADED_FMT.format(n=5, nl=3, nm=2, nt=3, np=2)
    assert "非 clean" not in txt                    # 明细只进 tooltip，不占状态栏
    assert H.find(w, names.STATUS_LEFT).toolTip().startswith("非 clean 2")
    w.close()


def test_c269_status_message_channel(qapp):
    st = FakeState()
    w = make_win(qapp, state=st)
    st.statusMessage.emit(terms.STATUS_COPIED)
    assert H.find(w, names.STATUS_LEFT).text() == terms.STATUS_COPIED
    st.statusMessage.emit("从 cone 展开取到 3 条")   # I-12：后端文本过 scrub
    assert "cone" not in H.find(w, names.STATUS_LEFT).text()
    w.close()


def test_status_right_last_export(qapp, tmp_path):
    st = FakeState()
    w = make_win(qapp, state=st)
    assert H.find(w, names.STATUS_RIGHT).text() == ""
    st.record_export("sv", r"D:\work\wr_rf_tc.sv")
    assert r"D:\work\wr_rf_tc.sv" in H.find(w, names.STATUS_RIGHT).text()
    st.editsChanged.emit("topout", "sig_00")
    assert H.find(w, names.STATUS_AUTOSAVE).text().startswith("编辑自动存盘")
    w.close()


# ══════════════════════════ app.py：快捷键 / 标题 / 版面 ══════════════════════════
def test_c253_c254_c255_c256_c257_shortcuts_bound(qapp, monkeypatch):
    # C4-int 起 Ctrl+G / Ctrl+R 真的会 `exec()` 一个导出中心 —— offscreen 下模态框
    # 不拦就是整条测试挂住（不是红，是永远不返回）。
    H.auto_dialogs(monkeypatch)
    w = make_win(qapp)
    got = {sc.key().toString() for sc in w.findChildren(QtGui.QShortcut)}
    want = {contracts.SHORTCUTS[k] for k in A.APP_SHORTCUTS}
    # Esc 不在 `contracts.SHORTCUTS` 里：那张表是「要印在顶栏按钮上的快捷键」，
    # Esc 谁也不印，它只管从「主视图独占中央区」退出来（C-280 / C-297）。
    assert got - {"Esc"} == want == {"Ctrl+O", "Ctrl+L", "Ctrl+P", "Ctrl+R", "Ctrl+G", "Ctrl+Shift+D"}
    assert "Esc" in got
    assert "Ctrl+D" not in got                      # 裁决①：Ctrl+D 留给真值表「复制列」
    for k in A.APP_SHORTCUTS:
        assert w.shortcuts[k].objectName() == A.fmt_shortcut(k)
    seen = []
    w.exportCenterRequested.connect(seen.append)
    w.svPreviewRequested.connect(lambda: seen.append("sv"))
    w.diagnosticsRequested.connect(lambda s: seen.append("diag:%s" % s))
    w.shortcuts["export_report"].activated.emit()   # Ctrl+R 预选「报告」行（C-256）
    w.shortcuts["export_center"].activated.emit()   # Ctrl+G 打开导出中心（C-257）
    w.shortcuts["sv_preview"].activated.emit()      # Ctrl+P .sv 预览（C-255）
    w.shortcuts["diagnostics"].activated.emit()
    assert seen == ["report", "", "sv", "diag:"]
    assert H.find(w, names.MAIN_VIEW).currentIndex() == 1
    w.close()


def test_c259_window_title_version_no_git(qapp):
    w = make_win(qapp, state=FakeState(version="abc1234"))
    assert w.windowTitle() == terms.WINDOW_TITLE_FMT.format(version="abc1234")
    assert "git" not in w.windowTitle().lower()     # §8.1 / 裁决⑬
    w.close()
    w2 = make_win(qapp, state=FakeState(version=""))
    assert w2.windowTitle() == terms.WINDOW_TITLE_NOVER
    w2.close()


def test_c260_c261_c262_default_sizes_and_clamps(qapp, tmp_path):
    st = FakeState()
    w = make_win(qapp, state=st, show=True)
    assert (w.width(), w.height()) == (theme.WIN_W, theme.WIN_H)          # C-260
    assert w.preferred_sizes() == {"listW": theme.LIST_W, "sideW": theme.SIDE_W,
                                   "truthH": theme.TRUTH_H, "chainH": theme.CHAIN_H}   # C-261
    sp = H.find(w, names.WIN_SPLIT_MAIN, QtWidgets.QSplitter)
    assert sp.handleWidth() == theme.HANDLE_W and not sp.childrenCollapsible()          # C-262
    lp = H.find(w, names.LIST_PANEL)
    assert (lp.minimumWidth(), lp.maximumWidth()) == theme.CLAMP_LIST
    side = H.find(w, names.SIDE_PANEL)
    assert (side.minimumWidth(), side.maximumWidth()) == theme.CLAMP_SIDE
    for nm in (names.WIN_SPLIT_SIDE, names.WIN_SPLIT_TRUTH_FLOW, names.WIN_SPLIT_CHAIN_INPUTS):
        s = H.find(w, nm, QtWidgets.QSplitter)
        assert s.handleWidth() == theme.HANDLE_W and not s.childrenCollapsible()
    # 1366×768：清单收到 420、右栏折叠
    w.load_path(touch_xlsx(tmp_path))
    w.resize(theme.WIN_MIN_W, theme.WIN_MIN_H)
    qapp.processEvents()
    assert w.is_narrow()
    assert sp.sizes()[0] == theme.LIST_W_NARROW
    assert not side.isVisibleTo(w)
    w.resize(theme.WIN_W, theme.WIN_H)
    qapp.processEvents()
    assert not w.is_narrow() and side.isVisibleTo(w)
    w.close()


def test_splitter_sizes_persist_through_state(qapp):
    st = FakeState(settings={"listW": 700, "sideW": 320, "truthH": 500, "chainH": 200})
    w = make_win(qapp, state=st, show=True)
    assert w.preferred_sizes() == {"listW": 700, "sideW": 320, "truthH": 500, "chainH": 200}
    H.find(w, names.WIN_SPLIT_MAIN, QtWidgets.QSplitter).splitterMoved.emit(700, 1)
    assert st.saved_settings and "listW" in st.saved_settings[-1]
    w.close()


def test_c266_c267_single_workbench_no_tabs(qapp, tmp_path):
    st = FakeState()
    w = make_win(qapp, state=st, show=True)
    assert w.findChildren(QtWidgets.QTabWidget) == []       # C-267：没有 6 个顶层 tab
    stack = H.find(w, names.WIN_STACK, QtWidgets.QStackedWidget)
    assert stack.count() == 2                               # 空态 / 工作台（载入态在工作台里）
    assert not H.find(w, names.FILTER_BAR).isVisibleTo(w)   # ② 只在 loaded 时出现
    w.load_path(touch_xlsx(tmp_path))
    assert H.find(w, names.FILTER_BAR).isVisibleTo(w)
    assert H.find(w, names.WIN_WORKBENCH).isVisibleTo(w)
    for nm in (names.LIST_PANEL, names.HDR_BAR, names.MAIN_VIEW, names.SIDE_PANEL):
        assert H.find(w, nm) is not None
    w.close()


def test_c282_window_owns_one_highlight_bus(qapp):
    """I-18：「当前线网」总线全窗口一个、由组合根持有（C2/C3 的四个订阅方连它，不互相 import）。"""
    from dreg_verify.ui.bus import HighlightBus
    w = make_win(qapp)
    assert isinstance(w.bus, HighlightBus)
    got = []
    w.bus.netSelected.connect(lambda net, origin: got.append((net, origin)))
    w.bus.select("D_RF_EN[3:0]", "list")
    assert got == [("d_rf_en", "list")] and w.bus.current_net == "d_rf_en"
    w.close()


def test_c251_module_entry_provides_main(qapp):
    assert callable(A.main) and callable(A.build_window)
    assert A.main.__module__ == "dreg_verify.ui.app"


# ══════════════════════════ app.py：objectName 覆盖 / 占位区 ══════════════════════════
def test_ui_names_present_for_app_areas(qapp, tmp_path):
    st = FakeState()
    w = make_win(qapp, state=st, show=True)
    reg = names.all_names()
    want = {k: v for k, v in reg.items()
            if k.split("_")[0] in ("WIN", "TOP", "STATUS", "EMPTY", "LOADING", "ERROR")}
    want.pop("HARNESS_EXCEL_PATH_EDIT", None)
    missing = [k for k, v in want.items() if H._find_opt(w, v) is None]
    assert not missing, "app.py 没给这些 objectName 落点：%s" % missing
    # 占位区也都在（C1-int / C2 / C3 换真件）
    for nm in A.PLACEHOLDER_AREAS:
        assert H.find(w, nm).property("placeholder") is True
    w.close()


def test_harness_can_drive_v2_window(qapp, tmp_path):
    """ui_harness 的 _set_excel_path / _do_load 退回路径在 v2 窗口上仍能用（C1-int 切工厂前提）。"""
    st = FakeState()
    w = make_win(qapp, state=st)
    p = touch_xlsx(tmp_path)
    H._set_excel_path(w, p)
    H._do_load(w)
    assert st.load_calls == [p]
    w.close()


# ══════════════════════════ app.py：场景路由 ══════════════════════════
def test_route_reason_action_targets(qapp, monkeypatch):
    H.auto_dialogs(monkeypatch)                     # export_nets 会真开一次导出中心（模态）
    w = make_win(qapp)
    diag, exp = [], []
    w.diagnosticsRequested.connect(diag.append)
    w.exportCenterRequested.connect(exp.append)
    assert w.route_reason_action("diag_cuvunf") == "diag_cuvunf"
    assert w.route_reason_action("diag_risky") == "diag_risky"
    assert w.route_reason_action("export_nets") == "nets"
    assert diag == ["diag_cuvunf", "diag_risky"] and exp == ["nets"]
    w.route_reason_action("copy_rows", {"text": "mux 页 行 188/189"})
    assert QtWidgets.QApplication.clipboard().text() == "mux 页 行 188/189"
    assert H.find(w, names.STATUS_LEFT).text() == terms.STATUS_COPIED
    assert w.route_reason_action("没这个目标") == ""
    w.close()


# ══════════════════════════ app.py：worker（C-276 的 app 侧）══════════════════════════
def test_worker_wiring_skeleton_then_upgrade(qapp, tmp_path):
    st = FakeState()
    w = make_win(qapp, state=st)
    w.load_path(touch_xlsx(tmp_path))
    assert not w.worker.is_running()                       # 同步 FakeWorker：一趟跑完
    assert [m["status"] for m in st.models()] == ["ok"] * 3 + ["unresolved"] * 2
    assert not H.find(w, names.LOADING_PANEL).isVisibleTo(w)


def test_loading_title_names_the_page_not_topout(qapp, tmp_path):
    """⑮ 的标题按范围说话：Topout 是「展开」，子页是「正在分析 <页> 页」（terms 两条模板都在用）。"""
    st = FakeState(models=make_models(5))
    w = make_win(qapp, state=st, worker_factory=lambda: FakeWorker(stop_after=1))
    w.load_path(touch_xlsx(tmp_path))
    assert H.find(w, names.LOADING_TITLE).text() == terms.LOADING_TITLE_FMT.format(done=1, total=5)
    st.set_scope("mux")                                 # 换范围 → 新的一趟，仍卡在第 2 个
    assert H.find(w, names.LOADING_TITLE).text() == terms.LOADING_TITLE_PAGE_FMT.format(
        page="mux", done=1, total=5)
    w.close()


def test_worker_failed_goes_to_error_bar(qapp, monkeypatch, tmp_path):
    rec = H.auto_dialogs(monkeypatch)
    st = FakeState()
    w = make_win(qapp, state=st, worker_factory=lambda: FakeWorker(fail_with="分析整表时出错：cone 断了"))
    w.load_path(touch_xlsx(tmp_path))
    txt = H.find(w, names.ERROR_TEXT).text()
    assert "分析整表时出错" in txt and "cone" not in txt    # I-12
    assert rec.count("critical") == 0
    assert not H.find(w, names.LOADING_PANEL).isVisibleTo(w)
    w.close()
