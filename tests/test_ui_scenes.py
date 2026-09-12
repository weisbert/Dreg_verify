# -*- coding: utf-8 -*-
"""GUI v2「8 个 Design 场景」= 8 组截图断言（架构文档 §5）。

每条既截图（`refactor_notes/gui_v2_shots/scene_NN.png`，目录已 gitignore）也断文本 ——
offscreen 缺中文字体，**版面靠截图过目、语义靠 `widget.text()` 断言**。

C1 落 ① 空态 / 载表、② 清单·原因展开、⑧ 载入 200+ 进行中；
③④⑤⑥⑦ 由 C2-int / C3-int / C4-int 各自接手（下面留桩，别删）。

C1-int 起，①②⑧ 都走**真工厂**：`ui.app.MainWindow()` 默认造真 `WorkbenchState`
（→ 真 `providers.TopoutProvider` / `PageProvider`）与真 `AnalysisWorker`（真 QThread）。
① 另留一版 FakeState 的（`..._with_fake_state`）—— 那条验的是「app 不认得 state 是真是假」。
"""
import re

import pytest

import ui_harness as H

pytest.importorskip("PySide6")

from PySide6 import QtWidgets                                   # noqa: E402

import ui_fakes as F                                            # noqa: E402
from dreg_verify.ui import bus as BUS                           # noqa: E402
from dreg_verify.ui import contracts, names, terms              # noqa: E402
from dreg_verify.ui.app import MainWindow, build_window         # noqa: E402
from dreg_verify.ui.signal_list import status_key_of            # noqa: E402
from test_ui_app import FakeState, FakeWorker, make_models      # noqa: E402

LC = contracts.ListCol
LR = contracts.ListRole

#: 场景②要的那两档（mirror wl 上天然存在 risky-generated；关掉强制生成就是 needs-prefix）
_PREFIX_GRADES = ("risky-generated", "needs-prefix")


@pytest.fixture
def qapp():
    return H.app()


@pytest.fixture
def isolated(monkeypatch, tmp_path):
    """真 state 会读写 settings / edits —— 一律指到 tmp（I-08）。"""
    H.isolate_settings(monkeypatch, tmp_path)
    return tmp_path


def _visible_texts(root):
    """root 下所有带文字控件的 text()（截图之外的语义断言入口）。"""
    out = []
    for cls in (QtWidgets.QLabel, QtWidgets.QAbstractButton, QtWidgets.QLineEdit):
        for w in root.findChildren(cls):
            t = w.text() if hasattr(w, "text") else ""
            if t:
                out.append(t)
    return out


def _row_of(panel, name):
    """清单里某个信号所在的**可见**行号（proxy 行）。"""
    for r in range(panel.proxy.rowCount()):
        if panel.proxy.index(r, int(LC.NAME)).data(int(LR.NAME)) == name:
            return r
    raise AssertionError("清单里没有 %r（可见的：%s）" % (name, panel.visible_names()))


# ═════════════════════ ① 空态 / 载表 ═════════════════════
def test_scene_01_empty_state(qapp, isolated):
    """空态：xlsx 虚线框 · 标题 · 说明 · 两个按钮 · 最近打开（无记录写「还没有最近打开的表」）。

    走真工厂：`build_window([])` 造真 `WorkbenchState`；settings 已隔离到 tmp，
    既没有 recent 也没有 last_excel → 停在空态、**一张表都没载**（C-003 的另一半）。

    ⚠ 裁决⑫：空态**不得出现任何示例路径**（Design 原稿拿本仓路径当样例，v2 不照抄）。"""
    w = build_window([])
    w.resize(1600, 900)
    w.show()
    qapp.processEvents()

    assert w.is_empty_state()
    assert w.state.wb is None and w.state.loaded_path == ""
    assert w.worker is None                         # 没载表就不该起任何后台分析
    panel = H.find(w, names.EMPTY_PANEL)
    assert H.find(w, names.EMPTY_TITLE).text() == terms.EMPTY_TITLE
    assert H.find(w, names.EMPTY_DESC).text() == terms.EMPTY_DESC
    assert H.find(w, names.EMPTY_BTN_OPEN).text().startswith(terms.EMPTY_BTN_OPEN)
    assert H.find(w, names.EMPTY_BTN_IMPORT_CONFIG).text() == terms.EMPTY_BTN_IMPORT_CONFIG
    none_lbl = H.find(w, names.EMPTY_RECENT_NONE)
    assert none_lbl.text() == terms.EMPTY_RECENT_NONE and none_lbl.isVisibleTo(w)
    assert not H.find(w, names.EMPTY_RECENT_LIST).isVisibleTo(w)
    # 顶栏：未载入时路径框写「尚未选择文件」，主按钮是「载入」不是「重新载入」
    assert H.find(w, names.TOP_EXCEL_PATH).text() == terms.TOP_PATH_EMPTY
    assert H.find(w, names.TOP_LOAD_BTN).text().startswith(terms.TOP_LOAD)
    assert not H.find(w, names.FILTER_BAR).isVisibleTo(w)
    # 裁决⑫：一条示例路径都不许有
    bad = [t for t in _visible_texts(panel)
           if re.search(r"[A-Za-z]:[\\/]", t) or ".xlsx" in t or "\\\\" in t]
    assert not bad, "空态出现了示例路径：%s" % bad

    shot = H.shot(w, "scene_01")
    assert shot.endswith("scene_01.png")
    w.close()


def test_scene_01_empty_state_with_fake_state(qapp):
    """同一套断言、换成 FakeState —— 组合根不该认得 state 是真件还是假件。"""
    st = FakeState(recent=())
    w = MainWindow(state=st, worker_factory=lambda: FakeWorker())
    w.show()
    qapp.processEvents()
    assert w.is_empty_state() and st.load_calls == []
    assert H.find(w, names.EMPTY_TITLE).text() == terms.EMPTY_TITLE
    assert H.find(w, names.EMPTY_RECENT_NONE).isVisibleTo(w)
    assert not H.find(w, names.FILTER_BAR).isVisibleTo(w)
    w.close()


def test_scene_01_recent_rows_when_present(qapp, tmp_path):
    """有 MRU 时空态列真实「最近打开」（N4）；点一条即载入（C-003 的可见入口）。"""
    p = str(tmp_path / "mirror_wl_dreg.xlsx")
    open(p, "wb").write(b"PK\x03\x04")
    st = FakeState(recent=[{"path": p, "ts": "", "n_signals": 21}])
    w = MainWindow(state=st, worker_factory=lambda: FakeWorker())
    w.show()
    qapp.processEvents()
    assert H.find(w, names.EMPTY_RECENT_LIST).isVisibleTo(w)
    assert not H.find(w, names.EMPTY_RECENT_NONE).isVisibleTo(w)
    row = H.find(w, names.EMPTY_RECENT_LIST)
    btns = row.findChildren(QtWidgets.QPushButton)
    assert [b.text() for b in btns] == [p]
    btns[0].click()
    assert st.load_calls == [p]
    w.close()


# ═════════════════════ ⑧ 载入 200+ 进行中 ═════════════════════
def _slow_state(n=8, delay=0.05):
    """真 `WorkbenchState` + 慢 `FakeProvider`，且**清单还没分析过**。

    为什么不用真表：真 mirror 9 行 40ms 就跑完了，截不到「进行中」；211 行的真表又不在公开仓里。
    `FakeProvider(delay)` 正是架构 §5 场景⑧ 指定的起法——worker / 载入态 / 清单全是真的，
    只有「一个信号要算多久」是假的。"""
    st = F.FakeState(names=["d_fake_sig_%02d" % i for i in range(n)], delay=delay)
    vid = st.scope
    st.set_models(vid, [], False)        # 清掉 FakeState 预载的那份
    st._fingerprint.clear()              # → needs_analysis 为真，载表流程照常起 worker
    return st


def test_scene_08_loading_in_progress(qapp, isolated):
    """worker 正在逐信号展开：进度标题「正在展开 Topout 信号 k / N」、清单已可点、可随时停止。

    真 `AnalysisWorker`（真 QThread）+ 真 `WorkbenchState`；进度靠队列信号回主线程，
    所以等待一律走 `H.wait_for`，不写死毫秒。"""
    n = 8
    st = _slow_state(n=n, delay=0.05)
    w = MainWindow(state=st)             # ← 默认 worker 工厂 = 真 AnalysisWorker
    w.resize(1600, 900)
    w.show()
    ended = []
    w.analysisEnded.connect(lambda vid, ok: ended.append((vid, ok)))
    st.workbookChanged.emit()            # = 载表成功（真表路径由 load_path 走，这里只给假 provider）

    bar = H.find(w, names.LOADING_BAR)
    assert H.wait_for(lambda: bar.value()[0] >= 2), "worker 没跑起来：%s" % (bar.value(),)
    done, total = bar.value()
    assert total == n and 2 <= done < n

    # —— 载入态卡片 ——
    assert w.worker.is_running()
    lp = H.find(w, names.LOADING_PANEL)
    assert lp.isVisibleTo(w)
    assert H.find(w, names.LOADING_TITLE).text() == terms.LOADING_TITLE_FMT.format(done=done, total=n)
    assert H.find(w, names.LOADING_CURRENT).text().startswith("当前：d_fake_sig_")
    assert H.find(w, names.LOADING_HINT).text() == terms.LOADING_HINT
    assert H.find(w, names.LOADING_BTN_STOP).text() == terms.LOADING_BTN_STOP

    # —— 清单已可点：骨架 N 行先出、展开完的先升级，其余仍「分析中」——
    assert not w.is_empty_state() and H.find(w, names.LIST_PANEL).isVisibleTo(w)
    rows = st.models()
    assert len(rows) == n
    assert [r["status"] for r in rows[:done]] == ["ok"] * done
    assert {r["status"] for r in rows[done:]} == {"pending"}
    assert terms.STATUS["pending"][0] == "分析中"
    assert w.list_panel.proxy.rowCount() == n        # 分析中的行照样在清单里、照样能点
    assert w.list_panel.select_first_visible()

    shot = H.shot(w, "scene_08")
    assert shot.endswith("scene_08.png")

    # —— 停止分析：已完成行保留，其余仍 pending；状态栏点名 done/total ——
    # ⚠ 等的是 `analysisEnded`，不是 `not is_running()`：线程先结束、`cancelled` 才排队回主线程，
    #   盯着 is_running 会在「线程停了但收尾还没跑」的那一瞬间就往下走（全量跑时偶发红）。
    H.find(w, names.LOADING_BTN_STOP).click()
    assert H.wait_for(lambda: bool(ended)), "没收到 cancelled 收尾"
    assert ended == [(st.scope, False)]
    assert not w.worker.is_running()
    assert not lp.isVisibleTo(w)
    rows = st.models()
    kept = sum(1 for r in rows if r["status"] != "pending")
    assert kept >= done                               # 已展开完的一行都不许退回 pending
    assert [r["status"] for r in rows[:kept]] == ["ok"] * kept
    assert {r["status"] for r in rows[kept:]} == {"pending"}
    assert H.find(w, names.STATUS_LEFT).text() == terms.LOADING_STOPPED_FMT.format(done=kept, total=n)
    w.close()


def test_scene_08_finished_run_clears_loading(qapp, isolated):
    """对照组：worker 正常跑完 → 载入态收起、全部行升级（cancelled vs finished 语义）。"""
    st = _slow_state(n=6, delay=0.0)
    w = MainWindow(state=st)
    ended = []
    w.analysisEnded.connect(lambda vid, ok: ended.append((vid, ok)))
    st.workbookChanged.emit()
    assert H.wait_for(lambda: bool(ended))
    assert ended == [(st.scope, True)]
    assert not w.worker.is_running()
    assert not H.find(w, names.LOADING_PANEL).isVisibleTo(w)
    assert "pending" not in {r["status"] for r in st.models()}
    w.close()


def test_scene_08_fake_worker_stops_at_third_signal(qapp, tmp_path):
    """同一场景的确定性版本（同步 FakeWorker，卡死在第 3 个信号）——不依赖线程时序。"""
    models = make_models(8)
    st = FakeState(models=models)
    w = MainWindow(state=st, worker_factory=lambda: FakeWorker(stop_after=2))
    w.resize(1600, 900)
    w.show()
    p = str(tmp_path / "t.xlsx")
    open(p, "wb").write(b"PK\x03\x04")
    w.load_path(p)
    qapp.processEvents()
    assert w.worker.is_running()
    assert H.find(w, names.LOADING_TITLE).text() == terms.LOADING_TITLE_FMT.format(done=2, total=8)
    assert H.find(w, names.LOADING_BAR).value() == (2, 8)
    assert models[1]["name"] in H.find(w, names.LOADING_CURRENT).text()
    rows = st.models()
    assert [r["status"] for r in rows[:2]] == ["ok", "ok"]
    assert {r["status"] for r in rows[2:]} == {"pending"}
    H.find(w, names.LOADING_BTN_STOP).click()
    qapp.processEvents()
    assert not w.worker.is_running()
    assert H.find(w, names.STATUS_LEFT).text() == terms.LOADING_STOPPED_FMT.format(done=2, total=8)
    w.close()


# ═════════════════════ ② 信号清单 · 原因展开 ═════════════════════
def test_scene_02_list_reason_expand(qapp, isolated, monkeypatch):
    """载 mirror wl → 等分析跑完 → 点一条缺前缀行的**状态格** → 行内原因块 → 去诊断。

    全真：真 state（→ 真 provider / 真引擎）、真 AnalysisWorker（真 QThread）、真清单。
    点击也是真的（`H.click_cell` 发 QTest 鼠标事件到 viewport），不直接调槽。"""
    H.auto_dialogs(monkeypatch)
    w = build_window([])
    w.resize(1600, 900)
    w.show()
    ended = []
    w.analysisEnded.connect(lambda vid, ok: ended.append((vid, ok)))
    w.load_path(H.mirror_path("wl"))
    assert H.wait_for(lambda: bool(ended)), "分析没跑完：%s" % (ended,)
    assert ended == [("topout", True)]

    models = w.state.models()
    grade = next((g for g in _PREFIX_GRADES
                  if any(status_key_of(m) == g for m in models)), "")
    assert grade, "mirror wl 里没有缺前缀档：%s" % sorted({status_key_of(m) for m in models})
    name = next(m["name"] for m in models if status_key_of(m) == grade)

    panel = w.list_panel
    assert panel.proxy.rowCount() == len(models) == 9      # 筛选默认全放行（空 owner 集合 ≠ 一个都不留）
    H.click_cell(panel.view, _row_of(panel, name), int(LC.STATUS))     # C-015：点状态格
    qapp.processEvents()

    assert panel.view.expanded_name == name
    block = H.find(w, names.LIST_REASON)
    assert block.isVisibleTo(w)
    assert H.find(block, names.LIST_REASON_TITLE).text() == terms.REASON_TEMPLATES[grade][0]
    assert H.find(block, names.LIST_REASON_BODY).text()
    btn = H.find(block, names.LIST_REASON_DIAG_BTN)
    assert btn.text().startswith("去诊断")
    assert btn.text() == terms.REASON_BTN_FMT.format(action=terms.REASON_TEMPLATES[grade][2])

    shot = H.shot(w, "scene_02")
    assert shot.endswith("scene_02.png")

    # 点「去诊断」→ 组合根发场景信号（抽屉本体是 C4-b）+ 状态栏给去向
    got = []
    w.diagnosticsRequested.connect(got.append)
    btn.click()
    qapp.processEvents()
    target = terms.REASON_TEMPLATES[grade][3]
    assert got == [target] and target in terms.REASON_TARGETS
    assert H.find(w, names.STATUS_LEFT).text() == terms.REASON_TARGETS[target]

    # 再点一次状态格 = 收起（同一时刻只展开一行）
    H.click_cell(panel.view, _row_of(panel, name), int(LC.STATUS))
    qapp.processEvents()
    assert panel.view.expanded_name == ""
    w.close()


# ═════════════════════ ③ 详情 · 标题栏 + 右栏 + 电路图 ═════════════════════
def _loaded_window(qapp, monkeypatch, kind="wl"):
    """真工厂起窗 + 载 mirror + 等这一趟分析跑完。"""
    H.auto_dialogs(monkeypatch)
    w = build_window([])
    w.resize(1600, 900)
    w.show()
    ended = []
    w.analysisEnded.connect(lambda vid, ok: ended.append((vid, ok)))
    w.load_path(H.mirror_path(kind))
    assert H.wait_for(lambda: bool(ended)), "分析没跑完：%s" % (ended,)
    qapp.processEvents()
    return w


def test_scene_03_detail_header_side_and_flow(qapp, isolated, monkeypatch):
    """③ 详情：选中一个信号后**标题栏 + 右栏 + 电路图**都有内容，点右栏一个网名电路图对应块高亮。

    ⚠ 真值表仍是占位（C3-c 接手），所以这条不断言真值表 —— 场景③ 的「冻结列首行 = 真名」
    那半条留给 C3-int 补（当时把本条改名成 `..._truth_and_flow` 即可）。

    全真：真 state（→ 真 provider / 真引擎）、真 worker、真清单 / 标题栏 / 右栏 / 电路图。
    夹具只用 `tests/` 里的 mirror 镜像表（公开仓，绝不出现真实信号名）。"""
    w = _loaded_window(qapp, monkeypatch)
    panel = w.list_panel

    # 挑一个真画得出图的信号（只读回读 / 未解析的没有图，验不到东西）
    name = ""
    for m in w.state.models():
        an = w.state.analyze(m["name"], want_graph=True)
        if an and an.get("graph") is not None and an["graph"].edges:
            name = m["name"]
            break
    assert name, "mirror 里没有画得出图的信号：%s" % [m["name"] for m in w.state.models()]

    H.click_cell(panel.view, _row_of(panel, name), int(LC.NAME))       # 真点击，不直接调槽
    qapp.processEvents()
    assert w.state.current_name == name

    # ④ 标题栏：名字 / 状态徽标 / 摘要三样都填上了
    assert H.find(w, names.HDR_NAME).text() == name
    assert H.find(w, names.HDR_STATUS_BADGE).text() == terms.STATUS[status_key_of(
        w.state.model_of(name))][0]
    assert terms.OWNER_NONE in H.find(w, names.HDR_META).text() or \
        (w.state.model_of(name).get("owner") or "") in H.find(w, names.HDR_META).text()

    # ⑨ 右栏：逐层展开非空 + 输入信号表有行
    assert H.find(w, names.SIDE_PANEL).isVisibleTo(w)
    rows = w.side_panel.inputs_view.model().rows()
    assert rows, "右栏输入表是空的"
    assert w.side_panel.inputs_title.text() == terms.SIDE_INPUTS_TITLE_FMT.format(n=len(rows))
    assert w.side_panel.chain_view.toPlainText().strip()

    # ⑦ 电路图：在画布页（不是空态），画的是**这个**信号的图
    # ⚠ 只断「有图」会漏：载表后清单自动选中第一行（C-044），那张图本来就在画布上，
    #   `currentChanged → 电路图` 这根线断了照样「有图」—— 必须比图的标题是不是这一条。
    assert w.main_view.tab() == "truth"                                # 真值表 + 电路图 那一页
    assert w.flow_view.body.currentWidget() is w.flow_view.canvas
    g = w.flow_view.canvas.graph
    assert g is not None and g.nodes
    assert name in g.title, "电路图画的是别的信号：%s" % g.title

    # C-282：点右栏输入表的一行 → 电路图对应块高亮（同一根网，钥匙同一把）
    net = next((r["name"] for r in rows
                if any(BUS.net_key(e.net) == BUS.net_key(r["name"])
                       for e in w.flow_view.canvas.graph.edges if e.net)), "")
    assert net, "右栏的输入在图上一根线都对不上：%s" % [r["name"] for r in rows]
    idx = next(i for i, r in enumerate(rows) if r["name"] == net)
    H.click_cell(w.side_panel.inputs_view, 2 * idx, 1)                  # 主行（第二行是驱动串）
    qapp.processEvents()
    key = BUS.net_key(net)
    assert w.bus.current_net == key and w.bus.current_origin == "inputs"
    assert w.flow_view.canvas.current_net == key, "点了右栏，电路图没跟着高亮"
    assert 'data-hl="1"' in w.flow_view.canvas.svg_text
    assert w.side_panel.inputs_view.model().highlight() == key
    assert w.side_panel.chain_view.highlight() == key

    shot = H.shot(w, "scene_03")
    assert shot.endswith("scene_03.png")
    w.close()


# ═════════════════════ ④ 覆盖度弹层 ═════════════════════
def test_scene_04_coverage_popover(qapp, isolated, monkeypatch):
    """④ 覆盖度：弹层打开 → 改一档 → 标题栏按钮与生效链当场回显（C-148 / C-150 / C-156）。"""
    w = _loaded_window(qapp, monkeypatch)
    panel = w.list_panel
    assert panel.select_first_visible()
    qapp.processEvents()
    name = w.state.current_name
    assert name

    btn = H.find(w, names.HDR_COV_BTN)
    assert btn.text(), "标题栏的覆盖度按钮是空的"
    assert not H.find(w, names.COV_POPOVER).isVisible()

    H.click(btn)                                        # 真点击（按钮是 checkable，点开弹层）
    qapp.processEvents()
    pop = H.find(w, names.COV_POPOVER)
    assert pop.isVisible(), "覆盖度弹层没打开"
    chain = H.find(w, names.COV_CHAIN_BAR).text()
    assert chain and terms.COV_FOLLOW_UP in chain      # 默认：本信号跟随上级
    before = btn.text()

    # 改全局默认档 → 按钮与生效链当场跟着变（C-142 / C-148）
    g = H.find(w, names.COV_GLOBAL_COMBO)
    g.setCurrentIndex((g.currentIndex() + 1) % g.count())
    qapp.processEvents()
    assert btn.text() != before, "改了全局档，标题栏按钮没回显"
    assert H.find(w, names.COV_CHAIN_BAR).text() != chain
    assert w.state.coverage().global_label == g.currentText()
    # 界面上不许出现形态编号（terms.FORBIDDEN 的 F0–F4）
    for t in (btn.text(), H.find(w, names.COV_CHAIN_BAR).text(), H.find(w, names.COV_TITLE).text()):
        assert not [x for x in terms.FORBIDDEN if x in t], t

    shot = H.shot(w, "scene_04")
    assert shot.endswith("scene_04.png")
    w.close()


# ═════════════════════ 其余 3 个场景（各波接手，别删这些桩）═════════════════════


@pytest.mark.skip(reason="C4-int：ui/export_center.py 接上后补")
def test_scene_05_export_center():
    """⑤ 导出中心（Ctrl+G）：6 行齐；EXPORT_SUMMARY 含「会被跳过」时列出名字。"""


@pytest.mark.skip(reason="C4-int：导出完成弹层接上后补")
def test_scene_06_export_done_names_skipped():
    """⑥ 导出完成 · 跳过点名：DONE_SKIPPED_BLOCK 在 DONE_WRITTEN_BLOCK 之上；文件真存在。"""


@pytest.mark.skip(reason="C4-int：ui/diagnostics.py 接上后补")
def test_scene_07_diagnostics_drawer():
    """⑦ 诊断 · 找不到网（Ctrl+Shift+D）：三步标题齐；DIAG_STEP2_BOX = python3 scan_rtl.py。"""
