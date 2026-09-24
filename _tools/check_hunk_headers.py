#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""校验 unified diff 的 hunk 头行数声明与实际内容是否一致。

起因: patches/50_add_susfs_in_kernel-5.4.patch 的 include/linux/susfs_def.h
新文件 hunk 声明 "@@ -0,0 +1,128 @@"，但实际有 158 行 '+'。GNU patch 读满
声明的 128 行后就跳到下一个 "diff -ruN" 头，静默丢弃多出的 30 行 —— 而这
30 行恰好包含 SUSFS_IS_INODE_SUS_MAP 等宏定义和末尾的 #endif，导致生成的
susfs_def.h 条件编译未闭合:

    ./include/linux/susfs_def.h:1:2: error: unterminated conditional directive
    mm/memory.c:4555:30: error: implicit declaration of function
        'SUSFS_IS_INODE_SUS_MAP' [-Werror,-Wimplicit-function-declaration]

用法: python check_hunk_headers.py <patch1> [patch2 ...]
退出码: 0 = 全部一致, 1 = 存在不一致
"""
import re
import sys

HUNK_RE = re.compile(r'^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@')


def check(path):
    with open(path, encoding='utf-8', errors='surrogateescape') as f:
        lines = f.read().split('\n')
    n = len(lines)
    bad = []
    cur_file = '?'
    i = 0
    while i < n:
        ln = lines[i]
        if ln.startswith('+++ '):
            cur_file = ln[4:].split('\t')[0].strip()
        m = HUNK_RE.match(ln)
        if not m:
            i += 1
            continue
        o0, oc_s, n0, nc_s = m.groups()
        oc = int(oc_s) if oc_s is not None else 1
        nc = int(nc_s) if nc_s is not None else 1
        j = i + 1
        a_old = a_new = 0
        while j < n:
            # 文件头 "--- a/xxx" 紧跟 "+++ b/xxx" 才算 hunk 结束
            if (lines[j].startswith('--- ') and j + 1 < n
                    and lines[j + 1].startswith('+++ ')):
                break
            c = lines[j][:1]
            if c == ' ':
                a_old += 1
                a_new += 1
            elif c == '-':
                a_old += 1
            elif c == '+':
                a_new += 1
            elif c == '\\':
                pass  # "\ No newline at end of file"
            else:
                break
            j += 1
        if (a_old, a_new) != (oc, nc):
            bad.append((i + 1, cur_file, ln.strip(), oc, nc, a_old, a_new))
        i = j if j > i else i + 1
    return bad


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    total_bad = 0
    for path in sys.argv[1:]:
        bad = check(path)
        print('=== %s ===' % path)
        if not bad:
            print('  OK: 所有 hunk 头行数声明与实际一致')
        for lineno, fn, hdr, oc, nc, ao, an in bad:
            total_bad += 1
            print('  行 %d: %s' % (lineno, fn))
            print('    声明: %s' % hdr)
            print('    实际: -%d,+%d   (声明 -%d,+%d)   差 -%+d,+%+d'
                  % (ao, an, oc, nc, ao - oc, an - nc))
        print()
    print('合计不一致 hunk: %d' % total_bad)
    return 1 if total_bad else 0


if __name__ == '__main__':
    sys.exit(main())
