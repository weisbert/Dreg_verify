# -*- coding: utf-8 -*-
"""main_view.py —— ⑤⑥⑦⑧ 主视图容器（GUI v2 Phase C2-c）。

Design 对照：docs/GUI_v2_Design对齐_20260912.md §1.2 ⑤（标签条 `tabDef` 只有 2 个）
    + ⑥（真值表 `height:S.truthH=420`，`flowFull` 时整块隐藏）+ ⑦（电路图永远在真值表下方）。
架构简报：docs/GUI_v2_架构_20260912.md §6.7；契约 ID = 附录 A「MAINVIEW」1 条
    C-280 图在 GUI 详情页显示：真值表下方常驻电路图区（truthH 分割、flowFull 可全屏）
配合：C-255（Ctrl+P 切 .sv 预览标签，快捷键由组合根绑）、C-297（真值表放大：隐藏电路图 + 右栏）。

**唯一的容器例外**（§1.2 依赖方向）：视图之间不互相 import，`main_view` 组合三块是明写的例外；
为了让 C2-b（`sigflow_view`）/ C3（`truth/panel`）能各自并行交付，这里一律走**注入**：
    MainView(truth_widget=None, flow_widget=None, sv_widget=None)
`None` 时放一块带 objectName 的占位（沿用 `ui/app.py` 的 `PLACEHOLDER_AREAS` 名字），
C2-int / C3-int 把真件传进来即可，本模块一行不改。
"""

from PySide6 import QtCore, QtWidgets

from dreg_verify.ui import names, terms, theme
from dreg_verify.ui.widgets import ui_font

# ── 本波用到、但 names.py 里还没有的 objectName（C2-int 请并进 names.py）──
PENDING_NAMES = {
    "MAIN_VIEW_PANEL": "main_view_panel",      # 标签条 + 堆叠的外壳（MAIN_VIEW 是里面那个堆叠）
    "MAIN_PAGE_TRUTH": "main_page_truth",      # 堆叠第 0 页（真值表 + 电路图）
}

#: 两个标签的键（顺序 = Design tabDef）
TABS = ("truth", "sv")


def placeholder(object_name, parent=None, min_h=0):
    """占位区：带 objectName，测试能 find 到区位；C2-int / C3-int 换真件。

    与 `ui/app.py` 的 `_placeholder` 同一套画法（白底 + 细边框），名字也沿用 PLACEHOLDER_AREAS。"""
    w = QtWidgets.QWidget(parent)
    w.setObjectName(object_name)
    w.setProperty("placeholder", True)
    w.setAttribute(QtCore.Qt.WA_StyledBackground, True)
    w.setStyleSheet("QWidget#%s{background:%s;border:1px solid %s;}"
                    % (object_name, theme.WHITE, theme.BORDER_LIGHT))
    if min_h:
        w.setMinimumHeight(int(min_h))
    lay = QtWidgets.QVBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    return w


class MainView(QtWidgets.QWidget):
    """⑤ 标签条 + `MAIN_VIEW` 堆叠（页 0 = 真值表/电路图垂直分割；页 1 = .sv 预览）。

    对外信号：
        tabChanged(str key)          —— "truth" / "sv"（组合根据此刷新 .sv 预览、状态栏）；
        flowFullscreenChanged(bool)  —— 电路图全屏开合（C-280）；
        truthMaximizedChanged(bool)  —— 真值表放大（C-297：组合根另行收起右栏）。

    公开 API：`tabs` / `set_tab(key)` / `tab()` / `splitter` / `set_flow_fullscreen(on)` /
    `set_truth_maximized(on)` / `truth_height()` / `set_truth_height(px)` /
    `set_truth_counts(ncols, n, m)`。
    """

    tabChanged = QtCore.Signal(str)
    flowFullscreenChanged = QtCore.Signal(bool)
    truthMaximizedChanged = QtCore.Signal(bool)

    def __init__(self, truth_widget=None, flow_widget=None, sv_widget=None, parent=None):
        super(MainView, self).__init__(parent)
        self.setObjectName(PENDING_NAMES["MAIN_VIEW_PANEL"])
        self._tab = "truth"
        self._flow_full = False
        self._truth_max = False
        self._truth_h = int(theme.TRUTH_H)
        self._build(truth_widget, flow_widget, sv_widget)

    # ═══════════════ 构建 ═══════════════
    def _build(self, truth_widget, flow_widget, sv_widget):
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        # ⑤ 标签条：两个 QToolButton 样式标签（选中态 = 顶部 2px 蓝 + 粗体）
        self.tabs = QtWidgets.QWidget(self)
        self.tabs.setObjectName(names.TABS_BAR)
        self.tabs.setAttribute(QtCore.Qt.WA_StyledBackground, True)
        self.tabs.setStyleSheet("QWidget#%s{background:%s;border-bottom:1px solid %s;}"
                                % (names.TABS_BAR, theme.PANEL_BG, theme.BORDER))
        tl = QtWidgets.QHBoxLayout(self.tabs)
        tl.setContentsMargins(8, 0, 8, 0)
        tl.setSpacing(2)
        self._tab_btns = {}
        for key, obj, text in (("truth", names.TABS_TRUTH, terms.TAB_TRUTH_PLAIN),
                               ("sv", names.TABS_SV, terms.TAB_SV)):
            b = QtWidgets.QToolButton(self.tabs)
            b.setObjectName(obj)
            b.setText(text)
            b.setFont(ui_font())
            b.setCheckable(True)
            b.setCursor(QtCore.Qt.PointingHandCursor)
            b.setFixedHeight(theme.ROW_H + 4)
            b.clicked.connect(lambda _c=False, k=key: self.set_tab(k))
            tl.addWidget(b)
            self._tab_btns[key] = b
        tl.addStretch(1)
        lay.addWidget(self.tabs)

        # ⑤ 堆叠（MAIN_VIEW：测试按 `find(w, names.MAIN_VIEW).currentIndex()` 认页）
        self.stack = QtWidgets.QStackedWidget(self)
        self.stack.setObjectName(names.MAIN_VIEW)

        page = QtWidgets.QWidget(self.stack)
        page.setObjectName(PENDING_NAMES["MAIN_PAGE_TRUTH"])
        pl = QtWidgets.QVBoxLayout(page)
        pl.setContentsMargins(0, 0, 0, 0)
        pl.setSpacing(0)
        self.splitter = QtWidgets.QSplitter(QtCore.Qt.Vertical, page)
        self.splitter.setObjectName(names.WIN_SPLIT_TRUTH_FLOW)
        self.splitter.setChildrenCollapsible(False)                # C-262：两边都拖不没
        self.splitter.setHandleWidth(theme.HANDLE_W)

        self.truth_widget = truth_widget or placeholder(names.TRUTH_PANEL, self.splitter,
                                                        min_h=theme.CLAMP_TRUTH[0])
        self.truth_widget.setMinimumHeight(theme.CLAMP_TRUTH[0])
        self.truth_widget.setMaximumHeight(theme.CLAMP_TRUTH[1])
        self.splitter.addWidget(self.truth_widget)
        self.flow_widget = flow_widget or placeholder(names.FLOW_PANEL, self.splitter, min_h=1)
        self.splitter.addWidget(self.flow_widget)                  # C-280：电路图永远在真值表下方
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)
        pl.addWidget(self.splitter)
        self.stack.addWidget(page)                                 # index 0

        self.sv_widget = sv_widget or placeholder(names.SV_PANEL, self.stack, min_h=1)
        self.stack.addWidget(self.sv_widget)                       # index 1（Ctrl+P，C-255）
        lay.addWidget(self.stack, 1)

        self.set_truth_height(self._truth_h)
        self._sync_tabs()

    # ═══════════════ ⑤ 标签 ═══════════════
    def set_tab(self, key, notify=True):
        """切标签：`"truth"` / `"sv"`（Ctrl+P 由组合根绑到 `set_tab("sv")`，C-255）。"""
        key = key if key in TABS else "truth"
        changed = (key != self._tab)
        self._tab = key
        self.stack.setCurrentIndex(TABS.index(key))
        self._sync_tabs()
        if changed and notify:
            self.tabChanged.emit(key)

    def tab(self):
        return self._tab

    def tab_button(self, key):
        return self._tab_btns.get(key)

    def set_truth_counts(self, ncols=None, n=None, m=None):
        """真值表标签上的「25 列 · 手填 7/25」（C-106 的标签面）。任一为 None → 回到干净文案。"""
        if ncols is None or n is None or m is None:
            self._tab_btns["truth"].setText(terms.TAB_TRUTH_PLAIN)
        else:
            self._tab_btns["truth"].setText(
                terms.TAB_TRUTH_FMT.format(ncols=int(ncols), n=int(n), m=int(m)))

    def _sync_tabs(self):
        for key, b in self._tab_btns.items():
            on = (key == self._tab)
            b.setChecked(on)
            f = b.font()
            f.setBold(on)
            b.setFont(f)
            b.setStyleSheet(
                "QToolButton{background:%s;color:%s;border:0;border-top:2px solid %s;padding:2px 12px;}"
                % ((theme.WHITE, theme.INK, theme.BLUE) if on
                   else (theme.PANEL_BG, theme.MUTE, "transparent")))

    # ═══════════════ ⑥⑦ 垂直分割 / 全屏 ═══════════════
    def truth_height(self):
        """真值表区当前高度（Design 的 `S.truthH`，默认 420，可拖）。"""
        sizes = self.splitter.sizes()
        return int(sizes[0]) if sizes else int(self._truth_h)

    def set_truth_height(self, px):
        lo, hi = theme.CLAMP_TRUTH
        px = max(int(lo), min(int(hi), int(px)))
        self._truth_h = px
        total = max(self.splitter.height(), px + theme.CLAMP_TRUTH[0])
        self.splitter.setSizes([px, max(total - px, 1)])

    def set_flow_fullscreen(self, on):
        """C-280 / 拍板 #8：电路图全屏 = 收起真值表区（标签条留着，随时切回）。"""
        on = bool(on)
        self._flow_full = on
        if on:
            self._truth_h = self.truth_height()
            self._truth_max = False
            self.flow_widget.setVisible(True)
        self.truth_widget.setVisible(not on)
        if not on:
            self.set_truth_height(self._truth_h)
        self.flowFullscreenChanged.emit(on)

    def flow_fullscreen(self):
        return self._flow_full

    def set_truth_maximized(self, on):
        """C-297：真值表放大 = 收起电路图（右栏由组合根同步收起）。"""
        on = bool(on)
        self._truth_max = on
        if on:
            self._flow_full = False
            self.truth_widget.setVisible(True)
        self.flow_widget.setVisible(not on)
        if on:
            self.truth_widget.setMaximumHeight(16777215)
        else:
            self.truth_widget.setMaximumHeight(theme.CLAMP_TRUTH[1])
            self.set_truth_height(self._truth_h)
        self.truthMaximizedChanged.emit(on)

    def truth_maximized(self):
        return self._truth_max
