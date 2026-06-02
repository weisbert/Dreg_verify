# -*- coding: utf-8 -*-
"""rtl_scan 测试 — 合成 RTL 镜像真实 lpbt_dig_top / bt_lp_pll_dig 结构。

真实结构（2026-06-02 公司机 grep 实证）：
  LPBT_DIG_TOP (lpbt_dig_top.v)
    ├── 顶层 wire/port: d_en_refbuf_ls, N_to_dsm[31:0], d_bt_lp_pll_en ...
    └── U_BT_LP_PLL_DIG (bt_lp_pll_dig.v)
          ├── 内部 wire: pll_n[31:0], mon_active        ← Excel 信号实际在这里
          └── U_BT_LP_DREG ...
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import fixtures                                  # noqa: E402
from dreg_verify import excel_model, rtl_scan    # noqa: E402


TOP_V = """
// auto-generated top
module LPBT_DIG_TOP(
    output           d_en_refbuf_ls                        ,
    output  [31:0]   N_to_dsm                              ,
    input            d_bt_lp_pll_en                        ,
    input            d_bt_lp_pll_dig_dft_iddq_mode
);
    wire    [31:0]   N_to_dsm                                   ;
    wire             d_pfd_en_lnmode_ls                         ;

    /* 子模块例化 */
    BT_LP_PLL_DIG U_BT_LP_PLL_DIG (
        .N_to_dsm                          (N_to_dsm[31:0]                       ),
        .d_en_refbuf_ls                    (d_en_refbuf_ls                       )
    );
endmodule
"""

PLL_DIG_V = """
module BT_LP_PLL_DIG(
    output  [31:0]   N_to_dsm,
    output           d_en_refbuf_ls
);
    wire    [31:0]   pll_n                             ;
    wire             mon_active                        ;
    wire             mon_active_delay                  ;

    BT_LP_DREG U_BT_LP_DREG (
        .pll_n                           (pll_n                                  ),
        .mon_active                      (mon_active                             )
    );
endmodule

module BT_LP_DREG(
    output     [31:0]                    pll_n,
    input                                mon_active
);
    wire       [31:0]                    pll_n1_to_logic;
endmodule
"""


@pytest.fixture(scope="module")
def modules():
    mods = {}
    mods.update(rtl_scan.parse_modules(TOP_V))
    mods.update(rtl_scan.parse_modules(PLL_DIG_V))
    return mods


# ───────────── 解析 ─────────────
def test_parse_modules_signals_and_instances(modules):
    assert set(modules) == {"LPBT_DIG_TOP", "BT_LP_PLL_DIG", "BT_LP_DREG"}
    top = modules["LPBT_DIG_TOP"]
    # 端口 + wire 都收；注释里的字不收
    assert {"d_en_refbuf_ls", "N_to_dsm", "d_bt_lp_pll_en",
            "d_bt_lp_pll_dig_dft_iddq_mode", "d_pfd_en_lnmode_ls"} <= top["signals"]
    assert top["instances"] == [("U_BT_LP_PLL_DIG", "BT_LP_PLL_DIG")]
    pll = modules["BT_LP_PLL_DIG"]
    assert {"pll_n", "mon_active", "mon_active_delay"} <= pll["signals"]
    assert pll["instances"] == [("U_BT_LP_DREG", "BT_LP_DREG")]


def test_signal_map_hierarchy(modules):
    sigmap = rtl_scan.build_signal_map(modules, "LPBT_DIG_TOP")
    # d_en_refbuf_ls 同时是顶层端口和 PLL 子模块端口 → 顶层路径必须在列表里(优先用)
    assert "" in sigmap["d_en_refbuf_ls"]
    assert sigmap["pll_n"] == ["U_BT_LP_PLL_DIG", "U_BT_LP_PLL_DIG.U_BT_LP_DREG"]
    assert sigmap["mon_active"][0] == "U_BT_LP_PLL_DIG"
    # 顶层和子模块都有的信号：顶层路径在列表里
    assert "" in sigmap["N_to_dsm"]


def test_signal_map_depth_limit(modules):
    sigmap = rtl_scan.build_signal_map(modules, "LPBT_DIG_TOP", max_depth=1)
    assert "pll_n" in sigmap                                        # 深度1=U_BT_LP_PLL_DIG 仍包含
    assert sigmap["pll_n"] == ["U_BT_LP_PLL_DIG"]                   # 但不含更深的 U_BT_LP_DREG


# ───────────── Excel 对照 ─────────────
@pytest.fixture(scope="module")
def wb(tmp_path_factory):
    path = tmp_path_factory.mktemp("scan") / "synthetic_pll.xlsx"
    fixtures.build_workbook(str(path), with_pll_chain=True)
    return excel_model.load_workbook(str(path))


def test_match_excel(wb, modules):
    sigmap = rtl_scan.build_signal_map(modules, "LPBT_DIG_TOP")
    prefixes, at_top, missing = rtl_scan.match_excel(wb, sigmap)
    # pll_n / mon_active 在 U_BT_LP_PLL_DIG 里 → 需要前缀
    assert prefixes["pll_n"] == "U_BT_LP_PLL_DIG"
    assert prefixes["mon_active"] == "U_BT_LP_PLL_DIG"
    # d_en_refbuf 的 RTL 名 d_en_refbuf_ls 在顶层 → 无需前缀
    assert "d_en_refbuf_ls" in at_top
    # iddq 输入在顶层 → 无需前缀
    assert "d_bt_lp_pll_dig_dft_iddq_mode" in at_top
    # 合成 Excel 里的 lna_agc(top_output=1)在 RTL 里没有 → 列入缺失
    # (reserve 是 top_output=0 内部信号，本就不收集，不在缺失清单里)
    missing_names = {n for n, _why in missing}
    assert "d_logic_bt_lp_lna_agc" in missing_names
    assert "d_logic_bt_lp_reserve" not in missing_names


def test_render_prefix_file_round_trip(wb, modules):
    """生成的映射文件可直接被 generator.parse_probe_prefix_lines 解析（GUI 导入/CLI 同一路径）。"""
    from dreg_verify import generator
    sigmap = rtl_scan.build_signal_map(modules, "LPBT_DIG_TOP")
    prefixes, at_top, missing = rtl_scan.match_excel(wb, sigmap)
    text = rtl_scan.render_prefix_file(prefixes, at_top, missing, top_module="LPBT_DIG_TOP")
    parsed = generator.parse_probe_prefix_lines(text)
    assert parsed == prefixes                       # 注释行全部被忽略，映射 1:1 还原
    assert "pll_n=U_BT_LP_PLL_DIG" in text
    assert "# d_en_refbuf_ls" in text               # 顶层信号以注释列出
    assert "d_logic_bt_lp_lna_agc" in text          # 缺失信号也在注释里


def test_end_to_end_scan_to_sv(wb, modules, tmp_path):
    """端到端：扫描 RTL → 写映射文件 → CLI --probe-prefix-file → .sv 探针/force 全部带正确路径。"""
    from dreg_verify import generator
    from dreg_verify.cli import _parse_probe_prefixes
    sigmap = rtl_scan.build_signal_map(modules, "LPBT_DIG_TOP")
    prefixes, at_top, missing = rtl_scan.match_excel(wb, sigmap)
    f = tmp_path / "probe_prefixes.txt"
    f.write_text(rtl_scan.render_prefix_file(prefixes, at_top, missing), encoding="utf-8")

    opts = generator.GenOptions(signals=["pll_n", "d_pfd_en_lnmode", "d_en_refbuf"],
                                probe_prefixes=_parse_probe_prefixes([], str(f)))
    res = generator.build(wb, opts)
    assert res["summary"]["n_generated"] == 3 and res["summary"]["n_skipped"] == 0
    text = generator.render(res)
    assert "assert (`ENV_RF.U_BT_LP_PLL_DIG.pll_n[31:0]==" in text      # 子模块输出
    assert "force `ENV_RF.U_BT_LP_PLL_DIG.mon_active=" in text          # 子模块输入
    assert "assert (`ENV_RF.d_en_refbuf_ls==" in text                   # 顶层输出不带前缀
