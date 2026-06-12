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


def _coerce_int_map(d, lower=False):
    """把 {key: value} 安全转成 {key: int}，跳过非法数值（不崩）。返回 (干净 dict, 跳过个数)。
    用于恢复/导入外部 edits.json 时——损坏/手改的 mux_expected/mux_data 不能让整个加载流程崩
    （_restore_edits 每次加载 Excel 都跑，审查 #7）。"""
    out, bad = {}, 0
    for k, v in (d or {}).items():
        try:
            out[str(k).lower() if lower else str(k)] = int(v)
        except (ValueError, TypeError):
            bad += 1
    return out, bad


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


def _serialize_mux_vecs(vecs):
    """mux 用户手编/复制/负向列(TestVector) → 可 JSON 化结构（第二十八轮）。
    存 assignments(取值) + auto_out/期望/负向错值 + case_index(路由 case) + 名字。"""
    out = []
    for v in vecs or []:
        out.append({
            "assignments": {str(k): int(x) for k, x in v.assignments.items()},
            "exp_value": int(v.exp_value), "exp_width": int(v.exp_width),
            "is_negative": bool(v.is_negative),
            "neg_value": None if v.neg_value is None else int(v.neg_value),
            "neg_mode": v.neg_mode,
            "name": v.name, "note": v.note or "",
            "designer_expected": None if v.designer_expected is None else int(v.designer_expected),
            "case_index": None if v.case_index is None else int(v.case_index),
        })
    return out


def _deserialize_mux_vecs(lst):
    """JSON 结构 → mux 用户列 TestVector 列表（损坏项跳过，不崩——每次加载 Excel 都跑）。"""
    out = []
    for d in (lst or []):
        if not isinstance(d, dict):
            continue
        try:
            assigns = {str(k): int(x) for k, x in (d.get("assignments") or {}).items()}
            v = V.TestVector(0, assigns, int(d.get("exp_value", 0)), int(d.get("exp_width", 1)),
                             is_negative=bool(d.get("is_negative")),
                             neg_value=None if d.get("neg_value") is None else int(d["neg_value"]),
                             neg_mode=d.get("neg_mode"),
                             note=d.get("note", "") or "", name=d.get("name"),
                             designer_expected=None if d.get("designer_expected") is None
                             else int(d["designer_expected"]),
                             case_index=None if d.get("case_index") is None else int(d["case_index"]))
        except (ValueError, TypeError):
            continue
        out.append(v)
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
            "如需强制生成：勾选工具栏「缺前缀强制生成」（或 CLI --include-risky）——\n"
            "裸名 force 交给仿真验证；仿真过=此设计不需前缀，CUVUNF 则跑 scan_rtl 配前缀。\n\n"
            + "\n\n".join(parts))


(COL_SEL, COL_NEG, COL_R, COL_K, COL_OWNER, COL_TYPE, COL_TOP, COL_STATUS,
 COL_PREFIX, COL_EXPR) = range(10)
HEADERS = ["选", "负向", "R", "输出名(K)", "owner", "type", "top", "状态", "探针前缀", "表达式"]
NO_OWNER = "（无 owner）"          # owner 下拉的特殊项：Excel owner 列留空(P/L/AE)的信号
STATUS_LABEL = {"clean": "clean", "wire-fallback": "⚠wire兜底",
                "unresolved": "✗未解析", "parse-err": "✗解析错",
                "spec-collision": "✗规格冲突·待designer核对",
                "needs-prefix": "⚠输入缺前缀·跳过", "bare-probe": "输出裸名·已生成",
                "false-green": "⚠字段太窄·假绿"}
STATUS_HELP = {"clean": "输入都解析到具体 net，可正常 force/RF_WRITE 驱动",
               "wire-fallback": "有输入回退成 wire 兜底；elaboration 可能在 ENV_RF 层找不到该 net",
               "unresolved": "有输入未解析到 net（ENV_RF 探不到，仿真会 CUVUNF）",
               "parse-err": "表达式或输入解析出错",
               "spec-collision": "【表数据·非工具能修】mux 页有两行控制选择值相同却选不同数据源——"
                                 "同一选择值 RTL 物理上只能输出一个，已整组跳过。两种成因都可能："
                                 "①真规格矛盾→改数据源；②两个 mux 撞了同一输出名(『一个控制管多个 mux』本身合法，"
                                 "designer 多半复制粘贴漏改名)→改输出名。源名孪生时明细会优先提示成因②。"
                                 "tooltip/明细里有撞车的 Excel 行号、两个源、owner，请对应 designer 核对改表。",
               "needs-prefix": "【输入侧·硬阻断】要 force 的某根输入网埋在子模块里（级联 _to_mux 衔接网 / "
                               "wire 兜底），force 基名钉不住——没配前缀就 force 必 CUVUNF，所以默认【跳过】"
                               "整组。先跑 scan_rtl 配好探针前缀，这组才会生成。",
               "bare-probe": "【输出侧·软提示，已生成】输出 top_out=0（喂内部、非芯片顶层输出），"
                             "工具照样用裸名探针 `ENV_RF.<输出名> 探、【照常生成】.sv。只有仿真 elaboration "
                             "真报 CUVUNF（说明它埋在子模块）时，再跑 scan_rtl 配前缀重生成即可（不是错误）。"
                             "—— 和『输入缺前缀』的区别：那个是输入 force 不到、硬阻断；这个是输出怎么探、不阻断。",
               "false-green": "结构全解析通了，不是未解析——只是数据寄存器字段太窄、装不下每条 case "
                              "的互异值，硬生成会变『RTL 接错路也 PASS』的假测试(假绿)。工具保护性跳过；"
                              "要验得加宽字段或拆组（属设计层，不是工具/表的错）"}
# 状态列颜色：红=信号坏掉(会 elaboration 失败)；橙=要前缀否则跳过；蓝=信息(裸名探针已生成,可选配前缀)；
# 琥珀(false-green)=能解析但字段太窄、硬生成是假绿——保护性跳过，不是故障，刻意不用红
STATUS_FG = {"needs-prefix": QtGui.QColor("#cc7a00"), "bare-probe": QtGui.QColor("#2a7ab0"),
             "false-green": QtGui.QColor("#9a5b00"),
             "spec-collision": QtGui.QColor("#b5179e")}
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
USER_BG = QtGui.QColor("#eef3fb")       # mux 用户手编/复制列（第二十八轮）的整列淡底色，与自动生成列区分

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


def _code_version():
    """工具代码版本（git 短 HEAD，拿不到给空）——进窗口标题。

    2026-06-10 实地教训：用户机器上可能同时存在旧拷贝/旧进程，「改了却看不到」排查
    了一整轮才怀疑到版本——标题带 HEAD 后一眼可辨跑的是哪份代码。"""
    try:
        import subprocess
        return subprocess.run(
            ["git", "-C", os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
             "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=5).stdout.strip()
    except Exception:  # noqa: BLE001
        return ""


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        _ver = _code_version()
        self.setWindowTitle("Dreg_verify — wr_rf_tc.sv 生成器 + 测试项编辑"
                            + ("　[代码版本 %s]" % _ver if _ver else ""))
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
        self._sig_cov = {}       # 单点覆盖度 {信号名小写: 档位"min"/"max"/"exhaustive"}——该信号专属覆盖档，
                                 # 压过全局 logic/mux 下拉；不在表里=跟随全局。
                                 # ⚠ 仅【会话内】临时档，【不存盘、不恢复】：否则上次留下的单点档会被静默恢复、
                                 # 暗中盖过全局下拉，让"刚打开 GUI 改全局却对某些信号无效"(用户实测的 bug)。
                                 # 开 GUI 即一张白纸，全局下拉对所有信号生效；要个别不同当场调，关掉即忘。
        self._sig_cov_loading = False  # 程序化设单点覆盖下拉时屏蔽其 changed 信号，防加载信号时误触重算
        self._suffix_override = {}  # 单点尾缀覆盖 {信号名小写: True=探尾缀网/False=探裸名}——压过类型默认
                                 # (logic 默认随全局开、mux 默认裸名)。用于 logic 撞名信号(lo2g5g→False)与
                                 # mux 端口带尾缀信号(rxiq→True)。稳定的设计事实，故【存盘+随配置导入导出】
                                 # (区别于会话内临时的 _sig_cov)；按 Excel 路径记忆，启动恢复。
        self._suffix_loading = False  # 程序化设「本信号探尾缀网」勾选时屏蔽其 changed 信号
        # RTL 补充逻辑（2026-06-12）：{信号基名小写: spec}，spec={enabled,expr,inputs:[{var,raw}],note,out_name?}。
        # Excel 真表丢了某信号顶层口后的 ECO 级(如 d_en_vco_fc：SE 确认接了 2:1 mux+二级 iddq、真表只到 DREG)，
        # 用户把 SE 给的 RTL 实情丢给 Claude→Claude 写出等价 logic 补充式→这里导入/启用，build 当合成 logic 行
        # 扫真值表(ECO 新输入自动成维度)。稳定设计事实→【存盘+随配置导入导出】(同 _suffix_override)，按路径记忆。
        self._logic_overrides = {}
        self._sig_cascade = {}   # 单点级联模式 {信号名小写: "cone"/"force"}——压过全局 logic/mux 下拉。
                                 # 会话内临时档（与 _sig_cov 同款，不存盘/恢复，避免静默盖过全局）。
        self._sig_cascade_loading = False  # 程序化设「本信号级联」下拉时屏蔽其 changed 信号
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
        # 勾了"负向"的 mux 信号名(小写)集合。mux 负向不走 _edited(无表达式可编辑)，生成时经
        # neg_signals 追加；这里单独存盘，否则关 GUI 重开后 mux 的负向勾选会丢(logic 的不丢)。
        self._mux_neg = set()
        # mux 数据值手填（B2，第二十轮）：{信号名小写: {物理基名小写: int}}。替换自动互异/标记值，
        # 按物理寄存器键（与 by_base/点名法同口径，覆盖度切换/case 重排都稳）。单独存盘。
        self._mux_data = {}
        # mux 删除/清空（第二十六轮，用户拍板「mux 也要能删、logic+mux 都要一键清空」）：
        #   _mux_dropped = {信号名小写: {向量签名,...}} 用户删掉的【个别 mux 测试列】(签名=mux_assign_key)；
        #   _mux_cleared = {信号名小写} 用户「一键清空」的 mux 信号 = 零用例(与覆盖度无关)。
        # logic 的清空走 _edited[name]["rows"]=[]（空 override → build 跳过给原因）。两者都随桶存盘/导入导出。
        self._mux_dropped = {}
        self._mux_cleared = set()
        # mux 用户手编/复制的测试列（第二十八轮，mux 与 logic 平级）：{信号名小写: [TestVector]}。
        # 自动生成列只能删/改值/改期望；用户列是【新增】的(加正向列/复制列/逐case加负向)，可改名。
        # 每条 vec 的 case_index 标它路由的 case → auto_out=该 case 路由源值(改数据值即时重算)。
        # 经 GenOptions.mux_user_vecs 注入 build/report；随桶存盘/导入导出。
        self._mux_user_vecs = {}
        self._ti_mux_data_rows = {}   # 当前 mux 表里可手填数据行 row -> (物理基名小写, 位宽, 绑定键)
        self._ti_mux_vecs = []    # 当前 mux 信号的向量（生成列 + 用户列 + 左表勾的全局负向列）
        self._ti_mux_user_start = 1 << 30  # 用户列起始列号([user_start,user_end)=用户手编列，可编辑/改名)
        self._ti_mux_user_end = 1 << 30    # 用户列结束=全局负向起始([user_end,len)=左表勾「负向」的自检列，只读)
        self._ti_mux_exp_row = -1 # 当前 mux 表里"期望"行的行号
        # 真值表列宽：用户手动拖过 → 重建表格时保留手动宽度(换信号才恢复自动)
        self._ti_user_widths = False
        self._ti_auto_resizing = False
        # 探针前缀 {信号名小写: 层级前缀}：输出网在 ENV_RF 的子模块里时（如 pll_n 在
        # U_BT_LP_PLL_DIG 内部），断言探针写 `ENV_RF.<前缀>.<网名>。按 Excel 路径持久化。
        self._probe_prefixes = {}
        # 强制 force 的基名集合（小写）：列进来的信号直接 force 顶层基名网、跳过 cone 展开
        # （for_test 那招）。用于覆盖工具的自动判断（如撞名 RO 寄存器的内部信号）。按 Excel 路径持久化。
        self._force_signals = set()
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
        b_nets = QtWidgets.QPushButton("导出 nets.txt…")
        b_nets.setToolTip("把当前表需要在 ENV_RF 层级定位的网清单导出为 nets.txt，传到仿真服务器跑\n"
                          "scan_rtl.py 扫 RTL → 得到 probe_prefixes.txt → 回来用『设置探针前缀 → 导入…』套用。\n"
                          "（等价于 CLI：python scan_rtl.py --excel 真表.xlsx --export-nets nets.txt）")
        b_nets.clicked.connect(self.on_export_nets)
        b_force = QtWidgets.QPushButton("强制 force 信号")
        b_force.setToolTip("列出要『直接 force 顶层基名网、跳过 cone 展开』的信号基名(每行一个)。\n"
                           "用于覆盖工具的自动判断：撞名 RO 寄存器的内部信号(如 d_wl_rf_linectrl_band_sel)\n"
                           "等价于 for_test 的 force `ENV_RF.<基名>。cone 成环时工具已会自动回退，这里是手动指定。")
        b_force.clicked.connect(self.on_set_force_signals)
        b_supp = QtWidgets.QPushButton("RTL 补充逻辑…")
        b_supp.setToolTip("Excel 真表丢了某信号顶层口后的 ECO 级时(如 d_en_vco_fc：SE 确认接了 2:1 mux+二级 iddq、\n"
                          "真表只到 DREG，缺的控制网悬空→X)，在这里【补一条等价 logic 表达式】，工具当合成 logic 行\n"
                          "扫真值表，ECO 新输入(选择位/faston/二级iddq)自动成为真值表维度。\n"
                          "用法：把 SE 给的 RTL 实情发给 Claude → Claude 写出补充 JSON → 这里【粘贴/导入】并启用。\n"
                          "合成块顶会强制标 // ⚠ 供 SE 复核(偏离纯 Excel 推导)。")
        b_supp.clicked.connect(self.on_logic_overrides)
        # 完整配置的导出/导入：给同事复用、入版本库、跨机器迁移（第二十六轮：不只测试编辑，
        # 信号勾选/全局档/探针前缀/强制force 等所有配置一起带走）
        b_exp_edits = QtWidgets.QPushButton("导出配置…")
        b_exp_edits.setToolTip("把当前【完整配置】导出为 .json 文件，可给同事导入复用、入版本库存档：\n"
                               "  · 勾选了哪些信号、全局覆盖度/用例上限/级联/DFT\n"
                               "  · 探针前缀、强制 force 信号\n"
                               "  · 全部测试编辑(手填期望/负向/自定义列/数据值/删除列/清空)\n"
                               "配置本来就会自动存盘(关 GUI 不丢、下次自动恢复)，导出是为了共享/迁移。")
        b_exp_edits.clicked.connect(self.on_export_edits)
        b_imp_edits = QtWidgets.QPushButton("导入配置…")
        b_imp_edits.setToolTip("从 .json 文件导入配置 = 加载这份工作状态：\n"
                               "  · 完整配置文件：信号勾选/全局档/探针/force/全部测试编辑 全部套用(先清后载)\n"
                               "  · 旧版『测试项编辑』文件：仍按合并语义只并入测试编辑\n"
                               "文件里有、当前表里没有的信号会列出名字并跳过。")
        b_imp_edits.clicked.connect(self.on_import_edits)
        for b in (b_check, b_selall, b_selnone):
            bulk.addWidget(b)
        bulk.addWidget(QtWidgets.QLabel(" 负向:"))
        for b in (b_negall, b_negnone):
            bulk.addWidget(b)
        bulk.addWidget(b_prefix)
        bulk.addWidget(b_nets)
        bulk.addWidget(b_force)
        bulk.addWidget(b_supp)
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
        # 覆盖度（第二十二轮起 logic/mux 解耦为两个下拉，互不绑定——用户拍板）：把旧的
        # min/max + 小信号全穷举 合成的高层档位(精简<全面<穷举)分给 logic 侧、mux 侧各一个。
        self.coverage = QtWidgets.QComboBox(); self.coverage.addItems(["精简", "全面", "穷举"])
        self.coverage.setToolTip(
            "【logic 信号】测试用例覆盖强度(对'未自定义'的信号即时生效)：\n"
            "  精简 = 每种控制位组合各取 1 组代表数据(最少用例)\n"
            "  全面 = 每种控制位组合再扫多组数据(全0/全1/反码/走步/区分)\n"
            "  穷举 = 所有输入的全部组合(仅当总输入位≤10，否则自动退化为'全面')")
        self.coverage_mux = QtWidgets.QComboBox(); self.coverage_mux.addItems(["精简", "全面", "穷举"])
        self.coverage_mux.setToolTip(
            "【mux 信号】覆盖强度，与 logic 侧独立设置：\n"
            "·单控制 logic 行(line/local 双路径)：\n"
            "  精简 = 每 case 1 条(x位取0) + 1 条另一路径抽测\n"
            "  全面 = 精简 + case 的 x 位展开 + 每 case 一轮反码数据(抓数据通路位坏死)\n"
            "  穷举 = 全面 + 另一条控制路径全扫每 case(两条物理驱动路径全验)\n"
            "·多控制 / 寄存器直出 / mux 级联(直接驱动控制)：\n"
            "  精简 = 每 case 1 条(x位取0)\n"
            "  全面 = 精简 + case 的 x 位展开 + 每 case 一轮反码数据(抓数据通路位坏死)\n"
            "  穷举 = 同全面(没有另一条物理控制路径可扫)\n"
            "·被 dft 页 iddq 门控的输出：门列为输入行（每条测试 force 到透传值）\n"
            "  + 各档都补 1 条 IDDQ 漏电态拍（force 门=1 验输出压 0）\n"
            "⚠ 精简档级联只走 mode=1 主载体（不验 mode=0 RO 线控分支）；\n"
            "  升【全面/穷举】才补该覆盖（缺口在报告/产物注释里也会标注）")
        # 恢复上次档位（连接信号【前】设值，避免初始化期间误触 on_coverage_changed）；
        # 迁移：旧文件只有单 coverage 键时同步赋两侧，缺键则默认精简（index 0）。
        # ⚠ pytest 下【不】从真实 settings 恢复（与 _save_settings 的 pytest no-op 对称）：否则用户
        # 真机 ~/.dreg_verify_gui.json 里持久化的 coverage_mux 会污染「断言覆盖档版面」的 GUI 测试
        # （旧版从不恢复 → 测试恒以默认精简起步，这里保持同一行为）。
        if "pytest" not in sys.modules:
            _cst = _load_settings()
            _legacy_cov = _cst.get("coverage")
            for _combo, _key in ((self.coverage, "coverage_logic"),
                                 (self.coverage_mux, "coverage_mux")):
                _v = _cst.get(_key, _legacy_cov)
                if _v in ("精简", "全面", "穷举"):
                    _combo.setCurrentText(_v)
        self.coverage.currentIndexChanged.connect(self.on_coverage_changed)
        self.coverage_mux.currentIndexChanged.connect(self.on_coverage_changed)
        self.cov_hint = QtWidgets.QLabel("")            # 实时显示当前信号的用例条数
        self.cov_hint.setStyleSheet("color:#1558d6;")
        self.max_tests = QtWidgets.QSpinBox(); self.max_tests.setRange(1, 100000); self.max_tests.setValue(256)
        self.max_tests.setToolTip("用例数上限(安全阀，防止穷举/全面产生过多用例)")
        if "pytest" not in sys.modules:          # 恢复上次用例上限(连信号前设值，避免初始化误触)；pytest 不恢复
            _mt = _load_settings().get("max_tests")
            if isinstance(_mt, int) and 1 <= _mt <= 100000:
                self.max_tests.setValue(_mt)
        self.max_tests.valueChanged.connect(self.on_coverage_changed)
        # 级联模式 logic/mux 解耦（2026-06-11 用户拍板）：输入/控制引用『上游算出来的网』时怎么驱动。
        # 两个独立下拉——logic 多走「展开上游」(纯 Excel 不需前缀)、mux 控制常需「force级联网」直 force 衔接网。
        _casc_tip = (
            "输入/mux 控制引用『上游算出来的网』(级联)时怎么驱动，点旁边 ? 看图解：\n\n"
            "  展开上游(默认)：把上游表达式代入，改为驱动它的源头寄存器/管脚。\n"
            "      优点=纯 Excel、不需要探针前缀；代价=上游逻辑跟本行一起验，上游有 bug 会连带本行 fail\n\n"
            "  force级联网：直接 force 那根 _to_logic/_to_mux 衔接网。\n"
            "      优点=隔离验证、fail 定位准（mux 控制是深级联时尤其稳）；代价=该网在子模块内部，\n"
            "      必须先跑 scan_rtl 拿到层级前缀，否则该信号会被跳过")
        self.cascade_logic_combo = QtWidgets.QComboBox()
        self.cascade_logic_combo.addItems(["展开上游(推荐)", "force级联网"])
        self.cascade_logic_combo.setToolTip("【logic 信号】级联驱动模式。\n" + _casc_tip)
        if _load_settings().get("cascade_logic", _load_settings().get("cascade_mode")) == "force":
            self.cascade_logic_combo.setCurrentIndex(1)
        self.cascade_logic_combo.currentIndexChanged.connect(self.on_cascade_mode_changed)
        self.cascade_mux_combo = QtWidgets.QComboBox()
        self.cascade_mux_combo.addItems(["展开上游(推荐)", "force级联网"])
        self.cascade_mux_combo.setToolTip(
            "【mux 信号】级联驱动模式。mux 控制信号若是另一个 mux 的输出(深级联)，"
            "「展开上游」常驱不到确定值(控制 X→输出走 default)→ 改「force级联网」直接 force 控制衔接网更稳。\n\n"
            + _casc_tip)
        if _load_settings().get("cascade_mux", _load_settings().get("cascade_mode")) == "force":
            self.cascade_mux_combo.setCurrentIndex(1)
        self.cascade_mux_combo.currentIndexChanged.connect(self.on_cascade_mode_changed)
        cascade_help = QtWidgets.QPushButton("?")
        cascade_help.setFixedWidth(24)
        cascade_help.setToolTip("级联模式帮助：两种模式的图解与选择建议(程序内置窗口)")
        cascade_help.clicked.connect(self._open_cascade_doc)
        # 输出尾缀开关（2026-06-11 Hi1108）：top_output=0 的输出若被下游引用，探针网名补【引用尾缀】，
        # 尾缀随 Excel：被 logic 行以 <名>_to_logic 引用→_to_logic；被 mux 页以 <名>_to_mux 引用→_to_mux。
        # 关掉=直接探基名网。**默认勾**（2026-06-11 用户拍板：Hi1108 rxiq 实证 top_out=0 内部网 RTL 真名
        # 就带尾缀=补尾缀才是常态；撞名信号是少数，用左表「本信号探裸名」单独关）。logic 与 mux 输出同口径。
        self.append_to_logic_chk = QtWidgets.QCheckBox("logic加尾缀")
        self.append_to_logic_chk.setChecked(True)   # 生产/pytest 默认勾(logic 被引用输出探尾缀网=RTL 真名)
        self.append_to_logic_chk.setToolTip(
            "【logic 输出】全局尾缀开关。勾上（默认）：被下游 logic 行以 <名>_to_logic 引用的 logic 输出，\n"
            "断言探针网名补 _to_logic（pll_n→pll_n_to_logic）——这是这些内部网的 RTL 真名（LPBT 实证）。\n"
            "取消：探基名裸网。（顶层输出、_ls 不受影响。）\n\n"
            "个别 logic 输出的 <名>_to_logic 恰好撞了另一个真实输入网（如 lo2g5g）→ 左表「本信号探尾缀网」\n"
            "单独取消，不必关全局。（= CLI --no-ref-suffix；mux 输出由旁边的「mux加尾缀」单独管。）")
        if "pytest" not in sys.modules:
            self.append_to_logic_chk.setChecked(bool(_load_settings().get("append_to_logic", True)))
        self.append_to_logic_chk.stateChanged.connect(self.on_append_to_logic_changed)
        # mux 输出全局尾缀开关（2026-06-11 用户拍板 logic/mux 分开）：**默认不勾**——mux 输出端口带不带
        # 尾缀是设计相关的(WL 裸名 / Hi1108 rxiq 带 _to_logic)，工具不默认改；整设计要补就勾这个一次全开。
        self.append_to_mux_chk = QtWidgets.QCheckBox("mux加尾缀")
        self.append_to_mux_chk.setChecked(False)
        self.append_to_mux_chk.setToolTip(
            "【mux 输出】全局尾缀开关。**默认不勾 = mux 输出探基名裸网**（WL 实证：mux 输出端口是裸名）。\n"
            "勾上：所有被下游引用的 mux 输出都补其去向尾缀（_to_logic/_to_mux）——用于端口真名带尾缀的设计\n"
            "（如 Hi1108 rxiq：2:1 mux 喂 sig_logic，RTL 端口本身叫 d_wl_rf_rxiq_phase_ctrl_to_logic）。\n\n"
            "为什么和 logic 分开：mux 输出端口带不带尾缀【设计相关、Excel 推不出】，所以不跟 logic 一起默认补。\n"
            "勾上后个别真裸名的 mux 输出 → 左表「本信号探尾缀网」单独取消。（= CLI --mux-ref-suffix）")
        if "pytest" not in sys.modules:
            self.append_to_mux_chk.setChecked(bool(_load_settings().get("append_to_mux", False)))
        self.append_to_mux_chk.stateChanged.connect(self.on_append_to_mux_changed)
        # 缺前缀强制生成（2026-06-10 Hi1108）：**默认勾选**（2026-06-11 用户拍板）——新设计层级可能与
        # LPBT/Hi1107C 不同、根本不需要前缀，先照常生成裸名 force 交给仿真验证(过=不需前缀；CUVUNF 再配)。
        self.include_risky_chk = QtWidgets.QCheckBox("缺前缀强制生成")
        self.include_risky_chk.setToolTip(
            "默认行为：要 force 的输入网被判定在子模块内部(级联衔接网/wire 兜底)、又没配探针前缀时，\n"
            "该信号跳过生成（防 elaboration CUVUNF——这是 LPBT/Hi1107C 上的实证结论）。\n\n"
            "勾上 = 这类信号照常生成（force 用裸名 `ENV_RF.<网名>），用仿真验证本设计是否真需要前缀：\n"
            "  · elaboration 全过 = 此设计这些网顶层直达，不需要前缀，保持勾选即可\n"
            "  · 报 CUVUNF = 网确实埋在子模块，跑 scan_rtl 配前缀后重新生成\n"
            "（与 CLI --include-risky 同义；左表状态列会显示「已强制生成」。）")
        # pytest 下保持不勾(基线=按 LPBT 跳过 risky，测试不变)；生产默认勾(缺键=True)，settings 可改
        if "pytest" not in sys.modules:
            self.include_risky_chk.setChecked(bool(_load_settings().get("include_risky", True)))
        self.include_risky_chk.stateChanged.connect(self.on_include_risky_changed)
        for w in (QtWidgets.QLabel("logic覆盖:"), self.coverage,
                  QtWidgets.QLabel("mux覆盖:"), self.coverage_mux, self.cov_hint,
                  QtWidgets.QLabel("   上限"), self.max_tests,
                  QtWidgets.QLabel("   logic级联:"), self.cascade_logic_combo,
                  QtWidgets.QLabel("mux级联:"), self.cascade_mux_combo, cascade_help,
                  self.append_to_logic_chk, self.append_to_mux_chk, self.include_risky_chk):
            opt.addWidget(w)
        opt.addStretch(1)
        root.addLayout(opt)

        btns = QtWidgets.QHBoxLayout()
        prev = QtWidgets.QPushButton("预览选中"); prev.clicked.connect(self.on_preview)
        prev.setShortcut("Ctrl+P")
        prev.setToolTip("预览所有已勾选信号合并生成的 .sv (Ctrl+P)；只看单信号用右侧『预览本信号.sv』")
        rep = QtWidgets.QPushButton("导出报告(HTML/Excel/CSV)…"); rep.clicked.connect(self.on_report)
        rep.setShortcut("Ctrl+R")
        rep.setToolTip("出'给人看'的测试用例报告(汇总+每信号真值表+完整明细)，自动带上你的编辑；"
                       "未勾选则覆盖全部信号。可选 HTML/Excel/CSV (Ctrl+R)")
        ft = QtWidgets.QPushButton("回填 for_test…"); ft.clicked.connect(self.on_fortest)
        ft.setToolTip("把当前测试项按 for_test 真值表排版回填到 Excel：复制源 Excel 全部 sheet、"
                      "只替换 for_test 页(源文件不动)。给 designer 看/复制粘贴；未勾选则覆盖全部信号")
        gen = QtWidgets.QPushButton("生成 .sv …"); gen.clicked.connect(self.on_generate)
        gen.setShortcut("Ctrl+G")
        gen.setToolTip("点开后可选导出范围(全部/仅正向/仅负向)与是否加注释 (Ctrl+G)")
        btns.addStretch(1); btns.addWidget(prev); btns.addWidget(rep)
        btns.addWidget(ft); btns.addWidget(gen)
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

        # 单点覆盖度：给【当前这一个信号】单独设覆盖档，压过工具栏的全局 logic/mux 下拉。
        # 「跟随全局」=不设单点、用全局档（默认）。按信号名记忆、随测试项编辑存盘/导入导出。
        sigcov_box = QtWidgets.QWidget()
        sigcov = QtWidgets.QHBoxLayout(sigcov_box)
        sigcov.setContentsMargins(0, 0, 0, 0)
        sigcov.addWidget(QtWidgets.QLabel("本信号覆盖度:"))
        self.sig_cov_combo = QtWidgets.QComboBox()
        self.sig_cov_combo.addItems(["跟随全局", "精简", "全面", "穷举"])
        self.sig_cov_combo.setEnabled(False)        # 未选信号时不可用
        self.sig_cov_combo.setToolTip(
            "给【当前选中的这一个信号】单独设覆盖档，压过上方工具栏的全局 logic/mux 覆盖下拉：\n"
            "  跟随全局 = 用全局档（默认）——改全局档对所有'跟随全局'的信号生效\n"
            "  精简/全面/穷举 = 只有此信号用该档（真值表/预览/生成/报告都按此）\n"
            "用法：先调全局档定大盘，再对个别要重点验/要省用例的信号单独设单点档。\n"
            "⚠ 只是【本次开着 GUI 期间】的临时档，不存盘——关掉重开即忘，开 GUI 永远是\n"
            "  全局档当家（避免上次留的单点档静默盖过全局下拉）。改全局下拉会清掉当前信号的单点档。")
        self.sig_cov_combo.currentIndexChanged.connect(self.on_sig_cov_changed)
        sigcov.addWidget(self.sig_cov_combo)
        self.sig_cov_tag = QtWidgets.QLabel("")     # 生效来源提示（单点/跟随全局），随信号刷新
        self.sig_cov_tag.setStyleSheet("color:#667;")
        sigcov.addWidget(self.sig_cov_tag)
        sigcov.addSpacing(16)
        # 本信号探尾缀网（2026-06-11）：单点强制【当前信号】探带去向尾缀(_to_logic/_to_mux)的网，压过
        # 类型默认。logic 输出被引用时默认就探尾缀网(勾)、mux 输出默认探裸名(不勾)。用于个别例外信号：
        # mux 端口本身带尾缀(rxiq→勾)、logic 撞名信号(lo2g5g→取消勾)。按信号名记忆、存盘 + 随配置导入导出。
        self.suffix_chk = QtWidgets.QCheckBox("本信号探尾缀网")
        self.suffix_chk.setEnabled(False)        # 未选信号 / 该信号未被下游引用(无尾缀可补) 时不可用
        self.suffix_chk.setToolTip(
            "勾上：【当前这一个信号】的断言探针探带【去向尾缀】的网(<名>_to_logic / <名>_to_mux)；\n"
            "取消：探基名裸网(<名>)。压过类型默认。\n\n"
            "类型默认：logic 输出被下游以 <名>_to_logic 引用时，RTL 真名就带尾缀→默认勾(=RTL 真名)；\n"
            "mux 输出端口带不带尾缀是【设计相关】(WL=裸名+另有衔接网；Hi1108 rxiq=端口本身叫 _to_logic)，\n"
            "工具推不出→默认【不勾】(探裸名)。\n\n"
            "什么时候动它：① mux 输出仿真报 CUVUNF、scan_rtl 查到真名带 _to_logic/_to_mux→勾上(如 rxiq)；\n"
            "② logic 输出的 <名>_to_logic 恰好撞了另一根真实输入网(如 lo2g5g)→取消勾、探裸名。\n"
            "(未被下游引用的信号无尾缀可补，本框禁用；= CLI --suffix-signals / --no-suffix-signals。)")
        self.suffix_chk.stateChanged.connect(self.on_suffix_changed)
        sigcov.addWidget(self.suffix_chk)
        sigcov.addSpacing(16)
        # 本信号级联（2026-06-11）：给【当前信号】单独设级联模式，压过全局 logic/mux 级联下拉。
        # 会话内临时档（同单点覆盖度，不存盘）。用于个别深级联 mux（如 rxiq 控制走 force）。
        sigcov.addWidget(QtWidgets.QLabel("本信号级联:"))
        self.sig_cascade_combo = QtWidgets.QComboBox()
        self.sig_cascade_combo.addItems(["跟随全局", "展开上游", "force级联网"])
        self.sig_cascade_combo.setEnabled(False)
        self.sig_cascade_combo.setToolTip(
            "给【当前选中的这一个信号】单独设级联模式，压过上方全局 logic/mux 级联下拉：\n"
            "  跟随全局 = 用对应类型的全局级联档（默认）\n"
            "  展开上游 / force级联网 = 只有此信号用该模式\n"
            "用于个别深级联信号(如 mux 控制是另一 mux 的输出、展开上游驱不到→单独切 force级联网)。\n"
            "⚠ 只是本次开着 GUI 期间的临时档，不存盘——开 GUI 永远全局档当家。")
        self.sig_cascade_combo.currentIndexChanged.connect(self.on_sig_cascade_changed)
        sigcov.addWidget(self.sig_cascade_combo)
        sigcov.addStretch(1)
        lay.addWidget(sigcov_box)

        bar_box = QtWidgets.QWidget()
        bar = FlowLayout(bar_box)        # 按钮多，窄屏自动换行，避免给右面板强加大最小宽度
        defs = [("重新生成", self.on_ti_regen, "丢弃本信号自定义，按当前向量选项从表达式重新生成"),
                ("加正向列", self.on_ti_add, "新增一条正向(真实)测试(输入全 0，auto_out 自动算，期望留空待填)"),
                ("复制列", self.on_ti_copy, "复制当前选中的测试列"),
                ("删除列", self.on_ti_del, "删除选中的测试列(logic/mux 都可；mux 删的是个别 case 测试列)"),
                ("清空本信号", self.on_ti_clear, "一键清空本信号的所有测试 = 零用例(生成 .sv 时本信号不产出)。\n"
                 "可逆：点「重新生成」按当前覆盖度恢复默认。"),
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
        self._ti_btns, self._ti_btn_tips = {}, {}
        for text, slot, tip in defs:
            b = QtWidgets.QPushButton(text); b.clicked.connect(slot)
            if text == "复制列":
                tip = "%s (Ctrl+D)" % tip       # 快捷键用 ti_table 上的 WidgetShortcut(见下)，不挂按钮(否则编辑中也会触发)
            b.setToolTip(tip)
            self._ti_btns[text] = b
            self._ti_btn_tips[text] = tip       # 存原 tooltip，选回 logic 信号时恢复
            bar.addWidget(b)
        # 第二十八轮：mux 与 logic 平级——全部列编辑按钮都给了 mux 分支(加正向列=按 case 加用户列、
        # 复制列/重命名列/逐 case 加负向/全部加负向/删负向/导出CSV)，故 mux 选中时【全部可用】，
        # 不再有"无 mux 分支"的置灰按钮。_ti_mux_disabled_btns 留空(保留机制以备将来)。
        self._ti_mux_enabled_btns = tuple(self._ti_btns.keys())
        self._ti_mux_disabled_btns = [t for t in self._ti_btns
                                      if t not in self._ti_mux_enabled_btns]
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
        """logic 侧「覆盖度」下拉 → (mode, exhaustive)。穷举位数过多时由 vectors 自动退化为'全面'。"""
        c = self.coverage.currentText()
        if c == "穷举":
            return ("max", True)
        if c == "全面":
            return ("max", False)
        return ("min", False)       # 精简

    def _mux_coverage(self):
        """mux 侧「覆盖度」下拉 → (mode, exhaustive)。与 logic 侧 _coverage() 独立（第二十二轮解耦）。"""
        c = self.coverage_mux.currentText()
        if c == "穷举":
            return ("max", True)
        if c == "全面":
            return ("max", False)
        return ("min", False)       # 精简

    def _mux_cov_mode(self):
        """mux 覆盖档 {min,max,exhaustive}（与 generator/report 同口径）。"""
        return mux_gen.coverage_mode(*self._mux_coverage())

    # 单点覆盖度档位 ↔ 中文标签互转（与全局下拉同口径：精简=min/全面=max/穷举=exhaustive）
    _COV_LABEL2MODE = {"精简": "min", "全面": "max", "穷举": "exhaustive"}
    _COV_MODE2LABEL = {"min": "精简", "max": "全面", "exhaustive": "穷举"}

    def _sig_cov_collapsed(self, sig):
        """该信号【实际生效】的覆盖档(min/max/exhaustive)：单点设置优先，否则跟随全局
        （mux 信号→mux 全局档，logic 信号→logic 全局档）。生成/预览/报告全经此口径。"""
        name_low = sig.out_name.lower() if sig is not None else None
        ov = self._sig_cov.get(name_low) if name_low else None
        if ov in ("min", "max", "exhaustive"):
            return ov
        if isinstance(sig, excel_model.MuxGroup):
            return self._mux_cov_mode()
        return mux_gen.coverage_mode(*self._coverage())

    def _set_sig_cov_combo(self, sig):
        """加载信号时把「本信号覆盖度」下拉置到该信号已存的单点档（无则"跟随全局"），不触发重算。
        同时刷新生效来源提示。无信号 → 禁用下拉并清空提示。"""
        if not hasattr(self, "sig_cov_combo"):
            return
        name_low = sig.out_name.lower() if sig is not None else None
        ov = self._sig_cov.get(name_low) if name_low else None
        label = self._COV_MODE2LABEL.get(ov, "跟随全局")
        self._sig_cov_loading = True
        try:
            self.sig_cov_combo.setEnabled(sig is not None)
            self.sig_cov_combo.setCurrentText(label)
        finally:
            self._sig_cov_loading = False
        if sig is None:
            self.sig_cov_tag.setText("")
        elif ov:
            self.sig_cov_tag.setText("（单点档，已压过全局）")
        else:
            gl = "mux" if isinstance(sig, excel_model.MuxGroup) else "logic"
            self.sig_cov_tag.setText("（跟随全局 %s 档=%s）"
                                     % (gl, self._COV_MODE2LABEL[self._sig_cov_collapsed(sig)]))

    def on_sig_cov_changed(self, *args):
        """「本信号覆盖度」下拉变化：记进 _sig_cov（"跟随全局"=删除该信号的单点设置），
        按新档重算【当前信号】的测试项。单点档是会话内临时档、不存盘。
        加载信号时由 _sig_cov_loading 守卫避免误触。"""
        if (self._sig_cov_loading or self._sig_loading
                or getattr(self, "_ti_loading", False)):
            return
        sig = self._ti_sig if self._ti_sig is not None else getattr(self, "_ti_mux_sig", None)
        if sig is None:
            return
        name_low = sig.out_name.lower()
        collapsed = self._COV_LABEL2MODE.get(self.sig_cov_combo.currentText())
        if collapsed is None:                 # 跟随全局
            self._sig_cov.pop(name_low, None)
        else:
            self._sig_cov[name_low] = collapsed
        # 单点档不存盘（会话内临时），故这里不调 _persist_edits
        if self._ti_sig is not None:
            self._load_test_items(self._ti_sig)
        elif getattr(self, "_ti_mux_sig", None) is not None:
            self._load_mux_test_items(self._ti_mux_sig)
        else:
            self._set_sig_cov_combo(sig)       # 兜底刷新提示
        self._update_cov_hint()

    def _suffix_type_default(self, sig):
        """该信号尾缀的【类型默认】(无单点覆盖时的状态)：mux 输出跟「mux加尾缀」、logic 输出跟「logic加尾缀」。"""
        if isinstance(sig, excel_model.MuxGroup):
            return self._append_to_mux_on()
        return self._append_to_logic_on()

    def _set_suffix_chk(self, sig):
        """加载信号时把「本信号探尾缀网」勾选置到该信号实际生效状态（单点覆盖优先，否则类型默认），
        不触发重析。无信号 / 该信号未被下游引用(无尾缀可补) → 禁用。"""
        if not hasattr(self, "suffix_chk"):
            return
        name_low = sig.out_name.lower() if sig is not None else None
        has_suffix = bool(sig is not None and getattr(sig, "ref_suffix", ""))
        ov = self._suffix_override.get(name_low) if name_low else None
        effective = ov if ov is not None else (self._suffix_type_default(sig) if sig else False)
        self._suffix_loading = True
        try:
            self.suffix_chk.setEnabled(has_suffix)
            self.suffix_chk.setChecked(bool(has_suffix and effective))
        finally:
            self._suffix_loading = False

    def on_suffix_changed(self, *args):
        """「本信号探尾缀网」勾选变化：记进 _suffix_override(=类型默认则删除该条，保持映射=偏离项)，
        存盘 + 重析全表(rtl_base 随之变，必须重建 Resolver)。加载时由 _suffix_loading 守卫避免误触。"""
        if (self._suffix_loading or self._sig_loading
                or getattr(self, "_ti_loading", False)):
            return
        sig = self._ti_sig if self._ti_sig is not None else getattr(self, "_ti_mux_sig", None)
        if sig is None:
            return
        name_low = sig.out_name.lower()
        checked = self.suffix_chk.isChecked()
        if checked == self._suffix_type_default(sig):
            self._suffix_override.pop(name_low, None)   # 回到类型默认 → 不留偏离项
        else:
            self._suffix_override[name_low] = checked
        self._save_suffix_override()
        self._reanalyze_all()       # rtl_base 变了 → 重建 Resolver 重析（前缀命中/状态随之刷新）

    def _save_suffix_override(self):
        """单点尾缀覆盖按 Excel 路径写入 settings（pytest 下 no-op，与 probe_prefixes 同策略）。"""
        if "pytest" in sys.modules:
            return
        st = _load_settings()
        all_maps = st.get("suffix_override", {})
        path = self.path_edit.text().strip()
        if self._suffix_override:
            all_maps[path] = dict(self._suffix_override)
        else:
            all_maps.pop(path, None)
        st["suffix_override"] = all_maps
        _save_settings(st)

    def _logic_signals(self):
        """wb.logic 应用当前 RTL 补充后的 logic 信号列表（合成信号替换原行/纯新增追加，与 build/report 同口径）。
        无补充 → 原 wb.logic（逐字节不变）。左表/编辑器用它，故补充加进来后真值表会同步刷新出新输入维度。"""
        if not self.wb:
            return []
        if not self._logic_overrides:
            return list(self.wb.logic)
        opts = generator.GenOptions(
            logic_overrides={k: dict(v) for k, v in self._logic_overrides.items()})
        return list(generator._logic_with_overrides(self.wb, opts))

    def _save_logic_overrides(self):
        """RTL 补充逻辑按 Excel 路径写入 settings（pytest 下 no-op，同 suffix_override 策略）。"""
        if "pytest" in sys.modules:
            return
        st = _load_settings()
        all_maps = st.get("logic_overrides", {})
        path = self.path_edit.text().strip()
        if self._logic_overrides:
            all_maps[path] = {k: dict(v) for k, v in self._logic_overrides.items()}
        else:
            all_maps.pop(path, None)
        st["logic_overrides"] = all_maps
        _save_settings(st)

    def _persist_coverage(self):
        """持久化两侧覆盖档（第二十二轮解耦）+ 用例上限（第二十六轮），下次启动恢复。pytest 下 no-op。"""
        st = _load_settings()
        st["coverage_logic"] = self.coverage.currentText()
        st["coverage_mux"] = self.coverage_mux.currentText()
        st["max_tests"] = self.max_tests.value()
        _save_settings(st)

    # ───────────── 级联模式（展开上游 / force级联网，logic/mux 解耦 + 单点） ─────────────
    def _logic_cascade(self):
        """logic 全局级联模式 → "cone"/"force"。"""
        if not hasattr(self, "cascade_logic_combo"):
            return "cone"
        return "force" if self.cascade_logic_combo.currentIndex() == 1 else "cone"

    def _mux_cascade(self):
        """mux 全局级联模式 → "cone"/"force"。"""
        if not hasattr(self, "cascade_mux_combo"):
            return "cone"
        return "force" if self.cascade_mux_combo.currentIndex() == 1 else "cone"

    def _cascade_for(self, sig):
        """该信号实际生效的级联模式：单点 _sig_cascade > 类型全局(mux/logic 下拉)。"""
        name = sig.out_name.lower() if sig is not None else None
        ov = self._sig_cascade.get(name) if name else None
        if ov in ("cone", "force"):
            return ov
        return self._mux_cascade() if isinstance(sig, excel_model.MuxGroup) else self._logic_cascade()

    def on_cascade_mode_changed(self, *args):
        """级联模式切换(logic 或 mux 全局) → 持久化 + 重建 Resolver 重析全表 + 重算当前编辑器信号。"""
        st = _load_settings()
        st["cascade_logic"] = self._logic_cascade()
        st["cascade_mux"] = self._mux_cascade()
        _save_settings(st)
        if not self.wb:
            return
        self._reanalyze_all()
        if (self._ti_sig is not None and self._ti_name_low is not None
                and self._ti_name_low not in self._customized):
            self._load_test_items(self._ti_sig)
        elif getattr(self, "_ti_mux_sig", None) is not None:
            # mux 真值表也按新级联模式重渲（同 on_coverage_changed）：切 force 后控制行变 force 衔接网。
            self._load_mux_test_items(self._ti_mux_sig)
        self.status.showMessage("级联模式已更新（logic=%s / mux=%s）——含级联的信号已按新模式重新解析"
                                % (self.cascade_logic_combo.currentText(),
                                   self.cascade_mux_combo.currentText()))

    _CASC_LABEL2MODE = {"展开上游": "cone", "force级联网": "force"}
    _CASC_MODE2LABEL = {"cone": "展开上游", "force": "force级联网"}

    def _set_sig_cascade_combo(self, sig):
        """加载信号时把「本信号级联」下拉置到该信号已存的单点档（无则"跟随全局"），不触发重析。无信号→禁用。"""
        if not hasattr(self, "sig_cascade_combo"):
            return
        name = sig.out_name.lower() if sig is not None else None
        ov = self._sig_cascade.get(name) if name else None
        self._sig_cascade_loading = True
        try:
            self.sig_cascade_combo.setEnabled(sig is not None)
            self.sig_cascade_combo.setCurrentText(self._CASC_MODE2LABEL.get(ov, "跟随全局"))
        finally:
            self._sig_cascade_loading = False

    def on_sig_cascade_changed(self, *args):
        """「本信号级联」下拉变化：记进 _sig_cascade（"跟随全局"=删该条），重析全表 + 重载当前编辑器。
        会话内临时档、不存盘。加载信号时由 _sig_cascade_loading 守卫避免误触。"""
        if (self._sig_cascade_loading or self._sig_loading
                or getattr(self, "_ti_loading", False)):
            return
        sig = self._ti_sig if self._ti_sig is not None else getattr(self, "_ti_mux_sig", None)
        if sig is None:
            return
        name = sig.out_name.lower()
        mode = self._CASC_LABEL2MODE.get(self.sig_cascade_combo.currentText())
        if mode is None:
            self._sig_cascade.pop(name, None)      # 跟随全局
        else:
            self._sig_cascade[name] = mode
        if self.wb is not None:
            self._reanalyze_all()
            self._reload_open_editor()

    def _append_to_logic_on(self):
        return (self.append_to_logic_chk.isChecked()
                if hasattr(self, "append_to_logic_chk") else True)

    def _reload_open_editor(self):
        """重析后把当前打开的 per-signal 编辑器也刷新——否则探针网名改了、左表变了，但编辑器/「预览
        本信号.sv」看着没动（用户会觉得"点了没生效"）。与 on_cascade_mode_changed 同口径。"""
        if (self._ti_sig is not None and self._ti_name_low is not None
                and self._ti_name_low not in self._customized):
            self._load_test_items(self._ti_sig)
        elif getattr(self, "_ti_mux_sig", None) is not None:
            self._load_mux_test_items(self._ti_mux_sig)

    def on_append_to_logic_changed(self, *args):
        """logic 输出引用尾缀开关切换：持久化 + 重建 Resolver 重析全表（影响所有 top_out=0 被引用
        logic 输出的探针网名 → 左表 out_net / 状态 / 预览全变）+ 刷新当前编辑器。"""
        st = _load_settings()
        st["append_to_logic"] = self.append_to_logic_chk.isChecked()
        _save_settings(st)
        if self.wb is not None:
            self._reanalyze_all()
            self._reload_open_editor()
        self._refresh_preview()

    def _append_to_mux_on(self):
        return (self.append_to_mux_chk.isChecked()
                if hasattr(self, "append_to_mux_chk") else False)

    def on_append_to_mux_changed(self, *args):
        """mux 输出引用尾缀开关切换：持久化 + 重建 Resolver 重析全表（影响所有被引用 mux 输出的
        探针网名）+ 刷新当前编辑器。与 logic 开关独立。"""
        st = _load_settings()
        st["append_to_mux"] = self.append_to_mux_chk.isChecked()
        _save_settings(st)
        if self.wb is not None:
            self._reanalyze_all()
            self._reload_open_editor()
        self._refresh_preview()

    def _include_risky_on(self):
        return (self.include_risky_chk.isChecked()
                if hasattr(self, "include_risky_chk") else False)

    def on_include_risky_changed(self, *args):
        """缺前缀强制生成切换：持久化 + 刷新左表状态/预览。
        include_risky【不改输入解析(Resolver)、也不改 logic 分析】——只影响 mux 风险分析与 skip/状态显示。
        故复用现有 Resolver，只重算 mux 组分析（吃 include_risky），logic 状态在重绘时按 include_risky 出
        『已强制生成』即可。省掉昂贵的 Resolver 重建 + 全 logic 重析（消除大表点击卡顿）。"""
        st = _load_settings()
        st["include_risky"] = self.include_risky_chk.isChecked()
        _save_settings(st)
        if self.wb is not None:
            for i, s in enumerate(self.signals):
                if isinstance(s, excel_model.MuxGroup):
                    try:
                        self._analysis[i] = self._analyze_one(s)
                    except Exception as ex:  # noqa: BLE001
                        self._analysis[i] = {"status": "解析异常", "inputs": [], "out_net": "",
                                             "error": repr(ex)}
            self._populate_table()
        self._refresh_preview()

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
        · 手改过测试项的信号：保留编辑不动(避免冲掉用户工作)。
        两侧覆盖下拉与 max_tests 都连到此；mux 编辑器读 mux 档、logic 编辑器读 logic 档。"""
        self._persist_coverage()
        if self._sig_loading or getattr(self, "_ti_loading", False):
            return
        self._clear_displayed_sig_cov_on_global()    # 「动全局=对当前信号生效」(用户拍板)
        sig, name_low = self._ti_sig, self._ti_name_low
        if sig is not None and name_low is not None:
            # 纯自动信号、或 neg_only 信号(_load_test_items 内会按新覆盖度重算正向+补回负向) → 重载；
            # 手工编辑过测试项的信号(_customized 但非 neg_only)保留编辑、不重算(避免冲掉用户工作)。
            if name_low not in self._customized or name_low in self._neg_only:
                self._load_test_items(sig)
        elif getattr(self, "_ti_mux_sig", None) is not None:
            self._load_mux_test_items(self._ti_mux_sig)   # mux：按新覆盖度重算(手填期望按取值键自动回填)
        self._update_cov_hint()

    def _clear_displayed_sig_cov_on_global(self):
        """「动全局=对当前信号生效」(用户拍板)：当用户改动【与当前信号同类】的全局覆盖下拉时，
        清掉当前正在看的这个信号的单点档(若有)，让全局改动立刻可见——你伸手去拧哪个旋钮都对
        眼前信号生效。其它没在看的信号各自保留单点档（下次点开经 tag 可见）。

        · 只认覆盖下拉(logic→self.coverage / mux→self.coverage_mux)，按信号类型对号；
          改了不相干那侧的下拉不清档。
        · 不认 max_tests：那是用例数上限(对单点档照样生效)，不该清档。
        · 经 self.sender() 辨别触发控件；非信号触发(直接调用)时 sender 为 None，不清档。
        """
        sender = self.sender()
        if sender not in (self.coverage, self.coverage_mux):
            return
        sig = self._ti_sig if self._ti_sig is not None else getattr(self, "_ti_mux_sig", None)
        if sig is None:
            return
        relevant = (self.coverage_mux if isinstance(sig, excel_model.MuxGroup)
                    else self.coverage)
        if sender is not relevant:
            return            # 改的是另一类的全局下拉，与当前信号无关
        name_low = sig.out_name.lower()
        if name_low in self._sig_cov:
            self._sig_cov.pop(name_low, None)   # 单点档不存盘，无需 _persist_edits
            # 立刻回显单点下拉/tag 为「跟随全局」——自定义信号那条 on_coverage_changed 不重渲，
            # 否则下拉会停在旧的「单点档」直到重选(对抗评审 minor)。
            self._set_sig_cov_combo(sig)
            self.status.showMessage("已清除 %s 的单点覆盖档，改回跟随全局『%s』"
                                    % (sig.out_name, sender.currentText()))

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
            n_neg = sum(1 for v in vecs if v.is_negative)
            extra = "，含 %d 负向" % n_neg if n_neg else ""
            extra += "，期望已手填 %d" % n_filled if n_filled else ""
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
        # RTL 补充逻辑按 Excel 路径持久化：换表自动恢复（同 suffix_override；spec 原样存）。
        # ★须在建 self.signals 前加载——signals 要用「应用补充后」的合成 logic 信号，左表/编辑器才同步显示补充。
        self._logic_overrides = {str(k).strip().lower(): dict(v) for k, v in
                                 _load_settings().get("logic_overrides", {}).get(path, {}).items()
                                 if str(k).strip() and isinstance(v, dict)}
        # 左表/编辑器用【应用 RTL 补充后】的 logic 信号(合成信号替换原行/纯新增追加)，与 build/report 同口径。
        self.signals = self._logic_signals() + list(self.wb.mux)
        # 探针前缀按 Excel 路径持久化：换表自动恢复上次配置（须在建 Resolver 前加载——wire 前缀要传进去）
        self._probe_prefixes = dict(_load_settings().get("probe_prefixes", {}).get(path, {}))
        self._force_signals = set(_load_settings().get("force_signals", {}).get(path, []))
        self._suffix_override = {str(k).strip().lower(): bool(v) for k, v in
                                 _load_settings().get("suffix_override", {}).get(path, {}).items()
                                 if str(k).strip()}
        # 解析画像：逐信号 try，一个坏信号不连累整体加载
        self._resolver = R.Resolver(self.wb, wire_prefixes=self._probe_prefixes,
                                    force_overrides=self._force_signals,
                                    cascade_mode=self._logic_cascade(),
                                    append_to_logic=self._append_to_logic_on(),
                                    append_to_mux=self._append_to_mux_on(),
                                    suffix_override=dict(self._suffix_override))
        self._analysis = {}
        # 切换工作簿，清空旧的测试项编辑状态
        self._edited = {}
        self._customized = set()
        self._neg_only = {}
        self._sig_cov = {}
        self._mux_expected = {}
        self._mux_neg = set()
        self._mux_data = {}
        self._mux_dropped = {}
        self._mux_cleared = set()
        self._mux_user_vecs = {}        # ★必须随换表清空：恢复用 .extend，不清会跨表叠加/泄漏到错的桶
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
        self.type_combo.blockSignals(True)
        self.type_combo.clear(); self.type_combo.addItem("全部 type"); self.type_combo.addItems(types)
        self.type_combo.blockSignals(False)
        # owner 下拉：全部 → （无 owner）[仅当真有空 owner 信号时] → 具体 owner 名
        n_no_owner = sum(1 for s in self.signals if not s.owner)
        self.owner_combo.blockSignals(True); self.owner_combo.clear()
        self.owner_combo.addItem("全部 owner")
        if n_no_owner:
            self.owner_combo.addItem("%s ×%d" % (NO_OWNER, n_no_owner))
        self.owner_combo.addItems(owners)
        self.owner_combo.blockSignals(False)

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
                _lab = STATUS_LABEL.get(st, st)
                if st == "needs-prefix" and self._include_risky_on():
                    _lab = "⚠缺前缀·已强制生成"   # 开「缺前缀强制生成」后如实反映：进 .sv，待仿真验证
                it = QtWidgets.QTableWidgetItem(_lab)
                if st != "clean":
                    # 橙=needs-prefix(要前缀否则跳过)；蓝=bare-probe(裸名已生成,信息)；其余故障红
                    it.setForeground(STATUS_FG.get(st, QtGui.QColor("red")))
                tip = STATUS_HELP.get(st, st)
                err = self._analysis.get(r, {}).get("error")
                if err and st in ("needs-prefix", "bare-probe", "unresolved", "parse-err",
                                  "false-green", "spec-collision"):
                    tip = "%s\n\n%s" % (tip, err)        # 缺前缀/坏掉/裸名提示时把后端 error 全文带上
                # 嵌套 mux 自动折叠：状态格加 ⚙ 标记 + tooltip 带全文，让 designer 一眼能复核
                nnote = getattr(sig, "normalized_note", "")
                if nnote:
                    it.setText("%s ⚙" % it.text())
                    tip = "%s\n\n⚙ %s" % (tip, nnote)
                it.setToolTip(tip)
                self.table.setItem(r, COL_STATUS, it)
                self.table.setItem(r, COL_PREFIX, self._prefix_cell(sig, self._analysis.get(r, {})))
                if getattr(sig, "_is_supplement", False):
                    # RTL 补充信号：表达式列加 ⚠[RTL补充] 前缀 + 信号名/表达式格琥珀底，左表一眼可见
                    self._set_text(r, COL_EXPR, "⚠[RTL补充] %s" % sig.expr)
                    _amber = QtGui.QColor("#fff7ed")
                    _snote = getattr(sig, "_supplement_note", "") or ""
                    _stip = ("⚠ 本信号逻辑为【RTL 补充】(Excel 真表缺此级 ECO，手工补)。\n"
                             "生成/预览/报告均按此补充式扫真值表，.sv 块顶带 // ⚠。"
                             + (("\n理由: %s" % _snote) if _snote else ""))
                    for _c in (COL_K, COL_EXPR):
                        _cell = self.table.item(r, _c)
                        if _cell is not None:
                            _cell.setBackground(_amber)
                            _cell.setToolTip(_stip)
                else:
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
            COL_PREFIX: "探针网名怎么来的：\n"
                        "  输出→U_BT_LP_PLL_DIG = 断言探针带层级前缀（信号在子模块里，点下方『设置探针前缀』）\n"
                        "  mon_active→U_BT_LP_PLL_DIG = 该输入的 force 路径带前缀\n"
                        "  探针口=d_en_refbuf_ls (level_shift) = 探针网名经 level_shift 页解析为该真网名"
                        "（随表自动生效、无需设置；前缀只按此真名匹配）\n"
                        "蓝色 = 已生效；鼠标悬停可看完整路径/来源。",
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
            if owner.startswith(NO_OWNER):           # 「（无 owner） ×N」：只看 owner 列留空的
                if sig.owner:
                    show = False
            elif owner != "全部 owner" and sig.owner != owner:
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
        # 批量勾选：挂起逐格存盘(否则每个 checkbox 触发一次 _persist_edits 文件写=全选时几百次)，循环后存一次
        prev = getattr(self, "_persist_suspended", False)
        self._persist_suspended = True
        try:
            for r in range(self.table.rowCount()):
                if not self.table.isRowHidden(r):
                    self.table.item(r, COL_SEL).setCheckState(
                        QtCore.Qt.Checked if checked else QtCore.Qt.Unchecked)
        finally:
            self._persist_suspended = prev
        self._persist_edits()

    def on_check_selected_rows(self):
        """把信号表里当前『选中的行』(框选/Ctrl/Shift)一次性勾上『选』——省去逐个点小复选框。"""
        rows = sorted({i.row() for i in self.table.selectedItems()})
        if not rows:
            self.status.showMessage("先在信号表里选中若干行(鼠标框选 / Ctrl·Shift 点)，再点『勾选选中行』")
            return
        prev = getattr(self, "_persist_suspended", False)
        self._persist_suspended = True
        try:
            for r in rows:
                cell = self.table.item(r, COL_SEL)
                if cell is not None:
                    cell.setCheckState(QtCore.Qt.Checked)
        finally:
            self._persist_suspended = prev
        self._persist_edits()
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
        if self._sig_loading or item is None:
            return
        if item.column() == COL_SEL:
            self._persist_edits()        # 勾选变化也存盘（第二十六轮：启动恢复信号勾选）
            return
        if item.column() != COL_NEG:
            return
        r = item.row()
        sig = self._sig_of_row(r)
        # mux 信号：负向不走编辑器 override（无表达式可编辑），勾选状态在生成时经 neg_signals 生效。
        # 记进 _mux_neg 并存盘——否则关 GUI 重开后 mux 负向勾选会丢(这正是用户碰到的 bug)。
        if isinstance(sig, excel_model.MuxGroup):
            name_low = sig.out_name.lower()
            want_mux = item.checkState() == QtCore.Qt.Checked
            if want_mux:
                self._mux_neg.add(name_low)
                self._persist_edits()
                if getattr(self, "_ti_mux_sig", None) is sig:   # 正看该信号 → 重渲编辑器把负向列显示出来
                    self._load_mux_test_items(sig)
                self.status.showMessage("%s（mux）已标记负向——真值表追加 1 条故意填错的自检列(_NEG)，生成 .sv 时同步"
                                        % sig.out_name)
                return
            # 取消勾选 = 本信号不要负向：清整信号标记 + 删用户手编负向列(有手编负向先确认防误删)。
            user_negs = [v for v in self._mux_user_vecs.get(name_low, []) if v.is_negative]
            if user_negs and QtWidgets.QMessageBox.question(
                    self, "确认取消负向",
                    "%s 有 %d 条手编负向列，取消勾选会一并删除。确定？" % (sig.out_name, len(user_negs))
                    ) != QtWidgets.QMessageBox.Yes:
                self._set_left_neg_check(sig, True)        # 用户放弃 → 因仍有负向，勾选恢复
                return
            self._mux_neg.discard(name_low)
            if user_negs:
                kept = [v for v in self._mux_user_vecs.get(name_low, []) if not v.is_negative]
                if kept:
                    self._mux_user_vecs[name_low] = kept
                else:
                    self._mux_user_vecs.pop(name_low, None)
            if getattr(self, "_ti_mux_sig", None) is sig:   # 正看该信号 → 重渲编辑器去掉负向列
                self._load_mux_test_items(sig)
            self._persist_edits()
            self.status.showMessage("%s（mux）已清除负向%s"
                                    % (sig.out_name,
                                       "(含 %d 条手编负向列)" % len(user_negs) if user_negs else ""))
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
        # 全名(无视尾缀开关)也试一遍：尾缀开关关时 rtl_base 退裸名，scan_rtl/用户按 _to_logic 全名
        # 配的前缀才不会静默失配(与 generator.probe_prefix_for 同口径，2026-06-11 Hi1108 rxiq 实证)。
        rb_full = getattr(sig, "rtl_base_full", sig.rtl_base)
        rn_full = getattr(sig, "rtl_name_full", sig.rtl_name)
        rtl_keys = (p.get(sig.rtl_name.lower()) or p.get(sig.rtl_base.lower())
                    or p.get(rn_full.lower()) or p.get(rb_full.lower()) or "")
        # 走 level_shift 的输出：前缀只认 _ls 真网名，不认 K 列裸名（按裸名配的指向移位前/消费侧那根网，
        # 对这根 _ls 网无效）。与 generator.probe_prefix_for 同口径（2026-06-12 pll_n/datapath_clk_en）。
        if getattr(sig, "_ls_name", None):
            return rtl_keys
        return p.get(sig.out_name.lower()) or p.get(sig.out_base.lower()) or rtl_keys

    def _prefix_cell(self, sig, analysis):
        """探针前缀列：探针网名怎么来的——① 经 level_shift 页定到顶层 _ls 口(自动、无需配置)
        ② 配了层级前缀的输出/受前缀影响的输入(force 路径带前缀)。

        让用户一眼看到"探针网名解析在哪生效"。非空时蓝色高亮；tooltip 给完整路径/来源。
        """
        parts, tips = [], []
        ls = getattr(sig, "_ls_name", None)
        if ls:
            parts.append("探针口=%s (level_shift)" % sig.rtl_name)
            tips.append("探针网名经『level_shift 页』解析为 %s（随表自动生效，无需设置；"
                        "不受『logic 加尾缀』开关影响）。\n层级前缀只按此真网名匹配——按电平移位前的"
                        "裸名配的前缀指向移位前/消费侧那根网、对这根无效，会被忽略。" % sig.rtl_name)
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
            it.setForeground(QtGui.QColor("#0a58c4"))    # 蓝 = 探针网名解析已生效（区别于红=故障）
            it.setToolTip("探针网名解析：\n" + "\n".join(tips))
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
            "支持多种写法（可混用）。信号多时推荐【合并格式】——路径只写一次：\n"
            "    U_BT_LP_PLL_DIG:                  ← 『层级路径:』单独一行\n"
            "        pll_n, mon_active             ← 其下信号名逗号/空格分隔（每行一个也可以）\n"
            "    U_BT_LP_PLL_DIG.DIG_1:\n"
            "        xxx\n"
            "扁平写法仍可用：pll_n=U_BT_LP_PLL_DIG（每行一条 信号名=路径）。\n"
            "被验证输出 → assert 探针带层级；force 输入 wire → force 路径带层级。\n"
            "删除行 = 清除映射；# 开头 = 注释。")
        lay.addWidget(hint)
        edit = QtWidgets.QPlainTextEdit()
        edit.setPlainText(generator.render_probe_prefix_grouped(self._probe_prefixes))
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
            edit.setPlainText(generator.render_probe_prefix_grouped(merged))

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

    def on_export_nets(self):
        """导出 nets.txt：当前表需要在 ENV_RF 层级定位的网清单，供仿真服务器跑 scan_rtl 扫 RTL。

        这是跨机器两段式工作流（Excel 在 Windows、RTL 在 Linux）的第①步——以前要落到命令行
        跑 `python scan_rtl.py --excel 真表.xlsx --export-nets nets.txt`，现在直接在 GUI 出。
        网清单取 logic 探针 + 两种级联模式的 force 输入 + mux 三类网 + dft iddq 门网的并集
        （与 scan_rtl._load_excel_nets 同口径，一次扫覆盖 cone/force 两模式，宁多勿漏）。
        """
        if not self.wb:
            QtWidgets.QMessageBox.information(self, "无表", "先加载一张 Excel 真表，再导出 nets.txt。")
            return
        from dreg_verify import rtl_scan
        try:
            nets = rtl_scan.collect_excel_nets(self.wb)
            n_logic = len(nets)
            mux_nets = rtl_scan.collect_mux_nets(self.wb)
            for name, why in mux_nets.items():
                nets.setdefault(name, why)
            dft_nets = rtl_scan.collect_dft_nets(self.wb)
            for name, why in dft_nets.items():
                nets.setdefault(name, why)
        except Exception as ex:  # noqa: BLE001
            QtWidgets.QMessageBox.critical(self, "导出失败", "收集网清单出错：\n%s" % ex)
            return
        finally:
            # collect_excel_nets 会把 logic 信号的 _append_to_logic 强设为 True(找 RTL 真名)，
            # 内部又建临时 Resolver 重盖所有信号的尾缀标记——重建本 GUI 的 Resolver 还原成当前设置，
            # 否则后续左表/预览会按被污染的尾缀标记显示。
            self._reanalyze_all()

        base = os.path.dirname(self.path_edit.text().strip()) or os.getcwd()
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "导出 nets.txt（信号清单，传服务器跑 scan_rtl）",
            os.path.join(base, "nets.txt"), "信号清单 (*.txt);;全部文件 (*)")
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(rtl_scan.render_nets_text(nets))
        except OSError as ex:
            QtWidgets.QMessageBox.critical(self, "导出失败", str(ex))
            return
        QtWidgets.QMessageBox.information(
            self, "已导出 nets.txt",
            "共 %d 个网已写出（logic %d · mux %d · dft %d，去重并集）：\n%s\n\n"
            "下一步（跨机器两段式）：\n"
            "  ① 把 scan_rtl.py + 这个 nets.txt 一起传到仿真服务器\n"
            "  ② source dreg 环境后跑：python3 scan_rtl.py\n"
            "  ③ 把生成的 probe_prefixes.txt 拷回 → 『设置探针前缀 → 导入…』套用"
            % (len(nets), n_logic, len(mux_nets), len(dft_nets), path))

    def _save_force_signals(self):
        """强制 force 基名按 Excel 路径写入 settings（pytest 下 no-op，与其它持久化策略一致）。"""
        st = _load_settings()
        all_maps = st.get("force_signals", {})
        path = self.path_edit.text().strip()
        if self._force_signals:
            all_maps[path] = sorted(self._force_signals)
        else:
            all_maps.pop(path, None)
        st["force_signals"] = all_maps
        _save_settings(st)

    def on_set_force_signals(self):
        """强制 force 信号编辑器：每行一个基名，列进来的信号直接 force 顶层基名网、跳过 cone 展开。

        用于覆盖工具自动判断（如撞名 RO 寄存器的内部信号），等价 CLI 的 --force-signals、
        也等价 for_test 的 force `ENV_RF.<基名>。cone 成环时工具已自动回退，这里供手动指定别的信号。
        """
        if not self.wb:
            return
        dlg = QtWidgets.QDialog(self)
        dlg.setWindowTitle("强制 force 信号（跳过 cone，直接 force 顶层基名）")
        lay = QtWidgets.QVBoxLayout(dlg)
        lay.addWidget(QtWidgets.QLabel(
            "每行一个信号基名（去 _to_logic/_to_mux 后缀、去位宽）。列进来的信号：\n"
            "    · 直接 force 顶层基名网 `ENV_RF.<基名>（= for_test 那招），跳过 cone 展开；\n"
            "    · 适合撞名 RO 寄存器的内部信号（如 d_wl_rf_linectrl_band_sel）。\n"
            "前提：该基名在 ENV_RF 顶层真实存在（tmm/regmap 里有这个寄存器/网），否则仿真会 CUVUNF。\n"
            "留空 = 清除。cone 成环时工具已会自动回退到 force，这里是手动覆盖别的信号。"))
        edit = QtWidgets.QPlainTextEdit()
        edit.setPlainText("\n".join(sorted(self._force_signals)))
        self._mono(edit)
        lay.addWidget(edit)
        bb = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok
                                        | QtWidgets.QDialogButtonBox.Cancel)
        bb.accepted.connect(dlg.accept); bb.rejected.connect(dlg.reject)
        lay.addWidget(bb)
        dlg.resize(560, 380)
        if dlg.exec() != QtWidgets.QDialog.Accepted:
            return
        names = set()
        for line in edit.toPlainText().splitlines():
            s = line.split("#", 1)[0].strip().lower()    # # 开头/行尾 = 注释
            if s:
                names.add(s)
        self._force_signals = names
        self._save_force_signals()
        self._reanalyze_all()
        self.status.showMessage("强制 force 信号已更新（共 %d 个）——这些信号跳过 cone、直接 force 顶层基名"
                                "（状态列应变 clean/需前缀）" % len(names))

    def _supplement_template(self):
        """为【当前编辑器里的 logic 信号】生成一份补充 spec 模板：预填原表达式+原输入映射，
        用户/Claude 只需把 ECO 级包在外面、加新输入。无选中信号 → 通用空模板。"""
        sig = getattr(self, "_ti_sig", None)
        if sig is not None and not isinstance(sig, excel_model.MuxGroup):
            name = sig.out_base.lower()
            inputs = [{"var": k, "raw": info.get("raw", "")}
                      for k, info in sig.inputs.items()]
            return {name: {
                "_提示": "把 ECO 级(如 2:1 mux/二级 iddq)包在原表达式外面，并在 inputs 里加新输入；理由填 note",
                "enabled": True,
                "note": "（填理由：SE 说 RTL 顶层口后多了什么级）",
                "expr": sig.expr,
                "inputs": inputs,
            }}
        return {"<信号基名>": {
            "_提示": "var=表达式里的变量名(大小写无关)，raw=真实网名(可带[msb:lsb])；理由填 note",
            "enabled": True, "note": "（理由）",
            "expr": "ECO_IDDQ ? 1'b0 : (VCO_FC_SEL ? VCO_EN_FASTON : (EN & ~IDDQ))",
            "inputs": [{"var": "EN", "raw": "<原使能寄存器>"},
                       {"var": "IDDQ", "raw": "iddq"},
                       {"var": "VCO_FC_SEL", "raw": "d_vco_fc_sel_ls[0]"},
                       {"var": "VCO_EN_FASTON", "raw": "d_vco_en_faston"},
                       {"var": "ECO_IDDQ", "raw": "<ECO 二级 iddq 网>"}]}}

    def _validate_supplements(self, data):
        """校验 {信号: spec} 映射：返回 (规范化后的 dict, [错误串])。错误非空时不应保存。"""
        if not isinstance(data, dict):
            return {}, ["顶层必须是 JSON 对象 {信号基名: {expr, inputs, ...}}"]
        out, errs = {}, []
        for raw_name, spec in data.items():
            name = str(raw_name).strip().lower()
            if not name or name.startswith("<"):
                errs.append("信号名 %r 无效(占位符未替换?)" % raw_name); continue
            if not isinstance(spec, dict):
                errs.append("%s: spec 必须是对象" % name); continue
            expr = str(spec.get("expr", "") or "").strip()
            if not expr:
                errs.append("%s: 缺 expr" % name); continue
            ins = spec.get("inputs")
            if not isinstance(ins, list) or not ins:
                errs.append("%s: inputs 应为非空列表 [{var,raw},...]" % name); continue
            try:
                node = E.parse(expr)
            except Exception as ex:  # noqa: BLE001
                errs.append("%s: 表达式解析失败 — %s" % (name, ex)); continue
            try:
                sigobj = generator.make_supplement_signal(name, spec, None)
            except Exception as ex:  # noqa: BLE001
                errs.append("%s: inputs 解析失败 — %s" % (name, ex)); continue
            missing = set(E.collect_vars(node)) - set(sigobj.inputs.keys())
            if missing:
                errs.append("%s: 表达式用到的变量没有 input 映射: %s"
                            % (name, ", ".join(sorted(missing))))
                continue
            out[name] = spec
        return out, errs

    def on_logic_overrides(self):
        """RTL 补充逻辑编辑器：Excel 真表缺某信号 ECO 级时，手工补一条等价 logic 式扫真值表。

        用 JSON 直接编辑/粘贴(主路径=把 SE 给的 RTL 发给 Claude→Claude 写 spec→这里粘贴)；
        校验通过后存盘并随配置导入导出。合成块顶强制 // ⚠，偏离纯 Excel 推导供 SE 复核。
        """
        if not self.wb:
            QtWidgets.QMessageBox.information(self, "提示", "请先加载 Excel")
            return
        dlg = QtWidgets.QDialog(self)
        dlg.setWindowTitle("RTL 补充逻辑（Excel 真表缺级时手工补等价式扫真值表）")
        lay = QtWidgets.QVBoxLayout(dlg)
        lay.addWidget(QtWidgets.QLabel(
            "用途：Excel 真表丢了某信号顶层口后的 ECO 级(如 d_en_vco_fc：SE 确认接了 2:1 mux + 二级 iddq、\n"
            "真表只到 DREG、缺的控制网悬空→X)。在这里补一条【等价 logic 表达式】，工具当合成 logic 行扫真值表，\n"
            "ECO 新输入(选择位/faston/二级 iddq)自动成为真值表新维度；合成块顶强制标 // ⚠ 供 SE 复核。\n"
            "格式 {信号基名: {enabled, expr, inputs:[{var,raw}], note}}：var=表达式里变量名(大小写无关)，\n"
            "raw=真实网名(可带[msb:lsb])。enabled=false 临时停用。下面直接编辑/粘贴 Claude 写好的 JSON。\n"
            "（补充在【生成/.sv 预览/报告】里生效并扫真值表；左表与右侧测试项编辑器仍显示 Excel 原逻辑。）"))
        edit = QtWidgets.QPlainTextEdit()
        cur = {k: dict(v) for k, v in self._logic_overrides.items()}
        edit.setPlainText(json.dumps(cur, ensure_ascii=False, indent=2) if cur else "")
        edit.setPlaceholderText("（空 = 没有补充。点「插入模板」或粘贴 Claude 写好的 JSON）")
        self._mono(edit)
        lay.addWidget(edit)
        row = QtWidgets.QHBoxLayout()
        b_tmpl = QtWidgets.QPushButton("插入模板(当前信号)")
        b_tmpl.setToolTip("把当前编辑器选中信号的原表达式+原输入预填进来，你/Claude 在外面包 ECO 级")
        b_file = QtWidgets.QPushButton("从文件导入…")

        def _insert_tmpl():
            tmpl = self._supplement_template()
            txt = edit.toPlainText().strip()
            if not txt:
                edit.setPlainText(json.dumps(tmpl, ensure_ascii=False, indent=2))
            else:
                try:
                    cur2 = json.loads(txt)
                    if isinstance(cur2, dict):
                        cur2.update(tmpl)
                        edit.setPlainText(json.dumps(cur2, ensure_ascii=False, indent=2))
                except ValueError:
                    QtWidgets.QMessageBox.warning(dlg, "提示", "当前内容不是合法 JSON，无法合并模板；请先修好或清空。")

        def _from_file():
            fp, _ = QtWidgets.QFileDialog.getOpenFileName(dlg, "导入补充 JSON", "",
                                                          "JSON (*.json);;全部文件 (*)")
            if not fp:
                return
            try:
                with open(fp, encoding="utf-8") as f:
                    edit.setPlainText(f.read())
            except OSError as ex:
                QtWidgets.QMessageBox.critical(dlg, "读取失败", str(ex))
        b_tmpl.clicked.connect(_insert_tmpl)
        b_file.clicked.connect(_from_file)
        row.addWidget(b_tmpl); row.addWidget(b_file); row.addStretch(1)
        lay.addLayout(row)
        bb = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok
                                        | QtWidgets.QDialogButtonBox.Cancel)
        bb.accepted.connect(dlg.accept); bb.rejected.connect(dlg.reject)
        lay.addWidget(bb)
        dlg.resize(720, 560)
        if dlg.exec() != QtWidgets.QDialog.Accepted:
            return
        txt = edit.toPlainText().strip()
        if not txt:
            self._logic_overrides = {}
            self._save_logic_overrides()
            self._refresh_after_logic_overrides()
            self.status.showMessage("RTL 补充逻辑已清空")
            return
        try:
            data = json.loads(txt)
        except ValueError as ex:
            QtWidgets.QMessageBox.critical(self, "JSON 解析失败",
                                           "不是合法 JSON：\n%s" % ex)
            return
        norm, errs = self._validate_supplements(data)
        if errs:
            QtWidgets.QMessageBox.critical(
                self, "补充逻辑有误（未保存）",
                "请修正后重试：\n\n" + "\n".join("· %s" % e for e in errs[:20]))
            return
        # 文件里有、当前表里找不到基名的补充：允许(纯新增信号也是合法用法)，但提示一下
        have = {s.out_base.lower() for s in self.wb.logic}
        unknown = [n for n in norm if n not in have]
        self._logic_overrides = norm
        self._save_logic_overrides()
        self._refresh_after_logic_overrides()
        n_on = sum(1 for v in norm.values() if v.get("enabled", True) is not False)
        msg = "已保存 %d 条 RTL 补充逻辑（%d 条启用）。生成/预览/报告时按补充式扫真值表，块顶带 // ⚠。" \
              % (len(norm), n_on)
        if unknown:
            msg += "\n\n注意：以下基名不在当前 Excel logic 页(将作为【纯新增】合成信号生成)：\n" + \
                   "\n".join("  %s" % n for n in unknown[:20])
        QtWidgets.QMessageBox.information(self, "已保存", msg)
        self.status.showMessage("RTL 补充逻辑已更新（共 %d 条）" % len(norm))

    def _refresh_after_logic_overrides(self):
        """RTL 补充变更后即时刷新：重建信号列表(合成替换原行/纯新增追加) → 重析 → 重建左表 →
        刷新编辑器真值表/预览。这样『加进去补充后 GUI 真值表不刷新』就被治掉了。"""
        if not self.wb:
            return
        cur_name = (self._ti_sig.out_name
                    if getattr(self, "_ti_sig", None) is not None else None)
        self.signals = self._logic_signals() + list(self.wb.mux)
        # 编辑器原指向的对象可能已被合成对象替换 → 按名重新指向新对象，否则刷新后仍显示旧逻辑
        if cur_name is not None:
            self._ti_sig = next((s for s in self.signals if s.out_name == cur_name), None)
        self._analysis = {}
        self._reanalyze_all()          # 重建 resolver + 全表重析 + _populate_table + 重载 logic 编辑器 + 刷新预览
        if getattr(self, "_ti_mux_sig", None) is not None:
            self._load_mux_test_items(self._ti_mux_sig)

    def _analyze_one(self, sig):
        """单信号解析画像：logic 走 analyze_signal，mux 组走 analyze_mux_group（2026-06-03 第九轮）。"""
        self._resolver.cascade_mode = self._cascade_for(sig)   # 级联模式 logic/mux/单点（与 build/report 同口径）
        if isinstance(sig, excel_model.MuxGroup):
            # 传全部已配置探针前缀（不只本信号的）——级联衔接网的前缀也要能命中，
            # mux_prefix_risks 才能正确区分"还缺前缀"和"已配好可生成"
            # 带上手填数据值(B2)——否则左表状态用自动值算，与右侧编辑器/生成结果不一致(审查 #9)
            opts = generator.GenOptions(probe_prefixes=self._probe_prefixes,
                                        mux_data={k: dict(v) for k, v in self._mux_data.items()},
                                        include_risky=self._include_risky_on())
            return generator.analyze_mux_group(
                self._resolver, self.wb, sig,
                mode=self._sig_cov_collapsed(sig),     # 单点优先，与右侧编辑器/生成同口径
                probe_prefix=self._prefix_of(sig), opts=opts)
        return generator.analyze_signal(self._resolver, sig, wb=self.wb,
                                        probe_prefix=self._prefix_of(sig))

    def _reanalyze_all(self):
        """探针前缀/级联模式变更后重建 Resolver（两者都影响所有信号的输入解析）并刷新全表。"""
        self._resolver = R.Resolver(self.wb, wire_prefixes=self._probe_prefixes,
                                    force_overrides=self._force_signals,
                                    cascade_mode=self._logic_cascade(),
                                    append_to_logic=self._append_to_logic_on(),
                                    append_to_mux=self._append_to_mux_on(),
                                    suffix_override=dict(self._suffix_override))
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

    def _append_negatives(self, pos_rows, which, src_rows):
        """在 pos_rows 之上按 first/all 追加正向的"故意填错"负向副本，继承 src_rows 里同源
        负向(按输入取值键)的改名/手填错值(不静默丢失)。返回 [正向… + 负向…]（未 recompute）。"""
        old_negs = {}
        for rd in src_rows:
            if rd.get("kind") == "neg":
                old_negs.setdefault(tuple(sorted(rd["base_values"].items())), []).append(rd)
        new_rows = list(pos_rows)                      # 正向测试原样保留
        targets = pos_rows if which == "all" else pos_rows[:1]
        for prd in targets:                            # 每个(或首个)正向 → 追加一条负向副本
            neg = {"base_values": dict(prd["base_values"]),
                   "kind": "neg", "wrong_value": None, "user_added": True,
                   "note": "负向(真实测试的故意填错副本)"}
            bucket = old_negs.get(tuple(sorted(prd["base_values"].items())))
            if bucket:                                 # 继承同源旧负向的自定义名与手填错值
                old = bucket.pop(0)
                if old.get("name") is not None:
                    neg["name"] = old["name"]
                if old.get("wrong_value") is not None:
                    neg["wrong_value"] = old["wrong_value"]
            new_rows.append(neg)
        return new_rows

    def _neg_only_rows_now(self, sig, name_low):
        """neg_only 信号(正向全自动、仅加了负向)在【build 时】按当前全局覆盖度重算正向，
        再按记住的 first/all 规则补回负向 → recompute 后的 rows。

        设计哲学(用户反馈)：纯加负向不该把信号冻结成自定义——全局『精简/全面/穷举』对它
        依然生效。neg_only 信号必无手改正向(任何手改都会把它移出 neg_only 变冻结)，故负向
        都是自动取反副本、重算无损。_load_test_items(GUI 显示侧)早已 reflow，本函数是 build
        /report/导出侧的对应补丁，闭合『加负向后切覆盖度对它失效』的缺口。"""
        node, bindings, groups, _chain, _err = self._expand_sig(sig)
        if node is None:
            return None
        pos_rows = self._auto_rows(sig, node, bindings, groups)   # 当前覆盖度的正向
        if not pos_rows:
            return []
        which = self._neg_only.get(name_low, "first")
        src = self._edited.get(name_low, {}).get("rows", [])      # 继承旧负向改名/错值(neg_only 通常无)
        new_rows = self._append_negatives(pos_rows, which, src)
        for rd in new_rows:
            self._recompute_row(node, bindings, groups, sig.out_width, rd)
        return new_rows

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
        # neg_only 信号(正向全自动、只加了负向)：正向永远取【当前覆盖度】的新 auto 行，不要用
        # _edited 里冻结的旧行——否则它的正向被钉死在"加负向时"的档，切到它看着不跟全局(用户实测 bug)。
        # 仅手工编辑过测试项的信号(hand_edited)才沿用 _edited 缓存(保住编辑)。
        use_cached = name_low in self._edited and name_low not in self._neg_only
        rows = (self._edited[name_low]["rows"] if use_cached
                else self._auto_rows(sig, node, bindings, groups))
        pos_rows = [rd for rd in rows if rd.get("kind") != "neg"]
        if not pos_rows:
            return
        new_rows = (self._append_negatives(pos_rows, which, rows) if want_neg
                    else list(pos_rows))               # 继承旧负向改名/错值见 _append_negatives
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
                if isinstance(sig, excel_model.MuxGroup):     # mux 负向走 _mux_neg(存盘)，不进 _edited
                    if want:
                        self._mux_neg.add(name_low)
                    else:
                        self._mux_neg.discard(name_low)
                elif want:
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
    # 选 mux 信号时若干列编辑按钮的 tooltip 换成 mux 语义说明（第二十八轮 mux 与 logic 平级，全部可用，
    # 但 mux 的"加列/复制/负向"是按 case 走、不是按表达式输入，故 tooltip 单独说明；选 logic 恢复原文）。
    _MUX_BTN_TIPS = {
        "加正向列": "mux：基于当前选中列的 case 新增一条【用户列】(双击数据行改本列取值，auto_out 随路由源重算；可改名)",
        "复制列": "mux：把当前列整列克隆成一条用户列(数据值/期望一并带走，可改名)",
        "重命名列…": "mux：给你新增的【用户列】改名(自动生成列名不可改，与 logic 的 T0/T1 同规则)",
        "加负向(选中)": "mux：给选中列各加一条负向【用户列】(错值=路由源取反，显示为 _NEG 列)",
        "全部用例加负向": "mux：给每条正向列(自动+用户)各加一条负向用户列",
        "删负向": "mux：删掉本信号全部负向用户列 + 清整信号负向标记(左表)",
        "导出CSV": "把本 mux 信号测试项导出为真值表 CSV(Excel 可开)",
    }

    def _set_ti_buttons_for_mux(self, is_mux):
        """第二十八轮起 mux 与 logic 平级：全部列编辑按钮都有 mux 分支 → mux 选中时【全部可用】，
        只把"加列/复制/负向"等的 tooltip 换成 mux 语义说明；选 logic/无信号恢复原 tooltip。"""
        btns = getattr(self, "_ti_btns", None)
        if not btns:
            return
        for t, b in btns.items():
            b.setEnabled(True)
            if is_mux and t in self._MUX_BTN_TIPS:
                b.setToolTip(self._MUX_BTN_TIPS[t])
            else:
                b.setToolTip(self._ti_btn_tips.get(t, b.toolTip()))

    def _clear_test_items(self, header_text):
        self._ti_sig = None; self._ti_node = None
        self._ti_bindings = {}; self._ti_groups = []; self._ti_rows = []
        self._ti_cone = False
        self._ti_chain = []
        self._ti_name_low = None
        self._ti_hl_col = -1
        self._ti_mux_vecs = []; self._ti_mux_exp_row = -1   # mux 期望编辑状态一并清(防陈旧引用)
        self._ti_mux_user_start = 1 << 30                   # 用户列起始(无信号=无用户列)
        self._ti_mux_user_end = 1 << 30                     # 用户列结束=全局负向起始
        self._ti_mux_exp = None                             # mux 展开缓存(导出CSV/用户向量复用)
        self._ti_dft_pin = None; self._ti_mux_dft_pin = None   # DFT 门输入行状态(防跨信号陈旧)
        self._ti_mux_disp = []; self._ti_gate_row = None       # 输入行显示次序(for_test 对齐)
        self._set_ti_buttons_for_mux(False)                 # 无信号：列编辑按钮恢复可用(原行为)
        self._set_sig_cov_combo(None)                       # 无信号：单点覆盖下拉禁用并复位
        self._set_suffix_chk(None)                          # 无信号：探尾缀网勾选禁用
        self._set_sig_cascade_combo(None)                   # 无信号：本信号级联下拉禁用
        self.ti_header.setStyleSheet("color:#445;")         # 复位 RTL 补充琥珀横幅
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
        # iddq 门=横向输入行（2026-06-10，与 mux 侧同口径）：受门控的 logic 输出真值表
        # 多一行门网、每条测试取透传值（generator.pin_dft_gate 在 build/report 实际驱动）。
        # 输入行次序按 designer for_test 同组行序排（门也参与；无 for_test=原序+门殿后）。
        # 门若已是本信号显式输入(RTL 补充列了该 iddq / logic 行引用它)→ 不重复出门行（与 build/report 去重同口径）。
        _ti_ibases = {b.base.lower() for b in bindings.values()
                      if b is not None and getattr(b, "base", None)}
        self._ti_dft_pin = self._dft_pin_display(sig.out_base, input_bases=_ti_ibases)
        _pin = self._ti_dft_pin

        def _lgbind(e):
            return bindings.get(e[1].get("rep")) if e[0] == "g" else None
        _entries = [("g", g) for g in groups] + ([("gate", None)] if _pin else [])
        _entries = generator.fortest_order_entries(
            _entries, self.wb, sig.out_base,
            lambda e: ((excel_model._strip_width(e[1].get("base") or e[1].get("label") or "")[0]
                        .lower()) if e[0] == "g" else _pin[0].lower()),
            key_fn=lambda e: ((_pin[2], _pin[3]) if e[0] == "gate"
                              else ((_lgbind(e).address, _lgbind(e).reg_lsb)
                                    if _lgbind(e) is not None else (None, None))))
        groups = [e[1] for e in _entries if e[0] == "g"]
        self._ti_gate_row = next((i for i, e in enumerate(_entries) if e[0] == "gate"), None)
        self._set_ti_buttons_for_mux(False)   # logic 信号：列编辑按钮全可用
        self._set_sig_cov_combo(sig)          # 单点覆盖下拉置到本信号档（不触发重算）
        self._set_suffix_chk(sig)             # 探尾缀网勾选置到本信号实际状态
        self._set_sig_cascade_combo(sig)      # 本信号级联下拉置到本信号档
        self._ti_bindings = bindings; self._ti_groups = groups
        self._ti_cone = bool(chain)       # 头部/输入表据此标注"已展开上游"
        self._ti_chain = chain            # 展开链(本行+逐层代入的上游行)，cone 信号显示
        self._ti_name_low = name_low
        # neg_only 信号(正向全自动、只加了负向)：每次加载都按【当前全局覆盖度】重算正向 + 按原规则
        # 补回负向，刷新 _edited 缓存。否则切到它会显示"加负向时"那档的冻结正向、不跟全局(用户实测 bug)。
        # 重算是确定性的(由覆盖度决定)，故抑制此处的逐次存盘(每点一个信号写一次盘没必要)。
        # ★不变式(保证 reflow 不丢用户负向)：一旦给负向【命名/手填错值/精挑加负向】，都会经
        #   _ti_mark_customized 把信号【移出 _neg_only】→变冻结(hand_edited)→走下面 _edited 缓存分支、
        #   不再 reflow。故还留在 _neg_only 的信号其负向必为自动取反(无名/无手填值)，重算只是确定性再生。
        if name_low in self._neg_only:
            prev_susp = getattr(self, "_persist_suspended", False)
            self._persist_suspended = True
            try:
                self._set_signal_negatives(sig, True, self._neg_only[name_low])
            finally:
                self._persist_suspended = prev_susp
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
        self._set_sig_cov_combo(grp)          # 单点覆盖下拉置到本信号档（不触发重算）
        self._set_suffix_chk(grp)             # 探尾缀网勾选置到本信号实际状态
        self._set_sig_cascade_combo(grp)      # 本信号级联下拉置到本信号档
        self._set_ti_buttons_for_mux(True)    # mux 信号：置灰列结构/CSV 按钮(无 mux 分支)，给准确 tooltip
        exp = mux_gen.expand_mux_group(self.wb, self._resolver, grp)
        self._ti_mux_exp = exp                # 缓存：导出CSV/用户向量编辑复用(避免重复展开)
        # 『输入信号』表：解析有问题也照样填（哪个输入坏了一眼看到）——修"mux 点开输入信号框空白"
        self._populate_mux_inputs(grp, exp)
        if exp["issues"]:
            self.ti_header.setText("mux 信号 %s：无法生成测试 — %s"
                                   % (grp.out_name, "；".join(exp["issues"])))
            return
        mux_mode = self._sig_cov_collapsed(grp)                # 单点优先，否则跟随全局 mux 档
        data_ov = self._mux_data.get(grp.out_name.lower())     # B2 用户手填数据值（按物理基名）
        vecs, meta = mux_gen.make_mux_vectors(grp, exp, mode=mux_mode,
                                              max_tests=self.max_tests.value(),
                                              data_overrides=data_ov)
        name_low = grp.out_name.lower()
        # 用户「一键清空」该 mux 信号（第二十六轮）→ 零用例：优先于其它"无向量"提示，表保持空。
        if name_low in self._mux_cleared:
            self._ti_mux_vecs = []
            self.ti_header.setText("mux 信号 %s：已清空(零用例，本信号不产出测试)。点「重新生成」按当前覆盖度恢复默认。"
                                   % grp.out_name)
            self._update_cov_hint()
            return
        # 用户删掉的【个别 mux 测试列】：按签名过滤（与 build/report 同口径 mux_assign_key）。
        dropped = self._mux_dropped.get(name_low)
        if dropped:
            vecs = [v for v in vecs if generator.mux_assign_key(v.assignments) not in dropped]
        if meta.get("value_collision"):
            self.ti_header.setText(
                "mux 信号 %s：⚠字段太窄·假绿 —— 结构解析通了，但数据寄存器字段装不下 %d 条 case 的"
                "互异值，硬生成是『接错路也 PASS』的假测试，故跳过。要验得加宽字段或拆组（设计层）。"
                "上方『输入信号』表可看各数据寄存器的实际字段位宽。" % (grp.out_name, len(grp.cases)))
            return
        if not vecs:
            if dropped:
                self.ti_header.setText("mux 信号 %s：已删除全部测试列(零用例)。点「重新生成」按当前覆盖度恢复默认。"
                                       % grp.out_name)
            else:
                self.ti_header.setText("mux 信号 %s：控制信号没有可用的驱动路径，无法生成测试向量（见左表状态列）"
                                       % grp.out_name)
            return
        # 已手填的期望按输入取值键回填到向量（与生成/报告同一逻辑）
        generator.apply_mux_expected(vecs, self._mux_expected.get(grp.out_name.lower()))
        used = exp["used_vars"]
        # 追加用户手编/复制的测试列（第二十八轮，mux 与 logic 平级）：放自动生成列之后，
        # 列号 >= gen_n = 用户列(可编辑本列数据/改名)。正向用户列按 case_index 重算 auto_out=路由源值
        # (与当前展开一致)；负向用户列原样保留(改错值)。
        gen_n = len(vecs)
        for uv in self._mux_user_vecs.get(name_low, []):
            if not uv.is_negative:
                self._recompute_mux_user_exp(uv, grp, exp)
            vecs.append(uv)
        self._ti_mux_user_start = gen_n
        self._ti_mux_user_end = len(vecs)
        # 左表勾「负向」→ 与 build/report/CSV 同口径补 1 条全局负向(which=first)，编辑器真值表也显示出来
        # （此前只在生成/导出/报告时追加，编辑器看不到 → 用户以为「负向没生成」。这是用户报的 bug）。
        # 显示为只读参考列(琥珀)：生成时随覆盖度重算、默认错值=正确值取反；要自定义错值/列名走「逐case负向」。
        # 与用户手编负向重叠时由 _dedup_negatives 收敛(与 build 一致)。
        if name_low in self._mux_neg and vecs:
            vecs = V.add_negatives(vecs, mode="invert", which="first")
            vecs = generator._dedup_negatives(vecs)
            for gi in range(self._ti_mux_user_end, len(vecs)):
                vecs[gi].index = gi          # 顺位标号(仅列头 T<n>_NEG；对象是新建负向，安全可改)
        self._ti_mux_vecs = vecs
        # iddq 门=横向输入行（2026-06-10 用户定稿）：受门控的输出，真值表输入区多一行
        # 门网，每条测试取透传值（.sv 每条向量显式 force，generator.pin_dft_gate 同口径）。
        # 输入行次序按 designer for_test 同组行序排（门也参与；无 for_test=原序+门殿后）；
        # auto_out/期望 行号统一经 _ti_mux_exp_row 透出给各处理器。
        self._ti_mux_dft_pin = self._dft_pin_display(grp.out_base)
        pin = self._ti_mux_dft_pin
        disp = [("key", k) for k in used] + ([("gate", None)] if pin else [])
        self._ti_mux_disp = generator.fortest_order_entries(
            disp, self.wb, grp.out_base,
            lambda e: ((exp["bindings"][e[1]].base or "").lower() if e[0] == "key"
                       else pin[0].lower()),
            key_fn=lambda e: ((exp["bindings"][e[1]].address, exp["bindings"][e[1]].reg_lsb)
                              if e[0] == "key" else (pin[2], pin[3])))
        n_in = len(self._ti_mux_disp)
        self._ti_mux_exp_row = n_in + 1
        self._update_mux_header(grp, vecs, mux_mode, meta)
        if meta.get("override_collision"):     # B2：手填值撞值（非阻断，用户负责）
            self.ti_header.setText(self.ti_header.text()
                                   + "　|　⚠ 手填数据值有撞值：≥2 条数据路取到相同值=选错路也测不出"
                                     "(假绿)，请核对手填值")
        self._ti_loading = True
        try:
            self.ti_table.clear()
            self.ti_table.setColumnCount(len(vecs))
            self.ti_table.setHorizontalHeaderLabels([W.test_label(v) for v in vecs])
            self.ti_table.setRowCount(n_in + 2)
            vlabels = []
            data_key_set = set(exp.get("data_keys", []))   # 真·数据输入（d:*）；控制/上游配方键不在内
            self._ti_mux_data_rows = {}                     # 可手填数据行 row -> (物理基名小写, 位宽, 绑定键)
            ov_bases = set((data_ov or {}).keys())
            ustart = self._ti_mux_user_start
            uend = self._ti_mux_user_end                   # [ustart,uend)=用户列；[uend,len)=左表勾的全局负向
            pin = self._ti_mux_dft_pin
            for ri, ent in enumerate(self._ti_mux_disp):
                if ent[0] == "gate":
                    # DFT 门输入行：每条测试都显式驱到透传值（只读——值由 dft 页公式决定）
                    vlabels.append("%s (DFT门)" % pin[0])
                    for ci in range(len(vecs)):
                        it = QtWidgets.QTableWidgetItem(self._cell_text(pin[1], 1))
                        it.setFlags(QtCore.Qt.ItemIsEnabled)
                        if ustart <= ci < uend:
                            it.setBackground(USER_BG)      # 用户列整列着色，与其它输入行一致
                        elif ci >= uend:
                            it.setBackground(NEG_BG)       # 全局负向列：琥珀，与 logic 负向列一致
                        it.setToolTip(
                            "DFT 门（dft 页）：本输出的源头控制之一。每条测试都显式 force 到"
                            "透传值 %d（输出走功能值），与 for_test 的输入清单同口径。\n"
                            ".sv 末尾另自动追加 1 条门=%d 的 IDDQ 漏电态拍（验门控本身）。"
                            % (pin[1], 1 - pin[1]))
                        self.ti_table.setItem(ri, ci, it)
                    continue
                key = ent[1]
                b = exp["bindings"][key]
                is_data = key in data_key_set               # 只有真数据输入可手填（控制/级联配方只读）
                vlabels.append("%s (%s)" % (b.base, "数据" if is_data else "控制"))
                base_low = (b.base or "").lower()
                for ci, v in enumerate(vecs):
                    val = v.assignments.get(key, 0)
                    it = QtWidgets.QTableWidgetItem(self._cell_text(val, b.width))
                    is_user = ustart <= ci < uend
                    is_gneg = ci >= uend                    # 左表勾「负向」追加的全局负向列(只读)
                    # 数据行可手填；负向列输入只读(负向=同输入·改错值)。
                    # 用户列：改【本列】该数据源(auto_out 随路由源值重算)；自动列：按物理寄存器 by_base 同步。
                    if is_data and base_low and not v.is_negative:
                        it.setFlags(QtCore.Qt.ItemIsEnabled | QtCore.Qt.ItemIsSelectable
                                    | QtCore.Qt.ItemIsEditable)
                        if is_user:
                            it.setBackground(USER_BG)
                            it.setToolTip("用户手编列：双击改【本列】该数据源的值(只影响这一列)。\n"
                                          "auto_out 随路由 case(case_index)的源值重算。")
                        elif base_low in ov_bases:          # 已手填：加粗+底色，与"已填期望"呼应
                            it.setBackground(DSGN_BG)
                            f = it.font(); f.setBold(True); it.setFont(f)
                            it.setToolTip("手填数据值(替换自动互异/标记值)。清空可恢复自动；"
                                          "按物理寄存器 %s 整行同步。" % b.base)
                        else:
                            it.setToolTip("双击手填该数据源的值——替换自动分配的互异/标记值。\n"
                                          "按物理寄存器(%s)生效、整行同步；两路撞值会提示假绿(你负责)。"
                                          % b.base)
                    else:
                        it.setFlags(QtCore.Qt.ItemIsEnabled)        # 控制/级联配方行 + 负向列输入只读
                        if is_user:
                            it.setBackground(USER_BG)               # 用户列整列着色(含只读的控制行)
                        elif is_gneg:
                            it.setBackground(NEG_BG)                # 全局负向列：琥珀
                    self.ti_table.setItem(ri, ci, it)
                if is_data and base_low:
                    self._ti_mux_data_rows[ri] = (base_low, b.width, key)
            # auto_out 行(只读) + 期望 行(可手填)——与 logic 编辑器同语义/同配色
            vlabels.append("auto_out")
            vlabels.append("期望(进.sv)")
            for ci in range(len(vecs)):
                self._render_mux_exp_col(ci, n_in)
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
        # 覆盖档标签取【实际生效】的 mux_mode（单点优先），单点时加标记——否则切单点档头部看着不动
        cov_lab = self._COV_MODE2LABEL.get(mux_mode, mux_mode)
        if grp.out_name.lower() in self._sig_cov:
            cov_lab += "·单点"
        self.ti_header.setText(
            "mux 信号: %s = case(%s) %d 选 1　|　覆盖度=%s → 测试 %d 个（%s）　|　"
            "期望已手填 %d/%d　|　输入由 case 结构自动生成（只读）；期望行可手填，数据值可手填，负向勾选照常"
            % (grp.out_name, generator._mux_ctrl_desc(grp), len(grp.cases),
               cov_lab, len(vecs), cov_desc, n_filled, len(pos)))
        snote = (meta or {}).get("shadowed_note")        # A2 死分支：靠后重复 case 已跳过，标注出来
        if snote:
            self.ti_header.setText(self.ti_header.text() + "　|　⚙ " + snote)
        # 受 dft 页 iddq 门控（2026-06-10 用户定稿）：门=横向输入行，每条测试驱透传值；
        # 门不可 force 时退化为提示（输入表红色未解析 + 报告 iddq_skipped 兜底）
        g = (getattr(self.wb, "dft", None) or {}).get((grp.out_base or "").lower())
        if g:
            if getattr(self, "_ti_mux_dft_pin", None):
                note = ("受 dft 页 iddq 门控(%s)：已列为输入行，每条测试 force 到透传值；"
                        ".sv 另自动追加 1 条门=1 的 IDDQ 漏电态拍" % g["gate_base"])
            else:
                note = ("受 dft 页 iddq 门控(%s)：门网解析不到可 force 的 RO 网，"
                        "未能当输入驱动（见上方输入表/报告）" % g["gate_base"])
            self.ti_header.setText(self.ti_header.text() + "　|　⚙ " + note)

    def _render_mux_exp_col(self, ci, n_inputs):
        """渲染 mux 表第 ci 列的 auto_out + 期望 两格（手填状态变化时单列重绘）。
        用户负向列（第二十八轮）：auto_out 行仍显路由源的正确值(只读参考)，「期望」行=故意填错的错值(可改)。"""
        v = self._ti_mux_vecs[ci]
        ustart = getattr(self, "_ti_mux_user_start", 1 << 30)
        uend = getattr(self, "_ti_mux_user_end", 1 << 30)
        is_user = ustart <= ci < uend
        is_gneg = ci >= uend                    # 左表勾「负向」追加的全局负向列(只读参考)
        de = v.designer_expected
        # auto_out 格（只读）
        autoit = QtWidgets.QTableWidgetItem(W.fmt_bin(v.exp_value, v.exp_width))
        autoit.setFlags(QtCore.Qt.ItemIsEnabled)
        fa = autoit.font(); fa.setItalic(True); autoit.setFont(fa)
        autoit.setForeground(QtGui.QColor("#555555"))
        if is_user:
            autoit.setBackground(USER_BG)
        elif is_gneg:
            autoit.setBackground(NEG_BG)
        autoit.setToolTip("auto_out：程序按 case 结构算出的值（只读参考）。\n"
                          ".sv 断言对比的是下面 designer 手填的「期望」(未填→兜底用此值)。")
        self.ti_table.setItem(n_inputs, ci, autoit)
        if v.is_negative:
            negit = QtWidgets.QTableWidgetItem(W.fmt_bin(v.asserted_value, v.exp_width))
            negit.setForeground(NEG_FG)
            if is_gneg:
                # 左表勾「负向」自动追加的自检列：只读(琥珀)。生成时随覆盖度重算、错值=正确值取反。
                negit.setFlags(QtCore.Qt.ItemIsEnabled)
                negit.setBackground(NEG_BG)
                negit.setToolTip("左表勾「负向」→ 生成 .sv 时自动追加的自检列（默认错值=正确值取反），"
                                 "随覆盖度重算、不在此直接编辑。\n"
                                 "要自定义错值/列名：选一条正向列点工具栏「加负向(选中)」，"
                                 "把它变成可编辑的用户负向列。")
            else:
                # 用户负向列：期望格 = 故意填错的错值(可改)；auto_out 仍是正确值供对照
                negit.setFlags(QtCore.Qt.ItemIsEnabled | QtCore.Qt.ItemIsSelectable
                               | QtCore.Qt.ItemIsEditable)
                negit.setToolTip("负向(故意填错)：.sv 断言用此【错值】，仿真应 FAIL=负向生效。\n"
                                 "双击改错值；清空=恢复默认(正确值取反)。")
            self.ti_table.setItem(n_inputs + 1, ci, negit)
            return
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
        """按当前向量选项自动生成测试项 → rowdict 列表。覆盖档走单点优先口径。"""
        mode, exhaustive = generator._decompose_cov(self._sig_cov_collapsed(sig))
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
        # 受 dft 页 iddq 门控的 logic 输出：门=输入行，每条测试驱透传值（与 mux 侧同口径）
        _dftg = (getattr(self.wb, "dft", None) or {}).get(
            (self._ti_sig.out_base or "").lower()) if self.wb else None
        iddq_tag = ("   [⚙dft页iddq门控(%s)：已列为输入行，每条测试驱透传值；.sv另自动+1条漏电态拍]"
                    % _dftg["gate_base"] if _dftg else "")
        # RTL 补充逻辑(Excel 缺级、手工补)：头部显眼琥珀横幅 + 标注，且真值表已按补充式扫出新输入维度
        supp = getattr(self._ti_sig, "_is_supplement", False)
        if supp:
            _snote = getattr(self._ti_sig, "_supplement_note", "") or ""
            supp_tag = ("　⚠【RTL补充·Excel缺此级ECO，已用此式扫真值表】"
                        + (("理由:%s" % _snote) if _snote else ""))
            self.ti_header.setStyleSheet(
                "color:#a05a00;background:#fff7ed;border:1px solid #fdba74;"
                "border-radius:5px;padding:4px 8px;font-weight:600;")
        else:
            supp_tag = ""
            self.ti_header.setStyleSheet("color:#445;")
        # 表达式写成 "输出 = RHS" 等式；字母对照已下移到『输入信号』表，头部保持精简
        self.ti_header.setText(
            "信号 %s     %s = %s     用例 %d 条%s%s%s%s%s"
            % (self._ti_sig.out_name, self._ti_sig.out_base or "out", self._ti_sig.expr,
               len(self._ti_rows), fill_tag, tag, cone_tag, iddq_tag, supp_tag))

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
        checked = self._collect_checked()    # 信号勾选(哪些输出参与生成)——第二十六轮起也存盘+启动恢复
        # 单点覆盖度【不进桶】——它是会话内临时档，存盘会被静默恢复、暗中盖过全局下拉(用户实测 bug)。
        has_edits = bool(self._edited or self._mux_expected or self._mux_neg or self._mux_data
                         or self._mux_dropped or self._mux_cleared or self._mux_user_vecs)
        if has_edits or checked:
            allbuckets[path] = {
                "edits": {name: _serialize_rows(ed["rows"]) for name, ed in self._edited.items()},
                "neg_only": dict(self._neg_only),
                # mux 手填期望：{信号名: {输入取值键: int}}（mux 不走 rows 编辑模型，单独一段）
                "mux_expected": {name: dict(m) for name, m in self._mux_expected.items() if m},
                # 勾了负向的 mux 信号名（mux 负向也是用户的选择，必须存盘）
                "mux_neg": sorted(self._mux_neg),
                # mux 数据值手填（B2）：{信号名: {物理基名: int}}
                "mux_data": {name: dict(d) for name, d in self._mux_data.items() if d},
                # mux 删除的个别测试列(签名) / 一键清空的整组(第二十六轮)
                "mux_dropped": {name: sorted(s) for name, s in self._mux_dropped.items() if s},
                "mux_cleared": sorted(self._mux_cleared),
                # mux 用户手编/复制/负向列（第二十八轮）：{信号名: [序列化 TestVector]}
                "mux_user_vecs": {name: _serialize_mux_vecs(vv)
                                  for name, vv in self._mux_user_vecs.items() if vv},
                # 信号勾选：原样信号名(保留大小写，恢复时按小写匹配)
                "signals_checked": checked,
            }
        else:
            allbuckets.pop(path, None)        # 啥都没有 → 桶也删掉
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
        self._apply_signal_checks(bucket.get("signals_checked"))   # 恢复信号勾选(第二十六轮)
        if missing:
            # 追加(不覆盖)——on_load 可能已往预览页写了"分析异常"清单，两份信息都要保留
            self.preview.appendPlainText(
                "\n以下信号有上次保存的测试项编辑，但在当前 Excel 里找不到(已跳过)：\n"
                + "\n".join("  %s — 信号名在 logic 页不存在(表改名/删行?)" % n for n in missing))
        return n_restored

    def _sync_neg_checks_from_edits(self):
        """把左表"负向"列同步成与编辑状态一致（恢复/导入后调用）——【权威】：有负向的勾上、
        没有的取消（导入完整配置时清掉上一会话残留的负向勾选；on_load 时表本就全空，取消无副作用）。
        logic：_edited 里有 kind==neg 行；mux：在 _mux_neg 集合里。"""
        self._sig_loading = True
        try:
            for r in range(self.table.rowCount()):
                sig = self._sig_of_row(r)
                if sig is None:
                    continue
                if isinstance(sig, excel_model.MuxGroup):
                    # 有负向就勾：整信号负向标记 OR 用户逐 case 负向列(第二十八轮)——否则左表会误示"无负向"
                    checked = (sig.out_name.lower() in self._mux_neg
                               or self._mux_has_user_neg(sig.out_name.lower()))
                else:
                    checked = self._signal_has_negative(sig.out_name.lower())
                cell = self.table.item(r, COL_NEG)
                if cell is not None:
                    cell.setCheckState(QtCore.Qt.Checked if checked else QtCore.Qt.Unchecked)
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
            coerced, bad = _coerce_int_map(exp_map)
            if coerced:
                self._mux_expected.setdefault(name_low, {}).update(coerced)
                n_restored += 1
            if bad:
                missing.append(name_low + "（mux 期望：%d 个数值非法，已跳过）" % bad)
        # mux 负向勾选：恢复到 _mux_neg（仍存在于 mux 页的才恢复，改名/删行的列出来）
        for name_low in (bucket.get("mux_neg") or []):
            if name_low in mux_by_name:
                self._mux_neg.add(name_low)
            else:
                missing.append(name_low + "（mux 负向：mux 页不存在）")
        # mux 数据值手填（B2）：仍在 mux 页的才恢复（物理基名键，与 by_base 同口径）
        for name_low, dmap in (bucket.get("mux_data") or {}).items():
            if name_low not in mux_by_name:
                missing.append(name_low + "（mux 数据值：mux 页不存在）")
                continue
            if isinstance(dmap, dict) and dmap:
                coerced, bad = _coerce_int_map(dmap, lower=True)
                if coerced:
                    self._mux_data.setdefault(name_low, {}).update(coerced)
                    n_restored += 1
                if bad:
                    missing.append(name_low + "（mux 数据值：%d 个数值非法，已跳过）" % bad)
        # mux 删除的个别测试列(签名) / 一键清空（第二十六轮）：仍在 mux 页的才恢复
        for name_low, sigs in (bucket.get("mux_dropped") or {}).items():
            if name_low not in mux_by_name:
                missing.append(name_low + "（mux 删除列：mux 页不存在）")
            elif sigs:
                self._mux_dropped[name_low] = set(sigs)
                n_restored += 1
        for name_low in (bucket.get("mux_cleared") or []):
            if name_low in mux_by_name:
                self._mux_cleared.add(name_low)
                n_restored += 1
            else:
                missing.append(name_low + "（mux 清空：mux 页不存在）")
        # mux 用户手编/复制/负向列（第二十八轮）：仍在 mux 页的才恢复（损坏项 _deserialize 内部跳过）
        for name_low, vlst in (bucket.get("mux_user_vecs") or {}).items():
            if name_low not in mux_by_name:
                missing.append(name_low + "（mux 用户列：mux 页不存在）")
                continue
            uv = _deserialize_mux_vecs(vlst)
            if uv:
                self._mux_user_vecs[name_low] = uv   # 赋值(非 extend)：恢复=权威状态，二次调用不叠加
                n_restored += 1
        # 单点覆盖度【不恢复】——会话内临时档，旧桶里残留的 sig_cov 一律忽略(见 _persist_edits 注释)。
        return n_restored, missing

    def _collect_config(self):
        """收集【完整配置】(第二十六轮，用户拍板「不只mux，logic/勾选/全局/测试编辑等所有配置一键带走」)：
        信号勾选 + 全局工具栏设置 + 探针前缀 + 强制force + 全部 per-signal 测试编辑(含 mux 删除/清空)。
        单点覆盖度【不含】——会话内临时档(见 _persist_edits 注释)。
        excel/excel_path：记下这份配置对应的源表(全路径+文件名)，导入时按文件名核对、配错表给提示。"""
        excel = (self.path_edit.text() or "").strip()
        return {
            "dreg_verify_config": 2,
            "excel": os.path.basename(excel),     # 文件名（跨机器比对用，路径常不同）
            "excel_path": excel,                  # 全路径（本机来源参考）
            "signals_checked": self._collect_checked(),
            "global": {
                "coverage_logic": self.coverage.currentText(),
                "coverage_mux": self.coverage_mux.currentText(),
                "max_tests": self.max_tests.value(),
                "cascade_logic": self._logic_cascade(), "cascade_mux": self._mux_cascade(),
                "append_to_logic": bool(self.append_to_logic_chk.isChecked()),
                "append_to_mux": bool(self.append_to_mux_chk.isChecked()),
                "include_risky": self._include_risky_on(),   # 缺前缀强制生成（改产物→须随配置带走）
            },
            "probe_prefixes": dict(self._probe_prefixes),
            "force_signals": sorted(self._force_signals),
            "suffix_override": dict(self._suffix_override),
            # RTL 补充逻辑：Excel 缺级时手工补的等价 logic 式（Claude 代写、用户导入复核）
            "logic_overrides": {k: dict(v) for k, v in self._logic_overrides.items()},
            "edits": {name: _serialize_rows(ed["rows"]) for name, ed in self._edited.items()},
            "neg_only": dict(self._neg_only),
            "mux_expected": {name: dict(m) for name, m in self._mux_expected.items() if m},
            "mux_neg": sorted(self._mux_neg),
            "mux_data": {name: dict(d) for name, d in self._mux_data.items() if d},
            "mux_dropped": {name: sorted(s) for name, s in self._mux_dropped.items() if s},
            "mux_cleared": sorted(self._mux_cleared),
            "mux_user_vecs": {name: _serialize_mux_vecs(vv)
                              for name, vv in self._mux_user_vecs.items() if vv},
        }

    def _apply_global_settings(self, g):
        """导入：套用全局工具栏设置(覆盖度/上限/级联/输出引用尾缀/缺前缀强制生成)。blockSignals 设值，
        避免逐项触发联动(resolver 重建/编辑器重载由调用方统一做一次)；并写入 settings 持久化。"""
        if not isinstance(g, dict):
            return
        for combo, key in ((self.coverage, "coverage_logic"), (self.coverage_mux, "coverage_mux")):
            v = g.get(key)
            if v in ("精简", "全面", "穷举"):
                combo.blockSignals(True); combo.setCurrentText(v); combo.blockSignals(False)
        mt = g.get("max_tests")
        if isinstance(mt, int) and 1 <= mt <= 100000:
            self.max_tests.blockSignals(True); self.max_tests.setValue(mt); self.max_tests.blockSignals(False)
        # 级联模式 logic/mux（缺新键时回退旧 cascade_mode；都缺=cone）
        cm = g.get("cascade_mode")
        for combo, key in ((self.cascade_logic_combo, "cascade_logic"),
                           (self.cascade_mux_combo, "cascade_mux")):
            v = g.get(key, cm)
            combo.blockSignals(True)
            combo.setCurrentIndex(1 if v == "force" else 0)
            combo.blockSignals(False)
        atl = g.get("append_to_logic")
        # 缺键(旧配置/含已删的 dft_observe)→ 复位到默认 True(=生产默认勾，2026-06-11 翻回)，使「导入这份
        # 工作状态」确定性，不残留本会话先前的手动切换。
        atl = atl if isinstance(atl, bool) else True
        self.append_to_logic_chk.blockSignals(True); self.append_to_logic_chk.setChecked(atl)
        self.append_to_logic_chk.blockSignals(False)
        atm = g.get("append_to_mux")
        # 缺键 → 复位默认 False(=mux 默认探裸名)，确定性。
        atm = atm if isinstance(atm, bool) else False
        self.append_to_mux_chk.blockSignals(True); self.append_to_mux_chk.setChecked(atm)
        self.append_to_mux_chk.blockSignals(False)
        ir = g.get("include_risky")
        # include_risky(缺前缀强制生成)：与上面几个开关不同——【缺键时保持当前】而非复位默认。
        # 这是较新字段，旧配置/pytest 基线本就无此键，不该翻动用户(或测试基线)刻意设的本机选择；
        # 新配置必带此键，「整份载入」对它们仍确定。本字段会改产物(skip vs force 生成)，故 present 即套用。
        if isinstance(ir, bool) and hasattr(self, "include_risky_chk"):
            self.include_risky_chk.blockSignals(True); self.include_risky_chk.setChecked(ir)
            self.include_risky_chk.blockSignals(False)
        self._persist_coverage()                 # coverage_logic/mux + max_tests
        st = _load_settings()
        st["cascade_logic"] = self._logic_cascade(); st["cascade_mux"] = self._mux_cascade()
        st["append_to_logic"] = bool(self.append_to_logic_chk.isChecked())
        st["append_to_mux"] = bool(self.append_to_mux_chk.isChecked())
        if hasattr(self, "include_risky_chk"):
            st["include_risky"] = bool(self.include_risky_chk.isChecked())
        _save_settings(st)

    def _reset_all_config_state(self):
        """导入【完整配置】前清空全部可编辑状态(= 加载这份工作状态，而非叠加在现有之上)。
        不碰已加载的 wb/signals/解析画像——只清用户配置层。"""
        self._edited = {}; self._customized = set(); self._neg_only = {}
        self._mux_expected = {}; self._mux_neg = set(); self._mux_data = {}
        self._mux_dropped = {}; self._mux_cleared = set(); self._mux_user_vecs = {}
        self._sig_cov = {}; self._sig_cascade = {}
        self._probe_prefixes = {}; self._force_signals = set(); self._suffix_override = {}
        self._logic_overrides = {}

    def on_export_edits(self):
        """导出【完整配置】为 .json（给同事/版本库/跨机器）：信号勾选 + 全局设置 + 探针前缀 +
        强制force + 全部测试编辑(含 mux 删除/清空)。单点覆盖度不导出(会话内临时档)。"""
        if not self.wb:
            QtWidgets.QMessageBox.information(self, "提示", "请先加载 Excel")
            return
        excel = (self.path_edit.text() or "").strip()
        default = os.path.splitext(os.path.basename(excel) or "dreg")[0] + "_config.json"
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "导出完整配置", default,
                                                        "JSON (*.json)")
        if not path:
            return
        payload = self._collect_config()   # 内含 excel/excel_path（源表文件名 + 全路径）
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
            self, "完成",
            "已导出完整配置：\n"
            "  · 勾选信号 %d 个；全局档 logic=%s/mux=%s，上限 %d\n"
            "  · 探针前缀 %d 条，强制force %d 个\n"
            "  · 测试编辑：logic %d 信号(手填期望 %d)、mux 手填期望 %d、删除列 %d、清空 %d\n%s"
            % (len(payload["signals_checked"]), payload["global"]["coverage_logic"],
               payload["global"]["coverage_mux"], payload["global"]["max_tests"],
               len(self._probe_prefixes), len(self._force_signals),
               len(self._edited), n_de, n_mux, len(self._mux_dropped), len(self._mux_cleared), path))

    def on_import_edits(self):
        """导入配置。v2【完整配置】= 先清空再照单恢复(信号勾选/全局/探针/force/全部测试编辑)；
        v1 旧【测试项编辑】文件 = 沿用合并语义(只并 per-signal 编辑)。跳过的信号列名字+原因。"""
        if not self.wb:
            QtWidgets.QMessageBox.information(self, "提示", "请先加载 Excel")
            return
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "导入配置", "",
                                                        "JSON (*.json);;全部文件 (*)")
        if not path:
            return
        try:
            with open(path, encoding="utf-8") as f:
                payload = json.load(f)
        except (OSError, ValueError) as ex:
            QtWidgets.QMessageBox.critical(self, "导入失败", "无法读取/解析 %s：\n%s" % (path, ex))
            return
        is_full = isinstance(payload, dict) and bool(payload.get("dreg_verify_config"))
        is_legacy = isinstance(payload, dict) and (
            payload.get("dreg_verify_edits")
            or any(k in payload for k in ("edits", "mux_expected", "mux_data")))
        if not (is_full or is_legacy):
            QtWidgets.QMessageBox.critical(
                self, "导入失败",
                "%s 不是 dreg_verify 配置/编辑文件(缺少 dreg_verify_config 或 edits/mux_* 段)。" % path)
            return
        # 源表核对：配置里记的 excel 文件名与当前加载的不一致 → 末尾提示(仍照常导入)。
        # 按【文件名】比对而非全路径——跨机器/跨同事路径不同是正常用法，不该误报。
        cfg_excel = str(payload.get("excel")
                        or os.path.basename(str(payload.get("excel_path") or ""))).strip()
        cur_excel = os.path.basename((self.path_edit.text() or "").strip())
        excel_mismatch = bool(cfg_excel and cur_excel and cfg_excel.lower() != cur_excel.lower())
        if is_full:
            # 完整配置：清空全部可编辑状态后照单恢复(= 加载这份工作状态)；先套全局/探针/force 并
            # 重建 resolver，再恢复编辑(编辑重算依赖正确的 resolver)，最后恢复勾选。
            self._reset_all_config_state()
            self._apply_global_settings(payload.get("global") or {})
            pp = payload.get("probe_prefixes")
            if isinstance(pp, dict):
                self._probe_prefixes = {str(k).strip().lower(): str(v).strip()
                                        for k, v in pp.items() if v and str(v).strip()}
                self._save_probe_prefixes()
            fs = payload.get("force_signals")
            if isinstance(fs, list):
                self._force_signals = {str(x).strip().lower() for x in fs if str(x).strip()}
                self._save_force_signals()
            so = payload.get("suffix_override")
            if isinstance(so, dict):
                self._suffix_override = {str(k).strip().lower(): bool(v)
                                         for k, v in so.items() if str(k).strip()}
                self._save_suffix_override()
            lo = payload.get("logic_overrides")
            if isinstance(lo, dict):
                self._logic_overrides = {str(k).strip().lower(): dict(v)
                                         for k, v in lo.items()
                                         if str(k).strip() and isinstance(v, dict)}
                self._save_logic_overrides()
            self._resolver = R.Resolver(self.wb, wire_prefixes=self._probe_prefixes,
                                        force_overrides=self._force_signals,
                                        cascade_mode=self._logic_cascade(),
                                        append_to_logic=self._append_to_logic_on(),
                                        append_to_mux=self._append_to_mux_on(),
                                        suffix_override=dict(self._suffix_override))
        n_restored, missing = self._apply_edits_bucket(payload)
        self._sync_neg_checks_from_edits()
        if is_full:
            self._reanalyze_all()                  # 重析全表(用新探针/force/级联) + 重建左表 + 刷新 logic 编辑器
            if getattr(self, "_ti_mux_sig", None) is not None:   # mux 编辑器也刷新(自审 Finding 3，_reanalyze_all 只管 logic)
                self._load_mux_test_items(self._ti_mux_sig)
            self._apply_signal_checks(payload.get("signals_checked"))   # 勾选权威：列出的勾上、其余清空
        else:
            if self._ti_sig is not None:           # legacy 合并：编辑器若停在某信号 → 刷新
                self._load_test_items(self._ti_sig)
            elif getattr(self, "_ti_mux_sig", None) is not None:
                self._load_mux_test_items(self._ti_mux_sig)
        self._persist_edits()                      # 导入结果进入自动存盘
        kind = "完整配置" if is_full else "测试项编辑"
        msg = "已导入%s（%d 个信号的测试编辑）。" % (kind, n_restored)
        if is_full:
            msg += "\n勾选信号、全局档、探针前缀、强制force 均已套用。"
        if excel_mismatch:
            msg = ("⚠ 这份配置是为《%s》导出的，当前加载的是《%s》。\n"
                   "  已照常导入；若两表信号不同，部分配置会落空(见下方跳过清单)。\n\n"
                   % (cfg_excel, cur_excel)) + msg
        if missing:
            msg += "\n\n以下信号在文件里有配置、但当前 Excel 里找不到(已跳过)：\n" + \
                   "\n".join("  %s" % n for n in missing[:30])
            if len(missing) > 30:
                msg += "\n  …等共 %d 个" % len(missing)
        QtWidgets.QMessageBox.information(self, "导入完成", msg)
        self.status.showMessage("已导入%s：%s（%d 个信号）" % (kind, path, n_restored))

    # 纵向(真值表)布局：每个输入/输出一行(纵表头)，每条测试一列 T0/T1...。
    #   行: 0..G-1 = 各输入(base)；R_AUTO = auto_out(表达式计算，只读)；R_EXP = 期望(designer 手填)。
    #   列: 每列一条测试用例(正向 或 负向；负向列标红、列头带 _NEG)。
    def _ti_dims(self):
        self._ti_G = len(self._ti_groups)
        # DFT 门输入行（受 dft 页门控的输出，2026-06-10）插在输入区（位置按 for_test 行序，
        # 见 _ti_gate_row）→ auto/期望行整体 +1。行号统一经 R_AUTO/R_EXP 属性透出。
        _extra = 1 if getattr(self, "_ti_dft_pin", None) else 0
        self.R_AUTO = self._ti_G + _extra   # auto_out 行：程序按表达式算出的值(参考，只读)
        self.R_EXP = self.R_AUTO + 1        # 期望 行：designer 手填；未填→生成 .sv 时 auto_out 兜底
        return self.R_EXP + 1      # 总行数 = 输入数 (+DFT门行) + 2(auto_out + 期望)

    def _ti_group_row(self, gi):
        """第 gi 个输入组 → 真值表显示行号（DFT 门行插在输入区中间时，其后的组整体 +1）。"""
        gr = getattr(self, "_ti_gate_row", None)
        return gi if (gr is None or gi < gr) else gi + 1

    def _ti_row_group(self, r):
        """真值表行号 → 输入组下标；DFT 门行/非输入行 → None（编辑处理器据此忽略门行）。"""
        gr = getattr(self, "_ti_gate_row", None)
        if gr is not None and r == gr:
            return None
        gi = r if (gr is None or r < gr) else r - 1
        return gi if 0 <= gi < len(self._ti_groups) else None

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
            drive = "force ENV_RF.%s" % b.wire_lhs
            # force 级联网 / 内部衔接网（_to_mux/_to_logic）：resolved=True 但要 scan_rtl 配前缀，
            # 否则 force 必 CUVUNF 被跳过——标出来，让切到 force 模式时一眼看见多了这道前缀要求。
            if getattr(b, "found_in", "") in ("needs-prefix", "mux-output"):
                drive += "  ⚠需探针前缀(内部衔接网，跑 scan_rtl 配前缀否则跳过)"
            return (kind, drive)
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
        # ── 数据寄存器：按【物理寄存器】收拢（同一寄存器只显一行，与 used_vars 同口径）——
        #    死分支重复行 / LUT 型同源多 case 不再刷成多行，与 for_test 一致(t0~t7 各一行)。
        seen_bases = set()
        for di, key in enumerate(exp.get("data_keys", [])):
            b = exp["bindings"].get(key)
            if b is None:
                continue
            bkey = (b.base or "").lower() or key
            if bkey in seen_bases:
                continue
            seen_bases.add(bkey)
            kind, drive = self._binding_meta(b)
            case_raw = grp.cases[di].case_raw if di < len(grp.cases) else "?"
            # RO 线控数据走 force（线控寄存器），其余是被该 case 选中的本地/lut 寄存器
            if (b.kind or "") == "RO":
                role = "数据(线控,force)"
            else:
                role = "数据寄存器(被该case选中)"
            rows.append({"letter": "case %s" % case_raw, "label": self._mux_label(b),
                         "role": role, "kind": kind, "drive": drive, "bold": False})
        # ── DFT 门（dft 页）：受 iddq 门控的输出，把门网当输入亮出来（.sv 自动+1 条漏电态拍）──
        dft_row = self._dft_gate_input_row(grp.out_base)
        if dft_row:
            rows.append(dft_row)
        tbl.setRowCount(len(rows))
        for i, rd in enumerate(rows):
            for c, v in enumerate([rd["letter"], rd["label"], rd["role"],
                                   rd["kind"], rd["drive"]]):
                it = QtWidgets.QTableWidgetItem(v)
                if rd.get("bold") and c == 0:         # 控制输入字母加粗（与 logic 行为呼应）
                    f = it.font(); f.setBold(True); it.setFont(f)
                if c == 4 and "未解析" in v:
                    it.setForeground(QtGui.QColor("red"))
                elif c == 4 and "需探针前缀" in v:
                    it.setForeground(QtGui.QColor("#d97706"))   # 琥珀：需前缀(非阻断但要配)
                tbl.setItem(i, c, it)
        tbl.resizeColumnsToContents()
        self._fit_inputs_height()

    @staticmethod
    def _mux_label(b):
        """绑定 → 『信号(位宽)』列文本。"""
        return b.base + ("[%d:0]" % (b.width - 1) if b.width > 1 else "")

    def _dft_gate_input_row(self, out_base):
        """输出受 dft 页 iddq 门控 → 『输入信号』表追加一行门网；不受门控返回 None。

        2026-06-10 Hi1108 实地反馈：iddq 门不在 cone/case 输入里（在 dft 页），编辑器真值表
        从不显示它，用户对照 for_test 以为漏了这个源头控制。在输入表单独亮出（驱动列照常给
        未解析/需探针前缀着色——它正是 IDDQ 漏电态拍的 force 目标）。"""
        g = (getattr(self.wb, "dft", None) or {}).get((out_base or "").lower())
        if not g or self._resolver is None:
            return None
        info = {"raw": g["gate_base"], "base": g["gate_base"],
                "width": 1, "msb": None, "lsb": None}
        b = self._resolver.resolve("dft_gate_" + g["gate_base"], info)
        kind, drive = self._binding_meta(b)
        return {"letter": "dft页", "label": g["gate_base"],
                "role": "DFT门(iddq)·每条测试驱透传值", "kind": kind,
                "drive": drive, "bold": True}

    def _dft_pin_display(self, out_base, input_bases=None):
        """真值表「DFT门」输入行的显示信息 (门基名, 透传值)；不被门控/门不可 force → None。

        2026-06-10 用户三轮澄清后定稿：iddq 是被门控输出的【横向输入参数】——必须像
        for_test 一样作为输入行出现在真值表里、每条测试有取值。判定与
        generator.pin_dft_gate 同口径（编辑器显示的=每条向量 .sv 实际 force 的）。
        input_bases：本信号已显式驱动的输入基名集合——门已是显式输入(如 RTL 补充列了该 iddq)→ 返回
        None，不再单列 DFT 门行（否则同一 iddq 网真值表里出现两行；与 build/report 去重同口径）。"""
        g = (getattr(self.wb, "dft", None) or {}).get((out_base or "").lower())
        if not g or self._resolver is None:
            return None
        if input_bases and g["gate_base"] in input_bases:
            return None
        info = {"raw": g["gate_base"], "base": g["gate_base"],
                "width": 1, "msb": None, "lsb": None}
        b = self._resolver.resolve("dft_gate_" + g["gate_base"], info)
        if not (b.resolved and b.kind == "RO"):
            return None
        # (门基名, 透传值, 寄存器地址, bit位)——后两项给"地址+bit"默认排序用
        return (g["gate_base"], int(g["transparent"]), b.address, b.reg_lsb)

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
        # DFT 门行（dft 页）：logic 输出也可能被 iddq 门控，与 mux 侧同口径亮出
        dft_row = self._dft_gate_input_row(self._ti_sig.out_base if self._ti_sig else "")
        tbl.setRowCount(len(self._ti_groups) + (1 if dft_row else 0))
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
                elif c == 4 and "需探针前缀" in v:
                    it.setForeground(QtGui.QColor("#d97706"))   # 琥珀：需前缀(非阻断但要配)
                tbl.setItem(i, c, it)
        if dft_row:
            i = len(self._ti_groups)
            for c, v in enumerate([dft_row["letter"], dft_row["label"], dft_row["role"],
                                   dft_row["kind"], dft_row["drive"]]):
                it = QtWidgets.QTableWidgetItem(v)
                if c == 0:
                    f = it.font(); f.setBold(True); it.setFont(f)
                if c == 4 and "未解析" in v:
                    it.setForeground(QtGui.QColor("red"))
                elif c == 4 and "需探针前缀" in v:
                    it.setForeground(QtGui.QColor("#d97706"))
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
            vlabels = [self._vheader_short(g) for g in self._ti_groups]
            if getattr(self, "_ti_dft_pin", None):
                _gr = self._ti_gate_row if self._ti_gate_row is not None else len(vlabels)
                vlabels.insert(_gr, "%s (DFT门)" % self._ti_dft_pin[0])
            vlabels += [auto_label, exp_label]
            self.ti_table.setVerticalHeaderLabels(vlabels)
            for i, g in enumerate(self._ti_groups):
                hi = self.ti_table.verticalHeaderItem(self._ti_group_row(i))
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
                self.ti_table.setItem(self._ti_group_row(i), c, it)
            # DFT 门输入行（受门控输出）：每条测试都驱到透传值（只读，值由 dft 页公式决定）
            pin = getattr(self, "_ti_dft_pin", None)
            if pin:
                git = self._mk_item(self._cell_text(pin[1], 1), False)
                git.setTextAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
                git.setToolTip(
                    "DFT 门（dft 页）：本输出的源头控制之一。每条测试都显式 force 到透传值 %d"
                    "（输出走功能值），与 for_test 的输入清单同口径。\n"
                    ".sv 末尾另自动追加 1 条门=%d 的 IDDQ 漏电态拍（验门控本身）。"
                    % (pin[1], 1 - pin[1]))
                _gr = self._ti_gate_row if self._ti_gate_row is not None else self._ti_G
                self.ti_table.setItem(_gr, c, git)
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
            # mux 信号：先按列分流——用户手编列(>= _ti_mux_user_start)走本列编辑，自动生成列走原 by-base/取值键。
            if item.column() >= getattr(self, "_ti_mux_user_start", 1 << 30):
                if item.row() == self._ti_mux_exp_row:
                    self._on_mux_user_exp_changed(item)
                elif item.row() in getattr(self, "_ti_mux_data_rows", {}):
                    self._on_mux_user_data_changed(item)
                return
            # 自动生成列：数据行手填（B2，按物理基名）/「期望」行手填（按输入取值键）
            if item.row() in getattr(self, "_ti_mux_data_rows", {}):
                self._on_mux_data_changed(item)
            else:
                self._on_mux_exp_changed(item)
            return
        r, c = item.row(), item.column()       # 列 c = 第 c 条测试；行 r = 输入/期望/负向
        if c < 0 or c >= len(self._ti_rows):
            return
        rd = self._ti_rows[c]
        try:
            _gi = self._ti_row_group(r)        # 行号→输入组（DFT 门行/非输入行=None）
            if _gi is not None:
                g = self._ti_groups[_gi]
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
        self._update_mux_header(grp, vecs, self._sig_cov_collapsed(grp))   # 单点优先，与表格同档
        self._update_cov_hint()

    def _on_mux_data_changed(self, item):
        """mux 数据行手填（B2）：按物理寄存器基名存进 _mux_data，替换自动互异/标记值。
        清空 = 恢复自动。改后整表重渲（override 流过 make_mux_vectors → by_base 整行同步 +
        auto_out/期望 重算 + 撞值"假绿"提示）。期望键(mux_assign_key)含数据值，改数据可能令已填期望失配被丢。"""
        grp = getattr(self, "_ti_mux_sig", None)
        if grp is None or item.row() not in self._ti_mux_data_rows:
            return
        base_low, width, _key = self._ti_mux_data_rows[item.row()]
        name_low = grp.out_name.lower()
        try:
            txt = item.text().strip()
            if txt == "":                       # 清空 = 恢复自动分配
                self._mux_data.get(name_low, {}).pop(base_low, None)
                if not self._mux_data.get(name_low):
                    self._mux_data.pop(name_low, None)
            else:
                self._mux_data.setdefault(name_low, {})[base_low] = self._parse_int(txt) & E.mask(width)
        except ValueError as ex:
            self.status.showMessage("数值解析失败: %s（已还原）" % ex)
            self._load_mux_test_items(grp)      # 还原显示
            return
        self._persist_edits()                   # 手填数据值即时存盘
        self._load_mux_test_items(grp)          # 整表重渲：override 经 make_mux_vectors 生效、整行同步
        # 左表状态/明细也按手填值重算（否则左表用自动值=与右侧/生成不一致，审查 #9）
        try:
            idx = self.signals.index(grp)
            self._analysis[idx] = self._analyze_one(grp)
            self._populate_table()
        except (ValueError, RuntimeError):
            pass
        self.status.showMessage("已手填 mux 数据值（物理寄存器 %s）并按 by_base 同步；清空可恢复自动" % base_low)

    # ───────────── mux 用户手编列（第二十八轮，mux 与 logic 平级）─────────────
    def _recompute_mux_user_exp(self, vec, grp, exp=None):
        """重算用户正向列的 auto_out = 它路由 case(case_index) 的数据源值。
        无需复跑选路：vec 克隆自某真实 case，case_index 即路由 case，data_keys[ci] 即路由源键。"""
        exp = exp or getattr(self, "_ti_mux_exp", None)
        out_w = grp.out_width or 1
        ci = getattr(vec, "case_index", None)
        dkeys = (exp or {}).get("data_keys") or []
        if ci is not None and 0 <= ci < len(dkeys):
            vec.exp_value = vec.assignments.get(dkeys[ci], 0) & E.mask(out_w)

    def _unique_mux_user_name(self, name_low, prefix="U"):
        """给用户列起一个不与现有列(自动 T<n> / 已有用户名)冲突的名字 U1/U2…。"""
        taken = set()
        for v in (self._ti_mux_vecs or []):
            taken.add((v.name or "").upper())
            taken.add(W.test_label(v).upper())
        for v in self._mux_user_vecs.get(name_low, []):
            taken.add((v.name or "").upper())
        k = 1
        while ("%s%d" % (prefix, k)).upper() in taken:
            k += 1
        return "%s%d" % (prefix, k)

    def _add_mux_user_vec(self, grp, vec):
        """把一条用户 vec 收进 _mux_user_vecs[name] + 落盘 + 整表重渲（注入经 build/report 生效）。"""
        name_low = grp.out_name.lower()
        self._mux_user_vecs.setdefault(name_low, []).append(vec)
        self._persist_edits()
        self._load_mux_test_items(grp)

    def _mux_src_vec_for_col(self, c):
        """取第 c 列对应的 vec（用户加列/复制列/加负向的克隆源）；越界则取首列。"""
        vecs = self._ti_mux_vecs
        if not vecs:
            return None
        if not (0 <= c < len(vecs)):
            c = 0
        return vecs[c]

    def _mux_add_col(self, grp):
        """加正向列(mux)：克隆当前选中列的 case(控制+数据)为一条【新】用户正向列，可改名/改本列数据值。
        mux 没有自由控制位空间——一列必须路由一条真实 case，故以选中列的 case 为基(默认首列)。"""
        src = self._mux_src_vec_for_col(self.ti_table.currentColumn())
        if src is None:
            QtWidgets.QMessageBox.information(self, "提示", "本 mux 信号没有可作基准的测试列")
            return
        name_low = grp.out_name.lower()
        nv = V.clone_vector(src)
        nv.is_negative = False; nv.neg_value = None; nv.neg_mode = None
        nv.designer_expected = None                       # 新列默认未填期望(auto_out 兜底)
        nv.name = self._unique_mux_user_name(name_low)
        nv.note = "用户手编(mux)"
        self._recompute_mux_user_exp(nv, grp)
        self._add_mux_user_vec(grp, nv)
        self.status.showMessage("已加一条 mux 正向列 %s（基于 case「%s」；双击数据行改本列取值/双击列头改名）"
                                % (nv.name, getattr(grp.cases[nv.case_index], "case_raw", "?")
                                   if nv.case_index is not None and nv.case_index < len(grp.cases) else "?"))

    def _mux_copy_col(self, grp):
        """复制列(mux)：把当前选中列(自动或用户)整列克隆为一条用户列(数据值/期望一并带走，改名)。"""
        c = self.ti_table.currentColumn()
        if not (0 <= c < len(self._ti_mux_vecs)):
            QtWidgets.QMessageBox.information(self, "提示", "请先选中要复制的测试列")
            return
        name_low = grp.out_name.lower()
        nv = V.clone_vector(self._ti_mux_vecs[c])
        nv.name = self._unique_mux_user_name(name_low)    # 不复制原名(避免 .sv 标号冲突)
        nv.note = "用户复制(mux)"
        self._add_mux_user_vec(grp, nv)
        self.status.showMessage("已复制为 mux 用户列 %s" % nv.name)

    def _mux_add_neg(self, grp, cols):
        """加负向(mux)：给指定列(正向)各克隆一条负向用户列(错值=路由源取反)；已存在相同负向则跳过。"""
        vecs = self._ti_mux_vecs
        name_low = grp.out_name.lower()
        out_w = grp.out_width or 1
        existing = {(tuple(sorted(v.assignments.items())), v.neg_value)
                    for v in self._mux_user_vecs.get(name_low, []) if v.is_negative}
        added = skipped = 0
        for c in cols:
            if not (0 <= c < len(vecs)) or vecs[c].is_negative:
                continue
            nv = V.make_negative(V.clone_vector(vecs[c]))
            key = (tuple(sorted(nv.assignments.items())), nv.neg_value)
            if key in existing:
                skipped += 1
                continue
            existing.add(key)
            nv.name = self._unique_mux_user_name(name_low)
            nv.note = "用户负向(mux)"
            self._mux_user_vecs.setdefault(name_low, []).append(nv)
            added += 1
        if not added:
            self.status.showMessage("选中的列都已有相同负向，未重复添加")
            return
        self._persist_edits()
        self._load_mux_test_items(grp)
        self._set_left_neg_check(grp, True)      # 左表「负向」指示同步成"有负向"(否则误示无负向)
        msg = "已为 %s 加 %d 条 mux 负向列" % (grp.out_name, added)
        if skipped:
            msg += "；%d 列已有相同负向已跳过" % skipped
        self.status.showMessage(msg)

    def _on_mux_user_data_changed(self, item):
        """用户列数据行手填：只改【本列】该绑定键的取值 + 重算 auto_out（路由源值）。"""
        grp = getattr(self, "_ti_mux_sig", None)
        c = item.column()
        vecs = self._ti_mux_vecs
        info = self._ti_mux_data_rows.get(item.row())
        if grp is None or info is None or not (self._ti_mux_user_start <= c < self._ti_mux_user_end):
            return
        _base_low, width, key = info
        vec = vecs[c]
        try:
            vec.assignments[key] = self._parse_int(item.text()) & E.mask(width)
        except ValueError as ex:
            self.status.showMessage("数值解析失败: %s（已还原）" % ex)
            self._load_mux_test_items(grp)
            return
        self._recompute_mux_user_exp(vec, grp)
        self._persist_edits()
        self._load_mux_test_items(grp)
        self.status.showMessage("已改 mux 用户列数据值（仅本列）；auto_out 已按路由源重算")

    def _on_mux_user_exp_changed(self, item):
        """用户列「期望」行手填：正向列存 designer_expected；负向列存错值(neg_value)。清空=恢复默认。"""
        grp = getattr(self, "_ti_mux_sig", None)
        c = item.column()
        vecs = self._ti_mux_vecs
        if grp is None or item.row() != self._ti_mux_exp_row \
                or not (self._ti_mux_user_start <= c < self._ti_mux_user_end):
            return
        vec = vecs[c]
        out_w = grp.out_width or 1
        m = E.mask(out_w)
        txt = item.text().strip()
        try:
            if vec.is_negative:
                if txt == "":
                    vec.neg_value = (~vec.exp_value) & m          # 空=恢复默认(正确值取反)
                else:
                    wrong = self._parse_int(txt) & m
                    if wrong == (vec.exp_value & m):              # 错值==正确值→负向必 PASS(无意义)，翻一位逼真错
                        wrong = (~wrong) & m
                        if wrong == (vec.exp_value & m):
                            wrong = wrong ^ 1
                        self.status.showMessage("负向错值不能等于正确值(否则断言会 PASS)，已自动改成 %s"
                                                % W.fmt_bin(wrong, out_w))
                    vec.neg_value = wrong
            else:
                vec.designer_expected = None if txt == "" else (self._parse_int(txt) & m)
        except ValueError as ex:
            self.status.showMessage("数值解析失败: %s（已还原）" % ex)
            self._load_mux_test_items(grp)
            return
        self._persist_edits()
        self._ti_loading = True
        try:
            self._render_mux_exp_col(c, self._ti_mux_exp_row - 1)
        finally:
            self._ti_loading = False
        self._update_mux_header(grp, vecs, self._sig_cov_collapsed(grp))

    def on_ti_add(self):
        grp = getattr(self, "_ti_mux_sig", None)
        if grp is not None and not self._ti_sig:        # mux：加一条用户正向列(基于选中列的 case)
            self._mux_add_col(grp)
            return
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
        grp = getattr(self, "_ti_mux_sig", None)
        if grp is not None and not self._ti_sig:    # mux：给选中列各加一条用户负向列
            cols = sorted({i.column() for i in self.ti_table.selectedItems()})
            if not cols:
                c = self.ti_table.currentColumn()
                cols = [c] if c >= 0 else [0]
            self._mux_add_neg(grp, cols)
            return
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
        grp = getattr(self, "_ti_mux_sig", None)
        if grp is not None and not self._ti_sig:    # mux：给每条正向列(自动+用户)各加一条用户负向列
            pos_cols = [c for c, v in enumerate(self._ti_mux_vecs) if not v.is_negative]
            if not pos_cols:
                self.status.showMessage("本 mux 信号没有可作负向来源的正向列")
                return
            self._mux_add_neg(grp, pos_cols)
            return
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
        grp = getattr(self, "_ti_mux_sig", None)
        if grp is not None and not self._ti_sig:    # mux：删用户负向列 + 清整信号负向标记(左表)
            name_low = grp.out_name.lower()
            user_negs = [v for v in self._mux_user_vecs.get(name_low, []) if v.is_negative]
            had_flag = name_low in self._mux_neg
            if not user_negs and not had_flag:
                self.status.showMessage("%s 没有负向测试可删" % grp.out_name)
                return
            kept = [v for v in self._mux_user_vecs.get(name_low, []) if not v.is_negative]
            if kept:
                self._mux_user_vecs[name_low] = kept
            else:
                self._mux_user_vecs.pop(name_low, None)
            if had_flag:                            # 整信号负向标记一并清掉
                self._mux_neg.discard(name_low)
            self._persist_edits()
            self._load_mux_test_items(grp)
            self._set_left_neg_check(grp, False)    # 负向已全删 → 左表指示同步成"无负向"
            self.status.showMessage("已删除 %s 的全部 mux 负向(用户负向列 %d 条%s)"
                                    % (grp.out_name, len(user_negs),
                                       " + 整信号负向标记" if had_flag else ""))
            return
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

    def _mux_rename_col(self, grp, col):
        """改 mux 用户列的名字（自动生成列拒绝，与 logic 的 T0/T1 同规则）。"""
        import re
        vecs = self._ti_mux_vecs
        if not (0 <= col < len(vecs)):
            return
        if not (self._ti_mux_user_start <= col < self._ti_mux_user_end):
            QtWidgets.QMessageBox.information(
                self, "不可改名", "这是自动生成列 / 左表勾「负向」的自检列，名字不可改。\n"
                "只有你新增的用户列(加正向列/复制列/加负向)可以改名。")
            return
        vec = vecs[col]
        cur = vec.name or W.test_label(vec)
        text, ok = QtWidgets.QInputDialog.getText(self, "重命名 mux 测试列",
                                                  "新名字(字母/数字/下划线)：", text=cur)
        if not ok:
            return
        nm = self._sanitize_name(text)
        if not nm:
            QtWidgets.QMessageBox.warning(self, "改名失败", "名字为空或全是非法字符")
            return
        if re.match(r"(?i)^t\d+(_neg)?$", nm):
            QtWidgets.QMessageBox.warning(self, "改名失败", "名字 %s 与自动测试命名(T<编号>)冲突，请换个名字" % nm)
            return
        final = nm + ("_NEG" if vec.is_negative and not nm.upper().endswith("NEG") else "")
        for j, other in enumerate(vecs):
            if j != col and W.test_label(other) == final:
                QtWidgets.QMessageBox.warning(self, "改名失败",
                                              "名字与列 %s 重复(会造成 .sv 标号冲突)" % final)
                return
        vec.name = nm
        self._persist_edits()
        self._load_mux_test_items(grp)
        self.status.showMessage("mux 测试列已改名为 %s" % final)

    def on_ti_rename_col(self, col):
        """改第 col 个测试列的名字。仅用户新增的列可改；自动生成的 T0/T1 拒绝。"""
        grp = getattr(self, "_ti_mux_sig", None)
        if grp is not None and not self._ti_sig:    # mux 用户列改名
            self._mux_rename_col(grp, col)
            return
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
        grp = getattr(self, "_ti_mux_sig", None)
        if grp is not None and not self._ti_sig:    # mux：整列克隆为用户列(数据值/期望带走，改名)
            self._mux_copy_col(grp)
            return
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
        # mux 信号（第二十六轮）：删的是个别 case 测试列 → 记签名进 _mux_dropped，与 logic 删列对称。
        grp = getattr(self, "_ti_mux_sig", None)
        if grp is not None and not self._ti_sig:
            self._mux_del_cols(grp)
            return
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

    def _mux_del_cols(self, grp):
        """删除选中的 mux 测试列：自动生成列 → 记签名进 _mux_dropped(可「重新生成」恢复)；
        用户手编列 → 从 _mux_user_vecs 按身份直接移除(不经签名，避免与自动列签名混淆)。"""
        cols = sorted({i.column() for i in self.ti_table.selectedItems()})
        if not cols:
            c = self.ti_table.currentColumn()
            cols = [c] if c >= 0 else []
        if not cols:
            QtWidgets.QMessageBox.information(self, "提示", "请先选中要删除的 mux 测试列")
            return
        name_low = grp.out_name.lower()
        ustart = self._ti_mux_user_start
        uend = self._ti_mux_user_end
        sigs = self._mux_dropped.setdefault(name_low, set())
        # 用户列按【对象身份】删（先取出，避免删自动列签名时索引错乱）
        del_user = [self._ti_mux_vecs[c] for c in cols if ustart <= c < uend]
        del_gneg = [c for c in cols if c >= uend]    # 左表勾的全局负向列：删它=清整信号负向标记
        n = 0
        for c in cols:
            if 0 <= c < ustart:                      # 自动生成列：签名过滤
                sigs.add(generator.mux_assign_key(self._ti_mux_vecs[c].assignments))
                n += 1
        if del_user:
            cur = self._mux_user_vecs.get(name_low, [])
            self._mux_user_vecs[name_low] = [v for v in cur if v not in del_user]
            if not self._mux_user_vecs.get(name_low):
                self._mux_user_vecs.pop(name_low, None)
            n += len(del_user)
        gneg_cleared = False
        if del_gneg and name_low in self._mux_neg:   # 删全局负向列 = 取消左表「负向」勾选(同 删负向)
            self._mux_neg.discard(name_low)
            gneg_cleared = True
            n += 1
        if not sigs:
            self._mux_dropped.pop(name_low, None)
        self._persist_edits()
        self._load_mux_test_items(grp)
        if gneg_cleared:
            self._set_left_neg_check(grp, self._mux_has_user_neg(name_low))   # 左表指示同步
        self.status.showMessage("已删除 %s 的 %d 条 mux 测试列（自动列可「重新生成」恢复）" % (grp.out_name, n))

    def on_ti_clear(self):
        """一键清空当前信号的所有测试 = 零用例（logic：空 override；mux：入 _mux_cleared）。可「重新生成」恢复。"""
        sig = self._ti_sig
        grp = getattr(self, "_ti_mux_sig", None)
        if sig is None and grp is None:
            QtWidgets.QMessageBox.information(self, "提示", "请先在左侧选择一个信号")
            return
        name = sig.out_name if sig is not None else grp.out_name
        if QtWidgets.QMessageBox.question(
                self, "确认清空",
                "清空 %s 的所有测试 = 该信号零用例（生成 .sv 时本信号不产出任何测试）。\n"
                "可点「重新生成」按当前覆盖度恢复默认。确定？" % name
                ) != QtWidgets.QMessageBox.Yes:
            return
        if sig is not None:                       # logic：空 rows = 空 override（build 跳过给原因）
            self._ti_rows = []
            self._ti_mark_customized()            # 写 _edited[name]={"rows":[]} + 存盘
            self._ti_populate()
        else:                                     # mux：入 _mux_cleared（零用例，与覆盖度无关）
            had_neg = (grp.out_name.lower() in self._mux_neg
                       or self._mux_has_user_neg(grp.out_name.lower()))
            self._mux_cleared.add(grp.out_name.lower())
            self._mux_user_vecs.pop(grp.out_name.lower(), None)   # 清空连用户手编列一并清(零用例)
            self._persist_edits()
            self._load_mux_test_items(grp)
            if had_neg:
                self._set_left_neg_check(grp, False)              # 零用例 → 无负向，左表指示同步
        self.status.showMessage("已清空 %s 的测试（零用例，可点「重新生成」恢复默认）" % name)

    def _set_left_neg_check(self, sig, checked):
        """直接设左表某信号的「负向」勾选（_sig_loading 守卫，不触发 on_signal_table_item_changed）。"""
        name_low = sig.out_name.lower()
        self._sig_loading = True
        try:
            for r in range(self.table.rowCount()):
                s = self._sig_of_row(r)
                if s is not None and s.out_name.lower() == name_low:
                    cell = self.table.item(r, COL_NEG)
                    if cell is not None:
                        cell.setCheckState(QtCore.Qt.Checked if checked else QtCore.Qt.Unchecked)
                    break
        finally:
            self._sig_loading = False

    def on_ti_regen(self):
        # mux 信号（第二十六轮）：重新生成 = 丢弃本信号全部 mux 自定义（清空/删列/手填期望/数据值/负向）→ 回到出厂。
        grp = getattr(self, "_ti_mux_sig", None)
        if grp is not None and not self._ti_sig:
            name_low = grp.out_name.lower()
            self._mux_cleared.discard(name_low)
            self._mux_dropped.pop(name_low, None)
            self._mux_expected.pop(name_low, None)
            self._mux_data.pop(name_low, None)
            self._mux_user_vecs.pop(name_low, None)   # 丢弃用户手编/复制/负向列(第二十八轮)
            self._mux_neg.discard(name_low)        # 整信号负向标记一并清
            self._set_left_neg_check(grp, False)   # 出厂态无负向 → 左表指示同步
            self._persist_edits()
            self._load_mux_test_items(grp)
            self.status.showMessage("已重新生成 %s 的 mux 测试项（丢弃自定义：清空/删列/手填期望/数据值/用户列/负向）"
                                    % grp.out_name)
            return
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
        # mux 信号（第二十八轮）：导出真值表 CSV——与 logic 同排版(每列一条测试，行=输入/auto_out/期望/驱动)。
        grp = getattr(self, "_ti_mux_sig", None)
        if grp is not None and not self._ti_sig:
            self._export_mux_csv(grp)
            return
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
                disp_rows = [(self._vheader_label(g),
                              [self._fmt_val(rd["base_values"].get(g["key"], 0), g["width"])
                               for rd in rows]) for g in self._ti_groups]
                if getattr(self, "_ti_dft_pin", None):   # DFT 门输入行：每条测试驱透传值
                    _gr = (self._ti_gate_row if self._ti_gate_row is not None
                           else len(disp_rows))
                    disp_rows.insert(_gr, ("%s (DFT门)" % self._ti_dft_pin[0],
                                           [str(self._ti_dft_pin[1])] * len(rows)))
                for _lbl, _vals in disp_rows:            # 行序与编辑器/for_test 一致
                    wr.writerow([_lbl] + _vals)
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

    def _mux_drive_strs(self, vec, bindings, used_vars):
        """mux 向量的 force / RF_WRITE 驱动文本（与 logic 的 _drive_strs 对称，供 CSV 展示）。"""
        try:
            forces, writes, _unres = W.compute_drives(vec, bindings, used_vars)
        except Exception:  # noqa: BLE001
            return "", ""
        fs = "; ".join("%s=%s" % (f["wire"], f["hex"]) for f in forces)
        ws = "; ".join("%s=%s" % (w["addr"], w["hex"]) for w in writes)
        return fs, ws

    def _export_mux_csv(self, grp):
        """导出 mux 信号测试项为真值表 CSV（给 designer 看 / 复制粘贴）。

        与 logic 的 on_ti_export_csv 同排版：第一列=信号/字段名，其后每列一条测试。
        忠于生成产物：若该信号勾了「负向」(左表，build 走 neg_signals/which=first 追加 1 条)，
        CSV 一并带上负向列；行=控制/数据输入 + auto_out + 期望(进.sv) + 期望来源 + 负向标记 + force/RF_WRITE。"""
        pos = self._ti_mux_vecs
        if not pos:
            QtWidgets.QMessageBox.information(self, "提示", "本 mux 信号当前没有可导出的测试列（见编辑器头部原因）")
            return
        exp = getattr(self, "_ti_mux_exp", None)
        if exp is None:
            exp = mux_gen.expand_mux_group(self.wb, self._resolver, grp)
        used = exp["used_vars"]
        bindings = exp["bindings"]
        data_key_set = set(exp.get("data_keys", []))
        # 忠于 build：克隆(不改 live 的 _ti_mux_vecs 对象)后按 build 同口径补整信号负向(which=first)+
        # 去重(全局负向与用户逐 case 负向重叠时与 build 一致)+ 重排号。
        vecs = [V.clone_vector(v) for v in pos]
        if grp.out_name.lower() in self._mux_neg:
            vecs = V.add_negatives(vecs, mode="invert", which="first")
        vecs = generator._dedup_negatives(vecs)
        for i, v in enumerate(vecs):
            v.index = i
        default = "%s_mux_tests.csv" % (grp.out_base or "mux")
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "导出本 mux 信号测试项 CSV", default,
                                                        "CSV (*.csv)")
        if not path:
            return
        import csv
        out_w = grp.out_width or 1
        wsuf = "[%d:0]" % (out_w - 1) if out_w > 1 else ""
        try:
            with open(path, "w", encoding="utf-8-sig", newline="") as f:
                wr = csv.writer(f)
                wr.writerow(["信号\\测试"] + [W.test_label(v) for v in vecs])
                pin = getattr(self, "_ti_mux_dft_pin", None)
                disp = (getattr(self, "_ti_mux_disp", None)
                        or [("key", k) for k in used] + ([("gate", None)] if pin else []))
                for ent in disp:                  # 行序与编辑器/for_test 一致
                    if ent[0] == "gate":          # DFT 门输入行：每条测试驱透传值
                        wr.writerow(["%s (DFT门)" % pin[0]] + [str(pin[1])] * len(vecs))
                        continue
                    key = ent[1]
                    b = bindings.get(key)
                    width = b.width if b is not None else 1
                    role = "数据" if key in data_key_set else "控制"
                    label = "%s (%s)" % ((b.base if b is not None else key), role)
                    wr.writerow([label] + [self._fmt_val(v.assignments.get(key, 0), width) for v in vecs])
                wr.writerow(["auto_out%s" % wsuf] + [self._fmt_val(v.exp_value & E.mask(out_w), out_w)
                                                     for v in vecs])
                wr.writerow(["期望(进.sv)%s" % wsuf] + [self._fmt_val(v.asserted_value & E.mask(out_w), out_w)
                                                       for v in vecs])
                wr.writerow(["期望(bin)"] + [W.fmt_bin(v.asserted_value, out_w) for v in vecs])
                wr.writerow(["期望来源"] + [("负向(故意填错)" if v.is_negative else
                                            ("designer手填" if v.designer_expected is not None
                                             else "auto_out兜底")) for v in vecs])
                wr.writerow(["负向?"] + ["是" if v.is_negative else "" for v in vecs])
                drives = [self._mux_drive_strs(v, bindings, used) for v in vecs]
                wr.writerow(["force"] + [fs for fs, _ in drives])
                wr.writerow(["RF_WRITE"] + [ws for _, ws in drives])
        except OSError as ex:
            QtWidgets.QMessageBox.critical(self, "导出失败", str(ex))
            return
        QtWidgets.QMessageBox.information(self, "完成", "已导出 %d 条 mux 测试项：\n%s" % (len(vecs), path))
        self.status.showMessage("已导出 mux 测试项 CSV：%s" % path)

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
            sig = ed["sig"]
            # neg_only 信号(纯加负向)：build 时按【当前全局覆盖度】重算正向+补负向，不冻结——
            # 否则切覆盖度对它失效(用户实测的设计哲学 bug)。手改过测试项的信号才用冻结 _edited 行。
            if name_low in self._neg_only:
                rows = self._neg_only_rows_now(sig, name_low)
                if rows is None:
                    continue
            else:
                rows = ed["rows"]
            node, bindings, groups, _chain, _err = self._expand_sig(sig)
            if node is None:
                continue
            vecs = self._rows_to_vectors(node, bindings, groups, sig.out_width, rows)
            if positive_only:
                vecs = [v for v in vecs if not v.is_negative]
            elif negative_only:
                # 已清空(rows==[])的信号：保留空 override = 零用例，别在"仅负向"导出里回退自动重生(自审 Finding 2)。
                # 有正向但无负向的信号才整个略过。
                if not rows:
                    ov[name_low] = []
                    continue
                vecs = [v for v in vecs if v.is_negative]
                if not vecs:
                    continue                 # 该信号有正向但无负向 → "仅负向"导出里整个略过
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
            # 覆盖档位解耦（第二十二轮）：logic 侧走 mode/exhaustive（=logic 下拉），mux 侧独立。
            mux_mode=self._mux_cov_mode(),
            # 单点覆盖度：个别信号专属覆盖档，build/report 经 logic_vec_params(name)/
            # mux_cov_mode(name) 压过全局——所见(右侧编辑器)即所得(导出/报告)。
            sig_cov=dict(self._sig_cov),
            top_output_only=False,   # GUI 已按表勾选，不再二次过滤
            probe_prefixes=dict(self._probe_prefixes),
            force_overrides=set(self._force_signals),
            owner_in_msg=owner_in_msg,
            sv_summary=sv_summary,
            logic_cascade=self._logic_cascade(), mux_cascade=self._mux_cascade(),
            sig_cascade=dict(self._sig_cascade),
            vector_overrides=self._vector_overrides(positive_only=positive_only,
                                                    negative_only=negative_only),
            # mux 手填期望：所有导出范围都传——负向的错值防撞要看到它，
            # 保证"全部"与"仅负向"两份导出的负向错值一致(便于对照)
            mux_expected={k: dict(v) for k, v in self._mux_expected.items()},
            # mux 手填数据值（B2）：替换自动互异/标记值，预览/生成/报告都按此走
            mux_data={k: dict(v) for k, v in self._mux_data.items()},
            # mux 删除/清空（第二十六轮）：删掉的个别测试列(按签名) / 一键清空的整组(零用例)
            mux_dropped={k: sorted(v) for k, v in self._mux_dropped.items() if v},
            mux_cleared=sorted(self._mux_cleared),
            # mux 用户手编/复制/负向列（第二十八轮）：注入 build/report(make_mux_vectors 之后)，与 logic 平级
            mux_user_vecs={k: list(v) for k, v in self._mux_user_vecs.items() if v},
            # 输出引用尾缀开关（2026-06-11 Hi1108）：默认随 Excel 补 _to_logic/_to_mux 当探针网名；关=探基名网
            append_to_logic=self._append_to_logic_on(),
            append_to_mux=self._append_to_mux_on(),
            # 单点探裸名豁免：撞名信号(如 lo2g5g)即便全局开尾缀也单独不补（压过 append_to_logic）
            suffix_override=dict(self._suffix_override),
            # 缺前缀强制生成（2026-06-10）：force 子模块内部网缺前缀的信号也照常生成裸名 force，
            # 交给仿真验证此设计是否真需要前缀（=CLI --include-risky）
            include_risky=self._include_risky_on(),
            # RTL 补充逻辑（2026-06-12）：Excel 真表缺某信号 ECO 级时，用手工补的等价式扫真值表（块顶 ⚠）
            logic_overrides={k: dict(v) for k, v in self._logic_overrides.items()})

    # ───────────── 收集 / 选项 ─────────────
    def _collect(self):
        """返回勾选(COL_SEL)的信号名列表。负向不再单独收集——已在 vector_overrides 里。"""
        sel = []
        for r in range(self.table.rowCount()):
            if self.table.item(r, COL_SEL).checkState() == QtCore.Qt.Checked:
                sel.append(self._sig_of_row(r).out_name)
        return sel

    def _collect_checked(self):
        """勾选信号的原始名(存盘/导出用)，对 None 项健壮——持久化可能在表未完全就绪时触发。"""
        out = []
        for r in range(self.table.rowCount()):
            cell = self.table.item(r, COL_SEL)
            sig = self._sig_of_row(r)
            if cell is not None and sig is not None and cell.checkState() == QtCore.Qt.Checked:
                out.append(sig.out_name)
        return out

    def _apply_signal_checks(self, names):
        """按信号名列表恢复左表「选」勾选(大小写不敏感)——列出的勾上、其余清空(配置即权威)。
        names 为 None → 不动(无该段配置时保持现状，向后兼容旧桶)。"""
        if names is None:
            return
        want = {str(n).lower() for n in names}
        self._sig_loading = True
        try:
            for r in range(self.table.rowCount()):
                sig = self._sig_of_row(r)
                cell = self.table.item(r, COL_SEL)
                if sig is None or cell is None:
                    continue
                cell.setCheckState(QtCore.Qt.Checked if sig.out_name.lower() in want
                                   else QtCore.Qt.Unchecked)
        finally:
            self._sig_loading = False

    def _mux_has_user_neg(self, name_low):
        """该 mux 信号是否有用户手编的负向列（第二十八轮）。"""
        return any(v.is_negative for v in self._mux_user_vecs.get(name_low, []))

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
        """导出'给人看'的测试用例报告。三种格式(按扩展名/所选筛选器分派)：
        · HTML —— 汇总+每信号真值表(可筛 owner/信号名/类型/top)+完整明细+可验证性；
        · Excel —— 真值表 sheet(给 designer 看与复制粘贴) + 汇总/明细/可验证性(autofilter+冻结表头)；
        · CSV —— 明细 + 汇总(+可验证性)三份扁平表。
        勾选了信号则只报告这些，否则覆盖全部信号；自动带上测试项编辑/负向。"""
        if not self.wb:
            return
        from dreg_verify import cli            # 复用 CLI 的报告写出器(按扩展名出 HTML/Excel/CSV)
        sel = self._collect()
        fmts = [("HTML 网页 (*.html)", ".html"),
                ("Excel 工作簿 (*.xlsx)", ".xlsx"),
                ("CSV 表格 (*.csv)", ".csv")]
        path, selfilter = QtWidgets.QFileDialog.getSaveFileName(
            self, "导出测试用例报告", "dreg_report.html", ";;".join(f for f, _ in fmts))
        if not path:
            return
        # 按所选筛选器补正扩展名（筛选器=显式格式选择，权威）：先剥掉任何已知报告扩展名，
        # 再补所选格式的扩展名。这样「默认名 dreg_report.html + 选 Excel」→ .xlsx；不会出双扩展名。
        want_ext = next((e for f, e in fmts if f == selfilter), None)
        if want_ext:
            base = path
            for e in (".html", ".htm", ".xlsx", ".csv"):
                if base.lower().endswith(e):
                    base = base[:-len(e)]
                    break
            path = base + want_ext
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

    def on_fortest(self):
        """把当前测试项按 for_test 真值表排版回填到 Excel：复制源 Excel 全部 sheet、只替换
        for_test 页(源文件不动)。给 designer 看/复制粘贴；自动带上测试项编辑/负向。
        勾选了信号则只回填这些，否则覆盖全部 logic 信号(mux 表结构不同，不进 for_test)。"""
        if not self.wb:
            return
        src = self.path_edit.text().strip()
        if not src or not os.path.isfile(src):
            QtWidgets.QMessageBox.warning(self, "提示", "请先加载有效的源 .xlsx —— 回填要复制它的全部 sheet")
            return
        from dreg_verify import fortest_writer
        sel = self._collect()
        default = os.path.splitext(os.path.basename(src))[0] + "_fortest.xlsx"
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "回填 for_test 到新 Excel", default, "Excel 工作簿 (*.xlsx)")
        if not path:
            return
        if not path.lower().endswith(".xlsx"):
            path += ".xlsx"
        if os.path.abspath(path) == os.path.abspath(src):
            QtWidgets.QMessageBox.warning(self, "提示", "输出文件不能是源 Excel 本身(回填产物是新文件，源文件不动)")
            return
        try:
            rep = generator.report(self.wb, self._opts(sel or None))
            n_grp = fortest_writer.write_fortest(src, path, rep)
        except Exception as ex:  # noqa: BLE001
            QtWidgets.QMessageBox.critical(self, "回填失败", str(ex))
            return
        scope = "勾选的" if sel else "全部"
        n_logic = sum(1 for t in rep.get("tables", []) if t.get("is_logic") and t.get("tests"))
        n_mux = sum(1 for t in rep.get("tables", []) if not t.get("is_logic"))
        mux_note = ("；mux 信号 %d 个未回填(for_test 是 logic cone 真值表排版，mux 结构不同)" % n_mux) if n_mux else ""
        QtWidgets.QMessageBox.information(
            self, "完成", "已回填 %s logic 信号(%d 组)到 for_test 页%s：\n%s\n\n"
            "(源 Excel 各 sheet 数据已复制，源文件未改；图表/图片等非数据元素可能不保留)"
            % (scope, n_grp, mux_note, path))
        self.status.showMessage("已回填 for_test：%s（%d 组）" % (path, n_grp))

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
