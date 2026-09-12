# -*- coding: utf-8 -*-
"""dialogs.py —— GUI v2 C2-d：对话框集（架构 §1.1「三处确认 / 重命名列 / mux 数据整表 / 列设置 /
粘贴名单 / 预设 / 重复标号确认 / 导入结果 / 批量填」+ .sv 导出选项弹层内容）。

十个对话框（每个一个类，都是 `QDialog` 子类）：

    ConfirmDialog        ① 三处确认：清零 / 删反例（含「手调过的错值会丢」加强文案）/ auto→期望
    RenameColumnDialog   ② 重命名列（truth_edit.check_col_name 三道关，错误就地显示不关框）
    MuxDataDialog        ③ mux 数据值整表（每个数据基名一行，8 种数值写法，失败就地标红）
    ColumnsDialog        ④ 列设置（从 signal_list.py 原样搬来，objectName / 返回形状不变）
    PasteNamesDialog     ⑤ 粘贴名单勾选（从 signal_list.py 原样搬来）
    PresetsDialog        ⑥ 预设（存：名字 + 覆盖提示 / 取：列表选择 + 删除）
    DupLabelsDialog      ⑦ 重复 assert 标号确认（C-164）
    ImportReportDialog   ⑧ 导入结果（点名 + 原因在前、计数在后 —— C-194 / C-270 / I-20）
    BatchFillDialog      ⑨ 批量填期望（常量 / 取 auto / 清空 三选一 + 范围）
    ExportOptionsDialog  ⑩ .sv 导出选项（注释 / 末尾汇总 / owner 三勾 + 范围三选，C-159/160/161）

硬约束（架构 §1.1 表「只准 import 的东西」+ §1.2 分层）：

  · 本模块只 import `truth_edit` / `edits` / `names` / `terms` / `theme` / `widgets`（+ PySide6）。
    **不 import `exports`** —— .sv 选项的默认值由调用方把 `exports.EXPORT_OPTION_DEFAULTS` 传进来；
    重复标号的行文本由调用方用 `exports.dup_label_text` 渲好传进来（也可直接给三元组，本模块同款渲染）。
    **不 import `contracts`** —— 列设置的列键与顺序取自 `terms.LIST_HEADERS`（与 `contracts.ListCol`
    同序，`tests/test_ui_dialogs.py::test_dlg_columns_keys_match_contracts` 机械锁住）。
  · 一切用户可见文案取自 `terms`，色值取自 `theme`，objectName 取自 `names`；本模块**不改**这三个文件
    —— 还没进去的常量暂存在下面的 `PENDING_TERMS` / `PENDING_NAMES` 两张表里（C2-int 整块搬进
    `terms.py` / `names.py` 即可，搬完本模块自动改读它们，见 `_txt` / `_oname`）。
  · 阻塞只有一处：`QDialog.exec()`（`tests/ui_harness.auto_dialogs` 按 `windowTitle` 拦它）。
    每个类同时提供**非模态** `open()`（不阻塞，结果走 `accepted` / `finished` 信号）。
    本模块内部**不调**任何 `QMessageBox` 静态法，也不开嵌套事件循环。
  · 校验失败**不关框**：`accept()` 先过 `validate()`，不过就地显示错误 / 标红后 return。

契约 ID（只列对话框本体涉及的；入口与后果归各调用方）：
C-089/C-090（清零 + 确认）· C-102/C-103（删反例 + 保护性确认）· C-094/C-095（auto→期望 + 确认）·
C-036（清除反例的同款确认）· C-091/C-092/C-093（重命名列 + 三道校验 + 自动列拒改名文案）·
C-110/C-113（mux 数据值整表 + 撞值提示）· C-008/C-046（列设置）· C-290（粘贴名单）· C-291（预设）·
C-164（重复 assert 标号）· C-193/C-194/C-195（导入结果三段）· C-298（批量填）·
C-159/C-160/C-161/C-163（.sv 导出三选项 + 范围）· C-270 / I-20（点名在前、计数在后）· I-14（objectName）。
"""

from PySide6 import QtCore, QtWidgets

from dreg_verify import edits as ED
from dreg_verify import truth_edit as TE

from . import names as N
from . import terms as T
from . import theme as TH
from .widgets import mono_font, ui_font

Qt = QtCore.Qt

__all__ = [
    "ConfirmDialog", "RenameColumnDialog", "MuxDataDialog", "ColumnsDialog", "PasteNamesDialog",
    "PresetsDialog", "DupLabelsDialog", "ImportReportDialog", "BatchFillDialog", "ExportOptionsDialog",
    "confirm", "CONFIRM_KINDS", "BATCH_MODES", "BATCH_SCOPES", "EXPORT_SCOPES", "EXPORT_FLAGS",
    "LIST_COLUMN_KEYS", "TITLES", "PENDING_TERMS", "PENDING_NAMES", "PENDING_NAME_PREFIXES",
    "fmt_value", "fmt_mux_data_edit",
]


# ═════════════════════════ 暂存：待搬进 terms.py / names.py 的常量 ═════════════════════════
# C2-int 把这两张表整块搬进 ui/terms.py / ui/names.py（additive）即可；搬完本模块自动改读那边
# （`_txt` / `_oname` 都是先 getattr(模块) 再回退本表），这两张表随即变成死数据、可整块删掉。

#: 待补进 `ui/terms.py` 的文案
PENDING_TERMS = {
    # ① 三处确认
    "DLG_CONFIRM_TITLES": {"clear": "清零本信号", "del_neg": "删除全部反例", "auto_fill": "auto→期望"},
    "DLG_CONFIRM_DEL_NEG_PLAIN_FMT": "将删除本信号全部 {n} 条反例，正向用例保留。确定删除？",
    "DLG_CONFIRM_NAMES_HEAD": "会丢掉的反例列 —— 名字和原因：",
    "DLG_CONFIRM_NAMES_COUNT_FMT": "共 {n} 列",
    "DLG_CONFIRM_REASON_NAMED": "自定义命名",
    "DLG_CONFIRM_REASON_HAND": "手调过错值",
    # ② 重命名列
    "DLG_RENAME_HINT": "只能用字母 / 数字 / 下划线；不能占 T<编号> 这个自动测试保留名；不能与其它列重名。",
    # ③ mux 数据值整表
    "DLG_MUX_DATA_HEADERS": ("数据寄存器", "位宽", "当前值", "新值"),
    "DLG_MUX_DATA_EMPTY": "这个信号没有可手填的数据寄存器行。",
    # ④ 列设置
    "DLG_COLUMNS_HINT": "勾选列常驻第一列，不在这里关。",
    # ⑤ 粘贴名单
    "DLG_PASTE_NAMES_EMPTY": "一个名字都没填。",
    # ⑥ 预设
    "DLG_PRESETS_SAVE_TITLE": "存为预设",
    "DLG_PRESETS_MANAGE_TITLE": "管理预设",
    "DLG_PRESETS_NAME_HINT": "给当前的勾选 + 筛选起个名字。",
    "DLG_PRESETS_OVERWRITE_FMT": "已有同名预设「{name}」，存下去会覆盖它。",
    "DLG_PRESETS_NAME_REQUIRED": "名字不能为空。",
    "DLG_PRESETS_DELETE": "删除",
    "DLG_PRESETS_EMPTY": "还没有存过预设。",
    # ⑦ 重复标号
    "DLG_DUP_LABELS_CONTINUE": "仍要写出",
    "DLG_DUP_LABELS_ROW_FMT": "  {label}  ←  {a} / {b}",
    # ⑧ 导入结果
    "DLG_IMPORT_REPORT_TITLE": "导入结果",
    "DLG_IMPORT_MISSING_HEAD": "配置里有、当前表里找不到的信号 —— 名字和原因：",
    "DLG_IMPORT_MISSING_COUNT_FMT": "共 {n} 个（这些跳过，其余照常导入）",
    "DLG_IMPORT_MISSING_MORE_FMT": "…（只列前 {k} 个）",
    "DLG_IMPORT_ROW_FMT": "{name}　└ {reason}",
    "DLG_IMPORT_MISSING_REASON": "当前表里没有这个信号（改名？删行？该页不存在？）",
    "DLG_IMPORT_NONE": "没有找不到的信号。",
    # ⑨ 批量填期望
    "DLG_BATCH_FILL_MODES": {"const": "填一个固定值", "auto": "取程序算的值（auto）", "clear": "清空期望"},
    "DLG_BATCH_FILL_SCOPE_SELECTED_FMT": "只填选中的 {n} 列",
    "DLG_BATCH_FILL_SCOPE_ALL_FMT": "全部 {n} 列",
    "DLG_BATCH_FILL_SCOPE_LABEL": "范围",
    # ⑩ .sv 导出选项
    "DLG_EXPORT_OPTIONS_TITLE": "导出 .sv 选项",
    "DLG_EXPORT_SCOPE_LABEL": "范围",
}

#: 待补进 `ui/names.py` 的 objectName（值 = 小写 snake_case，前缀 dlg_）
PENDING_NAMES = {
    # 通用按钮（同一时刻只有一个对话框在，名字不会撞）
    "DLG_BUTTON_BOX": "dlg_button_box",
    "DLG_BTN_OK": "dlg_btn_ok",
    "DLG_BTN_CANCEL": "dlg_btn_cancel",
    # ① 三处确认
    "DLG_CONFIRM_NAMES": "dlg_confirm_names",
    "DLG_CONFIRM_COUNT": "dlg_confirm_count",
    # ② 重命名列
    "DLG_RENAME_COL_HINT": "dlg_rename_col_hint",
    # ③ mux 数据值整表
    "DLG_MUX_DATA_HINT": "dlg_mux_data_hint",
    "DLG_MUX_DATA_ERROR": "dlg_mux_data_error",
    # ④ 列设置
    "DLG_COLUMNS_HINT": "dlg_columns_hint",
    # ⑤ 粘贴名单
    "DLG_PASTE_NAMES_HINT": "dlg_paste_names_hint",
    # ⑥ 预设
    "DLG_PRESETS_HINT": "dlg_presets_hint",
    "DLG_PRESETS_DELETE_BTN": "dlg_presets_delete_btn",
    # ⑧ 导入结果
    "DLG_IMPORT_REPORT_NOTES": "dlg_import_report_notes",
    "DLG_IMPORT_REPORT_HEAD": "dlg_import_report_head",
    "DLG_IMPORT_REPORT_LIST": "dlg_import_report_list",
    "DLG_IMPORT_REPORT_COUNT": "dlg_import_report_count",
    # ⑨ 批量填期望
    "DLG_BATCH_FILL_HINT": "dlg_batch_fill_hint",
    "DLG_BATCH_FILL_ERROR": "dlg_batch_fill_error",
    "DLG_BATCH_FILL_MODE_CONST": "dlg_batch_fill_mode_const",
    "DLG_BATCH_FILL_MODE_AUTO": "dlg_batch_fill_mode_auto",
    "DLG_BATCH_FILL_MODE_CLEAR": "dlg_batch_fill_mode_clear",
    "DLG_BATCH_FILL_SCOPE_SELECTED": "dlg_batch_fill_scope_selected",
    "DLG_BATCH_FILL_SCOPE_ALL": "dlg_batch_fill_scope_all",
    # ⑩ .sv 导出选项
    "DLG_EXPORT_OPTIONS": "dlg_export_options",
    "DLG_EXPORT_OPT_COMMENTS": "dlg_export_opt_comments",
    "DLG_EXPORT_OPT_SV_SUMMARY": "dlg_export_opt_sv_summary",
    "DLG_EXPORT_OPT_OWNER_IN_MSG": "dlg_export_opt_owner_in_msg",
    "DLG_EXPORT_SCOPE_ALL": "dlg_export_scope_all",
    "DLG_EXPORT_SCOPE_POS": "dlg_export_scope_pos",
    "DLG_EXPORT_SCOPE_NEG": "dlg_export_scope_neg",
}

#: 动态生成的 objectName 前缀（names.py 里对应 `fmt_*` 函数；测试白名单按前缀放行）
PENDING_NAME_PREFIXES = ("dlg_mux_data_edit_",)


def _txt(key):
    """文案：先 `ui/terms.py`，再本模块 `PENDING_TERMS`。两边都没有 = 编码错误，立刻炸。"""
    val = getattr(T, key, None)
    if val is None:
        val = PENDING_TERMS.get(key)
    if val is None:
        raise KeyError("文案 %s 既不在 ui/terms.py 也不在 dialogs.PENDING_TERMS" % key)
    return val


def _oname(key):
    """objectName：先 `ui/names.py`，再本模块 `PENDING_NAMES`。"""
    val = getattr(N, key, None)
    if val is None:
        val = PENDING_NAMES.get(key)
    if val is None:
        raise KeyError("objectName %s 既不在 ui/names.py 也不在 dialogs.PENDING_NAMES" % key)
    return val


# ═════════════════════════ 标题表（harness 按 windowTitle 拦截，必须稳定）═════════════════════════
def _confirm_titles():
    return dict(_txt("DLG_CONFIRM_TITLES"))


#: {标题键: 窗口标题} —— `ui_harness.auto_dialogs(answers={标题: 答案})` 的键就从这里取。
TITLES = {
    "confirm_clear": _confirm_titles()["clear"],
    "confirm_del_neg": _confirm_titles()["del_neg"],
    "confirm_auto_fill": _confirm_titles()["auto_fill"],
    "rename_col": T.DLG_RENAME_TITLE,
    "mux_data": T.DLG_MUX_DATA_TITLE,
    "columns": T.DLG_COLUMNS_TITLE,
    "paste_names": T.DLG_PASTE_NAMES_TITLE,
    "presets_save": _txt("DLG_PRESETS_SAVE_TITLE"),
    "presets_manage": _txt("DLG_PRESETS_MANAGE_TITLE"),
    "dup_labels": T.EXPORT_DUP_LABELS_TITLE,
    "import_report": _txt("DLG_IMPORT_REPORT_TITLE"),
    "batch_fill": T.DLG_BATCH_FILL_TITLE,
    "export_options": _txt("DLG_EXPORT_OPTIONS_TITLE"),
}


# ═════════════════════════ 小工具 ═════════════════════════
def fmt_value(v, width=1):
    """数值显示格式（与 C-084 同一套、能被 `truth_edit.parse_int` 原样读回）：
    1 位 `0/1`；2–4 位 `0bXXXX`；更宽 `0xNN`。None → 空串。

    ⚠ 真值表里的规范实现是 C3-a 的 `ui/truth/model.fmt_cell`；本函数只服务本模块的
    「当前值」只读列，测试 `test_dlg_mux_data_value_format_roundtrips` 锁住它与 parse_int 的互逆。"""
    if v is None:
        return ""
    w = max(1, int(width or 1))
    val = int(v) & ((1 << w) - 1)
    if w <= 1:
        return "%d" % val
    if w <= 4:
        return "0b" + format(val, "0%db" % w)
    return "0x%0*X" % ((w + 3) // 4, val)


def fmt_mux_data_edit(base):
    """mux 数据整表里某个数据寄存器的「新值」输入框名：`dlg_mux_data_edit_<物理基名>`。"""
    return "dlg_mux_data_edit_%s" % str(base or "").strip().lower()


def _small_label(text, parent, object_name=None, mute=True, wrap=True):
    lab = QtWidgets.QLabel(str(text), parent)
    if object_name:
        lab.setObjectName(object_name)
    lab.setFont(ui_font(TH.FS_UI_SMALL))
    lab.setWordWrap(bool(wrap))
    if mute:
        lab.setStyleSheet("color:%s;" % TH.MUTE)
    return lab


def _error_style():
    return "color:%s;" % TH.BAD_FG


def _bad_edit_style():
    """就地标红：非法数值写法的输入框（底 + 边框 + 字全走 theme，不写裸十六进制）。"""
    return "QLineEdit{background:%s;border:1px solid %s;color:%s;}" % (TH.BAD_BG, TH.BAD_FG, TH.BAD_FG)


class _BaseDialog(QtWidgets.QDialog):
    """所有对话框的共同骨架：objectName / 标题 / 字体 / 按钮条 / 「校验不过不关框」/ harness 可读文本。

    · `exec()` **不重写**（`ui_harness.auto_dialogs` 打的是 `QtWidgets.QDialog.exec`，重写就拦不住）；
    · `open()` 重写成真正的非模态（show，不阻塞、不夺焦点链），结果走 `accepted` / `finished`；
    · `text()` / `detailedText()` 给 harness 的 `DialogRecorder` 读 —— 「跳过项必须点名」这类
      契约（C-270 / I-20）调用方就靠 `rec.saw(名字)` 验。
    """

    OBJECT_NAME = ""
    TITLE = ""

    def __init__(self, parent=None, object_name=None, title=None, width=None):
        super().__init__(parent)
        self.setObjectName(object_name or self.OBJECT_NAME)
        self.setWindowTitle(title if title is not None else self.TITLE)
        self.setFont(ui_font())
        self._body_text = ""
        self._detail_text = ""
        self.error_label = None
        self.ok_btn = None
        self.cancel_btn = None
        self.lay = QtWidgets.QVBoxLayout(self)
        self.lay.setContentsMargins(16, 14, 16, 12)
        self.lay.setSpacing(8)
        if width:
            self.setMinimumWidth(int(width))

    # ── harness 可读文本 ──
    def text(self):
        """正文（`DialogRecorder` 读它做文案断言）。"""
        return self._body_text

    def detailedText(self):
        """详情（点名块；`DialogRecorder.saw` 也扫它）。"""
        return self._detail_text

    # ── 构件 ──
    def _add_error_label(self, object_name):
        self.error_label = QtWidgets.QLabel("", self)
        self.error_label.setObjectName(object_name)
        self.error_label.setFont(ui_font(TH.FS_UI_SMALL))
        self.error_label.setWordWrap(True)
        self.error_label.setStyleSheet(_error_style())
        self.lay.addWidget(self.error_label)
        return self.error_label

    def _add_buttons(self, ok_text=None, cancel_text=None, default_no=False, with_cancel=True):
        box = QtWidgets.QDialogButtonBox(self)
        box.setObjectName(_oname("DLG_BUTTON_BOX"))
        self.ok_btn = box.addButton(str(ok_text or T.DLG_YES), QtWidgets.QDialogButtonBox.AcceptRole)
        self.ok_btn.setObjectName(_oname("DLG_BTN_OK"))
        self.ok_btn.setFont(ui_font())
        if with_cancel:
            self.cancel_btn = box.addButton(str(cancel_text or T.DLG_NO), QtWidgets.QDialogButtonBox.RejectRole)
            self.cancel_btn.setObjectName(_oname("DLG_BTN_CANCEL"))
            self.cancel_btn.setFont(ui_font())
        box.accepted.connect(self.accept)
        box.rejected.connect(self.reject)
        # C-090 / C-095 / C-103：三处确认的默认按钮 = 否（手滑回车不该把活删了）
        for btn, is_default in ((self.ok_btn, not default_no), (self.cancel_btn, default_no)):
            if btn is None:
                continue
            btn.setAutoDefault(bool(is_default))
            btn.setDefault(bool(is_default))
        self.lay.addWidget(box)
        return box

    # ── 校验：不过不关框 ──
    def validate(self):
        """→ (ok, 给用户看的失败原因)。子类重写。"""
        return True, ""

    def show_error(self, msg):
        if self.error_label is not None:
            self.error_label.setText(str(msg or ""))
            self.error_label.setVisible(bool(msg))

    def clear_error(self):
        self.show_error("")

    def accept(self):
        ok, msg = self.validate()
        if not ok:
            self.show_error(msg)
            return                                   # 就地显示错误，**不关框**
        self.clear_error()
        super(_BaseDialog, self).accept()

    def answer(self):
        """本次的结果：确定 → True（非模态 `open()` 流程用；模态流程用 `_run()`）。"""
        return self.result() == QtWidgets.QDialog.Accepted

    def _run(self):
        """阻塞一次并返回「是不是按了确定」。

        ⚠ 用的是 `exec()` 的**返回码**而不是 `result()` —— `ui_harness.auto_dialogs` 把
        `QDialog.exec` 整个换掉、根本不会 `setResult()`，读 `result()` 会让 `answers` 永远说了不算。
        所有 `ask*()` 入口一律经本方法。"""
        return int(self.exec()) == int(QtWidgets.QDialog.Accepted)

    # ── 非模态打开 ──
    def open(self):
        """非模态打开（不阻塞、不做窗口模态）；结果经 `accepted` / `rejected` / `finished` 回调。"""
        self.setModal(False)
        self.setWindowModality(Qt.NonModal)
        self.show()
        self.raise_()
        return self


# ═════════════════════════ ① 三处确认 ═════════════════════════
CONFIRM_CLEAR = "clear"
CONFIRM_DEL_NEG = "del_neg"
CONFIRM_AUTO_FILL = "auto_fill"
#: 三处确认的 kind（V2Spec §5 M22「三处都加二次确认」）
CONFIRM_KINDS = (CONFIRM_CLEAR, CONFIRM_DEL_NEG, CONFIRM_AUTO_FILL)


def confirm_title(kind):
    """三处确认的窗口标题（harness 按它拦截，必须稳定）。"""
    return _confirm_titles()[str(kind)]


def confirm_text(kind, n=0):
    """三处确认的正文（全部来自 terms；删反例按「有没有值得保护的反例」分强弱两句）。"""
    kind = str(kind)
    if kind == CONFIRM_CLEAR:
        return T.TRUTH_CONFIRM_CLEAR                                     # C-089 / C-090
    if kind == CONFIRM_AUTO_FILL:
        return T.TRUTH_CONFIRM_AUTO_FILL                                 # C-094 / C-095
    if kind == CONFIRM_DEL_NEG:
        if int(n or 0) > 0:                                              # C-103：自定义命名 / 手调过错值
            return T.TRUTH_CONFIRM_DEL_NEG_FMT.format(n=int(n))
        return _txt("DLG_CONFIRM_DEL_NEG_PLAIN_FMT").format(n=int(n or 0))
    raise ValueError("未知的确认 kind=%r（可选：%s）" % (kind, ", ".join(CONFIRM_KINDS)))


class ConfirmDialog(_BaseDialog):
    """① 三处确认：清零（C-089/C-090）/ 删反例（C-102/C-103/C-036）/ auto→期望（C-094/C-095）。

    构造：`ConfirmDialog(kind, n=0, names=(), parent=None)`
      kind  —— "clear" / "del_neg" / "auto_fill"
      n     —— 删反例时 = 值得保护（自定义命名或手调过错值）的反例条数，>0 走加强文案
      names —— 这些反例列的名字：**点名在前、计数在后**（C-270 / I-20）
    返回：`answer() -> bool`；一句话用法 `dialogs.confirm(kind, n, names, parent) -> bool`。
    """

    OBJECT_NAME = N.DLG_CONFIRM

    def __init__(self, kind, n=0, names=(), parent=None):
        kind = str(kind)
        if kind not in CONFIRM_KINDS:
            raise ValueError("未知的确认 kind=%r（可选：%s）" % (kind, ", ".join(CONFIRM_KINDS)))
        super(ConfirmDialog, self).__init__(parent, title=confirm_title(kind), width=440)
        self.kind = kind
        self.n = int(n or 0)
        self.names_shown = [str(x) for x in (names or ())]
        self._body_text = confirm_text(kind, self.n)

        body = QtWidgets.QLabel(self._body_text, self)
        body.setObjectName(N.DLG_CONFIRM_TEXT)
        body.setWordWrap(True)
        body.setFont(ui_font())
        self.lay.addWidget(body)

        self.names_list = None
        self.count_label = None
        if self.names_shown:
            head = _txt("DLG_CONFIRM_NAMES_HEAD")
            self.lay.addWidget(_small_label(head, self))
            self.names_list = QtWidgets.QListWidget(self)
            self.names_list.setObjectName(_oname("DLG_CONFIRM_NAMES"))
            self.names_list.setFont(mono_font())
            for nm in self.names_shown:
                QtWidgets.QListWidgetItem(nm, self.names_list)
            self.names_list.setMaximumHeight(TH.ROW_H * 5)
            self.lay.addWidget(self.names_list)
            count = _txt("DLG_CONFIRM_NAMES_COUNT_FMT").format(n=len(self.names_shown))
            self.count_label = _small_label(count, self, _oname("DLG_CONFIRM_COUNT"))
            self.lay.addWidget(self.count_label)                          # 计数在点名【之后】
            self._detail_text = "\n".join([head] + ["  " + x for x in self.names_shown] + [count])

        self._add_buttons(default_no=True)                                # C-090：默认按钮 = 否

    @classmethod
    def ask(cls, kind, n=0, names=(), parent=None):
        """起框 + 阻塞（harness 可拦）→ bool。"""
        dlg = cls(kind, n=n, names=names, parent=parent)
        return dlg._run()


def confirm(kind, n=0, names=(), parent=None):
    """三处确认的一句话入口（架构 §6.11 的 `confirm(...) -> bool`）。"""
    return ConfirmDialog.ask(kind, n=n, names=names, parent=parent)


def protected_negative_names(cols):
    """把 `edits.protected_negatives(cols)` 的结果变成「名字 + 原因」两行——给 ConfirmDialog 点名用。

    （本函数只做展示层的拼名，判定仍在 `edits`：自定义命名 / 手调过错值。）"""
    out = []
    for col in ED.protected_negatives(cols):
        nm = str(col.get("name") or "")
        reason = (_txt("DLG_CONFIRM_REASON_NAMED") if not TE.is_auto_neg_name(nm)
                  else _txt("DLG_CONFIRM_REASON_HAND"))
        out.append("%s　└ %s" % (nm, reason))
    return out


# ═════════════════════════ ② 重命名列 ═════════════════════════
class RenameColumnDialog(_BaseDialog):
    """② 重命名列（C-091）。即时走 `truth_edit.check_col_name` 三道关（C-092）：
    非法字符 / 占了 `T<编号>` 保留名 / 与其它列最终标号重名；**错误就地显示、框不关**。

    构造：`RenameColumnDialog(current="", others=(), negative=False, parent=None)`
      others —— 其它列的最终标号（不含本列）；negative —— 本列是不是反例列（最终名自动带 _NEG）
    返回：`final_name() -> str`（合法时的最终标号，非法 → ""）；`ask(...) -> str | None`。

    自动生成的 T 列不许改名（C-093）由调用方在打开本框前判定，提示文案 `terms.TRUTH_RENAME_AUTO_REFUSED`。
    """

    OBJECT_NAME = N.DLG_RENAME_COL
    TITLE = T.DLG_RENAME_TITLE

    def __init__(self, current="", others=(), negative=False, parent=None):
        super(RenameColumnDialog, self).__init__(parent, width=420)
        self._others = [str(x) for x in (others or ())]
        self._negative = bool(negative)
        self._body_text = _txt("DLG_RENAME_HINT")

        self.edit = QtWidgets.QLineEdit(str(current or ""), self)
        self.edit.setObjectName(N.DLG_RENAME_COL_EDIT)
        self.edit.setFont(mono_font())
        self.edit.selectAll()
        self.lay.addWidget(self.edit)
        self.lay.addWidget(_small_label(self._body_text, self, _oname("DLG_RENAME_COL_HINT")))
        self._add_error_label(N.DLG_RENAME_COL_ERROR)
        self._add_buttons()
        self.edit.textChanged.connect(self._live_check)
        self._live_check()

    # ── 校验 ──
    def check(self):
        """→ (ok, 最终名 或 失败原因)（`truth_edit.check_col_name` 原样返回）。"""
        return TE.check_col_name(self.edit.text(), self._others, negative=self._negative)

    def _live_check(self, _text=None):
        ok, info = self.check()
        self.show_error("" if ok else info)

    def validate(self):
        return self.check()

    def final_name(self):
        ok, info = self.check()
        return info if ok else ""

    @classmethod
    def ask(cls, current="", others=(), negative=False, parent=None):
        dlg = cls(current, others=others, negative=negative, parent=parent)
        ok = dlg._run()
        return dlg.final_name() if ok else None


# ═════════════════════════ ③ mux 数据值整表 ═════════════════════════
class MuxDataDialog(_BaseDialog):
    """③ mux 数据值整表手填（C-110）：每个**物理数据寄存器**一行（名 / 位宽 / 当前值 / 新值），
    按物理基名同步整表；新值留空 = 恢复自动分配（`terms.DLG_MUX_DATA_HINT`）。

    构造：`MuxDataDialog(rows, parent=None)`，rows 每项：
        {"base": 物理基名(小写), "label": 显示名(默认 base), "width": 位宽,
         "value": 当前生效值 int|None, "value_text": 可选，覆盖「当前值」列的显示,
         "override": 已手填过的值 int|None（预填进「新值」，没手填过就留空）}
    返回：`texts() -> {base: 用户填的原文}`、`values() -> {base: int|None}`（None = 清空恢复自动）；
          `apply_to(mux_data, name_low, src_out_name, name)` 直接走 `edits.set_mux_data_value` 落进会话档。
    非法写法**就地标红且不关框**（`terms.TRUTH_PARSE_FAILED_FMT`，8 种写法见 `truth_edit.parse_int`）。
    撞值提示 `terms.TRUTH_MUX_COLLISION`（C-113）由调用方在重析后给，本框只管取值。
    """

    OBJECT_NAME = N.DLG_MUX_DATA
    TITLE = T.DLG_MUX_DATA_TITLE

    COL_NAME, COL_WIDTH, COL_CUR, COL_NEW = 0, 1, 2, 3

    def __init__(self, rows, parent=None):
        super(MuxDataDialog, self).__init__(parent, width=520)
        self.rows = [dict(r) for r in (rows or [])]
        self._body_text = T.DLG_MUX_DATA_HINT
        self._editors = {}

        self.lay.addWidget(_small_label(self._body_text, self, _oname("DLG_MUX_DATA_HINT")))

        headers = list(_txt("DLG_MUX_DATA_HEADERS"))
        self.table = QtWidgets.QTableWidget(len(self.rows), len(headers), self)
        self.table.setObjectName(N.DLG_MUX_DATA_TABLE)
        self.table.setHorizontalHeaderLabels(headers)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.table.setSelectionMode(QtWidgets.QAbstractItemView.NoSelection)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setFont(mono_font())
        for r, row in enumerate(self.rows):
            base = str(row.get("base") or "")
            width = int(row.get("width") or 1)
            label = str(row.get("label") or base)
            cur = row.get("value_text")
            if cur is None:
                cur = fmt_value(row.get("value"), width)
            for c, val in ((self.COL_NAME, label), (self.COL_WIDTH, "%d" % width), (self.COL_CUR, cur)):
                item = QtWidgets.QTableWidgetItem(str(val))
                item.setFlags(Qt.ItemIsEnabled)                 # 只读三列
                self.table.setItem(r, c, item)
            editor = QtWidgets.QLineEdit(self.table)
            editor.setObjectName(fmt_mux_data_edit(base))
            editor.setFont(mono_font())
            ov = row.get("override")
            editor.setText(fmt_value(ov, width) if ov is not None else "")
            editor.textChanged.connect(self._clear_mark)
            self.table.setCellWidget(r, self.COL_NEW, editor)
            self._editors[base.lower()] = editor
            self.table.setRowHeight(r, TH.ROW_H)
        self.table.setMinimumHeight(TH.HEADER_H + TH.ROW_H * min(8, max(1, len(self.rows))) + 4)
        self.lay.addWidget(self.table, 1)
        if not self.rows:
            self.lay.addWidget(_small_label(_txt("DLG_MUX_DATA_EMPTY"), self))

        self._add_error_label(_oname("DLG_MUX_DATA_ERROR"))
        self._add_buttons()

    # ── 取值 ──
    def editor(self, base):
        """某个物理基名那一行的「新值」输入框（测试 / 调用方定位用）。"""
        return self._editors.get(str(base or "").lower())

    def texts(self):
        """{物理基名: 用户填的原文}（原样，未解析 —— 空串 = 清空恢复自动）。"""
        return {b: e.text().strip() for b, e in self._editors.items()}

    def values(self):
        """{物理基名: int|None}（None = 清空恢复自动）。解析不了的条目值为 None 且会在
        `validate()` 里被拦下 —— 正常流程里 `accept()` 过了才调本方法。"""
        out = {}
        for base, txt in self.texts().items():
            if txt == "":
                out[base] = None
                continue
            try:
                out[base] = TE.parse_int(txt)
            except ValueError:
                out[base] = None
        return out

    def bad_entries(self):
        """→ [(物理基名, 原文)]：解析不出数值的行。"""
        bad = []
        for base, txt in self.texts().items():
            if txt == "":
                continue
            try:
                TE.parse_int(txt)
            except ValueError:
                bad.append((base, txt))
        return bad

    def validate(self):
        bad = self.bad_entries()
        self._mark(bad)
        if not bad:
            return True, ""
        return False, T.TRUTH_PARSE_FAILED_FMT.format(text="、".join(t for _b, t in bad))

    def _mark(self, bad):
        bad_keys = {b for b, _t in bad}
        for base, editor in self._editors.items():
            editor.setStyleSheet(_bad_edit_style() if base in bad_keys else "")

    def _clear_mark(self, _text=None):
        sender = self.sender()
        if isinstance(sender, QtWidgets.QLineEdit):
            sender.setStyleSheet("")

    def apply_to(self, mux_data, name_low, src_out_name, name):
        """把整表新值写进会话档 `mux_data`（唯一写入口 = `edits.set_mux_data_value`）。
        → (改了几行, 清空了几行)；解析失败会抛 ValueError（正常流程里 accept 已挡住）。"""
        changed = cleared = 0
        for row in self.rows:
            base = str(row.get("base") or "").lower()
            editor = self._editors.get(base)
            if editor is None:
                continue
            txt = editor.text().strip()
            ED.set_mux_data_value(mux_data, str(name_low), str(src_out_name), str(name),
                                  base, int(row.get("width") or 1), txt)
            if txt == "":
                cleared += 1
            else:
                changed += 1
        return changed, cleared

    @classmethod
    def ask(cls, rows, parent=None):
        dlg = cls(rows, parent=parent)
        ok = dlg._run()
        return dlg.values() if ok else None


# ═════════════════════════ ④ 列设置 ═════════════════════════
#: 全部清单列键（顺序 = `terms.LIST_HEADERS` = `contracts.ListCol`，测试机械锁）
ALL_LIST_COLUMN_KEYS = tuple(T.LIST_HEADERS)
#: 进列设置的列键（勾选列常驻第一列、不进本对话框）
LIST_COLUMN_KEYS = tuple(k for k in ALL_LIST_COLUMN_KEYS if k != "check")


def _norm_col_keys(visible):
    """把 `visible`（列键字符串 / `contracts.ListCol` 整数 都吃）规整成列键集合。"""
    out = set()
    for x in (visible or ()):
        if isinstance(x, str):
            out.add(x)
            continue
        try:
            idx = int(x)
        except (TypeError, ValueError):
            continue
        if 0 <= idx < len(ALL_LIST_COLUMN_KEYS):
            out.add(ALL_LIST_COLUMN_KEYS[idx])
    return out


class ColumnsDialog(_BaseDialog):
    """④ 列设置（C-008 / C-046）。勾选列常驻第一列、不进本对话框（用户标注截图 c22c965c）。

    从 `ui/signal_list.py` 原样搬来：objectName（`DLG_COLUMNS` / `DLG_COLUMNS_LIST`）、标题、
    返回形状 `selection() -> {列键: bool}` 全不变；C2-int 把 signal_list 那一份删掉改成 import 这里。

    构造：`ColumnsDialog(visible, parent=None, items=None)`
      visible —— 当前可见的列（列键字符串或 `contracts.ListCol` 值，两种都吃）
      items   —— 可选，自带 [(列键, 显示名, 是否勾上)]，给非清单场景复用
    """

    OBJECT_NAME = N.DLG_COLUMNS
    TITLE = T.DLG_COLUMNS_TITLE

    def __init__(self, visible=(), parent=None, items=None):
        super(ColumnsDialog, self).__init__(parent, width=280)
        self._body_text = _txt("DLG_COLUMNS_HINT")
        self.list = QtWidgets.QListWidget(self)
        self.list.setObjectName(N.DLG_COLUMNS_LIST)
        self.list.setFont(ui_font())
        if items is None:
            vis = _norm_col_keys(visible)
            items = [(key, T.LIST_HEADERS.get(key) or key, key in vis) for key in LIST_COLUMN_KEYS]
        for key, label, checked in items:
            item = QtWidgets.QListWidgetItem(str(label), self.list)
            item.setData(Qt.UserRole, str(key))
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked if checked else Qt.Unchecked)
        self.lay.addWidget(self.list)
        self.lay.addWidget(_small_label(self._body_text, self, _oname("DLG_COLUMNS_HINT")))
        self._add_buttons()

    def selection(self):
        """→ {列键: 是否可见}（不含常驻的勾选列）。"""
        out = {}
        for i in range(self.list.count()):
            it = self.list.item(i)
            out[str(it.data(Qt.UserRole))] = it.checkState() == Qt.Checked
        return out

    @classmethod
    def ask(cls, visible=(), parent=None, items=None):
        dlg = cls(visible, parent=parent, items=items)
        ok = dlg._run()
        return dlg.selection() if ok else None


# ═════════════════════════ ⑤ 粘贴名单勾选 ═════════════════════════
class PasteNamesDialog(_BaseDialog):
    """⑤ 粘贴名单勾选（C-290）。多行文本 → 裸名小写列表：每行一个信号名，可带位宽切片、大小写无关。

    从 `ui/signal_list.py` 原样搬来（objectName / 标题 / `names()` 全不变）。
    构造：`PasteNamesDialog(parent=None, text="")`；返回 `names() -> [裸名小写]`；
    结果回填 `set_result(n_checked, missing)` → `terms.DLG_PASTE_NAMES_RESULT_FMT`（点名在前）。
    """

    OBJECT_NAME = N.DLG_PASTE_NAMES
    TITLE = T.DLG_PASTE_NAMES_TITLE

    def __init__(self, parent=None, text=""):
        super(PasteNamesDialog, self).__init__(parent, width=420)
        self._body_text = T.DLG_PASTE_NAMES_HINT
        self.lay.addWidget(_small_label(self._body_text, self, _oname("DLG_PASTE_NAMES_HINT")))
        # 属性名避开 text / result —— QDialog 的这两个名字被 Qt 与测试夹具当方法调（会炸在夹具里）
        self.editor = QtWidgets.QPlainTextEdit(self)
        self.editor.setObjectName(N.DLG_PASTE_NAMES_TEXT)
        self.editor.setFont(mono_font())
        if text:
            self.editor.setPlainText(str(text))
        self.lay.addWidget(self.editor, 1)
        self.result_label = QtWidgets.QLabel("", self)
        self.result_label.setObjectName(N.DLG_PASTE_NAMES_RESULT)
        self.result_label.setWordWrap(True)
        self.result_label.setFont(ui_font(TH.FS_UI_SMALL))
        self.lay.addWidget(self.result_label)
        self._add_buttons()

    def names(self):
        """→ [裸名小写]（去掉位宽切片、跳过空行与 # 注释行）。"""
        out = []
        for line in self.editor.toPlainText().splitlines():
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            if s.endswith("]") and "[" in s:
                s = s[:s.rindex("[")].strip()
            out.append(s.lower())
        return out

    def set_result(self, n_checked, missing=()):
        """把「勾上几个 / 找不到哪些」回填进结果行（C-290 / C-270：点名在前）。"""
        missing = [str(x) for x in (missing or ())]
        msg = T.DLG_PASTE_NAMES_RESULT_FMT.format(n=int(n_checked), m=len(missing),
                                                  names="、".join(missing) or "—")
        self.result_label.setText(msg)
        self._detail_text = msg
        return msg

    def validate(self):
        if not self.names():
            return False, _txt("DLG_PASTE_NAMES_EMPTY")
        return True, ""

    def show_error(self, msg):
        self.result_label.setText(str(msg or ""))

    @classmethod
    def ask(cls, parent=None, text=""):
        dlg = cls(parent=parent, text=text)
        ok = dlg._run()
        return dlg.names() if ok else None


# ═════════════════════════ ⑥ 预设 ═════════════════════════
PRESET_SAVE = "save"
PRESET_MANAGE = "manage"
PRESET_MODES = (PRESET_SAVE, PRESET_MANAGE)


class PresetsDialog(_BaseDialog):
    """⑥ 预设（C-291）：把当前勾选 + 筛选存成命名预设 / 下次一键取回。

    构造：`PresetsDialog(mode, presets=(), parent=None)`
      mode="save"   —— 名字输入 + 同名覆盖提示（`DLG_PRESETS_OVERWRITE_FMT`）；`preset_name() -> str`
      mode="manage" —— 已存预设列表 + 「删除」；`selected() -> str`、`deleted() -> tuple`、`remaining() -> tuple`
    presets —— 已存的预设名（dict 直接给键也行）。落盘走 `persist.presets_get/set`，本框不碰文件。
    """

    OBJECT_NAME = N.DLG_PRESETS

    def __init__(self, mode=PRESET_SAVE, presets=(), parent=None):
        mode = str(mode)
        if mode not in PRESET_MODES:
            raise ValueError("未知的预设模式 mode=%r（可选：%s）" % (mode, ", ".join(PRESET_MODES)))
        title = TITLES["presets_save"] if mode == PRESET_SAVE else TITLES["presets_manage"]
        super(PresetsDialog, self).__init__(parent, title=title, width=380)
        self.mode = mode
        self._presets = [str(x) for x in (presets or ())]
        self._deleted = []
        self._body_text = _txt("DLG_PRESETS_NAME_HINT") if mode == PRESET_SAVE else title

        self.name_edit = None
        if mode == PRESET_SAVE:
            self.name_edit = QtWidgets.QLineEdit(self)
            self.name_edit.setObjectName(N.DLG_PRESETS_NAME)
            self.name_edit.setFont(ui_font())
            self.lay.addWidget(self.name_edit)
            self.lay.addWidget(_small_label(self._body_text, self))

        self.list = QtWidgets.QListWidget(self)
        self.list.setObjectName(N.DLG_PRESETS_LIST)
        self.list.setFont(ui_font())
        for nm in self._presets:
            QtWidgets.QListWidgetItem(nm, self.list)
        self.list.setMaximumHeight(TH.ROW_H * 6)
        self.lay.addWidget(self.list, 1)

        self.hint = _small_label(_txt("DLG_PRESETS_EMPTY") if not self._presets else "",
                                 self, _oname("DLG_PRESETS_HINT"))
        self.lay.addWidget(self.hint)

        self.delete_btn = None
        if mode == PRESET_MANAGE:
            self.delete_btn = QtWidgets.QPushButton(_txt("DLG_PRESETS_DELETE"), self)
            self.delete_btn.setObjectName(_oname("DLG_PRESETS_DELETE_BTN"))
            self.delete_btn.setFont(ui_font())
            self.delete_btn.clicked.connect(self.delete_selected)
            self.lay.addWidget(self.delete_btn, 0, Qt.AlignLeft)
            if self._presets:
                self.list.setCurrentRow(0)

        self._add_buttons()
        if self.name_edit is not None:
            self.name_edit.textChanged.connect(self._live_hint)

    # ── 存 ──
    def preset_name(self):
        return self.name_edit.text().strip() if self.name_edit is not None else ""

    def _live_hint(self, _text=None):
        nm = self.preset_name()
        if nm and nm in self._presets:
            self.hint.setText(_txt("DLG_PRESETS_OVERWRITE_FMT").format(name=nm))
        else:
            self.hint.setText("")

    # ── 取 / 删 ──
    def selected(self):
        it = self.list.currentItem()
        return str(it.text()) if it is not None else ""

    def delete_selected(self):
        """从列表里删掉当前条（真正落盘由调用方按 `deleted()` 做）。"""
        row = self.list.currentRow()
        if row < 0:
            return ""
        nm = str(self.list.item(row).text())
        self.list.takeItem(row)
        self._deleted.append(nm)
        if nm in self._presets:
            self._presets.remove(nm)
        if not self._presets:
            self.hint.setText(_txt("DLG_PRESETS_EMPTY"))
        return nm

    def deleted(self):
        return tuple(self._deleted)

    def remaining(self):
        return tuple(str(self.list.item(i).text()) for i in range(self.list.count()))

    def validate(self):
        if self.mode == PRESET_SAVE and not self.preset_name():
            return False, _txt("DLG_PRESETS_NAME_REQUIRED")
        return True, ""

    def show_error(self, msg):
        self.hint.setText(str(msg or ""))

    @classmethod
    def ask_save(cls, presets=(), parent=None):
        dlg = cls(PRESET_SAVE, presets=presets, parent=parent)
        ok = dlg._run()
        return dlg.preset_name() if ok else None

    @classmethod
    def ask_manage(cls, presets=(), parent=None):
        dlg = cls(PRESET_MANAGE, presets=presets, parent=parent)
        ok = dlg._run()
        return (dlg.selected() if ok else None), dlg.deleted()


# ═════════════════════════ ⑦ 重复 assert 标号 ═════════════════════════
class DupLabelsDialog(_BaseDialog):
    """⑦ 重复 assert 标号确认（C-164）：写文件前列出**冲突的标号与两个信号**，继续 / 取消。

    构造：`DupLabelsDialog(dups=(), rows_text=None, parent=None)`
      dups      —— [(标号, 信号A, 信号B)]（本模块同款渲染；也可只给 rows_text）
      rows_text —— 调用方用 `exports.dup_label_text(dups)` 渲好的行文本（本模块不 import exports）
    返回：`answer() -> bool`（True = 仍要写出）。默认按钮 = 否。
    """

    OBJECT_NAME = N.DLG_DUP_LABELS
    TITLE = T.EXPORT_DUP_LABELS_TITLE

    def __init__(self, dups=(), rows_text=None, parent=None):
        super(DupLabelsDialog, self).__init__(parent, width=560)
        self.dups = [tuple(d) for d in (dups or ())]
        if rows_text is None:
            fmt = _txt("DLG_DUP_LABELS_ROW_FMT")
            rows_text = "\n".join(fmt.format(label=d[0], a=d[1], b=d[2]) for d in self.dups)
        n = len(self.dups) if self.dups else len([x for x in str(rows_text).splitlines() if x.strip()])
        self._body_text = T.EXPORT_DUP_LABELS_FMT.format(n=n, rows=rows_text)
        self._detail_text = str(rows_text)

        view = QtWidgets.QPlainTextEdit(self._body_text, self)
        view.setObjectName(N.DLG_DUP_LABELS_TEXT)
        view.setReadOnly(True)
        view.setFont(mono_font())
        view.setMinimumHeight(TH.ROW_H * 6)
        self.lay.addWidget(view, 1)
        self.view = view
        self._add_buttons(ok_text=_txt("DLG_DUP_LABELS_CONTINUE"), default_no=True)

    @classmethod
    def ask(cls, dups=(), rows_text=None, parent=None):
        dlg = cls(dups=dups, rows_text=rows_text, parent=parent)
        return dlg._run()


# ═════════════════════════ ⑧ 导入结果 ═════════════════════════
class ImportReportDialog(_BaseDialog):
    """⑧ 导入结果（配置 C-192 / 期望 C-298 导入后）：**点名找不到的信号 + 原因在前、计数在后**
    （C-194 / C-270 / I-20）；表不一致（C-193）与「不是本工具的配置」（C-195）作顶部提示行。

    构造：`ImportReportDialog(missing=(), counts=(), notes=(), parent=None)`
      missing —— [(名字, 原因)] 或 [名字]；原因缺省用 `DLG_IMPORT_MISSING_REASON`
      counts  —— 计数 / 逐段报数行（显示在点名块**之后**）
      notes   —— 顶部提示行（调用方用 `terms.EXPORT_IMPORT_MISMATCH_FMT` / `EXPORT_IMPORT_BAD_FILE` 渲好）
    返回：只有「知道了」一个按钮；`missing_names() -> tuple`。
    """

    OBJECT_NAME = N.DLG_IMPORT_REPORT
    TITLE = TITLES["import_report"]

    def __init__(self, missing=(), counts=(), notes=(), parent=None):
        super(ImportReportDialog, self).__init__(parent, width=560)
        self.missing = [(x[0], x[1]) if isinstance(x, (tuple, list)) and len(x) >= 2
                        else (x, _txt("DLG_IMPORT_MISSING_REASON")) for x in (missing or ())]
        self._counts = [str(x) for x in (counts or ())]
        self._notes = [str(x) for x in (notes or ())]

        self.notes_label = None
        if self._notes:
            self.notes_label = QtWidgets.QLabel("\n".join(self._notes), self)
            self.notes_label.setObjectName(_oname("DLG_IMPORT_REPORT_NOTES"))
            self.notes_label.setWordWrap(True)
            self.notes_label.setFont(ui_font())
            self.notes_label.setStyleSheet("color:%s;" % TH.WARN_FG)
            self.lay.addWidget(self.notes_label)

        # ── 点名块（在前）──
        head = _txt("DLG_IMPORT_MISSING_HEAD") if self.missing else _txt("DLG_IMPORT_NONE")
        self.head_label = QtWidgets.QLabel(head, self)
        self.head_label.setObjectName(_oname("DLG_IMPORT_REPORT_HEAD"))
        self.head_label.setWordWrap(True)
        self.head_label.setFont(ui_font())
        self.lay.addWidget(self.head_label)

        self.missing_list = QtWidgets.QListWidget(self)
        self.missing_list.setObjectName(_oname("DLG_IMPORT_REPORT_LIST"))
        self.missing_list.setFont(mono_font())
        row_fmt = _txt("DLG_IMPORT_ROW_FMT")
        limit = int(getattr(TH, "IMPORT_MISSING_LIST_MAX", 30))
        for name, reason in self.missing[:limit]:
            QtWidgets.QListWidgetItem(row_fmt.format(name=name, reason=reason), self.missing_list)
        if len(self.missing) > limit:
            QtWidgets.QListWidgetItem(_txt("DLG_IMPORT_MISSING_MORE_FMT").format(k=limit), self.missing_list)
        self.missing_list.setMaximumHeight(TH.ROW_H * 8)
        self.missing_list.setVisible(bool(self.missing))
        self.lay.addWidget(self.missing_list, 1)

        # ── 计数（在后）──
        count_txt = (_txt("DLG_IMPORT_MISSING_COUNT_FMT").format(n=len(self.missing))
                     if self.missing else "")
        self.count_label = _small_label(count_txt, self, _oname("DLG_IMPORT_REPORT_COUNT"))
        self.lay.addWidget(self.count_label)

        self.counts_label = QtWidgets.QLabel("\n".join(self._counts), self)
        self.counts_label.setObjectName(N.DLG_IMPORT_REPORT_TEXT)
        self.counts_label.setWordWrap(True)
        self.counts_label.setFont(ui_font())
        self.lay.addWidget(self.counts_label)

        rows = [row_fmt.format(name=n, reason=r) for n, r in self.missing]
        self._body_text = "\n".join(self._notes + [head] + rows + [count_txt] + self._counts).strip()
        self._detail_text = "\n".join(rows)
        self._add_buttons(ok_text=T.DONE_BTN_OK, with_cancel=False)

    def missing_names(self):
        return tuple(n for n, _r in self.missing)

    @classmethod
    def ask(cls, missing=(), counts=(), notes=(), parent=None):
        dlg = cls(missing=missing, counts=counts, notes=notes, parent=parent)
        return dlg._run()


# ═════════════════════════ ⑨ 批量填期望 ═════════════════════════
#: 批量填三选一
BATCH_MODES = ("const", "auto", "clear")
#: 批量填的范围两选一
BATCH_SCOPES = ("selected", "all")


class BatchFillDialog(_BaseDialog):
    """⑨ 批量填期望（C-298）：常量 / 取 auto / 清空 三选一 + 范围（选中列 / 全部）。

    构造：`BatchFillDialog(n_selected=0, n_total=0, parent=None)`（没选中列时「只填选中」置灰）。
    返回：`spec() -> {"mode": const|auto|clear, "value": int|None, "scope": selected|all}`。
    常量模式下数值走 `truth_edit.parse_int`（8 种写法），**非法就地标红且不关框**。
    """

    OBJECT_NAME = N.DLG_BATCH_FILL
    TITLE = T.DLG_BATCH_FILL_TITLE

    def __init__(self, n_selected=0, n_total=0, parent=None):
        super(BatchFillDialog, self).__init__(parent, width=420)
        self.n_selected = int(n_selected or 0)
        self.n_total = int(n_total or 0)
        self._body_text = T.DLG_BATCH_FILL_HINT
        self.lay.addWidget(_small_label(self._body_text, self, _oname("DLG_BATCH_FILL_HINT")))

        # ── 三选一 ──
        labels = _txt("DLG_BATCH_FILL_MODES")
        self.mode_btns = {}
        self._mode_group = QtWidgets.QButtonGroup(self)
        for key in BATCH_MODES:
            rb = QtWidgets.QRadioButton(labels[key], self)
            rb.setObjectName(_oname("DLG_BATCH_FILL_MODE_%s" % key.upper()))
            rb.setFont(ui_font())
            self._mode_group.addButton(rb)
            self.mode_btns[key] = rb
            self.lay.addWidget(rb)
            rb.toggled.connect(self._sync_value_enabled)
        self.mode_btns["const"].setChecked(True)

        self.value_edit = QtWidgets.QLineEdit(self)
        self.value_edit.setObjectName(N.DLG_BATCH_FILL_VALUE)
        self.value_edit.setFont(mono_font())
        self.value_edit.textChanged.connect(lambda _t=None: self.value_edit.setStyleSheet(""))
        self.lay.addWidget(self.value_edit)

        # ── 范围 ──
        self.lay.addWidget(_small_label(_txt("DLG_BATCH_FILL_SCOPE_LABEL"), self))
        self.scope_btns = {}
        self._scope_group = QtWidgets.QButtonGroup(self)
        scope_text = {"selected": _txt("DLG_BATCH_FILL_SCOPE_SELECTED_FMT").format(n=self.n_selected),
                      "all": _txt("DLG_BATCH_FILL_SCOPE_ALL_FMT").format(n=self.n_total)}
        for key in BATCH_SCOPES:
            rb = QtWidgets.QRadioButton(scope_text[key], self)
            rb.setObjectName(_oname("DLG_BATCH_FILL_SCOPE_%s" % key.upper()))
            rb.setFont(ui_font())
            self._scope_group.addButton(rb)
            self.scope_btns[key] = rb
            self.lay.addWidget(rb)
        self.scope_btns["selected"].setEnabled(self.n_selected > 0)
        self.scope_btns["selected" if self.n_selected > 0 else "all"].setChecked(True)

        self._add_error_label(_oname("DLG_BATCH_FILL_ERROR"))
        self._add_buttons()
        self._sync_value_enabled()

    def _sync_value_enabled(self, _on=None):
        edit = getattr(self, "value_edit", None)       # 建三个 radio 时就会触发 toggled，那时还没有输入框
        if edit is not None:
            edit.setEnabled(self.mode() == "const")

    def mode(self):
        for key, rb in self.mode_btns.items():
            if rb.isChecked():
                return key
        return BATCH_MODES[0]

    def scope(self):
        for key, rb in self.scope_btns.items():
            if rb.isChecked():
                return key
        return "all"

    def spec(self):
        """→ {"mode", "value", "scope"}；value 只有 const 模式有值（解析不了 → None）。"""
        value = None
        txt = self.value_edit.text().strip()
        if self.mode() == "const" and txt:
            try:
                value = TE.parse_int(txt)
            except ValueError:
                value = None
        return {"mode": self.mode(), "value": value, "scope": self.scope()}

    def validate(self):
        if self.mode() != "const":
            return True, ""
        txt = self.value_edit.text().strip()
        try:
            if txt == "":
                raise ValueError(txt)        # 「填固定值」却没填 = 打了一半，别让 parse_int 的空串=0 静默兜底
            TE.parse_int(txt)
        except ValueError:
            self.value_edit.setStyleSheet(_bad_edit_style())
            return False, T.TRUTH_PARSE_FAILED_FMT.format(text=txt)
        self.value_edit.setStyleSheet("")
        return True, ""

    @classmethod
    def ask(cls, n_selected=0, n_total=0, parent=None):
        dlg = cls(n_selected=n_selected, n_total=n_total, parent=parent)
        ok = dlg._run()
        return dlg.spec() if ok else None


# ═════════════════════════ ⑩ .sv 导出选项 ═════════════════════════
#: .sv 范围三选（键与 `exports.SCOPE_LABEL` 一致；文案走 `terms.EXPORT_SV_SCOPES`）
EXPORT_SCOPES = ("all", "pos", "neg")
#: 三个开关（键与 `exports.EXPORT_OPTION_DEFAULTS` 一致）
EXPORT_FLAGS = ("comments", "sv_summary", "owner_in_msg")
#: `defaults=None` 时的兜底 —— **必须**与 `exports.EXPORT_OPTION_DEFAULTS 等值**（本模块不 import
#: exports，由 `tests/test_ui_dialogs.py::test_dlg_export_options_defaults_match_exports` 机械锁住）。
FALLBACK_EXPORT_OPTIONS = {"scope": "all", "comments": False, "sv_summary": False, "owner_in_msg": False}


class ExportOptionsDialog(_BaseDialog):
    """⑩ .sv 导出选项弹层的内容（C-159 注释 / C-160 末尾汇总 / C-161 写 owner + 范围三选）。

    构造：`ExportOptionsDialog(defaults, parent=None)` —— **默认值由调用方传入**
    （`exports.EXPORT_OPTION_DEFAULTS` 或 `exports.load_export_options(settings)`，C-162/C-163）；
    本模块**不 import exports**，只做控件与返回 dict。
    返回：`options() -> {"scope", "comments", "sv_summary", "owner_in_msg"}`（形状与 exports 完全一致）。
    """

    OBJECT_NAME = _oname("DLG_EXPORT_OPTIONS")
    TITLE = TITLES["export_options"]

    def __init__(self, defaults=None, parent=None):
        super(ExportOptionsDialog, self).__init__(parent,
                                                  object_name=_oname("DLG_EXPORT_OPTIONS"), width=340)
        opts = dict(FALLBACK_EXPORT_OPTIONS)
        opts.update({k: v for k, v in (defaults or {}).items() if k in opts})
        self.defaults = opts
        self._body_text = self.TITLE

        self.checks = {}
        for key in EXPORT_FLAGS:
            cb = QtWidgets.QCheckBox(T.EXPORT_SV_OPTIONS[key], self)
            cb.setObjectName(_oname("DLG_EXPORT_OPT_%s" % key.upper()))
            cb.setFont(ui_font())
            cb.setChecked(bool(opts[key]))
            self.checks[key] = cb
            self.lay.addWidget(cb)

        self.lay.addWidget(_small_label(_txt("DLG_EXPORT_SCOPE_LABEL"), self))
        self.scopes = {}
        self._scope_group = QtWidgets.QButtonGroup(self)
        for key in EXPORT_SCOPES:
            rb = QtWidgets.QRadioButton(T.EXPORT_SV_SCOPES[key], self)
            rb.setObjectName(_oname("DLG_EXPORT_SCOPE_%s" % key.upper()))
            rb.setFont(ui_font())
            self._scope_group.addButton(rb)
            self.scopes[key] = rb
            self.lay.addWidget(rb)
        self.scopes.get(opts["scope"], self.scopes["all"]).setChecked(True)
        self._add_buttons()

    def options(self):
        """→ {"scope", "comments", "sv_summary", "owner_in_msg"}。"""
        scope = next((k for k, rb in self.scopes.items() if rb.isChecked()), self.defaults["scope"])
        out = {"scope": scope}
        out.update({k: bool(cb.isChecked()) for k, cb in self.checks.items()})
        return out

    @classmethod
    def ask(cls, defaults=None, parent=None):
        dlg = cls(defaults=defaults, parent=parent)
        ok = dlg._run()
        return dlg.options() if ok else None
