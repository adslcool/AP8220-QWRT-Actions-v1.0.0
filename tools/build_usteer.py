#!/usr/bin/env python3
"""Compile upstream usteer with the QCA control backend against QWRT's ABI.

Link-time libraries are built from the exact libubox/ubus sources because the
input image's sstrip'ed libraries cannot be consumed by a linker. These build
libraries are NEVER installed; the executable runs with the original ROM libs.
"""
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import tarfile

COMMIT = '7532e1252fb937fe34b6da05e86fd34babd91ef5'
PACKAGE_VERSION = '2026.05.19~7532e1252f-ap8220.1'
TOOLCHAIN_NAME = 'openwrt-toolchain-24.10.6-qualcommax-ipq807x_gcc-13.3.0_musl.Linux-x86_64.tar.zst'
TOOLCHAIN_URL = 'https://downloads.openwrt.org/releases/24.10.6/targets/qualcommax/ipq807x/' + TOOLCHAIN_NAME
TOOLCHAIN_SHA256 = '7cc53103a968bb8eb87f162af48dbb223b71119fc301c76aad568a718a61f2be'
SOURCES = 'main.c local_node.c node.c sta.c policy.c ubus.c remote.c parse.c netifd.c timeout.c event.c measurement.c band_steering.c'.split()
ARCHIVES = {
    f'upstream-{COMMIT}.tar.gz': '070d3ce88f45d935dcf93baf3179cdcbbbfc6a18cb374e02d1f30921b7aa8856',
    'libubox-49056d17.tar.gz': 'b63d252b1eeb055fac151c171f37c19b1e5ca6ede81e868d9301ab2e9f568d5d',
    'ubus-60e04048.tar.gz': 'f53683262123bd6f09b99db1d2da2bc3bd52630c03638d24ae1c6ce89e0cbf1d',
}


def run(argv, **kwargs):
    return subprocess.run(list(map(str, argv)), check=True, **kwargs)


def build(project, rootfs, work, compiler=None, json_include=None):
    work.mkdir(parents=True)
    vendor = project / 'vendor/usteer'
    if compiler is None:
        archive = work / TOOLCHAIN_NAME
        run(['curl', '--fail', '--silent', '--show-error', '--location', '--retry', '3',
             '--connect-timeout', '15', '--max-time', '300', TOOLCHAIN_URL, '-o', archive])
        if hashlib.sha256(archive.read_bytes()).hexdigest() != TOOLCHAIN_SHA256:
            raise ValueError('OpenWrt cross-toolchain checksum mismatch')
        run(['tar', '--zstd', '--no-same-owner', '-xf', archive, '-C', work])
        compiler = next(work.glob('openwrt-toolchain-*/toolchain-*/bin/aarch64-openwrt-linux-musl-gcc'))
    # The toolchain wrapper selects gcc/strip by argv[0]; keep its symlink name.
    compiler = compiler.absolute()
    include = work / 'include'
    include.mkdir()
    for name, expected in ARCHIVES.items():
        if hashlib.sha256((vendor / name).read_bytes()).hexdigest() != expected:
            raise ValueError('Vendored source checksum mismatch: ' + name)
        with tarfile.open(vendor / name) as tf:
            tf.extractall(work, filter='data')
    box = work / 'libubox-49056d17'
    bus = work / 'ubus-60e04048'
    source = work / ('usteer-' + COMMIT)
    run(['patch', '-s', '-p1', '-i', vendor / '100-qca-control-backend.patch'], cwd=source)
    (include / 'libubox').symlink_to(box, target_is_directory=True)
    env = dict(os.environ, STAGING_DIR=str(compiler.parents[2]))
    json_include = Path(json_include or '/usr/include/json-c')
    if not (json_include / 'json.h').exists():
        raise ValueError('Install libjson-c-dev or set --json-include to its headers')
    libs = work / 'link-only-libraries'
    libs.mkdir()
    base = [compiler, '-Os', '-std=gnu99', '-fPIC', '-shared', '-I' + str(include), '-I' + str(bus)]
    box_files = 'avl.c avl-cmp.c blob.c blobmsg.c uloop.c usock.c ustream.c ustream-fd.c vlist.c utils.c safe_list.c runqueue.c md5.c kvlist.c ulog.c base64.c udebug.c udebug-remote.c'.split()
    libbox = libs / 'libubox.so.20240329'
    libbus = libs / 'libubus.so.20250102'
    libjson = libs / 'libblobmsg_json.so.20240329'
    run(base + [box / f for f in box_files] + ['-Wl,-soname,' + libbox.name, '-o', libbox], env=env)
    bus_files = 'libubus.c libubus-io.c libubus-obj.c libubus-sub.c libubus-req.c libubus-acl.c'.split()
    run(base + ['-DUBUS_UNIX_SOCKET="/var/run/ubus/ubus.sock"', '-DUBUS_MAX_MSGLEN=1048576'] +
        [bus / f for f in bus_files] + [libbox, '-Wl,-soname,' + libbus.name, '-o', libbus], env=env)
    run(base + ['-I' + str(json_include), box / 'blobmsg_json.c', libbox,
                '-Wl,-soname,' + libjson.name, '-o', libjson], env=env)
    executable = work / 'usteerd'
    run([compiler, '-Os', '-Wall', '-Werror', '--std=gnu99',
         '-ffile-prefix-map=' + str(work) + '=/build', '-I' + str(include), '-I' + str(bus),
         '-Wl,--allow-shlib-undefined'] + [source / f for f in SOURCES] +
        [libbox, libbus, libjson, '-o', executable], env=env)
    strip = compiler.with_name('aarch64-openwrt-linux-musl-strip')
    run([strip, executable])
    # Used only by isolated tests, never installed in the firmware.
    run([compiler, '-Os', '-Wall', '-Werror', project / 'tests/verify_password.c',
         '-lcrypt', '-o', work / 'verify-password'], env=env)
    return executable
