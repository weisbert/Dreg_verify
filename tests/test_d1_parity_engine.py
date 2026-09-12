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
