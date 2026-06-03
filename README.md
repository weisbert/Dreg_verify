# Dreg_verify

从 Dreg 核心 Excel 的 `logic` 页（真值表达式）与 `mux` 页（N 选 1 选择器）**重新推导**、
自动生成 Dreg 信号验证文件 `wr_rf_tc.sv`（UVM / SystemVerilog）的工具。
自带 Excel 结构导出与表达式形态覆盖自检。

> CLI + GUI（PySide6）均可用。mux 页验证见 `mux验证说明.md`；级联驱动见 `级联模式说明.md`。

## 工作原理

不依赖旧 VB 生成的 `for_test` 页，而是：

1. **读 Excel**（`logic` / `regmap` / `total_memory_map` / `mux` 四页；无 mux 页则只做 logic）
2. **解析命名与地址**：输入名去 `_to_logic` → 在 `total_memory_map` 按字段名查地址(F)、位段(B)、
   RO/RW 类型；判 force（RO 管脚/只读）还是 `RF_WRITE`（RW 寄存器）
3. **求值表达式**：严格按 Verilog 两遍位宽语义实现三元 `?:` / 拼接 `{}` / 重复 `{n{}}` /
   按位 `~&|^` / 归约 / 比较 / 移位 / 位常量
4. **生成测试向量**：控制位（三元条件 + 门控位）全组合 × 数据总线确定性特征值；
   期望输出 = 在每个向量上对表达式求值
5. **渲染 .sv**：每信号一块，每 test 先 `force` / `RF_WRITE`（同地址 RW 字段合并成一条），
   `#1ps` 后 `assert_<R>_T<n>: assert(...)` 配 `uvm_report_info/error`
6. **mux 页**（2026-06 新增）：`G = case(B){F:A;…}` N 选 1——数据寄存器写互异值 +
   `assert_mux<N>_T<n>` 验证选路。支持**两种表形态**：LPBT（单控制、控制来自 logic 行、输出在顶层）
   与 WL_RFTRX（多控制拼接 `case={B,C,D,E}` + 控制可寄存器直出/级联上游 mux + 输出非顶层需 scan_rtl 前缀），
   工具自动适配。CLI `--no-mux` / `--mux-only` 控制范围；细节见 `mux验证说明.md`（含 WL 形态专章）

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
| `--regex RE` / `--type to_mux,ls` | 其它筛选 |
| `--include-internal` | **默认只生成 top_output=1（RTL 可见、要验证的输出）**；加此项才连 top_output=0 内部信号一起生成（内部信号在 ENV_RF 层探不到，会导致 elaboration 失败） |
| `--exclude N1,N2` / `--exclude-regex RE` | 排除信号（如 `--exclude-regex "pll_n|_to_dsm"` 排掉探不到的 datapath 中间信号） |
| `--mode min\|max` | 向量密度：min=控制全组合×1 数据特征；max=多数据模式 |
| `--exhaustive` | 总输入位很少时做真·全穷举 |
| `--neg-signals` / `--neg-all` | 选哪些信号加负向用例 |
| `--neg-mode invert\|inc\|value` `--neg-value 0xN` | 造错方式 |
| `--neg-which first\|all` | 每信号造 1 个还是每向量都造 |
| `--neg-file inline\|separate` | 负向放同文件还是单独 `*_neg.sv` |
| `--force-signals` / `--rfwrite-signals` | 手动指定 RO/RW（修正名称/类型判定） |
| `--cascade-mode cone\|force` | **级联驱动模式**（输入引用上游 logic 计算网时）：cone(默认)=展开上游表达式驱动其源头寄存器（纯 Excel）；force=直接 force 字面 `_to_logic` 网（隔离验证，需 scan_rtl 前缀）。**图解见 [级联模式说明.md](级联模式说明.md)** |
| `--no-wire-fallback` | 关闭 wire 兜底：非 RW 寄存器且查不到的输入不再默认 force，而是标 UNKNOWN |
| `--include-risky` | 强制生成含'不可驱动输入'(wire兜底/未解析)的信号。**默认跳过**这类信号（force 不存在的 net 会让 elaboration 失败；与 VBA 一致跳过） |
| `--diagnose` | 覆盖诊断：类型列原文分布 + 输入归类(RF_WRITE/force-RO/force-级联/force-wire/UNKNOWN)覆盖率 |
| `--comments` | 在 .sv 加少量导航注释（默认零注释，对齐真实模板） |
| `--report 路径.csv\|.html` | 导出给人看的测试用例表格（每信号汇总 + 每条用例明细：驱动值/期望/负向）。可与 `--out` 并用 |

**输入驱动模型**（沿用旧 for_test 规则）：输入是 **RW 寄存器**（在 total_memory_map/regmap 有地址）→ `` `RF_WRITE ``；
其余都是 **wire** → `force`（按信号名），包括 RO 管脚、**级联中间信号**（某输入其实是另一个 logic 的输出，
位宽自动取该输出的真实位宽）、以及表中查不到的信号（wire 兜底）。`--diagnose` 会把这几类分开列出，
其中"wire 兜底"是你需要重点确认"是否真是 wire、还是命名没匹配上的寄存器"的部分。

**级联（输入引用另一个 logic 行的输出）** 按上游行结构自动分流（背后的 RTL 知识与图解见
[级联模式说明.md](级联模式说明.md)）：上游**自引用** → `_to_logic` 是 regfile 前级 → force 基名；
上游**不自引用** → `_to_logic` 是上游表达式算的 → 按 `--cascade-mode` 选择展开上游（默认）或 force 该网。

**cone 展开后的溯源**（GUI 测试项页 / HTML 报告，2026-06 新增）：被展开过的信号（输入引用内部信号/上游计算网）
真正驱动的是叶子寄存器，为了能对回 Excel：

- **字母列 = Excel 来源坐标**：本行直接输入显示列字母（`E`）；上游行展开来的叶子显示
  `上游行名.字母`（如 `pll_n1.A` = logic 页 pll_n1 那一行的 A 列）——每个叶子都能直接翻回 Excel。
- **展开链**：真值表上方列出本行与逐层代入的上游行，每行两种形式——Excel 原式（L 列原文）
  与"字母代入真实信号名"的等价形式。链整体就是展开后的等价表达式（分行摆，不合并成一行）。

## 图形界面（PySide6）

```powershell
python -m dreg_verify.gui
```

加载 Excel → 信号表（按 owner / type / 名字 / **状态** 筛选，多选 + 全选/清空）→ 勾"负向"列加异常用例
→ "预览选中"看 .sv 片段 → "生成 .sv …"导出。后端与 CLI 同一套逻辑。

**debug 辅助**：每信号有**状态列**（clean / ⚠wire兜底 / ✗未解析 / 解析错）——非 clean 的最可能导致
elaboration 失败；**点信号**看它 force/RF_WRITE 哪些 net 的明细（对比 `ENV_RF` 层是否真有该 net）；
**覆盖诊断**按钮列出所有 wire兜底/未解析输入；**状态筛选**可只看有问题的信号，用来二分定位"跑不出结果"。

## RTL 层级扫描与探针前缀（`scan_rtl.py`）

`.sv` 里 force / assert 的网如果不在 DUT 顶层，必须带层级前缀（如 `` `ENV_RF.U_BT_LP_PLL_DIG.pll_n ``），
否则仿真报 **CUVUNF（找不到 net）**。`scan_rtl.py`（单文件零依赖，可直接拷到仿真服务器）
静态扫描 RTL，把 Excel 里每个信号的层级**一次找全**，不再"仿真→报错→grep→重生成"逐个试错：

```bat
:: ① Windows: 从 Excel 导出需要定位的网
python scan_rtl.py --excel 真表.xlsx --export-nets nets.txt
:: ② 把 scan_rtl.py + nets.txt 上传仿真服务器
:: ③ 服务器(source 过 dreg 环境后)零参数全自动:
::      python3 scan_rtl.py
:: ④ 生成的 probe_prefixes.txt 拷回 Windows:
::      GUI「设置探针前缀 → 导入…」 或 CLI --probe-prefix-file probe_prefixes.txt
```

什么时候要跑 / 输出三段怎么看 / 全部参数 / 单机用法：见 **[scan_rtl使用说明.md](scan_rtl使用说明.md)**。

## auto_out 与「期望」分离（防自证验证）

Dreg 验证的对象是 **designer 写的逻辑表达式本身**。工具按表达式算出的输出值（`auto_out`）
如果直接当断言期望，等于"用表达式验证表达式"——自证，抓不到表达式写错的 bug。
因此期望值拆成两个概念：

| | auto_out | 期望 |
|---|---|---|
| 来源 | 程序按表达式自动计算 | **designer 自己手填**（GUI 真值表 / 导入编辑文件） |
| 作用 | 只读参考 | **`.sv` 断言的对比值** |
| 未填时 | — | 生成 .sv 时用 auto_out 兜底（并在报告/完成弹窗里标出兜底条数） |

- **手填期望 ≠ auto_out 不算负向**：仿真该断言 FAIL 恰恰说明表达式与 designer 意图不符——这正是要抓的 bug。
- **GUI 真值表**拆成 auto_out（只读）+ 期望（可编辑）两行；「auto→期望」按钮可一键采信表达式值
  （只填未填的列）。绿=手填且与 auto_out 一致；红=手填但不一致。
- **编辑自动存盘**（含手填期望/负向/自定义列，按 Excel 路径存 `~/.dreg_verify_edits.json`），关 GUI 不丢；
  「导出编辑…/导入编辑…」可给同事复用或入版本库。
- **HTML 报告**新增「**真值表检查**」标签页：点「开始检查」遮盖所有 auto_out、期望变成填空——
  designer 只看输入自己算输出，回车立即判定（绿=与表达式一致 / 红=不一致），离线自测不被 auto_out 影响。

## 表达式形态覆盖自检（强烈建议先跑）

由于生成器完全从 `logic.L` 表达式重推，先确认求值器能解析真表里**所有**表达式形态：

```powershell
python inspect_excel.py 核心文件.xlsx --expr-forms
```

会导出 `<名字>_exprforms.txt`：枚举所有不同表达式、按结构形态归并、并用求值器逐条试解析
（`[OK]` / `[解析失败]`）。若有 `[解析失败]`，把那几条发给维护者扩展 `dreg_verify/expr.py` 即可。

## 抽取 VBA 宏源码（`inspect_vba.py`）

原始核心文件是带宏的 `.xlsm`，旧的 `.sv` 由其中 VBA 宏生成。要 1:1 复刻生成逻辑（地址算法、cone 展开、消息格式等），直接读 VBA 源码最准。

```powershell
pip install oletools                       # 推荐(更稳)；缺失时脚本用内置纯 Python 兜底
python inspect_vba.py 核心文件.xlsm --list   # 列出模块，标出疑似生成 .sv 的模块
python inspect_vba.py 核心文件.xlsm          # 导出全部 VBA 源码到 <名字>_vba.txt
python inspect_vba.py 核心文件.xlsm --find "Print #,RF_WRITE,pll_n,top_output"   # 定位相关过程
```

## Excel 结构导出（`inspect_excel.py`）

把各 sheet 的列结构 / 表头 / 样本 / 取值枚举导出成文本，便于审阅。

```powershell
python inspect_excel.py 核心文件.xlsx --compact --mask-owners --rows 10 `
    --sheets logic,regmap,total_memory_map
```

## mux 页排版探查（`inspect_mux.py`）

新设计 / 新表第一次做 mux 验证前，先探查 mux 页的真实排版（列结构 / case 值形态 / 与 logic·regmap
页的衔接关系），确认与工具的解析假设一致：

```powershell
python inspect_mux.py 核心文件.xlsx --mask-owners
```

导出 `<名字>_mux_inspect.txt`。LPBT 真表已探查过（结果固化进 `excel_model.read_mux`）；
换设计（如 WUR）时重跑一次，排版不同就把 txt 发给维护者适配。

## 演示 Excel（`make_sample_excel.py`）

没有真表也能体验完整流程——生成一个覆盖典型场景（直通 / mux / 门控 / 多位总线 / 同地址合并 /
RO·RW 混合）的示例表：

```powershell
python make_sample_excel.py demo.xlsx
python -m dreg_verify.gui demo.xlsx
```

## 测试

```powershell
python -m pytest -q
```

涵盖：表达式求值器（含 Verilog 位宽陷阱）、端到端（合成 Excel → 校验 RF_WRITE 合并值等）。

## 目录

```
dreg_verify/            生成器后端（CLI 与 GUI 共用）
  expr.py               表达式词法/解析/两遍位宽求值/变量角色分类/case 字面量(x 位)
  excel_model.py        读 logic/regmap/total_memory_map/mux 四页
  resolver.py           命名→RO/RW + 地址 + 位段（多策略匹配，失败清晰标注）
  cone.py               cone 展开：内部信号/上游计算网的表达式代入 + 展开链记录
  vectors.py            logic 测试向量生成 + 负向用例 + designer 手填期望
  mux_gen.py            mux 测试生成：控制路径发现/互异值/覆盖度三档
  sv_writer.py          渲染 .sv（含同地址 RW 合并、负向标记、汇总块）
  generator.py          编排 + 筛选 + 报告（build/report 双轨）
  rtl_scan.py           Excel→需定位网清单（scan_rtl.py 的 Excel 侧配套）
  cli.py                命令行入口
  gui.py                PySide6 图形界面（筛选/测试项编辑/探针前缀/预览/导出）

scan_rtl.py             RTL 层级扫描 → 探针前缀映射（单文件零依赖，可拷到仿真服务器）
inspect_excel.py        Excel 结构导出 + 表达式形态覆盖报告(--expr-forms)
inspect_mux.py          mux 页排版探查（新设计首次做 mux 验证前跑）
inspect_vba.py          .xlsm 的 VBA 宏源码抽取
make_sample_excel.py    生成演示用示例 Excel
tests/                  单元 + 端到端 + GUI 离屏冒烟测试（含合成 Excel 夹具）

README.md               本文档（核心用法）
scan_rtl使用说明.md      RTL 扫描与探针前缀：何时跑/两段式工作流/输出怎么看
级联模式说明.md          级联驱动两种模式（展开上游 vs force级联网）图解
mux验证说明.md           mux 页验证：语义/测试配方/覆盖度三档/log 解读
mux环境验证.md           mux 网名 RTL 环境核查步骤（scan_rtl 实例）
mux功能影响面分析.md      mux 功能实现蓝本（开发参考）
```

## 注意

- 真实 Excel（`*.xlsx`）、导出文本、生成的 `wr_rf_tc.sv` 均已在 `.gitignore` 中排除，**不会**提交。
- `.sv` 顶部的 `ENV_RF` / `RF_WRITE` / `uvm_*` 为脚手架宏；消息模板集中在 `sv_writer.py` 常量，可按需调整。
