# -*- coding: utf-8 -*-
"""
gui.py — Dreg_verify 的 PySide6 图形界面（含 debug 辅助 + 测试项可视化编辑）：
  加载 Excel → 信号表(owner/类型/名字/状态筛选 + 多选 + 负向勾选)
  → 点信号看它 force/RF_WRITE 哪些 net 的明细(查 elaboration 找不到 net 的问题)
  → 「测试项」标签页：把该信号的全部测试用例列成可编辑表格(逐输入改值/auto_out 自动重算/
     designer 手填期望/加删复制行/重新生成/导出CSV/预览本信号.sv)
  → 「.sv 预览」标签页：预览选中信号的完整 .sv + 覆盖诊断
  → 导出 wr_rf_tc.sv（编辑过的测试项经 vector_overrides 真实回流到产物）。
后端复用 generator，与 CLI 同一套逻辑。

测试项编辑语义（核心，2026-06-03 更新——auto_out 与「期望」分离）：
  Dreg 验证的对象是 designer 写的逻辑表达式本身；用表达式算出的值去验证表达式有自证嫌疑。
  所以真值表拆成两行：
  · auto_out 行（只读）= 程序按表达式算出的输出值（参考）。
  · 期望 行（可编辑）= designer 手填的期望；.sv 断言用它对比。未填 → 生成时用 auto_out 兜底。
    手填值 != auto_out 不算负向——仿真 FAIL 恰恰说明表达式与 designer 意图不符（要抓的 bug）。
  · 改输入值 → auto_out 自动重算；已手填的期望保持不动。
  · 负向列的期望 = 故意填错值(预期断言 FAIL，自检 checker)，与 designer 期望是两回事。
  编辑（含手填期望）按 Excel 路径自动存盘，关 GUI 不丢；也可导出/导入编辑文件。

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
from dreg_verify import mux_gen                   # noqa: E402
from dreg_verify import resolver as R            # noqa: E402
from dreg_verify import expr as E                 # noqa: E402
from dreg_verify import vectors as V              # noqa: E402
from dreg_verify import sv_writer as W            # noqa: E402

# 记住上次加载的 Excel，下次启动自动加载（省去重复浏览/点击）
SETTINGS_PATH = os.path.join(os.path.expanduser("~"), ".dreg_verify_gui.json")
# 测试项编辑(含 designer 手填期望)的持久化文件：按 Excel 路径分桶，关 GUI 不丢、换表自动恢复。
# 与 SETTINGS_PATH 分开存——编辑数据可能较大，且语义上是"劳动成果"而非"界面偏好"。
EDITS_PATH = os.path.join(os.path.expanduser("~"), ".dreg_verify_edits.json")
_EDITS_PATH_DEFAULT = EDITS_PATH
# rowdict 里需要持久化的字段（计算字段 correct/expected/_vec 等加载后重算，不落盘）
_ROW_PERSIST_KEYS = ("kind", "wrong_value", "name", "user_added", "note", "designer_expected")


def _load_edits_file():
    """读取测试项编辑持久化文件。返回 {excel_path: {"edits": {...}, "neg_only": {...}}}。"""
    try:
        with open(EDITS_PATH, encoding="utf-8") as f:
            d = json.load(f)
            return d if isinstance(d, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def _save_edits_file(d):
    # 测试环境(pytest)下默认不落盘(防污染用户真实编辑)；测试可 monkeypatch EDITS_PATH 到临时文件启用
    if "pytest" in sys.modules and EDITS_PATH == _EDITS_PATH_DEFAULT:
        return
    try:
        with open(EDITS_PATH, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False)
    except Exception:  # noqa: BLE001
        pass


def _serialize_rows(rows):
    """rowdict 列表 → 可 JSON 化的精简结构（只留输入取值与用户意图，计算字段重算）。"""
    out = []
    for rd in rows:
        d = {"base_values": {k: int(v) for k, v in rd.get("base_values", {}).items()}}
        for k in _ROW_PERSIST_KEYS:
            v = rd.get(k)
            if v is not None and v != "" and v is not False:
                d[k] = v
        d.setdefault("kind", "pos")
        out.append(d)
    return out


def _deserialize_rows(rows_json):
    """JSON 结构 → rowdict 列表（correct/expected 等由 _recompute_row 重算）。"""
    out = []
    for d in rows_json or []:
        if not isinstance(d, dict):
            continue
        rd = {"base_values": {str(k): int(v) for k, v in (d.get("base_values") or {}).items()},
              "kind": d.get("kind", "pos"), "note": d.get("note", "")}
        for k in ("wrong_value", "name", "designer_expected"):
            if d.get(k) is not None:
                rd[k] = d[k]
        if d.get("user_added"):
            rd["user_added"] = True
        out.append(rd)
    return out


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


def _skipped_detail_text(skipped):
    """被跳过信号的明细文本：每个信号一段，列出哪些输入不可驱动及原因。
    skipped: list[(out_name, assert_id, risky)]，risky = list[(字母, 输入名, 原因)]。"""
    parts = []
    for name, _aid, risky in skipped:
        reasons = "\n".join("    %s = %s：%s" % (letter, base, note or "不可驱动")
                            for letter, base, note in risky)
        parts.append("%s\n%s" % (name, reasons))
    return ("跳过原因：以下输入在 ENV_RF 层探不到（force 会 elaboration 失败）。\n"
            "如需强制生成请用 CLI --include-risky。\n\n" + "\n\n".join(parts))


(COL_SEL, COL_NEG, COL_R, COL_K, COL_OWNER, COL_TYPE, COL_TOP, COL_STATUS,
 COL_PREFIX, COL_EXPR) = range(10)
HEADERS = ["选", "负向", "R", "输出名(K)", "owner", "type", "top", "状态", "探针前缀", "表达式"]
STATUS_LABEL = {"clean": "clean", "wire-fallback": "⚠wire兜底",
                "unresolved": "✗未解析", "parse-err": "✗解析错",
                "needs-prefix": "⚠需探针前缀", "bare-probe": "裸名探针"}
STATUS_HELP = {"clean": "输入都解析到具体 net，可正常 force/RF_WRITE 驱动",
               "wire-fallback": "有输入回退成 wire 兜底；elaboration 可能在 ENV_RF 层找不到该 net",
               "unresolved": "有输入未解析到 net（ENV_RF 探不到，仿真会 CUVUNF）",
               "parse-err": "表达式或输入解析出错",
               "needs-prefix": "要 force 子模块内的衔接网（级联网/wire 兜底）但没配前缀——"
                               "先跑 scan_rtl 配好探针前缀再生成（否则这组会跳过）",
               "bare-probe": "输出 top_out=0（喂内部、非芯片顶层输出）——已按裸名探针 `ENV_RF.<名> "
                             "照常生成；若仿真 elaboration 报 CUVUNF 说明它埋在子模块，"
                             "再跑 scan_rtl 配前缀重生成即可（不是错误）"}
# 状态列颜色：红=信号坏掉(会 elaboration 失败)；橙=要前缀否则跳过；蓝=信息(裸名探针已生成,可选配前缀)
STATUS_FG = {"needs-prefix": QtGui.QColor("#cc7a00"), "bare-probe": QtGui.QColor("#2a7ab0")}
# 输入来源(found_in)的中文标签——明细面板用；未映射的原样显示
FOUND_IN_LABEL = {"tmm": "tmm命中", "regmap": "regmap命中", "logic": "级联前级",
                  "logic-internal": "内部信号", "wire": "wire兜底",
                  "prefixed-wire": "前缀wire", "self-input": "自引用前级",
                  "needs-prefix": "需探针前缀(跑scan_rtl)",
                  "logic-computed": "上游计算网(展开驱动)"}
# 「输入信号」表(真值表上方)：把字母→信号/角色/驱动 集中成一张可读的小表(取代头部那行难读的图例)
INPUT_COLS = ["字母", "信号(位宽)", "角色", "类型", "驱动"]
# 负向用 琥珀，刻意区别于"状态列红=信号坏掉/会 elaboration 失败"；红只留给真正的故障
NEG_BG = QtGui.QColor("#fdeccb")        # 负向用例行底色（琥珀，能压过隔行底色）
NEG_FG = QtGui.QColor("#9a5b00")        # 负向列头/标记文字色（深琥珀）
HL_BG = QtGui.QColor("#dbe8ff")         # 当前选中测试列的高亮底色（淡蓝；列多横滚时不丢"我在哪列"）
HL_NEG_BG = QtGui.QColor("#ffdca0")     # 负向列又被选中：琥珀+高亮叠加（更深的琥珀）
# 「期望」行(designer 手填)的状态色：
DSGN_BG = QtGui.QColor("#e2f2e2")       # 已手填且 == auto_out（绿：designer 审过且与表达式一致）
DIFF_BG = QtGui.QColor("#ffd9d9")       # 已手填但 != auto_out（红：表达式可能与 designer 意图不符）
FB_FG = QtGui.QColor("#999999")         # 未手填（灰字显示兜底的 auto_out 值）

# 「级联 ?」内置帮助的兜底内容：仓库根目录的 级联模式说明.md 缺失时显示(正常显示完整 .md)
CASCADE_DOC_FALLBACK = """\
# 级联模式 — 展开上游 vs force级联网

> 完整图解在仓库根目录『级联模式说明.md』，当前没找到该文件，以下为内置摘要。

当一个信号的输入引用了**另一行 logic 算出来的网**(级联)时，有两种驱动办法：

| | **展开上游**(默认) | **force级联网** |
|---|---|---|
| 做法 | 把上游表达式代入，驱动它的源头寄存器/管脚 | 直接 force 那根 `_to_logic` 网 |
| 需要 scan_rtl 前缀 | 不需要(纯 Excel) | 需要(该网在 sig_logic 模块内部) |
| 验证粒度 | 上游+本行一起验(端到端) | 只验本行(隔离验证, fail 定位准) |
| 没配前缀时 | 照常生成 | 该信号被跳过(给原因) |

**建议**：日常用「展开上游」；某个信号 fail、想定位是上游还是本行的问题时，
切「force级联网」对可疑信号单独出 .sv(需先跑 scan_rtl 拿前缀)。
"""


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
        self._ti_cone = False    # 当前信号是否经 cone 展开(输入=叶子寄存器而非本行 A/B/C 列)
        self._ti_chain = []      # cone 展开链 [{"out","expr","subst"},...]，『展开链』面板显示用
        self._ti_rows = []
        self._ti_name_low = None
        self._ti_loaded_idx = None
        self._ti_hl_col = -1      # 当前高亮(选中)的测试列；-1=无
        self._ti_loading = False  # 程序化填表时屏蔽 itemChanged，防递归
        self._sig_loading = False # 程序化改信号表(含左侧负向勾选)时屏蔽其 itemChanged
        self._persist_suspended = False  # 批量操作时暂停逐次存盘，结束后统一存一次
        # mux 信号的 designer 手填期望：{信号名小写: {输入取值键(generator.mux_assign_key): int}}
        # mux 向量由 case 结构自动生成、不走 vector_overrides → 期望单独按取值键存（覆盖度切换不串号）
        self._mux_expected = {}
        self._ti_mux_vecs = []    # 当前 mux 信号的向量（期望行编辑时对号入座）
        self._ti_mux_exp_row = -1 # 当前 mux 表里"期望"行的行号
        # 真值表列宽：用户手动拖过 → 重建表格时保留手动宽度(换信号才恢复自动)
        self._ti_user_widths = False
        self._ti_auto_resizing = False
        # 探针前缀 {信号名小写: 层级前缀}：输出网在 ENV_RF 的子模块里时（如 pll_n 在
        # U_BT_LP_PLL_DIG 内部），断言探针写 `ENV_RF.<前缀>.<网名>。按 Excel 路径持久化。
        self._probe_prefixes = {}
        self._preview_source = None   # 预览页内容来源: None/"all"/"signal" —— 配置变更后联动刷新
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
        self.name_edit = QtWidgets.QLineEdit()
        self.name_edit.setPlaceholderText("搜索: 输出名/表达式/输入信号名(如 mon_active)…支持正则")
        self.name_edit.setToolTip("除输出名和表达式外，也按【输入信号名】匹配——\n"
                                  "搜某个输入(如 mon_active)即可列出所有用到它的输出信号。")
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
        b_prefix = QtWidgets.QPushButton("设置探针前缀")
        b_prefix.setToolTip("输出网不在 ENV_RF 顶层、而在子模块里时（如 pll_n 在 U_BT_LP_PLL_DIG 内部），\n"
                            "给勾选/选中的信号设置层级前缀 → 断言写 `ENV_RF.<前缀>.<信号名>。留空清除。")
        b_prefix.clicked.connect(self.on_set_probe_prefix)
        # 测试项编辑(含 designer 手填期望)的导出/导入：给同事复用、入版本库、跨机器迁移
        b_exp_edits = QtWidgets.QPushButton("导出编辑…")
        b_exp_edits.setToolTip("把当前 Excel 的全部测试项编辑(手填期望/负向/自定义列)导出为 .json 文件，\n"
                               "可给同事导入复用、入版本库存档。编辑本来就会自动存盘(关 GUI 不丢)，导出是为了共享。")
        b_exp_edits.clicked.connect(self.on_export_edits)
        b_imp_edits = QtWidgets.QPushButton("导入编辑…")
        b_imp_edits.setToolTip("从 .json 文件导入测试项编辑(手填期望/负向/自定义列)，与现有编辑合并：\n"
                               "同名信号以导入为准。文件里有、当前表里没有的信号会列出名字并跳过。")
        b_imp_edits.clicked.connect(self.on_import_edits)
        for b in (b_check, b_selall, b_selnone):
            bulk.addWidget(b)
        bulk.addWidget(QtWidgets.QLabel(" 负向:"))
        for b in (b_negall, b_negnone):
            bulk.addWidget(b)
        bulk.addWidget(b_prefix)
        bulk.addWidget(QtWidgets.QLabel(" 编辑:"))
        bulk.addWidget(b_exp_edits)
        bulk.addWidget(b_imp_edits)
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
            "【logic 信号】\n"
            "  精简 = 每种控制位组合各取 1 组代表数据(最少用例)\n"
            "  全面 = 每种控制位组合再扫多组数据(全0/全1/反码/走步/区分)\n"
            "  穷举 = 所有输入的全部组合(仅当总输入位≤10，否则自动退化为'全面')\n"
            "【mux 信号·单控制 logic 行(line/local 双路径)】\n"
            "  精简 = 每 case 1 条(x位取0) + 1 条另一路径抽测\n"
            "  全面 = 精简 + case 的 x 位展开 + 每 case 一轮反码数据(抓数据通路位坏死)\n"
            "  穷举 = 全面 + 另一条控制路径全扫每 case(两条物理驱动路径全验)\n"
            "【mux 信号·多控制 / 寄存器直出 / mux 级联(直接驱动控制)】\n"
            "  精简 = 每 case 1 条(x位取0)\n"
            "  全面 = 精简 + case 的 x 位展开 + 每 case 一轮反码数据(抓数据通路位坏死)\n"
            "  穷举 = 同全面(没有另一条物理控制路径可扫)")
        self.coverage.currentIndexChanged.connect(self.on_coverage_changed)
        self.cov_hint = QtWidgets.QLabel("")            # 实时显示当前信号的用例条数
        self.cov_hint.setStyleSheet("color:#1558d6;")
        self.max_tests = QtWidgets.QSpinBox(); self.max_tests.setRange(1, 100000); self.max_tests.setValue(256)
        self.max_tests.setToolTip("用例数上限(安全阀，防止穷举/全面产生过多用例)")
        self.max_tests.valueChanged.connect(self.on_coverage_changed)
        # 级联模式：输入引用"上游 logic 计算网"(如 d_ndiv_n 的 mode_sel_to_logic)时怎么驱动
        self.cascade_combo = QtWidgets.QComboBox()
        self.cascade_combo.addItems(["展开上游(推荐)", "force级联网"])
        self.cascade_combo.setToolTip(
            "输入引用『上游 logic 算出来的网』(级联)时怎么驱动，点旁边 ? 看图解：\n\n"
            "  展开上游(默认)：把上游表达式代入，改为驱动它的源头寄存器/管脚。\n"
            "      优点=纯 Excel、不需要探针前缀；代价=上游逻辑跟本行一起验，上游有 bug 会连带本行 fail\n\n"
            "  force级联网：直接 force 那根 _to_logic 网。\n"
            "      优点=每行 logic 隔离验证、fail 定位准；代价=该网在 sig_logic 模块内部，\n"
            "      必须先跑 scan_rtl 拿到层级前缀，否则该信号会被跳过")
        if _load_settings().get("cascade_mode") == "force":
            self.cascade_combo.setCurrentIndex(1)
        self.cascade_combo.currentIndexChanged.connect(self.on_cascade_mode_changed)
        cascade_help = QtWidgets.QPushButton("?")
        cascade_help.setFixedWidth(24)
        cascade_help.setToolTip("级联模式帮助：两种模式的图解与选择建议(程序内置窗口)")
        cascade_help.clicked.connect(self._open_cascade_doc)
        for w in (QtWidgets.QLabel("覆盖度:"), self.coverage, self.cov_hint,
                  QtWidgets.QLabel("   上限"), self.max_tests,
                  QtWidgets.QLabel("   级联:"), self.cascade_combo, cascade_help):
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
                ("加正向列", self.on_ti_add, "新增一条正向(真实)测试(输入全 0，auto_out 自动算，期望留空待填)"),
                ("复制列", self.on_ti_copy, "复制当前选中的测试列"),
                ("删除列", self.on_ti_del, "删除选中的测试列"),
                ("重命名列…", self.on_ti_rename_current, "给用户新增的测试列改名(双击列头亦可；自动生成的 T0/T1 不可改)"),
                ("auto→期望", self.on_ti_fill_expected,
                 "把 auto_out 填进「期望」行（只填未填的列，已手填的不动）。\n"
                 "⚠ 这等于直接采信表达式计算值——失去了 designer 独立核对的意义，确认表达式无误时再用"),
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

        # 「展开链」(仅 cone 信号显示)：本行 + 逐层代入的上游行。每行两种形式：
        #   Excel 原式(L 列原文，字母=该行自己的 A/B/C 列) = 字母代入真实信号名后的等价形式。
        # 链整体就是展开后的等价表达式——不强行合并成一行(深层 cone 会嵌套爆炸读不了)。
        self.ti_chain_cap = QtWidgets.QLabel("展开链  （① 本行 → 逐层代入的上游行；每行：Excel 原式 = 代入信号名）")
        self.ti_chain_cap.setStyleSheet("color:#445;font-weight:bold;")
        lay.addWidget(self.ti_chain_cap)
        self.ti_chain = QtWidgets.QPlainTextEdit()
        self.ti_chain.setReadOnly(True)
        self._mono(self.ti_chain)
        self.ti_chain.setLineWrapMode(QtWidgets.QPlainTextEdit.NoWrap)
        self.ti_chain.setToolTip("cone 展开过程：① 是被验证的本行，往下是被代入的上游行。\n"
                                 "每行第一条 = Excel L 列原文(字母是该行自己的 A~J 列)；\n"
                                 "第二条 = 字母换成真实信号名的等价形式，最末行的叶子信号\n"
                                 "与下方『输入信号』表的坐标(如 pll_n1.A)一一对应。")
        lay.addWidget(self.ti_chain)
        self.ti_chain_cap.hide(); self.ti_chain.hide()   # 非 cone 信号不占空间

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
                                  "(RO→force net；RW→RF_WRITE 地址+bit位)。\n"
                                  "字母列 = Excel 来源坐标：本行输入 = 列字母(A/B/C…)；"
                                  "展开上游(cone)后的叶子寄存器 = 『上游行名.字母』\n"
                                  "(如 pll_n1.A = logic 页 pll_n1 那一行的 A 列)。")
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
        # 列宽可拖动：给个好抓的最小宽度；用户手动拖过后，重建表格不再重置回自动宽度
        self.ti_table.horizontalHeader().setMinimumSectionSize(44)
        self.ti_table.horizontalHeader().sectionResized.connect(self._on_ti_section_resized)
        # Ctrl+D 复制列：用 WidgetShortcut 挂在真值表上——仅当表本身有焦点时触发；
        # 单元格正在编辑时焦点在子 QLineEdit 上，快捷键不会触发，避免打断/丢失编辑(对抗式审查 #1)
        copy_sc = QtGui.QShortcut(QtGui.QKeySequence("Ctrl+D"), self.ti_table)
        copy_sc.setContext(QtCore.Qt.WidgetShortcut)
        copy_sc.activated.connect(self.on_ti_copy)
        # 当前测试列高亮：列多横滚时随时知道光标在哪条测试上(行表头已是冻结的纵表头，自然不跟着横滚)
        self.ti_table.currentCellChanged.connect(self._ti_on_current_col)
        self.ti_table.horizontalHeader().setHighlightSections(True)
        lay.addWidget(self.ti_table, 1)

        hint = QtWidgets.QLabel(
            "行表头=输入信号名(粗体=控制/选择位，决定逻辑分支、测试穷举其 0/1 组合)；"
            "信号对应表达式里哪个字母(A/B/C…)看行表头悬停提示或上方『输入信号』表。"
            "auto_out 行=程序按表达式算的值(只读参考)；期望 行=designer 手填、.sv 断言用它对比"
            "(未填→生成时 auto_out 兜底；绿=与 auto_out 一致，红=不一致=表达式可能有 bug)。"
            "负向列(琥珀)=故意填错的自检用例。编辑(含手填期望)自动存盘并在生成/预览的 .sv 里生效。")
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

    # ───────────── 级联模式（展开上游 / force级联网） ─────────────
    def _cascade_mode(self):
        """级联模式下拉 → GenOptions/Resolver 的 cascade_mode 字符串。"""
        if not hasattr(self, "cascade_combo"):
            return "cone"
        return "force" if self.cascade_combo.currentIndex() == 1 else "cone"

    def on_cascade_mode_changed(self, *args):
        """级联模式切换 → 持久化 + 重建 Resolver 重析全表 + 重算当前编辑器信号(未自定义的)。"""
        st = _load_settings()
        st["cascade_mode"] = self._cascade_mode()
        _save_settings(st)
        if not self.wb:
            return
        self._reanalyze_all()
        if (self._ti_sig is not None and self._ti_name_low is not None
                and self._ti_name_low not in self._customized):
            self._load_test_items(self._ti_sig)
        self.status.showMessage("级联模式已切换为『%s』——含级联输入的信号已按新模式重新解析"
                                % self.cascade_combo.currentText())

    def _open_cascade_doc(self):
        """级联 ? → 程序内置帮助窗(直接渲染『级联模式说明.md』)，不调外部编辑器。"""
        if getattr(self, "_cascade_doc_dlg", None) is None:
            dlg = QtWidgets.QDialog(self)
            dlg.setWindowTitle("级联模式说明 — 展开上游 vs force级联网")
            dlg.setWindowFlags(dlg.windowFlags() | QtCore.Qt.WindowMaximizeButtonHint)
            lay = QtWidgets.QVBoxLayout(dlg)
            self._cascade_doc_view = QtWidgets.QTextBrowser()
            self._cascade_doc_view.setOpenExternalLinks(True)
            lay.addWidget(self._cascade_doc_view)
            bb = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Close)
            bb.rejected.connect(dlg.close)
            lay.addWidget(bb)
            dlg.resize(900, 720)
            self._cascade_doc_dlg = dlg
        # 每次打开都重读文件：文档更新后无需重启 GUI；找不到文件则退化为内置摘要
        doc = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "级联模式说明.md")
        try:
            with open(doc, encoding="utf-8") as f:
                md = f.read()
        except OSError:
            md = CASCADE_DOC_FALLBACK
        self._cascade_doc_view.setMarkdown(md)
        dlg = self._cascade_doc_dlg
        dlg.show(); dlg.raise_(); dlg.activateWindow()

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
        elif getattr(self, "_ti_mux_sig", None) is not None:
            self._load_mux_test_items(self._ti_mux_sig)   # mux：按新覆盖度重算(手填期望按取值键自动回填)
        self._update_cov_hint()

    def _update_cov_hint(self):
        """工具栏「覆盖度」旁实时显示当前信号的用例条数，把抽象档位变具体。logic 与 mux 信号都显示。"""
        if not hasattr(self, "cov_hint"):
            return
        # mux 信号：_ti_sig=None 但 _ti_mux_vecs 有向量 → 同样显示条数(与 logic 一致，用户反馈)
        if self._ti_sig is None:
            vecs = getattr(self, "_ti_mux_vecs", None) or []
            if getattr(self, "_ti_mux_sig", None) is None or not vecs:
                self.cov_hint.setText("")
                return
            n_filled = sum(1 for v in vecs if not v.is_negative and v.designer_expected is not None)
            extra = "，期望已手填 %d" % n_filled if n_filled else ""
            self.cov_hint.setText("→ 当前信号 %d 条%s" % (len(vecs), extra))
            return
        if not self._ti_rows:
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
        # logic 信号 + mux 组同表混排（用户拍板；mux 组鸭子兼容信号表所需的全部属性）
        self.signals = list(self.wb.logic) + list(self.wb.mux)
        # 探针前缀按 Excel 路径持久化：换表自动恢复上次配置（须在建 Resolver 前加载——wire 前缀要传进去）
        self._probe_prefixes = dict(_load_settings().get("probe_prefixes", {}).get(path, {}))
        # 解析画像：逐信号 try，一个坏信号不连累整体加载
        self._resolver = R.Resolver(self.wb, wire_prefixes=self._probe_prefixes,
                                    cascade_mode=self._cascade_mode())
        self._analysis = {}
        # 切换工作簿，清空旧的测试项编辑状态
        self._edited = {}
        self._customized = set()
        self._neg_only = {}
        self._mux_expected = {}
        # 编辑持久化按"已加载"的 Excel 路径分桶（不能用 path_edit 实时文本——
        # 用户改了路径还没点加载时，编辑仍属于旧表）
        self._loaded_excel_path = path
        self._ti_loaded_idx = None
        self._preview_source = None        # 换表后旧预览作废，不参与联动刷新
        self._clear_test_items("加载完成，点左侧信号查看测试项。")
        errs = []
        for i, s in enumerate(self.signals):
            try:
                self._analysis[i] = self._analyze_one(s)
            except Exception as ex:  # noqa: BLE001
                self._analysis[i] = {"status": "解析异常", "inputs": [], "out_net": "",
                                     "error": repr(ex)}
                errs.append((s.out_name, s.assert_id, repr(ex)))
        self._populate_filters()
        self._populate_table()
        nbad = sum(1 for a in self._analysis.values() if a["status"] != "clean")
        msg = ("已加载 %d 信号（logic %d + mux %d，%d 个非 clean）；tmm字段=%d regmap=%d"
               % (len(self.signals), len(self.wb.logic), len(self.wb.mux), nbad,
                  len(self.wb.tmm), len(self.wb.regmap)))
        if errs:
            msg += "；⚠ %d 个信号分析异常(状态'解析异常',点开看 error)" % len(errs)
            self.preview.setPlainText("分析异常的信号(请把下面发给维护者):\n" +
                                      "\n".join("R=%s %s: %s" % (a, n, e) for n, a, e in errs[:50]))
        # 恢复上次存盘的测试项编辑（含 designer 手填期望），并把负向勾选同步回左表
        n_restored = self._restore_edits()
        if n_restored:
            msg += "；已恢复 %d 个信号的测试项编辑(含手填期望)" % n_restored
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
        # 重建前按信号名保留勾选状态(选/负向)——设置探针前缀等操作会重建表格，
        # 不能把用户已勾选的测试清零
        prev_checks = {}
        for r in range(self.table.rowCount()):
            k_it = self.table.item(r, COL_K)
            sel_it = self.table.item(r, COL_SEL)
            neg_it = self.table.item(r, COL_NEG)
            if k_it is not None and sel_it is not None and neg_it is not None:
                prev_checks[k_it.text()] = (sel_it.checkState() == QtCore.Qt.Checked,
                                            neg_it.checkState() == QtCore.Qt.Checked)
        self.table.setRowCount(len(self.signals))
        for r, sig in enumerate(self.signals):
            was_sel, was_neg = prev_checks.get(sig.out_name, (False, False))
            try:
                self._set_check(r, COL_SEL, was_sel)
                self._set_check(r, COL_NEG, was_neg)
                self._set_text(r, COL_R, str(sig.assert_id))
                self._set_text(r, COL_K, sig.out_name)
                self._set_text(r, COL_OWNER, sig.owner)
                self._set_text(r, COL_TYPE, sig.suffix)
                self._set_text(r, COL_TOP, str(sig.top_output))
                st = self._analysis.get(r, {}).get("status", "?")
                it = QtWidgets.QTableWidgetItem(STATUS_LABEL.get(st, st))
                if st != "clean":
                    # 橙=needs-prefix(要前缀否则跳过)；蓝=bare-probe(裸名已生成,信息)；其余故障红
                    it.setForeground(STATUS_FG.get(st, QtGui.QColor("red")))
                tip = STATUS_HELP.get(st, st)
                err = self._analysis.get(r, {}).get("error")
                if err and st in ("needs-prefix", "bare-probe", "unresolved", "parse-err"):
                    tip = "%s\n\n%s" % (tip, err)        # 缺前缀/坏掉/裸名提示时把后端 error 全文带上
                it.setToolTip(tip)
                self.table.setItem(r, COL_STATUS, it)
                self.table.setItem(r, COL_PREFIX, self._prefix_cell(sig, self._analysis.get(r, {})))
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
            COL_PREFIX: "探针层级前缀：信号网不在 ENV_RF 顶层、而在子模块里时配置（点下方『设置探针前缀』）。\n"
                        "  输出→U_BT_LP_PLL_DIG = 断言探针带前缀（如 pll_n）\n"
                        "  mon_active→U_BT_LP_PLL_DIG = 该输入的 force 路径带前缀\n"
                        "蓝色 = 已生效；鼠标悬停可看完整路径。",
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
        n_by_input = 0          # 仅因"输入信号名匹配"而显示的行数（搜输入名时给出明确反馈）
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
            # 名字搜索：输出名 / 表达式 / 输入信号名（如搜 mon_active → 列出所有用它做输入的输出）
            if pat:
                # mux 组没有 inputs 属性（鸭子兼容 logic 时缺这一个）——只按输出名/表达式匹配，
                # 输入名命中交给 _analysis 的 inputs 行（控制信号/数据寄存器都在那里）
                ana_inputs = self._analysis.get(self._idx_of_row(r), {}).get("inputs", [])
                if isinstance(sig, excel_model.MuxGroup):
                    inputs = [i.get("base") for i in ana_inputs if i.get("base")]
                else:
                    inputs = [i["base"] for i in sig.inputs.values() if i.get("base")]
                if rx:
                    out_hit = rx.search(sig.out_name) or rx.search(sig.expr)
                    in_hit = any(rx.search(b) for b in inputs)
                else:
                    low = pat.lower()
                    out_hit = low in (sig.out_name + sig.expr).lower()
                    in_hit = any(low in b.lower() for b in inputs)
                if not out_hit and not in_hit:
                    show = False
                elif in_hit and not out_hit:
                    n_by_input += 1
            if top_only and str(sig.top_output).strip() not in ("1", "1.0", "True", "true"):
                show = False
            self.table.setRowHidden(r, not show)
            visible += show
        msg = "可见信号 %d / 共 %d" % (visible, len(self.signals))
        if pat and n_by_input:
            msg += "（其中 %d 个因输入信号匹配 %r 而列出）" % (n_by_input, pat)
        self.status.showMessage(msg)

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
        # mux 信号：负向不走编辑器 override（无表达式可编辑），勾选状态在生成时经 neg_signals 生效
        if isinstance(sig, excel_model.MuxGroup):
            want_mux = item.checkState() == QtCore.Qt.Checked
            self.status.showMessage("%s（mux）已%s负向——生成时追加 1 条故意填错的自检断言(_NEG)"
                                    % (sig.out_name, "标记" if want_mux else "清除"))
            return
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

    def _prefix_of(self, sig):
        """该信号配置的探针前缀（无则空串）。
        映射 key 兼容 Excel K 列名与 RTL 网名(_ls 后缀)——scan_rtl.py 导出的是后者。"""
        p = self._probe_prefixes
        return (p.get(sig.out_name.lower()) or p.get(sig.out_base.lower())
                or p.get(sig.rtl_name.lower()) or p.get(sig.rtl_base.lower()) or "")

    def _prefix_cell(self, sig, analysis):
        """探针前缀列：输出前缀 + 受前缀影响的输入(如 mon_active→U_BT_LP_PLL_DIG)。

        让用户一眼看到"映射生效在哪"：输出探针带前缀、哪些输入 wire 的 force 路径带前缀。
        非空时蓝色高亮；tooltip 给完整 force/assert 路径。
        """
        parts, tips = [], []
        out_pfx = self._prefix_of(sig)
        if out_pfx:
            parts.append("输出→%s" % out_pfx)
            tips.append("输出探针: %s" % analysis.get("out_net", ""))
        for i in analysis.get("inputs", []):
            if i.get("found_in") == "prefixed-wire":
                pfx = self._probe_prefixes.get((i.get("base") or "").lower(), "")
                parts.append("%s→%s" % (i["base"], pfx))
                tips.append("输入 %s: %s" % (i["base"], i.get("net", "")))
        it = QtWidgets.QTableWidgetItem("；".join(parts))
        if parts:
            it.setForeground(QtGui.QColor("#0a58c4"))    # 蓝 = 前缀已生效（区别于红=故障）
            it.setToolTip("探针前缀已生效：\n" + "\n".join(tips))
        return it

    def _save_probe_prefixes(self):
        """探针前缀按 Excel 路径写入 settings（pytest 下 no-op，与其它持久化策略一致）。"""
        st = _load_settings()
        all_maps = st.get("probe_prefixes", {})
        path = self.path_edit.text().strip()
        if self._probe_prefixes:
            all_maps[path] = dict(self._probe_prefixes)
        else:
            all_maps.pop(path, None)
        st["probe_prefixes"] = all_maps
        _save_settings(st)

    def on_set_probe_prefix(self):
        """探针前缀映射编辑器：每行『信号名=ENV_RF 下的层级路径』，可导入/导出复用。

        作用于两类网（同一张映射表）：
        ① 被验证输出（如 pll_n）→ 断言写 `ENV_RF.<层级>.pll_n[31:0]
        ② force 的输入 wire（如 mon_active）→ force `ENV_RF.<层级>.mon_active
        """
        if not self.wb:
            return
        dlg = QtWidgets.QDialog(self)
        dlg.setWindowTitle("探针前缀映射")
        lay = QtWidgets.QVBoxLayout(dlg)
        hint = QtWidgets.QLabel(
            "每行一条映射：信号名=ENV_RF 之下的层级路径。例如：\n"
            "    信号实际在  `ENV_RF.U_BT_LP_PLL_DIG.pll_n        →  pll_n=U_BT_LP_PLL_DIG\n"
            "    信号实际在  `ENV_RF.U_BT_LP_PLL_DIG.DIG_1.xxx    →  xxx=U_BT_LP_PLL_DIG.DIG_1\n"
            "被验证输出 → assert 探针带层级；force 输入 wire → force 路径带层级。\n"
            "删除行 = 清除映射；# 开头 = 注释。")
        lay.addWidget(hint)
        edit = QtWidgets.QPlainTextEdit()
        edit.setPlainText("\n".join("%s=%s" % (k, v)
                                    for k, v in sorted(self._probe_prefixes.items())))
        self._mono(edit)
        lay.addWidget(edit)

        btns = QtWidgets.QHBoxLayout()
        b_imp = QtWidgets.QPushButton("导入…")
        b_imp.setToolTip("从映射文件(.txt)导入：与现有合并，同名以导入为准")
        b_exp = QtWidgets.QPushButton("导出…")
        b_exp.setToolTip("把当前映射存为 .txt，下次/换表/给同事直接导入复用")
        btns.addWidget(b_imp); btns.addWidget(b_exp); btns.addStretch(1)
        bb = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok
                                        | QtWidgets.QDialogButtonBox.Cancel)
        bb.accepted.connect(dlg.accept); bb.rejected.connect(dlg.reject)
        btns.addWidget(bb)
        lay.addLayout(btns)

        def do_import():
            path, _ = QtWidgets.QFileDialog.getOpenFileName(
                dlg, "导入探针前缀映射", "", "映射文本 (*.txt);;全部文件 (*)")
            if not path:
                return
            try:
                with open(path, "r", encoding="utf-8") as f:
                    text = f.read()
            except OSError as ex:
                QtWidgets.QMessageBox.critical(dlg, "导入失败", str(ex))
                return
            merged = generator.parse_probe_prefix_lines(edit.toPlainText())
            merged.update(generator.parse_probe_prefix_lines(text))
            edit.setPlainText("\n".join("%s=%s" % (k, v) for k, v in sorted(merged.items())))

        def do_export():
            path, _ = QtWidgets.QFileDialog.getSaveFileName(
                dlg, "导出探针前缀映射", "probe_prefixes.txt", "映射文本 (*.txt)")
            if not path:
                return
            try:
                with open(path, "w", encoding="utf-8") as f:
                    f.write(edit.toPlainText().rstrip() + "\n")
            except OSError as ex:
                QtWidgets.QMessageBox.critical(dlg, "导出失败", str(ex))
                return
            QtWidgets.QMessageBox.information(dlg, "完成", "已导出：%s" % path)

        b_imp.clicked.connect(do_import)
        b_exp.clicked.connect(do_export)
        dlg.resize(620, 460)
        if dlg.exec() != QtWidgets.QDialog.Accepted:
            return
        mapping = generator.parse_probe_prefix_lines(edit.toPlainText())
        self._probe_prefixes = mapping
        self._save_probe_prefixes()
        self._reanalyze_all()
        # 反馈映射生效范围：输出探针带前缀 / 任一输入 force 路径带前缀 的信号数
        affected = sum(1 for i, s in enumerate(self.signals)
                       if self._prefix_of(s)
                       or any(x.get("found_in") == "prefixed-wire"
                              for x in self._analysis.get(i, {}).get("inputs", [])))
        self.status.showMessage("探针前缀映射已更新（共 %d 条），影响 %d 个信号"
                                "（见蓝色『探针前缀』列；状态列应变 clean）" % (len(mapping), affected))

    def _analyze_one(self, sig):
        """单信号解析画像：logic 走 analyze_signal，mux 组走 analyze_mux_group（2026-06-03 第九轮）。"""
        if isinstance(sig, excel_model.MuxGroup):
            mode, exhaustive = self._coverage()
            # 传全部已配置探针前缀（不只本信号的）——级联衔接网的前缀也要能命中，
            # mux_prefix_risks 才能正确区分"还缺前缀"和"已配好可生成"
            opts = generator.GenOptions(probe_prefixes=self._probe_prefixes)
            return generator.analyze_mux_group(
                self._resolver, self.wb, sig,
                mode=mux_gen.coverage_mode(mode, exhaustive),
                probe_prefix=self._prefix_of(sig), opts=opts)
        return generator.analyze_signal(self._resolver, sig, wb=self.wb,
                                        probe_prefix=self._prefix_of(sig))

    def _reanalyze_all(self):
        """探针前缀/级联模式变更后重建 Resolver（两者都影响所有信号的输入解析）并刷新全表。"""
        self._resolver = R.Resolver(self.wb, wire_prefixes=self._probe_prefixes,
                                    cascade_mode=self._cascade_mode())
        for i, s in enumerate(self.signals):
            try:
                self._analysis[i] = self._analyze_one(s)
            except Exception as ex:  # noqa: BLE001
                self._analysis[i] = {"status": "解析异常", "inputs": [], "out_net": "",
                                     "error": repr(ex)}
        self._populate_table()
        # 右侧编辑器/预览页联动刷新：旧内容是按旧前缀算的，留着会误导（"导出有前缀但预览没有"）
        if self._ti_sig is not None:
            self._load_test_items(self._ti_sig)
        self._refresh_preview()

    def _refresh_preview(self):
        """按当前配置重算 .sv 预览页（仅当预览页有内容时；不抢标签页焦点）。"""
        src = self._preview_source
        if src == "all" and self._collect():
            self.on_preview(switch_tab=False)
        elif src == "signal" and self._ti_sig is not None:
            self.on_ti_preview_signal(switch_tab=False)

    def _expand_sig(self, sig):
        """解析 + 按需 cone 展开 + 输入分组。
        返回 (node, bindings, groups, chain, err)；失败时 node=None。
        chain = cone 展开链 [{"out","expr","subst"},...]；非 cone 信号为空 list，
        故 bool(chain) 即"是否做过 cone 展开"。"""
        chain = []
        try:
            node, bindings, _expanded = generator.expand_signal(self.wb, self._resolver, sig,
                                                                chain_out=chain)
        except E.ExprError as ex:
            return None, None, None, [], "表达式解析失败: %s" % ex
        except generator.cone.ConeError as ex:
            return None, None, None, [], "cone 展开失败: %s" % ex
        return node, bindings, V.input_groups(node, bindings), chain, None

    def _set_signal_negatives(self, sig, want_neg, which):
        """给某信号(重新)设置负向测试：保留全部正向测试，按 first/all 追加正向测试的"故意填错"
        副本作为负向；want_neg=False 则删除所有负向。可作用于未显示的信号(存进 override)。"""
        if self._resolver is None:
            return
        name_low = sig.out_name.lower()
        node, bindings, groups, _chain, _err = self._expand_sig(sig)
        if node is None:
            return
        hand_edited = (name_low in self._edited and name_low not in self._neg_only)
        # 仅靠"加负向"定制的信号，清负向 = 回到纯自动 → 整体撤销定制(恢复默认 risky-skip 等行为)
        if not want_neg and not hand_edited:
            self._edited.pop(name_low, None)
            self._customized.discard(name_low)
            self._neg_only.pop(name_low, None)
            self._persist_edits()
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
        self._persist_edits()                  # 负向定制也是编辑，存盘

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
        self._persist_suspended = True       # 批量操作只在结束后统一存盘一次(避免每信号写一次文件)
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
            self._persist_suspended = False
        self._persist_edits()
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
            src = FOUND_IN_LABEL.get(inp["found_in"], inp["found_in"])
            lines.append("  %s=%s  [%s/%s]%s  ->  %s"
                         % (inp["letter"], inp["base"], inp["kind"], src, flag, inp["net"]))
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
        self._ti_cone = False
        self._ti_chain = []
        self._ti_name_low = None
        self._ti_hl_col = -1
        self._ti_mux_vecs = []; self._ti_mux_exp_row = -1   # mux 期望编辑状态一并清(防陈旧引用)
        self.ti_header.setText(header_text)
        self._ti_loading = True
        try:
            self.ti_table.clear()
            self.ti_table.setRowCount(0)
            self.ti_table.setColumnCount(0)
            if hasattr(self, "ti_inputs"):
                self.ti_inputs.setRowCount(0)
            self._populate_chain()       # 空链 → 隐藏『展开链』面板
        finally:
            self._ti_loading = False
        self._update_cov_hint()

    def _load_test_items(self, sig):
        if not self.wb or self._resolver is None or sig is None:
            return
        self._ti_mux_sig = None
        # mux 页信号：输入由 case 结构自动生成（只读），期望行可由 designer 手填
        if isinstance(sig, excel_model.MuxGroup):
            self._load_mux_test_items(sig)
            return
        name_low = sig.out_name.lower()
        if name_low != self._ti_name_low:
            self._ti_user_widths = False     # 换信号 → 列宽恢复自动适应(手动宽度只在同一信号内保留)
        node, bindings, groups, chain, err = self._expand_sig(sig)
        if node is None:
            self._clear_test_items("信号: %s — %s（无法生成测试项）" % (sig.out_name, err))
            return
        self._ti_sig = sig; self._ti_node = node
        self._ti_bindings = bindings; self._ti_groups = groups
        self._ti_cone = bool(chain)       # 头部/输入表据此标注"已展开上游"
        self._ti_chain = chain            # 展开链(本行+逐层代入的上游行)，cone 信号显示
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
        self._populate_chain()        # 『展开链』面板(仅 cone 信号显示)
        self._populate_inputs()       # 上方『输入信号』表(字母/角色/驱动)，随信号刷新
        self._ti_populate()

    def _load_mux_test_items(self, grp):
        """mux 信号的测试项展示（2026-06-03 第九轮只读 → 第十一轮续：期望行可手填）。

        先 _clear_test_items（_ti_sig=None → 列编辑按钮安全屏蔽），再填充：
        行 = 控制行输入 + 数据寄存器（只读，由 case 结构决定）+ auto_out（只读）+ 期望（可手填）。
        手填期望按"输入取值键"存进 _mux_expected → 生成/报告经 GenOptions.mux_expected 生效；
        负向勾选 / 生成 / 预览本信号 照常可用（走 neg_signals / build 路径）。"""
        if getattr(self, "_ti_mux_sig", None) is not grp:
            self._ti_user_widths = False     # 换信号 → 列宽恢复自动适应
        # 重建前记下现有列宽(必须在 _clear_test_items 之前——clear 会把列数清零)
        old_widths = [self.ti_table.columnWidth(c) for c in range(self.ti_table.columnCount())]
        self._clear_test_items("")
        self._ti_mux_sig = grp
        exp = mux_gen.expand_mux_group(self.wb, self._resolver, grp)
        # 『输入信号』表：解析有问题也照样填（哪个输入坏了一眼看到）——修"mux 点开输入信号框空白"
        self._populate_mux_inputs(grp, exp)
        if exp["issues"]:
            self.ti_header.setText("mux 信号 %s：无法生成测试 — %s"
                                   % (grp.out_name, "；".join(exp["issues"])))
            return
        mode, exhaustive = self._coverage()
        mux_mode = mux_gen.coverage_mode(mode, exhaustive)
        vecs, meta = mux_gen.make_mux_vectors(grp, exp, mode=mux_mode,
                                              max_tests=self.max_tests.value())
        if meta.get("value_collision") or not vecs:
            self.ti_header.setText("mux 信号 %s：互异值分配失败或控制信号无驱动路径（见左表状态列）"
                                   % grp.out_name)
            return
        # 已手填的期望按输入取值键回填到向量（与生成/报告同一逻辑）
        generator.apply_mux_expected(vecs, self._mux_expected.get(grp.out_name.lower()))
        self._ti_mux_vecs = vecs
        used = exp["used_vars"]
        self._ti_mux_exp_row = len(used) + 1
        self._update_mux_header(grp, vecs, mux_mode, meta)
        self._ti_loading = True
        try:
            self.ti_table.clear()
            self.ti_table.setColumnCount(len(vecs))
            self.ti_table.setHorizontalHeaderLabels([W.test_label(v) for v in vecs])
            self.ti_table.setRowCount(len(used) + 2)
            vlabels = []
            for ri, key in enumerate(used):
                b = exp["bindings"][key]
                role = "控制" if key.startswith("c:") else "数据"
                vlabels.append("%s (%s)" % (b.base, role))
                for ci, v in enumerate(vecs):
                    val = v.assignments.get(key, 0)
                    txt = ("0x%X" % val) if b.width > 4 else format(val, "0%db" % max(b.width, 1))
                    it = QtWidgets.QTableWidgetItem(txt)
                    it.setFlags(QtCore.Qt.ItemIsEnabled)            # 只读(输入由 case 结构决定)
                    self.ti_table.setItem(ri, ci, it)
            # auto_out 行(只读) + 期望 行(可手填)——与 logic 编辑器同语义/同配色
            vlabels.append("auto_out")
            vlabels.append("期望(进.sv)")
            for ci in range(len(vecs)):
                self._render_mux_exp_col(ci, len(used))
            self.ti_table.setVerticalHeaderLabels(vlabels)
            self._ti_fit_columns(old_widths)
        finally:
            self._ti_loading = False
        self._update_cov_hint()

    def _update_mux_header(self, grp, vecs, mux_mode, meta=None):
        """mux 编辑器头部：case 结构 + 覆盖度说明 + 期望手填进度。

        两种形态覆盖度文案不同（meta['scan_path']=='direct' 为通用形态：多控制/寄存器直出/级联）：
          LPBT（单控制 logic 行，line/local 双路径）= 历史三档文案不变；
          通用形态没有"另一条物理控制路径"概念，三档按 x 位展开 + 反码数据轮描述。
        """
        if (meta or {}).get("scan_path") == "direct":
            cov_desc = {"min": "每 case 1 条（x位取0）",
                        "max": "每 case×x位展开 + 反码数据轮",
                        "exhaustive": "同全面（无另一条控制路径概念）"}[mux_mode]
        else:
            cov_desc = {"min": "每 case 1 条 + 另一路径抽测",
                        "max": "每 case×x位展开 + 反码数据轮 + 另一路径抽测",
                        "exhaustive": "每 case×x位展开 + 反码数据轮 + 另一条控制路径全扫"}[mux_mode]
        pos = [v for v in vecs if not v.is_negative]
        n_filled = sum(1 for v in pos if v.designer_expected is not None)
        self.ti_header.setText(
            "mux 信号: %s = case(%s) %d 选 1　|　覆盖度=%s → 测试 %d 个（%s）　|　"
            "期望已手填 %d/%d　|　输入由 case 结构自动生成（只读）；期望行可手填，负向勾选/生成/预览照常可用"
            % (grp.out_name, generator._mux_ctrl_desc(grp), len(grp.cases),
               self.coverage.currentText(), len(vecs), cov_desc, n_filled, len(pos)))

    def _render_mux_exp_col(self, ci, n_inputs):
        """渲染 mux 表第 ci 列的 auto_out + 期望 两格（手填状态变化时单列重绘）。"""
        v = self._ti_mux_vecs[ci]
        de = v.designer_expected
        # auto_out 格（只读）
        autoit = QtWidgets.QTableWidgetItem(W.fmt_bin(v.exp_value, v.exp_width))
        autoit.setFlags(QtCore.Qt.ItemIsEnabled)
        fa = autoit.font(); fa.setItalic(True); autoit.setFont(fa)
        autoit.setForeground(QtGui.QColor("#555555"))
        autoit.setToolTip("auto_out：程序按 case 结构算出的值（只读参考）。\n"
                          ".sv 断言对比的是下面 designer 手填的「期望」(未填→兜底用此值)。")
        self.ti_table.setItem(n_inputs, ci, autoit)
        # 期望 格（可手填；显示语义与 logic 编辑器一致：未填=空白灰、一致=绿、不一致=红）
        exp_text = "" if de is None else W.fmt_bin(de, v.exp_width)
        expit = QtWidgets.QTableWidgetItem(exp_text)
        expit.setFlags(QtCore.Qt.ItemIsEnabled | QtCore.Qt.ItemIsSelectable | QtCore.Qt.ItemIsEditable)
        ff = expit.font(); ff.setBold(de is not None); expit.setFont(ff)
        if de is None:
            expit.setForeground(FB_FG)
            expit.setToolTip("未手填——生成 .sv 时用 auto_out=%s 兜底。\n"
                             "双击填入你认为的输出值(按 case 结构应选中的寄存器值)。"
                             % W.fmt_bin(v.exp_value, v.exp_width))
        elif de == v.exp_value:
            expit.setBackground(DSGN_BG)
            expit.setToolTip("designer 手填期望 = %s，与 auto_out 一致 ✓\n清空单元格可恢复未填(兜底)。"
                             % W.fmt_bin(de, v.exp_width))
        else:
            expit.setBackground(DIFF_BG)
            expit.setToolTip("⚠ designer 手填期望 = %s，但 case 结构算出 auto_out = %s！\n"
                             "若你确认期望没填错，则 mux 配置与你的意图不符(仿真该测试会 FAIL)。\n"
                             ".sv 断言用你手填的期望；清空单元格可恢复未填(兜底)。"
                             % (W.fmt_bin(de, v.exp_width), W.fmt_bin(v.exp_value, v.exp_width)))
        self.ti_table.setItem(n_inputs + 1, ci, expit)

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
        """重算一行 correct(auto_out)/expected/is_negative，并缓存向量。显式传上下文，可对任意信号重算。

        rd['kind']:
          'pos' —— 正向(真实)测试：correct = auto_out(表达式计算值)；
                   expected = designer 手填期望(rd['designer_expected'])，未填 → auto_out 兜底。
                   手填值 != auto_out 不算负向：仿真 FAIL 恰恰说明表达式与 designer 意图不符。
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
            de = rd.get("designer_expected")
            if de is not None:
                de = de & m                          # 手填期望按输出位宽裁剪
                rd["designer_expected"] = de
            rd["expected"] = de if de is not None else correct
            rd["is_negative"] = False
            rd["_vec"] = V.make_vector_from_base_values(
                node, bindings, groups, rd["base_values"], out_width, designer_expected=de)

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
        # cone 标记：表达式里引用了内部信号/上游计算网 → 输入已展开成叶子寄存器，
        # 『输入信号』表的字母列显示 Excel 来源坐标("上游行名.字母")而非本行 A/B/C
        cone_tag = "   [已展开上游→输入为叶子寄存器]" if getattr(self, "_ti_cone", False) else ""
        # 期望手填进度：designer 手填了几条/共几条正向（其余生成时 auto_out 兜底）
        pos_rows = [rd for rd in self._ti_rows if rd.get("kind") != "neg"]
        n_filled = sum(1 for rd in pos_rows if rd.get("designer_expected") is not None)
        fill_tag = ("   期望已手填 %d/%d" % (n_filled, len(pos_rows))) if pos_rows else ""
        # 表达式写成 "输出 = RHS" 等式；字母对照已下移到『输入信号』表，头部保持精简
        self.ti_header.setText(
            "信号 %s     %s = %s     用例 %d 条%s%s%s"
            % (self._ti_sig.out_name, self._ti_sig.out_base or "out", self._ti_sig.expr,
               len(self._ti_rows), fill_tag, tag, cone_tag))

    def _ti_mark_customized(self):
        if not self._ti_sig:
            return
        self._edited[self._ti_name_low] = {"sig": self._ti_sig, "rows": self._ti_rows}
        self._customized.add(self._ti_name_low)
        self._neg_only.pop(self._ti_name_low, None)   # 有手工编辑 → 不再是"纯负向定制"(冻结，保住编辑)
        self._update_ti_header(True)
        self._sync_left_neg()        # 右表负向变化 → 回写左侧"负向"勾选
        self._persist_edits()        # 编辑(含手填期望)即时存盘，关 GUI 不丢

    # ───────────── 测试项编辑持久化（designer 手填期望是劳动成果，必须存盘） ─────────────
    def _persist_edits(self):
        """把当前 Excel 的全部测试项编辑(含 designer 手填期望/负向/自定义列)写到 EDITS_PATH。

        按"已加载"的 Excel 路径分桶；_persist_suspended=True 时跳过(批量操作中，结束后统一存一次)。"""
        if getattr(self, "_persist_suspended", False):
            return
        path = getattr(self, "_loaded_excel_path", "") or ""
        if not path:
            return
        allbuckets = _load_edits_file()
        if self._edited or self._mux_expected:
            allbuckets[path] = {
                "edits": {name: _serialize_rows(ed["rows"]) for name, ed in self._edited.items()},
                "neg_only": dict(self._neg_only),
                # mux 手填期望：{信号名: {输入取值键: int}}（mux 不走 rows 编辑模型，单独一段）
                "mux_expected": {name: dict(m) for name, m in self._mux_expected.items() if m},
            }
        else:
            allbuckets.pop(path, None)        # 编辑全清空 → 桶也删掉
        _save_edits_file(allbuckets)

    def _restore_edits(self):
        """加载 Excel 后恢复上次存盘的测试项编辑。信号在新表里找不到 → 跳过并提示(列名字+原因)。
        返回恢复的信号个数。"""
        path = getattr(self, "_loaded_excel_path", "") or ""
        bucket = _load_edits_file().get(path)
        if not bucket:
            return 0
        n_restored, missing = self._apply_edits_bucket(bucket)
        self._sync_neg_checks_from_edits()
        if missing:
            # 追加(不覆盖)——on_load 可能已往预览页写了"分析异常"清单，两份信息都要保留
            self.preview.appendPlainText(
                "\n以下信号有上次保存的测试项编辑，但在当前 Excel 里找不到(已跳过)：\n"
                + "\n".join("  %s — 信号名在 logic 页不存在(表改名/删行?)" % n for n in missing))
        return n_restored

    def _sync_neg_checks_from_edits(self):
        """把 _edited 里含负向行的信号在左表勾上"负向"列（恢复/导入编辑后调用）。"""
        self._sig_loading = True
        try:
            for r in range(self.table.rowCount()):
                sig = self._sig_of_row(r)
                if sig is None or isinstance(sig, excel_model.MuxGroup):
                    continue
                if self._signal_has_negative(sig.out_name.lower()):
                    cell = self.table.item(r, COL_NEG)
                    if cell is not None:
                        cell.setCheckState(QtCore.Qt.Checked)
        finally:
            self._sig_loading = False

    def _apply_edits_bucket(self, bucket):
        """把一个编辑桶({"edits":{...},"neg_only":{...},"mux_expected":{...}})应用到当前工作簿。
        返回 (恢复个数, 找不到的信号名列表)。供恢复与导入共用。"""
        by_name = {s.out_name.lower(): s for s in self.wb.logic} if self.wb else {}
        n_restored, missing = 0, []
        for name_low, rows_json in (bucket.get("edits") or {}).items():
            sig = by_name.get(name_low)
            if sig is None:
                missing.append(name_low)
                continue
            node, bindings, groups, _chain, _err = self._expand_sig(sig)
            if node is None:
                missing.append(name_low + "（表达式解析失败）")
                continue
            rows = _deserialize_rows(rows_json)
            for rd in rows:
                self._recompute_row(node, bindings, groups, sig.out_width, rd)
            self._edited[name_low] = {"sig": sig, "rows": rows}
            self._customized.add(name_low)
            n_restored += 1
        for name_low, rule in (bucket.get("neg_only") or {}).items():
            if name_low in self._edited:
                self._neg_only[name_low] = rule if rule in ("first", "all") else "first"
                self._customized.add(name_low)
        # mux 手填期望：信号在当前表的 mux 页里找不到 → 跳过并列名字
        mux_by_name = ({g.out_name.lower(): g for g in self.wb.mux} if self.wb else {})
        for name_low, exp_map in (bucket.get("mux_expected") or {}).items():
            if name_low not in mux_by_name:
                missing.append(name_low + "（mux 页不存在）")
                continue
            if not isinstance(exp_map, dict) or not exp_map:
                continue
            merged = self._mux_expected.setdefault(name_low, {})
            merged.update({str(k): int(v) for k, v in exp_map.items()})
            n_restored += 1
        return n_restored, missing

    def on_export_edits(self):
        """把当前 Excel 的全部测试项编辑(logic 行编辑 + mux 手填期望)导出为 .json（给同事/版本库/跨机器）。"""
        if not self._edited and not self._mux_expected:
            QtWidgets.QMessageBox.information(self, "提示", "当前没有任何测试项编辑可导出。\n"
                                              "(手填期望/加负向/自定义列之后再导出)")
            return
        excel = (self.path_edit.text() or "").strip()
        default = os.path.splitext(os.path.basename(excel) or "dreg")[0] + "_edits.json"
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "导出测试项编辑", default,
                                                        "JSON (*.json)")
        if not path:
            return
        payload = {
            "dreg_verify_edits": 1,
            "excel": os.path.basename(excel),
            "edits": {name: _serialize_rows(ed["rows"]) for name, ed in self._edited.items()},
            "neg_only": dict(self._neg_only),
            "mux_expected": {name: dict(m) for name, m in self._mux_expected.items() if m},
        }
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=1)
        except OSError as ex:
            QtWidgets.QMessageBox.critical(self, "导出失败", str(ex))
            return
        n_de = sum(1 for ed in self._edited.values()
                   for rd in ed["rows"] if rd.get("designer_expected") is not None)
        n_mux = sum(len(m) for m in self._mux_expected.values())
        QtWidgets.QMessageBox.information(
            self, "完成", "已导出 %d 个信号的测试项编辑（logic 手填期望 %d 条，mux 手填期望 %d 条）：\n%s"
            % (len(self._edited) + len(self._mux_expected), n_de, n_mux, path))

    def on_import_edits(self):
        """从 .json 导入测试项编辑，与现有合并（同名信号以导入为准）。跳过的信号列名字+原因。"""
        if not self.wb:
            QtWidgets.QMessageBox.information(self, "提示", "请先加载 Excel")
            return
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "导入测试项编辑", "",
                                                        "JSON (*.json);;全部文件 (*)")
        if not path:
            return
        try:
            with open(path, encoding="utf-8") as f:
                payload = json.load(f)
        except (OSError, ValueError) as ex:
            QtWidgets.QMessageBox.critical(self, "导入失败", "无法读取/解析 %s：\n%s" % (path, ex))
            return
        if not isinstance(payload, dict) or ("edits" not in payload and "mux_expected" not in payload):
            QtWidgets.QMessageBox.critical(self, "导入失败",
                                           "%s 不是测试项编辑文件(缺少 edits/mux_expected 段)。" % path)
            return
        n_restored, missing = self._apply_edits_bucket(payload)
        self._sync_neg_checks_from_edits()
        self._persist_edits()                      # 导入的编辑也进入自动存盘
        if self._ti_sig is not None:               # 当前编辑器里的信号若被导入覆盖 → 刷新显示
            self._load_test_items(self._ti_sig)
        elif getattr(self, "_ti_mux_sig", None) is not None:   # mux 信号同理
            self._load_mux_test_items(self._ti_mux_sig)
        msg = "已导入 %d 个信号的测试项编辑。" % n_restored
        if missing:
            msg += "\n\n以下信号在文件里有编辑、但当前 Excel 里找不到(已跳过)：\n" + \
                   "\n".join("  %s" % n for n in missing[:30])
            if len(missing) > 30:
                msg += "\n  …等共 %d 个" % len(missing)
        QtWidgets.QMessageBox.information(self, "导入完成", msg)
        self.status.showMessage("已导入测试项编辑：%s（%d 个信号）" % (path, n_restored))

    # 纵向(真值表)布局：每个输入/输出一行(纵表头)，每条测试一列 T0/T1...。
    #   行: 0..G-1 = 各输入(base)；R_AUTO = auto_out(表达式计算，只读)；R_EXP = 期望(designer 手填)。
    #   列: 每列一条测试用例(正向 或 负向；负向列标红、列头带 _NEG)。
    def _ti_dims(self):
        self._ti_G = len(self._ti_groups)
        self.R_AUTO = self._ti_G          # auto_out 行：程序按表达式算出的值(参考，只读)
        self.R_EXP = self._ti_G + 1       # 期望 行：designer 手填；未填→生成 .sv 时用 auto_out 兜底
        return self._ti_G + 2     # 总行数 = 输入数 + 2(auto_out + 期望)

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
        """该输入组的 Excel 来源坐标(可能多个，如同一物理信号占 A、B 两个字母)。
        普通信号 = 表达式字母(A/B/C…)；cone 展开信号 = 叶子来源("上游行名.字母"，如 pll_n1.A)。"""
        return ",".join(g.get("xl_letters") or g.get("letters") or [])

    @classmethod
    def _vheader_label(cls, g):
        """完整行表头：'A,B → d_xxx[14:14]'（CSV 导出用，保持自描述）。"""
        ltr = cls._group_letters(g)
        base_lbl = g.get("label", g["base"])
        return "%s → %s" % (ltr, base_lbl) if ltr else base_lbl

    @classmethod
    def _vheader_short(cls, g):
        """真值表行表头(GUI 用)：信号名(带位宽)+控制标记（2026-06-03 用户拍板：直接用信号名，不用字母）。
        字母→信号 的对照仍在上方『输入信号』表与 tooltip 里（对照表达式 A/B/C 时用）。"""
        label = g.get("label", g["base"])
        return "%s (控制)" % label if g.get("is_control") else label

    @staticmethod
    def _binding_meta(b):
        """绑定 → (类型, 驱动机制) 文本：RO→force <net>；RW→RF_WRITE 0x<地址> bit<<<lsb>；
        未解析→标红提示。logic 的『输入信号』表与 mux 的（_populate_mux_inputs）共用。"""
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

    def _input_meta(self, g):
        """该输入组的 (类型, 驱动机制) 文本，供『输入信号』表的『类型』『驱动』列。"""
        b = self._ti_bindings.get(g["rep"]) if self._ti_bindings else None
        return self._binding_meta(b)

    def _populate_mux_inputs(self, grp, exp):
        """mux 信号的『输入信号』表（修"mux 点开输入信号框空白"，2026-06-03 第十一轮；
        第十四轮按控制三来源/数据三来源重排——WL 多控制·寄存器直出·mux 级联）。

        行 = 各控制信号的驱动输入（按 exp['ctrl_drivers'] 三来源细分角色）+ 数据寄存器（d:*）：
          『字母』列：寄存器直出控制显示 Excel 控制列字母(B/C/D/E)；logic 控制显示其表达式字母；
                      mux 级联控制显示"经 mux<N>"；数据寄存器显示它对应的 case 值
          『角色』列：寄存器直出=控制(寄存器直出)；logic=line路径/local路径/模式位(LPBT 不变)；
                      mux 级联=控制(经上游mux驱动)，并把上游载体/上游控制各加一行(上游mux配方)；
                      数据寄存器标"被哪个 case 选中"，RO 线控数据标"数据(线控,force)"
        """
        if not hasattr(self, "ti_inputs"):
            return
        tbl = self.ti_inputs
        rows = []       # 每行 = {letter, label, role, kind, drive, bold}
        # ── 控制信号：按 ctrl_drivers 三来源渲染 ──
        for drv in exp.get("ctrl_drivers", []):
            rows.extend(self._mux_ctrl_rows(drv, exp))
        # ── 数据寄存器：与 grp.cases 一一对应 ──
        for di, key in enumerate(exp.get("data_keys", [])):
            b = exp["bindings"].get(key)
            if b is None:
                continue
            kind, drive = self._binding_meta(b)
            case_raw = grp.cases[di].case_raw if di < len(grp.cases) else "?"
            # RO 线控数据走 force（线控寄存器），其余是被该 case 选中的本地/lut 寄存器
            if (b.kind or "") == "RO":
                role = "数据(线控,force)"
            else:
                role = "数据寄存器(被该case选中)"
            rows.append({"letter": "case %s" % case_raw, "label": self._mux_label(b),
                         "role": role, "kind": kind, "drive": drive, "bold": False})
        tbl.setRowCount(len(rows))
        for i, rd in enumerate(rows):
            for c, v in enumerate([rd["letter"], rd["label"], rd["role"],
                                   rd["kind"], rd["drive"]]):
                it = QtWidgets.QTableWidgetItem(v)
                if rd.get("bold") and c == 0:         # 控制输入字母加粗（与 logic 行为呼应）
                    f = it.font(); f.setBold(True); it.setFont(f)
                if c == 4 and "未解析" in v:
                    it.setForeground(QtGui.QColor("red"))
                tbl.setItem(i, c, it)
        tbl.resizeColumnsToContents()
        self._fit_inputs_height()

    @staticmethod
    def _mux_label(b):
        """绑定 → 『信号(位宽)』列文本。"""
        return b.base + ("[%d:0]" % (b.width - 1) if b.width > 1 else "")

    def _mux_ctrl_rows(self, drv, exp):
        """一个控制信号的驱动器 → 『输入信号』表的若干行（按三来源给角色文案）。"""
        out = []
        src = drv.get("source")
        if src == "logic":
            # LPBT 形态：保持 line路径/local路径/模式位 三分角色（文案与历史一致）
            line_key = drv["line"]["key"] if drv.get("line") else None
            local_key = drv["local"]["key"] if drv.get("local") else None
            for key in drv.get("keys", []):
                b = exp["bindings"].get(key) or drv["bindings"].get(key)
                kind, drive = self._binding_meta(b)
                if key == line_key:
                    role = "控制·line路径(force线控)"
                elif key == local_key:
                    role = "控制·local路径(本地寄存器)"
                else:
                    role = "控制·模式位/门控"
                out.append({"letter": key.split(":")[-1], "label": self._mux_label(b),
                            "role": role, "kind": kind, "drive": drive, "bold": True})
            return out
        if src in ("reg", "mux-force"):
            key = drv.get("key")
            b = exp["bindings"].get(key) or drv["bindings"].get(key)
            kind, drive = self._binding_meta(b)
            role = ("控制(寄存器直出)" if src == "reg"
                    else "控制(force上游mux衔接网)")
            out.append({"letter": drv.get("letter") or "?", "label": self._mux_label(b),
                        "role": role, "kind": kind, "drive": drive, "bold": True})
            return out
        if src == "mux":
            # mux 级联控制：本控制行 + 上游配方（载体寄存器 + 上游各控制）
            upstream = drv.get("upstream")
            up_no = getattr(upstream, "group_no", "?")
            out.append({"letter": drv.get("letter") or "?",
                        "label": drv.get("base") or "?",
                        "role": "控制(经上游mux%s驱动)" % up_no,
                        "kind": "mux", "drive": "经上游 mux%s 输出选路" % up_no, "bold": True})
            recipe = drv.get("recipe") or {}
            carrier_key = recipe.get("carrier_key")
            if carrier_key is not None:
                b = exp["bindings"].get(carrier_key) or recipe.get("bindings", {}).get(carrier_key)
                if b is not None:
                    kind, drive = self._binding_meta(b)
                    out.append({"letter": "经 mux%s" % up_no, "label": self._mux_label(b),
                                "role": "上游mux配方(载体寄存器写目标值)",
                                "kind": kind, "drive": drive, "bold": False})
            for ud in recipe.get("ctrl_drivers", []):
                for key in ud.get("keys", []):
                    b = exp["bindings"].get(key) or ud["bindings"].get(key)
                    if b is None:
                        continue
                    kind, drive = self._binding_meta(b)
                    out.append({"letter": "经 mux%s" % up_no, "label": self._mux_label(b),
                                "role": "上游mux配方(上游控制驱到载体case)",
                                "kind": kind, "drive": drive, "bold": False})
            return out
        # unknown：来源没解析出来——照样列出，角色写明"无法驱动"
        key = drv.get("key")
        b = (exp["bindings"].get(key) or drv.get("bindings", {}).get(key)) if key else None
        kind, drive = self._binding_meta(b)
        out.append({"letter": drv.get("letter") or "?", "label": drv.get("base") or "?",
                    "role": "控制(来源未知,无法驱动)", "kind": kind, "drive": drive, "bold": True})
        return out

    def _populate_chain(self):
        """『展开链』面板：cone 信号显示展开过程，非 cone 信号隐藏(不占空间)。

        每个链节两行：『① 行名 = Excel 原式』+『(对齐) = 字母代入真实信号名的等价形式』。
        链整体 = 展开后的等价表达式(分行摆，不合并成一行——深层 cone 嵌套爆炸读不了)。"""
        if not hasattr(self, "ti_chain"):
            return
        chain = self._ti_chain or []
        if len(chain) < 2:                    # 非 cone(空) / 异常的单行链 → 隐藏
            self.ti_chain_cap.hide(); self.ti_chain.hide()
            return
        marks = "①②③④⑤⑥⑦⑧⑨"
        lines = []
        for i, c in enumerate(chain):
            mark = marks[i] if i < len(marks) else "(%d)" % (i + 1)
            head = "%s %s" % (mark, c["out"])
            lines.append("%s = %s" % (head, c["expr"]))
            lines.append("%s = %s" % (" " * len(head), c["subst"]))
        self.ti_chain.setPlainText("\n".join(lines))
        # 高度贴内容：每链节 2 行，最多显示约 8 行(4 层)，更深内部滚动
        fm = self.ti_chain.fontMetrics()
        shown = min(len(lines), 8)
        self.ti_chain.setFixedHeight(shown * fm.lineSpacing() + 14)
        self.ti_chain_cap.show(); self.ti_chain.show()

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
        self._ti_hl_col = -1          # 列集变了，旧高亮作废(下次选中再亮)
        # 重建前记下现有列宽：用户手动拖过的宽度在重建后恢复（修"列宽拖了又弹回去"）
        old_widths = [self.ti_table.columnWidth(c) for c in range(self.ti_table.columnCount())]
        self._ti_loading = True
        try:
            self.ti_table.clear()
            nrows = self._ti_dims()
            ntests = len(self._ti_rows)
            self.ti_table.setRowCount(nrows)
            self.ti_table.setColumnCount(ntests)
            out_w = self._ti_sig.out_width if self._ti_sig else 1
            wsuf = "[%d:0]" % (out_w - 1) if out_w and out_w > 1 else ""
            auto_label = "auto_out%s" % wsuf
            exp_label = "期望(进.sv)%s" % wsuf
            # 行表头=信号名(带位宽，控制位带标记+加粗)；字母↔信号对照在 tooltip 与上方『输入信号』表
            vlabels = [self._vheader_short(g) for g in self._ti_groups] + [auto_label, exp_label]
            self.ti_table.setVerticalHeaderLabels(vlabels)
            for i, g in enumerate(self._ti_groups):
                hi = self.ti_table.verticalHeaderItem(i)
                if hi:
                    hi.setToolTip("%s  =  表达式里的 %s\n(%s, %dbit%s)"
                                  % (g["label"], self._group_letters(g) or "?", g["kind"], g["width"],
                                     ", 控制位/选择位" if g["is_control"] else ", 数据位"))
                    if g["is_control"]:          # 控制/选择位加粗，提示"看这几行的 0/1 组合"
                        f = hi.font(); f.setBold(True); hi.setFont(f)
            out_name = self._ti_sig.out_name if self._ti_sig else "out"
            # auto_out 行表头：程序按表达式算出的值(参考，只读)
            hauto = self.ti_table.verticalHeaderItem(self.R_AUTO)
            if hauto:
                hauto.setToolTip("auto_out = 程序按表达式算出的 %s 输出值（只读，参考用）。\n"
                                 "⚠ 它来自表达式本身——用它当期望去验证表达式有自证嫌疑，\n"
                                 "所以 .sv 断言对比的是下面 designer 手填的「期望」。" % out_name)
                fa = hauto.font(); fa.setItalic(True); hauto.setFont(fa)
            # 期望行表头：designer 手填，.sv 断言用它
            hexp = self.ti_table.verticalHeaderItem(self.R_EXP)
            if hexp:
                hexp.setToolTip("期望 = designer 自己手填的 %s 输出值，.sv 断言用它对比。\n"
                                "· 未填(空白) → 生成 .sv 时用上面的 auto_out 兜底\n"
                                "· 已填且 == auto_out → 绿色(designer 审过且与表达式一致)\n"
                                "· 已填但 != auto_out → 红色(表达式可能与你的意图不符——仿真 FAIL 正是要抓的)\n"
                                "· 负向列 → 故意填错值(琥珀色)，与 designer 期望是两回事" % out_name)
                fe = hexp.font(); fe.setBold(True); hexp.setFont(fe)
            self.ti_table.setHorizontalHeaderLabels(["T%d" % i for i in range(ntests)])
        finally:
            self._ti_loading = False
        for c in range(len(self._ti_rows)):
            self._ti_render_col(c)
        self._ti_fit_columns(old_widths)
        self._update_cov_hint()

    # ───────────── 真值表列宽（可拖动 + 手动宽度不被重建冲掉） ─────────────
    def _on_ti_section_resized(self, *args):
        """用户手动拖列宽 → 记住"手动调过"；之后重建表格保留手动宽度(换信号才恢复自动)。"""
        if not self._ti_loading and not self._ti_auto_resizing:
            self._ti_user_widths = True

    def _ti_fit_columns(self, old_widths=None):
        """列宽策略：用户手动调过 → 沿用重建前的宽度(新列按内容)；否则全部按内容自适应。"""
        self._ti_auto_resizing = True
        try:
            n = self.ti_table.columnCount()
            if self._ti_user_widths and old_widths:
                for c in range(min(len(old_widths), n)):
                    self.ti_table.setColumnWidth(c, old_widths[c])
                for c in range(len(old_widths), n):          # 新增的列没有旧宽度 → 按内容
                    self.ti_table.resizeColumnToContents(c)
            else:
                self.ti_table.resizeColumnsToContents()
        finally:
            self._ti_auto_resizing = False

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
                val = rd["base_values"].get(g["key"], 0)
                it = self._mk_item(self._cell_text(val, g["width"]), True)
                it.setTextAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)   # 右对齐→低位对齐，列间差异一眼看出
                self.ti_table.setItem(i, c, it)
            drv_tip = ("\nforce: %s" % fs if fs else "") + ("\nRF_WRITE: %s" % ws if ws else "")
            # ── auto_out 行（只读）：程序按表达式算出的值 ──
            autoit = self._mk_item(self._cell_text(rd["correct"] & E.mask(w), w), False)
            autoit.setTextAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
            fa = autoit.font(); fa.setItalic(True); autoit.setFont(fa)
            autoit.setForeground(QtGui.QColor("#555555"))
            autoit.setToolTip("auto_out(表达式计算值) bin: %s\n只读参考；.sv 断言对比的是下面的「期望」%s"
                              % (W.fmt_bin(rd["correct"], w), drv_tip))
            self.ti_table.setItem(self.R_AUTO, c, autoit)
            # ── 期望 行（可编辑）：负向=错值；正向=designer 手填(未填→空白，生成时 auto_out 兜底) ──
            de = rd.get("designer_expected")
            if neg:
                exp_text = self._cell_text(rd["expected"] & E.mask(w), w)
            else:
                exp_text = "" if de is None else self._cell_text(de & E.mask(w), w)
            expit = self._mk_item(exp_text, True)
            expit.setTextAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
            if neg:
                tip = ("负向：故意填错的期望 = %s(预期断言 FAIL，自检 checker)。\n"
                       "正确(auto_out)应为 %s；可双击改这个错值。"
                       % (W.fmt_bin(rd["expected"], w), self._cell_text(rd["correct"], w)))
            elif de is None:
                tip = ("未手填——生成 .sv 时用 auto_out=%s 兜底。\n"
                       "双击填入你认为的输出值(不看上一行自己算，才能验出表达式的错)。"
                       % self._cell_text(rd["correct"], w))
            elif de == rd["correct"]:
                tip = ("designer 手填期望 = %s，与 auto_out 一致 ✓\n.sv 断言用此值；清空单元格可恢复未填(兜底)。"
                       % W.fmt_bin(de, w))
            else:
                tip = ("⚠ designer 手填期望 = %s，但表达式算出 auto_out = %s！\n"
                       "若你确认期望没填错，则表达式与你的意图不符(仿真该测试会 FAIL——这正是 Dreg 要抓的 bug)。\n"
                       ".sv 断言用你手填的期望；清空单元格可恢复未填(兜底)。"
                       % (W.fmt_bin(de, w), W.fmt_bin(rd["correct"], w)))
            expit.setToolTip(tip + drv_tip)
            if not neg:
                ff = expit.font(); ff.setBold(de is not None); expit.setFont(ff)
                if de is None:
                    expit.setForeground(FB_FG)
            self.ti_table.setItem(self.R_EXP, c, expit)
            # 列头：自定义名/T<n>(负向带 _NEG，红字)；tooltip 提示可否改名 + 驱动
            hh = self.ti_table.horizontalHeaderItem(c)
            if hh:
                hh.setText(self._ti_label(rd, c))
                rename_hint = "双击列头可改名" if rd.get("user_added") else "自动生成，名字不可改"
                exp_state = ("负向(故意填错)" if neg else
                             ("期望已手填" if de is not None else "期望未填(auto_out兜底)"))
                hh.setToolTip("%s · %s · %s\nforce: %s\nRF_WRITE: %s"
                              % ("负向(故意填错)" if neg else "正向(真实)", exp_state, rename_hint,
                                 fs or "(无)", ws or "(无)"))
                hh.setForeground(NEG_FG if neg else QtGui.QColor("black"))   # 负向=琥珀，不与"状态红=坏掉"撞色
            # 列底色：负向=琥珀；当前选中列=淡蓝高亮；两者叠加=更深琥珀。其余列不设(留给隔行底色)。
            hl = (c == self._ti_hl_col)
            if neg or hl:
                bg = HL_NEG_BG if (neg and hl) else (NEG_BG if neg else HL_BG)
                for r in range(self.ti_table.rowCount()):
                    cell = self.ti_table.item(r, c)
                    if cell is not None:
                        cell.setBackground(bg)
            # 期望格状态色(正向)：已填且==auto_out → 绿；已填但!=auto_out → 红(可能是表达式 bug)。
            # 盖在列底色之上(状态信息优先级最高)；未填不上色(空白+灰字提示已足够)。
            if not neg and de is not None:
                expit.setBackground(DSGN_BG if de == rd["correct"] else DIFF_BG)
        finally:
            self._ti_loading = False

    def _ti_on_current_col(self, row, col, prow, pcol):
        """选中单元格变列 → 移动列高亮(只重绘旧/新两列，便宜)。行表头是冻结纵表头，横滚自然不丢。"""
        if self._ti_loading or col == self._ti_hl_col:
            return
        old = self._ti_hl_col
        self._ti_hl_col = col
        if 0 <= old < len(self._ti_rows):
            self._ti_render_col(old)        # 旧列：清掉高亮(此时 old != _ti_hl_col)
        if 0 <= col < len(self._ti_rows):
            self._ti_render_col(col)        # 新列：加上高亮

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
        if self._ti_loading:
            return
        if not self._ti_sig:
            # mux 信号：只有「期望」行可编辑（designer 手填期望，按输入取值键存储）
            self._on_mux_exp_changed(item)
            return
        r, c = item.row(), item.column()       # 列 c = 第 c 条测试；行 r = 输入/期望/负向
        if c < 0 or c >= len(self._ti_rows):
            return
        rd = self._ti_rows[c]
        try:
            if 0 <= r < len(self._ti_groups):
                g = self._ti_groups[r]
                val = self._parse_int(item.text()) & E.mask(g["width"])
                rd["base_values"][g["key"]] = val
                self._ti_recompute(rd)        # 正向→auto_out 自动重算(手填期望保持不动)；负向→错值随之重算
            elif r == self.R_EXP:
                w = E.mask(rd.get("correct_width") or 1)
                if rd.get("kind") == "neg":
                    # 负向：改的是"故意填错"的错值
                    rd["wrong_value"] = self._parse_int(item.text()) & w
                else:
                    # 正向：designer 手填期望。清空 = 恢复未填(生成 .sv 时 auto_out 兜底)
                    txt = item.text().strip()
                    rd["designer_expected"] = None if txt == "" else (self._parse_int(txt) & w)
                self._ti_recompute(rd)
            else:
                return        # auto_out 行只读(无 editable flag，正常不会进来)，防御
        except ValueError as ex:
            self.status.showMessage("数值解析失败: %s（已还原）" % ex)
        self._ti_render_col(c)
        self._ti_mark_customized()

    def _on_mux_exp_changed(self, item):
        """mux 信号「期望」行编辑：手填期望按输入取值键存进 _mux_expected（持久化 + 生成时生效）。"""
        grp = getattr(self, "_ti_mux_sig", None)
        vecs = self._ti_mux_vecs
        r, c = item.row(), item.column()
        if grp is None or not vecs or r != self._ti_mux_exp_row or not (0 <= c < len(vecs)):
            return
        vec = vecs[c]
        if vec.is_negative:
            return                          # 防御：编辑器里的 mux 向量都是正向(负向生成时才追加)
        name_low = grp.out_name.lower()
        key = generator.mux_assign_key(vec.assignments)
        try:
            txt = item.text().strip()
            if txt == "":
                # 清空 = 恢复未填(生成 .sv 时 auto_out 兜底)
                self._mux_expected.get(name_low, {}).pop(key, None)
                if not self._mux_expected.get(name_low):
                    self._mux_expected.pop(name_low, None)
                vec.designer_expected = None
            else:
                val = self._parse_int(txt) & E.mask(vec.exp_width)
                self._mux_expected.setdefault(name_low, {})[key] = val
                vec.designer_expected = val
        except ValueError as ex:
            self.status.showMessage("数值解析失败: %s（已还原）" % ex)
        self._persist_edits()               # mux 期望也是劳动成果，即时存盘
        # 单列重绘 + 头部进度/工具栏条数提示刷新
        n_inputs = self._ti_mux_exp_row - 1
        self._ti_loading = True
        try:
            self._render_mux_exp_col(c, n_inputs)
        finally:
            self._ti_loading = False
        mode, exhaustive = self._coverage()
        self._update_mux_header(grp, vecs, mux_gen.coverage_mode(mode, exhaustive))
        self._update_cov_hint()

    def on_ti_add(self):
        if not self._ti_sig:
            QtWidgets.QMessageBox.information(self, "提示", "请先在左侧选择一个信号")
            return
        rd = {"base_values": {g["key"]: 0 for g in self._ti_groups},
              "kind": "pos", "note": "", "user_added": True}   # 用户新增正向列(可改名)
        self._ti_recompute(rd)
        self._ti_rows.append(rd)
        self._ti_mark_customized()
        self._ti_populate()
        self.ti_table.setCurrentCell(0, len(self._ti_rows) - 1)

    def on_ti_fill_expected(self):
        """「auto→期望」：把 auto_out 填进期望行（只填未填的列；已手填/负向的不动）。

        这是用户要求的便捷功能——designer 核对过表达式后可一键采信 auto_out。
        填进去之后就算"已手填"(designer_filled=True，报告里区别于 auto_out 兜底)。
        logic 与 mux 信号都支持（mux 走 _mux_expected 按取值键存储）。"""
        if not self._ti_sig:
            if self._fill_mux_expected():    # mux 信号：填 _mux_expected
                return
            QtWidgets.QMessageBox.information(self, "提示", "请先在左侧选择一个信号")
            return
        targets = [rd for rd in self._ti_rows
                   if rd.get("kind") != "neg" and rd.get("designer_expected") is None]
        if not targets:
            self.status.showMessage("没有可填的列：期望都已手填(或全是负向列)")
            return
        if not self._confirm_fill_expected(len(targets)):
            return
        for rd in targets:
            rd["designer_expected"] = rd.get("correct", 0)
            self._ti_recompute(rd)
        self._ti_mark_customized()
        self._ti_populate()
        self.status.showMessage("已把 auto_out 填入 %s 的 %d 列「期望」(已手填的未动)"
                                % (self._ti_sig.out_name, len(targets)))

    def _confirm_fill_expected(self, n):
        """「auto→期望」确认框（logic/mux 共用）。"""
        return QtWidgets.QMessageBox.question(
            self, "auto_out → 期望",
            "把 auto_out(程序计算值) 填进 %d 列未填的「期望」？\n\n"
            "⚠ 注意：这等于直接采信程序算出的值——失去了 designer 独立核对的意义。\n"
            "更稳妥的做法是自己算一遍再填(或用 HTML 报告的『真值表检查』页自测)。\n"
            "确认无误时再用这个快捷方式。" % n,
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
            QtWidgets.QMessageBox.No) == QtWidgets.QMessageBox.Yes

    def _fill_mux_expected(self):
        """mux 信号的「auto→期望」：未填的列按取值键写进 _mux_expected。返回是否处理（是 mux 信号）。"""
        grp = getattr(self, "_ti_mux_sig", None)
        vecs = self._ti_mux_vecs
        if grp is None or not vecs:
            return False
        targets = [v for v in vecs if not v.is_negative and v.designer_expected is None]
        if not targets:
            self.status.showMessage("没有可填的列：期望都已手填")
            return True
        if not self._confirm_fill_expected(len(targets)):
            return True
        name_low = grp.out_name.lower()
        for v in targets:
            val = v.exp_value & E.mask(v.exp_width)
            self._mux_expected.setdefault(name_low, {})[generator.mux_assign_key(v.assignments)] = val
            v.designer_expected = val
        self._persist_edits()
        self._load_mux_test_items(grp)       # 整表重绘（颜色/头部进度）
        self.status.showMessage("已把 auto_out 填入 %s 的 %d 列「期望」(已手填的未动)"
                                % (grp.out_name, len(targets)))
        return True

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
        if src.get("designer_expected") is not None:        # designer 手填期望随复制带走
            rd["designer_expected"] = src["designer_expected"]
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
        self._persist_edits()                  # 丢弃自定义也要同步到盘(否则下次启动又恢复回来)
        self._load_test_items(self._ti_sig)
        self.status.showMessage("已从表达式重新生成 %s 的测试项（丢弃自定义）" % self._ti_sig.out_name
                                if self._ti_sig else "")

    def on_ti_preview_signal(self, switch_tab=True):
        if not self._ti_sig:
            # mux 信号：走完整 build 路径预览（含手填期望/负向勾选，所见即所得）
            grp = getattr(self, "_ti_mux_sig", None)
            if grp is not None and self.wb:
                res = generator.build(self.wb, self._opts([grp.out_name]))
                self.preview.setPlainText(generator.render(res, comments=True))
                self._preview_source = "signal"
                if switch_tab:
                    self.tabs.setCurrentWidget(self.preview_tab)
                self.status.showMessage("已预览 mux 信号 %s 的 .sv 片段" % grp.out_name)
                return
            QtWidgets.QMessageBox.information(self, "提示", "请先在左侧选择一个信号")
            return
        sig = self._ti_sig
        vecs = self._rows_to_vectors(self._ti_node, self._ti_bindings, self._ti_groups,
                                     sig.out_width, self._ti_rows)
        # node/probe_prefix 必须传：cone 信号的驱动用叶子变量名；前缀信号探针带层级路径。
        # owner_in_msg 跟随导出设置(预览=导出，所见即所得)；counters 不传——单信号片段
        # 没有文件级 begin/end 包裹，计数器++会显示成未声明变量，徒增困惑。
        lines, _stats = W.render_signal_block(sig, self._ti_bindings, vecs,
                                              {"truncated": False}, comments=True,
                                              node=self._ti_node,
                                              probe_prefix=self._prefix_of(sig),
                                              owner_in_msg=bool(_load_settings().get(
                                                  "export_owner_in_msg", True)))
        self.preview.setPlainText("\n".join(lines))
        self._preview_source = "signal"
        if switch_tab:
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
                    bk = g["key"]
                    wr.writerow([self._vheader_label(g)] + [self._fmt_val(rd["base_values"].get(bk, 0), g["width"])
                                                            for rd in rows])
                out_w = self._ti_sig.out_width or 1
                wsuf = "[%d:0]" % (out_w - 1) if out_w > 1 else ""
                # auto_out(表达式计算) 与 期望(进 .sv 的对比值) 分两行——与编辑器/HTML 报告一致
                wr.writerow(["auto_out%s" % wsuf] + [self._fmt_val(rd["correct"] & E.mask(rd.get("correct_width") or 1),
                                                                   rd.get("correct_width") or 1) for rd in rows])
                wr.writerow(["期望(进.sv)%s" % wsuf] + [self._fmt_val(rd["expected"] & E.mask(rd.get("correct_width") or 1),
                                                                     rd.get("correct_width") or 1) for rd in rows])
                wr.writerow(["期望(bin)"] + [W.fmt_bin(rd["expected"], rd.get("correct_width") or 1)
                                            for rd in rows])
                wr.writerow(["期望来源"] + [("负向(故意填错)" if rd["is_negative"] else
                                            ("designer手填" if rd.get("designer_expected") is not None
                                             else "auto_out兜底")) for rd in rows])
                wr.writerow(["负向?"] + ["是" if rd["is_negative"] else "" for rd in rows])
                drives = [self._drive_strs(rd) for rd in rows]
                wr.writerow(["force"] + [fs for fs, _ in drives])
                wr.writerow(["RF_WRITE"] + [ws for _, ws in drives])
        except OSError as ex:
            QtWidgets.QMessageBox.critical(self, "导出失败", str(ex))
            return
        QtWidgets.QMessageBox.information(self, "完成", "已导出 %d 条测试项：\n%s"
                                          % (len(self._ti_rows), path))
        self.status.showMessage("已导出测试项 CSV：%s" % path)

    def _rows_to_vectors(self, node, bindings, groups, out_width, rows):
        """把 rowdict 列表构造成 TestVector 列表（负向行用 expected 作 override 编码；
        正向行带 designer 手填期望(未填=None→生成时 auto_out 兜底)；带自定义名）。"""
        vecs = []
        for i, rd in enumerate(rows):
            neg = rd.get("is_negative")
            exp_override = rd["expected"] if neg else None
            vecs.append(V.make_vector_from_base_values(
                node, bindings, groups, rd["base_values"], out_width,
                index=i, expected_override=exp_override, name=rd.get("name"),
                designer_expected=None if neg else rd.get("designer_expected")))
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
            node, bindings, groups, _chain, _err = self._expand_sig(sig)
            if node is None:
                continue
            vecs = self._rows_to_vectors(node, bindings, groups, sig.out_width, rows)
            if positive_only:
                vecs = [v for v in vecs if not v.is_negative]
            elif negative_only:
                vecs = [v for v in vecs if v.is_negative]
                if not vecs:
                    continue                 # 该信号无负向 → "仅负向"导出里整个略过
            ov[name_low] = vecs          # 空列表也保留：删空=零用例，不回退自动
        return ov or None

    def _opts(self, signals, neg_signals=None, positive_only=False, negative_only=False,
              owner_in_msg=None, sv_summary=None):
        # 注意：GUI 的负向统一走 vector_overrides(左侧"负向"列与右侧编辑器是同一套)，
        # 故这里默认不传 neg_signals，避免与 override 里的负向重复追加。
        # 例外：mux 信号的负向走 neg_signals（mux 不经 override——其期望由 case 结构决定）。
        mux_neg = self._mux_neg_checked()
        if mux_neg:
            neg_signals = list(neg_signals or []) + mux_neg
        mode, exhaustive = self._coverage()
        # owner/汇总选项：on_generate 直接传对话框的返回值(当次导出以对话框为准，
        # 不依赖"先写盘再读回"的往返——写盘失败/测试环境下会静默丢失)；
        # 预览等其它路径不传 → 从持久化设置读上次的选择(预览=导出，所见即所得)。
        st = _load_settings()
        if owner_in_msg is None:
            owner_in_msg = bool(st.get("export_owner_in_msg", True))
        if sv_summary is None:
            sv_summary = bool(st.get("export_sv_summary", True))
        return generator.GenOptions(
            signals=signals or None, neg_signals=neg_signals or None,
            mode=mode, max_tests=self.max_tests.value(), exhaustive=exhaustive,
            top_output_only=False,   # GUI 已按表勾选，不再二次过滤
            probe_prefixes=dict(self._probe_prefixes),
            owner_in_msg=owner_in_msg,
            sv_summary=sv_summary,
            cascade_mode=self._cascade_mode(),
            vector_overrides=self._vector_overrides(positive_only=positive_only,
                                                    negative_only=negative_only),
            # mux 手填期望：所有导出范围都传——负向的错值防撞要看到它，
            # 保证"全部"与"仅负向"两份导出的负向错值一致(便于对照)
            mux_expected={k: dict(v) for k, v in self._mux_expected.items()})

    # ───────────── 收集 / 选项 ─────────────
    def _collect(self):
        """返回勾选(COL_SEL)的信号名列表。负向不再单独收集——已在 vector_overrides 里。"""
        sel = []
        for r in range(self.table.rowCount()):
            if self.table.item(r, COL_SEL).checkState() == QtCore.Qt.Checked:
                sel.append(self._sig_of_row(r).out_name)
        return sel

    def _mux_neg_checked(self):
        """勾了"负向"列的 mux 信号名（mux 负向经 neg_signals 在生成时追加，不走 override）。"""
        out = []
        for r in range(self.table.rowCount()):
            neg_it = self.table.item(r, COL_NEG)
            if neg_it is None or neg_it.checkState() != QtCore.Qt.Checked:
                continue
            sig = self._sig_of_row(r)
            if isinstance(sig, excel_model.MuxGroup):
                out.append(sig.out_name)
        return out

    def _negative_signal_names(self, sel):
        """从勾选信号 sel 里挑出"含负向测试"的原始信号名(用于'仅负向'导出)。"""
        ovneg = self._vector_overrides(negative_only=True) or {}
        low2name = {s.lower(): s for s in sel}
        return [low2name[k] for k in ovneg if k in low2name]

    # ───────────── 预览 / 生成 ─────────────
    def on_preview(self, switch_tab=True):
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
        self._preview_source = "all"
        if switch_tab:
            self.tabs.setCurrentWidget(self.preview_tab)
        s = res["summary"]
        msg = "预览: 生成 %d，向量 %d（负向 %d）" % (s["n_generated"], s["n_vectors"], s["n_negative"])
        n_pos = s["n_vectors"] - s["n_negative"]
        if n_pos:
            msg += "；期望: 手填 %d / auto_out兜底 %d" % (s.get("n_designer", 0),
                                                          n_pos - s.get("n_designer", 0))
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
        """生成 .sv 前的导出选项：内容范围(全部/仅正向/仅负向) + 注释 + owner + 末尾汇总。
        记住上次选择(下次预选)。返回 {"scope","comments","owner_in_msg","sv_summary"} 或 None。"""
        st = _load_settings()
        last_scope, last_cm = st.get("export_scope", "all"), bool(st.get("export_comments", False))
        last_owner = bool(st.get("export_owner_in_msg", True))
        last_summary = bool(st.get("export_sv_summary", True))
        dlg = QtWidgets.QDialog(self)
        dlg.setWindowTitle(title)
        lay = QtWidgets.QVBoxLayout(dlg)
        lay.addWidget(QtWidgets.QLabel("导出内容范围："))
        scope_combo = QtWidgets.QComboBox()
        scope_combo.addItem("全部（正向 + 负向）", "all")
        scope_combo.addItem("仅正向（正确用例）", "pos")
        scope_combo.addItem("仅负向（故意填错）", "neg")
        scope_combo.setToolTip("仅负向：只导出你标了'负向'的故意填错用例。\n"
                               "反例断言语法与正例完全一样(==)，期望值故意填错 → 仿真时必然 FAIL\n"
                               "→ uvm_report_error 正常触发(预期内的报错，消息带 NEG-EXPECTED-FAIL 标签)，\n"
                               "用来自检你的验证系统真的能看见报错")
        si = scope_combo.findData(last_scope)
        if si >= 0:
            scope_combo.setCurrentIndex(si)
        lay.addWidget(scope_combo)
        cm_chk = QtWidgets.QCheckBox("加注释（在 .sv 里标注每条用例/负向说明）")
        cm_chk.setChecked(last_cm)
        lay.addWidget(cm_chk)
        owner_chk = QtWidgets.QCheckBox("断言消息带 owner（log 里直接看出是谁的信号）")
        owner_chk.setToolTip("在 uvm_report 消息尾部追加 ', owner:<logic P列>'。\n"
                             "消息前半段格式不变，已有的 log 解析脚本不受影响。产物纯英文。")
        owner_chk.setChecked(last_owner)
        lay.addWidget(owner_chk)
        sum_chk = QtWidgets.QCheckBox("末尾测试汇总（断言总数/反例数/真 FAIL 数）")
        sum_chk.setToolTip("仿真 log 最后一行直接给出：信号数、断言总数、正/反例数、\n"
                           "运行时统计的真 FAIL 数(REAL FAIL)和没起作用的反例数(NEG broken)。\n"
                           "注意：反例的 error 是故意触发的 → log 的 UVM_ERROR 总数应 =\n"
                           "REAL FAIL + 反例数 - NEG broken。\n"
                           "实现上会把整个语句体包进一层命名 begin/end 块(声明计数变量)，\n"
                           "贴进任何 task/initial 体里都是合法 SV。")
        sum_chk.setChecked(last_summary)
        lay.addWidget(sum_chk)
        bb = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        bb.accepted.connect(dlg.accept); bb.rejected.connect(dlg.reject)
        lay.addWidget(bb)
        if dlg.exec() != QtWidgets.QDialog.Accepted:
            return None
        scope, comments = scope_combo.currentData(), cm_chk.isChecked()
        owner_in_msg, sv_summary = owner_chk.isChecked(), sum_chk.isChecked()
        st["export_scope"] = scope; st["export_comments"] = comments
        st["export_owner_in_msg"] = owner_in_msg; st["export_sv_summary"] = sv_summary
        _save_settings(st)               # 记住，下次预选
        return {"scope": scope, "comments": comments,
                "owner_in_msg": owner_in_msg, "sv_summary": sv_summary}

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
        # 当次导出以对话框返回值为准(而非磁盘配置往返)；预览路径仍读持久化设置
        kw = {"owner_in_msg": opt["owner_in_msg"], "sv_summary": opt["sv_summary"]}
        # 按内容范围构建：全部 / 仅正向(剔负向) / 仅负向(只含负向、无负向的信号略过)
        if scope == "neg":
            names = self._negative_signal_names(sel)
            if not names:
                QtWidgets.QMessageBox.information(
                    self, "提示", "勾选的信号里没有任何负向测试，无法只导出'错误用例'。\n"
                    "请先勾左侧'负向'列(1条)，或在右侧编辑器'加负向(选中)'/'全部用例加负向'。")
                return
            res = generator.build(self.wb, self._opts(names, negative_only=True, **kw))
            scope_msg = "（仅负向，共 %d 个含负向信号）" % len(names)
        elif scope == "pos":
            res = generator.build(self.wb, self._opts(sel, positive_only=True, **kw))
            scope_msg = "（仅正向）"
        else:
            res = generator.build(self.wb, self._opts(sel, **kw))
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
            # 汇总命名块后缀跟导出范围走：『仅正向』『仅负向』两份贴进同一作用域也不重名
            suffix = {"pos": "_pos", "neg": "_neg"}.get(scope, "")
            self._write(path, generator.render(res, comments=cm, block_suffix=suffix))
        except OSError as ex:
            QtWidgets.QMessageBox.critical(
                self, "写出失败",
                "无法写入 %s：\n%s\n\n(文件是否正被仿真器/编辑器占用？)" % (path, ex))
            return
        nsk = res["summary"].get("n_skipped", 0)
        skipped = res.get("skipped") or []
        skipmsg = ""
        if nsk:
            names = [name for name, _aid, _risky in skipped]
            shown = "\n".join("  ↷ %s" % n for n in names[:12])
            more = "\n  …等共 %d 个（点『Show Details』看全部及原因）" % nsk if nsk > 12 else ""
            skipmsg = ("\n\n跳过 %d 个含不可驱动输入的信号（保证产物能 elaborate）：\n%s%s"
                       % (nsk, shown, more))
        # 已自定义信号数按"真正写进本次产物"的 block 统计(避免把未勾选/被范围过滤的也算进来)
        n_cust = sum(1 for (_l, st) in res["blocks"]
                     if st.get("out_name", "").lower() in self._customized)
        custmsg = ("\n\n含 %d 个已自定义测试项的信号(编辑已写入产物)。" % n_cust
                   if n_cust else "")
        # 期望来源统计：designer 手填 vs auto_out 兜底（兜底=未经 designer 人工核对，有自证嫌疑）
        s = res["summary"]
        n_pos = s["n_vectors"] - s["n_negative"]
        n_dsgn = s.get("n_designer", 0)
        expmsg = ""
        if n_pos and scope != "neg":
            expmsg = "\n\n断言期望来源：designer 手填 %d 条，auto_out 兜底 %d 条。" % (n_dsgn, n_pos - n_dsgn)
            if n_pos - n_dsgn:
                expmsg += ("\n（兜底 = 期望未手填、直接用表达式计算值对比——有自证嫌疑；"
                           "\n  建议在右侧编辑器手填期望，或用 HTML 报告『真值表检查』页核对）")
        box = QtWidgets.QMessageBox(QtWidgets.QMessageBox.Information, "完成",
                                    "已写出：%s%s%s%s%s" % (path, scope_msg, expmsg, skipmsg, custmsg),
                                    QtWidgets.QMessageBox.Ok, self)
        if skipped:
            box.setDetailedText(_skipped_detail_text(skipped))
        box.exec()
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
        QtWidgets.QMessageBox.information(
            self, "完成", "已导出报告(%s信号，用例 %d 条，负向 %d 条)：\n%s"
            % (scope, n_tc, n_neg, "\n".join(written)))
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
