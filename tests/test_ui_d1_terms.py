# -*- coding: utf-8 -*-
"""test_ui_d1_terms.py —— Phase D 修复波 F2（界面文案与反馈）的逐条回归。

对抗 review R3 抽了 48003 条用户可见文本，证明三条用户硬规矩
（**跳过必须点名 + 原因、计数在后**；**不写开发者视角的提醒**；**说 IC 工程师的语言**）
在 3 BLOCKER / 10 MAJOR / 6 MINOR 处被破。每条修复在这里各有一句断言，
断言截的就是**屏幕上那句话**（不是「函数返回了什么」）。

整屏扫描那一半在 `tests/test_ui_terms_scan.py`（起窗 + 收全部可见文字 + 三规矩正则）。

口径与别的 UI 测试一致：真组合根 + 真 state + 真 worker，夹具只用 `tests/` 下的
mirror 镜像表（公开仓，**绝不出现真实信号名**）；持久化一律 `H.isolate_settings` 指到 tmp。
"""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import ui_harness as H                                      # noqa: E402

pytest.importorskip("PySide6")

from dreg_verify import inputs_table as IT                  # noqa: E402
from dreg_verify.ui import app as A                         # noqa: E402
from dreg_verify.ui import contracts                        # noqa: E402
from dreg_verify.ui import diagnostics as DG                # noqa: E402
from dreg_verify.ui import export_center as EC              # noqa: E402
from dreg_verify.ui import names                            # noqa: E402
from dreg_verify.ui import signal_list as SL                # noqa: E402
from dreg_verify.ui import sv_preview as SVP                # noqa: E402
from dreg_verify.ui import terms as T                       # noqa: E402

LC = contracts.ListCol
LR = contracts.ListRole


@pytest.fixture(scope="module")
def qapp():
    return H.app()


@pytest.fixture
def win(qapp, monkeypatch, tmp_path):
    H.isolate_settings(monkeypatch, tmp_path)
    H.auto_dialogs(monkeypatch, save_dir=str(tmp_path))
    w = A.MainWindow()
    w.resize(1600, 900)
    w.show()
    yield w
    w.close()
    qapp.processEvents()


def _load(w, kind="btlp", risky=True):
    ended = []
    w.analysisEnded.connect(lambda vid, ok: ended.append((vid, ok)))
    w.load_path(H.mirror_path(kind))
    assert H.wait_for(lambda: bool(ended)), "worker 没跑完"
    if not risky:                       # 关掉「缺前缀是否强制生成」→ needs-prefix 档才出得来
        del ended[:]
        w.state.set_include_risky(False)
        w.start_analysis(force=True)
        assert H.wait_for(lambda: bool(ended)), "worker 没跑完（关了强制生成）"
    H.app().processEvents()
    return w.state.models()


def _first_with(w, key):
    for m in w.state.models():
        if SL.status_key_of(m) == key:
            return m
    return None


# ═══════════════ R3-03 / R2-13 / P-24：Python 异常原文一个字都不上屏 ═══════════════
def test_r3_03_exc_text_maps_every_known_failure_to_one_sentence():
    """七类错误各有一句定版人话；查不到的退兜底句 —— **永远不是 `str(exc)`**。"""
    import json as _json
    import zipfile
    cases = [
        (FileNotFoundError(2, "No such file or directory", r"C:\x\y\表.xlsx"), "找不到这个文件"),
        (PermissionError(13, "Permission denied", r"C:\x\y\wr_rf_tc.sv"), "占着"),
        (IsADirectoryError(21, "Is a directory", r"C:\x\y"), "文件夹"),
        (NotADirectoryError(20, "Not a directory", r"C:\x\y\z"), "文件夹"),
        (zipfile.BadZipFile("File is not a zip file"), "Excel 工作簿"),
        (_json.JSONDecodeError("Expecting value", "{ bad", 2), "JSON"),
        (UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte"), "编码"),
        (OSError(28, "No space left on device"), "读写失败"),
    ]
    for exc, must in cases:
        got = T.exc_text(exc)
        assert must in got, "%s → %r" % (type(exc).__name__, got)
        assert "Errno" not in got and type(exc).__name__ not in got, got
        assert "\\\\" not in got                       # 不要 repr 的双反斜杠
    assert T.exc_text(KeyError("nope")) == T.EXC_FALLBACK
    assert T.exc_text(None) == T.EXC_FALLBACK
    # 路径只给「上级目录/文件名」，不给本机全路径
    assert T.short_path(r"C:\Users\me\proj\out\wr_rf_tc.sv") == "out/wr_rf_tc.sv"
    assert T.exc_text(FileNotFoundError(2, "x", r"C:\a\b\c.xlsx")).endswith("b/c.xlsx")


def test_r3_03_load_error_bar_says_it_in_chinese(win, tmp_path):
    """① 载不进来的表 → 错误条上是人话 + 文件名，没有 `[Errno 2]`、没有本机全路径。"""
    bad = str(tmp_path / "不存在的目录" / "没这张表.xlsx")
    assert not win.load_path(bad)
    text = H.find(win, names.ERROR_TEXT).text()
    assert "Errno" not in text and "\\\\" not in text and bad not in text
    assert "没这张表.xlsx" in text


def test_r3_03_diagnostics_editor_read_write_failures(win, monkeypatch, tmp_path):
    """② 三个编辑器的读 / 写失败 → 编辑器错误行是人话（以前整行就是 `[Errno 13] …`）。"""
    _load(win)
    missing = str(tmp_path / "没有这个映射.txt")
    monkeypatch.setattr(DG, "_ask_open", lambda *a, **k: missing)
    monkeypatch.setattr(DG, "_ask_save", lambda *a, **k: str(tmp_path))   # 目录当文件写
    for cls in (DG.PrefixEditorDialog, DG.ForceEditorDialog):
        d = cls(win.state, win)
        d.on_import()
        txt = H.find(d, names.DIAG_PREFIX_ERRORS if cls is DG.PrefixEditorDialog
                     else names.DIAG_FORCE_ERRORS).text()
        assert "Errno" not in txt and T.EXC_TEXT["FileNotFoundError"] in txt
        assert "没有这个映射.txt" in txt
        d.on_export()
        txt = H.find(d, names.DIAG_PREFIX_ERRORS if cls is DG.PrefixEditorDialog
                     else names.DIAG_FORCE_ERRORS).text()
        assert "Errno" not in txt and "Permission" not in txt
        d.close()


def test_r3_03_supplement_bad_json_gives_coordinates_not_english(win):
    """③ 补充逻辑校验：坏 JSON 只给「第几行第几列」，不给 `Expecting value: line 1 …`。"""
    _load(win)
    d = DG.SupplementEditorDialog(win.state, win)
    d.edit.setPlainText("{ 这不是 json ")
    assert d.on_save() is None
    txt = H.find(d, names.DIAG_SUPP_ERRORS).text()
    assert "Expecting" not in txt and "JSONDecodeError" not in txt
    assert T.EXC_JSON_POS_FMT.split("{")[0] in txt          # 「第 … 行 … 列起读不下去」
    d.close()


def test_r3_03_import_config_bad_json_and_wrong_file(win, tmp_path):
    """④ 导入配置：坏 JSON / 不是本工具的文件 → 两句人话，一个英文异常都没有（R2-13）。"""
    _load(win)
    bad = tmp_path / "半个.json"
    bad.write_text("{ not json ", encoding="utf-8")
    rep = EC.import_config(win.state, str(bad))
    assert "Expecting" not in rep.text() and "Errno" not in rep.text()
    assert T.EXC_TEXT["JSONDecodeError"] in rep.text() and "半个.json" in rep.text()

    gone = tmp_path / "压根没有.json"
    rep2 = EC.import_config(win.state, str(gone))
    assert T.EXC_TEXT["FileNotFoundError"] in rep2.text() and "Errno" not in rep2.text()


# ═══════════════ R3-01 / P-17：跳过原因三屏同一句人话，且点名 ═══════════════
def test_r3_01_p17_skip_reason_is_the_same_human_sentence_on_all_three_screens(win, tmp_path):
    """关掉「缺前缀是否强制生成」→ 缺前缀的信号被跳过。

    以前三屏（导出前摘要点名块 / .sv 预览尾 / 完成弹层琥珀块）同时写着
    「generator.build 未产出该块（规格冲突/空向量/被跳过，见账目）」—— 一个内部函数名
    加三个并列的猜测，而这是 needs-prefix 档**唯一**能给的那句（P-17 同源）。
    现在三屏各自去问同一个 `terms.skip_reason_of`，说的是「缺哪几根网的层级前缀」。"""
    _load(win, "wl", risky=False)
    assert _first_with(win, "needs-prefix"), "关了强制生成却没有 needs-prefix 档，样本选错了"

    rows = EC.default_rows(win.state)
    for r in rows:
        r.enabled = (r.kind == "sv")
    plan = EC.build_plan(win.state, rows)
    assert plan.will_skip, "这一趟一条都没跳过，本条就验不到了"
    res = EC.run_plan(win.state, plan, lambda _r: str(tmp_path / "o.sv"))
    win.sv_preview.set_mode("checked")
    win.sv_preview.refresh(force=True)
    tail = win.sv_preview.text()

    by_name = dict(plan.will_skip)
    assert dict(res.skipped) == by_name, "摘要与完成弹层说的不是同一句"
    hit_prefix = hit_rows = False
    for name, why in plan.will_skip:
        assert "generator" not in why and "未产出该块" not in why       # R3-01 的原句没了
        assert "%s：%s" % (name, why) in tail, "预览尾与摘要说的不是同一句"
        if T.SKIP_REASON_NEEDS_PREFIX_FMT.split("{")[0] in why:
            hit_prefix = True
            assert "_to_mux" in why or "_to_logic" in why, "缺前缀却没点名是哪根网：%r" % why
        if "规格矛盾" in why:
            hit_rows = True
            assert "行与第" in why, "规格矛盾却没点 Excel 行号：%r" % why
    assert hit_prefix and hit_rows, "wl 这趟没同时验到缺前缀与规格矛盾两档"

    # 完成弹层琥珀块上写的就是这一句（名字在前、原因在下一行）
    done = EC.ExportDoneDialog(res, win)
    body = done.skipped_text(full=True)
    for name, why in res.skipped:
        assert name in body and why.splitlines()[0] in body
    assert "generator" not in body
    done.close()


def test_r3_01_skip_reason_of_falls_back_without_an():
    """`an` 取不到（预览侧只有 build 结果）也不能把引擎兜底句照抄上屏。"""
    raw = "被跳过；generator.build 未产出该块（规格冲突/空向量/被跳过，见账目）"
    assert T.skip_reason_of(raw) == T.SKIP_REASON_FALLBACK
    assert T.skip_reason_of("") == T.SKIP_REASON_FALLBACK
    # 认得出的状态各自改写；认不出的（RO 回读这种本来就是人话）原样留
    assert T.skip_reason_of("用户已清空(零用例，本信号不产出测试)") == T.SKIP_REASON_CLEARED
    assert T.skip_reason_of("RO 回读信号，本就不产断言") == "RO 回读信号，本就不产断言"
    got = T.skip_reason_of("mux 规格冲突：mux 页第 7 行与第 3 行选了不同数据源")
    assert "第 7 行与第 3 行" in got and "designer" in got


# ═══════════════ R3-02 / R3-17：行内原因块正文必须点名 ═══════════════
def _reason_body(win, name):
    from PySide6 import QtWidgets
    assert win.list_panel.view.set_expanded(name) == name, name
    H.app().processEvents()
    lab = win.list_panel.view.findChild(QtWidgets.QLabel, names.LIST_REASON_BODY)
    assert lab is not None, "原因块没画出来"
    return lab.text()


def test_r3_02_needs_prefix_reason_block_names_the_nets(win):
    """关了强制生成 → needs-prefix 的原因块正文点名缺前缀的那几根输入。

    以前引擎不给 `issues_meta`，模板的 `{input}` 永远填不上，整段退化成该档的通用悬停解释
    「要 force 的某根输入网埋在子模块里…」——「某根」是哪根，屏幕上没有第二处能查。"""
    _load(win, "wl", risky=False)
    m = _first_with(win, "needs-prefix")
    assert m, "关了强制生成却没有 needs-prefix 档，样本选错了"
    body = _reason_body(win, m["name"])
    assert "某根" not in body and T.STATUS["needs-prefix"][2] not in body
    nets = IT.needs_prefix_rows(win.state.analyze(m["name"]))
    assert nets, "这个信号本该有缺前缀的输入"
    for r in nets[:4]:
        assert r["name"] in body, "缺前缀的输入 %r 没出现在原因块里：%r" % (r["name"], body)


def test_r3_02_risky_generated_reason_block_names_signal_and_nets(win):
    """risky-generated 的原因块必含**信号名 + 裸名 force 的那几根网**（以前一个名字都没有）。"""
    _load(win, "wl")
    m = _first_with(win, "risky-generated")
    assert m, "这张镜像没有 risky-generated 档，样本选错了"
    body = _reason_body(win, m["name"])
    assert T.STATUS["risky-generated"][2] not in body
    nets = IT.needs_prefix_rows(win.state.analyze(m["name"]))
    assert nets
    for r in nets[:4]:
        assert r["name"] in body
    assert "强制生成" in body


def test_r3_17_bare_probe_reason_block_does_not_repeat_the_name(win):
    """R3-17：bare-probe 的原因块以前把同一个名字连写两遍（`{group}` 与 `{signal}` 都填 disp）。"""
    _load(win, "btlp")
    m = _first_with(win, "bare-probe")
    assert m, "这张镜像没有 bare-probe 档，样本选错了"
    body = _reason_body(win, m["name"])
    base = str(m["name"])
    assert body.count(base) == 1, "同一个名字写了 %d 遍：%r" % (body.count(base), body)
    assert base in body and "nets.txt" in body


# ═══════ R3-04 / R3-05 / R3-14 / R3-15 / R3-16 / R2-12 / C-041：定版文案逐条 ═══════
def test_r3_04_add_neg_hint_has_no_contract_id():
    """R3-04：「按 C-096 给第一条正向列加反例」—— 契约 ID 是给维护者看的，不是给用户看的。"""
    assert "C-096" not in T.TRUTH_ADD_NEG_NO_SELECTION
    assert "默认给第一条正向列加反例" in T.TRUTH_ADD_NEG_NO_SELECTION


def test_r3_05_export_write_failed_branches_on_errno():
    """R3-05：目录不存在 ≠ 文件被占用 —— 两种要做的事完全不同，说错就是白查一轮。"""
    import errno
    from dreg_verify import exports as X
    no_dir = X.ExportError("boom", path=r"C:\proj\没这个目录\wr_rf_tc.sv", errno=errno.ENOENT)
    busy = X.ExportError("boom", path=r"C:\proj\out\wr_rf_tc.sv", errno=errno.EACCES)
    full = X.ExportError("boom", path=r"C:\proj\out\wr_rf_tc.sv", errno=errno.ENOSPC)
    assert "文件夹不存在" in T.export_write_failed(no_dir)
    assert T.EXPORT_WRITE_BUSY not in T.export_write_failed(no_dir)
    assert T.EXPORT_WRITE_BUSY in T.export_write_failed(busy)
    assert T.EXPORT_WRITE_NO_SPACE in T.export_write_failed(full)
    for exc in (no_dir, busy, full):                       # 路径只给上级目录 + 文件名
        assert "C:\\" not in T.export_write_failed(exc)
    # 引擎自己判出来的中文原因（没有 errno）留住，不被改写成「写不出去」
    plain = X.ExportError("输出文件不能是源 Excel 本身(回填产物是新文件，源文件不动)")
    assert "源 Excel 本身" in T.export_write_failed(plain)


def test_r3_14_r3_15_r3_16_no_developer_voice():
    """三条 MINOR：「已捕获，未崩」/「面板未调 set_reanalyzer」/「缺少 dreg_verify_config 段」。"""
    blob = "\n".join(str(v) for v in T.all_copy().values() if isinstance(v, str))
    for bad in ("已捕获", "未崩", "set_reanalyzer", "dreg_verify_config"):
        assert bad not in blob, bad
    assert T.HDR_ANALYSIS_FAILED == "分析失败（其余信号不受影响）"
    assert "工具内部没接上" in T.TRUTH_MUX_DATA_NO_REANALYZER
    assert "已还原" in T.TRUTH_MUX_DATA_NO_REANALYZER
    assert T.EXPORT_IMPORT_BAD_FILE == "这不是本工具导出的配置文件（也不是旧版的测试项编辑文件）"


def test_c041_status_badges_keep_the_input_word():
    """C-041（主控裁决）：两档都写「输入缺前缀」—— 输入侧硬阻断 ≠ 输出侧裸名探针。"""
    assert T.STATUS["needs-prefix"][0] == "⚠ 输入缺前缀·跳过"
    assert T.STATUS["risky-generated"][0] == "⚠ 输入缺前缀·已强制生成"
    assert T.STATUS["bare-probe"][0] == "输出裸名·已生成"


def test_r2_12_newer_config_version_says_one_line(win, tmp_path):
    """R2-12：`dreg_verify_config: 3` 以前当 v2 照单全收、一声不吭 —— 新版新增的段被静默忽略。"""
    from dreg_verify import session
    _load(win)
    p = tmp_path / "未来版本.json"
    p.write_text(json.dumps({"dreg_verify_config": session.CONFIG_VERSION + 1,
                             "global": {}}, ensure_ascii=False), encoding="utf-8")
    rep = EC.import_config(win.state, str(p))
    assert rep.ok and T.EXPORT_IMPORT_NEWER_VERSION in rep.text()
    # 认得的版本不说这句（不然每次导入都多一行噪声）
    p2 = tmp_path / "本版.json"
    p2.write_text(json.dumps({"dreg_verify_config": session.CONFIG_VERSION, "global": {}},
                             ensure_ascii=False), encoding="utf-8")
    assert T.EXPORT_IMPORT_NEWER_VERSION not in EC.import_config(win.state, str(p2)).text()


def test_f1_two_new_strings_reviewed():
    """F1 留给 F2 过目的两条：名字在前、不写本机全路径。"""
    # `{names}（共 {n} 列）…` —— 名字在前、计数在后（I-20）
    s = T.TRUTH_MUX_DATA_DROPPED_FMT
    assert s.index("{names}") < s.index("{n}")
    # 备份文件只给文件名（红线①），见 state.load 里的 short_path
    assert "{path}" in T.EDITS_CORRUPT_BACKED_UP_FMT
    assert T.short_path(r"C:\Users\me\.dreg_verify_edits.json.corrupt-2026") == \
        "me/.dreg_verify_edits.json.corrupt-2026"
