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
    ("btlp", "clk_force_on"): "13bbcf9323001c64",
    ("btlp", "d_bt_lp_lna_itrim"): "5e92669ce69bd017",
    ("btlp", "d_en_refbuf_ls"): "e059f790358188e1",
    ("btlp", "d_logic_bt_lp_lna_agc"): "ec270c1d5a191c60",
    ("btlp", "d_logic_bt_lp_lpf_agc"): "5da31e1a1cc97ab9",
    ("btlp", "d_logic_bt_lp_reserve"): "9da3b13977dbb7cd",
    ("btlp", "d_logic_bt_lp_rx_dcoc_i"): "f10cd40b8219421c",
    ("btlp", "d_logic_bt_lp_rx_dcoc_q"): "3256ec40da06b3f9",
    ("btlp", "d_logic_bt_lp_rx_en"): "8a6f1d9e6ba58b86",
    ("btlp", "d_logic_bt_lp_tsensor"): "133cce6e964d80b8",
    ("btlp", "en_dig_clk"): "b69709ae294490a1",
    ("btlp", "pll_lock_indicator"): "16374709e214e68f",
    ("wl", "d_wl_rf_lo2g5g_bias_en"): "f2c5399628658988",
    ("wl", "d_wl_rf_tx_epa_2g_mixer_en"): "9919e21e5b7489a2",
    ("wl", "d_wl_rf_lo2g5g_lcbufc0_2g_pfb_band_trim"): "c1ee9ba0a1172116",
    ("wl", "d_wl_rf_lo2g5g_mixer2g_trim"): "c1732fc03aa46acd",
    ("wl", "d_wl_rf_lo2g5g_mixer5g_trim"): "f7e7b924867b0b8b",
    ("wl", "d_wl_rf_lp5g_rxrf_lna_lctune"): "49d290f21be9a752",
    ("wl", "d_bt_rx_slna_1st_bias_trim_gain_cal_wl"): "4e6fad74ed87bab7",
    ("wl", "d_wl_rf_lp5g_gm_itrim"): "d6840e145caa6541",
    ("wl", "d_wl_rf_lpf_cmain"): "3665431536f6b538",
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
    # 每个图元的包围盒都原样写在 SVG 上（data-box）——十三种形里矩形只剩几种，命中层不能再靠
    # 认 <rect>；有了 data-box，「命中层坐标 == 画面坐标」就是一条可机检的等式
    for nid, (x, y, w, h) in lay.pos.items():
        assert 'data-box="%d,%d,%d,%d"' % (x, y, w, h) in svg, nid
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
    assert 'data-node="n1" data-kind="REG" data-shape="reg" data-net="clk_force_on"' in svg
    assert re.search(r'data-node="n1"[^>]*data-hl="1"', svg)
    assert SF.FLOW_HL_BG in svg                          # 选中盒底 #eef4fd
    assert SF.FLOW_HL in svg                             # REG 左色条高亮时也转蓝


# ═══════════════ ③ 第3步：样式层重写（C-279 十三种图元 / C-286 版面） ═══════════════
def _qt_ok(svg):
    """SVG 能被 QtSvg 真解析（GUI 就是拿 QSvgRenderer 显示它的，吐半截会白屏）。"""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QByteArray
    from PySide6.QtSvg import QSvgRenderer
    r = QSvgRenderer(QByteArray(svg.encode("utf-8")))
    return r.isValid() and r.defaultSize().width() > 0


def _one_kind_graph(kind):
    """造一张「该图元 → TOPOUT」的最小图（数据层照常只读，这里只用公开的 add_node/add_edge）。"""
    g = SF.Graph("kind_" + kind)
    meta, ports, sub = {}, [], ""
    if kind == "REG":
        meta = {"trusted": True, "base": "some_reg", "address": 0x2d}
        sub = "RW @0x2d[4:4]"
    elif kind == "PIN":
        meta = {"trusted": False, "base": "some_wire", "tip": SF.UNTRUSTED_TIP,
                "found_in": "wire"}
        sub = "RO force some_wire"
    elif kind == "GATE":
        meta = {"bubble": True, "gate_base": "iddq_mode", "transparent": 0}
        sub = "iddq_mode ? 0 : D"
        ports = [{"name": "G", "side": "top", "label": "iddq_mode"},
                 {"name": "D", "side": "left", "label": "功能"}]
    elif kind in ("NAND", "NOR", "NOT"):
        meta = {"bubble": True}
        ports = [{"name": "A", "side": "left", "label": ""}]
    elif kind == "MUX2":
        ports = [{"name": "S", "side": "top", "label": "sel"},
                 {"name": "D1", "side": "left", "label": "1"},
                 {"name": "D0", "side": "left", "label": "0"}]
    elif kind == "MUXN":
        sub = "case(sel)"
        meta = {"group": 3, "cases": ["2'b00", "2'b11"], "case_rows": [58, 214],
                "conflict_rows": [214], "shadowed": [], "undriven_cases": []}
        ports = [{"name": "S0", "side": "top", "label": "sel"},
                 {"name": "D0", "side": "left", "label": "2'b00"},
                 {"name": "D1", "side": "left", "label": "2'b11 ⚠conflict"}]
    elif kind == "RENAME":
        meta = {"source": "src_net", "probe": "top_port"}
        sub = "src_net → top_port"
        ports = [{"name": "A", "side": "left", "label": ""}]
    elif kind in ("BUSTAP", "BUSMERGE", "REDUCE", "CMP", "OP"):
        sub = "抽头"
        ports = [{"name": "A", "side": "left", "label": ""}]
    elif kind == "TOPOUT":
        ports = [{"name": "A", "side": "left", "label": ""}]
    else:
        ports = [{"name": "A", "side": "left", "label": ""}]
    n = g.add_node(kind, kind.title(), sub=sub, ports=ports, meta=meta)
    if kind == "TOPOUT":
        src = g.add_node("REG", "some_reg", sub="RW @0x2d[4:4]",
                         meta={"trusted": True, "base": "some_reg", "address": 0x2d})
        g.add_edge(src.id, n.id, "A", net="some_reg", width=1)
        return g, n
    top = g.add_node("TOPOUT", "fake_out", ports=[{"name": "A", "side": "left", "label": ""}])
    for p in n.ports:
        src = g.add_node("REG", "in_" + p["name"], sub="RW @0x1[0:0]",
                         meta={"trusted": True, "base": "in_" + p["name"], "address": 1})
        g.add_edge(src.id, n.id, p["name"], net="in_" + p["name"], label=p["label"])
    if not n.ports:
        src = g.add_node("REG", "in_a", sub="RW @0x1[0:0]",
                         meta={"trusted": True, "base": "in_a", "address": 1})
        g.add_edge(src.id, n.id, "A", net="in_a")
    g.add_edge(n.id, top.id, "A", net="fake_out", width=4)
    return g, n


def test_c279_all_kinds_render():
    """C-279：`KINDS` 每一种都造一张图渲染 —— 不抛、QtSvg 解析得了、带自己那个形状标记。

    Design 只画了 reg/AND/OR/MUX/TOP 五种，剩下的按 V2Spec §4「电路图图元」的文字规范补形。
    形状用 `data-shape` 钩子认（别去比 path 的 d：那是随盒子尺寸变的），种类仍挂 data-kind。
    """
    assert len(SF.KINDS) == len(set(SF.KINDS)) == 19       # 17 种逻辑图元 + GATE + TOPOUT
    seen = set()
    for kind in SF.KINDS:
        g, n = _one_kind_graph(kind)
        svg = SF.render_svg(g)
        assert _qt_ok(svg), "%s 渲出来的 SVG QtSvg 解析不了" % kind
        assert 'data-kind="%s"' % kind in svg, kind
        shape = SF._SHAPE[kind]
        assert 'data-shape="%s"' % shape in svg, kind
        assert 'data-node="%s"' % n.id in svg and 'data-box="' in svg
        seen.add(shape)
        # 五色以外的糖果色底一个都不许再冒出来
        assert "#eef2ff" not in svg and "#111827" not in svg and "#fef3c7" not in svg, kind
    assert seen == set(SF._SHAPE.values())

    # 逐条核对九项样式（Design §6.2）落到了具体图元上
    svg = SF.render_svg(_one_kind_graph("REG")[0])
    assert 'data-bar="1"' in svg and SF.FLOW_REG_BAR in svg          # ① REG 左 5px 色条
    svg = SF.render_svg(_one_kind_graph("MUXN")[0])
    assert '<path d="M' in svg                                       # ② MUX 梯形
    assert "Excel 行 214" in svg                                     # ⑥ 冲突支写出 Excel 行号
    assert SF.FLOW_BAD in svg and 'stroke-dasharray="%s"' % SF.FLOW_DASH_BAD in svg
    svg = SF.render_svg(_one_kind_graph("GATE")[0])
    assert 'data-bubble="in"' in svg                                 # ③ 门的气泡在【输入侧】
    assert 'data-bubble="out"' not in svg
    for k in ("NAND", "NOR", "NOT"):                                 # 输出取反的仍在输出侧
        assert 'data-bubble="out"' in SF.render_svg(_one_kind_graph(k)[0]), k
    assert "≥1" in SF.render_svg(_one_kind_graph("OR")[0])           # ④ OR 曲线体写 ≥1
    svg = SF.render_svg(_one_kind_graph("TOPOUT")[0])
    assert SF.FLOW_TOP_BG in svg and "顶层输出" in svg               # ⑤ TOP 箭头形 + 浅蓝底
    svg = SF.render_svg(_one_kind_graph("PIN")[0])
    assert SF.UNTRUSTED_TIP in svg and "※" not in svg                # ⑨ 猜名提示并进副标
    assert "RO force ·" in svg                                       # 网名不再抄第二遍
    assert SF.FLOW_GUESS_BG in svg


def test_c279_legend_and_line_styles(wl):
    """⑦ 图例行四项 + ⑧ 四种线型都在真图上出现过。"""
    wb, res = wl
    topo = next(t for t in wb.topout if t.name == "d_wl_rf_lp5g_rxrf_lna_lctune")
    g = T.analyze_signal(wb, res, topo, mode="min", max_tests=32, want_graph=True).graph
    svg = SF.render_svg(g, highlight_net="d_wl_rf_lp5g_rxrf_lna_lctune")
    for key in ("reg", "guess", "hl", "bad"):
        assert 'data-legend="%s"' % key in svg, key
    for word in ("寄存器（表里查到地址）", "名字来自命名约定，表里未查到",
                 "当前选中线网", "规格冲突的 case 支"):
        assert word in svg, word
    # 四种线型：普通 1.2 / bus 2.4 / hl 2.2 / 猜名 ghost 虚线
    assert 'stroke-width="1.2"' in svg and 'stroke-width="2.4"' in svg
    assert 'stroke-width="2.2"' in svg
    assert 'stroke-dasharray="%s"' % SF.FLOW_DASH_GHOST in svg
    assert _qt_ok(svg)


def _rects_overlap(a, b):
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    return ax < bx + bw and bx < ax + aw and ay < by + bh and by < ay + ah


def test_c286_layout_no_overlap(btlp, wl):
    """C-286：① 任意两个图元的包围盒不重叠；② 一条列缝里 ≤6 条竖段时，通道两两不重叠。

    重叠 = 两个盒糊在一起 / 两根线并成一根粗棍，图当场废掉。通道只有 `_NCH`=6 条，超过 6 条
    的缝会绕回去共用通道（那是已知取舍，不在这条断言的范围里）。
    """
    bad = []
    for tag, (wb, res) in (("btlp", btlp), ("wl", wl)):
        for name, g in _graphs(wb, res):
            if not g.nodes:
                continue
            lay = SF.layout_graph(g)
            boxes = sorted(lay.pos.items())
            for i, (na, ra) in enumerate(boxes):
                for nb, rb in boxes[i + 1:]:
                    if _rects_overlap(ra, rb):
                        bad.append("%s/%s 盒重叠 %s%s %s%s" % (tag, name, na, ra, nb, rb))

            # 列带：每一列的 [左缘, 右缘]，缝 g 就是 列g 右缘 → 列g+1 左缘 之间那段
            band = {}
            for nid, (x, _y, w, _h) in lay.pos.items():
                r = lay.rank[nid]
                lo, hi = band.get(r, (x, x + w))
                band[r] = (min(lo, x), max(hi, x + w))
            gaps = {}                       # 缝号 → [(lane_x, y0, y1)…]
            for _e, pts, _side in lay.edges:
                for (ax, ay), (bx, by) in zip(pts, pts[1:]):
                    if abs(ax - bx) > 0.5 or abs(ay - by) < 0.5:
                        continue            # 只看竖段
                    for r, (_lo, hi) in band.items():
                        nxt = band.get(r + 1)
                        if nxt is not None and hi < ax < nxt[0]:
                            gaps.setdefault(r, []).append((ax, min(ay, by), max(ay, by)))
                            break
            for r, segs in gaps.items():
                if len(segs) > 6:           # 通道只有 6 条，更多就必然共用（已知取舍）
                    continue
                for i, (xa, a0, a1) in enumerate(segs):
                    for xb, b0, b1 in segs[i + 1:]:
                        if abs(xa - xb) < 1.0 and a0 < b1 and b0 < a1:
                            bad.append("%s/%s 缝%d 两条竖段重叠于 x=%.0f" % (tag, name, r, xa))
    assert not bad, "\n".join(bad[:20])


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
