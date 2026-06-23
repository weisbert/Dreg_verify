# Topout-rooted 重构 — 终局 /goal prompt

> 用法：在 Claude Code 里粘下面 ``` 里整段(含 `/goal` 开头)。这是【终端可用 end-state】goal——
> 一口气做到 GUI 能用为止，scope 已替你定成 additive 两标签，不中途停问。
> 数据层引擎 dreg_verify/topout.py 已建好(HEAD 含 bdd1f31..97ab6a2)，本 goal 把它接进 GUI/CLI。
> 验证夹具 = mirror_btlp_dreg.xlsx(7 条真族金标准)；真表明早自己载。

```
/goal 把 Dreg「Topout-rooted」验证做成【从 GUI 端到端可用】的终局：打开 GUI、载入 Excel(真表或 mirror)、
看到 Topout 要验信号清单、逐个看它的跨页 cone 展开+真值表、能生成/导出 .sv 上传验证、能导出报告。
数据层引擎 dreg_verify/topout.py 已建好(read_topout/resolve_root/analyze_signal/analyze_all/
report_for_topout/compose_topout_account/topout_fortest_rows)——本 goal 把它【接进 GUI 和 CLI】，明早直接能用。
⚠ 一口气做到可用为止：不拆成等我拍板的小步、不中途停下问我、不给任何文件设禁区。每完成一块且测试全过就 git commit。最多 200 轮。

## 工作方式(按需开 agent 工作流，别单线程闷头干)
- 适合就开：①并行探查(铺开摸 gui.py/cli.py/sv_writer 结构)；②分头实现互不依赖的模块；
  ③每块写完用【对抗 review 工作流】(多维度找bug→验证→批判)再 commit。
- GUI 改完【必须截图自验】：QT_QPA_PLATFORM=offscreen + MainWindow().on_load(mirror) + w.grab().save(png)，
  用 .venv/Scripts/python.exe(有 PySide6)，Read 那张 png 过目版面；文字内容另用程序断言 widget.text()
  (offscreen 缺中文字体、文字会成方框，所以版面靠截图、文字靠断言)。

## 已替你定好的方向(不要再停下来问)
- scope = 两标签(这就是重构主线，不是凑数)：Topout 视图 = 【主视图/默认入口】；
  现有 logic/mux 视图【降级】成一个『排查(旧)』分页——【保留不删】(它正是 per-page 排查/隔离测试工具，
  前缀/后缀/级联那套住这儿)，但不再是默认门面。「保留」≠「当默认」：Topout 上位、旧的退二线。
- Topout 视图里【不放】对它没意义的控件(级联force / 尾缀开关 / top_output 筛)——那些只属于『排查(旧)』分页。
- 「不改旧行为」只指：降级后的『排查(旧)』分页 + 旧 CLI 仍【功能正确、其测试不破】——不是把旧的冻成门面。
  GUI 默认即 Topout；旧 CLI 默认保留 + 新增 --topout。
- 前缀/后缀机制保留(降级逃生阀，住排查分页)。验证用 mirror_btlp_dreg.xlsx(7 条真族金标准)；真表明早用户自己载。
- UI 取向拿不准就用最朴素可用的，别卡住。

## 范围(GUI 在内，没有禁区)：可改 dreg_verify/ 任何文件(含 gui.py/cli.py/sv_writer.py)，前提见护栏。新增为主。

## 护栏(只有这三条；别再自设禁区或决策停)
1. 现有 674 测试全程绿(降级/改默认导致个别 GUI 测试要随之更新=可以，但【只准改断言对齐新结构，不准把测试掏空/弱化】，
   且核心断言要更强不能更弱)；降级后的『排查(旧)』分页 + 旧 CLI 功能保持正确(它是降级保留的排查工具，不是被冻住的门面)。
2. 每块新功能带测试：GUI 用无头 Qt(offscreen，仿 tests/test_mux_wl_gui.py) + 关键版面截图过目。
3. 解析不了的 Topout 信号在 GUI/报告里优雅记账(显示分类/原因)，绝不崩、不静默丢。

## 遇阻(唯一允许停的情形)
- 只有「不拿到真实 Topout 完整 dump 就物理上没法继续」才停——但 mirror 已够建+测全部 GUI/CLI 功能，基本不该停。
- 其它一律自己用 additive 默认推进，别停下来等我。

## Definition of Done(= 明早打开就能用)
1. GUI Topout 视图(新 tab/视图，旧视图不动)：载入 Excel 后列出 Topout 要验信号 + 分类
   (选路/mux/直连寄存器/RO跳过/未解析)；选中一个→显示【跨页 cone 展开链(缩进+页标签)+真值表+账目状态】。
2. GUI 出 .sv：能为(全部或勾选的)Topout 信号生成 .sv 预览 + 导出文件(report_for_topout→sv 写出)。
3. GUI 出报告：能导出 Topout 报告/账目(→HTML/CSV)，堵 3 静默陷阱(默认不空/不刷 top_out=0 假警告/回填含 mux)。
4. CLI 入口：新增 --topout 跑 Topout-rooted 路径(列信号/出 .sv/出报告)，headless 可脚本化。
5. 无头 Qt 测试 + 截图覆盖 1-4 关键路径(载 mirror→Topout 视图列出 12 信号→选 rx_en 真值表对上金标准→生成含断言的 .sv→截一张 Topout 视图 png)。
6. 全程 674+新增测试绿；旧视图/CLI 逐字节不变。

## 最终交付
- 一句话总结 + 【怎么用】：明早打开 GUI 点哪个 tab、怎么载表、怎么看 Topout 信号、怎么出 .sv。附 Topout 视图截图。
- 若有 xfail，列清原因(别拿"等 scope/等真表"当借口停——已替你定/已有 mirror)。
```
