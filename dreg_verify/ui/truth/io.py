# -*- coding: utf-8 -*-
"""truth/io.py —— 真值表的剪贴板 / 导入 / 导出（**Qt-free**）。

架构 §6.15，契约 TRUTH_IO 6 条（C-136/C-137/C-138/C-139/C-294/C-298）。

分工（护栏）：

* **本模块不落格、不碰撤销栈**。`paste_plan` 只算「哪一格该写什么」，谁去写、裹不裹成一步
  撤销是 `truth/model.py` 的事（C3-int 会把 `model.paste_tsv` 改成调这里的 `paste_plan`，
  两边规则一字不差由 `test_c294_paste_plan_equivalent_to_model_paste` 钉死）。
* **本模块不 import PySide6**。`indexes` 只当作「有 `.row()` / `.column()` 的对象」，
  model 只当作 `contracts.TruthModelProto` 的面来调（`row_kind` / `cell_state` / `col_state`
  / `cols` / `editable_kind` / `rowCount` / `columnCount` / `data`）。
* **读文件在这边、写格子在 model 那边**（C-298）：`read_expectations` 只把
  {列名: 值} 读出来，认不认得上这个列名是 `model.apply_expectations` 报。

导出 CSV 的两条口径（别混）：

1. **行**（第一列的信号/字段名）走 `csv_input_rows(an)` —— 与 v1 `SignalView.e_inputs`
   逐字节同（真名带位宽，不带真值表冻结列那个 `(角色 · 端口)` 后缀）。CSV 是给
   designer 拿去比对 / 回填的交换件，不是界面。
2. **列**（每列一条测试）：logic / register / dft 走 model 现在这张表（所见即所得）；
   **mux 走产物**（C-139）—— 列集 = `exports.render_sv(provider, only=[name], edited=…)`
   那次 build 里该信号实际渲染进 .sv 的向量，经 `exports.columns_from_mux_vectors` 折成列。
   mux 的列不能拿 model 的 `cols()`：整信号负向（`which=first` 追加一条）、负向去重、
   T 编号重排、iddq 自检拍都发生在 build 里，编辑器手上那份看不到，照它导出的 CSV 与
   .sv 对不上 —— 而 designer 拿 CSV 就是来核对 .sv 的。
"""

import csv
import os
from dataclasses import dataclass, field

from ... import exports as X
from ... import inputs_table as IT
from ... import truth_edit as TE
from .. import contracts
from .. import terms

__all__ = ["PastePlan", "parse_tsv", "copy_tsv", "paste_plan", "paste_report_text",
           "plan_added_names", "read_expectations", "csv_input_rows", "signal_csv_columns",
           "export_signal_csv", "COV_KEYS", "PENDING_TERMS"]


#: `ui/terms.py` 里还没有、但本模块要用的文案（C2-int 正在并行改 terms.py，这波不碰它）。
#: C3-int 负责搬进 `terms.py` 并删掉这张表；取值一律经 `_t()`（terms 有就用 terms 的）。
#: ⚠ 前两条与 `truth/model.py` 的 `PENDING_TERMS` 是**同名同值**的两份——两边各自兜底，
#: 搬进 terms.py 之前谁改一边都会被 `test_c294_paste_plan_equivalent_to_model_paste`
#: （逐字比报告文案）当场抓住。
PENDING_TERMS = {
    # 粘贴结果说明的两截尾巴（`terms.TRUTH_PASTE_REPORT_FMT` 的 {added} / {skipped} 占位）
    "TRUTH_PASTE_ADDED_FMT": "，新增列 {names}",
    "TRUTH_PASTE_SKIPPED_FMT": "，跳过 {n} 格（只读行/列）",
    # 跳过某一格的原因（给用户逐格看的，不进汇总那句）
    "TRUTH_PASTE_SKIP_READONLY": "只读格（auto_out 行 / 只读输入行 / 自检拍列）",
    "TRUTH_PASTE_SKIP_PARSE_FMT": "写法没认出来：{text}",
    # 导入期望（C-298）：读文件这半边的提示，逐条给名字 + 原因，不报「成功 N 条」了事
    "TRUTH_IMPORT_NO_COLUMNS": "第一行是空的——第一行要是列名（导出的 CSV 原样改就行）",
    "TRUTH_IMPORT_NO_EXP_ROW": "文件里没有「期望」行，也不止一行数据——没取到任何期望值",
    "TRUTH_IMPORT_ONE_ROW_FMT": "文件里没有「期望」行，按唯一的那行「{label}」取值",
    "TRUTH_IMPORT_BAD_CELL_FMT": "列「{name}」的写法没认出来：{text}（这一列跳过）",
    "TRUTH_IMPORT_DUP_COL_FMT": "列名「{name}」在文件里出现了不止一次，只取第一处",
    "TRUTH_IMPORT_EMPTY_FILE": "这个文件里一行都没有",
}

#: CSV / xlsx 里「期望」那一行的行名——按这个顺序找，第一个命中的算数。
#: 与 `exports.signal_csv_text` 写出来的行名对齐（导出的表原样改回来就能导入）。
EXP_ROW_PREFIXES = ("期望(进.sv)", "期望(bin)", "期望")
#: 长得像「期望」但不是取值的行（`期望来源` 是 C-138 那行文字说明）
EXP_ROW_EXCLUDE = ("期望来源",)


def _t(_key, **fmt):
    """文案取值：`terms` 里有就用 `terms` 的，没有退回 `PENDING_TERMS`（C3-int 搬完即一致）。

    形参叫 `_key` 不叫 `name`：这里的占位符里就有一个 `{name}`（「列『T2』的写法没认出来」），
    两者重名会直接 `TypeError: got multiple values`。
    """
    s = getattr(terms, _key, None)
    if s is None:
        s = PENDING_TERMS[_key]
    return s.format(**fmt) if fmt else s


# ═════════════════════════ 一、TSV（Excel 剪贴板）═════════════════════════
def parse_tsv(text):
    """Excel 口径的 TSV 解析：`\\r\\n` / `\\r` / `\\n` 都算换行，制表符分列，**空串格保留**
    （中间的空格 = 「这一格不填」，不能被吃掉），只剔末尾的空行。

    返回 `[[格文本, …], …]`。与 `truth/model.py::paste_tsv` 开头那几行同一套规则。
    """
    rows = [ln.split("\t") for ln in
            str(text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    while rows and rows[-1] == [""]:
        rows.pop()
    return rows


def copy_tsv(model, indexes):
    """选区 → TSV（C-293/C-294 的复制半边）：按选区行列的**外接矩形**出，空格子留空串。

    与 `TruthModel.copy_tsv` 同行为（`test_copy_tsv_matches_model` 逐字比）。
    `indexes` 只当作「有 `.row()` / `.column()`（可能还有 `.isValid()`）的对象」，
    取值走 `model.data(ix)` 的默认 role（= DisplayRole），本模块不 import Qt。
    """
    cells = {}
    for ix in (indexes or ()):
        if ix is None:
            continue
        valid = getattr(ix, "isValid", None)
        if valid is not None and not valid():
            continue
        cells[(ix.row(), ix.column())] = model.data(ix) or ""
    if not cells:
        return ""
    rs = [r for (r, _c) in cells]
    cs = [c for (_r, c) in cells]
    lines = []
    for r in range(min(rs), max(rs) + 1):
        lines.append("\t".join(cells.get((r, c), "")
                               for c in range(min(cs), max(cs) + 1)))
    return "\n".join(lines)


@dataclass
class PastePlan:
    """一次粘贴的**规划**（不落格）。

    cells         [(行, 列, 该写的文本)] —— 能落的格子，**含「值没变」的那些**
                  （`len(cells)` 就是报告里那个「落了 N 格」，与 model 的 n_ok 同口径）
    new_cols      要先追加几条测试列（超出现有列数的部分，C-294「超列自动追加」）
    rejected_rows 被拒的**源行**下标（超出真值表行数的那些）；非空 = 整次粘贴不做
    skipped       [(行, 列, 原因)] —— 只读格 / 写法没认出来的格子
    report_text   给用户看的那一句（`terms.TRUTH_PASTE_REPORT_FMT` / OVERFLOW）
    """
    cells: list = field(default_factory=list)
    new_cols: int = 0
    rejected_rows: list = field(default_factory=list)
    skipped: list = field(default_factory=list)
    report_text: str = ""

    @property
    def ok(self):
        """整次粘贴做不做得成（超行 = 整次拒绝，与 `model.paste_tsv` 的返回值同口径）。"""
        return not self.rejected_rows


def _cell_editable(model, r, c):
    """这一格能不能写 —— 与 `TruthModel._is_editable` 同判据，只经 Proto 的公开面。

    （`flags()` 是 Qt 的，本模块不 import Qt；`test_cell_editable_matches_model_flags`
    拿两张 mirror 的信号逐格对着 `model.flags()` 断言相等，漂了当场红。）
    """
    if not (0 <= c < model.columnCount()) or not (0 <= r < model.rowCount()):
        return False
    if not model.editable_kind():                       # C-134：不可建信号 → 全表只读
        return False
    kind = model.row_kind(r)
    if kind == contracts.TruthRowKind.AUTO:
        return False                                     # auto_out 行恒只读（C-077）
    if model.col_state(c) == contracts.TruthColState.DFT:
        return False                                     # iddq 自检拍列整列只读（C-129/C-130）
    if kind == contracts.TruthRowKind.EXP:
        return True
    return model.cell_state(r, c) == "editable"


def _new_col_editable(model, r, ref_col):
    """**还没加出来的那条列**里第 r 行能不能写。

    输入行的可写性只跟【行】走（`edits.input_cell_editable` 只看输入行的角色），列只在
    「是不是 iddq 自检拍列」这一点上说话 —— 所以拿表上任一条非自检拍列当参照即可。

    表上一条列都没有时（清零过 → 零用例，然后从 Excel 整片粘回来）没有参照列可看：
    此时能加出列的只有 logic（`edits.add_col`；mux 的 `copy_cols` 没有源列可复制，
    压根加不出来），而 logic 的输入行**除 iddq 门行外全可编辑**（`truth/rows.py` 给
    每个分组 `editable=True`，只有门行是 False）—— 门行按行标签里的
    `inputs_table.ROLE_DFT_GATE` 认（那一行的标签就是 `vheader_display` 用这个常量拼的）。

    ⚠ 残留假设：新加出来的那条列不是自检拍列。`edits.add_col` 给新列的门值是 0，
    只有 iddq **透传值 = 1** 的信号会让它变成自检拍列（两张 mirror 上透传值都是 0，
    没有实例）；真出现时 model 会整列跳过、比这里算的少落几格。
    """
    if not model.editable_kind():
        return False
    kind = model.row_kind(r)
    if kind == contracts.TruthRowKind.AUTO:
        return False
    if kind == contracts.TruthRowKind.EXP:
        return True
    if ref_col is not None:
        return model.cell_state(r, ref_col) == "editable"
    if model.columnCount() or model.editable_kind() != "logic":
        return False                      # 表上全是自检拍列 → 判不了，当只读（保守）
    return IT.ROLE_DFT_GATE not in str(model.row_label(r) or "")


def plan_added_names(model, n):
    """追加 n 条测试列会拿到的【最终标号】——`model.append_test_column(n)` 的**预测**。

    规划期报不出列名的话，那句「新增列 U0, U1」就只能事后补，而粘贴报告是一次性给的。
    预测规则 = `edits.new_col_name`（`truth_edit.uniq_col_name` 查全集）：
      · logic：`edits.add_col` 造正向列，名字就是 U<n>；
      · mux  ：`edits.copy_cols` 复制**最后一列**，负向标记跟着源列走（U<n>_NEG）；
        表上一条列都没有时 `copy_cols` 什么也造不出来 —— 预测同样给空，别报个假名字。
    `test_plan_added_names_matches_model` 拿 logic/mux 两条信号对着真 `append_test_column` 比。
    """
    n = int(n or 0)
    kind = model.editable_kind()
    if not kind or n <= 0:
        return []
    cols = list(model.cols())
    if kind == "mux" and not cols:
        return []                       # copy_cols 没有源列可复制 → 一条也加不出来
    raw = [str(c.get("name") or "") for c in cols]
    neg = bool(cols[-1].get("neg")) if (kind == "mux" and cols) else False
    out = []
    for _ in range(n):
        nm = TE.uniq_col_name(raw + out)
        out.append(nm)
    return [TE.final_col_name(nm, neg) for nm in out]


def paste_report_text(n_cells, added_names, n_skipped):
    """粘贴结果那一句（C-294）。`added_names` 给真实落下来的列名时文案就是最终态——
    C3-int 让 `model.paste_tsv` 调本函数时把 `append_test_column` 的返回值喂进来即可。"""
    added = list(added_names or ())
    return _t("TRUTH_PASTE_REPORT_FMT", n=int(n_cells),
              added=(_t("TRUTH_PASTE_ADDED_FMT", names=", ".join(added)) if added else ""),
              skipped=(_t("TRUTH_PASTE_SKIPPED_FMT", n=int(n_skipped)) if n_skipped else ""))


def paste_plan(model, text, r0, c0):
    """TSV 粘贴的规划（C-294）：落在活动格 (r0, c0)；**超列追加列、超行整次拒绝**。

    超行为什么拒绝而不是追加：真值表的行是【输入信号】，凭空加一行等于凭空多一根输入，
    那是 Excel 表的事，不是这里能补的。只读格（auto_out 行 / 只读输入行 / 自检拍列）与
    写法认不出来的格子逐个跳过并给原因，**整次粘贴照常落别的格**（C-083 的同一条原则：
    认不出来绝不静默吞成 0）。

    返回 `PastePlan`。本函数**一格都不写**、一步撤销都不占。
    """
    rows = parse_tsv(text)
    if not rows:
        return PastePlan(report_text=paste_report_text(0, [], 0))
    r0, c0 = max(0, int(r0)), max(0, int(c0))
    n_rows = model.rowCount()
    if r0 + len(rows) > n_rows:
        # 拒掉的是【落不下的那些源行】—— 报给用户的是整次拒绝（与 model 同），
        # 但把具体哪几行落不下留在 plan 里，面板要点名时不用自己再算一遍。
        rejected = [i for i in range(len(rows)) if r0 + i >= n_rows]
        return PastePlan(rejected_rows=rejected,
                         report_text=terms.TRUTH_PASTE_OVERFLOW_ROWS_FMT.format(
                             rows=len(rows), max=n_rows))

    n_old = model.columnCount()
    need = c0 + max(len(x) for x in rows) - n_old
    new_cols = need if need > 0 else 0
    added = plan_added_names(model, new_cols)      # 真加得出来几条（mux 零列表时一条也加不出）
    n_have = n_old + len(added)                    # 追加之后一共有几列（落点判定按这个）
    ref_col = next((j for j in range(n_old)
                    if model.col_state(j) != contracts.TruthColState.DFT), None)

    cells, skipped = [], []
    for dr, line in enumerate(rows):
        for dc, txt in enumerate(line):
            r, c = r0 + dr, c0 + dc
            can = (_cell_editable(model, r, c) if c < n_old
                   else (c < n_have and _new_col_editable(model, r, ref_col)))
            if not can:
                skipped.append((r, c, _t("TRUTH_PASTE_SKIP_READONLY")))
                continue
            try:
                TE.parse_int(txt)                  # 空串 = 0 / 期望格的「清空」，都不算失败
            except ValueError:
                skipped.append((r, c, _t("TRUTH_PASTE_SKIP_PARSE_FMT", text=txt)))
                continue
            cells.append((r, c, txt))

    return PastePlan(cells=cells, new_cols=new_cols, skipped=skipped,
                     report_text=paste_report_text(len(cells), added, len(skipped)))


# ═════════════════════════ 二、导入期望（C-298）═════════════════════════
def _rows_from_csv(path):
    """CSV → 二维文本表。`utf-8-sig` 容忍 Excel 存出来的 BOM（导出那头写的就是它）。"""
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        return [list(r) for r in csv.reader(f)]


def _rows_from_xlsx(path):
    """xlsx → 二维值表（**只读第一张表**）。openpyxl 惰性 import：没装它也不该拦住 CSV 那条路。"""
    import openpyxl                       # noqa: PLC0415  惰性：只有真读 xlsx 才需要
    wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
    try:
        ws = wb[wb.sheetnames[0]]
        return [list(r) for r in ws.iter_rows(values_only=True)]
    finally:
        wb.close()


def _cell_text(v):
    """表格里一个格子 → 文本。xlsx 读出来的可能是 int/float（Excel 把 5 存成数字），
    那就直接当十进制值，别经 `parse_int` 的裸 hex 兜底把 11 读成 0x11。"""
    if v is None:
        return ""
    if isinstance(v, bool):
        return str(int(v))
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        return str(int(v)) if float(v).is_integer() else str(v)
    return str(v)


def _pick_exp_row(rows):
    """数据行里挑「期望」那一行。返回 (行, 用的哪一行的行名, 要不要提示)。

    找法（与导出的 CSV 行名对齐）：`期望(进.sv)` > `期望(bin)` > `期望` >（只有一行数据时）那一行。
    `期望来源` 长得像但是文字说明，明确排除掉。
    """
    data = rows[1:]
    for pre in EXP_ROW_PREFIXES:
        for r in data:
            lbl = _cell_text(r[0] if r else "").strip()
            if lbl in EXP_ROW_EXCLUDE:
                continue
            if lbl.startswith(pre):
                return r, lbl, None
    if len(data) == 1:
        lbl = _cell_text(data[0][0] if data[0] else "").strip()
        return data[0], lbl, _t("TRUTH_IMPORT_ONE_ROW_FMT", label=lbl)
    return None, "", _t("TRUTH_IMPORT_NO_EXP_ROW")


def read_expectations(path):
    """从 CSV / xlsx 按【列名】读期望值（C-298 的读文件那半边）。

    返回 `(by_name, notes)`：

    * `by_name` = `{列名: int}` —— 直接喂 `model.apply_expectations(by_name)`。
      **给的是解析好的 int**，不是原文：`apply_expectations` 里是 `int(val) & mask`，
      而表里写的是 IC 工程师那 8 种写法（`0xA` / `4'b1010` / `16'hA` …），`int()` 一个都不认。
      文本 → 值的翻译是读文件这一侧的事（model 不碰文件、也不碰文本解析）。
    * `notes` = 逐条提示（哪一列的写法没认出来、列名重了取了哪一处、没找到期望行……）。
      **列名对不对得上本信号不在这里判** —— 那是 `model.apply_expectations` 返回的
      `missing`（C-298：报没对上的列名）。

    文件格式 = 导出的那张 CSV 原样改（`exports.signal_csv_text`）：第一行是列名
    （第一格是行名列的表头，如 `信号\\测试`），其后找「期望」行取值；整个文件只有一行
    数据时就按那一行取（designer 手写一张两行小表也能用）。
    """
    ext = os.path.splitext(str(path or ""))[1].lower()
    rows = _rows_from_xlsx(path) if ext in (".xlsx", ".xlsm") else _rows_from_csv(path)
    rows = [r for r in rows if any(_cell_text(v).strip() for v in (r or ()))]
    notes = []
    if not rows:
        return {}, [_t("TRUTH_IMPORT_EMPTY_FILE")]
    header = [_cell_text(v).strip() for v in rows[0]]
    if len(header) < 2 or not any(header[1:]):
        return {}, [_t("TRUTH_IMPORT_NO_COLUMNS")]

    row, _lbl, note = _pick_exp_row(rows)
    if note:
        notes.append(note)
    if row is None:
        return {}, notes

    by_name = {}
    for i, nm in enumerate(header):
        if i == 0 or not nm:
            continue
        if nm in by_name:
            notes.append(_t("TRUTH_IMPORT_DUP_COL_FMT", name=nm))
            continue
        txt = _cell_text(row[i] if i < len(row) else "").strip()
        if txt == "":
            continue                       # 这一列没填 = 不动它（不是「填 0」）
        try:
            by_name[nm] = TE.parse_int(txt)
        except ValueError:
            notes.append(_t("TRUTH_IMPORT_BAD_CELL_FMT", name=nm, text=txt))
    return by_name, notes


# ═════════════════════════ 三、单信号真值表 CSV（C-136…C-139）═════════════════════════
def csv_input_rows(an):
    """CSV 的输入行（第一列的信号/字段名）—— 与 v1 `SignalView.e_inputs` 的三个键同口径。

    行名用【带位宽的真名】，不带真值表冻结列那个 `　(角色 · 端口)` 后缀：CSV 是拿去和
    寄存器表 / .sv 对名字的交换件，多一截中文会让「按名字查」变难。
    行序与编辑器一致（logic 按 `an["groups"]`，mux 按 `expansion["used_vars"]`，iddq 门殿后）。
    """
    out = []
    an = an or {}
    kind = an.get("editable") or ""
    if kind == "logic":
        for g in (an.get("groups") or []):
            out.append({"key": g["key"], "label": g.get("label") or g.get("base") or g["key"],
                        "width": g.get("width") or 1})
    elif kind == "mux":
        exp = an.get("expansion") or {}
        bindings = exp.get("bindings") or {}
        for k in (exp.get("used_vars") or []):
            b = bindings.get(k)
            if b is None:
                continue
            out.append({"key": k, "label": IT.mux_label(b), "width": b.width})
    gate = an.get("dft_gate")
    if gate:
        out.append({"key": gate["key"], "label": gate["label"], "width": gate.get("width", 1)})
    return out


def _gate_value(gate, vec):
    """该向量把 iddq 门驱成了几（功能拍 = 透传值，DFT 自检拍 = 门被 force 的那个值）。
    与 `edits.cols_from_vectors` 同口径 —— 真值表上那一行显示什么，CSV 里就是什么。"""
    gv = gate["transp"]
    for (wl, wv, _ww) in (getattr(vec, "extra_forces", None) or []):
        if wl == gate["wire_lhs"]:
            gv = wv
    return gv


def _mux_columns_from_vectors(an, vecs):
    """build 的向量 → CSV 列模型（C-139 忠于产物）。"""
    exp = an.get("expansion") or {}
    out_w = int(an.get("out_width") or 1)
    cols, _rows = X.columns_from_mux_vectors(
        vecs, exp.get("bindings") or {}, exp.get("used_vars") or [],
        data_keys=exp.get("data_keys") or (), out_width=out_w)
    gate = an.get("dft_gate")
    if gate:
        # `columns_from_mux_vectors` 的 gate_value 是整表一个常量，对不上 DFT 自检拍
        # （那一拍门被 force 成非透传值）——按每条向量的 extra_forces 补，和编辑器一致。
        for col, v in zip(cols, vecs):
            col["vals"][gate["key"]] = _gate_value(gate, v)
    return cols


#: `cov` 认得的键 = `exports.render_sv` 的覆盖度形参（与 `sv_preview._cov_args()` 一一对应）
COV_KEYS = ("mode", "max_tests", "exhaustive", "sig_cov", "form_cov")


def _render_kwargs(cov):
    """`cov` → `exports.render_sv` 的关键字。None / {} → 用 render_sv 自己的默认。

    ⚠ 必须与算出 `an` 的那一档**同一个覆盖度**，否则 CSV 列集是一个档、编辑器是另一个档，
    看着像「导出漏了几条」。面板（C3-c）给的就是 `sv_preview._cov_args()` 那五个值
    （本信号：单点 > 逻辑类型 > 全局已由 `CoverageState.mode_for` 折平）。
    """
    if not cov:
        return {}
    bad = set(cov) - set(COV_KEYS)
    if bad:
        raise TypeError("cov 只认 %s，不认 %s" % (list(COV_KEYS), sorted(bad)))
    return {k: cov[k] for k in COV_KEYS if k in cov}


def signal_csv_columns(model, an, name, provider=None, edited=None, cov=None):
    """CSV 的列（每列一条测试）。

    mux 且给了 `provider`：走产物（C-139）—— `exports.render_sv(provider, only=[name],
    edited=edited)` 那次 build 里该信号实际渲染的向量。整信号负向、负向去重、T 编号重排、
    iddq 自检拍都在 build 里，编辑器那份列模型看不到。
    其余（logic / register / dft / 只读信号）：model 现在这张表 —— 所见即所得，
    也正是 v1 `SignalView.on_export_csv` 用的那份（`sv.cur_cols`）。

    mux 但**拿不到产物**（没传 provider / 该信号这次 build 里没产出块，比如被跳过或只记账）
    → 退回 model 的列。面板（C3-c）导出前该自己判一下并照实说一句，别让用户以为导出的
    CSV 与 .sv 对得上。
    """
    kw = _render_kwargs(cov)                   # cov 键写错是调用方的 bug，当场抛，不吞
    if (an or {}).get("editable") == "mux" and provider is not None:
        try:
            _text, build = X.render_sv(provider, only=[name], edited=edited, **kw)
        except Exception:                      # noqa: BLE001  导不出 .sv 不该连 CSV 也导不了
            build = None
        vecs = X.signal_build_vectors(build, name) if build else None
        if vecs:
            return _mux_columns_from_vectors(an, vecs)
    return list(model.cols())


def export_signal_csv(path, model, an, name, provider=None, edited=None, cov=None):
    """导出【本信号】真值表 CSV（C-136），返回写出的文本。

    转置排版（第一列 = 信号/字段名，其后每列一条测试）与行序全在
    `exports.signal_csv_text`：输入行… → auto_out → 期望(进.sv) → **期望(bin)**（C-137）
    → **期望来源** → **负向?**（C-138）→ **force** → **RF_WRITE**（C-137）。

    `provider` / `edited` / `cov` 只有 mux 用得上（C-139，见 `signal_csv_columns`）：面板传
    `state.provider()`、`state.compute_edited()`、以及算 `an` 用的那一档覆盖度 —— 与
    .sv 预览 / 导出中心喂给 `exports.render_sv` 的是同几样东西，所以 CSV 与 .sv 必然同一份产物。
    """
    out_w = int((an or {}).get("out_width") or 1)
    cols = signal_csv_columns(model, an, name, provider=provider, edited=edited, cov=cov)
    rows = csv_input_rows(an)
    drive_fn = X.make_drive_fn(*X.drive_context(an))
    text = X.signal_csv_text(cols, rows, out_width=out_w, drive_fn=drive_fn)
    X.write_signal_csv(path, cols, rows, out_width=out_w, drive_fn=drive_fn,
                       name=name, text=text)
    return text
