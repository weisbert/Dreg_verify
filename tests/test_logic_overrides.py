# -*- coding: utf-8 -*-
"""第三十七轮——「RTL 补充逻辑」(logic_overrides)：Excel 真表丢了某信号顶层口后的 ECO 级时
(如 d_en_vco_fc：SE 确认接了 2:1 mux + 二级 iddq、真表只到 DREG)，手工补一条【等价 logic 式】，
工具当合成 logic 行扫真值表(ECO 新输入自动成维度)。覆盖：合成被扫 + 块顶 ⚠ + 汇总 + 报告/HTML banner、
swap-restore 不污染共享 wb、enabled=False 停用、空/None 与非补充信号逐字节不变。"""

import pytest

from tests import fixtures
from dreg_verify import excel_model, generator as G, cli


@pytest.fixture
def wbx(tmp_path):
    p = str(tmp_path / "wb.xlsx")
    fixtures.build_workbook(p)
    return excel_model.load_workbook(p)


# 给 lna_agc 包一级旁路：EXTRA(=d_bt_lp_iddq，regmap 里 RO，可解析) 是 Excel 真表没有的 ECO 输入。
# 原逻辑 A?C:B 改写成真实名 LINE?LOCAL:AGCLINE，外面再包 EXTRA。
SUPP = {"d_logic_bt_lp_lna_agc": {
    "enabled": True,
    "note": "SE: ECO 顶层口加了一级旁路，真表只到 DREG",
    "expr": "EXTRA ? 3'b0 : (LINE ? LOCAL : AGCLINE)",
    "inputs": [
        {"var": "EXTRA",   "raw": "d_bt_lp_iddq"},
        {"var": "LINE",    "raw": "d_bt_lp_lna_line_sel"},
        {"var": "LOCAL",   "raw": "d_bt_lp_lna_agc_local[2:0]"},
        {"var": "AGCLINE", "raw": "d_bt_lp_lna_agc_line[2:0]"},
    ],
}}


def test_supplement_sweeps_new_input_and_warns(wbx):
    opts = G.GenOptions(signals=["d_logic_bt_lp_lna_agc"], logic_overrides=SUPP)
    res = G.build(wbx, opts)
    assert res["summary"]["n_supplement"] == 1
    assert len(res["supplement_warnings"]) == 1
    name, aid, why = res["supplement_warnings"][0]
    assert name.startswith("d_logic_bt_lp_lna_agc") and "手工补充" in why
    blk = res["blocks"][0][0]
    assert blk[0].startswith("// ⚠") and "手工补充" in blk[0]
    assert "ECO 顶层口加了一级旁路" in blk[0]          # 理由进块顶
    # 报告真值表：ECO 新输入 d_bt_lp_iddq 被扫成真值表维度
    rep = G.report(wbx, opts)
    tbl = [t for t in rep["tables"] if t["signal"].startswith("d_logic_bt_lp_lna_agc")][0]
    labels = [i["label"] for i in tbl["inputs"]]
    assert "d_bt_lp_iddq" in labels
    assert len(labels) == 4                            # 原 3 输入 + EXTRA
    assert tbl["supplement"]                           # 报告携带补充标记


def test_supplement_html_banner(wbx, tmp_path):
    import json
    import re
    opts = G.GenOptions(top_output_only=False, logic_overrides=SUPP)
    rep = G.report(wbx, opts)
    p = tmp_path / "r.html"
    cli.write_report(str(p), rep, "synthetic.xlsx")
    raw = p.read_text(encoding="utf-8")
    assert ".suppbar{" in raw                          # banner 样式在静态壳
    # 真值表块 HTML 在 tt-data blob 的 item.h 里（JSON 编码、引号转义）——解析出来再查
    m = re.search(r'<script type="application/json" id="tt-data">(.*?)</script>', raw, re.S)
    assert m, "缺少 tt-data blob"
    items = json.loads(m.group(1))
    supp_blk = next((it["h"] for it in items
                     if it.get("s", "").startswith("d_logic_bt_lp_lna_agc")), None)
    assert supp_blk, "应有补充信号的真值表块"
    assert 'class="suppbar"' in supp_blk               # 补充 banner 渲进该块
    assert "手工补充" in supp_blk
    # 非补充信号的块不应有 banner
    other = next((it["h"] for it in items
                  if it.get("s", "").startswith("d_logic_bt_lp_reserve")), None)
    if other:
        assert "suppbar" not in other


def test_supplement_no_pollution_on_shared_wb(wbx):
    """swap-restore：补充 build 不改 wb.logic；同一 wb 紧接着无补充 build 完全不受影响。"""
    base_expr = [s.expr for s in wbx.logic if s.out_base == "d_logic_bt_lp_lna_agc"][0]
    plain = G.render(G.build(wbx, G.GenOptions()))
    G.build(wbx, G.GenOptions(logic_overrides=SUPP))   # 带补充，同 wb
    # wb.logic 原行 expr 未被原地改写
    assert [s.expr for s in wbx.logic if s.out_base == "d_logic_bt_lp_lna_agc"][0] == base_expr
    # 紧接着再无补充 build → 与最初逐字节相同（无残留污染）
    assert G.render(G.build(wbx, G.GenOptions())) == plain


def test_supplement_does_not_leak_to_other_signals(wbx):
    one = G.GenOptions(signals=["d_logic_bt_lp_reserve"])
    one_supp = G.GenOptions(signals=["d_logic_bt_lp_reserve"], logic_overrides=SUPP)
    assert G.render(G.build(wbx, one)) == G.render(G.build(wbx, one_supp))


def test_supplement_disabled_is_noop(wbx):
    off = {"d_logic_bt_lp_lna_agc":
           dict(SUPP["d_logic_bt_lp_lna_agc"], enabled=False)}
    a = G.render(G.build(wbx, G.GenOptions(signals=["d_logic_bt_lp_lna_agc"])))
    b = G.render(G.build(wbx, G.GenOptions(signals=["d_logic_bt_lp_lna_agc"],
                                           logic_overrides=off)))
    assert a == b
    res = G.build(wbx, G.GenOptions(signals=["d_logic_bt_lp_lna_agc"], logic_overrides=off))
    assert res["summary"]["n_supplement"] == 0


def test_supplement_empty_and_none_byte_identical(wbx):
    a = G.render(G.build(wbx, G.GenOptions()))
    b = G.render(G.build(wbx, G.GenOptions(logic_overrides={})))
    c = G.render(G.build(wbx, G.GenOptions(logic_overrides=None)))
    assert a == b == c


def test_supplement_pure_new_signal(wbx):
    """基名不在 Excel logic 页 → 作为【纯新增】合成信号生成（仍带 ⚠）。"""
    nov = {"d_logic_brand_new_sig": {
        "enabled": True, "note": "整条新增的 ECO 信号",
        "expr": "SEL ? B : A",
        "inputs": [
            {"var": "SEL", "raw": "d_bt_lp_iddq"},
            {"var": "A",   "raw": "d_bt_lp_bt_mode_sel"},
            {"var": "B",   "raw": "d_bt_lp_bt_mode_sel_local"},
        ],
    }}
    res = G.build(wbx, G.GenOptions(signals=["d_logic_brand_new_sig"], logic_overrides=nov))
    assert res["summary"]["n_supplement"] == 1
    assert res["summary"]["n_generated"] == 1
    assert res["blocks"][0][0][0].startswith("// ⚠")


# ───────────── GUI：校验 / 配置导出导入 / _opts 透传 ─────────────
@pytest.fixture(scope="module")
def qapp():
    pytest.importorskip("PySide6")
    from PySide6 import QtWidgets
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def test_gui_logic_overrides_validate_config_and_opts(qapp, tmp_path_factory):
    from dreg_verify import gui
    path = tmp_path_factory.mktemp("glo") / "synthetic_dreg.xlsx"
    fixtures.build_workbook(str(path))
    w = gui.MainWindow()
    w.path_edit.setText(str(path))
    w.on_load()

    # 校验器：好 spec 通过；表达式用了没映射的变量 → 报错（不保存）
    good, errs = w._validate_supplements(SUPP)
    assert not errs and "d_logic_bt_lp_lna_agc" in good
    bad = {"d_logic_bt_lp_lna_agc": {"enabled": True, "expr": "NOPE ? A : B",
                                     "inputs": [{"var": "A", "raw": "d_bt_lp_iddq"}]}}
    _g, errs2 = w._validate_supplements(bad)
    assert errs2 and any("NOPE" in e or "变量" in e for e in errs2)
    # 表达式语法错也被挡
    _g2, errs3 = w._validate_supplements(
        {"x": {"enabled": True, "expr": "A ? ", "inputs": [{"var": "A", "raw": "d_bt_lp_iddq"}]}})
    assert errs3

    # 模板：当前编辑器信号 → 预填原表达式+原输入
    sig = next(s for s in w.signals if s.out_name == "d_logic_bt_lp_reserve")
    w._load_test_items(sig)
    tmpl = w._supplement_template()
    assert "d_logic_bt_lp_reserve" in tmpl and tmpl["d_logic_bt_lp_reserve"]["expr"] == sig.expr

    # 进 _opts → build 生效（GUI 配置 → 合成扫真值表）
    w._logic_overrides = {k: dict(v) for k, v in SUPP.items()}
    res = G.build(w.wb, w._opts(["d_logic_bt_lp_lna_agc"]))
    assert res["summary"]["n_supplement"] == 1

    # 完整配置导出带 logic_overrides；reset 清空
    cfg = w._collect_config()
    assert "logic_overrides" in cfg and "d_logic_bt_lp_lna_agc" in cfg["logic_overrides"]
    w._reset_all_config_state()
    assert w._logic_overrides == {}
