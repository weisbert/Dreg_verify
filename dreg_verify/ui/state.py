# -*- coding: utf-8 -*-
"""state.py —— 会话状态 `WorkbenchState`（架构 §2.1；不变量 I-01…I-11 / I-21）。

组合根持有**一个**，全窗口共用。视图只读它、只经它写；视图之间不互相调，改了什么由它发信号广播。
它同时是 `providers.ConfigSourceProto`（wb / 三套诊断配置 / include_risky / engine_lock），
所以五个 provider 拿到的配置与界面上看到的永远是同一份——这一层存在的首要理由就是「口径只有一份」。

里面装着什么（`contracts.WorkbenchStateProto`）：
    表与范围      excel_path / loaded_path / wb / scope / current_name + 五个 provider
    清单          每个范围一份 models（骨架 → lite 逐信号升级）+ 指纹（C-265 判要不要重跑）
    用户的活      勾选 / 反例 / 逐信号真值表编辑 / mux 数据值手填
    覆盖度        每个范围一个 `session.CoverageState`（三层：单点 > 逻辑类型 > 全局）
    诊断配置      探针前缀 / 强制 force / RTL 补充逻辑（按 Excel **全路径**分桶，I-04）+ include_risky
    互斥          `engine_lock`：后台 worker 与主线程共用同一个 wb（I-21）

三条最容易写错、这里写死的规矩：
  · **持久化只经 `persist`**：格式归 `session`/`edits`，路径与落盘策略归 `persist`，本模块不碰文件。
  · **改配置不碰勾选**（C-205）：改前缀/force/补充逻辑只让分析失效（清缓存 + 指纹变），
    用户挑了半天的勾选集不许跟着被重置。
  · **批量操作挂起存盘**（C-244）：`with state.suspend_persist():` 里怎么改都只在退出时写一次。

依赖方向（§1.2）：Qt-free 层（session / edits / exports / providers）+ `excel_model.load_workbook`
（§2.1 指定的载表入口）+ contracts / persist / terms + PySide6.QtCore。不 import 任何视图。
"""

import contextlib
import threading

from PySide6 import QtCore

from dreg_verify import edits as ED
from dreg_verify import excel_model
from dreg_verify import providers as PV
from dreg_verify import session

from . import contracts
from . import persist
from . import terms

__all__ = ["WorkbenchState", "EngineLock", "STATUS_RESTORE_BAD_FMT"]

#: C-240：桶里数值损坏 → 逐条跳过并报个数。
#: ⚠ 界面文案本该住在 `ui/terms.py`（I-13），但 terms.py 不在 C1-a 的 owner 文件集里
#: （本波四个 agent 并行，改公共文件必撞车）——C4/C5 收口时连同这条注释一起搬过去。
STATUS_RESTORE_BAD_FMT = "另有 {n} 个存盘数值读不出来，已逐条跳过（其余编辑不受影响）"

#: legacy 覆盖度键（C-230）：只在缺 `cov_<view_id>` 时读一次作迁移，**永不回写**
_LEGACY_COV_KEYS = {"logic": "coverage_logic", "mux": "coverage_mux"}
_LEGACY_COV_FALLBACK = "coverage"
_LEGACY_MAXT_KEY = "max_tests"

#: 三套按路径分桶的诊断配置：`configChanged` 的 which 值 = settings 段名
CFG_PREFIXES = "probe_prefixes"
CFG_FORCE = "force_signals"
CFG_OVERRIDES = "logic_overrides"
CFG_RISKY = "include_risky"


class EngineLock(object):
    """引擎互斥锁（I-21）：可重入 + **等待计数**。

    §2.1 写的是裸 `threading.RLock()`；这里多一个 `waiting` 计数，理由是 worker 那边
    「信号边界让锁」需要知道**到底有没有人在等**：
      · 没人等 → 一个字节都不浪费（不 release、不 sleep、不 acquire，200 个信号跑下来零开销）；
      · 有人等 → 让出后自旋等到对方真的拿走再回来取，交接是确定的，不靠 `sleep(0)` 赌调度。
    裸 RLock 两条都做不到：`release(); sleep(0); acquire()` 在 Windows 上可能原地又被自己抢回来，
    主线程饿着——表现就是「载入时点信号没反应」，且只在别人机器上偶发。

    对外仍然只是个上下文管理器，`providers._BaseProvider._engine()` 照用不误。"""

    def __init__(self):
        self._lock = threading.RLock()
        self._guard = threading.Lock()
        self._waiting = 0

    @property
    def waiting(self):
        """此刻有几个线程堵在 `acquire` 上。"""
        return self._waiting

    def acquire(self, blocking=True):
        with self._guard:
            self._waiting += 1
        try:
            return self._lock.acquire(blocking)
        finally:
            with self._guard:
                self._waiting -= 1

    def release(self):
        self._lock.release()

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *exc):
        self.release()
        return False


class WorkbenchState(QtCore.QObject):
    """会话状态对象。信号逐个按 `contracts.STATE_SIGNALS` 声明（测试按名 getattr 核对）。"""

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

    def __init__(self, parent=None):
        QtCore.QObject.__init__(self, parent)
        # ── 表与范围 ──
        self.excel_path = ""            # 顶栏路径框里的（可能还没载）
        self.loaded_path = ""           # 已载入的（编辑分桶键，C-236）
        self.wb = None
        self.scope = contracts.DEFAULT_VIEW_ID
        self.current_name = ""
        # ── 诊断配置（ConfigSourceProto 的四项）──
        self.probe_prefixes = {}
        self.force_signals = set()
        self.logic_overrides = {}
        # I-11 / C-217：可见开关**缺省 True**。刻意不在构造时读 settings——不然一台真机上
        # 存着 False，所有默认值测试就随机红；真正的恢复在 load() 里（那时路径已隔离/已确定）。
        self.include_risky = True
        # ── 引擎互斥（I-21）：worker 与主线程共用同一个 wb，逐信号粒度由 worker 收放 ──
        self.engine_lock = EngineLock()
        # ── 逐范围的会话数据 ──
        self._providers = {}
        self._cov = {vid: session.CoverageState(vid, load=persist.load_settings,
                                                save=persist.save_settings)
                     for vid in contracts.VIEW_IDS}
        self._models = {vid: [] for vid in contracts.VIEW_IDS}
        self._fingerprint = {}          # view_id -> 上一趟跑完时的指纹（C-265）
        self._checks = {vid: None for vid in contracts.VIEW_IDS}      # None = 全勾（C-243）
        self._negs = {vid: set() for vid in contracts.VIEW_IDS}
        self._edits = {vid: {} for vid in contracts.VIEW_IDS}
        self._mux_data = {vid: {} for vid in contracts.VIEW_IDS}
        # ── 分析缓存（键含指纹）──
        self._an_cache = {}
        self._an_fp = {}
        self._cfg_ver = 0
        # ── 存盘 ──
        self._bucket = {}               # 本表的 edits 桶（载表时读一次）
        self._restored = set()          # 已按本表桶还原过的范围（每次载表一遍）
        self._owned = set()             # 本会话管着的范围 → 只有它们才回写（见 persist_edits）
        self._persist_suspended = 0
        self._persist_pending = False
        self._code_version = None       # 懒算一次，见 code_version()

    # ═════════════ ① 载表 ═════════════
    def load(self, path):
        """载一张表：建 provider → 恢复覆盖度/诊断配置 → 读本表编辑桶 → 记 MRU。

        返回是否成功。**唯一允许失败的地方**：表读不进来 → `loadFailed`（已 scrub 的一句人话，
        C-005 / C-272：不弹窗、不退出、不把 traceback 送上界面）。"""
        path = str(path or "")
        self.excel_path = path
        try:
            wb = excel_model.load_workbook(path)
        except Exception as exc:        # noqa: BLE001  坏表/占用中/根本不是 xlsx 都走这条
            self.loadFailed.emit(terms.STATUS_LOAD_FAILED_FMT.format(
                reason=terms.scrub(str(exc).strip() or exc.__class__.__name__)))
            return False
        self.wb = wb
        self.loaded_path = path
        self._reset_for_new_workbook()
        st = persist.load_settings()
        self.include_risky = bool(st.get(CFG_RISKY, True))
        self._restore_coverage(st)
        self._restore_config(path)
        self._bucket = persist.load_edits_bucket(path)      # C-236：按【已载入】路径分桶
        self._cfg_ver += 1
        persist.push_recent(path)                           # 顺带照写 last_excel（C-003 / C-228）
        self.workbookChanged.emit()
        return True

    def _reset_for_new_workbook(self):
        """换表 = 先把上一张表的一切清干净（C-237：上一张表的编辑绝不按同名串进新表）。"""
        self._providers = {"topout": PV.TopoutProvider(self)}
        for page in contracts.VIEW_IDS[1:]:
            self._providers[page] = PV.PageProvider(self, page)
        for vid in contracts.VIEW_IDS:
            self._models[vid] = []
            self._checks[vid] = None
            self._negs[vid] = set()
            self._edits[vid] = {}
            self._mux_data[vid] = {}
        self._fingerprint.clear()
        self._an_cache.clear()
        self._an_fp.clear()
        self._restored.clear()
        self._owned.clear()
        self.current_name = ""

    def _restore_coverage(self, st):
        """每个范围各自恢复全局档与用例上限（I-02 / C-229），缺键时读一次 legacy 键作迁移（C-230）。

        `may_read_machine_settings()` 挡住的只有「pytest + 真机路径」那一种组合（I-08）；
        真机上四条恢复全都照常跑。"""
        allow = persist.may_read_machine_settings()
        for vid, cov in self._cov.items():
            cov.sig_cov = {}                    # C-245：单点档/逻辑类型档是会话内的，换表清掉
            cov.form_cov = {}
            if not allow:
                continue
            cov.restore_global_label()
            cov.restore_max_tests(skip_under_pytest=False)
            if cov.cov_key not in st:           # 没有 cov_<view_id> 才看旧键，且**只读不写**
                legacy = st.get(_LEGACY_COV_KEYS.get(vid, ""), None) or st.get(_LEGACY_COV_FALLBACK)
                if legacy in session.COV_LABELS:
                    cov.global_label = legacy
            if cov.maxt_key not in st:
                v = st.get(_LEGACY_MAXT_KEY)
                lo, hi = session.MAX_TESTS_RANGE
                if isinstance(v, int) and lo <= v <= hi:
                    cov.max_tests = v

    def _restore_config(self, path):
        """三套诊断配置按 Excel 全路径分桶恢复（I-04 / C-233）。

        `suffix_override` 也在同一批旧键里（A3 §3.1），v2 **不读它**——随入口删掉的开关，
        载入时视而不见即可，绝不因为文件里有这个键就报错。"""
        self.probe_prefixes = session.normalize_probe_prefixes(
            persist.path_map_of(CFG_PREFIXES, path)) or {}
        self.force_signals = session.normalize_force_signals(
            list(persist.path_map_of(CFG_FORCE, path) or [])) or set()
        self.logic_overrides = session.normalize_logic_overrides(
            persist.path_map_of(CFG_OVERRIDES, path)) or {}

    # ═════════════ ② 范围与 provider ═════════════
    def set_scope(self, view_id):
        """只换当前范围（C-038）。要不要重跑清单由 app 看 `needs_analysis()` 决定（C-265）。"""
        vid = self._vid(view_id)
        if vid == self.scope:
            return
        self.scope = vid
        self.scopeChanged.emit(vid)

    def provider(self, view_id=None):
        return self._providers.get(self._vid(view_id))

    def page_available(self, view_id):
        p = self.provider(view_id)
        return bool(p is not None and p.has_page())

    # ═════════════ ③ 清单模型 ═════════════
    def models(self, view_id=None):
        return self._models.get(self._vid(view_id), [])

    def model_of(self, name, view_id=None):
        low = str(name or "").lower()
        return next((m for m in self.models(view_id) if str(m["name"]).lower() == low), None)

    def set_models(self, view_id, models, partial=False):
        """worker 回填整张清单。

        `partial=True`（骨架第一趟 / 取消后的部分结果）：**按名合并**，没回填到的行保留原样
        （骨架行的 `status="pending"`），绝不把已经分析好的行退回 pending，也绝不凭空丢行。"""
        vid = self._vid(view_id)
        new = list(models or [])
        if partial and self._models.get(vid):
            by = {str(m["name"]).lower(): m for m in new}
            merged = [by.pop(str(old["name"]).lower(), old) for old in self._models[vid]]
            merged.extend(by.values())          # 骨架里没有的新行（正常不会有，出现也不丢）
            new = merged
        self._models[vid] = new
        if not partial:
            self._fingerprint[vid] = self.fingerprint(vid)      # C-265：这一趟的指纹记下来
            if self.loaded_path:
                persist.update_recent_count(self.loaded_path, len(new))     # N4 补信号数
        self._restore_edits_for(vid)
        self.modelsChanged.emit(vid)

    def update_model(self, view_id, model):
        """worker 的 `signalDone`：只换一行（清单只 dataChanged 那一行，不整表重建）。"""
        vid = self._vid(view_id)
        name = str((model or {}).get("name") or "")
        low = name.lower()
        rows = self._models.get(vid) or []
        for i, m in enumerate(rows):
            if str(m["name"]).lower() == low:
                rows[i] = model
                break
        else:
            rows.append(model)
        self.modelUpdated.emit(vid, name)

    def fingerprint(self, view_id=None):
        """清单重建指纹（C-265）：覆盖度三层 + 用例上限 + 配置版本。

        §2.1 的公式写的是 `sorted(sig_cov)`（只排键）——这里用 `sorted(items())`：
        把某个信号的单点档从『精简』改成『穷举』时键集没变、用例数却变了，只排键会漏掉这种改动，
        清单就停在旧数上。多带上值不花钱，漏掉要靠用户发现。"""
        vid = self._vid(view_id)
        cov = self._cov[vid]
        mode, exh = cov.mode()
        return "%s|%s|%s|%d|%s|%s|%d" % (
            vid, mode, exh, int(cov.max_tests),
            sorted(cov.sig_cov.items()), sorted(cov.form_cov.items()), self._cfg_ver)

    def needs_analysis(self, view_id=None):
        """这个范围要不要（重新）跑清单：没跑过 或 指纹变了（C-265：切页不重跑）。"""
        vid = self._vid(view_id)
        return self._fingerprint.get(vid) != self.fingerprint(vid)

    def analysis_request(self, view_id=None):
        """→ `contracts.AnalysisRequest`（worker 的入参），覆盖度三层已折进去。"""
        vid = self._vid(view_id)
        cov = self._cov[vid]
        mode, exh = cov.mode()
        prov = self.provider(vid)
        sig_ok = bool(prov is not None and getattr(prov, "supports_sig_cov", False))
        return contracts.AnalysisRequest(
            view_id=vid, mode=mode, max_tests=int(cov.max_tests), exhaustive=bool(exh),
            sig_cov=(dict(cov.sig_cov) or None) if sig_ok else None,
            form_cov=(dict(cov.form_cov) or None) if sig_ok else None,
            fingerprint=self.fingerprint(vid))

    def set_current(self, name):
        """当前信号（""=无）。只记名——详情区自己去 `analyze`。"""
        nm = str(name or "")
        if nm == self.current_name:
            return
        self.current_name = nm
        self.currentChanged.emit(nm)

    # ═════════════ ④ 勾选 / 反例 ═════════════
    def checked(self, view_id=None):
        """勾选集；**None = 全勾**（默认态，不写桶，C-243）。"""
        return self._checks.get(self._vid(view_id))

    def is_checked(self, name, view_id=None):
        ch = self.checked(view_id)
        return True if ch is None else (str(name or "").lower() in ch)

    def checked_names(self, view_id=None):
        """当前真正勾着的原样信号名列表（全勾时 = 全部模型名）——导出/预览的 only 用它。"""
        vid = self._vid(view_id)
        ch = self._checks.get(vid)
        names = [m["name"] for m in self.models(vid)]
        return names if ch is None else [n for n in names if n.lower() in ch]

    def set_checked(self, names, on, view_id=None):
        """勾上/取消一批信号（C-022 / C-242）。全勾 → 回到 None（默认态，不写桶，C-243）。"""
        vid = self._vid(view_id)
        all_low = {str(m["name"]).lower() for m in self.models(vid)}
        cur = self._checks.get(vid)
        cur = set(all_low) if cur is None else set(cur)
        for n in (names or []):
            low = str(n).lower()
            if on:
                cur.add(low)
            else:
                cur.discard(low)
        self._checks[vid] = None if (all_low and cur >= all_low) else cur
        self._owned.add(vid)
        self.checksChanged.emit(vid)
        self.persist_edits()

    def negs(self, view_id=None):
        """勾了反例的信号名（小写）。派生自编辑状态——「有反例列」才算勾着。"""
        return self._negs.get(self._vid(view_id), set())

    def has_negatives(self, name, view_id=None):
        return str(name or "").lower() in self.negs(view_id)

    def set_neg(self, name, on, view_id=None):
        """整信号加/清反例（C-023）。返回 `(加了几条 或 删了几条, 跳过几条)`。

        与 v1 `_toggle_signal_negative` 同语义（已有反例不重复加、只对正向列造），
        但错值走 `edits.add_negatives` —— 它会避开 auto_out 与 designer 手填期望两个「正确值」，
        裸 `~auto` 恰好等于手填期望时反例会 PASS（NEG-BROKEN，静默假绿）。"""
        vid = self._vid(view_id)
        an = self.analyze(name, vid)
        if an is None or not an.get("editable"):
            return (0, 0)
        low = str(name).lower()
        ed = self._edits[vid].get(low)
        cols = list(ed["cols"]) if ed else ED.cols_from_vectors(an, self._vec_keys(an))
        n, skipped = 0, 0
        if on:
            if not any(c["neg"] for c in cols):
                first = next((j for j, c in enumerate(cols) if not c["neg"]), None)
                if first is not None:
                    made, skipped = ED.add_negatives(cols, [first], False)
                    cols = list(cols) + made
                    n = len(made)
        else:
            cols, n = ED.del_negatives(cols)
        self.put_edit(name, {"kind": an["kind"], "src_out_name": an["src_out_name"],
                             "name": an["name"], "renamed": an.get("renamed", False),
                             "cols": cols, "an": an}, vid)
        self.negsChanged.emit(vid)
        return (n, skipped)

    @staticmethod
    def _vec_keys(an):
        """`edits.cols_from_vectors` 要的 e_inputs —— 它只用其中的 `key` 一项（且只有 mux 路用）。

        真值表行的显示信息（标签/角色/可编辑）归 `ui/truth/model.py`；这里造最小的那份，
        免得为了勾一个反例把整套行模型搬进状态层。"""
        if (an or {}).get("editable") == "mux":
            return [{"key": k} for k in ((an.get("expansion") or {}).get("used_vars") or [])]
        return []

    def protected_negatives(self, names, view_id=None):
        """这批信号里，反例「值得保护」的那些（C-036：清除反例前先点名再计数）。

        值得保护 = 自定义命名过 或 手填过错值 —— 口径只有一份，在 `edits.protected_negatives`
        （清单视图不 import edits，只问这里）。没有编辑记录 = 没有反例 = 不在名单里。
        返回**原样**信号名（界面要拿去点名给用户看，不能给小写键）。"""
        vid = self._vid(view_id)
        out = []
        for n in (names or ()):
            ed = self._edits[vid].get(str(n).lower())
            if ed and ED.protected_negatives(ed.get("cols") or []):
                out.append(str(n))
        return out

    def _sync_negs(self, vid):
        self._negs[vid] = {low for low, ed in self._edits[vid].items()
                           if any(c["neg"] for c in (ed.get("cols") or []))}

    # ═════════════ ⑤ 真值表编辑（edits.py 的列模型）═════════════
    def edits(self, view_id=None):
        return self._edits.get(self._vid(view_id), {})

    def edit_of(self, name, view_id=None):
        return self.edits(view_id).get(str(name or "").lower())

    def put_edit(self, name, record, view_id=None):
        """写入某信号的列模型 `{kind, src_out_name, name, renamed, cols, an}`（真值表 panel 的出口）。"""
        vid = self._vid(view_id)
        self._edits[vid][str(name).lower()] = record
        self._owned.add(vid)
        self._sync_negs(vid)
        self.editsChanged.emit(vid, str(name))
        self.persist_edits()

    def drop_edit(self, name, view_id=None):
        """丢掉某信号的编辑（回到引擎默认真值表）。"""
        vid = self._vid(view_id)
        if self._edits[vid].pop(str(name).lower(), None) is None:
            return
        self._owned.add(vid)
        self._sync_negs(vid)
        self.editsChanged.emit(vid, str(name))
        self.persist_edits()

    def mux_data(self, view_id=None):
        """mux 数据值手填 `{信号名低: {"src","name","data"}}`——**会话档，刻意不落盘**。"""
        return self._mux_data.setdefault(self._vid(view_id), {})

    def compute_edited(self, view_id=None):
        """编辑状态 → 喂 `exports.render_sv` / 报告 的 `edited`。"""
        vid = self._vid(view_id)
        return ED.compute_edited(self._edits[vid], self._mux_data[vid])

    def analyze(self, name, view_id=None, want_graph=False):
        """一个信号的统一分析结果 an（真值表/展开链/输入表/电路图都吃它）。找不到 → None。

        · 覆盖度按**本信号**的生效档（单点 > 逻辑类型 > 全局，`CoverageState.mode_for`）；
        · 缓存键含指纹与该信号的 mux 数据手填，配置一变自然失效；
        · **不再套一层 `engine_lock`**：provider 每次引擎调用内部已经持锁（providers.py 的
          `_engine()`），这里再包一层只是白拿一次可重入锁，反而让「谁持有锁」难读。"""
        vid = self._vid(view_id)
        prov = self.provider(vid)
        if prov is None or self.wb is None or not name:
            return None
        fp = self.fingerprint(vid)
        if self._an_fp.get(vid) != fp:                  # 指纹变了 → 这个范围的缓存整段作废
            self._an_fp[vid] = fp
            for k in [k for k in self._an_cache if k[0] == vid]:
                self._an_cache.pop(k, None)
        cov = self._cov[vid]
        mode, exh = cov.mode_for(name, self.models(vid))
        data = self._mux_data_for(name, vid)
        key = (vid, str(name).lower(), fp, bool(want_graph), repr(sorted((data or {}).items())))
        if key in self._an_cache:
            return self._an_cache[key]
        try:
            an = prov.analyze(name, mode, int(cov.max_tests), exh,
                              mux_data=data, want_graph=want_graph)
        except Exception:       # noqa: BLE001  单信号炸了不连累别人（C-007：那一行标解析异常）
            an = None
        if an is not None:
            self._an_cache[key] = an
        return an

    def _mux_data_for(self, name, vid):
        ent = self._mux_data[vid].get(str(name).lower())
        return dict(ent["data"]) if ent and ent.get("data") else None

    # ═════════════ ⑥ 覆盖度 ═════════════
    def coverage(self, view_id=None):
        """这个范围的 `session.CoverageState`（三层档）。视图改完档**必须**调 `coverage_touched`。"""
        return self._cov[self._vid(view_id)]

    def coverage_touched(self, view_id=None):
        """覆盖度三层任一层变了（C-148）：广播 + 让指纹变（清单该重跑了）。"""
        vid = self._vid(view_id)
        self.coverageChanged.emit(vid)

    # ═════════════ ⑦ 诊断配置（写本表桶）═════════════
    def set_probe_prefixes(self, mapping):
        """探针前缀整体替换。返回 `(配了几条, 当前范围有几个信号吃到)`——诊断抽屉要报影响面。"""
        self.probe_prefixes = {str(k).strip().lower(): str(v).strip()
                               for k, v in (mapping or {}).items() if str(v).strip()}
        self._save_bucketed(CFG_PREFIXES, dict(self.probe_prefixes))
        n_aff = sum(1 for m in self.models()
                    if str(m.get("probe_net") or "").lower() in self.probe_prefixes)
        self._config_changed(CFG_PREFIXES)
        return (len(self.probe_prefixes), n_aff)

    def set_force_signals(self, names):
        """强制 force 名单整体替换。返回条数。"""
        self.force_signals = {str(n).strip().lower() for n in (names or ()) if str(n).strip()}
        self._save_bucketed(CFG_FORCE, sorted(self.force_signals))
        self._config_changed(CFG_FORCE)
        return len(self.force_signals)

    def set_logic_overrides(self, spec):
        """RTL 补充逻辑整体替换（{基名低: spec}）。"""
        self.logic_overrides = {str(k).strip().lower(): dict(v)
                                for k, v in (spec or {}).items() if isinstance(v, dict)}
        self._save_bucketed(CFG_OVERRIDES, dict(self.logic_overrides))
        self._config_changed(CFG_OVERRIDES)

    def _save_bucketed(self, key, value):
        """写一段按路径分桶的配置。**没载表就只在会话里生效、不落盘**——
        分桶键是 Excel 全路径，没有路径时写进去就是一条 `""` 的垃圾桶，
        换了表还会被当成「那张表的配置」读回来。"""
        if not self.loaded_path:
            return False
        return persist.save_path_map(key, self.loaded_path, value)

    def set_include_risky(self, on):
        """缺探针前缀的输入放不放行（C-217）。**顶层键**，不按表分桶（它是工具的口径，不是某张表的配置）。"""
        self.include_risky = bool(on)
        persist.patch_settings({CFG_RISKY: bool(on)})
        self._config_changed(CFG_RISKY)

    def _config_changed(self, which):
        """配置变了：分析全作废、指纹翻篇；**勾选一个不动**（C-205）。"""
        self._cfg_ver += 1
        self._an_cache.clear()
        self._an_fp.clear()
        self.configChanged.emit(str(which))

    # ═════════════ ⑧ 偏好 / 导出记录 ═════════════
    def settings(self):
        return persist.load_settings()

    def save_settings(self, patch):
        persist.patch_settings(patch)
        self.settingsChanged.emit()

    def recent_excels(self):
        return [contracts.RecentExcel(path=it["path"], ts=it["ts"], n_signals=it["n_signals"])
                for it in persist.recent_excels()]

    def last_export(self, kind):
        it = persist.last_export(kind)
        return contracts.LastExport(path=it["path"], ts=it["ts"]) if it else None

    def record_export(self, kind, path):
        if persist.record_last_export(kind, path):
            self.exportRecorded.emit(str(kind))

    def status(self, text):
        """状态栏逐操作反馈（C-269）——本层自己产的消息也走它，视图不必重复拼。"""
        if text:
            self.statusMessage.emit(str(text))

    def code_version(self):
        """工具代码版本（短 HEAD；拿不到给空串）→ 窗口标题（C-259 / 裁决⑬：界面不写 git 字样）。

        **一个进程只问一次**：`session.code_version()` 起的是 `git rev-parse` 子进程，
        而标题每次载表都刷一遍——每张表付一次子进程的钱没道理，版本在进程生命期内也不会变。"""
        if self._code_version is None:
            self._code_version = str(session.code_version() or "")
        return self._code_version

    # ═════════════ ⑨ 编辑存盘（C-244 / C-300）═════════════
    @contextlib.contextmanager
    def suspend_persist(self):
        """批量操作挂起逐格存盘，退出时统一写一次（C-244）。可嵌套。"""
        self._persist_suspended += 1
        try:
            yield self
        finally:
            self._persist_suspended -= 1
            if self._persist_suspended <= 0 and self._persist_pending:
                self._persist_pending = False
                self.persist_edits()

    def persist_edits(self):
        """把本会话管着的范围写回 edits 文件（**桶合并**，C-300）。返回是否真落了盘。

        `_owned` 是「本会话按本表桶还原过、或本会话改过」的范围。只回写这些：
        清单还没出来（worker 还在跑）时若把五个范围一股脑写回去，等于用一份空编辑
        覆盖掉同事存在文件里的手填期望——而且没有任何报错。"""
        if self._persist_suspended > 0:
            self._persist_pending = True
            return False
        if not self.loaded_path or not self._owned:
            return False
        ve, vc = {}, {}
        for vid in sorted(self._owned):
            ve[vid] = ED.serialize_view_edits(self._edits[vid])
            ch = self._checks.get(vid)
            # C-243：全勾（None）= 默认态 → 写 None，`write_edits_bucket` 会把这一格删掉
            vc[vid] = None if ch is None else [m["name"] for m in self.models(vid)
                                               if str(m["name"]).lower() in ch]
        return persist.write_edits_bucket(self.loaded_path, {"view_edits": ve, "view_checks": vc})

    def _restore_edits_for(self, vid):
        """清单一有名字就按本表桶还原编辑与勾选（C-234 / C-237 / C-238 / C-239 / C-240 / C-241）。

        每次载表每个范围只做一遍（骨架清单到齐就能做——还原只要名字对得上）。
        还原走 `edits.restore_view_edits`：逐信号重新分析、**重算 auto**（C-238，改表后不留陈旧假绿），
        桶里有而当前表没有的信号点名跳过（C-239），坏数值逐条跳过（C-240），最后报个数（C-241）。"""
        if vid in self._restored or not self._models.get(vid):
            return
        self._restored.add(vid)
        self._owned.add(vid)
        ve = ((self._bucket.get("view_edits") or {}).get(vid)) or {}
        vc = ((self._bucket.get("view_checks") or {}).get(vid))
        if ve:
            models = self._models[vid]
            have = {str(m["name"]).lower() for m in models}
            missing = [n for n in ve if str(n).lower() not in have]
            self._edits[vid] = ED.restore_view_edits(ve, models, lambda real: self.analyze(real, vid))
            self._sync_negs(vid)
            bad = sum(max(0, len((ve.get(low) or {}).get("cols") or []) - len(ed.get("cols") or []))
                      for low, ed in self._edits[vid].items())
            self._report_restore(len(self._edits[vid]), missing, bad)
        if vc is not None:
            want = {str(n).lower() for n in vc}
            self._checks[vid] = {str(m["name"]).lower() for m in self._models[vid]
                                 if str(m["name"]).lower() in want}
            self.checksChanged.emit(vid)

    def _report_restore(self, n, missing, bad):
        """恢复结果落状态栏：先点名、再计数（C-270 / I-20）。"""
        if missing:
            self.status(terms.STATUS_RESTORE_MISSING_FMT.format(
                n=len(missing), names="、".join(str(m) for m in missing)))
        if bad:
            self.status(STATUS_RESTORE_BAD_FMT.format(n=bad))
        if n:
            self.status(terms.STATUS_RESTORED_FMT.format(n=n))

    # ═════════════ 小工具 ═════════════
    def _vid(self, view_id=None):
        vid = str(view_id or self.scope)
        return vid if vid in contracts.VIEW_IDS else contracts.DEFAULT_VIEW_ID
