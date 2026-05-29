# -*- coding: utf-8 -*-
"""
gui.py — Dreg_verify 的 PySide6 图形界面：加载 Excel → 信号表(owner/类型/名字筛选 + 多选 +
负向勾选) → 预览 .sv 片段 → 导出 wr_rf_tc.sv。后端复用 generator，与 CLI 同一套逻辑。

运行: python -m dreg_verify.gui
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from PySide6 import QtCore, QtWidgets
except Exception as ex:  # noqa: BLE001
    raise SystemExit("需要 PySide6：pip install PySide6（原始错误：%s）" % ex)

from dreg_verify import excel_model, generator  # noqa: E402


# 表列定义
COL_SEL, COL_NEG, COL_R, COL_K, COL_OWNER, COL_TYPE, COL_TOP, COL_EXPR = range(8)
HEADERS = ["选", "负向", "R", "输出名(K)", "owner", "type", "top", "表达式"]


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Dreg_verify — wr_rf_tc.sv 生成器")
        self.resize(1180, 720)
        self.wb = None
        self.signals = []          # 全部 LogicSignal
        self._build_ui()

    # ───────────── UI 构建 ─────────────
    def _build_ui(self):
        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        root = QtWidgets.QVBoxLayout(central)

        # 顶部：Excel 选择
        top = QtWidgets.QHBoxLayout()
        self.path_edit = QtWidgets.QLineEdit()
        self.path_edit.setPlaceholderText("选择 Dreg 核心 Excel (.xlsx) ...")
        browse = QtWidgets.QPushButton("浏览…")
        browse.clicked.connect(self.on_browse)
        load = QtWidgets.QPushButton("加载")
        load.clicked.connect(self.on_load)
        top.addWidget(QtWidgets.QLabel("Excel:"))
        top.addWidget(self.path_edit, 1)
        top.addWidget(browse)
        top.addWidget(load)
        root.addLayout(top)

        # 筛选条
        flt = QtWidgets.QHBoxLayout()
        self.owner_combo = QtWidgets.QComboBox()
        self.owner_combo.addItem("全部 owner")
        self.owner_combo.currentIndexChanged.connect(self.apply_filter)
        self.type_combo = QtWidgets.QComboBox()
        self.type_combo.addItem("全部 type")
        self.type_combo.currentIndexChanged.connect(self.apply_filter)
        self.name_edit = QtWidgets.QLineEdit()
        self.name_edit.setPlaceholderText("名字/正则搜索…")
        self.name_edit.textChanged.connect(self.apply_filter)
        self.top_only = QtWidgets.QCheckBox("仅 top_output=1")
        self.top_only.stateChanged.connect(self.apply_filter)
        flt.addWidget(QtWidgets.QLabel("筛选:"))
        flt.addWidget(self.owner_combo)
        flt.addWidget(self.type_combo)
        flt.addWidget(self.name_edit, 1)
        flt.addWidget(self.top_only)
        sel_all = QtWidgets.QPushButton("全选(可见)")
        sel_all.clicked.connect(lambda: self.set_all_visible(True))
        sel_none = QtWidgets.QPushButton("清空")
        sel_none.clicked.connect(lambda: self.set_all_visible(False))
        flt.addWidget(sel_all)
        flt.addWidget(sel_none)
        root.addLayout(flt)

        # 主体：左表 + 右预览
        splitter = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        self.table = QtWidgets.QTableWidget(0, len(HEADERS))
        self.table.setHorizontalHeaderLabels(HEADERS)
        self.table.setSortingEnabled(True)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        splitter.addWidget(self.table)

        self.preview = QtWidgets.QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setLineWrapMode(QtWidgets.QPlainTextEdit.NoWrap)
        font = self.preview.font()
        font.setFamily("Consolas")
        self.preview.setFont(font)
        splitter.addWidget(self.preview)
        splitter.setSizes([620, 540])
        root.addWidget(splitter, 1)

        # 选项条
        opt = QtWidgets.QHBoxLayout()
        self.mode_combo = QtWidgets.QComboBox()
        self.mode_combo.addItems(["min", "max"])
        self.exhaustive = QtWidgets.QCheckBox("小信号全穷举")
        self.max_tests = QtWidgets.QSpinBox()
        self.max_tests.setRange(1, 100000)
        self.max_tests.setValue(256)
        self.neg_mode = QtWidgets.QComboBox()
        self.neg_mode.addItems(["invert", "inc", "value"])
        self.neg_which = QtWidgets.QComboBox()
        self.neg_which.addItems(["first", "all"])
        self.neg_separate = QtWidgets.QCheckBox("负向单独出文件")
        opt.addWidget(QtWidgets.QLabel("向量:"))
        opt.addWidget(self.mode_combo)
        opt.addWidget(self.exhaustive)
        opt.addWidget(QtWidgets.QLabel("上限"))
        opt.addWidget(self.max_tests)
        opt.addSpacing(20)
        opt.addWidget(QtWidgets.QLabel("负向:"))
        opt.addWidget(self.neg_mode)
        opt.addWidget(self.neg_which)
        opt.addWidget(self.neg_separate)
        opt.addStretch(1)
        root.addLayout(opt)

        # 按钮条
        btns = QtWidgets.QHBoxLayout()
        prev_btn = QtWidgets.QPushButton("预览选中")
        prev_btn.clicked.connect(self.on_preview)
        gen_btn = QtWidgets.QPushButton("生成 .sv …")
        gen_btn.clicked.connect(self.on_generate)
        btns.addStretch(1)
        btns.addWidget(prev_btn)
        btns.addWidget(gen_btn)
        root.addLayout(btns)

        self.status = self.statusBar()
        self.status.showMessage("请选择并加载 Excel。")

    # ───────────── 数据加载 ─────────────
    def on_browse(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "选择 Excel", "", "Excel (*.xlsx)")
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
        self._populate_filters()
        self._populate_table()
        self.status.showMessage(
            "已加载 %d 个信号；tmm字段=%d，regmap信号=%d，sheets=%s"
            % (len(self.signals), len(self.wb.tmm), len(self.wb.regmap), self.wb.sheet_names))

    def _populate_filters(self):
        owners = sorted({s.owner for s in self.signals if s.owner})
        types = sorted({s.suffix for s in self.signals if s.suffix})
        self.owner_combo.blockSignals(True)
        self.type_combo.blockSignals(True)
        self.owner_combo.clear(); self.owner_combo.addItem("全部 owner"); self.owner_combo.addItems(owners)
        self.type_combo.clear(); self.type_combo.addItem("全部 type"); self.type_combo.addItems(types)
        self.owner_combo.blockSignals(False)
        self.type_combo.blockSignals(False)

    def _populate_table(self):
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(self.signals))
        for r, sig in enumerate(self.signals):
            self._set_check(r, COL_SEL, False)
            self._set_check(r, COL_NEG, False)
            self._set_text(r, COL_R, str(sig.assert_id))
            self._set_text(r, COL_K, sig.out_name)
            self._set_text(r, COL_OWNER, sig.owner)
            self._set_text(r, COL_TYPE, sig.suffix)
            self._set_text(r, COL_TOP, str(sig.top_output))
            self._set_text(r, COL_EXPR, sig.expr)
            self.table.item(r, COL_R).setData(QtCore.Qt.UserRole, r)  # 记录原索引
        self.table.setSortingEnabled(True)
        self.table.resizeColumnsToContents()
        self.apply_filter()

    def _set_text(self, r, c, text):
        it = QtWidgets.QTableWidgetItem(text or "")
        self.table.setItem(r, c, it)

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
            show = True
            if owner != "全部 owner" and sig.owner != owner:
                show = False
            if typ != "全部 type" and sig.suffix != typ:
                show = False
            if rx and not (rx.search(sig.out_name) or rx.search(sig.expr)):
                show = False
            elif pat and not rx and pat.lower() not in (sig.out_name + sig.expr).lower():
                show = False
            if top_only and str(sig.top_output).strip() not in ("1", "1.0", "True", "true"):
                show = False
            self.table.setRowHidden(r, not show)
            if show:
                visible += 1
        self.status.showMessage("可见信号 %d / 共 %d" % (visible, len(self.signals)))

    def _sig_of_row(self, r):
        idx = self.table.item(r, COL_R).data(QtCore.Qt.UserRole)
        return self.signals[idx]

    def set_all_visible(self, checked):
        for r in range(self.table.rowCount()):
            if not self.table.isRowHidden(r):
                self.table.item(r, COL_SEL).setCheckState(
                    QtCore.Qt.Checked if checked else QtCore.Qt.Unchecked)

    # ───────────── 收集选择 ─────────────
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
            signals=signals or None,
            neg_signals=neg_signals or None,
            mode=self.mode_combo.currentText(),
            max_tests=self.max_tests.value(),
            exhaustive=self.exhaustive.isChecked(),
            neg_mode=self.neg_mode.currentText(),
            neg_which=self.neg_which.currentText(),
        )

    # ───────────── 预览 / 生成 ─────────────
    def on_preview(self):
        if not self.wb:
            return
        sel, neg = self._collect()
        if not sel:
            QtWidgets.QMessageBox.information(self, "提示", "请先勾选至少一个信号")
            return
        res = generator.build(self.wb, self._opts(sel, neg))
        text = generator.render(res)
        lines = text.splitlines()
        shown = "\n".join(lines[:400])
        if len(lines) > 400:
            shown += "\n... (预览截断，共 %d 行；生成时写完整文件)" % len(lines)
        self.preview.setPlainText(shown)
        s = res["summary"]
        msg = "预览: 选中 %d，向量 %d（负向 %d）" % (s["n_generated"], s["n_vectors"], s["n_negative"])
        if s["n_unresolved_signals"]:
            msg += "；⚠ %d 个信号含未解析输入" % s["n_unresolved_signals"]
        self.status.showMessage(msg)

    def on_generate(self):
        if not self.wb:
            return
        sel, neg = self._collect()
        if not sel:
            QtWidgets.QMessageBox.information(self, "提示", "请先勾选至少一个信号")
            return
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "保存 wr_rf_tc.sv", "wr_rf_tc.sv", "SystemVerilog (*.sv)")
        if not path:
            return
        if self.neg_separate.isChecked() and neg:
            pos = generator.build(self.wb, self._opts(sel, None))
            self._write(path, generator.render(pos))
            npath = os.path.splitext(path)[0] + "_neg.sv"
            negres = generator.build(self.wb, self._opts(sel, neg))
            negblocks = [(l, st) for (l, st) in negres["blocks"] if st["n_negative"] > 0]
            self._write(npath, generator.render({"blocks": negblocks, "selected": [],
                                                 "errors": [], "summary": negres["summary"]}))
            extra = "；负向→%s" % os.path.basename(npath)
        else:
            res = generator.build(self.wb, self._opts(sel, neg))
            self._write(path, generator.render(res))
            extra = ""
        QtWidgets.QMessageBox.information(self, "完成", "已写出：%s%s" % (path, extra))
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
