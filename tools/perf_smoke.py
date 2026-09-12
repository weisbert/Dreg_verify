# -*- coding: utf-8 -*-
"""perf_smoke.py —— GUI v2 工作台的**性能冒烟**：200 信号量级下，界面到底冻不冻。

量的是执行计划 §2.2 / C-276 那两句承诺的**数**（不是断言，不进 CI，跑完给人看）：

  T1  `load_path()` 返回时，清单已有 N 行且**可点**（`select_first_visible()` 选得中）
      —— 主线程只做 `skeleton_models()`，这一步慢 = 「点了加载半天没反应」。
  T2  骨架出来 → `analysisEnded`（后台 worker 把 N 条逐条升级完）
      —— 这一段界面必须是活的，所以同时量三样：
        · **最大一次事件循环停顿** = 100 ms 心跳 QTimer 两次回调间隔 − 100 ms 的最大值
          （一次都没轮上就按整段算，见 `_Heartbeat.peek`）。>500 ms ≈ 肉眼可见的卡。
        · **界面响应延迟** = 从别的线程把一次「用户动作」投进主线程队列，到它真被处理的间隔。
          队列连接和真鼠标排的是同一条队，前面堵着多少活它就等多久 —— 这是最贴近
          「点了没反应几秒」的那个数。
        · **点信号**：轮到它之后 `state.set_current` → 「真值表/右栏出内容」的等待。
          这一段走 `state.analyze`，要和 worker 抢引擎锁（I-21，worker 在信号边界让锁，
          `ui/worker._yield_engine`）——这个数大才说明让锁没生效。
  对照  同一张表跑一次**全量** CLI 产出（`generator.build` / `render_sv` / `view_models(lite)`）——
      GUI 那一路比它慢多少，就是 Qt 信号 + 清单刷新那层的开销。

每项跑 `--repeat` 次**取最小值**（机器上通常还有别的活在跑，最小值最接近真实下限）。

用法（worktree 里没 .venv，用主仓的）：
    C:/code/Dreg_verify/.venv/Scripts/python.exe tools/perf_smoke.py --n 200
    ... --n 50 --repeat 3            # 换档
    ... --n 200 --stop-test          # 另跑一次「分析中途点停止」
    ... --trace-alloc                # 额外一趟量 Python 对象峰值（不参与计时表）
    ... --path some.xlsx             # 用现成的表（默认按 --n 现造一张到临时目录）

⚠ 起窗前脚本自己设 `DREG_VERIFY_NO_PERSIST=1`（I-08 第二把锁）+ `QT_QPA_PLATFORM=offscreen`：
   pytest 外起窗不设它的话，镜像表路径会进用户真机的 `~/.dreg_verify_gui.json`（P-22）。
⚠ 计时那几趟**绝不能**开 tracemalloc：实测它把 200 信号的 T2 从 1.6s 抬到 4.1s，
   量出来的全是 tracemalloc 自己。要 Python 对象峰值就 `--trace-alloc`（单独一趟）。
"""

import argparse
import os
import random
import sys
import tempfile
import threading
import time
import tracemalloc

# ── 起窗前两把锁必须在 import Qt / dreg_verify.ui 之前设好 ──
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["DREG_VERIFY_NO_PERSIST"] = "1"

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

#: 心跳周期（ms）。停顿 = 实测间隔 − 这个数。
HEARTBEAT_MS = 100

#: 「点一个信号」等内容出现的上限（秒）——超了就记成超时，不是 0。
CLICK_TIMEOUT_S = 20.0

#: 整趟分析的上限（秒）。
ANALYSIS_TIMEOUT_S = 300.0


# ═════════════════════════ 小工具 ═════════════════════════
def _peak_rss_mb():
    """本进程**峰值**工作集（MB）。Windows 走 GetProcessMemoryInfo（含 Qt 的 C++ 侧，
    tracemalloc 只看得见 Python 那一半）；拿不到就返回 None，报告里如实留空。"""
    try:
        import ctypes
        from ctypes import wintypes

        class _PMC(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                        ("PeakWorkingSetSize", ctypes.c_size_t),
                        ("WorkingSetSize", ctypes.c_size_t),
                        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                        ("PagefileUsage", ctypes.c_size_t),
                        ("PeakPagefileUsage", ctypes.c_size_t)]

        pmc = _PMC()
        pmc.cb = ctypes.sizeof(_PMC)
        # ⚠ restype 必须显式给 HANDLE：默认 c_int 会把 64 位伪句柄截成 32 位，
        # 调用「成功」返回 0（失败）而不报错 —— 报告里就成了「量不到内存」。
        getproc = ctypes.windll.kernel32.GetCurrentProcess
        getproc.restype = wintypes.HANDLE
        gpmi = ctypes.windll.psapi.GetProcessMemoryInfo
        gpmi.argtypes = [wintypes.HANDLE, ctypes.POINTER(_PMC), wintypes.DWORD]
        if not gpmi(getproc(), ctypes.byref(pmc), pmc.cb):
            return None
        return pmc.PeakWorkingSetSize / (1024.0 * 1024.0)
    except Exception:                                  # noqa: BLE001  量不到内存不算失败
        return None


class _Heartbeat(object):
    """100 ms 打点，记录「两次回调间隔 − 100 ms」的最大值 = 最大一次事件循环停顿。

    为什么这么量：主线程被后台占住时，定时器到点也投递不出去，下一次回调就会迟到那么多。
    Windows 的 QTimer 本身有 ~15 ms 粒度抖动，所以几十毫秒的读数是噪声，看的是**量级**。"""

    def __init__(self, qtcore):
        self._qt = qtcore
        self.timer = qtcore.QTimer()
        self.timer.setInterval(HEARTBEAT_MS)
        self.timer.setTimerType(qtcore.Qt.PreciseTimer)
        self.timer.timeout.connect(self._tick)
        self.max_stall = 0.0
        self.ticks = 0
        self._last = None

    def start(self):
        self.reset()
        self.timer.start()

    def reset(self):
        """清零重新计（载表那一段与分析那一段要分开看：前者是主线程自己在干活）。"""
        self.max_stall, self.ticks, self._last = 0.0, 0, time.perf_counter()

    def peek(self):
        """当前最大停顿，**含还没结束的那一段**：一次都没轮上（ticks=0）时，停顿就是整个窗口——
        不把这一段算进去，「心跳 0 次」反而会读成「最大停顿 0.000s」，正好把最坏的情况报没了。"""
        tail = 0.0
        if self._last is not None:
            tail = (time.perf_counter() - self._last) - HEARTBEAT_MS / 1000.0
        return max(self.max_stall, tail, 0.0)

    def stop(self):
        self.max_stall = self.peek()
        self.timer.stop()

    def _tick(self):
        now = time.perf_counter()
        if self._last is not None:
            self.max_stall = max(self.max_stall, (now - self._last) - HEARTBEAT_MS / 1000.0)
        self._last = now
        self.ticks += 1


def make_poker(qtcore):
    """造一个「从普通 Python 线程往主线程投递调用」的小对象（跨线程 = 队列连接 = posted event）。

    为什么不用 QTimer 排点击 / 排停止：**实测**（见本工具的报告）200 信号那一趟里，
    主线程被 worker 的 posted event 洪流占满，100 ms 的 QTimer 在整趟 T2 里一次都轮不上
    —— 用 QTimer 排的点击根本发不出去，量出来是 0 次点击而不是「点了很慢」。
    posted event 走的是**正在被排干的那条队**，所以投得进去。

    ⚠ 也正因为如此，它量到的是「事件派发到之后 `set_current` 要等多久（引擎锁竞争，I-21）」，
    **不是**真人点鼠标的输入延迟 —— 真鼠标是窗口消息，和 QTimer 一起被饿着。"""

    class _Poker(qtcore.QObject):
        poke = qtcore.Signal(int)

    return _Poker()


def _spin(app, qtcore, pred, timeout_s):
    """转事件循环直到 pred() 为真或超时。返回 (是否成真, 用时秒)。

    `processEvents` 带 1 ms 上限：既让 Qt 把队列里的跨线程信号投递掉，又不至于把测量本身
    变成「每次至少睡 10 ms」（那会把点信号的等待时间量虚高一个量级）。"""
    t0 = time.perf_counter()
    while not pred():
        if time.perf_counter() - t0 > timeout_s:
            return False, time.perf_counter() - t0
        app.processEvents(qtcore.QEventLoop.AllEvents, 1)
    return True, time.perf_counter() - t0


def _truth_has_content(w):
    """真值表/右栏「出内容了」= 真值表装上了列，或右栏输入表有行。
    （RO 回读那类信号没有真值表，只会出一行文案 —— 调用点已经把它们排除在点击目标外。）"""
    try:
        if w.truth_panel.model.columnCount() > 0:
            return True
    except Exception:                                  # noqa: BLE001
        pass
    try:
        m = w.side_panel.inputs_view.model()
        return bool(m is not None and m.rowCount() > 0)
    except Exception:                                  # noqa: BLE001
        return False


# ═════════════════════════ 一趟测量 ═════════════════════════
def run_once(app, qtcore, path, n_expect, clicks=5, seed=0, stop_at=None, verbose=False,
             click_ms=20, cov_label=""):
    """起一个新窗 → 载表 → 量 T1/T2/停顿/点击 → 关窗。返回一份指标 dict。

    stop_at：不是 None 就做「分析中途点停止」——等到这么多行升级完就调 `on_stop_analysis()`，
    量到 `analysisEnded(_, False)` 的用时，并核对已展开完的行**没有**回退成 pending。
    cov_label：""=界面默认档（`session.DEFAULT_COV_LABEL`，即「全面」）；给「穷举」可以把引擎
    压慢到它真的成为瓶颈 —— 停止测试需要那种局面（默认档下引擎几十毫秒就跑完了，
    根本没有「中途」可停）。"""
    from dreg_verify.ui import app as A

    w = A.build_window([])
    w.resize(1600, 900)
    w.show()
    if cov_label:
        w.state.coverage("topout").global_label = cov_label
    loop = qtcore.QEventLoop()
    ended = []

    def _on_end(vid, ok):
        ended.append((vid, ok, time.perf_counter()))
        loop.quit()

    w.analysisEnded.connect(_on_end)
    app.processEvents()

    hb = _Heartbeat(qtcore)
    out = {"n": n_expect, "clicks": [], "click_when": [], "click_timeouts": 0,
           "dispatch": [], "dispatch_in": 0}

    # ── T1：load_path 返回 → 清单 N 行且可点 ──
    hb.start()                                         # 载表这一段也量一次（主线程自己在干活）
    t0 = time.perf_counter()
    ok = w.load_path(path)
    t1 = time.perf_counter()
    out["stall_during_load"] = hb.peek()
    rows = w.list_panel.proxy.rowCount()
    picked = w.list_panel.select_first_visible()
    out["t1"] = t1 - t0
    out["rows_after_load"] = rows
    out["load_ok"] = bool(ok)
    out["clickable"] = bool(picked)
    if not ok or rows != n_expect:
        hb.stop()
        w.close()
        app.processEvents()
        out["error"] = "载表失败或行数不符：ok=%s rows=%s expect=%s" % (ok, rows, n_expect)
        return out

    # 点击目标：骨架里就知道 kind，RO 回读那类没有真值表，排除掉
    skel = w.state.models()
    pool = [m["name"] for m in skel if m.get("kind") not in ("ro-readback", "unresolved")]
    rnd = random.Random(seed)
    targets = rnd.sample(pool, min(clicks, len(pool)))

    # ── T2：骨架出来 → analysisEnded ──
    # 用**真的** QEventLoop.exec()（不是 processEvents 轮询）：这才是用户面前那个循环。
    # 轮询会把「谁占着主线程」这件事量歪 —— 轮询自己就是在替事件循环转圈。
    done_seen = [0]
    upd_ts = []

    def _on_upd(*_a):
        done_seen[0] += 1
        upd_ts.append(time.perf_counter())

    w.state.modelUpdated.connect(_on_upd)
    t_skel = time.perf_counter()
    next_click = [0]
    stop_info = {"n": None, "t": None}

    def _do_click():
        """一次「点信号」：`set_current` 之后等真值表/右栏出内容（同线程直连，通常当场就有）。

        不拿 `ended` 卡住：分析已经跑完才轮到它，那本身就是要报的事实（`click_when` 记着
        点的时候已经升级了几行），拿掉就成了「一次都没点成」。"""
        if next_click[0] >= len(targets):
            return
        nm = targets[next_click[0]]
        next_click[0] += 1
        n_done = done_seen[0]
        tc = time.perf_counter()
        w.state.set_current(nm)
        got = _truth_has_content(w)
        if not got:                                    # 还没出 → 让事件循环转到出为止
            got, _ = _spin(app, qtcore, lambda: _truth_has_content(w), CLICK_TIMEOUT_S)
        dt = time.perf_counter() - tc
        out["clicks"].append(dt)
        out["click_when"].append(n_done)
        if not got:
            out["click_timeouts"] += 1
        if verbose:
            print("    点 %-44s %.3fs（此刻已升级 %d/%d 行）" % (nm, dt, n_done, n_expect))

    def _maybe_stop():
        if stop_at is None or stop_info["t"] is not None or done_seen[0] < stop_at:
            return False
        stop_info["n"] = done_seen[0]
        out["_snapshot"] = dict((m["name"], m["status"]) for m in w.state.models())
        stop_info["t"] = time.perf_counter()
        w.on_stop_analysis()
        return True

    emit_ts = {}                                       # 投递序号 -> 发出时刻（pump 线程写）

    def _on_poke(i):
        """一次「用户动作」被主线程真正处理到了。

        `dispatch` = 从 pump 线程 emit 到这里执行的间隔 —— 这就是**界面响应延迟**：
        队列连接的调用和真鼠标点击排的是同一条主线程队伍，前面堵着多少活，它就等多久。"""
        t_emit = emit_ts.pop(i, None)
        if t_emit is not None:
            # 一律记：**全部**落在 analysisEnded 之后，本身就是「这段时间界面没在处理任何事」的证据，
            # 只记「分析中派发到的」反而会把最坏的情况过滤掉（实测就是 0 条）。
            out["dispatch"].append(time.perf_counter() - t_emit)
            if not ended:
                out["dispatch_in"] += 1
        if _maybe_stop():
            return
        _do_click()

    poker = make_poker(qtcore)
    poker.poke.connect(_on_poke)                       # 跨线程 → 队列连接
    pump_stop = threading.Event()

    def _pump():
        i = 0
        while not pump_stop.is_set():
            i += 1
            emit_ts[i] = time.perf_counter()
            poker.poke.emit(i)                         # 第一发**不等**：分析刚开跑就投进去
            if pump_stop.wait(max(1, int(click_ms)) / 1000.0):
                break

    hb.reset()                                         # 分析这一段单独计（载表那段已单独记下）
    t_exec0 = time.perf_counter()
    pump = threading.Thread(target=_pump, daemon=True)
    if targets or stop_at is not None:
        pump.start()
    guard = qtcore.QTimer()
    guard.setSingleShot(True)
    guard.timeout.connect(loop.quit)
    guard.start(int(ANALYSIS_TIMEOUT_S * 1000))
    if not ended:
        loop.exec()
    guard.stop()
    pump_stop.set()
    hb.stop()
    out["exec_wall"] = time.perf_counter() - t_exec0
    out["heartbeats_expected"] = int(out["exec_wall"] / (HEARTBEAT_MS / 1000.0))
    if not ended:
        out["error"] = "分析超时 %.0fs" % ANALYSIS_TIMEOUT_S

    out["t2"] = (ended[0][2] - t_skel) if ended else float("nan")
    out["t_first_update"] = (upd_ts[0] - t_skel) if upd_ts else None
    out["t_last_update"] = (upd_ts[-1] - t_skel) if upd_ts else None
    out["ended_ok"] = bool(ended and ended[0][1])
    out["max_stall"] = hb.max_stall
    out["heartbeats"] = hb.ticks
    out["n_upgraded"] = done_seen[0]
    models = w.state.models()
    out["statuses"] = {}
    for m in models:
        out["statuses"][m["status"]] = out["statuses"].get(m["status"], 0) + 1
    out["kinds"] = {}
    for m in models:
        out["kinds"][m["kind"]] = out["kinds"].get(m["kind"], 0) + 1

    if stop_at is not None:
        out["stop_requested_after"] = stop_info["n"]
        out["stop_latency"] = ((ended[0][2] - stop_info["t"])
                               if (ended and stop_info["t"]) else None)
        snap = out.pop("_snapshot", {}) or {}
        reverted = [nm for nm, st in snap.items()
                    if st != "pending" and (w.state.model_of(nm) or {}).get("status") == "pending"]
        out["reverted_rows"] = reverted
        out["done_before_stop"] = sum(1 for st in snap.values() if st != "pending")

    w.close()
    app.processEvents()
    return out


def run_cli(path, mode="max", exhaustive=False):
    """同一张表的**全量 CLI 产出**用时（对照组）：载表 / generator.build / render_sv / 逐信号分析。

    档位默认取界面出厂档（`session.DEFAULT_COV_LABEL` = 「全面」= mode "max"），
    不然对照组跑的是比 GUI 更省的一档，比出来的倍数是假的。"""
    from dreg_verify import excel_model as EM, generator, providers
    from dreg_verify import exports as EX

    t = time.perf_counter()
    wb = EM.load_workbook(path)
    t_load = time.perf_counter() - t

    class _Cfg(object):
        pass

    cfg = _Cfg()
    cfg.wb = wb
    prov = providers.TopoutProvider(cfg)

    t = time.perf_counter()
    generator.build(wb, generator.GenOptions(mode=mode, max_tests=256, exhaustive=exhaustive))
    t_gen = time.perf_counter() - t

    t = time.perf_counter()
    EX.render_sv(prov, None, mode, 256, exhaustive, None)
    t_sv = time.perf_counter() - t

    t = time.perf_counter()
    prov.view_models(mode, 256, exhaustive, lite=True)
    t_vm = time.perf_counter() - t
    return {"load_wb": t_load, "generator_build": t_gen, "render_sv": t_sv, "view_models_lite": t_vm}


# ═════════════════════════ 入口 ═════════════════════════
def _fmt(x, unit="s", nd=3):
    if x is None:
        return "—"
    try:
        if x != x:                                     # NaN
            return "—"
    except TypeError:
        return "—"
    return ("%%.%df%%s" % nd) % (x, unit)


def main(argv=None):
    ap = argparse.ArgumentParser(description="GUI v2 工作台性能冒烟（200 信号量级）")
    ap.add_argument("--n", type=int, default=200, help="Topout 信号数（默认 200）")
    ap.add_argument("--path", default="", help="现成的 .xlsx；默认按 --n 现造一张到临时目录")
    ap.add_argument("--repeat", type=int, default=3, help="每项跑几次取最小值（默认 3）")
    ap.add_argument("--clicks", type=int, default=5, help="分析中随机点几个信号（默认 5）")
    ap.add_argument("--click-ms", type=int, default=20, help="两次点击之间隔多少 ms（默认 20）")
    ap.add_argument("--stop-test", action="store_true", help="另跑一次「分析中途点停止」")
    ap.add_argument("--cov", default="", choices=["", "精简", "全面", "穷举"],
                    help="覆盖度全局档；空=界面出厂档（全面）。停止测试建议 穷举（引擎才够慢）")
    ap.add_argument("--trace-alloc", action="store_true",
                    help="额外跑一趟开着 tracemalloc 的（只为 Python 对象峰值；会把计时抬 2 倍，"
                         "所以**不**参与上面那张表）")
    ap.add_argument("--verbose", action="store_true", help="逐次打印明细")
    args = ap.parse_args(argv)

    try:                                               # 中文输出别在 GBK 控制台上炸/乱码
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:                                  # noqa: BLE001
        pass

    path = args.path
    if not path:
        import make_mirror_big as B
        path = os.path.join(tempfile.gettempdir(), "perf_smoke_%d.xlsx" % args.n)
        t = time.perf_counter()
        B.build(path, args.n)
        print("生成合成表 %s（%d 信号，%.2fs）" % (path, args.n, time.perf_counter() - t))

    # ⚠ 绝不在计时那几趟开 tracemalloc：实测它把 T2 抬了整整一倍（200 信号 1.9s → 4.1s）。
    # 想要 Python 对象峰值就 `--trace-alloc`，那一趟单独跑、不进计时表。
    from PySide6 import QtCore, QtWidgets
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    mode, exh = {"": ("max", False), "精简": ("min", False), "全面": ("max", False),
                 "穷举": ("exhaustive", True)}[args.cov]
    runs = []
    for i in range(max(1, args.repeat)):
        r = run_once(app, QtCore, path, args.n, clicks=args.clicks, seed=i, verbose=args.verbose,
                     click_ms=args.click_ms, cov_label=args.cov)
        runs.append(r)
        print("  第 %d 次：T1=%s T2=%s 最大停顿=%s 升级 %s 行（首/末 %s / %s）派发延迟 max=%s"
              % (i + 1, _fmt(r.get("t1")), _fmt(r.get("t2")), _fmt(r.get("max_stall")),
                 r.get("n_upgraded"), _fmt(r.get("t_first_update")), _fmt(r.get("t_last_update")),
                 _fmt(max(r.get("dispatch") or [0]) or None)))
        if r.get("error"):
            print("  ⚠ %s" % r["error"])

    clis = [run_cli(path, mode, exh) for _ in range(max(1, args.repeat))]
    rss_peak = _peak_rss_mb()
    py_peak = None
    if args.trace_alloc:
        tracemalloc.start()
        run_once(app, QtCore, path, args.n, clicks=0, seed=7, click_ms=args.click_ms,
                 cov_label=args.cov)
        py_peak = tracemalloc.get_traced_memory()[1] / (1024.0 * 1024.0)
        tracemalloc.stop()

    good = [r for r in runs if not r.get("error")]
    ref = good[0] if good else runs[0]

    def _min(key):
        vals = [r[key] for r in good if r.get(key) is not None and r[key] == r[key]]
        return min(vals) if vals else None

    n = args.n
    t1, t2 = _min("t1"), _min("t2")
    stall = _min("max_stall")
    all_clicks = [c for r in good for c in (r.get("clicks") or [])]
    print("")
    print("══════ n=%d ══════（%d 次取最小；机器上有并行负载）" % (n, len(runs)))
    print("  T1 载表→清单可点        %s   （清单 %s 行，select_first_visible=%s）"
          % (_fmt(t1), ref.get("rows_after_load"), ref.get("clickable")))
    print("  T2 骨架→analysisEnded   %s   （每信号均摊 %s）"
          % (_fmt(t2), _fmt((t2 / n) * 1000 if t2 else None, "ms", 1)))
    print("  最大一次事件循环停顿      %s   （分析中；%d ms 心跳实触发 %d 次 / 应 %d 次）"
          % (_fmt(stall), HEARTBEAT_MS, ref.get("heartbeats", 0),
             ref.get("heartbeats_expected", 0)))
    print("     └ 载表那一段（同步）    %s   （主线程自己在 load+skeleton，必然顶到 ≈T1）"
          % _fmt(_min("stall_during_load")))
    disp = [d for r in good for d in (r.get("dispatch") or [])]
    print("  界面响应延迟（投递→处理）  max=%s  中位=%s  共 %d 次投递，其中 %d 次在分析结束前被处理"
          % (_fmt(max(disp) if disp else None),
             _fmt(sorted(disp)[len(disp) // 2] if disp else None), len(disp),
             sum(r.get("dispatch_in", 0) for r in good)))
    print("     └ 派发到之后点信号     max=%s  中位=%s  超时 %d 次  点时已升级 %s 行"
          % (_fmt(max(all_clicks) if all_clicks else None),
             _fmt(sorted(all_clicks)[len(all_clicks) // 2] if all_clicks else None),
             sum(r.get("click_timeouts", 0) for r in good),
             "/".join(str(x) for x in (ref.get("click_when") or [])) or "—"))
    print("  内存峰值                进程工作集 %s（含 Qt C++ 侧）/ Python 对象 %s"
          % (_fmt(rss_peak, " MB", 1),
             _fmt(py_peak, " MB", 1) if py_peak is not None else "—（要 --trace-alloc）"))
    print("  CLI 对照（同表全量，档=%s）载表 %s · generator.build %s · render_sv %s · view_models(lite) %s"
          % (args.cov or "全面",
             _fmt(min(c["load_wb"] for c in clis)),
             _fmt(min(c["generator_build"] for c in clis)),
             _fmt(min(c["render_sv"] for c in clis)),
             _fmt(min(c["view_models_lite"] for c in clis))))
    print("  状态分布                %s" % (ref.get("statuses"),))
    print("  分类分布                %s" % (ref.get("kinds"),))

    if args.stop_test:
        print("")
        print("══════ 分析中途点停止（n=%d）══════" % n)
        r = run_once(app, QtCore, path, n, clicks=0, seed=99, stop_at=max(5, n // 4),
                     verbose=args.verbose, click_ms=args.click_ms, cov_label=args.cov)
        print("  T2=%s；已升级 %s 行时点停止 → analysisEnded(False) 用时 %s（正常跑完=%s）"
              % (_fmt(r.get("t2")), r.get("stop_requested_after"), _fmt(r.get("stop_latency")),
                 r.get("ended_ok")))
        print("  停止前已展开完 %s 行；停止后回退成 pending 的行：%s"
              % (r.get("done_before_stop"), r.get("reverted_rows") or "（一行都没回退 ✅）"))
        print("  停止后状态分布 %s" % (r.get("statuses"),))
    return 0


if __name__ == "__main__":
    sys.exit(main())
