# -*- coding: utf-8 -*-
"""test_session.py — 会话状态层（dreg_verify/session.py）单元测试。

session.py 是 GUI v2「换包不换引擎」的地基：设置文件 IO、覆盖度三层状态、配置导入导出、
诊断配置校验——全部 Qt-free。本文件绝大多数用例【不起 Qt】，直接构造数据测；
只有最后两组「两套门面口径一致」的对照测试要起一次 offscreen GUI 拿现值比对。

夹具一律用仓库里的 mirror 生成脚本（公开仓，不得出现真实信号名）。
"""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import make_mirror_btlp                              # noqa: E402  BT_LP 镜像（Topout 门面）
import make_mirror_excel                             # noqa: E402  WL 镜像（判据二硬案）
from dreg_verify import excel_model as M             # noqa: E402
from dreg_verify import forms as FORMS               # noqa: E402
from dreg_verify import resolver as R                # noqa: E402
from dreg_verify import session                      # noqa: E402
from dreg_verify import topout as T                  # noqa: E402


def test_session_is_qt_free():
    """⭐ 地基不变量：会话状态层不得 import PySide6/gui——v2 换界面时这一层原样复用。"""
    import ast
    src = open(session.__file__, encoding="utf-8").read()
    mods = set()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Import):
            mods.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            mods.add(node.module or "")
            mods.update("%s.%s" % (node.module or "", a.name) for a in node.names)
    assert not any("PySide6" in m or m.endswith("gui") for m in mods), sorted(mods)


# ───────────────────────────── 设置文件 IO ─────────────────────────────
def test_load_settings_missing_or_broken_gives_empty(tmp_path):
    """没文件/坏 JSON/不是对象 → {}，绝不抛（每次开窗都读，抛了就开不出来）。"""
    assert session.load_settings(str(tmp_path / "nope.json")) == {}
    p = tmp_path / "broken.json"
    p.write_text("{ not json", encoding="utf-8")
    assert session.load_settings(str(p)) == {}
    p.write_text("[1, 2]", encoding="utf-8")
    assert session.load_settings(str(p)) == {}


def test_save_settings_roundtrip_and_pytest_guard(tmp_path):
    """pytest 下默认不落盘(防污染用户真实配置)；显式关掉守卫才真写，写完读得回来。"""
    p = str(tmp_path / "s.json")
    assert session.save_settings({"a": 1}, p) is False           # pytest 守卫
    assert not os.path.exists(p)
    assert session.save_settings({"a": 1, "last_excel": "x.xlsx"}, p,
                                 skip_under_pytest=False) is True
    assert session.load_settings(p) == {"a": 1, "last_excel": "x.xlsx"}


def test_save_settings_bad_path_is_silent(tmp_path):
    """写不进去(路径是目录)也只返回 False，不抛——配置存不下不该打断正在干的活。"""
    assert session.save_settings({"a": 1}, str(tmp_path), skip_under_pytest=False) is False


def test_last_excel_roundtrip(tmp_path):
    p = str(tmp_path / "s.json")
    assert session.load_last_excel(p) is None
    session.save_settings({"last_excel": "d:/x/mirror_btlp_dreg.xlsx"}, p, skip_under_pytest=False)
    assert session.load_last_excel(p) == "d:/x/mirror_btlp_dreg.xlsx"


@pytest.mark.contract("C-003", "C-228")
def test_c003_c228_recent_excels_mru():
    """N4（冲突⑥）：空态「最近打开」要多条 → settings 新键 recent_excels，last_excel 照写。

    上限 5 / 置顶去重 / ts 是 ISO / 信号数在分析完成时补写 / 缺键时用 last_excel 造单条。"""
    store = {}
    io = {"load": lambda: dict(store), "save": lambda d: store.clear() or store.update(d)}

    assert session.recent_excels(load=io["load"]) == []                  # 两个键都没有 → 空
    store["last_excel"] = "d:/old/mirror_wl_dreg.xlsx"                   # 只有旧的顶层键（旧版本存的）
    fallback = session.recent_excels(load=io["load"])
    assert fallback == [{"path": "d:/old/mirror_wl_dreg.xlsx", "ts": "", "n_signals": 0}]

    session.push_recent_excel("d:/a/mirror_btlp_dreg.xlsx", **io)
    assert store["last_excel"] == "d:/a/mirror_btlp_dreg.xlsx"           # 顶层键照写（回退旧版本仍能恢复）
    one = session.recent_excels(load=io["load"])[0]
    assert one["path"] == "d:/a/mirror_btlp_dreg.xlsx" and one["n_signals"] == 0
    import datetime
    datetime.datetime.fromisoformat(one["ts"])                           # ts 是可解析的 ISO 串

    session.push_recent_excel("d:/b/mirror_wl_dreg.xlsx", n_signals=21, **io)
    assert [it["path"] for it in session.recent_excels(load=io["load"])] == \
        ["d:/b/mirror_wl_dreg.xlsx", "d:/a/mirror_btlp_dreg.xlsx"]       # 最新的置顶

    # 分析完成补写信号数（载表那一刻还不知道几个信号）
    assert session.update_recent_count("d:/a/mirror_btlp_dreg.xlsx", 9, **io) is True
    assert session.recent_excels(load=io["load"])[1]["n_signals"] == 9
    assert session.update_recent_count("d:/查无此表.xlsx", 3, **io) is False    # 不凭空造条目

    # 再开一次 a → 置顶且【保留】上次记下的信号数（n_signals 不传 = 别清零）
    session.push_recent_excel("D:\\A\\mirror_btlp_dreg.xlsx", **io)      # 大小写/分隔符不同 = 同一张表
    items = session.recent_excels(load=io["load"])
    assert len(items) == 2 and items[0]["n_signals"] == 9

    for i in range(6):                                                   # 上限 5：最老的挤出去
        session.push_recent_excel("d:/m/mirror_%d.xlsx" % i, **io)
    items = session.recent_excels(load=io["load"])
    assert len(items) == 5
    assert [it["path"] for it in items] == ["d:/m/mirror_%d.xlsx" % i for i in (5, 4, 3, 2, 1)]

    store[session.RECENT_KEY] = ["垃圾", {"no_path": 1}, {"path": "d:/ok.xlsx", "n_signals": "x"}]
    assert session.recent_excels(load=io["load"]) == \
        [{"path": "d:/ok.xlsx", "ts": "", "n_signals": 0}]               # 脏数据只丢不抛


@pytest.mark.contract("C-198")
def test_c198_last_export_per_kind():
    """N5（冲突⑦）：导出中心「上次导出到哪」= settings 新键 last_export，6 种交付物各一格。"""
    store = {}
    io = {"load": lambda: dict(store), "save": lambda d: store.clear() or store.update(d)}

    for kind in session.EXPORT_KINDS:
        assert session.last_export(kind, load=io["load"]) is None        # 没导过 → 界面写「从未导出」

    for kind in session.EXPORT_KINDS:                                    # 6 格互不干扰
        session.record_last_export(kind, "d:/out/%s.bin" % kind, **io)
    assert set(store[session.LAST_EXPORT_KEY]) == set(session.EXPORT_KINDS)
    for kind in session.EXPORT_KINDS:
        got = session.last_export(kind, load=io["load"])
        assert got["path"] == "d:/out/%s.bin" % kind
        import datetime
        datetime.datetime.fromisoformat(got["ts"])

    session.record_last_export("sv", "d:/out2/mirror_wl_dreg.sv", **io)  # 同 kind 覆盖，别的不动
    assert session.last_export("sv", load=io["load"])["path"] == "d:/out2/mirror_wl_dreg.sv"
    assert session.last_export("nets", load=io["load"])["path"] == "d:/out/nets.bin"

    assert session.record_last_export("sv", "   ", **io) is None         # 空路径不写
    assert session.last_export("sv", load=io["load"])["path"] == "d:/out2/mirror_wl_dreg.sv"
    store[session.LAST_EXPORT_KEY] = "不是 dict"
    assert session.last_export("sv", load=io["load"]) is None            # 脏数据只丢不抛


def test_save_path_map_sets_and_clears():
    """按 Excel 路径分桶的诊断配置：非空写入、空值删本表条目（不误伤别的表）。"""
    store = {}
    io = {"load": lambda: dict(store), "save": lambda d: store.update(d)}
    session.save_path_map("probe_prefixes", "a.xlsx", {"sig_a": "U_X"}, **io)
    session.save_path_map("probe_prefixes", "b.xlsx", {"sig_b": "U_Y"}, **io)
    assert store["probe_prefixes"] == {"a.xlsx": {"sig_a": "U_X"}, "b.xlsx": {"sig_b": "U_Y"}}
    session.save_path_map("probe_prefixes", "a.xlsx", {}, **io)       # 清空 = 删条目
    assert store["probe_prefixes"] == {"b.xlsx": {"sig_b": "U_Y"}}
    assert session.path_map_of("probe_prefixes", "b.xlsx", load=io["load"]) == {"sig_b": "U_Y"}


def test_save_path_map_pytest_guard():
    """带守卫的那几套(RTL 补充逻辑)在 pytest 下一律 no-op。"""
    store = {}
    assert session.save_path_map("logic_overrides", "a.xlsx", {"x": {}},
                                 load=lambda: dict(store), save=lambda d: store.update(d),
                                 skip_under_pytest=True) is False
    assert store == {}


def test_code_version_is_str():
    """git 拿得到就是短 HEAD，拿不到给空——都不能抛。"""
    assert isinstance(session.code_version(), str)
    assert session.code_version(root="/no/such/dir") == ""


# ───────────────────────────── 覆盖度三层状态 ─────────────────────────────
def test_decompose_label_three_tiers():
    """三档中文名 → (mode, exhaustive)；非法值按『全面』兜底(旧下拉行为)。"""
    assert session.decompose_label("精简") == ("min", False)
    assert session.decompose_label("全面") == ("max", False)
    assert session.decompose_label("穷举") == ("max", True)
    assert session.decompose_label("") == ("max", False)


def test_coverage_priority_sig_over_form_over_global():
    """⭐三层优先级：单点 > 逻辑类型 > 全局。"""
    st = session.CoverageState("topout", global_label="精简")
    st.set_form_cov({"select": "exhaustive", "register": ""})     # 空值=跟随全局，不入档
    assert st.form_cov == {"select": "exhaustive"}
    models = [{"name": "sig_sel", "form": "select"}, {"name": "sig_reg", "form": "register"}]
    assert st.mode_for("sig_sel", models=models) == ("max", True)       # 形态档命中
    assert st.mode_for("sig_reg", models=models) == ("min", False)      # 跟随全局(精简)
    st.set_sig_cov("SIG_SEL", "min")                                    # 单点档压形态档(大小写无关)
    assert st.mode_for("sig_sel", models=models) == ("min", False)
    st.set_sig_cov("sig_sel", "")                                       # 清单点 → 回到形态档
    assert st.sig_cov == {}
    assert st.mode_for("sig_sel", models=models) == ("max", True)


def test_coverage_unknown_signal_follows_global():
    """清单里没有的名字 → 定不出形态 → 跟随全局。"""
    st = session.CoverageState("topout", global_label="全面")
    st.set_form_cov({"select": "min"})
    assert st.mode_for("查无此信号", models=[{"name": "sig_sel", "form": "select"}]) == ("max", False)


def test_coverage_global_mode_argument_wins_over_state():
    """gui 那边下拉控件才是全局档真相源：传 global_mode 时用它兜底，不用状态里的。"""
    st = session.CoverageState("topout", global_label="精简")
    assert st.mode_for("x", global_mode=("max", True)) == ("max", True)
    assert st.mode_for("x") == ("min", False)


def test_coverage_effective_label_says_where_it_came_from():
    """给 v2 用的『生效档 + 来源』文案。"""
    st = session.CoverageState("topout", global_label="全面")
    models = [{"name": "sig_sel", "form": "select"}]
    assert st.effective_label("sig_sel", models=models) == ("全面", "全局默认")
    st.set_form_cov({"select": "min"})
    assert st.effective_label("sig_sel", models=models) == ("精简", "逻辑类型·选路 (F2)")
    st.set_sig_cov("sig_sel", "exhaustive")
    assert st.effective_label("sig_sel", models=models) == ("穷举", "本信号")


@pytest.mark.contract("C-150")
def test_c150_effective_chain_three_levels():
    """N7（冲突⑧）：effective_chain 给整条三层继承链，effective_label 原样不动。

    Design 蓝条要的是「本信号「跟随上级」→ 逻辑类型「选路 (F2)」= 全面 → 全局默认 = 全面」，
    active 按冲突⑧：本信号没单设 → 逻辑类型层与全局层都算参与生效（两行高亮）。"""
    st = session.CoverageState("topout", global_label="全面")
    models = [{"name": "sig_sel", "form": "select"}]

    ch = st.effective_chain("sig_sel", models=models)
    assert [r["level"] for r in ch] == ["本信号", "逻辑类型·选路 (F2)", "全局默认"]
    assert [r["label"] for r in ch] == ["跟随上级", "跟随全局", "全面"]
    assert [r["active"] for r in ch] == [False, True, True]
    assert [r["key"] for r in ch] == ["", "", "max"]
    assert all(set(r) == {"level", "label", "active", "key"} for r in ch)

    st.set_form_cov({"select": "min"})                     # 逻辑类型层单设 → 它决定，但两层仍都 active
    ch = st.effective_chain("sig_sel", models=models)
    assert [r["label"] for r in ch] == ["跟随上级", "精简", "全面"]
    assert [r["active"] for r in ch] == [False, True, True]
    assert [r["key"] for r in ch] == ["", "min", "max"]
    assert st.effective_label("sig_sel", models=models) == ("精简", "逻辑类型·选路 (F2)")

    st.set_sig_cov("sig_sel", "exhaustive")                # 本信号单设 → 只有它 active
    ch = st.effective_chain("sig_sel", models=models)
    assert [r["label"] for r in ch] == ["穷举", "精简", "全面"]
    assert [r["active"] for r in ch] == [True, False, False]
    assert [r["key"] for r in ch] == ["exhaustive", "min", "max"]
    # 赢的那层 = chain 里第一个 active 行，与 effective_label 同口径（两个 API 不许分叉）
    win = next(r for r in ch if r["active"])
    assert win["label"] == st.effective_label("sig_sel", models=models)[0]

    # 定不出形态（清单里没这个名）→ 逻辑类型层退化成通用层名 + 跟随全局，恒三行不塌
    ch = st.effective_chain("查无此信号", models=models)
    assert [r["level"] for r in ch] == ["本信号", "逻辑类型", "全局默认"]
    assert [r["label"] for r in ch] == ["跟随上级", "跟随全局", "全面"]

    # form_key 直接给（不查 models）与查 models 同结果
    assert st.effective_chain("sig_sel", form_key="select") == \
        st.effective_chain("sig_sel", models=models)


def test_coverage_persist_and_restore_per_view():
    """全局档/上限按 view_id 分桶存盘、下次恢复；单点档与形态档【不】存盘(R25)。"""
    store = {}
    io = {"load": lambda: dict(store), "save": lambda d: store.update(d)}
    a = session.CoverageState("topout", **io)
    b = session.CoverageState("logic", **io)
    a.persist_global_label("穷举")
    a.persist_max_tests(77)
    b.persist_global_label("精简")
    a.set_sig_cov("sig_a", "min")
    a.set_form_cov({"select": "exhaustive"})
    assert store == {"cov_topout": "穷举", "maxt_topout": 77, "cov_logic": "精简"}
    a2 = session.CoverageState("topout", **io)
    assert a2.restore_global_label() == "穷举"
    assert a2.restore_max_tests(skip_under_pytest=False) == 77
    assert a2.sig_cov == {} and a2.form_cov == {}      # 会话内临时档不恢复
    assert session.CoverageState("dft", **io).restore_global_label() == "全面"   # 没存过=默认


def test_coverage_restore_rejects_junk():
    """存档里的脏值(手改/旧版)不该顶掉默认值。"""
    store = {"cov_topout": "乱写", "maxt_topout": 999999}
    st = session.CoverageState("topout", load=lambda: dict(store))
    assert st.restore_global_label() == "全面"
    assert st.restore_max_tests(skip_under_pytest=False) is None      # 越界 → 调用方保持默认
    assert st.max_tests == session.DEFAULT_MAX_TESTS


def test_coverage_restore_max_tests_skipped_under_pytest():
    """pytest 下不恢复上限（与 save_settings 的 no-op 对称，防真机配置污染测试基线）。"""
    st = session.CoverageState("topout", load=lambda: {"maxt_topout": 77})
    assert st.restore_max_tests() is None


def test_form_key_of_and_hint_text():
    models = [{"name": "Sig_A", "form": "gated"}]
    assert session.form_key_of(models, "sig_a") == "gated"
    assert session.form_key_of(models, "sig_b") is None
    assert session.form_key_of(None, "sig_a") is None
    assert session.cov_hint_text([]) == ""
    cols = [{"neg": False}, {"neg": True}, {"neg": False}]
    assert session.cov_hint_text(cols) == "→ 当前信号 3 条，含 1 负向"
    assert session.cov_hint_text(cols[:1], customized=True) == "→ 当前信号 1 条（已自定义）"


# ─────────────── 覆盖度口径 == topout 的口径（同源，非各写一份）───────────────
@pytest.fixture(scope="module")
def mirrors(tmp_path_factory):
    """两张 mirror 夹具：(名字, wb, resolver)。"""
    d = tmp_path_factory.mktemp("sess_mirror")
    out = []
    for tag, mod, fn in (("btlp", make_mirror_btlp, "mirror_btlp_dreg.xlsx"),
                         ("wl", make_mirror_excel, "mirror_wl_dreg.xlsx")):
        p = str(d / fn)
        mod.build(p)
        wb = M.load_workbook(p)
        out.append((tag, p, wb, R.Resolver(wb)))
    return out


_COV_CASES = [
    ({}, {}),
    ({}, {"select": "exhaustive"}),
    ({}, {"register": "min", "boolean": "exhaustive", "gated": "min"}),
]


@pytest.mark.parametrize("sig_cov, form_cov", _COV_CASES)
def test_coverage_state_matches_topout_effective_cov(mirrors, sig_cov, form_cov):
    """⭐⭐ 两套门面同源：对两张 mirror 的每个信号，CoverageState 的生效档 == topout 的
    _effective_cov_str / _cov_for。口径一旦分叉，清单『用例』列与真值表列数就会对不上(#3 实证)。"""
    for tag, _p, wb, res in mirrors:
        if not getattr(wb, "topout", None):
            continue
        models = T.topout_view_models(wb, mode="min", form_cov=form_cov or None)
        st = session.CoverageState("topout", global_label="精简")
        st.set_form_cov(form_cov)
        st.sig_cov.update(sig_cov)
        # 再叠一层单点档：拿清单里头两个信号钉死，验单点压形态
        for nm, c in zip([m["name"] for m in models][:2], ("exhaustive", "min")):
            st.set_sig_cov(nm, c)
        assert models, "%s: Topout 清单不该为空" % tag
        for m in models:
            name = m["name"]
            shape = FORMS.Shape(m["form"]) if m["form"] else None
            assert st.effective_cov(name, models=models) == \
                T._effective_cov_str(st.sig_cov, st.form_cov, shape, name), \
                "%s/%s 生效档与 topout 不一致" % (tag, name)
            assert st.mode_for(name, models=models) == \
                T._cov_for(st.sig_cov, name, "min", False, form_cov=st.form_cov, shape=shape), \
                "%s/%s (mode,exhaustive) 与 topout 不一致" % (tag, name)


def test_coverage_state_matches_signal_view(tmp_path_factory):
    """⭐⭐ 与【现在这套界面】逐信号对照：SignalView._mode_for(已委托本层) 的结果，
    == 独立构造的 CoverageState.mode_for。起一次 offscreen GUI 取现值即可。
    两张 mirror × 全部 SignalView(Topout + 子视图) × 每个信号。"""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PySide6")
    from PySide6 import QtWidgets
    from dreg_verify import legacy_gui as G
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])   # noqa: F841
    d = tmp_path_factory.mktemp("sess_gui")
    checked = 0
    for mod, fn in ((make_mirror_btlp, "mirror_btlp_dreg.xlsx"),
                    (make_mirror_excel, "mirror_wl_dreg.xlsx")):
        p = str(d / fn)
        mod.build(p)
        w = G.MainWindow()
        w.path_edit.setText(p)
        w.on_load()
        try:
            for vid, v in w._all_signal_views().items():
                v.cov.setCurrentText("精简")                      # 全局=精简
                v._form_cov = {"select": "exhaustive", "register": "min"}
                v.refresh()
                names = [m["name"] for m in v.models]
                for nm, c in zip(names[:2], ("exhaustive", "min")):
                    v._sig_cov[nm.lower()] = c
                st = session.CoverageState(vid, global_label="精简")
                st.set_form_cov(dict(v._form_cov))
                st.sig_cov.update(dict(v._sig_cov))
                for nm in names:
                    assert st.mode_for(nm, models=v.models) == v._mode_for(nm), \
                        "%s/%s/%s 覆盖档与 SignalView 不一致" % (fn, vid, nm)
                    checked += 1
        finally:
            w.close()
    assert checked >= 40, "对照的信号太少(%d)，夹具没载进来？" % checked


# ───────────────────────────── 配置导入导出 ─────────────────────────────
def _cfg(**kw):
    g = {"coverage_logic": "全面", "coverage_mux": "穷举", "max_tests": 77,
         "cascade_logic": "cone", "cascade_mux": "force",
         "append_to_logic": True, "append_to_mux": False, "include_risky": True}
    return session.collect_config("d:/tables/mirror_btlp_dreg.xlsx", g, **kw)


def test_collect_config_shape_and_excel_stamp():
    """完整配置带版本号 + 源表文件名/全路径（导入时按文件名核对）。"""
    cfg = _cfg(signals_checked=["sig_a"], probe_prefixes={"sig_a": "U_X"},
               force_signals={"sig_b"}, view_edits={"topout": {"x": 1}}, view_checks=None)
    assert cfg["dreg_verify_config"] == session.CONFIG_VERSION
    assert cfg["excel"] == "mirror_btlp_dreg.xlsx"
    assert cfg["excel_path"] == "d:/tables/mirror_btlp_dreg.xlsx"
    assert cfg["global"]["max_tests"] == 77
    assert cfg["signals_checked"] == ["sig_a"]
    assert cfg["force_signals"] == ["sig_b"]              # set → 排序后的 list(可 JSON 化)
    assert cfg["view_edits"] == {"topout": {"x": 1}}      # 不透明数据原样进出
    assert "sig_cov" not in cfg                           # 单点档是会话内临时档，不导出


def test_collect_config_drops_empty_buckets_and_sorts():
    """空桶不进配置文件(体积/噪声)，集合类一律排序 → 两次导出逐字节可比。"""
    cfg = _cfg(mux_expected={"a": {"x": 1}, "b": {}}, mux_dropped={"a": {3, 1}, "b": set()},
               mux_cleared={"z", "y"}, mux_user_vecs={"a": [{"n": 1}], "b": []},
               mux_data={"a": {"d": 2}, "b": {}})
    assert set(cfg["mux_expected"]) == {"a"}
    assert cfg["mux_dropped"] == {"a": [1, 3]}
    assert cfg["mux_cleared"] == ["y", "z"]
    assert set(cfg["mux_user_vecs"]) == {"a"}
    assert set(cfg["mux_data"]) == {"a"}


def test_collect_config_copies_not_aliases():
    """收集后的配置是快照：之后再改 GUI 状态不该串改已导出的 payload。"""
    src = {"sig_a": "U_X"}
    cfg = _cfg(probe_prefixes=src)
    src["sig_b"] = "U_Y"
    assert cfg["probe_prefixes"] == {"sig_a": "U_X"}


def test_config_file_roundtrip(tmp_path):
    p = str(tmp_path / "cfg.json")
    cfg = _cfg(signals_checked=["sig_a"])
    session.write_config_file(p, cfg)
    assert json.loads(open(p, encoding="utf-8").read()) == cfg
    assert session.read_config_file(p) == cfg
    with pytest.raises(OSError):
        session.read_config_file(str(tmp_path / "nope.json"))
    (tmp_path / "bad.json").write_text("{ nope", encoding="utf-8")
    with pytest.raises(ValueError):
        session.read_config_file(str(tmp_path / "bad.json"))


def test_default_config_filename():
    assert session.default_config_filename("d:/x/mirror_btlp_dreg.xlsx") == \
        "mirror_btlp_dreg_config.json"
    assert session.default_config_filename("") == "dreg_config.json"


def test_classify_config_v2_v1_and_junk():
    assert session.classify_config(_cfg())[0] is True                       # v2 完整配置
    assert session.classify_config({"dreg_verify_edits": 1, "edits": {}}) == (False, True)
    assert session.classify_config({"edits": {}}) == (False, True)          # v1 只有编辑段
    assert session.classify_config({"mux_data": {}}) == (False, True)
    assert session.classify_config({"hello": 1}) == (False, False)
    assert session.classify_config("不是 dict") == (False, False)


def test_apply_config_rejects_unknown_file():
    plan = session.apply_config({"hello": 1})
    assert plan["ok"] is False and plan["error"] == session.CONFIG_BAD_FILE_MSG


def test_apply_config_excel_mismatch_by_filename_only():
    """源表核对按【文件名】比，跨机器路径不同是正常用法，不该误报。"""
    cfg = _cfg()
    plan = session.apply_config(cfg, current_excel="/home/u/tables/mirror_btlp_dreg.xlsx")
    assert plan["ok"] and plan["is_full"] and plan["excel_mismatch"] is False
    plan2 = session.apply_config(cfg, current_excel="/home/u/mirror_wl_dreg.xlsx")
    assert plan2["excel_mismatch"] is True
    assert plan2["cfg_excel"] == "mirror_btlp_dreg.xlsx" and plan2["cur_excel"] == "mirror_wl_dreg.xlsx"
    plan3 = session.apply_config(cfg, current_excel="")      # 没加载表 → 不报不一致
    assert plan3["excel_mismatch"] is False


def test_apply_config_normalizes_sections():
    cfg = _cfg(probe_prefixes={" SIG_A ": " U_X ", "sig_bad": ""},
               force_signals={" SIG_B ", ""}, suffix_override={"SIG_C": 1},
               logic_overrides={" SIG_D ": {"expr": "A"}})
    plan = session.apply_config(cfg, current_excel="mirror_btlp_dreg.xlsx")
    assert plan["probe_prefixes"] == {"sig_a": "U_X"}        # 去空格/小写、空值丢弃
    assert plan["force_signals"] == {"sig_b"}
    assert plan["suffix_override"] == {"sig_c": True}
    assert plan["logic_overrides"] == {"sig_d": {"expr": "A"}}


def test_apply_config_legacy_has_no_full_sections():
    """v1 旧【测试项编辑】文件走合并语义：不套全局/探针/force(值为 None = 保持当前)。"""
    plan = session.apply_config({"dreg_verify_edits": 1, "edits": {}})
    assert plan["ok"] and plan["is_legacy"] and not plan["is_full"]
    assert all(plan[k] is None for k in ("global", "probe_prefixes", "force_signals",
                                         "suffix_override", "logic_overrides"))


def test_apply_config_bad_section_types_are_skipped():
    """段类型不对(手改坏的配置) → None = 不套用，而不是清空用户现有配置。"""
    cfg = _cfg()
    cfg["probe_prefixes"] = ["不是 dict"]
    cfg["force_signals"] = {"不是": "list"}
    plan = session.apply_config(cfg)
    assert plan["probe_prefixes"] is None and plan["force_signals"] is None


def test_normalize_global_settings_key_compat():
    """版本/键兼容：缺键各自的回退规则（导入旧配置时的确定性）。"""
    n = session.normalize_global_settings({})
    assert n["coverage_logic"] is None and n["coverage_mux"] is None      # 保持当前
    assert n["max_tests"] is None
    assert n["cascade_logic"] == "cone" and n["cascade_mux"] == "cone"
    assert n["append_to_logic"] is True                                    # 缺键复位默认 True
    assert n["append_to_mux"] is False                                     # 缺键复位默认 False
    assert n["include_risky"] is None                                      # 缺键【保持当前】
    # 旧 cascade_mode 单键 → 两侧都回退到它
    n2 = session.normalize_global_settings({"cascade_mode": "force"})
    assert n2["cascade_logic"] == "force" and n2["cascade_mux"] == "force"
    # 新键压旧键
    n3 = session.normalize_global_settings({"cascade_mode": "force", "cascade_mux": "cone"})
    assert n3["cascade_logic"] == "force" and n3["cascade_mux"] == "cone"
    # 非法值不生效
    n4 = session.normalize_global_settings({"coverage_logic": "乱写", "max_tests": 0})
    assert n4["coverage_logic"] is None and n4["max_tests"] is None
    n5 = session.normalize_global_settings({"coverage_logic": "穷举", "max_tests": 100000,
                                            "include_risky": False})
    assert n5["coverage_logic"] == "穷举" and n5["max_tests"] == 100000
    assert n5["include_risky"] is False
    assert session.normalize_global_settings("不是 dict")["append_to_logic"] is True


def test_persist_global_settings_only_writes_given_keys():
    store = {"keep": 1}
    session.persist_global_settings({"cascade_logic": "force", "append_to_mux": True},
                                    load=lambda: dict(store), save=lambda d: store.update(d))
    assert store == {"keep": 1, "cascade_logic": "force", "append_to_mux": True}
    assert "include_risky" not in store


def test_blank_config_state_covers_user_config_layer():
    """导入前清空的字段清单：容器类型对、且每次给新对象(不共享)。"""
    a = session.blank_config_state()
    b = session.blank_config_state()
    assert set(a) >= {"_edited", "_customized", "_neg_only", "_mux_expected", "_mux_neg",
                      "_mux_data", "_mux_dropped", "_mux_cleared", "_mux_user_vecs",
                      "_sig_cov", "_sig_cascade", "_probe_prefixes", "_force_signals",
                      "_suffix_override", "_logic_overrides"}
    assert isinstance(a["_customized"], set) and isinstance(a["_edited"], dict)
    a["_edited"]["x"] = 1
    assert b["_edited"] == {}


# ───────────────────── 诊断配置：文本解析/格式化/校验 ─────────────────────
def test_force_signal_text_roundtrip():
    """每行一个基名；# 注释、空行、大小写、行尾注释都要处理干净。"""
    text = "SIG_A\n  sig_b  # 撞名 RO 寄存器\n# 整行注释\n\nsig_a\n"
    assert session.parse_force_signal_text(text) == {"sig_a", "sig_b"}
    assert session.render_force_signal_text({"sig_b", "sig_a"}) == "sig_a\nsig_b"
    assert session.parse_force_signal_text("") == set()
    assert session.render_force_signal_text(None) == ""


def test_probe_prefix_text_merge_import_wins():
    """导入映射文件 = 与编辑框现有内容合并，同名以导入为准。"""
    cur = session.render_probe_prefix_text({"sig_a": "U_X", "sig_b": "U_Y"})
    merged = session.merge_probe_prefix_text(cur, "sig_b=U_Z\nsig_c=U_W\n")
    got = session.parse_probe_prefix_text(merged)
    assert got == {"sig_a": "U_X", "sig_b": "U_Z", "sig_c": "U_W"}


def test_text_file_io(tmp_path):
    p = str(tmp_path / "probe_prefixes.txt")
    session.write_mapping_text(p, "sig_a=U_X\n\n\n")
    assert open(p, encoding="utf-8").read() == "sig_a=U_X\n"      # 尾部规范成单个换行
    assert session.read_text_file(p) == "sig_a=U_X\n"
    with pytest.raises(OSError):
        session.read_text_file(str(tmp_path / "nope.txt"))


def test_supplement_template_generic_has_placeholder():
    """没选中信号 → 通用模板；占位符名字会被校验挡掉(防原样保存)。"""
    tmpl = session.supplement_template(None)
    assert list(tmpl) == ["<信号基名>"]
    _norm, errs = session.validate_supplements(tmpl)
    assert errs and "占位符" in errs[0]


def test_supplement_template_prefills_current_signal(mirrors):
    """选中 logic 信号 → 预填原表达式 + 原输入映射，用户只在外面包 ECO 级。"""
    _tag, _p, wb, _res = mirrors[0]
    sig = wb.logic[0]
    tmpl = session.supplement_template(sig)
    spec = tmpl[sig.out_base.lower()]
    assert spec["expr"] == sig.expr and spec["enabled"] is True
    assert {i["var"] for i in spec["inputs"]} == set(sig.inputs)
    norm, errs = session.validate_supplements(tmpl)
    assert not errs and set(norm) == {sig.out_base.lower()}       # 模板本身就是合法 spec


def test_validate_supplements_catches_each_error_kind():
    """校验要逐条说清哪儿不对（错误非空时调用方不保存）。"""
    def errs_of(data):
        norm, errs = session.validate_supplements(data)
        return norm, "\n".join(errs)

    _n, e = errs_of("不是对象")
    assert "顶层必须是 JSON 对象" in e
    _n, e = errs_of({"sig_a": "不是对象"})
    assert "spec 必须是对象" in e
    _n, e = errs_of({"sig_a": {"inputs": [{"var": "A", "raw": "sig_x"}]}})
    assert "缺 expr" in e
    _n, e = errs_of({"sig_a": {"expr": "A", "inputs": []}})
    assert "inputs 应为非空列表" in e
    _n, e = errs_of({"sig_a": {"expr": "A &", "inputs": [{"var": "A", "raw": "sig_x"}]}})
    assert "表达式解析失败" in e
    _n, e = errs_of({"sig_a": {"expr": "A & B", "inputs": [{"var": "A", "raw": "sig_x"}]}})
    assert "没有 input 映射: B" in e
    norm, e = errs_of({" SIG_A ": {"expr": "A & ~B", "enabled": True,
                                   "inputs": [{"var": "A", "raw": "sig_x"},
                                              {"var": "B", "raw": "sig_y[1:0]"}]}})
    assert e == "" and list(norm) == ["sig_a"]                    # 名字归一成小写


def test_supplements_json_text_helpers():
    assert session.render_supplements_json({}) == ""
    d = {"sig_a": {"expr": "A", "note": "中文不转义"}}
    txt = session.render_supplements_json(d)
    assert "中文不转义" in txt and json.loads(txt) == d
    assert session.parse_supplements_json("  ") is None           # 空 = 清空补充
    assert session.parse_supplements_json(txt) == d
    with pytest.raises(ValueError):
        session.parse_supplements_json("{ 坏 json")


def test_merge_supplements_text():
    tmpl = {"sig_b": {"expr": "B"}}
    txt, ok = session.merge_supplements_text("", tmpl)
    assert ok and json.loads(txt) == tmpl
    txt2, ok2 = session.merge_supplements_text(json.dumps({"sig_a": {"expr": "A"}}), tmpl)
    assert ok2 and set(json.loads(txt2)) == {"sig_a", "sig_b"}
    txt3, ok3 = session.merge_supplements_text("{ 坏 json", tmpl)
    assert ok3 is False and txt3 == "{ 坏 json"                    # 不动用户正在编辑的内容
