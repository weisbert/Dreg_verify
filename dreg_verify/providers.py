# -*- coding: utf-8 -*-
"""providers.py — GUI 的【数据源层】：Topout 视图与四个页视图对引擎的唯一入口（**Qt-free**）。

为什么单独一层（GUI v2「换包不换引擎」的最后一块地基）：
  现在这套界面(gui.py)里的 `_TopoutProvider` / `_PageProvider` 是两个内嵌在窗口对象上的小类，
  拿 `self.main._probe_prefixes` 这类控件旁的属性当配置源 —— v2 的 state/worker 用不了它们。
  本模块把这两个类**逐方法搬出来**，配置来源改成一个朴素的 `cfg` 对象（ConfigSourceProto）。
  搬出来之后两套门面跑的是同一份数据源：口径不可能再分叉，而这正是最难发现的一类漂移
  （.sv 字节闸门守的是 topout 那条路，两个不同入口各跑各的，它守不住）。

cfg 要提供什么（`ui/contracts.ConfigSourceProto`，v2 的 `WorkbenchState` 满足）：
    wb                 已载入的工作簿
    probe_prefixes     {网名: 层级前缀}      —— 与『排查(旧)』/全局编辑器同一份
    force_signals      {强制 force 的基名}   —— 兼容别名 force_overrides
    logic_overrides    {基名: RTL 补充逻辑 spec}
    include_risky      缺前缀的输入放不放行（C-217，默认 True）
    engine_lock        可选：后台 worker 与主线程共用同一个 wb 时的互斥（缺省 = 不上锁）

本模块【不】import PySide6、不 import gui：
  · gui.py 里那两个 provider **一字不动**（它们随 legacy 门面在 C5 一起退役），
    `tests/test_providers.py` 的等价性测试是这次搬家的证明。
  · 引擎侧分批落地中的新形参（C0-a 的 block_suffix / progress / should_cancel / lite /
    skeleton），用 `_accepts` 探一下再传：给引擎一个它还不认识的参数 = TypeError 炸在
    分析或导出中途，那是最难查的一类失败。C0-a 合并后这些条件自然恒真。

⚠ 锁的粒度：本层是**每次引擎调用**一把锁（架构 §2.2 想要的是 view_models 里**逐信号**的粒度，
  好让主线程在信号之间插进来点当前信号）。逐信号粒度要引擎的循环自己配合（analyze_all 的
  progress/should_cancel 边界），provider 从外面包不进去 —— 留给 C1-a 的 worker 一并处理。
"""

import contextlib
import inspect

from . import analysis_norm as AN
from . import edits as ED
from . import generator
from . import resolver as R

__all__ = ["TopoutProvider", "PageProvider", "TOPOUT_KIND_LABEL", "PAGE_KIND_LABEL"]

# 「分类」= 根来自哪种页（logic/mux/寄存器…）。与 gui.TOPO_KIND_LABEL 同值；
# v2 的视图层另有 ui/terms.KIND_LABELS（界面文案归 terms，本层只是把值带给老门面用）。
TOPOUT_KIND_LABEL = {"logic": "选路/logic", "mux": "mux", "register": "直连寄存器",
                     "ro-readback": "RO回读(跳过)", "unresolved": "未解析"}
PAGE_KIND_LABEL = {"logic": "logic", "mux": "mux"}

TOPOUT_TITLE = "要验信号 = Topout 页 B 列（顶层真名，cone 展到源寄存器，断言贴真名）"
TOPOUT_EMPTY_HINT = ("⚠ 当前 Excel 没有 Topout 页（B 列要验信号清单为空）。可切到其它视图(logic/mux/dft)"
                     "或『排查(旧)』。")


def _accepts(fn, name):
    """fn 收不收得住这个关键字参数（收 **kwargs 也算）。拿不到签名一律当「收不住」。"""
    if fn is None:
        return False
    try:
        sig = inspect.signature(fn)
    except (TypeError, ValueError):  # noqa: BLE001
        return False
    for p in sig.parameters.values():
        if p.kind is inspect.Parameter.VAR_KEYWORD:
            return True
        if p.name == name and p.kind in (inspect.Parameter.POSITIONAL_OR_KEYWORD,
                                         inspect.Parameter.KEYWORD_ONLY):
            return True
    return False


def _opt(fn, **kw):
    """把 fn 收得住的那些关键字挑出来（值为 None/省略语义的不筛，调用方自己决定传不传）。"""
    return {k: v for k, v in kw.items() if _accepts(fn, k)}


class _BaseProvider(object):
    """两个 provider 的共同部分：配置读取 + 引擎锁 + 探针前缀回填。"""

    has_chain = True

    def __init__(self, cfg):
        self.cfg = cfg

    # ── 配置读取（全部 getattr 兜底：cfg 缺字段 = 没配过，不是错误）──
    @property
    def wb(self):
        return getattr(self.cfg, "wb", None)

    def _pp(self):
        """当前探针前缀映射（空 → None，与引擎「没配过」的口径一致）。"""
        return getattr(self.cfg, "probe_prefixes", None) or None

    def _fo(self):
        """『强制 force 信号』基名集合：列进来的叶子跳过 cone、直接 force 顶层基名网。
        contracts 叫 force_signals，架构文里也写过 force_overrides —— 两个名都认，免得
        cfg 换个说法就静默读成「没配过」（配了等于没配是这套配置最典型的坑）。"""
        v = getattr(self.cfg, "force_signals", None)
        if v is None:
            v = getattr(self.cfg, "force_overrides", None)
        return set(v or ()) or None

    def _lo(self):
        return getattr(self.cfg, "logic_overrides", None) or None

    def _risky(self):
        """缺探针前缀的输入放不放行（C-217）。cfg 没这个字段 → True = 引擎原先写死的值。"""
        return bool(getattr(self.cfg, "include_risky", True))

    def _engine(self):
        """引擎互斥：后台 worker 与主线程共用同一个 wb（Resolver.__init__ / _supplemented 都回写
        wb 上的标记），不上锁就会互相看到半个状态。cfg 没给锁 → 不上锁（单线程调用方）。"""
        return getattr(self.cfg, "engine_lock", None) or contextlib.nullcontext()

    # ── 探针前缀（an 里缺的那一格）──
    def _fill_probe_prefix(self, an, fallback_net=""):
        """给 an 补 `probe_prefix`：探针网**在哪一层**是配置，不是网名本身，引擎不该知道；
        拿着 probe_prefixes 的是 provider，所以由它补（执行计划 §7-1）。
        优先用 C0-a 补的 an["out_net"]；没有就用调用点算出的探针网兜底。"""
        if not an:
            return an
        from . import topout as T
        an["probe_prefix"] = T._probe_prefix_for_name(self._pp(), an.get("out_net") or fallback_net)
        return an


class TopoutProvider(_BaseProvider):
    """Topout 视图数据源：cone 展到源寄存器、断言贴顶层真名。

    与 `gui._TopoutProvider` 逐方法等价（tests/test_providers.py 逐键对过），差别只有三处：
    配置源从窗口属性换成 cfg、每次引擎调用上锁、多了 skeleton/progress/lite/want_graph 这些
    v2 后台分析要用的口子。"""

    view_id = "topout"
    kind_label = TOPOUT_KIND_LABEL
    supports_sig_cov = True            # Topout 视图支持单点覆盖度（子视图暂不放）
    supports_logic_overrides = True    # Topout 视图支持 RTL 补充逻辑（cone 看得到补充信号）

    # ── 门面文案 ──
    def title(self):
        return TOPOUT_TITLE

    def empty_hint(self):
        return TOPOUT_EMPTY_HINT

    def has_page(self):
        return bool(getattr(self.wb, "topout", None))

    @contextlib.contextmanager
    def _supplemented(self):
        """临时把 wb.logic 换成应用了 RTL 补充逻辑的版本——让 cone(resolve_root/build_index) 与
        generator.build 都看到补充信号（如某个 ECO 级），退出 swap-and-restore（不污染共享 wb）。
        无补充 = 不动（逐字节不变）。在 provider 层包，免得逐函数加形参。"""
        lo = self._lo()
        if not lo:
            yield
            return
        wb = self.wb
        saved = wb.logic
        wb.logic = generator._logic_with_overrides(wb, generator.GenOptions(logic_overrides=lo))
        try:
            yield
        finally:
            wb.logic = saved

    # ── 清单 ──
    def skeleton_models(self):
        """**便宜**清单（只 resolve_root，不出向量）：载入 200+ 信号时先让清单可点，
        用例数/真值表等后台 worker 逐信号回填（C-276）。每行 status="pending"。"""
        from . import topout as T
        fn = getattr(T, "topout_skeleton_models", None)
        with self._engine(), self._supplemented():
            if fn is not None:                    # C0-a 的实现（合并后走这条）
                return fn(self.wb, probe_prefixes=self._pp(), force_overrides=self._fo(),
                          **_opt(fn, logic_overrides=self._lo()))
            return self._skeleton_fallback()

    def _skeleton_fallback(self):
        """引擎侧还没有 topout_skeleton_models 时的等价兜底（键集与 C0-a 规格一致）。"""
        from . import topout as T
        wb = self.wb
        out = []
        try:
            logic_idx, mux_idx = T.build_index(wb)
        except Exception:  # noqa: BLE001  索引都建不起来 → 给空清单，别让窗口开不出来
            return out
        pp = self._pp()
        for topo in (getattr(wb, "topout", None) or []):
            try:
                root = T.resolve_root(wb, topo.name, logic_idx, mux_idx)
            except Exception:  # noqa: BLE001  单信号解析失败不连累整批
                root = None
            kind = getattr(root, "kind", T.UNRESOLVED) if root is not None else T.UNRESOLVED
            pnet = topo.name
            if root is not None:
                if getattr(root, "renamed", False) and getattr(root, "probe_name", ""):
                    pnet = root.probe_name
                elif kind in (T.LOGIC, T.MUX) and getattr(root, "obj", None) is not None:
                    pnet = getattr(root.obj, "rtl_base", None) or topo.name
            out.append({
                "name": topo.name,
                "disp": "%s%s" % (topo.name, T._topout_slice_suffix(topo, getattr(topo, "width", 1))),
                "owner": topo.owner, "width": getattr(topo, "width", 1),
                "kind": kind, "status": "pending", "status_detail": "pending",
                "note": "", "issues": [], "n_vectors": None,
                "form": "", "form_label": "",
                "probe_net": pnet, "prefix": T._probe_prefix_for_name(pp, pnet),
                "assert_id": "", "matched_name": getattr(root, "matched_name", "") if root else "",
            })
        return out

    def view_models(self, mode, max_tests, exhaustive, sig_cov=None, form_cov=None,
                    progress=None, should_cancel=None, lite=False):
        """整表视图模型。progress/should_cancel/lite 是 v2 后台分析用的（C0-a 在 topout 里落地）；
        引擎还没有这几个形参时忽略它们 —— 行为 = 一趟跑完，与旧门面逐键相同。"""
        from . import topout as T
        with self._engine(), self._supplemented():
            return T.topout_view_models(
                self.wb, mode=mode, max_tests=max_tests, exhaustive=exhaustive,
                probe_prefixes=self._pp(), force_overrides=self._fo(),
                sig_cov=sig_cov, form_cov=form_cov,
                **_opt(T.topout_view_models, progress=progress, should_cancel=should_cancel,
                       lite=lite, include_risky=self._risky()))

    # ── 单信号 ──
    def analyze(self, name, mode, max_tests, exhaustive, mux_data=None, want_graph=False):
        """一个 Topout 信号 → 统一分析 dict an（编辑器/真值表/展开链/输入表都吃它）。
        找不到这个名字 → None（调用方按「没选中」处理，不是错误）。"""
        from . import topout as T
        with self._engine(), self._supplemented():
            topo = next((t for t in (self.wb.topout or []) if t.name == name), None)
            if topo is None:
                return None
            res = T.analyze_signal(self.wb, R.Resolver(self.wb, wire_prefixes=self._pp(),
                                                       force_overrides=self._fo()), topo,
                                   mode=mode, max_tests=max_tests, exhaustive=exhaustive,
                                   mux_data=mux_data,
                                   **_opt(T.analyze_signal, want_graph=want_graph))
            an = AN.norm_topout_result(res, self.wb)   # 传 wb → 输入按 for_test 行序
        return self._fill_probe_prefix(an, T._topout_probe_net(res))

    # ── 产出 ──
    def render_sv(self, only, mode, max_tests, exhaustive, edited, comments=True,
                  sv_summary=False, owner_in_msg=False, scope="all", sig_cov=None, form_cov=None,
                  block_suffix=""):
        from . import topout as T
        eo = ED.topout_edit_overrides(edited)
        with self._engine(), self._supplemented():
            return T.render_topout_sv(
                self.wb, mode=mode, max_tests=max_tests, exhaustive=exhaustive,
                comments=comments, sv_summary=sv_summary, owner_in_msg=owner_in_msg,
                only=only, edit_overrides=eo, probe_prefixes=self._pp(),
                force_overrides=self._fo(), scope=scope, sig_cov=sig_cov, form_cov=form_cov,
                **_opt(T.render_topout_sv, block_suffix=block_suffix,
                       include_risky=self._risky()))

    def render_report(self, mode, max_tests, exhaustive, only=None, sig_cov=None, form_cov=None):
        from . import topout as T
        with self._engine(), self._supplemented():
            return T.topout_report(
                self.wb, mode=mode, max_tests=max_tests, exhaustive=exhaustive,
                probe_prefixes=self._pp(), force_overrides=self._fo(), only=only,
                sig_cov=sig_cov, form_cov=form_cov,
                **_opt(T.topout_report, include_risky=self._risky()))

    def fortest(self, src, out, mode, max_tests, exhaustive, only=None):
        """回填 for_test（含 mux 表）。返回写出的组数——exports 拿它报「回填 N 组」。"""
        from . import topout as T
        from . import fortest_writer as F
        with self._engine(), self._supplemented():
            rep = T.report_for_topout(self.wb, R.Resolver(self.wb, wire_prefixes=self._pp(),
                                                          force_overrides=self._fo()),
                                      mode=mode, max_tests=max_tests, exhaustive=exhaustive,
                                      probe_prefixes=self._pp(), only=only)
        return F.write_fortest(src, out, rep, include_mux=True)


class PageProvider(_BaseProvider):
    """子视图（logic/mux/dft/iddq）数据源：页本地、不跨页 cone（force 级联），只看本模块输入输出。

    与 `gui._PageProvider` 逐方法等价。页本地没有 RTL 补充逻辑那一路（cone 看不到跨页补充），
    故 `_supplemented` 在这边不参与 —— 与旧门面一致，不是漏搬。"""

    supports_sig_cov = False           # 子视图暂不放单点覆盖度（仅 Topout）
    supports_logic_overrides = False
    kind_label = PAGE_KIND_LABEL

    def __init__(self, cfg, page):
        _BaseProvider.__init__(self, cfg)
        self.page = page
        self.view_id = page

    def title(self):
        from . import pageviews as P
        return "%s —— %s" % (P.PAGE_LABEL.get(self.page, self.page),
                             P.PAGE_DESC.get(self.page, ""))

    def empty_hint(self):
        from . import pageviews as P
        return ("⚠ 当前 Excel 的 %s 页没有可显示的行（空页/无此页）。"
                % P.PAGE_LABEL.get(self.page, self.page))

    def has_page(self):
        from . import pageviews as P
        return P.page_available(self.wb, self.page)

    # ── 清单 ──
    def skeleton_models(self):
        """页本地便宜清单：页行本身就带名字/owner/位宽，连 resolve_root 都不用跑。"""
        from . import pageviews as P
        out = []
        with self._engine():
            for sig in P.page_signals(self.wb, self.page):
                pnet = getattr(sig, "rtl_base", None) or sig.out_name
                out.append({
                    "name": sig.out_name, "disp": sig.out_name,
                    "owner": getattr(sig, "owner", ""), "width": getattr(sig, "out_width", 1),
                    "kind": ("mux" if self.page == "mux" else "logic"),
                    "status": "pending", "status_detail": "pending",
                    "note": "", "issues": [], "n_vectors": None,
                    "form": "", "form_label": "", "page": self.page,
                    "probe_net": pnet,
                    "prefix": P._prefix_for(self._pp(), pnet),
                    "assert_id": str(getattr(sig, "assert_id", "") or ""),
                    "matched_name": sig.out_name.lower(),
                })
        return out

    def view_models(self, mode, max_tests, exhaustive, sig_cov=None, form_cov=None,
                    progress=None, should_cancel=None, lite=False):
        """页视图不支持单点/逐形态覆盖度（supports_sig_cov=False），sig_cov/form_cov 只为接口
        一致而存在、不传给引擎——与旧门面 `gui._PageProvider.view_models` 完全一致。
        lite 同理：页模型本来就没有 cone 联表，没有可跳过的第二趟。"""
        from . import pageviews as P
        with self._engine():
            return P.page_view_models(
                self.wb, self.page, mode=mode, max_tests=max_tests, exhaustive=exhaustive,
                probe_prefixes=self._pp(), force_overrides=self._fo(),
                **_opt(P.page_view_models, progress=progress, should_cancel=should_cancel,
                       include_risky=self._risky()))

    # ── 单信号 ──
    def analyze(self, name, mode, max_tests, exhaustive, mux_data=None, want_graph=False):
        from . import pageviews as P
        with self._engine():
            sig = next((s for s in P.page_signals(self.wb, self.page) if s.out_name == name), None)
            if sig is None:
                return None
            res = P.analyze_page_signal(
                self.wb, P._page_resolver(self.wb, self._pp(), self._fo()), sig, self.page,
                mode=mode, max_tests=max_tests, exhaustive=exhaustive,
                **_opt(P.analyze_page_signal, mux_data=mux_data, want_graph=want_graph))
            an = AN.norm_page_result(res, self.wb)     # 传 wb → 输入按 for_test 行序
        return self._fill_probe_prefix(an, getattr(res.sig, "rtl_base", None) or res.name)

    # ── 产出 ──
    def render_sv(self, only, mode, max_tests, exhaustive, edited, comments=True,
                  sv_summary=False, owner_in_msg=False, scope="all", sig_cov=None, form_cov=None,
                  block_suffix=""):
        from . import pageviews as P
        with self._engine():
            return P.build_page_sv(
                self.wb, self.page, mode=mode, max_tests=max_tests, exhaustive=exhaustive,
                edit_overrides=ED.page_edit_overrides(edited), only=only, comments=comments,
                sv_summary=sv_summary, owner_in_msg=owner_in_msg, probe_prefixes=self._pp(),
                scope=scope, force_overrides=self._fo(),
                **_opt(P.build_page_sv, block_suffix=block_suffix, include_risky=self._risky()))

    def render_report(self, mode, max_tests, exhaustive, only=None, sig_cov=None, form_cov=None):
        from . import pageviews as P
        with self._engine():
            return P.page_report(
                self.wb, self.page, mode=mode, max_tests=max_tests, exhaustive=exhaustive,
                probe_prefixes=self._pp(), only=only, force_overrides=self._fo(),
                **_opt(P.page_report, include_risky=self._risky()))

    def fortest(self, src, out, mode, max_tests, exhaustive, only=None):
        """回填 for_test（含 mux 表）。page_fortest 不回组数 → 返回 None，exports 就不报组数。"""
        from . import pageviews as P
        with self._engine():
            return P.page_fortest(
                self.wb, self.page, src, out, mode=mode, max_tests=max_tests,
                exhaustive=exhaustive, probe_prefixes=self._pp(), only=only,
                force_overrides=self._fo(),
                **_opt(P.page_fortest, include_risky=self._risky()))
