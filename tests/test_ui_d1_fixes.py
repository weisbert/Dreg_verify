# -*- coding: utf-8 -*-
"""test_ui_d1_fixes.py —— Phase D 修复波 F1 里**要起窗**才验得到的那几条。

Qt-free 的引擎层回归在 `tests/test_d1_engine_fixes.py`；这里只放「必须走真组合根
（真 `ui.app.MainWindow` + 真 worker + 真按钮）」的：重新生成按钮、导入完整配置、
清单勾反例。口径与 `tests/test_ui_c3_integration.py` / `test_ui_migrated_c5a.py` 一致：
真控件交互 + 可观察结果，夹具只用两张 mirror 镜像表（公开仓，**绝不出现真实信号名**）。

持久化一律经 `H.isolate_settings` 指到 tmp（I-08）：绝不碰 `~/.dreg_verify*`。
"""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import ui_harness as H                                      # noqa: E402

pytest.importorskip("PySide6")

from PySide6 import QtCore                                  # noqa: E402

from dreg_verify import exports as X                        # noqa: E402
from dreg_verify.ui import app as A                         # noqa: E402
from dreg_verify.ui import contracts                        # noqa: E402
from dreg_verify.ui import export_center as EC              # noqa: E402
from dreg_verify.ui import names                            # noqa: E402
from dreg_verify.ui import persist as P                     # noqa: E402

LC = contracts.ListCol
LR = contracts.ListRole
VID = "topout"


@pytest.fixture(scope="module")
def qapp():
    return H.app()


@pytest.fixture
def win(qapp, monkeypatch, tmp_path):
    """真组合根 + 真 state + 真 worker（settings/edits 隔离到 tmp）。"""
    H.isolate_settings(monkeypatch, tmp_path)
    H.auto_dialogs(monkeypatch)
    w = A.build_window([])
    w.resize(1600, 900)
    w.show()
    yield w
    w.close()
    qapp.processEvents()


def _load(w, kind="btlp"):
    ended = []
    w.analysisEnded.connect(lambda vid, ok: ended.append((vid, ok)))
    w.load_path(H.mirror_path(kind))
    assert H.wait_for(lambda: bool(ended)), "worker 没跑完：%s" % (ended,)
    H.app().processEvents()
    return w.state.models()


def _row_of(w, name):
    panel = w.list_panel
    row = next((r for r in range(panel.proxy.rowCount())
                if panel.proxy.index(r, int(LC.NAME)).data(int(LR.NAME)) == name), None)
    assert row is not None, "清单里没有 %r" % name
    return row


def _select(w, name):
    H.click_cell(w.list_panel.view, _row_of(w, name), int(LC.NAME))
    H.app().processEvents()
    assert w.state.current_name == name
    return name


def _sv(w, name, vid=VID):
    cov = w.state.coverage(vid)
    mode, exh = cov.mode_for(name, w.state.models(vid))
    text, _b = X.render_sv(w.state.provider(vid), only=[name], mode=mode,
                           max_tests=int(cov.max_tests), exhaustive=exh,
                           edited=w.state.compute_edited(vid))
    return text


def _editables(w, kind=None, vid=VID):
    out = []
    for m in w.state.models(vid):
        an = w.state.analyze(m["name"], vid)
        if an and an.get("editable") and (kind is None or an["editable"] == kind):
            out.append(m["name"])
    return out


# ═══════════════ P-01：重新生成什么都不改 → 产物一个字节不许变 ═══════════════
@pytest.mark.contract("C-085", "C-119")
@pytest.mark.parametrize("kind", ("btlp", "wl"))
def test_p01_c085_regen_without_changes_keeps_sv_byte_identical(win, qapp, kind):
    """P-01：打开一个信号、**什么都不改**，按一次「重新生成」，`.sv` 必须逐字节不变。

    用户看到的：wl 的 mux 断言从 `==4'b1010` 变成 `==4'b0000` —— 模型里那一格的期望
    还写着「未填」、屏幕上也没有任何变化，产物里却多出一条 designer 手填期望 0。
    落盘、重启仍在。designer 照着这份 .sv 去仿真，整条用例必 FAIL，而没人知道是谁填的。

    两个根因：① `edits.mux_derive` 把 iddq 漏电态自检拍那条**工具推导**的常量期望
    当成 designer 手填，而它与同一组取值的功能拍 `mux_assign_key` 完全相同，于是被
    按键号盖到功能拍头上；② `truth/panel._do_regen` 不 `drop_edit`（v1 `_e_regen`
    第一行就是 `edits.pop`），重出之后又把引擎默认列集 `put_edit` 回去。
    """
    w = win
    _load(w, kind)
    names_ = _editables(w)
    assert names_, "mirror 上一条可编辑信号都没有"
    n_mux = 0
    for name in names_:
        _select(w, name)
        before = _sv(w, name)
        had_edit = w.state.edit_of(name, VID) is not None
        assert not had_edit, "%s 还没动过就有编辑记录（夹具不干净）" % name
        H.click(H.find(w, names.TRUTH_BTN_REGEN))          # 真按按钮
        qapp.processEvents()
        assert _sv(w, name) == before, "%s：什么都不改按一次重新生成，.sv 变了" % name
        assert w.state.edit_of(name, VID) is None, \
            "%s：什么都不改按一次重新生成，却留下了一条编辑记录" % name
        n_mux += 1 if (w.state.analyze(name, VID) or {}).get("editable") == "mux" else 0
    if kind == "wl":
        assert n_mux >= 2, "wl 上只对了 %d 条 mux 信号，覆盖太窄" % n_mux


@pytest.mark.contract("C-085", "C-119")
def test_p01_c085_regen_discards_edits_and_undo_brings_them_back(win, qapp):
    """P-01 的另一半：重新生成**确实丢弃自定义**（C-085），而 Ctrl+Z 能把它们连记录一起撤回来。

    「不留记录」不能靠「什么都不做」来实现 —— 真有编辑时那条记录必须被丢掉，
    撤销之后又必须回来（否则用户撤销完，屏幕上编辑回来了、导出的还是重出的那份）。
    """
    w = win
    _load(w)
    name = _editables(w)[0]
    _select(w, name)
    m = w.truth_panel.model
    exp_r = m.rowCount() - 1
    hand = (m.cols()[0]["auto"] ^ 1) & ((1 << (m.cols()[0]["auto_w"] or 1)) - 1)
    assert m.setData(m.index(exp_r, 0), str(hand)) is True
    qapp.processEvents()
    assert w.state.edit_of(name, VID) is not None
    edited_sv = _sv(w, name)

    H.click(H.find(w, names.TRUTH_BTN_REGEN))
    qapp.processEvents()
    assert w.state.edit_of(name, VID) is None, "重新生成没丢掉自定义（C-085）"
    assert _sv(w, name) != edited_sv, "重新生成之后产物还和手填时一样"

    m.undo_stack().undo()                                  # Ctrl+Z
    qapp.processEvents()
    assert w.state.edit_of(name, VID) is not None, "撤销重新生成，编辑记录没回来"
    assert _sv(w, name) == edited_sv, "撤销之后屏幕回来了、产物没回来"


# ═══════════════ P-03 / P-09：导入完整配置不许殃及别的范围 ═══════════════
def _write_config(w, path):
    """把当前会话导出成一份【完整配置】文件（= 导出中心那条路的 payload）。"""
    payload = EC.collect_config(w.state)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)
    return path


def _fill_one(w, vid):
    """在某个范围里挑第一条可编辑信号，手填一格期望（走 state.put_edit 的真出口）。"""
    from dreg_verify import edits as ED
    from dreg_verify.ui.truth.rows import e_inputs_from_an
    for m in w.state.models(vid):
        an = w.state.analyze(m["name"], vid)
        if not (an and an.get("editable")):
            continue
        cols = ED.cols_from_vectors(an, e_inputs_from_an(an))
        if not cols:
            continue
        cols[0]["exp"] = (cols[0]["auto"] ^ 1)
        w.state.put_edit(m["name"], {"kind": an["kind"], "src_out_name": an["src_out_name"],
                                     "name": an["name"], "renamed": an.get("renamed", False),
                                     "cols": cols, "an": an}, vid)
        return m["name"]
    return None


@pytest.mark.contract("C-192", "C-300")
def test_p03_c192_import_config_keeps_other_view_edits(win, qapp, tmp_path):
    """P-03：待在 Topout 导入一份完整配置，**别的范围**存在 edits.json 里的手填期望
    不许被静默删掉。

    用户看到的：同事在 logic 页手填了一上午，回到 Topout 导入一份配置，
    logic 页那一段就从 `~/.dreg_verify_edits.json` 里没了 —— 没有任何提示，
    他下次打开 logic 页才发现。

    根因：`reset_config_state` 对**五个**范围都调 `set_checked` → 五个范围全进
    `_owned`，而 logic/mux/dft/iddq 这时清单还没跑过、`_edits[vid]` 是空 dict，
    `persist_edits` 就把 `view_edits[vid] = {}` 交给 `write_edits_bucket`，
    空 dict 的旧语义正是「删掉这一格」。
    """
    w = win
    path = _load(w) and H.mirror_path("btlp")
    # 造一份「别的范围」的劳动成果：直接写进桶（模拟同事上一趟在 logic 页干的活）
    fake = {"d_page_sig": {"kind": "logic", "src_out_name": "d_page_sig", "name": "d_page_sig",
                           "renamed": False,
                           "cols": [{"name": "T0", "neg": False, "vals": {"a": 1}, "exp": 3,
                                     "auto": 3, "auto_w": 2, "user": False, "dft": False,
                                     "case_index": None}]}}
    assert P.write_edits_bucket(str(path), {"view_edits": {"logic": fake}})
    assert _fill_one(w, VID), "topout 范围一条可编辑信号都没有"
    before = P.load_edits_bucket(str(path))["view_edits"]
    assert set(before) >= {"logic", VID}

    cfg = _write_config(w, str(tmp_path / "cfg.json"))
    rep = EC.import_config(w.state, cfg)
    assert not rep.error, rep.error
    w.state.persist_edits()
    qapp.processEvents()

    after = P.load_edits_bucket(str(path)).get("view_edits") or {}
    assert after.get("logic") == fake, "导入配置把 logic 范围那一段抹掉/改了：%s" % sorted(after)


@pytest.mark.contract("C-243", "C-192")
def test_p09_c243_import_config_does_not_freeze_zero_checks(win, qapp, tmp_path):
    """P-09：导入完整配置之后，**还没分析过**的四个范围不许被固化成「0 勾选」。

    用户看到的：导入一次配置，下次打开 logic / mux / dft / iddq 四页一个信号都不勾，
    一导出什么都没有 —— 而他从没在那几页点过一下。

    根因同 P-03：`set_checked` 在清单还没跑过（`all_low` 为空集）时把勾选集判成
    `set()` = 一个都没勾，还跟着落了盘。
    """
    w = win
    _load(w)
    cfg = _write_config(w, str(tmp_path / "cfg.json"))
    rep = EC.import_config(w.state, cfg)
    assert not rep.error, rep.error
    w.state.persist_edits()
    qapp.processEvents()

    for vid in contracts.VIEW_IDS:
        if vid == VID or w.state.models(vid):
            continue
        assert w.state.checked(vid) is None, "%s 还没分析就被设成了 %r" % (vid, w.state.checked(vid))
    vc = P.load_edits_bucket(H.mirror_path("btlp")).get("view_checks") or {}
    frozen = [vid for vid in vc if not w.state.models(vid)]
    assert not frozen, "还没分析的范围被写进了勾选桶：%s" % frozen
