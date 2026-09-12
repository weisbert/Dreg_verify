# -*- coding: utf-8 -*-
"""exports.py — 导出编排层（**不 import PySide6**，可单测、可给 CLI 复用）。

GUI 的「导出中心」骨架（GUI v2 执行计划 Phase A4d / Design prompt §3 Q5「6 种交付物一张表」）：
把『渲染 → 落盘 → 统计 → 点名跳过了谁、为什么』集中到一处，GUI 只负责
「弹对话框拿参数 → 调本模块 → 显示 ExportOutcome」。

分工（护栏）：
  · 本模块：编排 + 文件 IO + 统计 + 跳过明细翻译（**零 Qt**）。
  · gui.py：对话框、文件选择、弹框；拿到 ExportOutcome 后只负责显示。
  · generator/topout/pageviews/sv_writer/fortest_writer：产物本体，本模块一个字节都不碰。

交付物（ExportOutcome.kind）：
  sv / report_html / report_csv / report_xlsx / fortest / nets / claims / config / signal_csv

设计原则（gui-feedback：跳过内容必须可见，名字 + 原因 > 数量）：
  每个 outcome 都带 skipped=[(信号名, 原因)]；detail_text() 把跳过项放在计数之前。
"""

import contextlib
import csv
import io
import json
import os
import re
from dataclasses import dataclass, field

from . import expr as E
from . import generator
from . import inputs_table as IT
from . import sv_writer as W

# ═════════════════════════ 一、导出选项（.sv）：一套默认值 + 一套 settings 键 ═════════════════════════
# ⚠ 历史差异（2026-09-12 统一）：SignalView 的 `_ask_export_options()` 与『排查(旧)』的
#   `_ask_export_options(title)` 共用下面这组 settings 键，默认值却不同——
#     SignalView：comments=False, sv_summary=False, owner_in_msg=False
#     legacy    ：comments=False, sv_summary=True,  owner_in_msg=True
#   同一份磁盘配置被两套默认值解释，"没配过"时两个入口给出不同产物（静默不一致）。
#   现统一以 **SignalView 版为准**（下面的 EXPORT_OPTION_DEFAULTS），两边对话框都读它。
#   注：MainWindow._opts() 读同名键时另有一套 True 兜底（预览路径），不在本次统一范围内。
EXPORT_OPTION_DEFAULTS = {
    "scope": "all",            # all=正向+负向 / pos=仅正向 / neg=仅负向
    "comments": False,         # 加注释（默认关 = 纯语句体便于 diff）
    "sv_summary": False,       # 末尾测试汇总 + 计数器（命名 begin/end 块）
    "owner_in_msg": False,     # 断言消息尾部追加 owner
}
# 选项名 → settings 键名（两个对话框共用，别再各写各的字面量）
EXPORT_OPTION_KEYS = {
    "scope": "export_scope",
    "comments": "export_comments",
    "sv_summary": "export_sv_summary",
    "owner_in_msg": "export_owner_in_msg",
}
#: scope 取值 → 人读标签。`split`（正向 / 负向各写一个文件，走 `export_sv_split`）是
#: **界面上的第四档**：v2 导出中心把它当 `export_scope` 存进 settings，而 `load_export_options`
#: 只放行本表里认识的键 —— 不列它就会「存得进、读不回」（下次打开静默退回「全部」，
#: 用户看不出是自己没选还是工具忘了）。渲染路径一个字节不变：`export_sv_split` 自己按
#: pos / neg 各渲一趟，`render_sv` 永远收不到 `scope="split"`。
SCOPE_LABEL = {"all": "全部（正向 + 负向）", "pos": "仅正向", "neg": "仅负向",
               "split": "正向 + 负向分文件"}


def load_export_options(settings):
    """从 settings dict 读出上次的导出选项（缺项用统一默认值兜底）。settings 为 None → 全默认。"""
    st = settings or {}
    opt = {}
    for name, default in EXPORT_OPTION_DEFAULTS.items():
        v = st.get(EXPORT_OPTION_KEYS[name], default)
        opt[name] = str(v) if name == "scope" else bool(v)
    if opt["scope"] not in SCOPE_LABEL:
        opt["scope"] = EXPORT_OPTION_DEFAULTS["scope"]
    return opt


def store_export_options(settings, opt):
    """把本次导出选项写回 settings dict（原地更新并返回，落盘由调用方负责）。"""
    st = settings if isinstance(settings, dict) else {}
    for name, key in EXPORT_OPTION_KEYS.items():
        if name in opt:
            st[key] = opt[name]
    return st


# ═════════════════════════ 二、统一结果对象 ═════════════════════════
class ExportError(Exception):
    """导出失败——message 已是给 IC 工程师看的中文原因（GUI 直接弹，不再二次加工）。"""


KIND_LABEL = {
    "sv": ".sv 断言",
    "report_html": "HTML 报告",
    "report_csv": "CSV 报告",
    "report_xlsx": "Excel 报告",
    "fortest": "for_test 回填",
    "nets": "nets.txt 网清单",
    "claims": "claims.json 校验契约",
    "config": "配置 JSON",
    "signal_csv": "单信号真值表 CSV",
}
# 计数键 → 人读标签（counts 是有序 dict，显示顺序 = 插入顺序）
COUNT_LABELS = {
    "n_signals": "信号",
    "n_blocks": "断言块",
    "n_vectors": "测试用例",
    "n_negative": "其中负向",
    "n_designer": "其中 designer 手填期望",
    "n_fallback": "其中 auto_out 兜底",
    "n_accounted": "记账(不产断言)",
    "n_dup_labels": "重复 assert 标号",
    "n_customized": "含已自定义测试项的信号",
    "n_tests": "用例",
    "n_neg": "负向用例",
    "n_groups": "回填组数",
    "n_logic": "logic 信号",
    "n_mux": "mux 信号",
    "n_nets": "网",
    "n_claims": "claim",
    "n_probe": "探针 claim",
    "n_force": "force claim",
    "n_rfwrite": "rfwrite claim",
    "n_columns": "测试列",
    "n_inputs": "输入行",
    # N3 nets.txt 按用途三类（冲突⑤）——与按页类别并存，两套都往 counts 里塞
    "topout_out": "顶层输出",
    "force_target": "force 目标",
    "guessed": "猜名的网",
}


@dataclass
class ExportOutcome:
    """一次导出的统一结果：**所有** GUI 反馈都从它来（摘要一行 + 明细多行）。

    kind     : 交付物类型（KIND_LABEL 的键）
    path     : 主产物路径（CSV 报告这种一次写多份的，全部在 paths 里）
    counts   : 计数 {键: 数}，按交付物不同；键的人读标签见 COUNT_LABELS
    skipped  : [(信号名, 原因)] —— 名字 + 原因，原因用工程师语言（数量永远不够，要点名）
    dup_labels: [(标号, 信号A, 信号B)] 重复 assert 标号（非法 SV），导出前该弹确认
    warnings : 额外提醒（如 auto_out 兜底有自证嫌疑）
    """

    kind: str
    path: str = ""
    counts: dict = field(default_factory=dict)
    skipped: list = field(default_factory=list)
    dup_labels: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    paths: list = field(default_factory=list)
    note: str = ""

    def __post_init__(self):
        if not self.paths:
            self.paths = [self.path] if self.path else []

    # ── 显示 ──
    def label(self):
        return KIND_LABEL.get(self.kind, self.kind)

    def counts_text(self):
        return "；".join("%s %s" % (COUNT_LABELS.get(k, k), v) for k, v in self.counts.items())

    def summary_text(self):
        """一行摘要（GUI 弹框正文）：交付物 + 路径 + 计数（+ 跳过/重复标号点数，明细见 detail_text）。"""
        parts = ["%s已导出：%s" % (self.label(), self.path or "(未落盘)")]
        ct = self.counts_text()
        if ct:
            parts.append(ct)
        if len(self.paths) > 1:
            parts.append("另写出 %d 份附表" % (len(self.paths) - 1))
        if self.skipped:
            parts.append("↷ 跳过 %d 个信号（点『Show Details』看名字和原因）" % len(self.skipped))
        if self.dup_labels:
            parts.append("⛔ 重复 assert 标号 %d 处" % len(self.dup_labels))
        if self.note:
            parts.append(self.note)
        return " ｜ ".join(parts)

    def detail_text(self, limit=None):
        """明细：**跳过项在前**（名字 + 原因），计数在后，最后是警告/附表。
        limit 给了就只列前 N 个跳过项（弹框正文用；不给=全列，给 setDetailedText/日志用）。"""
        out = []
        if self.skipped:
            shown = self.skipped if limit is None else self.skipped[:int(limit)]
            out.append("跳过 %d 个信号（本次产物里没有它们）：" % len(self.skipped))
            for name, reason in shown:
                out.append("  ↷ %s" % name)
                for line in str(reason or "原因未记录").splitlines():
                    out.append("      %s" % line)
            if len(shown) < len(self.skipped):
                out.append("  …等共 %d 个（完整账目见 HTML 报告的『可验证性』页）" % len(self.skipped))
            out.append("")
        if self.dup_labels:
            out.append("重复 assert 标号 %d 处（同一作用域内重复 → elaboration 失败）：" % len(self.dup_labels))
            out.append(dup_label_text(self.dup_labels))
            out.append("")
        if self.counts:
            out.append("计数：")
            for k, v in self.counts.items():
                out.append("  %s %s" % (COUNT_LABELS.get(k, k), v))
        if self.warnings:
            out.append("")
            out.append("提醒：")
            for w in self.warnings:
                out.append("  · %s" % w)
        if len(self.paths) > 1:
            out.append("")
            out.append("写出的文件：")
            for p in self.paths:
                out.append("  %s" % p)
        return "\n".join(out).rstrip()

    def has_detail(self):
        return bool(self.skipped or self.dup_labels or self.counts or self.warnings
                    or len(self.paths) > 1)


# ═════════════════════════ 三、跳过原因：翻成工程师语言 ═════════════════════════
# 账目状态（topout.build_for_topout 的 accounted[].status）→ 一句话说明。
# 原则：说清楚「为什么这个信号不在产物里」，别只给状态码。
SKIP_STATUS_LABEL = {
    "skipped": "被跳过",
    "skip": "RO 回读信号，本就不产断言",
    "unresolved": "输入没解析到 net（ENV_RF 探不到，仿真会 CUVUNF）",
    "error": "分析出错",
    "dup-name": "Topout 页同名重复行（与已产出的同名信号共用断言标号，重复产出=非法 SV）",
    "dup-source": "与已产出的信号是同一个源对象（避免断言标号重复，只产一次）",
    "cleared": "用户已清空该信号的测试列（零用例）",
    "scope-empty": "本次导出范围里它没有用例",
    "renamed-mux": "dft 改名指向 mux 源，探针名覆盖暂不支持",
    "needs-prefix": "输入缺探针前缀（force 基名钉不住，没配前缀必 CUVUNF）——先跑 scan_rtl 配前缀",
    "spec-collision": "mux 规格冲突：同一控制选择值选了不同数据源，整组跳过（待 designer 核对改表）",
    "false-green": "数据寄存器字段太窄，硬生成会变『接错路也 PASS』的假测试，保护性跳过",
    "no-tests": "本信号没有测试用例（明细表里不会出现）",
    "mux-not-fortest": "mux 结构与 for_test 的 logic cone 真值表排版不同，不回填",
}


def skip_reason(status, reason=""):
    """账目状态 + 原文 → 一条给人看的原因。两者都有时：状态说明在前、原文在后（原文常带表行号/信号名）。"""
    label = SKIP_STATUS_LABEL.get(str(status or ""), str(status or "") or "未说明")
    reason = (reason or "").strip()
    if not reason:
        return label
    if reason == label:
        return label
    return "%s；%s" % (label, reason)


def risky_reason(risky):
    """generator.build 的 skipped[(name, aid, risky)] 里那份 risky → 一条原因。
    risky = [(字母, 输入名, 原因)]：哪些输入不可驱动、为什么。"""
    parts = ["%s = %s：%s" % (letter, base, note or "不可驱动") for letter, base, note in (risky or [])]
    head = SKIP_STATUS_LABEL["needs-prefix"] if parts else SKIP_STATUS_LABEL["skipped"]
    return "%s\n%s" % (head, "\n".join("  " + p for p in parts)) if parts else head


def skipped_detail_text(skipped):
    """被跳过信号的明细文本：每个信号一段，列出哪些输入不可驱动及原因。
    skipped: list[(out_name, assert_id, risky)]，risky = list[(字母, 输入名, 原因)]。
    （原 gui._skipped_detail_text，2026-09-12 搬进 exports；gui 侧保留同名别名。）"""
    parts = []
    for name, _aid, risky in skipped:
        reasons = "\n".join("    %s = %s：%s" % (letter, base, note or "不可驱动")
                            for letter, base, note in risky)
        parts.append("%s\n%s" % (name, reasons))
    return ("跳过原因：以下输入在 ENV_RF 层探不到（force 会 elaboration 失败）。\n"
            "如需强制生成：勾选工具栏「缺前缀强制生成」——\n"
            "裸名 force 交给仿真验证；仿真过=此设计不需前缀，CUVUNF 则跑 scan_rtl 配前缀。\n\n"
            + "\n\n".join(parts))


def build_skipped(build):
    """构建结果 → [(信号名, 原因)]。两种 build 结构都吃：
    · Topout/页 provider：跳过在 accounted[{name,kind,status,reason}]
    · generator.build   ：跳过在 skipped[(name,aid,risky)] 与 errors[(name,aid,msg)]"""
    out = []
    for a in (build.get("accounted") or []):
        out.append((a.get("name", ""), skip_reason(a.get("status", ""), a.get("reason", ""))))
    for item in (build.get("skipped") or []):
        try:
            name, _aid, risky = item
        except (TypeError, ValueError):
            continue
        out.append((str(name), risky_reason(risky)))
    for item in (build.get("errors") or []):
        try:
            name, _aid, msg = item
        except (TypeError, ValueError):
            continue
        out.append((str(name), skip_reason("error", str(msg))))
    return out


def dup_label_text(dups, limit=15):
    """重复 assert 标号的数据部分（弹框正文由 GUI 拼）：每行『标号 ← 信号A / 信号B』。"""
    dups = list(dups or [])
    lines = "\n".join("  %s  ←  %s / %s" % (lbl, a, b) for lbl, a, b in dups[:limit])
    if len(dups) > limit:
        lines += "\n  …(共 %d 处)" % len(dups)
    return lines


# ═════════════════════════ 四、文件 IO（错误翻译） ═════════════════════════
def write_text(path, text, encoding="utf-8", newline=None):
    """写文本产物。OSError → ExportError（带『是不是被仿真器/编辑器占用』的提示）。

    newline 默认 None = 平台换行（.sv/nets.txt 与旧行为逐字节一致，Windows 下仍是 CRLF）；
    CSV 传 newline="" 让 csv 模块自己控制行尾（否则会写出 \\r\\r\\n）。"""
    try:
        with open(path, "w", encoding=encoding, newline=newline) as f:
            f.write(text)
    except OSError as ex:
        raise ExportError("无法写入 %s：\n%s\n\n(文件是否正被仿真器/编辑器占用？)" % (path, ex)) from ex
    return path


def write_json(path, payload, indent=1):
    """写 JSON 配置（导出完整配置用）。返回 kind='config' 的 outcome。"""
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=indent)
    except OSError as ex:
        raise ExportError("无法写入 %s：\n%s" % (path, ex)) from ex
    return ExportOutcome(kind="config", path=path)


def read_json(path):
    """读 JSON 配置。OSError/格式错 → ExportError（沿用原提示措辞）。"""
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError) as ex:
        raise ExportError("无法读取/解析 %s：\n%s" % (path, ex)) from ex


# ═════════════════════════ 五、.sv ═════════════════════════
def render_sv(provider, only=None, mode="min", max_tests=256, exhaustive=False, edited=None,
              options=None, sig_cov=None, form_cov=None):
    """渲染 .sv（不落盘）——预览与导出走同一条路径（所见即所得）。返回 (text, build)。

    options=None → 用 provider 自己的默认（预览路径，逐字节与旧行为一致）；
    options=dict → 按导出对话框的选择渲染（缺项用 EXPORT_OPTION_DEFAULTS 兜底）。"""
    kw = {}
    if options is not None:
        o = dict(EXPORT_OPTION_DEFAULTS)
        o.update(options or {})
        kw = {"comments": o["comments"], "sv_summary": o["sv_summary"],
              "owner_in_msg": o["owner_in_msg"], "scope": o["scope"]}
        # block_suffix（C-170）只有 export_sv_split 会传，且不进 EXPORT_OPTION_DEFAULTS/KEYS
        # ——它是「这一次导出」的事，不是要记盘的界面偏好。空串 = 不加后缀，就不必打扰
        # provider（预览与普通导出这一路的调用签名逐字不变）；非空才传，provider 必须收得住
        # （`ui/contracts.ProviderProto.render_sv` 已把 block_suffix 写进接口）。
        bs = str(o.get("block_suffix") or "")
        if bs:
            kw["block_suffix"] = bs
    return provider.render_sv(only, mode, max_tests, exhaustive, edited,
                              sig_cov=sig_cov, form_cov=form_cov, **kw)


def sv_outcome(path, build, scope="all"):
    """.sv 构建结果 → ExportOutcome（两种 build 结构都吃，见 build_skipped）。"""
    s = build.get("summary") or {}
    counts = {}
    if "n_total" in s:                       # Topout/页 provider 结构
        counts["n_signals"] = s.get("n_total", 0)
        counts["n_blocks"] = s.get("n_emitted", 0)
    else:                                    # generator.build 结构
        counts["n_blocks"] = s.get("n_generated", 0)
    n_vec, n_neg = s.get("n_vectors", 0), s.get("n_negative", 0)
    n_dsgn = s.get("n_designer", 0)
    counts["n_vectors"] = n_vec
    counts["n_negative"] = n_neg
    counts["n_designer"] = n_dsgn
    n_pos = max(n_vec - n_neg, 0)
    if scope != "neg":
        counts["n_fallback"] = max(n_pos - n_dsgn, 0)
    skipped = build_skipped(build)
    if skipped:
        counts["n_accounted"] = len(skipped)
    dups = list(build.get("dup_labels") or [])
    if dups:
        counts["n_dup_labels"] = len(dups)
    warnings = []
    if scope != "neg" and counts.get("n_fallback"):
        warnings.append("auto_out 兜底 %d 条 = 期望没手填、直接拿表达式算出的值去对比表达式，有自证嫌疑；"
                        "建议在编辑器手填期望，或用 HTML 报告『真值表检查』页核对。" % counts["n_fallback"])
    if scope in ("pos", "neg"):
        warnings.append("本次导出范围：%s（范围外的用例不在产物里）。" % SCOPE_LABEL.get(scope, scope))
    if build.get("skipped"):        # 输入不可驱动被跳过 → 给出「怎么让它生成」的下一步
        warnings.append("如需强制生成：勾选工具栏「缺前缀强制生成」——裸名 force 交给仿真验证；"
                        "仿真过=此设计不需前缀，CUVUNF 则跑 scan_rtl 配前缀。")
    return ExportOutcome(kind="sv", path=path, counts=counts, skipped=skipped,
                         dup_labels=dups, warnings=warnings)


def export_sv(provider, path, only=None, mode="min", max_tests=256, exhaustive=False, edited=None,
              options=None, sig_cov=None, form_cov=None, text=None, build=None):
    """渲染（或复用已渲染的 text/build）→ 落盘 → outcome。返回 (text, outcome)。

    ⚠ 重复 assert 标号的确认弹框在 GUI 侧：先 render_sv 拿 build、确认后再调本函数
    （传 text/build 复用，不重算）——别把 Qt 对话框拖进本模块。"""
    if text is None or build is None:
        text, build = render_sv(provider, only=only, mode=mode, max_tests=max_tests,
                                exhaustive=exhaustive, edited=edited, options=options,
                                sig_cov=sig_cov, form_cov=form_cov)
    write_text(path, text)
    scope = (options or {}).get("scope", "all")
    return text, sv_outcome(path, build, scope=scope)


# N2（冲突④）：导出选项「正向 + 反例分文件」——export_sv 一次只出一份，这里编排出两份。
SPLIT_SUFFIX = {"pos": "_pos", "neg": "_neg"}


def split_sv_paths(base_path):
    """分文件导出的两个落盘路径：`<stem>_pos.sv` / `<stem>_neg.sv`（C-169）。
    导出前摘要要先把路径显给用户看（别等写完了才知道写去了哪），故单独开口。"""
    stem, ext = os.path.splitext(base_path or "")
    ext = ext or ".sv"
    return stem + SPLIT_SUFFIX["pos"] + ext, stem + SPLIT_SUFFIX["neg"] + ext


def export_sv_split(provider, base_path, only=None, mode="min", max_tests=256, exhaustive=False,
                    edited=None, options=None, sig_cov=None, form_cov=None):
    """.sv **正向 / 反例分两个文件**（C-169/C-170）。返回 (pos_outcome, neg_outcome)。

    两次独立的 render（scope="pos" / "neg"，各自 build），不是把一份切两半——
    负向用例的来源、跳过原因、重复标号都要各算各的，否则两份产物的统计对不上自己那份文件。
    汇总命名块各带 `_pos`/`_neg` 后缀（C-170）：两份贴进同一个 testcase 时同名命名块是非法 SV。
    调用方给的 options 里的 scope 会被忽略（分文件本身就定死了范围）。
    """
    pos_path, neg_path = split_sv_paths(base_path)
    outs = []
    for scope, path in (("pos", pos_path), ("neg", neg_path)):
        o = dict(options or {})
        o["scope"] = scope
        o["block_suffix"] = SPLIT_SUFFIX[scope]
        _text, out = export_sv(provider, path, only=only, mode=mode, max_tests=max_tests,
                               exhaustive=exhaustive, edited=edited, options=o,
                               sig_cov=sig_cov, form_cov=form_cov)
        outs.append(out)
    return outs[0], outs[1]


# ═════════════════════════ 六、报告（HTML / CSV / Excel 一个函数按扩展名分派） ═════════════════════════
# 文件对话框的格式筛选器（两个入口共用同一份，免得一边有 Excel 一边没有）
REPORT_FILTERS = [("HTML 网页 (*.html)", ".html"),
                  ("Excel 工作簿 (*.xlsx)", ".xlsx"),
                  ("CSV 表格 (*.csv)", ".csv")]
REPORT_FILTER_STR = ";;".join(f for f, _e in REPORT_FILTERS)
_REPORT_EXTS = (".html", ".htm", ".xlsx", ".csv")


def correct_report_ext(path, selected_filter=None):
    """按所选筛选器补正扩展名（筛选器=显式格式选择，权威）：先剥掉任何已知报告扩展名，
    再补所选格式的。『默认名 dreg_report.html + 选 Excel』→ .xlsx，不会出双扩展名。"""
    want = next((e for f, e in REPORT_FILTERS if f == selected_filter), None)
    if not want:
        return path
    base = path
    for e in _REPORT_EXTS:
        if base.lower().endswith(e):
            base = base[:-len(e)]
            break
    return base + want


def report_kind(path):
    ext = os.path.splitext(path)[1].lower()
    if ext in (".html", ".htm"):
        return "report_html"
    if ext == ".xlsx":
        return "report_xlsx"
    return "report_csv"


def attach_report_graphs(wb, rep, probe_prefixes=None, mode="min", max_tests=256,
                         exhaustive=False, only=None, resolver=None):
    """C-285：把每个信号的电路图内联进 HTML 报告的 `rep['tables']`（键 `sigflow_svg`）。返回张数。

    与 CLI 主路径（`cli.cmd_topout` 里 `--no-sigflow-svg` **不给**时那一条）同一个写出器
    `sigflow.attach_report_svgs`，所以两边的报告 HTML 结构一致。

    为什么这层薄封装必须在 `exports` 而不是在 GUI 里：`ui/` 的分层规则（架构 §1.2 / I-19）
    禁止视图 import `sigflow`；而「报告要不要内联图」是导出编排的事，不是渲染器的事。
    **只碰报告 HTML**：`rep['tables']` 多一个键，`.sv` / 向量 / 账目一个字节都不动
    （byte-gate 的 6 个 sha 与这条路径无关）。

    永不抛：装不上图只是这份报告没有图，报告照常写出（与 `attach_report_svgs` 的容错口径一致）。
    """
    try:
        from . import sigflow as SF
        return SF.attach_report_svgs(wb, rep, probe_prefixes=probe_prefixes, resolver=resolver,
                                     mode=mode, max_tests=max_tests, exhaustive=exhaustive,
                                     only=only)
    except Exception:                      # noqa: BLE001 —— 没有图 < 没有报告
        return 0


def export_report(path, rep, excel, selected_filter=None):
    """导出『给人看』的报告：按扩展名分派 HTML / Excel / CSV（复用 cli.write_report 的写出器）。

    · 扩展名纠正：selected_filter 给了就以它为准（见 correct_report_ext）。
    · 计数：信号数 / 用例数 / 负向数。
    · 跳过点名：没有任何用例的信号——它们在明细表里根本不出现，必须说出名字。"""
    from . import cli                      # 延迟导入：cli 拉 openpyxl 等重家伙
    path = correct_report_ext(path, selected_filter)
    try:
        written = cli.write_report(path, rep, excel or "excel")
    except OSError as ex:
        raise ExportError("无法写出报告 %s：\n%s\n\n(文件是否正被 Excel/浏览器占用？)" % (path, ex)) from ex
    except Exception as ex:                # noqa: BLE001 —— 渲染器里的任何错都翻成人读原因
        raise ExportError("报告写出失败：\n%s" % ex) from ex
    detail = rep.get("detail") or []
    counts = {"n_signals": len(rep.get("summary") or []),
              "n_tests": len(detail),
              "n_neg": sum(1 for r in detail if r.get("neg") == "是")}
    skipped = []
    for row in (rep.get("summary") or []):
        try:
            n_tests = int(row.get("n_tests") or 0)
        except (TypeError, ValueError):
            n_tests = 0
        if n_tests:
            continue
        why = (row.get("error") or row.get("unresolved") or "").strip()
        skipped.append((row.get("signal") or "", skip_reason("no-tests", why)))
    return ExportOutcome(kind=report_kind(path), path=path, counts=counts,
                         skipped=skipped, paths=list(written))


# ═════════════════════════ 七、for_test 回填 ═════════════════════════
def normalize_fortest_path(path, src):
    """回填产物路径规整：自动补 .xlsx；与源表同一个文件则拒绝（回填产物是新文件，源文件不动）。"""
    if not path.lower().endswith(".xlsx"):
        path += ".xlsx"
    if src and os.path.abspath(path) == os.path.abspath(src):
        raise ExportError("输出文件不能是源 Excel 本身(回填产物是新文件，源文件不动)")
    return path


def export_fortest(src, path, provider=None, wb=None, opts=None, mode="min", max_tests=256,
                   exhaustive=False, only=None):
    """把当前测试项按 for_test 真值表排版回填到【新】Excel（两条路径合一）：

    · provider 给的是 SignalView 视图（Topout/子视图，含 mux 表）；
    · wb + opts 给『排查(旧)』（generator.report + fortest_writer，mux 不进 for_test）。

    统一做到：补 .xlsx、防覆盖源表、报组数、mux 未回填时**点名**（不是只报个数）。"""
    if not src or not os.path.isfile(src):
        raise ExportError("回填要复制源 Excel 的全部 sheet——请先加载有效的源 .xlsx")
    path = normalize_fortest_path(path, src)
    counts, skipped, note = {}, [], ""
    try:
        if provider is not None:
            n_grp = provider.fortest(src, path, mode, max_tests, exhaustive, only=only)
            note = "含 mux 表"
            if isinstance(n_grp, int):
                counts["n_groups"] = n_grp
        else:
            from . import fortest_writer
            rep = generator.report(wb, opts)
            counts["n_groups"] = fortest_writer.write_fortest(src, path, rep)
            tables = rep.get("tables") or []
            counts["n_logic"] = sum(1 for t in tables if t.get("is_logic") and t.get("tests"))
            for t in tables:
                if not t.get("is_logic"):
                    skipped.append((t.get("signal") or t.get("name") or "(未命名 mux)",
                                    skip_reason("mux-not-fortest")))
    except ExportError:
        raise
    except OSError as ex:
        raise ExportError("无法写出 %s：\n%s\n\n(文件是否正被 Excel 占用？)" % (path, ex)) from ex
    except Exception as ex:                # noqa: BLE001
        raise ExportError("回填失败：\n%s" % ex) from ex
    out = ExportOutcome(kind="fortest", path=path, counts=counts, skipped=skipped, note=note)
    out.warnings.append("源 Excel 各 sheet 数据已复制，源文件未改；图表/图片等非数据元素可能不保留。")
    return out


# ═════════════════════════ 八、nets.txt ═════════════════════════
# （类别的界面标签/tooltip 留在 gui._NETS_CAT_INFO —— 那是对话框文案，不是编排层的事。）
def nets_categories(wb):
    """当前表【有内容】的导出类别（键）：Topout（有 Topout 页）+ 四子模块页中可用的那些。
    与 GUI 顶层 tab 一一对应——空页不出现在导出框里（空页导出也是空，无意义）。"""
    cats = []
    if getattr(wb, "topout", None):
        cats.append("topout-cone")       # ⭐第一性推荐：探针 + cone 输入
        cats.append("topout")            # 仅探针(降级保留)
    try:
        from . import pageviews as P
        cats += [p for p in P.PAGES if P.page_available(wb, p)]
    except Exception:  # noqa: BLE001
        cats += ["logic", "mux", "dft", "iddq"]
    return cats


def collect_nets(wb, pages):
    """收集网清单 + 每类计数（去重并集）。返回 (nets, {类别: 条数})。

    ⚠ 副作用：collect_excel_nets/collect_topout_nets 会临时改 wb 上的尾缀标记，调用方
    （GUI）收集完必须重建 Resolver 还原——这一步留在 GUI 侧（它才有 resolver）。"""
    from . import rtl_scan
    pages = sorted(pages or [])
    try:
        nets = rtl_scan.collect_nets(wb, pages=pages)
        per = {p: len(rtl_scan.collect_nets(wb, pages=[p])) for p in pages}
    except Exception as ex:  # noqa: BLE001
        raise ExportError("收集网清单出错：\n%s" % ex) from ex
    return nets, per


def export_nets(wb, path, pages):
    """导出 nets.txt：当前表需在 ENV_RF 层定位的网清单，供仿真服务器跑 scan_rtl 扫 RTL。
    返回 (nets, outcome)——nets 交给调用方做后续提示，outcome 负责显示。"""
    from . import rtl_scan
    nets, per = collect_nets(wb, pages)
    write_text(path, rtl_scan.render_nets_text(nets))
    counts = {"n_nets": len(nets)}
    for p in sorted(per):
        counts[p] = per[p]
    return nets, ExportOutcome(kind="nets", path=path, counts=counts,
                               note="按类别去重并集")


# ── N3（冲突⑤）：**按用途**三类，与上面的「按页」类别并存 ──────────────────────────
# 按页分类（logic/mux/dft/iddq/topout…）是工具内部视角；红区那位工程师问的其实是
# 「我要扫的是哪些网」——顶层输出 / 要 force 的目标 / 名字是猜出来的。两套并存：
# 旧 nets_pages 键继续读写（同事机器上已有值，不能删），新三类是叠加层。
NETS_PURPOSES = ("topout_out", "force_target", "guessed")
_NET_NAME_RE = re.compile(r"^[A-Za-z_]\w*$")     # 与 rtl_scan 同一道「像不像个网名」的关（位宽/层级已剥）
NETS_PURPOSE_NOTE = {
    "topout_out": "顶层输出（assert 探针贴的网）",
    "force_target": "force 目标（cone 展到底的输入叶子 + iddq 门网）",
    "guessed": "猜名的网（不在寄存器表里，按命名约定推出来的——最该先核对）",
}


def nets_purpose_categories():
    """按用途的三类键（次序 = 导出中心的显示次序）。无参：三类恒定，不随表变。"""
    return NETS_PURPOSES


def _guessed_cone_nets(wb, probe_prefixes=None, resolver=None):
    """cone 输入叶子里【名字是猜的】那些：found_in ∉ inputs_table.TRUSTED_FOUND_IN。

    判据只用 binding 的 found_in（tmm/regmap 才算真查到），与输入信号表的 `trusted` 同一口径——
    界面说「这根不是表里查到的」和 nets.txt 里导出来的那批必须是同一批，否则工程师核对时对不上账。
    ⚠ 口径注意：本类别 = `not trusted`（**含** needs-prefix/mux-output），比输入行的 `guessed`
      布尔宽一档（那个把「需前缀」单拆出去了）——因为要扫的网清单里，这两种都得让 scan_rtl 去核。
    任何异常 → 已收的照返，绝不波及别的类别（与 rtl_scan 的容错口径一致）。
    """
    from . import topout as T
    from . import resolver as R
    from .excel_model import _strip_width
    nets = {}
    try:
        rs = resolver if resolver is not None else R.Resolver(wb, wire_prefixes=probe_prefixes)
        for t in (getattr(wb, "topout", None) or []):
            try:
                res = T.analyze_signal(wb, rs, t, mode="min", want_vectors=True)
            except Exception:  # noqa: BLE001  单信号失败跳过，不连累整批
                continue
            if res.status != "ok":
                continue
            for b in (res.bindings or {}).values():
                fi = getattr(b, "found_in", "") or ""
                if not fi or fi in IT.TRUSTED_FOUND_IN:
                    continue
                raw = str(getattr(b, "wire", "") or getattr(b, "base", "") or "").split(".")[-1]
                base = _strip_width(raw)[0]
                if base and _NET_NAME_RE.match(base):
                    nets.setdefault(base, "Topout %s 的输入 %s（%s：按命名约定猜的名字）"
                                    % (t.name, getattr(b, "base", base),
                                       IT.FOUND_IN_LABEL.get(fi, fi)))
    except Exception:  # noqa: BLE001
        return nets
    return nets


def collect_nets_by_purpose(wb, purposes, probe_prefixes=None, resolver=None):
    """按用途收集网清单 + 每类计数（去重并集）。返回 (nets, {用途: 条数})——与 collect_nets 同形。

    topout_out   = rtl_scan.collect_topout_nets（每个可验证 Topout 信号的 assert 探针网）
    force_target = collect_topout_cone_nets 里【探针以外】的那批 = cone 展到底的 RO/force 输入叶子
                   + iddq 门网（RW 输入走 RF_WRITE、按地址写，不是物理网，故不在内）
    guessed      = 上面这些输入叶子里 found_in ∉ TRUSTED_FOUND_IN 的（与输入表的「猜名」同判据）

    ⚠ 副作用同 collect_nets：收集过程会临时改 wb 上的尾缀标记，调用方收完须重建 Resolver 还原。
    """
    from . import rtl_scan
    want = [p for p in NETS_PURPOSES if p in {str(x).strip().lower() for x in (purposes or [])}]
    nets, per = {}, {}
    try:
        probe = rtl_scan.collect_topout_nets(wb) if want else {}
        if "topout_out" in want:
            per["topout_out"] = len(probe)
            for k, v in probe.items():
                nets.setdefault(k, v)
        if "force_target" in want:
            cone = rtl_scan.collect_topout_cone_nets(wb, probe_prefixes=probe_prefixes)
            leaves = {k: v for k, v in cone.items() if k not in probe}
            per["force_target"] = len(leaves)
            for k, v in leaves.items():
                nets.setdefault(k, v)
        if "guessed" in want:
            g = _guessed_cone_nets(wb, probe_prefixes=probe_prefixes, resolver=resolver)
            per["guessed"] = len(g)
            for k, v in g.items():
                nets.setdefault(k, v)
    except Exception as ex:  # noqa: BLE001
        raise ExportError("收集网清单出错：\n%s" % ex) from ex
    return nets, per


def export_nets_by_purpose(wb, path, purposes, pages=None, probe_prefixes=None, resolver=None):
    """导出 nets.txt（按用途三类）。pages 非空时**并上**旧的「按页」类别（两套口径可同时勾）。
    返回 (nets, outcome)——与 export_nets 同形，GUI 的显示代码一份不必改。"""
    from . import rtl_scan
    nets, per = collect_nets_by_purpose(wb, purposes, probe_prefixes=probe_prefixes,
                                        resolver=resolver)
    if pages:
        page_nets, page_per = collect_nets(wb, pages)
        for k, v in page_nets.items():
            nets.setdefault(k, v)
        per.update(page_per)
    write_text(path, rtl_scan.render_nets_text(nets))
    counts = {"n_nets": len(nets)}
    for k in NETS_PURPOSES:                      # 三类固定次序在前，按页类别排在后面
        if k in per:
            counts[k] = per[k]
    for p in sorted(k for k in per if k not in NETS_PURPOSES):
        counts[p] = per[p]
    return nets, ExportOutcome(kind="nets", path=path, counts=counts,
                               note="按用途去重并集" if not pages else "按用途 + 按页去重并集")


# ═════════════════════════ 九、claims.json（红区 scan_rtl 校验器的输入契约） ═════════════════════════
def claims_naming_model(provider):
    """探针命名模型：Topout 视图 = 顶层真名（topout）；页本地子视图 = 按命名约定猜（logic-rooted）。
    与 CLI 的 cmd_topout / cmd_page 同口径（不一致 = 红区 binder 拿错契约）。"""
    if getattr(provider, "view_id", "") == "topout":
        return generator.NAMING_MODEL_TOPOUT
    return generator.NAMING_MODEL_LOGIC


def export_claims(source, path, excel, naming_model=None, only=None, mode="min", max_tests=256,
                  exhaustive=False, edited=None, sig_cov=None, form_cov=None):
    """导出 claims.json —— 与 CLI `--export-claims` **逐字节同构**（同一个写出器）。

    source 可以是：
      · build 结果 dict（已有 claims 的，直接写）
      · provider（SignalView 的 _TopoutProvider/_PageProvider）——先渲染再写
    naming_model 缺省按 source 推断（见 claims_naming_model）。

    本任务只提供函数、不加按钮（v2 的导出中心接）。写出器复用 cli._export_claims：
    契约字段（schema_version / naming_model / note / schema）只有一份定义，不会两处漂移。"""
    from . import cli
    build = source
    if hasattr(source, "render_sv"):
        if naming_model is None:
            naming_model = claims_naming_model(source)
        _text, build = render_sv(source, only=only, mode=mode, max_tests=max_tests,
                                 exhaustive=exhaustive, edited=edited,
                                 sig_cov=sig_cov, form_cov=form_cov)
    if naming_model is None:
        naming_model = generator.NAMING_MODEL_LOGIC
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):     # CLI 写出器会 print 一行，GUI 里吞掉
            cli._export_claims(build, path, excel, naming_model=naming_model)
    except OSError as ex:
        raise ExportError("无法写入 %s：\n%s" % (path, ex)) from ex
    claims = build.get("claims") or []
    counts = {"n_claims": len(claims),
              "n_probe": sum(1 for c in claims if c.get("kind") == "probe"),
              "n_force": sum(1 for c in claims if c.get("kind") == "force"),
              "n_rfwrite": sum(1 for c in claims if c.get("kind") == "rfwrite")}
    return ExportOutcome(kind="claims", path=path, counts=counts,
                         skipped=build_skipped(build),
                         note="命名模型=%s" % naming_model)


# ═════════════════════════ 十、单信号真值表 CSV ═════════════════════════
# §7-6 驱动串单口径：本模块原先自己有一份逐字节相同的 drive_strings 实现，与
# inputs_table.drive_pair 并存 —— 两份实现同一件事就是等着漂（改一处忘另一处 = 同一条向量
# 在 CSV 与输入表里印出两种驱动文本）。现在**指向同一个函数对象**，名字保留给老调用点/测试。
drive_strings = IT.drive_pair


def drive_context(an):
    """统一分析 dict → (bindings, used_vars)：logic/register 走 node，mux 走 expansion。"""
    an = an or {}
    if an.get("kind") == "mux" or an.get("editable") == "mux":
        exp = an.get("expansion") or {}
        return (exp.get("bindings") or {}), (exp.get("used_vars") or [])
    node = an.get("node")
    return (an.get("bindings") or {}), (E.collect_vars(node) if node is not None else [])


def make_drive_fn(bindings, used_vars):
    """列 → (force, RF_WRITE) 的取值函数（列里带 'vec' 才算得出来）。"""
    def _fn(col):
        return drive_strings(col.get("vec"), bindings, used_vars)
    return _fn


def _exp_source(col):
    if col.get("neg"):
        return "负向(故意填错)"
    return "designer手填" if col.get("exp") is not None else "auto_out兜底"


def signal_csv_text(columns, input_rows, out_width=1, drive_fn=None):
    """单信号真值表 CSV（转置：第一列=信号/字段名，其后每列一条测试）。

    行序（logic / register / mux 同排版，与编辑器/for_test 一致）：
      输入行… → auto_out → 期望(进.sv) → 期望(bin) → 期望来源 → 负向? → force → RF_WRITE
    最后三行是『排查(旧)』一直有、SignalView 一直缺的能力，现在两边都出（契约要求的能力补齐）。

    columns   : [{"name","neg","vals","exp","auto","auto_w","vec"}]（SignalView 的 cur_cols 直接喂）
    input_rows: [{"key","label","width"}]（SignalView 的 e_inputs 直接喂）
    drive_fn  : col -> (force 文本, RF_WRITE 文本)；None → 两行留空（形状恒定，便于 diff/脚本）
    """
    columns = list(columns or [])
    input_rows = list(input_rows or [])
    out_w = int(out_width or 1)
    wsuf = "[%d:0]" % (out_w - 1) if out_w > 1 else ""
    fc = generator._fmt_cell

    def _w(col):
        return int(col.get("auto_w") or out_w or 1)

    def _exp_val(col):
        return col["exp"] if col.get("exp") is not None else col.get("auto", 0)

    buf = io.StringIO(newline="")
    wr = csv.writer(buf)
    wr.writerow(["信号\\测试"] + [c.get("name", "") for c in columns])
    for e in input_rows:
        width = int(e.get("width") or 1)
        wr.writerow([e.get("label", e.get("key", ""))]
                    + [fc((c.get("vals") or {}).get(e.get("key"), 0) & E.mask(width), width)
                       for c in columns])
    wr.writerow(["auto_out%s" % wsuf]
                + [fc(c.get("auto", 0) & E.mask(_w(c)), _w(c)) for c in columns])
    wr.writerow(["期望(进.sv)%s" % wsuf]
                + [fc(_exp_val(c) & E.mask(_w(c)), _w(c)) for c in columns])
    wr.writerow(["期望(bin)"] + [W.fmt_bin(_exp_val(c), _w(c)) for c in columns])
    wr.writerow(["期望来源"] + [_exp_source(c) for c in columns])
    wr.writerow(["负向?"] + ["是" if c.get("neg") else "" for c in columns])
    drives = [(drive_fn(c) if drive_fn else ("", "")) for c in columns]
    wr.writerow(["force"] + [fs for fs, _ws in drives])
    wr.writerow(["RF_WRITE"] + [ws for _fs, ws in drives])
    return buf.getvalue()


def write_signal_csv(path, columns, input_rows, out_width=1, drive_fn=None, name="", text=None):
    """写单信号真值表 CSV（utf-8-sig，Excel 双击不乱码）。返回 outcome。

    text：已经渲染好的一份就传进来复用（与 `export_sv(text=…, build=…)` 同一个理由——
    调用方要先拿文本做别的事时，别让驱动串算两遍/两份文本有出入的可能）。"""
    if text is None:
        text = signal_csv_text(columns, input_rows, out_width=out_width, drive_fn=drive_fn)
    write_text(path, text, encoding="utf-8-sig", newline="")
    counts = {"n_columns": len(list(columns or [])), "n_inputs": len(list(input_rows or []))}
    return ExportOutcome(kind="signal_csv", path=path, counts=counts,
                         note=("信号 %s" % name) if name else "")


def _strip_width_suffix(s):
    """`d_bt_lp_lna_itrim[3:0]` → `d_bt_lp_lna_itrim`（块的 out_name 带位宽、Topout 名不带）。"""
    return re.sub(r"\[[^\]]*\]\s*$", "", str(s or "")).strip()


def signal_build_vectors(build, name):
    """一次 build 里【该信号那一块实际渲染进 .sv 的】向量（`sv_writer.render_signal_block`
    记在 block stats 的 `vectors` 里）。

    给「CSV / 报告要与 .sv 一条不差」的调用方用（C-139）：整信号负向、负向去重、T 编号
    重排、iddq 自检拍都只发生在 build 里，拿分析结果或编辑器的列模型二次推都会漂。

    名字三种写法都认：Topout 名（`topout_name`）、源对象名（`out_name`，可能带位宽）、
    RTL 网名（`rtl_name`）。该信号这次没产出块（被跳过 / 只记账 / 不在 only 里）→ **None**
    （不是空列表——调用方要分得清「产物里零用例」和「产物里根本没有这个块」）。
    """
    low = str(name or "").strip().lower()
    if not low or not build:
        return None
    low_base = _strip_width_suffix(low)
    for _lines, st in (build.get("blocks") or []):
        for key in (st.get("topout_name"), st.get("out_name"), st.get("rtl_name")):
            k = str(key or "").strip().lower()
            if not k:
                continue
            if k == low or _strip_width_suffix(k) in (low, low_base):
                return list(st.get("vectors") or [])
    return None


# ── 两个 legacy 数据模型 → 统一列模型的适配器（『排查(旧)』的真值表用 rowdict / TestVector）──
def columns_from_rowdicts(rows, groups, out_width, label_col, label_row,
                          gate_label=None, gate_value=None, gate_row=None):
    """『排查(旧)』logic 编辑器的 rowdict 列表 → (columns, input_rows)。

    label_col(rd, i) / label_row(g)：列头与行头文本由 GUI 给（两边表头措辞历史不同，先不统一）。
    gate_*：dft 门控 logic 的只读门输入行（每条测试驱透传值），插在 gate_row 位置。"""
    rows = list(rows or [])
    groups = list(groups or [])
    gate_key = "__dft_gate__"
    columns = []
    for i, rd in enumerate(rows):
        width = int(rd.get("correct_width") or out_width or 1)
        vals = dict(rd.get("base_values") or {})
        if gate_label is not None:
            vals[gate_key] = int(gate_value or 0)
        neg = bool(rd.get("is_negative"))
        has_exp = neg or rd.get("designer_expected") is not None
        columns.append({"name": label_col(rd, i), "neg": neg, "vals": vals,
                        "exp": rd.get("expected") if has_exp else None,
                        "auto": rd.get("correct", 0), "auto_w": width,
                        "vec": rd.get("_vec"), "row": rd})
    input_rows = [{"key": g["key"], "label": label_row(g), "width": g.get("width") or 1}
                  for g in groups]
    if gate_label is not None:
        at = len(input_rows) if gate_row is None else max(0, min(int(gate_row), len(input_rows)))
        input_rows.insert(at, {"key": gate_key, "label": gate_label, "width": 1})
    return columns, input_rows


def columns_from_mux_vectors(vecs, bindings, used_vars, data_keys=(), out_width=1, disp=None,
                             gate_label=None, gate_value=None):
    """mux 组的 TestVector 列表 → (columns, input_rows)（行=控制/数据输入，标注角色）。

    disp：编辑器的行序 [("key", k) | ("gate", None)]；None → 按 used_vars 原序 + 末尾门行。"""
    vecs = list(vecs or [])
    out_w = int(out_width or 1)
    gate_key = "__dft_gate__"
    data_key_set = set(data_keys or ())
    columns = []
    for v in vecs:
        vals = {k: v.assignments.get(k, 0) for k in (used_vars or [])}
        if gate_label is not None:
            vals[gate_key] = int(gate_value or 0)
        neg = bool(v.is_negative)
        has_exp = neg or getattr(v, "designer_expected", None) is not None
        columns.append({"name": W.test_label(v), "neg": neg, "vals": vals,
                        "exp": v.asserted_value if has_exp else None,
                        "auto": v.exp_value, "auto_w": out_w, "vec": v})
    if disp is None:
        disp = [("key", k) for k in (used_vars or [])]
        if gate_label is not None:
            disp = disp + [("gate", None)]
    input_rows = []
    for ent in disp:
        if ent[0] == "gate":
            if gate_label is not None:
                input_rows.append({"key": gate_key, "label": gate_label, "width": 1})
            continue
        key = ent[1]
        b = (bindings or {}).get(key)
        width = b.width if b is not None else 1
        role = "数据" if key in data_key_set else "控制"
        label = "%s (%s)" % ((b.base if b is not None else key), role)
        input_rows.append({"key": key, "label": label, "width": width})
    return columns, input_rows
