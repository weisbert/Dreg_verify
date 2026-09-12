# -*- coding: utf-8 -*-
"""sv_preview.py —— ⑧ .sv 预览（GUI v2 Phase C2-c）。

Design 对照：docs/GUI_v2_Design对齐_20260912.md §1.2 ⑧
    工具条「本信号 .sv 片段（不落盘）」│ →右「改看勾选集」「复制」；正文 `white-space:pre`，Consolas 12。
架构简报：docs/GUI_v2_架构_20260912.md §6.8；契约 ID = 附录 A「SV」7 条
    C-067 预览勾选集的 .sv（含真值表编辑，所见即所得）
    C-068 预览本信号的 .sv 片段（不必取消其余勾选）
    C-069 超 600 行截断并注明总行数
    C-070 末尾追加本次跳过哪些信号、各缺哪根输入
    C-072 配置变更后自动按新配置重算，不留旧内容误导
    C-132 编辑（含手填期望）立刻在预览与导出里生效
    C-268 两处 .sv 预览合成一处（本信号 ↔ 勾选集 一键切）

一条路径原则：预览与导出**同源**——都走 `exports.render_sv(provider, …, options=None)`，
所以「所见即所得」不是靠对齐两段代码，而是根本就只有一段（§6.8）。

分层（I-19）：本模块不 import `topout` / `generator`；只经 `state.provider()` + `exports`。
I-20 / C-270：跳过点名在前、计数在后。
"""

from PySide6 import QtCore, QtWidgets

from dreg_verify import exports as X
from dreg_verify.ui import names, terms, theme
from dreg_verify.ui.widgets import mono_font, ui_font

#: 两种范围
MODE_SIGNAL = "single"
MODE_CHECKED = "checked"


def truncate(text, max_lines=theme.SV_PREVIEW_MAX_LINES):
    """C-069：超 `max_lines` 行就截断并注明总行数（导出的 .sv 仍是完整的）。"""
    lines = (text or "").split("\n")
    if len(lines) <= int(max_lines):
        return text or ""
    return "\n".join(lines[:int(max_lines)]) + terms.SV_TRUNCATED_FMT.format(
        total=len(lines), shown=int(max_lines))


def skipped_tail(build):
    """C-070 / C-270：本次会跳过哪些信号、各缺哪根输入 —— **名字在前**，计数在后。"""
    rows = X.build_skipped(build or {})
    if not rows:
        return ""
    out = [terms.SV_SKIPPED_TAIL_HEAD]
    for name, reason in rows:
        body = terms.scrub(str(reason or "")).replace("\n", "\n      ")
        out.append("  %s：%s" % (name, body))
    return "\n".join(out)


class SvPreview(QtWidgets.QWidget):
    """⑧ .sv 预览面板。

    对外信号：
        statusMessage(str)  —— C-172：渲染完报一次计数（组合根接到状态栏左段）。

    公开 API：`set_state` / `show_signal(name)` / `mode()` / `set_mode(m)` / `toggle_mode()` /
    `refresh(force=False)` / `text()` / `is_dirty()`。
    """

    statusMessage = QtCore.Signal(str)

    def __init__(self, state=None, parent=None):
        super(SvPreview, self).__init__(parent)
        self.setObjectName(names.SV_PANEL)
        self.setAttribute(QtCore.Qt.WA_StyledBackground, True)
        self.setStyleSheet("QWidget#%s{background:%s;}" % (names.SV_PANEL, theme.WHITE))
        self._state = None
        self._name = ""
        self._mode = MODE_SIGNAL
        self._dirty = True
        self._conns = []
        self._last_counts = ""
        self._build()
        self.set_state(state)

    # ═══════════════ 构建 ═══════════════
    def _build(self):
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        bar = QtWidgets.QWidget(self)
        bar.setObjectName(names.SV_TOOLBAR)
        bar.setAttribute(QtCore.Qt.WA_StyledBackground, True)
        bar.setStyleSheet("QWidget#%s{background:%s;border-bottom:1px solid %s;}"
                          % (names.SV_TOOLBAR, theme.PANEL_BG, theme.BORDER_LIGHT))
        h = QtWidgets.QHBoxLayout(bar)
        h.setContentsMargins(10, 5, 10, 5)
        h.setSpacing(8)

        self.title = QtWidgets.QLabel(bar)
        self.title.setObjectName(names.SV_TITLE)
        self.title.setFont(ui_font(theme.FS_UI_TITLE, bold=True))
        self.title.setStyleSheet("color:%s;" % theme.INK)
        h.addWidget(self.title)
        h.addStretch(1)

        self.scope_btn = QtWidgets.QToolButton(bar)
        self.scope_btn.setObjectName(names.SV_BTN_TOGGLE_SCOPE)
        self.scope_btn.setFont(ui_font())
        self.scope_btn.setCursor(QtCore.Qt.PointingHandCursor)
        self.scope_btn.setFixedHeight(theme.BTN_H)
        self.scope_btn.setStyleSheet(
            "QToolButton{background:%s;color:%s;border:1px solid %s;border-radius:3px;padding:2px 10px;}"
            % (theme.WHITE, theme.TEXT, theme.BORDER))
        self.scope_btn.clicked.connect(lambda _c=False: self.toggle_mode())
        h.addWidget(self.scope_btn)

        self.copy_btn = QtWidgets.QToolButton(bar)
        self.copy_btn.setObjectName(names.SV_BTN_COPY)
        self.copy_btn.setText(terms.SV_BTN_COPY)
        self.copy_btn.setFont(ui_font())
        self.copy_btn.setCursor(QtCore.Qt.PointingHandCursor)
        self.copy_btn.setFixedHeight(theme.BTN_H)
        self.copy_btn.setStyleSheet(
            "QToolButton{background:%s;color:%s;border:1px solid %s;border-radius:3px;padding:2px 10px;}"
            % (theme.WHITE, theme.TEXT, theme.BORDER))
        self.copy_btn.clicked.connect(lambda _c=False: self.copy_to_clipboard())
        h.addWidget(self.copy_btn)
        lay.addWidget(bar)

        self.view = QtWidgets.QPlainTextEdit(self)
        self.view.setObjectName(names.SV_TEXT)
        self.view.setReadOnly(True)
        self.view.setFont(mono_font(theme.FS_MONO_SV))
        self.view.setLineWrapMode(QtWidgets.QPlainTextEdit.NoWrap)   # = white-space:pre
        self.view.setStyleSheet("QPlainTextEdit{background:%s;border:0;color:%s;}"
                                % (theme.WHITE, theme.INK))
        lay.addWidget(self.view, 1)
        self._sync_toolbar()

    # ═══════════════ state ═══════════════
    def set_state(self, state):
        """换 state。订阅四条「内容会变」的信号 → 标脏（C-072/C-132），可见时才重算。"""
        for sig, slot in self._conns:
            try:
                sig.disconnect(slot)
            except (RuntimeError, TypeError):
                pass
        self._conns = []
        self._state = state
        if state is not None:
            for sig_name in ("editsChanged", "coverageChanged", "configChanged",
                             "checksChanged", "modelsChanged", "workbookChanged"):
                sig = getattr(state, sig_name, None)
                if sig is not None:
                    sig.connect(self._mark_dirty)
                    self._conns.append((sig, self._mark_dirty))
            cur = getattr(state, "currentChanged", None)
            if cur is not None:
                cur.connect(self._on_current_changed)
                self._conns.append((cur, self._on_current_changed))
            self._name = str(getattr(state, "current_name", "") or "")
        self._mark_dirty()
        if self.isVisible():
            self.refresh()

    def state(self):
        return self._state

    # ═══════════════ 范围与当前信号 ═══════════════
    def show_signal(self, name):
        """换当前信号（本信号模式下内容跟着换；勾选集模式下只记住名字）。"""
        name = str(name or "")
        if name == self._name:
            return
        self._name = name
        self._mark_dirty()
        if self.isVisible():
            self.refresh()

    def current(self):
        return self._name

    def mode(self):
        return self._mode

    def set_mode(self, mode):
        mode = MODE_CHECKED if mode == MODE_CHECKED else MODE_SIGNAL
        if mode == self._mode:
            return
        self._mode = mode
        self._sync_toolbar()
        self._mark_dirty()
        self.refresh()

    def toggle_mode(self):
        """C-067 / C-068 / C-268：本信号 ↔ 勾选集 一键切。"""
        self.set_mode(MODE_SIGNAL if self._mode == MODE_CHECKED else MODE_CHECKED)

    # ═══════════════ 内容 ═══════════════
    def text(self):
        return self.view.toPlainText()

    def is_dirty(self):
        return self._dirty

    def counts_text(self):
        """上一次渲染的计数串（C-172 状态栏那行的数据部分）。"""
        return self._last_counts

    def copy_to_clipboard(self):
        cb = QtWidgets.QApplication.clipboard()
        if cb is not None:
            cb.setText(self.text())
        self.statusMessage.emit(terms.STATUS_COPIED)

    def refresh(self, force=False):
        """重算正文。`force=False` 且不脏 → 原样返回（切标签不必每次重渲）。"""
        if not (force or self._dirty):
            return self.text()
        self._dirty = False
        text, build = self._render()
        body = truncate(text)
        tail = skipped_tail(build)
        if tail:
            body = (body.rstrip("\n") + "\n\n" + tail) if body.strip() else tail
        self.view.setPlainText(body)
        self._report_counts(build)
        return body

    def showEvent(self, ev):                                       # noqa: N802  Qt 命名
        super(SvPreview, self).showEvent(ev)
        if self._dirty:
            self.refresh()

    # ═══════════════ 内部 ═══════════════
    def _sync_toolbar(self):
        checked = (self._mode == MODE_CHECKED)
        self.title.setText(terms.SV_TITLE_CHECKED if checked else terms.SV_TITLE_SIGNAL)
        self.scope_btn.setText(terms.SV_BTN_TO_SIGNAL if checked else terms.SV_BTN_TO_CHECKED)

    def _mark_dirty(self, *_a):
        self._dirty = True

    def _on_current_changed(self, name):
        self.show_signal(name)

    def _cov_args(self):
        """(mode, max_tests, exhaustive, sig_cov, form_cov) —— 与清单/导出同一套三层覆盖度。

        本信号：单点 > 逻辑类型 > 全局已由 `mode_for` 折平，不再把两本字典喂给引擎；
        勾选集：全局档 + 两本字典（provider 支持单点档时才传，与 `state.analysis_request` 同口径）。"""
        st = self._state
        cov = st.coverage() if (st is not None and hasattr(st, "coverage")) else None
        if cov is None:
            return ("min", 256, False, None, None)
        if self._mode == MODE_SIGNAL:
            models = list(st.models() or []) if hasattr(st, "models") else []
            mode, exh = cov.mode_for(self._name, models=models)
            return (mode, int(cov.max_tests), bool(exh), None, None)
        mode, exh = cov.mode()
        prov = st.provider() if hasattr(st, "provider") else None
        sig_ok = bool(prov is not None and getattr(prov, "supports_sig_cov", False))
        return (mode, int(cov.max_tests), bool(exh),
                (dict(cov.sig_cov) or None) if sig_ok else None,
                (dict(cov.form_cov) or None) if sig_ok else None)

    def _only(self):
        """本信号 = [当前名]；勾选集 = 勾着的名字（C-067）。"""
        st = self._state
        if self._mode == MODE_SIGNAL:
            return [self._name] if self._name else []
        if st is None or not hasattr(st, "checked_names"):
            return None
        return list(st.checked_names() or [])

    def _render(self):
        """→ (text, build)。渲染不了（没载表 / 没选信号）→ ("", {})。"""
        st = self._state
        if st is None:
            return ("", {})
        prov = st.provider() if hasattr(st, "provider") else None
        if prov is None:
            return ("", {})
        only = self._only()
        if only is not None and not only:
            return ("", {})
        mode, max_tests, exh, sig_cov, form_cov = self._cov_args()
        edited = st.compute_edited() if hasattr(st, "compute_edited") else None
        try:
            text, build = X.render_sv(prov, only=only, mode=mode, max_tests=max_tests,
                                      exhaustive=exh, edited=edited, options=None,
                                      sig_cov=sig_cov, form_cov=form_cov)
        except Exception as ex:                                    # noqa: BLE001
            return (terms.exc_text(ex), {})
        return (text or "", build or {})

    def _report_counts(self, build):
        """C-172：状态栏报计数（走 `exports.sv_outcome` 的同一套口径，不另拼一份）。"""
        if not build:
            self._last_counts = ""
            return
        try:
            counts = X.sv_outcome("", build).counts_text()
        except Exception:                                          # noqa: BLE001
            counts = ""
        self._last_counts = counts
        if counts:
            self.statusMessage.emit(terms.SV_PREVIEW_STATUS_FMT.format(counts=counts))


#: 架构文档 §6.8 里的名字（组合根按哪个名字 import 都行）
SvPreviewPanel = SvPreview
