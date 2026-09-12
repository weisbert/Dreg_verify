# -*- coding: utf-8 -*-
"""GUI v2 C2-b：电路图 `ui/sigflow_view.py` 的契约测试（附录 A「FLOW」5 条，每条测试名含 ID）。

口径（执行计划 §2 L3/L4/L5）：
  · offscreen 起**独立** `SigflowView`（不起 MainWindow），鼠标 / 滚轮一律发**真实事件**
    （`QTest` / `QWheelEvent` / `QHelpEvent`），不直接调槽、不 emit 信号冒充用户；
  · 图数据是 **mirror 的真图** —— `providers.TopoutProvider(cfg).analyze(..., want_graph=True)`，
    wl 的三级 mux 级联（`d_wl_rf_lpf_cmain`：mux202→mux58→mux57，6 列 11 节点）
    与 btlp 的直连寄存器（`clk_force_on`：REG→TOPOUT 两节点）各一，
    外加 wl 的多级门 + mux（`d_wl_rf_lo2g5g_bias_en`）；
  · 命中层与画面的坐标一致性直接对 SVG 的 `data-box` / `<polyline points>` 逐个比（路线 C 的命门）；
  · `ui/bus.py` 用实物（C1-a 已合入），`ui/state.py` 不参与 —— 本模块只吃 an。
"""

import re

import pytest

import ui_harness as H

pytest.importorskip("PySide6")

from PySide6 import QtCore, QtGui, QtWidgets          # noqa: E402
from PySide6.QtSvgWidgets import QGraphicsSvgItem     # noqa: E402
from PySide6.QtTest import QTest                      # noqa: E402

from dreg_verify import excel_model, providers, sigflow   # noqa: E402
from dreg_verify.ui import bus as BUS                 # noqa: E402
from dreg_verify.ui import names as N                 # noqa: E402
from dreg_verify.ui import sigflow_view as FV         # noqa: E402
from dreg_verify.ui import terms as T                 # noqa: E402
from dreg_verify.ui import theme as TH                # noqa: E402

Qt = QtCore.Qt

#: 三种规模的真信号（§6.10 完成标准 / 截图 b13df23d 的诉求）
MUX3 = ("wl", "d_wl_rf_lpf_cmain")            # 三级 mux 级联
GATES = ("wl", "d_wl_rf_lo2g5g_bias_en")      # 多级门 + mux（带 dft 门反相气泡）
DIRECT = ("btlp", "clk_force_on")             # 直连寄存器：REG → TOPOUT，两个节点
SLICED = ("wl", "d_wl_rf_tx_epa_2g_mixer_en")  # 线上带位切片（`…freq_sel[1]`）—— 两把钥匙的对齐点
NOGRAPH = ("btlp", "pll_lock_indicator")      # 只读回读：status=skip，an['graph'] is None


# ─────────────────────────── 真图（mirror）───────────────────────────
class _Cfg(object):
    """`providers.ConfigSourceProto` 的最小满足物（诊断配置全空 = 出厂态）。"""

    def __init__(self, wb):
        self.wb = wb
        self.probe_prefixes = {}
        self.force_signals = set()
        self.logic_overrides = {}
        self.include_risky = True


_AN_CACHE = {}


def analysis(kind, name):
    """mirror 的真 an（含 `graph`）。整张表的 provider 缓存起来，21 个信号只建一次工作簿。"""
    key = (kind, name)
    if key not in _AN_CACHE:
        prov = _AN_CACHE.get(("prov", kind))
        if prov is None:
            prov = providers.TopoutProvider(_Cfg(excel_model.load_workbook(H.mirror_path(kind))))
            _AN_CACHE[("prov", kind)] = prov
        _AN_CACHE[key] = prov.analyze(name, "min", 256, False, want_graph=True)
    return _AN_CACHE[key]


def make_view(sig=MUX3, size=(1100, 560), show=True):
    """独立起一个 `SigflowView` + 实物 bus，喂一个真信号。返回 (view, bus, an)。"""
    H.app()
    bus = BUS.HighlightBus()
    v = FV.SigflowView(bus=bus)
    v.resize(*size)
    if show:
        v.show()
        H.app().processEvents()
    an = analysis(*sig) if sig else None
    if an is not None:
        v.set_signal(an, name=sig[1])
        H.app().processEvents()
    return v, bus, an


# ─────────────────────────── 真实事件 ───────────────────────────
def wheel(canvas, pos, notches=1, modifier=Qt.ControlModifier):
    """真 `QWheelEvent` 发给 **viewport**（QGraphicsView 的滚轮是经 viewport 转进来的）。"""
    p = QtCore.QPointF(pos)
    ev = QtGui.QWheelEvent(p, QtCore.QPointF(canvas.viewport().mapToGlobal(pos)),
                           QtCore.QPoint(0, 0), QtCore.QPoint(0, 120 * int(notches)),
                           Qt.NoButton, modifier, Qt.NoScrollPhase, False)
    QtWidgets.QApplication.sendEvent(canvas.viewport(), ev)
    H.app().processEvents()


def tooltip_at(canvas, viewport_pos):
    """真 `QHelpEvent(ToolTip)` → Qt 自己走 scene->helpEvent 找命中体，返回真正弹出的文本。"""
    ev = QtGui.QHelpEvent(QtCore.QEvent.ToolTip, viewport_pos,
                          canvas.viewport().mapToGlobal(viewport_pos))
    QtWidgets.QApplication.sendEvent(canvas.viewport(), ev)
    H.app().processEvents()
    return QtWidgets.QToolTip.text()


def point_on(canvas, item):
    """item 上一个「点下去真能命中它」的 viewport 坐标（盒压线，边的中点常被盒盖住）。"""
    if isinstance(item, FV.EdgeHit):
        cands = [item.path().pointAtPercent(t / 20.0) for t in range(1, 20)]
    else:
        cands = [item.rect().center()]
    for sp in cands:
        if canvas.hit_at(sp) is item:
            return canvas.mapFromScene(sp)
    raise AssertionError("命中体 %r 上找不到不被遮挡的点（net=%r）"
                         % (item, item.data(FV.ROLE_NET)))


def svg_boxes(svg_text):
    """SVG 里每个图元 `<g>` 的 `data-box` → {nid: (x, y, w, h)}。"""
    out = {}
    for nid, box in re.findall(r'data-node="([^"]+)"[^>]*data-box="([^"]+)"', svg_text):
        out[nid] = tuple(int(x) for x in box.split(","))
    return out


def svg_polylines(svg_text):
    """SVG 里每条线 → [(net, [(x, y)…], 是否 data-hl)]。"""
    out = []
    for m in re.finditer(r'<polyline points="([^"]*)"[^>]*data-net="([^"]*)"([^>]*)/>', svg_text):
        pts = [tuple(float(v) for v in p.split(",")) for p in m.group(1).split() if p]
        out.append((m.group(2), pts, 'data-hl="1"' in m.group(3)))
    return out


# ═══════════════════════════ C-278 每个信号一张门级图 ═══════════════════════════
def test_c278_svg_item_present_for_mux_signal():
    """三级 mux 级联：SVG 画面 + 命中层都在，源寄存器在左、顶层输出在右，每条线挂 data-net。"""
    v, _bus, an = make_view(MUX3)
    c = v.canvas
    assert c.graph is not None and c.layout is not None
    items = [i for i in c._scene.items() if isinstance(i, QGraphicsSvgItem)]
    assert len(items) == 1, "画面必须是 render_svg 出来的那一个 QGraphicsSvgItem（路线 C）"
    assert v.body.currentWidget() is c

    # 命中层：每条边一个 EdgeHit、每个节点一个 NodeHit，一个不多一个不少
    edges = [i for i in c.hits() if isinstance(i, FV.EdgeHit)]
    nodes = [i for i in c.hits() if isinstance(i, FV.NodeHit)]
    assert len(edges) == len(c.layout.edges) == len(an["graph"].edges)
    assert len(nodes) == len(c.layout.pos) == len(an["graph"].nodes)

    # 三级 mux 级联：至少三个 MUXN，且分处不同列
    muxn = [n for n in an["graph"].nodes if n.kind == "MUXN"]
    assert len(muxn) >= 3, "选的不是三级 mux 级联：%s" % [n.kind for n in an["graph"].nodes]
    assert len({c.layout.rank[n.id] for n in muxn}) >= 3

    # 源寄存器在左、顶层输出在右
    regs = [n for n in an["graph"].nodes if n.kind == "REG"]
    tops = [n for n in an["graph"].nodes if n.kind == "TOPOUT"]
    assert regs and tops
    assert max(c.layout.pos[n.id][0] for n in regs) < min(c.layout.pos[n.id][0] for n in tops)

    # 每条有网名的线都挂了 data-net（C-282 的前提）
    named = [e for e in an["graph"].edges if e.net]
    assert named and all(('data-net="%s"' % e.net) in c.svg_text for e in named)
    H.shot(v, "flow_mux3_fit")
    v.close()


def test_c278_direct_register_signal_renders_two_nodes():
    """直连寄存器（btlp `clk_force_on`）：两个节点一条线也要画得出来（§6.10 完成标准）。"""
    v, _bus, an = make_view(DIRECT)
    assert len(an["graph"].nodes) == 2 and len(an["graph"].edges) == 1
    assert len([i for i in v.canvas.hits() if isinstance(i, FV.NodeHit)]) == 2
    assert v.canvas.zoom() == pytest.approx(1.0), "小图装得下就停在 100%，不放大"
    H.shot(v, "flow_direct_100")
    v.close()


# ═══════════════════════════ C-281 缩放平移 ═══════════════════════════
def test_c281_zoom_clamp_step_anchor_doubleclick_reset():
    """Ctrl+滚轮：步进 ZOOM_STEP、clamp [ZOOM_MIN, ZOOM_MAX]、以光标为锚；双击复位到 100%。"""
    v, _bus, _an = make_view(MUX3)
    c = v.canvas
    anchor = QtCore.QPoint(260, 180)

    # ① 步进 = ZOOM_STEP（一格一档）
    c.set_zoom(1.0)
    wheel(c, anchor, +1)
    assert c.zoom() == pytest.approx(TH.ZOOM_STEP, rel=1e-6)
    wheel(c, anchor, -1)
    assert c.zoom() == pytest.approx(1.0, rel=1e-6)

    # ② 以光标为锚：锚点下的场景坐标缩放前后不动（先放大到两向都有滚动余量，否则是滚动条顶到头）
    c.set_zoom(2.0)
    for bar in (c.horizontalScrollBar(), c.verticalScrollBar()):
        assert bar.maximum() > bar.minimum(), "这一档没有滚动余量，锚点断言验不到东西"
        bar.setValue((bar.minimum() + bar.maximum()) // 2)
    before = c.mapToScene(anchor)
    wheel(c, anchor, +1)
    after = c.mapToScene(anchor)
    assert abs(before.x() - after.x()) <= 1.5 and abs(before.y() - after.y()) <= 1.5

    # ③ clamp 上限：一路放大也不越过 ZOOM_MAX
    for _ in range(40):
        wheel(c, anchor, +1)
    assert c.zoom() == pytest.approx(TH.ZOOM_MAX, rel=1e-6)
    # ④ clamp 下限
    for _ in range(60):
        wheel(c, anchor, -1)
    assert c.zoom() == pytest.approx(TH.ZOOM_MIN, rel=1e-6)

    # ⑤ 百分比标签跟着走（FLOW_ZOOM_LABEL）
    c.set_zoom(1.0)
    assert H.find(v, N.FLOW_ZOOM_LABEL).text() == T.FLOW_ZOOM_FMT.format(pct=100)

    # ⑥ 双击复位：100% + 回到左上角
    c.set_zoom(2.0)
    c.horizontalScrollBar().setValue(c.horizontalScrollBar().maximum())
    QTest.mouseDClick(c.viewport(), Qt.LeftButton, Qt.NoModifier, QtCore.QPoint(200, 150))
    H.app().processEvents()
    assert c.zoom() == pytest.approx(1.0)
    assert c.horizontalScrollBar().value() == c.horizontalScrollBar().minimum()
    H.shot(v, "flow_mux3_zoom100")
    v.close()


def test_c281_wheel_without_ctrl_scrolls_instead_of_zooming():
    """不按 Ctrl 的滚轮是**滚动**不是缩放（图比窗口高，滚动才是第一位的需求）。"""
    v, _bus, _an = make_view(MUX3)
    c = v.canvas
    c.set_zoom(1.0)
    z0, y0 = c.zoom(), c.verticalScrollBar().value()
    wheel(c, QtCore.QPoint(300, 200), -1, modifier=Qt.NoModifier)
    assert c.zoom() == pytest.approx(z0), "没按 Ctrl 不许缩放"
    assert c.verticalScrollBar().value() != y0 or c.verticalScrollBar().maximum() == 0
    v.close()


@pytest.mark.parametrize("button", [Qt.RightButton, Qt.MiddleButton])
def test_c281_pan_with_right_and_middle_button(button):
    """右键 / 中键按住拖 → 平移；松开后不再跟手。"""
    v, _bus, _an = make_view(MUX3)
    c = v.canvas
    c.set_zoom(2.0)                      # 两向都要有滚动余量，否则「平移了没有」验不出来
    hbar, vbar = c.horizontalScrollBar(), c.verticalScrollBar()
    for bar in (hbar, vbar):
        assert bar.maximum() > bar.minimum()
        bar.setValue((bar.minimum() + bar.maximum()) // 2)
    h0, v0 = hbar.value(), vbar.value()

    QTest.mousePress(c.viewport(), button, Qt.NoModifier, QtCore.QPoint(400, 240))
    assert c._panning is True
    QTest.mouseMove(c.viewport(), QtCore.QPoint(330, 190))
    H.app().processEvents()
    assert hbar.value() == h0 + 70 and vbar.value() == v0 + 50
    QTest.mouseRelease(c.viewport(), button, Qt.NoModifier, QtCore.QPoint(330, 190))
    assert c._panning is False

    h1, v1 = hbar.value(), vbar.value()
    QTest.mouseMove(c.viewport(), QtCore.QPoint(200, 100))
    H.app().processEvents()
    assert (hbar.value(), vbar.value()) == (h1, v1), "松开后不该继续跟手"
    v.close()


def test_c281_right_button_does_not_open_context_menu():
    """右键是平移手柄 → contextMenu 必须被吃掉（Design ⑦ `onContextMenu` 屏蔽）。"""
    v, _bus, _an = make_view(MUX3)
    vp = v.canvas.viewport()
    ev = QtGui.QContextMenuEvent(QtGui.QContextMenuEvent.Mouse, QtCore.QPoint(200, 150),
                                 vp.mapToGlobal(QtCore.QPoint(200, 150)))
    ev.ignore()
    QtWidgets.QApplication.sendEvent(vp, ev)
    assert ev.isAccepted(), "右键菜单没被屏蔽，一拖就弹菜单"
    v.close()


def test_c281_fit_is_width_based_with_vertical_scroll():
    """「适应窗口」= 按宽度 fit + 纵向滚（C0-c 备注 ①：整图 fit 会把 3400px 压成灰条）。"""
    v, _bus, _an = make_view(MUX3)
    c = v.canvas
    c.set_zoom(1.0)
    H.click(H.find(v, N.FLOW_BTN_FIT))
    H.app().processEvents()
    want = min(1.0, float(c.viewport().width() - 2) / float(c.layout.size[0]))
    assert c.zoom() == pytest.approx(want, rel=1e-6)
    assert c.horizontalScrollBar().maximum() == 0, "按宽度 fit 之后不该还要横向滚"
    assert H.find(v, N.FLOW_ZOOM_LABEL).text() == T.FLOW_ZOOM_FMT.format(pct=int(round(want * 100)))

    H.click(H.find(v, N.FLOW_BTN_100))
    H.app().processEvents()
    assert c.zoom() == pytest.approx(1.0)
    v.close()


# ═══════════════════════════ C-282 点一根线 → 总线 ═══════════════════════════
def test_c282_click_edge_selects_net_on_bus_and_rerenders_highlight():
    """左键真点一条线 → `bus.select(net, "flow")`，画面带 highlight_net 重渲染出 data-hl。"""
    v, bus, an = make_view(MUX3)
    c = v.canvas
    seen = []
    bus.netSelected.connect(lambda net, org: seen.append((net, org)))

    edge = next(e for e in an["graph"].edges if e.net)
    item = c.hit_for_net(edge.net)
    assert isinstance(item, FV.EdgeHit)
    before = c.svg_text
    QTest.mouseClick(c.viewport(), Qt.LeftButton, Qt.NoModifier, point_on(c, item))
    H.app().processEvents()

    assert seen == [(BUS.net_key(edge.net), "flow")]
    assert bus.current_net == BUS.net_key(edge.net)
    assert c.svg_text != before, "选中变化后必须重渲染（路线 C：画面永远是 render_svg 出的）"
    hl = [net for net, _pts, is_hl in svg_polylines(c.svg_text) if is_hl]
    assert hl and all(x.lower() == edge.net.lower() for x in hl)
    H.shot(v, "flow_mux3_highlight")
    v.close()


def test_c282_bus_select_from_outside_rerenders_and_does_not_write_back():
    """真值表 / 展开链选中（origin≠flow）→ 电路图只重渲染样式，**不回写选择**（I-18）。"""
    v, bus, an = make_view(MUX3)
    c = v.canvas
    edge = next(e for e in an["graph"].edges if e.net)
    assert 'data-hl' not in c.svg_text

    bus.select(edge.net, "truth")
    H.app().processEvents()
    assert bus.current_origin == "truth", "电路图不许把 origin 抢成 flow（会来回递归）"
    assert c.current_net == BUS.net_key(edge.net)
    hl_nets = {net.lower() for net, _p, is_hl in svg_polylines(c.svg_text) if is_hl}
    assert hl_nets == {edge.net.lower()}

    # 命中层不随重渲染重建（坐标没变）：点选仍然指向同一条线
    assert c.hit_for_net(edge.net) is not None

    bus.clear("truth")
    H.app().processEvents()
    assert 'data-hl' not in c.svg_text
    v.close()


def test_c282_bit_sliced_net_still_highlights_after_bus_strips_width():
    """位切片线（`d_wl_rf_freq_sel[1]`）：点它 → **同一根网的各位一起亮**。

    C2-int 之前 `sigflow._hl_key` 只做小写、`bus.net_key` 剥位宽，两把钥匙对不上：
    把总线的键递进 `render_svg` 时这类线一条都不亮（mirror wl 的 tx_epa 里有 3 条），
    GUI 侧只好自己猜「图上那根线的原写法」。现在 `_hl_key` 也走 `excel_model._strip_width`，
    钥匙只有一把 —— 「当前线网」本来就是一根**网**（不分第几位），各位切片一起亮才是对的。"""
    v, bus, an = make_view(SLICED)
    c = v.canvas
    edge = next(e for e in an["graph"].edges if e.net and "[" in e.net)
    key = BUS.net_key(edge.net)
    assert key != edge.net.lower(), "选的这条线没有位切片，验不到东西"
    same = sorted({str(e.net) for e in an["graph"].edges if e.net and BUS.net_key(e.net) == key})
    assert len(same) > 1, "这根网在图上只有一条线，验不到「各位一起亮」"

    item = c.hit_for_net(edge.net)
    QTest.mouseClick(c.viewport(), Qt.LeftButton, Qt.NoModifier, point_on(c, item))
    H.app().processEvents()

    assert bus.current_net == key
    assert c.current_net == key
    hl = sorted({net for net, _p, is_hl in svg_polylines(c.svg_text) if is_hl})
    assert hl == same, "同一根网的各位没有一起亮：%r != %r" % (hl, same)

    # 外部（真值表行 = 剥了位宽的整根网）选中：结果必须一模一样（同一把钥匙，没有「第几位」之分）
    bus.clear("truth")
    bus.select(key, "truth")
    H.app().processEvents()
    hl2 = sorted({net for net, _p, is_hl in svg_polylines(c.svg_text) if is_hl})
    assert hl2 == same, "外部选整根网与在图上点一位，亮的不是同一批：%r" % hl2
    # 别的网一条都没被误伤
    assert all(BUS.net_key(x) == key for x in hl2)
    v.close()


def test_c282_click_blank_canvas_clears_highlight():
    """点空白处 = 清除当前线网（`bus.clear`）。"""
    v, bus, an = make_view(MUX3)
    c = v.canvas
    edge = next(e for e in an["graph"].edges if e.net)
    bus.select(edge.net, "truth")
    H.app().processEvents()

    blank = c.mapFromScene(QtCore.QPointF(c.layout.size[0] - 6, c.layout.size[1] - 6))
    assert c.hit_at(c.mapToScene(blank)) is None
    QTest.mouseClick(c.viewport(), Qt.LeftButton, Qt.NoModifier, blank)
    H.app().processEvents()
    assert bus.current_net == ""
    assert 'data-hl' not in c.svg_text
    v.close()


# ═══════════════════════════ C-283 hover 寄存器盒 ═══════════════════════════
def test_c283_hover_register_tooltip_has_address_bits_reason():
    """真 hover 一个寄存器盒 → tooltip 里有地址、位段、判成 RO/RW 的依据。"""
    v, _bus, an = make_view(MUX3)
    c = v.canvas
    reg = next(n for n in an["graph"].nodes if n.kind == "REG" and n.meta.get("address") is not None)
    item = c.hit_for_node(reg.id)
    tip = tooltip_at(c, point_on(c, item))

    assert tip, "hover 寄存器盒没出 tooltip"
    assert reg.label in tip
    assert ("@0x%x" % reg.meta["address"]) in tip, "缺地址"
    assert ("[%d:%d]" % (reg.meta["reg_msb"], reg.meta["reg_lsb"])) in tip, "缺位段"
    assert str(reg.meta["reg_kind"]) in tip, "缺 RO/RW"
    assert T.FLOW_WHY_REG in tip, "缺「为什么判成 RW」的依据"
    v.close()


def test_c283_untrusted_pin_tooltip_says_why_force_only():
    """猜名的 RO 端子（mirror 真节点）：tooltip 说清名字是猜的 + 为什么只能 force。"""
    found = None
    for kind in ("wl", "btlp"):
        wb = excel_model.load_workbook(H.mirror_path(kind))
        for topo in (wb.topout or []):
            g = (analysis(kind, topo.name) or {}).get("graph")
            for n in (g.nodes if g is not None else []):
                if n.meta.get("trusted") is False and (n.meta.get("note") or ""):
                    found = n
                    break
            if found:
                break
        if found:
            break
    assert found is not None, "mirror 里没有带说明的猜名端子，这条断言就验不到东西了"
    tip = FV.node_tooltip(found)
    assert sigflow.UNTRUSTED_TIP in tip or sigflow.UNKNOWN_TIP in tip, "没说名字是猜的"
    assert "force" in tip, "没说这根只能 force"
    assert T.scrub(found.meta["note"]) in tip, "没把引擎判 RO 的理由带出来"
    leaked = [w for w in T.FORBIDDEN if w in tip]
    assert not leaked, "tooltip 漏了内部术语 %s：%r" % (leaked, tip)


def test_c283_tooltip_scrubs_backend_terms():
    """I-12：tooltip 的每一段（sub / note / 猜名提示）都必须过 `terms.scrub`。

    mirror 现有的绑定说明里恰好不含内部术语 —— 那就直接拿一个带术语的 `Node` 走同一条路，
    免得这条不变量靠「夹具里碰巧没有」蒙混过关。"""
    node = sigflow.Node("n1", "PIN", "some_net", sub="RO force some_net",
                        meta={"trusted": False, "found_in": "wire", "base": "some_net",
                              "tip": sigflow.UNTRUSTED_TIP,
                              "note": "该字段在 regmap 里没查到，由 wire 兜底(force 信号名)；"
                                      "无 cone 可展，跳过 + 记账"})
    tip = FV.node_tooltip(node)
    leaked = [w for w in T.FORBIDDEN if w in tip]
    assert not leaked, "tooltip 漏了内部术语 %s：%r" % (leaked, tip)
    assert "按命名约定当线网处理" in tip and "没有可展开的上游" in tip


def test_c283_bubble_node_hit_box_covers_the_bubble():
    """C0-c 备注 ⑥：反相气泡画在包围盒**外**（dft 门 `y0-5.5`），命中盒必须把它圈进来。"""
    v, _bus, an = make_view(GATES)
    c = v.canvas
    node = next(n for n in an["graph"].nodes if n.meta.get("bubble"))
    item = c.hit_for_node(node.id)
    x, y, w, h = c.layout.pos[node.id]
    assert item.rect().contains(QtCore.QRectF(x, y, w, h)), "命中盒比原包围盒还小"
    assert item.rect().top() <= y - 10.5, "dft 门输入气泡（圆心 y0-5.5、r=5）没被圈进来"
    assert c.hit_at(QtCore.QPointF(x + w / 2.0, y - 5.5)) is not None
    H.shot(v, "flow_gates_bubble")
    v.close()


# ═══════════════════════════ C-284 导出 SVG / PNG ═══════════════════════════
def test_c284_export_svg_png_files_written(monkeypatch, tmp_path):
    """工具条两个导出键真落盘：SVG 是文本且带 data-net，PNG 能被 QImage 读回 2× 尺寸。"""
    def _save(call, *a, **k):
        flt = str(k.get("filter") or (a[3] if len(a) > 3 else ""))
        ext = ".png" if "png" in flt.lower() else ".svg"
        return (str(tmp_path / ("flow" + ext)), "")

    rec = H.auto_dialogs(monkeypatch, answers={"getSaveFileName": _save})
    v, _bus, an = make_view(MUX3)
    written = []
    v.exported.connect(written.append)

    H.click(H.find(v, N.FLOW_BTN_EXPORT_SVG))
    H.app().processEvents()
    svg_path = tmp_path / "flow.svg"
    assert svg_path.exists() and svg_path.stat().st_size > 0
    text = svg_path.read_text(encoding="utf-8")
    assert text.startswith("<svg") and 'data-net="' in text
    assert 'data-hl' not in text, "导出的交付物不该记录「我当时点中了哪根线」"

    H.click(H.find(v, N.FLOW_BTN_EXPORT_PNG))
    H.app().processEvents()
    png_path = tmp_path / "flow.png"
    assert png_path.exists() and png_path.stat().st_size > 0
    img = QtGui.QImage(str(png_path))
    assert not img.isNull()
    assert img.width() == pytest.approx(v.canvas.layout.size[0] * 2, abs=2)
    assert img.height() == pytest.approx(v.canvas.layout.size[1] * 2, abs=2)

    assert written == [str(svg_path), str(png_path)]
    assert rec.count("getSaveFileName") == 2
    v.close()


def test_c284_export_cancelled_writes_nothing(monkeypatch, tmp_path):
    """取消保存对话框 → 一个字节都不写（不留半拉文件）。"""
    H.auto_dialogs(monkeypatch, answers={"getSaveFileName": ("", "")})
    v, _bus, _an = make_view(MUX3)
    assert v.export_svg() == "" and v.export_png() == ""
    assert not list(tmp_path.iterdir())
    v.close()


# ═══════════════════════ 命中层 ↔ SVG 坐标一致性（路线 C 的命门）═══════════════════════
@pytest.mark.parametrize("sig", [MUX3, GATES, DIRECT], ids=["mux3", "gates", "direct"])
def test_flow_hit_layer_matches_svg_data_box(sig):
    """每个 `<g data-box>` 与 `Layout.pos`、与 NodeHit 的原始盒逐个对上 —— 漂一像素点选就点空。"""
    v, _bus, an = make_view(sig)
    c = v.canvas
    boxes = svg_boxes(c.svg_text)
    assert set(boxes) == set(c.layout.pos) == {n.id for n in an["graph"].nodes}
    for nid, box in boxes.items():
        assert tuple(int(x) for x in c.layout.pos[nid]) == box, "SVG 与 Layout 漂了：%s" % nid
        item = c.hit_for_node(nid)
        assert item is not None and item.data(FV.ROLE_BOX) == box
        # 命中盒只准往外扩（气泡余量），绝不准比 data-box 小
        assert item.rect().contains(QtCore.QRectF(*[float(x) for x in box]))
        # data-net 也必须与命中体一致，否则点盒子和点它右边那根线会选中两个网
        m = re.search(r'data-node="%s"[^>]*data-net="([^"]*)"' % re.escape(nid), c.svg_text)
        assert m and m.group(1) == item.data(FV.ROLE_NET)
    v.close()


@pytest.mark.parametrize("sig", [MUX3, GATES], ids=["mux3", "gates"])
def test_flow_hit_layer_matches_svg_polylines(sig):
    """每条 `<polyline points>` 与 EdgeHit 的路径点列逐点对上（命中层不认 SVG，但必须同源）。"""
    v, _bus, _an = make_view(sig)
    c = v.canvas
    lines = svg_polylines(c.svg_text)
    edges = [i for i in c.hits() if isinstance(i, FV.EdgeHit)]
    assert len(lines) == len(edges) == len(c.layout.edges)
    for (net, pts, _hl), item, (edge, lay_pts, _side) in zip(lines, edges, c.layout.edges):
        assert net == (edge.net or "") == item.data(FV.ROLE_NET)
        path = item.path()
        assert path.elementCount() == len(lay_pts) == len(pts)
        for i, (x, y) in enumerate(pts):
            el = path.elementAt(i)
            # SVG 的点列是 %.0f 印出来的，命中层用的是同一份 float —— 只准差在这一次取整上
            assert abs(el.x - x) <= 0.5 and abs(el.y - y) <= 0.5
    v.close()


def test_flow_edge_hit_pen_is_transparent_and_wide():
    """命中层是**透明**的（画面只有 SVG 一份）且笔宽 8（1.2px 的线按真宽命中等于要求像素级瞄准）。"""
    v, _bus, _an = make_view(MUX3)
    for item in v.canvas.hits():
        if isinstance(item, FV.EdgeHit):
            assert item.pen().widthF() == FV.HIT_PEN_W
            assert item.pen().color().alpha() == 0
            assert item.brush().style() == Qt.NoBrush
        else:
            assert item.pen().style() == Qt.NoPen and item.brush().style() == Qt.NoBrush
        assert item.acceptHoverEvents() is True
    v.close()


# ═══════════════════════════ 空态 / 文案 / objectName ═══════════════════════════
def test_flow_empty_states_name_the_reason():
    """三种空态：没选信号 / 后台还在展开 / 构图失败（失败要点名 issue 原文，且过 scrub）。"""
    v, _bus, _an = make_view(sig=None)
    empty = H.find(v, N.FLOW_EMPTY)
    assert v.body.currentWidget() is empty and empty.text() == T.FLOW_EMPTY
    for name in (N.FLOW_BTN_FIT, N.FLOW_BTN_100, N.FLOW_BTN_EXPORT_SVG, N.FLOW_BTN_EXPORT_PNG):
        assert H.find(v, name).isEnabled() is False, "没图时 %s 不该可点" % name

    v.set_pending()
    assert empty.text() == T.FLOW_PENDING      # 专门文案（C2-int 前借的是 T.HDR_PENDING）

    # 只读回读：引擎压根没给图 → 通用文案
    v.set_signal(analysis(*NOGRAPH), name=NOGRAPH[1])
    assert analysis(*NOGRAPH)["graph"] is None
    assert empty.text() == T.FLOW_EMPTY

    # 构图失败：把引擎那条 ⚠ 原样点名（C-270），内部术语先过 scrub（I-12）
    v.set_signal({"graph": None,
                  "issues": ["⚠ 信号流图构建失败(不影响验证与产物): ValueError('无 cone')"]})
    assert empty.text().startswith(T.FLOW_BUILD_FAILED)      # 抬头先说清「图没画出来」
    assert "信号流图构建失败" in empty.text()
    assert not [w for w in T.FORBIDDEN if w in empty.text()], empty.text()
    H.shot(v, "flow_empty_build_failed")
    v.close()


def test_flow_objectnames_and_terms_come_from_the_registries():
    """I-14：每个可测控件都有 objectName，且名字只来自 `names.py`（C2-int 起没有 PENDING 表了）。"""
    v, _bus, _an = make_view(MUX3)
    for key, value in N.all_names().items():
        if key.startswith("FLOW_"):
            assert H.find(v, value) is not None, "names.%s 没落到控件上" % key
    for i in range(len(T.FLOW_LEGEND)):                     # 四条图例各自可测（fmt_flow_legend_item）
        assert H.find(v, N.fmt_flow_legend_item(i)) is not None
    assert v.canvas.viewport().objectName() == N.FLOW_VIEWPORT

    # 文案一律来自 terms
    assert H.find(v, N.FLOW_TITLE).text() == T.FLOW_TITLE
    assert H.find(v, N.FLOW_SUBTITLE).text() == T.FLOW_SUBTITLE
    assert H.find(v, N.FLOW_FOOTER).text() == T.FLOW_FOOTER
    assert H.find(v, N.FLOW_BTN_FIT).text() == T.FLOW_BTN_FIT
    assert H.find(v, N.FLOW_BTN_100).text() == T.FLOW_BTN_100
    assert H.find(v, N.FLOW_BTN_EXPORT_SVG).text() == T.FLOW_BTN_EXPORT_SVG
    assert H.find(v, N.FLOW_BTN_EXPORT_PNG).text() == T.FLOW_BTN_EXPORT_PNG
    legend = H.find(v, N.FLOW_LEGEND)
    assert [lb.text() for lb in legend.items] == list(T.FLOW_LEGEND)
    v.close()


def test_flow_no_internal_terms_on_screen():
    """I-12 起窗版：面板上所有 QLabel 与命中层 tooltip 里都不许裸出现内部术语。"""
    v, _bus, _an = make_view(MUX3)
    texts = [lb.text() for lb in v.findChildren(QtWidgets.QLabel)]
    texts += [lb.toolTip() for lb in v.findChildren(QtWidgets.QWidget)]
    texts += [it.toolTip() for it in v.canvas.hits()]
    for s in texts:
        leaked = [w for w in T.FORBIDDEN if w in (s or "")]
        assert not leaked, "界面上漏了内部术语 %s：%r" % (leaked, s)
    v.close()


def test_flow_fullscreen_button_toggles_and_emits():
    """全屏按钮 → `fullscreenToggled(bool)`（main_view 接它；本模块不认识 main_view）。"""
    v, _bus, _an = make_view(MUX3)
    got = []
    v.fullscreenToggled.connect(got.append)
    btn = H.find(v, N.FLOW_BTN_FULLSCREEN)
    assert btn.isCheckable() and btn.text() == T.FLOW_BTN_FULLSCREEN

    H.click(btn)
    H.app().processEvents()
    assert got == [True] and btn.text() == T.FLOW_BTN_EXIT_FULLSCREEN
    H.click(btn)
    H.app().processEvents()
    assert got == [True, False] and btn.text() == T.FLOW_BTN_FULLSCREEN

    v.set_fullscreen(True)          # 外部改状态：同步按钮，不重复 emit 出第二条 True
    assert btn.isChecked() and got == [True, False, True]
    v.set_fullscreen(True)
    assert got == [True, False, True]
    v.close()


def test_flow_switching_signal_rebuilds_scene():
    """换信号：旧图的命中层必须整个拆掉（残留一层就会点到上一个信号的网）。"""
    v, bus, _an = make_view(MUX3)
    c = v.canvas
    n_mux = len(c.hits())
    v.set_signal(analysis(*DIRECT), name=DIRECT[1])
    H.app().processEvents()
    assert len(c.hits()) == 3 != n_mux          # 2 节点 + 1 条线
    assert all(it.scene() is c._scene for it in c.hits())

    v.clear()
    assert c.graph is None and c.hits() == []
    assert bus.current_net == "", "清空画布不该顺手改总线上的当前线网"
    v.close()
