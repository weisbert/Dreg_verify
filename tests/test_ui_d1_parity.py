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


def _v1_sv(view, only=None):
    """v1 Topout 视图当前编辑状态渲染出的 .sv 全文（= 它 `on_export_sv` 写盘的那份）。"""
    mode, exh = view._mode()
    text, _ = X.render_sv(view.provider, only=only, mode=mode, max_tests=view._maxt(),
                          exhaustive=exh, edited=view._compute_edited(),
                          sig_cov=view._sig_cov, form_cov=view._form_cov)
    return text


def _v2_sv(w, vid="topout", only=None):
    """v2 同一份 .sv（同一个 `exports.render_sv`，参数从 state 取）。"""
    cov = w.state.coverage(vid)
    mode, exh = cov.mode()
    text, _ = X.render_sv(w.state.provider(vid), only=only, mode=mode,
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


# ═════════════ P-07：「清空勾选」只清可见（v1 口径）═════════════
def test_p07_uncheck_all_only_clears_the_visible_rows(monkeypatch, tmp_path):
    """C-033。工程师的用法是「筛出这一批 → 清掉这一批 → 换个筛选再挑」。v1
    `SignalView._check_all(False)` 跳过 `isRowHidden` 的行，v2 清的是整张清单 ——
    筛选之外那些他刚挑好的被静默抹掉，清单上看不见、状态栏也只报一个数（P-07）。

    序列两边一字不差：载表（默认全勾）→ 搜 `d_logic` → 清空勾选 → 清掉搜索 →
    按「勾选的」渲 .sv → 逐字节比。
    """
    H.isolate_settings(monkeypatch, tmp_path)
    H.auto_dialogs(monkeypatch)
    path = H.mirror_path("btlp")

    w1, v1 = _v1_window(path)
    try:
        n_all = len(v1._checked_names())
        v1.search.setText("d_logic")
        v1._apply_filter()
        n_vis1 = sum(1 for r in range(v1.sig_table.rowCount())
                     if not v1.sig_table.isRowHidden(r))
        v1._check_all(False)                     # 「清空勾选」
        v1.search.setText("")
        v1._apply_filter()
        left1 = sorted(v1._checked_names())
        sv1 = _v1_sv(v1, only=v1._checked_names())
        # 第二次、且不同：换个筛选再「全选(可见)」——两边的「全选」本来就只作用可见
        v1.search.setText("en_")
        v1._apply_filter()
        v1._check_all(True)
        v1.search.setText("")
        v1._apply_filter()
        left1b = sorted(v1._checked_names())
    finally:
        w1.close()
    H.app().processEvents()

    w2 = _v2_window(path)
    try:
        lp = w2.list_panel
        assert len(w2.state.checked_names("topout")) == n_all
        lp.set_filters(regex="d_logic")
        H.app().processEvents()
        assert lp.proxy.n_visible() == n_vis1
        lp.uncheck_all()
        lp.set_filters()
        H.app().processEvents()
        left2 = sorted(w2.state.checked_names("topout"))
        sv2 = _v2_sv(w2, only=w2.state.checked_names("topout"))
        lp.set_filters(regex="en_")
        H.app().processEvents()
        lp.check_all_visible()
        lp.set_filters()
        H.app().processEvents()
        left2b = sorted(w2.state.checked_names("topout"))
    finally:
        w2.close()

    assert 0 < n_vis1 < n_all, "筛选得真的筛掉点东西，否则这条测试验不到作用域"
    assert 0 < len(left1) < n_all, "v1 清空勾选后既有留下的也有清掉的"
    assert left2 == left1
    assert left2b == left1b and len(left1b) > len(left1)
    assert sv2 == sv1, ".sv 与 v1 不再逐字节相同（v1 %d / v2 %d 字符）" % (len(sv1), len(sv2))


# ═════════════ P-08：「全部加反例」作用于所有勾着的（v1 口径）═════════════
def test_p08_neg_all_covers_every_checked_signal_even_the_hidden_ones(monkeypatch, tmp_path):
    """C-035。勾选是「我要验这一批」的声明，与「这会儿筛选让我看见哪几行」是两件事。
    v1 `_bulk_neg` 取的是 `_checked_names()`（整张清单里勾着的），v2 取的是「可见 ∩ 勾着」
    —— 先勾 20 个、筛到 3 个、点「全部加反例」，另外 17 个静默没加上，导出时才发现（P-08）。

    序列两边一字不差：载表（默认全勾）→ 搜 `d_logic` → 全部加反例 → 清掉搜索 → 比 .sv。
    """
    H.isolate_settings(monkeypatch, tmp_path)
    H.auto_dialogs(monkeypatch)
    path = H.mirror_path("btlp")

    w1, v1 = _v1_window(path)
    try:
        v1.search.setText("d_logic")
        v1._apply_filter()
        n_vis = sum(1 for r in range(v1.sig_table.rowCount())
                    if not v1.sig_table.isRowHidden(r))
        v1._bulk_neg(True)
        v1.search.setText("")
        v1._apply_filter()
        neg1 = sorted(_neg_names(v1.edits))
        sv1 = _v1_sv(v1)
        # 第二次、且不同：再筛一个更小的集合「清除反例」——同一条作用域规矩反着走一遍
        v1.search.setText("en_dig")
        v1._apply_filter()
        v1._bulk_neg(False)
        v1.search.setText("")
        v1._apply_filter()
        neg1b = sorted(_neg_names(v1.edits))
    finally:
        w1.close()
    H.app().processEvents()

    w2 = _v2_window(path)
    try:
        lp = w2.list_panel
        lp.set_filters(regex="d_logic")
        H.app().processEvents()
        assert lp.proxy.n_visible() == n_vis
        lp.neg_all()
        lp.set_filters()
        H.app().processEvents()
        neg2 = sorted(_neg_names(w2.state.edits("topout")))
        sv2 = _v2_sv(w2)
        lp.set_filters(regex="en_dig")
        H.app().processEvents()
        lp.neg_clear()
        lp.set_filters()
        H.app().processEvents()
        neg2b = sorted(_neg_names(w2.state.edits("topout")))
    finally:
        w2.close()

    assert len(neg1) > n_vis, "v1 应当连被筛掉的勾选信号一起加上，否则这条测试验不到作用域"
    assert neg2 == neg1
    assert sv2 == sv1, ".sv 与 v1 不再逐字节相同（v1 %d / v2 %d 字符）" % (len(sv1), len(sv2))
    # 「清除反例」走同一个 `neg_targets`：信号全都还勾着 → 一次清光（v1 也是），两边同样空
    assert neg2b == neg1b == []


# ═════════════ P-21 / P-27：状态列排序次序 与 逻辑类型列标签按 v1 ═════════════
def test_p21_status_column_sort_order_matches_v1(monkeypatch, tmp_path):
    """C-037。v1 的状态列排的是格子里那四档标签的文字序；v2 排的是八档折下来的色档权重，
    `bare-probe`（v1 status=ok）被算成 note、跟只读回读根排到了一起 —— btlp 上两代排序的
    **唯一**差异就是这一行（P-21）。修法与状态筛 P-13 同一份判据（`terms` 那张 note 归属表）。

    序列与 R1 复现脚本一致：先点「信号」、再点「owner」、最后点「状态」（前两下把平局序
    也定死，否则比的是 Qt 的排序稳定性而不是排序键）。
    """
    from PySide6 import QtCore as QC                        # noqa: PLC0415
    from dreg_verify.ui.contracts import ListCol as LC      # noqa: PLC0415
    H.isolate_settings(monkeypatch, tmp_path)
    H.auto_dialogs(monkeypatch)
    path = H.mirror_path("btlp")

    w1, v1 = _v1_window(path)
    try:
        v1.sig_table.setSortingEnabled(True)
        for col in (2, 4, 7):                    # legacy_gui: TOPO_NAME / TOPO_OWNER / TOPO_STATUS
            v1.sig_table.sortItems(col, QC.Qt.AscendingOrder)
        order1 = [v1.sig_table.item(r, 2).text() for r in range(v1.sig_table.rowCount())]
        # v1 的四档标签序列（真正的排序键），一并钉住，免得只比到「名字碰巧一样」
        st1 = [v1.sig_table.item(r, 7).text() for r in range(v1.sig_table.rowCount())]
    finally:
        w1.close()
    H.app().processEvents()

    w2 = _v2_window(path)
    try:
        lp = w2.list_panel
        for col in (LC.NAME, LC.OWNER, LC.STATUS):
            lp.sort_by(col, QC.Qt.AscendingOrder)
            H.app().processEvents()
        order2 = [lp.proxy.index(r, int(LC.NAME)).data() for r in range(lp.proxy.rowCount())]
    finally:
        w2.close()

    assert len(set(st1)) >= 2, "这张镜像得同时有可建与非可建的行，否则排序验不到东西"
    assert order2 == order1, "状态列排序与 v1 不同：\n  v1 %s\n  v2 %s" % (order1, order2)


def test_p27_form_column_labels_match_v1(monkeypatch, tmp_path):
    """C-013。v1 的「逻辑类型」列写的是引擎的 `form_label`（门控按内层形态分成
    「门控·选路」/「门控·布尔/位运算」）；v2 拿 `form` 去查四档表，wl 镜像上 7 个门控信号
    全塌成同一个标签，「这一批到底是哪种」在列上就没了（P-27）。"""
    from dreg_verify.ui.contracts import ListCol as LC      # noqa: PLC0415
    H.isolate_settings(monkeypatch, tmp_path)
    H.auto_dialogs(monkeypatch)
    path = H.mirror_path("wl")

    w1, v1 = _v1_window(path)
    try:
        labels1 = {v1.sig_table.item(r, 2).text().split("[")[0]:
                   v1.sig_table.item(r, 6).text()             # legacy_gui: TOPO_FORM
                   for r in range(v1.sig_table.rowCount())}
    finally:
        w1.close()
    H.app().processEvents()

    w2 = _v2_window(path)
    try:
        lp = w2.list_panel
        labels2 = {str(lp.proxy.index(r, int(LC.NAME)).data() or "").split("[")[0]:
                   str(lp.proxy.index(r, int(LC.FORM)).data() or "")
                   for r in range(lp.proxy.rowCount())}
    finally:
        w2.close()

    gated = [n for n, lab in labels1.items() if lab.startswith("门控")]
    assert len(gated) >= 7, "wl 镜像上应有 7 个门控信号，实际 %d" % len(gated)
    assert len({labels1[n] for n in gated}) >= 2, "v1 就该把门控信号分成不止一档"
    assert labels2 == labels1
    assert not any("F0" in s or "F1" in s or "F2" in s or "F3" in s or "F4" in s
                   for s in labels2.values()), "逻辑类型列不得出现 F 编号"
