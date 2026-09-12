# -*- coding: utf-8 -*-
"""GUI v2 C1-b：信号清单 `ui/signal_list.py` 的契约测试（附录 A「LIST」31 条，每条至少一个测试名含 ID）。

口径（执行计划 §2 L3/L4/L5）：
  · offscreen 起**独立** `SignalListPanel`（不起 MainWindow），真实鼠标 / 键盘走 `ui_harness` 的
    `click / click_cell / keys`（QTest），不直接调槽、不 emit 信号冒充用户；
  · 喂进去的是 **mirror 的真模型** —— `providers.TopoutProvider(cfg).skeleton_models()` 与
    `view_models(lite=True)`，两张 mirror 合起来 21 行、四档色都有；镜像里天然没有的状态档
    （needs-prefix / false-green / parse-err / error / wire-fallback）由真模型派生一行，
    只改 `status_detail` / `issues` 这几格，其余键全是引擎给的；
  · `ui/state.py` 由 C1-a 并行在写 —— 这里只用本文件内的最小 fake，接口假设见
    `signal_list.STATE_REQUIREMENTS`。
"""

import os
import re

import pytest

import ui_harness as H

pytest.importorskip("PySide6")

from PySide6 import QtCore, QtGui, QtWidgets          # noqa: E402

from dreg_verify import excel_model, providers        # noqa: E402
from dreg_verify.ui import contracts as CT            # noqa: E402
from dreg_verify.ui import names as N                 # noqa: E402
from dreg_verify.ui import signal_list as SL          # noqa: E402
from dreg_verify.ui import terms as T                 # noqa: E402
from dreg_verify.ui import theme as TH                # noqa: E402

Qt = QtCore.Qt
LC = CT.ListCol
LR = CT.ListRole


# ─────────────────────────── 真模型（mirror）───────────────────────────
class _Cfg(object):
    """`providers.ConfigSourceProto` 的最小满足物（诊断配置全空 = 出厂态）。"""

    def __init__(self, wb, probe_prefixes=None):
        self.wb = wb
        self.probe_prefixes = dict(probe_prefixes or {})
        self.force_signals = set()
        self.logic_overrides = {}
        self.include_risky = True


_CACHE = {}


def _provider(kind, probe_prefixes=None):
    wb = excel_model.load_workbook(H.mirror_path(kind))
    return providers.TopoutProvider(_Cfg(wb, probe_prefixes))


def real_models(kind="wl", lite=True):
    key = (kind, lite)
    if key not in _CACHE:
        p = _provider(kind)
        _CACHE[key] = (p.view_models("max", 256, False, lite=True) if lite else p.skeleton_models())
    return [dict(m) for m in _CACHE[key]]


def all_models():
    """两张 mirror 的 Topout 真模型（9 + 12 = 21 行，ok / warn / bad / note 四档齐）。"""
    return real_models("wl") + real_models("btlp")


def skeleton_models(kind="btlp"):
    return real_models(kind, lite=False)


def variant(base, **over):
    """在一行真模型上改几格 —— mirror 里天然不出现的状态档靠它造，其余键仍是引擎给的。"""
    m = dict(base)
    m.update(over)
    return m


def by_status(models, key):
    return [m for m in models if SL.status_key_of(m) == key]


def first_with(models, key):
    got = by_status(models, key)
    assert got, "mirror 里没有 %s 档的行（现有：%s）" % (key, sorted({SL.status_key_of(m) for m in models}))
    return got[0]


# ─────────────────────────── 最小 fake state ───────────────────────────
class FakeState(QtCore.QObject):
    """`contracts.WorkbenchStateProto` 里清单用得到的那一小块（见 STATE_REQUIREMENTS）。

    全内存、不落盘；signals 名与 `contracts.STATE_SIGNALS` 一致。"""

    workbookChanged = QtCore.Signal()
    scopeChanged = QtCore.Signal(str)
    modelsChanged = QtCore.Signal(str)
    modelUpdated = QtCore.Signal(str, str)
    currentChanged = QtCore.Signal(str)
    checksChanged = QtCore.Signal(str)
    negsChanged = QtCore.Signal(str)
    configChanged = QtCore.Signal(str)
    statusMessage = QtCore.Signal(str)

    def __init__(self, models=None, probe_prefixes=None, protected=()):
        super().__init__()
        self.scope = CT.DEFAULT_VIEW_ID
        self._models = [dict(m) for m in (models or [])]
        self._checked = None                     # None = 全勾（C-243）
        self._negs = set()
        self.current_name = ""
        self.probe_prefixes = dict(probe_prefixes or {})
        self.force_signals = set()
        self.logic_overrides = {}
        self.include_risky = True
        self._settings = {}
        self._protected = list(protected)
        self.bulk_depth = 0
        self.bulk_count = 0

    # —— 清单模型 ——
    def models(self, view_id=None):
        return [dict(m) for m in self._models]

    def model_of(self, name, view_id=None):
        return next((dict(m) for m in self._models if m.get("name") == name), None)

    def set_models(self, view_id, models, partial=False):
        self._models = [dict(m) for m in models]
        self.modelsChanged.emit(view_id or self.scope)

    def update_model(self, view_id, model):
        for i, m in enumerate(self._models):
            if m.get("name") == model.get("name"):
                self._models[i] = dict(model)
                break
        self.modelUpdated.emit(view_id or self.scope, model.get("name", ""))

    # —— 勾选 / 反例 ——
    def checked(self, view_id=None):
        return None if self._checked is None else set(self._checked)

    def is_checked(self, name, view_id=None):
        return self._checked is None or name in self._checked

    def set_checked(self, names, on, view_id=None):
        if self._checked is None:
            self._checked = {m.get("name", "") for m in self._models}
        for n in names:
            self._checked.add(n) if on else self._checked.discard(n)
        self.checksChanged.emit(view_id or self.scope)

    def negs(self, view_id=None):
        return set(self._negs)

    def set_neg(self, name, on, view_id=None):
        before = len(self._negs)
        self._negs.add(name) if on else self._negs.discard(name)
        self.negsChanged.emit(view_id or self.scope)
        return (abs(len(self._negs) - before), len(self._negs))

    def protected_negatives(self, names, view_id=None):
        return [n for n in names if n in self._protected]

    # —— 当前信号 ——
    def set_current(self, name):
        self.current_name = name
        self.currentChanged.emit(name)

    # —— 偏好 ——
    def settings(self):
        return dict(self._settings)

    def save_settings(self, patch):
        self._settings.update(patch)

    # —— 批量挂起（C-244）——
    def suspend_persist(self):
        state = self

        class _Ctx(object):
            def __enter__(self_inner):
                state.bulk_depth += 1
                state.bulk_count += 1
                return self_inner

            def __exit__(self_inner, *exc):
                state.bulk_depth -= 1
                return False
        return _Ctx()


# ─────────────────────────── 起窗 / 交互小工具 ───────────────────────────
_LIVE = []


def make_panel(models=None, state=None, size=(560, 760), show=True, **kw):
    """offscreen 起一个独立清单面板（不起 MainWindow）。"""
    H.app()
    st = state if state is not None else FakeState(all_models() if models is None else models, **kw)
    panel = SL.SignalListPanel(st)
    panel.resize(*size)
    if show:
        panel.show()
    H.app().processEvents()
    _LIVE.append(panel)
    return panel, st


@pytest.fixture(autouse=True)
def _close_widgets():
    yield
    while _LIVE:
        w = _LIVE.pop()
        w.close()
        w.deleteLater()
    H.app().processEvents()


def row_of_name(panel, name):
    """信号名 → proxy 里的可见行号（找不到给出全表，别对着空断言猜）。"""
    for r in range(panel.proxy.rowCount()):
        if panel.proxy.index(r, int(LC.NAME)).data(int(LR.NAME)) == name:
            return r
    raise AssertionError("清单里没有 %r；可见：%s" % (name, panel.visible_names()))


def cell(panel, name, col, role=Qt.DisplayRole):
    return panel.proxy.index(row_of_name(panel, name), int(col)).data(role)


def click_col(panel, name, col):
    """真实点一格（H.click_cell 走 visualRect 中心 + QTest）。"""
    H.click_cell(panel.view, row_of_name(panel, name), int(col))
    H.app().processEvents()


def click_header(panel, col):
    """真实点表头某一列（排序，C-037）。"""
    hdr = panel.view.header()
    x = hdr.sectionViewportPosition(int(col)) + hdr.sectionSize(int(col)) // 2
    y = max(1, hdr.viewport().height() // 2)
    H.click(hdr, (x, y))
    H.app().processEvents()


def expand_reason(panel, name):
    """真实点状态格展开行内原因块，返回 ReasonWidget。"""
    click_col(panel, name, LC.STATUS)
    return panel.view.reason_widget


def all_visible_text(widget):
    """窗口上所有能看见的文本（标签 / 按钮 / 富文本 / tooltip）—— 术语扫描用。"""
    out = []
    for w in widget.findChildren(QtWidgets.QWidget):
        for attr in ("text", "toolTip", "placeholderText"):
            fn = getattr(w, attr, None)
            if callable(fn):
                try:
                    out.append(str(fn() or ""))
                except TypeError:
                    pass
    m = widget.proxy if hasattr(widget, "proxy") else None
    if m is not None:
        for r in range(m.rowCount()):
            for c in LC:
                for role in (Qt.DisplayRole, Qt.ToolTipRole):
                    v = m.index(r, int(c)).data(role)
                    if v:
                        out.append(str(v))
    return "\n".join(out)


# ═══════════════════════════ C-044 / C-008 / C-045 骨架与列 ═══════════════════════════
def test_c044_first_row_selected_after_load():
    """C-044：刷新清单后自动选中第一个可见行（直接就能看到内容）。"""
    panel, st = make_panel()
    assert panel.visible_names(), "清单一行都没有"
    first = panel.visible_names()[0]
    assert panel.current_name() == first
    assert st.current_name == first, "选中第一行没有写回 state.set_current"
    H.shot(panel, "list_default")


def test_c008_c046_columns_dialog_toggles_optional_columns(monkeypatch):
    """C-008：10 列里 6 列默认可见、其余进「列设置…」；C-046：表达式列可选且默认隐藏。"""
    panel, st = make_panel()
    assert panel.visible_columns() == CT.LIST_DEFAULT_VISIBLE
    assert panel.view.isColumnHidden(int(LC.EXPR)), "表达式列默认应隐藏（C-046）"
    for col in CT.LIST_OPTIONAL:
        assert panel.view.isColumnHidden(int(col)), CT.LIST_COL_KEYS[col]

    def _turn_on_expr(call, *a, **k):
        dlg = call.args[0]
        lst = H.find(dlg, N.DLG_COLUMNS_LIST)
        idx = [i for i in range(lst.count())
               if lst.item(i).data(Qt.UserRole) == CT.LIST_COL_KEYS[LC.EXPR]][0]
        H.keys(lst, " ".join(["Down"] * (idx + 1) + ["Space"]))      # 真实键盘勾上「表达式」
        return QtWidgets.QDialog.Accepted

    rec = H.auto_dialogs(monkeypatch, answers={T.DLG_COLUMNS_TITLE: _turn_on_expr})
    H.click(H.find(panel, N.LIST_COLUMNS_BTN))
    H.app().processEvents()
    assert rec.saw("", key=T.DLG_COLUMNS_TITLE) or rec.count("exec"), rec.dump()
    assert not panel.view.isColumnHidden(int(LC.EXPR)), "列设置勾上后表达式列应可见"
    assert st.settings()[CT.SETTINGS_LIST_COLUMNS]["expr"] is True, "列可见性没有经 state 持久化"
    expr = cell(panel, panel.visible_names()[0], LC.EXPR)
    assert expr is not None
    H.shot(panel, "list_columns_expr_on")


def test_c045_default_visible_columns_no_hscroll():
    """C-045：默认可见列必须含「状态」与「用例」，任何布局下都不准进横向滚动条。"""
    panel, _ = make_panel(size=(1600, 700))
    H.app().processEvents()
    assert not panel.view.isColumnHidden(int(LC.STATUS))
    assert not panel.view.isColumnHidden(int(LC.NTEST))
    assert panel.view.horizontalScrollBar().maximum() == 0, "1600px 下出了横向滚动条"
    panel.resize(TH.CLAMP_LIST[0], 700)                                # 拖到最窄 320px 也不准出横滚
    H.app().processEvents()
    assert panel.view.horizontalScrollBar().maximum() == 0


# ═══════════════════════════ 列内容 C-009…C-013 / C-021 ═══════════════════════════
def test_c009_name_column_shows_width_slice_but_lookup_is_bare():
    """C-009：信号名列带位宽切片显示，查找仍按裸名。"""
    panel, _ = make_panel()
    sliced = [m for m in all_models() if "[" in str(m.get("disp", ""))]
    assert sliced, "mirror 里没有带位宽切片的信号"
    m = sliced[0]
    assert cell(panel, m["name"], LC.NAME) == m["disp"]
    assert "[" in cell(panel, m["name"], LC.NAME)
    assert cell(panel, m["name"], LC.NAME, int(LR.NAME)) == m["name"]   # 裸名做查找键
    assert panel.model.index_of(m["name"]).isValid()


def test_c010_assert_id_column_and_tooltip():
    """C-010：断言号列 + 悬停说明（仿真 log 报 assert_<号>_T<n> 时按它回查本信号）。"""
    panel, _ = make_panel()
    panel.set_visible_columns(set(CT.LIST_DEFAULT_VISIBLE) | {LC.ASSERT_ID})
    m = all_models()[0]
    assert cell(panel, m["name"], LC.ASSERT_ID) == str(m["assert_id"])
    assert cell(panel, m["name"], LC.ASSERT_ID, Qt.ToolTipRole) == T.LIST_ASSERT_TIP
    hdr = panel.model.headerData(int(LC.ASSERT_ID), Qt.Horizontal, Qt.ToolTipRole)
    assert hdr == T.LIST_ASSERT_TIP


def test_c011_owner_column_default_visible():
    """C-011：owner 列（默认可见，宽 76px）。"""
    panel, _ = make_panel()
    assert LC.OWNER in panel.visible_columns()
    assert panel.view.header().sectionSize(int(LC.OWNER)) == TH.LIST_COL_W["owner"]
    owners = {cell(panel, n, LC.OWNER) for n in panel.visible_names()}
    assert owners == {str(m["owner"]) for m in all_models()}
    assert len(owners) > 1, "mirror 里应有多个 owner"


def test_c012_kind_column_labels():
    """C-012：分类列（选路/logic · mux · 直连寄存器 · RO回读(跳过) · 未解析）。"""
    panel, _ = make_panel()
    panel.set_visible_columns(set(CT.LIST_DEFAULT_VISIBLE) | {LC.KIND})
    seen = {cell(panel, n, LC.KIND) for n in panel.visible_names()}
    assert T.KIND_LABELS["mux"] in seen or T.KIND_LABELS["logic"] in seen
    ro = [m for m in all_models() if m.get("kind") == "ro-readback"]
    assert ro, "mirror btlp 里应有 RO 回读信号"
    assert cell(panel, ro[0]["name"], LC.KIND) == T.KIND_LABELS["ro-readback"]


def test_c013_form_column_labels():
    """C-013：逻辑类型列（展开后表达式形态），覆盖度按它派发 —— 界面上不出现 F 编号。"""
    panel, _ = make_panel()
    panel.set_visible_columns(set(CT.LIST_DEFAULT_VISIBLE) | {LC.FORM})
    seen = {cell(panel, n, LC.FORM) for n in panel.visible_names() if cell(panel, n, LC.FORM)}
    # 门控行按内层形态分档（「门控·选路」/「门控·布尔/位运算」，P-27），所以不是 FORM_LABELS
    # 的子集；但每一档都得以四档表里的某个名字打头（覆盖度弹层按同一批名字派发）。
    assert all(any(s.startswith(v) for v in T.FORM_LABELS.values()) or s.startswith("门控")
               for s in seen), seen
    assert seen & {T.FORM_LABELS["select"], T.FORM_LABELS["register"]}
    assert not any(re.search(r"\bF[0-4]\b", s) for s in seen), "逻辑类型列不得出现 F 编号"


def test_c021_ntest_column_counts_and_pending_blank():
    """C-021：用例数列；「分析中」骨架行还没有用例数 → 留空而不是 0。"""
    panel, _ = make_panel()
    m = all_models()[0]
    assert cell(panel, m["name"], LC.NTEST) == str(m["n_vectors"])
    assert panel.model.headerData(int(LC.NTEST), Qt.Horizontal, Qt.TextAlignmentRole) \
        == int(Qt.AlignVCenter | Qt.AlignRight)
    sk = skeleton_models()
    p2, _ = make_panel(models=sk)
    assert cell(p2, sk[0]["name"], LC.NTEST) == ""


# ═══════════════════════════ 状态列 C-014…C-016 / C-040 / C-041 / C-007 ═══════════════════════════
def test_c014_status_column_four_tones():
    """C-014：状态列 4 档带颜色（ok / warn / bad / note，色值 = theme.TONE_COLORS）。"""
    panel, _ = make_panel()
    tones = {cell(panel, n, LC.STATUS, int(LR.TONE)) for n in panel.visible_names()}
    assert tones == {"ok", "warn", "bad", "note"}, "两张 mirror 合起来应四档齐：%s" % tones
    for tone in tones:
        assert TH.TONE_COLORS[tone][0].startswith("#") and TH.TONE_COLORS[tone][1].startswith("#")
    labels = {cell(panel, n, LC.STATUS) for n in panel.visible_names()}
    assert T.STATUS["clean"][0] in labels


def test_c015_c016_click_status_expands_reason_row():
    """C-015 / C-016：有问题的状态格点开 = 行内原因块列出全部原因；再点收起。同一时刻只展开一行。"""
    models = all_models()
    bad = first_with(models, "spec-collision")["name"]
    warn = first_with(models, "risky-generated")["name"]
    panel, _ = make_panel(models=models)

    w = expand_reason(panel, bad)
    assert w is not None and w.objectName() == N.LIST_REASON
    assert H.find(panel, N.LIST_REASON) is w
    assert H.find(panel, N.LIST_REASON_TITLE).text()
    body = H.find(panel, N.LIST_REASON_BODY).text()
    assert body.strip(), "原因块正文不得为空"
    assert panel.view.expanded_name == bad
    parent = panel.proxy.mapFromSource(panel.model.index_of(bad))
    assert panel.proxy.rowCount(parent) == 1
    H.app().processEvents()
    rect = panel.view.visualRect(panel.proxy.index(0, 0, parent))
    assert rect.height() > TH.ROW_H, "原因子行没有加高"
    assert rect.height() >= w.diag_btn.geometry().bottom(), "原因块里的按钮被行高切掉了"
    assert panel.view.isFirstColumnSpanned(0, parent), "原因子行要跨整行显示"
    H.shot(panel, "list_reason_open")

    expand_reason(panel, warn)                                   # 换一行 → 只剩这一行展开
    assert panel.view.expanded_name == warn
    assert panel.proxy.rowCount(panel.proxy.mapFromSource(panel.model.index_of(bad))) == 0

    click_col(panel, warn, LC.STATUS)                            # 再点同一格 = 收起
    assert panel.view.expanded_name == ""
    assert panel.view.reason_widget is None


def test_c016_status_tooltip_covers_eight_grades():
    """C-016：状态 8 档逐档解释（悬停）—— 八档 + 只读回读 + 分析中都要有自己的解释。"""
    base = all_models()[0]
    keys = ("clean", "wire-fallback", "needs-prefix", "risky-generated", "bare-probe",
            "false-green", "spec-collision", "parse-err", "unresolved", "skip", "error", "pending")
    models = [variant(base, name="sig_%d" % i, disp="sig_%d" % i, status_detail=k)
              for i, k in enumerate(keys)]
    panel, _ = make_panel(models=models)
    for i, k in enumerate(keys):
        tip = cell(panel, "sig_%d" % i, LC.STATUS, Qt.ToolTipRole) or ""
        assert T.STATUS[k][2] in tip, k
        assert cell(panel, "sig_%d" % i, LC.STATUS) .startswith(T.STATUS[k][0]), k


def test_c007_error_row_keeps_full_text():
    """C-007：单个信号解析炸了不连累整张表，那一行标「解析异常」并留住 error 全文。"""
    models = all_models()
    boom = "TypeError: 展开第 3 层时炸了（无 cone，跳过+记账）"
    models.append(variant(models[0], name="sig_boom", disp="sig_boom",
                          status="error", status_detail="error", issues=[boom], n_vectors=None))
    panel, _ = make_panel(models=models)
    assert len(panel.visible_names()) == len(models), "一行炸了不该影响别的行"
    assert cell(panel, "sig_boom", LC.STATUS) == T.STATUS["error"][0]
    assert cell(panel, "sig_boom", LC.STATUS, int(LR.TONE)) == "bad"
    tip = cell(panel, "sig_boom", LC.STATUS, Qt.ToolTipRole)
    assert "展开第 3 层时炸了" in tip and "记账" not in tip          # 全文留住、术语已换
    w = expand_reason(panel, "sig_boom")
    assert "展开第 3 层时炸了" in w.body.text()


def test_c040_normalized_gear_mark_and_tooltip():
    """C-040：嵌套 mux 被自动折叠过的信号，状态格加 ⚙ 标记、悬停给折叠全文。"""
    base = first_with(all_models(), "clean")
    note = "mux3 已折叠进上一级（第 2 级）"
    panel, _ = make_panel(models=[variant(base, name="sig_norm", disp="sig_norm",
                                          normalized_note=note)])
    assert cell(panel, "sig_norm", LC.STATUS).endswith("⚙")
    assert T.LIST_NORMALIZED_TIP.format(note=note) in cell(panel, "sig_norm", LC.STATUS, Qt.ToolTipRole)


def test_c041_risky_generated_is_sixth_status_grade():
    """C-041：开了「缺前缀强制生成」后状态列如实写「⚠ 输入缺前缀·已强制生成」而不是「跳过」。"""
    models = all_models()
    m = first_with(models, "risky-generated")
    panel, _ = make_panel(models=models)
    assert cell(panel, m["name"], LC.STATUS) == T.STATUS["risky-generated"][0]
    assert "强制生成" in cell(panel, m["name"], LC.STATUS)
    assert T.STATUS["needs-prefix"][0] != T.STATUS["risky-generated"][0]
    # C-041（主控裁决）：两档都带「输入」二字 —— 输入侧硬阻断 ≠ 输出侧裸名探针（bare-probe）
    for key in ("needs-prefix", "risky-generated"):
        assert T.STATUS[key][0].startswith("⚠ 输入缺前缀·"), T.STATUS[key][0]
    assert "输入" not in T.STATUS["bare-probe"][0]
    w = expand_reason(panel, m["name"])
    assert w.diag_btn.text() == T.REASON_BTN_FMT.format(action=T.REASON_TEMPLATES["risky-generated"][2])
    assert w.risky_btn is None, "这一档主按钮本身就是跳全局开关，不该再放一个同文案的按钮"


def test_c127_false_green_reason_block_explains_group_skip():
    """C-127：字段太窄·假绿的组，头部说明为什么整组跳过（要加宽字段或拆组，属设计层）。"""
    base = first_with(all_models(), "clean")
    panel, _ = make_panel(models=[variant(base, name="sig_fg", disp="sig_fg",
                                          status_detail="false-green",
                                          issues=["寄存器字段只有 2 bit（@0x91[5:4]），输出是 4 bit"])])
    assert cell(panel, "sig_fg", LC.STATUS) == T.STATUS["false-green"][0]
    w = expand_reason(panel, "sig_fg")
    assert w.title.text() == T.REASON_TEMPLATES["false-green"][0]
    assert "2 bit" in w.body.text()
    assert w.diag_btn.text() == T.REASON_BTN_FMT.format(action=T.REASON_TEMPLATES["false-green"][2])
    H.shot(panel, "list_reason_false_green")


def test_reason_block_needs_prefix_has_both_buttons():
    """裁决③：缺前缀档除了「三步流程」，还给一个跳全局开关的「去诊断 · 缺前缀是否强制生成」
    （Design 的「仍然生成（风险自负）」按裁决改成这个跳转）。"""
    base = first_with(all_models(), "clean")
    panel, _ = make_panel(models=[variant(base, name="sig_np", disp="sig_np",
                                          status_detail="needs-prefix",
                                          issues=["输入 mon_active 在 ENV_RF 层探不到"])])
    w = expand_reason(panel, "sig_np")
    seen = []
    w.diagRequested.connect(seen.append)
    risky = H.find(panel, N.LIST_REASON_RISKY_BTN)
    assert risky.text() == T.REASON_RISKY_BTN
    assert "仍然生成" not in risky.text()
    H.click(H.find(panel, N.LIST_REASON_DIAG_BTN))
    H.click(risky)
    assert seen == [T.REASON_TEMPLATES["needs-prefix"][3], "diag_risky"]


def test_reason_button_routes_through_panel_signal():
    """原因块的按钮只发路由信号（跳转由 app.route_reason_action 处理，视图不自己动配置）。"""
    models = all_models()
    name = first_with(models, "spec-collision")["name"]
    panel, _ = make_panel(models=models)
    got = []
    panel.diagRequested.connect(lambda target, sig: got.append((target, sig)))
    expand_reason(panel, name)
    H.click(H.find(panel, N.LIST_REASON_DIAG_BTN))
    assert got == [(T.REASON_TEMPLATES["spec-collision"][3], name)]


# ═══════════════════════════ 探针前缀列 C-017…C-020 ═══════════════════════════
def test_c017_c018_prefix_column_and_tooltip():
    """C-017：探针前缀列蓝色 = 已配；C-018：悬停给探针网真名 + 断言完整路径（配了 / 没配两种）。"""
    models = all_models()
    m0 = models[0]
    pfx = "U_WL_RF_TOP"
    p = _provider("wl", {m0["out_net"]: pfx})
    with_prefix = p.view_models("max", 256, False, lite=True)          # 真配上前缀再跑一趟引擎
    panel, _ = make_panel(models=with_prefix)
    panel.set_visible_columns(set(CT.LIST_DEFAULT_VISIBLE) | {LC.PREFIX})
    assert cell(panel, m0["name"], LC.PREFIX) == pfx
    assert cell(panel, m0["name"], LC.PREFIX, Qt.ForegroundRole).name() == TH.BLUE_DARK
    tip = cell(panel, m0["name"], LC.PREFIX, Qt.ToolTipRole)
    assert tip == T.LIST_PREFIX_TIP_SET_FMT.format(net=m0["out_net"], prefix=pfx)
    other = [m for m in with_prefix if not m.get("prefix")][0]
    assert cell(panel, other["name"], LC.PREFIX) == ""
    assert cell(panel, other["name"], LC.PREFIX, Qt.ToolTipRole) \
        == T.LIST_PREFIX_TIP_UNSET_FMT.format(net=other["out_net"])
    assert cell(panel, other["name"], LC.PREFIX, int(LR.PREFIX_TIP)) \
        == cell(panel, other["name"], LC.PREFIX, Qt.ToolTipRole)


def test_c019_prefix_cell_marks_level_shift_port():
    """C-019：探针网名经 level_shift 页自动解析时，列里标「探针口=xxx (level_shift)」（无需手配）。"""
    base = all_models()[0]
    ls = base["name"] + "_ls"
    panel, _ = make_panel(models=[variant(base, name="sig_ls", disp="sig_ls", probe_net=ls)])
    panel.set_visible_columns(set(CT.LIST_DEFAULT_VISIBLE) | {LC.PREFIX})
    assert cell(panel, "sig_ls", LC.PREFIX) == T.LIST_PREFIX_LEVEL_SHIFT_FMT.format(net=ls)
    assert "level_shift" in cell(panel, "sig_ls", LC.PREFIX)


def test_c020_prefix_cell_marks_input_side_hit():
    """C-020：某根 force 输入的路径带前缀时，列里标「<输入名>→<前缀>」。"""
    m = [x for x in all_models() if x.get("input_names")][0]
    inp = m["input_names"][-1]
    panel, _ = make_panel(probe_prefixes={inp.lower(): "U_SUB_BLK"})
    panel.set_visible_columns(set(CT.LIST_DEFAULT_VISIBLE) | {LC.PREFIX})
    assert cell(panel, m["name"], LC.PREFIX) == T.LIST_PREFIX_INPUT_HIT_FMT.format(
        input=inp, prefix="U_SUB_BLK")
    H.shot(panel, "list_prefix_column")


# ═══════════════════════════ 勾选 / 反例 C-022 / C-023 ═══════════════════════════
def test_c022_c023_check_and_neg_columns_write_state():
    """C-022 / C-023：勾选列与反例列都是真勾选框，点一下就写回 state（勾选列常驻第一列）。"""
    panel, st = make_panel()
    assert int(LC.CHECK) == 0 and int(LC.NEG) == 1
    name = panel.visible_names()[0]
    assert st.checked() is None                                       # 默认全勾不写桶（C-243）

    click_col(panel, name, LC.CHECK)                                  # 取消勾选
    assert st.is_checked(name) is False
    assert cell(panel, name, LC.CHECK, Qt.CheckStateRole) == Qt.Unchecked
    click_col(panel, name, LC.CHECK)                                  # 再勾上
    assert st.is_checked(name) is True

    assert st.negs() == set()
    click_col(panel, name, LC.NEG)
    assert st.negs() == {name}
    assert cell(panel, name, LC.NEG, Qt.CheckStateRole) == Qt.Checked
    click_col(panel, name, LC.NEG)
    assert st.negs() == set()
    H.shot(panel, "list_checked")


def test_c022_space_key_toggles_check_on_current_row():
    """键盘也能勾（真实 Space，不是调槽）。"""
    panel, st = make_panel()
    name = panel.visible_names()[0]
    panel.view.setCurrentIndex(panel.proxy.index(row_of_name(panel, name), int(LC.CHECK)))
    H.keys(panel.view, "Space")
    H.app().processEvents()
    assert st.is_checked(name) is False


# ═══════════════════════════ 底部工具条 C-032…C-036 / C-290 ═══════════════════════════
def test_c032_c033_c034_bottom_bar():
    """C-032 全选（可见）/ C-033 清空全部勾选 / C-034 勾选框选中的行。"""
    panel, st = make_panel()
    names = panel.visible_names()

    H.click(H.find(panel, N.LIST_BTN_UNCHECK_ALL))                    # C-033
    assert st.checked() == set()
    assert all(not st.is_checked(n) for n in names)

    H.click(H.find(panel, N.LIST_BTN_CHECK_ALL))                      # C-032
    assert st.checked() == set(names)

    H.click(H.find(panel, N.LIST_BTN_UNCHECK_ALL))
    sel = names[1:4]
    sm = panel.view.selectionModel()
    sm.clearSelection()
    for n in sel:
        sm.select(panel.proxy.index(row_of_name(panel, n), int(LC.NAME)),
                  QtCore.QItemSelectionModel.Select | QtCore.QItemSelectionModel.Rows)
    H.click(H.find(panel, N.LIST_BTN_CHECK_SELECTED))                 # C-034
    assert st.checked() == set(sel)
    assert st.bulk_count >= 3, "批量勾选没有包在 state.suspend_persist() 里（C-244）"


def test_c032_check_all_only_touches_visible_rows():
    """C-032 的「全选」= 全选**可见**信号（筛掉的不该被顺手勾上）。"""
    panel, st = make_panel()
    H.click(H.find(panel, N.LIST_BTN_UNCHECK_ALL))
    panel.set_filters(regex="lna")
    vis = panel.visible_names()
    assert 0 < len(vis) < len(st.models())
    H.click(H.find(panel, N.LIST_BTN_CHECK_ALL))
    assert st.checked() == set(vis)


def test_c035_c036_bulk_neg_confirm_when_named(monkeypatch):
    """C-035 一键给一批信号各加 1 条反例（有勾选只作用勾选）；
    C-036 清除反例：含自定义命名 / 手填错值的先弹确认，且**先点名字后计数**。"""
    models = all_models()
    protected = [models[2]["name"]]
    st = FakeState(models, protected=protected)
    panel, _ = make_panel(state=st)

    H.click(H.find(panel, N.LIST_BTN_UNCHECK_ALL))
    picked = [m["name"] for m in models[:3]]
    st.set_checked(picked, True)
    H.app().processEvents()
    H.click(H.find(panel, N.LIST_BTN_NEG_ALL))                        # C-035：只作用勾选
    assert st.negs() == set(picked)
    assert cell(panel, picked[0], LC.NEG, Qt.CheckStateRole) == Qt.Checked
    H.shot(panel, "list_neg_all")

    rec = H.auto_dialogs(monkeypatch)                                 # 默认答「是」
    H.click(H.find(panel, N.LIST_BTN_NEG_CLEAR))                      # C-036
    assert rec.count("question") == 1, rec.dump()
    blob = rec.last("question").blob
    assert protected[0] in blob, "二次确认必须先点名"
    assert blob.index(protected[0]) < blob.index(str(len(protected))), "点名要在计数之前（I-20）"
    assert st.negs() == set()

    st.set_neg(protected[0], True)
    st.set_checked([protected[0]], True)
    rec2 = H.auto_dialogs(monkeypatch, answers={"question": QtWidgets.QMessageBox.No})
    H.click(H.find(panel, N.LIST_BTN_NEG_CLEAR))
    assert rec2.count("question") == 1
    assert st.negs() == {protected[0]}, "答「否」不该删掉反例"


def test_c035_neg_all_falls_back_to_visible_when_nothing_checked():
    """C-035 的第二半：没有勾选时作用于全部可见。"""
    panel, st = make_panel()
    H.click(H.find(panel, N.LIST_BTN_UNCHECK_ALL))
    panel.set_filters(regex="lna")
    vis = panel.visible_names()
    H.click(H.find(panel, N.LIST_BTN_NEG_ALL))
    assert st.negs() == set(vis)


def test_c036_neg_clear_without_protected_does_not_ask(monkeypatch):
    """C-036：没有值得保护的反例就别拿确认框烦人。"""
    panel, st = make_panel()
    H.click(H.find(panel, N.LIST_BTN_NEG_ALL))
    assert st.negs()
    rec = H.auto_dialogs(monkeypatch)
    H.click(H.find(panel, N.LIST_BTN_NEG_CLEAR))
    assert rec.count("question") == 0, rec.dump()
    assert st.negs() == set()


def test_c290_paste_names_checks(monkeypatch):
    """C-290：粘贴一份信号名单，一次勾上对应的信号；找不到的点名回报。"""
    models = all_models()
    panel, st = make_panel(models=models)
    H.click(H.find(panel, N.LIST_BTN_UNCHECK_ALL))
    want = [models[0]["name"], models[5]["disp"]]                     # 带位宽切片的也认
    text = "\n".join(want + ["# 注释行", "d_not_in_this_table"])

    def _fill(call, *a, **k):
        dlg = call.args[0]
        # 真实 Ctrl+V（这个框本来就是拿来粘的）—— 别用 QTest.keyClicks 送多行串：
        # PySide6 6.11 的 keyClicks 碰到 "\n" 会把进程整个带走（offscreen 下实测）。
        H.paste(H.find(dlg, N.DLG_PASTE_NAMES_TEXT), text)
        return QtWidgets.QDialog.Accepted

    H.auto_dialogs(monkeypatch, answers={T.DLG_PASTE_NAMES_TITLE: _fill})
    H.click(H.find(panel, N.LIST_BTN_PASTE_NAMES))
    H.app().processEvents()
    assert st.checked() == {models[0]["name"], models[5]["name"]}
    assert "d_not_in_this_table" in H.find(panel, N.DLG_PASTE_NAMES_RESULT).text()


# ═══════════════════════════ 排序 C-037 ═══════════════════════════
def test_c037_sort_keeps_reason_row_attached():
    """C-037：点表头排序；排序后行内原因块仍贴着自己那一行（架构 §1.3① 选两级树的头号判据）。"""
    models = all_models()
    name = first_with(models, "spec-collision")["name"]
    panel, _ = make_panel(models=models)
    before = panel.visible_names()

    expand_reason(panel, name)
    assert panel.view.expanded_name == name

    click_header(panel, LC.OWNER)                                     # 真实点表头
    after = panel.visible_names()
    assert after != before, "点表头没有改变行序"
    assert after == sorted(after, key=lambda n: str(
        next(m for m in models if m["name"] == n)["owner"]).lower())

    assert panel.view.expanded_name == name, "排序后原因块跟丢了"
    w = H.find(panel, N.LIST_REASON)
    parent = panel.proxy.mapFromSource(panel.model.index_of(name))
    child = panel.proxy.index(0, 0, parent)
    assert panel.view.indexWidget(child) is w, "原因子行没有挂在原来那一行下面"
    assert panel.proxy.rowCount(parent) == 1
    H.shot(panel, "list_sorted")


def test_c037_header_sort_by_ntest_and_status_uses_sort_role():
    """C-037：按用例数（数值序，不是字符串序）与状态（按色档）排。"""
    panel, _ = make_panel()
    click_header(panel, LC.NTEST)
    vals = [int(cell(panel, n, LC.NTEST) or -1) for n in panel.visible_names()]
    assert vals == sorted(vals), vals
    click_header(panel, LC.STATUS)
    names = panel.visible_names()
    by_name = {m["name"]: m for m in all_models()}
    # 权重按色档，但 note 色档跟着「v1 四档归属」分家（P-21）：`bare-probe` 在 v1 里
    # status=ok，排序时就该落在可建那一堆里，而不是跟只读回读根挤在一起。
    order = {"bad": 0, "warn": 1, "note": 2, "ok": 3}

    def rank(n):
        m = by_name[n]
        key = T.status_key_of(m)
        if key in T.NOTE_AS_OK_KEYS:
            return order["ok"]
        if key in T.NOTE_AS_PROBLEM_KEYS:
            return order["note"]
        return order[T.tone_of(m)]

    ranks = [rank(n) for n in names]
    assert ranks == sorted(ranks), [(n, cell(panel, n, LC.STATUS, int(LR.TONE))) for n in names]


def test_c037_sort_menu_is_second_entry():
    """C-037 的第二个入口：清单头部「排序」菜单（按状态 / 用例 / owner / 信号名）。"""
    panel, _ = make_panel()
    btn = H.find(panel, N.LIST_SORT_BTN)
    assert btn.text() == T.LIST_SORT
    labels = [a.text() for a in panel.sort_menu.actions()]
    assert labels == [T.LIST_HEADERS["status"], T.LIST_HEADERS["ntest"],
                      T.LIST_HEADERS["owner"], T.LIST_HEADERS["name"]]
    panel.sort_menu.actions()[2].trigger()                            # owner
    assert panel.view.header().sortIndicatorSection() == int(LC.OWNER)


# ═══════════════════════════ RTL 补充标记 C-039 ═══════════════════════════
def test_c039_supplement_amber_dot_and_tooltip():
    """C-039：RTL 补充逻辑信号在信号名后琥珀圆点 + 悬停给理由（横幅已改为状态栏一行）。"""
    base = all_models()[0]
    note = "ECO 级补充：加了一级与门"
    panel, _ = make_panel(models=[variant(base, name="sig_supp", disp="sig_supp",
                                          supplement=True, normalized_note=note),
                                  variant(base, name="sig_plain", disp="sig_plain")])
    assert cell(panel, "sig_supp", LC.NAME, int(LR.SUPPLEMENT)) is True
    dot = cell(panel, "sig_supp", LC.NAME, Qt.DecorationRole)
    assert isinstance(dot, QtGui.QIcon) and not dot.isNull()
    assert cell(panel, "sig_supp", LC.NAME, Qt.ToolTipRole) == T.LIST_SUPPLEMENT_TIP.format(note=note)
    assert cell(panel, "sig_plain", LC.NAME, Qt.DecorationRole) is None
    H.shot(panel, "list_supplement_dot")


# ═══════════════════════════ 骨架态 / 增量升级（裁决⑨ · C-276）═══════════════════════════
def test_pending_rows_are_greyed_and_upgrade_without_reset():
    """「分析中」骨架行灰显；signalDone 后只发 dataChanged 增量升级，**不重建**。"""
    sk = skeleton_models("wl")
    lite = real_models("wl")
    st = FakeState(sk)
    panel, _ = make_panel(state=st)
    name = sk[0]["name"]
    assert cell(panel, name, LC.STATUS) == T.STATUS["pending"][0]
    assert cell(panel, name, LC.STATUS, int(LR.PENDING)) is True
    assert cell(panel, name, LC.NAME, Qt.ForegroundRole).name() == TH.DISABLED_TEXT
    H.shot(panel, "list_pending")

    resets, changed = [], []
    panel.model.modelReset.connect(lambda: resets.append(1))
    panel.model.dataChanged.connect(lambda a, b, *r: changed.append(a.row()))
    st.update_model("topout", next(m for m in lite if m["name"] == name))
    H.app().processEvents()
    assert not resets, "逐信号升级不该重建 model"
    assert changed, "逐信号升级要发 dataChanged"
    assert cell(panel, name, LC.STATUS) != T.STATUS["pending"][0]
    assert cell(panel, name, LC.NTEST) == str(next(m for m in lite if m["name"] == name)["n_vectors"])


def test_models_changed_rebuilds_and_reselects_first_row():
    """worker finished → state.modelsChanged → 整表重建并重新选中第一个可见行（C-044）。"""
    st = FakeState(skeleton_models("wl"))
    panel, _ = make_panel(state=st)
    st.set_models("topout", real_models("wl"))
    H.app().processEvents()
    assert panel.visible_names()
    assert panel.current_name() == panel.visible_names()[0]
    assert all(cell(panel, n, LC.STATUS) != T.STATUS["pending"][0] for n in panel.visible_names())


def test_current_row_syncs_both_ways_with_state():
    """清单选中行 ↔ state.current_name 双向同步（真实方向键）。"""
    panel, st = make_panel()
    H.keys(panel.view, "Down")
    H.app().processEvents()
    assert st.current_name == panel.current_name()
    assert st.current_name == panel.visible_names()[1]
    target = panel.visible_names()[4]
    st.set_current(target)
    H.app().processEvents()
    assert panel.current_name() == target


# ═══════════════════════════ 筛选计数接口（C-031 的清单侧）═══════════════════════════
def test_counts_text_and_visible_counts_text():
    """头部「要验信号 N · 已勾选 K · 有问题 P」与筛选后给状态栏的一行。"""
    panel, st = make_panel()
    n = len(panel.visible_names())
    # P-20：「有问题」的判据只有 `terms.match_status` 一份（清单头部 / 筛选行 / 状态栏共用）。
    # 以前这里按色档算，`skip`（只读回读根，v1 四档里 status="skip"）不算 —— 而筛选行
    # 「仅有问题」筛得出它，同一块屏幕上两个数。
    p = sum(1 for x in st.models() if T.match_status(x, T.STATUS_FILTER_ITEMS[2]))
    assert H.find(panel, N.LIST_HEADER_COUNTS).text() == T.LIST_COUNTS_FMT.format(n=n, k=n, p=p)
    said = []
    panel.statusMessage.connect(said.append)
    panel.set_filters(regex="lna")
    v = len(panel.visible_names())
    assert said and said[-1] == T.STATUS_VISIBLE_FMT.format(v=v, m=n, k=panel.proxy.n_by_input())
    assert H.find(panel, N.LIST_HEADER_COUNTS).text().startswith("要验信号 %d " % v)


def test_regex_filter_can_match_input_signal_names():
    """搜索命中输入信号名时单独计数（C-031 的「其中 K 个按输入信号名命中」）。"""
    m = [x for x in all_models() if x.get("input_names")][0]
    inp = [i for i in m["input_names"] if i.lower() != m["name"].lower()]
    if not inp:
        pytest.skip("这张 mirror 里没有与输出不同名的输入")
    panel, _ = make_panel()
    panel.set_filters(regex=re.escape(inp[0]))
    assert m["name"] in panel.visible_names()
    assert panel.proxy.n_by_input() >= 1


def test_broken_regex_falls_back_to_literal_search():
    """用户手打的正则写坏了不报错、按字面串搜（GUI 说工程师的语言，不弹 re.error）。"""
    panel, _ = make_panel()
    n = len(panel.visible_names())
    panel.set_filters(regex="d_wl_rf_lo2g5g_bias_en[")                # 未闭合字符组
    assert panel.visible_names() == []
    panel.set_filters(regex="*trim")                                  # nothing to repeat
    assert panel.visible_names() == []
    panel.set_filters(regex="(")                                      # 按字面的「(」搜 → 命中表达式里的括号
    hit = panel.visible_names()
    assert 0 < len(hit) <= n
    assert all("(" in (cell(panel, x, LC.NAME, int(LR.EXPR)) or "") for x in hit)


# ═══════════════════════════ 不变量 I-12 / I-14 ═══════════════════════════
def test_i14_every_list_objectname_is_present():
    """I-14：`names.py` 里所有 `list_*` 名字都能在清单区 find 到（原因块的两个按钮要展开才有）。"""
    base = all_models()[0]
    panel, _ = make_panel(models=[variant(base, name="sig_np", disp="sig_np",
                                          status_detail="needs-prefix", issues=["缺前缀"])])
    expand_reason(panel, "sig_np")
    want = {v for k, v in N.all_names().items() if v.startswith("list_")}
    assert want, "names.py 里一个 list_* 都没有？"
    for objname in sorted(want):
        H.find(panel, objname)                                        # 找不到会带候选名一起报


def test_i12_backend_text_is_scrubbed_everywhere():
    """I-12：所有显示后端文本的地方先过 terms.scrub —— 界面上不得裸露内部术语。"""
    base = all_models()[0]
    raw = "无 cone，跳过+记账；prefixed-wire 提升；regmap 没命中；必 CUVUNF"
    panel, _ = make_panel(models=[variant(base, name="sig_raw", disp="sig_raw",
                                          status_detail="unresolved", issues=[raw], note=raw,
                                          normalized_note=raw, expr=raw)])
    panel.set_visible_columns(set(LC))
    expand_reason(panel, "sig_raw")
    blob = all_visible_text(panel)
    for word in ("记账", "CUVUNF", "prefixed-wire", "regmap", "wire 兜底"):
        assert word not in blob, "界面上裸露了内部术语 %r" % word
    # R3-06：`记账` 统一译成「只记录、不产生断言」（与术语表 T 数组同一个说法）
    assert "找不到这根网" in blob and "不产生断言" in blob


def test_reason_block_never_shows_unfilled_placeholder():
    """引擎还没给结构化 meta 时正文退化成全文，标题退成该档标签 —— 界面上绝不露 `{n_bad}`。"""
    models = all_models()
    panel, _ = make_panel(models=models)
    for key in sorted({SL.status_key_of(m) for m in models}):
        if key not in T.REASON_TEMPLATES:
            continue
        name = first_with(models, key)["name"]
        w = expand_reason(panel, name)
        assert w is not None, key
        for text in (w.title.text(), w.body.text(), w.diag_btn.text()):
            assert not re.search(r"\{[a-z_]+\}", text), (key, text)
            assert text.strip(), key


def test_theme_and_terms_are_the_only_source_of_pixels_and_copy():
    """视图模块里不写裸十六进制色值、不写用户可见的裸中文串（B3 骨架的硬规则）。"""
    src = open(SL.__file__, encoding="utf-8").read()
    body = "\n".join(ln for ln in src.splitlines()
                     if not ln.strip().startswith("#") and "」" not in ln)
    assert not re.findall(r'"#[0-9a-fA-F]{6}"', body), "signal_list.py 里出现了裸色值"
    assert "from . import theme" in src and "from . import terms" in src


def test_screenshots_exist():
    """截图落在 refactor_notes/gui_v2_shots/（.gitignore 覆盖，产物不入库）。"""
    panel, _ = make_panel()
    path = H.shot(panel, "list_panel_final")
    assert os.path.exists(path) and os.path.getsize(path) > 0
    assert os.path.basename(os.path.dirname(path)) == "gui_v2_shots"


# ═══════════════════════════ 契约 ID 自查 ═══════════════════════════
#: 附录 A「LIST —— ui/signal_list.py（31 条）」
LIST_IDS = ("C-007", "C-008", "C-009", "C-010", "C-011", "C-012", "C-013", "C-014", "C-015",
            "C-016", "C-017", "C-018", "C-019", "C-020", "C-021", "C-022", "C-023", "C-032",
            "C-033", "C-034", "C-035", "C-036", "C-037", "C-039", "C-040", "C-041", "C-044",
            "C-045", "C-046", "C-127", "C-290")


def test_every_list_contract_id_has_a_test():
    """31 条 LIST 契约每条至少一个测试名含 ID（C5 的 contract_check --strict 靠这个机械锁）。"""
    names_here = [k for k in globals() if k.startswith("test_")]
    missing = [cid for cid in LIST_IDS
               if not any(cid.lower().replace("-", "") in n for n in names_here)]
    assert not missing, "这些 LIST 契约还没有对应测试：%s" % missing
    assert len(LIST_IDS) == 31
