# -*- coding: utf-8 -*-
"""filter_bar.py —— ② 筛选行（GUI v2 Phase C1-c）。

Design 对照：docs/GUI_v2_Design对齐_20260912.md §1.2 ②
    「范围」5 段联排（Topout 全 / 只看 logic / 只看 mux / dft / iddq，首段选中蓝底）
    + scopeHint 小字 + 「全部 owner ▾」「全部分类 ▾」「全部状态 ▾」三个下拉
    + 正则搜索框 + 右端「预设 ▾」。
架构简报：docs/GUI_v2_架构_20260912.md §6.4；契约 ID = 附录 A「FILTER」12 条
    C-024 C-025 C-026 C-027 C-028 C-029 C-030 C-031 C-038 C-042 C-047 C-291。

本模块**不改 state**（§1.2 依赖方向：视图只读 state、只发信号）：
    · 范围段点击 → `scopeChanged(view_id)`，由组合根调 `state.set_scope`；
    · 任一筛选项变化 → `filterChanged(dict)`，dict 可直接 `proxy.set_filters(**d)`（§2.3 签名）；
    · 预设存/取 → `presetSaveRequested()` / `presetLoadRequested(name)`（对话框在 C2-d 的 dialogs.py）。

临时自建控件（C1-int 集成时替换）：
    · `_Segmented` → `ui/widgets.py` 的 `SegmentedControl`（C1-d 交付）；
    · `_CheckableMenu` → `ui/widgets.py` 的 `CheckableMenu`（语义抄自 gui.py L275–283）。
"""

import re

from PySide6 import QtCore, QtGui, QtWidgets

from dreg_verify.ui import contracts, names, terms, theme

#: 搜索框去抖（ms）——键入不逐字符重筛（C-030）。
SEARCH_DEBOUNCE_MS = 150

#: 状态下拉三项 → 过滤键（terms.STATUS_FILTER_ITEMS 的同序值）。
STATUS_VALUES = ("", "ok", "issues")

#: ⚠ names.py 里还没有「筛选行的粘贴名单勾选入口」这个名字（`LIST_BTN_PASTE_NAMES` 属清单区，
#: 同一个 objectName 不得跨模块复用）。已回报主控补 `FILTER_PASTE_NAMES_BTN`；补进去之后
#: 删掉本常量、改从 names 取。
PASTE_NAMES_OBJECT_NAME = "filter_paste_names_btn"


# ═════════════════════ 纯函数：筛选判定（proxy 与状态栏同一口径）═════════════════════

def compile_regex(pattern):
    """搜索框文本 → 编译好的正则（大小写无关）。空串或写坏的正则 → None（调用方退化为子串匹配）。"""
    pat = (pattern or "").strip()
    if not pat:
        return None
    try:
        return re.compile(pat, re.I)
    except re.error:
        return None


def _hit(rx, raw, fields):
    """rx 有就用正则，没有（正则写坏了）就用大小写无关子串。

    **逐字段**匹配而不是拼成一条长串 —— 否则 `^sig_gamma$` 这种锚定正则永远匹配不上。"""
    if not raw:
        return True
    for f in fields:
        s = str(f or "")
        if not s:
            continue
        if rx.search(s) if rx is not None else (raw.lower() in s.lower()):
            return True
    return False


def match_row(model, owners=(), kind="", status="", rx=None, raw=""):
    """一行清单模型是否通过筛选 → `(可见, 是否只因输入信号名命中)`。

    C-024 owner 多选 = 任一命中（不勾 = 全部）；C-028 分类；C-029 状态；
    C-030/C-047 正则同时匹配 信号名 / 断言号 / 表达式 / 输入信号名（`_to_dft` 这类后缀也在里面）。
    第二个返回值给 C-031 的「其中 K 个按输入信号名命中」计数用。
    """
    owner = model.get("owner") or terms.OWNER_NONE
    if owners and owner not in owners:
        return False, False
    if kind and model.get("kind") != kind:
        return False, False
    st = model.get("status")
    if status == "ok" and st != "ok":
        return False, False
    if status == "issues" and st == "ok" and not model.get("issues"):
        return False, False
    if not raw:
        return True, False
    own_fields = [model.get(k) for k in ("name", "disp", "assert_id", "expr")]
    in_fields = list(model.get("input_names") or [])
    own_ok = _hit(rx, raw, own_fields)
    in_ok = _hit(rx, raw, in_fields) if in_fields else False
    if own_ok:
        return True, False
    if in_ok:
        return True, True
    return False, False


def count_visible(models, filters):
    """整张清单按 filters 过一遍 → `(可见数, 总数, 其中只因输入信号名命中的数)`（C-031）。"""
    rx = compile_regex(filters.get("regex", ""))
    raw = (filters.get("regex") or "").strip()
    owners = set(filters.get("owners") or ())
    kind = filters.get("kind") or ""
    status = filters.get("status") or ""
    vis = by_in = 0
    for m in models or []:
        ok, only_in = match_row(m, owners, kind, status, rx, raw)
        if ok:
            vis += 1
            by_in += 1 if only_in else 0
    return vis, len(models or []), by_in


def visible_status_text(visible, total, by_input):
    """C-031 的状态栏文案（terms.STATUS_VISIBLE_FMT）。"""
    return terms.STATUS_VISIBLE_FMT.format(v=visible, m=total, k=by_input)


def _ui_font(size=theme.FS_UI, bold=False):
    f = QtGui.QFont(theme.FONT_UI)
    f.setPixelSize(int(round(size)))
    f.setBold(bool(bold))
    return f


# ═════════════════════ 临时控件（C1-int 换 widgets.py 版本）═════════════════════

class _CheckableMenu(QtWidgets.QMenu):
    """可勾选下拉菜单：点 checkable 项只切勾选、菜单不关（owner 多选，C-024）。

    ⚠ 临时件：C1-int 换成 `ui/widgets.py` 的 `CheckableMenu`（语义与 gui.py `_CheckableMenu` 相同）。
    """

    def mouseReleaseEvent(self, e):
        act = self.activeAction()
        if act is not None and act.isEnabled() and act.isCheckable():
            act.trigger()
            return
        super(_CheckableMenu, self).mouseReleaseEvent(e)


class _Segmented(QtWidgets.QWidget):
    """5 段联排（范围切换）：首段选中蓝底、其余白底、段间竖分隔（Design ②）。

    ⚠ 临时件：C1-int 换成 `ui/widgets.py` 的 `SegmentedControl`（C1-d 交付），
    换的时候只需保证 `changed(str)` / `set_current` / `current` / `set_available` 四个口不变。
    """

    changed = QtCore.Signal(str)

    def __init__(self, items, parent=None):
        super(_Segmented, self).__init__(parent)
        self._btns = {}
        self._keys = [k for k, _ in items]
        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self._group = QtWidgets.QButtonGroup(self)
        self._group.setExclusive(True)
        for i, (key, label) in enumerate(items):
            b = QtWidgets.QToolButton(self)
            b.setObjectName(names.fmt_scope_btn(key))
            b.setText(label)
            b.setCheckable(True)
            b.setFont(_ui_font())
            b.setFixedHeight(theme.BTN_H)
            b.setCursor(QtCore.Qt.PointingHandCursor)
            b.setStyleSheet(self._btn_qss(first=(i == 0), last=(i == len(items) - 1)))
            b.clicked.connect(lambda _c=False, k=key: self._on_click(k))
            self._group.addButton(b)
            lay.addWidget(b)
            self._btns[key] = b
        if self._keys:
            self._btns[self._keys[0]].setChecked(True)

    @staticmethod
    def _btn_qss(first, last):
        radius = "border-top-left-radius:3px;border-bottom-left-radius:3px;" if first else ""
        radius += "border-top-right-radius:3px;border-bottom-right-radius:3px;" if last else ""
        return ("QToolButton{background:%s;color:%s;border:1px solid %s;%s"
                "border-left-width:%dpx;padding:2px 10px;}"
                "QToolButton:checked{background:%s;color:%s;border-color:%s;}"
                "QToolButton:disabled{background:%s;color:%s;}"
                % (theme.WHITE, theme.TEXT, theme.BORDER_SEG, radius, 1 if first else 0,
                   theme.BLUE, theme.WHITE, theme.BLUE, theme.DISABLED_BG, theme.DISABLED_TEXT))

    def _on_click(self, key):
        b = self._btns.get(key)
        if b is None or not b.isEnabled():
            return
        b.setChecked(True)
        self.changed.emit(key)

    def button(self, key):
        return self._btns.get(key)

    def current(self):
        for k, b in self._btns.items():
            if b.isChecked():
                return k
        return self._keys[0] if self._keys else ""

    def set_current(self, key):
        b = self._btns.get(key)
        if b is not None and b.isEnabled():
            b.setChecked(True)

    def set_available(self, key, on, tip=""):
        """C-042：本表没这一页 → 置灰 + tooltip 标「本表无 dft 页」。"""
        b = self._btns.get(key)
        if b is None:
            return
        b.setEnabled(bool(on))
        b.setToolTip("" if on else tip)


# ═════════════════════ 筛选行 ═════════════════════

class FilterBar(QtWidgets.QWidget):
    """② 筛选行：范围 5 段 + owner 多选 + 分类 + 状态 + 正则搜索 + 预设。

    对外只发信号，不动 state：
        scopeChanged(str view_id)          范围段点击（C-038）
        filterChanged(dict)                {"owners": set, "kind": str, "status": str, "regex": str}
                                           —— 直接 `proxy.set_filters(**d)`（§2.3）
        statusMessage(str)                 C-031 的「可见 N / 共 M（其中 K 个按输入信号名命中）」
        presetSaveRequested()              「存为预设…」（对话框 C2-d）
        presetLoadRequested(str name)      点某条已存预设（C-291）
        pasteNamesRequested()              「粘贴名单勾选…」（对话框 C2-d，C-290）
    """

    scopeChanged = QtCore.Signal(str)
    filterChanged = QtCore.Signal(dict)
    statusMessage = QtCore.Signal(str)
    presetSaveRequested = QtCore.Signal()
    presetLoadRequested = QtCore.Signal(str)
    pasteNamesRequested = QtCore.Signal()

    def __init__(self, state=None, parent=None):
        super(FilterBar, self).__init__(parent)
        self.setObjectName(names.FILTER_BAR)
        self._state = state
        self._models = []
        self._owner_acts = {}
        self._owner_sel = set()
        self._missing = []
        self._mute = 0                      # >0 时不发 filterChanged（批量 set_filters / 重建）
        self._build()

    # ───────── 构建 ─────────
    def _build(self):
        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(12, 5, 12, 5)
        lay.setSpacing(8)
        self.setStyleSheet("QWidget#%s{background:%s;border-bottom:1px solid %s;}"
                           % (names.FILTER_BAR, theme.WHITE, theme.BORDER_LIGHT))

        self.scope_seg = _Segmented([(v, terms.SCOPE_LABELS[v]) for v in contracts.VIEW_IDS], self)
        self.scope_seg.setObjectName(names.FILTER_SCOPE_SEG)
        self.scope_seg.changed.connect(self._on_scope)
        lay.addWidget(self.scope_seg)

        # scopeHint：Design 的模板没渲染它（只在脚本里），这里按同样口径 —— 全文进范围段的 tooltip，
        # 行上只在「本表少页」时才占位（C-042 的可见标注），免得把搜索框挤没。
        self.scope_seg.setToolTip(terms.SCOPE_HINT)
        self.scope_hint = QtWidgets.QLabel("", self)
        self.scope_hint.setObjectName(names.FILTER_SCOPE_HINT)
        self.scope_hint.setFont(_ui_font(theme.FS_UI_SMALL))
        self.scope_hint.setToolTip(terms.SCOPE_HINT)
        self.scope_hint.setStyleSheet("color:%s;" % theme.AMBER_FG)
        lay.addWidget(self.scope_hint)
        lay.addSpacing(6)

        # owner 多选（C-024..C-027）
        self.owner_btn = QtWidgets.QToolButton(self)
        self.owner_btn.setObjectName(names.FILTER_OWNER_BTN)
        self.owner_btn.setText(terms.OWNER_ALL)
        self.owner_btn.setFont(_ui_font())
        self.owner_btn.setFixedHeight(theme.BTN_H)
        self.owner_btn.setPopupMode(QtWidgets.QToolButton.InstantPopup)
        self.owner_btn.setToolButtonStyle(QtCore.Qt.ToolButtonTextOnly)
        self.owner_menu = _CheckableMenu(self.owner_btn)
        self.owner_menu.setObjectName(names.FILTER_OWNER_MENU)
        self.owner_btn.setMenu(self.owner_menu)
        lay.addWidget(self.owner_btn)

        self.kind_combo = QtWidgets.QComboBox(self)
        self.kind_combo.setObjectName(names.FILTER_KIND_COMBO)
        self.kind_combo.setFont(_ui_font())
        self.kind_combo.setFixedHeight(theme.BTN_H)
        self.kind_combo.addItem(terms.KIND_ALL, "")
        self.kind_combo.currentIndexChanged.connect(self._emit_filters)
        lay.addWidget(self.kind_combo)

        self.status_combo = QtWidgets.QComboBox(self)
        self.status_combo.setObjectName(names.FILTER_STATUS_COMBO)
        self.status_combo.setFont(_ui_font())
        self.status_combo.setFixedHeight(theme.BTN_H)
        for label, val in zip(terms.STATUS_FILTER_ITEMS, STATUS_VALUES):
            self.status_combo.addItem(label, val)
        self.status_combo.currentIndexChanged.connect(self._emit_filters)
        lay.addWidget(self.status_combo)

        self.search = QtWidgets.QLineEdit(self)
        self.search.setObjectName(names.FILTER_SEARCH)
        self.search.setFont(_ui_font())
        self.search.setFixedHeight(theme.BTN_H)
        self.search.setClearButtonEnabled(True)
        self.search.setMinimumWidth(280)
        self.search.setPlaceholderText(terms.SEARCH_PLACEHOLDER)
        self._debounce = QtCore.QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(SEARCH_DEBOUNCE_MS)
        self._debounce.timeout.connect(self._emit_filters)
        self.search.textChanged.connect(self._on_search_typed)
        lay.addWidget(self.search, 1)

        # 预设（C-291）+ 粘贴名单勾选入口（C-290）
        self.presets_btn = QtWidgets.QToolButton(self)
        self.presets_btn.setObjectName(names.FILTER_PRESETS_BTN)
        self.presets_btn.setText(terms.PRESETS)
        self.presets_btn.setFont(_ui_font())
        self.presets_btn.setFixedHeight(theme.BTN_H)
        self.presets_btn.setPopupMode(QtWidgets.QToolButton.InstantPopup)
        self.presets_btn.setToolButtonStyle(QtCore.Qt.ToolButtonTextOnly)
        self.presets_menu = QtWidgets.QMenu(self.presets_btn)
        self.presets_btn.setMenu(self.presets_menu)
        self.presets_menu.aboutToShow.connect(self.rebuild_presets_menu)
        lay.addWidget(self.presets_btn)
        self.rebuild_presets_menu()

    # ───────── state ─────────
    def set_state(self, state):
        self._state = state
        self.rebuild()

    def state(self):
        return self._state

    # ───────── 范围（C-038 / C-042）─────────
    def _on_scope(self, view_id):
        self.scopeChanged.emit(view_id)

    def scope(self):
        return self.scope_seg.current()

    def set_scope(self, view_id):
        self.scope_seg.set_current(view_id)

    def missing_pages(self):
        """本表没有的页（C-042）；组合根拿去写状态栏。"""
        return list(self._missing)

    def missing_pages_text(self):
        if not self._missing:
            return ""
        return terms.STATUS_MISSING_PAGES_FMT.format(pages=" / ".join(self._missing))

    def _refresh_scope_availability(self):
        st = self._state
        self._missing = []
        for vid in contracts.VIEW_IDS:
            ok = True
            if st is not None and hasattr(st, "page_available"):
                try:
                    ok = bool(st.page_available(vid))
                except Exception:                                  # noqa: BLE001
                    ok = True
            self.scope_seg.set_available(vid, ok, terms.SCOPE_MISSING_FMT.format(page=vid))
            if not ok:
                self._missing.append(vid)
        self.scope_hint.setText(self.missing_pages_text())

    # ───────── owner 多选（C-024..C-027）─────────
    def _rebuild_owner_menu(self):
        keep = set(self._owner_sel)
        self.owner_menu.clear()
        self._owner_acts = {}
        n_none = sum(1 for m in self._models if not (m.get("owner") or "").strip())
        entries = []
        if n_none:
            entries.append((terms.OWNER_NONE, "%s ×%d" % (terms.OWNER_NONE, n_none)))   # C-025
        for o in sorted({(m.get("owner") or "").strip() for m in self._models if (m.get("owner") or "").strip()}):
            entries.append((o, "%s ×%d" % (o, sum(1 for m in self._models
                                                  if (m.get("owner") or "").strip() == o))))
        for key, label in entries:
            a = self.owner_menu.addAction(label)
            a.setCheckable(True)
            a.setData(key)
            a.setChecked(key in keep)          # 先 setChecked 再 connect：不误触处理器
            a.toggled.connect(self._on_owner_toggled)
            self._owner_acts[key] = a
        self._owner_sel = {k for k in keep if k in self._owner_acts}    # C-027 丢死项
        self._update_owner_btn()

    def _on_owner_toggled(self, checked):
        act = self.sender()
        if act is None:
            return
        key = act.data()
        if checked:
            self._owner_sel.add(key)
        else:
            self._owner_sel.discard(key)
        self._update_owner_btn()
        self._emit_filters()

    def _update_owner_btn(self):
        """C-026：按钮文字随已选个数变，悬停列全部已选。"""
        sel = sorted(self._owner_sel)
        self.owner_btn.setText(terms.OWNER_ALL if not sel else terms.OWNER_N_FMT.format(n=len(sel)))
        self.owner_btn.setToolTip("\n".join(sel) if sel else terms.OWNER_ALL)

    def owner_actions(self):
        return dict(self._owner_acts)

    # ───────── 分类（C-028）─────────
    def _rebuild_kind_combo(self):
        cur = self.kind_combo.currentData()
        self.kind_combo.blockSignals(True)
        self.kind_combo.clear()
        self.kind_combo.addItem(terms.KIND_ALL, "")
        for k in sorted({m.get("kind") for m in self._models if m.get("kind")}):
            self.kind_combo.addItem(terms.KIND_LABELS.get(k, k), k)
        idx = self.kind_combo.findData(cur) if cur else 0
        self.kind_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self.kind_combo.blockSignals(False)

    # ───────── 搜索（C-030 / C-047）─────────
    def _on_search_typed(self, _text):
        self._debounce.start()

    def flush_search(self):
        """把待发的去抖立刻发掉（测试与「回车立即筛」用）。"""
        if self._debounce.isActive():
            self._debounce.stop()
            self._emit_filters()

    # ───────── 预设（C-291）─────────
    def preset_names(self):
        st = self._state
        try:
            data = st.settings().get("presets") or {} if st is not None else {}
        except Exception:                                          # noqa: BLE001
            data = {}
        return sorted(data) if isinstance(data, dict) else []

    def rebuild_presets_menu(self):
        self.presets_menu.clear()
        save = self.presets_menu.addAction(terms.PRESET_SAVE)
        save.triggered.connect(lambda _c=False: self.presetSaveRequested.emit())
        manage = self.presets_menu.addAction(terms.PRESET_MANAGE)
        manage.triggered.connect(lambda _c=False: self.presetLoadRequested.emit(""))
        paste = self.presets_menu.addAction(terms.LIST_BTN_PASTE_NAMES)          # C-290 入口
        paste.setObjectName(PASTE_NAMES_OBJECT_NAME)
        paste.triggered.connect(lambda _c=False: self.pasteNamesRequested.emit())
        got = self.preset_names()
        if got:
            self.presets_menu.addSeparator()
        for nm in got:
            a = self.presets_menu.addAction(nm)
            a.triggered.connect(lambda _c=False, n=nm: self.presetLoadRequested.emit(n))
        return self.presets_menu

    def preset_payload(self):
        """存预设时交给 dialogs/persist 的载荷（C-291：勾选由 state 出，筛选由本行出）。"""
        return {"scope": self.scope(), "filters": self.filters()}

    # ───────── 筛选值 ─────────
    def filters(self):
        return {"owners": set(self._owner_sel),
                "kind": self.kind_combo.currentData() or "",
                "status": self.status_combo.currentData() or "",
                "regex": self.search.text().strip()}

    def set_filters(self, d):
        """整体回填（预设取回 / 换表复位）；只发一次 filterChanged。"""
        d = d or {}
        self._mute += 1
        try:
            want = set(d.get("owners") or ())
            # 菜单还没建（换表前回填预设）时原样收下；建好了就只留本表真有的 owner（C-027）
            self._owner_sel = {o for o in want if o in self._owner_acts} if self._owner_acts else want
            for key, a in self._owner_acts.items():
                a.blockSignals(True)
                a.setChecked(key in self._owner_sel)
                a.blockSignals(False)
            self._update_owner_btn()
            idx = self.kind_combo.findData(d.get("kind") or "")
            self.kind_combo.setCurrentIndex(idx if idx >= 0 else 0)
            sidx = self.status_combo.findData(d.get("status") or "")
            self.status_combo.setCurrentIndex(sidx if sidx >= 0 else 0)
            self._debounce.stop()
            self.search.setText(d.get("regex") or "")
            self._debounce.stop()
        finally:
            self._mute -= 1
        self._emit_filters()

    def reset_filters(self):
        self.set_filters({"owners": set(), "kind": "", "status": "", "regex": ""})

    # ───────── 重建（换表 / 换范围 / models 到位）─────────
    def rebuild(self, models=None):
        """换表或换范围后重建 owner 菜单 / 分类下拉 / 范围可用性（C-027 / C-028 / C-042）。"""
        if models is None:
            st = self._state
            models = []
            if st is not None and hasattr(st, "models"):
                try:
                    models = list(st.models() or [])
                except Exception:                                  # noqa: BLE001
                    models = []
        self._models = list(models or [])
        self._mute += 1
        try:
            self._refresh_scope_availability()
            self._rebuild_owner_menu()
            self._rebuild_kind_combo()
        finally:
            self._mute -= 1
        self._emit_filters()

    def models(self):
        return list(self._models)

    # ───────── 发信号 ─────────
    def _emit_filters(self, *_a):
        if self._mute:
            return
        d = self.filters()
        self.filterChanged.emit(d)
        self.set_counts(*count_visible(self._models, d))

    def set_counts(self, visible, total, by_input):
        """C-031：状态栏「可见 N / 共 M（其中 K 个按输入信号名命中）」。

        清单 proxy 接好线之后由组合根用 `proxy.n_visible / proxy.n_by_input` 覆盖调用本方法；
        没接线时本行用自己的 `models` 算，口径与 `match_row` 完全一致。"""
        txt = visible_status_text(visible, total, by_input)
        self.setToolTip(txt)
        self.statusMessage.emit(txt)
        return txt
