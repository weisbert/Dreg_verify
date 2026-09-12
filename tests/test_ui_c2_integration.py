# -*- coding: utf-8 -*-
"""GUI v2 C2-int：详情区四件接进组合根之后的**跨模块**行为。

单块的契约测试各自在 `test_ui_detail_header.py` / `test_ui_main_view.py` /
`test_ui_sv_preview.py` / `test_ui_side_panel.py` / `test_ui_sigflow_view.py`；
这里只放「拆开看谁都对、接起来才可能错」的七件事：

  · C-055/C-056/C-068/C-280  切信号 → 标题栏 / 右栏 / 电路图 / .sv 预览四件同步，
                             且一次点选**只跑一遍引擎**（四件各自要 an，不许各算各的）；
  · C-282                    bus 三方（链 / 输入表 / 电路图）互相高亮，origin 相同不回环；
  · C-276/C-280              骨架→全量升级时电路图从 `set_pending` 变真图；
  · C-261                    右栏收起 / 展开记 settings 的 `sideW`；
  · C-280/C-297              主视图独占中央区（电路图全屏 / 真值表放大）+ Esc 退出；
  · C-291                    预设定版三键读写 + 旧两键文件兼容；
  · C-194 / I-20             「名字在前、计数在后」。

用真表（mirror wl，9 行）+ 真 state + 真 worker；后台信号一律 `H.wait_for` 等，不写死毫秒。
夹具只用 `tests/` 里的 mirror 镜像表（公开仓，代码 / 测试 / 截图里绝不出现真实信号名）。
"""
import json
import os

import pytest

import ui_harness as H

pytest.importorskip("PySide6")

from PySide6 import QtWidgets                                  # noqa: E402

from dreg_verify.ui import bus as BUS                          # noqa: E402
from dreg_verify.ui import contracts, names, persist, terms    # noqa: E402
from dreg_verify.ui import app as A                            # noqa: E402
from dreg_verify.ui import dialogs as D                        # noqa: E402

LC = contracts.ListCol


@pytest.fixture
def qapp():
    return H.app()


@pytest.fixture
def win(qapp, monkeypatch, tmp_path):
    """真组合根 + 真 state + 真 worker，settings/edits 隔离到 tmp。"""
    H.isolate_settings(monkeypatch, tmp_path)
    H.auto_dialogs(monkeypatch)
    w = A.build_window([])
    w.resize(1600, 900)
    w.show()
    yield w
    w.close()
    qapp.processEvents()


def _load(w, kind="wl"):
    ended = []
    w.analysisEnded.connect(lambda vid, ok: ended.append((vid, ok)))
    w.load_path(H.mirror_path(kind))
    assert H.wait_for(lambda: bool(ended)), "worker 没跑完：%s" % (ended,)
    H.app().processEvents()
    return w.state.models()


def _with_graph(w):
    """挑一个真画得出图的信号（只读回读 / 未解析的没有图，验不到联动）。"""
    for m in w.state.models():
        an = w.state.analyze(m["name"], want_graph=True)
        if an and an.get("graph") is not None and an["graph"].edges:
            return m["name"]
    raise AssertionError("mirror 里没有画得出图的信号")


# ═══════════ ① C-055 / C-056 / C-068 / C-280：切信号 → 四件同步，引擎只跑一遍 ═══════════
def test_c055_c056_c068_c280_current_changed_syncs_four_widgets_with_one_analyze(win, qapp):
    w = win
    _load(w)
    name = _with_graph(w)
    w.state.set_current("")                 # 先清干净，四件都该是空态
    qapp.processEvents()
    assert H.find(w, names.HDR_NAME).text() == ""
    assert w.flow_view.canvas.graph is None
    assert w.side_panel.inputs_view.model().rowCount() == 0

    # 数引擎调用：四件都要 an，但同一个信号、同一份指纹，只该跑一遍
    prov = w.state.provider()
    calls = []
    real = prov.analyze

    def counting(*a, **k):
        calls.append((a[0], bool(k.get("want_graph"))))
        return real(*a, **k)

    prov.analyze = counting
    # `_with_graph` 刚才已经把这一条算过一遍且进了缓存 —— 把缓存清掉，
    # 测的才是「第一次点这个信号时四件一共招呼了几次引擎」
    w.state._an_cache.clear()
    w.state.set_current(name)
    qapp.processEvents()

    assert calls == [(name, True)], "一次点选跑了 %d 遍引擎：%s" % (len(calls), calls)

    # ④ 标题栏（C-055）
    assert H.find(w, names.HDR_NAME).text() == name
    assert H.find(w, names.HDR_STATUS_BADGE).text()
    assert H.find(w, names.HDR_META).text()
    # ⑨ 右栏（C-056）
    rows = w.side_panel.inputs_view.model().rows()
    assert rows and w.side_panel.inputs_title.text() == terms.SIDE_INPUTS_TITLE_FMT.format(n=len(rows))
    # ⑦ 电路图（C-280）
    assert w.flow_view.body.currentWidget() is w.flow_view.canvas
    assert w.flow_view.canvas.graph is not None
    # ⑧ .sv 预览（C-068）：跟着换了当前信号；内容等切到那一页再算（C-072 的脏标记）
    assert w.sv_preview.current() == name

    # 切回「没有当前信号」→ 四件各自回空态
    w.state.set_current("")
    qapp.processEvents()
    assert H.find(w, names.HDR_NAME).text() == ""
    assert w.flow_view.canvas.graph is None
    assert w.side_panel.inputs_view.model().rowCount() == 0
    assert terms.SIDE_EMPTY_NO_SIGNAL in w.side_panel.chain_view.toPlainText()


def test_c255_ctrl_p_switches_main_view_to_sv_tab(win, qapp):
    """C-255：Ctrl+P 切到 .sv 预览标签（快捷键接的是 `main_view.set_tab`，不是旧的 setCurrentIndex）。"""
    w = win
    _load(w)
    assert w.main_view.tab() == "truth"
    seen = []
    w.svPreviewRequested.connect(lambda: seen.append(1))
    w.shortcuts["sv_preview"].activated.emit()
    qapp.processEvents()
    assert w.main_view.tab() == "sv"
    assert H.find(w, names.MAIN_VIEW, QtWidgets.QStackedWidget).currentIndex() == 1
    assert len(seen) == 1, "按一次 Ctrl+P 发了 %d 次 svPreviewRequested" % len(seen)
    w.shortcuts["sv_preview"].activated.emit()      # 已经在这一页：仍算一次请求
    qapp.processEvents()
    assert len(seen) == 2 and w.main_view.tab() == "sv"


# ═══════════════ ② C-282：bus 三方高亮，origin 相同不回环 ═══════════════
def test_c282_bus_highlights_chain_inputs_and_flow_without_echo(win, qapp):
    w = win
    _load(w)
    name = _with_graph(w)
    w.state.set_current(name)
    qapp.processEvents()

    rows = w.side_panel.inputs_view.model().rows()
    graph = w.flow_view.canvas.graph
    net = next(r["name"] for r in rows
               if any(BUS.net_key(e.net) == BUS.net_key(r["name"]) for e in graph.edges if e.net))
    key = BUS.net_key(net)

    seen = []
    w.bus.netSelected.connect(lambda n, o: seen.append((n, o)))

    # 右栏输入表点一行（真点击）→ 广播一次，三方都亮
    idx = next(i for i, r in enumerate(rows) if r["name"] == net)
    H.click_cell(w.side_panel.inputs_view, 2 * idx, 1)
    qapp.processEvents()
    assert seen == [(key, "inputs")], "一次点击广播了 %d 次（回环了？）：%s" % (len(seen), seen)
    assert w.side_panel.inputs_view.model().highlight() == key
    assert w.side_panel.chain_view.highlight() == key
    assert w.flow_view.canvas.current_net == key

    # 电路图上点同一根网：再广播一次（origin 换成 flow）—— 但**只一次**。
    # 回环长什么样：收到广播的视图又去 `select` 一次，这里就会看到 3 条、4 条……
    w.flow_view._on_net_clicked(net)
    qapp.processEvents()
    assert seen == [(key, "inputs"), (key, "flow")], "有人收到广播后又回写了选择：%s" % (seen,)
    assert w.flow_view.canvas.current_net == key
    assert w.side_panel.inputs_view.model().highlight() == key

    # 清除高亮：三方一起灭
    w.bus.clear("flow")
    qapp.processEvents()
    assert seen[-1] == ("", "flow") and len(seen) == 3
    assert w.flow_view.canvas.current_net == ""
    assert w.side_panel.inputs_view.model().highlight() == ""


# ═══════════ ③ C-276 / C-280：骨架 → 全量升级时电路图从 pending 变真图 ═══════════
def test_c276_c280_flow_goes_from_pending_to_real_graph_on_model_update(win, qapp):
    """清单先出「分析中」N 行，用户当场点了其中一行 → 电路图先说「还在展开」；
    这一条算完（worker 的 `signalDone` → `state.update_model`）→ 当场变真图，不必等整表跑完。

    这里直接走 `update_model`（= `app._on_worker_signal_done` 做的那一件事），
    不依赖线程时序：要验的是「这一条升级了，图跟不跟」，不是 worker 本身。"""
    w = win
    _load(w)
    name = _with_graph(w)
    good = dict(w.state.model_of(name))
    vid = w.state.scope

    # 回到骨架态（全部「分析中」）
    skeleton = list(w.state.provider(vid).skeleton_models() or [])
    assert skeleton and {m["status"] for m in skeleton} == {"pending"}
    w.state.set_models(vid, skeleton, True)
    w.state.set_current(name)
    qapp.processEvents()

    assert w.state.model_of(name)["status"] == "pending"
    assert w.flow_view.body.currentWidget() is w.flow_view.empty
    assert w.flow_view.empty.text() == terms.FLOW_PENDING
    assert w.flow_view.canvas.graph is None

    # 这一条算完了 → 当场变真图
    w.state.update_model(vid, good)
    qapp.processEvents()
    assert w.flow_view.body.currentWidget() is w.flow_view.canvas
    assert w.flow_view.canvas.graph is not None and w.flow_view.canvas.graph.nodes


# ═══════════════ ④ C-261：右栏收起 / 展开记 sideW ═══════════════
def test_c261_side_toggle_hides_panel_and_remembers_side_width(win, qapp, tmp_path):
    w = win
    _load(w)
    assert w.side_panel.isVisible()
    toggle = H.find(w, names.HDR_SIDE_TOGGLE)
    assert toggle.text() == terms.HDR_SIDE_HIDE

    # 先拖出一个不是出厂值的宽度，再收起 —— 收起前必须把它记下来
    total = sum(w.split_side.sizes())
    want = 388
    w.split_side.setSizes([max(1, total - want), want])
    qapp.processEvents()

    H.click(toggle)                                   # 真点击「隐藏右栏 ▶」
    qapp.processEvents()
    assert not w.side_panel.isVisible()
    assert toggle.text() == terms.HDR_SIDE_SHOW
    got = w.preferred_sizes()["sideW"]
    assert abs(got - want) <= 4, "收起前没记住右栏宽度：%s" % got
    assert persist.load_settings().get("sideW") == got, "sideW 没落盘"

    H.click(toggle)                                   # 再点 → 展开，按记下的宽度恢复
    qapp.processEvents()
    assert w.side_panel.isVisible()
    assert toggle.text() == terms.HDR_SIDE_HIDE
    assert abs(w.split_side.sizes()[1] - got) <= 4


# ═══════════════ ⑤ C-280 / C-297：主视图独占中央区 + Esc 退出 ═══════════════
def test_c280_c297_fullscreen_takes_over_center_and_esc_exits(win, qapp):
    w = win
    _load(w)
    w.state.set_current(_with_graph(w))
    qapp.processEvents()
    assert not w.is_fullscreen()

    # ⑦ 电路图全屏（工具条按钮 → main_view → 组合根）
    H.click(H.find(w, names.FLOW_BTN_FULLSCREEN))
    qapp.processEvents()
    assert w.is_fullscreen() and w.main_view.flow_fullscreen()
    assert not w.list_panel.isVisible(), "全屏了清单还占着地方"
    assert not w.side_panel.isVisible()
    assert not w.detail_header.isVisible()
    assert H.find(w, names.FLOW_BTN_FULLSCREEN).text() == terms.FLOW_BTN_EXIT_FULLSCREEN

    w.esc_shortcut.activated.emit()                   # Esc
    qapp.processEvents()
    assert not w.is_fullscreen()
    assert w.list_panel.isVisible() and w.side_panel.isVisible() and w.detail_header.isVisible()
    assert H.find(w, names.FLOW_BTN_FULLSCREEN).text() == terms.FLOW_BTN_FULLSCREEN

    # ⑥ 真值表放大（C-297）：电路图由 MainView 收起、右栏由组合根收起，标题栏留着看进度
    w.main_view.set_truth_maximized(True)
    qapp.processEvents()
    assert w.is_fullscreen() and w.main_view.truth_maximized()
    assert not w.flow_view.isVisible() and not w.side_panel.isVisible()
    assert w.detail_header.isVisible()
    w.esc_shortcut.activated.emit()
    qapp.processEvents()
    assert not w.is_fullscreen() and w.side_panel.isVisible() and w.flow_view.isVisible()

    # 两种独占互斥：开了一个，另一个自动让位
    w.main_view.set_flow_fullscreen(True)
    w.main_view.set_truth_maximized(True)
    qapp.processEvents()
    assert w.main_view.truth_maximized() and not w.main_view.flow_fullscreen()
    assert w.exit_fullscreen() and not w.is_fullscreen()
    assert w.exit_fullscreen() is False, "没在独占态时 Esc 不该被吞掉"


# ═══════════════ ⑥ C-291：预设定版三键 + 旧两键文件兼容 ═══════════════
def test_c291_preset_three_keys_roundtrip_and_old_two_key_file(win, qapp, monkeypatch):
    w = win
    _load(w)
    vid = w.state.scope
    names_all = [m["name"] for m in w.state.models()]

    # 存：勾两个 + 改一档筛选 → 三键都进 settings（owners 落成可 JSON 化的 list）
    with w.state.suspend_persist():
        w.state.set_checked(names_all, False, vid)      # 空 list 什么都不动，要逐个点名
        w.state.set_checked(names_all[:2], True, vid)
    w.filter_bar.set_filters({"owners": set(), "kind": "", "status": "ok", "regex": "d_"})
    qapp.processEvents()

    H.auto_dialogs(monkeypatch, answers={D.TITLES["presets_save"]: "nightly"})
    assert w.save_preset() == "nightly"

    saved = persist.load_settings()[contracts.SETTINGS_PRESETS]["nightly"]
    assert set(saved) == set(persist.PRESET_KEYS) == {"scope", "checks", "filters"}
    assert saved["scope"] == vid
    assert sorted(saved["checks"]) == sorted(names_all[:2])
    assert saved["filters"]["status"] == "ok" and saved["filters"]["regex"] == "d_"
    json.dumps(saved)                                  # 必须能落 JSON（owners 不许是 set）

    # 取：范围 / 筛选 / 勾选三样一起回来
    with w.state.suspend_persist():
        w.state.set_checked(names_all, False, vid)
    w.filter_bar.reset_filters()
    qapp.processEvents()
    assert w.load_preset("nightly") == "nightly"
    qapp.processEvents()
    assert w.state.checked(vid) == set(names_all[:2])
    assert w.filter_bar.filters()["regex"] == "d_"

    # 旧的两键文件（没有 scope）：读得进、不报错、缺的键补默认
    old = dict(persist.load_settings()[contracts.SETTINGS_PRESETS])
    old["legacy"] = {"checks": [names_all[0]], "filters": {"kind": "mux"}}   # 两键，没有 scope
    persist.patch_settings({contracts.SETTINGS_PRESETS: old})
    got = w._presets()["legacy"]
    assert got == {"scope": "", "checks": [names_all[0]], "filters": {"kind": "mux"}}
    with w.state.suspend_persist():
        w.state.set_checked(names_all, False, vid)
    assert w.load_preset("legacy") == "legacy"
    qapp.processEvents()
    assert w.state.checked(vid) == {names_all[0]}
    assert w.filter_bar.filters()["kind"] == "mux"

    # 「管理预设」里删掉一条 → 当场落盘
    H.auto_dialogs(monkeypatch, answers={"PresetsDialog.ask_manage": ("", ("legacy",))})
    w.load_preset("")
    assert "legacy" not in persist.load_settings()[contracts.SETTINGS_PRESETS]
    assert "nightly" in persist.load_settings()[contracts.SETTINGS_PRESETS]


def test_c291_preset_with_owners_does_not_wipe_settings(win, qapp, monkeypatch):
    """回归：筛选里的 `owners` 是 set，直接塞进 settings 会让 `json.dump` 抛 —— 而写设置是
    「先截断文件再序列化、抛了还吞掉」，结果是整份 settings 被清空（存一次预设，用户全部偏好没）。"""
    w = win
    _load(w)
    owners = sorted({(m.get("owner") or "") for m in w.state.models() if m.get("owner")})
    assert owners, "mirror 里没有 owner，验不到东西"
    w.filter_bar.set_filters({"owners": set(owners), "kind": "", "status": "", "regex": ""})
    qapp.processEvents()

    before = dict(persist.load_settings())
    assert before, "还没写过 settings，验不到「被清空」"
    H.auto_dialogs(monkeypatch, answers={D.TITLES["presets_save"]: "by_owner"})
    assert w.save_preset() == "by_owner"

    after = persist.load_settings()
    assert after, "存预设把整份 settings 清空了"
    for k, v in before.items():
        if k != contracts.SETTINGS_PRESETS:
            assert after.get(k) == v, "存预设把 %s 弄丢了" % k
    assert after[contracts.SETTINGS_PRESETS]["by_owner"]["filters"]["owners"] == owners


# ═══════════════ ⑦ C-194 / I-20：名字在前、计数在后 ═══════════════
def test_c194_export_import_missing_names_come_before_the_count():
    """I-20 主控裁决：跳过 / 找不到的东西一律**先点名、计数放后面**。

    「（3 个）：a、b、c」先给一个数字，再让人自己往后找是哪三个；
    「a、b、c（共 3 个…）」一眼就是名字。同一条模板全仓只有一份，这里锁住它的顺序。"""
    txt = terms.EXPORT_IMPORT_MISSING_FMT.format(n=3, names="sig_a、sig_b、sig_c")
    assert "sig_a" in txt and "3" in txt
    assert txt.index("sig_a") < txt.index("3"), "计数跑到名字前面去了：%s" % txt
    # 对话框那一份（C-194 的可见落点）同样是点名块在前、计数在后
    assert terms.DLG_IMPORT_MISSING_HEAD and terms.DLG_IMPORT_MISSING_COUNT_FMT.format(n=3)


def test_c194_import_report_dialog_puts_names_above_the_count(qapp, monkeypatch):
    """同一条规矩在对话框上的落点：点名列表的位置必须在计数之上。"""
    H.auto_dialogs(monkeypatch, answers={"ask": H.REAL})
    dlg = D.ImportReportDialog(missing=[("sig_a", "当前表里没有"), ("sig_b", "当前表里没有")],
                               counts=["勾选 2 个"], notes=[])
    try:
        head = H.find(dlg, names.DLG_IMPORT_REPORT_HEAD)
        lst = H.find(dlg, names.DLG_IMPORT_REPORT_LIST)
        cnt = H.find(dlg, names.DLG_IMPORT_REPORT_COUNT)
        assert dlg.lay.indexOf(head) < dlg.lay.indexOf(lst) < dlg.lay.indexOf(cnt)
        assert "sig_a" in lst.item(0).text()
        assert cnt.text() == terms.DLG_IMPORT_MISSING_COUNT_FMT.format(n=2)
    finally:
        dlg.deleteLater()


# ═══════════════ 接线总账：四件真的在窗口里，占位只剩真值表 ═══════════════
def test_c2_areas_are_real_widgets_now(win, qapp):
    """C2-int 的验收线：四块真件都在窗口里，`PLACEHOLDER_AREAS` 只剩真值表（C3）。"""
    from dreg_verify.ui.detail_header import DetailHeader
    from dreg_verify.ui.main_view import MainView
    from dreg_verify.ui.side_panel import SidePanel
    from dreg_verify.ui.sigflow_view import SigflowView
    from dreg_verify.ui.sv_preview import SvPreview

    w = win
    _load(w)
    assert isinstance(H.find(w, names.HDR_BAR), DetailHeader)
    assert isinstance(w.main_view, MainView)
    assert isinstance(H.find(w, names.SIDE_PANEL), SidePanel)
    assert isinstance(H.find(w, names.FLOW_PANEL), SigflowView)
    assert isinstance(H.find(w, names.SV_PANEL), SvPreview)
    assert w.coverage is w.detail_header.cov, "window.coverage 没指向标题栏里那一个"
    assert H.find(w, names.HDR_COV_BTN) is w.coverage.button

    # C3-int 之后 `PLACEHOLDER_AREAS` 空了（真值表也换成真件）——本条只守 C2 那四块
    assert names.TRUTH_PANEL not in A.PLACEHOLDER_AREAS
    placeholders = {x.objectName() for x in w.findChildren(QtWidgets.QWidget)
                    if x.property("placeholder")}
    assert not (placeholders & {names.HDR_BAR, names.FLOW_PANEL, names.SV_PANEL,
                                names.SIDE_PANEL, names.MAIN_VIEW_PANEL}),         "C2 的四块里还有占位没换：%s" % sorted(placeholders)
    assert os.path.exists(H.shot(w, "c2_wired_workbench"))
