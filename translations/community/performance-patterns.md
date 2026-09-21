---
schema_version: 1
skill_id: community/performance-patterns
translated_at: 2026-09-21
---

<!-- Windows port. Derived from intel/intel-performance-skills (MIT). Original (C) 2026 Intel Corporation. -->
# 性能模式技能

这是一个持续扩展的目录，收录会引发性能问题的知名代码模式，并为每种模式提供检测信号和解决操作手册。核心模式目录聚焦 x86 CPU 代码，同时提供 Windows、WSL Linux、原生 Linux 以及 CUDA/NVIDIA 环境的平台路由。

只要硬件相同，优化知识就可以跨平台复用。不同平台之间变化的是具体操作方式：分析器术语、编译器选项、调试信息格式、同步原语、CPU 特性检测，以及瓶颈究竟位于 CPU 主机代码还是 CUDA 设备工作中。

---
## 第 0 步——确定平台路线

如果用户提到 WSL、Linux、CUDA、NVIDIA、GPU、Nsight、驱动程序与工具包不匹配、容器，或者平台不明确，请先阅读 **`references/platform-routing.md`**。

然后加载对应平台的参考资料：

| 平台或症状 | 阅读 |
|---|---|
| Windows 原生 C/C++ CPU 性能 | `PORTING-NOTES.md` |
| WSL Linux CPU 性能 | `references/wsl-linux.md`，然后阅读 `references/linux-native.md` |
| 原生 Linux CPU 性能 | `references/linux-native.md` |
| CUDA/NVIDIA 设置或 GPU 性能 | `references/cuda.md` |

当环境本身存在问题，或者用户没有提供足够的工具链和分析器上下文时，在 Windows 上使用 `scripts/collect-perf-env.ps1`，在 WSL 或原生 Linux 中使用 `scripts/collect-perf-env.sh`。

---
## 如何使用本技能
### 第 1 步——加载适合当前上下文的文件

| 上下文 | 阅读此文件 |
|---------|-----------|
| 你有**性能分析输出**（VTune、AMD uProf、ETW/WPA、perf、火焰图、Nsight 摘要等） | `triggers/from-profile.md` |
| 你正在**阅读现有源代码**，但还没有性能分析数据 | `triggers/from-source.md` |
| 你正在**编写新的**性能敏感 C/C++ 或 SIMD 代码 | `guidelines/new-code.md` |

这些触发文件覆盖相同的全部模式；它们彼此分开，使你只需加载当前相关的内容。`guidelines/new-code.md` 是编写代码时使用的检查清单——生成新代码时加载它，而不是加载触发文件；审查现有代码时则不要这样做。

### 第 2 步——识别匹配的模式

每个触发文件都包含精简表格和简短说明，足以判断代码或性能分析结果是否与某个已知模式匹配。

### 第 3 步——阅读模式详情文件

模式匹配后，读取 `patterns/` 中对应的文件。不要凭记忆尝试修复。

### 第 4 步——应用修复并验证

按照模式文件中的分步说明和验证方法操作，并使用第 0 步所加载路由文件中的平台机制。

多个模式可能同时适用。选定一个模式前，检查所有合理的匹配项。

---
## 可复用的库模块

这些独立实现指南可供使用本技能的任何 Agent 调用，并不局限于跟随某个特定模式时使用。如果需要相应能力，请直接加载相关文件。

| 模块 | 提供的内容 |
|--------|-----------------|
| `library/cpu-dispatch.md` | Windows 上的运行时 CPU 特性检测和变体选择。**只允许手动函数指针分派**——PE/COFF 目标不支持 `target_clones`（没有 IFUNC）。涵盖 `__builtin_cpu_supports`、显式 `__cpuid`/`_xgetbv` 路径，以及每个变体的 `__attribute__((target(...)))`。不限定 CPU 厂商（Intel 和 AMD 均适用）。当一个函数有多个需要在运行时连接起来的性能等级实现时使用。 |
| `patterns/simd-upconversion-impl.md` | 用内在函数把向量寄存器宽度翻倍的完整分步 zipper 算法（SSE→AVX2 或 AVX2→AVX-512）；AVX-512 累加器模板；变换后的检查清单（CPUID 防护、vzeroupper、target 属性）。 |
| `patterns/fast-crc32c-impl.md` | 可直接使用的 CRC32C 库：AVX-512 VPCLMULQDQ 融合、SSE4.2 + PCLMULQDQ 多累加器，以及纯 C 后备实现。包含运行时 CPU 分派包装器。需要新的 CRC32C 代码或现有实现成为瓶颈时使用。 |
| `references/platform-routing.md` | 在应用模式之前，为 Windows、WSL、原生 Linux 和 CUDA 请求选择路线。 |
| `references/cuda.md` | CUDA 设置和性能分诊：驱动程序与工具包可见性、Nsight Systems 与 Nsight Compute 的选择，以及 WSL GPU 边界。 |

---
## 跨技能集成

性能分析器或数据采集技能识别出已知模式后，可以调用本技能。本技能也可以直接根据源代码、性能分析片段或环境探测输出独立工作。

## 技能状态

本技能已移植并针对 Windows 原生 C/C++ 完成审阅，随后扩展了 WSL Linux、原生 Linux 和 CUDA/NVIDIA 环境的路由说明。当前包含以下核心文件：本 `SKILL.md`、`PORTING-NOTES.md`、`triggers/from-source.md`、`triggers/from-profile.md`、`guidelines/new-code.md`、`design.md`、`library/cpu-dispatch.md`、CRC32C 库源代码、平台参考资料、基准测试，以及全部模式文件：

- `parallel-accumulator`、`missing-vzeroupper`、`missing-restrict`
- `ttas`、`false-sharing`、`per-cpu-stats`、`cold-path-annotation`
- `cv-thundering-herd`、`mutex-to-rwlock`、`simd-sort`
- `simd-upconversion` + `simd-upconversion-impl`
- `fast-crc32c` + `fast-crc32c-impl`
- `library-version-upgrade`

`references/library-versions.md` 有意把未经验证的第三方 DLL 条目保留在 TODO 表格中；未经一手来源核验和 Windows 实测，不得把这些条目提升为推荐项。
