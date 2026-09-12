# -*- coding: utf-8 -*-
"""worker.py —— 后台分析 worker（架构 §2.2 / §1.3③；C-276、不变量 I-12 / I-21）。

要解决的是一件很具体的事：红区那张表 200+ 个信号，整表分析要十几秒。旧门面在这十几秒里
整个界面是死的，用户以为工具卡了就去点关闭。v2 的做法（§2.2 唯一写法）：

  1. 主线程先 `provider.skeleton_models()`（只 resolve_root，毫秒级）→ 清单**立刻**出来、立刻可点；
  2. 本 worker 在后台线程里 `provider.view_models(..., lite=True)`，引擎每分析完一个信号回调一次，
     worker 把它变成 `progress` + `signalDone` 两条 Qt 信号发出去 → 清单那一行从「分析中」升级；
  3. 用户随时可以停：`cancel()` 在**下一个信号之前**生效，已经分析完的行**保留**（绝不回退成 pending）。

两条与「正确性」直接相关的规矩：
  · I-21 引擎互斥：worker 与主线程共用同一个 `wb`（`Resolver.__init__` / `_supplemented()` 都往 wb 上
    回写标记），必须互斥。锁的**粒度**由本模块负责降到逐信号——见 `_detach_engine_lock` 的长注释。
  · I-12 文案：`failed` 的消息先过 `terms.scrub`，且只给一句人话（绝不把 traceback 发上界面，C-272）。

依赖方向（§1.2）：PySide6.QtCore + `contracts` + `terms`，不 import 视图、不 import 引擎。
"""

import copy
import threading
import time

from PySide6 import QtCore

from . import contracts
from . import terms

__all__ = ["AnalysisWorker"]

#: 换范围时上一趟还在跑：等它在信号边界停下来的上限（ms）。等不到就让它自生自灭
#: （结果由 serial 号挡掉，不会串进新的一趟）。
CANCEL_WAIT_MS = 3000

#: 信号边界让锁时，等对方把锁拿走的上限（秒）。等不到就自己取回继续跑——
#: 宁可这一个信号不让，也不能让后台分析停在这儿。
YIELD_MAX_S = 0.05


def _detach_engine_lock(provider, lock):
    """把引擎锁的粒度从「整趟 `view_models`」降到「逐信号」。返回 (给线程用的 provider, 是否已接管锁)。

    `providers.py` 是在**每次引擎调用**外面包一把锁的（它自己的注释写明：逐信号粒度要调用方配合，
    provider 从外面包不进去）。照原样用的话整趟 view_models 都握着锁，主线程点一个信号要等整张表
    跑完——载入态上那句「清单已可点，展开中…」就成了空话。

    做法：给 provider 换一个 `engine_lock` 置空的 cfg 代理（providers 读不到锁 → 用 nullcontext），
    锁改由本模块自己持有，并在**每个信号的回调里**完整让出一次（`_yield`）。这样：
      信号内 = 锁在 worker 手上（与主线程互斥，I-21）；信号之间 = 锁真的空出来，主线程插得进去。

    ⚠ 让锁时 `wb.logic` 可能正被 `_supplemented()` 换成「应用了 RTL 补充」的版本。主线程此刻
    进来做 analyze 时也会走自己的 `_supplemented()`（同一份 logic_overrides）→ 换成同值、退出时
    还原成同值，两边看到的逻辑一致。配置真变了会 `configChanged` → 整趟重跑，不存在半新半旧。

    provider 认不出 cfg（视图测试里的假 provider 没有 `.cfg`）→ 原样返回、不接管锁，
    退回「整趟一把锁」：慢一点，但仍然正确。"""
    cfg = getattr(provider, "cfg", None)
    if lock is None or cfg is None or getattr(cfg, "engine_lock", None) is None:
        return provider, False
    try:
        prov = copy.copy(provider)
        prov.cfg = _UnlockedCfg(cfg)
    except Exception:      # noqa: BLE001  复制不了（奇怪的 provider）→ 退回整趟一把锁
        return provider, False
    return prov, True


class _UnlockedCfg(object):
    """cfg 的只读代理：除 `engine_lock` 外一律转发给真 cfg。

    `engine_lock = None` 让 `providers._BaseProvider._engine()` 拿到 nullcontext ——
    锁并没有消失，只是换成由 worker 在信号边界上收放（见 `_detach_engine_lock`）。"""

    engine_lock = None

    def __init__(self, cfg):
        object.__setattr__(self, "_cfg", cfg)

    def __getattr__(self, name):
        return getattr(object.__getattribute__(self, "_cfg"), name)


class AnalysisWorker(QtCore.QObject):
    """一次「整表逐信号分析」的后台作业（实现 `contracts.AnalysisWorkerProto`）。

    信号严格按 `contracts.WORKER_SIGNALS`：

        started(view_id, total)            —— 开跑（total = 主线程骨架清单的行数，0 = 还不知道）
        progress(done, total, name)        —— 每个信号分析完一次；载入态进度条 + 当前信号名
        signalDone(name, lite_model)       —— 同上，附该行的 lite 模型；清单据此把那行换掉
        finished(view_id, models)          —— 全部跑完
        cancelled(view_id, models_partial) —— 用户停止；**已完成的都在里面**
        failed(view_id, message)           —— 整趟炸了（已捕获、已 scrub；C-043）

    §2.2 偏差一处并说明：本类是 `QObject` + 内部 `QThread`，不是 `QThread` 子类——
    `QThread` 自带 `started` / `finished` 两个同名信号，直接继承会把它们盖掉（Qt 那两个还照发，
    于是同一个名字上挂着两套语义的信号，是最难查的一类 Qt bug）。跑活的仍是真 `QThread`。"""

    started = QtCore.Signal(str, int)
    progress = QtCore.Signal(int, int, str)
    signalDone = QtCore.Signal(str, dict)
    finished = QtCore.Signal(str, list)
    cancelled = QtCore.Signal(str, list)
    failed = QtCore.Signal(str, str)

    def __init__(self, parent=None):
        QtCore.QObject.__init__(self, parent)
        self._thread = None
        self._cancel = threading.Event()
        self._serial = 0            # 每趟一个号：上一趟迟到的信号按号丢掉，不串进新的一趟

    # ── 对外三个方法（contracts.AnalysisWorkerProto）──
    def start(self, provider, request, engine_lock=None, total=0):
        """开一趟。`request` 是 `contracts.AnalysisRequest`（含指纹，C-265 由 state 判要不要跑）。

        §2.2 规矩 4「同一时刻只跑一个 job」：已经在跑 → 先 `cancel()` 并等它在信号边界停下
        （最多 `CANCEL_WAIT_MS`），等不到也照常开新的一趟——旧趟的结果由 serial 号挡掉。

        ⚠ **先翻号再叫停**：反过来的话，旧线程是在 `wait()` 期间收尾的，那一刻号还没翻，
        它的 `cancelled` 照样过关、排进队列，等新的一趟跑完才被主线程取出来——
        于是新清单被一份空的「取消结果」盖掉。"""
        self._serial += 1
        if self.is_running():
            self._cancel.set()
            self._thread.wait(CANCEL_WAIT_MS)
        self._cancel = threading.Event()
        self._thread = _AnalysisThread(self, provider, request, engine_lock,
                                       self._cancel, self._serial)
        self.started.emit(request.view_id, int(total or 0))
        self._thread.start()

    def cancel(self):
        """请求停止。**下一个信号之前**生效；已完成的结果随 `cancelled` 一起交回来。"""
        self._cancel.set()

    def is_running(self):
        return bool(self._thread is not None and self._thread.isRunning())

    def wait(self, msec=CANCEL_WAIT_MS):
        """等这趟跑完（测试与关窗用；界面主流程不该调）。"""
        return True if self._thread is None else self._thread.wait(msec)

    # ── 线程回调（在 worker 线程里被调；emit 走队列送回主线程）──
    def _emit(self, serial, name, *args):
        if serial == self._serial:
            getattr(self, name).emit(*args)


class _AnalysisThread(QtCore.QThread):
    """真正跑引擎的线程。只做「调 provider + 把回调翻译成 Qt 信号 + 收放引擎锁」。"""

    def __init__(self, owner, provider, request, engine_lock, cancel_ev, serial):
        QtCore.QThread.__init__(self)
        self._owner = owner
        self._request = request
        self._lock = engine_lock
        self._cancel = cancel_ev
        self._serial = serial
        self._provider, self._holds_lock = _detach_engine_lock(provider, engine_lock)

    def _yield_engine(self):
        """信号边界：把引擎锁完整让出一次，等着的主线程就能插进来（I-21 的「逐信号粒度」）。

        `state.EngineLock` 带等待计数：**没人等就什么都不做**（整表跑下来零开销），
        有人等就让出并自旋到对方真的拿走——交接是确定的，不靠 `sleep(0)` 赌调度谁先醒。
        拿到的是裸 RLock（别的调用方自带的锁）→ 退回「让一下再取回」，仍然正确。"""
        if not self._holds_lock:
            return
        waiting = getattr(self._lock, "waiting", None)
        if waiting is None:                       # 裸锁：不知道有没有人等，让一下就是了
            self._lock.release()
            time.sleep(0)
            self._lock.acquire()
            return
        if not waiting:
            return
        self._lock.release()
        t0 = time.time()
        while getattr(self._lock, "waiting", 0) and time.time() - t0 < YIELD_MAX_S:
            time.sleep(0.0002)                    # 等对方真的把锁拿走（通常一两次就够）
        self._lock.acquire()                      # 对方用完自然轮到我们

    def run(self):
        req = self._request
        vid = req.view_id
        done_models = []

        def _cb(done, total, name, lite=None):
            """引擎每分析完一个信号回调一次（`topout.analyze_all` 的 progress，第 4 参是 lite 模型）。"""
            self._owner._emit(self._serial, "progress", int(done), int(total), str(name))
            if isinstance(lite, dict):
                done_models.append(lite)
                self._owner._emit(self._serial, "signalDone", str(name), lite)
            self._yield_engine()

        if self._holds_lock:
            self._lock.acquire()
        try:
            models = self._provider.view_models(
                req.mode, req.max_tests, req.exhaustive,
                sig_cov=req.sig_cov, form_cov=req.form_cov,
                progress=_cb, should_cancel=self._cancel.is_set, lite=True)
        except Exception as exc:                      # noqa: BLE001  整趟炸了也不许让窗口跟着走
            # I-12 / C-272：只给一句人话（类名兜底，绝不把 traceback 发上界面）
            msg = terms.scrub(str(exc).strip() or exc.__class__.__name__)
            self._owner._emit(self._serial, "failed", vid, msg)
            return
        finally:
            if self._holds_lock:
                self._lock.release()
        # 引擎在信号边界 break → 返回的是**部分**列表；一个都没回调过时用 done_models 兜底
        models = list(models or []) or list(done_models)
        if self._cancel.is_set():
            self._owner._emit(self._serial, "cancelled", vid, models)
        else:
            self._owner._emit(self._serial, "finished", vid, models)


#: 供 app / 测试构造请求时对齐字段名，别在视图里散写字面量
AnalysisRequest = contracts.AnalysisRequest
