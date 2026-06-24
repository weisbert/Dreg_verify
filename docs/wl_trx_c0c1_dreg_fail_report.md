# WL_TRX_C0C1_DREG 寄存器验证失败分析（给 designer）

> 测试：`wr_rf_tc.sv`（自动生成的寄存器真值表验证）
> 表：`Hi1108V100_WL_RFTRX_C0C1_DREG_to_dig_99P_TEST_Loopback`
> 结论：报 `|||Failed|||`，但**绝大多数「失败」是工具的负向自检陷阱（NEG），不是真问题**。真正要排查的只有下面 **3 类、约 20 个信号**。

---

## 一、先看这条：怎么读 log（否则会被几百行 NEG 吓到）

工具给每个测试点生成 **正向** + **负向（`_NEG`）** 两条。负向 = **故意把断言里的期望值按位取反**，用来证明自检有效。所以：

- **`_NEG` 行：`sim out` 才是 RTL 真实输出（=正确值），`you set` 是它的按位取反（诱饵）** → 这种 mismatch 是设计好的，**不是 failure**。
  - 例：`d_wl_rf_rx5g_lna_gcode[4:0]  sim out:0xa  you set:0x15`（0x15 = ~0xa，5bit）= **通过**。
- 只有**不带 `_NEG` 的正向行**出现 mismatch，才是真问题。

一句话：**`grep -v _NEG` 之后剩下的才需要查。** 本报告下面列的就是这些。

---

## 二、真正的失败（3 类）

| 类别 | 信号数 | 症状 | 性质 |
|---|---|---|---|
| **A. 增益选择的 trim mux 冻结** | 16 | 输出恒定、不随增益/数据变 | spec ↔ RTL **增益选择结构 + 数据来源**不一致 |
| **B. 5G trxbuf 使能打不开** | 3 | 期望 1、sim=0 | 使能门控条件 RTL≠真表 |
| **C. 5G tx_lodiv 使能关不掉** | 1 | 期望 0、sim=1 | 使能门控条件 RTL≠真表 |

---

## 三、A 类（主项）：16 个增益选择的 trim mux 冻结

### 症状
这些 mux 输出在 sim 里**恒定不变**，不随写入的增益/数据变化（只有期望值恰好等于那个常数的测试点才过）：

| assert | 信号 | sim 恒定值 |
|---|---|---|
| mux102 | d_wl_rf_2g_lna_itrim_ptat | 0x9 |
| mux104 | d_wl_rf_2g_lna_itrim_ptat_temp | 0x9 |
| mux106 | d_wl_rf_5g_lna_itrim_ptat_auto | 0x9 |
| mux107 | d_wl_rf_5g_lna_itrim_ptat_beacon | 0x8 |
| mux109 | d_wl_rf_5g_lna_itrim_ptat_temp | 0x9 |
| mux124 | d_wl_rf_2g_lna_lctune | 0x2 |
| mux126 | d_wl_rf_2g_lna_lctune_gain | 0x2 |
| mux128 | d_wl_rf_2g_lna_mctune_gain | 0x2 |
| mux130 | d_wl_rf_2g_lna_mctune | 0x2 |
| mux138 | d_bt_slna_match | 0x2 |
| mux140 | d_bt_slna_match_gain | 0x2 |
| mux142 | d_bt_slna_rtune_1st | 0x2 |
| mux144 | d_bt_slna_bias_trim2 | 0x9 |
| mux146 | d_bt_slna_load_cap_trim | 0x2 |
| mux148 | d_bt_slna_rtune | 0x2 |
| mux150 | d_bt_slna_bias_trim3 | 0x9 |

### 根因：增益选择这一级，真表(spec) ↔ RTL 实现对不上

这些 trim 都是「按 LNA 增益从一组值里选一个」。真表把**增益**建成一个 **2:1** mux（regmap / mux 页 / SignalPath / for_test 四处口径一致）：

```
真表 spec:
  d_wl_rf_rx2g_lna_gain = ctrl_mode ? local : linectrl
       ctrl_mode = RF reg 0x4  bit10   (1=寄存器控制 / 0=线控)
       local     = RF reg 0xA  [2:0]
       linectrl  = RF reg 0x42 [2:0] (RO)
  d_wl_rf_2g_lna_itrim_ptat = 按 rx2g_lna_gain[2:0] 从 g0~g7 选
```

但 RTL（`dreg_for_syntex_check/WL_TRX_DREG_sig_logic.v:2339`）实际是 **3 级**：

```verilog
d_top_wb_2g_lna_gain = (d_mux_rx_gain_fb_sel==0)
     ? (d_wb_lna_gain_mode    ? d_wb_lna_gain_local : d_wb_linectrl_lna_gain)  // 普通路
     : (d_wb_fb_gain_map_mode ? d_wb_lna_gain_local : <gain_fb LUT>);          // 反馈路
   // d_wb_lna_gain_mode  = rw_reg_0[13]
   // d_wb_lna_gain_local = rw_reg_5[2:0]
```

**三处不一致：**
1. **RTL 多一层 `d_mux_rx_gain_fb_sel`（普通路 / gain-feedback-LUT 路）**——真表完全没建这层。若它默认不在「普通路」，增益来自反馈 LUT、和写入的 local 无关 → 增益不动。
2. **控制/寄存器对不上**：真表 `ctrl_mode=0x4[10] / local=0xA`；RTL `gain_mode=reg0[13] / local=reg5`，且多了 `fb_sel` / `fb_gain_map_mode`。
3. RTL 里没有 `d_wl_rf_rx2g_lna_gain` 这个网名（叫 `d_top_wb_2g_lna_gain`）。

→ 测试写 `0x4[10]=1` + `0xA=增益` 去扫，但 DUT 有效增益不跟着变 → trim 输出冻死。

### ⚠ 额外一层（仅 itrim_ptat 那 5 个 mux102/104/106/107/109）
RTL 里 itrim 的每档值还要再过一个 **温度 bank LUT**（`WL_TRX_DREG_sig_logic.v:2356~`）：

```verilog
d_logic_wb_rf_2g_rx_lna_itrim_ptat_G7_temp = (d_top_wb_temp_bank_sel==0)
        ? ..._G7_t0_lut_temp : ..._G7_t1_lut_temp;   // 来自 rw_reg_511+/644+
```

也就是 itrim 的**数据源在温度 LUT 寄存器（reg 511+/644+ 区）**，不是真表里写的那两个数据寄存器。所以这 5 个 itrim mux**既有增益选择问题、又有数据源问题**，两层都得对齐。

### 请 designer 确认
1. `d_mux_rx_gain_fb_sel` / `d_wb_fb_gain_map_mode` 默认值？要不要先设到「普通路」才能用 local 增益控 trim？
2. 真表这条增益链的寄存器（ctrl_mode=0x4[10]、local=0xA、linectrl=0x42）与 RTL（reg0[13]/reg5 + fb 层）对得上吗？是 **真表/regmap 该更新**，还是 **RTL 偏离了 spec**？
3. itrim 那 5 个：数据是否确实走温度 LUT（reg 511+/644+），真表数据寄存器映射要不要改？
4. 这 16 个同结构，确认一处即可推广其余。

---

## 四、B 类：5G trxbuf 使能打不开（assert_83 / 84 / 85）

```
d_wl_rf_lo2g5g_5g_trxbuf_lo_en   期望 1, sim=0   (T42/46/54/62)
d_wl_rf_lo2g5g_5g_trxbuf_tx_en   期望 1, sim=0   (T6)
d_wl_rf_lo2g5g_5g_trxbuf_rx_en   期望 1, sim=0   (T6)
```
这三个使能在某些配置下真表期望拉高、但 RTL 一直 0。请查它们在 RTL 里的使能门控条件，是否比真表多了额外 gating（某个 mode/en 没满足）。

---

## 五、C 类：5G tx_lodiv 使能关不掉（assert_99）

```
d_wl_rf_5g_tx_lodiv_en_to_logic   期望 0, sim=1   (T64~T124 大片正向)
```
与 B 相反——真表期望关、RTL 一直开。请查 `5g_tx_lodiv_en` 的关断条件，RTL 是否有一路把它强行拉高。

---

## 六、其余全部是 NEG 自检陷阱（请忽略）

assert_43~82、100~117 等几百行 `_NEG`，以及 mux101/103/105/.../209 等的 `_NEG`，均满足 `sim out = ~(you set)`，即 **RTL 输出 = 正确值、诱饵值对不上 = 自检通过**，非 failure。

（注：`mux131 d_bt_slna_gtune` 同属增益选择族，但仅 T25「全 0」一个正向点不过、其余正向都过，可与 A 类一并核。）
