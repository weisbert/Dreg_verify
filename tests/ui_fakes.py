# -*- coding: utf-8 -*-
"""ui_fakes.py —— v2 视图测试用的两个替身（C1-a 交付给 C1-b/c/d 与后续各波）。

为什么要替身：清单 / 筛选行 / 覆盖度这些视图要测的是**它们自己**（列出了几行、点了之后调没调
state、原因块拼得对不对），不是引擎。真跑一次 mirror 分析要秒级，还会把「视图的 bug」和
「引擎的 bug」搅在一起。

两个替身：
  · `FakeProvider` —— `contracts.ProviderProto` 的最小实现：给定几个名字，`view_models` 逐个
    回调 progress（支持 delay / should_cancel / lite / 抛异常），用来测 worker 与载入态。
  · `FakeState`    —— **真的** `WorkbenchState`，只是把五个 provider 换成 FakeProvider。
    这样视图拿到的是真信号、真语义（勾选回落 None、编辑回写、指纹…），却一行引擎都不跑。

用法：
    import ui_fakes as F
    st = F.FakeState()                     # 已经载好 4 个假信号的 topout 清单
    st = F.FakeState(names=[...], delay=0.02)
"""

import time

from dreg_verify.ui import contracts
from dreg_verify.ui import state as ST

#: 公开仓：假名字也走 mirror 命名（绝不出现真实信号名）
DEFAULT_NAMES = ("d_fake_lna_itrim", "d_fake_rx_en", "d_fake_pll_lock", "d_fake_agc_gain")

_KIND_LABEL = {"logic": "选路/logic", "mux": "mux", "register": "直连寄存器"}


def lite_model(name, i=0, **over):
    """一行 lite 视图模型（键集 = `contracts.LITE_MODEL_KEYS`，一个不多一个不少）。"""
    m = {"name": name, "disp": name, "owner": "fake_owner", "width": 1,
         "kind": ("mux" if i % 2 else "logic"), "status": "ok", "status_detail": "clean",
         "note": "", "issues": [], "matched_name": name.lower(), "n_leaves": 1,
         "n_vectors": 2 + i, "form": "boolean", "form_label": "布尔 (F1)",
         "probe_net": name, "prefix": "", "assert_id": str(i + 1), "expr": "A & B",
         "input_names": ["d_fake_in_a", "d_fake_in_b"], "supplement": False,
         "normalized_note": "", "out_net": name}
    m.update(over)
    return m


class FakeProvider(object):
    """`contracts.ProviderProto` 的最小实现。

    delay: 每个信号假装算这么久（秒）——用来测载入态与取消；
    fail:  非空则 `view_models` 抛这句话（测 `failed` 通道与 I-12 的 scrub）。"""

    has_chain = True
    supports_sig_cov = True
    kind_label = _KIND_LABEL

    def __init__(self, names=DEFAULT_NAMES, view_id="topout", delay=0.0, fail=""):
        self.view_id = view_id
        self.names = list(names)
        self.delay = float(delay)
        self.fail = str(fail or "")
        self.calls = []                 # 调用流水，测试断言「视图有没有多跑一趟」

    def title(self):
        return "假数据源 · %s" % self.view_id

    def empty_hint(self):
        return "假数据源没有行"

    def has_page(self):
        return bool(self.names)

    def skeleton_models(self):
        self.calls.append("skeleton")
        return [lite_model(n, i, status="pending", status_detail="pending", n_vectors=None)
                for i, n in enumerate(self.names)]

    def view_models(self, mode="max", max_tests=256, exhaustive=False, sig_cov=None,
                    form_cov=None, progress=None, should_cancel=None, lite=False):
        self.calls.append(("view_models", mode, max_tests, exhaustive, bool(lite)))
        if self.fail:
            raise RuntimeError(self.fail)
        out, total = [], len(self.names)
        for i, n in enumerate(self.names):
            if should_cancel is not None and should_cancel():
                break
            if self.delay:
                time.sleep(self.delay)
            out.append(lite_model(n, i))
            if progress is not None:
                progress(i + 1, total, n, out[-1])
        return out

    def analyze(self, name, mode="max", max_tests=256, exhaustive=False, mux_data=None,
                want_graph=False):
        self.calls.append(("analyze", name, mode, bool(want_graph)))
        if name not in self.names:
            return None
        return {"name": name, "src_out_name": name, "kind": "logic", "editable": "",
                "renamed": False, "groups": [], "vectors": [], "out_width": 1,
                "note": "", "issues": [], "status": "ok", "status_detail": "clean",
                "out_net": name, "graph": None if not want_graph else object()}

    def render_sv(self, only=None, mode="max", max_tests=256, exhaustive=False, edited=None,
                  comments=True, sv_summary=False, owner_in_msg=False, scope="all",
                  sig_cov=None, form_cov=None, block_suffix=""):
        return ("// fake sv\n", {"n_signals": len(self.names), "skipped": []})

    def render_report(self, mode="max", max_tests=256, exhaustive=False, only=None,
                      sig_cov=None, form_cov=None):
        return {"tables": [], "signals": list(self.names)}

    def fortest(self, src, out, mode="max", max_tests=256, exhaustive=False, only=None):
        return 0


class FakeState(ST.WorkbenchState):
    """真 `WorkbenchState`，五个范围的 provider 全换成 `FakeProvider`，并已载好一份清单。

    视图测试拿它当 state 用即可：信号、勾选回落、编辑回写、指纹全都是真的行为。"""

    def __init__(self, names=DEFAULT_NAMES, view_id=contracts.DEFAULT_VIEW_ID, delay=0.0,
                 loaded_path="<fake.xlsx>", parent=None):
        ST.WorkbenchState.__init__(self, parent)
        self.wb = object()                       # provider 只在真引擎路径上用 wb，假的够了
        self.excel_path = self.loaded_path = loaded_path
        self._providers = {vid: FakeProvider(names, view_id=vid, delay=delay)
                           for vid in contracts.VIEW_IDS}
        self.scope = view_id
        self.set_models(view_id, self._providers[view_id].view_models(lite=True))
