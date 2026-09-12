# -*- coding: utf-8 -*-
"""dreg_verify/edits.py —— Qt-free 真值表编辑层的单元测试（GUI v2 地基，A4b）。

全部直接构造数据测：**不起 Qt、不开 MainWindow**。gui.SignalView 的同名方法现在是薄委托，
既有 ≈420 条 GUI 测试是「行为逐字节不变」的等价性证明；这里测的是抽出来那一层自己的语义。
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dreg_verify import edits as ED            # noqa: E402
from dreg_verify import expr as E              # noqa: E402
from dreg_verify import generator as G         # noqa: E402
from dreg_verify import vectors as V           # noqa: E402
from dreg_verify.resolver import InputBinding  # noqa: E402


# ───────────────────────── 构造用小工具（全是 mirror 夹具名） ─────────────────────────
def _bind(letter, base, width=1):
    return InputBinding(letter, base, base, width, "RO", None, None, None, base, "tmm")


def _logic_an(expr_text="A & B", out_width=1, dft_gate=None):
    """手搭一个 logic 分析结果 an（只含 edits.py 用到的键）。"""
    node = E.parse(expr_text)
    bindings = {"A": _bind("A", "d_mir_a"), "B": _bind("B", "d_mir_b")}
    groups = V.input_groups(node, bindings)
    vecs = []
    for i, bv in enumerate(({"d_mir_a": 0, "d_mir_b": 0}, {"d_mir_a": 1, "d_mir_b": 1})):
        vecs.append(V.make_vector_from_base_values(node, bindings, groups, bv, out_width, index=i))
    an = {"name": "d_mir_out", "src_out_name": "d_mir_out", "kind": "logic",
          "editable": "logic", "node": node, "bindings": bindings, "groups": groups,
          "out_width": out_width, "vectors": vecs}
    if dft_gate:
        an["dft_gate"] = dft_gate
    return an


def _logic_inputs(an):
    return [{"key": g["key"], "label": g["label"], "width": g["width"],
             "editable": True, "control": g.get("is_control", False)} for g in an["groups"]]


def _mux_an():
    """手搭一个 mux 分析结果 an（mux 路只用 vectors/expansion/out_width，不需要 node）。"""
    vecs = []
    for i, (c, d0, d1) in enumerate(((0, 5, 9), (1, 5, 9))):
        v = V.TestVector(i, {"c:S": c, "d:0": d0, "d:1": d1},
                         (d0 if c == 0 else d1), 4, case_index=i)
        vecs.append(v)
    return {"name": "d_mir_mux", "src_out_name": "d_mir_mux", "kind": "mux", "editable": "mux",
            "groups": [], "out_width": 4, "vectors": vecs,
            "expansion": {"used_vars": ["c:S", "d:0", "d:1"], "data_keys": ["d:0", "d:1"],
                          "bindings": {}}}


def _mux_inputs():
    return [{"key": "c:S", "label": "d_mir_sel", "width": 1, "editable": False, "control": True,
             "mux_data_base": None},
            {"key": "d:0", "label": "d_mir_src0[3:0]", "width": 4, "editable": False,
             "control": False, "mux_data_base": "d_mir_src0"},
            {"key": "d:1", "label": "d_mir_src1[3:0]", "width": 4, "editable": False,
             "control": False, "mux_data_base": "d_mir_src1"}]


# ═══════════════════════ ① 文件 IO 与序列化 ═══════════════════════
def test_edits_file_roundtrip_and_never_crashes(tmp_path):
    """存盘/读回往返；文件不存在或损坏 → {}（每次加载 Excel 都跑，绝不能崩）。"""
    p = str(tmp_path / "edits.json")
    assert ED.load_edits_file(p) == {}                       # 不存在
    assert ED.save_edits_file(p, {"x.xlsx": {"edits": {}}}) is True
    assert ED.load_edits_file(p) == {"x.xlsx": {"edits": {}}}
    open(p, "w", encoding="utf-8").write("{ 这不是 json")
    assert ED.load_edits_file(p) == {}                       # 损坏
    open(p, "w", encoding="utf-8").write("[1, 2]")
    assert ED.load_edits_file(p) == {}                       # 顶层不是 dict


def test_save_edits_file_skips_default_path_under_pytest(tmp_path):
    """pytest 下写【默认路径】一律跳过——防污染用户真实编辑；monkeypatch 到临时文件才真落盘。"""
    assert ED.save_edits_file(ED.DEFAULT_EDITS_PATH, {"a": 1}) is False
    p = str(tmp_path / "e.json")
    assert ED.save_edits_file(p, {"a": 1}, ED.DEFAULT_EDITS_PATH) is True
    assert os.path.exists(p)


def test_coerce_int_map_skips_bad_values():
    out, bad = ED.coerce_int_map({"A": 1, "B": "x", "C": None, "D": "3"})
    assert out == {"A": 1, "D": 3} and bad == 2
    out, bad = ED.coerce_int_map({"MixedCase": 7}, lower=True)
    assert out == {"mixedcase": 7} and bad == 0


def test_serialize_rows_drops_computed_fields():
    rows = [{"base_values": {"a": 1}, "kind": "pos", "designer_expected": 1,
             "correct": 1, "_vec": object()}]
    ser = ED.serialize_rows(rows)
    json.dumps(ser)                                          # 必须可 JSON 化
    back = ED.deserialize_rows(ser)
    assert back[0]["designer_expected"] == 1
    assert "correct" not in back[0] and "_vec" not in back[0]
    assert ED.deserialize_rows([None, 3, "x"]) == []          # 损坏项跳过


def test_mux_vecs_roundtrip_and_skips_broken():
    v = V.TestVector(0, {"c:S": 1, "d:1": 9}, 9, 4, name="U0", case_index=1,
                     designer_expected=9)
    back = ED.deserialize_mux_vecs(json.loads(json.dumps(ED.serialize_mux_vecs([v]))))
    assert len(back) == 1 and back[0].assignments == {"c:S": 1, "d:1": 9}
    assert back[0].name == "U0" and back[0].case_index == 1 and back[0].designer_expected == 9
    assert ED.deserialize_mux_vecs([{"assignments": {"a": "坏"}}, None]) == []


# ═══════════════════════ ② 列模型 ↔ 向量 ═══════════════════════
def test_cols_from_vectors_logic():
    an = _logic_an()
    cols = ED.cols_from_vectors(an, _logic_inputs(an))
    assert [c["name"] for c in cols] == ["T0", "T1"]
    assert cols[0]["vals"]["d_mir_a"] == 0 and cols[1]["vals"]["d_mir_b"] == 1
    assert cols[0]["auto"] == 0 and cols[1]["auto"] == 1      # A&B
    assert all(c["exp"] is None and not c["user"] for c in cols)


def test_cols_to_vectors_keeps_designer_expected_and_negative():
    an = _logic_an()
    cols = ED.cols_from_vectors(an, _logic_inputs(an))
    cols[1]["exp"] = 0                                        # designer 手填 != auto(=1)
    vecs = ED.cols_to_vectors(an, cols)
    assert vecs[1].designer_expected == 0 and not vecs[1].is_negative   # 手填不等于负向


def test_negative_column_survives_wrong_value_equal_correct():
    """⭐负向列把错值填成 auto → 必须仍是负向(NEG-BROKEN，仿真会过但日志看得见)，不静默降级成正向。"""
    an = _logic_an()
    cols = ED.cols_from_vectors(an, _logic_inputs(an))
    cols[1]["neg"] = True
    cols[1]["exp"] = cols[1]["auto"]                          # 错值 == 正确值
    v = ED.cols_to_vectors(an, cols)[1]
    assert v.is_negative and v.neg_value == cols[1]["auto"] and v.neg_mode == "value"


def test_mux_derive_dropped_expected_user():
    an = _mux_an()
    cols = ED.cols_from_vectors(an, _mux_inputs())
    cols[0]["exp"] = 7                                        # 手填期望
    user = dict(cols[1]); user["user"] = True; user["name"] = "U0"
    user["vec"] = V.clone_vector(cols[1]["vec"])
    d = ED.mux_derive(an, [cols[0], user])
    assert not d["cleared"]
    assert d["dropped"] == [G.mux_assign_key(an["vectors"][1].assignments)]   # 自动列 T1 被替换=丢
    assert d["expected"] == {G.mux_assign_key(an["vectors"][0].assignments): 7}
    assert len(d["user_vecs"]) == 1
    assert ED.mux_derive(an, [])["cleared"] is True


def test_mux_resync_cols_keeps_user_intent():
    """整表数据值变了：自动列按新分析重建但贴回手填期望/负向；删过的自动列不复活；用户列原样留。"""
    an = _mux_an()
    e_in = _mux_inputs()
    old = ED.cols_from_vectors(an, e_in)
    old[0]["exp"] = 3; old[0]["neg"] = True
    user = {"name": "U0", "neg": False, "vals": dict(old[1]["vals"]), "exp": None,
            "auto": 9, "auto_w": 4, "user": True, "vec": V.clone_vector(old[1]["vec"])}
    resynced = ED.mux_resync_cols(an, [old[0], user], e_in)   # 老模型里没有 T1 = 用户删过
    assert [c["name"] for c in resynced] == ["T0", "U0"]
    assert resynced[0]["exp"] == 3 and resynced[0]["neg"] is True
    assert resynced[1]["user"] is True


def test_is_dft_pitch_col_and_recompute_skips_it():
    """iddq 漏电态自检拍：节点不含 iddq 门，重算会得功能值 → 必须跳过，否则假红当成反例。"""
    gate = {"key": "gate", "label": "d_mir_iddq", "width": 1, "wire_lhs": "d_mir_iddq",
            "transp": 0}
    an = _logic_an(dft_gate=gate)
    col = {"name": "T9", "neg": False, "vals": {"d_mir_a": 1, "d_mir_b": 1, "gate": 1},
           "exp": 0, "auto": 0, "auto_w": 1, "user": False, "vec": None, "dft": True}
    assert ED.is_dft_pitch_col(an, col)
    ED.recompute_col_an(an, col)
    assert col["auto"] == 0                                   # 没被重算成功能值 1
    normal = {"name": "T0", "neg": False, "vals": {"d_mir_a": 1, "d_mir_b": 1, "gate": 0},
              "exp": None, "auto": 0, "auto_w": 1, "user": False, "vec": None, "dft": False}
    assert not ED.is_dft_pitch_col(an, normal)
    ED.recompute_col_an(an, normal)
    assert normal["auto"] == 1                                # 普通列照常重算
    assert not ED.is_dft_pitch_col(_logic_an(), normal)       # 无 dft 门的信号一律 False


def test_recompute_mux_user_auto_follows_routed_case():
    an = _mux_an()
    cols = ED.cols_from_vectors(an, _mux_inputs())
    col = dict(cols[1]); col["user"] = True; col["vec"] = V.clone_vector(cols[1]["vec"])
    col["vec"].assignments["d:1"] = 0xC                       # 改路由源(case_index=1 → d:1)
    ED.recompute_mux_user_auto(an, col)
    assert col["auto"] == 0xC and col["vec"].exp_value == 0xC


def test_compute_edited_and_overrides_routing():
    an = _logic_an()
    cols = ED.cols_from_vectors(an, _logic_inputs(an))
    edits = {"d_mir_out": {"kind": "logic", "src_out_name": "d_mir_out", "name": "d_mir_out",
                           "renamed": False, "cols": cols, "an": an}}
    ed = ED.compute_edited(edits, {})
    assert ed["d_mir_out"]["cleared"] is False and len(ed["d_mir_out"]["vectors"]) == 2
    ov = ED.topout_edit_overrides(ed)
    assert "d_mir_out" in ov["vector_overrides"] and ov["reg_overrides"] is None
    # dft 改名根 → 走 reg_overrides（否则编辑落 vov[源名] 被改名路忽略 = 改了不生效，静默）
    edits["d_mir_out"]["renamed"] = True
    edits["d_mir_out"]["name"] = "d_mir_out_ls"
    ov2 = ED.topout_edit_overrides(ED.compute_edited(edits, {}))
    assert ov2["vector_overrides"] is None and "d_mir_out_ls" in ov2["reg_overrides"]


def test_compute_edited_injects_mux_data_only_record():
    """纯手填 mux 数据值（信号根本没进 edits）也要补一条 data-only rec，否则手填静默不生效。"""
    mux_data = {"d_mir_mux": {"src": "d_mir_mux", "name": "d_mir_mux",
                              "data": {"d_mir_src0": 6}}}
    ed = ED.compute_edited({}, mux_data)
    assert ed["d_mir_mux"]["mux"]["data"] == {"d_mir_src0": 6}
    assert ED.topout_edit_overrides(ed)["mux_data"] == {"d_mir_mux": {"d_mir_src0": 6}}
    assert ED.compute_edited({}, {"x": {"src": "x", "name": "x", "data": {}}}) == {}   # 空不写


def test_page_edit_overrides_has_no_register_root():
    ed = {"a": {"kind": "register", "src_out_name": "d_mir_reg", "name": "d_mir_reg",
                "vectors": [], "cleared": False}}
    assert ED.page_edit_overrides(ed)["vector_overrides"] is None   # 页本地无 register 根


# ═══════════════════════ ③ 编辑操作 ═══════════════════════
def test_new_col_name_checks_whole_namespace():
    """唯一性查【全集】（含 _NEG 后缀、大小写无关）——此前只查 "U%d"，批量加负向全撞 U0_NEG。"""
    cols = [{"name": "T0"}, {"name": "u0_NEG"}, {"name": "U1"}]
    assert ED.new_col_name(cols) == "U0"
    assert ED.new_col_name(cols, "_NEG") == "U1_NEG"
    assert ED.col_names(cols) == {"T0", "u0_NEG", "U1"}


def test_add_copy_del_cols():
    an = _logic_an()
    e_in = _logic_inputs(an)
    cols = ED.cols_from_vectors(an, e_in)
    new = ED.add_col(an, cols, e_in)
    assert new["user"] and new["name"] == "U0" and new["exp"] is None
    assert new["vals"] == {"d_mir_a": 0, "d_mir_b": 0} and new["auto"] == 0   # auto 自动算
    cols.append(new)
    copied = ED.copy_cols(cols, [1])
    assert len(copied) == 1 and copied[0]["name"] == "U1" and copied[0]["user"]
    assert copied[0]["vals"] == cols[1]["vals"] and copied[0]["vals"] is not cols[1]["vals"]
    assert [c["name"] for c in ED.copy_cols(cols, None)] == ["U1"]            # 未选=复制最后一列
    assert [c["name"] for c in ED.copy_cols(cols, [0, 1])] == ["U1", "U2"]    # 批量不重名
    assert [c["name"] for c in ED.del_cols(cols, {0, 2})] == ["T1"]


def test_fill_expected_only_untouched_positives():
    an = _logic_an()
    cols = ED.cols_from_vectors(an, _logic_inputs(an))
    cols[0]["exp"] = 1                                        # 已手填 → 不动
    cols.append({"name": "U0_NEG", "neg": True, "vals": {}, "exp": 0, "auto": 1, "auto_w": 1,
                 "user": True, "vec": None})                  # 负向 → 不填
    assert [c["name"] for c in ED.fill_targets(cols)] == ["T1"]
    assert ED.fill_expected(cols) == 1
    assert cols[0]["exp"] == 1 and cols[1]["exp"] == cols[1]["auto"]
    assert ED.fill_expected(cols) == 0                        # 再填一次没得填


def test_add_negatives_no_nesting_dedup_unique_names():
    an = _logic_an()
    cols = ED.cols_from_vectors(an, _logic_inputs(an))
    added, skipped = ED.add_negatives(cols, None, True)
    assert len(added) == 2 and skipped == 0
    assert [c["name"] for c in added] == ["U0_NEG", "U1_NEG"]  # 列名对全集唯一
    assert all(c["neg"] and c["user"] and c["exp"] != c["auto"] for c in added)
    cols.extend(added)
    again, skipped2 = ED.add_negatives(cols, None, True)       # 同输入取值已有负向 → 全跳过
    assert again == [] and skipped2 == 2
    # 只对正向造负向：选中两条负向列 → 退回本信号首条正向，不给负向再套娃
    added3, _ = ED.add_negatives([c for c in cols if c["neg"]], [0, 1], False)
    assert added3 == []


def test_add_negatives_wrong_value_avoids_designer_expected():
    """m6：错值要同时避开 auto_out 与 designer 手填期望，否则反例会 PASS(NEG-BROKEN 静默)。"""
    cols = [{"name": "T0", "neg": False, "vals": {"d_mir_a": 1}, "exp": 2, "auto": 1,
             "auto_w": 4, "user": False, "vec": None}]
    added, _ = ED.add_negatives(cols, [0], False)
    assert added[0]["exp"] not in (1, 2)


def test_protected_negatives_and_del_negatives():
    auto_wrong = V.make_negative(V.TestVector(0, {}, 1, 4), mode="invert").neg_value
    cols = [
        {"name": "T0", "neg": False, "vals": {}, "exp": None, "auto": 1, "auto_w": 4,
         "user": False, "vec": None},
        {"name": "U0_NEG", "neg": True, "vals": {}, "exp": auto_wrong, "auto": 1, "auto_w": 4,
         "user": True, "vec": None},                          # 工具造的：名字自动 + 错值=防撞值
        {"name": "my_neg", "neg": True, "vals": {}, "exp": auto_wrong, "auto": 1, "auto_w": 4,
         "user": True, "vec": None},                          # 自定义命名 → 值得保护
        {"name": "T1_NEG", "neg": True, "vals": {}, "exp": 0xA, "auto": 1, "auto_w": 4,
         "user": True, "vec": None},                          # 手填过错值 → 值得保护
    ]
    prot = ED.protected_negatives(cols)
    assert [c["name"] for c in prot] == ["my_neg", "T1_NEG"]
    rest, n = ED.del_negatives(cols)
    assert n == 3 and [c["name"] for c in rest] == ["T0"]      # 正向保留


def test_rename_col_three_gates():
    cols = [{"name": "T0", "neg": False}, {"name": "U0", "neg": False},
            {"name": "U1", "neg": True}]
    assert ED.rename_col(cols, 1, "  ")[0] is False                 # 空/全非法字符
    assert ED.rename_col(cols, 1, "T3")[0] is False                 # T<编号> 保留名
    assert ED.rename_col(cols, 1, "T0")[0] is False                 # 与其它列最终标号重名
    ok, info = ED.rename_col(cols, 2, "my case")                    # 负向列自动补 _NEG
    assert ok and info == "my_case_NEG" and cols[2]["name"] == "my_case_NEG"
    assert ED.rename_col(cols, 9, "x")[0] is False                  # 越界不崩


def test_set_mux_data_value_set_clear_and_bad_text():
    md = {}
    ED.set_mux_data_value(md, "d_mir_mux", "d_mir_mux", "d_mir_mux", "d_mir_src0", 4, "16'hA")
    assert md["d_mir_mux"]["data"] == {"d_mir_src0": 0xA}
    ED.set_mux_data_value(md, "d_mir_mux", "d_mir_mux", "d_mir_mux", "d_mir_src0", 4, "0x1F")
    assert md["d_mir_mux"]["data"] == {"d_mir_src0": 0xF}           # 按位宽 mask
    ED.set_mux_data_value(md, "d_mir_mux", "d_mir_mux", "d_mir_mux", "d_mir_src0", 4, "  ")
    assert md == {}                                                  # 清空=恢复自动，空桶删掉
    try:
        ED.set_mux_data_value(md, "d_mir_mux", "d_mir_mux", "d_mir_mux", "d_mir_src0", 4, "??")
    except ValueError:
        pass
    else:
        raise AssertionError("非法写法必须抛 ValueError，绝不静默吞成 0")


def test_set_mux_user_data_only_touches_own_column():
    an = _mux_an()
    cols = ED.cols_from_vectors(an, _mux_inputs())
    col = dict(cols[1]); col["user"] = True; col["vec"] = V.clone_vector(cols[1]["vec"])
    col["vals"] = dict(cols[1]["vals"])
    assert ED.set_mux_user_data(an, col, "d:1", 4, "'hC") is True
    assert col["vals"]["d:1"] == 0xC and col["auto"] == 0xC
    assert cols[0]["vals"]["d:1"] == 9                               # 别的列没被联动
    assert ED.set_mux_user_data(an, {"vec": None}, "d:1", 4, "1") is False


# ═══════════════════════ ④ 存盘子桶 / 还原 ═══════════════════════
def test_serialize_view_edits_drops_analysis_and_keeps_case_index():
    an = _mux_an()
    cols = ED.cols_from_vectors(an, _mux_inputs())
    ser = ED.serialize_view_edits({"d_mir_mux": {
        "kind": "mux", "src_out_name": "d_mir_mux", "name": "d_mir_mux",
        "renamed": False, "cols": cols, "an": an}})
    json.dumps(ser)                                                  # an/vec 都不能落盘
    assert set(ser["d_mir_mux"]) == {"kind", "src_out_name", "name", "renamed", "cols"}
    assert ser["d_mir_mux"]["cols"][1]["case_index"] == 1


def test_restore_cols_rebuilds_mux_vec_and_recomputes_logic_auto():
    an = _mux_an()
    specs = ED.serialize_view_edits({"m": {"kind": "mux", "src_out_name": "m", "name": "m",
                                           "cols": ED.cols_from_vectors(an, _mux_inputs())}
                                     })["m"]["cols"]
    back = ED.restore_cols(an, specs)
    assert back[1]["vec"] is not None and back[1]["vec"].case_index == 1
    # logic：auto 一律按当前表重算（存盘里的陈旧 auto 不作数，否则改表后假绿）
    lan = _logic_an()
    lspec = [{"name": "T1", "neg": False, "vals": {"d_mir_a": 1, "d_mir_b": 1},
              "exp": None, "auto": 0, "auto_w": 1, "user": False}]      # 陈旧 auto=0
    assert ED.restore_cols(lan, lspec)[0]["auto"] == 1
    assert ED.restore_cols(lan, [None, 3, {"vals": {"a": "坏"}}]) == []   # 损坏项跳过，不崩


def test_restore_view_edits_skips_missing_broken_and_uneditable():
    an = _logic_an()
    models = [{"name": "D_Mir_Out"}]                                 # 清单里大小写不敏感匹配
    bucket = {"d_mir_out": {"cols": [{"name": "T0", "neg": False,
                                      "vals": {"d_mir_a": 1, "d_mir_b": 1}, "exp": 1,
                                      "auto": 1, "auto_w": 1, "user": False}]},
              "d_mir_gone": {"cols": []}}                            # 当前表里没有 → 跳过
    out = ED.restore_view_edits(bucket, models, lambda real: an)
    assert list(out) == ["d_mir_out"] and out["d_mir_out"]["cols"][0]["exp"] == 1
    assert out["d_mir_out"]["an"] is an

    def _boom(real):
        raise RuntimeError("分析炸了")

    assert ED.restore_view_edits(bucket, models, _boom) == {}         # 护栏3：吞异常不崩
    assert ED.restore_view_edits(bucket, models, lambda r: {"editable": None}) == {}
    assert ED.restore_view_edits(bucket, [], lambda r: an) == {}
    assert ED.restore_view_edits(None, models, lambda r: an) == {}


# ═══════════════════════ ⑤ 派生量（v2 用） ═══════════════════════
def test_fill_progress_counts_positives_only():
    cols = [{"neg": False, "exp": 1}, {"neg": False, "exp": None},
            {"neg": True, "exp": 0}]
    assert ED.fill_progress(cols) == (1, 2)
    assert ED.fill_progress([]) == (0, 0)


def test_expected_cell_state_priority():
    an = _logic_an()
    base = {"neg": False, "exp": None, "auto": 1, "auto_w": 1, "vals": {}, "dft": False}
    assert ED.expected_cell_state(an, dict(base)) == ED.EXP_UNFILLED
    assert ED.expected_cell_state(an, dict(base, exp=1)) == ED.EXP_MATCH
    assert ED.expected_cell_state(an, dict(base, exp=0)) == ED.EXP_DIFF
    assert ED.expected_cell_state(an, dict(base, neg=True, exp=0)) == ED.EXP_NEG
    gate = {"key": "gate", "label": "g", "width": 1, "wire_lhs": "g", "transp": 0}
    dan = _logic_an(dft_gate=gate)
    # iddq 拍优先于负向/手填判定：绝不能被当成反例或"手填不符"染红
    assert ED.expected_cell_state(dan, dict(base, dft=True, exp=0)) == ED.EXP_DFT
    assert ED.expected_cell_state(dan, dict(base, exp=0, vals={"gate": 1})) == ED.EXP_DFT


def test_input_cell_state_and_editable():
    lan = _logic_an()
    e_logic = _logic_inputs(lan)[0]
    assert ED.input_cell_editable(lan, e_logic)
    assert ED.input_cell_state(lan, e_logic) == ED.CELL_EDITABLE
    man = _mux_an()
    ctrl, data, _ = _mux_inputs()
    assert not ED.input_cell_editable(man, ctrl)                     # 控制位只读
    assert ED.input_cell_state(man, ctrl) == ED.CELL_READONLY
    assert ED.input_cell_editable(man, data)                         # 数据角色行可手填
    assert ED.input_cell_state(man, data) == ED.CELL_MUX_DATA
    gate_e = {"key": "gate", "label": "g", "width": 1, "editable": False, "control": False,
              "is_dft_gate": True, "transp": 0}
    assert ED.input_cell_state(lan, gate_e) == ED.CELL_DFT_GATE      # 门行压过其它角色
    assert not ED.input_cell_editable(lan, gate_e)


# ═══════════════════════ ⑥ 这一层必须 Qt-free ═══════════════════════
def test_edits_layer_never_imports_qt():
    """v2「换包不换引擎」的底线：编辑层不许 import PySide6，否则换门面就得连引擎一起改。"""
    import ast
    tree = ast.parse(open(ED.__file__, encoding="utf-8").read())
    mods = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            mods += [a.name for a in n.names]
        elif isinstance(n, ast.ImportFrom):
            mods.append(n.module or "")
    assert not [m for m in mods if "PySide" in m or "Qt" in m or m.endswith("gui")], mods
