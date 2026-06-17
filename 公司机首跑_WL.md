# 到公司先干什么 —— WL_RFTRX 表首跑（2026-06-04 交接）

> 昨天（本机）做完了 WL_RFTRX 表的 mux 全链路支持，三次提交都在 main 上，408 个测试全过。
> 这份是「到公司打开电脑第一件事」的操作单。每一步都给了**做什么 / 看什么 / 不对劲怎么办**。
> 跑出来的结果（尤其有疑问的）直接贴回下一个对话框，我接着改。

---

## 0. 先同步代码（1 分钟）

```bat
cd <Dreg_verify 仓库目录>
git pull
```

应该拉到三个新提交（`git log --oneline -3`）：
```
5eb338e 跳过可见性：内部节点不再静默过滤 + --account 完整账目
8186e7b mux top_out=0 改为默认生成裸名探针 + 警告（不再硬跳过）
89660bd WL_RFTRX mux 支持: 多控制拼接/控制·数据三来源/mux级联 + 对抗式审查5缺陷修复
```
没拉到 / 有冲突 → 停下贴给我。

---

## 1. 看一眼 270 组 mux 到底解析成什么样（最重要的一步）

**这一步回答你最关心的"有没有谁被悄悄漏掉"。** 用昨天新加的「完整账目」：

```bat
python -m dreg_verify.cli --excel "Hi1108V100_WL_RFTRX_C0C1_DREG_to_dig_95P.xlsx" --account --out-file account.txt
```

> ⚠ **别用 `> account.txt` 重定向**：PowerShell 在中文 Windows 上会拿 GBK 解 Python 的 UTF-8 输出，account.txt 会变乱码（`装载`→`瑁呰浇`）。用上面的 `--out-file`，由 Python 直接写 UTF-8(带 BOM)，记事本/Excel 都能正常打开。

打开 `account.txt`，**先看顶部那行小结**，比如：
```
完整账目：N 个 logic 信号 + 270 个 mux 组，逐个去向（一个不漏）
  小结：生成 X | 生成(裸名探针) Y | 跳过 Z | 过滤 W
```

| 看到 | 含义 | 要不要管 |
|---|---|---|
| **生成 / 生成(裸名探针)** | 正常，已经能出 .sv（裸名探针=top_out=0，正常） | ✅ 不用管 |
| **跳过 Z**（Z 比较大） | 有一批组没生成——**这是要重点看的** | ⬇ 见第 2 步 |
| **过滤 W** | 被 --owner/--type 等滤掉，或 logic 内部节点 | 一般不用管 |

> 昨天专门改了：**没有任何东西是"消失了不告诉你"的**——每个跳过/过滤都在 `account.txt` 里有名字+原因。

---

## 2. 如果有"跳过"——分清是「工具帮你抓 Excel 错」还是「工具在保护你」

在 `account.txt` 里搜 `跳过`，看每行末尾的原因。对照下表（原因里的关键词）：

| 原因关键词 | 是哪类 | 你该做什么 |
|---|---|---|
| `控制信号…在 logic 页找不到` / `case 值位宽…不一致` / `续行控制列…不一致` / `无可透传路径` / `数据输入…未解析` | **工具替你抓到 Excel 的问题**（丁类） | 这正是你想知道的——核对那几行 Excel；**把这些行贴给我**，多半要么是真表有我没料到的写法、要么 Excel 真有错 |
| `互异值…装不下…假绿` | **工具在保护你**（丙类） | 这组数据字段太窄，硬生成出来是"RTL 坏了也PASS"的假测试——跳过是对的；要验得加宽字段或拆组 |
| `需要探针前缀…force…衔接网…子模块内` | **force 子模块网没前缀**（丙类） | 见第 4 步 scan_rtl；或先放着 |
| `成环` / `深度超过上限` | mux 套 mux 级联异常 | 贴给我，看是真表结构还是我的展开逻辑要调 |

**重点**：跳过 ≠ "出毛病了你不知道"。丁类是工具替你逮 bug，丙类是工具替你挡坑——两种都已经把名字+原因写给你了。**真正要我介入的是丁类和"成环/深度"这两种**（可能是真表有 fixture 没覆盖的形态）。

---

## 3. 直接生成 .sv（默认就能出，不用先跑 scan_rtl）

```bat
python -m dreg_verify.cli --excel "Hi1108V100_WL_RFTRX_C0C1_DREG_to_dig_95P.xlsx" --out wr_rf_tc.sv
```

**昨天的关键改动**：WL 输出全部 top_out=0，但工具**不再**因此强制你先跑 scan_rtl。它默认照常生成**裸名探针** `` `ENV_RF.<输出名> ``（和 LPBT 一样），每个这种块顶上留一行 `// ⚠ …若 CUVUNF 跑 scan_rtl…`。命令末尾会汇总：
```
⚠ N 个 mux 组用裸名探针生成（输出 top_out=0，非芯片顶层输出）
```
这是**正常的**，不是错误。

> 也可以开 GUI 看真值表：`python -m dreg_verify.gui "…95P.xlsx"`。mux 组现在都能渲染出 case 表；状态列「裸名探针」(蓝)=已生成、「需探针前缀」(橙)=force 网缺前缀真阻断、「✗未解析」(红)=表有问题。

---

## 4. 拿 `wr_rf_tc.sv` 去仿真 —— 这才是真正的判官

把 .sv 放进你的 UVM 环境跑 elaboration + 仿真：

- **elaboration 通过、跑起来了** → 太好了，看 assert 结果（`assert_mux<N>_T<n>` 的 UVM_ERROR=真问题；带 `_NEG` 的是故意反例，正常）。
- **报 CUVUNF（找不到 net）** → 说明那个输出/输入确实埋在子模块里。这时才需要 scan_rtl：
  ```bat
  :: ① 本机导出要定位的网
  python redzone_tools/scan_rtl.py --excel "…95P.xlsx" --export-nets nets.txt
  :: ② nets.txt + redzone_tools/scan_rtl.py 传到仿真服务器，source dreg 环境后：
  python3 scan_rtl.py
  :: ③ 把生成的 probe_prefixes.txt 拷回本机，重新生成：
  python -m dreg_verify.cli --excel "…95P.xlsx" --probe-prefix-file probe_prefixes.txt --out wr_rf_tc.sv
  ```
  （详细两段式见 `scan_rtl使用说明.md`；mux 场景见 `mux验证说明.md` 第 6.4 节。）

---

## 5. 把结果贴回来给我

按你方便，挑要紧的贴：
1. **`account.txt` 的顶部小结那几行**（生成/跳过/过滤各多少）——我一眼就知道 270 组整体健不健康。
2. **任何"跳过"且原因是丁类（控制找不到/位宽不一致/续行冲突…）或"成环/深度"的行**——这些最可能要我改代码。
3. **仿真 log**：elaboration 过没过 / 有没有 CUVUNF / assert 的 UVM_ERROR（区分真问题 vs `_NEG` 反例）。

---

## 附：昨天做了什么（背景，不用细看）

1. **WL mux 支持**：多控制拼接、控制/数据三来源、mux 套 mux 级联自动展开。对抗式审查抓出并修了 5 个真缺陷（2 个 critical）。
2. **top_out=0 改默认生成**：你说"凭 top_out=0 就强制要前缀不自在"——改成默认裸名生成+警告，前缀按需后补。
3. **跳过可见性**：你说"不喜欢被跳过、怕看不见"——加了 `--account` 完整账目 + 内部节点过滤也列名字。

> LPBT 那张表的行为**一个字节都没动**（现有测试全保）——这些只对 WL 形态生效。
