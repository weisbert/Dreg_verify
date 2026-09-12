# -*- coding: utf-8 -*-
"""edits.py —— 真值表【编辑层】的 Qt-free 实现（GUI v2「换包不换引擎」的地基）。

本模块是 gui.SignalView 里那套「列模型 ↔ 向量 ↔ 存盘 JSON」的全部非界面逻辑：
gui.py 的同名私有方法现在都是薄委托，v2 的 spreadsheet 编辑器直接接这一层即可，
**不 import PySide6**（能直接单测，见 tests/test_edits.py，不用起 Qt）。

与 truth_edit.py 的分工（相邻、不重复实现）：
  · truth_edit = 更底层的【纯文本/命名】规则：数值写法解析、列名生成/校验、加负向挑列；
  · edits      = 列模型本身：造列/改列/删列、列↔TestVector 互转、存盘序列化、派生量。
edits.py 复用 truth_edit（TE.parse_int / TE.uniq_col_name / TE.check_col_name / TE.plan_negatives …），
不再抄一遍。

────────────────────────── 列模型（col）schema ──────────────────────────
真值表一【列】= 一条测试用例（屏幕上竖着一列：若干输入行 + auto_out 行 + 期望行）：
    {
      "name":   str,          # 测试标号（.sv 里的 assert 标号），自动列 T<n>、用户列 U<n>
      "neg":    bool,         # 负向列（故意填错期望，自检 checker）
      "vals":   {key: int},   # 每个输入行的取值；key = e_inputs[i]["key"]
      "exp":    int | None,   # designer 手填期望；None = 未填（生成时 auto 兜底）；负向列=错值
      "auto":   int,          # auto_out：程序按表达式算出的输出值（只读参考）
      "auto_w": int,          # auto_out 位宽
      "user":   bool,         # 用户新增/复制/负向列（可改名；自动 T 列不可）
      "vec":    TestVector|None,   # mux 走向量模型（路由 case 在 vec.case_index）；logic 可为 None
      "dft":    bool,         # iddq 漏电态自检拍（工具自动加的末列，不是反例）
    }
输入行（e_inputs[i]，由 gui 按分析结果 an 造）本模块只读这些键：
    key / label / width / editable / control / mux_data_base / is_dft_gate / wire_lhs / transp

────────────────────── SignalView 存盘桶（view_edits）schema ──────────────────────
EDITS_PATH（~/.dreg_verify_edits.json）顶层 = {excel 绝对路径: bucket}。
bucket["view_edits"] = {view_id: {signal_name_low: {…}}}，
view_id ∈ {"topout", "logic", "mux", "dft", "iddq"}：
    {signal_name_low: {
        "kind":         "logic" | "mux" | "register",
        "src_out_name": str,     # 源输出名（回流 vector_overrides 用的键）
        "name":         str,     # 显示名（dft 改名后的顶层名）
        "renamed":      bool,    # dft 改名根（按顶层名走 reg_overrides）
        "cols": [ {"name","neg","vals","exp","auto","auto_w","user","dft","case_index"} , … ],
    }}
存盘只留【可重算字段 + 用户意图】：auto 只作兜底，恢复时 logic 列一律按当前表/覆盖度重算
（防改表后陈旧假绿），mux 列据 vals + case_index 重建 TestVector。
bucket["view_checks"] = {view_id: [信号名]}——仅"取消过部分勾选"的视图才写（全勾=默认=不写桶）。
bucket["view_mux_data"] = {view_id: {信号名low: {物理基名low: int}}}——mux 数据值手填（R2-01；
形状 = legacy 的 `mux_data` 段多一层 view_id）。恢复顺序：**先**喂 analyze(mux_data=…) 再 restore_cols。

────────────── legacy（『排查(旧)』MainWindow）桶格式：只记录，不迁移/不读/不删 ──────────────
同一个 bucket 里还躺着旧门面 MainWindow._persist_edits 写的这些键（v2 默认策略=原样保留、
新代码一概不碰，换表时旧门面自己会读它们）：
    "edits":        {信号名low: [序列化 rowdict]}  # rowdict 见 ROW_PERSIST_KEYS
    "neg_only":     {信号名low: "first"|"all"}
    "mux_expected": {信号名low: {输入取值键: int}}
    "mux_neg":      [信号名low]
    "mux_data":     {信号名low: {物理基名low: int}}
    "mux_dropped":  {信号名low: [取值签名]}
    "mux_cleared":  [信号名low]
    "mux_user_vecs":{信号名low: [序列化 TestVector]}
    "signals_checked": [信号名（原样大小写）]
"""

import json
import os
import sys

from dreg_verify import expr as E
from dreg_verify import generator
from dreg_verify import sv_writer as W
from dreg_verify import truth_edit as TE
from dreg_verify import vectors as V

__all__ = [
    # ① 文件 IO 与序列化
    "DEFAULT_EDITS_PATH", "ROW_PERSIST_KEYS",
    "load_edits_file", "save_edits_file", "coerce_int_map",
    "serialize_rows", "deserialize_rows", "serialize_mux_vecs", "deserialize_mux_vecs",
    "serialize_view_edits", "restore_cols", "restore_view_edits",
    # ② 列模型 ↔ 向量
    "cols_from_vectors", "cols_to_vectors", "mux_derive", "mux_resync_cols",
    "is_dft_pitch_col", "recompute_col_an", "recompute_mux_user_auto",
    "compute_edited", "topout_edit_overrides", "page_edit_overrides",
    # ③ 编辑操作（纯逻辑）
    "col_names", "new_col_name", "add_col", "copy_cols", "del_cols",
    "fill_targets", "fill_expected", "add_negatives", "protected_negatives",
    "del_negatives", "rename_col", "set_mux_data_value", "set_mux_user_data",
    # ④ 派生量（v2 用）
    "fill_progress", "expected_cell_state", "input_cell_state", "input_cell_editable",
    "EXP_DFT", "EXP_NEG", "EXP_UNFILLED", "EXP_MATCH", "EXP_DIFF",
    "CELL_EDITABLE", "CELL_MUX_DATA", "CELL_DFT_GATE", "CELL_READONLY",
]

# 测试项编辑(含 designer 手填期望)的持久化文件：按 Excel 路径分桶，关 GUI 不丢、换表自动恢复。
# 与 gui.SETTINGS_PATH 分开存——编辑数据可能较大，且语义上是"劳动成果"而非"界面偏好"。
DEFAULT_EDITS_PATH = os.path.join(os.path.expanduser("~"), ".dreg_verify_edits.json")

# legacy rowdict 里需要持久化的字段（计算字段 correct/expected/_vec 等加载后重算，不落盘）
ROW_PERSIST_KEYS = ("kind", "wrong_value", "name", "user_added", "note", "designer_expected")


# ═══════════════════════ ① edits 文件 IO 与序列化 ═══════════════════════
def load_edits_file(path):
    """读取测试项编辑持久化文件。返回 {excel_path: {"edits": {...}, "neg_only": {...}}}。
    文件不存在/损坏 → {}（每次加载 Excel 都跑，绝不能崩）。"""
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
            return d if isinstance(d, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def save_edits_file(path, d, default_path=DEFAULT_EDITS_PATH):
    """写持久化文件。返回是否真的落盘了。

    测试环境(pytest)下默认不落盘(防污染用户真实编辑)；测试可 monkeypatch 调用方的 EDITS_PATH
    到临时文件启用——故 default_path 由调用方传入（gui 传它模块级的 _EDITS_PATH_DEFAULT）。"""
    if "pytest" in sys.modules and path == default_path:
        return False
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False)
        return True
    except Exception:  # noqa: BLE001
        return False


def coerce_int_map(d, lower=False):
    """把 {key: value} 安全转成 {key: int}，跳过非法数值（不崩）。返回 (干净 dict, 跳过个数)。
    用于恢复/导入外部 edits.json 时——损坏/手改的 mux_expected/mux_data 不能让整个加载流程崩
    （_restore_edits 每次加载 Excel 都跑，审查 #7）。"""
    out, bad = {}, 0
    for k, v in (d or {}).items():
        try:
            out[str(k).lower() if lower else str(k)] = int(v)
        except (ValueError, TypeError):
            bad += 1
    return out, bad


def serialize_rows(rows):
    """legacy rowdict 列表 → 可 JSON 化的精简结构（只留输入取值与用户意图，计算字段重算）。"""
    out = []
    for rd in rows:
        d = {"base_values": {k: int(v) for k, v in rd.get("base_values", {}).items()}}
        for k in ROW_PERSIST_KEYS:
            v = rd.get(k)
            if v is not None and v != "" and v is not False:
                d[k] = v
        d.setdefault("kind", "pos")
        out.append(d)
    return out


def deserialize_rows(rows_json):
    """JSON 结构 → legacy rowdict 列表（correct/expected 等由 _recompute_row 重算）。"""
    out = []
    for d in rows_json or []:
        if not isinstance(d, dict):
            continue
        rd = {"base_values": {str(k): int(v) for k, v in (d.get("base_values") or {}).items()},
              "kind": d.get("kind", "pos"), "note": d.get("note", "")}
        for k in ("wrong_value", "name", "designer_expected"):
            if d.get(k) is not None:
                rd[k] = d[k]
        if d.get("user_added"):
            rd["user_added"] = True
        out.append(rd)
    return out


def serialize_mux_vecs(vecs):
    """mux 用户手编/复制/负向列(TestVector) → 可 JSON 化结构（第二十八轮）。
    存 assignments(取值) + auto_out/期望/负向错值 + case_index(路由 case) + 名字。"""
    out = []
    for v in vecs or []:
        out.append({
            "assignments": {str(k): int(x) for k, x in v.assignments.items()},
            "exp_value": int(v.exp_value), "exp_width": int(v.exp_width),
            "is_negative": bool(v.is_negative),
            "neg_value": None if v.neg_value is None else int(v.neg_value),
            "neg_mode": v.neg_mode,
            "name": v.name, "note": v.note or "",
            "designer_expected": None if v.designer_expected is None else int(v.designer_expected),
            "case_index": None if v.case_index is None else int(v.case_index),
        })
    return out


def deserialize_mux_vecs(lst):
    """JSON 结构 → mux 用户列 TestVector 列表（损坏项跳过，不崩——每次加载 Excel 都跑）。"""
    out = []
    for d in (lst or []):
        if not isinstance(d, dict):
            continue
        try:
            assigns = {str(k): int(x) for k, x in (d.get("assignments") or {}).items()}
            v = V.TestVector(0, assigns, int(d.get("exp_value", 0)), int(d.get("exp_width", 1)),
                             is_negative=bool(d.get("is_negative")),
                             neg_value=None if d.get("neg_value") is None else int(d["neg_value"]),
                             neg_mode=d.get("neg_mode"),
                             note=d.get("note", "") or "", name=d.get("name"),
                             designer_expected=None if d.get("designer_expected") is None
                             else int(d["designer_expected"]),
                             case_index=None if d.get("case_index") is None else int(d["case_index"]))
        except (ValueError, TypeError):
            continue
        out.append(v)
    return out


def serialize_view_edits(edits):
    """SignalView.edits（{信号名low: {kind,src_out_name,name,renamed,cols,an}}）→ 可 JSON 化子桶。
    序列化只留可重算/用户意图字段(vals/exp/neg/name/user，auto 作兜底)；an 不落盘。

    ⚠ mux 列多存一段 `assign`（R2-02，**additive**，旧文件没有这段照常读）：屏幕上那份
    `vals` 的键集 = 真值表【输入行】的键集（`expansion["used_vars"]` + iddq 门那一行），
    而向量自己的 `vec.assignments` 是**另一个键集**（没有 `__dft_gate__`，却有死分支的
    `d:5…d:9`、少了没绑定的 `m57.d:0`）。生成器认的键是
    `generator.mux_assign_key(vec.assignments)` —— 按 `vals` 重建向量，重开后每一条
    手填期望都对不上号（实证：wl 七条 mux 信号 7/7 键集不符，.sv 104 行 → 71 行）。
    所以存的是**向量自己那份取值**，恢复时原样喂回去。
    """
    out = {}
    for name_low, ed in (edits or {}).items():
        cols = []
        is_mux = (ed.get("kind") == "mux")
        for c in (ed.get("cols") or []):
            vec = c.get("vec")
            cs = {
                "name": c.get("name"), "neg": bool(c.get("neg")),
                "vals": {str(k): int(v) for k, v in (c.get("vals") or {}).items()},
                "exp": None if c.get("exp") is None else int(c["exp"]),
                "auto": int(c.get("auto") or 0), "auto_w": int(c.get("auto_w") or 1),
                "user": bool(c.get("user")), "dft": bool(c.get("dft")),
                "case_index": (int(vec.case_index) if vec is not None
                               and getattr(vec, "case_index", None) is not None else None),
            }
            if is_mux and vec is not None:
                cs["assign"] = {str(k): int(v)
                                for k, v in (getattr(vec, "assignments", None) or {}).items()}
                # iddq 漏电态自检拍（与复制它得到的手编列）：门是 force 出来的，不在
                # assignments 里 —— 不存的话重开后那一拍的 `force …=1` 变成 `=0`，
                # 断言照旧写着 DFT 常量支的期望 → 仿真必 FAIL，而屏幕上完全看不出。
                ef = [[str(a), int(b), int(c)]
                      for (a, b, c) in (getattr(vec, "extra_forces", None) or [])]
                if ef:
                    cs["forces"] = ef
                rn = [str(x) for x in (getattr(vec, "release_nets", None) or [])]
                if rn:
                    cs["release"] = rn
            cols.append(cs)
        out[name_low] = {"kind": ed["kind"], "src_out_name": ed["src_out_name"],
                         "name": ed["name"], "renamed": bool(ed.get("renamed")), "cols": cols}
    return out


def restore_cols(an, specs):
    """存盘的列 spec → 列模型。logic 列重算 auto（改表后不陈旧；DFT 拍跳过），
    mux 列据 `assign`（没有就退回 vals）+ case_index 重建 TestVector。损坏项跳过，不崩。"""
    cols, ow = [], (an.get("out_width") or 1)
    for cs in (specs or []):
        if not isinstance(cs, dict):
            continue
        try:
            vals = {str(k): int(v) for k, v in (cs.get("vals") or {}).items()}
        except (ValueError, TypeError):
            continue
        exp = cs.get("exp")
        col = {"name": cs.get("name") or "T", "neg": bool(cs.get("neg")), "vals": vals,
               "exp": None if exp is None else int(exp),
               "auto": int(cs.get("auto") or 0), "auto_w": int(cs.get("auto_w") or ow),
               "user": bool(cs.get("user")), "dft": bool(cs.get("dft")), "vec": None}
        if an["editable"] == "logic":
            recompute_col_an(an, col)                    # 权威重算 auto（改表后不陈旧；DFT 拍跳过）
        elif an["editable"] == "mux":
            ci = cs.get("case_index")
            vec = V.TestVector(0, _restore_assign(cs, vals), col["auto"], col["auto_w"],
                               is_negative=bool(col["neg"]),
                               name=(col["name"] if col["user"] else None),
                               case_index=None if ci is None else int(ci))
            vec.dft_pitch = bool(col["dft"])
            vec.extra_forces = _restore_forces(cs)
            vec.release_nets = [str(x) for x in (cs.get("release") or [])]
            col["vec"] = vec
        cols.append(col)
    return cols


def _restore_assign(spec, vals):
    """mux 列 spec → 重建 TestVector 用的 assignments（R2-02）。

    新文件带 `assign`（= 落盘时那条向量自己的取值，键集与生成器一致）就用它；
    v1 时代 / v2 早期写的旧文件没有这段 → 退回按 `vals` 重建，行为与从前逐字节相同。"""
    asg = spec.get("assign")
    if isinstance(asg, dict):
        try:
            return {str(k): int(v) for k, v in asg.items()}
        except (ValueError, TypeError):
            pass                                        # 手改坏了 → 按 vals 兜底，不整列丢掉
    return dict(vals)


def _restore_forces(spec):
    """mux 列 spec 的 `forces` 段 → `TestVector.extra_forces`（旧文件没有这段 = 空）。"""
    out = []
    for it in (spec.get("forces") or []):
        try:
            out.append((str(it[0]), int(it[1]), int(it[2])))
        except (IndexError, TypeError, ValueError, KeyError):
            continue                                    # 坏一条跳一条，不连累整列
    return out


def restore_view_edits(view_bucket, models, analyze):
    """把序列化的视图子桶还原成 SignalView.edits（{信号名low: {...}}）。

    models:  [{"name": 原样信号名, …}]（当前表/覆盖度下的清单）；
    analyze: callable(真实信号名) → 分析结果 an（本函数吞掉它抛的任何异常，护栏3）。
    信号在当前表/覆盖度下找不到、分析失败或不可编辑 → 跳过（不崩）。
    """
    out = {}
    if not isinstance(view_bucket, dict) or not models:
        return out
    real_of = {m["name"].lower(): m["name"] for m in models}
    for name_low, ev in view_bucket.items():
        real = real_of.get(str(name_low).lower())
        if real is None or not isinstance(ev, dict):
            continue
        try:
            an = analyze(real)
        except Exception:   # noqa: BLE001 —— 护栏3
            an = None
        if an is None or not an.get("editable"):
            continue
        out[real.lower()] = {
            "kind": an["kind"], "src_out_name": an["src_out_name"], "name": an["name"],
            "cols": restore_cols(an, ev.get("cols")), "an": an,
            "renamed": an.get("renamed", False)}
    return out


# ═══════════════════════ ② 列模型 ↔ 向量 互转 ═══════════════════════
def cols_from_vectors(an, e_inputs):
    """分析结果 an 的 vectors → 列模型（打开信号/重新生成时的默认真值表）。"""
    cols = []
    groups = an["groups"]
    keys = [e["key"] for e in e_inputs]
    for vec in an["vectors"]:
        if an["editable"] == "mux":
            vals = {k: vec.assignments.get(k, 0) for k in keys}
        else:
            bv = V.vector_to_base_values(vec, groups)
            vals = {g["key"]: bv.get(g["key"], 0) for g in groups}
        gate = an.get("dft_gate")            # iddq 门值：本向量 extra_forces 里 force 的值(功能拍=透传,DFT 拍=1)
        if gate:
            gv = gate["transp"]
            for (wl, wv, _ww) in (getattr(vec, "extra_forces", None) or []):
                if wl == gate["wire_lhs"]:
                    gv = wv
            vals[gate["key"]] = gv
        neg = vec.is_negative
        exp = (vec.asserted_value if neg
               else (vec.designer_expected if vec.designer_expected is not None else None))
        cols.append({"name": W.test_label(vec), "neg": neg, "vals": vals,
                     "exp": exp, "auto": vec.exp_value, "auto_w": vec.exp_width,
                     "user": False, "vec": vec,
                     "dft": bool(getattr(vec, "dft_pitch", False))})
    return cols


def cols_to_vectors(an, cols):
    """列模型 → TestVector 列表（logic/register 路的 vector_overrides 回流）。"""
    vecs = []
    for i, c in enumerate(cols):
        nm = c["name"] if c["user"] else None
        if c["neg"]:
            v = V.make_vector_from_base_values(
                an["node"], an["bindings"], an["groups"], c["vals"], an["out_width"],
                index=i, expected_override=c["exp"], name=nm)
            # 用户把负向列期望恰好填成 auto → make_vector 不标负向(会静默退化成『通过』断言)。
            # 显式保住负向身份(=NEG-BROKEN：标负向但错值==正确值，仿真会过)，不静默丢用户的负向意图。
            if not v.is_negative and c["exp"] is not None:
                v.is_negative = True
                v.neg_value = c["exp"] & E.mask(v.exp_width)
                v.neg_mode = "value"
        else:
            v = V.make_vector_from_base_values(
                an["node"], an["bindings"], an["groups"], c["vals"], an["out_width"],
                index=i, name=nm, designer_expected=c["exp"])
        vecs.append(v)
    return vecs


def mux_derive(an, cols):
    """mux 列模型 → {cleared, dropped, expected, user_vecs}（mux 路的 edit_overrides 原料）。

    ⚠ 用户列的 `uv.name` 必须按**列名**贴（R2-03）：`clone_vector` 复制的是它被复制那一刻的
    源向量，源是自动列时 `name is None`（.sv 里退回自动 T<n> 标号）、源是别的用户列时带的是
    **那一列**的名字。屏幕表头写 U0、.sv 里却写 T25，改名成 MY_CASE 更是整个搜不到——
    三处标号必须是同一个。`cols_to_vectors`（logic 路）与 `restore_cols` 本来就是这个口径。"""
    auto_keys = {generator.mux_assign_key(v.assignments) for v in an["vectors"]}
    cur_auto_keys, expected, user_vecs = set(), {}, []
    for c in cols:
        vec = c["vec"]
        if vec is None:
            continue
        key = generator.mux_assign_key(vec.assignments)
        if c["user"]:
            uv = V.clone_vector(vec)
            uv.name = c["name"]                  # R2-03：标号以【列名】为准（自动列 name=None）
            if c["neg"]:
                uv.is_negative = True; uv.neg_value = c["exp"]; uv.neg_mode = "value"
            elif c["exp"] is not None:
                uv.designer_expected = c["exp"]
            user_vecs.append(uv)
        else:
            cur_auto_keys.add(key)
            if c["exp"] is not None and not c["neg"]:
                expected[key] = c["exp"]
    return {"cleared": len(cols) == 0, "dropped": list(auto_keys - cur_auto_keys),
            "expected": expected, "user_vecs": user_vecs}


def mux_resync_cols(an, old_cols, e_inputs):
    """mux 数据值改动后，把冻结的编辑列模型与【新分析】对齐 —— 所见即所得。

    自动生成列整表按新数据值重建（取值 by_base 同步 + auto_out 重算），只把用户意图贴回来：
    已手填的期望 / 负向标记；用户删掉的自动列不复活（清零过就还是零用例）；
    用户手编列原样保留（它们的数据值归自己管，与 legacy 的 _mux_user_vecs 一样不随整表走）。
    """
    old = list(old_cols or [])
    old_auto = {str(c.get("name")): c for c in old if not c.get("user")}
    cols = []
    for c in cols_from_vectors(an, e_inputs):
        o = old_auto.get(str(c["name"]))
        if o is None:
            continue                       # 老模型里没有这列 = 用户删过 → 不复活
        c["exp"] = o.get("exp")
        c["neg"] = bool(o.get("neg"))
        cols.append(c)
    cols.extend(c for c in old if c.get("user"))
    return cols


def is_dft_pitch_col(an, col):
    """该列是否 iddq 漏电态自检拍（iddq 门被 force 到非透传值=DFT 态、输出压常量）。
    节点表达式不建模 iddq 门，故重算 auto / 染色都要把它当特例，否则会假红当成反例。"""
    gate = an.get("dft_gate") if an else None
    if not gate:
        return False
    if col.get("dft"):
        return True
    return col.get("vals", {}).get(gate["key"]) != gate["transp"]


def recompute_col_an(an, col):
    """按当前输入取值重算该列 auto_out（只对 logic；mux/register 不动）。"""
    if an is None or an["editable"] != "logic":
        return
    if is_dft_pitch_col(an, col):
        return    # iddq DFT 拍：节点不含 iddq 门，重算会得功能值→与 force 的常量 0 假红；保留 0
    vec = V.make_vector_from_base_values(an["node"], an["bindings"], an["groups"],
                                         col["vals"], an["out_width"])
    col["auto"], col["auto_w"] = vec.exp_value, vec.exp_width


def recompute_mux_user_auto(an, col):
    """用户列 auto_out = 它路由的那条 case 的数据源取值——vec 克隆自某条真实 case，
    case_index 即路由 case，expansion['data_keys'][case_index] 即路由源的绑定键。"""
    vec = col.get("vec")
    exp = (an or {}).get("expansion") or {}
    dkeys = exp.get("data_keys") or []
    ci = getattr(vec, "case_index", None) if vec is not None else None
    if ci is None or not (0 <= ci < len(dkeys)):
        return
    val = vec.assignments.get(dkeys[ci], 0) & E.mask(col.get("auto_w") or 1)
    col["auto"] = val
    vec.exp_value = val


def compute_edited(edits, mux_data):
    """SignalView 的编辑状态 → 逐信号 edit 记录（再经 topout/page_edit_overrides 喂给生成器）。

    edits:    {信号名low: {kind, src_out_name, name, renamed, cols, an}}
    mux_data: {信号名low: {"src":…, "name":…, "data": {物理基名low: int}}}（会话档，mux 数据值手填）
    """
    out = {}
    for name_low, ed in (edits or {}).items():
        an = ed["an"]
        cols = ed["cols"]
        rec = {"kind": ed["kind"], "src_out_name": ed["src_out_name"], "name": ed["name"],
               "renamed": ed.get("renamed", False)}
        # dft 改名根 = 节点路（与 register 同：节点+向量），无论底层 kind 是 logic 还是 register
        if rec["renamed"] or ed["kind"] in ("logic", "register"):
            rec["cleared"] = (len(cols) == 0)
            rec["vectors"] = cols_to_vectors(an, cols)
        elif ed["kind"] == "mux":
            rec["mux"] = mux_derive(an, cols)
        out[name_low] = rec
    # N8：mux 数据值手填——注入对应 mux 信号的 rec；没在 edits 里(纯手填数据)的补一个 data-only rec
    for name_low, ent in (mux_data or {}).items():
        data = ent.get("data") or {}
        if not data:
            continue
        if name_low in out and out[name_low].get("mux") is not None:
            out[name_low]["mux"]["data"] = dict(data)
        elif ent.get("src"):
            out[name_low] = {"kind": "mux", "src_out_name": ent["src"], "name": ent["name"],
                             "renamed": False,
                             "mux": {"cleared": False, "dropped": [], "expected": {},
                                     "user_vecs": [], "data": dict(data)}}
    return out


def topout_edit_overrides(edited):
    """SignalView 收集的逐信号编辑 → topout.build_for_topout 的 edit_overrides。
    edited[name_low] = {'kind','src_out_name','cleared','vectors'(logic/reg),'mux':{...}}。"""
    vov, reg_ov = {}, {}
    mux_user, mux_exp, mux_drop, mux_cleared, mux_data = {}, {}, {}, [], {}
    for ed in (edited or {}).values():
        kind = ed["kind"]
        # dft 改名信号：build_for_topout 走 _topout_probe_block(reg 路)、按顶层名键 reg_overrides——
        # 编辑也必须走 reg 路按顶层名键，否则编辑落 vov[源名] 被改名路忽略=改了不生效(静默)。
        if ed.get("renamed"):
            reg_ov[ed["name"].lower()] = ed["vectors"]
        elif kind == "logic":
            vov[ed["src_out_name"].lower()] = ed["vectors"]
        elif kind == "register":
            reg_ov[ed["name"].lower()] = ed["vectors"]
        elif kind == "mux":
            src = ed["src_out_name"].lower()
            mx = ed.get("mux") or {}
            if mx.get("cleared"):
                mux_cleared.append(src)
            if mx.get("dropped"):
                mux_drop[src] = list(mx["dropped"])
            if mx.get("expected"):
                mux_exp[src] = dict(mx["expected"])
            if mx.get("user_vecs"):
                mux_user[src] = list(mx["user_vecs"])
            if mx.get("data"):                    # B2/N8：mux 数据值手填 {物理基名: int}
                mux_data[src] = dict(mx["data"])
    return {"vector_overrides": vov or None, "reg_overrides": reg_ov or None,
            "mux_user_vecs": mux_user or None, "mux_expected": mux_exp or None,
            "mux_dropped": mux_drop or None, "mux_cleared": mux_cleared or None,
            "mux_data": mux_data or None}


def page_edit_overrides(edited):
    """SignalView 收集的逐信号编辑 → pageviews build/report 的 edit_overrides（页本地无 register 根）。"""
    vov, mux_user, mux_exp, mux_drop, mux_cleared = {}, {}, {}, {}, []
    for ed in (edited or {}).values():
        if ed["kind"] == "logic":
            vov[ed["src_out_name"].lower()] = ed["vectors"]
        elif ed["kind"] == "mux":
            src = ed["src_out_name"].lower()
            mx = ed.get("mux") or {}
            if mx.get("cleared"):
                mux_cleared.append(src)
            if mx.get("dropped"):
                mux_drop[src] = list(mx["dropped"])
            if mx.get("expected"):
                mux_exp[src] = dict(mx["expected"])
            if mx.get("user_vecs"):
                mux_user[src] = list(mx["user_vecs"])
    return {"vector_overrides": vov or None, "mux_user_vecs": mux_user or None,
            "mux_expected": mux_exp or None, "mux_dropped": mux_drop or None,
            "mux_cleared": mux_cleared or None}


# ═══════════════════════ ③ 编辑操作（纯逻辑，弹框由 gui 决定） ═══════════════════════
def col_names(cols):
    """当前全部列名（集合）。"""
    return {c["name"] for c in (cols or [])}


def new_col_name(cols, suffix=""):
    """新列名：唯一性查【全集】（含 T<n> 自动列与 _NEG 负向列，大小写无关）。
    此前只查 "U%d"、查不到已存在的 U0_NEG → 批量加负向时每条都拿到同一个 U0_NEG。"""
    return TE.uniq_col_name(col_names(cols), suffix=suffix)


def add_col(an, cols, e_inputs):
    """新增一条测试列（输入全 0，auto_out 自动算，期望留空待填）。返回新列（未挂进 cols）。
    mux 不走这里（选路由 case 决定，不能凭空造输入）——gui 会改调 copy_cols。"""
    col = {"name": new_col_name(cols), "neg": False,
           "vals": {e["key"]: 0 for e in e_inputs}, "exp": None,
           "auto": 0, "auto_w": an["out_width"] or 1, "user": True, "vec": None}
    recompute_col_an(an, col)
    return col


def copy_cols(cols, sel):
    """复制选中列（未选=复制最后一列）。返回新造的列列表（未挂进 cols）。

    克隆出来的向量要跟着改名（R2-03 同口径）：不然 .sv 里新列顶着**源列**的标号。"""
    out = []
    sel = sel or [len(cols) - 1]
    for j in sel:
        if j < 0 or j >= len(cols):
            continue
        src = cols[j]
        nm = new_col_name(list(cols) + out)
        out.append({"name": nm, "neg": src["neg"],
                    "vals": dict(src["vals"]), "exp": src["exp"],
                    "auto": src["auto"], "auto_w": src["auto_w"], "user": True,
                    "dft": bool(src.get("dft")),      # R2-02：DFT 拍的复制品还是 DFT 拍
                    "vec": _clone_named(src["vec"], nm)})
    return out


def _clone_named(vec, name):
    """克隆一条向量并把标号贴成 `name`（R2-03：列名 = .sv 标号，两边只有一个口径）。"""
    if vec is None:
        return None
    uv = V.clone_vector(vec)
    uv.name = name
    return uv


def del_cols(cols, sel):
    """删掉下标在 sel 里的列。返回新列表。"""
    sel = set(sel or ())
    return [c for i, c in enumerate(cols) if i not in sel]


def fill_targets(cols):
    """『auto_out → 期望』能填的列 = 正向 且 期望未手填。"""
    return [c for c in (cols or []) if not c["neg"] and c["exp"] is None]


def fill_expected(cols):
    """把 auto_out 填进所有未手填的正向列期望（就地改）。返回填了几条。"""
    targets = fill_targets(cols)
    for c in targets:
        c["exp"] = c["auto"]
    return len(targets)


def add_negatives(cols, sel, all_positive):
    """加负向（选中列 / 全部正向用例）。返回 (新造的负向列列表, 被跳过条数)——列未挂进 cols。

    三条语义与『排查(旧)』一致（挑列在 truth_edit.plan_negatives）：
      · 只对【正向】列造负向（此前会给负向列再造负向：全选一次 12→24→48）；
      · 同一组输入取值已经有负向 → 跳过，不叠重复断言；
      · 列名对全集唯一（此前每条都叫 U0_NEG，.sv 标号全撞）。
    """
    targets, skipped = TE.plan_negatives(cols, sel, all_positive=all_positive)
    out = []
    for j in targets:
        src = cols[j]
        # m6：错值防撞——避开 auto_out(=src['auto']) 与 designer 手填期望(=src['exp'])两个『正确值』，
        # 否则 ~auto 恰=designer 期望时反例会 PASS=NEG-BROKEN(静默)。复用 vectors.make_negative 的防撞
        # 逻辑(避 correct + designer)，不再裸 ~auto。src['exp']=None(无手填)时退化成 ~auto、行为不变。
        _tmpv = V.TestVector(0, {}, src["auto"], src["auto_w"], designer_expected=src["exp"])
        wrong = V.make_negative(_tmpv, mode="invert").neg_value
        nm = new_col_name(list(cols) + out, "_NEG")
        out.append({"name": nm, "neg": True,
                    "vals": dict(src["vals"]), "exp": wrong,
                    "auto": src["auto"], "auto_w": src["auto_w"], "user": True,
                    "dft": bool(src.get("dft")),              # R2-02：向量身份跟着列走
                    "vec": _clone_named(src["vec"], nm)})     # R2-03：标号跟列名走
    return out, skipped


def protected_negatives(cols):
    """"值得保护"的负向列 = 自定义命名 或 手填过错值（误删就是丢用户的活）。
    自动造的负向叫 T<n>_NEG / U<n>_NEG、错值 = make_negative 的防撞取反值。"""
    out = []
    for c in (cols or []):
        if not c.get("neg"):
            continue
        named = not TE.is_auto_neg_name(c.get("name"))
        _tmpv = V.TestVector(0, {}, c["auto"], c["auto_w"])
        hand = (c.get("exp") is not None
                and c["exp"] != V.make_negative(_tmpv, mode="invert").neg_value)
        if named or hand:
            out.append(c)
    return out


def del_negatives(cols):
    """删掉全部负向列（正向保留）。返回 (新列表, 删掉几条)。"""
    negs = [c for c in (cols or []) if c["neg"]]
    return [c for c in (cols or []) if not c["neg"]], len(negs)


def rename_col(cols, j, new_name):
    """改列名（就地改）。返回 (True, 最终名) 或 (False, 给用户看的失败原因)。

    校验三道关在 truth_edit.check_col_name：非法字符清成下划线且不得为空、不许占 T<编号>
    这个自动测试保留名、不得与其它列的最终标号重名(否则 .sv 里两块同名)。
    """
    if j < 0 or not cols or j >= len(cols):
        return False, "列下标越界"
    col = cols[j]
    others = [c["name"] for i, c in enumerate(cols) if i != j]
    good, info = TE.check_col_name(new_name, others, negative=bool(col["neg"]))
    if not good:
        return False, info
    col["name"] = info
    return True, info


def set_mux_data_value(mux_data, name_low, src_out_name, name, base_low, width, text):
    """mux 数据行手填(N8/B2) 的【数据部分】：按物理基名存进会话档 mux_data（空文本=恢复自动）。

    就地改 mux_data；解析不出数值 → 抛 ValueError（调用方负责提示+还原整表）。
    """
    ent = mux_data.setdefault(name_low, {"src": src_out_name, "name": name, "data": {}})
    txt = (text or "").strip()
    if txt == "":
        ent["data"].pop(base_low, None)
    else:
        val = TE.parse_int(txt)
        ent["data"][base_low] = val & E.mask(width)
    if not ent["data"]:
        mux_data.pop(name_low, None)
    return ent


def set_mux_user_data(an, col, key, width, text):
    """mux【用户手编列】数据行手填：只改本列该键的取值 + 按路由源重算 auto_out，不整表联动
    （对齐 legacy _on_mux_user_data_changed；自动生成列才走 set_mux_data_value 的 by_base 同步）。

    就地改 col/col['vec']；解析不出数值 → 抛 ValueError。列没有 vec（非 mux）→ 返回 False 不动。
    """
    vec = col.get("vec")
    if vec is None:
        return False
    val = TE.parse_int(text) & E.mask(width)
    vec.assignments[key] = val
    col["vals"][key] = val
    recompute_mux_user_auto(an, col)
    return True


# ═══════════════════════ ④ 给 v2 用的派生量 ═══════════════════════
# 期望格的判定结果（颜色由 gui 映射，本层只说"是什么"）
EXP_DFT = "dft"              # iddq 漏电态自检拍（工具自动加，不是反例）
EXP_NEG = "neg"              # 负向/反例列（故意填错期望）
EXP_UNFILLED = "unfilled"    # 期望未手填（生成时 auto_out 兜底）
EXP_MATCH = "match"          # designer 手填 == auto_out
EXP_DIFF = "diff"            # designer 手填 != auto_out（仿真 FAIL 恰恰说明表达式与意图不符）

# 输入格的角色
CELL_EDITABLE = "editable"   # logic 输入行，可改
CELL_MUX_DATA = "mux_data"   # mux 数据角色行，可手填
CELL_DFT_GATE = "dft_gate"   # iddq DFT 门行，只读
CELL_READONLY = "readonly"   # 其余只读（mux 控制/选择位等）


def fill_progress(cols):
    """期望手填进度 (n_filled, n_total)：designer 手填了几条 / 共几条正向
    （其余生成时 auto_out 兜底）。算法与 legacy _update_ti_header 一致：只数正向列。"""
    pos = [c for c in (cols or []) if not c.get("neg")]
    return sum(1 for c in pos if c.get("exp") is not None), len(pos)


def expected_cell_state(an, col):
    """该列【期望格】的状态（gui 据此染色，v2 同一套判定）：
    EXP_DFT > EXP_NEG > EXP_UNFILLED > EXP_MATCH / EXP_DIFF（优先级即判定顺序）。"""
    if is_dft_pitch_col(an, col):
        return EXP_DFT
    if col.get("neg"):
        return EXP_NEG
    if col.get("exp") is None:
        return EXP_UNFILLED
    m = E.mask(col.get("auto_w") or 1)
    return EXP_MATCH if (col["exp"] & m) == (col.get("auto", 0) & m) else EXP_DIFF


def input_cell_editable(an, e):
    """该输入行的格子是否可编辑：logic 输入行可编辑；mux 数据角色行可手填(控制位/选择位只读)。"""
    return bool((an["editable"] == "logic" and e.get("editable"))
                or (an["editable"] == "mux" and e.get("mux_data_base")))


def input_cell_state(an, e):
    """该输入行的角色（gui 据此染色/加提示）。"""
    if e.get("is_dft_gate"):
        return CELL_DFT_GATE
    if an["editable"] == "mux" and e.get("mux_data_base"):
        return CELL_MUX_DATA
    if input_cell_editable(an, e):
        return CELL_EDITABLE
    return CELL_READONLY
