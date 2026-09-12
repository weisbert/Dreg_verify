# -*- coding: utf-8 -*-
"""sigflow_view.py —— ⑦ 电路图区（GUI v2 Phase C2-b）。

架构 §1.3② **路线 C**（唯一写法）的落地 —— 画面与命中层分家：

    画面（picture）  `sigflow.render_svg(graph, layout=…, highlight_net=…)` → `QSvgRenderer`
                     → 一个 `QGraphicsSvgItem`。报告 HTML 内联的是同一份 `render_svg`，
                     两边绝不会漂成两张图。
    命中层（hit）    Qt-free 的 `sigflow.layout_graph(graph) -> Layout` 给坐标，
                     每条边一个**透明** `QGraphicsPathItem`（笔宽 `HIT_PEN_W`，`shape()` 精确）、
                     每个节点一个**透明** `QGraphicsRectItem`。点选 / hover tooltip 全走它。

为什么不认 SVG 元素：`QSvgRenderer.boundsOnElement(id)` 只给包围盒且要求元素有 id，折线的包围盒
能盖住半张图 —— 点一条斜着绕过去的长线会命中一片别的网。命中层按 `Layout` 的坐标铺，
与 SVG 里每个 `<g>` 上挂的 `data-box="x,y,w,h"` 同源（都出自 `layout_graph`），
`tests/test_ui_sigflow_view.py::test_flow_hit_layer_matches_svg_data_box` 逐个节点对过。

本模块只做「拿 an → 画 → 把用户动作转成 bus 调用」：
  · 用户可见文案取自 `terms`，色值 / 缩放规格取自 `theme`，objectName 取自 `names`；
  · 一切后端文本（`an["issues"]` / `Node.meta` 的 note / tip）先过 `terms.scrub`（不变量 I-12）；
  · 「当前线网」只经 `bus`，不直接 import 其它视图（不变量 I-18 / C-282）。

契约 ID（附录 A「FLOW」5 条）：C-278 C-281 C-282 C-283 C-284。
（C-279 / C-286 / C-287 归 C0-c 的 `sigflow.py`，不在本模块。）

对 `ui/state.py` / `ui/main_view.py` 的接口假设见模块末尾 `HOST_REQUIREMENTS`（C2-int 逐条核对）。
"""

import os
import re

from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtSvgWidgets import QGraphicsSvgItem

from dreg_verify import sigflow

from . import names as N
from . import terms as T
from . import theme as TH
from .bus import net_key
from .widgets import FlowLayout, ui_font

Qt = QtCore.Qt

__all__ = ["FlowCanvas", "SigflowView", "SigFlowPanel", "EdgeHit", "NodeHit",
           "HIT_PEN_W", "BUBBLE_PAD", "node_net", "node_tooltip",
           "edge_tooltip", "HOST_REQUIREMENTS"]

#: 第 i 条图例的 objectName（顺序 = `terms.FLOW_LEGEND`）——定义在 `ui/names.py`
fmt_legend_item = N.fmt_flow_legend_item


#: 命中层的笔宽（架构 §1.3②「透明 QGraphicsPathItem，笔宽 8」）。线只有 1.2–2.4px 粗，
#: 按真实笔宽做命中等于要求用户像素级瞄准；8px 是「手感对得上、又不会盖住隔壁那根线」的值。
HIT_PEN_W = 8.0

#: 节点命中盒往外扩的余量（C0-c 备注 ⑥）：dft 门的输入反相气泡圆心画在 `y0-5.5`、半径 5 —— 在
#: 包围盒**外**；输出气泡同理画在 `x0+w+5`。不扩这一圈，hover 气泡就落在盒外点不着。
BUBBLE_PAD = 11.0

#: 构图失败的 issue（`topout._attach_graph` 的既有口径：`⚠ 信号流图构建失败(...)`）。
#: 引擎没给结构化 meta，只能按这条文本认；认不出来就退回 `terms.FLOW_EMPTY`（不瞎猜）。
_GRAPH_FAIL_RE = re.compile(r"图.*(失败|构建不出)")

#: 命中层 item 上挂的数据（`QGraphicsItem.data(role)`）
ROLE_NET = 0        # 这条线 / 这个盒代表的网名（原文，比对时再规整）
ROLE_NODE = 1       # 节点 id（"n7"）；边为 None
ROLE_BOX = 2        # 节点在 Layout 里的原始 (x, y, w, h)——与 SVG 的 data-box 同一份数字
ROLE_KIND = 3       # 节点 kind（REG / TOPOUT / MUXN …）；边为 None

#: 图例四条的颜色（顺序与 `terms.FLOW_LEGEND` / `sigflow._LEGEND` 一致）
_LEGEND_COLORS = (TH.FLOW_MUTE, TH.FLOW_AMB, TH.FLOW_HL, TH.FLOW_BAD)

#: 「为什么判成 RW」的那句（C-283）。C2-int 之前借的是图例第一条（去掉线型前缀）；
#: 图例说的是「这条线是什么」、tooltip 说的是「为什么这么判」，两句话各改各的，现在各有各的常量。
_WHY_REG = T.FLOW_WHY_REG


# ═════════════════════════════ 文本 ═════════════════════════════
def node_net(node):
    """节点代表的网名 —— **必须与 `render_svg` 写进 `<g data-net>` 的那一份逐字一致**，
    否则「点盒子」和「点它右边那根线」会选中两个不同的网。"""
    return str(node.meta.get("base") or node.meta.get("out_base") or node.label or "")


def node_tooltip(node):
    """hover 寄存器盒的 tooltip（C-283：地址 / 位段 / 为什么判成 RO 或 RW）。

    三行全部来自 `Node.meta` 与 `binding.note`（引擎的结论），过 `terms.scrub` 后才上屏（I-12）：
      第 1 行 主标 = 网名（带位段切片）
      第 2 行 `sigflow._sub_text(node)` = `RW @0x1a[3:0]` / `RO force <网名>`（+ 猜名提示）
      第 3 行 判成 RW 的依据 = 表里查到地址（REG 且来源可信时；猜名的已并进第 2 行）
      第 4 行 `binding.note` = 引擎判 RO 的理由（为什么只能 force、要不要探针前缀）
    """
    lines = [str(node.label or "")]
    sub = sigflow._sub_text(node)
    if sub:
        lines.append(sub)
    if node.kind == "REG" and node.meta.get("trusted") is not False:
        lines.append(_WHY_REG)
    note = str(node.meta.get("note") or "")
    if note and note not in sub:
        lines.append(note)
    return T.scrub("\n".join(x for x in lines if x))


def edge_tooltip(edge):
    """hover 一根线：网名 + 位宽（宽度 1 不标，与 `render_svg` 的标注口径一致）。"""
    net = str(edge.net or "")
    if not net:
        return ""
    if (edge.width or 1) > 1 and not net.endswith("]"):
        net += "[%d:0]" % (edge.width - 1)
    if not edge.trusted:
        net += " · " + sigflow.UNTRUSTED_TIP
    return T.scrub(net)


# ═════════════════════════════ 命中层 item ═════════════════════════════
class EdgeHit(QtWidgets.QGraphicsPathItem):
    """一条线的命中体：透明、笔宽 `HIT_PEN_W`，`shape()` 由 `QPainterPathStroker` 精确给出
    （折线绕过去的那一段不会把包围盒里的别的网一起吃掉）。"""

    def __init__(self, path, net, tip):
        QtWidgets.QGraphicsPathItem.__init__(self, path)
        pen = QtGui.QPen(QtGui.QColor(0, 0, 0, 0), HIT_PEN_W)
        pen.setJoinStyle(Qt.RoundJoin)
        pen.setCapStyle(Qt.RoundCap)
        self.setPen(pen)
        self.setBrush(QtGui.QBrush(Qt.NoBrush))
        self._shape = self._stroke(path)
        self.setAcceptHoverEvents(True)
        self.setData(ROLE_NET, net)
        self.setData(ROLE_NODE, None)
        self.setData(ROLE_BOX, None)
        self.setData(ROLE_KIND, None)
        if tip:
            self.setToolTip(tip)
        if net:
            self.setCursor(Qt.PointingHandCursor)

    @staticmethod
    def _stroke(path):
        """折线 → 命中区域。**必须 `simplified()`**：折线拐弯处两段的描边会互相叠住，而
        `QPainterPath` 默认的 odd-even 填充规则会把叠住的那一小块判成「洞」——线上有几处
        拐角，点下去就有几处点不着（本地实测：拐角上方 1px 的带子恒 miss）。
        `simplified()` 把自交的描边并成一个不自交的区域，洞就没了。"""
        st = QtGui.QPainterPathStroker()
        st.setWidth(HIT_PEN_W)
        st.setJoinStyle(Qt.RoundJoin)
        st.setCapStyle(Qt.RoundCap)
        return st.createStroke(path).simplified()

    def shape(self):
        return self._shape


class NodeHit(QtWidgets.QGraphicsRectItem):
    """一个图元的命中体：透明矩形，几何 = `Layout.pos[nid]` 外扩 `BUBBLE_PAD`（备注 ⑥ 的气泡）。"""

    def __init__(self, rect, node, tip):
        QtWidgets.QGraphicsRectItem.__init__(self, rect)
        self.setPen(QtGui.QPen(Qt.NoPen))
        self.setBrush(QtGui.QBrush(Qt.NoBrush))
        self.setAcceptHoverEvents(True)
        self.setData(ROLE_NET, node_net(node))
        self.setData(ROLE_NODE, node.id)
        self.setData(ROLE_KIND, node.kind)
        if tip:
            self.setToolTip(tip)
        self.setCursor(Qt.PointingHandCursor)


# ═════════════════════════════ 画布 ═════════════════════════════
class FlowCanvas(QtWidgets.QGraphicsView):
    """`QGraphicsView` + 一个 `QGraphicsSvgItem`（画面）+ 一层透明命中体（点选 / hover）。

    交互规格（Design §5 第 22 条 / §1.2 ⑦，全部走 `theme` 的四个常量）：
      · Ctrl + 滚轮 → 以**光标**为锚缩放 `ZOOM_STEP`^±1，clamp `[ZOOM_MIN, ZOOM_MAX]`；
        不按 Ctrl 就是普通滚动（图比窗口高，滚动是第一位的需求）
      · 右键 / 中键按住拖 → 平移（右键的 contextMenu 一并屏蔽）
      · 双击 → 复位（100% + 回到左上角）
      · 左键点线 / 点盒 → `netClicked(net)`；点空白 → `netClicked("")`（= 清除高亮）
    """

    #: 左键命中的网名（""=点了空白）。上层把它接到 `bus.select(net, "flow")`。
    netClicked = QtCore.Signal(str)
    #: 当前缩放倍率变了（工具条的百分比标签接它）
    zoomChanged = QtCore.Signal(float)

    def __init__(self, parent=None):
        QtWidgets.QGraphicsView.__init__(self, parent)
        self.setObjectName(N.FLOW_VIEW)
        self.viewport().setObjectName(N.FLOW_VIEWPORT)
        self._scene = QtWidgets.QGraphicsScene(self)
        self.setScene(self._scene)
        self.setBackgroundBrush(QtGui.QBrush(QtGui.QColor(TH.WHITE)))
        self.setFrameShape(QtWidgets.QFrame.NoFrame)
        self.setRenderHints(QtGui.QPainter.Antialiasing |
                            QtGui.QPainter.TextAntialiasing |
                            QtGui.QPainter.SmoothPixmapTransform)
        self.setDragMode(QtWidgets.QGraphicsView.NoDrag)
        self.setTransformationAnchor(QtWidgets.QGraphicsView.NoAnchor)
        self.setResizeAnchor(QtWidgets.QGraphicsView.NoAnchor)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.setMouseTracking(True)

        self.graph = None
        self.layout = None
        self.svg_text = ""
        #: 当前高亮的比对键。C2-int 起它直接递给 `render_svg(highlight_net=)`：
        #: `sigflow._hl_key` 已改成与 `bus.net_key` 同一把钥匙（小写 + 剥位宽），
        #: 不必再在 GUI 侧猜「图上那根线的原写法」（旧的 `_graph_spelling` 已删）。
        self._key = ""
        self._renderer = QSvgRenderer(self)
        self._svg_item = None
        self._hits = []                     # 命中层 item（边在前、节点在后 = 节点压线）
        self._panning = False
        self._pan_from = None
        self._fit_pending = False           # 还没排过版就换图 → 等 show/resize 再 fit 一次

    # ── 数据 ──
    def set_graph(self, graph, highlight_net=""):
        """换一张图：重算 `Layout` → 渲染 SVG → 铺命中层 → 按宽度 fit。graph=None 清空。"""
        self._clear_scene()
        self.graph = graph
        self._key = net_key(highlight_net)
        if graph is None:
            self.layout = None
            self.svg_text = ""
            self._scene.setSceneRect(QtCore.QRectF(0, 0, 1, 1))
            return
        self.layout = sigflow.layout_graph(graph)
        self._scene.setSceneRect(QtCore.QRectF(0, 0, float(self.layout.size[0]),
                                               float(self.layout.size[1])))
        self._svg_item = QGraphicsSvgItem()
        # DeviceCoordinateCache（QGraphicsSvgItem 的默认值）会把第一帧缓存下来，
        # 之后 `renderer.load()` 换了内容也不重画 —— 高亮就永远不刷新。
        self._svg_item.setCacheMode(QtWidgets.QGraphicsItem.NoCache)
        self._svg_item.setZValue(0)
        self._render()
        self._svg_item.setSharedRenderer(self._renderer)
        self._scene.addItem(self._svg_item)
        self._build_hit_layer()
        self._fit_pending = True
        self.zoom_fit()

    def highlight(self, net, origin="flow"):
        """当前线网变了 → 带新的 `highlight_net` **重渲染整张 SVG** 再 `renderer.load()`。

        ≤60 节点 ≈ 2ms（架构 §1.3② 实测口径），换来的是「GUI 与报告 HTML 是同一份画法」——
        另写一套 Qt 描边样式必然与 `render_svg` 漂开。命中层不动（坐标没变）。

        `origin` 是谁发的在这里不影响画面：「当前线网」就是一根网（不分第几位），
        同一根网的各位切片一起亮 —— `sigflow._hl_key` 与 `bus.net_key` 同一把钥匙后自然如此。"""
        key = net_key(net)
        if key == self._key:
            return False
        self._key = key
        if self.graph is None:
            return False
        self._render()
        if self._svg_item is not None:
            self._svg_item.setSharedRenderer(self._renderer)   # 重新读一次 defaultSize
            self._svg_item.update()
        self.viewport().update()
        return True

    @property
    def current_net(self):
        """当前高亮的线网 —— 与总线同一把钥匙（剥位宽 + 小写）。"""
        return self._key

    def _render(self):
        """画面 = `render_svg`。**图例不内嵌**（`legend=False`）：GUI 的图例是钉在画布下方的
        `FlowLegend` 控件，内嵌那份会跟着缩放平移滚出视野（C0-c 备注 ⑤），同时留着就是两份。
        报告 HTML / ppt 那边不传 `legend`，字节照旧。"""
        self.svg_text = sigflow.render_svg(self.graph, layout=self.layout,
                                           highlight_net=self._key or None, legend=False)
        self._renderer.load(QtCore.QByteArray(self.svg_text.encode("utf-8")))

    def _clear_scene(self):
        self._scene.clear()          # QGraphicsScene.clear 会析构 item，list 必须同步清空
        self._svg_item = None
        self._hits = []

    def _build_hit_layer(self):
        """按 `Layout` 铺透明命中体。**只认 Layout 坐标，不认 SVG 里的 `<rect>`**
        （C0-c 备注：梯形 / 箭头 / 门体的 `<path>` 根本没有 `<rect>` 可认）。"""
        lay, g = self.layout, self.graph
        for edge, pts, _side in lay.edges:
            path = QtGui.QPainterPath(QtCore.QPointF(float(pts[0][0]), float(pts[0][1])))
            for x, y in pts[1:]:
                path.lineTo(float(x), float(y))
            it = EdgeHit(path, str(edge.net or ""), edge_tooltip(edge))
            it.setZValue(10)
            self._scene.addItem(it)
            self._hits.append(it)
        for n in g.nodes:
            box = lay.pos.get(n.id)
            if box is None:
                continue
            x, y, w, h = box
            rect = QtCore.QRectF(float(x), float(y), float(w), float(h))
            if n.meta.get("bubble"):      # 备注 ⑥：反相气泡画在包围盒外，命中要算进去
                rect = rect.adjusted(0.0, -BUBBLE_PAD, BUBBLE_PAD, 0.0)
            it = NodeHit(rect, n, node_tooltip(n))
            it.setData(ROLE_BOX, (int(x), int(y), int(w), int(h)))
            it.setZValue(20)              # 盒压线：盒上点下去要选盒，不是从它底下穿过的那根线
            self._scene.addItem(it)
            self._hits.append(it)

    # ── 查询（测试与 hover 用；与 Qt 自己派事件时的命中口径同一份）──
    def hits(self):
        return list(self._hits)

    def hit_at(self, scene_pos):
        """场景坐标上最靠前的命中体（没有 → None）。"""
        pt = scene_pos if isinstance(scene_pos, QtCore.QPointF) else QtCore.QPointF(*scene_pos)
        best = None
        for it in self._scene.items(pt):
            if isinstance(it, (EdgeHit, NodeHit)):
                if best is None or it.zValue() > best.zValue():
                    best = it
        return best

    def hit_for_net(self, net):
        """第一条代表该网的命中体（测试里拿它算点击位置）。"""
        key = str(net or "").strip().lower()
        for it in self._hits:
            if str(it.data(ROLE_NET) or "").strip().lower() == key:
                return it
        return None

    def hit_for_node(self, nid):
        for it in self._hits:
            if it.data(ROLE_NODE) == nid:
                return it
        return None

    # ── 缩放 / 平移 ──
    def zoom(self):
        return float(self.transform().m11())

    def set_zoom(self, factor):
        f = max(TH.ZOOM_MIN, min(TH.ZOOM_MAX, float(factor)))
        self.setTransform(QtGui.QTransform.fromScale(f, f))
        self.zoomChanged.emit(f)
        return f

    def zoom_fit(self):
        """「适应窗口」= **按宽度 fit + 纵向滚**（C0-c 备注 ①）。

        真实图的长宽比失控（tx_epa 3407×502、lna_lctune 2506×1314）：整图 fitInView 会把
        3407px 压到 ~0.2×，字全糊成灰条。按宽度装进来、高度交给纵向滚动条，才读得出网名。
        本来就装得下的小图（clk_force_on 560×154）**不放大**，停在 100%。

        （`theme.ZOOM_FIT=0.82` 是 Design 画板上那个写死的「适应窗口」倍率 —— 它假设图宽
        与画板同量级；真实图宽 1300–3400px，乘 0.82 仍然出框，所以这里按宽度算，不用它。）"""
        if self.layout is None or not self.layout.size[0]:
            self._fit_pending = False
            return self.set_zoom(1.0)
        avail = self.viewport().width() - 2
        if not self.isVisible() or avail < 8:
            # 还没排版（没 show / QStackedWidget 还停在空态页）—— 这时 viewport 只有默认 100px 宽，
            # 照着它算 fit 会得出 0.04 → 直接被 clamp 到 30%。先停在 100%，等 show/resize 再 fit 一次。
            self._fit_pending = True
            return self.set_zoom(1.0)
        self._fit_pending = False
        f = min(1.0, float(avail) / float(self.layout.size[0]))
        f = self.set_zoom(f)
        self.horizontalScrollBar().setValue(self.horizontalScrollBar().minimum())
        self.verticalScrollBar().setValue(self.verticalScrollBar().minimum())
        return f

    def zoom_100(self):
        """100%：保持视野中心不动（只换倍率，别把人甩到图的另一头）。"""
        center = self.mapToScene(self.viewport().rect().center())
        f = self.set_zoom(1.0)
        self.centerOn(center)
        return f

    def reset_view(self):
        """双击复位 = 100% + 回到左上角（图的源寄存器在左，复位就该看见它们）。"""
        f = self.set_zoom(1.0)
        self.horizontalScrollBar().setValue(self.horizontalScrollBar().minimum())
        self.verticalScrollBar().setValue(self.verticalScrollBar().minimum())
        return f

    def zoom_at(self, anchor, factor):
        """以 viewport 坐标 `anchor` 为锚缩放 `factor` 倍，clamp `[ZOOM_MIN, ZOOM_MAX]`。

        不用 `AnchorUnderMouse`：那条路要求 `transformationAnchor` 与 `scale()` 配合，一旦
        我们自己 clamp 到边界（不缩了）它仍会挪一下视口 —— 手动算锚点才能做到「到顶就真的不动」。"""
        a = anchor if isinstance(anchor, QtCore.QPoint) else QtCore.QPoint(int(anchor.x()),
                                                                          int(anchor.y()))
        cur = self.zoom()
        new = max(TH.ZOOM_MIN, min(TH.ZOOM_MAX, cur * float(factor)))
        if abs(new - cur) < 1e-9:
            return cur
        before = self.mapToScene(a)
        self.setTransform(QtGui.QTransform.fromScale(new, new))
        after = self.mapFromScene(before)
        d = after - a
        self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() + d.x())
        self.verticalScrollBar().setValue(self.verticalScrollBar().value() + d.y())
        self.zoomChanged.emit(new)
        return new

    # ── 导出（C-284）──
    def export_svg(self, path):
        """导出 SVG：**不带高亮**（交付物不该记录「我当时点中了哪根线」），复用同一份 layout。"""
        if self.graph is None:
            return ""
        # 交付物里图例**要内嵌**（收图的人手里只有这一个文件，控件版的图例跟不出去）
        text = sigflow.render_svg(self.graph, layout=self.layout, highlight_net=None)
        with open(str(path), "w", encoding="utf-8") as fh:
            fh.write(text)
        return str(path)

    def export_png(self, path, scale=2.0):
        """导出 PNG：走 `sigflow.render_png`（与 ppt / 邮件贴图同一条路），2× 便于贴幻灯片。"""
        if self.graph is None:
            return ""
        # 交付物里图例**要内嵌**（收图的人手里只有这一个文件，控件版的图例跟不出去）
        text = sigflow.render_svg(self.graph, layout=self.layout, highlight_net=None)
        return sigflow.render_png(text, str(path), scale=float(scale))

    # ── 事件 ──
    def wheelEvent(self, event):
        if event.modifiers() & Qt.ControlModifier:
            dy = event.angleDelta().y()
            if dy:
                step = TH.ZOOM_STEP if dy > 0 else (1.0 / TH.ZOOM_STEP)
                pos = event.position()
                self.zoom_at(QtCore.QPoint(int(pos.x()), int(pos.y())), step)
            event.accept()
            return
        QtWidgets.QGraphicsView.wheelEvent(self, event)

    def mousePressEvent(self, event):
        btn = event.button()
        if btn in (Qt.RightButton, Qt.MiddleButton):
            self._panning = True
            self._pan_from = event.position().toPoint()
            self.viewport().setCursor(Qt.ClosedHandCursor)
            event.accept()
            return
        if btn == Qt.LeftButton and self.graph is not None:
            it = self.hit_at(self.mapToScene(event.position().toPoint()))
            hit_net = str(it.data(ROLE_NET) or "") if it is not None else ""
            self.netClicked.emit(hit_net)
            event.accept()
            return
        QtWidgets.QGraphicsView.mousePressEvent(self, event)

    def mouseMoveEvent(self, event):
        if self._panning and self._pan_from is not None:
            p = event.position().toPoint()
            d = p - self._pan_from
            self._pan_from = p
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - d.x())
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() - d.y())
            event.accept()
            return
        QtWidgets.QGraphicsView.mouseMoveEvent(self, event)

    def mouseReleaseEvent(self, event):
        if self._panning and event.button() in (Qt.RightButton, Qt.MiddleButton):
            self._panning = False
            self._pan_from = None
            self.viewport().setCursor(Qt.ArrowCursor)
            event.accept()
            return
        QtWidgets.QGraphicsView.mouseReleaseEvent(self, event)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.reset_view()
            event.accept()
            return
        QtWidgets.QGraphicsView.mouseDoubleClickEvent(self, event)

    def contextMenuEvent(self, event):
        """右键是「按住拖动平移」的手柄，不能同时弹菜单（Design ⑦ `onContextMenu` 屏蔽）。"""
        event.accept()

    def showEvent(self, event):
        QtWidgets.QGraphicsView.showEvent(self, event)
        if self._fit_pending:
            self.zoom_fit()

    def resizeEvent(self, event):
        QtWidgets.QGraphicsView.resizeEvent(self, event)
        if self._fit_pending:
            self.zoom_fit()


# ═════════════════════════════ 图例 ═════════════════════════════
class FlowLegend(QtWidgets.QWidget):
    """`FLOW_LEGEND`：四种线型各一条样线 + 一句人话（文案 = `terms.FLOW_LEGEND`）。

    SVG 自带一份画在画布底部的图例（`sigflow._draw_legend`）—— 缩放 / 平移之后它会滚出视野
    （C0-c 备注 ⑤）。GUI 侧另起一个**钉在画布下方、不随缩放走**的控件版；
    等 `render_svg` 有了 `legend=False` 再把内嵌那份关掉（见 `HOST_REQUIREMENTS`）。
    """

    def __init__(self, parent=None):
        QtWidgets.QWidget.__init__(self, parent)
        self.setObjectName(N.FLOW_LEGEND)
        # 四条并排 ≈ 700px。用 `FlowLayout`（清单底部工具条同一个）让它在窄窗口上**折行**，
        # 而不是把电路图区的最小宽度顶到 700 —— 那样 1366×768 上清单收不到 Design 要的 420
        # （C-260）。宽窗口下仍是一行，看不出区别。
        lay = FlowLayout(self, hspacing=16, vspacing=2)
        lay.setContentsMargins(10, 2, 10, 2)
        self.items = []
        for i, text in enumerate(T.FLOW_LEGEND):
            lb = QtWidgets.QLabel(text, self)
            lb.setObjectName(fmt_legend_item(i))
            lb.setFont(ui_font(TH.FS_UI_SMALL))
            lb.setStyleSheet("color:%s;" % _LEGEND_COLORS[i % len(_LEGEND_COLORS)])
            lay.addWidget(lb)
            self.items.append(lb)


# ═════════════════════════════ 面板 ═════════════════════════════
class SigflowView(QtWidgets.QWidget):
    """⑦ 电路图区：工具条 + `FlowCanvas` + 图例 + 底栏提示（Design §1.2 ⑦）。

    数据入口只有 `set_signal(an)`：`an["graph"]` 由 `state.analyze(name, want_graph=True)` 带出来，
    本模块**不自己调引擎**（架构 §1.2 分层）。
    """

    #: 全屏按钮（`main_view.set_flow_fullscreen` 接它；本模块不认识 main_view）
    fullscreenToggled = QtCore.Signal(bool)
    #: 导出落盘成功（上层接到状态栏 / `state.record_export`）
    exported = QtCore.Signal(str)

    def __init__(self, bus=None, parent=None):
        QtWidgets.QWidget.__init__(self, parent)
        self.setObjectName(N.FLOW_PANEL)
        self.bus = bus
        self.an = None
        self._name = ""

        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._build_toolbar())

        self.canvas = FlowCanvas(self)
        self.empty = QtWidgets.QLabel("", self)
        self.empty.setObjectName(N.FLOW_EMPTY)
        self.empty.setAlignment(Qt.AlignCenter)
        self.empty.setWordWrap(True)
        self.empty.setFont(ui_font(TH.FS_UI))
        self.empty.setStyleSheet("color:%s;background:%s;padding:24px;"
                                 % (TH.MUTE, TH.HINT_BG))
        self.body = QtWidgets.QStackedWidget(self)
        self.body.setObjectName(N.FLOW_BODY)
        self.body.addWidget(self.canvas)     # index 0
        self.body.addWidget(self.empty)      # index 1
        root.addWidget(self.body, 1)

        self.legend = FlowLegend(self)
        root.addWidget(self.legend)

        self.footer = QtWidgets.QLabel(T.FLOW_FOOTER, self)
        self.footer.setObjectName(N.FLOW_FOOTER)
        self.footer.setWordWrap(True)
        self.footer.setFont(ui_font(TH.FS_UI_SMALL))
        self.footer.setStyleSheet("color:%s;background:%s;border-top:1px solid %s;padding:4px 10px;"
                                  % (TH.MUTE, TH.PANEL_BG, TH.BORDER_LIGHT))
        root.addWidget(self.footer)

        self.canvas.netClicked.connect(self._on_net_clicked)
        self.canvas.zoomChanged.connect(self._on_zoom_changed)
        self._connect_bus()
        self.clear()

    # ── 工具条 ──
    def _build_toolbar(self):
        bar = QtWidgets.QWidget(self)
        bar.setObjectName(N.FLOW_TOOLBAR)
        bar.setStyleSheet("QWidget#%s{background:%s;border-bottom:1px solid %s;}"
                          % (N.FLOW_TOOLBAR, TH.PANEL_BG, TH.BORDER_LIGHT))
        lay = QtWidgets.QHBoxLayout(bar)
        lay.setContentsMargins(10, 4, 10, 4)
        lay.setSpacing(8)

        self.title = QtWidgets.QLabel(T.FLOW_TITLE, bar)
        self.title.setObjectName(N.FLOW_TITLE)
        self.title.setFont(ui_font(TH.FS_UI_TITLE, bold=True))
        self.title.setStyleSheet("color:%s;" % TH.INK)
        lay.addWidget(self.title)

        self.subtitle = QtWidgets.QLabel(T.FLOW_SUBTITLE, bar)
        self.subtitle.setObjectName(N.FLOW_SUBTITLE)
        self.subtitle.setFont(ui_font(TH.FS_UI_SMALL))
        self.subtitle.setStyleSheet("color:%s;" % TH.MUTE)
        # C-260：副标是一句提示，不该参与「这块区最少要多宽」的计算。
        # 不这么写，工具条 5 个按钮 + 这一句会把电路图区的最小宽度顶到 ~700px，
        # 1366×768 上清单就收不到 Design 要的 420 了（正常宽度下显示不受影响）。
        self.subtitle.setSizePolicy(QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Preferred)
        lay.addWidget(self.subtitle)
        lay.addStretch(1)

        self.full_btn = self._bar_button(T.FLOW_BTN_FULLSCREEN, N.FLOW_BTN_FULLSCREEN, bar, lay)
        self.full_btn.setCheckable(True)
        self.full_btn.toggled.connect(self._on_fullscreen_toggled)

        self.fit_btn = self._bar_button(T.FLOW_BTN_FIT, N.FLOW_BTN_FIT, bar, lay)
        self.fit_btn.clicked.connect(self.canvas_fit)

        self.hundred_btn = self._bar_button(T.FLOW_BTN_100, N.FLOW_BTN_100, bar, lay)
        self.hundred_btn.clicked.connect(self.canvas_100)

        self.zoom_label = QtWidgets.QLabel("", bar)
        self.zoom_label.setObjectName(N.FLOW_ZOOM_LABEL)
        self.zoom_label.setFont(ui_font(TH.FS_UI_SMALL))
        self.zoom_label.setStyleSheet("color:%s;" % TH.TEXT)
        self.zoom_label.setMinimumWidth(46)
        self.zoom_label.setAlignment(Qt.AlignCenter)
        lay.addWidget(self.zoom_label)

        self.svg_btn = self._bar_button(T.FLOW_BTN_EXPORT_SVG, N.FLOW_BTN_EXPORT_SVG, bar, lay)
        self.svg_btn.clicked.connect(self.export_svg)
        self.png_btn = self._bar_button(T.FLOW_BTN_EXPORT_PNG, N.FLOW_BTN_EXPORT_PNG, bar, lay)
        self.png_btn.clicked.connect(self.export_png)
        return bar

    @staticmethod
    def _bar_button(text, obj_name, parent, layout):
        b = QtWidgets.QPushButton(text, parent)
        b.setObjectName(obj_name)
        b.setFont(ui_font(TH.FS_UI_SMALL))
        b.setFixedHeight(TH.BTN_H)
        b.setCursor(Qt.PointingHandCursor)
        b.setStyleSheet("QPushButton{background:%s;border:1px solid %s;border-radius:3px;"
                        "padding:0 8px;color:%s;}"
                        "QPushButton:checked{background:%s;border-color:%s;color:%s;}"
                        % (TH.WHITE, TH.BORDER, TH.TEXT,
                           TH.LIGHT_BLUE_BG, TH.LIGHT_BLUE_BORDER, TH.LIGHT_BLUE_FG))
        layout.addWidget(b)
        return b

    # ── bus（C-282 / I-18）──
    def _connect_bus(self):
        sig = getattr(self.bus, "netSelected", None) if self.bus is not None else None
        if sig is not None and hasattr(sig, "connect"):
            sig.connect(self._on_bus_net_selected)

    def set_bus(self, bus):
        """C2-int 接线用：构造时没给 bus 也能起窗（与 C1 各视图同一口径）。"""
        self.bus = bus
        self._connect_bus()

    def _on_net_clicked(self, net):
        """左键命中 → 只往总线上广播；画面高亮**等总线回来再改**（单一真相源，I-18）。"""
        if self.bus is None:
            self.canvas.highlight(net)
            return
        if net:
            self.bus.select(net, "flow")
        else:
            self.bus.clear("flow")

    def _on_bus_net_selected(self, net, origin):
        """总线广播 → 只更新样式，**绝不回写选择**（重渲染是纯样式，不产生新的 select）。"""
        self.canvas.highlight(net, origin)

    # ── 数据入口 ──
    def set_signal(self, an, name=""):
        """喂一份 `an`（`providers.analyze(..., want_graph=True)` 的产物）。

        四种画面：有图 / 没图（只读回读 · 未解析）/ 构图失败（点名 issue 原文）/ 没选信号。"""
        self.an = an
        self._name = str(name or (an or {}).get("name") or "")
        graph = (an or {}).get("graph") if isinstance(an, dict) else None
        if graph is None:
            self._show_empty(self._empty_text(an))
            return
        # 先切页再换图：QStackedWidget 藏着的那一页宽度还是默认 100px，先 set_graph 会按它算 fit。
        self._show_canvas()
        self.canvas.set_graph(graph, highlight_net=self._bus_net())
        self._on_zoom_changed(self.canvas.zoom())

    def set_pending(self):
        """后台还在展开这个信号（worker 的骨架行先出，图要等分析完）。"""
        self.an = None
        self._show_empty(T.FLOW_PENDING)

    def clear(self):
        """没选信号 / 换表。"""
        self.an = None
        self._name = ""
        self.canvas.set_graph(None)
        self._show_empty(T.FLOW_EMPTY)

    def _empty_text(self, an):
        """没图的原因：构图失败就把引擎那条 ⚠ 原样点名（过 scrub），否则退回通用文案。

        「点名 + 原因在前」是 C-270 的口径 —— 只说「画不出来」等于让人去猜。"""
        issues = list((an or {}).get("issues") or []) if isinstance(an, dict) else []
        named = [T.scrub(str(x)) for x in issues if _GRAPH_FAIL_RE.search(str(x))]
        if named:
            return "\n".join([T.FLOW_BUILD_FAILED] + named)
        return T.FLOW_EMPTY

    def _bus_net(self):
        return getattr(self.bus, "current_net", "") if self.bus is not None else ""

    def _show_canvas(self):
        self.body.setCurrentWidget(self.canvas)
        for b in (self.fit_btn, self.hundred_btn, self.svg_btn, self.png_btn):
            b.setEnabled(True)
        self._on_zoom_changed(self.canvas.zoom())

    def _show_empty(self, text):
        self.empty.setText(text)
        self.body.setCurrentWidget(self.empty)
        for b in (self.fit_btn, self.hundred_btn, self.svg_btn, self.png_btn):
            b.setEnabled(False)
        self.zoom_label.setText("")

    # ── 工具条动作 ──
    def canvas_fit(self):
        return self.canvas.zoom_fit()

    def canvas_100(self):
        return self.canvas.zoom_100()

    def _on_zoom_changed(self, factor):
        self.zoom_label.setText(T.FLOW_ZOOM_FMT.format(pct=int(round(float(factor) * 100))))

    def _on_fullscreen_toggled(self, on):
        self.full_btn.setText(T.FLOW_BTN_EXIT_FULLSCREEN if on else T.FLOW_BTN_FULLSCREEN)
        self.fullscreenToggled.emit(bool(on))

    def set_fullscreen(self, on):
        """外部（main_view / 快捷键）改状态时同步按钮，不重复 emit。"""
        if bool(on) != self.full_btn.isChecked():
            self.full_btn.setChecked(bool(on))

    # ── 导出（C-284）──
    def _default_export_name(self, ext):
        base = self._name or (self.canvas.graph.title if self.canvas.graph is not None else "")
        base = re.sub(r"[^0-9A-Za-z_.-]+", "_", str(base)).strip("_") or "sigflow"
        return base + ext

    def _ask_path(self, ext, caption, flt):
        path, _f = QtWidgets.QFileDialog.getSaveFileName(
            self, caption, self._default_export_name(ext), flt)
        return path

    def export_svg(self):
        if self.canvas.graph is None:
            return ""
        path = self._ask_path(".svg", T.FLOW_BTN_EXPORT_SVG, "SVG (*.svg)")
        if not path:
            return ""
        if not os.path.splitext(path)[1]:
            path += ".svg"
        out = self.canvas.export_svg(path)
        if out:
            self.exported.emit(out)
        return out

    def export_png(self):
        if self.canvas.graph is None:
            return ""
        path = self._ask_path(".png", T.FLOW_BTN_EXPORT_PNG, "PNG (*.png)")
        if not path:
            return ""
        if not os.path.splitext(path)[1]:
            path += ".png"
        out = self.canvas.export_png(path)
        if out:
            self.exported.emit(out)
        return out


#: 架构 §6.10 里这个面板叫 `SigFlowPanel`；C2-int 两个名字都能 import，不必二选一改文档。
SigFlowPanel = SigflowView


# ═══════════════════════ 对宿主（state / main_view / sigflow）的要求 ═══════════════════════
HOST_REQUIREMENTS = (
    ("state.analyze(name, want_graph=True)", "→ an；`an['graph']` 就是本模块要的 sigflow.Graph。"
                                             "本模块不调引擎，只吃 an（架构 §1.2）"),
    ("state.currentChanged", "换信号 → 宿主调 set_signal(an, name)；还没分析完先调 set_pending()"),
    ("bus.netSelected / bus.select(net, 'flow') / bus.clear('flow')",
     "C-282 的唯一通道；本模块收到广播只重渲染样式，绝不回写选择（I-18）"),
    ("main_view.set_flow_fullscreen(on)", "接 `fullscreenToggled(bool)`；反向用 set_fullscreen(on)"),
    ("sigflow.render_svg(..., legend=False)",
     "✔ C2-int 已加：`FlowCanvas._render` 传 `legend=False`（画面用控件版 FLOW_LEGEND，"
     "内嵌那份会随缩放滚出视野）；`export_svg` / `export_png` **不传**，交付物照旧内嵌；"
     "默认 True，报告 HTML 与 ppt 的字节一个都没变"),
    ("sigflow._hl_key 用总线同一把钥匙",
     "✔ C2-int 已改成 `excel_model._strip_width`（= `bus.net_key`）：选中一根位切片 = 整根网"
     "各位一起亮，这正是「当前线网」该有的语义。GUI 侧兜底的 `FlowCanvas._graph_spelling` 随之删掉"
     "（回归测试：`test_c282_bit_sliced_net_still_highlights_after_bus_strips_width`）"),
    ("names.py 的 5 个 objectName", "✔ C2-int 已并进 names.py：FLOW_SUBTITLE / FLOW_BODY / FLOW_EMPTY / "
                                   "FLOW_VIEWPORT / fmt_flow_legend_item(i)"),
    ("terms.py 的两条文案", "✔ C2-int 已补：`FLOW_PENDING`（分析中）、`FLOW_BUILD_FAILED`（构图失败的抬头，"
                         "后面仍按 C-270 点名 an['issues'] 的 ⚠ 原文）、`FLOW_WHY_REG`（C-283 tooltip）"),
)
