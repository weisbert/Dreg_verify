# -*- coding: utf-8 -*-
"""diagnostics.py —— ⑬ 诊断抽屉（GUI v2 Phase C4-b）。

Design（`docs/GUI_v2_Design对齐_20260912.md` §1.2 ⑬）的版面：右侧 620px 抽屉，
**按症状选、不按按钮选**——

    「按症状选，不是按按钮选。」                       DIAG_INTRO
    ┌ 仿真报「找不到这根网」（CUVUNF） ─────────┐    DIAG_CUVUNF_BOX（主症状蓝框）
    │ 解释 + 三步：① 导 nets.txt ② 红区跑 scan_rtl  │  每步：编号 + 标题 + 正文 + 结果框 + 主按钮
    │ ③ 把 probe_prefixes.txt 导回来                │
    └───────────────────────────────────┘
    其它症状（五条折叠项）                             DIAG_SYM_SUPPLEMENT / _FORCE / _COVERAGE
      ① 规格缺一级逻辑 → RTL 补充逻辑编辑器             / _RISKY / _LEGACY
      ② 两个东西撞名   → 强制 force 名单编辑器
      ③ 覆盖度不够     → 打开覆盖度弹层（只发信号；组合根 `app.open_coverage` 接，用标题栏那一个）
      ④ 还有网没前缀   → 「缺前缀是否强制生成」全局开关（默认 True，I-11 / C-217）
      ⑤ 以前在旧版界面填过期望 → 旧版真值表编辑迁移（C-302，一次性显式动作）
    「这里的每一项都是逃生阀，不是日常主力。」           DIAG_FOOTER

本模块的分工（与 `side_panel.py` / `export_center.py` 同一套规矩）：

  · **算东西全在 Qt-free 段**（文件上半部：`make_snapshot` / `prefix_impact` / `nets_summary` /
    `plan_legacy_import` / `apply_legacy_import`）——它们只吃 `state` 与 `session` / `edits` /
    `inputs_table`，不碰 Qt，可以脱开界面单测；
  · 文本格式（探针前缀 .txt / 强制 force .txt / 补充逻辑 .json）**一个字节都不在这里拼**，
    全走 `session.*`（C-248 / C-249 的文件格式不变靠这一条）；
  · 文案只取 `terms`、色值只取 `theme`、objectName 只取 `names`；后端来的文本（六道校验的
    错误串、文件 IO 的报错）先过 `terms.scrub`（不变量 I-12）；
  · 三套诊断配置**只经 `state.set_*` 写**（不变量 I-04：按 Excel 全路径分桶的唯一入口在状态层）。

契约 ID（架构附录 A「DIAG」25 条）：C-200 C-201 C-202 C-203 C-204 C-205 C-206 C-207 C-208
C-209 C-210 C-211 C-212 C-213 C-214 C-215 C-216 C-217 C-218 C-219 C-220 C-221 C-248 C-249
C-302；另 C-189（第 1 步结果框）。不变量：I-04（诊断配置单一来源）、I-11（`include_risky`
默认 True）、I-12（后端文本过 scrub）。

C-218 / C-219 / C-220（级联模式说明窗）**刻意不实现**：三处级联下拉（C-223 / C-224）本轮删除，
说明窗随入口一并退役，`docs/级联模式说明.md` 留作背景资料。

对 `ui/state.py` 的接口假设见模块末尾 `STATE_REQUIREMENTS`。
"""

from PySide6 import QtCore, QtGui, QtWidgets

from dreg_verify import edits as ED
from dreg_verify import inputs_table as IT
from dreg_verify import session as S

from . import contracts as C
from . import dialogs as DLG
from . import names as N
from . import persist as P
from . import terms as T
from . import theme as TH
from . import widgets as W

Qt = QtCore.Qt

__all__ = [
    "DiagnosticsDrawer", "PrefixEditorDialog", "ForceEditorDialog",
    "SupplementEditorDialog", "LegacyImportDialog",
    "LegacyPlan", "make_snapshot", "prefix_impact", "prefix_snapshot", "nets_summary",
    "plan_legacy_import", "apply_legacy_import",
    "SYMPTOM_TARGETS", "STATE_REQUIREMENTS",
]

#: 引擎 `binding.found_in` 里「这根网真带上了层级前缀」的那个键。**机器可读键，不上屏**
#: （界面文案一律走 `inputs_table.FOUND_IN_TEXT` / `terms`）。v1 判「这份映射影响到谁」
#: 认的就是它，不是「输入名在不在映射里」—— 后者是字符串交集，配了前缀但那根网压根没人
#: force 时照样报命中（P-16 的假绿）。
PREFIXED_WIRE = "prefixed-wire"


# ═════════════════════════════ 名字 / 文案（唯一真相源在 names.py / terms.py）═══════════════
#: C4-int：本模块原来的 `PENDING_NAMES`（13 条）/ `PENDING_TERMS`（25 条）已整块搬进
#: `ui/names.py` / `ui/terms.py`；`_nm()` 连同 `_t()` 的 `getattr` 回退一并删掉 ——
#: 有回退在，「搬没搬过去」从代码上看不出来，两份字面量漂开也没人会发现。
def _t(key, **fmt):
    """界面文案：`ui/terms.py` 里的同名常量（没有 = 编码错误，立刻炸）。

    ⚠ 第一个形参叫 `key` 不叫 `name`：好几条文案的占位符本身就是 `{name}`。"""
    try:
        s = getattr(T, key)
    except AttributeError:
        raise KeyError("文案 %s 不在 ui/terms.py 里" % key)
    return s.format(**fmt) if fmt else s


#: 行内原因块「去处理」的跳转目标（`terms.REASON_TARGETS` 里 `diag_` 开头的那些）→ 抽屉里滚到哪。
#: 五条折叠项自己也各有一个键，清单 / 详情 / 导出完成弹层都能直链过来。
SYMPTOM_TARGETS = {
    "diag_cuvunf": N.DIAG_CUVUNF_BOX,
    "diag_risky": N.DIAG_SYM_RISKY,
    "diag_supplement": N.DIAG_SYM_SUPPLEMENT,
    "diag_force": N.DIAG_SYM_FORCE,
    "diag_coverage": N.DIAG_SYM_COVERAGE,
    "diag_legacy": N.DIAG_SYM_LEGACY,
}


# ═════════════════════════ 一、快照与影响面（Qt-free）═════════════════════════
def _models(state):
    try:
        return list(state.models() or [])
    except Exception:                       # noqa: BLE001  没载表 / 还没分析：当空清单
        return []


def _an_of(state, name):
    """当前范围里一个信号的 an；分析不出来 → None（诊断抽屉不因为一个信号炸掉）。

    `want_graph` 一律不给：抽屉只数输入行，多算一张电路图是白付钱（`state.analyze` 的
    docstring 明写 want_graph=False 是「我不需要为此多算一张图」，不是「保证没有图」）。"""
    try:
        return state.analyze(name)
    except Exception:                       # noqa: BLE001
        return None


def _scan_inputs(state):
    """把当前范围扫一遍输入行，一次数三样：

    → `(n_prefix_missing, n_net, n_guess)`
      · n_prefix_missing = Σ 各信号 `inputs_table.input_rows(an)` 里 `needs_prefix` 的行数
        （= 还差几根网没有前缀，喂 `DiagnosticsSnapshot.n_prefix_missing` 与第 3 步结果框）；
      · n_net           = 探针网 + 全部输入网的**去重**根数（第 1 步结果框「共 N 根网」）；
      · n_guess         = 其中 `guessed`（名字是按命名约定猜的）的根数。

    一趟扫完三样，是因为三样都要 `analyze` 全表；`state.analyze` 自己按指纹缓存，
    重复调不会重跑引擎。"""
    n_missing, nets, guessed = 0, set(), set()
    for m in _models(state):
        pnet = str(m.get("probe_net") or m.get("out_net") or "").strip().lower()
        if pnet:
            nets.add(pnet)
        an = _an_of(state, m.get("name"))
        if not an:
            continue
        for r in (IT.input_rows(an) or []):
            base = str(r.get("base") or r.get("name") or "").strip().lower()
            if r.get("needs_prefix"):
                n_missing += 1
            if base:
                nets.add(base)
                if r.get("guessed"):
                    guessed.add(base)
    return (n_missing, len(nets), len(guessed))


def make_snapshot(state):
    """打开抽屉时从 state 取的一份只读快照（架构 §2.7 第一行）。

    抽屉里的每一处编辑都经 `state.set_*` 写回、写完重新取快照——快照自己不是真相源（I-04）。"""
    n_missing = 0
    legacy = {}
    if state is not None:
        n_missing = _scan_inputs(state)[0]
        try:
            # `kind_of` 一定要给：legacy `edits` 段里没有 kind 字段，不查当前表就分不出
            # logic 根还是直连寄存器根 —— 不给的话两格会全算在 logic 上（它的 docstring 明写）。
            legacy = P.legacy_bucket_counts(
                getattr(state, "loaded_path", "") or "",
                kind_of=lambda nm: (state.resolve_root(nm) or {}).get("kind"))
        except Exception:                   # noqa: BLE001  旧桶坏了不连累抽屉
            legacy = {}
    return C.DiagnosticsSnapshot(
        probe_prefixes=dict(getattr(state, "probe_prefixes", {}) or {}),
        force_signals=set(getattr(state, "force_signals", set()) or set()),
        logic_overrides=dict(getattr(state, "logic_overrides", {}) or {}),
        include_risky=bool(getattr(state, "include_risky", True)),
        n_prefix_missing=int(n_missing),
        last_nets=(state.last_export("nets") if state is not None else None),
        legacy_counts=dict(legacy or {}),
    )


def nets_summary(state):
    """第 1 步结果框要的三个数：`(n_sig, n_net, n_guess)`（C-189）。

    `settings["last_export"]` 只记了路径与时间（N5 的结构），分项计数没存——所以这里按**当前表**
    重新数一遍：导出 nets.txt 的内容本来就是当前表的网，这三个数说的是同一件事。"""
    _missing, n_net, n_guess = _scan_inputs(state)
    return (len(_models(state)), n_net, n_guess)


def _mp_of(state, mapping):
    """要报影响面的那份映射（`None` = 用 state 里现存的那份）→ {信号名低: 路径}。"""
    src = mapping if mapping is not None else (getattr(state, "probe_prefixes", {}) or {})
    return {str(k).strip().lower(): v for k, v in (src or {}).items()}


def prefix_snapshot(state, mapping=None):
    """当前每个信号「**探到哪根网、force 哪几条路径**」的指纹（P-16）。

    改探针前缀之前照一张、`state.set_probe_prefixes` 之后再照一张，两张不同的那几个信号
    才是真被影响到的 —— 这是唯一不用猜的判据：`drive` 就是 .sv 里那句 force 的路径，
    它变了 .sv 一定变，它没变 .sv 一定不变。

    为什么不能只看「配了几条映射」或「名字在不在映射里」：R41 那条总根（工具不知道 RTL 真网名，
    全靠命名约定 + cone 猜）在视图层的第二实例 —— 字符串交集当命中，配在一个根本没人用的
    名字上也报「影响 2 个信号」（.sv 前后逐字节相同），配在 `_to_mux` 衔接网上 .sv 真变了
    却一个都不报。"""
    mp = _mp_of(state, mapping)
    out = {}
    for m in _models(state):
        name = str(m.get("name") or "")
        pnet = str(m.get("probe_net") or m.get("out_net") or "").strip().lower()
        an = _an_of(state, name)
        rows = (IT.input_rows(an) or []) if an else []
        out[name.lower()] = (
            mp.get(pnet, ""),                       # 输出探针这一侧：带不带前缀、带哪个
            tuple((str(r.get("base") or ""), str(r.get("drive") or ""),
                   str(r.get("found_in") or ""), bool(r.get("resolved", True)))
                  for r in rows))                   # 输入这一侧：每根网的 force 路径原文
    return out


def prefix_impact(state, mapping=None, before=None):
    """改完探针前缀报影响面：`(共 N 条映射, 影响 M 个信号)`（C-204）。

    `before` = 改之前的 `prefix_snapshot`（`PrefixEditorDialog.on_save` 会照一张）→ 影响面 =
    **重分析前后指纹真变了**的信号数，假绿假阴都没得混（P-16）。

    没给 `before`（比如只是想知道现存这份映射喂到了几个信号）时退回 v1
    `on_set_probe_prefix` 末尾那个口径：一个信号只要 ① 它的**输出探针网**在映射里
    （assert 带层级前缀），或 ② 它的任一**输入**的 `found_in` 是「需要层级前缀的网」
    （force 路径真带上了层级），就算被这份映射影响到。
    ⚠ v1 判的是**引擎给出的 found_in**，不是「输入名在不在映射里」—— 后者是字符串交集，
    配了前缀但那根网压根没人 force 时照样报命中（假绿）。"""
    mp = _mp_of(state, mapping)
    if before is not None:
        after = prefix_snapshot(state, mapping)
        return (len(mp), sum(1 for k, v in after.items() if before.get(k) != v))
    n_sig = 0
    for m in _models(state):
        pnet = str(m.get("probe_net") or m.get("out_net") or "").strip().lower()
        if pnet and pnet in mp:
            n_sig += 1
            continue
        an = _an_of(state, m.get("name"))
        rows = (IT.input_rows(an) or []) if an else []
        if any(str(r.get("found_in") or "") == PREFIXED_WIRE for r in rows):
            n_sig += 1
    return (len(mp), n_sig)


# ═════════════════════ 二、旧版真值表编辑迁移（C-302，Qt-free）═════════════════════
class LegacyPlan(object):
    """一次迁移的计划书：迁什么、不迁什么、为什么（`rows` 在前、`skipped` 点名，C-270 的形状）。

    · `rows`    = [(Topout 名, 迁几列, 一句说明)]
    · `skipped` = [(名字, 原因)]
    · `keys`    = {Topout 名: legacy 桶里的键}（执行时按它回去取行；`rows` 的元组形状是给界面看的）
    """

    __slots__ = ("rows", "skipped", "keys")

    def __init__(self, rows=None, skipped=None, keys=None):
        self.rows = list(rows or [])
        self.skipped = list(skipped or [])
        self.keys = dict(keys or {})

    def __bool__(self):
        return bool(self.rows)

    __nonzero__ = __bool__

    @property
    def n_rows(self):
        return len(self.rows)

    @property
    def n_cols(self):
        return sum(int(r[1] or 0) for r in self.rows)

    def names(self):
        return [r[0] for r in self.rows]


def _legacy_mux_names(bucket):
    """旧桶里属于 mux 的名字（六个 mux_* 段的并集）——这些**点名不迁**。"""
    out = set()
    for seg in ("mux_expected", "mux_data", "mux_dropped", "mux_user_vecs"):
        out |= set((bucket.get(seg) or {}).keys())
    for seg in ("mux_neg", "mux_cleared"):
        out |= set(bucket.get(seg) or ())
    return out


def legacy_bucket(state):
    """这张表的 legacy 九段（**只读**；C-235 说的「不读不删」是指自动恢复流程不碰它）。"""
    try:
        return P.load_legacy_bucket(getattr(state, "loaded_path", "") or "") or {}
    except Exception:                       # noqa: BLE001
        return {}


def plan_legacy_import(state, bucket=None):
    """旧版桶 → 迁移计划（只看、不改任何东西）。C-302。

    只迁 `kind ∈ {logic, register}`：这两种的键空间与 v2 列模型**完全相同**（都按物理基名），
    `base_values` 直接就是 `vals`（删除安全审计 §3.2-D 的实证）。mux 不行——它按 `c:A` / `d:0`
    这种 case / 数据列坐标存，与旧版按物理基名存的取值对不上，所以**点名列出 + 写清原因**。

    旧桶按【源 out_name】键、v2 按【Topout 顶层名】键（dft 改名根两者不同名），中间那一跳
    只有引擎知道怎么走 —— 走 `state.resolve_root`（视图不直接 import topout，I-19）。"""
    b = bucket if bucket is not None else legacy_bucket(state)
    plan = LegacyPlan()
    for nm in sorted(_legacy_mux_names(b)):
        plan.skipped.append((str(nm), _t("DIAG_LEGACY_SKIP_MUX")))
    for key, rows_json in sorted((b.get("edits") or {}).items()):
        root = None
        try:
            root = state.resolve_root(key)
        except Exception:                   # noqa: BLE001
            root = None
        if not root:
            plan.skipped.append((str(key), _t("DIAG_LEGACY_SKIP_UNKNOWN")))
            continue
        top = str(root.get("name") or key)
        kind = str(root.get("kind") or "")
        if kind == "mux":
            plan.skipped.append((top, _t("DIAG_LEGACY_SKIP_MUX")))
            continue
        if kind not in ("logic", "register"):
            plan.skipped.append((top, _t("DIAG_LEGACY_SKIP_KIND_FMT", kind=kind or "?")))
            continue
        rows = ED.deserialize_rows(rows_json)
        if not rows:
            plan.skipped.append((top, _t("DIAG_LEGACY_SKIP_EMPTY")))
            continue
        note = _t("DIAG_LEGACY_NOTE_LOST") if any(r.get("note") for r in rows) else ""
        plan.rows.append((top, len(rows), note))
        plan.keys[top] = str(key)
    return plan


def _cols_spec_from_rows(rows, an):
    """legacy 行模型 → v2 列 spec（`edits.restore_cols` 吃的那份）。

    字段对应见删除安全审计 §3.2-D 的迁移表：`base_values→vals`、`kind=="neg"→neg`、
    负向的 `wrong_value` / 正向的 `designer_expected` 都落 `exp`、`name`、`user_added→user`。
    `auto` / `auto_w` 填占位 —— `restore_cols` 对 `editable=="logic"`（logic 与直连寄存器根都是）
    会 `recompute_col_an` **权威重算**，改表后不会留陈旧的 auto（C-238 同一条护栏）。

    ⚠ `exp` 用 `is not None` 判空（C-301）：`wrong_value: 0` / `designer_expected: 0` 是
    合法的「错值 = 0」/「期望 = 0」，真值判断会把它们静默丢掉。"""
    ow = int((an or {}).get("out_width") or 1)
    specs = []
    for i, rd in enumerate(rows or []):
        neg = str(rd.get("kind") or "pos") == "neg"
        exp = rd.get("wrong_value") if neg else rd.get("designer_expected")
        try:
            vals = {str(k): int(v) for k, v in (rd.get("base_values") or {}).items()}
        except (TypeError, ValueError):
            continue                        # 手改坏的数值逐条跳过，整个迁移不崩（C-240 同口径）
        specs.append({
            "name": str(rd.get("name") or "T%d" % i),
            "neg": neg,
            "vals": vals,
            "exp": None if exp is None else int(exp),
            "auto": 0, "auto_w": ow,
            "user": bool(rd.get("user_added")),
            "dft": False,
            "case_index": None,             # logic / register 恒 null
        })
    return specs


def apply_legacy_import(state, plan, bucket=None):
    """按计划执行迁移 → `(迁了几个信号, 迁了几列)`。C-302。

    **legacy 段只读不删**：本函数一个字节都不往旧段写；v2 自己的 `view_edits` 由
    `state.put_edit` 落盘，`persist.write_edits_bucket` 的桶合并会原样保住旧段（C-300 / I-05）。
    整批包在 `state.suspend_persist()` 里：一次显式动作只写一次盘（C-244）。"""
    b = bucket if bucket is not None else legacy_bucket(state)
    legacy_edits = b.get("edits") or {}
    n_sig = n_col = 0
    with state.suspend_persist():
        for name, _n, _note in list(plan.rows):
            key = plan.keys.get(name, name)
            rows = ED.deserialize_rows(legacy_edits.get(key) or [])
            an = _an_of(state, name)
            if not an or not an.get("editable"):
                continue
            cols = ED.restore_cols(an, _cols_spec_from_rows(rows, an))
            if not cols:
                continue
            state.put_edit(name, {"kind": an["kind"], "src_out_name": an["src_out_name"],
                                  "name": an["name"], "renamed": bool(an.get("renamed", False)),
                                  "cols": cols, "an": an})
            n_sig += 1
            n_col += len(cols)
    return (n_sig, n_col)


# ═════════════════════════════ 三、小零件（Qt）═════════════════════════════
def _label(text, parent=None, object_name=None, font=None, color=None, wrap=True, mono=False):
    lb = QtWidgets.QLabel(str(text or ""), parent)
    if object_name:
        lb.setObjectName(object_name)
    lb.setWordWrap(bool(wrap))
    lb.setFont(font or (W.mono_font(TH.FS_MONO) if mono else W.ui_font(TH.FS_UI)))
    if color:
        lb.setStyleSheet("color:%s;" % color)
    lb.setTextInteractionFlags(Qt.TextSelectableByMouse)
    return lb


def _result_box(text, parent=None, object_name=None):
    """每步下面那个灰色结果框（Design 的 stepResult）。"""
    lb = _label(text, parent, object_name, font=W.mono_font(TH.FS_MONO), color=TH.TEXT)
    lb.setStyleSheet("QLabel{background:%s;border:1px solid %s;color:%s;padding:6px 8px;}"
                     % (TH.HINT_BG, TH.BORDER_LIGHT, TH.TEXT))
    return lb


def _button(text, parent=None, object_name=None, primary=False):
    b = QtWidgets.QPushButton(str(text), parent)
    if object_name:
        b.setObjectName(object_name)
    b.setCursor(Qt.PointingHandCursor)
    b.setFont(W.ui_font(TH.FS_UI))
    if primary:
        b.setStyleSheet("QPushButton{background:%s;color:%s;border:none;padding:6px 14px;}"
                        % (TH.BLUE, TH.WHITE))
    else:
        b.setStyleSheet("QPushButton{background:%s;color:%s;border:1px solid %s;padding:6px 12px;}"
                        % (TH.WHITE, TH.BLUE_DARK, TH.BORDER))
    return b


def _mono_edit(parent=None, object_name=None, text="", placeholder=""):
    e = QtWidgets.QPlainTextEdit(parent)
    if object_name:
        e.setObjectName(object_name)
    e.setFont(W.mono_font(TH.FS_MONO))
    e.setPlainText(str(text or ""))         # ⚠ 非 ASCII / 换行一律走 setPlainText（PySide6 6.11）
    if placeholder:
        e.setPlaceholderText(str(placeholder))
    e.setLineWrapMode(QtWidgets.QPlainTextEdit.NoWrap)
    return e


def _ask_open(parent, title, filt):
    """打开文件框（测试里由 `ui_harness.auto_dialogs` 拦下；本模块不自己判 pytest）。"""
    path, _f = QtWidgets.QFileDialog.getOpenFileName(parent, title, "", filt)
    return path or ""


def _ask_save(parent, title, suggest, filt):
    path, _f = QtWidgets.QFileDialog.getSaveFileName(parent, title, suggest, filt)
    return path or ""


class _Collapsible(QtWidgets.QFrame):
    """「其它症状」的一条折叠项：标题行（可点收放）+ 提示 + 内容区。

    objectName 挂在**整条**上（`names.DIAG_SYM_*`），`open_for` 直接按它找、展开、滚过去。"""

    def __init__(self, title, hint, object_name, parent=None):
        super().__init__(parent)
        self.setObjectName(object_name)
        self.setFrameShape(QtWidgets.QFrame.NoFrame)
        self.setStyleSheet("QFrame#%s{background:%s;border:1px solid %s;}"
                           % (object_name, TH.WHITE, TH.BORDER_LIGHT))
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.setSpacing(6)
        self.head = QtWidgets.QToolButton(self)
        self.head.setText(str(title))
        self.head.setCheckable(True)
        self.head.setCursor(Qt.PointingHandCursor)
        self.head.setFont(W.ui_font(TH.FS_UI, bold=True))
        self.head.setToolButtonStyle(Qt.ToolButtonTextOnly)
        self.head.setStyleSheet("QToolButton{border:none;text-align:left;color:%s;}" % TH.INK)
        self.head.toggled.connect(self._on_toggled)
        lay.addWidget(self.head)
        self.hint = _label(hint, self, color=TH.MUTE, font=W.ui_font(TH.FS_UI_SMALL))
        lay.addWidget(self.hint)
        self.body = QtWidgets.QWidget(self)
        self._body_lay = QtWidgets.QVBoxLayout(self.body)
        self._body_lay.setContentsMargins(0, 4, 0, 0)
        self._body_lay.setSpacing(6)
        lay.addWidget(self.body)
        self.body.hide()

    def content_layout(self):
        return self._body_lay

    def set_hint(self, text):
        self.hint.setText(str(text or ""))

    def expand(self):
        self.head.setChecked(True)

    def is_expanded(self):
        return self.body.isVisible()

    def _on_toggled(self, on):
        self.body.setVisible(bool(on))


# ═════════════════════════════ 四、三个编辑器 + 迁移窗 ═════════════════════════════
class _EditorDialog(QtWidgets.QDialog):
    """三个编辑器的共同骨架：提示 + 等宽编辑框 + 一行按钮 + 一行错误。"""

    def __init__(self, state, parent=None, object_name=None, title="", width=720, height=520):
        super().__init__(parent)
        if object_name:
            self.setObjectName(object_name)
        self.setWindowTitle(str(title))
        self.state = state
        self.resize(int(width), int(height))
        self._lay = QtWidgets.QVBoxLayout(self)
        self._lay.setContentsMargins(16, 14, 16, 14)
        self._lay.setSpacing(8)

    def _add_buttons(self, extra=(), save_name=None):
        row = QtWidgets.QHBoxLayout()
        row.setSpacing(8)
        for b in extra:
            row.addWidget(b)
        row.addStretch(1)
        self.btn_cancel = _button(_t("DIAG_CANCEL"), self)
        self.btn_cancel.clicked.connect(self.reject)
        row.addWidget(self.btn_cancel)
        self.btn_save = _button(_t("DIAG_SAVE"), self, save_name, primary=True)
        self.btn_save.clicked.connect(self.on_save)
        row.addWidget(self.btn_save)
        self._lay.addLayout(row)

    def _add_errors(self, object_name):
        self.errors = _label("", self, object_name, font=W.mono_font(TH.FS_MONO), color=TH.RED_DARK)
        self.errors.setStyleSheet("QLabel{background:%s;border:1px solid %s;color:%s;padding:6px 8px;}"
                                  % (TH.BAD_BG, TH.AMBER_BORDER, TH.RED_DARK))
        self.errors.hide()
        self._lay.addWidget(self.errors)

    def show_errors(self, lines, head=""):
        """逐条列错（后端来的串先过 scrub，I-12）。空 = 收起。"""
        items = [T.scrub(str(x)) for x in (lines or []) if str(x).strip()]
        if not items:
            self.errors.setText("")
            self.errors.hide()
            return ""
        body = "\n".join("· %s" % x for x in items)
        text = ("%s\n%s" % (head, body)) if head else body
        self.errors.setText(text)
        self.errors.show()
        return text

    def on_save(self):
        raise NotImplementedError


class PrefixEditorDialog(_EditorDialog):
    """探针前缀映射编辑器（C-200 / C-201 / C-202 / C-203 / C-204 / C-205 / C-248）。

    文本格式一个字节都不在这里拼：解析 `session.parse_probe_prefix_text`、渲染
    `session.render_probe_prefix_grouped`（经 `session.render_probe_prefix_text`）、
    导入合并 `session.merge_probe_prefix_text`、写出 `session.write_mapping_text` ——
    红区 scan_rtl 产出的 probe_prefixes.txt 直接能导（C-248 靠这条）。"""

    saved = QtCore.Signal(int, int)         # (共几条映射, 影响几个信号)

    def __init__(self, state, parent=None):
        super().__init__(state, parent, N.DIAG_PREFIX_DIALOG, _t("DIAG_PREFIX_TITLE"), 720, 540)
        self._lay.addWidget(_label(_t("DIAG_PREFIX_HINT"), self, N.DIAG_PREFIX_HINT,
                                   color=TH.MUTE, font=W.ui_font(TH.FS_UI_SMALL)))
        cur = dict(getattr(state, "probe_prefixes", {}) or {})
        self.edit = _mono_edit(self, N.DIAG_PREFIX_TEXT, S.render_probe_prefix_text(cur))
        self._lay.addWidget(self.edit, 1)
        self.impact = _result_box(_t("DIAG_PREFIX_IMPACT_FMT", n=len(cur), m=0),
                                  self, N.DIAG_PREFIX_IMPACT)
        self._lay.addWidget(self.impact)
        self._add_errors(N.DIAG_PREFIX_ERRORS)
        b_imp = _button(_t("DIAG_PREFIX_IMPORT"), self, N.DIAG_PREFIX_IMPORT_BTN)
        b_imp.clicked.connect(self.on_import)
        b_exp = _button(_t("DIAG_PREFIX_EXPORT_SHORT"), self, N.DIAG_PREFIX_EXPORT_BTN)
        b_exp.clicked.connect(self.on_export)
        self._add_buttons((b_imp, b_exp), N.DIAG_PREFIX_SAVE_BTN)

    def text(self):
        return self.edit.toPlainText()

    def mapping(self):
        """编辑框文本 → 映射（合并写法 / 扁平写法混用、`#` 注释都在 session 里认，C-201）。"""
        return S.parse_probe_prefix_text(self.text())

    def on_import(self):
        """从 .txt 导入：与编辑框现有内容**合并，同名以导入为准**（C-202）。"""
        path = _ask_open(self, _t("DIAG_PREFIX_TITLE"), "映射文本 (*.txt);;全部文件 (*)")
        if not path:
            return ""
        try:
            new = S.read_text_file(path)
        except OSError as ex:
            self.show_errors([_t("DIAG_FILE_READ_FAIL_FMT", err=T.exc_text(ex, path))])
            return ""
        merged = S.merge_probe_prefix_text(self.text(), new)
        self.edit.setPlainText(merged)      # ⚠ 不用 type_text：非 ASCII / 换行会崩进程
        self.show_errors([])
        return merged

    def on_export(self):
        """导出成 .txt（换表 / 给同事复用，C-203）——渲染与 v1 同一个 `render_probe_prefix_grouped`。"""
        path = _ask_save(self, _t("DIAG_PREFIX_EXPORT"), "probe_prefixes.txt", "映射文本 (*.txt)")
        if not path:
            return ""
        try:
            S.write_mapping_text(path, S.render_probe_prefix_text(self.mapping()))
        except OSError as ex:
            self.show_errors([_t("DIAG_FILE_WRITE_FAIL_FMT", err=T.exc_text(ex, path))])
            return ""
        self.show_errors([])
        return path

    def on_save(self):
        """保存 → `state.set_probe_prefixes`（I-04 的唯一入口）→ 报影响面（C-204）。

        `state.set_probe_prefixes` 里 `_config_changed` 明写「勾选一个不动」——清单随后重建，
        用户挑了半天的导出集还在（C-205，测试里拿真 state 证明）。

        影响面（C-204）先照一张**改之前**的指纹再套用（P-16）：报出来的那个数 = 重分析前后
        force 路径 / 探针路径真变了的信号数，不是「配了几条」也不是名字的字符串交集。"""
        mapping = self.mapping()
        before = prefix_snapshot(self.state)         # ← 必须在 set_probe_prefixes 之前
        self.state.set_probe_prefixes(mapping)
        n_map, n_sig = prefix_impact(self.state, mapping, before=before)
        self.impact.setText(_t("DIAG_PREFIX_IMPACT_FMT", n=n_map, m=n_sig))
        self.saved.emit(int(n_map), int(n_sig))
        self.accept()
        return (n_map, n_sig)


class ForceEditorDialog(_EditorDialog):
    """强制 force 信号名单编辑器（C-206 / C-207 / C-208 / C-209）。

    行尾 `#` 注释、留空 = 清除都在 `session.parse_force_signal_text` 里（C-207）；
    导入 / 导出同一份 `.txt` 格式（C-208，与探针前缀同款）。名单存进 `state.set_force_signals`
    之后，Topout 视图与页本地视图的 `state.analyze` / `.sv` 都真正吃到（C-209——`providers`
    的 `_fo()` 把它喂给 `Resolver(force_overrides=)`）。"""

    saved = QtCore.Signal(int)

    def __init__(self, state, parent=None):
        super().__init__(state, parent, N.DIAG_FORCE_DIALOG, _t("DIAG_FORCE_TITLE"), 640, 480)
        self._lay.addWidget(_label(_t("DIAG_FORCE_HINT"), self, N.DIAG_FORCE_HINT,
                                   color=TH.MUTE, font=W.ui_font(TH.FS_UI_SMALL)))
        cur = set(getattr(state, "force_signals", set()) or set())
        self.edit = _mono_edit(self, N.DIAG_FORCE_TEXT, S.render_force_signal_text(cur))
        self._lay.addWidget(self.edit, 1)
        self._add_errors(N.DIAG_FORCE_ERRORS)
        b_imp = _button(_t("DIAG_FORCE_IMPORT"), self, N.DIAG_FORCE_IMPORT_BTN)
        b_imp.clicked.connect(self.on_import)
        b_exp = _button(_t("DIAG_FORCE_EXPORT"), self, N.DIAG_FORCE_EXPORT_BTN)
        b_exp.clicked.connect(self.on_export)
        self._add_buttons((b_imp, b_exp), N.DIAG_FORCE_SAVE_BTN)

    def text(self):
        return self.edit.toPlainText()

    def names(self):
        return S.parse_force_signal_text(self.text())

    def on_import(self):
        path = _ask_open(self, _t("DIAG_FORCE_TITLE"), "名单文本 (*.txt);;全部文件 (*)")
        if not path:
            return ""
        try:
            new = S.read_text_file(path)
        except OSError as ex:
            self.show_errors([_t("DIAG_FILE_READ_FAIL_FMT", err=T.exc_text(ex, path))])
            return ""
        merged = S.render_force_signal_text(
            S.parse_force_signal_text(self.text()) | S.parse_force_signal_text(new))
        self.edit.setPlainText(merged)
        self.show_errors([])
        return merged

    def on_export(self):
        path = _ask_save(self, _t("DIAG_FORCE_TITLE"), "force_signals.txt", "名单文本 (*.txt)")
        if not path:
            return ""
        try:
            S.write_mapping_text(path, S.render_force_signal_text(self.names()))
        except OSError as ex:
            self.show_errors([_t("DIAG_FILE_WRITE_FAIL_FMT", err=T.exc_text(ex, path))])
            return ""
        self.show_errors([])
        return path

    def on_save(self):
        names = self.names()
        self.state.set_force_signals(names)
        self.saved.emit(len(names))
        self.accept()
        return len(names)


class SupplementEditorDialog(_EditorDialog):
    """RTL 补充逻辑编辑器（C-210 / C-211 / C-212 / C-213 / C-214 / C-249）。

    JSON 格式 `{基名: {enabled, expr, inputs:[{var,raw}], note}}` 不变（C-249）——渲染 / 解析 /
    合并模板全走 `session.render_supplements_json` / `parse_supplements_json` /
    `merge_supplements_text`，六道校验走 `session.validate_supplements`（C-213：不过不保存、
    逐条列错到 `DIAG_SUPP_ERRORS`）。"""

    saved = QtCore.Signal(int)

    def __init__(self, state, parent=None):
        super().__init__(state, parent, N.DIAG_SUPP_DIALOG, _t("DIAG_SUPP_TITLE"), 760, 600)
        self._lay.addWidget(_label(_t("DIAG_SUPP_HINT"), self, N.DIAG_SUPP_HINT,
                                   color=TH.MUTE, font=W.ui_font(TH.FS_UI_SMALL)))
        cur = {k: dict(v) for k, v in (getattr(state, "logic_overrides", {}) or {}).items()}
        self.edit = _mono_edit(self, N.DIAG_SUPP_TEXT, S.render_supplements_json(cur))
        self._lay.addWidget(self.edit, 1)
        self.unknown = _label("", self, N.DIAG_SUPP_UNKNOWN,
                              font=W.mono_font(TH.FS_MONO), color=TH.AMBER_FG)
        self.unknown.setStyleSheet("QLabel{background:%s;border:1px solid %s;color:%s;padding:6px 8px;}"
                                   % (TH.AMBER_BG, TH.AMBER_BORDER, TH.AMBER_FG))
        self.unknown.hide()
        self._lay.addWidget(self.unknown)
        self._add_errors(N.DIAG_SUPP_ERRORS)
        b_tmpl = _button(_t("DIAG_SUPP_TEMPLATE"), self, N.DIAG_SUPP_TEMPLATE_BTN)
        b_tmpl.clicked.connect(self.on_template)
        b_imp = _button(_t("DIAG_SUPP_IMPORT"), self, N.DIAG_SUPP_IMPORT_BTN)
        b_imp.clicked.connect(self.on_import)
        self._add_buttons((b_tmpl, b_imp), N.DIAG_SUPP_SAVE_BTN)

    def text(self):
        return self.edit.toPlainText()

    def _current_sig(self):
        """当前信号的源对象（模板要拿它的原表达式与原输入映射）。拿不到 → 通用空模板。"""
        name = str(getattr(self.state, "current_name", "") or "")
        if not name:
            return None
        an = _an_of(self.state, name)
        return (an or {}).get("sig")

    def on_template(self):
        """插入模板（当前信号）：预填原表达式与原输入映射（C-211）。"""
        text, ok = S.merge_supplements_text(self.text(), S.supplement_template(self._current_sig()))
        if not ok:
            self.show_errors([_t("DIAG_SUPP_TMPL_BAD_JSON")])
            return ""
        self.edit.setPlainText(text)
        self.show_errors([])
        return text

    def on_import(self):
        """从 .json 导入（C-212）——整份替换编辑框内容，校验在保存时统一做。"""
        path = _ask_open(self, _t("DIAG_SUPP_TITLE"), "JSON (*.json);;全部文件 (*)")
        if not path:
            return ""
        try:
            text = S.read_text_file(path)
        except OSError as ex:
            self.show_errors([_t("DIAG_FILE_READ_FAIL_FMT", err=T.exc_text(ex, path))])
            return ""
        self.edit.setPlainText(text)
        self.show_errors([])
        return text

    def unknown_names(self, norm):
        """补充的基名不在当前 Excel **logic 页**的那些（C-214：允许，但要问一声）。

        判据与 v1 `on_logic_overrides` 末尾一字不差：`{s.out_base.lower() for s in wb.logic}`。
        以前拿的是**当前范围的清单**（Topout 范围下那是顶层名，dft 改名根还与基名根本不同名），
        于是一条表里明明有的基名也会被报成「将作为纯新增合成信号生成」，用户以为自己写错了，
        或者反过来：换到别的范围再存同一份补充，提示就没了（P-19）。

        只做属性访问，不 import excel_model / topout（I-19 分层）；wb 还没载 → 全当不认识。"""
        have = set()
        wb = getattr(self.state, "wb", None)
        for sig in (getattr(wb, "logic", None) or ()):
            v = str(getattr(sig, "out_base", "") or "").strip().lower()
            if v:
                have.add(v)
        return [n for n in sorted(norm or {}) if n not in have]

    def on_save(self):
        """六道校验不过 **不保存**、逐条列错（C-213）；过了才 `state.set_logic_overrides`。"""
        txt = self.text().strip()
        if not txt:                          # 空文本 = 清空补充（与 v1 同语义）
            self.state.set_logic_overrides({})
            self.unknown.hide()
            self.show_errors([])
            self.saved.emit(0)
            self.accept()
            return 0
        try:
            data = S.parse_supplements_json(txt)
        except ValueError as ex:
            # R3-03：`json` 的英文异常原文（`Expecting value: line 1 column 3 …`）不上屏
            self.show_errors([_t("DIAG_SUPP_BAD_JSON_FMT", err=T.json_pos_text(ex))],
                             _t("DIAG_SUPP_INVALID_HEAD"))
            return None
        norm, errs = S.validate_supplements(data)
        if errs:
            self.show_errors(errs, _t("DIAG_SUPP_INVALID_HEAD"))
            return None
        unknown = self.unknown_names(norm)
        if unknown:
            self.unknown.setText("\n".join(_t("DIAG_SUPP_UNKNOWN_FMT", name=n) for n in unknown))
            self.unknown.show()
        else:
            self.unknown.hide()
        self.show_errors([])
        self.state.set_logic_overrides(norm)
        self.saved.emit(len(norm))
        self.accept()
        return len(norm)


class LegacyImportDialog(QtWidgets.QDialog):
    """从旧版真值表编辑迁进来（C-302）：先给计划书，点「开始迁移」才动。

    版面按 C-270 的形状：**不迁的名字 + 原因在前**，能迁的清单与计数在后。"""

    imported = QtCore.Signal(int, int)      # (迁了几个信号, 迁了几列)

    def __init__(self, state, parent=None):
        super().__init__(parent)
        self.setObjectName(N.DIAG_LEGACY_DIALOG)
        self.setWindowTitle(_t("DIAG_LEGACY_TITLE"))
        self.state = state
        self.resize(760, 520)
        self.plan = plan_legacy_import(state)
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(8)
        self.skipped = _label("", self, N.DIAG_LEGACY_SKIPPED,
                              font=W.mono_font(TH.FS_MONO), color=TH.AMBER_FG)
        self.skipped.setStyleSheet("QLabel{background:%s;border:1px solid %s;color:%s;padding:8px 10px;}"
                                   % (TH.AMBER_BG, TH.AMBER_BORDER, TH.AMBER_FG))
        lay.addWidget(self.skipped)
        self.preview = _label("", self, N.DIAG_LEGACY_PREVIEW,
                              font=W.mono_font(TH.FS_MONO), color=TH.TEXT)
        self.preview.setStyleSheet("QLabel{background:%s;border:1px solid %s;color:%s;padding:8px 10px;}"
                                   % (TH.HINT_BG, TH.BORDER_LIGHT, TH.TEXT))
        self.preview.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        lay.addWidget(self.preview, 1)
        row = QtWidgets.QHBoxLayout()
        row.addStretch(1)
        self.btn_close = _button(_t("DIAG_CANCEL"), self)
        self.btn_close.clicked.connect(self.reject)
        row.addWidget(self.btn_close)
        self.btn_run = _button(_t("DIAG_LEGACY_RUN"), self, N.DIAG_LEGACY_RUN_BTN, primary=True)
        self.btn_run.clicked.connect(self.on_run)
        row.addWidget(self.btn_run)
        lay.addLayout(row)
        self.refresh()

    def refresh(self):
        mux_names = [n for n, why in self.plan.skipped if why == _t("DIAG_LEGACY_SKIP_MUX")]
        body = [_t("DIAG_LEGACY_PREVIEW_FMT", n_ok=self.plan.n_rows)]
        if mux_names:                      # R3-09：一个 mux 都不跳过时，这两截整句不出
            body.append(_t("DIAG_LEGACY_PREVIEW_MUX_FMT", n_mux=len(mux_names),
                           mux_names="、".join(mux_names)))
        body.append("")
        if self.plan.rows:
            for name, n, note in self.plan.rows:
                body.append(_t("DIAG_LEGACY_ROW_FMT", name=name, n=n))
                if note:
                    body.append("　└ %s" % note)
        else:
            body.append(_t("DIAG_LEGACY_NONE"))
        self.preview.setText("\n".join(body))
        if self.plan.skipped:
            lines = [_t("DIAG_LEGACY_SKIP_HEAD")]
            for name, why in self.plan.skipped:
                lines.append(str(name))
                lines.append("　└ %s" % T.scrub(str(why)))
            self.skipped.setText("\n".join(lines))
            self.skipped.show()
        else:
            self.skipped.setText("")
            self.skipped.hide()
        self.btn_run.setEnabled(bool(self.plan.rows))

    def on_run(self):
        n_sig, n_col = apply_legacy_import(self.state, self.plan)
        self.imported.emit(int(n_sig), int(n_col))
        self.accept()
        return (n_sig, n_col)


# ═════════════════════════════ 五、抽屉本体 ═════════════════════════════
class DiagnosticsDrawer(QtWidgets.QFrame):
    """⑬ 诊断抽屉（620px，挂在 MainWindow 右侧）。

    构造：`DiagnosticsDrawer(state=None, parent=None)`；`set_state(state)` 后续补。
    打开：`open_for(symptom="")` —— symptom 取 `SYMPTOM_TARGETS` 的键（= `terms.REASON_TARGETS`
    里 `diag_` 开头那些），滚到对应项并展开；关：`close_drawer()`。

    对外只发三个信号，自己不碰导出中心也不碰覆盖度弹层（C4-int 接线）：
      · `exportNetsRequested()` —— 第 1 步：打开导出中心并预勾 nets 行（不在抽屉里重做一套导出）
      · `coverageRequested()`   —— 折叠项 ③：打开覆盖度弹层
      · `statusMessage(str)`    —— 逐操作反馈进状态栏（C-269）
    """

    exportNetsRequested = QtCore.Signal()
    coverageRequested = QtCore.Signal()
    statusMessage = QtCore.Signal(str)
    closed = QtCore.Signal()

    def __init__(self, state=None, parent=None):
        super().__init__(parent)
        self.setObjectName(N.DIAG_DRAWER)
        self.setFrameShape(QtWidgets.QFrame.NoFrame)
        self.setFixedWidth(int(TH.DIAG_W))
        self.setAutoFillBackground(True)
        self.setStyleSheet("QFrame#%s{background:%s;border-left:1px solid %s;}"
                           % (N.DIAG_DRAWER, TH.WHITE, TH.BORDER))
        shadow = QtWidgets.QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(28)
        shadow.setXOffset(-8)
        shadow.setYOffset(0)
        shadow.setColor(QtGui.QColor(24, 30, 38, 60))
        self.setGraphicsEffect(shadow)

        self.state = None
        self.snapshot = None
        self._dialogs = []                  # 打开过的编辑器（测试要拿到；也免得被 GC）

        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addLayout(self._build_header())
        self.scroll = QtWidgets.QScrollArea(self)
        self.scroll.setObjectName(N.DIAG_SCROLL)
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        self.scroll.setStyleSheet("QScrollArea,QScrollArea>QWidget>QWidget{background:%s;}" % TH.WHITE)
        body = QtWidgets.QWidget(self.scroll)
        self.body = QtWidgets.QVBoxLayout(body)
        self.body.setContentsMargins(18, 14, 18, 18)
        self.body.setSpacing(12)
        self._build_body()
        self.body.addStretch(1)
        self.scroll.setWidget(body)
        outer.addWidget(self.scroll, 1)

        self.hide()
        if state is not None:
            self.set_state(state)

    # ── 版面 ──
    def _build_header(self):
        row = QtWidgets.QHBoxLayout()
        row.setContentsMargins(18, 14, 12, 10)
        row.setSpacing(8)
        self.title = _label(_t("DIAG_TITLE"), self, N.DIAG_TITLE,
                            font=W.ui_font(TH.FS_UI_TITLE, bold=True), color=TH.INK, wrap=False)
        row.addWidget(self.title, 1)
        self.btn_close = QtWidgets.QToolButton(self)
        self.btn_close.setObjectName(N.DIAG_BTN_CLOSE)
        self.btn_close.setText(_t("DIAG_CLOSE"))
        self.btn_close.setCursor(Qt.PointingHandCursor)
        self.btn_close.setFont(W.ui_font(TH.FS_UI))
        self.btn_close.setStyleSheet("QToolButton{border:none;color:%s;}" % TH.BLUE_DARK)
        self.btn_close.clicked.connect(self.close_drawer)
        row.addWidget(self.btn_close, 0)
        return row

    def _build_body(self):
        self.intro = _label(_t("DIAG_INTRO"), self, N.DIAG_INTRO, color=TH.TEXT)
        self.body.addWidget(self.intro)
        self.body.addWidget(self._build_cuvunf())
        for w in self._build_other():
            self.body.addWidget(w)
        self.footer = _label(_t("DIAG_FOOTER"), self, N.DIAG_FOOTER,
                             color=TH.MUTE, font=W.ui_font(TH.FS_UI_SMALL))
        self.body.addWidget(self.footer)

    def _build_cuvunf(self):
        """主症状蓝框 + 三步（C-221）。"""
        box = QtWidgets.QFrame(self)
        box.setObjectName(N.DIAG_CUVUNF_BOX)
        box.setStyleSheet("QFrame#%s{background:%s;border:1px solid %s;}"
                          % (N.DIAG_CUVUNF_BOX, TH.LIGHT_BLUE_BG, TH.LIGHT_BLUE_BORDER))
        lay = QtWidgets.QVBoxLayout(box)
        lay.setContentsMargins(14, 12, 14, 14)
        lay.setSpacing(8)
        lay.addWidget(_label(_t("DIAG_CUVUNF_TITLE"), box, N.DIAG_CUVUNF_TITLE,
                             font=W.ui_font(TH.FS_UI, bold=True), color=TH.LIGHT_BLUE_FG))
        lay.addWidget(_label(_t("DIAG_CUVUNF_BODY"), box, N.DIAG_CUVUNF_BODY, color=TH.TEXT))

        steps = T.DIAG_STEPS
        self.step_boxes, self.step_btns = {}, {}
        title_names = (N.DIAG_STEP1_TITLE, N.DIAG_STEP2_TITLE, N.DIAG_STEP3_TITLE)
        box_names = (N.DIAG_STEP1_BOX, N.DIAG_STEP2_BOX, N.DIAG_STEP3_BOX)
        btn_names = (N.DIAG_STEP1_BTN, N.DIAG_STEP2_BTN, N.DIAG_STEP3_BTN)
        handlers = (self.on_step1, self.on_step2, self.on_step3)
        for i, (no, title, text, result, btn_text) in enumerate(steps):
            lay.addWidget(_label("%s　%s" % (no, title), box, title_names[i],
                                 font=W.ui_font(TH.FS_UI, bold=True), color=TH.INK))
            lay.addWidget(_label(text, box, color=TH.TEXT, font=W.ui_font(TH.FS_UI_SMALL)))
            rb = _result_box(result, box, box_names[i])
            self.step_boxes[i] = rb
            lay.addWidget(rb)
            b = _button(btn_text, box, btn_names[i], primary=(i == 0))
            b.clicked.connect(handlers[i])
            self.step_btns[i] = b
            lay.addWidget(b, 0, Qt.AlignLeft)

        self.btn_prefix_edit = _button(_t("DIAG_PREFIX_EDIT"), box, N.DIAG_PREFIX_EDIT_BTN)
        self.btn_prefix_edit.clicked.connect(self.open_prefix_editor)
        lay.addWidget(self.btn_prefix_edit, 0, Qt.AlignLeft)
        self.cuvunf_box = box
        return box

    def _build_other(self):
        """其它症状五条折叠项（Design 4 条 + 第 5 条 C-302）。"""
        other = T.DIAG_OTHER
        obj = (N.DIAG_SYM_SUPPLEMENT, N.DIAG_SYM_FORCE, N.DIAG_SYM_COVERAGE,
               N.DIAG_SYM_RISKY, N.DIAG_SYM_LEGACY)
        self.items = {}
        out = []
        for i, (title, hint) in enumerate(other):
            it = _Collapsible(title, hint if "{state}" not in hint else "", obj[i], self)
            self.items[obj[i]] = it
            out.append(it)

        # ① 规格缺了一级逻辑 → RTL 补充逻辑
        b = _button(_t("DIAG_SUPP_OPEN"), self, N.DIAG_SUPP_OPEN_BTN)
        b.clicked.connect(self.open_supplement_editor)
        self.items[N.DIAG_SYM_SUPPLEMENT].content_layout().addWidget(b, 0, Qt.AlignLeft)
        # ② 两个东西撞名 → 强制 force
        b = _button(_t("DIAG_FORCE_OPEN"), self, N.DIAG_FORCE_OPEN_BTN)
        b.clicked.connect(self.open_force_editor)
        self.items[N.DIAG_SYM_FORCE].content_layout().addWidget(b, 0, Qt.AlignLeft)
        # ③ 覆盖度不够 → 发信号，弹层归 C4-int
        b = _button(_t("DIAG_COVERAGE_OPEN"), self, N.DIAG_COVERAGE_BTN)
        b.clicked.connect(self.coverageRequested.emit)
        self.items[N.DIAG_SYM_COVERAGE].content_layout().addWidget(b, 0, Qt.AlignLeft)
        # ④ 缺前缀是否强制生成（默认 True，I-11 / C-217）
        self.risky_chk = QtWidgets.QCheckBox(T.DIAG_OTHER[3][1].format(state=T.DIAG_RISKY_ON), self)
        self.risky_chk.setObjectName(N.DIAG_RISKY_TOGGLE)
        self.risky_chk.setFont(W.ui_font(TH.FS_UI))
        self.risky_chk.setCursor(Qt.PointingHandCursor)
        self.risky_chk.setChecked(True)
        self.risky_chk.toggled.connect(self._on_risky_toggled)
        self.items[N.DIAG_SYM_RISKY].content_layout().addWidget(self.risky_chk, 0, Qt.AlignLeft)
        self.risky_warn = _label(T.DIAG_RISKY_OFF_WARNING, self, color=TH.AMBER_FG,
                                 font=W.ui_font(TH.FS_UI_SMALL))
        self.items[N.DIAG_SYM_RISKY].content_layout().addWidget(self.risky_warn)
        # ⑤ 旧版真值表编辑迁移（C-302）
        b = _button(_t("DIAG_LEGACY_OPEN"), self, N.DIAG_LEGACY_IMPORT_BTN)
        b.clicked.connect(self.open_legacy_import)
        self.items[N.DIAG_SYM_LEGACY].content_layout().addWidget(b, 0, Qt.AlignLeft)
        return out

    # ── state ──
    def set_state(self, state):
        """挂状态层（可重复调；换表 / 改配置后自动刷新）。"""
        old = self.state
        if old is not None:
            for sig in ("configChanged", "workbookChanged", "exportRecorded"):
                s = getattr(old, sig, None)
                if s is not None:
                    try:
                        s.disconnect(self._on_state_changed)
                    except (RuntimeError, TypeError):
                        pass
        self.state = state
        if state is not None:
            for sig in ("configChanged", "workbookChanged", "exportRecorded"):
                s = getattr(state, sig, None)
                if s is not None:
                    s.connect(self._on_state_changed)
        self.refresh()

    def _on_state_changed(self, *_a):
        if self.isVisible():
            self.refresh()

    def refresh(self):
        """重新取快照并把三个结果框 / 开关 / 折叠项提示刷一遍。"""
        self.snapshot = make_snapshot(self.state) if self.state is not None else None
        snap = self.snapshot
        # 步 1：上次导出到哪 + 三个数；从没导出过 → DIAG_STEP1_NEVER（C-189）
        last = getattr(snap, "last_nets", None) if snap else None
        if last is not None:
            n_sig, n_net, n_guess = nets_summary(self.state)
            self.step_boxes[0].setText(T.DIAG_STEPS[0][3].format(
                path=getattr(last, "path", ""), n_sig=n_sig, n_net=n_net, n_guess=n_guess))
        else:
            self.step_boxes[0].setText(T.DIAG_STEP1_NEVER)
        # 步 2：那行命令（Design 指定的用户动作，照抄不改）
        self.step_boxes[1].setText(T.DIAG_STEPS[1][3])
        # 步 3：当前映射条数 + 还差几根网
        n_map = len(getattr(snap, "probe_prefixes", {}) or {}) if snap else 0
        n_missing = int(getattr(snap, "n_prefix_missing", 0) or 0) if snap else 0
        self.step_boxes[2].setText(T.DIAG_STEPS[2][3].format(n_map=n_map, n_missing=n_missing))
        # ④ 开关：默认 True（I-11）
        on = bool(getattr(snap, "include_risky", True)) if snap else True
        self.risky_chk.blockSignals(True)
        self.risky_chk.setChecked(on)
        self.risky_chk.blockSignals(False)
        self._sync_risky_text(on)
        # ⑤ 旧版桶有多少东西
        counts = dict(getattr(snap, "legacy_counts", {}) or {}) if snap else {}
        n_ok = int(counts.get("edits") or 0)
        n_mux = int(counts.get("mux") or 0)
        self.items[N.DIAG_SYM_LEGACY].set_hint(
            T.DIAG_OTHER[4][1] if (n_ok or n_mux) else T.DIAG_LEGACY_NONE)
        self.items[N.DIAG_SYM_LEGACY].findChild(
            QtWidgets.QPushButton, N.DIAG_LEGACY_IMPORT_BTN).setEnabled(bool(n_ok or n_mux))

    def _sync_risky_text(self, on):
        state_word = T.DIAG_RISKY_ON if on else T.DIAG_RISKY_OFF
        self.risky_chk.setText(T.DIAG_OTHER[3][1].format(state=state_word))
        self.risky_warn.setVisible(not on)

    # ── 打开 / 收起 ──
    def open_for(self, symptom=""):
        """打开抽屉；`symptom` 命中 `SYMPTOM_TARGETS` 就展开对应项并滚过去。

        清单行内原因块的「去诊断 · …」按钮、详情「解析明细」末尾三步、导出完成弹层的
        「去处理这 N 个信号」都走这一个入口（`terms.REASON_TARGETS` 的键即 symptom）。"""
        self.show()
        self.raise_()
        self.refresh()
        target = SYMPTOM_TARGETS.get(str(symptom or ""))
        if not target:
            self.scroll.verticalScrollBar().setValue(0)
            return ""
        item = self.items.get(target)
        if item is not None:
            item.expand()
        w = self.findChild(QtWidgets.QWidget, target)
        if w is not None:
            self.scroll.ensureWidgetVisible(w, 0, 40)
        return target

    def close_drawer(self):
        self.hide()
        self.closed.emit()

    # ── 三步 ──
    def on_step1(self):
        """第 1 步：打开导出中心并预勾 nets 行（C-185…C-189 都在导出中心那边，这里只发信号）。"""
        self.exportNetsRequested.emit()

    def on_step2(self):
        """第 2 步：把那行命令复制到剪贴板（C-221）。

        这行命令是 **Design 指定的用户动作**（要在仿真服务器上跑的那条），不是开发者视角的提醒——
        文案红线禁的是仓库路径 / git / 本工具的 CLI，这条不在其内（裁决⑪已把措辞定死）。"""
        cmd = T.DIAG_STEPS[1][3]
        cb = QtWidgets.QApplication.clipboard()
        if cb is not None:
            cb.setText(cmd)
        self.statusMessage.emit(T.STATUS_COPIED)
        return cmd

    def on_step3(self):
        """第 3 步：导入 probe_prefixes.txt（与现有合并，同名以导入为准）→ 存 → 报影响面。"""
        path = _ask_open(self, T.DIAG_STEPS[2][4], "映射文本 (*.txt);;全部文件 (*)")
        if not path:
            return None
        try:
            text = S.read_text_file(path)
        except OSError as ex:
            self.statusMessage.emit(T.scrub(_t("DIAG_FILE_READ_FAIL_FMT", err=T.exc_text(ex, path))))
            return None
        cur = S.render_probe_prefix_text(dict(getattr(self.state, "probe_prefixes", {}) or {}))
        merged = S.merge_probe_prefix_text(cur, text)
        mapping = S.parse_probe_prefix_text(merged)
        self.state.set_probe_prefixes(mapping)
        n_map, n_sig = prefix_impact(self.state, mapping)
        msg = T.DIAG_PREFIX_IMPACT_FMT.format(n=n_map, m=n_sig)
        self.step_boxes[2].setText(msg)
        self.statusMessage.emit(msg)
        return (n_map, n_sig)

    # ── 编辑器 ──
    def _run(self, dlg):
        self._dialogs.append(dlg)
        dlg.exec()
        return dlg

    def open_prefix_editor(self):
        dlg = PrefixEditorDialog(self.state, self)
        dlg.saved.connect(lambda n, m: self._after_prefix(n, m))
        return self._run(dlg)

    def _after_prefix(self, n_map, n_sig):
        msg = T.DIAG_PREFIX_IMPACT_FMT.format(n=n_map, m=n_sig)
        self.step_boxes[2].setText(msg)
        self.statusMessage.emit(msg)
        self.refresh()

    def open_force_editor(self):
        dlg = ForceEditorDialog(self.state, self)
        dlg.saved.connect(lambda n: self.statusMessage.emit(T.DIAG_FORCE_DONE_FMT.format(n=n)))
        return self._run(dlg)

    def open_supplement_editor(self):
        dlg = SupplementEditorDialog(self.state, self)
        dlg.saved.connect(self._after_supplement)
        return self._run(dlg)

    def _after_supplement(self, n):
        if not n:
            self.statusMessage.emit(_t("DIAG_SUPP_CLEARED"))
            return
        spec = dict(getattr(self.state, "logic_overrides", {}) or {})
        n_on = sum(1 for v in spec.values() if v.get("enabled", True) is not False)
        self.statusMessage.emit(_t("DIAG_SUPP_DONE_FMT", n=n, n_on=n_on))

    def open_legacy_import(self):
        dlg = LegacyImportDialog(self.state, self)
        dlg.imported.connect(
            lambda a, b: self.statusMessage.emit(_t("DIAG_LEGACY_DONE_FMT", n_sig=a, n_col=b)))
        out = self._run(dlg)
        self.refresh()
        return out

    # ── ④ 开关 ──
    def _on_risky_toggled(self, on):
        """关掉时先确认一次（改产物是显式动作，C-217）；不确认就不改，开关弹回去。"""
        if not on and not self._confirm_risky_off():
            self.risky_chk.blockSignals(True)
            self.risky_chk.setChecked(True)
            self.risky_chk.blockSignals(False)
            self._sync_risky_text(True)
            return False
        self.state.set_include_risky(bool(on))
        self._sync_risky_text(bool(on))
        return True

    def _confirm_risky_off(self):
        """二次确认（C-217）：`dialogs.ConfirmDialog` 的第四种 kind `risky_off`。

        ⚠ C4-b 当时用的是 `QMessageBox.question`，理由是「现有三个 kind 都不是这件事」。
        C4-int 改口：这四件事的性质是同一件（一句话说清会丢什么 + 默认按钮=否），
        标准问答框的按钮文字不受 `terms` 管、offscreen 下还要 harness 单拦一条
        —— 界面上同一类动作出现两种框，用户与测试都得记两套。"""
        return bool(DLG.ConfirmDialog.ask(DLG.CONFIRM_RISKY_OFF, parent=self))


# ═════════════════════════ 对 state 的接口假设（C4-int 逐条核对）═════════════════════════
#: ★ = 契约 `WorkbenchStateProto` 里没有、本模块另要的
STATE_REQUIREMENTS = (
    ("probe_prefixes / force_signals / logic_overrides / include_risky",
     "四份诊断配置的当前值（快照只读它们，不自己存一份——I-04）"),
    ("set_probe_prefixes / set_force_signals / set_logic_overrides / set_include_risky",
     "四个唯一写入口（按 Excel 全路径分桶 + 作废分析缓存 + **保住勾选**，C-205 靠它）"),
    ("loaded_path", "旧版桶按【已载入】路径取（C-236 同一把钥匙）"),
    ("last_export('nets')", "第 1 步结果框：上次把 nets.txt 导到哪（C-189）"),
    ("models() / analyze(name)", "影响面与「还差几根网没前缀」要扫当前范围全表的输入行"),
    ("current_name", "插入模板（当前信号）要它（C-211）"),
    ("put_edit(name, record) / suspend_persist()",
     "C-302 迁移的落点：整批挂起、结束写一次（C-244）；桶合并保住 legacy 段（C-300）"),
    ("resolve_root(name)",
     "★ additive：旧版桶按【源 out_name】键、v2 按【Topout 顶层名】键，中间这一跳要引擎的 "
     "`topout.resolve_root`——视图不直接 import topout（I-19），所以从 state 借"),
    ("信号 configChanged / workbookChanged / exportRecorded", "有哪条接哪条，缺的静默跳过"),
)
