# scan_rtl.py 使用说明 —— RTL 层级扫描与探针前缀

> 单文件、零第三方依赖（纯 Python 标准库），可直接拷到仿真服务器上运行。
> 配套文档：`级联模式说明.md`（force 级联模式为什么需要前缀）、`mux环境验证.md`（mux 网名核查实例）。

## 一、解决什么问题

`.sv` 里的 force / assert 都要写真实的 RTL 层级路径：

```systemverilog
force `ENV_RF.d_bt_lp_lna_agc_line[2:0]=16'h2;            // 网在 DUT 顶层 → 直接写名字
assert (`ENV_RF.U_BT_LP_PLL_DIG.pll_n[31:0]==32'h...)     // 网在子模块里 → 必须带层级前缀
```

Excel 只知道信号名，不知道它在 RTL 的哪一层——前缀漏配/配错，仿真就报 **CUVUNF（找不到 net）**，
elaboration 直接失败。

没有这个工具时的流程：生成 → 仿真 → CUVUNF → 手工 grep RTL → 配一个前缀 → 重新生成 → 再仿真 →
下一个 CUVUNF……每个有问题的信号循环一次。

**scan_rtl.py 把这个循环压成一次**：静态扫描 RTL，把 Excel 里每个信号的层级一次找全，
生成 `probe_prefixes.txt`，直接导入 GUI / CLI。

## 二、什么时候需要跑

| 场景 | 要不要跑 |
|------|---------|
| 第一次在新设计 / 新 Excel 上做验证 | ✅ 必须（顺便拿到"RTL 里找不到的信号"清单） |
| 仿真报 CUVUNF / elaboration 找不到 net | ✅ 跑一次，比逐个 grep 快 |
| 要切 force 级联模式（`--cascade-mode force`） | ✅ 必须（`_to_logic` 网都在 sig_logic 模块内部） |
| Excel 加了 mux 页、第一次生成 mux 测试 | ✅ 建议（核对 mux 输出 / 线控网的层级，见 `mux环境验证.md`） |
| WL_RFTRX 形态的表（mux 输出全部 top_out=0） | ⭕ 建议（默认用裸名探针照常生成；仿真 elaboration 报 CUVUNF 时跑一次配前缀重生成，见 `mux验证说明.md` 6.4） |
| 日常用 cone 模式（默认）重新生成 | ❌ 不用（cone 模式纯 Excel；已配过的前缀继续生效） |
| RTL 改版（信号搬了模块） | ✅ 重跑，新文件覆盖旧映射 |

## 三、两段式工作流（Excel 在 Windows、RTL 在 Linux 服务器）—— 推荐

### ① Windows 工具机：从 Excel 导出"需要定位的网"清单

```bat
python scan_rtl.py --excel 真表.xlsx --export-nets nets.txt
```

nets.txt = 所有输出的探针网 + 所有 force 输入网（**两种级联模式的并集**，之后切模式不用重扫）
+ mux 页相关网（有 mux 页时控制台会显示 `✓ mux 页: 发现 N 个相关网...`）。

### ② 上传 2 个文件到仿真服务器

`scan_rtl.py` + `nets.txt`，放到运行目录。服务器只需要 python3，**不需要装任何第三方库**。

### ③ 服务器：扫描 RTL（先 source dreg 环境）

```bash
python3 scan_rtl.py
```

零参数全自动——脚本从 dreg 验证环境变量推断一切：

| 环境变量 | 推断出什么 |
|---------|-----------|
| `$dreg_dir` + `$dreg_file` | DUT 顶层文件 = `$dreg_dir/$dreg_file.v` |
| `$dreg_top` | 顶层模块名 |
| （无变量） | RTL 扫描范围 = `$dreg_dir` 上两级（整个 digital/）；信号清单 = 当前目录 nets.txt |

没 source 环境（或要手动控制）时全部参数手给：

```bash
python3 scan_rtl.py --nets nets.txt \
    --top /path/to/lpbt_dig_top.v \
    --rtl-dirs /path/to/digital/pll,/path/to/digital/common \
    --out probe_prefixes.txt
```

### ④ probe_prefixes.txt 拷回 Windows，导入工具

- **GUI**：「设置探针前缀」按钮 → 「导入…」→ 选这个文件（与现有映射合并，同名以导入为准）
  → 左表里原来 needs-prefix / ✗未解析 的信号状态变 clean
- **CLI**：加 `--probe-prefix-file probe_prefixes.txt`（单个信号临时配：`--probe-prefix 信号=层级路径`）

之后正常生成 .sv，所有 force / assert 自动带上正确层级。

## 四、单机工作流（Excel 和 RTL 在同一台机器）

```bat
python scan_rtl.py --excel 真表.xlsx --top lpbt_dig_top.v --rtl-dirs rtl目录 --out probe_prefixes.txt
```

一步到位（= 两段式的 ① 和 ③ 合并，需要本机有 openpyxl）。

## 五、输出文件 probe_prefixes.txt 怎么看

文件分三段：

```
# 由 scan_rtl.py 自动生成 (DUT top: LPBT_DIG_TOP)
# 每行: 信号名=ENV_RF 之下的层级路径

pll_n=U_BT_LP_PLL_DIG                          ← ① 在子模块里 → 这些行就是映射本体
d2a_cnt_sclk=U_BT_LP_PLL_DIG.U_BT_LP_DREG

# ── 以下信号在 DUT 顶层就能探到，无需前缀 ──
# d_bt_lp_rccal_i                               ← ② 顶层直达 → 不用任何配置
# d_bt_lp_linectrl_tsensor

# ── ⚠ 以下信号在 RTL 中找不到（不可验证，建议反馈 Dreg 团队核对）──
# d_xxx_yyy    (输出 d_xxx_yyy[3:0] 的 assert 探针)    ← ③ RTL 里没有这个名字
```

| 段 | 含义 | 怎么处理 |
|----|------|---------|
| `信号=路径` | 网在子模块，需要前缀 | 导入即可，工具自动带前缀 |
| `# 顶层就能探到` | 网在 DUT 顶层 | 不用管 |
| `# ⚠ RTL 中找不到` | RTL 里不存在这个名字 | Excel 与 RTL 命名对不上 / 信号不存在 → 反馈 Dreg 团队；这些信号生成时会被**跳过并写明原因**，不会硬生成导致 elaboration 失败 |

## 六、全部参数

| 参数 | 说明 | 默认 |
|------|------|------|
| `--excel 真表.xlsx` | 从 Excel 抽信号清单（需要 dreg_verify 包 + openpyxl，Windows 工具机用） | |
| `--export-nets nets.txt` | 只导出清单不扫描（两段式第①步，配合 `--excel`） | |
| `--nets nets.txt` | 用现成清单扫描（服务器上用，零依赖） | 当前目录 nets.txt |
| `--top 顶层.v` | DUT 顶层文件 | `$dreg_dir/$dreg_file.v` |
| `--top-module 名` | 顶层模块名 | `$dreg_top`，或 `--top` 文件里第一个 module |
| `--rtl-dirs A,B` | RTL 目录（逗号分隔，递归扫 .v/.sv） | `$dreg_dir` 上两级 |
| `--out 文件` | 输出映射文件 | probe_prefixes.txt |
| `--max-depth N` | 层级展开深度 | 4 |

## 七、原理与限制

- **纯文本静态解析**：解析每个 module 里声明的 input/output/inout/wire/reg/logic + 子模块实例化，
  从 DUT 顶层递归展开成「信号 → 层级路径」表，再与 nets.txt 对照。
- 同名信号出现在多个层级时取**最浅**的那个。
- 不展开 generate / 宏 / \`include——这类极端结构找不到时会落到"找不到"段；
  确认信号真实存在的话，在 GUI『设置探针前缀』里手写一行 `信号=路径` 补上即可。
- **换设计（如 WUR）零修改可用**：服务器端只认环境变量和 nets.txt；Windows 端换 `--excel` 指向新表即可。
