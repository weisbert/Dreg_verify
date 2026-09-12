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


def test_supplement_self_reference_input_resolves_as_register(wbx):
    """d_en_vco_fc 真形：补充式的某输入(EN)基名 == 输出基名(自引用)，且该基名同时是寄存器。
    必须解析成 RF_WRITE(寄存器)、不被错误 cone 展开成自身、不递归。"""
    # 让 d_logic_bt_lp_reserve 同时也是 0x16 寄存器（镜像 d_en_vco_fc 既是 logic 输出又是寄存器）
    wbx.regmap["d_logic_bt_lp_reserve"] = excel_model.RegmapEntry(
        signal="d_logic_bt_lp_reserve", reg_name="RSV", reg_type="RW", default=0,
        bit_lsb=1, bit_msb=1, owner="x", address=0x16)
    ov = {"d_logic_bt_lp_reserve": {
        "enabled": True, "note": "自引用输入(镜像 d_en_vco_fc EN=自身寄存器)",
        "expr": "IDDQ2 ? 1'b0 : (SEL ? FASTON : EN)",
        "inputs": [
            {"var": "EN",     "raw": "d_logic_bt_lp_reserve_to_logic"},  # base==out_base 自引用
            {"var": "SEL",    "raw": "d_bt_lp_bt_mode_sel"},
            {"var": "FASTON", "raw": "d_bt_lp_bt_mode_sel_local"},
            {"var": "IDDQ2",  "raw": "d_bt_lp_iddq"},
        ]}}
    res = G.build(wbx, G.GenOptions(signals=["d_logic_bt_lp_reserve"], logic_overrides=ov))
    assert res["summary"]["n_supplement"] == 1
    assert res["summary"]["n_generated"] == 1          # 没被当 risky 跳过、没递归报错
    sv = "\n".join(res["blocks"][0][0])
    assert "RF_WRITE(10'h16" in sv                      # 自引用 EN 解析成 0x16 寄存器写


# ───────────── DFT 门 与 显式输入 去重（SE 把 iddq 挪进 dft 页后的交互） ─────────────
def test_dft_gate_deduped_when_already_explicit_input(wbx):
    """门网已是本信号【显式输入】时，不再单列 DFT 门行 / 不重复补 DFT 拍。
    reserve 表达式 (A?C:B)&~J 的 J 本就是 d_bt_lp_iddq；若 SE 又把它挪进 dft 页当门控 → 撞两行，去重。"""
    opts = G.GenOptions(signals=["d_logic_bt_lp_reserve"])
    base = G.report(wbx, opts)["tables"]
    bt = [x for x in base if x["signal"].startswith("d_logic_bt_lp_reserve")][0]
    n_in, n_col = len(bt["inputs"]), len(bt["tests"])
    # SE：把 d_bt_lp_iddq 挪到 dft 页当 d_logic_bt_lp_reserve 的门——但它本就是 J 输入
    wbx.dft["d_logic_bt_lp_reserve"] = {
        "gate_base": "d_bt_lp_iddq", "gate_raw": "d_bt_lp_iddq", "transparent": 0}
    t = [x for x in G.report(wbx, opts)["tables"]
         if x["signal"].startswith("d_logic_bt_lp_reserve")][0]
    assert all(i.get("letters") != "dft门" for i in t["inputs"])   # 没有单列的 DFT 门行
    assert len(t["inputs"]) == n_in                                # 输入行数不变（没多一行 iddq）
    assert len(t["tests"]) == n_col                                # 没多补 DFT 拍列
    # build 侧同口径：不报 iddq_skipped、向量数不因 dft 门增加
    res = G.build(wbx, opts)
    assert "// ⚠" not in res["blocks"][0][0][0] or "iddq" not in res["blocks"][0][0][0].lower()


def test_dft_gate_still_pins_when_not_an_explicit_input(wbx):
    """门网【不是】显式输入时，DFT 门照常单列（去重不过度）。lna_agc 三输入都不是 d_bt_lp_iddq。"""
    wbx.dft["d_logic_bt_lp_lna_agc"] = {
        "gate_base": "d_bt_lp_iddq", "gate_raw": "d_bt_lp_iddq", "transparent": 0}
    t = [x for x in G.report(wbx, G.GenOptions(signals=["d_logic_bt_lp_lna_agc"]))["tables"]
         if x["signal"].startswith("d_logic_bt_lp_lna_agc")][0]
    assert any(i.get("letters") == "dft门" for i in t["inputs"])   # 门不是输入 → 照常单列 DFT 门行


# ───── 补充逻辑：校验 / 模板 / 进 build / 进配置（C5-b：原「起个 v1 窗」的 2 条 → 1 条 Qt-free）─────
# 原 `test_gui_logic_overrides_validate_config_and_opts` 起一台 v1 `MainWindow`，
# 调的却是 `session.validate_supplements` / `session.supplement_template` /
# `session.collect_config` 这三个 Qt-free 函数加一次 `generator.build` —— 窗口只是个壳。
# 原 `test_gui_supplement_syncs_truth_table_and_marks`（补充后真值表同步 + 清单标注）在 v2 由
# `tests/test_ui_diagnostics.py::test_c215_c216_supplement_visible_in_topout_expand`（新输入成维度）、
# `tests/test_ui_signal_list.py::test_c039_supplement_amber_dot_and_tooltip`（清单琥珀标记）与
# `tests/test_ui_migrated_c5b.py::test_c216_supplement_does_not_dirty_wb_logic_and_still_renders_sv`
# （补充/撤销两个方向 + wb.logic 不被改脏）三条合起来守。


def test_supplements_validate_template_and_reach_build(tmp_path_factory):
    """校验器挡住坏 spec；模板预填原式原输入；好 spec 一路进 build 与完整配置。"""
    from dreg_verify import session

    path = tmp_path_factory.mktemp("glo") / "synthetic_dreg.xlsx"
    fixtures.build_workbook(str(path))
    wbx = excel_model.load_workbook(str(path))

    # ① 校验器：好 spec 通过；表达式用了没映射的变量 → 报错（不保存）
    good, errs = session.validate_supplements(SUPP)
    assert not errs and "d_logic_bt_lp_lna_agc" in good
    bad = {"d_logic_bt_lp_lna_agc": {"enabled": True, "expr": "NOPE ? A : B",
                                     "inputs": [{"var": "A", "raw": "d_bt_lp_iddq"}]}}
    _g, errs2 = session.validate_supplements(bad)
    assert errs2 and any("NOPE" in e or "变量" in e for e in errs2)
    # 表达式语法错也被挡
    _g2, errs3 = session.validate_supplements(
        {"x": {"enabled": True, "expr": "A ? ", "inputs": [{"var": "A", "raw": "d_bt_lp_iddq"}]}})
    assert errs3

    # ② 模板：给一条 logic 信号 → 预填原表达式 + 原输入映射
    sig = next(s for s in wbx.logic if s.out_name == "d_logic_bt_lp_reserve")
    tmpl = session.supplement_template(sig)
    assert "d_logic_bt_lp_reserve" in tmpl and tmpl["d_logic_bt_lp_reserve"]["expr"] == sig.expr

    # ③ 进 GenOptions → build 真的按补充后的逻辑扫真值表
    res0 = G.build(wbx, G.GenOptions(signals=["d_logic_bt_lp_lna_agc"]))
    assert res0["summary"].get("n_supplement", 0) == 0
    res = G.build(wbx, G.GenOptions(signals=["d_logic_bt_lp_lna_agc"],
                                    logic_overrides={k: dict(v) for k, v in SUPP.items()}))
    assert res["summary"]["n_supplement"] == 1

    # ④ 完整配置带 logic_overrides（同事之间靠这份文件传补充逻辑）
    cfg = session.collect_config(str(path), {}, logic_overrides=SUPP)
    assert "logic_overrides" in cfg and "d_logic_bt_lp_lna_agc" in cfg["logic_overrides"]
    # 空补充 → 桶被剔掉（`_reset_all_config_state` 之后导出的就是这一份）
    cfg0 = session.collect_config(str(path), {}, logic_overrides={})
    assert not cfg0.get("logic_overrides")
