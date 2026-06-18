# -*- coding: utf-8 -*-
"""
diag_rtl_gui.py — 红区只读诊断 GUI（goal-redzone-binder 里程碑 M2）

把 M1 的命令行诊断（diag_rtl_binding）做成【可探索、可信】的桌面视图：
点一根探针就把"工具凭什么这么判"摊开——裸名真的不在 RTL、_to_mux 真网就在那、
由哪条 assign 驱动、候选有哪些——把射频设计师的"我不懂那 148"变成"哦原来如此"。

═══ 红区铁律（本文件严格遵守）═══
  · 零 egress：真网名【不出红区】。无任何写 .sv / 写外部 / 导出 / 保存 / 联网 控件。
    filedialog 仅 askopenfilename（读 claims）；复制仅 root.clipboard_append（留红区，不算外发）。
  · 只读：不改 .sv、不改 claims、不改 RTL。
  · 零第三方依赖：只用标准库 + tkinter（stdlib）。复用同目录 diag_rtl_binding / scan_rtl。
  · 兼容 py3.7+：% 格式化、避开海象(:=)/match；tk 调用包 try/except，无显示不崩。
    （控制台 UTF-8 兜底依赖 3.7 的 reconfigure；红区 Linux 默认 UTF-8，窗口内文本走 tk 渲染不受控制台编码影响。）

═══ 用法（红区，已 source dreg 环境时全自动）═══
    python3 diag_rtl_gui.py
  打开后点『打开 claims…』选 claims.json；RTL 默认走 dreg 环境自动推断，也可手填 top / rtl-dirs。

═══ 可测性架构 ═══
  纯数据层（全部单测、零 tk 依赖）：build_rows / row_matches / detail_text
  （find_driving_assigns 在 diag_rtl_binding 里）。
  tkinter 外壳：App 类组装 Treeview/详情/搜索/按钮，只调纯函数算好的结果，自身不含判定逻辑。
"""

import argparse
import os
import sys

import diag_rtl_binding as D

# tkinter 是标准库，但无显示环境 import 可能就失败 —— 纯数据层不需要它，故容错 import。
try:
    import tkinter as tk
    from tkinter import ttk, filedialog, messagebox
except Exception:  # noqa: BLE001  无 DISPLAY / 无 tk —— 纯函数仍可用，GUI 入口才需要
    tk = None
    ttk = None
    filedialog = None
    messagebox = None


# ───────────────────────────── 纯数据层（零 tk 依赖，全部单测）─────────────────────────────
# 判定 → 行 tag（颜色键）+ 短标签。amber_missing = unknown 且裸名 missing（=那 148 类的强信号）。
_VERDICT_LABEL = {
    "output": "✓ OUTPUT",
    "input-suspect": "⚠ INPUT-suspect",
    "unknown": "? UNKNOWN",
}


def _row_tag(result):
    """单个 result → 单个行 tag（颜色键）。优先级从高到低：
       input_suspect > mismatch > amber_missing > unknown > output。
    （input-suspect 最刺眼；prefix mismatch 次之；裸名 missing 的 unknown 用略深琥珀一眼认出。）"""
    verdict = result.get("verdict")
    if verdict == "input-suspect":
        return "input_suspect"
    if result.get("prefix_check") == "mismatch":
        return "mismatch"
    if verdict == "unknown" and result.get("location") == "missing":
        return "amber_missing"
    if verdict == "unknown":
        return "unknown"
    return "output"


def build_rows(results):
    """results → Treeview 行模型 list。

    每项 {"cols": (signal, verdict_label, probe_net, real_out, loc_text),
          "tag": str, "key": int, "result": result}。
    判定+上色全算在纯层，tkinter 外壳零判定逻辑（tree.insert 只用 row['tag']）。
    """
    rows = []
    for i, r in enumerate(results):
        verdict = r.get("verdict")
        label = _VERDICT_LABEL.get(verdict, str(verdict))
        # 真输出建议：output 行没有需要改探的真网 → "—"
        real_out = r.get("real_output") or "—"
        # 位置文本：missing 用 ❌ 标注；prefix mismatch 在文本前叠 ⚠（即便位置本身存在）
        loc = r.get("location")
        if loc == "missing":
            loc_text = "❌missing"
        else:
            loc_text = loc if loc else "—"
        if r.get("prefix_check") == "mismatch":
            loc_text = "⚠ " + loc_text
        cols = (r.get("signal"), label, r.get("probe_net"), real_out, loc_text)
        rows.append({"cols": cols, "tag": _row_tag(r), "key": i, "result": r})
    return rows


def row_matches(result, query, verdict_set):
    """过滤谓词：搜索（信号/探针网/真输出 子串、大小写不敏感）AND verdict 过滤。

    · query 空（strip 后）→ 文本条件恒真。
    · verdict_set 为空集 → 恒 False（外壳据此显示『至少勾一个』提示）。
    零 tk 依赖，可脱壳全组合单测。
    """
    if not verdict_set:
        return False
    if result.get("verdict") not in verdict_set:
        return False
    q = (query or "").strip().lower()
    if not q:
        return True
    sig = (result.get("signal") or "").lower()
    probe = (result.get("probe_net") or "").lower()
    real = (result.get("real_output") or "").lower()
    return q in sig or q in probe or q in real


def _fmt_assigns(net, ctx):
    """find_driving_assigns(net) → 多行 'assign <lhs> = <rhs> ;' 原文；
    返 [] 时显式提示『未找到 assign 驱动…人工核对』，绝不留空（诚实暴露局限=建立信任）。"""
    drivers = D.find_driving_assigns(net, ctx) if (net and ctx is not None) else []
    if not drivers:
        return ["    （未找到 assign 驱动：可能来自 always 块 / 端口连接，本诊断未覆盖，请人工核对真 RTL）"]
    return ["    assign %s = %s ;" % (d["lhs_raw"], d["rhs_raw"]) for d in drivers]


def detail_text(result, ctx):
    """组装【五节证据链】纯文本（内部调 find_driving_assigns）。

    把 diagnose 的隐含推理摊成可逐节肉眼核对的证据——这是『把那 148 变成哦原来如此』的核心。
    返回纯 str（上色由外壳 Text tag 做，不进本函数）。py3.6+ 兼容。
    """
    verdict = result.get("verdict")
    probe = result.get("probe_net")
    real_out = result.get("real_output")
    prefix = (result.get("prefix") or "").strip()
    candidates = result.get("candidates") or []

    lines = []
    # 顶行人话结论（措辞偏『你配的是 X / 我在 RTL 里看到 Y』）
    lines.append("【结论】%s" % _VERDICT_LABEL.get(verdict, str(verdict)))
    lines.append("    信号：%s    你配的探针网：%s" % (result.get("signal"), probe))
    lines.append("    " + (result.get("evidence") or ""))
    lines.append("")

    # ① 存在性
    lines.append("① 存在性 / 层级")
    exists = result.get("exists")
    location = result.get("location")
    if location == "missing":
        lines.append("    ❌ 裸名 %s 在 RTL 里找不到（没声明、没被任何 assign/always 驱动）。" % probe)
    elif exists is True:
        lines.append("    ✓ 存在：位置 = %s" % location)
    elif exists is False:
        lines.append("    ❌ 不存在：%s" % (location or "missing"))
    else:
        lines.append("    （未提供 RTL 层级信息——本次未加载 sigmap 或该网未被驱动）")
    pc = result.get("prefix_check")
    if pc == "ok":
        lines.append("    前缀核对：✓ 与真层级一致")
    elif pc == "mismatch":
        lines.append("    前缀核对：⚠ 配的前缀对不上真层级")
    else:
        lines.append("    前缀核对：—（未配前缀）")
    lines.append("")

    # ② 同基名真实网（candidates 全列、不截断）
    #   用 claim.input_nets 这个【权威输入清单】给输入网打『别探』——否则把 mux 数据输入网
    #   和真输出并列、会诱导用户探到自己输入（=本 GOAL 全力要消灭的假绿）。
    lines.append("② RTL 里同基名的真实网（← 建议探这根；标「输入网」的别探，探它=假绿）")
    if candidates:
        suggest = (real_out or "").lower()
        input_lc = {n.lower() for n in (result.get("input_nets") or [])}
        for c in candidates:
            cl = c.lower()
            if cl == suggest:
                mark = "  ← 建议探这根"
            elif cl in input_lc:
                mark = "  （输入网，别探：探它=假绿）"
            else:
                mark = ""
            lines.append("    %s%s" % (c, mark))
    else:
        lines.append("    无")
    lines.append("")

    # ③ 真输出建议——【按 verdict 措辞，不能只看 real_out】，否则把判不准的 UNKNOWN
    #   粉饰成『已认定真输出、无需改探』= 假安全感（最该谨慎的探针上最危险）。
    lines.append("③ 真输出建议")
    if real_out:
        lines.append("    → %s" % real_out)
        lines.append("    （理由：规范尾缀 _to_mux/_to_logic/_ls 压过带 _tN/_gN 中缀的子变体）")
    elif verdict == "output":
        if pc == "mismatch":
            lines.append("    探针网本身正确，但配的前缀对不上真层级（见 ⑤）"
                         "——需改前缀/层级后才算真绿，别只按本行当『没问题』。")
        else:
            lines.append("    （已认定探针网就是真输出，无需改探）")
    else:
        lines.append("    ⚠ 本诊断给不出真输出建议（找不到本信号 assign，或驱动来自端口连接）"
                     " → 必须人工核对真 RTL，别当成『没问题』。")
    lines.append("")

    # ④ 驱动证据——标题也按 verdict，UNKNOWN 不冒称『凭什么是输出』。
    if verdict == "input-suspect":
        lines.append("④ 凭什么说你探的是【输入】（假绿嫌疑）")
        lines.append("    驱动真输出 %s 的那条 assign 把你探的网 %s 放在了【右边】= 它是输入："
                     % (real_out or "?", probe))
        lines += _fmt_assigns(real_out or probe, ctx)
    elif verdict == "output":
        lines.append("④ 凭什么是输出（驱动它的 assign 原文）")
        lines += _fmt_assigns(real_out or probe, ctx)
    else:
        lines.append("④ 驱动证据（assign 原文；本诊断未必定位得到，仅供人工核对）")
        lines += _fmt_assigns(real_out or probe, ctx)
    lines.append("")

    # ⑤ 前缀核对（配了才显）
    if prefix:
        lines.append("⑤ 前缀核对")
        lines.append("    你配的前缀：%s" % prefix)
        if pc == "ok":
            lines.append("    ✓ 前缀末段能在真层级路径里找到")
        elif pc == "mismatch":
            lines.append("    ⚠ 前缀末段在真层级路径里找不到——可能配错层级")
        lines.append("")

    lines.append("─" * 60)
    lines.append("⚠ 只读诊断：assign 解析可能漏端口连接 / generate，请对真 RTL 人工核对。")
    return "\n".join(lines)


# ───────────────────────────── tkinter 外壳（薄，判定全在纯层）─────────────────────────────
# 行 tag 背景色（老 Tk 部分平台 Treeview tag background 不生效 → 列文本里另带 ✓/⚠/❌ 符号兜底）。
_TAG_BG = {
    "output": "#e6f4ea",          # 浅绿
    "unknown": "#fff4d6",         # 琥珀
    "amber_missing": "#ffe8a8",   # 略深琥珀（那 148 类，一眼认出）
    "input_suspect": "#fde0e0",   # 浅红（最刺眼）
    "mismatch": "#ffe0c0",        # 橙
}
_TAG_FG = {
    "mismatch": "#a85000",        # 橙前景兜底
    "input_suspect": "#b00000",
}

_COLUMNS = ("signal", "verdict", "probe", "real_out", "loc")
_HEADINGS = {"signal": "信号", "verdict": "判定", "probe": "探针网",
             "real_out": "真输出建议", "loc": "位置"}


class App(object):
    """master-detail 诊断窗。

    可注入架构（为可测）：把『加载』(有 IO 的 load_results) 与『渲染』(纯内存的
    set_results(results, ctx)) 分开——冒烟测试只走 set_results，不碰磁盘/网络/RTL。
    """

    def __init__(self, root):
        self.root = root
        self._results = []
        self._ctx = None
        self._meta = {}
        # 全集计数（始终显示全集统计，不随过滤变 → 防『过滤后以为问题变少』）
        self._counts = {"input-suspect": 0, "unknown": 0, "output": 0}

        try:
            root.title("红区只读诊断 — 探针绑定核对（只读 · 零外发）")
        except Exception:  # noqa: BLE001
            pass

        self.search_var = tk.StringVar()
        self.top_var = tk.StringVar()
        self.dirs_var = tk.StringVar()
        self.infer_var = tk.StringVar(value="（未加载）")
        self.count_var = tk.StringVar(value="")
        self.shown_var = tk.StringVar(value="")
        self.f_suspect = tk.BooleanVar(value=True)    # 默认勾 suspect + unknown（开门即问题优先）
        self.f_unknown = tk.BooleanVar(value=True)
        self.f_output = tk.BooleanVar(value=False)

        self._build_toolbar()
        self._build_panes()

    # ── 顶部条（工具 / 计数 / 搜索 三行）──
    def _build_toolbar(self):
        bar = ttk.Frame(self.root)
        bar.pack(side="top", fill="x")

        # 行1：工具
        r1 = ttk.Frame(bar)
        r1.pack(side="top", fill="x", padx=4, pady=2)
        ttk.Button(r1, text="打开 claims…", command=self.on_open).pack(side="left")
        ttk.Button(r1, text="重新加载", command=self.on_reload).pack(side="left", padx=(4, 12))
        ttk.Label(r1, text="top:").pack(side="left")
        ttk.Entry(r1, textvariable=self.top_var, width=22).pack(side="left", padx=(2, 8))
        ttk.Label(r1, text="rtl-dirs:").pack(side="left")
        ttk.Entry(r1, textvariable=self.dirs_var, width=22).pack(side="left", padx=(2, 8))
        ttk.Label(r1, textvariable=self.infer_var, foreground="#555").pack(side="left")

        # 行2：计数（点击当快捷过滤）
        r2 = ttk.Frame(bar)
        r2.pack(side="top", fill="x", padx=4, pady=2)
        self.lbl_suspect = ttk.Label(r2, textvariable=self.count_var, foreground="#b00000")
        self.lbl_suspect.pack(side="left")
        # 三个独立 Label 用同一 count_var 不便分别上色 → 拆三个变量
        self.cnt_suspect = tk.StringVar()
        self.cnt_unknown = tk.StringVar()
        self.cnt_output = tk.StringVar()
        self.lbl_suspect.config(textvariable=self.cnt_suspect)
        lu = ttk.Label(r2, textvariable=self.cnt_unknown, foreground="#a85000")
        lu.pack(side="left", padx=12)
        lo = ttk.Label(r2, textvariable=self.cnt_output, foreground="#176a2e")
        lo.pack(side="left")
        # 计数 Label 当快捷过滤
        self.lbl_suspect.bind("<Button-1>", lambda e: self._quick_filter("input-suspect"))
        lu.bind("<Button-1>", lambda e: self._quick_filter("unknown"))
        lo.bind("<Button-1>", lambda e: self._quick_filter("output"))

        # 行3：搜索 + verdict 复选
        r3 = ttk.Frame(bar)
        r3.pack(side="top", fill="x", padx=4, pady=2)
        ttk.Label(r3, text="搜索:").pack(side="left")
        ent = ttk.Entry(r3, textvariable=self.search_var, width=28)
        ent.pack(side="left", padx=(2, 12))
        ent.bind("<KeyRelease>", lambda e: self.refresh())
        ttk.Checkbutton(r3, text="suspect", variable=self.f_suspect,
                        command=self.refresh).pack(side="left")
        ttk.Checkbutton(r3, text="unknown", variable=self.f_unknown,
                        command=self.refresh).pack(side="left")
        ttk.Checkbutton(r3, text="output", variable=self.f_output,
                        command=self.refresh).pack(side="left")
        ttk.Label(r3, textvariable=self.shown_var, foreground="#555").pack(side="left", padx=12)

    # ── 主体：上下可拖 PanedWindow（上 Treeview / 下 详情）──
    def _build_panes(self):
        pw = ttk.PanedWindow(self.root, orient="vertical")
        pw.pack(side="top", fill="both", expand=True)

        # 上半：Treeview
        top_frame = ttk.Frame(pw)
        self.tree = ttk.Treeview(top_frame, columns=_COLUMNS, show="headings", selectmode="browse")
        for c in _COLUMNS:
            self.tree.heading(c, text=_HEADINGS[c])
            self.tree.column(c, width=180 if c in ("signal", "probe", "real_out") else 130,
                             anchor="w")
        vsb = ttk.Scrollbar(top_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        # 行 tag 上色（背景 + 前景兜底）
        for tag, bg in _TAG_BG.items():
            try:
                self.tree.tag_configure(tag, background=bg)
            except Exception:  # noqa: BLE001
                pass
        for tag, fg in _TAG_FG.items():
            try:
                self.tree.tag_configure(tag, foreground=fg)
            except Exception:  # noqa: BLE001
                pass
        self.tree.bind("<<TreeviewSelect>>", lambda e: self._on_select())
        self.tree.bind("<Double-1>", lambda e: self._copy_real_output())
        self.tree.bind("<Button-3>", self._popup_menu)
        pw.add(top_frame, weight=55)

        # 下半：详情面板（只读 Text + 复制按钮）
        bot = ttk.Frame(pw)
        self.detail = tk.Text(bot, wrap="word", state="disabled",
                              font=("Consolas", 10), height=12)
        dsb = ttk.Scrollbar(bot, orient="vertical", command=self.detail.yview)
        self.detail.configure(yscrollcommand=dsb.set)
        self.detail.pack(side="left", fill="both", expand=True)
        dsb.pack(side="right", fill="y")
        # 详情上色 tag
        try:
            self.detail.tag_configure("assign", foreground="#0a5")
            self.detail.tag_configure("susp", foreground="#b00000")
        except Exception:  # noqa: BLE001
            pass
        act = ttk.Frame(bot)
        act.pack(side="bottom", fill="x")
        ttk.Button(act, text="复制真输出建议",
                   command=self._copy_real_output).pack(side="left", padx=2, pady=2)
        ttk.Button(act, text="复制探针网",
                   command=self._copy_probe_net).pack(side="left", padx=2, pady=2)
        pw.add(bot, weight=45)

        # 右键菜单
        try:
            self._menu = tk.Menu(self.root, tearoff=0)
            self._menu.add_command(label="复制真输出建议", command=self._copy_real_output)
            self._menu.add_command(label="复制探针网", command=self._copy_probe_net)
        except Exception:  # noqa: BLE001
            self._menu = None

    # ── 渲染（纯内存，无 IO）──
    def set_results(self, results, ctx, meta=None):
        """注入诊断结果 + ctx（冒烟测试走这条，不碰磁盘）。"""
        self._results = list(results or [])
        self._ctx = ctx
        self._meta = meta or {}
        self._counts = {"input-suspect": 0, "unknown": 0, "output": 0}
        for r in self._results:
            v = r.get("verdict")
            if v in self._counts:
                self._counts[v] += 1
        if self._meta:
            self.infer_var.set("top: %s   文件:%s   网:%s   assign:%s"
                               % (self._meta.get("top_module", "?"),
                                  self._meta.get("n_files", "?"),
                                  self._meta.get("n_nets", "?"),
                                  self._meta.get("n_assigns", "?")))
        self.refresh()

    def _verdict_set(self):
        s = set()
        if self.f_suspect.get():
            s.add("input-suspect")
        if self.f_unknown.get():
            s.add("unknown")
        if self.f_output.get():
            s.add("output")
        return s

    def refresh(self):
        """build_rows → row_matches 过滤 → 清空 tree → re-insert 通过的行 → 更新计数与 X/Y。

        计数始终显示全集（self._counts），只有可见行随过滤变。"""
        # 计数（全集，不随过滤变）
        total = len(self._results)
        self.cnt_suspect.set("⚠INPUT-suspect %d" % self._counts["input-suspect"])
        self.cnt_unknown.set("?UNKNOWN %d" % self._counts["unknown"])
        self.cnt_output.set("✓OUTPUT %d  （共 %d）" % (self._counts["output"], total))

        rows = build_rows(self._results)
        query = self.search_var.get()
        vset = self._verdict_set()
        # 记住当前选中行，刷新后若仍可见则重选、被过滤掉则清空详情（防陈旧残留）。
        prev = None
        try:
            s = self.tree.selection()
            if s:
                prev = s[0]
        except Exception:  # noqa: BLE001
            pass
        # 清空后 re-insert（不动底层 results 与计数）
        try:
            for iid in self.tree.get_children():
                self.tree.delete(iid)
        except Exception:  # noqa: BLE001
            pass
        shown = 0
        for row in rows:
            if not row_matches(row["result"], query, vset):
                continue
            try:
                self.tree.insert("", "end", iid=str(row["key"]),
                                 values=row["cols"], tags=(row["tag"],))
            except Exception:  # noqa: BLE001
                pass
            shown += 1
        # 选中行仍在可见集 → 重选（详情仍对应它，不必重绘）；否则清空详情。
        try:
            if prev is not None and prev in self.tree.get_children():
                self.tree.selection_set(prev)
            else:
                self._clear_detail()
        except Exception:  # noqa: BLE001
            pass
        if not vset:
            self.shown_var.set("（至少勾一个判定）")
        else:
            self.shown_var.set("显示 %d/%d" % (shown, total))

    def _quick_filter(self, verdict):
        """点计数 Label 当快捷过滤：点 UNKNOWN=只勾 unknown，点 OUTPUT=把 output 也勾上。"""
        if verdict == "input-suspect":
            self.f_suspect.set(True)
            self.f_unknown.set(False)
            self.f_output.set(False)
        elif verdict == "unknown":
            self.f_suspect.set(False)
            self.f_unknown.set(True)
            self.f_output.set(False)
        elif verdict == "output":
            self.f_output.set(True)
        self.refresh()

    # ── 选中 → 刷详情 ──
    def _selected_result(self):
        try:
            sel = self.tree.selection()
        except Exception:  # noqa: BLE001
            return None
        if not sel:
            return None
        try:
            key = int(sel[0])
        except (ValueError, IndexError):
            return None
        if 0 <= key < len(self._results):
            return self._results[key]
        return None

    def _clear_detail(self):
        try:
            self.detail.config(state="normal")
            self.detail.delete("1.0", "end")
            self.detail.config(state="disabled")
        except Exception:  # noqa: BLE001
            pass

    def _on_select(self):
        r = self._selected_result()
        text = detail_text(r, self._ctx) if r is not None else ""
        try:
            self.detail.config(state="normal")
            self.detail.delete("1.0", "end")
            self.detail.insert("1.0", text)
            self._colorize_detail(r)
            self.detail.config(state="disabled")
        except Exception:  # noqa: BLE001
            pass

    def _colorize_detail(self, result):
        """对 assign 原文行 / input-suspect 的 RHS 探针网上色（上色在外壳，文本来自纯函数）。"""
        try:
            idx = "1.0"
            while True:
                idx = self.detail.search("    assign ", idx, stopindex="end")
                if not idx:
                    break
                line_end = self.detail.index("%s lineend" % idx)
                self.detail.tag_add("assign", idx, line_end)
                idx = line_end
            # input-suspect：把探针网在详情里标红
            if result is not None and result.get("verdict") == "input-suspect":
                probe = result.get("probe_net")
                if probe:
                    pos = "1.0"
                    while True:
                        pos = self.detail.search(probe, pos, stopindex="end")
                        if not pos:
                            break
                        endp = "%s+%dc" % (pos, len(probe))
                        self.detail.tag_add("susp", pos, endp)
                        pos = endp
        except Exception:  # noqa: BLE001
            pass

    # ── 复制（留红区，clipboard_append 不算 egress）──
    def _copy(self, text):
        if not text:
            return
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(text)
        except Exception:  # noqa: BLE001
            pass

    def _copy_real_output(self):
        r = self._selected_result()
        if r is not None:
            self._copy(r.get("real_output") or r.get("probe_net") or "")

    def _copy_probe_net(self):
        r = self._selected_result()
        if r is not None:
            self._copy(r.get("probe_net") or "")

    def _popup_menu(self, event):
        try:
            row = self.tree.identify_row(event.y)
            if row:
                self.tree.selection_set(row)
            if self._menu is not None:
                self._menu.tk_popup(event.x_root, event.y_root)
        except Exception:  # noqa: BLE001
            pass

    # ── 加载（有 IO，调 load_results）──
    def _args_from_ui(self, claims_path):
        return argparse.Namespace(
            claims=claims_path,
            top=(self.top_var.get().strip() or None),
            top_module=None,
            rtl_dirs=(self.dirs_var.get().strip() or None),
            max_depth=16, out=None, only=None, nets=None, excel=None)

    def on_open(self):
        try:
            path = filedialog.askopenfilename(
                title="打开 claims.json（只读）",
                filetypes=[("claims JSON", "*.json"), ("所有文件", "*.*")])
        except Exception:  # noqa: BLE001
            path = None
        if not path:
            return
        self._claims_path = path
        self._do_load(path)

    def on_reload(self):
        path = getattr(self, "_claims_path", None)
        if not path:
            self._warn("还没打开 claims.json —— 先点『打开 claims…』")
            return
        self._do_load(path)

    def _do_load(self, path):
        logs = []
        try:
            args = self._args_from_ui(path)
            ctx, results, meta = D.load_results(args, log=lambda m: logs.append(str(m)))
            self.set_results(results, ctx, meta)
        except SystemExit as ex:        # load_results 里 sys.exit(...) 的友好提示
            self._warn("加载失败：%s\n\n已读进度:\n%s" % (ex, "\n".join(logs)))
        except Exception as ex:         # noqa: BLE001  找不到 RTL / claims / 解析错——不崩
            self._warn("加载出错：%s\n\n已读进度:\n%s" % (ex, "\n".join(logs)))

    def _warn(self, msg):
        try:
            messagebox.showwarning("诊断 GUI", msg)
        except Exception:  # noqa: BLE001
            sys.stderr.write(msg + "\n")


def main():
    if tk is None:
        sys.exit("[!] 本机没有可用的 tkinter / 显示环境，GUI 无法启动（纯数据层仍可被测试 import）。")
    ap = argparse.ArgumentParser(
        description="红区只读诊断 GUI：可视化核对每根探针是真输出还是探到了自己输入（只读 · 零外发）。")
    ap.add_argument("--claims", help="启动即加载的 claims.json（可选，也可在界面里打开）")
    args = ap.parse_args()
    try:
        root = tk.Tk()
    except Exception as ex:  # noqa: BLE001
        sys.exit("[!] 无法创建窗口（无显示环境？）：%s" % ex)
    app = App(root)
    if args.claims:
        app._claims_path = args.claims
        app._do_load(args.claims)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
