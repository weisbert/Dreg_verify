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
