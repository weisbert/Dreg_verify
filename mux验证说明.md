# mux 页验证说明

> 2026-06-03 第九轮新增。从此工具同时验证 **logic 页**（真值表达式）和 **mux 页**（N 选 1 选择器）。
> 配套文档：`级联模式说明.md`（mux 控制信号的驱动复用级联机制）、`mux环境验证.md`（RTL 网名核查流程）。

## 一、mux 页是什么、和 logic 页有什么不同

| | logic 页 | mux 页 |
|---|---------|--------|
| 语义 | 输出 = 真值表达式（三元/位运算） | 输出 = case(控制信号) N 选 1 |
| 例子 | `lpf_agc = 模式位 ? 本地寄存器 : 线控` | `rccal_i = case(lpf_agc){010:G1; 011:G2; …}` |
| 物理含义 | 线控/本地二选一 + iddq 门控 | 增益/温度档位 → 选对应的校准寄存器 |
| 被验证输出 | K 列（带 _ls/_to_logic 后缀规则） | G 列原名（顶层网，零后缀，已实测） |
| assert 标签 | `assert_<R列>_T<n>` | `assert_mux<N列>_T<n>`（mux 前缀防撞号） |
| iddq 测试 | 有 | 无（designer 同样不做） |

**两页的衔接**：mux 页的控制信号（B 列）就是 logic 页 M=to_mux 行的输出。
驱动 mux 的控制 = 驱动那行 logic 的输入（line 路径 force 线控 / local 路径写本地寄存器）。

## 二、一个 mux 测试长什么样

以 `d_bt_lp_mix_bias[3:0] = case(tsensor) 8 选 1` 为例，每个测试 T：

```systemverilog
`RF_WRITE(10'h2D,16'h0);      // ① 控制信号的模式位 = 0（line 路径）
`RF_WRITE(10'h30,16'h789A);   // ② 数据寄存器 t1..t4 = 互异值 A,9,8,7
`RF_WRITE(10'h31,16'h3456);   //    数据寄存器 t5..t8 = 互异值 6,5,4,3
`RF_WRITE(10'h32,16'hD);      // ③ 控制的本地寄存器 = 干扰值（证明走的是 line 路）
force `ENV_RF.d_bt_lp_linectrl_tsensor[3:0]=16'h2;   // ④ 线控 = 第 2 个 case 的值
#1ps;
assert_mux7_T1:
assert (`ENV_RF.d_bt_lp_mix_bias[3:0]==4'b1001)      // ⑤ 期望 = 被选中寄存器 t2 的值 0x9
```

**测试集**（覆盖度三档，2026-06-03 第十一轮拉开差距——每升一档多抓一类真实故障）：

| 覆盖度 | mux 测试内容 | 多抓什么故障 | tsensor 类（8 case 带 x 位）T 数 | 无 x 位 4 选 1 的 T 数 |
|--------|------------|------------|-----------------------------|---------------------|
| 精简 | line 路径扫每 case（x 位取 0）+ 1 条另一路径抽测 | case 接到错的寄存器（选路错） | 8+1 = 9 | 4+1 = 5 |
| 全面 | 精简 + x 位展开 + 每 case 一轮**反码数据** | 数据通路位坏死（第一轮值恰好等于坏死值时测不出，反码轮每位都翻过） | 16+8+1 = 25 | 4+4+1 = 9 |
| 穷举 | 全面 + **另一条控制路径全扫**每 case（取代单条抽测） | 只在另一条物理驱动路径下出现的选路错 | 16+8+16 = 40 | 4+4+4 = 12 |

**另一条路径**：控制信号切到 local 路径（模式位=1 + 本地寄存器=case 值），
验证控制级联的两条物理路都通——对应 designer 测试里的最后一个用例。
精简/全面只抽测 1 条（case[0]）；穷举对每个 case 都走一遍。

**反码数据轮**（全面/穷举）：同一批 case 再扫一遍，但数据寄存器写第一轮互异值的按位取反
（如 0xA,0x9,0x8 → 0x5,0x6,0x7）。两轮合起来，每个数据寄存器的每一位都被驱过 0 和 1——
数据通路上某位接断/短接（stuck-at）时，必有一轮能抓到。

**为什么数据寄存器必须写互异值**：如果两个寄存器值相同，RTL 选错路时输出也相同
→ 断言照样 PASS → 假绿（测试全绿但什么都没验证）。工具保证互异；
位宽装不下互异值的组会**跳过并告知原因**（这种组没法验证选路，硬生成出来的是无效测试）。

## 三、怎么用

### CLI

```bat
:: 默认 logic + mux 都生成（推荐）
python -m dreg_verify.cli --excel 真表.xlsx --out wr_rf_tc.sv

:: 只出 mux（隔离调试用）
python -m dreg_verify.cli --excel 真表.xlsx --out mux_only.sv --mux-only

:: 不出 mux（回到纯 logic，与旧版产物一致）
python -m dreg_verify.cli --excel 真表.xlsx --out logic_only.sv --no-mux

:: HTML 报告（mux 组出现在全部四个标签页：汇总/真值表(case 选择表)/明细/可验证性）
python -m dreg_verify.cli --excel 真表.xlsx --report report.html
```

负向（`--neg-all` / `--neg-signals`）、owner（`--owner-in-msg`）、汇总（`--sv-summary`）、
探针前缀（`--probe-prefix-file`）对 mux 全部照常生效。

### GUI

- mux 信号与 logic 信号**同表混排**：type 列显示 `mux`，R 列显示 `mux1..mux7`，表达式列显示 case 结构
- 勾选/批量勾选/owner 过滤/type 过滤（选 `mux` 只看 mux）/搜索 照常
- **负向勾选**：勾上 = 生成时给该 mux 组追加 1 条故意填错的自检断言（`_NEG`）
- 点 mux 信号 → 右侧**只读 case 表**（行=控制+数据寄存器+期望，列=测试）；
  mux 测试项由 case 结构自动生成，不支持手工编辑（logic 信号的编辑器照常）
- 「预览本信号 .sv」「生成 .sv」「导出报告」照常可用
- 覆盖度下拉切换 → mux 的 T 数实时重算

## 四、什么样的 mux 组会被跳过（跳过必有名字+原因）

| 原因 | 含义 | 怎么修 |
|------|------|--------|
| 控制信号在 logic 页找不到对应行 | mux↔logic 衔接断裂 | 核对 mux 页 B 列拼写 |
| 数据输入不是带地址的 RW 寄存器 | 写不进互异值 | 核对 A 列名 ↔ tmm/regmap |
| case 值位宽与控制信号位宽不一致 | case 命中会错位 | 修 Excel 的 F 列 |
| 数据寄存器位宽装不下互异值 | 选路不可验证（假绿） | 拆分组或加宽字段 |
| 控制信号表达式没有可透传路径 | 无法把控制驱到指定值 | 检查该 logic 行表达式 |

## 五、仿真 log 怎么读（与 logic 一致）

- `assert_mux<N>_T<n>` 的 UVM_ERROR（不带 `_NEG`）= **真问题**（RTL 选路错 / Excel case 表错）
- `assert_mux<N>_T<n>_NEG` 的 UVM_ERROR + `NEG-EXPECTED-FAIL` 标签 = 故意的反例，预期内
- `NEG-BROKEN` 出现 = 反例没起作用，要查
- 开了 `--sv-summary` 时末尾汇总行的 `REAL FAIL` 只统计真问题

## 六、WL_RFTRX 形态的 mux（多控制 / 寄存器直出 / 级联）

> 2026-06-03 第十四轮新增。前面 ① ~ ⑤ 讲的是 LPBT 那种「单个控制信号、控制来自 logic 行、
> 输出在顶层」的 mux。WL_RFTRX 的 mux 页排版不一样：一个 mux 可以有**好几个控制信号**、
> 控制信号**直接来自寄存器或上游另一个 mux**、而且**输出全都不在 DUT 顶层**。
> 工具对这两种形态自动适配，下面讲清楚区别和操作。

### 6.1 两种表形态对照

| | LPBT 形态 | WL_RFTRX 形态 |
|---|---|---|
| 控制信号个数 | 1 个（B 列） | 1 ~ 4 个（B/C/D/E 列） |
| 控制信号来源 | logic 页一行（line/local 二选一） | 多数是**寄存器直出**（RW），也可能是 logic 行或**上游另一个 mux 的输出** |
| 数据输入来源 | RW 本地寄存器 | RW 本地 / **RO 线控**（linectrl，force 驱动）/ **上游 mux 衔接网** |
| 输出位置 | 顶层（top_out=1，零前缀直探） | **全部不在顶层**（top_out=0，assert 探针**必须带层级前缀**） |
| case 值 | 单段，宽度 = 控制位宽 | 各控制按列序**拼接**，可含 don't-care（x）位 |

### 6.2 多控制拼接语义：`case = {B,C,D,E}`，B 是高位

一个 mux 有多个控制信号时，case 值（F 列）就是**把各控制信号按列字母顺序拼起来**——
**B 列在最高位，往右依次 C、D、E**。位宽 = 各控制位宽之和。

以真表组4 `tx_rc_code[5:0]` 为例，它有两个控制：B 列 `lut_en`（1 位）、C 列 `bwctrl`（4 位），
case 总宽 = 1 + 4 = 5 位，工具把它显示成 `case({lut_en, bwctrl})`：

| F 列 case 值 | 拆开（B 高位） | 选中的数据输入 | 含义 |
|---|---|---|---|
| `5'b0xxxx` | lut_en=0，bwctrl=**don't care** | local 寄存器 | lut 没使能时，不管 bwctrl 是几都走 local |
| `5'b10000` | lut_en=1，bwctrl=0 | lut0 | lut 使能 + 带宽档 0 |
| `5'b10001` | lut_en=1，bwctrl=1 | lut1 | … |
| **`5'b10010`** | **lut_en=1，bwctrl=2** | **lut2** | ← 黄金样本：高 1 位给 lut_en，低 4 位给 bwctrl |
| `5'b10011` | lut_en=1，bwctrl=3 | lut3 | |

**黄金样本 `5'b10010`**：最高位 `1` 是 B 列的 lut_en，剩下 `0010`（=2）是 C 列的 bwctrl。
工具就是这么拆的——拆错一位整张 case 表就对错号。`x`（don't-care）位在生成测试时取 0 代入。

### 6.3 三种控制来源各自怎么驱动

mux 的控制信号要被驱到某个 case 值，按它的来源工具自动选驱动方式：

| 控制来源 | 怎么识别 | 怎么驱动到目标 case 值 |
|---|---|---|
| **寄存器直出**（WL 最常见） | 控制名在 total_memory_map 里是带地址的 RW 字段 | 一条 `RF_WRITE` 直接写模式寄存器（如 case 要 1 就 `RF_WRITE(模式寄存器, 1)`） |
| **logic 行**（LPBT 形态） | 控制名是 logic 页某行的输出 | 走那行的 line/local 双路径（同 LPBT：模式位 + 线控/本地寄存器，见本文 ① ~ ②） |
| **上游另一个 mux** | 控制名是另一个 mux 组的输出（mux 套 mux 级联） | **自动展开上游配方**：把上游 mux 驱到目标值 = 写上游的**模式寄存器** + 写上游被选中的**载体寄存器**（优先选 RW 那条路），两条 `RF_WRITE` |

数据输入同理三种：RW 本地寄存器→`RF_WRITE`；RO 线控→`force`（线控本身是顶层 RO 寄存器，无前缀）；
上游 mux 衔接网→`force` 那根衔接网（在子模块内部，**需要探针前缀**）。

### 6.4 ⭐ WL 表必须先跑 scan_rtl（输出全部不在顶层）

**这是 WL 形态和 LPBT 最关键的操作差异，也是最容易踩的坑：**

WL 的 mux 输出**全部 top_out=0**——它们不在 DUT 顶层，而在某个子模块内部。
assert 探针 `` `ENV_RF.<输出名> `` 直接写名字会 **CUVUNF（找不到 net）**，必须带层级前缀
（如 `` `ENV_RF.U_WL_DREG.U_RF_MUX.d_wl_rf_lna_gain ``）。

因此 **没导入探针前缀时，所有 WL mux 组都会被跳过**，跳过原因里写明「输出不在 DUT 顶层、
需要跑 scan_rtl 拿层级前缀」。**这不是 bug，是设计**——宁可跳过并说清原因，也不硬生成一份
注定 elaboration 报废的 .sv。

| 现象 | 含义 | 怎么办 |
|---|---|---|
| GUI 状态列显示「**需探针前缀(跑scan_rtl)**」 | 该 mux 结构没问题，只是缺层级前缀 | 跑 scan_rtl 导入前缀（真值表 / case 表照常能看，不受影响） |
| CLI 生成时把 mux 组跳过、原因含 `scan_rtl` | 同上 | 同上 |
| 导入前缀后状态变 clean、正常生成 | 一切就绪 | 正常生成 .sv |

操作步骤（详见 `scan_rtl使用说明.md`）：

```bat
:: ① 从 Excel 导出需要定位的网（含 mux 输出 / 线控网 / 级联衔接网）
python scan_rtl.py --excel 真表.xlsx --export-nets nets.txt
:: ② 上传 scan_rtl.py + nets.txt 到仿真服务器，source dreg 环境后零参数扫描
::      python3 scan_rtl.py
:: ③ probe_prefixes.txt 拷回，导入工具：
::      GUI「设置探针前缀 → 导入…」 或 CLI --probe-prefix-file probe_prefixes.txt
```

> WL 的 mux 输出是非顶层信号，CLI 默认只取顶层输出（top_output=1）。要把这些非顶层 mux 纳入，
> 命令行加 `--include-internal`（之后仍由探针前缀机制按上表放行/跳过）；GUI 里直接勾选即可。

带前缀生成的一个 WL mux 测试长这样（组1 lna_gain，控制是寄存器直出、数据是 RO 线控 + RW 本地）：

```systemverilog
`RF_WRITE(10'h50,16'h0);                                       // ① 控制模式寄存器 = 0 → 选线控
force `ENV_RF.d_wl_rf_linectrl_lna_gain[2:0]=16'h1;            // ② RO 线控数据(顶层，force 无前缀)
`RF_WRITE(10'h51,16'h2);                                       // ③ RW 本地数据(干扰值，证明走线控)
#1ps;
assert_mux1_T0:
assert (`ENV_RF.U_WL_DREG.U_RF_MUX.d_wl_rf_lna_gain[2:0]==3'b001)  // ④ 输出带层级前缀(top_out=0)
```

### 6.5 通用形态下覆盖度三档的语义

WL 这种「多控制 / 寄存器直出 / 级联」的 mux 走**通用向量生成器**，覆盖度三档语义如下
（和 ② 里 LPBT 的 line/local 双路径不同——通用形态没有"另一条物理路径"概念）：

| 覆盖度 | 内容 | 多抓什么故障 |
|---|---|---|
| 精简 | 每个 case 一条向量（x 位取 0），控制驱到该 case 值、数据寄存器写互异值 | case 接到错的数据寄存器（选路错） |
| 全面 | 精简 + **don't-care（x）位展开**（每个 x 位 0/1 都扫一遍）+ 每个 case 一轮**反码数据** | x 位覆盖不全的选路错 + 数据通路某位坏死（反码轮把每位都翻过） |
| 穷举 | 同全面（通用形态没有"另一条控制路径"可全扫，所以与全面一致） | 同全面 |

互异值/反码轮的道理与 ② 一致：数据寄存器值必须互异，否则选错路也测不出（假绿）；
反码轮让每个数据位都被驱过 0 和 1，抓数据通路 stuck-at。位宽装不下互异值的组会**跳过并给原因**。
