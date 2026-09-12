# -*- coding: utf-8 -*-
"""GUI v2 术语 / 文案红线的**起窗版**扫描（C-271 / C-273 / C-274）+ 五种状态的视觉编码（C-277）。

`tests/test_ui_contracts.py` 扫的是 `terms.py` 里的字面量 —— 那只证明「文案表干净」，
不证明「屏幕上干净」：一条裸字符串写在视图里、或某个后端串没过 `terms.scrub` 就贴上去，
字面量扫描一个都抓不到。所以这里**真起一台窗**，把十六区连同 ⑪⑫⑬ 与四个编辑器都打开，
再把屏幕上所有看得见的文字（含 tooltip）收一遍，按 V2Spec §6 的四条红线与术语表逐条判。

**D1 对抗 review R3-18：这四条以前全绿、一条没拦**（3 BLOCKER 从它眼皮底下过去了）。
扫描器自己的三处缺陷都在本次补上：

  ① `_open_everything` 覆盖面太窄 —— 不切 `.sv` 标签、不展开解析明细、只展开**一个**
     信号的原因块、**零条错误路径**、十一个对话框一个没起。现在按 R3 的场景表铺开：
     三台窗（btlp / wl 开着强制生成 / wl 关掉强制生成 —— 八档状态就是这么凑齐的）×
     每档状态各选一个信号（原因块 + 解析明细 + 真值表 + .sv 预览）+ 六条错误路径 +
     十一个对话框 + 覆盖度弹层 + 诊断抽屉与它的四个编辑器。
  ② `_REDLINE_1` 缺四类：`[Errno`、异常类名（`\\w+Error`）、契约 / 不变量 ID
     （`C-\\d{3}` / `I-\\d{2}`）、内部模块与方法名（`generator.` / `set_reanalyzer` /
     `dreg_verify_config` / `*.py`）。R3-03 那七处英文异常原文、R3-04 的「按 C-096」、
     R3-15 的「面板未调 set_reanalyzer」、R3-16 的「缺少 dreg_verify_config 段」
     全是被这四类漏掉的。
  ③ `_FORBIDDEN_OK` 漏了两处**真·就地解释**（`DLG_MUX_DATA_HINT` 的「假绿」、
     解析明细里的 `CUVUNF_FIRST`），于是只能把整块面板排除在扫描之外。

另外两条**机械正则扫不出来**的规矩（「跳过/找不到先点名」「零跳过不出现」）在
`test_c270_*` 里按形状判；逐条修复的断言在 `tests/test_ui_d1_terms.py`。

口径与别的 UI 测试一致：真组合根 + 真 state（→ 真 provider / 真引擎）+ 真 worker，
夹具只用 `tests/` 里的 mirror 镜像表（公开仓，**绝不出现真实信号名**）。
"""
import os
import re

import pytest

import ui_harness as H

pytest.importorskip("PySide6")

from PySide6 import QtWidgets                                  # noqa: E402

from dreg_verify import inputs_table as IT                     # noqa: E402
from dreg_verify.ui import app as A                            # noqa: E402
from dreg_verify.ui import contracts, names, terms, theme      # noqa: E402
from dreg_verify.ui import dialogs as DLG                      # noqa: E402
from dreg_verify.ui import diagnostics as DG                   # noqa: E402
from dreg_verify.ui import export_center as EC                 # noqa: E402
from dreg_verify.ui import signal_list as SL                   # noqa: E402

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
#: 「上次导出到哪」列、完成弹层的「已写出」。它们当然会出现盘符路径 —— 红线①禁的是
#: 「界面上冒出**仓库**路径 / git / 本工具的 CLI」，不是「不许显示用户刚打开的那张表在哪」。
#: 状态栏右段就是「上次导出 <交付物>：<时间> → <路径>」（C-197 / C-198）——用户自己导出的落点
_USER_PATH_NAMES = (names.TOP_EXCEL_PATH, names.STATUS_RIGHT)
#: 这几块底下的所有控件都在显示用户自己的路径（整块跳过，不必逐个控件起名）
_USER_PATH_CONTAINERS = (names.EMPTY_RECENT_LIST, names.DONE_WRITTEN_BLOCK)
#: 动态名：导出中心每一行的「上次导出到哪」
_USER_PATH_PREFIXES = ("export_last_",)
#: `.sv` 预览是**产物本身**（R3-19 主控裁决：文件给红区看，字节受 byte-gate 保护）——
#: 引擎注释原样显示不算违规，整块不扫。
_PRODUCT_NAMES = (names.SV_TEXT,)


def _under(widget, container_names):
    """这个控件是不是挂在 `container_names` 里某一块下面（逐级往上问父）。"""
    p = widget
    while p is not None:
        if p.objectName() in container_names:
            return True
        p = p.parent()
    return False


# ═══════════════════ 场景表：屏幕上到底要打开些什么 ═══════════════════
#: 三台窗。八档状态是这么凑齐的：btlp 出 clean / bare-probe / skip，
#: wl 开着「缺前缀是否强制生成」出 risky-generated / spec-collision，
#: 关掉它同一张表就变成 needs-prefix —— R3-01 的原句只在这一档出现。
_SCENES = (("btlp", True), ("wl", True), ("wl", False))


def _make_win(kind, risky, tmp_path):
    w = A.MainWindow()
    w.resize(1600, 900)
    w.show()
    ended = []
    w.analysisEnded.connect(lambda vid, ok: ended.append((vid, ok)))
    w.load_path(H.mirror_path(kind))
    assert H.wait_for(lambda: bool(ended)), "worker 没跑完（%s）" % kind
    if not risky:
        del ended[:]
        w.state.set_include_risky(False)
        w.start_analysis(force=True)
        assert H.wait_for(lambda: bool(ended)), "worker 没跑完（%s 关强制生成）" % kind
    H.app().processEvents()
    return w


def _open_everything(w, qapp, tmp_path, alive):
    """把该开的都开一遍。返回这台窗上收到的 [(objectName, 来源, 文本)]。

    R3-18：以前这里只展开一个原因块、连 `.sv` 标签都不切，一条错误路径都不走。"""
    out = []

    # ① 每一档状态各选一个信号：行内原因块 + 解析明细 + 真值表 + .sv 预览
    seen = {}
    for m in w.state.models():
        seen.setdefault(SL.status_key_of(m), m["name"])
    assert seen, "这张表一条信号都没有"
    for key, nm in sorted(seen.items()):
        w.state.set_current(nm)
        qapp.processEvents()
        w.list_panel.view.set_expanded(nm)
        w.detail_header.resolve_btn.setChecked(True)
        qapp.processEvents()
        out += _visible_strings(w, tag=key)
        w.main_view.set_tab("sv")
        w.sv_preview.refresh(force=True)
        qapp.processEvents()
        out += _visible_strings(w, tag=key + "/sv")
        w.main_view.set_tab("truth")
        qapp.processEvents()

    # ② 覆盖度弹层 + 诊断抽屉 + 四个编辑器
    w.coverage.open_popover()
    w.diag_drawer.open_for("")
    qapp.processEvents()
    out += _visible_strings(w, tag="cov+diag")
    for cls in (DG.PrefixEditorDialog, DG.ForceEditorDialog,
                DG.SupplementEditorDialog, DG.LegacyImportDialog):
        d = cls(w.state, w)
        alive.append(d)
        out += _visible_strings(d, tag=cls.__name__)

    # ③ 导出中心（六行选项 + 摘要点名块展开）+ 完成弹层（**真跑一趟**，原因是引擎给的）
    ec = EC.ExportCenterDialog(w.state, w)
    alive.append(ec)
    for kind_ in ("sv", "report", "nets"):        # 只有这三行有选项弹层
        ec.open_options(kind_)
        qapp.processEvents()
        out += _visible_strings(ec, tag="ec/" + kind_)
    ec._toggle_skipped()
    qapp.processEvents()
    out += _visible_strings(ec, tag="ec/skipped")

    rows = EC.default_rows(w.state)
    for r in rows:
        r.enabled = (r.kind == "sv")
    plan = EC.build_plan(w.state, rows)
    res = EC.run_plan(w.state, plan, lambda _r: str(tmp_path / "out.sv"))
    done = EC.ExportDoneDialog(res, w)
    alive.append(done)
    done._toggle_detail()
    qapp.processEvents()
    out += _visible_strings(done, tag="done")

    # ④ 六条错误路径（R3-03 那七处英文异常原文就住在这里）
    out += _error_paths(w, qapp, tmp_path, alive)
    return out


def _error_paths(w, qapp, tmp_path, alive):
    """五类错误路径都走一遍：载表失败 / 整表分析炸了 / 导入配置两种 / 编辑器读写失败 / 坏 JSON。"""
    out = []
    w.load_path(str(tmp_path / "没有这张表.xlsx"))            # ① 载不进来
    qapp.processEvents()
    out += _visible_strings(w, tag="err/load")
    w._on_worker_failed(w._scope(), "boom")                   # ② 整表展开炸了
    qapp.processEvents()
    out += _visible_strings(w, tag="err/worker")

    bad = tmp_path / "半个.json"                              # ③ 导入配置：坏 JSON
    bad.write_text("{ not json ", encoding="utf-8")
    out.append(("err/import_bad", "text", EC.import_config(w.state, str(bad)).text()))
    other = tmp_path / "别人的.json"                           # ④ 导入配置：不是本工具的文件
    other.write_text('{"hello": 1}', encoding="utf-8")
    out.append(("err/import_other", "text", EC.import_config(w.state, str(other)).text()))

    missing = str(tmp_path / "没有这个映射.txt")                # ⑤ 三个编辑器的读 / 写失败
    a_dir = tmp_path / "写不进去的目录"                         # 目录当文件写 → 真 OSError
    a_dir.mkdir(exist_ok=True)
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(DG, "_ask_open", lambda *a, **k: missing)
        mp.setattr(DG, "_ask_save", lambda *a, **k: str(a_dir))
        for cls in (DG.PrefixEditorDialog, DG.ForceEditorDialog):
            d = cls(w.state, w)
            alive.append(d)
            d.on_import()
            qapp.processEvents()
            out += _visible_strings(d, tag="err/%s.read" % cls.__name__)
            d.on_export()
            qapp.processEvents()
            out += _visible_strings(d, tag="err/%s.write" % cls.__name__)
    sup = DG.SupplementEditorDialog(w.state, w)                # ⑥ 补充逻辑：坏 JSON
    alive.append(sup)
    sup.edit.setPlainText("{ 这不是 json ")
    sup.on_save()
    qapp.processEvents()
    out += _visible_strings(sup, tag="err/supplement")
    return out


def _all_dialogs(qapp, alive):
    """十一个对话框（不起 MainWindow，各自给一份有内容的入参）。"""
    out = []
    mux_rows = [{"base": "reg_lna", "width": 4, "value": 3},
                {"base": "reg_mix", "width": 8, "value": 0x2A, "override": 0x10}]
    made = [
        ("confirm/clear", DLG.ConfirmDialog(DLG.CONFIRM_CLEAR)),
        ("confirm/del_neg", DLG.ConfirmDialog(DLG.CONFIRM_DEL_NEG, 2, ["U0_NEG", "T1_NEG"])),
        ("confirm/auto_fill", DLG.ConfirmDialog(DLG.CONFIRM_AUTO_FILL)),
        ("confirm/risky_off", DLG.ConfirmDialog(DLG.CONFIRM_RISKY_OFF)),   # 裁决③ 的二次确认
        ("rename", DLG.RenameColumnDialog("T1", ["T1", "T2"])),
        ("mux_data", DLG.MuxDataDialog(mux_rows)),
        ("columns", DLG.ColumnsDialog(["name", "status"])),
        ("paste_names", DLG.PasteNamesDialog()),
        ("presets", DLG.PresetsDialog(DLG.PRESET_SAVE, ["only_bad"])),
        ("dup_labels", DLG.DupLabelsDialog([("assert_1_T0", "d_a", "d_b")])),
        ("import_report", DLG.ImportReportDialog(
            [("d_gone_a", terms.DLG_IMPORT_MISSING_REASON)], counts=["勾选 7 个"])),
        ("batch_fill", DLG.BatchFillDialog(3, 9)),
        ("export_options", DLG.ExportOptionsDialog({})),
    ]
    for tag, d in made:
        alive.append(d)
        d.open()
        qapp.processEvents()
        out += _visible_strings(d, tag="dlg/" + tag)
        # 粘贴名单的结果行只有真填过才出（零跳过那半句不该出现，R3-08）
        if tag == "paste_names":
            d.set_result(3, ["d_gone_a", "d_gone_b"])
            out += _visible_strings(d, tag="dlg/paste_names.result")
    return out


@pytest.fixture(scope="module")
def screen_texts(request):
    """三台窗 × 全部区 + 十一个对话框，屏幕上所有看得见的文字收一遍（整模块只跑一次）。"""
    H.app()
    qapp = H.app()
    texts, alive, wins = [], [], []
    with pytest.MonkeyPatch.context() as mp:
        tmp = request.getfixturevalue("tmp_path_factory").mktemp("terms_scan")
        H.isolate_settings(mp, tmp)
        H.auto_dialogs(mp, save_dir=str(tmp))
        for kind, risky in _SCENES:
            w = _make_win(kind, risky, tmp)
            wins.append(w)
            tag = "%s/risky=%s" % (kind, risky)
            for on, src, text in _open_everything(w, qapp, tmp, alive):
                texts.append(("%s/%s" % (tag, on), src, text))
        texts += _all_dialogs(qapp, alive)
    yield texts
    for d in alive:
        d.close()
    for w in wins:
        w.close()
    qapp.processEvents()


def _visible_strings(w, skip_user_paths=True, tag=""):
    """屏幕上所有看得见的文字 → [(objectName, 来源, 文本)]（含 tooltip）。

    包括隐藏控件：隐藏的那些只是此刻没显示，文案照样是界面文案（换个状态就露出来了）。"""
    out = []
    classes = (QtWidgets.QLabel, QtWidgets.QAbstractButton, QtWidgets.QLineEdit,
               QtWidgets.QPlainTextEdit, QtWidgets.QTextEdit, QtWidgets.QComboBox,
               QtWidgets.QGroupBox)
    title = getattr(w, "windowTitle", None)
    if callable(title) and str(title() or "").strip():
        out.append(("%s/%s" % (tag, w.objectName()), "windowTitle", title()))
    for cls in classes:
        for x in w.findChildren(cls):
            on = x.objectName()
            if on in _PRODUCT_NAMES:
                continue
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
                    out.append(("%s/%s" % (tag, on), getter, t))
    return out


# ═══════════════ C-271：界面不出现仓库路径 / git / CLI 命令 / 「发给维护者」 ═══════════════
#: 红线①（V2Spec §6）。例外只有一条，由裁决⑪ 明文给出：诊断第 2 步那行要在**仿真服务器上
#: 亲手敲**的命令 —— 它是用户的动作，不是本工具的 CLI。
_SANCTIONED_CLI = "python3 scan_rtl.py"
#: 放行 `scan_rtl.py` 本身（诊断三步里「在红区跑 scan_rtl.py」那一行没带 `python3`）
_SANCTIONED = (_SANCTIONED_CLI, "scan_rtl.py")
_REDLINE_1 = (
    (re.compile(r"[A-Za-z]:[\\/]"), "盘符路径"),
    (re.compile(r"(?<![\w.])/(?:home|usr|opt|mnt)/"), "unix 绝对路径"),
    (re.compile(r"\\\\[A-Za-z0-9_.-]+\\"), "UNC 路径"),
    (re.compile(r"(?i)\bgit\b"), "git"),
    (re.compile(r"(?i)\bpython\b"), "命令行"),
    (re.compile(r"(?i)python\s+-m\s"), "本工具的 CLI"),
    (re.compile(r"(?i)\bdreg_verify\s*(?:\.|--)"), "本工具的 CLI"),
    (re.compile(r"发给维护者|发给开发|提交到仓库|请把下面"), "「请把下面发给维护者」"),
    # ── R3-18 补的四类（R3 的 3 BLOCKER + 3 MINOR 就是从这几个缺口过去的）──
    (re.compile(r"\[Errno\b"), "Python 异常原文"),
    (re.compile(r"(?<![\w])\w+Error\b"), "异常类名"),
    (re.compile(r"(?<![\w-])C-\d{3}\b"), "契约 ID"),
    (re.compile(r"(?<![\w-])I-\d{2}\b"), "不变量 ID"),
    (re.compile(r"generator\.|set_reanalyzer|dreg_verify_config|(?<![\w])\w+\.py\b"),
     "内部模块 / 方法名"),
    (re.compile(r"(?i)\bpytest\b|\.venv"), "开发环境"),
)


@pytest.mark.contract("C-271")
def test_c271_no_repo_path_git_or_cli_on_screen(screen_texts):
    """三台窗把每个区、每个对话框、每条错误路径都打开之后，屏幕上一处开发者视角的东西都没有。

    唯一例外是诊断第 2 步那行 `python3 scan_rtl.py`（裁决⑪：红区仿真服务器上必须亲手敲的
    那一条），它在下面单独点名放行 —— 放行是**逐条写下来**的，不是把规则放宽。"""
    assert len(screen_texts) > 2000, "只收到 %d 条文案，扫描大概没覆盖到几个区" % len(screen_texts)

    # 先证明这条扫描扫得到东西（不然「一条都没有」可能只是正则写错了）
    probe = [why for rx, why in _REDLINE_1
             if rx.search(r"照着 C:\code\repo 跑 git log，再 python -m dreg_verify.cli --export")]
    assert len(probe) >= 4, "红线①的正则连一条明显违规的话都抓不全：%s" % probe
    # 新补的四类也各自证明一次（R3-18：扫描器自己也得能证明它扫得到）
    for sample, want in ((r"[Errno 2] No such file or directory: 'x.xlsx'", "Python 异常原文"),
                         ("JSONDecodeError: Expecting value", "异常类名"),
                         ("没选中列，按 C-096 给第一条正向列加反例", "契约 ID"),
                         ("原因已 scrub（I-12）", "不变量 ID"),
                         ("generator.build 未产出该块", "内部模块 / 方法名"),
                         ("面板未调 set_reanalyzer", "内部模块 / 方法名"),
                         ("缺少 dreg_verify_config 段", "内部模块 / 方法名")):
        assert any(rx.search(sample) and why == want for rx, why in _REDLINE_1), \
            "补的正则抓不到 %r（该按 %s 判）" % (sample, want)

    bad = []
    for on, src, text in screen_texts:
        for rx, why in _REDLINE_1:
            if not rx.search(text):
                continue
            if any(s in text for s in _SANCTIONED) and why in ("命令行", "内部模块 / 方法名"):
                continue                                  # 裁决⑪ 的那一条，逐条放行
            bad.append("%s[%s] %s：%r" % (on or "?", src, why, text[:110]))
    assert not bad, "界面上出现了红线①禁的东西：\n%s" % "\n".join(sorted(set(bad))[:40])


@pytest.mark.contract("C-271")
def test_c271_sanctioned_cli_line_is_still_there(win, qapp):
    """例外那一条确实还在（放行不能变成「顺手把它也删了」）。"""
    win.diag_drawer.open_for("")
    qapp.processEvents()
    assert H.find(win.diag_drawer, names.DIAG_STEP2_BOX).text() == _SANCTIONED_CLI


# ═══════════════ C-273：禁用词必须就地解释或换说法 ═══════════════
#: 屏幕上**允许**出现禁用词的地方：{词: ((就地解释过的那几句, …), 为什么这几处不算裸用)}。
#: 一条文本里含禁用词、却不含其中任何一句 = 有人在视图里裸用了术语（或某段后端文本
#: 漏过 `terms.scrub`）。来源一律取常量，文案改了这里跟着走，不会变成一份过期的副本。
_FORBIDDEN_OK = {
    "CUVUNF": ((terms.DIAG_CUVUNF_TITLE, terms.CUVUNF_FIRST),
               "括号前就是人话；括号里留错误码是为了让工程师拿仿真 log 对得上号。"
               "解析明细末尾那句 `CUVUNF_FIRST` 是术语表钦定的『首次出现的完整说法』"
               "（R3-18 之前漏登记，于是整块解析明细只能排除在扫描之外）"),
    "claims": ((terms.EXPORT_ROWS["claims"][0], IT.CLAIMS_TEXT),
               "claims.json 是**产物文件名**，红区脚本按这个名字读；两处都在同一句里"
               "写明了它是什么（『红区比对』/『探针清单』）"),
    "regmap": ((terms.EMPTY_DESC, IT.PAGE_NAME_TEXT["regmap"], IT.FOUND_IN_TEXT["regmap"]),
               "这里列的是 Excel 里真实的页名（用户在 Excel 标签上看到的就是这几页），"
               "不是拿它当术语；R3-06 之后页名位置一律「中文说法（真页名 页）」"),
    "tmm": ((IT.PAGE_NAME_TEXT["tmm"], IT.FOUND_IN_TEXT["tmm"]),
            "同 regmap：页名位置保留真页名 + 括号外的中文说法 = 就地解释"),
    "假绿": ((terms.DLG_MUX_DATA_HINT, terms.TRUTH_MUX_COLLISION,
              terms.STATUS["false-green"][2]),
             "三处都在同一句里跟着写了「驱不动，断言必过」——『假绿』是工程师之间的行话，"
             "带着解释出现比换成一句绕口的描述更好认（R3-18 之前漏登记）"),
}


@pytest.mark.contract("C-273")
def test_c273_forbidden_terms_only_where_explained_in_place(screen_texts):
    """屏幕上每一处禁用词都**就地解释**过；除下面点名的几处，一处裸用都没有。"""
    # 先证明扫描扫得到：拿一条明显裸用的话喂进同一套判据
    naked = "本信号无 cone，按 wire 兜底处理，记账后跳过"
    assert [w for w in terms.FORBIDDEN if w in naked], "禁用词表连这句都判不出来"

    seen, unexpected = {}, []
    for on, src, text in screen_texts:
        for word in terms.FORBIDDEN:
            if word not in text:
                continue
            seen.setdefault(word, set()).add(text)
            oks = (_FORBIDDEN_OK.get(word) or ((), ""))[0]
            if not any(ok and ok in text for ok in oks):
                unexpected.append("%s ← %s：%r" % (word, on, text[:110]))
    assert not unexpected, ("这些地方裸用了禁用词（要么换说法、要么就地解释后登记进 "
                           "_FORBIDDEN_OK）：\n%s" % "\n".join(sorted(set(unexpected))[:40]))

    # 登记在案的那几处，各自的「就地解释」确实还在（登记不是免检，是写明理由）
    for word, (srcs, why) in _FORBIDDEN_OK.items():
        assert srcs and str(why).strip(), word
        for src_text in srcs:
            assert word in src_text, "%s 的登记来源里没有这个词了，登记该删：%r" % (word, src_text)
    # 反过来：登记表不许留没人用的死条目
    assert set(_FORBIDDEN_OK) <= set(seen), \
        "这些登记项屏幕上已经不出现了，该删：%s" % sorted(set(_FORBIDDEN_OK) - set(seen))


# ═══════════════ C-270 / I-20：跳过与找不到，先点名、计数在后 ═══════════════
#: 「计数在前」的形状（机械可判的那几种）。三条都由 D1 对抗 review 的原句反推：
#:   ·「跳过 2 格（只读行/列）」「不迁 0 个 mux 信号：—」—— 数字在名字/坐标前；
#:   ·「全选 12」—— 一个动作名加一个裸数字，没说作用在谁身上；
#:   ·「有 3 个信号的编辑在当前表找不到：…」—— 数字在名字前。
_COUNT_FIRST = (
    re.compile(r"(?:跳过|忽略|不迁|找不到|没认出|漏掉|勾上|回填了|恢复了)\s*\d+\s*"
               r"(?:个|条|格|列|行)[^。\n]{0,24}[:：]"),
    re.compile(r"^(?:全选|清空勾选|全部加反例|勾选选中行|清除反例)\s+\d+$"),
    re.compile(r"^有\s*\d+\s*个.*[:：]"),
)
#: 「零跳过」不该出现在屏幕上（没跳过的东西不用报，报了反而让人去找那个「—」是什么）
_ZERO_SKIP = re.compile(r"(?:跳过|不迁|找不到|没认出|漏掉)\s*0\s*(?:个|条|格|列|行)")


@pytest.mark.contract("C-270")
def test_c270_i20_names_before_counts_and_no_zero_skip(screen_texts):
    """屏幕上没有一句「先报个数、再说是谁」，也没有一句「跳过 0 个」。"""
    # 先证明判据抓得到（R3 原句逐条喂进来）
    for sample in ("粘贴落了 0 格，跳过 2 格（只读行/列）：a×b",
                   "全选 12",
                   "有 3 个信号的编辑在当前表找不到：sig_a、sig_b"):
        assert any(rx.search(sample) for rx in _COUNT_FIRST), sample
    assert _ZERO_SKIP.search("可迁移 0 个信号；不迁 0 个 mux 信号：—")

    bad = []
    for on, src, text in screen_texts:
        for line in text.splitlines():
            line = line.strip()
            if any(rx.search(line) for rx in _COUNT_FIRST):
                bad.append("%s[%s] 计数在前：%r" % (on, src, line[:110]))
            if _ZERO_SKIP.search(line):
                bad.append("%s[%s] 零跳过也报了：%r" % (on, src, line[:110]))
    assert not bad, ("这些句子把计数排在了名字前面（或报了一个「跳过 0 个」）：\n%s"
                     % "\n".join(sorted(set(bad))[:40]))


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
            "禁用词 %r 在术语表查不到替代说法" % word
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
    # R3-06：同一段文本被 scrub 两遍（`resolve_detail` 内部一次、整段再一次）结果必须一样
    for raw in ("regmap 页缺 addr 列", "按 wire 兜底 force 裸名 x",
                IT.FOUND_IN_TEXT["tmm"], "导出 claims 给红区"):
        once = IT.scrub_terms(raw)
        assert IT.scrub_terms(once) == once, "scrub 两遍就变了：%r → %r" % (raw, once)
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


# ═══════════════ 扫描器自己的自检：场景表真的都到过 ═══════════════
def test_scan_reaches_every_scene(screen_texts):
    """「一条违规都没有」与「压根没扫到那一片」在报告上长得一模一样 —— 先证明扫到了。

    R3-18 的教训就是这条：四个测试全绿，而 `.sv` 标签、解析明细、错误路径、
    十一个对话框一个都没打开过，3 BLOCKER 从它眼皮底下过去了。"""
    tags = [on for on, _s, _t in screen_texts]
    blob = "\n".join(tags)
    for want, what in (("btlp/risky=True", "btlp 那台窗"),
                       ("wl/risky=True", "wl 开着强制生成"),
                       ("wl/risky=False", "wl 关掉强制生成（needs-prefix 档）"),
                       ("/sv", ".sv 预览标签"),
                       ("cov+diag", "覆盖度弹层 + 诊断抽屉"),
                       ("ec/sv", "导出中心的选项弹层"),
                       ("ec/skipped", "导出前摘要的点名块"),
                       ("done", "导出完成弹层"),
                       ("err/load", "载表失败"),
                       ("err/worker", "整表展开炸了"),
                       ("err/import_bad", "导入坏 JSON 配置"),
                       ("err/PrefixEditorDialog.read", "编辑器读失败"),
                       ("err/supplement", "补充逻辑校验失败"),
                       ("dlg/confirm/risky_off", "「缺前缀是否强制生成」二次确认"),
                       ("dlg/import_report", "导入结果框"),
                       ("dlg/mux_data", "mux 数据值框")):
        assert want in blob, "这一片一条都没扫到：%s" % what
    # 八档状态各选一个信号（`_open_everything` 的第 ① 步）—— 至少这五档要在
    keys = {t.split("/")[2] for t in tags if t.count("/") >= 2}
    for key in ("clean", "bare-probe", "skip", "risky-generated", "needs-prefix",
                "spec-collision"):
        assert key in keys, "%s 档一个信号都没展开过（扫到的档：%s）" % (key, sorted(keys))
    # 十一个对话框（这里起了 13 个：四种确认各算一个）
    assert len({t for t in tags if t.startswith("dlg/")}) >= 13
    assert len(screen_texts) > 2000, "只收到 %d 条" % len(screen_texts)


def test_scan_writes_nothing_into_the_repo(screen_texts):
    """扫描器自己不许往仓库里写产物（它真跑了一趟 .sv 导出 + 三次落盘失败）。"""
    here = os.path.dirname(os.path.abspath(__file__))
    for junk in ("out.sv", "半个.json", "别人的.json", "probe_prefixes.txt"):
        assert not os.path.exists(os.path.join(here, junk)), junk
