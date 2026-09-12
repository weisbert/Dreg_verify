# -*- coding: utf-8 -*-
"""GUI v2 骨架（Phase B3 产出 B）自检：import 全过、objectName 无重复、色值格式、术语覆盖 11 个禁用词。

不起 Qt：contracts / names / theme / terms 四个模块必须在无 PySide6 的环境下也能 import。
"""
import re

import pytest

from dreg_verify.ui import contracts, names, terms, theme


def test_ui_skeleton_imports():
    import dreg_verify.ui  # noqa: F401
    assert contracts.VIEW_IDS[0] == "topout"
    assert len(contracts.VIEW_IDS) == 5


# ─────────────── names ───────────────
_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")


def test_names_unique_and_well_formed():
    reg = names.all_names()
    assert len(reg) >= 180, "注册表太小：%d" % len(reg)
    vals = list(reg.values())
    dup = {v for v in vals if vals.count(v) > 1}
    assert not dup, "objectName 重复：%s" % sorted(dup)
    bad = [k for k, v in reg.items() if not _NAME_RE.match(v)]
    assert not bad, "不合规的 objectName：%s" % bad


def test_names_fmt_helpers_do_not_collide_with_constants():
    reg = set(names.all_names().values())
    dyn = set()
    for k in names.EXPORT_KINDS:
        dyn |= {names.fmt_export_check(k), names.fmt_export_scope(k), names.fmt_export_scope_objects(k),
                names.fmt_export_scope_sv(k), names.fmt_export_options(k), names.fmt_export_last(k)}
    for v in contracts.VIEW_IDS:
        dyn.add(names.fmt_scope_btn(v))
    for f in ("register", "boolean", "select", "gated"):
        dyn.add(names.fmt_cov_form_combo(f))
    assert all(_NAME_RE.match(d) for d in dyn)
    # 动态名与常量名允许相同值仅当它们指同一控件（scope 段 / cov 下拉），其余不得撞
    allowed_same = {names.fmt_scope_btn(v) for v in contracts.VIEW_IDS} | {
        names.fmt_cov_form_combo(f) for f in ("register", "boolean", "select", "gated")}
    clash = (dyn & reg) - allowed_same
    assert not clash, clash
    assert names.HARNESS_EXCEL_PATH_EDIT == "excel_path_edit"   # ui_harness._set_excel_path 的退回名


# ─────────────── theme ───────────────
_HEX_RE = re.compile(r"^#[0-9a-f]{6}$")


def test_theme_colors_are_lowercase_hex():
    cols = theme.all_colors()
    assert len(cols) >= 55
    bad = {k: v for k, v in cols.items() if not _HEX_RE.match(v)}
    assert not bad, bad
    assert theme.MASK.startswith("rgba(")


def test_theme_cell_states_seven_and_design_values():
    assert set(theme.CELL_STATES) == {"match", "diff", "unfilled", "neg", "dft", "readonly", "editable"}
    for k, (bg, border, fg, style) in theme.CELL_STATES.items():
        assert _HEX_RE.match(bg) and _HEX_RE.match(border) and _HEX_RE.match(fg), k
        assert style in ("solid", "dashed")
    assert theme.CELL_STATES["unfilled"][3] == "dashed"
    assert theme.CELL_STATES["dft"][0] == "#eef4fd"        # 裁决⑭：iddq 拍淡蓝
    assert theme.CELL_STATES["diff"] == ("#fdecea", "#e5a9a4", "#a9302a", "solid")
    assert theme.TONE_COLORS["warn"] == ("#8a6412", "#fdf3e0")


def test_theme_sizes_from_design_state():
    assert (theme.LIST_W, theme.SIDE_W, theme.CHAIN_H, theme.FROZEN_W, theme.TRUTH_H) == (560, 470, 300, 288, 420)
    assert theme.CLAMP_LIST == (320, 900) and theme.CLAMP_TRUTH == (160, 820)
    assert theme.ZOOM_MIN == 0.3 and theme.ZOOM_MAX == 3.0 and theme.ZOOM_STEP == 1.12
    assert theme.HANDLE_W == 6 and theme.ROW_H == 26 and theme.TRUTH_COL_W == 34


# ─────────────── terms ───────────────
def test_terms_table_has_eleven_rows_covering_forbidden_words():
    assert len(terms.TERMS) == 11
    bads = [t[0] for t in terms.TERMS]
    for word in ("cone", "F0–F4", "缝", "prefixed-wire", "wire 兜底", "记账", "claims", "tmm", "regmap", "CUVUNF", "假绿"):
        assert any(word in b for b in bads), word
    for bad, good, full in terms.TERMS:
        assert good and full and good != bad


def test_terms_scrub_is_inputs_table_scrub_terms():
    from dreg_verify import inputs_table
    assert terms.scrub is inputs_table.scrub_terms
    for raw in ("无 cone，跳过+记账", "prefixed-wire 提升", "wire 兜底", "regmap 命中", "tmm 命中", "必 CUVUNF", "claims 单列"):
        out = terms.scrub(raw)
        assert out != raw, raw
        assert "记账" not in out and "CUVUNF" not in out


def test_terms_status_eight_grades_plus_extras():
    for k in ("clean", "wire-fallback", "unresolved", "parse-err", "spec-collision", "needs-prefix",
              "bare-probe", "false-green"):                       # C-016 八档
        assert k in terms.STATUS, k
    assert "risky-generated" in terms.STATUS                       # C-041 第 6 档
    assert "pending" in terms.STATUS and "skip" in terms.STATUS
    for k, (label, tone, help_text) in terms.STATUS.items():
        assert label and tone in ("ok", "warn", "bad", "note") and help_text, k
    assert set(terms.STATUS_FALLBACK) == {"ok", "skip", "unresolved", "error"}


def test_terms_reason_templates_follow_design_instances():
    for k in ("false-green", "spec-collision", "needs-prefix", "bare-probe"):   # Design D 数组四条实例
        title, body, action, target = terms.REASON_TEMPLATES[k]
        assert title and body and action and target in terms.REASON_TARGETS
    assert "工具不猜哪条对" in terms.REASON_TEMPLATES["spec-collision"][1]
    assert "仿真必过" in terms.REASON_TEMPLATES["false-green"][1]
    assert terms.REASON_RISKY_BTN.startswith("去诊断")            # 裁决③：没有「仍然生成」按钮
    assert "仍然生成" not in terms.REASON_RISKY_BTN


def test_terms_copy_has_no_git_word_and_step2_reworded():
    blob = []

    def _walk(v):
        if isinstance(v, str):
            blob.append(v)
        elif isinstance(v, dict):
            for x in v.values():
                _walk(x)
        elif isinstance(v, (tuple, list)):
            for x in v:
                _walk(x)
    for k, v in terms.all_copy().items():
        if k in ("REDLINES", "FORBIDDEN", "TERMS"):
            continue
        _walk(v)
    text = "\n".join(blob)
    assert "git" not in text.lower()                                # 裁决⑬
    assert "零参数" not in text                                     # 裁决⑪
    assert "在仿真服务器上跑（不带参数）" in text
    assert "C:\\code" not in text and "mirror_" not in text         # 裁决⑫：不写示例路径
    assert "请修正" not in text                                     # V2Spec §3 六


# ─────────────── contracts ───────────────
def test_contracts_list_columns_default_visible_matches_design():
    C = contracts.ListCol
    assert contracts.LIST_DEFAULT_VISIBLE == (C.CHECK, C.NEG, C.NAME, C.STATUS, C.NTEST, C.OWNER)
    assert set(contracts.LIST_OPTIONAL) == {C.ASSERT_ID, C.KIND, C.FORM, C.PREFIX, C.EXPR}
    assert set(contracts.LIST_COL_KEYS) == set(C)
    assert set(terms.LIST_HEADERS) == set(contracts.LIST_COL_KEYS.values())


def test_contracts_roles_unique_and_above_user_role():
    for enum in (contracts.ListRole, contracts.TruthRole):
        vals = [int(x) for x in enum]
        assert len(vals) == len(set(vals))
        assert min(vals) > 0x0100


def test_contracts_shortcuts_ruling():
    s = contracts.SHORTCUTS
    assert s["copy_col"] == "Ctrl+D" and s["diagnostics"] == "Ctrl+Shift+D"
    assert s["export_center"] == "Ctrl+G" and s["sv_preview"] == "Ctrl+P" and s["export_report"] == "Ctrl+R"
    keys = [v for k, v in s.items() if k not in ("undo", "redo", "paste")]
    assert len(keys) == len(set(keys)), "全局快捷键撞键"


def test_contracts_signal_tables_and_dataclasses():
    for table in (contracts.WORKER_SIGNALS, contracts.STATE_SIGNALS, contracts.BUS_SIGNALS, contracts.TRUTH_SIGNALS):
        for name, sig in table.items():
            assert re.match(r"^[a-z][A-Za-z]*$", name) and sig.startswith("("), (name, sig)
    r = contracts.ExportRowSpec(kind="sv")
    assert r.object_scope == "checked" and r.sv_scope == "all"
    assert contracts.EXPORT_KINDS == names.EXPORT_KINDS
    assert set(terms.EXPORT_ROWS) == set(contracts.EXPORT_KINDS)
    assert "split" in contracts.EXPORT_SV_SCOPES and set(terms.EXPORT_SV_SCOPES) == set(contracts.EXPORT_SV_SCOPES)
    # C4-int：四档的键**与引擎侧同一套** —— `exports.SCOPE_LABEL` 少一档 split 时，
    # 导出中心选得出、`load_export_options` 却读不回（C-162 的「记住上次选择」静默少一档）。
    from dreg_verify import exports as _X
    assert tuple(_X.SCOPE_LABEL) == contracts.EXPORT_SV_SCOPES
    assert contracts.CHAIN_ENTRY_KEYS == ("out", "expr", "subst", "page", "kind")


@pytest.mark.parametrize("mod", [contracts, names, theme, terms])
def test_skeleton_modules_are_qt_free(mod):
    src = open(mod.__file__, encoding="utf-8").read()
    assert not re.search(r"^\s*(import|from)\s+PySide6", src, re.M), mod.__name__
