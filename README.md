# Dreg_verify

从 Dreg 核心 Excel 的 `logic` 页真值表达式**重新推导**、自动生成 Dreg 逻辑信号验证文件
`wr_rf_tc.sv`（UVM / SystemVerilog）的工具。自带 Excel 结构导出与表达式形态覆盖自检。

> MVP 已可用：CLI 生成器 + 表达式求值器 + 端到端测试。GUI（PySide6 筛选/预览/导出）为后续。

## 工作原理

不依赖旧 VB 生成的 `for_test` 页，而是：

1. **读 Excel**（`logic` / `regmap` / `total_memory_map` 三页）
2. **解析命名与地址**：输入名去 `_to_logic` → 在 `total_memory_map` 按字段名查地址(F)、位段(B)、
   RO/RW 类型；判 force（RO 管脚/只读）还是 `RF_WRITE`（RW 寄存器）
3. **求值表达式**：严格按 Verilog 两遍位宽语义实现三元 `?:` / 拼接 `{}` / 重复 `{n{}}` /
   按位 `~&|^` / 归约 / 比较 / 移位 / 位常量
4. **生成测试向量**：控制位（三元条件 + 门控位）全组合 × 数据总线确定性特征值；
   期望输出 = 在每个向量上对表达式求值
5. **渲染 .sv**：每信号一块，每 test 先 `force` / `RF_WRITE`（同地址 RW 字段合并成一条），
   `#1ps` 后 `assert_<R>_T<n>: assert(...)` 配 `uvm_report_info/error`

## 环境

- Python 3.13（Windows）；依赖见 `requirements.txt`

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## 生成 wr_rf_tc.sv（核心）

```powershell
# 生成全部信号
python -m dreg_verify.cli --excel 核心文件.xlsx --out wr_rf_tc.sv

# 按 owner 筛选
python -m dreg_verify.cli --excel 核心文件.xlsx --owner Alice --out wr_rf_tc.sv

# 给某些信号加负向(异常)用例：故意填错期望值，自检 checker 能否抓错
python -m dreg_verify.cli --excel 核心文件.xlsx \
    --neg-signals d_logic_bt_lp_reserve --neg-mode invert --neg-file separate

# 导出"给人看"的测试用例表格（Excel 打开 .csv / 浏览器打开 .html）
python -m dreg_verify.cli --excel 核心文件.xlsx --owner "Yao Wang" --report 用例表.csv
python -m dreg_verify.cli --excel 核心文件.xlsx --neg-all --out wr_rf_tc.sv --report 用例表.html

# 列出可生成信号清单（不生成）
python -m dreg_verify.cli --excel 核心文件.xlsx --list

# 覆盖诊断：实测各输入被解析成 force(RO)/RF_WRITE(RW)/未知，类型列有哪些写法，有无 >16bit 输入
python -m dreg_verify.cli --excel 核心文件.xlsx --diagnose
```

> **owner 名字含空格**（如 `Wei Yu`）：CLI 加引号 `--owner "Wei Yu"`（多个用逗号 `"Wei Yu,Alice"`）；
> 匹配大小写无关且会折叠多余空格。GUI 里直接从 owner 下拉框选即可。

常用参数：

| 参数 | 说明 |
|------|------|
| `--owner A,B` | 按 owner 筛选（logic P 列） |
| `--signals N1,N2` | 按信号名筛选（K 全名或去位宽基名） |
| `--regex RE` / `--type to_mux,ls` / `--top-output-only` | 其它筛选 |
| `--exclude N1,N2` / `--exclude-regex RE` | 排除信号（如 `--exclude-regex "pll_n|_to_dsm"` 排掉探不到的 datapath 中间信号） |
| `--mode min\|max` | 向量密度：min=控制全组合×1 数据特征；max=多数据模式 |
| `--exhaustive` | 总输入位很少时做真·全穷举 |
| `--neg-signals` / `--neg-all` | 选哪些信号加负向用例 |
| `--neg-mode invert\|inc\|value` `--neg-value 0xN` | 造错方式 |
| `--neg-which first\|all` | 每信号造 1 个还是每向量都造 |
| `--neg-file inline\|separate` | 负向放同文件还是单独 `*_neg.sv` |
| `--force-signals` / `--rfwrite-signals` | 手动指定 RO/RW（修正名称/类型判定） |
| `--no-wire-fallback` | 关闭 wire 兜底：非 RW 寄存器且查不到的输入不再默认 force，而是标 UNKNOWN |
| `--diagnose` | 覆盖诊断：类型列原文分布 + 输入归类(RF_WRITE/force-RO/force-级联/force-wire/UNKNOWN)覆盖率 |
| `--report 路径.csv\|.html` | 导出给人看的测试用例表格（每信号汇总 + 每条用例明细：驱动值/期望/负向）。可与 `--out` 并用 |

**输入驱动模型**（沿用旧 for_test 规则）：输入是 **RW 寄存器**（在 total_memory_map/regmap 有地址）→ `` `RF_WRITE ``；
其余都是 **wire** → `force`（按信号名），包括 RO 管脚、**级联中间信号**（某输入其实是另一个 logic 的输出，
位宽自动取该输出的真实位宽）、以及表中查不到的信号（wire 兜底）。`--diagnose` 会把这几类分开列出，
其中"wire 兜底"是你需要重点确认"是否真是 wire、还是命名没匹配上的寄存器"的部分。

## 图形界面（PySide6）

```powershell
python -m dreg_verify.gui
```

加载 Excel → 信号表（按 owner / type / 名字 筛选，多选 + 全选/清空）→ 勾"负向"列给信号加异常用例
→ "预览选中"看 .sv 片段 → "生成 .sv …"导出。后端与 CLI 同一套逻辑。

## 表达式形态覆盖自检（强烈建议先跑）

由于生成器完全从 `logic.L` 表达式重推，先确认求值器能解析真表里**所有**表达式形态：

```powershell
python inspect_excel.py 核心文件.xlsx --expr-forms
```

会导出 `<名字>_exprforms.txt`：枚举所有不同表达式、按结构形态归并、并用求值器逐条试解析
（`[OK]` / `[解析失败]`）。若有 `[解析失败]`，把那几条发给维护者扩展 `dreg_verify/expr.py` 即可。

## Excel 结构导出（`inspect_excel.py`）

把各 sheet 的列结构 / 表头 / 样本 / 取值枚举导出成文本，便于审阅。

```powershell
python inspect_excel.py 核心文件.xlsx --compact --mask-owners --rows 10 `
    --sheets logic,regmap,total_memory_map
```

## 测试

```powershell
python -m pytest -q
```

涵盖：表达式求值器（含 Verilog 位宽陷阱）、端到端（合成 Excel → 校验 RF_WRITE 合并值等）。

## 目录

```
dreg_verify/        生成器后端（CLI 与未来 GUI 共用）
  expr.py           表达式词法/解析/两遍位宽求值/变量角色分类
  excel_model.py    读 logic/regmap/total_memory_map
  resolver.py       命名→RO/RW + 地址 + 位段（多策略匹配，失败清晰标注）
  vectors.py        测试向量生成 + 负向用例
  sv_writer.py      渲染 .sv（含同地址 RW 合并、负向标记）
  generator.py      编排 + 筛选
  cli.py            命令行入口
  gui.py            PySide6 图形界面（筛选/多选/预览/导出）
inspect_excel.py    Excel 结构导出 + 表达式形态覆盖报告(--expr-forms)
tests/              单元 + 端到端测试（含合成 Excel 夹具）
```

## 注意

- 真实 Excel（`*.xlsx`）、导出文本、生成的 `wr_rf_tc.sv` 均已在 `.gitignore` 中排除，**不会**提交。
- `.sv` 顶部的 `ENV_RF` / `RF_WRITE` / `uvm_*` 为脚手架宏；消息模板集中在 `sv_writer.py` 常量，可按需调整。
