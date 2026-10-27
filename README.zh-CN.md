# OHOS_DFT

[English](README.md) | [简体中文](README.zh-CN.md)

估计 OHOS/HarmonyOS hitrace 内核时钟与 Unix epoch 时间之间的偏移，用于将另一组标准时间戳数据对齐到 trace。仅依赖 Python 标准库，无需第三方包。

```text
偏移 = epoch - 内核时间
kernel_ns = epoch_ns - offset_ns
epoch_ns = kernel_ns + offset_ns
```

JSON 中选定的纳秒偏移 `epoch_minus_kernel_nanoseconds` 使用十进制整数字符串保存，应通过 `int` 读取，避免浮点精度损失。中位数统计可能包含小数纳秒，使用十进制字符串表示。以纳秒表示结果，不代表达到纳秒准确度。

## 选择方法

| 方法 | 输入 | 估计方式与限制 |
| --- | --- | --- |
| 多个 `realtime_ts` 同步点（推荐） | 原始文本 ftrace | 在误差使结果偏小的模型下取最大样本偏移，同时保留全部样本、中位数与范围。同步点只保存 epoch 毫秒，无法承诺绝对误差上限。 |
| 多文件名区间交集（数据依赖最少） | 文件名或命令输出，以及设备 UTC 偏移 | 对每个整秒时间建立偏移区间并取交集，输出中点、上下界和量化不确定性。不读取二进制 trace，也不需要 hilog。 |

两种方法独立、显式运行。同步点缺失时不会自动切换到文件名方法，也不会静默丢弃不一致样本。

## 环境要求

- Python 3.9+；已在 Windows/PowerShell、Python 3.14 上测试。
- 离线分析只需要输入文件和本仓库脚本。
- 在线采集需要 `hdc` 位于 `PATH`、连接一台设备、已开启开发者/调试访问，并具有 hitrace 采集权限。
- 同一时间只运行一个采样程序。在线采集会修改设备共享的 tracing 状态，使用 204800 KB 缓冲区。

在线采集明确指定 `boot` 时钟，它包含休眠时间。偏移仅适用于同一设备、同次开机、同一内核时钟及同一 epoch 时间基准。重启或系统校时后应重新采集。使用 `mono` 时，休眠也会改变其与 epoch 的偏移。离线脚本不会根据数值推断或验证原 trace 所用时钟。

## 无设备快速验证

`examples/` 中是合成示例，不是设备原始采集数据。

```sh
python get_trace_epoch_offset.py examples/boot.ftrace
python get_filename_epoch_offset.py examples/filenames.txt --utc-offset=+08:00
python trace_to_epoch.py examples/boot.ftrace converted_epoch.ftrace
python -m unittest discover -s tests -t . -v
```

前两个命令会输出 JSON，并保存 `examples/boot.offset.json`、`examples/filenames.offset.json`。两个独立示例的偏移分别为 `1792781198.324412000` 秒和 `1735689500.225000000` 秒，它们不属于同一条时间轴。

转换脚本创建新文件，拒绝覆盖已有输出，也拒绝再次转换自身生成的 epoch trace。只修改事件头的时间戳并添加校准注释，保留事件正文中的时间值。`converted_epoch.ftrace` 是派生分析文件；不同查看器对 epoch 时间戳的支持不同，因此应保留原始 trace。

## 在线采集

### 推荐：多个 trace 同步点

```sh
python get_trace_epoch_offset.py
```

脚本依次执行：

1. 使用 `--trace_clock boot` 和 `app sched` 类别开始采集。
2. 调用 19 次 `--trace_dump`，每次之后等待 50 毫秒。
3. 调用 `--trace_finish` 写入第 20 个同步点，并拉取最终文本 trace。
4. 计算所有偏移，检查一致性，输出并保存结果。

命令调用、导出和传输都有额外开销，因此 20 点采样不等于 1 秒采集。结果位于带时间戳的 `trace_epoch_sync_*/` 目录，包含 `capture.log`、`boot.ftrace` 和 `boot.offset.json`。

已有原始文本 trace 可直接离线计算：

```sh
python get_trace_epoch_offset.py path/to/original.ftrace
```

至少需要一个格式正确的 `realtime_ts` 同步点。只有一个点时标记为 `single_point_unverified`。多个点要求偏移范围不超过 1 毫秒；这是一个一致性检查阈值，不是误差保证，调度延迟也可能导致检查失败。失败时应检查原始同步点及采集日志。

### 数据依赖最少：文件名方法

```sh
python get_filename_epoch_offset.py --utc-offset=+08:00
```

脚本执行 20 次独立的 1 秒二进制采集，将文件名和命令输出保存至 `filename_epoch_sync_*/`。每次还有初始化、导出等开销。二进制 trace 留在设备上，脚本不会拉取或删除；请确保设备空间充足，并另行管理这些文件。

已有文件名可以整理成文本清单，也可以直接使用包含文件名、完整路径的 hitrace 命令输出：

```sh
python get_filename_epoch_offset.py filenames.txt --utc-offset=+08:00
```

UTC 偏移是**设备在采集时的本地时区偏移**，不是主机时区。明确的默认值为 `+08:00`；负偏移应写成 `--utc-offset=-05:00`。同一清单必须使用同一 UTC 偏移，所有文件须来自同次开机，且 epoch 与 boot 的偏移保持不变。

支持 `trace_`、`record_`、`record_trace_` 前缀。`-` 后的字段是纳秒整数，不是直接拼接的小数：`@334949-18089377` 表示 `334949.018089377` 秒。重复文件名不提供额外信息。

## 在另一组数据处理中使用偏移

处理另一组数据前，先读取一次校准 JSON：

```python
import json
from decimal import Decimal

with open("examples/boot.offset.json", encoding="utf-8") as source:
    calibration = json.load(source)

offset_ns = int(calibration["epoch_minus_kernel_nanoseconds"])

# 将此字符串替换为每条数据记录的 epoch 秒时间戳。
epoch_seconds = "1793113965.749000000"
epoch_ns = int(Decimal(epoch_seconds) * 1_000_000_000)
kernel_ns = epoch_ns - offset_ns
```

输入已是整数 epoch 纳秒时直接减去偏移；输入是 epoch 毫秒时先乘 `1_000_000`。若数据时间戳来自另一台主机，需要它与设备使用相同的 epoch 时间基准；本工具不测量主机与设备之间的时钟差。

文件名方法应同时保留 `offset_lower_inclusive_nanoseconds`、`offset_upper_exclusive_nanoseconds` 和 `quantization_uncertainty_nanoseconds`。它们表达模型下的文件名量化不确定性，不是实测的绝对误差上限。

## 估计原理

### 同步点模型

同步点的 trace 时间为 `K_i` 秒，`realtime_ts=R_i` 毫秒：

```text
O_i = R_i / 1000 - K_i
O_estimate = max(O_i)
```

公开的 hitrace 实现先读取 `CLOCK_REALTIME`，截断到毫秒，再写入 `trace_marker`。如果偏移保持不变，且误差仅来自截断和写入延迟：

```text
测得偏移 = 真实偏移 - 截断误差 - 读取到标记写入的延迟
```

取最大值倾向于选择偏小程度最小的观测。这是有条件的估计，不是真实偏移的证明。增加样本可以改善重复性，但不能据此证明绝对准确度。所有时间计算采用整数或 `Decimal`，偶数样本中位数也不使用浮点数。

### 文件名模型

将文件名中的本地整秒换算为 epoch，记为 `W_i`；boot 时间记为 `B_i`：

```text
O ∈ [W_i - B_i, W_i + 1秒 - B_i)
lower = max(W_i - B_i)
upper = min(W_i + 1秒 - B_i)
估计值 = [lower, upper) 的中点
```

中点使区间内的最大量化误差最小。只有新增文件覆盖了不同的小数秒位置，交集才会继续缩小。交集为空时直接报错。此模型假设文件名中的现实时间与 boot 值采样时刻足够接近；工具没有测定两者采样延迟的上限。

## 设备实验观察

以下来自单台设备、同次开机的实验，不是普遍的精度保证：

- 两组独立的 20 点同步采样，最大偏移估计相差 119 微秒。
- 20 个文件名将 1 秒的不确定区间缩小到 47.076719 毫秒宽。
- 1000 条 hilog 对比中，`monotonic` 显示值的偏移有整秒跳变；同一 PID 中的 MUSL 日志还出现不同的秒时间域。显示的小数部分复用了现实时间纳秒，因此不把整个 hilog `monotonic` 输出当作统一的 boot 或 mono 时钟。
- 从 hilog 整数秒边界得到的 107.187 微秒宽约束依赖尚未验证的日志产生端时钟模型，未作为正式校准方法。

## 诊断与范围

- 设备命令附带明确的远端退出状态标记，不会仅凭 hdc 主机进程返回零认定设备命令成功。
- 缺失/格式异常的同步点、超过 1 毫秒的同步偏移范围、在线样本数不完整、无效时区、文件名区间不相交都会报错，原始数据与命令日志保留供诊断。
- 文本转换脚本只处理文本 ftrace，不解码二进制 `.sys` 或 `.htrace`。
- 支持的时钟、类别和权限取决于设备，不会自动替换时钟或采集格式。
- 原始设备 trace、诊断日志、生成的偏移报告及 Python 缓存不纳入 Git。复现实验时请在本地保留文件名清单和结果。

## 测试

单元测试统一放在 [`tests/`](tests/README.md)，覆盖同步点偏移估计、文件名区间交集、hdc 失败诊断和 trace 转换。输入均为合成数据或模拟调用，无需连接设备。可用 `python -m unittest tests.test_hdc_capture -v` 运行单个模块。GitHub Actions 在 Python 3.8、3.9、3.10 上运行全部测试和 Pylint。

## 作者

- [ymzhou94](https://github.com/ymzhou94)：项目负责人及需求提出者。
- **Codex（OpenAI）**：AI 共同作者，参与代码实现、测试和文档编写。

## 来源

- [华为 hitrace 文档](https://developer.huawei.com/consumer/cn/doc/doccenter-capabilities/hitrace)
- [OpenHarmony MarkClockSync：现实时间毫秒与 parent_ts](https://github.com/openharmony/hiviewdfx_hitrace/blob/master/utils/common_utils.cpp#L76)
- [OpenHarmony 文本采集、dump、finish 写入同步点的位置](https://github.com/openharmony/hiviewdfx_hitrace/blob/master/cmd/hitrace_cmd.cpp#L1740)
- [HiLog 时间显示实现](https://github.com/openharmony/hiviewdfx_hilog/blob/master/frameworks/libhilog/utils/log_print.cpp#L101)
- [MUSL 独立 HiLog 适配器](https://github.com/openharmony/third_party_musl/blob/master/src/hilog/linux/hilog_adapter.c#L154)
