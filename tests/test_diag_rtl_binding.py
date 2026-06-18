# -*- coding: utf-8 -*-
"""diag_rtl_binding.py 单测（goal-redzone-binder M1）。

真 RTL 在红区碰不到 → 用手写 mock Verilog 证明 assign 解析 + 输出/输入判定的逻辑对：
headline = 探针落在本信号 assign 的右边(=探到自己输入)=假绿嫌疑，要被揪出并给真输出建议。
"""

import ast
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import diag_rtl_binding as D   # noqa: E402


def _ctx(*texts, sigmap=None):
    return D.build_context(list(texts), sigmap=sigmap)


def _pc(net_base, signal=None, prefix="", input_nets=None, on_missing="warn"):
    return {"kind": "probe", "net_base": net_base, "signal": signal or net_base,
            "prefix": prefix, "input_nets": input_nets or [], "on_missing": on_missing}


# ───────────── 纯解析器 ─────────────
def test_parse_assigns_basic_and_concat():
    a = D.parse_assigns("assign x = a & b; assign {hi, lo[1:0]} = data;")
    assert a[0]["lhs_bases"] == ["x"] and a[0]["rhs_names"] == {"a", "b"}
    assert a[1]["lhs_bases"] == ["hi", "lo"]


def test_rhs_drops_number_literals():
    """4'd3 / 8'hFF / 'b0 不能被当成网名（否则 d3/hFF/b0 污染 RHS 指纹）。"""
    a = D.parse_assigns("assign y = 4'd3 + sigA - 8'hFF | 1'b0;")
    assert a[0]["rhs_names"] == {"sigA"}


def test_parse_always_lhs():
    s = D.parse_always_lhs("always @(posedge clk) begin q <= d; r = s; end")
    assert "q" in s and "r" in s


def test_parse_port_dirs():
    ins, outs = D.parse_port_dirs("module m(input [3:0] a, output b); endmodule")
    assert "a" in ins and "b" in outs


# ───────────── 单信号诊断：核心判定 ─────────────
def test_probe_on_assign_lhs_is_output():
    ctx = _ctx("assign d_x_to_mux = sel ? a_local : d_x_to_logic;")
    r = D.diagnose(_pc("d_x_to_mux", signal="d_x"), ctx)
    assert r["verdict"] == "output"


def test_self_ref_probe_on_rhs_is_input_suspect():
    """R32/R40 假绿型：本信号真输出是 d_x_to_mux，探针却配到 d_x_to_logic(=它的右边输入)。
    必须判 INPUT-suspect 并建议真输出 d_x_to_mux。"""
    ctx = _ctx("assign d_x_to_mux = sel ? a_local : d_x_to_logic;")
    r = D.diagnose(_pc("d_x_to_logic", signal="d_x",
                       input_nets=["sel", "a_local", "d_x_to_logic"]), ctx)
    assert r["verdict"] == "input-suspect"
    assert r["real_output"] == "d_x_to_mux"


def test_fingerprint_picks_right_assign_among_same_prefix():
    """同基名两个 assign，靠 claim.input_nets 与 RHS 的交集择优(否则会挑错那条)。"""
    rtl = ("assign d_g_to_mux  = sel  ? aa : bb;\n"
           "assign d_g_to_logic = ctrl ? cc : dd;\n")
    # 探针落在第二条(d_g_to_logic)的右边 cc；指纹 input_nets 对上第二条
    r = D.diagnose(_pc("cc", signal="d_g", input_nets=["ctrl", "cc", "dd"]), _ctx(rtl))
    assert r["verdict"] == "input-suspect" and r["real_output"] == "d_g_to_logic"


def test_registered_output_via_always_is_output():
    ctx = _ctx("module m(); reg q; always @(posedge clk) q <= d_in; endmodule")
    r = D.diagnose(_pc("q", signal="q"), ctx)
    assert r["verdict"] == "output"


def test_pure_input_port_probe_is_input_suspect():
    ctx = _ctx("module m(input d_in_only, output d_o); assign d_o = d_in_only; endmodule")
    r = D.diagnose(_pc("d_in_only", signal="d_in_only"), ctx)
    assert r["verdict"] == "input-suspect"


def test_unknown_when_no_evidence():
    """探针网既无本信号 assign、又非显式 input、也不是任何 LHS → 老实 UNKNOWN，不瞎下结论。"""
    ctx = _ctx("module m(); wire w; endmodule")
    r = D.diagnose(_pc("d_driven_by_port", signal="d_driven_by_port"), ctx)
    assert r["verdict"] == "unknown"


# ───────────── 存在性 / 层级（复用 scan_rtl sigmap）─────────────
def test_existence_and_location_from_sigmap():
    ctx = _ctx("assign d_real = a;", sigmap={"d_real": ["", "U_SUB"]})
    assert D.diagnose(_pc("d_real"), ctx)["location"] == "top"
    miss = D.diagnose(_pc("d_ghost"), ctx)
    assert miss["exists"] is False and miss["location"] == "missing"


def test_prefix_check_ok_and_mismatch():
    ctx = _ctx("assign pll_n = a;", sigmap={"pll_n": ["U_BT_LP_PLL_DIG"]})
    ok = D.diagnose(_pc("pll_n", prefix="ENV_RF.U_BT_LP_PLL_DIG"), ctx)
    bad = D.diagnose(_pc("pll_n", prefix="ENV_RF.WRONG_MOD"), ctx)
    assert ok["prefix_check"] == "ok" and bad["prefix_check"] == "mismatch"


def test_report_renders_and_counts():
    ctx = _ctx("assign d_x_to_mux = sel ? a : d_x_to_logic;")
    results = D.diagnose_claims(
        [_pc("d_x_to_mux", signal="d_x"),
         _pc("d_x_to_logic", signal="d_x", input_nets=["sel", "a", "d_x_to_logic"])], ctx)
    txt = D.format_report(results)
    assert "INPUT-suspect" in txt and "汇总" in txt


# ───────────── 候选搜全网域 + 报告分组/筛选 ─────────────
def test_universe_candidates_surface_suffixed_real_net_for_missing_bare():
    """裸名 RTL 里没有，但带后缀真网存在(sigmap/assign 里) → candidates 把它捞出来当线索。"""
    ctx = _ctx("assign d_x_to_mux = sel ? a : b;",
               sigmap={"d_x_to_mux": [""], "d_x_to_logic": ["U_SUB"]})
    r = D.diagnose(_pc("d_x", signal="d_x"), ctx)        # 探裸名 d_x（RTL 没有）
    assert r["exists"] is False
    assert "d_x_to_mux" in r["candidates"] and "d_x_to_logic" in r["candidates"]


def test_report_summary_on_top_and_groups():
    ctx = _ctx("assign d_x_to_mux = s ? a : d_x_to_logic;")
    results = D.diagnose_claims(
        [_pc("d_x_to_mux", signal="d_x"),
         _pc("d_x_to_logic", signal="d_x", input_nets=["s", "a", "d_x_to_logic"])], ctx)
    txt = D.format_report(results)
    assert txt.startswith("===") and "汇总：" in txt
    assert "──── ⚠ INPUT-suspect" in txt and "──── ✓ OUTPUT" in txt


def test_canonical_suffix_real_output_preferred_over_subvariant():
    """真网有规范 X_to_mux 和一堆 X_tN_to_mux 时，真输出建议应指向 X_to_mux、而非子变体
    （真表反馈：bias_trim_g6 建议曾错指 _t0_to_mux，应指 _g6_to_mux）。"""
    rtl = "assign d_g6_t0_to_mux = a; assign d_g6_to_mux = b;"
    ctx = _ctx(rtl, sigmap={"d_g6_t0_to_mux": [""], "d_g6_to_mux": [""]})
    r = D.diagnose(_pc("d_g6", signal="d_g6"), ctx)     # 探裸名 d_g6（RTL 无）
    assert r["real_output"] == "d_g6_to_mux"
    assert "应改探 d_g6_to_mux" in r["evidence"]


def test_report_only_filter_hides_output_detail():
    ctx = _ctx("assign d_x_to_mux = s ? a : d_x_to_logic;")
    results = D.diagnose_claims([_pc("d_x_to_mux", signal="d_x")], ctx)   # 一个 output
    full = D.format_report(results)
    filt = D.format_report(results, only={"input-suspect", "unknown"})
    assert "──── ✓ OUTPUT" in full and "──── ✓ OUTPUT" not in filt
    assert "✓OUTPUT 1" in filt    # 摘要计数仍含全部


# ───────────── find_driving_assigns（M2 新增纯函数）─────────────
def test_find_driving_assigns_hit():
    ctx = _ctx("assign d_x_to_mux = sel ? a : b;")
    drivers = D.find_driving_assigns("d_x_to_mux", ctx)
    assert len(drivers) == 1
    assert "d_x_to_mux" in drivers[0]["lhs_raw"]
    assert "sel ? a : b" in drivers[0]["rhs_raw"]


def test_find_driving_assigns_with_bit_select():
    """带位选 d_x_to_mux[3:0] 按基名仍命中。"""
    ctx = _ctx("assign d_x_to_mux = sel ? a : b;")
    drivers = D.find_driving_assigns("d_x_to_mux[3:0]", ctx)
    assert len(drivers) == 1 and "d_x_to_mux" in drivers[0]["lhs_raw"]


def test_find_driving_assigns_not_present():
    ctx = _ctx("assign d_x_to_mux = sel ? a : b;")
    assert D.find_driving_assigns("d_nope", ctx) == []


def test_find_driving_assigns_concat_lhs():
    """concat LHS：assign {d_x_to_mux, d_y_to_mux}=... 时按基名命中该条。"""
    ctx = _ctx("assign {d_x_to_mux, d_y_to_mux} = data;")
    dy = D.find_driving_assigns("d_y_to_mux", ctx)
    assert len(dy) == 1 and "d_y_to_mux" in dy[0]["lhs_raw"]


def test_find_driving_assigns_exact_match_not_startswith():
    """⭐关键：精确基名匹配，不用 startswith。
    裸名 d_x 非任何 LHS → []；d_x_to_mux 只返自己那条，不误捞同前缀的 d_xy_to_logic。"""
    ctx = _ctx("assign d_x_to_mux = a;\nassign d_xy_to_logic = b;\n")
    assert D.find_driving_assigns("d_x", ctx) == []
    hit = D.find_driving_assigns("d_x_to_mux", ctx)
    assert len(hit) == 1 and "d_x_to_mux" in hit[0]["lhs_raw"]
    assert "d_xy_to_logic" not in hit[0]["lhs_raw"]


def test_find_driving_assigns_multiple_same_base_preserve_order():
    """多条同基名（concat + 直驱）全返回且保 RTL 出现顺序。"""
    ctx = _ctx("assign {d_z_to_mux, other} = p;\nassign d_z_to_mux = q;\n")
    drivers = D.find_driving_assigns("d_z_to_mux", ctx)
    assert len(drivers) == 2
    # 第一条是 concat（rhs=p），第二条是直驱（rhs=q）—— 保 RTL 顺序
    assert drivers[0]["rhs_raw"] == "p" and drivers[1]["rhs_raw"] == "q"


# ───────────── load_results 抽取 + CLI 黄金回归 ─────────────
def _fake_scan(monkeypatch):
    """monkeypatch scan_rtl 的 RTL 加载，使 load_results 不碰真磁盘。"""
    import scan_rtl
    monkeypatch.setattr(scan_rtl, "_infer_from_dreg_env", lambda args: None)
    monkeypatch.setattr(scan_rtl, "find_verilog_files", lambda dirs: ["fake_top.v"])
    monkeypatch.setattr(scan_rtl, "scan_files",
                        lambda files: {"TOP": {"signals": {"d_x_to_mux"}, "instances": []}})
    monkeypatch.setattr(scan_rtl, "resolve_top_module", lambda tm, mods, top: "TOP")
    monkeypatch.setattr(scan_rtl, "build_signal_map",
                        lambda mods, top, max_depth=16: {"d_x_to_mux": [""]})
    monkeypatch.setattr(scan_rtl, "parse_modules", lambda text: {"TOP": None})


def _write_claims(tmp_path):
    import json
    from types import SimpleNamespace
    claims = {"claims": [
        {"kind": "probe", "net_base": "d_x_to_mux", "signal": "d_x",
         "prefix": "", "input_nets": ["sel", "a", "d_x_to_logic"], "on_missing": "warn"},
        {"kind": "force", "net_base": "ignore_me"},   # 非 probe → 被过滤
    ]}
    p = tmp_path / "claims.json"
    p.write_text(json.dumps(claims), encoding="utf-8")
    return SimpleNamespace(claims=str(p), top="fake_top.v", top_module=None,
                           rtl_dirs="rtl", max_depth=16, out=None, only=None,
                           nets=None, excel=None)


def test_load_results_structure(tmp_path, monkeypatch):
    _fake_scan(monkeypatch)
    args = _write_claims(tmp_path)
    # load_results 内部读 fake_top.v 文本 → 写一份让 open 能读到
    (tmp_path / "fake_top.v").write_text("assign d_x_to_mux = sel ? a : b;", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    args.top = "fake_top.v"
    ctx, results, meta = D.load_results(args, log=lambda m: None)
    assert isinstance(ctx, dict) and "assigns" in ctx
    assert len(results) == 1 and results[0]["signal"] == "d_x"   # force 被过滤
    assert meta["n_probes"] == 1 and meta["top_module"] == "TOP"
    assert set(meta) == {"claims_name", "n_probes", "n_files", "top_module",
                         "n_nets", "n_assigns"}


def test_load_results_golden_log_lines(tmp_path, monkeypatch):
    """黄金串：log 收集器跑 load_results，三条打印行文本/顺序与 CLI 一字不差。"""
    _fake_scan(monkeypatch)
    args = _write_claims(tmp_path)
    (tmp_path / "fake_top.v").write_text("assign d_x_to_mux = sel ? a : b;", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    args.top = "fake_top.v"
    logs = []
    D.load_results(args, log=logs.append)
    assert logs[0] == "claims: claims.json（1 探针）"
    assert logs[1] == "RTL 文件: 1 个"
    assert logs[2].startswith("DUT 顶层: TOP  RTL 网总数: 1  assign 语句: ")
    assert logs[2].endswith("\n")
    assert len(logs) == 3


def test_load_results_is_read_only_no_egress():
    """GUI 把全部文件 IO 委托给 load_results → 这个函数必须钉死【只读】：
    无写模式 open、无 socket/urllib/subprocess 等外发符号。否则将来有人给它加写/缓存，
    会在 GUI 侧静默开一条 egress 路径，而 GUI 自己的源码守卫一条都抓不到（红区头号铁律无人值守）。"""
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "redzone_tools", "diag_rtl_binding.py")
    with open(path, "r", encoding="utf-8") as f:
        src = f.read()
    tree = ast.parse(src)
    fn = next((n for n in ast.walk(tree)
               if isinstance(n, ast.FunctionDef) and n.name == "load_results"), None)
    assert fn is not None, "找不到 load_results"
    bad = {"socket", "urllib", "subprocess", "requests", "http", "smtplib", "ftplib"}
    for node in ast.walk(fn):
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "open":
            for a in node.args[1:2]:                 # open 的 mode 实参
                if isinstance(a, ast.Constant) and isinstance(a.value, str):
                    m = a.value
                    assert "w" not in m and "a" not in m and "x" not in m, \
                        "load_results 出现写模式 open(...,%r)" % m
        if isinstance(node, ast.Name):
            assert node.id not in bad, "load_results 引用外发符号 %s" % node.id
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            assert node.value.id not in bad, \
                "load_results 引用外发符号 %s.%s" % (node.value.id, node.attr)
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            mod = (node.names[0].name if isinstance(node, ast.Import) else node.module) or ""
            assert mod.split(".")[0] not in bad, "load_results 内 import 外发模块 %s" % mod
