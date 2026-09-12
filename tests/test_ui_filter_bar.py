# -*- coding: utf-8 -*-
"""GUI v2 Phase C1-c —— ② 筛选行 `dreg_verify/ui/filter_bar.py`。

契约 ID（附录 A「FILTER」12 条，每条 ≥1 条测试名含 ID）：
    C-024 owner 多选 / C-025 「（无 owner）」带条数 / C-026 按钮文字随已选数变 · 悬停列全部 /
    C-027 换表丢死项 / C-028 分类只列本表真有的 / C-029 状态三档 / C-030 正则四字段 /
    C-031 「可见 N / 共 M（其中 K 个按输入信号名命中）」/ C-038 范围 5 段 / C-042 无该页置灰并标 /
    C-047 `_to_dft` 这类目的地后缀也能命中 / C-291 预设存取。

起 offscreen 独立控件（不起主窗），用真实事件（`ui_harness.click / type_text`）；
`ui/state.py` 由 C1-a 并行在写 —— 这里只按 `contracts.WorkbenchStateProto` 的四个方法做最小 fake。
"""
import os

import pytest

import ui_harness as H

pytest.importorskip("PySide6")

from PySide6 import QtCore, QtWidgets            # noqa: E402

from dreg_verify.ui import contracts, names, terms                # noqa: E402
from dreg_verify.ui import filter_bar as FB                       # noqa: E402


# ───────────────────────── 最小 fake state ─────────────────────────

class FakeState(object):
    """只实现 filter_bar 用到的那几个 `WorkbenchStateProto` 成员（C1-a 的真 state 是超集）。"""

    def __init__(self, models=None, pages=None, settings=None):
        self.scope = contracts.DEFAULT_VIEW_ID
        self._models = list(models or [])
        self._pages = dict(pages or {v: True for v in contracts.VIEW_IDS})
        self._settings = dict(settings or {})

    def models(self, view_id=None):
        return list(self._models)

    def set_models(self, models):
        self._models = list(models)

    def page_available(self, view_id):
        return bool(self._pages.get(view_id, True))

    def set_scope(self, view_id):
        self.scope = view_id

    def settings(self):
        return self._settings


def _m(name, owner="", kind="logic", status="ok", issues=(), expr="", inputs=(), aid=""):
    return {"name": name, "disp": name, "owner": owner, "kind": kind, "status": status,
            "issues": list(issues), "expr": expr, "input_names": list(inputs), "assert_id": aid,
            "n_vectors": 8, "form": "boolean"}


def sample_models():
    return [
        _m("sig_alpha", "wang", "logic", "ok", expr="A & B",
           inputs=["d_rf_alpha_en", "d_rf_alpha_sel"], aid="1"),
        _m("sig_beta", "wang", "mux", "ok", expr="sel ? A : B",
           inputs=["d_rf_beta_to_mux"], aid="2"),
        _m("sig_gamma", "li", "logic", "unresolved", issues=["缺一根输入"], expr="C | D",
           inputs=["d_rf_gamma_to_dft"], aid="3"),
        _m("sig_delta", "", "register", "ok", expr="d_reg[3:0]",
           inputs=["d_rf_delta_raw"], aid="4"),
        _m("sig_eps", "", "mux", "ok", issues=["名字是猜的"], expr="sel ? E : F",
           inputs=["d_rf_eps_to_dft"], aid="5"),
    ]


@pytest.fixture()
def bar():
    H.app()
    st = FakeState(sample_models())
    host = QtWidgets.QWidget()
    host.resize(1600, 60)
    lay = QtWidgets.QVBoxLayout(host)
    lay.setContentsMargins(0, 0, 0, 0)
    w = FB.FilterBar(st, host)
    lay.addWidget(w)
    w.rebuild()
    host.show()
    H.app().processEvents()
    yield w
    host.close()
    host.deleteLater()


def _catch(sig):
    """把信号收进 list（不动槽逻辑，断言用）。"""
    got = []
    sig.connect(lambda *a: got.append(a[0] if len(a) == 1 else a))
    return got


def _click_action(menu, act):
    """对 QMenu 的某一项发真实鼠标点击（走 `_CheckableMenu.mouseReleaseEvent`）。"""
    menu.popup(QtCore.QPoint(0, 0))
    H.app().processEvents()
    H.click(menu, menu.actionGeometry(act).center())
    H.app().processEvents()
    menu.hide()
    H.app().processEvents()


# ───────────────────────── C-038 / C-042 范围 ─────────────────────────

def test_c038_scope_switch_changes_state_scope(bar):
    seg = H.find(bar, names.FILTER_SCOPE_SEG)
    assert seg is bar.scope_seg
    # 5 段都在，名字来自 names.fmt_scope_btn
    for vid in contracts.VIEW_IDS:
        b = H.find(bar, names.fmt_scope_btn(vid))
        assert b.text() == terms.SCOPE_LABELS[vid]
    assert bar.scope() == contracts.DEFAULT_VIEW_ID          # 首段默认选中
    st = bar.state()
    bar.scopeChanged.connect(st.set_scope)                   # 组合根的接法：视图不直接改 state
    got = _catch(bar.scopeChanged)
    H.click(H.find(bar, names.fmt_scope_btn("logic")))
    H.app().processEvents()
    assert got == ["logic"]
    assert st.scope == "logic"
    assert bar.scope() == "logic"
    # scopeHint 全文（Design 模板未渲染它）挂在范围段的 tooltip 上，行上不占位
    assert bar.scope_seg.toolTip() == terms.SCOPE_HINT
    assert H.find(bar, names.FILTER_SCOPE_HINT).text() == ""


def test_c042_missing_page_disabled_with_label(bar):
    st = bar.state()
    st._pages["dft"] = False
    st._pages["iddq"] = False
    bar.rebuild()
    H.app().processEvents()
    dft = H.find(bar, names.fmt_scope_btn("dft"))
    assert not dft.isEnabled()
    assert dft.toolTip() == terms.SCOPE_MISSING_FMT.format(page="dft") == "本表无 dft 页"
    assert H.find(bar, names.fmt_scope_btn("logic")).isEnabled()
    assert bar.missing_pages() == ["dft", "iddq"]
    assert bar.missing_pages_text() == terms.STATUS_MISSING_PAGES_FMT.format(pages="dft / iddq")
    hint = H.find(bar, names.FILTER_SCOPE_HINT).text()
    assert hint == "本表无 dft / iddq 页，门控层跳过"          # 置灰的同时在界面上写明白
    # 置灰的段点不动（不发 scopeChanged）
    got = _catch(bar.scopeChanged)
    H.click(dft)
    H.app().processEvents()
    assert got == []


# ───────────────────────── C-024..C-027 owner 多选 ─────────────────────────

def test_c024_c025_c026_c027_owner_menu(bar):
    btn = H.find(bar, names.FILTER_OWNER_BTN)
    menu = bar.owner_menu
    assert btn.text() == terms.OWNER_ALL
    acts = bar.owner_actions()
    assert set(acts) == {terms.OWNER_NONE, "li", "wang"}
    # C-025：「（无 owner）」单列一项并带条数（样例里 2 个空 owner）
    assert acts[terms.OWNER_NONE].text() == "%s ×2" % terms.OWNER_NONE

    got = _catch(bar.filterChanged)
    _click_action(menu, acts["wang"])                        # C-024 勾一个
    assert acts["wang"].isChecked()
    assert got and got[-1]["owners"] == {"wang"}
    # C-026：按钮文字随已选个数变、悬停列全部已选
    assert btn.text() == terms.OWNER_N_FMT.format(n=1)
    _click_action(menu, acts["li"])
    assert btn.text() == terms.OWNER_N_FMT.format(n=2)
    assert btn.toolTip() == "li\nwang"
    assert bar.filters()["owners"] == {"li", "wang"}
    # C-024 语义：勾多个 = 任一命中
    vis, total, _by = FB.count_visible(bar.models(), bar.filters())
    assert (vis, total) == (3, 5)

    # C-027：换表后新表没有 li → 丢掉死项，计数不虚高
    bar.state().set_models([_m("sig_new", "wang"), _m("sig_other", "zhao")])
    bar.rebuild()
    H.app().processEvents()
    assert set(bar.owner_actions()) == {"wang", "zhao"}
    assert bar.filters()["owners"] == {"wang"}
    assert btn.text() == terms.OWNER_N_FMT.format(n=1)


# ───────────────────────── C-028 / C-029 分类 · 状态 ─────────────────────────

def test_c028_c029_kind_status_filters(bar):
    kind = H.find(bar, names.FILTER_KIND_COMBO)
    # C-028：只列本表真有的分类（样例三种），第一项「全部分类」
    assert kind.itemText(0) == terms.KIND_ALL
    got_kinds = [kind.itemData(i) for i in range(1, kind.count())]
    assert got_kinds == ["logic", "mux", "register"]
    assert kind.itemText(1) == terms.KIND_LABELS["logic"]
    assert "iddq" not in got_kinds

    got = _catch(bar.filterChanged)
    kind.setCurrentIndex(kind.findData("mux"))
    H.app().processEvents()
    assert got[-1]["kind"] == "mux"
    assert FB.count_visible(bar.models(), bar.filters())[0] == 2
    kind.setCurrentIndex(0)

    # C-029：全部 / 仅可建 / 仅有问题
    status = H.find(bar, names.FILTER_STATUS_COMBO)
    assert [status.itemText(i) for i in range(status.count())] == list(terms.STATUS_FILTER_ITEMS)
    status.setCurrentIndex(1)                                 # 仅可建
    H.app().processEvents()
    assert got[-1]["status"] == "ok"
    assert FB.count_visible(bar.models(), bar.filters())[0] == 4
    status.setCurrentIndex(2)                                 # 仅有问题（非 ok 或带告警）
    H.app().processEvents()
    assert got[-1]["status"] == "issues"
    assert FB.count_visible(bar.models(), bar.filters())[0] == 2


# ───────────────────────── C-030 / C-031 / C-047 搜索 ─────────────────────────

def test_c030_c031_regex_search_reports_visible_and_by_input(bar):
    search = H.find(bar, names.FILTER_SEARCH)
    assert search.placeholderText() == terms.SEARCH_PLACEHOLDER
    msgs = _catch(bar.statusMessage)
    got = _catch(bar.filterChanged)

    # 去抖 150ms：敲字当下不重筛
    H.type_text(search, "alpha|beta")
    assert got == []
    bar.flush_search()
    assert got[-1]["regex"] == "alpha|beta"
    assert msgs[-1] == terms.STATUS_VISIBLE_FMT.format(v=2, m=5, k=0)

    # 正则真的走了引擎（^ 锚定）；同时匹配 断言号 / 表达式 / 输入信号名
    for pat, exp_v, exp_k in (("^sig_gamma$", 1, 0),      # 信号名
                              ("sel \\? A", 1, 0),        # 表达式
                              ("d_rf_delta_raw", 1, 1),   # 只因输入信号名命中 → C-031 的 K
                              ("^5$", 1, 0)):             # 断言号
        search.setText(pat)
        bar.flush_search()
        v, m, k = FB.count_visible(bar.models(), bar.filters())
        assert (v, m, k) == (exp_v, 5, exp_k), pat
        assert msgs[-1] == terms.STATUS_VISIBLE_FMT.format(v=exp_v, m=5, k=exp_k)
    assert "按输入信号名命中" in msgs[-1]

    # 去抖计时器真的会自己到点（不靠 flush）
    search.setText("")
    bar.flush_search()
    n0 = len(got)
    H.type_text(search, "sig_")
    QtCore.QCoreApplication.processEvents()
    from PySide6.QtTest import QTest
    QTest.qWait(FB.SEARCH_DEBOUNCE_MS + 120)
    assert len(got) > n0 and got[-1]["regex"] == "sig_"


def test_c047_type_suffix_search(bar):
    """Excel type 列筛并进正则搜索：`_to_dft` 这类目的地后缀也能命中（C-047）。"""
    search = H.find(bar, names.FILTER_SEARCH)
    search.setText("_to_dft")
    bar.flush_search()
    v, m, k = FB.count_visible(bar.models(), bar.filters())
    assert (v, m, k) == (2, 5, 2)                 # gamma / eps 的输入是 *_to_dft
    hit = [mm["name"] for mm in bar.models()
           if FB.match_row(mm, rx=FB.compile_regex("_to_dft"), raw="_to_dft")[0]]
    assert hit == ["sig_gamma", "sig_eps"]
    # 写坏的正则不炸：退化成子串匹配
    search.setText("(_to_dft")
    bar.flush_search()
    assert FB.compile_regex("(_to_dft") is None
    assert FB.count_visible(bar.models(), bar.filters())[0] == 0


# ───────────────────────── C-291 预设 ─────────────────────────

def test_c291_presets_roundtrip(bar):
    btn = H.find(bar, names.FILTER_PRESETS_BTN)
    assert btn.text() == terms.PRESETS
    saved = _catch(bar.presetSaveRequested)
    loaded = _catch(bar.presetLoadRequested)
    pasted = _catch(bar.pasteNamesRequested)

    menu = bar.rebuild_presets_menu()
    labels = [a.text() for a in menu.actions() if not a.isSeparator()]
    assert labels[:3] == [terms.PRESET_SAVE, terms.PRESET_MANAGE, terms.LIST_BTN_PASTE_NAMES]
    menu.actions()[0].trigger()
    assert len(saved) == 1
    # 存预设的载荷 = 当前范围 + 当前筛选（勾选由 state 出）
    H.find(bar, names.FILTER_STATUS_COMBO).setCurrentIndex(1)
    H.find(bar, names.FILTER_SEARCH).setText("sig_a")
    bar.flush_search()
    payload = bar.preset_payload()
    assert payload == {"scope": "topout",
                       "filters": {"owners": set(), "kind": "", "status": "ok", "regex": "sig_a"}}

    # 已存预设进菜单，点一条 → presetLoadRequested(name)
    bar.state().settings()["presets"] = {"我的常用": payload, "夜班": payload}
    menu = bar.rebuild_presets_menu()
    assert bar.preset_names() == ["夜班", "我的常用"]
    act = [a for a in menu.actions() if a.text() == "夜班"][0]
    act.trigger()
    assert loaded == ["夜班"]

    # 取回预设 = 整体回填，只发一次 filterChanged
    bar.reset_filters()
    got = _catch(bar.filterChanged)
    bar.set_filters(payload["filters"])
    assert len(got) == 1
    assert bar.filters() == payload["filters"]

    # C-290 入口（粘贴名单勾选）只发信号，对话框在 C2-d
    paste = [a for a in menu.actions() if a.objectName() == FB.PASTE_NAMES_OBJECT_NAME][0]
    paste.trigger()
    assert len(pasted) == 1


# ───────────────────────── objectName 全覆盖 + 截图 ─────────────────────────

def test_filter_bar_object_names_and_shot(bar):
    for nm in (names.FILTER_BAR, names.FILTER_SCOPE_SEG, names.FILTER_SCOPE_HINT,
               names.FILTER_OWNER_BTN, names.FILTER_KIND_COMBO, names.FILTER_STATUS_COMBO,
               names.FILTER_SEARCH, names.FILTER_PRESETS_BTN):
        assert H.find(bar, nm) is not None
    assert H.find(bar, names.FILTER_OWNER_MENU, QtWidgets.QMenu) is bar.owner_menu
    path = H.shot(bar, "filter_bar_default")
    assert os.path.exists(path)
    bar.state()._pages["dft"] = False
    bar.rebuild()
    H.find(bar, names.FILTER_SEARCH).setText("d_rf_.*_to_dft")
    bar.flush_search()
    H.app().processEvents()
    assert os.path.exists(H.shot(bar, "filter_bar_missing_page_and_search"))
