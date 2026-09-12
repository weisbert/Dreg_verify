# -*- coding: utf-8 -*-
"""bus.py —— 「当前线网」高亮总线（架构 §2.5 / C-282 / 不变量 I-18）。

为什么要一根总线而不是让视图互相调：
  点一根线，要同时亮起来的有四处——电路图、展开链、输入信号表、真值表列头。
  四个视图若互相 import 再互相 setXxx，就是 4×3 条依赖 + 一串「谁先亮谁后亮」的时序 bug
  （最典型的是 A 通知 B、B 回写 A → 无限递归；第二典型的是重入时把用户刚点的那根冲掉）。
  本模块把这件事收成一条广播：谁都只 `select(net, origin)`，谁都只连 `netSelected`。

订阅方的唯一规矩：`origin` 是自己发的 → 只更新样式，**不要回写选择**（否则就是上面的递归）。
比对键一律 `net_key(name)`（小写 + 剥位宽），与 `excel_model._strip_width` 同源，不另写一份。

依赖方向（§1.2）：PySide6.QtCore + contracts + 引擎里的名字规整函数，不 import 任何视图。
"""

from PySide6 import QtCore

from dreg_verify import excel_model

from . import contracts

__all__ = ["HighlightBus", "net_key"]


def net_key(name):
    """线网名 → 比对键（小写、剥掉 `[3:0]` / `[2]` 这类位宽切片）。

    四个订阅方与本总线共用它：图上的 `data-net`、真值表列头、输入表行名三处写法不一定一致
    （有的带切片、有的不带），只要都过这道就能对上。**不自己写正则**——位宽写法的口径
    归 `excel_model._strip_width` 一家，多一份必漂。"""
    return excel_model._strip_width(str(name or ""))[0].strip().lower()


class HighlightBus(QtCore.QObject):
    """C-282：点一根线 → 电路图 / 展开链 / 输入信号表 / 真值表 同名行一起高亮。

    实现 `contracts.HighlightBusProto`，信号按 `contracts.BUS_SIGNALS`。"""

    #: (net, origin)：net="" 表示清除高亮；origin ∈ contracts.BUS_ORIGINS
    netSelected = QtCore.Signal(str, str)

    def __init__(self, parent=None):
        QtCore.QObject.__init__(self, parent)
        self.current_net = ""      # 已规整（net_key）
        self.current_origin = ""

    def select(self, net, origin="none"):
        """选中一根线网。返回是否真的广播了。

        同一根线 + 同一个发起方再点一次 → 不重发（I-18：防重入/防抖动）。
        换了发起方仍要重发：新发起方要靠这条广播知道「这根线现在归我选中」，
        而其余订阅方对重复值做幂等更新，代价只是一次样式刷新。"""
        key = net_key(net)
        org = str(origin or "none")
        if org not in contracts.BUS_ORIGINS:
            org = "none"
        if key == self.current_net and org == self.current_origin:
            return False
        self.current_net = key
        self.current_origin = org
        self.netSelected.emit(key, org)
        return True

    def clear(self, origin="none"):
        """清除当前高亮（换信号 / 点空白处）。"""
        return self.select("", origin)
