# -*- coding: utf-8 -*-
"""
gui.py — Dreg_verify 的 PySide6 图形界面（含 debug 辅助）：
  加载 Excel → 信号表(owner/类型/名字/状态筛选 + 多选 + 负向勾选)
  → 点信号看它 force/RF_WRITE 哪些 net 的明细(查 elaboration 找不到 net 的问题)
  → 预览 .sv → 覆盖诊断 → 导出 wr_rf_tc.sv。后端复用 generator，与 CLI 同一套逻辑。

运行: python -m dreg_verify.gui
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from PySide6 import QtCore, QtGui, QtWidgets
except Exception as ex:  # noqa: BLE001
    raise SystemExit("需要 PySide6：pip install PySide6（原始错误：%s）" % ex)

from dreg_verify import excel_model, generator  # noqa: E402
from dreg_verify import resolver as R            # noqa: E402

COL_SEL, COL_NEG, COL_R, COL_K, COL_OWNER, COL_TYPE, COL_TOP, COL_STATUS, COL_EXPR = range(9)
HEADERS = ["选", "负向", "R", "输出名(K)", "owner", "type", "top", "状态", "表达式"]
STATUS_LABEL = {"clean": "clean", "wire-fallback": "⚠wire兜底",
                "unresolved": "✗未解析", "parse-err": "✗解析错"}


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Dreg_verify — wr_rf_tc.sv 生成器 + debug")
        self.resize(1280, 760)
        self.wb = None
        self.signals = []
        self._analysis = {}     # row index -> generator.analyze_signal 结果
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
        sel_all = QtWidgets.QPushButton("全选(可见)"); sel_all.clicked.connect(lambda: self.set_all_visible(True))
        sel_none = QtWidgets.QPushButton("清空"); sel_none.clicked.connect(lambda: self.set_all_visible(False))
        flt.addWidget(sel_all); flt.addWidget(sel_none)
        root.addLayout(flt)

        splitter = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        # 左：表 + 明细
        left = QtWidgets.QSplitter(QtCore.Qt.Vertical)
        self.table = QtWidgets.QTableWidget(0, len(HEADERS))
        self.table.setHorizontalHeaderLabels(HEADERS)
        self.table.setSortingEnabled(True)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.table.currentCellChanged.connect(self.on_row_focus)
        left.addWidget(self.table)
        self.detail = QtWidgets.QPlainTextEdit(); self.detail.setReadOnly(True)
        self.detail.setMaximumHeight(200)
        self._mono(self.detail)
        left.addWidget(self.detail)
        left.setSizes([520, 180])
        splitter.addWidget(left)

        self.preview = QtWidgets.QPlainTextEdit(); self.preview.setReadOnly(True)
        self.preview.setLineWrapMode(QtWidgets.QPlainTextEdit.NoWrap)
        self._mono(self.preview)
        splitter.addWidget(self.preview)
        splitter.setSizes([680, 580])
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
        gen = QtWidgets.QPushButton("生成 .sv …"); gen.clicked.connect(self.on_generate)
        btns.addWidget(diag); btns.addStretch(1); btns.addWidget(prev); btns.addWidget(gen)
        root.addLayout(btns)

        self.status = self.statusBar()
        self.status.showMessage("请选择并加载 Excel。")

    def _mono(self, w):
        f = w.font(); f.setFamily("Consolas"); w.setFont(f)

    # ───────────── 加载 + 分析 ─────────────
    def on_browse(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "选择 Excel", "", "Excel (*.xlsx)")
        if path:
            self.path_edit.setText(path)

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
        res = R.Resolver(self.wb)
        self._analysis = {}
        errs = []
        for i, s in enumerate(self.signals):
            try:
                self._analysis[i] = generator.analyze_signal(res, s)
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
        self.status.showMessage(msg)

    def _populate_filters(self):
        owners = sorted({s.owner for s in self.signals if s.owner})
        types = sorted({s.suffix for s in self.signals if s.suffix})
        for combo, items, head in ((self.owner_combo, owners, "全部 owner"),
                                   (self.type_combo, types, "全部 type")):
            combo.blockSignals(True); combo.clear(); combo.addItem(head); combo.addItems(items)
            combo.blockSignals(False)

    def _populate_table(self):
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

    # ───────────── 点信号看明细（debug 关键） ─────────────
    def on_row_focus(self, row, col, prow, pcol):
        if row < 0 or not self.signals:
            return
        a = self._analysis.get(self._idx_of_row(row))
        sig = self._sig_of_row(row)
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

    # ───────────── 收集 / 选项 ─────────────
    def _collect(self):
        sel, neg = [], []
        for r in range(self.table.rowCount()):
            if self.table.item(r, COL_SEL).checkState() == QtCore.Qt.Checked:
                sig = self._sig_of_row(r)
                sel.append(sig.out_name)
                if self.table.item(r, COL_NEG).checkState() == QtCore.Qt.Checked:
                    neg.append(sig.out_name)
        return sel, neg

    def _opts(self, signals, neg_signals):
        return generator.GenOptions(
            signals=signals or None, neg_signals=neg_signals or None,
            mode=self.mode_combo.currentText(), max_tests=self.max_tests.value(),
            exhaustive=self.exhaustive.isChecked(), comments=self.comments.isChecked(),
            neg_mode=self.neg_mode.currentText(), neg_which=self.neg_which.currentText(),
            top_output_only=False)   # GUI 已按表勾选，不再二次过滤

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
        self.status.showMessage("诊断完成：wire兜底 %d，UNKNOWN %d（这些最可能让 elaboration 失败）"
                                % (len(d["fallback_wires"]), len(d["unknown"])))

    def on_preview(self):
        if not self.wb:
            return
        sel, neg = self._collect()
        if not sel:
            QtWidgets.QMessageBox.information(self, "提示", "请先勾选至少一个信号")
            return
        res = generator.build(self.wb, self._opts(sel, neg))
        text = generator.render(res, comments=self.comments.isChecked())
        lines = text.splitlines()
        self.preview.setPlainText("\n".join(lines[:600])
                                  + ("\n... (预览截断，共 %d 行)" % len(lines) if len(lines) > 600 else ""))
        s = res["summary"]
        msg = "预览: 生成 %d，向量 %d（负向 %d）" % (s["n_generated"], s["n_vectors"], s["n_negative"])
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
        sel, neg = self._collect()
        if not sel:
            QtWidgets.QMessageBox.information(self, "提示", "请先勾选至少一个信号")
            return
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "保存 wr_rf_tc.sv", "wr_rf_tc.sv",
                                                        "SystemVerilog (*.sv)")
        if not path:
            return
        cm = self.comments.isChecked()
        if self.neg_separate.isChecked() and neg:
            pos = generator.build(self.wb, self._opts(sel, None))
            self._write(path, generator.render(pos, comments=cm))
            npath = os.path.splitext(path)[0] + "_neg.sv"
            negres = generator.build(self.wb, self._opts(sel, neg))
            negblocks = [(l, st) for (l, st) in negres["blocks"] if st["n_negative"] > 0]
            self._write(npath, generator.render({"blocks": negblocks, "selected": [],
                                                 "errors": [], "summary": negres["summary"]}, comments=cm))
            extra = "；负向→%s" % os.path.basename(npath)
            nsk = pos["summary"].get("n_skipped", 0)
        else:
            res = generator.build(self.wb, self._opts(sel, neg))
            self._write(path, generator.render(res, comments=cm))
            extra = ""
            nsk = res["summary"].get("n_skipped", 0)
        skipmsg = ("\n\n↷ 跳过了 %d 个含不可驱动输入的信号(默认跳过以保证可 elaborate)；"
                   "如需强制生成用 CLI --include-risky。" % nsk) if nsk else ""
        QtWidgets.QMessageBox.information(self, "完成", "已写出：%s%s%s" % (path, extra, skipmsg))
        self.status.showMessage("已生成：%s%s" % (path, extra))

    def _write(self, path, text):
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)


def main():
    app = QtWidgets.QApplication(sys.argv)
    w = MainWindow()
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
