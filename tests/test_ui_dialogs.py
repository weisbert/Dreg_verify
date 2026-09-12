# -*- coding: utf-8 -*-
"""GUI v2 C2-d：对话框集 `ui/dialogs.py` 的契约测试。

口径（执行计划 §2 L3/L4/L5）：
  · offscreen 起**单个对话框**（不起 MainWindow），用户动作一律走 `ui_harness` 的
    `click / keys / type_text`（QTest 真实事件），不直接调槽、不 emit 信号冒充用户；
  · 失败路径必测：重命名非法名**不关框**、mux 数据值非法写法**就地标红且不关框**、
    批量填「固定值」没填值不关框；
  · `test_dlg_all_object_names_registered` 把每个对话框及其主要控件的 objectName 与
    `names.all_names()` 对账，还没进 names.py 的列成白名单（= `dialogs.PENDING_NAMES`，
    回报主控整块搬进去）；
  · `test_dlg_titles_stable_for_harness` 验 `auto_dialogs(answers={标题: 答案})` 能按标题
    （精确 + 子串）命中每一个对话框 —— C2-int 往 harness 加 CUSTOM_MODALS 前的前置条件。

契约 ID：C-036 C-089 C-090 C-091 C-092 C-093 C-094 C-095 C-102 C-103 C-110 C-113
C-008 C-046 C-159 C-160 C-161 C-163 C-164 C-193 C-194 C-195 C-270 C-290 C-291 C-298（+ I-14 / I-20）。
"""

import io
import os

import pytest

import ui_harness as H

pytest.importorskip("PySide6")

from PySide6 import QtCore, QtWidgets                 # noqa: E402

from dreg_verify import edits as ED                   # noqa: E402
from dreg_verify import exports as EX                 # noqa: E402
from dreg_verify import truth_edit as TE              # noqa: E402
from dreg_verify.ui import contracts as CT            # noqa: E402
from dreg_verify.ui import dialogs as D               # noqa: E402
from dreg_verify.ui import names as N                 # noqa: E402
from dreg_verify.ui import signal_list as SL          # noqa: E402
from dreg_verify.ui import terms as T                 # noqa: E402
from dreg_verify.ui import theme as TH                # noqa: E402

Qt = QtCore.Qt
ACCEPTED = QtWidgets.QDialog.Accepted


# ─────────────────────────── 夹具 ───────────────────────────
@pytest.fixture
def mk():
    """建对话框并保证收场关掉（offscreen 下残留窗口会让后面的 grab 截到别人的画面）。"""
    H.app()
    made = []

    def _mk(cls, *a, **k):
        dlg = cls(*a, **k)
        dlg.resize(max(dlg.width(), 460), max(dlg.height(), 220))
        made.append(dlg)
        return dlg
    yield _mk
    for dlg in made:
        dlg.close()
        dlg.deleteLater()
    H.app().processEvents()


def _open(dlg):
    """非模态打开（真实 show，不阻塞）—— 之后的 QTest 点击才落在真控件上。"""
    dlg.open()
    H.app().processEvents()
    return dlg


def _click_check(w):
    """点勾选框 / 单选钮：必须点在**指示器**上——QCheckBox / QRadioButton 的 hitButton 只覆盖
    「指示器 + 文字」，`H.click` 的默认「控件正中」落在右边空白里，事件会被丢掉（静默不生效）。"""
    H.click(w, (8, max(2, w.height() // 2)))
    H.app().processEvents()
    return w


def _still_open(dlg):
    return dlg.isVisible() and dlg.result() != ACCEPTED


def _mux_rows():
    return [{"base": "reg_lna", "width": 4, "value": 3},
            {"base": "reg_mix", "width": 8, "value": 0x2A, "override": 0x10}]


# ═════════════════ ① 三处确认（C-089/C-090 · C-102/C-103/C-036 · C-094/C-095）═════════════════
def test_dlg_confirm_c090_clear_text_and_default_is_no(mk):
    dlg = _open(mk(D.ConfirmDialog, D.CONFIRM_CLEAR))
    assert dlg.objectName() == N.DLG_CONFIRM
    assert dlg.windowTitle() == D.TITLES["confirm_clear"]
    assert H.find(dlg, N.DLG_CONFIRM_TEXT).text() == T.TRUTH_CONFIRM_CLEAR
    # C-090：默认按钮 = 否（手滑回车不该把本信号清成零用例）
    assert dlg.cancel_btn.isDefault() and not dlg.ok_btn.isDefault()
    assert os.path.exists(H.shot(dlg, "dlg_confirm_clear"))


def test_dlg_confirm_c103_del_neg_names_before_count(mk):
    names = ["U0_hand　└ 自定义命名", "T3_NEG　└ 手调过错值"]
    dlg = _open(mk(D.ConfirmDialog, D.CONFIRM_DEL_NEG, 2, names))
    body = H.find(dlg, N.DLG_CONFIRM_TEXT).text()
    assert body == T.TRUTH_CONFIRM_DEL_NEG_FMT.format(n=2)      # C-103 加强文案（手调过错值会丢）
    lst = H.find(dlg, N.DLG_CONFIRM_NAMES)
    assert [lst.item(i).text() for i in range(lst.count())] == names
    count = H.find(dlg, N.DLG_CONFIRM_COUNT)
    # C-270 / I-20：点名在前、计数在后（版面顺序 + 文本顺序两头都验）
    assert dlg.lay.indexOf(lst) < dlg.lay.indexOf(count)
    detail = dlg.detailedText()
    assert detail.index(names[0]) < detail.index(count.text())
    assert dlg.cancel_btn.isDefault()
    assert os.path.exists(H.shot(dlg, "dlg_confirm_del_neg"))


def test_dlg_confirm_c036_del_neg_plain_text_when_nothing_protected(mk):
    dlg = mk(D.ConfirmDialog, D.CONFIRM_DEL_NEG, 0)
    assert dlg.text() == T.DLG_CONFIRM_DEL_NEG_PLAIN_FMT.format(n=0)
    assert dlg.names_list is None


def test_dlg_confirm_c095_auto_fill_says_lose_independent_check(mk):
    dlg = _open(mk(D.ConfirmDialog, D.CONFIRM_AUTO_FILL))
    body = H.find(dlg, N.DLG_CONFIRM_TEXT).text()
    assert body == T.TRUTH_CONFIRM_AUTO_FILL
    assert "独立核对" in body                                    # 采信表达式 = 失去独立核对意义
    assert os.path.exists(H.shot(dlg, "dlg_confirm_auto_fill"))


def test_dlg_confirm_click_yes_and_no_give_bool(mk):
    yes = _open(mk(D.ConfirmDialog, D.CONFIRM_CLEAR))
    H.click(yes.ok_btn)
    assert yes.answer() is True
    no = _open(mk(D.ConfirmDialog, D.CONFIRM_CLEAR))
    H.click(no.cancel_btn)
    assert no.answer() is False


def test_dlg_confirm_rejects_unknown_kind():
    with pytest.raises(ValueError):
        D.ConfirmDialog("whatever")


def test_dlg_confirm_protected_names_come_from_edits(mk):
    """点名用的名字 + 原因由 `edits.protected_negatives` 判定，本模块只拼展示串。"""
    cols = [{"name": "T1", "neg": False, "auto": 1, "auto_w": 4, "exp": None, "vals": {}},
            {"name": "U9_mine", "neg": True, "auto": 1, "auto_w": 4, "exp": 0, "vals": {}},
            {"name": "T1_NEG", "neg": True, "auto": 1, "auto_w": 4, "exp": None, "vals": {}}]
    got = D.protected_negative_names(cols)
    assert any(x.startswith("U9_mine") for x in got)
    assert not any(x.startswith("T1_NEG") for x in got)         # 自动造的、没手调过 → 不用点名


# ═════════════════ ② 重命名列（C-091 / C-092 / C-093）═════════════════
def test_dlg_rename_col_c091_valid_name_accepts(mk):
    dlg = _open(mk(D.RenameColumnDialog, "", ["T1", "T2"]))
    edit = H.find(dlg, N.DLG_RENAME_COL_EDIT)
    H.type_text(edit, "corner_hi")
    H.click(dlg.ok_btn)
    assert dlg.answer() is True
    assert dlg.final_name() == "corner_hi"


def test_dlg_rename_col_c092_negative_gets_neg_suffix(mk):
    dlg = mk(D.RenameColumnDialog, "", ["T1"], True)
    H.type_text(H.find(dlg, N.DLG_RENAME_COL_EDIT), "mine")
    assert dlg.final_name() == "mine_NEG"


@pytest.mark.parametrize("bad,why", [("", "空名"), ("T7", "保留名"), ("T2", "重名")])
def test_dlg_rename_col_c092_invalid_keeps_dialog_open(mk, bad, why):
    """三道关任一条不过：错误就地显示在 DLG_RENAME_COL_ERROR，框**不关**。"""
    dlg = _open(mk(D.RenameColumnDialog, "T1", ["T2", "T3"]))
    edit = H.find(dlg, N.DLG_RENAME_COL_EDIT)
    edit.clear()
    if bad:
        H.type_text(edit, bad)
    err = H.find(dlg, N.DLG_RENAME_COL_ERROR)
    assert err.text(), "即时校验没报错：%s（%s）" % (bad, why)
    H.click(dlg.ok_btn)
    assert _still_open(dlg), "非法名 %s（%s）按了确定竟然把框关了" % (bad, why)
    assert err.text() == TE.check_col_name(bad, ["T2", "T3"])[1]
    assert dlg.final_name() == ""
    if bad == "T7":
        H.shot(dlg, "dlg_rename_col_error")


def test_dlg_rename_col_c093_auto_col_refusal_text_exists():
    """自动生成的 T 列拒改名由调用方在开框前判定，文案是 terms 里这一条（C-093）。"""
    assert T.TRUTH_RENAME_AUTO_REFUSED
    assert "复制列" in T.TRUTH_RENAME_AUTO_REFUSED


def test_dlg_rename_col_shot(mk):
    dlg = _open(mk(D.RenameColumnDialog, "U0", ["T1", "T2"]))
    assert os.path.exists(H.shot(dlg, "dlg_rename_col"))


# ═════════════════ ③ mux 数据值整表（C-110 / C-113）═════════════════
def test_dlg_mux_data_c110_one_row_per_data_base(mk):
    dlg = _open(mk(D.MuxDataDialog, _mux_rows()))
    table = H.find(dlg, N.DLG_MUX_DATA_TABLE)
    assert table.rowCount() == 2 and table.columnCount() == 4
    assert H.header_texts(table) == list(T.DLG_MUX_DATA_HEADERS)
    rows = H.table_texts(table, cols=(0, 1, 2))
    assert rows[0] == ["reg_lna", "4", "0b0011"]
    assert rows[1] == ["reg_mix", "8", "0x2A"]
    # 已手填过的值预填进「新值」；没手填过的留空 = 自动分配
    assert dlg.editor("reg_lna").text() == ""
    assert dlg.editor("reg_mix").text() == "0x10"
    assert os.path.exists(H.shot(dlg, "dlg_mux_data"))


def test_dlg_mux_data_c110_typed_values_parse_eight_forms(mk):
    dlg = _open(mk(D.MuxDataDialog, _mux_rows()))
    for base, text, want in (("reg_lna", "16'hA", 10), ("reg_mix", "0b1010", 10)):
        ed = H.find(dlg, D.fmt_mux_data_edit(base))
        ed.clear()
        H.type_text(ed, text)
        assert dlg.values()[base] == want
    H.click(dlg.ok_btn)
    assert dlg.answer() is True


def test_dlg_mux_data_c110_empty_means_back_to_auto(mk):
    dlg = mk(D.MuxDataDialog, _mux_rows())
    dlg.editor("reg_mix").clear()
    assert dlg.values() == {"reg_lna": None, "reg_mix": None}


def test_dlg_mux_data_c110_bad_form_marks_red_and_keeps_open(mk):
    """非法写法：输入框就地标红 + 错误行点名原文，框**不关**（绝不静默吞成 0）。"""
    dlg = _open(mk(D.MuxDataDialog, _mux_rows()))
    ed = H.find(dlg, D.fmt_mux_data_edit("reg_lna"))
    ed.clear()
    H.type_text(ed, "0b")                      # 只有前缀没数字 —— parse_int 明确拒收
    H.click(dlg.ok_btn)
    assert _still_open(dlg)
    assert TH.BAD_BG in ed.styleSheet(), "非法写法没有就地标红"
    err = H.find(dlg, N.DLG_MUX_DATA_ERROR)
    assert "0b" in err.text()
    assert os.path.exists(H.shot(dlg, "dlg_mux_data_error"))
    # 改成合法写法后红色撤掉、框能关
    ed.clear()
    H.type_text(ed, "0x3")
    assert TH.BAD_BG not in ed.styleSheet()
    H.click(dlg.ok_btn)
    assert dlg.answer() is True


def test_dlg_mux_data_c110_apply_to_goes_through_edits(mk):
    """落盘语义唯一入口 = `edits.set_mux_data_value`（空串 = 清空、该信号没有手填就整条删掉）。"""
    dlg = mk(D.MuxDataDialog, _mux_rows())
    dlg.editor("reg_lna").setText("0x3")
    dlg.editor("reg_mix").setText("")
    mux_data = {}
    changed, cleared = dlg.apply_to(mux_data, "sig_x", "src_x", "SIG_X")
    assert (changed, cleared) == (1, 1)
    assert mux_data["sig_x"]["data"] == {"reg_lna": 3}
    want = {}
    ED.set_mux_data_value(want, "sig_x", "src_x", "SIG_X", "reg_lna", 4, "0x3")
    assert mux_data == want


def test_dlg_mux_data_value_format_roundtrips_parse_int():
    """「当前值」列的写法必须能被 `truth_edit.parse_int` 原样读回（C-084 同一套）。"""
    for width, val in ((1, 1), (3, 5), (4, 10), (8, 0x2A), (12, 0x3FF), (16, 0xBEEF)):
        assert TE.parse_int(D.fmt_value(val, width)) == val
    assert D.fmt_value(None, 4) == ""


def test_dlg_mux_data_c113_collision_text_belongs_to_terms():
    """撞值提示（C-113）由调用方重析后给，文案是 terms 这一条。"""
    assert T.TRUTH_MUX_COLLISION and "相同值" in T.TRUTH_MUX_COLLISION


def test_dlg_mux_data_empty_rows_says_so(mk):
    dlg = mk(D.MuxDataDialog, [])
    assert H.find(dlg, N.DLG_MUX_DATA_TABLE).rowCount() == 0
    assert dlg.values() == {}


# ═════════════════ ④ 列设置（C-008 / C-046）═════════════════
def test_dlg_columns_c008_selection_shape(mk):
    dlg = _open(mk(D.ColumnsDialog, ["name", "status"]))
    got = dlg.selection()
    assert set(got) == set(D.LIST_COLUMN_KEYS)
    assert got["name"] is True and got["status"] is True and got["expr"] is False
    assert "check" not in got, "勾选列常驻第一列，不进列设置"
    assert os.path.exists(H.shot(dlg, "dlg_columns"))


def test_dlg_columns_c046_space_toggles_optional_column(mk):
    dlg = _open(mk(D.ColumnsDialog, ["name"]))
    lst = H.find(dlg, N.DLG_COLUMNS_LIST)
    row = next(i for i in range(lst.count()) if lst.item(i).data(Qt.UserRole) == "expr")
    lst.setCurrentRow(row)
    H.keys(lst, "Space")                        # 真实按键，不是 setCheckState
    assert dlg.selection()["expr"] is True


def test_dlg_columns_keys_match_contracts():
    """列键与顺序取自 terms.LIST_HEADERS —— 它必须与 contracts.ListCol 同序（本模块不 import contracts）。"""
    assert D.ALL_LIST_COLUMN_KEYS == tuple(CT.LIST_COL_KEYS[c] for c in CT.ListCol)
    assert D.LIST_COLUMN_KEYS == tuple(k for k in D.ALL_LIST_COLUMN_KEYS if k != "check")
    assert set(CT.LIST_COL_KEYS[c] for c in CT.LIST_OPTIONAL) <= set(D.LIST_COLUMN_KEYS)


def test_dlg_columns_same_object_names_as_signal_list_version(mk):
    """C2-int 会把 signal_list 那一份删掉换成 import 这里：objectName / 标题 / 返回形状必须一致。"""
    mine = mk(D.ColumnsDialog, [CT.ListCol.NAME, CT.ListCol.STATUS])
    theirs = mk(SL.ColumnsDialog, {CT.ListCol.NAME, CT.ListCol.STATUS})
    assert mine.objectName() == theirs.objectName() == N.DLG_COLUMNS
    assert mine.windowTitle() == theirs.windowTitle() == T.DLG_COLUMNS_TITLE
    assert mine.selection() == theirs.selection()


# ═════════════════ ⑤ 粘贴名单勾选（C-290）═════════════════
def test_dlg_paste_names_c290_parses_lines_to_bare_lower_names(mk):
    dlg = _open(mk(D.PasteNamesDialog))
    ed = H.find(dlg, N.DLG_PASTE_NAMES_TEXT)
    # 换行用真实 Return 键（`type_text` 逐字符发 "\n" 会在 QTest 里炸掉整个进程）
    H.type_text(ed, "D_BT_LP_LNA_ITRIM[3:0]")
    H.keys(ed, "Return")
    H.type_text(ed, "# comment")
    H.keys(ed, "Return")
    H.type_text(ed, "  d_wl_rf_tx_gain  ")
    assert dlg.names() == ["d_bt_lp_lna_itrim", "d_wl_rf_tx_gain"]
    assert os.path.exists(H.shot(dlg, "dlg_paste_names"))


def test_dlg_paste_names_c290_result_line_names_missing(mk):
    dlg = mk(D.PasteNamesDialog)
    msg = dlg.set_result(3, ["no_such_a", "no_such_b"])
    assert H.find(dlg, N.DLG_PASTE_NAMES_RESULT).text() == msg
    assert "no_such_a" in msg and "no_such_b" in msg      # C-270：找不到的必须点名
    assert dlg.detailedText() == msg


def test_dlg_paste_names_empty_keeps_dialog_open(mk):
    dlg = _open(mk(D.PasteNamesDialog))
    H.click(dlg.ok_btn)
    assert _still_open(dlg)
    assert H.find(dlg, N.DLG_PASTE_NAMES_RESULT).text()


def test_dlg_paste_names_same_parse_as_signal_list_version(mk):
    mine, theirs = mk(D.PasteNamesDialog), mk(SL.PasteNamesDialog)
    text = "A_B[7:0]\n#x\nc_d\n"
    mine.editor.setPlainText(text)
    theirs.editor.setPlainText(text)
    assert mine.names() == theirs.names()
    assert mine.objectName() == theirs.objectName() == N.DLG_PASTE_NAMES


# ═════════════════ ⑥ 预设（C-291）═════════════════
def test_dlg_presets_c291_save_shows_overwrite_hint(mk):
    # QTest.keyClicks 只认 ASCII（非 ASCII 字符会把整个进程打崩），预设名用 ASCII 造
    dlg = _open(mk(D.PresetsDialog, D.PRESET_SAVE, ["only_bad", "weekly"]))
    name = H.find(dlg, N.DLG_PRESETS_NAME)
    H.type_text(name, "weekly")
    hint = H.find(dlg, N.DLG_PRESETS_HINT)
    assert "覆盖" in hint.text() and "weekly" in hint.text()
    assert dlg.preset_name() == "weekly"
    assert os.path.exists(H.shot(dlg, "dlg_presets_save"))
    name.clear()
    H.type_text(name, "brand_new")
    assert hint.text() == ""


def test_dlg_presets_c291_save_empty_name_keeps_dialog_open(mk):
    dlg = _open(mk(D.PresetsDialog, D.PRESET_SAVE, ["a"]))
    H.click(dlg.ok_btn)
    assert _still_open(dlg)


def test_dlg_presets_c291_manage_select_and_delete(mk):
    dlg = _open(mk(D.PresetsDialog, D.PRESET_MANAGE, ["只看有问题", "本周要交的", "全量"]))
    lst = H.find(dlg, N.DLG_PRESETS_LIST)
    lst.setCurrentRow(1)
    assert dlg.selected() == "本周要交的"
    H.click(H.find(dlg, N.DLG_PRESETS_DELETE_BTN))
    assert dlg.deleted() == ("本周要交的",)
    assert dlg.remaining() == ("只看有问题", "全量")
    assert H.find(dlg, N.DLG_PRESETS_HINT).text() == ""
    assert os.path.exists(H.shot(dlg, "dlg_presets_manage"))


def test_dlg_presets_rejects_unknown_mode():
    with pytest.raises(ValueError):
        D.PresetsDialog("nope")


# ═════════════════ ⑦ 重复 assert 标号（C-164）═════════════════
def test_dlg_dup_labels_c164_lists_label_and_two_signals(mk):
    dups = [("assert_12_T3", "d_wl_rf_tx_gain", "d_wl_rf_rx_gain"),
            ("assert_12_T4", "sig_a", "sig_b")]
    dlg = _open(mk(D.DupLabelsDialog, dups))
    body = H.find(dlg, N.DLG_DUP_LABELS_TEXT).toPlainText()
    for part in ("assert_12_T3", "d_wl_rf_tx_gain", "d_wl_rf_rx_gain", "assert_12_T4"):
        assert part in body
    assert dlg.cancel_btn.isDefault(), "写非法 SV 的默认按钮必须是取消"
    assert dlg.ok_btn.text() == T.DLG_DUP_LABELS_CONTINUE
    assert os.path.exists(H.shot(dlg, "dlg_dup_labels"))


def test_dlg_dup_labels_c164_accepts_exports_rendered_rows(mk):
    """行文本也可由调用方用 `exports.dup_label_text` 渲好传进来（本模块不 import exports）。"""
    dups = [("assert_1_T1", "a", "b")]
    dlg = mk(D.DupLabelsDialog, (), EX.dup_label_text(dups))
    assert "assert_1_T1" in dlg.text() and "a / b" in dlg.text()


def test_dlg_dup_labels_c164_continue_returns_true(mk):
    dlg = _open(mk(D.DupLabelsDialog, [("assert_1_T1", "a", "b")]))
    H.click(dlg.ok_btn)
    assert dlg.answer() is True


# ═════════════════ ⑧ 导入结果（C-193 / C-194 / C-195）═════════════════
def test_dlg_import_report_c194_names_and_reasons_before_counts(mk):
    missing = [("d_wl_rf_no_such", "当前表里没有这个信号"), ("d_bt_gone", "该页不存在")]
    counts = [T.EXPORT_CONFIG_DONE_FMT.format(k=3, cov="全面", np=2, nf=1, ne=4, nx=12)]
    dlg = _open(mk(D.ImportReportDialog, missing, counts))
    head = H.find(dlg, N.DLG_IMPORT_REPORT_HEAD)
    lst = H.find(dlg, N.DLG_IMPORT_REPORT_LIST)
    cnt = H.find(dlg, N.DLG_IMPORT_REPORT_COUNT)
    txt = H.find(dlg, N.DLG_IMPORT_REPORT_TEXT)
    # I-20 / C-270：版面顺序 = 点名块 → 计数 → 逐段报数
    assert dlg.lay.indexOf(head) < dlg.lay.indexOf(lst) < dlg.lay.indexOf(cnt) < dlg.lay.indexOf(txt)
    rows = [lst.item(i).text() for i in range(lst.count())]
    assert "d_wl_rf_no_such" in rows[0] and "当前表里没有这个信号" in rows[0]
    assert "该页不存在" in rows[1]
    assert "2" in cnt.text()
    body = dlg.text()
    assert body.index("d_wl_rf_no_such") < body.index(cnt.text()) < body.index(counts[0])
    assert dlg.missing_names() == ("d_wl_rf_no_such", "d_bt_gone")
    assert os.path.exists(H.shot(dlg, "dlg_import_report"))


def test_dlg_import_report_c193_c195_notes_on_top(mk):
    notes = [T.EXPORT_IMPORT_MISMATCH_FMT.format(cfg="别人的表.xlsx", cur="我的表.xlsx"),
             T.EXPORT_IMPORT_BAD_FILE]
    dlg = _open(mk(D.ImportReportDialog, [], ["恢复了 4 个信号"], notes))
    lab = H.find(dlg, N.DLG_IMPORT_REPORT_NOTES)
    assert dlg.lay.indexOf(lab) < dlg.lay.indexOf(H.find(dlg, N.DLG_IMPORT_REPORT_HEAD))
    assert "别人的表.xlsx" in lab.text() and "dreg_verify_config" in lab.text()
    assert os.path.exists(H.shot(dlg, "dlg_import_report_notes"))


def test_dlg_import_report_c194_long_list_is_capped(mk):
    missing = [("sig_%03d" % i, "没有") for i in range(TH.IMPORT_MISSING_LIST_MAX + 5)]
    dlg = mk(D.ImportReportDialog, missing)
    lst = H.find(dlg, N.DLG_IMPORT_REPORT_LIST)
    assert lst.count() == TH.IMPORT_MISSING_LIST_MAX + 1        # 末行是「只列前 N 个」
    cnt = H.find(dlg, N.DLG_IMPORT_REPORT_COUNT)
    assert str(len(missing)) in cnt.text()


def test_dlg_import_report_no_missing_says_so(mk):
    dlg = mk(D.ImportReportDialog, [], ["恢复了 4 个信号"])
    assert H.find(dlg, N.DLG_IMPORT_REPORT_HEAD).text() == \
        T.DLG_IMPORT_NONE
    assert dlg.cancel_btn is None                               # 只有「知道了」


# ═════════════════ ⑨ 批量填期望（C-298）═════════════════
def test_dlg_batch_fill_c298_three_modes_and_scope(mk):
    dlg = _open(mk(D.BatchFillDialog, 3, 25))
    const = H.find(dlg, N.DLG_BATCH_FILL_MODE_CONST)
    auto = H.find(dlg, N.DLG_BATCH_FILL_MODE_AUTO)
    clear = H.find(dlg, N.DLG_BATCH_FILL_MODE_CLEAR)
    assert const.isChecked() and not auto.isChecked() and not clear.isChecked()
    val = H.find(dlg, N.DLG_BATCH_FILL_VALUE)
    assert val.isEnabled()
    _click_check(auto)                              # 真实点击切模式
    assert not val.isEnabled()
    assert dlg.spec() == {"mode": "auto", "value": None, "scope": "selected"}
    _click_check(H.find(dlg, N.DLG_BATCH_FILL_SCOPE_ALL))
    assert dlg.spec()["scope"] == "all"
    assert "3" in H.find(dlg, N.DLG_BATCH_FILL_SCOPE_SELECTED).text()
    assert "25" in H.find(dlg, N.DLG_BATCH_FILL_SCOPE_ALL).text()
    assert os.path.exists(H.shot(dlg, "dlg_batch_fill"))


def test_dlg_batch_fill_c298_const_value_parses(mk):
    dlg = _open(mk(D.BatchFillDialog, 0, 25))
    assert not H.find(dlg, N.DLG_BATCH_FILL_SCOPE_SELECTED).isEnabled()
    assert dlg.spec()["scope"] == "all"             # 没选中列时自动退到「全部」
    H.type_text(H.find(dlg, N.DLG_BATCH_FILL_VALUE), "16'hA")
    H.click(dlg.ok_btn)
    assert dlg.answer() is True
    assert dlg.spec() == {"mode": "const", "value": 10, "scope": "all"}


@pytest.mark.parametrize("bad", ["0b", "zz!"])
def test_dlg_batch_fill_c298_bad_value_marks_red_and_keeps_open(mk, bad):
    dlg = _open(mk(D.BatchFillDialog, 2, 9))
    val = H.find(dlg, N.DLG_BATCH_FILL_VALUE)
    H.type_text(val, bad)
    H.click(dlg.ok_btn)
    assert _still_open(dlg)
    assert TH.BAD_BG in val.styleSheet()
    assert H.find(dlg, N.DLG_BATCH_FILL_ERROR).text()


def test_dlg_batch_fill_c298_const_without_value_keeps_open(mk):
    """「填固定值」却一个字没填 —— parse_int 的「空串=0」不能在这里静默兜底。"""
    dlg = _open(mk(D.BatchFillDialog, 2, 9))
    H.click(dlg.ok_btn)
    assert _still_open(dlg)


# ═════════════════ ⑩ .sv 导出选项（C-159 / C-160 / C-161 / C-163）═════════════════
def test_dlg_export_options_c159_c160_c161_three_flags_and_scope(mk):
    dlg = _open(mk(D.ExportOptionsDialog, {"scope": "pos", "comments": True,
                                           "sv_summary": False, "owner_in_msg": False}))
    cmt = H.find(dlg, N.DLG_EXPORT_OPT_COMMENTS)
    smy = H.find(dlg, N.DLG_EXPORT_OPT_SV_SUMMARY)
    own = H.find(dlg, N.DLG_EXPORT_OPT_OWNER_IN_MSG)
    assert (cmt.text(), smy.text(), own.text()) == (T.EXPORT_SV_OPTIONS["comments"],
                                                    T.EXPORT_SV_OPTIONS["sv_summary"],
                                                    T.EXPORT_SV_OPTIONS["owner_in_msg"])
    assert cmt.isChecked() and not smy.isChecked()
    assert H.find(dlg, N.DLG_EXPORT_SCOPE_POS).isChecked()
    _click_check(smy)                                       # C-160 末尾汇总
    _click_check(H.find(dlg, N.DLG_EXPORT_SCOPE_NEG))
    assert dlg.options() == {"scope": "neg", "comments": True,
                             "sv_summary": True, "owner_in_msg": False}
    assert os.path.exists(H.shot(dlg, "dlg_export_options"))


def test_dlg_export_options_c163_defaults_come_from_caller_and_match_exports():
    """dialogs 不准 import exports：兜底默认值必须与 `exports.EXPORT_OPTION_DEFAULTS` 逐键相等。"""
    assert D.FALLBACK_EXPORT_OPTIONS == EX.EXPORT_OPTION_DEFAULTS
    assert D.EXPORT_SCOPES == tuple(EX.SCOPE_LABEL)
    assert set(D.EXPORT_FLAGS) | {"scope"} == set(EX.EXPORT_OPTION_DEFAULTS)


def test_dlg_export_options_shape_matches_exports_options(mk):
    dlg = mk(D.ExportOptionsDialog, EX.load_export_options({}))
    assert set(dlg.options()) == set(EX.EXPORT_OPTION_DEFAULTS)
    assert dlg.options() == EX.EXPORT_OPTION_DEFAULTS


def test_dlg_dialogs_never_imports_exports_or_contracts():
    src = io.open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                               "dreg_verify", "ui", "dialogs.py"), encoding="utf-8").read()
    head = src.split("__all__")[0]
    for banned in ("import exports", "from dreg_verify import exports",
                   "from . import contracts", "import contracts"):
        assert banned not in head, "dialogs.py 不该 import %s（架构 §1.1）" % banned


# ═════════════════ 全局：objectName / 标题 / 非阻塞 ═════════════════
def _all_dialogs(mk):
    """每个对话框各造一个（参数取「控件最全」的那一支）。"""
    return [
        mk(D.ConfirmDialog, D.CONFIRM_DEL_NEG, 2, ["U0_hand　└ 自定义命名"]),
        mk(D.RenameColumnDialog, "U0", ["T1"]),
        mk(D.MuxDataDialog, _mux_rows()),
        mk(D.ColumnsDialog, ["name"]),
        mk(D.PasteNamesDialog),
        mk(D.PresetsDialog, D.PRESET_SAVE, ["a"]),
        mk(D.PresetsDialog, D.PRESET_MANAGE, ["a"]),
        mk(D.DupLabelsDialog, [("assert_1_T1", "a", "b")]),
        mk(D.ImportReportDialog, [("x", "没有")], ["c"], ["n"]),
        mk(D.BatchFillDialog, 2, 9),
        mk(D.ExportOptionsDialog, EX.EXPORT_OPTION_DEFAULTS),
    ]


#: 动态拼名的 objectName 前缀（`names.fmt_mux_data_edit`；注册表里只有带 `_` 的模板常量）
DYNAMIC_NAME_PREFIXES = ("dlg_mux_data_edit_",)


def test_dlg_all_object_names_registered(mk):
    """I-14：每个对话框及其主要控件的 objectName ∈ names.all_names()。

    C2-int 起 `dialogs.PENDING_NAMES` 已整块搬进 `ui/names.py`，白名单只剩动态拼名的前缀
    —— 冒出任何别的名字就是「视图里写了裸字符串」。"""
    registered = set(N.all_names().values())
    seen = set()
    for dlg in _all_dialogs(mk):
        for w in H.find_all(dlg, QtWidgets.QWidget):
            nm = w.objectName()
            if nm and not nm.startswith("qt_"):          # Qt 内部件（viewport / 滚动条容器）不算
                seen.add(nm)
    unknown = {nm for nm in seen
               if nm not in registered and not any(nm.startswith(p) for p in DYNAMIC_NAME_PREFIXES)}
    assert not unknown, "不在 names.py 里的 objectName：%s" % sorted(unknown)
    assert seen & registered, "一个 names.py 里的名字都没用上？"
    # 反过来：names.py 里每个 DLG_* 都真被某个对话框用上（别在注册表里留没人用的死名字）
    dlg_names = {k: v for k, v in N.all_names().items() if k.startswith("DLG_")}
    assert len(dlg_names) >= 45, "DLG_* 的名字只剩 %d 个了" % len(dlg_names)
    unused = sorted(k for k, v in dlg_names.items() if v not in seen)
    assert not unused, "names.py 里这些 DLG_* 没被任何对话框用到：%s" % unused


def test_dlg_object_names_are_dlg_prefixed_snake_case():
    for key, val in N.all_names().items():
        if not key.startswith("DLG_"):
            continue
        assert val.startswith("dlg_"), "%s = %r" % (key, val)
        assert val == val.lower() and " " not in val
    assert N.fmt_mux_data_edit("REG_A") == "dlg_mux_data_edit_reg_a"


def test_dlg_titles_stable_for_harness(monkeypatch, mk):
    """`auto_dialogs(answers={标题: 答案})` 必须能按 windowTitle 命中每一个对话框。"""
    assert len(set(D.TITLES.values())) == len(D.TITLES), "标题有重复，harness 按标题答就会串台"
    for dlg in _all_dialogs(mk):
        assert dlg.windowTitle() in set(D.TITLES.values()), \
            "%s 的标题 %r 不在 dialogs.TITLES 里" % (type(dlg).__name__, dlg.windowTitle())
    rec = H.auto_dialogs(monkeypatch)
    for title in D.TITLES.values():
        dlg = mk(QtWidgets.QDialog)
        dlg.setWindowTitle(title)
        dlg.exec()
    assert rec.titles == list(D.TITLES.values())
    for title in D.TITLES.values():
        assert rec.saw(title), "harness 没记到标题 %r" % title


def test_dlg_answers_hit_by_title_exact_and_substring(monkeypatch, mk):
    """answers 的键既能写全标题，也能写子串（harness `_lookup` 的两条路）。"""
    rec = H.auto_dialogs(monkeypatch, answers={
        D.TITLES["confirm_clear"]: QtWidgets.QDialog.Rejected,       # 全标题
        "导出 .sv": QtWidgets.QDialog.Rejected,                       # 子串
    })
    assert D.ConfirmDialog.ask(D.CONFIRM_CLEAR) is False
    assert D.ExportOptionsDialog.ask(EX.EXPORT_OPTION_DEFAULTS) is None
    assert D.ConfirmDialog.ask(D.CONFIRM_AUTO_FILL) is True          # 没给答案 → 默认 Accepted
    assert rec.count(D.TITLES["confirm_clear"]) == 1


def test_dlg_ask_entrypoints_return_shapes_under_harness(monkeypatch):
    """十个入口在 harness 下都不阻塞，且返回形状固定（C2-int 接线时照这个写）。"""
    H.auto_dialogs(monkeypatch)
    assert D.confirm(D.CONFIRM_CLEAR) is True
    assert D.RenameColumnDialog.ask("U0", ["T1"]) == "U0"
    assert D.MuxDataDialog.ask(_mux_rows()) == {"reg_lna": None, "reg_mix": 16}
    assert set(D.ColumnsDialog.ask(["name"])) == set(D.LIST_COLUMN_KEYS)
    assert D.PasteNamesDialog.ask(text="a_b[1:0]\n") == ["a_b"]
    assert D.PresetsDialog.ask_save(["x"]) == ""
    assert D.PresetsDialog.ask_manage(["x"]) == ("x", ())
    assert D.DupLabelsDialog.ask([("assert_1_T1", "a", "b")]) is True
    assert D.ImportReportDialog.ask([("x", "没有")]) is True
    assert D.BatchFillDialog.ask(2, 9) == {"mode": "const", "value": None, "scope": "selected"}
    assert D.ExportOptionsDialog.ask(EX.EXPORT_OPTION_DEFAULTS) == EX.EXPORT_OPTION_DEFAULTS


def test_dlg_open_is_non_modal_and_exec_not_overridden(mk):
    """两种用法：`exec()`（阻塞，harness 拦它）与 `open()`（非模态、不阻塞）。

    子类**不许**自己定义 `exec` —— `auto_dialogs` 打的是 `QtWidgets.QDialog.exec`，
    子类一旦覆盖就拦不住，offscreen 下会真卡死。"""
    for cls in (D.ConfirmDialog, D.RenameColumnDialog, D.MuxDataDialog, D.ColumnsDialog,
                D.PasteNamesDialog, D.PresetsDialog, D.DupLabelsDialog, D.ImportReportDialog,
                D.BatchFillDialog, D.ExportOptionsDialog, D._BaseDialog):
        assert "exec" not in vars(cls) and "exec_" not in vars(cls), "%s 自己覆盖了 exec" % cls.__name__
    dlg = mk(D.ConfirmDialog, D.CONFIRM_CLEAR)
    dlg.open()
    H.app().processEvents()
    assert dlg.isVisible() and not dlg.isModal()
    assert dlg.windowModality() == Qt.NonModal


def test_dlg_no_static_message_boxes_inside():
    """本模块内部不调任何 QMessageBox 静态法（那是 offscreen 下真会阻塞的入口）。"""
    src = io.open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                               "dreg_verify", "ui", "dialogs.py"), encoding="utf-8").read()
    code = "\n".join(l for l in src.splitlines() if not l.strip().startswith("#"))
    for banned in ("QMessageBox.question(", "QMessageBox.warning(", "QMessageBox.information(",
                   "QMessageBox.critical(", "QInputDialog.", "QEventLoop("):
        assert banned not in code, "dialogs.py 里有阻塞入口 %s" % banned


def test_dlg_i12_i13_copy_has_no_forbidden_terms():
    """I-12 / I-13：对话框文案里不许出现术语红线词。

    C2-int 起文案都在 `terms.py`，这条按 `DLG_*` 前缀从注册表里取（以前扫的是本模块的 PENDING_TERMS）。"""
    blob = []

    def _walk(v):
        if isinstance(v, dict):
            for x in v.values():
                _walk(x)
        elif isinstance(v, (tuple, list)):
            for x in v:
                _walk(x)
        else:
            blob.append(str(v))

    copy = {k: v for k, v in T.all_copy().items() if k.startswith("DLG_")}
    assert len(copy) >= 35, "DLG_* 的文案只剩 %d 条了" % len(copy)
    for val in copy.values():
        _walk(val)
    # 「假绿」是 terms.py 明说的例外：出现时必须当场把它解释掉（= 驱不动，断言必过）
    hits = []
    for line in blob:
        for w in T.FORBIDDEN:
            if w not in line:
                continue
            if w == "假绿" and "驱不动，断言必过" in line:
                continue
            hits.append((w, line))
    assert not hits, "对话框文案里有术语红线词：%s" % hits
    text = "\n".join(blob).lower()
    for bad in ("git", "仓库", "traceback"):
        assert bad not in text


def test_dlg_no_pending_tables_left():
    """C2-int 之后 `dialogs.py` 不许再有影子定义表：文案只在 terms.py、名字只在 names.py。"""
    for attr in ("PENDING_TERMS", "PENDING_NAMES", "PENDING_NAME_PREFIXES"):
        assert not hasattr(D, attr), "dialogs.%s 还在，说明常量有两份" % attr
    # `_txt` / `_oname` 只剩「按键拼名」的动态取值，取不到必须立刻炸（不许静默回退）
    with pytest.raises(KeyError):
        D._txt("DLG_NO_SUCH_COPY_KEY")
    with pytest.raises(KeyError):
        D._oname("DLG_NO_SUCH_NAME_KEY")
