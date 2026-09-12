# -*- coding: utf-8 -*-
"""detail_header.py —— ④ 详情标题栏（GUI v2 Phase C2-c）。

Design 对照：docs/GUI_v2_Design对齐_20260912.md §1.2 ④
    curName（Consolas 16/700）· curStatus 状态徽标 ·「owner X · 分类 · 用例 N」
    ｜「覆盖度 … ▾」按钮位（`ui/coverage.CoverageControl`，文案由它自己渲染）
    ｜右侧：「手填期望 7/25」+ 90×6 绿条 +「其中 K 条与程序算的不一致」·「解析明细」· 右栏开合。
架构简报：docs/GUI_v2_架构_20260912.md §6.6；契约 ID = 附录 A「HDR」6 条
    C-043 分析失败（已捕获，未崩）+ 原因全文
    C-055 头部一行摘要：名 / owner / 分类 / 状态 / 用例数
    C-064 「解析明细」按钮 → 面板（inputs_table.resolve_detail）
    C-065 解析明细里输入来源翻成中文（FOUND_IN_TEXT）
    C-066 解析明细末尾「仿真器找不到这根网」三步提示 + 直链诊断
    C-106 手填期望 N/M + 进度条 + 「其中 K 条与程序算的不一致」

⚠ 术语红线（I-12 / I-13）：
  · 覆盖度按钮文案**只**由 `CoverageControl` 渲染 —— `session.CoverageState.effective_label`
    的来源串带形态编号（`terms.FORBIDDEN` 点名的那批），本模块一个字都不转手；
  · 一切后端文本（note / issues / 解析明细）先过 `terms.scrub`。

分层（I-19）：只 import `state` / `coverage`（同层视图工具，§1.2 允许标题栏嵌覆盖度控件）
/ `contracts` `names` `terms` `theme` `widgets` + Qt-free 的 `inputs_table` `edits`。
"""

from PySide6 import QtCore, QtWidgets

from dreg_verify import edits as ED
from dreg_verify import inputs_table as IT
from dreg_verify.ui import names, terms, theme
from dreg_verify.ui.coverage import CoverageControl
from dreg_verify.ui.widgets import ProgressBadge, mono_font, ui_font

#: C-066 的直链目标（terms.REASON_TARGETS 的键）
DIAG_TARGET_CUVUNF = "diag_cuvunf"
#: 直链按钮文案：复用行内原因块的「去诊断 · {action}」句式（needs-prefix 那条的 action）
DIAG_BTN_TEXT = terms.REASON_BTN_FMT.format(action=terms.REASON_TEMPLATES["needs-prefix"][2])

#: 摘要里没有值时的占位（不写「未知」这种开发者腔）
DASH = "—"


#: 模型行 → `terms.STATUS` 的键。四档→八档的映射**只写在 `terms.py`**（主控裁决，C2-int）：
#: 清单 / 筛选行 / 本模块共用同一个函数，否则 `risky-generated` 这类行会三边说法不一。
status_key_of = terms.status_key_of


def _vec_keys(an):
    """`edits.cols_from_vectors` 要的 e_inputs —— 它只用其中的 `key`（且只有 mux 路用）。"""
    if (an or {}).get("editable") == "mux":
        return [{"key": k} for k in ((an.get("expansion") or {}).get("used_vars") or [])]
    return []


def progress_of(an, cols):
    """(已手填, 共几条正向, 其中与 auto 不一致几条) —— C-106 的三个数。"""
    cols = list(cols or [])
    n, m = ED.fill_progress(cols)
    diff = 0
    if an:
        for c in cols:
            if c.get("neg"):
                continue
            try:
                if ED.expected_cell_state(an, c) == ED.EXP_DIFF:
                    diff += 1
            except Exception:                                      # noqa: BLE001
                continue
    return n, m, diff


#: `scrub_detail` 里临时占位的哨兵（不可能出现在真文本里）
_CUVUNF_SENTINEL = "\x00cuvunf-first\x00"


def scrub_detail(text):
    """解析明细的 scrub（I-12）：整段过 `terms.scrub`，**但保住 `CUVUNF_FIRST` 那一句**。

    `inputs_table.resolve_detail` 本身已是术语干净的产出（note/issues 在它内部就 scrub 过），
    末尾那句「仿真器找不到这根网（elaboration 阶段报错，错误码 CUVUNF）」是术语表钦定的
    「首次出现的完整说法」—— 工程师要拿这个错误码去 grep 仿真 log。再 scrub 一遍会把错误码
    本身也替换掉，变成「错误码 找不到这根网」这种自指的废话。所以先摘出来、scrub 完再放回去。"""
    out = (text or "").replace(terms.CUVUNF_FIRST, _CUVUNF_SENTINEL)
    return terms.scrub(out).replace(_CUVUNF_SENTINEL, terms.CUVUNF_FIRST)


def failure_text(model, an=None):
    """C-043：分析失败那一行的全文（标题 + 原因，原因已 scrub）。没有原因就只给标题。

    原因在 an 和清单行里都可能有（单信号炸了只有清单行带 issues，整表炸了 an 根本没有），
    两边都收、按出现序去重 —— 少一句都是「只剩一句失败」，用户没法往下查。"""
    body, seen = [], set()
    for src in (an or {}, model or {}):
        for raw in [src.get("note")] + list(src.get("issues") or []):
            txt = terms.scrub(raw) if raw else ""
            if txt and txt not in seen:
                seen.add(txt)
                body.append(txt)
    return "%s\n%s" % (terms.HDR_ANALYSIS_FAILED, "\n".join(body)) if body \
        else terms.HDR_ANALYSIS_FAILED


class DetailHeader(QtWidgets.QWidget):
    """④ 详情标题栏。

    对外信号：
        diagRequested(str symptom)     —— 解析明细末尾的直链（`terms.REASON_TARGETS` 的键），
                                          组合根拿去 `route_reason_action(symptom)`；
        sideToggled(bool visible)      —— 右栏开合（True = 展开链 · 输入信号 可见）；
        resolveDetailToggled(bool open)—— 解析明细面板开合。

    公开 API：`set_state` / `show_signal(model, an)` / `clear()` / `refresh()` /
    `set_progress(n, m, n_diff)`（订阅 `TruthModel.progressChanged`，经 state 转发）/
    `set_error(text)`（worker failed → C-043）/ `set_side_visible` / `set_resolve_open`。
    """

    diagRequested = QtCore.Signal(str)
    sideToggled = QtCore.Signal(bool)
    resolveDetailToggled = QtCore.Signal(bool)

    def __init__(self, state=None, parent=None):
        super(DetailHeader, self).__init__(parent)
        self.setObjectName(names.HDR_BAR)
        self.setAttribute(QtCore.Qt.WA_StyledBackground, True)
        self.setStyleSheet("QWidget#%s{background:%s;}" % (names.HDR_BAR, theme.WHITE))
        self._state = None
        self._name = ""
        self._model = {}
        self._an = None
        self._side_visible = True
        self._conns = []
        self._build()
        self.clear()
        self.set_state(state)

    # ═══════════════ 构建 ═══════════════
    def _build(self):
        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(14, 8, 14, 8)
        outer.setSpacing(4)

        row = QtWidgets.QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(10)

        self.name_label = QtWidgets.QLabel(self)
        self.name_label.setObjectName(names.HDR_NAME)
        self.name_label.setFont(mono_font(theme.FS_MONO_NAME_BIG, bold=True))
        self.name_label.setStyleSheet("color:%s;" % theme.INK)
        self.name_label.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        # C-260：**最小**宽度不跟着信号名长短走。QLabel 默认拿「整行文字画得下」当最小宽度，
        # 于是一个长信号名就能把整条标题栏、进而把整个详情列的最小宽度顶到近千像素——
        # 1366×768 上清单再也收不到 420（Design 明写的窄屏版面）。正常宽度下显示不受影响。
        self.name_label.setSizePolicy(QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Preferred)
        row.addWidget(self.name_label)

        self.status_badge = QtWidgets.QLabel(self)
        self.status_badge.setObjectName(names.HDR_STATUS_BADGE)
        self.status_badge.setFont(ui_font(theme.FS_UI_SMALL, bold=True))
        row.addWidget(self.status_badge)

        self.meta_label = QtWidgets.QLabel(self)
        self.meta_label.setObjectName(names.HDR_META)
        self.meta_label.setFont(ui_font())
        self.meta_label.setStyleSheet("color:%s;" % theme.MUTE)
        self.meta_label.setSizePolicy(QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Preferred)
        row.addWidget(self.meta_label)

        self.cov = CoverageControl(None, self)
        row.addWidget(self.cov)
        row.addStretch(1)

        self.progress_label = QtWidgets.QLabel(self)
        self.progress_label.setObjectName(names.HDR_PROGRESS_LABEL)
        self.progress_label.setFont(ui_font())
        self.progress_label.setStyleSheet("color:%s;" % theme.TEXT)
        row.addWidget(self.progress_label)

        self.progress_bar = ProgressBadge(self)
        self.progress_bar.setObjectName(names.HDR_PROGRESS_BAR)
        row.addWidget(self.progress_bar)

        self.progress_diff = QtWidgets.QLabel(self)
        self.progress_diff.setObjectName(names.HDR_PROGRESS_DIFF)
        self.progress_diff.setFont(ui_font(theme.FS_UI_SMALL))
        self.progress_diff.setStyleSheet("color:%s;" % theme.BAD_FG)
        row.addWidget(self.progress_diff)

        self.resolve_btn = QtWidgets.QToolButton(self)
        self.resolve_btn.setObjectName(names.HDR_RESOLVE_BTN)
        self.resolve_btn.setText(terms.HDR_RESOLVE)
        self.resolve_btn.setFont(ui_font())
        self.resolve_btn.setCheckable(True)
        self.resolve_btn.setCursor(QtCore.Qt.PointingHandCursor)
        self.resolve_btn.setFixedHeight(theme.BTN_H)
        self.resolve_btn.setStyleSheet(
            "QToolButton{background:%s;color:%s;border:1px solid %s;border-radius:3px;padding:2px 10px;}"
            "QToolButton:checked{background:%s;color:%s;border-color:%s;}"
            % (theme.WHITE, theme.TEXT, theme.BORDER,
               theme.LIGHT_BLUE_BG, theme.LIGHT_BLUE_FG, theme.BLUE))
        self.resolve_btn.toggled.connect(self._on_resolve_toggled)
        row.addWidget(self.resolve_btn)

        self.side_toggle = QtWidgets.QToolButton(self)
        self.side_toggle.setObjectName(names.HDR_SIDE_TOGGLE)
        self.side_toggle.setFont(ui_font())
        self.side_toggle.setCursor(QtCore.Qt.PointingHandCursor)
        self.side_toggle.setFixedHeight(theme.BTN_H)
        self.side_toggle.setStyleSheet(
            "QToolButton{background:%s;color:%s;border:1px solid %s;border-radius:3px;padding:2px 10px;}"
            % (theme.WHITE, theme.TEXT, theme.BORDER))
        self.side_toggle.clicked.connect(self._on_side_clicked)
        row.addWidget(self.side_toggle)
        outer.addLayout(row)

        # ── 状态行（三者互斥、平时都藏着）──
        self.pending_label = QtWidgets.QLabel(self)
        self.pending_label.setObjectName(names.HDR_PENDING)
        self.pending_label.setFont(ui_font(theme.FS_UI_SMALL))
        self.pending_label.setText(terms.HDR_PENDING)
        self.pending_label.setStyleSheet("color:%s;" % theme.MUTE)
        outer.addWidget(self.pending_label)

        self.not_editable_label = QtWidgets.QLabel(self)
        self.not_editable_label.setObjectName(names.HDR_NOT_EDITABLE)
        self.not_editable_label.setFont(ui_font(theme.FS_UI_SMALL))
        self.not_editable_label.setText(terms.HDR_NOT_EDITABLE)
        self.not_editable_label.setStyleSheet("color:%s;" % theme.WARN_FG)
        outer.addWidget(self.not_editable_label)

        self.error_label = QtWidgets.QLabel(self)                  # C-043
        self.error_label.setObjectName(names.HDR_ERROR_LABEL)
        self.error_label.setFont(ui_font(theme.FS_UI_SMALL))
        self.error_label.setWordWrap(True)
        self.error_label.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        self.error_label.setStyleSheet(
            "color:%s;background:%s;border:1px solid %s;border-radius:3px;padding:3px 8px;"
            % (theme.BAD_FG, theme.BAD_BG, theme.AMBER_BORDER))
        outer.addWidget(self.error_label)

        # ── 解析明细面板（C-064/065/066）──
        self.resolve_box = QtWidgets.QFrame(self)
        self.resolve_box.setObjectName(names.HDR_RESOLVE_BOX)
        self.resolve_box.setFrameShape(QtWidgets.QFrame.NoFrame)
        box = QtWidgets.QVBoxLayout(self.resolve_box)
        box.setContentsMargins(0, 4, 0, 0)
        box.setSpacing(4)
        self.resolve_panel = QtWidgets.QPlainTextEdit(self.resolve_box)
        self.resolve_panel.setObjectName(names.HDR_RESOLVE_PANEL)
        self.resolve_panel.setReadOnly(True)
        self.resolve_panel.setFont(mono_font())
        self.resolve_panel.setLineWrapMode(QtWidgets.QPlainTextEdit.NoWrap)
        self.resolve_panel.setMinimumHeight(7 * theme.ROW_H)
        self.resolve_panel.setStyleSheet(
            "QPlainTextEdit{background:%s;border:1px solid %s;color:%s;}"
            % (theme.HINT_BG, theme.BORDER_LIGHT, theme.INK))
        box.addWidget(self.resolve_panel)
        self.resolve_diag_btn = QtWidgets.QToolButton(self.resolve_box)
        self.resolve_diag_btn.setObjectName(names.HDR_RESOLVE_DIAG_BTN)
        self.resolve_diag_btn.setText(DIAG_BTN_TEXT)
        self.resolve_diag_btn.setFont(ui_font(theme.FS_UI_SMALL))
        self.resolve_diag_btn.setCursor(QtCore.Qt.PointingHandCursor)
        self.resolve_diag_btn.setStyleSheet(
            "QToolButton{background:transparent;border:0;color:%s;text-decoration:underline;}"
            % theme.BLUE_DARK)
        self.resolve_diag_btn.clicked.connect(
            lambda _c=False: self.diagRequested.emit(DIAG_TARGET_CUVUNF))
        box.addWidget(self.resolve_diag_btn, 0, QtCore.Qt.AlignLeft)
        outer.addWidget(self.resolve_box)

        self.resolve_box.hide()
        self._set_side_button_text()

    # ═══════════════ state ═══════════════
    def set_state(self, state):
        """换 state（组合根载表后调一次）。自动跟住 state 的刷新信号。"""
        for sig, slot in self._conns:
            try:
                sig.disconnect(slot)
            except (RuntimeError, TypeError):
                pass
        self._conns = []
        self._state = state
        self.cov.set_state(state)
        if state is not None:
            for sig_name, slot in (("currentChanged", self._on_current_changed),
                                   ("modelsChanged", self._on_view_changed),
                                   ("modelUpdated", self._on_model_updated),
                                   ("editsChanged", self._on_model_updated),
                                   ("coverageChanged", self._on_view_changed),
                                   ("configChanged", self._on_view_changed),
                                   ("scopeChanged", self._on_view_changed)):
                sig = getattr(state, sig_name, None)
                if sig is not None:
                    sig.connect(slot)
                    self._conns.append((sig, slot))
        self.refresh()

    def state(self):
        return self._state

    def current(self):
        return self._name

    # ═══════════════ 显示一个信号 ═══════════════
    def show_signal(self, model=None, an=None):
        """显示一个信号。`model` 可以是清单行 dict，也可以直接给名字；`an` 不给就现查。"""
        if isinstance(model, str):
            name = model
            model = self._model_of(name)
        else:
            model = dict(model or {})
            name = str(model.get("name") or "")
        if not name:
            self.clear()
            return
        self._name = name
        self._model = model or {}
        key = status_key_of(self._model) if self._model else "pending"
        if an is None and key != "pending":
            an = self._analyze(name)
        self._an = an
        self._render()

    def clear(self):
        """没有当前信号：整条收成空壳（组合根的载入态卡片会占住这块）。"""
        self._name = ""
        self._model = {}
        self._an = None
        self.cov.set_current("")
        self.name_label.setText("")
        self.status_badge.setText("")
        self.status_badge.setStyleSheet("")
        self.meta_label.setText("")
        self.set_progress(0, 0, 0)
        self.resolve_panel.setPlainText("")
        self.error_label.setText("")
        self.error_label.hide()
        self.pending_label.hide()
        self.not_editable_label.hide()

    def refresh(self):
        """按当前 state 重取 model / an 再画一遍（覆盖度、编辑、配置变了都走它）。"""
        if not self._name:
            if self._state is not None and getattr(self._state, "current_name", ""):
                self.show_signal(self._state.current_name)
            return
        self.show_signal(self._name)

    # ═══════════════ 外部驱动的三件事 ═══════════════
    def set_progress(self, n, m, n_diff=0):
        """C-106：手填期望 N/M + 进度条 + 「其中 K 条与程序算的不一致」。

        真值表在编辑时经 `TruthModel.progressChanged` → state → 这里，不必重算一遍列模型。"""
        n, m, n_diff = int(n or 0), int(m or 0), int(n_diff or 0)
        self.progress_bar.set_value(n, m)
        if m <= 0:
            self.progress_label.setText("")
            self.progress_bar.hide()
        else:
            self.progress_label.setText(terms.HDR_PROGRESS_FMT.format(n=n, m=m))
            self.progress_bar.show()
        self.progress_diff.setText(
            terms.HDR_PROGRESS_DIFF_FMT.format(k=n_diff) if n_diff > 0 else "")

    def progress(self):
        n, m = self.progress_bar.value()
        return (n, m, self.progress_diff.text())

    def set_error(self, text=""):
        """C-043：分析失败（已捕获，未崩）+ 原因全文。空串 = 收起这一行。"""
        msg = terms.scrub(str(text or ""))
        self.error_label.setText(msg)
        self.error_label.setVisible(bool(msg))

    def error_text(self):
        return self.error_label.text()

    # ═══════════════ 右栏开合 / 解析明细 ═══════════════
    def set_side_visible(self, on, notify=False):
        """记住右栏当前是展开还是收起（按钮文案随之切）。notify=True 才回发信号。"""
        self._side_visible = bool(on)
        self._set_side_button_text()
        if notify:
            self.sideToggled.emit(self._side_visible)

    def side_visible(self):
        return self._side_visible

    def set_resolve_open(self, on):
        self.resolve_btn.setChecked(bool(on))

    def resolve_open(self):
        return self.resolve_btn.isChecked()

    def resolve_text(self):
        return self.resolve_panel.toPlainText()

    # ═══════════════ 内部 ═══════════════
    def _set_side_button_text(self):
        self.side_toggle.setText(terms.HDR_SIDE_HIDE if self._side_visible else terms.HDR_SIDE_SHOW)

    def _on_side_clicked(self, *_a):
        self.set_side_visible(not self._side_visible, notify=True)

    def _on_resolve_toggled(self, on):
        on = bool(on)
        if on:
            self._fill_resolve()
        self.resolve_box.setVisible(on)
        self.resolveDetailToggled.emit(on)

    def _fill_resolve(self):
        """C-064/C-065/C-066：`inputs_table.resolve_detail(an)` 的全文（再过一道 scrub）。"""
        an = self._an
        if an is None and self._name:
            an = self._an = self._analyze(self._name)
        text = scrub_detail(IT.resolve_detail(an)) if an else ""
        if not text:
            text = failure_text(self._model, an) if self._name else ""
        self.resolve_panel.setPlainText(text)

    def _model_of(self, name):
        st = self._state
        if st is None or not hasattr(st, "model_of"):
            return {}
        try:
            return dict(st.model_of(name) or {})
        except Exception:                                          # noqa: BLE001
            return {}

    def _analyze(self, name):
        st = self._state
        if st is None or not hasattr(st, "analyze"):
            return None
        try:
            return st.analyze(name)
        except Exception as ex:                                    # noqa: BLE001  C-043
            self.set_error("%s\n%s" % (terms.HDR_ANALYSIS_FAILED, ex))
            return None

    def _cols(self):
        """当前信号的列模型：有手填编辑用编辑后的，没有就按 an 现出一份默认真值表。"""
        an, st = self._an, self._state
        if an is None:
            return []
        ed = None
        if st is not None and hasattr(st, "edit_of"):
            try:
                ed = st.edit_of(self._name)
            except Exception:                                      # noqa: BLE001
                ed = None
        if ed and ed.get("cols") is not None:
            return list(ed["cols"])
        if not an.get("editable"):
            return []
        try:
            return ED.cols_from_vectors(an, _vec_keys(an))
        except Exception:                                          # noqa: BLE001
            return []

    def _render(self):
        m, an = self._model, self._an
        key = status_key_of(m) if m else "pending"
        self.name_label.setText(str(m.get("disp") or m.get("name") or self._name))
        label, tone, help_text = terms.STATUS.get(key, terms.STATUS["error"])
        fg, bg = theme.TONE_COLORS.get(tone, theme.TONE_COLORS["note"])
        self.status_badge.setText(label)
        self.status_badge.setToolTip(terms.scrub(help_text))
        self.status_badge.setStyleSheet(
            "color:%s;background:%s;border-radius:3px;padding:1px 7px;" % (fg, bg))

        n_vec = m.get("n_vectors")
        self.meta_label.setText(terms.HDR_META_FMT.format(
            owner=(m.get("owner") or terms.OWNER_NONE),
            kind=terms.KIND_LABELS.get(m.get("kind") or "", m.get("kind") or DASH),
            n=(DASH if n_vec is None else n_vec)))
        self.meta_label.setToolTip(terms.scrub(m.get("note") or ""))

        self.cov.set_current(self._name)

        cols = self._cols()
        self.set_progress(*progress_of(an, cols))

        pending = (key == "pending")
        self.pending_label.setVisible(pending)
        self.not_editable_label.setVisible(bool(an is not None and not an.get("editable")))
        failed = (not pending) and (an is None or key == "error")
        self.set_error(failure_text(m, an) if failed else "")
        self.resolve_btn.setEnabled(bool(self._name) and not pending)
        if self.resolve_btn.isChecked():
            self._fill_resolve()

    # ── state 信号 ──
    def _on_current_changed(self, name):
        if name:
            self.show_signal(str(name))
        else:
            self.clear()

    def _on_view_changed(self, *_a):
        if self._name:
            self.show_signal(self._name)

    def _on_model_updated(self, _vid, name=""):
        if not self._name:
            return
        if not name or str(name).lower() == self._name.lower():
            self.show_signal(self._name)
