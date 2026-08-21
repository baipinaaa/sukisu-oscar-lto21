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
Linux version 5.4.302-qgki-gc8c87694a044 (root@3c794f190b77)
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
| 编译器 | clang-r510928 (clang 14) | clang-r574158 (clang 21.0.0, 官方同源) |
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
  -d CONFIG_LOCALVERSION_AUTO --set-str CONFIG_LOCALVERSION "-qgki-gc8c87694a044"
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
| CONFIG_LOCALVERSION | "-qgki" + AUTO | 最终 -qgki-gc8c87694a044 |

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

- 保持 `CONFIG_LOCALVERSION="-qgki-gc8c87694a044"` (写死官方串) +
  `-d CONFIG_LOCALVERSION_AUTO` → vermagic 与官方一致
  (`5.4.302-qgki-gc8c87694a044 SMP preempt mod_unload modversions aarch64`)。
- LTO/CFI 编译后 struct module 布局与官方一致 → module_layout CRC 自动匹配，
  不需要 60_ patch 跳 CRC。

## 验证点 (编译后)

1. `.config` 确认 `CONFIG_LTO_CLANG=y` + `CONFIG_CFI_CLANG=y` +
   `CONFIG_THINLTO=y` + `CONFIG_LD_IS_LLD=y` (workflow 自动检查)
2. `strings Image | grep "Linux version"` 应显示 clang 21 + LLD 21
3. 刷机后 `dmesg` 无 `disagrees about version of symbol module_layout`、
   无 `Unknown symbol` 错误
4. `lsmod` 应出现 btpower/adsp/q6/wcd938x 等音频蓝牙模块
5. `cat /proc/kallsyms | grep swr_driver_register` 应能解析到地址

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
