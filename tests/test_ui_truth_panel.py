# -*- coding: utf-8 -*-
"""test_ui_truth_panel.py —— 真值表**面板**（C3-c）：`ui/truth/panel.py`。

覆盖架构附录 A「TRUTH_PANEL」26 条：C-063 C-085 C-086 C-087 C-088 C-089 C-090 C-091 C-094
C-095 C-096 C-101 C-102 C-103 C-110 C-111 C-113 C-122 C-124 C-125 C-126 C-134 C-135 C-140
C-296 C-297，外加不变量 I-05（批量只写一次盘）/ I-14（objectName）/ I-18（当前线网只经 bus）/
I-20（名字在前、计数在后）。

口径（与 `test_ui_side_panel.py` / `test_ui_truth_view.py` 一致）：
  · offscreen 起**独立** `TruthPanel`（不起 MainWindow）；按钮一律 `ui_harness.click` 真点，
    菜单项走真 `QContextMenuEvent` + `QAction.trigger()`，不直接调私有槽；
  · state 是**真** `WorkbenchState`（持久化指到 tmp），provider 是真 `TopoutProvider`，
    信号全部来自两张 mirror（公开仓，**绝不出现真实信号名**）；
  · 对话框走 `ui_harness.auto_dialogs`：默认拦 `类.ask`，要验「默认按钮 = 否」的那条
    用 `answers={"ConfirmDialog.ask": H.REAL}` 放行真框，再由 `QDialog.exec` 那一层记标题。

PySide6 6.11 的坑（`test_ui_truth_view.py` 踩过）：`QTest.keyClicks` 发非 ASCII / `\\n` 会整
进程退出；`QMenu.exec` 真弹会挂 —— 面板用的是 `popup()`（不阻塞），测试另走 `build_context_menu`。
"""

import csv
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import ui_harness as H                                    # noqa: E402

pytest.importorskip("PySide6")

from PySide6 import QtCore, QtGui, QtWidgets              # noqa: E402

from dreg_verify import edits as ED                       # noqa: E402
from dreg_verify.ui import bus as BUS                     # noqa: E402
from dreg_verify.ui import contracts as CT                # noqa: E402
from dreg_verify.ui import dialogs as DLG                 # noqa: E402
from dreg_verify.ui import names as N                     # noqa: E402
from dreg_verify.ui import persist as P                   # noqa: E402
from dreg_verify.ui import state as ST                    # noqa: E402
from dreg_verify.ui import terms as T                     # noqa: E402
from dreg_verify.ui import theme as TH                    # noqa: E402
from dreg_verify.ui.truth import panel as PANEL           # noqa: E402
from dreg_verify.ui.truth import io as TIO                # noqa: E402

Qt = QtCore.Qt
TR = CT.TruthRole

#: mirror 上的对照信号（与 `test_ui_truth_model.py` / `test_ui_truth_view.py` 同一批）
LOGIC_SIG = ("btlp", "d_logic_bt_lp_rx_en")            # 4 条可编辑输入行 + 12 列
MUX_SIG = ("wl", "d_wl_rf_lo2g5g_mixer2g_trim")        # 13 case（5 条死分支）+ iddq 门控
MUX_SMALL = ("wl", "d_wl_rf_lp5g_gm_itrim")            # 2 case / 6 列，跑 mux 数据值同步快
RO_SIG = ("btlp", "pll_lock_indicator")                # 只读回读 → editable == ""（C-134）

_ALIVE = []            # 面板引用：Qt 父子链之外 Python 侧也得留着，免得被 GC 掉


# ─────────────────────────── 夹具 ───────────────────────────
@pytest.fixture(scope="module")
def qapp():
    return H.app()


@pytest.fixture(autouse=True)
def iso(monkeypatch, tmp_path):
    """持久化指到临时目录（本模块真的会写 edits 文件 —— I-05 要数写了几次）。"""
    H.isolate_settings(monkeypatch, tmp_path)


@pytest.fixture(autouse=True)
def no_swallowed_slot_errors(monkeypatch):
    """槽里抛的异常 Qt 只往 stderr 打一条就算了事 —— 那会让「按钮点了什么也没发生」变成一条**绿**测试。

    这里接管 `sys.excepthook`（PySide6 的槽异常走它）：本条用例里只要炸过一次就判红。
    """
    boom = []
    real = sys.excepthook

    def _hook(etype, value, tb):
        boom.append("%s: %s" % (etype.__name__, value))
        real(etype, value, tb)

    monkeypatch.setattr(sys, "excepthook", _hook)
    yield
    assert not boom, "槽里抛了异常（Qt 只打了 stderr，动作其实没做成）：%s" % "；".join(boom)


@pytest.fixture(autouse=True)
def cleanup():
    yield
    for w in _ALIVE:
        try:
            w.close_context_menu()
            w.set_state(None)          # 断开 state 信号：下一条用例的 state 别再喂到这块上
            w.close()
        except RuntimeError:           # pragma: no cover —— 已经被 Qt 侧销毁
            pass
    del _ALIVE[:]
    H.app().processEvents()


def make_state(kind):
    """真 `WorkbenchState` + 真 `TopoutProvider`（清单用 skeleton：只要名字对得上就够）。"""
    st = ST.WorkbenchState()
    assert st.load(H.mirror_path(kind)), "mirror %s 载不进来（夹具坏了）" % kind
    st.set_models(CT.DEFAULT_VIEW_ID, st.provider().skeleton_models())
    return st


def make_panel(sig=LOGIC_SIG, bus=None, size=(1100, 460), state=None):
    """(panel, state) —— 载好一个信号的面板。"""
    kind, name = sig
    st = state if state is not None else make_state(kind)
    p = PANEL.TruthPanel(state=st, bus=bus)
    p.resize(*size)
    p.show()
    _ALIVE.append(p)
    st.set_current(name)
    H.app().processEvents()
    assert p.current_name() == name and p.current_an(), "面板没装上 %s" % name
    return p, st


def btn(p, key):
    return H.find(p, getattr(N, "TRUTH_BTN_%s" % key.upper()), QtWidgets.QPushButton)


def click_btn(p, key):
    H.click(btn(p, key))
    H.app().processEvents()


def select_cols(p, cols, row=None):
    """真点选若干列（首列点一下，其余 Ctrl+点）—— 不调 selectionModel.select。"""
    r = p.model.rowCount() - 1 if row is None else row
    for i, c in enumerate(cols):
        H.click_cell(p.grid, r, c, modifier=(Qt.NoModifier if i == 0 else Qt.ControlModifier))
    H.app().processEvents()


def hint(p):
    return p.hint_text()[0]


def right_click(p, r, c):
    """真右键：`QContextMenuEvent` 发到 viewport（面板用 popup，不阻塞）。"""
    ix = p.model.index(r, c)
    p.grid.scrollTo(ix)
    H.app().processEvents()
    pos = p.grid.visualRect(ix).center()
    ev = QtGui.QContextMenuEvent(QtGui.QContextMenuEvent.Mouse, pos,
                                 p.grid.viewport().mapToGlobal(pos))
    QtWidgets.QApplication.sendEvent(p.grid.viewport(), ev)
    H.app().processEvents()
    return p.context_menu()


def menu_act(menu, key):
    name = N.fmt_truth_menu(key)
    return next(a for a in menu.actions() if a.objectName() == name)


# ═══════════════════ 工具条 → model（C-085…C-091）═══════════════════
@pytest.mark.contract("C-085", "C-086", "C-087", "C-088", "C-091")
def test_c085_to_c091_toolbar_actions_route_to_model(qapp):
    """C-085/086/087/088/091：五个列动作各走一次，每次都落在 model 上、且恰好一步撤销。"""
    p, _st = make_panel(LOGIC_SIG)
    m = p.model
    undo = m.undo_stack()
    n0 = m.columnCount()
    assert n0 > 1 and m.editable_kind() == "logic"

    # C-086 加列：新列名进提示条
    click_btn(p, "add_col")
    assert m.columnCount() == n0 + 1 and undo.count() == 1
    added = m.all_names()[-1]
    assert added in hint(p)

    # C-087 复制列：复制刚加的那条（先真点它一格当当前列）
    H.click_cell(p.grid, 0, m.columnCount() - 1)
    click_btn(p, "copy_col")
    assert m.columnCount() == n0 + 2 and undo.count() == 2

    # C-091 重命名：自动 T 列先拒绝（C-093 的面板面），手编列才弹框
    H.click_cell(p.grid, 0, 0)
    click_btn(p, "rename_col")
    assert hint(p) == T.TRUTH_RENAME_AUTO_REFUSED
    assert undo.count() == 2, "被拒的改名不许占一步撤销"
    H.click_cell(p.grid, 0, m.columnCount() - 1)
    with pytest.MonkeyPatch.context() as mp:
        H.auto_dialogs(mp, answers={"RenameColumnDialog.ask": "MY_CASE"})
        click_btn(p, "rename_col")
    assert "MY_CASE" in m.all_names() and undo.count() == 3

    # C-088 删列：选中两列一起删，名字进提示条
    select_cols(p, [0, 1])
    n1 = m.columnCount()
    click_btn(p, "del_col")
    assert m.columnCount() == n1 - 2 and undo.count() == 4

    # C-085 重新生成：丢弃自定义，回到出厂列集（仍是一步撤销）
    click_btn(p, "regen")
    assert undo.count() == 5
    assert m.all_names() == [c["name"] for c in ED.cols_from_vectors(p.current_an(), p._e_inputs)]
    assert "MY_CASE" not in m.all_names()
    undo.undo()
    assert "MY_CASE" in m.all_names(), "重新生成要能一步撤回去"


@pytest.mark.contract("C-089", "C-094")
def test_c089_c094_clear_and_auto_fill_apply_when_confirmed(qapp, monkeypatch):
    """C-089 清零 = 零用例；C-094 auto→期望只填未填的（确认按「是」的那条路）。"""
    p, _st = make_panel(LOGIC_SIG)
    m = p.model
    H.auto_dialogs(monkeypatch)                 # ConfirmDialog.ask 默认答「是」
    exp_r = m.rowCount() - 1
    before = m.fill_progress()[0]
    click_btn(p, "auto_fill")
    n, _mm, _k = m.fill_progress()
    assert n > before and n == m.columnCount()
    assert all(m.data(m.index(exp_r, c), int(TR.IS_FALLBACK)) is False
               for c in range(m.columnCount()))
    click_btn(p, "clear")
    assert m.columnCount() == 0
    assert hint(p) == T.TRUTH_CLEAR_DONE
    assert "零用例" in hint(p), "清零之后要说清后果（导出时只记录、不产生断言）"


@pytest.mark.contract("C-090", "C-095", "C-103")
def test_c090_c095_c103_three_confirms_default_no(qapp, monkeypatch):
    """C-090/C-095/C-103：三处确认都弹、标题对得上、**默认按钮 = 否**，按「否」什么都不改。"""
    p, _st = make_panel(LOGIC_SIG)
    m = p.model
    # 先造一条「值得保护」的反例（自定义命名）→ 删反例才会走确认（C-103）
    H.click_cell(p.grid, 0, 0)
    with pytest.MonkeyPatch.context() as mp:
        H.auto_dialogs(mp)
        click_btn(p, "add_neg")
    neg_i = next(i for i, c in enumerate(m.cols()) if c["neg"])
    H.click_cell(p.grid, 0, neg_i)
    with pytest.MonkeyPatch.context() as mp:
        H.auto_dialogs(mp, answers={"RenameColumnDialog.ask": "KEEPME"})
        click_btn(p, "rename_col")
    assert m.protected_negatives(), "没造出值得保护的反例，C-103 这条就白测了"

    n_cols, n_filled = m.columnCount(), m.fill_progress()[0]
    rec = H.auto_dialogs(monkeypatch, answers={
        "ConfirmDialog.ask": H.REAL,                       # 放行真框，由 QDialog.exec 记标题
        DLG.confirm_title(DLG.CONFIRM_CLEAR): QtWidgets.QDialog.Rejected,
        DLG.confirm_title(DLG.CONFIRM_AUTO_FILL): QtWidgets.QDialog.Rejected,
        DLG.confirm_title(DLG.CONFIRM_DEL_NEG): QtWidgets.QDialog.Rejected,
    })
    for key, kind in (("clear", DLG.CONFIRM_CLEAR), ("auto_fill", DLG.CONFIRM_AUTO_FILL),
                      ("del_neg", DLG.CONFIRM_DEL_NEG)):
        click_btn(p, key)
        call = rec.last("ConfirmDialog.exec")
        assert call is not None and call.title == DLG.confirm_title(kind), rec.dump()
        dlg = call.args[0]
        assert dlg.cancel_btn.isDefault() and not dlg.ok_btn.isDefault(), \
            "%s 的默认按钮不是「否」——手滑回车就把活删了" % kind
    assert rec.count("ConfirmDialog.exec") == 3, rec.dump()
    assert m.columnCount() == n_cols and m.fill_progress()[0] == n_filled
    assert m.protected_negatives(), "按了「否」还把反例删了"
    assert rec.saw("KEEPME"), "删反例的确认框没点名那条自定义命名的反例（C-103 / I-20）"


# ═══════════════════ 反例（C-096 / C-101 / C-102 / C-122）═══════════════════
@pytest.mark.contract("C-096", "C-102")
def test_c096_c102_add_and_del_negatives_from_toolbar(qapp, monkeypatch):
    """C-096 给选中的正向列各加一条反例；C-102 删反例只删反例、正向全留。"""
    p, _st = make_panel(LOGIC_SIG)
    m = p.model
    H.auto_dialogs(monkeypatch)
    n0 = m.columnCount()
    select_cols(p, [0, 1])
    click_btn(p, "add_neg")
    assert m.columnCount() == n0 + 2
    assert sum(1 for c in m.cols() if c["neg"]) == 2
    assert "2" in hint(p)
    click_btn(p, "del_neg")                     # 没有值得保护的反例 → 不弹确认，直接删
    assert m.columnCount() == n0 and not any(c["neg"] for c in m.cols())


@pytest.mark.contract("C-096")
def test_c096_add_neg_without_selection_says_so(qapp, monkeypatch):
    """C-096 的「未选则取首条正向」：没选中列也照做，但提示条要说清它挑了谁。"""
    p, _st = make_panel(LOGIC_SIG)
    H.auto_dialogs(monkeypatch)
    p.grid.clearSelection()
    p.grid.setCurrentIndex(QtCore.QModelIndex())
    click_btn(p, "add_neg")
    assert sum(1 for c in p.model.cols() if c["neg"]) == 1
    assert T.TRUTH_ADD_NEG_NO_SELECTION in hint(p)


@pytest.mark.contract("C-101", "C-122")
def test_c101_c122_all_positive_negatives_share_one_path(qapp, monkeypatch):
    """C-101 / C-122：清单底部「全部加反例」= 逐正向列加反例，logic 与 mux 同一条 model 路。

    面板的「加反例（选中列）」把全部正向列选上就等价于它（`edits.add_negatives` 的
    `all_positive` 分支与逐列分支同一个 `plan_negatives`）——两边条数必须一致。
    """
    H.auto_dialogs(monkeypatch)
    for sig in (LOGIC_SIG, MUX_SMALL):
        p, _st = make_panel(sig)
        m = p.model
        pos = [i for i, c in enumerate(m.cols()) if not c["neg"]]
        want, _skipped = ED.add_negatives(m.cols(), [], True)      # 清单那条路（全部正向）
        select_cols(p, pos)
        click_btn(p, "add_neg")                                    # 面板这条路（全选）
        assert sum(1 for c in m.cols() if c["neg"]) == len(want) > 0, sig
        assert m.columnCount() == len(pos) + len(want)


# ═══════════════════ mux 头部条（C-124 / C-125 / C-126 / C-113）═══════════════════
@pytest.mark.contract("C-124", "C-125", "C-126")
def test_c124_c125_c126_mux_header_strip(qapp):
    """C-124 case 结构 + 生效档 + 怎么展开 + 手填进度；C-125 死分支点名；C-126 iddq 门控 + 能否 force。"""
    p, st = make_panel(MUX_SIG)
    head = H.find(p, N.TRUTH_MUX_HEADER)
    assert head.isVisible()
    an = p.current_an()
    exp = an["expansion"]

    case = p.mux_labels["case"].text()
    assert "case(" in case and "%d 选 1" % len(exp["parsed_cases"]) in case
    label, _src = st.coverage().effective_label(p.current_name(), models=st.models())
    assert label in case and T.TRUTH_MUX_HOW[label] in case
    n, m, _k = p.model.fill_progress()
    assert "手填 %d/%d" % (n, m) in case

    shadowed = p.mux_labels["shadowed"]
    assert exp["shadowed"], "夹具挑错了：这个 mux 没有死分支，C-125 测不到"
    assert shadowed.isVisible()
    first = sorted(exp["shadowed"])[0]
    assert PANEL._case_literal(*exp["parsed_cases"][first]) in shadowed.text()

    gated = p.mux_labels["gated"]
    assert an["dft_gate"] and gated.isVisible()
    assert an["dft_gate"]["label"] in gated.text()
    assert T.TRUTH_MUX_GATE_FORCEABLE in gated.text(), \
        "这个门的 binding.kind 是 RO，头部条要说「能 force」"

    # logic 信号上整条头部条都不该露头
    p2, _ = make_panel(LOGIC_SIG)
    assert not H.find(p2, N.TRUTH_MUX_HEADER).isVisible()


@pytest.mark.contract("C-113")
def test_c113_mux_collision_line_shows_on_model_signal(qapp):
    """C-113：撞值提示（model 重分析后发 `muxCollision`）落在头部条的第四行，文案取自 terms。"""
    p, _st = make_panel(MUX_SMALL)
    line = p.mux_labels["collision"]
    assert not line.isVisible()
    p.model.muxCollision.emit(T.TRUTH_MUX_COLLISION)
    H.app().processEvents()
    assert line.isVisible() and line.text() == T.TRUTH_MUX_COLLISION
    assert T.TRUTH_MUX_COLLISION in hint(p)


# ═══════════════════ 按钮启停与 tooltip（C-134 / C-135 / C-140）═══════════════════
@pytest.mark.contract("C-134", "C-135")
def test_c134_c135_buttons_disabled_and_mux_tips(qapp):
    """C-134 不可建信号全置灰；C-135 选中 mux 时说明文案换成 mux 语义。"""
    p, _st = make_panel(RO_SIG)
    assert p.model.editable_kind() == "", "夹具挑错了：这个信号是可建的"
    assert all(not btn(p, k).isEnabled() for k in PANEL.TOOLBAR_KEYS)
    assert H.find(p, N.TRUTH_BTN_MAXIMIZE, QtWidgets.QPushButton).isEnabled(), \
        "放大是排版动作，不该跟着不可建一起灰掉"

    p_logic, _ = make_panel(LOGIC_SIG)
    p_mux, _ = make_panel(MUX_SMALL)
    assert all(btn(p_logic, k).isEnabled() for k in PANEL.TOOLBAR_KEYS if k != "mux_data")
    assert not btn(p_logic, "mux_data").isEnabled(), "logic 信号没有 mux 数据值可设"
    assert btn(p_mux, "mux_data").isEnabled()
    shared = set(T.TRUTH_BTN_TIPS_MUX) & set(T.TRUTH_BTN_TIPS_LOGIC)
    assert shared
    for key in sorted(shared):
        assert btn(p_logic, key).toolTip() == T.TRUTH_BTN_TIPS_LOGIC[key]
        assert btn(p_mux, key).toolTip() == T.TRUTH_BTN_TIPS_MUX[key]


@pytest.mark.contract("C-140")
def test_c140_single_toolbar_thirteen_buttons(qapp):
    """C-140：两套编辑器合成一套工具条 —— 13 个动作键（+ 放大），名字与文案全来自 names/terms。"""
    p, _st = make_panel(LOGIC_SIG)
    bar = H.find(p, N.TRUTH_TOOLBAR)
    assert len(PANEL.TOOLBAR_KEYS) == 13
    buttons = [b for b in bar.findChildren(QtWidgets.QPushButton)]
    assert len(buttons) == 14, [b.objectName() for b in buttons]
    for key in PANEL.TOOLBAR_KEYS:
        b = btn(p, key)
        assert b.parent() is bar
        assert b.text() == T.TRUTH_BTN[key]
        assert b.objectName() == getattr(N, "TRUTH_BTN_%s" % key.upper())
    mx = H.find(p, N.TRUTH_BTN_MAXIMIZE, QtWidgets.QPushButton)
    assert mx.text() == T.TRUTH_BTN["maximize"] and mx.parent() is bar


# ═══════════════════ 右键菜单（C-296 / C-111）═══════════════════
@pytest.mark.contract("C-296")
def test_c296_context_menu_items(qapp):
    """C-296：右键行/列 → 插入 · 删除 · 复制 · 设为反例 · 设置本列 mux 数据值 · 重命名。"""
    p, _st = make_panel(LOGIC_SIG)
    menu = right_click(p, 0, 0)
    assert menu is not None and menu.objectName() == N.TRUTH_CONTEXT_MENU
    assert [a.text() for a in menu.actions()] == [T.TRUTH_CONTEXT_MENU[k]
                                                 for k in PANEL.MENU_KEYS]
    assert menu_act(menu, "insert").isEnabled()
    assert not menu_act(menu, "rename").isEnabled(), "自动 T 列不给改名（C-093）"
    assert not menu_act(menu, "mux_data_col").isEnabled(), "logic 信号没有 mux 数据值"
    p.close_context_menu()

    n0 = p.model.columnCount()
    menu_act(p.build_context_menu(0, 0), "insert").trigger()
    H.app().processEvents()
    assert p.model.columnCount() == n0 + 1
    menu_act(p.build_context_menu(0, 0), "delete").trigger()
    H.app().processEvents()
    assert p.model.columnCount() == n0


@pytest.mark.contract("C-111")
def test_c111_context_set_mux_col_data(qapp, monkeypatch):
    """C-111：mux 手编列的「设置本列 mux 数据值」只改本列，auto_out 按路由 case 重算。"""
    p, _st = make_panel(MUX_SMALL)
    m = p.model
    data_r = next(i for i, e in enumerate(p._e_inputs) if e["mux_data_base"])
    # 自动生成列上这一项不给点（改它是整表的事 = C-110）
    assert not menu_act(p.build_context_menu(data_r, 0), "mux_data_col").isEnabled()

    H.click_cell(p.grid, 0, 0)
    click_btn(p, "copy_col")                      # mux 的「复制列」= 造一条手编列
    c = m.columnCount() - 1
    assert m.cols()[c]["user"] and m.col_state(c) in (CT.TruthColState.USER, CT.TruthColState.NEG)
    act = menu_act(p.build_context_menu(data_r, c), "mux_data_col")
    assert act.isEnabled()

    base = p._e_inputs[data_r]["mux_data_base"]
    old = m.data(m.index(data_r, c), int(TR.RAW_VALUE))
    others = [m.data(m.index(data_r, j), int(TR.RAW_VALUE)) for j in range(c)]
    new = (old ^ 0b101) & ((1 << p._e_inputs[data_r]["width"]) - 1)
    n_undo = m.undo_stack().count()
    H.auto_dialogs(monkeypatch, answers={"MuxDataDialog.ask": {base: new}})
    act.trigger()
    H.app().processEvents()
    assert m.data(m.index(data_r, c), int(TR.RAW_VALUE)) == new
    assert [m.data(m.index(data_r, j), int(TR.RAW_VALUE)) for j in range(c)] == others, \
        "C-111 只改本列，别的列一个都不许动"
    assert m.undo_stack().count() == n_undo + 1


# ═══════════════════ C-110 mux 数据值整表 ═══════════════════
@pytest.mark.contract("C-110")
def test_c110_mux_data_dialog_whole_table(qapp, monkeypatch):
    """C-110：工具条整表入口 → 按物理寄存器同步整表 + 写进 `state.mux_data()`；清空恢复自动。"""
    p, st = make_panel(MUX_SMALL)
    m = p.model
    assert hasattr(m, "set_reanalyzer"), "C3-a 的 set_reanalyzer 不在，C-110 接不上"
    rows = p._mux_data_rows()
    assert rows, "夹具挑错了：这个 mux 没有可手填的数据寄存器行"
    base = rows[0]["base"]
    width = rows[0]["width"]
    new = 0b101 & ((1 << width) - 1)
    low = p.current_name().lower()

    H.auto_dialogs(monkeypatch, answers={"MuxDataDialog.ask": {base: new}})
    n_undo = m.undo_stack().count()
    click_btn(p, "mux_data")
    assert st.mux_data().get(low, {}).get("data", {}).get(base) == new, \
        "整表同步要落进会话档（compute_edited 靠它喂生成器）"
    assert m.undo_stack().count() == n_undo + 1
    data_rows = [i for i, e in enumerate(p._e_inputs) if (e["mux_data_base"] or "") == base]
    got = {m.data(m.index(i, c), int(TR.RAW_VALUE))
           for i in data_rows for c in range(m.columnCount())}
    assert new in got, "整表一条列都没取到手填值 = 所见非所得"

    m.undo_stack().undo()                          # 撤销走同一条 reanalyzer（传回旧文本）
    H.app().processEvents()
    assert base not in (st.mux_data().get(low) or {}).get("data", {})


# ═══════════════════ C-298 导入期望 / 批量填 ═══════════════════
@pytest.mark.contract("C-298")
def test_c298_import_and_batch_fill_report_names_first(qapp, monkeypatch, tmp_path):
    """C-298：按列名导入期望 + 批量填。没对上的列名**先点名再计数**（I-20 / C-270）。"""
    p, _st = make_panel(LOGIC_SIG)
    m = p.model
    good = m.all_names()[0]
    bogus = "T_NOT_HERE"
    path = str(tmp_path / "exp.csv")
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["信号\\测试", good, bogus])
        w.writerow(["期望(进.sv)", "0x1", "0x1"])

    H.auto_dialogs(monkeypatch, answers={"QFileDialog.getOpenFileName": (path, "")})
    click_btn(p, "import_exp")
    txt = hint(p)
    assert bogus in txt and "1" in txt
    # C3-int：这句现在由 `truth/io.import_report_text` 拼，model 与面板共用同一份
    assert txt == TIO.import_report_text(1, [bogus], [])
    assert txt.index(bogus) < txt.index(
        T.TRUTH_IMPORT_EXP_REPORT_FMT.format(missing="", n=1)), "名字必须在计数前（I-20）"
    exp_r = m.rowCount() - 1
    assert m.data(m.index(exp_r, 0), int(TR.RAW_VALUE)) == 1

    # 批量填：常量走 model.batch_fill，取 auto / 清空走 setData + 一个宏
    select_cols(p, [1, 2])
    H.auto_dialogs(monkeypatch, answers={
        "BatchFillDialog.ask": {"mode": "const", "value": 0, "scope": "selected"}})
    n_undo = m.undo_stack().count()
    click_btn(p, "batch_fill")
    assert m.undo_stack().count() == n_undo + 1
    assert m.data(m.index(exp_r, 1), int(TR.RAW_VALUE)) == 0
    H.auto_dialogs(monkeypatch, answers={
        "BatchFillDialog.ask": {"mode": "clear", "value": None, "scope": "all"}})
    click_btn(p, "batch_fill")
    assert all(m.data(m.index(exp_r, c), int(TR.IS_FALLBACK))
               for c in range(m.columnCount())), "「清空」之后每一格都该回到兜底显示"


# ═══════════════════ C-136…C-140 导出 CSV ═══════════════════
@pytest.mark.contract("C-136", "C-139")
def test_c136_export_csv_passes_provider_edited_cov(qapp, monkeypatch, tmp_path):
    """C-136/C-139：导出 CSV 把 provider / edited / cov 三样原样交给 `io.export_signal_csv`。

    `cov` 必须与算出这份 an 的那一档**同一个**（`sv_preview._cov_args()` 的本信号分支），
    否则 CSV 列集是一个档、编辑器是另一个档，看着像「导出漏了几条」。
    """
    p, st = make_panel(MUX_SMALL)
    seen = {}
    real = TIO.export_signal_csv

    def _spy(path, model, an, name, provider=None, edited=None, cov=None, cols=None):
        seen.update(path=path, name=name, provider=provider, edited=edited, cov=cov, cols=cols)
        return real(path, model, an, name, provider=provider, edited=edited, cov=cov, cols=cols)

    monkeypatch.setattr(TIO, "export_signal_csv", _spy)
    out = str(tmp_path / "sig.csv")
    H.auto_dialogs(monkeypatch, answers={"QFileDialog.getSaveFileName": (out, "")})
    click_btn(p, "export_csv")

    assert seen and seen["name"] == p.current_name() and seen["path"] == out
    assert seen["provider"] is st.provider()
    assert seen["edited"] == st.compute_edited()
    cov = st.coverage()
    mode, exh = cov.mode_for(p.current_name(), models=st.models())
    assert seen["cov"] == {"mode": mode, "max_tests": int(cov.max_tests),
                           "exhaustive": bool(exh), "sig_cov": None, "form_cov": None}
    assert set(seen["cov"]) == set(TIO.COV_KEYS)
    # C3-int：面板先问一次列（判 mux 有没有产物），再把**同一份**交给写文件 ——
    # 不传 cols 的话同一件事要 render 两遍 .sv，而且判断用的那份与写出去的那份可能不是同一个。
    assert seen["cols"] is not None, "面板没把现成的列传给 io，等于又 render 了一遍"
    assert os.path.exists(out) and os.path.getsize(out) > 0
    assert out in hint(p)
    assert T.TRUTH_EXPORT_MUX_NO_BUILD not in hint(p), \
        "产物取到了却还说「没产出」—— 那句提醒会天天弹，用户很快就不看了"


@pytest.mark.contract("C-139")
def test_c139_export_says_so_when_mux_product_missing(qapp, monkeypatch, tmp_path):
    """C-139 的反面：mux 这次没产出 .sv 块 → CSV 退回编辑器这张表，**照实说一句**。"""
    p, st = make_panel(MUX_SMALL)

    def _boom(*_a, **_k):
        raise RuntimeError("这次 build 里没有这个信号")

    monkeypatch.setattr(st.provider(), "render_sv", _boom)
    out = str(tmp_path / "fallback.csv")
    H.auto_dialogs(monkeypatch, answers={"QFileDialog.getSaveFileName": (out, "")})
    click_btn(p, "export_csv")
    assert os.path.exists(out), "产物取不到也要把编辑器这张表导出来，不是什么都不写"
    assert T.TRUTH_EXPORT_MUX_NO_BUILD in hint(p)
    assert hint(p).index(T.TRUTH_EXPORT_MUX_NO_BUILD) < hint(p).index(out), \
        "先说清这份 CSV 与 .sv 不一定对得上，再报写到哪儿了"


@pytest.mark.contract("C-294", "C-083")
def test_c294_c083_paste_and_parse_reports_reach_the_hint_bar(qapp):
    """C-294 / C-083：Ctrl+V 与写法解析失败的说明都落在提示条 —— 面板只转述，不重复处理键位。"""
    p, _st = make_panel(LOGIC_SIG)
    m = p.model
    exp_r = m.rowCount() - 1
    H.click_cell(p.grid, exp_r, 0)
    H.paste(p.grid, "1\t1")
    H.app().processEvents()
    assert "粘贴落了" in hint(p) and m.data(m.index(exp_r, 0), int(TR.RAW_VALUE)) == 1

    m.parseFailed.emit(T.TRUTH_PARSE_FAILED_FMT.format(text="0b"))
    H.app().processEvents()
    assert "0b" in hint(p) and "已还原" in hint(p)


# ═══════════════════ 不变量 ═══════════════════
@pytest.mark.contract("C-244")
def test_i05_put_edit_on_cols_changed_within_suspend_persist(qapp, monkeypatch):
    """I-05 / C-244：一次批量动作只落一次盘（`auto→期望` 会连发 N 条 dataChanged）。"""
    p, st = make_panel(LOGIC_SIG)
    H.auto_dialogs(monkeypatch)
    calls = []
    real = P.write_edits_bucket
    monkeypatch.setattr(P, "write_edits_bucket",
                        lambda path, patch: (calls.append(path), real(path, patch))[1])
    assert p.model.columnCount() > 2, "列太少的话「批量」和「单次」看不出区别"
    click_btn(p, "auto_fill")
    assert len(calls) == 1, "auto→期望 写了 %d 次盘（没裹 suspend_persist？）" % len(calls)
    assert st.edit_of(p.current_name()) is not None
    assert len(st.edit_of(p.current_name())["cols"]) == p.model.columnCount()


def test_i05_cell_navigation_does_not_persist(qapp, monkeypatch):
    """I-05 的另一半：只是换当前列（`dataChanged` 带 IS_CURRENT_COL role）不许写盘。"""
    p, _st = make_panel(LOGIC_SIG)
    calls = []
    monkeypatch.setattr(P, "write_edits_bucket", lambda path, patch: calls.append(path))
    for c in range(min(4, p.model.columnCount())):
        H.click_cell(p.grid, 0, c)
    H.app().processEvents()
    assert not calls, "在表上点几下就存了 %d 次盘" % len(calls)


@pytest.mark.contract("C-282")
def test_i18_bus_highlight_row_no_echo(qapp):
    """I-18 / C-282：点冻结列一行 → 只经 bus 广播一次；另一块面板同名行跟着亮，不回环。"""
    bus = BUS.HighlightBus()
    seen = []
    bus.netSelected.connect(lambda net, origin: seen.append((net, origin)))
    p1, st = make_panel(LOGIC_SIG, bus=bus)
    p2, _ = make_panel(LOGIC_SIG, bus=bus, state=st)
    row = 0
    H.click_cell(p1.frozen, row, 0)
    H.app().processEvents()
    assert len(seen) == 1 and seen[0][1] == "truth", seen
    net = seen[0][0]
    assert p1.highlighted_row() == row and p2.highlighted_row() == row
    assert [i for i in p2.frozen.selectionModel().selectedRows()][0].row() == row
    # 收到自己发的广播不许再 select 一次（否则就是两块视图互相激发）
    assert bus.current_net == net and len(seen) == 1
    bus.select("", "list")
    H.app().processEvents()
    assert p1.highlighted_row() == -1 and p2.highlighted_row() == -1


def test_i18_net_key_matches_bus(qapp):
    """面板不 import `ui.bus`，自带一份线网比对键 —— 拿两张 mirror 的真名逐个对着 bus 比。"""
    from dreg_verify import inputs_table as IT
    names = []
    for kind in ("btlp", "wl"):
        st = make_state(kind)
        for mdl in st.models():
            an = st.analyze(mdl["name"])
            if not an:
                continue
            names.extend(str(r.get("name") or "") for r in IT.input_rows(an))
    names = [n for n in names if n]
    assert len(names) > 40, "只比到 %d 个名字，这条测试等于没验" % len(names)
    assert BUS.net_key("D_FOO[3:0]") == "d_foo"          # 先证明这条比较判得出「剥了位宽」
    bad = [n for n in names if PANEL._net_key(n) != BUS.net_key(n)]
    assert not bad, "这些名字两边算出来的键不一样：%s" % bad[:5]


@pytest.mark.contract("C-106")
def test_c106_progress_signal_forwarded(qapp, monkeypatch):
    """C-106：`model.progressChanged(n, m, k)` 原样转发给组合根（接 MainView / DetailHeader）。"""
    p, _st = make_panel(LOGIC_SIG)
    got = []
    p.progressChanged.connect(lambda n, m, k: got.append((n, m, k)))
    H.auto_dialogs(monkeypatch)
    click_btn(p, "auto_fill")
    assert got and got[-1] == p.model.fill_progress()
    assert got[-1][0] == p.model.columnCount()


@pytest.mark.contract("C-297")
def test_c297_maximize_emits(qapp):
    """C-297：放大 → `maximizeRequested(True)`（收起电路图 / 右栏是组合根的事），再点还原。"""
    p, _st = make_panel(LOGIC_SIG)
    got = []
    p.maximizeRequested.connect(got.append)
    mx = H.find(p, N.TRUTH_BTN_MAXIMIZE, QtWidgets.QPushButton)
    H.click(mx)
    H.app().processEvents()
    assert got == [True] and mx.text() == T.TRUTH_BTN["restore"]
    H.click(mx)
    H.app().processEvents()
    assert got == [True, False] and mx.text() == T.TRUTH_BTN["maximize"]
    p.set_maximized(True)                       # 组合根反向同步不许再发一次
    assert got == [True, False] and mx.isChecked()


@pytest.mark.contract("C-063")
def test_c063_column_header_tooltip_has_drive_detail(qapp):
    """C-063：列头悬停给该列的 force / RF_WRITE 明细（面板把 model 的 DRIVE_TIP 透到表头）。"""
    p, _st = make_panel(LOGIC_SIG)
    hdr = p.grid.horizontalHeader()
    tip = hdr.header_tooltip(0)
    assert tip and p.model.all_names()[0] in tip
    drives = p.model.column_drives(0)
    if drives:
        assert T.scrub(str(drives[0])) in tip


def test_panel_names_all_set(qapp):
    """I-14：面板上每个可测控件都有 objectName，且名字**只来自 `names.py`**（C3-int 收口）。"""
    p, _st = make_panel(MUX_SIG)
    want = [N.TRUTH_PANEL, N.TRUTH_TOOLBAR, N.TRUTH_HINT_SELECTION, N.TRUTH_HINT_PASTE,
            N.TRUTH_HINT_CONTEXT, N.TRUTH_NAMES_VIEW, N.TRUTH_GRID_VIEW, N.TRUTH_LEGEND,
            N.TRUTH_MUX_HEADER, N.TRUTH_SUPPLEMENT_DOT, N.TRUTH_BTN_MAXIMIZE]
    want += [getattr(N, "TRUTH_BTN_%s" % k.upper()) for k in PANEL.TOOLBAR_KEYS]
    want += [N.TRUTH_SPLIT_NAMES_GRID, N.TRUTH_HINT_BAR, N.TRUTH_EMPTY, N.TRUTH_MUX_CASE,
             N.TRUTH_MUX_SHADOWED, N.TRUTH_MUX_GATED, N.TRUTH_MUX_COLLISION_HINT]
    want += [N.fmt_truth_legend(k) for k, _txt in T.TRUTH_LEGEND]
    assert H._find_opt(p, "no_such_object_name_xyz") is None      # 这条查找判得出「没有」
    missing = [x for x in want if H._find_opt(p, x) is None]
    assert not missing, "这些 objectName 在面板上找不到：%s" % missing
    menu = p.build_context_menu(0, 0)
    assert menu.objectName() == N.TRUTH_CONTEXT_MENU
    assert all(a.objectName() == N.fmt_truth_menu(k)
               for a, k in zip(menu.actions(), PANEL.MENU_KEYS))
    # C3-int：影子表不许再回来（文案在 terms.py、名字在 names.py，一处定义一处改）
    for attr in ("PENDING_NAMES", "PENDING_TERMS", "NAME_LEGEND_FMT", "NAME_MENU_FMT"):
        assert not hasattr(PANEL, attr), "panel.%s 还在，说明常量有两份" % attr
    assert set(want) <= set(N.all_names().values()) | {N.fmt_truth_legend(k)
                                                       for k, _t in T.TRUTH_LEGEND}


def test_panel_empty_state_when_no_signal(qapp):
    """空态：没选信号 → 表收起、空态文案露头、工具条全灰（点不出任何动作）。"""
    st = make_state("btlp")
    p = PANEL.TruthPanel(state=st)
    p.resize(900, 400)
    p.show()
    _ALIVE.append(p)
    H.app().processEvents()
    empty = H.find(p, N.TRUTH_EMPTY, QtWidgets.QLabel)
    assert empty.isVisible() and empty.text() == T.TRUTH_EMPTY_NO_SIGNAL
    assert p.model.columnCount() == 0 and p.model.rowCount() == 2
    assert all(not btn(p, k).isEnabled() for k in PANEL.TOOLBAR_KEYS)
    st.set_current(LOGIC_SIG[1])
    H.app().processEvents()
    assert not empty.isVisible() and p.model.columnCount() > 0
    st.set_current("")
    H.app().processEvents()
    assert empty.isVisible()


def test_panel_legend_colors_come_from_theme(qapp):
    """图例六项（`terms.TRUTH_LEGEND`）按 `theme.CELL_STATES` 上色，面板不写裸十六进制。"""
    p, _st = make_panel(LOGIC_SIG)
    for key, text in T.TRUTH_LEGEND:
        item = H.find(p, N.fmt_truth_legend(key))
        lb = item.findChild(QtWidgets.QLabel)
        sw = item.findChild(QtWidgets.QFrame)
        bg, border, fg, _line = TH.CELL_STATES[key]
        assert lb.text() == text and fg in lb.styleSheet()
        assert bg in sw.styleSheet() and border in sw.styleSheet()


def test_shots_logic_and_mux(qapp, monkeypatch):
    """截图：logic 与 mux 两张（`refactor_notes/gui_v2_shots/`，目录已 gitignore）。"""
    H.auto_dialogs(monkeypatch)
    p, _st = make_panel(LOGIC_SIG, size=(1180, 470))
    H.click_cell(p.grid, p.model.rowCount() - 1, 1)
    H.app().processEvents()
    assert os.path.exists(H.shot(p, "truth_panel_logic"))
    p2, _ = make_panel(MUX_SIG, size=(1180, 470))
    H.app().processEvents()
    assert os.path.exists(H.shot(p2, "truth_panel_mux"))
