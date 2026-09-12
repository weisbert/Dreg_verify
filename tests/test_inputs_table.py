# -*- coding: utf-8 -*-
"""inputs_table / analysis_norm —— 『驱动明细整族』抽成 Qt-free 模块后的回归测试（GUI v2 阶段 A4c）。

三部分：
  ① 纯函数单测（不起 Qt）：输入信号表的行、每列驱动明细、解析明细文本、术语合规；
  ② 归一化层单测（不起 Qt）：Topout 与页视图两条流水线产出的 an 同构；
  ③ **等价性对照**（起 offscreen Qt）：拿『排查(旧)』真跑出来的 `ti_inputs` 表格
     与列头 tooltip，逐格对上 `inputs_table` 的新函数——差异表见 test 的 docstring。

夹具全部是仓库里的合成镜像（mirror_btlp / mirror_wl），不含任何真实信号名。
"""

import os
import sys

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")   # GUI 对照测试用离屏后端
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import make_mirror_btlp                              # noqa: E402
import make_mirror_excel                             # noqa: E402
from dreg_verify import analysis_norm as AN          # noqa: E402
from dreg_verify import excel_model as M             # noqa: E402
from dreg_verify import inputs_table as IT           # noqa: E402
from dreg_verify import pageviews as P               # noqa: E402
from dreg_verify import resolver as R                # noqa: E402
from dreg_verify import sv_writer as W               # noqa: E402
from dreg_verify import topout as T                  # noqa: E402


# ───────────────────────────── 夹具 ─────────────────────────────
@pytest.fixture(scope="module")
def btlp(tmp_path_factory):
    p = tmp_path_factory.mktemp("it_btlp") / "mirror_btlp_dreg.xlsx"
    make_mirror_btlp.build(str(p))
    return M.load_workbook(str(p))


@pytest.fixture(scope="module")
def wl(tmp_path_factory):
    p = tmp_path_factory.mktemp("it_wl") / "mirror_wl_dreg.xlsx"
    make_mirror_excel.build(str(p))
    return M.load_workbook(str(p))


def _an(wb, name):
    """Topout 流水线 → 归一化分析结果 an。"""
    topo = next(t for t in wb.topout if t.name == name)
    return AN.norm_topout_result(T.analyze_signal(wb, R.Resolver(wb), topo), wb)


_ROW_KEYS = ("letter", "name", "width", "role", "rw", "drive",
             "found_in", "trusted", "note")


class _FakeBinding(object):
    """最小绑定替身——用来钉「表里查到的」vs「按命名约定猜的」这条分界。"""

    def __init__(self, found_in, kind="RO", resolved=True, note=""):
        self.kind = kind
        self.resolved = resolved
        self.found_in = found_in
        self.note = note
        self.base = "d_fake_net"
        self.width = 1
        self.wire_lhs = "d_fake_net"
        self.address = None
        self.reg_lsb = None


def _fake_an(binding):
    g = {"key": "d_fake_net", "rep": "A", "base": "d_fake_net", "label": "d_fake_net",
         "width": 1, "is_control": False, "letters": ["A"]}
    return {"kind": "logic", "status": "ok", "issues": [], "note": "", "name": "d_fake_out",
            "sig": None, "groups": [g], "bindings": {"A": binding},
            "node": None, "expansion": None, "vectors": [], "out_width": 1,
            "chain": [], "dft_gate": None}


# ───────────────────── ① 输入信号表：行的内容 ─────────────────────
def test_input_rows_logic_every_row_has_the_promised_fields(btlp):
    """每行都给齐：字母 → 真名(位宽)、角色、RO/RW、怎么驱动、来源、可信与否。"""
    rows = IT.input_rows(_an(btlp, "d_logic_bt_lp_reserve"))
    assert len(rows) == 4                      # (A?C:B)&(~J) 四个输入
    for r in rows:
        for k in _ROW_KEYS:
            assert k in r, "行缺字段 %s: %r" % (k, r)
        assert r["name"] and r["role"] and r["drive"]
        assert r["rw"] in ("RO", "RW", "mux", "?")
    roles = {r["role"] for r in rows}
    assert "控制/选择位" in roles and "数据位" in roles


def test_input_rows_logic_golden_cells(btlp):
    """金标准 · logic：五列文本逐格钉死（与重构前『排查(旧)』画出来的一模一样）。"""
    rows = IT.input_rows(_an(btlp, "d_logic_bt_lp_reserve"))
    assert [IT.row_cells(r) for r in rows] == [
        ["A", "d_bt_lp_linelocal_mode_ctrl", "控制/选择位", "RW", "RF_WRITE 0x2D bit<<0"],
        ["B", "d_bt_lp_bt_mode_sel", "数据位", "RO", "force ENV_RF.d_bt_lp_bt_mode_sel"],
        ["C", "d_bt_lp_bt_mode_sel_local", "数据位", "RW", "RF_WRITE 0x2D bit<<1"],
        ["D", "d_bt_lp_pll_dig_dft_iddq_mode", "控制/选择位", "RO",
         "force ENV_RF.d_bt_lp_pll_dig_dft_iddq_mode"],
    ]


def test_input_rows_mux_golden_cells(btlp):
    """金标准 · mux：控制三来源(模式位/line/local) + 八个 case 数据寄存器，逐格钉死。"""
    rows = IT.input_rows(_an(btlp, "d_bt_lp_lna_itrim"))
    assert [IT.row_cells(r) for r in rows[:3]] == [
        ["A", "d_bt_lp_linelocal_tsensor_ctrl", "控制·模式位/门控", "RW", "RF_WRITE 0x2D bit<<2"],
        ["B", "d_bt_lp_linectrl_tsensor[3:0]", "控制·line路径(force线控)", "RO",
         "force ENV_RF.d_bt_lp_linectrl_tsensor[3:0]"],
        ["C", "d_bt_lp_tsensor_local[3:0]", "控制·local路径(本地寄存器)", "RW",
         "RF_WRITE 0x32 bit<<0"],
    ]
    assert [IT.row_cells(r) for r in rows[3:]] == [
        ["case 4'b%s" % c, "d_bt_lp_lna_itrim_t%d[3:0]" % (i + 1),
         "数据寄存器(被该case选中)", "RW", "RF_WRITE 0x%X bit<<%d" % (0x60 + i // 4, (i % 4) * 4)]
        for i, c in enumerate(["000x", "001x", "010x", "011x",
                               "100x", "101x", "110x", "111x"])]


def test_input_rows_drive_text_is_actionable(btlp):
    """驱动列必须是能照着做的一句话：RW→RF_WRITE 0x<地址> bit<<<位>；RO→force ENV_RF.<网>。"""
    rows = IT.input_rows(_an(btlp, "d_logic_bt_lp_rx_en"))
    for r in rows:
        if r["rw"] == "RW":
            assert r["drive"].startswith("RF_WRITE 0x") and "bit<<" in r["drive"]
        elif r["rw"] == "RO":
            assert r["drive"].startswith("force ENV_RF.")
    assert any(r["rw"] == "RW" for r in rows) and any(r["rw"] == "RO" for r in rows)


def test_input_rows_order_follows_fortest_group_order(btlp):
    """行序 = an['groups'] 的次序（已由 analysis_norm 排成 for_test 地址序），不另外重排。"""
    an = _an(btlp, "d_logic_bt_lp_reserve")
    rows = IT.input_rows(an)
    assert [r["name"] for r in rows] == [g.get("label", g["base"]) for g in an["groups"]]


def test_input_rows_register_root_is_one_row(btlp):
    """直连寄存器根：一行，直接给 RF_WRITE。"""
    rows = IT.input_rows(_an(btlp, "en_dig_clk"))
    assert len(rows) == 1
    assert rows[0]["rw"] == "RW" and rows[0]["drive"].startswith("RF_WRITE 0x")


def test_input_rows_ro_readback_root_has_no_inputs(btlp):
    """只读回读信号：没有可驱动的输入 → 空表（不许崩、也不许编一行出来）。"""
    assert IT.input_rows(_an(btlp, "pll_lock_indicator")) == []


def test_input_rows_ls_rename_root(btlp):
    """电平移位/改名根（d_en_refbuf → d_en_refbuf_ls）照样展开到源寄存器。"""
    rows = IT.input_rows(_an(btlp, "d_en_refbuf_ls"))
    assert len(rows) == 5
    assert all(r["drive"] and not r["drive"].startswith("✗") for r in rows)


def test_input_rows_mux_covers_ctrl_and_data_ports(btlp):
    """mux 根：控制口与数据口都要在表里，数据口标出它被哪个 case 选中。"""
    rows = IT.input_rows(_an(btlp, "d_bt_lp_lna_itrim"))
    assert len(rows) == 11
    assert any(r["role"].startswith("控制") for r in rows)
    data = [r for r in rows if r["role"].startswith("数据")]
    assert data and all(r["letter"].startswith("case ") for r in data)
    assert all(r["is_control"] for r in rows if r["role"].startswith("控制·"))


def test_input_rows_mux_cascade_lists_upstream_recipe(wl):
    """mux 控制由上游 mux 驱动时：本控制行 + 上游『配方』行（载体寄存器 + 上游控制）都要列。

    §7-3 给了 binding 之后，本控制行的**唯一写法**是照那根衔接网写：『信号』列 = 上游 mux
    的输出网名、『类型』RO、『驱动』`force ENV_RF.<衔接网>`——.sv 里真正会 force 的就是它
    （此前是占位文案「经上游 muxN 输出选路」，用户看不出这一口到底是什么网、也无从核对）。
    「经上游 muxN 选路」退到 note 里保留，别让人以为这一口自己有个寄存器可写。"""
    an = _an(wl, "d_wl_rf_lpf_cmain")
    rows = IT.input_rows(an)
    head, drv = rows[0], an["expansion"]["ctrl_drivers"][0]
    assert drv["source"] == "mux"
    b = drv["binding"]                            # C0-a §7-3 补的上游输出衔接网绑定
    assert "上游mux" in head["role"] and head["is_control"] and head["bold"]
    assert head["name"] == IT.mux_label(b)        # 信号列 = 上游 mux 输出网名（带位宽）
    assert head["rw"] == "RO"
    assert head["drive"].startswith("force ENV_RF.%s" % b.wire_lhs)
    assert "需探针前缀" in head["drive"]
    assert head["found_in"] == "mux-output"
    assert head["needs_prefix"] is True and head["guessed"] is False
    assert "经上游 mux%s 选路" % drv["upstream"].group_no in head["note"]
    assert b.note in head["note"]                 # 解析器的原话也不许丢
    recipe = [r for r in rows if r["role"].startswith("上游mux配方")]
    assert len(recipe) >= 2                       # 载体寄存器 + 至少一个上游控制
    assert any("载体寄存器" in r["role"] for r in recipe)


def test_input_rows_dft_gate_is_last_row(wl):
    """带 iddq 门的信号：门单独一行殿后、字母写 dft页、加粗，角色说清每条测试驱透传值。"""
    for name in ("d_wl_rf_lo2g5g_bias_en", "d_wl_rf_lo2g5g_mixer2g_trim"):
        rows = IT.input_rows(_an(wl, name))
        gate = rows[-1]
        assert gate["is_dft_gate"] and gate["bold"]
        assert gate["letter"] == "dft页"
        assert "DFT门(iddq)" in gate["role"] and "透传" in gate["role"]
        assert gate["drive"].startswith("force ENV_RF.")
        assert sum(1 for r in rows if r["is_dft_gate"]) == 1      # 只出一行门


def test_input_rows_no_gate_when_signal_is_not_gated(btlp):
    assert not any(r["is_dft_gate"] for r in IT.input_rows(_an(btlp, "d_logic_bt_lp_rx_en")))


def test_row_cells_match_input_cols():
    """row_cells 的次序与列名一一对上（GUI 直接 enumerate 着画）。"""
    row = {"letter": "A", "name": "n", "role": "r", "rw": "RO", "drive": "d"}
    assert len(IT.row_cells(row)) == len(IT.INPUT_COLS) == 5
    assert IT.row_cells(row) == ["A", "n", "r", "RO", "d"]


# ────────────── ① 续：表里查到的 vs 按命名约定猜的 ──────────────
def test_trusted_marks_table_hits_only(btlp):
    """镜像夹具里的输入都能在寄存器表里查到 → 全部 trusted。"""
    rows = IT.input_rows(_an(btlp, "d_logic_bt_lp_reserve"))
    assert all(r["trusted"] for r in rows)
    assert all(r["found_in"] in IT.TRUSTED_FOUND_IN for r in rows)


@pytest.mark.parametrize("found_in", ["wire", "prefixed-wire", "needs-prefix", "logic"])
def test_untrusted_when_name_is_only_a_convention(found_in):
    """表里查不到、靠命名约定推出来的网 → trusted=False，且来源说明要点破『名字对不上就找不到』。"""
    rows = IT.input_rows(_fake_an(_FakeBinding(found_in)))
    assert rows[0]["trusted"] is False
    assert rows[0]["found_in_text"]


def test_needs_prefix_drive_carries_the_warning():
    rows = IT.input_rows(_fake_an(_FakeBinding("needs-prefix")))
    assert "需探针前缀" in rows[0]["drive"]


@pytest.mark.contract("C-058", "C-059")
def test_c058_c059_input_rows_flags(btlp):
    """N10：把「需前缀」与「猜名」拆成两个布尔——Design 只有一个『猜名』标记，后端分得更细。

    needs_prefix = 网在、但埋子模块，不配前缀 force 必被跳过；
    guessed      = 名字本身是按命名约定猜的（不在寄存器表里查到），可能根本没这根网。
    两者互斥；都为假 = 真查到的可信名。binding_meta 不变（驱动文案一字不动）。"""
    for found_in, want in (("tmm", (False, False)), ("regmap", (False, False)),
                           ("needs-prefix", (True, False)), ("mux-output", (True, False)),
                           ("wire", (False, True)), ("prefixed-wire", (False, True)),
                           ("logic", (False, True)), ("", (False, False))):
        r = IT.input_rows(_fake_an(_FakeBinding(found_in)))[0]
        assert (r["needs_prefix"], r["guessed"]) == want, found_in
        assert not (r["needs_prefix"] and r["guessed"]), found_in      # 互斥
        assert r["trusted"] == (found_in in IT.TRUSTED_FOUND_IN)       # 老字段不受影响
    # 没有绑定的行（mux 级联控制占位行）也带这两个键，且都为假——界面不必到处 .get 兜底
    r = IT._row(letter="A", name="x", role="控制", b=None)
    assert r["needs_prefix"] is False and r["guessed"] is False
    # 真表：镜像夹具的输入全在寄存器表里查到 → 两个标记都不该亮
    rows = IT.input_rows(_an(btlp, "d_logic_bt_lp_reserve"))
    assert not any(r["needs_prefix"] or r["guessed"] for r in rows)
    # 键齐全（v2 输入表逐行读它们画橙字/⚠）
    assert all({"needs_prefix", "guessed"} <= set(r) for r in rows)


@pytest.mark.contract("C-074")
def test_c074_vheader_display_format(btlp):
    """真值表冻结列行标签的 Design 格式 `<真名>　(角色 · 端口)`；vheader_short 原样不动。"""
    an = _an(btlp, "d_logic_bt_lp_reserve")
    g = an["groups"][0]
    assert IT.vheader_display(g) == "d_bt_lp_linelocal_mode_ctrl　(控制位 · A)"
    assert IT.vheader_short(g) == "d_bt_lp_linelocal_mode_ctrl (控制)"      # 旧格式一字不动
    assert "　(" in IT.vheader_display(g)                                   # 全角空格分隔
    data_g = next(gg for gg in an["groups"] if not gg.get("is_control"))
    assert IT.vheader_display(data_g).endswith("(数据位 · %s)" % IT.group_letters(data_g))

    # mux：端口写成 mux<组号>.ctrl<i> / mux<组号>.d<i>（与 mux_gen 合成子树挂的来源标签同名）
    man = _an(btlp, "d_bt_lp_lna_itrim")
    no = man["sig"].group_no
    rows = IT.input_rows(man)
    ctrl0 = rows[0]
    assert IT.vheader_display(ctrl0, man) == "%s　(控制位 · mux%s.ctrl0)" % (ctrl0["name"], no)
    data0 = next(r for r in rows if r["role"].startswith("数据寄存器"))
    assert IT.vheader_display(data0, man) == "%s　(数据位 · mux%s.d0)" % (data0["name"], no)
    # 不给 an → 定不出端口，退回字母（case 值），但格式与角色不变
    assert IT.vheader_display(data0).startswith("%s　(数据位 · case " % data0["name"])

    # DFT 门行：角色写 DFT 门(iddq)
    gate = IT.dft_gate_row({"label": "d_fake_gate", "binding": _FakeBinding("tmm"),
                            "transp": 0, "width": 1, "key": "__dft_gate__"})
    assert IT.vheader_display(gate).startswith("d_fake_gate　(DFT 门(iddq)")
    # 空输入不炸（界面在没选中信号时也会调）
    assert IT.vheader_display({}) == "?　(数据位)"


@pytest.mark.contract("C-060")
def test_c060_mux_ctrl_binding_and_missing_keys():
    """§7-3/4 消费：mux 级联控制口有 Binding 就用它出类型/驱动；used_vars 漏掉的控制键殿后补行。

    两个键都是 C0-a 在引擎侧补的 additive 键——这里用手工 an 覆盖「有」与「没有」两种，
    合并前后行为都钉住（没有 → 走旧的占位分支，一字不变）。"""
    class _Up(object):
        group_no = 7

    exp_bindings = {"m7.B": _FakeBinding("mux-output")}
    drv_no_binding = {"source": "mux", "letter": "B", "base": "d_up_out",
                      "upstream": _Up(), "recipe": {}, "keys": [], "bindings": {}}
    old = IT.mux_ctrl_rows(drv_no_binding, {"bindings": exp_bindings})
    assert old[0]["rw"] == "mux" and old[0]["drive"] == "经上游 mux7 输出选路"
    assert old[0]["name"] == "d_up_out" and old[0]["needs_prefix"] is False

    drv = dict(drv_no_binding, binding=_FakeBinding("mux-output", note="上游 mux 输出"), key="m7.B")
    new = IT.mux_ctrl_rows(drv, {"bindings": exp_bindings})
    assert new[0]["role"] == old[0]["role"] == "控制(经上游mux7驱动)"      # 角色文案不变
    assert new[0]["rw"] == "RO" and new[0]["drive"].startswith("force ENV_RF.")
    assert "需探针前缀" in new[0]["drive"] and new[0]["needs_prefix"] is True
    assert new[0]["key"] == "m7.B" and new[0]["is_control"] and new[0]["bold"]
    # 占位文案让位给真网名之后，「经上游 muxN 选路」退到 note 里保留（解析器原话接在后面）
    assert new[0]["note"] == "经上游 mux7 选路；上游 mux 输出"
    assert IT.mux_ctrl_rows(dict(drv, binding=_FakeBinding("mux-output")),
                            {"bindings": exp_bindings})[0]["note"] == "经上游 mux7 选路"

    # §7-4：an["ctrl_keys_missing"] 的键殿后补行（角色「控制(line 路径，未展开)」）
    class _Grp(object):
        group_no = 7
        cases = []

    base_an = {"kind": "mux", "status": "ok", "sig": _Grp(), "dft_gate": None,
               "expansion": {"bindings": {"c:0": _FakeBinding("tmm")}, "used_vars": ["c:0"],
                             "data_keys": [], "ctrl_drivers": []}}
    assert IT.input_rows(base_an) == []                                  # 没这个键 → 一行都不补
    rows = IT.input_rows(dict(base_an, ctrl_keys_missing=["c:0"]))
    assert len(rows) == 1
    assert rows[0]["role"] == "控制(line 路径，未展开)" and rows[0]["key"] == "c:0"
    assert rows[0]["is_control"] and rows[0]["rw"] == "RO"
    # 已经有行的键不重复补
    dup_an = dict(base_an, ctrl_keys_missing=["d:0"])
    dup_an["expansion"] = dict(base_an["expansion"], data_keys=["d:0"],
                              bindings={"d:0": _FakeBinding("tmm")})
    assert len(IT.input_rows(dup_an)) == 1


def test_unresolved_binding_says_so_instead_of_faking_a_net():
    rows = IT.input_rows(_fake_an(_FakeBinding("wire", resolved=False, note="ENV_RF 探不到")))
    assert rows[0]["drive"].startswith("✗未解析")
    assert "ENV_RF 探不到" in rows[0]["drive"]
    assert rows[0]["resolved"] is False


def test_binding_meta_without_binding_is_not_a_crash():
    assert IT.binding_meta(None) == ("?", "(无绑定)")


# ───────────────────── ② 每列驱动明细 ─────────────────────
def test_column_drives_lists_every_statement(btlp):
    """某测试列实际下的每条语句：先 RF_WRITE（按地址）再 force，一句一行，能照抄进 .sv。"""
    an = _an(btlp, "d_logic_bt_lp_rx_en")
    lines = IT.column_drives(an, 0)
    assert lines
    bind, used = IT.drive_ctx(an)
    forces, writes, _u = W.compute_drives(an["vectors"][0], bind, used)
    n_extra = len(getattr(an["vectors"][0], "extra_forces", None) or [])
    assert len(lines) == len(forces) + len(writes) + n_extra
    assert sum(1 for x in lines if x.startswith("`RF_WRITE(")) == len(writes)
    assert sum(1 for x in lines if x.startswith("force `ENV_RF.")) == len(forces) + n_extra
    # RF_WRITE 在 force 前面（与产出 .sv 的次序一致）
    idx = [0 if x.startswith("`RF_WRITE(") else 1 for x in lines]
    assert idx == sorted(idx)


def test_column_drives_out_of_range_is_empty(btlp):
    an = _an(btlp, "d_logic_bt_lp_rx_en")
    assert IT.column_drives(an, -1) == [] and IT.column_drives(an, 9999) == []
    assert IT.column_drives(None, 0) == []


def test_column_drives_includes_the_iddq_gate_force(wl):
    """被 iddq 门控的信号：每条功能测试都显式 force 门到透传值——明细里必须看得见这一条。"""
    an = _an(wl, "d_wl_rf_lo2g5g_bias_en")
    assert any("iddq 门" in x for x in IT.column_drives(an, 0))


def test_column_drives_covers_mux(wl):
    an = _an(wl, "d_wl_rf_lo2g5g_mixer2g_trim")
    lines = IT.column_drives(an, 0)
    assert lines and any(x.startswith("`RF_WRITE(") for x in lines)


def test_drive_pair_is_the_compact_form(btlp):
    """drive_pair = 旧门面 tooltip/CSV 的紧凑两串，与 compute_drives 同源。"""
    an = _an(btlp, "d_logic_bt_lp_lna_agc")
    bind, used = IT.drive_ctx(an)
    fs, ws = IT.drive_pair(an["vectors"][0], bind, used)
    forces, writes, _u = W.compute_drives(an["vectors"][0], bind, used)
    assert fs == "; ".join("%s=%s" % (f["wire"], f["hex"]) for f in forces)
    assert ws == "; ".join("%s=%s" % (w["addr"], w["hex"]) for w in writes)
    assert IT.drive_pair(None, bind, used) == ("", "")


def test_drive_tips_render_empty_as_explicit_none():
    assert IT.cell_drive_tip("", "") == ""
    assert IT.cell_drive_tip("a", "") == "\nforce: a"
    assert IT.header_drive_tip("", "") == "force: (无)\nRF_WRITE: (无)"


# ───────────────────── ③ 解析明细文本 ─────────────────────
# §8.6 禁止在界面上裸用的词（tmm / regmap 允许「就地解释」，单独查）
_BANNED_BARE = ["cone", "prefixed-wire", "wire 兜底", "wire兜底", "记账", "claims"]


def _all_details(wb):
    return [IT.resolve_detail(_an(wb, t.name)) for t in wb.topout]


def test_resolve_detail_has_the_four_headline_facts(wl):
    """信号 / 逻辑式 / 状态 / 断言探针 —— 四件事一眼可见。"""
    txt = IT.resolve_detail(_an(wl, "d_wl_rf_lo2g5g_bias_en"))
    assert txt.startswith("信号 d_wl_rf_lo2g5g_bias_en")
    assert "逻辑式" in txt and "状态" in txt and "断言探针" in txt
    assert "`ENV_RF." in txt


def test_resolve_detail_lists_every_input_with_its_drive(wl):
    an = _an(wl, "d_wl_rf_lo2g5g_bias_en")
    txt = IT.resolve_detail(an)
    rows = IT.input_rows(an)
    assert "输入逐条解析（%d 条）" % len(rows) in txt
    for r in rows:
        assert r["name"] in txt
        assert r["drive"] in txt


def test_resolve_detail_gives_the_three_troubleshooting_steps(btlp):
    txt = IT.resolve_detail(_an(btlp, "d_logic_bt_lp_rx_en"))
    assert IT.CUVUNF_FIRST in txt
    assert "nets.txt" in txt and "scan_rtl" in txt
    for step in ("1.", "2.", "3."):
        assert step in txt


def test_resolve_detail_flags_the_guessed_names_as_prime_suspects():
    txt = IT.resolve_detail(_fake_an(_FakeBinding("wire")))
    assert "⚠ 猜的" in txt
    assert "最可疑" in txt and "d_fake_net" in txt


def test_resolve_detail_says_so_when_nothing_is_guessed(btlp):
    txt = IT.resolve_detail(_an(btlp, "d_logic_bt_lp_reserve"))
    assert "✔ 查到的" in txt
    assert "输入全是从寄存器表里查到的" in txt


def test_resolve_detail_handles_a_signal_with_no_inputs(btlp):
    txt = IT.resolve_detail(_an(btlp, "pll_lock_indicator"))
    assert "输入逐条解析（0 条）" in txt
    assert "只读回读" in txt
    assert "没有要 force 的输入" in txt          # 不许反过来说『输入全查到了』
    assert IT.resolve_detail(None) == ""


def test_resolve_detail_names_the_upstream_mux_net_instead_of_blaming_the_port(wl):
    """上游 mux 驱动的那一口，§7-3 之后**有网了**（上游 mux 的输出衔接网）：

    明细要把那根网的名字亮出来、标成『⚠ 要配层级前缀』，并单独列一行告诉用户「配好前缀就
    force 得到」。它【不】算『最可疑』——名字不是猜的，只是埋在子模块里（N10 把「猜名」与
    「缺前缀」拆成了两件事，明细不许再合回去把用户支去改 Excel 名字）。"""
    an = _an(wl, "d_wl_rf_lpf_cmain")
    txt = IT.resolve_detail(an)
    head = IT.input_rows(an)[0]
    assert head["drive"] in txt                              # force ENV_RF.<衔接网>
    assert "来源: ⚠ 要配层级前缀" in txt
    assert "⚠ 猜的" not in txt
    assert "经上游 mux%s 选路" % an["expansion"]["ctrl_drivers"][0]["upstream"].group_no in txt
    assert "埋在子模块里的衔接网" in txt and head["name"] in txt
    assert "最可疑" not in txt                                # 名字不是猜的 → 不进嫌疑名单
    assert "输入全是从寄存器表里查到的" not in txt              # 但也绝不能说「全查到了」


def test_resolve_detail_still_explains_a_port_with_no_net_at_all():
    """真·连网都没有的那一口（旧展开数据没有 §7-3 binding）：仍要说清「这一口由上游 mux 选路
    决定、本身没有单独的网可 force」，并且照样不算『最可疑』——别让用户以为是漏显示。"""
    class _Up(object):
        group_no = 7

    an = {"kind": "mux", "status": "ok", "issues": [], "note": "", "name": "d_x", "sig": None,
          "dft_gate": None, "vectors": [], "out_width": 1,
          "expansion": {"bindings": {}, "used_vars": [], "data_keys": [],
                        "ctrl_drivers": [{"source": "mux", "letter": "B", "base": "d_up_out",
                                          "upstream": _Up(), "recipe": {}}]}}
    row = IT.input_rows(an)[0]
    assert row["rw"] == "mux" and row["drive"] == "经上游 mux7 输出选路"
    txt = IT.resolve_detail(an)
    assert "由上游 mux 选路决定" in txt
    assert "最可疑" not in txt and "埋在子模块里的衔接网" not in txt


@pytest.mark.parametrize("fixture_name", ["btlp", "wl"])
def test_resolve_detail_never_leaks_a_banned_bare_term(fixture_name, request):
    """术语闸门（Design prompt §8.6）：禁用词不许裸露；CUVUNF 只能出现在展开说法里；
    tmm / regmap 必须就地解释成『寄存器定义表 / 寄存器地址映射表』。"""
    wb = request.getfixturevalue(fixture_name)
    for txt in _all_details(wb):
        low = txt.lower()
        for bad in _BANNED_BARE:
            assert bad not in low, "解析明细裸用了禁用词 %r" % bad
        if "cuvunf" in low:
            assert IT.CUVUNF_FIRST in txt, "CUVUNF 必须展开成『仿真器找不到这根网』"
            assert low.count("cuvunf") == txt.count(IT.CUVUNF_FIRST)
        if "tmm" in low:
            assert "寄存器定义表" in txt
        if "regmap" in low:
            assert "寄存器地址映射表" in txt


def test_scrub_terms_rewrites_backend_jargon():
    """后端 note 里那句『RO 回读(regmap)…无 cone，跳过+记账』不许原样上界面。"""
    src = "RO 回读(regmap)——模拟/FSM 状态、非寄存器组合函数，无 cone，跳过+记账"
    got = IT.scrub_terms(src)
    for bad in ("regmap", "cone", "记账"):
        assert bad not in got
    assert "寄存器地址映射表" in got and "记在清单里" in got
    assert IT.scrub_terms("") == "" and IT.scrub_terms(None) == ""


def test_scrub_terms_keeps_signal_names_intact():
    """替换只动术语，不许改信号名——名字被改掉就没法拿去 nets.txt 里搜了。"""
    assert IT.scrub_terms("d_tmm_cone_regmap_x 没解析到") == "d_tmm_cone_regmap_x 没解析到"
    assert IT.scrub_terms("force ENV_RF.d_wire_claims_net") == "force ENV_RF.d_wire_claims_net"


def test_scrub_terms_expands_the_error_code():
    assert "CUVUNF" not in IT.scrub_terms("没配前缀就 force 必 CUVUNF，所以跳过")
    assert "找不到这根网" in IT.scrub_terms("elaboration 会 CUVUNF")


def test_found_in_text_covers_every_label_key():
    """两张来源映射表的键必须对齐——否则 v2 会退回显示机器键。"""
    assert set(IT.FOUND_IN_LABEL) <= set(IT.FOUND_IN_TEXT)


# ───────────────── ④ 归一化层：两条流水线的 an 同构 ─────────────────
def test_topout_and_page_an_have_the_same_shape(btlp):
    """Topout 与页本地两条流水线产出的 an 字段集一致——v2 与 SignalView 才敢只认一种。"""
    an_t = _an(btlp, "d_logic_bt_lp_reserve")
    sig = next(s for s in P.page_signals(btlp, "logic")
               if s.out_name == "d_logic_bt_lp_reserve")
    res = P.analyze_page_signal(btlp, P._page_resolver(btlp, None, None), sig, "logic")
    an_p = AN.norm_page_result(res, btlp)
    assert set(an_t) == set(an_p)
    assert [r["name"] for r in IT.input_rows(an_t)] == [r["name"] for r in IT.input_rows(an_p)]


def test_norm_carries_the_gate_binding(wl):
    """an['dft_gate'] 带上绑定对象——没有它，输入表拼不出门的 RO/RW 与 force 串。"""
    gate = _an(wl, "d_wl_rf_lo2g5g_bias_en")["dft_gate"]
    assert gate and gate["binding"] is not None
    assert gate["key"] == "__dft_gate__" and gate["width"] == 1
    assert gate["transp"] in (0, 1)
    pin = IT.gate_pin(_an(wl, "d_wl_rf_lo2g5g_bias_en"))
    assert pin and pin[0] == gate["label"] and pin[1] == gate["transp"]
    assert IT.gate_pin({"dft_gate": None}) is None


def test_subst_expr_substitutes_letters(btlp):
    assert AN.subst_expr("A?C:B", {"A": "sel", "B": "lo", "C": "hi"}) == "sel?hi:lo"
    assert AN.subst_expr("", {}) == ""
    assert AN.subst_expr("AB", {"A": "x"}) == "AB"      # 只换独立的单字母


def test_order_groups_fortest_is_a_pure_reorder(btlp):
    an = _an(btlp, "d_logic_bt_lp_reserve")
    got = AN.order_groups_fortest(an["groups"], an["bindings"], btlp, "d_logic_bt_lp_reserve")
    assert sorted(id(g) for g in got) == sorted(id(g) for g in an["groups"])
    assert AN.order_groups_fortest(an["groups"], an["bindings"], None, "x") is an["groups"]
    assert AN.order_groups_fortest([], {}, btlp, "x") == []


# ══════════════════ ⑤ 等价性对照：起 offscreen 旧 GUI ══════════════════
@pytest.fixture(scope="module")
def gui_app():
    pytest.importorskip("PySide6")
    from PySide6 import QtWidgets
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture(autouse=True)
def _isolate_gui_settings(monkeypatch, tmp_path):
    """别碰用户真实的 ~/.dreg_verify_*.json。"""
    pytest.importorskip("PySide6")
    from dreg_verify import gui as G
    monkeypatch.setattr(G, "SETTINGS_PATH", str(tmp_path / "gui_settings.json"))
    monkeypatch.setattr(G, "EDITS_PATH", str(tmp_path / "edits.json"))


@pytest.fixture(scope="module")
def btlp_path(tmp_path_factory):
    p = tmp_path_factory.mktemp("it_gui_btlp") / "mirror_btlp_dreg.xlsx"
    make_mirror_btlp.build(str(p))
    return str(p)


@pytest.fixture(scope="module")
def wl_path(tmp_path_factory):
    p = tmp_path_factory.mktemp("it_gui_wl") / "mirror_wl_dreg.xlsx"
    make_mirror_excel.build(str(p))
    return str(p)


def _open(gui_app, path):
    from dreg_verify import gui as G
    w = G.MainWindow()
    w.path_edit.setText(path)
    w.on_load()
    return w


def _topout_ans(w, mode="min"):
    """Topout 清单 → {源信号名(小写): an}（an 的 sig 就是 logic/mux 页那一行的对象）。"""
    prov = w.topout_view.provider
    out = {}
    for t in (w.wb.topout or []):
        an = prov.analyze(t.name, mode, w.max_tests.value(), False)
        nm = getattr((an or {}).get("sig"), "out_name", None)
        if nm:
            out.setdefault(nm.lower(), an)
    return out


def _legacy_an(w, sig):
    """用**旧门面自己那套对象**（_expand_sig 出来的 groups/bindings，或 mux 展开 exp，
    门由 GUI 的 resolver 现场解）拼一份 an 同形 dict —— 对照组，不经 Topout 流水线。"""
    from dreg_verify import excel_model as EM
    if isinstance(sig, EM.MuxGroup):
        return {"kind": "mux", "sig": sig, "expansion": w._ti_mux_exp,
                "dft_gate": w._dft_gate_entry(sig.out_base)}
    return {"kind": "logic", "sig": sig, "groups": w._ti_groups, "bindings": w._ti_bindings,
            "dft_gate": w._dft_gate_entry(sig.out_base)}


def _legacy_input_cells(w):
    return [[(w.ti_inputs.item(i, c).text() if w.ti_inputs.item(i, c) else None)
             for c in range(len(IT.INPUT_COLS))]
            for i in range(w.ti_inputs.rowCount())]


def _tip_drives(tip):
    fs = ws = None
    for ln in (tip or "").split("\n"):
        if ln.startswith("force: "):
            fs = ln[len("force: "):]
        elif ln.startswith("RF_WRITE: "):
            ws = ln[len("RF_WRITE: "):]
    return fs, ws


@pytest.mark.parametrize("fixture_name,least", [("btlp_path", 9), ("wl_path", 9)])
def test_legacy_inputs_table_equals_input_rows(gui_app, request, fixture_name, least):
    """**等价性对照 · 输入信号表**：offscreen 起『排查(旧)』，对每个既在旧 logic/mux 表、
    又在 Topout 清单里的信号，逐格比较三样东西：

      ① 『排查(旧)』真画出来的 `ti_inputs` 五列文本（走 `_expand_sig` 那条老路：
         LogicSignal + bindings + groups / MuxGroup + exp，门由 GUI 的 resolver 现场解）；
      ② 拿**旧对象**直接喂 `input_rows` 的结果；
      ③ 拿 **Topout 的 an** 喂 `input_rows` 的结果。

    ①==② 证明 gui 的委托没有画错；②==③ 才是真正要证的那句：**an 携带的信息足以复刻
    旧门面的驱动明细**——两边的输入分组、绑定、mux 展开、门，全都对得上。
    （逐格文本本身另有金标准单测钉死，见 test_input_rows_*_golden_cells。）

    实测：两张镜像各 9 个信号（logic / mux / 带 iddq 门 / 上游 mux 级联都覆盖到），
    **五列文本零差异**。以下是仍存在、但在镜像夹具上不显形的【允许差异】：

    | # | 场景 | 『排查(旧)』 | `input_rows(an)` | 根因 |
    |---|------|-------------|------------------|------|
    | 1 | iddq 门解析不了 / 不是 RO | 照样出一行、驱动列标红『✗未解析』 | 不出这一行 | an 的门由 `generator.pin_dft_gate` 钉，它过滤掉解析不了/非 RO 的门 |
    | 2 | iddq 门已是本信号的显式输入 | 重复出一行 | 只出显式输入那一行 | 同上，`pin_dft_gate` 带 `input_bases` 去重 |
    | 3 | 断言探针名 | 带会话里配的层级前缀 | 不带前缀 | an 里没有 probe_prefix 字段（v2 待补） |
    | 4 | 测试列数 | 旧编辑器的正向列 | 末尾多一条 iddq 漏电态拍 | Topout 流水线自动补 DFT 拍 |

    1/2 只在门坏掉或与显式输入撞名时出现，镜像夹具两者都不触发，故本测试断言严格相等。
    3/4 不影响输入信号表（只影响解析明细的探针行与列数），见下一个测试。
    """
    from dreg_verify import gui as G
    w = _open(gui_app, request.getfixturevalue(fixture_name))
    try:
        ans = _topout_ans(w)
        compared = 0
        for r in range(w.table.rowCount()):
            sig = w.signals[w._idx_of_row(r)]
            an = ans.get(sig.out_name.lower())
            if an is None:
                continue
            w.on_row_focus(r, G.COL_K, -1, -1)
            painted = _legacy_input_cells(w)
            from_an = [IT.row_cells(x) for x in IT.input_rows(an)]
            from_legacy_objects = [IT.row_cells(x) for x in IT.input_rows(_legacy_an(w, sig))]
            assert painted, "%s 的输入信号表不该是空的" % sig.out_name
            assert painted == from_legacy_objects, "委托画错了：%s" % sig.out_name
            assert from_legacy_objects == from_an, \
                "an 复刻不出旧对象那份驱动明细：%s" % sig.out_name
            compared += 1
        assert compared >= least, "对照到的信号太少(%d)，夹具或匹配规则退化了" % compared
    finally:
        w.close()


@pytest.mark.parametrize("fixture_name,least", [("btlp_path", 20), ("wl_path", 14)])
def test_legacy_column_tooltip_equals_drive_pair(gui_app, request, fixture_name, least):
    """**等价性对照 · 每列驱动**：旧门面列头 tooltip 里的 `force:` / `RF_WRITE:` 两行，
    与 `inputs_table.drive_pair(an['vectors'][c], ...)` 逐列相等。

    只比两边都有的列：Topout 的 an 末尾会多一条 iddq 漏电态拍（旧编辑器没有），
    mux 信号的旧编辑器列头不带驱动 tooltip（跳过，mux 的驱动走 CSV 那条路）。
    `column_drives` 比 tooltip 多给一条 iddq 门的 force——那是 extra_forces，
    产出 .sv 里真有，旧 tooltip 漏了（见 test_column_drives_includes_the_iddq_gate_force）。
    """
    from dreg_verify import gui as G
    w = _open(gui_app, request.getfixturevalue(fixture_name))
    try:
        ans = _topout_ans(w)
        compared = 0
        for r in range(w.table.rowCount()):
            sig = w.signals[w._idx_of_row(r)]
            an = ans.get(sig.out_name.lower())
            if an is None:
                continue
            w.on_row_focus(r, G.COL_K, -1, -1)
            bind, used = IT.drive_ctx(an)
            for c in range(min(w.ti_table.columnCount(), len(an["vectors"]))):
                hh = w.ti_table.horizontalHeaderItem(c)
                if hh is None:
                    continue
                fs, ws = _tip_drives(hh.toolTip())
                if fs is None and ws is None:
                    continue                       # mux 列头不带驱动 tooltip
                want = IT.header_drive_tip(*IT.drive_pair(an["vectors"][c], bind, used))
                assert "force: %s\nRF_WRITE: %s" % (fs, ws) == want, \
                    "第 %d 列驱动对不上：%s" % (c, sig.out_name)
                compared += 1
        assert compared >= least, "对照到的列太少(%d)" % compared
    finally:
        w.close()


def test_legacy_resolve_detail_still_renders_the_old_text(gui_app, btlp_path):
    """左下『解析明细』搬进模块后，旧门面的文案逐字不变（信号/表达式/状态/断言/提示 五段）。"""
    from dreg_verify import gui as G
    w = _open(gui_app, btlp_path)
    try:
        w.on_row_focus(0, G.COL_K, -1, -1)
        txt = w.detail.toPlainText()
        assert txt.startswith("信号: ")
        for seg in ("表达式: ", "状态: ", "断言: assert (", "提示: "):
            assert seg in txt
        sig = w.signals[w._idx_of_row(0)]
        a = w._analysis[w._idx_of_row(0)]
        assert txt == IT.legacy_resolve_detail(sig, a, G.STATUS_LABEL)
    finally:
        w.close()


@pytest.mark.parametrize("fixture_name", ["btlp_path", "wl_path"])
def test_signalview_an_feeds_input_rows(gui_app, request, fixture_name):
    """**新门面 smoke**：SignalView 每个信号的 an 喂 `input_rows` 都不抛错；
    非 mux 信号的行数 == 真值表的输入行数；mux 至少给齐每个输入（上游配方会多列几行）。"""
    w = _open(gui_app, request.getfixturevalue(fixture_name))
    try:
        v = w.topout_view
        assert v.models
        for m in v.models:
            v._load_signal(m["name"])
            an = v.cur_an
            assert an is not None, m["name"]
            rows = IT.input_rows(an)
            for r in rows:
                for k in _ROW_KEYS:
                    assert k in r
            if an["kind"] == "mux":
                assert len(rows) >= len(an["expansion"]["used_vars"])
            else:
                assert len(rows) == len(v.e_inputs), m["name"]
            assert IT.resolve_detail(an)          # 明细文本也得出得来
    finally:
        w.close()
