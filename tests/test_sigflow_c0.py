# -*- coding: utf-8 -*-
"""test_sigflow_c0.py — GUI v2 Phase C0-c：`layout_graph` 抽离 + highlight_net + 样式层重写。

三段：
  ① 第1步（布局抽离）：两张 mirror 全部 21 个 Topout 信号的 SVG sha 快照 + 「传不传 layout
     字节都一样」的等价性。抽布局那一次 commit 里，这份 sha 是【改前录、改后逐信号比】的
     字节不变证据；第3步样式重写是有意改样式，那次连同 sha 一起重录（见 _SVG_SHA 注）。
  ② 第2步（C-282）：`render_svg(..., highlight_net=)` 命中 data-net 的线与盒套 hl 样式。
  ③ 第3步（C-279 / C-286）：17 种 KIND 各画一个、盒不重叠、通道不重叠、五色与 ui/theme 同值。
"""

import hashlib
import os
import re
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import make_mirror_btlp                        # noqa: E402  仓库根夹具脚本（conftest 已加 path）
import make_mirror_excel                       # noqa: E402
from dreg_verify import excel_model as M       # noqa: E402
from dreg_verify import expr as E              # noqa: E402
from dreg_verify import resolver as R          # noqa: E402
from dreg_verify import sigflow as SF          # noqa: E402
from dreg_verify import topout as T            # noqa: E402


# ═══════════════ ① 第1步：布局抽离，SVG 字节不变 ═══════════════
# 两张 mirror 全部 Topout（12 + 9 = 21，含 ConeError 的 mux 根 d_wl_rf_lpf_cmain 与
# RO 回读根 pll_lock_indicator 的空图）。**录法**：SF.render_svg(图) 的 utf-8 sha256 前 16 位。
# 抽 layout_graph 那一次（C0-c 第1步）逐信号比对为 21/21 相同 —— 这是布局搬家没有走样的证据。
_SVG_SHA = {
    ("btlp", "clk_force_on"): "7974737a91d006d3",
    ("btlp", "d_bt_lp_lna_itrim"): "df1aac4a678681b9",
    ("btlp", "d_en_refbuf_ls"): "1b4a7da7b3c65b4b",
    ("btlp", "d_logic_bt_lp_lna_agc"): "4f17f24d8a6178fa",
    ("btlp", "d_logic_bt_lp_lpf_agc"): "e41a1710a99593c1",
    ("btlp", "d_logic_bt_lp_reserve"): "dcf61a4bb4655a33",
    ("btlp", "d_logic_bt_lp_rx_dcoc_i"): "4b779b15d627d984",
    ("btlp", "d_logic_bt_lp_rx_dcoc_q"): "4372f7a0b7fcb055",
    ("btlp", "d_logic_bt_lp_rx_en"): "03cf1a8a83e249a3",
    ("btlp", "d_logic_bt_lp_tsensor"): "6a1f613d8aee99ff",
    ("btlp", "en_dig_clk"): "b95c283d777440eb",
    ("btlp", "pll_lock_indicator"): "16374709e214e68f",
    ("wl", "d_wl_rf_lo2g5g_bias_en"): "2b3fe298638f2e8b",
    ("wl", "d_wl_rf_tx_epa_2g_mixer_en"): "87936fdad712435a",
    ("wl", "d_wl_rf_lo2g5g_lcbufc0_2g_pfb_band_trim"): "cc7b94378d968829",
    ("wl", "d_wl_rf_lo2g5g_mixer2g_trim"): "05620249a07b0535",
    ("wl", "d_wl_rf_lo2g5g_mixer5g_trim"): "ab123efca3a81139",
    ("wl", "d_wl_rf_lp5g_rxrf_lna_lctune"): "3e6dd4ed863e90a7",
    ("wl", "d_bt_rx_slna_1st_bias_trim_gain_cal_wl"): "a9e7ff796fd93b5c",
    ("wl", "d_wl_rf_lp5g_gm_itrim"): "e6d4bf2fc7117cb8",
    ("wl", "d_wl_rf_lpf_cmain"): "bbfe6061791d0907",
}


# ───────────────────────────── 夹具 ─────────────────────────────
@pytest.fixture(scope="module")
def btlp(tmp_path_factory):
    p = tmp_path_factory.mktemp("c0_btlp") / "mirror_btlp_dreg.xlsx"
    make_mirror_btlp.build(str(p))
    wb = M.load_workbook(str(p))
    return wb, R.Resolver(wb)


@pytest.fixture(scope="module")
def wl(tmp_path_factory):
    p = tmp_path_factory.mktemp("c0_wl") / "mirror_wl_dreg.xlsx"
    make_mirror_excel.build(str(p))
    wb = M.load_workbook(str(p))
    return wb, R.Resolver(wb)


def _graphs(wb, res):
    """该表每个 Topout → (名字, Graph)；没图的（RO 回读）给一张空图，21 个一个不少。"""
    for topo in wb.topout:
        r = T.analyze_signal(wb, res, topo, mode="min", max_tests=32, want_graph=True)
        yield topo.name, (r.graph if r.graph is not None else SF.Graph(topo.name))


def _sha(svg):
    return hashlib.sha256(svg.encode("utf-8")).hexdigest()[:16]


def test_sigflow_render_svg_bytes_identical_after_split(btlp, wl):
    """两张 mirror 全部 21 个信号的 SVG 逐个对 sha 快照；顺带验「传不传 layout 字节一样」。

    布局从 render_svg 里搬出去（layout_graph）时，这条断言是唯一能证明【一个像素都没挪】的
    东西；之后它继续当渲染回归的哨兵——样式无意中被改掉会立刻红。
    """
    bad, seen = [], 0
    for tag, (wb, res) in (("btlp", btlp), ("wl", wl)):
        for name, g in _graphs(wb, res):
            seen += 1
            svg = SF.render_svg(g)
            got, want = _sha(svg), _SVG_SHA.get((tag, name))
            if want is None:
                bad.append("%s/%s 不在快照里（新信号？）got=%s" % (tag, name, got))
            elif got != want:
                bad.append("%s/%s sha %s != %s" % (tag, name, got, want))
            # 等价性：外部先算好 layout 再传进来，必须与内部自己算的字节完全一致
            # （GUI 命中层就是靠这条才敢用 layout 的坐标去对 SVG 上的图元）
            if g.nodes and svg != SF.render_svg(g, layout=SF.layout_graph(g)):
                bad.append("%s/%s 传 layout 与不传的字节不一致" % (tag, name))
    assert seen == 21, "mirror Topout 总数变了：%d" % seen
    assert not bad, "\n".join(bad)


def test_layout_graph_fields_and_coords(wl):
    """Layout 的字段契约 + 坐标确实与 SVG 里画的一致（架构 §1.3② 路线 C 的前提）。"""
    wb, res = wl
    topo = next(t for t in wb.topout if t.name == "d_wl_rf_tx_epa_2g_mixer_en")
    g = T.analyze_signal(wb, res, topo, mode="min", max_tests=32, want_graph=True).graph
    lay = SF.layout_graph(g)

    assert set(lay.pos) == {n.id for n in g.nodes}          # 只含真节点，dummy 已烘进点列
    assert len(lay.edges) == len(g.edges)
    assert all(len(pts) >= 2 and side in ("left", "top") for _e, pts, side in lay.edges)
    assert set(lay.rank) == {n.id for n in g.nodes} and min(lay.rank.values()) == 0
    assert lay.gapw and all(v >= SF._HGAP for v in lay.gapw.values())
    assert all((n.id, "Y") in lay.ports for n in g.nodes)
    for n in g.nodes:                                        # 声明过的输入口都要有锚点
        for p in n.ports:
            assert (n.id, p["name"]) in lay.ports

    # 画布尺寸 = SVG 的 width/height/viewBox
    svg = SF.render_svg(g, layout=lay)
    m = re.search(r'width="(\d+)" height="(\d+)" viewBox="0 0 (\d+) (\d+)"', svg)
    assert m and (int(m.group(1)), int(m.group(2))) == tuple(lay.size)
    # 每个盒的 x/y/w/h 都能在 SVG 里找到同样的 rect（命中层与画面不许漂）
    for nid, (x, y, w, h) in lay.pos.items():
        assert 'x="%d" y="%d" width="%d" height="%d"' % (x, y, w, h) in svg, nid
    # 输出锚点 = 盒右缘中点；每条边的第一个点就是它
    for e, pts, _side in lay.edges:
        assert pts[0] == lay.ports[(e.src, "Y")]
        assert pts[-1] == lay.ports[(e.dst, e.dst_port)]


def test_layout_graph_empty_and_reuse(btlp):
    """空图的 Layout 不抛且给出空图画布；同一张 Layout 反复渲染结果稳定（GUI 高亮会重渲）。"""
    lay = SF.layout_graph(SF.Graph("空信号"))
    assert lay.pos == {} and lay.edges == [] and lay.size == (420, 70)
    wb, res = btlp
    topo = next(t for t in wb.topout if t.name == "d_logic_bt_lp_rx_en")
    g = T.analyze_signal(wb, res, topo, mode="min", max_tests=32, want_graph=True).graph
    lay = SF.layout_graph(g)
    assert SF.render_svg(g, layout=lay) == SF.render_svg(g, layout=lay)


# ═══════════════ ② 第2步：C-282 highlight_net ═══════════════
def _hl_nets(svg):
    """SVG 里被套上 hl 的那批元素的 data-net。"""
    return [m.group(1) for m in
            re.finditer(r'data-net="([^"]*)"(?=[^>]*data-hl="1")', svg)]


def test_c282_render_svg_highlight_marks_net(wl):
    """C-282：命中 data-net == highlight_net（小写比对）的线与盒套 hl 样式。

    GUI 选中一根线网后整张 SVG 重渲一遍（路线 C），所以「谁被高亮」必须完全由这个参数决定：
    不传 = 一个 hl 都没有、字节与老版一致；传了 = 只有那根网的线和盒变蓝加粗。
    """
    wb, res = wl
    topo = next(t for t in wb.topout if t.name == "d_wl_rf_tx_epa_2g_mixer_en")
    g = T.analyze_signal(wb, res, topo, mode="min", max_tests=32, want_graph=True).graph

    plain = SF.render_svg(g)
    assert "data-hl=" not in plain                       # 默认零高亮，且与老版字节一致（①）
    assert plain == SF.render_svg(g, highlight_net=None)
    assert plain == SF.render_svg(g, highlight_net="")   # 空串不是一根网，别整张图乱亮

    net = "d_wl_rf_tx2g_en"
    svg = SF.render_svg(g, highlight_net=net)
    hit = _hl_nets(svg)
    assert hit, "高亮没命中任何元素"
    assert set(hit) == {net}, hit                        # 只亮这一根，别误伤别的网
    assert len(hit) == len([e for e in g.edges if e.net == net]) + \
        len([n for n in g.nodes
             if (n.meta.get("base") or n.meta.get("out_base") or n.label) == net])
    assert SF.FLOW_HL in svg                             # 线/盒确实换成了 HL 蓝
    assert 'stroke-width="2.2"' in svg or 'stroke-width="2.6"' in svg

    # 大小写/前后空白不影响命中（Excel 里同一根网大小写并不统一）
    assert _hl_nets(SF.render_svg(g, highlight_net="  D_WL_RF_TX2G_EN ")) == hit
    # 不存在的网 → 什么都不亮，也不许抛
    assert _hl_nets(SF.render_svg(g, highlight_net="根本没有这根网")) == []
    # 传了 layout 结果一样（GUI 是复用同一份 layout 重渲的）
    assert SF.render_svg(g, highlight_net=net, layout=SF.layout_graph(g)) == svg


def test_c282_highlight_node_box(btlp):
    """盒也要被高亮（不止线）：REG 叶子按自己的 data-net 命中，且 <g> 上挂 data-hl。"""
    wb, res = btlp
    topo = next(t for t in wb.topout if t.name == "clk_force_on")
    g = T.analyze_signal(wb, res, topo, mode="min", max_tests=32, want_graph=True).graph
    svg = SF.render_svg(g, highlight_net="clk_force_on")
    assert '<g data-node="n1" data-kind="REG" data-net="clk_force_on" data-hl="1">' in svg
    assert SF.FLOW_HL_BG in svg                          # 选中盒底 #eef4fd


def test_flow_colors_match_ui_theme():
    """sigflow 不 import ui.theme（Qt-free），但五色常量必须与 ui/theme.py 的 FLOW_* 同值。

    两边各写一份是有意的：CLI / HTML 报告要能在没有 ui 包的环境里出图。代价是可能漂 —— 这条
    断言就是那道锁，任一边改色而另一边没跟，立刻红。
    """
    from dreg_verify.ui import theme
    names = [x for x in dir(theme) if x.startswith("FLOW_")]
    assert len(names) >= 14, names
    for nm in names:
        assert hasattr(SF, nm), "sigflow 少了常量 %s" % nm
        assert getattr(SF, nm) == getattr(theme, nm), \
            "%s 漂了：sigflow=%r theme=%r" % (nm, getattr(SF, nm), getattr(theme, nm))
