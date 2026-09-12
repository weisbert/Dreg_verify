# -*- coding: utf-8 -*-
"""GUI v2 C2-a：右侧常驻栏 `ui/side_panel.py` 的契约测试（附录 A「SIDE」14 条，每条至少一个测试名含 ID）。

口径（执行计划 §2 L3/L4/L5，与 `test_ui_signal_list.py` 一致）：
  · offscreen 起**独立** `SidePanel`（不起 MainWindow）；鼠标一律走 `ui_harness` 的
    `click / click_cell`（QTest 真实事件），不直接调槽、不 emit 信号冒充用户；
  · 喂进去的是 **mirror 的真 an** —— `providers.TopoutProvider(cfg).analyze(name, ...)`，
    两张 mirror 的 Topout 共 21 个信号，四种根（带 iddq 门的 logic / 单级 logic / mux / 直连寄存器）
    与三种「有话要说」的档（只读回读跳过 / 规格冲突 / 猜名）都齐；
  · 高亮走真 `ui.bus.HighlightBus`，且用**两块面板共用一根总线**来证明「经 bus 不直连」（C-282）。
"""

import os

import pytest

import ui_harness as H

pytest.importorskip("PySide6")

from PySide6 import QtCore, QtGui, QtWidgets        # noqa: E402

from dreg_verify import excel_model, inputs_table, providers      # noqa: E402
from dreg_verify.ui import bus as BUS                # noqa: E402
from dreg_verify.ui import names as N                # noqa: E402
from dreg_verify.ui import persist as P              # noqa: E402
from dreg_verify.ui import side_panel as SP          # noqa: E402
from dreg_verify.ui import terms as T                # noqa: E402
from dreg_verify.ui import theme as TH               # noqa: E402

Qt = QtCore.Qt


# ─────────────────────────── 真 an（mirror）───────────────────────────
class _Cfg(object):
    """`providers.ConfigSourceProto` 的最小满足物（诊断配置全空 = 出厂态）。"""

    def __init__(self, wb):
        self.wb = wb
        self.probe_prefixes = {}
        self.force_signals = set()
        self.logic_overrides = {}
        self.include_risky = True


_PROV = {}
_AN = {}
_ALIVE = []            # 面板引用：Qt 侧父子链之外，Python 侧也得留着，免得被 GC 掉


def provider(kind):
    if kind not in _PROV:
        wb = excel_model.load_workbook(H.mirror_path(kind))
        _PROV[kind] = providers.TopoutProvider(_Cfg(wb))
    return _PROV[kind]


def an_of(kind, name):
    key = (kind, name)
    if key not in _AN:
        _AN[key] = provider(kind).analyze(name, "max", 256, False)
    assert _AN[key], "mirror %s 里分析不出 %s" % (kind, name)
    return _AN[key]


def all_ans():
    """两张 mirror 的 Topout 全部信号的真 an（21 个）。"""
    out = []
    for kind in ("wl", "btlp"):
        p = provider(kind)
        for m in p.view_models("max", 256, False, lite=True):
            out.append((kind, m["name"], an_of(kind, m["name"])))
    return out


@pytest.fixture(scope="module")
def qapp():
    return H.app()


@pytest.fixture(autouse=True)
def iso(monkeypatch, tmp_path):
    """持久化文件指到临时目录（本模块不写盘，纯保险：FakeState 之外的路径也不碰用户真机）。"""
    monkeypatch.setattr(P, "SETTINGS_PATH", str(tmp_path / "gui_settings.json"))
    monkeypatch.setattr(P, "EDITS_PATH", str(tmp_path / "edits.json"))


def panel(bus=None, state=None, size=(470, 760)):
    """一块独立的右栏（默认不挂 state，直接 `show_signal(an)` 喂数据）。"""
    w = SP.SidePanel(state=state, bus=bus)
    w.resize(*size)
    w.show()
    H.app().processEvents()
    _ALIVE.append(w)
    return w


def chain_text(w):
    return w.chain_view.toPlainText()


def main_row(w, i):
    """第 i 个输入的主行四格文本。"""
    m = w.inputs_view.model()
    return [str(m.data(m.index(2 * i, c), Qt.DisplayRole) or "") for c in range(m.columnCount())]


def drive_row(w, i):
    """第 i 个输入的第二行（常驻驱动串）文本。"""
    m = w.inputs_view.model()
    return str(m.data(m.index(2 * i + 1, 0), Qt.DisplayRole) or "")


def color_of(w, row, col, role=Qt.ForegroundRole):
    m = w.inputs_view.model()
    v = m.data(m.index(row, col), role)
    return v.name().lower() if isinstance(v, QtGui.QColor) else ""


def cell_has_color(w, row, col, hexcolor):
    """真的把那一格画出来，数一数有没有这个颜色的像素（虚下划线 / 描边这类只能这么验）。"""
    view = w.inputs_view
    idx = view.model().index(row, col)
    view.scrollTo(idx)
    H.app().processEvents()
    rect = view.visualRect(idx)
    if rect.isEmpty():
        return False
    img = view.viewport().grab(rect).toImage()
    want = QtGui.QColor(hexcolor).rgb()
    return any(img.pixel(x, y) == want
               for y in range(img.height()) for x in range(img.width()))


# ═════════════════════ C-050 逐层展开：序号 + 页标签 + 原式/代入两行 ═════════════════════
def test_c050_chain_layers_have_marks_page_tags_and_two_lines(qapp):
    """跨页展开链：每层 ①②③ + 页标签（dft 页门控 / logic 页 / mux 页 · mux3）+ 原式 + 代入真名。"""
    w = panel()
    an = an_of("wl", "d_wl_rf_lo2g5g_bias_en")
    w.show_signal(an)

    blocks = w.chain_view.blocks()
    assert len(blocks) == len(an["chain"]) == 3
    assert [b["mark"] for b in blocks] == ["①", "②", "③"]
    assert [b["tag"] for b in blocks] == [T.CHAIN_PAGE_TAGS["dft"], T.CHAIN_PAGE_TAGS["logic"],
                                          T.CHAIN_PAGE_TAGS["mux"].format(group="mux3")]
    text = chain_text(w)
    for b, c in zip(blocks, an["chain"]):
        assert b["expr"] == c["expr"] and b["subst"] == c["subst"]
        assert c["expr"] in text, "原式没画出来：%s" % c["expr"]
        assert c["subst"] in text, "代入真名行没画出来：%s" % c["subst"]
    # 序号与页标签各占一行，原式与代入各占一行 → 每层至少 3 行
    assert text.count("①") == 1 and text.count("③") == 1
    H.shot(w, "side_chain_three_layers")


def test_c050_page_tag_omitted_when_engine_gives_no_page(qapp):
    """没有 `page` 键的链（C0 之前的 an）→ 只出序号，**不猜**页标签。"""
    w = panel()
    an = dict(an_of("wl", "d_wl_rf_lo2g5g_bias_en"))
    an["chain"] = [{k: v for k, v in c.items() if k not in ("page", "kind")} for c in an["chain"]]
    w.show_signal(an)
    assert [b["tag"] for b in w.chain_view.blocks()] == ["", "", ""]
    for tag in T.CHAIN_PAGE_TAGS.values():
        if "{group}" not in tag:
            assert tag not in chain_text(w)


def test_c050_chain_height_defaults_to_300_and_is_draggable(qapp):
    """上半「逐层展开」默认 `theme.CHAIN_H`=300，可拖（clamp 120–620）。"""
    w = panel()
    assert TH.CHAIN_H == 300
    assert abs(w.chain_height() - TH.CHAIN_H) <= 2
    assert w.splitter.objectName() == N.WIN_SPLIT_CHAIN_INPUTS
    assert not w.splitter.childrenCollapsible()
    w.set_chain_height(9999)
    assert w.chain_height() <= TH.CLAMP_CHAIN[1]
    w.set_chain_height(0)
    assert w.chain_height() >= TH.CLAMP_CHAIN[0]


# ═════════════════════ C-051 单级 logic：原式 / 代入真名 + 直连叶子说明 ═════════════════════
def test_c051_single_level_logic_shows_expr_subst_and_leaf_note(qapp):
    """没有上游的单级 logic：链区仍给「原式 / 字母代入真名」两行，并说明输入均为直连叶子。"""
    w = panel()
    an = an_of("btlp", "d_logic_bt_lp_rx_en")
    assert not an["chain"], "这个信号本来就没有跨页展开链（兜底路径才是被测对象）"
    w.show_signal(an)

    blocks = w.chain_view.blocks()
    assert len(blocks) == 1 and blocks[0]["tag"] == T.CHAIN_PAGE_TAGS["logic"]
    text = chain_text(w)
    assert an["sig"].expr in text                       # Excel 原式（还是字母）
    for b in an["bindings"].values():                   # 代入行是真实信号名
        assert b.base in text
    assert T.CHAIN_LEAF_NOTE in text
    H.shot(w, "side_chain_single_logic")


# ═════════════════════ C-052 mux：case 选路结构 ═════════════════════
def test_c052_mux_chain_shows_case_select_structure(qapp):
    """mux 信号链区显示 case 选路结构（控制值 → 选中的数据源）。"""
    w = panel()
    an = an_of("wl", "d_wl_rf_lpf_cmain")
    w.show_signal(an)

    blocks = w.chain_view.blocks()
    assert len(blocks) == 1
    assert blocks[0]["tag"] == T.CHAIN_PAGE_TAGS["mux"].format(group="mux%s" % an["sig"].group_no)
    text = chain_text(w)
    assert "case(" in text
    assert an["sig"].expr in text
    for src in ("d_wl_rf_lpf_cmain0", "d_wl_rf_lpf_cmain1"):     # 两条数据路都点了名
        assert src in text
    H.shot(w, "side_chain_mux")


# ═════════════════════ C-053 直连寄存器 ═════════════════════
def test_c053_register_root_says_top_port_equals_field_no_expansion(qapp):
    """直连寄存器信号：链区写「顶层口 == 该寄存器字段写值，无展开」。"""
    w = panel()
    an = an_of("btlp", "clk_force_on")
    assert an["kind"] == "register"
    w.show_signal(an)
    text = chain_text(w)
    assert T.CHAIN_REGISTER_DIRECT in text
    assert "clk_force_on" in text
    H.shot(w, "side_chain_register")


# ═════════════════════ C-054 不可建信号：状态与原因，不是空白 ═════════════════════
def test_c054_unbuildable_signal_writes_status_and_reason_not_blank(qapp):
    """只读回读 / 规格冲突：链区直接写状态标签 + 原因全文（且后端术语已 scrub）。"""
    w = panel()

    ro = an_of("btlp", "pll_lock_indicator")            # status=skip
    w.show_signal(ro)
    text = chain_text(w)
    assert text.strip(), "不可建信号的链区不能是空白"
    assert T.STATUS["skip"][0] in text
    assert T.scrub(ro["note"]) in text
    for raw in ("记账", "无 cone", "regmap"):            # I-12：后端术语不许裸出现
        assert raw not in text
    H.shot(w, "side_chain_unbuildable")

    bad = an_of("wl", "d_wl_rf_lp5g_rxrf_lna_lctune")   # status_detail=spec-collision
    w.show_signal(bad)
    text = chain_text(w)
    assert T.STATUS["spec-collision"][0] in text
    assert T.scrub(bad["issues"][0]) in text


def test_c054_empty_pending_and_failed_states_use_terms_copy(qapp):
    """空态三种：未选信号 / 还在后台展开 / 分析失败——文案都来自 terms，且输入表清空。"""
    import ui_fakes as F

    st = F.FakeState()
    w = panel(state=st)
    assert chain_text(w).strip() == ""                       # 未选信号
    assert w.inputs_title.text() == T.SIDE_INPUTS_TITLE_FMT.format(n=0)

    st.set_models(st.scope, st.provider().skeleton_models(), partial=True)
    st.set_current(F.DEFAULT_NAMES[0])                       # 骨架行 status=pending
    assert T.HDR_PENDING in chain_text(w)

    st.set_models(st.scope, st.provider().view_models(lite=True))
    st.analyze = lambda *a, **k: None                        # 分析失败（state 吞掉异常返回 None）
    st.set_current(F.DEFAULT_NAMES[1])
    assert T.HDR_ANALYSIS_FAILED in chain_text(w)
    assert w.inputs_view.model().rowCount() == 0


# ═════════════════════ C-056 输入表四列 + 两行一组 ═════════════════════
def test_c056_inputs_table_four_columns_and_two_rows_per_input(qapp):
    """『输入信号』表常驻：四列（字母 / 信号(位宽) / 角色 / 类型），每个输入占两行。"""
    w = panel()
    an = an_of("wl", "d_wl_rf_lo2g5g_bias_en")
    rows = inputs_table.input_rows(an)
    w.show_signal(an)

    assert H.header_texts(w.inputs_view) == list(T.SIDE_INPUTS_HEADERS)
    m = w.inputs_view.model()
    assert m.columnCount() == 4
    assert m.rowCount() == 2 * len(rows)
    assert w.inputs_title.text() == T.SIDE_INPUTS_TITLE_FMT.format(n=len(rows))
    for i, r in enumerate(rows):
        assert main_row(w, i) == [r["letter"], r["name"], T.scrub(r["role"]), r["rw"]]
        assert w.inputs_view.columnSpan(2 * i + 1, 0) == 4      # 驱动行整行跨列
    H.shot(w, "side_inputs_table")


def test_c056_inputs_rows_match_engine_for_every_mirror_signal(qapp):
    """两张 mirror 的 21 个 Topout 信号，逐行零差异对照 `inputs_table.input_rows`。"""
    w = panel()
    checked = 0
    for kind, name, an in all_ans():
        rows = inputs_table.input_rows(an)
        w.show_signal(an)
        m = w.inputs_view.model()
        assert m.rowCount() == 2 * len(rows), "%s/%s 行数对不上" % (kind, name)
        for i, r in enumerate(rows):
            assert main_row(w, i) == [r["letter"], r["name"], T.scrub(r["role"]), r["rw"]], \
                "%s/%s 第 %d 行" % (kind, name, i)
            assert drive_row(w, i) == SP.drive_text(r)
        checked += 1
    assert checked == 21


# ═════════════════════ C-057 常驻驱动串 ═════════════════════
def test_c057_drive_line_shows_rfwrite_and_force(qapp):
    """每行下面第二行常驻驱动串：RW → `RF_WRITE 0x<地址> bit<<<位>`，RO → `force ENV_RF.<网名>`。"""
    w = panel()
    an = an_of("wl", "d_wl_rf_lo2g5g_bias_en")
    rows = inputs_table.input_rows(an)
    w.show_signal(an)

    rw = [i for i, r in enumerate(rows) if r["rw"] == "RW"]
    ro = [i for i, r in enumerate(rows) if r["rw"] == "RO"]
    assert rw and ro, "这个信号同时有 RW 与 RO 输入，两种驱动写法各验一条"
    assert drive_row(w, rw[0]).startswith("RF_WRITE 0x")
    assert "bit<<" in drive_row(w, rw[0])
    assert drive_row(w, ro[0]).startswith("force ENV_RF.")
    # 驱动行常驻（不是 tooltip / 不是悬停才出现）：DisplayRole 直接就有
    for i in range(len(rows)):
        assert drive_row(w, i)


# ═════════════════════ C-058 需探针前缀标记 ═════════════════════
def test_c058_needs_prefix_row_marked_in_drive_line(qapp):
    """内部衔接网：驱动行带「⚠需探针前缀（…跑 scan_rtl 配前缀否则跳过）」，文案取自 terms。"""
    w = panel()
    an = an_of("wl", "d_wl_rf_lpf_cmain")
    rows = inputs_table.input_rows(an)
    idx = [i for i, r in enumerate(rows) if r["needs_prefix"]]
    assert idx, "这个信号的控制口是上游 mux 的衔接网，本来就要前缀"
    w.show_signal(an)

    for i in idx:
        assert T.SIDE_NEEDS_PREFIX_MARK in drive_row(w, i)
        assert w.inputs_view.model().data(
            w.inputs_view.model().index(2 * i, 1), SP.ROLE_NEEDS_PREFIX) is True
    for i in set(range(len(rows))) - set(idx):
        assert T.SIDE_NEEDS_PREFIX_MARK not in drive_row(w, i)
    H.shot(w, "side_inputs_needs_prefix")


# ═════════════════════ C-059 驱动行两档色 ═════════════════════
def test_c059_drive_colors_unresolved_red_needs_prefix_amber_else_blue(qapp):
    """驱动行上色：未解析=红 / 需前缀=琥珀 / 其余=蓝（色值只来自 theme）。"""
    w = panel()
    an = an_of("wl", "d_wl_rf_lpf_cmain")
    rows = inputs_table.input_rows(an)
    w.show_signal(an)
    for i, r in enumerate(rows):
        want = TH.WARN_FG if r["needs_prefix"] else TH.BLUE_DARK
        assert color_of(w, 2 * i + 1, 0) == want.lower(), "第 %d 行驱动色不对" % i

    # 未解析行 mirror 里天然没有 —— 在真行上只改 resolved / drive 两格造一条（其余键全是引擎给的）
    fake = [dict(rows[0])]
    fake[0].update({"resolved": False, "needs_prefix": False,
                    "drive": "%s：没解析到网" % T.SIDE_UNRESOLVED_MARK})
    w.inputs_view.set_rows(fake)
    assert color_of(w, 1, 0) == TH.BAD_FG.lower()
    assert drive_row(w, 0).startswith(T.SIDE_UNRESOLVED_MARK)


# ═════════════════════ C-060 mux 控制三来源细分角色 ═════════════════════
def test_c060_mux_control_roles_split_by_source(qapp):
    """mux 的输入表按控制三来源细分角色（寄存器直出 / line 路径 / local 路径 / 模式位 /
    经上游 mux 驱动 / 上游 mux 配方）——由 `inputs_table` 算，本模块逐字显示。"""
    w = panel()
    seen = set()
    for kind, name in (("btlp", "d_bt_lp_lna_itrim"), ("wl", "d_wl_rf_lpf_cmain"),
                       ("wl", "d_wl_rf_lp5g_rxrf_lna_lctune")):
        an = an_of(kind, name)
        rows = inputs_table.input_rows(an)
        w.show_signal(an)
        for i, r in enumerate(rows):
            assert main_row(w, i)[2] == T.scrub(r["role"])
            seen.add(r["role"])
    joined = " | ".join(sorted(seen))
    for frag in ("模式位", "line路径", "local路径", "寄存器直出", "经上游mux", "上游mux配方"):
        assert frag in joined, "角色列少了「%s」这一档：%s" % (frag, joined)
    H.shot(w, "side_inputs_mux_roles")


# ═════════════════════ C-061 mux 数据寄存器收拢 + 标 case ═════════════════════
def test_c061_mux_data_registers_collapsed_with_case_label(qapp):
    """mux 的数据寄存器按物理寄存器收拢（同一寄存器只显一行），字母列标「被哪个 case 选中」。"""
    w = panel()
    an = an_of("btlp", "d_bt_lp_lna_itrim")
    rows = inputs_table.input_rows(an)
    w.show_signal(an)

    data_rows = [(i, r) for i, r in enumerate(rows) if str(r["letter"]).startswith("case ")]
    assert len(data_rows) >= 2
    names = [r["name"] for _i, r in data_rows]
    assert len(names) == len(set(names)), "同一物理寄存器出现了多行：%s" % names
    for i, r in data_rows:
        cells = main_row(w, i)
        assert cells[0].startswith("case "), cells
        assert cells[0] == r["letter"] and cells[1] == r["name"]


# ═════════════════════ C-062 dft 门殿后 ═════════════════════
def test_c062_dft_gate_row_is_last(qapp):
    """受 iddq 门控的信号：dft 页那根门单列一行，且在输入表**末行**。"""
    w = panel()
    an = an_of("wl", "d_wl_rf_lo2g5g_bias_en")
    rows = inputs_table.input_rows(an)
    assert rows[-1]["is_dft_gate"] and not any(r["is_dft_gate"] for r in rows[:-1])
    w.show_signal(an)

    last = len(rows) - 1
    cells = main_row(w, last)
    assert cells[0] == "dft页"
    assert "DFT门(iddq)" in cells[2]
    assert cells[1] == an["dft_gate"]["label"]
    assert w.inputs_view.model().rowCount() == 2 * len(rows)   # 门行也是两行一组


# ═════════════════════ C-222 逻辑展开说明 ═════════════════════
def test_c222_chain_help_text_sits_on_the_title_row(qapp):
    """「逐层展开」标题行右侧的帮助文（从顶层输出往回到源寄存器 · 每层：原式 = 代入真名）。"""
    w = panel()
    assert H.find(w, N.SIDE_CHAIN_TITLE, QtWidgets.QLabel).text() == T.SIDE_CHAIN_TITLE
    help_lbl = H.find(w, N.SIDE_CHAIN_HELP, QtWidgets.QLabel)
    assert help_lbl.text() == T.SIDE_CHAIN_HELP
    assert help_lbl.isVisible()
    # 帮助文在标题行上（与标题同一个父），不是画到链正文里去了
    assert help_lbl.parent() is H.find(w, N.SIDE_CHAIN_TITLE, QtWidgets.QLabel).parent()
    assert T.SIDE_CHAIN_HELP not in chain_text(w)


# ═════════════════════ C-275 猜名区分 ═════════════════════
def test_c275_guessed_names_get_badge_and_amber_marking(qapp):
    """表里查不到、按命名约定猜的名字：标题右角标 + 该行名字淡黄底 + 琥珀虚下划线。"""
    w = panel()
    an = an_of("wl", "d_wl_rf_lp5g_rxrf_lna_lctune")
    rows = inputs_table.input_rows(an)
    guessed = [i for i, r in enumerate(rows) if r["guessed"]]
    trusted = [i for i, r in enumerate(rows) if not r["guessed"]]
    assert guessed and trusted
    w.show_signal(an)

    badge = H.find(w, N.SIDE_INPUTS_GUESS_BADGE, QtWidgets.QLabel)
    assert badge.text() == T.SIDE_INPUTS_GUESS_BADGE and badge.isVisible()
    m = w.inputs_view.model()
    for i in guessed:
        assert m.data(m.index(2 * i, 1), SP.ROLE_GUESSED) is True
        assert color_of(w, 2 * i, 1, Qt.BackgroundRole) == TH.GUESS_BG.lower()
    for i in trusted:
        assert m.data(m.index(2 * i, 1), SP.ROLE_GUESSED) is False
        assert color_of(w, 2 * i, 1, Qt.BackgroundRole) != TH.GUESS_BG.lower()
    # 虚下划线：真把格子画出来数像素——猜名行有琥珀描边，查到的名字那行没有
    assert cell_has_color(w, 2 * guessed[0], 1, TH.AMBER_STROKE)
    assert not cell_has_color(w, 2 * trusted[0], 1, TH.AMBER_STROKE)
    H.shot(w, "side_inputs_guessed")

    # 名字全是查到的信号 → 角标不出现（别让用户以为处处有猜名）
    w.show_signal(an_of("btlp", "d_logic_bt_lp_rx_en"))
    assert not badge.isVisible()


# ═════════════════════ C-282 联动高亮（双向，且必须经 bus）═════════════════════
def test_c282_chain_and_inputs_highlight_each_other_through_the_bus(qapp):
    """点链里的真名 → 输入表同名行高亮；点输入表行 → 链里同名处高亮。两边都只经 `bus`。"""
    b = BUS.HighlightBus()
    p1, p2 = panel(bus=b), panel(bus=b)          # 两块面板共用一根总线 = 「不直连」的证据
    an = an_of("wl", "d_wl_rf_lo2g5g_bias_en")
    p1.show_signal(an)
    p2.show_signal(an)
    rows = inputs_table.input_rows(an)
    target = "d_wl_rf_linectrl_logen_mixer_en"
    want_row = next(i for i, r in enumerate(rows) if BUS.net_key(r["name"]) == target)

    # ① 链 → 输入表（真实鼠标点在那个名字上）
    pt = p1.chain_view.net_point(target)
    assert pt is not None, "链里没有可点的 %s：%s" % (target, p1.chain_view.net_names())
    H.click(p1.chain_view, pt)
    assert (b.current_net, b.current_origin) == (target, "chain")
    assert p1.inputs_view.model().highlighted_rows() == [2 * want_row, 2 * want_row + 1]
    assert p2.inputs_view.model().highlighted_rows() == [2 * want_row, 2 * want_row + 1]
    assert color_of(p2, 2 * want_row, 1, Qt.BackgroundRole) == TH.SELECTED_BG.lower()
    assert any(target in line for line in p1.chain_view.highlighted_lines())
    H.shot(p1, "side_highlight_from_chain")

    # ② 输入表 → 链（真实鼠标点第 0 行）
    H.click_cell(p2.inputs_view, 0, 1)
    first = BUS.net_key(rows[0]["name"])
    assert (b.current_net, b.current_origin) == (first, "inputs")
    assert any(first in line.lower() for line in p1.chain_view.highlighted_lines())
    assert p1.chain_view.highlight() == first
    H.shot(p2, "side_highlight_from_inputs")

    # ③ 拔掉总线 → 另一块面板不再跟着亮（证明联动确实只走 bus，没有直连）
    p2.set_bus(None)
    H.click(p1.chain_view, p1.chain_view.net_point("d_wl_rf_logen_mixer_en_local"))
    assert b.current_net == "d_wl_rf_logen_mixer_en_local"
    assert p2.inputs_view.model().highlight() == first


def test_c282_side_panel_never_imports_sibling_views(qapp):
    """I-18：右栏不 import 任何兄弟视图，当前线网只有 bus 一条路。"""
    import ast

    src = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "dreg_verify", "ui", "side_panel.py")
    with open(src, encoding="utf-8") as f:
        tree = ast.parse(f.read())
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for a in node.names:
                imported.add(a.name)
        elif isinstance(node, ast.Import):
            for a in node.names:
                imported.add(a.name.split(".")[-1])
    for sibling in ("app", "signal_list", "filter_bar", "coverage", "sigflow_view",
                    "main_view", "detail_header", "sv_preview", "truth", "gui", "legacy_gui"):
        assert sibling not in imported, "右栏 import 了兄弟视图 %s" % sibling


# ═════════════════════ I-14 / 通用 ═════════════════════
def test_c056_every_side_object_name_is_present(qapp):
    """I-14：names.py 里 SIDE_* 与右栏分割条的每个名字都能 find 到。"""
    w = panel()
    w.show_signal(an_of("wl", "d_wl_rf_lp5g_rxrf_lna_lctune"))
    for key, value in N.all_names().items():
        if key.startswith("SIDE_") or key == "WIN_SPLIT_CHAIN_INPUTS":
            assert H.find(w, value) is not None, "找不到 %s=%s" % (key, value)


def test_c056_side_panel_size_hint_follows_design(qapp):
    """右栏宽度默认 `theme.SIDE_W`=470，拖拽 clamp 280–900（整栏隐藏由 app 控制）。"""
    w = panel()
    assert w.sizeHint().width() == TH.SIDE_W == 470
    assert w.minimumWidth() == TH.CLAMP_SIDE[0] == 280
    assert w.maximumWidth() == TH.CLAMP_SIDE[1] == 900
    w.setVisible(False)
    assert not w.isVisible()
    w.setVisible(True)


def test_c054_every_mirror_signal_renders_without_crashing(qapp):
    """21 个真信号轮一遍：链区永远有内容（层或状态原因），输入表行数与引擎一致。"""
    w = panel()
    for kind, name, an in all_ans():
        w.show_signal(an)
        rows = inputs_table.input_rows(an)
        blocks = w.chain_view.blocks()
        assert blocks or SP.chain_status(an), "%s/%s 链区两头空" % (kind, name)
        assert chain_text(w).strip(), "%s/%s 链区一片空白" % (kind, name)
        assert w.inputs_view.model().input_count() == len(rows)
