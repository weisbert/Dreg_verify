# -*- coding: utf-8 -*-
"""truth/panel.py —— 真值表编辑器的**面板**（C3-c，架构 §6.14）。

把 C3 前四件组装成一块能独立起窗的 `TruthPanel(QWidget)`：

    工具条（13 键 + 放大）  →  动作转 model，三处确认走 `ui/dialogs.py`
    mux 头部条              →  case 结构 / 生效档 / 死分支 / iddq 门控 / 撞值（C-124…C-126 / C-113）
    提示条三行              →  选区摘要 · 粘贴说明 · 右键说明（C-293 / C-294 / C-296）
    冻结列 + 网格           →  `view.FrozenNamesView` + `view.TruthTableView` + `delegate.TruthDelegate`
    图例                    →  `terms.TRUTH_LEGEND` 按 `theme.CELL_STATES` 上色
    右键菜单                →  插入 / 删除 / 复制 / 设为反例 / 设置本列 mux 数据值 / 重命名

**面板不算任何东西**：列怎么变归 `model`，格子怎么画归 `delegate`，文件怎么读写归 `io`，
行怎么排归 `rows`，对话框归 `dialogs`。本模块只做三件事——① 把用户动作翻译成 model 调用；
② 把 model / view 的信号翻译成提示条文字与 `state.put_edit`；③ 按 `state` 的当前信号装表。

四条本模块必须守住的线：

1. **I-05 批量写一次盘**：任何会连发多条 `dataChanged` / `colsChanged` 的动作（auto→期望、
   批量填、导入期望、清零、加反例、重新生成）一律裹在 `state.suspend_persist()` 里，
   退出时才落一次盘（`test_i05_put_edit_on_cols_changed_within_suspend_persist` 计调用次数）。
2. **I-18 当前线网只经 bus**：点冻结列一行 → `bus.select(net, "truth")`；收到 `netSelected`
   只改高亮，**绝不回写选择**（回写 = 两个视图互相激发的死循环）。
3. **I-20 名字在前、计数在后**：导入期望没对上的列名、粘贴跳过的格子、加反例跳过的列，
   一律先点名再报数。
4. **装表时不写盘**：`model.load()` 也会发 `colsChanged`，但那是「打开一个信号」不是「用户改了
   什么」——`_loading` 闸门挡住它，否则光是点一遍清单就会给每个信号存一份编辑记录。

依赖（硬性清单，`tests/test_ui_c1_integration.py::test_ui_layering` 扫）：PySide6、
`dreg_verify.edits`（Qt-free，白名单已登记）、`ui.contracts / names / terms / theme / widgets /
dialogs`、`.model / .view / .delegate / .io / .rows`。**不 import** state 实现类（只用
`WorkbenchStateProto` 的面）、不 import `ui.bus`（见 `_net_key`）、不 import 引擎 / gui。
"""

import contextlib
import re

from PySide6 import QtCore, QtGui, QtWidgets

from ... import edits as ED
from ... import inputs_table as IT
from .. import contracts as CT
from .. import dialogs as DLG
from .. import names as N
from .. import terms as T
from .. import theme as TH
from .. import widgets as W
from . import io as TIO
from .delegate import TruthDelegate
from .model import TruthModel
from .rows import e_inputs_from_an
from .view import FrozenNamesView, TruthTableView, bind_frozen

Qt = QtCore.Qt
TR = CT.TruthRole

__all__ = ["TruthPanel", "TOOLBAR_KEYS", "MENU_KEYS", "STATE_REQUIREMENTS"]


#: 工具条 13 键（Design「真值表工具条 13 按钮 + 1 提示条」，C-140）。顺序 = 屏幕上从左到右。
#: `mux_data` 只在 mux 信号上可用，其余 12 键按 `editable_kind()` 统一启停（C-134）。
TOOLBAR_KEYS = ("regen", "add_col", "copy_col", "del_col", "rename_col", "clear",
                "add_neg", "del_neg", "import_exp", "batch_fill", "auto_fill",
                "mux_data", "export_csv")
#: 右键菜单的项（顺序 = `terms.TRUTH_CONTEXT_MENU` 的语义顺序，C-296）
MENU_KEYS = ("insert", "delete", "copy", "set_neg", "mux_data_col", "rename")

#: ⚠ C3-int 起本模块**不再有 `PENDING_NAMES` / `PENDING_TERMS` 影子表**：objectName 只在
#: `ui/names.py`（含动态的 `names.fmt_truth_legend` / `names.fmt_truth_menu`），
#: 文案只在 `ui/terms.py`。粘贴 / 导入期望的**结果说明整句**在 `truth/io.py`，
#: 与 `truth/model.py` 共用同一份（一处改，两处一起变）。


#: 位宽切片（与 `excel_model._WIDTH_RANGE` / `_WIDTH_BIT` 同式：只认**结尾**的 `[3:0]` / `[2]`）
_WIDTH_SLICE = re.compile(r"\[\d+(?:\s*:\s*\d+)?\]\s*$")


def _net_key(name):
    """线网比对键：剥掉结尾的位宽切片 + 小写。

    与 `ui/bus.py::net_key`（= `excel_model._strip_width`）**同口径**——本模块按硬性依赖清单
    不 import `ui.bus`，所以自带一份；两边哪天漂了由
    `tests/test_ui_truth_panel.py::test_i18_net_key_matches_bus`（拿两张 mirror 上全部输入行的
    真名逐个对着 `bus.net_key` 比）当场判红。
    """
    s = str(name or "").strip()
    m = _WIDTH_SLICE.search(s)
    if m:
        s = s[:m.start()]
    return s.strip().lower()


def _case_literal(cv, cw, dc):
    """`expansion["parsed_cases"]` 的一条 → IC 工程师写法的 case 字面量（`3'b1x0`）。

    `parsed_cases` = [(值, 位宽, don't-care 掩码)]（`expr.parse_case_literal` 的产物，
    don't-care 位在 `dc` 里置 1）。`meta["case_map"]` 里才有原文 `case_raw`，而 `an` 不带
    `meta`（见 STATE_REQUIREMENTS 的「引擎层发现」），所以这里按结构化字段还原，不猜原文。
    """
    w = int(cw or 1)
    bits = []
    for i in range(w - 1, -1, -1):
        bits.append("x" if (int(dc or 0) >> i) & 1 else str((int(cv or 0) >> i) & 1))
    return "%d'b%s" % (w, "".join(bits))


class TruthPanel(QtWidgets.QWidget):
    """⑥ 真值表编辑器整块（TRUTH_PANEL 26 条）。

    与外界的往来只有四条线（C3-int 接线就这五行，见模块末尾 `STATE_REQUIREMENTS`）：

      · `set_state(state)`      —— 听 `currentChanged` / `coverageChanged` 自取 an 装表；
      · `set_bus(bus)`          —— 当前线网双向：点冻结列 → `select`，收广播 → 只改高亮（I-18）；
      · `progressChanged(n,m,k)`—— 转发 model 的 C-106，接 `MainView.set_truth_counts` /
                                   `DetailHeader.set_progress`；
      · `maximizeRequested(on)` —— C-297，接 `MainView.set_truth_maximized`（右栏由组合根同步收起）。

    另有 `statusMessage(str)` —— 与 `sv_preview` 同款，接状态栏（C-269）；面板内部所有反馈
    同时写进提示条第一行，所以没接也不丢信息。
    """

    progressChanged = QtCore.Signal(int, int, int)     # C-106（转发 model）
    maximizeRequested = QtCore.Signal(bool)            # C-297
    statusMessage = QtCore.Signal(str)                 # C-269（可选）

    def __init__(self, state=None, bus=None, parent=None):
        QtWidgets.QWidget.__init__(self, parent)
        self.setObjectName(N.TRUTH_PANEL)
        self.state = None
        self.bus = None
        self._conns = []
        self._an = None
        self._name = ""
        self._e_inputs = []
        self._loading = False           # 装表期间不写盘（见模块 docstring 第 4 条）
        self._menu = None
        self._hl_row = -1
        self._net_rows = {}             # 线网比对键 → (输入行下标, 原样真名)

        self.model = TruthModel(self)
        self._build()
        self._wire_model()
        self.model.set_reanalyzer(self._reanalyze_mux)   # C-110（C3-a 的 set_reanalyzer）
        self.set_bus(bus)
        self.set_state(state)

    # ═════════════════════ 一、搭界面 ═════════════════════
    def _build(self):
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(self._build_toolbar())
        lay.addWidget(self._build_mux_header())
        lay.addWidget(self._build_hints())
        lay.addWidget(self._build_grid(), 1)
        lay.addWidget(self._build_legend())
        self.empty = QtWidgets.QLabel(T.TRUTH_EMPTY_NO_SIGNAL, self)
        self.empty.setObjectName(N.TRUTH_EMPTY)
        self.empty.setFont(W.ui_font())
        self.empty.setAlignment(Qt.AlignCenter)
        self.empty.setWordWrap(True)
        self.empty.setStyleSheet("color:%s;background:%s;" % (TH.MUTE, TH.HINT_BG))
        lay.addWidget(self.empty)
        self.setMinimumHeight(TH.CLAMP_TRUTH[0])

    # ── 工具条（C-134 / C-135 / C-140）──
    def _build_toolbar(self):
        bar = QtWidgets.QWidget(self)
        bar.setObjectName(N.TRUTH_TOOLBAR)
        bar.setAttribute(Qt.WA_StyledBackground, True)
        bar.setStyleSheet("QWidget#%s{background:%s;border-bottom:1px solid %s;}"
                          % (N.TRUTH_TOOLBAR, TH.PANEL_BG, TH.BORDER_LIGHT))
        flow = W.FlowLayout(bar, margin=6, hspacing=6, vspacing=4)
        self.buttons = {}
        for key in TOOLBAR_KEYS:
            b = self._make_button(bar, key, getattr(N, "TRUTH_BTN_%s" % key.upper()))
            b.clicked.connect(lambda _c=False, k=key: self._on_action(k))
            flow.addWidget(b)
            self.buttons[key] = b
        # 放大：可勾选，只发信号（真正收起电路图 / 右栏是组合根的事，C-297）
        self.max_btn = self._make_button(bar, "maximize", N.TRUTH_BTN_MAXIMIZE)
        self.max_btn.setCheckable(True)
        self.max_btn.toggled.connect(self._on_maximize)
        flow.addWidget(self.max_btn)
        # C-215：用了 RTL 补充逻辑的信号，名字旁边一颗琥珀点
        self.supplement_dot = QtWidgets.QLabel(T.TRUTH_SUPPLEMENT_DOT_TEXT, bar)
        self.supplement_dot.setObjectName(N.TRUTH_SUPPLEMENT_DOT)
        self.supplement_dot.setFont(W.ui_font(TH.FS_UI_TITLE, bold=True))
        self.supplement_dot.setStyleSheet("color:%s;" % TH.AMBER_STROKE)
        self.supplement_dot.setVisible(False)
        flow.addWidget(self.supplement_dot)
        return bar

    def _make_button(self, parent, key, object_name):
        b = QtWidgets.QPushButton(T.TRUTH_BTN[key], parent)
        b.setObjectName(object_name)
        b.setFont(W.ui_font())
        b.setFixedHeight(TH.BTN_H)
        b.setCursor(Qt.PointingHandCursor)
        b.setFocusPolicy(Qt.NoFocus)          # 焦点留在网格上：点完按钮还能接着按 Ctrl+Z
        stroke = {"auto_fill": TH.AMBER_STROKE, "add_neg": TH.AMBER_BORDER}.get(key, TH.BORDER)
        b.setStyleSheet(
            "QPushButton{background:%s;color:%s;border:1px solid %s;border-radius:3px;"
            "padding:1px 8px;}"
            "QPushButton:disabled{background:%s;color:%s;border:1px solid %s;}"
            % (TH.WHITE, TH.INK, stroke, TH.DISABLED_BG, TH.DISABLED_TEXT, TH.BORDER_LIGHT))
        return b

    # ── mux 头部条（C-124 / C-125 / C-126 / C-113）──
    def _build_mux_header(self):
        box = QtWidgets.QWidget(self)
        box.setObjectName(N.TRUTH_MUX_HEADER)
        box.setAttribute(Qt.WA_StyledBackground, True)
        box.setStyleSheet("QWidget#%s{background:%s;border-bottom:1px solid %s;}"
                          % (N.TRUTH_MUX_HEADER, TH.LIGHT_BLUE_BG, TH.LIGHT_BLUE_BORDER))
        v = QtWidgets.QVBoxLayout(box)
        v.setContentsMargins(10, 4, 10, 4)
        v.setSpacing(1)
        self.mux_labels = {}
        for key, oname, color in (("case", N.TRUTH_MUX_CASE, TH.LIGHT_BLUE_FG),
                                  ("shadowed", N.TRUTH_MUX_SHADOWED, TH.MUTE),
                                  ("gated", N.TRUTH_MUX_GATED, TH.MUTE),
                                  ("collision", N.TRUTH_MUX_COLLISION_HINT, TH.AMBER_FG)):
            lb = QtWidgets.QLabel("", box)
            lb.setObjectName(oname)
            lb.setFont(W.ui_font(TH.FS_UI_SMALL, bold=(key == "case")))
            lb.setWordWrap(True)
            lb.setStyleSheet("color:%s;" % color)
            lb.setVisible(False)
            v.addWidget(lb)
            self.mux_labels[key] = lb
        self.mux_header = box
        box.setVisible(False)
        return box

    # ── 提示条三行 ──
    def _build_hints(self):
        box = QtWidgets.QWidget(self)
        box.setObjectName(N.TRUTH_HINT_BAR)
        box.setAttribute(Qt.WA_StyledBackground, True)
        box.setStyleSheet("QWidget#%s{background:%s;border-bottom:1px solid %s;}"
                          % (N.TRUTH_HINT_BAR, TH.HINT_BG, TH.BORDER_LIGHT))
        v = QtWidgets.QVBoxLayout(box)
        v.setContentsMargins(10, 3, 10, 3)
        v.setSpacing(1)
        self.hint_selection = self._hint_label(box, N.TRUTH_HINT_SELECTION,
                                               T.TRUTH_HINT_SELECTION_NONE, TH.TEXT)
        self.hint_paste = self._hint_label(box, N.TRUTH_HINT_PASTE, T.TRUTH_HINT_PASTE, TH.MUTE)
        self.hint_context = self._hint_label(box, N.TRUTH_HINT_CONTEXT, T.TRUTH_HINT_CONTEXT, TH.MUTE)
        for lb in (self.hint_selection, self.hint_paste, self.hint_context):
            v.addWidget(lb)
        return box

    @staticmethod
    def _hint_label(parent, object_name, text, color):
        lb = QtWidgets.QLabel(text, parent)
        lb.setObjectName(object_name)
        lb.setFont(W.ui_font(TH.FS_UI_SMALL))
        lb.setWordWrap(True)
        lb.setStyleSheet("color:%s;" % color)
        return lb

    # ── 冻结列 + 网格（C-295 / C-107 / C-108）──
    def _build_grid(self):
        split = QtWidgets.QSplitter(Qt.Horizontal, self)
        split.setObjectName(N.TRUTH_SPLIT_NAMES_GRID)
        split.setChildrenCollapsible(False)
        split.setHandleWidth(TH.HANDLE_W)
        split.setStyleSheet("QSplitter::handle{background:%s;}" % TH.HANDLE)
        self.frozen = FrozenNamesView(split)
        self.grid = TruthTableView(split)
        self.grid.setItemDelegate(TruthDelegate(self.grid))   # 显式一次：换 delegate 只此一处
        split.addWidget(self.frozen)
        split.addWidget(self.grid)
        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 1)
        split.setSizes([TH.FROZEN_W, max(TH.FROZEN_W, 1)])    # 冻结列 FROZEN_W，可拖 CLAMP_FROZEN
        self.splitter = split
        # 接法顺序（view.py 明写）：grid.setModel → frozen.set_source → bind_frozen，
        # 且 bind_frozen 只做一次（它每调一次就连一遍信号；model 实例全程不换，不用重连）。
        self.grid.setModel(self.model)
        self.frozen.set_source(self.model, None, [])
        self._frozen_guard = bind_frozen(self.frozen, self.grid)
        return split

    # ── 图例 ──
    def _build_legend(self):
        box = QtWidgets.QWidget(self)
        box.setObjectName(N.TRUTH_LEGEND)
        box.setAttribute(Qt.WA_StyledBackground, True)
        box.setStyleSheet("QWidget#%s{background:%s;border-top:1px solid %s;}"
                          % (N.TRUTH_LEGEND, TH.HINT_BG, TH.BORDER_LIGHT))
        flow = W.FlowLayout(box, margin=6, hspacing=12, vspacing=2)
        self.legend_items = {}
        for key, text in T.TRUTH_LEGEND:
            bg, border, fg, line = TH.CELL_STATES[key]
            item = QtWidgets.QWidget(box)
            item.setObjectName(N.fmt_truth_legend(key))
            h = QtWidgets.QHBoxLayout(item)
            h.setContentsMargins(0, 0, 0, 0)
            h.setSpacing(4)
            sw = QtWidgets.QFrame(item)
            sw.setFixedSize(12, 12)
            sw.setStyleSheet("background:%s;border:1px %s %s;"
                             % (bg, "dashed" if line == "dashed" else "solid", border))
            lb = QtWidgets.QLabel(text, item)
            lb.setFont(W.ui_font(TH.FS_UI_SMALL))
            lb.setStyleSheet("color:%s;" % fg)
            h.addWidget(sw)
            h.addWidget(lb)
            flow.addWidget(item)
            self.legend_items[key] = item
        self.legend = box
        return box

    # ═════════════════════ 二、接线 ═════════════════════
    def _wire_model(self):
        m = self.model
        m.parseFailed.connect(self._say)                       # C-083
        m.pasteReport.connect(self._say)                       # C-294
        m.muxCollision.connect(self._on_mux_collision)         # C-113
        m.progressChanged.connect(self._on_progress)           # C-106
        m.colsChanged.connect(self._persist)                   # I-05
        m.dataChanged.connect(self._on_data_changed)           # I-05
        g = self.grid
        g.copyColumnRequested.connect(self._on_copy_column)     # C-087 / C-109 Ctrl+D
        g.renameRequested.connect(self._on_rename_column)       # C-091 双击列头
        g.contextMenuRequested.connect(self._on_context_menu)   # C-296
        g.selectionSummaryChanged.connect(self._on_selection)   # C-293
        self.frozen.clicked.connect(self._on_frozen_clicked)    # I-18

    def set_bus(self, bus):
        """挂上高亮总线（I-18）。收到广播只改高亮，**绝不回写选择**。"""
        if self.bus is not None:
            try:
                self.bus.netSelected.disconnect(self._on_net_selected)
            except (RuntimeError, TypeError):
                pass
        self.bus = bus
        if bus is not None:
            bus.netSelected.connect(self._on_net_selected)
            self._apply_highlight(getattr(bus, "current_net", ""))

    def set_state(self, state):
        """挂上会话状态（允许先造后接，与 C2 四件一致）。接上就按当前信号装一次表。"""
        for obj, sig, slot in self._conns:
            try:
                getattr(obj, sig).disconnect(slot)
            except (RuntimeError, TypeError):
                pass
        self._conns = []
        self.state = state
        if state is not None:
            # C5a-2：`negsChanged` 也要听 —— 清单上勾「反例」那一格之后，正看着的这个信号
            # 的真值表当场就该多出那条 `_NEG` 列（v1 2026-06-10 修过的用户报障，v2 复发了）。
            # **不会自激**：本槽只走 `refresh()` → `show_signal()`，而 `show_signal` 装表全程
            # `_loading=True`，`model.load` 发出来的 `colsChanged` 被 `_persist` 的闸门挡掉，
            # 于是不会再有 `put_edit` → `negsChanged` 这一圈。
            for sig, slot in (("currentChanged", self._on_current_changed),
                              ("coverageChanged", self._on_refresh_signal),
                              ("configChanged", self._on_refresh_signal),
                              ("scopeChanged", self._on_refresh_signal),
                              ("negsChanged", self._on_refresh_signal),
                              ("workbookChanged", self._on_refresh_signal)):
                s = getattr(state, sig, None)
                if s is not None:
                    s.connect(slot)
                    self._conns.append((state, sig, slot))
        self.refresh()

    # ═════════════════════ 三、装表 ═════════════════════
    def refresh(self):
        """按 `state` 的当前信号重取 an 并装表（分析失败只落一行文案，绝不抛到组合根）。"""
        st = self.state
        name = str(getattr(st, "current_name", "") or "") if st is not None else ""
        if not name:
            self.show_empty()
            return
        try:
            an = st.analyze(name)                    # C2-int：不带图（电路图那份另取）
        except Exception:                            # noqa: BLE001 —— C-043：失败也只是一行字
            an = None
        if not an:
            self.show_empty(T.TRUTH_EMPTY_FAILED)
            return
        self.show_signal(name, an)

    def show_signal(self, name, an, cols=None):
        """装一个信号的表（`state` 有编辑记录就用记录里的列，否则出厂默认）。

        C-154（C5a-1）：**只加过反例、正向一格没动过**的信号是个例外 —— 它的正向列还是
        「引擎按某一档出的那份」，换了覆盖度档就该跟着重算，再把反例补回去。此前 `set_neg`
        写下的整份 cols 被这里原样取回，于是那个信号的正向永远冻在勾反例那一刻的档位上
        （精简 9 列 → 拧到穷举还是 9 列，导出侧同样冻着）。手填过期望 / 加过列 / 删过列的
        信号仍然冻结不动（C-153：那是他的活，拧全局档不许冲掉）。
        """
        self._name = str(name or "")
        self._an = an or {}
        self._e_inputs = e_inputs_from_an(self._an)
        resynced = False
        if cols is None:
            ed = None
            if self.state is not None and hasattr(self.state, "edit_of"):
                ed = self.state.edit_of(self._name)
            if ed and ed.get("cols") is not None:
                fresh = ED.cov_resync_cols(self._an, self._e_inputs,
                                           ed.get("an"), ed["cols"])
                cols = fresh if fresh is not None else list(ed["cols"])
                resynced = fresh is not None
            else:
                cols = ED.cols_from_vectors(self._an, self._e_inputs)
        self._loading = True
        try:
            self.model.load(self._an, cols, self._e_inputs)
            # view.py 明写：每次 `model.load` 之后都要再 `set_source`（元信息跟着信号换）
            self.frozen.set_source(self.model, self._an, self._e_inputs)
        finally:
            self._loading = False
        if resynced:
            self._persist()          # 重算过 = 记录真的变了，state 与屏幕不许各说各话
        self._net_rows = self._build_net_rows()
        self.empty.setVisible(False)
        self.splitter.setVisible(True)
        self._sync_toolbar()
        self._sync_mux_header()
        self._sync_supplement()
        self._on_selection("", 0)
        self._apply_highlight(getattr(self.bus, "current_net", "") if self.bus else "")

    def show_empty(self, message=None):
        """空态：没选信号 / 分析失败。表清空（`load` 是 model 唯一允许 reset 的地方）。

        ⚠ 名字不叫 `clear()`：`tests/test_ui_truth_model.py::test_i15_no_clear_no_reset_outside_load`
        对整个 `ui/truth/` 做源码扫，**任何** `xxx.clear()` 调用都判红（v1 `_populate_truth` 的
        `tbl.clear()` 是列宽被弹回的根因）。与 `side_panel.show_message` 同一套叫法。
        """
        self._name = ""
        self._an = None
        self._e_inputs = []
        self._net_rows = {}
        self._loading = True
        try:
            self.model.load({}, [], [])
            self.frozen.set_source(self.model, None, [])
        finally:
            self._loading = False
        self.empty.setText(message or T.TRUTH_EMPTY_NO_SIGNAL)
        self.empty.setVisible(True)
        self.splitter.setVisible(False)
        self.mux_header.setVisible(False)
        self.supplement_dot.setVisible(False)
        self._sync_toolbar()
        self._on_selection("", 0)

    def current_an(self):
        return self._an

    def current_name(self):
        return self._name

    def _build_net_rows(self):
        """输入行下标 ↔ 线网（I-18 两个方向都要）。名字取 `inputs_table.input_rows` 的真名。"""
        by_key = {}
        for row in (IT.input_rows(self._an) if self._an else []):
            by_key.setdefault(row.get("key"), row)
        out = {}
        for i, e in enumerate(self._e_inputs):
            row = by_key.get(e.get("key")) or {}
            nm = str(row.get("name") or "")
            key = _net_key(nm)
            if key:
                out.setdefault(key, (i, nm))
        return out

    # ═════════════════════ 四、工具条状态（C-134 / C-135）═════════════════════
    def _sync_toolbar(self):
        kind = self.model.editable_kind()
        tips = T.TRUTH_BTN_TIPS_MUX if kind == "mux" else T.TRUTH_BTN_TIPS_LOGIC
        for key, b in self.buttons.items():
            on = bool(kind) if key != "mux_data" else (kind == "mux")
            b.setEnabled(on)
            b.setToolTip(tips.get(key) or T.TRUTH_BTN_TIPS_LOGIC.get(key) or T.TRUTH_BTN[key])
        self.max_btn.setToolTip(T.TRUTH_BTN["maximize"])

    def _sync_supplement(self):
        """C-215：这个信号用了 RTL 补充逻辑 → 琥珀点 + 说明（判据只看 `an["sig"]` 的结构化标记）。"""
        sig = (self._an or {}).get("sig")
        on = bool(getattr(sig, "_is_supplement", False))
        self.supplement_dot.setVisible(on)
        if on:
            note = T.scrub(str(getattr(sig, "_supplement_note", "") or ""))
            self.supplement_dot.setToolTip(T.LIST_SUPPLEMENT_TIP.format(note=note))

    # ═════════════════════ 五、mux 头部条（C-124/125/126/113）═════════════════════
    def _sync_mux_header(self):
        an = self._an or {}
        if an.get("editable") != "mux":
            self.mux_header.setVisible(False)
            for lb in self.mux_labels.values():
                lb.setText("")
                lb.setVisible(False)
            return
        self._set_mux_line("case", self._mux_case_text())
        self._set_mux_line("shadowed", self._mux_shadowed_text())
        self._set_mux_line("gated", self._mux_gated_text())
        self._set_mux_line("collision", self._mux_collision_text())
        self.mux_header.setVisible(True)

    def _set_mux_line(self, key, text):
        lb = self.mux_labels[key]
        lb.setText(text or "")
        lb.setVisible(bool(text))

    def _mux_case_text(self):
        """C-124：case 结构 + 生效档 + 该档怎么展开 + 手填进度。"""
        an = self._an or {}
        exp = an.get("expansion") or {}
        cases = list(exp.get("parsed_cases") or [])
        # case 结构：跨页展开链里有 mux 层就用它的原式，没有（本信号就是 mux 根）用根的 expr
        expr = ""
        for c in (an.get("chain") or []):
            if str(c.get("kind") or c.get("page") or "") == "mux":
                expr = str(c.get("expr") or "")
                break
        if not expr:
            expr = str(getattr(an.get("sig"), "expr", "") or "")
        head = self._case_head(expr, len(cases))
        label, _src = self._effective_label()
        n, m, _k = self.model.fill_progress()
        if not label:            # 没接会话档（独立起窗）→ 不编一个假的档位名出来
            return T.TRUTH_MUX_HEADER_NOCOV_FMT.format(case_desc=head, n=n, m=m)
        how = T.TRUTH_MUX_HOW.get(label, "")
        return T.TRUTH_MUX_HEADER_FMT.format(case_desc=head, cov=label, how=how, n=n, m=m)

    @staticmethod
    def _case_head(expr, n_cases):
        """`case(sel){...}` 的原式 → 头部那一句。取不到控制口就只说「mux 选路」，不猜。"""
        txt = T.scrub(str(expr or "").strip())
        i, j = txt.find("("), txt.find(")")
        sel = txt[i + 1:j].strip() if 0 <= i < j else ""
        if not sel:
            return T.TRUTH_MUX_CASE_UNKNOWN
        return T.TRUTH_MUX_CASE_FMT.format(expr=sel, n=int(n_cases or 0))

    def _mux_shadowed_text(self):
        """C-125：靠后重复 case 被跳过 → 点名是哪几条（`expansion["shadowed"]`，没有 meta 层）。"""
        exp = (self._an or {}).get("expansion") or {}
        idx = sorted(int(i) for i in (exp.get("shadowed") or ()))
        if not idx:
            return ""
        cases = list(exp.get("parsed_cases") or [])
        shown = [_case_literal(*cases[i]) if 0 <= i < len(cases) else "#%d" % (i + 1)
                 for i in idx]
        return T.TRUTH_MUX_SHADOWED_FMT.format(cases="、".join(shown))

    def _mux_gated_text(self):
        """C-126：受 dft 页 iddq 门控 + 门能不能 force（`binding.kind == "RO"`）。"""
        gate = (self._an or {}).get("dft_gate")
        if not gate:
            return ""
        forceable = getattr(gate.get("binding"), "kind", "") == "RO"
        return T.TRUTH_MUX_GATED_FMT.format(
            gate=T.scrub(str(gate.get("label") or "")),
            forceable=(T.TRUTH_MUX_GATE_FORCEABLE if forceable
                       else T.TRUTH_MUX_GATE_NOT_FORCEABLE))

    def _mux_collision_text(self):
        """C-113：手填数据值撞值。判据 = `an["status_detail"] == "false-green"`
        （引擎的 `meta["override_collision"]` 已归到这一档；`an` 不带 `meta`）。"""
        return (T.TRUTH_MUX_COLLISION
                if (self._an or {}).get("status_detail") == "false-green" else "")

    def _on_mux_collision(self, text):
        """model 重分析后现场发的撞值提示（与头部条同一句，两条路都点得亮这一行）。"""
        self._set_mux_line("collision", text or "")
        if text:
            self.mux_header.setVisible(True)
            self._say(text)

    def _effective_label(self):
        """本信号的生效覆盖档 (中文档名, 来源)。没接 state → 用全局默认的中文名兜底。"""
        st = self.state
        cov = st.coverage() if (st is not None and hasattr(st, "coverage")) else None
        if cov is None:
            return "", ""
        models = list(st.models() or []) if hasattr(st, "models") else []
        return cov.effective_label(self._name, models=models)

    def _cov_args(self):
        """算出这份 `an` 的那一档覆盖度 → `truth/io.py` 的 `cov`（键 = `io.COV_KEYS`）。

        与 `ui/sv_preview.py::_cov_args()` 的「本信号」分支同口径：单点 > 逻辑类型 > 全局
        已由 `CoverageState.mode_for` 折平，两本字典不再往下传。
        """
        st = self.state
        cov = st.coverage() if (st is not None and hasattr(st, "coverage")) else None
        if cov is None:
            return None
        models = list(st.models() or []) if hasattr(st, "models") else []
        mode, exh = cov.mode_for(self._name, models=models)
        return {"mode": mode, "max_tests": int(cov.max_tests), "exhaustive": bool(exh),
                "sig_cov": None, "form_cov": None}

    # ═════════════════════ 六、提示条 / 进度 / 存盘 ═════════════════════
    def _say(self, text):
        """一切反馈的唯一出口：提示条第一行 + 状态栏（都过 `terms.scrub`，I-12）。"""
        txt = T.scrub(str(text or ""))
        self.hint_selection.setText(txt or T.TRUTH_HINT_SELECTION_NONE)
        if txt:
            self.statusMessage.emit(txt)

    def hint_text(self):
        """提示条三行（测试与 C3-int 都按它读，不去 find 三个标签）。"""
        return [self.hint_selection.text(), self.hint_paste.text(), self.hint_context.text()]

    def _on_selection(self, col, n):
        """C-293：选区摘要。单列给列名，跨列给列数——两种都要说得出「选了多少」。"""
        if not n:
            self.hint_selection.setText(T.TRUTH_HINT_SELECTION_NONE)
            return
        if col:
            self.hint_selection.setText(T.TRUTH_HINT_SELECTION_FMT.format(col=col, n=int(n)))
            return
        ncol = len(self.selected_columns(fallback_current=False)) or 1
        self.hint_selection.setText(
            T.TRUTH_HINT_SELECTION_MULTI_FMT.format(n=int(n), ncol=ncol))

    def _on_progress(self, n, m, k):
        self.progressChanged.emit(int(n), int(m), int(k))

    def _on_data_changed(self, _top_left, _bottom_right, roles=()):
        """只有**真的改了数据**才回写（I-05）。

        `model.set_current_col` 也发 `dataChanged`，但它带 `roles=[IS_CURRENT_COL]`——那是
        「当前列高亮换了一列」，不是编辑。不滤掉的话，用户在表上点一下方向键就存一次盘。
        """
        if roles:
            return
        self._persist()

    def _persist(self):
        """列或格子变了 → `state.put_edit`（记录结构 = `edits.compute_edited` 认识的那份）。"""
        if self._loading or self.state is None or not self._an or not self._name:
            return
        if not hasattr(self.state, "put_edit"):
            return
        an = self._an
        self.state.put_edit(self._name, {
            "kind": an["kind"], "src_out_name": an["src_out_name"], "name": an["name"],
            "renamed": an.get("renamed", False), "cols": self.model.cols(), "an": an})

    @contextlib.contextmanager
    def _bulk(self):
        """批量动作：挂起逐格存盘，退出时统一写一次（I-05 / C-244）。没接 state 时是空壳。"""
        st = self.state
        if st is None or not hasattr(st, "suspend_persist"):
            yield
            return
        with st.suspend_persist():
            yield

    # ═════════════════════ 七、选区 / 当前列 ═════════════════════
    def current_column(self):
        c = self.grid.currentIndex().column()
        return c if 0 <= c < self.model.columnCount() else -1

    def selected_columns(self, fallback_current=True):
        """选中的列下标（升序）。没选区时按当前列兜底（按钮都是对「当前这一列」说话的）。"""
        sm = self.grid.selectionModel()
        cs = sorted({ix.column() for ix in (sm.selectedIndexes() if sm is not None else [])})
        if cs:
            return cs
        c = self.current_column()
        return [c] if (fallback_current and c >= 0) else []

    # ═════════════════════ 八、工具条动作 ═════════════════════
    def _on_action(self, key):
        getattr(self, "_do_%s" % key)()

    # ── C-085 重新生成 ──
    def _do_regen(self):
        """重新生成 = **丢弃这个信号的全部自定义**，按当前覆盖度重出（C-085 / C-119）。

        P-01：v1 `_e_regen` 第一行就是 `edits.pop(name)`；v2 这里不但不删，重出之后
        `colsChanged` 还把「引擎默认列集」当成一份编辑 `put_edit` 回去 —— 于是
        「什么都不改按一次重新生成」也会在 `state.edits` 里留下一条记录，落盘、重启还在。
        本身不该有可见后果（那份列集就是引擎默认），但它把「有没有编辑」这件事说谎了，
        而 `compute_edited` 走的是另一条路（mux 的 `expected` 按键号打补丁），一旦哪条
        列的期望来源不是 designer（比如 iddq 漏电态自检拍），补丁就落到别的拍头上。

        所以这里两件事一起做：先 `drop_edit` 去掉记录，再在**不回写**的状态下重出。
        之后用户真改了哪一格，`_persist` 自然会把记录建回来；`Ctrl+Z` 撤销重新生成时
        `Regenerate.undo` 也照常发 `colsChanged`，老列集连同记录一起回来。
        """
        st = self.state
        an = self._an
        if st is not None and self._name and hasattr(st, "analyze"):
            an = st.analyze(self._name) or an       # 覆盖度可能已经改过，按现在这一档重出
        if not an:
            return
        if st is not None and self._name and hasattr(st, "drop_edit"):
            st.drop_edit(self._name)
        self._an = an
        self._e_inputs = e_inputs_from_an(an)
        was_loading, self._loading = self._loading, True    # 重出本身不算「用户改了什么」
        try:
            with self._bulk():
                self.model.regenerate(an, self._e_inputs)
        finally:
            self._loading = was_loading
        self.frozen.set_source(self.model, an, self._e_inputs)
        self._net_rows = self._build_net_rows()
        self._sync_toolbar()
        self._sync_mux_header()
        self._say(T.TRUTH_REGEN_DONE_FMT.format(n=self.model.columnCount()))

    # ── C-086 加列 ──
    def _do_add_col(self):
        c = self.current_column()
        names = self.model.append_test_column(1, src_idx=(c if c >= 0 else None))
        if not names:
            self._say(T.TRUTH_NO_COLUMN)
            return
        self._say(T.TRUTH_ADD_COL_DONE_FMT.format(names="、".join(names)))
        self._sync_mux_header()

    # ── C-087 / C-109 复制列 ──
    def _do_copy_col(self):
        c = self.current_column()
        if c < 0:
            self._say(T.TRUTH_NO_COLUMN)
            return
        self._on_copy_column(c)

    def _on_copy_column(self, c):
        """Ctrl+D 与工具条走同一条路（视图只发信号，写入在这里，见 view.py 第 1 条线）。"""
        at, name = self.model.duplicate_column(int(c))
        if at < 0:
            self._say(T.TRUTH_NO_COLUMN)
            return
        self._say(T.TRUTH_ADD_COL_DONE_FMT.format(names=name))
        self._sync_mux_header()

    # ── C-088 删列 ──
    def _do_del_col(self):
        cs = self.selected_columns()
        if not cs:
            self._say(T.TRUTH_NO_COLUMN)
            return
        names = [self.model.all_names()[i] for i in cs]
        with self._bulk():
            gone = self.model.remove_columns(cs)
        self._say(T.TRUTH_DEL_COL_DONE_FMT.format(names="、".join(names), n=len(gone)))
        self._sync_mux_header()

    # ── C-091 / C-092 / C-093 重命名列 ──
    def _do_rename_col(self):
        c = self.current_column()
        if c < 0:
            self._say(T.TRUTH_NO_COLUMN)
            return
        self._on_rename_column(c)

    def _on_rename_column(self, c):
        """双击列头与工具条同一条路。自动生成的 T 列先说清为什么不给改，不白弹一个框（C-093）。"""
        c = int(c)
        cols = self.model.cols()
        if not (0 <= c < len(cols)):
            self._say(T.TRUTH_NO_COLUMN)
            return
        col = cols[c]
        if not col.get("user"):
            self._say(T.TRUTH_RENAME_AUTO_REFUSED)
            return
        others = [x["name"] for i, x in enumerate(cols) if i != c]
        got = DLG.RenameColumnDialog.ask(col.get("name") or "", others=others,
                                         negative=bool(col.get("neg")), parent=self)
        if got is None:
            return
        ok, info = self.model.rename_column(c, got)
        self._say(info if not ok else self.model.all_names()[c])

    # ── C-089 / C-090 清零 ──
    def _do_clear(self):
        if not DLG.confirm(DLG.CONFIRM_CLEAR, parent=self):
            return
        with self._bulk():
            self.model.clear_all()
        self._say(T.TRUTH_CLEAR_DONE)
        self._sync_mux_header()

    # ── C-096 / C-101 / C-122 加反例 ──
    def _do_add_neg(self):
        cs = self.selected_columns(fallback_current=False)
        note = ""
        if not cs:
            note = T.TRUTH_ADD_NEG_NO_SELECTION + "；"     # 无选中 → 取首条正向（C-096）
        with self._bulk():
            n, skipped = self.model.add_negatives(cs)
        msg = note + T.TRUTH_ADD_NEG_DONE_FMT.format(n=n)
        if skipped:
            msg += T.TRUTH_ADD_NEG_SKIPPED_FMT.format(n=skipped)
        self._say(msg)
        self._sync_mux_header()

    # ── C-102 / C-103 删反例 ──
    def _do_del_neg(self):
        prot = self.model.protected_negatives()
        if prot:                                   # 自定义命名 / 手填过错值 → 先点名再确认
            names = DLG.protected_negative_names(prot)
            if not DLG.confirm(DLG.CONFIRM_DEL_NEG, n=len(prot), names=names, parent=self):
                return
        with self._bulk():
            n = self.model.del_negatives()
        self._say(T.TRUTH_DEL_NEG_DONE_FMT.format(n=n))
        self._sync_mux_header()

    # ── C-094 / C-095 auto→期望 ──
    def _do_auto_fill(self):
        if not DLG.confirm(DLG.CONFIRM_AUTO_FILL, parent=self):
            return
        with self._bulk():
            n = self.model.fill_expected()
        self._say(T.TRUTH_AUTO_FILL_DONE_FMT.format(n=n))

    # ── C-298 导入期望 ──
    def _do_import_exp(self):
        path, _f = QtWidgets.QFileDialog.getOpenFileName(
            self, T.TRUTH_IMPORT_EXP_TITLE, "", T.TRUTH_IMPORT_EXP_FILTER)
        if not path:
            return
        try:
            by_name, notes = TIO.read_expectations(path)
        except Exception as ex:                    # noqa: BLE001 —— 坏文件 / 占用中，绝不崩
            self._say(T.TRUTH_IMPORT_READ_FAILED_FMT.format(err=T.exc_text(ex, path)))
            return
        with self._bulk():
            n, missing = self.model.apply_expectations(by_name)
        # 结果说明整句在 `truth/io.py`（C3-int 去重）：逐条原因 + 没对上的列名在前、计数在后
        # （I-20）。`model.import_expectations` 走的是同一份，两条路说出来的话一字不差。
        self._say(TIO.import_report_text(n, missing, notes))

    # ── C-298 批量填 ──
    def _do_batch_fill(self):
        cs_sel = self.selected_columns(fallback_current=False)
        spec = DLG.BatchFillDialog.ask(n_selected=len(cs_sel),
                                       n_total=self.model.columnCount(), parent=self)
        if not spec:
            return
        cs = cs_sel if spec.get("scope") == "selected" else list(range(self.model.columnCount()))
        if spec.get("mode") == "const":
            with self._bulk():
                _n, msg = self.model.batch_fill(cs, "%d" % int(spec.get("value") or 0))
            self._say(msg)
            return
        # 取 auto / 清空：没有专门的 model 方法，走唯一写入口 `setData` + 一个宏（仍是一步撤销）
        with self._bulk():
            n = self._fill_from(cs, auto=(spec.get("mode") == "auto"))
        self._say(T.TRUTH_BATCH_FILL_NOTHING if not n
                  else T.TRUTH_BATCH_FILL_REPORT_FMT.format(n=n))

    def _fill_from(self, cs, auto):
        """把选中列的期望格设成 auto_out（auto=True）或清空（auto=False）。一步撤销。"""
        m = self.model
        exp_r, auto_r = m.rowCount() - 1, m.rowCount() - 2
        stack = m.undo_stack()
        stack.beginMacro(T.TRUTH_BTN["batch_fill"])
        n = 0
        for c in sorted({int(x) for x in (cs or ()) if 0 <= int(x) < m.columnCount()}):
            txt = str(m.data(m.index(auto_r, c), Qt.DisplayRole) or "") if auto else ""
            if m.setData(m.index(exp_r, c), txt, Qt.EditRole):
                n += 1
        stack.endMacro()
        return n

    # ── C-110 mux 数据值整表 ──
    def _do_mux_data(self):
        rows = self._mux_data_rows()
        got = DLG.MuxDataDialog.ask(rows, parent=self)
        if not got:
            return
        widths = {r["base"]: r["width"] for r in rows}
        with self._bulk():
            for base, val in got.items():
                self.model.set_mux_data_value(
                    base, "" if val is None else DLG.fmt_value(val, widths.get(base, 1)))
        self._sync_mux_header()

    def _mux_data_rows(self):
        """`MuxDataDialog` 的行：每个**物理数据寄存器**一行（名 / 位宽 / 当前值 / 已手填值）。"""
        ov = {}
        if self.state is not None and hasattr(self.state, "mux_data"):
            ent = (self.state.mux_data() or {}).get(str(self._name).lower()) or {}
            ov = dict(ent.get("data") or {})
        rows, seen = [], set()
        m = self.model
        for i, e in enumerate(self._e_inputs):
            base = e.get("mux_data_base")
            if not base or base in seen:
                continue
            seen.add(base)
            cur = (m.data(m.index(i, 0), int(TR.RAW_VALUE)) if m.columnCount() else None)
            rows.append({"base": base, "label": T.scrub(str(e["label"])), "width": e["width"],
                         "value": cur, "override": ov.get(base)})
        return rows

    # ── C-136…C-140 导出 CSV ──
    def _do_export_csv(self):
        path, _f = QtWidgets.QFileDialog.getSaveFileName(
            self, T.TRUTH_EXPORT_CSV_TITLE, "%s.csv" % (self._name or "truth"),
            T.TRUTH_EXPORT_CSV_FILTER)
        if not path:
            return
        st = self.state
        provider = st.provider() if (st is not None and hasattr(st, "provider")) else None
        edited = st.compute_edited() if (st is not None and hasattr(st, "compute_edited")) else None
        cov = self._cov_args()
        try:
            # 先问一次列（C-139：mux 要的是**产物**的列，拿它判「这次有没有产物」），
            # 再把**同一份列**交给写文件 —— C3-int 给 `export_signal_csv` 加了 `cols=`，
            # 不用为了同一件事 render 两遍 .sv，也不会出现「判断用 A 份、写出去 B 份」。
            cols = TIO.signal_csv_columns(self.model, self._an, self._name,
                                          provider=provider, edited=edited, cov=cov)
            TIO.export_signal_csv(path, self.model, self._an, self._name,
                                  provider=provider, edited=edited, cov=cov, cols=cols)
        except Exception as ex:                    # noqa: BLE001 —— 盘满 / 文件被占用，绝不崩
            self._say(T.TRUTH_EXPORT_FAILED_FMT.format(err=T.exc_text(ex, path)))
            return
        note = ""
        if self.model.editable_kind() == "mux" and self._is_editor_table(cols):
            note = T.TRUTH_EXPORT_MUX_NO_BUILD + "；"      # C-136…C-140：没产物就照实说
        self._say(note + T.TRUTH_EXPORT_DONE_FMT.format(path=path, n=len(cols)))

    def _is_editor_table(self, cols):
        """这份 CSV 的列是不是「编辑器这张表」（= mux 产物没取到，`io` 退回了 model 的列）。

        判据是**向量对象的身份**，不是列数：产物列的 `vec` 来自那次 build（新对象），
        退回的那份就是 model 手上这一批。列数相同可能只是巧合，身份不会。
        """
        mine = self.model.cols()
        if not mine or len(cols) != len(mine):
            return False
        return all(c.get("vec") is d.get("vec") for c, d in zip(cols, mine))

    # ── C-297 放大 ──
    def _on_maximize(self, on):
        self.max_btn.setText(T.TRUTH_BTN["restore" if on else "maximize"])
        self.maximizeRequested.emit(bool(on))

    def set_maximized(self, on):
        """组合根反向同步（电路图全屏时把放大按钮弹回来）。不重复发信号。"""
        self.max_btn.blockSignals(True)
        self.max_btn.setChecked(bool(on))
        self.max_btn.setText(T.TRUTH_BTN["restore" if on else "maximize"])
        self.max_btn.blockSignals(False)

    # ═════════════════════ 九、右键菜单（C-296 / C-111）═════════════════════
    def build_context_menu(self, r, c):
        """右键菜单（**只建不弹**，测试按 `menu.actions()` 断言）。

        项的可用性按「这一格是什么」定：没有列 → 只有「插入列」可点；自动生成的列不给改名
        （C-093）；「设置本列 mux 数据值」只对 mux 的**手编列**、且右键点在数据行上时可用（C-111）。
        """
        menu = QtWidgets.QMenu(self)
        menu.setObjectName(N.TRUTH_CONTEXT_MENU)
        kind = self.model.editable_kind()
        cols = self.model.cols()
        has_col = 0 <= int(c) < len(cols)
        col = cols[int(c)] if has_col else {}
        mux_key = self._mux_data_key(int(r)) if kind == "mux" else None
        enabled = {
            "insert": bool(kind),
            "delete": bool(kind) and has_col,
            "copy": bool(kind) and has_col,
            "set_neg": bool(kind) and has_col and not col.get("neg"),
            "mux_data_col": kind == "mux" and has_col and bool(col.get("user")) and bool(mux_key),
            "rename": bool(kind) and has_col and bool(col.get("user")),
        }
        self._menu_acts = {}
        for key in MENU_KEYS:
            act = menu.addAction(T.TRUTH_CONTEXT_MENU[key])
            act.setObjectName(N.fmt_truth_menu(key))
            act.setEnabled(bool(enabled[key]))
            act.triggered.connect(lambda _x=False, k=key, rr=int(r), cc=int(c):
                                  self._on_menu(k, rr, cc))
            self._menu_acts[key] = act
        return menu

    def _mux_data_key(self, r):
        """右键点的那一行是不是 mux 的**数据**行 → 该行的赋值键（不是就 None）。"""
        if not (0 <= r < len(self._e_inputs)):
            return None
        e = self._e_inputs[r]
        return e["key"] if e.get("mux_data_base") else None

    def _on_context_menu(self, global_pos, r, c):
        """视图只发「在哪一格按的」，菜单在这里建、在这里弹（`popup` 不阻塞，offscreen 也安全）。"""
        self.close_context_menu()
        self._menu = self.build_context_menu(r, c)
        self._menu.popup(global_pos if isinstance(global_pos, QtCore.QPoint)
                         else QtGui.QCursor.pos())
        return self._menu

    def context_menu(self):
        return self._menu

    def close_context_menu(self):
        if self._menu is not None:
            self._menu.close()
            self._menu.deleteLater()
            self._menu = None

    def _on_menu(self, key, r, c):
        self.close_context_menu()
        if key == "insert":
            self.model.append_test_column(1, src_idx=(c if c >= 0 else None))
            self._sync_mux_header()
        elif key == "delete":
            self._do_del_col_at(c)
        elif key == "copy":
            self._on_copy_column(c)
        elif key == "set_neg":
            with self._bulk():
                n, skipped = self.model.add_negatives([c])
            msg = T.TRUTH_ADD_NEG_DONE_FMT.format(n=n)
            if skipped:
                msg += T.TRUTH_ADD_NEG_SKIPPED_FMT.format(n=skipped)
            self._say(msg)
        elif key == "mux_data_col":
            self._set_mux_col_data(r, c)
        elif key == "rename":
            self._on_rename_column(c)

    def _do_del_col_at(self, c):
        if not (0 <= c < self.model.columnCount()):
            self._say(T.TRUTH_NO_COLUMN)
            return
        name = self.model.all_names()[c]
        with self._bulk():
            gone = self.model.remove_columns([c])
        self._say(T.TRUTH_DEL_COL_DONE_FMT.format(names=name, n=len(gone)))
        self._sync_mux_header()

    def _set_mux_col_data(self, r, c):
        """C-111：只改**本列**这个数据源（auto_out 按路由 case 的数据源重算）。

        复用 `MuxDataDialog`（只放这一根物理寄存器一行）——本波不许改 `ui/dialogs.py`，
        而这件事要的正是它那套「8 种写法 + 非法就地标红不关框」。
        """
        key = self._mux_data_key(r)
        if key is None:
            return
        e = self._e_inputs[r]
        cur = self.model.data(self.model.index(r, c), int(TR.RAW_VALUE))
        got = DLG.MuxDataDialog.ask([{"base": e["mux_data_base"], "label": T.scrub(str(e["label"])),
                                      "width": e["width"], "value": cur, "override": cur}],
                                    parent=self)
        if not got:
            return
        val = got.get(e["mux_data_base"])
        self.model.set_mux_user_data(c, key, "" if val is None
                                     else DLG.fmt_value(val, e["width"]))

    # ═════════════════════ 十、C-110 的 reanalyzer ═════════════════════
    def _reanalyze_mux(self, base_low, text):
        """`model.set_reanalyzer` 的回路：写 `state.mux_data()` 桶 → 重分析本信号 → 新 an。

        与 v1 `gui.SignalView._set_mux_data` 同一条路（`edits.set_mux_data_value` 是唯一写入口，
        `src_out_name` 按 v1 取小写）。`text` 已由 model 校验 + 掩码 + 规范化；空串 = 清掉该
        物理基名、恢复引擎的自动互异分配。撤销/重做走同一个函数（传回旧文本），所以
        **面板不在 model 之外改 `state.mux_data()`** —— 改了 model 手上那份撤销文本就对不上了。
        """
        st, an, name = self.state, self._an, self._name
        if st is None or not an or not name or not hasattr(st, "mux_data"):
            return None
        width = next((e["width"] for e in self._e_inputs
                      if (e.get("mux_data_base") or "") == str(base_low or "").lower()), 1)
        try:
            ED.set_mux_data_value(st.mux_data(), str(name).lower(),
                                  str(an["src_out_name"]).lower(), str(an["name"]),
                                  str(base_low or "").lower(), int(width or 1), text)
        except ValueError:                   # model 已经校验过；真走到这儿只说明位宽对不上
            return None
        if hasattr(st, "mux_data_touched"):  # R2-01：数据值手填也要落盘
            st.mux_data_touched(name)
        new_an = st.analyze(name)
        if new_an is not None:
            self._an = new_an
        return new_an

    # ═════════════════════ 十一、当前线网（I-18 / C-282）═════════════════════
    def _on_frozen_clicked(self, index):
        """点冻结列一行 → 广播这根线网（origin="truth"）。没挂总线时本地也要看得见。"""
        r = index.row() if index is not None and index.isValid() else -1
        net = next((nm for (i, nm) in self._net_rows.values() if i == r), "")
        if not net:
            return
        if self.bus is not None:
            self.bus.select(net, "truth")
        else:
            self._apply_highlight(_net_key(net))

    def _on_net_selected(self, net, origin):
        """收到广播只更新高亮——**不**回写选择（I-18：回写就是两个视图互相激发的死循环）。"""
        self._apply_highlight(net)

    def _apply_highlight(self, net):
        """同名输入行高亮：冻结列选中该行，网格跟着滚到那一行（滚动由 `bind_frozen` 同步）。

        刻意**不动网格的选区** —— 选区是用户正在编辑的那片格子，被高亮顺手清掉就等于打断编辑。
        """
        hit = self._net_rows.get(_net_key(net)) if net else None
        self._hl_row = hit[0] if hit else -1
        if self._hl_row < 0:
            self.frozen.clearSelection()
            return
        self.frozen.selectRow(self._hl_row)
        fm = self.frozen.model()
        if fm is not None and fm.rowCount():
            self.frozen.scrollTo(fm.index(self._hl_row, 0))

    def highlighted_row(self):
        """当前高亮的输入行下标（没有 = -1）。"""
        return self._hl_row

    # ═════════════════════ 十二、state 信号 ═════════════════════
    def _on_current_changed(self, name=""):
        self.refresh()

    def _on_refresh_signal(self, *_a):
        self.refresh()


#: C3-int 接线时逐条核对（★ = `WorkbenchStateProto` 里没有、本模块另要的）
STATE_REQUIREMENTS = (
    ("current_name / analyze(name)", "当前信号 + 它的 an：装表的唯一数据源；抛异常 = 分析失败"
                                     "（本模块转成一行空态文案，不再往上抛，C-043）"),
    ("edit_of(name) / put_edit(name, rec)",
     "已有编辑就用记录里的 cols，没有就 `edits.cols_from_vectors`；改动回写 rec = "
     "{kind, src_out_name, name, renamed, cols, an}（`edits.compute_edited` 认的那份）"),
    ("suspend_persist()", "I-05 / C-244：批量动作只落一次盘"),
    ("mux_data() / provider() / compute_edited()",
     "C-110 的会话档、C-136…C-139 导出 CSV 要的 provider 与编辑状态"),
    ("coverage()", "★ `session.CoverageState`：`effective_label`（mux 头部条的生效档，C-124）+ "
                   "`mode_for`（导出 CSV 的 cov 五元组，与 `sv_preview._cov_args()` 同口径）"),
    ("models()", "★ `effective_label` / `mode_for` 查逻辑类型档要清单行（拿不到就当没单设）"),
    # ⚠ 不要 `record_export` —— 主控裁决（C3-int）：**单信号 CSV 不进「上次导出」**
    # （`contracts.EXPORT_KINDS` 里没有这一档，硬塞会把导出中心那六行的“上次导到哪”带偏）。
    ("信号 currentChanged / coverageChanged / configChanged / scopeChanged / workbookChanged",
     "有哪条接哪条，缺的静默跳过。**刻意不接 `editsChanged`** —— 那是本模块自己发出去的，"
     "接了就是自激"),
    ("bus.netSelected(net, origin) / bus.select(net, origin)",
     "当前线网的唯一通道（I-18）；收到广播只改高亮，绝不回写"),
    ("an['expansion']['shadowed'] / ['parsed_cases']",
     "★ C-125 死分支就在 expansion 顶层（**没有** meta 层，架构 §6.14 写的 "
     "`expansion['meta']['shadowed']` 与实物不符）"),
    ("an['status_detail'] == 'false-green'",
     "★ C-113 撞值：引擎的 `meta['override_collision']` 没进 an，归到这一档；"
     "model 的 `muxCollision` 是第二条路"),
    ("an['dft_gate']['binding'].kind == 'RO'", "★ C-126 门能不能 force"),
    ("an['sig']._is_supplement / ._supplement_note", "★ C-215 RTL 补充逻辑的琥珀点"),
)
