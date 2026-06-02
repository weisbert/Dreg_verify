# -*- coding: utf-8 -*-
"""Cone 展开测试 — 用真表 pll_n←pll_n2←pll_n1 三层链做 oracle（2026-06-02 实证数据）。

for_test 对 pll_n 的处理（块 行154..160）：
  驱动 6 个叶子寄存器: en_dig_clk_div2(0x1 bit7) en_dig_clk_div4(0x1 bit6)
                       int_n[8:0](0x2 15:7)  frac_n_msb[6:0](0x2 6:0)
                       frac_n_lsb[15:0](0x3) d_xo_freq_sel(0xC bit0)
  T0: int_n=256 frac_n_msb=64 frac_n_lsb=0xFFFF 其余=1
  → RF_WRITE 0x1=0xC0, 0x2=0x8040, 0x3=0xFFFF, 0xC=0x1
  期望(按 logic 表达式) pll_n = pll_n1 << 2 = 0x0081FFFC（for_test 写的 32'b1 是错的，不抄）。
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import fixtures                                     # noqa: E402
from dreg_verify import cone, excel_model, generator  # noqa: E402
from dreg_verify import expr as E                   # noqa: E402
from dreg_verify import resolver as R               # noqa: E402
from dreg_verify import vectors as V                # noqa: E402


@pytest.fixture(scope="module")
def wb(tmp_path_factory):
    path = tmp_path_factory.mktemp("cone") / "synthetic_pll.xlsx"
    fixtures.build_workbook(str(path), with_pll_chain=True)
    return excel_model.load_workbook(str(path))


@pytest.fixture(scope="module")
def resolver(wb):
    return R.Resolver(wb)


# ───────────── expr.Part 节点 ─────────────
def test_part_eval_and_width():
    node = E.Part(E.Var("A"), 30, 23)
    env = E.Env({"A": 32}, {"A": 0x40207FFF})
    assert E.self_width(node, env) == 8
    # 0x40207FFF bits[30:23] = 0x80
    assert E.evaluate(node, env, 8)[0] == 0x80
    # Part 嵌套: ((A)[30:23])[7]  → bit7 of 0x80 = 1
    nested = E.Part(E.Part(E.Var("A"), 30, 23), 7, 7)
    assert E.evaluate(nested, env, 1)[0] == 1
    assert E.collect_vars(nested) == ["A"]


# ───────────── 展开结构 ─────────────
def test_expand_pll_chain_leaves(wb, resolver):
    sig = next(s for s in wb.logic if s.out_base == "pll_n")
    node, bindings = cone.expand(sig, wb, resolver)
    # 叶子 = 6 个真寄存器，全部 RW 可驱动
    assert set(bindings) == {"INT_N", "FRAC_N_MSB", "FRAC_N_LSB",
                             "D_XO_FREQ_SEL", "EN_DIG_CLK_DIV2", "EN_DIG_CLK_DIV4"}
    assert all(b.kind == "RW" and b.address is not None for b in bindings.values())
    # frac_n_lsb 两个切片([15:1]与[0])合并成一个 16bit 叶子
    assert bindings["FRAC_N_LSB"].width == 16
    assert bindings["INT_N"].width == 9
    # 地址来自 tmm
    assert bindings["EN_DIG_CLK_DIV2"].address == 0x1
    assert bindings["INT_N"].address == 0x2
    assert bindings["FRAC_N_LSB"].address == 0x3
    assert bindings["D_XO_FREQ_SEL"].address == 0xC


def test_expand_expected_value_matches_logic(wb, resolver):
    """for_test T0 输入 → 期望值按 logic 表达式 = 0x0081FFFC（pll_n1 左移 2 位）。"""
    sig = next(s for s in wb.logic if s.out_base == "pll_n")
    node, bindings = cone.expand(sig, wb, resolver)
    widths = {k: b.width for k, b in bindings.items()}
    values = {"INT_N": 256, "FRAC_N_MSB": 64, "FRAC_N_LSB": 0xFFFF,
              "D_XO_FREQ_SEL": 1, "EN_DIG_CLK_DIV2": 1, "EN_DIG_CLK_DIV4": 1}
    val, w = E.evaluate(node, E.Env(widths, values), sig.out_width)
    # pll_n1 = {1'b0, 256(9b), 64(7b), 0x7FFF(15b)} = 0x40207FFF；左移两次(高位丢弃) = 0x0081FFFC
    assert w == 32
    assert val == 0x0081FFFC
    # 控制位 = 三个时钟/频率选择，数据 = int_n/frac_n
    control, data = E.classify_vars(node, E.Env(widths))
    assert set(control) == {"D_XO_FREQ_SEL", "EN_DIG_CLK_DIV2", "EN_DIG_CLK_DIV4"}
    assert set(data) == {"INT_N", "FRAC_N_MSB", "FRAC_N_LSB"}


def test_expand_only_when_needed(wb, resolver):
    """没有内部输入的信号不做 cone 展开（reserve 原样）。"""
    sig = next(s for s in wb.logic if s.out_base == "d_logic_bt_lp_reserve")
    node, bindings, expanded = generator.expand_signal(wb, resolver, sig)
    assert not expanded
    assert set(bindings) == {"A", "B", "C", "J"}


def test_expand_cycle_detection(resolver):
    """内部信号循环引用(top→a→b→a) → ConeError 而非死循环。"""
    from dreg_verify.excel_model import DregWorkbook, LogicSignal
    mk = lambda name, inp, r, top: LogicSignal(       # noqa: E731
        row=1, out_name=name + "[3:0]", out_width=4, expr="A",
        suffix="to_logic", top_output=top, notes="", owner="", assert_id=r,
        inputs={"A": {"raw": inp, "base": inp, "width": 4, "msb": 3, "lsb": 0}})
    wb2 = DregWorkbook(logic=[mk("sig_top", "sig_a", "1", 1),
                              mk("sig_a", "sig_b", "2", 0),
                              mk("sig_b", "sig_a", "3", 0)],
                       regmap={}, tmm={}, sheet_names=[])
    with pytest.raises(cone.ConeError):
        cone.expand(wb2.logic[0], wb2, R.Resolver(wb2))


# ───────────── 端到端 .sv ─────────────
def test_pll_n_e2e_sv(wb):
    opts = generator.GenOptions(signals=["pll_n"], mode="min")
    res = generator.build(wb, opts)
    assert res["summary"]["n_generated"] == 1      # 不再被跳过
    assert res["summary"]["n_skipped"] == 0
    text = generator.render(res)
    # 驱动 = 4 个地址的 RF_WRITE（同址合并），无 force
    assert "`RF_WRITE(10'h1," in text
    assert "`RF_WRITE(10'h2," in text
    assert "`RF_WRITE(10'h3," in text
    assert "`RF_WRITE(10'hC," in text
    assert "force" not in text
    # 断言探 pll_n（带位宽），期望 32 位
    assert "assert (`ENV_RF.pll_n[31:0]==32'b" in text
    # 标号 = R 列序号
    assert "assert_3_T0:" in text


def test_pll_n_vectors_cover_t0(wb, resolver):
    """min 模式控制位全组合：3 个控制位 → 至少 8 个用例；期望值全部按表达式计算。"""
    sig = next(s for s in wb.logic if s.out_base == "pll_n")
    node, bindings = cone.expand(sig, wb, resolver)
    vecs, meta = V.generate_vectors(node, bindings, sig.out_width, mode="min")
    assert len(vecs) >= 8
    widths = {k: b.width for k, b in bindings.items()}
    for v in vecs:
        val, _ = E.evaluate(node, E.Env(widths, v.assignments), sig.out_width)
        assert val == v.exp_value


def test_pll_n_report_and_verifiability(wb):
    """报告：pll_n 真值表输入为 6 个叶子寄存器；可验证性=clean(经 cone 展开)。"""
    rep = generator.report(wb, generator.GenOptions(signals=["pll_n"], top_output_only=False))
    assert len(rep["tables"]) == 1
    labels = [i["label"] for i in rep["tables"][0]["inputs"]]
    assert any("int_n" in lb for lb in labels)
    assert any("frac_n_lsb" in lb for lb in labels)
    verif = {v["signal"]: v["status"] for v in rep["verifiability"]["signals"]}
    assert verif["pll_n[31:0]"] == "clean"


def test_internal_signals_still_skipped_by_default(wb):
    """pll_n1/pll_n2 自身是内部信号(top_output=0)：默认仍不出现在产物里。"""
    opts = generator.GenOptions(top_output_only=True)
    res = generator.build(wb, opts)
    names = {st["out_name"] for _l, st in res["blocks"]}
    assert "pll_n[31:0]" in names
    assert "pll_n1[31:0]" not in names and "pll_n2[31:0]" not in names


# ───────────── 探针前缀（输出网在 ENV_RF 子模块里） ─────────────
def test_probe_prefix_in_sv(wb):
    """pll_n 实际在 U_BT_LP_PLL_DIG 内部 → 探针 = `ENV_RF.U_BT_LP_PLL_DIG.pll_n[31:0]。"""
    opts = generator.GenOptions(signals=["pll_n"], mode="min",
                                probe_prefixes={"pll_n": "U_BT_LP_PLL_DIG"})
    text = generator.render(generator.build(wb, opts))
    assert "assert (`ENV_RF.U_BT_LP_PLL_DIG.pll_n[31:0]==32'b" in text
    assert "`ENV_RF.pll_n[31:0]==" not in text
    # 驱动(RF_WRITE)不受前缀影响
    assert "`RF_WRITE(10'h1," in text


def test_probe_prefix_only_for_named_signal(wb):
    """前缀只作用于映射里的信号，其它信号探针不变。"""
    opts = generator.GenOptions(signals=["pll_n", "d_en_refbuf"],
                                probe_prefixes={"pll_n": "U_BT_LP_PLL_DIG"})
    text = generator.render(generator.build(wb, opts))
    assert "`ENV_RF.U_BT_LP_PLL_DIG.pll_n[31:0]==" in text
    assert "`ENV_RF.d_en_refbuf_ls==" in text          # 不带前缀(且带 _ls 后缀)


def test_force_prefix_for_input_wire(wb):
    """mon_active 风格：force 的输入 wire 在 ENV_RF 子模块里 → force 路径带前缀，
    且配置前缀后不再被当 wire 兜底风险跳过。"""
    # 无前缀：mon_active 是 wire 兜底 → 默认 risky → 整个信号被跳过
    res0 = generator.build(wb, generator.GenOptions(signals=["d_pfd_en_lnmode"]))
    assert res0["summary"]["n_generated"] == 0 and res0["summary"]["n_skipped"] == 1

    # 配置前缀：mon_active 在 U_BT_LP_PLL_DIG 下 → 信号可生成，force 带前缀
    opts = generator.GenOptions(signals=["d_pfd_en_lnmode"],
                                probe_prefixes={"mon_active": "U_BT_LP_PLL_DIG"})
    res = generator.build(wb, opts)
    assert res["summary"]["n_generated"] == 1 and res["summary"]["n_skipped"] == 0
    text = generator.render(res)
    assert "force `ENV_RF.U_BT_LP_PLL_DIG.mon_active=" in text
    # 输出探针不带前缀(映射里没有 d_pfd_en_lnmode)，且 ls 信号自动加 _ls 后缀
    assert "assert (`ENV_RF.d_pfd_en_lnmode_ls==" in text
    # 其它输入不受影响：寄存器位 RF_WRITE、iddq force 无前缀
    assert "`RF_WRITE(10'hD," in text
    assert "force `ENV_RF.d_bt_lp_pll_dig_dft_iddq_mode=" in text


def test_probe_prefix_cli_parse():
    from dreg_verify.cli import _parse_probe_prefixes
    assert _parse_probe_prefixes(["pll_n=U_BT_LP_PLL_DIG", "x=A.B"]) == {
        "pll_n": "U_BT_LP_PLL_DIG", "x": "A.B"}
    assert _parse_probe_prefixes([]) == {}
    with pytest.raises(SystemExit):
        _parse_probe_prefixes(["no_equal_sign"])


def test_parse_probe_prefix_lines():
    """映射文本解析：支持注释/空行/多级层级路径；空路径与坏行跳过；首尾点去除。"""
    text = """
# 这是注释
pll_n=U_BT_LP_PLL_DIG

mon_active=U_BT_LP_PLL_DIG.DIG_1
empty_prefix=
no_equal_sign
spaced  =  U_X.Y.
"""
    assert generator.parse_probe_prefix_lines(text) == {
        "pll_n": "U_BT_LP_PLL_DIG",
        "mon_active": "U_BT_LP_PLL_DIG.DIG_1",
        "spaced": "U_X.Y",
    }
    assert generator.parse_probe_prefix_lines("") == {}
    assert generator.parse_probe_prefix_lines(None) == {}


def test_probe_prefix_file_cli(tmp_path):
    """CLI --probe-prefix-file 读 GUI 导出的映射文件；命令行 --probe-prefix 同名覆盖文件。"""
    from dreg_verify.cli import _parse_probe_prefixes
    f = tmp_path / "probe_prefixes.txt"
    f.write_text("# 项目通用映射\npll_n=U_BT_LP_PLL_DIG\nmon_active=U_OLD\n", encoding="utf-8")
    assert _parse_probe_prefixes([], str(f)) == {
        "pll_n": "U_BT_LP_PLL_DIG", "mon_active": "U_OLD"}
    # 命令行优先于文件
    assert _parse_probe_prefixes(["mon_active=U_NEW"], str(f))["mon_active"] == "U_NEW"
    # 文件不存在 → 明确报错
    with pytest.raises(SystemExit):
        _parse_probe_prefixes([], str(tmp_path / "missing.txt"))


def test_probe_prefix_analyze_signal(wb, resolver):
    sig = next(s for s in wb.logic if s.out_base == "pll_n")
    a = generator.analyze_signal(resolver, sig, wb=wb, probe_prefix="U_BT_LP_PLL_DIG")
    assert a["out_net"] == "`ENV_RF.U_BT_LP_PLL_DIG.pll_n[31:0]"
    assert a["status"] == "clean"


# ───────────── GUI 冒烟（离屏） ─────────────
def test_gui_cone_signal_editor(tmp_path_factory):
    """GUI 点 pll_n → 测试项编辑器显示 6 个叶子寄存器列；状态=clean；生成不抛异常。"""
    pytest.importorskip("PySide6")
    from PySide6 import QtWidgets
    QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    from dreg_verify import gui
    path = tmp_path_factory.mktemp("gui_cone") / "synthetic_pll.xlsx"
    fixtures.build_workbook(str(path), with_pll_chain=True)
    w = gui.MainWindow()
    w.path_edit.setText(str(path)); w.on_load()
    sig = next(s for s in w.signals if s.out_base == "pll_n")
    w._load_test_items(sig)
    # 输入分组 = 6 个叶子寄存器（不是 pll_n2 切片）
    bases = {g["base"].lower() for g in w._ti_groups}
    assert bases == {"int_n", "frac_n_msb", "frac_n_lsb",
                     "d_xo_freq_sel", "en_dig_clk_div2", "en_dig_clk_div4"}
    # 状态列：cone 展开后输入全部可驱动 → clean
    row = next(r for r in range(w.table.rowCount()) if w._sig_of_row(r).out_base == "pll_n")
    assert w._analysis[row]["status"] == "clean"
    assert w._analysis[row]["cone"] is True
    # 全选导出（全部范围）能产出 pll_n 的块
    res = generator.build(w.wb, w._opts(["pll_n"]))
    assert res["summary"]["n_generated"] == 1 and res["summary"]["n_skipped"] == 0


def test_gui_probe_prefix_flows_to_sv(tmp_path_factory):
    """GUI 设置探针前缀 → 状态列 out_net 带前缀 → 生成的 .sv 探针带前缀 → 持久化结构正确。"""
    pytest.importorskip("PySide6")
    from PySide6 import QtWidgets
    QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    from dreg_verify import gui
    path = tmp_path_factory.mktemp("gui_pfx") / "synthetic_pll.xlsx"
    fixtures.build_workbook(str(path), with_pll_chain=True)
    w = gui.MainWindow()
    w.path_edit.setText(str(path)); w.on_load()
    sig = next(s for s in w.signals if s.out_base == "pll_n")
    idx = w.signals.index(sig)
    # 模拟『设置探针前缀』(绕过输入对话框直接走数据路径)
    w._probe_prefixes[sig.out_name.lower()] = "U_BT_LP_PLL_DIG"
    w._analysis[idx] = gui.generator.analyze_signal(w._resolver, sig, wb=w.wb,
                                                    probe_prefix=w._prefix_of(sig))
    w._populate_table()
    assert w._analysis[idx]["out_net"].startswith("`ENV_RF.U_BT_LP_PLL_DIG.")
    # 前缀列显示（输出前缀 → 带"输出→"标识）
    row = next(r for r in range(w.table.rowCount()) if w._sig_of_row(r).out_base == "pll_n")
    assert w.table.item(row, gui.COL_PREFIX).text() == "输出→U_BT_LP_PLL_DIG"
    # 生成走 _opts → .sv 探针带前缀
    res = generator.build(w.wb, w._opts(["pll_n"]))
    text = generator.render(res)
    assert "`ENV_RF.U_BT_LP_PLL_DIG.pll_n[31:0]==" in text


def _pll_window(tmp_path_factory, sub):
    pytest.importorskip("PySide6")
    from PySide6 import QtWidgets
    QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    from dreg_verify import gui
    path = tmp_path_factory.mktemp(sub) / "synthetic_pll.xlsx"
    fixtures.build_workbook(str(path), with_pll_chain=True)
    w = gui.MainWindow()
    w.path_edit.setText(str(path)); w.on_load()
    return gui, w


def test_gui_search_by_input_signal(tmp_path_factory):
    """按输入信号搜索：搜 mon_active → 只列出用它做输入的输出信号(d_pfd_en_lnmode)。"""
    gui, w = _pll_window(tmp_path_factory, "gui_search")
    w.name_edit.setText("mon_active")
    visible = [w._sig_of_row(r).out_name for r in range(w.table.rowCount())
               if not w.table.isRowHidden(r)]
    assert visible == ["d_pfd_en_lnmode"]
    # 输入名正则同样支持
    w.name_edit.setText("^mon_act.*e$")
    visible = [w._sig_of_row(r).out_name for r in range(w.table.rowCount())
               if not w.table.isRowHidden(r)]
    assert visible == ["d_pfd_en_lnmode"]
    # 清空搜索 → 全部显示
    w.name_edit.setText("")
    assert all(not w.table.isRowHidden(r) for r in range(w.table.rowCount()))


def test_gui_prefix_column_shows_input_effect(tmp_path_factory):
    """输入信号配置前缀后，『探针前缀』列显示 mon_active→U_BT_LP_PLL_DIG（蓝色）。"""
    gui, w = _pll_window(tmp_path_factory, "gui_pfxcol")
    # 配置输入前缀 + 输出前缀
    w._probe_prefixes = {"mon_active": "U_BT_LP_PLL_DIG", "pll_n": "U_BT_LP_PLL_DIG"}
    w._reanalyze_all()
    # d_pfd_en_lnmode：输入 mon_active 受影响
    row = next(r for r in range(w.table.rowCount())
               if w._sig_of_row(r).out_base == "d_pfd_en_lnmode")
    cell = w.table.item(row, gui.COL_PREFIX)
    assert "mon_active→U_BT_LP_PLL_DIG" in cell.text()
    assert "U_BT_LP_PLL_DIG.mon_active" in cell.toolTip()   # 完整 force 路径
    # pll_n：输出受影响
    row2 = next(r for r in range(w.table.rowCount())
                if w._sig_of_row(r).out_base == "pll_n")
    assert "输出→U_BT_LP_PLL_DIG" in w.table.item(row2, gui.COL_PREFIX).text()
    # 不相关的信号前缀列为空
    row3 = next(r for r in range(w.table.rowCount())
                if w._sig_of_row(r).out_base == "d_logic_bt_lp_reserve")
    assert w.table.item(row3, gui.COL_PREFIX).text() == ""


def test_gui_prefix_mapping_covers_input_wire(tmp_path_factory):
    """GUI 映射编辑器数据路径：配置 mon_active 前缀 → _reanalyze_all → 信号变 clean，force 带前缀。"""
    pytest.importorskip("PySide6")
    from PySide6 import QtWidgets
    QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    from dreg_verify import gui
    path = tmp_path_factory.mktemp("gui_wire_pfx") / "synthetic_pll.xlsx"
    fixtures.build_workbook(str(path), with_pll_chain=True)
    w = gui.MainWindow()
    w.path_edit.setText(str(path)); w.on_load()
    sig = next(s for s in w.signals if s.out_base == "d_pfd_en_lnmode")
    idx = w.signals.index(sig)
    # 无前缀: mon_active wire 兜底 → 非 clean
    assert w._analysis[idx]["status"] == "wire-fallback"
    # 配置前缀并全表重析（与映射编辑器保存后路径一致）
    w._probe_prefixes = {"mon_active": "U_BT_LP_PLL_DIG"}
    w._reanalyze_all()
    assert w._analysis[idx]["status"] == "clean"
    nets = {i["base"]: i["net"] for i in w._analysis[idx]["inputs"]}
    assert "U_BT_LP_PLL_DIG.mon_active" in nets["mon_active"]
    # 导出路径同样生效
    text = generator.render(generator.build(w.wb, w._opts([sig.out_name])))
    assert "force `ENV_RF.U_BT_LP_PLL_DIG.mon_active=" in text
