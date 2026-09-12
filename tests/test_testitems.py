# -*- coding: utf-8 -*-
"""测试项编辑能力的回归测试（**引擎层**）：
  · vectors.input_groups / vector_to_base_values / make_vector_from_base_values
  · generator.build 的 vector_overrides 回流(编辑 → .sv)
  · generator.report / cli.write_report 的真值表与可验证性

2026-09-12（GUI v2 C5-a）：本文件原有 77 条，其中 49 条打的是 v1 门面
（`legacy_gui.MainWindow`）。它们的能力已由 v2 契约测试接手或随入口退役，逐条处置记在
`docs/GUI_v2_测试迁移_C5a.csv`（含每条老断言原文 + 接手的 v2 nodeid）。
剩下的 27 条是**不起窗**的引擎/报告断言，原样保留；另有 1 条
`test_gui_header_tooltips_present` 是 legacy 独有、契约表里没有的能力，
挂 `legacy_only` 标记留到主控裁决。
"""

import os
import sys

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")   # GUI 测试用离屏后端
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dreg_verify import excel_model, generator     # noqa: E402
from dreg_verify import expr as E                   # noqa: E402
from dreg_verify import vectors as V                # noqa: E402
from dreg_verify import resolver as R               # noqa: E402
import fixtures                                      # noqa: E402


@pytest.fixture(scope="module")
def wb(tmp_path_factory):
    path = tmp_path_factory.mktemp("xl") / "synthetic_dreg.xlsx"
    fixtures.build_workbook(str(path))
    return excel_model.load_workbook(str(path))


def _reserve(wb):
    sig = next(s for s in wb.logic if s.out_name == "d_logic_bt_lp_reserve")
    node = E.parse(sig.expr)               # (A?C:B)&(~J)
    bindings = R.Resolver(wb).resolve_signal_inputs(sig)
    groups = V.input_groups(node, bindings)
    return sig, node, bindings, groups


# ───────────── input_groups ─────────────
def test_input_groups_order_and_roles(wb):
    sig, node, bindings, groups = _reserve(wb)
    bases = [g["base"] for g in groups]
    # collect_vars 发现顺序 = A,C,B,J（三元先 cond 再 then/els，再门控位）
    assert bases == ["d_bt_lp_linelocal_mode_ctrl", "d_bt_lp_bt_mode_sel_local",
                     "d_bt_lp_bt_mode_sel", "d_bt_lp_iddq"]
    gd = {g["base"]: g for g in groups}
    assert gd["d_bt_lp_linelocal_mode_ctrl"]["is_control"]   # 三元条件 A
    assert gd["d_bt_lp_iddq"]["is_control"]                  # 门控位 J
    assert not gd["d_bt_lp_bt_mode_sel"]["is_control"]       # 分支数据 B
    assert all(g["width"] == 1 for g in groups)


def test_input_groups_label_keeps_width(wb):
    """显示用 label 保留位宽 [msb:lsb]，但 base(查表 key)仍不带位宽。"""
    sig = next(s for s in wb.logic if s.out_name == "d_logic_bt_lp_lna_agc[2:0]")
    node = E.parse(sig.expr)
    bindings = R.Resolver(wb).resolve_signal_inputs(sig)
    by_base = {g["base"]: g for g in V.input_groups(node, bindings)}
    assert by_base["d_bt_lp_lna_agc_local"]["label"] == "d_bt_lp_lna_agc_local[2:0]"
    assert by_base["d_bt_lp_lna_agc_line"]["label"] == "d_bt_lp_lna_agc_line[2:0]"
    assert by_base["d_bt_lp_lna_line_sel"]["label"] == "d_bt_lp_lna_line_sel"   # 1bit 无切片
    assert by_base["d_bt_lp_lna_agc_local"]["base"] == "d_bt_lp_lna_agc_local"  # base 仍纯净


def test_input_groups_shared_base(wb):
    """同一物理信号占多个字母时只占一列（直通信号 d_en_refbuf 只有 A）。"""
    sig = next(s for s in wb.logic if s.out_name == "d_en_refbuf")
    node = E.parse(sig.expr)
    bindings = R.Resolver(wb).resolve_signal_inputs(sig)
    groups = V.input_groups(node, bindings)
    assert [g["base"] for g in groups] == ["d_bt_lp_en_refbuf_cfg"]
    assert groups[0]["letters"] == ["A"]


# ───────────── make_vector_from_base_values ─────────────
def test_make_vector_expected_autocompute(wb):
    sig, node, bindings, groups = _reserve(wb)
    # A=1(选 C),C=1,B=0,J=0 → (1?1:0)&(~0)=1
    bv = {"d_bt_lp_linelocal_mode_ctrl": 1, "d_bt_lp_bt_mode_sel_local": 1,
          "d_bt_lp_bt_mode_sel": 0, "d_bt_lp_iddq": 0}
    vec = V.make_vector_from_base_values(node, bindings, groups, bv, sig.out_width)
    assert vec.exp_value == 1 and not vec.is_negative
    assert vec.assignments["A"] == 1 and vec.assignments["C"] == 1
    assert vec.assignments["B"] == 0 and vec.assignments["J"] == 0
    # 门控 J=1 → 拉 0
    bv2 = dict(bv); bv2["d_bt_lp_iddq"] = 1
    assert V.make_vector_from_base_values(node, bindings, groups, bv2, sig.out_width).exp_value == 0


def test_make_vector_override_marks_negative(wb):
    sig, node, bindings, groups = _reserve(wb)
    bv = {"d_bt_lp_linelocal_mode_ctrl": 1, "d_bt_lp_bt_mode_sel_local": 1,
          "d_bt_lp_bt_mode_sel": 0, "d_bt_lp_iddq": 0}   # 正确期望=1
    neg = V.make_vector_from_base_values(node, bindings, groups, bv, sig.out_width,
                                         expected_override=0)
    assert neg.is_negative and neg.neg_value == 0 and neg.exp_value == 1
    assert neg.asserted_value == 0
    # 手填的期望与算出值相同 → 不算负向
    ok = V.make_vector_from_base_values(node, bindings, groups, bv, sig.out_width,
                                        expected_override=1)
    assert not ok.is_negative


def test_vector_to_base_values_roundtrip(wb):
    sig, node, bindings, groups = _reserve(wb)
    vecs, _meta = V.generate_vectors(node, bindings, sig.out_width)
    for vec in vecs:
        bv = V.vector_to_base_values(vec, groups)
        rt = V.make_vector_from_base_values(node, bindings, groups, bv, sig.out_width)
        assert rt.exp_value == vec.exp_value          # 往返不改期望
        assert rt.assignments == vec.assignments       # 字母展开一致


def test_base_value_clamped_to_width(wb):
    """编辑值超过输入位宽时按位宽裁剪，不溢出。"""
    sig = next(s for s in wb.logic if s.out_name == "d_logic_bt_lp_lna_agc[2:0]")
    node = E.parse(sig.expr)                # A?C:B, B/C 为 3bit
    bindings = R.Resolver(wb).resolve_signal_inputs(sig)
    groups = V.input_groups(node, bindings)
    gd = {g["base"]: g for g in groups}
    assert gd["d_bt_lp_lna_agc_local"]["width"] == 3
    bv = {"d_bt_lp_lna_line_sel": 1, "d_bt_lp_lna_agc_local": 0xFF,
          "d_bt_lp_lna_agc_line": 0}
    vec = V.make_vector_from_base_values(node, bindings, groups, bv, sig.out_width)
    # 0xFF 裁到 3bit=7；A=1 选 C → 期望=7
    assert vec.exp_value == 7


# ───────────── generator.build vector_overrides ─────────────
def test_vector_overrides_replaces_auto(wb):
    sig, node, bindings, groups = _reserve(wb)
    bv = {"d_bt_lp_linelocal_mode_ctrl": 1, "d_bt_lp_bt_mode_sel_local": 1,
          "d_bt_lp_bt_mode_sel": 0, "d_bt_lp_iddq": 0}
    custom = V.make_vector_from_base_values(node, bindings, groups, bv, sig.out_width)
    opts = generator.GenOptions(signals=["d_logic_bt_lp_reserve"], top_output_only=False,
                                vector_overrides={"d_logic_bt_lp_reserve": [custom]})
    res = generator.build(wb, opts)
    assert res["summary"]["n_generated"] == 1
    assert res["summary"]["n_vectors"] == 1           # 只有我们这一条，不是自动那批
    text = "\n".join(res["blocks"][0][0])
    assert "==1'b1" in text                            # 期望 1
    assert "`RF_WRITE(10'h2D,16'h3);" in text          # A,C 同地址 0x2D 合并→bit0|bit1=0x3
    assert "force `ENV_RF.d_bt_lp_iddq=16'h0;" in text  # J(RO)→force


def test_vector_overrides_negative_to_sv(wb):
    sig, node, bindings, groups = _reserve(wb)
    bv = {"d_bt_lp_linelocal_mode_ctrl": 1, "d_bt_lp_bt_mode_sel_local": 1,
          "d_bt_lp_bt_mode_sel": 0, "d_bt_lp_iddq": 0}
    neg = V.make_vector_from_base_values(node, bindings, groups, bv, sig.out_width,
                                         expected_override=0)
    opts = generator.GenOptions(signals=["d_logic_bt_lp_reserve"], top_output_only=False,
                                vector_overrides={"d_logic_bt_lp_reserve": [neg]})
    res = generator.build(wb, opts)
    text = "\n".join(res["blocks"][0][0])
    # 反例(自检式，2026-06-03)：断言语法与正例一模一样，== 故意填错的 0（正确应为 1）→
    # 仿真时必然 FAIL → uvm_report_error 正常触发(消息带 NEG-EXPECTED-FAIL 标签)
    assert "==1'b0" in text and "!=" not in text
    assert "NEG-EXPECTED-FAIL" in text and "NEG-BROKEN" in text
    assert res["blocks"][0][1]["n_negative"] == 1


def test_overrides_only_affect_named_signal(wb):
    """有 override 的信号用 override；其它信号仍走自动生成。"""
    sig, node, bindings, groups = _reserve(wb)
    bv = {"d_bt_lp_linelocal_mode_ctrl": 0, "d_bt_lp_bt_mode_sel_local": 0,
          "d_bt_lp_bt_mode_sel": 0, "d_bt_lp_iddq": 0}
    custom = V.make_vector_from_base_values(node, bindings, groups, bv, sig.out_width)
    opts = generator.GenOptions(top_output_only=False,
                                vector_overrides={"d_logic_bt_lp_reserve": [custom]})
    res = generator.build(wb, opts)
    by_name = {st["out_name"]: (lines, st) for lines, st in res["blocks"]}
    assert by_name["d_logic_bt_lp_reserve"][1]["n_vectors"] == 1        # override
    assert by_name["d_logic_bt_lp_lna_agc[2:0]"][1]["n_vectors"] > 1     # 自动


# ───────────── 审查修复回归 ─────────────
def test_unequal_width_same_base_roundtrip():
    """#5: 同一 base 跨不等宽字母时，往返不丢高位（取最宽字母作代表）。"""
    from dreg_verify.resolver import InputBinding
    A = InputBinding("A", "sig", "sig", 2, "RO", None, None, None, "sig", "wire")
    B = InputBinding("B", "sig", "sig", 4, "RO", None, None, None, "sig", "wire")
    bindings = {"A": A, "B": B}
    node = E.parse("A|B")
    groups = V.input_groups(node, bindings)
    assert len(groups) == 1
    assert groups[0]["rep"] == "B" and groups[0]["width"] == 4   # 最宽字母作代表
    vecs, _ = V.generate_vectors(node, bindings, 4)
    for vec in vecs:
        bv = V.vector_to_base_values(vec, groups)
        rt = V.make_vector_from_base_values(node, bindings, groups, bv, 4)
        assert rt.exp_value == vec.exp_value, "往返丢位: %r" % vec.assignments


def test_empty_override_means_zero_tests(wb):
    """#1: 空 override 列表 = 该信号零用例(一键清空)，而非回退自动生成；
    且不静默——整组进 skipped 并给「用户已清空」原因(第二十六轮：与 mux 清空对称、可见)。"""
    opts = generator.GenOptions(signals=["d_logic_bt_lp_reserve"], top_output_only=False,
                                vector_overrides={"d_logic_bt_lp_reserve": []})
    res = generator.build(wb, opts)
    assert res["summary"]["n_generated"] == 0
    assert res["summary"]["n_vectors"] == 0
    skipped_names = {n for n, _aid, _r in res["skipped"]}
    assert "d_logic_bt_lp_reserve" in skipped_names
    reason = next(r for n, _aid, r in res["skipped"] if n == "d_logic_bt_lp_reserve")
    assert "清空" in "; ".join(str(w) for *_h, w in reason)
    # 报告侧同口径：summary 行 n_tests=0 且 error 带「清空」(双轨一致)
    rep = generator.report(wb, opts)
    row = next(r for r in rep["summary"] if r["signal"] == "d_logic_bt_lp_reserve")
    assert row["n_tests"] == 0 and "清空" in row["error"]


def test_negative_sv_has_neg_suffix(wb):
    """#4: 负向断言 id 带 _NEG 后缀，可与正向区分。"""
    sig, node, bindings, groups = _reserve(wb)
    bv = {"d_bt_lp_linelocal_mode_ctrl": 1, "d_bt_lp_bt_mode_sel_local": 1,
          "d_bt_lp_bt_mode_sel": 0, "d_bt_lp_iddq": 0}
    neg = V.make_vector_from_base_values(node, bindings, groups, bv, sig.out_width,
                                         expected_override=0)
    res = generator.build(wb, generator.GenOptions(
        signals=["d_logic_bt_lp_reserve"], top_output_only=False,
        vector_overrides={"d_logic_bt_lp_reserve": [neg]}))
    text = "\n".join(res["blocks"][0][0])
    assert "assert_106_T0_NEG:" in text


def test_override_plus_left_neg_appends_auto(wb):
    """#3: 既 override 又在 neg 列表里的信号，正向行仍会补自动负向(不被静默丢)。"""
    sig, node, bindings, groups = _reserve(wb)
    bv = {"d_bt_lp_linelocal_mode_ctrl": 1, "d_bt_lp_bt_mode_sel_local": 1,
          "d_bt_lp_bt_mode_sel": 0, "d_bt_lp_iddq": 0}
    pos = V.make_vector_from_base_values(node, bindings, groups, bv, sig.out_width)  # 正向
    res = generator.build(wb, generator.GenOptions(
        signals=["d_logic_bt_lp_reserve"], top_output_only=False,
        neg_all=True, neg_which="first",
        vector_overrides={"d_logic_bt_lp_reserve": [pos]}))
    st = res["blocks"][0][1]
    assert st["n_vectors"] == 2          # 1 正向 + 1 追加的自动负向
    assert st["n_negative"] == 1


def test_report_tables_structure(wb):
    """report() 返回 tables（每信号真值表）：输入做行、各测试做列，负向列有 _NEG。"""
    rep = generator.report(wb, generator.GenOptions(
        signals=["d_logic_bt_lp_reserve"], top_output_only=False,
        neg_all=True, neg_which="first"))
    assert "tables" in rep
    t = next(x for x in rep["tables"] if x["signal"] == "d_logic_bt_lp_reserve")
    assert t["inputs"] and t["tests"]
    names = [tc["name"] for tc in t["tests"]]
    assert any(n.endswith("_NEG") for n in names)              # 含负向列
    assert all(len(tc["values"]) == len(t["inputs"]) for tc in t["tests"])  # 每列值数 = 输入数
    neg_tc = next(tc for tc in t["tests"] if tc["neg"])
    assert neg_tc["expected"] != neg_tc["correct"]             # 负向期望 != 正确值


def test_report_tables_bitwidth_label(wb):
    """真值表的输入/输出行标签保留位宽。"""
    rep = generator.report(wb, generator.GenOptions(
        signals=["d_logic_bt_lp_lna_agc"], top_output_only=False))
    t = next(x for x in rep["tables"] if x["signal"] == "d_logic_bt_lp_lna_agc[2:0]")
    labels = [i["label"] for i in t["inputs"]]
    assert "d_bt_lp_lna_agc_local[2:0]" in labels
    assert t["exp_label"] == "期望(out)[2:0]"


def test_custom_test_name_flows_to_sv(wb):
    """自定义 name 贯穿到 .sv 断言 id；默认 None 时仍是 T<index>；负向仍带 _NEG。"""
    import dreg_verify.sv_writer as W
    sig, node, bindings, groups = _reserve(wb)
    bv = {g["base"].lower(): 0 for g in groups}
    v_named = V.make_vector_from_base_values(node, bindings, groups, bv, sig.out_width,
                                             index=0, name="boundary_case")
    v_default = V.make_vector_from_base_values(node, bindings, groups, bv, sig.out_width, index=1)
    v_neg = V.make_vector_from_base_values(node, bindings, groups, bv, sig.out_width,
                                           index=2, expected_override=1, name="bad_iddq")
    assert W.test_label(v_named) == "boundary_case"
    assert W.test_label(v_default) == "T1"               # 默认仍 T<index>
    assert W.test_label(v_neg) == "bad_iddq_NEG"          # 负向自动带 _NEG
    res = generator.build(wb, generator.GenOptions(
        signals=["d_logic_bt_lp_reserve"], top_output_only=False,
        vector_overrides={"d_logic_bt_lp_reserve": [v_named, v_neg]}))
    text = "\n".join(res["blocks"][0][0])
    assert "assert_106_boundary_case:" in text
    assert "assert_106_bad_iddq_NEG:" in text


def test_build_no_dup_labels_normal(wb):
    """正常情况(R 各异)无重复标号。"""
    res = generator.build(wb, generator.GenOptions(top_output_only=False))
    assert res["summary"]["n_dup_labels"] == 0
    assert res["dup_labels"] == []


def test_build_dup_labels_same_R(wb, tmp_path_factory):
    """构造一张两信号同 R 的表，确认 build 抓到重复标号。"""
    import openpyxl
    from openpyxl.utils import column_index_from_string
    path = tmp_path_factory.mktemp("dup") / "dup.xlsx"
    import fixtures
    fixtures.build_workbook(str(path))
    # 把 lna_agc(R=108) 的 R 改成和 reserve 一样的 106
    wbx = openpyxl.load_workbook(str(path))
    ws = wbx["logic"]
    for row in ws.iter_rows():
        if ws.cell(row=row[0].row, column=column_index_from_string("K")).value == "d_logic_bt_lp_lna_agc[2:0]":
            ws.cell(row=row[0].row, column=column_index_from_string("R")).value = 106
    wbx.save(str(path))
    wb2 = excel_model.load_workbook(str(path))
    res = generator.build(wb2, generator.GenOptions(top_output_only=False))
    assert res["summary"]["n_dup_labels"] > 0          # 106_T0 等被两信号共用
    assert any("106_T0" == lbl for lbl, _a, _b in res["dup_labels"])


def test_report_honors_overrides(wb):
    """#8: report() 也用 override，与 .sv 一致。"""
    sig, node, bindings, groups = _reserve(wb)
    bv = {"d_bt_lp_linelocal_mode_ctrl": 0, "d_bt_lp_bt_mode_sel_local": 0,
          "d_bt_lp_bt_mode_sel": 0, "d_bt_lp_iddq": 0}
    custom = V.make_vector_from_base_values(node, bindings, groups, bv, sig.out_width)
    rep = generator.report(wb, generator.GenOptions(
        signals=["d_logic_bt_lp_reserve"], top_output_only=False,
        vector_overrides={"d_logic_bt_lp_reserve": [custom]}))
    row = next(r for r in rep["summary"] if r["signal"] == "d_logic_bt_lp_reserve")
    assert row["n_tests"] == 1           # override 那一条，不是自动那批


def test_no_overrides_unchanged(wb):
    """不传 vector_overrides 时行为与以前完全一致（默认 None）。"""
    a = generator.build(wb, generator.GenOptions(signals=["d_logic_bt_lp_reserve"],
                                                 top_output_only=False))
    b = generator.build(wb, generator.GenOptions(signals=["d_logic_bt_lp_reserve"],
                                                 top_output_only=False, vector_overrides=None))
    assert a["summary"]["n_vectors"] == b["summary"]["n_vectors"]
    assert generator.render(a) == generator.render(b)


# ───────────── 可验证性报告（取代旧'覆盖诊断'按钮） ─────────────
def test_report_verifiability_structure(wb):
    """report() 返回 verifiability：counts 四类齐全，且与逐信号 status 计数一致。"""
    from collections import Counter
    rep = generator.report(wb, generator.GenOptions(top_output_only=False))
    v = rep["verifiability"]
    assert set(v["counts"]) >= {"clean", "wire-fallback", "unresolved", "parse-err"}
    assert sum(v["counts"].values()) == len(v["signals"]) == len(wb.logic)
    assert all(s["status"] in v["counts"] for s in v["signals"])
    c = Counter(s["status"] for s in v["signals"])
    for k, n in c.items():
        assert v["counts"][k] == n


def test_html_report_has_verifiability_tab(wb, tmp_path):
    """HTML 报告含第④'可验证性'标签页与对应行。"""
    from dreg_verify import cli
    rep = generator.report(wb, generator.GenOptions(top_output_only=False))
    path = tmp_path / "r.html"
    cli.write_report(str(path), rep, "synthetic.xlsx")
    html = path.read_text(encoding="utf-8")
    assert "可验证性" in html and 'data-tab="ver"' in html
    assert 'id="ver"' in html and "vrow" in html


def test_csv_report_writes_verifiability(wb, tmp_path):
    """CSV 报告额外写出 *_verifiability.csv。"""
    from dreg_verify import cli
    rep = generator.report(wb, generator.GenOptions(top_output_only=False))
    path = tmp_path / "r.csv"
    written = cli.write_report(str(path), rep, "synthetic.xlsx")
    verif = [p for p in written if p.endswith("_verifiability.csv")]
    assert verif, "应生成可验证性 CSV"
    content = open(verif[0], encoding="utf-8-sig").read()
    assert "可验证性" in content


# ───────────── 真值表标注表达式字母 A/B/C（#1） ─────────────
def test_report_table_inputs_carry_letters(wb):
    """report() 的真值表 inputs 每项带 letters（A/B/C…），供 HTML/CSV 对照表达式。
    2026-06-10 起行序=寄存器地址+bit 位（for_test 生成规则），不再是表达式字母序。"""
    rep = generator.report(wb, generator.GenOptions(top_output_only=False))
    t = next(x for x in rep["tables"] if x["signal"] == "d_logic_bt_lp_reserve")
    assert sorted(i["letters"] for i in t["inputs"]) == ["A", "B", "C", "J"]   # 不丢输入
    assert [i["letters"] for i in t["inputs"]] == ["B", "A", "C", "J"]          # 地址序
    assert all("label" in i for i in t["inputs"])   # 原 label 不破坏


def test_html_report_maps_letters_to_signals(wb, tmp_path):
    """HTML 报告真值表行表头出现 '字母 → 信号' 的映射。"""
    from dreg_verify import cli
    rep = generator.report(wb, generator.GenOptions(top_output_only=False))
    path = tmp_path / "r.html"
    cli.write_report(str(path), rep, "synthetic.xlsx")
    html = path.read_text(encoding="utf-8")
    assert "A → d_bt_lp_linelocal_mode_ctrl" in html
    assert "J → d_bt_lp_iddq" in html


def test_html_report_truth_table_value_filter(wb, tmp_path):
    """⭐真值表「按值筛选」(2026-06-12)：穷举真值表列多时，designer 在第0列每个『变化的输入』旁的输入框里
    像 Excel 一样手填值 → 只留该值匹配的测试列（整列 display:none）。验证输入框/列标 data-c/计数+清除/
    CSS+JS(含边打边筛 input)通道都在，且真值表块仍只在 JSON blob 里（不预渲进静态壳）。"""
    import json
    import re
    from dreg_verify import cli
    rep = generator.report(wb, generator.GenOptions(top_output_only=False))
    path = tmp_path / "r.html"
    cli.write_report(str(path), rep, "synthetic.xlsx")
    raw = path.read_text(encoding="utf-8")

    # ②③ 共用的真值表数据 blob：解析出来逐项查（HTML 在 item.h 里，未转义）
    m = re.search(r'<script type="application/json" id="tt-data">(.*?)</script>', raw, re.S)
    assert m, "缺少 tt-data blob"
    items = json.loads(m.group(1))
    blk = next((it["h"] for it in items if 'class="ttf"' in it["h"]), None)
    assert blk, "应至少有一个真值表块（多列+变化输入）挂上按值筛选输入框"
    # 是手填输入框（不是下拉），带 data-ri，varying 输入才有
    assert re.search(r'<input class="ttf" type="text" data-ri="\d+"', blk)
    assert "<select" not in blk and "(全部)" not in blk     # 已不是下拉
    assert 'data-c="0"' in blk                             # 每列每格带列标（整列一起显隐）
    assert 'class="ttfcount"' in blk and 'class="ttfclr"' in blk   # 计数 + 清除
    assert 'class="inrow" data-ri=' in blk                 # 输入行可被 JS 定位读取本列取值

    # 真值表块仍只在 blob、不在静态壳；CSS/JS 通道齐
    shell = re.sub(r'<script type="application/json"[^>]*>.*?</script>', "", raw, flags=re.S)
    assert '<div class="ttblock">' not in shell
    assert '.tt .ttfx{display:none}' in shell               # 整列隐藏的 CSS
    assert 'function filterBlock' in shell                  # 筛选核心
    assert "contains('ttf')" in shell                       # 委托接上 .ttf 输入框
    assert "contains('ttfclr')" in shell                    # click 委托接上「清除」
    assert "addEventListener('input'" in shell              # 边打边筛（live 输入）


# ───────────── legacy 独有：清单列头 tooltip（C5-a 未裁决，留到 legacy 删除日） ─────────────
@pytest.fixture(scope="module")
def qapp():
    pytest.importorskip("PySide6")
    from PySide6 import QtWidgets
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.mark.legacy_only
def test_gui_header_tooltips_present(qapp, wb, tmp_path_factory):
    """B2：'负向'/'状态'表头与状态单元格带说明 tooltip。

    C5-a 处置：**保留·legacy独有（待主控裁决）**。v2 的清单 model 只给「断言号」列头挂了
    tooltip（C-010），「反例 / 状态」两个列头没有；契约表里也没有「清单列头自带说明」这一条。
    见 `docs/GUI_v2_测试迁移_C5a.csv` 同名行。"""
    from dreg_verify import legacy_gui as gui
    path = tmp_path_factory.mktemp("g_tips") / "synthetic_dreg.xlsx"
    fixtures.build_workbook(str(path))
    w = gui.MainWindow()
    w.path_edit.setText(str(path))
    w.on_load()
    try:
        assert "负向" in w.table.horizontalHeaderItem(gui.COL_NEG).toolTip()
        assert w.table.horizontalHeaderItem(gui.COL_STATUS).toolTip()
        assert w.table.item(0, gui.COL_STATUS).toolTip()
    finally:
        w.close()
