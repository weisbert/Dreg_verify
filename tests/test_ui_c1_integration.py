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
#: C3 / C4 才落地的区（本波 find 不到很正常）——每条注明谁交付。
#: ⚠ C2-int 把详情区四件接进组合根后，HDR_* / TABS_* / MAIN_* / SV_* / SIDE_* / FLOW_*
#:   整片从这张白名单里**删掉**了：它们现在起窗就该找得到（白名单只减不增）。
LATER_WAVE_NAMES = {
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
    # C2-int 把详情区四件接进来之后这个数只增不减（C1-int 收尾时是 95）
    assert len(want) >= 130, "本波该覆盖的名字只剩 %d 个了，白名单是不是放太宽" % len(want)
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

#: Qt-free 层（架构 §1.2 第三层 + `excel_model`）。这一层 `ui/` **可以**依赖，
#: 但不是随便依赖 —— 逐文件按下面的白名单准入（架构 §8 决策 9，C2-int 主控裁决）。
QTFREE_MODULES = ("session", "edits", "inputs_table", "analysis_norm", "exports",
                  "providers", "truth_edit", "excel_model")
#: {ui 文件: {Qt-free 模块: 为什么这一条准入}}。白名单以**实际 import** 为准：
#: 多一条要写清理由、少一条（有 import 没登记）直接报红。
QTFREE_ALLOWED = {
    "terms.py": {
        "inputs_table": "scrub_terms / STATUS_HELP / FOUND_IN_TEXT —— 术语替换表只有那一份（§1.1 明写）",
    },
    "persist.py": {
        "session": "settings 的格式、默认路径与 recent / last_export 的结构",
        "edits": "edits 文件的桶结构与默认路径（I-05 桶合并写按它的段名走）",
        "exports": "导出选项的默认值与 load/store（I-03：四个 export_* 键不另写一套）",
    },
    "state.py": {
        "session": "CoverageState（三层覆盖度）/ code_version / MRU —— 会话格式的唯一定义",
        "edits": "编辑桶读写与 protected_negatives 的口径",
        "providers": "载表后按范围建 TopoutProvider / PageProvider（I-21 的引擎锁在 state 手上）",
        "excel_model": "load_workbook：把 Excel 读成 wb（state 是全窗唯一持有 wb 的地方）",
    },
    "bus.py": {
        "excel_model": "`_strip_width`：当前线网的比对键剥位宽 —— 与图 / 真值表同一把钥匙",
    },
    "coverage.py": {
        "session": "FORM_COV_ROWS / effective_chain —— 三层覆盖度的判据只此一份（§6.5；主控裁决允许）",
    },
    "detail_header.py": {
        "edits": "fill_progress / expected_cell_state / cols_from_vectors（C-106 的三个数）",
        "inputs_table": "resolve_detail —— 解析明细全文（C-064 / C-065 / C-066）",
    },
    "side_panel.py": {
        "inputs_table": "input_rows —— 输入表的全部内容（角色细分 / 收拢 / 三个布尔，主控裁决允许）",
        "analysis_norm": "subst_expr —— 单级 logic 的「= 代入真名」（不在视图里自己写正则）",
    },
    "sv_preview.py": {
        "exports": "render_sv / build_skipped / sv_outcome —— 预览与导出同源（§6.8 一条路径原则）",
    },
    "dialogs.py": {
        "edits": "反例列的保护性判定（哪些是自定义命名 / 手调过错值的）",
        "truth_edit": "check_col_name / parse_int —— 重命名列与 mux 数据值的三道校验",
    },
    # ── ui/truth/ 子包（C3；键带子目录）──
    "truth/model.py": {
        "edits": "列 schema 与全部列操作的 Qt-free 实现（cols_from_vectors / add_col / expected_cell_state …），model 只包撤销",
        "inputs_table": "drive_ctx / vector_drives —— 列头驱动明细（C-063）按当前列向量算",
        "truth_edit": "parse_int / check_col_name —— 八种数值写法与列名校验只此一份",
    },
    "truth/rows.py": {
        "inputs_table": "vheader_display —— 输入行标签「真名　(角色 · 端口)」（C-074）",
    },
    "truth/view.py": {
        "inputs_table": "input_rows —— 冻结左列的猜名 / 需前缀 / rw / note 元信息按 key 查（C-074 / C-075）",
    },
    "truth/delegate.py": {
        "truth_edit": "parse_int —— 编辑器即时校验红框（C-083，不阻止提交）",
    },
}


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
                path = os.path.join(base, fn)
                # 键 = 相对 ui/ 的路径（顶层文件就是文件名；子包写 "truth/model.py"），免得子包重名串号
                yield os.path.relpath(path, UI_DIR).replace(os.sep, "/"), path


def test_ui_layering():
    """I-19：① 引擎模块与旧门面一律禁；② Qt-free 层按 `QTFREE_ALLOWED` 显式准入。

    ② 是 C2-int 的主控裁决（架构 §8 决策 9）：分层线的真正含义是「views 不 import 引擎 / gui」，
    Qt-free 层（session / edits / inputs_table / analysis_norm / exports / providers / truth_edit
    / excel_model）本来就该给 ui 用 —— 但**谁用哪一个、为什么**要写下来，否则「允许 Qt-free」
    会一路滑成「什么都能 import」。"""
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
        known = QTFREE_ALLOWED.get(fn, {})
        for m in sorted(set(mods) & set(QTFREE_MODULES)):
            if m not in known:
                bad.append("%s import 了 Qt-free 模块 %s，但 QTFREE_ALLOWED 里没登记"
                           "（要么别 import，要么在白名单里写一句为什么）" % (fn, m))
    assert not bad, "\n".join(bad)


def test_ui_layering_whitelist_has_no_dead_entries():
    """白名单以**实际 import** 为准：登记了却没人 import 的条目要删掉（别留过期的许可）。"""
    actual = {fn: _imported_modules(path) for fn, path in _ui_sources()}
    dead = []
    for fn, entries in QTFREE_ALLOWED.items():
        mods = actual.get(fn)
        if mods is None:
            dead.append("%s 这个文件已经没了" % fn)
            continue
        for m, why in entries.items():
            if m not in mods:
                dead.append("%s 已经不 import %s 了" % (fn, m))
            assert str(why).strip(), "%s → %s 少了「为什么」" % (fn, m)
    assert not dead, "\n".join(dead)


def test_ui_layering_catches_a_known_target():
    """搜索/扫描类测试自己也要能证明它扫得到东西：`gui.py` 确实 import 着引擎模块。"""
    gui_py = os.path.join(os.path.dirname(UI_DIR), "gui.py")
    mods = _imported_modules(gui_py)
    assert mods & set(ENGINE_MODULES), "扫描器没在 gui.py 里看到任何引擎模块，这条规则等于没验"
