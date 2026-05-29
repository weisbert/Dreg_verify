# Dreg_verify

基于核心 Excel 自动生成 Dreg 逻辑信号验证文件（`wr_rf_tc.sv`）的工具，带 PyQt GUI 做筛选 / 预览 / 导出。

> 🚧 开发中。目前已完成 Excel 结构导出工具 `inspect_excel.py`；生成器与 GUI 待实现。

## 环境

- Python 3.13（Windows）
- 依赖见 `requirements.txt`

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## 工具

### `inspect_excel.py`

读取核心 Excel，把各 sheet 的列结构 / 表头 / 样本行 / 列取值枚举导出成文本，方便审阅与分析。

```powershell
# 默认导出（推荐加 --mask-owners 隐去人名）
python inspect_excel.py "核心文件.xlsx" --mask-owners --rows 40

# 只导关键页
python inspect_excel.py "核心文件.xlsx" --mask-owners --sheets logic,regmap,NamingRule,for_test

# 信号名保结构脱敏（保留下划线/位宽/表达式原文）
python inspect_excel.py "核心文件.xlsx" --anon-signals --mask-owners
```

输出写到 Excel 同目录的 `<名字>_inspect.txt`。

## 注意

- 真实 Excel（`*.xlsx`）、导出文本（`*_inspect.txt`）、生成的 `wr_rf_tc.sv` 都已在 `.gitignore` 中排除，**不会**被提交。
