# Tests / 测试说明

These tests validate offset estimation, timestamp conversion and command failure handling. They use synthetic inputs and mocked hdc calls; no connected device or third-party test framework is required.

这些测试验证偏移估计、时间戳转换及命令失败处理。测试使用合成输入和模拟 hdc 调用，不需要连接设备，也不需要第三方测试框架。

| Module / 模块 | Coverage / 覆盖内容 |
| --- | --- |
| `test_get_trace_epoch_offset.py` | Exact nanosecond arithmetic, multi-marker estimates, median precision, missing/malformed markers and inconsistent offsets. / 纳秒精确计算、多同步点估计、中位数精度、同步点缺失或异常及偏移不一致。 |
| `test_get_filename_epoch_offset.py` | Filename interval intersection, quantization bounds, duplicate names, unpadded nanoseconds, timezones and conversion direction. / 文件名区间交集、量化边界、重复文件名、未补零纳秒字段、时区及换算方向。 |
| `test_hdc_capture.py` | Host/device exit status, missing status markers, file transfers and diagnostic output. / 主机与设备退出状态、状态标记缺失、文件传输及诊断输出。 |
| `test_trace_to_epoch.py` | Event timestamp conversion, payload preservation, multi-marker calibration and repeated-conversion rejection. / 事件时间转换、正文保留、多同步点校准及拒绝重复转换。 |

Run all tests from the repository root / 在仓库根目录运行全部测试：

```sh
python -m unittest discover -s tests -t . -v
```

Run one module / 运行单个模块：

```sh
python -m unittest tests.test_hdc_capture -v
```

GitHub Actions runs Pylint and the complete test suite on Python 3.8, 3.9 and 3.10. Unit tests verify the algorithms and diagnostics; they do not measure physical device clock accuracy.

GitHub Actions 在 Python 3.8、3.9、3.10 上运行 Pylint 和全部测试。单元测试验证算法和诊断行为，不测量真实设备时钟的准确度。
