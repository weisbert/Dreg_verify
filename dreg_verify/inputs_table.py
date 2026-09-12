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
    "vheader_label", "vheader_short", "vheader_display", "mux_label", "dft_gate_row", "mux_ctrl_rows",
    "input_rows", "row_cells", "drive_ctx", "drive_pair", "vector_drives", "column_drives",
    "cell_drive_tip", "header_drive_tip", "gate_pin", "legacy_resolve_detail", "resolve_detail",
    "needs_prefix_rows", "shaky_rows", "CLAIMS_TEXT", "PAGE_NAME_TEXT",
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

# 探针清单的定版说法（术语表 T 数组那一行；界面上到处都是这一个写法，不另起一套）
CLAIMS_TEXT = "探针清单（claims.json）"

#: `tmm` / `regmap` 出现在**页名位置**（「tmm 页」「regmap 页」）时的写法（R3-06 ③）。
#: 它们是用户在 Excel 标签上真看得见的页名 —— 直接换掉就成了「寄存器定义表 页缺 addr 列」，
#: 换回原页名又是裸用术语。所以两样都给：中文说法 + 括号里的真页名（= 就地解释）。
PAGE_NAME_TEXT = {"tmm": "寄存器定义表（tmm 页）", "regmap": "寄存器地址映射表（regmap 页）"}

#: **已经是人话**的定版串：过 scrub 时先摘成哨兵、替换完再放回去（R3-06 ①）。
#: 这些串本身就含「tmm 页」「claims.json」「错误码 CUVUNF」这类词 —— 它们是**说明**，
#: 不是待替换的术语。同一段文本会被 scrub 两次（`resolve_detail` 内部一次、
#: `detail_header.scrub_detail` 整段再一次），没有保护第二次就会把说明本身也换掉。
#: 按长度降序摘，长串先走（短串是长串的一部分时不会先把长串切碎）。
_PROTECTED = tuple(sorted(
    set(FOUND_IN_TEXT.values()) | set(PAGE_NAME_TEXT.values()) | {CUVUNF_FIRST, CLAIMS_TEXT},
    key=len, reverse=True))
_SENTINEL_FMT = "\x00t%d\x00"
#: 定版串 → 它的哨兵。**规则的替换文本也用哨兵**：替换是顺序执行的，
#: 「regmap 页 → 寄存器地址映射表（regmap 页）」插进去之后，下一条规则
#: 「regmap → 寄存器地址映射表」会再吃掉括号里那个真页名（→「（寄存器地址映射表 页）」）。
_SENT = {s: _SENTINEL_FMT % i for i, s in enumerate(_PROTECTED)}

# 术语替换表（Design prompt §8.6）：后端给的 note / issues 里还留着一堆内部说法
# （『无 cone，跳过+记账』这种），照搬到界面上就是术语裸露。往界面送之前过一遍这张表。
# ASCII 词用词边界匹配，免得把信号名里的同名片段也改了（d_tmm_xxx 不能动）。
#
# R3-06 三处修正：
#   ① 规则**带前后文**。以前 `wire 兜底 → 按命名约定当线网处理` 是裸替换，引擎那句
#      「按 wire 兜底 force 裸名 X」于是变成「按 按命名约定当线网处理 force 裸名 X」
#      （多一个「按」、多一个「处理」）。现在「按…force」「按…处理」各有各的规则。
#   ② `claims` 的译法与术语表 T 数组统一成 `CLAIMS_TEXT`（以前这里写「认领清单」、
#      术语表写「探针清单（claims.json）」，同一个东西两个名字）。
#   ③ `tmm` / `regmap` 出现在**页名位置**（「tmm 页」「Excel 的 regmap 页」）时换成
#      `PAGE_NAME_TEXT`：中文说法 + 括号里的**真页名**。直接换掉页名会变成
#      「寄存器定义表 页缺 addr 列」（对不上用户在 Excel 标签上看到的东西），
#      原样留着又是裸用术语 —— 两样都给，就是「就地解释」。
_TERM_RULES = [
    (re.compile(r"跳过\s*\+\s*记账"), "跳过，只记录不产生断言"),
    (re.compile(r"无\s*cone(?![A-Za-z0-9_])", re.I), "没有可展开的上游"),
    (re.compile(r"(?<![A-Za-z0-9_])cone\s*展开", re.I), "从输出往回展开到源寄存器"),
    (re.compile(r"(?<![A-Za-z0-9_])cone(?![A-Za-z0-9_])", re.I), "从输出往回展到源寄存器"),
    (re.compile(r"记账"), "只记录、不产生断言"),
    (re.compile(r"账目"), "只记录不产生断言的那份清单"),
    (re.compile(r"(?<![A-Za-z0-9_])prefixed-wire(?![A-Za-z0-9_])", re.I), "带层级前缀的线网"),
    (re.compile(r"按\s*wire\s*兜底\s*(?=force|驱动)"), "按命名约定当线网 "),
    (re.compile(r"按\s*wire\s*兜底\s*处理"), "按命名约定当线网处理"),
    (re.compile(r"wire\s*兜底"), "按命名约定当线网处理"),
    (re.compile(r"(?<![A-Za-z0-9_])regmap\s*页", re.I), _SENT[PAGE_NAME_TEXT["regmap"]]),
    (re.compile(r"(?<![A-Za-z0-9_])tmm\s*页", re.I), _SENT[PAGE_NAME_TEXT["tmm"]]),
    (re.compile(r"(?<![A-Za-z0-9_])regmap(?![A-Za-z0-9_])", re.I), "寄存器地址映射表"),
    (re.compile(r"(?<![A-Za-z0-9_])tmm(?![A-Za-z0-9_])", re.I), "寄存器定义表"),
    (re.compile(r"(?<![A-Za-z0-9_])claims(?![A-Za-z0-9_.])", re.I), _SENT[CLAIMS_TEXT]),
    (re.compile(r"必\s*CUVUNF"), "必然找不到这根网"),
    (re.compile(r"(?<![A-Za-z0-9_])CUVUNF(?![A-Za-z0-9_])"), "找不到这根网"),
]

def scrub_terms(text):
    """把后端文本（an 的 note / issues、解析器留的说明）里的内部术语换成工程师说法。

    只动这几个 §8.6 点名的词，其余原样——信号名、地址、位段都不能碰。
    已是人话的定版串（`_PROTECTED`）先摘成哨兵，**不会被二次替换**；
    规则自己插进去的定版串也是哨兵，收尾时一起还原。"""
    out = text or ""
    if not out:
        return out
    for s in _PROTECTED:                      # 长串先摘（短串是长串一部分时不会把长串切碎）
        if s in out:
            out = out.replace(s, _SENT[s])
    for rx, rep in _TERM_RULES:
        out = rx.sub(rep, out)
    for s in _PROTECTED:                      # 摘出去的 + 规则新插进来的，一起放回
        sent = _SENT[s]
        if sent in out:
            out = out.replace(sent, s)
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


# 真值表冻结列行标签（Design 冻结列格式 `<真名>　(角色 · 端口)`，全角空格分隔）
VHEADER_DISPLAY_FMT = "%s　(%s)"
ROLE_CTRL = "控制位"
ROLE_DATA = "数据位"
ROLE_DFT_GATE = "DFT 门(iddq)"


def _mux_port_of(an, key):
    """mux 输入的**端口名** `mux<组号>.ctrl<i>` / `mux<组号>.d<i>`（与 mux_gen 合成子树挂的来源标签同名）。

    端口是「这根信号接在这个 mux 的哪个口上」，比字母更能对上 Excel 的 B~E / case 行；
    定不出来（不是 mux / 没 expansion / key 不在表里）→ 空串，调用方退回字母。"""
    exp = (an or {}).get("expansion") or {}
    if not key or not exp:
        return ""
    no = getattr((an or {}).get("sig"), "group_no", None)
    if no is None:
        return ""
    for i, k in enumerate(exp.get("data_keys") or []):
        if k == key:
            return "mux%s.d%d" % (no, i)
    for i, drv in enumerate(exp.get("ctrl_drivers") or []):
        if key == drv.get("key") or key in (drv.get("keys") or []):
            return "mux%s.ctrl%d" % (no, i)
    return ""


def vheader_display(g, an=None):
    """真值表**冻结列**行标签（Design 冻结列格式）：`<真名>　(角色 · 端口)`。

    例：`d_bt_lp_bt_mode_sel[2:0]　(控制位 · mux3.ctrl0)` / `d_xo_freq_sel　(数据位 · A,B)`。
    角色 = 控制位 / 数据位 / DFT 门(iddq)；端口 = mux 的 `ctrl0`/`d1` 口（带组号）或 logic 的表达式字母。
    `vheader_short`（旧门面真值表行表头）**一字不动**——这是并列的新格式，不是替换。

    `g` 吃两种行：`input_groups` 的分组 dict（logic/register 根）与 `input_rows` 的行 dict
    （mux 根 / DFT 门行）——v2 真值表 mux 时的输入行来自后者，两边喂同一个函数免得格式漂。
    `an` 只在 mux 时用得上（端口要查 expansion 的 ctrl_drivers/data_keys）；不给 → 退回字母。
    """
    g = g or {}
    # 真名一律取【带位宽】的那个：分组 dict 是 label(缺则 base)，输入行 dict 是 name(base 已剥了位宽)
    name = g.get("label") or g.get("name") or g.get("base") or "?"
    if g.get("is_dft_gate"):
        role = ROLE_DFT_GATE
    elif g.get("is_control"):
        role = ROLE_CTRL
    else:
        role = ROLE_DATA
    port = _mux_port_of(an, g.get("key")) or group_letters(g) or str(g.get("letter") or "")
    return VHEADER_DISPLAY_FMT % (name, "%s · %s" % (role, port) if port else role)


def mux_label(b):
    """绑定 → 『信号(位宽)』列文本。"""
    return b.base + ("[%d:0]" % (b.width - 1) if b.width > 1 else "")


# ─────────────────────────── 行构造 ───────────────────────────
def _row(letter, name, role, b, width=None, bold=False, key=None,
         is_control=False, is_dft_gate=False, kind=None, drive=None, note=None):
    """统一的输入行 dict。`b` 是绑定(可 None)；kind/drive/note 可显式覆盖
    (mux 级联控制行没有绑定时给占位文案；有绑定时 note 要补上「经上游 muxN 选路」)。"""
    bk, bd = binding_meta(b)
    k = bk if kind is None else kind
    d = bd if drive is None else drive
    found_in = getattr(b, "found_in", "") or ""
    return {
        "letter": letter,
        "name": name,
        # .sv 里真正会 force 的那根网（`force ENV_RF.<net>` 的 net）。界面上「缺哪根网的
        # 层级前缀」要点的就是它 —— 以前只有拼在 `drive` 文本里的一份，没法单独取。
        "net": (getattr(b, "wire_lhs", "") or "") if b is not None else "",
        "width": width if width is not None else (getattr(b, "width", 1) or 1),
        "role": role,
        "rw": k,                       # RO / RW / ? / mux
        "drive": d,
        "found_in": found_in,
        "found_in_label": FOUND_IN_LABEL.get(found_in, found_in),
        "found_in_text": FOUND_IN_TEXT.get(found_in, found_in),
        "trusted": found_in in TRUSTED_FOUND_IN,
        # N10（C-058/C-059）：Design 只有一个「猜名」标记，后端的 found_in 分得更细——把两件
        # **不同的事**分开标，别再让界面自己拿 trusted 一个布尔去猜：
        #   needs_prefix = 网确实存在、但埋在子模块里，不跑 scan_rtl 配前缀 force 必 CUVUNF 被跳过；
        #   guessed      = 名字本身就是按命名约定猜的（不在 tmm/regmap 里查到），可能根本没这根网。
        # 两者互斥（needs-prefix/mux-output 归前者），都为假 = 真查到的可信名。
        "needs_prefix": found_in in ("needs-prefix", "mux-output"),
        "guessed": bool(found_in) and found_in not in TRUSTED_FOUND_IN
                   and found_in not in ("needs-prefix", "mux-output"),
        "note": (getattr(b, "note", "") or "") if note is None else note,
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


def _cascade_ctrl_note(up_no, b):
    """mux 级联控制行的 note：先点明「经上游 muxN 选路」，再接解析器留下的原话。

    §7-3 之前这一行的**驱动**列写的就是占位文案「经上游 muxN 输出选路」；现在驱动列让位给
    .sv 里真正会 force 的那根衔接网，选路这件事就落到 note 上——它不能丢：用户要知道这一口
    不是自己有个寄存器可写，而是被上游那个 mux 选出来的（要驱它得照『上游mux配方』那几行做）。"""
    head = "经上游 mux%s 选路" % up_no
    tail = (getattr(b, "note", "") or "").strip()
    return "%s；%s" % (head, tail) if tail else head


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
        # §7-3：上游 mux 输出网的 Binding（C0-a 在 mux_gen.expand_mux_group 里补的 additive 键）——
        # 有它就**照它写**（唯一写法）：『信号』列给上游 mux 的输出网名、『类型』RO、
        # 『驱动』`force ENV_RF.<衔接网>` —— 这才是 .sv 里真正会 force 的那根网，也让 N10 的
        # needs_prefix 标得上。「经上游 muxN 选路」这件事不丢，退到 note 里（见 _cascade_ctrl_note）。
        # 没有 binding（旧展开数据）→ 原样走下面那行写死的占位文案，不编一个网名出来。
        cb = drv.get("binding")
        if cb is not None:
            out.append(_row(letter=drv.get("letter") or "?", name=mux_label(cb),
                            role="控制(经上游mux%s驱动)" % up_no, b=cb, bold=True,
                            key=drv.get("key"), is_control=True,
                            note=_cascade_ctrl_note(up_no, cb)))
        else:
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
    # ── §7-4：used_vars 里有、但三来源驱动器与数据口都没落到行上的控制键（C0-a 在 analysis_norm
    #    里算好的 an["ctrl_keys_missing"]）。典型是 LPBT 形态的 line 路径键——真值表按 used_vars
    #    出行，输入表少了它就会「真值表 6 行、输入表 5 行」对不上（C-060）。殿后补齐，不改前面行序。
    seen_keys = {r.get("key") for r in rows if r.get("key")}
    for key in (an.get("ctrl_keys_missing") or []):
        if key in seen_keys:
            continue
        seen_keys.add(key)
        b = (exp.get("bindings") or {}).get(key)
        rows.append(_row(letter=str(key).split(":")[-1],
                         name=(mux_label(b) if b is not None else str(key)),
                         role="控制(line 路径，未展开)", b=b, bold=True,
                         key=key, is_control=True))
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
    """**名字本身靠不住**的输入：按命名约定猜出来的名，或者压根没解析出网 —— 报『找不到这根网』
    时先看这几行。两类【不】算在内：
      · 埋在子模块、只差一层层级前缀的衔接网（名字是对的，另起一行说，见 `_needs_prefix_rows`）——
        N10 已把「猜名」和「缺前缀」拆成两件事，这里不许再合回去；
      · 没有绑定的口（控制由上游 mux 选路决定）——它本来就没有单独的网可 force。"""
    return [r for r in rows if r.get("guessed") or (r["found_in"] and not r["resolved"])]


def _needs_prefix_rows(rows):
    """网名对、但**埋在子模块里**的输入（上游 mux 输出衔接网 / 级联内部网）：只差跑 scan_rtl
    配一层层级前缀就 force 得到；没配则整组跳过。名字不是猜的，所以不进『最可疑』那一行。"""
    return [r for r in rows if r.get("needs_prefix") and r["resolved"]]


def needs_prefix_rows(an):
    """an → 缺层级前缀、force 不到的那几条输入行（公开口，v2 的跳过原因 / 原因块点名用）。

    「跳过必须点名」这条规矩要点的是**哪几根网**：以前只有解析明细里那一行拼过它们，
    跳过原因与行内原因块都只能说「某根输入网埋在子模块里」（R3-01 / R3-02）。"""
    return _needs_prefix_rows(input_rows(an or {}))


def shaky_rows(an):
    """an → **名字本身靠不住**的那几条输入行（猜名的 / 没解析出网）。公开口，同 `needs_prefix_rows`。"""
    return _shaky_rows(input_rows(an or {}))


def _source_mark(row):
    """来源标记（三档，与 N10 的 trusted / needs_prefix / guessed 一一对应）：
    表里真查到的 → ✔；名字对但埋子模块、缺层级前缀 → ⚠ 要配层级前缀；其余 → ⚠ 猜的。"""
    if row["trusted"]:
        return "✔ 查到的"
    return "⚠ 要配层级前缀" if row.get("needs_prefix") else "⚠ 猜的"


def _input_detail_lines(rows):
    """逐输入解析明细：每条输入 2~3 行（谁 → 怎么驱动 → 名字是查到的、缺前缀的、还是猜的）。"""
    lines = []
    pad = max([len(r["letter"] or "-") for r in rows] or [1])
    for r in rows:
        lines.append("  %s  %s  [%s · %s]"
                     % ((r["letter"] or "-").ljust(pad), r["name"], r["role"], r["rw"]))
        lines.append("       驱动: %s" % (r["drive"] or "(无)"))
        src = r.get("found_in_text") or r.get("found_in") or ""
        if src:
            lines.append("       来源: %s —— %s" % (_source_mark(r), src))
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


def resolve_detail(an, status_text=None):
    """**解析明细**：一整块给 IC 工程师看的文本（信号 / 逻辑式 / 状态 / 断言探针 /
    逐输入解析 / 找不到网时的排查步骤）。an 为空 → 空串。

    术语按 Design prompt §8.6：CUVUNF 首次出现展开成「仿真器找不到这根网」，
    不出现 cone / 记账 / prefixed-wire 这类裸词。

    `status_text`：「状态」那一行写什么。不给就退到 `AN_STATUS_TEXT` 的**四档**
    （`an["status"]`）—— 而清单徽标是**八档**（`terms.status_key_of`），同一块屏幕上于是
    出现「解析明细：状态 可建」对着「徽标：⚠ 输入缺前缀·跳过」（R3-07）。v2 的调用方
    （`ui/detail_header`）一律把八档那一句传进来；四档只留给旧门面与单测兜底。
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
    lines.append("状态     %s" % (status_text or AN_STATUS_TEXT.get(st, st)))
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
    needs_prefix = _needs_prefix_rows(rows)
    lines.append("")
    lines.append("%s时，按这三步查：" % CUVUNF_FIRST)
    lines.append("  1. 把上面的探针名 / force 名，拿去 nets.txt（工具可导出）里搜一下真有没有这根网；")
    lines.append("  2. 搜不到 → 它多半埋在某个子模块里：在红区跑 scan_rtl 扫出层级前缀，"
                 "把前缀映射导回工具重新生成；")
    lines.append("  3. 搜得到但名字对不上 → 是 Excel 里的输入名/输出名写法与 RTL 不一致，"
                 "改表，或者用『强制 force 信号』指定真名。")
    if shaky:
        lines.append("  最可疑的是上面标了『⚠ 猜的』或驱动写着『✗未解析』的那 %d 行：%s"
                     % (len(shaky), _name_list(shaky)))
    if needs_prefix:
        # 与上一行是两回事：这几根网**名字没问题**，只是不在顶层——第 2 步（配前缀）直接对症，
        # 不必回头怀疑 Excel 里的名字写错了。
        lines.append("  另有 %d 行是埋在子模块里的衔接网（名字没问题，按上面第 2 步配好层级前缀"
                     "就 force 得到；没配则整组跳过）：%s"
                     % (len(needs_prefix), _name_list(needs_prefix)))
    if not shaky and not needs_prefix:
        if rows:
            lines.append("  这个信号的输入全是从寄存器表里查到的，名字不是猜的——"
                         "真报找不到，优先怀疑输出探针那一侧。")
        else:
            lines.append("  这个信号没有要 force 的输入，真报找不到就是输出探针那根网的名字或层级不对。")
    return "\n".join(lines)


def _name_list(rows, cap=6):
    """挑头几行的信号名列成一串（超出就省略号，别把整张表抄进一行里）。"""
    return "、".join(r["name"] for r in rows[:cap]) + ("…" if len(rows) > cap else "")
