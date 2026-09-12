# -*- coding: utf-8 -*-
"""exports.py（导出编排层）单元测试 —— **不起 Qt**。

盯三件事：
  ① Qt-free：import dreg_verify.exports 不能把 PySide6 拖进来（GUI 之外也能用/能测）。
  ② 跳过必须可见：每种交付物的 outcome 都要点名「哪些信号、为什么」，明细里跳过项在计数之前。
  ③ 契约：claims.json 与 CLI `--export-claims` 逐字节同构；单信号 CSV 带
     期望(bin)/force/RF_WRITE 三行（此前只有『排查(旧)』有、SignalView 缺）。
"""

import json
import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import make_mirror_btlp                              # 仓库根夹具脚本（conftest 已加 path）
from dreg_verify import cli                          # noqa: E402
from dreg_verify import excel_model as M             # noqa: E402
from dreg_verify import exports as X                 # noqa: E402
from dreg_verify import topout as T                  # noqa: E402
from dreg_verify import vectors as V                 # noqa: E402


@pytest.fixture(scope="module")
def mirror(tmp_path_factory):
    p = tmp_path_factory.mktemp("exports_mirror") / "mirror_btlp_dreg.xlsx"
    make_mirror_btlp.build(str(p))
    return str(p)


@pytest.fixture(scope="module")
def mirror_wb(mirror):
    return M.load_workbook(mirror)


class _FakeTopoutProvider:
    """gui._TopoutProvider 的最小替身（只要 render_sv/view_id）——本文件不许起 Qt。"""

    view_id = "topout"

    def __init__(self, wb):
        self.wb = wb

    def render_sv(self, only, mode, max_tests, exhaustive, edited, comments=False,
                  sv_summary=False, owner_in_msg=False, scope="all", sig_cov=None, form_cov=None):
        return T.render_topout_sv(self.wb, mode=mode, max_tests=max_tests, exhaustive=exhaustive,
                                  comments=comments, sv_summary=sv_summary,
                                  owner_in_msg=owner_in_msg, only=only, scope=scope)


# ───────────── ① Qt-free ─────────────
def test_exports_does_not_import_pyside6():
    """新模块不 import PySide6：对话框/弹框留在 gui，编排层必须能在无 GUI 环境跑。"""
    code = ("import sys; import dreg_verify.exports; "
            "sys.exit(1 if 'PySide6' in sys.modules else 0)")
    assert subprocess.call([sys.executable, "-c", code]) == 0


# ───────────── ② 导出选项：一套默认值 + 一套键名 ─────────────
def test_export_options_single_source_of_defaults():
    """两个导出对话框共用同一批 settings 键，默认值统一以 SignalView 版为准（全 False）。"""
    assert X.EXPORT_OPTION_DEFAULTS == {"scope": "all", "comments": False,
                                        "sv_summary": False, "owner_in_msg": False}
    assert X.load_export_options(None) == X.EXPORT_OPTION_DEFAULTS
    assert X.load_export_options({}) == X.EXPORT_OPTION_DEFAULTS


def test_export_options_roundtrip_uses_shared_keys():
    st = {}
    opt = {"scope": "neg", "comments": True, "sv_summary": True, "owner_in_msg": False}
    X.store_export_options(st, opt)
    assert st == {"export_scope": "neg", "export_comments": True,
                  "export_sv_summary": True, "export_owner_in_msg": False}
    assert X.load_export_options(st) == opt


def test_export_options_bad_scope_falls_back():
    assert X.load_export_options({"export_scope": "垃圾"})["scope"] == "all"


# ───────────── ③ ExportOutcome：跳过项在前、名字 + 原因 ─────────────
def _outcome():
    return X.ExportOutcome(kind="sv", path="x.sv",
                           counts={"n_signals": 3, "n_vectors": 7},
                           skipped=[("d_sig_a", "输入缺探针前缀"), ("d_sig_b", "RO 回读")],
                           warnings=["兜底有自证嫌疑"])


def test_outcome_summary_is_one_line_and_names_skipped_count():
    s = _outcome().summary_text()
    assert "\n" not in s
    assert "x.sv" in s and "信号 3" in s and "跳过 2" in s


def test_outcome_detail_puts_skipped_before_counts():
    d = _outcome().detail_text()
    assert d.index("d_sig_a") < d.index("计数：")
    assert "d_sig_b" in d and "RO 回读" in d          # 名字 + 原因都在
    assert "兜底有自证嫌疑" in d


def test_outcome_detail_limit_elides_but_says_total():
    o = X.ExportOutcome(kind="sv", path="x.sv",
                        skipped=[("s%d" % i, "原因") for i in range(20)])
    d = o.detail_text(limit=3)
    assert "s0" in d and "s19" not in d
    assert "等共 20 个" in d


def test_skip_reason_prefixes_status_label():
    r = X.skip_reason("needs-prefix", "d_x 埋在子模块")
    assert "探针前缀" in r and "d_x 埋在子模块" in r
    assert X.skip_reason("cleared") == X.SKIP_STATUS_LABEL["cleared"]


def test_build_skipped_reads_both_build_shapes():
    """provider 的 accounted 与 generator.build 的 skipped/errors 都要翻成 (名字, 原因)。"""
    prov = {"accounted": [{"name": "d_ro", "kind": "register", "status": "skip", "reason": "RO 回读"}]}
    assert X.build_skipped(prov) == [("d_ro", X.skip_reason("skip", "RO 回读"))]
    legacy = {"skipped": [("d_need", "12", [("A", "d_leaf", "wire 兜底")])],
              "errors": [("d_bad", "13", "表达式解析失败")]}
    got = dict(X.build_skipped(legacy))
    assert "d_leaf" in got["d_need"] and "wire 兜底" in got["d_need"]
    assert "表达式解析失败" in got["d_bad"]


def test_dup_label_text_lists_pairs_and_total():
    dups = [("R%d" % i, "a%d" % i, "b%d" % i) for i in range(20)]
    t = X.dup_label_text(dups)
    assert "R0  ←  a0 / b0" in t and "共 20 处" in t


def test_skipped_detail_text_moved_but_wording_kept():
    """老 gui._skipped_detail_text 搬进来：措辞不变（GUI 里仍按它给『怎么强制生成』的下一步）。"""
    t = X.skipped_detail_text([("d_x", "1", [("A", "d_leaf", "wire 兜底")])])
    assert "d_x" in t and "d_leaf" in t and "缺前缀强制生成" in t
    from dreg_verify import gui  # noqa: F401 —— gui 侧别名仍在（老调用点不破）
    assert gui._skipped_detail_text is X.skipped_detail_text


# ───────────── ④ .sv：outcome 计数 + 记账点名 ─────────────
def test_sv_outcome_from_topout_build_names_accounted(mirror_wb):
    _text, b = T.render_topout_sv(mirror_wb, mode="min", max_tests=64)
    out = X.sv_outcome("t.sv", b)
    assert out.kind == "sv"
    assert out.counts["n_signals"] == b["summary"]["n_total"]
    assert out.counts["n_blocks"] == b["summary"]["n_emitted"]
    assert out.counts["n_vectors"] == b["summary"]["n_vectors"]
    # mirror 里有 RO 回读信号被记账 → 必须点名（不是只报个数）
    assert out.skipped and len(out.skipped) == len(b["accounted"])
    names = [n for n, _r in out.skipped]
    assert all(n for n in names)
    assert "记账" in out.detail_text() or names[0] in out.detail_text()


def test_sv_outcome_scope_neg_has_no_fallback_count(mirror_wb):
    _text, b = T.render_topout_sv(mirror_wb, mode="min", max_tests=64)
    assert "n_fallback" not in X.sv_outcome("t.sv", b, scope="neg").counts
    assert "n_fallback" in X.sv_outcome("t.sv", b, scope="all").counts


def test_export_sv_writes_file_and_outcome(mirror_wb, tmp_path):
    prov = _FakeTopoutProvider(mirror_wb)
    out_path = tmp_path / "o.sv"
    text, out = X.export_sv(prov, str(out_path), mode="min", max_tests=64,
                            options={"scope": "all", "comments": False,
                                     "sv_summary": False, "owner_in_msg": False})
    assert out_path.read_text(encoding="utf-8") == text
    assert out.counts["n_vectors"] > 0


class _FakePageProvider:
    """页本地 provider 的最小替身——它的 render_sv **认得** block_suffix（走 pageviews）。"""

    def __init__(self, wb, page="logic"):
        self.wb = wb
        self.page = page
        self.view_id = page

    def render_sv(self, only, mode, max_tests, exhaustive, edited, comments=False,
                  sv_summary=False, owner_in_msg=False, scope="all", sig_cov=None, form_cov=None,
                  block_suffix=""):
        from dreg_verify import pageviews as P
        return P.build_page_sv(self.wb, self.page, mode=mode, max_tests=max_tests,
                               exhaustive=exhaustive, comments=comments, sv_summary=sv_summary,
                               owner_in_msg=owner_in_msg, only=only, scope=scope,
                               block_suffix=block_suffix)


@pytest.mark.contract("C-169", "C-170")
def test_c169_c170_export_sv_split_two_files(mirror_wb, tmp_path):
    """N2（冲突④）：.sv『正向 + 反例分文件』= 两次独立 render，两个 outcome。

    C-169 路径 `<stem>_pos.sv` / `<stem>_neg.sv`；C-170 汇总命名块各带 `_pos`/`_neg` 后缀
    （两份贴进同一个 testcase 时同名 begin/end 块是非法 SV）。"""
    from dreg_verify import sv_writer as W

    opts = {"comments": False, "sv_summary": True, "owner_in_msg": False, "scope": "垃圾"}
    base = tmp_path / "mirror.sv"
    assert X.split_sv_paths(str(base)) == (str(tmp_path / "mirror_pos.sv"),
                                           str(tmp_path / "mirror_neg.sv"))
    assert X.split_sv_paths(str(tmp_path / "无扩展名")) == \
        (str(tmp_path / "无扩展名_pos.sv"), str(tmp_path / "无扩展名_neg.sv"))

    prov = _FakeTopoutProvider(mirror_wb)
    pos, neg = X.export_sv_split(prov, str(base), mode="min", max_tests=64, options=opts)
    assert [o.path for o in (pos, neg)] == list(X.split_sv_paths(str(base)))
    assert all(os.path.exists(o.path) for o in (pos, neg))
    assert pos.kind == neg.kind == "sv"
    # 两次独立 render：各自的统计只说自己那份文件的事（scope 由分文件定死，options 里的被忽略）
    assert any("仅正向" in w for w in pos.warnings)
    assert any("仅负向" in w for w in neg.warnings)
    assert "n_fallback" in pos.counts and "n_fallback" not in neg.counts
    pos_text = open(pos.path, encoding="utf-8").read()
    neg_text = open(neg.path, encoding="utf-8").read()
    assert pos_text != neg_text

    # C-170 汇总块后缀：provider（→引擎）接得住 block_suffix 时必须真的加上。
    # 旧门面的 provider 没这个形参（C5 随 legacy 退役）——那种情况下只断言「没炸、没乱加」。
    if X._accepts_kw(prov.render_sv, "block_suffix"):
        assert (W.SUMMARY_BLOCK + "_pos") in pos_text
        assert (W.SUMMARY_BLOCK + "_neg") in neg_text
    else:
        assert (W.SUMMARY_BLOCK + "_pos") not in pos_text          # 接不住就别偷偷改名
        assert W.SUMMARY_BLOCK in pos_text

    # 认得 block_suffix 的 provider（页本地）：两份产物的命名块必须不同名
    # （⚠ 页本地『仅负向』产物的**内容**另有一处既有缺陷，见 pageviews.build_page_sv 的 scope 分支——
    #   已回报主控，不在本次 additive 范围内；这里只断言命名块，不给那个行为背书。）
    page_base = tmp_path / "page.sv"
    ppos, pneg = X.export_sv_split(_FakePageProvider(mirror_wb), str(page_base),
                                   mode="max", exhaustive=True, options=opts)
    ptext = open(ppos.path, encoding="utf-8").read()
    ntext = open(pneg.path, encoding="utf-8").read()
    assert (W.SUMMARY_BLOCK + "_pos") in ptext and (W.SUMMARY_BLOCK + "_neg") not in ptext
    assert (W.SUMMARY_BLOCK + "_neg") in ntext and (W.SUMMARY_BLOCK + "_pos") not in ntext


def test_render_sv_without_options_keeps_provider_defaults(mirror_wb):
    """预览路径 options=None → 不覆盖 provider 默认（逐字节与旧预览一致）。"""
    seen = {}

    class _P:
        view_id = "topout"

        def render_sv(self, only, mode, max_tests, exhaustive, edited, **kw):
            seen.update(kw)
            return "", {"summary": {}}

    X.render_sv(_P(), only=None, mode="min", max_tests=8, exhaustive=False, edited=None)
    assert "comments" not in seen and "scope" not in seen
    X.render_sv(_P(), only=None, mode="min", max_tests=8, exhaustive=False, edited=None,
                options={"comments": True})
    assert seen["comments"] is True and seen["scope"] == "all"


def test_write_text_translates_oserror(tmp_path):
    with pytest.raises(X.ExportError) as ex:
        X.write_text(str(tmp_path), "x")          # 目录当文件写 → OSError
    assert "无法写入" in str(ex.value)


# ───────────── ⑤ 报告：扩展名纠正 + 计数 + 无用例信号点名 ─────────────
def test_correct_report_ext_by_filter():
    xlsx = X.REPORT_FILTERS[1][0]
    assert X.correct_report_ext("dreg_report.html", xlsx).endswith(".xlsx")
    assert X.correct_report_ext("dreg_report.html", xlsx).count(".") == 1   # 不出双扩展名
    assert X.correct_report_ext("a.csv", None) == "a.csv"                   # 没选筛选器 → 不动


def test_report_kind_by_ext():
    assert X.report_kind("a.html") == "report_html"
    assert X.report_kind("a.xlsx") == "report_xlsx"
    assert X.report_kind("a.csv") == "report_csv"


def test_export_report_csv_counts_and_paths(mirror_wb, tmp_path):
    rep = T.topout_report(mirror_wb, mode="min", max_tests=64)
    out = X.export_report(str(tmp_path / "rep.csv"), rep, "mirror_btlp_dreg.xlsx")
    assert out.kind == "report_csv"
    assert out.counts["n_tests"] == len(rep["detail"])
    assert len(out.paths) >= 2 and all(os.path.exists(p) for p in out.paths)   # 明细 + 汇总
    # 无用例的信号必须点名（它在明细表里根本不出现）
    zero = [r.get("signal") for r in rep["summary"] if not (r.get("n_tests") or 0)]
    assert [n for n, _r in out.skipped] == zero


def test_export_report_ext_correction_applies(mirror_wb, tmp_path):
    rep = T.topout_report(mirror_wb, mode="min", max_tests=64)
    out = X.export_report(str(tmp_path / "r.html"), rep, "x.xlsx", X.REPORT_FILTERS[2][0])
    assert out.path.endswith(".csv") and out.kind == "report_csv"


# ───────────── ⑥ for_test 回填 ─────────────
def test_normalize_fortest_path_adds_ext_and_refuses_source(tmp_path):
    src = tmp_path / "src.xlsx"
    src.write_text("x", encoding="utf-8")
    assert X.normalize_fortest_path(str(tmp_path / "out"), str(src)).endswith("out.xlsx")
    with pytest.raises(X.ExportError) as ex:
        X.normalize_fortest_path(str(src), str(src))
    assert "不能是源 Excel 本身" in str(ex.value)


def test_export_fortest_needs_real_source(tmp_path):
    with pytest.raises(X.ExportError) as ex:
        X.export_fortest("", str(tmp_path / "o.xlsx"), wb=None, opts=None)
    assert "源 Excel" in str(ex.value)


def test_export_fortest_via_provider_reports_groups(mirror, mirror_wb, tmp_path):
    calls = {}

    class _P:
        view_id = "topout"

        def fortest(self, src, out, mode, max_tests, exhaustive, only=None):
            calls.update(src=src, out=out)
            open(out, "w", encoding="utf-8").close()
            return 7                              # provider 回组数 → outcome 报「回填 7 组」

    out = X.export_fortest(mirror, str(tmp_path / "ft"), provider=_P(), mode="min", max_tests=8)
    assert out.kind == "fortest" and out.path.endswith(".xlsx")
    assert out.counts["n_groups"] == 7 and "含 mux 表" in out.summary_text()
    assert calls["out"].endswith(".xlsx")


# ───────────── ⑦ nets.txt ─────────────
def test_nets_categories_from_pages(mirror_wb):
    cats = X.nets_categories(mirror_wb)
    assert cats[:2] == ["topout-cone", "topout"]          # 有 Topout 页 → 两个 Topout 类别在前
    assert "logic" in cats and "mux" in cats
    from dreg_verify import pageviews as P
    assert all(c in ("topout-cone", "topout") or c in P.PAGES for c in cats)


def test_export_nets_writes_and_counts_per_category(mirror_wb, tmp_path):
    p = tmp_path / "nets.txt"
    nets, out = X.export_nets(mirror_wb, str(p), {"topout", "logic"})
    assert p.exists() and nets
    assert out.counts["n_nets"] == len(nets)
    assert out.counts["topout"] > 0 and out.counts["logic"] > 0
    assert out.counts["n_nets"] <= out.counts["topout"] + out.counts["logic"]   # 去重并集


@pytest.mark.contract("C-186")
def test_c186_nets_by_purpose_three_cats(mirror_wb, tmp_path):
    """N3（冲突⑤）：nets.txt 多一套**按用途**三类，旧的按页类别与 nets_pages 键原样不动。

    按页分类是工具内部视角；红区工程师问的是「我要扫的是哪些网」——
    顶层输出 / 要 force 的目标 / 名字是猜出来的。"""
    from dreg_verify import inputs_table as IT
    from dreg_verify import rtl_scan

    assert X.nets_purpose_categories() == ("topout_out", "force_target", "guessed")

    # ① 三类的定义各自钉死（与 rtl_scan / 输入表的同一批产物对齐，不许另起一套判据）
    probe = rtl_scan.collect_topout_nets(mirror_wb)
    cone = rtl_scan.collect_topout_cone_nets(mirror_wb)
    nets, per = X.collect_nets_by_purpose(mirror_wb, ("topout_out",))
    assert set(nets) == set(probe) and per == {"topout_out": len(probe)}

    nets, per = X.collect_nets_by_purpose(mirror_wb, ("force_target",))
    assert set(nets) == set(cone) - set(probe)             # 探针以外的 = force 叶子 + iddq 门网
    assert per["force_target"] == len(nets) > 0

    nets, per = X.collect_nets_by_purpose(mirror_wb, ("guessed",))
    assert per["guessed"] == len(nets)
    # 「猜名」与输入信号表的 trusted 必须同一判据：btlp 镜像的输入全在寄存器表里查到 → 一根都不猜
    assert set(IT.TRUSTED_FOUND_IN) == {"tmm", "regmap"}
    assert nets == {}

    # ② 三类一起勾 = 去重并集，每类计数照出
    nets, per = X.collect_nets_by_purpose(mirror_wb, X.nets_purpose_categories())
    assert set(per) == {"topout_out", "force_target", "guessed"}
    assert set(nets) == set(cone)                          # 探针 + 叶子 = cone 全集
    assert len(nets) <= sum(per.values())
    assert X.collect_nets_by_purpose(mirror_wb, ()) == ({}, {})       # 一个都不勾 → 不干活

    # ③ 导出：写盘 + counts 三类在前、n_nets 是去重后的总数
    p = tmp_path / "nets_purpose.txt"
    got, out = X.export_nets_by_purpose(mirror_wb, str(p), ("topout_out", "force_target"))
    assert p.exists() and got and out.kind == "nets"
    assert list(out.counts)[:3] == ["n_nets", "topout_out", "force_target"]
    assert out.counts["n_nets"] == len(got) == len(set(cone))
    assert "顶层输出" in out.counts_text() and "force 目标" in out.counts_text()
    assert p.read_text(encoding="utf-8") == rtl_scan.render_nets_text(got)

    # ④ pages 非空 → 与旧「按页」口径**并集**，两套计数并排出现；旧 export_nets 一字不动
    p2 = tmp_path / "nets_both.txt"
    both, out2 = X.export_nets_by_purpose(mirror_wb, str(p2), ("topout_out",), pages={"logic"})
    page_nets, _ = X.collect_nets(mirror_wb, {"logic"})
    assert set(both) == set(probe) | set(page_nets)
    assert out2.counts["topout_out"] == len(probe) and out2.counts["logic"] == len(page_nets)
    assert list(out2.counts).index("topout_out") < list(out2.counts).index("logic")


@pytest.mark.contract("C-186")
def test_c186_nets_by_purpose_guessed_is_not_empty_on_wl(tmp_path):
    """N3 续：WL 镜像有真正「表里查不到」的输入 → guessed 类别非空，且严格是 force 目标的子集。

    btlp 镜像的输入全在寄存器表里查到（guessed=0），只用它测等于没测到这条分支。"""
    import make_mirror_excel
    from dreg_verify import rtl_scan
    p = tmp_path / "mirror_wl_dreg.xlsx"
    make_mirror_excel.build(str(p))
    wb = M.load_workbook(str(p))

    nets, per = X.collect_nets_by_purpose(wb, X.nets_purpose_categories())
    assert per["guessed"] > 0
    guessed, _ = X.collect_nets_by_purpose(wb, ("guessed",))
    force, _ = X.collect_nets_by_purpose(wb, ("force_target",))
    assert set(guessed) <= set(force)                  # 猜名的必定也是要 force 的那批里的
    assert set(nets) == set(rtl_scan.collect_topout_cone_nets(wb))
    # 每条用途说明都点破「为什么这根网在清单里」（nets.txt 的注释行是工程师唯一的线索）
    assert all("猜" in why for why in guessed.values())


# ───────────── ⑧ claims.json：与 CLI 逐字节同构 ─────────────
def test_export_claims_byte_identical_to_cli(mirror, mirror_wb, tmp_path, capsys):
    """同一 mirror、同一选项：GUI 函数 export_claims 与 CLI `--topout --export-claims`
    写出的 JSON 必须**逐字节**相同（claims.json 是红区 scan_rtl 校验器的输入契约，
    两条路径漂了就等于给红区两份不同的契约）。payload 里没有时间戳类字段，故直接比字节。"""
    cli_json = tmp_path / "cli_claims.json"
    cli.main(["--excel", mirror, "--topout", "--export-claims", str(cli_json)])
    capsys.readouterr()
    gui_json = tmp_path / "gui_claims.json"
    out = X.export_claims(_FakeTopoutProvider(mirror_wb), str(gui_json), mirror)
    assert gui_json.read_bytes() == cli_json.read_bytes()
    payload = json.loads(gui_json.read_text(encoding="utf-8"))
    assert payload["naming_model"] == "topout"             # Topout 视图 = 顶层真名
    assert payload["n_claims"] == out.counts["n_claims"] > 0
    assert out.counts["n_probe"] + out.counts["n_force"] + out.counts["n_rfwrite"] <= payload["n_claims"]


def test_export_claims_does_not_print_to_stdout(mirror_wb, tmp_path, capsys):
    """CLI 写出器会 print 一行；GUI 侧必须吞掉（pythonw 下 stdout 可能为 None）。"""
    X.export_claims(_FakeTopoutProvider(mirror_wb), str(tmp_path / "c.json"), "m.xlsx")
    assert "claim 清单已导出" not in capsys.readouterr().out


def test_claims_naming_model_per_view():
    class _Page:
        view_id = "logic"
    assert X.claims_naming_model(_FakeTopoutProvider(None)) == "topout"
    assert X.claims_naming_model(_Page()) == "logic-rooted"


def test_export_claims_from_build_dict(tmp_path):
    b = {"claims": [{"kind": "probe", "signal": "d_x"}, {"kind": "force", "signal": "d_x"}]}
    out = X.export_claims(b, str(tmp_path / "c.json"), "m.xlsx")
    payload = json.loads((tmp_path / "c.json").read_text(encoding="utf-8"))
    assert payload["n_claims"] == 2 and out.counts["n_probe"] == 1


# ───────────── ⑨ 单信号真值表 CSV：三行补齐 ─────────────
def _cols():
    return [{"name": "T0", "neg": False, "vals": {"a": 1}, "exp": None, "auto": 0,
             "auto_w": 1, "vec": None},
            {"name": "T1_NEG", "neg": True, "vals": {"a": 0}, "exp": 1, "auto": 0,
             "auto_w": 1, "vec": None}]


def test_signal_csv_text_has_bin_force_rfwrite_rows():
    """契约补齐：SignalView 的 CSV 此前缺 期望(bin)/force/RF_WRITE 三行（『排查(旧)』本就有）。"""
    text = X.signal_csv_text(_cols(), [{"key": "a", "label": "d_in", "width": 1}], out_width=1)
    labels = [ln.split(",")[0] for ln in text.strip().splitlines()]
    assert labels == ["信号\\测试", "d_in", "auto_out", "期望(进.sv)", "期望(bin)",
                      "期望来源", "负向?", "force", "RF_WRITE"]
    assert "1'b1" in text                                  # 负向列的期望(bin)


def test_signal_csv_transpose_and_exp_source():
    text = X.signal_csv_text(_cols(), [{"key": "a", "label": "d_in", "width": 1}], out_width=1)
    rows = [ln.split(",") for ln in text.strip().splitlines()]
    assert rows[0] == ["信号\\测试", "T0", "T1_NEG"]        # 每列一条测试
    src = next(r for r in rows if r[0] == "期望来源")
    assert src[1] == "auto_out兜底" and src[2] == "负向(故意填错)"
    neg = next(r for r in rows if r[0] == "负向?")
    assert neg[1] == "" and neg[2] == "是"


def test_signal_csv_drive_fn_fills_force_rows():
    def _drive(col):
        return ("d_leaf=1'h1", "0x10=1'h0")
    text = X.signal_csv_text(_cols(), [], out_width=1, drive_fn=_drive)
    assert "d_leaf=1'h1" in text and "0x10=1'h0" in text


def test_signal_csv_multibit_width_suffix():
    cols = [{"name": "T0", "neg": False, "vals": {}, "exp": None, "auto": 5,
             "auto_w": 4, "vec": None}]
    text = X.signal_csv_text(cols, [], out_width=4)
    assert "auto_out[3:0]" in text and "期望(进.sv)[3:0]" in text and "4'b0101" in text


def test_write_signal_csv_is_utf8_sig(tmp_path):
    p = tmp_path / "s.csv"
    out = X.write_signal_csv(str(p), _cols(), [], out_width=1, name="d_x")
    assert p.read_bytes().startswith(b"\xef\xbb\xbf")      # Excel 双击不乱码
    assert out.kind == "signal_csv" and out.counts["n_columns"] == 2
    assert "d_x" in out.summary_text()


def test_drive_strings_never_raises():
    assert X.drive_strings(None, {}, []) == ("", "")
    assert X.drive_strings(object(), {}, []) == ("", "")   # 垃圾向量也只返回空串，不炸


@pytest.mark.contract("C-057")
def test_c057_drive_pair_single_source():
    """§7-6：驱动串只许有一个口径。exports.drive_strings 就是 inputs_table.drive_pair
    【同一个函数对象】——两份实现同一件事必漂（改一处忘另一处 = CSV 与输入表印出两种驱动文本）。"""
    from dreg_verify import inputs_table as IT
    assert X.drive_strings is IT.drive_pair


def test_drive_context_picks_mux_expansion():
    an = {"kind": "mux", "expansion": {"bindings": {"c:0": 1}, "used_vars": ["c:0"]},
          "node": None, "bindings": {}}
    assert X.drive_context(an) == ({"c:0": 1}, ["c:0"])
    assert X.drive_context({"kind": "logic", "node": None, "bindings": {"A": 2}}) == ({"A": 2}, [])


# ───────────── ⑩ 两个 legacy 数据模型 → 统一列模型 ─────────────
def test_columns_from_rowdicts_maps_expected_and_gate():
    rows = [{"base_values": {"A": 1}, "correct": 1, "correct_width": 1, "expected": 1,
             "is_negative": False, "designer_expected": None},
            {"base_values": {"A": 0}, "correct": 0, "correct_width": 1, "expected": 1,
             "is_negative": True, "designer_expected": None}]
    groups = [{"key": "A", "label": "d_in", "width": 1}]
    cols, inputs = X.columns_from_rowdicts(
        rows, groups, 1, label_col=lambda rd, i: "T%d" % i, label_row=lambda g: g["label"],
        gate_label="d_gate (DFT门)", gate_value=1, gate_row=0)
    assert cols[0]["exp"] is None                       # 未手填 → 走 auto_out 兜底
    assert cols[1]["exp"] == 1 and cols[1]["neg"] is True
    assert inputs[0]["label"] == "d_gate (DFT门)"       # 门行插在 gate_row 指定的位置
    assert all(c["vals"]["__dft_gate__"] == 1 for c in cols)


def test_columns_from_mux_vectors_labels_roles():
    v = V.TestVector(0, {"c:0": 1, "m0": 3}, 3, 2)

    class _B:
        def __init__(self, base, width):
            self.base, self.width = base, width

    bindings = {"c:0": _B("d_ctrl", 1), "m0": _B("d_data", 2)}
    cols, inputs = X.columns_from_mux_vectors([v], bindings, ["c:0", "m0"],
                                              data_keys={"m0"}, out_width=2)
    assert [e["label"] for e in inputs] == ["d_ctrl (控制)", "d_data (数据)"]
    assert cols[0]["auto"] == 3 and cols[0]["exp"] is None
    text = X.signal_csv_text(cols, inputs, out_width=2)
    assert "(数据)" in text and "(控制)" in text
