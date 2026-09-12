# -*- coding: utf-8 -*-
"""app.py —— v2 组合根 `MainWindow` + `main()`（C1-d 骨架 → 每波集成 agent 接线；C4-int 接完最后两块）。

版面（Design §1.2 区号）—— 十六区**一块不缺**：

    QMainWindow(WIN_MAIN)
      ├ ① 顶栏 TOP_BAR（品牌 · 真表路径 · 浏览 Ctrl+O · 载入/重新载入 Ctrl+L · 诊断 Ctrl+Shift+D · 导出中心 Ctrl+G）
      ├ 错误条 ERROR_BAR（C-005 / C-272：载表出错用工程师语言，**不弹窗不退出**）
      ├ ② 筛选行 FILTER_BAR（`filter_bar.FilterBar`）
      ├ WIN_STACK（QStackedWidget）
      │    ├ ⑭ 空态 EMPTY_PANEL（620px 居中；xlsx 虚线框 / 标题 / 说明 / 两个按钮 / 最近打开）
      │    └ 工作台 WIN_WORKBENCH
      │         └ WIN_SPLIT_MAIN（③ 清单 LIST_PANEL = `signal_list.SignalListPanel` | 详情列）
      │              └ 详情列 = ⑮ 载入态 LOADING_PANEL + ④ 标题栏 `detail_header.DetailHeader`
      │                          （⑯ `coverage.CoverageControl` 内嵌其中）
      │                          + WIN_SPLIT_SIDE（⑤ `main_view.MainView` | ⑨ `side_panel.SidePanel`）
      │                            MainView = 标签条 + MAIN_VIEW 堆叠：
      │                              页 0 = ⑥ `truth.panel.TruthPanel` / ⑦ `sigflow_view.SigflowView`
      │                              页 1 = ⑧ `sv_preview.SvPreview`
      ├ ⑬ 诊断抽屉 `diagnostics.DiagnosticsDrawer`（**覆盖层**：以窗口为父、setGeometry 贴右边缘，
      │    不进任何布局 —— 见 `_build_drawer`）
      └ ⑩ 状态栏 STATUS_BAR（STATUS_LEFT / STATUS_RIGHT / STATUS_AUTOSAVE）

    ⑪ 导出中心 `export_center.ExportCenterDialog` / ⑫ 完成弹层 `ExportDoneDialog` 是**模态框**，
    由 `openExportCenter()` 现起现关（Ctrl+G / Ctrl+R / 抽屉第 1 步 / 空态「导入配置…」四个入口）。

⚠ 载入态**不是** WIN_STACK 的独立一页：C-276 / 场景⑧ 要求「清单立刻可点」，
  所以载入卡片挂在工作台详情列顶部（架构 §6.1「详情列 = 载入态⑮卡片 + 标题栏④ + WIN_SPLIT_SIDE」）。

一趟载表的数据流（§2.2 的唯一写法，C-265 / C-276）：

    load_path → state.load → workbookChanged
      → start_analysis：provider.skeleton_models() → state.set_models(vid, 骨架, partial=True)
        —— 清单**立刻**出 N 行「分析中」且可点、可勾、可排序；
      → 指纹没变（state.needs_analysis 为假）就到此为止，一个引擎调用都不起（切页不重跑）；
      → 起 worker：progress/signalDone 逐行 update_model（清单只 dataChanged 那一行，不重建）
        finished/cancelled 收尾 → 载入态收起。

依赖注入（C1 各 agent 并行的关键，集成后仍然保留——测试要能换假件）：
    MainWindow(state=None, worker_factory=None, providers_factory=None)
真实 `ui.state.WorkbenchState` / `ui.worker.AnalysisWorker` **只在默认工厂里惰性 import**。

占位区（见 `PLACEHOLDER_AREAS`）：C3-int 之后**一块不剩**（最后一块 TRUTH_PANEL 已换真件）。
场景路由三个信号（`diagnosticsRequested` / `exportCenterRequested` / `svPreviewRequested`）
在 C4-int 之后**照发不误**：它们是「谁把用户送到这儿」的观测点，接上真件不等于这条线可以省。

「当前信号」这一下的分工（C2-int 定版）：
    ④ 标题栏 / ⑧ .sv 预览 / ⑨ 右栏 自己订阅 `state.currentChanged`（各自 `set_state` 干的）；
    ⑦ 电路图**不认 state**（它只吃 an），由组合根 `_on_current_changed` / `_on_model_updated` 喂。
    组合根的 `currentChanged` 槽**先**连上（见 `__init__` 的顺序注释），它那次
    `analyze(name, want_graph=True)` 先进缓存，后面三件的 `analyze(name)` 直接复用 —— 一次点选
    只跑一遍引擎。
"""

import os
import sys

from PySide6 import QtCore, QtGui, QtWidgets

from . import contracts, dialogs, names, persist, terms, theme
from .bus import HighlightBus
from .detail_header import DetailHeader
from .diagnostics import DiagnosticsDrawer
from .export_center import ExportCenterDialog
from .filter_bar import FilterBar
from .main_view import MainView
from .side_panel import SidePanel
from .sigflow_view import SigflowView
from .signal_list import SignalListPanel, build_reason_block
from .sv_preview import SvPreview
from .truth.panel import TruthPanel
from .widgets import ErrorBar, ProgressBadge, mono_font, ui_font

#: 还没换成真件的区（objectName → 哪个波接手）。测试断言它们都带 property("placeholder")。
#: ⚠ C3-int 起**空了**：最后一块 TRUTH_PANEL 已换成 `ui/truth/panel.TruthPanel`。
#: 常量保留（不删）是因为 `test_ui_app` / `test_ui_c2_integration` / `test_ui_main_view`
#: 都按它断言「窗口里一块占位都不剩」—— 留一个空 dict 比删掉更说明问题。
PLACEHOLDER_AREAS = {}

#: 组合根在**窗口级**绑的快捷键。落点（C4-int 起全是真件，不再是「只发个信号」）：
#:   open Ctrl+O → `on_browse`（C-001/C-253）      load Ctrl+L → `on_load`（C-002/C-254）
#:   sv_preview Ctrl+P → `on_sv_preview`（C-255）   export_report Ctrl+R → 导出中心预选报告行（C-256）
#:   export_center Ctrl+G → ⑪ 导出中心（C-257）     diagnostics Ctrl+Shift+D → ⑬ 诊断抽屉
#: 另有一个不在表里的 Esc（`on_escape`）：先收⑬ 抽屉，没开抽屉才退出独占态（C-280 / C-297）。
#: ⚠ copy_col(Ctrl+D) / undo(Ctrl+Z) / redo(Ctrl+Y) / paste(Ctrl+V) 故意**不在这里**：
#: 它们是真值表网格的**视图级**键位（`truth/view.TruthTableView.keyPressEvent`），
#: 网格有焦点时才生效——绑成 WindowShortcut 的话，在清单里按 Ctrl+D 会去复制真值表的列，
#: 在 .sv 预览页按 Ctrl+V 会往看不见的表里粘贴。C-109 还要求「单元格正在编辑时不触发 Ctrl+D」，
#: 那个条件只有在 `keyPressEvent` 里看得到（`QShortcut` 拿不到 `state() == EditingState`）。
APP_SHORTCUTS = ("open", "load", "sv_preview", "export_report", "export_center", "diagnostics")

#: settings 里记分割尺寸的键（Design §1.1 state 键同名）
SIZE_KEYS = ("listW", "sideW", "truthH", "chainH")
SIZE_DEFAULTS = {"listW": theme.LIST_W, "sideW": theme.SIDE_W,
                 "truthH": theme.TRUTH_H, "chainH": theme.CHAIN_H}

#: 关窗时等后台线程在信号边界停下来的上限（ms）——QThread 还在跑就析构 = 进程级崩溃
CLOSE_WAIT_MS = 3000

#: 状态栏那一行计数的合并窗口（ms）。worker 逐信号升级时多次 `modelUpdated` 只算一次（PERF-1）。
STATUS_COUNTS_COALESCE_MS = 100

fmt_shortcut = names.fmt_shortcut          # 名字已并进 names.py；这两个别名保住既有引用
fmt_recent_row = names.fmt_recent_row


# ═════════════════════════ 默认工厂（惰性 import，测试可整体替换）═════════════
def default_state_factory():
    """真实会话状态。**函数内 import**：测试传 state=FakeState() 时一行 state.py 都不用加载。

    `WorkbenchState` 自己就是 `providers.ConfigSourceProto`（wb / 三套诊断配置 /
    include_risky / engine_lock），载表时按范围建 `TopoutProvider` + 四个 `PageProvider`。

    ⚠ C1-d 这里曾经收一个 `providers_factory` 并在 `TypeError` 时回落裸建 —— 主控裁决删掉：
    `WorkbenchState` 根本不收这个参数，那个 try/except 永远走回落分支，等于一段看着在生效、
    实际从没生效过的代码（「想换数据源」的测试改从 `MainWindow(providers_factory=…)` 走）。"""
    from .state import WorkbenchState
    return WorkbenchState()


def default_worker_factory():
    """真实后台分析 worker（N8）。同样惰性 import。"""
    from .worker import AnalysisWorker
    return AnalysisWorker()


# ═════════════════════════ 小工具 ═════════════════════════
def _connect(obj, signal_name, slot):
    """按名连信号；对象上没有这个信号就跳过（C1 各 agent 并行期的容错，不吞真实异常）。"""
    sig = getattr(obj, signal_name, None)
    if sig is None or not hasattr(sig, "connect"):
        return False
    sig.connect(slot)
    return True


def _btn_label(text, shortcut_key=None):
    """按钮文字 + 快捷键角标（Design ①「浏览… Ctrl+O」）。"""
    if not shortcut_key:
        return text
    return "%s　%s" % (text, contracts.SHORTCUTS[shortcut_key])


#: C4-int：本模块与 `export_center.py` 各有一份逐字相同的 `_fmt_when` → 收成 `terms.fmt_when`。
#: 这里留个别名（「上次导出」列与空态「最近打开」两处调用点不动）。
_fmt_when = terms.fmt_when


def _tone_of(model):
    """清单模型 → 四档色 tone（terms.STATUS 的第二项）。"""
    key = (model.get("status_detail") or "").strip()
    if key not in terms.STATUS:
        key = terms.STATUS_FALLBACK.get(model.get("status") or "", "")
    return terms.STATUS[key][1] if key in terms.STATUS else "note"


# ⚠ C3-int 删掉了 `_placeholder(...)`：四个区全换成真件之后组合根里再没有占位块了。
# （`ui/main_view.py` 自己那份 `placeholder()` 留着：单独起 `MainView` 不注入任何东西时要用。）


# ═════════════════════════════════ MainWindow ═════════════════════════════════
class MainWindow(QtWidgets.QMainWindow):
    """v2 工作台组合根（C-266 / C-267：5 份工具条收成 1 份、6 个顶层 tab 收成 1 个工作台）。"""

    #: 场景路由（本波只发信号 + 状态栏提示；C4 的 diagnostics / export_center 接上去）
    diagnosticsRequested = QtCore.Signal(str)      # symptom（terms.REASON_TARGETS 的键或 ""）
    exportCenterRequested = QtCore.Signal(str)     # preselect（contracts.EXPORT_KINDS 之一或 ""）
    svPreviewRequested = QtCore.Signal()
    analysisStarted = QtCore.Signal(str, int)      # view_id, total（测试观测点）
    analysisEnded = QtCore.Signal(str, bool)       # view_id, 是否正常跑完（False = 被停止/失败）

    def __init__(self, state=None, worker_factory=None, providers_factory=None, parent=None):
        super().__init__(parent)
        self.setObjectName(names.WIN_MAIN)
        self._providers_factory = providers_factory
        self._state = state if state is not None else default_state_factory()
        # I-18 / C-282：「当前线网」总线全窗口**一个**，由组合根持有。
        # C1 还没有订阅方（电路图⑦ / 展开链⑨ / 输入表⑨ / 真值表⑥ 分别是 C2-b / C2-a / C3），
        # 但它是组合根的物件——现在就建好，C2 起各视图只管 `window.bus.netSelected.connect(...)`。
        self.bus = HighlightBus(self)
        self._worker_factory = worker_factory or default_worker_factory
        self._worker = None
        self._retired = []             # 已经作废、但线程还没停下来的旧 worker（见 _retire_worker）
        self._run_id = 0               # 分析「第几趟」：旧趟迟到的信号按它丢掉
        # PERF-1：状态栏那一行计数（`_counts_of` 要扫全表两遍）逐信号算一次就是 O(N²)，
        # 合并成 100 ms 一发。写过状态栏（`set_status`）就把合并中的那一发作废。
        self._counts_timer = QtCore.QTimer(self)
        self._counts_timer.setSingleShot(True)
        self._counts_timer.setInterval(STATUS_COUNTS_COALESCE_MS)
        self._counts_timer.timeout.connect(self._refresh_status_counts)
        self._analysis_vid = ""        # 这一趟 worker 在跑哪个范围（迟到的信号按它对齐）
        self._excel_path = ""
        self._load_error = ""
        self._narrow = None
        self._side_visible = True
        self._flow_full = False            # ⑦ 电路图全屏（C-280）
        self._truth_max = False            # ⑥ 真值表放大（C-297）
        self.export_center = None          # ⑪ 当前那一个导出中心（`openExportCenter` 现起现关）
        self._sizes = self._read_sizes()

        self.setFont(ui_font(theme.FS_UI))
        self._build_ui()
        self._build_shortcuts()
        self._connect_state()
        self._connect_views()
        # ⚠ 详情区四件是**建完窗、接完 state 之后**才挂 state 的（都支持 `set_state`）。
        #   顺序是有意义的：Qt 的 `currentChanged` 按连接先后派发，组合根先连上，
        #   它那一次 `analyze(name, want_graph=True)` 就先跑、先进缓存；随后右栏/标题栏
        #   自己那次 `analyze(name)` 直接命中（`state.analyze` 会复用带图的那份，见其注释）。
        #   反过来（视图先连）就是一次点选跑两遍引擎 —— 211 行的真表上按一下卡两次。
        self._attach_detail_state()
        self._refresh_title()
        self._refresh_recent()
        self._show_empty()
        self.resize(theme.WIN_W, theme.WIN_H)

    # ───────────────────────── 构建 ─────────────────────────
    def _build_ui(self):
        central = QtWidgets.QWidget(self)
        central.setObjectName(names.WIN_WORKBENCH_ROOT)
        root = QtWidgets.QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        root.addWidget(self._build_top_bar())
        self.error_bar = ErrorBar(central)
        root.addWidget(self.error_bar)

        self.filter_bar = FilterBar(self._state, central)          # ② C1-c 的真件
        self.filter_bar.setVisible(False)            # 只在 loaded 时出现
        root.addWidget(self.filter_bar)

        self.stack = QtWidgets.QStackedWidget(central)
        self.stack.setObjectName(names.WIN_STACK)
        self.stack.addWidget(self._build_empty_page())          # index 0
        self.stack.addWidget(self._build_workbench())           # index 1
        root.addWidget(self.stack, 1)

        self.setCentralWidget(central)
        self._build_status_bar()
        self._build_drawer()

    # ⑬ 诊断抽屉（C4-b 的真件）：**不进任何布局**，直接盖在窗口右侧
    def _build_drawer(self):
        """抽屉是覆盖层，不是第三栏。

        进布局的话它一出现就会把右栏（⑨）与主视图挤窄一次、收起时再弹回去 —— 用户按
        Ctrl+Shift+D 只是想看一眼三步流程，不该让底下那张真值表重排。所以它直接以窗口为父、
        用 `setGeometry` 贴在右边缘（`theme.DIAG_W` 620px 定宽），几何只在 `resizeEvent`
        与每次打开时算一次（Design ⑬ 的 box-shadow 由抽屉自己带，视觉上就是浮在上层）。

        ⚠ state 这里**不挂**（传 None）：与详情区五件同一个理由，见 `__init__` 的顺序注释
        —— `set_state` 会订阅 configChanged / workbookChanged，还会立刻取一次快照
        （扫全表输入行）。真正挂 state 在 `_attach_detail_state`。"""
        self.diag_drawer = DiagnosticsDrawer(None, self)
        self.diag_drawer.hide()
        return self.diag_drawer

    def _place_drawer(self):
        """把抽屉贴到中央区的右边缘（顶栏之下、状态栏之上）。"""
        drawer = getattr(self, "diag_drawer", None)
        central = self.centralWidget()
        if drawer is None or central is None:
            return
        top = central.mapTo(self, QtCore.QPoint(0, 0)).y()
        w = drawer.width() or int(theme.DIAG_W)
        drawer.setGeometry(max(0, self.width() - w), top, w, central.height())

    # ① 顶栏
    def _build_top_bar(self):
        bar = QtWidgets.QWidget(self)
        bar.setObjectName(names.TOP_BAR)
        bar.setStyleSheet("QWidget#%s{background:%s;border-bottom:1px solid %s;}"
                          % (names.TOP_BAR, theme.PANEL_BG, theme.BORDER))
        lay = QtWidgets.QHBoxLayout(bar)
        lay.setContentsMargins(12, 7, 12, 7)
        lay.setSpacing(8)

        brand = QtWidgets.QLabel(terms.TOP_BRAND, bar)
        brand.setObjectName(names.TOP_BRAND)
        brand.setFont(ui_font(theme.FS_BRAND, bold=True))
        lay.addWidget(brand)

        sep = QtWidgets.QFrame(bar)
        sep.setFrameShape(QtWidgets.QFrame.VLine)
        sep.setStyleSheet("color:%s;" % theme.BORDER)
        lay.addWidget(sep)

        tag = QtWidgets.QLabel(terms.TOP_TABLE_TAG, bar)
        tag.setObjectName(names.TOP_TABLE_TAG)
        tag.setStyleSheet("color:%s;" % theme.MUTE)
        lay.addWidget(tag)

        self.path_edit = QtWidgets.QLineEdit(bar)
        self.path_edit.setObjectName(names.TOP_EXCEL_PATH)
        self.path_edit.setReadOnly(True)
        self.path_edit.setFont(mono_font(theme.FS_MONO))
        self.path_edit.setText(terms.TOP_PATH_EMPTY)
        self.path_edit.setFixedHeight(theme.BTN_H)
        lay.addWidget(self.path_edit, 1)

        self.browse_btn = self._top_btn(bar, names.TOP_BROWSE_BTN, terms.TOP_BROWSE, "open")
        self.browse_btn.clicked.connect(self.on_browse)
        lay.addWidget(self.browse_btn)

        self.load_btn = self._top_btn(bar, names.TOP_LOAD_BTN, terms.TOP_LOAD, "load", primary=True)
        self.load_btn.clicked.connect(self.on_load)
        lay.addWidget(self.load_btn)

        self.diag_btn = self._top_btn(bar, names.TOP_DIAG_BTN, terms.TOP_DIAG, "diagnostics")
        self.diag_btn.clicked.connect(lambda: self.openDiagnostics(""))
        lay.addWidget(self.diag_btn)

        self.export_btn = self._top_btn(bar, names.TOP_EXPORT_BTN, terms.TOP_EXPORT, "export_center",
                                        accent=True)
        self.export_btn.clicked.connect(lambda: self.openExportCenter(""))
        lay.addWidget(self.export_btn)
        return bar

    def _top_btn(self, parent, object_name, text, shortcut_key, primary=False, accent=False):
        b = QtWidgets.QPushButton(_btn_label(text, shortcut_key), parent)
        b.setObjectName(object_name)
        b.setFixedHeight(theme.BTN_H)
        b.setCursor(QtCore.Qt.PointingHandCursor)
        if primary:
            css = "background:%s;color:%s;border:1px solid %s;font-weight:700;" % (
                theme.BLUE, theme.WHITE, theme.BLUE)
        elif accent:
            css = "background:%s;color:%s;border:1px solid %s;font-weight:700;" % (
                theme.WHITE, theme.BLUE_DARK, theme.BLUE_DARK)
        else:
            css = "background:%s;color:%s;border:1px solid %s;" % (theme.WHITE, theme.TEXT, theme.BORDER)
        b.setStyleSheet("QPushButton{%spadding:2px 10px;}" % css)
        return b

    # ⑭ 空态
    def _build_empty_page(self):
        page = QtWidgets.QWidget(self)
        page.setObjectName(names.EMPTY_PANEL)
        page.setAttribute(QtCore.Qt.WA_StyledBackground, True)
        page.setStyleSheet("QWidget#%s{background:%s;}" % (names.EMPTY_PANEL, theme.WHITE))
        outer = QtWidgets.QVBoxLayout(page)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addStretch(1)

        row = QtWidgets.QHBoxLayout()
        row.addStretch(1)
        card = QtWidgets.QWidget(page)
        card.setObjectName(names.EMPTY_CARD)
        card.setFixedWidth(theme.EMPTY_W)
        lay = QtWidgets.QVBoxLayout(card)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(12)

        icon = QtWidgets.QLabel("xlsx", card)
        icon.setObjectName(names.EMPTY_ICON)
        icon.setFixedSize(88, 88)
        icon.setAlignment(QtCore.Qt.AlignCenter)
        icon.setStyleSheet("border:2px dashed %s;color:%s;border-radius:6px;"
                           % (theme.BORDER, theme.MUTE))
        lay.addWidget(icon, 0, QtCore.Qt.AlignHCenter)

        title = QtWidgets.QLabel(terms.EMPTY_TITLE, card)
        title.setObjectName(names.EMPTY_TITLE)
        title.setFont(ui_font(theme.FS_UI_TITLE, bold=True))
        title.setAlignment(QtCore.Qt.AlignCenter)
        lay.addWidget(title)

        desc = QtWidgets.QLabel(terms.EMPTY_DESC, card)
        desc.setObjectName(names.EMPTY_DESC)
        desc.setWordWrap(True)
        desc.setAlignment(QtCore.Qt.AlignCenter)
        desc.setStyleSheet("color:%s;" % theme.MUTE)
        lay.addWidget(desc)

        btns = QtWidgets.QHBoxLayout()
        btns.addStretch(1)
        self.empty_open_btn = QtWidgets.QPushButton(_btn_label(terms.EMPTY_BTN_OPEN, "open"), card)
        self.empty_open_btn.setObjectName(names.EMPTY_BTN_OPEN)
        self.empty_open_btn.setFixedHeight(theme.BTN_H + 4)
        self.empty_open_btn.setStyleSheet("QPushButton{background:%s;color:%s;border:1px solid %s;"
                                          "font-weight:700;padding:2px 16px;}"
                                          % (theme.BLUE, theme.WHITE, theme.BLUE))
        self.empty_open_btn.clicked.connect(self.on_browse)
        btns.addWidget(self.empty_open_btn)
        self.empty_import_btn = QtWidgets.QPushButton(terms.EMPTY_BTN_IMPORT_CONFIG, card)
        self.empty_import_btn.setObjectName(names.EMPTY_BTN_IMPORT_CONFIG)
        self.empty_import_btn.setFixedHeight(theme.BTN_H + 4)
        self.empty_import_btn.clicked.connect(lambda: self.openExportCenter("config"))
        btns.addWidget(self.empty_import_btn)
        btns.addStretch(1)
        lay.addLayout(btns)

        rtitle = QtWidgets.QLabel(terms.EMPTY_RECENT_TITLE, card)
        rtitle.setObjectName(names.EMPTY_RECENT_TITLE)
        rtitle.setStyleSheet("color:%s;" % theme.MUTE)
        lay.addWidget(rtitle)

        self.recent_list = QtWidgets.QWidget(card)
        self.recent_list.setObjectName(names.EMPTY_RECENT_LIST)
        self._recent_lay = QtWidgets.QVBoxLayout(self.recent_list)
        self._recent_lay.setContentsMargins(0, 0, 0, 0)
        self._recent_lay.setSpacing(4)
        lay.addWidget(self.recent_list)

        self.recent_none = QtWidgets.QLabel(terms.EMPTY_RECENT_NONE, card)
        self.recent_none.setObjectName(names.EMPTY_RECENT_NONE)
        self.recent_none.setStyleSheet("color:%s;" % theme.DISABLED_TEXT)
        lay.addWidget(self.recent_none)

        row.addWidget(card)
        row.addStretch(1)
        outer.addLayout(row)
        outer.addStretch(1)
        return page

    # ⑮ 载入态卡片（挂在工作台详情列顶部，不是 stack 的独立一页）
    def _build_loading_card(self, parent):
        panel = QtWidgets.QWidget(parent)
        panel.setObjectName(names.LOADING_PANEL)
        panel.setStyleSheet("QWidget#%s{background:%s;border-bottom:1px solid %s;}"
                            % (names.LOADING_PANEL, theme.LIGHT_BLUE_BG, theme.LIGHT_BLUE_BORDER))
        outer = QtWidgets.QHBoxLayout(panel)
        outer.setContentsMargins(14, 10, 14, 10)
        outer.addStretch(1)
        card = QtWidgets.QWidget(panel)
        card.setObjectName(names.LOADING_CARD)
        card.setFixedWidth(theme.LOADING_W)
        lay = QtWidgets.QVBoxLayout(card)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)

        self.loading_title = QtWidgets.QLabel(card)
        self.loading_title.setObjectName(names.LOADING_TITLE)
        self.loading_title.setFont(ui_font(theme.FS_UI_TITLE, bold=True))
        self.loading_title.setStyleSheet("color:%s;" % theme.LIGHT_BLUE_FG)
        lay.addWidget(self.loading_title)

        self.loading_bar = ProgressBadge(card, height=theme.LOADING_BAR_H,
                                         color=theme.BLUE, stretch=True)
        self.loading_bar.setObjectName(names.LOADING_BAR)
        lay.addWidget(self.loading_bar)

        self.loading_current = QtWidgets.QLabel(card)
        self.loading_current.setObjectName(names.LOADING_CURRENT)
        self.loading_current.setFont(mono_font(theme.FS_MONO))
        lay.addWidget(self.loading_current)

        self.loading_hint = QtWidgets.QLabel(terms.LOADING_HINT, card)
        self.loading_hint.setObjectName(names.LOADING_HINT)
        self.loading_hint.setWordWrap(True)
        self.loading_hint.setStyleSheet("color:%s;" % theme.MUTE)
        lay.addWidget(self.loading_hint)

        self.loading_stop_btn = QtWidgets.QPushButton(terms.LOADING_BTN_STOP, card)
        self.loading_stop_btn.setObjectName(names.LOADING_BTN_STOP)
        self.loading_stop_btn.setFixedHeight(theme.BTN_H)
        self.loading_stop_btn.clicked.connect(self.on_stop_analysis)
        lay.addWidget(self.loading_stop_btn, 0, QtCore.Qt.AlignLeft)

        outer.addWidget(card)
        outer.addStretch(1)
        panel.setVisible(False)
        return panel

    # 工作台
    def _build_workbench(self):
        wb = QtWidgets.QWidget(self)
        wb.setObjectName(names.WIN_WORKBENCH)
        lay = QtWidgets.QVBoxLayout(wb)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        self.split_main = QtWidgets.QSplitter(QtCore.Qt.Horizontal, wb)
        self.split_main.setObjectName(names.WIN_SPLIT_MAIN)
        self.split_main.setChildrenCollapsible(False)              # C-262：两侧都拖不没
        self.split_main.setHandleWidth(theme.HANDLE_W)

        self.list_panel = SignalListPanel(self._state, self._scope(), self.split_main)   # ③ C1-b 的真件
        self.list_panel.setMinimumWidth(theme.CLAMP_LIST[0])
        self.list_panel.setMaximumWidth(theme.CLAMP_LIST[1])
        self.split_main.addWidget(self.list_panel)

        self.detail_column = QtWidgets.QWidget(self.split_main)
        self.detail_column.setObjectName(names.DETAIL_COLUMN)
        dlay = QtWidgets.QVBoxLayout(self.detail_column)
        dlay.setContentsMargins(0, 0, 0, 0)
        dlay.setSpacing(0)
        self.loading_panel = self._build_loading_card(self.detail_column)
        dlay.addWidget(self.loading_panel)
        self.hdr_bar = self._build_hdr_bar(self.detail_column)
        dlay.addWidget(self.hdr_bar)

        self.split_side = QtWidgets.QSplitter(QtCore.Qt.Horizontal, self.detail_column)
        self.split_side.setObjectName(names.WIN_SPLIT_SIDE)
        self.split_side.setChildrenCollapsible(False)
        self.split_side.setHandleWidth(theme.HANDLE_W)
        self.split_side.addWidget(self._build_main_view(self.split_side))
        self.side_panel = self._build_side_panel(self.split_side)
        self.split_side.addWidget(self.side_panel)
        dlay.addWidget(self.split_side, 1)

        self.split_main.addWidget(self.detail_column)
        self.split_main.setStretchFactor(0, 0)
        self.split_main.setStretchFactor(1, 1)
        lay.addWidget(self.split_main)

        self.split_main.splitterMoved.connect(lambda *_: self._save_sizes())
        self.split_side.splitterMoved.connect(lambda *_: self._save_sizes())
        self._apply_sizes()
        return wb

    # ④ 详情标题栏（C2-c 的真件；⑯ 覆盖度控件内嵌在它里面）
    def _build_hdr_bar(self, parent):
        """`DetailHeader` 自带覆盖度按钮（`hdr_cov_btn`）。

        `window.coverage` 仍然指向**同一个** `CoverageControl` —— 它从占位块右上角搬进了
        标题栏，但 app 里 `self.coverage.set_current/refresh` 那几处调用一个字都不用改
        （弹层本来就锚在自己按钮上、自动 reparent 到窗口，搬家不动它的逻辑）。"""
        self.detail_header = DetailHeader(None, parent)   # state 稍后挂（见 _attach_detail_state）
        self.coverage = self.detail_header.cov
        return self.detail_header

    def _build_main_view(self, parent):
        """⑤⑥⑦⑧ 主视图：标签条 + 堆叠（真值表+电路图 页 / .sv 预览 页）。

        三块现在全是真件（C3-int 换掉最后一块占位）。`MainView` 是架构 §1.2 里唯一允许
        组合别的视图的容器，三块一律**注入**进去。

        ⚠ `TruthPanel` 这里传 `state=None`，到 `_attach_detail_state` 才 `set_state` ——
        与详情区另外四件同一个理由（见 `__init__` 里的顺序注释）：面板的 `set_state` 会
        订阅 `currentChanged`，先订阅就先跑它那次不带图的 `analyze(name)`，组合根随后那次
        `analyze(name, want_graph=True)` 缓存没命中，一次点选跑两遍引擎。"""
        self.truth_panel = TruthPanel(state=None, bus=self.bus)        # ⑥ C3-c 的真件
        self.flow_view = SigflowView(bus=self.bus)                     # ⑦ C2-b 的真件
        self.sv_preview = SvPreview(None)                              # ⑧ C2-c 的真件（state 稍后挂）
        mv = MainView(truth_widget=self.truth_panel, flow_widget=self.flow_view,
                      sv_widget=self.sv_preview, parent=parent)
        self.main_view = mv
        self.split_truth_flow = mv.splitter
        #: 旧属性名（C1 骨架期的占位块）继续指向真件，免得调用方/测试到处改
        self.flow_panel = self.flow_view
        self.sv_panel = self.sv_preview
        mv.splitter.splitterMoved.connect(lambda *_: self._save_sizes())
        return mv

    def _build_side_panel(self, parent):
        """⑨ 右侧常驻栏：逐层展开 / 输入信号 两段（C2-a 的真件）。"""
        self.side_panel = SidePanel(None, self.bus, parent)   # state 稍后挂
        self.split_chain_inputs = self.side_panel.splitter
        self.split_chain_inputs.splitterMoved.connect(lambda *_: self._save_sizes())
        return self.side_panel

    # ⑩ 状态栏
    def _build_status_bar(self):
        sb = QtWidgets.QStatusBar(self)
        sb.setObjectName(names.STATUS_BAR)
        sb.setStyleSheet("QStatusBar{background:%s;}QStatusBar::item{border:0;}" % theme.PANEL_BG)
        f = ui_font(theme.FS_STATUS)
        self.status_left = QtWidgets.QLabel("", sb)
        self.status_left.setObjectName(names.STATUS_LEFT)
        self.status_left.setFont(f)
        sb.addWidget(self.status_left, 1)
        self.status_right = QtWidgets.QLabel("", sb)
        self.status_right.setObjectName(names.STATUS_RIGHT)
        self.status_right.setFont(f)
        sb.addPermanentWidget(self.status_right)
        self.status_autosave = QtWidgets.QLabel("", sb)
        self.status_autosave.setObjectName(names.STATUS_AUTOSAVE)
        self.status_autosave.setFont(f)
        self.status_autosave.setStyleSheet("color:%s;" % theme.MUTE)
        sb.addPermanentWidget(self.status_autosave)
        self.setStatusBar(sb)

    # 快捷键（§9 裁决：Ctrl+D 留给复制列，诊断是 Ctrl+Shift+D）
    def _build_shortcuts(self):
        slots = {
            "open": self.on_browse,
            "load": self.on_load,
            "sv_preview": self.on_sv_preview,
            "export_report": lambda: self.openExportCenter("report"),
            "export_center": lambda: self.openExportCenter(""),
            "diagnostics": lambda: self.openDiagnostics(""),
        }
        self.shortcuts = {}
        for key in APP_SHORTCUTS:
            sc = QtGui.QShortcut(QtGui.QKeySequence(contracts.SHORTCUTS[key]), self)
            sc.setObjectName(fmt_shortcut(key))
            sc.setContext(QtCore.Qt.WindowShortcut)
            sc.activated.connect(slots[key])
            self.shortcuts[key] = sc
        # Esc：先收诊断抽屉⑬，没开抽屉才退出「主视图独占中央区」（电路图全屏 C-280 / 真值表放大 C-297）。
        # 不进 `contracts.SHORTCUTS`：那张表是「顶栏按钮上要印出来的快捷键」，Esc 不印在任何按钮上。
        self.esc_shortcut = QtGui.QShortcut(QtGui.QKeySequence(QtCore.Qt.Key_Escape), self)
        self.esc_shortcut.setObjectName(fmt_shortcut("exit_fullscreen"))
        self.esc_shortcut.setContext(QtCore.Qt.WindowShortcut)
        self.esc_shortcut.activated.connect(self.on_escape)

    # ───────────────────────── state 接线 ─────────────────────────
    def _connect_state(self):
        s = self._state
        _connect(s, "workbookChanged", self._on_workbook_changed)
        _connect(s, "loadFailed", self._on_load_failed)
        _connect(s, "scopeChanged", self._on_scope_changed)
        _connect(s, "modelsChanged", self._on_models_changed)
        _connect(s, "modelUpdated", self._on_model_updated)
        _connect(s, "currentChanged", self._on_current_changed)
        _connect(s, "statusMessage", self.set_status)
        _connect(s, "exportRecorded", self._on_export_recorded)
        _connect(s, "editsChanged", lambda *_: self._touch_autosave())
        _connect(s, "checksChanged", lambda *_: self._touch_autosave())
        _connect(s, "coverageChanged", self._on_recompute_needed)
        _connect(s, "configChanged", lambda *_: self._on_recompute_needed())
        _connect(s, "settingsChanged", lambda *_: None)

    # ───────────────────────── 视图接线（信号 → 槽，唯一一处）─────────────────────────
    def _connect_views(self):
        """三个真件与组合根之间的全部往来。视图只发信号，写 state 的活在这里做（§1.2）。"""
        fb, lp = self.filter_bar, self.list_panel
        fb.scopeChanged.connect(self.on_scope_selected)          # ② → state.set_scope（C-038）
        fb.filterChanged.connect(self._on_filters_changed)       # ② → proxy.set_filters（§2.3）
        fb.statusMessage.connect(self.set_status)                # C-031
        fb.presetSaveRequested.connect(self.save_preset)         # C-291
        fb.presetLoadRequested.connect(self.load_preset)
        fb.pasteNamesRequested.connect(lp.open_paste_names_dialog)   # C-290（入口在②，对话框在③）
        lp.diagRequested.connect(self.on_reason_action)          # ③ 行内原因块按钮 → 场景路由
        lp.statusMessage.connect(self.set_status)                # C-269
        self.coverage.coverageChanged.connect(lambda *_: self.coverage.refresh())

        dh = self.detail_header                                  # ④ C2-c 的真件
        dh.diagRequested.connect(self.openDiagnostics)           # C-066 解析明细末尾的直链
        dh.sideToggled.connect(self.on_side_toggled)             # 右栏开合（记 settings 的 sideW）
        dh.resolveDetailToggled.connect(self.on_resolve_detail_toggled)   # C-064/C-065

        mv = self.main_view                                      # ⑤ C2-c 的真件
        mv.tabChanged.connect(self.on_tab_changed)               # C-255
        mv.flowFullscreenChanged.connect(self.on_flow_fullscreen)        # C-280
        mv.truthMaximizedChanged.connect(self.on_truth_maximized)        # C-297

        tp = self.truth_panel                                    # ⑥ C3-c 的真件
        tp.progressChanged.connect(self._on_truth_progress)      # C-106（标签条 + 标题栏两处）
        tp.maximizeRequested.connect(mv.set_truth_maximized)     # C-297 放大按钮 → 容器
        mv.truthMaximizedChanged.connect(tp.set_maximized)       # C-297 反向（Esc / 电路图全屏）
        tp.statusMessage.connect(self.set_status)                # C-269

        self.flow_view.fullscreenToggled.connect(mv.set_flow_fullscreen)  # ⑦ 按钮 → 容器
        self.flow_view.exported.connect(self._on_flow_exported)           # C-284
        self.sv_preview.statusMessage.connect(self.set_status)            # ⑧ C-172

        dr = self.diag_drawer                                    # ⑬ C4-b 的真件
        dr.exportNetsRequested.connect(lambda: self.openExportCenter("nets"))   # 第 1 步
        dr.coverageRequested.connect(self.open_coverage)         # 折叠项 ③ → ⑯ 覆盖度弹层
        dr.statusMessage.connect(self.set_status)                # C-269
        dr.closed.connect(self._on_drawer_closed)                # 收起后焦点回清单

    def _attach_detail_state(self):
        """把会话状态挂给详情区五件 + ⑬ 抽屉（构造时都传的 `None`，见 `__init__` 里的顺序注释）。"""
        self.detail_header.set_state(self._state)     # 内含覆盖度控件的 set_state
        self.sv_preview.set_state(self._state)
        self.side_panel.set_state(self._state)
        # ⑬ 诊断抽屉：`set_state` 订阅 configChanged / workbookChanged / exportRecorded，
        # 并立刻取一次快照（要扫全表输入行）。挂在这里而不是构造时，同样是为了不抢在组合根
        # 前面订阅 —— 它自己也只在**可见**时才按信号重取快照（`_on_state_changed`）。
        self.diag_drawer.set_state(self._state)
        # ⑥ 真值表：`set_state` 订阅 currentChanged / coverageChanged / configChanged /
        # scopeChanged / workbookChanged 五条，自己按当前信号装表（空态 / 换表 / 切范围都自理）。
        # 组合根只保证这一句在 `_connect_state` **之后**、在任何 `workbookChanged` 之前跑。
        self.truth_panel.set_state(self._state)
        # 电路图**不认 state**（HOST_REQUIREMENTS）：图从 `state.analyze(..., want_graph=True)`
        # 来，由组合根的 `_on_current_changed` 喂给它。
        self.flow_view.set_bus(self.bus)
        self.side_panel.set_bus(self.bus)

    @property
    def state(self):
        return self._state

    @property
    def worker(self):
        return self._worker

    def _scope(self):
        return getattr(self._state, "scope", contracts.DEFAULT_VIEW_ID)

    # ───────────────────────── 载表 ─────────────────────────
    def on_browse(self):
        """C-001 / C-253：浏览并选中 Excel，**选完即载入**（不用再点一次加载）。"""
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, terms.EMPTY_BTN_OPEN, self._excel_path or "", "Excel (*.xlsx *.xlsm);;所有文件 (*)")
        if not path:
            return False
        return self.load_path(path)

    def on_load(self):
        """C-002 / C-254：载入 / 重新载入路径框里的表。"""
        txt = self.path_edit.text().strip()
        if txt == terms.TOP_PATH_EMPTY:
            txt = ""
        path = txt or self._excel_path
        if not path:
            return self.on_browse()
        return self.load_path(path)

    def load_path(self, path):
        """真正的载表入口：state.load(path) → 成功进工作台并起 worker；失败只写错误条（C-005/C-272）。

        ⚠ 第一件事是把上一趟停掉：`state.load` 一执行，上一张表的 models 就被清空了，
        此刻还在跑的旧 worker 每回调一次就往新表的清单里塞一行上一张表的信号。"""
        self._retire_worker()
        path = str(path or "")
        was = str(getattr(self._state, "loaded_path", "") or "")   # R3-12：失败要回滚到它
        self._set_path_text(path)
        self.error_bar.dismiss()
        self._load_error = ""
        ok = False
        try:
            ok = bool(self._state.load(path))
        except Exception as exc:                      # noqa: BLE001  载表异常不许崩窗
            ok = False
            if not self._load_error:
                self._on_load_failed(terms.STATUS_LOAD_FAILED_FMT.format(
                    reason=terms.exc_text(exc, path)))
        if not ok and not self._load_error:
            self._on_load_failed(terms.STATUS_LOAD_FAILED_FMT.format(
                reason=terms.exc_text("OSError", path)))
        if not ok:
            self._rollback_path(was)
        return ok

    def _rollback_path(self, was):
        """R3-12：载表失败 → 路径框回到**真正载着的那张表**，错误条补一句「当前仍是《…》」。

        路径框在 `state.load` 之前就被改成新路径了（要让用户看见工具正在载哪一张）。
        失败后不回滚的话，顶栏写着 A 表、清单 / 真值表 / .sv 预览却还是 B 表 ——
        用户会照着 A 的规格去核对 B 的用例，而屏幕上没有任何一处提示这件事。"""
        self._set_path_text(was)
        if not was:
            return
        cur = os.path.basename(was) or was
        self._load_error = terms.STATUS_LOAD_FAILED_KEPT_FMT.format(
            reason=self._load_error.rstrip("。"), cur=cur)
        self.error_bar.show_error(self._load_error)
        self.set_status(self.error_bar.text())

    def apply_startup(self, argv=()):
        """C-004：命令行带一个 .xlsx → 跳过空态直接载；否则 C-003：按 last_excel 自动载入。"""
        for a in list(argv or ()):
            p = str(a)
            if p.lower().endswith((".xlsx", ".xlsm")) and os.path.exists(p):
                return self.load_path(p)
        last = ""
        try:
            last = str((self._state.settings() or {}).get("last_excel") or "")
        except Exception:                             # noqa: BLE001
            last = ""
        if last and os.path.exists(last):
            return self.load_path(last)
        return False

    def _set_path_text(self, path):
        self._excel_path = str(path or "")
        self.path_edit.setText(self._excel_path or terms.TOP_PATH_EMPTY)
        self.path_edit.setToolTip(self._excel_path)

    def _on_load_failed(self, message):
        """C-005 / C-272 / I-12：错误条（已 scrub），不弹窗、不进 .sv 预览、不退出。"""
        self._load_error = str(message or "")
        self.error_bar.show_error(self._load_error)
        self.set_status(self.error_bar.text())

    def _on_workbook_changed(self):
        self._show_workbench()
        self.load_btn.setText(_btn_label(terms.TOP_RELOAD, "load"))
        self._set_path_text(getattr(self._state, "loaded_path", "") or self._excel_path)
        self._refresh_title()
        self._refresh_recent()
        self.filter_bar.set_scope(self._scope())
        self.filter_bar.reset_filters()               # 换表 = 筛选复位（owner 菜单随 models 重建）
        self.list_panel.set_view_id(self._scope())
        self.coverage.set_current("")
        self._refresh_last_export()
        self.start_analysis()

    def _on_scope_changed(self, view_id=""):
        """范围换了（C-038）：三个视图跟上，清单要不要重跑由 `needs_analysis` 判（C-265）。"""
        vid = str(view_id or self._scope())
        if self._analysis_vid and self._analysis_vid != vid:
            self._retire_worker()                     # 上一趟跑的是别的范围，先收掉
        self.filter_bar.set_scope(vid)
        self.coverage.refresh()
        self._show_flow(getattr(self._state, "current_name", ""))
        self.start_analysis()

    def _on_models_changed(self, view_id=""):
        if view_id not in ("", self._scope()):
            return
        self.filter_bar.rebuild(self._models())       # owner / 分类下拉只列本表真有的（C-027/C-028）
        self._refresh_status_counts()                 # 顺序：先重建（它会发筛选计数）再写载表小结

    def _on_model_updated(self, view_id="", name=""):
        """某一行升级了（worker 逐信号回来）。**当前这一行**升级时电路图要跟着从骨架变真图。

        C-276 的语义在这一句上：清单先出 N 行「分析中」，用户当场点了其中一行 → 电路图
        `set_pending()`；等 worker 把这一条算完，`modelUpdated` 到，这里再喂真图。
        不跟这一步的话，得等整表跑完（或用户再点一次）图才出来 —— 看着就是「点了没反应」。

        ⑥ 真值表同理（C-148 / P-18）：`coverage.mode_for` 要从**清单行**里取该信号的逻辑类型
        （`form`），而改档那一刻推给清单的是骨架（`form` 还是空的）—— ⑥ 听到 `coverageChanged`
        就装表，装出来的是「查不到逻辑类型档、退回全局档」的那一版。以前它能对上纯属侥幸：
        清单整表重建时选中行会跳到第一行，用户再点回来才触发第二次装表；P-18 把选中行按名字
        留住之后，那次「点回来」没有了，真值表就一直停在旧档上（清单说 9 条、真值表画着 25 列）。"""
        if view_id not in ("", self._scope()):
            return
        self._refresh_status_counts_soon()            # PERF-1：逐行升级时把状态栏计数合并成一次
        cur = str(getattr(self._state, "current_name", "") or "")
        if cur and (not name or str(name).lower() == cur.lower()):
            self._show_flow(cur)
            self.truth_panel.refresh()                # 同一句话对 ⑥ 也成立，见下方注释

    def _on_current_changed(self, name=""):
        """C-149 / C-280：切信号 → 覆盖度回显 + 电路图换图。

        ④ 标题栏 / ⑧ .sv 预览 / ⑨ 右栏三件自己订阅了 `currentChanged`（各自的 `set_state` 干的），
        这里只管**不认 state 的那一件**（⑦ 电路图）与组合根自己的活。"""
        self.coverage.set_current(name)               # C-149：只回显本信号的生效档，不重算
        self._relayout_loading(bool(name))
        self._show_flow(name)

    def _show_flow(self, name):
        """⑦ 电路图的唯一喂食口：没选信号 → `clear()`；还在分析 → `set_pending()`；否则喂真 an。"""
        nm = str(name or "")
        if not nm:
            self.flow_view.clear()
            return
        model = {}
        try:
            model = dict(self._state.model_of(nm, self._scope()) or {})
        except Exception:                             # noqa: BLE001
            model = {}
        if str(model.get("status") or "") == "pending":
            self.flow_view.set_pending()
            return
        try:
            an = self._state.analyze(nm, self._scope(), True)
        except Exception:                             # noqa: BLE001  C-043：图画不出来不连累别人
            an = None
        self.flow_view.set_signal(an, nm)

    def _on_export_recorded(self, _kind=""):
        self._refresh_last_export()

    def _on_recompute_needed(self, view_id=""):
        """覆盖度 / 诊断配置变了（C-148 / C-205）：指纹翻篇 → 清单按新档重跑，勾选一个不动。"""
        if view_id in ("", self._scope()):
            self.coverage.refresh()
            self.start_analysis()

    # ───────────────────────── ② 筛选行 ─────────────────────────
    @QtCore.Slot(str)
    def on_scope_selected(self, view_id):
        """用户点了范围段：写 state（视图自己不改 state，§1.2）。"""
        self._state.set_scope(str(view_id))
        hint = self.filter_bar.missing_pages_text()
        if hint:
            self.set_status(hint)                     # C-042：本表少哪几页，写在状态栏
        return self._scope()

    def _on_filters_changed(self, d):
        """② 的一份筛选 → ③ 的 proxy，再把**清单真正数出来的**可见数回写给②（C-031）。

        回写这一步不是多余的：筛选行自己也能按 `match_row` 算一份，但它算的是「status 四档」，
        清单按的是 `status_detail` 八档的 tone —— 两份数会在 `risky-generated` 这类行上差开。
        屏幕上只该有一个数，且必须是「清单里真能看见的那些行」的数（`set_counts` 的注释
        写的就是这个交接）。"""
        self.list_panel.set_filters(**dict(d or {}))
        px = self.list_panel.proxy
        self.filter_bar.set_counts(px.n_visible(), px.n_total(), px.n_by_input())

    def _presets(self):
        """settings 里那一段预设，逐条按定版三键规整（旧的两键文件缺 scope 补空串）。"""
        raw = (self._state.settings() or {}).get(contracts.SETTINGS_PRESETS)
        out = {}
        for nm, spec in (raw if isinstance(raw, dict) else {}).items():
            spec = spec if isinstance(spec, dict) else {}
            out[str(nm)] = persist.preset_spec(spec.get("scope"), spec.get("checks"),
                                               spec.get("filters"))
        return out

    def save_preset(self):
        """C-291 存预设：勾选集由 state 出、筛选由②出，落 settings["presets"]。"""
        presets = self._presets()
        name = dialogs.PresetsDialog.ask_save(sorted(presets), self)
        name = str(name or "").strip()
        if not name:
            return ""
        spec = self.filter_bar.preset_payload()       # {scope, checks=None, filters}
        spec["checks"] = list(self._state.checked_names(self._scope()))
        presets[name] = spec
        self._state.save_settings({contracts.SETTINGS_PRESETS: presets})
        self.filter_bar.rebuild_presets_menu()
        self.set_status("%s · %s" % (terms.PRESETS, name))
        return name

    @QtCore.Slot(str)
    def load_preset(self, name):
        """C-291 取预设：范围 → 筛选 → 勾选，三样一起回到当时的样子。

        名字为空 = 走「管理预设」（选一条取回 / 删掉几条），C2-d 的 `DLG_PRESETS` 一框两用。"""
        presets = self._presets()
        nm = str(name or "")
        if not nm:
            nm, deleted = dialogs.PresetsDialog.ask_manage(sorted(presets), self)
            if deleted:
                for d in deleted:
                    presets.pop(str(d), None)
                self._state.save_settings({contracts.SETTINGS_PRESETS: presets})
                self.filter_bar.rebuild_presets_menu()
            nm = str(nm or "")
            if not nm:
                self.set_status(terms.PRESET_MANAGE)
                return ""
        spec = presets.get(nm)
        if not isinstance(spec, dict):
            return ""
        if spec.get("scope"):
            self._state.set_scope(spec["scope"])
        self.filter_bar.set_filters(spec.get("filters") or {})
        checks = spec.get("checks")
        if checks is not None:
            with self._state.suspend_persist():       # C-244：一次写盘
                self._state.set_checked([], False, self._scope())
                self._state.set_checked(list(checks), True, self._scope())
        self.set_status("%s · %s" % (terms.PRESETS, nm))
        return nm

    # ───────────────────────── ③ 清单 ─────────────────────────
    @QtCore.Slot(str, str)
    def on_reason_action(self, target, signal_name=""):
        """行内原因块的按钮（C-015/C-016/C-127）→ 场景路由，带上这一行的原因全文 / 行号。"""
        return self.route_reason_action(target, self._reason_payload(signal_name))

    def _reason_payload(self, signal_name):
        """copy_rows / copy_detail 要复制的东西：点名的 Excel 行号在前、原因全文在后（I-20）。

        原因块的拼法只有一份（`signal_list.build_reason_block`），这里不另写一套。"""
        name = str(signal_name or "")
        model = self._state.model_of(name, self._scope()) if name else None
        block = build_reason_block(model) if model else None
        if block is None:
            return {"name": name, "text": name}
        # 正文里本来就带着点名的 Excel 行号（模板 `行 {rows}` / issues 原文的「第 N 行」），
        # 这里不再另拼一句——多拼一份就多一处会和界面漂的文案。
        return {"name": name, "rows": block.rows,
                "text": "\n".join(x for x in (name, block.title, block.body) if x)}

    # ───────────────────────── 后台分析（N8 / C-276 / C-265）─────────────────────────
    def start_analysis(self, force=False):
        """骨架先出（清单立刻可点）→ 起 worker 逐信号升级。返回「这一趟真的开跑了没有」。

        ⚠ `needs_analysis` 要在**推骨架之前**问：骨架行是 `status="pending"`，
        而 `set_models(partial=True)` 是按名覆盖——对一个已经分析好的范围推一遍骨架，
        整张清单会倒退回「分析中」，然后因为指纹没变又不重跑，就永远停在那儿了（C-265）。"""
        vid = self._scope()
        try:
            provider = self._state.provider(vid)
        except Exception:                             # noqa: BLE001
            provider = None
        if provider is None:
            return False
        if not force and not self._state.needs_analysis(vid):
            self.coverage.refresh()                   # 切页回来：清单原样留着，只刷一下回显
            self._refresh_status_counts()
            return False
        self._retire_worker()                         # §2.2 规矩 4：同一时刻只跑一个 job
        try:
            skeleton = list(provider.skeleton_models() or [])
        except Exception as exc:                      # noqa: BLE001
            self._on_worker_failed(vid, terms.exc_text(exc))     # R3-03：不贴异常原文
            return False
        self._state.set_models(vid, skeleton, True)
        total = len(skeleton)
        self._analysis_vid = vid
        self._set_loading(True, 0, total, "")
        self._worker = self._worker_factory()
        self._connect_worker(self._worker, self._run_id)
        self.analysisStarted.emit(vid, total)
        self._worker.start(provider, self._state.analysis_request(vid), self._engine_lock(), total)
        return True

    def _engine_lock(self):
        """I-21：worker 与主线程共用同一个 wb，锁从这里传进去（worker 在信号边界收放）。"""
        return getattr(self._state, "engine_lock", None)

    def _connect_worker(self, w, run):
        """接这一趟 worker 的六条信号，每条都带「趟号」闸门。

        为什么光 disconnect 不够：`emit` 发生在 worker 线程，跨线程是**队列连接**——
        信号已经排进主线程事件队列之后再 disconnect，那一条照样会被投递
        （Qt 只保证接收方析构时清掉待投递事件，不保证断连能撤回已排队的）。
        所以真正的闸门是趟号：`_retire_worker` 一加号，旧趟所有迟到的信号当场作废。"""
        def gate(fn):
            def slot(*args):
                if run == self._run_id:
                    fn(*args)
            return slot
        _connect(w, "started", gate(self._on_worker_started))
        _connect(w, "progress", gate(self._on_worker_progress))
        _connect(w, "signalDone", gate(self._on_worker_signal_done))
        _connect(w, "finished", gate(self._on_worker_finished))
        _connect(w, "cancelled", gate(self._on_worker_cancelled))
        _connect(w, "failed", gate(self._on_worker_failed))

    def _retire_worker(self):
        """让上一趟停下来、作废它之后发的一切。

        为什么非作废不可：换表 / 换范围会新建一个 worker，旧 worker 是另一个对象，
        它的 serial 号只挡得住「自己那一趟」的迟到信号，挡不住「上一张表那一趟」。
        放任不管的话，旧趟的 `signalDone` 会带着上一张表的信号名走 `update_model`，
        而那个名字在新清单里没有 —— `update_model` 于是把它**追加**进去：
        新表的清单里凭空多出上一张表的行（C-237 最恨的那类串味，且没有任何报错）。

        断完还要留着引用：QThread 还在跑时对象被 Python 回收 = 进程直接没。
        `_retired` 就是这个用处，跑完自然清掉。"""
        self._run_id += 1                              # ← 真正的闸门（见 _connect_worker）
        w, self._worker = self._worker, None
        if w is None:
            return
        if w.is_running():
            w.cancel()
        for nm in contracts.WORKER_SIGNALS:
            sig = getattr(w, nm, None)
            if sig is None:
                continue
            try:
                sig.disconnect()
            except (RuntimeError, TypeError):          # 本来就没接过：没什么可断的
                pass
        self._retired = [x for x in self._retired if x.is_running()]
        if w.is_running():
            self._retired.append(w)

    def on_stop_analysis(self):
        """⑮「停止分析」：已展开完的保留，其余仍显示「分析中」（C-276）。"""
        if self._worker is not None and self._worker.is_running():
            self._worker.cancel()
            return True
        self._set_loading(False)
        return False

    def _on_worker_started(self, view_id="", total=0):
        self._set_loading(True, 0, int(total or 0), "")

    def _on_worker_progress(self, done, total, name):
        self._set_loading(True, int(done), int(total), str(name or ""))

    def _on_worker_signal_done(self, name, lite_model):
        """逐行升级（C-276）：只 `update_model` 那一行 —— 清单发 dataChanged，不重建整表。"""
        self._state.update_model(self._analysis_vid or self._scope(), dict(lite_model or {}))

    def _on_worker_finished(self, view_id, models):
        self._state.set_models(view_id, list(models or []), False)
        self._set_loading(False)
        self._refresh_status_counts()
        self._refresh_truth_after_analysis(view_id)
        self.analysisEnded.emit(str(view_id), True)

    def _refresh_truth_after_analysis(self, view_id):
        """整表跑完 → 当前信号的真值表按**最终**清单再装一次（C-148 / P-18）。

        与 `_on_model_updated` 里那一句是同一条理由（清单行的 `form` 是覆盖度派发的判据），
        这里兜的是「当前信号那一行这一趟没被 `modelUpdated` 单独报过」的场合。"""
        if view_id not in ("", self._scope()):
            return
        if str(getattr(self._state, "current_name", "") or ""):
            self.truth_panel.refresh()

    def _on_worker_cancelled(self, view_id, models_partial):
        part = list(models_partial or [])
        self._state.set_models(view_id, part, True)
        done, total = self.loading_bar.value()
        self._set_loading(False)
        self.set_status(terms.LOADING_STOPPED_FMT.format(done=len(part) or done, total=total))
        self.analysisEnded.emit(str(view_id), False)

    def _on_worker_failed(self, view_id, message):
        """整表展开这一趟炸了（C-043 / C-272 / R3-13 / C5b-1）。

        三件事一起做，少一件用户就只看见「半张清单」而没人说为什么：
          ① 错误条 + 状态栏写**同一句人话**（`STATUS_ANALYZE_FAILED_FMT`，原因已过
             `terms.exc_text`）—— 以前状态栏整条就是引擎异常的 message；
          ② 详情区那行「分析失败（已捕获，未崩）」点亮 —— C-043 要的就是它，
             而这条路以前一直是空的：`hdr.set_error()` 能用，但真的 worker 失败时没人去调
             （C5-b 把这件事钉成了 strict xfail，本次摘除）。"""
        self._set_loading(False)
        reason = terms.scrub(str(message or "")) or terms.EXC_FALLBACK
        text = terms.STATUS_ANALYZE_FAILED_FMT.format(reason=reason)
        self.error_bar.show_error(text)
        self.set_status(text)
        self.detail_header.set_error("%s\n%s" % (terms.HDR_ANALYSIS_FAILED, reason))
        self.analysisEnded.emit(str(view_id), False)

    def _set_loading(self, on, done=0, total=0, name=""):
        if not on:
            self.loading_panel.setVisible(False)
            return
        self.loading_title.setText(self._loading_title(int(done), int(total)))
        self.loading_bar.set_value(done, total)
        self.loading_current.setText(terms.LOADING_CURRENT_FMT.format(name=name, hint="") if name else "")
        self.loading_panel.setVisible(True)
        self._relayout_loading(bool(getattr(self._state, "current_name", "")))

    def _loading_title(self, done, total):
        """⑮ 的标题按范围说话：Topout 是「展开」（要一路展到源寄存器），子页是「分析本页」。

        不分的话，在 mux 页上会写「正在展开 Topout 信号」——用户看着就是工具在干别的事。"""
        vid = self._analysis_vid or self._scope()
        if vid == contracts.DEFAULT_VIEW_ID:
            return terms.LOADING_TITLE_FMT.format(done=done, total=total)
        return terms.LOADING_TITLE_PAGE_FMT.format(page=vid, done=done, total=total)

    def _relayout_loading(self, compact):
        """有当前信号时把载入卡片压成一行条（架构 §6.1）。"""
        if not hasattr(self, "loading_hint"):
            return
        self.loading_hint.setVisible(not compact)
        self.loading_current.setVisible(not compact)

    # ───────────────────────── 场景路由 ─────────────────────────
    @QtCore.Slot(str)
    def openDiagnostics(self, symptom=""):
        """⑬ 诊断抽屉（C4-b 的真件）：滑出右侧覆盖层并滚到 `symptom` 那一条。

        三条直链都走这一个口（`terms.REASON_TARGETS` 里 `diag_` 开头的键即 symptom）：
        清单行内原因块的「去诊断 · …」、详情「解析明细」末尾的直链、导出完成弹层的
        「去处理这 N 个信号」。Ctrl+Shift+D / 顶栏「诊断」按钮给空串 = 只打开、滚到顶。

        `diagnosticsRequested` 仍然照发：它是「谁把用户送到诊断」的观测点，测试与后续的
        场景路由都盯着它 —— 抽屉接上了不等于这条线可以省掉。"""
        sym = str(symptom or "")
        self.diagnosticsRequested.emit(sym)
        self.set_status(terms.REASON_TARGETS.get(sym) or terms.DIAG_TITLE)
        self._place_drawer()                          # 几何按当前窗口大小算（窗口可能刚改过尺寸）
        self.diag_drawer.open_for(sym)
        return sym

    @QtCore.Slot(str)
    def openExportCenter(self, preselect=""):
        """⑪ 导出中心（C4-a 的真件）：模态起框。preselect ∈ `contracts.EXPORT_KINDS` 时只勾那一行。

        入口共四个：Ctrl+G / 顶栏按钮 `""`（六行按默认勾选）、Ctrl+R `"report"`（C-256）、
        诊断抽屉第 1 步 `"nets"`、空态「导入配置…」`"config"`（C-192：开完直接进导入文件框）。

        四条回信都在这里接（对话框自己不碰别的区，见 `export_center` 模块头）：
          goFixRequested → ⑬ 诊断抽屉；svPreviewRequested → ⑧ .sv 预览标签（C-168）；
          statusMessage → ⑩ 状态栏；exported → 刷新状态栏右侧的「上次导出」（C-197/C-198）。

        ⚠ `exec()` 期间事件循环是嵌套的：`exported` 发生在框还没关的时候，
        `_refresh_last_export` 读的是 `state.last_export`（`run_plan` 已经逐个 `record_export`
        写完了），所以这一刷新拿到的就是刚写出的那份，不必等框关。"""
        pre = str(preselect or "")
        self.exportCenterRequested.emit(pre)
        if pre in contracts.EXPORT_KINDS:
            self.set_status("%s · %s" % (terms.EXPORT_TITLE, terms.EXPORT_ROWS[pre][0]))
        else:
            self.set_status(terms.EXPORT_TITLE)
        # 上一次那个框收掉：它以窗口为父，只把属性指走的话 Qt 那边还留着 ——
        # 按一次 Ctrl+G 攒一个隐藏对话框，一天下来几十个（每个都还订着 state 的信号）。
        old, self.export_center = self.export_center, None
        if old is not None:
            old.setParent(None)
            old.deleteLater()
        dlg = ExportCenterDialog(self._state, self, preselect=pre)
        self.export_center = dlg                      # 测试拿得到；也免得被 GC
        dlg.goFixRequested.connect(self.openDiagnostics)          # C-167 →「去处理这 N 个信号」
        dlg.svPreviewRequested.connect(self.on_sv_preview)        # C-168
        dlg.statusMessage.connect(self.set_status)                # C-269
        dlg.exported.connect(self._on_exported)
        dlg.exec()
        return pre

    def _on_exported(self, _result=None):
        """导出中心跑完一趟：状态栏右侧的「上次导出」当场翻新（C-197 / C-198）。

        `state.exportRecorded` 本来也会走到 `_refresh_last_export`，但那条信号只有在
        `persist.record_last_export` 真写成时才发；这里再刷一次是为了「一趟导了三种产物」
        的那一下——右边应当显示最新的那份，而不是三条信号里最先到的那份。"""
        self._refresh_last_export()

    def open_coverage(self):
        """⑯ 覆盖度弹层的唯一打开口（诊断抽屉折叠项 ③ 「打开覆盖度设置…」的落点）。

        弹层锚在标题栏④ 自己的按钮上 —— 空态（还没载表）时标题栏不可见，弹层也就无处可锚，
        此时只在状态栏说一句，不弹一个飘在角落里的空弹层。"""
        if self.is_empty_state():
            self.set_status(terms.EMPTY_TITLE)
            return False
        self.coverage.open_popover()
        return True

    def _on_drawer_closed(self):
        """⑬ 收起后焦点回③ 清单：抽屉是覆盖层，收起时键盘焦点留在一块看不见的控件上，
        下一次方向键 / 空格就会「按了没反应」。空态时没有清单可回，什么都不做。"""
        if self.is_empty_state():
            return False
        self.list_panel.view.setFocus(QtCore.Qt.OtherFocusReason)
        return True

    def on_sv_preview(self):
        """Ctrl+P（C-255 / 裁决①）：主视图切到 .sv 预览标签。

        `svPreviewRequested` 只由 `on_tab_changed` 发（标签真的换过去了才算），
        否则按一次 Ctrl+P 会发两遍（这里一遍、tabChanged 一遍）。已经在这一页时就地补发一次
        —— 观察点不该因为「已经在了」而消失。"""
        if self.main_view.tab() == "sv":
            self.svPreviewRequested.emit()
            self.sv_preview.refresh()
        else:
            self.main_view.set_tab("sv")
        self.set_status(terms.SV_TITLE_SIGNAL)
        return True

    # ───────────────────────── ④⑤⑥⑦⑧⑨ 详情区联动 ─────────────────────────
    @QtCore.Slot(bool)
    def on_side_toggled(self, visible):
        """④「隐藏右栏 ▶」/「◀ 展开链 · 输入信号」：收起前把当前宽度记进 settings 的 `sideW`。

        不先记就没得恢复：收起之后分割器的右段是 0，再展开只能退回出厂 470，
        用户拖过的宽度白拖了。"""
        on = bool(visible)
        if not on:
            self._save_sizes()
        self._side_visible = on
        self._sync_side_visible()
        if on:
            self._apply_side_width()
        return on

    @QtCore.Slot(bool)
    def on_resolve_detail_toggled(self, open_):
        """④「解析明细」开合 → ⑨ 输入表的驱动行同步带上/去掉来源（C-064 / C-065）。"""
        self.side_panel.set_show_source(bool(open_))
        # R3-11：不写面板名（「解析明细」三个字对着一块刚展开的面板 = 把控件名念了一遍）
        name = str(getattr(self._state, "current_name", "") or "")
        self.set_status(terms.STATUS_RESOLVE_OPENED_FMT.format(name=name)
                        if (open_ and name) else "")
        return bool(open_)

    @QtCore.Slot(str)
    def on_tab_changed(self, key):
        """⑤ 标签换了：切到 .sv 预览时让它把脏内容重算一遍（C-072 / C-132）。"""
        k = str(key or "")
        if k == "sv":
            self.svPreviewRequested.emit()
            self.sv_preview.refresh()
        return k

    @QtCore.Slot(bool)
    def on_flow_fullscreen(self, on):
        """C-280 电路图全屏：主视图接管窗口中央区（清单 / 标题栏 / 右栏都让开），Esc 退出。"""
        on = bool(on)
        self._flow_full = on
        if on:
            self._truth_max = False
            # C-297 两种独占互斥：`MainView.set_flow_fullscreen` 自己把 `_truth_max` 清了，
            # 但它**不发** `truthMaximizedChanged`（那是“真值表放大”这件事的信号）。
            # 不在这里把面板的放大按钮弹回来，退出电路图全屏之后真值表回来了、
            # 按钮却还按着 —— 再点一下变成“取消放大”，用户看到的是按钮反了。
            self.truth_panel.set_maximized(False)
        self.flow_view.set_fullscreen(on)             # 同步按钮文案（值没变不重复 emit）
        self._sync_fullscreen()
        return on

    @QtCore.Slot(int, int, int)
    def _on_truth_progress(self, n, m, k):
        """C-106 手填进度：真值表 model 一处算，**标签条与标题栏两处一起刷**。

        `TruthModel.progressChanged(n, m, k)` = (手填几条, 正向几条, 其中与 auto 不一致几条)；
        列数从 model 现问（`set_truth_counts` 的第一个数是「25 列」那一截，不在信号里）。
        两处都喂同一组数，界面上不会出现「标签说 7/25、标题栏说 6/25」这种自相矛盾。
        """
        self.main_view.set_truth_counts(self.truth_panel.model.columnCount(), n, m)
        self.detail_header.set_progress(n, m, k)

    @QtCore.Slot(bool)
    def on_truth_maximized(self, on):
        """C-297 真值表放大：电路图由 `MainView` 收起，右栏由组合根收起。Esc 退出。"""
        on = bool(on)
        self._truth_max = on
        if on:
            self._flow_full = False
            self.flow_view.set_fullscreen(False)
        self._sync_fullscreen()
        return on

    def is_fullscreen(self):
        """主视图是不是正独占中央区（电路图全屏 或 真值表放大）。"""
        return bool(self._flow_full or self._truth_max)

    def on_escape(self):
        """Esc 的优先级：**先收诊断抽屉⑬，再退出独占态**（电路图全屏 C-280 / 真值表放大 C-297）。

        为什么是这个次序：抽屉是盖在最上面的那一层，用户按 Esc 想关的是「刚打开的那个东西」。
        反过来先退全屏的话，在「电路图全屏 + 抽屉开着」时按一次 Esc，屏幕上抽屉纹丝不动、
        底下的版面却换了一副样子 —— 而那正是用户此刻没在看的地方。

        两件都没有时返回 False（**不吞掉 Esc**）：对话框 / 编辑器自己的 Esc 还要用。"""
        if self.diag_drawer.isVisible():
            self.diag_drawer.close_drawer()
            return True
        return self.exit_fullscreen()

    def exit_fullscreen(self):
        """Esc：从两种「独占」态里退出来（都没开就什么都不做，别吞掉 Esc）。"""
        if not self.is_fullscreen():
            return False
        if self._flow_full:
            self.main_view.set_flow_fullscreen(False)
        if self._truth_max:
            self.main_view.set_truth_maximized(False)
        return True

    def _sync_fullscreen(self):
        """独占态下让清单 / 标题栏让开；退出时按之前的可见性还原。"""
        full = self.is_fullscreen()
        self.list_panel.setVisible(not full)
        self.detail_header.setVisible(not self._flow_full)     # 真值表放大时标题栏还要看进度
        self._sync_side_visible()

    def _sync_side_visible(self):
        """⑨ 右栏的可见性只此一处算：用户开关 × 窄屏折叠（C-260）× 独占态。"""
        self.side_panel.setVisible(
            self._side_visible and not self.is_narrow() and not self.is_fullscreen())

    def _on_flow_exported(self, path):
        """⑦ 导出 SVG / PNG 落盘成功（C-284）：状态栏报一句，**不进 `state.record_export`**。

        主控裁决（C4-int）：电路图的 SVG / PNG 不是六种交付物之一
        （`contracts.EXPORT_KINDS` 没有这一档）。硬塞进去会让状态栏右侧的「上次导出 …」
        指向一张图片 —— 那一格说的是「上次交出去的产物在哪」，而一张电路图不是交付物，
        它是看图时顺手存的一张截图。同理单信号 CSV 也不记（C3-int 已同此裁决）。"""
        p = str(path or "")
        if not p:
            return ""
        self.set_status(p)
        return p

    def route_reason_action(self, target, payload=None):
        """行内原因块 / 解析明细的跳转路由（terms.REASON_TARGETS 的键）。"""
        t = str(target or "")
        if t == "diag_cuvunf":
            return self.openDiagnostics(t)
        if t == "diag_risky":
            return self.openDiagnostics(t)
        if t == "export_nets":
            return self.openExportCenter("nets")
        if t in ("copy_rows", "copy_detail"):
            text = ""
            if isinstance(payload, dict):
                text = str(payload.get("text") or "")
            elif payload is not None:
                text = str(payload)
            app = QtWidgets.QApplication.instance()
            if app is not None and text:
                app.clipboard().setText(text)
            self.set_status(terms.STATUS_COPIED)
            return t
        if t == "resolve_detail":
            nm = str(getattr(self._state, "current_name", "") or "")
            self.set_status(terms.STATUS_RESOLVE_OPENED_FMT.format(name=nm) if nm else "")
            return t
        return ""

    # ───────────────────────── 状态栏 / 标题 ─────────────────────────
    @QtCore.Slot(str)
    def set_status(self, text):
        """C-269：逐操作反馈都走 statusLeft。"""
        self._counts_timer.stop()        # 刚写的这一句别被合并中的那一发计数盖掉
        self.status_left.setText(terms.scrub(str(text or "")))

    def _refresh_status_counts_soon(self):
        """把连成一串的逐行升级合并成**一次**状态栏计数（PERF-1 节流）。

        `_counts_of` 要把整张清单扫两遍（`_status_detail` 的悬停行再扫两遍），逐信号算一次
        就是 O(N²) —— 合成 200 信号表上 cProfile 数出 163,400 次 `match_status`。
        worker 收尾（`_on_worker_finished`）会同步算一次准的。"""
        if not self._counts_timer.isActive():
            self._counts_timer.start()

    def _models(self):
        try:
            return list(self._state.models(self._scope()) or [])
        except Exception:                             # noqa: BLE001
            return []

    @staticmethod
    def _counts_of(models):
        """(要验几个, 有问题几个) —— **判据只有 `terms.match_status` 一份**（P-20）。

        以前这里按色档数（ok / warn·bad），而悬停那一行按「非 clean」数，筛选行又是第三套：
        同一块屏幕上「有问题 0」对着悬停「非 clean 2」对着筛选「仅有问题」筛出 0 条。
        `match_status` 里 note 色档按 v1 的四档分家（skip 算有问题、bare-probe 算可建）。"""
        ok_key = terms.STATUS_FILTER_ITEMS[1]
        bad_key = terms.STATUS_FILTER_ITEMS[2]
        return (sum(1 for m in models if terms.match_status(m, ok_key)),
                sum(1 for m in models if terms.match_status(m, bad_key)))

    def _refresh_status_counts(self):
        """C-006 / P-15：载完在状态栏报信号总数 / 要验几个 / 有问题几个 —— **按当前范围说话**。

        Topout 那一行还多一截 logic+mux 的分家（它是全链范围，两页的信号混在一起）；
        换到 logic / mux / dft / iddq 时那一截没有意义（dft 页上写「logic 9」纯属看错表）。"""
        self._counts_timer.stop()                     # 合并中的那一发作废：这一发就是现算的
        models = self._models()
        if not models:
            return
        n = len(models)
        nt, np_ = self._counts_of(models)
        vid = self._scope()
        if vid == contracts.DEFAULT_VIEW_ID:
            nm = sum(1 for m in models if (m.get("kind") or "") == "mux")
            txt = terms.STATUS_LOADED_FMT.format(n=n, nl=n - nm, nm=nm, nt=nt, np=np_)
        else:
            txt = terms.STATUS_LOADED_SCOPE_FMT.format(
                page=terms.SCOPE_PAGE_NAMES.get(vid, vid), n=n, nt=nt, np=np_)
        self.status_left.setText(txt)
        self.status_left.setToolTip(self._status_detail(models))

    def _status_detail(self, models):
        wb = getattr(self._state, "wb", None)
        ntmm = nreg = 0
        for attr, key in (("tmm", "ntmm"), ("regmap", "nreg")):
            try:
                v = len(getattr(wb, attr, None) or ())
            except TypeError:
                v = 0
            if key == "ntmm":
                ntmm = v
            else:
                nreg = v
        _nt, nbad = self._counts_of(models)          # P-20：与上面那一行同一份判据
        return terms.STATUS_LOADED_DETAIL_FMT.format(nbad=nbad, ntmm=ntmm, nreg=nreg)

    def _refresh_last_export(self):
        best = None
        for kind in contracts.EXPORT_KINDS:
            try:
                le = self._state.last_export(kind)
            except Exception:                         # noqa: BLE001
                le = None
            if le is None:
                continue
            if best is None or str(getattr(le, "ts", "")) > str(getattr(best[1], "ts", "")):
                best = (kind, le)
        if best is None:
            self.status_right.setText("")
            return
        kind, le = best
        self.status_right.setText(terms.STATUS_LAST_EXPORT_FMT.format(
            kind=terms.EXPORT_ROWS[kind][0], when=_fmt_when(getattr(le, "ts", "")),
            path=getattr(le, "path", "")))

    def _touch_autosave(self):
        import datetime
        now = datetime.datetime.now()
        self.status_autosave.setText(terms.STATUS_AUTOSAVE_FMT.format(
            when="%02d:%02d" % (now.hour, now.minute)))

    def _refresh_title(self):
        v = self._code_version()
        self.setWindowTitle(terms.WINDOW_TITLE_FMT.format(version=v) if v else terms.WINDOW_TITLE_NOVER)

    def _code_version(self):
        """C-259 / 裁决⑬：标题写「版本 <短HEAD>」，不写 git 字样。

        只走 state（`code_version` 已进 `WorkbenchStateProto`）——C1-d 那条「缺了就惰性
        import session 兜底」的回落已删：组合根不该绕过状态层直接问引擎层（I-19）。"""
        try:
            return str(self._state.code_version() or "")
        except Exception:                             # noqa: BLE001
            return ""

    # ───────────────────────── 空态「最近打开」─────────────────────────
    def _refresh_recent(self):
        while self._recent_lay.count():
            it = self._recent_lay.takeAt(0)
            w = it.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()
        items = []
        try:
            items = list(self._state.recent_excels() or [])
        except Exception:                             # noqa: BLE001
            items = []
        for i, rec in enumerate(items[:theme.RECENT_MAX]):
            path = str(getattr(rec, "path", "") or (rec.get("path") if isinstance(rec, dict) else ""))
            if not path:
                continue
            ts = str(getattr(rec, "ts", "") or (rec.get("ts", "") if isinstance(rec, dict) else ""))
            n = getattr(rec, "n_signals", None)
            if n is None and isinstance(rec, dict):
                n = rec.get("n_signals", 0)
            row = QtWidgets.QWidget(self.recent_list)
            row.setObjectName(fmt_recent_row(i))
            rl = QtWidgets.QHBoxLayout(row)
            rl.setContentsMargins(0, 0, 0, 0)
            btn = QtWidgets.QPushButton(path, row)
            btn.setFlat(True)
            btn.setCursor(QtCore.Qt.PointingHandCursor)
            btn.setFont(mono_font(theme.FS_MONO))
            btn.setStyleSheet("QPushButton{color:%s;border:none;text-align:left;padding:0;}" % theme.BLUE)
            btn.clicked.connect(lambda _=False, p=path: self.load_path(p))
            rl.addWidget(btn, 1)
            meta = QtWidgets.QLabel(terms.EMPTY_RECENT_ROW_FMT.format(
                when=_fmt_when(ts), n=int(n or 0)), row)
            meta.setStyleSheet("color:%s;" % theme.MUTE)
            rl.addWidget(meta, 0)
            self._recent_lay.addWidget(row)
        has = self._recent_lay.count() > 0
        self.recent_list.setVisible(has)
        self.recent_none.setVisible(not has)          # 裁决⑫：无记录写「还没有最近打开的表」，不写示例路径

    # ───────────────────────── 三态切换 / 尺寸 ─────────────────────────
    def _show_empty(self):
        self.stack.setCurrentIndex(0)
        self.filter_bar.setVisible(False)

    def _show_workbench(self):
        self.stack.setCurrentIndex(1)
        self.filter_bar.setVisible(True)

    def is_empty_state(self):
        return self.stack.currentIndex() == 0

    def _read_sizes(self):
        st = {}
        try:
            st = dict(self._state.settings() or {})
        except Exception:                             # noqa: BLE001
            st = {}
        out = dict(SIZE_DEFAULTS)
        for k in SIZE_KEYS:
            v = st.get(k)
            if isinstance(v, int) and v > 0:
                out[k] = v
        return out

    def preferred_sizes(self):
        """当前生效的分割尺寸（C-261：listW 560 / sideW 470 / truthH 420 / chainH 300）。"""
        return dict(self._sizes)

    def _apply_sizes(self):
        s = self._sizes
        self.split_main.setSizes([s["listW"], max(1, theme.WIN_W - s["listW"])])
        self._apply_side_width()
        self.main_view.set_truth_height(s["truthH"])      # ⑥ 真件自己夹 CLAMP_TRUTH
        self.side_panel.set_chain_height(s["chainH"])     # ⑨ 真件自己夹 CLAMP_CHAIN

    def _apply_side_width(self):
        total = sum(self.split_side.sizes()) or theme.WIN_W - self._sizes["listW"]
        side = self._sizes["sideW"]
        self.split_side.setSizes([max(1, total - side), side])

    def _save_sizes(self):
        m, sd = self.split_main.sizes(), self.split_side.sizes()
        patch = {}
        if m and m[0] > 0:
            patch["listW"] = int(m[0])
        if len(sd) > 1 and sd[1] > 0:
            patch["sideW"] = int(sd[1])
        th = self.main_view.truth_height()
        if th > 0:
            patch["truthH"] = int(th)
        ch = self.side_panel.chain_height()
        if ch > 0:
            patch["chainH"] = int(ch)
        if not patch:
            return
        self._sizes.update(patch)
        try:
            self._state.save_settings(patch)
        except Exception:                             # noqa: BLE001
            pass

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        self._place_drawer()                          # ⑬ 覆盖层不在布局里，几何得自己跟
        narrow = self.width() <= theme.WIN_MIN_W      # C-260：1366×768 时清单收到 420、右栏折叠
        if narrow == self._narrow:
            return
        self._narrow = narrow
        if narrow:
            w = theme.LIST_W_NARROW
            self.split_main.setSizes([w, max(1, self.width() - w)])
        else:
            self.split_main.setSizes([self._sizes["listW"], max(1, self.width() - self._sizes["listW"])])
        self._sync_side_visible()

    def is_narrow(self):
        return bool(self._narrow)

    def set_side_visible(self, on):
        """④ HDR_SIDE_TOGGLE 的落点。标题栏按钮的文案也跟着切（两边说的是同一件事）。"""
        self._side_visible = bool(on)
        self.detail_header.set_side_visible(self._side_visible)   # notify=False：不回弹信号
        self._sync_side_visible()

    def closeEvent(self, ev):
        """关窗前把后台线程收干净：QThread 还在跑就被析构 = 进程级崩溃（不是报错，是直接没）。"""
        for w in [self._worker] + list(getattr(self, "_retired", [])):
            if w is None or not w.is_running():
                continue
            w.cancel()
            wait = getattr(w, "wait", None)
            if callable(wait):
                wait(CLOSE_WAIT_MS)
        super().closeEvent(ev)


# ═════════════════════════════════ 入口 ═════════════════════════════════
def build_window(argv=(), state=None, worker_factory=None, providers_factory=None):
    """起窗 + 走启动载入（C-003 / C-004）。测试用这个，不要用 `main`（它会进事件循环）。"""
    w = MainWindow(state=state, worker_factory=worker_factory, providers_factory=providers_factory)
    w.apply_startup(argv)
    return w


def main(argv=None):
    """I-17：`python -m dreg_verify.gui` 的最终落点（C5 把 gui.py 变薄壳指到这里）。"""
    args = list(sys.argv[1:] if argv is None else argv)
    app = QtWidgets.QApplication.instance()
    owns = app is None
    if owns:
        app = QtWidgets.QApplication(sys.argv[:1] + args)
    w = build_window(args)
    w.show()
    return app.exec() if owns else 0


if __name__ == "__main__":                            # pragma: no cover
    sys.exit(main())
