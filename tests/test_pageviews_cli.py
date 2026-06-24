# -*- coding: utf-8 -*-
"""CLI --page 路径测试（2026-06-24 子视图：logic/mux/dft/iddq 页本地·不 cone 接入 CLI）。

引擎在 test_pageviews.py 已把关；这里覆盖 cli.py 的 --page 分派：
  ① --page logic --list → 列本页行 + 表达式
  ② --page mux (默认)   → 出 .sv（mux 选路断言）
  ③ --page dft --report → HTML 报告
  ④ --page iddq (空页)  → 优雅提示、不崩
  ⑤ 不带 --page → 旧路径不破
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import make_mirror_btlp                              # noqa: E402
from dreg_verify import cli                          # noqa: E402


@pytest.fixture(scope="module")
def mirror(tmp_path_factory):
    p = tmp_path_factory.mktemp("pv_cli") / "mirror_btlp_dreg.xlsx"
    make_mirror_btlp.build(str(p))
    return str(p)


def test_page_logic_list(mirror, capsys):
    cli.main(["--excel", mirror, "--page", "logic", "--list"])
    out = capsys.readouterr().out
    assert "logic 视图 要验信号 8 个（页本地·不 cone）" in out
    assert "d_logic_bt_lp_rx_en" in out and "(A?C:B)&(~D)" in out


def test_page_mux_sv(mirror, tmp_path, capsys):
    out = tmp_path / "mux.sv"
    cli.main(["--excel", mirror, "--page", "mux", "--out", str(out), "--mode", "max"])
    msg = capsys.readouterr().out
    assert "已写出(mux 视图 子视图·页本地)" in msg
    text = out.read_text(encoding="utf-8")
    assert text.count("assert (") > 0
    assert "lna_itrim" in text


def test_page_dft_report(mirror, tmp_path, capsys):
    out = tmp_path / "dft.html"
    cli.main(["--excel", mirror, "--page", "dft", "--report", str(out)])
    assert out.exists()
    html = out.read_text(encoding="utf-8")
    assert "clk_force_on" in html


def test_page_iddq_empty_graceful(mirror, capsys):
    cli.main(["--excel", mirror, "--page", "iddq", "--list"])
    out = capsys.readouterr().out
    assert "没有可显示的行" in out


def test_page_logic_sv_default_name(mirror, tmp_path, capsys, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cli.main(["--excel", mirror, "--page", "logic"])
    assert (tmp_path / "wr_rf_tc_logic.sv").exists()
    text = (tmp_path / "wr_rf_tc_logic.sv").read_text(encoding="utf-8")
    assert text.count("assert (") > 0
