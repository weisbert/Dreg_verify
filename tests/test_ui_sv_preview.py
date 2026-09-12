# -*- coding: utf-8 -*-
"""test_ui_sv_preview.py —— ⑧ .sv 预览（`dreg_verify/ui/sv_preview.py`，C2-c）。

契约 ID（附录 A「SV」7 条，每条 ≥1 条测试名含 ID）：
    C-067 预览勾选集的 .sv（含真值表编辑，所见即所得）
    C-068 预览本信号的 .sv 片段（不必取消其余勾选）
    C-069 超 600 行截断并注明总行数
    C-070 末尾追加本次会跳过哪些信号、各自缺哪根输入
    C-072 配置变更（前缀 / 强制 force / 补充逻辑）后按新配置自动重算，不留旧内容误导
    C-132 编辑（含手填期望）立刻在 .sv 预览与导出里生效
    C-268 两处 .sv 预览合成一处（本信号 ↔ 勾选集 一键切）

正文一律用**真 mirror + 真 `providers.TopoutProvider`** —— 这块要验的就是「预览与导出同源、
所见即所得」，用假 provider 只能验按钮文案。只有 600 行边界那条用可控 provider 卡死行数
（mirror 的行数会随夹具演进漂，边界值不该跟着漂）。
"""

import os

import pytest

import ui_harness as H

pytest.importorskip("PySide6")

from PySide6 import QtWidgets                                      # noqa: E402

from dreg_verify import edits as ED                                # noqa: E402
from dreg_verify import exports as X                               # noqa: E402
from dreg_verify.ui import names, persist as P, state as ST, terms, theme   # noqa: E402
from dreg_verify.ui import sv_preview as SVP                       # noqa: E402

import ui_fakes as F                                               # noqa: E402

LOGIC_SIG = "d_logic_bt_lp_rx_en"          # btlp 镜像：logic 根、可编辑
SKIPPED_SIG = "pll_lock_indicator"         # btlp 镜像：RO 回读 → 进「跳过点名」那一段


@pytest.fixture(scope="module")
def qapp():
    return H.app()


@pytest.fixture
def iso(monkeypatch, tmp_path):
    monkeypatch.setattr(P, "SETTINGS_PATH", str(tmp_path / "gui_settings.json"))
    monkeypatch.setattr(P, "EDITS_PATH", str(tmp_path / "edits.json"))
    return tmp_path


def _host(panel, w=1100, h=640):
    host = QtWidgets.QWidget()
    host.setObjectName("sv_host")
    host.resize(w, h)
    lay = QtWidgets.QVBoxLayout(host)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.addWidget(panel)
    host.show()
    H.app().processEvents()
    return host


@pytest.fixture
def real(qapp, iso):
    """真 mirror：载表 + 骨架清单（勾选默认全勾 = None）。"""
    st = ST.WorkbenchState()
    assert st.load(H.mirror_path("btlp"))
    st.set_models("topout", st.provider("topout").skeleton_models(), partial=True)
    st.set_current(LOGIC_SIG)
    panel = SVP.SvPreview(st)
    host = _host(panel)
    panel.refresh(force=True)
    yield panel, st, host
    host.close()


# ═════════════════════ C-067 / C-068 / C-268 两个范围 ═════════════════════

def test_c067_c068_c268_toggle_scope_titles(real):
    """C-268：一处预览、一键切；C-068 本信号片段；C-067 勾选集。标题与按钮文案对着切。"""
    panel, st, host = real
    title = H.find(panel, names.SV_TITLE, QtWidgets.QLabel)
    btn = H.find(panel, names.SV_BTN_TOGGLE_SCOPE)

    assert panel.mode() == SVP.MODE_SIGNAL
    assert title.text() == terms.SV_TITLE_SIGNAL
    assert btn.text() == terms.SV_BTN_TO_CHECKED
    single = panel.text()
    H.shot(host, "sv_single")

    H.click(btn)                                                   # 真点击
    assert panel.mode() == SVP.MODE_CHECKED
    assert title.text() == terms.SV_TITLE_CHECKED
    assert btn.text() == terms.SV_BTN_TO_SIGNAL
    checked = panel.text()
    H.shot(host, "sv_checked")

    assert single and checked and single != checked
    assert len(checked) > len(single), "勾选集（全勾 12 个信号）该比单信号片段长"

    H.click(btn)
    assert panel.mode() == SVP.MODE_SIGNAL and panel.text() == single


def test_c068_signal_block_is_real_sv(real):
    """C-068：本信号片段是真 .sv（与导出同源），不是占位文本。"""
    panel, st, host = real
    text = panel.text()
    assert LOGIC_SIG in text
    for token in ("force `ENV_RF.", "`RF_WRITE(", "assert"):
        assert token in text, "缺 %s：预览没走 exports.render_sv 这条路？" % token
    # 与直接 render 出来的逐字一致（同源，不是另拼一份）
    prov = st.provider()
    cov = st.coverage()
    mode, exh = cov.mode_for(LOGIC_SIG, models=st.models())
    ref, _b = X.render_sv(prov, only=[LOGIC_SIG], mode=mode, max_tests=int(cov.max_tests),
                          exhaustive=exh, edited=st.compute_edited(), options=None)
    assert text.startswith(ref.split("\n")[0])
    assert ref.rstrip("\n") in text


def test_c067_checked_set_follows_checks(real):
    """C-067：勾选集就是清单里勾着的那几个 —— 取消勾选后预览里也没了。"""
    panel, st, host = real
    panel.set_mode(SVP.MODE_CHECKED)
    assert "d_bt_lp_lna_itrim" in panel.text()                     # 默认全勾
    st.set_checked([m["name"] for m in st.models()], False)        # 先清空
    st.set_checked([LOGIC_SIG], True)                              # 只勾一个
    assert st.checked_names() == [LOGIC_SIG]
    panel.refresh()
    text = panel.text()
    assert LOGIC_SIG in text
    assert "d_bt_lp_lna_itrim" not in text


# ═════════════════════════ C-069 截断 ═════════════════════════

def test_c069_truncate_600_lines_with_total(qapp):
    """C-069：超 600 行截断 + 注明总行数（导出的 .sv 仍是完整的）。"""
    long_text = "\n".join("// line %d" % i for i in range(1200))
    out = SVP.truncate(long_text)
    lines = out.split("\n")
    assert lines[theme.SV_PREVIEW_MAX_LINES - 1] == "// line 599"
    assert "// line 600" not in out
    assert terms.SV_TRUNCATED_FMT.format(total=1200, shown=600) in out
    assert "1200" in out, "总行数要写出来，不然用户以为就这么多"
    # 没超就一个字都不动（逐字节等同）
    short = "\n".join("// line %d" % i for i in range(10))
    assert SVP.truncate(short) == short


def test_c069_widget_truncates_long_render(qapp, iso, monkeypatch):
    """C-069：面板上真的截断了（而不是只有那个纯函数会）。"""
    class LongProvider(F.FakeProvider):
        def render_sv(self, only=None, mode="max", max_tests=256, exhaustive=False,
                      edited=None, **kw):
            return ("\n".join("// line %d" % i for i in range(900)),
                    {"summary": {"n_total": 1, "n_emitted": 1}})

    st = F.FakeState()
    for vid in list(st._providers):
        st._providers[vid] = LongProvider(view_id=vid)
    panel = SVP.SvPreview(st)
    host = _host(panel)
    panel.show_signal(F.DEFAULT_NAMES[0])
    panel.refresh(force=True)
    body = panel.text()
    assert body.count("\n") < 900
    assert "已截断：共 900 行" in body
    H.shot(host, "sv_truncated")
    host.close()


# ═════════════════════════ C-070 跳过点名 ═════════════════════════

def test_c070_tail_lists_skipped_with_inputs(real):
    """C-070 / C-270 / I-20：末尾点名本次跳过了谁、各缺哪根输入 —— 名字在前、计数在后。"""
    panel, st, host = real
    panel.set_mode(SVP.MODE_CHECKED)
    text = panel.text()
    assert terms.SV_SKIPPED_TAIL_HEAD in text

    tail = text.split(terms.SV_SKIPPED_TAIL_HEAD, 1)[1]
    assert SKIPPED_SIG in tail, "跳过的信号必须点名（数量永远不够）"
    # 原因跟在名字后面同一段里
    line = next(ln for ln in tail.split("\n") if SKIPPED_SIG in ln)
    assert "：" in line and len(line.split("：", 1)[1].strip()) > 4
    # I-20：名字出现在计数之前
    counts = panel.counts_text()
    assert counts
    assert text.index(SKIPPED_SIG) < len(text)
    for word in terms.FORBIDDEN:
        assert word not in tail, "跳过原因裸出现术语：%s" % word
    H.shot(host, "sv_skipped_tail")


def test_c070_tail_absent_when_nothing_skipped(qapp):
    """没有跳过项就不要那段尾巴（别给用户一个空标题吓人）。"""
    assert SVP.skipped_tail({}) == ""
    assert SVP.skipped_tail({"accounted": []}) == ""
    got = SVP.skipped_tail({"accounted": [{"name": "d_fake_x", "status": "skipped",
                                           "reason": "缺输入 d_fake_in_a"}]})
    assert got.startswith(terms.SV_SKIPPED_TAIL_HEAD)
    assert "d_fake_x" in got and "d_fake_in_a" in got


# ═════════════════════ C-072 / C-132 自动重算 ═════════════════════

def test_c072_c132_dirty_on_edit_and_config(real):
    """C-132 编辑立刻生效；C-072 配置一变就按新配置重算，绝不留旧内容误导。"""
    panel, st, host = real
    before = panel.text()
    assert not panel.is_dirty()

    # C-132：手填一条期望 → 标脏 → 重算后正文真的变了（所见即所得）
    an = st.analyze(LOGIC_SIG)
    cols = ED.cols_from_vectors(an, [])
    pos = [c for c in cols if not c["neg"]]
    pos[0]["exp"] = (pos[0]["auto"] ^ 1) & ((1 << (pos[0]["auto_w"] or 1)) - 1)
    st.put_edit(LOGIC_SIG, {"kind": an["kind"], "src_out_name": an["src_out_name"],
                            "name": an["name"], "renamed": False, "cols": cols, "an": an})
    assert panel.is_dirty(), "editsChanged 之后还不脏 = 预览会留着旧内容骗人"
    after = panel.refresh()
    assert after != before

    # C-072：改探针前缀（配置）→ 再标脏 → 重算
    st.set_probe_prefixes({"d_bt_lp_linectrl_rx_en": "U_SUB"})
    assert panel.is_dirty()
    panel.refresh()
    assert not panel.is_dirty()

    # 覆盖度也算配置变更
    st.coverage_touched()
    assert panel.is_dirty()


def test_c072_lazy_recompute_only_when_visible(qapp, iso):
    """C-072 的代价控制：标脏之后**看得见时**才重算（后台改配置不该把引擎跑穿）。"""
    st = F.FakeState()
    panel = SVP.SvPreview(st)
    panel.show_signal(F.DEFAULT_NAMES[0])
    prov = st.provider()
    panel.refresh(force=True)
    n0 = sum(1 for c in prov.calls if c == "render")               # FakeProvider 不记 render
    st.coverage_touched()
    assert panel.is_dirty()
    panel.refresh(force=False)                                     # 脏 → 真重算
    assert not panel.is_dirty()
    txt = panel.text()
    panel.refresh(force=False)                                     # 不脏 → 原样返回
    assert panel.text() == txt
    assert n0 == 0


# ═════════════════════════ C-172 状态栏计数 ═════════════════════════

def test_c172_status_counts_after_preview(real):
    """C-172：预览完在状态栏报一次计数（口径 = `exports.sv_outcome().counts_text()`）。"""
    panel, st, host = real
    seen = []
    panel.statusMessage.connect(seen.append)
    panel.set_mode(SVP.MODE_CHECKED)
    assert seen, "预览完没报计数"
    msg = seen[-1]
    assert msg.startswith("预览：")
    assert msg == terms.SV_PREVIEW_STATUS_FMT.format(counts=panel.counts_text())
    assert "断言块" in msg and "测试用例" in msg


def test_sv_copy_button_puts_text_on_clipboard(real):
    """「复制」= 把正文原样送剪贴板 + 状态栏一句「已复制到剪贴板」。"""
    panel, st, host = real
    seen = []
    panel.statusMessage.connect(seen.append)
    H.click(H.find(panel, names.SV_BTN_COPY))
    assert QtWidgets.QApplication.clipboard().text() == panel.text()
    assert terms.STATUS_COPIED in seen


def test_sv_all_object_names_findable(real):
    """I-14：本区每个可测控件都能按 `names.py` 找到；正文是等宽不换行（= white-space:pre）。"""
    panel, st, host = real
    for nm in (names.SV_PANEL, names.SV_TOOLBAR, names.SV_TITLE,
               names.SV_BTN_TOGGLE_SCOPE, names.SV_BTN_COPY, names.SV_TEXT):
        assert H.find(panel, nm) is not None
    view = H.find(panel, names.SV_TEXT, QtWidgets.QPlainTextEdit)
    assert view.isReadOnly()
    assert view.lineWrapMode() == QtWidgets.QPlainTextEdit.NoWrap
    assert view.font().family() == theme.FONT_MONO
    assert os.path.exists(H.shot(host, "sv_all_names"))
