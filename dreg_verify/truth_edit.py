# -*- coding: utf-8 -*-
"""真值表编辑层的【纯逻辑】——不依赖 Qt，新门面(SignalView)与『排查(旧)』共用同一份实现。

放这里的东西只有三类（都是此前两套门面各写一遍、语义已经漂移的）：
  ① 用户输入的数值写法解析（16'hA / 0x.. / hA / 'b.. / 'd.. / 0b.. / 十进制 / 裸 hex）；
  ② 测试列名的生成 / 清洗 / 校验（唯一性要查【全集】，包括 _NEG 后缀的列）；
  ③ 加负向时"哪些列该造、哪些已有负向要跳过"的判定。

不碰 Qt、不碰 Excel、不碰生成器：可以直接单测（tests/test_truth_edit.py），GUI 重做后照样复用。
"""

import re

__all__ = ["parse_int", "parse_cell", "uniq_col_name", "sanitize_name",
           "is_reserved_test_name", "is_auto_neg_name", "final_col_name",
           "check_col_name", "vals_key", "plan_negatives"]


# ───────────────────────── ① 数值写法 ─────────────────────────
_RE_TICK_H = re.compile(r"'h([0-9a-f]+)$")
_RE_TICK_B = re.compile(r"'b([01]+)$")
_RE_TICK_D = re.compile(r"'d(\d+)$")
_RE_DEC = re.compile(r"\d+$")
_RE_HEX = re.compile(r"[0-9a-f]+$")


def parse_int(s):
    """宽松解析用户在真值表里填的数值，支持 IC 工程师常写的 8 种写法：

        16'hA / 'hA（带位宽的 Verilog 十六进制）      → 10
        'b1010 / 0b1010（二进制）                     → 10
        'd9（十进制）                                  → 9
        0xA / hA（十六进制前缀）                       → 10
        9（十进制）/ ab（裸十六进制）

    空串 = 0（与「这一格没填」由调用方用 parse_cell 区分）。
    识别不了 → 抛 ValueError（**绝不返回 0/None 静默吞值**，调用方必须让用户看见）。
    """
    t = str(s).strip().lower().replace(" ", "")
    if t == "":
        return 0
    if t in ("0x", "0b"):        # 只有前缀没数字 = 打了一半，别让裸 hex 兜底把 "0b" 读成 11
        raise ValueError("只有前缀没有数值: %r" % s)
    m = _RE_TICK_H.search(t)
    if m:
        return int(m.group(1), 16)
    m = _RE_TICK_B.search(t)
    if m:
        return int(m.group(1), 2)
    m = _RE_TICK_D.search(t)
    if m:
        return int(m.group(1), 10)
    if t.startswith("0b") and t[2:] and set(t[2:]) <= {"0", "1"}:
        return int(t, 2)         # 仅当 0b 后全是 0/1 才当二进制；'0bc' 之类仍按裸 hex（向后兼容）
    if t.startswith("0x") and t[2:]:
        return int(t, 16)
    if t.startswith("h") and t[1:]:
        return int(t[1:], 16)
    if _RE_DEC.match(t):
        return int(t, 10)
    if _RE_HEX.match(t):
        return int(t, 16)
    raise ValueError("无法识别 %r（用 0x.. 或 16'h.. 或纯数字）" % s)


def parse_cell(s):
    """真值表单元格：空/全空白 = 这一格没填 → None；其余同 parse_int（非法照样抛 ValueError）。"""
    if str(s or "").strip() == "":
        return None
    return parse_int(s)


# ───────────────────────── ② 列名 ─────────────────────────
_RE_AUTO_NAME = re.compile(r"(?i)^t\d+(_neg)?$")
_RE_AUTO_NEG = re.compile(r"(?i)^[tu]\d+_neg$")


def uniq_col_name(existing, prefix="U", suffix="", start=0):
    """造一个不与 existing 里任何列名冲突的 <prefix><n><suffix>。

    唯一性查【全集】（大小写无关，含 _NEG 后缀的负向列名）——只查 "U%d" 是此前批量加负向
    全撞成同一个 U0_NEG 的根因。
    """
    taken = {str(x).strip().upper() for x in (existing or ())}
    n = int(start)
    while True:
        cand = "%s%d%s" % (prefix, n, suffix)
        if cand.upper() not in taken:
            return cand
        n += 1


def sanitize_name(s):
    """把用户输入清成合法的 SV 标号片段（只留字母/数字/下划线）。"""
    return re.sub(r"[^0-9A-Za-z_]", "_", str(s).strip())


def is_reserved_test_name(nm):
    """T<编号> / T<编号>_NEG 是自动测试的保留命名——手填会在列位移后撞名，禁止。"""
    return bool(_RE_AUTO_NAME.match(str(nm or "")))


def is_auto_neg_name(nm):
    """是不是【工具自动造】的负向列名（T<n>_NEG / U<n>_NEG）。
    反之 = 用户自己改过名 → 属"值得保护"的负向，删之前要先问一声。"""
    return bool(_RE_AUTO_NEG.match(str(nm or "")))


def final_col_name(nm, negative=False):
    """列的最终标号：负向列自动带 _NEG（用户已经自己写了 NEG 结尾则不重复加）。"""
    nm = str(nm or "")
    if negative and not nm.upper().endswith("NEG"):
        return nm + "_NEG"
    return nm


def check_col_name(new_name, others, negative=False):
    """校验用户填的新列名。others = 其它列的【最终标号】集合（不含本列）。

    返回 (True, 最终名) 或 (False, 给用户看的失败原因)。三道关（与『排查(旧)』同一套）：
      空/全非法字符 → 拒；T<编号> 保留名 → 拒；与其它列最终标号重名（会撞 .sv 标号）→ 拒。
    """
    nm = sanitize_name(new_name)
    if not nm:
        return False, "名字为空或全是非法字符（只能用字母/数字/下划线）"
    if is_reserved_test_name(nm):
        return False, "名字 %s 与自动测试命名(T<编号>)冲突，请换个名字" % nm
    final = final_col_name(nm, negative)
    taken = {str(x).strip().upper() for x in (others or ())}
    if final.upper() in taken:
        return False, "名字与列 %s 重复(会造成 .sv 标号冲突)" % final
    return True, final


# ───────────────────────── ③ 加负向的挑列 / 去重 ─────────────────────────
def vals_key(vals):
    """一列的"输入取值"指纹——判定"这组取值是不是已经有负向了"用。"""
    out = []
    for k, v in (vals or {}).items():
        try:
            out.append((str(k), int(v)))
        except (TypeError, ValueError):
            out.append((str(k), v))
    return tuple(sorted(out, key=lambda x: x[0]))


def plan_negatives(cols, sel=None, all_positive=False):
    """挑出真正要造负向的【源列下标】，语义与『排查(旧)』的"加负向(选中)"一致：

      · 只对【正向】列造负向——负向列不再套娃（此前全选一次 12→24→48 的根因）；
      · 选中列里一条正向都没有 → 退回本信号首条正向（一条正向都没有 → 空）；
      · 同一组输入取值已经有负向 → 跳过，不给同一条用例叠重复断言。

    cols: [{'neg': bool, 'vals': {...}}, …]（多余字段忽略）。
    sel:  选中的列下标（None/空 = 未选中）。all_positive=True 时忽略 sel，取全部正向列。
    返回 (要造负向的下标列表, 被跳过的条数)。
    """
    cols = list(cols or [])
    pos_idx = [i for i, c in enumerate(cols) if not c.get("neg")]
    if all_positive:
        want = list(pos_idx)
    else:
        sel = [i for i in (sel or []) if 0 <= i < len(cols)]
        want = [i for i in sel if not cols[i].get("neg")]
        if not want:
            want = pos_idx[:1]
    existing = {vals_key(c.get("vals")) for c in cols if c.get("neg")}
    targets, skipped = [], 0
    for i in want:
        key = vals_key(cols[i].get("vals"))
        if key in existing:
            skipped += 1
            continue
        existing.add(key)
        targets.append(i)
    return targets, skipped
