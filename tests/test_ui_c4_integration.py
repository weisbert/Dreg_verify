# -*- coding: utf-8 -*-
"""GUI v2 C4-int：⑪ 导出中心 + ⑬ 诊断抽屉接进组合根之后的**跨模块**行为。

单块的契约测试各自在 `test_ui_export_center.py` / `test_ui_diagnostics.py`；这里只放
「拆开看谁都对、接起来才可能错」的那些：

  · C-256 / C-257     —— Ctrl+R 预选报告行、Ctrl+G 六行默认；两条快捷键都真开出框来；
  · C-168             —— 完成弹层关掉之后主视图切 .sv 预览（断开这根线当场红）；
  · C-167             —— 「去处理这 N 个信号」→ ⑬ 抽屉停在「找不到网」那一条；
  · 行内原因块三路     —— diag_cuvunf / diag_risky / export_nets 各自落到对的地方；
  · 抽屉第 1 步        —— `exportNetsRequested` → 导出中心预勾 nets 行（不在抽屉里重做一套导出）；
  · 折叠项 ③          —— `coverageRequested` → ⑯ 覆盖度弹层（用标题栏那一个，不另起一个）；
  · Esc 的优先级       —— 先收抽屉，再退出独占态；两样都没有时**不吞** Esc；
  · C-192             —— 空态「导入配置…」= 打开导出中心并直接进导入流程；
  · C-197 / C-198     —— 跑完一趟导出，状态栏右侧与导出中心的「上次导出到哪」当场翻新；
  · `reset_config_state` —— 三套诊断配置 + include_risky + 覆盖度 + 编辑一次清干净（C-192 的前半）；
  · 两个模块测试里不再有本地对话框打桩（都提到 `ui_harness` 了）；
  · I-14              —— 把 ⑪⑫⑬ 与四个编辑器都打开之后，`names.py` 里 C4 那几片名字全都 find 得到。

口径与 C1/C2/C3 三份集成测试一致：真组合根 + 真 state（→ 真 provider / 真引擎）+ 真 worker，
夹具只用 `tests/` 里的两张 mirror 镜像表（公开仓，**绝不出现真实信号名**）。
模态框一律由 `ui_harness.auto_dialogs` 拦下 —— offscreen 下不拦不是红，是整条用例挂住。
"""
import io
import os
import re

import pytest

import ui_harness as H

pytest.importorskip("PySide6")

from PySide6 import QtCore, QtWidgets                          # noqa: E402

from dreg_verify import exports as X                           # noqa: E402
from dreg_verify import session as S                           # noqa: E402
from dreg_verify.ui import app as A                            # noqa: E402
from dreg_verify.ui import contracts, names, terms             # noqa: E402
from dreg_verify.ui import diagnostics as DG                   # noqa: E402
from dreg_verify.ui import export_center as EC                 # noqa: E402
from dreg_verify.ui import state as ST                         # noqa: E402

#: ⑤⑥ 与本文件用 btlp 镜像：它有一个 RO 回读根按设计不产断言 → 跳过块 / 「去处理」按钮真的会出现
SKIP_KIND = "btlp"


@pytest.fixture
def qapp():
    return H.app()


@pytest.fixture
def rec(monkeypatch, tmp_path):
    """所有模态入口拦截；「另存为」落到 tmp/save（导出一路跑完并真写文件）。"""
    d = tmp_path / "save"
    d.mkdir()
    return H.auto_dialogs(monkeypatch, save_dir=str(d))


@pytest.fixture
def win(qapp, monkeypatch, tmp_path, rec):
    """真组合根 + 真 state + 真 worker，settings/edits 隔离到 tmp。"""
    H.isolate_settings(monkeypatch, tmp_path)
    w = A.build_window([])
    w.resize(1600, 900)
    w.show()
    yield w
    w.close()
    qapp.processEvents()


def _load(w, kind=SKIP_KIND):
    """载表并等这一趟分析跑完。"""
    ended = []
    w.analysisEnded.connect(lambda vid, ok: ended.append((vid, ok)))
    w.load_path(H.mirror_path(kind))
    assert H.wait_for(lambda: bool(ended)), "worker 没跑完：%s" % (ended,)
    return w.state.models()


def _enabled_kinds(dlg):
    return [r.kind for r in dlg.rows() if r.enabled]


def _run_sv_export(w, qapp):
    """打开导出中心、只勾 .sv、跑一趟。→ ExportRunResult。"""
    w.openExportCenter("")
    qapp.processEvents()
    d = w.export_center
    for k, cb in d.checks.items():
        cb.setChecked(k == "sv")
    res = d.run()
    qapp.processEvents()
    return res


# ═══════════ ① C-256 / C-257：两条快捷键都真把导出中心开出来 ═══════════
@pytest.mark.contract("C-256", "C-257", "C-197")
def test_c4_export_center_reachable_by_ctrl_g_and_ctrl_r(win, qapp):
    """Ctrl+G = 六行按默认勾选；Ctrl+R = **只勾报告行**（C-256）。两条都得真开出框来。

    C4-int 之前 `openExportCenter` 只发一个信号 —— 「信号发了」与「框开了」是两件事，
    这条用例盯的就是后者（`w.export_center` 是不是真造出来了、预选对不对）。"""
    _load(win)
    assert win.export_center is None, "还没按过，不该先有框"

    win.shortcuts["export_center"].activated.emit()          # Ctrl+G
    qapp.processEvents()
    d = win.export_center
    assert d is not None and d.objectName() == names.EXPORT_DIALOG
    assert _enabled_kinds(d) == ["sv", "report", "claims"]   # Design ER 数组 [0,1,4]
    assert H.find(d, names.EXPORT_TABLE).rowCount() == 6
    assert H.find(win, names.STATUS_LEFT).text() == terms.EXPORT_TITLE

    win.shortcuts["export_report"].activated.emit()          # Ctrl+R（C-256）
    qapp.processEvents()
    d2 = win.export_center
    assert d2 is not d, "Ctrl+R 复用了上一次那个框（预选就套不上了）"
    assert _enabled_kinds(d2) == ["report"]
    assert H.find(win, names.STATUS_LEFT).text() == "%s · %s" % (
        terms.EXPORT_TITLE, terms.EXPORT_ROWS["report"][0])
    # 顶栏按钮走同一个口
    H.click(H.find(win, names.TOP_EXPORT_BTN))
    qapp.processEvents()
    assert _enabled_kinds(win.export_center) == ["sv", "report", "claims"]


# ═══════════ ② C-168：完成弹层关掉之后切 .sv 预览 ═══════════
@pytest.mark.contract("C-168")
def test_c168_done_dialog_then_sv_preview_tab(win, qapp):
    """导出完 .sv → 完成弹层 → 关掉 → 主视图停在 .sv 预览标签。

    ⚠ 变异验证（人工跑过）：把 `app.openExportCenter` 里
    `dlg.svPreviewRequested.connect(self.on_sv_preview)` 那一行注释掉，本条当场红
    （`tab()` 停在 "truth"）—— 这根线断了在界面上的表现只是「导完之后没跳页」，
    没有任何报错，只有这条断言看得见。"""
    _load(win)
    assert win.main_view.tab() == "truth"
    seen = []
    win.svPreviewRequested.connect(lambda: seen.append(1))

    res = _run_sv_export(win, qapp)
    assert res is not None and [o.kind for o in res.outcomes] == ["sv"]
    assert win.main_view.tab() == "sv", "完成弹层关掉之后没切到 .sv 预览（C-168）"
    assert seen, "svPreviewRequested 一次都没到组合根"
    # .sv 预览页真的在前台，且内容不是空的
    assert H.find(win, names.MAIN_VIEW).currentIndex() == 1
    assert H.find(win, names.SV_TEXT).toPlainText().strip()


# ═══════════ ③ C-167：「去处理这 N 个信号」→ 抽屉停在「找不到网」 ═══════════
@pytest.mark.contract("C-167")
def test_c4_go_fix_opens_drawer_at_cuvunf(win, qapp):
    """完成弹层的「去处理这 N 个信号」→ ⑬ 抽屉打开并滚到主症状蓝框。

    ⚠ 变异验证（人工跑过）：把 `app.openExportCenter` 里
    `dlg.goFixRequested.connect(self.openDiagnostics)` 注释掉，本条当场红
    （抽屉不出现）—— 界面上的表现是「点了按钮什么都没发生」，那种 bug 只有这里抓得住。"""
    _load(win)
    res = _run_sv_export(win, qapp)
    assert res.skipped, "这一趟一条都没跳过，「去处理」按钮就不会出现"
    done = win.export_center.done_dialog
    assert done is not None, "跑完没造出完成弹层"
    assert done.go_fix_btn.text() == terms.DONE_BTN_GO_FIX_FMT.format(n=len(res.skipped))
    assert not win.diag_drawer.isVisible()

    H.click(done.go_fix_btn)
    qapp.processEvents()
    assert win.diag_drawer.isVisible()
    assert win.diag_drawer.items[names.DIAG_SYM_RISKY].is_expanded() is False
    assert H.find(win.diag_drawer, names.DIAG_CUVUNF_BOX) is not None
    assert H.find(win, names.STATUS_LEFT).text() == terms.REASON_TARGETS[EC.GO_FIX_SYMPTOM]


# ═══════════ ④ 行内原因块三种 target 各一 ═══════════
@pytest.mark.contract("C-015", "C-016")
def test_c4_reason_block_go_diag_opens_drawer_at_symptom(win, qapp):
    """清单行内原因块的「去诊断 · …」三种落点各验一条（`terms.REASON_TARGETS` 的键）：

      diag_cuvunf → ⑬ 抽屉主症状蓝框     diag_risky → ⑬ 抽屉折叠项 ④（展开）
      export_nets → ⑪ 导出中心并**只勾** nets 行

    （真按钮点击那一路在 `test_ui_scenes.test_scene_02_list_reason_expand` 里，
     这里验的是同一个路由在**三种 target 上都落对了地方**。）"""
    _load(win)
    dr = win.diag_drawer

    assert win.route_reason_action("diag_cuvunf") == "diag_cuvunf"
    qapp.processEvents()
    assert dr.isVisible()
    assert H.find(dr, names.DIAG_CUVUNF_BOX) is not None
    assert not dr.items[names.DIAG_SYM_RISKY].is_expanded()

    assert win.route_reason_action("diag_risky") == "diag_risky"
    qapp.processEvents()
    assert dr.isVisible() and dr.items[names.DIAG_SYM_RISKY].is_expanded(), \
        "diag_risky 没把「缺前缀是否强制生成」那一条展开"

    dr.close_drawer()
    assert win.route_reason_action("export_nets") == "nets"
    qapp.processEvents()
    assert _enabled_kinds(win.export_center) == ["nets"]
    # 三种 target 都在 REASON_TARGETS 里（不是随手编的字符串）
    for t in ("diag_cuvunf", "diag_risky", "export_nets"):
        assert t in terms.REASON_TARGETS


# ═══════════ ⑤ 抽屉第 1 步 → 导出中心预勾 nets ═══════════
@pytest.mark.contract("C-185", "C-189")
def test_c4_step1_opens_export_center_preselect_nets(win, qapp):
    """⑬ 第 1 步「导出 nets.txt…」不在抽屉里重做一套导出，而是打开 ⑪ 并**只勾** nets 行。"""
    _load(win)
    win.openDiagnostics("")
    qapp.processEvents()
    assert win.diag_drawer.isVisible()

    H.click(H.find(win.diag_drawer, names.DIAG_STEP1_BTN))
    qapp.processEvents()
    d = win.export_center
    assert d is not None and _enabled_kinds(d) == ["nets"]
    assert H.find(win, names.STATUS_LEFT).text() == "%s · %s" % (
        terms.EXPORT_TITLE, terms.EXPORT_ROWS["nets"][0])


# ═══════════ ⑥ 折叠项 ③ → 覆盖度弹层 ═══════════
@pytest.mark.contract("C-142", "C-156")
def test_c4_coverage_requested_opens_popover(win, qapp):
    """⑬ 折叠项 ③「打开覆盖度设置…」→ 打开的是**标题栏④ 里那一个** `CoverageControl` 的弹层，
    不是另起一套（两套档位控件 = 用户改了一处、清单按另一处重算）。"""
    _load(win)
    assert win.list_panel.select_first_visible()
    qapp.processEvents()
    pop = H.find(win, names.COV_POPOVER)
    assert not pop.isVisible()

    win.openDiagnostics("diag_coverage")
    qapp.processEvents()
    assert win.diag_drawer.items[names.DIAG_SYM_COVERAGE].is_expanded()
    H.click(H.find(win.diag_drawer, names.DIAG_COVERAGE_BTN))
    qapp.processEvents()
    assert pop.isVisible(), "折叠项 ③ 没把覆盖度弹层打开"
    assert H.find(win, names.COV_POPOVER) is pop, "开的不是标题栏那一个覆盖度控件"

    # 空态（还没载表）时标题栏不可见 → 不弹一个飘在角落里的空弹层，只在状态栏说一句
    # ⚠ 用 `MainWindow()` 不用 `build_window([])`：后者会走 `apply_startup`，而本条用例前面
    #   刚载过表、settings 里已经有 `last_excel` —— 那会直接把表载进来，就不是空态了。
    w2 = A.MainWindow()
    assert w2.is_empty_state()
    assert w2.open_coverage() is False
    w2.close()


# ═══════════ ⑦ Esc 的优先级 ═══════════
@pytest.mark.contract("C-280", "C-297")
def test_c4_esc_closes_drawer_before_fullscreen(win, qapp):
    """Esc：**先收抽屉，再退出独占态**；两样都没有时不吞 Esc（返回 False）。

    次序有意义：抽屉是盖在最上面的那一层，用户按 Esc 想关的是刚打开的那个东西。
    反过来先退全屏，屏幕上抽屉纹丝不动、底下的版面却换了一副样子。"""
    _load(win)
    assert win.on_escape() is False, "两样都没有时 Esc 不该被吞掉"

    win.main_view.set_flow_fullscreen(True)                  # C-280
    qapp.processEvents()
    assert win.is_fullscreen()
    win.openDiagnostics("")
    qapp.processEvents()
    assert win.diag_drawer.isVisible() and win.is_fullscreen()

    assert win.on_escape() is True                           # ① 先收抽屉
    assert not win.diag_drawer.isVisible()
    assert win.is_fullscreen(), "第一下 Esc 把全屏也退了（抽屉与全屏一次退两样）"

    assert win.on_escape() is True                           # ② 再退全屏
    assert not win.is_fullscreen()
    assert win.on_escape() is False

    # 真值表放大（C-297）也是同一条优先级
    win.main_view.set_truth_maximized(True)
    win.openDiagnostics("")
    qapp.processEvents()
    assert win.on_escape() is True and win.is_fullscreen()
    assert win.on_escape() is True and not win.is_fullscreen()


# ═══════════ ⑧ C-192：空态「导入配置…」 ═══════════
@pytest.mark.contract("C-192")
def test_c192_empty_state_import_config_opens_center_config(win, qapp, rec):
    """⑭ 空态的「导入配置…」= 打开 ⑪ 并**直接进导入流程**（preselect="config"）。

    还没载表就能把同事那份配置拿进来，是 C-192 的一半；另一半（真恢复出编辑）在
    `test_ui_export_center.py::test_c190_c191_c192_..._config_roundtrip`。"""
    assert win.is_empty_state()
    H.click(H.find(win, names.EMPTY_BTN_IMPORT_CONFIG))
    qapp.processEvents()
    d = win.export_center
    assert d is not None
    # `"config"` 也是 `EXPORT_KINDS` 的一员 → 与别的 preselect 同一套语义：**只勾那一行**
    assert _enabled_kinds(d) == ["config"]
    assert d._auto_import is True, "preselect='config' 还要额外「开完直接进导入流程」"

    # harness 把 `exec` 换成了立刻返回 → showEvent 没跑过；这里真 show 一次让那一枪打出来
    n0 = rec.count("QFileDialog.getOpenFileName")
    d.show()
    qapp.processEvents()
    qapp.processEvents()                                     # singleShot(0, on_import_config)
    assert rec.count("QFileDialog.getOpenFileName") == n0 + 1, "没有直接进导入配置"
    d.close()


# ═══════════ ⑨ C-197 / C-198：「上次导出到哪」当场翻新 ═══════════
@pytest.mark.contract("C-197", "C-198")
def test_c197_c198_last_export_column_refreshes_after_run(win, qapp):
    """跑完一趟导出：状态栏右侧与导出中心那一列**都**从「从未导出」变成刚写出的路径。

    两处是同一份 `state.last_export(kind)`；不刷新的表现是「导完了界面还说从未导出」，
    下一次用户就会照着那句话再导一遍。"""
    _load(win)
    assert H.find(win, names.STATUS_RIGHT).text() == ""
    win.openExportCenter("")
    qapp.processEvents()
    assert H.find(win.export_center, names.fmt_export_last("sv")).text() == terms.EXPORT_LAST_NEVER

    res = _run_sv_export(win, qapp)
    path = res.outcomes[0].path
    right = H.find(win, names.STATUS_RIGHT).text()
    assert path in right and terms.EXPORT_ROWS["sv"][0] in right, right

    win.openExportCenter("")                                 # 再开一次：那一列已经是新的
    qapp.processEvents()
    assert path in H.find(win.export_center, names.fmt_export_last("sv")).text()


# ═══════════ ⑩ state.reset_config_state ═══════════
def test_c4_reset_config_state_clears_all_three_and_risky_true(qapp, monkeypatch, tmp_path):
    """C-192 的前半：导入【完整配置】前把可编辑状态清回出厂态。

    清三套诊断配置 + `include_risky=True`（I-11）+ 覆盖度三层回默认 + 各范围的编辑 / 勾选；
    **不碰**已载入的 wb / provider / 清单模型（换的是工作状态，不是表）。"""
    H.isolate_settings(monkeypatch, tmp_path)
    H.auto_dialogs(monkeypatch)
    st = ST.WorkbenchState()
    assert st.load(H.mirror_path(SKIP_KIND))
    st.set_models("topout", st.provider("topout").skeleton_models(), partial=True)
    models = st.models("topout")
    name = models[0]["name"]

    # 先把每一样都弄成「非出厂态」
    st.set_probe_prefixes({"pll_n": "U_BT_LP_PLL_DIG"})
    st.set_force_signals({"d_bt_lp_rx_en"})
    st.set_logic_overrides({"d_x": {"enabled": True, "expr": "a", "inputs": [{"var": "a", "raw": "n"}]}})
    st.set_include_risky(False)
    st.coverage("topout").persist_global_label("精简")
    st.coverage("topout").set_form_cov({"boolean": "min"})
    st.coverage("topout").sig_cov[name.lower()] = "min"
    st.set_checked([m["name"] for m in models], False, "topout")
    st.set_checked([name], True, "topout")
    assert st.checked("topout") is not None

    fired = []
    st.configChanged.connect(fired.append)
    wb_before, prov_before = st.wb, st.provider("topout")

    assert st.reset_config_state() is True

    assert st.probe_prefixes == {} and st.force_signals == set() and st.logic_overrides == {}
    assert st.include_risky is True                          # I-11 出厂默认
    cov = st.coverage("topout")
    assert cov.global_label == S.DEFAULT_COV_LABEL and cov.max_tests == S.DEFAULT_MAX_TESTS
    assert cov.form_cov == {} and cov.sig_cov == {}
    assert st.checked("topout") is None, "勾选该回到「全勾 = 默认态」"
    assert st.edits("topout") == {} and st.mux_data("topout") == {}
    # 四个 set_* 各发一次 configChanged（清单据此翻指纹重跑）
    assert set(fired) == {"probe_prefixes", "force_signals", "logic_overrides", "include_risky"}
    # 表本身一动没动
    assert st.wb is wb_before and st.provider("topout") is prov_before
    assert len(st.models("topout")) == len(models)

    # 模块层的编排口就是它（C4-a 那条 `getattr` 回退已删）
    assert EC.reset_config_state(st) is True
    src = io.open(EC.__file__, encoding="utf-8").read()
    assert 'getattr(state, "reset_config_state"' not in src


# ═══════════ ⑪ 模块测试里不再有本地对话框打桩 ═══════════
#: harness 已经拦下的模态入口（类名 / 方法名）—— 测试文件里再本地 monkeypatch 一遍，
#: 就是同一个框两处各拦一套答案，改了一处两边说的话就不一样了。
_HARNESS_COVERED = (
    {cls for _mod, cls in ((e[0], e[1]) for e in H.V2_DIALOGS)}
    | {cls for _mod, cls, _meth, _fn in H.CUSTOM_MODALS}
    | {meth for _mod, _cls, meth, _fn in H.CUSTOM_MODALS}
    | {cls for cls, _meth in H.STATIC_MODALS}
    | {meth for _cls, meth in H.STATIC_MODALS}
)
_SETATTR_RE = re.compile(r"monkeypatch\.setattr\(\s*([^,]+),\s*[\"']([A-Za-z_]+)[\"']")


def _local_dialog_stubs(path):
    """这个测试文件里有没有「harness 已经管了、却又自己 monkeypatch 一遍」的对话框入口。"""
    bad = []
    for target, attr in _SETATTR_RE.findall(io.open(path, encoding="utf-8").read()):
        tail = target.strip().rsplit(".", 1)[-1]
        if tail in _HARNESS_COVERED or attr in _HARNESS_COVERED:
            bad.append("%s.%s" % (target.strip(), attr))
    return bad


def test_c4_no_stub_dialog_left_in_module_tests():
    """C4 两个模块的测试文件都改用 `ui_harness` 的答案，不再自己拦对话框。"""
    here = os.path.dirname(os.path.abspath(__file__))
    # 先证明这条扫描扫得到东西（搜索返回空时，第一件事是验证搜索本身能命中已知靶子）
    probe = os.path.join(here, "_probe_stub_scan.py")
    io.open(probe, "w", encoding="utf-8").write(
        'monkeypatch.setattr(DLG.DupLabelsDialog, "ask", lambda *a: False)\n')
    try:
        assert _local_dialog_stubs(probe) == ["DLG.DupLabelsDialog.ask"]
    finally:
        os.remove(probe)

    for fn in ("test_ui_export_center.py", "test_ui_diagnostics.py"):
        bad = _local_dialog_stubs(os.path.join(here, fn))
        assert not bad, "%s 里还留着本地对话框打桩（该走 auto_dialogs 的 answers）：%s" % (fn, bad)
    # 反过来：这六个框现在确实归 harness 管
    assert {"ExportCenterDialog", "ExportDoneDialog", "PrefixEditorDialog", "ForceEditorDialog",
            "SupplementEditorDialog", "LegacyImportDialog"} <= {e[1] for e in H.V2_DIALOGS}


# ═══════════ ⑫ I-14：把 C4 的框都打开之后，名字全都 find 得到 ═══════════
def test_i14_all_names_reachable_with_dialogs_open(win, qapp):
    """I-14：⑪⑫⑬ 与四个编辑器都打开之后，`names.py` 里 `export_` / `done_` / `diag_`
    三片名字**一个不少**都能按 objectName 找到；两个模块的 PENDING 表已经搬空。

    （全窗口范围的那条在 `test_ui_c1_integration.test_ui_names_all_present`；
     这里是 C4 两片的专项，附带守「PENDING 表真的删了」。）"""
    _load(win)
    alive = []
    ec = EC.ExportCenterDialog(win.state, win)
    ec.open_options("nets")                                  # export_nets_more_btn 在 nets 弹层里
    alive.append(ec)
    alive.append(EC.ExportDoneDialog(contracts.ExportRunResult(
        skipped=[("d_fake", "输入缺层级前缀")], accounted=["d_ro"],
        errors=[("sv", "写不出去")]), win))
    win.diag_drawer.open_for("")
    for cls in (DG.PrefixEditorDialog, DG.ForceEditorDialog,
                DG.SupplementEditorDialog, DG.LegacyImportDialog):
        alive.append(cls(win.state, win))
    qapp.processEvents()

    want = {k: v for k, v in names.all_names().items()
            if v.startswith(("export_", "done_", "diag_"))
            and k not in ("TOP_DIAG_BTN", "TOP_EXPORT_BTN", "LIST_REASON_DIAG_BTN",
                          "HDR_RESOLVE_DIAG_BTN")}
    assert len(want) >= 60, "C4 两片的名字只剩 %d 个了" % len(want)
    assert H._find_opt(win, "no_such_object_name_xyz") is None     # 先证明这条查找能判「没有」
    missing = sorted("%s=%s" % (k, v) for k, v in want.items() if H._find_opt(win, v) is None)
    assert not missing, "这些 objectName 打开对应的框之后仍然找不到：%s" % missing

    # 两个模块的 PENDING 表与 getattr 回退都已经删干净（留着就看不出名字搬没搬过去）
    for mod in (EC, DG):
        for attr in ("PENDING_NAMES", "PENDING_TERMS", "_n", "_nm"):
            assert not hasattr(mod, attr), "%s 还留着 %s" % (mod.__name__, attr)
    assert DG._t("DIAG_SAVE") == terms.DIAG_SAVE
    with pytest.raises(KeyError):
        DG._t("NO_SUCH_TERM_XYZ")                            # 回退删掉了：缺文案当场炸
    for dlg in alive:
        dlg.close()


# ═══════════ 附：电路图 SVG/PNG 不进「上次导出」（主控裁决）═══════════
@pytest.mark.contract("C-284")
def test_c284_flow_export_does_not_record_last_export(win, qapp, tmp_path):
    """⑦ 电路图导出 SVG / PNG 只写状态栏，**不进** `state.record_export`。

    主控裁决：它不是六种交付物之一（`contracts.EXPORT_KINDS` 没有这一档）。记进去的话，
    状态栏右侧那句「上次导出 …」会指向一张图片 —— 而那一格说的是「上次交出去的产物在哪」。"""
    _load(win)
    assert H.find(win, names.STATUS_RIGHT).text() == ""
    p = str(tmp_path / "flow.svg")
    win._on_flow_exported(p)
    qapp.processEvents()
    assert H.find(win, names.STATUS_LEFT).text() == p
    assert H.find(win, names.STATUS_RIGHT).text() == "", "电路图的图进了「上次导出」"
    assert all(win.state.last_export(k) is None for k in contracts.EXPORT_KINDS)
