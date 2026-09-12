# -*- coding: utf-8 -*-
"""side_panel.py —— ⑨ 右侧常驻栏（GUI v2 Phase C2-a）。

Design（`docs/GUI_v2_Design对齐_20260912.md` §1.2 ⑨）把这一栏分成上下两半，中间一条可拖手柄：

    上半「逐层展开」(chainH=300)   每层：①②③ + 灰小字页标签 + Excel 原式 + 「= 代入真名」
    下半「输入信号 N 个」          每行两行高：主行四列（字母 / 信号(位宽) / 角色 / 类型）
                                   + 第二行常驻驱动串（`RF_WRITE 0x64 bit<<0` / `force ENV_RF.x`）

本模块只做「拿 an → 画 → 把用户动作转成 bus 调用」：

  · 链的层、输入表的行、驱动串，全部来自 Qt-free 层（`an["chain"]` / `inputs_table.input_rows` /
    `inputs_table.column_drives`），这里一行都不算；链为空时的三种兜底（直连寄存器 / 单级 logic /
    mux 根）也只是把 an 里已有的 `sig.expr` 摆出来，代入真名走 `analysis_norm.subst_expr`；
  · 文案只取 `terms`、色值只取 `theme`、objectName 只取 `names`（三个文件本波只读）；
  · 一切后端文本（note / issues / drive / found_in 说明）先过 `terms.scrub`（不变量 I-12）；
  · 「当前线网」只经 `bus`（不变量 I-18 / C-282）：链里点一个真名 → `bus.select(net, "chain")`，
    输入表点一行 → `bus.select(net, "inputs")`；两个视图都只**听** `bus.netSelected` 改底色，
    **绝不**互相 import、也绝不在收到广播时回写选择（那就是重入递归）。

契约 ID（架构附录 A「SIDE」14 条）：C-050 C-051 C-052 C-053 C-054 C-056 C-057 C-058 C-059
C-060 C-061 C-062 C-222 C-275；另配合 C-282（高亮总线双向联动）。

对 `ui/state.py` / `ui/bus.py` / an 的接口假设见模块末尾 `STATE_REQUIREMENTS`。
"""

import html as _html
import re

from PySide6 import QtCore, QtGui, QtWidgets

from dreg_verify import analysis_norm as AN
from dreg_verify import inputs_table as IT

from . import bus as BUS
from . import names as N
from . import terms as T
from . import theme as TH
from . import widgets as W

Qt = QtCore.Qt

__all__ = ["ChainView", "InputsModel", "InputsDelegate", "InputsView", "SidePanel",
           "chain_blocks", "chain_status", "drive_text", "ROLE_NET", "ROLE_IS_DRIVE",
           "ROLE_INPUT_INDEX", "ROLE_GUESSED", "ROLE_NEEDS_PREFIX", "STATE_REQUIREMENTS"]

# ═════════════════════════════ 模块级常量 ═════════════════════════════
#: 层级序号（与 v1『展开链』面板、报告 HTML 同一套记号）
MARKS = "①②③④⑤⑥⑦⑧⑨"

#: 输入表的自定义 role（`contracts.ListRole` 是清单专用的，这里另开一段，不与之重叠）
ROLE_NET = int(Qt.UserRole) + 1          # 该行的线网比对键（net_key 后）
ROLE_IS_DRIVE = int(Qt.UserRole) + 2     # True = 这是第二行（常驻驱动串行）
ROLE_INPUT_INDEX = int(Qt.UserRole) + 3  # 属于第几个输入（两行一组）
ROLE_GUESSED = int(Qt.UserRole) + 4      # C-275：名字是按命名约定猜的
ROLE_NEEDS_PREFIX = int(Qt.UserRole) + 5 # C-058：网存在但埋在子模块里，要层级前缀

#: 表达式里的「真名」token：标识符 + 可选位宽切片。前置 `'` 排除 `1'b0` / `3'b101` 的进制字母。
_NET_RE = re.compile(r"(?<![A-Za-z0-9_'])([A-Za-z_][A-Za-z0-9_]*)(\[[^\]]*\])?")
#: 不是线网的 token：表达式关键字 + don't-care 记号（单字母 A–J 另按长度排除）
_NOT_NET = frozenset(("case", "default", "x", "z"))
#: mux 根的链头写成 `mux<组号>`，它是「组」不是网，不做可点真名
_MUX_HEAD_RE = re.compile(r"^mux\d+$")


def _px(size):
    return int(round(float(size)))


def _esc(text):
    return _html.escape(str(text if text is not None else ""))


# ═════════════════════════════ 一、链的层（纯函数，无 Qt）═════════════════════════════
def _page_tag(page, out):
    """页标签文案。`page` 缺失（C0 之前的 an）→ 空串：**不猜**（架构 §6.9）。"""
    tmpl = T.CHAIN_PAGE_TAGS.get(str(page or ""))
    if not tmpl:
        return ""
    return tmpl.format(group=out) if "{group}" in tmpl else tmpl


def _block(i, out, expr, subst, page, kind, note=""):
    return {"mark": MARKS[i] if i < len(MARKS) else "(%d)" % (i + 1),
            "tag": _page_tag(page, out), "out": str(out or ""), "expr": str(expr or ""),
            "subst": str(subst or ""), "page": str(page or ""), "kind": str(kind or ""),
            "note": str(note or "")}


def chain_blocks(an):
    """an → 链区要画的层列表（每层 = 一个 dict：mark / tag / out / expr / subst / note）。

    `an["chain"]` 非空 → 逐条照搬（C-050：跨页展开链每层两行 + 页标签）。
    空链说明这个信号没有跨页上游，按根的类型给**一层**（v1 `_render_chain` 的同一套口径）：
      · register  → 只有一句话：顶层口 == 该寄存器字段写值，无展开（C-053）；
      · logic     → 原式 + 字母代入真名，附「输入均为直连叶子」（C-051）；
      · mux       → `case(ctrl){...}` 选路结构（C-052；mux 的原式里本来就是真名，代入行同文）。
    其余（只读回读 / 未解析）→ 空列表，由 `chain_status` 写状态与原因（C-054）。
    """
    if not an:
        return []
    out = []
    for i, c in enumerate(an.get("chain") or []):
        out.append(_block(i, c.get("out"), c.get("expr"), c.get("subst"),
                          c.get("page"), c.get("kind")))
    if out:
        return out

    kind = str(an.get("kind") or "")
    sig = an.get("sig")
    name = str(an.get("name") or "")
    if kind == "register":
        return [_block(0, name, "", "", "register", "register", note=T.CHAIN_REGISTER_DIRECT)]
    expr = str(getattr(sig, "expr", "") or "").strip()
    if kind == "logic" and expr:
        name_of = {ltr: b.base for ltr, b in (an.get("bindings") or {}).items()
                   if b is not None and getattr(b, "base", None)}
        return [_block(0, name, expr, AN.subst_expr(expr, name_of), "logic", "logic",
                       note=T.CHAIN_LEAF_NOTE)]
    if kind == "mux" and expr:
        head = "mux%s" % (getattr(sig, "group_no", "") or "")
        return [_block(0, head, expr, expr, "mux", "mux")]
    return []


def chain_status(an):
    """不可建 / 有告警的信号 → (状态标签, 原因全文, 色档)；干净信号 → None。文本都已 `terms.scrub`。

    C-054：链区不能是空白——状态与原因直接写在这儿（清单行内原因块之外的第二处，两处同源）。
    判据只看结构化字段：`status != "ok"` 或 `issues` 非空（spec-collision 这类 status 仍是 ok、
    但整组被跳过的，靠 issues 命中）。
    """
    if not an:
        return None
    status = str(an.get("status") or "ok")
    issues = [x for x in (an.get("issues") or []) if x]
    if status == "ok" and not issues:
        return None
    key = str(an.get("status_detail") or "") or T.STATUS_FALLBACK.get(status, "error")
    label, tone, _help = T.STATUS.get(key) or T.STATUS["error"]
    lines = [T.scrub(an.get("note") or "")] + [T.scrub(x) for x in issues]
    return label, "\n".join(x for x in lines if x), tone


# ═════════════════════════════ 二、输入行的显示口径（纯函数，无 Qt）═════════════════════
def drive_text(row):
    """输入行 → 第二行要显示的驱动串（C-057 / C-058 / C-059）。

    引擎的 `drive` 已经是 `RF_WRITE 0x64 bit<<0` / `force ENV_RF.<net>` / `✗未解析：…` 这三种
    工程师写法，照用；只把引擎顺手缀在后面的那句「⚠…」换成 `terms` 的正式文案——界面文案一律
    从 `terms` 出，免得同一句话在引擎和界面各有一份、改一处漏一处。
    """
    raw = T.scrub(row.get("drive") or "")
    raw = raw.split("⚠")[0].rstrip()
    if row.get("needs_prefix"):
        return "%s  %s" % (raw, T.SIDE_NEEDS_PREFIX_MARK) if raw else T.SIDE_NEEDS_PREFIX_MARK
    if not row.get("resolved", True):
        return raw if raw.startswith(T.SIDE_UNRESOLVED_MARK) else "%s %s" % (
            T.SIDE_UNRESOLVED_MARK, raw)
    return raw


def _drive_color(row):
    """驱动行字色（C-059）：未解析 = 红、需前缀 = 琥珀、其余 = 蓝。"""
    if not row.get("resolved", True):
        return TH.BAD_FG
    if row.get("needs_prefix"):
        return TH.WARN_FG
    return TH.BLUE_DARK


def _row_tip(row):
    """一行的 tooltip：来源的工程师说法 + 解析器留下的原话（都过 scrub）。"""
    parts = [T.scrub(row.get("found_in_text") or ""), T.scrub(row.get("note") or "")]
    if row.get("guessed"):
        parts.append(T.SIDE_INPUTS_GUESS_BADGE)
    if row.get("needs_prefix"):
        parts.append(T.SIDE_NEEDS_PREFIX_MARK)
    return "\n".join(x for x in parts if x)


# ═════════════════════════════ 三、上半「逐层展开」═════════════════════════════
class ChainView(QtWidgets.QTextBrowser):
    """链区画面。用富文本而不是自绘表格，只因为这一栏要的正是「一段可读的表达式，里面某几个
    词可点」——每个真名是一个 `<a href="net:...">`，点它 → `netClicked`（面板再转 `bus.select`）。

    高亮（C-282）：收到当前线网后整行底 `SELECTED_BG`、名字加粗；比对键一律 `bus.net_key`
    （链里 mux 段的代入行是大写叶子键、输入表是小写带切片，只有过这道才对得上）。
    """

    #: 点了链里某个真名（已 net_key 规整）
    netClicked = QtCore.Signal(str)

    def __init__(self, parent=None):
        QtWidgets.QTextBrowser.__init__(self, parent)
        self.setObjectName(N.SIDE_CHAIN_VIEW)
        self.setOpenLinks(False)
        self.setOpenExternalLinks(False)
        self.setFrameShape(QtWidgets.QFrame.NoFrame)
        self.setLineWrapMode(QtWidgets.QTextEdit.WidgetWidth)
        self.document().setDefaultFont(W.ui_font())
        self.document().setDefaultStyleSheet("a{color:%s;text-decoration:none;}" % TH.INK)
        self.setStyleSheet("QTextBrowser{background:%s;border:none;}" % TH.WHITE)
        self._blocks = []
        self._status = None
        self._message = ""
        self._highlight = ""
        self.anchorClicked.connect(self._on_anchor)

    # ── 内容 ──
    def set_content(self, blocks=(), status=None, message=""):
        """一次性换内容：层列表 + （可选）状态/原因 + （可选）占位文案。"""
        self._blocks = [dict(b) for b in (blocks or ())]
        self._status = status
        self._message = str(message or "")
        self._render()

    def blocks(self):
        return [dict(b) for b in self._blocks]

    def set_highlight(self, net):
        """当前线网变了 → 重画（重画而不是局部改格式：富文本里同一个名字可能出现在好几行）。"""
        key = BUS.net_key(net)
        if key == self._highlight:
            return
        self._highlight = key
        self._render()
        if key:
            self.ensure_net_visible(key)      # 亮了却在可视区外 = 等于没亮

    def highlight(self):
        return self._highlight

    def _render(self):
        bar = self.verticalScrollBar()
        keep = bar.value()
        self.setHtml(self._build_html())
        bar.setValue(min(keep, bar.maximum()))

    # ── HTML ──
    def _build_html(self):
        out = []
        if self._message:
            for line in self._message.split("\n"):
                out.append(self._div(_esc(line), TH.MUTE, TH.FS_UI, mono=False))
        if self._status:
            label, detail, tone = self._status
            fg = TH.TONE_COLORS.get(tone, TH.TONE_COLORS["bad"])[0]
            out.append(self._div(_esc(label), fg, TH.FS_UI, bold=True, mono=False))
            for line in (detail or "").split("\n"):
                if line:
                    out.append(self._div(_esc(line), TH.TEXT, TH.FS_UI_SMALL, mono=False))
        for b in self._blocks:
            out.extend(self._block_html(b))
        return "".join(out) or self._div("", TH.MUTE, TH.FS_UI, mono=False)

    def _block_html(self, b):
        out = []
        head = b["mark"] + (("　" + b["tag"]) if b["tag"] else "")
        out.append(self._div(_esc(head), TH.MUTE, TH.FS_PAGE_TAG, mono=False))
        head_is_net = bool(b["out"]) and not _MUX_HEAD_RE.match(b["out"])
        if b["expr"] or b["subst"]:
            # 每层两行（C-050）：上行 Excel 原式、下行字母代入真实信号名
            out.append(self._line("%s = " % b["out"], b["expr"], head_is_net))
            out.append(self._line("　= ", b["subst"], False))
        elif b["out"]:                      # 直连寄存器：没有表达式，只把顶层口的名字摆出来（C-053）
            out.append(self._line(b["out"], "", head_is_net))
        if b["note"]:
            out.append(self._div(_esc(b["note"]), TH.MUTE, TH.FS_UI_SMALL, mono=False))
        out.append('<div style="font-size:%dpx">&nbsp;</div>' % _px(TH.FS_UI_SMALL))
        return out

    def _line(self, head, body, head_is_net):
        """一行 = 头（`out = ` 或缩进的 `= `）+ 正文；里面的真名转成可点锚，命中当前线网整行加底。"""
        head_html, hit = (_linkify(head, self._highlight) if head_is_net else (_esc(head), False))
        body_html, hit2 = _linkify(body, self._highlight)
        inner = head_html + body_html
        if hit or hit2:
            inner = '<span style="background-color:%s">%s</span>' % (TH.SELECTED_BG, inner)
        return self._div(inner, TH.INK, TH.FS_MONO, mono=True)

    @staticmethod
    def _div(body, color, size, bold=False, mono=True):
        """一行 = 一个 `<div>`（= 一个 QTextBlock，`highlighted_lines` 才能逐行读回底色）。
        `body` 必须已是转义好的 HTML。"""
        fam = (",".join("'%s'" % x for x in TH.FONT_MONO_FALLBACKS) if mono
               else ",".join("'%s'" % x for x in TH.FONT_UI_FALLBACKS))
        style = "margin:0;color:%s;font-family:%s;font-size:%dpx;%s" % (
            color, fam, _px(size), "font-weight:700;" if bold else "")
        return '<div style="%s">%s</div>' % (style, body)

    # ── 高亮 / 命中位置（测试与面板都用这两个）──
    def highlighted_lines(self):
        """文档里真正带 `SELECTED_BG` 底色的那几行（从渲染结果读回，不是记账）。"""
        want = TH.SELECTED_BG.lower()
        doc = self.document()
        out = []
        blk = doc.firstBlock()
        while blk.isValid():
            start, end = blk.position(), blk.position() + blk.length() - 1
            for pos in range(start + 1, end + 1):
                cur = QtGui.QTextCursor(doc)
                cur.setPosition(pos)
                if cur.charFormat().background().color().name().lower() == want:
                    out.append(blk.text())
                    break
            blk = blk.next()
        return out

    def _net_span(self, net):
        """某个真名在文档里的 (起, 止) 字符位置；没有返回 None。"""
        href = "net:%s" % BUS.net_key(net)
        doc = self.document()
        start = end = -1
        for pos in range(1, doc.characterCount()):
            cur = QtGui.QTextCursor(doc)
            cur.setPosition(pos)
            if cur.charFormat().anchorHref() == href:
                if start < 0:
                    start = pos - 1
                end = pos
            elif start >= 0:
                break
        return None if start < 0 else (start, end)

    def ensure_net_visible(self, net):
        """把某个真名滚进可见区（链深了之后当前线网可能在折叠线以下）。滚了返回 True。"""
        span = self._net_span(net)
        if span is None:
            return False
        cur = QtGui.QTextCursor(self.document())
        cur.setPosition(span[0])
        rect = self.cursorRect(cur)
        bar = self.verticalScrollBar()
        height = self.viewport().height()
        if rect.top() < 0:
            bar.setValue(bar.value() + rect.top() - 4)
        elif rect.bottom() > height:
            bar.setValue(bar.value() + rect.bottom() - height + 4)
        return True

    def net_point(self, net):
        """某个真名在画面上的一个可点坐标（viewport 坐标）；没有这个名字 / 点不着返回 None。

        两处坑：① viewport 外的坐标点不着（Qt 当成点在空白处）——先滚进可见区；
        ② 长表达式会折行，名字可能横跨两行，取首尾中点会落到行外——所以从中间往两边试，
        用 `anchorAt` 逐个验，返回**确实压在这个锚上**的那个点。"""
        href = "net:%s" % BUS.net_key(net)
        if not self.ensure_net_visible(net):
            return None
        span = self._net_span(net)
        if span is None:                      # pragma: no cover - 滚动不会改文档
            return None
        start, end = span
        doc = self.document()
        mid = (start + end) // 2
        for pos in sorted(range(start, end), key=lambda p: abs(p - mid)):
            cur = QtGui.QTextCursor(doc)
            cur.setPosition(pos)
            rect = self.cursorRect(cur)
            pt = QtCore.QPoint(rect.center().x() + 2, rect.center().y())
            if self.anchorAt(pt) == href:
                return pt
        return None

    def net_names(self):
        """文档里所有可点真名（去重、保序）——测试与自查用。"""
        doc = self.document()
        out, seen, last = [], set(), ""
        for pos in range(1, doc.characterCount()):
            cur = QtGui.QTextCursor(doc)
            cur.setPosition(pos)
            href = cur.charFormat().anchorHref()
            if href and href.startswith("net:") and href != last:
                key = href[4:]
                if key not in seen:
                    seen.add(key)
                    out.append(key)
            last = href
        return out

    def _on_anchor(self, url):
        text = url.toString()
        if text.startswith("net:"):
            self.netClicked.emit(text[4:])


def _linkify(text, highlight):
    """文本里的真名 → `<a href="net:key">`；返回 (html, 是否命中当前线网)。

    单字母（Excel 原式里的 A/B/C…）、`case` 这类关键字、`1'b0` 的进制字母都不当真名。
    """
    text = str(text or "")
    out, hit, pos = [], False, 0
    for m in _NET_RE.finditer(text):
        name = m.group(1)
        if len(name) < 2 or name.lower() in _NOT_NET:
            continue
        token = m.group(0)
        key = BUS.net_key(token)
        if not key:
            continue
        out.append(_esc(text[pos:m.start()]))
        style = "color:%s;" % TH.INK
        if key == highlight:
            hit = True
            style += "font-weight:700;"
        out.append('<a href="net:%s" style="%s">%s</a>' % (_esc(key), style, _esc(token)))
        pos = m.end()
    out.append(_esc(text[pos:]))
    return "".join(out), hit


# ═════════════════════════════ 四、下半「输入信号 N 个」═════════════════════════════
class InputsModel(QtCore.QAbstractTableModel):
    """两行一组：主行四列（字母 / 信号(位宽) / 角色 / 类型），第二行整行跨列的常驻驱动串。

    行全部来自 `inputs_table.input_rows(an)`——角色细分（C-060）、数据寄存器按物理寄存器收拢并
    标「被哪个 case 选中」（C-061）、dft 门殿后（C-062）都是它算好的，本模型只负责摆。
    """

    COLS = 4

    def __init__(self, parent=None):
        QtCore.QAbstractTableModel.__init__(self, parent)
        self._rows = []
        self._highlight = ""

    # ── 数据进出 ──
    def set_rows(self, rows):
        self.beginResetModel()
        self._rows = [dict(r) for r in (rows or ())]
        self.endResetModel()

    def rows(self):
        return [dict(r) for r in self._rows]

    def input_count(self):
        return len(self._rows)

    def row_at(self, row):
        """显示行号 → (输入行 dict, 是不是驱动行)；越界返回 (None, False)。"""
        i = int(row) // 2
        if not (0 <= i < len(self._rows)):
            return None, False
        return self._rows[i], bool(int(row) % 2)

    def set_highlight(self, net):
        key = BUS.net_key(net)
        if key == self._highlight:
            return
        self._highlight = key
        if self._rows:
            self.dataChanged.emit(self.index(0, 0),
                                  self.index(self.rowCount() - 1, self.COLS - 1),
                                  [Qt.BackgroundRole])

    def highlight(self):
        return self._highlight

    def highlighted_rows(self):
        """当前高亮的显示行号（两行一组都算）——测试用。"""
        if not self._highlight:
            return []
        return [r for r in range(self.rowCount())
                if BUS.net_key((self.row_at(r)[0] or {}).get("name")) == self._highlight]

    # ── QAbstractTableModel ──
    def rowCount(self, parent=QtCore.QModelIndex()):
        return 0 if parent.isValid() else len(self._rows) * 2

    def columnCount(self, parent=QtCore.QModelIndex()):
        return 0 if parent.isValid() else self.COLS

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if orientation == Qt.Horizontal and role == Qt.DisplayRole:
            if 0 <= section < len(T.SIDE_INPUTS_HEADERS):
                return T.SIDE_INPUTS_HEADERS[section]
        return None

    def flags(self, index):
        if not index.isValid():
            return Qt.NoItemFlags
        _row, is_drive = self.row_at(index.row())
        if is_drive:                      # 驱动行只看不选（选中语义归主行）
            return Qt.ItemIsEnabled
        return Qt.ItemIsEnabled | Qt.ItemIsSelectable

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        row, is_drive = self.row_at(index.row())
        if row is None:
            return None
        col = index.column()

        if role == ROLE_NET:
            return BUS.net_key(row.get("name"))
        if role == ROLE_IS_DRIVE:
            return is_drive
        if role == ROLE_INPUT_INDEX:
            return index.row() // 2
        if role == ROLE_GUESSED:
            return bool(row.get("guessed"))
        if role == ROLE_NEEDS_PREFIX:
            return bool(row.get("needs_prefix"))

        if role == Qt.DisplayRole:
            if is_drive:
                return drive_text(row) if col == 0 else ""
            # 名字 / 字母 / RO·RW 是标识符，一个字符都不许动；角色是后端写的说明文字 → 过 scrub（I-12）
            return (str(row.get("letter") or ""), str(row.get("name") or ""),
                    T.scrub(row.get("role") or ""), str(row.get("rw") or ""))[col]
        if role == Qt.ToolTipRole:
            return _row_tip(row) or None
        if role == Qt.TextAlignmentRole:
            return int(Qt.AlignLeft | Qt.AlignVCenter)
        if role == Qt.FontRole:
            if is_drive:
                return W.mono_font(TH.FS_MONO)
            if col in (0, 1):
                return W.mono_font(TH.FS_MONO, bold=bool(row.get("bold")))
            return W.ui_font(TH.FS_UI_SMALL, bold=bool(row.get("is_control")))
        if role == Qt.ForegroundRole:
            if is_drive:
                return QtGui.QColor(_drive_color(row))
            if col == 2 and row.get("is_dft_gate"):
                return QtGui.QColor(TH.BLUE_DARK)
            return QtGui.QColor(TH.INK if col in (0, 1) else TH.TEXT)
        if role == Qt.BackgroundRole:
            if self._highlight and BUS.net_key(row.get("name")) == self._highlight:
                return QtGui.QColor(TH.SELECTED_BG)
            if is_drive:
                return QtGui.QColor(TH.HINT_BG)
            if col == 1 and row.get("guessed"):
                return QtGui.QColor(TH.GUESS_BG)
            return QtGui.QColor(TH.WHITE)
        return None


class InputsDelegate(QtWidgets.QStyledItemDelegate):
    """猜名的信号名多画一条琥珀虚下划线（C-275：Design 的橙色虚下划线，QFont 画不出虚线）。"""

    def paint(self, painter, option, index):
        QtWidgets.QStyledItemDelegate.paint(self, painter, option, index)
        if index.column() != 1 or not index.data(ROLE_GUESSED) or index.data(ROLE_IS_DRIVE):
            return
        text = index.data(Qt.DisplayRole) or ""
        if not text:
            return
        painter.save()
        pen = QtGui.QPen(QtGui.QColor(TH.AMBER_STROKE))
        pen.setStyle(Qt.DashLine)
        pen.setWidth(1)
        painter.setPen(pen)
        fm = QtGui.QFontMetrics(index.data(Qt.FontRole) or option.font)
        w = min(fm.horizontalAdvance(str(text)), option.rect.width() - 8)
        y = option.rect.bottom() - 4
        painter.drawLine(option.rect.left() + 4, y, option.rect.left() + 4 + w, y)
        painter.restore()


class InputsView(QtWidgets.QTableView):
    """输入信号表：每个输入两行高（第二行跨四列放驱动串），点一行 → `netClicked`。"""

    netClicked = QtCore.Signal(str)

    def __init__(self, parent=None):
        QtWidgets.QTableView.__init__(self, parent)
        self.setObjectName(N.SIDE_INPUTS_VIEW)
        self.setModel(InputsModel(self))
        self.setItemDelegate(InputsDelegate(self))
        self.setShowGrid(False)
        self.setWordWrap(False)
        self.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.setFrameShape(QtWidgets.QFrame.NoFrame)
        self.setHorizontalScrollMode(QtWidgets.QAbstractItemView.ScrollPerPixel)
        self.setVerticalScrollMode(QtWidgets.QAbstractItemView.ScrollPerPixel)
        vh = self.verticalHeader()
        vh.setVisible(False)
        vh.setDefaultSectionSize(TH.ROW_H)
        hh = self.horizontalHeader()
        hh.setFixedHeight(TH.HEADER_H)
        hh.setHighlightSections(False)
        hh.setFont(W.ui_font(TH.FS_UI_SMALL))
        hh.setSectionResizeMode(1, QtWidgets.QHeaderView.Stretch)   # 「信号(位宽)」吃掉余下宽度
        self.setColumnWidth(0, TH.INPUTS_COL_W["letter"])           # 只在这儿设一次：换信号不该
        self.setColumnWidth(2, TH.INPUTS_COL_W["role"])             # 把用户拖过的列宽冲掉
        self.setColumnWidth(3, TH.INPUTS_COL_W["rw"])
        self.setStyleSheet(
            "QTableView{background:%s;color:%s;selection-background-color:%s;selection-color:%s;}"
            "QHeaderView::section{background:%s;color:%s;border:none;"
            "border-bottom:1px solid %s;padding-left:6px;}"
            % (TH.WHITE, TH.INK, TH.SELECTED_BG, TH.INK, TH.PANEL_BG, TH.MUTE, TH.BORDER))
        self.clicked.connect(self._on_clicked)

    def set_rows(self, rows):
        self.model().set_rows(rows)
        self.clearSpans()
        for i in range(self.model().input_count()):
            self.setSpan(2 * i + 1, 0, 1, InputsModel.COLS)      # 驱动行跨四列
            self.setRowHeight(2 * i, TH.ROW_H)
            self.setRowHeight(2 * i + 1, TH.INPUTS_ROW_H - TH.ROW_H)

    def set_highlight(self, net):
        self.model().set_highlight(net)
        rows = self.model().highlighted_rows()
        if rows:                              # 亮了却在可视区外 = 等于没亮
            self.scrollTo(self.model().index(rows[0], 0),
                          QtWidgets.QAbstractItemView.EnsureVisible)
        self.viewport().update()

    def _on_clicked(self, index):
        net = index.data(ROLE_NET)
        if net:
            self.netClicked.emit(net)


# ═════════════════════════════ 五、整块右栏 ═════════════════════════════
class SidePanel(QtWidgets.QWidget):
    """⑨ 右侧常驻栏：上「逐层展开」+ 下「输入信号 N 个」，中间可拖（chainH 默认 300）。

    与外界的往来只有三条线：
      · `set_state(state)` —— 听 `currentChanged` 自取 an（也可由 app 直接 `show_signal(an)`）；
      · `set_bus(bus)`     —— 当前线网双向：点 → `select`，收 → 只改底色（I-18）；
      · `sizeHint()`       —— 右栏整体隐藏/展开由 app 控制，这里只报一个 Design 默认宽。
    """

    def __init__(self, state=None, bus=None, parent=None):
        QtWidgets.QWidget.__init__(self, parent)
        self.setObjectName(N.SIDE_PANEL)
        self.state = None
        self.bus = None
        self._an = None
        self._conns = []

        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        self.splitter = QtWidgets.QSplitter(Qt.Vertical, self)
        self.splitter.setObjectName(N.WIN_SPLIT_CHAIN_INPUTS)
        self.splitter.setChildrenCollapsible(False)
        self.splitter.setHandleWidth(TH.HANDLE_W)
        self.splitter.setStyleSheet("QSplitter::handle{background:%s;}" % TH.HANDLE)
        lay.addWidget(self.splitter, 1)

        # ── 上半：逐层展开 ──
        chain_box = QtWidgets.QWidget(self.splitter)
        cl = QtWidgets.QVBoxLayout(chain_box)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(0)
        head = self._header(chain_box)
        self.chain_title = QtWidgets.QLabel(T.SIDE_CHAIN_TITLE, head)
        self.chain_title.setObjectName(N.SIDE_CHAIN_TITLE)
        self.chain_title.setFont(W.ui_font(TH.FS_UI_TITLE, bold=True))
        self.chain_title.setStyleSheet("color:%s;" % TH.INK)
        head.layout().addWidget(self.chain_title)
        head.layout().addStretch(1)
        self.chain_help = QtWidgets.QLabel(T.SIDE_CHAIN_HELP, head)        # C-222
        self.chain_help.setObjectName(N.SIDE_CHAIN_HELP)
        self.chain_help.setFont(W.ui_font(TH.FS_UI_SMALL))
        self.chain_help.setStyleSheet("color:%s;" % TH.MUTE)
        self.chain_help.setWordWrap(True)
        head.layout().addWidget(self.chain_help, 1)
        cl.addWidget(head)
        self.chain_view = ChainView(chain_box)
        cl.addWidget(self.chain_view, 1)
        chain_box.setMinimumHeight(TH.CLAMP_CHAIN[0])
        chain_box.setMaximumHeight(TH.CLAMP_CHAIN[1])

        # ── 下半：输入信号 ──
        inputs_box = QtWidgets.QWidget(self.splitter)
        il = QtWidgets.QVBoxLayout(inputs_box)
        il.setContentsMargins(0, 0, 0, 0)
        il.setSpacing(0)
        ihead = self._header(inputs_box)
        self.inputs_title = QtWidgets.QLabel(T.SIDE_INPUTS_TITLE_FMT.format(n=0), ihead)
        self.inputs_title.setObjectName(N.SIDE_INPUTS_TITLE)
        self.inputs_title.setFont(W.ui_font(TH.FS_UI_TITLE, bold=True))
        self.inputs_title.setStyleSheet("color:%s;" % TH.INK)
        ihead.layout().addWidget(self.inputs_title)
        ihead.layout().addStretch(1)
        self.guess_swatch = QtWidgets.QFrame(ihead)                        # 橙色虚线小方块
        self.guess_swatch.setFixedSize(12, 12)
        self.guess_swatch.setStyleSheet("background:%s;border:1px dashed %s;"
                                        % (TH.GUESS_BG, TH.AMBER_STROKE))
        ihead.layout().addWidget(self.guess_swatch)
        self.guess_badge = QtWidgets.QLabel(T.SIDE_INPUTS_GUESS_BADGE, ihead)   # C-275
        self.guess_badge.setObjectName(N.SIDE_INPUTS_GUESS_BADGE)
        self.guess_badge.setFont(W.ui_font(TH.FS_UI_SMALL))
        self.guess_badge.setStyleSheet("color:%s;" % TH.AMBER_FG)
        ihead.layout().addWidget(self.guess_badge)
        il.addWidget(ihead)
        self.inputs_view = InputsView(inputs_box)
        il.addWidget(self.inputs_view, 1)
        inputs_box.setMinimumHeight(TH.ROW_H * 3)

        self.splitter.addWidget(chain_box)
        self.splitter.addWidget(inputs_box)
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setSizes([TH.CHAIN_H, max(TH.CHAIN_H, 1)])

        self.setMinimumWidth(TH.CLAMP_SIDE[0])
        self.setMaximumWidth(TH.CLAMP_SIDE[1])

        self.chain_view.netClicked.connect(lambda net: self._emit_select(net, "chain"))
        self.inputs_view.netClicked.connect(lambda net: self._emit_select(net, "inputs"))

        self._set_guess_badge_visible(False)
        self.set_bus(bus)
        self.set_state(state)

    @staticmethod
    def _header(parent):
        box = QtWidgets.QWidget(parent)
        hl = QtWidgets.QHBoxLayout(box)
        hl.setContentsMargins(10, 6, 10, 6)
        hl.setSpacing(8)
        box.setStyleSheet("background:%s;" % TH.WHITE)
        return box

    # ── 尺寸（右栏整体隐藏 / 展开由 app 控制）──
    def sizeHint(self):
        return QtCore.QSize(TH.SIDE_W, TH.CHAIN_H + TH.INPUTS_ROW_H * 5)

    def minimumSizeHint(self):
        return QtCore.QSize(TH.CLAMP_SIDE[0], TH.CLAMP_CHAIN[0] + TH.ROW_H * 3)

    def chain_height(self):
        """当前「逐层展开」区高度（默认 `theme.CHAIN_H` = 300，可拖）。"""
        return self.splitter.sizes()[0]

    def set_chain_height(self, px):
        total = sum(self.splitter.sizes())
        h = max(TH.CLAMP_CHAIN[0], min(TH.CLAMP_CHAIN[1], int(px)))
        self.splitter.setSizes([h, max(total - h, 1)])

    # ── 接线 ──
    def set_bus(self, bus):
        """挂上高亮总线（I-18：当前线网只经它；收到广播只改底色，绝不回写选择）。"""
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
        """挂上会话状态：`currentChanged` 来了自取 an（app 也可以直接 `show_signal(an)`）。"""
        for obj, sig, slot in self._conns:
            try:
                getattr(obj, sig).disconnect(slot)
            except (RuntimeError, TypeError):
                pass
        self._conns = []
        self.state = state
        if state is None:
            self.refresh()
            return
        for sig, slot in (("currentChanged", self._on_current_changed),
                          ("modelUpdated", self._on_model_updated),
                          ("modelsChanged", self._on_models_changed),
                          ("configChanged", self._on_refresh_signal),
                          ("coverageChanged", self._on_refresh_signal),
                          ("editsChanged", self._on_refresh_signal),
                          ("scopeChanged", self._on_refresh_signal)):
            s = getattr(state, sig, None)
            if s is not None:
                s.connect(slot)
                self._conns.append((state, sig, slot))
        self.refresh()

    # ── 渲染 ──
    def show_signal(self, an):
        """把一个分析结果画出来（链 + 输入表）。`an` 为空 → 空态。"""
        self._an = an
        if not an:
            self.show_message("")
            return
        rows = IT.input_rows(an)
        self.chain_view.set_content(chain_blocks(an), status=chain_status(an))
        self.inputs_view.set_rows(rows)
        self.inputs_title.setText(T.SIDE_INPUTS_TITLE_FMT.format(n=len(rows)))
        self._set_guess_badge_visible(any(r.get("guessed") for r in rows))
        self._apply_highlight(getattr(self.bus, "current_net", "") if self.bus else "")

    def show_message(self, text):
        """占位文案（未选信号 / 还在后台展开 / 分析失败）。"""
        self._an = None
        self.chain_view.set_content((), status=None, message=text or "")
        self.inputs_view.set_rows([])
        self.inputs_title.setText(T.SIDE_INPUTS_TITLE_FMT.format(n=0))
        self._set_guess_badge_visible(False)

    def current_an(self):
        return self._an

    def refresh(self):
        """按 state 的当前信号重取 an 并重画（分析失败只落成一行文案，绝不抛到 app）。"""
        state = self.state
        if state is None:
            self.show_message("")
            return
        name = getattr(state, "current_name", "") or ""
        if not name:
            self.show_message("")
            return
        model = None
        try:
            model = state.model_of(name)
        except Exception:                      # noqa: BLE001 —— 取不到模型不影响画链
            model = None
        if model and str(model.get("status") or "") == "pending":
            self.show_message(T.HDR_PENDING)
            return
        try:
            an = state.analyze(name)
        except Exception as ex:                # noqa: BLE001 —— C-043：失败也只是一行字
            self.show_message("%s\n%s" % (T.HDR_ANALYSIS_FAILED, T.scrub(str(ex))))
            return
        if not an:
            self.show_message(T.HDR_ANALYSIS_FAILED)
            return
        self.show_signal(an)

    def _set_guess_badge_visible(self, on):
        self.guess_badge.setVisible(bool(on))
        self.guess_swatch.setVisible(bool(on))

    # ── 当前线网 ──
    def _emit_select(self, net, origin):
        if self.bus is not None:
            self.bus.select(net, origin)
        else:                                  # 没挂总线（单元测试 / app 还没接线）→ 本地也要看得见
            self._apply_highlight(net)

    def _on_net_selected(self, net, origin):
        """收到广播只更新样式——**不**回写选择（I-18：否则两个视图互相激发成死循环）。"""
        self._apply_highlight(net)

    def _apply_highlight(self, net):
        self.chain_view.set_highlight(net)
        self.inputs_view.set_highlight(net)

    # ── state 信号 ──
    def _on_current_changed(self, name=""):
        self.refresh()

    def _on_model_updated(self, view_id, name=None):
        cur = getattr(self.state, "current_name", "") if self.state else ""
        changed = name if name is not None else view_id
        if cur and str(changed).lower() == str(cur).lower():
            self.refresh()

    def _on_models_changed(self, view_id=""):
        self.refresh()

    def _on_refresh_signal(self, *args):
        self.refresh()


# ═════════════════════════════ 对 state / bus / an 的接口假设 ═══════════════════════
#: C2-int 接线时逐条核对（★ = 契约 `WorkbenchStateProto` 里没有、本模块另要的）
STATE_REQUIREMENTS = (
    ("current_name", "属性：当前信号名；空串 = 没选信号（本模块画空态）"),
    ("analyze(name)", "→ an dict：链与输入表的唯一数据源；抛异常 = 分析失败（本模块转成一行文案，"
                      "不再往上抛，C-043）"),
    ("model_of(name)", "→ lite 模型：只用来看 status 是不是 pending（拿不到就当不是）"),
    ("信号 currentChanged / modelUpdated / modelsChanged / configChanged / coverageChanged / "
     "editsChanged / scopeChanged", "有哪条接哪条，缺的静默跳过"),
    ("bus.netSelected(net, origin) / bus.select(net, origin) / bus.net_key(name)",
     "当前线网的唯一通道（I-18）；本模块收到广播只改底色，绝不回写"),
    ("an['chain'] = [{out, expr, subst, page, kind}]",
     "页标签取 `page`（缺 page 不显示标签、不猜）；mux 层的标签用 `out` 当组名"),
    ("an['sig'].expr / .group_no / an['bindings']",
     "★ 空链时的三种兜底（C-051 单级 logic 用 expr + bindings 代入真名；C-052 mux 根用 expr；"
     "C-053 直连寄存器只写一句话）——引擎若哪天把根也写进 chain，这段兜底自动失效、不用改"),
    ("inputs_table.input_rows(an)",
     "输入表全部内容：角色细分（C-060）、mux 数据寄存器收拢 + 被哪个 case 选中（C-061）、"
     "dft 门殿后（C-062）、needs_prefix / guessed / resolved 三个布尔（C-058/C-059/C-275）"),
    ("analysis_norm.subst_expr(expr, name_of)", "★ 单级 logic 的「= 代入真名」行（不在视图里自己写正则）"),
)
