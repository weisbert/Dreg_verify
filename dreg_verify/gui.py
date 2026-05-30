# -*- coding: utf-8 -*-
"""
gui.py — Dreg_verify 的 PySide6 图形界面（含 debug 辅助 + 测试项可视化编辑）：
  加载 Excel → 信号表(owner/类型/名字/状态筛选 + 多选 + 负向勾选)
  → 点信号看它 force/RF_WRITE 哪些 net 的明细(查 elaboration 找不到 net 的问题)
  → 「测试项」标签页：把该信号的全部测试用例列成可编辑表格(逐输入改值/期望自动重算/
     手填期望即标负向/加删复制行/重新生成/导出CSV/预览本信号.sv)
  → 「.sv 预览」标签页：预览选中信号的完整 .sv + 覆盖诊断
  → 导出 wr_rf_tc.sv（编辑过的测试项经 vector_overrides 真实回流到产物）。
后端复用 generator，与 CLI 同一套逻辑。

测试项编辑语义（核心）：一条测试项 = 各物理输入取值 → 表达式自动求出期望。
  · 改输入值 → 期望自动重算（永远自洽、永远对）。
  · 改期望值 → 若与算出值不同则该行自动标为负向(故意填错，预期断言应 FAIL)。

运行: python -m dreg_verify.gui
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from PySide6 import QtCore, QtGui, QtWidgets
except Exception as ex:  # noqa: BLE001
    raise SystemExit("需要 PySide6：pip install PySide6（原始错误：%s）" % ex)

from dreg_verify import excel_model, generator  # noqa: E402
from dreg_verify import resolver as R            # noqa: E402
from dreg_verify import expr as E                # noqa: E402
from dreg_verify import vectors as V             # noqa: E402
from dreg_verify import sv_writer as W           # noqa: E402

# 记住上次加载的 Excel，下次启动自动加载（省去重复浏览/点击）
SETTINGS_PATH = os.path.join(os.path.expanduser("~"), ".dreg_verify_gui.json")


def _load_settings():
    """读取持久化配置(上次的 Excel、上次的导出选项等)。返回 dict。"""
    try:
        with open(SETTINGS_PATH, encoding="utf-8") as f:
            d = json.load(f)
            return d if isinstance(d, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def _save_settings(d):
    # 测试环境(pytest)下不落盘，避免把临时状态污染到用户的真实配置
    if "pytest" in sys.modules:
        return
    try:
        with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
            json.dump(d, f)
    except Exception:  # noqa: BLE001
        pass


def _load_last_excel():
    return _load_settings().get("last_excel")


def _save_last_excel(path):
    d = _load_settings()
    d["last_excel"] = path
    _save_settings(d)


def _git_repo_root(path):
    """从 path(文件或目录)向上找 .git，找到则返回仓库根，否则 None。
    用于在写出含机密信号名的产物时提醒'别提交进 git 仓库'。"""
    try:
        d = os.path.abspath(path)
        if os.path.isfile(d) or os.path.splitext(d)[1]:
            d = os.path.dirname(d)
        while True:
            # .git 可能是目录(常规仓库)或文件(worktree/submodule 里 .git 是 'gitdir: …' 文件)
            if os.path.exists(os.path.join(d, ".git")):
                return d
            parent = os.path.dirname(d)
            if parent == d:
                return None
            d = parent
    except Exception:  # noqa: BLE001
        return None


SECRET_HINT = "产物可能含机密信号名，请勿提交到 git。"


COL_SEL, COL_NEG, COL_R, COL_K, COL_OWNER, COL_TYPE, COL_TOP, COL_STATUS, COL_EXPR = range(9)
HEADERS = ["选", "负向", "R", "输出名(K)", "owner", "type", "top", "状态", "表达式"]
STATUS_LABEL = {"clean": "clean", "wire-fallback": "⚠wire兜底",
                "unresolved": "✗未解析", "parse-err": "✗解析错"}
STATUS_HELP = {"clean": "输入都解析到具体 net，可正常 force/RF_WRITE 驱动",
               "wire-fallback": "有输入回退成 wire 兜底；elaboration 可能在 ENV_RF 层找不到该 net",
               "unresolved": "有输入未解析到 net（ENV_RF 探不到，仿真会 CUVUNF）",
               "parse-err": "表达式或输入解析出错"}
# 「输入信号」表(真值表上方)：把字母→信号/角色/驱动 集中成一张可读的小表(取代头部那行难读的图例)
INPUT_COLS = ["字母", "信号(位宽)", "角色", "类型", "驱动"]
# 负向用 琥珀，刻意区别于"状态列红=信号坏掉/会 elaboration 失败"；红只留给真正的故障
NEG_BG = QtGui.QColor("#fdeccb")        # 负向用例行底色（琥珀，能压过隔行底色）
NEG_FG = QtGui.QColor("#9a5b00")        # 负向列头/标记文字色（深琥珀）


class FlowLayout(QtWidgets.QLayout):
    """按钮按可用宽度自动换行的布局（Qt 官方 FlowLayout 示例改写）。

    用途：工具条按钮一多，普通 QHBoxLayout 会把"所有按钮宽度之和"作为父面板的最小宽度，
    导致 QSplitter 拖不动、面板被挤没。FlowLayout 在宽度不够时把按钮折到下一行，
    面板最小宽度 ≈ 单个最宽按钮，于是 splitter 可以自由拖动、按钮永不被吞。
    """

    def __init__(self, parent=None, margin=0, hspacing=6, vspacing=4):
        super().__init__(parent)
        self._items = []
        self._hspace = hspacing
        self._vspace = vspacing
        self.setContentsMargins(margin, margin, margin, margin)

    # Qt 要求实现的接口 ----------------------------------------------------
    def addItem(self, item):
        self._items.append(item)

    def count(self):
        return len(self._items)

    def itemAt(self, index):
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index):
        return self._items.pop(index) if 0 <= index < len(self._items) else None

    def expandingDirections(self):
        return QtCore.Qt.Orientations()

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self._do_layout(QtCore.QRect(0, 0, width, 0), test_only=True)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._do_layout(rect, test_only=False)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        size = QtCore.QSize()
        for it in self._items:
            size = size.expandedTo(it.minimumSize())     # ≈ 单个最宽控件 → 面板可缩到很窄
        m = self.contentsMargins()
        return size + QtCore.QSize(m.left() + m.right(), m.top() + m.bottom())

    def _do_layout(self, rect, test_only):
        m = self.contentsMargins()
        eff = rect.adjusted(m.left(), m.top(), -m.right(), -m.bottom())
        x, y, line_h = eff.x(), eff.y(), 0
        for it in self._items:
            w, h = it.sizeHint().width(), it.sizeHint().height()
            next_x = x + w + self._hspace
            if next_x - self._hspace > eff.right() and line_h > 0:   # 放不下且本行已有控件 → 换行
                x = eff.x()
                y = y + line_h + self._vspace
                next_x = x + w + self._hspace
                line_h = 0
            if not test_only:
                it.setGeometry(QtCore.QRect(QtCore.QPoint(x, y), QtCore.QSize(w, h)))
            x = next_x
            line_h = max(line_h, h)
        return y + line_h - rect.y() + m.bottom()


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Dreg_verify — wr_rf_tc.sv 生成器 + 测试项编辑")
        self.resize(1360, 800)
        self.wb = None
        self.signals = []
        self._analysis = {}     # row index -> generator.analyze_signal 结果
        self._resolver = None
        # ── 测试项编辑状态 ──
        self._edited = {}        # out_name(小写) -> {"sig":LogicSignal, "rows":[rowdict]}
        self._customized = set() # 被用户改过的信号名(小写)，仅这些走 vector_overrides
        self._neg_only = {}      # {信号名小写: 负向规则"first"/"all"} —— 正向全自动、仅加了负向的信号；
                                 # 记规则是为了切覆盖度重算时按原规则补回(默认1条不会被炸成每条一条)；清负向时可整体撤销定制
        self._ti_sig = None      # 当前在编辑器里的信号
        self._ti_node = None
        self._ti_bindings = {}
        self._ti_groups = []
        self._ti_rows = []
        self._ti_name_low = None
        self._ti_loaded_idx = None
        self._ti_loading = False  # 程序化填表时屏蔽 itemChanged，防递归
        self._sig_loading = False # 程序化改信号表(含左侧负向勾选)时屏蔽其 itemChanged
        self._build_ui()

    # ───────────── UI ─────────────
    def _build_ui(self):
        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        root = QtWidgets.QVBoxLayout(central)

        top = QtWidgets.QHBoxLayout()
        self.path_edit = QtWidgets.QLineEdit()
        self.path_edit.setPlaceholderText("选择 Dreg 核心 Excel (.xlsx) ...")
        browse = QtWidgets.QPushButton("浏览…"); browse.clicked.connect(self.on_browse)
        browse.setShortcut("Ctrl+O"); browse.setToolTip("选择 Excel (Ctrl+O)")
        load = QtWidgets.QPushButton("加载"); load.clicked.connect(self.on_load)
        load.setShortcut("Ctrl+L"); load.setToolTip("重新加载当前 Excel (Ctrl+L)")
        top.addWidget(QtWidgets.QLabel("Excel:")); top.addWidget(self.path_edit, 1)
        top.addWidget(browse); top.addWidget(load)
        root.addLayout(top)

        flt = QtWidgets.QHBoxLayout()
        self.owner_combo = QtWidgets.QComboBox(); self.owner_combo.addItem("全部 owner")
        self.owner_combo.currentIndexChanged.connect(self.apply_filter)
        self.type_combo = QtWidgets.QComboBox(); self.type_combo.addItem("全部 type")
        self.type_combo.currentIndexChanged.connect(self.apply_filter)
        self.status_combo = QtWidgets.QComboBox()
        self.status_combo.addItems(["全部状态", "仅 clean", "仅有问题(非clean)"])
        self.status_combo.currentIndexChanged.connect(self.apply_filter)
        self.name_edit = QtWidgets.QLineEdit(); self.name_edit.setPlaceholderText("名字/正则搜索…")
        self.name_edit.textChanged.connect(self.apply_filter)
        self.top_only = QtWidgets.QCheckBox("仅 top_output=1")    # 默认显示全部，便于 debug
        self.top_only.stateChanged.connect(self.apply_filter)
        for w in (QtWidgets.QLabel("筛选:"), self.owner_combo, self.type_combo,
                  self.status_combo, self.name_edit, self.top_only):
            flt.addWidget(w, 1 if w is self.name_edit else 0)
        flt.addStretch(1)
        root.addLayout(flt)

        splitter = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        # 左：信号表 + 批量操作条 + 明细
        left = QtWidgets.QSplitter(QtCore.Qt.Vertical)
        top_box = QtWidgets.QWidget()
        tv = QtWidgets.QVBoxLayout(top_box); tv.setContentsMargins(0, 0, 0, 0); tv.setSpacing(3)
        self.table = QtWidgets.QTableWidget(0, len(HEADERS))
        self.table.setHorizontalHeaderLabels(HEADERS)
        self.table.setSortingEnabled(True)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        # 行多选(框选/Ctrl/Shift)，配合"勾选选中行"按钮，一次勾一批
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        self.table.currentCellChanged.connect(self.on_row_focus)
        self.table.itemChanged.connect(self.on_signal_table_item_changed)  # 左侧"负向"勾选→联动右表
        self._set_header_tooltips()
        tv.addWidget(self.table)
        # 批量操作条（就近放在信号表下方，符合使用习惯）。用 FlowLayout：窄了自动换行，不卡死 splitter。
        bulk_box = QtWidgets.QWidget()
        bulk = FlowLayout(bulk_box)
        b_check = QtWidgets.QPushButton("勾选选中行")
        b_check.setToolTip("把表里当前选中的行(框选/Ctrl/Shift 多选)一次性勾上'选'")
        b_check.clicked.connect(self.on_check_selected_rows)
        b_selall = QtWidgets.QPushButton("全选输出(可见)"); b_selall.setToolTip("勾选所有可见信号的'选'")
        b_selall.clicked.connect(lambda: self.set_all_visible(True))
        b_selnone = QtWidgets.QPushButton("清空选择"); b_selnone.clicked.connect(lambda: self.set_all_visible(False))
        b_negall = QtWidgets.QPushButton("全部加负向")
        b_negall.setToolTip("给目标信号各加 1 条负向自检(每信号 1 条)。\n有勾选→只作用于已勾选信号，否则作用于全部可见。")
        b_negall.clicked.connect(lambda: self.on_all_signals_neg(True))
        b_negnone = QtWidgets.QPushButton("清除负向")
        b_negnone.setToolTip("清除目标信号的负向(有勾选→只清已勾选，否则清全部可见)。含命名/手填错值会先确认。")
        b_negnone.clicked.connect(lambda: self.on_all_signals_neg(False))
        for b in (b_check, b_selall, b_selnone):
            bulk.addWidget(b)
        bulk.addWidget(QtWidgets.QLabel(" 负向:"))
        for b in (b_negall, b_negnone):
            bulk.addWidget(b)
        tv.addWidget(bulk_box)
        left.addWidget(top_box)
        self.detail = QtWidgets.QPlainTextEdit(); self.detail.setReadOnly(True)
        self.detail.setMaximumHeight(200)
        self._mono(self.detail)
        left.addWidget(self.detail)
        left.setSizes([540, 170])
        splitter.addWidget(left)

        # 右：标签页（测试项编辑 / .sv 预览）
        self.tabs = QtWidgets.QTabWidget()
        self.tabs.addTab(self._build_testitems_tab(), "测试项")
        self.preview_tab = QtWidgets.QWidget()
        pv = QtWidgets.QVBoxLayout(self.preview_tab); pv.setContentsMargins(0, 0, 0, 0)
        self.preview = QtWidgets.QPlainTextEdit(); self.preview.setReadOnly(True)
        self.preview.setLineWrapMode(QtWidgets.QPlainTextEdit.NoWrap)
        self._mono(self.preview)
        pv.addWidget(self.preview)
        self.tabs.addTab(self.preview_tab, ".sv 预览")
        splitter.addWidget(self.tabs)
        # 拖动体验：两侧都不可被拖没(setChildrenCollapsible False)，给小而合理的最小宽度，
        # 加粗手柄更易抓取；左侧固定、右侧吃伸缩。配合 FlowLayout 工具条 → 可自由拖动两侧大小。
        splitter.setChildrenCollapsible(False)
        splitter.setHandleWidth(6)
        left.setMinimumWidth(220)
        self.tabs.setMinimumWidth(300)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([640, 700])
        root.addWidget(splitter, 1)

        opt = QtWidgets.QHBoxLayout()
        # 覆盖度：把旧的 min/max + 小信号全穷举 合成一个高层选择(精简<全面<穷举)。
        self.coverage = QtWidgets.QComboBox(); self.coverage.addItems(["精简", "全面", "穷举"])
        self.coverage.setToolTip(
            "测试用例的覆盖强度(对'未自定义'的信号即时生效)：\n"
            "  精简 = 每种控制位组合各取 1 组代表数据(最少用例)\n"
            "  全面 = 每种控制位组合再扫多组数据(全0/全1/反码/走步/区分)\n"
            "  穷举 = 所有输入的全部组合(仅当总输入位≤10，否则自动退化为'全面')")
        self.coverage.currentIndexChanged.connect(self.on_coverage_changed)
        self.cov_hint = QtWidgets.QLabel("")            # 实时显示当前信号的用例条数
        self.cov_hint.setStyleSheet("color:#1558d6;")
        self.max_tests = QtWidgets.QSpinBox(); self.max_tests.setRange(1, 100000); self.max_tests.setValue(256)
        self.max_tests.setToolTip("用例数上限(安全阀，防止穷举/全面产生过多用例)")
        self.max_tests.valueChanged.connect(self.on_coverage_changed)
        for w in (QtWidgets.QLabel("覆盖度:"), self.coverage, self.cov_hint,
                  QtWidgets.QLabel("   上限"), self.max_tests):
            opt.addWidget(w)
        opt.addStretch(1)
        root.addLayout(opt)

        btns = QtWidgets.QHBoxLayout()
        prev = QtWidgets.QPushButton("预览选中"); prev.clicked.connect(self.on_preview)
        prev.setShortcut("Ctrl+P")
        prev.setToolTip("预览所有已勾选信号合并生成的 .sv (Ctrl+P)；只看单信号用右侧『预览本信号.sv』")
        rep = QtWidgets.QPushButton("导出报告(HTML/CSV)…"); rep.clicked.connect(self.on_report)
        rep.setShortcut("Ctrl+R")
        rep.setToolTip("出'给人看'的测试用例报告(汇总+每信号真值表+完整明细)，自动带上你的编辑；"
                       "未勾选则覆盖全部信号 (Ctrl+R)")
        gen = QtWidgets.QPushButton("生成 .sv …"); gen.clicked.connect(self.on_generate)
        gen.setShortcut("Ctrl+G")
        gen.setToolTip("点开后可选导出范围(全部/仅正向/仅负向)与是否加注释 (Ctrl+G)")
        btns.addStretch(1); btns.addWidget(prev); btns.addWidget(rep); btns.addWidget(gen)
        root.addLayout(btns)

        self.status = self.statusBar()
        self.status.showMessage("请选择并加载 Excel。")

    def _build_testitems_tab(self):
        """测试项编辑标签页：表头说明 + 工具条 + 可编辑表格。"""
        page = QtWidgets.QWidget()
        lay = QtWidgets.QVBoxLayout(page); lay.setContentsMargins(4, 4, 4, 4)
        self.ti_header = QtWidgets.QLabel("点左侧任一信号，这里列出它的测试项（可编辑）。")
        self.ti_header.setWordWrap(True)
        self.ti_header.setStyleSheet("color:#445;")
        lay.addWidget(self.ti_header)

        bar_box = QtWidgets.QWidget()
        bar = FlowLayout(bar_box)        # 按钮多，窄屏自动换行，避免给右面板强加大最小宽度
        defs = [("重新生成", self.on_ti_regen, "丢弃本信号自定义，按当前向量选项从表达式重新生成"),
                ("加正向列", self.on_ti_add, "新增一条正向(真实)测试(输入全 0，期望自动算)"),
                ("复制列", self.on_ti_copy, "复制当前选中的测试列"),
                ("删除列", self.on_ti_del, "删除选中的测试列"),
                ("重命名列…", self.on_ti_rename_current, "给用户新增的测试列改名(双击列头亦可；自动生成的 T0/T1 不可改)"),
                ("加负向(选中)", self.on_ti_add_neg_selected,
                 "给选中的测试列各加一条负向(故意填错期望)；未选中则取首条正向。正向测试不动"),
                ("全部用例加负向", self.on_ti_add_neg_all,
                 "每条正向测试各追加一条负向(全覆盖；用例数翻倍，按需用)"),
                ("删负向", self.on_ti_del_neg, "删除本信号所有负向测试(保留正向)"),
                ("预览本信号.sv", self.on_ti_preview_signal, "用当前(含编辑)测试项渲染该信号的 .sv 片段"),
                ("导出CSV", self.on_ti_export_csv, "把本信号测试项导出为 CSV(Excel 可开)")]
        for text, slot, tip in defs:
            b = QtWidgets.QPushButton(text); b.clicked.connect(slot)
            if text == "复制列":
                tip = "%s (Ctrl+D)" % tip       # 快捷键用 ti_table 上的 WidgetShortcut(见下)，不挂按钮(否则编辑中也会触发)
            b.setToolTip(tip)
            bar.addWidget(b)
        lay.addWidget(bar_box)

        # 「输入信号」表：字母 → 物理信号 / 角色(控制·数据) / 类型(RO·RW) / 驱动机制(force·RF_WRITE)。
        # 取代头部那行难读的小字图例，并把原本只在左下明细里的驱动信息搬到真值表正上方。
        cap = QtWidgets.QLabel("输入信号  （字母 → 物理信号 · 角色 · 驱动机制）")
        cap.setStyleSheet("color:#445;font-weight:bold;")
        lay.addWidget(cap)
        self.ti_inputs = QtWidgets.QTableWidget(0, len(INPUT_COLS))
        self.ti_inputs.setHorizontalHeaderLabels(INPUT_COLS)
        self._mono(self.ti_inputs)
        self.ti_inputs.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.ti_inputs.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.ti_inputs.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.ti_inputs.verticalHeader().setVisible(False)
        self.ti_inputs.horizontalHeader().setStretchLastSection(True)   # 驱动列吃剩余宽度
        self.ti_inputs.setToolTip("每个表达式变量对应的物理信号、是控制位还是数据位、以及怎么被驱动"
                                  "(RO→force net；RW→RF_WRITE 地址+bit位)")
        self._fit_inputs_height()         # 起始(无信号)也保持紧凑，不留大空白
        lay.addWidget(self.ti_inputs)

        cap2 = QtWidgets.QLabel("真值表  （行=输入/输出，列=测试 T0/T1…）")
        cap2.setStyleSheet("color:#445;font-weight:bold;")
        lay.addWidget(cap2)
        self.ti_table = QtWidgets.QTableWidget(0, 0)
        self._mono(self.ti_table)
        self.ti_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectItems)
        self.ti_table.setAlternatingRowColors(True)
        self.ti_table.itemChanged.connect(self.on_ti_item_changed)
        self.ti_table.horizontalHeader().sectionDoubleClicked.connect(self.on_ti_rename_col)
        # Ctrl+D 复制列：用 WidgetShortcut 挂在真值表上——仅当表本身有焦点时触发；
        # 单元格正在编辑时焦点在子 QLineEdit 上，快捷键不会触发，避免打断/丢失编辑(对抗式审查 #1)
        copy_sc = QtGui.QShortcut(QtGui.QKeySequence("Ctrl+D"), self.ti_table)
        copy_sc.setContext(QtCore.Qt.WidgetShortcut)
        copy_sc.activated.connect(self.on_ti_copy)
        lay.addWidget(self.ti_table, 1)

        hint = QtWidgets.QLabel(
            "行表头=字母(粗体=控制/选择位，决定逻辑分支、测试穷举其 0/1 组合)，详见上方『输入信号』表；"
            "改输入值→期望自动重算；改期望值或勾“负向”→该列标为故意填错(预期 FAIL)。编辑会在生成/预览的 .sv 里生效。")
        hint.setWordWrap(True); hint.setStyleSheet("color:#888;font-size:11px;")
        lay.addWidget(hint)
        return page

    def _mono(self, w):
        f = w.font(); f.setFamily("Consolas"); w.setFont(f)

    # ───────────── 覆盖度（精简/全面/穷举 → 向量生成参数） ─────────────
    def _coverage(self):
        """把「覆盖度」下拉映射成 (mode, exhaustive)。穷举位数过多时由 vectors 自动退化为'全面'。"""
        c = self.coverage.currentText()
        if c == "穷举":
            return ("max", True)
        if c == "全面":
            return ("max", False)
        return ("min", False)       # 精简

    def on_coverage_changed(self, *args):
        """覆盖度/上限变化 → 即时重算当前信号的测试项：
        · 纯自动信号：直接按新覆盖度重算；
        · 仅靠'负向'定制(无手改正向)的信号：按新覆盖度重算正向后再补回负向；
        · 手改过测试项的信号：保留编辑不动(避免冲掉用户工作)。"""
        if self._sig_loading or getattr(self, "_ti_loading", False):
            return
        sig, name_low = self._ti_sig, self._ti_name_low
        if sig is not None and name_low is not None:
            if name_low not in self._customized:
                self._load_test_items(sig)               # 纯自动：按新覆盖度重算
            elif name_low in self._neg_only:
                # 仅负向定制(无手改) → 撤销定制、按新覆盖度重算正向、再按原规则补回负向
                rule = self._neg_only[name_low]   # "first"/"all"——保住"默认1条不被炸成每条一条"
                self._customized.discard(name_low)
                self._neg_only.pop(name_low, None)
                self._edited.pop(name_low, None)
                self._load_test_items(sig)
                self._set_signal_negatives(sig, True, rule)
                self._load_test_items(sig)
        self._update_cov_hint()

    def _update_cov_hint(self):
        """工具栏「覆盖度」旁实时显示当前信号的用例条数，把抽象档位变具体。"""
        if not hasattr(self, "cov_hint"):
            return
        if self._ti_sig is None or not self._ti_rows:
            self.cov_hint.setText("")
            return
        n = len(self._ti_rows)
        n_neg = sum(1 for rd in self._ti_rows if rd.get("is_negative"))
        tag = "（已自定义）" if self._ti_name_low in self._customized else ""
        extra = "，含 %d 负向" % n_neg if n_neg else ""
        self.cov_hint.setText("→ 当前信号 %d 条%s%s" % (n, extra, tag))

    # ───────────── 加载 + 分析 ─────────────
    def on_browse(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "选择 Excel", "", "Excel (*.xlsx)")
        if path:
            self.path_edit.setText(path)
            self.on_load()        # 选完即加载，无需再点"加载"

    def on_load(self):
        path = self.path_edit.text().strip()
        if not path or not os.path.isfile(path):
            QtWidgets.QMessageBox.warning(self, "提示", "请先选择有效的 .xlsx 文件")
            return
        try:
            self.wb = excel_model.load_workbook(path)
        except Exception as ex:  # noqa: BLE001
            QtWidgets.QMessageBox.critical(self, "加载失败", str(ex))
            return
        self.signals = list(self.wb.logic)
        # 解析画像：逐信号 try，一个坏信号不连累整体加载
        self._resolver = R.Resolver(self.wb)
        self._analysis = {}
        # 切换工作簿，清空旧的测试项编辑状态
        self._edited = {}
        self._customized = set()
        self._neg_only = {}
        self._ti_loaded_idx = None
        self._clear_test_items("加载完成，点左侧信号查看测试项。")
        errs = []
        for i, s in enumerate(self.signals):
            try:
                self._analysis[i] = generator.analyze_signal(self._resolver, s)
            except Exception as ex:  # noqa: BLE001
                self._analysis[i] = {"status": "解析异常", "inputs": [], "out_net": "",
                                     "error": repr(ex)}
                errs.append((s.out_name, s.assert_id, repr(ex)))
        self._populate_filters()
        self._populate_table()
        nbad = sum(1 for a in self._analysis.values() if a["status"] != "clean")
        msg = ("已加载 %d 信号（%d 个非 clean）；tmm字段=%d regmap=%d"
               % (len(self.signals), nbad, len(self.wb.tmm), len(self.wb.regmap)))
        if errs:
            msg += "；⚠ %d 个信号分析异常(状态'解析异常',点开看 error)" % len(errs)
            self.preview.setPlainText("分析异常的信号(请把下面发给维护者):\n" +
                                      "\n".join("R=%s %s: %s" % (a, n, e) for n, a, e in errs[:50]))
        _save_last_excel(path)         # 记住这次的文件，下次启动自动加载
        self.status.showMessage(msg)

    def _populate_filters(self):
        owners = sorted({s.owner for s in self.signals if s.owner})
        types = sorted({s.suffix for s in self.signals if s.suffix})
        for combo, items, head in ((self.owner_combo, owners, "全部 owner"),
                                   (self.type_combo, types, "全部 type")):
            combo.blockSignals(True); combo.clear(); combo.addItem(head); combo.addItems(items)
            combo.blockSignals(False)

    def _populate_table(self):
        self._sig_loading = True
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(self.signals))
        for r, sig in enumerate(self.signals):
            try:
                self._set_check(r, COL_SEL, False)
                self._set_check(r, COL_NEG, False)
                self._set_text(r, COL_R, str(sig.assert_id))
                self._set_text(r, COL_K, sig.out_name)
                self._set_text(r, COL_OWNER, sig.owner)
                self._set_text(r, COL_TYPE, sig.suffix)
                self._set_text(r, COL_TOP, str(sig.top_output))
                st = self._analysis.get(r, {}).get("status", "?")
                it = QtWidgets.QTableWidgetItem(STATUS_LABEL.get(st, st))
                if st != "clean":
                    it.setForeground(QtGui.QColor("red"))
                it.setToolTip(STATUS_HELP.get(st, st))
                self.table.setItem(r, COL_STATUS, it)
                self._set_text(r, COL_EXPR, sig.expr)
                self.table.item(r, COL_R).setData(QtCore.Qt.UserRole, r)
            except Exception:  # noqa: BLE001  单行异常不连累整表
                self._set_text(r, COL_R, str(getattr(sig, "assert_id", "?")))
                self._set_text(r, COL_K, getattr(sig, "out_name", "?"))
                self.table.item(r, COL_R).setData(QtCore.Qt.UserRole, r)
        self.table.setSortingEnabled(True)
        self.table.resizeColumnsToContents()
        self._sig_loading = False
        self.apply_filter()

    def _set_text(self, r, c, text):
        self.table.setItem(r, c, QtWidgets.QTableWidgetItem(text or ""))

    def _set_check(self, r, c, checked):
        it = QtWidgets.QTableWidgetItem()
        it.setFlags(QtCore.Qt.ItemIsUserCheckable | QtCore.Qt.ItemIsEnabled)
        it.setCheckState(QtCore.Qt.Checked if checked else QtCore.Qt.Unchecked)
        self.table.setItem(r, c, it)

    def _set_header_tooltips(self):
        """给关键表头加说明——把'负向'(自检语义)和'状态'(可验证性/CUVUNF)讲在用户第一眼看到的地方。"""
        tips = {
            COL_SEL: "勾选要写进 wr_rf_tc.sv 的信号",
            COL_NEG: "负向 = 故意把期望填错的自检测试，断言预期 FAIL，用来验证 checker 真能抓错；"
                     "默认每信号 1 条足够。\n勾此 = 给该信号加 1 条；要多条/精确选，去右侧『测试项』编辑器。",
            COL_STATUS: "信号可验证性（点信号看右下明细的 force/输出 net 名）：\n"
                        "  clean = 输入都解析到 net\n"
                        "  ⚠wire兜底 = 输入回退成 wire，elaboration 可能找不到\n"
                        "  ✗未解析/解析错 = 输入在 ENV_RF 层探不到（CUVUNF）",
        }
        for c, t in tips.items():
            it = self.table.horizontalHeaderItem(c)
            if it:
                it.setToolTip(t)

    # ───────────── 筛选 ─────────────
    def apply_filter(self):
        import re
        owner = self.owner_combo.currentText()
        typ = self.type_combo.currentText()
        statusf = self.status_combo.currentText()
        pat = self.name_edit.text().strip()
        rx = None
        if pat:
            try:
                rx = re.compile(pat, re.I)
            except re.error:
                rx = None
        top_only = self.top_only.isChecked()
        visible = 0
        for r in range(self.table.rowCount()):
            sig = self._sig_of_row(r)
            st = self._analysis[self._idx_of_row(r)]["status"]
            show = True
            if owner != "全部 owner" and sig.owner != owner:
                show = False
            if typ != "全部 type" and sig.suffix != typ:
                show = False
            if statusf == "仅 clean" and st != "clean":
                show = False
            if statusf == "仅有问题(非clean)" and st == "clean":
                show = False
            if rx and not (rx.search(sig.out_name) or rx.search(sig.expr)):
                show = False
            elif pat and not rx and pat.lower() not in (sig.out_name + sig.expr).lower():
                show = False
            if top_only and str(sig.top_output).strip() not in ("1", "1.0", "True", "true"):
                show = False
            self.table.setRowHidden(r, not show)
            visible += show
        self.status.showMessage("可见信号 %d / 共 %d" % (visible, len(self.signals)))

    def _idx_of_row(self, r):
        return self.table.item(r, COL_R).data(QtCore.Qt.UserRole)

    def _sig_of_row(self, r):
        return self.signals[self._idx_of_row(r)]

    def set_all_visible(self, checked):
        for r in range(self.table.rowCount()):
            if not self.table.isRowHidden(r):
                self.table.item(r, COL_SEL).setCheckState(
                    QtCore.Qt.Checked if checked else QtCore.Qt.Unchecked)

    def on_check_selected_rows(self):
        """把信号表里当前『选中的行』(框选/Ctrl/Shift)一次性勾上『选』——省去逐个点小复选框。"""
        rows = sorted({i.row() for i in self.table.selectedItems()})
        if not rows:
            self.status.showMessage("先在信号表里选中若干行(鼠标框选 / Ctrl·Shift 点)，再点『勾选选中行』")
            return
        for r in rows:
            cell = self.table.item(r, COL_SEL)
            if cell is not None:
                cell.setCheckState(QtCore.Qt.Checked)
        self.status.showMessage("已把选中的 %d 行勾上『选』" % len(rows))

    def _checked_rows(self):
        """勾了『选』且当前可见的行——隐藏行不进批量作用域，避免改到被筛掉、看不见的信号
        (批量负向用；导出用的 _collect() 另算，仍含隐藏的勾选行)。"""
        return [r for r in range(self.table.rowCount())
                if not self.table.isRowHidden(r)
                and self.table.item(r, COL_SEL) is not None
                and self.table.item(r, COL_SEL).checkState() == QtCore.Qt.Checked]

    def _scope_rows(self):
        """批量操作的目标行：有勾选→只取已勾选行；否则取全部可见行。"""
        checked = self._checked_rows()
        if checked:
            return checked
        return [r for r in range(self.table.rowCount()) if not self.table.isRowHidden(r)]

    def _confirm_lose_named(self, names):
        return QtWidgets.QMessageBox.question(
            self, "确认清除负向",
            "将清除负向，其中 %d 个信号含手工命名/填错值的负向(会一并丢失)：\n%s\n\n确定？"
            % (len(names), "、".join(names[:10]) + (" …" if len(names) > 10 else ""))
            ) == QtWidgets.QMessageBox.Yes

    # ───────────── 左侧"负向"列 → 联动测试项 ─────────────
    def _signal_negatives(self, name_low):
        """该信号当前所有负向行(从 _edited 里取；未定制信号没有负向)。"""
        return [rd for rd in self._edited.get(name_low, {}).get("rows", [])
                if rd.get("kind") == "neg"]

    def _signal_has_negative(self, name_low):
        return bool(self._signal_negatives(name_low))

    def _signal_has_named_negative(self, name_low):
        """是否含"值得保护"的负向：有自定义名或手填错值(误删它们=丢用户的活)。"""
        return any(rd.get("name") or rd.get("wrong_value") is not None
                   for rd in self._signal_negatives(name_low))

    def on_signal_table_item_changed(self, item):
        """勾/取消左侧"负向"列：勾=确保该信号至少有 1 条负向自检(已有更多的保持不动，不塌成1条)；
        取消=清掉全部负向(若含命名/手填错值的负向，先确认防误删)。
        想要更多/更精确，去右侧编辑器「加负向(选中)」或「全部用例加负向」。左右联动。"""
        if self._sig_loading or item is None or item.column() != COL_NEG:
            return
        r = item.row()
        sig = self._sig_of_row(r)
        name_low = sig.out_name.lower()
        want = item.checkState() == QtCore.Qt.Checked
        if want:
            if not self._signal_has_negative(name_low):   # 没有才加 1 条；已有的原样保留
                self._set_signal_negatives(sig, True, "first")
            msg = "%s 已标记负向(1 条自检)" % sig.out_name
        else:
            if self._signal_has_named_negative(name_low):
                if QtWidgets.QMessageBox.question(
                        self, "确认清除负向",
                        "%s 有自定义命名或手填错值的负向，取消勾选会全部删除。确定？" % sig.out_name
                        ) != QtWidgets.QMessageBox.Yes:
                    self._sig_loading = True               # 用户取消 → 还原勾选状态
                    try:
                        item.setCheckState(QtCore.Qt.Checked)
                    finally:
                        self._sig_loading = False
                    return
            self._set_signal_negatives(sig, False, "first")
            msg = "%s 已清除负向" % sig.out_name
        if self._idx_of_row(r) == self._ti_loaded_idx:    # 正在编辑该信号→刷新右表
            self._load_test_items(sig)
        self.status.showMessage(msg)

    def _set_signal_negatives(self, sig, want_neg, which):
        """给某信号(重新)设置负向测试：保留全部正向测试，按 first/all 追加正向测试的"故意填错"
        副本作为负向；want_neg=False 则删除所有负向。可作用于未显示的信号(存进 override)。"""
        if self._resolver is None:
            return
        name_low = sig.out_name.lower()
        try:
            node = E.parse(sig.expr)
        except E.ExprError:
            return
        bindings = self._resolver.resolve_signal_inputs(sig)
        groups = V.input_groups(node, bindings)
        hand_edited = (name_low in self._edited and name_low not in self._neg_only)
        # 仅靠"加负向"定制的信号，清负向 = 回到纯自动 → 整体撤销定制(恢复默认 risky-skip 等行为)
        if not want_neg and not hand_edited:
            self._edited.pop(name_low, None)
            self._customized.discard(name_low)
            self._neg_only.pop(name_low, None)
            return
        rows = (self._edited[name_low]["rows"] if name_low in self._edited
                else self._auto_rows(sig, node, bindings, groups))
        pos_rows = [rd for rd in rows if rd.get("kind") != "neg"]
        if not pos_rows:
            return
        # 现存负向行按其输入取值建索引，重建时继承用户对负向列的改名/手填错值(不静默丢失)
        old_negs = {}
        for rd in rows:
            if rd.get("kind") == "neg":
                old_negs.setdefault(tuple(sorted(rd["base_values"].items())), []).append(rd)
        new_rows = list(pos_rows)                      # 正向测试原样保留
        if want_neg:
            targets = pos_rows if which == "all" else pos_rows[:1]
            for prd in targets:                        # 每个(或首个)正向 → 追加一条负向副本
                neg = {"base_values": dict(prd["base_values"]),
                       "kind": "neg", "wrong_value": None, "user_added": True,
                       "note": "负向(真实测试的故意填错副本)"}
                bucket = old_negs.get(tuple(sorted(prd["base_values"].items())))
                if bucket:                             # 继承同源旧负向的自定义名与手填错值
                    old = bucket.pop(0)
                    if old.get("name") is not None:
                        neg["name"] = old["name"]
                    if old.get("wrong_value") is not None:
                        neg["wrong_value"] = old["wrong_value"]
                new_rows.append(neg)
        for rd in new_rows:
            self._recompute_row(node, bindings, groups, sig.out_width, rd)
        self._edited[name_low] = {"sig": sig, "rows": new_rows}
        self._customized.add(name_low)
        if hand_edited:
            self._neg_only.pop(name_low, None)
        elif want_neg:
            self._neg_only[name_low] = which   # 正向全自动、仅加了负向；记住规则(first/all)
        else:
            self._neg_only.pop(name_low, None)

    def _sync_left_neg(self):
        """编辑器里负向有变 → 回写左侧"负向"勾选(任一用例为负向则勾上)。"""
        if not self._ti_sig:
            return
        any_neg = any(rd.get("is_negative") for rd in self._ti_rows)
        for r in range(self.table.rowCount()):
            if self._idx_of_row(r) == self._ti_loaded_idx:
                cell = self.table.item(r, COL_NEG)
                if cell is None:
                    return
                self._sig_loading = True
                try:
                    cell.setCheckState(QtCore.Qt.Checked if any_neg else QtCore.Qt.Unchecked)
                finally:
                    self._sig_loading = False
                return

    def on_all_signals_neg(self, want):
        """对『目标信号』一键加/清负向：有勾选→只作用于已勾选信号，否则作用于全部可见。
        每信号默认 1 条自检，已有负向的不动；清除时若含命名/手填错值的负向先确认(防误删)。"""
        if not self.wb:
            return
        rows = self._scope_rows()
        scope_word = "已勾选" if self._checked_rows() else "可见"
        if not want:
            protected = [self._sig_of_row(r).out_name for r in rows
                         if self._signal_has_named_negative(self._sig_of_row(r).out_name.lower())]
            if protected and not self._confirm_lose_named(protected):
                return
        self._sig_loading = True
        n = 0
        try:
            for r in rows:
                sig = self._sig_of_row(r)
                name_low = sig.out_name.lower()
                if want:
                    if not self._signal_has_negative(name_low):   # 已有负向的不塌成 1 条
                        self._set_signal_negatives(sig, True, "first")
                else:
                    self._set_signal_negatives(sig, False, "first")
                cell = self.table.item(r, COL_NEG)
                if cell is not None:
                    cell.setCheckState(QtCore.Qt.Checked if want else QtCore.Qt.Unchecked)
                n += 1
        finally:
            self._sig_loading = False
        if self._ti_sig is not None and self._ti_loaded_idx is not None:
            self._load_test_items(self._ti_sig)   # 当前编辑器信号若被影响则刷新
        self.status.showMessage("已对 %d 个%s信号%s负向(每信号 1 条自检；已有的保持不动)"
                                % (n, scope_word, "添加" if want else "清除"))

    # ───────────── 点信号看明细（debug 关键） ─────────────
    def on_row_focus(self, row, col, prow, pcol):
        if row < 0 or not self.signals:
            return
        idx = self._idx_of_row(row)
        a = self._analysis.get(idx)
        sig = self.signals[idx]
        if a is None:
            return
        lines = ["信号: %s   (assert_%s, %s, top_output=%s)"
                 % (sig.out_name, sig.assert_id, sig.suffix, sig.top_output),
                 "表达式: %s" % sig.expr,
                 "状态: %s" % STATUS_LABEL.get(a["status"], a["status"]),
                 "断言: assert (%s == <期望>)" % a["out_net"], ""]
        if a["error"]:
            lines.append("解析错误: %s" % a["error"])
        for inp in a["inputs"]:
            flag = "" if inp["resolved"] else "  ✗"
            lines.append("  %s=%s  [%s/%s]%s  ->  %s"
                         % (inp["letter"], inp["base"], inp["kind"], inp["found_in"], flag, inp["net"]))
            if inp["note"]:
                lines.append("        note: %s" % inp["note"])
        lines.append("")
        lines.append("提示: elaboration 报 CUVUNF 找不到 net 时，对比这里的 force/输出 net 名是否真存在于 ENV_RF 层；"
                     "⚠wire兜底/✗未解析 的最可疑。")
        self.detail.setPlainText("\n".join(lines))
        # 仅当信号变化时刷新测试项编辑表（避免同行换列时重建）
        if self._ti_loaded_idx != idx:
            self._ti_loaded_idx = idx
            self._load_test_items(sig)

    # ───────────── 测试项编辑器 ─────────────
    def _clear_test_items(self, header_text):
        self._ti_sig = None; self._ti_node = None
        self._ti_bindings = {}; self._ti_groups = []; self._ti_rows = []
        self._ti_name_low = None
        self.ti_header.setText(header_text)
        self._ti_loading = True
        try:
            self.ti_table.clear()
            self.ti_table.setRowCount(0)
            self.ti_table.setColumnCount(0)
            if hasattr(self, "ti_inputs"):
                self.ti_inputs.setRowCount(0)
        finally:
            self._ti_loading = False
        self._update_cov_hint()

    def _load_test_items(self, sig):
        if not self.wb or self._resolver is None or sig is None:
            return
        name_low = sig.out_name.lower()
        try:
            node = E.parse(sig.expr)
        except E.ExprError as ex:
            self._clear_test_items("信号: %s — 表达式解析失败: %s（无法生成测试项）" % (sig.out_name, ex))
            return
        bindings = self._resolver.resolve_signal_inputs(sig)
        groups = V.input_groups(node, bindings)
        self._ti_sig = sig; self._ti_node = node
        self._ti_bindings = bindings; self._ti_groups = groups
        self._ti_name_low = name_low
        if name_low in self._edited:
            self._ti_rows = self._edited[name_low]["rows"]
            custom = True
        else:
            self._ti_rows = self._auto_rows(sig, node, bindings, groups)
            custom = False
        for rd in self._ti_rows:
            self._ti_recompute(rd)
        self._update_ti_header(custom)
        self._populate_inputs()       # 上方『输入信号』表(字母/角色/驱动)，随信号刷新
        self._ti_populate()

    def _auto_rows(self, sig, node, bindings, groups):
        """按当前向量选项自动生成测试项 → rowdict 列表。"""
        mode, exhaustive = self._coverage()
        try:
            vecs, _meta = V.generate_vectors(
                node, bindings, sig.out_width,
                mode=mode, max_tests=self.max_tests.value(), exhaustive=exhaustive)
        except E.ExprError:
            vecs = []
        rows = []
        for vec in vecs:
            rows.append({
                "base_values": V.vector_to_base_values(vec, groups),
                "kind": "pos",          # 自动生成的都是正向(真实)测试
                "note": vec.note,
            })
        return rows

    def _recompute_row(self, node, bindings, groups, out_width, rd):
        """重算一行 correct/expected/is_negative，并缓存向量。显式传上下文，可对任意信号重算。

        rd['kind']:
          'pos' —— 正向(真实)测试：期望永远 = 表达式算出的正确值，不会被改坏；
          'neg' —— 负向(故意填错)测试：期望 = 错误值(rd['wrong_value'] 手填，或默认取反 correct 派生)。
        """
        try:
            base_vec = V.make_vector_from_base_values(
                node, bindings, groups, rd["base_values"], out_width)
        except E.ExprError:
            # 表达式含缺位宽变量等无法求值：保底，不让编辑面板崩
            rd["correct"] = 0
            rd["correct_width"] = out_width or 1
            rd["is_negative"] = (rd.get("kind") == "neg")
            rd["expected"] = 0
            rd["_vec"] = None
            return
        correct, w = base_vec.exp_value, base_vec.exp_width
        m = E.mask(w)
        rd["correct"] = correct
        rd["correct_width"] = w
        if rd.get("kind") == "neg":
            wv = rd.get("wrong_value")
            if wv is None:                          # 未手填 → 默认取反正确值(最显然的"错")
                wrong = V.make_negative(base_vec, mode="invert").neg_value
            else:
                wrong = wv & m
                if wrong == correct:                # 手填值恰等于正确值 → 强制翻一位保证"错"
                    wrong = (~correct) & m
                    if wrong == correct:
                        wrong = correct ^ 1
            rd["expected"] = wrong
            rd["is_negative"] = True
            rd["_vec"] = V.make_vector_from_base_values(
                node, bindings, groups, rd["base_values"], out_width, expected_override=wrong)
        else:
            rd["expected"] = correct
            rd["is_negative"] = False
            rd["_vec"] = base_vec

    def _ti_recompute(self, rd):
        """对当前编辑器显示信号的行重算（薄封装 _recompute_row）。"""
        self._recompute_row(self._ti_node, self._ti_bindings, self._ti_groups,
                            self._ti_sig.out_width, rd)

    def _drive_strs(self, rd):
        """该行的 force / RF_WRITE 驱动文本（供表格与 CSV 展示）。"""
        vec = rd.get("_vec")
        if vec is None and "correct" not in rd:
            self._ti_recompute(rd); vec = rd["_vec"]
        if vec is None:
            return "", ""
        try:
            forces, writes, _unres = W.compute_drives(
                vec, self._ti_bindings, E.collect_vars(self._ti_node))
        except Exception:  # noqa: BLE001
            return "", ""
        fs = "; ".join("%s=%s" % (f["wire"], f["hex"]) for f in forces)
        ws = "; ".join("%s=%s" % (w["addr"], w["hex"]) for w in writes)
        return fs, ws

    def _update_ti_header(self, custom):
        tag = "   [已自定义★]" if custom else ""
        # 表达式写成 "输出 = RHS" 等式；字母对照已下移到『输入信号』表，头部保持精简
        self.ti_header.setText(
            "信号 %s     %s = %s     用例 %d 条%s"
            % (self._ti_sig.out_name, self._ti_sig.out_base or "out", self._ti_sig.expr,
               len(self._ti_rows), tag))

    def _ti_mark_customized(self):
        if not self._ti_sig:
            return
        self._edited[self._ti_name_low] = {"sig": self._ti_sig, "rows": self._ti_rows}
        self._customized.add(self._ti_name_low)
        self._neg_only.pop(self._ti_name_low, None)   # 有手工编辑 → 不再是"纯负向定制"(冻结，保住编辑)
        self._update_ti_header(True)
        self._sync_left_neg()        # 右表负向变化 → 回写左侧"负向"勾选

    # 纵向(真值表)布局：每个输入/输出一行(纵表头)，每条测试一列 T0/T1...。
    #   行: 0..G-1 = 各输入(base)；R_EXP = 期望(out)。
    #   列: 每列一条测试用例(正向 或 负向；负向列标红、列头带 _NEG)。
    def _ti_dims(self):
        self._ti_G = len(self._ti_groups)
        self.R_EXP = self._ti_G
        return self._ti_G + 1     # 总行数 = 输入数 + 1(期望)

    @staticmethod
    def _fmt_val(val, width):
        """取值显示(CSV 用，Excel 友好的纯 hex)：1 位 → 0/1；多位 → 0xNN。"""
        if width <= 1:
            return str(val & 1)
        return "0x%X" % val

    @staticmethod
    def _cell_text(val, width):
        """编辑器单元格取值显示(GUI 专用，能被 _parse_int 原样读回)：
        1 位→0/1；2..4 位→0bXXXX(零填充,真值表里看清每个控制/数据位)；更宽→0xNN。"""
        m = E.mask(width)
        if width <= 1:
            return str(val & 1)
        if width <= 4:
            return "0b" + format(val & m, "0%db" % width)
        return "0x%X" % (val & m)

    @staticmethod
    def _ti_label(rd, idx):
        """列头/测试名显示：自定义 name 优先，否则 T<idx>；负向保证带 _NEG。"""
        nm = rd.get("name")
        label = nm if nm else ("T%d" % idx)
        if rd.get("is_negative") and not label.upper().endswith("NEG"):
            label += "_NEG"
        return label

    @staticmethod
    def _group_letters(g):
        """该输入组对应的表达式变量字母(可能多个，如同一物理信号占 A、B 两个字母)。"""
        return ",".join(g.get("letters") or [])

    @classmethod
    def _vheader_label(cls, g):
        """完整行表头：'A,B → d_xxx[14:14]'（CSV 导出用，保持自描述）。"""
        ltr = cls._group_letters(g)
        base_lbl = g.get("label", g["base"])
        return "%s → %s" % (ltr, base_lbl) if ltr else base_lbl

    @classmethod
    def _vheader_short(cls, g):
        """精简行表头(GUI 真值表用)：只留字母(+控制标记)；信号全名/角色/驱动在上方『输入信号』表。"""
        ltr = cls._group_letters(g)
        if not ltr:
            return g.get("label", g["base"])
        return "%s (控制)" % ltr if g.get("is_control") else ltr

    def _input_meta(self, g):
        """该输入组的 (类型, 驱动机制) 文本，供『输入信号』表的『类型』『驱动』列：
        RO→force <net>；RW→RF_WRITE 0x<地址> bit<<<lsb>；未解析→标红提示。"""
        b = self._ti_bindings.get(g["rep"]) if self._ti_bindings else None
        if b is None:
            return ("?", "(无绑定)")
        kind = b.kind or "?"
        if not getattr(b, "resolved", True):
            note = getattr(b, "note", "") or ""
            return (kind, "✗未解析" + ("：" + note if note else ""))
        if b.kind == "RW" and b.address is not None:
            return (kind, "RF_WRITE 0x%X bit<<%d" % (b.address, b.reg_lsb or 0))
        if b.kind == "RO":
            return (kind, "force ENV_RF.%s" % b.wire_lhs)
        return (kind, getattr(b, "note", "") or "?")

    def _populate_inputs(self):
        """填『输入信号』表：每个输入一行(字母/信号(位宽)/角色/类型/驱动)。随信号变化，不随逐格编辑变。"""
        if not hasattr(self, "ti_inputs"):
            return
        tbl = self.ti_inputs
        tbl.setRowCount(len(self._ti_groups))
        for i, g in enumerate(self._ti_groups):
            kind, drive = self._input_meta(g)
            role = "控制/选择位" if g["is_control"] else "数据位"
            for c, v in enumerate([self._group_letters(g), g.get("label", g["base"]),
                                   role, kind, drive]):
                it = QtWidgets.QTableWidgetItem(v)
                if g["is_control"] and c == 0:           # 控制位字母加粗，与真值表行表头呼应
                    f = it.font(); f.setBold(True); it.setFont(f)
                if c == 4 and "未解析" in v:
                    it.setForeground(QtGui.QColor("red"))
                tbl.setItem(i, c, it)
        tbl.resizeColumnsToContents()
        self._fit_inputs_height()

    def _fit_inputs_height(self):
        """『输入信号』表高度贴合行数(最多约 7 行可见，多了内部滚动)，不抢真值表空间。
        用 defaultSectionSize 估行高(未 show 时也稳定)，把表头当作 1 行。"""
        t = self.ti_inputs
        n = t.rowCount()
        row_h = max(t.verticalHeader().defaultSectionSize(), 22)
        shown = min(max(n, 1), 7)
        t.setFixedHeight((shown + 1) * row_h + 6)       # +1 行给表头

    def _ti_populate(self):
        self._ti_loading = True
        try:
            self.ti_table.clear()
            nrows = self._ti_dims()
            ntests = len(self._ti_rows)
            self.ti_table.setRowCount(nrows)
            self.ti_table.setColumnCount(ntests)
            out_w = self._ti_sig.out_width if self._ti_sig else 1
            exp_label = "out → 期望%s" % ("[%d:0]" % (out_w - 1) if out_w and out_w > 1 else "")
            # 行表头精简为字母(+控制标记)；完整信号/角色/驱动在上方『输入信号』表
            vlabels = [self._vheader_short(g) for g in self._ti_groups] + [exp_label]
            self.ti_table.setVerticalHeaderLabels(vlabels)
            for i, g in enumerate(self._ti_groups):
                hi = self.ti_table.verticalHeaderItem(i)
                if hi:
                    hi.setToolTip("表达式变量 %s  =  %s\n(%s, %dbit%s)"
                                  % (self._group_letters(g) or "?", g["label"], g["kind"], g["width"],
                                     ", 控制位/选择位" if g["is_control"] else ", 数据位"))
                    if g["is_control"]:          # 控制/选择位加粗，提示"看这几行的 0/1 组合"
                        f = hi.font(); f.setBold(True); hi.setFont(f)
            # 期望行表头：把输出信号名也带上，呼应表达式左边
            hexp = self.ti_table.verticalHeaderItem(self.R_EXP)
            if hexp:
                hexp.setToolTip("输出(表达式左边) = %s\n各列是该输入组合下表达式算出的期望值"
                                % (self._ti_sig.out_name if self._ti_sig else "out"))
            self.ti_table.setHorizontalHeaderLabels(["T%d" % i for i in range(ntests)])
        finally:
            self._ti_loading = False
        for c in range(len(self._ti_rows)):
            self._ti_render_col(c)
        self.ti_table.resizeColumnsToContents()
        self._update_cov_hint()

    def _mk_item(self, text, editable):
        it = QtWidgets.QTableWidgetItem(text)
        flags = QtCore.Qt.ItemIsEnabled | QtCore.Qt.ItemIsSelectable
        if editable:
            flags |= QtCore.Qt.ItemIsEditable
        it.setFlags(flags)
        return it

    def _ti_render_col(self, c):
        """渲染第 c 列（第 c 条测试）的整列单元格。"""
        if c < 0 or c >= len(self._ti_rows):
            return
        rd = self._ti_rows[c]
        w = rd.get("correct_width") or 1
        neg = rd["is_negative"]
        fs, ws = self._drive_strs(rd)
        self._ti_loading = True
        try:
            for i, g in enumerate(self._ti_groups):
                val = rd["base_values"].get(g["base"].lower(), 0)
                it = self._mk_item(self._cell_text(val, g["width"]), True)
                it.setTextAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)   # 右对齐→低位对齐，列间差异一眼看出
                self.ti_table.setItem(i, c, it)
            # 期望：正向只读(永远=正确值，不可改坏)；负向可编辑(可手填具体错值)
            expit = self._mk_item(self._cell_text(rd["expected"] & E.mask(w), w), neg)
            expit.setTextAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
            tip = "期望 bin: %s" % W.fmt_bin(rd["expected"], w)
            if neg:
                tip += "\n(负向：故意填错，正确应为 %s；可双击改这个错值)" % self._cell_text(rd["correct"], w)
            if fs:
                tip += "\nforce: %s" % fs
            if ws:
                tip += "\nRF_WRITE: %s" % ws
            expit.setToolTip(tip)
            self.ti_table.setItem(self.R_EXP, c, expit)
            # 列头：自定义名/T<n>(负向带 _NEG，红字)；tooltip 提示可否改名 + 驱动
            hh = self.ti_table.horizontalHeaderItem(c)
            if hh:
                hh.setText(self._ti_label(rd, c))
                rename_hint = "双击列头可改名" if rd.get("user_added") else "自动生成，名字不可改"
                hh.setToolTip("%s · %s\nforce: %s\nRF_WRITE: %s"
                              % ("负向(故意填错)" if neg else "正向(真实)", rename_hint,
                                 fs or "(无)", ws or "(无)"))
                hh.setForeground(NEG_FG if neg else QtGui.QColor("black"))   # 负向=琥珀，不与"状态红=坏掉"撞色
            if neg:
                for r in range(self.ti_table.rowCount()):
                    cell = self.ti_table.item(r, c)
                    if cell is not None:
                        cell.setBackground(NEG_BG)
        finally:
            self._ti_loading = False

    @staticmethod
    def _parse_int(s):
        """宽松解析用户输入的数值：16'hA / 0x.. / hA / 'b.. / 'd.. / 十进制 / 裸 hex。"""
        import re
        t = str(s).strip().lower().replace(" ", "")
        if t == "":
            return 0
        m = re.search(r"'h([0-9a-f]+)$", t)
        if m:
            return int(m.group(1), 16)
        m = re.search(r"'b([01]+)$", t)
        if m:
            return int(m.group(1), 2)
        m = re.search(r"'d(\d+)$", t)
        if m:
            return int(m.group(1), 10)
        if t.startswith("0b") and t[2:] and set(t[2:]) <= {"0", "1"}:
            return int(t, 2)             # 仅当 0b 后全是 0/1 才当二进制；'0bc' 之类仍按裸 hex 解析(向后兼容)
        if t.startswith("0x"):
            return int(t, 16)
        if t.startswith("h"):
            return int(t[1:], 16)
        if re.fullmatch(r"\d+", t):
            return int(t, 10)
        if re.fullmatch(r"[0-9a-f]+", t):
            return int(t, 16)
        raise ValueError("无法识别 %r（用 0x.. 或 16'h.. 或纯数字）" % s)

    def on_ti_item_changed(self, item):
        if self._ti_loading or not self._ti_sig:
            return
        r, c = item.row(), item.column()       # 列 c = 第 c 条测试；行 r = 输入/期望/负向
        if c < 0 or c >= len(self._ti_rows):
            return
        rd = self._ti_rows[c]
        try:
            if 0 <= r < len(self._ti_groups):
                g = self._ti_groups[r]
                val = self._parse_int(item.text()) & E.mask(g["width"])
                rd["base_values"][g["base"].lower()] = val
                self._ti_recompute(rd)        # 正向→期望自动重算；负向→错值随之重算
            elif r == self.R_EXP:
                if rd.get("kind") != "neg":   # 正向期望只读，不接受手改(防御)
                    return
                rd["wrong_value"] = self._parse_int(item.text()) & E.mask(rd.get("correct_width") or 1)
                self._ti_recompute(rd)
            else:
                return
        except ValueError as ex:
            self.status.showMessage("数值解析失败: %s（已还原）" % ex)
        self._ti_render_col(c)
        self._ti_mark_customized()

    def on_ti_add(self):
        if not self._ti_sig:
            QtWidgets.QMessageBox.information(self, "提示", "请先在左侧选择一个信号")
            return
        rd = {"base_values": {g["base"].lower(): 0 for g in self._ti_groups},
              "kind": "pos", "note": "", "user_added": True}   # 用户新增正向列(可改名)
        self._ti_recompute(rd)
        self._ti_rows.append(rd)
        self._ti_mark_customized()
        self._ti_populate()
        self.ti_table.setCurrentCell(0, len(self._ti_rows) - 1)

    def on_ti_add_neg_selected(self):
        """给选中的测试列各加一条负向(精确控制)；未选中则取首条正向。正向测试不动。
        这是"手挑哪几条加负向"的入口——属手工定制，切换覆盖度时会保留(不被重算冲掉)。"""
        if not self._ti_sig:
            QtWidgets.QMessageBox.information(self, "提示", "请先在左侧选择一个信号")
            return
        cols = sorted({i.column() for i in self.ti_table.selectedItems()})
        if not cols:                          # 无多选 → 退当前列(与"复制列/删除列"一致)
            c = self.ti_table.currentColumn()
            cols = [c] if c >= 0 else []
        pos_cols = [c for c in cols if 0 <= c < len(self._ti_rows)
                    and self._ti_rows[c].get("kind") != "neg"]
        if not pos_cols:                      # 仍无正向列 → 默认首条正向
            first = next((i for i, rd in enumerate(self._ti_rows)
                          if rd.get("kind") != "neg"), None)
            if first is None:
                QtWidgets.QMessageBox.information(self, "提示", "本信号没有可作负向来源的正向用例")
                return
            pos_cols = [first]
        # 已有负向的输入取值集合 → 跳过，避免给同一条用例叠出重复负向断言
        existing = {tuple(sorted(rd["base_values"].items()))
                    for rd in self._ti_rows if rd.get("kind") == "neg"}
        added = skipped = 0
        for c in pos_cols:
            prd = self._ti_rows[c]
            key = tuple(sorted(prd["base_values"].items()))
            if key in existing:               # 这条正向已经有负向了 → 不重复加
                skipped += 1
                continue
            existing.add(key)
            neg = {"base_values": dict(prd["base_values"]), "kind": "neg",
                   "wrong_value": None, "user_added": True,
                   "note": "负向(真实测试的故意填错副本)"}
            self._ti_recompute(neg)
            self._ti_rows.append(neg)
            added += 1
        if added == 0:
            self.status.showMessage("选中的列都已有负向，未重复添加")
            return
        self._ti_mark_customized()            # 含 _sync_left_neg；精挑负向 → 手工定制(冻结)
        self._ti_populate()
        msg = "已为 %s 加 %d 条负向(选中列)" % (self._ti_sig.out_name, added)
        if skipped:
            msg += "；%d 列已有负向已跳过" % skipped
        self.status.showMessage(msg)

    def on_ti_add_neg_all(self):
        """每条正向测试各追加一条负向(全覆盖)：复制每条正向为故意填错的副本，正向测试不动。"""
        if not self._ti_sig:
            QtWidgets.QMessageBox.information(self, "提示", "请先在左侧选择一个信号")
            return
        self._set_signal_negatives(self._ti_sig, True, "all")
        self._load_test_items(self._ti_sig)
        self._sync_left_neg()
        n = sum(1 for rd in self._ti_rows if rd.get("kind") == "neg")
        self.status.showMessage("已为 %s 添加 %d 条负向测试(每条正向各一条)" % (self._ti_sig.out_name, n))

    def on_ti_del_neg(self):
        """删除本信号所有负向测试，保留正向。含命名/手填错值的负向先确认(防误删)。"""
        if not self._ti_sig:
            return
        if self._signal_has_named_negative(self._ti_name_low):
            if QtWidgets.QMessageBox.question(
                    self, "确认删除负向",
                    "%s 有手工命名/填错值的负向，删除会丢失。确定？" % self._ti_sig.out_name
                    ) != QtWidgets.QMessageBox.Yes:
                return
        self._set_signal_negatives(self._ti_sig, False, "all")
        self._load_test_items(self._ti_sig)
        self._sync_left_neg()
        self.status.showMessage("已删除 %s 的全部负向测试" % self._ti_sig.out_name)

    # ───────────── 测试列改名（仅用户新增的可改） ─────────────
    @staticmethod
    def _sanitize_name(s):
        """把用户输入清成合法的 SV 标号片段(只留字母/数字/下划线)。"""
        import re
        return re.sub(r"[^0-9A-Za-z_]", "_", str(s).strip())

    def on_ti_rename_current(self):
        c = self.ti_table.currentColumn()
        if c < 0:
            QtWidgets.QMessageBox.information(self, "提示", "请先选中一个测试列")
            return
        self.on_ti_rename_col(c)

    def on_ti_rename_col(self, col):
        """改第 col 个测试列的名字。仅用户新增的列可改；自动生成的 T0/T1 拒绝。"""
        if not self._ti_sig or col < 0 or col >= len(self._ti_rows):
            return
        rd = self._ti_rows[col]
        if not rd.get("user_added"):
            QtWidgets.QMessageBox.information(
                self, "不可改名", "T%d 是从表达式自动生成的测试，名字不可改。\n只有你自己新增的测试列(加正向列/复制列/加负向)可以改名。" % col)
            return
        cur = self._ti_label(rd, col)
        text, ok = QtWidgets.QInputDialog.getText(self, "重命名测试列",
                                                  "新名字(字母/数字/下划线)：", text=cur)
        if not ok:
            return
        ok2, msg = self._ti_set_test_name(col, text)
        if not ok2:
            QtWidgets.QMessageBox.warning(self, "改名失败", msg)

    def _ti_set_test_name(self, col, new_name):
        """设置测试列自定义名。返回 (成功, 信息/最终名)。供 UI 与测试调用。"""
        rd = self._ti_rows[col]
        if not rd.get("user_added"):
            return False, "自动生成的测试不可改名"
        nm = self._sanitize_name(new_name)
        if not nm:
            return False, "名字为空或全是非法字符"
        import re
        if re.match(r"(?i)^t\d+(_neg)?$", nm):     # T<编号> 是自动测试保留命名，禁止手填(防后续位移撞名)
            return False, "名字 %s 与自动测试命名(T<编号>)冲突，请换个名字" % nm
        # 计算该名的最终标号(负向会带 _NEG)，与其它列的最终标号查重
        final = nm + ("_NEG" if rd.get("is_negative") and not nm.upper().endswith("NEG") else "")
        for j, other in enumerate(self._ti_rows):
            if j != col and self._ti_label(other, j) == final:
                return False, "名字与列 %s 重复(会造成 .sv 标号冲突)" % self._ti_label(other, j)
        rd["name"] = nm
        self._ti_mark_customized()
        self._ti_populate()
        self.status.showMessage("测试列已改名为 %s" % final)
        return True, final

    def on_ti_copy(self):
        if not self._ti_sig:
            return
        c = self.ti_table.currentColumn()    # 复制当前选中列(测试)
        if c < 0 or c >= len(self._ti_rows):
            QtWidgets.QMessageBox.information(self, "提示", "请先选中要复制的测试列")
            return
        src = self._ti_rows[c]
        # 复制列：用户新建列(可改名)，但不复制 name(避免与原列重名→.sv 标号冲突)
        rd = {"base_values": dict(src["base_values"]),
              "kind": src.get("kind", "pos"), "note": src.get("note", ""), "user_added": True}
        if src.get("wrong_value") is not None:
            rd["wrong_value"] = src["wrong_value"]
        self._ti_recompute(rd)
        self._ti_rows.insert(c + 1, rd)
        self._ti_mark_customized()
        self._ti_populate()
        self.ti_table.setCurrentCell(0, c + 1)

    def on_ti_del(self):
        if not self._ti_sig:
            return
        cols = sorted({i.column() for i in self.ti_table.selectedItems()}, reverse=True)
        if not cols:
            c = self.ti_table.currentColumn()
            cols = [c] if c >= 0 else []
        if not cols:
            QtWidgets.QMessageBox.information(self, "提示", "请先选中要删除的测试列")
            return
        for c in cols:
            if 0 <= c < len(self._ti_rows):
                del self._ti_rows[c]
        self._ti_mark_customized()
        self._ti_populate()

    def on_ti_regen(self):
        if not self._ti_sig:
            return
        self._customized.discard(self._ti_name_low)
        self._neg_only.pop(self._ti_name_low, None)
        self._edited.pop(self._ti_name_low, None)
        self._load_test_items(self._ti_sig)
        self.status.showMessage("已从表达式重新生成 %s 的测试项（丢弃自定义）" % self._ti_sig.out_name
                                if self._ti_sig else "")

    def on_ti_preview_signal(self):
        if not self._ti_sig:
            QtWidgets.QMessageBox.information(self, "提示", "请先在左侧选择一个信号")
            return
        sig = self._ti_sig
        vecs = self._rows_to_vectors(self._ti_node, self._ti_bindings, self._ti_groups,
                                     sig.out_width, self._ti_rows)
        lines, _stats = W.render_signal_block(sig, self._ti_bindings, vecs,
                                              {"truncated": False}, comments=True)
        self.preview.setPlainText("\n".join(lines))
        self.tabs.setCurrentWidget(self.preview_tab)
        self.status.showMessage("已预览信号 %s 的 .sv 片段（%d 用例）" % (sig.out_name, len(vecs)))

    def on_ti_export_csv(self):
        if not self._ti_sig:
            QtWidgets.QMessageBox.information(self, "提示", "请先在左侧选择一个信号")
            return
        sig = self._ti_sig
        default = "%s_tests.csv" % (sig.out_base or "signal")
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "导出本信号测试项 CSV", default,
                                                        "CSV (*.csv)")
        if not path:
            return
        import csv
        rows = self._ti_rows
        # 纵向(真值表)导出：第一列=信号/字段名，其后每列一条测试 T0/T1...
        try:
            with open(path, "w", encoding="utf-8-sig", newline="") as f:
                wr = csv.writer(f)
                wr.writerow(["信号\\测试"] + [self._ti_label(rd, i) for i, rd in enumerate(rows)])
                for g in self._ti_groups:
                    bk = g["base"].lower()
                    wr.writerow([self._vheader_label(g)] + [self._fmt_val(rd["base_values"].get(bk, 0), g["width"])
                                                            for rd in rows])
                out_w = self._ti_sig.out_width or 1
                exp_label = "期望(out)%s" % ("[%d:0]" % (out_w - 1) if out_w > 1 else "")
                wr.writerow([exp_label] + [self._fmt_val(rd["expected"] & E.mask(rd.get("correct_width") or 1),
                                                         rd.get("correct_width") or 1) for rd in rows])
                wr.writerow(["期望(bin)"] + [W.fmt_bin(rd["expected"], rd.get("correct_width") or 1)
                                            for rd in rows])
                wr.writerow(["负向?"] + ["是" if rd["is_negative"] else "" for rd in rows])
                drives = [self._drive_strs(rd) for rd in rows]
                wr.writerow(["force"] + [fs for fs, _ in drives])
                wr.writerow(["RF_WRITE"] + [ws for _, ws in drives])
        except OSError as ex:
            QtWidgets.QMessageBox.critical(self, "导出失败", str(ex))
            return
        QtWidgets.QMessageBox.information(self, "完成", "已导出 %d 条测试项：\n%s\n\n（%s）"
                                          % (len(self._ti_rows), path, SECRET_HINT))
        self.status.showMessage("已导出测试项 CSV：%s" % path)

    def _rows_to_vectors(self, node, bindings, groups, out_width, rows):
        """把 rowdict 列表构造成 TestVector 列表（负向行用 expected 作 override 编码；带自定义名）。"""
        vecs = []
        for i, rd in enumerate(rows):
            exp_override = rd["expected"] if rd.get("is_negative") else None
            vecs.append(V.make_vector_from_base_values(
                node, bindings, groups, rd["base_values"], out_width,
                index=i, expected_override=exp_override, name=rd.get("name")))
        return vecs

    def _vector_overrides(self, positive_only=False, negative_only=False):
        """汇总所有被用户改过的信号的测试项 → {out_name(小写): [TestVector]} 喂给 build()。
        positive_only=True 时剔除负向向量(供"仅正向"导出，避免负向断言泄入)。
        negative_only=True 时只保留负向向量、且无负向的信号整个不出现(供"仅负向"导出)。
        删光所有行的信号会得到空列表(=该信号零用例)，而非回退自动生成——尊重用户清空意图。
        """
        if not self._customized or self._resolver is None:
            return None
        ov = {}
        for name_low in list(self._customized):
            ed = self._edited.get(name_low)
            if not ed:
                continue
            sig, rows = ed["sig"], ed["rows"]
            try:
                node = E.parse(sig.expr)
            except E.ExprError:
                continue
            bindings = self._resolver.resolve_signal_inputs(sig)
            groups = V.input_groups(node, bindings)
            vecs = self._rows_to_vectors(node, bindings, groups, sig.out_width, rows)
            if positive_only:
                vecs = [v for v in vecs if not v.is_negative]
            elif negative_only:
                vecs = [v for v in vecs if v.is_negative]
                if not vecs:
                    continue                 # 该信号无负向 → "仅负向"导出里整个略过
            ov[name_low] = vecs          # 空列表也保留：删空=零用例，不回退自动
        return ov or None

    def _opts(self, signals, neg_signals=None, positive_only=False, negative_only=False):
        # 注意：GUI 的负向统一走 vector_overrides(左侧"负向"列与右侧编辑器是同一套)，
        # 故这里默认不传 neg_signals，避免与 override 里的负向重复追加。
        mode, exhaustive = self._coverage()
        return generator.GenOptions(
            signals=signals or None, neg_signals=neg_signals or None,
            mode=mode, max_tests=self.max_tests.value(), exhaustive=exhaustive,
            top_output_only=False,   # GUI 已按表勾选，不再二次过滤
            vector_overrides=self._vector_overrides(positive_only=positive_only,
                                                    negative_only=negative_only))

    # ───────────── 收集 / 选项 ─────────────
    def _collect(self):
        """返回勾选(COL_SEL)的信号名列表。负向不再单独收集——已在 vector_overrides 里。"""
        sel = []
        for r in range(self.table.rowCount()):
            if self.table.item(r, COL_SEL).checkState() == QtCore.Qt.Checked:
                sel.append(self._sig_of_row(r).out_name)
        return sel

    def _negative_signal_names(self, sel):
        """从勾选信号 sel 里挑出"含负向测试"的原始信号名(用于'仅负向'导出)。"""
        ovneg = self._vector_overrides(negative_only=True) or {}
        low2name = {s.lower(): s for s in sel}
        return [low2name[k] for k in ovneg if k in low2name]

    # ───────────── 预览 / 生成 ─────────────
    def on_preview(self):
        if not self.wb:
            return
        sel = self._collect()
        if not sel:
            QtWidgets.QMessageBox.information(self, "提示", "请先勾选至少一个信号")
            return
        res = generator.build(self.wb, self._opts(sel))
        text = generator.render(res, comments=True)
        lines = text.splitlines()
        self.preview.setPlainText("\n".join(lines[:600])
                                  + ("\n... (预览截断，共 %d 行)" % len(lines) if len(lines) > 600 else ""))
        self.tabs.setCurrentWidget(self.preview_tab)
        s = res["summary"]
        msg = "预览: 生成 %d，向量 %d（负向 %d）" % (s["n_generated"], s["n_vectors"], s["n_negative"])
        if self._customized:
            msg += "；含 %d 个已自定义信号" % len(self._customized)
        if s.get("n_skipped"):
            msg += "；↷ 跳过 %d 个(含不可驱动输入，会 elaboration 失败)" % s["n_skipped"]
        if s.get("n_dup_labels"):
            msg += "；⛔ %d 处重复标号(非法SV，生成时会拦截)" % s["n_dup_labels"]
        self.status.showMessage(msg)
        if s.get("n_skipped") and res.get("skipped"):
            tip = "\n\n// ↷ 跳过 %d 个含不可驱动输入(wire兜底/未解析)的信号:" % s["n_skipped"]
            for name, aid, risky in res["skipped"][:30]:
                tip += "\n//   %s ← %s" % (name, ", ".join("%s=%s" % (l, b) for l, b, _ in risky))
            self.preview.appendPlainText(tip)

    def _ask_export_options(self, title):
        """生成 .sv 前的导出选项：内容范围(全部/仅正向/仅负向) + 是否加注释。
        记住上次选择(下次预选)，并提醒产物含机密信号名。返回 {"scope":..., "comments":bool} 或 None。"""
        st = _load_settings()
        last_scope, last_cm = st.get("export_scope", "all"), bool(st.get("export_comments", False))
        dlg = QtWidgets.QDialog(self)
        dlg.setWindowTitle(title)
        lay = QtWidgets.QVBoxLayout(dlg)
        lay.addWidget(QtWidgets.QLabel("导出内容范围："))
        scope_combo = QtWidgets.QComboBox()
        scope_combo.addItem("全部（正向 + 负向）", "all")
        scope_combo.addItem("仅正向（正确用例）", "pos")
        scope_combo.addItem("仅负向（故意填错，预期 FAIL）", "neg")
        scope_combo.setToolTip("仅负向：只导出你标了'负向'的故意填错用例，方便单独验证'错了能否被抓到'")
        si = scope_combo.findData(last_scope)
        if si >= 0:
            scope_combo.setCurrentIndex(si)
        lay.addWidget(scope_combo)
        cm_chk = QtWidgets.QCheckBox("加注释（在 .sv 里标注每条用例/负向说明）")
        cm_chk.setChecked(last_cm)
        lay.addWidget(cm_chk)
        warn = QtWidgets.QLabel("⚠ " + SECRET_HINT)
        warn.setStyleSheet("color:#9a5b00;")
        lay.addWidget(warn)
        bb = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        bb.accepted.connect(dlg.accept); bb.rejected.connect(dlg.reject)
        lay.addWidget(bb)
        if dlg.exec() != QtWidgets.QDialog.Accepted:
            return None
        scope, comments = scope_combo.currentData(), cm_chk.isChecked()
        st["export_scope"] = scope; st["export_comments"] = comments
        _save_settings(st)               # 记住，下次预选
        return {"scope": scope, "comments": comments}

    def _confirm_dup_labels(self, res):
        """有重复 assert 标号(非法 SV)就弹警告列出冲突对，让用户选『仍然生成/取消』。无冲突直接 True。"""
        dups = res.get("dup_labels") or []
        if not dups:
            return True
        lines = "\n".join("  %s  ←  %s / %s" % (lbl, a, b) for lbl, a, b in dups[:15])
        more = "\n  …(共 %d 处)" % len(dups) if len(dups) > 15 else ""
        return QtWidgets.QMessageBox.warning(
            self, "重复 assert 标号（非法 SV）",
            "检测到 %d 处重复的 assert 标号；同一作用域内重复会导致 elaboration 失败：\n%s%s\n\n"
            "多因两个信号共用同一 R(序号)。仍要生成吗？" % (len(dups), lines, more),
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
            QtWidgets.QMessageBox.No) == QtWidgets.QMessageBox.Yes

    def on_generate(self):
        if not self.wb:
            return
        sel = self._collect()
        if not sel:
            QtWidgets.QMessageBox.information(self, "提示", "请先勾选至少一个信号")
            return
        opt = self._ask_export_options("生成 .sv — 导出选项")
        if opt is None:
            return
        scope, cm = opt["scope"], opt["comments"]
        # 按内容范围构建：全部 / 仅正向(剔负向) / 仅负向(只含负向、无负向的信号略过)
        if scope == "neg":
            names = self._negative_signal_names(sel)
            if not names:
                QtWidgets.QMessageBox.information(
                    self, "提示", "勾选的信号里没有任何负向测试，无法只导出'错误用例'。\n"
                    "请先勾左侧'负向'列(1条)，或在右侧编辑器'加负向(选中)'/'全部用例加负向'。")
                return
            res = generator.build(self.wb, self._opts(names, negative_only=True))
            scope_msg = "（仅负向，共 %d 个含负向信号）" % len(names)
        elif scope == "pos":
            res = generator.build(self.wb, self._opts(sel, positive_only=True))
            scope_msg = "（仅正向）"
        else:
            res = generator.build(self.wb, self._opts(sel))
            scope_msg = ""

        # 生成前拦截：重复 assert 标号 = 非法 SV(elaboration 必失败)，先让用户知情再决定写不写
        if not self._confirm_dup_labels(res):
            return
        default_name = {"neg": "wr_rf_tc_neg.sv", "pos": "wr_rf_tc_pos.sv"}.get(scope, "wr_rf_tc.sv")
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "保存 .sv", default_name,
                                                        "SystemVerilog (*.sv)")
        if not path:
            return
        try:
            self._write(path, generator.render(res, comments=cm))
        except OSError as ex:
            QtWidgets.QMessageBox.critical(
                self, "写出失败",
                "无法写入 %s：\n%s\n\n(文件是否正被仿真器/编辑器占用？)" % (path, ex))
            return
        nsk = res["summary"].get("n_skipped", 0)
        skipmsg = ("\n\n↷ 跳过了 %d 个含不可驱动输入的信号(默认跳过以保证可 elaborate)；"
                   "如需强制生成用 CLI --include-risky。" % nsk) if nsk else ""
        # 已自定义信号数按"真正写进本次产物"的 block 统计(避免把未勾选/被范围过滤的也算进来)
        n_cust = sum(1 for (_l, st) in res["blocks"]
                     if st.get("out_name", "").lower() in self._customized)
        custmsg = ("\n\n含 %d 个已自定义测试项的信号(编辑已写入产物)。" % n_cust
                   if n_cust else "")
        gitwarn = ""
        root = _git_repo_root(path)
        if root:
            gitwarn = "\n⚠ 该位置在 git 仓库内（%s），注意别 add/commit。" % root
        QtWidgets.QMessageBox.information(self, "完成", "已写出：%s%s%s%s\n\n（%s）%s"
                                          % (path, scope_msg, skipmsg, custmsg, SECRET_HINT, gitwarn))
        self.status.showMessage("已生成：%s%s" % (path, scope_msg))

    def on_report(self):
        """导出'给人看'的测试用例报告(HTML 三段：汇总+每信号真值表+完整明细；或 CSV)。
        勾选了信号则只报告这些，否则覆盖全部信号；自动带上测试项编辑/负向。"""
        if not self.wb:
            return
        from dreg_verify import cli            # 复用 CLI 的报告写出器(按扩展名出 HTML/CSV)
        sel = self._collect()
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "导出测试用例报告", "dreg_report.html", "HTML 网页 (*.html);;CSV 表格 (*.csv)")
        if not path:
            return
        try:
            rep = generator.report(self.wb, self._opts(sel or None))
            written = cli.write_report(path, rep, self.path_edit.text() or "excel")
        except Exception as ex:  # noqa: BLE001
            QtWidgets.QMessageBox.critical(self, "导出失败", str(ex))
            return
        scope = "勾选的 %d 个" % len(sel) if sel else "全部"
        n_tc = len(rep["detail"]); n_neg = sum(1 for r in rep["detail"] if r.get("neg") == "是")
        gitwarn = "\n⚠ 在 git 仓库内，注意别 add/commit。" if _git_repo_root(path) else ""
        QtWidgets.QMessageBox.information(
            self, "完成", "已导出报告(%s信号，用例 %d 条，负向 %d 条)：\n%s\n\n（%s）%s"
            % (scope, n_tc, n_neg, "\n".join(written), SECRET_HINT, gitwarn))
        self.status.showMessage("已导出报告：%s" % "  ".join(written))

    def _write(self, path, text):
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)


def main():
    app = QtWidgets.QApplication(sys.argv)
    w = MainWindow()
    w.show()
    # 启动自动加载：命令行给了 .xlsx 就用它，否则用上次加载过的文件（都不需要再点"加载"）。
    preload = None
    cli_args = [a for a in sys.argv[1:] if not a.startswith("-")]
    if cli_args and os.path.isfile(cli_args[0]):
        preload = cli_args[0]
    else:
        last = _load_last_excel()
        if last and os.path.isfile(last):
            preload = last
    if preload:
        w.path_edit.setText(preload)
        w.on_load()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
