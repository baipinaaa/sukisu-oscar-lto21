#!/usr/bin/env python3
"""
解析 LineageOS 官方 API，找出当前仍可下载的 lineage-<version> nightly，
输出原版 boot.img 的直链 / sha256 / OTA zip 直链，供 workflow 与本地脚本复用。

为什么必须动态解析（2026-10-15 教训）:
  mirrorbits 只保留最近几个 nightly 的 /full/<device>/<date>/ 目录。写死日期的直链
  一旦被清理就 404 —— build 104858632193 就是死在写死的 20260921 上:
      https://mirrorbits.lineageos.org/full/oscar/20260921/boot.img  -> 404
  与此同时官方 API 仍能给出该版本最后一个可用 build 的 boot.img url + sha256。

用法:
  python3 _tools/resolve_los_build.py --device oscar --version 23.2 \
      --fallback-date 20261005            # stdout 为 KEY=VALUE, 可直接 eval
  eval "$(python3 _tools/resolve_los_build.py --device oscar --version 23.2)"
  python3 _tools/resolve_los_build.py --device oscar --version 23.2 --json
  # 离线自测: 用已保存的 API 响应
  python3 _tools/resolve_los_build.py --device oscar --version 23.2 \
      --api-json-file api.json

输出键:
  LOS_DATE / LOS_VERSION / SOURCE(api|fallback)
  BOOT_URL / BOOT_URL_MIRROR / BOOT_SHA256 / BOOT_SIZE
  ZIP_URL / ZIP_SHA256
"""

import argparse
import json
import sys
import urllib.error
import urllib.request

API_TMPL = "https://download.lineageos.org/api/v2/devices/{device}/builds"
# 备用镜像: 同 path, 换 host (mirrorbits 挂了/限流时用)
MIRROR_HOSTS = [
    ("https://mirrorbits.lineageos.org", "https://mirror.freedif.org/lineageos"),
]
UA = "Mozilla/5.0 (compatible; sukisu-oscar-lto21/1.0)"


def log(msg):
    print(msg, file=sys.stderr)


def fetch(url, timeout=30, retries=3):
    last = None
    for i in range(1, retries + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except (urllib.error.URLError, OSError) as e:
            last = e
            log("  ! 请求失败 (%d/%d): %s -> %s" % (i, retries, url, e))
    raise SystemExit("API 不可用: %s (%s)" % (url, last))


def norm_date(s):
    return (s or "").replace("-", "")


def mirror_url(url):
    """mirrorbits 直链 -> 备用镜像直链"""
    for src, dst in MIRROR_HOSTS:
        if url.startswith(src + "/"):
            return dst + url[len(src):]
    return url


def pick_build(builds, want_ver):
    """选出 version == want_ver 的最新 build(按 date)。返回 (build, why)"""
    exact = [b for b in builds if str(b.get("version", "")).strip() == want_ver]
    if exact:
        exact.sort(key=lambda b: norm_date(b.get("date")))
        return exact[-1], "精确匹配 version=%s" % want_ver
    # 宽松匹配: '23' 命中 '23.2'; '23.2' 不会命中 '24.0'
    loose = [
        b for b in builds
        if str(b.get("version", "")).strip().startswith(want_ver + ".")
        or str(b.get("version", "")).strip() == want_ver
    ]
    if loose:
        loose.sort(key=lambda b: norm_date(b.get("date")))
        return loose[-1], "宽松匹配 version≈%s" % want_ver
    return None, "API 中没有 version=%s 的 build (现有: %s)" % (
        want_ver, ", ".join(sorted({str(b.get('version')) for b in builds})) or "无")


def find_file(build, name):
    for f in build.get("files") or []:
        if f.get("filename") == name:
            return f
    return None


def emit(kv, as_json):
    if as_json:
        print(json.dumps(kv, ensure_ascii=False, indent=2))
        return
    for k, v in kv.items():
        print("%s=%s" % (k, "" if v is None else v))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--device", default="oscar")
    ap.add_argument("--version", default="23.2", help="LineageOS 版本, 如 23.2")
    ap.add_argument("--fallback-date", default="", help="API 不可用时用的 nightly 日期 YYYYMMDD")
    ap.add_argument("--api-json-file", default="", help="离线自测: 直接用本地 API 响应")
    ap.add_argument("--no-network", action="store_true", help="禁止联网(仅配合 --api-json-file)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    want = args.version.strip()
    fb = norm_date(args.fallback_date)
    kv = {}

    if args.api_json_file:
        log("=== 读取本地 API 响应: %s ===" % args.api_json_file)
        builds = json.load(open(args.api_json_file, encoding="utf-8"))
    elif args.no_network:
        raise SystemExit("--no-network 需要 --api-json-file")
    else:
        url = API_TMPL.format(device=args.device)
        log("=== 查询官方 API: %s ===" % url)
        try:
            builds = json.loads(fetch(url).decode("utf-8"))
        except SystemExit as e:
            # API 挂了不是致命错误: 退到 --fallback-date 模板直链
            log("  !! %s" % e)
            builds = []

    if isinstance(builds, dict):  # 兼容 {"response": [...]} 形态
        builds = builds.get("response", [])
    if not isinstance(builds, list):
        raise SystemExit("API 响应形态异常: %s" % type(builds).__name__)
    if builds:
        log("  共 %d 个 build: %s" % (
            len(builds),
            ", ".join("%s@%s" % (b.get("version"), norm_date(b.get("date"))) for b in builds),
        ))
    else:
        log("  API 无数据, 直接走 fallback")

    build, why = pick_build(builds, want)
    if build:
        date = norm_date(build.get("date"))
        boot = find_file(build, "boot.img")
        zipf = find_file(build, "lineage-%s-%s-nightly-%s-signed.zip" % (want, date, args.device)) \
            or find_file(build, "lineage-%s-%s-nightly-%s-signed.zip" % (build.get("version"), date, args.device))
        if not boot:
            log("  ! 该 build 的 files 里没有 boot.img -> 走 fallback")
            build = None
        else:
            log("  [OK] %s: version=%s date=%s" % (why, build.get("version"), date))
            kv = {
                "SOURCE": "api",
                "LOS_VERSION": str(build.get("version")),
                "LOS_DATE": date,
                "BOOT_URL": boot.get("url") or "",
                "BOOT_URL_MIRROR": mirror_url(boot.get("url") or ""),
                "BOOT_SHA256": boot.get("sha256") or "",
                "BOOT_SIZE": boot.get("size") or "",
                "ZIP_URL": (zipf or {}).get("url") or "",
                "ZIP_SHA256": (zipf or {}).get("sha256") or "",
            }

    if not kv:
        if not fb:
            raise SystemExit("!! %s, 且未提供 --fallback-date, 无法构造下载链接" % why)
        log("  ! %s -> fallback 到日期模板 %s" % (why, fb))
        base = "https://mirrorbits.lineageos.org/full/%s/%s" % (args.device, fb)
        kv = {
            "SOURCE": "fallback",
            "LOS_VERSION": want,
            "LOS_DATE": fb,
            "BOOT_URL": "%s/boot.img" % base,
            "BOOT_URL_MIRROR": mirror_url("%s/boot.img" % base),
            "BOOT_SHA256": "",
            "BOOT_SIZE": "",
            "ZIP_URL": "%s/lineage-%s-%s-nightly-%s-signed.zip" % (base, want, fb, args.device),
            "ZIP_SHA256": "",
        }

    log("=== 解析结果 (SOURCE=%s) ===" % kv["SOURCE"])
    log("  LOS_VERSION=%s LOS_DATE=%s" % (kv["LOS_VERSION"], kv["LOS_DATE"]))
    log("  BOOT_URL=%s" % kv["BOOT_URL"])
    log("  BOOT_SHA256=%s" % kv["BOOT_SHA256"])
    emit(kv, args.json)


if __name__ == "__main__":
    main()
