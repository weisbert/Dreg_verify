# -*- coding: utf-8 -*-
"""contract_check.py —— GUI v2 能力契约覆盖闸门（执行计划 §2 的 L3 机械锁）。

「零遗漏」不靠记性：契约表(`docs/GUI_v2_能力契约.csv`)每条能力一个 ID，v2 的每条 GUI 测试
按 ID 挂钩；本脚本把两边对上，任一非「删除」ID 没有 ≥1 条【通过】的测试就报红。

测试 → 契约 ID 两种挂钩方式（都支持，可混用）：
  ① 函数名里含 `c\\d{3}`         —— `def test_c042_export_skipped_names()`（大小写不敏感，
                                    一个测试名可含多个 ID：`test_c042_c043_xxx`）
  ② `@pytest.mark.contract(...)` —— `@pytest.mark.contract("C-042", "C-043")`
                                    （tests/conftest.py 把 marker 写进 junit 的 <properties>）
ID 归一化：`c042` / `C-042` / `C_042` 视为同一个 `C-042`。

用法：
    # 用已有的 junit 结果
    .venv/Scripts/python.exe tools/contract_check.py --csv docs/GUI_v2_能力契约.csv \
        --junit build/junit.xml --md refactor_notes/contract_coverage.md --strict
    # 自己跑一遍 pytest（junit 写到临时目录）
    .venv/Scripts/python.exe tools/contract_check.py --csv docs/GUI_v2_能力契约.csv --run -k gui
    # 只看某几个区
    .venv/Scripts/python.exe tools/contract_check.py --csv ... --junit ... --areas 信号表,导出

退出码：0 = 通过（或非 --strict）；1 = --strict 且有未覆盖/覆盖失败/未知 ID；2 = 用法或文件错误。
"""

import argparse
import csv
import os
import re
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: 契约 csv 的固定表头（顺序不强制，缺列才报错）
COLUMNS = ["id", "area", "capability", "category", "old_entry", "backend_api",
           "v2_location", "test_ids", "status", "source", "note"]

#: 类别取值。「删除」= 这条能力 v2 不再有 → 不要求测试。
CATEGORIES = ("保留", "合并", "降级", "删除", "新增", "延期")
EXEMPT_CATEGORIES = ("删除", "延期")   # 延期 = 已拍板排到 v2 之后单开任务（B3 §8-1）

_ID_RE = re.compile(r"[Cc][-_ ]?(\d{3,})")

COVERED, FAILING, UNCOVERED, EXEMPT = "覆盖通过", "覆盖但失败", "未覆盖", "免检(删除/延期)"


class ContractError(Exception):
    """用法/文件错误 —— 退出码 2。"""


# ─────────────────────────── ID / csv ───────────────────────────

def norm_id(raw):
    """`c042` / `C-042` / `C_042` / ` C 042 ` → `C-042`。认不出返回 None。"""
    if raw is None:
        return None
    m = _ID_RE.fullmatch(str(raw).strip())
    return "C-%s" % m.group(1) if m else None


#: 函数名里的 ID：c 前必须是词边界（否则 `test_abc123_x` 会被误读成 C-123）
_NAME_ID_RE = re.compile(r"(?:^|[^0-9A-Za-z])[Cc](\d{3,})(?![0-9])")


def ids_from_name(name):
    """从测试函数名里抠出所有契约 ID（`test_c042_c043_xxx` → ['C-042','C-043']）。"""
    base = str(name).split("[", 1)[0]                    # 去掉 parametrize 的 [..]
    return ["C-%s" % d for d in _NAME_ID_RE.findall(base)]


def split_ids(cell):
    """契约表 test_ids 列：`;` 分隔（顺带容忍 , 、 空格）。"""
    parts = re.split(r"[;,、\s]+", str(cell or "").strip())
    return [p for p in parts if p]


class Row(object):
    __slots__ = tuple(COLUMNS) + ("lineno",)

    def __init__(self, d, lineno):
        for c in COLUMNS:
            setattr(self, c, (d.get(c) or "").strip())
        self.lineno = lineno

    def __repr__(self):
        return "<Row %s %s %s>" % (self.id, self.category, self.capability)


def load_contract(csv_path):
    """读契约 csv → (rows, problems)。problems = 表本身的毛病（重复 ID / 非法类别 / 认不出的 ID）。"""
    if not os.path.exists(csv_path):
        raise ContractError(
            "找不到契约表：%s\n"
            "  —— 它由 Phase A2 产出（docs/GUI_v2_能力契约.csv，与 .md 同源）。\n"
            "     表还没写好时可以先用自测夹具跑通本脚本：\n"
            "       tools/contract_check.py --csv tests/contract_sample.csv --junit <你的 junit.xml>"
            % csv_path)
    with open(csv_path, encoding="utf-8-sig", newline="") as f:
        rd = csv.DictReader(f)
        header = [(h or "").strip() for h in (rd.fieldnames or [])]
        missing = [c for c in COLUMNS if c not in header]
        if missing:
            raise ContractError("契约表列头不对：%s\n  缺列：%s\n  应为：%s"
                                % (csv_path, ", ".join(missing), ",".join(COLUMNS)))
        raw_rows = [({(k or "").strip(): v for k, v in d.items()}, i)
                    for i, d in enumerate(rd, start=2)]

    rows, problems, seen = [], [], {}
    for d, lineno in raw_rows:
        if not any((v or "").strip() for v in d.values()):
            continue                                     # 空行
        r = Row(d, lineno)
        cid = norm_id(r.id)
        if cid is None:
            problems.append("第 %d 行：ID %r 认不出（应形如 C-042）" % (lineno, r.id))
            continue
        r.id = cid
        # 类别允许带括号说明（如「降级(诊断抽屉)」「新增(合并出来)」），判定只看括号前的主类别
        r.category = re.sub(r"\s*[（(].*$", "", (r.category or "").strip())
        if r.category not in CATEGORIES:
            problems.append("第 %d 行 %s：类别 %r 不在 {%s} 里"
                            % (lineno, cid, r.category, "/".join(CATEGORIES)))
        if cid in seen:
            problems.append("第 %d 行：ID %s 重复（首见于第 %d 行）" % (lineno, cid, seen[cid]))
            continue
        seen[cid] = lineno
        rows.append(r)
    return rows, problems


def filter_areas(rows, areas):
    """只留 area 在 areas 里的行（areas 为空 → 原样返回）。"""
    want = {a.strip() for a in (areas or []) if a.strip()}
    return rows if not want else [r for r in rows if r.area in want]


# ─────────────────────────── junit ───────────────────────────

class TestResult(object):
    __slots__ = ("name", "classname", "outcome", "ids")

    def __init__(self, name, classname, outcome, ids):
        self.name, self.classname, self.outcome, self.ids = name, classname, outcome, ids

    @property
    def nodeid(self):
        return "%s::%s" % (self.classname, self.name) if self.classname else self.name

    @property
    def passed(self):
        return self.outcome == "passed"


def parse_junit(xml_path):
    """读 pytest --junitxml 产物 → list[TestResult]（ID 来自函数名 + <properties> 两路并集）。"""
    if not os.path.exists(xml_path):
        raise ContractError("找不到 junit XML：%s（用 --run 让脚本自己跑一遍）" % xml_path)
    try:
        root = ET.parse(xml_path).getroot()
    except ET.ParseError as ex:
        raise ContractError("junit XML 解析失败：%s（%s）" % (xml_path, ex))
    out = []
    for tc in root.iter("testcase"):
        name = tc.get("name") or ""
        outcome = "passed"
        for child in tc:
            tag = child.tag
            if tag in ("failure", "error"):
                outcome = "failed"
                break
            if tag == "skipped":
                outcome = "skipped"
        ids = list(ids_from_name(name))
        for prop in tc.iter("property"):
            if (prop.get("name") or "") == "contract_ids":
                for raw in split_ids(prop.get("value")):
                    cid = norm_id(raw)
                    if cid and cid not in ids:
                        ids.append(cid)
        out.append(TestResult(name, tc.get("classname") or "", outcome, ids))
    return out


def run_pytest(junit_path, k=None, extra=None, cwd=None):
    """自己跑一遍 pytest 出 junit（offscreen；解释器 = 当前解释器）。返回 pytest 退出码。"""
    cmd = [sys.executable, "-m", "pytest", "-q", "--junitxml", junit_path]
    if k:
        cmd += ["-k", k]
    cmd += list(extra or [])
    env = dict(os.environ)
    env.setdefault("QT_QPA_PLATFORM", "offscreen")
    print("+ %s" % " ".join(cmd))
    return subprocess.call(cmd, cwd=cwd or _ROOT, env=env)


# ─────────────────────────── 判定 ───────────────────────────

class Entry(object):
    __slots__ = ("row", "state", "tests")

    def __init__(self, row, state, tests):
        self.row, self.state, self.tests = row, state, tests


class Report(object):
    def __init__(self, entries, unknown, problems):
        self.entries = entries
        self.unknown = unknown          # {ID: [nodeid...]} 测试里有、契约表里没有
        self.problems = problems        # 契约表自身的毛病

    def by_state(self, state):
        return [e for e in self.entries if e.state == state]

    @property
    def ok(self):
        return not (self.by_state(UNCOVERED) or self.by_state(FAILING)
                    or self.unknown or self.problems)

    def counts(self):
        return {s: len(self.by_state(s)) for s in (COVERED, FAILING, UNCOVERED, EXEMPT)}


def evaluate(rows, results, known_ids=None):
    """契约行 × 测试结果 → Report。

    规则：类别=删除 免检；其余每 ID 至少一条【通过】的测试，否则 未覆盖 / 覆盖但失败。
    另报「测试里有 ID、契约表里没有」的（防 ID 写错）。
    known_ids：判「未知」时用的全量 ID 集合 —— `--areas` 只筛显示的行，不能把别的区的
    ID 一起打成「未知」。默认取 rows 自身的 ID。"""
    by_id = {}
    for t in results:
        for cid in t.ids:
            by_id.setdefault(cid, []).append(t)

    entries = []
    known = set(known_ids) if known_ids is not None else {r.id for r in rows}
    for r in rows:
        known.add(r.id)
        tests = by_id.get(r.id, [])
        if r.category in EXEMPT_CATEGORIES:
            state = EXEMPT
        elif any(t.passed for t in tests):
            state = COVERED
        elif tests:
            state = FAILING
        else:
            state = UNCOVERED
        entries.append(Entry(r, state, tests))

    unknown = {cid: sorted({t.nodeid for t in ts})
               for cid, ts in by_id.items() if cid not in known}
    return Report(entries, unknown, [])


# ─────────────────────────── 输出 ───────────────────────────

_MARK = {COVERED: "✔", FAILING: "✘", UNCOVERED: "—", EXEMPT: "·"}


def _tests_cell(entry, sep=", "):
    if not entry.tests:
        return ""
    return sep.join("%s%s" % (t.name, "" if t.passed else "(%s)" % t.outcome)
                    for t in entry.tests)


def render_md(report, csv_path=None, junit_path=None):
    """覆盖报告（Markdown 表：每 ID | 类别 | 测试 | 结果）。"""
    c = report.counts()
    lines = ["# GUI v2 能力契约覆盖报告", ""]
    if csv_path:
        lines.append("- 契约表：`%s`" % csv_path)
    if junit_path:
        lines.append("- 测试结果：`%s`" % junit_path)
    lines += [
        "- 合计 %d 条：%s %d / %s %d / %s %d / %s %d"
        % (len(report.entries), COVERED, c[COVERED], FAILING, c[FAILING],
           UNCOVERED, c[UNCOVERED], EXEMPT, c[EXEMPT]),
        "",
        "| ID | 区 | 能力 | 类别 | 测试 | 结果 |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for e in sorted(report.entries, key=lambda x: x.row.id):
        lines.append("| %s | %s | %s | %s | %s | %s %s |"
                     % (e.row.id, e.row.area, e.row.capability, e.row.category,
                        _tests_cell(e, "<br>") or "—", _MARK[e.state], e.state))
    if report.unknown:
        lines += ["", "## 未知 ID（测试挂了 ID，契约表里没有 —— 多半是 ID 写错）", ""]
        for cid in sorted(report.unknown):
            lines.append("- **%s** ← %s" % (cid, ", ".join(report.unknown[cid])))
    if report.problems:
        lines += ["", "## 契约表自身的问题", ""] + ["- %s" % p for p in report.problems]
    return "\n".join(lines) + "\n"


def render_text(report):
    c = report.counts()
    out = ["契约覆盖：合计 %d 条  %s %d / %s %d / %s %d / %s %d"
           % (len(report.entries), COVERED, c[COVERED], FAILING, c[FAILING],
              UNCOVERED, c[UNCOVERED], EXEMPT, c[EXEMPT])]
    for state in (UNCOVERED, FAILING):
        got = report.by_state(state)
        if got:
            out.append("")
            out.append("%s %s（%d）：" % (_MARK[state], state, len(got)))
            for e in sorted(got, key=lambda x: x.row.id):
                tail = ("  测试：%s" % _tests_cell(e)) if e.tests else ""
                out.append("  %s  [%s] %s%s" % (e.row.id, e.row.category, e.row.capability, tail))
    if report.unknown:
        out.append("")
        out.append("? 未知 ID（测试有、契约表没有；多半 ID 写错）：")
        for cid in sorted(report.unknown):
            out.append("  %s  ← %s" % (cid, ", ".join(report.unknown[cid])))
    if report.problems:
        out.append("")
        out.append("! 契约表自身的问题：")
        out += ["  %s" % p for p in report.problems]
    return "\n".join(out)


# ─────────────────────────── CLI ───────────────────────────

def build_parser():
    p = argparse.ArgumentParser(
        prog="contract_check.py",
        description="GUI v2 能力契约覆盖闸门：契约 csv × pytest junit 结果",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--csv", default=os.path.join(_ROOT, "docs", "GUI_v2_能力契约.csv"),
                   help="契约表 csv（默认 docs/GUI_v2_能力契约.csv）")
    p.add_argument("--junit", help="已有的 pytest --junitxml 产物")
    p.add_argument("--run", action="store_true", help="自己跑一遍 pytest 出 junit（写到临时目录）")
    p.add_argument("-k", dest="k", help="配合 --run：传给 pytest 的 -k 表达式")
    p.add_argument("--pytest-arg", dest="pytest_args", action="append", default=[],
                   help="配合 --run：追加给 pytest 的参数（可重复）")
    p.add_argument("--areas", help="只看这些区（area 列），逗号分隔")
    p.add_argument("--md", help="把覆盖报告写成 Markdown 到这个路径")
    p.add_argument("--strict", action="store_true",
                   help="有未覆盖/覆盖失败/未知 ID/表本身问题 → 退出码 1")
    return p


def main(argv=None):
    # Windows 控制台默认 GBK，报告里有 ✔ / ✅ 之类符号会直接崩；能重配就重配，重配不了就替换
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    args = build_parser().parse_args(argv)
    try:
        if not args.junit and not args.run:
            raise ContractError("要么给 --junit <已有结果>，要么加 --run 让脚本自己跑 pytest。")
        rows_all, problems = load_contract(args.csv)
        rows = filter_areas(rows_all, args.areas.split(",") if args.areas else None)
        junit = args.junit
        if args.run:
            tmpdir = tempfile.mkdtemp(prefix="contract_check_")
            junit = os.path.join(tmpdir, "junit.xml")
            run_pytest(junit, k=args.k, extra=args.pytest_args)
        results = parse_junit(junit)
    except ContractError as ex:
        sys.stderr.write("%s\n" % ex)
        return 2

    report = evaluate(rows, results, known_ids={r.id for r in rows_all})
    report.problems = problems
    print(render_text(report))
    if args.md:
        d = os.path.dirname(os.path.abspath(args.md))
        if d:
            os.makedirs(d, exist_ok=True)
        with open(args.md, "w", encoding="utf-8") as f:
            f.write(render_md(report, args.csv, junit))
        print("\n覆盖报告已写出：%s" % args.md)
    if args.strict and not report.ok:
        print("\nCONTRACT-CHECK FAIL（--strict）")
        return 1
    print("\nCONTRACT-CHECK %s" % ("PASS" if report.ok else "WARN（未加 --strict，不拦）"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
