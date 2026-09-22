---
schema_version: 1
skill_id: community/performance-patterns
translated_at: 2026-09-21
---

<!-- (C) 2026 Intel Corporation，MIT 许可证 -->

# 性能模式技能

## 技能说明

从源代码或性能分析输出（perf、VTune、火焰图）中检测并修复 x86/C/C++ 性能模式。当用户要求优化、进行性能审查，或编写新的 SIMD/向量化代码时，即使没有性能分析数据也应调用本技能。

触发情形包括：串行累加循环、可以从 xmm/ymm 扩展到 ymm/zmm 的窄 SIMD、`_mm*` 内在函数、HITM/`cmpxchg` 聚集、伪共享、缺少 `restrict` 或 `vzeroupper`、`futex_wake`/`notify_all` 惊群、系统库（`.so`）中存在版本差距的热点符号，以及任何编写快速归约、点积或 CPU 分派函数的请求。

覆盖的模式包括：串行累加器、TTAS 自旋锁、SIMD 扩宽（zipper）、伪共享、每 CPU 统计、缺少 `vzeroupper`、缺少 `restrict`、条件变量惊群、互斥锁转读写锁、CPU 分派、库版本升级、快速 CRC32C、已知算法（余弦相似度、汉明距离、Jaccard 距离）和 SIMD 排序（x86-simd-sort）。

这是一个持续扩展的目录，收录会引发性能问题的知名代码模式，并为每种模式提供检测信号和解决操作手册。

---

## 如何使用本技能

### 第 1 步——加载适合当前上下文的文件

| 上下文 | 阅读此文件 |
|---|---|
| 你有**性能分析输出**（perf annotate、perf c2c、perf stat、VTune、火焰图等） | `triggers/from-profile.md` |
| 你正在**阅读现有源代码**，但还没有性能分析数据 | `triggers/from-source.md` |
| 你正在**编写新的**性能敏感 C/C++ 或 SIMD 代码 | `guidelines/new-code.md` |

这些触发文件覆盖相同的全部模式；它们彼此分开，使你只需加载当前相关的内容。`guidelines/new-code.md` 是编写代码时使用的检查清单：生成新代码时加载它，而不是触发文件；审查现有代码时不要这样做。

### 第 2 步——识别匹配的模式

每个触发文件都包含精简表格和简短说明，足以判断代码或性能分析结果是否与某个已知模式匹配。

### 第 3 步——阅读模式详情文件

模式匹配后，读取 `patterns/` 中对应的文件。不要凭记忆尝试修复。

### 第 4 步——应用修复并验证

按照模式文件中的分步说明和验证方法操作。

多个模式可能同时适用。选定一个模式前，检查所有合理的匹配项。

---

## 可复用的库模块

这些独立实现指南可供使用本技能的任何 Agent 调用，并不局限于跟随某个特定模式时使用。如果需要相应能力，请直接加载相关文件。

| 模块 | 提供的内容 |
|---|---|
| `library/cpu-dispatch.md` | 运行时 CPU 特性检测和变体选择：`target_clones`（由编译器驱动的普通 C/C++）与 `__builtin_cpu_supports`（手写变体）。当一个函数有多个需要在运行时连接起来的性能等级实现时使用。 |
| `patterns/simd-upconversion-impl.md` | 在汇编或内在函数中把向量寄存器宽度翻倍的完整 zipper 算法（SSE→AVX2 或 AVX2→AVX-512）；AVX-512 累加器模板；变换后的检查清单（CPUID 防护、`vzeroupper`、clobber 列表）。 |
| `patterns/fast-crc32c-impl.md` | 可直接使用的 CRC32C 库：AVX-512 VPCLMULQDQ 融合（corsix v3s1_s3，64–97 GB/s）、SSE4.2 + PCLMULQDQ 三累加器（约 15–25 GB/s）以及纯 C 后备实现。包含运行时 CPU 分派包装器。需要新的 CRC32C 代码或现有实现成为瓶颈时使用。 |
