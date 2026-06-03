# mux 环境验证 —— 动手写 mux 功能前，先确认 RTL 里的网都能探、能驱动

> 2026-06-03 第九轮。目的：避免重蹈 logic 验证时 CUVUNF（网不在 ENV_RF 层级）反复试错的覆辙。
> mux 功能代码还没写——这一步是先把环境验清楚，结果会直接决定 mux 功能里网名怎么解析。
> scan_rtl.py 的通用用法（何时跑/全部参数/输出怎么看）见 **`scan_rtl使用说明.md`**，本文是它在 mux 场景的实例。

## 一、这一步在验证什么

mux 页的测试要在 RTL 上碰三类网。designer 的 .sv 证明它们在他的环境里都是顶层直达的，
但我们要自己扫一遍确认（logic 验证时 d2a_cnt_sclk 就吃过"以为在顶层、实际在子模块"的亏）：

| # | 类型 | 数量 | 例子 | 测试里怎么用 |
|---|------|------|------|------------|
| ① | mux 输出（mux 页 G 列） | 7 | `d_bt_lp_lpf_bias_i[3:0]` | `assert (`ENV_RF.xxx==…)` 探针 |
| ② | 控制信号的线控输入 | 3 | `d_bt_lp_linectrl_tsensor[3:0]` | `force `ENV_RF.xxx=…` |
| ③ | logic→mux 衔接网 | 3 | `d_logic_bt_lp_tsensor_to_mux[3:0]` | 不直接碰，核对 RTL 结构用 |

数据寄存器（mux 页 A 列那 49 个）走 `RF_WRITE`，不涉及网名，无需验证。

## 二、操作步骤（就是原来的 scan_rtl 两段式，nets.txt 会自动多出 mux 的网）

### ① Windows 工具机（本仓库目录）

```bat
git pull
python scan_rtl.py --excel "Hi1108_Pilot_BT_LP_DREG_95P_28May.xlsx" --export-nets nets.txt
```

✅ 确认控制台出现这一行（没有这行 = mux 页没读到，停下来反馈）：

```
✓ mux 页: 发现 N 个相关网，新增导出 N 个（输出探针 + 控制衔接网）
```

### ② 上传 2 个文件到仿真服务器

`scan_rtl.py` + `nets.txt`，放到运行目录。

### ③ 服务器上（先 source dreg 环境）

```bash
python3 scan_rtl.py
```

零参数，全自动（从 `$dreg_dir`/`$dreg_file`/`$dreg_top` 推断），生成 `probe_prefixes.txt`。

### ④ 把 probe_prefixes.txt 全文贴回给 Claude

## 三、结果怎么看

`probe_prefixes.txt` 分三段：

| 段 | 含义 | 对 mux 网的判定 |
|----|------|---------------|
| `信号=层级路径` | 网在子模块里，需要前缀 | ⚠ 可以接受——mux 功能实现时自动带前缀，但要记下来 |
| `# ── 顶层就能探到 ──` | 网在 DUT 顶层 | ✅ 理想情况（designer 的 .sv 暗示应该全在这） |
| `# ── ⚠ RTL 中找不到 ──` | RTL 里不存在这个名字的网 | ❌ 有问题，见下 |

**通过标准**：下面 13 个网全部落在前两段（顶层 或 有前缀）：

```
① 输出探针 (7):  d_bt_lp_rccal_i        d_bt_lp_rccal_q       d_bt_lp_lpf_bias_i
                d_bt_lp_lpf_bias_q     d_bt_lp_lna_cmatch    d_bt_lp_lna_itrim
                d_bt_lp_mix_bias
② 线控输入 (3):  d_bt_lp_linectrl_lpf_agc   d_bt_lp_linectrl_tsensor   d_bt_lp_linectrl_lna_agc
③ 衔接网   (3):  d_logic_bt_lp_lpf_agc_to_mux   d_logic_bt_lp_tsensor_to_mux   d_logic_bt_lp_lna_agc_to_mux
```

任何一个落在"找不到"段 → 不要继续，把整个 `probe_prefixes.txt` 贴回来分析
（可能是 Excel 名字和 RTL 对不上，也可能是 RTL 的 mux 结构和我们理解的不一样）。

## 四、和原有流程的关系

- 这次扫描**顺便把 logic 页的网也重扫了一遍**（nets.txt 是 logic + mux 的并集），
  原有的 probe_prefixes.txt 可以直接被新的覆盖，不会丢东西。
- 服务器端的 scan_rtl.py 没有任何改动——只有 Windows 端导出的 nets.txt 变长了。
- 验证通过后，probe_prefixes.txt 留着：mux 功能实现后，GUI『设置探针前缀 → 导入』同一个文件。
