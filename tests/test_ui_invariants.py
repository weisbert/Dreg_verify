# -*- coding: utf-8 -*-
"""test_ui_invariants.py —— 架构 §3「跨模块不变量」21 条的**对账与补强**（C4-c）。

§3 每一条都写了「保证方」和「验证测试」。这个文件干两件事：

  ① **对账**：下面这张表把 21 条逐条落到真实存在的测试 nodeid 上。已有测试的断言 ⊇ §3
     表述的，登记即可（不重复造一遍）；
  ② **补强**：已有测试弱于 §3 表述的那几条，在本文件里补一条**更强的**（不动旧测试）。

对账表（`I-xx | 保证方 | 验证测试`；`←本文件` = 本文件补的那一条）：

    I-01 | persist.load_settings = session.load_settings
         | tests/test_ui_state.py::test_c227_settings_tolerant_load                        （已有，⊇）
    I-02 | session.CoverageState + state.load
         | tests/test_ui_state.py::test_c229_c230_coverage_keys_per_view                   （已有）
         | tests/test_ui_invariants.py::test_i02_c229_c230_five_views_and_legacy_migrates_once  ←本文件
    I-03 | export_center 只经 exports.load/store_export_options
         | tests/test_ui_state.py::test_c231_c232_export_option_keys                       （已有，⊇）
         | tests/test_ui_export_center.py::test_i03_export_options_single_source           （已有）
    I-04 | state.set_* → session.save_path_map
         | tests/test_ui_state.py::test_c233_config_bucketed_by_path                       （已有，⊇）
         | tests/test_ui_diagnostics.py::test_i04_diag_config_single_source_in_state        （已有）
    I-05 | persist.write_edits_bucket
         | tests/test_ui_state.py::test_c300_c235_bucket_merge_preserves_legacy            （已有，逐字节，⊇）
    I-06 | state.load + edits.restore_view_edits / coerce_int_map
         | tests/test_ui_state.py::test_c236_c237_switch_table_isolates_edits              （已有）
         | tests/test_ui_state.py::test_c238_c239_c240_c241_restore_report                 （已有，⊇）
    I-07 | state.set_checked / suspend_persist
         | tests/test_ui_state.py::test_c242_c243_checks_persist_and_default_not_written    （已有）
         | tests/test_ui_state.py::test_c244_bulk_single_write                             （已有）
         | tests/test_ui_c1_integration.py::test_c244_bulk_check_single_write              （已有，起窗版）
    I-08 | session.CoverageState；persist 路径 == 默认路径时 no-op
         | tests/test_ui_state.py::test_c245_sig_cov_not_persisted                         （已有）
         | tests/test_ui_state.py::test_c246_pytest_never_touches_home                     （已有，不起窗）
         | tests/test_ui_coverage.py::test_c245_sig_cov_not_in_settings                    （已有）
         | tests/test_ui_invariants.py::test_i08_c245_c246_full_window_never_touches_home   ←本文件（起窗版）
    I-09 | session.collect_config（不改）
         | tests/test_ui_state.py::test_c247_c250_config_schema_frozen                     （已有，走 session）
         | tests/test_ui_state.py::test_c248_c249_text_formats_roundtrip                   （已有）
         | tests/test_ui_invariants.py::test_i09_c247_c250_config_schema_frozen             ←本文件（走 export_center + global 键集）
    I-10 | edits.serialize_view_edits（不改）+ v2 迁移代码用 `is not None`
         | tests/test_ui_state.py::test_c301_zero_values_survive_roundtrip                 （已有，序列化 + 落盘）
         | tests/test_ui_invariants.py::test_i10_c301_c302_zero_values_survive_roundtrip    ←本文件（restore + legacy 迁移）
    I-11 | state.include_risky=True 缺省；provider 传给 topout/pageviews
         | tests/test_ui_state.py::test_c217_include_risky_default_true                    （已有）
         | tests/test_ui_diagnostics.py::test_i11_include_risky_default_true_everywhere      （已有）
         | tests/test_providers.py::test_c217_include_risky_param_default_true_bytes_equal  （已有）+ tools/byte_gate.py
    I-12 | 各视图取文本的唯一入口 terms.scrub；state.loadFailed 发前已 scrub
         | tests/test_ui_terms_scan.py::test_c273_forbidden_terms_only_where_explained_in_place（已有，btlp + 控件类）
         | tests/test_ui_signal_list.py::test_i12_backend_text_is_scrubbed_everywhere        （已有）
         | tests/test_ui_invariants.py::test_i12_c273_no_raw_terms_on_screen_full_window[btlp|wl] ←本文件（两张镜像 + 5 类新面）
    I-13 | terms
         | tests/test_ui_terms_scan.py::test_c271_no_repo_path_git_or_cli_on_screen         （已有，btlp + 控件类）
         | tests/test_ui_terms_scan.py::test_c274_term_table_is_queryable_and_wired_to_scrub （已有）
         | tests/test_ui_invariants.py::test_i13_c271_c274_no_dev_warnings_on_screen[btlp|wl] ←本文件（同上 5 类新面）
    I-14 | 每个视图模块
         | tests/test_ui_c1_integration.py::test_ui_names_all_present                       （已有，正向：注册表 → 控件）
         | tests/test_ui_c4_integration.py::test_i14_all_names_reachable_with_dialogs_open   （已有，C4 两片）
         | tests/test_ui_signal_list.py::test_i14_every_list_objectname_is_present          （已有）
         | tests/test_ui_invariants.py::test_i14_names_registry_matches_widgets              ←本文件（**反向**：控件 → 注册表）
         | tests/test_ui_invariants.py::test_i14_workbench_root_name_is_not_in_the_registry  ←本文件（**xfail strict**：实物破坏一处，证据见该用例）
    I-15 | truth/model.py
         | tests/test_ui_truth_model.py::test_c299_every_write_is_one_undo_step             （已有）
         | tests/test_ui_truth_model.py::test_c299_write_ops_table_covers_every_proto_write_method（已有）
         | tests/test_ui_truth_model.py::test_i15_no_clear_no_reset_outside_load            （已有，⊇）
         | tests/test_ui_truth_view.py::test_c108_column_width_kept_after_edit_and_insert   （已有）
    I-16 | 每个 C0 agent + 每波集成 agent
         | tools/byte_gate.py（CI 闸门，不是 pytest 用例；本轮实测 6/6）
    I-17 | C5：gui.py 成薄壳 → ui.app.main
         | **归 C5-c1**（本轮按分工跳过）；现状 tests/test_ui_app.py::test_c251_module_entry_provides_main
    I-18 | bus.py
         | tests/test_ui_state.py::test_c282_highlight_bus_fanout                           （已有，四订阅方各收一次）
         | tests/test_ui_state.py::test_c282_bus_no_reentry                                 （已有，不回环）
         | tests/test_ui_c2_integration.py::test_c282_bus_highlights_chain_inputs_and_flow_without_echo（已有，真件）
         | tests/test_ui_invariants.py::test_i18_c282_bus_four_subscribers_no_cross_import   ←本文件（静态半条：四模块互不 import）
    I-19 | 静态检查
         | tests/test_ui_c1_integration.py::test_ui_layering                                （已有，⊇）
         | tests/test_ui_c1_integration.py::test_ui_layering_whitelist_has_no_dead_entries  （已有）
    I-20 | export_center / sv_preview / state
         | tests/test_ui_export_center.py::test_i20_every_done_text_names_before_counts     （已有，三处）
         | tests/test_ui_state.py::test_c238_c239_c240_c241_restore_report                  （已有，恢复编辑那一处）
         | tests/test_ui_invariants.py::test_i20_c199_c270_skipped_names_before_counts_everywhere ←本文件（五出口一网打尽）
    I-21 | providers.*（内部持锁）+ state.analyze
         | tests/test_ui_worker.py::test_c276_worker_and_detail_interleave                  （已有，21 信号 + 逐键一致 + 交错证据，⊇）

口径与其余 UI 测试一致：真组合根 / 真 state（→ 真 provider / 真引擎）+ 真 worker，
夹具只用 `tests/` 里的两张 mirror 镜像表（公开仓，**绝不出现真实信号名**）。
模态框一律经 `ui_harness.auto_dialogs`；conftest 的槽异常闸门对本文件生效（文件名 `test_ui_*`）。
"""

import ast
import inspect
import json
import os
import re

import pytest

import ui_harness as H                                          # noqa: E402

pytest.importorskip("PySide6")

from PySide6 import QtCore, QtGui, QtWidgets                    # noqa: E402

from dreg_verify import edits as ED                             # noqa: E402
from dreg_verify import exports as X                            # noqa: E402
from dreg_verify import session                                 # noqa: E402
from dreg_verify.ui import app as A                             # noqa: E402
from dreg_verify.ui import contracts, names, terms              # noqa: E402
from dreg_verify.ui import diagnostics as DG                    # noqa: E402
from dreg_verify.ui import export_center as EC                  # noqa: E402
from dreg_verify.ui import persist as P                         # noqa: E402
from dreg_verify.ui import state as ST                          # noqa: E402
from dreg_verify.ui import sv_preview as SVP                    # noqa: E402

#: btlp 镜像里一个 logic 根（可编辑、有列）
LOGIC_SIG = "d_logic_bt_lp_rx_en"
#: btlp 镜像里的 RO 回读根 —— 按设计不产断言，导出 / 预览都会点名跳过它
RO_SIG = "pll_lock_indicator"
#: wl 镜像里「缺前缀」的那一类根（include_risky 默认 True 时是 `risky-generated`，
#: 关掉开关就变 `needs-prefix`）—— I-12 / I-13 要的就是这种带一屏后端解释文字的信号
WL_RISKY_SIG = "d_wl_rf_lo2g5g_lcbufc0_2g_pfb_band_trim"

_UI_DIR = os.path.dirname(os.path.abspath(A.__file__))


@pytest.fixture(scope="module")
def qapp():
    return H.app()


@pytest.fixture
def iso(monkeypatch, tmp_path):
    """两份持久化文件指到临时目录（与 `test_ui_state.py` 的 `iso` 同一件事）。"""
    monkeypatch.setattr(P, "SETTINGS_PATH", str(tmp_path / "gui_settings.json"))
    monkeypatch.setattr(P, "EDITS_PATH", str(tmp_path / "edits.json"))
    return tmp_path


@pytest.fixture(scope="module")
def btlp():
    return H.mirror_path("btlp")


@pytest.fixture(scope="module")
def wl():
    return H.mirror_path("wl")


def _loaded(path, vid="topout"):
    """载表 + 灌骨架清单（= app 在 worker 起跑前做的那两步）。"""
    st = ST.WorkbenchState()
    assert st.load(path)
    st.set_models(vid, st.provider(vid).skeleton_models(), partial=True)
    return st


# ═══════════════════ I-02：五个范围各写各读；legacy 只迁一次 ═══════════════════
@pytest.mark.contract("C-229", "C-230")
def test_i02_c229_c230_five_views_and_legacy_migrates_once(qapp, iso, btlp):
    """I-02 补强：`cov_<view_id>` / `maxt_<view_id>` **五个范围**各写各读、互不串；
    legacy 的 `coverage_*` / `coverage` / `max_tests` 只在首次载入时读作迁移，
    **之后任何一次改动都不再写回**（已有那条只验了 logic 一格写一次）。

    串格的后果是静默的：用户在 mux 视图把上限调到 512，回 topout 发现清单少了一半信号，
    而界面上两处下拉显示的都是「他自己设的那个值」—— 没人会怀疑是存盘串了格。
    """
    legacy = {"coverage_logic": "精简", "coverage_mux": "穷举",
              "coverage": "全面", "max_tests": 77}
    P.save_settings(dict(legacy))

    st = ST.WorkbenchState()
    assert st.load(btlp)
    # 首次载入：五个范围都从 legacy 迁到（没有 cov_<vid> 新键时才迁）
    assert st.coverage("logic").global_label == "精简"
    assert st.coverage("mux").global_label == "穷举"
    for vid in ("topout", "dft", "iddq"):
        assert st.coverage(vid).global_label == "全面", vid          # 都缺 → 迁 `coverage`
        assert st.coverage(vid).max_tests == 77, vid                 # 迁 `max_tests`

    # 五个范围各写各的（值两两不同，串了格当场看得出来）
    labels = {"topout": "穷举", "logic": "精简", "mux": "全面", "dft": "穷举", "iddq": "精简"}
    maxts = {"topout": 64, "logic": 128, "mux": 256, "dft": 32, "iddq": 500}
    assert set(labels) == set(contracts.VIEW_IDS)
    for vid in contracts.VIEW_IDS:
        st.coverage(vid).persist_global_label(labels[vid])
        st.coverage(vid).persist_max_tests(maxts[vid])

    raw = P.load_settings()
    for vid in contracts.VIEW_IDS:
        assert raw["cov_%s" % vid] == labels[vid], vid
        assert raw["maxt_%s" % vid] == maxts[vid], vid
    # 各读各的：下次开工具五格原样回来
    st2 = ST.WorkbenchState()
    assert st2.load(btlp)
    for vid in contracts.VIEW_IDS:
        assert st2.coverage(vid).global_label == labels[vid], vid
        assert st2.coverage(vid).max_tests == maxts[vid], vid

    # C-230：改了两次（上面一次 + 下面一次），legacy 四个键一个字节都没有被写回
    assert {k: raw.get(k) for k in legacy} == legacy
    st2.coverage("logic").persist_global_label("穷举")
    st2.coverage("mux").persist_max_tests(96)
    raw2 = P.load_settings()
    assert {k: raw2.get(k) for k in legacy} == legacy, "第二次改动把 legacy 键写回去了"
    assert raw2["cov_logic"] == "穷举" and raw2["maxt_mux"] == 96


# ═══════════════════ I-08：起窗跑一串操作也碰不到用户真机 ═══════════════════
def _stamp(path):
    """(在不在, mtime, 大小) —— 三样一起看，改内容不改 mtime 的情况也判得出来。"""
    if not os.path.exists(path):
        return (False, 0, 0)
    return (True, os.path.getmtime(path), os.path.getsize(path))


@pytest.mark.contract("C-245", "C-246")
def test_i08_c245_c246_full_window_never_touches_home(qapp, monkeypatch, tmp_path):
    """I-08 补强：**起一台真窗**、载表、把会存盘的动作做一遍（勾选 / 全局档 / 单点档 /
    诊断配置 / 反例 / include_risky），`~/.dreg_verify*` 两份文件依旧一个字节不动。

    本条刻意**不用** `isolate_settings`：已有的 `test_c246_pytest_never_touches_home`
    只验了直调 `persist.*` 的那一层；真正会伤到同事机器的是「组合根跑起来之后」——
    窗口一路上有十几处 `persist.patch_settings` / `state.persist_edits`，
    哪一处绕过闸门直接写，只有起窗才看得见。

    C-245 的另一半也在这里顺手验了：单点档 / 逻辑类型档改了之后 settings 文件也没被造出来。
    """
    assert P.SETTINGS_PATH == session.DEFAULT_SETTINGS_PATH
    assert P.EDITS_PATH == ED.DEFAULT_EDITS_PATH
    assert P.is_isolated() is False, "这条用例一旦被隔离就什么都验不到了"
    before = (_stamp(P.SETTINGS_PATH), _stamp(P.EDITS_PATH))

    H.auto_dialogs(monkeypatch, save_dir=str(tmp_path))
    w = A.build_window([])
    w.resize(1280, 800)
    w.show()
    try:
        ended = []
        w.analysisEnded.connect(lambda vid, ok: ended.append((vid, ok)))
        w.load_path(H.mirror_path("btlp"))
        assert H.wait_for(lambda: bool(ended)), "worker 没跑完：%s" % (ended,)

        st = w.state
        rows = [m["name"] for m in st.models("topout")]
        assert rows, "清单是空的，下面的动作等于没做"
        st.set_checked(rows[:2], False)                       # C-242 勾选 → 会写 edits 桶
        st.coverage("topout").persist_global_label("穷举")     # C-229 全局档 → 会写 settings
        st.coverage("topout").persist_max_tests(64)
        st.coverage("topout").set_sig_cov(rows[0], "exhaustive")   # C-245 单点档（本就不存盘）
        st.coverage("topout").set_form_cov({"boolean": "min"})     # C-245 逻辑类型档
        st.set_probe_prefixes({"d_probe_demo": "U_TOP.U_SUB"})     # C-233 诊断配置 → 会写 settings
        st.set_force_signals({"d_force_demo"})
        st.set_include_risky(False)                                # C-217 顶层键
        st.set_neg(LOGIC_SIG, True)                                # 编辑 → 会写 edits 桶
        st.persist_edits()
        qapp.processEvents()
    finally:
        w.close()
        qapp.processEvents()

    assert (_stamp(P.SETTINGS_PATH), _stamp(P.EDITS_PATH)) == before, \
        "起窗跑了一串会存盘的动作之后，用户真机的配置 / 编辑文件被碰过了"


# ═══════════════════ I-09：配置 .json 的两层键集冻结 ═══════════════════
#: 删除安全审计 §3.3「实测顶层键」那一行，**硬编码成冻结基线**（同事之间靠这个文件传配置，
#: 少一个键 = 他导的我导不进来；多一个键 = 他的旧版本读到不认识的段）
A3_CONFIG_TOP_KEYS = {
    "dreg_verify_config", "edits", "excel", "excel_path", "force_signals", "global",
    "logic_overrides", "mux_cleared", "mux_data", "mux_dropped", "mux_expected", "mux_neg",
    "mux_user_vecs", "neg_only", "probe_prefixes", "signals_checked", "suffix_override",
    "view_checks", "view_edits",
}
#: 审计 §3.3 的 `global` 段（8 个键）。`cascade_* / append_to_*` 四项 v2 已无界面，
#: **字段照留**——老同事导出的文件才回得来（审计 §3.3 的「v2 注意」）。
A3_CONFIG_GLOBAL_KEYS = {
    "coverage_logic", "coverage_mux", "max_tests",
    "cascade_logic", "cascade_mux", "append_to_logic", "append_to_mux", "include_risky",
}


@pytest.mark.contract("C-247", "C-250")
def test_i09_c247_c250_config_schema_frozen(qapp, iso, btlp):
    """I-09 补强：走 **`export_center.collect_config(state)`**（= 界面真正调的那一条路，
    已有那条走的是底下的 `session.collect_config`）核对两层键集，并真写一次文件再读回来。

    `global` 段的键集之前谁都没验过：v2 把四个已退役的开关从界面上删掉了，
    `collect_config` 少写一格，同事那边导入就会「静默回落成默认值」。
    """
    st = _loaded(btlp)
    payload = EC.collect_config(st)

    assert set(payload) == A3_CONFIG_TOP_KEYS
    assert set(payload["global"]) == A3_CONFIG_GLOBAL_KEYS
    assert payload["dreg_verify_config"] == 2 == session.CONFIG_VERSION
    assert payload["excel"] == os.path.basename(btlp)
    assert session.classify_config(payload) == (True, True)

    # 真写一趟文件再读回来：json 那一跳不许把 None 的两段（view_edits/view_checks）吃掉
    path = str(iso / "mirror_btlp_dreg_config.json")
    session.write_config_file(path, payload)
    with open(path, encoding="utf-8") as f:
        back = json.load(f)
    assert set(back) == A3_CONFIG_TOP_KEYS
    assert set(back["global"]) == A3_CONFIG_GLOBAL_KEYS

    # 有活的时候也一样（编辑 + 取消一个勾选 → 两段有内容，键集仍然分毫不差）
    an = st.analyze(LOGIC_SIG, "topout")
    cols = ED.cols_from_vectors(an, [])
    cols[0]["exp"] = 1
    st.put_edit(LOGIC_SIG, {"kind": an["kind"], "src_out_name": an["src_out_name"],
                            "name": an["name"], "renamed": False, "cols": cols, "an": an})
    st.set_checked([RO_SIG], False)
    payload2 = EC.collect_config(st)
    assert set(payload2) == A3_CONFIG_TOP_KEYS
    assert set(payload2["global"]) == A3_CONFIG_GLOBAL_KEYS
    assert payload2["view_edits"]["topout"][LOGIC_SIG.lower()]["cols"][0]["exp"] == 1
    assert RO_SIG not in payload2["view_checks"]["topout"]


# ═══════════════════ I-10：0 值穿过恢复与旧版迁移两条路 ═══════════════════
def _legacy_zero_bucket():
    """『排查(旧)』门面写的 `edits` 段：一条正向期望 = 0、一条反例错值 = 0。

    1 位信号取反恰好就是 0 —— 这是最常见的那种反例，不是边角数据。"""
    return {"edits": {LOGIC_SIG: [
        {"base_values": {}, "kind": "pos", "name": "T_ZERO", "designer_expected": 0},
        {"base_values": {}, "kind": "neg", "name": "T_ZERO_NEG", "wrong_value": 0,
         "user_added": True},
    ]}}


@pytest.mark.contract("C-301", "C-302")
def test_i10_c301_c302_zero_values_survive_roundtrip(qapp, iso, btlp):
    """I-10 补强：`exp: 0` 不只要活过序列化 + 落盘（已有那条验到这里为止），
    还要活过**恢复**（`edits.restore_view_edits`）与**旧版迁移**（`diagnostics` 用 `is not None`）。

    写成 `if v:` 的那种过滤会把 0 当空丢掉，那条反例列就静默变成「没填错值」=
    断言必过 = 假绿。序列化那一层修好了、恢复那一层还漏，症状一模一样。
    """
    # ── ① 落盘 → 恢复 ──
    st = _loaded(btlp)
    an = st.analyze(LOGIC_SIG, "topout")
    cols = ED.cols_from_vectors(an, [])
    cols[0]["exp"] = 0
    cols[0]["neg"] = True                                   # 反例列，错值恰好是 0
    st.put_edit(LOGIC_SIG, {"kind": an["kind"], "src_out_name": an["src_out_name"],
                            "name": an["name"], "renamed": False, "cols": cols, "an": an})
    st.persist_edits()
    on_disk = P.load_edits_all()[btlp]["view_edits"]["topout"][LOGIC_SIG.lower()]
    assert on_disk["cols"][0]["exp"] == 0                   # 序列化（已有那条也验了）

    st2 = _loaded(btlp)                                     # 下次开工具：真走 restore_view_edits
    ed = st2.edit_of(LOGIC_SIG, "topout")
    assert ed is not None and ed["cols"], "恢复出来的编辑是空的"
    assert ed["cols"][0]["exp"] == 0, "恢复那一跳把 exp=0 吃成了 %r" % ed["cols"][0]["exp"]
    assert ed["cols"][0]["neg"] is True

    # `restore_cols` 这一层单独再验一次（上面那条经过 state，这条是保证方本体）
    restored = ED.restore_cols(an, [{"name": "T0", "neg": True, "vals": {}, "exp": 0,
                                     "auto": 0, "auto_w": 1, "user": True, "dft": False,
                                     "case_index": None}])
    assert restored and restored[0]["exp"] == 0

    # ── ② 旧版迁移（C-302 那条路）：wrong_value: 0 / designer_expected: 0 ──
    bucket = _legacy_zero_bucket()
    st3 = _loaded(btlp)
    plan = DG.plan_legacy_import(st3, bucket)
    assert [r[0] for r in plan.rows] == [LOGIC_SIG], "迁移计划没认出这个信号：%s" % (plan.skipped,)
    n_sig, n_col = DG.apply_legacy_import(st3, plan, bucket)
    assert (n_sig, n_col) == (1, 2)
    got = st3.edit_of(LOGIC_SIG, "topout")["cols"]
    assert [c["exp"] for c in got] == [0, 0], \
        "迁移那一跳把 0 当成空丢了（`if v:` 而不是 `is not None`）：%r" % [c["exp"] for c in got]
    assert [c["neg"] for c in got] == [False, True]

    # 顺手守住 legacy 序列化那一半（0 不能被 `serialize_rows` 的空值过滤吃掉）
    ser = ED.serialize_rows([{"base_values": {}, "kind": "neg", "wrong_value": 0,
                              "name": "T_ZERO_NEG", "designer_expected": 0}])
    assert ser[0]["wrong_value"] == 0 and ser[0]["designer_expected"] == 0


# ═══════════════════ I-12 / I-13：起窗后的「另外五类面」 ═══════════════════
#: 显示**用户自己选的 / 自己导出的文件**的那几处（与 `test_ui_terms_scan` 同一份口径）：
#: 红线①禁的是界面上冒出**仓库**路径 / git / 本工具 CLI，不是「不许显示用户刚打开的那张表」。
_USER_PATH_NAMES = (names.TOP_EXCEL_PATH,)
_USER_PATH_CONTAINERS = (names.EMPTY_RECENT_LIST,)
_USER_PATH_PREFIXES = ("export_last_", "empty_recent_row_")

#: 红线①（V2Spec §6）。唯一例外由裁决⑪ 明文给出：诊断第 2 步那行要在红区仿真服务器上
#: **亲手敲**的命令 —— 它是用户的动作，不是本工具的 CLI。
_SANCTIONED_CLI = "python3 scan_rtl.py"
_REDLINE_1 = (
    (re.compile(r"[A-Za-z]:[\\/]"), "盘符路径"),
    (re.compile(r"(?<![\w.])/(?:home|usr|opt|mnt)/"), "unix 绝对路径"),
    (re.compile(r"\\\\[A-Za-z0-9_.-]+\\"), "UNC 路径"),
    (re.compile(r"(?i)\bgit\b"), "git"),
    (re.compile(r"(?i)\.venv\b"), "虚拟环境目录"),
    (re.compile(r"(?i)\bpytest\b"), "测试框架"),
    (re.compile(r"(?i)\bpython\b"), "命令行"),
    (re.compile(r"(?i)python\s+-m\s"), "本工具的 CLI"),
    (re.compile(r"(?i)\bdreg_verify\s*(?:\.|--)"), "本工具的 CLI"),
    (re.compile(r"发给维护者|发给开发|提交到仓库|请把下面"), "「请把下面发给维护者」"),
)

#: 屏幕上**允许**出现禁用词的三处（与 `test_ui_terms_scan._FORBIDDEN_OK` 同一份登记，
#: 来源一律取 `terms` 的常量 —— 文案改了这里跟着走，不会变成一份过期的副本）
_FORBIDDEN_OK = {
    "CUVUNF": terms.DIAG_CUVUNF_TITLE,
    "claims": terms.EXPORT_ROWS["claims"][0],
    "regmap": terms.EMPTY_DESC,
}


def _under(widget, container_names):
    p = widget
    while p is not None:
        if p.objectName() in container_names:
            return True
        p = p.parent()
    return False


def _skip_user_path(widget):
    on = widget.objectName() if hasattr(widget, "objectName") else ""
    return (on in _USER_PATH_NAMES or on.startswith(_USER_PATH_PREFIXES)
            or (isinstance(widget, QtWidgets.QWidget) and _under(widget, _USER_PATH_CONTAINERS)))


def _extra_strings(root):
    """屏幕上 `test_ui_terms_scan._visible_strings` **收不到**的那五类文字。

    那一份按控件类收 `text()/toPlainText()/toolTip()`（QLabel / 按钮 / 行编辑 /
    QPlainTextEdit / 下拉 / 分组框）。真正最大的一片文字其实在别处：

      ① `QTextBrowser` / `QTextEdit`（右栏「信号链」正文就是它，QLabel 那一族收不到）；
      ② `statusTip` / `whatsThis` / 无障碍名（悬停与读屏看得见的第二套文案）；
      ③ `QAction`（菜单项不是控件）；
      ④ `QTabWidget` 的页签文字与页签 tooltip；
      ⑤ **item model**：清单 12 列 / 输入信号表 / 真值表 / 导出中心六行 —— 这些格子里的
         文字全来自 model 的 `DisplayRole` / `ToolTipRole`，一个控件都没有。
    """
    out = []

    def put(owner, src, value):
        if isinstance(value, str) and value.strip():
            out.append((owner, src, value))

    # ① 富文本正文
    for x in root.findChildren(QtWidgets.QTextEdit):            # QTextBrowser 是它的子类
        if not _skip_user_path(x):
            put(x.objectName(), "toPlainText", x.toPlainText())
    # ② 第二套文案
    for x in root.findChildren(QtWidgets.QWidget):
        if _skip_user_path(x):
            continue
        for getter in ("statusTip", "whatsThis", "accessibleName", "accessibleDescription"):
            fn = getattr(x, getter, None)
            if callable(fn):
                put(x.objectName(), getter, fn())
    # ③ 菜单项
    for a in root.findChildren(QtGui.QAction):
        for getter in ("text", "toolTip", "statusTip", "whatsThis"):
            fn = getattr(a, getter, None)
            if callable(fn):
                put(a.objectName(), "QAction.%s" % getter, fn())
    # ④ 页签
    for tw in root.findChildren(QtWidgets.QTabWidget):
        for i in range(tw.count()):
            put(tw.objectName(), "tabText", tw.tabText(i))
            put(tw.objectName(), "tabToolTip", tw.tabToolTip(i))
    # ⑤ item model
    for v in root.findChildren(QtWidgets.QAbstractItemView):
        if _skip_user_path(v):
            continue
        m = v.model()
        if m is None:
            continue
        who = v.objectName() or m.__class__.__name__
        for role in (int(QtCore.Qt.DisplayRole), int(QtCore.Qt.ToolTipRole)):
            for r in range(min(m.rowCount(), 400)):
                for c in range(min(m.columnCount(), 60)):
                    put(who, "model[%d,%d]" % (r, c), m.index(r, c).data(role))
            for orient in (QtCore.Qt.Horizontal, QtCore.Qt.Vertical):
                n = m.columnCount() if orient == QtCore.Qt.Horizontal else m.rowCount()
                for s in range(min(n, 60)):
                    put(who, "header", m.headerData(s, orient, role))
    return out


#: 两张镜像各选一个信号摊开。**两张都要扫**，理由不同：
#:   · wl  —— 「缺前缀」那一档（`risky-generated`）带着一屏解释文案，是任务点名要的那种；
#:   · btlp —— 它的引擎原文里**真带禁用词**（见 `_RAW_TERM_TARGETS`）：直连寄存器那句
#:            「(RW，regmap)」、RO 回读那句「无 cone，跳过+记账」。只扫 wl 的话，
#:            清单 model 这一片漏了 `terms.scrub` 也照样全绿 —— 靶子根本不在那张表上。
_SCAN_PICKS = {"btlp": RO_SIG, "wl": WL_RISKY_SIG}
#: 这张表里哪些根的**引擎原文**带禁用词（= I-12 的靶子）。没有靶子的扫描只能证明「没扫到」。
_RAW_TERM_TARGETS = {"btlp": ("pll_lock_indicator", "clk_force_on", "en_dig_clk"), "wl": ()}


@pytest.fixture(params=sorted(_SCAN_PICKS), ids=sorted(_SCAN_PICKS))
def scan_win(request, qapp, monkeypatch, tmp_path):
    """真组合根 + 一张镜像 + 选中那张表上最「话多」的信号 + 把右栏 / 抽屉 / 导出中心都摊开。"""
    kind = request.param
    H.isolate_settings(monkeypatch, tmp_path)
    H.auto_dialogs(monkeypatch, save_dir=str(tmp_path))
    w = A.MainWindow()                                   # 不走 build_window：不要 apply_startup 自动载表
    w.resize(1600, 900)
    w.show()
    ended = []
    w.analysisEnded.connect(lambda vid, ok: ended.append((vid, ok)))
    w.load_path(H.mirror_path(kind))
    assert H.wait_for(lambda: bool(ended)), "worker 没跑完：%s" % (ended,)

    alive = []
    w.state.set_current(_SCAN_PICKS[kind])
    qapp.processEvents()
    assert w.state.current_name == _SCAN_PICKS[kind]
    panel = w.list_panel
    panel.set_visible_columns(set(contracts.ListCol))    # 12 列全打开：藏着的列也是界面文案
    for r in range(panel.proxy.rowCount()):              # 展开一个有原因块的行（后端原文最集中）
        nm = panel.proxy.index(r, int(contracts.ListCol.NAME)).data(int(contracts.ListRole.NAME))
        if panel.view.set_expanded(nm):
            break
    w.coverage.open_popover()
    ec = EC.ExportCenterDialog(w.state, w)
    ec.open_options("nets")
    alive.append(ec)
    w.diag_drawer.open_for("")
    for cls in (DG.PrefixEditorDialog, DG.ForceEditorDialog,
                DG.SupplementEditorDialog, DG.LegacyImportDialog):
        alive.append(cls(w.state, w))
    qapp.processEvents()
    yield w, kind
    for d in alive:
        d.close()
    w.close()
    qapp.processEvents()


def _assert_surfaces_reached(strings):
    """先证明这五类面**真的都收到了东西**——扫描类测试自己也得能证明它扫得到。

    「一条违规都没有」与「压根没扫到那一片」在报告上长得一模一样；
    awk 不认 `|` 那次就是这么把「没用到」报错了一轮的。"""
    kinds = {src.split("[")[0] for _o, src, _t in strings}
    for want, what in (("model", "item model（清单 / 输入表 / 真值表 / 导出六行）"),
                       ("header", "表头"),
                       ("QAction.text", "菜单项"),
                       ("toPlainText", "富文本正文（右栏「信号链」）")):
        assert want in kinds, "这一类面一条都没收到：%s（收到的只有 %s）" % (what, sorted(kinds))
    assert len(strings) > 300, "只收到 %d 条，扫描大概没覆盖到几个面" % len(strings)


@pytest.mark.contract("C-273")
def test_i12_c273_no_raw_terms_on_screen_full_window(scan_win, qapp):
    """I-12 补强：两张镜像各起一台真窗，把 `test_ui_terms_scan` **没开的那五类面**
    （富文本正文 / statusTip / 菜单项 / 页签 / item model）全收一遍，
    `terms.FORBIDDEN` 除登记在案的三处外一个都不许出现。

    item model 这一类尤其要紧：清单 12 列与输入信号表里的「原因」「说明」全是引擎原文，
    它们从来没有经过一个 QLabel —— 按控件类扫的那一份一条都收不到。
    """
    win, kind = scan_win
    strings = _extra_strings(win)
    _assert_surfaces_reached(strings)

    # 先证明这套判据抓得到东西（搜索返回空时，第一件事是验证搜索本身能命中已知靶子）
    naked = "本信号无 cone，按 wire 兜底处理，记账后跳过"
    assert [x for x in terms.FORBIDDEN if x in naked], "禁用词表连这句都判不出来"
    # 再证明**这张表上**真有靶子：引擎原文里带禁用词，只是上屏前被 scrub 掉了。
    # 少了这一步，「一个违规都没有」也可能只是因为这张表根本不产生内部术语。
    hit_any = False
    for nm in _RAW_TERM_TARGETS[kind]:
        raw = str((win.state.model_of(nm, "topout") or {}).get("note") or "")
        hits = [x for x in terms.FORBIDDEN if x in raw]
        assert hits, "%s 的引擎原文已经不带禁用词了，这张表的靶子没了：%r" % (nm, raw[:90])
        hit_any = True
    assert hit_any or kind == "wl", "%s 这张表上一个靶子都没登记" % kind

    bad = []
    for owner, src, text in strings:
        for word in terms.FORBIDDEN:
            if word not in text:
                continue
            src_text = _FORBIDDEN_OK.get(word)
            if src_text is not None and src_text in text:
                continue                                 # 就地解释过的三处，逐条放行
            bad.append("%s[%s] 裸用 %r：%r" % (owner or "?", src, word, text[:110]))
    assert not bad, ("这些面上裸露了内部术语（要么换说法、要么经 terms.scrub）：\n%s"
                     % "\n".join(sorted(set(bad))))


@pytest.mark.contract("C-271", "C-274")
def test_i13_c271_c274_no_dev_warnings_on_screen(scan_win, qapp):
    """I-13 补强：同样那五类面上，不得出现 git / 仓库路径 / `.venv` / `pytest` /
    `python -m` —— 唯一例外是诊断第 2 步那行 `python3 scan_rtl.py`（裁决⑪，逐条放行）。

    「别加开发者视角的提醒」是 owner 明说过的红线：同事看到界面上冒出仓库路径或
    「请把下面发给维护者」，第一反应是这工具还没做完。
    """
    win, _kind = scan_win
    strings = _extra_strings(win)
    _assert_surfaces_reached(strings)

    probe = [why for rx, why in _REDLINE_1
             if rx.search(r"照着 C:\code\repo 跑 git log，再 .venv 里 python -m dreg_verify.cli")]
    assert len(probe) >= 5, "红线①的正则连一条明显违规的话都抓不全：%s" % probe

    bad = []
    for owner, src, text in strings:
        for rx, why in _REDLINE_1:
            if not rx.search(text):
                continue
            if _SANCTIONED_CLI in text and why in ("命令行",):
                continue                                 # 裁决⑪ 的那一条
            bad.append("%s[%s] %s：%r" % (owner or "?", src, why, text[:110]))
    assert not bad, "这些面上出现了红线①禁的东西：\n%s" % "\n".join(sorted(set(bad)))

    # 例外那一条确实还在（放行不能变成「顺手把它也删了」）
    assert H.find(win.diag_drawer, names.DIAG_STEP2_BOX).text() == _SANCTIONED_CLI
    # C-274：红线本身是可查的四条，不是散在各处的口头约定
    assert len(terms.REDLINES) == 4 and all(str(x).strip() for x in terms.REDLINES)


# ═══════════════════ I-14：反向对账（控件 → 注册表）═══════════════════
def _dynamic_name_patterns():
    """`names.py` 里每个 `fmt_*` 函数 → 一条正则。

    做法是拿探针值真调一次再把探针换成通配：这样注册表新增一个 `fmt_*`，
    本条自动认得，不必在测试里手抄一份键域（手抄的那份一定会过期）。"""
    pats = []
    for key, fn in sorted(vars(names).items()):
        if not key.startswith("fmt_") or not callable(fn):
            continue
        n = len(inspect.signature(fn).parameters)
        got, used = None, None
        for probe in (["zzprobe%dzz" % i for i in range(n)], [987650 + i for i in range(n)]):
            try:
                got, used = fn(*probe), probe
            except Exception:                            # noqa: BLE001  换另一种探针再试
                continue
            break
        assert got is not None, "names.%s 两种探针都喂不进去" % key
        rx = re.escape(str(got))
        for p in used:
            rx = rx.replace(re.escape(str(p)), r"[A-Za-z0-9_]+")
        pats.append((key, re.compile("^%s$" % rx)))
    assert len(pats) >= 10, "只认出 %d 个动态名模板" % len(pats)
    return pats


#: Qt 自己给内部零件起的名（viewport / 滚动条容器 / 行编辑的清空按钮 / splitter 手柄 …）。
#: 两种前缀都是 Qt 源码里的约定，不是我们的注册表该管的东西。
_QT_INTERNAL = ("qt_", "_q_")
#: **实物里确实没登记**的那一个（证据见下面的 xfail 用例）：`ui/app.py:211` 拿
#: `names.WIN_WORKBENCH + "_root"` 拼出来的工作台根容器名。放行是**逐条写下来**的，
#: 不是把规则放宽；哪天它进了注册表，下面那条 `strict=True` 的 xfail 会当场变红提醒删掉这里。
_UNREGISTERED_KNOWN = {"win_workbench_root": "ui/app.py:211 用 WIN_WORKBENCH + \"_root\" 拼的"}


def _unregistered_object_names(root, allow_known=True):
    """窗口里所有**不在** `names.py` 注册表（含 `fmt_*` 模板）里的控件 / 菜单项 objectName。"""
    known = set(names.all_names().values())
    pats = _dynamic_name_patterns()
    bad = {}
    owners = (list(root.findChildren(QtWidgets.QWidget))
              + list(root.findChildren(QtGui.QAction)) + [root])
    for x in owners:
        on = x.objectName()
        if not on or on.startswith(_QT_INTERNAL) or on in known:
            continue
        if allow_known and on in _UNREGISTERED_KNOWN:
            continue
        if any(rx.match(on) for _k, rx in pats):
            continue
        bad.setdefault(on, x.__class__.__name__)
    return bad


def test_i14_names_registry_matches_widgets(qapp, monkeypatch, tmp_path):
    """I-14 的**反向**半条：起窗 + 把所有对话框 / 弹层打开之后，窗口里每个有 objectName 的
    对象，它的名字都得在 `names.all_names()`（或某个 `fmt_*` 模板）里。

    正向那条（`test_ui_names_all_present`）守的是「注册表里的名字都找得到」；
    反向这条守的是「屏幕上没有注册表不认识的名字」。少了它，谁随手
    `setObjectName("tmp_btn")` 都不会被发现，而测试与 `ui_harness.find` 全靠名字——
    没登记的名字等于一个谁都没验过的控件。
    """
    H.isolate_settings(monkeypatch, tmp_path)
    H.auto_dialogs(monkeypatch, save_dir=str(tmp_path))
    w = A.build_window([])
    w.resize(1600, 900)
    w.show()
    alive = []
    try:
        ended = []
        w.analysisEnded.connect(lambda vid, ok: ended.append((vid, ok)))
        w.load_path(H.mirror_path("btlp"))
        assert H.wait_for(lambda: bool(ended)), "worker 没跑完"

        panel = w.list_panel
        assert panel.select_first_visible()
        for r in range(panel.proxy.rowCount()):          # 展开一个有原因块的行
            nm = panel.proxy.index(r, int(contracts.ListCol.NAME)).data(
                int(contracts.ListRole.NAME))
            if panel.view.set_expanded(nm):
                break
        w.coverage.open_popover()
        qapp.processEvents()
        tp = w.truth_panel
        assert tp.model.columnCount() > 0, "真值表一列都没有"
        tp.build_context_menu(0, 0)                      # 右键菜单只在交互中存在
        tp.grid.edit(tp.model.index(tp.model.rowCount() - 1, 0))
        ec = EC.ExportCenterDialog(w.state, w)
        ec.open_options("nets")
        alive.append(ec)
        alive.append(EC.ExportDoneDialog(contracts.ExportRunResult(
            skipped=[("d_fake", "输入缺层级前缀")], accounted=["d_ro"],
            errors=[("sv", "写不出去")]), w))
        w.diag_drawer.open_for("")
        for cls in (DG.PrefixEditorDialog, DG.ForceEditorDialog,
                    DG.SupplementEditorDialog, DG.LegacyImportDialog):
            alive.append(cls(w.state, w))
        qapp.processEvents()

        # 先证明这条扫描判得出「没登记」（不然「一个都没有」可能只是扫描写错了）
        probe = QtWidgets.QWidget(w)
        probe.setObjectName("zz_not_in_names_registry_xyz")
        assert "zz_not_in_names_registry_xyz" in _unregistered_object_names(w)
        probe.setParent(None)
        probe.deleteLater()
        qapp.processEvents()

        bad = _unregistered_object_names(w)
        assert not bad, ("这些 objectName 不在 names.py 里（要么登记进注册表，"
                         "要么别起名）：\n%s"
                         % "\n".join("%s（%s）" % (k, v) for k, v in sorted(bad.items())))
        # 登记表不许留没人用的死条目（放行一条就得有一条真的还在）
        still = set(_unregistered_object_names(w, allow_known=False))
        assert set(_UNREGISTERED_KNOWN) <= still, \
            "这些放行项屏幕上已经不出现了，该删：%s" % sorted(set(_UNREGISTERED_KNOWN) - still)
    finally:
        for d in alive:
            d.close()
        w.close()
        qapp.processEvents()


@pytest.mark.xfail(strict=True,
                   reason="I-14 被 ui/app.py 破坏：工作台根容器名 `win_workbench_root` 是 "
                          "`app.py:211` 用 `names.WIN_WORKBENCH + \"_root\"` 拼出来的，"
                          "没有进 names.py，`names.all_names()` 里查不到")
def test_i14_workbench_root_name_is_not_in_the_registry(qapp):
    """I-14 的那一处**实物破坏**，单独立一条 `strict=True` 的 xfail 钉住。

    `setObjectName(names.WIN_WORKBENCH + "_root")` 这种拼法看着无害，坏在两头：
    注册表里查不到这个名字（`test_ui_names_all_present` 那条正向用例扫不到它），
    反向用例又只能把它放进白名单 —— 于是这个控件谁都没验过。
    修的成本是一行（`names.py` 加一个 `WIN_WORKBENCH_ROOT`），但那要动 `dreg_verify/`，
    归 C4-int / C5；这里只负责让它别再悄无声息。"""
    assert "win_workbench_root" in set(names.all_names().values())


# ═══════════════════ I-18：四个订阅方互不 import（静态半条）═══════════════════
#: 「当前线网」的四个订阅**视图**（架构 §2.5）→ 它们住在哪三个模块里
#: （chain 与 inputs 是右栏的两块，同住 `side_panel.py`）
_BUS_ORIGIN_HOME = {"chain": "side_panel.py", "inputs": "side_panel.py",
                    "flow": "sigflow_view.py", "truth": "truth/panel.py"}
#: 互不 import 的扫描范围：三个订阅方模块 + 清单区。
#: 清单区**不是**订阅方（`contracts.BUS_ORIGINS` 里的 `"list"` 是预留的、暂无实现），
#: 但它与另外三块同属「点一个东西、别处跟着亮」这条链，串了 import 一样会绕开 bus。
_BUS_SUBSCRIBERS = tuple(sorted(set(_BUS_ORIGIN_HOME.values()) | {"signal_list.py"}))


def _imported_names(path):
    """这个文件 import 了哪些模块名（只取最后一段，`from .. import terms as T` 记 `terms`）。"""
    with open(path, encoding="utf-8") as f:
        tree = ast.parse(f.read())
    got = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.module:
                got.add(str(node.module).split(".")[-1])
            for a in node.names:
                got.add(a.name.split(".")[-1])
        elif isinstance(node, ast.Import):
            for a in node.names:
                got.add(a.name.split(".")[-1])
    return got


@pytest.mark.contract("C-282")
def test_i18_c282_bus_four_subscribers_no_cross_import(qapp):
    """I-18 的静态半条：「当前线网」的四个订阅方**两两互不 import**。

    动态半条（一次 `bus.select` → 另外三方各收一次、发起方自己不回环）已由
    `test_ui_state.py::test_c282_highlight_bus_fanout` / `test_c282_bus_no_reentry` 与
    `test_ui_c2_integration.py::test_c282_bus_highlights_chain_inputs_and_flow_without_echo` 覆盖；
    这里补的是「别的路会不会偷偷存在」—— 只要有一条直连，四方联动就会在某个组合下
    双向递归或漏刷新，而动态测试只看得到当下这一种连法。

    已有的 `test_ui_side_panel.py::test_c282_side_panel_never_imports_sibling_views`
    只守右栏一个方向；这里是四个方向全守。"""
    mods = {}
    for rel in _BUS_SUBSCRIBERS:
        path = os.path.join(_UI_DIR, *rel.split("/"))
        assert os.path.exists(path), path
        mods[rel] = _imported_names(path)

    # 先证明这条扫描扫得到东西：组合根确实 import 着这四个
    app_mods = _imported_names(os.path.join(_UI_DIR, "app.py"))
    for rel in _BUS_SUBSCRIBERS:
        stem = rel.split("/")[-1][:-3]
        assert stem in app_mods or "truth" in app_mods, \
            "扫描器在 app.py 里没看到 %s，这条规则等于没验" % stem

    bad = []
    for rel, got in sorted(mods.items()):
        for other in _BUS_SUBSCRIBERS:
            if other == rel:
                continue
            stem = other.split("/")[-1][:-3]
            if stem in got:
                bad.append("%s import 了兄弟订阅方 %s（当前线网只许走 bus）" % (rel, stem))
    assert not bad, "\n".join(bad)

    # 反过来：四个订阅方各自的 origin 确实落在它该在的模块里
    # （不 import 兄弟 ≠ 根本没接总线；两条一起才说明「只经 bus」）
    for origin, rel in sorted(_BUS_ORIGIN_HOME.items()):
        path = os.path.join(_UI_DIR, *rel.split("/"))
        with open(path, encoding="utf-8") as f:
            src = f.read()
        assert re.search(r"bus\.(select|clear)\(", src), "%s 根本没往 bus 上发过" % rel
        assert '"%s"' % origin in src, "%s 里找不到 origin %r" % (rel, origin)
        assert origin in contracts.BUS_ORIGINS


# ═══════════════════ I-20：五个出口，名字都在计数之前 ═══════════════════
def _exit_export_summary(qapp, iso, btlp, tmp_path):
    """出口①：导出**前**摘要（裁决⑯：点名可展开，不是只给个数）。"""
    st = _loaded(btlp)
    d = EC.ExportCenterDialog(st)
    d.resize(1080, 520)
    for k, cb in d.checks.items():
        cb.setChecked(k == "sv")
    d.refresh_summary()
    d.show()
    qapp.processEvents()
    text = d.skipped_text()
    assert d.skipped_btn.isVisible(), "有信号会被跳过，摘要却没给点名的入口（= 只给个数）"
    assert text.strip(), "点名块是空的"
    name = str(d.plan().will_skip[0][0])
    reason = terms.scrub(str(d.plan().will_skip[0][1])).split("\n")[0].strip()
    d.close()
    return ("导出前摘要", text, name, reason)


def _exit_done_dialog(qapp, iso, btlp, tmp_path):
    """出口②：导出完成弹层（C-167 / C-199）。"""
    res = contracts.ExportRunResult(
        skipped=[("d_fake_skipped", "输入缺层级前缀")], accounted=["d_fake_ro"],
        errors=[], out_dir=str(tmp_path))
    d = EC.ExportDoneDialog(res)
    text = d.text()
    body = d.skipped_text(full=True).splitlines()
    assert body and body[0] == "d_fake_skipped", "跳过块第一行不是信号名：%r" % body[:2]
    d.close()
    return ("完成弹层", text, "d_fake_skipped", terms.DONE_WRITTEN)


def _exit_sv_preview_tail(qapp, iso, btlp, tmp_path):
    """出口③：`.sv` 预览尾（C-070）+ 状态栏计数（C-172）—— 正文点名在上，计数在下。"""
    st = _loaded(btlp)
    st.set_current(LOGIC_SIG)
    panel = SVP.SvPreview(st)
    panel.resize(900, 600)
    seen = []
    panel.statusMessage.connect(seen.append)
    panel.set_mode(SVP.MODE_CHECKED)
    qapp.processEvents()
    assert seen, "预览完没报计数"
    text = panel.text() + "\n" + seen[-1]
    assert terms.SV_SKIPPED_TAIL_HEAD in panel.text(), "预览正文里没有跳过点名那一段"
    panel.deleteLater()
    return (".sv 预览尾", text, RO_SIG, "断言块")


def _exit_import_report(qapp, iso, btlp, tmp_path):
    """出口④：导入配置结果（C-194）。"""
    rep = EC.ImportReport()
    rep.missing = [("d_gone_a", "当前表里没有这个信号"), ("d_gone_b", "当前表里没有这个信号")]
    rep.counts = ["已导入完整配置：恢复了 0 个信号的手填编辑"]
    text = rep.text()
    assert text.index("d_gone_a") < text.index("共 2 个"), \
        "点名行排到了「共 N 个」那句后面：\n%s" % text
    return ("导入配置结果", text, "d_gone_a", rep.counts[0])


def _exit_restore_status(qapp, iso, btlp, tmp_path):
    """出口⑤：恢复编辑的状态栏（C-239 点名 → C-240 / C-241 计数）。"""
    ve = {"d_signal_not_in_this_table": {"kind": "logic", "src_out_name": "x", "name": "x",
                                         "renamed": False, "cols": []}}
    ref = _loaded(btlp)
    an = ref.analyze(LOGIC_SIG, "topout")
    real = ED.cols_from_vectors(an, [])
    ve[LOGIC_SIG.lower()] = {
        "kind": an["kind"], "src_out_name": an["src_out_name"], "name": an["name"],
        "renamed": False,
        "cols": [{"name": "T0", "neg": False,
                  "vals": {k: int(v) for k, v in real[0]["vals"].items()},
                  "exp": 1, "auto": real[0]["auto"], "auto_w": real[0]["auto_w"],
                  "user": False, "dft": False, "case_index": None}]}
    with open(P.EDITS_PATH, "w", encoding="utf-8") as f:
        json.dump({btlp: {"view_edits": {"topout": ve}}}, f, ensure_ascii=False)

    st = ST.WorkbenchState()
    msgs = []
    st.statusMessage.connect(msgs.append)
    assert st.load(btlp)
    st.set_models("topout", st.provider("topout").skeleton_models(), partial=True)
    assert msgs, "恢复编辑一句话都没报"
    return ("恢复编辑状态栏", "\n".join(msgs), "d_signal_not_in_this_table",
            terms.STATUS_RESTORED_FMT.format(n=1))


_I20_EXITS = (_exit_export_summary, _exit_done_dialog, _exit_sv_preview_tail,
              _exit_import_report, _exit_restore_status)


@pytest.mark.contract("C-199", "C-270")
@pytest.mark.parametrize("build", _I20_EXITS, ids=[f.__name__[6:] for f in _I20_EXITS])
def test_i20_c199_c270_skipped_names_before_counts_everywhere(build, qapp, iso, btlp, tmp_path):
    """I-20 补强：§3 点名的**五个出口一网打尽**（已有那条覆盖其中三处，
    恢复编辑那处在 `test_ui_state.py`，`.sv` 预览尾此前没人从「顺序」这个角度验过）。

    红线②：跳过 / 过滤的东西一律先点名字和原因，计数放后面。只给「跳过了 3 个」，
    工程师得把 Excel 从头翻一遍才知道是哪三个 —— 那三个通常正是最需要他处理的。
    """
    label, text, name, after = build(qapp, iso, btlp, tmp_path)
    assert name in text, "%s：一个名字都没点（只给了个数）" % label
    assert after in text, "%s：计数 / 原因那一段不见了（断言基准没了）" % label
    assert text.index(name) < text.index(after), \
        "%s：计数排在了名字前面：\n%s" % (label, text[:400])
