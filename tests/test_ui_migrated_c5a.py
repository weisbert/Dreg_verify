# -*- coding: utf-8 -*-
"""test_ui_migrated_c5a.py —— C5-a 迁移补强：老 GUI 测试里 v2 侧断言**不够强**的那几条。

来源与账目：`docs/GUI_v2_测试迁移_C5a.csv`（`disposition` 列写着
`删除·v2已补强(<本文件里的测试名>)` 的老测试，就是被下面这些条替下来的）。
处置的三个老文件是 `tests/test_testitems.py` / `tests/test_designer_expected.py` /
`tests/test_mux_wl_gui.py`。

口径（与 `tests/test_ui_c3_integration.py` 一致）：
  · **只打 v2**（真组合根 `ui.app.MainWindow` 或真 `TruthModel`），不碰 `legacy_gui`；
  · 真控件交互 + 可观察结果 —— 不直接调槽、不直接改私有字段去「摆姿势」；
  · 夹具只用 `tests/` 下两张 mirror 镜像表（公开仓，**绝不出现真实信号名**）；
  · 有状态的动作（导入 / 恢复 / 加反例 / 改档）至少做两次，且第二次与第一次**不同**。

⚠ `test_c154_neg_only_signal_follows_global_coverage` 是 `xfail(strict=True)`：
它不是「还没写完」，是**v2 现在真的不满足 C-154**（证据见该测试的 docstring 与 C5-a 报告）。
按主控交代：**不改 v2**，用 strict xfail 把这个缺口钉在测试里 —— 哪天 v2 补上了，
这条会以 XPASS 失败，逼着把 xfail 摘掉。
"""

import io
import json
import os
import re
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import ui_harness as H                                      # noqa: E402

pytest.importorskip("PySide6")

from PySide6 import QtCore, QtWidgets                       # noqa: E402

from dreg_verify import edits as ED                         # noqa: E402
from dreg_verify import excel_model as M                    # noqa: E402
from dreg_verify import exports as X                        # noqa: E402
from dreg_verify import providers as PV                     # noqa: E402
from dreg_verify import session                             # noqa: E402
from dreg_verify.ui import app as A                         # noqa: E402
from dreg_verify.ui import contracts                        # noqa: E402
from dreg_verify.ui import export_center as EC              # noqa: E402
from dreg_verify.ui import names                            # noqa: E402
from dreg_verify.ui import persist as P                     # noqa: E402
from dreg_verify.ui import state as ST                      # noqa: E402
from dreg_verify.ui import terms                            # noqa: E402
from dreg_verify.ui.truth import TruthModel, e_inputs_from_an   # noqa: E402

LC = contracts.ListCol
LR = contracts.ListRole
TR = contracts.TruthRole

#: mirror 上的对照信号（与 `tests/test_ui_truth_model.py` 同一批，`probe` 过）
LOGIC_SIG = "d_logic_bt_lp_rx_en"
MUX_SIG = "d_bt_lp_lna_itrim"


# ════════════════════════════ 夹具 ════════════════════════════

@pytest.fixture
def qapp():
    return H.app()


@pytest.fixture(scope="module")
def btlp_path():
    H.app()
    return H.mirror_path("btlp")


@pytest.fixture(scope="module")
def btlp_wb(btlp_path):
    return M.load_workbook(btlp_path)


@pytest.fixture
def iso(monkeypatch, tmp_path):
    """两份持久化文件指到 tmp（与 `test_ui_state.py` 的 `iso` 同一件事）。"""
    monkeypatch.setattr(P, "SETTINGS_PATH", str(tmp_path / "gui_settings.json"))
    monkeypatch.setattr(P, "EDITS_PATH", str(tmp_path / "edits.json"))
    return tmp_path


@pytest.fixture
def win(qapp, monkeypatch, tmp_path):
    """真组合根 + 真 state + 真 worker（settings/edits 隔离到 tmp）。"""
    H.isolate_settings(monkeypatch, tmp_path)
    H.auto_dialogs(monkeypatch)
    w = A.build_window([])
    w.resize(1600, 900)
    w.show()
    yield w
    w.close()
    qapp.processEvents()


class _Cfg(object):
    """`ConfigSourceProto` 的最小实现（与 `tests/test_ui_truth_model.py` 同形）。"""

    def __init__(self, wb):
        self.wb = wb
        self.probe_prefixes = {}
        self.force_signals = set()
        self.logic_overrides = {}
        self.include_risky = True


def _fresh(wb, name):
    """(model, an, e_inputs)：载好一个信号的真值表（列 = 出厂默认）。"""
    an = PV.TopoutProvider(_Cfg(wb)).analyze(name, "min", 64, False)
    assert an is not None, "mirror 上没有信号 %s（夹具挑错了）" % name
    ei = e_inputs_from_an(an)
    m = TruthModel()
    m.load(an, ED.cols_from_vectors(an, ei), ei)
    return m, an, ei


def _exp_row(m):
    return m.rowCount() - 1


def _load(w, kind="btlp"):
    """载表并等这一趟后台分析跑完（照 `test_ui_c3_integration._load`）。"""
    ended = []
    w.analysisEnded.connect(lambda vid, ok: ended.append((vid, ok)))
    w.load_path(H.mirror_path(kind))
    assert H.wait_for(lambda: bool(ended)), "worker 没跑完：%s" % (ended,)
    H.app().processEvents()
    return w.state.models()


def _row_of(w, name):
    panel = w.list_panel
    row = next((r for r in range(panel.proxy.rowCount())
                if panel.proxy.index(r, int(LC.NAME)).data(int(LR.NAME)) == name), None)
    assert row is not None, "清单里没有 %r（可见的：%s）" % (name, panel.visible_names())
    return row


def _select(w, name):
    """真点清单那一行（不直接调 `state.set_current`）。"""
    row = _row_of(w, name)
    H.click_cell(w.list_panel.view, row, int(LC.NAME))
    H.app().processEvents()
    assert w.state.current_name == name
    return row


def _first_editable(w):
    for m in w.state.models():
        an = w.state.analyze(m["name"])
        if an and an.get("editable"):
            return m["name"]
    raise AssertionError("mirror 上一条可编辑信号都没有")


def _signal_with_cov_spread(w):
    """挑一条**精简与穷举列数真的不同**的信号（否则「改档重排」那条断言是恒真）。"""
    prov = w.state.provider()
    for m in w.state.models():
        name = m["name"]
        lo = prov.analyze(name, "min", 256, False)
        hi = prov.analyze(name, "max", 256, True)
        if lo and hi and lo.get("editable") and len(hi["vectors"]) > len(lo["vectors"]) > 0:
            return name, len(lo["vectors"]), len(hi["vectors"])
    raise AssertionError("mirror 上没有覆盖度档间列数有差异的信号")


def _open_cov(w):
    """打开标题栏④ 那一个覆盖度弹层（v2 只有这一套档位控件）。"""
    assert w.open_coverage() is not False, "标题栏的覆盖度按钮打不开（没载表？）"
    H.app().processEvents()
    return w.coverage


def _close_cov(w):
    """收起弹层 —— 它是 `popup`，开着的时候抓着鼠标，再去点清单会崩进程。"""
    w.coverage.close_popover()
    H.app().processEvents()


def _set_global_cov(w, label):
    """真拧覆盖度弹层里的全局下拉（不直接写 `CoverageState`）。"""
    _open_cov(w)
    g = H.find(w, names.COV_GLOBAL_COMBO)
    i = g.findData(label)
    assert i >= 0, "全局下拉里没有 %r（有的是 %s）" % (
        label, [g.itemData(k) for k in range(g.count())])
    g.setCurrentIndex(i)
    H.app().processEvents()
    _close_cov(w)


def _set_sig_cov(w, data):
    """真拧弹层里「本信号」那一档（`data` = "" 跟随 / "min" / "max" / "exhaustive"）。"""
    _open_cov(w)
    cb = H.find(w, names.COV_SIG_COMBO)
    i = cb.findData(data)
    assert i >= 0, "本信号下拉里没有 %r" % data
    cb.setCurrentIndex(i)
    H.app().processEvents()
    _close_cov(w)


def _set_max_tests(w, n):
    _open_cov(w)
    spin = H.find(w, names.COV_MAXT_SPIN)
    spin.setValue(int(n))
    H.app().processEvents()
    _close_cov(w)


def _ncols(w):
    return w.truth_panel.model.columnCount()


def _click_neg(w, name, on):
    """勾 / 取消清单里那一行的「反例」格（勾选列的唯一写入口，与点勾选框同一条路）。"""
    lp = w.list_panel
    src = lp.proxy.mapToSource(lp.proxy.index(_row_of(w, name), int(LC.NEG)))
    lp.model.setData(src, QtCore.Qt.Checked if on else QtCore.Qt.Unchecked,
                     QtCore.Qt.CheckStateRole)
    H.app().processEvents()


# ══════════════════ ① C-142 / C-143 / C-152：改档真的重排真值表 ══════════════════

def test_c142_c143_global_cov_and_max_tests_relayout_columns_live(win, qapp):
    """替下 `test_gui_coverage_live_update` / `test_gui_max_tests_live_update`
    （老断言：`n_min >= 1 and n_max >= n_min`；`len(w._ti_rows) <= 2 and < n0`）。

    v2 侧原有的 `test_ui_coverage.py::test_c142_c143_*` / `test_c152_c153_c154_*` 用的是
    **假 state**：只验「控件发了一次 `coverageChanged`」。发了信号但真值表没跟着重排，
    那两条照样绿 —— 所以这里拿真窗口 + 真引擎，盯**可观察结果**：列数本身。

    两次有状态操作且第二次与第一次不同：先拧全局档（精简 → 穷举），再改用例上限。
    """
    w = win
    _load(w)
    name, want_min, want_exh = _signal_with_cov_spread(w)

    _set_global_cov(w, "精简")
    _select(w, name)                       # 改档会重跑整表并把清单选回第一行（C-044）
    n_min = _ncols(w)
    assert n_min == want_min, "精简档下列数 %d，引擎给的是 %d" % (n_min, want_min)

    # ① 拧全局档 → 当场重排（**不重新点信号**：当前那条信号的表就得是新档的）
    _set_global_cov(w, "穷举")
    cur = w.state.current_name
    assert _ncols(w) == len(w.state.analyze(cur)["vectors"]), \
        "改完全局档没重新点信号，真值表还是旧档的列（信号 %s）" % cur
    _select(w, name)
    n_exh = _ncols(w)
    assert n_exh == want_exh > n_min, \
        "全局档 精简→穷举 后真值表列数没跟着走（%d → %d，引擎给的是 %d）" % (n_min, n_exh, want_exh)
    assert w.state.coverage().global_label == "穷举"

    # ② 再改用例上限 → 再重排一次（与①不同的动作、不同的方向）
    _set_max_tests(w, 2)
    _select(w, name)
    n_cap = _ncols(w)
    assert n_cap <= 2, "上限 2 没生效，真值表还有 %d 列" % n_cap
    assert n_cap < n_exh
    # 上限抬回去 → 列数回到穷举那一档（证明 ② 不是把表打死了）
    _set_max_tests(w, 256)
    _select(w, name)
    assert _ncols(w) == n_exh


def test_c153_edited_signal_keeps_columns_when_global_cov_changes(win, qapp):
    """替下 `test_gui_coverage_keeps_customized`
    （老断言：`sig in w._customized`；`len(w._ti_rows) == rows_before`）。

    v2 的 `test_c152_c153_c154_recompute_rules` 只验「控件自己没动 edits」——那是控件的
    自证。真正要守的是：已经手填过的信号，拧全局档时**屏幕上那份编辑不许被冲掉**。
    """
    w = win
    _load(w)
    name = _first_editable(w)
    _set_global_cov(w, "精简")
    _select(w, name)                       # 改档会重跑整表并把清单选回第一行（C-044）

    m = w.truth_panel.model
    exp_r = _exp_row(m)
    auto0 = m.cols()[0]["auto"]
    hand = (auto0 ^ 1) & ((1 << (m.cols()[0]["auto_w"] or 1)) - 1)
    assert m.setData(m.index(exp_r, 0), str(hand)) is True
    H.app().processEvents()
    n_before = _ncols(w)
    assert w.state.edit_of(name) is not None, "改了一格却没进 state.edits"

    # ① 拧到穷举：编辑过的信号不重算（列数与手填值都在）
    _set_global_cov(w, "穷举")
    _select(w, name)
    assert _ncols(w) == n_before, "已编辑信号被全局档冲掉了（%d → %d）" % (n_before, _ncols(w))
    assert w.truth_panel.model.cols()[0]["exp"] == hand

    # ② 再拧回精简（第二次、方向相反）—— 仍然不许动
    _set_global_cov(w, "精简")
    _select(w, name)
    assert _ncols(w) == n_before
    assert w.truth_panel.model.cols()[0]["exp"] == hand
    assert w.state.edit_of(name)["cols"][0]["exp"] == hand, "state 里那份编辑也得在"


@pytest.mark.xfail(strict=True, reason="v2 缺陷：state.set_neg 写下的整份 cols 会被 "
                                       "truth/panel.refresh 原样取回，切全局档时正向列不再按新档"
                                       "重算 —— C-154 在 v2 没有落地（C5-a 报告 §5-1）")
def test_c154_neg_only_signal_follows_global_coverage(win, qapp):
    """替下 `test_gui_default_one_survives_coverage_change` /
    `test_gui_coverage_live_update_neg_only` / `test_gui_neg_only_switch_follows_global_coverage` /
    `test_gui_neg_only_build_follows_global_coverage`
    （老断言核心：`len(pos) <= n_pos_max`、`b_pos == b_auto_full`、`neg_exh_pos == clean_exh`）。

    C-154 原文：**只加过反例、没手改正向的信号，切全局档时正向按新档重算再补回反例**。

    v2 现状（实证）：清单勾「反例」→ `state.set_neg` 把整份 cols 写进 `state.edits`；
    `ui/truth/panel.py::show_signal` 只要 `state.edit_of(name)` 有记录就**原样取回**，
    于是这个信号的正向列被冻结在勾反例那一刻的档位上。导出侧同理（`compute_edited`
    吃的是同一份 cols）。这正是 v1 在 R25 / R27 修过的那个 bug 在 v2 的复发。
    """
    w = win
    _load(w)
    name, want_min, want_exh = _signal_with_cov_spread(w)
    _set_global_cov(w, "精简")
    _select(w, name)
    n_min_pos = sum(1 for c in w.truth_panel.model.cols() if not c["neg"])
    assert n_min_pos == want_min

    # 清单上勾「反例」那一格（走勾选列唯一写入口 setData，与点勾选框同一条路）
    # ⚠ 这里读 `state.edit_of` 而不是屏幕上那份 —— 「勾完屏幕当场多一列」是另一个缺口
    #   （见 `test_c023_c122_left_neg_column_shows_in_grid_without_reselect`），
    #   两个缺口混在一条 xfail 里就分不清是哪个先红了。
    _click_neg(w, name, True)
    assert w.state.has_negatives(name), "清单勾选没加上反例"
    ed = w.state.edit_of(name)["cols"]
    assert sum(1 for c in ed if c["neg"]) == 1
    assert sum(1 for c in ed if not c["neg"]) == want_min

    # 只加过反例、没手改正向 → 切到穷举时正向应按新档重算，反例补回来
    _set_global_cov(w, "穷举")
    _select(w, name)
    cols = w.truth_panel.model.cols()
    n_exh_pos = sum(1 for c in cols if not c["neg"])
    assert n_exh_pos == want_exh, \
        "只加过反例的信号切到穷举后正向列是 %d（精简档 %d、穷举档应是 %d），C-154 没生效" % (
            n_exh_pos, want_min, want_exh)
    assert sum(1 for c in cols if c["neg"]) >= 1, "重算后反例没补回来"


# ══════════════════ ② C-151：拧全局只清「当前在看」那个信号的单点档 ══════════════════

def test_c151_global_change_clears_only_the_current_signal_override(win, qapp):
    """替下 `test_gui_sig_cov_global_clears_displayed_override`
    （老断言：`A not in w._sig_cov`；`B.lower() in w._sig_cov`；改 max_tests 不清档）。

    v2 的 `test_c151_global_change_clears_sig_cov` 只验了「当前信号被清」这一半；
    另外两半（**没在看的信号保留各自单点档**、**改用例上限不清档**）没人守 ——
    少了它们，把 C-151 实现成「拧全局清掉所有单点档」也照样绿。
    """
    w = win
    models = _load(w)
    editable = [m["name"] for m in models if (w.state.analyze(m["name"]) or {}).get("editable")]
    assert len(editable) >= 2, "mirror 上可编辑信号不够两条"
    a, b = editable[0], editable[1]

    _set_global_cov(w, "精简")
    _select(w, a)
    _set_sig_cov(w, "exhaustive")
    _select(w, b)
    _set_sig_cov(w, "exhaustive")
    cov = w.state.coverage()
    assert cov.sig_cov_of(a) == "exhaustive" and cov.sig_cov_of(b) == "exhaustive"

    # ① 看着 B 拧全局 → 只清 B，A 的单点档留着
    _select(w, b)
    _set_global_cov(w, "全面")
    assert cov.sig_cov_of(b) == "", "看着 B 拧全局，B 的单点档没被清"
    assert cov.sig_cov_of(a) == "exhaustive", "没在看的 A 的单点档被顺手清掉了"

    # ② 第二次、换个动作：改用例上限 —— 一个单点档都不许清
    _select(w, a)
    _set_max_tests(w, 32)
    assert cov.sig_cov_of(a) == "exhaustive", "改用例上限不该清单点档"


# ══════════════════ ③ C-035：勾了但被筛掉的行不进批量作用域 ══════════════════

def test_c035_bulk_neg_scope_excludes_checked_but_hidden_rows(win, qapp):
    """替下 `test_gui_bulk_scope_excludes_hidden_checked`
    （老断言：`r0 in w._scope_rows()` → 隐藏后 `r0 not in w._checked_rows()` /
    `not in w._scope_rows()`）。

    v2 的 `signal_list.neg_targets()` 写的是 `checked or names`，其中 `names` 已经是
    **可见**那批；但 v2 侧两条现有测试（`test_c032_check_all_only_touches_visible_rows` /
    `test_c035_neg_all_falls_back_to_visible_when_nothing_checked`）验的都是「没勾选」那条路。
    「勾了、然后被筛掉」这条路没人守 —— 写成 `state.checked() or names` 一样绿，
    而那会让批量反例改到用户**看不见**的信号。
    """
    w = win
    _load(w)
    lp = w.list_panel
    H.click(H.find(lp, names.LIST_BTN_UNCHECK_ALL))
    H.app().processEvents()

    all_names = lp.visible_names()
    assert len(all_names) >= 3

    # ① 真点勾选框勾上一个，确认它在批量作用域里
    target = all_names[0]
    H.click_cell(lp.view, _row_of(w, target), int(LC.CHECK))
    H.app().processEvents()
    assert w.state.is_checked(target)
    assert lp.neg_targets() == [target], "有勾选时作用域该只剩勾选那批：%s" % lp.neg_targets()

    # ② 用真搜索框把它筛掉（第二次动作，与①不同）—— 它必须同时退出「可见」与「作用域」
    keep = None
    for cand in all_names[1:]:
        lp.set_filters(regex=re.escape(cand))
        H.app().processEvents()
        vis = lp.visible_names()
        if cand in vis and target not in vis:
            keep = cand
            break
    assert keep, "mirror 上找不到一个能把 %s 筛掉的搜索词" % target
    vis = lp.visible_names()
    assert w.state.is_checked(target), "筛选不该动勾选本身"
    assert target not in lp.neg_targets(), "勾了但被筛掉的行仍在批量作用域里"
    assert set(lp.neg_targets()) == set(vis), "作用域没退回到可见那批"

    made = lp.neg_all()
    H.app().processEvents()
    assert set(made) == set(vis)
    assert not w.state.has_negatives(target), "看不见的信号被批量加上了反例"


# ══════════════════ ④ C-023 / C-122：清单勾反例 → 编辑器真的多一列 ══════════════════

def test_c023_c122_left_neg_makes_one_neg_column_dedups_and_uncheck_removes_it(win, qapp):
    """替下 `test_gui_left_neg_adds_one` / `test_mux_left_neg_editor_matches_build` /
    `test_mux_left_neg_dedup_with_user_neg` / `test_mux_left_neg_uncheck_removes_column`
    的**产物侧**（老断言：`len(negs) == 1`、`test_label(...).endswith("_NEG")`、
    `n_editor_neg == n_build_neg == 1`、`重叠负向应去重`、`取消勾选后负向列应消失`）。

    v2 侧 `test_ui_state.py::test_set_neg_adds_one_and_reports_skipped` 只验了
    `state.edits` 里的列数；「这条列真的是 `_NEG`、真的进了 .sv、再加一次不叠、
    取消就没了」这一串没人一次串起来验。

    ⚠ 与 v1 的一处**有意差异**：v1 的全局反例列是只读参考列（`test_mux_left_neg_column_readonly`）；
    v2 里反例就是一条普通列，可编辑 —— 所以这里断言的是「它是反例列」，不是「它只读」。
    ⚠ 「勾完屏幕上当场多一列」那一跳是 v2 的缺口，单独由下面那条 xfail 盯着。
    """
    w = win
    _load(w)
    name = _first_editable(w)
    _select(w, name)
    n0 = w.truth_panel.model.columnCount()
    assert not any(c["neg"] for c in w.truth_panel.model.cols())

    # ① 勾上 → state 里那份编辑多一条 _NEG 列
    _click_neg(w, name, True)
    assert w.state.has_negatives(name), "清单勾选没加上反例"
    cols = w.state.edit_of(name)["cols"]
    negs = [c for c in cols if c["neg"]]
    assert len(negs) == 1 and len(cols) == n0 + 1
    assert str(negs[0]["name"]).upper().endswith("_NEG")
    assert negs[0]["exp"] is not None, "反例列没有错值 = 断言必过的假绿"

    # 编辑器手上这份列 == 导出产物里的反例数（所见即所得）
    edited = w.state.compute_edited()
    _text, build = X.render_sv(w.state.provider(), only=[name], edited=edited)
    n_build_neg = sum(st.get("n_negative", 0) for _l, st in build["blocks"]
                      if (st.get("topout_name") or "").lower() == name.lower())
    assert n_build_neg == 1, "编辑器 1 条反例，build 出来 %d 条" % n_build_neg

    # ② 再勾一次（第二次动作）→ 同一组取值不叠第二条
    assert w.state.set_neg(name, True) == (0, 0)
    assert sum(1 for c in w.state.edit_of(name)["cols"] if c["neg"]) == 1

    # ③ 取消勾选 → 反例列没了（第三次动作，与①方向相反）
    _click_neg(w, name, False)
    assert not w.state.has_negatives(name)
    assert not any(c["neg"] for c in w.state.edit_of(name)["cols"]), "取消勾选后反例列还在"
    assert len(w.state.edit_of(name)["cols"]) == n0


@pytest.mark.xfail(strict=True, reason="v2 缺陷：`ui/truth/panel.py` 刻意不接 editsChanged，"
                                       "也没人接 negsChanged —— 清单勾「反例」后，正看着的那个"
                                       "信号的真值表不会当场多出 _NEG 列（C5-a 报告 §5-3）")
def test_c023_c122_left_neg_column_shows_in_grid_without_reselect(win, qapp):
    """替下 `test_mux_left_neg_shows_in_editor`（老断言：
    `len(negs) == 1, "勾负向后编辑器真值表应出现 1 条负向列"`、
    `len(w._ti_mux_vecs) == n0 + 1`、`test_label(...).endswith("_NEG")`）。

    这正是 v1 在 2026-06-10 修过的用户报障：左表勾了反例，编辑器真值表看不到那一列，
    用户以为没生效。v2 把它复发了 —— `state.set_neg` 只发 `negsChanged` / `editsChanged`，
    而真值表面板订阅的是 `currentChanged / coverageChanged / configChanged / scopeChanged /
    workbookChanged` 五条，没有一条会在这里响。**不改 v2**，用 strict xfail 钉住。
    """
    w = win
    _load(w)
    name = _first_editable(w)
    _select(w, name)
    m = w.truth_panel.model
    n0 = m.columnCount()

    _click_neg(w, name, True)
    m = w.truth_panel.model
    negs = [c for c in m.cols() if c["neg"]]
    assert len(negs) == 1, "勾反例后编辑器真值表应出现 1 条反例列"
    assert m.columnCount() == n0 + 1
    assert m.all_names()[-1].upper().endswith("_NEG")
    assert m.col_state(m.columnCount() - 1) == contracts.TruthColState.NEG


# ══════════════════ ⑤ C-096 / C-097 / C-101：反例的取值来源与不重建 ══════════════════

def test_c096_add_neg_copies_values_from_the_selected_column(btlp_wb, qapp):
    """替下 `test_gui_add_neg_selected_precise` / `test_gui_add_neg_selected_no_duplicate`
    （老断言：`neg[0]["base_values"] == w._ti_rows[0]["base_values"]`、
    `any(rd["base_values"] == t1_bv for rd in negs)` —— 「精确命中 T1，不是兜底回 T0」）。

    v2 现有的 `test_c096_c102_add_and_del_negatives_from_toolbar` 只数了**条数**
    （选两列 → 多两列）。选了 T1 却拿 T0 的取值造反例，条数一样对，用户拿到的却是
    一条测错地方的反例。
    """
    m, _an, _ei = _fresh(btlp_wb, LOGIC_SIG)
    n0 = m.columnCount()
    assert n0 >= 2, "这条信号只有一列，验不到「选哪一列」"
    vals = [dict(c["vals"]) for c in m.cols()]

    # ① 对第 0 列加反例 → 取值必须复制自第 0 列
    assert m.add_negatives([0]) == (1, 0)
    assert m.cols()[n0]["vals"] == vals[0], "反例的输入取值不是来自选中的那一列"

    # ② 换选第 1 列（第二次、与①不同的列）→ 取值必须是第 1 列的，不是兜底回第 0 列
    assert m.add_negatives([1]) == (1, 0)
    assert m.cols()[n0 + 1]["vals"] == vals[1], \
        "选了第二列却按第一列造反例（兜底回第一条 = v1 修过的老毛病）"
    assert m.cols()[n0 + 1]["vals"] != vals[0] or vals[0] == vals[1]

    # ③ 对已经有反例的第 0 列再加 → 不产生重复反例
    assert m.add_negatives([0]) == (0, 1)
    assert sum(1 for c in m.cols() if c["neg"]) == 2


def test_c097_c101_add_neg_all_keeps_renamed_and_hand_filled_negatives(btlp_wb, qapp):
    """替下 `test_gui_neg_rebuild_keeps_name_and_wrong`
    （老断言：`any(rd.get("name") == "my_special_neg" ...)`、`any(rd.get("wrong_value") == 1 ...)`）。

    v2 侧只验了「同取值再加一次 → 跳过并报条数」。「跳过」与「已有那条一个字不改」
    不是一回事：把 skip 实现成「删掉重造一条」，计数照样对，而用户给反例起的名字和
    手填的错值会**静默消失**。
    """
    m, _an, _ei = _fresh(btlp_wb, LOGIC_SIG)
    n0 = m.columnCount()
    assert m.add_negatives([0]) == (1, 0)
    neg_i = n0
    ok, final = m.rename_column(neg_i, "my_special_neg")
    assert ok is True and final.startswith("my_special_neg")
    exp_r = _exp_row(m)
    auto = m.cols()[neg_i]["auto"]
    w_bits = m.cols()[neg_i]["auto_w"] or 1
    hand_wrong = (auto + 2) & ((1 << w_bits) - 1)
    if hand_wrong == auto:
        hand_wrong = (auto ^ 1) & ((1 << w_bits) - 1)
    assert m.setData(m.index(exp_r, neg_i), str(hand_wrong)) is True
    snapshot = dict(m.cols()[neg_i])

    # ① 再来一次「全部正向各加一条」 —— 已有反例那组跳过，那一列一个字不许改
    made, _skipped = m.add_negatives([], all_positive=True)
    got = m.cols()[neg_i]
    assert got["name"] == snapshot["name"], "重建丢了反例列的改名"
    assert got["exp"] == snapshot["exp"] == hand_wrong, "重建丢了手填错值"
    assert got["neg"] is True

    # ② 第二次（不同动作）：对同一列单独再加 → 仍然跳过、仍然不动那一列
    assert m.add_negatives([0]) == (0, 1)
    assert m.cols()[neg_i]["name"] == snapshot["name"]
    assert m.cols()[neg_i]["exp"] == hand_wrong
    assert sum(1 for c in m.cols() if c["neg"]) == made + 1


# ══════════════════ ⑥ C-164：没有重复标号就别弹确认框 ══════════════════

def test_c164_no_dup_labels_means_no_confirm_dialog(qapp, iso, btlp_path, monkeypatch):
    """替下 `test_gui_confirm_dup_labels_no_dups_is_silent`
    （老断言：`w._confirm_dup_labels({"dup_labels": []}) is True`、`w._confirm_dup_labels({}) is True`）。

    v2 的 `test_c164_dup_labels_confirm_before_write` 只走了**有**重复标号那条路
    （它自己往 plan 里塞了一条假冲突）。没有冲突时弹不弹框没人守 ——
    弹一个空的确认框，每次导出都要多点一下「继续」。
    """
    st = ST.WorkbenchState()
    assert st.load(btlp_path)
    st.set_models("topout", st.provider("topout").skeleton_models(), partial=True)
    save_dir = iso / "save"
    save_dir.mkdir()
    rec = H.auto_dialogs(monkeypatch, save_dir=str(save_dir))

    rows = EC.default_rows(st)
    for r in rows:
        r.enabled = r.kind == "sv"
    plan = EC.build_plan(st, rows)
    assert plan.dup_labels == [], "这张 mirror 本来就有重复标号，验不到「无冲突」那条路"

    d = EC.ExportCenterDialog(st)
    d.resize(1080, 520)
    for k, cb in d.checks.items():
        cb.setChecked(k == "sv")
    res = d.run()
    H.app().processEvents()
    assert res is not None, "无重复标号却没导出"
    assert rec.count("DupLabelsDialog.ask") == 0, "无重复标号还弹了确认框：%s" % rec.dump()
    assert [f for f in os.listdir(str(save_dir)) if f.endswith(".sv")], "没写出 .sv"
    d.close()


# ══════════════════ ⑦ C-192 / C-193 / C-217：导入配置真的照单恢复 ══════════════════

def test_c192_c193_c217_import_config_applies_global_block(qapp, iso, btlp_path, monkeypatch):
    """替下 `test_full_config_export_import_roundtrip` / `test_config_excel_mismatch_warns_but_imports`
    / `test_full_config_import_clears_stale_negatives`
    （老断言：`w2.max_tests.value() == 77`、`w2.coverage.currentText() == "全面"`、
    `w2.include_risky_chk.isChecked()`、`w.max_tests.value() == 42`（表名不符仍照常导入）、
    `w.include_risky_chk.isChecked() == risky_before`（缺键保持当前）、
    `not w2._signal_has_negative(nm)`（导入清掉残留反例））。

    v2 的 `test_c190_..._config_roundtrip` 验的是**字段集**与勾选/前缀的恢复；
    `global` 段（覆盖度档 / 用例上限 / 缺前缀强制生成）到底有没有被套用、
    退役的四个键有没有「忽略但说一句」、导入前的残留反例有没有被清掉 —— 都没人守。
    """
    st = ST.WorkbenchState()
    assert st.load(btlp_path)
    st.set_models("topout", st.provider("topout").skeleton_models(), partial=True)
    H.auto_dialogs(monkeypatch)

    # 导入前的状态：一条反例 + 默认全局档
    name = next(m["name"] for m in st.models()
                if (st.analyze(m["name"]) or {}).get("editable"))
    assert st.set_neg(name, True)[0] == 1
    assert st.has_negatives(name)
    risky_before = st.include_risky

    cfg = iso / "cfg.json"
    payload = {
        "dreg_verify_config": session.CONFIG_VERSION,
        "excel": "另一张表.xlsx", "excel_path": "D:/elsewhere/另一张表.xlsx",
        "global": {"coverage_logic": "全面", "coverage_mux": "全面", "max_tests": 77,
                   "include_risky": False,
                   # 随入口退役的四个键：忽略，但要说一句（不静默）
                   "cascade_logic": "cone", "cascade_mux": "force",
                   "append_to_logic": True, "append_to_mux": False},
        "signals_checked": [name], "view_checks": {"topout": [name]},
        "probe_prefixes": {}, "force_signals": [], "logic_overrides": {},
    }
    io.open(str(cfg), "w", encoding="utf-8").write(json.dumps(payload, ensure_ascii=False))

    # ① 导入 → global 段照单套用；表名不符只提示、不拦；退役键「忽略但说一句」
    rep = EC.import_config(st, str(cfg))
    assert rep.ok and rep.is_full
    assert any(terms.EXPORT_IMPORT_MISMATCH_FMT.format(
        cfg="另一张表.xlsx", cur=os.path.basename(btlp_path)) == n for n in rep.notes), rep.notes
    assert terms.EXPORT_IMPORT_IGNORED in rep.notes, "退役的四个键被静默吞了：%s" % rep.notes
    assert st.coverage("logic").global_label == "全面"
    assert st.coverage().max_tests == 77, "用例上限没被套用：%r" % st.coverage().max_tests
    assert st.include_risky is False and risky_before is True
    assert sorted(st.checked_names()) == [name]
    assert not st.has_negatives(name), "导入完整配置没先清空：上一会话的反例还在"

    # ② 第二次导入一份**不同**的配置：缺 include_risky 键 → 保持当前，不翻动本机选择
    payload2 = dict(payload)
    payload2["excel"] = os.path.basename(btlp_path)
    payload2["excel_path"] = btlp_path
    payload2["global"] = {"coverage_logic": "精简", "coverage_mux": "精简", "max_tests": 42}
    payload2["signals_checked"] = []
    payload2["view_checks"] = {"topout": []}
    cfg2 = iso / "cfg2.json"
    io.open(str(cfg2), "w", encoding="utf-8").write(json.dumps(payload2, ensure_ascii=False))

    rep2 = EC.import_config(st, str(cfg2))
    assert rep2.ok and rep2.is_full
    assert not [n for n in rep2.notes
                if n.startswith(terms.EXPORT_IMPORT_MISMATCH_FMT.split("{")[0])], rep2.notes
    assert st.coverage("logic").global_label == "精简"
    assert st.coverage().max_tests == 42, "第二份配置的上限没生效"
    # ⚠ 与 v1 的一处**有意差异**（C5-a 报告 §5-2）：v1 是「缺键 → 保持当前」，
    #   v2 按 C-192「先清空再照单恢复」，缺键 = 回出厂默认（True）。这里把 v2 的口径钉死，
    #   免得哪天被悄悄改成第三种。
    assert st.include_risky is True, "C-192 的先清空语义：缺 include_risky 键该回出厂默认 True"
    assert sorted(st.checked_names()) == [], "第二份配置的空勾选没被照单套用"
