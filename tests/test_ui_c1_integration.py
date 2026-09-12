# -*- coding: utf-8 -*-
"""GUI v2 C1-int：四块接进组合根之后的**跨模块**行为（单块的契约测试各自在 test_ui_*.py）。

这里只放「拆开看谁都对、接起来才可能错」的四件事：
  · C-276 / C-265  载表 → 骨架清单秒出 → worker 逐信号升级 → 指纹没变不重跑；
  · C-244          批量操作只写一次盘（清单 → state → persist 一路挂起）；
  · I-14           起窗 + 载表后，C1 范围内每个 `names.py` 的名字都能 `find` 到；
  · I-19           `ui/` 的 import 方向（views 不直接调引擎、不 import gui）。

用真表（mirror wl，9 行）+ 真 state + 真 worker；后台信号一律 `H.wait_for` 等，不写死毫秒。
"""
import ast
import os

import pytest

import ui_harness as H

pytest.importorskip("PySide6")

from PySide6 import QtGui                                       # noqa: E402

from dreg_verify.ui import contracts, names, persist            # noqa: E402
from dreg_verify.ui import app as A                             # noqa: E402

LC = contracts.ListCol
UI_DIR = os.path.dirname(A.__file__)


@pytest.fixture
def qapp():
    return H.app()


@pytest.fixture
def win(qapp, monkeypatch, tmp_path):
    """真组合根 + 真 state + 真 worker，settings/edits 隔离到 tmp。"""
    H.isolate_settings(monkeypatch, tmp_path)
    H.auto_dialogs(monkeypatch)
    w = A.build_window([])
    w.resize(1600, 900)
    w.show()
    yield w
    w.close()
    qapp.processEvents()


def _load_and_wait(w, kind="wl"):
    """载表并等这一趟分析跑完，返回 (analysisEnded 事件列表, 清单模型)。"""
    ended = []
    w.analysisEnded.connect(lambda vid, ok: ended.append((vid, ok)))
    w.load_path(H.mirror_path(kind))
    assert H.wait_for(lambda: bool(ended)), "worker 没跑完：%s" % (ended,)
    return ended, w.state.models()


# ═══════════════ C-276 / C-265：骨架秒出 → 逐信号升级 → 指纹没变不重跑 ═══════════════
def test_c276_c265_load_skeleton_then_worker_upgrade(win, qapp):
    w = win
    started = []
    w.analysisStarted.connect(lambda vid, n: started.append((vid, n)))
    ended = []
    w.analysisEnded.connect(lambda vid, ok: ended.append((vid, ok)))

    w.load_path(H.mirror_path("wl"))
    # —— ① 还没让出事件循环，清单就已经是「N 行分析中」并且可点（C-276 的全部意义）——
    skeleton = w.state.models()
    n = len(skeleton)
    assert n == 9
    assert {m["status"] for m in skeleton} == {"pending"}
    assert started == [("topout", n)]
    assert w.list_panel.proxy.rowCount() == n
    assert H.find(w, names.LOADING_PANEL).isVisibleTo(w)

    # —— ② worker 逐信号升级 ——
    assert H.wait_for(lambda: bool(ended))
    assert ended == [("topout", True)]
    rows = w.state.models()
    assert len(rows) == n
    assert "pending" not in {m["status"] for m in rows}
    assert not H.find(w, names.LOADING_PANEL).isVisibleTo(w)
    assert w.list_panel.proxy.rowCount() == n

    # —— ③ C-265：指纹没变 → 再叫一次也不跑（也不许把清单打回「分析中」）——
    assert not w.state.needs_analysis("topout")
    assert w.start_analysis() is False
    assert started == [("topout", n)]
    assert "pending" not in {m["status"] for m in w.state.models()}

    # —— ④ 切到别的范围会跑一趟；切回来不跑（C-265 的原话：切页不重跑）——
    w.filter_bar.scope_seg.set_current("logic")
    assert H.wait_for(lambda: len(ended) >= 2)
    assert started[-1][0] == "logic" and ended[-1] == ("logic", True)
    n_logic = len(w.state.models("logic"))
    assert n_logic and "pending" not in {m["status"] for m in w.state.models("logic")}

    w.filter_bar.scope_seg.set_current("topout")
    qapp.processEvents()
    assert len(started) == 2, "切回已经分析过的范围不该再起一趟：%s" % (started,)
    assert len(w.state.models("topout")) == n
    assert "pending" not in {m["status"] for m in w.state.models("topout")}

    # —— ⑤ 覆盖度改档 = 指纹翻篇 → 自动重跑（C-148 / C-265 的另一半）——
    cov = w.state.coverage("topout")
    cov.persist_global_label("穷举")
    w.state.coverage_touched("topout")
    assert H.wait_for(lambda: len(started) >= 3)
    assert started[-1] == ("topout", n)


# ═══════════════ C-244：批量操作只写一次盘 ═══════════════
def test_c244_bulk_check_single_write(win, monkeypatch):
    w = win
    _ended, models = _load_and_wait(w)
    panel = w.list_panel
    assert len(models) == 9 and panel.proxy.rowCount() == 9

    writes = []
    real = persist.write_edits_bucket
    monkeypatch.setattr(persist, "write_edits_bucket",
                        lambda path, patch: writes.append((path, patch)) or real(path, patch))

    # 全部取消勾选：9 个名字一次调用 —— 落盘恰好 1 次
    names_off = panel.uncheck_all()
    assert len(names_off) == 9
    assert len(writes) == 1, "清空勾选写了 %d 次盘" % len(writes)
    assert w.state.checked("topout") == set()                  # 一个都没勾（真实选择，照写）
    assert writes[-1][1]["view_checks"]["topout"] == []

    # 再全选回来：仍然只写 1 次，且回到「全勾 = 默认态」→ 那一格删掉（C-243）
    del writes[:]
    names_on = panel.check_all_visible()
    assert len(names_on) == 9
    assert len(writes) == 1, "全选写了 %d 次盘" % len(writes)
    assert w.state.checked("topout") is None
    assert writes[-1][1]["view_checks"]["topout"] is None

    # 真正吃紧的那一种：一键加反例 = 9 次 `state.set_neg`（每次都会碰编辑），仍然只写一次盘
    del writes[:]
    got = panel.neg_all()
    assert len(got) == 9
    assert len(w.state.negs("topout")) == 9
    assert len(writes) == 1, "一键加反例写了 %d 次盘（C-244 就是为了让它是 1）" % len(writes)


# ═══════════════ I-14：objectName 全覆盖 ═══════════════
#: C2 / C3 / C4 才落地的区（本波 find 不到很正常）——每条注明谁交付
LATER_WAVE_NAMES = {
    "HDR_NAME", "HDR_STATUS_BADGE", "HDR_META", "HDR_PROGRESS_LABEL", "HDR_PROGRESS_BAR",
    "HDR_PROGRESS_DIFF", "HDR_RESOLVE_BTN", "HDR_RESOLVE_PANEL", "HDR_RESOLVE_BOX",
    "HDR_RESOLVE_DIAG_BTN", "HDR_PENDING", "HDR_NOT_EDITABLE", "HDR_SIDE_TOGGLE",
    "HDR_ERROR_LABEL",                                           # C2-c detail_header
    "TABS_BAR", "TABS_TRUTH", "TABS_SV", "MAIN_VIEW_PANEL", "MAIN_PAGE_TRUTH",   # C2-c main_view
    "SV_TOOLBAR", "SV_TITLE", "SV_BTN_TOGGLE_SCOPE", "SV_BTN_COPY", "SV_TEXT",   # C2-c sv_preview
    "SIDE_CHAIN_TITLE", "SIDE_CHAIN_HELP", "SIDE_INPUTS_TITLE",
    "SIDE_INPUTS_GUESS_BADGE",                                    # C2-a side_panel
    "FLOW_TOOLBAR", "FLOW_TITLE", "FLOW_SUBTITLE", "FLOW_BTN_FULLSCREEN", "FLOW_BTN_FIT",
    "FLOW_BTN_100", "FLOW_ZOOM_LABEL", "FLOW_BTN_EXPORT_SVG", "FLOW_BTN_EXPORT_PNG",
    "FLOW_VIEW", "FLOW_VIEWPORT", "FLOW_BODY", "FLOW_EMPTY",
    "FLOW_FOOTER", "FLOW_LEGEND",                                 # C2-b sigflow_view
    "EXPORT_DIALOG", "EXPORT_TABLE", "EXPORT_SUMMARY", "EXPORT_SUMMARY_SKIPPED",
    "EXPORT_BTN_CANCEL", "EXPORT_BTN_RUN", "EXPORT_BTN_IMPORT_CONFIG",
    "EXPORT_OPTIONS_POPOVER",                                     # C4-a export_center
    "DONE_DIALOG", "DONE_SKIPPED_BLOCK", "DONE_WRITTEN_BLOCK", "DONE_BTN_OPEN_DIR",
    "DONE_BTN_GO_FIX", "DONE_BTN_OK",                             # C4-a 导出完成
    "LIST_REASON_RISKY_BTN",      # 只在 needs-prefix 且主按钮不是 diag_risky 的行上出现
    "HARNESS_EXCEL_PATH_EDIT",    # v1 的名字，harness 的退回路径用
}
#: 真值表 / 诊断抽屉整片（C3 / C4-b）+ 对话框整片（`ui/dialogs.py`，弹出时才存在）
LATER_WAVE_PREFIXES = ("TRUTH_", "DIAG_", "DLG_")


def _in_scope(key, value):
    """这个名字本波该不该找得到。占位区（TRUTH_PANEL 等）虽在后面几波的前缀里，但**现在就有**。"""
    if value in A.PLACEHOLDER_AREAS:
        return True
    return not (key in LATER_WAVE_NAMES or key.startswith(LATER_WAVE_PREFIXES))


def _find_any(root, value):
    """控件里找不到就找 QAction（筛选行的「粘贴名单勾选…」是菜单项，不是控件）。"""
    if H._find_opt(root, value) is not None:
        return True
    return any(a.objectName() == value for a in root.findChildren(QtGui.QAction))


def test_ui_names_all_present(win, qapp):
    """I-14：起窗 + 载表 + 展开一个原因块之后，C1 范围内的每个名字都能按 objectName 找到。"""
    w = win
    _ended, models = _load_and_wait(w)
    panel = w.list_panel
    # 原因块只在展开时存在 —— 挑一个有原因块的行点开（LIST_REASON* 三个名字靠它）
    row = next(r for r in range(panel.proxy.rowCount())
               if panel.view.set_expanded(panel.proxy.index(r, int(LC.NAME))
                                          .data(int(contracts.ListRole.NAME))))
    assert row is not None and panel.view.reason_widget is not None
    w.coverage.open_popover()                       # 覆盖度弹层（COV_* 那一批）
    qapp.processEvents()

    want = {k: v for k, v in names.all_names().items() if _in_scope(k, v)}
    assert len(want) > 90, "本波该覆盖的名字只剩 %d 个了，白名单是不是放太宽" % len(want)
    assert not _find_any(w, "no_such_object_name_xyz")     # 先证明这条查找能判「没有」
    missing = [("%s=%s" % (k, v)) for k, v in want.items() if not _find_any(w, v)]
    assert not missing, "这些 objectName 在起窗后找不到：%s" % missing


# ═══════════════ I-19：分层 ═══════════════
#: 视图绝不直接调的引擎模块（要什么经 state / provider 要）
ENGINE_MODULES = ("topout", "pageviews", "sigflow", "generator", "resolver", "vectors",
                  "mux_gen", "cone", "sv_writer")
#: `ui/` 里谁都不许 import 的（拍板 #1：gui.py 整体搬去 legacy_gui）
FORBIDDEN_MODULES = ("gui", "legacy_gui")
#: 唯一例外（架构 §1.2 写明）：电路图视图可以调 sigflow 的**渲染**三个函数，不做分析
LAYERING_EXCEPTIONS = {"sigflow_view.py": ("sigflow",)}


def _imported_modules(path):
    """这个文件 import 了哪些 `dreg_verify.X` 的顶层模块名。"""
    tree = ast.parse(open(path, encoding="utf-8").read(), filename=path)
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                out.add(a.name.split(".")[-1])
        elif isinstance(node, ast.ImportFrom):
            mod = (node.module or "").split(".")
            if node.level and not node.module:                   # from . import x
                out |= {a.name for a in node.names}
            elif mod[:1] == ["dreg_verify"] and len(mod) == 1:    # from dreg_verify import x
                out |= {a.name for a in node.names}
            elif mod:
                out.add(mod[-1])
    return out


def _ui_sources():
    for base, _dirs, files in os.walk(UI_DIR):
        for fn in sorted(files):
            if fn.endswith(".py") and not fn.startswith("__"):
                yield fn, os.path.join(base, fn)


def test_ui_layering():
    bad = []
    for fn, path in _ui_sources():
        mods = _imported_modules(path)
        allowed = LAYERING_EXCEPTIONS.get(fn, ())
        for m in ENGINE_MODULES:
            if m in mods and m not in allowed:
                bad.append("%s import 了引擎模块 %s" % (fn, m))
        for m in FORBIDDEN_MODULES:
            if m in mods:
                bad.append("%s import 了 %s（ui/ 不许依赖旧门面）" % (fn, m))
    assert not bad, "\n".join(bad)


def test_ui_layering_catches_a_known_target():
    """搜索/扫描类测试自己也要能证明它扫得到东西：`gui.py` 确实 import 着引擎模块。"""
    gui_py = os.path.join(os.path.dirname(UI_DIR), "gui.py")
    mods = _imported_modules(gui_py)
    assert mods & set(ENGINE_MODULES), "扫描器没在 gui.py 里看到任何引擎模块，这条规则等于没验"
