# -*- coding: utf-8 -*-
"""test_ui_d1_parity.py —— Phase D 修复波 F3「行为 parity」的**起窗**回归（P-nn）。

对抗 review R1 用 62 对逐字节产物证明了 v2 相对 v1 的一批行为差异，主控逐条裁决（多数按 v1
对齐）。本文件收「非得两代窗口各起一个、同一串动作走真入口、再比产物」的那几条；纯数据口径
的在 `tests/test_d1_parity_engine.py`。

每条 `test_p<nn>_…` 的 nn = 发现汇总里的 P 编号（回查裁决原文用）。脚本原型是 R1 的复现脚本
（`refactor_notes/review_r1/`），这里只把「打印出来给人看」换成断言。

夹具一律用 mirror 生成脚本（公开仓，**无真实信号名**）。
"""

import os

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


# ═════════════ P-04：导入「旧版测试项编辑文件」走 C-302 那条迁移路 ═════════════
#: 文件里掺进去的两个**当前表没有**的名字（C-194：跳过必须点名）
_GHOST_LOGIC = "d_ghost_signal_gone"
_GHOST_MUX = "d_ghost_mux_gone"


def _make_legacy_file(w1, out_path):
    """用 v1【排查(旧)】门面真造一份旧格式【测试项编辑】文件（只有九段、没有版本号键）。"""
    import json                                            # noqa: PLC0415
    from dreg_verify import legacy_gui as G                 # noqa: PLC0415
    picked = []
    for sig in w1.signals:
        if not str(getattr(sig, "out_name", "")).startswith("d_logic") or len(picked) >= 2:
            continue
        w1._load_test_items(sig)
        H.app().processEvents()
        w1.ti_table.item(w1.R_EXP, 0).setText(str(1 - (w1._ti_rows[0]["correct"] & 1)))
        H.app().processEvents()
        picked.append(sig.out_name)
    assert len(picked) == 2, "镜像表里应当至少有两个 logic 信号可手填"
    w1._persist_edits()
    H.app().processEvents()
    bucket = G._load_edits_file().get(w1._loaded_excel_path) or {}
    payload = {"dreg_verify_edits": 1, "excel": os.path.basename(w1._loaded_excel_path)}
    for k in ("edits", "neg_only", "mux_expected", "mux_neg", "mux_data",
              "mux_dropped", "mux_cleared", "mux_user_vecs"):
        if k in bucket:
            payload[k] = bucket[k]
    payload["edits"][_GHOST_LOGIC] = [{"base_values": {}, "kind": "pos"}]
    payload["mux_expected"] = dict(payload.get("mux_expected") or {})
    payload["mux_expected"][_GHOST_MUX] = {"c:A=1": 1}
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
    return picked


def test_p04_legacy_edits_file_import_restores_the_same_signals_as_v1(monkeypatch, tmp_path):
    """旧版【测试项编辑】文件（只有 edits / neg_only / mux_* 九段、没有 `dreg_verify_config`）
    在 v2 **完全空转**：v1 恢复 2 个信号并点名跳过 2 个，v2 报「恢复了 0 个信号」（P-04，BLOCKER）
    —— 那一支只调了 `_apply_view_edits`，而它只读 `view_edits` / `view_checks` 两段。

    裁决：走 C-302 那条迁移路（`diagnostics.plan/apply_legacy_import`，桶换成**文件内容**），
    点名迁了谁、跳过谁 + 原因，用的是既有文案。
    """
    from dreg_verify import edits as ED                     # noqa: PLC0415
    from dreg_verify.ui import export_center as EC          # noqa: PLC0415
    from dreg_verify.ui import terms as T                   # noqa: PLC0415
    H.isolate_settings(monkeypatch, tmp_path)
    H.auto_dialogs(monkeypatch)
    path = H.mirror_path("btlp")
    legacy = str(tmp_path / "old_edits.json")

    w1, _v = _v1_window(path)
    try:
        picked = _make_legacy_file(w1, legacy)
    finally:
        w1.close()
    H.app().processEvents()

    # v1 参照：真走 on_import_edits（弹框已被 auto_dialogs 接管）
    H.auto_dialogs(monkeypatch, answers={"getOpenFileName": lambda c, *a, **k: (legacy, "")})
    w1b, _v = _v1_window(path)
    try:
        w1b.on_import_edits()
        H.app().processEvents()
        v1_restored = {k.lower() for k in w1b._edited}
    finally:
        w1b.close()
    H.app().processEvents()

    w2 = _v2_window(path)
    try:
        rep = EC.import_config(w2.state, legacy)
        H.app().processEvents()
        v2_restored = set(ED.serialize_view_edits(w2.state.edits("topout")))
        text = rep.text()
        missing = dict(rep.missing)
    finally:
        w2.close()

    assert rep.ok and rep.is_legacy and not rep.is_full
    assert {p.lower() for p in picked} <= v1_restored, "v1 参照本身就没恢复出来，夹具有问题"
    assert v2_restored == v1_restored
    assert rep.n_restored == len(v1_restored) == 2
    # C-194 / I-20：跳过的先点名 + 原因（用的是 C-302 那几句既有文案）
    assert missing.get(_GHOST_LOGIC) == T.DIAG_LEGACY_SKIP_UNKNOWN
    assert missing.get(_GHOST_MUX) == T.DIAG_LEGACY_SKIP_MUX
    for nm in picked:                                      # 迁了谁也点名
        assert nm in text
    assert text.index(_GHOST_LOGIC) < text.index(
        T.EXPORT_IMPORT_DONE_FMT.format(kind=T.EXPORT_IMPORT_KIND_LEGACY, n=2))


def test_p04_a_full_config_does_not_go_down_the_legacy_migration_path(monkeypatch, tmp_path):
    """第二次、且不同：**完整配置**里也带着那九段（C-250 的字段集，P-05 起还会原样透传），
    但它归 `view_edits` 那条路管 —— 别把同一份劳动成果再顺着 C-302 迁一遍。"""
    from dreg_verify import session as S                    # noqa: PLC0415
    from dreg_verify.ui import export_center as EC          # noqa: PLC0415
    H.isolate_settings(monkeypatch, tmp_path)
    H.auto_dialogs(monkeypatch)
    path = H.mirror_path("btlp")
    legacy = str(tmp_path / "old_edits.json")

    w1, _v = _v1_window(path)
    try:
        _make_legacy_file(w1, legacy)
    finally:
        w1.close()
    H.app().processEvents()

    import json                                            # noqa: PLC0415
    with open(legacy, encoding="utf-8") as f:
        old = json.load(f)
    full = str(tmp_path / "cfg.json")
    S.write_config_file(full, S.collect_config(
        path, {}, edits=old.get("edits"), mux_expected=old.get("mux_expected")))

    w2 = _v2_window(path)
    try:
        rep = EC.import_config(w2.state, full)
        H.app().processEvents()
        restored = set(w2.state.edits("topout"))
    finally:
        w2.close()

    assert rep.ok and rep.is_full
    assert rep.n_restored == 0 and not restored, "完整配置的 legacy 段不该被 C-302 再迁一遍"
    assert not rep.missing


# ═════════════ P-16：探针前缀的影响面按「.sv 真变了」算 ═════════════
def _state_sv(st, vid="topout"):
    """一份 state 当前配置下渲染出的 .sv 全文（不起窗）。"""
    cov = st.coverage(vid)
    mode, exh = cov.mode()
    text, _ = X.render_sv(st.provider(vid), only=None, mode=mode,
                          max_tests=int(cov.max_tests), exhaustive=exh,
                          edited=st.compute_edited(vid))
    return text


def test_p16_prefix_impact_counts_exactly_the_signals_whose_sv_changed(monkeypatch, tmp_path):
    """C-204。「影响 M 个信号」以前是**字符串交集**：输入名在映射里就算命中 —— 配在一个谁也
    没 force 的名字上，.sv 前后逐字节相同却报「影响 2 个信号」（假绿）；配在真正的 `_to_mux`
    衔接网上，.sv 真变了却一个都不报（假阴）。R41 那条总根在视图层的第二实例（P-16）。

    裁决：命中只认引擎给的 `found_in`（v1 口径），影响面按**重分析前后真变了**的信号算。
    这里把这条口径直接钉成等价关系：**报的数 > 0 ⟺ .sv 真变了**，一次盖掉假绿与假阴。
    """
    from dreg_verify import inputs_table as IT              # noqa: PLC0415
    from dreg_verify.ui import diagnostics as D             # noqa: PLC0415
    from dreg_verify.ui import state as ST                  # noqa: PLC0415
    H.isolate_settings(monkeypatch, tmp_path)
    H.app()
    st = ST.WorkbenchState()
    assert st.load(H.mirror_path("wl"))
    st.set_models("topout", st.provider("topout").skeleton_models(), True)

    # 找一根**引擎明说要层级前缀**的网（真影响）和一根寄存器背书的输入基名（假绿的诱饵）
    real_wire, decoy = "", ""
    for m in st.models():
        for r in IT.input_rows(st.analyze(m["name"])) or []:
            base = str(r.get("base") or "").strip().lower()
            if not base:
                continue
            if not real_wire and str(r.get("found_in") or "") in ("needs-prefix", "prefixed-wire"):
                real_wire = base
            elif not decoy and str(r.get("found_in") or "").startswith("reg"):
                decoy = base
    assert real_wire, "wl 镜像上应当有缺前缀的衔接网，否则这条测试验不到东西"

    cases = [("谁也不认识的名字", {"d_no_such_net_anywhere_xyz": "U_A.U_B"}),
             ("寄存器背书的输入基名（假绿的诱饵）", {decoy or "d_no_such_2": "U_A.U_B"}),
             ("真要层级前缀的衔接网", {real_wire: "U_WL_RF_TOP.U_SUB"}),
             ("清空映射", {})]
    for tag, mapping in cases:
        sv_before = _state_sv(st)
        before = D.prefix_snapshot(st)
        st.set_probe_prefixes(mapping)
        n_map, n_sig = D.prefix_impact(st, mapping, before=before)
        sv_after = _state_sv(st)
        assert n_map == len(mapping), tag
        assert (n_sig > 0) == (sv_after != sv_before), (
            "%s：报「影响 %d 个信号」，.sv 却%s变" % (tag, n_sig, "" if sv_after != sv_before else "没"))


def test_p16_hit_only_counts_the_engines_prefixed_wire(monkeypatch, tmp_path):
    """不给「改之前」的快照时退回 v1 `on_set_probe_prefix` 末尾那个口径 —— 判据是引擎给的
    `found_in`，**不是**「输入名在不在映射里」。后者会把「配了但没人 force」也算成命中。"""
    from dreg_verify.ui import diagnostics as D             # noqa: PLC0415
    from dreg_verify.ui import state as ST                  # noqa: PLC0415
    H.isolate_settings(monkeypatch, tmp_path)
    H.app()
    st = ST.WorkbenchState()
    assert st.load(H.mirror_path("btlp"))
    st.set_models("topout", st.provider("topout").skeleton_models(), True)
    src = "dreg_verify/ui/diagnostics.py"
    assert 'str(r.get("base") or "").strip().lower() in mp' not in open(
        src, encoding="utf-8").read(), "字符串交集那条判据必须已经删掉（P-16）"

    n_map, n_sig = D.prefix_impact(st, {"d_bt_lp_linectrl_rx_en": "U_A.U_B"})
    assert (n_map, n_sig) == (1, 0), "寄存器背书的输入名配了前缀也不该算命中"

    # 第二次、且不同：配在某个信号的**输出探针网**上 → 那一个信号确实被影响（assert 带前缀）
    pnet = str(st.models()[0].get("probe_net") or st.models()[0]["name"])
    assert D.prefix_impact(st, {pnet: "U_A.U_B"}) == (1, 1)


# ═════════════ P-18：改覆盖度档之后保持当前选中 ═════════════
def test_p18_changing_coverage_keeps_the_selected_signal(monkeypatch, tmp_path):
    """改「本信号覆盖度」会重跑分析、推一遍骨架清单，而清单整表重建后无条件选第一行 ——
    选中行当场跳走。紧接着那一下拧全局档，改的就是**跳过去那个信号**的档，而用户以为自己
    还在原来那一行上（P-18）。"""
    H.isolate_settings(monkeypatch, tmp_path)
    H.auto_dialogs(monkeypatch)
    w2 = _v2_window(H.mirror_path("btlp"))
    try:
        lp, st = w2.list_panel, w2.state
        names = lp.visible_names()
        assert len(names) >= 3
        target = names[2]                      # 刻意不是第一行
        st.set_current(target)
        H.app().processEvents()
        assert lp.current_name() == target

        def n_list():
            return next(m["n_vectors"] for m in st.models() if m["name"] == target)

        n0 = n_list()
        done = []
        w2.analysisEnded.connect(lambda vid, ok: done.append(1))
        st.coverage("topout").set_sig_cov(target, "exhaustive")     # 「本信号覆盖度」
        st.coverage_touched("topout")
        H.wait_for(lambda: bool(done), timeout_ms=60000)
        H.app().processEvents()
        assert lp.current_name() == target, "改本信号覆盖度后选中行跳走了"
        assert str(st.current_name) == target
        # 选中行留住之后，「用户再点一次」那次重装表也没有了 —— 真值表得自己跟上（C-148）。
        # 分析中那一瞬清单还是骨架（form 为空），那时算出来的 an 退回全局档，绝不能被缓存顶死。
        assert n_list() >= n0
        assert w2.truth_panel.model.columnCount() == n_list(), \
            "清单说 %s 条、真值表画了 %s 列" % (n_list(), w2.truth_panel.model.columnCount())

        # 第二次、且不同：拧全局档 —— 同样不许跳
        done[:] = []
        st.coverage("topout").persist_global_label("穷举")
        st.coverage_touched("topout")
        H.wait_for(lambda: bool(done), timeout_ms=60000)
        H.app().processEvents()
        assert lp.current_name() == target, "改全局档后选中行跳走了"
        assert st.coverage("topout").sig_cov_of(target) == "exhaustive", \
            "被清掉单点档的是跳过去那个信号（P-18 的真实后果）"
        assert w2.truth_panel.model.columnCount() == n_list()
    finally:
        w2.close()


def test_p18_first_row_is_still_the_fallback(monkeypatch, tmp_path):
    """C-044 不变：找不回原来那一行（换表 / 那个名字没了）才退到第一个可见行。"""
    H.isolate_settings(monkeypatch, tmp_path)
    H.auto_dialogs(monkeypatch)
    w2 = _v2_window(H.mirror_path("btlp"))
    try:
        lp = w2.list_panel
        assert lp.select_name("d_no_such_signal_at_all") is False
        w2.state.set_current(lp.visible_names()[2])
        H.app().processEvents()
        lp.reload()
        assert lp.current_name() == lp.visible_names()[2]
        w2.state.set_current("")
        lp.view.setCurrentIndex(lp.view.model().index(-1, -1))
        lp.reload()
        assert lp.current_name() == lp.visible_names()[0]
    finally:
        w2.close()


# ═════════════ P-19：RTL 补充的基名比对用 logic 页 ═════════════
def test_p19_supplement_unknown_names_compare_against_the_logic_page(monkeypatch, tmp_path):
    """C-214 的判据在 v1 是 `{s.out_base.lower() for s in wb.logic}`；v2 拿的是**当前范围的
    清单**（Topout 范围下那是顶层名）。两边差出来的是双向的（P-19）：

      · `d_en_refbuf` 在 logic 页上有、Topout 清单里叫 `d_en_refbuf_ls` → 补一条它的逻辑
        会被误报成「将作为纯新增合成信号生成」，用户以为自己名字写错了；
      · `clk_force_on` 这种直连寄存器 / mux 根在 Topout 清单里有、logic 页上没有 → 补它
        **确实**是纯新增合成信号，而以前一声不吭。
    """
    from dreg_verify.ui import diagnostics as D             # noqa: PLC0415
    from dreg_verify.ui import state as ST                  # noqa: PLC0415
    H.isolate_settings(monkeypatch, tmp_path)
    H.app()
    st = ST.WorkbenchState()
    assert st.load(H.mirror_path("btlp"))
    st.set_models("topout", st.provider("topout").skeleton_models(), True)
    dlg = D.SupplementEditorDialog(st)
    try:
        logic_bases = {s.out_base.lower() for s in st.wb.logic}
        assert "d_en_refbuf" in logic_bases and "clk_force_on" not in logic_bases
        assert "d_en_refbuf" not in {m["name"].lower() for m in st.models()}

        norm = {"d_en_refbuf": {}, "clk_force_on": {}, "d_brand_new_eco_sig": {}}
        assert dlg.unknown_names(norm) == ["clk_force_on", "d_brand_new_eco_sig"]

        # 第二次、且不同：logic 页上真有的那几个，一个都不该被报
        assert dlg.unknown_names({n: {} for n in sorted(logic_bases)}) == []
    finally:
        dlg.close()
