# redzone_tools/ —— 要传到红区 / 仿真服务器的脚本

这个文件夹里的脚本【单文件、零第三方依赖（纯 Python 标准库）】，专门拷到 RTL 所在的
**红区 / 仿真服务器**上跑。需要上传时，把整夹子里的 `.py` 一起带过去即可。

> 红区约束：数据【进】红区不限、【出】红区基本不行。所以这些脚本都设计成
> **在红区里跑、结果在屏幕上看、不往外导数据**。

## 脚本

### `scan_rtl.py` —— RTL 层级扫描 → 探针前缀
静态扫一遍 RTL，把 Excel 每个信号的层级一次找全，免去「生成→仿真→CUVUNF→grep→配前缀」试错循环。
- **公司机**（有 dreg_verify + openpyxl）抽信号清单：
  `python redzone_tools/scan_rtl.py --excel 真表.xlsx --export-nets nets.txt`
- **红区**（只要 python3）扫 RTL（已 source dreg 环境时零参数）：
  `python3 scan_rtl.py`
- 完整用法见文件头 / 仓库根 `scan_rtl使用说明.md`。

### `diag_rtl_binding.py` —— 红区只读诊断（goal-redzone-binder 里程碑 M1）
拿工具导出的 `claims.json` + 真 RTL，逐探针判 **OUTPUT / INPUT-suspect（探到自己输入=假绿） / UNKNOWN**，
疑似探错时给真输出建议。**纯只读、不改不导出、不联网**。import 同目录的 `scan_rtl.py`。
- **公司机**生成 claims：
  `python -m dreg_verify.cli --excel 真表.xlsx --include-risky --export-claims claims.json`
- **红区**诊断（已 source dreg 环境时 RTL 参数自动推断）：
  `python3 diag_rtl_binding.py --claims claims.json`
  - 信号多时：`--out report.txt` 把完整报告写文件（留红区，grep/翻页用）；`--only suspect,unknown` 只看要处理的。
  - 报告摘要置顶，问题项（INPUT-suspect / UNKNOWN）详列、OUTPUT 仅列名；每个问题项给出 RTL 同基名真实网当线索。

### `diag_rtl_gui.py` —— 红区只读诊断 GUI（goal-redzone-binder 里程碑 M2）
把 M1 的文本诊断做成 **tkinter 桌面视图**：探针列表（按判定上色）+ 详情面板。点一根探针就把
「裸名在不在 RTL、真网带什么尾缀、由哪条 `assign` 驱动、候选有哪些、配的前缀对不对」**摊开**，
帮你逐节肉眼核对那批 UNKNOWN 到底该不该改探。**只读、零外发**（filedialog 仅读 claims，复制只进剪贴板；
无任何写 .sv / 导出 / 联网控件）。import 同目录的 `diag_rtl_binding`（后者再 import `scan_rtl`）。
- **红区**（有桌面、tkinter 是标准库）：`python3 diag_rtl_gui.py`，打开后点『打开 claims…』选 `claims.json`；
  RTL 默认走 dreg 环境自动推断，也可在界面里手填 top / rtl-dirs。也可 `--claims claims.json` 启动即加载。
- 兼容 py3.7+；红区 Linux 默认 UTF-8，窗口内文本走 tk 渲染不受控制台编码影响。

## 注意
- **三个脚本一起上传**（`diag_rtl_gui.py` → `diag_rtl_binding.py` → `scan_rtl.py` 依次 import，须同目录）。
  只跑命令行诊断时带前两个即可；要 GUI 就三个都带。
- **保持零第三方依赖**（`tests/test_rtl_scan.py::test_scan_rtl_is_stdlib_only` +
  `tests/test_diag_rtl_gui.py::test_diag_rtl_gui_is_stdlib_only` 守着）——别在这里 import openpyxl/numpy 等。
- 包内 `dreg_verify/rtl_scan.py` 会自动把本文件夹加进 `sys.path` 复用 `scan_rtl` 的解析，公司机侧无需手动配。
