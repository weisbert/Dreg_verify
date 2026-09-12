# -*- coding: utf-8 -*-
"""app.py —— v2 组合根 `MainWindow` + `main()`（C1-d 骨架；之后每波集成 agent 接管）。

版面（Design §1.2 区号）：

    QMainWindow(WIN_MAIN)
      ├ ① 顶栏 TOP_BAR（品牌 · 真表路径 · 浏览 Ctrl+O · 载入/重新载入 Ctrl+L · 诊断 Ctrl+Shift+D · 导出中心 Ctrl+G）
      ├ 错误条 ERROR_BAR（C-005 / C-272：载表出错用工程师语言，**不弹窗不退出**）
      ├ ② 筛选行 FILTER_BAR（C1-c 的 filter_bar，本波占位）
      ├ WIN_STACK（QStackedWidget）
      │    ├ ⑭ 空态 EMPTY_PANEL（620px 居中；xlsx 虚线框 / 标题 / 说明 / 两个按钮 / 最近打开）
      │    └ 工作台 WIN_WORKBENCH
      │         └ WIN_SPLIT_MAIN（③ 清单 LIST_PANEL | 详情列）
      │              └ 详情列 = ⑮ 载入态 LOADING_PANEL + ④ 标题栏 HDR_BAR
      │                          + WIN_SPLIT_SIDE（⑤⑥⑦⑧ MAIN_VIEW | ⑨ SIDE_PANEL）
      └ ⑩ 状态栏 STATUS_BAR（STATUS_LEFT / STATUS_RIGHT / STATUS_AUTOSAVE）

⚠ 载入态**不是** WIN_STACK 的独立一页：C-276 / 场景⑧ 要求「清单立刻可点」，
  所以载入卡片挂在工作台详情列顶部（架构 §6.1「详情列 = 载入态⑮卡片 + 标题栏④ + WIN_SPLIT_SIDE」）。
  names.WIN_STACK 注释里的「三页」是 B3 阶段的旧说法，以本实现为准。

依赖注入（C1 各 agent 并行的关键）：
    MainWindow(state=None, worker_factory=None, providers_factory=None)
真实 `ui.state.WorkbenchState` / `ui.worker.AnalysisWorker` **只在默认工厂里惰性 import**，
测试传自己的 FakeState / FakeWorker（按 `contracts.WorkbenchStateProto` / `WORKER_SIGNALS` 写）即可起窗。

占位区（C1-int / C2 / C3 换成真件，见 `PLACEHOLDER_AREAS`）：
    FILTER_BAR · LIST_PANEL · HDR_BAR · MAIN_VIEW（含 TRUTH_PANEL / FLOW_PANEL / SV_PANEL）· SIDE_PANEL
"""

import os
import sys

from PySide6 import QtCore, QtGui, QtWidgets

from . import contracts, names, terms, theme
from .widgets import ErrorBar, ProgressBadge, mono_font, ui_font

# ── 本波用到、但 names.py 里还没有的 objectName（C1-int 请把它们并进 names.py）──
PENDING_NAMES = {
    "EMPTY_ICON": "empty_icon",                 # ⑭ 88px「xlsx」虚线方块
    "EMPTY_RECENT_TITLE": "empty_recent_title",  # ⑭「最近打开」小标题
    "EMPTY_CARD": "empty_card",                 # ⑭ 620px 居中卡片
    "LOADING_CARD": "loading_card",             # ⑮ 520px 居中卡片（LOADING_PANEL 是它的外壳）
    "TOP_TABLE_TAG": "top_table_tag",           # ①「真表」标签
    "DETAIL_COLUMN": "detail_column",           # 详情列容器（载入态 + 标题栏 + WIN_SPLIT_SIDE）
    "SHORTCUT_FMT": "shortcut_%s",              # fmt_shortcut(key)：QShortcut 也要能按名找
    "RECENT_ROW_FMT": "empty_recent_row_%d",    # fmt_recent_row(i)
}

#: 本波留的占位控件（objectName → 哪个波换成真件）
PLACEHOLDER_AREAS = {
    names.FILTER_BAR: "C1-c ui/filter_bar.py",
    names.LIST_PANEL: "C1-b ui/signal_list.py",
    names.HDR_BAR: "C2-c ui/detail_header.py",
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


def fmt_shortcut(key):
    return PENDING_NAMES["SHORTCUT_FMT"] % key


def fmt_recent_row(i):
    return PENDING_NAMES["RECENT_ROW_FMT"] % int(i)


# ═════════════════════════ 默认工厂（惰性 import，C1-a 的实物不在时也能起窗）═════════════
def default_state_factory(providers_factory=None):
    """真实会话状态。**函数内 import**：C1-a 还没合入时，传 state=FakeState() 照样起窗。"""
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
        self._excel_path = ""
        self._load_error = ""
        self._narrow = None
        self._side_visible = True
        self._sizes = self._read_sizes()

        self.setFont(ui_font(theme.FS_UI))
        self._build_ui()
        self._build_shortcuts()
        self._connect_state()
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

        self.filter_bar = _placeholder(names.FILTER_BAR, central, min_h=theme.BTN_H + 12,
                                       bg=theme.WHITE)
        self.filter_bar.setMaximumHeight(theme.BTN_H + 12)
        self.filter_bar.setVisible(False)            # ② 只在 loaded 时出现
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
        tag.setObjectName(PENDING_NAMES["TOP_TABLE_TAG"])
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
        card.setObjectName(PENDING_NAMES["EMPTY_CARD"])
        card.setFixedWidth(theme.EMPTY_W)
        lay = QtWidgets.QVBoxLayout(card)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(12)

        icon = QtWidgets.QLabel("xlsx", card)
        icon.setObjectName(PENDING_NAMES["EMPTY_ICON"])
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
        rtitle.setObjectName(PENDING_NAMES["EMPTY_RECENT_TITLE"])
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
        card.setObjectName(PENDING_NAMES["LOADING_CARD"])
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

        self.list_panel = _placeholder(names.LIST_PANEL, self.split_main)
        self.list_panel.setMinimumWidth(theme.CLAMP_LIST[0])
        self.list_panel.setMaximumWidth(theme.CLAMP_LIST[1])
        self.split_main.addWidget(self.list_panel)

        self.detail_column = QtWidgets.QWidget(self.split_main)
        self.detail_column.setObjectName(PENDING_NAMES["DETAIL_COLUMN"])
        dlay = QtWidgets.QVBoxLayout(self.detail_column)
        dlay.setContentsMargins(0, 0, 0, 0)
        dlay.setSpacing(0)
        self.loading_panel = self._build_loading_card(self.detail_column)
        dlay.addWidget(self.loading_panel)
        self.hdr_bar = _placeholder(names.HDR_BAR, self.detail_column, min_h=2 * theme.ROW_H)
        self.hdr_bar.setMaximumHeight(2 * theme.ROW_H)
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
        _connect(s, "settingsChanged", lambda *_: None)

    @property
    def state(self):
        return self._state

    @property
    def worker(self):
        return self._worker

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
        """真正的载表入口：state.load(path) → 成功进工作台并起 worker；失败只写错误条（C-005/C-272）。"""
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
        self._refresh_status_counts()
        self._refresh_last_export()
        self.start_analysis()

    def _on_scope_changed(self, _view_id=""):
        self.start_analysis()

    def _on_models_changed(self, _view_id=""):
        self._refresh_status_counts()

    def _on_model_updated(self, _view_id="", _name=""):
        self._refresh_status_counts()

    def _on_current_changed(self, name=""):
        self._relayout_loading(bool(name))

    def _on_export_recorded(self, _kind=""):
        self._refresh_last_export()

    # ───────────────────────── 后台分析（N8 / C-276）─────────────────────────
    def start_analysis(self):
        """骨架先出（清单立刻可点）→ 起 worker 逐信号升级。"""
        vid = getattr(self._state, "scope", contracts.DEFAULT_VIEW_ID)
        try:
            provider = self._state.provider(vid)
        except Exception:                             # noqa: BLE001
            provider = None
        if provider is None:
            return False
        if self._worker is not None and self._worker.is_running():
            self._worker.cancel()
        try:
            skeleton = list(provider.skeleton_models() or [])
        except Exception as exc:                      # noqa: BLE001
            self._on_worker_failed(vid, str(exc))
            return False
        self._state.set_models(vid, skeleton, True)
        total = len(skeleton)
        self._set_loading(True, 0, total, "")
        self._worker = self._worker_factory()
        _connect(self._worker, "started", self._on_worker_started)
        _connect(self._worker, "progress", self._on_worker_progress)
        _connect(self._worker, "signalDone", self._on_worker_signal_done)
        _connect(self._worker, "finished", self._on_worker_finished)
        _connect(self._worker, "cancelled", self._on_worker_cancelled)
        _connect(self._worker, "failed", self._on_worker_failed)
        self.analysisStarted.emit(vid, total)
        self._worker.start(provider, self._analysis_request(vid), self._engine_lock())
        return True

    def _analysis_request(self, vid):
        mode, exhaustive, max_tests, sig_cov, form_cov = "max", False, 256, None, None
        try:
            cov = self._state.coverage(vid)
            mode, exhaustive = cov.mode()
            max_tests = int(cov.max_tests)
            sig_cov = dict(cov.sig_cov)
            form_cov = dict(cov.form_cov)
        except Exception:                             # noqa: BLE001  覆盖度是 C1-c/C1-a 的事，缺了用默认
            pass
        fp = ""
        try:
            fp = str(self._state.fingerprint(vid) or "")
        except Exception:                             # noqa: BLE001
            fp = ""
        return contracts.AnalysisRequest(view_id=vid, mode=mode, max_tests=max_tests,
                                         exhaustive=exhaustive, sig_cov=sig_cov,
                                         form_cov=form_cov, fingerprint=fp)

    def _engine_lock(self):
        return getattr(self._state, "engine_lock", None)

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
        vid = getattr(self._state, "scope", contracts.DEFAULT_VIEW_ID)
        try:
            self._state.update_model(vid, dict(lite_model or {}))
        except Exception:                             # noqa: BLE001
            pass

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

    def _refresh_status_counts(self):
        """C-006：载完在状态栏报信号总数 / logic+mux / 要验几个 / 有问题几个。"""
        try:
            models = list(self._state.models() or [])
        except Exception:                             # noqa: BLE001
            models = []
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
        走 state（C1-a 补 `code_version()` 后这里就只剩第一支）；缺了才惰性回落到 session。"""
        fn = getattr(self._state, "code_version", None)
        if callable(fn):
            try:
                return str(fn() or "")
            except Exception:                         # noqa: BLE001
                return ""
        from dreg_verify import session as _session   # 惰性，见 I-19
        try:
            return str(_session.code_version() or "")
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
        if self._worker is not None and self._worker.is_running():
            try:
                self._worker.cancel()
            except Exception:                         # noqa: BLE001
                pass
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
