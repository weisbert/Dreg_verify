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
from dreg_verify import excel_model, generator         # noqa: E402


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


def test_append_to_logic_toggle_gui(gui_app, tmp_path):
    """GUI『输出加_to_logic尾缀』开关（2026-06-11 Hi1108）：默认开→pll_n1(top_out=0,被引用)探针带
    _to_logic；关→直接探基名；resolver/_opts 同步、重析无碍、可来回切。"""
    from dreg_verify import gui as G
    excel = tmp_path / "atl.xlsx"
    fixtures.build_workbook(str(excel), with_pll_chain=True, with_mux=True)
    w = G.MainWindow(); w.path_edit.setText(str(excel)); w.on_load()
    try:
        assert w.append_to_logic_chk.isChecked()           # 默认勾(2026-06-11 翻回=RTL 真名)
        assert w._opts(["pll_n1"]).append_to_logic is True
        assert w._resolver.append_to_logic is True
        sig = next(s for s in w.signals if getattr(s, "out_base", "") == "pll_n1")
        assert sig.rtl_name == "pll_n1_to_logic[31:0]"     # 开：探针网名补 _to_logic
        # 取消勾选 → on_append_to_logic_changed 触发重析(重建 Resolver，回填 _append_to_logic=False)
        w.append_to_logic_chk.setChecked(False)
        assert w._resolver.append_to_logic is False
        assert w._opts(["pll_n1"]).append_to_logic is False
        assert sig.rtl_name == "pll_n1[31:0]"              # 关：直接探基名网
        # 勾回 → 复原
        w.append_to_logic_chk.setChecked(True)
        assert w._resolver.append_to_logic is True
        assert sig.rtl_name == "pll_n1_to_logic[31:0]"
    finally:
        w.close()


def test_append_to_logic_config_roundtrip(gui_app, tmp_path, monkeypatch):
    """配置导入导出含 append_to_logic：导出 OFF→导入还原 OFF（checkbox/resolver/_opts/.sv 全 OFF）；
    套用缺该键的全局设置→确定性复位 True（不残留本会话切换）。"""
    import json
    from PySide6 import QtWidgets
    from dreg_verify import gui as G
    excel = tmp_path / "atl_cfg.xlsx"
    fixtures.build_workbook(str(excel), with_pll_chain=True, with_mux=True)
    w = G.MainWindow(); w.path_edit.setText(str(excel)); w.on_load()
    try:
        sig = next(s for s in w.signals if getattr(s, "out_base", "") == "pll_n1")
        # 关开关 → 导出配置（global 含 append_to_logic=False）
        w.append_to_logic_chk.setChecked(False)
        cfg = w._collect_config()
        assert cfg["global"]["append_to_logic"] is False
        cfg_path = tmp_path / "cfg.json"
        cfg_path.write_text(json.dumps(cfg), encoding="utf-8")
        # 模拟新会话：开关复位 True
        w.append_to_logic_chk.setChecked(True)
        assert sig.rtl_name == "pll_n1_to_logic[31:0]"
        # 导入该配置 → 还原 OFF（含 resolver 重建）。monkeypatch 文件对话框 + 完成提示框
        monkeypatch.setattr(QtWidgets.QFileDialog, "getOpenFileName",
                            staticmethod(lambda *a, **k: (str(cfg_path), "")))
        monkeypatch.setattr(QtWidgets.QMessageBox, "information",
                            staticmethod(lambda *a, **k: None))
        w.on_import_edits()
        assert w.append_to_logic_chk.isChecked() is False
        assert w._resolver.append_to_logic is False
        assert w._opts(["pll_n1"]).append_to_logic is False
        assert sig.rtl_name == "pll_n1[31:0]"
        # 确定性：套用缺 append_to_logic 键的全局设置（如旧配置）→ 复位生产默认 True（2026-06-11 翻回，
        # =RTL 真名；不残留本会话切换）
        w._apply_global_settings({"coverage_logic": "精简"})
        assert w.append_to_logic_chk.isChecked() is True
    finally:
        w.close()


def test_export_nets_gui(gui_app, tmp_path, monkeypatch):
    """GUI『导出 nets.txt』(2026-06-12)：把当前表需要在 ENV_RF 层级定位的网清单写出，
    供仿真服务器跑 scan_rtl（原 CLI scan_rtl.py --export-nets 的 GUI 化）。验证：
      ① 文件可被 scan_rtl.parse_nets_text 解析、非空、含 mux 输出探针网（logic+mux+dft 并集）
      ② 导出过程不污染左表尾缀状态——collect_excel_nets 会临时把 _append_to_logic 强设 True，
         导出后必须还原成当前开关（关尾缀时探基名应保持基名）。"""
    from PySide6 import QtWidgets
    from dreg_verify import gui as G
    from scan_rtl import parse_nets_text
    excel = tmp_path / "nets_gui.xlsx"
    fixtures.build_workbook(str(excel), with_pll_chain=True, with_mux=True)
    w = G.MainWindow(); w.path_edit.setText(str(excel)); w.on_load()
    try:
        out = tmp_path / "nets.txt"
        monkeypatch.setattr(QtWidgets.QFileDialog, "getSaveFileName",
                            staticmethod(lambda *a, **k: (str(out), "")))
        monkeypatch.setattr(QtWidgets.QMessageBox, "information",
                            staticmethod(lambda *a, **k: None))
        # 关『logic 加尾缀』→ pll_n1 探基名；导出后应仍是基名（污染已被 _reanalyze_all 还原）
        w.append_to_logic_chk.setChecked(False)
        sig = next(s for s in w.signals if getattr(s, "out_base", "") == "pll_n1")
        assert sig.rtl_name == "pll_n1[31:0]"
        w.on_export_nets()
        assert out.exists()
        nets = parse_nets_text(out.read_text(encoding="utf-8"))
        assert nets, "nets.txt 应非空"
        mux_bases = {g.out_base for g in (w.wb.mux or [])}
        assert mux_bases & set(nets), "应导出 mux 输出探针网（mux 三类网并入）"
        assert sig.rtl_name == "pll_n1[31:0]"            # 尾缀状态未被导出污染
    finally:
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
    # 通用形态穷举文案（mux 侧档位，第二十二轮起与 logic 解耦）
    w.coverage_mux.setCurrentText("穷举")
    w._load_test_items(grp)
    assert "无另一条控制路径概念" in w.ti_header.text()
    w.coverage_mux.setCurrentText("精简")


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


def test_per_signal_suffix_override_gui(wl_win):
    """单点「本信号探尾缀网」(2026-06-11 Hi1108 rxiq)：mux 输出默认【不勾】(探裸名)；勾/取消即时改
    rtl_name + 重析；override 映射只存偏离项(回默认即删)。mux 默认裸名是关键回归(端口尾缀设计相关)。"""
    w = wl_win
    grp = _mux_grp(w, "d_wl_rf_lna_gain")     # top_out=0 mux 输出、被 logic 以 _to_logic 引用
    w._load_test_items(grp)
    assert w.suffix_chk.isEnabled()           # 被下游引用 → 有尾缀可补，本框可用
    assert w.suffix_chk.isChecked() is False  # mux 默认探裸名（不随 logic 全局开关）
    assert grp.rtl_name == "d_wl_rf_lna_gain[2:0]"
    # 勾上 → 探尾缀网 + 重析（rtl_base 随之变）。override 按 out_name 记忆(与 _sig_cov 同口径)
    key = grp.out_name.lower()
    w.suffix_chk.setChecked(True)
    assert grp.rtl_name == "d_wl_rf_lna_gain_to_logic[2:0]"
    assert w._suffix_override.get(key) is True
    assert w._opts(["d_wl_rf_lna_gain"]).suffix_override.get(key) is True
    # 取消 → 回默认（映射不留偏离项）
    w.suffix_chk.setChecked(False)
    assert grp.rtl_name == "d_wl_rf_lna_gain[2:0]"
    assert key not in w._suffix_override


def test_append_to_mux_global_drives_default(wl_win):
    """全局「mux加尾缀」开关(2026-06-11 用户拍板 logic/mux 分开)：默认不勾→mux 输出探裸名；勾上→所有
    被引用的 mux 输出探尾缀网(单点默认跟随)；与 logic 开关独立。"""
    w = wl_win
    grp = _mux_grp(w, "d_wl_rf_lna_gain")
    w._load_test_items(grp)
    assert w.append_to_mux_chk.isChecked() is False     # mux 全局默认不勾
    assert w.suffix_chk.isChecked() is False            # 单点跟随=不勾(探裸名)
    assert grp.rtl_name == "d_wl_rf_lna_gain[2:0]"
    w.append_to_mux_chk.setChecked(True)                # 开全局 mux 尾缀 → 重析
    assert grp.rtl_name == "d_wl_rf_lna_gain_to_logic[2:0]"
    w._load_test_items(grp)                             # 重载刷新单点勾选
    assert w.suffix_chk.isChecked() is True             # 单点跟随全局=勾
    # logic 开关独立：关 logic 尾缀不影响 mux
    w.append_to_logic_chk.setChecked(False)
    assert grp.rtl_name == "d_wl_rf_lna_gain_to_logic[2:0]"   # mux 仍带尾缀


def test_suffix_setting_does_not_freeze_coverage(wl_win):
    """回应用户顾虑：设「探尾缀网」【不】把信号变成自定义属性、不冻结覆盖度——设完后信号既不进
    _customized 也不进 _sig_cov，全局覆盖度切换照常对它生效。"""
    w = wl_win
    grp = _mux_grp(w, "d_wl_rf_lna_gain")
    w._load_test_items(grp)
    w.suffix_chk.setChecked(True)                       # 给它设尾缀
    key = grp.out_name.lower()
    assert key in w._suffix_override                    # 尾缀记下了(独立属性)
    assert key not in w._customized                     # 但【没】变成自定义测试项
    assert key not in w._sig_cov                        # 也【没】钉死单点覆盖档
    # 全局 mux 覆盖度仍对它生效（实际生效档跟着全局走，没被尾缀冻结）
    w.coverage_mux.setCurrentText("精简")
    assert w._sig_cov_collapsed(grp) == "min"
    w.coverage_mux.setCurrentText("穷举")
    assert w._sig_cov_collapsed(grp) == "exhaustive"


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


# ───────────── ⑧ 第二十轮 B1：mux 编辑器按钮态 + 级联切换重渲 ─────────────
def test_b1_mux_buttons_all_enabled_peer_with_logic(lpbt_win):
    """[B1a] 第二十八轮 mux 与 logic 平级：选 mux 信号 → 列编辑按钮【全部可用】(不再置灰)，
    "加列/复制/负向"等的 tooltip 换成 mux 语义说明；切回 logic 恢复原 tooltip。"""
    w = lpbt_win
    mux = next(s for s in w.signals if isinstance(s, excel_model.MuxGroup))
    w._load_test_items(mux)
    assert w._ti_mux_sig is mux and w._ti_sig is None
    for t in ("加正向列", "复制列", "重命名列…", "加负向(选中)",
              "全部用例加负向", "删负向", "导出CSV", "auto→期望", "预览本信号.sv",
              "删除列", "清空本信号", "重新生成"):
        assert w._ti_btns[t].isEnabled(), t                  # 全部可用
    assert "mux" in w._ti_btns["加正向列"].toolTip()          # mux 语义 tooltip
    assert "case" in w._ti_btns["加正向列"].toolTip()
    # 切回 logic 信号 → 列编辑按钮恢复可用 + 原 tooltip
    logic = next(s for s in w.signals if not isinstance(s, excel_model.MuxGroup))
    w._load_test_items(logic)
    assert w._ti_sig is logic
    assert w._ti_btns["加正向列"].isEnabled()
    assert "mux：" not in w._ti_btns["加正向列"].toolTip()     # 恢复 logic 原文


def test_b1_cascade_toggle_rerenders_mux(wl_win):
    """[B1b] 选中 mux 信号时切级联模式 → mux 真值表必须按新模式重渲（修前 on_cascade_mode_changed
    只重渲 logic、mux 看着"切了没反应"）。切到 force 后控制行会显示被 force 的衔接网+needs-prefix。"""
    w = wl_win
    mux = next(s for s in w.signals if isinstance(s, excel_model.MuxGroup))
    w._load_test_items(mux)
    assert w._ti_mux_sig is mux
    calls = []
    orig = w._load_mux_test_items
    w._load_mux_test_items = lambda g, _o=orig: (calls.append(g), _o(g))[1]
    try:
        w.on_cascade_mode_changed()                # 切级联模式（不依赖持久化的下拉初值）
    finally:
        w._load_mux_test_items = orig
    assert calls and calls[-1] is mux              # mux 真值表确实被重渲了


def test_cascade_decouple_gui(wl_win):
    """级联 logic/mux 解耦 + 单点(2026-06-11 用户拍板)：两个全局下拉 + 本信号级联，各自独立生效。"""
    w = wl_win
    assert w._logic_cascade() == "cone" and w._mux_cascade() == "cone"   # 默认两侧 cone
    # mux 全局切 force，logic 保持 cone
    w.cascade_mux_combo.setCurrentIndex(1)
    assert w._mux_cascade() == "force" and w._logic_cascade() == "cone"
    opts = w._opts(["x"])
    assert opts.mux_cascade == "force" and opts.logic_cascade == "cone"
    mux = next(s for s in w.signals if isinstance(s, excel_model.MuxGroup))
    assert w._cascade_for(mux) == "force"            # mux 信号跟随 mux 全局
    # 本信号级联单点：给该 mux 设 cone，压过全局 force
    w._load_test_items(mux)
    assert w.sig_cascade_combo.isEnabled()
    w.sig_cascade_combo.setCurrentText("展开上游")
    assert w._cascade_for(mux) == "cone"
    assert w._opts(["x"]).sig_cascade.get(mux.out_name.lower()) == "cone"
    # 回「跟随全局」→ 删单点，回到全局 force
    w.sig_cascade_combo.setCurrentText("跟随全局")
    assert w._cascade_for(mux) == "force"
    assert mux.out_name.lower() not in w._sig_cascade


# ───────────── ⑨ 第二十轮 B2：mux 数据值可手填 + 落盘 + 重开恢复 ─────────────
def test_b2_mux_data_cell_editable_persist_reload(gui_app, tmp_path, monkeypatch):
    """[B2] mux 数据行可双击手填(控制行只读) → 存进 _mux_data + 落盘 + 整表重渲生效；重开恢复。"""
    from PySide6 import QtCore
    from dreg_verify import gui as G
    monkeypatch.setattr(G, "EDITS_PATH", str(tmp_path / "edits.json"))
    excel = tmp_path / "wl.xlsx"
    fixtures.build_wl_workbook(str(excel))
    w = G.MainWindow(); w.path_edit.setText(str(excel)); w.on_load()
    # 找一个能生成向量(有可手填数据行)的 mux 组
    grp = None
    for s in w.signals:
        if isinstance(s, excel_model.MuxGroup):
            w._load_test_items(s)
            if w._ti_mux_data_rows:
                grp = s; break
    assert grp is not None, "没有任何 mux 组有可手填数据行"
    name_low = grp.out_name.lower()
    drow = min(w._ti_mux_data_rows)
    base_low, width, _key = w._ti_mux_data_rows[drow]   # 第二十八轮起带绑定键(供用户列按列改)
    # 数据格可编辑；控制行(行0)若是控制则只读
    dcell = w.ti_table.item(drow, 0)
    assert dcell.flags() & QtCore.Qt.ItemIsEditable
    ctrl_rows = [r for r in range(w.ti_table.rowCount())
                 if r not in w._ti_mux_data_rows and r < w._ti_mux_exp_row - 1]
    if ctrl_rows:
        assert not (w.ti_table.item(ctrl_rows[0], 0).flags() & QtCore.Qt.ItemIsEditable)
    # 手填数据值（取与当前显示不同的值——setText 同值不触发 itemChanged）
    nv = 2 if w.ti_table.item(drow, 0).text() != w._cell_text(2, width) else 1
    w.ti_table.item(drow, 0).setText(w._cell_text(nv, width))
    assert w._mux_data.get(name_low, {}).get(base_low) == nv
    # 落盘（edits.json 里有 mux_data 段）
    import json
    saved = json.load(open(str(tmp_path / "edits.json"), encoding="utf-8"))
    assert any((b.get("mux_data") or {}).get(name_low, {}).get(base_low) == nv
               for b in saved.values()), saved
    w.close()
    # 重开 → _mux_data 恢复
    w2 = G.MainWindow(); w2.path_edit.setText(str(excel)); w2.on_load()
    assert w2._mux_data.get(name_low, {}).get(base_low) == nv
    w2.close()


# ───────────── 第二十六轮：mux 删除测试列 / 一键清空 / 重新生成(撤销) ─────────────
def _load_buildable_mux(w):
    """加载第一个 min 档 >=2 条向量的 mux 信号到编辑器，返回该组。"""
    for s in w.signals:
        if isinstance(s, excel_model.MuxGroup):
            w._load_test_items(s)
            if len(w._ti_mux_vecs) >= 2:
                return s
    raise AssertionError("无可建 mux 组")


def test_mux_delete_column(lpbt_win):
    """删除选中的 mux 测试列 → 记签名进 _mux_dropped；编辑器少一列；_opts/build 同口径过滤。"""
    w = lpbt_win
    mux = _load_buildable_mux(w)
    n0 = len(w._ti_mux_vecs)
    sig0 = generator.mux_assign_key(w._ti_mux_vecs[0].assignments)
    w.ti_table.setCurrentCell(0, 0)
    w.on_ti_del()
    assert sig0 in w._mux_dropped[mux.out_name.lower()]
    assert len(w._ti_mux_vecs) == n0 - 1
    opts = w._opts([mux.out_name])
    assert sig0 in opts.mux_dropped[mux.out_name.lower()]
    res = generator.build(w.wb, opts)
    n_build = sum(st["n_vectors"] for _l, st in res["blocks"] if st.get("out_name") == mux.out_name)
    assert n_build == n0 - 1


def test_mux_clear_and_regen(lpbt_win, monkeypatch):
    """一键清空 mux → _mux_cleared + 编辑器零向量 + build 跳过；重新生成 → 撤销恢复。"""
    from PySide6 import QtWidgets
    monkeypatch.setattr(QtWidgets.QMessageBox, "question",
                        staticmethod(lambda *a, **k: QtWidgets.QMessageBox.Yes))
    w = lpbt_win
    mux = _load_buildable_mux(w)
    w.on_ti_clear()
    assert mux.out_name.lower() in w._mux_cleared
    assert w._ti_mux_vecs == []
    res = generator.build(w.wb, w._opts([mux.out_name]))
    assert mux.out_name not in {st.get("out_name") for _l, st in res["blocks"]}
    # 重新生成 = 撤销清空，回到默认
    w.on_ti_regen()
    assert mux.out_name.lower() not in w._mux_cleared
    assert len(w._ti_mux_vecs) >= 2


def test_mux_regen_discards_all_customizations(lpbt_win, monkeypatch):
    """mux「重新生成」丢弃全部自定义：删列/清空/手填期望/数据值/负向 全清，回到出厂。"""
    from PySide6 import QtWidgets
    monkeypatch.setattr(QtWidgets.QMessageBox, "question",
                        staticmethod(lambda *a, **k: QtWidgets.QMessageBox.Yes))
    w = lpbt_win
    mux = _load_buildable_mux(w)
    nl = mux.out_name.lower()
    w.ti_table.setCurrentCell(0, 0); w.on_ti_del()       # 删一列
    w._mux_neg.add(nl)                                    # 假装勾了负向
    w._mux_expected[nl] = {"x": 1}                        # 假装手填了期望
    w.on_ti_regen()
    assert nl not in w._mux_dropped and nl not in w._mux_cleared
    assert nl not in w._mux_neg and nl not in w._mux_expected


def test_mux_drop_clear_persist_reopen(gui_app, tmp_path, monkeypatch):
    """删列 + 清空 跨 GUI 重开持久化（随 EDITS 桶存盘/恢复）。"""
    from PySide6 import QtWidgets
    from dreg_verify import gui as G
    monkeypatch.setattr(G, "EDITS_PATH", str(tmp_path / "edits.json"))
    monkeypatch.setattr(QtWidgets.QMessageBox, "question",
                        staticmethod(lambda *a, **k: QtWidgets.QMessageBox.Yes))
    excel = tmp_path / "lpbt.xlsx"
    fixtures.build_workbook(str(excel), with_mux=True)
    w1 = G.MainWindow(); w1.path_edit.setText(str(excel)); w1.on_load()
    muxes = [s for s in w1.signals if isinstance(s, excel_model.MuxGroup)]
    m_drop, sig0 = None, None
    for s in muxes:
        w1._load_test_items(s)
        if len(w1._ti_mux_vecs) >= 2:
            m_drop = s
            sig0 = generator.mux_assign_key(w1._ti_mux_vecs[0].assignments)
            w1.ti_table.setCurrentCell(0, 0); w1.on_ti_del()
            break
    assert m_drop is not None
    m_clear = next(s for s in muxes if s is not m_drop)
    w1._load_test_items(m_clear); w1.on_ti_clear()
    w1.close()
    w2 = G.MainWindow(); w2.path_edit.setText(str(excel)); w2.on_load()
    assert sig0 in w2._mux_dropped.get(m_drop.out_name.lower(), set())
    assert m_clear.out_name.lower() in w2._mux_cleared
    w2.close()


# ───────────── 第二十八轮：mux 用户手编列（mux 与 logic 平级）─────────────
def test_mux_add_positive_user_col(lpbt_win):
    """加正向列(mux)：基于选中列的 case 新增一条用户正向列 → _mux_user_vecs +1、编辑器多一列、
    带 case_index、auto_out=路由源值；build 也多出该列(带自定义名)。"""
    w = lpbt_win
    grp = _load_buildable_mux(w)
    nl = grp.out_name.lower()
    n0 = len(w._ti_mux_vecs)
    w.ti_table.setCurrentCell(0, 0)
    w.on_ti_add()
    assert len(w._mux_user_vecs.get(nl, [])) == 1
    uv = w._mux_user_vecs[nl][0]
    assert uv.case_index is not None and not uv.is_negative and uv.name
    assert len(w._ti_mux_vecs) == n0 + 1                  # 多一列(用户列在最后)
    assert w._ti_mux_user_start == n0
    # build 注入该用户列
    res = generator.build(w.wb, w._opts([grp.out_name]))
    blk = next((l, s) for l, s in res["blocks"] if s.get("is_mux") and s.get("out_name") == grp.out_name)
    assert uv.name in "\n".join(blk[0])


def test_mux_user_col_data_editable_per_column(lpbt_win):
    """用户列数据格可双击改【本列】值 → 只动这列 + auto_out 随路由源重算(不 by_base 整行同步)。"""
    w = lpbt_win
    grp = _load_buildable_mux(w)
    nl = grp.out_name.lower()
    if not w._ti_mux_data_rows:
        pytest.skip("该 mux 组没有可手填数据行")
    w.ti_table.setCurrentCell(0, 0); w.on_ti_add()
    ucol = w._ti_mux_user_start                          # 新用户列列号
    drow = min(w._ti_mux_data_rows)
    _base, width, key = w._ti_mux_data_rows[drow]
    cell = w.ti_table.item(drow, ucol)
    from PySide6 import QtCore
    assert cell.flags() & QtCore.Qt.ItemIsEditable        # 用户列数据格可编辑
    newv = (w._ti_mux_vecs[ucol].assignments.get(key, 0) ^ 0x1) & ((1 << width) - 1)
    w.ti_table.item(drow, ucol).setText(w._cell_text(newv, width))
    uv = w._mux_user_vecs[nl][0]
    assert uv.assignments.get(key) == newv                # 本列被改
    # auto_out（exp_value）= 路由 case 数据键的值
    exp = w._ti_mux_exp
    dk = exp["data_keys"][uv.case_index]
    assert uv.exp_value == (uv.assignments.get(dk, 0) & ((1 << (grp.out_width or 1)) - 1))


def test_mux_copy_col_makes_user_col(lpbt_win):
    """复制列(mux)：整列克隆为用户列(唯一名)。"""
    w = lpbt_win
    grp = _load_buildable_mux(w)
    nl = grp.out_name.lower()
    n0 = len(w._ti_mux_vecs)
    w.ti_table.setCurrentCell(0, 0); w.on_ti_copy()
    assert len(w._mux_user_vecs.get(nl, [])) == 1
    assert len(w._ti_mux_vecs) == n0 + 1
    assert w._mux_user_vecs[nl][0].name                    # 有唯一名(不复制原名)


def test_mux_rename_user_col(lpbt_win, monkeypatch):
    """重命名列(mux)：用户列可改名；自动生成列拒绝。"""
    from PySide6 import QtWidgets
    w = lpbt_win
    grp = _load_buildable_mux(w)
    nl = grp.out_name.lower()
    w.ti_table.setCurrentCell(0, 0); w.on_ti_add()
    ucol = w._ti_mux_user_start
    monkeypatch.setattr(QtWidgets.QInputDialog, "getText",
                        staticmethod(lambda *a, **k: ("MY_CASE", True)))
    w.on_ti_rename_col(ucol)
    assert w._mux_user_vecs[nl][0].name == "MY_CASE"
    # 自动生成列改名被拒（不抛、名字不变）
    seen = {"info": False}
    monkeypatch.setattr(QtWidgets.QMessageBox, "information",
                        staticmethod(lambda *a, **k: seen.__setitem__("info", True)))
    w.on_ti_rename_col(0)
    assert seen["info"]


def test_mux_add_neg_selected_and_del(lpbt_win):
    """加负向(选中)(mux)：选中列加负向用户列(显示 _NEG)；删负向清掉它。"""
    w = lpbt_win
    grp = _load_buildable_mux(w)
    nl = grp.out_name.lower()
    n0 = len(w._ti_mux_vecs)
    w.ti_table.setCurrentCell(0, 0)
    w.on_ti_add_neg_selected()
    negs = [v for v in w._mux_user_vecs.get(nl, []) if v.is_negative]
    assert len(negs) == 1 and len(w._ti_mux_vecs) == n0 + 1
    from dreg_verify import sv_writer as W
    assert W.test_label(w._ti_mux_vecs[-1]).endswith("_NEG")
    # build 里多一条负向
    res = generator.build(w.wb, w._opts([grp.out_name]))
    blk = next((l, s) for l, s in res["blocks"] if s.get("is_mux") and s.get("out_name") == grp.out_name)
    assert blk[1]["n_negative"] >= 1
    # 删负向
    w.on_ti_del_neg()
    assert not [v for v in w._mux_user_vecs.get(nl, []) if v.is_negative]


def test_mux_add_neg_selected_dedup(lpbt_win):
    """加负向(选中)对同一条列重复点 → 不重复添加相同负向。"""
    w = lpbt_win
    grp = _load_buildable_mux(w)
    nl = grp.out_name.lower()
    w.ti_table.setCurrentCell(0, 0); w.on_ti_add_neg_selected()
    w.ti_table.setCurrentCell(0, 0); w.on_ti_add_neg_selected()   # 再点同一列
    assert len([v for v in w._mux_user_vecs.get(nl, []) if v.is_negative]) == 1


def test_mux_user_vec_persist_reopen(gui_app, tmp_path, monkeypatch):
    """用户手编列跨 GUI 重开持久化（随 EDITS 桶存盘/恢复，含 case_index/name）。"""
    from dreg_verify import gui as G
    monkeypatch.setattr(G, "EDITS_PATH", str(tmp_path / "edits.json"))
    excel = tmp_path / "lpbt.xlsx"
    fixtures.build_workbook(str(excel), with_mux=True)
    w1 = G.MainWindow(); w1.path_edit.setText(str(excel)); w1.on_load()
    grp = _load_buildable_mux(w1)
    nl = grp.out_name.lower()
    w1.ti_table.setCurrentCell(0, 0); w1.on_ti_add()
    nm = w1._mux_user_vecs[nl][0].name
    ci = w1._mux_user_vecs[nl][0].case_index
    w1.close()
    w2 = G.MainWindow(); w2.path_edit.setText(str(excel)); w2.on_load()
    restored = w2._mux_user_vecs.get(nl, [])
    assert len(restored) == 1 and restored[0].name == nm and restored[0].case_index == ci
    w2.close()


def test_mux_regen_drops_user_vecs(lpbt_win):
    """重新生成(撤销)清掉用户手编列。"""
    w = lpbt_win
    grp = _load_buildable_mux(w)
    nl = grp.out_name.lower()
    w.ti_table.setCurrentCell(0, 0); w.on_ti_add()
    assert w._mux_user_vecs.get(nl)
    w.on_ti_regen()
    assert nl not in w._mux_user_vecs


def test_mux_user_vec_no_dup_on_reload(gui_app, tmp_path, monkeypatch):
    """对抗评审 C1（blocker）：换表/重载同一表时 _mux_user_vecs 必须先清空——否则恢复用 .extend 会叠加。"""
    from dreg_verify import gui as G
    monkeypatch.setattr(G, "EDITS_PATH", str(tmp_path / "edits.json"))
    excel = tmp_path / "lpbt.xlsx"
    fixtures.build_workbook(str(excel), with_mux=True)
    w = G.MainWindow(); w.path_edit.setText(str(excel)); w.on_load()
    grp = _load_buildable_mux(w)
    nl = grp.out_name.lower()
    w.ti_table.setCurrentCell(0, 0); w.on_ti_add()
    assert len(w._mux_user_vecs[nl]) == 1
    w.on_load()                                  # 重新加载同一表(同窗口)
    assert len(w._mux_user_vecs.get(nl, [])) == 1, "重载不应叠加用户手编列"
    w.close()


def test_mux_user_neg_wrong_value_forced_mismatch(lpbt_win):
    """对抗评审 B4：用户给 mux 负向列填的错值若 == 正确值(auto_out)，自动改成不同值(否则断言会 PASS)。"""
    w = lpbt_win
    grp = _load_buildable_mux(w)
    w.ti_table.setCurrentCell(0, 0); w.on_ti_add_neg_selected()
    ncol = w._ti_mux_user_start                  # 负向用户列
    nvec = w._ti_mux_vecs[ncol]
    assert nvec.is_negative
    # 把错值设成 == 正确值(exp_value) → 应被改成 != 正确值
    w.ti_table.item(w._ti_mux_exp_row, ncol).setText(w._cell_text(nvec.exp_value, grp.out_width or 1))
    assert nvec.neg_value != nvec.exp_value


def test_mux_left_neg_indicator_reflects_user_neg(lpbt_win):
    """对抗评审 B1：左表「负向」勾选反映用户逐 case 负向(不再误示无负向)；编辑器加负向后同步勾上。"""
    from PySide6 import QtCore
    from dreg_verify import gui as G
    w = lpbt_win
    grp = _load_buildable_mux(w)
    r = _mux_idx(w, grp.out_base)
    w.ti_table.setCurrentCell(0, 0); w.on_ti_add_neg_selected()
    assert w.table.item(r, G.COL_NEG).checkState() == QtCore.Qt.Checked   # 左表已勾(有负向)


# ───────────── 2026-06-10 Hi1108：iddq 门=真值表的【横向输入行】（用户三轮澄清定稿） ─────────────
def _dft_wb(tmp_path, fname, iddq_typ=("Y", "RO"), fortest_groups=None):
    import test_mux_wl as TM
    return TM._build_mux_wb(
        str(tmp_path / fname),
        tmm_fields=[
            ("SEL", "h10", "d_sel[1:0]", "1:0", "N", "RW"),
            ("IDDQ", "h11", "d_iddq_mode", "0", iddq_typ[0], iddq_typ[1]),
            ("S0", "h20", "d_s0[1:0]", "1:0", "N", "RW"),
            ("S1", "h21", "d_s1[1:0]", "1:0", "N", "RW"),
        ],
        mux_rows=[
            TM._mrow("d_s0_to_mux[1:0]", "d_sel_to_mux[1:0]", "2'b00", "d_g[1:0]", 1),
            TM._mrow("d_s1_to_mux[1:0]", "d_sel_to_mux[1:0]", "2'b01", "d_g[1:0]", 1),
        ],
        logic_rows=[{"A": "d_s0_to_logic[1:0]", "K": "d_lg[1:0]",
                     "L": "A", "M": "ls", "N": 1, "R": 1}],
        dft_rows=[("d_g_to_dft[1:0]", "d_iddq_mode_to_dft", "d_g[1:0]", "B?0:A"),
                  ("d_lg_to_dft[1:0]", "d_iddq_mode_to_dft", "d_lg[1:0]", "B?0:A")],
        fortest_groups=fortest_groups,
    )


def test_mux_dft_gate_is_input_row(gui_app, tmp_path):
    """mux：iddq 门=真值表输入区末行（每条测试取透传值 0），期望行随之下移一行仍可手填；
    『输入信号』表照旧有 DFT 门行；头部说明改为「已列为输入行」。"""
    from dreg_verify import gui as G
    excel = tmp_path / "dftrow_gui.xlsx"
    _dft_wb(tmp_path, "dftrow_gui.xlsx")
    w = G.MainWindow()
    w.path_edit.setText(str(excel))
    w.on_load()
    try:
        grp = _mux_grp(w, "d_g")
        w._load_test_items(grp)
        used_n = len(w._ti_mux_exp["used_vars"])
        # 行数 = 输入 + DFT门行 + auto_out + 期望；行序=寄存器地址（for_test 生成规则）：
        # sel(h10)→iddq(h11)→s0(h20)→s1(h21) → DFT门行在第 2 行（不再固定殿后）
        assert w.ti_table.rowCount() == used_n + 3
        grow = next(r for r in range(w.ti_table.rowCount())
                    if "DFT门" in (w.ti_table.verticalHeaderItem(r).text() or ""))
        assert grow == 1, [w.ti_table.verticalHeaderItem(r).text()
                           for r in range(w.ti_table.rowCount())]
        assert "d_iddq_mode" in w.ti_table.verticalHeaderItem(grow).text()
        for c in range(w.ti_table.columnCount()):     # 每条测试都取透传值 0（只读）
            from PySide6 import QtCore
            it = w.ti_table.item(grow, c)
            assert it.text() == "0", (c, it.text())
            assert not (it.flags() & QtCore.Qt.ItemIsEditable)
        # 期望行下移一行后仍工作：_ti_mux_exp_row 指向最后一行，手填期望照常
        assert w._ti_mux_exp_row == used_n + 2
        w.ti_table.item(w._ti_mux_exp_row, 0).setText("2'b11")
        assert w._ti_mux_vecs[0].designer_expected == 3
        # 『输入信号』表照旧有 DFT 门行；头部说明=已列为输入行
        labels = [w.ti_inputs.item(r, 1).text() for r in range(w.ti_inputs.rowCount())]
        assert "d_iddq_mode" in labels, labels
        assert "已列为输入行" in w.ti_header.text(), w.ti_header.text()
    finally:
        w.close()


def test_mux_input_rows_follow_fortest_order(gui_app, tmp_path):
    """2026-06-10 用户要求：真值表输入行次序与 designer for_test 同组行序一致——
    门排中间也照排；编辑(手填期望)在新行号下照常工作。"""
    from dreg_verify import gui as G
    excel = tmp_path / "ftorder_gui.xlsx"
    _dft_wb(tmp_path, "ftorder_gui.xlsx",
            fortest_groups=[("d_g[1:0]", ["d_iddq_mode", "d_sel[1:0]",
                                          "d_s1[1:0]", "d_s0[1:0]"])])
    w = G.MainWindow()
    w.path_edit.setText(str(excel))
    w.on_load()
    try:
        grp = _mux_grp(w, "d_g")
        w._load_test_items(grp)
        vh = [w.ti_table.verticalHeaderItem(r).text()
              for r in range(w.ti_table.rowCount())]
        # 行序 = for_test：iddq(门) → sel(控制) → s1 → s0（数据），auto/期望殿后
        assert vh[0].startswith("d_iddq_mode"), vh
        assert "d_sel" in vh[1] and "d_s1" in vh[2] and "d_s0" in vh[3], vh
        assert w.ti_table.item(0, 0).text() == "0"        # 门行=透传值
        # 期望行在底部、手填仍工作
        w.ti_table.item(w._ti_mux_exp_row, 0).setText("2'b01")
        assert w._ti_mux_vecs[0].designer_expected == 1
    finally:
        w.close()


def test_gui_include_risky_toggle(gui_app, tmp_path, monkeypatch):
    """「缺前缀强制生成」开关（2026-06-10 Hi1108：前缀需求是 LPBT 实证，新设计可能根本不用，
    用户要强制生成交给仿真验证）：默认 force 子模块内部网缺前缀=跳过(阻断)；勾上=照常生成
    裸名 force、左表状态改「已强制生成」、build 不再跳过。"""
    import test_mux_wl as TM
    from dreg_verify import gui as G, generator
    monkeypatch.setattr(G, "EDITS_PATH", str(tmp_path / "edits.json"))
    excel = tmp_path / "risky_gui.xlsx"
    TM._build_mux_wb(
        str(excel),
        tmm_fields=[("SEL", "h10", "d_sel", "0", "N", "RW"),
                    ("S0", "h20", "d_s0[1:0]", "1:0", "N", "RW")],
        mux_rows=[   # case1 数据 d_unknown 表里查无 → force 兜底 → 默认硬阻断
            TM._mrow("d_s0_to_mux[1:0]", "d_sel_to_mux", "1'b0", "d_g[1:0]", 1),
            TM._mrow("d_unknown_to_mux[1:0]", "d_sel_to_mux", "1'b1", "d_g[1:0]", 1),
        ])
    w = G.MainWindow()
    w.path_edit.setText(str(excel))
    w.on_load()
    try:
        i = _mux_idx(w, "d_g")
        # 默认：needs-prefix + 阻断 + build 跳过
        assert not w.include_risky_chk.isChecked()
        assert w._analysis[i]["status"] == "needs-prefix"
        assert w._analysis[i].get("blocking") is True
        res0 = generator.build(w.wb, w._opts(["d_g[1:0]"]))
        assert res0["summary"]["n_mux_generated"] == 0
        assert res0["skipped"], "默认应跳过并给原因"
        # 勾上：重分析为非阻断、状态文案如实、build 生成
        resolver_before = w._resolver            # 缺前缀强制生成不改输入解析 → 不该重建 Resolver(防大表卡顿)
        w.include_risky_chk.setChecked(True)
        assert w._resolver is resolver_before, "include_risky 切换不应重建 Resolver(消除点击卡顿)"
        assert w._analysis[i]["status"] == "needs-prefix"
        assert w._analysis[i].get("blocking") is False
        assert "已强制生成" in w._analysis[i]["error"]
        row = next(r for r in range(w.table.rowCount()) if w._idx_of_row(r) == i)
        assert w.table.item(row, G.COL_STATUS).text() == "⚠缺前缀·已强制生成"
        res1 = generator.build(w.wb, w._opts(["d_g[1:0]"]))
        assert res1["summary"]["n_mux_generated"] == 1, res1["skipped"]
        txt = generator.render(res1)
        assert "d_g" in txt
    finally:
        w.close()


def test_logic_dft_gate_is_input_row(gui_app, tmp_path):
    """logic：受 dft 页门控的输出，真值表同样多一行 DFT 门输入（R_AUTO/R_EXP 整体下移，
    期望手填/负向编辑不受影响）。"""
    from dreg_verify import gui as G
    excel = tmp_path / "dftrow_lg_gui.xlsx"
    _dft_wb(tmp_path, "dftrow_lg_gui.xlsx")
    w = G.MainWindow()
    w.path_edit.setText(str(excel))
    w.on_load()
    try:
        sig = next(s for s in w.signals
                   if not isinstance(s, excel_model.MuxGroup) and s.out_base == "d_lg")
        w._load_test_items(sig)
        gn = len(w._ti_groups)
        assert w.ti_table.rowCount() == gn + 3              # 输入 + DFT门 + auto + 期望
        # 行序=寄存器地址：iddq(h11) < d_s0(h20) → DFT门行在第 1 行（行0）
        grow = next(r for r in range(w.ti_table.rowCount())
                    if "DFT门" in (w.ti_table.verticalHeaderItem(r).text() or ""))
        assert grow == 0, [w.ti_table.verticalHeaderItem(r).text()
                           for r in range(w.ti_table.rowCount())]
        assert "d_iddq_mode" in w.ti_table.verticalHeaderItem(grow).text()
        assert w.R_AUTO == gn + 1 and w.R_EXP == gn + 2     # auto/期望整体下移一行
        assert w.ti_table.item(grow, 0).text() == "0"       # 每条测试取透传值
        # 期望行手填仍工作（行号经 R_EXP 透出，处理器不感知偏移）
        w.ti_table.item(w.R_EXP, 0).setText("2'b10")
        assert w._ti_rows[0].get("designer_expected") == 2
    finally:
        w.close()


# ───────── 2026-06-10 bug：左表勾「负向」→ mux 真值表也要显示出负向列（用户报） ─────────
def _mux_table_row(w, sig):
    return next(r for r in range(w.table.rowCount())
               if w.signals[w._idx_of_row(r)] is sig)


def test_mux_left_neg_shows_in_editor(lpbt_win):
    """⭐bug 回归(2026-06-10)：在左表给 mux 勾「负向」，编辑器真值表此前看不到负向列
    （只在生成/导出/报告时追加），用户以为没生效。修复：_load_mux_test_items 据 _mux_neg
    用 add_negatives(which=first) 追加全局负向列，列头 _NEG。"""
    from PySide6 import QtCore
    from dreg_verify import gui as G
    w = lpbt_win
    grp = _load_buildable_mux(w)
    n0 = len(w._ti_mux_vecs)
    assert sum(1 for v in w._ti_mux_vecs if v.is_negative) == 0    # 勾之前无负向列
    # 模拟用户在左表勾「负向」（触发 on_signal_table_item_changed → 正看该信号会重渲编辑器）
    row = _mux_table_row(w, grp)
    w.table.item(row, G.COL_NEG).setCheckState(QtCore.Qt.Checked)
    assert grp.out_name.lower() in w._mux_neg
    negs = [v for v in w._ti_mux_vecs if v.is_negative]
    assert len(negs) == 1, "勾负向后编辑器真值表应出现 1 条负向列"
    assert len(w._ti_mux_vecs) == n0 + 1
    assert G.W.test_label(w._ti_mux_vecs[-1]).endswith("_NEG")     # 负向列在末尾、列头带 _NEG


def test_mux_left_neg_editor_matches_build(lpbt_win):
    """编辑器显示的负向列数 == build 真实产出的 n_negative（所见即所得）。"""
    from PySide6 import QtCore
    from dreg_verify import gui as G
    w = lpbt_win
    grp = _load_buildable_mux(w)
    row = _mux_table_row(w, grp)
    w.table.item(row, G.COL_NEG).setCheckState(QtCore.Qt.Checked)
    n_editor_neg = sum(1 for v in w._ti_mux_vecs if v.is_negative)
    res = generator.build(w.wb, w._opts([grp.out_name]))
    n_build_neg = sum(st["n_negative"] for _l, st in res["blocks"]
                      if st.get("out_name") == grp.out_name)
    assert n_editor_neg == n_build_neg == 1


def test_mux_left_neg_column_readonly(lpbt_win):
    """全局负向列=只读参考列：输入格/期望格都不可编辑（要自定义错值走「加负向(选中)」）。"""
    from PySide6 import QtCore
    from dreg_verify import gui as G
    w = lpbt_win
    grp = _load_buildable_mux(w)
    row = _mux_table_row(w, grp)
    w.table.item(row, G.COL_NEG).setCheckState(QtCore.Qt.Checked)
    nc = len(w._ti_mux_vecs) - 1                       # 末列=全局负向
    assert w._ti_mux_vecs[nc].is_negative
    for r in range(w.ti_table.rowCount()):             # 整列每一格都只读
        it = w.ti_table.item(r, nc)
        assert it is not None and not (it.flags() & QtCore.Qt.ItemIsEditable), r


def test_mux_left_neg_uncheck_removes_column(lpbt_win):
    """取消勾选「负向」→ 正看该信号时编辑器负向列即时消失（无残留）。"""
    from PySide6 import QtCore
    from dreg_verify import gui as G
    w = lpbt_win
    grp = _load_buildable_mux(w)
    row = _mux_table_row(w, grp)
    w.table.item(row, G.COL_NEG).setCheckState(QtCore.Qt.Checked)
    assert any(v.is_negative for v in w._ti_mux_vecs)
    w.table.item(row, G.COL_NEG).setCheckState(QtCore.Qt.Unchecked)
    assert grp.out_name.lower() not in w._mux_neg
    assert not any(v.is_negative for v in w._ti_mux_vecs), "取消勾选后负向列应消失"


def test_mux_left_neg_dedup_with_user_neg(lpbt_win):
    """左表「负向」(全局 which=first) 与用户逐 case 负向重叠 → 编辑器只显 1 条（与 build 去重一致）。"""
    from PySide6 import QtCore
    from dreg_verify import gui as G
    w = lpbt_win
    grp = _load_buildable_mux(w)
    # 先对首列加一条用户负向（= 全局 which=first 会撞的那条）
    w.ti_table.setCurrentCell(0, 0)
    w.on_ti_add_neg_selected()
    n_user_neg = sum(1 for v in w._ti_mux_vecs if v.is_negative)
    assert n_user_neg == 1
    # 再勾左表「负向」→ 全局负向与用户负向同源同错值 → 去重后仍只 1 条
    row = _mux_table_row(w, grp)
    w.table.item(row, G.COL_NEG).setCheckState(QtCore.Qt.Checked)
    assert sum(1 for v in w._ti_mux_vecs if v.is_negative) == 1, "重叠负向应去重"
    # build 同口径：负向也只 1 条
    res = generator.build(w.wb, w._opts([grp.out_name]))
    n_build_neg = sum(st["n_negative"] for _l, st in res["blocks"]
                      if st.get("out_name") == grp.out_name)
    assert n_build_neg == 1


def test_mux_left_neg_user_columns_still_editable(lpbt_win):
    """加了用户正向列 + 勾全局负向：用户列仍可编辑、全局负向只读（边界 user_start/user_end 正确）。"""
    from PySide6 import QtCore
    from dreg_verify import gui as G
    w = lpbt_win
    grp = _load_buildable_mux(w)
    w.ti_table.setCurrentCell(0, 0)
    w.on_ti_add()                                      # 加一条用户正向列
    ustart = w._ti_mux_user_start
    row = _mux_table_row(w, grp)
    w.table.item(row, G.COL_NEG).setCheckState(QtCore.Qt.Checked)
    # 用户列边界正确：[user_start,user_end) 是用户列，末尾是全局负向
    assert w._ti_mux_user_start == ustart
    assert w._ti_mux_user_end == len(w._ti_mux_vecs) - 1
    assert w._ti_mux_vecs[-1].is_negative
    # 用户正向列的数据格仍可编辑
    drow = next(iter(w._ti_mux_data_rows))
    uit = w.ti_table.item(drow, ustart)
    assert uit.flags() & QtCore.Qt.ItemIsEditable, "用户列数据格应仍可编辑"
