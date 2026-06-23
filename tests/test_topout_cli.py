# -*- coding: utf-8 -*-
"""CLI --topout 路径的全流程测试（2026-06-23 块B：Topout-rooted 接入 CLI）。

覆盖 cli.py 的 --topout 分派（核心引擎在 test_topout.py 已把关）：
  ① --topout --list   → 列 Topout B 列要验信号 + 分类（logic/mux/register/ro-readback）
  ② --topout --account → 账目（默认不空、堵 top_out=0 假警告）
  ③ --topout (默认)    → 出 .sv，断言贴顶层真名、RO 回读记账（不静默丢）
  ④ --topout --report  → HTML / CSV 报告（Topout 限定）
  ⑤ 旧 CLI 不带 --topout 路径不破（逐字节回归在主套件，这里验仍可跑）
  ⑥ 无 Topout 页 → 优雅提示、不崩
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import make_mirror_btlp                            # 仓库根夹具脚本（conftest 已加 path）
from dreg_verify import cli                          # noqa: E402


@pytest.fixture(scope="module")
def mirror(tmp_path_factory):
    p = tmp_path_factory.mktemp("btlp_cli") / "mirror_btlp_dreg.xlsx"
    make_mirror_btlp.build(str(p))
    return str(p)


# ───────────── ① --topout --list ─────────────
def test_topout_list(mirror, capsys):
    cli.main(["--excel", mirror, "--topout", "--list"])
    out = capsys.readouterr().out
    assert "Topout 要验信号 12 个" in out
    assert "logic 8" in out and "mux 1" in out and "register 2" in out and "ro-readback 1" in out
    assert "d_logic_bt_lp_rx_en" in out
    assert "pll_lock_indicator" in out and "ro-readback" in out


# ───────────── ② --topout --account ─────────────
def test_topout_account(mirror, capsys):
    cli.main(["--excel", mirror, "--topout", "--account"])
    out = capsys.readouterr().out
    assert "Topout 账目：12 个要验信号" in out
    assert "可建 11" in out and "跳过(RO) 1" in out
    assert "未解析 0" in out and "error 0" in out


# ───────────── ③ --topout 出 .sv ─────────────
def test_topout_sv_output(mirror, tmp_path, capsys):
    out = tmp_path / "topout.sv"
    cli.main(["--excel", mirror, "--topout", "--out", str(out), "--mode", "max"])
    msg = capsys.readouterr().out
    assert "已写出(Topout 路径)" in msg
    text = out.read_text(encoding="utf-8")
    assert text.count("assert (") > 0
    # 断言贴顶层真名（无前后缀）
    assert "`ENV_RF.d_logic_bt_lp_rx_en==" in text
    assert "d_logic_bt_lp_rx_en_to_logic" not in text
    # 直连寄存器 passthrough：RF_WRITE + 断言顶层口
    assert "`ENV_RF.clk_force_on==" in text
    # RO 回读记账注释（不静默丢）
    assert "pll_lock_indicator" in text
    assert "记账" in msg


def test_topout_sv_no_duplicate_labels(mirror, tmp_path):
    import re
    out = tmp_path / "topout2.sv"
    cli.main(["--excel", mirror, "--topout", "--out", str(out), "--mode", "max"])
    labels = re.findall(r"^assert_(\S+):", out.read_text(encoding="utf-8"), re.M)
    assert labels and len(labels) == len(set(labels))


# ───────────── ④ --topout --report ─────────────
def test_topout_report_html(mirror, tmp_path, capsys):
    out = tmp_path / "rep.html"
    cli.main(["--excel", mirror, "--topout", "--report", str(out)])
    assert "Topout 报告已写出" in capsys.readouterr().out
    html = out.read_text(encoding="utf-8")
    assert "d_logic_bt_lp_rx_en" in html
    assert "pll_lock_indicator" in html                 # RO 也在报告（账目 summary，不丢）


def test_topout_report_csv(mirror, tmp_path, capsys):
    out = tmp_path / "rep.csv"
    cli.main(["--excel", mirror, "--topout", "--report", str(out)])
    capsys.readouterr()
    summ = (tmp_path / "rep_summary.csv").read_text(encoding="utf-8-sig")
    assert "d_logic_bt_lp_rx_en" in summ and "pll_lock_indicator" in summ
    assert "clk_force_on" in summ                        # 直连寄存器也在 summary


# ───────────── ⑤ 旧 CLI 路径不破（不带 --topout）─────────────
def test_old_cli_path_still_works(mirror, capsys):
    """不带 --topout → 旧 logic-rooted 路径照常（这里验仍可跑；逐字节回归在主套件其它用例）。"""
    cli.main(["--excel", mirror, "--list"])
    out = capsys.readouterr().out
    assert "可生成信号" in out                            # 旧 --list 文案，与 --topout 不同


# ───────────── ⑥ 无 Topout 页 → 优雅提示 ─────────────
def test_topout_graceful_when_no_topout_page(tmp_path, capsys):
    import fixtures
    xl = tmp_path / "plain.xlsx"
    fixtures.build_workbook(str(xl), with_mux=True)     # 普通夹具，无 Topout 页
    rc = cli.main(["--excel", str(xl), "--topout", "--list"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "无 Topout 页" in out
