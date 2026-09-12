# -*- coding: utf-8 -*-
"""coverage.py —— ⑯ 覆盖度弹层 + ④ 详情标题栏里的覆盖度按钮（GUI v2 Phase C1-c）。

Design 对照：docs/GUI_v2_Design对齐_20260912.md §1.2 ⑯（covPopStyle width 760、表格 150/1fr/128、
生效两行底色 #eef4fd）+ §4 冲突⑧（三层继承链由 `session.CoverageState.effective_chain` 出，N7 已交付）。
架构简报：docs/GUI_v2_架构_20260912.md §6.5；契约 ID = 附录 A「COV」18 条
    C-071 C-133 C-142 C-143 C-144 C-145 C-146 C-147 C-148 C-149 C-150 C-151 C-152 C-153 C-154
    C-155 C-156 C-245。

「1 个控件、1 套模型」（C-155/C-156）：全部三层都读写**同一个** `session.CoverageState`
（`state.coverage(view_id)`），本模块不另存一份档位。改档后调 `state.coverage_touched(view_id)`
让 state 去发 coverageChanged（C-152/C-153/C-154 的重算规则判定在 state 里，本模块只发信号）。

⚠ 术语红线（I-12/I-13）：`session.FORM_LABELS` / `CoverageState.effective_label` 的来源串带
形态编号后缀（terms.TERMS 第 2 行点名的那批），属 `terms.FORBIDDEN`。
界面上的逻辑类型名一律取 `terms.FORM_LABELS`，
生效来源串由本模块按 `terms.COV_CHAIN_FMT` 自己拼，不直接显示后端给的 level 串。

临时自建控件（C1-int 集成时替换）：`_Pop` → `ui/widgets.py` 的 `Popover`（C1-d 交付）。
"""

from PySide6 import QtCore, QtGui, QtWidgets

from dreg_verify import session
from dreg_verify.ui import names, terms, theme

#: 逻辑类型四行的键（= session.FORM_COV_ROWS 的第一列，顺序即 Design covRows 2–5 行）
FORM_KEYS = tuple(k for k, _n, _e, _t in session.FORM_COV_ROWS)
#: 形态键 → 例子表达式（C-146，后端文本已 scrub）
FORM_EXAMPLES = {k: terms.scrub(e) for k, _n, e, _t in session.FORM_COV_ROWS}

#: ⚠ names.py 里还没有「弹层内联帮助正文」的名字。已回报主控补 `COV_HELP_TEXT`（或 `COV_HELP_PANEL`）；
#: 补进去之后删掉本常量、改从 names 取。
HELP_TEXT_OBJECT_NAME = "cov_help_text"


def _ui_font(size=theme.FS_UI, bold=False):
    f = QtGui.QFont(theme.FONT_UI)
    f.setPixelSize(int(round(size)))
    f.setBold(bool(bold))
    return f


def _mono_font(size=theme.FS_MONO, bold=False):
    f = QtGui.QFont(theme.FONT_MONO)
    f.setPixelSize(int(round(size)))
    f.setBold(bool(bold))
    return f


def form_display(form_key):
    """逻辑类型的**界面名**（terms 版，无 F 编号）。未判定 → 空串。"""
    return terms.FORM_LABELS.get(form_key or "", "")


def source_text(cov, name, models=None, form_key=None):
    """详情标题栏按钮里的「来源」（C-148/C-156），去掉后端来源串里的 F 编号。"""
    label, src = cov.effective_label(name, models=models, form_key=form_key)
    if src == "本信号":
        return label, terms.COV_LEVEL_SIG
    if src == "全局默认":
        return label, terms.COV_LEVEL_GLOBAL
    return label, terms.COV_LEVEL_FORM_FMT.format(form=form_display(form_key))


def chain_text(cov, name, models=None, form_key=None):
    """蓝条「生效来源：本信号「跟随上级」→ 逻辑类型「选路」= 全面 → 全局默认 = 全面」（C-150）。

    三层数据来自 `CoverageState.effective_chain`（N7），层名换成 terms 版（无 F 编号）。"""
    chain = cov.effective_chain(name, models=models, form_key=form_key)
    return terms.COV_CHAIN_FMT.format(sig=chain[0]["label"],
                                      form=form_display(form_key),
                                      form_label=chain[1]["label"],
                                      global_label=chain[2]["label"])


# ═════════════════════ 临时控件（C1-int 换 widgets.Popover）═════════════════════

class _Pop(QtWidgets.QFrame):
    """无边框弹层：挂在窗口上盖住标题栏，点外部关闭。

    ⚠ 临时件：C1-int 换成 `ui/widgets.py` 的 `Popover`（C1-d 交付），
    换的时候保证 `popup(anchor)` / `close_pop()` / `closed` 三个口不变。"""

    closed = QtCore.Signal()

    def __init__(self, parent=None):
        super(_Pop, self).__init__(parent)
        self.setFrameShape(QtWidgets.QFrame.NoFrame)
        self.setAutoFillBackground(True)
        self.setStyleSheet("QFrame#%s{background:%s;border:1px solid %s;border-radius:4px;}"
                           % (names.COV_POPOVER, theme.WHITE, theme.BORDER))
        self.hide()

    def popup(self, anchor):
        """在 anchor 下方弹出（Design：covPop 挂在详情标题栏上，left 420 / top 40）。"""
        win = anchor.window() if anchor is not None else self.parentWidget()
        if win is not None and self.parentWidget() is not win:
            self.setParent(win)
        # 子控件弹层不受父布局管理 —— 自己按内容定高，否则内部控件会被压成 0 高
        self.adjustSize()
        self.resize(self.width(), max(self.height(), self.sizeHint().height()))
        if anchor is not None and win is not None:
            pt = anchor.mapTo(win, QtCore.QPoint(0, anchor.height() + 2))
            x = max(8, min(pt.x(), max(8, win.width() - self.width() - 8)))
            y = max(0, min(pt.y(), max(0, win.height() - self.height())))
            self.move(x, y)
        self.show()
        self.raise_()
        app = QtWidgets.QApplication.instance()
        if app is not None:
            app.installEventFilter(self)

    def close_pop(self):
        app = QtWidgets.QApplication.instance()
        if app is not None:
            app.removeEventFilter(self)
        if self.isVisible():
            self.hide()
            self.closed.emit()

    def eventFilter(self, obj, ev):
        if self.isVisible() and ev.type() == QtCore.QEvent.MouseButtonPress:
            w = obj if isinstance(obj, QtWidgets.QWidget) else None
            if w is None or not (w is self or self.isAncestorOf(w)):
                self.close_pop()
        return False


# ═════════════════════ 覆盖度控件 ═════════════════════

class CoverageControl(QtWidgets.QWidget):
    """④ 标题栏覆盖度按钮 + ⑯ 弹层（三层表格 / 上限 / 帮助）。

    对外信号：
        coverageChanged(str level, object value)
            level ∈ {"global", "form", "sig", "max_tests"}；
            value = 全局中文档名 / {形态键: 档位键} / 档位键或"" / int 上限。
            发之前已经写进 `session.CoverageState` 并调过 `state.coverage_touched(view_id)`。
        popoverToggled(bool)   弹层开合（组合根拿去把按钮画成蓝框蓝底）。
    """

    coverageChanged = QtCore.Signal(str, object)
    popoverToggled = QtCore.Signal(bool)

    def __init__(self, state=None, parent=None):
        super(CoverageControl, self).__init__(parent)
        self.setObjectName("cov_control")
        self._state = state
        self._name = ""
        self._mute = 0
        self._form_combos = {}
        self._row_widgets = {}          # 行键（"global"/形态键/"sig"）→ [层名 label, 说明 label]
        self._build()
        self.refresh()

    # ───────── 构建 ─────────
    def _build(self):
        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.button = QtWidgets.QToolButton(self)
        self.button.setObjectName(names.HDR_COV_BTN)
        self.button.setFont(_ui_font())
        self.button.setFixedHeight(theme.BTN_H)
        self.button.setCursor(QtCore.Qt.PointingHandCursor)
        self.button.setStyleSheet(
            "QToolButton{background:%s;color:%s;border:1px solid %s;border-radius:3px;padding:2px 10px;}"
            "QToolButton:checked{background:%s;color:%s;border-color:%s;}"
            % (theme.WHITE, theme.TEXT, theme.BORDER,
               theme.LIGHT_BLUE_BG, theme.LIGHT_BLUE_FG, theme.BLUE))
        self.button.setCheckable(True)
        self.button.clicked.connect(self._on_button)
        lay.addWidget(self.button)

        self.pop = _Pop(self)
        self.pop.setObjectName(names.COV_POPOVER)
        self.pop.setFixedWidth(theme.COV_POP_W)
        self.pop.closed.connect(self._on_pop_closed)
        self._build_pop()

    def _build_pop(self):
        v = QtWidgets.QVBoxLayout(self.pop)
        v.setContentsMargins(14, 12, 14, 12)
        v.setSpacing(8)

        head = QtWidgets.QHBoxLayout()
        self.title = QtWidgets.QLabel(self.pop)
        self.title.setObjectName(names.COV_TITLE)
        self.title.setFont(_ui_font(theme.FS_UI_TITLE, bold=True))
        head.addWidget(self.title, 1)
        self.close_btn = QtWidgets.QToolButton(self.pop)
        self.close_btn.setObjectName(names.COV_CLOSE_BTN)
        self.close_btn.setText(terms.COV_CLOSE)
        self.close_btn.setFont(_ui_font(theme.FS_UI_SMALL))
        self.close_btn.clicked.connect(lambda _c=False: self.close_popover())
        head.addWidget(self.close_btn)
        v.addLayout(head)

        self.chain_bar = QtWidgets.QLabel(self.pop)                 # C-150
        self.chain_bar.setObjectName(names.COV_CHAIN_BAR)
        self.chain_bar.setFont(_ui_font(theme.FS_UI_SMALL))
        self.chain_bar.setWordWrap(True)
        self.chain_bar.setStyleSheet("background:%s;color:%s;border:1px solid %s;"
                                     "border-radius:3px;padding:5px 8px;"
                                     % (theme.LIGHT_BLUE_BG, theme.LIGHT_BLUE_FG,
                                        theme.LIGHT_BLUE_BORDER))
        v.addWidget(self.chain_bar)

        self.table = QtWidgets.QWidget(self.pop)                    # 6 行三列
        self.table.setObjectName(names.COV_TABLE)
        g = QtWidgets.QGridLayout(self.table)
        g.setContentsMargins(0, 0, 0, 0)
        g.setHorizontalSpacing(10)
        g.setVerticalSpacing(4)
        lw, _flex, cw = theme.COV_TABLE_COLS
        g.setColumnMinimumWidth(0, lw)
        g.setColumnStretch(1, 1)
        g.setColumnMinimumWidth(2, cw)

        # 第 1 行：全局默认（C-142）
        self.global_combo = QtWidgets.QComboBox(self.table)
        self.global_combo.setObjectName(names.COV_GLOBAL_COMBO)
        for lab in session.COV_LABELS:
            self.global_combo.addItem(lab, lab)
        self.global_combo.currentIndexChanged.connect(self._on_global)
        self._add_row(g, 0, "global", terms.COV_LEVEL_GLOBAL, terms.COV_LEVEL_GLOBAL_DESC,
                      self.global_combo, tip="")

        # 第 2–5 行：按逻辑类型（C-145 / C-146）
        for i, (key, _disp, example, tip) in enumerate(session.FORM_COV_ROWS):
            cb = QtWidgets.QComboBox(self.table)
            cb.setObjectName(names.fmt_cov_form_combo(key))
            cb.addItem(terms.COV_FOLLOW_GLOBAL, "")
            for lab in session.COV_LABELS:
                cb.addItem(lab, session.LABEL_TO_COV[lab])
            cb.currentIndexChanged.connect(self._on_form)
            self._form_combos[key] = cb
            self._add_row(g, 1 + i, key,
                          terms.COV_LEVEL_FORM_FMT.format(form=terms.FORM_LABELS.get(key, key)),
                          terms.scrub(example), cb, tip=terms.scrub(tip), mono_desc=True)

        # 第 6 行：本信号（C-071 / C-147）
        self.sig_combo = QtWidgets.QComboBox(self.table)
        self.sig_combo.setObjectName(names.COV_SIG_COMBO)
        self.sig_combo.addItem(terms.COV_FOLLOW_UP, "")
        for lab in session.COV_LABELS:
            self.sig_combo.addItem(lab, session.LABEL_TO_COV[lab])
        self.sig_combo.currentIndexChanged.connect(self._on_sig)
        self._add_row(g, 5, "sig", terms.COV_LEVEL_SIG, "", self.sig_combo, tip="", mono_desc=True)
        v.addWidget(self.table)

        # 底行：上限 + 用例数 + 帮助
        foot = QtWidgets.QHBoxLayout()
        maxt_label = QtWidgets.QLabel(terms.COV_MAXT, self.pop)
        maxt_label.setFont(_ui_font())
        foot.addWidget(maxt_label)
        self.maxt_spin = QtWidgets.QSpinBox(self.pop)               # C-143
        self.maxt_spin.setObjectName(names.COV_MAXT_SPIN)
        self.maxt_spin.setRange(*session.MAX_TESTS_RANGE)
        self.maxt_spin.setValue(session.DEFAULT_MAX_TESTS)
        self.maxt_spin.setFont(_mono_font())
        self.maxt_spin.valueChanged.connect(self._on_maxt)
        foot.addWidget(self.maxt_spin)
        self.count_label = QtWidgets.QLabel(self.pop)               # C-133
        self.count_label.setObjectName(names.COV_COUNT_LABEL)
        self.count_label.setFont(_ui_font())
        self.count_label.setStyleSheet("color:%s;" % theme.MUTE)
        foot.addWidget(self.count_label)
        foot.addStretch(1)
        self.help_btn = QtWidgets.QToolButton(self.pop)             # C-144
        self.help_btn.setObjectName(names.COV_HELP_BTN)
        self.help_btn.setText(terms.COV_HELP)
        self.help_btn.setFont(_ui_font(theme.FS_UI_SMALL))
        self.help_btn.setCheckable(True)
        self.help_btn.setStyleSheet("QToolButton{border:none;color:%s;text-decoration:underline;}"
                                    % theme.BLUE)
        self.help_btn.setCursor(QtCore.Qt.PointingHandCursor)
        self.help_btn.toggled.connect(self._on_help)
        foot.addWidget(self.help_btn)
        v.addLayout(foot)

        self.help_text = QtWidgets.QLabel(terms.COV_HELP_TEXT, self.pop)
        self.help_text.setObjectName(HELP_TEXT_OBJECT_NAME)
        self.help_text.setFont(_ui_font(theme.FS_UI_SMALL))
        self.help_text.setWordWrap(True)
        self.help_text.setStyleSheet("background:%s;color:%s;border:1px solid %s;padding:6px 8px;"
                                     % (theme.HINT_BG, theme.TEXT, theme.BORDER_LIGHT))
        self.help_text.hide()
        v.addWidget(self.help_text)

    def _add_row(self, grid, r, key, level, desc, combo, tip="", mono_desc=False):
        lv = QtWidgets.QLabel(level, self.table)
        lv.setFont(_ui_font())
        ds = QtWidgets.QLabel(desc, self.table)
        ds.setFont(_mono_font() if mono_desc else _ui_font(theme.FS_UI_SMALL))
        ds.setWordWrap(True)
        if tip:
            lv.setToolTip(tip)
            ds.setToolTip(tip)
            combo.setToolTip(tip)
        combo.setFont(_ui_font())
        combo.setFixedHeight(theme.BTN_H)
        grid.addWidget(lv, r, 0)
        grid.addWidget(ds, r, 1)
        grid.addWidget(combo, r, 2)
        self._row_widgets[key] = [lv, ds]

    # ───────── state ─────────
    def set_state(self, state):
        self._state = state
        self.refresh()

    def state(self):
        return self._state

    def _view_id(self):
        return getattr(self._state, "scope", "") or "topout"

    def _coverage(self):
        st = self._state
        if st is None or not hasattr(st, "coverage"):
            return None
        try:
            return st.coverage(self._view_id())
        except Exception:                                          # noqa: BLE001
            return None

    def _models(self):
        st = self._state
        if st is None or not hasattr(st, "models"):
            return []
        try:
            return list(st.models(self._view_id()) or [])
        except Exception:                                          # noqa: BLE001
            return []

    def _form_key(self):
        return session.form_key_of(self._models(), self._name) if self._name else None

    def _touch(self):
        """改档后让 state 发 coverageChanged（C-152/C-153/C-154 的重算规则在 state 里判）。"""
        fn = getattr(self._state, "coverage_touched", None)
        if callable(fn):
            fn(self._view_id())

    # ───────── 当前信号（C-149：只回显，不重算）─────────
    def set_current(self, name):
        self._name = name or ""
        self.refresh()

    def current(self):
        return self._name

    # ───────── 弹层开合（C-156）─────────
    def _on_button(self, *_a):
        if self.pop.isVisible():
            self.close_popover()
        else:
            self.open_popover()

    def open_popover(self):
        self.refresh()
        self.pop.popup(self.button)
        self.button.setChecked(True)
        self.popoverToggled.emit(True)

    def close_popover(self):
        self.pop.close_pop()
        self.button.setChecked(False)

    def _on_pop_closed(self):
        self.button.setChecked(False)
        self.popoverToggled.emit(False)

    def is_open(self):
        return self.pop.isVisible()

    # ───────── 改档 ─────────
    def _on_global(self, *_a):
        if self._mute:
            return
        cov = self._coverage()
        if cov is None:
            return
        label = self.global_combo.currentData() or session.DEFAULT_COV_LABEL
        cov.persist_global_label(label)
        if self._name:
            cov.set_sig_cov(self._name, "")        # C-151：动全局 → 清当前信号单点档
        self._touch()
        self.refresh()
        self.coverageChanged.emit("global", label)

    def _on_form(self, *_a):
        if self._mute:
            return
        cov = self._coverage()
        if cov is None:
            return
        mapping = {k: (cb.currentData() or "") for k, cb in self._form_combos.items()}
        cov.set_form_cov(mapping)
        self._touch()
        self.refresh()
        self.coverageChanged.emit("form", {k: v for k, v in mapping.items() if v})

    def _on_sig(self, *_a):
        if self._mute:
            return
        cov = self._coverage()
        if cov is None or not self._name:
            return
        mode = self.sig_combo.currentData() or ""
        cov.set_sig_cov(self._name, mode)          # C-245：单点档只在会话内，不写 settings
        self._touch()
        self.refresh()
        self.coverageChanged.emit("sig", mode)

    def _on_maxt(self, *_a):
        if self._mute:
            return
        cov = self._coverage()
        if cov is None:
            return
        n = int(self.maxt_spin.value())
        cov.persist_max_tests(n)
        self._touch()
        self.refresh()
        self.coverageChanged.emit("max_tests", n)

    def _on_help(self, on):
        self.help_text.setVisible(bool(on))
        if self.pop.isVisible():                               # 展开说明后弹层要长高
            self.pop.adjustSize()
            self.pop.resize(self.pop.width(), self.pop.sizeHint().height())

    # ───────── 回显 ─────────
    def refresh(self):
        cov = self._coverage()
        if cov is None:
            self.button.setText(terms.HDR_COV_FMT.format(label=session.DEFAULT_COV_LABEL,
                                                         source=terms.COV_LEVEL_GLOBAL))
            return
        models = self._models()
        fkey = self._form_key()
        label, src = source_text(cov, self._name, models=models, form_key=fkey)
        self._mute += 1
        try:
            self.button.setText(terms.HDR_COV_FMT.format(label=label, source=src))
            self.button.setToolTip(chain_text(cov, self._name, models=models, form_key=fkey))
            self.title.setText(terms.COV_TITLE_FMT.format(label=label))
            self.chain_bar.setText(chain_text(cov, self._name, models=models, form_key=fkey))
            self._set_combo(self.global_combo, cov.global_label)
            for k, cb in self._form_combos.items():
                self._set_combo(cb, cov.form_cov.get(k, ""))
            self._set_combo(self.sig_combo, cov.sig_cov_of(self._name) if self._name else "")
            self.sig_combo.setEnabled(bool(self._name))
            self._row_widgets["sig"][1].setText(self._name or "")
            for k in FORM_KEYS:                                    # C-146 「← 本信号属这类」
                self._row_widgets[k][1].setText(
                    FORM_EXAMPLES[k] + (terms.COV_THIS_FORM_MARK if k == fkey else ""))
            self.maxt_spin.setValue(int(cov.max_tests))
            self.count_label.setText(self._count_text())
            self._paint_active(cov, models, fkey)
        finally:
            self._mute -= 1

    @staticmethod
    def _set_combo(combo, data):
        idx = combo.findData(data)
        combo.setCurrentIndex(idx if idx >= 0 else 0)

    def _paint_active(self, cov, models, fkey):
        """C-150：生效的两行（或单点档时的一行）底 LIGHT_BLUE_BG + 蓝粗体。"""
        chain = cov.effective_chain(self._name, models=models, form_key=fkey)
        active = {"sig": chain[0]["active"], "global": chain[2]["active"]}
        for k in FORM_KEYS:
            active[k] = bool(chain[1]["active"] and k == fkey)
        on = ("background:%s;color:%s;" % (theme.LIGHT_BLUE_BG, theme.LIGHT_BLUE_FG))
        for key, widgets in self._row_widgets.items():
            hit = bool(active.get(key))
            for w in widgets:
                w.setStyleSheet(on if hit else "")
                f = w.font()
                f.setBold(hit)
                w.setFont(f)

    def active_rows(self):
        """当前高亮的行键集合（测试与 C1-int 用）。"""
        out = set()
        for key, widgets in self._row_widgets.items():
            if widgets and widgets[0].font().bold():
                out.add(key)
        return out

    def _count_text(self):
        """C-133：「本信号当前 N 条」/ 已自定义时「…，含 X 反例（已自定义）」。"""
        if not self._name:
            return ""
        st = self._state
        rec = None
        if st is not None and hasattr(st, "edit_of"):
            try:
                rec = st.edit_of(self._name)
            except Exception:                                      # noqa: BLE001
                rec = None
        cols = (rec or {}).get("cols") or []
        if cols:
            neg = sum(1 for c in cols if c.get("neg"))
            return terms.COV_COUNT_CUSTOM_FMT.format(n=len(cols), neg=neg)
        m = None
        if st is not None and hasattr(st, "model_of"):
            try:
                m = st.model_of(self._name)
            except Exception:                                      # noqa: BLE001
                m = None
        n = (m or {}).get("n_vectors")
        return terms.COV_COUNT_FMT.format(n=n) if n is not None else ""
