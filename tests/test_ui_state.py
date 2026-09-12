# -*- coding: utf-8 -*-
"""test_ui_state.py —— 会话状态层（`dreg_verify/ui/{state,persist,bus}.py`，C1-a）。

契约 ID（附录 A · STATE 20 条）：C-227 C-228 C-229 C-230 C-233 C-234 C-235 C-236 C-237 C-238
C-239 C-240 C-241 C-242 C-243 C-244 C-246 C-265 C-300 C-301；另覆 C-205 / C-217 / C-245 /
C-247…C-250 / C-282（它们归别的模块，但**保证方**是本层）。
不变量：I-01…I-11（持久化护栏全套）、I-18（高亮总线）。

这一整个文件守的是同一件事：**用户的活不能丢**。红区 designer 手填一屏期望值要半小时，
存盘/恢复这条路上任何一处静默失效，损失都是「重做半天」且当场没人发现——所以每条不变量
都用真 mirror 走一遍真流程，不 mock 存盘。

不起窗（`H.app()` 只为拿进程级 QApplication）。夹具一律用 mirror 生成脚本（公开仓，无真名）。
"""

import json
import os

import pytest

import ui_harness as H                                 # noqa: E402
from dreg_verify import edits as ED                    # noqa: E402
from dreg_verify import exports as X                   # noqa: E402
from dreg_verify import session                        # noqa: E402
from dreg_verify.ui import bus as BUS                  # noqa: E402
from dreg_verify.ui import contracts                   # noqa: E402
from dreg_verify.ui import persist as P                # noqa: E402
from dreg_verify.ui import state as ST                 # noqa: E402
from dreg_verify.ui import terms                       # noqa: E402

LOGIC_SIG = "d_logic_bt_lp_rx_en"          # btlp 镜像里一个 logic 根、可编辑、4 列
REG_SIG = "clk_force_on"                   # 直连寄存器根（A3 §3.2 B 的真实样例就是它）


@pytest.fixture(scope="module")
def qapp():
    return H.app()


@pytest.fixture
def iso(monkeypatch, tmp_path):
    """两份持久化文件指到临时目录 —— 与 `ui_harness.isolate_settings` 对 gui 做的同一件事
    （属性名 `SETTINGS_PATH` / `EDITS_PATH` 与 legacy_gui.py 同名，C1-int 那边一个 helper 能管两套）。"""
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
    """载表 + 把骨架清单灌进去（= app 在 worker 起跑前做的那两步）。"""
    st = ST.WorkbenchState()
    assert st.load(path)
    st.set_models(vid, st.provider(vid).skeleton_models(), partial=True)
    return st


#: A3 §3.1 实测 dump 的全部设置键（表里 `cov_<view_id>` / `maxt_<view_id>` 各算一行，
#: 文里说的「27 个键」就是这么数的；展开成真键是下面 30 条）。同事机上的旧配置长这样。
A3_SETTINGS_27 = {
    "last_excel": r"C:\code\Dreg_verify\mirror_btlp_dreg.xlsx",
    "coverage_logic": "全面", "coverage_mux": "穷举", "coverage": "精简", "max_tests": 77,
    "cov_topout": "全面", "cov_logic": "全面", "cov_mux": "全面", "cov_dft": "全面",
    "cov_iddq": "全面",
    "maxt_topout": 512, "maxt_logic": 512, "maxt_mux": 512, "maxt_dft": 512, "maxt_iddq": 512,
    "cascade_logic": "force", "cascade_mux": "force", "cascade_mode": "force",
    "append_to_logic": False, "append_to_mux": True, "include_risky": True,
    "suffix_override": {r"C:\x\mirror_btlp_dreg.xlsx": {"clk_force_on": False}},
    "probe_prefixes": {r"C:\x\mirror_btlp_dreg.xlsx": {"d_probe_demo": "U_TOP.U_SUB"}},
    "force_signals": {r"C:\x\mirror_btlp_dreg.xlsx": ["d_force_demo"]},
    "logic_overrides": {r"C:\x\mirror_btlp_dreg.xlsx": {"d_demo_base": {"enabled": True}}},
    "export_scope": "pos", "export_comments": True, "export_sv_summary": True,
    "export_owner_in_msg": False, "nets_pages": ["logic", "mux", "topout"],
}


# ═══════════════ I-01 settings 宽容读 ═══════════════
@pytest.mark.contract("C-227")
def test_c227_settings_tolerant_load(iso):
    """I-01 / C-227：旧的 27 个键全可载入，未知键忽略但**原样留着**，坏文件 → `{}`。

    同事机上的配置是升级前那份：有一堆 v2 已经不认识的键（cascade_* / append_to_* /
    suffix_override…）。读的时候报错，同事就打不开工具；读的时候丢掉，他回退旧版本
    就会发现自己的设置没了——两种都不许。"""
    raw = dict(A3_SETTINGS_27)
    raw["某个未来版本才有的键"] = {"x": 1}
    with open(P.SETTINGS_PATH, "w", encoding="utf-8") as f:
        json.dump(raw, f, ensure_ascii=False)

    got = P.load_settings()
    assert got == raw                                   # 旧键 + 未知键，一个不少
    assert len([k for k in got if k in A3_SETTINGS_27]) == len(A3_SETTINGS_27)

    P.patch_settings({"include_risky": False})          # 只改一个键
    again = P.load_settings()
    assert again["include_risky"] is False
    assert again["cascade_logic"] == "force"            # 不认识的键原样留着
    assert again["某个未来版本才有的键"] == {"x": 1}

    with open(P.SETTINGS_PATH, "w", encoding="utf-8") as f:
        f.write("{ 这不是 json")
    assert P.load_settings() == {}                      # 坏文件绝不抛


# ═══════════════ I-02 覆盖度按范围分键 ═══════════════
@pytest.mark.contract("C-229", "C-230")
def test_c229_c230_coverage_keys_per_view(qapp, iso, btlp):
    """I-02：`cov_<view_id>` / `maxt_<view_id>` 按范围各存各的（C-229）；
    legacy 的 `coverage_logic` / `coverage_mux` / `coverage` / `max_tests` **只读一次作迁移、不再写**（C-230）。"""
    P.save_settings({"cov_topout": "穷举", "maxt_topout": 512,
                     "coverage_logic": "精简", "coverage_mux": "穷举",
                     "coverage": "精简", "max_tests": 77})
    st = ST.WorkbenchState()
    assert st.load(btlp)

    assert st.coverage("topout").global_label == "穷举"      # 新键优先
    assert st.coverage("topout").max_tests == 512
    assert st.coverage("logic").global_label == "精简"       # 缺新键 → 迁 coverage_logic
    assert st.coverage("mux").global_label == "穷举"         # 缺新键 → 迁 coverage_mux
    assert st.coverage("dft").global_label == "精简"         # 连 coverage_dft 都没有 → 迁 coverage
    assert st.coverage("logic").max_tests == 77              # 迁 max_tests

    st.coverage("logic").persist_global_label("全面")
    raw = P.load_settings()
    assert raw["cov_logic"] == "全面"                        # 新键写了
    assert raw["coverage_logic"] == "精简"                   # 旧键一个字节没动
    assert raw["coverage"] == "精简" and raw["max_tests"] == 77


# ═══════════════ I-03 导出选项键 ═══════════════
@pytest.mark.contract("C-231", "C-232")
def test_c231_c232_export_option_keys(iso):
    """I-03：四个 `export_*` 键统一默认值走 `exports.EXPORT_OPTION_DEFAULTS`（两套门面此前不一致），
    `nets_pages` 继续读写、不受影响。"""
    assert P.load_export_options() == X.EXPORT_OPTION_DEFAULTS
    P.patch_settings({"nets_pages": ["logic", "mux"]})
    P.store_export_options({"scope": "pos", "comments": True,
                            "sv_summary": True, "owner_in_msg": False})
    raw = P.load_settings()
    assert raw["export_scope"] == "pos" and raw["export_comments"] is True
    assert raw["export_sv_summary"] is True and raw["export_owner_in_msg"] is False
    assert raw["nets_pages"] == ["logic", "mux"]
    assert P.load_export_options() == {"scope": "pos", "comments": True,
                                       "sv_summary": True, "owner_in_msg": False}


# ═══════════════ I-04 诊断配置按路径分桶 ═══════════════
@pytest.mark.contract("C-233")
def test_c233_config_bucketed_by_path(qapp, iso, btlp):
    """I-04 / C-233：探针前缀 / 强制 force / RTL 补充逻辑按 Excel **全路径**分桶，换表自动恢复；
    别的表那一格一个字节不动；`suffix_override`（随入口删的旧键）载入时视而不见、不报错。"""
    other = str(iso / "another_dreg.xlsx")
    P.save_path_map("probe_prefixes", other, {"d_other_demo": "U_X"})
    P.patch_settings({"suffix_override": {other: {"clk_force_on": False}}})

    st = ST.WorkbenchState()
    assert st.load(btlp)                                    # 有 suffix_override 也照样载得进来
    n_map, _n_aff = st.set_probe_prefixes({"D_PROBE_DEMO": "U_TOP.U_SUB"})
    assert n_map == 1 and st.probe_prefixes == {"d_probe_demo": "U_TOP.U_SUB"}
    assert st.set_force_signals({"D_Force_Demo"}) == 1
    st.set_logic_overrides({"d_demo_base": {"enabled": True}})

    raw = P.load_settings()
    assert raw["probe_prefixes"][btlp] == {"d_probe_demo": "U_TOP.U_SUB"}
    assert raw["probe_prefixes"][other] == {"d_other_demo": "U_X"}     # 别的表没被动过
    assert raw["force_signals"][btlp] == ["d_force_demo"]
    assert raw["logic_overrides"][btlp] == {"d_demo_base": {"enabled": True}}
    assert raw["suffix_override"][other] == {"clk_force_on": False}    # 忽略 ≠ 删掉

    st2 = ST.WorkbenchState()
    assert st2.load(btlp)                                   # 换表回来自动恢复
    assert st2.probe_prefixes == {"d_probe_demo": "U_TOP.U_SUB"}
    assert st2.force_signals == {"d_force_demo"}
    assert st2.logic_overrides == {"d_demo_base": {"enabled": True}}
    assert st2.provider("topout")._pp() == {"d_probe_demo": "U_TOP.U_SUB"}   # provider 吃到同一份


@pytest.mark.contract("C-205")
def test_c205_config_change_keeps_checks_and_drops_cache(qapp, iso, btlp):
    """C-205：改诊断配置只让分析作废（缓存清空 + 指纹翻篇），**勾选一个不动**。

    用户挑一遍要导出哪些信号要花不少时间；改个前缀就把勾选重置，等于让他重挑一遍。"""
    st = _loaded(btlp)
    names = [m["name"] for m in st.models("topout")]
    st.set_checked([names[0], names[1]], False)
    before = set(st.checked("topout"))
    assert st.analyze(LOGIC_SIG, "topout") is not None and st._an_cache

    fp = st.fingerprint("topout")
    st.set_probe_prefixes({"d_probe_demo": "U_TOP"})
    assert set(st.checked("topout")) == before                  # 勾选没被碰
    assert st._an_cache == {}                                   # 分析全作废
    assert st.fingerprint("topout") != fp


# ═══════════════ I-05 edits 桶合并写 ═══════════════
def _legacy_bucket():
    """A3 §3.2 A 的真实形状（『排查(旧)』门面写的九段）。"""
    return {
        "edits": {"d_logic_bt_lp_rx_en": [
            {"base_values": {"d_bt_lp_linectrl_rx_en": 1}, "kind": "pos", "designer_expected": 1},
            {"base_values": {"d_bt_lp_linectrl_rx_en": 1}, "kind": "neg", "wrong_value": 0,
             "name": "T0_NEG", "user_added": True}]},
        "neg_only": {"d_logic_bt_lp_rx_en": "first"},
        "mux_expected": {"d_bt_lp_lna_itrim[3:0]": {"c:A=0|d:0=10": 10}},
        "mux_neg": ["d_bt_lp_lna_itrim[3:0]"],
        "mux_data": {"d_bt_lp_lna_itrim[3:0]": {"d_bt_lp_trim_src0": 10}},
        "mux_dropped": {"d_bt_lp_lna_itrim[3:0]": ["c:A=1"]},
        "mux_cleared": [],
        "mux_user_vecs": {"d_bt_lp_lna_itrim[3:0]": [{"assignments": {"c:A": 1}, "exp_value": 10,
                                                      "exp_width": 4, "is_negative": False}]},
        "signals_checked": ["clk_force_on"],
    }


@pytest.mark.contract("C-300", "C-235")
def test_c300_c235_bucket_merge_preserves_legacy(iso):
    """I-05 / C-300：写 edits 文件按路径桶**合并**——只覆盖自己那几格，
    legacy 九段（C-235：不读不删）与**别的 view_id** 子桶逐字节不动；别的表的桶也不动。

    整桶重建是这条路上最贵的一个 bug：同事在『排查(旧)』里手填的期望、或在 logic 视图里
    做的活，会在 v2 保存一次之后**静默消失**，没有任何报错，等到下次导出才发现。"""
    path = r"C:\x\mirror_btlp_dreg.xlsx"
    other = r"C:\x\other_dreg.xlsx"
    bucket = _legacy_bucket()
    bucket["view_edits"] = {"logic": {"d_page_sig": {"kind": "logic", "src_out_name": "d_page_sig",
                                                     "name": "d_page_sig", "renamed": False,
                                                     "cols": [{"name": "T0", "neg": False,
                                                               "vals": {"a": 1}, "exp": 3,
                                                               "auto": 3, "auto_w": 2,
                                                               "user": False, "dft": False,
                                                               "case_index": None}]}}}
    bucket["view_checks"] = {"logic": ["d_page_sig"]}
    allb = {path: bucket, other: {"edits": {"d_elsewhere": []}}}
    with open(P.EDITS_PATH, "w", encoding="utf-8") as f:
        json.dump(allb, f, ensure_ascii=False)
    frozen = json.dumps(bucket, sort_keys=True, ensure_ascii=False)

    new_topout = {"clk_force_on": {"kind": "register", "src_out_name": "clk_force_on",
                                   "name": "clk_force_on", "renamed": False, "cols": []}}
    assert P.write_edits_bucket(path, {"view_edits": {"topout": new_topout},
                                       "view_checks": {"topout": None}})

    now = P.load_edits_all()[path]
    keep = {k: now[k] for k in list(P.LEGACY_SEGMENTS) + ["view_edits", "view_checks"]}
    keep["view_edits"] = {k: v for k, v in keep["view_edits"].items() if k != "topout"}
    assert json.dumps(keep, sort_keys=True, ensure_ascii=False) == frozen   # 逐字节
    assert now["view_edits"]["topout"] == new_topout
    assert "topout" not in now["view_checks"]                # None = 全勾 → 不写这一格（C-243）
    assert P.load_edits_all()[other] == {"edits": {"d_elsewhere": []}}

    # R2-04：`view_edits` 现在按**信号**逐条合并，`{}` = 「这一趟没有要写的信号」= 什么都不做
    # （此前 `{}` 是「删掉这一格」—— 恢复了 0 个信号就把整段清了，那正是 R2-04 的洞）
    assert P.write_edits_bucket(path, {"view_edits": {"topout": {}}})
    assert P.load_edits_all()[path]["view_edits"]["topout"] == new_topout, "空 patch 不许动文件"

    # 某个信号写成 None = 把文件里那一条删掉；一条不剩时那一格才跟着没
    assert P.write_edits_bucket(path, {"view_edits": {"topout": {"clk_force_on": None}}})
    now2 = P.load_edits_all()[path]
    assert "topout" not in now2["view_edits"] and "logic" in now2["view_edits"]
    assert now2["edits"] == bucket["edits"]

    # 整个 view_id 写成 None 仍然是「整格删掉」（`view_checks` 的 C-243 用的就是这条）
    assert P.write_edits_bucket(path, {"view_edits": {"logic": None}})
    assert "view_edits" not in P.load_edits_all()[path], "段里一格不剩 → 段也该没了"

    # C-235：legacy 段只读不删，读出来的就是原文
    assert P.load_legacy_bucket(path) == {k: bucket[k] for k in P.LEGACY_SEGMENTS}
    cnt = P.legacy_bucket_counts(path)
    assert cnt["edits"] == 1 and cnt["mux"] == 1 and cnt["checks"] == 1
    assert P.legacy_bucket_counts(path, kind_of=lambda n: "register")["register"] == 1


# ═══════════════ I-06 换表隔离 + 恢复报告 ═══════════════
@pytest.mark.contract("C-236", "C-237")
def test_c236_c237_switch_table_isolates_edits(qapp, iso, btlp, wl):
    """I-06：编辑按**已载入**的路径分桶（C-236，路径框改了但没点载入 → 仍算旧表的）；
    换表先清空各范围编辑再按本表桶还原（C-237，上一张表的编辑不会按同名串进新表）。"""
    st = _loaded(btlp)
    an = st.analyze(LOGIC_SIG, "topout")
    cols = ED.cols_from_vectors(an, [])
    cols[0]["exp"] = 1
    st.put_edit(LOGIC_SIG, {"kind": an["kind"], "src_out_name": an["src_out_name"],
                            "name": an["name"], "renamed": False, "cols": cols, "an": an})
    assert LOGIC_SIG in st.edits("topout")

    st.excel_path = str(iso / "typed_but_not_loaded.xlsx")   # C-236：只改路径框不载
    st.persist_edits()
    allb = P.load_edits_all()
    assert btlp in allb and st.excel_path not in allb

    assert st.load(wl)                                       # C-237：换表先清空
    assert st.edits("topout") == {} and st.negs("topout") == set()
    st.set_models("topout", st.provider("topout").skeleton_models(), partial=True)
    assert st.edits("topout") == {}                          # wl 桶里没有 → 还是空

    st2 = _loaded(btlp)                                      # 载回来 → 原样还原
    assert LOGIC_SIG in st2.edits("topout")
    assert st2.edit_of(LOGIC_SIG, "topout")["cols"][0]["exp"] == 1


@pytest.mark.contract("C-238", "C-239", "C-240", "C-241")
def test_c238_c239_c240_c241_restore_report(qapp, iso, btlp):
    """I-06 的另一半：恢复时按当前表**重算 auto**（C-238）、找不到的信号点名（C-239）、
    坏数值逐条跳过报个数（C-240）、最后报恢复了几个（C-241）。

    C-238 是防「改表后的陈旧假绿」：Excel 改了逻辑，存盘里的 auto_out 还是旧值，
    照搬回来真值表看着全绿、实际断言错——所以恢复一律重算，存盘的 auto 只当兜底。"""
    ref = _loaded(btlp)
    an = ref.analyze(LOGIC_SIG, "topout")
    real = ED.cols_from_vectors(an, [])
    good = {"name": "T0", "neg": False,
            "vals": {k: int(v) for k, v in real[0]["vals"].items()},
            "exp": 1, "auto": 999, "auto_w": real[0]["auto_w"],     # auto 故意存成错的
            "user": False, "dft": False, "case_index": None}
    broken = dict(good, name="T1", vals={"d_whatever": "手改坏了"})  # C-240：这一条必须被跳过
    ve = {LOGIC_SIG.lower(): {"kind": an["kind"], "src_out_name": an["src_out_name"],
                              "name": an["name"], "renamed": False, "cols": [good, broken]},
          "d_signal_not_in_this_table": {"kind": "logic", "src_out_name": "x", "name": "x",
                                         "renamed": False, "cols": []}}
    with open(P.EDITS_PATH, "w", encoding="utf-8") as f:
        json.dump({btlp: {"view_edits": {"topout": ve}}}, f, ensure_ascii=False)

    st = ST.WorkbenchState()
    msgs = []
    st.statusMessage.connect(msgs.append)
    assert st.load(btlp)
    st.set_models("topout", st.provider("topout").skeleton_models(), partial=True)

    ed = st.edit_of(LOGIC_SIG, "topout")
    assert ed is not None and len(ed["cols"]) == 1                  # C-240 坏的那条跳过了
    assert ed["cols"][0]["auto"] == real[0]["auto"] != 999          # C-238 权威重算
    assert ed["cols"][0]["exp"] == 1                                # 手填期望留住
    joined = " ／ ".join(msgs)
    assert "d_signal_not_in_this_table" in joined                   # C-239 点名
    assert terms.STATUS_RESTORED_FMT.format(n=1) in msgs            # C-241 报个数
    assert ST.STATUS_RESTORE_BAD_FMT.format(n=1) in msgs            # C-240 报个数
    assert msgs.index(terms.STATUS_RESTORED_FMT.format(n=1)) == len(msgs) - 1   # 先点名后计数


@pytest.mark.contract("C-234")
def test_c234_load_restores_view_edits_and_checks(qapp, iso, btlp):
    """C-234：v2 必须能读 `view_edits` / `view_checks` 子桶（A3 §3.2 B 的真实样例形状）。"""
    ve = {REG_SIG: {"kind": "register", "src_out_name": REG_SIG, "name": REG_SIG,
                    "renamed": False,
                    "cols": [{"name": "T0", "neg": False, "vals": {REG_SIG: 0}, "exp": 5,
                              "auto": 0, "auto_w": 1, "user": False, "dft": False,
                              "case_index": None},
                             {"name": "T1", "neg": False, "vals": {REG_SIG: 1}, "exp": None,
                              "auto": 1, "auto_w": 1, "user": False, "dft": False,
                              "case_index": None}]}}
    with open(P.EDITS_PATH, "w", encoding="utf-8") as f:
        json.dump({btlp: {"view_edits": {"topout": ve},
                          "view_checks": {"topout": [REG_SIG, LOGIC_SIG]}}}, f, ensure_ascii=False)

    st = _loaded(btlp)
    ed = st.edit_of(REG_SIG, "topout")
    assert ed is not None and [c["name"] for c in ed["cols"]] == ["T0", "T1"]
    assert ed["cols"][0]["exp"] == 5 and ed["cols"][1]["exp"] is None
    assert ed["kind"] == "register" and ed["an"] is not None
    assert st.is_checked(REG_SIG) and st.is_checked(LOGIC_SIG)
    assert not st.is_checked("d_logic_bt_lp_tsensor")
    assert st.checked_names("topout") == [REG_SIG, LOGIC_SIG] or \
        set(st.checked_names("topout")) == {REG_SIG, LOGIC_SIG}


# ═══════════════ I-07 勾选持久化 / 批量挂起 ═══════════════
@pytest.mark.contract("C-242", "C-243")
def test_c242_c243_checks_persist_and_default_not_written(qapp, iso, btlp):
    """I-07：勾选持久化并在开工具时恢复（C-242）；全勾 = 默认 → **不写桶**（C-243）。"""
    st = _loaded(btlp)
    names = [m["name"] for m in st.models("topout")]
    assert st.checked("topout") is None and st.is_checked(names[0])
    st.persist_edits()
    assert "view_checks" not in P.load_edits_all().get(btlp, {})      # C-243

    st.set_checked([names[0]], False)
    seg = P.load_edits_all()[btlp]["view_checks"]["topout"]
    assert names[0] not in seg and len(seg) == len(names) - 1

    st2 = _loaded(btlp)                                              # C-242 恢复
    assert not st2.is_checked(names[0]) and st2.is_checked(names[1])

    st2.set_checked([names[0]], True)                                # 勾回来 → 回到默认态
    assert st2.checked("topout") is None
    assert "view_checks" not in P.load_edits_all().get(btlp, {})


@pytest.mark.contract("C-244")
def test_c244_bulk_single_write(qapp, iso, btlp, monkeypatch):
    """I-07 / C-244：批量操作挂起逐格存盘，结束统一写**一次**。

    清单底部「全选 / 清空 / 批量加反例」一按就是 200 次状态变化，逐次写盘要几秒，
    界面看起来就是卡死。"""
    st = _loaded(btlp)
    names = [m["name"] for m in st.models("topout")]
    calls = []
    real = P.write_edits_bucket
    monkeypatch.setattr(P, "write_edits_bucket",
                        lambda path, patch: (calls.append(path), real(path, patch))[1])

    with st.suspend_persist():
        for n in names:
            st.set_checked([n], False)
    assert len(calls) == 1                                    # 全程只写一次
    assert len(P.load_edits_all()[btlp]["view_checks"]["topout"]) == 0

    del calls[:]
    for n in names[:3]:
        st.set_checked([n], True)
    assert len(calls) == 3                                    # 不挂起时逐格写（旧行为）


# ═══════════════ I-08 不落盘的东西 ═══════════════
@pytest.mark.contract("C-245")
def test_c245_sig_cov_not_persisted(qapp, iso, btlp):
    """I-08 / C-245：单点档 / 逻辑类型档**刻意不存盘**。

    R25 的实测 bug：存了之后下次开工具会被静默恢复、暗中盖过全局下拉，
    用户表现是「刚开 GUI 改全局对某些信号没反应」，查了一整轮才找到。"""
    st = _loaded(btlp)
    st.coverage("topout").set_sig_cov(LOGIC_SIG, "exhaustive")
    st.coverage("topout").set_form_cov({"boolean": "min"})
    st.coverage_touched("topout")
    st.coverage("topout").persist_global_label("穷举")        # 全局档照存
    st.persist_edits()

    raw = json.dumps(P.load_settings(), ensure_ascii=False)
    assert LOGIC_SIG not in raw and "boolean" not in raw
    assert LOGIC_SIG not in json.dumps(P.load_edits_all(), ensure_ascii=False)

    st2 = ST.WorkbenchState()
    assert st2.load(btlp)
    assert st2.coverage("topout").sig_cov == {} and st2.coverage("topout").form_cov == {}
    assert st2.coverage("topout").global_label == "穷举"       # 全局档确实恢复了


@pytest.mark.contract("C-246")
def test_c246_pytest_never_touches_home(qapp):
    """I-08 / C-246：pytest 下**不写用户真机**的配置/编辑文件（本测试刻意不用 iso 夹具）。

    判据是「路径还是出厂默认值」而不是「在不在 pytest 里」：测试把路径 patch 到 tmp 之后
    写入必须照常生效，否则持久化这件事根本测不到。"""
    assert P.SETTINGS_PATH == session.DEFAULT_SETTINGS_PATH
    assert P.EDITS_PATH == ED.DEFAULT_EDITS_PATH
    assert P.is_isolated() is False
    assert P.may_read_machine_settings() is False      # 连「读真机偏好」都躲开（默认值断言才稳）

    def stamp(p):
        return (os.path.exists(p), os.path.getmtime(p) if os.path.exists(p) else 0)

    before = (stamp(P.SETTINGS_PATH), stamp(P.EDITS_PATH))
    assert P.save_settings({"污染": 1}) is False
    assert P.save_edits_all({"污染": {}}) is False
    assert P.write_edits_bucket("C:/whatever.xlsx", {"view_edits": {"topout": {"x": 1}}}) is False

    st = ST.WorkbenchState()
    st.loaded_path = "C:/whatever.xlsx"
    st._owned.add("topout")
    assert st.persist_edits() is False
    assert (stamp(P.SETTINGS_PATH), stamp(P.EDITS_PATH)) == before


# ═══════════════ I-09 配置文件 schema 冻结 ═══════════════
#: A3 §3.3 实测的顶层键集（导出的完整配置 .json）
CONFIG_TOP_KEYS = {"dreg_verify_config", "edits", "excel", "excel_path", "force_signals",
                   "global", "logic_overrides", "mux_cleared", "mux_data", "mux_dropped",
                   "mux_expected", "mux_neg", "mux_user_vecs", "neg_only", "probe_prefixes",
                   "signals_checked", "suffix_override", "view_checks", "view_edits"}


@pytest.mark.contract("C-247", "C-250")
def test_c247_c250_config_schema_frozen(qapp, iso, btlp):
    """I-09 / C-247 / C-250：导出的配置 .json 带 `dreg_verify_config: 2`，字段集与 A3 §3.3 一致。

    同事之间靠这个文件传配置，字段集一变就是「他导的我导不进来」。"""
    st = _loaded(btlp)
    payload = session.collect_config(
        st.loaded_path, {"include_risky": st.include_risky},
        signals_checked=st.checked_names("topout"),
        probe_prefixes=st.probe_prefixes, force_signals=st.force_signals,
        logic_overrides=st.logic_overrides,
        view_edits={"topout": ED.serialize_view_edits(st.edits("topout"))},
        view_checks={"topout": None})
    assert set(payload) == CONFIG_TOP_KEYS
    assert payload["dreg_verify_config"] == 2 == session.CONFIG_VERSION
    assert payload["excel"] == os.path.basename(btlp)
    assert session.classify_config(payload) == (True, True)


@pytest.mark.contract("C-248", "C-249")
def test_c248_c249_text_formats_roundtrip(iso):
    """I-09 / C-248 / C-249：探针前缀 `.txt` 与 RTL 补充 `.json` 的格式不变（原样往返）。

    这两种文件是红区仿真服务器上人手编辑/Claude 代写的，格式一变，同事手里那份就作废。"""
    mapping = {"d_probe_demo": "U_TOP.U_SUB", "d_other_demo": "U_TOP.U_AFE"}
    text = session.render_probe_prefix_text(mapping)
    assert session.parse_probe_prefix_text(text) == mapping
    merged = session.merge_probe_prefix_text(text, "d_third_demo=U_TOP.U_PLL\n")
    assert session.parse_probe_prefix_text(merged)["d_probe_demo"] == "U_TOP.U_SUB"
    assert session.parse_probe_prefix_text(merged)["d_third_demo"] == "U_TOP.U_PLL"

    names = {"d_force_demo", "d_another_demo"}
    assert session.parse_force_signal_text(session.render_force_signal_text(names)) == names

    tmpl = session.supplement_template()
    js = session.render_supplements_json(tmpl)
    assert session.parse_supplements_json(js) == tmpl
    spec = {"d_demo_base": {"enabled": True, "expr": "A & B",
                            "inputs": [{"var": "A", "raw": "d_in_a"},
                                       {"var": "B", "raw": "d_in_b"}]}}
    norm, errs = session.validate_supplements(spec)
    assert errs == [] and set(norm) == {"d_demo_base"}
    assert session.parse_supplements_json(session.render_supplements_json(norm)) == spec


# ═══════════════ I-10 0 值不许被真值判断吃掉 ═══════════════
@pytest.mark.contract("C-301")
def test_c301_zero_values_survive_roundtrip(iso):
    """I-10 / C-301：`exp: 0` / `wrong_value: 0` 必须活着穿过序列化。

    反例列的错值恰好是 0 是很常见的（1 位信号取反就是 0）。写成 `if v:` 的过滤会把它当空丢掉，
    结果是那条反例列静默变成「没填错值」= 断言必过 = 假绿。"""
    edits = {"d_fake_zero": {"kind": "logic", "src_out_name": "d_fake_zero", "name": "d_fake_zero",
                             "renamed": False,
                             "cols": [{"name": "T0_NEG", "neg": True, "vals": {"a": 0}, "exp": 0,
                                       "auto": 0, "auto_w": 1, "user": True, "dft": False,
                                       "vec": None}]}}
    ser = ED.serialize_view_edits(edits)
    assert ser["d_fake_zero"]["cols"][0]["exp"] == 0                 # 不是 None

    path = r"C:\x\zero_dreg.xlsx"
    assert P.write_edits_bucket(path, {"view_edits": {"topout": ser}})
    back = P.load_edits_all()[path]["view_edits"]["topout"]
    assert back["d_fake_zero"]["cols"][0]["exp"] == 0

    legacy = ED.serialize_rows([{"base_values": {"a": 0}, "kind": "neg", "wrong_value": 0,
                                 "name": "T0_NEG", "user_added": True, "designer_expected": 0}])
    assert legacy[0]["wrong_value"] == 0 and legacy[0]["designer_expected"] == 0


# ═══════════════ I-11 include_risky ═══════════════
@pytest.mark.contract("C-217")
def test_c217_include_risky_default_true(qapp, iso, btlp):
    """I-11 / C-217：缺前缀输入的放行开关默认 **True**（= 引擎此前写死的值），
    改动持久化到顶层键并被 provider 吃到；不改动时产物逐字节不变（byte-gate 那边守）。"""
    assert ST.WorkbenchState().include_risky is True          # 构造即 True，不看盘
    st = _loaded(btlp)
    assert st.include_risky is True and st.provider("topout")._risky() is True

    st.set_include_risky(False)
    assert P.load_settings()["include_risky"] is False
    assert st.provider("topout")._risky() is False

    st2 = ST.WorkbenchState()
    assert st2.load(btlp) and st2.include_risky is False      # 下次开工具恢复


# ═══════════════ C-265 指纹 ═══════════════
@pytest.mark.contract("C-265")
def test_c265_fingerprint_stable_until_cov_or_config_changes(qapp, iso, btlp):
    """C-265：切范围回来不重跑清单；只有覆盖度/上限/诊断配置真变过才重建。

    整表分析在红区那张表上是十几秒，每切一次页重跑一遍是不能接受的。"""
    st = _loaded(btlp)
    st.set_models("topout", st.provider("topout").view_models("max", 256, False, lite=True))
    assert not st.needs_analysis("topout")

    st.set_scope("logic")
    st.set_scope("topout")
    assert not st.needs_analysis("topout")                    # 切页回来：不重跑

    cov = st.coverage("topout")
    cov.persist_global_label("穷举")
    assert st.needs_analysis("topout")                        # 全局档变了 → 重建
    st.set_models("topout", st.models("topout"))

    cov.persist_max_tests(64)
    assert st.needs_analysis("topout")                        # 上限变了
    st.set_models("topout", st.models("topout"))

    cov.set_sig_cov(LOGIC_SIG, "min")                         # 单点档改值（键集没变）
    assert st.needs_analysis("topout")
    st.set_models("topout", st.models("topout"))

    st.set_force_signals({"d_force_demo"})                    # 诊断配置变了
    assert st.needs_analysis("topout")


# ═══════════════ C-228 最近打开 ═══════════════
@pytest.mark.contract("C-228", "C-003")
def test_c228_recent_excels_mru(qapp, iso, btlp, wl):
    """C-228 / C-003：记住最近打开的表（MRU 去重置顶），`last_excel` 顶层键照写；
    清单分析完再把信号数补上（载表那一刻还不知道几个信号）。"""
    st = ST.WorkbenchState()
    assert st.load(btlp) and st.load(wl) and st.load(btlp)
    paths = [r.path for r in st.recent_excels()]
    assert paths[:2] == [btlp, wl] and len(paths) == 2        # 去重 + 置顶
    assert P.load_settings()["last_excel"] == btlp
    assert st.recent_excels()[0].n_signals == 0               # 还没分析

    models = st.provider("topout").skeleton_models()
    st.set_models("topout", models)                           # 一趟跑完（partial=False）
    assert st.recent_excels()[0].n_signals == len(models)


# ═══════════════ 反例 / 编辑回流 ═══════════════
@pytest.mark.contract("C-023", "C-097")
def test_set_neg_adds_one_and_reports_skipped(qapp, iso, btlp):
    """`set_neg` 与 v1 `_toggle_signal_negative` 同语义：已有反例不重复加，取消时只删负向列。

    错值走 `edits.add_negatives`（避开 auto_out 与手填期望两个「正确值」），
    裸 `~auto` 恰好等于手填期望时反例会 PASS = 假绿。"""
    st = _loaded(btlp)
    n_before = len(ED.cols_from_vectors(st.analyze(LOGIC_SIG, "topout"), []))
    added, skipped = st.set_neg(LOGIC_SIG, True)
    assert (added, skipped) == (1, 0)
    assert st.has_negatives(LOGIC_SIG) and LOGIC_SIG in st.negs("topout")
    cols = st.edit_of(LOGIC_SIG, "topout")["cols"]
    assert len(cols) == n_before + 1 and cols[-1]["neg"] and cols[-1]["exp"] is not None

    assert st.set_neg(LOGIC_SIG, True) == (0, 0)              # 再勾一次不叠
    assert st.set_neg(LOGIC_SIG, False) == (1, 0)             # 取消 → 删掉那一条
    assert not st.has_negatives(LOGIC_SIG)
    assert len(st.edit_of(LOGIC_SIG, "topout")["cols"]) == n_before

    assert st.set_neg("pll_lock_indicator", True) == (0, 0)   # 不可编辑的信号：不崩、不改


def test_compute_edited_feeds_exports(qapp, iso, btlp):
    """编辑回流：`compute_edited` → `edits.topout_edit_overrides` 是导出/预览吃的那份。"""
    st = _loaded(btlp)
    st.set_neg(LOGIC_SIG, True)
    edited = st.compute_edited("topout")
    assert LOGIC_SIG in edited and edited[LOGIC_SIG]["vectors"]
    ov = ED.topout_edit_overrides(edited)
    assert ov["vector_overrides"] and any(v.is_negative
                                          for vs in ov["vector_overrides"].values() for v in vs)


def test_partial_models_keep_pending_rows(qapp, iso, btlp):
    """`set_models(partial=True)` 按名合并：回填过的换成结果，其余保留骨架的 pending。"""
    st = _loaded(btlp)
    rows = st.models("topout")
    assert rows and all(r["status"] == "pending" for r in rows)
    one = dict(rows[1], status="ok", status_detail="clean", n_vectors=4)
    st.set_models("topout", [one], partial=True)
    after = st.models("topout")
    assert len(after) == len(rows)                            # 一行都没丢
    assert after[1]["status"] == "ok" and after[0]["status"] == "pending"

    st.update_model("topout", dict(after[0], status="ok", n_vectors=2))
    assert st.model_of(after[0]["name"], "topout")["status"] == "ok"


def test_load_failed_message_is_scrubbed_not_raised(qapp, iso, tmp_path):
    """C-005 / C-272 / I-12：表读不进来 → 一句人话经 `loadFailed`，不抛、不弹窗、不带 traceback。"""
    bad = tmp_path / "not_really_an_excel.xlsx"
    bad.write_bytes(b"this is not a workbook")
    st = ST.WorkbenchState()
    seen = []
    st.loadFailed.connect(seen.append)
    assert st.load(str(bad)) is False
    assert st.wb is None and len(seen) == 1
    assert seen[0].startswith(terms.STATUS_LOAD_FAILED_FMT.split("{")[0])
    assert "Traceback" not in seen[0]


# ═══════════════ I-18 高亮总线 ═══════════════
@pytest.mark.contract("C-282")
def test_c282_highlight_bus_fanout(qapp):
    """I-18 / C-282：点一根线 → 四个订阅方一起收到；比对键小写、剥位宽。"""
    b = BUS.HighlightBus()
    seen = {k: [] for k in ("flow", "chain", "inputs", "truth")}
    for who in seen:
        b.netSelected.connect(lambda net, org, w=who: seen[w].append((net, org)))

    b.select("D_BT_LP_LNA_ITRIM[3:0]", "flow")
    assert all(v == [("d_bt_lp_lna_itrim", "flow")] for v in seen.values())
    assert b.current_net == "d_bt_lp_lna_itrim" and b.current_origin == "flow"

    b.clear("truth")
    assert all(v[-1] == ("", "truth") for v in seen.values())
    assert b.current_net == ""
    assert BUS.net_key("D_X[7:0]") == BUS.net_key("d_x[3]") == "d_x"


@pytest.mark.contract("C-282")
def test_c282_bus_no_reentry(qapp):
    """I-18：同一根线 + 同一个发起方再点不重发（防重入）；换了发起方要重发（新主人得知道）。

    订阅方收到自己发的那条只更新样式、不回写选择——两条规矩合起来才不会无限递归。"""
    b = BUS.HighlightBus()
    seen = []
    b.netSelected.connect(lambda net, org: seen.append((net, org)))

    assert b.select("d_x", "flow") is True
    assert b.select("d_x", "flow") is False                   # 原样再点：不重发
    assert b.select("d_x[1:0]", "flow") is False              # 规整后同一根：也不重发
    assert b.select("d_x", "list") is True                    # 换发起方：重发
    assert b.select("d_y", "list") is True
    assert seen == [("d_x", "flow"), ("d_x", "list"), ("d_y", "list")]

    b.select("d_z", "没这个发起方")                            # 非法 origin → 归一成 none
    assert b.current_origin == "none"
    assert set(contracts.BUS_ORIGINS) >= {"flow", "chain", "inputs", "truth", "list", "none"}


# ═══════════════ 契约面：信号名与 Protocol 对齐 ═══════════════
def test_state_and_bus_declare_every_contract_signal(qapp):
    """`contracts.STATE_SIGNALS` / `BUS_SIGNALS` 里的每个名字都真的是个 Qt 信号。

    信号名是跨模块唯一的约定（视图按名 connect），少一个就是某块界面永远不刷新、且不报错。"""
    st = ST.WorkbenchState()
    for name in contracts.STATE_SIGNALS:
        assert hasattr(st, name) and hasattr(getattr(st, name), "connect"), name
    b = BUS.HighlightBus()
    for name in contracts.BUS_SIGNALS:
        assert hasattr(b, name) and hasattr(getattr(b, name), "connect"), name
    for attr in ("wb", "probe_prefixes", "force_signals", "logic_overrides",
                 "include_risky", "engine_lock"):
        assert hasattr(st, attr), attr                        # ConfigSourceProto（provider 要用）
