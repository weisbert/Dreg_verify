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


# ═══════════════ R2-09 / R2-10：mux 数据值那一步的撤销（C-110 / C-299）═══════════════
#: btlp 镜像上一条 mux 信号：精简 9 列 / 全面 25 列 / 穷举 40 列，**行数三档都一样**
#: —— 「换了档、列集冻结在老档上」这件事才做得出来（`_apply_shape` 要求行数不变）。
COV_SPREAD_MUX = "d_bt_lp_lna_itrim"


class _Cfg(object):
    """`ConfigSourceProto` 的最小实现（与 `tests/test_ui_truth_model.py` 同形）。"""

    def __init__(self, wb):
        self.wb = wb
        self.probe_prefixes = {}
        self.force_signals = set()
        self.logic_overrides = {}
        self.include_risky = True


class _Rean(object):
    """`TruthModel.set_reanalyzer` 的替身 = 面板那条回路（照抄 state 的做法，真 provider 算）。

    `mode/exh` 是**重分析**用的那一档 —— 与列集当初出自哪一档**可以不同**，
    「切了覆盖度档、编辑列冻结在老档上」这件事就是这么发生的（R2-09 的现场）。"""

    def __init__(self, prov, an, name, e_inputs, mode="min", maxt=256, exh=False):
        from dreg_verify import edits as ED
        self._ED = ED
        self.prov, self.name, self.e_inputs = prov, name, list(e_inputs)
        self.src_out_name, self.disp_name = an["src_out_name"].lower(), an["name"]
        self.mode, self.maxt, self.exh = mode, maxt, exh
        self.bucket = {}                      # = state.mux_data()（会话档）

    def width_of(self, base_low):
        return next(e["width"] for e in self.e_inputs
                    if (e["mux_data_base"] or "") == base_low)

    def data(self):
        ent = self.bucket.get(self.name.lower())
        return dict(ent["data"]) if ent and ent.get("data") else None

    def __call__(self, base_low, text):
        self._ED.set_mux_data_value(self.bucket, self.name.lower(), self.src_out_name,
                                    self.disp_name, base_low, self.width_of(base_low), text)
        return self.prov.analyze(self.name, self.mode, self.maxt, self.exh,
                                 mux_data=self.data())


def _frozen_mux_model(kind="btlp", name=COV_SPREAD_MUX, cols_mode=("max", False),
                      rean_mode=("min", False)):
    """(model, an, e_inputs, rean)：列集出自 `cols_mode` 档、重分析走 `rean_mode` 档。

    = 「在全面档手填了一屏期望，然后把全局档拧到精简」之后的现场：屏幕上那 25 列还冻着，
    而下一次重分析会按精简档只给 9 条 case。
    """
    from dreg_verify import edits as ED
    from dreg_verify import excel_model as M
    from dreg_verify import providers as PV
    from dreg_verify.ui.truth import TruthModel, e_inputs_from_an
    prov = PV.TopoutProvider(_Cfg(M.load_workbook(H.mirror_path(kind))))
    an = prov.analyze(name, cols_mode[0], 256, cols_mode[1])
    assert an is not None and an["editable"] == "mux", name
    ei = e_inputs_from_an(an)
    m = TruthModel()
    m.load(an, ED.cols_from_vectors(an, ei), ei)
    rean = _Rean(prov, an, name, ei, mode=rean_mode[0], exh=rean_mode[1])
    m.set_reanalyzer(rean)
    return m, an, ei, rean


def _exp_snapshot(m):
    return [(c["name"], c["exp"], bool(c["neg"])) for c in m.cols()]


@pytest.mark.contract("C-110", "C-299")
def test_r2_09_mux_data_undo_restores_the_whole_frozen_column_set(qapp):
    """R2-09：全面档手填 5 条期望共 25 列 → 换精简档（列集冻结）→ 改一次 mux 数据值
    → 屏幕 25 列变 9 列、期望只剩 2 条，**Ctrl+Z 撤不回来**（而那时已经落了盘）。

    根因：`MuxData` 只记「那一格该填什么文本」，撤销时重走一遍 `mux_resync_cols`；
    而 resync 是**有损**的 —— 老模型里有、新分析里没有的自动列直接丢掉，再走一遍变不回来。
    改法与 `Regenerate` 同：连做之前的完整列集一起存，撤销时原样装回去。

    顺带钉住 R2-09 的另一半：被丢掉的那几列**要点名**（此前一声不吭）。
    """
    m, _an, ei, _rean = _frozen_mux_model()
    n0 = m.columnCount()
    assert n0 >= 20, "冻结下来的列太少（%d），测不到「25 → 9」" % n0
    exp_r = m.rowCount() - 1
    for c in range(0, min(5 * 2, n0), 2):                # 手填 5 条期望
        assert m.setData(m.index(exp_r, c), str(m.cols()[c]["auto"] ^ 1)) is True
    before = _exp_snapshot(m)
    n_filled0 = sum(1 for _n, e, _g in before if e is not None)
    assert n_filled0 >= 5

    said = []
    m.pasteReport.connect(said.append)
    base = next(e["mux_data_base"] for e in ei if e["mux_data_base"])
    assert m.set_mux_data_value(base, "5") is True
    assert m.columnCount() < n0, "这一步没缩列，R2-09 的现场没造出来"
    assert any("Ctrl+Z" in s for s in said), "被丢掉的列一声不吭：%s" % said

    m.undo_stack().undo()                                 # Ctrl+Z
    assert m.columnCount() == n0, "撤销没把冻结的列集还回来（%d → %d）" % (n0, m.columnCount())
    assert _exp_snapshot(m) == before, "撤销回来了，可手填的期望不是原来那份"

    m.undo_stack().redo()                                 # 重做：回到缩过的那份
    assert m.columnCount() < n0
    m.undo_stack().undo()
    assert _exp_snapshot(m) == before, "撤销 → 重做 → 再撤销之后对不上了"


@pytest.mark.contract("C-299")
def test_r2_10_addcols_undo_takes_back_by_identity(qapp):
    """R2-10：列集被 `MuxData` 缩掉之后，**更早的**加列 / 复制列撤销静默变成空操作。

    根因：`AddCols.undo` 按下标 `range(at, at+n)` 收，越界被 `_take_cols` 过滤掉 →
    返回空 → `self._cols` 变成 `[]`，这一步撤销什么也没干、重做也没得插了
    （fuzz seed=5 N=194 抓到的就是这条）。改成按**对象身份**收。
    """
    m, an, ei, _rean = _frozen_mux_model()
    n0 = m.columnCount()
    made = m.append_test_column(1, src_idx=0)             # 复制一列 = 一步 AddCols
    assert made and m.columnCount() == n0 + 1
    user_name = next(c["name"] for c in m.cols() if c["user"])

    # 列集被换成「短得多的一份」，但那条手编列**还是同一个对象**
    # （= `edits.mux_resync_cols` 缩完列之后的样子：自动列全是新造的，手编列原样带过去）
    inner = m._cols                                       # 故意用内部那份：身份才是这条的主角
    short = list(inner[:3]) + [c for c in inner if c["user"]]
    m._apply_shape(an, ei, short)
    assert m.columnCount() == 4

    m.undo_stack().undo()                                 # 撤那一步 AddCols
    assert not [c for c in m.cols() if c["user"]], "撤销没把手编列收回去（静默空操作）"
    assert m.columnCount() == 3
    m.undo_stack().redo()                                 # 重做还得插得回来
    back = [c for c in m.cols() if c["user"]]
    assert len(back) == 1 and back[0]["name"] == user_name, "重做插不回来了"


@pytest.mark.contract("C-299")
def test_r2_09_r2_10_full_undo_walk_back_to_the_start(qapp):
    """R2-09 + R2-10 合起来：一串动作做完，**整条撤销回去**必须回到出发点；
    再整条重做回来必须回到终点（`bisect_undo.py` 的口径）。"""
    m, _an, ei, _rean = _frozen_mux_model()
    start = _exp_snapshot(m)
    exp_r = m.rowCount() - 1
    base = next(e["mux_data_base"] for e in ei if e["mux_data_base"])

    m.setData(m.index(exp_r, 0), str(m.cols()[0]["auto"] ^ 1))
    m.append_test_column(1, src_idx=0)
    m.add_negatives([0])
    m.set_mux_data_value(base, "5")
    m.setData(m.index(exp_r, 1), str(m.cols()[1]["auto"] ^ 3))
    end = _exp_snapshot(m)
    n_steps = m.undo_stack().count()
    assert n_steps == 5, "五个动作该是五步撤销，实际 %d" % n_steps

    for _ in range(n_steps):
        m.undo_stack().undo()
    assert _exp_snapshot(m) == start, "整条撤销回去，没回到出发点"
    for _ in range(n_steps):
        m.undo_stack().redo()
    assert _exp_snapshot(m) == end, "整条重做回来，没回到终点"


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
