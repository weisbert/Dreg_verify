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

    # C-110 整表同步这一波不实现，但方法名必须在，且自动生成列的数据格写入不许静默落半个
    with pytest.raises(NotImplementedError):
        m.set_mux_data_value("whatever", "1")
    got_msgs = []
    m.parseFailed.connect(got_msgs.append)
    assert m.setData(m.index(data_r, 0), "0b0011") is False    # 0 号列是自动生成列
    assert len(got_msgs) == 1 and got_msgs[0]


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

    # 读文件那半边归 C3-d
    with pytest.raises(NotImplementedError):
        m.import_expectations("nope.csv")


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
    allowed = {"edits", "truth_edit", "inputs_table", "analysis_norm",
               "contracts", "names", "terms", "commands", "rows", "model", "io"}
    #: 视图层（C3-b）另外准 import `theme` / `widgets` —— 架构 §6.13：底色字色**只**在
    #: delegate/表头里按 role 查 theme 的表。model 那半边照旧不许（它只给 CELL_STATE 键名）。
    view_extra = {"theme", "widgets", "delegate", "view", "panel"}
    view_files = {"view.py", "delegate.py", "panel.py"}
    bad = []
    for fn in sorted(os.listdir(src_dir)):
        if not fn.endswith(".py"):
            continue
        ok = allowed | (view_extra if fn in view_files else set())
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
