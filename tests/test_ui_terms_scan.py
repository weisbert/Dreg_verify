# -*- coding: utf-8 -*-
"""GUI v2 术语 / 文案红线的**起窗版**扫描（C-271 / C-273 / C-274）+ 五种状态的视觉编码（C-277）。

`tests/test_ui_contracts.py` 扫的是 `terms.py` 里的字面量 —— 那只证明「文案表干净」，
不证明「屏幕上干净」：一条裸字符串写在视图里、或某个后端串没过 `terms.scrub` 就贴上去，
字面量扫描一个都抓不到。所以这里**真起一台窗**，把十六区连同 ⑪⑫⑬ 与四个编辑器都打开，
再把屏幕上所有看得见的文字（含 tooltip）收一遍，按 V2Spec §6 的四条红线与术语表逐条判。

口径与别的 UI 测试一致：真组合根 + 真 state（→ 真 provider / 真引擎）+ 真 worker，
夹具只用 `tests/` 里的 mirror 镜像表（公开仓，**绝不出现真实信号名**）。
"""
import re

import pytest

import ui_harness as H

pytest.importorskip("PySide6")

from PySide6 import QtWidgets                                  # noqa: E402

from dreg_verify import inputs_table as IT                     # noqa: E402
from dreg_verify.ui import app as A                            # noqa: E402
from dreg_verify.ui import contracts, names, terms, theme      # noqa: E402
from dreg_verify.ui import diagnostics as DG                   # noqa: E402
from dreg_verify.ui import export_center as EC                 # noqa: E402

TR = contracts.TruthRole


@pytest.fixture
def qapp():
    return H.app()


@pytest.fixture
def win(qapp, monkeypatch, tmp_path):
    H.isolate_settings(monkeypatch, tmp_path)
    H.auto_dialogs(monkeypatch, save_dir=str(tmp_path))
    w = A.MainWindow()                      # 不走 build_window：本条不要 apply_startup 的自动载表
    w.resize(1600, 900)
    w.show()
    ended = []
    w.analysisEnded.connect(lambda vid, ok: ended.append((vid, ok)))
    w.load_path(H.mirror_path("btlp"))
    assert H.wait_for(lambda: bool(ended)), "worker 没跑完"
    yield w
    w.close()
    qapp.processEvents()


#: 显示用户**自己选的 / 自己导出的文件**的那几处：顶栏路径框、空态「最近打开」、导出中心的
#: 「上次导出到哪」列。它们当然会出现盘符路径 —— 红线①禁的是「界面上冒出**仓库**路径 / git /
#: 本工具的 CLI」，不是「不许显示用户刚打开的那张表在哪」。
_USER_PATH_NAMES = (names.TOP_EXCEL_PATH,)
#: 这几块底下的所有控件都在显示用户自己的路径（整块跳过，不必逐个控件起名）
_USER_PATH_CONTAINERS = (names.EMPTY_RECENT_LIST,)
#: 动态名：导出中心每一行的「上次导出到哪」
_USER_PATH_PREFIXES = ("export_last_",)


def _under(widget, container_names):
    """这个控件是不是挂在 `container_names` 里某一块下面（逐级往上问父）。"""
    p = widget
    while p is not None:
        if p.objectName() in container_names:
            return True
        p = p.parent()
    return False


def _open_everything(w, qapp):
    """把 ⑪⑫⑬ 与四个编辑器、覆盖度弹层、行内原因块全部打开 —— 扫描才扫得到它们的文案。"""
    alive = []
    assert w.list_panel.select_first_visible()
    panel = w.list_panel
    for r in range(panel.proxy.rowCount()):          # 展开一个有原因块的行
        nm = panel.proxy.index(r, int(contracts.ListCol.NAME)).data(int(contracts.ListRole.NAME))
        if panel.view.set_expanded(nm):
            break
    w.coverage.open_popover()
    ec = EC.ExportCenterDialog(w.state, w)
    ec.open_options("nets")
    alive.append(ec)
    alive.append(EC.ExportDoneDialog(contracts.ExportRunResult(
        skipped=[("d_fake", "输入缺层级前缀")], accounted=["d_ro"],
        errors=[("sv", "写不出去（文件被占用？）")]), w))
    w.diag_drawer.open_for("")
    for cls in (DG.PrefixEditorDialog, DG.ForceEditorDialog,
                DG.SupplementEditorDialog, DG.LegacyImportDialog):
        alive.append(cls(w.state, w))
    qapp.processEvents()
    return alive


def _visible_strings(w, skip_user_paths=True):
    """屏幕上所有看得见的文字 → [(objectName, 来源, 文本)]（含 tooltip）。

    包括隐藏控件：隐藏的那些只是此刻没显示，文案照样是界面文案（换个状态就露出来了）。"""
    out = []
    classes = (QtWidgets.QLabel, QtWidgets.QAbstractButton, QtWidgets.QLineEdit,
               QtWidgets.QPlainTextEdit, QtWidgets.QComboBox, QtWidgets.QGroupBox)
    for cls in classes:
        for x in w.findChildren(cls):
            on = x.objectName()
            if skip_user_paths and (on in _USER_PATH_NAMES
                                    or on.startswith(_USER_PATH_PREFIXES)
                                    or _under(x, _USER_PATH_CONTAINERS)):
                continue
            for getter in ("text", "toPlainText", "currentText", "title",
                           "placeholderText", "toolTip"):
                fn = getattr(x, getter, None)
                if not callable(fn):
                    continue
                try:
                    t = fn()
                except Exception:                    # noqa: BLE001  某些控件的 text() 要参数
                    continue
                if isinstance(t, str) and t.strip():
                    out.append((on, getter, t))
    return out


# ═══════════════ C-271：界面不出现仓库路径 / git / CLI 命令 / 「发给维护者」 ═══════════════
#: 红线①（V2Spec §6）。例外只有一条，由裁决⑪ 明文给出：诊断第 2 步那行要在**仿真服务器上
#: 亲手敲**的命令 —— 它是用户的动作，不是本工具的 CLI。
_SANCTIONED_CLI = "python3 scan_rtl.py"
_REDLINE_1 = (
    (re.compile(r"[A-Za-z]:[\\/]"), "盘符路径"),
    (re.compile(r"(?<![\w.])/(?:home|usr|opt|mnt)/"), "unix 绝对路径"),
    (re.compile(r"\\\\[A-Za-z0-9_.-]+\\"), "UNC 路径"),
    (re.compile(r"(?i)\bgit\b"), "git"),
    (re.compile(r"(?i)\bpython\b"), "命令行"),
    (re.compile(r"(?i)python\s+-m\s"), "本工具的 CLI"),
    (re.compile(r"(?i)\bdreg_verify\s*(?:\.|--)"), "本工具的 CLI"),
    (re.compile(r"发给维护者|发给开发|提交到仓库|请把下面"), "「请把下面发给维护者」"),
)


@pytest.mark.contract("C-271")
def test_c271_no_repo_path_git_or_cli_on_screen(win, qapp):
    """起窗 + 载表 + 把每个区都打开之后，屏幕上一处仓库路径 / git / CLI 命令都没有。

    唯一例外是诊断第 2 步那行 `python3 scan_rtl.py`（裁决⑪：红区仿真服务器上必须亲手敲的
    那一条），它在下面单独点名放行 —— 放行是**逐条写下来**的，不是把规则放宽。"""
    alive = _open_everything(win, qapp)
    strings = _visible_strings(win)
    assert len(strings) > 200, "只收到 %d 条文案，扫描大概没覆盖到几个区" % len(strings)

    # 先证明这条扫描扫得到东西（不然「一条都没有」可能只是正则写错了）
    probe = [why for rx, why in _REDLINE_1
             if rx.search(r"照着 C:\code\repo 跑 git log，再 python -m dreg_verify.cli --export")]
    assert len(probe) >= 4, "红线①的正则连一条明显违规的话都抓不全：%s" % probe

    bad = []
    for on, src, text in strings:
        for rx, why in _REDLINE_1:
            m = rx.search(text)
            if not m:
                continue
            if _SANCTIONED_CLI in text and why == "命令行":
                continue                                  # 裁决⑪ 的那一条，逐条放行
            bad.append("%s[%s] %s：%r" % (on or "?", src, why, text[:90]))
    assert not bad, "界面上出现了红线①禁的东西：\n%s" % "\n".join(bad)

    # 例外那一条确实还在（放行不能变成「顺手把它也删了」）
    assert H.find(win.diag_drawer, names.DIAG_STEP2_BOX).text() == _SANCTIONED_CLI
    for d in alive:
        d.close()


# ═══════════════ C-273：禁用词必须就地解释或换说法 ═══════════════
#: 屏幕上**允许**出现禁用词的地方：{词: (来源文案, 就地解释必须含的那一截, 为什么这一处不算裸用)}。
#: 多出任何一处 = 有人在视图里裸用了术语（或某段后端文本漏过 `terms.scrub`）。
_FORBIDDEN_OK = {
    "CUVUNF": (terms.DIAG_CUVUNF_TITLE, "找不到这根网",
               "括号前就是人话；括号里留错误码是为了让工程师拿仿真 log 对得上号"),
    "claims": (terms.EXPORT_ROWS["claims"][0], "红区比对",
               "claims.json 是**产物文件名**，红区脚本按这个名字读；同一行的备注写明了它是什么"),
    "regmap": (terms.EMPTY_DESC, "logic / mux / dft / iddq / regmap",
               "这里列的是 Excel 里真实的页名（用户在 Excel 里看到的就是这几页），不是拿它当术语"),
}


@pytest.mark.contract("C-273")
def test_c273_forbidden_terms_only_where_explained_in_place(win, qapp):
    """屏幕上每一处禁用词都**就地解释**过；除下面点名的三处，一处裸用都没有。"""
    alive = _open_everything(win, qapp)
    strings = _visible_strings(win, skip_user_paths=False)

    # 先证明扫描扫得到：拿一条明显裸用的话喂进同一套判据
    naked = "本信号无 cone，按 wire 兜底处理，记账后跳过"
    assert [w for w in terms.FORBIDDEN if w in naked], "禁用词表连这句都判不出来"

    seen = {}
    for on, src, text in strings:
        for word in terms.FORBIDDEN:
            if word in text:
                seen.setdefault(word, set()).add(text)
    unexpected = []
    for word, texts in sorted(seen.items()):
        ok_src = _FORBIDDEN_OK.get(word)
        for text in sorted(texts):
            if ok_src is not None and ok_src[0] in text:
                continue
            unexpected.append("%s ← %r" % (word, text[:110]))
    assert not unexpected, ("这些地方裸用了禁用词（要么换说法、要么就地解释后登记进 "
                           "_FORBIDDEN_OK）：\n%s" % "\n".join(unexpected))

    # 登记在案的三处，各自的「就地解释」确实还在（登记不是免检，是写明理由）
    for word, (src_text, must_have, why) in _FORBIDDEN_OK.items():
        assert word in src_text, "%s 的登记来源里没有这个词了，登记该删：%r" % (word, src_text)
        assert must_have in src_text, "%s 的就地解释没了（%s）：%r" % (word, why, src_text)
        assert str(why).strip()
    # 反过来：登记表不许留没人用的死条目
    assert set(_FORBIDDEN_OK) <= set(seen), \
        "这些登记项屏幕上已经不出现了，该删：%s" % sorted(set(_FORBIDDEN_OK) - set(seen))
    for d in alive:
        d.close()


# ═══════════════ C-274：术语替换表是一份可查的表 ═══════════════
_F_RANGE_RE = re.compile(r"^f(\d)[–\-—]f(\d)$")


def _covers(bad_cell, word):
    """术语表第一列的这一格，管不管 `word` 这个禁用词。

    两处不能按裸子串判：① `F0–F4` 是**一段区间**，`FORBIDDEN` 里是逐个列的 F0…F4；
    ② `wire 兜底` 与 `wire兜底` 只差一个空格（两种写法都在 `FORBIDDEN` 里，表里只写一种）。"""
    b, w = bad_cell.strip().lower(), word.strip().lower()
    if w.replace(" ", "") in b.replace(" ", ""):
        return True
    m = _F_RANGE_RE.fullmatch(b.replace(" ", ""))
    if m and re.fullmatch(r"f\d", w):
        return int(m.group(1)) <= int(w[1]) <= int(m.group(2))
    return False


@pytest.mark.contract("C-274")
def test_c274_term_table_is_queryable_and_wired_to_scrub():
    """V2Spec §6 的 T 数组落成 `terms.TERMS`（可查）+ `inputs_table.scrub_terms`（可执行）两半。

    「可查的表」不是注释：三列各有内容、每个禁用词都能在表里查到替代说法；而后端文本真正被替换
    的那一半在 `scrub_terms` —— 表里写了却没人替换，跟没写一样。"""
    assert len(terms.TERMS) == 11, "术语表少了：%d 行" % len(terms.TERMS)
    for row in terms.TERMS:
        assert len(row) == 3, row
        bad, short, full = row
        assert bad.strip() and short.strip() and full.strip(), row
        # 替代说法不能又把那个词原样抄回来（那等于没换）——「界面上写成」这一列尤其
        assert bad.strip().lower() != short.strip().lower(), row
    # 每个禁用词都能在表里查到（表是给人查的，查不到就等于没有这一条）
    for word in terms.FORBIDDEN:
        assert any(_covers(bad, word) for bad, _s, _f in terms.TERMS), \
            "禁用词 %r 在术语表里查不到替代说法" % word
    # 可执行的那一半：后端文本里的内部说法真的被换掉了
    for raw, gone in (("无 cone，跳过+记账", "cone"),
                      ("输入是 prefixed-wire", "prefixed-wire"),
                      ("必 CUVUNF", "CUVUNF"),
                      ("查 regmap 得到地址", "regmap"),
                      ("tmm 里没有这个字段", "tmm"),
                      ("wire 兜底处理", "wire 兜底")):
        out = IT.scrub_terms(raw)
        assert gone not in out, "scrub_terms 没把 %r 换掉：%r" % (gone, out)
        assert out != raw and out.strip()
    assert terms.scrub is IT.scrub_terms, "ui 侧的 scrub 必须就是那一份，不许另抄一套"
    # 红线四条也是可查的（不是散在各处的口头约定）
    assert len(terms.REDLINES) == 4 and all(str(x).strip() for x in terms.REDLINES)


# ═══════════════ C-277：五种状态的视觉编码只有一份 ═══════════════
@pytest.mark.contract("C-277")
def test_c277_five_states_share_one_visual_encoding(win, qapp):
    """V2Spec §3 点名的五种状态，各自的视觉编码只有**一处**定义，且同严重度共用同一份色 token：

      ① Excel 没有某一页   → 范围段置灰 + `SCOPE_MISSING_FMT` tooltip + 状态栏 `STATUS_MISSING_PAGES_FMT`
      ② 未解析信号         → `terms.STATUS["unresolved"]` 色档 bad
      ③ 规格冲突           → `terms.STATUS["spec-collision"]` 色档 bad（与 ② 同色 = 同一档严重度）
      ④ 猜名的线网         → `terms.STATUS["wire-fallback"]` 色档 warn + 输入表 / 电路图同族琥珀底
      ⑤ 手填与 auto 不一致 → `theme.CELL_STATES["diff"]`，字色 = bad 那一族红

    判据是「同一件事在哪儿都长一个样」：色档表只有 `theme.TONE_COLORS` 一张，
    tone 的判定只有 `terms.tone_of` 一处，清单与详情徽标从同一对颜色取值。"""
    # ── 色档表只有一张，四档两两不同 ──
    assert set(theme.TONE_COLORS) == {"ok", "warn", "bad", "note"}
    pairs = list(theme.TONE_COLORS.values())
    assert len(set(pairs)) == 4, "有两档共用同一对颜色，屏幕上就分不出来了：%s" % pairs
    for key, (_label, tone, help_text) in terms.STATUS.items():
        assert tone in theme.TONE_COLORS, "状态档 %s 的色档 %r 不在 TONE_COLORS 里" % (key, tone)
        assert str(help_text).strip(), "状态档 %s 没有悬停解释（C-016）" % key

    # ── ②③④：三种状态的色档 ──
    assert terms.STATUS["unresolved"][1] == "bad"
    assert terms.STATUS["spec-collision"][1] == "bad"
    assert terms.STATUS["wire-fallback"][1] == "warn"
    # ⑤ 手填与 auto 不一致：与「有问题」同一族红；反例与「要注意」同一族琥珀
    assert theme.CELL_STATES["diff"][2] == theme.BAD_FG
    assert theme.CELL_STATES["neg"][2] == theme.WARN_FG
    assert theme.CELL_STATES["dft"][2] == theme.LIGHT_BLUE_FG
    assert dict(terms.TRUTH_LEGEND)["diff"] and dict(terms.TRUTH_LEGEND)["neg"]
    # ④ 猜名：输入表底 / 电路图盒底是同一族（都在琥珀/米色那边，不是各挑各的）
    assert theme.GUESS_BG and theme.FLOW_GUESS_BG and theme.AMBER_FG == theme.WARN_FG

    # ── ① 起窗验：mirror 没有 iddq 页 → 那一段置灰 + tooltip 就是那句 ──
    missing = [v for v in contracts.VIEW_IDS if not win.state.page_available(v)]
    assert missing, "这张 mirror 每一页都在，①（Excel 没有某一页）就验不到了"
    for vid in missing:
        btn = win.filter_bar.scope_seg.button(vid)
        assert not btn.isEnabled(), "缺 %s 页，范围段却还点得动" % vid
        assert btn.toolTip() == terms.SCOPE_MISSING_FMT.format(page=vid)
    assert win.filter_bar.missing_pages_text() == \
        terms.STATUS_MISSING_PAGES_FMT.format(pages="、".join(missing))

    # ── 起窗验：同一个信号，清单状态格与详情徽标取的是**同一对**颜色 ──
    assert win.list_panel.select_first_visible()
    qapp.processEvents()
    name = win.state.current_name
    model = win.state.model_of(name)
    tone = terms.tone_of(model)
    fg, bg = theme.TONE_COLORS[tone]
    row = next(r for r in range(win.list_panel.proxy.rowCount())
               if win.list_panel.proxy.index(r, int(contracts.ListCol.NAME))
               .data(int(contracts.ListRole.NAME)) == name)
    assert win.list_panel.proxy.index(row, int(contracts.ListCol.STATUS)) \
        .data(int(contracts.ListRole.TONE)) == tone, "清单给的色档与 terms.tone_of 不是一个"
    css = H.find(win, names.HDR_STATUS_BADGE).styleSheet()
    assert fg in css and bg in css, "详情徽标没用 TONE_COLORS 那一对：%r" % css
    assert H.find(win, names.HDR_STATUS_BADGE).text() == terms.STATUS[terms.status_key_of(model)][0]

    # ── ⑤ 起窗验：手填一个与 auto 不一样的期望 → 那一格的 CELL_STATE 就是 diff ──
    name = next((m["name"] for m in win.state.models()
                 if (win.state.analyze(m["name"]) or {}).get("editable")), "")
    assert name, "mirror 里没有可编辑的信号，⑤ 验不到"
    win.state.set_current(name)
    qapp.processEvents()
    m = win.truth_panel.model
    assert m.columnCount() > 0 and m.rowCount() >= 2
    r_auto, r_exp = m.rowCount() - 2, m.rowCount() - 1
    auto = m.data(m.index(r_auto, 0), int(TR.RAW_VALUE))
    assert auto is not None, "拿不到 auto_out 的原值"
    assert m.setData(m.index(r_exp, 0), str(int(auto) ^ 1))
    assert m.data(m.index(r_exp, 0), int(TR.CELL_STATE)) == "diff"
    assert m.setData(m.index(r_exp, 0), str(int(auto)))
    assert m.data(m.index(r_exp, 0), int(TR.CELL_STATE)) == "match"
