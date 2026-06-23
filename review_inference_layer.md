# dreg_verify「RTL 身份推断层」全面 Review 报告

> ultracode 多智能体审计：6 维度并行审码 → 每条发现派独立怀疑者对抗式验证 → 综合分级 + 完整性批判。
> 规模：59 个 agent，~4.7M token，860 次工具调用。51 条发现 → **37 确认 / 3 存疑 / 11 被证伪**。
> 日期 2026-06-16，HEAD `7b0f34b`。本报告不是代码改动，是给你定优先级用的。

---

## 一句话结论

你这 4 个问题（兼容性 / 尾缀 / cone / 耦合）几乎全部收束到**同一条总根**：

> **工具从来不"知道"RTL 真网名，全靠命名约定 + cone 展开去猜；而一旦配了探针前缀，
> `resolver.py:364` 就把"配了前缀字符串"无条件等同于"这根网真实存在且语义正确"——零校验。**

scan_rtl 其实**已经算出**了真网名集合和"查无此网"清单，但这份"裁判信息"在生成阶段被**整个丢弃**，
只拿去给 force 网拼前缀。这就是 R32/R34/R38/R40 四轮假绿反复踩坑的唯一系统性堵口，至今没做。

**最该先动手的 3 件事**（按"危险 × 便宜"排序）：
1. **尾缀：补 mux 那一侧的中心闸门**——R40 你只封了 logic 侧，mux 输出尾缀（`excel_model.py:991/993`）是**裸赋值、没过闸门**，`append_to_mux=True` 时就是 R40 的 mux 镜像翻版（假绿）。**这直接回答你的 Q2「为什么尾缀老出问题」。**
2. **生成时加一条不变量断言**：探针网名 ≠ 自己任何一条输入网 → 当场块顶⚠。把事后审计脚本固化进管线，**永不再靠下一轮人肉 debug**。
3. **几个跨表脆弱点的便宜止血**（tmm 重名无告警 / 换表级联泄漏 / 输出位宽漏标截位）。

**治本路径**（scan_rtl 真 nets 当裁判）：**值得做、但工作量大（large）**，分两阶段、第一阶段低风险可单独先上。

---

## 总根：一张图

```
Excel 真表 ──read_*──► 命名约定推断网名 (rtl_net_name / ref_suffix / _ls)
                              │
                              ▼
                        探针 / force 网名（纯字符串拼出来的）
                              │
        ┌─────────────────────┴─────────────────────┐
        │ 配了探针前缀？                              │ 没配？
        ▼                                            ▼
  resolver.py:364                              needs-prefix 风险
  "prefixed-wire 提升"                          (默认跳过，可见)
  = 无条件信任，清零所有风险/告警  ◄── 假绿放大器①
        │
        ▼
  生成 .sv：assert(ENV_RF.<prefix>.<net> == 期望)
        │
   ┌────┴────────────────────────────┐
   │ 网真实存在但语义错（=自己输入网）  │ 网不存在
   ▼                                  ▼
  静默"通过" = 假绿 ◄── 最致命       CUVUNF（看得见，浪费一轮）

  ※ scan_rtl 算出的【真 nets + missing 清单】从未喂进这条链做裁判 ◄── 堵口未做
```

---

## 你问的 4 个问题 —— 逐个回答

### Q1 · 如何让脚本在不同 Excel 里兼容性更好？

**真相**：大部分硬编码假设其实被"TMM 优先 / risky-skip 默认跳过 / 约定不变量"兜住了，换表多半是
**可见的报错或缺失**，不是假绿。真正危险的少数几个：

| 假设 | 在哪 | 换表怎么破 | 危险度 |
|---|---|---|---|
| 配了前缀=网存在且正确 | `resolver.py:361-366` | 大表 1716 条前缀，RTL 改版后必有失效前缀指向同名异处网 → **假绿** | 🔴 Critical |
| tmm 同名字段 first-wins 但**无任何重名告警** | `excel_model.py:805` | 比 regmap（R35 已修）更危险——tmm 是首选地址源，撞了悄无声息 | 🟠 Major |
| 地址进制靠字面猜（纯数字当 hex、裸 `d` 当 13） | `excel_model.py:387-391` | regmap H 列写不带前缀的十进制（`13`→当成 0x13=19）→ 静默写错地址 | 🟠 Major |
| 表头恒在第 2 行（mux/dft/level_shift 调用处根本没传 header_row） | `excel_model.py:418/629/...`，`load_workbook:1063` | 模板多/少一行说明 → 整页错读（多半可见） | 🟡 Minor |
| regmap 固定 16-bit（J..Y=bit15..0），RF_WRITE 恒 16'h | `excel_model.py:728-745`，`sv_writer.py:36` | 换 32 位寄存器表 → bit16-31 字段读不到 + 写值截断（多半可见写错） | 🟡 Minor |
| 缺页/页名换写法完全静默给空 | `_find_sheet` `excel_model.py:929` | `Register_Map` / `MUX_TABLE` 等改名 → 默默当"表里没有" | 🟡 Minor（观测性） |
| 类型用宽松子串判 RO/RW（含 R 即 RO、含 W 即 RW） | `excel_model.py:817`，`resolver.py:382` | `reserved/SRAM/Strobe` 判 RO、`RW1C` 判普通 RW、**WO 判 RW**（见盲区） | 🟡 Minor |

**结论**：兼容性的根治不是逐个补假设，而是 **Q 治本路径**——让 scan_rtl 真 nets 当裁判，把"换表破了某假设"
从"静默假绿"降级成"生成时可见的⚠"。便宜止血先做 tmm 重名告警 + 缺页/换表的一次性体检脚本。

---

### Q2 · 尾缀方案有什么风险？怎么设计更好？（★最该先修在这）

**为什么尾缀老出问题——结构根因找到了**：`ref_suffix` 的赋值点有 **4 处**，R40 只把其中 2 处收口进了
中心闸门 `_pick_ref_suffix`（不变量 `ref_suffix ∉ _self_ref_suffixes`）：

| 赋值点 | 文件 | 过闸门？ | 自引用排除？ |
|---|---|---|---|
| `read_logic` | `excel_model.py:495` | ✅ | ✅ |
| `_apply_mux_ref_suffix` | `excel_model.py:957` | ✅ | ✅ |
| **`_apply_mux_output_ref_suffix`** | **`excel_model.py:991/993`** | ❌ **裸赋值** | ❌ **无** |
| `make_supplement_signal` 继承 | `generator.py:272` | — | ❌ 不继承 `_self_ref_suffixes` |

> **SUF-2（Major，假绿 high，已确认）**：mux 输出尾缀是第三个赋值点，完全在 R40 闸门外。
> `MuxGroup` 连 `_self_ref_suffixes` 字段都没有。默认 `append_to_mux=False` 时退裸名是安全的，
> **但一旦 GUI 勾「mux 加尾缀」（R32 记过"Hi1108 勾 mux 即所有 rxiq 一次全补"）或单点 `suffix_override=True`**
> → 自引用 mux 输出探针 = 它自己的 `_to_mux` 输入前级网 = **R40 假绿的 mux 翻版**。
>
> 更尖锐：你那套"证明窟窿封死"的 `audit_self_ref_suffix.py` **只扫 `wb.logic`、从不看 `wb.mux`**
> ——【B】残留=0 这个"封死证据"，在 mux 输出路径上有**一模一样的盲区**。

其它两类尾缀风险：
- **SUF-3（Major，假绿 high）非自引用跨信号撞名**：X 的输出探针网 == 另一个**无关**信号 Y 的输入网
  （寄存器字段名和 logic 输出基名同名时）。Excel 里看不出、工具也不检测——这类**只能靠真 nets 当裁判**。
- **SUF-6（Minor，假绿 medium）`_ls_name` 短路压过一切**：`excel_model.py:79` level_shift 页 C 列
  一旦 SE 填错网名（填成移位前网/消费侧网），所有尾缀纠错（撞名退裸名、单点 override）全被短路，无法挽救。

**怎么设计更好（结论）**：
1. **短期（root-cause，便宜）**：把 `_pick_ref_suffix` 泛化成接受 LogicSignal/MuxGroup 任一的**统一闸门**；
   给 `MuxGroup` 加 `_self_ref_suffixes`；`make_supplement_signal` 继承它。**三个赋值点全过闸门**，
   不变量对 logic/mux/supplement 全成立。审计脚本【B】段扩到 `wb.mux`。
2. **长期（root-cause，贵）**：尾缀本质是"猜网名"，根治还是 **Q 真 nets 当裁判**——生成时校验
   探针网 ∈ 真 nets 且 ∉ 输入网集。三种方案可靠性排序：让 SE 在表里**显式标网名** > scan_rtl **真 nets 当裁判** > 现状**约定猜**。

---

### Q3 · Cone 展开有什么风险？

**好消息**：对抗层把 cone 相关的 5 条担忧（CONE-1/CONE-3/CONE-4 跨边界回退、cascade 共享状态、
append 写共享对象）**全部证伪**——机制属实但触发条件都已被现有 guard 挡住，**无可达假绿路径**。
cone 展开后输出探针网名不随展开改变（只换输入），这条路径本身是对的。所以 cone 整体**比你担心的健康**。

**真正确认的一个 cone 风险**（值得修）：

> **CONE-2（Major，假绿 high，已确认）**：`mux_expandable` 的"控制位宽 ≤ 2"门槛**只看声明的控制字段**。
> 展开链里若有更宽的寄存器叶子，`generate_vectors` 只取 `{0, 全1}` 两个端点 → **中间的 case 永远不被驱动**（假绿）。

而且**完整性批判抓到它有个更基础的孪生**（纯 logic、不经 mux，没被任何 finding 单列）：

> designer 在纯 logic 真表里用一个 ≥3bit 寄存器当三元选择器（如 4bit mode 选 8 选 1）极常见。
> `vectors._control_levels` 对宽切片控制**退化只测两个端点值** → 中间 6 个 mode 静默不验 = **最基础 logic 路径上的覆盖度黑洞**，
> 不依赖 cone/mux 任何机制。`A==3'd0?...` 这类一写就中。

次要：MAX_DEPTH=8 超深回退原因不透块顶（CONE-7，Minor）；mode=0 备用载体裸名 force（CONE-5，存疑）。

---

### Q4 · 功能之间有什么耦合风险？

**最大的耦合风险就是 Q1 的总根本身**——`prefixed-wire 提升`（`resolver.py:364`）是一个全局开关，
它把"配前缀"这一个动作的影响**静默扩散**到三处风险闸门（build risky-skip / mux_prefix_risks / analyze fallback），
配错一根前缀，本该亮的 needs-prefix 旗被一键压掉。

其它确认的耦合：
- **CONE-4（Major，假绿 low）换表时 `_sig_cascade`（单点级联档）未清空** → 上一张表的单点级联**泄漏**到下一张表同名信号。
  （和 R25 的单点覆盖度泄漏、R28 的 `_mux_user_vecs` 换表泄漏同一类毛病——"会话状态该清没清"。）
- **GATE-2（Major，假绿 medium）stale 文件无版本指纹**：nets.txt / prefix 文件 / Excel / RTL 四者之间
  **没有任何版本绑定校验**，`_infer_from_dreg_env` 还会自动捡当前目录的旧 nets.txt。配合 `resolver.py:364`
  的无条件信任，一份过期前缀就能静默压掉本该亮的旗。

**会话状态持久化**（`~/.dreg_verify_gui.json`：probe_prefixes / cascade / suffix_override）是这类泄漏的温床——
R25 教训"磁盘旧状态被静默恢复盖掉全局"在这里依然成立。建议审一遍：哪些该是会话临时档却被持久化、或反之。

---

## 你没问、但更该怕的（完整性批判抓的盲区，9 条）

这些**不在任何一轮 memory 里**，是这次新挖出来的，且有几条是**最基础的位宽/写值假绿**，比命名约定更底层：

1. **🔴 输出位宽漏标 → 断言只验最低 1 位**（最该查）。K 列若漏写 `[7:0]`，`out_width` 默认=1，
   `auto_out` 被截成 1bit，断言变成 `==1'b<值>` → **几乎所有高位选路故障静默通过**。
   根因极隐蔽（少写个位宽标注），报告里宽度看着也"正常"（就是 1）。换 SE 习惯极易触发。
2. **🟠 RW 字段写值静默截断**。`sv_writer.compute_drives` 的 `(val<<slice_lsb) & mask(fw)`，
   当输入声明位宽 > 字段容量且有切片偏移时，高位被裁掉、无 warning，且同样的截断会同步进 for_test 回填，
   designer 对照不出来 → 写进寄存器的值 ≠ 测试意图。
3. **🟠 WO（只写）寄存器被判 RW** → 工具写进去后立刻 assert 读回，而 WO 读回是垃圾/0 → 选路验证假绿。
   `XCOMP-7` 只查了反方向（含 R 误判 RO），WO 这边没人审。
4. **🟠 负向用例 × DFT 门的交互**。`make_negative` 新建的向量 `extra_forces=[]` 不继承门透传 force，
   被 IDDQ 门控的输出，其负向断言可能在**非透传门态**下比较 → 负向自检要么恒 PASS（NEG-BROKEN，
   你以为有自检其实没有），要么恒 error 污染 UVM_ERROR 计数。**这是"自检机制自身的假绿"。**
5. **🟠 诊断器 vs 生产 loader 漂移**。`inspect_mux.py` 有 `detect_header_row`（稳健探测），
   而 `load_workbook` 写死 header_row=2。**你用 inspect 工具"核对过表结构没问题"≠ 生成时真读对了**——假的安全感。
6. expr 算术式无符号回绕/除零静默取 0（减法、移位组合）→ auto_out 系统性偏差（R39 增益/trim 重灾区的求值侧隐患）。
7. 负向错值防撞只避开 auto_out/designer_expected，不避 mux 另一 case 的合法值/DFT 常量 → 负向可能假 PASS。
8. mux 手编向量/手填数据（`mux_user_vecs`/`mux_data`）与 cone 跨边界展开**两条路径互不读对方** →
   用户手编的 mux 列在"该 mux 被当作另一信号输入展开"时被静默忽略，验的不是一回事。
9. 所有跨表脆弱点缺一个"一次性体检"入口（建议收口成单个 `audit_table_health.py`）。

---

## 治本路径：scan_rtl 真 nets 当裁判（值不值得做）

**结论：值得做，是四轮假绿同根的唯一系统性堵口。工作量 large，但分两阶段、第一阶段低风险可单独先上。**

现状 = scan_rtl 已经算出真网名集合 + missing 清单，但生成期**整体丢弃**（只用作 force 前缀映射）。
`render_prefix_file` 把 missing 只写成 `#` 注释，`parse` 又 `if startswith('#'): continue` 丢掉——**missing 在导回时蒸发**。

**阶段一（存在性校验，低风险高回报，可单独先上）**：
- scan_rtl 把 missing 写成**可解析负向条目**（如 `!net` 或 `[missing]` 段），parse 收进 `GenOptions.known_missing_nets`。
- nets.txt / prefix 文件头部写入**源 Excel 文件名 + sheet 指纹 + 时间**（治 GATE-2 stale）。
- build 时探针/force 网基名 ∈ `known_missing_nets` → **即便配了前缀也降级为 needs-prefix 风险 + 块顶⚠**。
- 加 `GenOptions.rtl_nets`，断言每个探针/force 网 ∈ rtl_nets，违规标 `net-not-in-rtl` 默认跳过 + ⚠。
- **降级安全**：无 rtl_nets 时逐字节不变（opt-in），不破坏 LPBT/WL 现有产物。

**阶段二（输入网识别，配独立审计验证后再上）**：
- scan_rtl 加轻量 `assign` 扫描（正则抽 LHS=driven、RHS=read-only），导出 `rtl_input_nets`。
- build 断言**输出探针 ∉ rtl_input_nets** → **直接堵 R40 自引用类**（探针读回自己输入）假绿。

**Caveats**：阶段二的 assign 扫描最易做错（跨行/拼接/generate 块/层次例化重名），必须先出独立诊断脚本
在公司机对真 RTL 验证分类正确率再上，不可与阶段一捆绑；rtl_nets 仍依赖 scan_rtl 解析正确性 + 新鲜度（配 GATE-2 指纹）。

---

## 散落规则（该收口成中心不变量的，5 条）

| 规矩 | 散在 | 收口成 |
|---|---|---|
| 自引用抑制不变量 | read_logic ✅ / _apply_mux_ref_suffix ✅ / **_apply_mux_output_ref_suffix ❌** / supplement ❌ | 泛化 `_pick_ref_suffix` 统一闸门 + MuxGroup 加 `_self_ref_suffixes` |
| 生成期探针 ≠ 输入网 | 只在离线 audit 脚本 / 赋值期被动满足 | build 里加生成期主动断言（probe ∉ ro_inputs）|
| 同名 first-wins + 重名告警 | regmap ✅有告警 / **tmm ❌无告警** / VLOOKUP | 抽 `_field_dup_warning(table_tag)` 同覆盖 tmm+regmap |
| 剥接线后缀（_to_logic/_to_mux/_to_dft）| 6~8 处各自 endswith | `WIRE_SUFFIXES` 单一来源 + `strip_wire_suffix()` |
| 网名可信裁判（存在性+语义）| resolver:364 / rtl_net_name / _ls 短路 / mux_output_warning / scan_rtl 不消费 | **GATE-1 真 nets 裁判层**（总根收口）|

---

## 建议的行动顺序

**先止血（small，安全，可立刻做）**：
1. SUF-2：mux 输出尾缀过中心闸门 + MuxGroup 加 `_self_ref_suffixes` + audit 扩到 wb.mux。
2. FG-2：build 加生成期不变量「探针 ≠ 自己输入网」→ 块顶⚠（不依赖 rtl_nets，纯 Excel 侧兜底）。
3. XCOMP-3：read_tmm 加重名告警通道。CONE-4：换表清 `_sig_cascade`。
4. 完整性批判 #1（输出位宽漏标截位）：build 比对 out_width vs 表达式自决宽，疑似漏标 → ⚠。

**再治本（large，分阶段）**：5. GATE 阶段一（存在性 + 指纹）→ 6. GATE 阶段二（输入网识别）。

**别急的**：cone 那几条（多被证伪，健康）；散落规则里纯预防性的剥后缀收口。

---

## 需要你在公司机跑的只读诊断脚本（确认真表里到底有没有这些坑）

很多结论需要真数据证实（.xlsx 你给不了）。下面这些我可以**现在就写**，你拷到公司机跑（csh/tcsh，已处理 GBK）：

| 脚本 | 查什么 | 对应发现 | 优先 |
|---|---|---|---|
| `audit_mux_self_ref.py` | 枚举自引用 mux 组，看 `append_to_mux=True` 下探针是否=自己输入网 | SUF-2 | 🔴 高 |
| `audit_output_width.py` | 每个 logic 信号 out_width vs 表达式自决宽，列出疑似漏标位宽（断言截位假绿）| 批判#1 | 🔴 高 |
| `audit_prefix_vs_nets.py` | 全部 prefixed-wire 网 × 最新 nets.txt 求差集，暴露 stale/写错前缀 | resolver:364 | 🔴 高 |
| `audit_addr_consistency.py` | tmm vs regmap 同名字段地址差异 + tmm 页内同名计数 | XCOMP-3 | 🟠 中 |
| `audit_rw_field_truncation.py` | 列出 width+slice_lsb > 字段容量 的 RW 输入（写值静默截断）| 批判#2 | 🟠 中 |
| `audit_table_health.py` | 一次报告 header 行 / 缺页差集 / 进制采样 / 类型字面值 / 重名 | XCOMP-1~11 | 🟠 中 |

---

*（11 条被对抗层证伪的发现已剔除，主要是 cone 跨边界/共享状态类——机制属实但触发条件已被现有 guard 挡住，无可达假绿。这恰好说明 cone 子系统比预想健康。）*
