# -*- coding: utf-8 -*-
"""app.py —— v2 组合根 `MainWindow` + `main()`（C1-d 骨架 → C1-int 接线；之后每波集成 agent 接管）。

版面（Design §1.2 区号）：

    QMainWindow(WIN_MAIN)
      ├ ① 顶栏 TOP_BAR（品牌 · 真表路径 · 浏览 Ctrl+O · 载入/重新载入 Ctrl+L · 诊断 Ctrl+Shift+D · 导出中心 Ctrl+G）
      ├ 错误条 ERROR_BAR（C-005 / C-272：载表出错用工程师语言，**不弹窗不退出**）
      ├ ② 筛选行 FILTER_BAR（`filter_bar.FilterBar`）
      ├ WIN_STACK（QStackedWidget）
      │    ├ ⑭ 空态 EMPTY_PANEL（620px 居中；xlsx 虚线框 / 标题 / 说明 / 两个按钮 / 最近打开）
      │    └ 工作台 WIN_WORKBENCH
      │         └ WIN_SPLIT_MAIN（③ 清单 LIST_PANEL = `signal_list.SignalListPanel` | 详情列）
      │              └ 详情列 = ⑮ 载入态 LOADING_PANEL + ④ 标题栏 HDR_BAR（暂只挂 `coverage.CoverageControl`）
      │                          + WIN_SPLIT_SIDE（⑤⑥⑦⑧ MAIN_VIEW | ⑨ SIDE_PANEL）
      └ ⑩ 状态栏 STATUS_BAR（STATUS_LEFT / STATUS_RIGHT / STATUS_AUTOSAVE）

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

占位区（C2 / C3 换成真件，见 `PLACEHOLDER_AREAS`）：
    HDR_BAR · MAIN_VIEW（含 TRUTH_PANEL / FLOW_PANEL / SV_PANEL）· SIDE_PANEL
"""

import os
import sys

from PySide6 import QtCore, QtGui, QtWidgets

from . import contracts, names, terms, theme
from .coverage import CoverageControl
from .filter_bar import FilterBar
from .signal_list import SignalListPanel, build_reason_block
from .widgets import ErrorBar, ProgressBadge, mono_font, ui_font

#: 还没换成真件的区（objectName → 哪个波接手）。测试断言它们都带 property("placeholder")。
PLACEHOLDER_AREAS = {
    names.HDR_BAR: "C2-c ui/detail_header.py（覆盖度按钮已挂进来）",
    names.MAIN_VIEW: "C2-c ui/main_view.py",
    names.TRUTH_PANEL: "C3-c ui/truth/panel.py",
    names.FLOW_PANEL: "C2-b ui/sigflow_view.py",
    names.SV_PANEL: "C2-c ui/sv_preview.py",
    names.SIDE_PANEL: "C2-a ui/side_panel.py",
}

#: 组合根在窗口级绑的快捷键（copy_col / undo / redo / paste 是真值表的 WidgetShortcut，C3 绑）
APP_SHORTCUTS = ("open", "load", "sv_preview", "export_report", "export_center", "diagnostics")

#: settings 里记分割尺寸的键（Design §1.1 state 键同名）
SIZE_KEYS = ("listW", "sideW", "truthH", "chainH")
SIZE_DEFAULTS = {"listW": theme.LIST_W, "sideW": theme.SIDE_W,
                 "truthH": theme.TRUTH_H, "chainH": theme.CHAIN_H}

#: 关窗时等后台线程在信号边界停下来的上限（ms）——QThread 还在跑就析构 = 进程级崩溃
CLOSE_WAIT_MS = 3000

fmt_shortcut = names.fmt_shortcut          # 名字已并进 names.py；这两个别名保住既有引用
fmt_recent_row = names.fmt_recent_row


# ═════════════════════════ 默认工厂（惰性 import，测试可整体替换）═════════════
def default_state_factory(providers_factory=None):
    """真实会话状态。**函数内 import**：测试传 state=FakeState() 时一行 state.py 都不用加载。

    `WorkbenchState` 自己就是 `providers.ConfigSourceProto`（wb / 三套诊断配置 /
    include_risky / engine_lock），载表时按范围建 `TopoutProvider` + 四个 `PageProvider`，
    所以正常路径下没有 `providers_factory` 什么事；它留给「想换数据源」的测试，
    真 state 不认这个参数就照常裸建（I-21 的锁仍在 state 手上）。"""
    from .state import WorkbenchState
    if providers_factory is not None:
        try:
            return WorkbenchState(providers_factory=providers_factory)
        except TypeError:
            pass
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


def _fmt_when(ts):
    """ISO 时间串 → 「今天 09:14」/「昨天 17:02」/「2026-09-01 17:02」；空串 → ""。"""
    import datetime
    s = str(ts or "").strip()
    if not s:
        return ""
    try:
        t = datetime.datetime.fromisoformat(s)
    except ValueError:
        return s
    today = datetime.date.today()
    if t.date() == today:
        return "今天 %02d:%02d" % (t.hour, t.minute)
    if (today - t.date()).days == 1:
        return "昨天 %02d:%02d" % (t.hour, t.minute)
    return t.strftime("%Y-%m-%d %H:%M")


def _tone_of(model):
    """清单模型 → 四档色 tone（terms.STATUS 的第二项）。"""
    key = (model.get("status_detail") or "").strip()
    if key not in terms.STATUS:
        key = terms.STATUS_FALLBACK.get(model.get("status") or "", "")
    return terms.STATUS[key][1] if key in terms.STATUS else "note"


def _placeholder(object_name, parent=None, min_w=0, min_h=0, bg=None):
    """占位区：C1-int / C2 / C3 把它整块换掉。带 objectName，测试能 find 到区位。

    画一层白底 + 细边框，只是为了让骨架截图读得出 Design 的分区（不是最终皮肤）。"""
    w = QtWidgets.QWidget(parent)
    w.setObjectName(object_name)
    w.setProperty("placeholder", True)
    w.setAttribute(QtCore.Qt.WA_StyledBackground, True)
    w.setStyleSheet("QWidget#%s{background:%s;border:1px solid %s;}"
                    % (object_name, bg or theme.WHITE, theme.BORDER_LIGHT))
    if min_w:
        w.setMinimumWidth(min_w)
    if min_h:
        w.setMinimumHeight(min_h)
    lay = QtWidgets.QVBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    return w


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
        self._state = state if state is not None else default_state_factory(providers_factory)
        self._worker_factory = worker_factory or default_worker_factory
        self._worker = None
        self._retired = []             # 已经作废、但线程还没停下来的旧 worker（见 _retire_worker）
        self._run_id = 0               # 分析「第几趟」：旧趟迟到的信号按它丢掉
        self._analysis_vid = ""        # 这一趟 worker 在跑哪个范围（迟到的信号按它对齐）
        self._excel_path = ""
        self._load_error = ""
        self._narrow = None
        self._side_visible = True
        self._sizes = self._read_sizes()

        self.setFont(ui_font(theme.FS_UI))
        self._build_ui()
        self._build_shortcuts()
        self._connect_state()
        self._connect_views()
        self._refresh_title()
        self._refresh_recent()
        self._show_empty()
        self.resize(theme.WIN_W, theme.WIN_H)

    # ───────────────────────── 构建 ─────────────────────────
    def _build_ui(self):
        central = QtWidgets.QWidget(self)
        central.setObjectName(names.WIN_WORKBENCH + "_root")
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

    # ④ 详情标题栏（C2-c 的 detail_header 换掉整块；本波先把覆盖度按钮挂在右上角）
    def _build_hdr_bar(self, parent):
        bar = _placeholder(names.HDR_BAR, parent, min_h=2 * theme.ROW_H)
        bar.setMaximumHeight(2 * theme.ROW_H)
        lay = bar.layout()
        lay.setContentsMargins(12, 4, 12, 4)
        row = QtWidgets.QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.addStretch(1)
        self.coverage = CoverageControl(self._state, bar)    # ⑯ C1-c 的真件
        row.addWidget(self.coverage, 0, QtCore.Qt.AlignTop)
        lay.addLayout(row)
        lay.addStretch(1)
        return bar

    def _build_main_view(self, parent):
        """⑤⑥⑦⑧ 主视图：QStackedWidget（真值表+电路图 页 / .sv 预览 页）。C2-c 换真件。"""
        mv = QtWidgets.QStackedWidget(parent)
        mv.setObjectName(names.MAIN_VIEW)
        mv.setProperty("placeholder", True)
        page = QtWidgets.QWidget(mv)
        pl = QtWidgets.QVBoxLayout(page)
        pl.setContentsMargins(0, 0, 0, 0)
        self.split_truth_flow = QtWidgets.QSplitter(QtCore.Qt.Vertical, page)
        self.split_truth_flow.setObjectName(names.WIN_SPLIT_TRUTH_FLOW)
        self.split_truth_flow.setChildrenCollapsible(False)
        self.split_truth_flow.setHandleWidth(theme.HANDLE_W)
        self.truth_panel = _placeholder(names.TRUTH_PANEL, self.split_truth_flow, min_h=theme.CLAMP_TRUTH[0])
        self.truth_panel.setMaximumHeight(theme.CLAMP_TRUTH[1])
        self.split_truth_flow.addWidget(self.truth_panel)
        self.flow_panel = _placeholder(names.FLOW_PANEL, self.split_truth_flow, min_h=1)
        self.split_truth_flow.addWidget(self.flow_panel)
        pl.addWidget(self.split_truth_flow)
        mv.addWidget(page)                                   # index 0：真值表 + 电路图
        self.sv_panel = _placeholder(names.SV_PANEL, mv, min_h=1)
        mv.addWidget(self.sv_panel)                          # index 1：.sv 预览（Ctrl+P）
        self.main_view = mv
        self.split_truth_flow.splitterMoved.connect(lambda *_: self._save_sizes())
        return mv

    def _build_side_panel(self, parent):
        """⑨ 右侧常驻栏：逐层展开 / 输入信号 两段。C2-a 换真件。"""
        sp = QtWidgets.QWidget(parent)
        sp.setObjectName(names.SIDE_PANEL)
        sp.setProperty("placeholder", True)
        sp.setMinimumWidth(theme.CLAMP_SIDE[0])
        sp.setMaximumWidth(theme.CLAMP_SIDE[1])
        lay = QtWidgets.QVBoxLayout(sp)
        lay.setContentsMargins(0, 0, 0, 0)
        self.split_chain_inputs = QtWidgets.QSplitter(QtCore.Qt.Vertical, sp)
        self.split_chain_inputs.setObjectName(names.WIN_SPLIT_CHAIN_INPUTS)
        self.split_chain_inputs.setChildrenCollapsible(False)
        self.split_chain_inputs.setHandleWidth(theme.HANDLE_W)
        chain = _placeholder(names.SIDE_CHAIN_VIEW, self.split_chain_inputs)
        chain.setMinimumHeight(theme.CLAMP_CHAIN[0])
        chain.setMaximumHeight(theme.CLAMP_CHAIN[1])
        self.split_chain_inputs.addWidget(chain)
        self.split_chain_inputs.addWidget(_placeholder(names.SIDE_INPUTS_VIEW, self.split_chain_inputs))
        lay.addWidget(self.split_chain_inputs)
        self.split_chain_inputs.splitterMoved.connect(lambda *_: self._save_sizes())
        return sp

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
        self._set_path_text(path)
        self.error_bar.dismiss()
        self._load_error = ""
        ok = False
        try:
            ok = bool(self._state.load(path))
        except Exception as exc:                      # noqa: BLE001  载表异常不许崩窗
            ok = False
            if not self._load_error:
                self._on_load_failed(terms.STATUS_LOAD_FAILED_FMT.format(reason=exc))
        if not ok and not self._load_error:
            self._on_load_failed(terms.STATUS_LOAD_FAILED_FMT.format(reason=path))
        return ok

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
        self.start_analysis()

    def _on_models_changed(self, view_id=""):
        if view_id not in ("", self._scope()):
            return
        self.filter_bar.rebuild(self._models())       # owner / 分类下拉只列本表真有的（C-027/C-028）
        self._refresh_status_counts()                 # 顺序：先重建（它会发筛选计数）再写载表小结

    def _on_model_updated(self, view_id="", _name=""):
        if view_id in ("", self._scope()):
            self._refresh_status_counts()

    def _on_current_changed(self, name=""):
        self.coverage.set_current(name)               # C-149：只回显本信号的生效档，不重算
        self._relayout_loading(bool(name))

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

    def save_preset(self):
        """C-291 存预设：勾选集由 state 出、筛选由②出，落 settings["presets"]。

        ⏳ 取名用的是标准 `QInputDialog`；C2-d 的 `DLG_PRESETS` 到位后换成它（本处三行）。"""
        name, ok = QtWidgets.QInputDialog.getText(self, terms.PRESETS, terms.PRESET_SAVE)
        name = str(name or "").strip()
        if not ok or not name:
            return ""
        presets = dict((self._state.settings() or {}).get("presets") or {})
        spec = dict(self.filter_bar.preset_payload())
        spec["checks"] = list(self._state.checked_names(self._scope()))
        presets[name] = spec
        self._state.save_settings({"presets": presets})
        self.filter_bar.rebuild_presets_menu()
        self.set_status("%s · %s" % (terms.PRESETS, name))
        return name

    @QtCore.Slot(str)
    def load_preset(self, name):
        """C-291 取预设：范围 → 筛选 → 勾选，三样一起回到当时的样子。"""
        nm = str(name or "")
        if not nm:
            self.set_status(terms.PRESET_MANAGE)
            return ""
        spec = ((self._state.settings() or {}).get("presets") or {}).get(nm)
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
            self._on_worker_failed(vid, str(exc))
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
        self.analysisEnded.emit(str(view_id), True)

    def _on_worker_cancelled(self, view_id, models_partial):
        part = list(models_partial or [])
        self._state.set_models(view_id, part, True)
        done, total = self.loading_bar.value()
        self._set_loading(False)
        self.set_status(terms.LOADING_STOPPED_FMT.format(done=len(part) or done, total=total))
        self.analysisEnded.emit(str(view_id), False)

    def _on_worker_failed(self, view_id, message):
        self._set_loading(False)
        self.error_bar.show_error(message)
        self.set_status(self.error_bar.text())
        self.analysisEnded.emit(str(view_id), False)

    def _set_loading(self, on, done=0, total=0, name=""):
        if not on:
            self.loading_panel.setVisible(False)
            return
        self.loading_title.setText(terms.LOADING_TITLE_FMT.format(done=int(done), total=int(total)))
        self.loading_bar.set_value(done, total)
        self.loading_current.setText(terms.LOADING_CURRENT_FMT.format(name=name, hint="") if name else "")
        self.loading_panel.setVisible(True)
        self._relayout_loading(bool(getattr(self._state, "current_name", "")))

    def _relayout_loading(self, compact):
        """有当前信号时把载入卡片压成一行条（架构 §6.1）。"""
        if not hasattr(self, "loading_hint"):
            return
        self.loading_hint.setVisible(not compact)
        self.loading_current.setVisible(not compact)

    # ───────────────────────── 场景路由 ─────────────────────────
    @QtCore.Slot(str)
    def openDiagnostics(self, symptom=""):
        """⑬ 诊断抽屉（C4-b）。本波只发信号 + 状态栏提示，C4 接上真件。"""
        sym = str(symptom or "")
        self.diagnosticsRequested.emit(sym)
        self.set_status(terms.REASON_TARGETS.get(sym) or terms.DIAG_TITLE)
        return sym

    @QtCore.Slot(str)
    def openExportCenter(self, preselect=""):
        """⑪ 导出中心（C4-a）。preselect ∈ contracts.EXPORT_KINDS 时预选那一行。"""
        pre = str(preselect or "")
        self.exportCenterRequested.emit(pre)
        if pre in contracts.EXPORT_KINDS:
            self.set_status("%s · %s" % (terms.EXPORT_TITLE, terms.EXPORT_ROWS[pre][0]))
        else:
            self.set_status(terms.EXPORT_TITLE)
        return pre

    def on_sv_preview(self):
        """Ctrl+P（C-255 / 裁决①）：主视图切到 .sv 预览标签。"""
        self.main_view.setCurrentIndex(1)
        self.svPreviewRequested.emit()
        self.set_status(terms.SV_TITLE_SIGNAL)
        return True

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
            self.set_status(terms.HDR_RESOLVE)
            return t
        return ""

    # ───────────────────────── 状态栏 / 标题 ─────────────────────────
    @QtCore.Slot(str)
    def set_status(self, text):
        """C-269：逐操作反馈都走 statusLeft。"""
        self.status_left.setText(terms.scrub(str(text or "")))

    def _models(self):
        try:
            return list(self._state.models(self._scope()) or [])
        except Exception:                             # noqa: BLE001
            return []

    def _refresh_status_counts(self):
        """C-006：载完在状态栏报信号总数 / logic+mux / 要验几个 / 有问题几个。"""
        models = self._models()
        if not models:
            return
        n = len(models)
        nm = sum(1 for m in models if (m.get("kind") or "") == "mux")
        tones = [_tone_of(m) for m in models]
        txt = terms.STATUS_LOADED_FMT.format(n=n, nl=n - nm, nm=nm,
                                             nt=sum(1 for t in tones if t == "ok"),
                                             np=sum(1 for t in tones if t in ("warn", "bad")))
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
        nbad = sum(1 for m in models if _tone_of(m) != "ok")
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
        self.split_side.setSizes([max(1, theme.WIN_W - s["listW"] - s["sideW"]), s["sideW"]])
        self.split_truth_flow.setSizes([s["truthH"], max(1, theme.WIN_H - s["truthH"])])
        self.split_chain_inputs.setSizes([s["chainH"], max(1, theme.WIN_H - s["chainH"])])

    def _save_sizes(self):
        m, sd = self.split_main.sizes(), self.split_side.sizes()
        tf, ci = self.split_truth_flow.sizes(), self.split_chain_inputs.sizes()
        patch = {}
        if m and m[0] > 0:
            patch["listW"] = int(m[0])
        if len(sd) > 1 and sd[1] > 0:
            patch["sideW"] = int(sd[1])
        if tf and tf[0] > 0:
            patch["truthH"] = int(tf[0])
        if ci and ci[0] > 0:
            patch["chainH"] = int(ci[0])
        if not patch:
            return
        self._sizes.update(patch)
        try:
            self._state.save_settings(patch)
        except Exception:                             # noqa: BLE001
            pass

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        narrow = self.width() <= theme.WIN_MIN_W      # C-260：1366×768 时清单收到 420、右栏折叠
        if narrow == self._narrow:
            return
        self._narrow = narrow
        if narrow:
            w = theme.LIST_W_NARROW
            self.split_main.setSizes([w, max(1, self.width() - w)])
            self.side_panel.setVisible(False)
        else:
            self.split_main.setSizes([self._sizes["listW"], max(1, self.width() - self._sizes["listW"])])
            self.side_panel.setVisible(self._side_visible)

    def is_narrow(self):
        return bool(self._narrow)

    def set_side_visible(self, on):
        """④ HDR_SIDE_TOGGLE 的落点（C2-c 接线）。"""
        self._side_visible = bool(on)
        self.side_panel.setVisible(self._side_visible and not self.is_narrow())

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
