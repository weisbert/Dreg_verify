# -*- coding: utf-8 -*-
"""GUI v2「8 个 Design 场景」= 8 组截图断言（架构文档 §5）。

每条既截图（`refactor_notes/gui_v2_shots/scene_NN.png`，目录已 gitignore）也断文本 ——
offscreen 缺中文字体，**版面靠截图过目、语义靠 `widget.text()` 断言**。

本波（C1-d）只落 ① 空态 / 载表 与 ⑧ 载入 200+ 进行中；
②③④⑤⑥⑦ 由 C1-int / C2-int / C3-int / C4-int 各自接手（下面留桩，别删）。
"""
import re

import pytest

import ui_harness as H

pytest.importorskip("PySide6")

from PySide6 import QtWidgets                                   # noqa: E402

from dreg_verify.ui import names, terms                         # noqa: E402
from dreg_verify.ui.app import MainWindow                       # noqa: E402
from test_ui_app import FakeState, FakeWorker, make_models      # noqa: E402


@pytest.fixture
def qapp():
    return H.app()


def _visible_texts(root):
    """root 下所有带文字控件的 text()（截图之外的语义断言入口）。"""
    out = []
    for cls in (QtWidgets.QLabel, QtWidgets.QAbstractButton, QtWidgets.QLineEdit):
        for w in root.findChildren(cls):
            t = w.text() if hasattr(w, "text") else ""
            if t:
                out.append(t)
    return out


# ═════════════════════ ① 空态 / 载表 ═════════════════════
def test_scene_01_empty_state(qapp):
    """空态：xlsx 虚线框 · 标题 · 说明 · 两个按钮 · 最近打开（无记录写「还没有最近打开的表」）。

    ⚠ 裁决⑫：空态**不得出现任何示例路径**（Design 原稿拿本仓路径当样例，v2 不照抄）。"""
    st = FakeState(recent=())                       # settings 无 recent、无 last_excel
    w = MainWindow(state=st, worker_factory=lambda: FakeWorker())
    w.resize(1600, 900)
    w.show()
    qapp.processEvents()

    assert w.is_empty_state()
    assert st.load_calls == []
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
def test_scene_08_loading_in_progress(qapp, tmp_path):
    """worker 卡在第 3 个信号：进度标题「正在展开 Topout 信号 2 / N」、清单已可点、可随时停止。"""
    models = make_models(8)
    st = FakeState(models=models)
    w = MainWindow(state=st, worker_factory=lambda: FakeWorker(stop_after=2))
    w.resize(1600, 900)
    w.show()
    p = str(tmp_path / "t.xlsx")
    open(p, "wb").write(b"PK\x03\x04")
    w.load_path(p)
    qapp.processEvents()

    # —— 载入态卡片 ——
    assert w.worker.is_running()
    lp = H.find(w, names.LOADING_PANEL)
    assert lp.isVisibleTo(w)
    assert H.find(w, names.LOADING_TITLE).text() == terms.LOADING_TITLE_FMT.format(done=2, total=8)
    assert H.find(w, names.LOADING_BAR).value() == (2, 8)
    assert models[1]["name"] in H.find(w, names.LOADING_CURRENT).text()
    assert H.find(w, names.LOADING_HINT).text() == terms.LOADING_HINT
    assert H.find(w, names.LOADING_BTN_STOP).text() == terms.LOADING_BTN_STOP

    # —— 清单已可点：骨架 8 行先出，展开完的先升级，其余仍「分析中」——
    assert not w.is_empty_state() and H.find(w, names.LIST_PANEL).isVisibleTo(w)
    rows = st.models()
    assert len(rows) == 8
    assert [r["status"] for r in rows[:2]] == ["ok", "ok"]
    assert {r["status"] for r in rows[2:]} == {"pending"}
    assert terms.STATUS["pending"][0] == "分析中"

    shot = H.shot(w, "scene_08")
    assert shot.endswith("scene_08.png")

    # —— 停止分析：已完成行保留，其余仍 pending；状态栏点名 done/total ——
    H.find(w, names.LOADING_BTN_STOP).click()
    qapp.processEvents()
    assert not w.worker.is_running()
    assert not lp.isVisibleTo(w)
    rows = st.models()
    assert [r["status"] for r in rows[:2]] == ["ok", "ok"]
    assert {r["status"] for r in rows[2:]} == {"pending"}
    assert H.find(w, names.STATUS_LEFT).text() == terms.LOADING_STOPPED_FMT.format(done=2, total=8)
    w.close()


def test_scene_08_finished_run_clears_loading(qapp, tmp_path):
    """对照组：worker 正常跑完 → 载入态收起、全部行升级（cancelled vs finished 语义）。"""
    st = FakeState(models=make_models(8))
    w = MainWindow(state=st, worker_factory=lambda: FakeWorker())
    p = str(tmp_path / "t.xlsx")
    open(p, "wb").write(b"PK\x03\x04")
    w.load_path(p)
    assert not w.worker.is_running()
    assert not H.find(w, names.LOADING_PANEL).isVisibleTo(w)
    assert "pending" not in {r["status"] for r in st.models()}
    w.close()


# ═════════════════════ 其余 6 个场景（各波接手，别删这些桩）═════════════════════
@pytest.mark.skip(reason="C1-int：接上 ui/signal_list.py 后补（点 needs-prefix 行的状态格 → 行内原因块）")
def test_scene_02_list_reason_expanded():
    """② 信号清单 · 原因展开：LIST_REASON_TITLE = 模板标题；LIST_REASON_DIAG_BTN 以「去诊断」开头。"""


@pytest.mark.skip(reason="C3-int：真值表 + 电路图接上后补")
def test_scene_03_detail_truth_and_flow():
    """③ 详情 · 真值表 + 电路图：冻结列首行 = 真名；FLOW_VIEW 场景非空；tabs 文案含「手填 n/m」。"""


@pytest.mark.skip(reason="C2-int：ui/coverage.py 接上后补")
def test_scene_04_coverage_popover():
    """④ 覆盖度展开：COV_CHAIN_BAR 文本 = COV_CHAIN_FMT 渲染；生效两行高亮。"""


@pytest.mark.skip(reason="C4-int：ui/export_center.py 接上后补")
def test_scene_05_export_center():
    """⑤ 导出中心（Ctrl+G）：6 行齐；EXPORT_SUMMARY 含「会被跳过」时列出名字。"""


@pytest.mark.skip(reason="C4-int：导出完成弹层接上后补")
def test_scene_06_export_done_names_skipped():
    """⑥ 导出完成 · 跳过点名：DONE_SKIPPED_BLOCK 在 DONE_WRITTEN_BLOCK 之上；文件真存在。"""


@pytest.mark.skip(reason="C4-int：ui/diagnostics.py 接上后补")
def test_scene_07_diagnostics_drawer():
    """⑦ 诊断 · 找不到网（Ctrl+Shift+D）：三步标题齐；DIAG_STEP2_BOX = python3 scan_rtl.py。"""
