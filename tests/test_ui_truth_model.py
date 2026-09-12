# -*- coding: utf-8 -*-
"""test_ui_truth_model.py —— 真值表 model 核心（C3-a0）：`dreg_verify/ui/truth/`。

不起视图（`QApplication` 但不建窗口，`TruthModel` 是 `QAbstractTableModel`，
只有 `test_e_inputs_matches_v1_signalview` 那一条要起 v1 的 `SignalView` 做对照）。
夹具一律走 `ui_harness.mirror_path`（公开仓，代码/测试里**绝不出现真实信号名**）。

覆盖的契约 ID 写在函数名里（`tools/contract_check.py` 按 `c\\d{3}` 反查）：
C-073/074/076/077/078/079/080/081/082/083/084/086/087/088/089/091/092/093/094/096/097/098/
102/103/106/107/111/128/129/130/134/294/298/299 + 不变量 I-15。
"""

import ast
import io
import os
import re
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import ui_harness as H                                  # noqa: E402
from dreg_verify import edits as ED                     # noqa: E402
from dreg_verify import excel_model as M                # noqa: E402
from dreg_verify import providers as PV                 # noqa: E402
from dreg_verify import truth_edit as TE                # noqa: E402

pytest.importorskip("PySide6")

from dreg_verify.ui import contracts                    # noqa: E402
from dreg_verify.ui import terms                        # noqa: E402
from dreg_verify.ui.truth import TruthModel, e_inputs_from_an          # noqa: E402
from dreg_verify.ui.truth import io as TIO              # noqa: E402
from dreg_verify.ui.truth import model as TM            # noqa: E402
from dreg_verify.ui.truth import rows as TR             # noqa: E402

#: mirror 上挑出来的对照信号（`probe` 过：btlp 一条 logic 根 + 一条 mux 根；
#: wl 那条 logic 根带 iddq DFT 门 —— C-128/C-129/C-130 只有它测得到）
LOGIC_SIG = "d_logic_bt_lp_rx_en"
MUX_SIG = "d_bt_lp_lna_itrim"
GATED_SIG = "d_wl_rf_lo2g5g_bias_en"
RO_SIG = "pll_lock_indicator"            # status != ok → editable == ""（C-134）


class _Cfg(object):
    """`ConfigSourceProto` 的最小实现（与 tests/test_providers.py 的同名替身同形）。"""

    def __init__(self, wb):
        self.wb = wb
        self.probe_prefixes = {}
        self.force_signals = set()
        self.logic_overrides = {}
        self.include_risky = True


@pytest.fixture(scope="module")
def qapp():
    return H.app()


@pytest.fixture(scope="module")
def btlp(qapp):
    return M.load_workbook(H.mirror_path("btlp"))


@pytest.fixture(scope="module")
def wl(qapp):
    return M.load_workbook(H.mirror_path("wl"))


def _analyze(wb, name, page=None):
    prov = (PV.PageProvider(_Cfg(wb), page) if page else PV.TopoutProvider(_Cfg(wb)))
    an = prov.analyze(name, "min", 64, False)
    assert an is not None, "mirror 上没有信号 %s（夹具挑错了）" % name
    return an


def _fresh(wb, name, page=None):
    """(model, an, e_inputs) —— 载好一个信号的真值表（列 = 出厂默认）。"""
    an = _analyze(wb, name, page)
    ei = e_inputs_from_an(an)
    m = TruthModel()
    m.load(an, ED.cols_from_vectors(an, ei), ei)
    return m, an, ei


def _exp_row(m):
    return m.rowCount() - 1


def _auto_row(m):
    return m.rowCount() - 2


def _dump(m):
    """整表逐格快照（撤销后逐格回到原样的比对基准）。"""
    return ([[m.data(m.index(r, c)) for c in range(m.columnCount())]
             for r in range(m.rowCount())],
            m.all_names(),
            [m.col_state(c) for c in range(m.columnCount())])


def _first_editable_input_row(m):
    for r in range(m.rowCount() - 2):
        if m.flags(m.index(r, 0)) & _ITEM_EDITABLE:
            return r
    raise AssertionError("这个信号一条可编辑输入行都没有，挑错夹具了")


from PySide6.QtCore import Qt                           # noqa: E402

_ITEM_EDITABLE = Qt.ItemIsEditable


# ═══════════════════ 形状 / 行序 / 标签 ═══════════════════
@pytest.mark.contract("C-073", "C-074", "C-077")
def test_c073_c074_c077_shape_rows_are_inputs_plus_auto_plus_exp(btlp):
    """C-073：行 = 输入信号 + auto_out + 期望，列 = 一条测试。C-074/C-077 行标签。"""
    m, an, ei = _fresh(btlp, LOGIC_SIG)
    assert m.rowCount() == len(ei) + 2
    assert m.columnCount() == len(an["vectors"]) > 0
    kinds = [m.row_kind(r) for r in range(m.rowCount())]
    assert kinds == ([contracts.TruthRowKind.INPUT] * len(ei)
                     + [contracts.TruthRowKind.AUTO, contracts.TruthRowKind.EXP])
    # C-074：行标签写真实信号名（不是 A/B/C 字母），角色内联在括号里
    for r, e in enumerate(ei):
        assert m.row_label(r) == e["label"]
        assert e["label"].startswith(e["key"].split(":")[-1]) or "(" in e["label"]
    assert m.row_label(_auto_row(m)) == terms.TRUTH_ROW_AUTO      # C-077
    assert m.row_label(_exp_row(m)) == terms.TRUTH_ROW_EXP        # C-078
    # 列头 = 最终标号（负向带 _NEG）
    assert m.all_names() == [c["name"] for c in m.cols()]
    assert m.headerData(0, Qt.Horizontal, Qt.DisplayRole) == m.all_names()[0]
    # 行数不随列数走 —— 这是 I-15「加/删列不用 reset」的前提
    n_rows = m.rowCount()
    m.append_test_column(2)
    m.remove_columns([0])
    assert m.rowCount() == n_rows


@pytest.mark.contract("C-076")
def test_c076_row_order_matches_for_test(btlp, wl):
    """C-076：输入行按 for_test 页行序排 —— 与 `inputs_table.input_rows`（报告/回填同口径）一致。

    两边都吃同一个 `an`：logic 根按 `an["groups"]`（`analysis_norm.order_groups_fortest` 排过），
    mux 根按 `expansion["used_vars"]`，DFT 门行殿后。行序漂了 = 编辑器与导出两套行序。
    """
    from dreg_verify import inputs_table as IT
    checked = 0
    for wb, names in ((btlp, (LOGIC_SIG, MUX_SIG)), (wl, (GATED_SIG,))):
        for nm in names:
            an = _analyze(wb, nm)
            ei = e_inputs_from_an(an)
            rows = IT.input_rows(an)
            # 键序必须一致（输入表可能因 §7-4 补行而多出来，但公共前缀必须同序）
            k_model = [e["key"] for e in ei]
            k_table = [r.get("key") for r in rows]
            assert k_model == k_table[:len(k_model)], "%s 行序与输入信号表不一致" % nm
            assert ei[-1]["is_dft_gate"] == bool(an.get("dft_gate"))   # 门行殿后
            checked += 1
    assert checked == 3


@pytest.mark.contract("C-084")
def test_c084_display_format_roundtrips_parse_int(btlp):
    """C-084：1 位→0/1；2–4 位→0bXXXX 零填充；更宽→0xNN，**且能被 parse_int 原样读回**。

    并与 v1 `gui.MainWindow._cell_text` 逐值对照（这个格式是 v1 已经在用的那一份；
    `generator._fmt_cell` 是**报告**的格式：多位一律 0xN，不能被当成同一件事，故不许 import）。
    """
    from dreg_verify import generator as G
    from dreg_verify import gui as GUI
    assert TM.fmt_cell(1, 1) == "1" and TM.fmt_cell(0, 1) == "0"
    assert TM.fmt_cell(0xA, 4) == "0b1010"           # 2–4 位零填充
    assert TM.fmt_cell(1, 2) == "0b01"
    assert TM.fmt_cell(0x2D, 8) == "0x2D"            # 更宽 → 0xNN
    n_diff_vs_report = 0
    for w in range(1, 17):
        for v in (0, 1, 2, 3, 5, 9, 0xA, 0x2D, 0xFFFF):
            s = TM.fmt_cell(v, w)
            assert s == GUI.MainWindow._cell_text(v, w), (v, w)        # 与 v1 编辑器同
            assert TE.parse_int(s) == (v & ((1 << w) - 1)), (v, w, s)  # roundtrip
            if s != G._fmt_cell(v & ((1 << w) - 1), w):
                n_diff_vs_report += 1
    assert n_diff_vs_report > 0, "跟报告格式一模一样的话，这条对照就等于没验"
    # 真值表里逐格都满足 roundtrip（不是只对构造出来的样例成立）
    m, _an, ei = _fresh(btlp, LOGIC_SIG)
    for r in range(m.rowCount()):
        for c in range(m.columnCount()):
            raw = m.data(m.index(r, c), int(contracts.TruthRole.RAW_VALUE))
            assert TE.parse_int(m.data(m.index(r, c))) == raw


# ═══════════════════ roles / 状态色 ═══════════════════
@pytest.mark.contract("C-079", "C-063", "C-107", "C-128", "C-129", "C-130")
def test_c079_c107_c129_roles_states_and_current_col(wl):
    """八个自定义 role 全部给得出有意义的值；DFT 拍列整列只读且状态 dft（C-129/C-130）。"""
    m, an, ei = _fresh(wl, GATED_SIG)
    assert an.get("dft_gate"), "挑的对照信号没有 iddq 门，C-128/129/130 就没测到"
    R = contracts.TruthRole
    ix = m.index(0, 0)
    assert m.data(ix, int(R.ROW_KIND)) == contracts.TruthRowKind.INPUT
    assert m.data(ix, int(R.COL_STATE)) in ("auto", "user", "neg", "dft")
    assert m.data(ix, int(R.CELL_STATE)) in ("editable", "readonly", "dft")
    assert isinstance(m.data(ix, int(R.RAW_VALUE)), int)
    assert m.data(ix, int(R.COL_NAME)) == m.all_names()[0]
    assert m.data(ix, int(R.IS_FALLBACK)) is False
    assert m.data(m.index(_exp_row(m), 0), int(R.IS_FALLBACK)) is True   # 未手填 = 兜底显示
    drives = m.data(ix, int(R.DRIVE_TIP))                                # C-063
    assert isinstance(drives, list) and drives and drives == m.column_drives(0)
    # 底色/字色/字体不在 model 里做（delegate 查 CELL_STATE）
    for role in (Qt.BackgroundRole, Qt.ForegroundRole, Qt.FontRole):
        assert m.data(ix, role) is None

    # C-129/C-130：iddq 自检拍列
    dft_cols = [c for c in range(m.columnCount())
                if m.col_state(c) == contracts.TruthColState.DFT]
    assert len(dft_cols) == 1
    dc = dft_cols[0]
    for r in range(m.rowCount()):
        if m.row_kind(r) == contracts.TruthRowKind.AUTO:
            continue
        assert m.cell_state(r, dc) == "dft", r
        assert not (m.flags(m.index(r, dc)) & _ITEM_EDITABLE), "DFT 拍列不许可编辑"
    # C-128：门行在表里（只读）
    gate_r = next(r for r, e in enumerate(ei) if e["is_dft_gate"])
    assert m.cell_state(gate_r, 0) == "readonly"
    assert not (m.flags(m.index(gate_r, 0)) & _ITEM_EDITABLE)

    # C-107：当前列高亮只动一进一出两列
    seen = []
    m.dataChanged.connect(lambda a, b, roles=None: seen.append((a.column(), b.column())))
    m.set_current_col(1)
    assert seen == [(1, 1)]
    seen[:] = []
    m.set_current_col(2)
    assert sorted(seen) == [(1, 1), (2, 2)]
    assert m.data(m.index(0, 2), int(R.IS_CURRENT_COL)) is True
    assert m.data(m.index(0, 1), int(R.IS_CURRENT_COL)) is False


@pytest.mark.contract("C-134")
def test_c134_editable_kind_empty_means_readonly(btlp):
    """C-134：不可建信号（`an["editable"] == ""`）→ 全表只读、写入口一律不认。"""
    an = _analyze(btlp, RO_SIG)
    assert an["editable"] == ""
    ei = e_inputs_from_an(an)
    m = TruthModel()
    m.load(an, ED.cols_from_vectors(an, ei), ei)
    assert m.editable_kind() == ""
    for r in range(m.rowCount()):
        for c in range(m.columnCount()):
            assert not (m.flags(m.index(r, c)) & _ITEM_EDITABLE)
    # 写入口全部拒绝，且一步撤销都不许产生
    assert m.append_test_column(1) == []
    assert m.duplicate_column(0) == (-1, "")
    assert m.add_negatives([0]) == (0, 0)
    assert m.undo_stack().count() == 0


# ═══════════════════ 写路径 ═══════════════════
@pytest.mark.contract("C-078", "C-080", "C-081")
def test_c078_c080_c081_exp_and_auto_recompute(btlp):
    """C-078 期望逐格手填；C-080 改输入 → auto_out 当场重算、已手填期望不动；C-081 清空 = 回未填。"""
    m, an, ei = _fresh(btlp, LOGIC_SIG)
    exp_r, auto_r = _exp_row(m), _auto_row(m)
    in_r = _first_editable_input_row(m)

    # C-078：手填期望 → 状态从 unfilled 变成 match / diff，且不再是兜底
    auto0 = m.data(m.index(auto_r, 0), int(contracts.TruthRole.RAW_VALUE))
    assert m.cell_state(exp_r, 0) == "unfilled"
    assert m.setData(m.index(exp_r, 0), str(auto0)) is True
    assert m.cell_state(exp_r, 0) == "match"
    assert m.data(m.index(exp_r, 0), int(contracts.TruthRole.IS_FALLBACK)) is False
    assert m.setData(m.index(exp_r, 0), str(auto0 ^ 1)) is True
    assert m.cell_state(exp_r, 0) == "diff"

    # C-080：改输入格 → auto_out 重算；已手填的期望一个字不动
    before_exp = m.data(m.index(exp_r, 0))
    old_in = m.data(m.index(in_r, 0), int(contracts.TruthRole.RAW_VALUE))
    changed = 0
    for v in (0, 1):
        m.setData(m.index(in_r, 0), str(v))
        col = m.cols()[0]
        assert col["auto"] == _auto_of(an, col), "auto_out 没跟着输入重算（C-080）"
        changed += 1
    assert changed == 2
    assert m.data(m.index(exp_r, 0)) == before_exp, "重算把手填期望改掉了（C-080 明确禁止）"
    m.setData(m.index(in_r, 0), str(old_in))

    # C-081：期望清空 = 回到「待手填」（兜底显示 auto_out），负向标记一并清
    assert m.setData(m.index(exp_r, 0), "") is True
    assert m.cols()[0]["exp"] is None
    assert m.cell_state(exp_r, 0) == "unfilled"
    assert m.data(m.index(exp_r, 0), int(contracts.TruthRole.IS_FALLBACK)) is True
    assert m.data(m.index(exp_r, 0)) == m.data(m.index(auto_r, 0))


def _auto_of(an, col):
    """拿引擎单独算一遍这列的 auto_out（不信 model 自己说的）。"""
    probe = {"vals": dict(col["vals"]), "auto": None, "auto_w": col["auto_w"],
             "dft": col.get("dft"), "neg": col.get("neg")}
    ED.recompute_col_an(an, probe)
    return probe["auto"]


@pytest.mark.contract("C-082", "C-083")
def test_c082_c083_parse_int_forms_and_failure_restores(btlp):
    """C-082 八种数值写法都认；C-083 认不出来 → 发 `parseFailed` + 这一格还原，**绝不吞成 0**。"""
    m, _an, _ei = _fresh(btlp, LOGIC_SIG)
    exp_r = _exp_row(m)
    got = []
    m.parseFailed.connect(got.append)

    # 八种写法（C-082 契约原文那八条），期望格位宽够宽的信号用不上——这里逐写法比 parse_int
    forms = ["16'h3", "'b101", "'d9", "hA", "0x3", "0b101", "9", "A"]
    w = m.cols()[0]["auto_w"]
    for s in forms:
        assert m.setData(m.index(exp_r, 0), s) is True, s
        assert m.cols()[0]["exp"] == (TE.parse_int(s) & ((1 << w) - 1)), s
    assert not got, "合法写法不该报解析失败"

    # 认不出来：返回 False（Qt 据此还原显示）、发 parseFailed、格子内容一个字不变
    before = m.cols()[0]["exp"]
    shown = m.data(m.index(exp_r, 0))
    for bad in ("0x", "zz+", "12'hZ", "--"):
        got[:] = []
        assert m.setData(m.index(exp_r, 0), bad) is False, bad
        assert len(got) == 1 and terms.TRUTH_PARSE_FAILED_FMT.split("{")[0] in got[0]
        assert bad in got[0], "提示里要带用户填的原文"
        assert m.cols()[0]["exp"] == before and m.data(m.index(exp_r, 0)) == shown

    # 输入格同一套（空串 = 0，不是「未填」）
    in_r = _first_editable_input_row(m)
    got[:] = []
    assert m.setData(m.index(in_r, 0), "zz+") is False
    assert len(got) == 1
    assert m.setData(m.index(in_r, 0), "") is True
    assert m.data(m.index(in_r, 0), int(contracts.TruthRole.RAW_VALUE)) == 0


@pytest.mark.contract("C-106")
def test_c106_fill_progress(btlp):
    """C-106：(手填 n, 正向 m, 不一致 k)，且每次写入后 `progressChanged` 跟着报一次。"""
    m, _an, _ei = _fresh(btlp, LOGIC_SIG)
    exp_r, auto_r = _exp_row(m), _auto_row(m)
    seen = []
    m.progressChanged.connect(lambda a, b, c: seen.append((a, b, c)))
    n_pos = sum(1 for c in m.cols() if not c["neg"])
    assert m.fill_progress() == (0, n_pos, 0)

    a0 = m.data(m.index(auto_r, 0), int(contracts.TruthRole.RAW_VALUE))
    m.setData(m.index(exp_r, 0), str(a0))            # 与 auto 一致
    assert m.fill_progress() == (1, n_pos, 0)
    a1 = m.data(m.index(auto_r, 1), int(contracts.TruthRole.RAW_VALUE))
    m.setData(m.index(exp_r, 1), str(a1 ^ 1))        # 与【本列】auto 不一致 → k+1
    n, mm, k = m.fill_progress()
    assert (n, mm) == (2, n_pos) and k == 1
    assert seen[-1] == (n, mm, k), "progressChanged 没跟上"
    # 反例列只算进 k 之外的口径：加一条反例后正向总数不变
    m.add_negatives([0])
    assert m.fill_progress()[1] == n_pos


@pytest.mark.contract("C-111")
def test_c111_mux_user_data_matches_edits(btlp):
    """C-111：mux 手编列的数据值只改本列 + 按路由 case 重算 auto_out，且与 `edits.set_mux_user_data` 等价。"""
    m, an, ei = _fresh(btlp, MUX_SIG)
    assert m.editable_kind() == "mux"
    at, _nm = m.duplicate_column(0)                  # C-115/C-116：mux 加列 = 克隆 case
    assert at == m.columnCount() - 1 and m.cols()[at]["user"] is True
    data_r = next(r for r, e in enumerate(ei) if e["mux_data_base"])
    key = ei[data_r]["key"]

    # 参照物：拿引擎在一份独立拷贝上算一遍（model 把 edits.set_mux_user_data 的三步拆开了，
    # 为的是 redo/undo 走同一条路 —— 拆完还等不等价，只能这样逐字段对）
    ref = ED.cols_from_vectors(an, ei)
    ref_col = ED.copy_cols(ref, [0])[0]
    new_txt = "0b0101"
    ED.set_mux_user_data(an, ref_col, key, ei[data_r]["width"], new_txt)
    assert ref_col["vals"][key] != ref[0]["vals"][key], "挑的新值跟原值一样，这条等于没验"

    before_others = [c["vals"][key] for c in m.cols()]
    assert m.set_mux_user_data(at, key, new_txt) is True
    got = m.cols()[at]
    assert got["vals"][key] == ref_col["vals"][key]
    assert got["auto"] == ref_col["auto"], "auto_out 没按路由 case 的数据源重算"
    assert got["vec"].assignments[key] == ref_col["vec"].assignments[key]
    # 只改本列：别的列一个字不动（C-111 与 C-110 整表同步的分界就在这一条）
    assert [c["vals"][key] for c in m.cols()][:at] == before_others[:at]
    assert m.undo_stack().count() == 2                # duplicate + 这次改值，各一步
    m.undo_stack().undo()
    assert m.cols()[at]["vals"][key] == before_others[at]
    assert m.cols()[at]["vec"].assignments[key] == before_others[at]
    m.undo_stack().redo()

    # C-110 整表同步要 `set_reanalyzer` 接的外部回路（下一条测试验它）；**没接上时不许静默落半个**：
    # 自动生成列的数据格写入返回 False 并说明原因，表一个字不动（假的所见即所得比不做还糟）。
    got_msgs = []
    m.parseFailed.connect(got_msgs.append)
    assert m.set_mux_data_value("whatever", "1") is False
    before = _dump(m)
    assert m.setData(m.index(data_r, 0), "0b0011") is False    # 0 号列是自动生成列
    assert len(got_msgs) == 2 and all(got_msgs)
    assert got_msgs[-1] == terms.TRUTH_MUX_DATA_NO_REANALYZER
    assert _dump(m) == before


# ═══════════════════ 列操作 ═══════════════════
@pytest.mark.contract("C-086", "C-087", "C-088", "C-091", "C-092", "C-093", "C-099")
def test_c086_c087_c088_c093_column_ops(btlp):
    """加列 / 复制列 / 删列 / 改名（含三道校验 + 自动 T 列拒改名）。"""
    m, _an, _ei = _fresh(btlp, LOGIC_SIG)
    n0 = m.columnCount()
    seen_cols = []
    m.colsChanged.connect(lambda: seen_cols.append(m.columnCount()))

    # C-086 加列：输入全 0、auto_out 自动算、期望留空
    names = m.append_test_column(1)
    assert len(names) == 1 and m.columnCount() == n0 + 1
    new_i = n0
    assert m.cols()[new_i]["user"] is True and m.cols()[new_i]["exp"] is None
    assert m.cell_state(_exp_row(m), new_i) == "unfilled"
    for r in range(len(_e(m))):
        if m.flags(m.index(r, new_i)) & _ITEM_EDITABLE:
            assert m.data(m.index(r, new_i), int(contracts.TruthRole.RAW_VALUE)) == 0
    assert seen_cols and seen_cols[-1] == n0 + 1, "加列后没发 colsChanged"

    # C-099：新列名对【全集】唯一（含 T<n> 与 _NEG，大小写无关）
    assert len(set(x.upper() for x in m.all_names())) == m.columnCount()

    # C-087 复制列：取值与期望一并带走、自动换新名
    m.setData(m.index(_exp_row(m), new_i), "1")
    at, nm = m.duplicate_column(new_i)
    assert at == m.columnCount() - 1 and nm not in m.all_names()[:at]
    assert m.cols()[at]["vals"] == m.cols()[new_i]["vals"]
    assert m.cols()[at]["exp"] == m.cols()[new_i]["exp"]

    # C-093：自动生成的 T 列拒改名（文案就是 terms 那句）
    ok, why = m.rename_column(0, "MY_T")
    assert ok is False and why == terms.TRUTH_RENAME_AUTO_REFUSED
    assert m.all_names()[0].upper().startswith("T")

    # C-091/C-092：手编列可改名；三道校验（非法字符清成下划线 / 拒 T<编号> / 拒重名）
    ok, final = m.rename_column(at, "my col")
    assert ok is True and final == "my_col" and m.all_names()[at] == "my_col"
    assert m.rename_column(at, "")[0] is False
    assert m.rename_column(at, "T3")[0] is False
    assert m.rename_column(at, m.all_names()[new_i])[0] is False

    # C-088 删列（多选）
    before = m.all_names()
    removed = m.remove_columns([new_i, at])
    assert len(removed) == 2 and m.columnCount() == len(before) - 2
    assert m.all_names() == [x for i, x in enumerate(before) if i not in (new_i, at)]


def _e(m):
    """model 的输入行（测试里少写一句 rowCount-2）。"""
    return list(range(m.rowCount() - 2))


@pytest.mark.contract("C-096", "C-097", "C-098", "C-101", "C-102", "C-103")
def test_c096_c102_negatives_delegate_to_engine(btlp):
    """加/删反例全部委托 `truth_edit.plan_negatives` / `edits.*`——只验「接对了」与一步撤销。"""
    m, _an, _ei = _fresh(btlp, LOGIC_SIG)
    n0 = m.columnCount()
    made, skipped = m.add_negatives([0])
    assert (made, skipped) == (1, 0) and m.columnCount() == n0 + 1
    assert m.col_state(n0) == contracts.TruthColState.NEG
    assert m.all_names()[n0].upper().endswith("_NEG")
    assert m.cell_state(_exp_row(m), n0) == "neg"
    # C-097/C-098：同一组取值再加一次 → 跳过；反例列不套娃
    made2, skipped2 = m.add_negatives([0])
    assert (made2, skipped2) == (0, 1)
    # C-101：全部正向各加一条
    made3, _s = m.add_negatives([], all_positive=True)
    assert made3 == n0 - 1
    # C-103：值得保护的反例（改过名的）报得出来
    assert m.protected_negatives() == []
    m.rename_column(n0, "keep_me")
    assert [c["name"] for c in m.protected_negatives()] == ["keep_me_NEG"]
    # C-102：删反例只删反例
    n_neg = sum(1 for c in m.cols() if c["neg"])
    assert m.del_negatives() == n_neg
    assert all(not c["neg"] for c in m.cols()) and m.columnCount() == n0


@pytest.mark.contract("C-089", "C-085", "C-094")
def test_c089_c085_c094_clear_regen_fill(btlp):
    """清零 / 重新生成 / auto→期望 —— 各自一步撤销、各自发 colsChanged 或 dataChanged。"""
    m, an, ei = _fresh(btlp, LOGIC_SIG)
    base = _dump(m)

    # C-094 auto→期望：填进所有未填的正向列，已填的不动
    m.setData(m.index(_exp_row(m), 0), "1")
    n = m.fill_expected()
    assert n == sum(1 for c in m.cols() if not c["neg"]) - 1
    assert all(c["exp"] is not None for c in m.cols() if not c["neg"])
    assert ED.fill_targets(m.cols()) == []

    # C-089 清零 = 零用例；行不动
    n_rows = m.rowCount()
    m.clear_all()
    assert m.columnCount() == 0 and m.rowCount() == n_rows
    assert m.cols() == [] and m.fill_progress() == (0, 0, 0)

    # C-085 重新生成：回出厂态
    m.regenerate(an, ei)
    assert _dump(m) == base


# ═══════════════════ 复制 / 粘贴 ═══════════════════
@pytest.mark.contract("C-293", "C-294")
def test_c294_paste_minimal_overflow_and_one_undo(btlp):
    """C-294：落在活动格 / 超列自动追加 / **超行拒绝** / 整次一步撤销；复制走矩形选区。"""
    m, _an, _ei = _fresh(btlp, LOGIC_SIG)
    exp_r = _exp_row(m)
    base = _dump(m)
    reports = []
    m.pasteReport.connect(reports.append)

    # 复制：矩形选区，按行列外接矩形出 TSV
    ixs = [m.index(exp_r, c) for c in range(2)]
    tsv = m.copy_tsv(ixs)
    assert tsv == "\t".join(m.data(m.index(exp_r, c)) for c in range(2))

    # 落在活动格（r0, c0），只动那一片。撤销步数用 `index()` 量而不是 `count()`：
    # undo 之后再 push 会把 redo 尾巴丢掉，count 不涨（栈位置才是「现在踩在第几步上」），
    # 而这条测试中途撤销了好几次。
    n_undo = m.undo_stack().index()
    ok, msg = m.paste_tsv("1\t0", exp_r, 1)
    assert ok is True and msg and reports[-1] == msg
    assert m.undo_stack().index() == n_undo + 1
    assert m.cols()[1]["exp"] == 1 and m.cols()[2]["exp"] == 0
    assert m.cols()[0]["exp"] is None, "落点左边的列被动了"
    m.undo_stack().undo()
    assert _dump(m) == base

    # 超列 → 自动追加列；追加 + 落值仍是**一步**撤销（这是路线 A 选型的决定性一条）
    n_cols, n_undo = m.columnCount(), m.undo_stack().index()
    wide = "\t".join(["1"] * (n_cols + 2))
    ok, msg = m.paste_tsv(wide, exp_r, 0)
    assert ok is True
    assert m.columnCount() == n_cols + 2
    assert m.undo_stack().index() == n_undo + 1, "追加列 + 落值必须算一步撤销"
    m.undo_stack().undo()
    assert m.columnCount() == n_cols and _dump(m) == base

    # 超行 → 拒绝（真值表的行是输入信号，不能凭空加），一格都不落、一步撤销都不产生
    n_undo = m.undo_stack().index()
    tall = "\n".join(["1"] * (m.rowCount() + 1))
    ok, msg = m.paste_tsv(tall, 0, 0)
    assert ok is False
    assert msg == terms.TRUTH_PASTE_OVERFLOW_ROWS_FMT.format(rows=m.rowCount() + 1,
                                                             max=m.rowCount())
    assert m.undo_stack().index() == n_undo and _dump(m) == base
    assert reports[-1] == msg


@pytest.mark.contract("C-298")
def test_c298_apply_expectations_and_batch_fill(btlp):
    """C-298 的 model 面：按列名回填期望（报没对上的列名）+ 批量填。各一步撤销。"""
    m, _an, _ei = _fresh(btlp, LOGIC_SIG)
    names = m.all_names()
    n_undo = m.undo_stack().index()
    n, missing = m.apply_expectations({names[0]: 1, names[1].lower(): 0,
                                       "T_NOT_THERE": 7})
    assert n == 2 and missing == ["T_NOT_THERE"]
    assert m.undo_stack().index() == n_undo + 1
    assert m.cols()[0]["exp"] == 1 and m.cols()[1]["exp"] == 0
    m.undo_stack().undo()
    assert m.cols()[0]["exp"] is None

    n_undo = m.undo_stack().index()
    n, msg = m.batch_fill([0, 1, 2], "0x1")
    assert n == 3 and msg and m.undo_stack().index() == n_undo + 1
    assert [c["exp"] for c in m.cols()[:3]] == [1, 1, 1]
    got = []
    m.parseFailed.connect(got.append)
    n, msg = m.batch_fill([0], "zz+")
    assert n == 0 and len(got) == 1

    # 读文件那半边归 C3-d 的 truth/io.py（下面两条单测它的接线）；文件读不了时**不许崩**：
    # 照实报一句就返回（面板是从文件对话框拿的路径，用户可能选完就把文件挪走了）
    got[:] = []
    n, missing, msg = m.import_expectations("nope.csv")
    assert (n, missing) == (0, [])
    assert terms.TRUTH_IMPORT_READ_FAILED_FMT.split("{")[0] in msg
    assert len(got) == 1 and got[0] == msg
    assert m.undo_stack().index() == n_undo + 1, "读失败不该动撤销栈"


# ═══════════════════ I-15 / C-299 ═══════════════════
#: 每种写操作一条：(名字, 前置动作或 None, 干这件事的 callable)。
#: C-299 与 I-15 两条测试共用同一张表。前置动作在【量撤销步数之前】跑完——改名要先有一条
#: 手编列、删反例要先有反例，否则那两条量到的是「什么都没干」（假绿）。
def _write_ops(m):
    exp_r, in_r = _exp_row(m), _first_editable_input_row(m)
    return [
        ("setData 期望", None, lambda: m.setData(m.index(exp_r, 0), "1")),
        ("setData 输入", None, lambda: m.setData(m.index(in_r, 1), "1")),
        ("加列", None, lambda: m.append_test_column(1)),
        ("加两列", None, lambda: m.append_test_column(2)),
        ("复制列", None, lambda: m.duplicate_column(0)),
        ("删列", None, lambda: m.remove_columns([m.columnCount() - 1])),
        ("改名", lambda: m.append_test_column(1),
         lambda: m.rename_column(m.columnCount() - 1, "renamed_col")),
        ("加反例", None, lambda: m.add_negatives([0])),
        ("删反例", lambda: m.add_negatives([0]), lambda: m.del_negatives()),
        ("auto→期望", None, lambda: m.fill_expected()),
        ("批量填", None, lambda: m.batch_fill([0, 1], "1")),
        ("按列名填", None, lambda: m.apply_expectations({m.all_names()[0]: 0})),
        ("粘贴(不加列)", None, lambda: m.paste_tsv("1\t0", exp_r, 0)),
        ("粘贴(加列)", None, lambda: m.paste_tsv("\t".join(["1"] * (m.columnCount() + 2)),
                                                  exp_r, 0)),
        ("清零", None, lambda: m.clear_all()),
    ]


@pytest.mark.contract("C-299")
@pytest.mark.parametrize("idx", range(15))
def test_c299_every_write_is_one_undo_step(btlp, idx):
    """C-299：**每种写操作恰好 +1 步撤销**，undo 之后 `cols()` 与 `data()` 逐格回到原样。

    参数化逐条跑（一条挂了点名是哪一种写操作，而不是「撤销栈坏了」这种废话）。
    """
    m, _an, _ei = _fresh(btlp, LOGIC_SIG)
    name, setup, fn = _write_ops(m)[idx]
    if setup is not None:
        setup()
    before_cols, before_dump = m.cols(), _dump(m)
    n0 = m.undo_stack().count()
    fn()
    assert m.undo_stack().count() == n0 + 1, "%s 不是一步撤销" % name
    assert m.undo_stack().canUndo()
    m.undo_stack().undo()
    assert m.cols() == before_cols, "%s 撤销后 cols() 没回到原样" % name
    assert _dump(m) == before_dump, "%s 撤销后表面值没回到原样" % name
    m.undo_stack().redo()                       # 重做也要走同一条路
    assert m.undo_stack().count() == n0 + 1
    m.undo_stack().undo()
    assert _dump(m) == before_dump


def test_c299_write_ops_table_covers_every_proto_write_method(btlp):
    """上一条的参数化表不许漏掉写方法 —— 表本身也要被验一次（搜索/扫描类测试的老规矩）。"""
    m, _an, _ei = _fresh(btlp, LOGIC_SIG)
    assert len(_write_ops(m)) == 15
    covered = {"setData", "append_test_column", "duplicate_column", "remove_columns",
               "rename_column", "add_negatives", "del_negatives", "fill_expected",
               "batch_fill", "apply_expectations", "paste_tsv", "clear_all"}
    # Proto 里剩下的写方法各有归宿：regenerate 另有一条；两个 mux 写入口另有一条
    assert covered | {"regenerate", "set_mux_data_value", "set_mux_user_data",
                      "import_expectations"} >= _proto_write_methods()


def _proto_write_methods():
    return {"setData", "append_test_column", "duplicate_column", "remove_columns",
            "rename_column", "add_negatives", "del_negatives", "fill_expected",
            "clear_all", "regenerate", "set_mux_data_value", "set_mux_user_data",
            "paste_tsv", "import_expectations", "batch_fill", "apply_expectations"}


@pytest.mark.contract("C-085", "C-299")
def test_c085_regenerate_is_one_undo_step(btlp):
    """重新生成 = 删旧列 + 按覆盖度重出，`beginMacro` 裹成一步（C-085 + C-299）。"""
    m, an, ei = _fresh(btlp, LOGIC_SIG)
    m.setData(m.index(_exp_row(m), 0), "1")
    m.append_test_column(1)
    before = _dump(m)
    n0 = m.undo_stack().count()
    m.regenerate(an, ei)
    assert m.undo_stack().count() == n0 + 1
    assert m.cols() == ED.cols_from_vectors(an, ei) or m.columnCount() == len(an["vectors"])
    m.undo_stack().undo()
    assert _dump(m) == before


def test_i15_no_clear_no_reset_outside_load(btlp):
    """I-15：`load()` 之外绝不 reset；整个类里除撤销栈外零 `clear()`。

    三道：① `modelReset` 计数；② load 之后把 `beginResetModel` 换成会炸的桩，跑遍全部写操作；
    ③ 源码扫 `.clear(` 只准出现在 `self._undo.clear()` 上（v1 `_populate_truth` 的
    `tbl.clear()` 是列宽被弹回、编辑闪的根因，一个字都不许有）。
    """
    m, an, ei = _fresh(btlp, LOGIC_SIG)
    n_reset = []
    m.modelReset.connect(lambda: n_reset.append(1))

    # ① load 一次 = reset 一次
    m.load(an, ED.cols_from_vectors(an, ei), ei)
    assert len(n_reset) == 1

    # ② load 之后任何写操作都不许 reset
    def _boom(*_a, **_k):
        raise AssertionError("load() 之外调了 beginResetModel —— I-15 破了")

    m.beginResetModel = _boom
    m.endResetModel = _boom
    for _name, setup, fn in _write_ops(m):
        if setup is not None:
            setup()
        fn()
    m.set_current_col(0)
    m.copy_tsv([m.index(0, 0)])
    m.undo_stack().undo()
    m.undo_stack().redo()
    assert len(n_reset) == 1, "写操作里混进了 reset"

    # ③ 源码扫：唯一允许的 .clear() 是撤销栈的（走 AST，不然注释/docstring 里提一嘴都算命中）
    src_dir = os.path.dirname(TM.__file__)
    hits = []
    for fn in sorted(os.listdir(src_dir)):
        if fn.endswith(".py"):
            hits += _clear_calls(os.path.join(src_dir, fn), fn)
    assert not hits, "ui/truth 里出现了不该有的 clear()：\n%s" % "\n".join(hits)
    # 这条扫描自己也要能命中已知靶子：v1 的 _populate_truth 里确实有 tbl.clear()
    gui_py = os.path.join(os.path.dirname(os.path.dirname(src_dir)), "gui.py")
    known = _clear_calls(gui_py, "gui.py")
    assert any("tbl.clear" in x for x in known), \
        "扫描器对着 gui.py 都扫不出 tbl.clear()，这条规则等于没验"


def _clear_calls(path, tag):
    """这个文件里【除撤销栈之外】的 `xxx.clear()` 调用（AST，不吃注释与 docstring）。"""
    tree = ast.parse(io.open(path, encoding="utf-8").read(), filename=path)
    out = []
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "clear"):
            txt = ast.unparse(node.func)
            if txt != "self._undo.clear":
                out.append("%s:%d %s()" % (tag, node.lineno, txt))
    return out


# ═══════════════════ 接口面 / 分层 ═══════════════════
def test_truth_model_implements_proto_surface():
    """`TruthModelProto` 的每个方法名都在，`TRUTH_SIGNALS` 五个信号一个不少。"""
    want = [n for n in dir(contracts.TruthModelProto) if not n.startswith("_")]
    assert want, "Proto 上一个方法都没读到，这条断言等于没验"
    missing = [n for n in want if not hasattr(TruthModel, n)]
    assert not missing, "TruthModel 缺 Proto 方法：%s" % missing
    from PySide6.QtCore import SignalInstance
    m = TruthModel()
    for sig in contracts.TRUTH_SIGNALS:
        assert isinstance(getattr(m, sig), SignalInstance), sig
    assert TruthModel.progressChanged is not None


def test_key_role_matches_mux_gen(btlp, wl):
    """`truth/rows.key_role` 与 `mux_gen.key_role` 逐键相等（ui/ 不许 import 引擎，故抄了一份）。"""
    from dreg_verify import mux_gen
    n = 0
    for wb, name in ((btlp, MUX_SIG), (wl, "d_wl_rf_lo2g5g_mixer2g_trim")):
        an = _analyze(wb, name)
        exp = an.get("expansion") or {}
        for k in (exp.get("used_vars") or []) + list(exp.get("data_keys") or []):
            assert TR.key_role(k, exp.get("data_keys")) == mux_gen.key_role(k), k
            assert TR.key_role(k) == mux_gen.key_role(k), k
            n += 1
    assert n > 10, "只比了 %d 个键，覆盖太窄" % n
    assert TR.key_role("m3.x:A") == "upstream"     # 三档都要比到，不能只比到 data


def test_model_module_imports_only_allowed_layers():
    """I-19 的本地加强版：`ui/truth/*` 只准 import 允许的那几层（theme 也不许，model 只给键名）。"""
    src_dir = os.path.dirname(TM.__file__)
    # exports：C3-d 的 `io.py` 走它导 CSV / 拿 .sv build（架构 §6.15 明写）——它自己也是
    # Qt-free 的编排层，不是引擎（`test_ui_layering` 的 ENGINE_MODULES 里没有它）。
    allowed = {"edits", "truth_edit", "inputs_table", "analysis_norm", "exports",
               "contracts", "names", "terms", "commands", "rows", "model", "io", "panel"}
    #: 视图层（C3-b）另外准 import `theme` / `widgets` —— 架构 §6.13：底色字色**只**在
    #: delegate/表头里按 role 查 theme 的表。model 那半边照旧不许（它只给 CELL_STATE 键名）。
    view_extra = {"theme", "widgets", "delegate", "view", "panel"}
    view_files = {"view.py", "delegate.py", "panel.py"}
    #: C3-c 的面板另外准 import `ui/dialogs.py` —— 三处确认 / 重命名列 / mux 数据值整表 /
    #: 批量填四个框都在那儿（架构 §6.14 明写「三处确认 → dialogs.confirm」）。
    #: **只给 panel.py**：view / delegate 一旦能弹框，「视图不改数据」那条线就守不住了。
    panel_extra = {"dialogs"}
    bad = []
    for fn in sorted(os.listdir(src_dir)):
        if not fn.endswith(".py"):
            continue
        ok = allowed | (view_extra if fn in view_files else set())
        ok |= (panel_extra if fn == "panel.py" else set())
        tree = ast.parse(io.open(os.path.join(src_dir, fn), encoding="utf-8").read())
        for node in ast.walk(tree):
            if not (isinstance(node, ast.ImportFrom) and node.level):
                continue
            # `from .commands import X` → 模块是 commands；`from ... import edits as ED`
            # （module 为空的相对 import）→ 名字本身就是模块
            mods = ([node.module.split(".")[-1]] if node.module
                    else [a.name for a in node.names])
            bad += ["%s: %s" % (fn, mm) for mm in mods if mm not in ok]
    assert not bad, "ui/truth 里 import 了不该 import 的：%s" % bad
    # 先证明这条扫描判得出「不允许」：把 theme 塞进来会被抓（model 只给 CELL_STATE 键名）
    assert "theme" not in allowed
    heads = [ln for ln in io.open(os.path.join(src_dir, "model.py"), encoding="utf-8")
             if ln.startswith(("import ", "from "))]
    assert heads and not any("theme" in ln for ln in heads)


# ═══════════════════ 与 v1 的逐字段对照 ═══════════════════
def test_e_inputs_matches_v1_signalview(monkeypatch, tmp_path):
    """`e_inputs_from_an` 与 v1 `SignalView.e_inputs` 在两张 mirror 的**全部信号**上逐字段相同。

    唯一的**有意差异**是 `label`：v1 写裸信号名，v2 按架构 §6.12 换成
    `inputs_table.vheader_display`（真名　(角色 · 端口)，C-074/C-075 要的那一行）——
    所以这里断言「label 以 v1 那个名字开头、且等于 vheader_display 的结果」，其余八个键逐字相等。
    """
    from dreg_verify import inputs_table as IT
    H.isolate_settings(monkeypatch, tmp_path)
    n_sig, diffs = 0, []
    for kind in ("btlp", "wl"):
        w = H.make_window(kind=kind)
        try:
            views = [w.topout_view] + [w.page_views[p] for p in ("logic", "mux", "dft", "iddq")
                                       if p in w.page_views]
            for sv in views:
                for mdl in list(sv.models):
                    sv._load_signal(mdl["name"])
                    if sv.cur_an is None:
                        continue
                    v1 = [dict(e) for e in sv.e_inputs]
                    v2 = e_inputs_from_an(sv.cur_an)
                    n_sig += 1
                    if len(v1) != len(v2):
                        diffs.append("%s: 行数 %d vs %d" % (mdl["name"], len(v1), len(v2)))
                        continue
                    for a, b in zip(v1, v2):
                        for k in ("key", "width", "editable", "control", "mux_data_base",
                                  "is_dft_gate", "wire_lhs", "transp"):
                            va = a.get(k)
                            vb = b[k]
                            if k in ("editable", "control", "is_dft_gate"):
                                va, vb = bool(va), bool(vb)
                            if va != vb:
                                diffs.append("%s/%s.%s: %r vs %r"
                                             % (mdl["name"], a.get("key"), k, va, vb))
                        if not b["label"].startswith(a["label"]):
                            diffs.append("%s/%s.label: %r 不是以 v1 的 %r 开头"
                                         % (mdl["name"], a.get("key"), b["label"], a["label"]))
        finally:
            w.close()
    assert n_sig > 40, "只对了 %d 个信号，覆盖太窄（mirror 载失败？）" % n_sig
    assert not diffs, "与 v1 SignalView 的 e_inputs 有差异：\n%s" % "\n".join(diffs[:20])
    # label 的新格式确实是 vheader_display 给的（不是恰好以裸名开头）
    an = _analyze(M.load_workbook(H.mirror_path("btlp")), LOGIC_SIG)
    for g, e in zip(an["groups"], e_inputs_from_an(an)):
        assert e["label"] == IT.vheader_display(g, an)


# ═══════════════════ C-063：驱动明细跟着当前列走 ═══════════════════
#: 驱动明细一行的两种形状（`inputs_table.vector_drives` 的产物），用来把值从串里读回来
_RE_FORCE_LINE = re.compile(r"^force `ENV_RF\.\S+ = (\S+);\s+← (.+)$")
_RE_RFW_FIELD = re.compile(r"([0-9A-Za-z_]+)\(bit<<\d+\)=([^、\s]+)")


def _drive_map(lines):
    """驱动明细文本 → `{被驱动的基名小写: 值}`。

    **刻意不复用 model 的任何内部函数**：从产物串里把值读回来，这样「tooltip 说的」与
    「这一列的 vals」是两条独立的路——否则就是拿实现验实现。
    """
    out = {}
    for ln in lines:
        mm = _RE_FORCE_LINE.match(ln)
        if mm:
            out[mm.group(2).strip().lower()] = TE.parse_int(mm.group(1))
            continue
        if ln.startswith("`RF_WRITE"):
            for base, hx in _RE_RFW_FIELD.findall(ln):
                out[base.lower()] = TE.parse_int(hx)
    return out


@pytest.mark.contract("C-063")
def test_c063_column_drives_follow_current_columns(btlp, wl):
    """C-063：列头/单元格的驱动明细跟着【当前列】走——加列 / 删列 / 复制列之后不许串号。

    `inputs_table.column_drives(an, c)` 按 `an["vectors"][c]` 取向量，那是**出厂**那一批；
    列集合一动，第 c 列早就不是第 c 条向量了，tooltip 会说别的列的 force（C3-a0 报告 #1）。
    这里先在出厂态对齐引擎口径，再动列集，逐列把 tooltip 里的值与该列 `vals` 对回去。
    """
    from dreg_verify import inputs_table as IT
    n_checked = 0
    for wb, name in ((btlp, LOGIC_SIG), (wl, GATED_SIG)):
        m, an, _ei = _fresh(wb, name)
        # ① 出厂态：与引擎按下标取的那份逐字相同（现算的口径没跑偏）
        for c in range(m.columnCount()):
            assert m.column_drives(c) == IT.column_drives(an, c), (name, c)
            assert m.data(m.index(0, c), int(contracts.TruthRole.DRIVE_TIP)) \
                == m.column_drives(c)
        # ② 动列集：删首列 + 复制 + 加列 + 改一个输入格
        m.remove_columns([0])
        m.duplicate_column(0)
        m.append_test_column(1)
        in_r = _first_editable_input_row(m)
        m.setData(m.index(in_r, 0), "1")
        for c, col in enumerate(m.cols()):
            got = _drive_map(m.column_drives(c))
            common = [k for k in got if k in col["vals"]]
            assert len(common) >= 2, "%s 第 %d 列只解析出 %d 条驱动" % (name, c, len(common))
            for k in common:
                assert got[k] == col["vals"][k], \
                    "%s 第 %d 列的 %s：tooltip 说 %r，这一列的取值是 %r（串号了）" \
                    % (name, c, k, got[k], col["vals"][k])
            n_checked += 1
        # ③ 变异自证：旧写法（按下标取出厂向量）在这张表上**确实会**串号
        stale = [IT.column_drives(an, c) for c in range(m.columnCount())]
        assert stale != [m.column_drives(c) for c in range(m.columnCount())], \
            "%s 上旧写法恰好没串号，这条对照等于没验（换个夹具）" % name
        # 列头 tooltip 与单元格 role 走同一条（C-063 两处都给）
        assert m.headerData(0, Qt.Horizontal, int(contracts.TruthRole.DRIVE_TIP)) \
            == m.column_drives(0)
    assert n_checked >= 15, "只逐列比了 %d 列，覆盖太窄" % n_checked


# ═══════════════════ C-110 / C-112 / C-113 / C-114：整表 mux 数据值 ═══════════════════
class _FakeReanalyzer(object):
    """`TruthModel.set_reanalyzer` 的替身 = 面板（C3-c）将来要接的那条回路，一比一照抄
    `state` 的做法：写会话桶（`edits.set_mux_data_value`）→ 带 `mux_data` 重分析 → 返回
    **真** an（provider 真算的，不是桩）。"""

    def __init__(self, prov, an, name, e_inputs, mode="min", maxt=64, exh=False):
        self.prov, self.name, self.e_inputs = prov, name, list(e_inputs)
        self.src_out_name, self.disp_name = an["src_out_name"].lower(), an["name"]
        self.mode, self.maxt, self.exh = mode, maxt, exh
        self.bucket = {}                      # = state.mux_data()（会话档）
        self.calls = []

    def width_of(self, base_low):
        return next(e["width"] for e in self.e_inputs
                    if (e["mux_data_base"] or "") == base_low)

    def data(self):
        ent = self.bucket.get(self.name.lower())
        return dict(ent["data"]) if ent and ent.get("data") else None

    def __call__(self, base_low, text):
        self.calls.append((base_low, text))
        ED.set_mux_data_value(self.bucket, self.name.lower(), self.src_out_name,
                              self.disp_name, base_low, self.width_of(base_low), text)
        return self.prov.analyze(self.name, self.mode, self.maxt, self.exh,
                                 mux_data=self.data())


def _mux_model(wb, name=MUX_SIG):
    """(model, an, e_inputs, reanalyzer) —— 一个接好回路的 mux 真值表。"""
    prov = PV.TopoutProvider(_Cfg(wb))
    an = prov.analyze(name, "min", 64, False)
    assert an is not None and an["editable"] == "mux"
    ei = e_inputs_from_an(an)
    m = TruthModel()
    m.load(an, ED.cols_from_vectors(an, ei), ei)
    rean = _FakeReanalyzer(prov, an, name, ei)
    m.set_reanalyzer(rean)
    return m, an, ei, rean


def _data_bases(ei):
    return [e["mux_data_base"] for e in ei if e["mux_data_base"]]


@pytest.mark.contract("C-110", "C-112", "C-299")
def test_c110_c112_set_mux_data_value_syncs_whole_table_one_undo(btlp):
    """C-110 整表 by_base 同步（清空恢复自动分配）；C-112 屏幕值 == 内部/导出值；一步撤销。"""
    m, _an, ei, rean = _mux_model(btlp)
    base = _data_bases(ei)[0]
    data_r = next(i for i, e in enumerate(ei) if e["mux_data_base"] == base)
    key = ei[data_r]["key"]
    before = _dump(m)
    n_undo = m.undo_stack().index()

    assert m.set_mux_data_value(base, "0x5") is True
    assert m.undo_stack().index() == n_undo + 1, "整表同步不是一步撤销"
    assert rean.calls == [(base, "0b0101")], "传给回路的文本要先按该行位宽规范化过"
    # C-110：**整表**都改了，不是只改一格
    assert [c["vals"][key] for c in m.cols()] == [5] * m.columnCount()
    # C-112：屏幕值 == 会话桶里存的值 == 重分析后的向量取值（三处一致 = 所见即所得）
    assert rean.data() == {base: 5}
    assert m.data(m.index(data_r, 0)) == TM.fmt_cell(5, ei[data_r]["width"])
    for c, col in enumerate(m.cols()):
        assert col["vec"].assignments[key] == 5, c

    # 撤销：表回原样，**会话桶也回原样**（只把列塞回去的话 state 还留着新值 = C-112 的洞）
    m.undo_stack().undo()
    assert _dump(m) == before
    assert rean.data() is None, "撤销没把 state.mux_data 里的手填清掉"
    assert rean.calls[-1] == (base, ""), "撤销要经同一条回路把旧值写回去"
    m.undo_stack().redo()
    assert [c["vals"][key] for c in m.cols()] == [5] * m.columnCount()

    # 同一个值再设一次 = 没变 → 不占撤销步、也不白重分析一遍
    n_undo, n_calls = m.undo_stack().index(), len(rean.calls)
    assert m.set_mux_data_value(base, "0b0101") is True
    assert m.undo_stack().index() == n_undo and len(rean.calls) == n_calls

    # I-15：整表换列也不许 reset
    n_reset = []
    m.modelReset.connect(lambda: n_reset.append(1))
    m.set_mux_data_value(base, "")
    assert not n_reset
    assert rean.data() is None


@pytest.mark.contract("C-113")
def test_c113_mux_data_collision_warns(btlp):
    """C-113：≥2 条数据路取到相同值 = 选错路也测不出（假绿）——撞上了要发 `muxCollision`。

    撞值判据在引擎（`mux_gen` 的 `value_collision` / `override_collision`），`an` 里它归到
    `status_detail == "false-green"` 这一档（见报告「引擎层发现」：`an["expansion"]` **没有**
    `meta` 键，撞值标记只在 `res.meta` 上，没被 `analysis_norm` 抬进 an）。
    """
    m, _an, ei, _rean = _mux_model(btlp)
    bases = _data_bases(ei)
    seen = []
    m.muxCollision.connect(seen.append)

    # 只钉一条数据路：引擎会给别的路重新分配互异值 → 不撞
    target = m.cols()[1]["auto"]
    assert m.set_mux_data_value(bases[0], str(target)) is True
    assert seen == [], "只钉一条就报撞值 = 误报（引擎会自动避开）"

    # 两条数据路钉成同一个值 → 真撞了
    assert m.set_mux_data_value(bases[1], str(target)) is True
    assert seen == [terms.TRUTH_MUX_COLLISION]
    assert m._an["status_detail"] == "false-green"
    autos = [c["auto"] for c in m.cols()]
    assert autos.count(target) >= 2, "没真造出『两条路同值』，这条等于没验"


@pytest.mark.contract("C-114")
def test_c114_mux_keyed_expected_survives_resync(btlp):
    """C-114：mux 手填期望按【输入取值键】存 —— 改完数据值重分析，期望不串号、不丢。"""
    from dreg_verify import generator as G
    m, _an, ei, _rean = _mux_model(btlp)
    exp_r = _exp_row(m)
    base = _data_bases(ei)[0]
    base_r = next(i for i, e in enumerate(ei) if e["mux_data_base"] == base)
    names0 = m.all_names()
    # 在 T3 上手填一个与 auto 不同的期望 + 给 T1 加一条反例
    m.setData(m.index(exp_r, 3), "0xF")
    m.add_negatives([1])
    exp_before = [c["exp"] for c in m.cols()]
    neg_before = [c["neg"] for c in m.cols()]
    names_before = m.all_names()

    assert m.set_mux_data_value(base, "0x1") is True
    assert m.all_names() == names_before, "重分析把列名/列序动了"
    assert m.all_names()[:len(names0)] == names0
    assert [c["exp"] for c in m.cols()] == exp_before, "重分析把手填期望挪位/丢了（C-114）"
    assert [c["neg"] for c in m.cols()] == neg_before

    # 落到生成器的那一步：期望按 `generator.mux_assign_key` 键存，键来自【新】assignments
    d = ED.mux_derive(m._an, m.cols())
    col3 = m.cols()[3]
    assert d["expected"][G.mux_assign_key(col3["vec"].assignments)] == 15
    assert col3["vec"].assignments[ei[base_r]["key"]] == 1


def test_c110_set_mux_data_value_guards(btlp):
    """整表同步的四道门：非 mux 信号 / 没有这个基名 / 写法认不出 / 回路没接上——都不动表。"""
    m, _an, ei, rean = _mux_model(btlp)
    base = _data_bases(ei)[0]
    bad = []
    m.parseFailed.connect(bad.append)
    before = _dump(m)

    assert m.set_mux_data_value("no_such_base", "1") is False
    assert m.set_mux_data_value(base, "zz+") is False
    assert len(bad) == 2 and all(bad)
    assert _dump(m) == before and m.undo_stack().count() == 0 and rean.calls == []

    # logic 信号上调整表同步：直接不认（它没有 mux 数据行）
    lm, _a, _e = _fresh(btlp, LOGIC_SIG)
    assert lm.set_mux_data_value("whatever", "1") is False
    assert lm.undo_stack().count() == 0


# ═══════════════════ C-115…C-123：mux 语义 ═══════════════════
@pytest.mark.contract("C-115", "C-116", "C-117", "C-118", "C-119", "C-120",
                      "C-121", "C-122", "C-123")
def test_c115_to_c123_mux_semantics(btlp):
    """mux 的加列 / 复制 / 删列 / 清零 / 重新生成 / 改名 / 反例 逐条。"""
    m, an, ei = _fresh(btlp, MUX_SIG)
    base_dump = _dump(m)
    n0 = m.columnCount()

    # C-115 加列 = 克隆【选中列】的 case 成手编列（不是凭空造输入：mux 的输入由路由 case 决定）
    names = m.append_test_column(1, src_idx=1)
    at = m.columnCount() - 1
    assert len(names) == 1 and m.cols()[at]["user"] is True
    assert m.cols()[at]["vals"] == m.cols()[1]["vals"], "克隆的不是选中那条 case"
    assert m.cols()[at]["vec"] is not m.cols()[1]["vec"], "vec 必须是克隆，不能共用"
    assert m.cols()[at]["vec"].case_index == m.cols()[1]["vec"].case_index
    # 未选中时退回最后一列（与 `edits.copy_cols` 同）
    m.append_test_column(1)
    assert m.cols()[-1]["vals"] == m.cols()[at]["vals"]
    m.remove_columns([m.columnCount() - 1])

    # C-116 复制列：数据值与期望一并带走、自动换新名
    m.setData(m.index(_exp_row(m), at), "0x7")
    at2, nm2 = m.duplicate_column(at)
    assert m.cols()[at2]["exp"] == m.cols()[at]["exp"] == 7
    assert m.cols()[at2]["vals"] == m.cols()[at]["vals"]
    assert nm2 not in m.all_names()[:at2]

    # C-120 改名：手编列可改；自动生成列拒绝
    ok, _final = m.rename_column(at2, "my_case")
    assert ok is True and m.all_names()[at2] == "my_case"
    ok, why = m.rename_column(0, "nope")
    assert ok is False and why == terms.TRUTH_RENAME_AUTO_REFUSED

    # C-121 逐 case 加反例（mux 一列 = 一条 case）；同一组取值不叠第二条
    made, skipped = m.add_negatives([1])
    assert (made, skipped) == (1, 0)
    assert m.col_state(m.columnCount() - 1) == contracts.TruthColState.NEG
    assert m.add_negatives([1]) == (0, 1)
    # C-122 全部正向各加一条：按【取值】算，已有反例的那组跳过、取值重复的只算一组
    keys_pos = {TE.vals_key(c["vals"]) for c in m.cols() if not c["neg"]}
    keys_neg = {TE.vals_key(c["vals"]) for c in m.cols() if c["neg"]}
    made_all, _s = m.add_negatives([], all_positive=True)
    assert made_all == len(keys_pos - keys_neg), "已有反例/取值重复的 case 要跳过"
    assert {TE.vals_key(c["vals"]) for c in m.cols() if c["neg"]} == keys_pos
    # 反例列进 `mux_derive` 时是带 is_negative 的 user_vecs（不是自动列）
    d = ED.mux_derive(m._an, m.cols())
    assert sum(1 for v in d["user_vecs"] if v.is_negative) \
        == sum(1 for c in m.cols() if c["neg"]) > 0

    # C-123 删反例 = 手编反例列 + 整信号反例标记一起清（v2 只有一份标记：cols 里的 neg）
    n_neg = sum(1 for c in m.cols() if c["neg"])
    assert m.del_negatives() == n_neg
    assert all(not c["neg"] for c in m.cols())
    assert not any(v.is_negative for v in ED.mux_derive(m._an, m.cols())["user_vecs"])

    # C-117 删列：自动列只是从 cols 里移走，`mux_derive` 据「现在还剩哪些自动列」算 dropped
    auto0 = m.all_names()[0]
    assert ED.mux_derive(m._an, m.cols())["dropped"] == []
    m.remove_columns([0])
    dropped = ED.mux_derive(m._an, m.cols())["dropped"]
    assert len(dropped) == 1, "删掉一条自动列，dropped 要恰好多一条签名"
    assert auto0 not in m.all_names()

    # C-118 清零 = 零用例（与覆盖度无关）；C-119 重新生成回出厂态
    m.clear_all()
    assert m.columnCount() == 0 and ED.mux_derive(m._an, m.cols())["cleared"] is True
    m.regenerate(an, ei)
    assert _dump(m) == base_dump
    assert m.columnCount() == n0


@pytest.mark.contract("C-085", "C-119", "C-299")
def test_c085_regenerate_undo_restores_an_too(btlp):
    """C-085/C-119：重新生成一步撤销，且**撤销连 `an` 一起还原**。

    只把列塞回去的话，撤销后的表会拿【新 an】去解释【旧列】（DFT 拍列认不出、驱动明细算错），
    静默不一致——这里拿「换一个 an 去 regenerate，再撤销」把它钉死。
    """
    m, an, ei = _fresh(btlp, LOGIC_SIG)
    other = _analyze(btlp, LOGIC_SIG)              # 另一份等价的 an 对象（身份不同）
    assert other is not an
    m.setData(m.index(_exp_row(m), 0), "1")
    m.append_test_column(1)
    before, before_cols = _dump(m), m.cols()
    n0 = m.undo_stack().count()

    m.regenerate(other, e_inputs_from_an(other))
    assert m.undo_stack().count() == n0 + 1
    assert m._an is other
    m.undo_stack().undo()
    assert m._an is an, "撤销没把 an 还原（C3-a0 遗留：regenerate 的宏不管 an）"
    assert m.cols() == before_cols and _dump(m) == before
    m.undo_stack().redo()
    assert m._an is other and m.columnCount() == len(other["vectors"])


def _mux_write_ops(m, ei):
    """mux 专有写操作表（参数化与完整性两条测试共用）。每条都能在出厂态直接跑。"""
    base = _data_bases(ei)[0]
    key = next(e["key"] for e in ei if e["mux_data_base"])
    return [
        ("整表 mux 数据值", None, lambda: m.set_mux_data_value(base, "0x5")),
        ("mux 加列(克隆选中 case)", None, lambda: m.append_test_column(1, 0)),
        ("mux 本列数据值", lambda: m.duplicate_column(0),
         lambda: m.set_mux_user_data(m.columnCount() - 1, key, "0b0011")),
        ("mux 加反例", None, lambda: m.add_negatives([0])),
        ("mux 全部加反例", None, lambda: m.add_negatives([], all_positive=True)),
        ("mux 删反例", lambda: m.add_negatives([0]), lambda: m.del_negatives()),
        ("mux 删列", None, lambda: m.remove_columns([0])),
        ("mux 清零", None, lambda: m.clear_all()),
    ]


@pytest.mark.contract("C-299")
@pytest.mark.parametrize("idx", range(8))
def test_c299_mux_write_ops_are_one_undo_step(btlp, idx):
    """C-299 的 mux 面：mux 专有的写操作也是**每种恰好一步撤销**，撤销后逐格回原样。

    这里比 `_dump(m)` 而不是 `cols()`：整表 mux 数据值同步会经重分析**重建**列 dict 与 `vec`
    （对象身份必然变），逐对象比会假红；表面值 + 列名 + 列状态全回原样才是用户看得见的那件事。
    """
    m, _an, ei, _rean = _mux_model(btlp)
    name, setup, fn = _mux_write_ops(m, ei)[idx]
    if setup is not None:
        setup()
    before = _dump(m)
    n0 = m.undo_stack().count()
    fn()
    assert m.undo_stack().count() == n0 + 1, "%s 不是一步撤销" % name
    m.undo_stack().undo()
    assert _dump(m) == before, "%s 撤销后表面值没回到原样" % name
    m.undo_stack().redo()
    assert m.undo_stack().count() == n0 + 1
    m.undo_stack().undo()
    assert _dump(m) == before


def test_c299_mux_write_ops_table_is_complete(btlp):
    """上一条的参数化表也要被验一次：mux 专有的三个写入口一个不落。"""
    m, _an, ei, _rean = _mux_model(btlp)
    ops = _mux_write_ops(m, ei)
    assert len(ops) == 8
    got = [n for n, _s, _f in ops]
    assert got.count("整表 mux 数据值") == 1 and got.count("mux 本列数据值") == 1


# ═══════════════════ C-097…C-105：反例的四条规则 ═══════════════════
@pytest.mark.contract("C-097", "C-098", "C-099", "C-100")
def test_c097_c098_c099_c100_negatives_rules(btlp):
    """C-097 同取值不叠；C-098 不给反例套反例；C-099 名字对全集唯一；C-100 错值避开两个正确值。"""
    # C-100 要一条**多位**输出：1 位输出上「避开 auto_out 且避开手填期望」无解（两个值就把
    # 1 位占满了），`vectors.make_negative` 的注释明说此时保留取反值并记 NEG-BROKEN。
    mw, _anw, _eiw = _fresh(btlp, MUX_SIG)
    assert mw.cols()[0]["auto_w"] > 1, "挑的对照信号是 1 位输出，C-100 在它上面无解"
    exp_rw, nw = _exp_row(mw), mw.columnCount()
    auto0 = mw.cols()[0]["auto"]
    naive_wrong = (~auto0) & ((1 << mw.cols()[0]["auto_w"]) - 1)
    mw.setData(mw.index(exp_rw, 0), str(naive_wrong))
    assert mw.add_negatives([0]) == (1, 0)
    neg = mw.cols()[nw]
    assert neg["exp"] != auto0, "反例错值撞上了 auto_out（C-100）"
    assert neg["exp"] != naive_wrong, "反例错值撞上了 designer 手填期望（C-100 明确禁止）"

    m, _an, _ei = _fresh(btlp, LOGIC_SIG)
    exp_r = _exp_row(m)
    n0 = m.columnCount()
    m.add_negatives([0])
    assert m.cols()[n0]["exp"] != m.cols()[0]["auto"], "反例错值撞上了 auto_out（C-100）"

    # C-097：同一组输入取值再加一次 → 跳过并报条数
    assert m.add_negatives([0]) == (0, 1)
    # C-098：对着反例列本身加 → 退回首条正向；反例列不许套娃
    n_before = m.columnCount()
    m.add_negatives([n0])
    pos_vals = [c["vals"] for c in m.cols()[:n0] if not c["neg"]]
    for i in range(n_before, m.columnCount()):
        assert m.cols()[i]["vals"] in pos_vals, "反例列被当成了造反例的源（C-098）"
    # C-099：全集唯一（含 T<n> 与 _NEG，大小写无关）
    m.add_negatives([], all_positive=True)
    names = [x.upper() for x in m.all_names()]
    assert len(set(names)) == len(names), "列名撞了 = .sv 里两块同名"
    assert sum(1 for c in m.cols() if c["neg"]) == n0, "每条正向恰好一条反例"


@pytest.mark.contract("C-104")
def test_c104_neg_broken_kept(btlp):
    """C-104：反例期望被填成「正确值」时仍保住反例身份（记成 NEG-BROKEN，不静默退化成普通断言）。"""
    m, an, _ei = _fresh(btlp, LOGIC_SIG)
    exp_r = _exp_row(m)
    n0 = m.columnCount()
    m.add_negatives([0])
    c = n0
    assert m.cols()[c]["neg"] is True
    auto = m.cols()[c]["auto"]

    # 手填成 auto_out（= 正确值）：列还是反例列，状态色还是 neg
    assert m.setData(m.index(exp_r, c), str(auto)) is True
    assert m.cols()[c]["neg"] is True, "填成正确值就把反例身份丢了（C-104）"
    assert m.col_state(c) == contracts.TruthColState.NEG
    assert m.cell_state(exp_r, c) == "neg"
    assert m.all_names()[c].upper().endswith("_NEG")
    # 落到生成器那一步也还是负向（NEG-BROKEN：标负向但错值==正确值，仿真会过，不静默）
    v = ED.cols_to_vectors(an, m.cols())[c]
    assert v.is_negative is True and v.neg_value == (auto & ((1 << v.exp_width) - 1))

    # C-081：清空期望才是「取消反例身份」那件事，且两件事一起撤销
    assert m.setData(m.index(exp_r, c), "") is True
    assert m.cols()[c]["exp"] is None and m.cols()[c]["neg"] is False
    m.undo_stack().undo()
    assert m.cols()[c]["exp"] == auto and m.cols()[c]["neg"] is True


@pytest.mark.contract("C-105")
def test_c105_mux_neg_wrong_value_flips_off_correct(btlp):
    """C-105：mux 反例错值撞上「正确值」时自动翻一位（`vectors.make_negative` 那段防撞）。"""
    from dreg_verify import vectors as V
    m, _an, _ei = _fresh(btlp, MUX_SIG)
    exp_r = _exp_row(m)
    col0 = m.cols()[0]
    w = col0["auto_w"]
    naive = (~col0["auto"]) & ((1 << w) - 1)
    m.setData(m.index(exp_r, 0), str(naive))       # 手填期望 = 裸取反值 → 逼它翻一位
    n0 = m.columnCount()
    assert m.add_negatives([0]) == (1, 0)
    wrong = m.cols()[n0]["exp"]
    assert wrong != col0["auto"] and wrong != naive
    assert bin(wrong ^ naive).count("1") == 1, "不是『翻一位』避开的"
    # 对照：没有手填期望时就该是裸取反值（证明上面那条差异确实来自防撞，不是碰巧）
    m2, _a2, _e2 = _fresh(btlp, MUX_SIG)
    n2 = m2.columnCount()
    m2.add_negatives([0])
    assert m2.cols()[n2]["exp"] == (~m2.cols()[0]["auto"]) & ((1 << w) - 1)
    assert V.make_negative(V.TestVector(0, {}, col0["auto"], w),
                           mode="invert").neg_value == naive


# ═══════════════════ C-128…C-131：DFT 门行 / iddq 自检拍列 ═══════════════════
@pytest.mark.contract("C-128", "C-129", "C-130")
def test_c128_c129_c130_dft_gate_row_and_pitch_col(wl):
    """C-128 门行只读在表里；C-129 自检拍列整列 dft 态；C-130 自检拍列不参与表达式重算。"""
    m, an, ei = _fresh(wl, GATED_SIG)
    gate = an["dft_gate"]
    assert gate, "挑的对照信号没有 iddq 门"
    gate_r = next(r for r, e in enumerate(ei) if e["is_dft_gate"])
    dcs = [c for c in range(m.columnCount()) if m.col_state(c) == contracts.TruthColState.DFT]
    assert len(dcs) == 1
    dc = dcs[0]
    nc = next(c for c in range(m.columnCount()) if c != dc)

    # C-128：门行在表里、整行只读、值 = 该列向量实际 force 的门值
    assert ei[gate_r]["wire_lhs"] and ei[gate_r]["transp"] is not None
    for c in range(m.columnCount()):
        assert not (m.flags(m.index(gate_r, c)) & _ITEM_EDITABLE)
        assert m.data(m.index(gate_r, c), int(contracts.TruthRole.RAW_VALUE)) \
            == m.cols()[c]["vals"][gate["key"]]
    assert m.cols()[dc]["vals"][gate["key"]] != gate["transp"], "自检拍列的门值该是非透传值"

    # C-129：自检拍列整列 dft 态、整列不可编辑（期望格也不行——那列不是反例，改它没意义）
    for r in range(m.rowCount()):
        if m.row_kind(r) != contracts.TruthRowKind.AUTO:
            assert m.cell_state(r, dc) == "dft", r
        assert not (m.flags(m.index(r, dc)) & _ITEM_EDITABLE), r
    assert ED.expected_cell_state(an, m.cols()[dc]) == ED.EXP_DFT

    # C-130：不参与表达式重算（节点不含 iddq 门，重算会得功能值 → 与 force 的常量假红）
    probe = dict(m.cols()[dc]); probe["auto"] = None
    ED.recompute_col_an(an, probe)
    assert probe["auto"] is None, "自检拍列被重算了（C-130）"
    probe2 = dict(m.cols()[nc]); probe2["auto"] = None
    ED.recompute_col_an(an, probe2)
    assert probe2["auto"] is not None, "对照列都没被重算，这条守卫等于没验"

    # 整表粘贴也绕开它：一整行 1 粘过去，自检拍列一个字不动
    before_dft = [m.data(m.index(r, dc)) for r in range(m.rowCount())]
    in_r = _first_editable_input_row(m)
    ok, _msg = m.paste_tsv("\t".join(["1"] * m.columnCount()), in_r, 0)
    assert ok is True
    assert [m.data(m.index(r, dc)) for r in range(m.rowCount())] == before_dft
    assert m.data(m.index(in_r, nc), int(contracts.TruthRole.RAW_VALUE)) == 1


@pytest.mark.contract("C-131")
def test_c131_gate_row_not_duplicated_when_already_an_input(btlp, wl):
    """C-131：门已经是本信号显式输入时不再单列一行 DFT 门（与生成/报告去重同口径）。"""
    n_gate, n_skip = 0, 0
    for wb in (btlp, wl):
        prov = PV.TopoutProvider(_Cfg(wb))
        for mdl in prov.skeleton_models():
            an = prov.analyze(mdl["name"], "min", 64, False)
            if an is None:
                continue
            ei = e_inputs_from_an(an)
            keys = [e["key"] for e in ei]
            assert len(keys) == len(set(keys)), "%s 的输入行有重复键" % mdl["name"]
            gates = [e for e in ei if e["is_dft_gate"]]
            if an.get("dft_gate_skipped"):
                n_skip += 1
                assert not gates, "%s 的门被跳过了却还单列了一行" % mdl["name"]
            if gates:
                n_gate += 1
                assert len(gates) == 1
                assert gates[0]["key"] not in [g["key"] for g in (an.get("groups") or [])]
    assert n_gate > 0, "两张 mirror 上一个带门的信号都没有，这条等于没验"

    # 门被跳过时确实不出行（mirror 上没有现成的跳过样本，拿真 an 改一个键造出来）
    an = _analyze(wl, GATED_SIG)
    assert [e for e in e_inputs_from_an(an) if e["is_dft_gate"]], "对照信号本来就没门"
    skipped = dict(an)
    skipped["dft_gate"] = None
    skipped["dft_gate_skipped"] = {"gate_base": "x", "reason": "门网已经是本信号的显式输入"}
    assert not [e for e in e_inputs_from_an(skipped) if e["is_dft_gate"]]


# ═══════════════════ C-294 / C-298 / C-106 的收尾 ═══════════════════
@pytest.mark.contract("C-294", "C-083")
def test_c294_paste_names_the_cells_it_could_not_read(btlp):
    """C-294 + C-083 的粘贴面：没认出写法的格子要【点名】（行标签×列名），不是只报个数。"""
    m, _an, _ei = _fresh(btlp, LOGIC_SIG)
    exp_r = _exp_row(m)
    reports = []
    m.pasteReport.connect(reports.append)
    ok, msg = m.paste_tsv("1\tzz+\t0x", exp_r, 0)
    assert ok is True and reports[-1] == msg
    assert m.cols()[0]["exp"] == 1
    assert m.cols()[1]["exp"] is None and m.cols()[2]["exp"] is None
    for c in (1, 2):
        assert "%s×%s" % (m.row_label(exp_r), m.all_names()[c]) in msg, \
            "没点名第 %d 列那一格" % c
    assert "2" in msg

    # 规划（纯函数）与落格拆开了：规划一次不改任何东西
    before = _dump(m)
    n_undo = m.undo_stack().index()
    plan = m._paste_plan("1\t1\t1\t1\t1\t1", exp_r, 0)
    assert plan["ok"] is True and plan["need"] == max(0, 6 - m.columnCount())
    assert _dump(m) == before and m.undo_stack().index() == n_undo
    tall = "\n".join(["1"] * (m.rowCount() + 1))
    assert m._paste_plan(tall, 0, 0)["ok"] is False
    assert m._paste_plan("", 0, 0)["rows"] is None


@pytest.mark.contract("C-110", "C-112", "C-294")
def test_c294_paste_cannot_sneak_past_whole_table_mux_data(btlp):
    """C-110/C-112：粘贴**不许**从旁门改自动生成列的 mux 数据值（C3-d 报告的可疑点）。

    单格编辑走 `setData` 会被路由去整表同步，粘贴不能走那条路（一片格子逐个触发整表重分析，
    后一格盖掉前一格）。此前粘贴直接写 `col["vals"]`，而 mux 的 `recompute_col_an` 对 mux 早退、
    `vec.assignments` 一个字没动 → 屏幕 5、导出还是 10，正是 C-112 要堵的洞。
    """
    m, _an, ei, rean = _mux_model(btlp)
    data_r = next(i for i, e in enumerate(ei) if e["mux_data_base"])
    key = ei[data_r]["key"]
    before_vals = [c["vals"][key] for c in m.cols()]
    before_asg = [c["vec"].assignments[key] for c in m.cols()]
    reports = []
    m.pasteReport.connect(reports.append)

    ok, msg = m.paste_tsv("\t".join(["0b0011"] * m.columnCount()), data_r, 0)
    assert ok is True
    assert [c["vals"][key] for c in m.cols()] == before_vals, "粘贴绕过 C-110 改了自动列"
    assert [c["vec"].assignments[key] for c in m.cols()] == before_asg
    assert rean.calls == [], "粘贴不该逐格触发整表重分析"
    assert m.undo_stack().count() == 0
    assert terms.TRUTH_PASTE_MUX_WHOLE_FMT.split("{")[0] in msg
    assert str(m.columnCount()) in msg and reports[-1] == msg

    # 手编列的数据格照旧能粘（C-111：只改本列，`vec.assignments` 跟着走）
    at, _nm = m.duplicate_column(0)
    assert m.paste_tsv("0b0011", data_r, at)[0] is True
    assert m.cols()[at]["vals"][key] == 3
    assert m.cols()[at]["vec"].assignments[key] == 3, "手编列的粘贴没落进 vec（导出会与屏幕不一致）"


@pytest.mark.contract("C-294")
def test_c294_paste_says_why_it_could_not_add_columns(btlp):
    """C-294：加不出新列时结果说明要讲**真原因**，不能一律说成「只读行/列」。

    mux 清零过 = 一条 case 都没有 → `edits.copy_cols` 没有源列可克隆，`append_test_column`
    只能返回空（C-115：mux 的加列就是克隆选中 case，不能凭空造输入）。
    """
    m, _an, _ei = _fresh(btlp, MUX_SIG)
    m.clear_all()
    assert m.columnCount() == 0
    n_undo = m.undo_stack().index()
    ok, msg = m.paste_tsv("1\t1\t1", _exp_row(m), 0)
    assert ok is True and m.columnCount() == 0
    assert terms.TRUTH_PASTE_NO_NEW_COL_MUX in msg, \
        "没说清「mux 清零后没 case 可克隆」，只说了「只读」：%s" % msg
    assert terms.TRUTH_PASTE_SKIPPED_FMT.split("{")[0] not in msg
    assert m.undo_stack().index() == n_undo, "一格都没落，不该占一步撤销"
    # logic 信号清零后是加得出列的（对照：证明上面那句 mux 的原因不是随口一说）
    lm, lan, lei = _fresh(btlp, LOGIC_SIG)
    lm.clear_all()
    ok, msg2 = lm.paste_tsv("1\t1\t1", _exp_row(lm), 0)
    assert ok is True and lm.columnCount() == 3
    assert terms.TRUTH_PASTE_NO_NEW_COL_MUX not in msg2


@pytest.mark.contract("C-298")
def test_c298_import_expectations_goes_through_io(btlp, monkeypatch):
    """C-298：`import_expectations` 惰性接 `truth/io.read_expectations`，读完交给 `apply_expectations`。"""
    import types
    m, _an, _ei = _fresh(btlp, LOGIC_SIG)
    names = m.all_names()
    seen = []

    stub = types.ModuleType("dreg_verify.ui.truth.io")

    def _read(path):
        # io 的真实返回是 `(by_name, notes)`：值已解析成 int，notes 是逐条提示
        seen.append(path)
        return ({names[0]: 1, names[1].lower(): 0, "T_NOT_THERE": 7},
                ["T9 这一列写法怪：zz+"])

    stub.read_expectations = _read
    # 结果说明整句由真 io 拼（C3-int 去重后 model 与面板共用 `io.import_report_text`）——
    # 这条 stub 的只是「读文件」那一半，报告仍要走真实现，否则测的就不是真文案了。
    stub.import_report_text = TIO.import_report_text
    monkeypatch.setitem(sys.modules, "dreg_verify.ui.truth.io", stub)
    # 包上已经绑好 io 属性了（C3-d 的 io.py 已入库），`from . import io` 会先拿属性——
    # 只改 sys.modules 换不掉它，两处都得换
    import dreg_verify.ui.truth as _PKG
    monkeypatch.setattr(_PKG, "io", stub, raising=False)

    n_undo = m.undo_stack().index()
    n, missing, report = m.import_expectations("whatever.csv")
    assert seen == ["whatever.csv"]
    assert (n, missing) == (2, ["T_NOT_THERE"])
    assert "这一列写法怪" in report, "io 的逐条提示要原样进结果说明"
    assert m.undo_stack().index() == n_undo + 1, "整次导入必须是一步撤销"
    assert m.cols()[0]["exp"] == 1 and m.cols()[1]["exp"] == 0
    assert report and "T_NOT_THERE" in report
    m.undo_stack().undo()
    assert m.cols()[0]["exp"] is None

    # io 里没有 read_expectations（比如 C3-d 还没落地）→ 抛 NotImplementedError，不是 ImportError
    del stub.read_expectations
    with pytest.raises(NotImplementedError):
        m.import_expectations("whatever.csv")


@pytest.mark.contract("C-298")
def test_c298_import_expectations_reads_a_real_csv(btlp, tmp_path):
    """C-298 落地版：接【真的】`truth/io.read_expectations`（C3-d 已合入），读一张真 CSV。

    上一条用替身钉的是「接线对不对」；这一条钉的是「两边的返回形状真的对得上」——
    io 回的是 `(by_name, notes)` 且值已解析成 int（表里写的是 `0xA` / `4'b1010` 这类
    IC 写法，`int()` 一个都不认），model 这边多解一层、少解一层都会当场炸。
    """
    m, _an, _ei = _fresh(btlp, LOGIC_SIG)
    names = m.all_names()
    p = tmp_path / "exp.csv"
    p.write_text("信号\\测试,%s,%s,T_NOT_THERE\n期望,0x1,0b0,7\n" % (names[0], names[1]),
                 encoding="utf-8-sig")
    n_undo = m.undo_stack().index()
    n, missing, report = m.import_expectations(str(p))
    assert n == 2 and missing == ["T_NOT_THERE"]
    assert m.cols()[0]["exp"] == 1 and m.cols()[1]["exp"] == 0
    assert m.undo_stack().index() == n_undo + 1
    assert "T_NOT_THERE" in report


@pytest.mark.contract("C-106")
def test_c106_progress_emitted_after_every_write(btlp):
    """C-106：每次影响期望/列集的写之后 `progressChanged(n, m, k)` 都报一次（用 `QSignalSpy` 数）。"""
    from PySide6.QtTest import QSignalSpy
    m, _an, _ei = _fresh(btlp, LOGIC_SIG)
    spy = QSignalSpy(m.progressChanged)
    misses = []
    for name, setup, fn in _write_ops(m):
        if setup is not None:
            setup()
        n_before = spy.count()
        fn()
        if spy.count() <= n_before:
            misses.append(name)
        else:
            assert list(spy.at(spy.count() - 1)) == list(m.fill_progress()), name
    assert not misses, "这些写操作没发 progressChanged：%s" % misses
    # 撤销/重做也要报（撤销栈把表改回去了，进度条不能停在旧数字上）
    n_before = spy.count()
    m.undo_stack().undo()
    assert spy.count() > n_before
    assert list(spy.at(spy.count() - 1)) == list(m.fill_progress())


# ═══════════════════ C-132：同一串编辑 → 与 v1 逐字节相同的 .sv ═══════════════════
class _V1Edit(object):
    """在 v1 `SignalView` 上做编辑的薄壳。

    单元格走**真** `QTableWidget` item（`setText` → `itemChanged` → `_on_truth_item`，
    就是 v1 用户敲键的那条路）；mux 数据值走 `_set_mux_data` / `_set_mux_user_data`（也是真
    入口）。列操作走 `_e_add` / `_e_copy` / `_e_addneg`（选区用 `selectColumn` 真选），改名
    跳过 `QInputDialog` 直接走它后面那半截 `ED.rename_col` + `_commit`——对话框不是本条要
    比的东西，数据路径一个字不差。
    """

    def __init__(self, sv, name):
        self.sv, self.name = sv, name
        sv._load_signal(name)
        assert sv.cur_an is not None, name

    @property
    def an(self):
        return self.sv.cur_an

    @property
    def cols(self):
        return self.sv.cur_cols

    @property
    def e_inputs(self):
        return self.sv.e_inputs

    def ncols(self):
        return len(self.sv.cur_cols)

    def set_cell(self, r, c, txt):
        it = self.sv.truth.item(r, c)
        assert it is not None, (r, c)
        it.setText(txt)

    def set_exp(self, c, txt):
        self.set_cell(len(self.sv.e_inputs) + 1, c, txt)

    def add_col(self):
        self.sv._e_add()

    def copy_col(self, c):
        self.sv.truth.selectColumn(c)
        assert self.sv._sel_cols() == [c]
        self.sv._e_copy()

    def add_neg(self, c):
        self.sv.truth.selectColumn(c)
        n = self.ncols()
        self.sv._e_addneg()
        assert self.ncols() == n + 1, "v1 那边没加上反例，这条对照会变成拿空比空"

    def rename(self, c, nm):
        ok, info = ED.rename_col(self.sv.cur_cols, c, nm)
        assert ok, info
        self.sv._commit()

    def set_mux_data(self, base, width, txt):
        self.sv._set_mux_data(base, width, txt)

    def set_mux_user_data(self, c, e, txt):
        self.sv._set_mux_user_data(self.sv.cur_cols[c], e, txt)

    def edited(self):
        return {k: v for k, v in self.sv._compute_edited().items()
                if k == self.name.lower()}


class _V2Edit(object):
    """在 v2 `TruthModel` 上做同一串编辑（同一个 provider、同一组 mode/max_tests）。"""

    def __init__(self, prov, name, mode, maxt, exh):
        self.prov, self.name = prov, name
        self.mode, self.maxt, self.exh = mode, maxt, exh
        self.an = prov.analyze(name, mode, maxt, exh)
        assert self.an is not None, name
        self.e_inputs = e_inputs_from_an(self.an)
        self.bucket = {}                      # = state.mux_data()
        self.m = TruthModel()
        self.m.load(self.an, ED.cols_from_vectors(self.an, self.e_inputs), self.e_inputs)
        self.m.set_reanalyzer(self._reanalyze)

    def _reanalyze(self, base_low, text):
        w = next(e["width"] for e in self.e_inputs
                 if (e["mux_data_base"] or "") == base_low)
        ED.set_mux_data_value(self.bucket, self.name.lower(),
                              self.an["src_out_name"].lower(), self.an["name"],
                              base_low, w, text)
        ent = self.bucket.get(self.name.lower())
        data = dict(ent["data"]) if ent and ent.get("data") else None
        self.an = self.prov.analyze(self.name, self.mode, self.maxt, self.exh, mux_data=data)
        return self.an

    @property
    def cols(self):
        return self.m.cols()

    def ncols(self):
        return self.m.columnCount()

    def set_cell(self, r, c, txt):
        assert self.m.setData(self.m.index(r, c), txt) is True, (r, c, txt)

    def set_exp(self, c, txt):
        self.set_cell(self.m.rowCount() - 1, c, txt)

    def add_col(self):
        assert self.m.append_test_column(1)

    def copy_col(self, c):
        at, _nm = self.m.duplicate_column(c)
        assert at >= 0

    def add_neg(self, c):
        made, _skipped = self.m.add_negatives([c])
        assert made == 1

    def rename(self, c, nm):
        ok, info = self.m.rename_column(c, nm)
        assert ok, info

    def set_mux_data(self, base, width, txt):
        assert self.m.set_mux_data_value(base, txt) is True

    def set_mux_user_data(self, c, e, txt):
        assert self.m.set_mux_user_data(c, e["key"], txt) is True

    def edited(self):
        an = self.an
        rec = {self.name.lower(): {"kind": an["kind"], "src_out_name": an["src_out_name"],
                                   "name": an["name"], "renamed": an.get("renamed", False),
                                   "cols": self.m.cols(), "an": an}}
        return ED.compute_edited(rec, self.bucket)


def _first_editable_e(p):
    return next(i for i, e in enumerate(p.e_inputs) if ED.input_cell_editable(p.an, e))


def _diff_from_auto(p, c):
    """给这一列挑一个**与兜底值不同**的期望文本。

    v1 的期望格在未手填时显示的就是 auto_out 兜底值，往里填同一个值 → `setText` 根本不发
    `itemChanged` → 这一格在 v1 压根没记成手填（v2 分得清「未填(兜底)」与「手填了同一个值」，
    见 `TruthRole.IS_FALLBACK` 与下面 `test_c078_..._equal_to_auto` 那条）。
    逐字节对照要比的是两边都做得到的编辑，所以这里避开这个坑。
    """
    col = p.cols[c]
    return str((col["auto"] ^ 1) & ((1 << (col["auto_w"] or 1)) - 1))


def _script_logic(p):
    """logic 信号：加列 → 改输入格 → 手填期望 → 加反例 → 重命名手编列。"""
    p.add_col()
    c = p.ncols() - 1
    r = _first_editable_e(p)
    p.set_cell(r, c, "1")
    p.set_exp(c, _diff_from_auto(p, c))
    p.set_exp(0, _diff_from_auto(p, 0))
    p.add_neg(0)
    p.rename(c, "my_case")


def _script_mux(p):
    """mux 信号：整表 mux 数据值改一次 → 手编列改 data 格 → 加反例。"""
    e = next(x for x in p.e_inputs if x["mux_data_base"])
    p.set_mux_data(e["mux_data_base"], e["width"], "0x5")
    p.copy_col(0)
    c = p.ncols() - 1
    e2 = next(x for x in p.e_inputs if x["mux_data_base"])   # v1 重析后 e_inputs 换了对象
    p.set_mux_user_data(c, e2, "0b0011")
    p.add_neg(0)


def _script_dft(p):
    """iddq/dft 信号：改期望 + 加反例；**自检拍列只读**，两边都不碰它（v2 的只读另有断言）。"""
    cs = [i for i, c in enumerate(p.cols) if not ED.is_dft_pitch_col(p.an, c)]
    p.set_exp(cs[0], _diff_from_auto(p, cs[0]))
    p.set_exp(cs[1], _diff_from_auto(p, cs[1]))
    p.add_neg(cs[0])


#: (mirror, 信号, 编辑脚本)。三组分别覆盖 logic / mux / iddq 三条路
_ROUNDTRIP = (
    ("btlp", LOGIC_SIG, _script_logic),
    ("btlp", MUX_SIG, _script_mux),
    ("wl", GATED_SIG, _script_dft),
)


@pytest.mark.contract("C-132")
def test_c132_edit_roundtrip_matches_v1(monkeypatch, tmp_path):
    """C-132（完成标准）：在 v1 `SignalView` 与 v2 `TruthModel` 上做**同一串编辑**，
    两边各自 `edits.compute_edited` → `exports.render_sv`，.sv 文本**逐字节相同**。

    比的是整条链：列模型 → `cols_to_vectors` / `mux_derive`（+ mux 数据值会话档）→ 生成器。
    v2 多出来的那层（撤销命令 / `cols()` 快照 / 列 schema 补齐 / mux 重分析后 resync）只要
    有一处偏了，这里就会红。两张 mirror 都跑。
    """
    from dreg_verify import exports as X
    H.isolate_settings(monkeypatch, tmp_path)
    H.auto_dialogs(monkeypatch)
    diffs, n_run = [], 0
    for kind in ("btlp", "wl"):
        w = H.make_window(kind=kind)
        try:
            sv = w.topout_view
            for k, name, script in _ROUNDTRIP:
                if k != kind:
                    continue
                mode, exh = sv._mode_for(name)
                maxt = sv._maxt()

                sv.edits.clear()                 # 每组从干净态起（v1 的 edits 是整个视图共享的）
                sv._mux_data.clear()
                p1 = _V1Edit(sv, name)
                script(p1)
                t1, _b1 = X.render_sv(sv.provider, only=[name], mode=mode, max_tests=maxt,
                                      exhaustive=exh, edited=p1.edited())

                p2 = _V2Edit(sv.provider, name, mode, maxt, exh)
                script(p2)
                t2, _b2 = X.render_sv(sv.provider, only=[name], mode=mode, max_tests=maxt,
                                      exhaustive=exh, edited=p2.edited())

                n_run += 1
                assert t1 and name in t1 and "my_case" in t1 or name in t1
                assert p1.ncols() == p2.ncols(), \
                    "%s：列数就对不上（v1 %d / v2 %d）" % (name, p1.ncols(), p2.ncols())
                if t1 != t2:
                    import difflib
                    d = list(difflib.unified_diff(t1.splitlines(), t2.splitlines(),
                                                  "v1", "v2", lineterm=""))
                    diffs.append("%s / %s：\n%s" % (kind, name, "\n".join(d[:40])))
                    continue

                # 变异自证：这条比较**判得出**差异（不是两边都空 / 都一样的假绿）
                p2.set_exp(0, "1" if p2.cols[0]["exp"] != 1 else "0")
                t2b, _b = X.render_sv(sv.provider, only=[name], mode=mode, max_tests=maxt,
                                      exhaustive=exh, edited=p2.edited())
                assert t2b != t1, "%s：改了一格期望 .sv 却没变，这条逐字节对照等于没验" % name
        finally:
            w.close()
    assert n_run == len(_ROUNDTRIP), "只跑了 %d 组编辑序列" % n_run
    assert not diffs, "v1 / v2 的 .sv 不是逐字节相同：\n\n%s" % "\n\n".join(diffs)


@pytest.mark.contract("C-078", "C-079")
def test_c078_designer_expected_equal_to_auto_is_recorded(btlp):
    """C-078/C-079：手填的期望**恰好等于** auto_out 时也算「已填」（绿），不是「未填」（灰）。

    这也是 C-132 那条对照绕开的一处 v1 局限：v1 的期望格未填时显示的就是兜底值，往里填同一个值
    `setText` 不发 `itemChanged`，designer 的「我核对过、就是这个值」在 v1 GUI 里录不进去
    （v1 只有走「auto→期望」批量填才录得进去）。v2 的 model 分得清这两态。
    """
    from dreg_verify import exports as X
    m, an, _ei = _fresh(btlp, LOGIC_SIG)
    exp_r = _exp_row(m)
    auto0 = m.cols()[0]["auto"]
    assert m.cell_state(exp_r, 0) == "unfilled"
    assert m.data(m.index(exp_r, 0), int(contracts.TruthRole.IS_FALLBACK)) is True
    assert m.setData(m.index(exp_r, 0), str(auto0)) is True
    assert m.cols()[0]["exp"] == auto0
    assert m.cell_state(exp_r, 0) == "match"
    assert m.data(m.index(exp_r, 0), int(contracts.TruthRole.IS_FALLBACK)) is False
    # 落到 .sv 上：多一行 designer 手填的注记（= 这条断言有人核对过，不是程序自说自话）
    v = ED.cols_to_vectors(an, m.cols())[0]
    assert v.designer_expected == auto0 and not v.is_negative
    assert m.fill_progress()[0] == 1


@pytest.mark.contract("C-129", "C-130")
def test_c132_dft_pitch_col_is_readonly_in_v2(wl):
    """C-132 的 iddq 那一组里「拍列只读」是 v2 的**改进**（v1 的期望格可编辑）——单独钉一条。

    所以上面那条逐字节对照里两边都不碰自检拍列：拿 v1 改不了的东西去比字节，比的是 v1 的 bug。
    """
    m, an, _ei = _fresh(wl, GATED_SIG)
    dc = next(c for c in range(m.columnCount())
              if m.col_state(c) == contracts.TruthColState.DFT)
    exp_r = _exp_row(m)
    before = _dump(m)
    assert not (m.flags(m.index(exp_r, dc)) & _ITEM_EDITABLE)
    assert m.setData(m.index(exp_r, dc), "1") is False
    assert _dump(m) == before and m.undo_stack().count() == 0
