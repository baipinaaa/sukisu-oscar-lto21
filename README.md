# ReSukiSU LTO21 内核构建方案 (oscar / RMX3478)

> 与 [sukisu-oscar](../sukisu-oscar/)（clang14 + GNU ld + workaround patch）并行的
> **治本方案**：clang21 + ThinLTO + CFI + LLD，完全复现官方编译环境。

## 为什么需要 LTO 方案

从官方 boot.img 的 ikconfig 提取的**官方 config 铁证**：

```
CONFIG_LTO_CLANG=y   CONFIG_THINLTO=y   CONFIG_CFI_CLANG=y
CONFIG_CFI_CLANG_SHADOW=y   # CONFIG_CFI_PERMISSIVE is not set (enforcing!)
CONFIG_LD_IS_LLD=y   CONFIG_CLANG_VERSION=210000
CONFIG_LOCALVERSION="-qgki"   CONFIG_LOCALVERSION_AUTO=y
```

配合官方 boot.img 版本串：

```
Linux version 5.4.302-qgki-g84e7d691ee3a (root@3c794f190b77)
(Android (14054515, +pgo, +bolt, +lto, +mlgo, based on r563880c)
 clang version 21.0.0 (...), LLD 21.0.0 (...))
```

**官方 = clang21 + LTO(THIN) + CFI + LLD**。之前 sukisu-oscar 用 GNU ld 编译，
`LD_IS_LLD=n` → Kconfig 依赖不满足 → `LTO_CLANG=y` 被**自动降级为 LTO_NONE**，
这就是 module_layout CRC 不匹配 / 缺 `__cfi_slowpath` / PREL32 布局错位的
**共同根源**。本方案用 `make LLVM=1` 让 LTO/CFI 生效，与官方 ABI 完全对齐，
**不需要 60_/70_/80_ 三个 workaround patch**。

## 与 sukisu-oscar 的差异

| 项目 | sukisu-oscar (旧) | sukisu-oscar-lto21 (本方案) |
|---|---|---|
| 编译器 | clang-r510928 (clang 14) | clang-r563880 (clang 21.0.0, 官方同源) |
| clang 分支 | main | android16-qpr1-release (clang21 只在此分支) |
| 链接器 | GNU ld | ld.lld (LLVM=1) |
| LTO | 无 (LTO_NONE) | ThinLTO |
| CFI | 无 | CFI_CLANG + CFI_CLANG_SHADOW (官方 enforcing) |
| PREL32 | 用 80_ patch 关闭 | LTO 自动关闭 |
| CRC 校验 | 60_ patch 跳过 | 自动匹配 (结构布局与官方一致) |
| __cfi_slowpath | 70_ patch 假实现 | 内核自带真实现 |
| workaround patch | 60_/70_/80_ 三个 | **零个** |
| 编译时间 | 40-90 分钟 | 60-120 分钟 (LTO 链接慢) |

## 编译流程 (GitHub Actions, 23 步)
```
检出仓库 → 内核源码(缓存) → 依赖 → clang21(缓存) → 集成 ReSukiSU
→ SUSFS 5.4 patch → trace include / tcpc / oplus_project 源码兼容
→ 配置 (LLVM=1 + holi-qgki_defconfig + KSU/SUSFS 开关)
→ 编译 (LLVM=1 + ccache) → 获取原版 boot.img → 打包 → 上传
```

**关键编译命令**：

```bash
# 配置 (LLVM=1 让 LD=ld.lld, LTO/CFI 生效)
make LLVM=1 ARCH=arm64 KCFLAGS='-I. -Idrivers/usb/typec/tcpc' vendor/holi-qgki_defconfig
./scripts/config -e KSU -e KALLSYMS -e KALLSYMS_ALL -e KSU_SUSFS \
  -e KSU_SUSFS_SUS_PATH -e KSU_SUSFS_SUS_MOUNT -e KSU_SUSFS_SUS_KSTAT \
  -e KSU_SUSFS_SPOOF_UNAME -e KSU_SUSFS_ENABLE_LOG \
  -e KSU_SUSFS_HIDE_KSU_SUSFS_SYMBOLS \
  -e KSU_SUSFS_SPOOF_CMDLINE_OR_BOOTCONFIG -e KSU_SUSFS_OPEN_REDIRECT \
  -e KSU_SUSFS_SUS_MAP -d KSU_TRACEPOINT_HOOK \
  -d CONFIG_LOCALVERSION_AUTO --set-str CONFIG_LOCALVERSION "-qgki-g84e7d691ee3a"
make LLVM=1 ARCH=arm64 olddefconfig

# 编译
make LLVM=1 ARCH=arm64 CC="ccache clang" \
  KCFLAGS='-I. -Idrivers/usb/typec/tcpc -DKSU_COMPAT_HAS_SELINUX_STATE' -j$(nproc)
```

配置验证 (workflow 第 15 步自动检查):

```bash
grep -E "CONFIG_LTO|CONFIG_CFI|CONFIG_THINLTO|CONFIG_LD_IS_LLD" .config
# 期望:
#   CONFIG_LTO=y  CONFIG_THINLTO=y  CONFIG_LTO_CLANG=y
#   CONFIG_CFI_CLANG=y  CONFIG_CFI_CLANG_SHADOW=y
#   CONFIG_LD_IS_LLD=y
#   CONFIG_HAVE_ARCH_PREL32_RELOCATIONS 不存在 (LTO 自动关闭)
```

## 风险排查 (本轮核心工作)

### 1. 官方 config 实证 (已从 boot.img ikconfig 提取, 183KB)

| 配置项 | 官方值 | 含义 |
|---|---|---|
| CONFIG_LTO_CLANG | y | 开 LTO |
| CONFIG_THINLTO | y | ThinLTO (非全量 LTO) |
| CONFIG_CFI_CLANG | y | **开 CFI** |
| CONFIG_CFI_CLANG_SHADOW | y | CFI shadow call stack |
| CONFIG_CFI_PERMISSIVE | **n** | **CFI enforcing (panic 模式)** |
| CONFIG_LD_IS_LLD | y | LLD 链接 |
| CONFIG_CLANG_VERSION | 210000 | clang 21 |
| CONFIG_SHADOW_CALL_STACK | y | SCS |
| CONFIG_HAVE_ARCH_PREL32_RELOCATIONS | 无 | 无 PREL32 |
| CONFIG_MODVERSIONS | y | CRC 校验 |
| CONFIG_KPROBES | y | **kprobe 可用 (SUSFS 依赖)** |
| CONFIG_LOCALVERSION | "-qgki" + AUTO | 最终 -qgki-g84e7d691ee3a |

### 2. ReSukiSU 与 CFI 兼容性 (已验证源码)

- **`.cfi_jt` 符号解析**：`kernel/infra/symbol_resolver.c:18` 定义
  `cfi_suffix[] = ".cfi_jt"`，156-158 行**先尝试 `.cfi_jt` 变体**再回退原名。
  这是 KernelSU PR #3461/#3475 的同款实现——CFI 内核 kallsyms 暴露
  `符号.<hash>.cfi_jt` 跳板地址，KSU 必须解析跳板而非裸函数。
- **`__nocfi` 宏**：`kernel/compat/kernel_compat.h:315` 定义，
  `ksu_handle_selinux_setprocattr` / `ksu_syscall_dispatcher` /
  `ksu_hook_execve` 等被间接调用的函数全部标记 → CFI 校验通过。
- **syscall table patch**：ReSukiSU 直接 patch `sys_call_table[nr]`
  (hook/arm64/syscall_hook.c:206-207 解析 sys_call_table)。
  **CFI enforcing 下 sys_call_table 存的是 `.cfi_jt` 跳板地址**——
  必须把跳板地址解析出来替换，否则 CFI check 失败 panic。
  ReSukiSU 的 `.cfi_jt` 支持正是为此设计。

### 3. SUSFS 与 CFI 兼容性 (已验证)

- SUSFS 用 **kprobe** (`register_kprobe`/`unregister_kprobe`，
  见 `KernelSU/10_enable_susfs_for_ksu.patch:638-658`)。
- CFI 只校验**间接调用**，kprobe 是**指令级断点/跳转替换**——不经过 CFI 检查。
- 官方 config `CONFIG_KPROBES=y` + `CONFIG_KPROBES_QGKI=y` → kprobe 子系统
  在官方内核中启用，SUSFS 可正常注册。

### 4. clang21 编译 5.4 内核 (官方实证 + 社区实证)

- **官方实证**：官方 boot.img 就是 clang21.0.0 编的（版本串铁证）。
- **社区实证**：Xiaomi Mi Mix 4 (kernel 5.4.302) 用 Clang LTO + CFI +
  Shadow Call Stack 成功编译 KernelSU-Next + SUSFS v1.5.5。
- **风险点**：`Makefile:860 KBUILD_CFLAGS += -Werror` —— clang21 新警告
  (如 `-Wdefault-const-init-var-unsafe`) 会直接编译失败。
  **官方源码已通过 clang21 编译**（官方即 clang21），风险只在我们加的 patch：
  - 50_ SUSFS patch（大量新代码）→ 若触发新警告需补 `-Wno-*`
  - trace include / tcpc / oplus_project 都是极小改动
  - **兜底**：workflow 编译步骤前若失败，可加
    `KBUILD_CFLAGS += -Wno-default-const-init-var-unsafe` 等

### 5. LTO 链接风险

- 5.4 的 LTO 机制在 **Makefile 内部** (793-1057 行) + `scripts/module-lto.lds`，
  非 upstream 5.12+ 的 Makefile.lto 方式——脚本 `scripts/lto-ld` 不存在
  (官方 5.4 不用)。link-vmlinux.sh 已处理 ThinLTO (`--thinlto-cache-dir`)。
- `ld.lld` 21 对 5.4 的 `--discard-all`/`--orphan-handling` 等 flag 兼容。
- **风险**：5.4 老代码个别地方可能不兼容 clang21 语义 (如 `__attribute__` 变化)，
  但官方同版本 clang21 已编译成功 → 风险集中在我们的 patch。

### 6. CFI enforcing (官方) + KSU 的组合风险 (最高风险点)

- 官方 `# CONFIG_CFI_PERMISSIVE is not set` = **CFI 校验失败直接 panic**。
- KSU 的 syscall table patch 替换函数指针——**若替换成裸函数地址而非
  `.cfi_jt` 跳板，调用时 CFI 校验失败 → kernel panic (无法开机)**。
- ReSukiSU 已内置 `.cfi_jt` 解析 → 理论兼容。
- **兜底开关** (workflow 输入 `enable_cfi`)：若 panic，可关闭 CFI
  (`./scripts/config -d CFI_CLANG -d CFI_CLANG_SHADOW`) 仅保留 LTO。
  关闭 CFI 后模块 `__cfi_slowpath` 引用由 70_ patch 兜底 (可选)。

### 7. 版本号 / vermagic

- 保持 `CONFIG_LOCALVERSION="-qgki-g84e7d691ee3a"` (写死官方串) +
  `-d CONFIG_LOCALVERSION_AUTO` → vermagic 与官方一致
  (`5.4.302-qgki-g84e7d691ee3a SMP preempt mod_unload modversions aarch64`)。
- LTO/CFI 编译后 struct module 布局与官方一致 → module_layout CRC 自动匹配，
  不需要 60_ patch 跳 CRC。

### 8. Module.symvers CRC 校验 (workflow 编译步骤尾部)

**背景**：run 32489911904 的 Image 中 vendor 模块被拒 (`disagrees about
version of symbol module_layout`)。workflow 因此在编译步骤尾部加了
**Module.symvers CRC 校验**：40 个符号对照官方值，`module_layout`
不匹配立即失败；artifact 一并上传 `kernel/Module.symvers` 供失败分析。

**40 个符号清单来源**：vendor `swr_dlkm.ko` 的 `__versions` 段
（64B/条），再用官方 boot.img 的 `__kcrctab` 反查补全。

#### 8.1 内核 revision 变更导致的 CRC 变化 (2026-09-24 更新)

| nightly | 官方内核 revision | 树日期 | module_layout |
|---|---|---|---|
| 20260817（手机当前） | `c8c87694a044` | 2026-01-27 | `0x392bc26a` |
| 20260921 / 0914 / 0907 | `84e7d691ee3a` | 2026-08-22 | `0x32aa09e1` |

> `84e7d691ee3a` = `lineage-23.2` 分支 HEAD，commit message
> "arm64: configs: Regenerate Q/GKI holi defconfigs"。
> 两 revision 相差 **1090 个 commit**。

**40 个符号中 17 个未变、23 个已变**。变动的 23 个：

```
__devm_regmap_init       0x5b50214e -> 0xf813045f
__regmap_init            0xd2ca9c4f -> 0xc2cfe3d6
_dev_err                 0xd72f0f38 -> 0xf58b10d2
bus_register             0xccd0b4a3 -> 0x16fd06ab
bus_unregister           0x3ecd980d -> 0x6025f0e2
dev_get_regmap           0xde1c7ffb -> 0x2ef6d2f9
dev_set_name             0xf7079be9 -> 0x40ab455d
device_for_each_child    0x552d9990 -> 0x0543f71d
device_register          0x43ca8ed5 -> 0x4c8a95b7
device_unregister        0x070f701b -> 0x98be9da8
driver_register          0xa1e3d6b3 -> 0xd83b86f9
driver_unregister        0x66e29c37 -> 0xacfc56f9
get_device               0x3e9a0de0 -> 0x9f76c44c
kmalloc_caches           0x91ea9a65 -> 0x41bdb885
kmem_cache_alloc_trace   0x4a2650f5 -> 0x3d9902e6
module_layout            0x392bc26a -> 0x32aa09e1
of_alias_get_id          0xd4e71368 -> 0x43aadc47
of_get_next_available_child 0x64e65b58 -> 0xe6ba705e
of_modalias_node         0x7f16941c -> 0xf993cc8e
of_property_read_u64     0x34abb066 -> 0x37dfa3a3
pm_generic_resume        0x9d3e14da -> 0xdc98fa9e
pm_generic_suspend       0x6179774c -> 0xad5ff920
put_device               0x46bc19b5 -> 0x23e50e24
```

未变的 17 个（基本类型/小结构，不受 defconfig 影响）：
`__cfi_slowpath` `__kmalloc` `__list_add_valid` `__list_del_entry_valid`
`__mutex_init` `__stack_chk_fail` `__stack_chk_guard` `idr_alloc`
`idr_find` `idr_remove` `kfree` `krealloc` `mutex_lock` `mutex_unlock`
`printk` `strcmp` `strlcpy`

**根因**：`84e7d691ee3a` 的 "Regenerate Q/GKI holi defconfigs" 改了
`holi-qgki_defconfig`，配置差异传导到 `struct module / device / bus_type /
device_driver / kmem_cache / device_node` 的 genksyms 展开 → CRC 变。

> 8-17 时期的静态排查已排除源码树差异，当时留下的谜团
> "实测 0x32aa09e1 ≠ 官方 0x392bc26a" 现在解释清楚了：我们当时用的是
> HEAD 树（= 新 defconfig），而官方 8-17 nightly 用的是旧 defconfig。
> **当时实测的 0x32aa09e1 与现在官方 9-21 的值完全吻合**，反向印证了
> 本次反查的正确性。

#### 8.2 新 CRC 的反查方法 (20260921 官方 boot.img)

```
1. 从 boot.img 取出 kernel Image (43,493,888 B)
2. vaddr 映射: vaddr = 0xffffffc010080000 + file_off
3. 关键恒等式: 对同一符号, 6*Y - P = const
     P = __ksymtab   项文件偏移 (24B/项: value, name, namespace)
     Y = __kcrctab   项文件偏移 (4B/项)
   (因 __ksymtab/_gpl 与 __kcrctab/_gpl 紧邻无 padding, 两表 const 相同)
4. 用 17 个"旧值命中"的锚点 (在 Image 内直接搜到旧 CRC, 且反推出
   __ksymtab 引用位置) 定出 const = 0xb74cb30 —— 17/17 一致
5. 由 const 反解 23 个 MISS 符号的 Y, 读出新 CRC (每个符号唯一候选)
6. 交叉验证:
   - 40/40 项的 __ksymtab value 字段均为合法 vaddr (0/40 非法)
   - 17 未变 / 23 已变 与 "直接搜到/搜不到" 的 HIT/MISS 分布完全吻合
   - module_layout=0x32aa09e1 == 8-17 时期 HEAD 树实测值
```

反查脚本与本轮产物留在 `../_crc921/`（`final_crc.py` / `verify.py` /
`verify_value.py` / `kernel_921.raw`）。

**残留风险**：新值是反查所得（非官方直接给出）。若某个值有误，workflow
的 CRC 校验会在 `module_layout` 不匹配时**立即失败**（不会产出坏内核），
届时用 artifact 里的 `kernel/Module.symvers` 与官方值对照即可修正。

## 验证点 (编译后)

0. **Module.symvers 校验**：workflow 自动对比 40 个关键符号 CRC
   （20260921 对应期望 `module_layout=0x32aa09e1`，不匹配构建直接失败）
1. `.config` 确认 `CONFIG_LTO_CLANG=y` + `CONFIG_CFI_CLANG=y` +
   `CONFIG_THINLTO=y` + `CONFIG_LD_IS_LLD=y` (workflow 自动检查)
2. `strings Image | grep "Linux version"` 应显示 clang 21 + LLD 21
3. 刷机后 `dmesg` 无 `disagrees about version of symbol module_layout`、
   无 `Unknown symbol` 错误
4. `lsmod` 应出现 btpower/adsp/q6/wcd938x 等音频蓝牙模块
5. `cat /proc/kallsyms | grep swr_driver_register` 应能解析到地址

## genksyms 双树诊断 (workflow 步骤 6.5, 仅失败时运行)

**背景（8-17 时期）**：官方 Image (c8c87694a044 树) 的
`module_layout=0x392bc26a`，而我们用 lineage-23.2 HEAD 树实测
`0x32aa09e1`。静态排查已排除 config（官方=LTO21 子集）、树（284 文件
include 图仅 4 个无关 DIFF）、编译器（官方 boot.img 实证也是 clang
21.0.0 r563880c，llvm-project commit 5e96669f 同源）。

本步骤做**决定性对照实验**：官方 commit 纯树 + 我们的 `.config` +
我们的 clang21，重跑 `kernel/module.o`、`printk.o`、`slab_common.o`、
`drivers/base/core.o` 的 genksyms（`KBUILD_SYMTYPES=1` → `.symtypes`）：

```
[9a] HEAD 树: KBUILD_SYMTYPES=1 重编上述 4 个文件 → 4 个 .symtypes
[9b] 官方 commit 纯树 (GitHub archive tarball 下载) + 我们的 .config +
     我们的 clang21 → 同样 4 个 .symtypes + Module.symvers
[9c] diff 每对 .symtypes → 差异 token 直接可见
[9d] 判定:
     · 官方纯树 = 官方值   → 差异在 HEAD 树/config → diff 定位修复
     · 官方纯树 = 我们的值 → 官方 Image 含本地修改 (非纯树)
       → 后续走 CRC 强制覆盖方案 (改 genksyms 输出固定 CRC)
```

**2026-09-24 变更**：官方内核已改用 `84e7d691ee3a`（= `lineage-23.2`
HEAD），我们 checkout 的就是官方树，"树不一致" 这一根因已消除；且官方
9-21 的 `module_layout` 现在是 `0x32aa09e1`（已由 20260921 官方 boot.img
反查确认）。因此该步骤由 `if: always()` 改为 **`if: failure()`** ——
不再每次白下载 200-300MB tarball + 双份编译，仅在前面步骤失败时运行，
保留定位能力。

诊断产物（head_*.symtypes / off_*.symtypes / diff_*.txt）随 artifact 上传，
供本地精确分析。

## 备选: 关闭 CFI 的降级方案

若 CFI enforcing 下 KSU 引发 panic (无法开机)：
1. workflow 输入 `enable_cfi: false` 重跑 (仅 LTO, 无 CFI)
2. 或临时在 config 步骤加 `./scripts/config -e CFI_PERMISSIVE`
   (CFI 校验失败只告警不 panic, 最保守)

## 文件结构

```
sukisu-oscar-lto21/
├── .github/workflows/build-sukisu-lto21.yml   # 24 步 workflow
├── patches/
│   ├── 50_add_susfs_in_kernel-5.4.patch       # SUSFS 5.4 移植 (同 sukisu-oscar)
│   └── 70_add_cfi_slowpath.patch              # 仅 enable_cfi=false 时应用 (CFI 关闭兑底)
└── README.md
```
