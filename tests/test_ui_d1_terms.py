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
