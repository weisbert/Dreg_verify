# -*- coding: utf-8 -*-
"""signal_list.py —— ③ 清单区（GUI v2 Phase C1-b）。

架构 §1.3①「两级 QTreeView」的落地：

    SignalListModel(QAbstractItemModel)   父 = 信号行，子 = 行内原因块占位行（同一时刻只允许一行）
    SignalListProxy(QSortFilterProxyModel) 递归筛选关、子行恒接受、排序只作用父行（ListRole.SORT）
    SignalListView(QTreeView)             点状态格 → 展开唯一一行 + setIndexWidget(子行, ReasonWidget)
    ReasonWidget(QFrame)                  Design 发明①：琥珀左边条 + 标题 + 正文 + 「去诊断 · …」按钮
    SignalListPanel(QWidget)              头部计数 / 列设置 / 排序 + 视图 + 底部工具条 6 键

本模块只做「拿模型 → 画 → 把用户动作转成 state 调用」：
  · 一切用户可见文案取自 `terms`，色值 / 尺寸取自 `theme`，objectName 取自 `names`，列与 role 取自 `contracts`；
  · 一切后端文本（note / issues / normalized_note / 解析异常全文）先过 `terms.scrub`（不变量 I-12）；
  · 勾选 / 反例 / 当前信号 / 列设置持久化一律经 `state`，本模块不碰文件（不变量 I-07）。

契约 ID（附录 A「LIST」31 条）：C-007 C-008 C-009 C-010 C-011 C-012 C-013 C-014 C-015 C-016
C-017 C-018 C-019 C-020 C-021 C-022 C-023 C-032 C-033 C-034 C-035 C-036 C-037 C-039 C-040
C-041 C-044 C-045 C-046 C-127 C-290。

对 `ui/state.py`（C1-a）的接口假设见模块末尾 `STATE_REQUIREMENTS`（C1-int 接线时逐条核对）。
"""

import contextlib
import re
import string

from PySide6 import QtCore, QtGui, QtWidgets

from . import contracts as CT
from . import names as N
from . import terms as T
from . import theme as TH
from .widgets import FlowLayout, mono_font as _mono_font, ui_font as _ui_font

Qt = QtCore.Qt
LC = CT.ListCol
LR = CT.ListRole

__all__ = ["SignalListModel", "SignalListProxy", "SignalListView", "SignalListDelegate",
           "ReasonWidget", "SignalListPanel", "ColumnsDialog", "PasteNamesDialog",
           "build_reason_block", "status_key_of", "STATE_REQUIREMENTS"]

#: 状态排序权重（升序 = 有问题的在前，一眼看见要处理的；降序 = 可建的在前）
_TONE_ORDER = {"bad": 0, "warn": 1, "note": 2, "ok": 3}

#: 「仅有问题」筛选口径 = 非 ok 档（note 的只读回读 / 裸名不算问题）
_PROBLEM_TONES = ("warn", "bad")

#: 状态筛选两档的取值。筛选行发的是 `filter_bar.STATUS_VALUES` 的 "ok" / "issues"；
#: 这里连中文项与 clean/problem 一起认——两个模块各写各的字面量，漏一个就是「筛了等于没筛」，
#: 而且界面上看不出来（行数不变，用户以为本来就这么多）。
_STATUS_OK_KEYS = ("ok", "clean", T.STATUS_FILTER_ITEMS[1])
_STATUS_PROBLEM_KEYS = ("issues", "problem", T.STATUS_FILTER_ITEMS[2])

#: 原因块正文里「点名的 Excel 行号」——引擎暂未给结构化 meta 时从 issues 文本里取（C-127 / copy_rows）
_ROW_RE = re.compile(r"第\s*(\d+)\s*行")

_FORMATTER = string.Formatter()


# ═════════════════════════════ 小工具 ═════════════════════════════
def _checked(value):
    """Qt.CheckState（枚举 / int / None）→ bool。"""
    if value is None:
        return False
    return int(value.value if hasattr(value, "value") else value) == int(Qt.Checked.value)


def _event_pos(event):
    """QMouseEvent 的位置（Qt6 的 position() / Qt5 的 pos()）。"""
    if hasattr(event, "position"):
        return event.position().toPoint()
    return event.pos()                               # pragma: no cover - Qt5 回退


def _scrub(text):
    """后端文本 → 界面文本（不变量 I-12：本模块所有后端串的唯一出口）。"""
    return T.scrub(text or "")


def _scrub_lines(values):
    return [x for x in (_scrub(v) for v in (values or [])) if x]


def status_key_of(model):
    """模型行 → `terms.STATUS` 的键（C-014 / C-016 / C-041）。

    引擎给了 `status_detail`（C0-a §7-2 八档）就用它；只给四档 status 时按 `STATUS_FALLBACK` 映射。"""
    m = model or {}
    key = str(m.get("status_detail") or "").strip()
    if key in T.STATUS:
        return key
    st = str(m.get("status") or "").strip()
    if st in T.STATUS_FALLBACK:
        return T.STATUS_FALLBACK[st]
    return st if st in T.STATUS else "error"


def _tone_of(model):
    return T.STATUS[status_key_of(model)][1]


class _Blanks(dict):
    """`str.format_map` 用：缺的占位符留空而不是 KeyError（缺哪个由调用方判断是否退化）。"""

    def __missing__(self, key):
        return ""


def _reason_rows(model):
    """原因里点名的 Excel 行号（C-127 / copy_rows）。引擎给了结构化 meta 用 meta，否则从
    scrub 过的 issues 文本里取「第 N 行」—— 这是显示层的取值，不是重新判断。"""
    meta = dict((model or {}).get("issues_meta") or {})
    rows = meta.get("rows")
    if rows:
        return tuple(int(x) for x in rows)
    return tuple(int(x) for x in _ROW_RE.findall("\n".join(_scrub_lines((model or {}).get("issues")))))


def _reason_values(model, rows=()):
    """原因块模板可填的占位符。引擎还没给结构化 meta 时只有这几个是确定的。"""
    m = model or {}
    meta = dict(m.get("issues_meta") or {})            # C0-a 后续补；现在恒空
    vals = {k: v for k, v in meta.items() if v not in (None, "")}
    vals.setdefault("signal", m.get("disp") or m.get("name") or "")
    vals.setdefault("group", m.get("disp") or m.get("name") or "")
    vals.setdefault("net", m.get("out_net") or m.get("probe_net") or "")
    vals.setdefault("prefix", m.get("prefix") or "")
    if rows:
        vals.setdefault("n_bad", len(rows))            # 点名了几行就是几条——与「导出这 N 行」同一批
    detail = "\n".join(_scrub_lines(m.get("issues")) + ([_scrub(m.get("note"))] if m.get("note") else []))
    vals["detail"] = detail
    return vals


def _fields(template):
    """模板里的占位符名集合（`{a} {b}` → {"a", "b"}）。"""
    return {f for _, f, _, _ in _FORMATTER.parse(template or "") if f}


def _fillable(template, vals):
    """模板里的占位符是不是**全部**都有非空值（有一个填不上就整段退化，免得界面上露出 `{n_bad}`）。"""
    need = _fields(template)
    return all(str(vals.get(f, "")).strip() for f in need)


def _strip_placeholders(template):
    """兜底：把填不上的占位符连同多余空格去掉（按钮文案没有「退化成全文」这条路）。"""
    return re.sub(r"\s{2,}", " ", re.sub(r"\{[a-z_]+\}", "", template or "")).strip()


def build_reason_block(model):
    """模型行 → `contracts.ReasonBlock`（没有对应模板 = 这一档没有行内原因块，返回 None）。

    正文：模板占位符全部填得上就按 `terms.REASON_TEMPLATES` 拼；填不上（引擎还没给结构化 meta）
    按架构 §6.3 退化成 `{detail}` = scrub 后的 issues / note 全文，**标题不变**；两者都空时
    退到该档的悬停解释，保证原因块永远不是一片空白。"""
    key = status_key_of(model)
    tpl = T.REASON_TEMPLATES.get(key)
    if tpl is None:
        return None
    title_tpl, body_tpl, action_tpl, target = tpl
    rows = _reason_rows(model)
    vals = _reason_values(model, rows)
    title = (title_tpl.format_map(_Blanks(vals)) if _fillable(title_tpl, vals)
             else T.STATUS[key][0])               # 标题里的数字填不上 → 用该档标签，不留 {n_bad} 露在界面上
    body = body_tpl.format_map(_Blanks(vals)) if _fillable(body_tpl, vals) else vals.get("detail", "")
    if not str(body).strip():
        body = T.STATUS[key][2]
    action = (action_tpl.format_map(_Blanks(vals)) if _fillable(action_tpl, vals)
              else _strip_placeholders(action_tpl))
    return CT.ReasonBlock(title=title, body=body, action_label=action, action_target=target,
                          rows=rows, payload={"name": (model or {}).get("name", ""), "status": key})


# ═════════════════════════════ model ═════════════════════════════
class SignalListModel(QtCore.QAbstractItemModel):
    """清单两级 model（架构 §2.3）。

    父行 = 一个信号（`providers.*.skeleton_models()` / `view_models(lite=True)` 的模型 dict）；
    子行 = 行内原因块的占位行（`IS_REASON_ROW=True` + `REASON`，其余 role 返回 None，不可选中不可勾）。
    同一时刻最多展开一行 —— 展开 / 收起都是真正的 insert / removeRows，子行因此随父行一起被
    proxy 排序 / 筛选（C-037 排序后原因块仍贴着自己那一行）。"""

    #: 勾选列 / 反例列被用户点动了（panel 转成 state.set_checked / set_neg）
    checkToggled = QtCore.Signal(str, bool)
    negToggled = QtCore.Signal(str, bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._models = []
        self._index = {}
        self._checked = None          # None = 全勾（state.checked() 的默认态，C-243）
        self._negs = set()
        self._expanded = -1           # 展开中的源行号（-1 = 没展开）
        self._reason_h = TH.ROW_H * 4
        self._prefix_map = {}         # {小写网名: 层级前缀} —— C-020 输入侧命中
        self._dot = None

    # ── 装载 / 增量升级 ──
    def load(self, models, checked=None, negs=()):
        """整表重建（换表 / 换范围 / worker finished）——唯一允许 reset 的地方。"""
        self.beginResetModel()
        self._models = [dict(m or {}) for m in (models or [])]
        self._index = {str(m.get("name", "")): i for i, m in enumerate(self._models)}
        self._checked = None if checked is None else set(checked)
        self._negs = set(negs or ())
        self._expanded = -1
        self.endResetModel()

    def update_row(self, model):
        """worker.signalDone → 该行从「分析中」升级：只发 dataChanged，**不重建**（裁决⑨ / C-276）。"""
        name = str((model or {}).get("name", ""))
        r = self._index.get(name)
        if r is None:
            return False
        self._models[r] = dict(model)
        self.dataChanged.emit(self.index(r, 0), self.index(r, self.columnCount() - 1))
        if self._expanded == r:
            parent = self.index(r, 0)
            self.dataChanged.emit(self.index(0, 0, parent), self.index(0, 0, parent))
        return True

    def set_check_state(self, checked=None, negs=None):
        """state.checksChanged / negsChanged → 刷新两个勾选列（不重建）。"""
        if checked is not None or self._checked is not None:
            self._checked = None if checked is None else set(checked)
        if negs is not None:
            self._negs = set(negs)
        if self._models:
            self.dataChanged.emit(self.index(0, LC.CHECK), self.index(len(self._models) - 1, LC.NEG))

    def set_prefix_map(self, mapping):
        """探针前缀映射（state.probe_prefixes）—— 探针前缀列的输入侧命中要用（C-020）。"""
        self._prefix_map = {str(k).lower(): str(v) for k, v in dict(mapping or {}).items()}
        if self._models:
            self.dataChanged.emit(self.index(0, LC.PREFIX), self.index(len(self._models) - 1, LC.PREFIX))

    def set_reason_height(self, h):
        self._reason_h = max(TH.ROW_H, int(h))
        if self._expanded >= 0:
            parent = self.index(self._expanded, 0)
            self.dataChanged.emit(self.index(0, 0, parent), self.index(0, 0, parent))

    # ── 展开 / 收起唯一一行 ──
    def set_expanded(self, name):
        """展开 name 的原因子行（None / "" = 收起）。同一时刻只允许一行。"""
        want = self._index.get(str(name or ""), -1) if name else -1
        if want == self._expanded:
            return self._expanded
        if self._expanded >= 0:
            parent = self.index(self._expanded, 0)
            self.beginRemoveRows(parent, 0, 0)
            self._expanded = -1
            self.endRemoveRows()
        if want >= 0 and self.reason_of(want) is not None:
            parent = self.index(want, 0)
            self.beginInsertRows(parent, 0, 0)
            self._expanded = want
            self.endInsertRows()
        return self._expanded

    @property
    def expanded_name(self):
        return self.name_at(self._expanded) if self._expanded >= 0 else ""

    # ── 查询 ──
    def rows(self):
        return list(self._models)

    def model_at(self, row):
        return self._models[row] if 0 <= row < len(self._models) else {}

    def name_at(self, row):
        return str(self.model_at(row).get("name", ""))

    def index_of(self, name):
        r = self._index.get(str(name or ""), -1)
        return self.index(r, 0) if r >= 0 else QtCore.QModelIndex()

    def reason_of(self, row):
        return build_reason_block(self.model_at(row))

    def is_checked(self, name):
        return self._checked is None or str(name) in self._checked

    def is_neg(self, name):
        return str(name) in self._negs

    # ── QAbstractItemModel 必备 ──
    def index(self, row, column=0, parent=QtCore.QModelIndex()):
        if not self.hasIndex(row, column, parent):
            return QtCore.QModelIndex()
        if parent.isValid():
            return self.createIndex(row, column, parent.row() + 1)
        return self.createIndex(row, column, 0)

    def parent(self, index):
        if not index.isValid():
            return QtCore.QModelIndex()
        pid = int(index.internalId())
        if pid == 0:
            return QtCore.QModelIndex()
        return self.createIndex(pid - 1, 0, 0)

    def rowCount(self, parent=QtCore.QModelIndex()):
        if not parent.isValid():
            return len(self._models)
        if int(parent.internalId()) != 0:
            return 0
        return 1 if parent.row() == self._expanded else 0

    def columnCount(self, parent=QtCore.QModelIndex()):
        return len(LC)

    def flags(self, index):
        if not index.isValid():
            return Qt.NoItemFlags
        if int(index.internalId()):                      # 原因子行：不可选中、不可勾
            return Qt.ItemIsEnabled
        f = Qt.ItemIsEnabled | Qt.ItemIsSelectable
        if index.column() in (LC.CHECK, LC.NEG):
            f |= Qt.ItemIsUserCheckable
        return f

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if orientation != Qt.Horizontal:
            return None
        key = CT.LIST_COL_KEYS.get(LC(section)) if section in [int(c) for c in LC] else None
        if role == Qt.DisplayRole:
            return T.LIST_HEADERS.get(key, "")
        if role == Qt.ToolTipRole and key == "assert_id":
            return T.LIST_ASSERT_TIP                                    # C-010
        if role == Qt.TextAlignmentRole:
            return int(Qt.AlignVCenter | (Qt.AlignRight if key == "ntest" else Qt.AlignLeft))
        return None

    def setData(self, index, value, role=Qt.EditRole):
        """唯一写入口：两个勾选列。model 只更新自己的缓存并发信号，落盘由 state 负责（I-07）。"""
        if not index.isValid() or int(index.internalId()) or role != Qt.CheckStateRole:
            return False
        col = index.column()
        if col not in (LC.CHECK, LC.NEG):
            return False
        name = self.name_at(index.row())
        on = int(value.value if hasattr(value, "value") else value) == int(Qt.Checked.value)
        if col == LC.CHECK:
            if self._checked is None:
                self._checked = {self.name_at(r) for r in range(len(self._models))}
            self._checked.add(name) if on else self._checked.discard(name)
            self.checkToggled.emit(name, on)
        else:
            self._negs.add(name) if on else self._negs.discard(name)
            self.negToggled.emit(name, on)
        self.dataChanged.emit(index, index)
        return True

    # ── data ──
    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        if int(index.internalId()):
            return self._reason_data(index, role)
        return self._row_data(index, role)

    def _reason_data(self, index, role):
        parent_row = int(index.internalId()) - 1
        if role == LR.IS_REASON_ROW:
            return True
        if role == LR.REASON:
            return self.reason_of(parent_row)
        if role == LR.NAME:
            return self.name_at(parent_row)
        if role == Qt.SizeHintRole:
            return QtCore.QSize(0, self._reason_h)
        if role == LR.SORT:
            return 0
        return None

    def _row_data(self, index, role):
        m = self.model_at(index.row())
        col = LC(index.column())
        key = status_key_of(m)
        tone = T.STATUS[key][1]
        pending = key == "pending"

        if role == Qt.DisplayRole:
            return self._display(m, col, key)
        if role == Qt.CheckStateRole:
            if col == LC.CHECK:
                return Qt.Checked if self.is_checked(m.get("name", "")) else Qt.Unchecked
            if col == LC.NEG:
                return Qt.Checked if self.is_neg(m.get("name", "")) else Qt.Unchecked
            return None
        if role == Qt.ToolTipRole:
            return self._tooltip(m, col, key)
        if role == Qt.DecorationRole and col == LC.NAME and m.get("supplement"):
            return self._amber_dot()                                    # C-039
        if role == Qt.FontRole:
            if col in (LC.NAME, LC.NTEST, LC.PREFIX, LC.EXPR, LC.ASSERT_ID):
                return _mono_font()                                     # C-009 / C-021 信号名等一律 Consolas
            return _ui_font()
        if role == Qt.ForegroundRole:
            if pending:
                return QtGui.QColor(TH.DISABLED_TEXT)                   # 骨架态灰显（裁决⑨）
            if col == LC.PREFIX and self._prefix_parts(m):
                return QtGui.QColor(TH.BLUE_DARK)                       # C-017 蓝 = 已配
            return None
        if role == Qt.TextAlignmentRole:
            if col == LC.NTEST:
                return int(Qt.AlignRight | Qt.AlignVCenter)
            if col in (LC.CHECK, LC.NEG, LC.STATUS):
                return int(Qt.AlignCenter if col != LC.STATUS else (Qt.AlignLeft | Qt.AlignVCenter))
            return int(Qt.AlignLeft | Qt.AlignVCenter)
        if role == Qt.SizeHintRole:
            return QtCore.QSize(0, TH.ROW_H)
        if role == LR.NAME:
            return str(m.get("name", ""))
        if role == LR.STATUS_KEY:
            return key
        if role == LR.TONE:
            return tone
        if role == LR.ISSUES:
            return _scrub_lines(m.get("issues"))
        if role == LR.NOTE:
            return _scrub(m.get("note"))
        if role == LR.REASON:
            return self.reason_of(index.row())
        if role == LR.MODEL:
            return dict(m)
        if role == LR.PENDING:
            return pending
        if role == LR.IS_REASON_ROW:
            return False
        if role == LR.SUPPLEMENT:
            return bool(m.get("supplement"))
        if role == LR.PREFIX_TIP:
            return self._prefix_tip(m)
        if role == LR.EXPR:
            return _scrub(m.get("expr"))
        if role == LR.INPUT_NAMES:
            return [str(x) for x in (m.get("input_names") or [])]
        if role == LR.SORT:
            return self._sort_key(m, col, key, tone)
        return None

    def _display(self, m, col, key):
        if col in (LC.CHECK, LC.NEG):
            return ""
        if col == LC.NAME:
            return str(m.get("disp") or m.get("name") or "")            # C-009 带位宽切片
        if col == LC.STATUS:
            label = T.STATUS[key][0]
            if _scrub(m.get("normalized_note")):
                label += " ⚙"                                            # C-040 嵌套 mux 已折叠
            return label
        if col == LC.NTEST:
            n = m.get("n_vectors")
            return "" if n in (None, "") else str(n)                    # C-021（分析中留空）
        if col == LC.OWNER:
            return str(m.get("owner") or "")                            # C-011
        if col == LC.ASSERT_ID:
            return str(m.get("assert_id") or "")                        # C-010
        if col == LC.KIND:
            return T.KIND_LABELS.get(str(m.get("kind") or ""), str(m.get("kind") or ""))   # C-012
        if col == LC.FORM:
            return T.FORM_LABELS.get(str(m.get("form") or ""), _scrub(m.get("form_label")))  # C-013
        if col == LC.PREFIX:
            return "；".join(self._prefix_parts(m))                      # C-017 / C-019 / C-020
        if col == LC.EXPR:
            return _scrub(m.get("expr"))                                # C-046
        return ""

    def _prefix_parts(self, m):
        """探针前缀列的单元格文案：① level_shift 自动解析 ② 输出配的前缀 ③ 输入侧命中。"""
        parts = []
        probe = str(m.get("probe_net") or "")
        name = str(m.get("name") or "")
        if probe and probe.lower() != name.lower() and probe.lower().endswith("_ls"):
            parts.append(T.LIST_PREFIX_LEVEL_SHIFT_FMT.format(net=probe))          # C-019
        if m.get("prefix"):
            parts.append(str(m.get("prefix")))                                     # C-017
        for inp in (m.get("input_names") or []):
            pfx = self._prefix_map.get(str(inp).lower())
            if pfx:
                parts.append(T.LIST_PREFIX_INPUT_HIT_FMT.format(input=inp, prefix=pfx))   # C-020
        return parts

    def _prefix_tip(self, m):
        """C-018：探针网真名 + 断言完整路径（配了 / 没配两种文案）。"""
        net = str(m.get("out_net") or m.get("probe_net") or m.get("name") or "")
        pfx = str(m.get("prefix") or "")
        if pfx:
            return T.LIST_PREFIX_TIP_SET_FMT.format(net=net, prefix=pfx)
        return T.LIST_PREFIX_TIP_UNSET_FMT.format(net=net)

    def _tooltip(self, m, col, key):
        if col == LC.STATUS:
            tips = [T.STATUS[key][2]]                                   # C-016 八档逐档解释
            norm = _scrub(m.get("normalized_note"))
            if norm:
                tips.append(T.LIST_NORMALIZED_TIP.format(note=norm))    # C-040
            issues = _scrub_lines(m.get("issues"))
            if key == "error" and issues:
                tips.append(T.LIST_STATUS_ERROR_FMT.format(error="\n".join(issues)))   # C-007
            elif issues:
                tips.extend(issues)                                     # C-015 列出全部问题原因
            note = _scrub(m.get("note"))
            if note:
                tips.append(note)
            return "\n".join(tips)
        if col == LC.NAME and m.get("supplement"):
            return T.LIST_SUPPLEMENT_TIP.format(note=_scrub(m.get("normalized_note")) or
                                                str(m.get("name") or ""))            # C-039
        if col == LC.PREFIX:
            return self._prefix_tip(m)                                  # C-018
        if col == LC.ASSERT_ID:
            return T.LIST_ASSERT_TIP                                    # C-010
        if col == LC.EXPR:
            return _scrub(m.get("expr"))
        return None

    def _sort_key(self, m, col, key, tone):
        if col == LC.CHECK:
            return 0 if self.is_checked(m.get("name", "")) else 1
        if col == LC.NEG:
            return 0 if self.is_neg(m.get("name", "")) else 1
        if col == LC.STATUS:
            return _TONE_ORDER.get(tone, 9)
        if col == LC.NTEST:
            n = m.get("n_vectors")
            return -1 if n in (None, "") else int(n)
        return str(self._display(m, col, key)).lower()

    def _amber_dot(self):
        """RTL 补充逻辑的琥珀圆点（C-039 / C-215）。"""
        if self._dot is None:
            pm = QtGui.QPixmap(10, 10)
            pm.fill(Qt.transparent)
            p = QtGui.QPainter(pm)
            p.setRenderHint(QtGui.QPainter.Antialiasing, True)
            p.setPen(Qt.NoPen)
            p.setBrush(QtGui.QColor(TH.AMBER_CHECK))
            p.drawEllipse(1, 1, 8, 8)
            p.end()
            self._dot = QtGui.QIcon(pm)
        return self._dot


# ═════════════════════════════ proxy ═════════════════════════════
class SignalListProxy(QtCore.QSortFilterProxyModel):
    """筛选 + 排序（架构 §2.3）。

    · `filterAcceptsRow`：source_parent 有效（原因子行）→ 恒 True，于是原因块永远贴着父行；
    · `lessThan`：只用 `ListRole.SORT`（父行之间比；子行只有一条，比不着）；
    · 递归筛选关闭（`setRecursiveFilteringEnabled(False)`）——父行被筛掉，子行不该顶上来。"""

    #: QTreeView 的表头点击走 `model.sort()` —— 用它把「排序前收起 / 排序后重挂」钉死在排序两侧
    sortAboutToStart = QtCore.Signal()
    sortFinished = QtCore.Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._owners = None
        self._kind = ""
        self._status = ""
        self._rx = None
        try:
            self.setRecursiveFilteringEnabled(False)
        except AttributeError:                       # pragma: no cover - Qt5 回退
            pass
        self.setSortRole(int(LR.SORT))
        self.setDynamicSortFilter(True)

    # ── 筛选条件（filter_bar 调）──
    def set_filters(self, owners=None, kind="", status="", regex=""):
        """② 的一份筛选。签名 = §2.3，`filter_bar.filterChanged` 的 dict 可直接 `**` 进来。

        ⚠ `owners` **空集合 = 不按 owner 筛**（与 `filter_bar.match_row` 同口径）：
        筛选行一个 owner 都没勾时发的就是空集合，把它当成「只要 owner 在这 0 个里面的行」，
        整张清单会一行不剩 —— C1-int 端到端第一次载 mirror 就是这个现象。"""
        self._owners = {str(o) for o in owners} if owners else None
        self._kind = str(kind or "")
        self._status = str(status or "")
        self._rx = None
        if regex:
            try:
                self._rx = re.compile(regex, re.I)
            except re.error:
                self._rx = re.compile(re.escape(str(regex)), re.I)      # 写坏的正则按字面串搜，不报错
        self._invalidate()

    def _invalidate(self):
        """重算筛选。PySide6 6.11 把 invalidateFilter / invalidateRowsFilter 都标了 deprecated，
        只剩 invalidate() 干净（它连排序一起重来，对只有一条子行的两级表没有代价）。"""
        self.invalidate()

    def sort(self, column, order=Qt.AscendingOrder):
        self.sortAboutToStart.emit()
        super().sort(column, order)
        self.sortFinished.emit()

    # ── Qt 钩子 ──
    def filterAcceptsRow(self, source_row, source_parent):
        if source_parent.isValid():
            return True                                                 # 原因子行恒接受
        ok, _by_input = self._accepts(source_row)
        return ok

    def lessThan(self, left, right):
        a = left.data(int(LR.SORT))
        b = right.data(int(LR.SORT))
        if a is None or b is None:
            return super().lessThan(left, right)
        try:
            return a < b
        except TypeError:                            # pragma: no cover - 类型不齐时退字符串
            return str(a) < str(b)

    # ── 计数（C-031 / 清单头部计数）──
    def n_total(self):
        src = self.sourceModel()
        return src.rowCount() if src is not None else 0

    def n_visible(self):
        return self.rowCount()

    def n_by_input(self):
        """可见行里「只按输入信号名命中」的条数（C-031）。"""
        n = 0
        for r in range(self.n_total()):
            ok, by_input = self._accepts(r)
            if ok and by_input:
                n += 1
        return n

    def visible_names(self):
        return [self.index(r, LC.NAME).data(int(LR.NAME)) or "" for r in range(self.rowCount())]

    # ── 内部 ──
    def _accepts(self, source_row):
        src = self.sourceModel()
        if src is None:
            return (True, False)
        m = src.model_at(source_row)
        if not m:
            return (False, False)
        if self._owners is not None and str(m.get("owner") or "") not in self._owners:
            return (False, False)
        if self._kind and str(m.get("kind") or "") != self._kind:
            return (False, False)
        if self._status:
            tone = _tone_of(m)
            if self._status in _STATUS_OK_KEYS and tone != "ok":
                return (False, False)
            if self._status in _STATUS_PROBLEM_KEYS and tone not in _PROBLEM_TONES:
                return (False, False)
        if self._rx is None:
            return (True, False)
        head = " ".join(str(m.get(k) or "") for k in ("name", "disp", "assert_id"))
        head += " " + _scrub(m.get("expr"))
        hit_head = bool(self._rx.search(head))
        hit_input = any(self._rx.search(str(x)) for x in (m.get("input_names") or []))
        return (hit_head or hit_input, (hit_input and not hit_head))


# ═════════════════════════════ delegate ═════════════════════════════
class SignalListDelegate(QtWidgets.QStyledItemDelegate):
    """勾选 / 反例的 15px 圆角方块 + 状态徽标（Design ③ 的 `box()` 与 `stS`）。

    勾选列点在格子里任意位置都算（默认 delegate 只认 13px 指示器矩形——那对 30px 宽的列
    等于「只有点准了才算」，IC 工程师会以为勾不上）。"""

    BOX = 15

    def paint(self, painter, option, index):
        if index.data(int(LR.IS_REASON_ROW)):
            painter.fillRect(option.rect, QtGui.QColor(TH.REASON_BG))   # ReasonWidget 盖在上面
            return
        col = index.column()
        if col in (LC.CHECK, LC.NEG):
            self._paint_box(painter, option, index, col)
            return
        if col == LC.STATUS:
            self._paint_status(painter, option, index)
            return
        super().paint(painter, option, index)

    @staticmethod
    def _fill_bg(painter, option):
        """行底色：选中 `SELECTED_BG` / 斑马纹 `ZEBRA` / 普通白（自绘格子不走 style，免得
        默认 delegate 又画一遍自己的勾选指示器）。"""
        if option.state & QtWidgets.QStyle.State_Selected:
            painter.fillRect(option.rect, QtGui.QColor(TH.SELECTED_BG))
            return
        alt = False
        try:
            alt = bool(option.features & QtWidgets.QStyleOptionViewItem.Alternate)
        except TypeError:                            # pragma: no cover - 枚举差异
            pass
        painter.fillRect(option.rect, QtGui.QColor(TH.ZEBRA if alt else TH.WHITE))

    def _paint_box(self, painter, option, index, col):
        self._fill_bg(painter, option)
        on = _checked(index.data(Qt.CheckStateRole))
        r = QtCore.QRect(0, 0, self.BOX, self.BOX)
        r.moveCenter(option.rect.center())
        painter.save()
        painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
        accent = QtGui.QColor(TH.BLUE if col == LC.CHECK else TH.AMBER_CHECK)
        if on:
            painter.setPen(QtGui.QPen(accent, 1))
            painter.setBrush(accent)
            painter.drawRoundedRect(r, 3, 3)
            pen = QtGui.QPen(QtGui.QColor(TH.WHITE), 1.8)
            painter.setPen(pen)
            if col == LC.CHECK:                                          # ✓
                painter.drawLine(r.left() + 3, r.center().y(), r.center().x() - 1, r.bottom() - 4)
                painter.drawLine(r.center().x() - 1, r.bottom() - 4, r.right() - 3, r.top() + 4)
            else:                                                        # ✕
                painter.drawLine(r.left() + 4, r.top() + 4, r.right() - 4, r.bottom() - 4)
                painter.drawLine(r.right() - 4, r.top() + 4, r.left() + 4, r.bottom() - 4)
        else:
            painter.setPen(QtGui.QPen(QtGui.QColor(TH.CHECK_OFF_BORDER), 1))
            painter.setBrush(QtGui.QColor(TH.WHITE))
            painter.drawRoundedRect(r, 3, 3)
        painter.restore()

    def _paint_status(self, painter, option, index):
        self._fill_bg(painter, option)
        tone = index.data(int(LR.TONE)) or "note"
        fg, bg = TH.TONE_COLORS.get(tone, TH.TONE_COLORS["note"])
        text = str(index.data(Qt.DisplayRole) or "")
        rect = option.rect.adjusted(4, 3, -4, -3)
        painter.save()
        painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QtGui.QColor(bg))
        painter.drawRoundedRect(rect, 3, 3)
        painter.setFont(_ui_font(TH.FS_UI_SMALL))
        pen_color = QtGui.QColor(TH.DISABLED_TEXT) if index.data(int(LR.PENDING)) else QtGui.QColor(fg)
        painter.setPen(pen_color)
        tr = rect.adjusted(6, 0, -6, 0)
        painter.drawText(tr, int(Qt.AlignLeft | Qt.AlignVCenter), text)
        if tone in ("warn", "bad", "note"):                              # 点状下划线 = 点得开
            fm = QtGui.QFontMetrics(painter.font())
            w = min(fm.horizontalAdvance(text), tr.width())
            y = tr.center().y() + fm.ascent() // 2 + 1
            pen = QtGui.QPen(pen_color, 1)
            pen.setStyle(Qt.DotLine)
            painter.setPen(pen)
            painter.drawLine(tr.left(), y, tr.left() + w, y)
        painter.restore()

    def editorEvent(self, event, model, option, index):
        if index.column() in (LC.CHECK, LC.NEG) and not index.data(int(LR.IS_REASON_ROW)):
            if (event.type() == QtCore.QEvent.MouseButtonRelease
                    and event.button() == Qt.LeftButton and option.rect.contains(_event_pos(event))):
                on = _checked(index.data(Qt.CheckStateRole))
                model.setData(index, Qt.Unchecked if on else Qt.Checked, Qt.CheckStateRole)
                return True
        return super().editorEvent(event, model, option, index)

    def sizeHint(self, option, index):
        hint = index.data(Qt.SizeHintRole)
        if isinstance(hint, QtCore.QSize) and hint.height() > 0:
            base = super().sizeHint(option, index)
            return QtCore.QSize(base.width(), hint.height())
        return super().sizeHint(option, index)


# ═════════════════════════════ 行内原因块 ═════════════════════════════
class ReasonWidget(QtWidgets.QFrame):
    """Design 发明①：点状态格展开的行内原因块（C-015 / C-016 / C-127）。

    版式 = `reasonStyle`：`REASON_BG` 底 + 左 3px `AMBER_BORDER` 边条；标题琥珀粗体；
    正文 Consolas（保留换行，含点名的 Excel 行号）；按钮「去诊断 · {action}」。
    裁决③：Design 的「仍然生成（风险自负）」改成「去诊断 · 缺前缀是否强制生成」跳全局开关。"""

    diagRequested = QtCore.Signal(str)                 # ReasonBlock.action_target

    def __init__(self, block, parent=None):
        super().__init__(parent)
        self.setObjectName(N.LIST_REASON)
        self.block = block
        self.setAutoFillBackground(True)
        self.setStyleSheet(
            "#%s{background:%s;border:none;border-left:3px solid %s;}" % (N.LIST_REASON, TH.REASON_BG,
                                                                         TH.AMBER_BORDER))
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(12, 7, 10, 8)
        lay.setSpacing(4)

        self.title = QtWidgets.QLabel(block.title, self)
        self.title.setObjectName(N.LIST_REASON_TITLE)
        self.title.setFont(_ui_font(TH.FS_UI, bold=True))
        self.title.setStyleSheet("color:%s;" % TH.AMBER_FG)
        self.title.setWordWrap(True)
        lay.addWidget(self.title)

        self.body = QtWidgets.QLabel(block.body, self)
        self.body.setObjectName(N.LIST_REASON_BODY)
        self.body.setFont(_mono_font())
        self.body.setStyleSheet("color:%s;" % TH.TEXT)
        self.body.setWordWrap(True)
        self.body.setTextInteractionFlags(Qt.TextSelectableByMouse)
        lay.addWidget(self.body)

        row = QtWidgets.QHBoxLayout()
        row.setContentsMargins(0, 2, 0, 0)
        row.setSpacing(8)
        self.diag_btn = QtWidgets.QPushButton(T.REASON_BTN_FMT.format(action=block.action_label), self)
        self.diag_btn.setObjectName(N.LIST_REASON_DIAG_BTN)
        self.diag_btn.setFont(_ui_font(TH.FS_UI_SMALL))
        self.diag_btn.setFixedHeight(TH.BTN_H)
        self.diag_btn.setCursor(Qt.PointingHandCursor)
        self.diag_btn.clicked.connect(lambda: self.diagRequested.emit(block.action_target))
        row.addWidget(self.diag_btn)

        self.risky_btn = None
        if (block.payload.get("status") in ("needs-prefix", "risky-generated")
                and block.action_target != "diag_risky"):
            # risky-generated 档的主按钮本身就是这个跳转，再放一个一模一样的按钮 = 界面看着像坏了
            self.risky_btn = QtWidgets.QPushButton(T.REASON_RISKY_BTN, self)
            self.risky_btn.setObjectName(N.LIST_REASON_RISKY_BTN)
            self.risky_btn.setFont(_ui_font(TH.FS_UI_SMALL))
            self.risky_btn.setFixedHeight(TH.BTN_H)
            self.risky_btn.setCursor(Qt.PointingHandCursor)
            self.risky_btn.clicked.connect(lambda: self.diagRequested.emit("diag_risky"))
            row.addWidget(self.risky_btn)
        row.addStretch(1)
        lay.addLayout(row)


# ═════════════════════════════ view ═════════════════════════════
class SignalListView(QtWidgets.QTreeView):
    """两级清单视图（架构 §1.3① 的唯一写法）。

    `setRootIsDecorated(False)` / `setIndentation(0)` / `setItemsExpandable(False)` —— 视觉上仍是一张表；
    展开**只**由点状态格触发，且同一时刻只有一行；原因子行 `setFirstColumnSpanned` 后
    `setIndexWidget(子行, ReasonWidget)`，收起即销毁。"""

    diagRequested = QtCore.Signal(str)                 # ReasonBlock.action_target
    statusClicked = QtCore.Signal(str)                 # 点了哪个信号的状态格

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName(N.LIST_VIEW)
        self._reason = None
        self._expanded_name = ""
        self.setRootIsDecorated(False)
        self.setIndentation(0)
        self.setItemsExpandable(False)
        self.setExpandsOnDoubleClick(False)
        self.setUniformRowHeights(False)
        self.setAllColumnsShowFocus(True)
        self.setAlternatingRowColors(True)
        self.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        self.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.setItemDelegate(SignalListDelegate(self))
        self.setMouseTracking(True)
        self.setStyleSheet(
            "QTreeView{background:%s;alternate-background-color:%s;border:1px solid %s;"
            "selection-background-color:%s;selection-color:%s;outline:none;}"
            "QTreeView::item{border:0px;}"
            "QHeaderView::section{background:%s;color:%s;border:0px;border-right:1px solid %s;"
            "border-bottom:1px solid %s;padding-left:6px;}"
            % (TH.WHITE, TH.ZEBRA, TH.BORDER, TH.SELECTED_BG, TH.INK,
               TH.PANEL_BG, TH.MUTE, TH.BORDER_LIGHT, TH.BORDER))
        hdr = self.header()
        hdr.setFixedHeight(TH.HEADER_H)
        hdr.setFont(_ui_font(TH.FS_UI_SMALL))
        hdr.setStretchLastSection(False)
        hdr.setSectionsClickable(True)
        hdr.setHighlightSections(False)
        self.setSortingEnabled(True)                                     # C-037 点表头排序
        self.clicked.connect(self._on_clicked)

    # ── 绑定 model ──
    def setModel(self, model):
        old = self.model()
        if isinstance(old, SignalListProxy):
            try:
                old.sortAboutToStart.disconnect(self._before_sort)
                old.sortFinished.disconnect(self._after_sort)
            except (RuntimeError, TypeError):                            # pragma: no cover
                pass
        super().setModel(model)
        if isinstance(model, SignalListProxy):
            model.sortAboutToStart.connect(self._before_sort)
            model.sortFinished.connect(self._after_sort)
        self._apply_column_sizes()
        if model is not None:
            self.sortByColumn(int(LC.NAME), Qt.AscendingOrder)           # Design 表头「信号 ▴」
            self.header().setSortIndicator(int(LC.NAME), Qt.AscendingOrder)

    def source_model(self):
        m = self.model()
        return m.sourceModel() if isinstance(m, QtCore.QSortFilterProxyModel) else m

    def _apply_column_sizes(self):
        if self.model() is None:
            return
        hdr = self.header()
        for col in LC:
            key = CT.LIST_COL_KEYS[col]
            width = TH.LIST_COL_W.get(key)
            if col == LC.NAME:
                hdr.setSectionResizeMode(int(col), QtWidgets.QHeaderView.Stretch)
                continue
            hdr.setSectionResizeMode(int(col), QtWidgets.QHeaderView.Interactive)
            hdr.resizeSection(int(col), width or 92)

    # ── 展开 / 收起（同一时刻一行）──
    @property
    def expanded_name(self):
        return self._expanded_name

    @property
    def reason_widget(self):
        return self._reason

    def set_expanded(self, name):
        """展开 name 的行内原因块；None / "" = 收起。返回真正展开的信号名（"" = 没展开）。"""
        src = self.source_model()
        if src is None:
            return ""
        self._drop_widget()
        src.set_expanded(None)
        self._expanded_name = ""
        if not name:
            return ""
        block = build_reason_block(src.model_at(src.index_of(name).row())) if src.index_of(name).isValid() else None
        if block is None:
            return ""
        src.set_expanded(name)
        if not src.expanded_name:
            return ""
        self._expanded_name = src.expanded_name
        parent = self._proxy_index(src.index_of(name))
        if not parent.isValid():
            return self._expanded_name
        self.expand(parent)
        child = self.model().index(0, 0, parent)
        w = ReasonWidget(block, self.viewport())
        w.setFixedWidth(max(240, self.viewport().width() - 2))
        src.set_reason_height(self._reason_height(w))
        self.setFirstColumnSpanned(0, parent, True)
        self.setIndexWidget(child, w)
        w.diagRequested.connect(self.diagRequested)
        self._reason = w
        self.scrollTo(child, QtWidgets.QAbstractItemView.EnsureVisible)   # 点开的原因块要能看见
        return self._expanded_name

    def toggle_expanded(self, name):
        return self.set_expanded("" if self._expanded_name == name else name)

    @staticmethod
    def _reason_height(w):
        """原因块占多高。正文是自动换行的 QLabel —— `sizeHint()` 按不换行算，拿它当行高会把
        下面的两个按钮切掉（截图 list_reason_open 上一版就是这样），所以按 heightForWidth 算。"""
        width = w.width() or w.sizeHint().width()
        h = w.sizeHint().height()
        if w.hasHeightForWidth():
            h = max(h, w.heightForWidth(width))
        return h

    def _drop_widget(self):
        if self._reason is None:
            return
        w, self._reason = self._reason, None
        src = self.source_model()
        if src is not None and src.expanded_name:
            parent = self._proxy_index(src.index_of(src.expanded_name))
            if parent.isValid():
                self.setIndexWidget(self.model().index(0, 0, parent), None)
        w.setParent(None)
        w.deleteLater()

    def _proxy_index(self, source_index):
        m = self.model()
        if isinstance(m, QtCore.QSortFilterProxyModel):
            return m.mapFromSource(source_index)
        return source_index

    # ── 排序：收起 → 排 → 原样重挂（C-037）──
    def _before_sort(self):
        self._pending_expand = self._expanded_name
        if self._expanded_name:
            self.set_expanded(None)

    def _after_sort(self):
        name = getattr(self, "_pending_expand", "")
        self._pending_expand = ""
        if name:
            self.set_expanded(name)

    # ── 交互 ──
    def _on_clicked(self, index):
        if not index.isValid() or index.data(int(LR.IS_REASON_ROW)):
            return
        if index.column() != LC.STATUS:
            return
        name = index.data(int(LR.NAME)) or ""
        self.statusClicked.emit(name)
        self.toggle_expanded(name)                                       # C-015 点状态 → 行内原因块

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Space:
            idx = self.currentIndex()
            if idx.isValid() and not idx.data(int(LR.IS_REASON_ROW)):
                col = LC.NEG if idx.column() == LC.NEG else LC.CHECK
                target = self.model().index(idx.row(), int(col), idx.parent())
                on = _checked(target.data(Qt.CheckStateRole))
                self.model().setData(target, Qt.Unchecked if on else Qt.Checked, Qt.CheckStateRole)
                return
        super().keyPressEvent(event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._reason is not None:
            self._reason.setFixedWidth(max(240, self.viewport().width() - 2))
            src = self.source_model()
            if src is not None:
                src.set_reason_height(self._reason_height(self._reason))   # 变窄 = 正文多几行


# ═════════════════════════════ 两个小对话框 ═════════════════════════════
class ColumnsDialog(QtWidgets.QDialog):
    """「列设置…」（C-008 / C-046）。勾选列常驻第一列、不进本对话框（用户标注截图 c22c965c）。

    ⏳ 暂住在这里：C2-d 正在写 `ui/dialogs.py`，C2-int 把本类与 `PasteNamesDialog` 一起搬过去
    （objectName / 文案已按 names / terms 对齐，搬家时不用改调用方）。"""

    def __init__(self, visible, parent=None):
        super().__init__(parent)
        self.setObjectName(N.DLG_COLUMNS)
        self.setWindowTitle(T.DLG_COLUMNS_TITLE)
        lay = QtWidgets.QVBoxLayout(self)
        self.list = QtWidgets.QListWidget(self)
        self.list.setObjectName(N.DLG_COLUMNS_LIST)
        self.list.setFont(_ui_font())
        for col in LC:
            if col == LC.CHECK:
                continue                                                 # 勾选列不进列设置
            key = CT.LIST_COL_KEYS[col]
            item = QtWidgets.QListWidgetItem(T.LIST_HEADERS.get(key) or key, self.list)
            item.setData(Qt.UserRole, key)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked if col in visible else Qt.Unchecked)
        lay.addWidget(self.list)
        box = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel, self)
        box.button(QtWidgets.QDialogButtonBox.Ok).setText(T.DLG_YES)
        box.button(QtWidgets.QDialogButtonBox.Cancel).setText(T.DLG_NO)
        box.accepted.connect(self.accept)
        box.rejected.connect(self.reject)
        lay.addWidget(box)

    def selection(self):
        """→ {列键: 是否可见}（不含常驻的勾选列）。"""
        out = {}
        for i in range(self.list.count()):
            it = self.list.item(i)
            out[str(it.data(Qt.UserRole))] = it.checkState() == Qt.Checked
        return out


class PasteNamesDialog(QtWidgets.QDialog):
    """「粘贴名单勾选…」（C-290）。每行一个信号名，可带位宽切片、大小写无关。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName(N.DLG_PASTE_NAMES)
        self.setWindowTitle(T.DLG_PASTE_NAMES_TITLE)
        lay = QtWidgets.QVBoxLayout(self)
        hint = QtWidgets.QLabel(T.DLG_PASTE_NAMES_HINT, self)
        hint.setWordWrap(True)
        hint.setFont(_ui_font(TH.FS_UI_SMALL))
        hint.setStyleSheet("color:%s;" % TH.MUTE)
        lay.addWidget(hint)
        # 属性名避开 text / result —— QDialog 的这两个名字被 Qt 与测试夹具当方法调（会炸在夹具里）
        self.editor = QtWidgets.QPlainTextEdit(self)
        self.editor.setObjectName(N.DLG_PASTE_NAMES_TEXT)
        self.editor.setFont(_mono_font())
        lay.addWidget(self.editor)
        self.result_label = QtWidgets.QLabel("", self)
        self.result_label.setObjectName(N.DLG_PASTE_NAMES_RESULT)
        self.result_label.setWordWrap(True)
        self.result_label.setFont(_ui_font(TH.FS_UI_SMALL))
        lay.addWidget(self.result_label)
        box = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel, self)
        box.button(QtWidgets.QDialogButtonBox.Ok).setText(T.DLG_YES)
        box.button(QtWidgets.QDialogButtonBox.Cancel).setText(T.DLG_NO)
        box.accepted.connect(self.accept)
        box.rejected.connect(self.reject)
        lay.addWidget(box)

    def names(self):
        out = []
        for line in self.editor.toPlainText().splitlines():
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            out.append(re.sub(r"\[[^\]]*\]\s*$", "", s).strip().lower())
        return out


# ═════════════════════════════ 清单区面板 ═════════════════════════════
class SignalListPanel(QtWidgets.QWidget):
    """③ 清单区整块：头部计数 / 列设置 / 排序 + 两级视图 + 底部工具条 6 键。

    与 `state` 的往来只有两个方向：读（models / checked / negs / settings）与写
    （set_checked / set_neg / set_current / save_settings），批量写包在 `state.suspend_persist()` 里。"""

    #: (ReasonBlock.action_target, 信号名) —— app.route_reason_action 接
    diagRequested = QtCore.Signal(str, str)
    #: 逐操作反馈（app 接到状态栏 statusLeft，C-269）
    statusMessage = QtCore.Signal(str)

    def __init__(self, state, view_id=None, parent=None):
        super().__init__(parent)
        self.setObjectName(N.LIST_PANEL)
        self.state = state
        self._view_id = view_id or getattr(state, "scope", CT.DEFAULT_VIEW_ID)
        self._syncing = False

        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        # ── 头部：计数 + 列设置 + 排序 ──
        head = QtWidgets.QWidget(self)
        hl = QtWidgets.QHBoxLayout(head)
        hl.setContentsMargins(10, 6, 10, 6)
        hl.setSpacing(10)
        self.counts = QtWidgets.QLabel("", head)
        self.counts.setObjectName(N.LIST_HEADER_COUNTS)
        self.counts.setFont(_ui_font())
        hl.addWidget(self.counts)
        hl.addStretch(1)
        self.columns_btn = self._link_button(T.LIST_COLUMNS, N.LIST_COLUMNS_BTN, head)
        self.columns_btn.clicked.connect(self.open_columns_dialog)
        hl.addWidget(self.columns_btn)
        self.sort_btn = self._link_button(T.LIST_SORT, N.LIST_SORT_BTN, head)
        hl.addWidget(self.sort_btn)
        lay.addWidget(head)

        # ── 视图 ──
        self.model = SignalListModel(self)
        self.proxy = SignalListProxy(self)
        self.proxy.setSourceModel(self.model)
        self.view = SignalListView(self)
        self.view.setModel(self.proxy)
        lay.addWidget(self.view, 1)

        # ── 排序菜单（C-037 的第二个入口）──
        self.sort_menu = QtWidgets.QMenu(self)
        self.sort_menu.setObjectName(N.LIST_SORT_MENU)
        for col in (LC.STATUS, LC.NTEST, LC.OWNER, LC.NAME):
            act = self.sort_menu.addAction(T.LIST_HEADERS[CT.LIST_COL_KEYS[col]])
            act.setData(int(col))
            act.triggered.connect(lambda _=False, c=int(col): self.sort_by(c))
        self.sort_btn.setMenu(self.sort_menu)

        # ── 底部工具条 ──
        bar = QtWidgets.QWidget(self)
        bar.setObjectName(N.LIST_BOTTOM_BAR)
        bar.setStyleSheet("#%s{background:%s;border-top:1px solid %s;}" % (N.LIST_BOTTOM_BAR,
                                                                          TH.PANEL_BG, TH.BORDER))
        fl = FlowLayout(bar, margin=8, hspacing=6, vspacing=4)
        self.btn_check_all = self._bar_button(T.LIST_BTN_CHECK_ALL, N.LIST_BTN_CHECK_ALL, bar, fl)
        self.btn_uncheck_all = self._bar_button(T.LIST_BTN_UNCHECK_ALL, N.LIST_BTN_UNCHECK_ALL, bar, fl)
        self.btn_check_selected = self._bar_button(T.LIST_BTN_CHECK_SELECTED, N.LIST_BTN_CHECK_SELECTED, bar, fl)
        self.btn_neg_all = self._bar_button(T.LIST_BTN_NEG_ALL, N.LIST_BTN_NEG_ALL, bar, fl)
        self.btn_neg_clear = self._bar_button(T.LIST_BTN_NEG_CLEAR, N.LIST_BTN_NEG_CLEAR, bar, fl)
        self.btn_paste_names = self._bar_button(T.LIST_BTN_PASTE_NAMES, N.LIST_BTN_PASTE_NAMES, bar, fl)
        lay.addWidget(bar)

        self.btn_check_all.clicked.connect(self.check_all_visible)
        self.btn_uncheck_all.clicked.connect(self.uncheck_all)
        self.btn_check_selected.clicked.connect(self.check_selected_rows)
        self.btn_neg_all.clicked.connect(self.neg_all)
        self.btn_neg_clear.clicked.connect(self.neg_clear)
        self.btn_paste_names.clicked.connect(self.open_paste_names_dialog)

        # ── 信号接线 ──
        self.model.checkToggled.connect(self._on_check_toggled)
        self.model.negToggled.connect(self._on_neg_toggled)
        self.view.diagRequested.connect(self._on_diag_requested)
        self.proxy.rowsInserted.connect(self._refresh_counts)
        self.proxy.rowsRemoved.connect(self._refresh_counts)
        self.proxy.modelReset.connect(self._refresh_counts)
        sm = self.view.selectionModel()
        if sm is not None:
            sm.currentRowChanged.connect(self._on_current_row_changed)
        self._connect_state()
        self._apply_saved_columns()
        self.reload()

    # ── 小件 ──
    @staticmethod
    def _link_button(text, obj_name, parent):
        b = QtWidgets.QPushButton(text, parent)
        b.setObjectName(obj_name)
        b.setFlat(True)
        b.setCursor(Qt.PointingHandCursor)
        b.setFont(_ui_font(TH.FS_UI_SMALL))
        b.setStyleSheet("QPushButton{border:none;color:%s;padding:0 2px;}"
                        "QPushButton::menu-indicator{width:0px;}" % TH.BLUE)
        return b

    @staticmethod
    def _bar_button(text, obj_name, parent, layout):
        b = QtWidgets.QPushButton(text, parent)
        b.setObjectName(obj_name)
        b.setFont(_ui_font(TH.FS_UI_SMALL))
        b.setFixedHeight(TH.BTN_H)
        b.setCursor(Qt.PointingHandCursor)
        b.setStyleSheet("QPushButton{background:%s;border:1px solid %s;border-radius:3px;"
                        "padding:0 8px;color:%s;}" % (TH.WHITE, TH.BORDER, TH.TEXT))
        layout.addWidget(b)
        return b

    # ── state 接线（缺哪条信号就跳过哪条：C1-a 还在并行写）──
    def _connect_state(self):
        for sig, slot in (("modelsChanged", self._on_models_changed),
                          ("modelUpdated", self._on_model_updated),
                          ("checksChanged", self._on_checks_changed),
                          ("negsChanged", self._on_negs_changed),
                          ("currentChanged", self._on_state_current_changed),
                          ("scopeChanged", self._on_scope_changed),
                          ("configChanged", self._on_config_changed)):
            s = getattr(self.state, sig, None)
            if s is not None and hasattr(s, "connect"):
                s.connect(slot)

    # ── 数据 ──
    @property
    def view_id(self):
        return self._view_id

    def set_view_id(self, view_id):
        self._view_id = view_id
        self.reload()

    def reload(self):
        """整表重建（换表 / 换范围 / worker finished）+ 默认选中第一个可见行（C-044）。"""
        models = list(self.state.models(self._view_id) or [])
        self.model.set_prefix_map(getattr(self.state, "probe_prefixes", None) or {})
        self.model.load(models, self._checked_set(), self.state.negs(self._view_id))
        self.view.set_expanded(None)
        self.select_first_visible()
        self._refresh_counts()

    def _checked_set(self):
        fn = getattr(self.state, "checked", None)
        return fn(self._view_id) if callable(fn) else None

    def select_first_visible(self):
        if self.proxy.rowCount() <= 0:
            return False
        idx = self.proxy.index(0, int(LC.NAME))
        self.view.setCurrentIndex(idx)
        self.view.selectionModel().select(
            idx, QtCore.QItemSelectionModel.ClearAndSelect | QtCore.QItemSelectionModel.Rows)
        return True

    # ── 列设置（C-008 / C-046）──
    def visible_columns(self):
        return tuple(c for c in LC if not self.view.isColumnHidden(int(c)))

    def set_visible_columns(self, cols):
        cols = set(int(c) for c in cols) | {int(LC.CHECK)}               # 勾选列常驻
        for c in LC:
            self.view.setColumnHidden(int(c), int(c) not in cols)

    def _apply_saved_columns(self):
        saved = {}
        fn = getattr(self.state, "settings", None)
        if callable(fn):
            saved = dict((fn() or {}).get(CT.SETTINGS_LIST_COLUMNS) or {})
        cols = set(CT.LIST_DEFAULT_VISIBLE)
        for col in LC:
            key = CT.LIST_COL_KEYS[col]
            if key in saved:
                cols.add(col) if saved[key] else cols.discard(col)
        self.set_visible_columns(cols)

    def open_columns_dialog(self):
        dlg = ColumnsDialog(self.visible_columns(), self)
        if dlg.exec() != QtWidgets.QDialog.Accepted:
            return {}
        sel = dlg.selection()
        cols = {LC.CHECK} | {c for c in LC if sel.get(CT.LIST_COL_KEYS[c])}
        self.set_visible_columns(cols)
        save = getattr(self.state, "save_settings", None)
        if callable(save):
            save({CT.SETTINGS_LIST_COLUMNS: dict(sel)})
        return sel

    # ── 排序（C-037）──
    def sort_by(self, column, order=None):
        if order is None:
            order = Qt.AscendingOrder
        self.view.sortByColumn(int(column), order)
        self.view.header().setSortIndicator(int(column), order)

    # ── 计数（清单头部 / C-031）──
    def counts_text(self):
        names = self.visible_names()
        rows = {str(m.get("name", "")): m for m in self.model.rows()}
        k = sum(1 for n in names if self.model.is_checked(n))
        p = sum(1 for n in names if _tone_of(rows.get(n) or {}) in _PROBLEM_TONES)
        return T.LIST_COUNTS_FMT.format(n=len(names), k=k, p=p)

    def visible_counts_text(self):
        """筛选后给状态栏用的一行（C-031）：可见 N / 共 M（其中 K 个按输入信号名命中）。"""
        return T.STATUS_VISIBLE_FMT.format(v=self.proxy.n_visible(), m=self.proxy.n_total(),
                                           k=self.proxy.n_by_input())

    def _refresh_counts(self, *_):
        self.counts.setText(self.counts_text())

    def visible_names(self):
        return [n for n in self.proxy.visible_names() if n]

    def selected_names(self):
        sm = self.view.selectionModel()
        if sm is None:
            return []
        out = []
        for idx in sm.selectedRows(int(LC.NAME)):
            if idx.data(int(LR.IS_REASON_ROW)):
                continue
            n = idx.data(int(LR.NAME))
            if n and n not in out:
                out.append(n)
        return out

    def current_name(self):
        idx = self.view.currentIndex()
        return (idx.data(int(LR.NAME)) or "") if idx.isValid() else ""

    # ── 筛选（filter_bar 转发）──
    def set_filters(self, owners=None, kind="", status="", regex=""):
        keep = self.view.expanded_name
        self.view.set_expanded(None)
        self.proxy.set_filters(owners, kind, status, regex)
        if keep and keep in self.visible_names():
            self.view.set_expanded(keep)
        self._refresh_counts()
        self.statusMessage.emit(self.visible_counts_text())

    # ── 底部工具条 ──
    def check_all_visible(self):
        """C-032 全选（作用于当前可见行）。"""
        names = self.visible_names()
        self._set_checked(names, True)
        self.statusMessage.emit(T.LIST_BTN_CHECK_ALL + " %d" % len(names))
        return names

    def uncheck_all(self):
        """C-033 清空全部勾选（整张清单，不只可见行）。"""
        names = [str(m.get("name", "")) for m in self.model.rows()]
        self._set_checked(names, False)
        self.statusMessage.emit(T.LIST_BTN_UNCHECK_ALL + " %d" % len(names))
        return names

    def check_selected_rows(self):
        """C-034 把框选 / Ctrl 多选的行一次勾上。"""
        names = self.selected_names()
        self._set_checked(names, True)
        self.statusMessage.emit(T.LIST_BTN_CHECK_SELECTED + " %d" % len(names))
        return names

    def neg_targets(self):
        """C-035 的作用域：有勾选只作用勾选，否则作用于全部可见。"""
        names = self.visible_names()
        checked = [n for n in names if self.model.is_checked(n)]
        return checked or names

    def neg_all(self):
        """C-035 一键给一批信号各加 1 条反例。"""
        names = self.neg_targets()
        self._set_negs(names, True)
        self.statusMessage.emit(T.LIST_BTN_NEG_ALL + " %d" % len(names))
        return names

    def neg_clear(self):
        """C-036 清除一批信号的反例；含自定义命名 / 手填错值的先弹确认（先点名、计数在后）。"""
        names = [n for n in self.neg_targets() if self.model.is_neg(n)]
        if not names:
            return []
        protected = self._protected_neg_names(names)
        if protected:
            text = ("、".join(protected[:TH.IMPORT_MISSING_LIST_MAX]) + "\n"
                    + T.TRUTH_CONFIRM_DEL_NEG_FMT.format(n=len(protected)))
            ans = QtWidgets.QMessageBox.question(
                self, T.LIST_BTN_NEG_CLEAR, text,
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No)
            if ans != QtWidgets.QMessageBox.Yes:
                return []
        self._set_negs(names, False)
        self.statusMessage.emit(T.LIST_BTN_NEG_CLEAR + " %d" % len(names))
        return names

    def open_paste_names_dialog(self):
        """C-290 粘贴一份信号名单，一次勾上对应的信号。"""
        dlg = PasteNamesDialog(self)
        if dlg.exec() != QtWidgets.QDialog.Accepted:
            return ([], [])
        wanted = dlg.names()
        by_low = {str(m.get("name", "")).lower(): str(m.get("name", "")) for m in self.model.rows()}
        hit = [by_low[w] for w in wanted if w in by_low]
        miss = [w for w in wanted if w not in by_low]
        self._set_checked(hit, True)
        text = T.DLG_PASTE_NAMES_RESULT_FMT.format(
            n=len(hit), m=len(miss), names="、".join(miss[:TH.IMPORT_MISSING_LIST_MAX]))
        dlg.result_label.setText(text)
        self.statusMessage.emit(text)
        return (hit, miss)

    # ── 写回 state（批量包 suspend_persist，C-244）──
    def _bulk(self):
        fn = getattr(self.state, "suspend_persist", None)
        if callable(fn):
            return fn()
        return contextlib.nullcontext()

    def _set_checked(self, names, on):
        if not names:
            return
        with self._bulk():
            self.state.set_checked(list(names), bool(on), self._view_id)
        self._sync_check_state()

    def _set_negs(self, names, on):
        if not names:
            return
        with self._bulk():
            for n in names:
                self.state.set_neg(n, bool(on), self._view_id)
        self._sync_check_state()

    def _protected_neg_names(self, names):
        """「值得保护」的反例（自定义命名 / 手填过错值）落在哪些信号上 —— 口径在 Qt-free 层，
        视图不碰 edits，经 state 问（state 没这个方法 = 当作没有，不拦用户）。"""
        fn = getattr(self.state, "protected_negatives", None)
        if not callable(fn):
            return []
        try:
            return [n for n in fn(list(names), self._view_id) if n]
        except TypeError:                                                # pragma: no cover - 签名差异
            return [n for n in fn(list(names)) if n]

    def _sync_check_state(self):
        self.model.set_check_state(self._checked_set(), self.state.negs(self._view_id))
        self._refresh_counts()

    # ── 槽 ──
    def _on_check_toggled(self, name, on):
        with self._bulk():
            self.state.set_checked([name], bool(on), self._view_id)
        self._refresh_counts()

    def _on_neg_toggled(self, name, on):
        self.state.set_neg(name, bool(on), self._view_id)
        self._refresh_counts()

    def _on_diag_requested(self, target):
        self.diagRequested.emit(target, self.view.expanded_name or self.current_name())

    def _on_current_row_changed(self, cur, _prev):
        if self._syncing or not cur.isValid() or cur.data(int(LR.IS_REASON_ROW)):
            return
        name = cur.data(int(LR.NAME)) or ""
        if name and name != getattr(self.state, "current_name", ""):
            self.state.set_current(name)

    def _on_state_current_changed(self, name):
        if not name or name == self.current_name():
            return
        idx = self.proxy.mapFromSource(self.model.index_of(name))
        if not idx.isValid():
            return
        self._syncing = True
        try:
            self.view.setCurrentIndex(self.proxy.index(idx.row(), int(LC.NAME)))
        finally:
            self._syncing = False

    def _on_models_changed(self, view_id=None):
        if view_id in (None, "", self._view_id):
            self.reload()

    def _on_model_updated(self, view_id, name=None):
        """worker.signalDone → 增量升级一行（dataChanged，不重建）。"""
        if name is None:
            view_id, name = self._view_id, view_id
        if view_id not in (None, "", self._view_id):
            return
        m = self.state.model_of(name, self._view_id)
        if m:
            self.model.update_row(m)
            self._refresh_counts()

    def _on_checks_changed(self, view_id=None):
        if view_id in (None, "", self._view_id):
            self._sync_check_state()

    def _on_negs_changed(self, view_id=None):
        if view_id in (None, "", self._view_id):
            self._sync_check_state()

    def _on_scope_changed(self, view_id):
        self._view_id = view_id
        self.reload()

    def _on_config_changed(self, which=""):
        if which in ("", "probe_prefixes"):
            self.model.set_prefix_map(getattr(self.state, "probe_prefixes", None) or {})


# ═════════════════════════════ 对 state 的接口假设 ═════════════════════════════
#: C1-int 接线时逐条核对（`contracts.WorkbenchStateProto` 已有的不再复述，★ = 契约里没有、本模块新要的）
STATE_REQUIREMENTS = (
    ("scope", "属性：当前 view_id（构造时没给 view_id 就用它）"),
    ("models(view_id)", "→ list[dict]：skeleton_models() / view_models(lite=True) 的模型；键全部 .get 兜底"),
    ("model_of(name, view_id)", "→ dict：modelUpdated 后取升级过的那一行"),
    ("checked(view_id)", "→ set | None（None = 全勾，默认态）"),
    ("set_checked(names, on, view_id)", "批量勾选写回"),
    ("negs(view_id)", "→ set"),
    ("set_neg(name, on, view_id)", "逐信号反例写回（返回值本模块不解释）"),
    ("set_current(name) / current_name", "当前信号双向同步"),
    ("suspend_persist()", "with 上下文：批量操作挂起逐格存盘（C-244）"),
    ("settings() / save_settings(patch)", "列设置持久化，键 = contracts.SETTINGS_LIST_COLUMNS"),
    ("probe_prefixes", "属性 {网名: 前缀}：探针前缀列的输入侧命中（C-020）"),
    ("protected_negatives(names, view_id)", "→ list[str]：哪些信号的反例是自定义命名 / 手填过错值的"
                                            "（C-036 二次确认；口径 = edits.protected_negatives，"
                                            "视图不 import edits）。C1-int 已补进 state 与契约；"
                                            "state 没有（更早的假件）= 不拦用户直接删"),
    ("信号 modelsChanged / modelUpdated / checksChanged / negsChanged / currentChanged / "
     "scopeChanged / configChanged", "有哪条接哪条，缺的静默跳过"),
)
