# -*- coding: utf-8 -*-
"""export_center.py —— ⑪ 导出中心 + ⑫ 导出完成弹层（GUI v2 Phase C4-a）。

Design 对照：docs/GUI_v2_Design对齐_20260912.md §1.2 ⑪⑫（1080px 卡片 / 5 列 × 6 行 / 完成弹层
先琥珀块后「已写出」）+ §4 裁决⑯（导出**前**就点名会被跳过的信号，不把点名推迟到完成弹层）。
架构简报：docs/GUI_v2_架构_20260912.md §2.6（每种 kind 的唯一写法表）、§6.16；契约 = 附录 A「EXPORT」49 条：

    C-157 导出 .sv                          C-158 范围三选（全部 / 仅正向 / 仅反例）
    C-159 选项：加注释                      C-160 选项：末尾汇总 + 计数器
    C-161 选项：断言消息追加 owner          C-162 四个选项记住上次选择
    C-163 两个旧「导出 .sv 选项」框合一     C-164 重复 assert 标号写文件前弹确认
    C-165 完成摘要：信号 / 块 / 用例 …      C-166 点名「只记录、不产生断言」的信号
    C-167 点名跳过了谁、每个缺哪根输入      C-168 关掉完成弹层后切 .sv 预览
    C-169 按范围给默认文件名                C-170 汇总命名块按范围加 _pos/_neg
    C-171 写文件失败说清是否被占用          C-172 预览完状态栏报数（sv_preview 那边）
    C-173 报告 HTML   C-174 报告 CSV        C-175 报告 Excel   C-176 按所选格式纠正扩展名
    C-177 报告完成报范围 / 用例 / 反例      C-178 报告只导勾选的信号
    C-179 回填 for_test                     C-180 拒绝把源 Excel 当输出名
    C-181 回填自动补 .xlsx                  C-182 回填完成报组数 + mux 未进 for_test 点名
    C-183 回填只导勾选的信号                C-184 回填含 mux 表
    C-185 导出 nets.txt                     C-186 nets 按类别勾选
    C-187 类别只列本表真有内容的页          C-188 类别记住上次勾选（settings 键 nets_pages）
    C-189 nets 完成报总网数 + 分项计数      C-190 导出完整配置 .json
    C-191 导出配置完成后逐段报数            C-192 导入配置（完整 / 旧版编辑文件）
    C-193 导入时按文件名核对源表            C-194 导入时点名「文件里有、当前表没有」的信号
    C-195 不是本工具配置时说缺哪个段        C-196 导出 claims.json
    C-197 一个导出中心列全 6 种交付物       C-198 每种交付物记住上次导出到哪
    C-199 完成反馈统一「名字 + 原因在前」   C-231 四个 export_* 键持久化
    C-232 nets_pages 持久化                 C-247 配置 .json 带 dreg_verify_config: 2
    C-250 完整配置字段集不变                C-270 跳过/过滤必须点名 + 原因，计数在后
    C-285 电路图内联进 HTML 报告

不变量：
  · I-03 导出选项只经 `exports.load/store_export_options`（本模块**不写** export_* 字面量键）；
  · I-09 配置 .json 字段集冻结（走 `session.collect_config`，不自己拼 dict）；
  · I-20 跳过/过滤先点名 + 原因、计数在后（导出前摘要 · 完成弹层 · 导入配置结果）；
  · I-12 一切后端文本经 `terms.scrub`。

分层（I-19 / §1.2）：只 import PySide6 + `exports` / `session` / `edits` + ui 同层
（contracts / names / terms / theme / widgets / dialogs / persist）。**不 import 引擎**
（topout / pageviews / generator / resolver / sigflow…）——要什么经 `state.provider()` 或 `exports`。

文件分两段：上半段是**纯函数**（Qt-free 的编排逻辑，测试不必起窗）；下半段才是两个 QDialog。
"""

import os

from PySide6 import QtCore, QtGui, QtWidgets

from dreg_verify import edits as ED
from dreg_verify import exports as X
from dreg_verify import session
from dreg_verify.ui import contracts, names, persist, terms, theme
from dreg_verify.ui.dialogs import DupLabelsDialog, ImportReportDialog
from dreg_verify.ui.widgets import Popover, mono_font, ui_font

# ═════════════════════════ 0. 名字 / 文案（唯一真相源在 names.py / terms.py）═══════════════
#: C4-int：本模块原来的 `PENDING_NAMES`（5 条）/ `PENDING_TERMS`（19 条）已整块搬进
#: `ui/names.py` / `ui/terms.py`，两个 `getattr` 回退（`_n()` / `_t()`）一并删掉 ——
#: 回退存在期间，名字搬没搬过去从代码上看不出来，两份字面量漂开也不会有人发现。
#: 动态名两个工厂也进了 `names.py`（与既有 `fmt_export_*` 五个并列），这里只留别名。
fmt_export_opt = names.fmt_export_opt
fmt_export_nets_page = names.fmt_export_nets_page


# ═════════════════════════ 1. 常量 ═════════════════════════
#: 六行的次序（= Design ER 数组 = `contracts.EXPORT_KINDS`）
ROW_ORDER = contracts.EXPORT_KINDS
#: 默认勾选 = Design ER 数组的 `[0, 1, 4]`（.sv / 报告 / 红区比对那份）——按**下标**取，
#: 六行次序改了这里跟着改，不必再对一遍字符串
DEFAULT_ENABLED = tuple(ROW_ORDER[i] for i in (0, 1, 4))
#: nets.txt「按用途」三类默认全勾（主控拍板 #9）
NETS_PURPOSES = X.NETS_PURPOSES
#: settings 键：nets 的「按页类别」勾选（C-188 / C-232；v1 同键，同事机上的旧值照读）
NETS_PAGES_KEY = "nets_pages"

#: 完整配置 `global` 段的覆盖度档喂哪个范围（P-10）——v1 那三个控件在【排查(旧)】工具条上，
#: 按信号类型分 logic / mux 两侧；Topout 与各页视图各有自己的档，导入配置动不到它们。
GLOBAL_COV_KEYS = (("logic", "coverage_logic"), ("mux", "coverage_mux"))
#: 同上：`max_tests` 在 v1 只有一个控件，喂的就是这两侧（收集时也从这里头一个取，往返才稳）
GLOBAL_COV_VIEWS = tuple(vid for vid, _key in GLOBAL_COV_KEYS)
#: 报告三格式 → 扩展名（C-176 的纠正走 `exports.correct_report_ext`，这里只挑筛选器）
REPORT_EXT = {"html": ".html", "csv": ".csv", "xlsx": ".xlsx"}
#: 「只记录、不产生断言」的账目状态（C-166）。判据 = 原因文本以 `exports.SKIP_STATUS_LABEL`
#: 里这些状态的标签开头 —— 标签只有那一份，本模块不另抄一套说法。
#: 与「真的出了问题」区分开：unresolved / error / needs-prefix / spec-collision / false-green /
#: skipped 是**失败**（要去处理），下面这些是**按设计就不产断言**（RO 回读 / 去重 / 用户清空 /
#: 本次范围里没有用例 / dft 改名指向 mux 源）。
ACCOUNTED_STATUSES = ("skip", "dup-name", "dup-source", "cleared", "scope-empty", "renamed-mux")
#: 「去处理这 N 个信号」跳到诊断抽屉的哪个症状 —— **路由键**（不是文案），
#: 值必须是 `terms.REASON_TARGETS` 的键：`app.route_reason_action` 认的就是那张表。
GO_FIX_SYMPTOM = "diag_cuvunf"


# ═════════════════════════ 2. Qt-free：小工具 ═════════════════════════
#: C4-int：`_fmt_when` 本模块与 `ui/app.py` 各一份（逐字相同）→ 收成 `terms.fmt_when` 一份。
_fmt_when = terms.fmt_when


def last_text(last):
    """「上次导出到哪」列（C-197 / C-198）：`<路径>　今天 14:22` / 「从未导出」。"""
    path = str(getattr(last, "path", "") or "")
    if not path:
        return terms.EXPORT_LAST_NEVER
    return terms.EXPORT_LAST_FMT.format(path=path, when=terms.fmt_when(getattr(last, "ts", "")))


def scope_text(row, state):
    """「范围」列的对象范围段：勾选的 N 个 / 全部 M 个；config 行 `—`（C-197）。"""
    if row.kind == "config":
        return terms.EXPORT_SCOPE_NA
    if row.object_scope == "all":
        return terms.EXPORT_SCOPE_ALL_FMT.format(n=len(state.models()))
    return terms.EXPORT_SCOPE_CHECKED_FMT.format(n=len(state.checked_names()))


def options_text(row):
    """「选项」列的一行摘要。sv / report / nets 按当前选择拼；其余用 `terms.EXPORT_ROWS` 的定文。"""
    kind = row.kind
    o = row.options or {}
    if kind == "sv":
        parts = [terms.EXPORT_SV_SCOPES.get(row.sv_scope, row.sv_scope)]
        parts += [terms.EXPORT_SV_OPTIONS[k] for k in ("comments", "sv_summary", "owner_in_msg")
                  if o.get(k)]
        return " · ".join(parts)
    if kind == "report":
        return terms.EXPORT_REPORT_FORMATS.get(o.get("format") or "html", "")
    if kind == "nets":
        got = [terms.EXPORT_NETS_PURPOSE[p] for p in NETS_PURPOSES if p in (o.get("purposes") or ())]
        got += [terms.EXPORT_NETS_PAGES.get(p, p) for p in (o.get("pages") or ())]
        return " · ".join(got) or terms.EXPORT_ROWS[kind][1]
    return terms.EXPORT_ROWS[kind][1]


def summary_text(plan):
    """底部一行摘要。**名字不在这一行**——裁决⑯ 让点名进可展开的 `EXPORT_SUMMARY_SKIPPED`。"""
    k = sum(1 for r in plan.rows if r.enabled)
    why = getattr(plan, "summary_error", "")
    if why:                                   # 摘要没算出来时说实话，不给「0 个会被跳过」
        return terms.EXPORT_SUMMARY_UNAVAILABLE.format(reason=why)
    s = len(plan.will_skip)
    if s:
        return terms.EXPORT_SUMMARY_FMT.format(k=k, n=plan.n_signals, s=s)
    return terms.EXPORT_SUMMARY_NO_SKIP_FMT.format(k=k, n=plan.n_signals)


def skipped_lines(pairs):
    """(信号名, 原因) → 展示行：**名字在前**（I-20 / C-270），原因已 `scrub`（I-12）。"""
    out = []
    for name, reason in (pairs or ()):
        body = terms.scrub(str(reason or "")).replace("\n", " ")
        out.append(terms.EXPORT_SKIPPED_ROW_FMT.format(name=name, reason=body))
    return out


def _dedup(pairs):
    """按信号名去重、**保留首次出现的次序**（多份产物各报一次同一个信号 = 用户看三遍）。"""
    seen, out = set(), []
    for name, reason in (pairs or ()):
        key = str(name)
        if key in seen:
            continue
        seen.add(key)
        out.append((key, str(reason or "")))
    return out


def is_accounted(reason):
    """这条跳过是不是「只记录、不产生断言」（C-166）而不是真的出了问题。"""
    head = str(reason or "").split("；", 1)[0].split("\n", 1)[0].strip()
    return any(head == X.SKIP_STATUS_LABEL[s] for s in ACCOUNTED_STATUSES)


def report_filter(fmt):
    """报告三格式 → `exports.REPORT_FILTERS` 里的那条筛选器字符串（C-176 的权威输入）。"""
    want = REPORT_EXT.get(str(fmt or "html"), ".html")
    return next((f for f, e in X.REPORT_FILTERS if e == want), X.REPORT_FILTERS[0][0])


def file_filter(row):
    """该行的文件对话框过滤器。"""
    kind = row.kind
    if kind == "sv":
        return terms.EXPORT_FILTER_SV
    if kind == "report":
        return X.REPORT_FILTER_STR
    if kind == "fortest":
        return terms.EXPORT_FILTER_FORTEST
    if kind == "nets":
        return terms.EXPORT_FILTER_NETS
    return terms.EXPORT_FILTER_JSON


def _cov_args(state, view_id=None):
    """该范围的覆盖度三层 → 喂 exports 的 `(mode, max_tests, exhaustive, sig_cov, form_cov)`。

    与 `state.analysis_request()` 同口径（清单跑的是哪一档，导出就写哪一档 —— 所见即所得）。"""
    vid = view_id or state.scope
    cov = state.coverage(vid)
    mode, exh = cov.mode()
    prov = state.provider(vid)
    sig_ok = bool(prov is not None and getattr(prov, "supports_sig_cov", False))
    return (mode, int(cov.max_tests), bool(exh),
            (dict(cov.sig_cov) or None) if sig_ok else None,
            (dict(cov.form_cov) or None) if sig_ok else None)


def only_for(state, row):
    """该行的 `only`（对象范围）：全部 → None（引擎的「不过滤」）；勾选 → 勾着的原样信号名。

    ⚠ 与 v1 `SignalView` 的 `self._checked_names() or None` 有一处**刻意的分歧**：一个都没勾时
    v1 退化成 None（= 导出全部），v2 给 `[]`（= 一条都不导）。「我把勾选全清了」和「我要导全部」
    是两个意思，静默当成后者会导出一份根本没要的产物。"""
    if row.object_scope == "all":
        return None
    return list(state.checked_names())


def plan_scope_row(rows):
    """摘要按哪一行的对象范围算：**第一个勾上的行**（六行次序里 .sv 在最前，默认就是它）。

    六行的对象范围可以各选各的，而底部摘要只有一个数 —— 与其取并集给一个谁也对不上的数字，
    不如明说「这个数说的是第一个勾上的交付物」。"""
    for r in (rows or ()):
        if r.enabled and r.kind != "config":
            return r
    return None


# ═════════════════════════ 3. Qt-free：六行默认值 / 默认文件名 ═════════════════════════
def default_rows(state):
    """导出中心六行的初始状态（C-197）。

    · 默认勾 .sv / 报告 / claims.json（Design `[0,1,4]`）；
    · .sv 的四个选项从 `persist.load_export_options()` 预填（C-162 / C-231 / I-03）；
    · nets 三用途默认全勾（拍板 #9）、按页类别从 settings `nets_pages` 预填（C-187/C-188/C-232）；
    · 每行的「上次导出到哪」= `state.last_export(kind)`（C-198 / N5）。

    ⚠ nets 按页类别「**从没存过**」= 全勾（C-188 的「默认全勾」，P-06）：以前这里退化成 `[]`，
      默认导出的 nets.txt 只剩 52 根网、比 v1 少 143 根**缺前缀的**衔接网 —— 少的那些恰恰是
      要拿去 scan_rtl 配前缀的，红区扫完还以为都齐了。存过空列表是另一回事（用户真的一个都
      不要，照他说的办），判据因此是「键在不在」而不是「值真不真」。
    """
    opt = persist.load_export_options()          # I-03：四个 export_* 键只有这一个入口
    st = state.settings()
    cats = nets_categories(state)
    have = set(cats)
    pages_saved = st.get(NETS_PAGES_KEY)
    if NETS_PAGES_KEY not in st or not isinstance(pages_saved, list):
        pages = list(cats)                       # 从没存过 / 存成了别的类型 → 全勾（超集宁多勿漏）
    else:
        pages = [p for p in pages_saved if p in have]
    rows = []
    for kind in ROW_ORDER:
        row = contracts.ExportRowSpec(kind=kind, enabled=(kind in DEFAULT_ENABLED))
        row.last = state.last_export(kind)
        if kind == "sv":
            row.sv_scope = opt["scope"] if opt["scope"] in contracts.EXPORT_SV_SCOPES else "all"
            row.options = {k: bool(opt[k]) for k in ("comments", "sv_summary", "owner_in_msg")}
        elif kind == "report":
            row.options = {"format": "html"}
        elif kind == "nets":
            row.options = {"purposes": list(NETS_PURPOSES), "pages": pages}
        rows.append(row)
    return rows


def nets_categories(state):
    """本表【真有内容】的按页类别（C-187）——空页不出现在选项里。没载表 → 空。"""
    if getattr(state, "wb", None) is None:
        return []
    try:
        return list(X.nets_categories(state.wb))
    except Exception:                        # noqa: BLE001  类别列不出来不该挡住整个导出中心
        return []


def default_filename(row, state):
    """该行的默认文件名（C-169 / C-176 / C-181）。目录由调用方按「上次导出到哪」补。"""
    kind = row.kind
    if kind == "sv":
        if row.sv_scope == "pos":
            return terms.EXPORT_DEFAULT_SV_POS
        if row.sv_scope == "neg":
            return terms.EXPORT_DEFAULT_SV_NEG
        # split：给基名，`exports.split_sv_paths` 再派生 _pos / _neg 两份（C-169/C-170）
        return terms.EXPORT_DEFAULT_SV
    if kind == "report":
        stem = os.path.splitext(terms.EXPORT_DEFAULT_REPORT)[0]
        return stem + REPORT_EXT.get((row.options or {}).get("format") or "html", ".html")
    if kind == "fortest":
        src = state.loaded_path or state.excel_path or ""
        stem = os.path.splitext(os.path.basename(src))[0] or "for_test"
        return terms.EXPORT_DEFAULT_FORTEST_FMT.format(stem=stem)
    if kind == "nets":
        return terms.EXPORT_DEFAULT_NETS
    if kind == "claims":
        return terms.EXPORT_DEFAULT_CLAIMS
    return session.default_config_filename(state.loaded_path or state.excel_path or "")


def start_dir(row, state):
    """文件对话框的起始目录：上次导出到哪 > 源表所在目录 > 当前工作目录（C-198）。"""
    last = getattr(row, "last", None)
    for cand in (os.path.dirname(str(getattr(last, "path", "") or "")),
                 os.path.dirname(str(state.loaded_path or state.excel_path or ""))):
        if cand and os.path.isdir(cand):
            return cand
    return os.getcwd()


# ═════════════════════════ 4. Qt-free：导出前摘要 ═════════════════════════
def build_plan(state, rows):
    """导出**前**摘要（I-09：整个函数**只渲染一次** .sv）。

    为什么只渲染一次：六行里 .sv / claims / 报告都要走同一趟展开，各渲各的等于把 200 个信号
    的展开跑三遍（载表本身就要几十秒）。这里固定用 `scope="all"` 渲一次拿 build——
    「会跳过谁」「哪些 assert 标号撞了」「覆盖几个信号」都从这一份来；真正落盘时各行再按
    自己的选项渲染（那时用户已经确认过要导出了）。
    """
    rows = list(rows or ())
    plan = contracts.ExportPlan(rows=rows)
    prov = state.provider()
    if prov is None or getattr(state, "wb", None) is None:
        return plan
    row = plan_scope_row(rows)
    only = only_for(state, row) if row is not None else None
    plan.n_signals = len(state.models()) if only is None else len(only)
    if not any(r.enabled for r in rows):
        return plan
    mode, maxt, exh, sig_cov, form_cov = _cov_args(state)
    try:
        _text, build = X.render_sv(prov, only=only, mode=mode, max_tests=maxt, exhaustive=exh,
                                   edited=state.compute_edited(), sig_cov=sig_cov,
                                   form_cov=form_cov)
    except Exception as ex:                  # noqa: BLE001  摘要算不出来不该挡住导出本身
        # ⚠ 但也**不能装作「没有信号会被跳过」**——那是在没算出来的时候给一个让人放心的假数字。
        #   记一句真话，`summary_text` 据此改文案；真正的点名在完成弹层（那时是真跑过一趟）。
        plan.summary_error = terms.exc_text(ex)            # R3-03：不贴异常原文
        return plan
    # R3-01：屏幕上那一句由 `terms.skip_reason_of` 改写（引擎原文只进 .sv 注释，不上屏）
    plan.will_skip = terms.humanize_skipped(_dedup(X.build_skipped(build)),
                                            getattr(state, "analyze", None))
    plan.dup_labels = [tuple(d) for d in (build.get("dup_labels") or ())]
    return plan


# ═════════════════════════ 5. Qt-free：执行编排 ═════════════════════════
def _sv_options(row):
    o = dict(row.options or {})
    o["scope"] = row.sv_scope if row.sv_scope in ("all", "pos", "neg") else "all"
    return o


def _run_sv(state, row, path, prov, cov):
    """§2.6 第 1 行：sv_scope ∈ all/pos/neg → 一份；split → 两份（N2 / C-169 / C-170）。"""
    mode, maxt, exh, sig_cov, form_cov = cov
    only, edited = only_for(state, row), state.compute_edited()
    kw = dict(only=only, mode=mode, max_tests=maxt, exhaustive=exh, edited=edited,
              sig_cov=sig_cov, form_cov=form_cov)
    if row.sv_scope == "split":
        pos, neg = X.export_sv_split(prov, path, options=dict(row.options or {}), **kw)
        return [pos, neg]
    opts = _sv_options(row)
    _text, out = X.export_sv(prov, path, options=opts, **kw)
    return [out]


def _run_report(state, row, path, prov, cov):
    """§2.6 第 2 行：provider.render_report → exports.export_report（C-173…C-178 / C-285）。"""
    mode, maxt, exh, sig_cov, form_cov = cov
    only = only_for(state, row)
    fmt = (row.options or {}).get("format") or "html"
    rep = prov.render_report(mode, maxt, exh, only=only, sig_cov=sig_cov, form_cov=form_cov)
    if fmt == "html":
        # C-285：同一张电路图内联进 HTML 报告（CLI 的 `--no-sigflow-svg` 默认那条路）。
        # 走 `exports` 的薄封装，视图层不碰 sigflow（I-19）。挂不上图只是没图，报告照出。
        X.attach_report_graphs(state.wb, rep, probe_prefixes=state.probe_prefixes or None,
                               mode=mode, max_tests=maxt, exhaustive=exh, only=only)
    out = X.export_report(path, rep, state.loaded_path or state.excel_path or "",
                          selected_filter=report_filter(fmt))
    detail = (rep.get("detail") or [])
    out.ui_detail = terms.EXPORT_REPORT_DONE_FMT.format(       # C-177
        scope=scope_text(row, state), n=len(detail),
        neg=sum(1 for r in detail if r.get("neg") == "是"))
    return [out]


def _run_fortest(state, row, path, prov, cov):
    """§2.6 第 3 行：补 .xlsx + 防覆盖源表都在 `exports.normalize_fortest_path` 里（C-180/C-181）。"""
    mode, maxt, exh, _sig, _form = cov
    src = state.loaded_path or state.excel_path or ""
    out = X.export_fortest(src, path, provider=prov, mode=mode, max_tests=maxt,
                           exhaustive=exh, only=only_for(state, row))       # C-183 / C-184
    out.ui_detail = terms.EXPORT_FORTEST_DONE_FMT.format(                   # C-182
        n=int((out.counts or {}).get("n_groups") or 0))
    return [out]


def _run_nets(state, row, path, _prov, _cov):
    """§2.6 第 4 行：按用途三类（N3）+「更多」按页类别并集（C-185…C-189）。"""
    o = row.options or {}
    purposes = [p for p in NETS_PURPOSES if p in (o.get("purposes") or ())]
    pages = [p for p in (o.get("pages") or ()) if p]
    _nets, out = X.export_nets_by_purpose(state.wb, path, purposes, pages=pages or None,
                                          probe_prefixes=state.probe_prefixes or None)
    return [out]


def _run_claims(state, row, path, prov, cov):
    """§2.6 第 5 行：claims.json（C-196）——与 CLI `--export-claims` 同一个写出器。"""
    mode, maxt, exh, sig_cov, form_cov = cov
    out = X.export_claims(prov, path, state.loaded_path or state.excel_path or "",
                          only=only_for(state, row), mode=mode, max_tests=maxt, exhaustive=exh,
                          edited=state.compute_edited(), sig_cov=sig_cov, form_cov=form_cov)
    return [out]


def _run_config(state, _row, path, _prov, _cov):
    """§2.6 第 6 行：完整配置 .json（C-190 / C-191 / C-247 / C-250 / I-09）。"""
    payload = collect_config(state)
    out = X.write_json(path, payload)
    out.ui_detail = config_done_text(payload)                               # C-191
    return [out]


_RUNNERS = {"sv": _run_sv, "report": _run_report, "fortest": _run_fortest,
            "nets": _run_nets, "claims": _run_claims, "config": _run_config}


def run_plan(state, plan, ask_path):
    """按 §2.6 的表逐行落盘。`ask_path(row) -> str`（""= 用户取消了这一行，跳过、不算错）。

    · 每个成功 outcome → `state.record_export(kind, outcome.path)`（N5 / C-198）；
    · `ExportError` → `errors` 里带**已 scrub** 的原因（C-171 说清是不是被仿真器/编辑器占用）；
    · `skipped` 跨产物合并去重、**名字在前**（I-20 / C-167 / C-199）；
    · `accounted` = 其中「只记录、不产生断言」的那些（C-166）。
    """
    res = contracts.ExportRunResult()
    prov = state.provider()
    cov = _cov_args(state)
    skipped = []
    for row in plan.rows:
        if not row.enabled:
            continue
        path = str(ask_path(row) or "")
        if not path:                          # 用户在文件框按了取消 → 这一行不导，不是错误
            continue
        row.path = path
        try:
            outs = _RUNNERS[row.kind](state, row, path, prov, cov)
        except X.ExportError as ex:
            res.errors.append((row.kind, terms.scrub(str(ex))))
            continue
        except Exception as ex:               # noqa: BLE001  任何一行炸了都不连累别的行
            res.errors.append((row.kind, terms.exc_text(ex, path)))     # R3-03
            continue
        for out in outs:
            res.outcomes.append(out)
            skipped.extend(out.skipped or ())
            state.record_export(row.kind, out.path)
            if not res.out_dir:
                res.out_dir = os.path.dirname(out.path) or ""
    raw = _dedup(skipped)
    # ⚠ 「只记录、不产生断言」的判据（C-166）按 **引擎原文**的状态标签算 —— 必须在改写之前，
    #    改写后的句子里没有那些标签了。
    res.accounted = [n for n, why in raw if is_accounted(why)]
    res.skipped = terms.humanize_skipped(raw, getattr(state, "analyze", None))   # R3-01
    return res


# ═════════════════════════ 6. Qt-free：配置导出 / 导入 ═════════════════════════
def collect_config(state):
    """完整配置 payload（I-09 / C-247 / C-250）——**字段集与 v1 `_collect_config` 逐键相同**。

    全部经 `session.collect_config`，本模块一个键都不自己拼。v2 与 v1 的取值映射：
      · `coverage_logic` / `coverage_mux` ← 对应范围的全局档；`max_tests` ← **logic 范围**的上限
        （v1 只有一个上限控件，配置里也只有一格；取值范围与 `_apply_global` 写回的范围必须是
        同一批 `GLOBAL_COV_VIEWS`，否则「导出→导入→再导出」这个数会自己漂 —— P-10）；
      · `cascade_* / append_to_*` 四项 v2 已无界面（随入口退役），值从 settings 按
        `session.normalize_global_settings` 的兼容口径取 —— 字段留着，老同事的文件才导得回去；
      · legacy 的 `edits / neg_only / mux_*` 段 v2 不产（那是『排查(旧)』门面的劳动成果，
        C-235 规定不读不删），但**原样透传**本机 legacy 桶里已有的那几段（见 `_legacy_segments`）；
      · v2 的劳动成果全在 `view_edits` / `view_checks` 两段里。

    ⚠ 已知限制（报给 C4-int）：`global` 段只有 logic / mux 两格覆盖度档，
      topout / dft / iddq 三个范围的全局档随配置带不走 —— 要带走就得动 `global` 的键集（C-250）。
    """
    g = session.normalize_global_settings(state.settings())
    g["coverage_logic"] = state.coverage("logic").global_label
    g["coverage_mux"] = state.coverage("mux").global_label
    g["max_tests"] = int(state.coverage(GLOBAL_COV_VIEWS[0]).max_tests)
    g["include_risky"] = bool(state.include_risky)
    view_edits, view_checks = {}, {}
    for vid in contracts.VIEW_IDS:
        ser = ED.serialize_view_edits(state.edits(vid))
        if ser:
            view_edits[vid] = ser
        if state.checked(vid) is not None:          # 全勾 = 默认态 = 不写（与 C-243 同口径）
            view_checks[vid] = list(state.checked_names(vid))
    legacy = _legacy_segments(state)
    return session.collect_config(
        state.loaded_path or state.excel_path or "", g,
        signals_checked=legacy.pop("signals_checked", None) or list(state.checked_names()),
        probe_prefixes=dict(state.probe_prefixes or {}),
        force_signals=sorted(state.force_signals or ()),
        logic_overrides={k: dict(v) for k, v in (state.logic_overrides or {}).items()},
        view_edits=view_edits or None, view_checks=view_checks or None, **legacy)


def _legacy_segments(state):
    """本机 legacy 桶里那几段 → `session.collect_config` 的同名形参（**只读透传**，P-05）。

    C-235 说的「不读不删」是**自动恢复流程**不碰它；导出一份完整配置是显式动作，而那几段是
    同事在『排查(旧)』门面里干出来的活。以前 `collect_config` 把它们一律写成空段，于是
    「v1 导出 → v2 转一手 → v1 导回」之后 v1 那边的手填期望 / mux 六段 / 单点尾缀**全清零**，
    而顶层键集一模一样，静默（P-05）。这里一个字节都不改地搬过去。

    `signals_checked` 也在里面（P-25）：它在 v1 是**排查(旧)左表**的勾选，不是 Topout 清单的
    —— 桶里有就照搬，桶里没有（这台机器从没用过旧门面）才退回 v2 自己的「当前范围勾选」。

    只搬 `persist.LEGACY_SEGMENTS` 圈定的那几段：v2 自己的段（`view_edits` / `view_checks`
    以及后来加的逐信号段）不在里面，不会被当 legacy 搬。`suffix_override` 不在桶里
    —— 它在 v1 是按 Excel 路径分桶的 settings 段，从那儿取。"""
    path = state.loaded_path or state.excel_path or ""
    if not path:
        return {}
    out = {}
    try:
        bucket = persist.load_legacy_bucket(path) or {}
    except Exception:                       # noqa: BLE001  读不出来就当没有，绝不挡住导出
        bucket = {}
    for seg in persist.LEGACY_SEGMENTS:
        val = bucket.get(seg)
        if val:
            out[seg] = val
    try:
        so = persist.path_map_of("suffix_override", path)
    except Exception:                       # noqa: BLE001
        so = None
    if so:
        out["suffix_override"] = dict(so)
    return out


def config_done_text(payload):
    """C-191：导出配置完成后逐段报数。"""
    ve = payload.get("view_edits") or {}
    n_sig = sum(len(sub or {}) for sub in ve.values())
    n_exp = 0
    for sub in ve.values():
        for ed in (sub or {}).values():
            n_exp += sum(1 for c in (ed.get("cols") or ()) if c.get("exp") is not None)
    g = payload.get("global") or {}
    return terms.EXPORT_CONFIG_DONE_FMT.format(
        k=len(payload.get("signals_checked") or ()),
        cov="logic %s / mux %s" % (g.get("coverage_logic"), g.get("coverage_mux")),
        np=len(payload.get("probe_prefixes") or {}), nf=len(payload.get("force_signals") or ()),
        ne=n_sig, nx=n_exp)


class ImportReport(object):
    """`import_config` 的结果（纯数据；对话框只负责显示）。

    ok / error          —— 不是本工具的配置时 ok=False、error 已是一句人话（C-195）
    is_full / is_legacy —— 完整配置 / 旧版【测试项编辑】文件（C-247 的版本号判据）
    notes               —— 顶部提示行（源表不一致 C-193 / 忽略了哪几项）
    missing             —— [(名字, 原因)] 文件里有、当前表找不到的信号（C-194，**名字在前**）
    counts              —— 逐段报数行（显示在点名块**之后**，I-20）
    n_restored          —— 恢复了几个信号的手填编辑
    """

    __slots__ = ("ok", "error", "is_full", "is_legacy", "notes", "missing", "counts", "n_restored")

    def __init__(self):
        self.ok = False
        self.error = ""
        self.is_full = False
        self.is_legacy = False
        self.notes = []
        self.missing = []
        self.counts = []
        self.n_restored = 0

    def text(self):
        """整段结果文本（**点名在前、计数在后**，I-20 / C-270）——状态栏与测试都读它。"""
        rows = skipped_lines(self.missing)
        head = []
        if self.missing:
            head.append(terms.EXPORT_IMPORT_MISSING_FMT.format(
                names="、".join(n for n, _r in self.missing[:30]), n=len(self.missing)))
        return "\n".join(self.notes + rows + head + self.counts).strip()


def reset_config_state(state):
    """导入【完整配置】前清空可编辑状态（C-192「先清空再照单恢复」，= 加载这份工作状态而非叠加）。

    ⚠ C4-int：`state.reset_config_state()` 已 additive 落进 `ui/state.py`（C4-a 期间它还不存在，
    本函数当时按 Proto 的公开 API 逐项清并用 `getattr` 优先调 state 的实现）。那条回退已删 ——
    「清哪些东西」是状态层的事，两份实现迟早会漂开（而漂开的表现是「导入后还留着上一份配置」，
    界面上看不出来）。本函数现在只是个薄壳，留着是因为它是本模块对外的编排口。"""
    return state.reset_config_state()


def _apply_global(state, g):
    """完整配置的 `global` 段 → v2 只认覆盖度档 / 用例上限 / 缺前缀强制生成三样。

    另外四项（级联模式 ×2、输出引用尾缀 ×2）随入口退役 —— **忽略但说一句**（不静默）。
    返回要不要在结果里加那句提示。

    ⚠ 作用范围 = `GLOBAL_COV_VIEWS`（P-10）：这三个键在 v1 里是**排查(旧)** 工具条那三个
    控件（`coverage` / `coverage_mux` / `max_tests`，按信号类型分 logic 与 mux 两侧），
    `_apply_global_settings` 也只往这三个控件上套；Topout / dft / iddq 的 `SignalView`
    各有自己的档，导入配置从来动不到它们。以前这里把上限**无差别套到五个范围**，
    同一份配置导进来 .sv 就从 84483 变 110486 字节 —— 而用户只是「打开了同事给的配置」。
    """
    n = session.normalize_global_settings(g or {})
    for vid, key in GLOBAL_COV_KEYS:
        if n[key] is not None:
            state.coverage(vid).persist_global_label(n[key])
            state.coverage_touched(vid)
    if n["max_tests"] is not None:
        for vid in GLOBAL_COV_VIEWS:                   # v1 只有一个上限控件，喂的就是这两侧
            state.coverage(vid).persist_max_tests(int(n["max_tests"]))
            state.coverage_touched(vid)
    if n["include_risky"] is not None:
        state.set_include_risky(bool(n["include_risky"]))
    ignored = ("cascade_logic", "cascade_mux", "append_to_logic", "append_to_mux")
    return any(k in (g or {}) for k in ignored)


def _apply_view_edits(state, payload):
    """`view_edits` / `view_checks` 两段 → 各范围的编辑与勾选。返回 (恢复几个信号, 找不到的名字)。

    走 `edits.restore_view_edits`：逐信号重新分析、**重算 auto**（C-238），当前表没有的信号
    点名跳过（C-194 / C-239），坏数值逐条跳过（C-240）。"""
    ve = payload.get("view_edits") or {}
    vc = payload.get("view_checks") or {}
    n_restored, missing = 0, []
    with state.suspend_persist():
        for vid in contracts.VIEW_IDS:
            models = state.models(vid)
            sub = ve.get(vid)
            if sub and models:
                have = {str(m["name"]).lower() for m in models}
                missing += [(str(k), _MISSING_REASON) for k in sub
                            if str(k).lower() not in have]
                got = ED.restore_view_edits(sub, models,
                                            lambda real, _v=vid: state.analyze(real, _v))
                for low, rec in got.items():
                    state.put_edit(rec["name"], rec, vid)
                n_restored += len(got)
            names_on = vc.get(vid)
            if names_on is not None and models:        # 勾选权威：列出的勾上、其余清空
                state.set_checked([m["name"] for m in models], False, vid)
                state.set_checked([str(x) for x in names_on], True, vid)
    return n_restored, missing


#: C-194 的原因：文件里有、当前表里没有这个名字（工程师语言，不提文件格式/路径）
_MISSING_REASON = "当前表里没有这个信号，跳过它的手填编辑"


def _apply_legacy_file(state, payload):
    """旧版【测试项编辑】文件 → 走 **C-302 那条迁移路**。→ `(迁了几个, [(名字, 迁了几列)], 跳过的)`。

    以前这一支只调 `_apply_view_edits`，而它只读 `view_edits` / `view_checks` 两段 —— 旧版文件
    压根没有这两段，于是 v2 完全空转（v1 能恢复 2 个信号并点名跳过 2 个，v2 报「恢复了 0 个」，
    P-04，BLOCKER）。旧版那九段的键空间与 v2 列模型对不上，硬塞是不行的；`diagnostics` 里
    早就有一条把它们迁成 v2 列模型的正路（C-302，logic / 直连寄存器根迁、mux 根点名不迁），
    只是它读的是**本机 edits.json 的桶**。这里把同一条路的桶换成**文件内容**，一份实现两个入口。

    只取 `persist.LEGACY_SEGMENTS` 圈的那几段：文件里若还带着 v2 自己的段（同事导的是完整
    配置、却被当旧版文件读），那几段归 `_apply_view_edits` 管，别在这里重复搬一遍。"""
    from dreg_verify.ui import diagnostics as DG          # noqa: PLC0415  只有这一支要用
    bucket = {k: payload[k] for k in persist.LEGACY_SEGMENTS if k in (payload or {})}
    plan = DG.plan_legacy_import(state, bucket=bucket)
    before = set(state.edits())
    n_sig, _n_col = DG.apply_legacy_import(state, plan, bucket=bucket)
    done = set(state.edits()) - before
    migrated = [(nm, n) for nm, n, _note in plan.rows if str(nm).lower() in done]
    skipped = list(plan.skipped)
    skipped += [(nm, _MISSING_REASON) for nm, _n, _note in plan.rows
                if str(nm).lower() not in done]
    return n_sig, migrated, skipped


def import_config(state, path):
    """导入配置（C-192…C-195 / C-247）。→ `ImportReport`。

    · `is_full` → 先 `reset_config_state` 再照单恢复：global（只认三样）→ 三套诊断配置 →
      `view_edits` + `view_checks`；
    · `is_legacy`（旧版【测试项编辑】文件）→ **只并入**编辑，别的一律不动；旧版那九段走
      `_apply_legacy_file`（= C-302 的同一条迁移路，P-04），点名迁了谁、跳过谁 + 原因；
    · 都不是 → 明说缺哪个段（C-195），不静默失败。
    """
    rep = ImportReport()
    try:
        payload = session.read_config_file(path)
    except (OSError, ValueError) as ex:
        # R3-03 / R2-13 / P-24：坏 JSON / 文件不在 → 以前整条是 Python 的英文异常原文
        # （含本机全路径）。一律走 `terms.exc_text`。
        rep.error = terms.exc_text(ex, path)
        rep.notes.append(rep.error)
        return rep
    plan = session.apply_config(payload, current_excel=state.loaded_path or state.excel_path or "")
    rep.is_full, rep.is_legacy, rep.ok = plan["is_full"], plan["is_legacy"], plan["ok"]
    if not plan["ok"]:
        rep.error = terms.EXPORT_IMPORT_BAD_FILE                       # C-195
        rep.notes.append(rep.error)
        return rep
    if plan["excel_mismatch"]:                                         # C-193
        rep.notes.append(terms.EXPORT_IMPORT_MISMATCH_FMT.format(
            cfg=plan["cfg_excel"], cur=plan["cur_excel"]))
    if rep.is_full:
        reset_config_state(state)
        if _apply_global(state, payload.get("global")):
            rep.notes.append(terms.EXPORT_IMPORT_IGNORED)
        if plan["probe_prefixes"] is not None:
            state.set_probe_prefixes(plan["probe_prefixes"])
        if plan["force_signals"] is not None:
            state.set_force_signals(plan["force_signals"])
        if plan["logic_overrides"] is not None:
            state.set_logic_overrides(plan["logic_overrides"])
    rep.n_restored, rep.missing = _apply_view_edits(state, payload)
    if rep.is_legacy and not rep.is_full:                  # C-302 的同一条迁移路（P-04）
        n_sig, migrated, skipped = _apply_legacy_file(state, payload)
        rep.n_restored += n_sig
        rep.missing += skipped
        rep.counts += [terms.DIAG_LEGACY_ROW_FMT.format(name=nm, n=n) for nm, n in migrated]
    kind = terms.EXPORT_IMPORT_KIND_FULL if rep.is_full else terms.EXPORT_IMPORT_KIND_LEGACY
    rep.counts.append(terms.EXPORT_IMPORT_DONE_FMT.format(kind=kind, n=rep.n_restored))
    if rep.is_full:
        rep.counts.append(terms.EXPORT_IMPORT_APPLIED_FMT.format(
            k=len(payload.get("signals_checked") or ()),
            np=len(state.probe_prefixes or {}), nf=len(state.force_signals or ()),
            no=len(state.logic_overrides or {})))
    return rep


# ═════════════════════════ 7. Qt：小件 ═════════════════════════
def _label(parent, text, object_name="", mono=False, size=None, color=None, bold=False):
    lab = QtWidgets.QLabel(str(text), parent)
    if object_name:
        lab.setObjectName(object_name)
    lab.setFont(mono_font(size or theme.FS_MONO) if mono else ui_font(size or theme.FS_UI, bold))
    lab.setWordWrap(False)
    if color:
        lab.setStyleSheet("color:%s;" % color)
    return lab


def _button(parent, object_name, text, primary=False):
    b = QtWidgets.QPushButton(str(text), parent)
    b.setObjectName(object_name)
    b.setFont(ui_font())
    b.setCursor(QtCore.Qt.PointingHandCursor)
    b.setFixedHeight(theme.BTN_H if hasattr(theme, "BTN_H") else 24)
    if primary:
        b.setStyleSheet("QPushButton{background:%s;color:%s;border:1px solid %s;border-radius:3px;"
                        "padding:2px 14px;}QPushButton:disabled{background:%s;color:%s;border-color:%s;}"
                        % (theme.BLUE, theme.WHITE, theme.BLUE,
                           theme.DISABLED_BG, theme.DISABLED_TEXT, theme.BORDER))
    else:
        b.setStyleSheet("QPushButton{background:%s;color:%s;border:1px solid %s;border-radius:3px;"
                        "padding:2px 14px;}" % (theme.WHITE, theme.TEXT, theme.BORDER))
    return b


def _cell(parent, widget, margins=(8, 3, 8, 3)):
    """把一个控件包进表格单元（QTableWidget 的 cellWidget 要自己管边距）。"""
    box = QtWidgets.QWidget(parent)
    lay = QtWidgets.QHBoxLayout(box)
    lay.setContentsMargins(*margins)
    lay.setSpacing(6)
    lay.addWidget(widget)
    lay.addStretch(1)
    return box


# ═════════════════════════ 8. ⑪ 导出中心 ═════════════════════════
class ExportCenterDialog(QtWidgets.QDialog):
    """⑪ 导出中心（C-197）：6 种交付物 × 勾选 / 范围 / 选项 / 上次导出到哪，一张表定版。

    构造：`ExportCenterDialog(state, parent=None, preselect="")`
      preselect ∈ `contracts.EXPORT_KINDS` → 只勾那一行（`"report"` = Ctrl+R，C-256；
      `"nets"` = 诊断抽屉第 1 步）；`"config"` = 打开后直接进「导入配置…」（C-192）。

    对外信号：
      exported(object)        —— 本次的 `contracts.ExportRunResult`
      goFixRequested(str)     —— 完成弹层「去处理这 N 个信号」→ 诊断抽屉（C4-int 接）
      svPreviewRequested()    —— 完成弹层关掉后切 .sv 预览标签（C-168，C4-int 接）
      statusMessage(str)      —— 状态栏一句（导入配置结果等）

    公开 API：`rows()` / `plan()` / `refresh_summary()` / `run()` / `on_import_config()`。
    """

    exported = QtCore.Signal(object)
    goFixRequested = QtCore.Signal(str)
    svPreviewRequested = QtCore.Signal()
    statusMessage = QtCore.Signal(str)

    #: 5 列（Design：40 / 206 / 190 / 1fr / 240）
    COL_W = (40, 206, 190, 0, 240)

    def __init__(self, state, parent=None, preselect=""):
        super(ExportCenterDialog, self).__init__(parent)
        self.setObjectName(names.EXPORT_DIALOG)
        self.setWindowTitle(terms.EXPORT_TITLE)
        self.setFont(ui_font())
        self.setMinimumWidth(theme.EXPORT_DIALOG_W if hasattr(theme, "EXPORT_DIALOG_W") else 1080)
        self.resize(1080, 520)
        self.state = state
        self._rows = default_rows(state)
        self._plan = None
        self._plan_cache = {}           # `_plan_key` → ExportPlan（同一份输入只渲染一次）
        self.done_dialog = None         # ⑫ 最近一次的完成弹层（`run()` 里赋值）
        self._skipped_open = False
        self._auto_import = (str(preselect or "") == "config")
        self._shown = False
        self._apply_preselect(preselect)
        self._build()
        self.refresh_summary()

    # ── 预选（C-256 / 诊断第 1 步）──
    def _apply_preselect(self, preselect):
        pre = str(preselect or "")
        if pre not in contracts.EXPORT_KINDS:
            return
        for r in self._rows:
            r.enabled = (r.kind == pre)

    # ── 构建 ──
    def _build(self):
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(18, 16, 18, 14)
        lay.setSpacing(10)
        lay.addWidget(_label(self, terms.EXPORT_TITLE, size=theme.FS_UI_TITLE + 1, bold=True))
        self._build_table()
        lay.addWidget(self.table, 1)
        self._build_summary(lay)
        self._build_buttons(lay)
        self.options_pop = Popover(self, width=340, object_name=names.EXPORT_OPTIONS_POPOVER)

    def _build_table(self):
        t = QtWidgets.QTableWidget(len(self._rows), len(terms.EXPORT_HEADERS), self)
        t.setObjectName(names.EXPORT_TABLE)
        t.setHorizontalHeaderLabels(list(terms.EXPORT_HEADERS))
        t.verticalHeader().setVisible(False)
        t.setSelectionMode(QtWidgets.QAbstractItemView.NoSelection)
        t.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        t.setFocusPolicy(QtCore.Qt.NoFocus)
        t.setShowGrid(False)
        t.setAlternatingRowColors(True)
        t.setFont(ui_font())
        hh = t.horizontalHeader()
        for c, w in enumerate(self.COL_W):
            if w:
                t.setColumnWidth(c, w)
                hh.setSectionResizeMode(c, QtWidgets.QHeaderView.Fixed)
            else:
                hh.setSectionResizeMode(c, QtWidgets.QHeaderView.Stretch)
        t.verticalHeader().setDefaultSectionSize(38)
        self.table = t
        self.checks, self.obj_combos, self.sv_combos, self.opt_btns, self.last_labels = {}, {}, {}, {}, {}
        for i, row in enumerate(self._rows):
            self._build_row(i, row)

    def _build_row(self, i, row):
        t, kind = self.table, row.kind
        # ① 勾选
        cb = QtWidgets.QCheckBox(t)
        cb.setObjectName(names.fmt_export_check(kind))
        cb.setChecked(bool(row.enabled))
        cb.toggled.connect(lambda on, k=kind: self._on_check(k, on))
        self.checks[kind] = cb
        t.setCellWidget(i, 0, _cell(t, cb))
        # ② 交付物名 + 备注
        title, _summary, note = terms.EXPORT_ROWS[kind]
        box = QtWidgets.QWidget(t)
        v = QtWidgets.QVBoxLayout(box)
        v.setContentsMargins(4, 2, 4, 2)
        v.setSpacing(0)
        v.addWidget(_label(box, title, bold=True))
        if note:
            v.addWidget(_label(box, note, size=theme.FS_UI_SMALL, color=theme.MUTE))
        t.setCellWidget(i, 1, box)
        # ③ 范围（两段：对象范围 + sv 的 scope）
        t.setCellWidget(i, 2, self._build_scope(t, row))
        # ④ 选项
        t.setCellWidget(i, 3, self._build_options(t, row))
        # ⑤ 上次导出到哪
        lab = _label(t, last_text(row.last), names.fmt_export_last(kind), mono=bool(row.last),
                     size=theme.FS_MONO, color=None if row.last else theme.MUTE)
        lab.setToolTip(last_text(row.last))
        self.last_labels[kind] = lab
        t.setCellWidget(i, 4, _cell(t, lab))

    def _build_scope(self, parent, row):
        kind = row.kind
        box = QtWidgets.QWidget(parent)
        box.setObjectName(names.fmt_export_scope(kind))
        v = QtWidgets.QVBoxLayout(box)
        v.setContentsMargins(4, 2, 4, 2)
        v.setSpacing(1)
        if kind == "config":
            v.addWidget(_label(box, terms.EXPORT_SCOPE_NA,
                               names.fmt_export_scope_objects(kind), color=theme.MUTE))
            return box
        combo = QtWidgets.QComboBox(box)
        combo.setObjectName(names.fmt_export_scope_objects(kind))
        combo.setFont(ui_font())
        for key in contracts.EXPORT_OBJECT_SCOPES:
            combo.addItem("", key)
        combo.setCurrentIndex(list(contracts.EXPORT_OBJECT_SCOPES).index(row.object_scope))
        combo.currentIndexChanged.connect(lambda _i, k=kind: self._on_object_scope(k))
        self.obj_combos[kind] = combo
        v.addWidget(combo)
        if kind == "sv":
            sv = QtWidgets.QComboBox(box)
            sv.setObjectName(names.fmt_export_scope_sv(kind))
            sv.setFont(ui_font())
            for key in contracts.EXPORT_SV_SCOPES:
                sv.addItem(terms.EXPORT_SV_SCOPES[key], key)
            sv.setCurrentIndex(list(contracts.EXPORT_SV_SCOPES).index(row.sv_scope))
            sv.currentIndexChanged.connect(lambda _i: self._on_sv_scope())
            self.sv_combos[kind] = sv
            v.addWidget(sv)
        return box

    def _build_options(self, parent, row):
        kind = row.kind
        if kind in ("sv", "report", "nets"):
            b = QtWidgets.QToolButton(parent)
            b.setObjectName(names.fmt_export_options(kind))
            b.setFont(ui_font())
            b.setCursor(QtCore.Qt.PointingHandCursor)
            b.setToolButtonStyle(QtCore.Qt.ToolButtonTextOnly)
            b.setText(terms.EXPORT_OPT_BTN_FMT.format(summary=options_text(row)))
            b.setStyleSheet("QToolButton{background:%s;color:%s;border:1px solid %s;"
                            "border-radius:3px;padding:2px 10px;text-align:left;}"
                            % (theme.WHITE, theme.TEXT, theme.BORDER))
            b.clicked.connect(lambda _c=False, k=kind: self.open_options(k))
            self.opt_btns[kind] = b
            return _cell(parent, b)
        lab = _label(parent, options_text(row), names.fmt_export_options(kind),
                     size=theme.FS_UI_SMALL, color=theme.MUTE)
        self.opt_btns[kind] = lab
        return _cell(parent, lab)

    def _build_summary(self, lay):
        self.summary = _label(self, "", names.EXPORT_SUMMARY)
        lay.addWidget(self.summary)
        self.skipped_btn = QtWidgets.QToolButton(self)
        self.skipped_btn.setObjectName(names.EXPORT_SUMMARY_SKIPPED)
        self.skipped_btn.setFont(ui_font(theme.FS_UI_SMALL))
        self.skipped_btn.setCursor(QtCore.Qt.PointingHandCursor)
        self.skipped_btn.setStyleSheet("QToolButton{border:none;color:%s;text-align:left;}"
                                       % theme.AMBER_FG)
        self.skipped_btn.clicked.connect(self._toggle_skipped)
        lay.addWidget(self.skipped_btn)
        self.skipped_list = QtWidgets.QPlainTextEdit(self)
        self.skipped_list.setObjectName(names.EXPORT_SUMMARY_SKIPPED_LIST)
        self.skipped_list.setReadOnly(True)
        self.skipped_list.setFont(mono_font())
        self.skipped_list.setMaximumHeight(110)
        self.skipped_list.setStyleSheet("QPlainTextEdit{background:%s;border:1px solid %s;color:%s;}"
                                        % (theme.AMBER_BG, theme.AMBER_BORDER, theme.WARN_FG))
        self.skipped_list.hide()
        lay.addWidget(self.skipped_list)

    def _build_buttons(self, lay):
        bar = QtWidgets.QHBoxLayout()
        bar.setSpacing(8)
        self.import_btn = _button(self, names.EXPORT_BTN_IMPORT_CONFIG, terms.EXPORT_BTN_IMPORT_CONFIG)
        self.import_btn.clicked.connect(self.on_import_config)
        bar.addWidget(self.import_btn)
        bar.addStretch(1)
        self.cancel_btn = _button(self, names.EXPORT_BTN_CANCEL, terms.EXPORT_BTN_CANCEL)
        self.cancel_btn.clicked.connect(self.reject)
        bar.addWidget(self.cancel_btn)
        self.run_btn = _button(self, names.EXPORT_BTN_RUN, terms.EXPORT_BTN_RUN_NONE, primary=True)
        self.run_btn.clicked.connect(self.run)
        bar.addWidget(self.run_btn)
        lay.addLayout(bar)

    # ── 状态查询 ──
    def rows(self):
        """六行的当前状态（`contracts.ExportRowSpec`；勾选 / 范围 / 选项都已同步）。"""
        return list(self._rows)

    def row_of(self, kind):
        return next(r for r in self._rows if r.kind == str(kind))

    def plan(self):
        """最近一次 `build_plan` 的结果（勾选 / 范围一变就重算）。"""
        return self._plan

    def showEvent(self, ev):
        super(ExportCenterDialog, self).showEvent(ev)
        if self._shown:
            return
        self._shown = True
        if self._auto_import:                       # preselect="config"：直接进导入配置
            QtCore.QTimer.singleShot(0, self.on_import_config)

    # ── 联动 ──
    def _on_check(self, kind, on):
        self.row_of(kind).enabled = bool(on)
        self.refresh_summary()

    def _on_object_scope(self, kind):
        combo = self.obj_combos[kind]
        self.row_of(kind).object_scope = combo.currentData() or "checked"
        self.refresh_scopes()
        self.refresh_summary()

    def _on_sv_scope(self):
        row = self.row_of("sv")
        row.sv_scope = self.sv_combos["sv"].currentData() or "all"
        self._store_sv_options(row)
        self._refresh_options_text("sv")
        self.refresh_summary()

    def _store_sv_options(self, row):
        """C-162 / C-231 / I-03：四个选项经 `persist.store_export_options` 记住，本模块不写键名。

        ⚠ 四档 `all/pos/neg/split` 现在**存得进也读得回**：C4-int 在 `exports.SCOPE_LABEL` 里
        补上了 `split`（C4-a 期间它只认三档，`load_export_options` 会把 split 静默兜底成 "all"
        —— 用户上次选的「分文件」下次开窗变回「全部」，界面上看不出是谁改的）。
        归一化仍然只在 Qt-free 层那一份，本模块一个字节都不自己判（I-03）。"""
        opt = dict(row.options or {})
        opt["scope"] = row.sv_scope
        persist.store_export_options(opt)

    def refresh_scopes(self):
        for kind, combo in self.obj_combos.items():
            row = self.row_of(kind)
            for i, key in enumerate(contracts.EXPORT_OBJECT_SCOPES):
                combo.setItemText(i, (terms.EXPORT_SCOPE_ALL_FMT.format(n=len(self.state.models()))
                                      if key == "all" else
                                      terms.EXPORT_SCOPE_CHECKED_FMT.format(
                                          n=len(self.state.checked_names()))))
            combo.setToolTip(scope_text(row, self.state))

    def _refresh_options_text(self, kind):
        w = self.opt_btns.get(kind)
        if w is None:
            return
        txt = options_text(self.row_of(kind))
        w.setText(terms.EXPORT_OPT_BTN_FMT.format(summary=txt)
                  if isinstance(w, QtWidgets.QToolButton) else txt)
        w.setToolTip(txt)

    def _plan_key(self, rows):
        """摘要的渲染只由这几样决定：覆盖度/配置指纹 + 对象范围（勾了哪几个信号）+ 有没有勾交付物。

        **不含**「勾了哪几行交付物」—— 勾不勾 .sv 与「哪些信号会被跳过」无关，
        而对话框开着的时候（模态）指纹与编辑都不会变。这就是每点一下勾选框不必重跑一遍
        200 个信号展开的理由。"""
        row = plan_scope_row(rows)
        only = only_for(self.state, row) if row is not None else None
        return (self.state.fingerprint(),
                row.object_scope if row is not None else "",
                None if only is None else tuple(sorted(only)),
                any(r.enabled for r in rows))

    def refresh_summary(self):
        """勾选 / 范围一变就重算摘要（裁决⑯：导出**前**就点名会被跳过的信号）。

        同一份输入只渲染一次（见 `_plan_key`）——否则六个勾选框点一遍 = 引擎跑六趟。"""
        self.refresh_scopes()
        key = self._plan_key(self._rows)
        plan = self._plan_cache.get(key)
        if plan is None:
            plan = build_plan(self.state, self._rows)
            self._plan_cache[key] = plan
        plan.rows = self._rows
        self._plan = plan
        self.summary.setText(summary_text(self._plan))
        rows = skipped_lines(self._plan.will_skip)
        self.skipped_btn.setVisible(bool(rows))
        self.skipped_list.setPlainText("\n".join(rows))
        self.skipped_list.setVisible(bool(rows) and self._skipped_open)
        self.skipped_btn.setText("%s　%s" % (
            terms.EXPORT_SUMMARY_SKIPPED_HEAD,
            terms.EXPORT_SUMMARY_SKIPPED_LESS if self._skipped_open
            else terms.EXPORT_SUMMARY_SKIPPED_MORE))
        k = sum(1 for r in self._rows if r.enabled)
        self.run_btn.setEnabled(bool(k))
        self.run_btn.setText(terms.EXPORT_BTN_RUN_FMT.format(k=k) if k
                             else terms.EXPORT_BTN_RUN_NONE)
        return self._plan

    def _toggle_skipped(self):
        self._skipped_open = not self._skipped_open
        self.skipped_list.setVisible(self._skipped_open and bool(self._plan.will_skip))
        self.skipped_btn.setText("%s　%s" % (
            terms.EXPORT_SUMMARY_SKIPPED_HEAD,
            terms.EXPORT_SUMMARY_SKIPPED_LESS if self._skipped_open
            else terms.EXPORT_SUMMARY_SKIPPED_MORE))

    def skipped_text(self):
        """导出前摘要里点名的那段（测试 / 状态栏读它）。"""
        return self.skipped_list.toPlainText()

    # ── 选项弹层 ──
    def open_options(self, kind):
        """打开某一行的选项弹层（sv 三勾选 / report 三格式单选 / nets 三用途 + 更多按页）。"""
        pop = self.options_pop
        lay = pop.content_layout()
        while lay.count():
            it = lay.takeAt(0)
            w = it.widget()
            if w is not None:
                w.setParent(None)
        builder = {"sv": self._fill_sv_options, "report": self._fill_report_options,
                   "nets": self._fill_nets_options}[kind]
        builder(pop.body, lay)
        btn = self.opt_btns[kind]
        pos = btn.mapTo(self, QtCore.QPoint(0, btn.height()))
        pop.open_at(pos)
        return pop

    def _fill_sv_options(self, parent, lay):
        row = self.row_of("sv")
        for key in ("comments", "sv_summary", "owner_in_msg"):       # C-159 / C-160 / C-161
            cb = QtWidgets.QCheckBox(terms.EXPORT_SV_OPTIONS[key], parent)
            cb.setObjectName(fmt_export_opt("sv", key))
            cb.setFont(ui_font())
            cb.setChecked(bool((row.options or {}).get(key)))
            cb.toggled.connect(lambda on, k=key: self._set_sv_option(k, on))
            lay.addWidget(cb)

    def _set_sv_option(self, key, on):
        row = self.row_of("sv")
        row.options = dict(row.options or {})
        row.options[key] = bool(on)
        self._store_sv_options(row)                                  # C-162
        self._refresh_options_text("sv")

    def _fill_report_options(self, parent, lay):
        row = self.row_of("report")
        self._report_group = QtWidgets.QButtonGroup(parent)
        for key in contracts.EXPORT_REPORT_FORMATS:                  # C-173 / C-174 / C-175
            rb = QtWidgets.QRadioButton(terms.EXPORT_REPORT_FORMATS[key], parent)
            rb.setObjectName(fmt_export_opt("report", key))
            rb.setFont(ui_font())
            rb.setChecked((row.options or {}).get("format", "html") == key)
            self._report_group.addButton(rb)
            rb.toggled.connect(lambda on, k=key: on and self._set_report_format(k))
            lay.addWidget(rb)

    def _set_report_format(self, key):
        row = self.row_of("report")
        row.options = dict(row.options or {})
        row.options["format"] = key
        self._refresh_options_text("report")

    def _fill_nets_options(self, parent, lay):
        row = self.row_of("nets")
        for key in NETS_PURPOSES:                                    # C-186（拍板 #9 三类）
            cb = QtWidgets.QCheckBox(terms.EXPORT_NETS_PURPOSE[key], parent)
            cb.setObjectName(fmt_export_opt("nets", key))
            cb.setFont(ui_font())
            cb.setToolTip(terms.scrub(X.NETS_PURPOSE_NOTE.get(key, "")))
            cb.setChecked(key in ((row.options or {}).get("purposes") or ()))
            cb.toggled.connect(lambda on, k=key: self._set_nets_purpose(k, on))
            lay.addWidget(cb)
        more = QtWidgets.QToolButton(parent)
        more.setObjectName(names.EXPORT_NETS_MORE_BTN)
        more.setText(terms.EXPORT_NETS_MORE)
        more.setFont(ui_font())
        more.setCheckable(True)
        more.setCursor(QtCore.Qt.PointingHandCursor)
        more.setStyleSheet("QToolButton{border:none;color:%s;text-align:left;}" % theme.BLUE)
        lay.addWidget(more)
        holder = QtWidgets.QWidget(parent)
        hv = QtWidgets.QVBoxLayout(holder)
        hv.setContentsMargins(14, 0, 0, 0)
        hv.setSpacing(4)
        cats = nets_categories(self.state)                           # C-187：只列真有内容的页
        for page in cats:
            cb = QtWidgets.QCheckBox(terms.EXPORT_NETS_PAGES.get(page, page), holder)
            cb.setObjectName(fmt_export_nets_page(page))
            cb.setFont(ui_font())
            cb.setChecked(page in ((row.options or {}).get("pages") or ()))
            cb.toggled.connect(lambda on, p=page: self._set_nets_page(p, on))
            hv.addWidget(cb)
        holder.setVisible(bool((row.options or {}).get("pages")))
        more.setChecked(holder.isVisible())
        more.toggled.connect(holder.setVisible)
        lay.addWidget(holder)
        self._nets_more_btn, self._nets_pages_box = more, holder

    def _set_nets_purpose(self, key, on):
        row = self.row_of("nets")
        got = [p for p in NETS_PURPOSES if p in ((row.options or {}).get("purposes") or ())]
        got = [p for p in got if p != key] + ([key] if on else [])
        row.options = dict(row.options or {})
        row.options["purposes"] = [p for p in NETS_PURPOSES if p in got]
        self._refresh_options_text("nets")
        self.refresh_summary()

    def _set_nets_page(self, page, on):
        row = self.row_of("nets")
        got = list((row.options or {}).get("pages") or ())
        got = [p for p in got if p != page] + ([page] if on else [])
        cats = nets_categories(self.state)
        row.options = dict(row.options or {})
        row.options["pages"] = [p for p in cats if p in got]
        self.state.save_settings({NETS_PAGES_KEY: list(row.options["pages"])})   # C-188 / C-232
        self._refresh_options_text("nets")

    # ── 执行 ──
    def _ask_path(self, row):
        """逐行弹「另存为」（C-169 默认文件名 / C-176 报告按格式选筛选器 / C-198 起始目录）。"""
        start = os.path.join(start_dir(row, self.state), default_filename(row, self.state))
        sel = report_filter((row.options or {}).get("format") or "html") if row.kind == "report" else ""
        path, _f = QtWidgets.QFileDialog.getSaveFileName(
            self, terms.EXPORT_ROWS[row.kind][0], start, file_filter(row), sel)
        return str(path or "")

    def run(self):
        """导出勾选的 N 项。→ `ExportRunResult`（用户在重复标号确认里按了取消 → None）。"""
        plan = self.refresh_summary()
        if not any(r.enabled for r in plan.rows):
            return None
        if self.row_of("sv").enabled and plan.dup_labels:            # C-164：写文件前弹确认
            if not DupLabelsDialog.ask(plan.dup_labels, X.dup_label_text(plan.dup_labels), self):
                return None                                          # 取消 → **一个字节都不写**
        result = run_plan(self.state, plan, self._ask_path)
        self.exported.emit(result)
        if result.outcomes or result.errors:
            #: ⑫ 完成弹层留一份引用（C4-int）：组合根测试要在它上面点「去处理这 N 个信号」，
            #: 而 harness 把 `exec` 换成了立刻返回 —— 局部变量一出这个 if 就被 GC，谁也点不到。
            self.done_dialog = done = ExportDoneDialog(result, self)
            done.goFixRequested.connect(self.goFixRequested)
            done.exec()
        if any(o.kind == "sv" for o in result.outcomes):             # C-168
            self.svPreviewRequested.emit()
        self.accept()
        return result

    # ── 导入配置（C-192）──
    def on_import_config(self):
        """「导入配置…」：读文件 → 套用 → `ImportReportDialog` 三段（C-193 / C-194 / C-195）。"""
        path, _f = QtWidgets.QFileDialog.getOpenFileName(
            self, terms.EXPORT_BTN_IMPORT_CONFIG, start_dir(self.row_of("config"), self.state),
            terms.EXPORT_FILTER_JSON)
        if not path:
            return None
        rep = import_config(self.state, str(path))
        ImportReportDialog.ask(missing=rep.missing, counts=rep.counts, notes=rep.notes, parent=self)
        self.statusMessage.emit(rep.text().replace("\n", " · "))
        self._rows = default_rows(self.state)                        # 勾选 / 上次导出可能都变了
        self._rebuild_rows()
        return rep

    def _rebuild_rows(self):
        # 导入配置动过编辑与勾选 —— 指纹不一定变（`view_edits` 不进指纹），摘要缓存一律作废
        self._plan_cache.clear()
        for i, row in enumerate(self._rows):
            self._build_row(i, row)
        self.refresh_summary()


# ═════════════════════════ 9. ⑫ 导出完成 ═════════════════════════
class ExportDoneDialog(QtWidgets.QDialog):
    """⑫ 导出完成（C-165 / C-166 / C-167 / C-171 / C-199 / I-20）。

    版式即规范：**琥珀的「跳过点名」块在上、「已写出」计数块在下**。这是 V2Spec §6 那条红线
    （跳过/过滤的东西一律先点名字和原因，计数放后面）在界面上的样子 —— 谁没进产物、为什么，
    比「写了 168 条用例」重要得多。

    构造：`ExportDoneDialog(result, parent=None)`（result = `contracts.ExportRunResult`）。
    对外信号：`goFixRequested(str symptom)` —— 「去处理这 N 个信号」。
    """

    goFixRequested = QtCore.Signal(str)

    def __init__(self, result, parent=None):
        super(ExportDoneDialog, self).__init__(parent)
        self.setObjectName(names.DONE_DIALOG)
        self.setWindowTitle(terms.DONE_TITLE)
        self.setFont(ui_font())
        self.setMinimumWidth(theme.DONE_DIALOG_W if hasattr(theme, "DONE_DIALOG_W") else 820)
        self.result = result
        self._detail_open = False
        self._build()

    # ── 构建（次序就是契约：skipped 在上、written 在下）──
    def _build(self):
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(18, 16, 18, 14)
        lay.setSpacing(10)
        self._build_skipped(lay)          # ① 琥珀块（C-167 / C-199）
        self._build_written(lay)          # ② 已写出（C-165 / C-177 / C-182 / C-189）
        self._build_accounted(lay)        # ③ 只记录、不产生断言（C-166）
        self._build_errors(lay)           # ④ 写失败（C-171）
        self._build_buttons(lay)

    def _build_skipped(self, lay):
        skipped = self.result.skipped or []
        box = QtWidgets.QFrame(self)
        box.setObjectName(names.DONE_SKIPPED_BLOCK)
        box.setFrameShape(QtWidgets.QFrame.NoFrame)
        box.setAutoFillBackground(True)
        box.setStyleSheet("QFrame#%s{background:%s;border:1px solid %s;}"
                          % (names.DONE_SKIPPED_BLOCK, theme.AMBER_BG, theme.AMBER_BORDER))
        v = QtWidgets.QVBoxLayout(box)
        v.setContentsMargins(12, 10, 12, 10)
        v.setSpacing(4)
        v.addWidget(_label(box, terms.DONE_SKIPPED_HEAD_FMT.format(n=len(skipped)),
                           color=theme.WARN_FG, bold=True))
        self.skipped_view = QtWidgets.QPlainTextEdit(box)
        self.skipped_view.setReadOnly(True)
        self.skipped_view.setFont(mono_font())
        self.skipped_view.setPlainText(self.skipped_text())
        self.skipped_view.setStyleSheet("QPlainTextEdit{background:%s;border:none;color:%s;}"
                                        % (theme.AMBER_BG, theme.WARN_FG))
        self.skipped_view.setMaximumHeight(96)
        v.addWidget(self.skipped_view)
        self.more_btn = QtWidgets.QToolButton(box)
        self.more_btn.setObjectName(names.DONE_SKIPPED_MORE_BTN)
        self.more_btn.setText(terms.DONE_SKIPPED_MORE)
        self.more_btn.setFont(ui_font(theme.FS_UI_SMALL))
        self.more_btn.setCursor(QtCore.Qt.PointingHandCursor)
        self.more_btn.setStyleSheet("QToolButton{border:none;color:%s;text-align:left;}"
                                    % theme.WARN_FG)
        self.more_btn.clicked.connect(self._toggle_detail)
        v.addWidget(self.more_btn)
        box.setVisible(bool(skipped))
        self.skipped_block = box
        lay.addWidget(box)

    def _build_written(self, lay):
        box = QtWidgets.QFrame(self)
        box.setObjectName(names.DONE_WRITTEN_BLOCK)
        v = QtWidgets.QVBoxLayout(box)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(3)
        v.addWidget(_label(box, terms.DONE_WRITTEN, bold=True))
        for line in self.written_lines():
            v.addWidget(_label(box, line, mono=True, size=theme.FS_MONO))
        box.setVisible(bool(self.result.outcomes))
        self.written_block = box
        lay.addWidget(box)

    def _build_accounted(self, lay):
        names_ = self.result.accounted or []
        self.accounted_label = _label(
            self, terms.DONE_ACCOUNTED_FMT.format(names="、".join(names_)) if names_ else "",
            names.DONE_ACCOUNTED, size=theme.FS_UI_SMALL, color=theme.MUTE)
        self.accounted_label.setWordWrap(True)
        self.accounted_label.setVisible(bool(names_))
        lay.addWidget(self.accounted_label)

    def _build_errors(self, lay):
        rows = [terms.DONE_ERROR_ROW_FMT.format(kind=terms.EXPORT_ROWS[k][0], message=m)
                for k, m in (self.result.errors or ())]
        self.errors_label = _label(self, "\n".join(rows), names.DONE_ERRORS, color=theme.BAD_FG)
        self.errors_label.setWordWrap(True)
        self.errors_label.setVisible(bool(rows))
        lay.addWidget(self.errors_label)

    def _build_buttons(self, lay):
        bar = QtWidgets.QHBoxLayout()
        bar.setSpacing(8)
        self.open_dir_btn = _button(self, names.DONE_BTN_OPEN_DIR, terms.DONE_BTN_OPEN_DIR)
        self.open_dir_btn.clicked.connect(self.open_out_dir)
        self.open_dir_btn.setEnabled(bool(self.result.out_dir))
        bar.addWidget(self.open_dir_btn)
        bar.addStretch(1)
        n_skip = len(self.result.skipped or ())
        self.go_fix_btn = _button(self, names.DONE_BTN_GO_FIX,
                                  terms.DONE_BTN_GO_FIX_FMT.format(n=n_skip))
        self.go_fix_btn.clicked.connect(self._go_fix)
        self.go_fix_btn.setVisible(bool(n_skip))              # 只有跳过项时才出现
        bar.addWidget(self.go_fix_btn)
        self.ok_btn = _button(self, names.DONE_BTN_OK, terms.DONE_BTN_OK, primary=True)
        self.ok_btn.clicked.connect(self.accept)
        self.ok_btn.setDefault(True)
        bar.addWidget(self.ok_btn)
        lay.addLayout(bar)

    # ── 文本（测试与状态栏都读这两个）──
    def skipped_text(self, full=None):
        """琥珀块正文：每个信号一行名字 + 一行 `　└ 原因`（C-167，**名字在前**）。"""
        out = []
        for name, reason in (self.result.skipped or ()):
            out.append(str(name))
            body = terms.scrub(str(reason or ""))
            lines = body.splitlines() or [""]
            if not (self._detail_open if full is None else full):
                lines = lines[:1]
            for line in lines:
                out.append(terms.DONE_SKIPPED_ROW_FMT.format(reason=line.strip()))
        return "\n".join(out)

    def written_lines(self):
        """「已写出」每个 outcome 一行 = 路径 + 该交付物的报数口径。"""
        out = []
        for oc in (self.result.outcomes or ()):
            detail = terms.scrub(getattr(oc, "ui_detail", "") or oc.counts_text())
            out.append(terms.DONE_WRITTEN_ROW_FMT.format(path=oc.path, detail=detail).strip())
            for extra in (oc.paths or ())[1:]:               # C-177：报告一次写多份，逐份列出
                out.append(str(extra))
        return out

    def text(self):
        """整个弹层的可读文本（`DialogRecorder` / 顺序断言用）。"""
        parts = []
        if self.result.skipped:
            parts.append(terms.DONE_SKIPPED_HEAD_FMT.format(n=len(self.result.skipped)))
            parts.append(self.skipped_text(full=True))
        parts.append(terms.DONE_WRITTEN)
        parts += self.written_lines()
        if self.result.accounted:
            parts.append(terms.DONE_ACCOUNTED_FMT.format(names="、".join(self.result.accounted)))
        return "\n".join(p for p in parts if p)

    def detailedText(self):
        return self.skipped_text(full=True)

    # ── 动作 ──
    def _toggle_detail(self):
        self._detail_open = not self._detail_open
        self.skipped_view.setPlainText(self.skipped_text())
        self.skipped_view.setMaximumHeight(300 if self._detail_open else 96)
        self.more_btn.setText(terms.DONE_SKIPPED_LESS if self._detail_open
                              else terms.DONE_SKIPPED_MORE)

    def open_out_dir(self):
        """N6：打开输出目录（纯 GUI 一行，不进 Qt-free 层）。"""
        d = str(self.result.out_dir or "")
        if not d:
            return False
        return bool(QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(d)))

    def _go_fix(self):
        self.goFixRequested.emit(GO_FIX_SYMPTOM)
        self.accept()
