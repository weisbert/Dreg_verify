# -*- coding: utf-8 -*-
"""test_ui_migrated_c5b.py —— C5-b 迁移补强（架构 §7.1 的「v2 弱于老断言 → 先补强再删」）。

这个文件里的每一条，都对应 `docs/GUI_v2_测试迁移_C5b.csv` 里一行 `删除·v2已补强(...)`：
老测试里有一句断言，v2 侧现有测试**没有**同等或更强的对应，所以在删掉老测试之前先把那一句
钉在 v2 上。判据一律是「真控件交互 / 真产物 + 可观察结果」，不直接调槽、不比内部字段。

口径与 `test_ui_export_center.py` / `test_ui_c3_integration.py` 一致：真 `WorkbenchState`
（→ 真 provider / 真引擎）+ 真组合根；夹具只用 `tests/` 里的两张 mirror 镜像表
（公开仓，**绝不出现真实信号名**）。有状态的动作一律做两次，且第二次与第一次结果不同 ——
「做一次看着对」是这一层最容易漏掉的假绿。
"""

import io
import os

import pytest

import ui_harness as H                                    # noqa: E402

pytest.importorskip("PySide6")

from dreg_verify import exports as X                      # noqa: E402
from dreg_verify import rtl_scan                          # noqa: E402
from dreg_verify import session as SS                     # noqa: E402
from dreg_verify.ui import app as A                       # noqa: E402
from dreg_verify.ui import contracts, names               # noqa: E402
from dreg_verify.ui import export_center as EC            # noqa: E402
from dreg_verify.ui import persist as P                   # noqa: E402
from dreg_verify.ui import state as ST                    # noqa: E402

LC = contracts.ListCol
LR = contracts.ListRole

#: btlp 镜像里的三个落点 —— 一个 logic 根、一个 mux 根、一个直连寄存器根
LOGIC_SIG = "d_logic_bt_lp_rx_en"
MUX_SIG = "d_bt_lp_lna_itrim"
REG_SIG = "clk_force_on"
REG_SIG2 = "en_dig_clk"
RO_SIG = "pll_lock_indicator"


# ═══════════════════════════ 夹具 ═══════════════════════════
@pytest.fixture(scope="module")
def qapp():
    return H.app()


@pytest.fixture(scope="module")
def btlp():
    return H.mirror_path("btlp")


@pytest.fixture
def iso(monkeypatch, tmp_path):
    monkeypatch.setattr(P, "SETTINGS_PATH", str(tmp_path / "gui_settings.json"))
    monkeypatch.setattr(P, "EDITS_PATH", str(tmp_path / "edits.json"))
    return tmp_path


@pytest.fixture
def st(qapp, iso, btlp):
    """真 `WorkbenchState` + 骨架清单（= app 在 worker 起跑前的那两步）。"""
    s = ST.WorkbenchState()
    assert s.load(btlp)
    s.set_models("topout", s.provider("topout").skeleton_models(), partial=True)
    return s


@pytest.fixture
def rec(monkeypatch, tmp_path):
    d = tmp_path / "save"
    d.mkdir()
    return H.auto_dialogs(monkeypatch, save_dir=str(d))


def _enable(rows, *kinds):
    for r in rows:
        r.enabled = r.kind in kinds
    return rows


def _run(st_, rows, save_dir, path_of=None):
    plan = EC.build_plan(st_, rows)
    ask = path_of or (lambda row: os.path.join(str(save_dir), EC.default_filename(row, st_)))
    return EC.run_plan(st_, plan, ask)


def _read(path, encoding="utf-8"):
    return io.open(path, encoding=encoding).read()


# ═══════════ C-185…C-189：nets.txt 按页类别（老 test_topout_export_nets_topout_only_...）═══════════
@pytest.mark.contract("C-185", "C-187", "C-188")
def test_c185_c187_c188_nets_topout_category_keeps_register_roots(st, rec, tmp_path):
    """只勾「topout」这一类 → nets.txt 里有**直连寄存器根**的探针网。

    这正是老固定 logic+mux+dft 口径整类漏掉的那一批（v1 `test_topout_export_nets_topout_only_
    includes_register_roots`）。v2 的 `test_c185_..._nets_purpose_and_pages` 只验了「类别列表
    与分项计数」，没验**类别选择真的换了产物内容**——漏了的话界面上勾得有模有样、文件里少一批网，
    要等仿真报「找不到这根网」才发现。
    """
    rows = _enable(EC.default_rows(st), "nets")
    row = next(r for r in rows if r.kind == "nets")

    # ① 只勾 topout 类 → 两个寄存器直连根都在
    row.options = {"purposes": [], "pages": ["topout"]}
    res = _run(st, rows, tmp_path / "save")
    assert not res.errors, res.errors
    nets_topout = rtl_scan.parse_nets_text(_read(res.outcomes[0].path))
    assert REG_SIG in nets_topout, "只勾 topout 类却丢了直连寄存器根 %s" % REG_SIG
    assert REG_SIG2 in nets_topout

    # ② 换成只勾 logic 类 → 产物必须**不同**（类别不是摆设）
    row.options = {"purposes": [], "pages": ["logic"]}
    res2 = _run(st, rows, tmp_path / "save")
    nets_logic = rtl_scan.parse_nets_text(_read(res2.outcomes[0].path))
    assert nets_logic != nets_topout, "换了类别产物一个字没变 = 类别选择没接上"
    assert REG_SIG not in nets_logic, "logic 类不该含直连寄存器根探针"


# ═══════════ C-179…C-184：for_test 回填含 mux（老 test_topout_fortest_backfill_includes_mux）═══════════
@pytest.mark.contract("C-184")
def test_c184_fortest_backfill_carries_the_mux_truth_table(st, rec, tmp_path):
    """回填的 for_test 页里真的有 **mux** 的真值表（堵「只回填 logic」这个陷阱）。

    v2 的 `test_c179_..._fortest` 只验到 `out.note == "含 mux 表"` 与 `provider is not None`
    这一层标签；标签对、内容空是搬家时最典型的一种漏。
    """
    import openpyxl

    def cells(path):
        wb = openpyxl.load_workbook(path)
        ws = next(s for s in wb.worksheets if s.title.lower() == "for_test")
        return "\n".join(str(c.value) for r in ws.iter_rows() for c in r if c.value)

    rows = _enable(EC.default_rows(st), "fortest")
    row = next(r for r in rows if r.kind == "fortest")

    # ① 全部信号 → mux 表在
    row.object_scope = "all"
    res = _run(st, rows, tmp_path / "save",
               path_of=lambda _r: str(tmp_path / "ft_all.xlsx"))
    assert not res.errors, res.errors
    txt_all = cells(res.outcomes[0].path)
    assert "lna_itrim" in txt_all, "for_test 回填没带上 mux 真值表"

    # ② 只勾一个 logic 根 → mux 表不该再出现（范围真的收窄了）
    st.set_checked([m["name"] for m in st.models()], False)
    st.set_checked([LOGIC_SIG], True)
    row.object_scope = "checked"
    res2 = _run(st, rows, tmp_path / "save",
                path_of=lambda _r: str(tmp_path / "ft_one.xlsx"))
    txt_one = cells(res2.outcomes[0].path)
    assert "lna_itrim" not in txt_one, "范围切成勾选之后 mux 表还在 = 范围没生效"
    assert txt_one != txt_all


# ═══════════ C-158：.sv 导出范围「仅负向」（老 test_topout_export_scope_pos_neg 的后半）═══════════
@pytest.mark.contract("C-158")
def test_c158_sv_scope_neg_only_keeps_negatives_and_drops_clean_signals(st, rec, tmp_path):
    """「仅负向」= 只留反例断言，**一条反例都没有的信号整块不出现**（记账，不静默丢）。

    v2 的 `test_c157_..._sv_row_options_to_exports` 只验了「仅正向 → 产物里没有 _NEG」那一半；
    「仅负向」这一半没人验。漏了的话工程师拿到的 .sv 里混着正向断言，反例回归就测了个寂寞。
    """
    st.set_neg(LOGIC_SIG, True)                       # 给一个 logic 根加一条反例
    rows = _enable(EC.default_rows(st), "sv")
    row = next(r for r in rows if r.kind == "sv")
    row.object_scope = "all"

    # ① 仅负向
    row.sv_scope = "neg"
    res = _run(st, rows, tmp_path / "save", path_of=lambda _r: str(tmp_path / "neg.sv"))
    assert not res.errors, res.errors
    neg = _read(res.outcomes[0].path)
    assert "_NEG" in neg
    assert LOGIC_SIG in neg
    assert ("`ENV_RF.%s==" % REG_SIG) not in neg, "没有反例的寄存器信号不该出现在『仅负向』里"

    # ② 换成仅正向 → 同一份状态下产物必须不同，且一条 _NEG 都没有
    row.sv_scope = "pos"
    res2 = _run(st, rows, tmp_path / "save", path_of=lambda _r: str(tmp_path / "pos.sv"))
    pos = _read(res2.outcomes[0].path)
    assert "_NEG" not in pos and pos != neg
    assert ("`ENV_RF.%s==" % REG_SIG) in pos, "仅正向不该把没有反例的信号也删掉"

    # ③ 导出摘要里反例的条数要看得见（v1 `_export_summary_text` 的「负向 N」那一格）
    row.sv_scope = "all"
    res3 = _run(st, rows, tmp_path / "save", path_of=lambda _r: str(tmp_path / "all.sv"))
    line = EC.ExportDoneDialog(res3).written_lines()[0]
    assert "负向 1" in line, "导出摘要没把反例条数说出来（v1 的『负向 N』那一格）：%s" % line


# ═══════════ C-157 / C-183：.sv 只导勾选的（老 test_topout_export_sv_only_checked）═══════════
@pytest.mark.contract("C-157", "C-183")
def test_c157_c183_sv_object_scope_checked_emits_only_checked_signals(st, rec, tmp_path):
    """范围切「勾选」→ .sv 里**只有**勾着的那几个信号块。

    v2 的 `test_c177_c178_report_scope_and_summary` 只验了**报告**那一路的 `only=`；
    .sv 这一路（工程师真正交出去的那份）此前没人从产物内容上验过。
    """
    rows = _enable(EC.default_rows(st), "sv")
    row = next(r for r in rows if r.kind == "sv")
    row.object_scope = "checked"

    # ① 只勾一个 logic 根
    st.set_checked([m["name"] for m in st.models()], False)
    st.set_checked([LOGIC_SIG], True)
    one = _read(_run(st, rows, tmp_path / "save",
                     path_of=lambda _r: str(tmp_path / "one.sv")).outcomes[0].path)
    assert ("`ENV_RF.%s==" % LOGIC_SIG) in one
    assert ("`ENV_RF.%s==" % REG_SIG) not in one, "没勾的信号也产出了断言"

    # ② 再勾上寄存器根 → 两个都在（第二次与第一次不同）
    st.set_checked([REG_SIG], True)
    two = _read(_run(st, rows, tmp_path / "save",
                     path_of=lambda _r: str(tmp_path / "two.sv")).outcomes[0].path)
    assert ("`ENV_RF.%s==" % LOGIC_SIG) in two and ("`ENV_RF.%s==" % REG_SIG) in two
    assert two != one


# ═══════════ C-173…C-176：HTML 报告记账 RO（老 test_topout_export_report_html）═══════════
@pytest.mark.contract("C-174")
def test_c174_report_html_still_accounts_the_readonly_root(st, rec, tmp_path):
    """HTML 报告里既有可建信号的真值表，也点着名留下**只读回读**这一条（记账，不静默丢）。

    v2 的 `test_c173_..._report_formats_and_ext` 只验了「三种格式各出各的文件 + 扩展名」，
    没有验报告正文里那条「按设计不产断言」的信号还在不在 —— 它恰恰是 designer 最需要复核的一类。
    """
    rows = _enable(EC.default_rows(st), "report")
    row = next(r for r in rows if r.kind == "report")
    row.options = {"format": "html"}

    # ① 全部信号
    row.object_scope = "all"
    res = _run(st, rows, tmp_path / "save", path_of=lambda _r: str(tmp_path / "rep_all.html"))
    assert not res.errors, res.errors
    html_all = _read(res.outcomes[0].path)
    assert LOGIC_SIG in html_all
    assert RO_SIG in html_all, "只读回读根在报告里被静默丢了"

    # ② 范围收到只勾 logic 根 → RO 那条不再出现（范围真的生效）
    st.set_checked([m["name"] for m in st.models()], False)
    st.set_checked([LOGIC_SIG], True)
    row.object_scope = "checked"
    html_one = _read(_run(st, rows, tmp_path / "save",
                          path_of=lambda _r: str(tmp_path / "rep_one.html")).outcomes[0].path)
    assert LOGIC_SIG in html_one and RO_SIG not in html_one
    assert html_one != html_all


# ═══════════ C-209：强制 force 名单真的改 .sv（老 test_force_signals_reach_topout_and_page_backends）═══════════
@pytest.mark.contract("C-209")
def test_c209_force_signal_rewrites_the_sv_product_and_restores_bytes(st):
    """配了「强制 force」的网，在 **.sv 产物**里从 RF_WRITE 变成 `force`；清空名单逐字节回默认。

    v2 的 `test_c206_..._effective_on_topout_and_page` 验到 `binding.kind` 与输入表驱动串
    （分析层）为止。产物这一层单独要验：v1 的原始 bug 就是「分析层看着对了、生成 .sv 时
    根本没传 force_overrides」—— 用户配了等于没配，全程无提示。
    """
    leaf = "d_bt_lp_rx_en_local"
    forced = "force `ENV_RF.%s=" % leaf
    prov = st.provider("topout")

    sv0, _ = prov.render_sv([LOGIC_SIG], "min", 256, False, {})
    assert forced not in sv0, "出厂就是 force 的话这条测试比不出东西"

    # ① 配上 → 产物里该网变 force
    st.set_force_signals({leaf})
    sv1, _ = prov.render_sv([LOGIC_SIG], "min", 256, False, {})
    assert forced in sv1, ".sv 里那根网还是 RF_WRITE —— 名单没接到生成器"
    assert sv1 != sv0

    # ② 清空 → 逐字节回到默认（不留半个状态）
    st.set_force_signals(set())
    sv2, _ = prov.render_sv([LOGIC_SIG], "min", 256, False, {})
    assert sv2 == sv0, "清空名单后 .sv 没有逐字节回到默认"


# ═══════════ C-215 / C-216：补充逻辑不弄脏 wb（老 test_topout_logic_overrides_supplements_cone）═══════════
@pytest.mark.contract("C-216")
def test_c216_supplement_does_not_dirty_wb_logic_and_still_renders_sv(st):
    """RTL 补充逻辑走的是 swap-and-restore：cone 看得到补充，但 `wb.logic` **条数一条不多**。

    v2 的 `test_c215_c216_supplement_visible_in_topout_expand` 只验了「补充的输入成了真值表维度」；
    `tests/test_providers.py::test_providers_equivalent_with_logic_overrides` 只验到
    `wb.logic is not None`。漏了计数这一句，`_supplemented` 往 `wb.logic` 里追加而不还原时
    会静默积累 —— 同一个会话里点第二次，真值表维度就翻倍了。
    """
    supp = {"d_logic_bt_lp_lna_agc": {
        "enabled": True, "note": "SE: ECO 顶层口加了一级旁路，真表只到 DREG",
        "expr": "EXTRA ? 3'b0 : (LINE ? LOCAL : AGCLINE)",
        "inputs": [
            {"var": "EXTRA", "raw": "d_bt_lp_iddq"},
            {"var": "LINE", "raw": "d_bt_lp_lna_line_sel"},
            {"var": "LOCAL", "raw": "d_bt_lp_lna_agc_local[2:0]"},
            {"var": "AGCLINE", "raw": "d_bt_lp_lna_agc_line[2:0]"},
        ]}}
    prov = st.provider("topout")
    n_logic0 = len(st.wb.logic)
    vm0 = {m["name"]: m for m in prov.view_models("min", 256, False)}
    n0 = len(vm0["d_logic_bt_lp_lna_agc"]["inputs"])

    # ① 配上补充 → 多一维输入、.sv 仍产出该信号、wb.logic 条数没变
    st.set_logic_overrides({k: dict(v) for k, v in supp.items()})
    vm1 = {m["name"]: m for m in prov.view_models("min", 256, False)}
    labels = [i["label"] for i in vm1["d_logic_bt_lp_lna_agc"]["inputs"]]
    assert "d_bt_lp_iddq" in labels and len(labels) > n0
    text, _ = prov.render_sv(["d_logic_bt_lp_lna_agc"], "min", 256, False, {})
    assert "d_logic_bt_lp_lna_agc" in text
    assert len(st.wb.logic) == n_logic0, "swap-and-restore 漏了：wb.logic 被改脏"

    # ② 再算一遍（第二次）：维度不该累加 —— 只追加不还原的话这里会翻倍
    vm2 = {m["name"]: m for m in prov.view_models("min", 256, False)}
    assert len(vm2["d_logic_bt_lp_lna_agc"]["inputs"]) == len(labels)
    assert len(st.wb.logic) == n_logic0

    # ③ 撤掉补充 → 回到出厂维度
    st.set_logic_overrides({})
    vm3 = {m["name"]: m for m in prov.view_models("min", 256, False)}
    assert len(vm3["d_logic_bt_lp_lna_agc"]["inputs"]) == n0


# ═══════════ C-148 / C-152：清单用例数 == 真值表列数（老 test_form_cov_applies_to_truth_table_too）═══════════
@pytest.fixture
def win(qapp, monkeypatch, tmp_path):
    """真组合根 + 真 state + 真 worker（与 `test_ui_c3_integration.py::win` 同口径）。"""
    H.isolate_settings(monkeypatch, tmp_path)
    H.auto_dialogs(monkeypatch)
    w = A.build_window([])
    w.resize(1600, 900)
    w.show()
    yield w
    w.close()
    H.app().processEvents()


def _load_win(w, kind="btlp"):
    ended = []
    w.analysisEnded.connect(lambda vid, ok: ended.append((vid, ok)))
    w.load_path(H.mirror_path(kind))
    assert H.wait_for(lambda: bool(ended)), "worker 没跑完：%s" % (ended,)
    H.app().processEvents()
    return w.state.models()


def _click_row(w, name):
    panel = w.list_panel
    row = next((r for r in range(panel.proxy.rowCount())
                if panel.proxy.index(r, int(LC.NAME)).data(int(LR.NAME)) == name), None)
    assert row is not None, "清单里没有 %r" % name
    H.click_cell(panel.view, row, int(LC.NAME))
    H.app().processEvents()
    assert w.state.current_name == name
    return row


@pytest.mark.contract("C-148", "C-152")
def test_c148_c152_form_cov_gives_list_and_truth_table_the_same_count(win, qapp):
    """按逻辑类型设的档，**清单那一列的用例数**与**真值表的列数**必须是同一个数。

    v1 的实证 bug：`_mode_for` 只认单点档、不认逻辑类型档 → 左边清单按 form_cov 算、
    右边真值表按全局算，用户看到「清单 5 / 真值表 32」。v2 的
    `test_c148_c150_effective_chain_bar_text` 只验了标题栏那句话的文字，没验两边的**数**。
    """
    w = win
    _load_win(w)
    cov = w.state.coverage()

    def one_select_signal():
        for m in w.state.models():
            if m.get("form") == "select" and m.get("status") == "ok":
                return m["name"]
        pytest.skip("这张镜像表没有『选路』形态的可建信号")

    def n_list(name):
        return next(m["n_vectors"] for m in w.state.models() if m["name"] == name)

    name = one_select_signal()

    # ① 选路 = 穷举
    cov.set_form_cov({"select": "exhaustive"})
    w.state.coverage_touched("topout")
    qapp.processEvents()
    assert H.wait_for(lambda: n_list(name) is not None)
    _click_row(w, name)
    n_exh = n_list(name)
    assert w.truth_panel.model.columnCount() == n_exh, \
        "清单说 %s 条、真值表画了 %s 列" % (n_exh, w.truth_panel.model.columnCount())

    others = {m["name"]: m["n_vectors"] for m in w.state.models()
              if m.get("form") not in (None, "", "select")}

    # ② 换成精简（第二次，且与第一次不同）—— 两边仍是同一个数
    cov.set_form_cov({"select": "min"})
    w.state.coverage_touched("topout")
    qapp.processEvents()
    assert H.wait_for(lambda: n_list(name) != n_exh, timeout_ms=15000), \
        "改了逻辑类型档，清单用例数一个没动（C-152）"
    _click_row(w, name)
    n_min = n_list(name)
    assert n_min < n_exh
    assert w.truth_panel.model.columnCount() == n_min
    # 别的逻辑类型一个都不许被顺手改档（per-form 就是「只动这一档」）
    after = {m["name"]: m["n_vectors"] for m in w.state.models()
             if m.get("form") not in (None, "", "select")}
    assert after == others, "改『选路』档把别的逻辑类型也顺手改了：%s → %s" % (others, after)


@pytest.mark.contract("C-043", "C-237")
def test_c043_c237_switching_tables_leaves_no_stale_truth_table(win, qapp, tmp_path):
    """换一张表之后，真值表 / 右栏**绝不**还画着上一张表那个信号的内容。

    v1 的这一句在 `test_topout_refresh_failure_clears_stale_panels`（分析失败清空旧表）；
    v2 里同一件事的用户路径是「换表」——`test_c043_analysis_failed_message` 验的是那句文案、
    `test_c236_c237_switch_table_isolates_edits` 验的是**编辑**不串表，谁都没验**屏幕上**的
    陈旧内容。留着旧表最坏的结果不是报错，是工程师对着上一张表的真值表核对新表。
    """
    import fixtures
    w = win
    _load_win(w)
    _click_row(w, LOGIC_SIG)
    n_cols = w.truth_panel.model.columnCount()
    assert n_cols > 0 and w.truth_panel.model.rowCount() > 0

    # ① 换到一张连 Topout 页都没有、信号名完全不同的表
    plain = tmp_path / "plain.xlsx"
    fixtures.build_workbook(str(plain), with_mux=True)
    ended = []
    w.analysisEnded.connect(lambda vid, ok: ended.append((vid, ok)))
    w.load_path(str(plain))
    assert H.wait_for(lambda: bool(ended))
    qapp.processEvents()
    assert LOGIC_SIG not in w.list_panel.visible_names()
    assert w.state.current_name != LOGIC_SIG, "换表后当前信号还停在上一张表的信号上"
    assert w.state.edits() == {}, "换表后编辑桶没清（C-237）"

    # ② 换回来 → 真值表重新长出来（清空不是「从此空着」）
    _load_win(w)
    _click_row(w, LOGIC_SIG)
    assert w.truth_panel.model.columnCount() == n_cols


# ═══════════ C-042：没有 Topout 页的表（老 test_topout_graceful_when_no_topout_page）═══════════
@pytest.mark.contract("C-042")
def test_c042_workbook_without_topout_page_falls_back_instead_of_blank(win, qapp, tmp_path):
    """一张**没有 Topout 页**的表：不崩、Topout 那一档如实置灰并写明原因、范围自动落到还有内容的页。

    v2 的 `test_c042_missing_page_disabled_with_label` 验的是 dft / iddq 这类**子页**缺失时
    置灰 + 标注；「连 Topout 页都没有」是 v1 `test_topout_graceful_when_no_topout_page` 那一条路。
    v2 在这一档上比 v1 强（v1 是空清单 + 一句提示，v2 直接把范围落到还有内容的页），
    但「置灰的那一段仍然点不动、换回真表照常出行」这两句此前没人验过。
    """
    import fixtures
    from dreg_verify.ui import terms
    w = win
    plain = tmp_path / "plain.xlsx"
    fixtures.build_workbook(str(plain), with_mux=True)

    # ① 没有 Topout 页 → 该档置灰并写明「本表无 topout 页」，范围自动落到还有内容的页
    ended = []
    w.analysisEnded.connect(lambda vid, ok: ended.append((vid, ok)))
    w.load_path(str(plain))
    assert H.wait_for(lambda: bool(ended))
    qapp.processEvents()
    assert w.state.models("topout") == [], "这张表本来就没有 Topout 页"
    seg = H.find(w.filter_bar, names.fmt_scope_btn("topout"))
    assert not seg.isEnabled()
    assert seg.toolTip() == terms.SCOPE_MISSING_FMT.format(page="topout")
    assert "topout" in w.filter_bar.missing_pages()
    assert w.filter_bar.scope() != "topout", "空页不该当成默认落点（那才是 v1 的空白表）"
    assert w.list_panel.proxy.rowCount() > 0, "落到的那一页要真有内容，否则还是空白表"
    assert w.isVisible()

    # ② 换回真镜像表 → Topout 那一档解禁、清单当场有那个 logic 根（第二次与第一次不同）
    _load_win(w)
    assert H.find(w.filter_bar, names.fmt_scope_btn("topout")).isEnabled()
    assert "topout" not in w.filter_bar.missing_pages()
    assert LOGIC_SIG in w.list_panel.visible_names()


# ═══════════ C-038：范围 = 页本地（老 test_pageviews_gui.py 的四页子视图）═══════════
def _set_scope(w, vid):
    """真点范围段（不直调 state.set_scope），并等这一趟分析跑完。"""
    ended = []
    w.analysisEnded.connect(lambda v, ok: ended.append((v, ok)))
    need = w.state.needs_analysis(vid)
    H.click(H.find(w.filter_bar, names.fmt_scope_btn(vid)))
    H.app().processEvents()
    if need:
        assert H.wait_for(lambda: bool(ended)), "切到 %s 之后 worker 没跑完" % vid
    H.app().processEvents()
    assert w.state.scope == vid
    return w.state.models(vid)


@pytest.mark.contract("C-038", "C-042")
def test_c038_page_scopes_list_their_own_rows_and_do_not_cone(win, qapp):
    """范围切到 logic / mux / dft → 清单就是**本页**那几行，真值表是**页本地**的（不展到源寄存器）。

    这是 v1 四个子视图（`test_pageviews_gui.py`）的全部意义，v2 把它们合成了筛选行的「范围」。
    v2 现有的 `test_c038_scope_switch_changes_state_scope` 只验了「点一下 state.scope 变了」，
    `test_c276_c265_load_skeleton_then_worker_upgrade` 只验了「切范围会跑一趟」——
    **换出来的行到底是不是那一页的**，以及**页本地不 cone**，此前没人验。
    接反了的表现是：在 logic 视图里看到的是全链展开的真值表，designer 照着它核对就是核对了别的东西。
    """
    w = win
    _load_win(w)
    wb = w.state.wb

    # ① 全链（topout）：rx_en 的真值表展到**源寄存器**叶子
    _click_row(w, LOGIC_SIG)
    top_inputs = [r["name"] for r in w.side_panel.inputs_view.model().rows()]
    assert "d_bt_lp_rx_en_local" in top_inputs, "Topout 范围本该展到源寄存器叶子"

    # ② logic 页：行数 = 本页条数；同一个信号的输入是**本行声明的那几个**，不展上游
    rows = _set_scope(w, "logic")
    assert len(rows) == len(wb.logic) > 0
    assert w.list_panel.proxy.rowCount() == len(wb.logic)
    _click_row(w, LOGIC_SIG)
    an = w.state.analyze(LOGIC_SIG, "logic")
    assert an["kind"] == "logic"
    page_inputs = [r["name"] for r in w.side_panel.inputs_view.model().rows()]
    assert len(page_inputs) == 4, "logic 页本地输入应当就是本行声明的 A/B/C/D：%s" % page_inputs
    assert w.truth_panel.model.rowCount() == len(page_inputs) + 2   # 输入 + auto_out + 期望
    assert w.truth_panel.model.columnCount() > 0

    # ③ mux 页：行数 = mux 页条数，选中的是 mux 根
    rows = _set_scope(w, "mux")
    assert len(rows) == len(wb.mux) >= 1
    assert w.state.analyze(rows[0]["name"], "mux")["kind"] == "mux"

    # ④ dft 页：单输入透传（1 输入 + auto + 期望 = 3 行）
    rows = _set_scope(w, "dft")
    assert len(rows) == len(wb.dft_rows) > 0
    _click_row(w, REG_SIG)
    assert len(w.side_panel.inputs_view.model().rows()) == 1
    assert w.truth_panel.model.rowCount() == 3

    # ⑤ iddq 页：本表该页是空的 → 那一段置灰并写明原因（不是一张空白表）
    assert "iddq" in w.filter_bar.missing_pages()
    assert not H.find(w.filter_bar, names.fmt_scope_btn("iddq")).isEnabled()


@pytest.mark.contract("C-038", "C-089")
def test_c038_c089_clearing_in_a_page_scope_only_drops_it_from_that_scope(win, qapp):
    """在 logic 范围里把一个信号清零 → **该范围**的 .sv 不再有它，Topout 范围那份一个字节不动。

    v1 `test_logic_subview_edit_and_export` 验的是「子视图里清零 → 子视图导出不含它」；
    v2 把子视图换成了范围，`test_c236_c237_switch_table_isolates_edits` 只验了**换表**的桶隔离，
    **同一张表里各范围之间**的编辑隔离没人验 —— 串了的话在 logic 视图里做的清零会把
    Topout 那份交付物也挖掉一块，而两处界面都不会提示。
    """
    w = win
    _load_win(w)
    top_before, _ = w.state.provider("topout").render_sv(
        None, "min", 256, False, w.state.compute_edited("topout"))
    assert ("`ENV_RF.%s==" % LOGIC_SIG) in top_before

    _set_scope(w, "logic")
    _click_row(w, LOGIC_SIG)
    tp = w.truth_panel
    assert tp.model.columnCount() > 0
    base, _ = w.state.provider("logic").render_sv(
        None, "min", 256, False, w.state.compute_edited("logic"))
    assert "assert (" in base and ("`ENV_RF.%s==" % LOGIC_SIG) in base

    # ① 真点工具条「清零…」（确认框由 auto_dialogs 答「是」）
    H.click(H.find(tp, names.TRUTH_BTN_CLEAR))
    qapp.processEvents()
    assert tp.model.columnCount() == 0
    after, _ = w.state.provider("logic").render_sv(
        None, "min", 256, False, w.state.compute_edited("logic"))
    assert ("`ENV_RF.%s==" % LOGIC_SIG) not in after
    assert after != base

    # ② Topout 那一份一个字节没变（范围之间的编辑不串）
    top_after, _ = w.state.provider("topout").render_sv(
        None, "min", 256, False, w.state.compute_edited("topout"))
    assert top_after == top_before, "logic 范围里的清零把 Topout 那份产物也改了"


@pytest.mark.contract("C-205")
def test_c205_changing_a_prefix_keeps_both_checks_and_negatives(st):
    """改探针前缀（配置）之后，勾选**和反例**都得原样留着。

    v2 的 `test_c205_config_change_keeps_checks_and_drops_cache` 只验了勾选那一半；
    v1 `test_gui_prefix_update_keeps_checked_signals` 连反例（`_neg_only`）一起验过。
    反例被重置的表现是「自检用例静默消失」——比勾选被重置更难发现。
    """
    names = [m["name"] for m in st.models("topout")]
    st.set_checked(names, False)
    st.set_checked([names[0], names[1]], True)
    st.set_neg(LOGIC_SIG, True)
    checks0, negs0 = set(st.checked("topout")), set(st.negs("topout"))
    assert checks0 and LOGIC_SIG.lower() in {n.lower() for n in negs0}

    # ① 改前缀
    st.set_probe_prefixes({"d_probe_demo": "U_TOP.U_SUB"})
    assert set(st.checked("topout")) == checks0
    assert set(st.negs("topout")) == negs0, "改前缀把反例重置了"

    # ② 再改一次（换成别的映射）——第二次与第一次不同，两样仍原封不动
    st.set_probe_prefixes({"d_probe_demo": "U_OTHER"})
    assert st.probe_prefixes == {"d_probe_demo": "U_OTHER"}
    assert set(st.checked("topout")) == checks0
    assert set(st.negs("topout")) == negs0


# ═══════════ C-043 / C-272：整表分析炸了（F2 已修，xfail 已摘）═══════════
@pytest.mark.contract("C-043", "C-272")
def test_c043_whole_table_analysis_failure_shows_a_human_message(win, qapp, monkeypatch):
    """整表分析炸了 → 详情区一行「分析失败（已捕获，未崩）」+ 原因全文，界面不裸露后端原文。"""
    import dreg_verify.topout as T
    from dreg_verify.ui import terms
    w = win

    def boom(*a, **k):
        raise RuntimeError("regmap 页缺 addr 列")

    monkeypatch.setattr(T, "topout_view_models", boom)
    monkeypatch.setattr(T, "resolve_root", boom)
    ended = []
    w.analysisEnded.connect(lambda vid, ok: ended.append((vid, ok)))
    w.load_path(H.mirror_path("btlp"))
    assert H.wait_for(lambda: bool(ended))
    qapp.processEvents()
    assert ended == [("topout", False)], "这一趟本该失败：%s" % (ended,)
    assert w.isVisible(), "不许崩"

    err = H.find(w, names.HDR_ERROR_LABEL)
    assert err.isVisible(), "分析整表炸了，详情区没有『分析失败』那一行"
    assert err.text().startswith(terms.HDR_ANALYSIS_FAILED)
    assert "addr" in err.text(), "原因全文要留住"

    # R3-13：错误条与状态栏说同一句人话，且原因不是后端异常原文（I-12 / C-272）
    want = terms.STATUS_ANALYZE_FAILED_FMT.format(
        reason=terms.scrub("regmap 页缺 addr 列"))
    assert H.find(w, names.ERROR_TEXT).text() == want
    assert H.find(w, names.STATUS_LEFT).text() == want
    assert "regmap" not in want, "术语没过 scrub"
