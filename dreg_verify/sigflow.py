# -*- coding: utf-8 -*-
"""sigflow.py — 信号流门级图：数据层(build_graph) + SVG 渲染器(render_svg)。

需求（用户原话）：「增加一个信号流层级的图；一个信号从源头到 topout，画出 NAND/OR/MUX 等逻辑
组成的实际电路图，配上各线上的 net 名称。」调研与选型见 docs/信号流图可行性_20260912.md §6 MVP。

两条建图路线（各有各的不可替代性，别合并）：
  ① logic / register 根 → **AST 路线**：直接画 result.node（cone 展开后的那棵树）——它就是
     generate_vectors 求期望值用的同一棵树，所以图与真值表绝不会漂移。cone 代入把中间层 net 名
     抹掉了(cone._substitute 按引用嵌子树)，靠改动点 A 挂在子树根上的 origin_net 标回去。
  ② mux 根 → **结构路线**：从 MuxGroup + expand_mux_group 的 ctrl_drivers 递归建 MUXN，
     **不碰 synthesize_mux_expr**。理由：WL 15 个 mux 组里 5 组 synthesize 会抛 ConeError
     (宽 select / case 位宽不符 / 有 hole)，AST 路线在这些组上根本没有图可画；而结构路线
     只读 Excel 模型，永远画得出来，且 case 标签能原样保留 don't-care 位。

纯函数模块：只 import expr / cone / mux_gen / forms / resolver（**绝不 import PySide6**，
render_png 才惰性 import QtSvg）。GUI / HTML 报告 / ppt 三处复用同一份 render_svg。

不做（本轮 MVP 分界，见可行性调研 §6）：交叉最小化、ANSI 门形、交互高亮、自动分页。
"""

from dataclasses import dataclass, field

from . import cone
from . import expr as E
from . import mux_gen

# ───────────────────────────── 图元种类 ─────────────────────────────
KINDS = (
    "REG",        # 寄存器字段盒（RW/RO 且有地址）：两行 = 名 / RW @0x2d[4:4]
    "PIN",        # 管脚 / wire 叶子（force 目标）；名字来自命名约定时虚线框 + 角标
    "AND", "OR", "NOT", "XOR", "NAND", "NOR",
    "MUX2",       # 二选一（AST 的 Ternary）
    "MUXN",       # N 选一（mux 组 / 同 sel 的比较链折叠）
    "GATE",       # dft iddq 门（iddq ? 0 : inner）
    "REDUCE",     # 归约 &a |a ^a ~&a ~|a ~^a
    "CMP",        # 比较（右操作数是常量时直接写在块上）
    "OP",         # 移位 / 算术 / 其它算子
    "BUSTAP",     # 位段抽头 (expr.Part)
    "BUSMERGE",   # 拼接 / 重复 (expr.Concat / expr.Repeat)
    "RENAME",     # 改名 / level_shift 透传块（左源名、右顶层口名）
    "CONST",
    "TOPOUT",     # 顶层输出端子
)

# ⭐【白名单】只有这两个来源的名字是真从表里查到的：tmm(total_memory_map) / regmap 命中了字段。
# 其余一律算「猜的」——resolver.resolve 还会产出 None / mux-output / logic / logic-internal /
# logic-computed / self-input / wire / needs-prefix / prefixed-wire，它们的网名全是按命名约定
# 推的（R41「工具不知 RTL 真网名，全靠命名约定 + cone 猜」是总根）。用黑名单会漏掉今天没列举、
# 明天新加的来源 → 静默标成「可信」，恰恰是这张图最该防的假绿。
TRUSTED_FOUND_IN = ("tmm", "regmap")
UNTRUSTED_TIP = "名字来自命名约定，表里未查到"
UNKNOWN_TIP = "表里完全没查到这个名字"
# 兼容旧引用（测试/外部）：不可信来源的常见取值，仅供显示分类，判定一律走 TRUSTED_FOUND_IN 白名单
UNTRUSTED_FOUND_IN = ("wire", "needs-prefix", "prefixed-wire", "mux-output",
                      "logic", "logic-internal", "logic-computed", "self-input")

MAX_DEPTH = cone.MAX_DEPTH


# ───────────────────────────── 数据模型 ─────────────────────────────
class Node:
    """一个图元。

    id     — 图内唯一（"n7"）
    kind   — KINDS 之一
    label  — 主标（门名 / 信号名）
    sub    — 附注：寄存器地址位段 / 驱动来源 / 角标（渲染成第二行小字）
    ports  — [{"name","side"("left"/"top"),"label"}]，输入口；输出口恒为右边中点 "Y"
    meta   — 结构化附加信息（trusted / binding / group / row / rows …）
    """

    def __init__(self, nid, kind, label, sub="", ports=None, meta=None):
        self.id = nid
        self.kind = kind
        self.label = label
        self.sub = sub or ""
        self.ports = list(ports or [])
        self.meta = dict(meta or {})

    def port_names(self, side):
        return [p["name"] for p in self.ports if p["side"] == side]

    def __repr__(self):
        return "Node(%s,%s,%r)" % (self.id, self.kind, self.label)


class Edge:
    """一条线。net = 这根线的真实 RTL 网名（None=匿名内部节点间的线）。

    trusted=False → 网名是按命名约定猜的（源叶子 found_in ∈ wire/needs-prefix/prefixed-wire），
    渲染成虚线 + 角标。这是图相对表格的最大增量价值之一：让 IC 工程师一眼看穿哪根网是猜的。
    """

    def __init__(self, src, dst, dst_port="A", src_port="Y", net=None, width=None,
                 trusted=True, label=None):
        self.src = src
        self.src_port = src_port
        self.dst = dst
        self.dst_port = dst_port
        self.net = net
        self.width = width
        self.trusted = bool(trusted)
        self.label = label          # 支路标签（MUXN 的 case 值等），渲染在目标口旁

    def __repr__(self):
        return "Edge(%s→%s.%s net=%s w=%s)" % (self.src, self.dst, self.dst_port,
                                               self.net, self.width)


class Graph:
    def __init__(self, title=""):
        self.title = title
        self.nodes = []
        self.edges = []
        self.meta = {}
        self._by_id = {}
        self._seq = 0

    # —— 构造 ——
    def add_node(self, kind, label, sub="", ports=None, meta=None):
        self._seq += 1
        n = Node("n%d" % self._seq, kind, label, sub, ports, meta)
        self.nodes.append(n)
        self._by_id[n.id] = n
        return n

    def add_edge(self, src, dst, dst_port="A", **kw):
        e = Edge(src, dst, dst_port, **kw)
        self.edges.append(e)
        return e

    # —— 查询 ——
    def node(self, nid):
        return self._by_id.get(nid)

    def kinds(self):
        return {n.kind for n in self.nodes}

    def has_kind(self, kind):
        return any(n.kind == kind for n in self.nodes)

    @property
    def untrusted_edges(self):
        return [e for e in self.edges if not e.trusted]

    def __repr__(self):
        return "Graph(%r, %d nodes, %d edges)" % (self.title, len(self.nodes), len(self.edges))


# ───────────────────────────── 建图 ─────────────────────────────
_BIN_GATE = {"&": ("AND", "AND"), "|": ("OR", "OR"), "^": ("XOR", "XOR"),
             "~^": ("XOR", "XNOR"), "&&": ("AND", "AND"), "||": ("OR", "OR")}
_CMP_OPS = ("==", "!=", "<", "<=", ">", ">=")
_REDUCE_OPS = ("&", "|", "^", "~&", "~|", "~^")


def build_graph(wb, resolver, result, root=None):
    """一个 TopoutResult → Graph（源在左、Topout 在右）。

    result 必须已 analyze_signal 过（logic/register 根要有 .node/.bindings；mux 根要有 .expansion）。
    root 省略时取 result.root。永不依赖 synthesize_mux_expr（mux 根走结构路线）。
    """
    root = root if root is not None else result.root
    return _Builder(wb, resolver, result, root).run()


class _Builder:
    def __init__(self, wb, resolver, result, root):
        self.wb = wb
        self.resolver = resolver
        self.res = result
        self.root = root
        self.g = Graph()
        self._memo = {}     # 结构键 → node.id（CSE：同一叶子/同一子树只画一个源，扇出多条线）
        self._out = {}      # node.id → (net, width, trusted)  该节点输出线的属性
        self._stack = []    # 结构路线的环检测（与 cone 同口径：logic 裸名 / "mux:" 前缀）
        self._mode = "ast"  # "ast"=画 result.node（logic/register 根）；"struct"=结构路线（mux 根）
        # 结构路线的 CSE 作用域：E.parse(行表达式) 出来的变量是【行内字母】A..J，跨行会撞名
        # （row1 的 A 和 row2 的 A 完全不是一根网）→ 结构键必须带行作用域，否则 CSE 会错误合并。
        self._scope = None

    # —— 输出线属性登记 / 取用 ——
    def _emit(self, node, net=None, width=None, trusted=True):
        self._out[node.id] = (net, width, bool(trusted))
        return node

    def _link(self, src_id, dst_node, dst_port="A", label=None, net=None):
        net0, w0, tr0 = self._out.get(src_id, (None, None, True))
        return self.g.add_edge(src_id, dst_node.id, dst_port, net=(net or net0),
                               width=w0, trusted=tr0, label=label)

    # ───────── 主流程 ─────────
    def run(self):
        topo = self.res.topo
        w = self.res.out_width or topo.width or 1
        self.g.title = "%s%s" % (topo.name, ("[%d:0]" % (w - 1)) if w > 1 else "")
        self.g.meta = {"topout": topo.name, "root_kind": self.root.kind,
                       "out_width": w, "owner": getattr(topo, "owner", "")}

        inner = self._root_node()
        if inner is None:
            return self.g

        inner = self._wrap_rename(inner)
        inner = self._wrap_gate(inner)

        top = self.g.add_node("TOPOUT", topo.name,
                              sub=("[%d:0]" % (w - 1)) if w > 1 else "",
                              ports=[{"name": "A", "side": "left", "label": ""}],
                              meta={"warnings": list(getattr(self.root, "warnings", None) or []),
                                    "issues": list(self.res.issues or [])})
        self._link(inner.id, top, "A")
        self._emit(top, net=topo.name, width=w)
        return self.g

    def _root_node(self):
        kind = self.root.kind
        if kind == "logic":
            if self.res.node is None:
                return None
            self._mode = "ast"
            binds = self.res.bindings or {}
            env = E.Env({k: (getattr(b, "width", None) or 1) for k, b in binds.items()
                         if b is not None})
            return self._ast(self.res.node, binds, env)
        if kind == "mux":
            if self.root.obj is None:
                return None
            self._mode = "struct"
            return self._mux_group(self.root.obj, self.res.expansion, 0)
        if kind == "register":
            # F0 直连寄存器专用最简模板：[REG] → [TOPOUT]，两个节点一条线，不塞任何多余图元
            binds = self.res.bindings or {}
            b = next(iter(binds.values())) if binds else None
            base = (getattr(b, "base", None) or self.root.source_name
                    or self.res.topo.name)
            return self._leaf_node(base, b, self.res.out_width, None, None)
        return None

    # ───────── 改名 / level_shift 透传块 ─────────
    def _wrap_rename(self, inner):
        obj = self.root.obj
        src = str(getattr(obj, "out_base", "") or self.root.source_name or "").strip()
        top = str(self.root.probe_name or self.res.topo.name or "").strip()
        if not src or not top or src.lower() == top.lower():
            return inner
        ls = str(getattr(obj, "_ls_name", "") or "")
        label = "LS 电平移位" if (ls and ls.lower() == top.lower()) else "改名(dft)"
        n = self.g.add_node("RENAME", label, sub="%s → %s" % (src, top),
                            ports=[{"name": "A", "side": "left", "label": ""}],
                            meta={"source": src, "probe": top})
        # ⭐ M3：进框的那根线是【改名前】的源网，不是顶层口名。改动点 A 用的是 sig.rtl_base，而
        # rtl_base 在有 _ls_name 时直接返回顶层口名（d_en_refbuf → d_en_refbuf_ls），照抄就会把
        # 改名【前后两级】画成同一根网 —— 改名块两边写着同一个名字，这块也就白画了。
        # 只在这条边上覆盖 net（不 re-emit inner：inner 可能是被 CSE 共用的叶子，改它会串味）。
        self._link(inner.id, n, "A", net=src)
        self._emit(n, net=top, width=self.res.out_width)
        return n

    # ───────── dft iddq 门 ─────────
    def _wrap_gate(self, inner):
        gate = self.res.dft_gate
        if not gate:
            return inner
        gb, transp = gate[0], gate[1]
        gbase = getattr(gb, "base", "iddq")
        expr = ("%s ? 0 : D" % gbase) if int(transp) == 0 else ("%s ? D : 0" % gbase)
        n = self.g.add_node(
            "GATE", "DFT 门", sub=expr,
            ports=[{"name": "G", "side": "top", "label": gbase},
                   {"name": "D", "side": "left", "label": "功能"}],
            meta={"gate_base": gbase, "transparent": int(transp),
                  "force_net": getattr(gb, "wire", None), "bubble": True})
        self._link(inner.id, n, "D")
        gp = self._leaf_node(gbase, gb, 1, None, None)
        self._link(gp.id, n, "G")
        top = self.root.probe_name or self.res.topo.name
        self._emit(n, net=top, width=self.res.out_width)
        return n

    # ───────── 叶子（寄存器 / 管脚） ─────────
    def _leaf_node(self, base, binding, width, msb, lsb):
        base = str(base or "?")
        key = ("leaf", base.lower(), msb, lsb)
        if key in self._memo:
            return self.g.node(self._memo[key])
        found_in = getattr(binding, "found_in", None)
        kd = getattr(binding, "kind", None)
        addr = getattr(binding, "address", None)
        rmsb, rlsb = getattr(binding, "reg_msb", None), getattr(binding, "reg_lsb", None)
        trusted = found_in in TRUSTED_FOUND_IN       # 白名单，见 TRUSTED_FOUND_IN 注
        if addr is not None:
            kind = "REG"
            bits = ("[%d:%d]" % (rmsb, rlsb)) if (rmsb is not None and rlsb is not None) else ""
            sub = "%s @0x%x%s" % (kd or "?", addr, bits)
        else:
            kind = "PIN"
            wire = getattr(binding, "wire", None) or base
            sub = "%s force %s" % (kd or "?", wire)
        w = getattr(binding, "width", None) or width or 1
        meta = {"trusted": trusted, "found_in": found_in, "reg_kind": kd, "address": addr,
                "reg_msb": rmsb, "reg_lsb": rlsb, "base": base,
                "wire": getattr(binding, "wire", None),
                "reg_name": getattr(binding, "reg_name", ""),
                "xl_letters": list(getattr(binding, "xl_letters", None) or []),
                "note": getattr(binding, "note", "")}
        if not trusted:
            # 连 binding 都没有 / 来源为空 = 这个名字在 tmm/regmap 里根本没出现过，
            # 与「查到了但要按命名约定补前缀」不是一回事，文案要分开（别让人以为只是前缀问题）
            meta["tip"] = UNKNOWN_TIP if found_in is None else UNTRUSTED_TIP
        n = self.g.add_node(kind, base + E._slice_suffix(msb, lsb), sub=sub, meta=meta)
        self._memo[key] = n.id
        return self._emit(n, net=base, width=(abs(msb - lsb) + 1) if msb is not None else w,
                          trusted=trusted)

    def _wrap_passthrough(self, src, node, base, width):
        """叶子/子树被一串【纯透传 logic 行】(out = A) 改过名 → 补一个薄改名块，两级网名都留住（M6）。

        cone._substitute 对裸变量返回的是【同一个对象】，所以改动点 A 的标签直接落在了叶子 Var 上；
        不处理的话这一级的网名（如 d_wl_rf_linectrl_freq_sel_to_mux）在图上彻底消失——而它恰恰是
        RTL 里真实存在、工程师要拿去 grep / 看波形的那个名字。RTL 里这就是一句 `assign b = a;`，
        画成一个薄块既准确又能把两个名字都摆出来。
        """
        names, seen = [], {str(base).lower()}
        chain = getattr(node, "origin_chain", None)
        if not chain:
            one = getattr(node, "origin_net", None)
            chain = [one] if one else []
        for x in chain:
            if x and str(x).lower() not in seen:
                seen.add(str(x).lower())
                names.append(str(x))
        if not names:
            return src
        _net, w0, tr = self._out.get(src.id, (None, None, True))
        n = self.g.add_node("RENAME", "透传", sub=" → ".join([str(base)] + names),
                            ports=[{"name": "A", "side": "left", "label": ""}],
                            meta={"source": str(base), "probe": names[-1],
                                  "passthrough": True, "chain": names})
        self._link(src.id, n, "A")
        return self._emit(n, net=names[-1],
                          width=getattr(node, "origin_width", None) or w0 or width,
                          trusted=tr)

    # ───────── AST 路线（logic 根 / 结构路线里的 logic 行） ─────────
    def _key(self, n):
        """CSE 键 = 结构哈希 + **这棵子树的来源网名**。

        只看结构会把「长得一样但根本是两根不同网」的子树合并掉（M2）：两个 logic 行写着同样的
        表达式、或同一个表达式被两个不同的 mux 组合成出来——合并后第二处的线会被标成第一处的
        网名，图上就出现一根【不存在的网】。origin_net 由改动点 A 挂在子树根上，正是区分它们的
        唯一依据；没有标签(None)的匿名内部节点仍按纯结构合并（那本来就是同一根线）。
        """
        struct = self._key_struct(n)
        onet = getattr(n, "origin_net", None)
        return struct if onet is None else (onet, getattr(n, "origin_group", None), struct)

    def _key_struct(self, n):
        """纯结构哈希（子节点递归走 _key，所以子树的 origin_net 也参与区分）。"""
        if isinstance(n, E.Const):
            return ("C", n.value, n.width)
        if isinstance(n, E.Var):
            return ("V", n.name, n.msb, n.lsb)
        if isinstance(n, E.Unary):
            return ("U", n.op, self._key(n.operand))
        if isinstance(n, E.Binary):
            return ("B", n.op, self._key(n.left), self._key(n.right))
        if isinstance(n, E.Ternary):
            return ("T", self._key(n.cond), self._key(n.then), self._key(n.els))
        if isinstance(n, E.Concat):
            return ("K",) + tuple(self._key(p) for p in n.parts)
        if isinstance(n, E.Repeat):
            return ("R", self._key(n.count_node), self._key(n.body))
        if isinstance(n, E.Part):
            return ("P", n.msb, n.lsb, self._key(n.operand))
        return ("?", id(n))

    @staticmethod
    def _width(node, env):
        try:
            return E.self_width(node, env)
        except Exception:      # noqa: BLE001 —— 宽度算不出就不标，绝不连累建图
            return None

    def _ast(self, node, binds, env, _d=0):
        key = (self._scope, self._key(node))
        hit = self._memo.get(key)
        if hit is not None:
            return self.g.node(hit)
        n = self._ast_new(node, binds, env, _d)
        self._memo[key] = n.id
        return n

    def _ast_new(self, node, binds, env, _d):
        # 中间层真 net 名：改动点 A 挂在【本层展开返回的子树根】上的标签
        onet = getattr(node, "origin_net", None)
        owid = getattr(node, "origin_width", None)
        w = self._width(node, env)

        def fin(n, net=None, width=None, trusted=True):
            return self._emit(n, net=(onet or net), width=(owid or width or w), trusted=trusted)

        # —— 叶子 ——
        if isinstance(node, E.Var):
            b = (binds or {}).get(node.name)
            base = getattr(b, "base", None) or node.name.lower()
            # ⚠ 这里【不】顺着 Excel 往上游钻（M1）。结构路线只在引擎自己递归的两处递归：
            # ctrl driver source=='mux'（按 recipe 展上游组）与 source=='logic'（展那一行的
            # 表达式，一层）。一个 logic 行【自己的输入】引擎是 resolve_signal_inputs 停在
            # binding 上的（discover_ctrl_paths 选 line/local 透传），我们也必须停在这儿，
            # 否则图上写的 force 目标就不是 .sv 里那根网。
            leaf = self._leaf_node(base, b, w, node.msb, node.lsb)
            return self._wrap_passthrough(leaf, node, base, w)
        if isinstance(node, E.Const):
            lab = ("%d'b%s" % (node.width, format(node.value, "0%db" % max(node.width, 1)))
                   if node.width <= 4 else "%d'h%X" % (node.width, node.value))
            return fin(self.g.add_node("CONST", lab, meta={"value": node.value,
                                                           "width": node.width}),
                       width=node.width)

        # —— MUXn 折叠：同一 sel 的 (sel&care)==cv 比较链 ——
        folded = self._fold_muxn(node)
        if folded is not None:
            sel, cases, dflt = folded
            ports = [{"name": "S", "side": "top", "label": "sel"}]
            for i, (lab, _sub) in enumerate(cases):
                ports.append({"name": "D%d" % i, "side": "left", "label": lab})
            ports.append({"name": "Dd", "side": "left", "label": "default"})
            n = self.g.add_node("MUXN", "MUX %d:1" % (len(cases) + 1),
                                sub=(onet or ""), ports=ports,
                                meta={"cases": [c[0] for c in cases]})
            self._link(self._ast(sel, binds, env, _d + 1).id, n, "S")
            for i, (lab, sub) in enumerate(cases):
                self._link(self._ast(sub, binds, env, _d + 1).id, n, "D%d" % i, label=lab)
            self._link(self._ast(dflt, binds, env, _d + 1).id, n, "Dd", label="default")
            return fin(n)

        # —— NAND / NOR / XNOR：~ 与下面的 & | ^ 合成一个带气泡的门 ——
        if isinstance(node, E.Unary) and node.op in ("~", "!") and \
                isinstance(node.operand, E.Binary) and node.operand.op in ("&", "|", "^", "&&", "||"):
            inner = node.operand
            kind = {"&": "NAND", "&&": "NAND", "|": "NOR", "||": "NOR", "^": "XOR"}[inner.op]
            lab = "XNOR" if inner.op == "^" else kind
            n = self.g.add_node(kind, lab,
                                ports=[{"name": "A", "side": "left", "label": ""},
                                       {"name": "B", "side": "left", "label": ""}],
                                meta={"bubble": True, "op": "~" + inner.op})
            self._link(self._ast(inner.left, binds, env, _d + 1).id, n, "A")
            self._link(self._ast(inner.right, binds, env, _d + 1).id, n, "B")
            return fin(n)

        if isinstance(node, E.Unary):
            if node.op in ("~", "!"):
                n = self.g.add_node("NOT", "NOT" if node.op == "~" else "!",
                                    ports=[{"name": "A", "side": "left", "label": ""}],
                                    meta={"bubble": True, "op": node.op})
            elif node.op in _REDUCE_OPS:
                n = self.g.add_node("REDUCE", "%s/" % node.op, sub="归约",
                                    ports=[{"name": "A", "side": "left", "label": ""}],
                                    meta={"op": node.op})
            else:
                n = self.g.add_node("OP", node.op,
                                    ports=[{"name": "A", "side": "left", "label": ""}],
                                    meta={"op": node.op})
            self._link(self._ast(node.operand, binds, env, _d + 1).id, n, "A")
            return fin(n)

        if isinstance(node, E.Binary):
            op = node.op
            if op in _CMP_OPS:
                # 右操作数是常量 → 直接写在块上，不为它单画一个 CONST 盒
                if isinstance(node.right, E.Const):
                    lhs, lab = node.left, "%s %s" % (op, E.to_text(node.right))
                    # synthesize_mux_expr 的 case 条件恒写成 (sel & care)==cv（mux_gen.py:③）。
                    # care 是【合成产物】不是真硬件，画成一个 AND+常量盒纯属噪音 → 收进比较块的
                    # 标签，don't-care 位用 x 显示（4'b000x），一眼读出这支覆盖哪些控制值。
                    got = self._match_case_cond(node)
                    if op == "==" and got is not None:
                        sel, cv, care, cw = got
                        if sel is not node.left:          # 确实带 care 掩码
                            lhs = sel
                            lab = "== " + _case_label(cv, care, cw)
                    n = self.g.add_node("CMP", lab,
                                        ports=[{"name": "A", "side": "left", "label": ""}],
                                        meta={"op": op, "rhs": node.right.value})
                    self._link(self._ast(lhs, binds, env, _d + 1).id, n, "A")
                    return fin(n, width=1)
                n = self.g.add_node("CMP", op,
                                    ports=[{"name": "A", "side": "left", "label": ""},
                                           {"name": "B", "side": "left", "label": ""}],
                                    meta={"op": op})
            elif op in _BIN_GATE:
                kind, lab = _BIN_GATE[op]
                n = self.g.add_node(kind, lab,
                                    ports=[{"name": "A", "side": "left", "label": ""},
                                           {"name": "B", "side": "left", "label": ""}],
                                    meta={"op": op, "bubble": op == "~^"})
            else:
                n = self.g.add_node("OP", op, sub="算子",
                                    ports=[{"name": "A", "side": "left", "label": ""},
                                           {"name": "B", "side": "left", "label": ""}],
                                    meta={"op": op})
            self._link(self._ast(node.left, binds, env, _d + 1).id, n, "A")
            self._link(self._ast(node.right, binds, env, _d + 1).id, n, "B")
            return fin(n)

        if isinstance(node, E.Ternary):
            n = self.g.add_node("MUX2", "MUX 2:1", sub=(onet or ""),
                                ports=[{"name": "S", "side": "top", "label": "sel"},
                                       {"name": "D1", "side": "left", "label": "1"},
                                       {"name": "D0", "side": "left", "label": "0"}])
            self._link(self._ast(node.cond, binds, env, _d + 1).id, n, "S")
            self._link(self._ast(node.then, binds, env, _d + 1).id, n, "D1", label="1")
            self._link(self._ast(node.els, binds, env, _d + 1).id, n, "D0", label="0")
            return fin(n)

        if isinstance(node, E.Concat):
            # ⭐ M5：位宽算不出来（叶子宽度未知 → self_width 抛错）时【不许】硬编 0 —— 那会把
            # 占位段标成 [-1:-2]、附注写「拼接 0bit」，是明明白白的错信息。宽度未知就老实说未知。
            widths = [self._width(p, env) for p in node.parts]
            known = w is not None and all(x is not None for x in widths)
            ports, hi = [], ((w or 0) - 1)
            for i, p in enumerate(node.parts):
                if known:
                    pw = widths[i] or 1
                    lab = ("[%d:%d]" % (hi, hi - pw + 1)) if pw > 1 else "[%d]" % hi
                    hi -= pw
                else:
                    lab = ""            # 位宽未知：不编造占位段，端口只留 P0/P1… 的顺序
                ports.append({"name": "P%d" % i, "side": "left", "label": lab})
            n = self.g.add_node("BUSMERGE", "{ }",
                                sub=("拼接 %dbit" % w) if known else "拼接（位宽未知）",
                                ports=ports)
            for i, p in enumerate(node.parts):
                self._link(self._ast(p, binds, env, _d + 1).id, n, "P%d" % i,
                           label=ports[i]["label"])
            return fin(n)

        if isinstance(node, E.Repeat):
            cnt = ""
            try:
                cnt = str(E.eval_const(node.count_node, env))
            except Exception:      # noqa: BLE001
                cnt = "n"
            n = self.g.add_node("BUSMERGE", "{%s{ }}" % cnt, sub="重复",
                                ports=[{"name": "A", "side": "left", "label": ""}])
            self._link(self._ast(node.body, binds, env, _d + 1).id, n, "A")
            return fin(n)

        if isinstance(node, E.Part):
            n = self.g.add_node("BUSTAP", E._slice_suffix(node.msb, node.lsb), sub="抽头",
                                ports=[{"name": "A", "side": "left", "label": ""}],
                                meta={"msb": node.msb, "lsb": node.lsb})
            child = self._ast(node.operand, binds, env, _d + 1)
            self._link(child.id, n, "A")
            # 抽头出来的仍是一根【有名字】的线：上游网名 + 位段（d_wl_rf_freq_sel[1]）。
            # 与结构路线 _maybe_tap 同口径——别让 BUSTAP 后面那截凭空变匿名（M6）。
            cnet = self._out.get(child.id, (None, None, True))[0]
            tapped = ("%s%s" % (cnet, E._slice_suffix(node.msb, node.lsb))) if cnet else None
            return fin(n, net=tapped)

        return fin(self.g.add_node("OP", type(node).__name__))

    def _fold_muxn(self, node):
        """同一 sel 的比较链 → MUXN。返回 (sel_node, [(case标签, 数据子树)…], default 子树) 或 None。

        synthesize_mux_expr 把 N 选 1 编成嵌套三元 `(sel&care)==cv ? d : (…)`（mux_gen.py:③）。
        逐层画成 MUX2 会把一个 15 路 mux 摊成 14 个二选一 + 14 条比较链（lna_lctune 239 节点），
        人读不了；折成一个 MUXN 后 case 标签还能原样带回 don't-care 位（4'b000x）。
        """
        chain, cur, selkey, sel = [], node, None, None
        while isinstance(cur, E.Ternary):
            got = self._match_case_cond(cur.cond)
            if got is None:
                break
            s, cv, care, width = got
            k = self._key(s)
            if selkey is None:
                selkey, sel = k, s
            elif k != selkey:
                break
            chain.append((_case_label(cv, care, width), cur.then))
            cur = cur.els
        if len(chain) < 2:
            return None
        return sel, chain, cur

    def _match_case_cond(self, cond):
        """cond 形如 (sel & care) == cv  或  sel == cv → (sel, cv, care, width)；否则 None。"""
        if not (isinstance(cond, E.Binary) and cond.op == "==" and isinstance(cond.right, E.Const)):
            return None
        cv, width = cond.right.value, cond.right.width
        left = cond.left
        if isinstance(left, E.Binary) and left.op == "&" and isinstance(left.right, E.Const):
            return left.left, cv, left.right.value, max(width, left.right.width)
        return left, cv, E.mask(width), width

    # ───────── 结构路线（mux 根；不依赖 synthesize_mux_expr） ─────────
    def _mux_group(self, group, expansion=None, depth=0, recipe=None):
        """recipe：本组是【上游 mux】时 mux_gen.resolve_upstream_recipe 给的配方
        （只驱动载体/备用载体 case）。None = 本组是根 mux 组，expansion 的每个 case 都被驱动。"""
        gkey = ("mux", str(group.group_no), (recipe or {}).get("prefix") or "")
        if gkey in self._memo:
            return self.g.node(self._memo[gkey])
        stack_key = "mux:" + str(group.out_base).lower()
        if stack_key in self._stack or depth > MAX_DEPTH:
            return self._cycle_stub(group.out_base, group.out_width,
                                    "级联成环/超深，回退 force 衔接网")
        self._stack.append(stack_key)
        try:
            n = self._mux_group_new(group, expansion, depth, recipe, gkey)
        finally:
            self._stack.pop()
        self._memo[gkey] = n.id
        return n

    def _mux_group_new(self, group, expansion, depth, recipe, gkey):
        if expansion is None:
            try:
                expansion = mux_gen.expand_mux_group(self.wb, self.resolver, group)
            except Exception:      # noqa: BLE001 —— 拿不到 expansion 也要画得出结构
                expansion = {}
        # ⭐ 谁来定「这个输入停在哪根网上」：一律以【引擎自己解析出来的 binding】为准，绝不自己
        # 顺着 Excel 往上游钻（M1）。mux 数据输入 expand_mux_group 根本不递归——它 force 衔接网；
        # 我们要是看见 base 撞上了同名 logic 行就钻进去，图上就会写一根 .sv 里【根本不 force】的网
        # （实证：linectrl_band_sel 撞名 logic 行 → 图写 band_sel_line、.sv force linectrl_band_sel；
        #  rx5g_en_line 同理 → 图写 rx5g_en_pin）。图必须与 res.bindings 逐个叶子对得上。
        if recipe is not None:
            # 上游 mux：只有载体(carrier)/备用载体(carrier_alt)那几支真被驱动，其余支本轮不驱动
            drivers = recipe.get("ctrl_drivers") or []
            pfx = recipe.get("prefix") or ""
            dbinds = recipe.get("bindings") or {}
        else:
            drivers = expansion.get("ctrl_drivers") or []
            pfx = ""
            dbinds = expansion.get("bindings") or {}
        shadowed = set(expansion.get("shadowed") or ())
        conflict_rows = set()
        for sc in (expansion.get("spec_conflicts") or []):
            conflict_rows.add(sc.get("later_row"))
            conflict_rows.add(sc.get("first_row"))

        ctrls = list(getattr(group, "ctrls", None) or [])
        cases = list(getattr(group, "cases", None) or [])
        driven = {i: dbinds.get(pfx + (mux_gen.DATA_KEY % i)) for i in range(len(cases))}
        undriven = [i for i in range(len(cases)) if driven[i] is None]
        ports = []
        for i, c in enumerate(ctrls):
            ports.append({"name": "S%d" % i, "side": "top", "label": c.label})
        for i, cs in enumerate(cases):
            lab = cs.case_raw
            if i in shadowed:
                lab += " ✗死分支"
            if cs.row in conflict_rows:
                lab += " ⚠conflict"
            if i in undriven:
                lab += " ·本轮不驱动"
            ports.append({"name": "D%d" % i, "side": "left", "label": lab})
        sel = ("{%s}" % ",".join(c.label for c in ctrls)) if len(ctrls) > 1 else \
              (ctrls[0].label if ctrls else "?")
        sub = "case(%s)" % sel
        if undriven:
            sub += "　级联载体：%s" % "、".join(cases[i].case_raw for i in range(len(cases))
                                             if i not in undriven)
        n = self.g.add_node(
            "MUXN", "MUX %d:1  (mux%s)" % (len(cases), group.group_no),
            sub=sub, ports=ports,
            meta={"group": group.group_no, "out_base": group.out_base,
                  "cases": [c.case_raw for c in cases],
                  "case_rows": [c.row for c in cases],
                  "shadowed": sorted(shadowed), "conflict_rows": sorted(conflict_rows),
                  "undriven_cases": undriven, "is_upstream": recipe is not None,
                  "ctrl_total_width": group.ctrl_total_width})
        self._memo[gkey] = n.id                     # 先登记，容忍自引用扇入

        for i, c in enumerate(ctrls):
            drv = drivers[i] if i < len(drivers) else None
            src = self._ctrl_source(c, drv, depth)
            self._link(src.id, n, "S%d" % i, label=c.label)
        for i, cs in enumerate(cases):
            if i in undriven:
                # 本轮没有任何激励走这一支（上游 mux 只经载体 case 驱动）→ 端口留着让 mux 的
                # 真实路数/case 值完整可读，但不画一个 .sv 根本不碰的叶子（那才是骗人）
                continue
            src = self._leaf_node(cs.input_base, driven[i], cs.input_width,
                                  cs.input_msb, cs.input_lsb)
            self._link(src.id, n, "D%d" % i, label=ports[len(ctrls) + i]["label"])

        return self._emit(n, net=(getattr(group, "rtl_base", None) or group.out_base),
                          width=group.out_width)

    def _ctrl_source(self, ctrl, driver, depth):
        """一个 mux 控制信号 → 上游图元。三来源，与 mux_gen._resolve_ctrl_driver 逐条对齐：
        source='mux' → 按【它自己的 recipe】递归上游组；source='logic' → 展开那一行的表达式
        （只展一层，引擎也只展一层）；其余 → 停在引擎解析出的 binding 上画叶子。"""
        source = (driver or {}).get("source")
        if source == "mux" and (driver or {}).get("upstream") is not None:
            up = self._mux_group(driver["upstream"], None, depth + 1,
                                 recipe=(driver or {}).get("recipe"))
            return self._maybe_tap(up, ctrl.msb, ctrl.lsb, driver["upstream"].out_width)
        if source == "logic" and (driver or {}).get("ctrl_sig") is not None:
            lg = self._logic_row(driver["ctrl_sig"], depth + 1)
            return self._maybe_tap(lg, ctrl.msb, ctrl.lsb,
                                   driver["ctrl_sig"].out_width)
        b = (driver or {}).get("binding")
        if b is None:
            b = self._resolve_leaf(ctrl.raw, ctrl.base, ctrl.width, ctrl.msb, ctrl.lsb)
        return self._leaf_node(ctrl.base, b, ctrl.width, ctrl.msb, ctrl.lsb)

    def _logic_row(self, sig, depth):
        """一个 logic 行 → 它自己表达式的门图（Var 叶子继续按结构递归到 logic/mux/寄存器）。

        结构路线里不用 cone.expand：cone 会把上游整棵代入、抹掉中间网名，而这里恰恰要保住
        「每一级 logic 行输出叫什么网」——这是图上最值钱的信息。
        """
        lkey = ("logic", str(sig.out_base).lower())
        if lkey in self._memo:
            return self.g.node(self._memo[lkey])
        stack_key = str(sig.out_base).lower()
        if stack_key in self._stack or depth > MAX_DEPTH:
            return self._cycle_stub(sig.out_base, sig.out_width, "内部信号成环/超深")
        try:
            node = E.parse(sig.expr)
        except E.ExprError:
            return self._cycle_stub(sig.out_base, sig.out_width,
                                    "表达式解析失败：%s" % sig.expr)
        binds = self.resolver.resolve_signal_inputs(sig)
        env = E.Env({k: (getattr(b, "width", None) or 1) for k, b in binds.items()
                     if b is not None})
        self._stack.append(stack_key)
        outer_scope, self._scope = self._scope, stack_key
        try:
            n = self._ast(node, binds, env, depth)
        finally:
            self._scope = outer_scope
            self._stack.pop()
        rtl = getattr(sig, "rtl_base", None) or sig.out_base
        if n.kind in ("REG", "PIN"):
            # 纯透传行（out = A）：AST 根就是那个叶子盒，而叶子盒可能被别处 CSE 共用 → 不能就地
            # 改它的网名。补一个薄改名块把这一级的真名留住（M6，与 AST 路线的 _wrap_passthrough 同理）。
            _net, w0, tr = self._out.get(n.id, (None, None, True))
            src_name = str(n.meta.get("base") or n.label)
            if str(rtl).lower() != src_name.lower():
                rn = self.g.add_node("RENAME", "透传", sub="%s → %s" % (src_name, rtl),
                                     ports=[{"name": "A", "side": "left", "label": ""}],
                                     meta={"source": src_name, "probe": rtl,
                                           "passthrough": True, "row": sig.out_base})
                self._link(n.id, rn, "A")
                self._emit(rn, net=rtl, width=sig.out_width, trusted=tr)
                n = rn
        else:
            # 这一行的输出线 = 它的 RTL 网名（origin_net 在结构路线上的对等物）
            self._emit(n, net=rtl, width=sig.out_width,
                       trusted=self._out.get(n.id, (None, None, True))[2])
        self._memo[lkey] = n.id
        return n

    def _maybe_tap(self, src, msb, lsb, full_width):
        """引用上游输出的一个位段 → 插一个 BUSTAP 抽头（非零偏移一眼可见，如 temp_code[3:1]）。"""
        if msb is None:
            return src
        if lsb in (None, 0) and (full_width or 0) and msb == (full_width - 1):
            return src                              # 全宽引用，不必抽头
        net, _w, tr = self._out.get(src.id, (None, None, True))
        n = self.g.add_node("BUSTAP", E._slice_suffix(msb, lsb), sub="抽头",
                            ports=[{"name": "A", "side": "left", "label": ""}],
                            meta={"msb": msb, "lsb": lsb})
        self._link(src.id, n, "A")
        return self._emit(n, net=(("%s%s" % (net, E._slice_suffix(msb, lsb))) if net else None),
                          width=abs(msb - (lsb if lsb is not None else msb)) + 1, trusted=tr)

    def _cycle_stub(self, base, width, why):
        n = self.g.add_node("PIN", str(base), sub="⚠ %s" % why,
                            meta={"trusted": False, "tip": why, "base": base})
        return self._emit(n, net=str(base), width=width, trusted=False)

    def _resolve_leaf(self, raw, base, width, msb, lsb):
        try:
            return self.resolver.resolve("sigflow_" + str(base).lower(),
                                         {"raw": raw, "base": base, "width": width,
                                          "msb": msb, "lsb": lsb})
        except Exception:      # noqa: BLE001
            return None


def _case_label(cv, care, width):
    """case 值 → 带 don't-care 位的字面量（4'b000x）。care 位为 0 的位显示成 x。"""
    bits = []
    for i in range(width - 1, -1, -1):
        bits.append("x" if not ((care >> i) & 1) else str((cv >> i) & 1))
    return "%d'b%s" % (width, "".join(bits))


# ───────────────────────────── SVG 渲染 ─────────────────────────────
# ⭐ 五色工程图配色。**与 dreg_verify/ui/theme.py 的 FLOW_* 同值**（此处是复制，不是 import：
# sigflow 是 Qt-free 纯函数模块，CLI / HTML 报告要能在没有 ui 包的环境里跑，绝不能依赖 GUI 层）。
# 两边不许漂：test_sigflow_c0.py::test_flow_colors_match_ui_theme 逐个常量断言相等。
FLOW_INK = "#1f2933"          # 主线 / 主框 / 主标
FLOW_MUTE = "#5d6773"         # 副标 / 端口标 / 图例
FLOW_HL = "#1d4f9c"           # 当前选中线网
FLOW_AMB = "#a8710f"          # 猜名（名字来自命名约定）
FLOW_BAD = "#b3302a"          # 规格冲突 / 死分支
FLOW_REG_BAR = "#93a1b0"      # 寄存器盒左侧 5px 色条（高亮时 FLOW_HL）
FLOW_TOP_BG = "#e8f0fb"       # TOP 端子底
FLOW_GUESS_BG = "#fffbf3"     # 猜名盒底
FLOW_HL_BG = "#eef4fd"        # 选中盒底
FLOW_WIRE_W = 1.2
FLOW_BUS_W = 2.4
FLOW_HL_W = 2.2
FLOW_DASH_BAD = "6 3"
FLOW_DASH_GHOST = "5 3"

_FILL = {"REG": "#eef2ff", "PIN": "#f8fafc", "AND": "#dbeafe", "NAND": "#dbeafe",
         "OR": "#dcfce7", "NOR": "#dcfce7", "XOR": "#e0f2fe", "NOT": "#fee2e2",
         "MUX2": "#fef3c7", "MUXN": "#fef3c7", "GATE": "#fce7f3", "REDUCE": "#ede9fe",
         "CMP": "#ffe4e6", "OP": "#f1f5f9", "BUSTAP": "#ede9fe", "BUSMERGE": "#ede9fe",
         "RENAME": "#f0fdfa", "CONST": "#e5e7eb", "TOPOUT": "#111827"}
_STROKE = "#334155"
_WIRE = "#475569"
_NETC = "#0f766e"
_FONT = "Consolas,Menlo,'DejaVu Sans Mono',monospace"
_SANS = "'Segoe UI',Arial,'Noto Sans CJK SC','Microsoft YaHei',sans-serif"

_CH = 6.6          # 等宽字体 11px 的字符宽度估计
_CH9 = 5.4         # 同上，9px（net 名用这一号字）
_HGAP = 74         # 列间距下限（折点 + net 名都写在这段缝隙里）
_HGAP_MAX = 230    # 列间距上限：再长的 net 名就截断 + <title> 兜全名，别把画布拉爆
_VGAP = 18
_PORT_H = 15       # MUX 每个数据口占的高度


def _esc(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;"))


def _disp_w(s):
    """显示宽度：CJK/全角按 2 个字符宽算（否则中文附注会顶出框）。"""
    n = 0
    for ch in str(s):
        n += 2 if ord(ch) > 0x2E80 else 1
    return n


def _head_h(node):
    """框顶给主标/副标留的高度。**只有左侧端口带文字时才需要让位**（m1②）：否则 MUX 的
    case 标签(y≈y0+15) 必然压在主标(y0+14)/副标(y0+27)上，两行字叠成一团谁都读不出。
    端口没文字的门（AND/OR/NOT 的输入）不留，免得图白白变高。"""
    if not any(p["side"] == "left" and p["label"] for p in node.ports):
        return 0
    return 36 if node.sub else 22          # 主标基线 y0+17、副标 y0+31，各留 ~5px 下缘


def _left_band(node, y0, h):
    """左侧端口可用的竖直区间 [top, bottom]（锚点与端口文字共用同一份，绝不各算各的）。"""
    return y0 + _head_h(node), y0 + h


def _left_port_y(node, y0, h, i, n):
    top, bot = _left_band(node, y0, h)
    return top + (bot - top) * (i + 0.5) / max(n, 1)


def _box_size(node):
    labw = max(_disp_w(node.label), _disp_w(node.sub))
    plabw = max([_disp_w(p["label"]) for p in node.ports if p["side"] == "left"] or [0])
    w = max(96, int(max(labw, plabw) * _CH) + 22)
    nleft = len([p for p in node.ports if p["side"] == "left"])
    h = 40 if node.sub else 30
    if nleft:
        h = max(h, _head_h(node) + nleft * _PORT_H + 6)
    if node.kind == "TOPOUT":
        w = max(w, 120)
    return w, h


@dataclass
class Layout:
    """`layout_graph` 的产出：Qt-free 纯数据，坐标与 `render_svg` 画出来的 SVG **完全一致**。

    GUI（架构 §1.3② 路线 C）拿它铺命中层：每条 `edges` 一个透明 `QGraphicsPathItem`、每个
    `pos` 一个透明 `QGraphicsRectItem`，所以这里的坐标一旦与 SVG 漂了，点选就会点空。

    pos    — {nid: (x, y, w, h)}，只含图里的真节点（长边的 dummy 折点已经烘进 edges 的点列）
    edges  — [(edge, [(x, y)…], side)]，side ∈ {"left","top"} = 这条线从哪一侧进目标口
    size   — (总宽, 总高) = SVG 的 width/height/viewBox
    gapw   — {列号: 列缝宽}，net 名就写在源框右缘的这条缝里（截断按它算）
    rank   — {nid: 列号}
    ports  — {(nid, port): (x, y)} 输入口锚点；输出口记在 (nid, "Y")
    """

    pos: dict = field(default_factory=dict)
    edges: list = field(default_factory=list)
    size: tuple = (0, 0)
    gapw: dict = field(default_factory=dict)
    rank: dict = field(default_factory=dict)
    ports: dict = field(default_factory=dict)


def layout_graph(graph):
    """Graph → Layout（分层 / 列序 / dummy / 坐标 / 通道 / 锚点 / 折线点列）。

    纯函数、无 Qt、无字符串拼接：`render_svg` 与 GUI 命中层共用同一份坐标，绝不各算各的。
    最长路径分层 → 列内 DFS 到达序 → 跨 ≥2 列的长边插 dummy、折点走列间隙 → 三段正交折线。
    """
    if not graph.nodes:
        return Layout(size=(420, 70))

    nodes = graph.nodes
    idx = {n.id: n for n in nodes}
    fanin = {}
    for e in graph.edges:
        fanin.setdefault(e.dst, []).append(e)

    # ① 最长路径分层
    rank, seen = {}, set()

    def _rank(nid):
        if nid in rank:
            return rank[nid]
        if nid in seen:                       # 防御：图里不该有环，真有也不死循环
            return 0
        seen.add(nid)
        ins = fanin.get(nid) or []
        rank[nid] = 0 if not ins else 1 + max(_rank(e.src) for e in ins)
        return rank[nid]
    for n in nodes:
        _rank(n.id)
    maxr = max(rank.values()) if rank else 0

    # ② 列内顺序：从输出侧 DFS 的到达序（同一支相邻，稳定可复现）
    sinks = [n.id for n in nodes if not any(e.src == n.id for e in graph.edges)]
    order, ov = [], set()

    def _dfs(nid):
        if nid in ov:
            return
        ov.add(nid)
        order.append(nid)
        for e in (fanin.get(nid) or []):
            _dfs(e.src)
    for s in sinks:
        _dfs(s)
    for n in nodes:                            # 兜底：孤立节点也要有位置
        _dfs(n.id)

    cols = {}
    for nid in order:
        cols.setdefault(rank[nid], []).append(nid)

    # ③ 长边插 dummy（跨 ≥2 列 → 每个中间列一个折点，线走列间隙不压框）
    size = {n.id: _box_size(n) for n in nodes}
    dummies = {}                               # did → (rank,)
    routes = {}                                # edge → [dummy id…]
    dcount = [0]
    for e in graph.edges:
        span = rank[e.dst] - rank[e.src]
        if span <= 1:
            continue
        chain = []
        pos = cols[rank[e.src]].index(e.src) + 1
        for r in range(rank[e.src] + 1, rank[e.dst]):
            dcount[0] += 1
            did = "_d%d" % dcount[0]
            dummies[did] = r
            size[did] = (2, 2)
            rank[did] = r
            cols.setdefault(r, []).insert(min(pos, len(cols[r])), did)
            chain.append(did)
        routes[id(e)] = chain

    # ④ 坐标：列宽 = 该列最宽的框；列内自上而下堆叠
    colw = {r: max((size[i][0] for i in cols.get(r, [])), default=0) for r in range(maxr + 1)}
    # ⭐ m1①：列缝宽度按【本缝里要写的最长 net 名】算。net 名画在源框右缘、就写在这条缝里，
    # 固定 74px 只放得下 13 个字符，而真实网名动辄 25+ → 尾巴被下一列的框盖住，「配上各线
    # net 名称」这个核心需求当场废掉一半。上限 _HGAP_MAX 兜住，避免超长名把画布拉爆。
    gap_need = {}
    for e in graph.edges:
        if not e.net:
            continue
        txt = e.net + (("[%d:0]" % (e.width - 1)) if (e.width or 1) > 1 else "")
        r = rank[e.src]
        gap_need[r] = max(gap_need.get(r, 0), _disp_w(txt) + (2 if not e.trusted else 0))
    gapw = {r: int(max(_HGAP, min(_HGAP_MAX, gap_need.get(r, 0) * _CH9 + 16)))
            for r in range(maxr + 1)}
    xs, x = {}, 24
    for r in range(maxr + 1):
        xs[r] = x
        x += colw[r] + gapw[r]
    total_w = x - gapw.get(maxr, _HGAP) + 24 + 24

    pos = {}
    ytop = 46
    total_h = 0
    for r in range(maxr + 1):
        y = ytop
        for nid in cols.get(r, []):
            w, h = size[nid]
            pos[nid] = (xs[r], y, w, h)
            n = idx.get(nid)
            # 不可信叶子的「※ 名字来自命名约定」角标画在框下方 → 给它留一行
            y += h + _VGAP + (12 if (n is not None and n.meta.get("trusted") is False
                                     and n.meta.get("tip")) else 0)
        total_h = max(total_h, y)
    total_h += 24

    # 列间缝隙里的【走线通道】：同一条缝里多条边各占一条竖直通道，否则全挤在中点糊成一根粗棍
    _NCH = 6
    gap_use = {}

    def channel(gap_r, left_x, right_x):
        k = gap_use.get(gap_r, 0)
        gap_use[gap_r] = k + 1
        span = max(right_x - left_x, 16)
        step = max(6.0, (span - 14.0) / _NCH)
        return left_x + 8 + (k % _NCH) * step

    # ⑤ 端口锚点
    def anchor_out(nid):
        x0, y0, w, h = pos[nid]
        return (x0 + w, y0 + h / 2.0)

    def anchor_in(nid, port):
        x0, y0, w, h = pos[nid]
        n = idx.get(nid)
        if n is None:
            return (x0, y0 + h / 2.0, "left")
        left = [p["name"] for p in n.ports if p["side"] == "left"]
        top = [p["name"] for p in n.ports if p["side"] == "top"]
        if port in top:
            i = top.index(port)
            return (x0 + w * (i + 1.0) / (len(top) + 1.0), y0, "top")
        if port in left:
            # 与框内端口文字共用 _left_port_y（顶部给主标/副标让位，见 _head_h）
            return (x0, _left_port_y(n, y0, h, left.index(port), len(left)), "left")
        return (x0, y0 + h / 2.0, "left")

    ports_xy = {}
    for n in nodes:
        bx, by, bw, bh = pos[n.id]
        ports_xy[(n.id, "Y")] = (bx + bw, by + bh / 2.0)
        for p in n.ports:
            ax, ay, _s = anchor_in(n.id, p["name"])
            ports_xy[(n.id, p["name"])] = (ax, ay)

    # ⑥ 连线折点（横段走 dummy 自己占的那一行、竖段走列缝通道 → 长线不横穿图元）
    lay_edges = []
    for e in graph.edges:
        pts = []
        x1, y1 = anchor_out(e.src)
        pts.append((x1, y1))
        prev_x = x1
        # 跨 ≥2 列的长边：每个中间列一个 dummy 折点，横段走 dummy 自己占的那一行（列内已给它留槽，
        # 不会压到别的框上），竖段走列间缝隙的通道 → 长线不横穿图元
        for did in routes.get(id(e), []):
            r = rank[did]
            dy = pos[did][1]
            xm = channel(r - 1, prev_x, xs[r])
            pts += [(xm, y1), (xm, dy), (xs[r] + colw[r], dy)]
            prev_x, y1 = xs[r] + colw[r], dy
        px, py, side = anchor_in(e.dst, e.dst_port)
        xm = channel(rank[e.dst] - 1, prev_x, px)
        if side == "top":
            yup = py - 16
            pts += [(xm, y1), (xm, yup), (px, yup), (px, py)]
        else:
            pts += [(xm, y1), (xm, py), (px, py)]
        ports_xy.setdefault((e.dst, e.dst_port), (px, py))
        lay_edges.append((e, pts, side))

    return Layout(pos={n.id: pos[n.id] for n in nodes}, edges=lay_edges,
                  size=(total_w, total_h), gapw=gapw,
                  rank={n.id: rank[n.id] for n in nodes}, ports=ports_xy)


def _hl_key(net):
    """高亮比对键：网名一律小写去空白（Excel 里同一根网大小写并不统一，别让选中漏命中）。"""
    s = str(net or "").strip().lower()
    return s or None


def render_svg(graph, title=None, layout=None, highlight_net=None):
    """Graph → SVG 文本（源在左、Topout 在右）。三处复用：GUI / HTML 报告内联 / ppt 转 PNG。

    布局全部来自 `layout_graph`（传 layout 可复用已算好的一份，GUI 的命中层就靠它对齐）。
    标注：每条线的 net 名 + [msb:lsb]（1bit 不标）；宽总线粗线；REG 盒两行；不可信 PIN 虚线 + 角标。
    每个图元和每条线都挂 data-net（点选高亮同名网直接用）。

    highlight_net — 当前选中的线网名（小写比对）。命中的线与盒套 `hl` 样式（HL 蓝、线加粗到
    2.2px、盒 2px 边框 + 浅蓝底、REG 左色条转蓝），并额外挂 `data-hl="1"` 方便测试与二次加工。
    GUI 每次选中变化就带新的 highlight_net 重渲一遍（≤60 节点 ≈ 2ms，架构 §1.3② 路线 C）。
    """
    title = title or graph.title
    if not graph.nodes:
        return _empty_svg(title)

    hl_key = _hl_key(highlight_net)
    lay = layout if layout is not None else layout_graph(graph)
    pos, gapw, rank = lay.pos, lay.gapw, lay.rank
    total_w, total_h = lay.size
    idx = {n.id: n for n in graph.nodes}

    out = ['<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" viewBox="0 0 %d %d" '
           'font-family="%s" font-size="11">' % (total_w, total_h, total_w, total_h, _FONT),
           '<rect x="0" y="0" width="%d" height="%d" fill="#ffffff"/>' % (total_w, total_h),
           '<text x="16" y="24" font-size="14" font-weight="bold" font-family="%s" '
           'fill="#0f172a">%s</text>' % (_SANS, _esc(title or "")),
           '<g id="wires">']

    # ⑥ 连线
    for e, pts, side in lay.edges:
        x1, y1 = pts[0]
        px, py = pts[-1]
        ehl = hl_key is not None and _hl_key(e.net) == hl_key
        sw = 2.6 if (e.width or 1) > 1 else 1.2
        dash = ' stroke-dasharray="5,3"' if not e.trusted else ""
        color = "#b45309" if not e.trusted else _WIRE
        if ehl:                       # hl：选中的那根网压过虚线/粗细，一眼从图里挑出来
            color, sw = FLOW_HL, max(sw, FLOW_HL_W)
        out.append('<polyline points="%s" fill="none" stroke="%s" stroke-width="%.1f"%s '
                   'data-net="%s"%s/>'
                   % (" ".join("%.0f,%.0f" % p for p in pts), color, sw, dash,
                      _esc(e.net or ""), ' data-hl="1"' if ehl else ""))
        # 箭头（左进 = 朝右的三角；顶进 = 朝下的三角）
        if side == "top":
            out.append('<polygon points="%.0f,%.0f %.0f,%.0f %.0f,%.0f" fill="%s"/>'
                       % (px, py, px - 3.2, py - 7, px + 3.2, py - 7, color))
        else:
            out.append('<polygon points="%.0f,%.0f %.0f,%.0f %.0f,%.0f" fill="%s"/>'
                       % (px, py, px - 7, py - 3.2, px - 7, py + 3.2, color))
        # net 名 + 位宽：写在源框右缘的那条列缝里，按缝宽截断，<title> 兜全名（m1①）
        lbl = e.net or ""
        if lbl:
            # 网名本身已带位段（抽头出来的 a[1:0]）时不要再补一遍位宽，否则出 a[1:0][1:0]
            if (e.width or 1) > 1 and not lbl.endswith("]"):
                lbl += "[%d:0]" % (e.width - 1)
            if not e.trusted:
                lbl += " ※"
            room = gapw.get(rank[e.src], _HGAP) - 10
            shown = _clip9(lbl, room)
            out.append('<text x="%.0f" y="%.0f" font-size="9" fill="%s" data-net="%s">'
                       '<title>%s</title>%s</text>'
                       % (x1 + 5, pts[0][1] - 4,
                          FLOW_HL if ehl else ("#b45309" if not e.trusted else _NETC),
                          _esc(e.net or ""), _esc(lbl), _esc(shown)))
        # 支路标签（MUXN 的 case 值等）画在目标口外侧；与框内的端口标重复时不再画一遍（去噪）
        dstn = idx.get(e.dst)
        dup = dstn is not None and any(p["name"] == e.dst_port and p["label"] == e.label
                                       for p in dstn.ports)
        if e.label and not dup:
            out.append('<text x="%.0f" y="%.0f" font-size="8.5" fill="#b91c1c" '
                       'text-anchor="end">%s</text>' % (px - 9, py + 3, _esc(e.label)))
    out.append("</g><g id=\"cells\">")

    # ⑦ 图元
    for n in graph.nodes:
        x0, y0, w, h = pos[n.id]
        fill = _FILL.get(n.kind, "#f8fafc")
        untrusted = (n.meta.get("trusted") is False)
        dash = ' stroke-dasharray="5,3"' if untrusted else ""
        stroke = "#b45309" if untrusted else _STROKE
        self_net = (n.meta.get("base") or n.meta.get("out_base") or n.label)
        nhl = hl_key is not None and _hl_key(self_net) == hl_key
        out.append('<g data-node="%s" data-kind="%s" data-net="%s"%s>'
                   % (n.id, n.kind, _esc(self_net), ' data-hl="1"' if nhl else ""))
        if nhl:                       # hl 盒：HL 2px 边框 + 浅蓝底（Design §6.1「选中高亮」）
            fill, stroke = FLOW_HL_BG, FLOW_HL
        out.append('<rect x="%d" y="%d" width="%d" height="%d" rx="5" fill="%s" stroke="%s" '
                   'stroke-width="%s"%s/>'
                   % (x0, y0, w, h, fill, stroke, "2" if nhl else "1.2", dash))
        tc = "#ffffff" if n.kind == "TOPOUT" else "#0f172a"
        # 有 sub 或有带文字的端口 → 主标钉在框顶那条 header 带里（否则居中的主标会插在
        # 端口标签中间，两行字叠一起，m1②）；两者都没有才居中
        main_y = y0 + 17 if (n.sub or _head_h(n)) else y0 + h / 2 + 4
        out.append('<text x="%d" y="%.0f" font-size="11" fill="%s">%s</text>'
                   % (x0 + 8, main_y, tc, _esc(_clip(n.label, w))))
        if n.sub:
            out.append('<text x="%d" y="%d" font-size="8.5" fill="%s">%s</text>'
                       % (x0 + 8, y0 + 31, "#cbd5e1" if n.kind == "TOPOUT" else "#64748b",
                          _esc(_clip(n.sub, w))))
        if n.meta.get("bubble"):
            out.append('<circle cx="%d" cy="%.0f" r="3.4" fill="#ffffff" stroke="%s"/>'
                       % (x0 + w + 3, y0 + h / 2.0, stroke))
        # 端口标（MUX 数据口的 case 标签画在框内左侧）——与锚点共用 _left_port_y，
        # 顶部已由 _head_h 给主标/副标让出一条，不会再叠在一起（m1②）
        left = [p for p in n.ports if p["side"] == "left"]
        for i, p in enumerate(left):
            if not p["label"]:
                continue
            py = _left_port_y(n, y0, h, i, len(left))
            out.append('<text x="%d" y="%.0f" font-size="8" fill="#475569">'
                       '<title>%s</title>%s</text>'
                       % (x0 + 6, py + 3, _esc(p["label"]), _esc(_clip(p["label"], w - 12))))
        if untrusted and n.meta.get("tip"):
            out.append('<text x="%d" y="%d" font-size="8" fill="#b45309">※ %s</text>'
                       % (x0, y0 + h + 10, _esc(n.meta["tip"])))
        out.append("</g>")
    out.append("</g></svg>")
    return "\n".join(out)


def _clip9(s, roomw):
    """9px 小字按【可用像素宽】截断（net 名写在列缝里，缝有多宽就写多少）。"""
    return _clip_to(s, max(4, int(roomw / _CH9)))


def _clip(s, boxw):
    return _clip_to(s, max(4, int((boxw - 14) / _CH)))


def _clip_to(s, lim):
    s = str(s)
    if _disp_w(s) <= lim:
        return s
    acc, n = [], 0
    for ch in s:
        cw = 2 if ord(ch) > 0x2E80 else 1
        if n + cw > lim - 1:
            break
        acc.append(ch)
        n += cw
    return "".join(acc) + "…"


def _empty_svg(title):
    return ('<svg xmlns="http://www.w3.org/2000/svg" width="420" height="70" '
            'viewBox="0 0 420 70" font-family="%s" font-size="11">'
            '<rect x="0" y="0" width="420" height="70" fill="#ffffff"/>'
            '<text x="16" y="26" font-size="13" fill="#0f172a">%s</text>'
            '<text x="16" y="46" font-size="11" fill="#64748b">该信号没有可画的结构'
            '（未解析 / RO 回读）</text></svg>' % (_FONT, _esc(title or "")))


def attach_report_svgs(wb, rep, probe_prefixes=None, resolver=None,
                       mode="min", max_tests=256, exhaustive=False, only=None):
    """给 topout_report 的 rep['tables'] 逐表挂一张内联 SVG（键 `sigflow_svg`）。返回挂上的张数。

    HTML 报告的 ② 真值表 tab 每个信号一块，图就贴在展开链下面、真值表上面——「这张表在验的到底
    是个什么电路」当场看得见。**只影响报告 HTML，不碰 .sv/向量/账目**（.sv 的 6 个 sha 有专门测试守着）。

    永不抛：任何一个信号建图/渲染失败都只是那一张图没有，报告照出。
    """
    from . import resolver as R          # 惰性 import：避免与 topout 的相互 import 打架
    from . import topout as T

    tabs = (rep or {}).get("tables") or []
    if not tabs:
        return 0
    res = resolver if resolver is not None else R.Resolver(wb, wire_prefixes=probe_prefixes)
    by_name = {str(t.name).lower(): t for t in (getattr(wb, "topout", None) or [])}
    only_low = {str(n).lower() for n in only} if only is not None else None
    cache, n = {}, 0
    for tb in tabs:
        nm = str(tb.get("topout_name") or tb.get("signal") or "").lower()
        if not nm or (only_low is not None and nm not in only_low):
            continue
        topo = by_name.get(nm)
        if topo is None:
            continue
        if nm not in cache:
            cache[nm] = ""
            try:
                r = T.analyze_signal(wb, res, topo, mode=mode, max_tests=max_tests,
                                     exhaustive=exhaustive, want_graph=True)
                if r.graph is not None:
                    cache[nm] = render_svg(r.graph)
            except Exception:      # noqa: BLE001 —— 一张图失败不连累整份报告
                cache[nm] = ""
        if cache[nm]:
            tb["sigflow_svg"] = cache[nm]
            n += 1
    return n


def render_png(svg_text, path, scale=1.0):
    """SVG 文本 → PNG 文件（ppt / 邮件贴图用）。**惰性 import PySide6**——本模块的其余部分
    必须在没有 Qt 的环境里也能跑（CLI / 报告生成）。返回写出的路径。"""
    from PySide6.QtCore import QByteArray            # noqa: PLC0415  惰性
    from PySide6.QtGui import QImage, QPainter       # noqa: PLC0415
    from PySide6.QtSvg import QSvgRenderer           # noqa: PLC0415

    r = QSvgRenderer(QByteArray(svg_text.encode("utf-8")))
    if not r.isValid():
        raise ValueError("SVG 无法被 QSvgRenderer 解析")
    sz = r.defaultSize()
    img = QImage(max(1, int(sz.width() * scale)), max(1, int(sz.height() * scale)),
                 QImage.Format_ARGB32)
    img.fill(0xFFFFFFFF)
    p = QPainter(img)
    try:
        r.render(p)
    finally:
        p.end()
    img.save(str(path), "PNG")
    return str(path)
