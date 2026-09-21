---
schema_version: 1
id: data-and-performance
skills:
- id: community/performance-patterns
- id: data-engineering/spark-and-distributed-processing
title: 性能模式与分布式数据处理
summary: 以 Intel 官方 x86 性能模式技能分析 CPU 热点，并用 Spark 技能处理分布式作业；另说明扩展到 Windows、WSL 与 CUDA 时的衍生选择。
author: hwskill-maintainers
topics:
- performance
- spark
- data-engineering
evidence:
- url: https://github.com/intel/intel-performance-skills
  observed_at: '2026-09-21T00:00:00+08:00'
- url: https://github.com/2233admin/performance-patterns-skill
  observed_at: '2026-09-21T00:00:00+08:00'
- url: https://github.com/vaquarkhan/data-engineering-agent-skills
  observed_at: '2026-09-21T00:00:00+08:00'
status: ready
---

# 性能模式与分布式数据处理

## 首选 Intel 官方 `performance-patterns`

`community/performance-patterns` 现在直接指向 Intel 官方 `intel/intel-performance-skills` 中的 `skills/performance-patterns`。它适合已经有 C/C++ 源码，或者已经取得 perf、VTune、火焰图等证据的 x86 CPU 性能工作：先从触发文件识别模式，再读取对应 playbook，最后按模式给出的方式验证修改。

它重点覆盖串行累加器、窄 SIMD、伪共享、TTAS、缺少 `restrict` 或 `vzeroupper`、条件变量惊群、CPU 分派、库版本差距、CRC32C 与 SIMD 排序等问题。需要上游维护的核心模式目录时，应优先选择这个官方来源。

## 何时查看 2233admin 衍生版本

[`2233admin/performance-patterns-skill`](https://github.com/2233admin/performance-patterns-skill) 的 `SKILL.md` 明确声明它是从 Intel 技能派生的 Windows port。这个衍生版本在核心模式之外增加了 Windows 原生、WSL Linux、原生 Linux 与 CUDA/NVIDIA 的平台路由，以及对应的环境采集和工具选择说明。

当任务明确涉及 MSVC/clang-cl、ETW/WPA、Windows 版 VTune 或 uProf、`/mnt/c` 文件系统边界、WSL `perf` 限制、CUDA 驱动与 Toolkit 可见性、Nsight Systems/Compute 路由时，可以单独查看该衍生版本。它不作为本目录 `community/performance-patterns` 的主要来源，也不代表这些平台路径已经由本目录完成安装、行为或硬件效果验证。

## 与 Spark 技能组合

`data-engineering/spark-and-distributed-processing` 面向 Spark 分区、倾斜、Shuffle 和分布式数据处理。遇到 Spark 作业内的本地 C/C++/JNI 热点时，可以先用 Spark 技能确定问题落在哪个 Stage、分区或数据交换边界，再用 Intel 性能模式技能分析已经定位出的本地热点。单纯的 SQL 计划、分区与 Shuffle 问题不需要调用 CPU 模式目录。

## 当前证据边界

本推荐核对了三个公开仓库、目标技能路径和声明的许可证。尚未执行技能安装、示例行为验证、Windows/WSL/CUDA 环境验证或目标硬件性能实验；后续 PR 应提交独立验证报告，而不是把来源核对表述为功能已经验证。
