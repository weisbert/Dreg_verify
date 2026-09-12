# -*- coding: utf-8 -*-
"""test_ui_truth_io.py —— 真值表的剪贴板 / 导入 / 导出（C3-d）：`dreg_verify/ui/truth/io.py`。

契约 ID 写在函数名里（`tools/contract_check.py` 按 `c\\d{3}` 反查）：
C-136 / C-137 / C-138 / C-139 / C-294 / C-298（TRUTH_IO 6 条）。

夹具一律走 `ui_harness.mirror_path`（公开仓，代码/测试里**绝不出现真实信号名**）。
`io.py` 自己 Qt-free，但它吃的 model 是 `QAbstractTableModel` —— 所以这里照
`test_ui_truth_model.py` 的办法起 `QApplication`（不建窗口），只有两条 v1 对照测试
要起 v1 的 `SignalView`。
"""

import ast
import io as _io
import os
import re
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import ui_harness as H                                  # noqa: E402
from dreg_verify import edits as ED                     # noqa: E402
from dreg_verify import excel_model as M                # noqa: E402
from dreg_verify import exports as X                    # noqa: E402
from dreg_verify import providers as PV                 # noqa: E402
from dreg_verify import truth_edit as TE                # noqa: E402

pytest.importorskip("PySide6")

from dreg_verify.ui import terms                        # noqa: E402
from dreg_verify.ui.truth import TruthModel, e_inputs_from_an          # noqa: E402
from dreg_verify.ui.truth import io as IO               # noqa: E402
from dreg_verify.ui.truth import model as TM            # noqa: E402

#: mirror 上挑出来的对照信号（与 `test_ui_truth_model.py` 同一批，`probe` 过）
LOGIC_SIG = "d_logic_bt_lp_rx_en"        # btlp：logic 根
MUX_SIG = "d_bt_lp_lna_itrim"            # btlp：mux 根
GATED_SIG = "d_wl_rf_lo2g5g_bias_en"     # wl  ：logic 根 + iddq 门（C-128…C-130 那条）
MUX_GATED_SIG = "d_wl_rf_lo2g5g_mixer2g_trim"   # wl：mux 根 + iddq 门


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


def _prov(wb):
    return PV.TopoutProvider(_Cfg(wb))


def _fresh(wb, name):
    """(model, an, provider) —— 载好一个信号的真值表（列 = 出厂默认）。"""
    prov = _prov(wb)
    an = prov.analyze(name, "min", 64, False)
    assert an is not None, "mirror 上没有信号 %s（夹具挑错了）" % name
    ei = e_inputs_from_an(an)
    m = TruthModel()
    m.load(an, ED.cols_from_vectors(an, ei), ei)
    return m, an, prov


def _dump(m):
    """整表逐格快照（比对「按 plan 落格」与「直接 paste_tsv」的基准）。"""
    return ([[m.data(m.index(r, c)) for c in range(m.columnCount())]
             for r in range(m.rowCount())],
            m.all_names(),
            [m.col_state(c) for c in range(m.columnCount())])


def _exp_row(m):
    return m.rowCount() - 1


def _first_editable_input_row(m):
    for r in range(m.rowCount() - 2):
        if IO._cell_editable(m, r, 0):
            return r
    raise AssertionError("这个信号一条可编辑输入行都没有，挑错夹具了")


def _apply_plan(m, plan):
    """按 plan 用 model 的**公开写入口**落格（C3-int 里 `paste_tsv` 会做的事）。"""
    added = m.append_test_column(plan.new_cols) if plan.new_cols else []
    for (r, c, txt) in plan.cells:
        m.setData(m.index(r, c), txt)
    return added


#: 报告文案里【两边一定一致】的那一截：「粘贴落了 N 格」+「，新增列 …」
_RE_REPORT_HEAD = re.compile(r"^粘贴落了 \d+ 格(，新增列 [^，]*)?")


def _report_head(s):
    """⚠ C3-a（2026-09-12）起「跳过」那一截两边口径不同，**只比得了这个头**。

    `model.paste_tsv` 现在按原因分桶逐条点名（只读 / 右边没有测试列（原因）/ 自动生成列的
    mux 数据值要整表改 / 写法没认出来 + 逐格名字）；`io.paste_plan` 还是一句「跳过 N 格
    （只读行/列）」、把逐格原因留在 `plan.skipped` 里。**C3-int 把两个报告构造函数合成一份
    之后，请把下面的 `_report_head(...)` 比较改回 `plan.report_text == msg` 逐字相等。**
    行为等价（落了哪几格）照旧由 `_dump(ma) == _dump(mb)` 逐格钉死，没有放水。
    """
    m = _RE_REPORT_HEAD.match(str(s or ""))
    return m.group(0) if m else str(s or "")


# ═══════════════════ TSV：解析 / 复制 ═══════════════════
def test_parse_tsv_excel_dialect():
    """Excel 口径：三种换行都认、制表符分列、**中间空串格保留**、只剔末尾空行。"""
    assert IO.parse_tsv("a\tb\r\nc\td\n") == [["a", "b"], ["c", "d"]]
    assert IO.parse_tsv("a\tb\rc\td") == [["a", "b"], ["c", "d"]]
    assert IO.parse_tsv("a\t\tb") == [["a", "", "b"]]         # 中间空格子不许被吃掉
    assert IO.parse_tsv("a\nb\n\n\n") == [["a"], ["b"]]       # 末尾空行剔干净
    assert IO.parse_tsv("\na") == [[""], ["a"]]               # 开头的空行是真的一行
    assert IO.parse_tsv("") == [] and IO.parse_tsv(None) == []
    assert IO.parse_tsv("a") == [["a"]]
    # 行长不齐照实保留（补齐是 paste_plan 的事，不是解析的事）
    assert IO.parse_tsv("a\tb\nc") == [["a", "b"], ["c"]]


def test_copy_tsv_matches_model(btlp):
    """`io.copy_tsv` 与 `TruthModel.copy_tsv` 逐字相同（选区外接矩形、空格子留空串）。"""
    m, _an, _p = _fresh(btlp, LOGIC_SIG)
    assert m.columnCount() >= 3 and m.rowCount() >= 4
    cases = [
        [(0, 0), (0, 1), (1, 0), (1, 1)],                       # 实心矩形
        [(0, 0), (2, 2)],                                       # 稀疏选区 → 外接矩形补空串
        [(_exp_row(m), 0), (_exp_row(m), 1)],                   # 期望行
        [],                                                     # 空选区
    ]
    for sel in cases:
        ixs = [m.index(r, c) for (r, c) in sel]
        assert IO.copy_tsv(m, ixs) == m.copy_tsv(ixs), sel
    # 稀疏选区确实补了空串（不是恰好两边都返回空）
    got = IO.copy_tsv(m, [m.index(0, 0), m.index(2, 2)])
    assert got.count("\n") == 2 and got.splitlines()[1] == "\t\t"
    # 无效 index 两边都跳过
    assert IO.copy_tsv(m, [m.index(0, 0), m.index(999, 999)]) == \
        m.copy_tsv([m.index(0, 0), m.index(999, 999)])


# ═══════════════════ C-294 粘贴规划 ═══════════════════
@pytest.mark.contract("C-294")
def test_c294_paste_plan_overflow_rules(btlp, wl):
    """超列 → 追加列；**超行 → 整次拒绝**（真值表的行是输入信号，不能凭空加）；只读格跳过。"""
    m, _an, _p = _fresh(btlp, LOGIC_SIG)
    n_rows, n_cols = m.rowCount(), m.columnCount()
    r_in = _first_editable_input_row(m)

    # ① 落得下 → 不追加列、不拒行
    p = IO.paste_plan(m, "1\t0", r_in, 0)
    assert p.ok and p.new_cols == 0 and p.rejected_rows == []
    assert [(r, c) for (r, c, _t) in p.cells] == [(r_in, 0), (r_in, 1)]

    # ② 超列 → new_cols 说要加几条，报告里点名新列
    wide = "\t".join(["1"] * (n_cols + 2))
    p = IO.paste_plan(m, wide, r_in, 0)
    assert p.new_cols == 2 and p.ok
    assert len(p.cells) == n_cols + 2
    assert IO.plan_added_names(m, 2)[0] in p.report_text

    # ③ 从第 2 列开始贴 → 追加数按落点算
    p = IO.paste_plan(m, "1\t1", r_in, n_cols - 1)
    assert p.new_cols == 1

    # ④ 超行 → 整次拒绝：一格都不落，报告是 OVERFLOW 那句
    tall = "\n".join(["1"] * (n_rows + 1))
    p = IO.paste_plan(m, tall, 0, 0)
    assert not p.ok and p.cells == [] and p.new_cols == 0
    assert p.rejected_rows == [n_rows]          # 只有最后一源行落不下
    assert p.report_text == terms.TRUTH_PASTE_OVERFLOW_ROWS_FMT.format(rows=n_rows + 1,
                                                                      max=n_rows)
    # ⑤ 正好贴满最后一行 = 不算超行（边界：`>` 不是 `>=`）
    just = "\n".join(["1"] * n_rows)
    assert IO.paste_plan(m, just, 0, 0).ok

    # ⑥ 只读格跳过并给原因，别的格照落（auto_out 行恒只读）
    auto_r = m.rowCount() - 2
    p = IO.paste_plan(m, "1\n1", auto_r, 0)
    assert len(p.skipped) == 1 and p.skipped[0][0] == auto_r
    assert len(p.cells) == 1 and p.cells[0][0] == auto_r + 1
    assert "跳过 1 格" in p.report_text

    # ⑦ 写法认不出来的那一格跳过，整次粘贴照常落别的格（C-083 同一条原则）
    p = IO.paste_plan(m, "1\t零", r_in, 0)
    assert len(p.cells) == 1 and len(p.skipped) == 1
    assert "零" in p.skipped[0][2]

    # ⑧ 不可编辑信号（C-134）：一格都落不下
    mg, _an2, _p2 = _fresh(wl, GATED_SIG)
    ro = TruthModel()
    ro.load({"editable": ""}, mg.cols(), [])
    p = IO.paste_plan(ro, "1", 0, 0)
    assert p.cells == [] and p.skipped


@pytest.mark.contract("C-294")
def test_c294_paste_plan_equivalent_to_model_paste(btlp, wl):
    """**等价性**：按 plan 用 model 公开方法落格 == 直接 `model.paste_tsv`，逐格 + 报告文案。

    覆盖：落在活动格 / 超列追加 / 超行拒绝 / 只读格跳过 / 认不出来的写法 / 空串清期望 /
    锯齿行（行长不齐）/ 空文本，两条信号（普通 logic + 带 iddq 门的 logic）各跑一遍。
    """
    for wb, sig in ((btlp, LOGIC_SIG), (wl, GATED_SIG)):
        m0, _an, _p = _fresh(wb, sig)
        n_rows, n_cols = m0.rowCount(), m0.columnCount()
        r_in = _first_editable_input_row(m0)
        exp_r = _exp_row(m0)
        cases = [
            ("1\t0", r_in, 0),                                   # 普通两格
            ("1\t0\t1\t0\t1\t0", r_in, 0),                       # 超列追加
            ("\n".join(["1"] * (n_rows + 2)), 0, 0),             # 超行拒绝
            ("1\n1\n1", m0.rowCount() - 3, 0),                   # 撞上 auto_out 只读行
            ("1\t零\t0", r_in, 0),                                # 中间一格写法不认
            ("", 0, 0),                                          # 空文本
            ("\n\n", 0, 0),                                      # 只有空行
            ("0x1\t\t0b1", exp_r, 0),                            # 期望行：空串 = 清空
            ("1\t0\n0", r_in, 0),                                # 锯齿行
            ("1\t1\t1", r_in, n_cols - 1),                       # 落点靠右 → 部分追加
        ]
        for text, r0, c0 in cases:
            ma, _a, _pa = _fresh(wb, sig)
            mb, _b, _pb = _fresh(wb, sig)
            plan = IO.paste_plan(ma, text, r0, c0)
            _apply_plan(ma, plan)
            ok, msg = mb.paste_tsv(text, r0, c0)
            assert plan.ok == ok, (sig, text, r0, c0)
            assert _report_head(plan.report_text) == _report_head(msg), \
                (sig, text, r0, c0, plan.report_text, msg)
            assert bool(plan.skipped) == (_report_head(msg) != msg), \
                "一边说跳过了、另一边没说：%r / %r" % (plan.report_text, msg)
            assert _dump(ma) == _dump(mb), (sig, text, r0, c0)
    # 确实测到了「追加列」和「拒绝」两条路，不是全走了平凡分支
    m, _an, _p = _fresh(btlp, LOGIC_SIG)
    assert IO.paste_plan(m, "1\t0\t1\t0\t1\t0", 0, 0).new_cols > 0
    assert IO.paste_plan(m, "\n".join(["1"] * (m.rowCount() + 2)), 0, 0).rejected_rows


@pytest.mark.contract("C-294")
def test_c294_paste_plan_equivalent_on_cleared_table(btlp, wl):
    """清零（C-089 零用例）之后从 Excel 整片粘回来 —— 表上一条参照列都没有的那一档。

    logic 能靠 `add_col` 现加列（门行仍只读）；mux 的 `copy_cols` 没有源列可复制，
    一条也加不出来 —— 两种都要与 `model.paste_tsv` 逐格相同。
    """
    for wb, sig in ((btlp, LOGIC_SIG), (wl, GATED_SIG), (btlp, MUX_SIG)):
        ma, _a, _pa = _fresh(wb, sig)
        mb, _b, _pb = _fresh(wb, sig)
        ma.clear_all()
        mb.clear_all()
        assert ma.columnCount() == 0 and mb.columnCount() == 0
        text = "\n".join("\t".join(["1"] * 3) for _ in range(ma.rowCount() - 2))
        plan = IO.paste_plan(ma, text, 0, 0)
        _apply_plan(ma, plan)
        ok, msg = mb.paste_tsv(text, 0, 0)
        assert plan.ok == ok, sig
        assert _report_head(plan.report_text) == _report_head(msg), \
            (sig, plan.report_text, msg)
        assert bool(plan.skipped) == (_report_head(msg) != msg), (sig, plan.report_text, msg)
        assert _dump(ma) == _dump(mb), sig
    # 清零后的 logic 确实落进去了东西（不是两边都空所以恰好相等）
    m, _an, _p = _fresh(btlp, LOGIC_SIG)
    m.clear_all()
    assert IO.paste_plan(m, "1\t1", 0, 0).cells


def test_plan_added_names_matches_model(btlp):
    """新列名的**预测**（规划期要它才报得出「新增列 U0, U1」）与真 `append_test_column` 相同。"""
    for sig in (LOGIC_SIG, MUX_SIG):
        m, _an, _p = _fresh(btlp, sig)
        want = IO.plan_added_names(m, 3)
        got = m.append_test_column(3)
        assert want == got, sig
        assert len(got) == 3 and len(set(got)) == 3
        # 加过一轮之后再预测一次（名字要接着往下排，不能又给 U0）
        assert IO.plan_added_names(m, 2) == m.append_test_column(2)
    # 不可编辑信号：一条也加不出来，预测同样给空（别报个假名字）
    ro = TruthModel()
    ro.load({"editable": ""}, [], [])
    assert IO.plan_added_names(ro, 3) == ro.append_test_column(3) == []


def test_cell_editable_matches_model_flags(btlp, wl):
    """`io._cell_editable`（只经 Proto 的公开面）与 `model.flags()` 的 ItemIsEditable 逐格相同。"""
    from PySide6.QtCore import Qt
    n = 0
    for wb, sig in ((btlp, LOGIC_SIG), (btlp, MUX_SIG),
                    (wl, GATED_SIG), (wl, MUX_GATED_SIG)):
        m, _an, _p = _fresh(wb, sig)
        for r in range(m.rowCount()):
            for c in range(m.columnCount()):
                want = bool(m.flags(m.index(r, c)) & Qt.ItemIsEditable)
                assert IO._cell_editable(m, r, c) is want, (sig, r, c)
                n += 1
    assert n > 200, "只比了 %d 格，覆盖太窄" % n
    # 越界一律不可写（两边都不许抛）
    m, _an, _p = _fresh(btlp, LOGIC_SIG)
    assert IO._cell_editable(m, -1, 0) is False
    assert IO._cell_editable(m, 0, m.columnCount()) is False


def test_paste_report_terms_live_only_in_terms_py():
    """C3-int：粘贴 / 导入期望的文案**只有 terms.py 一份**，io 与 model 都不许再留影子表。

    以前这条守的是「io 与 model 的两张 `PENDING_TERMS` 同名同值」；现在两张表都没了，
    守的就是「一处定义」本身 —— 谁再在模块里写一份兜底，这条当场红。
    """
    for mod in (IO, TM):
        for attr in ("PENDING_TERMS", "PENDING_NAMES"):
            assert not hasattr(mod, attr), "%s.%s 还在，说明文案又有两份" % (mod.__name__, attr)
    for key in ("TRUTH_PASTE_ADDED_FMT", "TRUTH_PASTE_SKIPPED_FMT", "TRUTH_PASTE_REPORT_FMT",
                "TRUTH_PASTE_SKIP_READONLY", "TRUTH_PASTE_SKIP_PARSE_FMT",
                "TRUTH_IMPORT_EXP_REPORT_FMT", "TRUTH_IMPORT_MISSING_FMT"):
        assert isinstance(getattr(terms, key), str) and getattr(terms, key)


@pytest.mark.contract("C-298")
def test_c298_import_report_names_before_counts(btlp):
    """I-20：导入期望的结果说明**名字在前、计数在后**，且 model 与面板共用这一句。

    C3-int 之前 `model.import_expectations` 拼的是「按列名回填了 N 列的期望；这些列名…」
    （计数在前），面板另拼了一份名字在前的 —— 现在只剩 `io.import_report_text` 一份。
    """
    msg = IO.import_report_text(2, ["T_NOT_HERE", "T_ALSO_GONE"], ["第一行是空的"])
    assert msg.index("第一行是空的") < msg.index("T_NOT_HERE") < msg.index("2 列的期望")
    assert "共 2 个" in msg and msg.endswith(terms.TRUTH_IMPORT_EXP_REPORT_FMT.format(missing="", n=2))
    # 什么都没跳过时只剩计数那半句（不留空的分号）
    assert IO.import_report_text(1) == terms.TRUTH_IMPORT_EXP_REPORT_FMT.format(missing="", n=1)
    assert not IO.import_report_text(0).startswith("；")
    # model 走的是同一句（逐字比）
    m, _an, _p = _fresh(btlp, LOGIC_SIG)
    n, missing = m.apply_expectations({"T_NOT_HERE": 1})
    assert n == 0 and missing == ["T_NOT_HERE"]
    assert IO.import_report_text(n, missing, []) ==         terms.TRUTH_IMPORT_EXP_REPORT_FMT.format(
            missing=terms.TRUTH_IMPORT_MISSING_FMT.format(names="T_NOT_HERE", n=1) + "；", n=0)


# ═══════════════════ C-298 导入期望 ═══════════════════
def _write(path, rows, bom=True):
    import csv as _csv
    with open(path, "w", encoding=("utf-8-sig" if bom else "utf-8"), newline="") as f:
        _csv.writer(f).writerows(rows)
    return str(path)


@pytest.mark.contract("C-298")
def test_c298_import_csv_xlsx_by_column_name_reports_missing(btlp, tmp_path):
    """CSV / xlsx 按列名读期望 → `model.apply_expectations` 落格 + **点名没对上的列**。"""
    openpyxl = pytest.importorskip("openpyxl")
    m, _an, _p = _fresh(btlp, MUX_SIG)
    names = m.all_names()
    assert len(names) >= 3
    exp_r = _exp_row(m)
    w = (m.cols()[0].get("auto_w") or 1)
    v0, v1 = 1 & ((1 << w) - 1), 2 & ((1 << w) - 1)

    # ── CSV：导出的那张表原样改（首行列名 + 「期望(进.sv)」行），IC 工程师那几种写法都认 ──
    rows = [["信号\\测试", names[0], names[1], "T_NOT_HERE"],
            ["某输入", "0", "0", "0"],
            ["auto_out", "0x0", "0x0", "0x0"],
            ["期望(进.sv)", "0x%X" % v0, "%d'b%s" % (w, format(v1, "0%db" % w)), "0x1"],
            ["期望来源", "designer手填", "designer手填", "designer手填"]]
    p_csv = _write(tmp_path / "exp.csv", rows)
    by_name, notes = IO.read_expectations(p_csv)
    assert by_name == {names[0]: v0, names[1]: v1, "T_NOT_HERE": 1}, by_name
    assert notes == []
    n, missing = m.apply_expectations(by_name)
    assert n == 2 and missing == ["T_NOT_HERE"]          # C-298：没对上的列名要点名
    assert m.data(m.index(exp_r, 0)) == TM.fmt_cell(v0, w)
    assert m.data(m.index(exp_r, 1)) == TM.fmt_cell(v1, w)

    # ── xlsx：openpyxl 惰性 import，只读第一张表；数字格直接当十进制 ──
    m2, _an2, _p2 = _fresh(btlp, MUX_SIG)
    wbx = openpyxl.Workbook()
    ws = wbx.active
    ws.append(["信号\\测试", names[0], names[1], "T_NOT_HERE"])
    ws.append(["auto_out", 0, 0, 0])
    ws.append(["期望(进.sv)", int(v0), int(v1), 1])
    wbx.create_sheet("别的表").append(["信号\\测试", "别读我"])
    p_xlsx = str(tmp_path / "exp.xlsx")
    wbx.save(p_xlsx)
    by2, notes2 = IO.read_expectations(p_xlsx)
    assert by2 == {names[0]: v0, names[1]: v1, "T_NOT_HERE": 1}, by2
    assert notes2 == []
    n2, missing2 = m2.apply_expectations(by2)
    assert (n2, missing2) == (2, ["T_NOT_HERE"])

    # ── 一步撤销（C-299）：导入期望整次算一步 ──
    m3, _an3, _p3 = _fresh(btlp, MUX_SIG)
    before = m3.undo_stack().count()
    m3.apply_expectations(by_name)
    assert m3.undo_stack().count() == before + 1


@pytest.mark.contract("C-298")
def test_c298_import_notes_name_the_problem(btlp, tmp_path):
    """读文件这半边的提示：逐条给名字 + 原因，不静默吞。"""
    m, _an, _p = _fresh(btlp, LOGIC_SIG)
    names = m.all_names()
    # ① 某一列写法认不出来 → 点名那一列，其余照落
    p = _write(tmp_path / "bad.csv", [["信号\\测试", names[0], names[1]],
                                      ["期望(进.sv)", "1", "零"]])
    by, notes = IO.read_expectations(p)
    assert by == {names[0]: 1} and len(notes) == 1
    assert names[1] in notes[0] and "零" in notes[0]
    # ② 空格 = 这一列不动（不是「填 0」）
    p = _write(tmp_path / "blank.csv", [["信号\\测试", names[0], names[1]],
                                        ["期望(进.sv)", "1", ""]])
    by, notes = IO.read_expectations(p)
    assert by == {names[0]: 1} and notes == []
    # ③ 没有「期望」行、只有一行数据 → 按那一行取，并说一声
    p = _write(tmp_path / "one.csv", [["信号\\测试", names[0]], ["我的期望", "1"]])
    by, notes = IO.read_expectations(p)
    assert by == {names[0]: 1} and len(notes) == 1 and "我的期望" in notes[0]
    # ④ 没有「期望」行、又不止一行 → 一个值都不取，说清楚为什么
    p = _write(tmp_path / "none.csv", [["信号\\测试", names[0]], ["a", "1"], ["b", "2"]])
    by, notes = IO.read_expectations(p)
    assert by == {} and notes == [terms.TRUTH_IMPORT_NO_EXP_ROW]
    # ⑤ 「期望来源」那行长得像但不是取值行，不许被当成期望
    p = _write(tmp_path / "src.csv", [["信号\\测试", names[0]],
                                      ["期望来源", "auto_out兜底"],
                                      ["期望(bin)", "1'b1"]])
    by, notes = IO.read_expectations(p)
    assert by == {names[0]: 1} and notes == []
    # ⑥ 列名重复 → 只取第一处并说一声
    p = _write(tmp_path / "dup.csv", [["信号\\测试", names[0], names[0]],
                                      ["期望(进.sv)", "1", "0"]])
    by, notes = IO.read_expectations(p)
    assert by == {names[0]: 1} and len(notes) == 1 and names[0] in notes[0]
    # ⑦ 没有 BOM 也读得了；空文件 / 没有列名都不许抛
    p = _write(tmp_path / "nobom.csv", [["信号\\测试", names[0]], ["期望(进.sv)", "1"]],
               bom=False)
    assert IO.read_expectations(p)[0] == {names[0]: 1}
    p = _write(tmp_path / "empty.csv", [])
    assert IO.read_expectations(p) == ({}, [terms.TRUTH_IMPORT_EMPTY_FILE])
    p = _write(tmp_path / "nocol.csv", [["信号\\测试"], ["期望(进.sv)"]])
    assert IO.read_expectations(p) == ({}, [terms.TRUTH_IMPORT_NO_COLUMNS])


def test_import_roundtrips_the_exported_csv(btlp, tmp_path):
    """导出的 CSV 原样读回来 = 每一列的期望都对得上（导出/导入是同一个文件格式）。"""
    m, an, prov = _fresh(btlp, LOGIC_SIG)
    m.fill_expected()                      # 先把 auto_out 填进期望，才有值可对
    p = str(tmp_path / "rt.csv")
    IO.export_signal_csv(p, m, an, LOGIC_SIG, provider=prov)
    by, notes = IO.read_expectations(p)
    assert notes == [] and set(by) == set(m.all_names())
    exp_r = _exp_row(m)
    for c, nm in enumerate(m.all_names()):
        assert TM.fmt_cell(by[nm], m.cols()[c].get("auto_w") or 1) == m.data(m.index(exp_r, c))
    n, missing = m.apply_expectations(by)
    assert missing == [] and n == 0          # 值没变 → 一格都不用改


# ═══════════════════ C-136 / C-137 / C-138 单信号 CSV ═══════════════════
def _csv_rows(text):
    import csv as _csv
    return list(_csv.reader(_io.StringIO(text, newline="")))


@pytest.mark.contract("C-136", "C-137", "C-138")
def test_c136_c137_c138_signal_csv_rows(btlp, wl, tmp_path):
    """转置排版（每列一条测试）+ 期望(bin) / force / RF_WRITE（C-137）
    + 期望来源 / 负向?（C-138）五行都在，取值对得上。"""
    m, an, prov = _fresh(btlp, LOGIC_SIG)
    m.add_negatives([0])                                  # 造一条反例，才验得到「负向?」行
    p = str(tmp_path / "sig.csv")
    text = IO.export_signal_csv(p, m, an, LOGIC_SIG, provider=prov)
    assert open(p, "rb").read().startswith(b"\xef\xbb\xbf")   # utf-8-sig，Excel 双击不乱码
    assert open(p, encoding="utf-8-sig", newline="").read() == text

    rows = _csv_rows(text)
    labels = [r[0] for r in rows]
    names = m.all_names()
    assert rows[0] == ["信号\\测试"] + names                 # C-136 转置：一列一条测试
    for must in ("期望(bin)", "期望来源", "负向?", "force", "RF_WRITE"):
        assert any(l.startswith(must) for l in labels), must
    # 行序：输入行… → auto_out → 期望(进.sv) → 期望(bin) → 期望来源 → 负向? → force → RF_WRITE
    tail = [l.split("[")[0] for l in labels[-7:]]
    assert tail == ["auto_out", "期望(进.sv)", "期望(bin)", "期望来源", "负向?", "force", "RF_WRITE"]
    # 输入行 = 真名带位宽（不带真值表冻结列那个 `(角色 · 端口)` 后缀）
    assert all("(" not in l or l.endswith(")") is False or "·" not in l for l in labels[:-7])
    assert "·" not in "".join(labels)

    by = {r[0].split("[")[0]: r[1:] for r in rows}
    neg_i = next(i for i, nm in enumerate(names) if nm.endswith("_NEG"))
    assert by["负向?"][neg_i] == "是" and by["负向?"][0] == ""          # C-138
    assert by["期望来源"][neg_i] == "负向(故意填错)"                     # C-138
    assert by["期望来源"][0] == "auto_out兜底"
    assert re.match(r"^\d+'b[01]+$", by["期望(bin)"][0])               # C-137
    # C-137 force / RF_WRITE 能还原驱动：与 `inputs_table.column_drives` 同一份文本
    fs, ws = X.make_drive_fn(*X.drive_context(an))(m.cols()[0])
    assert by["force"][0] == fs and by["RF_WRITE"][0] == ws
    assert (fs or ws), "这条信号一条驱动都没有，验不到 C-137"

    # 手填期望 → 期望来源 改口（designer手填），期望(进.sv) 跟着走
    m.setData(m.index(_exp_row(m), 0), "1")
    rows2 = _csv_rows(IO.export_signal_csv(p, m, an, LOGIC_SIG, provider=prov))
    by2 = {r[0].split("[")[0]: r[1:] for r in rows2}
    assert by2["期望来源"][0] == "designer手填"

    # 带 iddq 门的信号：门行也在 CSV 里（C-128 的 CSV 侧），自检拍列的期望来源照实写
    mg, ang, provg = _fresh(wl, GATED_SIG)
    rows3 = _csv_rows(IO.export_signal_csv(str(tmp_path / "g.csv"), mg, ang, GATED_SIG,
                                           provider=provg))
    assert any(r[0].startswith(ang["dft_gate"]["label"]) for r in rows3)


@pytest.mark.contract("C-136", "C-137", "C-138")
def test_c136_c137_c138_signal_csv_matches_v1(monkeypatch, tmp_path):
    """与 v1 `SignalView.on_export_csv` **逐字节相同**——两张 mirror 上的**全部** Topout
    信号（含 logic / mux / iddq 门 / 只读根）都比，不是挑三条。"""
    H.isolate_settings(monkeypatch, tmp_path)
    v1_path, v2_path = str(tmp_path / "v1.csv"), str(tmp_path / "v2.csv")
    kinds, n_sig, diffs = set(), 0, []
    for kind in ("btlp", "wl"):
        w = H.make_window(kind=kind)
        try:
            prov = _prov(w.wb)
            sv = w.topout_view
            for mdl in list(sv.models):
                nm = mdl["name"]
                sv._load_signal(nm)
                if sv.cur_an is None:
                    continue
                an = sv.cur_an
                # v1：SignalView.on_export_csv 的函数体（对话框之外的部分逐字照搬）
                X.write_signal_csv(v1_path, sv.cur_cols, sv.e_inputs,
                                   out_width=an["out_width"] or 1,
                                   drive_fn=X.make_drive_fn(*X.drive_context(an)),
                                   name=an.get("name") or "")
                ei = e_inputs_from_an(an)
                m = TruthModel()
                m.load(an, ED.cols_from_vectors(an, ei), ei)
                # mux 走产物那条路，覆盖度必须与算 an 的那一档相同（否则列集是另一个档）
                mode, exh = sv._mode_for(nm)
                IO.export_signal_csv(v2_path, m, an, nm, provider=prov,
                                     cov={"mode": mode, "exhaustive": exh,
                                          "max_tests": sv._maxt()})
                n_sig += 1
                kinds.add(an.get("editable") or "")
                kinds.add("gated" if an.get("dft_gate") else "")
                a, b = open(v1_path, "rb").read(), open(v2_path, "rb").read()
                if a != b:
                    la = a.decode("utf-8-sig").splitlines()
                    lb = b.decode("utf-8-sig").splitlines()
                    for i in range(max(len(la), len(lb))):
                        x = la[i] if i < len(la) else "<缺这一行>"
                        y = lb[i] if i < len(lb) else "<缺这一行>"
                        if x != y:
                            diffs.append("%s 第 %d 行\n  v1: %s\n  v2: %s" % (nm, i, x[:160],
                                                                             y[:160]))
        finally:
            w.close()
    assert n_sig >= 18, "只对了 %d 个信号，覆盖太窄（mirror 载失败？）" % n_sig
    assert {"logic", "mux", "gated"} <= kinds, "没覆盖到 logic/mux/iddq 三种：%s" % kinds
    assert not diffs, "与 v1 SignalView 的单信号 CSV 有差异：\n%s" % "\n".join(diffs[:10])


# ═══════════════════ C-139 mux CSV 忠于产物 ═══════════════════
def _sv_labels(text, aid):
    """.sv 文本里该断言标号下的测试名（= 列名）。"""
    return [m for m in re.findall(r"assert_%s_(\S+):" % re.escape(aid), text)]


@pytest.mark.contract("C-139")
def test_c139_mux_csv_matches_sv_vectors(btlp, wl, tmp_path):
    """mux CSV 的列集与取值 = **同一次 build** 的 .sv 向量，一一对应。

    关键差别点：清单勾了「负向」之后，编辑器手上那条列叫 `U0_NEG`（手编列），而产物里
    它被重排成 `T<n>_NEG` —— 照 model 的 cols 导出的 CSV 与 .sv 对不上，designer 拿去
    核对就会以为多/少了一条。
    """
    for wb, sig in ((btlp, MUX_SIG), (wl, MUX_GATED_SIG)):
        m, an, prov = _fresh(wb, sig)
        cols = m.cols()
        made, _skipped = ED.add_negatives(cols, [0], False)
        assert made, "这条 mux 造不出反例，验不到 C-139"
        cols = cols + made
        m2 = TruthModel()
        m2.load(an, cols, e_inputs_from_an(an))
        assert any(nm.startswith("U") for nm in m2.all_names()), "编辑器这份列名没走手编列"

        edited = ED.compute_edited(
            {sig.lower(): {"kind": "mux", "src_out_name": an["sig"].out_name, "name": sig,
                           "renamed": False, "cols": cols, "an": an}}, {})
        text, build = X.render_sv(prov, only=[sig], edited=edited)
        vecs = X.signal_build_vectors(build, sig)
        assert vecs, "build 里没找到 %s 这一块" % sig
        aid = next(st["assert_id"] for _l, st in build["blocks"]
                   if (st.get("topout_name") or "").lower() == sig.lower())

        rows = _csv_rows(IO.export_signal_csv(str(tmp_path / "mux.csv"), m2, an, sig,
                                              provider=prov, edited=edited))
        names = rows[0][1:]
        # ① 列名 = .sv 里的测试名，顺序一致
        assert names == _sv_labels(text, aid), sig
        # ② 与 model 手上那份**确实不同**（不是恰好一样所以测不出）：
        #    编辑器那条反例叫 U<n>_NEG（手编列），产物里被重排成 T<n>_NEG
        assert names != m2.all_names(), sig
        assert any(x.endswith("_NEG") for x in names), sig
        assert [x for x in names if x.endswith("_NEG")] \
            != [x for x in m2.all_names() if x.endswith("_NEG")], sig
        # ③ 期望(bin) 与 .sv 断言里写的对比值逐条相同
        by = {r[0].split("[")[0]: r[1:] for r in rows}
        sv_exp = re.findall(r"assert \(`\S+==(\S+)\)begin", text)
        assert by["期望(bin)"] == sv_exp, sig
        # ④ 负向标记也对得上
        assert by["负向?"] == ["是" if v.is_negative else "" for v in vecs], sig
        # ⑤ 列数 = 产物的用例数
        assert len(names) == len(vecs)


@pytest.mark.contract("C-139")
def test_c139_mux_columns_fall_back_without_provider(btlp):
    """拿不到产物（没传 provider / 这次 build 里没这一块）→ 退回 model 的列，不许抛。"""
    m, an, prov = _fresh(btlp, MUX_SIG)
    assert IO.signal_csv_columns(m, an, MUX_SIG) == m.cols()
    # 名字对不上 build → signal_build_vectors 给 None（不是空列表），列退回 model
    _text, build = X.render_sv(prov, only=[MUX_SIG])
    assert X.signal_build_vectors(build, "不存在的信号") is None
    cols = IO.signal_csv_columns(m, an, "不存在的信号", provider=prov)
    assert [c["name"] for c in cols] == [c["name"] for c in m.cols()]
    # logic 信号不走 build 那条路（provider 传了也一样）
    ml, anl, provl = _fresh(btlp, LOGIC_SIG)
    assert IO.signal_csv_columns(ml, anl, LOGIC_SIG, provider=provl) == ml.cols()
    # cov 的键写错是调用方的 bug —— 当场抛，别静默按默认档去 build（那会与编辑器对不上）
    with pytest.raises(TypeError):
        IO.signal_csv_columns(m, an, MUX_SIG, provider=prov, cov={"coverage": "max"})
    assert set(IO.COV_KEYS) == {"mode", "max_tests", "exhaustive", "sig_cov", "form_cov"}
    # 给对了键就照着那一档 build（穷举档的列数与精简档不同）
    n_min = len(IO.signal_csv_columns(m, an, MUX_SIG, provider=prov, cov={"mode": "min"}))
    n_exh = len(IO.signal_csv_columns(m, an, MUX_SIG, provider=prov,
                                      cov={"mode": "max", "exhaustive": True,
                                           "max_tests": 100000}))
    assert n_exh > n_min, (n_min, n_exh)


# ═══════════════════ 分层 ═══════════════════
def test_io_module_is_qt_free():
    """`io.py` 不许 import PySide6 / theme / model 实现类 / state / 引擎 / gui（硬规矩）。"""
    src = _io.open(IO.__file__, encoding="utf-8").read()
    assert not re.search(r"^\s*(import|from)\s+PySide6", src, re.M)
    tree = ast.parse(src)
    mods = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            if node.level and not node.module:
                mods |= {a.name for a in node.names}
            elif node.module:
                mods.add(node.module.split(".")[-1])
    allowed = {"csv", "os", "dataclasses", "openpyxl",
               "exports", "edits", "truth_edit", "inputs_table", "contracts", "terms"}
    assert mods <= allowed, "io.py import 了不该 import 的：%s" % sorted(mods - allowed)
    for banned in ("PySide6", "theme", "state", "generator", "gui", "topout", "model"):
        assert banned not in mods
    # 证明这条扫描判得出「不允许」：model.py 里就有 PySide6
    msrc = _io.open(TM.__file__, encoding="utf-8").read()
    assert re.search(r"^\s*from\s+PySide6", msrc, re.M)
