# -*- coding: utf-8 -*-
"""test_sigflow.py — 信号流门级图（dreg_verify/sigflow.py + 改动点 A/A′/C）。

覆盖三件事：
  ① 两张 mirror 全部 Topout 信号都建得出图，且 render_svg 的产出能被 QtSvg 真解析（isValid）。
  ② 中间层 net 名标得回来（origin_net）、不可展 mux 走结构路线仍有图、F0 直连寄存器是最简模板、
     门控信号有 GATE、猜名叶子 trusted=False。
  ③ want_graph 默认 False 时 res.graph is None（旧路径零影响）。

顺带把 21 个信号的 SVG 写进 tests/_sigflow_out/（gitignored）供人打开看。
"""

import os
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

OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_sigflow_out")


# ───────────────────────────── 夹具 ─────────────────────────────
@pytest.fixture(scope="module")
def btlp(tmp_path_factory):
    p = tmp_path_factory.mktemp("sf_btlp") / "mirror_btlp_dreg.xlsx"
    make_mirror_btlp.build(str(p))
    wb = M.load_workbook(str(p))
    return wb, R.Resolver(wb)


@pytest.fixture(scope="module")
def wl(tmp_path_factory):
    p = tmp_path_factory.mktemp("sf_wl") / "mirror_wl_dreg.xlsx"
    make_mirror_excel.build(str(p))
    wb = M.load_workbook(str(p))
    return wb, R.Resolver(wb)


@pytest.fixture(scope="module")
def qt_app():
    """QtSvg 解析 SVG 需要一个 QGuiApplication（离屏）。"""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtGui import QGuiApplication
    return QGuiApplication.instance() or QGuiApplication([])


def _analyze(wb, res, name, **kw):
    topo = next(t for t in wb.topout if t.name == name)
    return T.analyze_signal(wb, res, topo, mode="min", max_tests=32, **kw)


def _svg_valid(svg_text):
    from PySide6.QtCore import QByteArray
    from PySide6.QtSvg import QSvgRenderer
    r = QSvgRenderer(QByteArray(svg_text.encode("utf-8")))
    return r.isValid(), r.defaultSize()


def _walk(node, fn):
    if node is None:
        return
    fn(node)
    for a in ("operand", "left", "right", "cond", "then", "els", "body", "count_node"):
        if hasattr(node, a):
            _walk(getattr(node, a), fn)
    for p in (getattr(node, "parts", None) or []):
        _walk(p, fn)


# ═════════════════ ① 全量：21 个 Topout 信号都建得出图、SVG 都能解析 ═════════════════
def test_all_topout_build_graph_and_render(btlp, wl, qt_app):
    """两张 mirror 全部 Topout（12+9=21）：analyze_signal(want_graph=True) 不抛错；
    有根的 20 个都出图且 SVG 能被 QSvgRenderer 加载；SVG 全部落盘供人看。

    pll_lock_indicator 是 RO 回读（status=skip、无 cone、无源对象）→ 按设计 graph is None，
    不硬造一张假图（改动点 C 只在 logic/mux/register 三个根分支挂图）。
    """
    os.makedirs(OUT_DIR, exist_ok=True)
    total, drawn, skipped = 0, 0, []
    for tag, (wb, res) in (("btlp", btlp), ("wl", wl)):
        for topo in wb.topout:
            total += 1
            r = T.analyze_signal(wb, res, topo, mode="min", max_tests=32, want_graph=True)
            assert not [i for i in r.issues if "信号流图构建失败" in i], \
                "%s 建图抛异常: %s" % (topo.name, r.issues)
            if r.status != "ok":
                assert r.graph is None
                skipped.append(topo.name)
                # 也落一张占位图，让 _sigflow_out/ 与 Topout 清单 21 个一一对应、不缺文件
                with open(os.path.join(OUT_DIR, "%s_%s.svg" % (tag, topo.name)), "w",
                          encoding="utf-8") as f:
                    f.write(SF.render_svg(SF.Graph(topo.name)))
                continue
            g = r.graph
            assert g is not None, "%s 没出图" % topo.name
            assert g.nodes and g.edges, "%s 图是空的" % topo.name
            assert g.has_kind("TOPOUT"), "%s 图里没有 TOPOUT 端子" % topo.name
            assert {n.kind for n in g.nodes} <= set(SF.KINDS), \
                "%s 出现未登记的图元种类" % topo.name
            svg = SF.render_svg(g)
            ok, sz = _svg_valid(svg)
            assert ok, "%s 的 SVG QtSvg 解析失败" % topo.name
            assert sz.width() > 0 and sz.height() > 0
            with open(os.path.join(OUT_DIR, "%s_%s.svg" % (tag, topo.name)), "w",
                      encoding="utf-8") as f:
                f.write(svg)
            drawn += 1
    assert total == 21
    assert skipped == ["pll_lock_indicator"]      # 唯一无源对象的（RO 回读）
    assert drawn == 20


def test_render_svg_shape_is_wellformed(btlp, qt_app):
    """SVG 骨架：viewBox 与 width/height 一致、根标签闭合、元素挂了 data-net（二期点选高亮用）。"""
    r = _analyze(*btlp, name="d_logic_bt_lp_rx_en", want_graph=True)
    svg = SF.render_svg(r.graph)
    assert svg.startswith("<svg xmlns=") and svg.rstrip().endswith("</svg>")
    import re
    m = re.search(r'width="(\d+)" height="(\d+)" viewBox="0 0 (\d+) (\d+)"', svg)
    assert m and m.group(1) == m.group(3) and m.group(2) == m.group(4)
    assert 'data-net="' in svg and 'data-kind="' in svg
    assert _svg_valid(svg)[0]


# ═════════════════ ② 中间层 net 名（改动点 A/A′）═════════════════
def test_origin_net_survives_cone_substitution(wl):
    """最深 cone 链 d_wl_rf_tx_epa_2g_mixer_en：最终 AST 上能取回 ≥5 处 origin_net，
    且含跨 logic↔mux 边界的 d_wl_rf_tx2g_en 与 d_wl_rf_freq_sel。

    这是改动点 A 的核心断言——cone._substitute 按引用嵌子树，所以挂在子树根上的标签
    活得过上层代入；标签一旦丢了，图上所有中间网名都会退化成匿名。
    """
    r = _analyze(*wl, name="d_wl_rf_tx_epa_2g_mixer_en")
    assert r.status == "ok" and r.node is not None
    hits = []
    _walk(r.node, lambda n: hits.append(n) if hasattr(n, "origin_net") else None)
    nets = [n.origin_net for n in hits]
    assert len(hits) >= 5, nets
    assert "d_wl_rf_tx2g_en" in nets
    assert "d_wl_rf_freq_sel" in nets
    assert r.node.origin_net == "d_wl_rf_tx_epa_2g_mixer_en"     # 根 = 本行输出网
    assert {getattr(n, "origin_kind", None) for n in hits} <= {"logic", "mux"}
    assert all(getattr(n, "origin_width", None) for n in hits)


def test_origin_net_on_non_cone_path(btlp):
    """改动点 A′：BTLP 全是非 cone 路径（无内部输入），根 AST 也必须带标签。
    d_logic_bt_lp_tsensor 的标签应是【RTL 网名】(_to_mux 尾缀)，不是 Excel 裸基名。"""
    r = _analyze(*btlp, name="d_logic_bt_lp_tsensor")
    assert not r.expanded                                   # 确实是非 cone 路径
    assert r.node.origin_net == "d_logic_bt_lp_tsensor_to_mux"
    assert r.node.origin_kind == "logic" and r.node.origin_width == 4


def test_graph_edges_carry_intermediate_net_names(wl):
    """图上确实用上了那些中间网名（不是只挂在 AST 上没画出来）。"""
    r = _analyze(*wl, name="d_wl_rf_tx_epa_2g_mixer_en", want_graph=True)
    nets = {e.net for e in r.graph.edges if e.net}
    assert "d_wl_rf_tx2g_en" in nets
    assert "d_wl_rf_freq_sel" in nets
    assert "d_wl_rf_tx_epa_2g_mixer_en" in nets              # 顶层输出线
    # 每条线的位宽都算得出来（self_width），多 bit 线才是粗线
    assert all(e.width is None or e.width >= 1 for e in r.graph.edges)


# ═════════════════ ② 结构路线：synthesize 抛错的 mux 也必须有图 ═════════════════
def test_unexpandable_mux_still_draws(wl):
    """d_wl_rf_lpf_cmain（mux202←58←57 三级级联，grp58 case 位宽≠ctrl 位宽 →
    synthesize_mux_expr 抛 ConeError、AST 路线没图可画）走结构路线仍出图。"""
    from dreg_verify import cone
    from dreg_verify import mux_gen
    wb, res = wl
    grp = cone._find_mux(wb, "d_wl_rf_lpf_cmain")
    with pytest.raises(cone.ConeError):                      # 前提：AST 路线确实不可用
        mux_gen.synthesize_mux_expr(wb, res, grp)

    r = _analyze(*wl, name="d_wl_rf_lpf_cmain", want_graph=True)
    assert r.status == "ok" and r.node is None               # mux 根本就没有 AST
    g = r.graph
    assert g is not None and len(g.nodes) >= 8, len(g.nodes)
    # 三级级联的三个 MUXN 都在图上，且各自标着自己的输出网名
    groups = {n.meta.get("group") for n in g.nodes if n.kind == "MUXN"}
    assert {"202", "58", "57"} <= {str(x) for x in groups if x is not None}, groups
    nets = {e.net for e in g.edges if e.net}
    assert "d_wl_rf_lpf_cmain" in nets and "d_wl_rf_bwctrl" in nets
    # 寄存器叶子带地址位段（图上要能读出 RW @0x.. [msb:lsb]）
    regs = [n for n in g.nodes if n.kind == "REG"]
    assert regs and all(n.meta.get("address") is not None for n in regs)


def test_mux_root_case_labels_and_bustap(wl):
    """mux 结构路线的 case 标签 = Excel F 列原文（don't-care 位原样），
    控制切片 temp_code[3:1] 画成 BUSTAP 抽头（非零偏移一眼可见）。"""
    r = _analyze(*wl, name="d_wl_rf_lo2g5g_mixer2g_trim", want_graph=True)
    g = r.graph
    mux = next(n for n in g.nodes if n.kind == "MUXN" and str(n.meta.get("group")) == "157")
    labels = [p["label"] for p in mux.ports if p["side"] == "left"]
    assert len(labels) == 13
    assert any("死分支" in x for x in labels), labels        # shadowed=[5..9] 标出来
    assert g.has_kind("BUSTAP")                              # temp_code[3:1] 抽头


def test_mux_spec_conflicts_marked(wl):
    """规格矛盾（同一选择值选不同源）在支标上标 conflict——图是让 designer 一眼看见的载体。"""
    r = _analyze(*wl, name="d_wl_rf_lp5g_rxrf_lna_lctune", want_graph=True)
    mux = next(n for n in r.graph.nodes if n.kind == "MUXN")
    labels = [p["label"] for p in mux.ports if p["side"] == "left"]
    assert any("conflict" in x for x in labels), labels
    assert mux.meta["conflict_rows"] and mux.meta["shadowed"]


def test_btlp_mux_root_with_logic_control(btlp):
    """BTLP d_bt_lp_lna_itrim：8 case 含 don't-care(4'b000x)，控制是 logic 行 →
    结构路线把那一行的门图也画出来（MUX2 喂 MUXN 的 S 口）。"""
    r = _analyze(*btlp, name="d_bt_lp_lna_itrim", want_graph=True)
    g = r.graph
    mux = next(n for n in g.nodes if n.kind == "MUXN")
    labels = [p["label"] for p in mux.ports if p["side"] == "left"]
    assert len(labels) == 8
    assert any("x" in x for x in labels), labels             # don't-care 位原样
    assert g.has_kind("MUX2")                                # 控制那一行的三元
    # 控制线上标的是 logic 行的 RTL 网名
    s_edge = next(e for e in g.edges if e.dst == mux.id and e.dst_port == "S0")
    assert s_edge.net == "d_logic_bt_lp_tsensor_to_mux"


# ═════════════════ ② F0 直连寄存器 / GATE / 改名 / 不可信叶子 ═════════════════
def test_register_root_minimal_template(btlp):
    """F0 直连寄存器根：专用最简模板 [REG] → [TOPOUT]，就两个节点一条线，不塞多余图元。"""
    r = _analyze(*btlp, name="clk_force_on", want_graph=True)
    g = r.graph
    assert len(g.nodes) == 2 and len(g.edges) == 1
    assert [n.kind for n in g.nodes] == ["REG", "TOPOUT"]
    reg = g.nodes[0]
    assert reg.meta["address"] is not None and reg.meta["reg_kind"] == "RW"
    assert "@0x" in reg.sub                                  # 盒上第二行 = RW @0x1[9:9]
    assert g.edges[0].net == "clk_force_on" and g.edges[0].trusted


def test_gated_signal_has_gate_node(wl):
    """WL 门控信号：res.dft_gate 有 → 图上套一层 GATE（iddq ? 0 : 功能），
    门网自己也是一个叶子（force 目标），门口在顶边。"""
    r = _analyze(*wl, name="d_wl_rf_lo2g5g_bias_en", want_graph=True)
    assert r.dft_gate is not None
    g = r.graph
    gate = next(n for n in g.nodes if n.kind == "GATE")
    assert gate.meta["transparent"] == 0
    assert "? 0 :" in gate.sub and gate.meta["gate_base"].endswith("iddq_mode")
    assert [p["side"] for p in gate.ports if p["name"] == "G"] == ["top"]
    # 门的输出直接进 TOPOUT
    top = next(n for n in g.nodes if n.kind == "TOPOUT")
    assert any(e.src == gate.id and e.dst == top.id for e in g.edges)
    # 门网叶子在图上
    assert any(n.meta.get("base") == gate.meta["gate_base"] for n in g.nodes)


def test_ungated_signal_has_no_gate_node(btlp):
    """BTLP dft 页是空的 → 一个 GATE 都不该冒出来（别把 logic 页显式 iddq 输入当门画）。"""
    r = _analyze(*btlp, name="d_logic_bt_lp_rx_en", want_graph=True)
    assert r.dft_gate is None
    assert not r.graph.has_kind("GATE")
    assert r.graph.has_kind("NOT") and r.graph.has_kind("AND")   # (A?C:B)&(~D) 的门


def test_level_shift_rename_block(btlp):
    """d_en_refbuf_ls：源 d_en_refbuf 经 level_shift 到顶层口 d_en_refbuf_ls →
    图上一个 RENAME 块（左源名、右顶层口名）。"""
    r = _analyze(*btlp, name="d_en_refbuf_ls", want_graph=True)
    rn = next(n for n in r.graph.nodes if n.kind == "RENAME")
    assert rn.meta["source"] == "d_en_refbuf" and rn.meta["probe"] == "d_en_refbuf_ls"
    assert "→" in rn.sub


def test_untrusted_leaves_marked(wl):
    """41% 的 WL 叶子名字是按命名约定猜的（found_in=wire/needs-prefix/prefixed-wire）→
    叶子 trusted=False + 角标，它出去的线也 trusted=False（渲染成虚线）。R41 推断层风险上图。"""
    r = _analyze(*wl, name="d_wl_rf_lp5g_rxrf_lna_lctune", want_graph=True)
    g = r.graph
    bad = [n for n in g.nodes if n.meta.get("trusted") is False]
    assert bad, "该信号有 17 个 wire 叶子，图上必须标出来"
    assert all(n.kind == "PIN" for n in bad)
    assert all(n.meta.get("found_in") in SF.UNTRUSTED_FOUND_IN for n in bad)
    assert all(n.meta.get("tip") == SF.UNTRUSTED_TIP for n in bad)
    ids = {n.id for n in bad}
    assert ids <= {e.src for e in g.untrusted_edges}         # 猜名叶子出去的线全是虚线
    # 有地址的寄存器叶子反过来必须是可信的，它出去的线一条都不能是虚线
    good = {n.id for n in g.nodes if n.kind == "REG"}
    assert good and all(n.meta.get("trusted") for n in g.nodes if n.id in good)
    assert not (good & {e.src for e in g.untrusted_edges})
    svg = SF.render_svg(g)
    assert "stroke-dasharray" in svg and SF.UNTRUSTED_TIP in svg


def test_trusted_is_a_whitelist_not_a_blacklist():
    """B1：可信 = found_in ∈ {tmm, regmap} 的【白名单】。

    resolver 还会产出 None / mux-output / logic / logic-internal / logic-computed /
    self-input… 用黑名单会把这些全默认成「可信」——恰恰是这张图最该防的假绿（今天没列举、
    明天新加一个来源就静默变可信）。binding 完全没有(None) 的文案还要跟「查到了但要补前缀」分开。
    """
    assert SF.TRUSTED_FOUND_IN == ("tmm", "regmap")

    class _B:                                                # 最小 InputBinding 替身
        def __init__(self, found_in):
            self.base, self.found_in, self.kind = "some_net", found_in, "RO"
            self.address = self.reg_msb = self.reg_lsb = None
            self.width, self.wire, self.reg_name, self.note = 1, "some_net", "", ""

    for fi in ("tmm", "regmap"):
        g = SF.build_graph(None, None, _FakeRes(E.Var("A"), {"A": _B(fi)}), _FakeRoot())
        leaf = next(n for n in g.nodes if n.kind in ("REG", "PIN"))
        assert leaf.meta["trusted"] is True, fi
    for fi in (None, "mux-output", "logic", "logic-internal", "logic-computed",
               "self-input", "wire", "needs-prefix", "prefixed-wire", "什么新来源"):
        g = SF.build_graph(None, None, _FakeRes(E.Var("A"), {"A": _B(fi)}), _FakeRoot())
        leaf = next(n for n in g.nodes if n.kind in ("REG", "PIN"))
        assert leaf.meta["trusted"] is False, fi
        assert leaf.meta["tip"] == (SF.UNKNOWN_TIP if fi is None else SF.UNTRUSTED_TIP)
        assert all(not e.trusted for e in g.edges if e.src == leaf.id)


def test_leaf_without_binding_is_untrusted():
    """B1：连 binding 都没有的叶子（bindings 里查不到那个变量）→ 不可信 + 虚线 + 「表里完全没查到」。"""
    g = SF.build_graph(None, None, _FakeRes(E.Var("A"), {}), _FakeRoot())
    leaf = next(n for n in g.nodes if n.kind == "PIN")
    assert leaf.meta["trusted"] is False and leaf.meta["found_in"] is None
    assert leaf.meta["tip"] == SF.UNKNOWN_TIP
    svg = SF.render_svg(g)
    assert "stroke-dasharray" in svg and SF.UNKNOWN_TIP in svg


def test_cse_merges_shared_subtrees(wl):
    """同一叶子/同构子树多处引用 → 结构哈希 CSE 合成一个源节点扇出（tx_epa 的 freq_sel
    mux 子树在 AST 里出现两次，图上只能有一个）。"""
    r = _analyze(*wl, name="d_wl_rf_tx_epa_2g_mixer_en", want_graph=True)
    g = r.graph
    n_ast = [0]
    _walk(r.node, lambda n: n_ast.__setitem__(0, n_ast[0] + 1))
    assert len(g.nodes) < n_ast[0]                            # 确实折过（32 → 20 上下）
    bases = [n.meta.get("base") for n in g.nodes if n.kind in ("REG", "PIN")]
    assert len(bases) == len(set(bases)), "同一叶子被画了两次"
    # 扇出确实发生了：至少一个节点有 >1 条出边
    fanout = {}
    for e in g.edges:
        fanout[e.src] = fanout.get(e.src, 0) + 1
    assert max(fanout.values()) > 1


# ═════════════════ ③ want_graph 默认 False：旧路径零影响 ═════════════════
def test_want_graph_defaults_off(btlp, wl):
    """默认不建图（build/report 批量路径不该为显示付代价），且 .graph 属性恒存在。"""
    for wb, res in (btlp, wl):
        for topo in wb.topout:
            r = T.analyze_signal(wb, res, topo, mode="min", max_tests=32)
            assert hasattr(r, "graph") and r.graph is None


def test_graph_failure_never_breaks_analysis(btlp, monkeypatch):
    """建图炸了也只记 ⚠，绝不把一个 ok 信号判成 error（图纯属显示，不进 .sv/向量/账目）。"""
    def boom(*a, **kw):
        raise RuntimeError("刻意炸")
    monkeypatch.setattr(SF, "build_graph", boom)
    r = _analyze(*btlp, name="d_logic_bt_lp_rx_en", want_graph=True)
    assert r.status == "ok" and r.graph is None
    assert any("信号流图构建失败" in i for i in r.issues)
    assert r.vectors                                          # 向量照常生成


# ═════════════════ 图元映射的单点回归（真表里有、mirror 里没有的形态） ═════════════════
class _FakeRoot:
    """最小 TopoutRoot 替身：logic 根、没有源对象（不改名、不套门）。"""
    kind = "logic"
    obj = None
    probe_name = None
    source_name = None
    warnings = []


class _FakeTopo:
    name = "fake_out"
    width = 1
    owner = "tester"


class _FakeRes:
    """最小 TopoutResult 替身：只给 build_graph 需要的字段。"""

    def __init__(self, node, bindings=None, out_width=1):
        self.topo = _FakeTopo()
        self.root = _FakeRoot()
        self.node = node
        self.bindings = bindings or {}
        self.out_width = out_width
        self.expansion = None
        self.dft_gate = None
        self.issues = []
        self.status = "ok"


def _graph_of_ast(node):
    return SF.build_graph(None, None, _FakeRes(node), _FakeRoot())


def test_muxn_folding_of_compare_chain():
    """同一 sel 的 (sel&care)==cv 比较链折成 MUXN，case 标签还原 don't-care 位。

    mirror 表里的 mux 组都只有 2 个 case（折不起来，画 MUX2 才对），但真表的 N 选 1 经
    synthesize_mux_expr 编出来就是这种链（mux_gen.py:③）——逐层画 MUX2 会把 15 路 mux 摊成
    14 个二选一（lna_lctune 239 节点），人读不了。这里直接按那个编码规则造一棵树验折叠。
    """
    sel = E.Var("SEL", 3, 0)
    d = [E.Var("D%d" % i) for i in range(4)]
    care_all, care_x = E.mask(4), E.mask(4) & ~0b0001        # 4'b000x：低位 don't-care
    specs = [(0b0000, care_x), (0b0100, care_all), (0b1000, care_all)]
    node = d[3]                                              # 最内层 els = default 支
    for i in range(len(specs) - 1, -1, -1):
        cv, care = specs[i]
        cond = E.Binary("==", E.Binary("&", sel, E.Const(care, 4)), E.Const(cv & care, 4))
        node = E.Ternary(cond, d[i], node)

    g = _graph_of_ast(node)
    mux = next(n for n in g.nodes if n.kind == "MUXN")
    labels = [p["label"] for p in mux.ports if p["side"] == "left"]
    assert labels == ["4'b000x", "4'b0100", "4'b1000", "default"]
    assert not g.has_kind("MUX2")                             # 整条链折成了一个 MUXN
    assert [p["side"] for p in mux.ports if p["name"] == "S"] == ["top"]


def test_nand_nor_fusion():
    """~(A&B) / ~(A|B) 合成带气泡的 NAND / NOR，不画成 NOT+AND 两个盒。"""
    for op, kind in (("&", "NAND"), ("|", "NOR")):
        g = _graph_of_ast(E.Unary("~", E.Binary(op, E.Var("A"), E.Var("B"))))
        assert g.has_kind(kind), [n.kind for n in g.nodes]
        assert not g.has_kind("NOT")
        assert next(n for n in g.nodes if n.kind == kind).meta["bubble"]


def test_reduce_cmp_bus_elements():
    """归约 / 比较 / 拼接 / 抽头四类图元的映射（真表有、两张 mirror 的 Topout 路径上没有）。"""
    g = _graph_of_ast(E.Unary("|", E.Var("A")))                       # |A 归约
    assert g.has_kind("REDUCE")
    g = _graph_of_ast(E.Binary(">", E.Var("A"), E.Var("B")))          # 非常量右操作数 → 两输入
    cmp_ = next(n for n in g.nodes if n.kind == "CMP")
    assert len(cmp_.port_names("left")) == 2
    g = _graph_of_ast(E.Concat([E.Var("A"), E.Var("B")]))             # {A,B} 拼接
    assert g.has_kind("BUSMERGE")
    g = _graph_of_ast(E.Part(E.Binary("&", E.Var("A"), E.Var("B")), 2, 1))
    tap = next(n for n in g.nodes if n.kind == "BUSTAP")
    assert tap.label == "[2:1]" and tap.meta["msb"] == 2 and tap.meta["lsb"] == 1
    g = _graph_of_ast(E.Binary("<<", E.Var("A"), E.Const(2, 4)))      # 移位 → OP
    assert g.has_kind("OP")


# ═════════════════ ④ HTML 报告内联（开关默认开；.sv 一个字节都不能动） ═════════════════
def _sv_digests(wb, max_tests=256):
    """与 tools/byte_gate.py 同口径的 3 档 .sv 摘要（那里用 max_tests=100000 跑权威 6 值，
    这里为测试速度用默认档——要证的是『建图有没有副作用』，不是重跑基线）。"""
    import hashlib
    out = {}
    for tag, kw in (("min", {"mode": "min"}), ("max", {"mode": "max"}),
                    ("exh", {"mode": "min", "exhaustive": True})):
        text, _build = T.render_topout_sv(wb, max_tests=max_tests, **kw)
        out[tag] = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return out


def test_report_svg_never_touches_sv(btlp, wl):
    """建图/内联 SVG 之后再渲 .sv，3 档摘要必须与建图之前完全一致——图纯属显示，
    绝不能对 .sv 产生任何副作用（权威 6 值由 tools/byte_gate.py 守）。"""
    for wb, _res in (btlp, wl):
        before = _sv_digests(wb)
        rep = T.topout_report(wb, mode="min", max_tests=32)
        assert SF.attach_report_svgs(wb, rep, mode="min", max_tests=32) > 0
        assert _sv_digests(wb) == before


def test_report_html_inlines_sigflow(wl, tmp_path):
    """HTML 报告 ② 真值表每块内联一张 <svg>，且 JSON 数据块仍能被 JSON.parse
    （SVG 里的 </svg> 必须被 js_blob 转义成 <\\/svg>，否则会提前闭掉外层 <script>）。"""
    import json
    import re

    from dreg_verify import cli
    wb, _res = wl
    rep = T.topout_report(wb, mode="min", max_tests=32)
    n = SF.attach_report_svgs(wb, rep, mode="min", max_tests=32)
    assert n == len(rep["tables"]) == 9

    p = tmp_path / "rep.html"
    cli.write_report(str(p), rep, "mirror_wl_dreg.xlsx")
    s = p.read_text(encoding="utf-8")
    assert "</svg>" not in s and s.count(r"<\/svg>") == 9      # 全部转义过，script 不会被闭掉
    assert "details.sigflow" in s                              # CSS 进来了
    m = re.search(r'<script type="application/json" id="tt-data">(.*?)</script>', s, re.S)
    assert m
    data = json.loads(m.group(1).replace(r"<\/", "</"))
    assert len(data) == 9 and all("<svg" in d["h"] for d in data)
    assert all('class="sigflowbox"' in d["h"] for d in data)


def test_report_html_without_sigflow(wl, tmp_path):
    """不调 attach_report_svgs（= --no-sigflow-svg）时报告里一张图都没有，其余内容照旧。"""
    from dreg_verify import cli
    wb, _res = wl
    rep = T.topout_report(wb, mode="min", max_tests=32)
    p = tmp_path / "rep.html"
    cli.write_report(str(p), rep, "mirror_wl_dreg.xlsx")
    s = p.read_text(encoding="utf-8")
    assert "sigflowbox\"><svg" not in s and r"<\/svg>" not in s
    assert '<table class="tt">' in s or "ttblock" in s          # 真值表本体还在


def test_cli_flag_exists():
    """--no-sigflow-svg 关掉内联；默认（不传）= 开。"""
    from dreg_verify import cli
    p = cli.build_argparser()
    assert p.parse_args(["--excel", "x.xlsx"]).no_sigflow_svg is False
    assert p.parse_args(["--excel", "x.xlsx", "--no-sigflow-svg"]).no_sigflow_svg is True


def test_attach_report_svgs_is_crash_proof(wl, monkeypatch):
    """单张图渲染炸掉只是那一张没有，报告数据结构不受影响（绝不让报告整份出不来）。"""
    wb, _res = wl
    rep = T.topout_report(wb, mode="min", max_tests=32)

    def boom(*a, **kw):
        raise RuntimeError("刻意炸")
    monkeypatch.setattr(SF, "render_svg", boom)
    assert SF.attach_report_svgs(wb, rep, mode="min", max_tests=32) == 0
    assert len(rep["tables"]) == 9
    assert not any("sigflow_svg" in t for t in rep["tables"])


def test_empty_graph_renders():
    """没有可画结构时也要给出一张能解析的空图（不能吐半截 SVG 让 GUI 崩）。"""
    svg = SF.render_svg(SF.Graph("空信号"))
    assert svg.startswith("<svg") and svg.rstrip().endswith("</svg>") and "空信号" in svg


def test_render_png_roundtrip(btlp, qt_app, tmp_path):
    """render_svg → render_png（ppt/邮件贴图用）；PySide6 只在这个函数里惰性 import。"""
    r = _analyze(*btlp, name="d_logic_bt_lp_rx_en", want_graph=True)
    p = tmp_path / "g.png"
    SF.render_png(SF.render_svg(r.graph), str(p))
    assert p.exists() and p.stat().st_size > 0


def test_sigflow_imports_no_pyside():
    """sigflow 是纯函数模块：模块级绝不 import PySide6（CLI / 报告生成要能在无 Qt 环境跑）。"""
    src = open(SF.__file__, encoding="utf-8").read()
    head, tail = src.split("def render_png", 1)
    import re
    # 只看真正的 import 语句（注释/docstring 里提到 PySide6 是说明，不算依赖）
    assert not re.findall(r"(?m)^\s*(?:import|from)\s+PySide6", head)
    assert re.findall(r"(?m)^\s+from\s+PySide6", tail)        # 惰性 import 缩进在函数体里
