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

## 注意
- **两个脚本一起上传**（`diag_rtl_binding.py` import `scan_rtl.py`，须同目录）。
- **保持零第三方依赖**（`tests/test_rtl_scan.py::test_scan_rtl_is_stdlib_only` 守着）——别在这里 import openpyxl 等。
- 包内 `dreg_verify/rtl_scan.py` 会自动把本文件夹加进 `sys.path` 复用 `scan_rtl` 的解析，公司机侧无需手动配。
