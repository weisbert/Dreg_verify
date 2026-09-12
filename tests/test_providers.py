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

import inspect
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
