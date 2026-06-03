# -*- coding: utf-8 -*-
"""auto_out / 期望 分离（防自证验证，2026-06-03 第十一轮）的回归测试：

  Dreg 验证的对象是 designer 写的逻辑表达式本身；用表达式算出的 auto_out 当期望
  去验证表达式有自证嫌疑。因此：
  · TestVector 增加 designer_expected（designer 手填期望），.sv 断言优先用它；
    未填 → auto_out(exp_value) 兜底；手填 != auto_out 不算负向（仿真 FAIL = 表达式 bug）。
  · GUI 真值表拆成 auto_out(只读) + 期望(可编辑) 两行，「auto→期望」一键填充，
    编辑按 Excel 路径持久化（关 GUI 不丢）+ 可导出/导入。
  · HTML 报告真值表拆两行 + 新增「真值表检查」tab（遮 auto_out、填期望、回车判定）。
"""

import json
import os
import sys

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")   # GUI 测试用离屏后端
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dreg_verify import excel_model, generator     # noqa: E402
from dreg_verify import expr as E                   # noqa: E402
from dreg_verify import vectors as V                # noqa: E402
from dreg_verify import resolver as R               # noqa: E402
from dreg_verify import sv_writer as W              # noqa: E402
import fixtures                                      # noqa: E402


@pytest.fixture(scope="module")
def wb(tmp_path_factory):
    path = tmp_path_factory.mktemp("xl_de") / "synthetic_dreg.xlsx"
    fixtures.build_workbook(str(path))
    return excel_model.load_workbook(str(path))


def _reserve(wb):
    sig = next(s for s in wb.logic if s.out_name == "d_logic_bt_lp_reserve")
    node = E.parse(sig.expr)               # (A?C:B)&(~J)
    bindings = R.Resolver(wb).resolve_signal_inputs(sig)
    groups = V.input_groups(node, bindings)
    return sig, node, bindings, groups


# A=1(选C),C=1,B=0,J=0 → auto_out = 1
BV_ONE = {"d_bt_lp_linelocal_mode_ctrl": 1, "d_bt_lp_bt_mode_sel_local": 1,
          "d_bt_lp_bt_mode_sel": 0, "d_bt_lp_iddq": 0}


# ───────────── 数据层：TestVector / make_vector_from_base_values ─────────────
def test_vector_asserted_value_priority():
    """断言对比值优先级：负向错值 > designer 手填期望 > auto_out 兜底。"""
    # 未填 → auto_out 兜底
    v = V.TestVector(0, {"A": 1}, exp_value=1, exp_width=1)
    assert v.asserted_value == 1 and not v.designer_filled
    # 手填 → 用手填值（即使与 auto_out 不同也不是负向）
    v2 = V.TestVector(0, {"A": 1}, exp_value=1, exp_width=1, designer_expected=0)
    assert v2.asserted_value == 0 and v2.designer_filled and not v2.is_negative
    # 负向优先级最高（负向不算 designer 手填）
    v3 = V.TestVector(0, {"A": 1}, exp_value=1, exp_width=1,
                      is_negative=True, neg_value=0, designer_expected=1)
    assert v3.asserted_value == 0 and not v3.designer_filled


def test_make_vector_designer_expected_not_negative(wb):
    """⭐核心语义：designer 手填期望 != auto_out 不标负向——那可能正是表达式的 bug。"""
    sig, node, bindings, groups = _reserve(wb)
    # auto_out = 1，designer 认为应该是 0
    vec = V.make_vector_from_base_values(node, bindings, groups, BV_ONE, sig.out_width,
                                         designer_expected=0)
    assert not vec.is_negative                  # 不是负向！
    assert vec.exp_value == 1                   # auto_out 仍是表达式算的 1
    assert vec.designer_expected == 0
    assert vec.asserted_value == 0              # .sv 断言用 designer 的 0
    assert vec.designer_filled


def test_make_vector_designer_expected_same_as_auto(wb):
    """手填值与 auto_out 相同：仍算 designer 手填（已人工核对，区别于兜底）。"""
    sig, node, bindings, groups = _reserve(wb)
    vec = V.make_vector_from_base_values(node, bindings, groups, BV_ONE, sig.out_width,
                                         designer_expected=1)
    assert not vec.is_negative and vec.designer_filled
    assert vec.asserted_value == vec.exp_value == 1


def test_make_vector_designer_expected_masked_to_width(wb):
    """手填期望按输出位宽裁剪（1bit 信号填 0xFF → 1）。"""
    sig, node, bindings, groups = _reserve(wb)
    vec = V.make_vector_from_base_values(node, bindings, groups, BV_ONE, sig.out_width,
                                         designer_expected=0xFF)
    assert vec.designer_expected == 1 and vec.asserted_value == 1


def test_make_vector_unfilled_falls_back_to_auto(wb):
    """期望未填（designer_expected=None）→ asserted_value = auto_out（向后兼容）。"""
    sig, node, bindings, groups = _reserve(wb)
    vec = V.make_vector_from_base_values(node, bindings, groups, BV_ONE, sig.out_width)
    assert vec.designer_expected is None and not vec.designer_filled
    assert vec.asserted_value == vec.exp_value == 1


def test_negative_override_still_works(wb):
    """负向(expected_override)机制不受影响：仍标负向、断言用错值。"""
    sig, node, bindings, groups = _reserve(wb)
    neg = V.make_vector_from_base_values(node, bindings, groups, BV_ONE, sig.out_width,
                                         expected_override=0)
    assert neg.is_negative and neg.asserted_value == 0 and not neg.designer_filled


def test_make_negative_avoids_designer_expected():
    """负向错值防撞：自动负向的错值要避开 designer 手填期望（否则反例在"designer 是对的"
    时会意外 PASS = NEG-BROKEN）。多位信号必避开；1bit 两者不同时无解(保持现状)。"""
    # 4bit：auto=5，designer 手填 10(=~5)——取反值恰好撞上手填期望 → 必须翻位避开
    v = V.TestVector(0, {"A": 1}, exp_value=5, exp_width=4, designer_expected=10)
    neg = V.make_negative(v, mode="invert")
    assert neg.neg_value != 5 and neg.neg_value != 10        # 与 auto_out、手填期望都不同
    # 无 designer 期望时行为不变：错值 = 取反
    v2 = V.TestVector(0, {"A": 1}, exp_value=5, exp_width=4)
    assert V.make_negative(v2, mode="invert").neg_value == 10
    # 1bit 且 designer != auto：无解(任何值都撞)，保持取反值（仿真时 NEG-BROKEN 标签可见）
    v3 = V.TestVector(0, {"A": 1}, exp_value=1, exp_width=1, designer_expected=0)
    assert V.make_negative(v3, mode="invert").neg_value == 0


# ───────────── .sv 层：断言用期望 + 统计 ─────────────
def test_sv_assert_uses_designer_expected(wb):
    """.sv 断言对比值 = designer 手填期望（即使与 auto_out 不同）。"""
    sig, node, bindings, groups = _reserve(wb)
    # auto_out=1 但 designer 填 0 → 断言应是 ==1'b0
    vec = V.make_vector_from_base_values(node, bindings, groups, BV_ONE, sig.out_width,
                                         designer_expected=0)
    res = generator.build(wb, generator.GenOptions(
        signals=["d_logic_bt_lp_reserve"], top_output_only=False,
        vector_overrides={"d_logic_bt_lp_reserve": [vec]}))
    text = "\n".join(res["blocks"][0][0])
    assert "==1'b0)begin" in text               # 用 designer 的 0，不是 auto 的 1
    assert "==1'b1)begin" not in text
    # 不是负向：无 _NEG 后缀、无 NEG 标签
    assert "_NEG" not in text and "NEG-EXPECTED-FAIL" not in text


def test_sv_stats_count_designer_filled(wb):
    """render_signal_block stats / build summary 统计 designer 手填条数。"""
    sig, node, bindings, groups = _reserve(wb)
    v_filled = V.make_vector_from_base_values(node, bindings, groups, BV_ONE, sig.out_width,
                                              index=0, designer_expected=1)
    v_unfilled = V.make_vector_from_base_values(node, bindings, groups, BV_ONE, sig.out_width,
                                                index=1)
    v_neg = V.make_vector_from_base_values(node, bindings, groups, BV_ONE, sig.out_width,
                                           index=2, expected_override=0)
    res = generator.build(wb, generator.GenOptions(
        signals=["d_logic_bt_lp_reserve"], top_output_only=False,
        vector_overrides={"d_logic_bt_lp_reserve": [v_filled, v_unfilled, v_neg]}))
    st = res["blocks"][0][1]
    assert st["n_vectors"] == 3 and st["n_negative"] == 1
    assert st["n_designer"] == 1                       # 只有 v_filled 算手填
    assert res["summary"]["n_designer"] == 1


def test_sv_comments_annotate_designer_filled(wb):
    """comments 模式：手填期望的断言上方加注释；与 auto_out 不同的额外提示"表达式可能与意图不符"。"""
    sig, node, bindings, groups = _reserve(wb)
    v_same = V.make_vector_from_base_values(node, bindings, groups, BV_ONE, sig.out_width,
                                            index=0, designer_expected=1)   # == auto
    v_diff = V.make_vector_from_base_values(node, bindings, groups, BV_ONE, sig.out_width,
                                            index=1, designer_expected=0)   # != auto
    lines, _st = W.render_signal_block(sig, bindings, [v_same, v_diff],
                                       {"truncated": False}, comments=True, node=node)
    text = "\n".join(lines)
    assert "designer-filled expected (matches expression value)" in text
    assert "does not match designer intent" in text


# ───────────── 报告层：report() 拆分 auto_out / 期望 ─────────────
def test_report_tables_have_auto_out_and_designer_flag(wb):
    """report() 真值表每测试带 auto_out / expected / designer_filled / auto_num。"""
    sig, node, bindings, groups = _reserve(wb)
    vec = V.make_vector_from_base_values(node, bindings, groups, BV_ONE, sig.out_width,
                                         designer_expected=0)    # != auto_out(1)
    rep = generator.report(wb, generator.GenOptions(
        signals=["d_logic_bt_lp_reserve"], top_output_only=False,
        vector_overrides={"d_logic_bt_lp_reserve": [vec]}))
    t = next(x for x in rep["tables"] if x["signal"] == "d_logic_bt_lp_reserve")
    tc = t["tests"][0]
    assert tc["auto_out"] == "1" and tc["expected"] == "0"
    assert tc["designer_filled"] is True and tc["neg"] is False
    assert tc["auto_num"] == 1 and tc["width"] == 1
    assert t["auto_label"].startswith("auto_out")
    # 明细行：auto_out 列 + 期望来源列
    d = next(r for r in rep["detail"] if r["signal"] == "d_logic_bt_lp_reserve")
    assert d["auto_out"] == "1'b1" and d["expected"] == "1'b0"
    assert d["exp_src"] == "designer手填"


def test_report_unfilled_marks_fallback(wb):
    """未填期望的测试在报告里标 auto_out兜底。"""
    rep = generator.report(wb, generator.GenOptions(
        signals=["d_logic_bt_lp_reserve"], top_output_only=False))
    t = next(x for x in rep["tables"] if x["signal"] == "d_logic_bt_lp_reserve")
    assert all(not tc["designer_filled"] for tc in t["tests"])
    assert all(tc["expected"] == tc["auto_out"] for tc in t["tests"])   # 兜底=auto
    d = [r for r in rep["detail"] if r["signal"] == "d_logic_bt_lp_reserve"]
    assert all(r["exp_src"] == "auto_out兜底" for r in d)


def test_report_negative_exp_src(wb):
    """负向测试的期望来源 = 负向(故意填错)。"""
    rep = generator.report(wb, generator.GenOptions(
        signals=["d_logic_bt_lp_reserve"], top_output_only=False,
        neg_all=True, neg_which="first"))
    rows = [r for r in rep["detail"] if r["signal"] == "d_logic_bt_lp_reserve"]
    neg_rows = [r for r in rows if r["neg"] == "是"]
    assert neg_rows and all(r["exp_src"] == "负向(故意填错)" for r in neg_rows)


# ───────────── HTML 报告：两行 + 真值表检查 tab ─────────────
def test_html_report_has_check_tab(wb, tmp_path):
    """HTML 报告含「真值表检查」tab：全局按钮 / 可遮盖 auto_out / 可填空期望 / 判定 JS。"""
    from dreg_verify import cli
    rep = generator.report(wb, generator.GenOptions(top_output_only=False))
    path = tmp_path / "r.html"
    cli.write_report(str(path), rep, "synthetic.xlsx")
    html = path.read_text(encoding="utf-8")
    # 五个 tab
    assert 'data-tab="chk"' in html and "真值表检查" in html and 'id="chk"' in html
    # 全局按钮 + 计分 + 说明
    assert 'id="chkbtn"' in html and 'id="chkscore"' in html
    # 检查表格结构：auto_out 格(cauto, 带数值 data-v) + 期望填空格(cquiz, 含 input)
    assert 'class="cauto"' in html or "cauto" in html
    assert "cquiz" in html and 'class="cin"' in html and "data-v=" in html
    # JS：数值解析 + 判定逻辑
    assert "parseVal" in html and "okc" in html and "badc" in html


def test_html_report_truth_table_two_rows(wb, tmp_path):
    """HTML 真值表 tab：auto_out 与 期望 拆成两行（autorow + exprow）。"""
    from dreg_verify import cli
    sig, node, bindings, groups = _reserve(wb)
    v1 = V.make_vector_from_base_values(node, bindings, groups, BV_ONE, sig.out_width,
                                        index=0, designer_expected=1)   # 手填且==auto
    v2 = V.make_vector_from_base_values(node, bindings, groups, BV_ONE, sig.out_width,
                                        index=1, designer_expected=0)   # 手填但!=auto
    v3 = V.make_vector_from_base_values(node, bindings, groups, BV_ONE, sig.out_width,
                                        index=2)                        # 未填(兜底)
    rep = generator.report(wb, generator.GenOptions(
        signals=["d_logic_bt_lp_reserve"], top_output_only=False,
        vector_overrides={"d_logic_bt_lp_reserve": [v1, v2, v3]}))
    path = tmp_path / "r.html"
    cli.write_report(str(path), rep, "synthetic.xlsx")
    html = path.read_text(encoding="utf-8")
    assert 'class="autorow"' in html               # auto_out 行
    assert "auto_out" in html
    assert 'class="dsgn"' in html or "dsgn" in html        # 手填且一致(绿)
    assert "dsgndiff" in html                              # 手填但不一致(红)
    assert 'class="fb"' in html or '"fb"' in html          # 未填兜底(灰)


def test_csv_report_detail_has_auto_out_column(wb, tmp_path):
    """CSV 明细含 auto_out 与期望来源列。"""
    from dreg_verify import cli
    rep = generator.report(wb, generator.GenOptions(top_output_only=False))
    path = tmp_path / "r.csv"
    cli.write_report(str(path), rep, "synthetic.xlsx")
    content = path.read_text(encoding="utf-8-sig")
    assert "auto_out(表达式计算)" in content and "期望来源" in content
    assert "auto_out兜底" in content


# ───────────── GUI：两行 / 手填 / auto→期望 / 持久化 ─────────────
@pytest.fixture(scope="module")
def qapp():
    pytest.importorskip("PySide6")
    from PySide6 import QtWidgets
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def _reserve_window(tmp_path_factory, sub):
    from dreg_verify import gui
    path = tmp_path_factory.mktemp(sub) / "synthetic_dreg.xlsx"
    fixtures.build_workbook(str(path))
    w = gui.MainWindow()
    w.path_edit.setText(str(path)); w.on_load()
    sig = next(s for s in w.signals if s.out_name == "d_logic_bt_lp_reserve")
    return gui, w, sig


def test_gui_truth_table_split_rows(qapp, wb, tmp_path_factory):
    """GUI 真值表 = 输入行 + auto_out 行(只读) + 期望行(可编辑)。"""
    from PySide6 import QtCore
    gui, w, sig = _reserve_window(tmp_path_factory, "de1")
    w._load_test_items(sig)
    G = len(w._ti_groups)
    assert w.ti_table.rowCount() == G + 2
    assert w.R_AUTO == G and w.R_EXP == G + 1
    # 行表头
    assert w.ti_table.verticalHeaderItem(w.R_AUTO).text().startswith("auto_out")
    assert "期望" in w.ti_table.verticalHeaderItem(w.R_EXP).text()
    # auto_out 行只读、有值；期望行可编辑、未填=空白
    auto_it = w.ti_table.item(w.R_AUTO, 0)
    exp_it = w.ti_table.item(w.R_EXP, 0)
    assert not (auto_it.flags() & QtCore.Qt.ItemIsEditable)
    assert auto_it.text() != ""
    assert exp_it.flags() & QtCore.Qt.ItemIsEditable
    assert exp_it.text() == ""                            # 未填显示空白


def test_gui_fill_expected_not_negative(qapp, wb, tmp_path_factory):
    """⭐GUI 手填期望（即使 != auto_out）不标负向；清空恢复兜底。"""
    gui, w, sig = _reserve_window(tmp_path_factory, "de2")
    w._load_test_items(sig)
    rd = w._ti_rows[0]
    auto = rd["correct"]
    wrong_of_auto = 1 - (auto & 1)
    # 手填一个与 auto_out 不同的值
    w.ti_table.item(w.R_EXP, 0).setText(str(wrong_of_auto))
    assert rd["designer_expected"] == wrong_of_auto
    assert not rd["is_negative"]                          # 不是负向！
    assert rd["expected"] == wrong_of_auto                # 进 .sv 的对比值 = 手填值
    assert rd["correct"] == auto                          # auto_out 不变
    assert "d_logic_bt_lp_reserve" in w._customized
    # 流到 .sv：断言用手填值、无 _NEG
    res = generator.build(w.wb, w._opts([sig.out_name]))
    text = generator.render(res)
    assert ("==1'b%d)begin" % wrong_of_auto) in text
    assert "T0_NEG" not in text
    # 清空 → 恢复未填(兜底 auto_out)
    w.ti_table.item(w.R_EXP, 0).setText("")
    assert rd["designer_expected"] is None
    assert rd["expected"] == auto


def test_gui_neg_row_expected_still_wrong_value(qapp, wb, tmp_path_factory):
    """负向列的期望行编辑仍是改错值（与 designer 期望是两回事）。"""
    gui, w, sig = _reserve_window(tmp_path_factory, "de3")
    w._load_test_items(sig)
    w.on_ti_add_neg_all()
    neg_col = next(i for i, rd in enumerate(w._ti_rows) if rd.get("kind") == "neg")
    rd = w._ti_rows[neg_col]
    correct = rd["correct"]
    assert rd["expected"] != correct                      # 默认错值 = 取反
    # 编辑负向列期望（cell 当前显示取反值；填"正确值"才会触发 itemChanged）
    # → 走 wrong_value 路径记录手填，但 1bit 下手填值==正确值会被强制翻回"错"(保证自检语义)
    w.ti_table.item(w.R_EXP, neg_col).setText(str(correct & 1))
    assert rd["is_negative"]
    assert rd["wrong_value"] == (correct & 1)             # 手填值已记录到 wrong_value
    assert rd["expected"] != correct                      # expected 仍被强制保持"错"
    assert rd.get("designer_expected") is None            # 负向不碰 designer 期望


def test_gui_auto_fill_button(qapp, wb, tmp_path_factory, monkeypatch):
    """「auto→期望」按钮：只填未填的列；已手填/负向的不动。"""
    from PySide6 import QtWidgets
    gui, w, sig = _reserve_window(tmp_path_factory, "de4")
    w._load_test_items(sig)
    n_pos = len([rd for rd in w._ti_rows if rd.get("kind") != "neg"])
    # 先手填第 0 列一个与 auto 不同的值（不应被按钮覆盖）
    rd0 = w._ti_rows[0]
    hand_val = 1 - (rd0["correct"] & 1)
    w.ti_table.item(w.R_EXP, 0).setText(str(hand_val))
    # 跳过确认对话框
    monkeypatch.setattr(QtWidgets.QMessageBox, "question",
                        staticmethod(lambda *a, **k: QtWidgets.QMessageBox.Yes))
    w.on_ti_fill_expected()
    # 全部正向列都已填；第 0 列保持手填值，其余 = auto_out
    for i, rd in enumerate(w._ti_rows):
        if rd.get("kind") == "neg":
            continue
        assert rd["designer_expected"] is not None
        if i == 0:
            assert rd["designer_expected"] == hand_val    # 已手填的不被覆盖
        else:
            assert rd["designer_expected"] == rd["correct"]
    # 头部进度显示
    assert ("期望已手填 %d/%d" % (n_pos, n_pos)) in w.ti_header.text()


def test_gui_header_fill_progress(qapp, wb, tmp_path_factory):
    """编辑器头部显示期望手填进度 X/Y。"""
    gui, w, sig = _reserve_window(tmp_path_factory, "de5")
    w._load_test_items(sig)
    n_pos = len(w._ti_rows)
    assert ("期望已手填 0/%d" % n_pos) in w.ti_header.text()
    w.ti_table.item(w.R_EXP, 0).setText(str(w._ti_rows[0]["correct"]))
    assert ("期望已手填 1/%d" % n_pos) in w.ti_header.text()


def test_gui_copy_column_carries_designer_expected(qapp, wb, tmp_path_factory):
    """复制列把手填期望也带走。"""
    gui, w, sig = _reserve_window(tmp_path_factory, "de6")
    w._load_test_items(sig)
    w.ti_table.item(w.R_EXP, 0).setText(str(w._ti_rows[0]["correct"]))
    w.ti_table.setCurrentCell(0, 0)
    w.on_ti_copy()
    assert w._ti_rows[1].get("designer_expected") == w._ti_rows[0]["designer_expected"]


def test_gui_mux_readonly_two_rows(qapp, tmp_path_factory):
    """mux 只读表也拆 auto_out / 期望 两行（v1 同值，排版与 logic 编辑器一致）。"""
    from dreg_verify import gui
    path = tmp_path_factory.mktemp("de_mux") / "synthetic_dreg.xlsx"
    fixtures.build_workbook(str(path), with_mux=True)
    w = gui.MainWindow()
    w.path_edit.setText(str(path)); w.on_load()
    grp = next(s for s in w.signals if getattr(s, "suffix", "") == "mux")
    w._load_test_items(grp)
    labels = [w.ti_table.verticalHeaderItem(i).text() for i in range(w.ti_table.rowCount())]
    assert "auto_out" in labels and any("期望" in x for x in labels)


# ───────────── 持久化：序列化 / 恢复 / 导入导出 ─────────────
def test_serialize_rows_roundtrip():
    """rowdict 序列化往返：保留输入取值与用户意图字段，丢计算字段。"""
    from dreg_verify import gui
    rows = [{"base_values": {"a": 1, "b": 0}, "kind": "pos", "designer_expected": 1,
             "correct": 1, "expected": 1, "_vec": object(), "is_negative": False},
            {"base_values": {"a": 0}, "kind": "neg", "wrong_value": 1, "name": "my_neg",
             "user_added": True, "note": "负向"},
            {"base_values": {"a": 1}, "kind": "pos"}]      # 未填期望
    ser = gui._serialize_rows(rows)
    # JSON 可序列化（_vec 等已剥掉）
    txt = json.dumps(ser, ensure_ascii=False)
    back = gui._deserialize_rows(json.loads(txt))
    assert back[0]["base_values"] == {"a": 1, "b": 0}
    assert back[0]["designer_expected"] == 1 and back[0]["kind"] == "pos"
    assert "correct" not in back[0] and "_vec" not in back[0]    # 计算字段不持久化
    assert back[1]["kind"] == "neg" and back[1]["wrong_value"] == 1
    assert back[1]["name"] == "my_neg" and back[1]["user_added"]
    assert back[2].get("designer_expected") is None              # 未填仍未填


def test_gui_edits_persist_across_reload(qapp, tmp_path_factory, monkeypatch):
    """⭐编辑(含手填期望)持久化：关 GUI 重开（重新 on_load）后还在。"""
    from dreg_verify import gui
    edits_path = str(tmp_path_factory.mktemp("persist") / "edits.json")
    monkeypatch.setattr(gui, "EDITS_PATH", edits_path)
    path = tmp_path_factory.mktemp("de_p") / "synthetic_dreg.xlsx"
    fixtures.build_workbook(str(path))

    # 第一个窗口：手填期望 + 加负向
    w1 = gui.MainWindow()
    w1.path_edit.setText(str(path)); w1.on_load()
    sig = next(s for s in w1.signals if s.out_name == "d_logic_bt_lp_reserve")
    w1._load_test_items(sig)
    auto0 = w1._ti_rows[0]["correct"]
    hand_val = 1 - (auto0 & 1)
    w1.ti_table.item(w1.R_EXP, 0).setText(str(hand_val))     # 手填(!=auto)
    w1.on_ti_add_neg_all()
    n_rows = len(w1._ti_rows)
    assert os.path.isfile(edits_path)                          # 已落盘

    # 第二个窗口（模拟重开 GUI）：自动恢复
    w2 = gui.MainWindow()
    w2.path_edit.setText(str(path)); w2.on_load()
    assert "d_logic_bt_lp_reserve" in w2._customized
    sig2 = next(s for s in w2.signals if s.out_name == "d_logic_bt_lp_reserve")
    w2._load_test_items(sig2)
    assert len(w2._ti_rows) == n_rows
    assert w2._ti_rows[0]["designer_expected"] == hand_val    # 手填期望还在
    assert not w2._ti_rows[0]["is_negative"]
    assert any(rd.get("kind") == "neg" for rd in w2._ti_rows)  # 负向还在
    # 左表负向勾选已同步
    from PySide6 import QtCore
    row = next(r for r in range(w2.table.rowCount())
               if w2._sig_of_row(r).out_name == "d_logic_bt_lp_reserve")
    assert w2.table.item(row, gui.COL_NEG).checkState() == QtCore.Qt.Checked
    # 生成的 .sv 用恢复的手填期望
    res = generator.build(w2.wb, w2._opts([sig2.out_name]))
    text = generator.render(res)
    assert ("==1'b%d)begin" % hand_val) in text


def test_gui_regen_clears_persisted(qapp, tmp_path_factory, monkeypatch):
    """重新生成(丢弃自定义) → 持久化文件同步清掉，重开不会恢复回来。"""
    from dreg_verify import gui
    edits_path = str(tmp_path_factory.mktemp("persist2") / "edits.json")
    monkeypatch.setattr(gui, "EDITS_PATH", edits_path)
    path = tmp_path_factory.mktemp("de_p2") / "synthetic_dreg.xlsx"
    fixtures.build_workbook(str(path))
    w = gui.MainWindow()
    w.path_edit.setText(str(path)); w.on_load()
    sig = next(s for s in w.signals if s.out_name == "d_logic_bt_lp_reserve")
    w._load_test_items(sig)
    w.ti_table.item(w.R_EXP, 0).setText("1")
    assert json.load(open(edits_path, encoding="utf-8"))       # 有内容
    w.on_ti_regen()
    data = json.load(open(edits_path, encoding="utf-8")) if os.path.isfile(edits_path) else {}
    assert not data.get(str(path), {}).get("edits")            # 已清空


def test_gui_export_import_edits_bucket(qapp, tmp_path_factory, monkeypatch):
    """导出/导入编辑：bucket 应用到新窗口后手填期望/负向都回来；找不到的信号列出名字。"""
    from dreg_verify import gui
    monkeypatch.setattr(gui, "EDITS_PATH",
                        str(tmp_path_factory.mktemp("persist3") / "edits.json"))
    path = tmp_path_factory.mktemp("de_imp") / "synthetic_dreg.xlsx"
    fixtures.build_workbook(str(path))
    w = gui.MainWindow()
    w.path_edit.setText(str(path)); w.on_load()
    sig = next(s for s in w.signals if s.out_name == "d_logic_bt_lp_reserve")
    w._load_test_items(sig)
    w.ti_table.item(w.R_EXP, 0).setText(str(1 - (w._ti_rows[0]["correct"] & 1)))
    # 构造导出 payload（与 on_export_edits 同构）
    payload = {"dreg_verify_edits": 1, "excel": "x.xlsx",
               "edits": {name: gui._serialize_rows(ed["rows"]) for name, ed in w._edited.items()},
               "neg_only": dict(w._neg_only)}
    payload["edits"]["ghost_signal_not_in_excel"] = [{"base_values": {}, "kind": "pos"}]
    # 应用到一个全新窗口
    w2 = gui.MainWindow()
    w2.path_edit.setText(str(path)); w2.on_load()
    w2.on_ti_regen() if w2._customized else None
    n, missing = w2._apply_edits_bucket(payload)
    assert n == 1
    assert "ghost_signal_not_in_excel" in missing              # 找不到的列名字
    assert "d_logic_bt_lp_reserve" in w2._edited
    rows = w2._edited["d_logic_bt_lp_reserve"]["rows"]
    assert rows[0]["designer_expected"] is not None


# ───────────── 覆盖度切换 / 负向重建 不丢手填期望 ─────────────
def test_set_negatives_preserves_designer_expected(qapp, wb, tmp_path_factory):
    """给信号加负向(重建行列表)时，正向行的手填期望原样保留。"""
    gui, w, sig = _reserve_window(tmp_path_factory, "de7")
    w._load_test_items(sig)
    hand_val = 1 - (w._ti_rows[0]["correct"] & 1)
    w.ti_table.item(w.R_EXP, 0).setText(str(hand_val))
    w._set_signal_negatives(sig, True, "all")              # 重建：正向 + 每条一负向
    w._load_test_items(sig)
    pos0 = next(rd for rd in w._ti_rows if rd.get("kind") != "neg")
    assert pos0["designer_expected"] == hand_val           # 手填期望没丢
