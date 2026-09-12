# -*- coding: utf-8 -*-
"""widgets.py —— v2 共享小件（C1-d）。

只放「与业务无关、多个视图都要用」的控件与字体工具。依赖方向（架构 §1.2）：
    widgets → PySide6 + theme / names / terms
本模块**不 import** state / worker / 任何引擎模块，也**不 import `dreg_verify.gui`**——
`FlowLayout` / `CheckableMenu` 是从 `gui.py` 复制过来的（行为逐字不变，只改名去掉下划线前缀，C-263）。

公开 API：
    FlowLayout          —— 按可用宽度自动换行的布局（C-263）
    CheckableMenu       —— 点 checkable 项不关闭的菜单（owner 多选用）
    SegmentedControl    —— 分段按钮联排（范围 5 段；支持置灰项 + tooltip，C-042）
    ProgressBadge       —— 细进度条（详情标题栏 90×6 绿条 / 载入态 8px 满宽条）
    ErrorBar            —— 可关闭的红色错误条（C-005 / C-272；文本一律过 terms.scrub）
    Popover             —— 无边框浮层（覆盖度弹层 / 导出选项弹层；点外部自动关）
    mono_font(size, bold)   —— Consolas 回退栈（信号名 / 地址 / 数值 / 表达式）
    ui_font(size, bold)     —— 微软雅黑回退栈（界面正文）
"""

from PySide6 import QtCore, QtGui, QtWidgets

from . import names, terms, theme


# ═════════════════════════════ 字体 ═════════════════════════════
def _mk_font(families, size, bold, hint):
    """按回退栈造 QFont：`setFamilies` 让缺字体时自动落到下一个（offscreen / 同事机器上没有
    微软雅黑 / Consolas 也不会变成随机字体）。size 单位 px（theme 的 FS_* 就是 px）。"""
    f = QtGui.QFont()
    concrete = [x for x in families if "-" not in x]      # 去掉 sans-serif / monospace 这类 CSS 泛型名
    f.setFamily(concrete[0])                               # 先设首选，再补回退栈（顺序反了会把栈冲掉）
    try:
        f.setFamilies(list(concrete))
    except (AttributeError, TypeError):                    # 极老 Qt 没有 setFamilies
        pass
    f.setStyleHint(hint)
    f.setPixelSize(max(1, int(round(float(size)))))
    f.setBold(bool(bold))
    return f


def mono_font(size=theme.FS_MONO, bold=False):
    """等宽字体（C-264）：Consolas → Cascadia Mono → Menlo → DejaVu Sans Mono。"""
    return _mk_font(theme.FONT_MONO_FALLBACKS, size, bold, QtGui.QFont.Monospace)


def ui_font(size=theme.FS_UI, bold=False):
    """界面字体：微软雅黑 → Segoe UI → Noto Sans CJK SC。"""
    return _mk_font(theme.FONT_UI_FALLBACKS, size, bold, QtGui.QFont.SansSerif)


# ═════════════════════════════ FlowLayout（复制自 gui.py L200–267，行为不变）═════════════
class FlowLayout(QtWidgets.QLayout):
    """按钮按可用宽度自动换行的布局（Qt 官方 FlowLayout 示例改写）。

    用途：工具条按钮一多，普通 QHBoxLayout 会把"所有按钮宽度之和"作为父面板的最小宽度，
    导致 QSplitter 拖不动、面板被挤没。FlowLayout 在宽度不够时把按钮折到下一行，
    面板最小宽度 ≈ 单个最宽按钮，于是 splitter 可以自由拖动、按钮永不被吞（C-263）。
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

    # —— v2 便利方法（测试用：换行了没有）——
    def row_count(self, width):
        """给定可用宽度时会排成几行（`test_c263_flowlayout_wraps` 用）。"""
        m = self.contentsMargins()
        eff_right = width - m.right()
        x, rows, line_h = m.left(), 0, 0
        for it in self._items:
            w, h = it.sizeHint().width(), it.sizeHint().height()
            if x + w > eff_right and line_h > 0:
                x = m.left()
                rows += 1
                line_h = 0
            elif rows == 0:
                rows = 1
            x += w + self._hspace
            line_h = max(line_h, h)
        return rows


# ═════════════════════════ CheckableMenu（复制自 gui.py L275–284）═════════════════════
class CheckableMenu(QtWidgets.QMenu):
    """可勾选下拉菜单：点 checkable 项只切换勾选、不关闭菜单（owner 多选用）。
    普通项（不可勾）仍按默认行为关闭，键盘导航不受影响。"""

    def mouseReleaseEvent(self, e):
        act = self.activeAction()
        if act is not None and act.isEnabled() and act.isCheckable():
            act.trigger()          # 切换勾选 + 发 toggled，但不调用 super → 菜单留开
            return
        super().mouseReleaseEvent(e)


# ═════════════════════════════ SegmentedControl ═════════════════════════════
class SegmentedControl(QtWidgets.QWidget):
    """分段按钮联排（Design ② 范围 5 段：首段选中蓝底，其余白底 + 竖分隔）。

    · `add_item(key, label, object_name=None, tip="")` 逐段加；
    · `set_item_enabled(key, on, tip="")` 置灰某段并挂 tooltip（C-042「本表无 dft 页」）；
    · 选中变化发 `currentChanged(str key)`；程序调 `set_current` 也发（`block=True` 可静音）。
    """

    currentChanged = QtCore.Signal(str)

    def __init__(self, items=(), parent=None):
        super().__init__(parent)
        self._btns = {}                     # key -> QToolButton
        self._order = []
        self._current = ""
        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self._lay = lay
        self._group = QtWidgets.QButtonGroup(self)
        self._group.setExclusive(True)
        for it in items or ():
            self.add_item(*it)

    # ── 构造 ──
    def add_item(self, key, label, object_name=None, tip=""):
        b = QtWidgets.QToolButton(self)
        b.setObjectName(object_name or ("seg_%s" % key))
        b.setText(str(label))
        b.setCheckable(True)
        b.setFont(ui_font(theme.FS_UI))
        b.setFixedHeight(theme.BTN_H)
        b.setCursor(QtCore.Qt.PointingHandCursor)
        b.setToolButtonStyle(QtCore.Qt.ToolButtonTextOnly)
        if tip:
            b.setToolTip(tip)
        b.clicked.connect(lambda _=False, k=key: self.set_current(k))
        self._group.addButton(b)
        self._lay.addWidget(b)
        self._btns[key] = b
        self._order.append(key)
        self._restyle()
        if len(self._order) == 1:
            self.set_current(key, block=True)
        return b

    # ── 查询 ──
    def keys(self):
        return list(self._order)

    def button(self, key):
        return self._btns.get(key)

    def current(self):
        return self._current

    def is_item_enabled(self, key):
        b = self._btns.get(key)
        return bool(b is not None and b.isEnabled())

    # ── 改状态 ──
    def set_item_enabled(self, key, on, tip=""):
        """置灰 / 恢复某一段；置灰时若它正选中，自动退到第一个可用段。"""
        b = self._btns.get(key)
        if b is None:
            return
        b.setEnabled(bool(on))
        b.setToolTip(tip or "")
        if not on and self._current == key:
            for k in self._order:
                if self._btns[k].isEnabled():
                    self.set_current(k)
                    break
        self._restyle()

    def set_current(self, key, block=False):
        if key not in self._btns:
            return
        b = self._btns[key]
        if not b.isEnabled():
            return
        changed = key != self._current
        self._current = key
        for k, w in self._btns.items():
            w.setChecked(k == key)
        self._restyle()
        if changed and not block:
            self.currentChanged.emit(key)

    def _restyle(self):
        for i, k in enumerate(self._order):
            b = self._btns[k]
            left = "0px" if i == 0 else "1px"
            b.setStyleSheet(
                "QToolButton{background:%s;color:%s;border:1px solid %s;border-left-width:%s;"
                "padding:2px 10px;}"
                "QToolButton:checked{background:%s;color:%s;border-color:%s;font-weight:700;}"
                "QToolButton:disabled{background:%s;color:%s;}"
                % (theme.WHITE, theme.TEXT, theme.BORDER_SEG, left,
                   theme.BLUE, theme.WHITE, theme.BLUE,
                   theme.DISABLED_BG, theme.DISABLED_TEXT))


# ═════════════════════════════ ProgressBadge ═════════════════════════════
class ProgressBadge(QtWidgets.QWidget):
    """细进度条：详情标题栏 90×6 绿条（C-106）与载入态满宽 8px 蓝条（C-276）共用一个件。

    `set_value(n, m)` 后 `ratio()` = n/m（m<=0 时 0）；`text()` 给测试读「n/m」。"""

    def __init__(self, parent=None, width=theme.PROGRESS_W, height=theme.PROGRESS_H,
                 color=theme.OK_FG, stretch=False):
        super().__init__(parent)
        self._n = 0
        self._m = 0
        self._color = color
        self.setFixedHeight(int(height))
        if stretch:
            self.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        else:
            self.setFixedWidth(int(width))

    def set_value(self, n, m):
        self._n, self._m = int(n or 0), int(m or 0)
        self.update()

    def value(self):
        return (self._n, self._m)

    def ratio(self):
        return (float(self._n) / float(self._m)) if self._m > 0 else 0.0

    def text(self):
        """测试用文本表示（本件自己不画字，字在旁边的 QLabel 上）。"""
        return "%d/%d" % (self._n, self._m)

    def paintEvent(self, _e):
        p = QtGui.QPainter(self)
        r = self.rect()
        p.fillRect(r, QtGui.QColor(theme.DISABLED_BG))
        w = int(round(r.width() * min(1.0, max(0.0, self.ratio()))))
        if w > 0:
            p.fillRect(QtCore.QRect(r.x(), r.y(), w, r.height()), QtGui.QColor(self._color))
        p.end()


# ═════════════════════════════ ErrorBar ═════════════════════════════
class ErrorBar(QtWidgets.QFrame):
    """红色错误条（C-005 / C-272）：载表出错用工程师语言写在这条上，**不弹窗、不退出**。

    `show_error(text)` 的文本一律先过 `terms.scrub`（I-12）；右侧「×」可关，关了发 `dismissed`。
    默认隐藏。"""

    dismissed = QtCore.Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName(names.ERROR_BAR)
        self.setFrameShape(QtWidgets.QFrame.NoFrame)
        self.setStyleSheet("QFrame#%s{background:%s;border:1px solid %s;}"
                           % (names.ERROR_BAR, theme.BAD_BG, theme.AMBER_BORDER))
        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(12, 6, 8, 6)
        lay.setSpacing(8)
        self.label = QtWidgets.QLabel(self)
        self.label.setObjectName(names.ERROR_TEXT)
        self.label.setWordWrap(True)
        self.label.setFont(ui_font(theme.FS_UI))
        self.label.setStyleSheet("color:%s;" % theme.RED_DARK)
        self.label.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        lay.addWidget(self.label, 1)
        self.close_btn = QtWidgets.QToolButton(self)
        self.close_btn.setObjectName(names.ERROR_CLOSE)
        self.close_btn.setText("×")
        self.close_btn.setCursor(QtCore.Qt.PointingHandCursor)
        self.close_btn.setStyleSheet("QToolButton{border:none;color:%s;font-size:14px;}" % theme.RED_DARK)
        self.close_btn.clicked.connect(self.dismiss)
        lay.addWidget(self.close_btn, 0)
        self.hide()

    def show_error(self, text):
        """显示一条错误（文本过 scrub；空文本 = 隐藏）。返回最终显示的文本。"""
        msg = terms.scrub(str(text or ""))
        self.label.setText(msg)
        self.setVisible(bool(msg))
        return msg

    def text(self):
        return self.label.text()

    def dismiss(self):
        self.label.setText("")
        self.hide()
        self.dismissed.emit()


# ═════════════════════════════ Popover ═════════════════════════════
class Popover(QtWidgets.QFrame):
    """无边框浮层（覆盖度弹层⑯ / 导出选项弹层）：点浮层外部自动关闭。

    用法：`pop = Popover(parent, width=theme.COV_POP_W)`；往 `pop.body` 里塞内容；
    `pop.open_at(QPoint)` 打开（坐标相对 parent），`pop.close_popover()` 关。
    """

    closed = QtCore.Signal()

    def __init__(self, parent=None, width=None, object_name=None):
        super().__init__(parent)
        if object_name:
            self.setObjectName(object_name)
        self.setFrameShape(QtWidgets.QFrame.NoFrame)
        self.setAutoFillBackground(True)
        self.setStyleSheet("QFrame{background:%s;border:1px solid %s;}" % (theme.WHITE, theme.BORDER))
        if width:
            self.setFixedWidth(int(width))
        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(1, 1, 1, 1)
        self.body = QtWidgets.QWidget(self)
        outer.addWidget(self.body)
        self._body_lay = QtWidgets.QVBoxLayout(self.body)
        self._body_lay.setContentsMargins(14, 12, 14, 12)
        self._body_lay.setSpacing(8)
        self._filtering = False
        self.hide()

    def content_layout(self):
        return self._body_lay

    def open_at(self, pos):
        """在 parent 坐标系的 pos 处打开（Design 的 covPopStyle: left 420 top 40）。"""
        self.move(QtCore.QPoint(*pos) if isinstance(pos, tuple) else pos)
        self.show()
        self.raise_()
        if not self._filtering:
            app = QtWidgets.QApplication.instance()
            if app is not None:
                app.installEventFilter(self)
                self._filtering = True

    def close_popover(self):
        if self._filtering:
            app = QtWidgets.QApplication.instance()
            if app is not None:
                app.removeEventFilter(self)
            self._filtering = False
        if self.isVisible():
            self.hide()
            self.closed.emit()

    def is_open(self):
        return self.isVisible()

    def eventFilter(self, obj, ev):
        if ev.type() == QtCore.QEvent.MouseButtonPress and self.isVisible():
            try:
                gp = ev.globalPosition().toPoint()
            except AttributeError:                     # 老 Qt：globalPos()
                gp = ev.globalPos()
            if not self.rect().contains(self.mapFromGlobal(gp)):
                self.close_popover()
        return False
