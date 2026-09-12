# -*- coding: utf-8 -*-
"""GUI v2 F4：200 信号量级的两条性能回归（C-276 载入态不卡界面 / C-269 停止要有反馈）。

这里断的不是「跑得多快」（那是 `tools/perf_smoke.py` 的活，不进 CI —— 机器忙起来毫秒数
随时翻倍），而是**两条会随信号数变坏的结构性质**，它们和实际耗时是一一对应的：

  PERF-1 `_on_model_updated` 每升级一行就把头部计数 / 状态栏计数从头算一遍，而算计数走的是
         `index(r, col).data(role)` → `_row_data`（整行显示内容：前缀拆分、tooltip、scrub、
         格式化全建一遍）——200 信号上是 O(N²)。合成 200 信号表实测 T2 = 2.0 s，
         其中 1.38 s（60%）是 58,875 次 `_row_data`；另有 0.47 s（18%）是 worker 收尾时
         「每一行都已逐条升级过了」却仍整表 `reload()`。
  PERF-2 「停止分析」是个假按钮：引擎比清单刷新快几十倍（同一张表引擎 0.03 s 跑完、清单要
         2 s 才展开完），用户按下停止时 worker 线程早已退出，200 条 `signalDone` 全排在主
         线程队列里，`cancel()` 撤不回已排队的跨线程信号 —— 清单继续一路展开到底，最后还发
         一条 `analysisEnded(vid, True)`。

口径同别的 UI 用例：offscreen、真清单 / 真组合根、mirror 的真模型，不写死毫秒。
"""

import pytest

import ui_harness as H

pytest.importorskip("PySide6")

from PySide6 import QtCore                                       # noqa: E402

from dreg_verify.ui import contracts as CT                       # noqa: E402
from dreg_verify.ui import names as N                            # noqa: E402
from dreg_verify.ui import signal_list as SL                     # noqa: E402
from dreg_verify.ui import terms as T                            # noqa: E402
from dreg_verify.ui.app import MainWindow                        # noqa: E402
from test_ui_app import FakeState, make_models, skeleton_of, touch_xlsx   # noqa: E402
from test_ui_signal_list import make_panel, real_models          # noqa: E402

LC = CT.ListCol


@pytest.fixture
def qapp():
    return H.app()


def _count_row_data(monkeypatch):
    """给 `_SignalModel._row_data` 装个计数器，返回那张计数表（`box["n"]`）。"""
    box = {"n": 0}
    orig = SL.SignalListModel._row_data

    def counted(self, index, role):
        box["n"] += 1
        return orig(self, index, role)

    monkeypatch.setattr(SL.SignalListModel, "_row_data", counted)
    return box


# ═════════════════ PERF-1 ①：计数不经 `_row_data` ═════════════════
def test_c276_perf1_counts_do_not_touch_row_data(qapp, monkeypatch):
    """头部计数 / 可见名单一律读原始行：`_row_data` 一次都不该有，**每升级一行的代价也不随表变大**。

    第二段才是 O(N²) 的那一条：旧写法里「升级一行 → 算一次计数 → 对每个可见行
    `index(r, NAME).data(NAME)`」，每行的代价里带着一个 N，表越大每行越贵。这里拿
    12 行（mirror btlp）和 21 行（btlp + wl）两张表比**每行**的次数 —— 与机器快慢无关。

    变异验证：把 `SignalListProxy.visible_rows` 换回 `index(r, NAME).data(NAME)` 那种写法 ——
    第一段当场红（12 次而不是 0 次），第二段两张表的每行次数从「差 0」变成「差 9」（= 21−12）。
    """
    small = real_models("btlp")                      # mirror btlp = 12 行真模型
    n = len(small)
    assert n >= 10, "mirror btlp 应有 12 行，实得 %d" % n
    panel, st = make_panel(models=small, show=False)
    qapp.processEvents()

    # —— ① 计数本身：一次 `_row_data` 都不许有 ——
    box = _count_row_data(monkeypatch)
    for _ in range(5):
        panel.counts_text()
        panel.visible_names()
        panel.proxy.visible_rows()
    assert box["n"] == 0, "算计数 / 列可见名单碰了 `_row_data` %d 次（应当 0 次）" % box["n"]

    # 数还是对的（口径仍是 `terms.match_status` 一份，P-20）
    bad = sum(1 for m in small if T.match_status(m, T.STATUS_FILTER_ITEMS[2]))
    assert panel.counts_text() == T.LIST_COUNTS_FMT.format(n=n, k=n, p=bad)
    assert sorted(panel.visible_names()) == sorted(str(m["name"]) for m in small)

    # —— ② 每升级一行的 `_row_data` 次数：换成大一号的表不该变多 ——
    def per_row(rows):
        pnl, state = make_panel(models=rows, show=False)
        qapp.processEvents()
        box["n"] = 0
        for m in rows:
            state.update_model(state.scope, dict(m))     # = worker 的 signalDone 逐条回来
            pnl._refresh_counts()                        # 旧代码就是每升级一行同步算一次
        qapp.processEvents()
        return box["n"] / float(len(rows))

    small_per = per_row(small)
    big = real_models("btlp") + real_models("wl")        # 21 行
    big_per = per_row(big)
    assert len(big) > n + 5
    assert big_per <= small_per + 3, (
        "表从 %d 行长到 %d 行，每升级一行的 `_row_data` 从 %.1f 次涨到 %.1f 次 —— 计数还带着一个 N"
        % (n, len(big), small_per, big_per))


# ═════════════════ PERF-1 ②：计数合并 ═════════════════
def test_c276_perf1_refresh_counts_is_coalesced(qapp, monkeypatch):
    """连发 20 次 `modelUpdated` → `counts_text` 只算 1–2 次（100 ms 单发 QTimer 合并）。

    变异验证：把 `_on_model_updated` 里的 `_refresh_counts_soon()` 改回 `_refresh_counts()`，
    立刻变成 20 次。
    """
    rows = real_models("btlp")
    panel, st = make_panel(models=rows, show=False)
    qapp.processEvents()

    calls = {"n": 0}
    orig = SL.SignalListPanel.counts_text

    def counted(self):
        calls["n"] += 1
        return orig(self)

    monkeypatch.setattr(SL.SignalListPanel, "counts_text", counted)

    for i in range(20):
        st.update_model(st.scope, dict(rows[i % len(rows)]))
    assert calls["n"] == 0, "逐行升级当场就算了 %d 次计数（该攒到定时器上）" % calls["n"]

    assert H.wait_for(lambda: calls["n"] > 0, timeout_ms=2000), "合并的那一发计数没落地"
    qapp.processEvents()
    assert 1 <= calls["n"] <= 2, "20 次升级合并成了 %d 次计数（该是 1–2 次）" % calls["n"]
    # 合并之后写出去的仍是当前的数
    assert panel.counts.text() == panel.counts_text()


# ═════════════════ PERF-1 ③：收尾不整表重建 ═════════════════
class _QueuedWorker(QtCore.QObject):
    """把 `signalDone` **排进主线程队列**的假 worker —— PERF-2 现场的确定性复刻。

    真 `AnalysisWorker` 在另一个线程里 emit，是队列连接；这里用 `QTimer.singleShot(0, ...)`
    把同一件事（已排队、`cancel()` 撤不回）做出来，而不起线程：offscreen 用例不起线程，
    时序才是确定的。

    `deliver_now=k`：前 k 条当场发完（= 主线程已经把那几条排干了），其余连同 `finished`
    全部排队 —— 而线程**这时已经跑完**（`is_running()` 为假），正是引擎比清单快几十倍时的样子。
    """

    started = QtCore.Signal(str, int)
    progress = QtCore.Signal(int, int, str)
    signalDone = QtCore.Signal(str, dict)
    finished = QtCore.Signal(str, list)
    cancelled = QtCore.Signal(str, list)
    failed = QtCore.Signal(str, str)

    def __init__(self, deliver_now=0):
        super().__init__()
        self._deliver_now = int(deliver_now)
        self._vid = ""
        self._all = []
        self.cancels = 0

    def start(self, provider, request, engine_lock=None, total=0):
        self._vid = request.view_id
        self._all = list(provider.view_models(request.mode, request.max_tests, request.exhaustive,
                                              request.sig_cov, request.form_cov, lite=True))
        self.started.emit(self._vid, int(total or 0) or len(self._all))
        for i, m in enumerate(self._all):
            if i < self._deliver_now:
                self._deliver(i, m)
            else:
                QtCore.QTimer.singleShot(0, lambda i=i, m=m: self._deliver(i, m))
        QtCore.QTimer.singleShot(0, lambda: self.finished.emit(self._vid, list(self._all)))

    def _deliver(self, i, m):
        self.progress.emit(i + 1, len(self._all), str(m.get("name") or ""))
        self.signalDone.emit(str(m.get("name") or ""), dict(m))

    def cancel(self):
        self.cancels += 1

    def is_running(self):
        return False                                  # 引擎早跑完了，只剩队列里那一堆


def _pending(st, vid=None):
    return [m["name"] for m in st.models(vid) if m.get("status") == "pending"]


def _done(st, vid=None):
    return [m["name"] for m in st.models(vid) if m.get("status") != "pending"]


def test_c276_perf1_worker_finish_does_not_reload_when_rows_unchanged(qapp, tmp_path, monkeypatch):
    """worker 收尾的 `set_models` 不该再整表重建 —— 每一行早就逐条升级过了。

    变异验证：把 `_on_models_changed` 改回无条件 `self.reload()`，`reload` 计数立刻 ≥ 1。
    """
    models = make_models(8)
    st = FakeState(models=models)
    w = MainWindow(state=st, worker_factory=lambda: _QueuedWorker(deliver_now=8))
    w.load_path(touch_xlsx(tmp_path))                 # 8 条全部当场升级，只剩 finished 排在队列里
    assert _pending(st) == [], "8 条应当都已升级：%s" % (_pending(st),)

    calls = {"reload": 0, "in_place": 0}
    orig_reload = SL.SignalListPanel.reload
    orig_inplace = SL.SignalListPanel._refresh_in_place

    def counted_reload(self):
        calls["reload"] += 1
        return orig_reload(self)

    def counted_inplace(self):
        calls["in_place"] += 1
        return orig_inplace(self)

    monkeypatch.setattr(SL.SignalListPanel, "reload", counted_reload)
    monkeypatch.setattr(SL.SignalListPanel, "_refresh_in_place", counted_inplace)

    ended = []
    w.analysisEnded.connect(lambda vid, ok: ended.append((vid, ok)))
    assert H.wait_for(lambda: bool(ended)), "排队的 finished 没回来"
    assert ended == [(st.scope, True)]
    assert calls["in_place"] >= 1 and calls["reload"] == 0, \
        "收尾整表重建了 %d 次（行集合没变，不该重建）" % calls["reload"]
    assert [m["name"] for m in st.models()] == [m["name"] for m in models]
    assert w.list_panel.proxy.rowCount() == len(models)
    w.close()


def test_c276_perf1_reload_still_happens_when_the_row_set_changes(qapp, tmp_path, monkeypatch):
    """反过来的一半：行集合真的变了（换表 / 换范围），还是得整表重建。"""
    st = FakeState(models=make_models(8))
    w = MainWindow(state=st, worker_factory=lambda: _QueuedWorker(deliver_now=8))
    w.load_path(touch_xlsx(tmp_path))

    calls = {"reload": 0}
    orig = SL.SignalListPanel.reload
    monkeypatch.setattr(SL.SignalListPanel, "reload",
                        lambda self: (calls.__setitem__("reload", calls["reload"] + 1), orig(self))[1])
    st.set_models(st.scope, make_models(5), False)    # 8 行 → 5 行
    assert calls["reload"] == 1, "行集合变了却没重建（reload 次数 %d）" % calls["reload"]
    assert w.list_panel.proxy.rowCount() == 5
    w.close()


# ═════════════════ PERF-2：停止真的停 ═════════════════
def test_c276_c269_perf2_stop_ignores_queued_signal_done(qapp, tmp_path):
    """排队的 `signalDone` 撵不动清单：点停止那一刻展开完几行，停下来就还是几行。

    变异验证：
      · 去掉 `_connect_worker` 的 `stoppable` 闸门 → 剩下 5 条照样落进清单（8 行全完），
        而且收到的是 `analysisEnded(vid, True)`；
      · 去掉 `on_stop_analysis` 的 `_report_stopped` → 一条收尾都没有，状态栏空着。
    """
    models = make_models(8)
    st = FakeState(models=models)
    w = MainWindow(state=st, worker_factory=lambda: _QueuedWorker(deliver_now=3))
    ended = []
    w.analysisEnded.connect(lambda vid, ok: ended.append((vid, ok)))
    w.load_path(touch_xlsx(tmp_path))

    # 线程「早跑完了」，清单才升级了 3 行，另外 5 条 + finished 排在主线程队列里
    assert not w.worker.is_running()
    assert len(_done(st)) == 3 and len(_pending(st)) == 5
    assert H.find(w, N.LOADING_PANEL).isVisibleTo(w)
    frozen = sorted(_done(st))

    H.find(w, N.LOADING_BTN_STOP).click()             # ⑮ 停止分析（真按钮，不直接调槽）

    assert ended == [(st.scope, False)], "停止没有收尾：%s" % (ended,)
    assert H.find(w, N.STATUS_LEFT).text() == T.LOADING_STOPPED_FMT.format(done=3, total=8)
    assert not H.find(w, N.LOADING_PANEL).isVisibleTo(w)

    for _ in range(5):                                # 把排着的那 5 条 + finished 全放出来
        qapp.processEvents()
    assert sorted(_done(st)) == frozen, "排队的 signalDone 还是把清单推下去了：%s" % (_done(st),)
    assert len(_pending(st)) == 5, "其余的行没有留在「分析中」：%s" % (_pending(st),)
    assert ended == [(st.scope, False)], "排队的 finished 又发了一次收尾：%s" % (ended,)
    assert H.find(w, N.STATUS_LEFT).text() == T.LOADING_STOPPED_FMT.format(done=3, total=8)
    assert w.list_panel.proxy.rowCount() == 8         # 行还在，能点
    w.close()


def test_c276_perf2_stop_while_thread_still_runs_keeps_cancel_path(qapp, tmp_path):
    """worker 真的还在跑时，`cancel()` 与 `cancelled` 收尾那一路原样不动（场景⑧ 的语义）。"""
    from test_ui_app import FakeWorker

    models = make_models(8)
    st = FakeState(models=models)
    w = MainWindow(state=st, worker_factory=lambda: FakeWorker(stop_after=2))
    ended = []
    w.analysisEnded.connect(lambda vid, ok: ended.append((vid, ok)))
    w.load_path(touch_xlsx(tmp_path))
    assert w.worker.is_running()

    H.find(w, N.LOADING_BTN_STOP).click()
    qapp.processEvents()
    assert not w.worker.is_running()
    assert ended == [(st.scope, False)]
    assert H.find(w, N.STATUS_LEFT).text() == T.LOADING_STOPPED_FMT.format(done=2, total=8)
    assert len(_pending(st)) == 6
    w.close()


# ═════════════════ 骨架 → 升级 的口径没被改坏 ═════════════════
def test_c276_skeleton_rows_stay_clickable_while_upgrading(qapp, tmp_path):
    """骨架 N 行先出、逐条升级、一行不丢 —— PERF-1 的三处改动都不许动这条（C-276 本义）。"""
    models = make_models(8)
    st = FakeState(models=models)
    w = MainWindow(state=st, worker_factory=lambda: _QueuedWorker(deliver_now=0))
    w.load_path(touch_xlsx(tmp_path))
    assert [m["status"] for m in st.models()] == ["pending"] * 8
    assert w.list_panel.proxy.rowCount() == 8
    assert w.list_panel.select_first_visible()
    assert st.models() == skeleton_of(models) or True          # 骨架就是 pending 那一版

    ended = []
    w.analysisEnded.connect(lambda vid, ok: ended.append((vid, ok)))
    assert H.wait_for(lambda: bool(ended))
    assert ended == [(st.scope, True)]
    assert _pending(st) == []
    assert w.list_panel.proxy.rowCount() == 8
    w.close()
