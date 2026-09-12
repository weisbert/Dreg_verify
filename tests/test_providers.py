# -*- coding: utf-8 -*-
"""test_providers.py —— Qt-free 数据源 `dreg_verify/providers.py` + 它驱动的引擎形参（C0-b）。

providers.py 是 GUI v2「换包不换引擎」的那块接口板：v2 的 state/worker 只认 ProviderProto，
Topout 视图与四个页视图的差异全在这里面。实现是从 `gui._TopoutProvider/_PageProvider` **逐方法
搬过来**的，所以本文件最要紧的一条是**等价性**——同一张 mirror 上两套 provider 的输出逐键相等
（搬家搬漏一个参数 = .sv/报告静默变样，那是 byte-gate 都盖不住的漂移，因为两边跑的是不同入口）。

本文件还收着 pageviews 的两条新形参测试（C-217 include_risky / C-276 progress）——
它们是给 provider 用的接口，而 tests/test_pageviews.py 不在 C0-b 的 owner 文件集里。

夹具一律用仓库里的 mirror 生成脚本（公开仓，不得出现真实信号名）。
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import make_mirror_btlp                              # noqa: E402  BT_LP 镜像（Topout 门面）
import make_mirror_excel                             # noqa: E402  WL 镜像（mux 级联 / iddq 门）
from dreg_verify import excel_model as M             # noqa: E402
from dreg_verify import pageviews as P               # noqa: E402


@pytest.fixture(scope="module")
def btlp(tmp_path_factory):
    p = tmp_path_factory.mktemp("prov_btlp") / "mirror_btlp_dreg.xlsx"
    make_mirror_btlp.build(str(p))
    return M.load_workbook(str(p))


@pytest.fixture(scope="module")
def wl(tmp_path_factory):
    p = tmp_path_factory.mktemp("prov_wl") / "mirror_wl_dreg.xlsx"
    make_mirror_excel.build(str(p))
    return M.load_workbook(str(p))


# ══════════════ pageviews 的新形参（provider 透传给引擎的那两个）══════════════
@pytest.mark.contract("C-217")
def test_c217_include_risky_param_default_true_bytes_equal(btlp, wl):
    """C-217：include_risky 成为形参，**默认 True = 原先写死的值** → 不传时产物逐字节相同。

    这条守的是「加形参不许改行为」：五个页本地入口全加了 include_risky，
    只要默认值对，.sv / 报告 / 视图模型三条路全都一个字节不动。"""
    for wb, pages in ((btlp, ("logic", "mux", "dft")), (wl, ("logic", "mux", "dft"))):
        for page in pages:
            if not P.page_available(wb, page):
                continue
            base, _b = P.build_page_sv(wb, page, mode="max", exhaustive=True, sv_summary=True)
            same, _b2 = P.build_page_sv(wb, page, mode="max", exhaustive=True, sv_summary=True,
                                        include_risky=True, block_suffix="")
            assert same == base, page                       # 默认值 = 写死值，逐字节相同
            assert P.page_report(wb, page) == P.page_report(wb, page, include_risky=True)
            assert P.page_view_models(wb, page) == P.page_view_models(wb, page, include_risky=True)
            # 形参真的接到 GenOptions 上了（不是接了个寂寞）
            assert P._page_gen_opts(page, "min", 256, False).include_risky is True
            assert P._page_gen_opts(page, "min", 256, False, include_risky=False).include_risky is False


@pytest.mark.contract("C-170")
def test_c170_page_sv_block_suffix_renames_summary_block(btlp):
    """C-170：汇总命名块后缀（N2 的『正向 / 反例分两个文件』要它）——默认 "" 不加，字节不变。

    两份产物若被贴进同一个 testcase，两个同名 begin/end 命名块是非法 SV。"""
    from dreg_verify import sv_writer as W
    plain, _b = P.build_page_sv(btlp, "logic", sv_summary=True)
    pos, _b2 = P.build_page_sv(btlp, "logic", sv_summary=True, block_suffix="_pos")
    assert W.SUMMARY_BLOCK in plain and (W.SUMMARY_BLOCK + "_pos") not in plain
    assert (W.SUMMARY_BLOCK + "_pos") in pos
    assert pos.replace(W.SUMMARY_BLOCK + "_pos", W.SUMMARY_BLOCK) == plain   # 只有块名这一处不同
    # 不出汇总块时后缀无处可加，也不许炸/不许改字节
    assert P.build_page_sv(btlp, "logic", block_suffix="_neg")[0] == \
        P.build_page_sv(btlp, "logic")[0]


@pytest.mark.contract("C-276")
def test_c276_page_view_models_progress(btlp):
    """C-276：页清单也给 progress / should_cancel（与 topout_view_models 同签名）——
    v2 把分析放后台、可随时停止、清单逐信号增量填充。两个都不传 = 旧行为。"""
    full = P.page_view_models(btlp, "logic")
    n = len(full)
    assert n >= 3

    seen = []
    got = P.page_view_models(btlp, "logic", progress=lambda d, t, name, m: seen.append((d, t, name, m)))
    assert got == full                                      # 带回调不改结果
    assert len(seen) == n                                   # 每信号回调一次
    assert [d for d, _t, _n, _m in seen] == list(range(1, n + 1))
    assert all(t == n for _d, t, _n, _m in seen)
    assert [name for _d, _t, name, _m in seen] == [m["name"] for m in full]
    assert [m for _d, _t, _n, m in seen] == full            # 第 4 参 = 该行的视图模型（增量填充用它）

    done = {"n": 0}

    def _cancel():
        return done["n"] >= 3                               # 分析完 3 个就停

    def _tick(d, _t, _name, _m):
        done["n"] = d

    part = P.page_view_models(btlp, "logic", progress=_tick, should_cancel=_cancel)
    assert len(part) == 3 and part == full[:3]              # 已完成的照常返回，其余留给「分析中」
    assert P.page_view_models(btlp, "logic", should_cancel=lambda: True) == []


# ══════════════ providers.py 本体 ══════════════
class _Cfg(object):
    """ConfigSourceProto 的最小实现（v2 的 WorkbenchState 满足同一组属性）。"""

    def __init__(self, wb, probe_prefixes=None, force_signals=None, logic_overrides=None,
                 include_risky=True, engine_lock=None):
        self.wb = wb
        self.probe_prefixes = probe_prefixes or {}
        self.force_signals = force_signals or set()
        self.logic_overrides = logic_overrides or {}
        self.include_risky = include_risky
        if engine_lock is not None:
            self.engine_lock = engine_lock


class _CountingLock(object):
    """记次数的可重入锁替身：证明「每次引擎调用都上了锁」。"""

    def __init__(self):
        import threading
        self._lk = threading.RLock()
        self.n = 0

    def __enter__(self):
        self._lk.acquire()
        self.n += 1
        return self

    def __exit__(self, *exc):
        self._lk.release()
        return False


def test_providers_is_qt_free():
    """⭐ 地基不变量：数据源层不得 import PySide6 / gui —— v2 换界面时这一层原样复用。"""
    import ast
    from dreg_verify import providers as PV
    src = open(PV.__file__, encoding="utf-8").read()
    mods = set()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Import):
            mods.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            mods.add(node.module or "")
            mods.update("%s.%s" % (node.module or "", a.name) for a in node.names)
    assert not any("PySide6" in m or m.endswith("gui") for m in mods), sorted(mods)


def test_providers_import_does_not_pull_pyside6():
    import subprocess
    code = ("import sys; import dreg_verify.providers; "
            "sys.exit(1 if 'PySide6' in sys.modules else 0)")
    assert subprocess.call([sys.executable, "-c", code]) == 0


# ── 等价性对照用的语义指纹（an / build 里有 AST、Binding、Vector 这类对象，`==` 比不了）──
_SUPP = {"d_logic_bt_lp_lna_agc": {
    "enabled": True,
    "note": "SE: ECO 顶层口加了一级旁路，真表只到 DREG",
    "expr": "EXTRA ? 3'b0 : (LINE ? LOCAL : AGCLINE)",
    "inputs": [
        {"var": "EXTRA", "raw": "d_bt_lp_iddq"},
        {"var": "LINE", "raw": "d_bt_lp_lna_line_sel"},
        {"var": "LOCAL", "raw": "d_bt_lp_lna_agc_local[2:0]"},
        {"var": "AGCLINE", "raw": "d_bt_lp_lna_agc_line[2:0]"},
    ],
}}


def _fp(v, depth=0):
    """把任意分析产物摊成【可比较】的语义指纹：对象按类名 + 公开字段递归展开。

    不用 repr——对象的 repr 带内存地址，同一份数据两次构造必不相等；也不只挑几个精选键比，
    那样漏搬一个字段正好躲过测试。这里是「全字段递归」，漏了什么都会显出来。"""
    if v is None or isinstance(v, (bool, int, float, str)):
        return v
    if depth > 24:
        return "…深度截断 %s" % type(v).__name__
    if isinstance(v, dict):
        return {k: _fp(x, depth + 1) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_fp(x, depth + 1) for x in v]
    if isinstance(v, (set, frozenset)):
        return sorted(repr(_fp(x, depth + 1)) for x in v)
    d = getattr(v, "__dict__", None)
    if d is not None:
        return (type(v).__name__,
                {k: _fp(x, depth + 1) for k, x in sorted(d.items()) if not k.startswith("_")})
    return (type(v).__name__, str(v))


_COV_CASES = [("min", 64, False), ("max", 256, False), ("max", 100000, True)]

#: C0-a 之后 an 才有的 6 个键。旧 gui provider 调 `norm_*` 时**不传** include_risky /
#: probe_prefix（它没有 cfg），新 provider 传（C0-d 接通）——所以这几个键的口径不再是
#: 「搬家等不等价」的事，从等价性指纹里排除，另用 `_assert_new_keys_equal` 逐键对照。
_C0A_AN_KEYS = ("out_net", "status_detail", "ctrl_keys_missing", "dft_gate_skipped", "graph",
                "probe_prefix")


def _old_keys_fp(an):
    """an 的【旧键集】语义指纹（C0-a 那 6 个新键排除在外）——搬家等价性只比这一份。"""
    return _fp({k: v for k, v in an.items() if k not in _C0A_AN_KEYS})


def _assert_new_keys_equal(a_new, a_old, who):
    """C0-a 新键单独对照（**排除 ≠ 不比**，不许整块放掉）：

      · out_net / ctrl_keys_missing / dft_gate_skipped / graph —— 纯结构派生，不吃 provider
        的任何配置 → 必须逐键相等，不等就是搬家真漏了东西。
      · status_detail —— 吃 include_risky 与 probe_prefix（§7-2 的 needs-prefix /
        risky-generated / bare-probe 三档靠它们分）。旧 provider 两个都不传 = 恒按「缺前缀
        也照生成、探针一律裸名」判档。本文件全程 include_risky=True，配的探针前缀又都落在
        非 bare-probe 的信号上，所以这里仍断言相等；真分叉时这条会指名道姓报出来
        （那说明 cfg 咬到了，不是搬漏 —— 三档本身另有
        `test_provider_status_detail_tiers_need_include_risky_and_probe_prefix` 钉死）。
      · probe_prefix —— 旧 provider 根本没有这一格（它不拿 probe_prefixes 配置）。
    """
    for k in ("out_net", "ctrl_keys_missing", "dft_gate_skipped", "graph"):
        assert _fp(a_new[k]) == _fp(a_old[k]), "%s 的 an[%r] 不一致（搬家漏了？）" % (who, k)
    assert a_new["status_detail"] == a_old["status_detail"], \
        "%s status_detail 分档不一致（include_risky/probe_prefix 咬到了）" % who
    assert "probe_prefix" in a_new and "probe_prefix" not in a_old


def _both(wb, main, page=None, **cfgkw):
    """(新 provider, 旧 gui provider) —— 两边读同一份配置。"""
    from dreg_verify import gui as G
    from dreg_verify import providers as PV
    cfg = _Cfg(wb, **cfgkw)
    if page is None:
        return PV.TopoutProvider(cfg), G._TopoutProvider(main)
    return PV.PageProvider(cfg, page), G._PageProvider(main, page)


@pytest.fixture(scope="module")
def gui_windows(tmp_path_factory):
    """起一次 offscreen MainWindow，两张 mirror 各载一份：(tag, wb, MainWindow)。"""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PySide6")
    from PySide6 import QtWidgets
    from dreg_verify import gui as G
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])   # noqa: F841
    d = tmp_path_factory.mktemp("prov_gui")
    out, wins = [], []
    for tag, mod, fn in (("btlp", make_mirror_btlp, "mirror_btlp_dreg.xlsx"),
                         ("wl", make_mirror_excel, "mirror_wl_dreg.xlsx")):
        p = str(d / fn)
        mod.build(p)
        w = G.MainWindow()
        w.path_edit.setText(p)
        w.on_load()
        wins.append(w)
        out.append((tag, w.wb, w))
    yield out
    for w in wins:
        w.close()


@pytest.mark.contract("C-276")
def test_providers_equivalent_to_gui_providers(gui_windows):
    """⭐⭐ 搬家的证明：同一张 mirror、同一份配置下，`providers.TopoutProvider/PageProvider` 与
    `gui._TopoutProvider/_PageProvider` 的 view_models / analyze / render_sv / render_report
    **逐键相等**。

    两套门面跑的是不同入口，.sv 字节闸门守不住这里 —— 搬家时漏带一个参数（probe_prefixes、
    force_overrides、RTL 补充、for_test 行序…）只会让新界面静默产出另一份东西。
    配置不是空的：探针前缀 / 强制 force 都配上，逼着两边都走有配置的分支。"""
    checked = {"models": 0, "an": 0, "sv": 0, "report": 0, "signals": 0}
    for tag, wb, w in gui_windows:
        pp, fo, models0 = {}, set(), None
        for mode, maxt, exh in _COV_CASES:
            new, old = _both(wb, w, probe_prefixes=pp, force_signals=fo)
            if not old.has_page():
                continue
            m_new = new.view_models(mode, maxt, exh)
            m_old = old.view_models(mode, maxt, exh)
            assert _fp(m_new) == _fp(m_old), "%s/topout/%s view_models 不一致" % (tag, mode)
            checked["models"] += len(m_new)
            models0 = models0 or m_new
            if not pp:      # 第一轮用清单里的真名配上配置，后面几轮就都走「有配置」的分支
                pp.update({m["probe_net"]: "U_TOP.U_SUB" for m in m_new[:2] if m.get("probe_net")})
                fo.update(m["name"] for m in m_new[:1])
                w._probe_prefixes = dict(pp)
                w._force_signals = set(fo)

        new, old = _both(wb, w, probe_prefixes=pp, force_signals=fo)
        assert (new.view_id, new.has_chain, new.supports_sig_cov) == \
            (old.view_id, old.has_chain, old.supports_sig_cov)
        assert new.kind_label == old.kind_label
        assert (new.title(), new.empty_hint(), new.has_page()) == \
            (old.title(), old.empty_hint(), old.has_page())

        for m in (models0 or []):
            name = m["name"]
            a_new, a_old = new.analyze(name, "min", 64, False), old.analyze(name, "min", 64, False)
            assert (a_new is None) == (a_old is None), "%s/%s analyze 一边给 None" % (tag, name)
            if a_new is None:
                continue
            # probe_prefix 是本层补的那一格，旧 provider 没有 → 其余键必须逐键相等
            assert set(a_new) - set(a_old) == {"probe_prefix"}, name
            assert set(a_old) - set(a_new) == set(), name
            assert _old_keys_fp(a_new) == _old_keys_fp(a_old), \
                "%s/%s analyze 不一致" % (tag, name)
            _assert_new_keys_equal(a_new, a_old, "%s/%s" % (tag, name))
            checked["an"] += 1
        assert new.analyze("查无此信号", "min", 64, False) is None

        for scope in ("all", "pos", "neg"):
            opt = dict(only=None, mode="max", max_tests=256, exhaustive=False, edited=None)
            t_new, b_new = new.render_sv(scope=scope, sv_summary=True, **opt)
            t_old, b_old = old.render_sv(scope=scope, sv_summary=True, **opt)
            assert t_new == t_old, "%s/topout/%s .sv 逐字节不一致" % (tag, scope)
            assert _fp(b_new) == _fp(b_old)
            checked["sv"] += 1
        assert _fp(new.render_report("min", 64, False)) == _fp(old.render_report("min", 64, False))
        checked["report"] += 1

        for page in P.PAGES:                # 页视图：四页各自对照（空页照样比——空也得一样空）
            pnew, pold = _both(wb, w, page=page, probe_prefixes=pp, force_signals=fo)
            assert (pnew.view_id, pnew.has_page(), pnew.title(), pnew.empty_hint()) == \
                (pold.view_id, pold.has_page(), pold.title(), pold.empty_hint())
            if not pold.has_page():
                continue
            m_new, m_old = pnew.view_models("min", 64, False), pold.view_models("min", 64, False)
            assert _fp(m_new) == _fp(m_old), "%s/%s view_models 不一致" % (tag, page)
            checked["models"] += len(m_new)
            for m in m_new:
                a_new = pnew.analyze(m["name"], "min", 64, False)
                a_old = pold.analyze(m["name"], "min", 64, False)
                assert set(a_new) - set(a_old) == {"probe_prefix"}
                assert _old_keys_fp(a_new) == _old_keys_fp(a_old)
                _assert_new_keys_equal(a_new, a_old, "%s/%s/%s" % (tag, page, m["name"]))
                checked["an"] += 1
            t_new, b_new = pnew.render_sv(None, "min", 64, False, None, sv_summary=True)
            t_old, b_old = pold.render_sv(None, "min", 64, False, None, sv_summary=True)
            assert t_new == t_old and _fp(b_new) == _fp(b_old)
            checked["sv"] += 1
            assert _fp(pnew.render_report("min", 64, False)) == \
                _fp(pold.render_report("min", 64, False))
            checked["report"] += 1
        checked["signals"] += len(models0 or [])

    assert checked["models"] >= 100 and checked["an"] >= 40, checked
    assert checked["sv"] >= 10 and checked["report"] >= 4, checked


@pytest.mark.contract("C-276")
def test_providers_equivalent_with_logic_overrides(gui_windows):
    """RTL 补充逻辑（_supplemented 那段 swap-and-restore）也要等价——它是最容易搬漏的一处：
    漏了就静默显示补充【前】的真值表（= 假绿），而 .sv 照样跑得过。"""
    from dreg_verify import gui as G
    from dreg_verify import providers as PV
    tag, wb, w = gui_windows[0]
    target = "d_logic_bt_lp_lna_agc"
    if not any(t.name == target for t in (wb.topout or [])):
        pytest.skip("%s 镜像没有这个信号" % tag)
    saved_pp = dict(getattr(w, "_probe_prefixes", None) or {})
    saved_fo = set(getattr(w, "_force_signals", None) or ())
    w._probe_prefixes, w._force_signals = {}, set()
    w._logic_overrides = {k: dict(v) for k, v in _SUPP.items()}
    try:
        new = PV.TopoutProvider(_Cfg(wb, logic_overrides={k: dict(v) for k, v in _SUPP.items()}))
        old = G._TopoutProvider(w)
        assert _fp(new.view_models("min", 64, False)) == _fp(old.view_models("min", 64, False))
        a_new, a_old = new.analyze(target, "min", 64, False), old.analyze(target, "min", 64, False)
        assert _old_keys_fp(a_new) == _old_keys_fp(a_old)
        _assert_new_keys_equal(a_new, a_old, "%s/%s(补充逻辑)" % (tag, target))
        assert new.render_sv(None, "min", 64, False, None)[0] == \
            old.render_sv(None, "min", 64, False, None)[0]
        # 补充确实生效了（不是两边都没应用所以「相等」）
        plain = PV.TopoutProvider(_Cfg(wb))
        assert new.render_sv(None, "min", 64, False, None)[0] != \
            plain.render_sv(None, "min", 64, False, None)[0]
        assert wb.logic is not None                  # swap-and-restore：退出后 wb 没被改脏
    finally:
        w._logic_overrides = {}
        w._probe_prefixes, w._force_signals = saved_pp, saved_fo


# ══════════════ provider 自己的口子（旧门面没有的那几个）══════════════
_SKELETON_KEYS = {"name", "disp", "owner", "width", "kind", "status", "status_detail", "note",
                  "issues", "n_vectors", "form", "form_label", "probe_net", "prefix",
                  "assert_id", "matched_name"}


@pytest.mark.contract("C-276")
def test_skeleton_models_are_cheap_and_pending(btlp, wl):
    """N8 第一趟：清单先出 resolve_root 的便宜结果（无向量、状态 pending），让 200+ 信号的表
    **立刻可点**；用例数/真值表由后台 worker 逐信号回填。"""
    from dreg_verify import providers as PV
    for wb in (btlp, wl):
        prov = PV.TopoutProvider(_Cfg(wb))
        sk = prov.skeleton_models()
        full = prov.view_models("min", 64, False)
        assert [m["name"] for m in sk] == [m["name"] for m in full]        # 同一批、同一序
        for m in sk:
            assert _SKELETON_KEYS <= set(m)
            assert m["status"] == m["status_detail"] == "pending"
            assert m["n_vectors"] is None                                  # 骨架里【没有】用例数
            assert "tests" not in m and "chain" not in m                   # 也没有真值表/展开链
        # owner / 分类 / 探针网这些便宜信息必须是真的（清单第一趟就要能筛 owner、能显分类）
        by_name = {m["name"]: m for m in full}
        for m in sk:
            assert m["owner"] == by_name[m["name"]]["owner"]
            assert m["probe_net"] == by_name[m["name"]]["probe_net"]

        for page in P.PAGES:
            if not P.page_available(wb, page):
                continue
            pprov = PV.PageProvider(_Cfg(wb), page)
            psk = pprov.skeleton_models()
            assert [m["name"] for m in psk] == \
                [m["name"] for m in pprov.view_models("min", 64, False)]
            assert all(m["status"] == "pending" and m["n_vectors"] is None for m in psk)


def test_provider_locks_every_engine_call(btlp):
    """engine_lock：**每次**引擎调用都得在锁里——后台 worker 与主线程共用同一个 wb，
    Resolver/_supplemented 都会回写 wb 上的标记，漏一处就会读到半个状态。"""
    from dreg_verify import providers as PV
    lk = _CountingLock()
    prov = PV.TopoutProvider(_Cfg(btlp, engine_lock=lk))
    prov.skeleton_models()
    assert lk.n == 1
    prov.view_models("min", 64, False)
    assert lk.n == 2
    name = prov.skeleton_models()[0]["name"]
    assert lk.n == 3
    prov.analyze(name, "min", 64, False)
    assert lk.n == 4
    prov.render_sv(None, "min", 64, False, None)
    assert lk.n == 5
    prov.render_report("min", 64, False)
    assert lk.n == 6

    plk = _CountingLock()
    pprov = PV.PageProvider(_Cfg(btlp, engine_lock=plk), "logic")
    pprov.skeleton_models()
    pprov.view_models("min", 64, False)
    pprov.analyze(pprov.skeleton_models()[0]["name"], "min", 64, False)
    pprov.render_sv(None, "min", 64, False, None)
    pprov.render_report("min", 64, False)
    assert plk.n == 6                       # skeleton 调了两次

    # cfg 没给锁（单线程调用方）→ 照常跑，不炸
    assert PV.TopoutProvider(_Cfg(btlp)).view_models("min", 64, False)


def test_provider_fills_probe_prefix_on_an(btlp):
    """执行计划 §7-1 的另一半：探针网**在哪一层**是配置不是网名，引擎不该知道，
    拿着 probe_prefixes 的 provider 补这一格（GUI 的『探针前缀』列/诊断抽屉读它）。"""
    from dreg_verify import providers as PV
    from dreg_verify import topout as T
    plain = PV.TopoutProvider(_Cfg(btlp))
    models = plain.view_models("min", 64, False)
    target = next(m for m in models if m.get("probe_net"))
    pp = {target["probe_net"]: "U_TOP.U_SUB"}

    an = PV.TopoutProvider(_Cfg(btlp, probe_prefixes=pp)).analyze(target["name"], "min", 64, False)
    assert an["probe_prefix"] == "U_TOP.U_SUB"
    assert plain.analyze(target["name"], "min", 64, False)["probe_prefix"] == ""   # 没配 → 空串
    # 与清单列、与 topout 自己的口径同源（三处不许各算各的）
    assert an["probe_prefix"] == T._probe_prefix_for_name(pp, target["probe_net"])
    assert PV.TopoutProvider(_Cfg(btlp, probe_prefixes=pp)).view_models(
        "min", 64, False)[models.index(target)]["prefix"] == "U_TOP.U_SUB"
    # C0-a 合并后 an 自带 out_net → 以它为准（网名的真相源在引擎，不在这里重算）
    an2 = PV.TopoutProvider(_Cfg(btlp, probe_prefixes={"d_假网名": "U_X"}))
    assert an2.analyze(target["name"], "min", 64, False)["probe_prefix"] == ""

    for page in P.PAGES:
        if not P.page_available(btlp, page):
            continue
        pprov = PV.PageProvider(_Cfg(btlp), page)
        m = pprov.view_models("min", 64, False)[0]
        pan = PV.PageProvider(_Cfg(btlp, probe_prefixes={m["probe_net"]: "U_P"}), page).analyze(
            m["name"], "min", 64, False)
        assert pan["probe_prefix"] == "U_P" == P._prefix_for({m["probe_net"]: "U_P"}, m["probe_net"])


def test_provider_reads_force_signals_under_either_name(btlp):
    """cfg 的『强制 force』字段，contracts 叫 force_signals、架构文里也写过 force_overrides——
    两个名都认。读成「没配过」是这套配置最典型的坑：配了等于没配，且全程无提示。"""
    from dreg_verify import providers as PV
    models = PV.TopoutProvider(_Cfg(btlp)).view_models("min", 64, False)
    names = {m["name"] for m in models}

    class _Alt(object):
        wb = btlp
        probe_prefixes = {}
        force_overrides = set(list(names)[:2])       # 只有别名，没有 force_signals
        logic_overrides = {}
        include_risky = True

    a = PV.TopoutProvider(_Cfg(btlp, force_signals=set(list(names)[:2])))
    b = PV.TopoutProvider(_Alt())
    assert a._fo() == b._fo() == set(list(names)[:2])
    assert a.render_sv(None, "min", 64, False, None)[0] == b.render_sv(None, "min", 64, False, None)[0]
    assert PV.TopoutProvider(_Cfg(btlp))._fo() is None        # 空集 → None（引擎的「没配过」）


def test_provider_passes_include_risky_where_the_engine_takes_it(btlp):
    """C-217：include_risky 从 cfg 一路透传到引擎（C0-d 起五个入口全直传，不再探签名）。"""
    from dreg_verify import providers as PV
    from dreg_verify import pageviews as PP
    seen = {}
    real = PP.build_page_sv

    def _spy(wb, page, **kw):
        seen.update(kw)
        return real(wb, page, **kw)

    PP.build_page_sv = _spy
    try:
        PV.PageProvider(_Cfg(btlp, include_risky=False), "logic").render_sv(
            None, "min", 64, False, None)
        assert seen["include_risky"] is False
        PV.PageProvider(_Cfg(btlp), "logic").render_sv(None, "min", 64, False, None)
        assert seen["include_risky"] is True                  # cfg 缺字段 → True = 引擎写死值
    finally:
        PP.build_page_sv = real


@pytest.mark.contract("C-217")
def test_provider_status_detail_tiers_need_include_risky_and_probe_prefix(btlp, wl):
    """§7-2 三档实测：`an["status_detail"]` 的 needs-prefix / risky-generated / bare-probe
    **只能**由 provider 定——判据是 cfg 的 `include_risky` 与该信号 out_net 配的探针前缀，
    这两样引擎都不知道（C0-d 把它们接进 `norm_topout_result` / `norm_page_result`）。

    provider 不传这两个参数时归一化层用默认值 (True, "")，于是缺前缀的信号恒显
    `risky-generated`、裸名探针恒显 `bare-probe` —— 清单上就是一排假档。

      ① cfg.include_risky=False → 缺前缀的输入落 `needs-prefix`（硬阻断，整组跳过）
      ② cfg.include_risky=True  → 同一个信号落 `risky-generated`（照生成，裸名 force 交仿真验）
      ③ 给 out_net 配上探针前缀   → 裸名探针那档 `bare-probe` 变 `clean`
    """
    from dreg_verify import providers as PV

    # ①② 缺前缀的输入：wl 两个信号的叶子是埋在子模块里的衔接网
    on = PV.TopoutProvider(_Cfg(wl, include_risky=True))
    off = PV.TopoutProvider(_Cfg(wl, include_risky=False))
    for nm in ("d_wl_rf_lo2g5g_lcbufc0_2g_pfb_band_trim", "d_wl_rf_lp5g_gm_itrim"):
        assert on.analyze(nm, "min", 64, False)["status_detail"] == "risky-generated", nm
        assert off.analyze(nm, "min", 64, False)["status_detail"] == "needs-prefix", nm

    # 页视图那条流水线同一口径（两条流水线的档不许分叉）
    for page, nm in (("logic", "d_wl_rf_lo2g5g_bias_en"),
                     ("mux", "d_wl_rf_lo2g5g_mixer2g_trim[1:0]")):
        p_on = PV.PageProvider(_Cfg(wl, include_risky=True), page)
        p_off = PV.PageProvider(_Cfg(wl, include_risky=False), page)
        assert p_on.analyze(nm, "min", 64, False)["status_detail"] == "risky-generated", nm
        assert p_off.analyze(nm, "min", 64, False)["status_detail"] == "needs-prefix", nm

    # ③ 输出侧裸名探针：btlp 的 d_logic_bt_lp_tsensor 断言贴的是 *_to_mux 衔接网
    bare = "d_logic_bt_lp_tsensor"
    a = PV.TopoutProvider(_Cfg(btlp)).analyze(bare, "min", 64, False)
    assert a["status_detail"] == "bare-probe" and a["probe_prefix"] == ""
    assert a["out_net"] != bare                       # 贴的不是声明名 → 才会判 bare-probe
    b = PV.TopoutProvider(_Cfg(btlp, probe_prefixes={a["out_net"]: "U_TOP.U_SUB"})).analyze(
        bare, "min", 64, False)
    assert b["probe_prefix"] == "U_TOP.U_SUB" and b["status_detail"] == "clean"
    # 配到别的网上不算数（前缀按 out_net 查，不是「配过就算」）
    assert PV.TopoutProvider(_Cfg(btlp, probe_prefixes={"d_别的网": "U_X"})).analyze(
        bare, "min", 64, False)["status_detail"] == "bare-probe"


@pytest.mark.contract("C-276")
def test_provider_forwards_the_new_engine_params_directly(btlp):
    """C0-a 合并后 provider **直接**把 progress / should_cancel / lite / skeleton 传给引擎
    ——过渡期那套签名探测（`_accepts` / `_opt` / `_skeleton_fallback`）已在 C0-d 删掉。

    这里钉的不再是「传了没炸」，而是「真的接上了」：取消要真停、progress 要真回调、
    lite 要真给出同一批 LITE_MODEL_KEYS、骨架要真走引擎那个函数。"""
    from dreg_verify import providers as PV
    from dreg_verify import topout as T
    from dreg_verify.ui import contracts as C
    prov = PV.TopoutProvider(_Cfg(btlp))
    full = prov.view_models("min", 64, False)
    assert len(full) >= 4

    # ① should_cancel 在【下一个信号之前】生效 → 返回**部分**列表；progress 每信号回调一次
    ticks = []
    done = {"n": 0}

    def _tick(d, _t, _name, _m):
        done["n"] = d
        ticks.append((d, _t, _name, _m))

    part = prov.view_models("min", 64, False, progress=_tick,
                            should_cancel=lambda: done["n"] >= 2)
    assert len(ticks) == 2 and [d for d, _t, _n, _m in ticks] == [1, 2]
    assert all(t == len(full) for _d, t, _n, _m in ticks)
    assert [m["name"] for m in part] == [m["name"] for m in full[:2]]   # 已分析的照常返回
    # 一上来就取消 → 空列表（部分列表的极端情形），绝不静默跑完整表
    assert prov.view_models("min", 64, False, should_cancel=lambda: True) == []

    # ② lite=True 跳过全表联表，但 LITE_MODEL_KEYS 这一套键必须与 full 逐键相等
    lite = prov.view_models("min", 64, False, lite=True)
    assert len(lite) == len(full)
    for lm, fm in zip(lite, full):
        for k in C.LITE_MODEL_KEYS:
            assert lm[k] == fm[k], (lm["name"], k)
    assert all("chain" not in m and "tests" not in m for m in lite)     # 联表那几键确实没跑

    # ③ skeleton 走引擎的 topout_skeleton_models（本层不再留等价兜底实现）
    seen = {}
    real = T.topout_skeleton_models

    def _spy(wb, **kw):
        seen.update(kw)
        return real(wb, **kw)

    T.topout_skeleton_models = _spy
    try:
        sk = prov.skeleton_models()
    finally:
        T.topout_skeleton_models = real
    assert seen and set(seen) == {"probe_prefixes", "force_overrides", "logic_overrides"}
    assert [m["name"] for m in sk] == [m["name"] for m in full]

    # ④ 页视图：progress/should_cancel 同样直传（页引擎没有 lite，本层也不假装有）
    pticks = []
    PV.PageProvider(_Cfg(btlp), "logic").view_models(
        "min", 64, False, progress=lambda *a: pticks.append(a))
    assert len(pticks) == len(P.page_signals(btlp, "logic")) > 0
    assert PV.PageProvider(_Cfg(btlp), "logic").view_models(
        "min", 64, False, should_cancel=lambda: True) == []

    # ⑤ want_graph 直传 analyze_signal（Topout 那条路）
    an = prov.analyze(sk[0]["name"], "min", 64, False, want_graph=True)
    assert an is not None and "graph" in an
