# -*- coding: utf-8 -*-
"""test_ui_detail_header.py —— ④ 详情标题栏（`dreg_verify/ui/detail_header.py`，C2-c）。

契约 ID（附录 A「HDR」6 条，每条 ≥1 条测试名含 ID）：
    C-043 分析整表/单信号出错 → 详情区写「分析失败（已捕获，未崩）」+ 原因全文，界面不崩
    C-055 头部一行摘要：信号名 / 状态 / owner / 分类 / 用例数
    C-064 「解析明细」按钮 → 面板（inputs_table.resolve_detail）
    C-065 解析明细里把输入来源翻成中文（FOUND_IN_TEXT 九种）
    C-066 解析明细末尾「仿真器找不到这根网」三步提示 + 直链诊断
    C-106 手填期望 N/M 显示在标题栏（+ 进度条 + 「其中 K 条与程序算的不一致」）

夹具两套：
  · 摘要 / 失败行 / 开合：`ui_fakes.FakeState`（真 WorkbenchState + 假 provider），不跑引擎；
  · 解析明细 / 手填进度：**真 mirror**（btlp）+ 真 `providers.TopoutProvider` —— 这两条要验的
    恰恰是「后端文本原样端上来」和「列模型算出来的进度」，替身验不了。
真实事件（H.click / H.keys），不直接调槽、不 emit 信号冒充点击。
"""

import os

import pytest

import ui_harness as H

pytest.importorskip("PySide6")

from PySide6 import QtWidgets                                      # noqa: E402

from dreg_verify import edits as ED                                # noqa: E402
from dreg_verify import inputs_table as IT                         # noqa: E402
from dreg_verify.ui import names, persist as P, state as ST, terms          # noqa: E402
from dreg_verify.ui.detail_header import DetailHeader                       # noqa: E402

import ui_fakes as F                                               # noqa: E402

#: btlp 镜像里一个 logic 根、可编辑、4 个输入 12 条用例（与 test_ui_state.py 同一个样例）
LOGIC_SIG = "d_logic_bt_lp_rx_en"


@pytest.fixture(scope="module")
def qapp():
    return H.app()


@pytest.fixture
def iso(monkeypatch, tmp_path):
    """两份持久化文件指到临时目录（与 test_ui_state.py 的 `iso` 同一件事）。"""
    monkeypatch.setattr(P, "SETTINGS_PATH", str(tmp_path / "gui_settings.json"))
    monkeypatch.setattr(P, "EDITS_PATH", str(tmp_path / "edits.json"))
    return tmp_path


def _host(hdr, w=1280, h=200):
    """把标题栏放进一个有尺寸的宿主里（截图与真实点击都要真几何）。"""
    host = QtWidgets.QWidget()
    host.setObjectName("hdr_host")
    host.resize(w, h)
    lay = QtWidgets.QVBoxLayout(host)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.addWidget(hdr)
    lay.addStretch(1)
    host.show()
    H.app().processEvents()
    return host


@pytest.fixture
def fake(qapp):
    st = F.FakeState()
    hdr = DetailHeader(st)
    host = _host(hdr)
    yield hdr, st, host
    host.close()


@pytest.fixture
def real(qapp, iso):
    """真 mirror + 真 provider：只灌骨架清单（跑整表 view_models 不是本模块的事）。"""
    st = ST.WorkbenchState()
    assert st.load(H.mirror_path("btlp"))
    st.set_models("topout", st.provider("topout").skeleton_models(), partial=True)
    hdr = DetailHeader(st)
    host = _host(hdr)
    yield hdr, st, host
    host.close()


def _ready_model(st, name, n_vectors=None):
    """骨架行（status=pending）→ 一条「分析完了」的行，免得为一个标题栏跑整表。"""
    m = dict(st.model_of(name) or {})
    m.update({"status": "ok", "status_detail": "clean"})
    if n_vectors is not None:
        m["n_vectors"] = n_vectors
    return m


# ═════════════════════════ C-055 头部一行摘要 ═════════════════════════

def test_c055_header_summary_fields(fake):
    """C-055：名 / 状态徽标 / owner · 分类 · 用例数 —— 四样都在，且都从模型来。"""
    hdr, st, host = fake
    m = st.models()[0]
    hdr.show_signal(m)

    assert H.find(hdr, names.HDR_NAME, QtWidgets.QLabel).text() == m["name"]
    badge = H.find(hdr, names.HDR_STATUS_BADGE, QtWidgets.QLabel)
    assert badge.text() == terms.STATUS["clean"][0]          # 「可建」
    assert badge.toolTip()                                    # 状态档解释挂 tooltip
    meta = H.find(hdr, names.HDR_META, QtWidgets.QLabel).text()
    assert meta == terms.HDR_META_FMT.format(
        owner=m["owner"], kind=terms.KIND_LABELS[m["kind"]], n=m["n_vectors"])
    assert m["owner"] in meta and str(m["n_vectors"]) in meta

    # 覆盖度按钮位 = CoverageControl 自己渲染的文案（C-156）
    cov_btn = H.find(hdr, names.HDR_COV_BTN)
    assert cov_btn.text().startswith("覆盖度")
    H.shot(host, "hdr_summary")


def test_c055_summary_switches_with_signal(fake):
    """C-055：换信号整条摘要跟着换（mux 行的分类写「mux」，用例数是它自己的）。"""
    hdr, st, host = fake
    a, b = st.models()[0], st.models()[1]
    hdr.show_signal(a)
    first = H.find(hdr, names.HDR_META, QtWidgets.QLabel).text()
    hdr.show_signal(b)
    second = H.find(hdr, names.HDR_META, QtWidgets.QLabel).text()
    assert first != second
    assert H.find(hdr, names.HDR_NAME, QtWidgets.QLabel).text() == b["name"]
    assert str(b["n_vectors"]) in second


def test_c055_no_form_code_in_coverage_button(fake):
    """红线（I-12/I-13）：标题栏不得裸出现术语表点名的词（覆盖度来源串的形态编号最容易漏）。"""
    hdr, st, host = fake
    hdr.show_signal(st.models()[0])
    blob = " ".join(w.text() for w in hdr.findChildren(QtWidgets.QLabel) if w.text())
    blob += " " + " ".join(b.text() for b in hdr.findChildren(QtWidgets.QToolButton))
    for word in terms.FORBIDDEN:
        assert word not in blob, "标题栏裸出现术语：%s（%s）" % (word, blob)


# ═════════════════════════ C-064 / C-065 / C-066 解析明细 ═════════════════════════

def test_c064_c065_c066_resolve_detail_panel_text(real):
    """C-064 点「解析明细」出面板；C-065 来源翻成中文；C-066 末尾三步 + 直链诊断。"""
    hdr, st, host = real
    hdr.show_signal(_ready_model(st, LOGIC_SIG))

    panel = H.find(hdr, names.HDR_RESOLVE_PANEL, QtWidgets.QPlainTextEdit)
    assert not panel.isVisible(), "解析明细默认收起"

    got = []
    hdr.resolveDetailToggled.connect(got.append)
    H.click(H.find(hdr, names.HDR_RESOLVE_BTN))                    # 真点击
    assert got == [True]
    assert panel.isVisible()

    text = hdr.resolve_text()
    assert LOGIC_SIG in text
    assert "输入逐条解析" in text
    # C-065：九种来源的中文说法（本信号的输入都在寄存器表里查到）
    assert IT.FOUND_IN_TEXT["regmap"].split("（")[0] in text
    assert "✔ 查到的" in text
    # C-066：末尾三步 + 「仿真器找不到这根网」（错误码留着，工程师要拿去 grep 仿真 log）
    assert terms.CUVUNF_FIRST in text
    for step in ("nets.txt", "scan_rtl", "强制 force 信号"):
        assert step in text
    H.shot(host, "hdr_resolve_detail")

    # 再点一次收起
    H.click(H.find(hdr, names.HDR_RESOLVE_BTN))
    assert got == [True, False]
    assert not panel.isVisible()


def test_c066_resolve_detail_links_to_diagnostics(real):
    """C-066：末尾三步文案里的「诊断」是条真链接 —— 点了发 diagRequested('diag_cuvunf')。"""
    hdr, st, host = real
    hdr.show_signal(_ready_model(st, LOGIC_SIG))
    H.click(H.find(hdr, names.HDR_RESOLVE_BTN))

    seen = []
    hdr.diagRequested.connect(seen.append)
    link = H.find(hdr, names.HDR_RESOLVE_DIAG_BTN)
    assert link.text() == terms.REASON_BTN_FMT.format(
        action=terms.REASON_TEMPLATES["needs-prefix"][2])
    H.click(link)
    assert seen == ["diag_cuvunf"]
    assert seen[0] in terms.REASON_TARGETS


def test_c065_resolve_detail_has_no_raw_terms(real):
    """C-065 / I-12：来源串翻成中文之后，术语表点名的词一个都不许裸出现。"""
    hdr, st, host = real
    hdr.show_signal(_ready_model(st, LOGIC_SIG))
    H.click(H.find(hdr, names.HDR_RESOLVE_BTN))
    text = hdr.resolve_text()
    # CUVUNF 只允许出现在「首次出现的完整说法」里（术语表第 10 行钦定的展开式）；
    # R3-06 ③：`tmm` / `regmap` 在**页名位置**保留真页名，但**括号外就是中文说法**
    # （`FOUND_IN_TEXT` / `PAGE_NAME_TEXT` 两处都是就地解释过的）——摘掉它们再扫。
    stripped = text.replace(terms.CUVUNF_FIRST, "")
    for ok in list(IT.FOUND_IN_TEXT.values()) + list(IT.PAGE_NAME_TEXT.values()):
        stripped = stripped.replace(ok, "")
    for word in terms.FORBIDDEN:
        assert word not in stripped, "解析明细裸出现术语：%s" % word
    # 反过来：来源串确实还在（放行不能变成「顺手把解释也删了」）
    assert "查到了这个字段" in text or "按命名约定" in text


# ═════════════════════════ C-106 手填期望进度 ═════════════════════════

def test_c106_progress_label_and_diff(real):
    """C-106：手填期望 N/M + 进度条 + 「其中 K 条与程序算的不一致」（K 由 auto 对比算出）。"""
    hdr, st, host = real
    an = st.analyze(LOGIC_SIG)
    assert an is not None and an["editable"] == "logic"
    cols = ED.cols_from_vectors(an, [])
    n_pos = sum(1 for c in cols if not c["neg"])
    assert n_pos >= 3

    # 两条手填：一条与 auto 一致，一条故意不一致（后者正是要抓的 bug，不是错误）
    pos = [c for c in cols if not c["neg"]]
    pos[0]["exp"] = pos[0]["auto"]
    pos[1]["exp"] = (pos[1]["auto"] ^ 1) & ((1 << (pos[1]["auto_w"] or 1)) - 1)
    st.put_edit(LOGIC_SIG, {"kind": an["kind"], "src_out_name": an["src_out_name"],
                            "name": an["name"], "renamed": False, "cols": cols, "an": an})

    hdr.show_signal(_ready_model(st, LOGIC_SIG, n_vectors=len(cols)))
    assert (H.find(hdr, names.HDR_PROGRESS_LABEL, QtWidgets.QLabel).text()
            == terms.HDR_PROGRESS_FMT.format(n=2, m=n_pos))
    assert (H.find(hdr, names.HDR_PROGRESS_DIFF, QtWidgets.QLabel).text()
            == terms.HDR_PROGRESS_DIFF_FMT.format(k=1))
    bar = H.find(hdr, names.HDR_PROGRESS_BAR)
    assert bar.value() == (2, n_pos)
    assert 0 < bar.ratio() < 1
    H.shot(host, "hdr_progress")


def test_c106_progress_pushed_from_truth_model(fake):
    """C-106：真值表编辑中经 `TruthModel.progressChanged` → state → `set_progress` 直推，
    不必回头重算一遍列模型（编辑器里每敲一格都要刷这一条）。"""
    hdr, st, host = fake
    hdr.show_signal(st.models()[0])
    hdr.set_progress(7, 25, 1)
    assert H.find(hdr, names.HDR_PROGRESS_LABEL, QtWidgets.QLabel).text() == "手填期望 7/25"
    assert "1" in H.find(hdr, names.HDR_PROGRESS_DIFF, QtWidgets.QLabel).text()
    hdr.set_progress(0, 0, 0)
    assert H.find(hdr, names.HDR_PROGRESS_LABEL, QtWidgets.QLabel).text() == ""


# ═════════════════════════ C-043 分析失败 ═════════════════════════

def test_c043_analysis_failed_message(fake):
    """C-043：分析出错 → 详情区写「分析失败（已捕获，未崩）」+ 原因全文，窗口还活着。"""
    hdr, st, host = fake
    m = dict(st.models()[0])
    m.update({"status": "error", "status_detail": "error",
              "issues": ["cone 展开失败: 表达式 A & 缺右操作数"], "note": ""})
    hdr.show_signal(m)

    err = H.find(hdr, names.HDR_ERROR_LABEL, QtWidgets.QLabel)
    assert err.isVisible()
    assert err.text().startswith(terms.HDR_ANALYSIS_FAILED)
    assert "缺右操作数" in err.text(), "原因全文要留住，不能只剩一句『失败』"
    assert "cone" not in err.text(), "后端术语要 scrub（I-12）"
    assert "Traceback" not in err.text()
    assert hdr.isVisible() and host.isVisible()                    # 没崩
    H.shot(host, "hdr_analysis_failed")


def test_c043_worker_failure_can_be_pushed_in(fake):
    """C-043：整表分析炸了（worker failed）也走同一行，组合根一句 `set_error` 就够。"""
    hdr, st, host = fake
    hdr.show_signal(st.models()[0])
    assert not H.find(hdr, names.HDR_ERROR_LABEL, QtWidgets.QLabel).isVisible()
    hdr.set_error("%s\n读 regmap 页时炸了" % terms.HDR_ANALYSIS_FAILED)
    err = H.find(hdr, names.HDR_ERROR_LABEL, QtWidgets.QLabel)
    # scrub 过：R3-06 ③ 之后，页名位置保留真页名但括号外给中文说法（就地解释，不是裸用）
    assert err.isVisible() and IT.PAGE_NAME_TEXT["regmap"] in err.text()
    hdr.set_error("")
    assert not err.isVisible()


# ═════════════════════════ 右栏开合 / pending / 不可建 ═════════════════════════

def test_hdr_side_toggle_emits_and_switches_label(fake):
    """右栏开合：真点击 → `sideToggled(bool)`，按钮文案在「隐藏右栏 ▶」/「◀ 展开链 · 输入信号」间切。"""
    hdr, st, host = fake
    hdr.show_signal(st.models()[0])
    btn = H.find(hdr, names.HDR_SIDE_TOGGLE)
    assert btn.text() == terms.HDR_SIDE_HIDE

    seen = []
    hdr.sideToggled.connect(seen.append)
    H.click(btn)
    assert seen == [False] and btn.text() == terms.HDR_SIDE_SHOW
    H.click(btn)
    assert seen == [False, True] and btn.text() == terms.HDR_SIDE_HIDE


def test_hdr_pending_and_not_editable_rows(fake, real):
    """骨架行显示「还在后台展开这个信号……」；不可建的信号显示 C-134 那一行。"""
    hdr, st, host = fake
    m = dict(st.models()[0])
    m.update({"status": "pending", "status_detail": "pending", "n_vectors": None})
    hdr.show_signal(m)
    assert H.find(hdr, names.HDR_PENDING, QtWidgets.QLabel).isVisible()
    assert not H.find(hdr, names.HDR_RESOLVE_BTN).isEnabled()

    hdr2, st2, host2 = real
    hdr2.show_signal(_ready_model(st2, "pll_lock_indicator"))      # RO 回读，不可建
    assert H.find(hdr2, names.HDR_NOT_EDITABLE, QtWidgets.QLabel).isVisible()
    assert H.find(hdr2, names.HDR_PROGRESS_LABEL, QtWidgets.QLabel).text() == ""


def test_hdr_follows_state_current_changed(fake):
    """`state.currentChanged` → 标题栏自己跟上（组合根不必再牵一根线）。"""
    hdr, st, host = fake
    st.set_current(st.models()[1]["name"])
    assert H.find(hdr, names.HDR_NAME, QtWidgets.QLabel).text() == st.models()[1]["name"]
    st.set_current("")
    assert H.find(hdr, names.HDR_NAME, QtWidgets.QLabel).text() == ""


def test_hdr_all_object_names_findable(fake):
    """I-14：`names.py` 里每个 `HDR_*` 都能在本区找到（C2-int 起没有 PENDING 表了，直接对账注册表）。"""
    hdr, st, host = fake
    hdr.show_signal(st.models()[0])
    hdr.set_resolve_open(True)
    hdr_names = {k: v for k, v in names.all_names().items() if k.startswith("HDR_")}
    assert len(hdr_names) >= 16, "HDR_* 的名字只剩 %d 个了" % len(hdr_names)
    missing = [k for k, v in hdr_names.items() if H._find_opt(hdr, v) is None]
    assert not missing, "这些 names.HDR_* 没落到控件上：%s" % missing
    assert os.path.exists(H.shot(host, "hdr_all_names"))
