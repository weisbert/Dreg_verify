# -*- coding: utf-8 -*-
"""完整账目 + 静默过滤可见性（2026-06-04）。

用户诉求：不喜欢被跳过、怕"出了问题却不知道在哪"。这两个特性保证：
  ① 唯一默认静默减少的一类（logic 内部节点 top_output=0）被拎出来有名有姓
  ② --account 把每个 logic 信号 + mux 组的去向列全，一个不漏
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dreg_verify import excel_model, generator  # noqa: E402
from fixtures import build_workbook, build_wl_workbook  # noqa: E402


def test_filtered_internal_surfaced(tmp_path):
    """top_output_only（CLI 默认）滤掉的 logic 内部节点 → filtered_internal 有名有姓。"""
    p = build_workbook(str(tmp_path / "l.xlsx"))      # LPBT：reserve 行 N=0（内部节点）
    wb = excel_model.load_workbook(p)
    # GenOptions 默认 top_output_only=False → 不过滤，列表空
    res0 = generator.build(wb, generator.GenOptions())
    assert res0["summary"]["n_filtered_internal"] == 0
    # top_output_only=True（CLI 默认）→ reserve 被静默过滤，但现在列出来了
    res1 = generator.build(wb, generator.GenOptions(top_output_only=True))
    fi = res1["filtered_internal"]
    assert res1["summary"]["n_filtered_internal"] == len(fi) > 0
    names = {s.out_name for s in fi}
    assert any("reserve" in n for n in names), names
    # 这些信号确实不在生成块里
    gen = {st["out_name"] for _l, st in res1["blocks"]}
    assert names.isdisjoint(gen)


def test_account_accounts_for_everything(tmp_path):
    """compose_account：每个 logic 信号 + 每个 mux 组都有一行去向，一个不漏。"""
    p = build_wl_workbook(str(tmp_path / "wl.xlsx"))
    wb = excel_model.load_workbook(p)
    opts = generator.GenOptions()
    res = generator.build(wb, opts)
    items = generator.compose_account(wb, opts, res)
    logic_items = [i for i in items if i["kind"] == "logic"]
    mux_items = [i for i in items if i["kind"] == "mux"]
    # 数量 = 表里全部（不漏任何一个）
    assert len(logic_items) == len(wb.logic)
    assert len(mux_items) == len(wb.mux)
    # 每一行都有名字 + 去向（无空白）
    assert all(i["name"] and i["disposition"] for i in items)
    # WL mux 默认无前缀 → 全部"生成(裸名探针)"
    assert all(i["disposition"].startswith("生成") for i in mux_items)
    assert any("裸名" in i["disposition"] for i in mux_items)
    # logic：fb_en 与 lna_gain_dly 都生成——后者输入是 mux 输出，R38 跨 logic↔mux 边界展开把该 mux
    # 拆成源寄存器(选择 mode + 数据 local/线控)端到端驱动(对齐 VBA)，不再因"需前缀"被跳过；都在账目里。
    by_name = {i["name"]: i for i in logic_items}
    dly = next(i for n, i in by_name.items() if "lna_gain_dly" in n)
    assert dly["disposition"].startswith("生成")
    # 展开后的输入 = mux 的源寄存器(全 tmm 干净叶子，无需探针前缀)
    drep = next(t for t in generator.report(wb, opts)["tables"] if "lna_gain_dly" in t["signal"])
    dlabels = {x["label"] for x in drep["inputs"]}
    assert any("lna_gain_ctrl_mode" in n for n in dlabels)       # mux 选择
    assert any("lna_gain_local" in n for n in dlabels)            # mux 寄存器数据
    assert any("linectrl_lna_gain" in n for n in dlabels)         # mux 线控数据
    # 原来那根 mux 输出衔接网不再作为输入残留（已被拆成上面三块源寄存器）
    assert not any(n == "d_wl_rf_lna_gain" or "lna_gain_to_logic" in n for n in dlabels)


def test_account_disposition_with_top_output_filter(tmp_path):
    """top_output_only 下：内部节点在账目里标'过滤'+原因（不是凭空消失）。"""
    p = build_workbook(str(tmp_path / "l.xlsx"))
    wb = excel_model.load_workbook(p)
    opts = generator.GenOptions(top_output_only=True)
    res = generator.build(wb, opts)
    items = generator.compose_account(wb, opts, res)
    reserve = next(i for i in items if "reserve" in i["name"])
    assert reserve["disposition"] == "过滤"
    assert "内部节点" in reserve["reason"]


def test_cli_account_smoke(tmp_path, capsys):
    """CLI --account：打印完整账目，含小结 + 每组去向。"""
    from dreg_verify import cli
    p = build_wl_workbook(str(tmp_path / "wl.xlsx"))
    cli.main(["--excel", str(p), "--account"])
    out = capsys.readouterr().out
    assert "完整账目" in out
    assert "小结" in out
    # 5 个 mux 组都列出来了
    for n in range(1, 6):
        assert "mux%d" % n in out


def test_cli_report_shows_filtered_internal(tmp_path, capsys):
    """CLI 生成摘要：默认过滤的内部节点有一行 ℹ 提示 + 名字（不再静默）。"""
    from dreg_verify import cli
    p = build_workbook(str(tmp_path / "l.xlsx"))      # 含 reserve 内部节点
    out_sv = tmp_path / "l.sv"
    cli.main(["--excel", str(p), "--out", str(out_sv)])    # CLI 默认 top_output_only=True
    out = capsys.readouterr().out
    assert "logic 内部节点" in out
    assert "--include-internal" in out
