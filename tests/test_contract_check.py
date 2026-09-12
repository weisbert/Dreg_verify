# -*- coding: utf-8 -*-
"""tools/contract_check.py 自测（假 csv + 假 junit XML，不跑真 pytest）。

四种情形各一条：覆盖通过 / 覆盖但失败 / 未覆盖 / 删除类免检 / 未知 ID，
外加 --areas 筛选、--md 报告、--strict 退出码、缺文件提示、两种挂钩方式。
夹具 csv = tests/contract_sample.csv（脚本自身的 --csv 也能直接指它跑）。
"""

import os
import subprocess
import sys

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, os.path.join(_ROOT, "tools"))

import contract_check as CC                              # noqa: E402

SAMPLE_CSV = os.path.join(_HERE, "contract_sample.csv")

# 假 junit：C-001 过、C-002 失败、C-005 走 marker 属性过、C-006/C-007 一条测试挂俩、
# C-900 契约表里没有（ID 写错），另有一条 skipped（不算通过）。
FAKE_JUNIT = """<?xml version="1.0" encoding="utf-8"?>
<testsuites><testsuite name="pytest" tests="6">
  <testcase classname="tests.test_v2" name="test_c001_owner_filter" time="0.01"/>
  <testcase classname="tests.test_v2" name="test_c002_export_options_memory" time="0.01">
    <failure message="assert 0">boom</failure>
  </testcase>
  <testcase classname="tests.test_v2" name="test_highlight_bus" time="0.01">
    <properties><property name="contract_ids" value="C-005"/></properties>
  </testcase>
  <testcase classname="tests.test_v2" name="test_c006_c007_nets_dialog" time="0.01"/>
  <testcase classname="tests.test_v2" name="test_c900_typo" time="0.01"/>
  <testcase classname="tests.test_v2" name="test_c001_owner_filter_slow" time="0.01">
    <skipped type="pytest.skip" message="慢"/>
  </testcase>
</testsuite></testsuites>
"""


@pytest.fixture()
def junit(tmp_path):
    p = tmp_path / "junit.xml"
    p.write_text(FAKE_JUNIT, encoding="utf-8")
    return str(p)


@pytest.fixture()
def report(junit):
    rows, problems = CC.load_contract(SAMPLE_CSV)
    rep = CC.evaluate(rows, CC.parse_junit(junit))
    rep.problems = problems
    return rep


def _state(rep, cid):
    return next(e.state for e in rep.entries if e.row.id == cid)


# ───────────── ID 归一化 / 两种挂钩方式 ─────────────
def test_norm_id_forms():
    assert CC.norm_id("c042") == CC.norm_id("C-042") == CC.norm_id("C_042") == "C-042"
    assert CC.norm_id("C 042") == "C-042"
    assert CC.norm_id("C-1234") == "C-1234"
    assert CC.norm_id("") is None and CC.norm_id("C42") is None and CC.norm_id("x1") is None


def test_ids_from_function_name():
    assert CC.ids_from_name("test_c042_export_skipped_names") == ["C-042"]
    assert CC.ids_from_name("test_c042_c043_both") == ["C-042", "C-043"]
    assert CC.ids_from_name("test_c042_x[param-1]") == ["C-042"]          # 去掉 parametrize 尾巴
    assert CC.ids_from_name("test_abc123_not_an_id") == []                # c 前非词边界 → 不误认
    assert CC.ids_from_name("test_mux_wl_c0c1_dreg") == []                # 位数不够 → 不误认
    assert CC.ids_from_name("test_plain") == []


def test_marker_ids_come_from_junit_properties(junit):
    got = {t.name: t.ids for t in CC.parse_junit(junit)}
    assert got["test_highlight_bus"] == ["C-005"]                          # ② marker → <properties>
    assert got["test_c001_owner_filter"] == ["C-001"]                      # ① 函数名
    assert got["test_c006_c007_nets_dialog"] == ["C-006", "C-007"]         # 一条测试两个 ID


# ───────────── 四种情形 ─────────────
def test_covered_passing(report):
    assert _state(report, "C-001") == CC.COVERED
    assert _state(report, "C-005") == CC.COVERED                           # marker 挂钩也算数
    assert _state(report, "C-006") == _state(report, "C-007") == CC.COVERED


def test_covered_but_failing(report):
    assert _state(report, "C-002") == CC.FAILING
    e = next(x for x in report.entries if x.row.id == "C-002")
    assert [t.name for t in e.tests] == ["test_c002_export_options_memory"]


def test_uncovered(report):
    assert _state(report, "C-003") == CC.UNCOVERED
    assert [e.row.id for e in report.by_state(CC.UNCOVERED)] == ["C-003"]


def test_deleted_category_is_exempt(report):
    """类别=删除 → 没有测试也不算缺口。"""
    assert _state(report, "C-004") == CC.EXEMPT
    assert not any(e.row.id == "C-004" for e in report.by_state(CC.UNCOVERED))


def test_unknown_id_reported(report):
    """测试挂了 ID、契约表里没有 → 单列出来（多半是 ID 写错）。"""
    assert list(report.unknown) == ["C-900"]
    assert report.unknown["C-900"] == ["tests.test_v2::test_c900_typo"]


def test_skipped_test_does_not_count_as_passing(junit):
    got = {t.name: t.outcome for t in CC.parse_junit(junit)}
    assert got["test_c001_owner_filter_slow"] == "skipped"
    assert got["test_c002_export_options_memory"] == "failed"
    # 只有 skipped 的话应判「覆盖但失败」——造一张只含那条 skipped 的结果验一下
    rows, _ = CC.load_contract(SAMPLE_CSV)
    only_skip = [t for t in CC.parse_junit(junit) if t.outcome == "skipped"]
    assert CC.evaluate(rows, only_skip).by_state(CC.FAILING)[0].row.id == "C-001"


def test_report_not_ok_and_counts(report):
    c = report.counts()
    assert c == {CC.COVERED: 4, CC.FAILING: 1, CC.UNCOVERED: 1, CC.EXEMPT: 1}
    assert report.ok is False


# ───────────── csv 校验 ─────────────
def test_areas_filter_keeps_known_ids_global(junit):
    """--areas 只筛显示的行，别的区的 ID 不能因此被打成「未知」。"""
    rows_all, _ = CC.load_contract(SAMPLE_CSV)
    rows = CC.filter_areas(rows_all, ["信号表"])
    assert sorted(r.id for r in rows) == ["C-001", "C-005"]
    rep = CC.evaluate(rows, CC.parse_junit(junit), known_ids={r.id for r in rows_all})
    assert list(rep.unknown) == ["C-900"]                 # C-002/C-006… 不算未知


def test_csv_problems_are_reported(tmp_path):
    bad = tmp_path / "bad.csv"
    bad.write_text(
        ",".join(CC.COLUMNS) + "\n"
        "C-001,a,能力1,保留,,,,c001,,,\n"
        "C-001,a,重复 ID,保留,,,,c001,,,\n"
        "C-002,a,类别写错,保存,,,,c002,,,\n"
        "XYZ,a,ID 认不出,保留,,,,,,,\n", encoding="utf-8")
    rows, problems = CC.load_contract(str(bad))
    assert [r.id for r in rows] == ["C-001", "C-002"]
    blob = "\n".join(problems)
    assert "重复" in blob and "类别" in blob and "认不出" in blob


def test_missing_csv_gives_clear_hint(tmp_path):
    with pytest.raises(CC.ContractError) as ei:
        CC.load_contract(str(tmp_path / "没有这张表.csv"))
    msg = str(ei.value)
    assert "找不到契约表" in msg and "contract_sample.csv" in msg      # 指路到自测夹具


def test_wrong_header_is_rejected(tmp_path):
    bad = tmp_path / "h.csv"
    bad.write_text("id,area,capability\nC-001,a,b\n", encoding="utf-8")
    with pytest.raises(CC.ContractError) as ei:
        CC.load_contract(str(bad))
    assert "缺列" in str(ei.value)


def test_missing_junit_gives_clear_hint(tmp_path):
    with pytest.raises(CC.ContractError) as ei:
        CC.parse_junit(str(tmp_path / "nope.xml"))
    assert "--run" in str(ei.value)


# ───────────── 报告 / CLI ─────────────
def test_render_md_table(report, tmp_path):
    md = CC.render_md(report, SAMPLE_CSV, "junit.xml")
    assert "| ID | 区 | 能力 | 类别 | 测试 | 结果 |" in md
    assert "| C-001 |" in md and CC.COVERED in md
    assert "test_c002_export_options_memory(failed)" in md
    assert "C-900" in md and "未知 ID" in md


def _cli(*args):
    cmd = [sys.executable, os.path.join(_ROOT, "tools", "contract_check.py")] + list(args)
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    p = subprocess.run(cmd, cwd=_ROOT, env=env, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def test_cli_strict_fails_and_writes_md(junit, tmp_path):
    out_md = tmp_path / "cov.md"
    rc, out = _cli("--csv", SAMPLE_CSV, "--junit", junit, "--md", str(out_md), "--strict")
    assert rc == 1 and "CONTRACT-CHECK FAIL" in out
    assert CC.UNCOVERED in out and "C-003" in out and "C-900" in out
    assert out_md.exists() and "| C-004 |" in out_md.read_text(encoding="utf-8")


def test_cli_without_strict_exits_zero(junit):
    rc, out = _cli("--csv", SAMPLE_CSV, "--junit", junit)
    assert rc == 0 and "CONTRACT-CHECK" in out


def test_cli_needs_junit_or_run(junit):
    rc, out = _cli("--csv", SAMPLE_CSV)
    assert rc == 2 and "--run" in out


def test_cli_missing_csv_exit_2(junit, tmp_path):
    rc, out = _cli("--csv", str(tmp_path / "无.csv"), "--junit", junit)
    assert rc == 2 and "找不到契约表" in out


def test_cli_areas_filter(junit):
    rc, out = _cli("--csv", SAMPLE_CSV, "--junit", junit, "--areas", "信号表")
    assert rc == 0 and "合计 2 条" in out


# ───────────── conftest 的 contract marker 真能落进 junit ─────────────
@pytest.mark.contract("C-001", "c002")
def test_marker_demo_for_junit_property():
    """本条只为给下面那条当靶子：证明 tests/conftest.py 的 marker 钩子真的写进了 junit。
    （函数名故意不含 c\\d{3}，确保读到的 ID 只可能来自 marker。）"""
    assert True


def _run_pytest_junit(tmp_path, node):
    xml = tmp_path / "j.xml"
    cmd = [sys.executable, "-m", "pytest", "-q", "--junitxml", str(xml), node]
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", PYTHONIOENCODING="utf-8")
    p = subprocess.run(cmd, cwd=_ROOT, env=env, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    assert p.returncode == 0, p.stdout + p.stderr
    return str(xml)


def test_conftest_marker_lands_in_junit_properties(tmp_path):
    node = os.path.join("tests", "test_contract_check.py") + "::test_marker_demo_for_junit_property"
    res = CC.parse_junit(_run_pytest_junit(tmp_path, node))
    assert [t.ids for t in res] == [["C-001", "C-002"]]      # c002 也归一化成 C-002


def test_cli_run_mode(tmp_path):
    """--run：脚本自己跑 pytest 出 junit（这里只跑一条，验证接线通）。"""
    rc, out = _cli("--csv", SAMPLE_CSV, "--run",
                   "-k", "marker_demo_for_junit_property",
                   "--pytest-arg", os.path.join("tests", "test_contract_check.py"))
    assert rc == 0
    assert "--junitxml" in out and "合计 7 条" in out
    assert "C-001" not in out.split("未覆盖")[-1].split("未知")[0]   # C-001 被 marker 覆盖了
