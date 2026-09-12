# -*- coding: utf-8 -*-
"""test_d1_engine_fixes.py —— Phase D 对抗 review R2 抓到的**数据正确性与持久化**缺口（F1）。

R2 用随机编辑序列模糊测试 + 文件兼容矩阵证明了两件事：
  · **mux 路四方不一致** —— 屏幕 / 会话 / edits 文件 / .sv 产物各说各话（logic 路零破口）；
  · **`view_edits` 段内整段替换** 会静默抹掉用户的活（恢复不出来的信号、两个窗口交替存盘）。

本文件一条测试对一条 R2-nn，名字里带编号；每条的 docstring 写清「用户看到的是什么」。
不起窗（`H.app()` 只为拿进程级 QApplication，`WorkbenchState` 要 QtCore 的信号）。
夹具一律 mirror 镜像表（公开仓，**绝不出现真实信号名**）。

⚠ 持久化实验一律把 `persist.SETTINGS_PATH / EDITS_PATH` patch 到 tmp（I-08）：
绝不碰 `~/.dreg_verify*`。
"""

import json
import os
import random
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import ui_harness as H                                      # noqa: E402

pytest.importorskip("PySide6")

from dreg_verify import edits as ED                         # noqa: E402
from dreg_verify import generator as G                      # noqa: E402
from dreg_verify import session                             # noqa: E402
from dreg_verify.ui import persist as P                     # noqa: E402
from dreg_verify.ui import state as ST                      # noqa: E402
from dreg_verify.ui.truth.rows import e_inputs_from_an      # noqa: E402

VID = "topout"


# ═══════════════════════ 夹具与小工具 ═══════════════════════
@pytest.fixture(scope="module")
def qapp():
    return H.app()


@pytest.fixture
def iso(monkeypatch, tmp_path):
    """两份持久化文件指到临时目录（I-08：绝不写用户真机那份）。"""
    monkeypatch.setattr(P, "SETTINGS_PATH", str(tmp_path / "gui_settings.json"))
    monkeypatch.setattr(P, "EDITS_PATH", str(tmp_path / "edits.json"))
    return tmp_path


@pytest.fixture(scope="module")
def btlp():
    return H.mirror_path("btlp")


@pytest.fixture(scope="module")
def wl():
    return H.mirror_path("wl")


def loaded(path, vid=VID):
    """载表 + 灌骨架清单（= app 在 worker 起跑前做的那两步）。"""
    st = ST.WorkbenchState()
    assert st.load(path)
    st.set_models(vid, st.provider(vid).skeleton_models(), partial=True)
    return st


def mux_signals(st, vid=VID):
    """这张表在当前档下**可编辑的 mux 信号**（名字来自镜像，公开仓可写进断言）。"""
    out = []
    for m in st.models(vid):
        an = st.analyze(m["name"], vid)
        if an and an.get("editable") == "mux":
            out.append(m["name"])
    return out


def first_editable(st, kind, vid=VID):
    for m in st.models(vid):
        an = st.analyze(m["name"], vid)
        if an and an.get("editable") == kind:
            return m["name"]
    return None


def rec_of(st, name, cols, vid=VID):
    an = st.analyze(name, vid)
    return {"kind": an["kind"], "src_out_name": an["src_out_name"], "name": an["name"],
            "renamed": an.get("renamed", False), "cols": cols, "an": an}


def sv_of(st, name, edited, vid=VID):
    """这一份 `edited` 下该信号的 .sv 正文（逐字节比的就是它）。"""
    cov = st.coverage(vid)
    mode, exh = cov.mode_for(name, st.models(vid))
    got = st.provider(vid).render_sv([name], mode, int(cov.max_tests), exh, edited)
    return got[0] if isinstance(got, tuple) else got


def reload_edits(st, rec_map, vid=VID):
    """本会话的编辑 →（序列化 → JSON 往返 → 恢复）→ 重开后的编辑。"""
    ser = json.loads(json.dumps(ED.serialize_view_edits(rec_map)))
    return ED.restore_view_edits(ser, st.models(vid), lambda real: st.analyze(real, vid))


# ═══════════════ R2-02：mux 存盘键集（C-131 / C-238）═══════════════
@pytest.mark.contract("C-131", "C-238")
def test_r2_02_mux_reload_sv_is_byte_identical(qapp, btlp, wl):
    """R2-02：mux 只改一格期望 → 关工具 → 重开，.sv 必须与关之前**逐字节相同**。

    用户看到的：手填的期望重开后屏幕上还在，导出的 .sv 却从 104 行缩到 71 行 ——
    整表的自动用例全被当成「用户删过」丢掉了，而界面上没有任何一处说得出来。

    根因：屏幕那份 `vals` 的键集 = 真值表输入行（`expansion["used_vars"]` + iddq 门那一行），
    向量自己的 `vec.assignments` 是另一个键集；生成器认的是后者
    （`generator.mux_assign_key(vec.assignments)`），按前者重建的向量一条也对不上号。
    """
    n_sig = 0
    for path in (btlp, wl):
        st = loaded(path)
        names = mux_signals(st)
        assert names, "这张镜像没有 mux 信号，测不到 R2-02：%s" % path
        for nm in names:
            an = st.analyze(nm)
            cols = ED.cols_from_vectors(an, e_inputs_from_an(an))
            if not cols:
                continue
            n_sig += 1
            cols[0]["exp"] = cols[0]["auto"] ^ 1            # 只改一格期望
            rec = {nm.lower(): rec_of(st, nm, cols)}
            live = ED.compute_edited(rec, {})
            relo = ED.compute_edited(reload_edits(st, rec), {})

            lm, rm = live[nm.lower()]["mux"], relo[nm.lower()]["mux"]
            assert sorted(lm["dropped"]) == sorted(rm["dropped"]), \
                "%s：重开后 %d 条自动用例被当成用户删过" % (nm, len(rm["dropped"]))
            assert lm["expected"] == rm["expected"], "%s：手填期望的键对不上号" % nm
            assert sv_of(st, nm, live) == sv_of(st, nm, relo), "%s：重开后 .sv 变了" % nm
    assert n_sig >= 8, "只对了 %d 条 mux 信号，覆盖太窄" % n_sig


#: sc02 的 18 种单动作 —— 每一种都要「做完存盘再打开，.sv 一个字节不差」
_SINGLE_OPS = (
    "exp_first", "exp_last", "exp_all", "clear_exp",
    "neg_first", "neg_all", "del_neg",
    "copy_first", "copy_last", "copy_two",
    "del_first", "del_last", "del_two", "clear_all",
    "rename_user", "rename_user_cn", "fill_expected", "noop",
)


def _apply_op(an, cols, op):
    """在列模型上做一个单动作（纯 `edits` 层，与 v1/v2 两套门面同一条路）。"""
    if op == "exp_first":
        cols[0]["exp"] = cols[0]["auto"] ^ 1
    elif op == "exp_last":
        cols[-1]["exp"] = cols[-1]["auto"] ^ 3
    elif op == "exp_all":
        for c in cols:
            c["exp"] = c["auto"]
    elif op == "clear_exp":
        for c in cols:
            c["exp"] = None
    elif op == "neg_first":
        made, _s = ED.add_negatives(cols, [0], False)
        cols.extend(made)
    elif op == "neg_all":
        made, _s = ED.add_negatives(cols, None, True)
        cols.extend(made)
    elif op == "del_neg":
        made, _s = ED.add_negatives(cols, [0], False)
        cols.extend(made)
        cols[:], _n = ED.del_negatives(cols)
    elif op == "copy_first":
        cols.extend(ED.copy_cols(cols, [0]))
    elif op == "copy_last":
        cols.extend(ED.copy_cols(cols, None))
    elif op == "copy_two":
        cols.extend(ED.copy_cols(cols, [0, min(1, len(cols) - 1)]))
    elif op == "del_first":
        cols[:] = ED.del_cols(cols, [0])
    elif op == "del_last":
        cols[:] = ED.del_cols(cols, [len(cols) - 1])
    elif op == "del_two":
        cols[:] = ED.del_cols(cols, [0, min(1, len(cols) - 1)])
    elif op == "clear_all":
        cols[:] = []
    elif op in ("rename_user", "rename_user_cn"):
        cols.extend(ED.copy_cols(cols, [0]))
        ok, _info = ED.rename_col(cols, len(cols) - 1,
                                  "MY_CASE" if op == "rename_user" else "MY_CASE_2")
        assert ok, _info
    elif op == "fill_expected":
        ED.fill_expected(cols)
    elif op == "noop":
        pass
    else:                                                   # pragma: no cover
        raise AssertionError(op)
    return cols


@pytest.mark.contract("C-131", "C-238")
@pytest.mark.parametrize("op", _SINGLE_OPS)
def test_r2_02_single_op_survives_reload(qapp, wl, op):
    """R2-02（sc02）：**每一种**单动作做完，存盘→重开的 .sv 都要逐字节相同。

    R2 实测 18 种里 17 种红 —— 不是某个动作写错了，是存盘格式丢了向量的取值。
    """
    st = loaded(wl)
    names = mux_signals(st)
    assert names
    for nm in names:
        an = st.analyze(nm)
        cols = _apply_op(an, ED.cols_from_vectors(an, e_inputs_from_an(an)), op)
        rec = {nm.lower(): rec_of(st, nm, cols)}
        live = ED.compute_edited(rec, {})
        relo = ED.compute_edited(reload_edits(st, rec), {})
        assert sv_of(st, nm, live) == sv_of(st, nm, relo), "%s / %s：重开后 .sv 变了" % (nm, op)


@pytest.mark.contract("C-234")
def test_r2_02_old_edits_file_without_assign_still_loads(qapp, wl):
    """R2-02 兼容：v1 时代 / v2 早期写的 edits 文件**没有** `assign` 段，照样读得进来。

    schema 只准 additive —— 同事回退旧版本、或拿着上周的 `~/.dreg_verify_edits.json`
    打开新版本，都不许报错、不许整条丢。
    """
    st = loaded(wl)
    nm = mux_signals(st)[0]
    an = st.analyze(nm)
    cols = ED.cols_from_vectors(an, e_inputs_from_an(an))
    cols[0]["exp"] = cols[0]["auto"] ^ 1
    ser = json.loads(json.dumps(ED.serialize_view_edits({nm.lower(): rec_of(st, nm, cols)})))

    assert all("assign" in c for c in ser[nm.lower()]["cols"]), "新文件该带 assign 段"
    old = json.loads(json.dumps(ser))                        # 造一份「旧文件」：把 assign 摘掉
    for c in old[nm.lower()]["cols"]:
        c.pop("assign")
    back = ED.restore_view_edits(old, st.models(VID), lambda real: st.analyze(real, VID))
    assert len(back[nm.lower()]["cols"]) == len(cols), "旧文件被整条丢掉了"
    assert back[nm.lower()]["cols"][0]["exp"] == cols[0]["exp"], "旧文件里的手填期望没了"
    # 坏掉的 assign（手改过的文件）也只退回 vals，不许把整列丢掉
    bad = json.loads(json.dumps(ser))
    bad[nm.lower()]["cols"][0]["assign"] = {"x": "不是数"}
    back2 = ED.restore_view_edits(bad, st.models(VID), lambda real: st.analyze(real, VID))
    assert len(back2[nm.lower()]["cols"]) == len(cols)


# ═══════════════ R2-01：mux 数据值手填要落盘（C-110 / C-112）═══════════════
def _data_row(an):
    """这个 mux 信号的第一条【数据角色】输入行（可手填的那种）。"""
    for e in e_inputs_from_an(an):
        if e.get("mux_data_base"):
            return e
    return None


def _fill_mux_data(st, name, text, vid=VID):
    """走 `edits.set_mux_data_value`（= 面板 reanalyzer 的唯一写入口）填一格数据值。"""
    an = st.analyze(name, vid)
    e = _data_row(an)
    assert e is not None, "%s 没有可手填的数据行" % name
    ED.set_mux_data_value(st.mux_data(vid), name.lower(), str(an["src_out_name"]).lower(),
                          str(an["name"]), e["mux_data_base"], int(e["width"]), text)
    st.mux_data_touched(name, vid)
    return e


@pytest.mark.contract("C-110", "C-112")
def test_r2_01_mux_data_survives_reopen(qapp, iso, wl):
    """R2-01：mux 数据值手填后关工具重开，`.sv` 里这一块不许消失。

    用户看到的：手填的数据值重开后屏幕上**还在**（它进了 `view_edits` 的 `vals`），
    edits 文件里也还在，可导出的 .sv 里这个信号**整块没了** —— 生成器拿不到 `mux_data`，
    自动互异分配与冻结下来的列对不上号，整条信号被判成「向量全被丢弃」。
    屏幕说有、产物里没有，而且没有任何一处报错。
    """
    st = loaded(wl)
    nm = next((n for n in mux_signals(st) if _data_row(st.analyze(n)) is not None), None)
    assert nm, "这张镜像没有可手填数据值的 mux 信号"

    _fill_mux_data(st, nm, "5")
    an2 = st.analyze(nm)
    st.put_edit(nm, rec_of(st, nm, ED.cols_from_vectors(an2, e_inputs_from_an(an2))))
    live_sv = sv_of(st, nm, st.compute_edited())
    assert nm in live_sv, "手填数据值之后本会话的 .sv 里就没有这个信号了（夹具挑错了）"

    # 重开：新建一个 WorkbenchState 走同一条载表 → 恢复的路
    st2 = loaded(wl)
    relo_sv = sv_of(st2, nm, st2.compute_edited())
    assert nm in relo_sv, "重开后 .sv 里这个信号整块消失了"
    assert live_sv == relo_sv, "重开前后 .sv 不一样"
    assert st2.mux_data(VID).get(nm.lower(), {}).get("data"), "重开后 mux 数据值没恢复"
    assert st2.mux_data(VID)[nm.lower()]["src"], "恢复出来的记录没有 src_out_name"
    # 盘上确实存下来了（段名 + 形状 = legacy 的 mux_data 多一层 view_id）
    assert P.load_edits_bucket(str(wl))["view_mux_data"][VID][nm.lower()], "mux 数据值没落盘"


@pytest.mark.contract("C-110")
def test_r2_01_mux_data_only_signal_needs_no_edit_record(qapp, iso, wl):
    """R2-01：**只**手填了数据值、真值表一格没改的信号，重开后也要照样进 .sv。

    这条走的是 `compute_edited` 的 data-only 分支（`edits` 里根本没有这个信号），
    恢复时得靠 `_bind_mux_data` 从 an 捞回 `src_out_name`，否则那条记录喂不到任何信号上。
    """
    st = loaded(wl)
    nm = next((n for n in mux_signals(st) if _data_row(st.analyze(n)) is not None), None)
    _fill_mux_data(st, nm, "7")
    assert not st.edits(VID), "这一趟不该有任何真值表编辑（那就走不到 data-only 分支）"
    live_sv = sv_of(st, nm, st.compute_edited())

    st2 = loaded(wl)
    ent = st2.mux_data(VID).get(nm.lower())
    assert ent and ent["data"], "重开后 data-only 的手填数据值没恢复"
    assert ent["src"] == str(st2.analyze(nm)["src_out_name"]).lower()
    assert sv_of(st2, nm, st2.compute_edited()) == live_sv


@pytest.mark.contract("C-235", "C-300")
def test_r2_01_mux_data_segment_does_not_touch_legacy(qapp, iso, wl):
    """R2-01：新段 `view_mux_data` 走的还是桶合并 —— legacy 九段与别的 view_id 一个字节不动。"""
    legacy = {"edits": {"d_old": []}, "mux_data": {"d_old_mux": {"d_old_base": 3}},
              "signals_checked": ["d_old"]}
    P.write_edits_bucket(str(wl), {})
    allb = P.load_edits_all()
    allb.setdefault(str(wl), {}).update(legacy)
    allb[str(wl)].setdefault("view_edits", {})["logic"] = {"d_x": {"kind": "logic",
                                                                  "src_out_name": "d_x",
                                                                  "name": "d_x",
                                                                  "renamed": False, "cols": []}}
    P.save_edits_all(allb)
    frozen = json.dumps({k: allb[str(wl)][k] for k in legacy}, sort_keys=True, ensure_ascii=False)

    st = loaded(wl)
    nm = next((n for n in mux_signals(st) if _data_row(st.analyze(n)) is not None), None)
    _fill_mux_data(st, nm, "6")
    now = P.load_edits_bucket(str(wl))
    assert json.dumps({k: now[k] for k in legacy}, sort_keys=True, ensure_ascii=False) == frozen
    assert now["view_edits"]["logic"]["d_x"]["name"] == "d_x", "别的 view_id 子桶被动了"
    assert now["view_mux_data"][VID][nm.lower()]


# ═══════════════ P-01：默认列集不许在产物里变出一条 designer 手填期望 ═══════════════
@pytest.mark.contract("C-085", "C-119")
def test_p01_default_cols_render_the_same_sv_as_no_edits(qapp, btlp, wl):
    """P-01（引擎侧根因）：把**引擎默认列集**原样当成一份编辑喂回去，`.sv` 必须逐字节不变。

    这是「什么都不改按一次重新生成，产物却变了」的根，也是整个编辑层的地基：
    `cols_from_vectors` 出来的列集经 `compute_edited` 再渲染，就该等于「根本没有编辑」。

    真实破口：iddq 漏电态自检拍那条**工具推导**的常量期望（designer_expected=0）被
    `mux_derive` 当成 designer 手填收进 `expected`；而它的门在 `extra_forces` 里、
    **不在 assignments 里**，于是它与同一组取值的功能拍 `mux_assign_key` 完全相同 ——
    生成器按键号把 0 盖到功能拍头上（wl 上 `==4'b1010` 变 `==4'b0000`）。
    """
    n_sig, n_gated = 0, 0
    for path in (btlp, wl):
        st = loaded(path)
        for m in st.models(VID):
            nm = m["name"]
            an = st.analyze(nm, VID)
            if not (an and an.get("editable")):
                continue
            cols = ED.cols_from_vectors(an, e_inputs_from_an(an))
            if not cols:
                continue
            n_sig += 1
            n_gated += 1 if an.get("dft_gate") else 0
            plain = sv_of(st, nm, {})                        # 完全没有编辑
            asis = sv_of(st, nm, ED.compute_edited({nm.lower(): rec_of(st, nm, cols)}, {}))
            assert plain == asis, "%s：默认列集喂回去，.sv 就变了" % nm
    assert n_sig >= 20, "只对了 %d 条信号，覆盖太窄" % n_sig
    assert n_gated >= 2, "一条带 iddq 门的信号都没对上，这条测不到根因"


# ═══════════════ R2-04 / R2-06：view_edits 逐信号合并（I-05 / C-300 / C-241）═══════════════
LOGIC_SIG = "d_logic_bt_lp_rx_en"          # btlp 镜像里一个 logic 根、可编辑


def _edit_one(st, name, exp=None, vid=VID):
    """在某个信号上改一格期望（走 `put_edit` = 真值表面板的出口）。"""
    an = st.analyze(name, vid)
    cols = ED.cols_from_vectors(an, e_inputs_from_an(an))
    cols[0]["exp"] = (cols[0]["auto"] ^ 1) if exp is None else exp
    st.put_edit(name, rec_of(st, name, cols, vid), vid)
    return cols


def _two_editables(st, vid=VID):
    out = []
    for m in st.models(vid):
        an = st.analyze(m["name"], vid)
        if an and an.get("editable"):
            out.append(m["name"])
        if len(out) == 2:
            break
    return out


@pytest.mark.contract("C-300", "C-241")
def test_r2_04_unrestorable_signal_is_not_wiped_and_is_named(qapp, iso, btlp, monkeypatch):
    """R2-04：某信号这一趟恢复不出来（引擎瞬时异常）→ 状态栏**点名** +
    文件里那条手填期望**原样留着**，接下来的普通编辑不许把它抹掉。

    用户看到的：分析偶发抛了一次，状态栏只说「已恢复 N 个」；他改了另一个信号一格，
    那个没恢复出来的信号存了一上午的手填期望就永久没了 —— 下次导出才发现。
    """
    st = loaded(btlp)
    a, b = _two_editables(st)
    _edit_one(st, a, 3)
    _edit_one(st, b, 5)
    before = P.load_edits_bucket(str(btlp))["view_edits"][VID]
    assert set(before) == {a.lower(), b.lower()}

    # 重开，但 a 的分析这一趟必炸（瞬时异常）
    st2 = ST.WorkbenchState()
    said = []
    st2.statusMessage.connect(said.append)
    assert st2.load(str(btlp))
    real_analyze = st2.analyze
    boom = {"on": True}

    def flaky(name, view_id=None, want_graph=False):
        if boom["on"] and str(name).lower() == a.lower():
            raise RuntimeError("boom")
        return real_analyze(name, view_id, want_graph)

    # ⚠ 不能用 `monkeypatch.undo()` 复原 —— 那会把 `iso` 夹具对 EDITS_PATH 的隔离一起撤掉，
    #   测试就去读用户真机那份 `~/.dreg_verify_edits.json` 了。
    monkeypatch.setattr(st2, "analyze", flaky)
    st2.set_models(VID, st2.provider(VID).skeleton_models(), partial=True)

    assert a.lower() not in st2.edits(VID), "夹具没生效：a 居然恢复出来了"
    assert any(a in s for s in said), "恢复不出来的信号没点名：%s" % said

    # 现在改 b 一格 —— a 那条必须原样躺在文件里
    boom["on"] = False
    _edit_one(st2, b, 9)
    now = P.load_edits_bucket(str(btlp))["view_edits"][VID]
    assert a.lower() in now, "恢复不出来的信号被下一次普通编辑抹掉了"
    assert now[a.lower()] == before[a.lower()], "它还被改了内容"
    assert now[b.lower()]["cols"][0]["exp"] == 9


@pytest.mark.contract("C-300")
def test_r2_06_two_windows_do_not_overwrite_each_other(qapp, iso, btlp):
    """R2-06：同一张表开两个窗口交替存盘，各改各的信号 → 两个人的活都要在。

    用户看到的：同事开着同一张表，你存一次，他的手填期望没了；他存一次，你的没了。
    零提示。两轮交替（第二轮与第一轮方向相反：这次先 B 后 A）。
    """
    A, B = loaded(btlp), loaded(btlp)
    a, b = _two_editables(A)

    _edit_one(A, a, 3)                                  # 第一轮：A 先写、B 后写
    _edit_one(B, b, 5)
    seg = P.load_edits_bucket(str(btlp))["view_edits"][VID]
    assert seg[a.lower()]["cols"][0]["exp"] == 3, "B 存盘把 A 的活盖掉了"
    assert seg[b.lower()]["cols"][0]["exp"] == 5

    _edit_one(B, b, 6)                                  # 第二轮：反过来，B 先写、A 后写
    _edit_one(A, a, 4)
    seg = P.load_edits_bucket(str(btlp))["view_edits"][VID]
    assert seg[a.lower()]["cols"][0]["exp"] == 4
    assert seg[b.lower()]["cols"][0]["exp"] == 6, "A 存盘把 B 的活盖掉了"


@pytest.mark.contract("C-300")
def test_r2_04_drop_edit_really_deletes_from_file(qapp, iso, btlp):
    """R2-04：逐信号合并之后，`drop_edit` 仍要把文件里那一条**真的删掉**
    （否则「回到引擎默认真值表」下次开工具又变回来了）。"""
    st = loaded(btlp)
    a, b = _two_editables(st)
    _edit_one(st, a, 3)
    _edit_one(st, b, 5)
    st.drop_edit(a)
    seg = P.load_edits_bucket(str(btlp))["view_edits"][VID]
    assert a.lower() not in seg, "drop_edit 没删掉文件里那条"
    assert seg[b.lower()]["cols"][0]["exp"] == 5, "顺手把别人删了"
    st.drop_edit(b)
    assert "view_edits" not in P.load_edits_bucket(str(btlp)), "一条不剩时该连段一起清掉"


@pytest.mark.contract("C-300")
def test_r2_04_mux_data_is_merged_per_signal_too(qapp, iso, wl):
    """R2-04：`view_mux_data` 同口径 —— 另一个窗口填的数据值不许被盖掉；清空那一格要真删。"""
    A, B = loaded(wl), loaded(wl)
    muxes = [n for n in mux_signals(A) if _data_row(A.analyze(n)) is not None]
    assert len(muxes) >= 2, "这张镜像 mux 信号不够两条"
    a, b = muxes[0], muxes[1]
    _fill_mux_data(A, a, "5")
    _fill_mux_data(B, b, "6")
    seg = P.load_edits_bucket(str(wl))["view_mux_data"][VID]
    assert a.lower() in seg and b.lower() in seg, "两个窗口互相盖掉了：%s" % sorted(seg)

    _fill_mux_data(A, a, "")                            # 清空 = 恢复自动分配
    seg = P.load_edits_bucket(str(wl))["view_mux_data"][VID]
    assert a.lower() not in seg, "清空那一格没从文件里删掉"
    assert b.lower() in seg


# ═══════════════ R2-11：编辑过的信号换覆盖度档（C-085 / C-148 / C-153）═══════════════
COV_LABELS = ("精简", "全面", "穷举")


def _sv_block_names(st, name, vid=VID):
    """这一份 `edited` 下该信号在 .sv 里的**测试块标号**（屏幕上那些列的对照物）。"""
    import re
    from dreg_verify import exports as X
    cov = st.coverage(vid)
    mode, exh = cov.mode_for(name, st.models(vid))
    text, build = X.render_sv(st.provider(vid), only=[name], mode=mode,
                              max_tests=int(cov.max_tests), exhaustive=exh,
                              edited=st.compute_edited(vid))
    aid = next((b[1]["assert_id"] for b in build["blocks"]
                if (b[1].get("topout_name") or "").lower() == name.lower()), None)
    if aid is None:
        return []
    return re.findall(r"assert_%s_(\S+):" % re.escape(aid), text)


def _set_cov(st, label, vid=VID):
    st.coverage(vid).persist_global_label(label)
    st.coverage_touched(vid)


@pytest.mark.contract("C-085", "C-148", "C-153")
@pytest.mark.parametrize("edit_at", COV_LABELS)
def test_r2_11_edited_signal_screen_matches_sv_across_all_coverages(qapp, iso, btlp, wl, edit_at):
    """R2-11：mux + 任一编辑 + 换覆盖度档 → **屏幕上有几列，.sv 里就得有几块**。

    用户看到的：在全面档手填了期望（屏幕 25 列），把全局档拧回精简，导出的 .sv 里
    只有 9 块；拧到穷举又变成 40 块 —— 屏幕一列没动，产物却跟着档走，两边都不吭声。
    designer 拿 .sv 去仿真，手填的那些用例根本没生成。

    根因：`compute_edited` 用的是编辑记录里**冻结的那份 an**（写记录那一刻的档），
    而生成器按**现在这一档**出 case 清单，`mux_derive` 拿冻结的 an 算 `dropped`
    自然算不出该丢哪几条。修法（R2-11 选的路 = 变体 a）：`state.compute_edited` 逐条把
    `an` 换成现在这一档的分析结果；`edits.mux_derive` 再把「屏幕上有、生成期那一档没有」
    的列当手编列补出去。**列集一列不动**（C-153：手填过的信号拧全局档不许被冲掉）。

    logic 路一并验（它本来就一致，这条是防回归）。
    """
    n_mux = 0
    for path in (btlp, wl):
        st = loaded(path)
        cases = []
        for m in st.models(VID):
            an = st.analyze(m["name"], VID)
            if an and an.get("editable"):
                cases.append((m["name"], an["editable"]))
        assert cases
        for nm, ek in cases[:6]:
            _set_cov(st, edit_at)
            an = st.analyze(nm, VID)
            cols = ED.cols_from_vectors(an, e_inputs_from_an(an))
            if not cols:
                continue
            n_mux += 1 if ek == "mux" else 0
            cols[0]["exp"] = cols[0]["auto"] ^ 1           # 一处编辑（= 冻结这份列集）
            st.put_edit(nm, rec_of(st, nm, cols), VID)
            screen = [c["name"] for c in cols]
            for lab in COV_LABELS:                        # 四档：编辑档 + 三档来回拧
                _set_cov(st, lab)
                blocks = _sv_block_names(st, nm)
                assert len(blocks) == len(screen), \
                    "%s（%s，%s 档编辑）看 %s 档：屏幕 %d 列 / .sv %d 块" % (
                        nm, ek, edit_at, lab, len(screen), len(blocks))
            st.drop_edit(nm, VID)
    assert n_mux >= 4, "只对了 %d 条 mux 信号，覆盖太窄" % n_mux


@pytest.mark.contract("C-154")
def test_r2_11_c154_neg_only_follows_coverage_but_handfilled_stays_frozen(qapp, wl):
    """R2-11 / C-154 的分界：**只加过反例**的信号正向随档重算；**手填过期望**的冻结不动。

    两边各走一遍才说得清这条分界 —— 只验一半的话，要么是「改档把用户的活冲掉了」，
    要么是「勾个反例就把这个信号永远钉死在那一档」。
    """
    st = loaded(wl)
    nm = next((m["name"] for m in st.models(VID)
               if (st.analyze(m["name"], VID) or {}).get("editable")), None)
    _set_cov(st, "精简")
    an = st.analyze(nm, VID)
    e_in = e_inputs_from_an(an)
    lo = ED.cols_from_vectors(an, e_in)

    # ① 只加过反例 → 正向跟着档走
    made, _s = ED.add_negatives(lo, [0], False)
    neg_cols = list(lo) + made
    assert ED.neg_only_cols(an, neg_cols) is True
    _set_cov(st, "穷举")
    hi_an = st.analyze(nm, VID)
    hi_ei = e_inputs_from_an(hi_an)
    fresh = ED.cov_resync_cols(hi_an, hi_ei, an, neg_cols)
    assert fresh is not None, "只加过反例的信号没跟着档重算（C-154）"
    n_pos = sum(1 for c in fresh if not c["neg"])
    assert n_pos == len(ED.cols_from_vectors(hi_an, hi_ei)), "正向没按新档重算"
    assert sum(1 for c in fresh if c["neg"]) >= 1, "重算后反例没补回来"

    # ② 手填过期望 → 冻结不动（C-153）
    hand = list(lo)
    hand[0] = dict(hand[0], exp=(hand[0]["auto"] ^ 1))
    assert ED.neg_only_cols(an, hand) is False
    assert ED.cov_resync_cols(hi_an, hi_ei, an, hand) is None, "手填过的信号被改档冲掉了"
    # ③ 加过列 / 删过列同样算「动过正向」
    assert ED.cov_resync_cols(hi_an, hi_ei, an, list(lo) + ED.copy_cols(lo, [0])) is None
    assert ED.cov_resync_cols(hi_an, hi_ei, an, neg_cols[1:]) is None


# ═══════════════ R2-05：同一个文件的不同路径写法（I-04 / C-233 / C-236）═══════════════
def _spellings(path):
    """同一个文件的几种写法：原样 / 正斜杠 / 盘符小写 / 绕一圈的相对写法。"""
    p = str(path)
    out = [p, p.replace("\\", "/")]
    if len(p) > 1 and p[1] == ":":
        out.append(p[0].swapcase() + p[1:])
    d, b = os.path.split(p)
    out.append(os.path.join(d, ".", b))
    return [x for i, x in enumerate(out) if x not in out[:i]]


@pytest.mark.contract("C-236", "C-233")
def test_r2_05_path_spellings_share_one_bucket(qapp, iso, btlp):
    """R2-05：同一张表用不同的路径写法打开，手填期望必须是**同一份**。

    用户看到的：从「最近打开」进来手填期望都在；自己在路径框敲一遍同一个文件（正斜杠 /
    盘符大小写不同）进来就「全没了」—— 其实是被分到了另一个桶里，而界面上什么都不说。
    """
    ways = _spellings(btlp)
    assert len(ways) >= 3, "造不出三种写法，测不到 R2-05：%s" % ways

    st = loaded(ways[0])
    a, b = _two_editables(st)
    _edit_one(st, a, 3)

    for way in ways[1:]:                                # 换个写法重开，同一份活要在
        st2 = loaded(way)
        ed = st2.edit_of(a)
        assert ed is not None, "换成 %r 打开，手填期望不见了" % way
        assert ed["cols"][0]["exp"] == 3
        _edit_one(st2, b, 5)                            # 在新写法下再改一笔
        assert len(session.path_bucket_keys(P.load_edits_all(), way)) == 1, \
            "同一个文件又分出了第二个桶：%s" % sorted(P.load_edits_all())
        st3 = loaded(ways[0])                           # 回到原写法：两笔都在
        assert st3.edit_of(a)["cols"][0]["exp"] == 3
        assert st3.edit_of(b)["cols"][0]["exp"] == 5
        st3.drop_edit(b)


@pytest.mark.contract("C-233")
def test_r2_05_two_old_buckets_are_merged_by_entry_count(qapp, iso, btlp):
    """R2-05：盘上**已经**分成两个桶（老文件）时，读的时候合成一份。

    合并规则（报告里写死的那条）：dict 逐键往下合；同一个键两边都有 → **条目多的那份赢**；
    一样多 → 文件里靠后的那份赢（它是后写进去的）。
    """
    p1, p2 = str(btlp), str(btlp).replace("\\", "/")
    one = {"view_edits": {VID: {"sig_a": {"kind": "logic", "src_out_name": "sig_a",
                                          "name": "sig_a", "renamed": False,
                                          "cols": [{"name": "T0"}]}}},
           "signals_checked": ["sig_a"]}
    two = {"view_edits": {VID: {"sig_b": {"kind": "logic", "src_out_name": "sig_b",
                                          "name": "sig_b", "renamed": False,
                                          "cols": [{"name": "T0"}, {"name": "T1"}]}},
                          "logic": {"sig_c": {"kind": "logic", "src_out_name": "sig_c",
                                              "name": "sig_c", "renamed": False, "cols": []}}}}
    P.save_edits_all({p1: one, p2: two})
    got = P.load_edits_bucket(p1)
    assert set(got["view_edits"][VID]) == {"sig_a", "sig_b"}, "两个旧桶没合到一起"
    assert "logic" in got["view_edits"], "别的 view_id 子桶在合并时掉了"
    assert got["signals_checked"] == ["sig_a"], "legacy 段在合并时掉了"

    # 冲突：同一个信号两边都有 → 列多的那份赢
    P.save_edits_all({
        p1: {"view_edits": {VID: {"sig_x": {"kind": "logic", "src_out_name": "sig_x",
                                            "name": "sig_x", "renamed": False,
                                            "cols": [{"name": "T0"}, {"name": "T1"}]}}}},
        p2: {"view_edits": {VID: {"sig_x": {"kind": "logic", "src_out_name": "sig_x",
                                            "name": "sig_x", "renamed": False,
                                            "cols": [{"name": "T0"}]}}}}})
    assert len(P.load_edits_bucket(p2)["view_edits"][VID]["sig_x"]["cols"]) == 2


@pytest.mark.contract("C-233")
def test_r2_05_settings_path_configs_share_one_bucket(qapp, iso, btlp):
    """R2-05：settings 里按路径分桶的**三套诊断配置**同口径（探针前缀 / 强制 force / RTL 补充）。"""
    p1, p2 = str(btlp), str(btlp).replace("\\", "/")
    st = loaded(p1)
    st.set_probe_prefixes({"d_mir_a": "U_TOP.U_SUB"})
    st.set_force_signals(["d_mir_f"])

    st2 = loaded(p2)
    assert st2.probe_prefixes == {"d_mir_a": "U_TOP.U_SUB"}, "换个路径写法，探针前缀不见了"
    assert st2.force_signals == {"d_mir_f"}
    st2.set_probe_prefixes({"d_mir_a": "U_TOP.U_SUB", "d_mir_b": "U_TOP"})
    seg = P.load_settings()["probe_prefixes"]
    assert len(session.path_bucket_keys(seg, p1)) == 1, "同一个文件又分出了第二个桶：%s" % sorted(seg)
    assert loaded(p1).probe_prefixes == {"d_mir_a": "U_TOP.U_SUB", "d_mir_b": "U_TOP"}


@pytest.mark.contract("C-235")
def test_r2_05_key_spelling_on_disk_is_not_rewritten(qapp, iso, btlp):
    """R2-05 的兼容底线：盘上**已有**的键拼法不许被改写。

    『排查(旧)』门面是拿它自己那个拼法去 `.get(path)` 的 —— 把键归一成小写盘符，
    同事回退旧版本就会发现自己的活「没了」（其实还在文件里，只是键对不上，C-235）。
    """
    weird = str(btlp).replace("\\", "/")                 # 盘上先有这个拼法
    P.save_edits_all({weird: {"edits": {"d_old": []}}})
    st = loaded(str(btlp))                               # 用另一个拼法打开并存盘
    a = _two_editables(st)[0]
    _edit_one(st, a, 3)
    keys = list(P.load_edits_all())
    assert keys == [weird], "盘上的键拼法被改写了：%s" % keys
    assert P.load_edits_all()[weird]["edits"] == {"d_old": []}, "legacy 段没了"


# ═══════════════ R2-07：edits 文件写了一半（I-05 / C-235）═══════════════
def _corrupt_the_file(path, text="{\"a\": {\"view_edits\": {\"topout\": {\"x\": "):
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return text


@pytest.mark.contract("C-235", "C-300")
def test_r2_07_corrupt_edits_file_is_backed_up_and_named(qapp, iso, btlp):
    """R2-07：整份 edits 文件读不出来（写了一半 / 手改坏了）→ 原文件改名另存 + 状态栏点名，
    **不再静默从空白开始**。

    用户看到的：一个编辑都没恢复，而没有任何一处说为什么；接着他第一次编辑，
    `write_edits_bucket` 以那份空 `{}` 为基底整份覆盖，**别的表的桶 + legacy 九段**一起没了
    —— 而原文件里那些字节本来是捞得回来的。
    """
    broken = _corrupt_the_file(P.EDITS_PATH)
    st = ST.WorkbenchState()
    said = []
    st.statusMessage.connect(said.append)
    assert st.load(str(btlp))

    hit = [s for s in said if ".corrupt-" in s]
    assert hit, "坏文件没点名：%s" % said
    bak = next(p for p in os.listdir(os.path.dirname(P.EDITS_PATH))
               if ".corrupt-" in p)
    bak = os.path.join(os.path.dirname(P.EDITS_PATH), bak)
    assert bak in hit[0], "点名的路径不是那份备份：%s" % hit[0]
    with open(bak, encoding="utf-8") as f:
        assert f.read() == broken, "备份不是原文件的字节"
    assert not os.path.exists(P.EDITS_PATH), "原文件该被改名走了"

    # 这一趟从空白开始，但**用户还捞得回来**；接着正常编辑照常存盘
    st.set_models(VID, st.provider(VID).skeleton_models(), partial=True)
    a = _two_editables(st)[0]
    _edit_one(st, a, 3)
    assert P.load_edits_bucket(str(btlp))["view_edits"][VID][a.lower()]["cols"][0]["exp"] == 3


@pytest.mark.contract("C-235")
def test_r2_07_unreadable_and_unbackupable_file_is_not_overwritten(qapp, iso, btlp, monkeypatch):
    """R2-07 的另一半：读不出来**又改不动名**（占用 / 权限）时，这一趟**不许整份覆盖**。

    整份覆盖以空 `{}` 为基底 —— 写下去就是把同事的 legacy 九段与别的表的桶一起销毁，
    而他什么提示都收不到。宁可这一次不存盘（编辑还在内存里），也不能销毁文件。
    """
    broken = _corrupt_the_file(P.EDITS_PATH)
    monkeypatch.setattr(os, "replace", lambda *a, **k: (_ for _ in ()).throw(OSError("locked")))
    st = loaded(btlp)
    a = _two_editables(st)[0]
    _edit_one(st, a, 3)
    with open(P.EDITS_PATH, encoding="utf-8") as f:
        assert f.read() == broken, "读不出来的文件被整份覆盖了"
    assert P.save_edits_all({"x": {}}) is False, "读不出来时 save_edits_all 该直接拒写"


# ═══════════════ R2-08：配置 .txt 带 BOM（C-233 / I-04）═══════════════
BOM = "﻿"


@pytest.mark.contract("C-233")
def test_r2_08_bom_in_prefix_and_force_files(qapp, tmp_path):
    """R2-08：记事本 / Excel 存出来的前缀 .txt 带 BOM → U+FEFF 拼进层级路径或信号名。

    用户看到的：前缀编辑器里明明配好了，导出的 .sv 里那个探针还是裸名（前缀**静默不生效**）；
    赶上 BOM 落在路径那一侧就更糟 —— `\\ufeffU_TOP.U_SUB` 一路带进 .sv 的层级路径。
    三种写法（扁平 / 合并组头 / 混用）都要过，force 名单同理。
    """
    flat = tmp_path / "flat.txt"
    flat.write_text(BOM + "d_mir_a=U_TOP.U_SUB\nd_mir_b=U_TOP\n", encoding="utf-8")
    grouped = tmp_path / "grp.txt"
    grouped.write_text(BOM + "U_TOP.U_SUB:\n    d_mir_a, d_mir_b\n", encoding="utf-8")
    mixed = tmp_path / "mix.txt"
    mixed.write_text(BOM + "# 注释\nd_mir_a=U_TOP.U_SUB\nU_TOP:\n    d_mir_b\n", encoding="utf-8")

    assert session.parse_probe_prefix_text(session.read_text_file(str(flat))) == \
        {"d_mir_a": "U_TOP.U_SUB", "d_mir_b": "U_TOP"}
    assert session.parse_probe_prefix_text(session.read_text_file(str(grouped))) == \
        {"d_mir_a": "U_TOP.U_SUB", "d_mir_b": "U_TOP.U_SUB"}
    assert session.parse_probe_prefix_text(session.read_text_file(str(mixed))) == \
        {"d_mir_a": "U_TOP.U_SUB", "d_mir_b": "U_TOP"}

    fo = tmp_path / "force.txt"
    fo.write_text(BOM + "d_mir_f\nd_mir_g\n", encoding="utf-8")
    assert session.parse_force_signal_text(session.read_text_file(str(fo))) == {"d_mir_f", "d_mir_g"}

    # 文本不经文件直接进来（粘贴 / 剪贴板）也要剥掉 —— 解析器自己兜一道
    assert G.parse_probe_prefix_lines(BOM + "d_mir_a=U_TOP") == {"d_mir_a": "U_TOP"}
    assert session.parse_force_signal_text(BOM + "d_mir_f") == {"d_mir_f"}
    # CRLF + BOM 一起（Windows 记事本的默认存法）
    fo2 = tmp_path / "crlf.txt"
    with open(str(fo2), "wb") as f:
        f.write((BOM + "d_mir_a=U_TOP.U_SUB\r\nd_mir_b=U_TOP\r\n").encode("utf-8"))
    assert session.parse_probe_prefix_text(session.read_text_file(str(fo2))) == \
        {"d_mir_a": "U_TOP.U_SUB", "d_mir_b": "U_TOP"}


@pytest.mark.contract("C-233")
def test_r2_08_no_bom_files_are_byte_for_byte_unchanged(qapp, tmp_path):
    """R2-08 兼容：**不带** BOM 的老文件解析结果一个字都不变（改的只是「多剥一个字符」）。"""
    plain = tmp_path / "plain.txt"
    plain.write_text("d_mir_a=U_TOP.U_SUB\n# 注释\nU_TOP:\n    d_mir_b\n", encoding="utf-8")
    assert session.parse_probe_prefix_text(session.read_text_file(str(plain))) == \
        {"d_mir_a": "U_TOP.U_SUB", "d_mir_b": "U_TOP"}
    # 往返：渲染回去仍然无损（`render_probe_prefix_grouped` 的既有保证）
    txt = session.render_probe_prefix_text({"d_mir_a": "U_TOP.U_SUB", "d_mir_b": "U_TOP"})
    assert session.parse_probe_prefix_text(txt) == {"d_mir_a": "U_TOP.U_SUB", "d_mir_b": "U_TOP"}


# ═══════════════ R2-03：手编列标号（C-091 / C-139）═══════════════
@pytest.mark.contract("C-091", "C-139")
def test_r2_03_user_column_label_same_in_header_and_sv(qapp, btlp, wl):
    """R2-03：手编列改名 `MY_CASE` 后，表头 / 本会话 .sv / 重开后 .sv 三处必须是同一个标号。

    用户看到的：表头写着 `U0`，导出的 .sv 里那块叫 `T25`；改名成 `MY_CASE` 之后
    .sv 里**整个搜不到**这个名字 —— designer 照着表头去 .sv 里找自己那条用例，找不到。

    根因：`mux_derive` 的 `clone_vector` 不贴列名，克隆出来的向量顶着**源列**的标号
    （源是自动列时干脆没有名字，.sv 退回自动 `T<n>`）。
    """
    n_sig = 0
    for path in (btlp, wl):
        st = loaded(path)
        for nm in mux_signals(st):
            an = st.analyze(nm)
            cols = ED.cols_from_vectors(an, e_inputs_from_an(an))
            if not cols:
                continue
            n_sig += 1
            cols.extend(ED.copy_cols(cols, [0]))             # 复制一列 = 手编列
            ok, _i = ED.rename_col(cols, len(cols) - 1, "MY_CASE")
            assert ok, _i
            cols[-1]["exp"] = cols[-1]["auto"] ^ 1

            rec = {nm.lower(): rec_of(st, nm, cols)}
            live_sv = sv_of(st, nm, ED.compute_edited(rec, {}))
            relo_sv = sv_of(st, nm, ED.compute_edited(reload_edits(st, rec), {}))
            assert "MY_CASE" in live_sv, "%s：本会话 .sv 里搜不到手填的标号" % nm
            assert "MY_CASE" in relo_sv, "%s：重开后 .sv 里搜不到手填的标号" % nm
            assert live_sv == relo_sv, "%s：重开前后 .sv 的标号不一样" % nm
            # 反例列同口径（`add_negatives` 也克隆向量）
            made, _s = ED.add_negatives(cols, [0], False)
            assert made
            assert made[0]["vec"] is None or made[0]["vec"].name == made[0]["name"]
    assert n_sig >= 8, "只对了 %d 条 mux 信号，覆盖太窄" % n_sig
