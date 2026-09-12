# -*- coding: utf-8 -*-
"""GUI v2 Phase C1-c —— ⑯ 覆盖度控件 `dreg_verify/ui/coverage.py`。

契约 ID（附录 A「COV」18 条，每条 ≥1 条测试名含 ID）：
    C-071 本信号档在详情区 / C-133 本信号用例数实时提示 / C-142 全局默认档 / C-143 用例数上限 /
    C-144 档位算法说明 / C-145 按逻辑类型整批设档 / C-146 每类一条例子表达式 /
    C-147 单点档最高优先级 / C-148 三层同一口径 / C-149 切信号回显不重算 / C-150 生效来源 +
    两行高亮 / C-151 动全局清单点档 / C-152 改逻辑类型档整表重算 / C-153 已自定义不冲掉编辑 /
    C-154 只加过反例的按新档重算再补反例 / C-155 5 个下拉收成 1 套 / C-156 三层塌成一个控件 /
    C-245 单点档不存盘。

覆盖度这块直接用真的 `session.CoverageState`（C0-b 已交付 `effective_chain`），
settings 的读写注入成内存 dict —— 既验「按 view_id 分桶存盘」，又不碰真机配置。
"""
import os

import pytest

import ui_harness as H

pytest.importorskip("PySide6")

from PySide6 import QtWidgets                                      # noqa: E402

from dreg_verify import session                                    # noqa: E402
from dreg_verify.ui import names, terms, theme                     # noqa: E402
from dreg_verify.ui import coverage as COV                         # noqa: E402


# ───────────────────────── 最小 fake state ─────────────────────────

class FakeState(object):
    """只实现 coverage.py 用到的成员；`coverage()` 给的是**真** `session.CoverageState`。"""

    def __init__(self, models=None, settings=None):
        self.scope = "topout"
        self._models = list(models or [])
        self.store = dict(settings or {})
        self._cov = {}
        self.touched = []
        self.edits = {}

    def coverage(self, view_id=None):
        vid = view_id or self.scope
        if vid not in self._cov:
            self._cov[vid] = session.CoverageState(
                vid, load=lambda: self.store, save=lambda st: self.store.update(st))
        return self._cov[vid]

    def coverage_touched(self, view_id):
        self.touched.append(view_id)

    def models(self, view_id=None):
        return list(self._models)

    def model_of(self, name, view_id=None):
        low = str(name).lower()
        return next((m for m in self._models if m["name"].lower() == low), None)

    def edit_of(self, name, view_id=None):
        return self.edits.get(str(name).lower())


def _m(name, form="select", n=25):
    return {"name": name, "disp": name, "owner": "wang", "kind": "mux", "status": "ok",
            "issues": [], "form": form, "n_vectors": n}


MODELS = [_m("sig_sel_a", "select", 25), _m("sig_bool_b", "boolean", 12),
          _m("sig_reg_c", "register", 2), _m("sig_gate_d", "gated", 30)]


@pytest.fixture()
def ctl():
    H.app()
    st = FakeState(MODELS)
    host = QtWidgets.QWidget()
    host.setObjectName("cov_host")
    host.resize(1200, 700)
    lay = QtWidgets.QVBoxLayout(host)
    w = COV.CoverageControl(st, host)
    lay.addWidget(w)
    lay.addStretch(1)
    host.show()
    H.app().processEvents()
    w.set_current("sig_sel_a")
    w.open_popover()
    H.app().processEvents()
    w._host = host
    yield w
    host.close()
    host.deleteLater()


def _catch(sig):
    got = []
    sig.connect(lambda *a: got.append(a if len(a) != 1 else a[0]))
    return got


# ───────────────────────── C-142 / C-143 存盘 ─────────────────────────

def test_c142_c143_global_and_maxt_persist_per_view(ctl):
    st = ctl.state()
    g = H.find(ctl._host, names.COV_GLOBAL_COMBO)
    assert [g.itemText(i) for i in range(g.count())] == list(session.COV_LABELS)
    assert g.currentText() == session.DEFAULT_COV_LABEL

    got = _catch(ctl.coverageChanged)
    g.setCurrentIndex(g.findData("穷举"))                      # C-142
    H.app().processEvents()
    assert st.coverage().global_label == "穷举"
    assert st.store["cov_topout"] == "穷举"                    # 按 view_id 分桶存盘（I-02）
    assert got[-1] == ("global", "穷举")

    spin = H.find(ctl._host, names.COV_MAXT_SPIN)              # C-143 安全阀
    assert (spin.minimum(), spin.maximum()) == session.MAX_TESTS_RANGE
    assert spin.value() == session.DEFAULT_MAX_TESTS
    spin.setValue(64)
    H.app().processEvents()
    assert st.store["maxt_topout"] == 64
    assert got[-1] == ("max_tests", 64)

    # 换范围 = 另一套键，互不污染
    st.scope = "logic"
    ctl.refresh()
    H.find(ctl._host, names.COV_GLOBAL_COMBO).setCurrentIndex(0)   # 精简
    H.app().processEvents()
    assert st.store["cov_logic"] == "精简" and st.store["cov_topout"] == "穷举"


# ───────────────────────── C-144 帮助 ─────────────────────────

def test_c144_help_text(ctl):
    btn = H.find(ctl._host, names.COV_HELP_BTN)
    assert btn.text() == terms.COV_HELP
    body = H.find(ctl._host, COV.HELP_TEXT_OBJECT_NAME)
    assert not body.isVisible()
    H.click(btn)
    H.app().processEvents()
    assert body.isVisible()
    txt = body.text()
    assert txt == terms.COV_HELP_TEXT
    for word in ("精简", "全面", "穷举", "三层优先级"):
        assert word in txt
    for bad in ("F0", "F1", "F2", "F3", "F4"):                 # 术语红线（I-12）
        assert bad not in txt


# ───────────────────────── C-145 / C-146 / C-071 三层表格 ─────────────────────────

def test_c145_c146_form_rows_with_examples(ctl):
    table = H.find(ctl._host, names.COV_TABLE)
    assert table is ctl.table
    for key in COV.FORM_KEYS:                                  # C-145 四类各一行
        cb = H.find(ctl._host, names.fmt_cov_form_combo(key))
        assert cb.itemText(0) == terms.COV_FOLLOW_GLOBAL
        assert [cb.itemText(i) for i in range(1, cb.count())] == list(session.COV_LABELS)
    # C-146：说明列是例子表达式；层名用 terms 版（无 F 编号）
    assert COV.FORM_EXAMPLES["register"] == "out = d_reg[3:0]"
    assert COV.FORM_EXAMPLES["select"] == "out = sel ? A : B"
    texts = [w.text() for ws in ctl._row_widgets.values() for w in ws]
    assert any(t.startswith("out = sel ? A : B") for t in texts)
    assert any(t == terms.COV_LEVEL_FORM_FMT.format(form="选路") for t in texts)
    for t in texts:
        for bad in ("F0", "F1", "F2", "F3", "F4"):
            assert bad not in t, t
    # 本信号（form=select）那一行标「← 本信号属这类」
    assert ctl._row_widgets["select"][1].text().endswith(terms.COV_THIS_FORM_MARK)
    assert not ctl._row_widgets["boolean"][1].text().endswith(terms.COV_THIS_FORM_MARK)


def test_c071_sig_row_is_in_detail_area(ctl):
    """C-071：本信号覆盖度下拉就在详情区（覆盖度弹层末行，可改档）。"""
    cb = H.find(ctl._host, names.COV_SIG_COMBO)
    assert cb.itemText(0) == terms.COV_FOLLOW_UP
    assert ctl._row_widgets["sig"][0].text() == terms.COV_LEVEL_SIG
    assert ctl._row_widgets["sig"][1].text() == "sig_sel_a"
    assert cb.isEnabled()
    ctl.set_current("")
    assert not H.find(ctl._host, names.COV_SIG_COMBO).isEnabled()


# ───────────────────────── C-147 / C-149 单点档 ─────────────────────────

def test_c147_c149_sig_cov_roundtrip_no_recompute(ctl):
    st = ctl.state()
    cb = H.find(ctl._host, names.COV_SIG_COMBO)
    got = _catch(ctl.coverageChanged)
    cb.setCurrentIndex(cb.findData("exhaustive"))              # C-147 最高优先级
    H.app().processEvents()
    assert st.coverage().sig_cov_of("sig_sel_a") == "exhaustive"
    assert got[-1] == ("sig", "exhaustive")
    assert st.coverage().effective_label("sig_sel_a", MODELS)[0] == "穷举"

    # C-149：切到别的信号回显它自己的档，切回来仍是穷举，且**不触发重算**
    n_touch = len(st.touched)
    ctl.set_current("sig_bool_b")
    assert H.find(ctl._host, names.COV_SIG_COMBO).currentData() == ""
    assert H.find(ctl._host, names.COV_SIG_COMBO).currentText() == terms.COV_FOLLOW_UP
    ctl.set_current("sig_sel_a")
    assert H.find(ctl._host, names.COV_SIG_COMBO).currentData() == "exhaustive"
    assert len(st.touched) == n_touch                          # 回显不重算
    assert len(got) == 1                                       # 回显不发 coverageChanged


def test_c245_sig_cov_not_in_settings(ctl):
    """C-245：单点档（与逻辑类型档）刻意不存盘，否则上次留的单点档会静默盖过全局下拉。"""
    st = ctl.state()
    cb = H.find(ctl._host, names.COV_SIG_COMBO)
    cb.setCurrentIndex(cb.findData("min"))
    H.find(ctl._host, names.fmt_cov_form_combo("select")).setCurrentIndex(1)
    H.app().processEvents()
    assert st.coverage().sig_cov_of("sig_sel_a") == "min"
    assert st.coverage().form_cov.get("select") == "min"
    assert not [k for k in st.store if k.startswith("sig_cov") or k.startswith("form_cov")]
    assert set(st.store) <= {"cov_topout", "maxt_topout"}


# ───────────────────────── C-148 / C-150 生效链 ─────────────────────────

def test_c148_c150_effective_chain_bar_text(ctl):
    st = ctl.state()
    bar = H.find(ctl._host, names.COV_CHAIN_BAR)
    # 都跟随 → 逻辑类型层与全局层两行都高亮（冲突⑧）
    assert bar.text() == terms.COV_CHAIN_FMT.format(
        sig=terms.COV_FOLLOW_UP, form="选路", form_label=terms.COV_FOLLOW_GLOBAL,
        global_label=session.DEFAULT_COV_LABEL)
    assert ctl.active_rows() == {"select", "global"}
    assert H.find(ctl._host, names.COV_TITLE).text() == \
        terms.COV_TITLE_FMT.format(label="全面")
    # C-148：标题栏按钮 = 三层同一口径的生效档（来源里不带 F 编号）
    btn = H.find(ctl._host, names.HDR_COV_BTN)
    assert btn.text() == terms.HDR_COV_FMT.format(label="全面", source=terms.COV_LEVEL_GLOBAL)
    H.find(ctl._host, names.fmt_cov_form_combo("select")).setCurrentIndex(1)   # 选路 = 精简
    H.app().processEvents()
    assert btn.text() == terms.HDR_COV_FMT.format(
        label="精简", source=terms.COV_LEVEL_FORM_FMT.format(form="选路"))
    assert H.find(ctl._host, names.COV_CHAIN_BAR).text() == terms.COV_CHAIN_FMT.format(
        sig=terms.COV_FOLLOW_UP, form="选路", form_label="精简",
        global_label=session.DEFAULT_COV_LABEL)
    # 单点档一锤定音 → 只剩「本信号」一行高亮
    cb = H.find(ctl._host, names.COV_SIG_COMBO)
    cb.setCurrentIndex(cb.findData("exhaustive"))
    H.app().processEvents()
    assert ctl.active_rows() == {"sig"}
    assert st.coverage().effective_chain("sig_sel_a", MODELS)[0]["active"] is True
    assert H.find(ctl._host, names.HDR_COV_BTN).text() == \
        terms.HDR_COV_FMT.format(label="穷举", source=terms.COV_LEVEL_SIG)


# ───────────────────────── C-151 / C-152 / C-153 / C-154 联动 ─────────────────────────

def test_c151_global_change_clears_sig_cov(ctl):
    st = ctl.state()
    cb = H.find(ctl._host, names.COV_SIG_COMBO)
    cb.setCurrentIndex(cb.findData("min"))
    H.app().processEvents()
    assert st.coverage().sig_cov_of("sig_sel_a") == "min"
    g = H.find(ctl._host, names.COV_GLOBAL_COMBO)
    g.setCurrentIndex(g.findData("穷举"))
    H.app().processEvents()
    assert st.coverage().sig_cov_of("sig_sel_a") == ""          # C-151 单点档被清
    assert H.find(ctl._host, names.COV_SIG_COMBO).currentText() == terms.COV_FOLLOW_UP
    assert H.find(ctl._host, names.HDR_COV_BTN).text() == \
        terms.HDR_COV_FMT.format(label="穷举", source=terms.COV_LEVEL_GLOBAL)


def test_c152_c153_c154_recompute_rules(ctl):
    """C-152 改逻辑类型档 → 整表重算；C-153/C-154 的『不冲掉编辑 / 补回反例』判定在
    `state.coverage_touched` 里做，本控件只负责发一次信号、不碰 edits。"""
    st = ctl.state()
    st.edits["sig_sel_a"] = {"cols": [{"name": "T1", "neg": False}, {"name": "T2", "neg": True}]}
    snapshot = {k: list(v["cols"]) for k, v in st.edits.items()}
    st.touched = []
    H.find(ctl._host, names.fmt_cov_form_combo("select")).setCurrentIndex(1)
    H.app().processEvents()
    assert st.touched == ["topout"]                              # C-152 一次重算请求
    H.find(ctl._host, names.COV_GLOBAL_COMBO).setCurrentIndex(0)
    H.app().processEvents()
    assert st.touched == ["topout", "topout"]                    # C-154 切全局也只发信号
    # C-153：控件自己一格编辑都没动
    assert {k: list(v["cols"]) for k, v in st.edits.items()} == snapshot


# ───────────────────────── C-133 用例数 ─────────────────────────

def test_c133_case_count_label(ctl):
    st = ctl.state()
    lab = H.find(ctl._host, names.COV_COUNT_LABEL)
    assert lab.text() == terms.COV_COUNT_FMT.format(n=25)
    st.edits["sig_sel_a"] = {"cols": [{"name": "T1", "neg": False}, {"name": "T2", "neg": True},
                                      {"name": "T3", "neg": True}]}
    ctl.refresh()
    assert lab.text() == terms.COV_COUNT_CUSTOM_FMT.format(n=3, neg=2)
    ctl.set_current("sig_bool_b")
    assert H.find(ctl._host, names.COV_COUNT_LABEL).text() == terms.COV_COUNT_FMT.format(n=12)


# ───────────────────────── C-155 / C-156 一个控件一套模型 ─────────────────────────

def test_c155_c156_single_widget_single_model(ctl):
    st = ctl.state()
    combos = [c for c in ctl.table.findChildren(QtWidgets.QComboBox)]
    assert len(combos) == 6                                      # 1 全局 + 4 逻辑类型 + 1 本信号
    assert len({c.objectName() for c in combos}) == 6
    # C-155：一套模型 —— 三层全写同一个 CoverageState
    cov = st.coverage()
    H.find(ctl._host, names.COV_GLOBAL_COMBO).setCurrentIndex(2)
    H.find(ctl._host, names.fmt_cov_form_combo("boolean")).setCurrentIndex(1)
    sig = H.find(ctl._host, names.COV_SIG_COMBO)
    sig.setCurrentIndex(sig.findData("max"))
    H.app().processEvents()
    assert st.coverage() is cov
    assert (cov.global_label, cov.form_cov.get("boolean"), cov.sig_cov_of("sig_sel_a")) == \
        ("穷举", "min", "max")
    assert len(st._cov) == 1
    # C-156：平时只显示「生效档 + 来源」，点开才见三层与上限
    ctl.close_popover()
    H.app().processEvents()
    assert not ctl.is_open()
    assert not ctl.table.isVisible()
    assert H.find(ctl._host, names.HDR_COV_BTN).isVisible()
    H.click(H.find(ctl._host, names.HDR_COV_BTN))
    H.app().processEvents()
    assert ctl.is_open() and ctl.table.isVisible()
    H.click(H.find(ctl._host, names.COV_CLOSE_BTN))
    H.app().processEvents()
    assert not ctl.is_open()


# ───────────────────────── objectName 全覆盖 + 截图 ─────────────────────────

def test_coverage_object_names_and_shot(ctl):
    for nm in (names.HDR_COV_BTN, names.COV_POPOVER, names.COV_TITLE, names.COV_CHAIN_BAR,
               names.COV_TABLE, names.COV_GLOBAL_COMBO, names.COV_SIG_COMBO,
               names.COV_MAXT_SPIN, names.COV_COUNT_LABEL, names.COV_HELP_BTN,
               names.COV_CLOSE_BTN):
        assert H.find(ctl._host, nm) is not None
    for key in COV.FORM_KEYS:
        assert H.find(ctl._host, names.fmt_cov_form_combo(key)) is not None
    assert ctl.pop.width() == theme.COV_POP_W
    assert os.path.exists(H.shot(ctl._host, "cov_popover_open"))
    H.find(ctl._host, names.COV_HELP_BTN).setChecked(True)
    sig = H.find(ctl._host, names.COV_SIG_COMBO)
    sig.setCurrentIndex(sig.findData("exhaustive"))
    H.app().processEvents()
    assert os.path.exists(H.shot(ctl._host, "cov_popover_help_and_sig_override"))
    ctl.close_popover()
    H.app().processEvents()
    assert os.path.exists(H.shot(ctl, "cov_button_collapsed"))
