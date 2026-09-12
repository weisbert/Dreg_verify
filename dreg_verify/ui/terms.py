# -*- coding: utf-8 -*-
"""terms.py —— 术语替换表 + 状态档文案 + 行内原因块模板 + 全部界面静态文案（唯一定义）。

来源：
  · V2Spec §6 术语表 T 数组 11 行（原文照抄）+「可以直接用的词」+ 文案红线 4 条；
  · Design 主画板 D 数组的 4 条行内原因实例（模板句式照抄）；
  · Design 各区文案（顶栏 / 空态 / 载入态 / 清单 / 标题栏 / 真值表 / 电路图 / .sv / 右栏 / 状态栏 /
    导出中心 / 完成弹层 / 诊断抽屉 / 覆盖度弹层），主控裁决①（快捷键）、⑪（步骤 2 措辞）、⑫（不写示例路径）、
    ⑬（「版本」不写 git）、⑯（导出前点名）已落进文案。

规则：
  · 视图模块里**不写任何用户可见的裸字符串**，全部从本模块取；
  · 一切显示后端文本（an.note / issues / 解析明细 / 跳过原因）的地方先过 `inputs_table.scrub_terms`
    （本模块 `scrub` 只是它的别名，方便 ui 内统一 import）；
  · 术语 FORBIDDEN 里的词不得裸出现在界面上（tests/test_ui_terms_scan.py（C5）扫 ui/*.py 字面量）。
"""

import os
import re

from dreg_verify.inputs_table import scrub_terms as scrub    # noqa: F401  统一入口别名
from dreg_verify.inputs_table import STATUS_HELP, AN_STATUS_TEXT, FOUND_IN_TEXT, CUVUNF_FIRST  # noqa: F401

# ═════════ 一、术语替换表（V2Spec §6 T 数组，原文）═════════
#: (禁止裸用, 界面上写成, 首次出现时的完整说法)
TERMS = (
    ("cone / cone 展开", "逐层展开 · 展开链", "从顶层输出往回展开到源寄存器"),
    ("F0–F4", "逻辑类型：直连寄存器 / 布尔·位运算 / 选路 / 门控 iddq", "覆盖度控件里直接写中文类型名，不出现 F 编号"),
    ("缝", "跨页边界（logic ↔ mux）", "这个信号的输入来自另一页"),
    ("prefixed-wire", "需要层级前缀的网", "这根网不在 DUT 顶层，force 时要带层级前缀"),
    ("wire 兜底", "表里查不到，按线网处理", "表里没查到这个名字，按线网 force；名字对不对要扫一次"),
    ("记账", "只记录、不产生断言", "这条只记录驱动，不生成断言"),
    ("claims", "探针清单（claims.json）", "每根探针探哪根网、force/写哪些网、名字是查到的还是猜的"),
    ("tmm", "地址表（total_memory_map 页）", "Excel 的 total_memory_map 页"),
    ("regmap", "寄存器表（regmap 页）", "Excel 的 regmap 页"),
    ("CUVUNF", "仿真器找不到这根网", "仿真报错 CUVUNF：仿真器找不到这根网（elaboration 失败）"),
    ("假绿", "驱不动，断言必过", "寄存器字段比输出窄，高位永远驱不动，断言过了也不说明逻辑对"),
)
#: 禁用词的机器可读形式（扫描字面量用；「假绿」允许出现在状态标签里但必须带 tooltip 解释，见 STATUS）
FORBIDDEN = ("cone", "F0", "F1", "F2", "F3", "F4", "缝", "prefixed-wire", "wire 兜底", "wire兜底",
             "记账", "claims", "tmm", "regmap", "CUVUNF", "假绿")
#: 可以直接用的词（V2Spec §6）
ALLOWED = ("ENV_RF", "force", "RF_WRITE", "断言", "RO/RW", "地址", "位段", "iddq", "DFT", "mux", "case",
           "探针", "层级前缀", "for_test", "nets.txt")
#: 文案红线（V2Spec §6；第 1 条按裁决⑪加例外注脚）
REDLINES = (
    "不出现仓库路径、git、本工具的 CLI 命令、「请把下面发给维护者」（例外：红区仿真服务器上必须亲手敲的 scan_rtl.py）",
    "跳过/过滤的东西一律先点名字和原因，计数放后面",
    "给真实层级路径示例，不给抽象描述",
    "表格行表头直接用真实信号名，字母对照放输入信号表和 tooltip",
)

# ═════════ 二、状态档（清单状态列 + 详情徽标）═════════
#: 状态键（an["status_detail"]，C0 §7 缺口 #2 补齐后由引擎给；引擎只给 status 四档时按 STATUS_FALLBACK 映射）
STATUS_KEYS = ("clean", "wire-fallback", "needs-prefix", "risky-generated", "bare-probe", "false-green",
               "spec-collision", "parse-err", "unresolved", "skip", "error", "pending")
#: 键 → (清单状态列标签, 色档 ok/warn/bad/note, 悬停解释)。8 档解释 = C-016（clean/wire兜底/未解析/解析错/
#: 规格冲突/输入缺前缀·跳过/输出裸名·已生成/字段太窄·假绿）+ C-041 第 6 档「输入缺前缀·已强制生成」
#: + RO/pending。⚠ 前两档的「**输入**」二字不能省（主控裁决，v1 就这么写）：它用来把
#: 「输入侧硬阻断（force 不到，跳过 / 强制生成）」与「输出侧裸名探针（bare-probe，不阻断）」
#: 分开 —— 两档都写「缺前缀」的话，清单上看不出这一行到底要不要处理。
STATUS = {
    "clean": ("可建", "ok", STATUS_HELP["clean"]),
    "wire-fallback": ("⚠ 名字是猜的·已生成", "warn",
                      "有输入表里没查到，按命名约定当线网 force 了——名字对不对要用 nets.txt 扫一次才知道。"),
    "needs-prefix": ("⚠ 输入缺前缀·跳过", "warn",
                     "要 force 的某根输入网埋在子模块里，没配层级前缀 force 必然找不到这根网，所以整组跳过。"
                     "先跑 scan_rtl 配好探针前缀，这组才会生成。"),
    "risky-generated": ("⚠ 输入缺前缀·已强制生成", "warn",
                        "「缺前缀是否强制生成」开着：这组照样进了 .sv，裸名 force 交给仿真验证；"
                        "仿真过 = 此设计不需前缀，报找不到网则跑 scan_rtl 配前缀重生成。"),
    "bare-probe": ("输出裸名·已生成", "note",
                   "输出名在寄存器表里没查到，按命名约定直接用了裸名探针；断言能生成，"
                   "只有仿真真报找不到这根网时再配前缀（不是错误）。"),
    "false-green": ("⚠ 字段太窄·假绿", "warn",
                    "结构全解析通了，只是数据寄存器字段太窄、装不下每条 case 的互异值——硬生成会变成"
                    "「接错路也 PASS」的假测试（假绿 = 驱不动，断言必过）。工具保护性跳过；要验得加宽字段或拆组。"),
    "spec-collision": ("✗ 规格冲突·待核对", "bad",
                       "mux 页有两行控制选择值相同却选不同数据源——同一选择值 RTL 只能输出一个，已整组跳过。"
                       "行内原因块点名了撞车的 Excel 行号，请对应 designer 核对改表；工具不猜。"),
    "parse-err": ("✗ 解析出错", "bad", "表达式或输入名有问题，看行内原因块的告警全文。"),
    "unresolved": ("✗ 未解析", "bad", "有输入没解析出网名——照这样生成，仿真器会报找不到这根网。"),
    "skip": ("↷ 只读回读·跳过", "note", "这是只读回读信号，写不进去也就没什么好验的；只记录、不产生断言。"),
    "error": ("✗ 解析异常", "bad", "分析这一个信号时出错（其余信号不受影响）；行内原因块留有全文。"),
    "pending": ("分析中", "note", "还在后台展开；清单已可点，展开完会自动更新。"),
}
#: 引擎只给 status 四档（ok/skip/unresolved/error）时的兜底映射
STATUS_FALLBACK = {"ok": "clean", "skip": "skip", "unresolved": "unresolved", "error": "error"}
#: 「全部状态」筛选下拉的三项（C-029）
STATUS_FILTER_ITEMS = ("全部状态", "仅可建", "仅有问题")
#: 色档里算「有问题」的两档（清单状态列橙 / 红）
PROBLEM_TONES = ("warn", "bad")
#: note 色档不是一档而是三样东西，按 **v1 的四档 status** 分家（D1 对抗 review P-13）：
#:   · `bare-probe` 在 v1 里 status=ok  → 算「可建」（输出名按命名约定猜的，断言照样生成）
#:   · `skip`       在 v1 里 status=skip → 算「有问题」（只读回读根，不产断言）
#:   · `pending`    v1 根本没有这一档   → 两边都不进（还在后台展开，别让它在筛选下闪进闪出）
#: 只按色档判的话，btlp 上「仅有问题」筛出 0 条、「仅可建」漏掉 bare-probe 那个信号。
NOTE_AS_OK_KEYS = ("bare-probe",)
NOTE_AS_PROBLEM_KEYS = ("skip",)
#: 状态筛选下拉的取值（第 0 项 = 不筛）；筛选行发的 `filters["status"]` 就是这两个键之一或 ""。
#: 中文项也一并认：两处各写各的字面量时，漏一个就是「筛了等于没筛」，而且界面上看不出来
#: （行数不变，用户只会以为本来就这么多）。
STATUS_FILTER_OK_KEYS = ("ok", "clean", STATUS_FILTER_ITEMS[1])
STATUS_FILTER_PROBLEM_KEYS = ("issues", "problem", STATUS_FILTER_ITEMS[2])


def status_key_of(model):
    """模型行 → `STATUS` 的键（C-014 / C-016 / C-041）。**四档→八档的映射只有这一处。**

    引擎给了 `status_detail`（八档）就用它；只给四档 `status` 时按 `STATUS_FALLBACK` 映射。
    清单（`signal_list`）、筛选行（`filter_bar`）、详情标题栏（`detail_header`）共用本函数
    —— 三边各带一份 5 行判定时，`risky-generated` 这类行会在「筛选说可建、清单说有问题」之间打架
    （主控裁决：搬进 terms 这片共享叶子，只写一处）。"""
    m = model or {}
    key = str(m.get("status_detail") or "").strip()
    if key in STATUS:
        return key
    st = str(m.get("status") or "").strip()
    if st in STATUS_FALLBACK:
        return STATUS_FALLBACK[st]
    return st if st in STATUS else "error"


#: 解析明细「状态」那一行的写法（R3-07）：标签 + 悬停解释，与清单徽标同一档。
STATUS_DETAIL_LINE_FMT = "{label} —— {help}"


def status_detail_text(model):
    """模型行 / an → 解析明细「状态」那一行。**与清单徽标同一档**（八档，`status_key_of`）。

    以前解析明细走的是 `inputs_table.AN_STATUS_TEXT`（引擎的**四档** status），于是同一块
    屏幕上出现「解析明细：状态 可建」对着「徽标：⚠ 输入缺前缀·跳过」—— 两个数、两句话，
    用户只能自己猜哪个算数（R3-07）。"""
    key = status_key_of(model)
    label, _tone, help_text = STATUS[key]
    return STATUS_DETAIL_LINE_FMT.format(label=label, help=help_text)


def tone_of(model):
    """模型行 → 色档 `ok` / `warn` / `bad` / `note`（= `STATUS[status_key_of(m)][1]`）。"""
    return STATUS[status_key_of(model)][1]


def match_status(model, status_filter):
    """这一行是否通过「全部状态 / 仅可建 / 仅有问题」筛选（C-029）。

    判据就是清单状态列上写着的那一档：可建 = 色档 ok，有问题 = 色档 warn/bad。
    空串（或任何别的值）= 不筛。筛选行与清单 proxy 共用本函数 —— 以前筛选行按四档
    `status` 判、清单按八档 tone 判，`risky-generated`（status 仍是 "ok"）这类行
    会被筛选行算进「仅可建」、被清单算进「有问题」，同一块屏幕上两个数。

    note 色档（`bare-probe` / `skip` / `pending`）按 v1 的四档 status 分家，见
    `NOTE_AS_OK_KEYS` / `NOTE_AS_PROBLEM_KEYS`：只按色档判的话这三档两边都不进，
    btlp 上「仅有问题」筛出 0 条、「仅可建」少一个信号（P-13）。"""
    s = str(status_filter or "")
    if s in STATUS_FILTER_OK_KEYS:
        return tone_of(model) == "ok" or status_key_of(model) in NOTE_AS_OK_KEYS
    if s in STATUS_FILTER_PROBLEM_KEYS:
        return tone_of(model) in PROBLEM_TONES or status_key_of(model) in NOTE_AS_PROBLEM_KEYS
    return True

# ═════════ 三、行内原因块模板（Design D 数组 4 条实例的句式）═════════
#: 键 = 状态键；值 = (标题, 正文模板, 按钮文案「去诊断 · {action}」的 action, 跳转目标)
#: 正文模板占位符：{signal} {input} {net} {prefix} {reg} {reg_w} {out_w} {rows} {group} {n_ctrl} {sel_w} {n_case}
#: {detail}（引擎 issues/note 经 scrub 后的全文）。跳转目标见 REASON_TARGETS。
REASON_TEMPLATES = {
    "false-green": (
        "为什么标「假绿」",
        "寄存器字段 {reg} 只有 {reg_w} bit（{addr}），输出是 {out_w} bit。\n"
        "高 {gap} 位永远驱不动，断言只会验到低 {reg_w} 位，仿真必过 —— 过了也不代表逻辑对。",
        "让 designer 核对字段宽度", "copy_detail"),
    "spec-collision": (
        "{n_bad} 条 case 规格矛盾，这次不生成",
        "{group} 的 {n_ctrl} 个控制拼成 {sel_w} bit select、共 {n_case} 条 case。\n"
        "下列 {n_bad} 条同一个 select 值指到了不同数据源：Excel mux 页 行 {rows}。\n"
        "工具不猜哪条对。",
        "导出这 {n_bad} 行给 designer", "copy_rows"),
    "needs-prefix": (
        "输入缺层级前缀，这次不生成",
        "输入 {input} 在 ENV_RF 层探不到，需要层级前缀。\n"
        "信号实际在 ENV_RF.{prefix}.{net} → 前缀应填 {prefix}。\n"
        "不填就 force 一根不存在的网，仿真 elaboration 直接失败。",
        "找不到网 三步流程", "diag_cuvunf"),
    "bare-probe": (
        "输出名在表里查不到，按裸名生成",
        "{group} 的输出网 {signal} 在寄存器表 / 地址表都没查到，按命名约定直接用了裸名。\n"
        "断言能生成，但名字对不对要用 nets.txt 扫一次才知道。",
        "导出 nets.txt 扫一次", "export_nets"),
    "wire-fallback": (
        "有输入名字是猜的，已按线网 force",
        "输入 {input} 在寄存器表 / 地址表都没查到，按命名约定当线网 force 了。\n"
        "名字对不对要用 nets.txt 扫一次；扫不到就跑 scan_rtl 配层级前缀。",
        "导出 nets.txt 扫一次", "export_nets"),
    "risky-generated": (
        "输入缺层级前缀，但按开关强制生成了",
        "输入 {input} 在 ENV_RF 层探不到；「缺前缀是否强制生成」当前是开着的，所以裸名 force 照样进了 .sv。\n"
        "仿真过 = 此设计不需前缀；报找不到网则按三步流程配前缀重生成。",
        "缺前缀是否强制生成", "diag_risky"),
    "unresolved": (
        "有输入解析不出网名，这次不生成",
        "{detail}",
        "找不到网 三步流程", "diag_cuvunf"),
    "parse-err": (
        "表达式或输入名解析出错",
        "{detail}",
        "解析明细", "resolve_detail"),
    "error": (
        "分析这个信号时出错（其余信号不受影响）",
        "{detail}",
        "解析明细", "resolve_detail"),
    "skip": (
        "只读回读信号，不产生断言",
        "{detail}",
        "解析明细", "resolve_detail"),
}
#: R3-02 / R3-17：模板占位符填不上时的**降一格正文** —— 细节少几格，但**仍然点名**。
#: 以前填不上就直接退到该档的悬停解释（`STATUS[key][2]`，一句通用话、一个名字都没有）：
#: needs-prefix 的原因块永远写着「要 force 的某根输入网埋在子模块里」，
#: risky-generated 连信号名都没有 —— 而「跳过/有问题的东西一律先点名」是红线第 2 条。
#: 这里只需要 `{input}`（缺前缀 / 猜名的那几条输入，由清单层从 an 捞）或 `{signal}`。
REASON_BODY_FALLBACK = {
    "needs-prefix": (
        "缺层级前缀的输入：{input}\n"
        "这几根网埋在子模块里，不配前缀就是 force 一根不存在的网，仿真 elaboration 直接失败。\n"
        "先跑 scan_rtl 扫出层级前缀导回来，这组才会生成。"),
    "risky-generated": (
        "缺层级前缀、这次按裸名 force 的输入：{input}\n"
        "「缺前缀是否强制生成」当前开着，所以 {signal} 照样进了 .sv。\n"
        "仿真过 = 此设计不需前缀；报找不到网则跑 scan_rtl 配前缀重生成。"),
    "wire-fallback": (
        "名字是按命名约定猜的输入：{input}\n"
        "它们在寄存器表 / 地址表都没查到，已按线网 force。\n"
        "名字对不对要用 nets.txt 扫一次；扫不到就跑 scan_rtl 配层级前缀。"),
    "bare-probe": (
        "输出网 {signal} 在寄存器表 / 地址表都没查到，按命名约定直接用了裸名。\n"
        "断言能生成，但名字对不对要用 nets.txt 扫一次才知道。"),
    "false-green": (
        "{signal} 的数据寄存器字段装不下每条 case 的互异值 —— 硬生成会变成「接错路也 PASS」"
        "的假测试，所以这组保护性跳过。要验得加宽字段或拆组。"),
    "spec-collision": (
        "{signal} 的 mux 页有两行控制选择值相同却选了不同数据源 —— 同一选择值 RTL 只能输出"
        "一个，已整组跳过。撞车的 Excel 行号在下面的告警全文里，请对应 designer 核对改表。"),
}
#: 跳转目标 → 谁处理（app.py 的 route_reason_action）
REASON_TARGETS = {
    "diag_cuvunf": "打开诊断抽屉并滚到「找不到网」三步",
    "diag_risky": "打开诊断抽屉并滚到「缺前缀是否强制生成」开关（裁决③：不做逐信号白名单）",
    "export_nets": "打开导出中心并预勾 nets.txt 行",
    "copy_rows": "把点名的 Excel 行号 + 信号名复制到剪贴板（状态栏报「已复制」）",
    "copy_detail": "把原因全文复制到剪贴板",
    "resolve_detail": "打开详情标题栏「解析明细」面板",
}
REASON_BTN_FMT = "去诊断 · {action}"
REASON_RISKY_BTN = "去诊断 · 缺前缀是否强制生成"      # 替代 Design 的「仍然生成（风险自负）」（裁决③）

# ═════════ 四、界面静态文案 ═════════
# ① 顶栏
TOP_BRAND = "Dreg_verify"
TOP_TABLE_TAG = "真表"
TOP_PATH_EMPTY = "尚未选择文件"
TOP_BROWSE = "浏览…"
TOP_LOAD = "载入"
TOP_RELOAD = "重新载入"
TOP_DIAG = "诊断"
TOP_EXPORT = "导出中心"
WINDOW_TITLE_FMT = "Dreg_verify · 版本 {version}"     # C-259（裁决⑬：不写 git）
WINDOW_TITLE_NOVER = "Dreg_verify"

# ② 筛选行
SCOPE_LABELS = {"topout": "Topout 全", "logic": "只看 logic", "mux": "只看 mux", "dft": "dft", "iddq": "iddq"}
SCOPE_HINT = "Topout = 全链展到源寄存器；其余 = 只看本页输入输出，不跨页"
SCOPE_MISSING_FMT = "本表无 {page} 页"                 # C-042 置灰标注
OWNER_ALL = "全部 owner"
OWNER_NONE = "（无 owner）"
OWNER_N_FMT = "owner：{n} 个"
KIND_ALL = "全部分类"
SEARCH_PLACEHOLDER = "搜索：信号名 / 表达式 / 输入信号名（支持正则）"
PRESETS = "预设"
PRESET_SAVE = "存为预设…"
PRESET_MANAGE = "管理预设…"

# ③ 清单区
LIST_COUNTS_FMT = "要验信号 {n} · 已勾选 {k} · 有问题 {p}"
LIST_COLUMNS = "列设置…"
LIST_SORT = "排序"
LIST_HEADERS = {"check": "", "neg": "反例", "name": "信号", "status": "状态", "ntest": "用例", "owner": "owner",
                "assert_id": "断言号", "kind": "分类", "form": "逻辑类型", "prefix": "探针前缀", "expr": "表达式"}
LIST_BTN_CHECK_ALL = "全选"
LIST_BTN_UNCHECK_ALL = "清空勾选"
LIST_BTN_CHECK_SELECTED = "勾选选中行"
LIST_BTN_NEG_ALL = "全部加反例"
LIST_BTN_NEG_CLEAR = "清除反例"
LIST_BTN_PASTE_NAMES = "粘贴名单勾选…"
LIST_ASSERT_TIP = "仿真 log 报 assert_{aid}_T<n> 时按它回查本信号"           # C-010
#: C-303：三个列头自带说明（v1 有、v2 漏了 —— C5a-6）。不查文档就知道这一列是什么。
#: 「断言号」那一列的说明是 `LIST_ASSERT_TIP`（C-010 已有，带 {aid} 占位、逐行填），
#: 这里给的是**列头**上那一句（不带具体标号）。
LIST_HEADER_TIPS = {
    "neg": "勾上 = 给这个信号加一条「故意填错」的用例，看自检 checker 抓不抓得到。"
           "已自定义命名 / 手填过错值的反例，清除前会先问一声。",
    "status": "这一行能不能生成断言、不能的话卡在哪。点信号名那一行左边的箭头展开原因块，"
              "里面有点名到网的原因和「去诊断」直链。",
    "assert_id": "本信号在 .sv 里的断言标号。仿真 log 报 assert_<标号>_T<第几条> 时，"
                 "按这个号回查是哪一个信号、哪一条用例。",
}
LIST_PREFIX_TIP_SET_FMT = "探针网 {net}\n断言完整路径 ENV_RF.{prefix}.{net}"   # C-018
LIST_PREFIX_TIP_UNSET_FMT = "探针网 {net}\n断言完整路径 ENV_RF.{net}（没配层级前缀）"
LIST_PREFIX_LEVEL_SHIFT_FMT = "探针口={net} (level_shift)"                  # C-019
LIST_PREFIX_INPUT_HIT_FMT = "{input}→{prefix}"                             # C-020
LIST_SUPPLEMENT_TIP = "用了 RTL 补充逻辑：{note}"                             # C-039 / C-215
LIST_NORMALIZED_TIP = "嵌套 mux 已自动折叠：{note}"                           # C-040
LIST_NORMALIZED_MARK = " ⚙"                                                 # C-040 状态格上的折叠标记
LIST_STATUS_ERROR_FMT = "解析异常：{error}"                                  # C-007
#: C-036 清单底部「清除反例」的保护性确认（真值表那条是单信号口吻，清单这条按「一批信号」说）
LIST_CONFIRM_NEG_CLEAR_FMT = ("要清的 {k} 个信号里，有 {n} 个的反例是你自定义命名或手填过错值的，"
                              "删了就是丢你的活。确定清除反例？")
KIND_LABELS = {"logic": "选路/logic", "mux": "选路/mux", "register": "直连寄存器/logic",
               "ro-readback": "RO回读(跳过)", "unresolved": "未解析"}          # C-012
FORM_LABELS = {"register": "直连寄存器", "boolean": "布尔/位运算", "select": "选路", "gated": "门控 iddq"}


def form_label_of(model):
    """模型行 → 清单「逻辑类型」列上写的那一档（C-013）。

    引擎给了 `form_label` 就用它（先过 `scrub`）：门控信号在 v1 里按**内层形态**分成
    「门控·选路」/「门控·布尔/位运算」，而 `FORM_LABELS` 只有 `gated` 一格 —— 直接查表会把
    wl 镜像上 7 个门控信号塌成同一个标签，「这一批到底是哪种」在列上就看不出来了（P-27）。
    `FORM_LABELS` 仍是覆盖度那四档的名字（每档一格，`coverage.py` 照用），也是这里的兜底。

    `forms.form_label` 从不带 F 编号（门控写成「门控·<内层>」），这条路不会把 F0–F4 放上屏。"""
    m = model or {}
    lab = scrub(str(m.get("form_label") or "").strip())
    return lab or FORM_LABELS.get(str(m.get("form") or ""), "")


# ④ 详情标题栏
HDR_META_FMT = "owner {owner} · {kind} · 用例 {n}"
HDR_COV_FMT = "覆盖度 {label} （来自：{source}） ▾"
HDR_PROGRESS_FMT = "手填期望 {n}/{m}"
HDR_PROGRESS_DIFF_FMT = "其中 {k} 条与程序算的不一致"
HDR_RESOLVE = "解析明细"
#: R3-11：点「解析明细」时状态栏别只写面板名（「解析明细」三个字对着一块刚展开的面板，
#: 等于把控件名念了一遍）。要么说清展开的是谁的，要么什么都不写（收起时就什么都不写）。
STATUS_RESOLVE_OPENED_FMT = "已展开 {name} 的解析明细（逐输入怎么驱动、名字是查到的还是猜的）"
HDR_SIDE_HIDE = "隐藏右栏 ▶"
HDR_SIDE_SHOW = "◀ 展开链 · 输入信号"
HDR_ANALYSIS_FAILED = "分析失败（其余信号不受影响）"                          # C-043 / R3-14
HDR_PENDING = "还在后台展开这个信号……"
HDR_NOT_EDITABLE = "这个信号不可建（见状态），真值表不可编辑"                   # C-134

# ⑤ 主视图标签
TAB_TRUTH_FMT = "真值表 + 电路图　{ncols} 列 · 手填 {n}/{m}"
TAB_TRUTH_PLAIN = "真值表 + 电路图"
TAB_SV = ".sv 预览"

# ⑥ 真值表区
TRUTH_BTN = {
    "regen": "重新生成", "add_col": "加列", "copy_col": "复制列", "del_col": "删列", "rename_col": "重命名列…",
    "clear": "清零…", "add_neg": "加反例（选中列）", "del_neg": "删反例…", "import_exp": "导入期望…",
    "batch_fill": "批量填…", "auto_fill": "auto→期望…", "mux_data": "mux 数据值…", "export_csv": "导出 CSV",
    "maximize": "放大", "restore": "还原",
}
TRUTH_BTN_TIPS_LOGIC = {
    "regen": "丢弃本信号自定义，按当前覆盖度重出真值表",
    "add_col": "加一条正向测试列（输入全 0、auto_out 自动算、期望留空）",
    "copy_col": "复制选中的测试列（Ctrl+D）",
    "del_col": "删除选中的测试列（可多选）",
    "rename_col": "给用户新增的列改名（自动生成的 T 列不可改）",
    "clear": "清零本信号 = 零用例（导出时只记录、不产生断言）",
    "add_neg": "给选中的正向列各加一条反例（未选则取首条正向）",
    "del_neg": "删除本信号全部反例、保留正向",
    "auto_fill": "把 auto_out 一次填进所有未填的期望格（已填的不动）——等于放弃 designer 独立核对",
}
TRUTH_BTN_TIPS_MUX = {                                                       # C-135 _MUX_BTN_TIPS
    "add_col": "mux：克隆选中列的 case 成一条可改名、可改本列数据值的手编列",
    "copy_col": "mux：复制列（数据值与期望一并带走，自动换新名避免标号冲突）",
    "del_col": "mux：自动列按签名记下（可重新生成恢复），手编列按对象删，全局反例列删 = 清整信号反例标记",
    "rename_col": "mux：只有手编列能改名（自动生成列与反例自检列拒绝改名）",
    "add_neg": "mux：逐 case 加反例（已有相同反例的跳过）",
    "clear": "mux：清空 = 零用例（与覆盖度无关）",
    "regen": "mux：丢弃清空/删列/手填期望/数据值/手编列/反例，回出厂态",
}
TRUTH_HINT_SELECTION_FMT = "已选 {col} 整列（{n} 格）"
TRUTH_HINT_SELECTION_NONE = "点一格或一列开始编辑"
TRUTH_HINT_PASTE = "Ctrl+V 从 Excel 粘贴 TSV，落在活动格；超出列数自动追加列，整次粘贴算一步撤销"
TRUTH_HINT_CONTEXT = "右键行/列：插入 · 删除 · 复制 · 设为反例"
TRUTH_FROZEN_HEADER = "信号（行 = 真实信号名）"
TRUTH_ROW_LABEL_FMT = "{name}　({role} · {port})"
TRUTH_ROW_AUTO = "auto_out　（程序按表达式算的，只读参考）"
TRUTH_ROW_EXP = "期望　（designer 手填，进 .sv 断言）"
TRUTH_AUTO_TIP = "它来自表达式本身，拿它当期望有自证嫌疑"                       # C-077
TRUTH_DIFF_TIP = "仿真该断言 FAIL 恰恰说明表达式与 designer 意图不符，这正是要抓的 bug"   # V2Spec §3 六
TRUTH_UNFILLED_TIP = "待手填：生成 .sv 时用程序算的值兜底"
TRUTH_NEG_TIP = "反例（故意填错）：自检 checker 抓不抓得到"
TRUTH_DFT_TIP = "iddq=1 漏电态自检拍：工具自动加的，不是反例"                      # C-129
TRUTH_LEGEND = (("match", "手填 · 与 auto 一致"), ("diff", "手填 · 与 auto 不一致"), ("unfilled", "待手填"),
                ("neg", "反例（故意填错）"), ("dft", "iddq=1 漏电态自检拍"))
TRUTH_PARSE_FAILED_FMT = "数值写法没认出来：{text}（已还原）。认的写法：16'h3 / 'b101 / 'd9 / hA / 0x3 / 0b101 / 9 / A"
TRUTH_PASTE_OVERFLOW_ROWS_FMT = "粘贴的行数（{rows}）超过真值表行数（{max}），已拒绝：真值表的行是输入信号，不能凭空加"
TRUTH_PASTE_REPORT_FMT = "{skipped}粘贴落了 {n} 格{added}"
TRUTH_CONTEXT_MENU = {"insert": "插入列", "delete": "删除列", "copy": "复制列", "set_neg": "设为反例",
                      "mux_data_col": "设置本列 mux 数据值…", "rename": "重命名列…"}
TRUTH_MUX_HEADER_FMT = "{case_desc} · 生效档 {cov}：{how} · 手填 {n}/{m}"    # C-124
TRUTH_MUX_SHADOWED_FMT = "被跳过的死分支：{cases}"                            # C-125
TRUTH_MUX_GATED_FMT = "受 dft 页 iddq 门控（{gate}，{forceable}）"           # C-126
TRUTH_MUX_COLLISION = "≥2 条数据路取到相同值 = 选错路也测不出（假绿 = 驱不动，断言必过）"   # C-113
TRUTH_CONFIRM_CLEAR = "清零本信号 = 零用例：导出时本信号只记录、不产生断言。确定清零？"
TRUTH_CONFIRM_DEL_NEG_FMT = "有 {n} 条反例是你自定义命名或手填过错值的，删了就是丢你的活。确定删除全部反例？"
TRUTH_CONFIRM_AUTO_FILL = "把程序算的值填进期望 = 放弃 designer 独立核对（断言会自证）。确定填入？"
TRUTH_RENAME_AUTO_REFUSED = "自动生成的 T 列不能改名；要改名请先「复制列」得到一条手编列"     # C-093
TRUTH_USER_COL_PREFIX = "U"

# ── C3-int：以下整片从 truth/model.py(16) / io.py(10) / view.py(6) / panel.py(31) 的
#    PENDING_TERMS 搬进来。同名同值的去重（粘贴报告两截尾巴 model 与 io 各一份、
#    「读不了这个文件」model 与 panel 各一份），同名不同值按【面板/用户可见】那份定版：
#    · `TRUTH_APPLY_EXP_MISSING_FMT`（model，只点名）→ 退役，统一用面板那句
#      `TRUTH_IMPORT_MISSING_FMT`（点名 + 「共 N 个，已跳过」，计数在后 = I-20）；
#    · `TRUTH_IMPORT_DONE_FMT`（panel）与 `TRUTH_IMPORT_EXP_REPORT_FMT`（model）说同一件事
#      → 合成后者一句，且**翻正成名字在前**（见下）。
# ── 空态 ──
TRUTH_EMPTY_NO_SIGNAL = "在左边清单里选一个信号，这里出它的真值表"
TRUTH_EMPTY_FAILED = "这个信号分析不出来，真值表空着（原因看左边清单的状态）"
# ── 选区摘要（TRUTH_HINT_SELECTION_FMT 只覆盖「整列」那一种）──
TRUTH_HINT_SELECTION_MULTI_FMT = "已选 {n} 格（跨 {ncol} 列）"
# ── 列头 / 行标签悬停（C-063 / C-075）──
TRUTH_HEADER_TIP_FMT = "{col}\n{drives}"
TRUTH_HEADER_TIP_NONE = "这一列不用下 force / RF_WRITE"
TRUTH_ROW_TIP_FMT = "{label}\n位宽 {width} 位 · {rw}"
TRUTH_ROW_TIP_AUTO = "程序按表达式算的值，只读参考"
TRUTH_ROW_TIP_EXP = "designer 手填的期望，进 .sv 断言"
TRUTH_UNDO_CLEAR_EXP = "清空期望"                                             # 撤销栈上这一步的名字
# ── 工具条动作的反馈 ──
TRUTH_NO_COLUMN = "先点一格或一列，再用这个按钮"
TRUTH_COL_OUT_OF_RANGE = "没有选中的测试列"
TRUTH_ADD_NEG_NO_SELECTION = "没选中列，默认给第一条正向列加反例"
TRUTH_ADD_NEG_DONE_FMT = "加了 {n} 条反例"
TRUTH_ADD_NEG_SKIPPED_FMT = "，跳过 {n} 条（那几条正向列已经有反例了，不叠第二条）"
TRUTH_DEL_NEG_DONE_FMT = "删了 {n} 条反例（正向列都留着）"
TRUTH_DEL_COL_DONE_FMT = "删了 {names}，共 {n} 列"
TRUTH_ADD_COL_DONE_FMT = "加了 {names}"
TRUTH_CLEAR_DONE = "本信号已清零 = 零用例：导出时只记录、不产生断言"
TRUTH_REGEN_DONE_FMT = "已按当前覆盖度重出 {n} 条测试列（本信号的自定义已丢弃，Ctrl+Z 可撤）"
TRUTH_AUTO_FILL_DONE_FMT = "把 auto_out 填进了 {n} 条未填的期望格"
TRUTH_BATCH_FILL_NOTHING = "没有可填的期望格（选中的列都是只读的）"
TRUTH_BATCH_FILL_REPORT_FMT = "批量填了 {n} 列的期望"
# ── C-294 粘贴结果说明的各截尾巴（`TRUTH_PASTE_REPORT_FMT` 的 {added} / {skipped}）──
TRUTH_PASTE_ADDED_FMT = "，新增列 {names}"
TRUTH_PASTE_SKIPPED_FMT = "{cells} 是只读格（auto_out 行 / 只读输入行 / 自检拍列），跳过；共 {n} 格。"
TRUTH_PASTE_SKIPPED_PLAIN_FMT = "跳过 {n} 格（只读行/列）。"          # 拿不到格名时的退路
TRUTH_PASTE_NO_COL_FMT = "{cells} 右边没有测试列了（{why}），跳过；共 {n} 格。"
TRUTH_PASTE_NO_COL_PLAIN_FMT = "{n} 格右边没有测试列了（{why}）。"
TRUTH_PASTE_NO_NEW_COL_MUX = "mux 信号清零后没有 case 可克隆，加不出新列——先「重新生成」"
TRUTH_PASTE_NO_NEW_COL = "这个信号加不出新列"
TRUTH_PASTE_MUX_WHOLE_FMT = ("{cells} 是自动生成列的 mux 数据值，要整表一起改"
                             "（工具条的「设置 mux 数据值」）；共 {n} 格。")
TRUTH_PASTE_MUX_WHOLE_PLAIN_FMT = "{n} 格是自动生成列的 mux 数据值（要整表一起改：工具条的「设置 mux 数据值」）。"
TRUTH_PASTE_BAD_FMT = "{names} 没认出写法，跳过；共 {n} 格。"                   # C-083 的粘贴面：逐格点名
TRUTH_PASTE_BAD_CELL_FMT = "{row}×{col}"
#: 跳过某一格的原因（给用户逐格看的，不进汇总那句；四桶各一条，跳过必有原因）
TRUTH_PASTE_SKIP_READONLY = "只读格（auto_out 行 / 只读输入行 / 自检拍列）"
TRUTH_PASTE_SKIP_PARSE_FMT = "写法没认出来：{text}"
TRUTH_PASTE_SKIP_NO_COL = "这一格右边没有测试列了，而且这个信号加不出新列"
TRUTH_PASTE_SKIP_MUX_WHOLE = "自动生成列的 mux 数据值，要整表一起改（工具条的「设置 mux 数据值」）"
# ── C-110 / C-112 整表 mux 数据值同步 ──
TRUTH_MUX_DATA_DONE_FMT = "已按物理寄存器 {base} 同步整表数据值（清空该格可恢复自动分配）"
#: R2-09：改完数据值后，冻结的编辑列里有几条在新的 case 集里没有对应用例 —— 名字在前（I-20）。
#: 此前 `mux_resync_cols` 把它们静默丢掉：25 列变 9 列、手填期望 5 条变 2 条，屏幕上没人说话。
TRUTH_MUX_DATA_DROPPED_FMT = "{names}（共 {n} 列）在新的数据值下没有对应的用例了，已从表里去掉——Ctrl+Z 可撤"
TRUTH_MUX_DATA_NO_BASE_FMT = "本信号没有物理寄存器 {base} 的 mux 数据行"
TRUTH_MUX_DATA_NO_REANALYZER = "这一格改不动（工具内部没接上），已还原"
# ── C-298 导入期望 / 批量填 ──
#: I-20 翻正（C3-int）：旧版是「按列名回填了 N 列的期望{没对上的}」= 计数在前。
#: 新版把点名那截放到句首（`{missing}` 由 `truth/io.import_report_text` 拼好、自带分号尾巴），
#: `model.import_expectations` 与面板的「导入期望…」共用这一份，不再各拼各的。
TRUTH_IMPORT_EXP_REPORT_FMT = "{missing}按列名回填了 {n} 列的期望"
TRUTH_IMPORT_MISSING_FMT = "这些列名在本信号里没有：{names}（共 {n} 个，已跳过）"
TRUTH_IMPORT_READ_FAILED_FMT = "读不了这个文件：{err}"
TRUTH_IMPORT_NO_COLUMNS = "第一行是空的——第一行要是列名（导出的 CSV 原样改就行）"
TRUTH_IMPORT_NO_EXP_ROW = "文件里没有「期望」行，也不止一行数据——没取到任何期望值"
TRUTH_IMPORT_ONE_ROW_FMT = "文件里没有「期望」行，按唯一的那行「{label}」取值"
TRUTH_IMPORT_BAD_CELL_FMT = "列「{name}」的写法没认出来：{text}（这一列跳过）"
TRUTH_IMPORT_DUP_COL_FMT = "列名「{name}」在文件里出现了不止一次，只取第一处"
TRUTH_IMPORT_EMPTY_FILE = "这个文件里一行都没有"
# ── C-136…C-140 导出本信号 CSV ──
TRUTH_EXPORT_DONE_FMT = "已写出 {path}（{n} 列）"
TRUTH_EXPORT_FAILED_FMT = "导不出 CSV：{err}"
TRUTH_EXPORT_MUX_NO_BUILD = ("这个 mux 信号这次没产出 .sv 块（被跳过或只记录），CSV 导的是编辑器这张表，"
                             "与 .sv 不一定一一对应")
TRUTH_EXPORT_CSV_FILTER = "CSV 表格 (*.csv)"
TRUTH_IMPORT_EXP_FILTER = "期望值表 (*.csv *.xlsx *.xlsm)"
TRUTH_EXPORT_CSV_TITLE = "导出本信号真值表 CSV"
TRUTH_IMPORT_EXP_TITLE = "导入期望值"
# ── mux 头部条（C-124 / C-125 / C-126）──
TRUTH_MUX_CASE_FMT = "case({expr}) {n} 选 1"
TRUTH_MUX_CASE_UNKNOWN = "mux 选路"
TRUTH_MUX_HEADER_NOCOV_FMT = "{case_desc} · 手填 {n}/{m}"
TRUTH_MUX_HOW = {
    "精简": "每个 case 1 条（don't-care 位取 0）",
    "全面": "精简 + case 的 x 位展开 + 每 case 一轮反码数据",
    "穷举": "全面 + 另一条物理控制路径全扫每 case",
}
TRUTH_MUX_GATE_FORCEABLE = "门是只读网，能 force 到透传值"
TRUTH_MUX_GATE_NOT_FORCEABLE = "门网 force 不了（见右栏输入信号表），只能靠功能拍"
TRUTH_SUPPLEMENT_DOT_TEXT = "●"                                               # C-215 RTL 补充逻辑标记

# ⑦ 电路图区
FLOW_TITLE = "电路图"
FLOW_SUBTITLE = "源寄存器在左 · 顶层输出在右"
FLOW_BTN_FULLSCREEN = "全屏"
FLOW_BTN_EXIT_FULLSCREEN = "退出全屏"
FLOW_BTN_FIT = "适应窗口"
FLOW_BTN_100 = "100%"
FLOW_BTN_EXPORT_SVG = "导出 SVG"
FLOW_BTN_EXPORT_PNG = "导出 PNG"
FLOW_FOOTER = ("右键按住拖动平移 · Ctrl + 滚轮缩放 · 双击复位。"
               "点一根线 → 真值表同名行与展开链同步高亮；hover 寄存器盒看地址 / 位段 / 为什么判成 RO 或 RW。")
FLOW_LEGEND = ("── 寄存器（表里查到地址）", "┈ 名字来自命名约定，表里未查到", "── 当前选中线网", "┈ 规格冲突的 case 支")
FLOW_BROKEN = "展不下去"                                                      # 未解析断点红色端子
FLOW_EMPTY = "这个信号没有可画的电路（只读回读 / 未解析），看左侧状态"
FLOW_PENDING = "还在后台展开这个信号，展开完自动出图"                            # 电路图区的「分析中」
#: 构图失败：引擎那条 ⚠ 原文点名在后（C-270 点名 + 原因），这句只交代「图没画出来、别的没事」
FLOW_BUILD_FAILED = "这个信号的电路图没画出来（其余功能不受影响）——引擎给的原因："
FLOW_WHY_REG = "判成 RW 的依据：地址表里查到了这个字段的地址"                     # C-283 寄存器盒 tooltip
FLOW_ZOOM_FMT = "{pct}%"

# ⑧ .sv 预览
SV_TITLE_SIGNAL = "本信号 .sv 片段（不落盘）"
SV_TITLE_CHECKED = "勾选集 .sv（不落盘）"
SV_BTN_TO_CHECKED = "改看勾选集"
SV_BTN_TO_SIGNAL = "改看本信号"
SV_BTN_COPY = "复制"
SV_TRUNCATED_FMT = "\n…（已截断：共 {total} 行，只显示前 {shown} 行；导出 .sv 是完整的）"     # C-069
SV_SKIPPED_TAIL_HEAD = "本次会跳过的信号（各缺哪根输入）："                        # C-070
SV_PREVIEW_STATUS_FMT = "预览：{counts}"                                      # C-172

# ⑨ 右侧常驻栏
SIDE_EMPTY_NO_SIGNAL = "在左边清单里选一个信号，这里出它的展开链和输入信号"       # 右栏空态
SIDE_CHAIN_TITLE = "逐层展开"
SIDE_CHAIN_HELP = "从顶层输出往回到源寄存器 · 每层：Excel 原式 = 代入真实信号名"   # C-222
CHAIN_PAGE_TAGS = {"dft": "dft 页门控", "logic": "logic 页", "mux": "mux 页 · {group}", "iddq": "iddq 页",
                   "level_shift": "level_shift 页", "register": "寄存器"}
CHAIN_REGISTER_DIRECT = "顶层口 == 该寄存器字段写值，无展开"                     # C-053
CHAIN_LEAF_NOTE = "输入均为直连叶子（本级无上游）"                               # C-051
SIDE_INPUTS_TITLE_FMT = "输入信号 {n} 个"
SIDE_INPUTS_GUESS_BADGE = "名字来自命名约定，表里未查到"
SIDE_INPUTS_HEADERS = ("字母", "信号(位宽)", "角色", "类型")
SIDE_NEEDS_PREFIX_MARK = "⚠需探针前缀（内部衔接网，跑 scan_rtl 配前缀否则跳过）"    # C-058
SIDE_UNRESOLVED_MARK = "✗未解析"

# ⑩ 状态栏
STATUS_LOADED_FMT = "已载入 {n} 个信号（logic {nl} + mux {nm}）· Topout 要验 {nt} 个 · 有问题 {np} 个"
#: P-15：切到别的范围后这一行还写着「Topout 要验 N 个」，dft 页写成「logic 9」——
#: 那两个数说的是**当前范围**的清单，标题却是另一个范围的名字。非 Topout 范围换这一句。
STATUS_LOADED_SCOPE_FMT = "{page}：{n} 个信号 · 要验 {nt} 个 · 有问题 {np} 个"
#: 范围 → 状态栏里怎么称呼它（`SCOPE_LABELS` 是筛选行按钮上的说法，「只看 logic」放进
#: 「{page}：9 个信号」里读不通）
SCOPE_PAGE_NAMES = {"topout": "Topout", "logic": "logic 页", "mux": "mux 页",
                    "dft": "dft 页", "iddq": "iddq 页"}
#: C-006 的悬停口径必须与上面那一行**同一份判据**（P-20）：这里以前写「非 clean N」
#: （= 色档不是 ok），上面写「有问题 N」（= warn/bad）—— 同一块屏幕两个数。
STATUS_LOADED_DETAIL_FMT = "有问题 {nbad} 个 · 寄存器定义表字段 {ntmm} · 寄存器地址映射表字段 {nreg}"   # C-006
STATUS_VISIBLE_FMT = "可见 {v} / 共 {m}（其中 {k} 个按输入信号名命中）"            # C-031
STATUS_MISSING_PAGES_FMT = "本表无 {pages} 页，门控层跳过"                        # C-042
STATUS_LAST_EXPORT_FMT = "上次导出 {kind}：{when} → {path}"
STATUS_AUTOSAVE_FMT = "编辑自动存盘 · 上次 {when}"
STATUS_RESTORED_FMT = "恢复了 {n} 个信号的手填编辑"                               # C-241
STATUS_RESTORE_MISSING_FMT = "{names}（共 {n} 个）的手填编辑在当前表里对不上信号：{reason}"   # C-239 / R3-08
#: 为什么对不上 —— 以前这句只有名字没有原因，用户看到「找不到」第一反应是「我的活丢了」。
STATUS_RESTORE_MISSING_REASON = "改名？删行？还是这一页不在当前范围里？盘上那份原样留着，没删"
STATUS_SUPPLEMENT_FMT = "RTL 补充逻辑生效：{names}"                              # C-215
STATUS_COPIED = "已复制到剪贴板"
STATUS_NEG_ADDED_FMT = "已加 {n} 条反例，跳过 {skipped} 条（同输入取值已有反例）"    # C-097
STATUS_NEG_NONE = "选中的用例都已有反例，未重复添加"
STATUS_MUX_FLIPPED_FMT = "反例错值撞上正确值，已自动翻一位：{name}"                  # C-105
STATUS_LOAD_FAILED_FMT = "表读不进来：{reason}"                                  # C-005
#: R3-12：载不进来时补一句「工作台上仍然是哪一张」。路径框已经先被改成新路径了，
#: 失败后不回滚的话，屏幕上写着 A 表、清单和真值表却还是 B 表 —— 用户照着 A 去核对。
STATUS_LOAD_FAILED_KEPT_FMT = "{reason}。当前仍是《{cur}》"
#: R3-13 / C5b-1：整表展开这一趟炸了（worker.failed）。以前错误条上整条就是引擎异常的
#: message（真表上是一串英文），而详情区那行「分析失败」压根没人去点亮（C-043 是空的）。
STATUS_ANALYZE_FAILED_FMT = "整表展开中断了：{reason}。已展开完的信号还在，重新载入可再跑"
STATUS_NO_ROWS_SELECTED = "先在清单里选中若干行（鼠标框选 / Ctrl·Shift 点），再点「勾选选中行」"
#: R3-10：清单底部五个批量动作的反馈。以前视图里写的是 `T.LIST_BTN_XXX + " %d"`，
#: 状态栏上就是「全选 12」「清空勾选 12」—— 一个按钮名加一个数字，既没说作用在谁身上
#: （可见行？整张清单？勾着的？三个按钮三种作用域），也没说这一下改变了什么。
STATUS_CHECK_ALL_FMT = "已勾选可见的 {n} 个信号（导出范围跟着变）"
STATUS_UNCHECK_ALL_FMT = "已取消勾选可见的 {n} 个信号（被筛掉看不见的那些不动）"
STATUS_CHECK_SELECTED_FMT = "已勾选选中的 {n} 个信号（导出范围跟着变）"
STATUS_NEG_ALL_FMT = "已给 {n} 个信号各加一条反例（自检 checker 抓不抓得到）"
STATUS_NEG_CLEAR_FMT = "已清除 {n} 个信号的反例（正向用例都留着）"

# ⑭ 空态 / ⑮ 载入态
EMPTY_TITLE = "先载入 Dreg 核心 Excel"
EMPTY_DESC = ("工具会读 logic / mux / dft / iddq / regmap / total_memory_map / Topout 这几页，"
              "对 Topout 页每个顶层信号从输出往回展开到源寄存器，再生成测试向量与断言。")
EMPTY_BTN_OPEN = "选择 Excel…"
EMPTY_BTN_IMPORT_CONFIG = "导入配置…"
EMPTY_RECENT_TITLE = "最近打开"
EMPTY_RECENT_NONE = "还没有最近打开的表"                                          # 裁决⑫
EMPTY_RECENT_ROW_FMT = "{when} · {n} 信号"
LOADING_TITLE_FMT = "正在展开 Topout 信号 {done} / {total}"
LOADING_TITLE_PAGE_FMT = "正在分析 {page} 页 {done} / {total}"
LOADING_CURRENT_FMT = "当前：{name}　{hint}"
LOADING_HINT = "清单已可点，展开完的信号先出现；未完成的显示「分析中」。"
LOADING_BTN_STOP = "停止分析"
LOADING_STOPPED_FMT = "已停止：展开完 {done} / {total}，其余显示「分析中」（重新载入可继续）"

# ⑯ 覆盖度弹层
COV_TITLE_FMT = "覆盖度：本信号生效档 = {label}"
COV_CLOSE = "收起"
COV_CHAIN_FMT = "生效来源：本信号「{sig}」→ 逻辑类型「{form}」= {form_label} → 全局默认 = {global_label}"
COV_LEVEL_GLOBAL = "全局默认"
COV_LEVEL_GLOBAL_DESC = "整张表的兜底档"
COV_LEVEL_FORM_FMT = "逻辑类型 · {form}"
COV_LEVEL_SIG = "本信号"
COV_FOLLOW_GLOBAL = "跟随全局"
COV_FOLLOW_UP = "跟随上级"
COV_THIS_FORM_MARK = "　← 本信号属这类"
COV_MAXT = "用例数上限"
COV_COUNT_FMT = "本信号当前 {n} 条"
COV_COUNT_CUSTOM_FMT = "本信号当前 {n} 条，含 {neg} 反例（已自定义）"                   # C-133
COV_HELP = "档位怎么算的？"
COV_HELP_TEXT = (
    "【logic / 直连寄存器】\n"
    "  精简 = 每种「控制位组合」各取 1 组代表数据（最少用例）\n"
    "  全面 = 每种控制位组合再扫多组数据（全0 / 全1 / 反码 / 走步，区分坏位）\n"
    "  穷举 = 所有输入位的全部组合（仅当总输入位 ≤ 10，否则自动退化为「全面」）\n\n"
    "【mux 选路信号】\n"
    "  精简 = 每个 case 1 条（don't-care 位取 0）\n"
    "  全面 = 精简 + case 的 x 位展开 + 每 case 一轮反码数据（抓数据通路坏位）\n"
    "  穷举 = 全面 + 另一条物理控制路径（line / local）全扫每 case\n\n"
    "三层优先级：本信号 > 逻辑类型 > 全局默认；改档即时重算清单用例数与真值表。")

# ⑪ 导出中心
EXPORT_TITLE = "导出中心"
EXPORT_HEADERS = ("", "交付物", "范围", "选项", "上次导出到哪")
#: 六行：kind → (交付物名, 选项摘要默认文案, 备注)
EXPORT_ROWS = {
    "sv": (".sv 断言文件", "无注释 · 不写末尾汇总 · 不写 owner", ""),
    "report": ("报告（给人看）", "HTML（含电路图）· 也可 CSV / Excel", ""),
    "fortest": ("回填 for_test 页", "写副本，不覆盖源表", ""),
    "nets": ("nets.txt（红区扫网名）", "类别：顶层输出 · force 目标 · 猜名的网", ""),
    "claims": ("claims.json（红区比对）", "每根探针：探哪根网 · 名字是查到的还是猜的", "随 .sv 进红区，诊断脚本的唯一输入"),
    "config": ("整份配置", "探针前缀 · 强制 force · 补充逻辑 · 覆盖度 · 手填期望", ""),
}
EXPORT_SCOPE_CHECKED_FMT = "勾选的 {n} 个"
EXPORT_SCOPE_ALL_FMT = "全部 {n} 个"
EXPORT_SCOPE_NA = "—"
EXPORT_SV_SCOPES = {"all": "全部（正向+反例）", "pos": "仅正向", "neg": "仅反例", "split": "正向+反例分文件"}
EXPORT_SV_OPTIONS = {"comments": "加注释", "sv_summary": "末尾汇总", "owner_in_msg": "写 owner"}
EXPORT_REPORT_FORMATS = {"html": "HTML（含电路图）", "csv": "CSV", "xlsx": "Excel"}
EXPORT_NETS_MORE = "更多（按页分类）"
EXPORT_NETS_PURPOSE = {"topout_out": "顶层输出", "force_target": "force 目标", "guessed": "猜名的网"}
EXPORT_NETS_PAGES = {"topout-cone": "Topout + 展开输入", "topout": "仅 Topout 探针", "logic": "logic",
                     "mux": "mux", "dft": "dft", "iddq": "iddq"}
EXPORT_LAST_NEVER = "从未导出"
EXPORT_LAST_FMT = "{path}　{when}"
EXPORT_SUMMARY_FMT = "勾选 {k} 项 · 覆盖 {n} 个信号 · {s} 个信号会被跳过"
EXPORT_SUMMARY_NO_SKIP_FMT = "勾选 {k} 项 · 覆盖 {n} 个信号"
EXPORT_SUMMARY_SKIPPED_HEAD = "会被跳过的信号 —— 名字和原因："
EXPORT_SUMMARY_SKIPPED_MORE = "展开名字和原因"
EXPORT_SUMMARY_SKIPPED_LESS = "收起"
#: 摘要算不出来时说实话，不给「0 个信号会被跳过」那种让人放心的假数字（裁决⑯）
EXPORT_SUMMARY_UNAVAILABLE = ("导出前摘要这次算不出来（{reason}）——仍可导出，"
                              "跳过了哪些信号会在导出完成后点名")
EXPORT_SKIPPED_ROW_FMT = "{name}　{reason}"                 # 名字在前（I-20 / C-270）
# ── R3-01 / P-17：跳过原因的 UI 层改写（导出前摘要 / .sv 预览尾 / 完成弹层三屏共用）──
#: ⚠ **引擎那句不动**：`topout._account_block` 的 reason 会原样写进 .sv 的块顶注释
#: （`// [topout] <名字> (<kind>/<status>)：<reason>`），受 byte-gate 保护。这里改的是
#: 三块屏幕上显示的那一份。以前它们照抄引擎的兜底句「generator.build 未产出该块
#: （规格冲突/空向量/被跳过，见账目）」—— 一个内部函数名 + 三个并列的猜测，
#: 而 needs-prefix 档**只有**这一句可给（关掉「缺前缀是否强制生成」后必然撞上）。
SKIP_REASON_NEEDS_PREFIX_FMT = "缺 {nets} 的层级前缀，这次没进 .sv —— 先跑 scan_rtl 配前缀"
SKIP_REASON_NEEDS_PREFIX_PLAIN = ("有输入网埋在子模块里、缺层级前缀，这次没进 .sv —— "
                                  "先跑 scan_rtl 配前缀")
SKIP_REASON_COLLISION_ROWS_FMT = ("mux 页第 {a} 行与第 {b} 行规格矛盾{more}，这次没进 .sv "
                                  "—— 请 designer 核对改表")
SKIP_REASON_COLLISION_MORE_FMT = "（这样的还有 {n} 处）"
SKIP_REASON_COLLISION = ("mux 页有两行规格矛盾（同一控制选择值选了不同数据源），这次没进 .sv "
                         "—— 请 designer 核对改表")
SKIP_REASON_CLEARED = "已清零成零用例：只记录、不产生断言"
#: 引擎确实没给原因时的兜底 —— 说清「去哪儿看」，不编三个并列的猜测
SKIP_REASON_FALLBACK = "这组没生成断言（引擎没给原因，看行内原因块）"
#: 引擎兜底句的识别特征（`topout.py` 那一句；`an` 取不到时按文本认）
_SKIP_RAW_FALLBACK_RE = re.compile(r"未产出该块|规格冲突/空向量")
_SKIP_ROW_RE = re.compile(r"第\s*(\d+)\s*行")
_SKIP_CLEARED_RE = re.compile(r"用户已清空|清空该信号的测试列")


def _needs_prefix_net_list(an, cap=4):
    """缺层级前缀的那几根网 —— 点名用（超出 cap 就省略号，别把整张表抄进一行）。"""
    try:
        from dreg_verify import inputs_table as _IT
        rows = _IT.needs_prefix_rows(an)
    except Exception:                                       # noqa: BLE001  取不到就退到不点名那句
        return []
    out = []
    for r in rows:
        nm = str(r.get("net") or r.get("name") or "").strip()
        if nm and nm not in out:
            out.append(nm)
    return out[:cap] + (["…"] if len(out) > cap else [])


def skip_reason_of(reason="", an=None):
    """引擎给的跳过原因 → 屏幕上那一句（**导出前摘要 / .sv 预览尾 / 完成弹层共用这一个函数**）。

    判据优先用 `an` 的 `status_detail`（八档，`status_key_of`）；`an` 取不到（预览侧只有
    build 结果）就退到按原文认。认不出的状态原样返回 —— RO 回读 / 同源已覆盖 /
    本次范围没有用例这些本来就是人话，不必改写。"""
    txt = scrub(str(reason or "")).strip()
    key = status_key_of(an) if an else ""
    engine_fallback = bool(not txt or _SKIP_RAW_FALLBACK_RE.search(txt))
    if key == "needs-prefix" or (not key and "缺探针前缀" in txt):
        nets = _needs_prefix_net_list(an)
        return (SKIP_REASON_NEEDS_PREFIX_FMT.format(nets="、".join(nets)) if nets
                else SKIP_REASON_NEEDS_PREFIX_PLAIN)
    # ⚠ 引擎兜底句里也有「规格冲突/空向量」四个字（那是三个并列的**猜测**，不是判断），
    #    所以认不出状态时先把它挡在前面 —— 否则一句「不知道为什么」会被当成「规格冲突」。
    if key == "spec-collision" or (not engine_fallback and "规格冲突" in txt):
        # 引擎那几句是「mux 页第 33 行与第 23 行…」—— 行号成对出现，点头一对、其余报个数
        rows = _SKIP_ROW_RE.findall(txt)
        if len(rows) >= 2:
            more = (SKIP_REASON_COLLISION_MORE_FMT.format(n=len(rows) // 2 - 1)
                    if len(rows) >= 4 else "")
            return SKIP_REASON_COLLISION_ROWS_FMT.format(a=rows[0], b=rows[1], more=more)
        return SKIP_REASON_COLLISION
    if _SKIP_CLEARED_RE.search(txt):
        return SKIP_REASON_CLEARED
    if engine_fallback:
        return SKIP_REASON_FALLBACK
    return txt


def export_write_failed(exc, path=""):
    """写产物失败 → 一句人话（C-171 / R3-05）。`exc` 是 `exports.ExportError`（自带 errno / path）。

    分支按 errno：目录不存在 ≠ 文件被占用 —— 两种情况要做的事完全不同（一个去建目录 /
    换位置，一个去关仿真器），说错等于让人白查一轮。"""
    import errno as _errno
    p = str(path or getattr(exc, "path", "") or "")
    code = getattr(exc, "errno", None)
    if code is None:
        # 不是文件系统失败，是引擎自己判出来的中文原因（「输出文件不能是源 Excel 本身」
        # 这类）—— 那句话本身就是要给工程师看的，留住。
        return exc_text(exc, p)
    if code in (_errno.ENOENT, _errno.ENOTDIR):
        why = EXPORT_WRITE_NO_DIR_FMT.format(dir=short_path(os.path.dirname(p) or p))
    elif code in (_errno.EACCES, _errno.EPERM, _errno.EBUSY, _errno.EROFS):
        why = EXPORT_WRITE_BUSY
    elif code in (_errno.ENOSPC, _errno.EDQUOT if hasattr(_errno, "EDQUOT") else _errno.ENOSPC):
        why = EXPORT_WRITE_NO_SPACE
    else:
        why = EXPORT_WRITE_OTHER
    return EXPORT_WRITE_FAILED_FMT.format(path=short_path(p) or EXPORT_LAST_NEVER, why=why)


def humanize_skipped(pairs, analyze=None):
    """[(信号名, 引擎原因)] → [(信号名, 屏幕上那一句)]。

    `analyze` 是 `state.analyze` 那样的 `name -> an`（给 `skip_reason_of` 拿 `status_detail`
    与缺前缀的网名）；不给就只按原文改写。**算不出 an 不该挡住导出**，所以整条包 try。"""
    out = []
    for name, reason in (pairs or ()):
        an = None
        if callable(analyze):
            try:
                an = analyze(name)
            except Exception:                               # noqa: BLE001
                an = None
        out.append((name, skip_reason_of(reason, an)))
    return out
EXPORT_OPT_BTN_FMT = "{summary}　▾"
EXPORT_BTN_RUN_FMT = "导出勾选的 {k} 项"
EXPORT_BTN_RUN_NONE = "先勾选要导出的交付物"
EXPORT_BTN_CANCEL = "取消"
EXPORT_BTN_IMPORT_CONFIG = "导入配置…"
EXPORT_DEFAULT_SV = "wr_rf_tc.sv"
EXPORT_DEFAULT_SV_POS = "wr_rf_tc_pos.sv"
EXPORT_DEFAULT_SV_NEG = "wr_rf_tc_neg.sv"
EXPORT_DEFAULT_REPORT = "用例表.html"
EXPORT_DEFAULT_NETS = "nets.txt"
EXPORT_DEFAULT_CLAIMS = "claims.json"
EXPORT_DEFAULT_FORTEST_FMT = "{stem}_fortest.xlsx"          # C-181：源表名 + _fortest
#: 文件对话框过滤器（用户可见；报告那三格式的筛选器在 `exports.REPORT_FILTERS`）
EXPORT_FILTER_SV = "SystemVerilog (*.sv)"
EXPORT_FILTER_FORTEST = "Excel 工作簿 (*.xlsx)"
EXPORT_FILTER_NETS = "信号清单 (*.txt);;全部文件 (*)"
EXPORT_FILTER_JSON = "JSON (*.json)"
EXPORT_DUP_LABELS_TITLE = "重复 assert 标号（非法 SV）"
EXPORT_DUP_LABELS_FMT = "以下 {n} 处 assert 标号重复，同一作用域内重复会让 elaboration 失败。仍要写出？\n{rows}"
#: C-171 / R3-05：写不出去。**按 errno 分支** —— 以前无条件拼「（文件是否正被仿真器 /
#: 编辑器占用？）」，目录根本不存在时那句是错的（对抗 review 实证：选了一个不存在的目录，
#: 工具让人去关仿真器）。路径只给「上级目录/文件名」（`short_path`），不给本机全路径。
EXPORT_WRITE_FAILED_FMT = "{path} 写不出去：{why}"
EXPORT_WRITE_NO_DIR_FMT = "这个文件夹不存在：{dir}（先建好，或换个位置）"
EXPORT_WRITE_BUSY = "正被别的程序占着（仿真器 / 编辑器 / Excel 开着？），或者这个文件是只读的"
EXPORT_WRITE_NO_SPACE = "这个盘写满了，换个位置再试"
EXPORT_WRITE_OTHER = "写入失败，换个位置再试"
EXPORT_IMPORT_MISMATCH_FMT = "这份配置是为《{cfg}》导出的，当前是《{cur}》"                          # C-193
#: C-194 / C-270 / I-20 主控裁决：**名字在前、计数在后**（旧版是「（{n} 个）：{names}」，
#: 计数先出现等于先让人看一个数字再去猜是哪些信号 —— 点名永远排在计数前面）
EXPORT_IMPORT_MISSING_FMT = "配置里有、当前表没有的信号：{names}（共 {n} 个，这些跳过，其余照常导入）"
EXPORT_IMPORT_BAD_FILE = "这不是本工具导出的配置文件（也不是旧版的测试项编辑文件）"      # C-195 / R3-16
EXPORT_IMPORT_KIND_FULL = "完整配置"
EXPORT_IMPORT_KIND_LEGACY = "测试项编辑"
EXPORT_IMPORT_DONE_FMT = "已导入{kind}：恢复了 {n} 个信号的手填编辑"
#: C-192：旧配置里那四项（两项展开模式 + 两项输出引用尾缀）随入口退役 —— 忽略但说一句，不静默。
#: ⚠ 措辞不提退役前的旧叫法：那些说法连同入口一起没了，再写出来只会让人去找一个不存在的开关。
EXPORT_IMPORT_IGNORED = ("这份配置里有四项当前版本已不再使用的旧设置（两项展开模式、两项输出引用尾缀），"
                         "已忽略；覆盖度档、用例上限、缺前缀是否强制生成照常套用")
#: R2-12：配置写的版本号比本工具认识的还新 —— 照单套用认得的那几段，但要说一句。
#: 静默全收的坏处是：新版新增的段被忽略了，界面上一点看不出来，用户以为整份都生效了。
EXPORT_IMPORT_NEWER_VERSION = ("这份配置是更新版本的工具导出的：认得的几段照常套用，"
                              "本版还不认识的设置已跳过")
EXPORT_IMPORT_APPLIED_FMT = ("已套用：勾选 {k} 个 · 覆盖度档与用例上限 · 前缀 {np} 条 · "
                             "强制 force {nf} 个 · 补充逻辑 {no} 条")
EXPORT_CONFIG_DONE_FMT = "配置已导出：勾选 {k} 个 · 全局档 {cov} · 前缀 {np} 条 · 强制 force {nf} 个 · 编辑 {ne} 个信号、手填期望 {nx} 条"   # C-191
EXPORT_REPORT_DONE_FMT = "范围 {scope} · 用例 {n} 条 · 反例 {neg} 条"                                 # C-177
EXPORT_FORTEST_DONE_FMT = "回填 {n} 组（含 mux）"                                                      # C-182

# ⑫ 导出完成
DONE_TITLE = "导出完成"
DONE_SKIPPED_HEAD_FMT = "{n} 个信号没有进 .sv —— 名字和原因："
DONE_SKIPPED_ROW_FMT = "　└ {reason}"
DONE_SKIPPED_MORE = "展开明细"                                                                      # C-167
DONE_SKIPPED_LESS = "收起明细"
DONE_WRITTEN = "已写出"
DONE_WRITTEN_ROW_FMT = "{path}　{detail}"
DONE_ERROR_ROW_FMT = "{kind}：{message}"                                                            # C-171
DONE_BTN_OPEN_DIR = "打开输出目录"
DONE_BTN_GO_FIX_FMT = "去处理这 {n} 个信号"
DONE_BTN_OK = "知道了"
DONE_ACCOUNTED_FMT = "只记录、不产生断言的信号：{names}"                                            # C-166

# ⑬ 诊断抽屉
DIAG_TITLE = "诊断"
DIAG_CLOSE = "收起"
DIAG_INTRO = "按症状选，不是按按钮选。"
DIAG_CUVUNF_TITLE = "仿真报「找不到这根网」（CUVUNF）"
DIAG_CUVUNF_BODY = ("．sv 里 force / assert 的网不在 DUT 顶层，要带层级前缀，"
                    "例如信号实际在 ENV_RF.U_BT_LP_PLL_DIG.pll_n → 前缀填 U_BT_LP_PLL_DIG。")
#: 三步：(编号, 标题, 正文, 结果框模板, 主按钮)。第 2 步措辞按裁决⑪。
DIAG_STEPS = (
    ("1", "从工具导出 nets.txt",
     "把这张表里所有要定位的网名写成一个清单。按类别勾：顶层输出 / force 目标 / 名字是猜的。",
     "{path}　{n_sig} 个信号 · {n_net} 根网 · 其中 {n_guess} 根名字是猜的", "导出 nets.txt…"),
    ("2", "在红区跑 scan_rtl.py",
     "把 scan_rtl.py 和 nets.txt 一起传到仿真服务器，在仿真服务器上跑（不带参数），"
     "它静态扫 RTL 一次找全层级，生成 probe_prefixes.txt。",
     "python3 scan_rtl.py", "复制这行命令"),
    ("3", "把 probe_prefixes.txt 导回来",
     "导入后清单里的探针前缀列会填上，重新导出 .sv 即可。例：信号实际在 ENV_RF.U_BT_LP_PLL_DIG.pll_n → 前缀 U_BT_LP_PLL_DIG。",
     "当前已有前缀映射 {n_map} 条 · 还差 {n_missing} 根网没有前缀", "导入 probe_prefixes.txt…"),
)
DIAG_STEP1_NEVER = "还没导出过 nets.txt"
DIAG_PREFIX_EDIT = "编辑前缀映射…"
DIAG_PREFIX_EXPORT = "导出前缀映射 .txt…"
#: 其它症状（Design 4 条 + 第 5 条 C-302）：(标题, 提示)
DIAG_OTHER = (
    ("规格缺了一级逻辑，工具展不下去", "手工补一段等价表达式（RTL 补充逻辑）"),
    ("两个东西撞名，展开走错了路", "强制直接 force 顶层网，跳过逐层展开"),
    ("覆盖度不够 / 用例太多跑不动", "调档位与上限"),
    ("还有网没有前缀，要不要照样生成", "缺前缀是否强制生成　当前：{state}"),
    ("以前在旧版界面填过期望", "从旧版真值表编辑迁进来（一次性，旧数据只读不删）"),
)
#: 五条折叠项各自的入口按钮（C4-int 从 diagnostics.PENDING_TERMS 搬进来）
DIAG_SUPP_OPEN = "补一段等价表达式…"
DIAG_FORCE_OPEN = "编辑强制 force 名单…"
DIAG_COVERAGE_OPEN = "打开覆盖度设置…"
DIAG_LEGACY_OPEN = "看看能迁过来什么…"
DIAG_RISKY_ON = "是"
DIAG_RISKY_OFF = "否"
DIAG_RISKY_OFF_WARNING = "关掉后缺前缀的信号会被跳过、不进 .sv（导出时逐个点名）；.sv 内容会变"
DIAG_RISKY_OFF_TITLE = "关掉「缺前缀是否强制生成」"            # C-217 二次确认的标题
DIAG_FOOTER = "这里的每一项都是逃生阀，不是日常主力。日常只用清单 + 详情 + 导出中心。"
#: 三个编辑器的通用按钮
DIAG_SAVE = "保存"
DIAG_CANCEL = "取消"
DIAG_PREFIX_IMPORT = "导入 .txt…"
DIAG_PREFIX_EXPORT_SHORT = "导出 .txt…"
DIAG_FORCE_IMPORT = "导入 .txt…"
DIAG_FORCE_EXPORT = "导出 .txt…"
#: 文件读写失败（走编辑器里的错误行，不弹窗）
DIAG_FILE_READ_FAIL_FMT = "读不进来：{err}"
DIAG_FILE_WRITE_FAIL_FMT = "写不出去：{err}"
DIAG_PREFIX_TITLE = "探针前缀映射"
DIAG_PREFIX_HINT = ("每行 信号名=层级路径；或先写「路径:」再在下面列信号名（逗号 / 换行分隔）；# 开头是注释。"
                    "红区 scan_rtl 生成的 probe_prefixes.txt 可直接导入。")
DIAG_PREFIX_IMPACT_FMT = "共 {n} 条映射 · 影响 {m} 个信号"                                             # C-204
DIAG_FORCE_TITLE = "强制 force 信号"
DIAG_FORCE_HINT = "每行一个基名（去 _to_logic / _to_mux 尾缀、去位宽）；行尾 # 之后是注释；留空 = 清除。"
DIAG_FORCE_DONE_FMT = "强制 force 名单已更新（共 {n} 个）——这些信号跳过逐层展开、直接 force 顶层基名网"
DIAG_SUPP_TITLE = "RTL 补充逻辑"
DIAG_SUPP_HINT = "JSON：{信号基名: {enabled, expr, inputs:[{var, raw}], note}}；var = 表达式里的变量名，raw = 真实网名（可带 [msb:lsb]）。"
DIAG_SUPP_TEMPLATE = "插入模板（当前信号）"
DIAG_SUPP_IMPORT = "从 .json 导入…"
DIAG_SUPP_UNKNOWN_FMT = "{name} 不在当前 logic 页：将作为纯新增合成信号生成"                             # C-214
DIAG_SUPP_INVALID_HEAD = "校验不通过，未保存："                                                       # C-213
DIAG_SUPP_BAD_JSON_FMT = "不是合法 JSON：{err}"
DIAG_SUPP_TMPL_BAD_JSON = "当前内容不是合法 JSON，合并不进去；请先修好或清空。"
DIAG_SUPP_DONE_FMT = "RTL 补充逻辑已更新（共 {n} 条，{n_on} 条启用）"
DIAG_SUPP_CLEARED = "RTL 补充逻辑已清空"
DIAG_LEGACY_TITLE = "从旧版真值表编辑迁进来"
#: R3-09：「不迁 N 个 mux 信号」那两截**只有真有 mux 要跳过时才出**。以前是无条件拼的，
#: 于是一张没有 mux 编辑的表上也端出「不迁 0 个 mux 信号：—」+ 三行 mux 解释 ——
#: 零跳过的东西不该出现在屏幕上（用户会去找那个「—」是什么）。
DIAG_LEGACY_PREVIEW_FMT = ("可迁移 {n_ok} 个信号（logic / 直连寄存器根）。\n"
                           "迁入后用例数以旧版编辑为准（如 4 → 原自动 12），旧数据只读不删。")
DIAG_LEGACY_PREVIEW_MUX_FMT = ("{mux_names}（共 {n_mux} 个 mux 信号）不迁：mux 用的是 case / "
                               "数据列坐标（c:A / d:0），与旧版按物理基名存的取值对不上。")
DIAG_LEGACY_NONE = "这张表没有旧版真值表编辑"
DIAG_LEGACY_RUN = "开始迁移"
#: C-302 迁移计划书：**不迁的名字 + 原因在前**（C-270 的形状），能迁的清单与计数在后
DIAG_LEGACY_SKIP_HEAD = "不迁的名字和原因："
DIAG_LEGACY_SKIP_MUX = "mux 用的是 case / 数据列坐标（c:A / d:0），与旧版按物理基名存的取值对不上"
DIAG_LEGACY_SKIP_UNKNOWN = "当前表里找不到这个名字（表改名 / 删行？）"
DIAG_LEGACY_SKIP_KIND_FMT = "根是「{kind}」，不是 logic / 直连寄存器"
DIAG_LEGACY_SKIP_EMPTY = "旧版桶里这个名字下没有可迁的行"
DIAG_LEGACY_ROW_FMT = "{name}　旧版 {n} 行 → 新版 {n} 列"
DIAG_LEGACY_NOTE_LOST = "旧版行上的备注不迁（新版列模型没有备注这一格）"
DIAG_LEGACY_DONE_FMT = "已迁入 {n_sig} 个信号、{n_col} 列；旧版数据只读不删，原样还在"

# ⑩ 时间（「上次导出到哪」「最近打开」两处共用；C4-int 从 app.py 与 export_center.py 各一份收成这里一份）
WHEN_TODAY_FMT = "今天 {hh}:{mm}"
WHEN_YESTERDAY_FMT = "昨天 {hh}:{mm}"
WHEN_DATE_FMT = "%Y-%m-%d %H:%M"           # strftime 模板（不是 str.format）


def fmt_when(ts):
    """ISO 时间串 → 「今天 09:14」/「昨天 17:02」/「2026-09-01 17:02」；空串 → ""。

    ⚠ 认不出的串**原样返回**（不吞、也不编一个时间）：`last_export` / `recent` 里存的是什么
    就让用户看见什么，比显示一个假日期好查。"""
    import datetime
    s = str(ts or "").strip()
    if not s:
        return ""
    try:
        t = datetime.datetime.fromisoformat(s)
    except ValueError:
        return s
    today = datetime.date.today()
    if t.date() == today:
        return WHEN_TODAY_FMT.format(hh="%02d" % t.hour, mm="%02d" % t.minute)
    if (today - t.date()).days == 1:
        return WHEN_YESTERDAY_FMT.format(hh="%02d" % t.hour, mm="%02d" % t.minute)
    return t.strftime(WHEN_DATE_FMT)


# ═════════ 五、异常 → 人话（R3-03 / R2-13 / P-24）═════════
#: `scrub` 只换中文术语，英文异常一字不动 —— 于是错误条上直接是
#: `[Errno 2] No such file or directory: 'C:\\...\\x.xlsx'`（repr 的双反斜杠 + 本机全路径）。
#: 错误路径一律先查这张表；查不到退 `EXC_FALLBACK`，**永远不贴 `str(exc)`**。
#: 键 = 异常类名（按 `__mro__` 逐级找，所以 `JSONDecodeError` 找不到时会落到 `ValueError`）。
EXC_TEXT = {
    "FileNotFoundError": "找不到这个文件（被移走或改名了？）",
    "PermissionError": "这个文件读写不了：多半正被仿真器 / Excel 占着，或者它是只读的",
    "IsADirectoryError": "这是一个文件夹，不是文件",
    "NotADirectoryError": "路径里有一截不是文件夹",
    "FileExistsError": "这个名字已经被别的东西占了",
    "BadZipFile": "这个文件打不开：不是完整的 Excel 工作簿（拷贝 / 下载到一半？）",
    "JSONDecodeError": "这个文件的内容不是合法的 JSON，读不下去",
    "UnicodeDecodeError": "这个文件的编码认不出来（要 UTF-8 纯文本）",
    "TimeoutError": "等这个文件等超时了（网络盘？）",
    "OSError": "这个文件读写失败",
    "ValueError": "这个文件的内容格式不对，读不下去",
}
#: 查不到对应说法时的定版句 —— 说清「哪里出事、别的没事」，不把类名 / traceback 送上屏。
EXC_FALLBACK = "工具内部出错（其余不受影响）"
#: 一句话 + 是哪个文件（路径只给「上级目录 / 文件名」，见 `short_path`）
EXC_WHAT_FMT = "{text}：{what}"
#: JSON 解析失败时补一句坐标（工程师拿它去文本编辑器里跳行）
EXC_JSON_AT_FMT = "{text}（第 {line} 行第 {col} 列起）"
#: 只要坐标那一截（前半句由调用方的模板给，如「不是合法 JSON：…」）
EXC_JSON_POS_FMT = "第 {line} 行第 {col} 列起读不下去"
EXC_JSON_POS_NONE = "读不下去（写法不对）"


def json_pos_text(exc):
    """JSON 解析失败 → 只给「第几行第几列」那一截。取不到坐标就说「写法不对」。

    要的是让工程师在编辑器里跳到那一行，不是把 `Expecting value: line 1 column 3 (char 2)`
    这句英文原文摆上去。"""
    line, col = getattr(exc, "lineno", None), getattr(exc, "colno", None)
    if line and col:
        return EXC_JSON_POS_FMT.format(line=int(line), col=int(col))
    return EXC_JSON_POS_NONE


def short_path(path):
    """路径 → 「上级目录/文件名」。

    界面上要回答的是「哪个文件」，不是「它在这台机器的哪个角落」：全路径既是红线① 禁的
    盘符路径，又常常长到把那句人话挤出可视区；`repr` 出来的双反斜杠更是没人认得。"""
    p = str(path or "").strip().strip('"').strip("'").replace("\\", "/").rstrip("/")
    if not p:
        return ""
    parts = [x for x in p.split("/") if x and not x.endswith(":")]
    if not parts:
        return ""
    return "/".join(parts[-2:])


#: 「这句是引擎自己写给工程师看的中文原因」的判据：有中文、且不带 Python 自己的噪声
#: （errno 码 / repr 出来的对象 / 盘符路径 / 异常类名）。引擎那些
#: 「Topout 页缺 owner 列」「mux 页第 33 行与第 23 行…」才是真正有用的那一句，不能连它一起吞。
_CJK_RE = re.compile(r"[一-鿿]")
_PY_NOISE_RE = re.compile(r"\[Errno\b|Traceback|object has no attribute|"
                          r"'[A-Za-z_][A-Za-z0-9_.]*' object|[A-Za-z]:[\\/]|\w+Error\b|"
                          r"line \d+ column \d+")


def exc_text(exc, path=None):
    """异常（或异常类名）→ 一句给 IC 工程师看的人话。**所有错误路径的唯一出口。**

    三档：
      ① 异常的 message 本身就是引擎写的中文原因（`_CJK_RE` 命中且没有 Python 噪声）
         → 原样留住（过一道 `scrub`）—— 那句话才是工程师要的；
      ② 认识这个异常类（`EXC_TEXT`，按 `__mro__` 逐级找）→ 定版人话
         + 「上级目录/文件名」（`path` 没给就试 `exc.filename`，OSError 一族自带）；
      ③ 都不是 → `EXC_FALLBACK`。宁可说「工具内部出错（其余不受影响）」，
         也不把 Python 的英文异常原文摆到界面上。"""
    tpl = None
    if isinstance(exc, BaseException):
        msg = str(exc).strip()
        if msg and _CJK_RE.search(msg) and not _PY_NOISE_RE.search(msg):
            return scrub(msg)
        for cls in type(exc).__mro__:
            tpl = EXC_TEXT.get(cls.__name__)
            if tpl:
                break
        what = short_path(path if path is not None else (getattr(exc, "filename", "") or ""))
    else:
        tpl = EXC_TEXT.get(str(exc or "").strip())
        what = short_path(path)
    if not tpl:
        return EXC_FALLBACK
    line, col = getattr(exc, "lineno", None), getattr(exc, "colno", None)
    if line and col:
        tpl = EXC_JSON_AT_FMT.format(text=tpl, line=int(line), col=int(col))
    return EXC_WHAT_FMT.format(text=tpl, what=what) if what else tpl


# ⑩ 编辑恢复（C4-int 从 state.py 搬进来 —— 界面文案只住 terms.py，I-13）
STATUS_RESTORE_BAD_FMT = "另有 {n} 个存盘数值读不出来，已逐条跳过（其余编辑不受影响）"   # C-240
#: R2-07：整份编辑文件读不出来（写了一半 / 手改坏了）。原文件已改名另存，这一趟从空白开始。
#: 此前是静默 `{}`，用户看到「一个编辑都没恢复」却没人说为什么，接着第一次编辑就把
#: 别的表的桶与 legacy 九段一起覆盖没了。
EDITS_CORRUPT_BACKED_UP_FMT = "编辑文件读不出来，已另存为 {path}，本次从空白开始"

# 对话框
# ① 四处确认（第四种 `risky_off` 由诊断抽屉的「缺前缀是否强制生成」开关用，C-217）
DLG_CONFIRM_TITLES = {"clear": "清零本信号", "del_neg": "删除全部反例", "auto_fill": "auto→期望",
                      "risky_off": DIAG_RISKY_OFF_TITLE}
DLG_CONFIRM_DEL_NEG_PLAIN_FMT = "将删除本信号全部 {n} 条反例，正向用例保留。确定删除？"
DLG_CONFIRM_NAMES_HEAD = "会丢掉的反例列 —— 名字和原因："
DLG_CONFIRM_NAMES_COUNT_FMT = "共 {n} 列"
DLG_CONFIRM_REASON_NAMED = "自定义命名"
DLG_CONFIRM_REASON_HAND = "手调过错值"
# ② 重命名列
DLG_RENAME_TITLE = "重命名列"
DLG_RENAME_HINT = "只能用字母 / 数字 / 下划线；不能占 T<编号> 这个自动测试保留名；不能与其它列重名。"
# ③ mux 数据值整表
DLG_MUX_DATA_TITLE = "mux 数据值（整表，按物理寄存器同步）"
DLG_MUX_DATA_HINT = "清空 = 恢复自动分配。两条数据路取到相同值时会提示假绿（驱不动，断言必过）。"
DLG_MUX_DATA_HEADERS = ("数据寄存器", "位宽", "当前值", "新值")
DLG_MUX_DATA_EMPTY = "这个信号没有可手填的数据寄存器行。"
# ④ 列设置
DLG_COLUMNS_TITLE = "列设置"
DLG_COLUMNS_HINT = "勾选列常驻第一列，不在这里关。"
# ⑤ 粘贴名单
DLG_PASTE_NAMES_TITLE = "粘贴名单勾选"
DLG_PASTE_NAMES_HINT = "每行一个信号名（可带位宽切片，大小写无关）。"
DLG_PASTE_NAMES_RESULT_FMT = "{names}（共 {m} 个）在当前清单里找不到；其余 {n} 个已勾上"   # R3-08
DLG_PASTE_NAMES_RESULT_ALL_FMT = "已勾上 {n} 个信号"
DLG_PASTE_NAMES_EMPTY = "一个名字都没填。"
# ⑥ 预设
DLG_PRESETS_TITLE = "预设"
DLG_PRESETS_SAVE_TITLE = "存为预设"
DLG_PRESETS_MANAGE_TITLE = "管理预设"
DLG_PRESETS_NAME_HINT = "给当前的勾选 + 筛选起个名字。"
DLG_PRESETS_OVERWRITE_FMT = "已有同名预设「{name}」，存下去会覆盖它。"
DLG_PRESETS_NAME_REQUIRED = "名字不能为空。"
DLG_PRESETS_DELETE = "删除"
DLG_PRESETS_EMPTY = "还没有存过预设。"
# ⑦ 重复标号
DLG_DUP_LABELS_CONTINUE = "仍要写出"
DLG_DUP_LABELS_ROW_FMT = "  {label}  ←  {a} / {b}"
# ⑧ 导入结果（名字在前、计数在后 —— C-194 / C-270 / I-20）
DLG_IMPORT_REPORT_TITLE = "导入结果"
DLG_IMPORT_MISSING_HEAD = "配置里有、当前表里找不到的信号 —— 名字和原因："
DLG_IMPORT_MISSING_COUNT_FMT = "共 {n} 个（这些跳过，其余照常导入）"
DLG_IMPORT_MISSING_MORE_FMT = "…（只列前 {k} 个）"
DLG_IMPORT_ROW_FMT = "{name}　└ {reason}"
DLG_IMPORT_MISSING_REASON = "当前表里没有这个信号（改名？删行？该页不存在？）"
DLG_IMPORT_NONE = "没有找不到的信号。"
# ⑨ 批量填期望
DLG_BATCH_FILL_TITLE = "批量填期望"
DLG_BATCH_FILL_HINT = "把这个值填进所有选中列的期望格（未选则全部未填的正向列）。"
DLG_BATCH_FILL_MODES = {"const": "填一个固定值", "auto": "取程序算的值（auto）", "clear": "清空期望"}
DLG_BATCH_FILL_SCOPE_SELECTED_FMT = "只填选中的 {n} 列"
DLG_BATCH_FILL_SCOPE_ALL_FMT = "全部 {n} 列"
DLG_BATCH_FILL_SCOPE_LABEL = "范围"
# ⑩ .sv 导出选项
DLG_EXPORT_OPTIONS_TITLE = "导出 .sv 选项"
DLG_EXPORT_SCOPE_LABEL = "范围"
DLG_YES = "确定"
DLG_NO = "取消"


def all_copy():
    """{常量名: 文案}——测试扫描用（含 dict/tuple 里的字符串会被 test 展开）。"""
    return {k: v for k, v in globals().items()
            if k.isupper() and not k.startswith("_") and isinstance(v, (str, tuple, dict))}
