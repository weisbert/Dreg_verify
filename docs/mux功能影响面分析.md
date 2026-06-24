# mux 功能影响面分析（2026-06-03，写代码前的全程序体检）

> 由 8 个模块分析 + 1 轮完整性交叉检查产出。实现前必读；实现顺序见末节。

## 一、数据流总览：mux 信号要打通的链路

```
Excel mux 页 ──read_mux()──> wb.mux ──select_mux_groups()──> make_mux_vectors()
                                │                                  │
                                │  resolver: 数据寄存器→RF_WRITE 地址  │  控制信号→line/local 双路径驱动
                                ▼                                  ▼
                          render_mux_block() ──> .sv (assert_mux<N>_T<n>)
                                │
                                ├──> HTML/CSV 报告（case 选择表）
                                └──> GUI（信号表 + 真值表编辑器 + 覆盖度）
```

**核心架构决定（所有模块分析一致推荐）：mux 走独立通道，不混入 logic 的数据结构。**
- `wb.mux`（list[MuxGroup]）与 `wb.logic` 平行，不把 mux 塞进 LogicSignal
- `make_mux_vectors()` / `render_mux_block()` 与 logic 的同名机制并列，不强行复用表达式路径
- 理由：logic 路径处处假设"有表达式 AST"（expand_signal 会 E.parse(sig.expr) 直接抛错）；
  且 5 处测试硬编码计数（test_e2e:27/185、test_testitems:402/506/672）只在 mux 独立通道时不破

## 二、各模块改动清单

| 模块 | 改动 | 工作量 |
|------|------|--------|
| **excel_model.py** | 新增 MuxGroup/MuxCase 类、read_mux()（表头第2行，按 N 列分组）、strip_to_mux()、DregWorkbook.mux 字段（默认 []，向后兼容） | 中 |
| **expr.py** | 新增 parse_case_literal()：解析 `4'b000x` 返回 (value, width, **dontcare_mask**)。不动现有 AST/求值器 | 小 |
| **vectors.py** | 新增 make_mux_vectors()：每 case 一组 T（x 位按覆盖度展开）+ 数据寄存器互异值分配 + local 路径尾 T | 大 |
| **generator.py** | select_mux_groups() + build()/report() **双轨同步**加 mux 段 + assert 标号唯一性检查纳入 mux + GenOptions 加开关 | 大 |
| **resolver.py** | mux 输出索引 + 数据寄存器（剥 _to_mux 后查表）解析；控制信号识别为 logic to_mux 输出 | 中 |
| **cone.py / 驱动** | ⭐控制信号双路径驱动：line=force 线控+模式位 / local=RF_WRITE 本地寄存器+模式位。cone 只会正向代入不能反解——需要新的"目标值→输入赋值"配方逻辑 | 大 |
| **sv_writer.py** | 新增 render_mux_block()（并列、不改 render_signal_block）；aid='mux%d'；LHS 用 G 列名直探不走 rtl_net_name；反例/owner/汇总计数器机制直接复用 | 大 |
| **cli.py** | --include-mux/--mux-only 旗标 + cmd_list/cmd_diagnose 区分来源 + 文案去"logic 专属"硬编码 | 中 |
| **cli.py HTML 报告** | mux 真值表 = **case 选择表**（行=case 值→选中寄存器，列=测试 T），report() tables 加 kind='mux' 标记分支渲染；可验证性加"数据寄存器解析失败=选路不可验证"档；头部统计 logic X / mux Y | 中 |
| **gui.py** | 11 处：信号表混入（type 列显示 mux）、真值表编辑器 case 分支、覆盖度分流、输入信号表（控制+数据寄存器角色）、导出选项、_expand_sig 防 ExprError 静默 | 大 |
| **tests/** | 新建 test_mux.py + fixtures.build_workbook 加 with_mux=False 开关 + make_sample_excel 加 mux 页 | 中 |
| **文档** | README、级联模式说明.md 补 mux、新建 mux验证说明.md、GUI tooltip/帮助按钮 | 小 |

## 三、HTML 报告具体怎么变（用户问的）

1. **不加新标签页**——mux 信号融入现有 ①汇总 ②真值表 ③明细 ④可验证性 四个标签页
2. **真值表形态变了**：logic 是"输入行 × 测试列 + 表达式行"；mux 是 **case 选择表**：
   ```
   行 = 每个 case：控制值(3'b010) → 被选中的寄存器(rccal_i_G1) → 期望输出
   列 = 测试 T（含 don't-care 位展开的 T、local 路径 T）
   ```
3. **可验证性新增判定**：mux 某条数据路 RF_WRITE 地址解析失败 → "选路不可验证"（现有四档判不出来，会假绿）
4. 搜索/owner 过滤/负向过滤对 mux 信号自动生效（数据结构对齐即可）
5. 头部统计行：`logic 信号 X 个 / mux 信号 Y 个`

## 四、十大深坑（实现时逐条防）

1. ⭐ **parse_based_literal 把 x 吞成 0**（expr.py:117 `re.sub([xXzZ?],'0')`）——直接用它解析 `4'b000x` 会丢 don't-care 信息且与其它 case 撞值。必须新写保留 x mask 的解析器。
2. ⭐ **控制信号 line/local 双路径 = per-vector 切换驱动来源**——现有 compute_drives/Resolver 是 per-binding 固定（一个信号一种驱动）。这是最大的结构性改动：同一控制信号，扫 case 的 T 用 force 线控，最后一个 T 用 RF_WRITE 本地寄存器。
3. ⭐ **数据寄存器互异值会被"同地址合并+字段位宽裁剪"破坏**（sv_writer.py:126-139）——两条数据总线同地址不同字段时，窄字段互异值可能被裁成相同低位 → 选错路也测不出 = 假绿。互异值分配必须按"裁剪后仍互异"设计。
4. **build()/report() 双轨必须同步**——两者独立遍历信号，已有 meta 形状不一致的前科（build 有 truncated 键 report 没有）。mux 段两边都要加，否则报告和 .sv 对不上。
5. **--neg-file separate 模式双 build**——mux 互异值必须纯确定性（不能依赖集合迭代顺序），否则正向文件和负向文件的驱动值对不上。
6. **Resolver._match 模糊匹配误判**（resolver.py:130-143）——mux 基名 `d_bt_lp_lpf_bias_i_t1` 与 tmm 里相近字段名可能触发"歧义→UNKNOWN"→ 静默跳过。
7. **regmap last-wins vs tmm first-wins**——同名字段两表取舍方向相反，mux 数据寄存器查表可能拿错地址（能 elaborate 但写错寄存器 = 假绿）。
8. **read_only 行 tuple 裁剪**——mux 页 232 列宽，N 列（组号）在右侧，行尾留空时 tuple 可能裁短 → 组号丢失归错组。_col 已防越界但要测试这个场景。
9. **probe_prefix 扁平 dict 串台**——mux 输出名与 logic 基名同 key 时前缀互相覆盖。
10. **GUI 定制导出链路**（make_vector_from_base_values，vectors.py:358）——用 E.evaluate 求期望，mux 没有表达式 → GUI 编辑过的 mux 信号导出时期望全错。mux 的 override 路径必须走自己的期望计算（按控制值选寄存器）。

## 五、可直接复用（不用动）

- **反例机制**（自检式 ==、NEG 标签、计数器）——只依赖 vec.is_negative/asserted_value，mux 向量直接接入
- **owner 尾巴**（OWNER_FMT/_ascii）——mux 信号暴露 .owner 属性即可
- **汇总块**（_wrap_with_summary）——stats 形状对齐自动计入；logic/mux 同文件时用 block_suffix 防命名块重名
- **驱动顺序规则**（先 RF_WRITE 按地址排序后 force）——compute_drives 的合并/排序逻辑复用
- **探针前缀机制**——MuxSignal 暴露四个名字属性后 _prefix_of 原样工作（注意坑 9）
- **RF_WRITE 地址解析**（resolver RW 路径 + tmm/regmap 查表）——数据寄存器剥 _to_mux 后直接用
- **scan_rtl 两段式**——已扩展完成（本轮 f4466de）
- **GUI 联动机制**（覆盖度切换重算/级联模式切换/设置持久化）——分流函数内部处理对即可

## 六、需要用户拍板的决定

| # | 问题 | 选项 | 推荐 |
|---|------|------|------|
| 1 | 默认行为：不带参数生成什么？ | A: logic+mux 都生成 / B: 仅 logic（旧行为），--include-mux 才出 mux | A（功能做了就该用上；GUI/CLI 都可关） |
| 2 | 产物组织：logic 和 mux 同一个 .sv？ | A: 同文件（designer 就是这样）/ B: 分文件 | A，summary 块用 block_suffix 区分 |
| 3 | 精简档 don't-care 位取值 | x=0 / x=1 / 随机 | x=0（确定性、可复现） |
| 4 | GUI mux 信号显示方式 | A: 与 logic 同表混排+type 列标"mux" / B: 独立标签页 | A（操作一致、改动小） |
| 5 | mux 反例的"错值" | A: 被选中寄存器值取反（与 logic 一致）/ B: 换成另一个寄存器的值 | A（B 在某些值组合下可能恰好合法） |

## 七、建议实现顺序（环境验证通过后）

1. **excel_model**：MuxGroup/read_mux/strip_to_mux/wb.mux + 单测（数据地基）
2. **expr**：parse_case_literal（x mask）+ 单测
3. **resolver**：数据寄存器解析 + 控制信号双路径驱动配方 + 单测（最难的在这，先攻坚）
4. **vectors**：make_mux_vectors（覆盖度三档 + 互异值 + local 尾 T）+ 单测
5. **generator + sv_writer**：build/report 双轨 + render_mux_block + 端到端单测（产出 .sv）
6. **CLI**：旗标 + HTML 报告 case 选择表
7. **GUI**：信号表/真值表编辑器/覆盖度
8. **文档**：README/mux验证说明.md
9. 每步跑全量测试；5 完成后先给用户出一份样例 .sv 人工核对，再继续 6-8
