# -*- coding: utf-8 -*-
"""test_ui_d1_parity.py —— Phase D 修复波 F3「行为 parity」的**起窗**回归（P-nn）。

对抗 review R1 用 62 对逐字节产物证明了 v2 相对 v1 的一批行为差异，主控逐条裁决（多数按 v1
对齐）。本文件收「非得两代窗口各起一个、同一串动作走真入口、再比产物」的那几条；纯数据口径
的在 `tests/test_d1_parity_engine.py`。

每条 `test_p<nn>_…` 的 nn = 发现汇总里的 P 编号（回查裁决原文用）。脚本原型是 R1 的复现脚本
（`refactor_notes/review_r1/`），这里只把「打印出来给人看」换成断言。

夹具一律用 mirror 生成脚本（公开仓，**无真实信号名**）。
"""

import pytest

import ui_harness as H                                    # noqa: E402

pytest.importorskip("PySide6")

from PySide6 import QtCore                                # noqa: E402

from dreg_verify import exports as X                      # noqa: E402


# ─────────────────────────── 两代窗口的公共起法 ───────────────────────────
def _v1_window(path):
    """v1 主窗（`ui_harness` 的默认工厂就是它）+ 它的 Topout 视图。"""
    w = H.make_window(path)
    return w, w.topout_view


def _v2_window(path):
    """v2 组合根：载表后等整表分析跑完（清单先出骨架，`analysisEnded` 才算数）。"""
    from dreg_verify.ui import app as A                    # noqa: PLC0415
    w = A.MainWindow()
    ended = []
    w.analysisEnded.connect(lambda vid, ok: ended.append((vid, ok)))
    w.load_path(path)
    H.wait_for(lambda: bool(ended), timeout_ms=60000)
    H.app().processEvents()
    return w


def _v1_sv(view):
    """v1 Topout 视图当前编辑状态渲染出的 .sv 全文（= 它 `on_export_sv` 写盘的那份）。"""
    mode, exh = view._mode()
    text, _ = X.render_sv(view.provider, only=None, mode=mode, max_tests=view._maxt(),
                          exhaustive=exh, edited=view._compute_edited(),
                          sig_cov=view._sig_cov, form_cov=view._form_cov)
    return text


def _v2_sv(w, vid="topout"):
    """v2 同一份 .sv（同一个 `exports.render_sv`，参数从 state 取）。"""
    cov = w.state.coverage(vid)
    mode, exh = cov.mode()
    text, _ = X.render_sv(w.state.provider(vid), only=None, mode=mode,
                          max_tests=int(cov.max_tests), exhaustive=exh,
                          edited=w.state.compute_edited(vid),
                          sig_cov=dict(cov.sig_cov) or None, form_cov=dict(cov.form_cov) or None)
    return text


def _neg_names(edits):
    """{信号名: [反例列标号]} —— 两代都是同一份列模型，直接比得。"""
    return {k: [c["name"] for c in e["cols"] if c["neg"]]
            for k, e in (edits or {}).items() if any(c["neg"] for c in e["cols"])}


# ═════════════════ P-02：清单勾「反例」的标号按 v1 = T0_NEG ═════════════════
@pytest.mark.parametrize("kind", ["btlp", "wl"])
@pytest.mark.parametrize("how", ["single", "bulk"])
def test_p02_list_negative_column_is_labelled_like_v1(kind, how, monkeypatch, tmp_path):
    """C-023：清单第 2 列勾「反例」/ 底部「全部加反例」造出来的那条反例列，v1 写死 `T0_NEG`，
    v2 走 `edits.new_col_name` 给的是 `U0_NEG` —— 两张镜像 × 单勾/批量 4 组 .sv 的**唯一**
    差异就是这个标号，红区拿旧 diff / log 脚本按 `T0_NEG` 回查会全落空（P-02，BLOCKER）。

    裁决：清单这一路按 v1 命名；真值表工具条「加负向(选中)」那一路不变（仍是 `U<n>_NEG`）。
    """
    H.isolate_settings(monkeypatch, tmp_path)
    H.auto_dialogs(monkeypatch)
    path = H.mirror_path(kind)

    w1, v1 = _v1_window(path)
    try:
        names = [m["name"] for m in v1.models if m["kind"] in ("logic", "register", "mux")][:3]
        if how == "single":
            for n in names:                       # 真勾选框 → _on_sig_item_changed
                r = next(i for i, m in enumerate(v1.models) if m["name"] == n)
                it = v1.sig_table.item(r, 1)      # legacy_gui.TOPO_NEG
                assert it is not None and (it.flags() & QtCore.Qt.ItemIsUserCheckable), n
                it.setCheckState(QtCore.Qt.Checked)
        else:
            v1._bulk_neg(True)
        v1_negs = _neg_names(v1.edits)
        sv1 = _v1_sv(v1)
    finally:
        w1.close()
    H.app().processEvents()

    w2 = _v2_window(path)
    try:
        if how == "single":
            for n in names:                       # = 清单第 2 列勾选的槽
                w2.state.set_neg(n, True)
        else:
            w2.list_panel.neg_all()
        H.app().processEvents()
        v2_negs = _neg_names(w2.state.edits("topout"))
        sv2 = _v2_sv(w2)
    finally:
        w2.close()

    assert v1_negs and set(v1_negs) == set(v2_negs)
    assert {n for v in v2_negs.values() for n in v} == {"T0_NEG"}
    assert v2_negs == v1_negs
    assert sv2 == sv1, "%s/%s 的 .sv 与 v1 不再逐字节相同（v1 %d / v2 %d 字符）" % (
        kind, how, len(sv1), len(sv2))


def test_p02_truth_toolbar_negatives_keep_their_own_numbering(monkeypatch, tmp_path):
    """第二次、且不同：**真值表工具条**那一路（`edits.add_negatives` 直调，C-096）不受影响
    —— 它在 v1 里本来就叫 `U<n>_NEG`，一起改掉的话批量加负向又会全撞同一个标号。"""
    from dreg_verify import edits as ED                     # noqa: PLC0415
    from dreg_verify.ui import state as ST                  # noqa: PLC0415
    H.isolate_settings(monkeypatch, tmp_path)
    st = ST.WorkbenchState()
    st.load(H.mirror_path("btlp"))
    st.set_models("topout", st.provider("topout").skeleton_models(), True)
    name = "d_logic_bt_lp_rx_en"
    an = st.analyze(name, "topout")
    cols = ED.cols_from_vectors(an, [])
    made, _skipped = ED.add_negatives(cols, [0, 1], False)
    assert [c["name"] for c in made] == ["U0_NEG", "U1_NEG"]
    assert ST.NEG_COL_NAME == "T0_NEG"
