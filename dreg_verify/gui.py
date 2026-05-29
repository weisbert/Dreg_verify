# -*- coding: utf-8 -*-
"""
gui.py — Dreg_verify 的 PySide6 图形界面（含 debug 辅助 + 测试项可视化编辑）：
  加载 Excel → 信号表(owner/类型/名字/状态筛选 + 多选 + 负向勾选)
  → 点信号看它 force/RF_WRITE 哪些 net 的明细(查 elaboration 找不到 net 的问题)
  → 「测试项」标签页：把该信号的全部测试用例列成可编辑表格(逐输入改值/期望自动重算/
     手填期望即标负向/加删复制行/重新生成/导出CSV/预览本信号.sv)
  → 「.sv 预览」标签页：预览选中信号的完整 .sv + 覆盖诊断
  → 导出 wr_rf_tc.sv（编辑过的测试项经 vector_overrides 真实回流到产物）。
后端复用 generator，与 CLI 同一套逻辑。

测试项编辑语义（核心）：一条测试项 = 各物理输入取值 → 表达式自动求出期望。
  · 改输入值 → 期望自动重算（永远自洽、永远对）。
  · 改期望值 → 若与算出值不同则该行自动标为负向(故意填错，预期断言应 FAIL)。

运行: python -m dreg_verify.gui
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from PySide6 import QtCore, QtGui, QtWidgets
except Exception as ex:  # noqa: BLE001
    raise SystemExit("需要 PySide6：pip install PySide6（原始错误：%s）" % ex)

from dreg_verify import excel_model, generator  # noqa: E402
from dreg_verify import resolver as R            # noqa: E402
from dreg_verify import expr as E                # noqa: E402
from dreg_verify import vectors as V             # noqa: E402
from dreg_verify import sv_writer as W           # noqa: E402

# 记住上次加载的 Excel，下次启动自动加载（省去重复浏览/点击）
SETTINGS_PATH = os.path.join(os.path.expanduser("~"), ".dreg_verify_gui.json")


def _load_last_excel():
    try:
        with open(SETTINGS_PATH, encoding="utf-8") as f:
            return json.load(f).get("last_excel")
    except Exception:  # noqa: BLE001
        return None


def _save_last_excel(path):
    # 测试环境(pytest)下不落盘，避免把临时表路径污染到用户的真实"上次文件"
    if "pytest" in sys.modules:
        return
    try:
        with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
            json.dump({"last_excel": path}, f)
    except Exception:  # noqa: BLE001
        pass


COL_SEL, COL_NEG, COL_R, COL_K, COL_OWNER, COL_TYPE, COL_TOP, COL_STATUS, COL_EXPR = range(9)
HEADERS = ["选", "负向", "R", "输出名(K)", "owner", "type", "top", "状态", "表达式"]
STATUS_LABEL = {"clean": "clean", "wire-fallback": "⚠wire兜底",
                "unresolved": "✗未解析", "parse-err": "✗解析错"}
NEG_BG = QtGui.QColor("#fff3f3")        # 负向用例行底色（与报告 HTML 一致）


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Dreg_verify — wr_rf_tc.sv 生成器 + 测试项编辑")
        self.resize(1360, 800)
        self.wb = None
        self.signals = []
        self._analysis = {}     # row index -> generator.analyze_signal 结果
        self._resolver = None
        # ── 测试项编辑状态 ──
        self._edited = {}        # out_name(小写) -> {"sig":LogicSignal, "rows":[rowdict]}
        self._customized = set() # 被用户改过的信号名(小写)，仅这些走 vector_overrides
        self._neg_only = set()   # 定制仅来自"加负向"(正向全自动)的信号——清负向时可整体撤销定制
        self._ti_sig = None      # 当前在编辑器里的信号
        self._ti_node = None
        self._ti_bindings = {}
        self._ti_groups = []
        self._ti_rows = []
        self._ti_name_low = None
        self._ti_loaded_idx = None
        self._ti_loading = False  # 程序化填表时屏蔽 itemChanged，防递归
        self._sig_loading = False # 程序化改信号表(含左侧负向勾选)时屏蔽其 itemChanged
        self._build_ui()

    # ───────────── UI ─────────────
    def _build_ui(self):
        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        root = QtWidgets.QVBoxLayout(central)

        top = QtWidgets.QHBoxLayout()
        self.path_edit = QtWidgets.QLineEdit()
        self.path_edit.setPlaceholderText("选择 Dreg 核心 Excel (.xlsx) ...")
        browse = QtWidgets.QPushButton("浏览…"); browse.clicked.connect(self.on_browse)
        load = QtWidgets.QPushButton("加载"); load.clicked.connect(self.on_load)
        top.addWidget(QtWidgets.QLabel("Excel:")); top.addWidget(self.path_edit, 1)
        top.addWidget(browse); top.addWidget(load)
        root.addLayout(top)

        flt = QtWidgets.QHBoxLayout()
        self.owner_combo = QtWidgets.QComboBox(); self.owner_combo.addItem("全部 owner")
        self.owner_combo.currentIndexChanged.connect(self.apply_filter)
        self.type_combo = QtWidgets.QComboBox(); self.type_combo.addItem("全部 type")
        self.type_combo.currentIndexChanged.connect(self.apply_filter)
        self.status_combo = QtWidgets.QComboBox()
        self.status_combo.addItems(["全部状态", "仅 clean", "仅有问题(非clean)"])
        self.status_combo.currentIndexChanged.connect(self.apply_filter)
        self.name_edit = QtWidgets.QLineEdit(); self.name_edit.setPlaceholderText("名字/正则搜索…")
        self.name_edit.textChanged.connect(self.apply_filter)
        self.top_only = QtWidgets.QCheckBox("仅 top_output=1")    # 默认显示全部，便于 debug
        self.top_only.stateChanged.connect(self.apply_filter)
        for w in (QtWidgets.QLabel("筛选:"), self.owner_combo, self.type_combo,
                  self.status_combo, self.name_edit, self.top_only):
            flt.addWidget(w, 1 if w is self.name_edit else 0)
        flt.addStretch(1)
        root.addLayout(flt)

        splitter = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        # 左：信号表 + 批量操作条 + 明细
        left = QtWidgets.QSplitter(QtCore.Qt.Vertical)
        top_box = QtWidgets.QWidget()
        tv = QtWidgets.QVBoxLayout(top_box); tv.setContentsMargins(0, 0, 0, 0); tv.setSpacing(3)
        self.table = QtWidgets.QTableWidget(0, len(HEADERS))
        self.table.setHorizontalHeaderLabels(HEADERS)
        self.table.setSortingEnabled(True)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.table.currentCellChanged.connect(self.on_row_focus)
        self.table.itemChanged.connect(self.on_signal_table_item_changed)  # 左侧"负向"勾选→联动右表
        tv.addWidget(self.table)
        # 批量操作条（就近放在信号表下方，符合使用习惯）
        bulk = QtWidgets.QHBoxLayout()
        b_selall = QtWidgets.QPushButton("全选输出(可见)"); b_selall.setToolTip("勾选所有可见信号的'选'")
        b_selall.clicked.connect(lambda: self.set_all_visible(True))
        b_selnone = QtWidgets.QPushButton("清空选择"); b_selnone.clicked.connect(lambda: self.set_all_visible(False))
        b_negall = QtWidgets.QPushButton("全部加负向"); b_negall.setToolTip("给所有可见信号都加负向测试(按下方 first/all)")
        b_negall.clicked.connect(lambda: self.on_all_signals_neg(True))
        b_negnone = QtWidgets.QPushButton("清除负向"); b_negnone.setToolTip("清除所有可见信号的负向测试")
        b_negnone.clicked.connect(lambda: self.on_all_signals_neg(False))
        for b in (b_selall, b_selnone):
            bulk.addWidget(b)
        bulk.addSpacing(16); bulk.addWidget(QtWidgets.QLabel("负向:"))
        for b in (b_negall, b_negnone):
            bulk.addWidget(b)
        bulk.addStretch(1)
        tv.addLayout(bulk)
        left.addWidget(top_box)
        self.detail = QtWidgets.QPlainTextEdit(); self.detail.setReadOnly(True)
        self.detail.setMaximumHeight(200)
        self._mono(self.detail)
        left.addWidget(self.detail)
        left.setSizes([540, 170])
        splitter.addWidget(left)

        # 右：标签页（测试项编辑 / .sv 预览）
        self.tabs = QtWidgets.QTabWidget()
        self.tabs.addTab(self._build_testitems_tab(), "测试项")
        self.preview_tab = QtWidgets.QWidget()
        pv = QtWidgets.QVBoxLayout(self.preview_tab); pv.setContentsMargins(0, 0, 0, 0)
        self.preview = QtWidgets.QPlainTextEdit(); self.preview.setReadOnly(True)
        self.preview.setLineWrapMode(QtWidgets.QPlainTextEdit.NoWrap)
        self._mono(self.preview)
        pv.addWidget(self.preview)
        self.tabs.addTab(self.preview_tab, ".sv 预览")
        splitter.addWidget(self.tabs)
        splitter.setSizes([640, 700])
        root.addWidget(splitter, 1)

        opt = QtWidgets.QHBoxLayout()
        self.mode_combo = QtWidgets.QComboBox(); self.mode_combo.addItems(["min", "max"])
        self.exhaustive = QtWidgets.QCheckBox("小信号全穷举")
        self.max_tests = QtWidgets.QSpinBox(); self.max_tests.setRange(1, 100000); self.max_tests.setValue(256)
        self.comments = QtWidgets.QCheckBox("加注释")
        self.neg_mode = QtWidgets.QComboBox(); self.neg_mode.addItems(["invert", "inc", "value"])
        self.neg_which = QtWidgets.QComboBox(); self.neg_which.addItems(["first", "all"])
        self.neg_separate = QtWidgets.QCheckBox("负向单独出文件")
        for w in (QtWidgets.QLabel("向量:"), self.mode_combo, self.exhaustive,
                  QtWidgets.QLabel("上限"), self.max_tests, self.comments,
                  QtWidgets.QLabel("  负向:"), self.neg_mode, self.neg_which, self.neg_separate):
            opt.addWidget(w)
        opt.addStretch(1)
        root.addLayout(opt)

        btns = QtWidgets.QHBoxLayout()
        diag = QtWidgets.QPushButton("覆盖诊断"); diag.clicked.connect(self.on_diagnose)
        prev = QtWidgets.QPushButton("预览选中"); prev.clicked.connect(self.on_preview)
        rep = QtWidgets.QPushButton("导出报告(HTML/CSV)…"); rep.clicked.connect(self.on_report)
        rep.setToolTip("出'给人看'的测试用例报告(汇总+每信号真值表+完整明细)，自动带上你的编辑；"
                       "未勾选则覆盖全部信号")
        gen = QtWidgets.QPushButton("生成 .sv …"); gen.clicked.connect(self.on_generate)
        btns.addWidget(diag); btns.addStretch(1); btns.addWidget(prev); btns.addWidget(rep); btns.addWidget(gen)
        root.addLayout(btns)

        self.status = self.statusBar()
        self.status.showMessage("请选择并加载 Excel。")

    def _build_testitems_tab(self):
        """测试项编辑标签页：表头说明 + 工具条 + 可编辑表格。"""
        page = QtWidgets.QWidget()
        lay = QtWidgets.QVBoxLayout(page); lay.setContentsMargins(4, 4, 4, 4)
        self.ti_header = QtWidgets.QLabel("点左侧任一信号，这里列出它的测试项（可编辑）。")
        self.ti_header.setWordWrap(True)
        self.ti_header.setStyleSheet("color:#445;")
        lay.addWidget(self.ti_header)

        bar = QtWidgets.QHBoxLayout()
        defs = [("重新生成", self.on_ti_regen, "丢弃本信号自定义，按当前向量选项从表达式重新生成"),
                ("加正向列", self.on_ti_add, "新增一条正向(真实)测试(输入全 0，期望自动算)"),
                ("复制列", self.on_ti_copy, "复制当前选中的测试列"),
                ("删除列", self.on_ti_del, "删除选中的测试列"),
                ("重命名列…", self.on_ti_rename_current, "给用户新增的测试列改名(双击列头亦可；自动生成的 T0/T1 不可改)"),
                ("加负向", self.on_ti_add_neg, "为本信号追加负向测试(按下方 first/all；正向测试不动)"),
                ("删负向", self.on_ti_del_neg, "删除本信号所有负向测试(保留正向)"),
                ("预览本信号.sv", self.on_ti_preview_signal, "用当前(含编辑)测试项渲染该信号的 .sv 片段"),
                ("导出CSV", self.on_ti_export_csv, "把本信号测试项导出为 CSV(Excel 可开)")]
        for text, slot, tip in defs:
            b = QtWidgets.QPushButton(text); b.clicked.connect(slot); b.setToolTip(tip)
            bar.addWidget(b)
        bar.addStretch(1)
        lay.addLayout(bar)

        self.ti_table = QtWidgets.QTableWidget(0, 0)
        self._mono(self.ti_table)
        self.ti_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectItems)
        self.ti_table.setAlternatingRowColors(True)
        self.ti_table.itemChanged.connect(self.on_ti_item_changed)
        self.ti_table.horizontalHeader().sectionDoubleClicked.connect(self.on_ti_rename_col)
        lay.addWidget(self.ti_table, 1)

        hint = QtWidgets.QLabel("纵向真值表：每行一个输入/输出，每列一条测试 T0/T1…。"
                                "改输入值→期望自动重算；改期望值或勾“负向”→该列标为故意填错(预期 FAIL)。"
                                "编辑会在生成/预览的 .sv 里生效。")
        hint.setWordWrap(True); hint.setStyleSheet("color:#888;font-size:11px;")
        lay.addWidget(hint)
        return page

    def _mono(self, w):
        f = w.font(); f.setFamily("Consolas"); w.setFont(f)

    # ───────────── 加载 + 分析 ─────────────
    def on_browse(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "选择 Excel", "", "Excel (*.xlsx)")
        if path:
            self.path_edit.setText(path)
            self.on_load()        # 选完即加载，无需再点"加载"

    def on_load(self):
        path = self.path_edit.text().strip()
        if not path or not os.path.isfile(path):
            QtWidgets.QMessageBox.warning(self, "提示", "请先选择有效的 .xlsx 文件")
            return
        try:
            self.wb = excel_model.load_workbook(path)
        except Exception as ex:  # noqa: BLE001
            QtWidgets.QMessageBox.critical(self, "加载失败", str(ex))
            return
        self.signals = list(self.wb.logic)
        # 解析画像：逐信号 try，一个坏信号不连累整体加载
        self._resolver = R.Resolver(self.wb)
        self._analysis = {}
        # 切换工作簿，清空旧的测试项编辑状态
        self._edited = {}
        self._customized = set()
        self._neg_only = set()
        self._ti_loaded_idx = None
        self._clear_test_items("加载完成，点左侧信号查看测试项。")
        errs = []
        for i, s in enumerate(self.signals):
            try:
                self._analysis[i] = generator.analyze_signal(self._resolver, s)
            except Exception as ex:  # noqa: BLE001
                self._analysis[i] = {"status": "解析异常", "inputs": [], "out_net": "",
                                     "error": repr(ex)}
                errs.append((s.out_name, s.assert_id, repr(ex)))
        self._populate_filters()
        self._populate_table()
        nbad = sum(1 for a in self._analysis.values() if a["status"] != "clean")
        msg = ("已加载 %d 信号（%d 个非 clean）；tmm字段=%d regmap=%d"
               % (len(self.signals), nbad, len(self.wb.tmm), len(self.wb.regmap)))
        if errs:
            msg += "；⚠ %d 个信号分析异常(状态'解析异常',点开看 error)" % len(errs)
            self.preview.setPlainText("分析异常的信号(请把下面发给维护者):\n" +
                                      "\n".join("R=%s %s: %s" % (a, n, e) for n, a, e in errs[:50]))
        _save_last_excel(path)         # 记住这次的文件，下次启动自动加载
        self.status.showMessage(msg)

    def _populate_filters(self):
        owners = sorted({s.owner for s in self.signals if s.owner})
        types = sorted({s.suffix for s in self.signals if s.suffix})
        for combo, items, head in ((self.owner_combo, owners, "全部 owner"),
                                   (self.type_combo, types, "全部 type")):
            combo.blockSignals(True); combo.clear(); combo.addItem(head); combo.addItems(items)
            combo.blockSignals(False)

    def _populate_table(self):
        self._sig_loading = True
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(self.signals))
        for r, sig in enumerate(self.signals):
            try:
                self._set_check(r, COL_SEL, False)
                self._set_check(r, COL_NEG, False)
                self._set_text(r, COL_R, str(sig.assert_id))
                self._set_text(r, COL_K, sig.out_name)
                self._set_text(r, COL_OWNER, sig.owner)
                self._set_text(r, COL_TYPE, sig.suffix)
                self._set_text(r, COL_TOP, str(sig.top_output))
                st = self._analysis.get(r, {}).get("status", "?")
                it = QtWidgets.QTableWidgetItem(STATUS_LABEL.get(st, st))
                if st != "clean":
                    it.setForeground(QtGui.QColor("red"))
                self.table.setItem(r, COL_STATUS, it)
                self._set_text(r, COL_EXPR, sig.expr)
                self.table.item(r, COL_R).setData(QtCore.Qt.UserRole, r)
            except Exception:  # noqa: BLE001  单行异常不连累整表
                self._set_text(r, COL_R, str(getattr(sig, "assert_id", "?")))
                self._set_text(r, COL_K, getattr(sig, "out_name", "?"))
                self.table.item(r, COL_R).setData(QtCore.Qt.UserRole, r)
        self.table.setSortingEnabled(True)
        self.table.resizeColumnsToContents()
        self._sig_loading = False
        self.apply_filter()

    def _set_text(self, r, c, text):
        self.table.setItem(r, c, QtWidgets.QTableWidgetItem(text or ""))

    def _set_check(self, r, c, checked):
        it = QtWidgets.QTableWidgetItem()
        it.setFlags(QtCore.Qt.ItemIsUserCheckable | QtCore.Qt.ItemIsEnabled)
        it.setCheckState(QtCore.Qt.Checked if checked else QtCore.Qt.Unchecked)
        self.table.setItem(r, c, it)

    # ───────────── 筛选 ─────────────
    def apply_filter(self):
        import re
        owner = self.owner_combo.currentText()
        typ = self.type_combo.currentText()
        statusf = self.status_combo.currentText()
        pat = self.name_edit.text().strip()
        rx = None
        if pat:
            try:
                rx = re.compile(pat, re.I)
            except re.error:
                rx = None
        top_only = self.top_only.isChecked()
        visible = 0
        for r in range(self.table.rowCount()):
            sig = self._sig_of_row(r)
            st = self._analysis[self._idx_of_row(r)]["status"]
            show = True
            if owner != "全部 owner" and sig.owner != owner:
                show = False
            if typ != "全部 type" and sig.suffix != typ:
                show = False
            if statusf == "仅 clean" and st != "clean":
                show = False
            if statusf == "仅有问题(非clean)" and st == "clean":
                show = False
            if rx and not (rx.search(sig.out_name) or rx.search(sig.expr)):
                show = False
            elif pat and not rx and pat.lower() not in (sig.out_name + sig.expr).lower():
                show = False
            if top_only and str(sig.top_output).strip() not in ("1", "1.0", "True", "true"):
                show = False
            self.table.setRowHidden(r, not show)
            visible += show
        self.status.showMessage("可见信号 %d / 共 %d" % (visible, len(self.signals)))

    def _idx_of_row(self, r):
        return self.table.item(r, COL_R).data(QtCore.Qt.UserRole)

    def _sig_of_row(self, r):
        return self.signals[self._idx_of_row(r)]

    def set_all_visible(self, checked):
        for r in range(self.table.rowCount()):
            if not self.table.isRowHidden(r):
                self.table.item(r, COL_SEL).setCheckState(
                    QtCore.Qt.Checked if checked else QtCore.Qt.Unchecked)

    # ───────────── 左侧"负向"列 → 联动测试项 ─────────────
    def on_signal_table_item_changed(self, item):
        """勾/取消左侧"负向"列 = 给该信号的测试项批量设/清负向(按下方 first/all)。
        这样左右两边是同一套负向，不再各管各的。"""
        if self._sig_loading or item is None or item.column() != COL_NEG:
            return
        r = item.row()
        sig = self._sig_of_row(r)
        want = item.checkState() == QtCore.Qt.Checked
        self._set_signal_negatives(sig, want, self.neg_which.currentText())
        if self._idx_of_row(r) == self._ti_loaded_idx:    # 正在编辑该信号→刷新右表
            self._load_test_items(sig)
        self.status.showMessage(
            "%s 已%s负向(%s)" % (sig.out_name, "标记" if want else "清除",
                               "全部用例" if self.neg_which.currentText() == "all" else "首条用例"))

    def _set_signal_negatives(self, sig, want_neg, which):
        """给某信号(重新)设置负向测试：保留全部正向测试，按 first/all 追加正向测试的"故意填错"
        副本作为负向；want_neg=False 则删除所有负向。可作用于未显示的信号(存进 override)。"""
        if self._resolver is None:
            return
        name_low = sig.out_name.lower()
        try:
            node = E.parse(sig.expr)
        except E.ExprError:
            return
        bindings = self._resolver.resolve_signal_inputs(sig)
        groups = V.input_groups(node, bindings)
        hand_edited = (name_low in self._edited and name_low not in self._neg_only)
        # 仅靠"加负向"定制的信号，清负向 = 回到纯自动 → 整体撤销定制(恢复默认 risky-skip 等行为)
        if not want_neg and not hand_edited:
            self._edited.pop(name_low, None)
            self._customized.discard(name_low)
            self._neg_only.discard(name_low)
            return
        rows = (self._edited[name_low]["rows"] if name_low in self._edited
                else self._auto_rows(sig, node, bindings, groups))
        pos_rows = [rd for rd in rows if rd.get("kind") != "neg"]
        if not pos_rows:
            return
        # 现存负向行按其输入取值建索引，重建时继承用户对负向列的改名/手填错值(不静默丢失)
        old_negs = {}
        for rd in rows:
            if rd.get("kind") == "neg":
                old_negs.setdefault(tuple(sorted(rd["base_values"].items())), []).append(rd)
        new_rows = list(pos_rows)                      # 正向测试原样保留
        if want_neg:
            targets = pos_rows if which == "all" else pos_rows[:1]
            for prd in targets:                        # 每个(或首个)正向 → 追加一条负向副本
                neg = {"base_values": dict(prd["base_values"]),
                       "kind": "neg", "wrong_value": None, "user_added": True,
                       "note": "负向(真实测试的故意填错副本)"}
                bucket = old_negs.get(tuple(sorted(prd["base_values"].items())))
                if bucket:                             # 继承同源旧负向的自定义名与手填错值
                    old = bucket.pop(0)
                    if old.get("name") is not None:
                        neg["name"] = old["name"]
                    if old.get("wrong_value") is not None:
                        neg["wrong_value"] = old["wrong_value"]
                new_rows.append(neg)
        for rd in new_rows:
            self._recompute_row(node, bindings, groups, sig.out_width, rd)
        self._edited[name_low] = {"sig": sig, "rows": new_rows}
        self._customized.add(name_low)
        if hand_edited:
            self._neg_only.discard(name_low)
        elif want_neg:
            self._neg_only.add(name_low)       # 正向全自动、仅加了负向
        else:
            self._neg_only.discard(name_low)

    def _sync_left_neg(self):
        """编辑器里负向有变 → 回写左侧"负向"勾选(任一用例为负向则勾上)。"""
        if not self._ti_sig:
            return
        any_neg = any(rd.get("is_negative") for rd in self._ti_rows)
        for r in range(self.table.rowCount()):
            if self._idx_of_row(r) == self._ti_loaded_idx:
                cell = self.table.item(r, COL_NEG)
                if cell is None:
                    return
                self._sig_loading = True
                try:
                    cell.setCheckState(QtCore.Qt.Checked if any_neg else QtCore.Qt.Unchecked)
                finally:
                    self._sig_loading = False
                return

    def on_all_signals_neg(self, want):
        """对所有可见信号一键加/清负向(满足'R 全部负向')。受筛选范围限制，可先筛再点。"""
        if not self.wb:
            return
        which = self.neg_which.currentText()
        self._sig_loading = True
        n = 0
        try:
            for r in range(self.table.rowCount()):
                if self.table.isRowHidden(r):
                    continue
                sig = self._sig_of_row(r)
                self._set_signal_negatives(sig, want, which)
                cell = self.table.item(r, COL_NEG)
                if cell is not None:
                    cell.setCheckState(QtCore.Qt.Checked if want else QtCore.Qt.Unchecked)
                n += 1
        finally:
            self._sig_loading = False
        if self._ti_sig is not None and self._ti_loaded_idx is not None:
            self._load_test_items(self._ti_sig)   # 当前编辑器信号若被影响则刷新
        self.status.showMessage("已对 %d 个可见信号%s负向测试(%s)"
                                % (n, "添加" if want else "清除",
                                   "每条正向各一" if which == "all" else "仅首条正向"))

    # ───────────── 点信号看明细（debug 关键） ─────────────
    def on_row_focus(self, row, col, prow, pcol):
        if row < 0 or not self.signals:
            return
        idx = self._idx_of_row(row)
        a = self._analysis.get(idx)
        sig = self.signals[idx]
        if a is None:
            return
        lines = ["信号: %s   (assert_%s, %s, top_output=%s)"
                 % (sig.out_name, sig.assert_id, sig.suffix, sig.top_output),
                 "表达式: %s" % sig.expr,
                 "状态: %s" % STATUS_LABEL.get(a["status"], a["status"]),
                 "断言: assert (%s == <期望>)" % a["out_net"], ""]
        if a["error"]:
            lines.append("解析错误: %s" % a["error"])
        for inp in a["inputs"]:
            flag = "" if inp["resolved"] else "  ✗"
            lines.append("  %s=%s  [%s/%s]%s  ->  %s"
                         % (inp["letter"], inp["base"], inp["kind"], inp["found_in"], flag, inp["net"]))
            if inp["note"]:
                lines.append("        note: %s" % inp["note"])
        lines.append("")
        lines.append("提示: elaboration 报 CUVUNF 找不到 net 时，对比这里的 force/输出 net 名是否真存在于 ENV_RF 层；"
                     "⚠wire兜底/✗未解析 的最可疑。")
        self.detail.setPlainText("\n".join(lines))
        # 仅当信号变化时刷新测试项编辑表（避免同行换列时重建）
        if self._ti_loaded_idx != idx:
            self._ti_loaded_idx = idx
            self._load_test_items(sig)

    # ───────────── 测试项编辑器 ─────────────
    def _clear_test_items(self, header_text):
        self._ti_sig = None; self._ti_node = None
        self._ti_bindings = {}; self._ti_groups = []; self._ti_rows = []
        self._ti_name_low = None
        self.ti_header.setText(header_text)
        self._ti_loading = True
        try:
            self.ti_table.clear()
            self.ti_table.setRowCount(0)
            self.ti_table.setColumnCount(0)
        finally:
            self._ti_loading = False

    def _load_test_items(self, sig):
        if not self.wb or self._resolver is None or sig is None:
            return
        name_low = sig.out_name.lower()
        try:
            node = E.parse(sig.expr)
        except E.ExprError as ex:
            self._clear_test_items("信号: %s — 表达式解析失败: %s（无法生成测试项）" % (sig.out_name, ex))
            return
        bindings = self._resolver.resolve_signal_inputs(sig)
        groups = V.input_groups(node, bindings)
        self._ti_sig = sig; self._ti_node = node
        self._ti_bindings = bindings; self._ti_groups = groups
        self._ti_name_low = name_low
        if name_low in self._edited:
            self._ti_rows = self._edited[name_low]["rows"]
            custom = True
        else:
            self._ti_rows = self._auto_rows(sig, node, bindings, groups)
            custom = False
        for rd in self._ti_rows:
            self._ti_recompute(rd)
        self._update_ti_header(custom)
        self._ti_populate()

    def _auto_rows(self, sig, node, bindings, groups):
        """按当前向量选项自动生成测试项 → rowdict 列表。"""
        try:
            vecs, _meta = V.generate_vectors(
                node, bindings, sig.out_width,
                mode=self.mode_combo.currentText(), max_tests=self.max_tests.value(),
                exhaustive=self.exhaustive.isChecked())
        except E.ExprError:
            vecs = []
        rows = []
        for vec in vecs:
            rows.append({
                "base_values": V.vector_to_base_values(vec, groups),
                "kind": "pos",          # 自动生成的都是正向(真实)测试
                "note": vec.note,
            })
        return rows

    def _recompute_row(self, node, bindings, groups, out_width, rd):
        """重算一行 correct/expected/is_negative，并缓存向量。显式传上下文，可对任意信号重算。

        rd['kind']:
          'pos' —— 正向(真实)测试：期望永远 = 表达式算出的正确值，不会被改坏；
          'neg' —— 负向(故意填错)测试：期望 = 错误值(rd['wrong_value'] 手填，或按 neg_mode 从 correct 派生)。
        """
        try:
            base_vec = V.make_vector_from_base_values(
                node, bindings, groups, rd["base_values"], out_width)
        except E.ExprError:
            # 表达式含缺位宽变量等无法求值：保底，不让编辑面板崩
            rd["correct"] = 0
            rd["correct_width"] = out_width or 1
            rd["is_negative"] = (rd.get("kind") == "neg")
            rd["expected"] = 0
            rd["_vec"] = None
            return
        correct, w = base_vec.exp_value, base_vec.exp_width
        m = E.mask(w)
        rd["correct"] = correct
        rd["correct_width"] = w
        if rd.get("kind") == "neg":
            wv = rd.get("wrong_value")
            if wv is None:                          # 未手填 → 按 neg_mode(invert/inc/value)派生
                wrong = V.make_negative(base_vec, mode=self.neg_mode.currentText()).neg_value
            else:
                wrong = wv & m
                if wrong == correct:                # 手填值恰等于正确值 → 强制翻一位保证"错"
                    wrong = (~correct) & m
                    if wrong == correct:
                        wrong = correct ^ 1
            rd["expected"] = wrong
            rd["is_negative"] = True
            rd["_vec"] = V.make_vector_from_base_values(
                node, bindings, groups, rd["base_values"], out_width, expected_override=wrong)
        else:
            rd["expected"] = correct
            rd["is_negative"] = False
            rd["_vec"] = base_vec

    def _ti_recompute(self, rd):
        """对当前编辑器显示信号的行重算（薄封装 _recompute_row）。"""
        self._recompute_row(self._ti_node, self._ti_bindings, self._ti_groups,
                            self._ti_sig.out_width, rd)

    def _drive_strs(self, rd):
        """该行的 force / RF_WRITE 驱动文本（供表格与 CSV 展示）。"""
        vec = rd.get("_vec")
        if vec is None and "correct" not in rd:
            self._ti_recompute(rd); vec = rd["_vec"]
        if vec is None:
            return "", ""
        try:
            forces, writes, _unres = W.compute_drives(
                vec, self._ti_bindings, E.collect_vars(self._ti_node))
        except Exception:  # noqa: BLE001
            return "", ""
        fs = "; ".join("%s=%s" % (f["wire"], f["hex"]) for f in forces)
        ws = "; ".join("%s=%s" % (w["addr"], w["hex"]) for w in writes)
        return fs, ws

    def _update_ti_header(self, custom):
        tag = "   [已自定义★]" if custom else ""
        self.ti_header.setText("信号: %s    表达式: %s    用例 %d 条%s"
                               % (self._ti_sig.out_name, self._ti_sig.expr,
                                  len(self._ti_rows), tag))

    def _ti_mark_customized(self):
        if not self._ti_sig:
            return
        self._edited[self._ti_name_low] = {"sig": self._ti_sig, "rows": self._ti_rows}
        self._customized.add(self._ti_name_low)
        self._neg_only.discard(self._ti_name_low)   # 有手工编辑 → 不再是"纯负向定制"
        self._update_ti_header(True)
        self._sync_left_neg()        # 右表负向变化 → 回写左侧"负向"勾选

    # 纵向(真值表)布局：每个输入/输出一行(纵表头)，每条测试一列 T0/T1...。
    #   行: 0..G-1 = 各输入(base)；R_EXP = 期望(out)。
    #   列: 每列一条测试用例(正向 或 负向；负向列标红、列头带 _NEG)。
    def _ti_dims(self):
        self._ti_G = len(self._ti_groups)
        self.R_EXP = self._ti_G
        return self._ti_G + 1     # 总行数 = 输入数 + 1(期望)

    @staticmethod
    def _fmt_val(val, width):
        """单元格里的取值显示：1 位 → 0/1；多位 → 0xNN（真值表观感）。"""
        if width <= 1:
            return str(val & 1)
        return "0x%X" % val

    @staticmethod
    def _ti_label(rd, idx):
        """列头/测试名显示：自定义 name 优先，否则 T<idx>；负向保证带 _NEG。"""
        nm = rd.get("name")
        label = nm if nm else ("T%d" % idx)
        if rd.get("is_negative") and not label.upper().endswith("NEG"):
            label += "_NEG"
        return label

    def _ti_populate(self):
        self._ti_loading = True
        try:
            self.ti_table.clear()
            nrows = self._ti_dims()
            ntests = len(self._ti_rows)
            self.ti_table.setRowCount(nrows)
            self.ti_table.setColumnCount(ntests)
            out_w = self._ti_sig.out_width if self._ti_sig else 1
            exp_label = "期望(out)%s" % ("[%d:0]" % (out_w - 1) if out_w and out_w > 1 else "")
            vlabels = [g.get("label", g["base"]) for g in self._ti_groups] + [exp_label]
            self.ti_table.setVerticalHeaderLabels(vlabels)
            for i, g in enumerate(self._ti_groups):
                hi = self.ti_table.verticalHeaderItem(i)
                if hi:
                    hi.setToolTip("%s  (%s, %dbit%s)" % (g["label"], g["kind"], g["width"],
                                                         ", 控制位" if g["is_control"] else ""))
            self.ti_table.setHorizontalHeaderLabels(["T%d" % i for i in range(ntests)])
        finally:
            self._ti_loading = False
        for c in range(len(self._ti_rows)):
            self._ti_render_col(c)
        self.ti_table.resizeColumnsToContents()

    def _mk_item(self, text, editable):
        it = QtWidgets.QTableWidgetItem(text)
        flags = QtCore.Qt.ItemIsEnabled | QtCore.Qt.ItemIsSelectable
        if editable:
            flags |= QtCore.Qt.ItemIsEditable
        it.setFlags(flags)
        return it

    def _ti_render_col(self, c):
        """渲染第 c 列（第 c 条测试）的整列单元格。"""
        if c < 0 or c >= len(self._ti_rows):
            return
        rd = self._ti_rows[c]
        w = rd.get("correct_width") or 1
        neg = rd["is_negative"]
        fs, ws = self._drive_strs(rd)
        self._ti_loading = True
        try:
            for i, g in enumerate(self._ti_groups):
                val = rd["base_values"].get(g["base"].lower(), 0)
                it = self._mk_item(self._fmt_val(val, g["width"]), True)
                it.setTextAlignment(QtCore.Qt.AlignCenter)
                self.ti_table.setItem(i, c, it)
            # 期望：正向只读(永远=正确值，不可改坏)；负向可编辑(可手填具体错值)
            expit = self._mk_item(self._fmt_val(rd["expected"] & E.mask(w), w), neg)
            expit.setTextAlignment(QtCore.Qt.AlignCenter)
            tip = "期望 bin: %s" % W.fmt_bin(rd["expected"], w)
            if neg:
                tip += "\n(负向：故意填错，正确应为 %s；可双击改这个错值)" % self._fmt_val(rd["correct"], w)
            if fs:
                tip += "\nforce: %s" % fs
            if ws:
                tip += "\nRF_WRITE: %s" % ws
            expit.setToolTip(tip)
            self.ti_table.setItem(self.R_EXP, c, expit)
            # 列头：自定义名/T<n>(负向带 _NEG，红字)；tooltip 提示可否改名 + 驱动
            hh = self.ti_table.horizontalHeaderItem(c)
            if hh:
                hh.setText(self._ti_label(rd, c))
                rename_hint = "双击列头可改名" if rd.get("user_added") else "自动生成，名字不可改"
                hh.setToolTip("%s · %s\nforce: %s\nRF_WRITE: %s"
                              % ("负向(故意填错)" if neg else "正向(真实)", rename_hint,
                                 fs or "(无)", ws or "(无)"))
                hh.setForeground(QtGui.QColor("red") if neg else QtGui.QColor("black"))
            if neg:
                for r in range(self.ti_table.rowCount()):
                    cell = self.ti_table.item(r, c)
                    if cell is not None:
                        cell.setBackground(NEG_BG)
        finally:
            self._ti_loading = False

    @staticmethod
    def _parse_int(s):
        """宽松解析用户输入的数值：16'hA / 0x.. / hA / 'b.. / 'd.. / 十进制 / 裸 hex。"""
        import re
        t = str(s).strip().lower().replace(" ", "")
        if t == "":
            return 0
        m = re.search(r"'h([0-9a-f]+)$", t)
        if m:
            return int(m.group(1), 16)
        m = re.search(r"'b([01]+)$", t)
        if m:
            return int(m.group(1), 2)
        m = re.search(r"'d(\d+)$", t)
        if m:
            return int(m.group(1), 10)
        if t.startswith("0x"):
            return int(t, 16)
        if t.startswith("h"):
            return int(t[1:], 16)
        if re.fullmatch(r"\d+", t):
            return int(t, 10)
        if re.fullmatch(r"[0-9a-f]+", t):
            return int(t, 16)
        raise ValueError("无法识别 %r（用 0x.. 或 16'h.. 或纯数字）" % s)

    def on_ti_item_changed(self, item):
        if self._ti_loading or not self._ti_sig:
            return
        r, c = item.row(), item.column()       # 列 c = 第 c 条测试；行 r = 输入/期望/负向
        if c < 0 or c >= len(self._ti_rows):
            return
        rd = self._ti_rows[c]
        try:
            if 0 <= r < len(self._ti_groups):
                g = self._ti_groups[r]
                val = self._parse_int(item.text()) & E.mask(g["width"])
                rd["base_values"][g["base"].lower()] = val
                self._ti_recompute(rd)        # 正向→期望自动重算；负向→错值随之重算
            elif r == self.R_EXP:
                if rd.get("kind") != "neg":   # 正向期望只读，不接受手改(防御)
                    return
                rd["wrong_value"] = self._parse_int(item.text()) & E.mask(rd.get("correct_width") or 1)
                self._ti_recompute(rd)
            else:
                return
        except ValueError as ex:
            self.status.showMessage("数值解析失败: %s（已还原）" % ex)
        self._ti_render_col(c)
        self._ti_mark_customized()

    def on_ti_add(self):
        if not self._ti_sig:
            QtWidgets.QMessageBox.information(self, "提示", "请先在左侧选择一个信号")
            return
        rd = {"base_values": {g["base"].lower(): 0 for g in self._ti_groups},
              "kind": "pos", "note": "", "user_added": True}   # 用户新增正向列(可改名)
        self._ti_recompute(rd)
        self._ti_rows.append(rd)
        self._ti_mark_customized()
        self._ti_populate()
        self.ti_table.setCurrentCell(0, len(self._ti_rows) - 1)

    def on_ti_add_neg(self):
        """给本信号添加负向测试(按 first/all)：复制正向测试为故意填错的副本，正向测试不动。"""
        if not self._ti_sig:
            QtWidgets.QMessageBox.information(self, "提示", "请先在左侧选择一个信号")
            return
        self._set_signal_negatives(self._ti_sig, True, self.neg_which.currentText())
        self._load_test_items(self._ti_sig)
        self._sync_left_neg()
        n = sum(1 for rd in self._ti_rows if rd.get("kind") == "neg")
        self.status.showMessage("已为 %s 添加 %d 条负向测试(正向测试未改动)" % (self._ti_sig.out_name, n))

    def on_ti_del_neg(self):
        """删除本信号所有负向测试，保留正向。"""
        if not self._ti_sig:
            return
        self._set_signal_negatives(self._ti_sig, False, self.neg_which.currentText())
        self._load_test_items(self._ti_sig)
        self._sync_left_neg()
        self.status.showMessage("已删除 %s 的全部负向测试" % self._ti_sig.out_name)

    # ───────────── 测试列改名（仅用户新增的可改） ─────────────
    @staticmethod
    def _sanitize_name(s):
        """把用户输入清成合法的 SV 标号片段(只留字母/数字/下划线)。"""
        import re
        return re.sub(r"[^0-9A-Za-z_]", "_", str(s).strip())

    def on_ti_rename_current(self):
        c = self.ti_table.currentColumn()
        if c < 0:
            QtWidgets.QMessageBox.information(self, "提示", "请先选中一个测试列")
            return
        self.on_ti_rename_col(c)

    def on_ti_rename_col(self, col):
        """改第 col 个测试列的名字。仅用户新增的列可改；自动生成的 T0/T1 拒绝。"""
        if not self._ti_sig or col < 0 or col >= len(self._ti_rows):
            return
        rd = self._ti_rows[col]
        if not rd.get("user_added"):
            QtWidgets.QMessageBox.information(
                self, "不可改名", "T%d 是从表达式自动生成的测试，名字不可改。\n只有你自己新增的测试列(加正向列/复制列/加负向)可以改名。" % col)
            return
        cur = self._ti_label(rd, col)
        text, ok = QtWidgets.QInputDialog.getText(self, "重命名测试列",
                                                  "新名字(字母/数字/下划线)：", text=cur)
        if not ok:
            return
        ok2, msg = self._ti_set_test_name(col, text)
        if not ok2:
            QtWidgets.QMessageBox.warning(self, "改名失败", msg)

    def _ti_set_test_name(self, col, new_name):
        """设置测试列自定义名。返回 (成功, 信息/最终名)。供 UI 与测试调用。"""
        rd = self._ti_rows[col]
        if not rd.get("user_added"):
            return False, "自动生成的测试不可改名"
        nm = self._sanitize_name(new_name)
        if not nm:
            return False, "名字为空或全是非法字符"
        import re
        if re.match(r"(?i)^t\d+(_neg)?$", nm):     # T<编号> 是自动测试保留命名，禁止手填(防后续位移撞名)
            return False, "名字 %s 与自动测试命名(T<编号>)冲突，请换个名字" % nm
        # 计算该名的最终标号(负向会带 _NEG)，与其它列的最终标号查重
        final = nm + ("_NEG" if rd.get("is_negative") and not nm.upper().endswith("NEG") else "")
        for j, other in enumerate(self._ti_rows):
            if j != col and self._ti_label(other, j) == final:
                return False, "名字与列 %s 重复(会造成 .sv 标号冲突)" % self._ti_label(other, j)
        rd["name"] = nm
        self._ti_mark_customized()
        self._ti_populate()
        self.status.showMessage("测试列已改名为 %s" % final)
        return True, final

    def on_ti_copy(self):
        if not self._ti_sig:
            return
        c = self.ti_table.currentColumn()    # 复制当前选中列(测试)
        if c < 0 or c >= len(self._ti_rows):
            QtWidgets.QMessageBox.information(self, "提示", "请先选中要复制的测试列")
            return
        src = self._ti_rows[c]
        # 复制列：用户新建列(可改名)，但不复制 name(避免与原列重名→.sv 标号冲突)
        rd = {"base_values": dict(src["base_values"]),
              "kind": src.get("kind", "pos"), "note": src.get("note", ""), "user_added": True}
        if src.get("wrong_value") is not None:
            rd["wrong_value"] = src["wrong_value"]
        self._ti_recompute(rd)
        self._ti_rows.insert(c + 1, rd)
        self._ti_mark_customized()
        self._ti_populate()
        self.ti_table.setCurrentCell(0, c + 1)

    def on_ti_del(self):
        if not self._ti_sig:
            return
        cols = sorted({i.column() for i in self.ti_table.selectedItems()}, reverse=True)
        if not cols:
            c = self.ti_table.currentColumn()
            cols = [c] if c >= 0 else []
        if not cols:
            QtWidgets.QMessageBox.information(self, "提示", "请先选中要删除的测试列")
            return
        for c in cols:
            if 0 <= c < len(self._ti_rows):
                del self._ti_rows[c]
        self._ti_mark_customized()
        self._ti_populate()

    def on_ti_regen(self):
        if not self._ti_sig:
            return
        self._customized.discard(self._ti_name_low)
        self._neg_only.discard(self._ti_name_low)
        self._edited.pop(self._ti_name_low, None)
        self._load_test_items(self._ti_sig)
        self.status.showMessage("已从表达式重新生成 %s 的测试项（丢弃自定义）" % self._ti_sig.out_name
                                if self._ti_sig else "")

    def on_ti_preview_signal(self):
        if not self._ti_sig:
            QtWidgets.QMessageBox.information(self, "提示", "请先在左侧选择一个信号")
            return
        sig = self._ti_sig
        vecs = self._rows_to_vectors(self._ti_node, self._ti_bindings, self._ti_groups,
                                     sig.out_width, self._ti_rows)
        lines, _stats = W.render_signal_block(sig, self._ti_bindings, vecs,
                                              {"truncated": False}, comments=self.comments.isChecked())
        self.preview.setPlainText("\n".join(lines))
        self.tabs.setCurrentWidget(self.preview_tab)
        self.status.showMessage("已预览信号 %s 的 .sv 片段（%d 用例）" % (sig.out_name, len(vecs)))

    def on_ti_export_csv(self):
        if not self._ti_sig:
            QtWidgets.QMessageBox.information(self, "提示", "请先在左侧选择一个信号")
            return
        sig = self._ti_sig
        default = "%s_tests.csv" % (sig.out_base or "signal")
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "导出本信号测试项 CSV", default,
                                                        "CSV (*.csv)")
        if not path:
            return
        import csv
        rows = self._ti_rows
        # 纵向(真值表)导出：第一列=信号/字段名，其后每列一条测试 T0/T1...
        try:
            with open(path, "w", encoding="utf-8-sig", newline="") as f:
                wr = csv.writer(f)
                wr.writerow(["信号\\测试"] + [self._ti_label(rd, i) for i, rd in enumerate(rows)])
                for g in self._ti_groups:
                    bk = g["base"].lower()
                    wr.writerow([g["label"]] + [self._fmt_val(rd["base_values"].get(bk, 0), g["width"])
                                                for rd in rows])
                out_w = self._ti_sig.out_width or 1
                exp_label = "期望(out)%s" % ("[%d:0]" % (out_w - 1) if out_w > 1 else "")
                wr.writerow([exp_label] + [self._fmt_val(rd["expected"] & E.mask(rd.get("correct_width") or 1),
                                                         rd.get("correct_width") or 1) for rd in rows])
                wr.writerow(["期望(bin)"] + [W.fmt_bin(rd["expected"], rd.get("correct_width") or 1)
                                            for rd in rows])
                wr.writerow(["负向?"] + ["是" if rd["is_negative"] else "" for rd in rows])
                drives = [self._drive_strs(rd) for rd in rows]
                wr.writerow(["force"] + [fs for fs, _ in drives])
                wr.writerow(["RF_WRITE"] + [ws for _, ws in drives])
        except OSError as ex:
            QtWidgets.QMessageBox.critical(self, "导出失败", str(ex))
            return
        QtWidgets.QMessageBox.information(self, "完成", "已导出 %d 条测试项：\n%s"
                                          % (len(self._ti_rows), path))
        self.status.showMessage("已导出测试项 CSV：%s" % path)

    def _rows_to_vectors(self, node, bindings, groups, out_width, rows):
        """把 rowdict 列表构造成 TestVector 列表（负向行用 expected 作 override 编码；带自定义名）。"""
        vecs = []
        for i, rd in enumerate(rows):
            exp_override = rd["expected"] if rd.get("is_negative") else None
            vecs.append(V.make_vector_from_base_values(
                node, bindings, groups, rd["base_values"], out_width,
                index=i, expected_override=exp_override, name=rd.get("name")))
        return vecs

    def _vector_overrides(self, positive_only=False):
        """汇总所有被用户改过的信号的测试项 → {out_name(小写): [TestVector]} 喂给 build()。
        positive_only=True 时剔除负向向量(供"负向单独出文件"的正向文件，避免负向断言泄入)。
        删光所有行的信号会得到空列表(=该信号零用例)，而非回退自动生成——尊重用户清空意图。
        """
        if not self._customized or self._resolver is None:
            return None
        ov = {}
        for name_low in list(self._customized):
            ed = self._edited.get(name_low)
            if not ed:
                continue
            sig, rows = ed["sig"], ed["rows"]
            try:
                node = E.parse(sig.expr)
            except E.ExprError:
                continue
            bindings = self._resolver.resolve_signal_inputs(sig)
            groups = V.input_groups(node, bindings)
            vecs = self._rows_to_vectors(node, bindings, groups, sig.out_width, rows)
            if positive_only:
                vecs = [v for v in vecs if not v.is_negative]
            ov[name_low] = vecs          # 空列表也保留：删空=零用例，不回退自动
        return ov or None

    def _opts(self, signals, neg_signals=None, positive_only=False):
        # 注意：GUI 的负向统一走 vector_overrides(左侧"负向"列与右侧编辑器是同一套)，
        # 故这里默认不传 neg_signals，避免与 override 里的负向重复追加。
        return generator.GenOptions(
            signals=signals or None, neg_signals=neg_signals or None,
            mode=self.mode_combo.currentText(), max_tests=self.max_tests.value(),
            exhaustive=self.exhaustive.isChecked(), comments=self.comments.isChecked(),
            neg_mode=self.neg_mode.currentText(), neg_which=self.neg_which.currentText(),
            top_output_only=False,   # GUI 已按表勾选，不再二次过滤
            vector_overrides=self._vector_overrides(positive_only=positive_only))

    # ───────────── 收集 / 选项 ─────────────
    def _collect(self):
        """返回勾选(COL_SEL)的信号名列表。负向不再单独收集——已在 vector_overrides 里。"""
        sel = []
        for r in range(self.table.rowCount()):
            if self.table.item(r, COL_SEL).checkState() == QtCore.Qt.Checked:
                sel.append(self._sig_of_row(r).out_name)
        return sel

    def _has_negatives(self):
        """选中信号里是否有任何负向用例(用于'负向单独出文件'的判定)。"""
        ov = self._vector_overrides()
        return bool(ov) and any(any(v.is_negative for v in vs) for vs in ov.values() if vs)

    # ───────────── 诊断 / 预览 / 生成 ─────────────
    def on_diagnose(self):
        if not self.wb:
            return
        d = generator.diagnose(self.wb, generator.GenOptions(top_output_only=False))
        c = d["cats"]
        lines = ["覆盖诊断（全部信号）:",
                 "  RF_WRITE(RW): %d   force-RO: %d   force-级联: %d   force-wire兜底: %d   UNKNOWN: %d"
                 % (c["rfwrite"], c["force_ro"], c["force_chained"], c["force_wire"], c["unknown"]),
                 "", "tmm 类型分布: %s" % d["tmm_type_raw"]]
        if d["fallback_wires"]:
            lines.append("\n⚠ wire 兜底(表中查无，按名 force——elaboration 最易 CUVUNF):")
            for name, ltr, base, w in d["fallback_wires"][:60]:
                lines.append("   %s.%s=%s" % (name, ltr, base))
        if d["unknown"]:
            lines.append("\n✗ UNKNOWN(未解析):")
            for name, ltr, base, note in d["unknown"][:60]:
                lines.append("   %s.%s=%s" % (name, ltr, base))
        self.preview.setPlainText("\n".join(lines))
        self.tabs.setCurrentWidget(self.preview_tab)
        self.status.showMessage("诊断完成：wire兜底 %d，UNKNOWN %d（这些最可能让 elaboration 失败）"
                                % (len(d["fallback_wires"]), len(d["unknown"])))

    def on_preview(self):
        if not self.wb:
            return
        sel = self._collect()
        if not sel:
            QtWidgets.QMessageBox.information(self, "提示", "请先勾选至少一个信号")
            return
        res = generator.build(self.wb, self._opts(sel))
        text = generator.render(res, comments=self.comments.isChecked())
        lines = text.splitlines()
        self.preview.setPlainText("\n".join(lines[:600])
                                  + ("\n... (预览截断，共 %d 行)" % len(lines) if len(lines) > 600 else ""))
        self.tabs.setCurrentWidget(self.preview_tab)
        s = res["summary"]
        msg = "预览: 生成 %d，向量 %d（负向 %d）" % (s["n_generated"], s["n_vectors"], s["n_negative"])
        if self._customized:
            msg += "；含 %d 个已自定义信号" % len(self._customized)
        if s.get("n_skipped"):
            msg += "；↷ 跳过 %d 个(含不可驱动输入，会 elaboration 失败)" % s["n_skipped"]
        self.status.showMessage(msg)
        if s.get("n_skipped") and res.get("skipped"):
            tip = "\n\n// ↷ 跳过 %d 个含不可驱动输入(wire兜底/未解析)的信号:" % s["n_skipped"]
            for name, aid, risky in res["skipped"][:30]:
                tip += "\n//   %s ← %s" % (name, ", ".join("%s=%s" % (l, b) for l, b, _ in risky))
            self.preview.appendPlainText(tip)

    def on_generate(self):
        if not self.wb:
            return
        sel = self._collect()
        if not sel:
            QtWidgets.QMessageBox.information(self, "提示", "请先勾选至少一个信号")
            return
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "保存 wr_rf_tc.sv", "wr_rf_tc.sv",
                                                        "SystemVerilog (*.sv)")
        if not path:
            return
        cm = self.comments.isChecked()
        if self.neg_separate.isChecked() and self._has_negatives():
            # 正向文件：剔除自定义测试项里的负向向量，避免故意填错的断言泄入主文件
            pos = generator.build(self.wb, self._opts(sel, positive_only=True))
            self._write(path, generator.render(pos, comments=cm))
            npath = os.path.splitext(path)[0] + "_neg.sv"
            negres = generator.build(self.wb, self._opts(sel))
            negblocks = [(l, st) for (l, st) in negres["blocks"] if st["n_negative"] > 0]
            self._write(npath, generator.render({"blocks": negblocks, "selected": [],
                                                 "errors": [], "summary": negres["summary"]}, comments=cm))
            extra = "；负向→%s" % os.path.basename(npath)
            nsk = pos["summary"].get("n_skipped", 0)
        else:
            res = generator.build(self.wb, self._opts(sel))
            self._write(path, generator.render(res, comments=cm))
            extra = ""
            nsk = res["summary"].get("n_skipped", 0)
        skipmsg = ("\n\n↷ 跳过了 %d 个含不可驱动输入的信号(默认跳过以保证可 elaborate)；"
                   "如需强制生成用 CLI --include-risky。" % nsk) if nsk else ""
        custmsg = ("\n\n含 %d 个已自定义测试项的信号(编辑已写入产物)。" % len(self._customized)
                   if self._customized else "")
        ndup = (res if not (self.neg_separate.isChecked() and self._has_negatives()) else pos)["summary"].get("n_dup_labels", 0)
        dupmsg = ("\n\n⛔ 警告：有 %d 处重复 assert 标号(同一作用域重复=非法 SV，会 elaboration 失败)！"
                  "多因两信号共用同一 R(序号)，请核对。" % ndup) if ndup else ""
        QtWidgets.QMessageBox.information(self, "完成", "已写出：%s%s%s%s%s" % (path, extra, skipmsg, custmsg, dupmsg))
        self.status.showMessage("已生成：%s%s" % (path, extra))

    def on_report(self):
        """导出'给人看'的测试用例报告(HTML 三段：汇总+每信号真值表+完整明细；或 CSV)。
        勾选了信号则只报告这些，否则覆盖全部信号；自动带上测试项编辑/负向。"""
        if not self.wb:
            return
        from dreg_verify import cli            # 复用 CLI 的报告写出器(按扩展名出 HTML/CSV)
        sel = self._collect()
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "导出测试用例报告", "dreg_report.html", "HTML 网页 (*.html);;CSV 表格 (*.csv)")
        if not path:
            return
        try:
            rep = generator.report(self.wb, self._opts(sel or None))
            written = cli.write_report(path, rep, self.path_edit.text() or "excel")
        except Exception as ex:  # noqa: BLE001
            QtWidgets.QMessageBox.critical(self, "导出失败", str(ex))
            return
        scope = "勾选的 %d 个" % len(sel) if sel else "全部"
        n_tc = len(rep["detail"]); n_neg = sum(1 for r in rep["detail"] if r.get("neg") == "是")
        QtWidgets.QMessageBox.information(
            self, "完成", "已导出报告(%s信号，用例 %d 条，负向 %d 条)：\n%s"
            % (scope, n_tc, n_neg, "\n".join(written)))
        self.status.showMessage("已导出报告：%s" % "  ".join(written))

    def _write(self, path, text):
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)


def main():
    app = QtWidgets.QApplication(sys.argv)
    w = MainWindow()
    w.show()
    # 启动自动加载：命令行给了 .xlsx 就用它，否则用上次加载过的文件（都不需要再点"加载"）。
    preload = None
    cli_args = [a for a in sys.argv[1:] if not a.startswith("-")]
    if cli_args and os.path.isfile(cli_args[0]):
        preload = cli_args[0]
    else:
        last = _load_last_excel()
        if last and os.path.isfile(last):
            preload = last
    if preload:
        w.path_edit.setText(preload)
        w.on_load()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
