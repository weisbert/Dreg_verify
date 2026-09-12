# -*- coding: utf-8 -*-
"""test_ui_worker.py —— 后台分析 worker（`dreg_verify/ui/worker.py`，C1-a）。

契约 ID：WORKER = C-276（载入 200+ 信号时给进行中态、分析放后台不卡界面）。
不变量：I-12（`failed` 文案先 scrub）、I-21（worker 与主线程共用 wb 的引擎互斥，**逐信号粒度**）。

不起窗（`H.app()` 只为拿进程级 QApplication 与事件循环——跨线程 Qt 信号是**队列连接**，
不 `processEvents` 就一条都收不到；这也正是真界面里的传递方式）。

夹具一律用仓库的 mirror 生成脚本（公开仓，不得出现真实信号名）。
"""

import copy
import time

import pytest
from PySide6 import QtCore                             # noqa: E402

import ui_fakes as F                                   # noqa: E402
import ui_harness as H                                 # noqa: E402
from dreg_verify import providers as PV                # noqa: E402
from dreg_verify.ui import contracts                   # noqa: E402
from dreg_verify.ui import persist as P                # noqa: E402
from dreg_verify.ui import state as ST                 # noqa: E402
from dreg_verify.ui import terms                       # noqa: E402
from dreg_verify.ui import worker as WK                # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return H.app()


@pytest.fixture
def iso(monkeypatch, tmp_path):
    """两份持久化文件指到临时目录（与 `ui_harness.isolate_settings` 对 gui 做的同一件事）。"""
    monkeypatch.setattr(P, "SETTINGS_PATH", str(tmp_path / "gui_settings.json"))
    monkeypatch.setattr(P, "EDITS_PATH", str(tmp_path / "edits.json"))
    return tmp_path


class Rec(object):
    """把 worker 的六条信号按**发生顺序**记下来——顺序本身就是契约（先 progress 后 signalDone）。"""

    def __init__(self, w):
        self.log = []
        w.started.connect(lambda v, n: self.log.append(("started", v, n)))
        w.progress.connect(lambda d, t, n: self.log.append(("progress", d, t, n)))
        w.signalDone.connect(lambda n, m: self.log.append(("signalDone", n, m)))
        w.finished.connect(lambda v, m: self.log.append(("finished", v, list(m))))
        w.cancelled.connect(lambda v, m: self.log.append(("cancelled", v, list(m))))
        w.failed.connect(lambda v, s: self.log.append(("failed", v, s)))

    def kinds(self):
        return [e[0] for e in self.log]

    def last(self, kind):
        return next((e for e in reversed(self.log) if e[0] == kind), None)


def _wait(app, cond, timeout=60.0):
    """跑事件循环直到条件成立（跨线程信号是队列连接，不 processEvents 收不到）。"""
    t0 = time.time()
    while time.time() - t0 < timeout:
        app.processEvents()
        if cond():
            return True
        time.sleep(0.002)
    app.processEvents()
    return cond()


# ═══════════════ ① 信号次序与内容 ═══════════════
@pytest.mark.contract("C-276")
def test_c276_worker_emits_progress_signaldone_finished_in_order(qapp):
    """C-276：每个信号 **先 progress 后 signalDone**，跑完一条 finished，模型是 lite 形状。

    次序是契约不是巧合：载入态先把进度条推到 n/N，再让清单那一行从「分析中」换成结果——
    反过来的话用户会看到「行已经好了、进度条还停在上一格」。"""
    prov = F.FakeProvider(delay=0.0)
    w = WK.AnalysisWorker()
    rec = Rec(w)
    req = contracts.AnalysisRequest(view_id="topout", mode="max", max_tests=256)
    w.start(prov, req, None, total=len(prov.names))
    assert _wait(qapp, lambda: rec.last("finished") is not None)

    assert rec.log[0] == ("started", "topout", 4)
    body = rec.kinds()[1:-1]
    assert body == ["progress", "signalDone"] * 4, body            # 严格交替
    d, t, nm = rec.log[1][1:]
    assert (d, t, nm) == (1, 4, F.DEFAULT_NAMES[0])
    lite = rec.log[2][2]
    assert set(lite) == set(contracts.LITE_MODEL_KEYS)             # 清单只认这一套键
    vid, models = rec.last("finished")[1:]
    assert vid == "topout" and [m["name"] for m in models] == list(F.DEFAULT_NAMES)
    assert prov.calls[-1][4] is True                               # lite=True（跳过联表那趟）
    assert not w.is_running()


@pytest.mark.contract("C-276")
def test_c276_started_total_comes_from_skeleton(qapp):
    """C-276：`started(view_id, total)` 的 total 来自主线程那趟骨架清单（载入态一开始就能写 n/N）。"""
    prov = F.FakeProvider(names=("d_fake_a", "d_fake_b"))
    w = WK.AnalysisWorker()
    rec = Rec(w)
    w.start(prov, contracts.AnalysisRequest(view_id="mux"), None,
            total=len(prov.skeleton_models()))
    assert rec.log[0] == ("started", "mux", 2)
    assert _wait(qapp, lambda: rec.last("finished") is not None)


# ═══════════════ ② 取消 ═══════════════
@pytest.mark.contract("C-276")
def test_c276_worker_cancel_keeps_done_rows(qapp):
    """C-276：停止分析 → 已经分析完的行**留着**，其余仍是骨架的 pending（绝不整表回退）。

    「停止」在红区是常用动作（先看前几个信号对不对再决定要不要跑完），
    停一次就把已经出来的结果丢掉的话，用户只能从头再跑一遍。"""
    prov = F.FakeProvider(names=["d_fake_%02d" % i for i in range(8)], delay=0.02)
    w = WK.AnalysisWorker()
    rec = Rec(w)
    w.start(prov, contracts.AnalysisRequest(view_id="topout"), None, total=8)
    assert _wait(qapp, lambda: len([e for e in rec.log if e[0] == "signalDone"]) >= 2)
    w.cancel()
    assert _wait(qapp, lambda: rec.last("cancelled") is not None)
    assert rec.last("finished") is None                       # 取消不发 finished
    part = rec.last("cancelled")[2]
    n_done = len([e for e in rec.log if e[0] == "signalDone"])
    assert 2 <= len(part) == n_done < 8                        # 回来的就是已完成的那几个

    # 状态层按 partial 合并：回填过的换成结果，其余保留骨架的 pending
    st = ST.WorkbenchState()
    st.set_models("topout", prov.skeleton_models(), partial=True)
    st.set_models("topout", part, partial=True)
    rows = st.models("topout")
    assert len(rows) == 8
    assert [r["status"] for r in rows[:len(part)]] == ["ok"] * len(part)
    assert all(r["status"] == "pending" for r in rows[len(part):])


@pytest.mark.contract("C-276")
def test_c276_restart_drops_stale_job_results(qapp):
    """C-276：换范围 = 先 cancel 再 start。上一趟迟到的结果按 serial 号丢掉，不许串进新的一趟。"""
    slow = F.FakeProvider(names=["d_fake_slow_%d" % i for i in range(6)], delay=0.02)
    fast = F.FakeProvider(names=["d_fake_fast_0", "d_fake_fast_1"])
    w = WK.AnalysisWorker()
    rec = Rec(w)
    w.start(slow, contracts.AnalysisRequest(view_id="topout"), None, total=6)
    w.start(fast, contracts.AnalysisRequest(view_id="mux"), None, total=2)
    assert _wait(qapp, lambda: rec.last("finished") is not None)
    qapp.processEvents()
    fins = [e for e in rec.log if e[0] in ("finished", "cancelled")]
    assert len(fins) == 1 and fins[0][1] == "mux"
    assert all("slow" not in e[1] for e in rec.log if e[0] == "signalDone")


# ═══════════════ ③ 失败通道（I-12）═══════════════
@pytest.mark.contract("C-276", "C-043")
def test_c276_failed_message_is_scrubbed_and_has_no_traceback(qapp):
    """I-12 / C-043：整趟炸了 → `failed` 一句人话（已过 terms.scrub），窗口不许跟着走。

    绝不把 traceback 送上界面（C-272），也绝不让 `cone` / `CUVUNF` 这类内部说法裸出现。"""
    prov = F.FakeProvider(fail="cone 展开失败：必 CUVUNF")
    w = WK.AnalysisWorker()
    rec = Rec(w)
    w.start(prov, contracts.AnalysisRequest(view_id="topout"), None, total=4)
    assert _wait(qapp, lambda: rec.last("failed") is not None)
    msg = rec.last("failed")[2]
    assert msg == terms.scrub("cone 展开失败：必 CUVUNF")
    assert "cone" not in msg.lower() and "CUVUNF" not in msg
    assert "Traceback" not in msg and "File \"" not in msg
    assert rec.last("finished") is None and rec.last("cancelled") is None


# ═══════════════ ④ 引擎互斥 · 逐信号粒度（I-21）═══════════════
#: 后台每个信号**在锁里**多花这么久 —— 让那一趟长到主线程真能插进中间。
#: wl 镜像 topout 9 个信号 ≈ 0.27 s，主线程跑完 21 个信号一轮约 0.04 s，留了 6 倍余量。
SLOW_PER_SIGNAL_S = 0.03


class _SlowTopout(PV.TopoutProvider):
    """真 provider，只是每个信号在锁里多睡一会儿（见 `SLOW_PER_SIGNAL_S`）。"""

    def view_models(self, *a, **kw):
        inner = kw.get("progress")

        def slow(done, total, name, lite=None):
            time.sleep(SLOW_PER_SIGNAL_S)
            if inner is not None:
                inner(done, total, name, lite)

        kw["progress"] = slow
        return PV.TopoutProvider.view_models(self, *a, **kw)


@pytest.mark.contract("C-276")
def test_c276_worker_and_detail_interleave(qapp, iso):
    """I-21 压力测试（架构 §8-4）：worker 跑 wl 整表时，主线程连点全部信号看详情。

    要证三件事：① 不崩；② 每个信号的分析结果与**独占**跑出来的逐键相同（锁真的互斥了，
    没让两条线程各看到半个 wb 状态）；③ 至少有一次 analyze 整个发生在 worker 的**循环中间**
    （前后各读一次已完成信号数，`0 < n < 总数`）——这条是「逐信号粒度」的证据：
    锁若像 providers 默认那样包住整趟 view_models，主线程必然等到整张表跑完才拿到锁，
    这个计数只会是 0，载入态那句「清单已可点」就是假的。"""
    st = ST.WorkbenchState()
    assert st.load(H.mirror_path("wl"))
    topout_names = [m["name"] for m in st.provider("topout").skeleton_models()]
    probe = [(vid, m["name"]) for vid in ("logic", "mux")
             for m in st.provider(vid).skeleton_models()]
    assert len(probe) == 21, len(probe)          # wl 镜像 logic 6 + mux 15（§8-4 的「21 个信号」）

    def snap(an):
        if an is None:
            return None
        return (an["name"], an["kind"], an["editable"], len(an["vectors"]),
                len(an["groups"]), an["out_net"])

    ref = {k: snap(st.analyze(n, vid)) for k, (vid, n) in
           ((("%s/%s" % (vid, n)), (vid, n)) for vid, n in probe)}     # ① 独占基准
    assert any(v is not None for v in ref.values())
    st._an_cache.clear()                                              # 缓存清掉，下面是真算

    w = WK.AnalysisWorker()
    rec = Rec(w)
    # DirectConnection：回调就在 worker 线程里跑 → 主线程能**实时**读到「后台跑到第几个了」，
    # 不必等 processEvents（队列连接要等主线程空下来，正好把要测的那段时间抹平）
    live = []
    w.progress.connect(lambda d, t, n: live.append(d), QtCore.Qt.DirectConnection)
    total = len(topout_names)
    w.start(_SlowTopout(st), st.analysis_request("topout"), st.engine_lock, total=total)

    got, n_during = {}, 0
    for _round in range(10):                                          # 跑到后台结束为止
        for vid, n in probe:
            before = len(live)
            got["%s/%s" % (vid, n)] = snap(st.analyze(n, vid))
            if 0 < before and len(live) < total:      # 整个发生在后台循环的中间
                n_during += 1
            qapp.processEvents()
        st._an_cache.clear()                                          # 每轮都真算一遍
        if not w.is_running():
            break
    assert _wait(qapp, lambda: rec.last("finished") is not None)

    assert got == ref                                                 # ② 结果一致
    assert n_during >= 1                                              # ③ 真的插进去了
    done = rec.last("finished")[2]
    assert [m["name"] for m in done] == topout_names
    assert all(m["status"] != "pending" for m in done)


@pytest.mark.contract("C-276")
def test_c276_lock_detach_falls_back_when_provider_has_no_cfg(qapp):
    """没有 `.cfg` 的假 provider → 不接管锁（退回整趟一把锁），仍然正确、绝不死锁。"""
    import threading
    lock = threading.RLock()
    prov, holds = WK._detach_engine_lock(F.FakeProvider(), lock)
    assert holds is False and prov is not None

    real = PV.TopoutProvider(F.FakeState())
    prov2, holds2 = WK._detach_engine_lock(real, lock)
    assert holds2 is True
    assert prov2 is not real and prov2.cfg.engine_lock is None
    assert prov2.cfg.wb is real.cfg.wb                          # 其余字段照样转发
    assert isinstance(copy.copy(prov2), PV.TopoutProvider)
