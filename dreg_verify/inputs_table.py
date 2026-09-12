# -*- coding: utf-8 -*-
"""inputs_table.py — 『驱动明细整族』的 Qt-free 模型层（GUI v2 阶段 A4c）。

这一族是『排查(旧)』门面独有、新门面还没有的东西，v2 要常驻显示：

  ① **输入信号表** —— 每个输入一行：字母 → 真实信号名(位宽)、角色、RO/RW、
     具体怎么驱动它（`RF_WRITE 0x64 bit<<0` / `force ENV_RF.xxx`）。
  ② **每列驱动明细** —— 某一条测试实际下的每条 force / RF_WRITE 语句
     （旧门面藏在列头 tooltip 里，v2 要给一块常驻面板）。
  ③ **解析明细 + 找不到网的排查提示** —— 逐输入说清「这根网是从表里查到的，
     还是按命名约定猜出来的」，以及仿真器报『找不到这根网』时怎么一步步查。

输入统一是 `analysis_norm` 归一化出来的 **an**（Topout 与页视图两种流水线都产 an），
所以两套门面吃同一份实现，文案不会再各写一遍然后漂移。

**本模块不 import PySide6**：只返回行 dict / 字符串，画表格、上色、加粗全是 gui 的事。

术语（Design prompt §8.6）：界面文案里不裸用 cone / prefixed-wire / wire 兜底 /
记账 / tmm / regmap / CUVUNF —— 要么换成工程师的说法，要么就地解释一句。
`FOUND_IN_LABEL` 是『排查(旧)』沿用的老短标签（保持逐字节不变），
`FOUND_IN_TEXT` 是给 v2 与 `resolve_detail()` 用的合规说法。
"""

import re

from . import expr as E
from . import sv_writer as W

__all__ = [
    "INPUT_COLS", "FOUND_IN_LABEL", "FOUND_IN_TEXT", "STATUS_HELP", "AN_STATUS_TEXT",
    "TRUSTED_FOUND_IN", "CUVUNF_FIRST", "scrub_terms", "binding_meta", "group_letters",
    "vheader_label", "vheader_short", "mux_label", "dft_gate_row", "mux_ctrl_rows",
    "input_rows", "row_cells", "drive_ctx", "drive_pair", "vector_drives", "column_drives",
    "cell_drive_tip", "header_drive_tip", "gate_pin", "legacy_resolve_detail", "resolve_detail",
]

# 「输入信号」表(真值表上方)：把字母→信号/角色/驱动 集中成一张可读的小表
INPUT_COLS = ["字母", "信号(位宽)", "角色", "类型", "驱动"]

# 输入来源(found_in)的中文短标签——『排查(旧)』明细面板用；未映射的原样显示
FOUND_IN_LABEL = {"tmm": "tmm命中", "regmap": "regmap命中", "logic": "级联前级",
                  "logic-internal": "内部信号", "wire": "wire兜底",
                  "prefixed-wire": "前缀wire", "self-input": "自引用前级",
                  "needs-prefix": "需探针前缀(跑scan_rtl)",
                  "logic-computed": "上游计算网(展开驱动)"}

# 同一来源的【工程师说法】——v2 与 resolve_detail 用；关键是说清「查到的」还是「猜的」
FOUND_IN_TEXT = {
    "tmm": "寄存器定义表（Excel 的 tmm 页）里查到了这个字段",
    "regmap": "寄存器地址映射表（Excel 的 regmap 页）里查到了这个字段",
    "logic": "上一级 logic 行算出来的网（已展开到它的源寄存器）",
    "logic-internal": "同一页里的内部信号（已展开到它的源寄存器）",
    "logic-computed": "上游算出来的网，驱动已展开到源寄存器",
    "self-input": "本信号前一级的输出（自引用）",
    "wire": "表里没查到，按命名约定直接当线网 force —— 名字对不上仿真器就找不到这根网",
    "prefixed-wire": "表里没查到，套了配置的层级前缀当线网 force —— "
                     "前缀或名字对不上仿真器就找不到这根网",
    "needs-prefix": "这根网埋在子模块里，要先跑 scan_rtl 扫出层级前缀才 force 得到",
    "mux-output": "上游 mux 的输出衔接网（埋在子模块里，要层级前缀）",
}

# 从表里真查到的来源 = 可信；其余都是靠命名约定推出来的（R41：前缀线网提升是零校验的猜）
TRUSTED_FOUND_IN = frozenset(("tmm", "regmap"))

# 『排查(旧)』左表状态列的 tooltip（analyze_signal 的 status 口径）
STATUS_HELP = {"clean": "输入都解析到具体 net，可正常 force/RF_WRITE 驱动",
               "wire-fallback": "有输入回退成 wire 兜底；elaboration 可能在 ENV_RF 层找不到该 net",
               "unresolved": "有输入未解析到 net（ENV_RF 探不到，仿真会 CUVUNF）",
               "parse-err": "表达式或输入解析出错",
               "spec-collision": "【表数据·非工具能修】mux 页有两行控制选择值相同却选不同数据源——"
                                 "同一选择值 RTL 物理上只能输出一个，已整组跳过。两种成因都可能："
                                 "①真规格矛盾→改数据源；②两个 mux 撞了同一输出名(『一个控制管多个 mux』本身合法，"
                                 "designer 多半复制粘贴漏改名)→改输出名。源名孪生时明细会优先提示成因②。"
                                 "tooltip/明细里有撞车的 Excel 行号、两个源、owner，请对应 designer 核对改表。",
               "needs-prefix": "【输入侧·硬阻断】要 force 的某根输入网埋在子模块里（级联 _to_mux 衔接网 / "
                               "wire 兜底），force 基名钉不住——没配前缀就 force 必 CUVUNF，所以默认【跳过】"
                               "整组。先跑 scan_rtl 配好探针前缀，这组才会生成。",
               "bare-probe": "【输出侧·软提示，已生成】输出 top_out=0（喂内部、非芯片顶层输出），"
                             "工具照样用裸名探针 `ENV_RF.<输出名> 探、【照常生成】.sv。只有仿真 elaboration "
                             "真报 CUVUNF（说明它埋在子模块）时，再跑 scan_rtl 配前缀重生成即可（不是错误）。"
                             "—— 和『输入缺前缀』的区别：那个是输入 force 不到、硬阻断；这个是输出怎么探、不阻断。",
               "false-green": "结构全解析通了，不是未解析——只是数据寄存器字段太窄、装不下每条 case "
                              "的互异值，硬生成会变『RTL 接错路也 PASS』的假测试(假绿)。工具保护性跳过；"
                              "要验得加宽字段或拆组（属设计层，不是工具/表的错）"}

# an["status"] 的工程师说法（Topout / 页视图两条流水线同口径）
AN_STATUS_TEXT = {
    "ok": "可建 —— 输入都落到了具体的网，能正常 force / RF_WRITE 驱动",
    "skip": "跳过 —— 这是只读回读信号，写不进去也就没什么好验的",
    "unresolved": "有输入没解析出网名 —— 照这样生成，仿真器会报找不到这根网",
    "error": "解析出错 —— 表达式或输入名有问题，先看下面的告警",
}

# CUVUNF 首次出现的展开说法（Design prompt §8.6：不许裸用错误码）
CUVUNF_FIRST = "仿真器找不到这根网（elaboration 阶段报错，错误码 CUVUNF）"

# 术语替换表（Design prompt §8.6）：后端给的 note / issues 里还留着一堆内部说法
# （『无 cone，跳过+记账』这种），照搬到界面上就是术语裸露。往界面送之前过一遍这张表。
# ASCII 词用词边界匹配，免得把信号名里的同名片段也改了（d_tmm_xxx 不能动）。
_TERM_RULES = [
    (re.compile(r"跳过\s*\+\s*记账"), "跳过并记在清单里"),
    (re.compile(r"无\s*cone(?![A-Za-z0-9_])", re.I), "没有可展开的上游"),
    (re.compile(r"(?<![A-Za-z0-9_])cone\s*展开", re.I), "从输出往回展开到源寄存器"),
    (re.compile(r"(?<![A-Za-z0-9_])cone(?![A-Za-z0-9_])", re.I), "从输出往回展到源寄存器"),
    (re.compile(r"记账"), "记在清单里"),
    (re.compile(r"(?<![A-Za-z0-9_])prefixed-wire(?![A-Za-z0-9_])", re.I), "带层级前缀的线网"),
    (re.compile(r"wire\s*兜底"), "按命名约定当线网处理"),
    (re.compile(r"(?<![A-Za-z0-9_])regmap(?![A-Za-z0-9_])", re.I), "寄存器地址映射表"),
    (re.compile(r"(?<![A-Za-z0-9_])tmm(?![A-Za-z0-9_])", re.I), "寄存器定义表"),
    (re.compile(r"(?<![A-Za-z0-9_])claims(?![A-Za-z0-9_])", re.I), "认领清单"),
    (re.compile(r"必\s*CUVUNF"), "必然找不到这根网"),
    (re.compile(r"(?<![A-Za-z0-9_])CUVUNF(?![A-Za-z0-9_])"), "找不到这根网"),
]


def scrub_terms(text):
    """把后端文本（an 的 note / issues、解析器留的说明）里的内部术语换成工程师说法。

    只动这几个 §8.6 点名的词，其余原样——信号名、地址、位段都不能碰。"""
    out = text or ""
    for rx, rep in _TERM_RULES:
        out = rx.sub(rep, out)
    return out


# ─────────────────────────── 绑定 → 显示文本 ───────────────────────────
def binding_meta(b):
    """绑定 → (类型, 驱动机制) 文本：RO→force <net>；RW→RF_WRITE 0x<地址> bit<<<lsb>；
    未解析→标红提示。logic 的『输入信号』表与 mux 的共用。"""
    if b is None:
        return ("?", "(无绑定)")
    kind = b.kind or "?"
    if not getattr(b, "resolved", True):
        note = getattr(b, "note", "") or ""
        return (kind, "✗未解析" + ("：" + note if note else ""))
    if b.kind == "RW" and b.address is not None:
        return (kind, "RF_WRITE 0x%X bit<<%d" % (b.address, b.reg_lsb or 0))
    if b.kind == "RO":
        drive = "force ENV_RF.%s" % b.wire_lhs
        # force 级联网 / 内部衔接网（_to_mux/_to_logic）：resolved=True 但要 scan_rtl 配前缀，
        # 否则 force 必 CUVUNF 被跳过——标出来，让切到 force 模式时一眼看见多了这道前缀要求。
        if getattr(b, "found_in", "") in ("needs-prefix", "mux-output"):
            drive += "  ⚠需探针前缀(内部衔接网，跑 scan_rtl 配前缀否则跳过)"
        return (kind, drive)
    return (kind, getattr(b, "note", "") or "?")


def group_letters(g):
    """该输入组的 Excel 来源坐标(可能多个，如同一物理信号占 A、B 两个字母)。
    普通信号 = 表达式字母(A/B/C…)；已展开上游的信号 = 叶子来源("上游行名.字母"，如 pll_n1.A)。"""
    return ",".join(g.get("xl_letters") or g.get("letters") or [])


def vheader_label(g):
    """完整行表头：'A,B → d_xxx[14:14]'（CSV 导出用，保持自描述）。"""
    ltr = group_letters(g)
    base_lbl = g.get("label", g["base"])
    return "%s → %s" % (ltr, base_lbl) if ltr else base_lbl


def vheader_short(g):
    """真值表行表头(GUI 用)：信号名(带位宽)+控制标记（2026-06-03 用户拍板：直接用信号名，不用字母）。
    字母→信号 的对照仍在上方『输入信号』表与 tooltip 里（对照表达式 A/B/C 时用）。"""
    label = g.get("label", g["base"])
    return "%s (控制)" % label if g.get("is_control") else label


def mux_label(b):
    """绑定 → 『信号(位宽)』列文本。"""
    return b.base + ("[%d:0]" % (b.width - 1) if b.width > 1 else "")


# ─────────────────────────── 行构造 ───────────────────────────
def _row(letter, name, role, b, width=None, bold=False, key=None,
         is_control=False, is_dft_gate=False, kind=None, drive=None):
    """统一的输入行 dict。`b` 是绑定(可 None)；kind/drive 可显式覆盖(mux 级联控制行没有绑定)。"""
    bk, bd = binding_meta(b)
    k = bk if kind is None else kind
    d = bd if drive is None else drive
    found_in = getattr(b, "found_in", "") or ""
    return {
        "letter": letter,
        "name": name,
        "width": width if width is not None else (getattr(b, "width", 1) or 1),
        "role": role,
        "rw": k,                       # RO / RW / ? / mux
        "drive": d,
        "found_in": found_in,
        "found_in_label": FOUND_IN_LABEL.get(found_in, found_in),
        "found_in_text": FOUND_IN_TEXT.get(found_in, found_in),
        "trusted": found_in in TRUSTED_FOUND_IN,
        "note": (getattr(b, "note", "") or ""),
        "resolved": bool(getattr(b, "resolved", True)) if b is not None else False,
        "base": getattr(b, "base", None),
        "key": key,
        "bold": bool(bold),
        "is_control": bool(is_control),
        "is_dft_gate": bool(is_dft_gate),
    }


def row_cells(row):
    """一行 → 『输入信号』表的 5 个单元格文本，次序与 INPUT_COLS 一致。"""
    return [row["letter"], row["name"], row["role"], row["rw"], row["drive"]]


def dft_gate_row(gate):
    """输出受 dft 页 iddq 门控 → 『输入信号』表追加的那一行门网；gate 为空返回 None。

    gate = an["dft_gate"]，形如 {"label": 门基名, "binding": 绑定, "transp": 透传值, ...}。

    2026-06-10 Hi1108 实地反馈：iddq 门不在表达式输入里（它在 dft 页），编辑器真值表
    从不显示它，用户对照 for_test 以为漏了这个源头控制。在输入表单独亮出（驱动列照常给
    未解析/需探针前缀着色——它正是 IDDQ 漏电态拍的 force 目标）。"""
    if not gate:
        return None
    b = gate.get("binding")
    return _row(letter="dft页", name=gate.get("label") or "?",
                role="DFT门(iddq)·每条测试驱透传值", b=b, width=gate.get("width", 1) or 1,
                bold=True, key=gate.get("key"), is_dft_gate=True)


def gate_pin(an):
    """真值表『DFT门』输入行要的 (门基名, 透传值, 寄存器地址, bit位)；不受门控 → None。

    与旧门面 `_dft_pin_display` 同口径（那边现场 resolve，这边直接读 an——an 里的门
    已经由 generator.pin_dft_gate 过滤过：解析不了 / 不是 RO / 门已是显式输入，都不钉）。"""
    g = (an or {}).get("dft_gate")
    if not g:
        return None
    b = g.get("binding")
    return (g.get("label"), int(g.get("transp", 0)),
            getattr(b, "address", None), getattr(b, "reg_lsb", None))


def mux_ctrl_rows(drv, exp):
    """一个控制信号的驱动器 → 『输入信号』表的若干行（按三来源给角色文案）。"""
    out = []
    src = drv.get("source")
    if src == "logic":
        # LPBT 形态：保持 line路径/local路径/模式位 三分角色（文案与历史一致）
        line_key = drv["line"]["key"] if drv.get("line") else None
        local_key = drv["local"]["key"] if drv.get("local") else None
        for key in drv.get("keys", []):
            b = exp["bindings"].get(key) or drv["bindings"].get(key)
            if key == line_key:
                role = "控制·line路径(force线控)"
            elif key == local_key:
                role = "控制·local路径(本地寄存器)"
            else:
                role = "控制·模式位/门控"
            out.append(_row(letter=key.split(":")[-1], name=mux_label(b), role=role,
                            b=b, bold=True, key=key, is_control=True))
        return out
    if src in ("reg", "mux-force"):
        key = drv.get("key")
        b = exp["bindings"].get(key) or drv["bindings"].get(key)
        role = ("控制(寄存器直出)" if src == "reg"
                else "控制(force上游mux衔接网)")
        out.append(_row(letter=drv.get("letter") or "?", name=mux_label(b), role=role,
                        b=b, bold=True, key=key, is_control=True))
        return out
    if src == "mux":
        # mux 级联控制：本控制行 + 上游配方（载体寄存器 + 上游各控制）
        upstream = drv.get("upstream")
        up_no = getattr(upstream, "group_no", "?")
        out.append(_row(letter=drv.get("letter") or "?", name=drv.get("base") or "?",
                        role="控制(经上游mux%s驱动)" % up_no, b=None, width=1, bold=True,
                        is_control=True, kind="mux",
                        drive="经上游 mux%s 输出选路" % up_no))
        recipe = drv.get("recipe") or {}
        carrier_key = recipe.get("carrier_key")
        if carrier_key is not None:
            b = exp["bindings"].get(carrier_key) or recipe.get("bindings", {}).get(carrier_key)
            if b is not None:
                out.append(_row(letter="经 mux%s" % up_no, name=mux_label(b),
                                role="上游mux配方(载体寄存器写目标值)", b=b, key=carrier_key))
        for ud in recipe.get("ctrl_drivers", []):
            for key in ud.get("keys", []):
                b = exp["bindings"].get(key) or ud["bindings"].get(key)
                if b is None:
                    continue
                out.append(_row(letter="经 mux%s" % up_no, name=mux_label(b),
                                role="上游mux配方(上游控制驱到载体case)", b=b, key=key))
        return out
    # unknown：来源没解析出来——照样列出，角色写明"无法驱动"
    key = drv.get("key")
    b = (exp["bindings"].get(key) or drv.get("bindings", {}).get(key)) if key else None
    out.append(_row(letter=drv.get("letter") or "?", name=drv.get("base") or "?",
                    role="控制(来源未知,无法驱动)", b=b, width=1, bold=True,
                    key=key, is_control=True))
    return out


def _logic_input_rows(an):
    """logic / 直连寄存器 / dft 改名 / 电平移位 根：每个输入分组一行，行序 = an["groups"] 的次序
    （已由 analysis_norm.order_groups_fortest 排成 for_test 行序）。"""
    rows = []
    groups = an.get("groups") or []
    bindings = an.get("bindings") or {}
    for g in groups:
        b = bindings.get(g.get("rep"))
        role = "控制/选择位" if g.get("is_control") else "数据位"
        rows.append(_row(letter=group_letters(g), name=g.get("label", g["base"]), role=role,
                         b=b, width=g.get("width", 1) or 1, bold=bool(g.get("is_control")),
                         key=g.get("key"), is_control=bool(g.get("is_control"))))
    return rows


def _mux_input_rows(an):
    """mux 根：控制信号（按三来源细分角色）+ 数据寄存器（按物理寄存器收拢，同 for_test 口径）。

    行 = 各控制信号的驱动输入（按 exp['ctrl_drivers'] 三来源细分角色）+ 数据寄存器（d:*）：
      『字母』列：寄存器直出控制显示 Excel 控制列字母(B/C/D/E)；logic 控制显示其表达式字母；
                  mux 级联控制显示"经 mux<N>"；数据寄存器显示它对应的 case 值
      『角色』列：寄存器直出=控制(寄存器直出)；logic=line路径/local路径/模式位(LPBT 不变)；
                  mux 级联=控制(经上游mux驱动)，并把上游载体/上游控制各加一行(上游mux配方)；
                  数据寄存器标"被哪个 case 选中"，RO 线控数据标"数据(线控,force)"
    """
    rows = []
    exp = an.get("expansion")
    if not exp:
        return rows
    grp = an.get("sig")
    cases = list(getattr(grp, "cases", None) or [])
    for drv in exp.get("ctrl_drivers", []):
        rows.extend(mux_ctrl_rows(drv, exp))
    # ── 数据寄存器：按【物理寄存器】收拢（同一寄存器只显一行，与 used_vars 同口径）——
    #    死分支重复行 / LUT 型同源多 case 不再刷成多行，与 for_test 一致(t0~t7 各一行)。
    seen_bases = set()
    for di, key in enumerate(exp.get("data_keys", [])):
        b = exp["bindings"].get(key)
        if b is None:
            continue
        bkey = (b.base or "").lower() or key
        if bkey in seen_bases:
            continue
        seen_bases.add(bkey)
        case_raw = cases[di].case_raw if di < len(cases) else "?"
        # RO 线控数据走 force（线控寄存器），其余是被该 case 选中的本地/lut 寄存器
        if (b.kind or "") == "RO":
            role = "数据(线控,force)"
        else:
            role = "数据寄存器(被该case选中)"
        rows.append(_row(letter="case %s" % case_raw, name=mux_label(b), role=role,
                         b=b, key=key))
    return rows


def input_rows(an):
    """**输入信号表**：an → 每个输入一行。

    行 dict 字段：
      letter        字母 / case 值 / "经 muxN" / "dft页"（Excel 来源坐标）
      name          真实信号名(带位宽)
      width         位宽
      role          控制/选择位 · 数据位 · 门控 iddq · 数据(线控,force) · 上游mux配方 …
      rw            RO / RW / mux / ?
      drive         `RF_WRITE 0x64 bit<<0` / `force ENV_RF.xxx`（怎么把值下进去）
      found_in      机器可读的来源键；found_in_label 老短标签；found_in_text 工程师说法
      trusted       True=在寄存器表里真查到的；False=按命名约定猜出来的名字
      note          解析器留下的补充说明（未解析时是原因）
      resolved / base / key / bold / is_control / is_dft_gate

    覆盖：logic 根、mux 根（含 ctrl/data 口）、直连寄存器根、dft 改名/电平移位根、
    以及带 iddq 门的信号（门单独一行殿后）。
    行序与 for_test 地址序一致（an["groups"] 进来时已排好）。
    """
    if not an:
        return []
    rows = (_mux_input_rows(an) if an.get("kind") == "mux" else _logic_input_rows(an))
    gate = dft_gate_row(an.get("dft_gate"))
    if gate:
        rows.append(gate)
    return rows


# ─────────────────────────── 每列驱动明细 ───────────────────────────
def drive_ctx(an):
    """an → (bindings, used_vars)：算驱动要的两样东西。算不出来返回 (None, None)。"""
    if not an:
        return None, None
    if an.get("kind") == "mux":
        exp = an.get("expansion")
        if not exp:
            return None, None
        return exp.get("bindings"), exp.get("used_vars")
    node = an.get("node")
    if node is None:
        return None, None
    try:
        return an.get("bindings"), E.collect_vars(node)
    except Exception:  # noqa: BLE001
        return None, None


def drive_pair(vec, bindings, used_vars):
    """一条测试向量的 (force 串, RF_WRITE 串)——旧门面表格/CSV/tooltip 的紧凑格式：
    `d_x=16'h1; d_y=16'h0` / `10'h064=16'h3`。算不出来返回 ("", "")。"""
    if vec is None:
        return "", ""
    try:
        forces, writes, _unres = W.compute_drives(vec, bindings, used_vars)
    except Exception:  # noqa: BLE001
        return "", ""
    fs = "; ".join("%s=%s" % (f["wire"], f["hex"]) for f in forces)
    ws = "; ".join("%s=%s" % (w["addr"], w["hex"]) for w in writes)
    return fs, ws


def vector_drives(vec, bindings, used_vars, extra_forces=True):
    """一条测试向量实际下的**每条**驱动语句，一句一行（给 v2 的『每列驱动明细』面板）。

    次序与产物 .sv 一致：先按地址排好的 `RF_WRITE，再所有 force，最后是这条测试额外
    钉上的 force（iddq 门）。没解析出网名的输入也列出来，写明它驱不动。
    """
    if vec is None:
        return []
    try:
        forces, writes, unres = W.compute_drives(vec, bindings, used_vars)
    except Exception:  # noqa: BLE001
        return []
    out = []
    for w in writes:
        fields = "、".join("%s(bit<<%d)=%s" % (f["base"], f["lsb"], f["hex"])
                           for f in w.get("fields", []))
        out.append("`RF_WRITE(%s, %s);%s" % (w["addr"], w["hex"],
                                             ("   ← %s" % fields) if fields else ""))
    for f in forces:
        out.append("force `ENV_RF.%s = %s;   ← %s" % (f["wire"], f["hex"], f["base"]))
    if extra_forces:
        for (wlhs, wval, wwid) in (getattr(vec, "extra_forces", None) or []):
            out.append("force `ENV_RF.%s = %s;   ← iddq 门（本拍钉到 %d）"
                       % (wlhs, W.fmt_hex(wval, max(W.DATA_WIDTH_HEX, wwid or 1)), wval))
    for (ltr, base, note) in unres:
        out.append("✗ %s=%s 驱不动：%s" % (ltr, base or "?", note or "没解析到网"))
    return out


def column_drives(an, col):
    """**每列驱动明细**：an 的第 col 条测试实际下的每条 force / RF_WRITE 语句（list[str]）。

    列头 tooltip 与 v2 的常驻面板都吃它。col 越界 / 分析结果不完整 → 空 list。
    """
    vecs = (an or {}).get("vectors") or []
    if not (0 <= col < len(vecs)):
        return []
    bindings, used = drive_ctx(an)
    if used is None:
        return []
    return vector_drives(vecs[col], bindings, used)


def cell_drive_tip(fs, ws):
    """真值表单元格 tooltip 尾巴上那两行驱动（没有就不占行）。"""
    return ("\nforce: %s" % fs if fs else "") + ("\nRF_WRITE: %s" % ws if ws else "")


def header_drive_tip(fs, ws):
    """列头 tooltip 里的驱动两行（空的显式写 (无)，免得以为漏显示）。"""
    return "force: %s\nRF_WRITE: %s" % (fs or "(无)", ws or "(无)")


# ─────────────────────────── 解析明细（左下面板） ───────────────────────────
def _probe_net(an):
    """断言探针网名：`ENV_RF.<RTL 真网名>。

    注意：an 里【没有】本信号的层级前缀（探针前缀映射在 GUI 会话状态里），
    所以这里给的是不带前缀的名字；配了前缀时实际探针会多一层。"""
    sig = an.get("sig")
    rtl = getattr(sig, "rtl_name", None) or an.get("name") or ""
    return "`%s.%s" % (W.ENV, rtl) if rtl else ""


def _shaky_rows(rows):
    """名字靠不住的输入：来源不是从寄存器表里查到的，或者压根没解析出网 —— 报『找不到网』时先看这几行。
    没有绑定的口（控制由上游 mux 选路决定）不算——它本来就没有单独的网可 force。"""
    return [r for r in rows if r["found_in"] and (not r["trusted"] or not r["resolved"])]


def _input_detail_lines(rows):
    """逐输入解析明细：每条输入 2~3 行（谁 → 怎么驱动 → 名字是查到的还是猜的）。"""
    lines = []
    pad = max([len(r["letter"] or "-") for r in rows] or [1])
    for r in rows:
        lines.append("  %s  %s  [%s · %s]"
                     % ((r["letter"] or "-").ljust(pad), r["name"], r["role"], r["rw"]))
        lines.append("       驱动: %s" % (r["drive"] or "(无)"))
        src = r.get("found_in_text") or r.get("found_in") or ""
        if src:
            mark = "✔ 查到的" if r["trusted"] else "⚠ 猜的"
            lines.append("       来源: %s —— %s" % (mark, src))
        elif r["rw"] == "mux":
            lines.append("       来源: 这一口由上游 mux 选路决定，本身没有单独的网可 force；"
                         "要驱它看下面『上游mux配方』那几行")
        if r.get("note"):
            lines.append("       说明: %s" % scrub_terms(r["note"]))
    return lines


def legacy_resolve_detail(sig, a, status_label=None):
    """『排查(旧)』左下『解析明细』的**原文**（吃 generator.analyze_signal / analyze_mux_group 的
    结果 dict，不是 an）。逐字保持历史文案——旧门面还在用，改文案会打断用户的肌肉记忆。

    status_label：状态键 → 中文标签的映射（gui.STATUS_LABEL），不给就原样显示状态键。
    v2 请改用 `resolve_detail(an)`：同一件事，但吃 an 且文案按 Design prompt §8.6 收拾过。
    """
    lab = status_label or {}
    lines = ["信号: %s   (assert_%s, %s, top_output=%s)"
             % (sig.out_name, sig.assert_id, sig.suffix, sig.top_output),
             "表达式: %s" % sig.expr,
             "状态: %s" % lab.get(a["status"], a["status"]),
             "断言: assert (%s == <期望>)" % a["out_net"], ""]
    if a["error"]:
        lines.append("解析错误: %s" % a["error"])
    for inp in a["inputs"]:
        flag = "" if inp["resolved"] else "  ✗"
        src = FOUND_IN_LABEL.get(inp["found_in"], inp["found_in"])
        lines.append("  %s=%s  [%s/%s]%s  ->  %s"
                     % (inp["letter"], inp["base"], inp["kind"], src, flag, inp["net"]))
        if inp["note"]:
            lines.append("        note: %s" % inp["note"])
    lines.append("")
    lines.append("提示: elaboration 报 CUVUNF 找不到 net 时，对比这里的 force/输出 net 名是否真存在于 ENV_RF 层；"
                 "⚠wire兜底/✗未解析 的最可疑。")
    return "\n".join(lines)


def resolve_detail(an):
    """**解析明细**：一整块给 IC 工程师看的文本（信号 / 逻辑式 / 状态 / 断言探针 /
    逐输入解析 / 找不到网时的排查步骤）。an 为空 → 空串。

    术语按 Design prompt §8.6：CUVUNF 首次出现展开成「仿真器找不到这根网」，
    不出现 cone / 记账 / prefixed-wire 这类裸词。
    """
    if not an:
        return ""
    sig = an.get("sig")
    name = an.get("name") or getattr(sig, "out_name", "") or "?"
    width = an.get("out_width") or getattr(sig, "out_width", None) or 1
    aid = getattr(sig, "assert_id", None)
    head = "信号 %s（输出 %d bit%s）" % (name, width,
                                      "，断言 assert_%s" % aid if aid else "")
    lines = [head]
    expr = getattr(sig, "expr", "") or ""
    if expr:
        lines.append("逻辑式   %s = %s" % (getattr(sig, "out_base", None) or "out", expr))
    st = an.get("status") or ""
    lines.append("状态     %s" % AN_STATUS_TEXT.get(st, st))
    probe = _probe_net(an)
    if probe:
        lines.append("断言探针 %s   ← 仿真里比对的就是这根网" % probe)
    if an.get("note"):
        lines.append("说明     %s" % scrub_terms(an["note"]))
    for msg in (an.get("issues") or []):
        lines.append("⚠ %s" % scrub_terms(msg))

    rows = input_rows(an)
    lines.append("")
    lines.append("输入逐条解析（%d 条）" % len(rows))
    if rows:
        lines.extend(_input_detail_lines(rows))
    else:
        lines.append("  （这个信号没有可驱动的输入——多半是只读回读，或者表达式没解析出来）")

    shaky = _shaky_rows(rows)
    lines.append("")
    lines.append("%s时，按这三步查：" % CUVUNF_FIRST)
    lines.append("  1. 把上面的探针名 / force 名，拿去 nets.txt（工具可导出）里搜一下真有没有这根网；")
    lines.append("  2. 搜不到 → 它多半埋在某个子模块里：在红区跑 scan_rtl 扫出层级前缀，"
                 "把前缀映射导回工具重新生成；")
    lines.append("  3. 搜得到但名字对不上 → 是 Excel 里的输入名/输出名写法与 RTL 不一致，"
                 "改表，或者用『强制 force 信号』指定真名。")
    if shaky:
        lines.append("  最可疑的是上面标了『⚠ 猜的』或驱动写着『✗未解析』的那 %d 行：%s"
                     % (len(shaky), "、".join(r["name"] for r in shaky[:6])
                        + ("…" if len(shaky) > 6 else "")))
    elif rows:
        lines.append("  这个信号的输入全是从寄存器表里查到的，名字不是猜的——"
                     "真报找不到，优先怀疑输出探针那一侧。")
    else:
        lines.append("  这个信号没有要 force 的输入，真报找不到就是输出探针那根网的名字或层级不对。")
    return "\n".join(lines)
