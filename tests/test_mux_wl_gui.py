# -*- coding: utf-8 -*-
"""WL_RFTRX 结构 mux 页的 GUI 层测试（2026-06-03 第十四轮·窗口 C）。

WL 与 LPBT 的 GUI 差异（只测 GUI 层；后端行为由 test_mux_wl.py 把关）：
  ① 控制三来源 → 『输入信号』表角色文案（寄存器直出 / logic 行 / mux 级联）
  ② 多控制 → 头部 case({c1,c2}) + 输入表多控制行 + 上游配方行
  ③ top_out 全 0 → 左表状态列="裸名探针"（信息蓝，照常生成；区别于"需前缀"橙、"未解析"红），真值表照常渲染
  ④ apply_filter 对 mux 组不再 AttributeError（搜索框输入文本不崩）

LPBT 的 GUI 行为由 test_mux.py 的 gui_win 用例把关（一个都不能动）。
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import fixtures                                       # noqa: E402
from dreg_verify import excel_model                   # noqa: E402


@pytest.fixture(scope="module")
def gui_app():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PySide6")
    from PySide6 import QtWidgets
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    yield app


@pytest.fixture()
def wl_win(gui_app, tmp_path):
    """加载 WL 表（探针前缀未配 → 状态列应为"需探针前缀"）。"""
    from dreg_verify import gui as G
    excel = tmp_path / "wl_gui.xlsx"
    fixtures.build_wl_workbook(str(excel))
    w = G.MainWindow()
    w.path_edit.setText(str(excel))
    w.on_load()
    yield w
    w.close()


@pytest.fixture()
def lpbt_win(gui_app, tmp_path):
    """加载 LPBT 表（含 mux）——回归：现有 GUI 行为不变。"""
    from dreg_verify import gui as G
    excel = tmp_path / "lpbt_gui.xlsx"
    fixtures.build_workbook(str(excel), with_mux=True)
    w = G.MainWindow()
    w.path_edit.setText(str(excel))
    w.on_load()
    yield w
    w.close()


def _mux_idx(w, out_base):
    """按 out_base 找到该 mux 组在 signals 里的行号。"""
    for i, s in enumerate(w.signals):
        if isinstance(s, excel_model.MuxGroup) and s.out_base == out_base:
            return i
    raise AssertionError("找不到 mux 组 %s（现有: %s）"
                         % (out_base, [s.out_base for s in w.signals]))


def _mux_grp(w, out_base):
    return w.signals[_mux_idx(w, out_base)]


# ───────────── ① 左表：5 个 mux 组 + 状态="需探针前缀" ─────────────
def test_wl_gui_all_mux_groups_present(wl_win):
    """WL 表 5 个 mux 组都混排进信号表，type=mux。"""
    w = wl_win
    mux_groups = [s for s in w.signals if isinstance(s, excel_model.MuxGroup)]
    assert len(mux_groups) == 5
    bases = {s.out_base for s in mux_groups}
    assert bases == {"d_wl_rf_lna_gain", "d_wl_rf_bwctrl", "d_wl_rf_tx_bwctrl",
                     "d_wl_rf_tx_rc_code", "d_wl_rf_dpd_path"}


def test_wl_gui_status_bare_probe(wl_win):
    """top_out=0 + 没配前缀 → 状态='bare-probe'（裸名探针已生成，不是 'unresolved' 也不是阻断）。"""
    w = wl_win
    for base in ("d_wl_rf_lna_gain", "d_wl_rf_bwctrl", "d_wl_rf_tx_bwctrl",
                 "d_wl_rf_tx_rc_code", "d_wl_rf_dpd_path"):
        i = _mux_idx(w, base)
        st = w._analysis[i]["status"]
        assert st == "bare-probe", "%s 状态应为 bare-probe，实际 %s" % (base, st)
        assert w._analysis[i]["blocking"] is False        # 不挡生成


def test_wl_gui_status_column_info_blue_not_red(wl_win):
    """状态列文本='输出裸名·已生成'、信息色蓝（区别于未解析的红、缺前缀的橙），tooltip 含 error 全文。"""
    from PySide6 import QtGui
    from dreg_verify import gui as G
    w = wl_win
    i = _mux_idx(w, "d_wl_rf_lna_gain")
    # 找到该信号在表格里的行（排序后行号 ≠ signals 下标）
    row = next(r for r in range(w.table.rowCount()) if w._idx_of_row(r) == i)
    cell = w.table.item(row, G.COL_STATUS)
    assert cell.text() == G.STATUS_LABEL["bare-probe"]    # 标签文本以常量为准（避免硬编码漂移）
    assert cell.foreground().color() == QtGui.QColor("#2a7ab0")    # 信息蓝，不是红/橙
    assert "scan_rtl" in cell.toolTip()                            # error 全文带上


def test_wl_gui_status_prefix_configured_clean(wl_win):
    """配好探针前缀后状态转 clean（探针前缀机制对 mux 生效）。"""
    w = wl_win
    pfx = "U_WL_DREG.U_RF_MUX"
    w._probe_prefixes = {
        "d_wl_rf_lna_gain": pfx, "d_wl_rf_bwctrl": pfx, "d_wl_rf_tx_bwctrl": pfx,
        "d_wl_rf_tx_rc_code": pfx, "d_wl_rf_dpd_path": pfx,
        "d_wl_rf_lna_gain_to_logic": pfx,
    }
    w._reanalyze_all()
    i = _mux_idx(w, "d_wl_rf_lna_gain")
    assert w._analysis[i]["status"] == "clean"


# ───────────── ② 输入信号表：控制三来源角色文案 ─────────────
def test_wl_gui_reg_ctrl_role(wl_win):
    """组1（寄存器直出控制）：输入表非空，控制行角色含"寄存器直出"。"""
    w = wl_win
    grp = _mux_grp(w, "d_wl_rf_lna_gain")
    w._load_test_items(grp)
    assert w.ti_inputs.rowCount() > 0
    roles = [w.ti_inputs.item(r, 2).text() for r in range(w.ti_inputs.rowCount())]
    assert any("寄存器直出" in x for x in roles), roles
    # 字母列显示 Excel 控制列字母（B）
    letters = [w.ti_inputs.item(r, 0).text() for r in range(w.ti_inputs.rowCount())]
    assert "B" in letters, letters
    # RO 线控数据角色标 force
    assert any("线控" in x for x in roles), roles


def test_wl_gui_multi_ctrl_rows(wl_win):
    """组4（多控制 {lut_en,bwctrl}+级联）：输入表含 2 个控制行 + 上游配方行。"""
    w = wl_win
    grp = _mux_grp(w, "d_wl_rf_tx_rc_code")
    w._load_test_items(grp)
    roles = [w.ti_inputs.item(r, 2).text() for r in range(w.ti_inputs.rowCount())]
    # 控制行 ≥ 2（B=lut_en 寄存器直出 + C=bwctrl mux 级联）
    n_ctrl = sum(1 for x in roles if x.startswith("控制"))
    assert n_ctrl >= 2, roles
    # 级联控制带上游配方行
    assert any("上游mux配方" in x for x in roles), roles
    # 经上游 mux 驱动的角色
    assert any("经上游mux" in x for x in roles), roles


def test_wl_gui_header_multi_ctrl_desc(wl_win):
    """组4 头部 case 描述用多控制拼接 {c1,c2}，覆盖度文案为通用形态。"""
    w = wl_win
    grp = _mux_grp(w, "d_wl_rf_tx_rc_code")
    w._load_test_items(grp)
    txt = w.ti_header.text()
    assert "{d_wl_rf_rc_code_lut_en,d_wl_rf_bwctrl}" in txt, txt
    # 通用形态穷举文案
    w.coverage.setCurrentText("穷举")
    w._load_test_items(grp)
    assert "无另一条控制路径概念" in w.ti_header.text()
    w.coverage.setCurrentText("精简")


# ───────────── ③ 级联组真值表照常渲染 ─────────────
def test_wl_gui_cascade_truth_table_rendered(wl_win):
    """组3（mux 级联控制）：真值表渲染出来（不再是"无法生成测试"）。"""
    w = wl_win
    grp = _mux_grp(w, "d_wl_rf_tx_bwctrl")
    w._load_test_items(grp)
    assert w._ti_mux_sig is grp
    assert w.ti_table.columnCount() > 0, "级联组真值表应渲染出向量列"
    assert w.ti_table.rowCount() > 0
    assert "无法生成测试" not in w.ti_header.text()
    assert "互异值分配失败" not in w.ti_header.text()


def test_wl_gui_lna_gain_truth_table(wl_win):
    """组1 真值表也渲染（寄存器直出控制 + RO 线控/RW 数据）。"""
    w = wl_win
    grp = _mux_grp(w, "d_wl_rf_lna_gain")
    w._load_test_items(grp)
    assert w.ti_table.columnCount() > 0
    assert w.ti_table.rowCount() > 0


# ───────────── ④ 搜索框对 mux 组不崩 ─────────────
def test_wl_gui_filter_text_no_crash(wl_win):
    """apply_filter 守卫：搜索框输入文本对 mux 组不再 AttributeError。"""
    w = wl_win
    for pat in ("lna", "bwctrl", "tx_rc", "d_wl_rf"):
        w.name_edit.setText(pat)
        w.apply_filter()          # 之前会因 MuxGroup 没有 .inputs 而 AttributeError
    # 搜输出名能命中对应组
    w.name_edit.setText("lna_gain")
    w.apply_filter()
    i = _mux_idx(w, "d_wl_rf_lna_gain")
    row = next(r for r in range(w.table.rowCount()) if w._idx_of_row(r) == i)
    assert not w.table.isRowHidden(row)
    w.name_edit.setText("")
    w.apply_filter()


def test_wl_gui_filter_by_input_name(wl_win):
    """按输入信号名搜索（mux 组从 _analysis 的 inputs 行取名）——线控数据名能命中其 mux 组。"""
    w = wl_win
    w.name_edit.setText("linectrl_lna_gain")
    w.apply_filter()
    i = _mux_idx(w, "d_wl_rf_lna_gain")
    row = next(r for r in range(w.table.rowCount()) if w._idx_of_row(r) == i)
    assert not w.table.isRowHidden(row)
    w.name_edit.setText("")
    w.apply_filter()


# ───────────── ⑤ LPBT 回归：现有 GUI 行为不变 ─────────────
def test_lpbt_gui_unchanged(lpbt_win):
    """LPBT 表（含 mux）：信号数/状态/输入表角色与历史一致（test_mux.py 同款断言精简版）。"""
    w = lpbt_win
    assert len(w.signals) == 5
    mux_idx = [i for i, s in enumerate(w.signals)
               if isinstance(s, excel_model.MuxGroup)]
    assert len(mux_idx) == 2
    # LPBT mux 输出 top_out=1 → clean（不需要前缀）
    assert w._analysis[mux_idx[0]]["status"] == "clean"
    # 输入表角色仍是 line/local/数据寄存器（LPBT 形态文案不变）
    grp = w.signals[mux_idx[0]]
    w._load_test_items(grp)
    roles = [w.ti_inputs.item(r, 2).text() for r in range(w.ti_inputs.rowCount())]
    assert any("line路径" in x for x in roles), roles
    assert any("local路径" in x for x in roles), roles
    assert sum(1 for x in roles if "数据寄存器" in x) == 3, roles


def test_lpbt_gui_header_single_ctrl(lpbt_win):
    """LPBT 头部 case 描述用单控制基名（不带 {} 拼接），覆盖度用 line/local 文案。"""
    w = lpbt_win
    grp = next(s for s in w.signals if isinstance(s, excel_model.MuxGroup))
    w._load_test_items(grp)
    txt = w.ti_header.text()
    assert "case(d_logic_bt_lp_lna_agc)" in txt, txt
    assert "另一路径抽测" in txt        # LPBT 双路径文案


# ───────────── ⑥ owner 留空的信号：下拉可筛「（无 owner）」 ─────────────
def test_gui_filter_no_owner(wl_win):
    """Excel owner 列(P/L/AE)留空 → sig.owner=''；下拉出现「（无 owner） ×N」，选中只显示这些。"""
    from dreg_verify import gui as G
    w = wl_win
    for s in (w.signals[0], w.signals[1]):       # 模拟两个信号 owner 列留空
        s.owner = ""
    w._populate_filters()
    n_blank = sum(1 for s in w.signals if not s.owner)
    assert n_blank >= 2
    texts = [w.owner_combo.itemText(k) for k in range(w.owner_combo.count())]
    item = next((t for t in texts if t.startswith(G.NO_OWNER)), None)
    assert item == "%s ×%d" % (G.NO_OWNER, n_blank), texts     # 带计数
    w.owner_combo.setCurrentText(item); w.apply_filter()
    for r in range(w.table.rowCount()):                         # 只有无 owner 的行可见
        s = w._sig_of_row(r)
        assert w.table.isRowHidden(r) == bool(s.owner), s.out_name
    w.owner_combo.setCurrentText("全部 owner"); w.apply_filter()


def test_gui_filter_no_owner_absent_when_all_owned(wl_win):
    """所有信号都有 owner 时，下拉不出现「（无 owner）」项（不打扰）。"""
    from dreg_verify import gui as G
    w = wl_win
    for s in w.signals:
        if not s.owner:
            s.owner = "someone"
    w._populate_filters()
    texts = [w.owner_combo.itemText(k) for k in range(w.owner_combo.count())]
    assert not any(t.startswith(G.NO_OWNER) for t in texts), texts


# ───────────── ⑦ mux 负向勾选跨会话持久化（bug 回归） ─────────────
def test_wl_gui_mux_negatives_persist_across_reload(gui_app, tmp_path, monkeypatch):
    """⭐bug 回归(2026-06-05)：mux 信号的"负向"勾选关 GUI 重开后丢失——表现为用户点了
    『全部加负向』(含 clean 的 logic 与非 clean 的 mux)，重开却只剩 logic 的负向还勾着。
    根因：mux 负向只在勾选框、不进 _edited 存盘，_sync_neg_checks_from_edits 又整个跳过 mux。
    修复：mux 负向存进 _mux_neg 并落盘/恢复。"""
    from PySide6 import QtCore
    from dreg_verify import gui as G
    monkeypatch.setattr(G, "EDITS_PATH", str(tmp_path / "edits.json"))
    excel = tmp_path / "wl.xlsx"
    fixtures.build_wl_workbook(str(excel))
    mux_lower = None

    def _mux_row(w, sig):
        return next(r for r in range(w.table.rowCount())
                    if w.signals[w._idx_of_row(r)] is sig)

    # 窗口1：全部加负向（5 个 mux 组 + 2 个 logic）→ 落盘
    w1 = G.MainWindow(); w1.path_edit.setText(str(excel)); w1.on_load()
    mux_lower = {s.out_name.lower() for s in w1.signals
                 if isinstance(s, excel_model.MuxGroup)}
    w1.on_all_signals_neg(True)
    assert set(w1._mux_neg) == mux_lower            # 落盘前就进了 _mux_neg
    w1.close()

    # 窗口2：重开 → mux 组负向都恢复勾上，生成时(_mux_neg_checked)也读得到
    w2 = G.MainWindow(); w2.path_edit.setText(str(excel)); w2.on_load()
    for s in [x for x in w2.signals if isinstance(x, excel_model.MuxGroup)]:
        cell = w2.table.item(_mux_row(w2, s), G.COL_NEG)
        assert cell.checkState() == QtCore.Qt.Checked, s.out_name
    assert {n.lower() for n in w2._mux_neg_checked()} == mux_lower
    # 取消一个 mux 负向 → 落盘更新
    g0 = next(x for x in w2.signals if isinstance(x, excel_model.MuxGroup))
    w2.table.item(_mux_row(w2, g0), G.COL_NEG).setCheckState(QtCore.Qt.Unchecked)
    assert g0.out_name.lower() not in w2._mux_neg
    w2.close()

    # 窗口3：再重开 → 那个被取消的 mux 不再勾，其余仍勾
    w3 = G.MainWindow(); w3.path_edit.setText(str(excel)); w3.on_load()
    for s in [x for x in w3.signals if isinstance(x, excel_model.MuxGroup)]:
        cell = w3.table.item(_mux_row(w3, s), G.COL_NEG)
        want = QtCore.Qt.Unchecked if s.out_name == g0.out_name else QtCore.Qt.Checked
        assert cell.checkState() == want, s.out_name
    w3.close()
