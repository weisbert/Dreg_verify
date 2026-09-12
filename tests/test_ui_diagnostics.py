# -*- coding: utf-8 -*-
"""GUI v2 C4-b：诊断抽屉 `ui/diagnostics.py` 的契约测试（附录 A「DIAG」25 条 + C-302，
每条至少一个测试名含 ID），外加不变量 I-04 / I-11。

口径（与 `test_ui_side_panel.py` / `test_ui_signal_list.py` 一致）：
  · offscreen 起**独立** `DiagnosticsDrawer`（不起 MainWindow）；
  · state 是**真** `ui.state.WorkbenchState` + 真 mirror 表（`persist` 的两个路径指到 tmp，
    绝不碰用户真机那份）—— 「改完名单 Topout 与页视图都真变了」这种事只有真 state 说了算；
  · 文件框 / QMessageBox 一律走 `ui_harness.auto_dialogs`（**不改 ui_harness**，
    要什么答案就在 `answers` 里给）；
  · 编辑框一律 `setPlainText` / `H.paste`，绝不 `type_text`（PySide6 6.11 上非 ASCII 与
    换行会崩进程）；QCheckBox 点指示器。
"""

import json
import os

import pytest

import ui_harness as H

pytest.importorskip("PySide6")

from PySide6 import QtCore, QtWidgets                      # noqa: E402

from dreg_verify import generator, inputs_table as IT, session as S   # noqa: E402
from dreg_verify.ui import diagnostics as D                # noqa: E402
from dreg_verify.ui import names as N                      # noqa: E402
from dreg_verify.ui import persist as P                    # noqa: E402
from dreg_verify.ui import state as ST                     # noqa: E402
from dreg_verify.ui import terms as T                      # noqa: E402
from dreg_verify.ui import theme as TH                     # noqa: E402

Qt = QtCore.Qt

_ALIVE = []                 # Qt 父子链之外，Python 侧也留一份，免得被 GC 掉

#: 删除安全审计 §3.2-A 的样例桶（**全部用 mirror 的名字**）：
#: 一条正向手填期望 + 一条负向（`wrong_value: 0` —— C-301 的「错值 = 0」必须活下来）
LEGACY_ROWS = [
    {"base_values": {"d_bt_lp_linelocal_mode_ctrl": 0, "d_bt_lp_linectrl_rx_en": 1,
                     "d_bt_lp_rx_en_local": 1, "d_bt_lp_pll_dig_dft_iddq_mode": 0},
     "kind": "pos", "note": "样例：手填期望", "designer_expected": 1},
    {"base_values": {"d_bt_lp_linelocal_mode_ctrl": 0, "d_bt_lp_linectrl_rx_en": 1,
                     "d_bt_lp_rx_en_local": 1, "d_bt_lp_pll_dig_dft_iddq_mode": 0},
     "kind": "neg", "wrong_value": 0, "name": "T0_NEG", "user_added": True,
     "note": "样例：手填期望"},
]

LOGIC_SIG = "d_logic_bt_lp_rx_en"          # btlp mirror 的 logic 根
MUX_SIG = "d_bt_lp_lna_itrim[3:0]"         # btlp mirror 的 mux 组（含位宽切片，legacy 的键形状）
GHOST = "d_bt_lp_gone_from_table"          # 旧桶里有、当前表里没有的名字


# ─────────────────────────── 夹具 ───────────────────────────
@pytest.fixture(scope="module")
def qapp():
    return H.app()


@pytest.fixture(autouse=True)
def iso(monkeypatch, tmp_path):
    """settings / edits 两个路径指到 tmp（I-08：pytest 下绝不写用户真机那份）。"""
    monkeypatch.setattr(P, "SETTINGS_PATH", str(tmp_path / "gui_settings.json"))
    monkeypatch.setattr(P, "EDITS_PATH", str(tmp_path / "edits.json"))
    return tmp_path


def make_state(kind="btlp", scopes=("topout",)):
    """真 `WorkbenchState` + 真 mirror；清单用**骨架**模型（便宜，且清单内容不是本波的被测物）。"""
    st = ST.WorkbenchState()
    assert st.load(H.mirror_path(kind)), "mirror %s 载不进来" % kind
    for vid in scopes:
        prov = st.provider(vid)
        if prov is not None and prov.has_page():
            st.set_models(vid, prov.skeleton_models())
    _ALIVE.append(st)
    return st


def drawer(st=None, show=True):
    d = D.DiagnosticsDrawer(state=st)
    d.resize(TH.DIAG_W, 1000)
    if show:
        d.show()
    _ALIVE.append(d)
    return d


def editor(cls, st):
    """起一个编辑器并 `show()`（**不 exec**）——子控件的 `isVisible()` 只有窗显示了才作数。"""
    dlg = cls(st)
    _ALIVE.append(dlg)
    dlg.show()
    H.app().processEvents()
    return dlg


def write_legacy_bucket(path, rows=None, extra=None):
    """往（已被指到 tmp 的）edits 文件里写一份 legacy 桶，返回写下去的原始 JSON 文本。"""
    bucket = {
        "edits": {LOGIC_SIG: list(rows if rows is not None else LEGACY_ROWS),
                  GHOST: [{"base_values": {"x": 0}, "kind": "pos"}]},
        "neg_only": {LOGIC_SIG: "first"},
        "mux_expected": {MUX_SIG: {"0": 10}},
        "mux_neg": [MUX_SIG],
        "signals_checked": [LOGIC_SIG],
    }
    bucket.update(extra or {})
    blob = {str(path): bucket}
    text = json.dumps(blob, ensure_ascii=False, indent=2)
    with open(P.EDITS_PATH, "w", encoding="utf-8") as f:
        f.write(text)
    return text


def click_check(chk):
    """QCheckBox 要点**指示器**（点文字区在某些风格下不切换）。"""
    H.click(chk, QtCore.QPoint(8, chk.height() // 2))


# ═══════════════════════ C-221 / C-189：三步 ═══════════════════════
def test_c221_three_steps_and_step2_command(qapp):
    """C-221：『仿真器找不到这根网』按症状组织成三步，每步有标题 / 结果框 / 主按钮；
    第 2 步把那行命令复制到剪贴板并报「已复制」（裁决⑪的措辞一字不改）。"""
    st = make_state()
    d = drawer(st)
    for i, oname in enumerate((N.DIAG_STEP1_BTN, N.DIAG_STEP2_BTN, N.DIAG_STEP3_BTN)):
        btn = H.find(d, oname, QtWidgets.QPushButton)
        assert btn.text() == T.DIAG_STEPS[i][4]
    for i, oname in enumerate((N.DIAG_STEP1_BOX, N.DIAG_STEP2_BOX, N.DIAG_STEP3_BOX)):
        assert H.find(d, oname, QtWidgets.QLabel) is not None
    # 主症状蓝框的标题 / 正文
    assert H.find(d, N.DIAG_CUVUNF_TITLE, QtWidgets.QLabel).text() == T.DIAG_CUVUNF_TITLE
    assert H.find(d, N.DIAG_INTRO, QtWidgets.QLabel).text() == T.DIAG_INTRO
    assert H.find(d, N.DIAG_FOOTER, QtWidgets.QLabel).text() == T.DIAG_FOOTER
    # 第 2 步：结果框 = 那行命令；点按钮 → 剪贴板 + statusMessage
    box2 = H.find(d, N.DIAG_STEP2_BOX, QtWidgets.QLabel)
    assert box2.text() == "python3 scan_rtl.py" == T.DIAG_STEPS[1][3]
    seen = []
    d.statusMessage.connect(seen.append)
    H.click(H.find(d, N.DIAG_STEP2_BTN, QtWidgets.QPushButton))
    assert QtWidgets.QApplication.clipboard().text() == "python3 scan_rtl.py"
    assert seen == [T.STATUS_COPIED]
    # 第 1 步只发信号，导出本体归导出中心（C4-int 接 preselect="nets"）
    fired = []
    d.exportNetsRequested.connect(lambda: fired.append(1))
    H.click(H.find(d, N.DIAG_STEP1_BTN, QtWidgets.QPushButton))
    assert fired == [1]
    d.close()


def test_c189_step1_box_shows_last_nets_or_never(qapp):
    """C-189：第 1 步结果框 = 上次导到哪 + 总网数 / 分项计数；从没导出过 → 「还没导出过」。"""
    st = make_state()
    d = drawer(st)
    box = H.find(d, N.DIAG_STEP1_BOX, QtWidgets.QLabel)
    assert box.text() == T.DIAG_STEP1_NEVER
    st.record_export("nets", os.path.join(os.path.dirname(st.loaded_path), "nets.txt"))
    d.refresh()
    n_sig, n_net, n_guess = D.nets_summary(st)
    assert n_sig == len(st.models()) and n_net > 0 and n_guess >= 0
    assert box.text() == T.DIAG_STEPS[0][3].format(
        path=st.last_export("nets").path, n_sig=n_sig, n_net=n_net, n_guess=n_guess)
    assert "nets.txt" in box.text()
    d.close()


# ═══════════════════════ C-200…C-205 / C-248：探针前缀 ═══════════════════════
def test_c200_c201_c202_c203_c204_c205_prefix_editor_roundtrip_and_keeps_checks(
        qapp, monkeypatch, tmp_path):
    """C-200 编辑器本体 / C-201 合并写法 + 扁平写法 + `#` 注释 / C-202 导入合并（同名以导入为准）
    / C-203 导出 .txt / C-204 保存后报影响面 / C-205 改完前缀勾选不被重置成全勾。"""
    st = make_state()
    names = [m["name"] for m in st.models()]
    assert len(names) > 3
    st.set_checked(names[:2], False)                 # 用户挑过的导出集
    picked = set(st.checked() or set())
    assert picked and len(picked) == len(names) - 2

    dlg = editor(D.PrefixEditorDialog, st)
    edit = H.find(dlg, N.DIAG_PREFIX_TEXT, QtWidgets.QPlainTextEdit)
    assert H.find(dlg, N.DIAG_PREFIX_IMPACT, QtWidgets.QLabel) is not None
    hint = dlg.findChild(QtWidgets.QLabel, N.DIAG_PREFIX_HINT)
    assert hint is not None and hint.text() == T.DIAG_PREFIX_HINT

    # C-201：合并写法（路径一行、其下信号名逗号 / 换行分隔）与扁平写法混用 + `#` 注释
    probe = str(st.models()[0].get("probe_net") or "").lower()
    assert probe
    edit.setPlainText("# 注释行\nU_A:\n    %s, foo\nbar=U_B\n" % probe)
    mapping = dlg.mapping()
    assert mapping == {probe: "U_A", "foo": "U_A", "bar": "U_B"}

    # C-202：导入与现有合并，同名以导入为准
    imp = tmp_path / "probe_prefixes.txt"
    imp.write_text("bar=U_NEW\nbaz=U_C\n", encoding="utf-8")
    rec = H.auto_dialogs(monkeypatch, answers={"getOpenFileName": (str(imp), "")})
    H.click(H.find(dlg, N.DIAG_PREFIX_IMPORT_BTN, QtWidgets.QPushButton))
    merged = dlg.mapping()
    assert merged["bar"] == "U_NEW" and merged["baz"] == "U_C" and merged["foo"] == "U_A"
    assert rec.count("getOpenFileName") == 1

    # C-203 / C-248：导出 .txt，格式与 v1 `generator.render_probe_prefix_grouped` 逐字节相同
    out = tmp_path / "out_prefixes.txt"
    H.auto_dialogs(monkeypatch, answers={"getSaveFileName": (str(out), "")})
    H.click(H.find(dlg, N.DIAG_PREFIX_EXPORT_BTN, QtWidgets.QPushButton))
    assert out.read_text(encoding="utf-8") == \
        generator.render_probe_prefix_grouped(merged).rstrip() + "\n"

    # C-204：保存 → 影响面「共 N 条映射 · 影响 M 个信号」；probe_net 命中的那个信号必须被数进去
    H.click(H.find(dlg, N.DIAG_PREFIX_SAVE_BTN, QtWidgets.QPushButton))
    n_map, n_sig = D.prefix_impact(st, st.probe_prefixes)
    assert n_map == len(merged) and n_sig >= 1
    assert H.find(dlg, N.DIAG_PREFIX_IMPACT, QtWidgets.QLabel).text() == \
        T.DIAG_PREFIX_IMPACT_FMT.format(n=n_map, m=n_sig)
    # I-04：写回的唯一入口是 state（视图不自己存一份）
    assert st.probe_prefixes == {k: v for k, v in merged.items()}

    # C-205：清单重建会把勾选重置成全勾 —— 改完前缀后用户挑的那份必须还在
    assert set(st.checked() or set()) == picked
    st.set_models("topout", st.provider("topout").skeleton_models())   # 改完配置后清单真的重建一次
    assert set(st.checked() or set()) == picked, "重建清单把用户挑的导出集冲掉了（C-205）"
    dlg.close()


def test_c202_c204_step3_imports_prefixes_from_drawer(qapp, monkeypatch, tmp_path):
    """C-202 / C-204：第 3 步「导入 probe_prefixes.txt…」—— 与现有合并、存回 state、
    结果框换成影响面（抽屉这条路径与编辑器那条是两条线，各测各的）。"""
    st = make_state()
    st.set_probe_prefixes({"keep_me": "U_OLD", "dup": "U_OLD"})
    d = drawer(st)
    box = H.find(d, N.DIAG_STEP3_BOX, QtWidgets.QLabel)
    assert box.text() == T.DIAG_STEPS[2][3].format(n_map=2, n_missing=D.make_snapshot(st).n_prefix_missing)

    probe = str(st.models()[0].get("probe_net") or "").lower()
    imp = tmp_path / "probe_prefixes.txt"
    imp.write_text("dup=U_NEW\n%s=U_HIT\n" % probe, encoding="utf-8")
    rec = H.auto_dialogs(monkeypatch, answers={"getOpenFileName": (str(imp), "")})
    seen = []
    d.statusMessage.connect(seen.append)
    H.click(H.find(d, N.DIAG_STEP3_BTN, QtWidgets.QPushButton))
    assert rec.count("getOpenFileName") == 1
    assert st.probe_prefixes == {"keep_me": "U_OLD", "dup": "U_NEW", probe: "U_HIT"}
    n_map, n_sig = D.prefix_impact(st, st.probe_prefixes)
    assert n_sig >= 1
    assert box.text() == T.DIAG_PREFIX_IMPACT_FMT.format(n=n_map, m=n_sig) == seen[-1]
    # 取消文件框 = 什么都不发生
    H.auto_dialogs(monkeypatch, answers={"getOpenFileName": ("", "")})
    before = dict(st.probe_prefixes)
    assert d.on_step3() is None and st.probe_prefixes == before
    d.close()


def test_diag_drawer_opens_four_editors(qapp, monkeypatch):
    """抽屉的四个入口各自开对窗：前缀 / 强制 force / RTL 补充 / 旧版迁移。"""
    st = make_state()
    write_legacy_bucket(st.loaded_path)
    d = drawer(st)
    d.refresh()
    H.auto_dialogs(monkeypatch)          # QDialog.exec 一律立刻 Accepted，不会卡住 offscreen
    for oname, cls in ((N.DIAG_PREFIX_EDIT_BTN, D.PrefixEditorDialog),
                       (N.DIAG_FORCE_OPEN_BTN, D.ForceEditorDialog),
                       (N.DIAG_SUPP_OPEN_BTN, D.SupplementEditorDialog),
                       (N.DIAG_LEGACY_IMPORT_BTN, D.LegacyImportDialog)):
        btn = d.findChild(QtWidgets.QPushButton, oname)
        assert btn is not None and btn.isEnabled(), oname
        H.click(btn)
        assert isinstance(d._dialogs[-1], cls), "%s 开错了窗" % oname
    d.close()


def test_c248_c249_text_formats_unchanged(qapp, monkeypatch, tmp_path):
    """C-248 探针前缀 .txt / C-249 RTL 补充逻辑 .json 的文件格式与 v1 逐字节相同。

    判据不是「看起来一样」，而是**本模块一个字节都不自己拼**：渲染 / 解析全过 `session.*`，
    而 `session.*` 就是 v1 那两个入口用的同一份 `generator.parse_probe_prefix_lines` /
    `render_probe_prefix_grouped`。"""
    st = make_state()
    mapping = {"pll_n": "U_BT_LP_PLL_DIG", "mon_active": "U_BT_LP_PLL_DIG", "x": "U_C.D_1"}
    st.set_probe_prefixes(mapping)
    dlg = editor(D.PrefixEditorDialog, st)
    # ① 编辑框初值 = v1 的合并渲染
    text = H.find(dlg, N.DIAG_PREFIX_TEXT, QtWidgets.QPlainTextEdit).toPlainText()
    assert text == generator.render_probe_prefix_grouped(st.probe_prefixes)
    # ② 解析 = v1 的解析；往返不丢
    assert dlg.mapping() == generator.parse_probe_prefix_lines(text) == dict(st.probe_prefixes)
    # ③ 写出 = 文本 + 单个换行（v1 `write_mapping_text` 同一函数）
    out = tmp_path / "p.txt"
    H.auto_dialogs(monkeypatch, answers={"getSaveFileName": (str(out), "")})
    H.click(H.find(dlg, N.DIAG_PREFIX_EXPORT_BTN, QtWidgets.QPushButton))
    # 文本模式写盘（v1 `write_mapping_text` 同一函数）——Windows 上换行落成 \r\n，按**文本**比
    assert out.read_text(encoding="utf-8") == text.rstrip() + "\n"
    dlg.close()

    # C-249：{基名: {enabled, expr, inputs:[{var,raw}], note}} 的字段集不变、往返逐键相同
    spec = _supp_spec()
    sdlg = editor(D.SupplementEditorDialog, st)
    sed = H.find(sdlg, N.DIAG_SUPP_TEXT, QtWidgets.QPlainTextEdit)
    sed.setPlainText(S.render_supplements_json(spec))
    H.click(H.find(sdlg, N.DIAG_SUPP_SAVE_BTN, QtWidgets.QPushButton))
    saved = st.logic_overrides[LOGIC_SIG]
    assert set(saved) == {"enabled", "expr", "inputs", "note"}
    assert saved["inputs"] and all(set(i) == {"var", "raw"} for i in saved["inputs"])
    assert S.parse_supplements_json(S.render_supplements_json(st.logic_overrides)) == \
        dict(st.logic_overrides)
    sdlg.close()


# ═══════════════════════ C-206…C-209：强制 force ═══════════════════════
def test_c206_c207_c208_c209_force_editor_import_export_effective_on_topout_and_page(
        qapp, monkeypatch, tmp_path):
    """C-206 名单编辑器 / C-207 行尾 `#` 注释 + 留空=清除 / C-208 导入 / 导出 .txt
    / C-209 名单对 **Topout 视图与页本地视图**的分析都真正生效。"""
    st = make_state(scopes=("topout", "logic"))
    leaf = "d_bt_lp_linelocal_mode_ctrl"        # 这根叶子出厂是 RW（RF_WRITE 下值）

    def kinds(vid):
        an = st.analyze(LOGIC_SIG, vid)
        return {b.base: b.kind for b in (an["bindings"] or {}).values()}

    before_t, before_p = kinds("topout"), kinds("logic")
    assert before_t[leaf] == "RW" and before_p[leaf] == "RW"

    dlg = editor(D.ForceEditorDialog, st)
    edit = H.find(dlg, N.DIAG_FORCE_TEXT, QtWidgets.QPlainTextEdit)
    hint = dlg.findChild(QtWidgets.QLabel, N.DIAG_FORCE_HINT)
    assert hint is not None and hint.text() == T.DIAG_FORCE_HINT

    # C-207：行尾 # 之后是注释、# 开头整行是注释、空行忽略
    edit.setPlainText("# 整行注释\n%s   # 撞名，手动强制\n\n" % leaf)
    assert dlg.names() == {leaf}

    # C-208：导入（并进现有名单）/ 导出（同一份 .txt 格式）
    imp = tmp_path / "force_signals.txt"
    imp.write_text("d_bt_lp_rx_en_local  # 另一根\n", encoding="utf-8")
    H.auto_dialogs(monkeypatch, answers={"getOpenFileName": (str(imp), "")})
    H.click(H.find(dlg, N.DIAG_FORCE_IMPORT_BTN, QtWidgets.QPushButton))
    assert dlg.names() == {leaf, "d_bt_lp_rx_en_local"}
    out = tmp_path / "force_out.txt"
    H.auto_dialogs(monkeypatch, answers={"getSaveFileName": (str(out), "")})
    H.click(H.find(dlg, N.DIAG_FORCE_EXPORT_BTN, QtWidgets.QPushButton))
    assert out.read_text(encoding="utf-8") == \
        S.render_force_signal_text({leaf, "d_bt_lp_rx_en_local"}) + "\n"

    # 保存只留一根，验 C-209
    edit.setPlainText("%s\n" % leaf)
    H.click(H.find(dlg, N.DIAG_FORCE_SAVE_BTN, QtWidgets.QPushButton))
    assert st.force_signals == {leaf}
    after_t, after_p = kinds("topout"), kinds("logic")
    assert after_t[leaf] == "RO" and after_p[leaf] == "RO", "名单对两种视图都得生效（C-209）"
    assert after_t != before_t and after_p != before_p
    drives = [r["drive"] for r in IT.input_rows(st.analyze(LOGIC_SIG)) if r["name"] == leaf]
    assert drives and drives[0].startswith("force "), "强制 force = 直接 force 顶层基名网"

    # C-207 后半：留空 = 清除
    dlg2 = editor(D.ForceEditorDialog, st)
    H.find(dlg2, N.DIAG_FORCE_TEXT, QtWidgets.QPlainTextEdit).setPlainText("   \n# 只剩注释\n")
    H.click(H.find(dlg2, N.DIAG_FORCE_SAVE_BTN, QtWidgets.QPushButton))
    assert st.force_signals == set()
    assert kinds("topout")[leaf] == "RW"
    dlg.close()
    dlg2.close()


# ═══════════════════════ C-210…C-216：RTL 补充逻辑 ═══════════════════════
def _supp_spec(expr="ECO_EN & (A & B)"):
    """一条合法的补充：在原式外面包一级 ECO 使能，并加一根**新输入**（clk_force_on）。"""
    return {LOGIC_SIG: {"enabled": True, "note": "样例：SE 说顶层口后多了一级 ECO 使能",
                        "expr": expr,
                        "inputs": [{"var": "A", "raw": "d_bt_lp_linelocal_mode_ctrl"},
                                   {"var": "B", "raw": "d_bt_lp_linectrl_rx_en"},
                                   {"var": "ECO_EN", "raw": "clk_force_on"}]}}


def test_c210_c211_c212_c213_c214_supplement_editor_validate(qapp, monkeypatch, tmp_path):
    """C-210 编辑器本体 / C-211 插入模板（当前信号）预填原式与原输入 / C-212 从 .json 导入
    / C-213 六道校验不过 **不保存** 并逐条列错 / C-214 基名不在 logic 页 → 提示但允许。"""
    st = make_state()
    st.set_current(LOGIC_SIG)
    dlg = editor(D.SupplementEditorDialog, st)
    edit = H.find(dlg, N.DIAG_SUPP_TEXT, QtWidgets.QPlainTextEdit)
    errors = H.find(dlg, N.DIAG_SUPP_ERRORS, QtWidgets.QLabel)
    assert not errors.isVisible()

    # C-211：插入模板（当前信号）——预填原表达式与原输入映射
    H.click(H.find(dlg, N.DIAG_SUPP_TEMPLATE_BTN, QtWidgets.QPushButton))
    tmpl = json.loads(edit.toPlainText())
    assert LOGIC_SIG in tmpl
    src = st.analyze(LOGIC_SIG)["sig"]
    assert tmpl[LOGIC_SIG]["expr"] == src.expr
    assert {i["var"] for i in tmpl[LOGIC_SIG]["inputs"]} == set(src.inputs)

    # C-213 ①：非法 JSON —— 不保存、列错
    edit.setPlainText("{ 这不是 JSON")
    assert dlg.on_save() is None
    assert errors.isVisible() and T.DIAG_SUPP_INVALID_HEAD in errors.text()
    assert st.logic_overrides == {}

    # C-213 ②：六道校验逐条列错（占位符未替换 / 缺 expr / inputs 非列表 / 变量没有 input 映射）
    bad = {"<信号基名>": {"expr": "A", "inputs": [{"var": "A", "raw": "x"}]},
           "sig_no_expr": {"inputs": [{"var": "A", "raw": "x"}]},
           "sig_bad_inputs": {"expr": "A", "inputs": {}},
           "sig_missing_var": {"expr": "A & ZZZ", "inputs": [{"var": "A", "raw": "x"}]}}
    edit.setPlainText(json.dumps(bad, ensure_ascii=False))
    assert dlg.on_save() is None
    _norm, errs = S.validate_supplements(bad)
    assert len(errs) == 4
    for e in errs:
        assert T.scrub(e) in errors.text()
    assert st.logic_overrides == {}, "校验不通过一个字节都不许存（C-213）"

    # C-212：从 .json 导入
    good = tmp_path / "supp.json"
    good.write_text(json.dumps(_supp_spec(), ensure_ascii=False), encoding="utf-8")
    H.auto_dialogs(monkeypatch, answers={"getOpenFileName": (str(good), "")})
    H.click(H.find(dlg, N.DIAG_SUPP_IMPORT_BTN, QtWidgets.QPushButton))
    assert json.loads(edit.toPlainText()) == _supp_spec()
    H.click(H.find(dlg, N.DIAG_SUPP_SAVE_BTN, QtWidgets.QPushButton))
    assert set(st.logic_overrides) == {LOGIC_SIG}
    dlg.close()

    # C-214：基名不在当前 logic 页 —— 允许保存，但要点名提示「将作为纯新增合成信号生成」
    dlg2 = editor(D.SupplementEditorDialog, st)
    spec = _supp_spec()
    spec["d_brand_new_eco_sig"] = spec.pop(LOGIC_SIG)
    H.find(dlg2, N.DIAG_SUPP_TEXT, QtWidgets.QPlainTextEdit).setPlainText(
        json.dumps(spec, ensure_ascii=False))
    H.click(H.find(dlg2, N.DIAG_SUPP_SAVE_BTN, QtWidgets.QPushButton))
    unknown = dlg2.findChild(QtWidgets.QLabel, N.DIAG_SUPP_UNKNOWN)
    assert not unknown.isHidden(), "提示条要摆出来（保存成功后窗自己关了，只能看它自己的显隐）"
    assert unknown.text() == T.DIAG_SUPP_UNKNOWN_FMT.format(name="d_brand_new_eco_sig")
    assert set(st.logic_overrides) == {"d_brand_new_eco_sig"}, "提示归提示，仍然允许保存"
    dlg2.close()


def test_c215_c216_supplement_visible_in_topout_expand(qapp):
    """C-215 / C-216：存了补充之后，`state.analyze` 的 an 带补充来源标记，
    且 **Topout 视图的展开**看得到补充的新输入（它自动成为真值表的一维）。"""
    st = make_state()
    an0 = st.analyze(LOGIC_SIG)
    assert getattr(an0["sig"], "_is_supplement", False) is False
    before = {r["name"] for r in IT.input_rows(an0)}
    assert "clk_force_on" not in before

    st.set_logic_overrides(S.validate_supplements(_supp_spec())[0])
    an1 = st.analyze(LOGIC_SIG)
    assert getattr(an1["sig"], "_is_supplement", False) is True, "C-215 的琥珀标记靠这个来源标记"
    assert getattr(an1["sig"], "_supplement_note", "")
    after = {r["name"] for r in IT.input_rows(an1)}
    assert "clk_force_on" in after, "C-216：补充的新输入自动成为真值表维度"
    assert len(an1.get("vectors") or []) > 0


# ═══════════════════════ C-217 / I-11：缺前缀是否强制生成 ═══════════════════════
def test_c217_risky_toggle_default_true_and_warns_off(qapp, monkeypatch):
    """C-217：可见开关，默认 **是**；关掉前先确认一次，不确认就不改（改产物是显式动作）。

    ⚠ C4-int 起这一次确认走 `dialogs.ConfirmDialog` 的第四种 kind `risky_off`
    （C4-b 当时用的是 `QMessageBox.question`）—— 所以答案按 `ConfirmDialog.ask` 的**领域值**
    （True / False）给，不再按 `QMessageBox.Yes/No` 的按钮码。正文仍然是同一句
    `terms.DIAG_RISKY_OFF_WARNING`，`rec.saw` 照样验得到（走真框 → `exec` 那一层记正文）。"""
    st = make_state()
    d = drawer(st)
    chk = H.find(d, N.DIAG_RISKY_TOGGLE, QtWidgets.QCheckBox)
    assert chk.isChecked() is True and st.include_risky is True
    assert chk.text() == T.DIAG_OTHER[3][1].format(state=T.DIAG_RISKY_ON)
    d.items[N.DIAG_SYM_RISKY].expand()

    # ① 确认框答「否」→ 开关弹回去、后端一个字节不动
    #    `ask: REAL` = 放真对话框跑到 `exec`：这一条要验的是**正文说没说清「.sv 会变」**，
    #    在 ask 那一层直接给 False 的话框根本没造出来，正文也就无从验起。
    rec = H.auto_dialogs(monkeypatch, answers={"ask": H.REAL,
                                               "ConfirmDialog.exec": QtWidgets.QDialog.Rejected})
    click_check(chk)
    assert rec.count("ConfirmDialog.exec") == 1
    assert rec.saw(T.DIAG_RISKY_OFF_WARNING), "关掉前必须把「.sv 会变」说清楚"
    assert rec.saw(T.DIAG_RISKY_OFF_TITLE), "标题要说清在关哪个开关"
    assert chk.isChecked() is True and st.include_risky is True

    # ② 确认框答「是」→ 真关掉，文案跟着变「否」，并把警告摆出来
    rec2 = H.auto_dialogs(monkeypatch, answers={"ConfirmDialog.ask": True})
    click_check(chk)
    assert rec2.count("ConfirmDialog.ask") == 1
    assert chk.isChecked() is False and st.include_risky is False
    assert chk.text() == T.DIAG_OTHER[3][1].format(state=T.DIAG_RISKY_OFF)
    assert d.risky_warn.isVisible()

    # ③ 再打开不需要确认
    rec3 = H.auto_dialogs(monkeypatch)
    click_check(chk)
    assert rec3.count("ConfirmDialog.ask") == 0 and rec3.count("ConfirmDialog.exec") == 0
    assert chk.isChecked() is True and st.include_risky is True
    d.close()


def test_i11_include_risky_default_true_everywhere(qapp):
    """I-11：`include_risky` 的出厂默认在**每一层**都是 True —— state / 快照 / 抽屉开关。

    默认值一旦被谁悄悄改成 False，byte-gate 的 6 个 .sv 立刻变脸（A3 §1.5 的护栏）。"""
    st = make_state()
    assert st.include_risky is True
    snap = D.make_snapshot(st)
    assert snap.include_risky is True
    d = drawer(st)
    assert H.find(d, N.DIAG_RISKY_TOGGLE, QtWidgets.QCheckBox).isChecked() is True
    # 没载表也是 True（空态打开抽屉不该显示「否」）
    d2 = drawer(ST.WorkbenchState())
    assert H.find(d2, N.DIAG_RISKY_TOGGLE, QtWidgets.QCheckBox).isChecked() is True
    assert D.make_snapshot(None).include_risky is True
    d.close()
    d2.close()


# ═══════════════════════ I-04：诊断配置单一来源 ═══════════════════════
def test_i04_diag_config_single_source_in_state(qapp, tmp_path):
    """I-04：三套诊断配置按 Excel **全路径**分桶，写入口只有 `state.set_*`；
    抽屉自己只拿快照，不留第二份真相。"""
    st = make_state()
    path = st.loaded_path
    st.set_probe_prefixes({"a": "U_A"})
    st.set_force_signals({"b"})
    st.set_logic_overrides(S.validate_supplements(_supp_spec())[0])
    for key, want in (("probe_prefixes", {"a": "U_A"}), ("force_signals", ["b"])):
        assert P.path_map_of(key, path) == want, "%s 没按全路径分桶" % key
    assert set(P.path_map_of("logic_overrides", path) or {}) == {LOGIC_SIG}

    snap = D.make_snapshot(st)
    assert snap.probe_prefixes == {"a": "U_A"}
    assert snap.force_signals == {"b"}
    assert set(snap.logic_overrides) == {LOGIC_SIG}
    # 快照是**拷贝**：改它不该反噬 state（它不是真相源）
    snap.probe_prefixes["a"] = "U_ZZZ"
    snap.force_signals.add("zzz")
    assert st.probe_prefixes == {"a": "U_A"} and st.force_signals == {"b"}
    # 抽屉里的每个写动作都落在 state.set_* 上（模块自己不碰 persist 的写口）
    src = open(D.__file__, "r", encoding="utf-8").read()
    for bad in ("persist.save_path_map", "P.save_path_map", "P.patch_settings"):
        assert bad not in src, "诊断配置只许经 state.set_*（I-04）"


# ═══════════════════════ C-302：旧版真值表编辑迁移 ═══════════════════════
def test_c302_legacy_import_only_logic_register_names_mux(qapp, monkeypatch):
    """C-302：一次性显式动作；只迁 logic / 直连寄存器根，mux **点名列出不迁的原因**；
    auto 由 `restore_cols` 权威重算；legacy 段**只读不删**。"""
    st0 = ST.WorkbenchState()
    mirror = H.mirror_path("btlp")
    raw = write_legacy_bucket(mirror)
    st = make_state()
    assert st.edits() == {}, "legacy 桶默认不进自动恢复（C-235）"

    # 计划书：迁什么、不迁什么
    plan = D.plan_legacy_import(st)
    assert plan.names() == [LOGIC_SIG]
    assert plan.n_cols == 2
    skipped = dict(plan.skipped)
    assert MUX_SIG in skipped and "c:A" in skipped[MUX_SIG] and "d:0" in skipped[MUX_SIG]
    assert GHOST in skipped and skipped[GHOST] == T.DIAG_LEGACY_SKIP_UNKNOWN

    # 快照的 legacy 两格要真分类（`legacy_bucket_counts` 不给 kind_of 时会把 register 也算进 logic）
    snap = D.make_snapshot(st)
    assert snap.legacy_counts["edits"] == 2 and snap.legacy_counts["mux"] == 1
    assert snap.legacy_counts["logic"] + snap.legacy_counts["register"] == 2

    # 弹窗：不迁的名字 + 原因在**前**，能迁的清单在后（C-270 的形状）
    dlg = editor(D.LegacyImportDialog, st)
    prev = H.find(dlg, N.DIAG_LEGACY_PREVIEW, QtWidgets.QLabel)
    skip_lb = dlg.findChild(QtWidgets.QLabel, N.DIAG_LEGACY_SKIPPED)
    assert skip_lb.isVisible() and MUX_SIG in skip_lb.text() and GHOST in skip_lb.text()
    assert LOGIC_SIG in prev.text() and MUX_SIG in prev.text()

    # 执行
    H.click(H.find(dlg, N.DIAG_LEGACY_RUN_BTN, QtWidgets.QPushButton))
    ed = st.edit_of(LOGIC_SIG)
    assert ed is not None and len(ed["cols"]) == 2
    pos, neg = ed["cols"]
    assert pos["neg"] is False and pos["exp"] == 1
    assert neg["neg"] is True and neg["exp"] == 0, "C-301：错值 = 0 不许被真值判断丢掉"
    assert neg["name"] == "T0_NEG" and neg["user"] is True
    assert pos["vals"] == LEGACY_ROWS[0]["base_values"], "键空间相同，base_values 直接搬"
    assert pos["auto_w"] == (st.analyze(LOGIC_SIG).get("out_width") or 1)
    # auto 由引擎权威重算（不是存盘里的占位 0）
    from dreg_verify import edits as ED
    chk = dict(pos)
    ED.recompute_col_an(st.analyze(LOGIC_SIG), chk)
    assert pos["auto"] == chk["auto"]

    # legacy 段只读不删：写盘之后旧九段仍逐字节在那儿
    with open(P.EDITS_PATH, "r", encoding="utf-8") as f:
        after = json.load(f)[mirror]
    before = json.loads(raw)[mirror]
    for seg in P.LEGACY_SEGMENTS:
        if seg in before:
            assert after.get(seg) == before[seg], "legacy 段 %s 被动了" % seg
    assert "view_edits" in after and LOGIC_SIG in after["view_edits"]["topout"]
    dlg.close()
    del st0


def test_c302_legacy_import_empty_bucket_is_a_no_op(qapp):
    """旧版桶是空的（多数同事的机器）：计划书为空、按钮置灰、抽屉写「这张表没有旧版真值表编辑」。"""
    st = make_state()
    plan = D.plan_legacy_import(st)
    assert not plan and plan.n_rows == 0 and plan.skipped == []
    d = drawer(st)
    btn = H.find(d, N.DIAG_LEGACY_IMPORT_BTN, QtWidgets.QPushButton)
    assert btn.isEnabled() is False
    assert d.items[N.DIAG_SYM_LEGACY].hint.text() == T.DIAG_LEGACY_NONE
    dlg = editor(D.LegacyImportDialog, st)
    assert T.DIAG_LEGACY_NONE in H.find(dlg, N.DIAG_LEGACY_PREVIEW, QtWidgets.QLabel).text()
    assert H.find(dlg, N.DIAG_LEGACY_RUN_BTN, QtWidgets.QPushButton).isEnabled() is False
    dlg.close()
    d.close()


# ═══════════════════════ C-218 / C-219 / C-220：级联说明窗退役 ═══════════════════════
def test_c218_c219_c220_cascade_retired(qapp):
    """C-218 / C-219 / C-220：级联模式说明窗随三处级联下拉（C-223 / C-224）一并退役 ——
    v2 不实现，`names.py` 里不留 cascade 名；文档留作背景资料。"""
    for k, v in N.all_names().items():
        assert "cascade" not in k.lower() and "cascade" not in v.lower(), \
            "级联入口已退役，names.py 不该再有 %s" % k
    # 界面文案里也不许再有级联的说法（说明窗没了，入口的文字也该一起没）
    for k, v in vars(T).items():
        if k.isupper() and isinstance(v, str):
            assert "级联" not in v, "terms.%s 还在说级联" % k
    assert not [k for k in vars(D) if "cascade" in k.lower()], "抽屉里不该有级联说明窗"
    assert not hasattr(D.DiagnosticsDrawer, "open_cascade_doc")
    doc = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "docs", "级联模式说明.md")
    assert os.path.exists(doc), "说明文档留作背景资料（只是不再有界面入口）"


# ═══════════════════════ 抽屉本体：直链 / 名字 / 快照 ═══════════════════════
def test_diag_open_for_symptom_scrolls_to_item(qapp):
    """清单行内原因块的「去诊断 · …」直链：`open_for(symptom)` 按 `terms.REASON_TARGETS`
    的键滚到 / 展开对应项。"""
    st = make_state()
    d = drawer(st)
    # `REASON_TARGETS` 里每个 diag_* 目标都得有落点（漏一个 = 行内按钮点了没反应）
    for key in T.REASON_TARGETS:
        if key.startswith("diag_"):
            assert key in D.SYMPTOM_TARGETS, "原因块跳转目标 %s 在抽屉里没有落点" % key
    d.hide()
    assert d.open_for("diag_cuvunf") == N.DIAG_CUVUNF_BOX
    assert d.isVisible()
    for sym, oname in (("diag_supplement", N.DIAG_SYM_SUPPLEMENT),
                       ("diag_force", N.DIAG_SYM_FORCE),
                       ("diag_coverage", N.DIAG_SYM_COVERAGE),
                       ("diag_risky", N.DIAG_SYM_RISKY),
                       ("diag_legacy", N.DIAG_SYM_LEGACY)):
        assert d.open_for(sym) == oname
        assert d.items[oname].is_expanded(), "%s 该被展开" % sym
    # 不认识的症状 = 就打开、滚到顶，不崩
    assert d.open_for("没这个症状") == ""
    assert d.open_for() == ""
    # 折叠项 ③ 只发信号（覆盖度弹层归 C4-int）
    fired = []
    d.coverageRequested.connect(lambda: fired.append(1))
    H.click(d.findChild(QtWidgets.QPushButton, N.DIAG_COVERAGE_BTN))
    assert fired == [1]
    # 收起
    H.click(H.find(d, N.DIAG_BTN_CLOSE, QtWidgets.QToolButton))
    assert not d.isVisible()


def test_diag_names_all_set(qapp):
    """I-14：`names.py` 里每个 `diag_*` objectName 都真的挂在某个控件上（抽屉 + 四个窗）。"""
    st = make_state()
    d = drawer(st)
    seen = set(H.object_names(d)) | {d.objectName()}
    for cls in (D.PrefixEditorDialog, D.ForceEditorDialog, D.SupplementEditorDialog,
                D.LegacyImportDialog):
        dlg = editor(cls, st)
        seen |= set(H.object_names(dlg)) | {dlg.objectName()}
        dlg.close()
    want = {v for k, v in N.all_names().items()
            if v.startswith("diag_") and k not in ("TOP_DIAG_BTN", "LIST_REASON_DIAG_BTN",
                                                   "HDR_RESOLVE_DIAG_BTN")}
    assert want - seen == set(), "这些 DIAG 名字没挂到控件上：%s" % sorted(want - seen)
    assert d.width() == TH.DIAG_W == 620
    assert d.graphicsEffect() is not None, "Design 的 box-shadow 用 QGraphicsDropShadowEffect"
    d.close()


def test_diag_snapshot_counts_missing_prefix_and_legacy(qapp):
    """§2.7 的快照：`n_prefix_missing` = Σ 各信号输入行的 `needs_prefix`；
    `legacy_counts` 来自 `persist.legacy_bucket_counts`。"""
    st = make_state("wl")
    snap = D.make_snapshot(st)
    hand = sum(1 for m in st.models()
               for r in IT.input_rows(st.analyze(m["name"]) or {})
               if r.get("needs_prefix"))
    assert snap.n_prefix_missing == hand
    assert snap.legacy_counts == P.legacy_bucket_counts(
        st.loaded_path, kind_of=lambda nm: (st.resolve_root(nm) or {}).get("kind"))
    # 第 3 步结果框用的就是这两个数
    d = drawer(st)
    assert H.find(d, N.DIAG_STEP3_BOX, QtWidgets.QLabel).text() == \
        T.DIAG_STEPS[2][3].format(n_map=len(st.probe_prefixes), n_missing=hand)
    d.close()


def test_diag_no_state_does_not_crash(qapp):
    """没载表就按 Ctrl+Shift+D：抽屉照样开得起来、每个框都有话说，一个异常都不许往外抛。"""
    d = drawer(None)
    assert d.open_for("diag_cuvunf") == N.DIAG_CUVUNF_BOX
    assert H.find(d, N.DIAG_STEP1_BOX, QtWidgets.QLabel).text() == T.DIAG_STEP1_NEVER
    assert H.find(d, N.DIAG_RISKY_TOGGLE, QtWidgets.QCheckBox).isChecked() is True
    d.set_state(ST.WorkbenchState())
    d.refresh()
    d.close()


# ═══════════════════════ 截图 ═══════════════════════
def test_shots(qapp):
    st = make_state()
    st.set_probe_prefixes({"pll_n": "U_BT_LP_PLL_DIG", "mon_active": "U_BT_LP_PLL_DIG"})
    d = drawer(st)
    d.resize(TH.DIAG_W, 1180)
    for it in d.items.values():
        it.expand()
    qapp.processEvents()
    assert os.path.exists(H.shot(d, "diag_drawer"))
    dlg = editor(D.PrefixEditorDialog, st)
    qapp.processEvents()
    assert os.path.exists(H.shot(dlg, "diag_prefix_editor"))
    dlg.close()
    d.close()
