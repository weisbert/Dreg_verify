# -*- coding: utf-8 -*-
"""contracts.py —— v2 模块间接口契约（Protocol / dataclass / 常量；**不 import PySide6，无行为**）。

每个 Protocol 上的 docstring 引用契约 ID（docs/GUI_v2_能力契约.csv）。实现模块：
  · state.py   实现 WorkbenchStateProto（信号名见 STATE_SIGNALS）
  · worker.py  实现 AnalysisWorkerProto（信号名见 WORKER_SIGNALS）
  · bus.py     实现 HighlightBusProto（信号名见 BUS_SIGNALS）
  · providers.py（Qt-free，C0 波新增）实现 ProviderProto；ConfigSourceProto 由 state 满足
  · truth/model.py 实现 TruthModelProto
  · export_center.py 消费 ExportRowSpec / ExportPlan，产出 ExportRunResult
  · diagnostics.py 消费 DiagnosticsSnapshot
Qt 的 Signal 无法用 Protocol 表达，这里用「信号名 → 参数签名」的常量表约定，测试按名 getattr。
"""

from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any, Callable, Dict, List, Optional, Protocol, Sequence, Tuple

# ═════════ 视图范围 / 快捷键 ═════════
VIEW_IDS = ("topout", "logic", "mux", "dft", "iddq")      # pageviews.PAGES 前加 topout；settings 键 cov_<id>/maxt_<id>
DEFAULT_VIEW_ID = "topout"

#: 主控裁决①：Ctrl+D 保留给复制列；诊断 Ctrl+Shift+D；Ctrl+G 导出中心；Ctrl+P .sv 预览标签；Ctrl+R 导出中心预选报告行
SHORTCUTS = {
    "open": "Ctrl+O",            # C-253
    "load": "Ctrl+L",            # C-254
    "sv_preview": "Ctrl+P",      # C-255
    "export_report": "Ctrl+R",   # C-256
    "export_center": "Ctrl+G",   # C-257
    "diagnostics": "Ctrl+Shift+D",
    "copy_col": "Ctrl+D",        # C-258 / C-109（WidgetShortcut，真值表有焦点时才生效）
    "undo": "Ctrl+Z",            # C-299
    "redo": "Ctrl+Y",
    "paste": "Ctrl+V",           # C-294
}

# ═════════ 信号清单 model：列 / roles ═════════


class ListCol(IntEnum):
    """清单列（源 model 的列序；可见性 / 显示顺序由列设置决定，C-008 / C-046）。"""
    CHECK = 0        # 勾选 C-022（常驻第一列，不进列设置）
    NEG = 1          # 反例 C-023
    NAME = 2         # 信号名带切片 C-009（RTL 补充琥珀圆点作 DecorationRole，C-039）
    STATUS = 3       # 状态 C-014 / C-016（点 → 展开原因子行）
    NTEST = 4        # 用例 C-021
    OWNER = 5        # C-011
    ASSERT_ID = 6    # 断言号 C-010（可选列）
    KIND = 7         # 分类 C-012（可选列）
    FORM = 8         # 逻辑类型 C-013（可选列）
    PREFIX = 9       # 探针前缀 C-017..C-020（可选列）
    EXPR = 10        # 表达式 C-046（可选列，默认隐藏）


LIST_DEFAULT_VISIBLE = (ListCol.CHECK, ListCol.NEG, ListCol.NAME, ListCol.STATUS, ListCol.NTEST, ListCol.OWNER)
LIST_OPTIONAL = (ListCol.ASSERT_ID, ListCol.KIND, ListCol.FORM, ListCol.PREFIX, ListCol.EXPR)
LIST_COL_KEYS = {ListCol.CHECK: "check", ListCol.NEG: "neg", ListCol.NAME: "name", ListCol.STATUS: "status",
                 ListCol.NTEST: "ntest", ListCol.OWNER: "owner", ListCol.ASSERT_ID: "assert_id",
                 ListCol.KIND: "kind", ListCol.FORM: "form", ListCol.PREFIX: "prefix", ListCol.EXPR: "expr"}
#: settings 键：列可见性 {列键: bool} 与列序
SETTINGS_LIST_COLUMNS = "list_columns"
#: settings 键：筛选 / 勾选预设 `{名字: {scope, checks, filters}}`（C-291；形状见 `persist.preset_spec`）
SETTINGS_PRESETS = "presets"

_USER_ROLE = 0x0100     # Qt.UserRole


class ListRole(IntEnum):
    """清单 model 自定义 role（视图 / delegate / 测试只认 role，不认业务 dict）。"""
    NAME = _USER_ROLE + 1          # 裸信号名（查找 / 勾选键）
    STATUS_KEY = _USER_ROLE + 2    # terms.STATUS 的键
    TONE = _USER_ROLE + 3          # ok / warn / bad / note
    ISSUES = _USER_ROLE + 4        # list[str]（已 scrub）
    NOTE = _USER_ROLE + 5          # str（已 scrub）
    REASON = _USER_ROLE + 6        # ReasonBlock 或 None
    MODEL = _USER_ROLE + 7         # 该行的 lite 视图模型 dict（LITE_MODEL_KEYS）
    PENDING = _USER_ROLE + 8       # bool：还在后台分析
    IS_REASON_ROW = _USER_ROLE + 9  # bool：原因子行
    SORT = _USER_ROLE + 10         # 排序键（状态按 tone 序、用例按数值）
    SUPPLEMENT = _USER_ROLE + 11   # bool：用了 RTL 补充逻辑
    PREFIX_TIP = _USER_ROLE + 12   # 探针前缀列悬停文案 C-018
    EXPR = _USER_ROLE + 13         # 表达式原文（搜索用）
    INPUT_NAMES = _USER_ROLE + 14  # list[str] 输入信号名（搜索 C-030 / C-031）


#: 后台 worker 逐信号发出的 lite 视图模型键（topout_view_models 的模型去掉 chain/inputs/tests）
LITE_MODEL_KEYS = ("name", "disp", "owner", "width", "kind", "status", "status_detail", "note", "issues",
                   "matched_name", "n_leaves", "n_vectors", "form", "form_label", "probe_net", "prefix",
                   "assert_id", "expr", "input_names", "supplement", "normalized_note", "out_net")


@dataclass
class ReasonBlock:
    """行内原因块（Design 发明①；C-015 / C-016 / C-127）。由 signal_list 按 terms.REASON_TEMPLATES 拼。"""
    title: str
    body: str
    action_label: str
    action_target: str            # terms.REASON_TARGETS 的键
    rows: Tuple[int, ...] = ()    # 点名的 Excel 行号（copy_rows 用）
    payload: Dict[str, Any] = field(default_factory=dict)


# ═════════ 后台分析 worker（N8）═════════
@dataclass
class AnalysisRequest:
    """一次清单分析请求。fingerprint = (view_id, mode, exhaustive, max_tests, sig_cov, form_cov, 配置版本)
    的哈希，C-265：指纹没变不重建。"""
    view_id: str
    mode: str = "max"
    max_tests: int = 256
    exhaustive: bool = False
    sig_cov: Optional[Dict[str, str]] = None
    form_cov: Optional[Dict[str, str]] = None
    fingerprint: str = ""


#: worker 的 Qt 信号：名 → 参数签名（worker.py 必须按此声明）
WORKER_SIGNALS = {
    "started": "(str view_id, int total)",
    "progress": "(int done, int total, str name)",          # 每信号分析前发；载入态进度条 + 当前信号名
    "signalDone": "(str name, dict lite_model)",            # 每信号分析后发；清单该行从「分析中」升级
    "finished": "(str view_id, list models)",               # 全部完成：完整模型列表（与 topout_view_models 同形）
    "cancelled": "(str view_id, list models_partial)",      # 用户停止：已完成的保留，其余仍「分析中」
    "failed": "(str view_id, str message)",                 # 整表分析异常（已捕获；C-043）
}


class AnalysisWorkerProto(Protocol):
    """C-276：载入 200+ 信号时分析放后台、可停止、增量填充。"""

    def start(self, provider: "ProviderProto", request: AnalysisRequest,
              engine_lock: Any = None, total: int = 0) -> None:
        """开一趟。`engine_lock` = `state.engine_lock`（I-21，worker 在信号边界收放）；
        `total` = 主线程骨架清单的行数，只用来让 `started` 一发出来进度条就有分母。

        ⚠ §2.2 的签名写的是两参 `(provider, request)`，实物（`worker.AnalysisWorker.start`）是
        `(provider, request, engine_lock=None, total=0)` —— C1-int 按**实物**定版：锁必须从外面传，
        worker 不去 state 里摸；两个尾参都有默认值，两参调用照样合法。"""
        ...

    def cancel(self) -> None: ...
    def is_running(self) -> bool: ...


# ═════════ 数据源（provider）与配置源 ═════════
class ConfigSourceProto(Protocol):
    """provider 从这里读诊断配置（state 满足）。三套按 Excel 路径分桶（C-233），include_risky 默认 True（C-217）。"""
    wb: Any
    probe_prefixes: Dict[str, str]
    force_signals: set
    logic_overrides: Dict[str, dict]
    include_risky: bool


class ProviderProto(Protocol):
    """Qt-free 数据源（C0 波 dreg_verify/providers.py；沿用 gui._TopoutProvider / _PageProvider 口径）。

    view_id ∈ VIEW_IDS；Topout 与四页视图差异全在实现里，视图层只认这个接口。"""
    view_id: str
    has_chain: bool
    supports_sig_cov: bool
    kind_label: Dict[str, str]

    def title(self) -> str: ...
    def empty_hint(self) -> str: ...
    def has_page(self) -> bool: ...

    def skeleton_models(self) -> List[dict]:
        """便宜清单（resolve_root 一趟）：name/disp/owner/width/kind/status='pending'，N8 第一趟。"""
        ...

    def view_models(self, mode: str, max_tests: int, exhaustive: bool, sig_cov=None, form_cov=None,
                    progress: Optional[Callable[[int, int, str, Optional[dict]], None]] = None,
                    should_cancel: Optional[Callable[[], bool]] = None, lite: bool = False) -> List[dict]:
        """progress(done, total, name, lite_model_or_None) 每信号一次；should_cancel() True → 提前返回已完成部分；
        lite=True 跳过 report 联表（v2 清单不需要 chain/inputs/tests）。三者都不传 = 旧行为逐字节不变。"""
        ...

    def analyze(self, name: str, mode: str, max_tests: int, exhaustive: bool, mux_data=None,
                want_graph: bool = False) -> Optional[dict]:
        """→ analysis_norm 的 an（want_graph=True 时 an['graph'] 为 sigflow.Graph）。"""
        ...

    def render_sv(self, only, mode, max_tests, exhaustive, edited, comments=True, sv_summary=False,
                  owner_in_msg=False, scope="all", sig_cov=None, form_cov=None,
                  block_suffix="") -> Tuple[str, dict]: ...   # 默认与 providers.py / 旧 gui provider 一致（预览路径靠它）
    def render_report(self, mode, max_tests, exhaustive, only=None, sig_cov=None, form_cov=None) -> dict: ...
    def fortest(self, src, out, mode, max_tests, exhaustive, only=None): ...


# ═════════ 会话状态 ═════════
#: state.py 的 Qt 信号：名 → 参数签名
STATE_SIGNALS = {
    "workbookChanged": "()",                     # 换表 / 重载后（wb、providers、models 全重建）
    "loadFailed": "(str message)",               # C-005 / C-272：工程师语言，不弹窗
    "scopeChanged": "(str view_id)",             # 范围切换 C-038
    "modelsChanged": "(str view_id)",            # 该范围清单整体重建（worker finished/cancelled）
    "modelUpdated": "(str view_id, str name)",   # 单行升级（worker signalDone）
    "currentChanged": "(str name)",              # 当前信号（"" = 无）
    "checksChanged": "(str view_id)",            # 勾选集变化 C-022
    "negsChanged": "(str view_id)",              # 反例勾选变化 C-023
    "editsChanged": "(str view_id, str name)",   # 某信号真值表编辑变化（name="" = 批量）
    "coverageChanged": "(str view_id)",          # 覆盖度三层任一层变化 C-148
    "configChanged": "(str which)",              # probe_prefixes / force_signals / logic_overrides / include_risky
    "settingsChanged": "()",                     # 界面偏好写盘后
    "statusMessage": "(str text)",               # 状态栏 statusLeft 逐操作反馈 C-269
    "exportRecorded": "(str kind)",              # last_export 更新 N5
}


class WorkbenchStateProto(Protocol):
    """会话状态对象（app 组合根持有一个；视图只读它、只经它写）。

    持久化护栏：C-227 宽容读 / C-236 按已载入路径分桶 / C-242-243 勾选持久化 / C-244 批量挂起 /
    C-245 单点档不存盘 / C-246 pytest 不落盘 / C-300 桶合并写 / C-301 保住 0。"""
    # ── 表与范围 ──
    excel_path: str          # 顶栏路径框里的路径（可能还没载）
    loaded_path: str         # 已载入的路径（编辑分桶键，C-236）
    wb: Any                  # excel_model.Workbook 或 None
    scope: str               # 当前 view_id
    current_name: str        # 当前信号（"" = 无）

    def load(self, path: str) -> bool: ...
    def set_scope(self, view_id: str) -> None: ...
    def provider(self, view_id: Optional[str] = None) -> ProviderProto: ...
    def page_available(self, view_id: str) -> bool: ...
    # ── 清单模型 ──
    def models(self, view_id: Optional[str] = None) -> List[dict]: ...
    def model_of(self, name: str, view_id: Optional[str] = None) -> Optional[dict]: ...
    def set_models(self, view_id: str, models: List[dict], partial: bool = False) -> None: ...
    def update_model(self, view_id: str, model: dict) -> None: ...
    def fingerprint(self, view_id: Optional[str] = None) -> str: ...
    def needs_analysis(self, view_id: Optional[str] = None) -> bool:
        """这个范围要不要（重新）跑清单：没跑过 或 指纹变了（C-265：切页 / 改完又改回来不重跑）。"""
        ...

    def analysis_request(self, view_id: Optional[str] = None) -> AnalysisRequest:
        """→ worker 的入参（覆盖度三层 + 上限 + 指纹已折进去）；app 不自己拼。"""
        ...

    def set_current(self, name: str) -> None: ...
    # ── 勾选 / 反例 ──
    def checked(self, view_id: Optional[str] = None) -> Optional[set]:
        """None = 全勾（默认态，不写桶，C-243）。"""
        ...
    def is_checked(self, name: str, view_id: Optional[str] = None) -> bool: ...
    def checked_names(self, view_id: Optional[str] = None) -> List[str]:
        """当前真正勾着的**原样**信号名（全勾时 = 全部模型名）——导出 / 预览的 only 用它。"""
        ...

    def set_checked(self, names: Sequence[str], on: bool, view_id: Optional[str] = None) -> None: ...
    def negs(self, view_id: Optional[str] = None) -> set: ...
    def has_negatives(self, name: str, view_id: Optional[str] = None) -> bool: ...
    def set_neg(self, name: str, on: bool, view_id: Optional[str] = None) -> Tuple[int, int]: ...
    def protected_negatives(self, names: Sequence[str], view_id: Optional[str] = None) -> List[str]:
        """这批信号里，反例是「自定义命名 / 手填过错值」的那些（C-036 删反例前先点名确认）。

        口径 = `edits.protected_negatives`；清单不 import edits，只问 state。"""
        ...
    # ── 真值表编辑（edits.py 的列模型）──
    def edits(self, view_id: Optional[str] = None) -> Dict[str, dict]: ...
    def edit_of(self, name: str, view_id: Optional[str] = None) -> Optional[dict]: ...
    def put_edit(self, name: str, record: dict, view_id: Optional[str] = None) -> None: ...
    def drop_edit(self, name: str, view_id: Optional[str] = None) -> None: ...
    def mux_data(self, view_id: Optional[str] = None) -> Dict[str, dict]: ...
    def analyze(self, name: str, view_id: Optional[str] = None, want_graph: bool = False) -> Optional[dict]:
        """provider.analyze + 本信号生效档（CoverageState.mode_for）+ mux_data；缓存按 (view, name, 指纹)。

        ⚠ `want_graph=False` 的语义是「**我不需要为此多算一张图**」，不是「保证 an['graph'] 是
        None」：同一个信号、同一份指纹上已经算过带图的那份时，实现可以直接把它还给你（超集）。
        —— 详情区四件里只有电路图要图，不这样就是一次点选跑两遍引擎（C2-int）。"""
        ...
    def compute_edited(self, view_id: Optional[str] = None) -> dict:
        """edits.compute_edited(edits, mux_data) → 喂 exports.render_sv 的 edited。"""
        ...
    def suspend_persist(self):
        """with 上下文：批量操作挂起逐格存盘、退出统一写一次（C-244）。"""
        ...
    # ── 覆盖度 ──
    def coverage(self, view_id: Optional[str] = None): ...   # session.CoverageState

    def coverage_touched(self, view_id: Optional[str] = None) -> None:
        """三层里任一层被改过（C-148）：广播 `coverageChanged` + 让指纹变（清单该重跑了）。
        覆盖度控件改完档**必须**调它——它是「改了档」与「重算」之间唯一的那根线。"""
        ...
    # ── 诊断配置（ConfigSourceProto）──
    probe_prefixes: Dict[str, str]
    force_signals: set
    logic_overrides: Dict[str, dict]
    include_risky: bool

    def set_probe_prefixes(self, mapping: Dict[str, str]) -> Tuple[int, int]: ...
    def set_force_signals(self, names: set) -> int: ...
    def set_logic_overrides(self, spec: Dict[str, dict]) -> None: ...
    def set_include_risky(self, on: bool) -> None: ...
    # ── 持久化 / 偏好 ──
    def settings(self) -> dict: ...
    def save_settings(self, patch: dict) -> None: ...
    def persist_edits(self) -> None: ...
    def recent_excels(self) -> List["RecentExcel"]: ...
    def last_export(self, kind: str) -> Optional["LastExport"]: ...
    def record_export(self, kind: str, path: str) -> None: ...
    # ── 杂 ──
    def status(self, text: str) -> None:
        """状态层自己产的逐操作反馈（C-269）→ `statusMessage`；视图不必替它拼。"""
        ...

    def code_version(self) -> str:
        """工具代码版本（短 HEAD，拿不到给空串）——只进窗口标题，界面不写 git 字样（C-259 / 裁决⑬）。"""
        ...


@dataclass
class RecentExcel:
    """N4 settings["recent_excels"] 一条（上限 theme.RECENT_MAX；last_excel 键仍照写）。"""
    path: str
    ts: str              # ISO 时间
    n_signals: int = 0   # 分析完成时补写


@dataclass
class LastExport:
    """N5 settings["last_export"][kind]。"""
    path: str
    ts: str


# ═════════ 「当前线网」高亮总线 ═════════
BUS_SIGNALS = {
    "netSelected": "(str net, str origin)",     # net="" 清除；origin ∈ BUS_ORIGINS（发起方不重入）
}
BUS_ORIGINS = ("flow", "chain", "inputs", "truth", "list", "none")


class HighlightBusProto(Protocol):
    """C-282：点一根线 → 展开链 / 真值表 / 输入表同名行联动高亮。四个订阅方各自按 net 名（小写、剥位宽）比对。"""
    current_net: str

    def select(self, net: str, origin: str) -> None: ...
    def clear(self, origin: str = "none") -> None: ...


# ═════════ 真值表 model（A5 §5 + 补 mux / 反例 / DFT 拍 / 进度）═════════
class TruthRowKind:
    INPUT = "input"
    AUTO = "auto"
    EXP = "exp"


class TruthColState:
    AUTO = "auto"      # 自动生成 T<n>
    USER = "user"      # 手编列 U<n>/自定义名（列头标记，不占底色，裁决⑮）
    NEG = "neg"        # 反例列
    DFT = "dft"        # iddq 自检拍列


class TruthRole(IntEnum):
    """真值表 model 自定义 role。底色 / 字色 / 字体走 Qt 标准 role（Background/Foreground/Font）。"""
    IS_FALLBACK = _USER_ROLE + 1     # 这格显示的是兜底值（灰斜体）
    ROW_KIND = _USER_ROLE + 2        # TruthRowKind
    COL_STATE = _USER_ROLE + 3       # TruthColState
    CELL_STATE = _USER_ROLE + 4      # theme.CELL_STATES 的键（match/diff/unfilled/neg/dft/readonly/editable）
    DRIVE_TIP = _USER_ROLE + 5       # 该列 force / RF_WRITE 明细（C-063）
    RAW_VALUE = _USER_ROLE + 6       # int 原值
    COL_NAME = _USER_ROLE + 7        # 最终标号（含 _NEG）
    IS_CURRENT_COL = _USER_ROLE + 8  # bool：当前列高亮 C-107


#: TruthModel 的 Qt 信号
TRUTH_SIGNALS = {
    "parseFailed": "(str text)",                 # C-083 数值写法没认出来（已还原）
    "pasteReport": "(str text)",                 # C-294 粘贴结果说明（落几格 / 新增哪些列 / 跳过哪些行及原因）
    "progressChanged": "(int n, int m, int n_diff)",   # C-106 手填进度 + 不一致条数
    "colsChanged": "()",                         # 列集合变了（加 / 删 / 改名 / 反例）→ state.put_edit + .sv 预览重算
    "muxCollision": "(str text)",                # C-113 mux 撞值提示
}


class TruthModelProto(Protocol):
    """C-073..C-141 的 model 面。**唯一写入口 setData（push QUndoStack）；刷新只发 dataChanged，绝不 clear() 重建**。"""
    # 形状
    def load(self, an: dict, cols: List[dict], e_inputs: List[dict]) -> None:
        """换信号 / 重新生成：beginResetModel 一次（唯一允许 reset 的地方）。"""
        ...
    def row_kind(self, r: int) -> str: ...
    def row_label(self, r: int) -> str: ...
    def all_names(self) -> List[str]: ...
    def cols(self) -> List[dict]:
        """当前列模型（edits.py schema）——state.put_edit 用；返回的是快照。"""
        ...
    def editable_kind(self) -> str:
        """"" / "logic" / "mux"（an["editable"]），"" 时全表只读（C-134）。"""
        ...
    # 列操作（都走 begin/endInsert/RemoveColumns + QUndoCommand）
    def append_test_column(self, n: int = 1, src_idx: Optional[int] = None) -> List[str]: ...
    def duplicate_column(self, c: int) -> Tuple[int, str]: ...
    def remove_columns(self, cs: Sequence[int]) -> List[dict]: ...
    def rename_column(self, c: int, new_name: str) -> Tuple[bool, str]: ...
    def add_negatives(self, cs: Sequence[int], all_positive: bool = False) -> Tuple[int, int]: ...
    def del_negatives(self) -> int: ...
    def protected_negatives(self) -> List[dict]: ...
    def fill_expected(self) -> int: ...
    def clear_all(self) -> None: ...
    def regenerate(self, an: dict, e_inputs: List[dict]) -> None: ...
    def set_mux_data_value(self, base_low: str, text: str) -> None:
        """整表 by_base 同步（C-110 / C-112，经 state.mux_data + provider 重分析 + edits.mux_resync_cols）。"""
        ...
    def set_mux_user_data(self, c: int, key: str, text: str) -> bool: ...
    def set_current_col(self, c: int) -> None: ...
    # 数据
    def setData(self, index, value, role=None) -> bool: ...
    def copy_tsv(self, indexes) -> str: ...
    def paste_tsv(self, text: str, r0: int, c0: int) -> Tuple[bool, str]: ...
    def import_expectations(self, path: str) -> Tuple[int, List[str], str]: ...
    def batch_fill(self, cs: Sequence[int], text: str) -> Tuple[int, str]: ...
    def fill_progress(self) -> Tuple[int, int, int]:
        """(手填 n, 正向 m, 不一致 k)。"""
        ...
    def column_drives(self, c: int) -> List[str]: ...
    def cell_state(self, r: int, c: int) -> str: ...
    def col_state(self, c: int) -> str: ...
    def undo_stack(self): ...


# ═════════ 导出中心 ═════════
EXPORT_KINDS = ("sv", "report", "fortest", "nets", "claims", "config")
EXPORT_OBJECT_SCOPES = ("checked", "all")
EXPORT_SV_SCOPES = ("all", "pos", "neg", "split")     # split = 正向+反例分文件（N2）
EXPORT_REPORT_FORMATS = ("html", "csv", "xlsx")


@dataclass
class ExportRowSpec:
    """导出中心一行的输入（表格 → 编排）。"""
    kind: str
    enabled: bool = False
    object_scope: str = "checked"       # checked / all；config 行忽略
    sv_scope: str = "all"               # 仅 sv 行
    options: Dict[str, Any] = field(default_factory=dict)   # sv: comments/sv_summary/owner_in_msg；report: format；nets: purposes/pages
    path: str = ""                      # 用户选的落盘路径（run 前由对话框填）
    last: Optional[LastExport] = None


@dataclass
class ExportPlan:
    """导出前摘要（裁决⑯：先点名再计数）。"""
    rows: List[ExportRowSpec]
    n_signals: int = 0
    will_skip: List[Tuple[str, str]] = field(default_factory=list)   # (信号名, 原因)
    dup_labels: List[Tuple[str, str, str]] = field(default_factory=list)


@dataclass
class ExportRunResult:
    """导出中心跑完一批的结果：outcomes 顺序 = 勾选行顺序；skipped 合并去重（名字在前）。"""
    outcomes: List[Any] = field(default_factory=list)     # exports.ExportOutcome
    skipped: List[Tuple[str, str]] = field(default_factory=list)
    accounted: List[str] = field(default_factory=list)    # C-166 只记录不产断言
    out_dir: str = ""
    errors: List[Tuple[str, str]] = field(default_factory=list)   # (kind, message)


# ═════════ 诊断抽屉 ═════════
@dataclass
class DiagnosticsSnapshot:
    """诊断抽屉打开时从 state 取的一份只读快照（抽屉内的编辑通过 state.set_* 写回）。"""
    probe_prefixes: Dict[str, str]
    force_signals: set
    logic_overrides: Dict[str, dict]
    include_risky: bool
    n_prefix_missing: int = 0            # 还差几根网没有前缀（needs-prefix 输入数）
    last_nets: Optional[LastExport] = None
    legacy_counts: Dict[str, int] = field(default_factory=dict)   # C-302：{"logic":n,"register":n,"mux":n}


# ═════════ C0 后端补齐后 an / chain 的新键（视图按键读，不做字符串推断）═════════
CHAIN_ENTRY_KEYS = ("out", "expr", "subst", "page", "kind")        # N9：page ∈ logic/mux/dft/iddq，kind ∈ logic/mux/gate
AN_EXTRA_KEYS = ("out_net", "status_detail", "dft_gate_skipped")   # 执行计划 §7 #1 / #2 / #5
INPUT_ROW_EXTRA_KEYS = ("needs_prefix", "guessed")                 # N10：inputs_table.input_rows 两个布尔
