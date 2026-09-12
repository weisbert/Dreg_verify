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
