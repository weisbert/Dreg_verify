# -*- coding: utf-8 -*-
"""diag_rtl_gui.py 单测（goal-redzone-binder M2）。

GUI 没法目视运行 → 把【纯数据层】(build_rows/row_matches/detail_text) 全单测，
tkinter 外壳只做 headless 冒烟（无显示则 skip、绝不进 mainloop）+ stdlib-only 守卫。
"""

import ast
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import diag_rtl_binding as D   # noqa: E402
import diag_rtl_gui as G       # noqa: E402


def _result(verdict="unknown", signal="d_sig", probe="d_sig", real_output=None,
            location=None, prefix_check=None, prefix="", candidates=None,
            evidence="ev", exists=None):
    return {"signal": signal, "probe_net": probe, "prefix": prefix,
            "on_missing": "warn", "exists": exists, "location": location,
            "prefix_check": prefix_check, "verdict": verdict, "evidence": evidence,
            "real_output": real_output, "candidates": candidates or []}


# ───────────── build_rows ─────────────
def test_build_rows_output():
    rows = G.build_rows([_result(verdict="output", signal="d_o", probe="d_o_to_mux")])
    row = rows[0]
    assert row["tag"] == "output"
    assert row["cols"][0] == "d_o" and row["cols"][1] == "✓ OUTPUT"
    assert row["cols"][2] == "d_o_to_mux"
    assert row["cols"][3] == "—"          # output 行真输出建议列 = —
    assert row["key"] == 0


def test_build_rows_unknown_non_missing():
    rows = G.build_rows([_result(verdict="unknown", real_output="d_g6_to_mux",
                                 location="driven(层级未定位)")])
    assert rows[0]["tag"] == "unknown"
    assert rows[0]["cols"][3] == "d_g6_to_mux"


def test_build_rows_amber_missing():
    """unknown + location=='missing' → amber_missing（那 148 类的强信号）。"""
    rows = G.build_rows([_result(verdict="unknown", real_output="d_g6_to_mux",
                                 location="missing")])
    assert rows[0]["tag"] == "amber_missing"
    assert "❌missing" in rows[0]["cols"][4]


def test_build_rows_input_suspect():
    rows = G.build_rows([_result(verdict="input-suspect", real_output="d_x_to_mux")])
    assert rows[0]["tag"] == "input_suspect"
    assert rows[0]["cols"][1] == "⚠ INPUT-suspect"


def test_build_rows_mismatch_prepends_warn():
    rows = G.build_rows([_result(verdict="unknown", location="top",
                                 prefix_check="mismatch")])
    assert rows[0]["tag"] == "mismatch"
    assert rows[0]["cols"][4].startswith("⚠ ")


def test_build_rows_tag_priority_suspect_over_mismatch():
    """优先级 input_suspect > mismatch：同现时取 input_suspect。"""
    rows = G.build_rows([_result(verdict="input-suspect", location="top",
                                 prefix_check="mismatch")])
    assert rows[0]["tag"] == "input_suspect"


def test_build_rows_tag_priority_mismatch_over_amber_missing():
    """优先级 mismatch > amber_missing：unknown+missing+mismatch 取 mismatch（橙压琥珀）。"""
    rows = G.build_rows([_result(verdict="unknown", location="missing",
                                 prefix_check="mismatch")])
    assert rows[0]["tag"] == "mismatch"


def test_build_rows_keys_enumerate():
    rs = [_result(signal="a"), _result(signal="b"), _result(signal="c")]
    rows = G.build_rows(rs)
    assert [r["key"] for r in rows] == [0, 1, 2]


# ───────────── row_matches ─────────────
_ALL = {"output", "unknown", "input-suspect"}


def test_row_matches_empty_query_passes_text():
    assert G.row_matches(_result(verdict="unknown"), "", _ALL) is True


def test_row_matches_substring_signal():
    r = _result(signal="d_wl_rf_lna_gain")
    assert G.row_matches(r, "lna", _ALL) is True
    assert G.row_matches(r, "nope", _ALL) is False


def test_row_matches_substring_probe_and_real():
    r = _result(probe="d_p_to_logic", real_output="d_p_to_mux")
    assert G.row_matches(r, "to_logic", _ALL) is True
    assert G.row_matches(r, "to_mux", _ALL) is True


def test_row_matches_case_insensitive():
    r = _result(signal="d_g6")
    assert G.row_matches(r, "G6", _ALL) is True


def test_row_matches_verdict_filter():
    r = _result(verdict="output")
    assert G.row_matches(r, "", {"unknown"}) is False
    assert G.row_matches(r, "", {"output"}) is True


def test_row_matches_empty_verdict_set_false():
    assert G.row_matches(_result(verdict="output"), "", set()) is False


def test_row_matches_and_semantics():
    """query 命中但 verdict 不在集合 → False（AND）。"""
    r = _result(verdict="output", signal="d_g6")
    assert G.row_matches(r, "g6", {"unknown"}) is False


# ───────────── detail_text（核心）─────────────
def test_detail_text_148_class():
    """unknown+missing，真输出 d_g6_to_mux，多 candidates，ctx 有驱动 assign。"""
    ctx = D.build_context(["assign d_g6_to_mux = sel ? aa : bb;"])
    r = _result(verdict="unknown", signal="d_g6", probe="d_g6", location="missing",
                real_output="d_g6_to_mux",
                candidates=["d_g6_to_mux", "d_g6_t0_to_mux", "d_g6_t1_to_mux"],
                evidence="探针探裸名(RTL 无此网)，真网带尾缀 → 应改探 d_g6_to_mux")
    txt = G.detail_text(r, ctx)
    assert "找不到" in txt and "missing" not in txt.replace("❌", "")  # 用人话不裸露 missing
    for c in r["candidates"]:
        assert c in txt
    assert "d_g6_to_mux" in txt
    assert "assign d_g6_to_mux" in txt and "aa" in txt and "bb" in txt
    assert "← 建议探这根" in txt


def test_detail_text_input_suspect():
    ctx = D.build_context(["assign d_x_to_mux = sel ? a : d_x_to_logic;"])
    r = _result(verdict="input-suspect", signal="d_x", probe="d_x_to_logic",
                real_output="d_x_to_mux",
                evidence="探针网出现在本信号 assign 的右边 = 它是输入")
    txt = G.detail_text(r, ctx)
    assert "d_x_to_mux" in txt           # 真输出建议
    assert "右边" in txt
    assert "assign d_x_to_mux" in txt     # 那条 assign 原文


def test_detail_text_output():
    ctx = D.build_context(["assign d_o_to_mux = ctl ? p : q;"])
    r = _result(verdict="output", signal="d_o", probe="d_o_to_mux",
                location="top", evidence="探针网 = 本信号 assign 的左边")
    txt = G.detail_text(r, ctx)
    assert "assign d_o_to_mux" in txt


def test_detail_text_no_driver_does_not_leave_blank():
    """real_output 有但 find_driving_assigns 返 []（驱动来自端口连接）→ 显式提示、不留空、不崩。"""
    ctx = D.build_context(["module m(input a, output b); endmodule"])
    r = _result(verdict="unknown", signal="d_p", probe="d_p", real_output="d_p_to_mux",
                location="missing")
    txt = G.detail_text(r, ctx)
    assert "未找到 assign 驱动" in txt and "人工核对" in txt


def test_detail_text_no_prefix_placeholder():
    ctx = D.build_context(["assign d_o_to_mux = a;"])
    r = _result(verdict="output", signal="d_o", probe="d_o_to_mux", prefix="")
    txt = G.detail_text(r, ctx)
    assert "未配前缀" in txt           # ① 节里『前缀核对：—（未配前缀）』


def test_detail_text_unknown_no_suggestion_not_whitewashed():
    """工具判不准的 UNKNOWN（real_output=None）绝不能写成『已认定真输出/无需改探』(假安全感)。"""
    ctx = D.build_context(["module m(input a, output b); endmodule"])
    r = _result(verdict="unknown", signal="d_mystery", probe="d_mystery",
                location="missing", real_output=None)
    txt = G.detail_text(r, ctx)
    assert "无需改探" not in txt
    assert "凭什么是输出" not in txt
    assert "人工核对" in txt


def test_detail_text_output_prefix_mismatch_not_whitewashed():
    """探针网对、但前缀对不上真层级 → ③ 不能说『无需改探』，要指向 ⑤ 改前缀。"""
    ctx = D.build_context(["assign d_o_to_mux = a;"])
    r = _result(verdict="output", signal="d_o", probe="d_o_to_mux",
                location="top", prefix_check="mismatch", prefix="ENV.WRONG",
                real_output=None)
    txt = G.detail_text(r, ctx)
    assert "无需改探" not in txt
    assert "前缀" in txt


def test_detail_text_marks_input_net_candidate():
    """候选里属于 input_nets 的网要标『别探=假绿』，不能和真输出并列诱导探它。"""
    ctx = D.build_context(["assign d_g_to_mux = sel ? d_g_local : d_g_line;"])
    r = _result(verdict="unknown", signal="d_g", probe="d_g", location="missing",
                real_output="d_g_to_mux",
                candidates=["d_g_to_mux", "d_g_local", "d_g_line"])
    r["input_nets"] = ["d_g_local", "d_g_line"]
    txt = G.detail_text(r, ctx)
    assert "← 建议探这根" in txt        # d_g_to_mux
    assert "输入网，别探" in txt        # d_g_local / d_g_line


# ───────────── headless 冒烟（无显示则 skip）─────────────
@pytest.fixture(scope="session")
def _tk_root():
    """整个测试会话只建一次 Tk()——反复 Tk()/destroy() 会触发 init.tcl 竞态，
    导致【有显示的机器上也随机 skip】，冒烟兜底形同虚设。建一次、各测试用 Toplevel 隔离。"""
    if G.tk is None:
        pytest.skip("no tkinter")
    try:
        root = G.tk.Tk()
        root.withdraw()
    except Exception as e:    # noqa: BLE001  tk.TclError 等 —— 真·无 DISPLAY
        pytest.skip("no display: %r" % e)
    yield root
    try:
        root.destroy()
    except Exception:         # noqa: BLE001
        pass


@pytest.fixture
def app(_tk_root):
    try:
        win = G.tk.Toplevel(_tk_root)
        a = G.App(win)
    except Exception as e:    # noqa: BLE001  显示中途掉线也降级为 skip、不 ERROR
        pytest.skip("no display: %r" % e)
    try:
        yield a
    finally:
        try:
            win.destroy()
        except Exception:     # noqa: BLE001
            pass


def _smoke_results():
    return [
        _result(verdict="output", signal="d_out", probe="d_out_to_mux"),
        _result(verdict="unknown", signal="d_unk", probe="d_unk", location="missing",
                real_output="d_unk_to_mux"),
        _result(verdict="input-suspect", signal="d_in", probe="d_in_to_logic",
                real_output="d_in_to_mux"),
    ]


def test_smoke_default_filter_rows(app):
    ctx = D.build_context(["assign d_unk_to_mux = a; assign d_in_to_mux = s ? b : d_in_to_logic;"])
    app.set_results(_smoke_results(), ctx)
    # 默认勾 suspect+unknown（不勾 output）→ 应显 2 行
    assert len(app.tree.get_children()) == 2


def test_smoke_enable_output_shows_more(app):
    app.set_results(_smoke_results(), D.build_context([""]))
    before = len(app.tree.get_children())
    app.f_output.set(True)
    app.refresh()
    assert len(app.tree.get_children()) > before


def test_smoke_search_narrows(app):
    app.set_results(_smoke_results(), D.build_context([""]))
    app.f_output.set(True)
    app.refresh()
    full = len(app.tree.get_children())
    app.search_var.set("d_in")
    app.refresh()
    assert len(app.tree.get_children()) < full


def test_smoke_select_fills_detail(app):
    ctx = D.build_context(["assign d_in_to_mux = s ? b : d_in_to_logic;"])
    app.set_results(_smoke_results(), ctx)
    kids = app.tree.get_children()
    assert kids
    app.tree.selection_set(kids[0])
    app._on_select()
    body = app.detail.get("1.0", "end")
    assert body.strip()                       # 详情非空
    r = app._selected_result()
    assert r["probe_net"] in body              # 含探针网


def test_smoke_counts_show_full_set_not_filtered(app):
    """计数始终显示全集，不随过滤变。"""
    app.set_results(_smoke_results(), D.build_context([""]))
    # 默认过滤只显 2 行，但 OUTPUT 计数仍为 1
    assert "✓OUTPUT 1" in app.cnt_output.get()
    assert "（共 3）" in app.cnt_output.get()


def test_smoke_quick_filter_output_additive(app):
    """点 OUTPUT 计数 = 把 output 也勾上（叠加，不动 suspect/unknown）。"""
    app.set_results(_smoke_results(), D.build_context([""]))
    app._quick_filter("output")
    assert app.f_output.get() is True
    assert app.f_suspect.get() is True and app.f_unknown.get() is True


def test_smoke_quick_filter_unknown_exclusive(app):
    """点 UNKNOWN 计数 = 只看 unknown（互斥重置）。"""
    app.set_results(_smoke_results(), D.build_context([""]))
    app._quick_filter("unknown")
    assert app.f_unknown.get() is True
    assert app.f_suspect.get() is False and app.f_output.get() is False
    # 只剩 unknown 那 1 行
    assert len(app.tree.get_children()) == 1


def test_smoke_filter_out_selected_clears_detail(app):
    """选中一行后把它过滤掉 → 详情面板清空，不留陈旧内容。"""
    ctx = D.build_context(["assign d_in_to_mux = s ? b : d_in_to_logic;"])
    app.set_results(_smoke_results(), ctx)
    app.f_output.set(True)
    app.refresh()
    app.tree.selection_set("0")          # d_out（results[0]）
    app._on_select()
    assert app.detail.get("1.0", "end").strip()      # 详情已填
    app.search_var.set("zzz_no_such_signal")         # 全过滤掉
    app.refresh()
    assert app.detail.get("1.0", "end").strip() == ""  # 详情被清空


# ───────────── stdlib-only 守卫 ─────────────
def test_diag_rtl_gui_is_stdlib_only():
    """diag_rtl_gui.py 全部 import（含 try 块内 / 函数内）只许标准库 + tkinter + 同目录红区脚本。

    ⚠ 必须 ast.walk 走全树、不能只看 tree.body——tkinter import 故意在 try 块里，
       只扫顶层会【根本看不到】，第三方依赖就能塞进 try/函数里溜进红区（实证 numpy/requests 漏检）。"""
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "redzone_tools", "diag_rtl_gui.py")
    with open(path, "r", encoding="utf-8") as f:
        src = f.read()
    tree = ast.parse(src)
    allowed = {"argparse", "json", "os", "re", "sys", "functools", "tkinter",
               "diag_rtl_binding", "scan_rtl"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                root = a.name.split(".")[0]
                assert root in allowed, "import %s 破坏零依赖（任意位置）" % a.name
        elif isinstance(node, ast.ImportFrom):
            if node.module is None:        # 相对 import（本文件没有，防御）
                continue
            root = node.module.split(".")[0]
            assert root in allowed, "from %s import 破坏零依赖（任意位置）" % node.module
    # 白名单已是主守卫；再对常见第三方做全文兜底断言
    for bad in ("openpyxl", "PyQt", "PySide", "numpy", "pandas", "requests", "scipy"):
        assert bad not in src, "不该依赖 %s" % bad


def test_no_egress_controls_in_source():
    """铁律：源码不得出现写 .sv / 写外部 / 保存对话框 / 联网。"""
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "redzone_tools", "diag_rtl_gui.py")
    with open(path, "r", encoding="utf-8") as f:
        src = f.read()
    for bad in ("asksaveasfilename", "socket", "urllib", "requests", "http.client",
                "smtplib", "ftplib"):
        assert bad not in src, "出现疑似 egress: %s" % bad
    # 不得有写文件：扫 AST 的 open(...) 调用，禁止 'w'/'a'/'x'/'wb' 等写模式
    # （字符串扫描会被 tkinter 的 anchor="w" 误伤，故用 AST 精确判 open 第二实参）
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "open":
            for a in node.args[1:2]:           # open 的 mode 实参
                # ast.Constant（3.8+ 规范；ast.Str 在 3.14 移除，别用）
                if isinstance(a, ast.Constant) and isinstance(a.value, str):
                    m = a.value
                    assert "w" not in m and "a" not in m and "x" not in m, \
                        "open(...,%r) 是写模式，违反只读铁律" % m
    # filedialog 只用 askopenfilename
    assert "askopenfilename" in src
