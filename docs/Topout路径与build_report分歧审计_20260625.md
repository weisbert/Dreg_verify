# Topout/analyze/SignalView 路径 vs 老 generator.build/report 分歧审计

> ultracode 多代理审计（37 agent，30 候选→28 confirmed，每条已在 .venv + fixtures 复现）。
> 基准 HEAD `bcba4c4`。起因=刚修的 iddq 门回归（老 GUI 修过、Topout 重构后重现），用户问「还有没有类似的」。

**总结**：除刚修的 iddq 门（logic/register 分支），新路径还有 **3 处同型 iddq 门回归未修**（MUX 分支、排查(旧)页视图、register 富表/HTML），外加一片**结构性丢失**：警告通道、claims 导出、for_test 回填、logic_overrides、负向开关、owner 注解、输入行序、负向防撞。

## 三组共性根因
1. **iddq 门没进新视图**（M1/M2/m2）——与刚修的同根，只是分支不同。
2. **警告通道没透出**（M3/M5/M8/m1/m7）——build_for_topout 把 G.build 算好的 selfaudit/regmap/supplement/claims 丢弃。
3. **passthrough 块绕过 build 的注入层**（M6/m5）——register/dft改名根的 .sv 不经 generator 的块顶 ⚠/owner 注入。
最稳收口=让 register/renamed 的 `_topout_probe_block` 也跑 generator 的警告/owner 注入层，并给所有 topout API 统一接 GenOptions（neg_*/logic_overrides/owner_in_msg），而非各分支零散补。

---

## MAJOR

### M1. MUX 分支 analyze_signal 漏 iddq 门 + DFT 拍（刚修 bug 的第三个未修分支）
- 症状：dft 门控的 mux Topout 信号，GUI 真值表少一列、无 iddq 只读行；左清单 n_vectors+1、.sv 也含 iddq+DFT 拍 → 三处对不上。实测 analyze=40 向量/dft_gate=None，view_models/.sv=41 含 iddq。
- 根因：`topout.py:390-411` MUX 分支 make_mux_vectors 后直接 return，从不调 pin_dft_gate/_append_dft_vectors。logic/register 都补了，唯独 mux 漏。
- 修法：make_mux_vectors 解包后补 `obs=dft_obs_name or grp.out_base.lower()` → `_append_dft_vectors`+`res.dft_gate=pin_dft_gate`。无门 no-op，7 真族逐字节不变。

### M2. 排查(旧) logic/dft/iddq 页子视图漏 iddq 门 + DFT 拍
- 症状：页 tab 看门控信号真值表 3 输入/无门行/min 2 列；同页自己 build_page_sv 的 .sv 含 iddq+第 3 列 → 自相矛盾，编辑时管不到门。
- 根因：`pageviews.py:91-113`(analyze_logiclike) 只调 generate_vectors，PageResult 无 dft_gate；`gui.py:463-475`(_norm_page_result) 无 'dft_gate' 键。
- 修法：analyze_logiclike 用 `obs=sig.out_base.lower()` 补 pin；PageResult 加 dft_gate；_norm_page_result 照搬 _norm_topout_result 末尾。

### M3. build_for_topout / build_page_sv 返回 dict 丢掉全部警告通道
- 症状：CLI --topout 汇总 + GUI 账目看不到 selfaudit 假绿/regmap 重名/RTL 补充计数（n_with_issues 恒 0）。
- 根因：`topout.py:900-912` 只透传 dup_labels，把 `built=G.build(...)` 算好的 selfaudit/regmap/supplement/mux/spec_conflicts/claims 全丢；`pageviews.py:303-310` 同。
- 修法：return dict 补六通道透传 + summary 补计数。

### M4. CLI --topout / --page 静默吞 --export-claims（红区 binder 契约导不出）
- 症状：`--topout --export-claims c.json` 退出码 0、不写 c.json、不报错。直接打脸 goal-redzone-binder M0 北极星（Topout 已是默认门面）。
- 根因：`cli.py:1363-1368` 提前 return，cmd_topout/cmd_page 全程不调 _export_claims；topout/pageviews 不产 claims。
- 修法：build_for_topout 调 collect_claims(is_topout=True) 累进；cmd_topout/cmd_page 出 .sv 后补 _export_claims。

### M5. compose_topout_account 的 n_with_issues 不含 selfaudit/regmap/supplement
- 症状：`--topout --account` 漏报 RW 写值截断假绿、regmap 重名、RTL 补充偏离（n_with_issues=0 vs build n_selfaudit_warnings=1）。
- 根因：`topout.py:574-576` 只数 r['issues']（cone 问题），不含生成期三检查。
- 修法：account 对 logic/mux 根复用 build，summary 加三独立计数列。

### M6. register / dft改名根 .sv 块不补块顶 ⚠（iddq_skipped / selfaudit / regmap重名）
- 症状：寄存器/改名根若门解析不了/RW 截断/regmap 重名，.sv 块顶无 `// ⚠`；同情形 logic 根有。实测 register 撞 regmap 重名照样写首个寄存器（R35 同款坑）无提示。
- 根因：`topout.py:706-730`(_topout_probe_block/_register_passthrough_block) 只调 render_signal_block，不调 _regmap_dup_warning/_selfaudit_*；register 的 res.meta['iddq_skipped'] 被设后无人读。
- 修法：wb 透进 _topout_probe_block，render 后 prepend 三类 ⚠（一处覆盖 register+改名两路）。

### M7. 直连寄存器根完全不进 for_test 回填（.sv 和 HTML 报告里却都有）
- 症状：「回填 for_test」对 clk_force_on/en_dig_clk 等寄存器根拿不到组/列=空白；同信号 .sv 和 HTML 报告都在。
- 根因：`topout.py:647-657`(report_for_topout) 只过滤 G.report 的 logic/mux 表，register 根从不出现；topout_fortest_rows 据它 → register 全丢。注意 topout_report 的 register 表缺 raw/writes/exp_num（直接换源会 KeyError）。
- 修法：对 register 根按 TopoutResult 走 compute_drives 算 raw+writes 组装表 dict 交 build_fortest_rows。

### M8. Topout 路径 logic_overrides（RTL 补充逻辑）整体丢失 → 真值表显示补充前旧逻辑（静默假绿）
- 症状：给 logic 根套 RTL 补充式后，Topout 真值表显示补充前旧逻辑（ECO 维度 5→3、少向量），.sv 不含补充式与注释；HTML supplement 列恒空。比「漏 ⚠」严重——**静默假绿**。
- 根因：build_for_topout/render_topout_sv/topout_report/topout_view_models/analyze_signal 签名全无 logic_overrides 形参。
- 修法：全部 topout API 加 logic_overrides 形参；analyze_signal LOGIC 根解析前经 _logic_with_overrides 替换合成信号。

## MINOR
- **m1**：Topout 路径无全局/批量负向（--neg-all/which=all/inc/固定错值），report n_neg 硬编码 0。
- **m2**：register/dft改名根的富报告表/HTML/for_test 漏 iddq 门输入行（编辑器已修）。`topout.py:928 _fill_register_model` 不读 result.dft_gate。
- **m3**：dft 改名根 for_test 回填用「源内部名」(d_en_vco_fc_fsm)而非顶层探针真名(d_en_vco_fc_ls)——designer 对不上、可能 CUVUNF。波及所有顶层名≠源名的信号（含纯 _ls 根）。
- **m4**：GUI 可编辑真值表/CSV 用 Excel 原始输入序，HTML/Excel/for_test 用 for_test 寄存器地址序——同信号两套行序（值按 key 绑不错，但人工核对/截图错位）。
- **m5**：owner_in_msg 对 register/dft改名根 .sv 断言消息静默丢失（半数 Topout 信号 log 里无 owner）。
- **m6**：GUI 新视图负向用裸 ~auto，跳过 designer_expected 防撞（designer 期望恰=~auto 时 NEG-BROKEN）。
- **m7**：topout_report 汇总 tab supplement 列永远空（真值表 tab suppbar 还在）。

## TRIVIAL
- **t1**：mux 根 .sv 块顶仍打「top_out=0 用裸名探针」噪声警告，与账目/报告刻意抑制不一致。

---

## 关键文件速查
- `topout.py`：analyze_signal MUX 分支 390-411 / build_for_topout 748-912 / _topout_probe_block 706-730 / _fill_register_model 928-946 / compose_topout_account 553-577 / topout_report 1086-1099 / report_for_topout 647-657 / topout_fortest_rows 590
- `pageviews.py`：analyze_logiclike 91-113 / _logic_table 159-175 / build_page_sv return 303-310
- `gui.py`：_norm_topout_result 457 / _norm_page_result 463-475 / _load_signal 1164-1185 / _e_addneg 1539-1553 / on_export_csv 2010-2028
- `cli.py`：cmd_topout 236-307 / cmd_page 310+ / _print_summary 1537-1562
- 对照基准 `generator.py`：build 1213-1281/1368-1371/1490-1508 / report 1727-1767/1823-1836/1892-1899
