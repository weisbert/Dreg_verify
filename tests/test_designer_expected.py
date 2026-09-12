# -*- coding: utf-8 -*-
"""auto_out / 期望 分离（防自证验证，2026-06-03 第十一轮）的回归测试：

  Dreg 验证的对象是 designer 写的逻辑表达式本身；用表达式算出的 auto_out 当期望
  去验证表达式有自证嫌疑。因此：
  · TestVector 增加 designer_expected（designer 手填期望），.sv 断言优先用它；
    未填 → auto_out(exp_value) 兜底；手填 != auto_out 不算负向（仿真 FAIL = 表达式 bug）。
  · HTML 报告真值表拆两行 + 新增「真值表检查」tab（遮 auto_out、填期望、回车判定）。

2026-09-12（GUI v2 C5-a）：本文件原有 50 条，其中 22 条打的是 v1 门面
（`legacy_gui.MainWindow`），能力已由 v2 契约测试接手，逐条处置记在
`docs/GUI_v2_测试迁移_C5a.csv`（含每条老断言原文 + 接手的 v2 nodeid）。
剩下的 28 条是**不起窗**的数据层 / .sv / 报告断言，原样保留。
"""

import json
import os
import sys

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")   # GUI 测试用离屏后端
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dreg_verify import excel_model, generator     # noqa: E402
from dreg_verify import expr as E                   # noqa: E402
from dreg_verify import vectors as V                # noqa: E402
from dreg_verify import resolver as R               # noqa: E402
from dreg_verify import sv_writer as W              # noqa: E402
import fixtures                                      # noqa: E402

import re                                             # noqa: E402


def _decoded_html(html):
    """窗口化报告把各 tab 的真值表/行内容存进 <script type=application/json> blob（懒渲染）。
    解码这些 blob 的 HTML 串并拼回静态壳，得到『前端真正会渲染出来的 HTML』，供子串断言。"""
    parts = [html]
    for m in re.finditer(r'<script type="application/json" id="[^"]+">(.*?)</script>', html, re.S):
        for it in json.loads(m.group(1)):
            parts.append(it.get("h", ""))
    return "".join(parts)


@pytest.fixture(scope="module")
def wb(tmp_path_factory):
    path = tmp_path_factory.mktemp("xl_de") / "synthetic_dreg.xlsx"
    fixtures.build_workbook(str(path))
    return excel_model.load_workbook(str(path))


def _reserve(wb):
    sig = next(s for s in wb.logic if s.out_name == "d_logic_bt_lp_reserve")
    node = E.parse(sig.expr)               # (A?C:B)&(~J)
    bindings = R.Resolver(wb).resolve_signal_inputs(sig)
    groups = V.input_groups(node, bindings)
    return sig, node, bindings, groups


# A=1(选C),C=1,B=0,J=0 → auto_out = 1
BV_ONE = {"d_bt_lp_linelocal_mode_ctrl": 1, "d_bt_lp_bt_mode_sel_local": 1,
          "d_bt_lp_bt_mode_sel": 0, "d_bt_lp_iddq": 0}


# ───────────── 数据层：TestVector / make_vector_from_base_values ─────────────
def test_vector_asserted_value_priority():
    """断言对比值优先级：负向错值 > designer 手填期望 > auto_out 兜底。"""
    # 未填 → auto_out 兜底
    v = V.TestVector(0, {"A": 1}, exp_value=1, exp_width=1)
    assert v.asserted_value == 1 and not v.designer_filled
    # 手填 → 用手填值（即使与 auto_out 不同也不是负向）
    v2 = V.TestVector(0, {"A": 1}, exp_value=1, exp_width=1, designer_expected=0)
    assert v2.asserted_value == 0 and v2.designer_filled and not v2.is_negative
    # 负向优先级最高（负向不算 designer 手填）
    v3 = V.TestVector(0, {"A": 1}, exp_value=1, exp_width=1,
                      is_negative=True, neg_value=0, designer_expected=1)
    assert v3.asserted_value == 0 and not v3.designer_filled


def test_make_vector_designer_expected_not_negative(wb):
    """⭐核心语义：designer 手填期望 != auto_out 不标负向——那可能正是表达式的 bug。"""
    sig, node, bindings, groups = _reserve(wb)
    # auto_out = 1，designer 认为应该是 0
    vec = V.make_vector_from_base_values(node, bindings, groups, BV_ONE, sig.out_width,
                                         designer_expected=0)
    assert not vec.is_negative                  # 不是负向！
    assert vec.exp_value == 1                   # auto_out 仍是表达式算的 1
    assert vec.designer_expected == 0
    assert vec.asserted_value == 0              # .sv 断言用 designer 的 0
    assert vec.designer_filled


def test_make_vector_designer_expected_same_as_auto(wb):
    """手填值与 auto_out 相同：仍算 designer 手填（已人工核对，区别于兜底）。"""
    sig, node, bindings, groups = _reserve(wb)
    vec = V.make_vector_from_base_values(node, bindings, groups, BV_ONE, sig.out_width,
                                         designer_expected=1)
    assert not vec.is_negative and vec.designer_filled
    assert vec.asserted_value == vec.exp_value == 1


def test_make_vector_designer_expected_masked_to_width(wb):
    """手填期望按输出位宽裁剪（1bit 信号填 0xFF → 1）。"""
    sig, node, bindings, groups = _reserve(wb)
    vec = V.make_vector_from_base_values(node, bindings, groups, BV_ONE, sig.out_width,
                                         designer_expected=0xFF)
    assert vec.designer_expected == 1 and vec.asserted_value == 1


def test_make_vector_unfilled_falls_back_to_auto(wb):
    """期望未填（designer_expected=None）→ asserted_value = auto_out（向后兼容）。"""
    sig, node, bindings, groups = _reserve(wb)
    vec = V.make_vector_from_base_values(node, bindings, groups, BV_ONE, sig.out_width)
    assert vec.designer_expected is None and not vec.designer_filled
    assert vec.asserted_value == vec.exp_value == 1


def test_negative_override_still_works(wb):
    """负向(expected_override)机制不受影响：仍标负向、断言用错值。"""
    sig, node, bindings, groups = _reserve(wb)
    neg = V.make_vector_from_base_values(node, bindings, groups, BV_ONE, sig.out_width,
                                         expected_override=0)
    assert neg.is_negative and neg.asserted_value == 0 and not neg.designer_filled


def test_make_negative_avoids_designer_expected():
    """负向错值防撞：自动负向的错值要避开 designer 手填期望（否则反例在"designer 是对的"
    时会意外 PASS = NEG-BROKEN）。多位信号必避开；1bit 两者不同时无解(保持现状)。"""
    # 4bit：auto=5，designer 手填 10(=~5)——取反值恰好撞上手填期望 → 必须翻位避开
    v = V.TestVector(0, {"A": 1}, exp_value=5, exp_width=4, designer_expected=10)
    neg = V.make_negative(v, mode="invert")
    assert neg.neg_value != 5 and neg.neg_value != 10        # 与 auto_out、手填期望都不同
    # 无 designer 期望时行为不变：错值 = 取反
    v2 = V.TestVector(0, {"A": 1}, exp_value=5, exp_width=4)
    assert V.make_negative(v2, mode="invert").neg_value == 10
    # 1bit 且 designer != auto：无解(任何值都撞)，保持取反值（仿真时 NEG-BROKEN 标签可见）
    v3 = V.TestVector(0, {"A": 1}, exp_value=1, exp_width=1, designer_expected=0)
    assert V.make_negative(v3, mode="invert").neg_value == 0


# ───────────── .sv 层：断言用期望 + 统计 ─────────────
def test_sv_assert_uses_designer_expected(wb):
    """.sv 断言对比值 = designer 手填期望（即使与 auto_out 不同）。"""
    sig, node, bindings, groups = _reserve(wb)
    # auto_out=1 但 designer 填 0 → 断言应是 ==1'b0
    vec = V.make_vector_from_base_values(node, bindings, groups, BV_ONE, sig.out_width,
                                         designer_expected=0)
    res = generator.build(wb, generator.GenOptions(
        signals=["d_logic_bt_lp_reserve"], top_output_only=False,
        vector_overrides={"d_logic_bt_lp_reserve": [vec]}))
    text = "\n".join(res["blocks"][0][0])
    assert "==1'b0)begin" in text               # 用 designer 的 0，不是 auto 的 1
    assert "==1'b1)begin" not in text
    # 不是负向：无 _NEG 后缀、无 NEG 标签
    assert "_NEG" not in text and "NEG-EXPECTED-FAIL" not in text


def test_sv_stats_count_designer_filled(wb):
    """render_signal_block stats / build summary 统计 designer 手填条数。"""
    sig, node, bindings, groups = _reserve(wb)
    v_filled = V.make_vector_from_base_values(node, bindings, groups, BV_ONE, sig.out_width,
                                              index=0, designer_expected=1)
    v_unfilled = V.make_vector_from_base_values(node, bindings, groups, BV_ONE, sig.out_width,
                                                index=1)
    v_neg = V.make_vector_from_base_values(node, bindings, groups, BV_ONE, sig.out_width,
                                           index=2, expected_override=0)
    res = generator.build(wb, generator.GenOptions(
        signals=["d_logic_bt_lp_reserve"], top_output_only=False,
        vector_overrides={"d_logic_bt_lp_reserve": [v_filled, v_unfilled, v_neg]}))
    st = res["blocks"][0][1]
    assert st["n_vectors"] == 3 and st["n_negative"] == 1
    assert st["n_designer"] == 1                       # 只有 v_filled 算手填
    assert res["summary"]["n_designer"] == 1


def test_sv_comments_annotate_designer_filled(wb):
    """comments 模式：手填期望的断言上方加注释；与 auto_out 不同的额外提示"表达式可能与意图不符"。"""
    sig, node, bindings, groups = _reserve(wb)
    v_same = V.make_vector_from_base_values(node, bindings, groups, BV_ONE, sig.out_width,
                                            index=0, designer_expected=1)   # == auto
    v_diff = V.make_vector_from_base_values(node, bindings, groups, BV_ONE, sig.out_width,
                                            index=1, designer_expected=0)   # != auto
    lines, _st = W.render_signal_block(sig, bindings, [v_same, v_diff],
                                       {"truncated": False}, comments=True, node=node)
    text = "\n".join(lines)
    assert "designer-filled expected (matches expression value)" in text
    assert "does not match designer intent" in text


# ───────────── 报告层：report() 拆分 auto_out / 期望 ─────────────
def test_report_tables_have_auto_out_and_designer_flag(wb):
    """report() 真值表每测试带 auto_out / expected / designer_filled / auto_num。"""
    sig, node, bindings, groups = _reserve(wb)
    vec = V.make_vector_from_base_values(node, bindings, groups, BV_ONE, sig.out_width,
                                         designer_expected=0)    # != auto_out(1)
    rep = generator.report(wb, generator.GenOptions(
        signals=["d_logic_bt_lp_reserve"], top_output_only=False,
        vector_overrides={"d_logic_bt_lp_reserve": [vec]}))
    t = next(x for x in rep["tables"] if x["signal"] == "d_logic_bt_lp_reserve")
    tc = t["tests"][0]
    assert tc["auto_out"] == "1" and tc["expected"] == "0"
    assert tc["designer_filled"] is True and tc["neg"] is False
    assert tc["auto_num"] == 1 and tc["width"] == 1
    assert t["auto_label"].startswith("auto_out")
    # 明细行：auto_out 列 + 期望来源列
    d = next(r for r in rep["detail"] if r["signal"] == "d_logic_bt_lp_reserve")
    assert d["auto_out"] == "1'b1" and d["expected"] == "1'b0"
    assert d["exp_src"] == "designer手填"


def test_report_unfilled_marks_fallback(wb):
    """未填期望的测试在报告里标 auto_out兜底。"""
    rep = generator.report(wb, generator.GenOptions(
        signals=["d_logic_bt_lp_reserve"], top_output_only=False))
    t = next(x for x in rep["tables"] if x["signal"] == "d_logic_bt_lp_reserve")
    assert all(not tc["designer_filled"] for tc in t["tests"])
    assert all(tc["expected"] == tc["auto_out"] for tc in t["tests"])   # 兜底=auto
    d = [r for r in rep["detail"] if r["signal"] == "d_logic_bt_lp_reserve"]
    assert all(r["exp_src"] == "auto_out兜底" for r in d)


def test_report_negative_exp_src(wb):
    """负向测试的期望来源 = 负向(故意填错)。"""
    rep = generator.report(wb, generator.GenOptions(
        signals=["d_logic_bt_lp_reserve"], top_output_only=False,
        neg_all=True, neg_which="first"))
    rows = [r for r in rep["detail"] if r["signal"] == "d_logic_bt_lp_reserve"]
    neg_rows = [r for r in rows if r["neg"] == "是"]
    assert neg_rows and all(r["exp_src"] == "负向(故意填错)" for r in neg_rows)


# ───────────── HTML 报告：两行 + 真值表检查 tab ─────────────
def test_html_report_has_check_tab(wb, tmp_path):
    """HTML 报告含「真值表检查」tab：全局按钮 / 可遮盖 auto_out / 可填空期望 / 判定 JS。"""
    from dreg_verify import cli
    rep = generator.report(wb, generator.GenOptions(top_output_only=False))
    path = tmp_path / "r.html"
    cli.write_report(str(path), rep, "synthetic.xlsx")
    raw = path.read_text(encoding="utf-8")
    html = _decoded_html(raw)        # 真值表内容现走 JSON blob 懒渲染，解码后断言实际渲染结构
    # 五个 tab + 共用一份真值表数据(②③ 不再各存一份 DOM)
    assert 'data-tab="chk"' in raw and "真值表检查" in raw and 'id="chk"' in raw
    assert 'id="chk-body"' in raw and 'id="tt-data"' in raw and 'id="chk-data"' not in raw
    # 全局按钮 + 计分 + 说明
    assert 'id="chkbtn"' in raw and 'id="chkscore"' in raw
    # 检查表格结构：auto_out 格(cauto, 带数值 data-v) + 期望填空格(cquiz, 含 input)
    assert 'class="cauto"' in html
    assert "cquiz" in html and 'class="cin"' in html and "data-v=" in html
    # JS：数值解析 + 判定逻辑
    assert "parseVal" in raw and "okc" in raw and "badc" in raw


def test_html_check_tab_mask_and_reanswer(wb, tmp_path):
    """⭐用户反馈回归(2026-06-03)：①遮盖必须 JS 换文本(CSS transparent 会被 tr.autorow
    颜色规则按优先级盖回去，原值看得见)；②答完必须允许改值重判(不锁定)。"""
    from dreg_verify import cli
    rep = generator.report(wb, generator.GenOptions(top_output_only=False))
    path = tmp_path / "r.html"
    cli.write_report(str(path), rep, "synthetic.xlsx")
    html = path.read_text(encoding="utf-8")
    # ① 遮盖 = JS 换文本（原值存 data-t、显示 ?），不再用 CSS 透明
    assert "function maskCell" in html and "function unmaskCell" in html
    assert "data-t" in html
    assert "color:transparent" not in html             # 根因：被 autorow 颜色规则盖掉
    # ② 允许重答：无 readOnly 锁定/data-done 单次限制；重答时计分修正
    assert "readOnly=true" not in html and "data-done" not in html
    assert "okn--" in html and "badn--" in html        # 重答把旧结果从计数挪走
    # 判定时先清旧色再上新色（重答列颜色能翻转）
    assert "classList.remove('okc','badc')" in html


def test_html_report_truth_table_two_rows(wb, tmp_path):
    """HTML 真值表 tab：auto_out 与 期望 拆成两行（autorow + exprow）。"""
    from dreg_verify import cli
    sig, node, bindings, groups = _reserve(wb)
    v1 = V.make_vector_from_base_values(node, bindings, groups, BV_ONE, sig.out_width,
                                        index=0, designer_expected=1)   # 手填且==auto
    v2 = V.make_vector_from_base_values(node, bindings, groups, BV_ONE, sig.out_width,
                                        index=1, designer_expected=0)   # 手填但!=auto
    v3 = V.make_vector_from_base_values(node, bindings, groups, BV_ONE, sig.out_width,
                                        index=2)                        # 未填(兜底)
    rep = generator.report(wb, generator.GenOptions(
        signals=["d_logic_bt_lp_reserve"], top_output_only=False,
        vector_overrides={"d_logic_bt_lp_reserve": [v1, v2, v3]}))
    path = tmp_path / "r.html"
    cli.write_report(str(path), rep, "synthetic.xlsx")
    html = _decoded_html(path.read_text(encoding="utf-8"))   # 解码 JSON blob 后断言渲染结构
    assert 'class="autorow"' in html               # auto_out 行
    assert "auto_out" in html
    # ②③ 统一用检查态标记(cquiz)，故颜色类与 cquiz 同格：cquiz dsgn / dsgndiff / cquiz fb
    assert 'class="cquiz dsgn"' in html                    # 手填且一致(绿)
    assert "dsgndiff" in html                              # 手填但不一致(红)
    assert 'class="cquiz fb"' in html                      # 未填兜底(灰)


def test_html_report_windowed_lazy_render(wb, tmp_path):
    """⭐窗口化懒渲染回归（2026-06-04）：大报告(几千组)在浏览器卡死/闪退的根因是整份内容
    一次性进 DOM + ②③ 各存一份。现在：①各 tab 内容存进 JSON blob，DOM 里只留空挂载点；
    ②②③共用一份真值表数据(无 chk-data)；③重内容(ttblock)不出现在静态壳里(不预渲)。"""
    from dreg_verify import cli
    rep = generator.report(wb, generator.GenOptions(top_output_only=False))
    path = tmp_path / "r.html"
    cli.write_report(str(path), rep, "synthetic.xlsx")
    raw = path.read_text(encoding="utf-8")

    # 挂载点都在且为空（内容靠 JS 注入），分页条占位都在
    for key in ("sum", "tt", "chk", "det", "ver"):
        assert ('id="%s-body"' % key) in raw and ('id="%s-nav"' % key) in raw
    assert 'id="tt-body"></div>' in raw                    # tt 挂载点是空的
    assert 'id="sum-body"></tbody>' in raw                 # sum 挂载点是空的

    # 各数据 blob 可被 JSON 解析，条数与 rep 对应；②③ 共用 tt-data（没有 chk-data）
    def blob(idv):
        m = re.search(r'<script type="application/json" id="%s">(.*?)</script>' % idv, raw, re.S)
        assert m, "缺少数据 blob: " + idv
        return json.loads(m.group(1))
    assert 'id="chk-data"' not in raw
    assert len(blob("tt-data")) == len(rep["tables"])
    assert len(blob("sum-data")) == len(rep["summary"])
    assert len(blob("det-data")) == len(rep["detail"])
    for it in blob("tt-data"):                             # 每个 item 带过滤元数据
        assert set(("h", "t", "o", "n")) <= set(it.keys())

    # 关键：重内容(真值表 ttblock)只在 JSON blob 里，不在静态 DOM 壳里(否则又会一次性进 DOM)。
    # 用渲染出的元素形式断言（裸词 ttblock/autorow 还会出现在 CSS 选择器里，不算预渲）。
    shell = re.sub(r'<script type="application/json"[^>]*>.*?</script>', "", raw, flags=re.S)
    assert '<div class="ttblock">' not in shell
    assert 'class="autorow"' not in shell


def test_html_report_initial_render(wb, tmp_path):
    """⭐bug回归(2026-06-05)：窗口化后首屏 active=①汇总 tab 没主动渲染 → 一片空白，
    要切一下 tab 才出来。修复：IIFE 末尾 boot() 首屏渲染 active tab + 设置匹配计数。"""
    from dreg_verify import cli
    rep = generator.report(wb, generator.GenOptions(top_output_only=False))
    path = tmp_path / "r.html"
    cli.write_report(str(path), rep, "synthetic.xlsx")
    raw = path.read_text(encoding="utf-8")
    assert "function boot()" in raw          # 首屏初始化函数存在
    assert re.search(r"\bboot\(\);", raw)     # 顶层有调用（否则首屏不渲染）
    assert "render(active)" in raw            # boot 体里渲染当前(首屏=汇总)tab


def test_html_check_toggle_perf(wb, tmp_path):
    """⭐性能回归(2026-06-04)：点「开始/结束检查」整页一次性显示几百个 <input> 会触发一次
    巨大同步重排、主线程冻结(鼠标卡)；几百个 sticky 真值表表头又让滚动持续卡。优化：
    ①applyChk 分帧调度(requestAnimationFrame 分批、帧间让步)；②真值表表头取消 sticky。"""
    from dreg_verify import cli
    rep = generator.report(wb, generator.GenOptions(top_output_only=False))
    path = tmp_path / "r.html"
    cli.write_report(str(path), rep, "synthetic.xlsx")
    raw = path.read_text(encoding="utf-8")
    # ① 检查态切换分帧：用 requestAnimationFrame 分批，主线程让步
    assert "requestAnimationFrame" in raw
    assert "function step()" in raw and "chkJob" in raw
    # ② 真值表每块表头不 sticky（扁平表仍保留 thead sticky）
    assert ".tt thead th{position:static" in raw
    assert "thead th{position:sticky" in raw       # 扁平表的 sticky 仍在
    # ③ 屏外真值表块跳过布局：窗口缩放/滚动只算可见块（2026-06-05 缩放卡顿优化）
    assert "content-visibility:auto" in raw and "contain-intrinsic-size" in raw


def test_html_report_no_owner_filter(wb, tmp_path):
    """owner 列留空 → 报告 owner 多选浮层给「（无 owner）×N」复选项（哨兵 __no_owner__），仅在真有空 owner 时出现。"""
    from dreg_verify import cli
    rep = generator.report(wb, generator.GenOptions(top_output_only=False))
    for r in rep["summary"][:2]:                    # 模拟两个信号 owner 列留空
        r["owner"] = ""
    path = tmp_path / "r.html"
    cli.write_report(str(path), rep, "synthetic.xlsx")
    raw = path.read_text(encoding="utf-8")
    n_blank = sum(1 for r in rep["summary"] if not r.get("owner"))
    assert ('<label><input type="checkbox" class="ownerck" value="__no_owner__">（无 owner） ×%d</label>'
            % n_blank) in raw
    assert 'id="ownerbox"' in raw and 'class="ownerck"' in raw      # 多选浮层就位


def test_html_report_no_owner_filter_absent_when_all_owned(wb, tmp_path):
    """所有信号都有 owner 时，浮层不出现「（无 owner）」复选项（不打扰）。"""
    from dreg_verify import cli
    rep = generator.report(wb, generator.GenOptions(top_output_only=False))
    for r in rep["summary"]:
        r["owner"] = r.get("owner") or "someone"
    path = tmp_path / "r.html"
    cli.write_report(str(path), rep, "synthetic.xlsx")
    raw = path.read_text(encoding="utf-8")
    assert 'value="__no_owner__"' not in raw


def test_html_report_owner_multi_select_markup(wb, tmp_path):
    """owner 多选：每个 owner 一个复选项 + 多选浮层骨架(按钮/清除/OR 过滤 JS)就位，
    且旧的单选 <select id="owner"> 已彻底移除。"""
    from dreg_verify import cli
    rep = generator.report(wb, generator.GenOptions(top_output_only=False))
    rep["summary"][0]["owner"] = "Amy"
    rep["summary"][1]["owner"] = "Bob"
    path = tmp_path / "r.html"
    cli.write_report(str(path), rep, "synthetic.xlsx")
    raw = path.read_text(encoding="utf-8")
    assert '<select id="owner">' not in raw                 # 单选已移除
    assert 'id="ownerbox"' in raw and 'id="ownerbtn"' in raw and 'id="ownerclr"' in raw
    assert '<input type="checkbox" class="ownerck" value="Amy">Amy</label>' in raw
    assert '<input type="checkbox" class="ownerck" value="Bob">Bob</label>' in raw
    assert "ownerSel" in raw and "readOwnerSel" in raw       # 多选 OR 过滤逻辑就位


def test_csv_report_detail_has_auto_out_column(wb, tmp_path):
    """CSV 明细含 auto_out 与期望来源列。"""
    from dreg_verify import cli
    rep = generator.report(wb, generator.GenOptions(top_output_only=False))
    path = tmp_path / "r.csv"
    cli.write_report(str(path), rep, "synthetic.xlsx")
    content = path.read_text(encoding="utf-8-sig")
    assert "auto_out(表达式计算)" in content and "期望来源" in content
    assert "auto_out兜底" in content


# ═════════════════ mux 信号的 designer 手填期望（第十一轮续，用户反馈#1） ═════════════════
@pytest.fixture(scope="module")
def wb_mux(tmp_path_factory):
    path = tmp_path_factory.mktemp("xl_demux") / "synthetic_mux.xlsx"
    fixtures.build_workbook(str(path), with_mux=True)
    return excel_model.load_workbook(str(path))


def test_mux_assign_key_stable():
    """mux 取值键：与字典插入顺序无关、值变即键变。"""
    k1 = generator.mux_assign_key({"c:A": 1, "d:0": 5, "c:B": 2})
    k2 = generator.mux_assign_key({"d:0": 5, "c:B": 2, "c:A": 1})   # 乱序同值
    k3 = generator.mux_assign_key({"c:A": 1, "d:0": 6, "c:B": 2})   # 值不同
    assert k1 == k2 and k1 != k3


def test_apply_mux_expected():
    """apply_mux_expected：按键对号入座、负向不碰、对不上的键丢弃。"""
    v_pos = V.TestVector(0, {"c:A": 1, "d:0": 5}, exp_value=5, exp_width=4)
    v_neg = V.TestVector(1, {"c:A": 1, "d:0": 5}, exp_value=5, exp_width=4,
                         is_negative=True, neg_value=10)
    key = generator.mux_assign_key(v_pos.assignments)
    generator.apply_mux_expected([v_pos, v_neg],
                                 {key: 7, "no:such=key": 9})
    assert v_pos.designer_expected == 7 and v_pos.asserted_value == 7
    assert v_neg.designer_expected is None                  # 负向不碰
    # 空 map / None 安全
    generator.apply_mux_expected([v_pos], None)
    generator.apply_mux_expected([v_pos], {})


def test_mux_expected_flows_to_sv(wb_mux):
    """⭐GenOptions.mux_expected → build：mux 断言用 designer 手填期望（≠auto_out 也不算负向）。"""
    grp = wb_mux.mux[0]
    resolver = R.Resolver(wb_mux)
    from dreg_verify import mux_gen
    exp = mux_gen.expand_mux_group(wb_mux, resolver, grp)
    vecs, _meta = mux_gen.make_mux_vectors(grp, exp, mode="min")
    v0 = vecs[0]
    wrong = (v0.exp_value + 1) & E.mask(v0.exp_width)
    key = generator.mux_assign_key(v0.assignments)
    res = generator.build(wb_mux, generator.GenOptions(
        signals=[grp.out_name], top_output_only=False,
        mux_expected={grp.out_name: {key: wrong}}))
    blk = next((lines, st) for lines, st in res["blocks"] if st.get("is_mux"))
    text = "\n".join(blk[0])
    assert ("==%s)begin" % W.fmt_bin(wrong, v0.exp_width)) in text   # 用手填值
    assert blk[1]["n_designer"] == 1
    assert "T0_NEG" not in text                                       # 不是负向


def test_mux_expected_in_report(wb_mux):
    """report() 的 mux 真值表反映手填期望（与 build 双轨同步）。"""
    grp = wb_mux.mux[0]
    resolver = R.Resolver(wb_mux)
    from dreg_verify import mux_gen
    exp = mux_gen.expand_mux_group(wb_mux, resolver, grp)
    vecs, _meta = mux_gen.make_mux_vectors(grp, exp, mode="min")
    v0 = vecs[0]
    wrong = (v0.exp_value + 1) & E.mask(v0.exp_width)
    rep = generator.report(wb_mux, generator.GenOptions(
        signals=[grp.out_name], top_output_only=False,
        mux_expected={grp.out_name: {generator.mux_assign_key(v0.assignments): wrong}}))
    t = next(x for x in rep["tables"] if x.get("kind") == "mux")
    tc0 = t["tests"][0]
    assert tc0["designer_filled"] is True
    assert tc0["expected"] != tc0["auto_out"]
    d0 = next(r for r in rep["detail"] if r["type"] == "mux")
    assert d0["exp_src"] == "designer手填"


def test_mux_expected_stale_key_dropped(wb_mux):
    """键对不上(如覆盖度切换后向量变了) → 期望丢弃(兜底 auto_out)，绝不张冠李戴。"""
    grp = wb_mux.mux[0]
    res = generator.build(wb_mux, generator.GenOptions(
        signals=[grp.out_name], top_output_only=False,
        mux_expected={grp.out_name: {"c:A=999;d:0=999": 5}}))    # 不存在的键
    blk = next((lines, st) for lines, st in res["blocks"] if st.get("is_mux"))
    assert blk[1]["n_designer"] == 0                              # 没有任何测试被它影响


# ═════════════════ 真值表列宽可拖动（用户反馈#2） ═════════════════
