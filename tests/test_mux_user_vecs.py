# -*- coding: utf-8 -*-
"""第二十八轮：用户手编 mux 测试列（mux 与 logic 平级）——generator 侧注入 + 去重的回归。

mux 向量过去由 case 结构自动生成、GUI 只能【删/改值/改期望/整信号负向】，不能【加列】。
本轮加 GenOptions.mux_user_vecs：在 make_mux_vectors 之后、删列过滤之后注入用户手编/复制的列，
正向参与 designer 期望/全局负向追加，负向用户列原样保留；与全局负向重叠的相同负向由 _dedup_negatives 去重。
每条用户 vec 的 case_index 标它路由的 case → auto_out = assignments[data_keys[case_index]]（无需复跑选路）。
"""
import os
import sys

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dreg_verify import excel_model, generator       # noqa: E402
from dreg_verify import expr as E                     # noqa: E402
from dreg_verify import mux_gen                       # noqa: E402
from dreg_verify import resolver as R                 # noqa: E402
from dreg_verify import sv_writer as W                # noqa: E402
from dreg_verify import vectors as V                  # noqa: E402
import fixtures                                        # noqa: E402


@pytest.fixture(scope="module")
def wb_mux(tmp_path_factory):
    path = tmp_path_factory.mktemp("xl_uv") / "synthetic_mux.xlsx"
    fixtures.build_workbook(str(path), with_mux=True)
    return excel_model.load_workbook(str(path))


def _gen_vecs(wb_mux, grp):
    resolver = R.Resolver(wb_mux)
    exp = mux_gen.expand_mux_group(wb_mux, resolver, grp)
    vecs, _meta = mux_gen.make_mux_vectors(grp, exp, mode="max")
    return exp, vecs


def test_mux_vec_carries_case_index(wb_mux):
    """make_mux_vectors 给每条向量打 case_index（与 data_keys 对齐）；auto_out=该 case 路由源值。"""
    grp = wb_mux.mux[0]
    exp, vecs = _gen_vecs(wb_mux, grp)
    assert vecs
    for v in vecs:
        assert v.case_index is not None
        dkey = exp["data_keys"][v.case_index]
        assert (v.assignments.get(dkey, 0) & E.mask(grp.out_width)) == v.exp_value


def test_build_injects_user_vec(wb_mux):
    """注入一条用户手编正向列（克隆某 case + 改路由源数据值 + 命名）→ build 多出该列、auto_out 跟随新值。"""
    grp = wb_mux.mux[0]
    exp, vecs = _gen_vecs(wb_mux, grp)
    v0 = vecs[0]
    ci = v0.case_index
    dkey = exp["data_keys"][ci]
    out_w = grp.out_width
    uv = V.clone_vector(v0)
    new_val = (uv.assignments.get(dkey, 0) ^ 0x1) & E.mask(out_w)   # 改路由源的数据值
    if new_val == uv.assignments.get(dkey, 0):
        new_val = (new_val ^ 0x2) & E.mask(out_w)
    uv.assignments[dkey] = new_val
    uv.exp_value = new_val & E.mask(out_w)
    uv.name = "MY_USER_TEST"

    base = generator.build(wb_mux, generator.GenOptions(signals=[grp.out_name], top_output_only=False))
    base_blk = next((l, s) for l, s in base["blocks"] if s.get("is_mux") and s.get("out_name") == grp.out_name)
    n_base = base_blk[1]["n_vectors"]

    res = generator.build(wb_mux, generator.GenOptions(
        signals=[grp.out_name], top_output_only=False,
        mux_user_vecs={grp.out_name.lower(): [uv]}))
    blk = next((l, s) for l, s in res["blocks"] if s.get("is_mux") and s.get("out_name") == grp.out_name)
    assert blk[1]["n_vectors"] == n_base + 1                  # 多了用户那一列
    text = "\n".join(blk[0])
    assert "MY_USER_TEST" in text                            # 用户列用自定义名做 assert 标号
    assert W.fmt_bin(new_val, out_w) in text                 # 断言用新数据值
    # 注入是克隆：原 uv 对象未被 build 重排 index 污染（仍可再次注入得到同结果）
    assert uv.name == "MY_USER_TEST"


def test_build_injects_user_negative_vec(wb_mux):
    """注入一条用户负向列（逐 case 加负向）→ build 出一条 _NEG，错值=路由源取反。"""
    grp = wb_mux.mux[0]
    exp, vecs = _gen_vecs(wb_mux, grp)
    neg = V.make_negative(V.clone_vector(vecs[0]))
    neg.name = "CASE0"
    res = generator.build(wb_mux, generator.GenOptions(
        signals=[grp.out_name], top_output_only=False,
        mux_user_vecs={grp.out_name.lower(): [neg]}))
    blk = next((l, s) for l, s in res["blocks"] if s.get("is_mux") and s.get("out_name") == grp.out_name)
    text = "\n".join(blk[0])
    assert "CASE0_NEG" in text                               # 负向用户列带 _NEG
    assert blk[1]["n_negative"] >= 1


def test_dedup_drops_duplicate_negative(wb_mux):
    """全局负向(neg_signals/which=first 对首条正向取反) 与 用户对【同一条】正向加的负向重叠 → 去重保留一条。"""
    grp = wb_mux.mux[0]
    exp, vecs = _gen_vecs(wb_mux, grp)
    # 用户负向 = 首条正向取反（与全局 which=first 命中的正是同一条 → 完全相同）
    dup_neg = V.make_negative(V.clone_vector(vecs[0]))
    res = generator.build(wb_mux, generator.GenOptions(
        signals=[grp.out_name], top_output_only=False,
        neg_signals=[grp.out_name],                          # 全局负向(which=first)
        mux_user_vecs={grp.out_name.lower(): [dup_neg]}))    # + 用户对同一条加负向
    blk = next((l, s) for l, s in res["blocks"] if s.get("is_mux") and s.get("out_name") == grp.out_name)
    sig = (tuple(sorted(dup_neg.assignments.items())), dup_neg.neg_value)
    # 渲染里这条相同负向只出现一次（去重）：统计该 assignments 的负向断言数应为 1
    # 用 n_negative 间接验证——若没去重，应比"仅全局负向"多 1
    only_global = generator.build(wb_mux, generator.GenOptions(
        signals=[grp.out_name], top_output_only=False, neg_signals=[grp.out_name]))
    g_blk = next((l, s) for l, s in only_global["blocks"]
                 if s.get("is_mux") and s.get("out_name") == grp.out_name)
    assert blk[1]["n_negative"] == g_blk[1]["n_negative"]    # 去重后负向数不增


def test_report_mirrors_build_user_vec(wb_mux):
    """report() 与 build() 双轨同步：注入的用户列也出现在报告 mux 真值表里。"""
    grp = wb_mux.mux[0]
    exp, vecs = _gen_vecs(wb_mux, grp)
    uv = V.clone_vector(vecs[0]); uv.name = "RPT_USER"
    rep = generator.report(wb_mux, generator.GenOptions(
        signals=[grp.out_name], top_output_only=False,
        mux_user_vecs={grp.out_name.lower(): [uv]}))
    tbl = next(t for t in rep["tables"] if not t.get("is_logic") and t["signal"] == grp.out_name)
    names = [t["name"] for t in tbl["tests"]]
    assert "RPT_USER" in names


def test_apply_mux_expected_skips_preset(wb_mux):
    """对抗评审 A1：apply_mux_expected 不覆盖【已有 designer_expected】的向量——用户手编列自带期望，
    其 assignments 可能与某自动列相同(同键)，若覆盖会被自动列的 _mux_expected 值盖掉。"""
    grp = wb_mux.mux[0]
    exp, vecs = _gen_vecs(wb_mux, grp)
    out_w = grp.out_width
    v = V.clone_vector(vecs[0]); v.designer_expected = 5 & E.mask(out_w)
    generator.apply_mux_expected([v], {generator.mux_assign_key(v.assignments): 9 & E.mask(out_w)})
    assert v.designer_expected == (5 & E.mask(out_w))      # 用户值未被 9 覆盖
    # 对照：未预设(None)的仍会被设上
    v2 = V.clone_vector(vecs[0]); v2.designer_expected = None
    generator.apply_mux_expected([v2], {generator.mux_assign_key(v2.assignments): 7 & E.mask(out_w)})
    assert v2.designer_expected == (7 & E.mask(out_w))


def test_make_negative_preserves_case_index(wb_mux):
    """对抗评审 B3：make_negative 保留 case_index——否则负向列(及由负向再转正向的列)auto_out 无法重算。"""
    grp = wb_mux.mux[0]
    exp, vecs = _gen_vecs(wb_mux, grp)
    neg = V.make_negative(vecs[0])
    assert neg.case_index == vecs[0].case_index and neg.case_index is not None
