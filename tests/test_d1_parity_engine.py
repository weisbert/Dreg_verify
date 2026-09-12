# -*- coding: utf-8 -*-
"""test_d1_parity_engine.py —— Phase D 修复波 F3「行为 parity」的 **Qt-free** 回归（P-nn）。

对抗 review R1 拿 62 对逐字节产物证明了 v2 相对 v1 的一批行为差异，主控逐条裁决（多数按 v1
对齐）。本文件收**不需要起窗**的那几条：配置段的收集/迁移口径、状态筛的档位归属、落盘护栏。
起窗才验得到的在 `tests/test_ui_d1_parity.py`。

每条测试的命名是 `test_p<nn>_…`，nn = 发现汇总里的 P 编号，方便回查裁决原文。
夹具一律用 mirror 生成脚本（公开仓，**无真实信号名**）。
"""

import pytest

import ui_harness as H                                    # noqa: E402

pytest.importorskip("PySide6")

from dreg_verify import edits as ED                       # noqa: E402
from dreg_verify import session                           # noqa: E402
from dreg_verify.ui import persist as P                   # noqa: E402


# ═════════════════════ P-22：pytest 外起窗的脚本别写用户真机 ═════════════════════
def test_p22_no_persist_env_keeps_the_machine_files_untouched(monkeypatch):
    """I-08 只挡住「pytest + 出厂默认路径」；pytest 外起窗的脚本两条都不满足，于是把镜像表
    路径写进了用户真机 `~/.dreg_verify_gui.json` 的 recent_excels（P-22 实证）。

    `DREG_VERIFY_NO_PERSIST=1` 是给那类脚本的第二把锁：路径还是真机那份时一个字节都不写。
    这里**不真的去碰那两份文件**——把底层写函数换成计数器，写没写一目了然。
    """
    wrote = []
    monkeypatch.setattr(session, "save_settings",
                        lambda *a, **k: wrote.append(("settings",) + a[:2]) or True)
    monkeypatch.setattr(ED, "save_edits_file",
                        lambda *a, **k: wrote.append(("edits",) + a[:1]) or True)
    monkeypatch.setattr(P, "SETTINGS_PATH", P._SETTINGS_DEFAULT)      # 就是真机那份
    monkeypatch.setattr(P, "EDITS_PATH", P._EDITS_DEFAULT)

    # ① 没设环境变量：不算隔离（老口径一字未变）
    monkeypatch.delenv(P.NO_PERSIST_ENV, raising=False)
    assert P.no_persist_env() is False
    assert P.is_isolated() is False

    # ② 设上 "1"：算隔离，两个写出口都 no-op
    monkeypatch.setenv(P.NO_PERSIST_ENV, "1")
    assert P.no_persist_env() is True
    assert P.is_isolated() is True
    assert P.save_settings({"recent_excels": ["X:/mirror.xlsx"]}) is False
    assert P.save_edits_all({"X:/mirror.xlsx": {"view_edits": {"topout": {}}}}) is False
    assert wrote == [], "设了 DREG_VERIFY_NO_PERSIST=1 还往真机那两份文件写：%r" % (wrote,)

    # ③ 第二次不同：改成 "0" —— 不是这把锁，恢复原口径（写出口照常被调到）
    monkeypatch.setenv(P.NO_PERSIST_ENV, "0")
    assert P.no_persist_env() is False
    assert P.is_isolated() is False
    P.save_settings({"a": 1})
    P.save_edits_all({"b": {}})
    assert [w[0] for w in wrote] == ["settings", "edits"]


def test_p22_no_persist_env_does_not_disturb_isolated_paths(monkeypatch, tmp_path):
    """路径已经指到 tmp（`isolate_settings` 干的）时，这把锁**不该**顺手把写也掐掉
    ——否则所有持久化测试一旦在设了环境变量的 shell 里跑就全成了「写了等于没写」。"""
    monkeypatch.setattr(P, "SETTINGS_PATH", str(tmp_path / "s.json"))
    monkeypatch.setenv(P.NO_PERSIST_ENV, "1")
    assert P.save_settings({"k": 1}) is True
    assert P.load_settings().get("k") == 1


# ═════════════════════ P-06：nets.txt 按页类别「从没存过」= 全勾 ═════════════════════
def test_p06_nets_pages_default_to_every_category(tmp_path, monkeypatch):
    """`settings["nets_pages"]` 从没存过时 v2 给的是 `[]`（一个类别都不勾）→ 默认导出的
    nets.txt 只剩 52 根网，比 v1 的 195 根少 143 根**缺前缀的**衔接网（P-06，BLOCKER）。
    C-188 写的就是「默认全勾」，裁决也是「超集宁多勿漏」。"""
    from dreg_verify.ui import export_center as EC         # noqa: PLC0415
    from dreg_verify.ui import state as ST                 # noqa: PLC0415
    H.isolate_settings(monkeypatch, tmp_path)
    st = ST.WorkbenchState()
    st.load(H.mirror_path("btlp"))
    cats = EC.nets_categories(st)
    assert cats, "镜像表至少要有一个真有内容的类别，否则这条测试验不到东西"
    assert EC.NETS_PAGES_KEY not in st.settings()
    row = next(r for r in EC.default_rows(st) if r.kind == "nets")
    assert row.options["pages"] == list(cats)

    # 第二次、且不同：存过空列表 = 用户真的一个都不要 → 尊重它，别又给他全勾回来
    P.patch_settings({EC.NETS_PAGES_KEY: []})
    row2 = next(r for r in EC.default_rows(st) if r.kind == "nets")
    assert row2.options["pages"] == []

    # 第三次：存过一部分 → 只留本表真有的那些（C-187 原样，不因为这次改动放宽）
    P.patch_settings({EC.NETS_PAGES_KEY: [cats[0], "这个页名根本不存在"]})
    row3 = next(r for r in EC.default_rows(st) if r.kind == "nets")
    assert row3.options["pages"] == [cats[0]]


def test_p06_default_nets_export_covers_every_category(tmp_path, monkeypatch):
    """默认那一份 nets.txt 与「全类别」逐字节相同 —— 少一根网就是仿真时一根 force 不到的网。"""
    from dreg_verify import exports as X                   # noqa: PLC0415
    from dreg_verify.ui import export_center as EC         # noqa: PLC0415
    from dreg_verify.ui import state as ST                 # noqa: PLC0415
    H.isolate_settings(monkeypatch, tmp_path)
    st = ST.WorkbenchState()
    st.load(H.mirror_path("btlp"))
    row = next(r for r in EC.default_rows(st) if r.kind == "nets")
    got, want = str(tmp_path / "a.txt"), str(tmp_path / "b.txt")
    X.export_nets_by_purpose(st.wb, got, list(row.options["purposes"]),
                             pages=list(row.options["pages"]) or None)
    X.export_nets_by_purpose(st.wb, want, list(row.options["purposes"]),
                             pages=list(EC.nets_categories(st)))
    a = open(got, "rb").read()
    b = open(want, "rb").read()
    assert a == b and len(a) > 0

    # 只按用途（= 旧的「一个页类别都不勾」）真的更少 —— 证明上面那条不是在比两个空文件
    only_purpose = str(tmp_path / "c.txt")
    X.export_nets_by_purpose(st.wb, only_purpose, list(row.options["purposes"]), pages=None)
    assert len(open(only_purpose, "rb").read()) < len(a)


# ═════════ P-10 / P-12：覆盖度配置只作用于 v1 当初真用它的那两个范围 ═════════
#: v1 那三个键（`coverage_logic` / `coverage_mux` / `max_tests`，外加只有单 `coverage` 的旧文件）
#: 全是【排查(旧)】工具条上的控件，按信号类型分 logic / mux 两侧。Topout 与各页 SignalView
#: 各有自己的 `cov_<vid>` / `maxt_<vid>`，既不读这几个键、导入配置也动不到。
_V1_GLOBAL_VIEWS = ("logic", "mux")
_V1_UNTOUCHED_VIEWS = ("topout", "dft", "iddq")


def test_p12_legacy_single_coverage_keys_only_reach_the_views_v1_fed_them_to(tmp_path,
                                                                            monkeypatch):
    """P-12：同事机上留着一个 `coverage: 穷举` / `max_tests: 4096`，升级到 v2 后第一次导出
    的 .sv 就从 110486 变 242551 字节 —— 他什么都没改。因为这两个旧键被迁到了全部五个范围。"""
    from dreg_verify.ui import state as ST                 # noqa: PLC0415
    H.isolate_settings(monkeypatch, tmp_path)
    P.save_settings({"coverage": "穷举", "max_tests": 4096})
    st = ST.WorkbenchState()
    assert st.load(H.mirror_path("btlp"))
    got = {vid: (st.coverage(vid).global_label, int(st.coverage(vid).max_tests))
           for vid in ("topout", "logic", "mux", "dft", "iddq")}
    for vid in _V1_GLOBAL_VIEWS:
        assert got[vid] == ("穷举", 4096), vid
    for vid in _V1_UNTOUCHED_VIEWS:
        assert got[vid] == (session.DEFAULT_COV_LABEL, session.DEFAULT_MAX_TESTS), vid


def test_p12_per_view_keys_still_win_over_the_legacy_ones(tmp_path, monkeypatch):
    """第二次、且不同：`cov_<vid>` / `maxt_<vid>` 存过就以它为准（C-229），旧键只是
    「这个范围没存过时读一次」的兜底（C-230，且只读不回写）。"""
    from dreg_verify.ui import state as ST                 # noqa: PLC0415
    H.isolate_settings(monkeypatch, tmp_path)
    P.save_settings({"coverage": "穷举", "max_tests": 4096,
                     "cov_logic": "全面", "cov_topout": "穷举", "maxt_topout": 64})
    st = ST.WorkbenchState()
    assert st.load(H.mirror_path("btlp"))
    assert (st.coverage("logic").global_label, int(st.coverage("logic").max_tests)) == ("全面", 4096)
    assert (st.coverage("mux").global_label, int(st.coverage("mux").max_tests)) == ("穷举", 4096)
    assert (st.coverage("topout").global_label, int(st.coverage("topout").max_tests)) == ("穷举", 64)
    raw = P.load_settings()
    assert raw["coverage"] == "穷举" and raw["max_tests"] == 4096      # 旧键一个字节没动
    assert "cov_dft" not in raw and "maxt_dft" not in raw              # 也没顺手回写新键


def test_p10_config_global_block_only_lands_on_the_views_v1_applied_it_to(tmp_path, monkeypatch):
    """P-10：导入一份完整配置，`global` 段的上限被无差别套到五个范围 → 同一份配置导进来，
    .sv 从 84483 变 110486 字节。v1 `_apply_global_settings` 只动排查(旧)工具条那三个控件。"""
    from dreg_verify.ui import export_center as EC         # noqa: PLC0415
    from dreg_verify.ui import state as ST                 # noqa: PLC0415
    H.isolate_settings(monkeypatch, tmp_path)
    st = ST.WorkbenchState()
    assert st.load(H.mirror_path("btlp"))
    before = {vid: (st.coverage(vid).global_label, int(st.coverage(vid).max_tests))
              for vid in _V1_UNTOUCHED_VIEWS}

    cfg = str(tmp_path / "cfg.json")
    session.write_config_file(cfg, session.collect_config(
        H.mirror_path("btlp"),
        {"coverage_logic": "穷举", "coverage_mux": "穷举", "max_tests": 4096}))
    rep = EC.import_config(st, cfg)
    assert rep.ok and rep.is_full
    for vid in _V1_GLOBAL_VIEWS:
        assert (st.coverage(vid).global_label, int(st.coverage(vid).max_tests)) == ("穷举", 4096), vid
    for vid in _V1_UNTOUCHED_VIEWS:
        assert (st.coverage(vid).global_label, int(st.coverage(vid).max_tests)) == before[vid], vid

    # 第二次、且不同：再导一份档位不同的配置 —— 作用范围还是那两个，其余仍原样
    cfg2 = str(tmp_path / "cfg2.json")
    session.write_config_file(cfg2, session.collect_config(
        H.mirror_path("btlp"),
        {"coverage_logic": "精简", "coverage_mux": "精简", "max_tests": 42}))
    assert EC.import_config(st, cfg2).is_full
    for vid in _V1_GLOBAL_VIEWS:
        assert (st.coverage(vid).global_label, int(st.coverage(vid).max_tests)) == ("精简", 42), vid
    for vid in _V1_UNTOUCHED_VIEWS:
        assert (st.coverage(vid).global_label, int(st.coverage(vid).max_tests)) == before[vid], vid


def test_p10_global_block_round_trips_without_drifting(tmp_path, monkeypatch):
    """收集与套用必须是同一批范围，否则「导出 → 导入 → 再导出」这三个数会自己漂
    （以前 `max_tests` 从 Topout 取、却写回五个范围）。"""
    from dreg_verify.ui import export_center as EC         # noqa: PLC0415
    from dreg_verify.ui import state as ST                 # noqa: PLC0415
    H.isolate_settings(monkeypatch, tmp_path)
    st = ST.WorkbenchState()
    assert st.load(H.mirror_path("btlp"))
    st.coverage("logic").persist_global_label("穷举")
    st.coverage("mux").persist_global_label("精简")
    st.coverage("logic").persist_max_tests(777)
    st.coverage("topout").persist_max_tests(12)            # Topout 另有一档，不该掺进来
    one = EC.collect_config(st)["global"]
    assert (one["coverage_logic"], one["coverage_mux"], one["max_tests"]) == ("穷举", "精简", 777)

    cfg = str(tmp_path / "cfg.json")
    session.write_config_file(cfg, EC.collect_config(st))
    assert EC.import_config(st, cfg).is_full
    two = EC.collect_config(st)["global"]
    assert two == one, "同一份配置导进来再导出去，global 段就变了"
    # Topout 的上限不在 `global` 段里（P-11 已登记 backlog）：导入时它走 C-192 的「先清空」
    # 回出厂 256，而**不是**被配置里那个 777 顶掉 —— 后者才是 P-10 那 26003 字节的来处
    assert int(st.coverage("topout").max_tests) == session.DEFAULT_MAX_TESTS
