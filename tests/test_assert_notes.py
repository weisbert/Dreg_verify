# -*- coding: utf-8 -*-
"""断言信息量增强（2026-06-02 拍板 + 2026-06-03 反例语义更正）：
  1. 反例自检式（2026-06-03 更正）：断言语法与正例完全一样(==)、期望值故意填错 →
     RTL 正确时必然 FAIL → uvm_report_error 正常触发（验证系统能看见报错，自检 checker）。
     error 分支带 NEG-EXPECTED-FAIL 标签；竟然 PASS(输出==错值) → info + NEG-BROKEN 标签。
  2. 末尾计数器汇总：begin/end 命名块 + 计数变量 + 汇总行(信号/断言/正反例数 + 运行时真 FAIL)
  3. log 标签分开正反例：_NEG 后缀 + NEG-EXPECTED-FAIL/NEG-BROKEN 标签
  4. owner 追加在消息尾部(前半段格式不变)，产物纯英文
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dreg_verify import excel_model, generator   # noqa: E402
from dreg_verify import sv_writer as W           # noqa: E402
from dreg_verify import vectors as V             # noqa: E402
from dreg_verify import resolver as R            # noqa: E402
import fixtures                                  # noqa: E402


@pytest.fixture(scope="module")
def wb(tmp_path_factory):
    path = tmp_path_factory.mktemp("xl_notes") / "synthetic_dreg.xlsx"
    fixtures.build_workbook(str(path))
    return excel_model.load_workbook(str(path))


@pytest.fixture(scope="module")
def wb_pll(tmp_path_factory):
    path = tmp_path_factory.mktemp("xl_notes_pll") / "synthetic_pll.xlsx"
    fixtures.build_workbook(str(path), with_pll_chain=True)
    return excel_model.load_workbook(str(path))


def _build_text(wb_, **kw):
    opts = generator.GenOptions(**kw)
    res = generator.build(wb_, opts)
    return generator.render(res, comments=opts.comments), res


# ───────────── 反例：自检式（断言语法与正例一模一样） ─────────────
def test_neg_assert_same_syntax_as_positive(wb):
    """反例断言语法与正例完全一样(==)，仅期望值故意填错——产物里绝不出现 !=。
    这样 RTL 正确时反例必然 FAIL → uvm_report_error 正常触发 → 验证系统能看见报错。"""
    text, res = _build_text(wb, signals=["d_logic_bt_lp_reserve"],
                            neg_signals=["d_logic_bt_lp_reserve"])
    # 所有断言(正例+反例)都用 ==
    n_assert_lines = sum(1 for ln in text.splitlines() if ln.startswith("assert ("))
    assert n_assert_lines == res["summary"]["n_vectors"]
    assert text.count("assert (`ENV_RF.d_logic_bt_lp_reserve==") == n_assert_lines
    assert "!=" not in text
    # 反例断言的 error 分支结构与正例一致(else begin + uvm_report_error)
    assert text.count("else begin") == n_assert_lines


def test_neg_branch_severity(wb):
    """NEG-EXPECTED-FAIL 标签只跟 uvm_report_error(预期内的报错，系统看得见)；
    NEG-BROKEN(竟然 PASS=反例没起作用)只跟 uvm_report_info。"""
    text, res = _build_text(wb, neg_all=True)
    n_exp = n_broken = 0
    for ln in text.splitlines():
        if "NEG-EXPECTED-FAIL" in ln:
            assert W.UVM_ERROR + "(" in ln
            n_exp += 1
        if "NEG-BROKEN" in ln:
            assert W.UVM_INFO + "(" in ln and W.UVM_ERROR not in ln
            n_broken += 1
    # 每条反例恰好一对 error(NEG-EXPECTED-FAIL)/info(NEG-BROKEN) 分支
    assert n_exp == n_broken == res["summary"]["n_negative"] >= 1


def test_neg_label_and_tags_greppable(wb):
    """正反例可用 grep 分离：断言 id 带 _NEG 后缀，且 NEG 标签行都含 _NEG id。"""
    text, _ = _build_text(wb, signals=["d_logic_bt_lp_reserve"],
                          neg_signals=["d_logic_bt_lp_reserve"])
    assert "_NEG:" in text                                       # 标号行
    for ln in text.splitlines():
        if "NEG-EXPECTED-FAIL" in ln or "NEG-BROKEN" in ln:
            assert "_NEG" in ln                                  # 消息行的 id 也带


def test_positive_assert_format_unchanged(wb):
    """正例断言消息与历史格式逐字一致（无任何新尾巴）——旧 log 解析脚本不受影响。"""
    text, _ = _build_text(wb, signals=["d_en_refbuf"])
    assert ('$sformatf("assert_%s: %s, sim out:0x%0h, you set:0x%0h",'
            '"50_T0","d_en_refbuf_ls",`ENV_RF.d_en_refbuf_ls, 1\'b') in text
    assert "owner" not in text and "NEG-" not in text and "dreg_n_" not in text


# ───────────── owner 追加在消息尾部 ─────────────
def test_owner_appended_to_msg(wb):
    """owner_in_msg=True：格式串尾部加 ', owner:%s'，owner 作 $sformatf 最后一个实参。"""
    text, _ = _build_text(wb, signals=["d_logic_bt_lp_reserve"], owner_in_msg=True)
    # 前半段保持原样 + owner 追加在尾部
    assert "assert_%s: %s, sim out:0x%0h, you set:0x%0h, owner:%s" in text
    assert ',"Alice")' in text


def test_owner_in_neg_msgs_too(wb):
    """反例消息也带 owner，顺序固定：NEG 标签在前、owner 在最后。"""
    text, _ = _build_text(wb, signals=["d_logic_bt_lp_reserve"],
                          neg_signals=["d_logic_bt_lp_reserve"], owner_in_msg=True)
    assert "NEG-EXPECTED-FAIL(wrong value set on purpose), owner:%s" in text
    assert "NEG-BROKEN(no error fired, output equals the wrong value), owner:%s" in text


def test_owner_off_by_default(wb):
    """默认不带 owner（向后兼容：产物与旧版正例输出一致）。"""
    text, _ = _build_text(wb, signals=["d_logic_bt_lp_reserve"])
    assert "owner" not in text


def test_owner_with_space(wb_pll):
    """owner 含空格(Yao Wang)原样进消息实参。"""
    text, _ = _build_text(wb_pll, signals=["pll_n"], owner_in_msg=True)
    assert ',"Yao Wang")' in text


def test_owner_empty_becomes_na(wb):
    """owner 为空 → 写 NA，不留空串。"""
    sig = next(s for s in wb.logic if s.out_name == "d_en_refbuf")
    old = sig.owner
    try:
        sig.owner = ""
        resolver = R.Resolver(wb)
        node, bindings, _exp = generator.expand_signal(wb, resolver, sig)
        vecs, meta = V.generate_vectors(node, bindings, sig.out_width)
        lines, _ = W.render_signal_block(sig, bindings, vecs, meta, node=node,
                                         owner_in_msg=True)
        assert ',"NA")' in "\n".join(lines)
    finally:
        sig.owner = old


def test_ascii_sanitize_helper():
    """_ascii：非 ASCII → ?；双引号 → 单引号；换行/制表折叠；反斜杠 → /。
    任何一条没做到都会破坏 SV 字符串字面量 → elaboration 失败。"""
    assert W._ascii("Alice") == "Alice"
    assert W._ascii('Yao "Wang"') == "Yao 'Wang'"
    assert all(ord(c) < 128 for c in W._ascii("王小明"))
    # Excel 单元格 Alt+Enter 换行：SV 字符串不能跨行
    assert W._ascii("Wei\nYu") == "Wei Yu"
    assert W._ascii("Wei\r\nYu\tZhang") == "Wei Yu Zhang"
    # 尾部反斜杠会把收尾引号转义掉 → 替换成 /
    assert "\\" not in W._ascii("Alice\\")
    assert "\\" not in W._ascii("C:\\temp\\bob")
    # 残余控制字符
    assert all(c.isprintable() for c in W._ascii("a\x01b\x7fc"))


def test_owner_with_newline_keeps_sv_legal(wb):
    """回归(审查发现-critical)：owner 含换行/尾部反斜杠时，产物每条语句仍是单行、
    字符串字面量不断裂（否则 ncelab 词法错误，elaboration 失败）。"""
    sig = next(s for s in wb.logic if s.out_name == "d_en_refbuf")
    old = sig.owner
    try:
        for bad in ("Wei\nYu", "Alice\\", "Wei\r\n Yu \t(backup)"):
            sig.owner = bad
            resolver = R.Resolver(wb)
            node, bindings, _exp = generator.expand_signal(wb, resolver, sig)
            vecs, meta = V.generate_vectors(node, bindings, sig.out_width)
            lines, _ = W.render_signal_block(sig, bindings, vecs, meta, node=node,
                                             owner_in_msg=True)
            for ln in lines:
                assert "\n" not in ln and "\r" not in ln     # 每条语句单行
                assert "\\" not in ln                        # 无反斜杠转义风险
                assert ln.count('"') % 2 == 0                # 引号成对(字符串不断裂)
    finally:
        sig.owner = old


# ───────────── 末尾计数器汇总 ─────────────
def test_summary_wrap_structure(wb):
    """sv_summary=True：命名 begin/end 块包裹 + 声明/赋0分离 + 声明先于一切语句。"""
    text, _ = _build_text(wb, neg_all=True, sv_summary=True)
    lines = text.splitlines()
    assert lines[0] == "begin : dreg_rf_test"
    assert lines[-1] == "end : dreg_rf_test"
    # 声明与赋 0 分开写：静态作用域(module initial)里带初始化的块内声明非法，
    # 分开写则贴进 task 体 / initial 块都合法
    assert "    int unsigned dreg_n_real_fail;" in lines
    assert "    int unsigned dreg_n_neg_broken;" in lines
    assert "    dreg_n_real_fail = 0;" in lines
    assert "    dreg_n_neg_broken = 0;" in lines
    # SV 要求块内声明必须先于语句
    decl_idx = lines.index("    int unsigned dreg_n_real_fail;")
    first_stmt = next(i for i, ln in enumerate(lines)
                      if ln.startswith("`RF_WRITE") or ln.startswith("force "))
    assert decl_idx < first_stmt


def test_summary_static_counts(wb):
    """汇总行静态数字 = 实际生成的信号数/断言数/正反例数；运行时计数器作实参。
    措辞说明反例的 FAIL 是故意的(intentional FAILs)。"""
    text, res = _build_text(wb, neg_all=True, sv_summary=True)
    s = res["summary"]
    n_sig, n_vec, n_neg = s["n_generated"], s["n_vectors"], s["n_negative"]
    # 静态数字写死在格式串里，运行时计数器保留 %0d 占位
    expect = ("signals:%d asserts:%d (positive:%d, negative:%d intentional FAILs), "
              "REAL FAIL:%%0d, NEG broken(no error fired):%%0d"
              % (n_sig, n_vec, n_vec - n_neg, n_neg))
    assert expect in text
    sum_line = next(ln for ln in text.splitlines() if "rf_test summary" in ln)
    assert W.UVM_INFO in sum_line
    assert "dreg_n_real_fail,dreg_n_neg_broken" in sum_line


def test_summary_counter_placement(wb):
    """计数器++位置：正例 fail(else/error 分支后)→real_fail++；
    反例竟然 PASS(begin/info 分支后)→neg_broken++；反例的 error 分支无计数(预期内 FAIL 不算真问题)。"""
    text, res = _build_text(wb, neg_all=True, sv_summary=True)
    lines = text.splitlines()
    n_real = sum(1 for ln in lines if ln.strip() == "dreg_n_real_fail++;")
    n_negb = sum(1 for ln in lines if ln.strip() == "dreg_n_neg_broken++;")
    s = res["summary"]
    assert n_real == s["n_vectors"] - s["n_negative"]    # 每条正例一个(else 分支)
    assert n_negb == s["n_negative"]                     # 每条反例一个(begin 分支)
    # real_fail++ 紧跟 uvm_report_error(正例 fail)；neg_broken++ 紧跟 uvm_report_info(反例竟然 PASS)
    for i, ln in enumerate(lines):
        if ln.strip() == "dreg_n_real_fail++;":
            assert W.UVM_ERROR in lines[i - 1]
        elif ln.strip() == "dreg_n_neg_broken++;":
            assert W.UVM_INFO in lines[i - 1] and "NEG-BROKEN" in lines[i - 1]


def test_summary_off_no_artifacts(wb):
    """默认不开汇总 → 无包裹/无声明/无计数器（产物结构与旧版一致）。"""
    text, _ = _build_text(wb, neg_all=True)
    assert "dreg_n_" not in text
    assert "begin : dreg_rf_test" not in text
    assert "rf_test summary" not in text


def test_summary_begin_end_balanced(wb):
    """begin/end 配平（粗 SV 结构自检：每断言 2 对 + 外层 1 对）。"""
    text, res = _build_text(wb, neg_all=True, sv_summary=True, owner_in_msg=True)
    n_begin = text.count("begin")
    n_end = sum(1 for ln in text.splitlines()
                if ln.strip() == "end" or ln.strip().startswith("end :"))
    assert n_begin == 2 * res["summary"]["n_vectors"] + 1
    assert n_end == n_begin


def test_full_featured_output_shape(wb):
    """全功能产物形状：块头 → 2声明 → 2赋0 → 空行 → 主体 → 空行 → 汇总 → 块尾。"""
    text, _ = _build_text(wb, neg_all=True, owner_in_msg=True, sv_summary=True)
    lines = text.splitlines()
    assert lines[0].startswith("begin : ")
    assert lines[1].strip().startswith("int unsigned ")
    assert lines[2].strip().startswith("int unsigned ")
    assert lines[3].strip().endswith(" = 0;")
    assert lines[4].strip().endswith(" = 0;")
    assert lines[5] == ""
    assert "rf_test summary" in lines[-2]
    assert lines[-1].startswith("end : ")


# ───────────── 纯英文（ASCII）产物 ─────────────
def test_output_pure_ascii(wb):
    """全部新选项 + 注释模式：产物必须纯 ASCII（用户要求全英文）。"""
    opts = generator.GenOptions(neg_all=True, owner_in_msg=True, sv_summary=True,
                                comments=True)
    text = generator.render(generator.build(wb, opts), comments=True)
    text.encode("ascii")    # 含任何中文/全角字符会抛 UnicodeEncodeError


def test_output_pure_ascii_pll_with_prefix(wb_pll):
    """pll 链 + 探针前缀 + 全选项：产物仍纯 ASCII。"""
    opts = generator.GenOptions(neg_all=True, owner_in_msg=True, sv_summary=True,
                                comments=True,
                                probe_prefixes={"pll_n": "U_BT_LP_PLL_DIG"})
    text = generator.render(generator.build(wb_pll, opts), comments=True)
    text.encode("ascii")


# ───────────── 渲染兼容性 ─────────────
def test_render_accepts_legacy_result_dict(wb):
    """render() 兼容不带 sv_summary 键的结果 dict（外部构造/旧代码路径）。"""
    res = generator.build(wb, generator.GenOptions(signals=["d_en_refbuf"]))
    legacy = {"blocks": res["blocks"]}                  # 最小 dict，无 sv_summary 键
    text = generator.render(legacy)
    assert "begin : dreg_rf_test" not in text           # 默认不包裹
    assert "assert (`ENV_RF.d_en_refbuf_ls==" in text


def test_block_suffix(wb):
    """块名后缀(审查发现)：『仅正向』『仅负向』两份产物贴进同一作用域时块名不能重名。"""
    res = generator.build(wb, generator.GenOptions(neg_all=True, sv_summary=True))
    t_all = generator.render(res)
    t_pos = generator.render(res, block_suffix="_pos")
    t_neg = generator.render(res, block_suffix="_neg")
    assert "begin : dreg_rf_test\n" in t_all and "end : dreg_rf_test\n" in t_all
    assert "begin : dreg_rf_test_pos" in t_pos and "end : dreg_rf_test_pos" in t_pos
    assert "begin : dreg_rf_test_neg" in t_neg and "end : dreg_rf_test_neg" in t_neg
    # pos/neg 两份没有任何同名命名块
    assert "begin : dreg_rf_test\n" not in t_pos and "begin : dreg_rf_test\n" not in t_neg


# ───────────── 真·仅负向（negative_vectors_only） ─────────────
def test_negative_vectors_only(wb):
    """仅负向模式：每信号只留负向向量(T 编号不重排)；无负向的信号整个不出现；
    汇总数字反映过滤后的子集(positive:0)。"""
    res = generator.build(wb, generator.GenOptions(
        neg_signals=["d_logic_bt_lp_reserve"],         # 3 个信号里只有 1 个有负向
        negative_vectors_only=True, sv_summary=True))
    # 只有含负向的 reserve 出现
    assert res["summary"]["n_generated"] == 1
    assert res["summary"]["n_vectors"] == res["summary"]["n_negative"] == 1
    text = generator.render(res, block_suffix="_neg")
    # 断言全是反例：语法与正例一样(==)，id 全带 _NEG
    assert_ids = [ln for ln in text.splitlines()
                  if ln.startswith("assert_") and ln.endswith(":")]
    assert assert_ids and all(ln.endswith("_NEG:") for ln in assert_ids)
    assert "assert (`ENV_RF.d_logic_bt_lp_reserve==" in text
    # T 编号保持"全部"导出时的位置(负向追加在正例后 → 编号不是 T0)
    assert "_T0_NEG:" not in text
    # 汇总：positive:0、计数器只有 neg_broken 会被用到(在反例的 begin 分支)
    assert "(positive:0, negative:1" in text
    assert "dreg_n_real_fail++;" not in text
    assert "dreg_n_neg_broken++;" in text


# ───────────── CLI ─────────────
def test_cli_new_flags_parse():
    from dreg_verify import cli
    p = cli.build_argparser()
    args = p.parse_args(["--excel", "x.xlsx", "--owner-in-msg", "--sv-summary"])
    assert args.owner_in_msg and args.sv_summary
    args2 = p.parse_args(["--excel", "x.xlsx"])
    assert not args2.owner_in_msg and not args2.sv_summary


def test_cli_separate_neg_file_truly_negative_only(tmp_path):
    """CLI --neg-file separate：负向文件真·仅负向(无正例断言)、计数器有声明、
    块名带 _neg 后缀(与主文件贴同一作用域不重名)、汇总数字反映仅负向子集。"""
    from dreg_verify import cli
    xl = tmp_path / "x.xlsx"
    fixtures.build_workbook(str(xl))
    out = tmp_path / "wr_rf_tc.sv"
    cli.main(["--excel", str(xl), "--out", str(out), "--neg-all",
              "--neg-file", "separate", "--sv-summary", "--owner-in-msg"])
    pos_text = out.read_text(encoding="utf-8")
    neg_text = (tmp_path / "wr_rf_tc_neg.sv").read_text(encoding="utf-8")
    # 主文件：全正例(无 _NEG 断言)，块名无后缀
    assert "_NEG:" not in pos_text and "begin : dreg_rf_test\n" in pos_text
    # 负向文件：断言全是 _NEG(语法仍是 ==，与正例一模一样)
    neg_ids = [ln for ln in neg_text.splitlines()
               if ln.startswith("assert_") and ln.endswith(":")]
    assert neg_ids and all(ln.endswith("_NEG:") for ln in neg_ids)
    assert "!=" not in neg_text                          # 绝不出现 != (2026-06-03 更正)
    assert "NEG-EXPECTED-FAIL" in neg_text               # error 分支带预期内标签
    # 计数器++有配套声明(包裹仍在)，块名 _neg 后缀
    assert "begin : dreg_rf_test_neg" in neg_text and "end : dreg_rf_test_neg" in neg_text
    assert "int unsigned dreg_n_neg_broken;" in neg_text
    # 汇总反映仅负向子集
    assert "(positive:0, negative:" in neg_text
    # 两份文件无同名命名块(可贴同一作用域)
    assert "begin : dreg_rf_test\n" not in neg_text


def test_cli_main_full_flow(tmp_path):
    """CLI 全流程：--neg-all --owner-in-msg --sv-summary 全部落到产物。"""
    from dreg_verify import cli
    xl = tmp_path / "x.xlsx"
    fixtures.build_workbook(str(xl))
    out = tmp_path / "wr_rf_tc.sv"
    cli.main(["--excel", str(xl), "--out", str(out), "--neg-all",
              "--owner-in-msg", "--sv-summary"])
    text = out.read_text(encoding="utf-8")
    assert "owner:%s" in text
    assert "begin : dreg_rf_test" in text and "end : dreg_rf_test" in text
    assert "NEG-EXPECTED-FAIL" in text and "NEG-BROKEN" in text
    text.encode("ascii")


# ───────────── GUI ─────────────
def _make_gui(tmp_path_factory, name, pll=False):
    pytest.importorskip("PySide6")
    from PySide6 import QtWidgets
    QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    from dreg_verify import gui
    path = tmp_path_factory.mktemp(name) / "synthetic.xlsx"
    fixtures.build_workbook(str(path), with_pll_chain=pll)
    w = gui.MainWindow()
    w.path_edit.setText(str(path)); w.on_load()
    return gui, w


def test_gui_export_dialog_new_checkboxes(tmp_path_factory, monkeypatch, tmp_path):
    """导出对话框含 owner/汇总复选框(无历史配置时默认勾选)，返回 dict 带两个新键。"""
    gui, w = _make_gui(tmp_path_factory, "gui_dlg")
    from PySide6 import QtWidgets
    monkeypatch.setattr(gui, "SETTINGS_PATH", str(tmp_path / "no_settings.json"))
    monkeypatch.setattr(QtWidgets.QDialog, "exec", lambda self: QtWidgets.QDialog.Accepted)
    opt = w._ask_export_options("test")
    assert opt is not None
    assert opt["owner_in_msg"] is True
    assert opt["sv_summary"] is True
    assert opt["scope"] == "all"


def test_gui_opts_maps_settings(tmp_path_factory, monkeypatch):
    """_opts() 把导出设置映射到 GenOptions；无配置时默认 True；显式实参优先于设置。"""
    gui, w = _make_gui(tmp_path_factory, "gui_opts")
    monkeypatch.setattr(gui, "_load_settings",
                        lambda: {"export_owner_in_msg": False, "export_sv_summary": True})
    o = w._opts(None)
    assert o.owner_in_msg is False and o.sv_summary is True
    monkeypatch.setattr(gui, "_load_settings", lambda: {})
    o2 = w._opts(None)
    assert o2.owner_in_msg is True and o2.sv_summary is True
    # 显式实参(对话框返回值)优先，不受设置影响
    o3 = w._opts(None, owner_in_msg=False, sv_summary=False)
    assert o3.owner_in_msg is False and o3.sv_summary is False


def test_gui_generate_uses_dialog_choices(tmp_path_factory, monkeypatch, tmp_path):
    """回归(审查发现)：on_generate 必须以对话框返回值为准，不能依赖『先写盘再读回』——
    写盘失败/测试环境下设置不落盘，老逻辑会静默用错选项。
    对话框取消勾选 owner/汇总 → 产物里就不能有。"""
    from PySide6 import QtCore, QtWidgets
    gui, w = _make_gui(tmp_path_factory, "gui_gen_e2e")
    # 磁盘设置(默认 True) 与对话框返回值(False) 故意相反
    monkeypatch.setattr(gui, "SETTINGS_PATH", str(tmp_path / "no_settings.json"))
    out_sv = tmp_path / "out.sv"
    monkeypatch.setattr(w, "_ask_export_options",
                        lambda title: {"scope": "all", "comments": False,
                                       "owner_in_msg": False, "sv_summary": False})
    monkeypatch.setattr(QtWidgets.QFileDialog, "getSaveFileName",
                        lambda *a, **k: (str(out_sv), "SystemVerilog (*.sv)"))
    monkeypatch.setattr(QtWidgets.QMessageBox, "exec", lambda *a, **k: 0)
    monkeypatch.setattr(QtWidgets.QMessageBox, "information",
                        lambda *a, **k: QtWidgets.QMessageBox.Ok)
    row = next(r for r in range(w.table.rowCount())
               if w._sig_of_row(r).out_base == "d_en_refbuf")
    w.table.item(row, gui.COL_SEL).setCheckState(QtCore.Qt.Checked)
    w.on_generate()
    text = out_sv.read_text(encoding="utf-8")
    assert "assert (`ENV_RF.d_en_refbuf_ls==" in text    # 正常生成
    assert "owner" not in text                           # 对话框取消了 owner → 没有
    assert "dreg_rf_test" not in text                    # 对话框取消了汇总 → 无包裹/计数器


def test_gui_preview_reflects_options(tmp_path_factory, monkeypatch, tmp_path):
    """预览=导出(所见即所得)：默认设置下整体预览含 owner 尾巴 + 汇总包裹。"""
    from PySide6 import QtCore
    gui, w = _make_gui(tmp_path_factory, "gui_prev")
    monkeypatch.setattr(gui, "SETTINGS_PATH", str(tmp_path / "no_settings.json"))
    row = next(r for r in range(w.table.rowCount())
               if w._sig_of_row(r).out_base == "d_en_refbuf")
    w.table.item(row, gui.COL_SEL).setCheckState(QtCore.Qt.Checked)
    w.on_preview(switch_tab=False)
    text = w.preview.toPlainText()
    assert "owner:%s" in text and '"Alice"' in text
    assert "begin : dreg_rf_test" in text


def test_gui_signal_preview_owner_no_counters(tmp_path_factory, monkeypatch, tmp_path):
    """单信号预览带 owner(跟随导出设置)；但不含计数器(片段没有声明包裹，显示了反而困惑)。"""
    gui, w = _make_gui(tmp_path_factory, "gui_ti_owner")
    monkeypatch.setattr(gui, "SETTINGS_PATH", str(tmp_path / "no_settings.json"))
    sig = next(s for s in w.signals if s.out_base == "d_en_refbuf")
    w._load_test_items(sig)
    w.on_ti_preview_signal(switch_tab=False)
    text = w.preview.toPlainText()
    assert "owner:%s" in text and '"Alice"' in text
    assert "dreg_n_" not in text
