#!/usr/bin/env python3
"""Repack a compatible QWRT AP8220 image without changing kernel/drivers.

Linux dependencies: squashfs-tools, gcc, qemu-user-static; Python:
PySquashfsImage==0.9.0. Does not flash or contact a router.
"""
import argparse
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tarfile
import re

from PySquashfsImage import SquashFsImage
from theme_manifest import THEMES, DEFAULT_THEME
from remove_3proxy import remove as remove_3proxy, package_records

BASELINE_SHA = '516f70c7282d186993ee6216c333cca844a0ae8203a3b75e0b6e6f44e1829d30'
VERSION = (Path(__file__).resolve().parents[1] / 'VERSION').read_text().strip()
EPOCH = 1792800000  # 2026-10-24 00:00:00 UTC, fixed for deterministic packing.
PACKAGES = {
    'ucode-mod-uloop_2025.07.18~3f64c808-r1_aarch64_cortex-a53.ipk': '2ee5554191e205aebe4e9033b686cb07c2e0b5ce16ee744554549a043af61c58',
    'librespeed-go_1.1.5-r5_aarch64_cortex-a53.ipk': 'f907ba56b3e4593354c368a37eecf6c7283b7a8bc25cbc67117871809ca83e5f',
}
PACKAGES.update({entry['file']: entry['sha256'] for entry in THEMES.values()})


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def run(argv, **kwargs):
    try:
        return subprocess.run([str(x) for x in argv], check=True, **kwargs)
    except subprocess.CalledProcessError as error:
        if error.stdout:
            print(error.stdout, file=sys.stderr)
        if error.stderr:
            print(error.stderr, file=sys.stderr)
        raise


def require(condition, message):
    if not condition:
        raise ValueError(message)


def validate_metadata(metadata):
    require('aliyun,ap8220' in metadata.get('supported_devices', []), 'Image does not support aliyun,ap8220')
    version = metadata.get('version', {})
    require(version.get('dist') == 'QWRT', 'Only QWRT QSDK images are supported by this AP profile')
    require(version.get('target') == 'ipq807x/generic', 'Unsupported target; expected QSDK ipq807x/generic')
    require(version.get('board') == 'aliyun_ap8220', 'Unsupported image board')


def validate_backend(rootfs, installed):
    for name in ['libc', 'luci-base', 'jsonfilter', 'ucode-mod-math', 'flock', 'coreutils',
                 'coreutils-timeout', 'uci', 'odhcp6c', 'luci-proto-ipv6', 'qca-hostap', 'qca-wifi-scripts', 'ucode',
                 'ucode-mod-fs', 'ucode-mod-socket', 'ucode-mod-ubus', 'ucode-mod-uci',
                 'libubox20240329', 'libubus20250102', 'libblobmsg-json20240329']:
        require(name in installed and installed[name].get('Status', '').endswith('installed'),
                f'Missing compatible built-in dependency: {name}')
    require('ap8220-ap-config' not in installed, 'Use an original QWRT image, not an already repacked AP image')
    require('usteer' not in installed and 'ucode-mod-uloop' not in installed,
            'This recipe expects an original image without preinstalled usteer/uloop')
    for package, commit in [('libubox20240329', '49056d17'), ('libubus20250102', '60e04048'),
                            ('libblobmsg-json20240329', '49056d17')]:
        require(commit in installed[package]['Version'], f'{package} requires matched headers; unexpected source version')
    require(installed['ucode']['Version'].startswith('2025-08-26-5f712ffd'),
            'The ucode/uloop combination must be validated for a different interpreter version')
    for entry in THEMES.values():
        require(entry['package'] not in installed, 'Use an original image without preinstalled ' + entry['package'])
    require('librespeed-go' not in installed, 'Use an original image without preinstalled librespeed-go')
    require(installed.get('libgcc1', {}).get('Status', '').endswith('installed') and
            (rootfs / 'lib/libgcc_s.so.1').is_file(),
            'LibreSpeed requires the original libgcc runtime')
    for name in ['luci-theme-aurora', 'luci-i18n-argon-config-zh-cn']:
        require(name not in installed, 'Use an original image without removed package ' + name)
    require('wget' in installed or any('wget' in p.get('Provides', '').split(', ') for p in installed.values()),
            'Original firmware must provide wget')
    require(installed['libc'].get('Architecture') == 'aarch64_cortex-a53', 'Unsupported userspace architecture')
    hostapd = (rootfs / 'lib/wifi/hostapd.sh').read_text()
    for option in ['ieee80211r', 'ft_psk_generate_local', 'rrm_neighbor_report', 'rrm_beacon_report', 'bss_transition']:
        require(option in hostapd, f'QCA hostapd generator lacks required option: {option}')
    network = (rootfs / 'etc/board.d/02_network').read_text()
    import re
    require(re.search(r'aliyun,ap8220\)\s*ucidef_set_interfaces_lan_wan\s+"eth1"\s+"eth0"', network),
            'AP8220 network defaults differ from the verified eth1/eth0 layout')


def tar_bytes(items):
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode='w', format=tarfile.GNU_FORMAT) as tf:
        for name, data, mode in items:
            ti = tarfile.TarInfo(name)
            ti.size, ti.mode, ti.uid, ti.gid, ti.mtime = len(data) if data is not None else 0, mode, 0, 0, EPOCH
            if data is None:
                ti.type = tarfile.DIRTYPE
                tf.addfile(ti)
            else:
                tf.addfile(ti, io.BytesIO(data))
    return gzip.compress(out.getvalue(), mtime=0)


def create_config_ipk(project):
    contents = []
    for p in sorted((project / 'files').rglob('*')):
        rel = p.relative_to(project / 'files').as_posix()
        if p.is_dir():
            contents.append(('./' + rel, None, 0o755))
        elif p.is_file():
            mode = (0o600 if rel in ['etc/config/ap8220_ap', 'etc/ap8220-root-password.hash'] else
                    0o755 if rel.startswith(('usr/sbin/', 'etc/init.d/', 'etc/uci-defaults/')) else 0o644)
            contents.append(('./' + rel, p.read_bytes(), mode))
    control = f'''Package: ap8220-ap-config
Version: {VERSION}
Depends: libc, uci, flock, odhcp6c, luci-proto-ipv6, luci-theme-argon, luci-theme-footstrap, qca-hostap, usteer, ucode-mod-fs, ucode-mod-socket, ucode-mod-ubus, ucode-mod-uci, ucode-mod-uloop, librespeed-go
Architecture: all
Section: net
Maintainer: AP8220 local customization
Description: QWRT AP8220 AP; Footstrap default, Argon, DHCPv6, k/v/r, experimental QCA usteer bridge
'''.encode()
    postinst = b'''#!/bin/sh
[ "${IPKG_NO_SCRIPT}" = "1" ] && exit 0
[ -s ${IPKG_INSTROOT}/lib/functions.sh ] || exit 0
. ${IPKG_INSTROOT}/lib/functions.sh
default_postinst "$0" "$@"
'''
    controls = [('./control', control, 0o644), ('./conffiles', b'/etc/config/ap8220_ap\n/etc/config/usteer\n', 0o644),
                ('./postinst', postinst, 0o755)]
    package = project / 'packages' / f'ap8220-ap-config_{VERSION}_all.ipk'
    package.write_bytes(tar_bytes([('./debian-binary', b'2.0\n', 0o644),
                                  ('./control.tar.gz', tar_bytes(controls), 0o644),
                                  ('./data.tar.gz', tar_bytes(contents), 0o644)]))
    return package


def snapshot(image):
    result = {}
    with SquashFsImage.from_file(str(image)) as im:
        for f in im.root.riter():
            if f.is_file:
                result[f.path] = ('file', f.mode, hashlib.sha256(im.read_file(f.inode)).hexdigest())
            elif f.is_symlink:
                result[f.path] = ('symlink', f.mode, f.inode.symlink)
            elif f.is_char_device or f.is_block_device:
                result[f.path] = ('device', f.mode)
            elif f.is_dir:
                result[f.path] = ('directory', f.mode)
    return result


def restore_original_modes(rootfs, original):
    """Restore source permission bits without following or changing symlinks.

    Non-root unsquashfs applies umask to regular files and removes setuid /
    setgid bits. Package directory entries may also differ from the input.
    Restore permissions only; subsequent snapshot comparison still checks bytes.
    """
    rootfs = Path(rootfs)
    counts = {'files_checked': 0, 'directories_checked': 0, 'modes_restored': 0}
    restored = []
    for name, entry in original.items():
        kind = entry[0]
        if kind not in ['file', 'directory']:
            continue
        path = rootfs / name.lstrip('/')
        actual = path.lstat()
        correct_type = stat.S_ISREG(actual.st_mode) if kind == 'file' else stat.S_ISDIR(actual.st_mode)
        require(correct_type, f'Original path type changed before permission restoration: {name}')
        expected_mode = stat.S_IMODE(entry[1])
        actual_mode = stat.S_IMODE(actual.st_mode)
        counts['files_checked' if kind == 'file' else 'directories_checked'] += 1
        if actual_mode != expected_mode:
            os.chmod(path, expected_mode)
            require(stat.S_IMODE(path.lstat().st_mode) == expected_mode,
                    f'Could not restore original permissions: {name}')
            counts['modes_restored'] += 1
            restored.append({'path': name, 'before': f'{actual_mode:04o}', 'after': f'{expected_mode:04o}'})
    return {**counts, 'restored': restored}


def compare_original_entries(original, rebuilt, allowed_changes=(), allowed_removals=()):
    """Categorize differences; do not waive content, mode or link changes."""
    allowed = set(allowed_changes)
    removed = set(allowed_removals)
    missing = sorted(set(original) - set(rebuilt) - removed)
    changed = []
    for path, old in original.items():
        if path in allowed or path not in rebuilt or old == rebuilt[path]:
            continue
        new = rebuilt[path]
        fields = []
        if old[0] != new[0]:
            fields.append('type')
        if stat.S_IMODE(old[1]) != stat.S_IMODE(new[1]):
            fields.append('mode')
        if len(old) > 2 and (len(new) < 3 or old[2] != new[2]):
            fields.append('target' if old[0] == 'symlink' else 'content')
        changed.append({'path': path, 'fields': fields,
                        'expected_mode': f'{stat.S_IMODE(old[1]):04o}',
                        'actual_mode': f'{stat.S_IMODE(new[1]):04o}'})
    return {'missing_paths': missing, 'changed_paths': sorted(changed, key=lambda item: item['path']),
            'checked_paths': len(original), 'allowed_changed_paths': sorted(allowed),
            'allowed_removed_paths': sorted(removed),
            'removed_paths': sorted(removed & (set(original) - set(rebuilt))),
            'unremoved_paths': sorted(removed & set(rebuilt))}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--input', type=Path, required=True)
    ap.add_argument('--work', type=Path, required=True)
    ap.add_argument('--output', type=Path, help='Output directory (default: dist)')
    ap.add_argument('--sha256', default='', help='Optional expected source SHA256')
    ap.add_argument('--download-record', type=Path, help='Optional sanitized downloader JSON record')
    ap.add_argument('--tool-dir', type=Path, help='Optional directory containing host executables')
    ap.add_argument('--toolchain', type=Path, help='Optional OpenWrt aarch64 musl gcc wrapper (otherwise downloads the pinned toolchain)')
    ap.add_argument('--json-include', type=Path, help='Optional json-c development include directory')
    ap.add_argument('--skip-ipc-test', action='store_true', help='Local inspection only: cannot publish a Release with this flag')
    args = ap.parse_args()
    project = Path(__file__).resolve().parents[1]
    from configure_profile import HASH_PATTERN
    root_hash_file = project / 'files/etc/ap8220-root-password.hash'
    if root_hash_file.exists():
        require(re.fullmatch(HASH_PATTERN, root_hash_file.read_text().strip()) is not None,
                'Invalid optional first-boot root password hash')
    password_policy = 'custom-first-boot' if root_hash_file.exists() else 'inherit-input-firmware'
    customization_file = project / 'files/etc/ap8220-build-profile.json'
    customization = json.loads(customization_file.read_text()) if customization_file.exists() else {'source': 'project-files'}
    require(customization.get('password_policy', password_policy) == password_policy,
            'Customization summary does not match the root password policy')
    args.input, args.work = args.input.resolve(), args.work.resolve()
    source_sha = sha(args.input)
    if args.sha256:
        require(len(args.sha256) == 64 and all(c in '0123456789abcdefABCDEF' for c in args.sha256), 'Invalid expected SHA256')
        require(source_sha == args.sha256.lower(), 'Source firmware SHA256 mismatch')
    assert not args.work.exists(), 'Use a new empty work path; never overwrite an existing work tree'
    args.work.mkdir(parents=True)
    firmware_dir = args.output.resolve() if args.output else project / 'dist'
    require(not firmware_dir.exists() or not any(firmware_dir.iterdir()), 'Output directory must be empty')
    firmware_dir.mkdir(parents=True, exist_ok=True)
    tools = {}
    for name in ['unsquashfs', 'mksquashfs', 'qemu-aarch64-static', 'gcc']:
        candidate = args.tool_dir / name if args.tool_dir else None
        tools[name] = str(candidate) if candidate and candidate.exists() else shutil.which(name)
        assert tools[name], f'Missing tool: {name}'
    fwtool = args.work / 'fwtool'
    run([tools['gcc'], '-O2', '-Wall', project / 'tools/fwtool/fwtool.c', '-o', fwtool])
    original_meta = args.work / 'original-metadata.json'
    run([fwtool, '-i', original_meta, args.input])
    metadata = json.loads(original_meta.read_text())
    validate_metadata(metadata)
    original_release = dict(metadata['version'])
    with tarfile.open(args.input, 'r:') as tf:
        members = tf.getmembers()
        expected = {'sysupgrade-aliyun_ap8220', 'sysupgrade-aliyun_ap8220/CONTROL',
                    'sysupgrade-aliyun_ap8220/kernel', 'sysupgrade-aliyun_ap8220/root'}
        require({m.name.rstrip('/') for m in members} == expected, 'Unexpected sysupgrade archive layout')
        for name in ['kernel', 'root']:
            member = tf.getmember('sysupgrade-aliyun_ap8220/' + name)
            require(member.isfile(), f'{name} is not a regular archive member')
            data = tf.extractfile(member).read()
            (args.work / name).write_bytes(data)
    with SquashFsImage.from_file(str(args.work / 'root')) as source_fs:
        require(source_fs.sblk.compression == 4, 'This recipe currently supports XZ-compressed QSDK roots only')
        require(source_fs.sblk.no_ids == 1 and source_fs.root.uid == 0 and source_fs.root.gid == 0,
                'This recipe expects original QWRT files owned by root; a different ownership layout needs adaptation')
    rootfs = args.work / 'rootfs'
    unpack = subprocess.run([tools['unsquashfs'], '-processors', '1', '-no-xattrs', '-no-progress',
                             '-d', str(rootfs), str(args.work / 'root')], text=True, capture_output=True,
                            umask=0)
    assert unpack.returncode in [0, 2], unpack.stderr
    # The exact input contains one device node. Unprivileged extraction cannot
    # create it; mksquashfs restores it explicitly without needing mknod.
    old_snapshot = snapshot(args.work / 'root')
    devices = {k: v for k, v in old_snapshot.items() if v[0] == 'device'}
    assert set(devices) == {'/dev/console'}, f'Unexpected device nodes: {devices}'
    missing = [p for p, v in old_snapshot.items() if v[0] != 'device' and
               not os.path.lexists(rootfs / p.lstrip('/'))]
    assert not missing, f'Extraction lost paths: {missing[:10]}'
    # Verify every dependency from the original installed package database.
    status_text = (rootfs / 'usr/lib/opkg/status').read_text()
    installed = {}
    for paragraph in status_text.split('\n\n'):
        fields = dict(line.split(': ', 1) for line in paragraph.splitlines() if ': ' in line and not line.startswith(' '))
        if 'Package' in fields:
            installed[fields['Package']] = fields
    validate_backend(rootfs, installed)
    for name, expected in PACKAGES.items():
        assert sha(project / 'packages' / name) == expected, name
    from build_usteer import build as build_usteer, PACKAGE_VERSION
    executable = build_usteer(project, rootfs, args.work / 'usteer-build', args.toolchain, args.json_include)
    loader = subprocess.run([tools['qemu-aarch64-static'], '-L', str(rootfs), str(executable), '-h'],
                            text=True, capture_output=True)
    require(loader.returncode == 1 and 'Usage:' in loader.stdout + loader.stderr and
            'Error relocating' not in loader.stderr, 'usteer cannot load with the original firmware libraries')
    usteer_control = f'''Package: usteer
Version: {PACKAGE_VERSION}
Depends: libc, libubox20240329, libubus20250102, libblobmsg-json20240329
Architecture: aarch64_cortex-a53
Section: net
License: GPL-2.0-only
Description: Experimental AP8220 QCA control backend of OpenWrt usteer
'''.encode()
    usteer_ipk = project / 'packages' / f'usteer_{PACKAGE_VERSION}_aarch64_cortex-a53.ipk'
    usteer_ipk.write_bytes(tar_bytes([('./debian-binary', b'2.0\n', 0o644),
        ('./control.tar.gz', tar_bytes([('./control', usteer_control, 0o644)]), 0o644),
        ('./data.tar.gz', tar_bytes([('./usr/sbin/usteerd', executable.read_bytes(), 0o755)]), 0o644)]))
    own_ipk = create_config_ipk(project)
    (rootfs / 'tmp/lock').mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, IPKG_NO_SCRIPT='1')
    run([tools['qemu-aarch64-static'], '-L', rootfs, rootfs / 'bin/opkg', '-o', rootfs,
         '--force-postinstall', 'install', *(project / 'packages' / p for p in PACKAGES), usteer_ipk, own_ipk], env=env)
    for source_file in (project / 'files').rglob('*'):
        if source_file.is_file():
            installed_file = rootfs / source_file.relative_to(project / 'files')
            require(installed_file.is_file() and installed_file.read_bytes() == source_file.read_bytes(),
                    f'Offline package installation lost or changed {source_file.relative_to(project / "files")}')
    status_path = rootfs / 'usr/lib/opkg/status'
    paragraphs = status_path.read_text().split('\n\n')
    new_names = {'ap8220-ap-config', 'ucode-mod-uloop', 'usteer', 'librespeed-go'}
    new_names.update(entry['package'] for entry in THEMES.values())
    for i, paragraph in enumerate(paragraphs):
        if paragraph.splitlines() and paragraph.splitlines()[0] in {'Package: ' + name for name in new_names}:
            lines = [f'Installed-Time: {EPOCH}' if line.startswith('Installed-Time: ') else line for line in paragraph.splitlines()]
            paragraphs[i] = '\n'.join(lines)
    status_path.write_text('\n\n'.join(paragraphs))
    rc_link = rootfs / 'etc/rc.d/S99ap8220-ap-init'
    rc_link.symlink_to('../init.d/ap8220-ap-init')
    for start, name in [(49, 'ap8220-usteer-bridge'), (50, 'usteer'), (99, 'librespeed-go')]:
        (rootfs / f'etc/rc.d/S{start:02d}{name}').symlink_to('../init.d/' + name)
    require(sorted(p.name for p in (rootfs / 'etc/uci-defaults').iterdir())[-1] == 'zzzz-ap8220-ap',
            'Source has a later first-boot defaults script; adapt its ordering before building')
    # Do not bake ephemeral lock files into the firmware.
    for p in (rootfs / 'tmp').iterdir():
        if p.is_dir() and not p.is_symlink():
            shutil.rmtree(p)
        else:
            p.unlink()
    # Preserve original timestamps; use a fixed timestamp for added entries.
    with SquashFsImage.from_file(str(args.work / 'root')) as original:
        times = {f.path: f.time for f in original.root.riter()}
        directory_modes = {f.path: stat.S_IMODE(f.mode) for f in original.root.riter() if f.is_dir}
        root_time = original.root.time
    for p in rootfs.rglob('*'):
        rel = '/' + p.relative_to(rootfs).as_posix()
        timestamp = times.get(rel, EPOCH)
        if rel == '/usr/lib/opkg/status':
            timestamp = EPOCH
        os.utime(p, (timestamp, timestamp), follow_symlinks=False)
    os.utime(rootfs, (root_time, root_time))
    report_path = args.work / 'profile-tests.txt'
    test = run([sys.executable, project / 'tests/test_profile.py', '--rootfs', rootfs,
                '--qemu', tools['qemu-aarch64-static'], '--crypt-probe', executable.with_name('verify-password')],
               text=True, capture_output=True)
    report_path.write_text(test.stdout)
    print(test.stdout, end='')
    librespeed_report_path = firmware_dir / 'LIBRESPEED-COMPATIBILITY.json'
    run([sys.executable, project / 'tests/test_librespeed.py', '--rootfs', rootfs,
         '--qemu', tools['qemu-aarch64-static'], '--output', librespeed_report_path])
    librespeed_report = json.loads(librespeed_report_path.read_text())
    ucode = [tools['qemu-aarch64-static'], '-L', rootfs, rootfs / 'usr/bin/ucode',
             '-L', rootfs / 'usr/lib/ucode', '-L', rootfs / 'usr/share/ucode']
    for script in ['usr/sbin/ap8220-usteer-bridge.uc', 'usr/share/ap8220/usteer-config.uc']:
        run(ucode + ['-c', '-o', args.work / 'usteer-compiled.uc', rootfs / script])
    native = run(ucode + [project / 'tests/native_modules.uc'], text=True, capture_output=True)
    print(native.stdout, end='')
    protocol = run(ucode[:4] + ['-L', project / 'tests/ucode-mocks'] + ucode[4:] +
                   [rootfs / 'usr/sbin/ap8220-usteer-bridge.uc'], text=True, capture_output=True)
    (args.work / 'usteer-protocol-tests.txt').write_text(protocol.stdout)
    print(protocol.stdout, end='')
    ipc_test = None
    if not args.skip_ipc_test:
        ipc_command = [sys.executable, project / 'tests/test_usteer.py', '--rootfs', rootfs,
                       '--qemu', tools['qemu-aarch64-static'], '--usteerd', rootfs / 'usr/sbin/usteerd']
        # The original ubusd only grants object publication to UID 0 without ACLs.
        # GitHub's runner offers passwordless sudo; all test sockets remain private.
        if os.geteuid() != 0:
            ipc_command = ['sudo', '-n'] + ipc_command
        ipc_test = run(ipc_command, text=True, capture_output=True)
        (args.work / 'usteer-ipc-tests.txt').write_text(ipc_test.stdout)
        print(ipc_test.stdout, end='')
    compatibility = firmware_dir / 'ARGON-COMPATIBILITY.json'
    themes_compatibility = firmware_dir / 'THEMES-COMPATIBILITY.json'
    run([sys.executable, project / 'tools/verify_themes.py', '--rootfs', rootfs,
         '--qemu', tools['qemu-aarch64-static'], '--output', themes_compatibility,
         '--argon-output', compatibility])
    # Guest UCI/template checks may recreate their global lock directory.
    # Keep every transient entry out of the packed /tmp tree.
    for p in (rootfs / 'tmp').iterdir():
        if p.is_dir() and not p.is_symlink():
            shutil.rmtree(p)
        else:
            p.unlink()
    os.utime(rootfs / 'tmp', (times['/tmp'], times['/tmp']))
    # Restore source modes after all tool/guest access, immediately before pack.
    # Includes special bits stripped by non-root extraction. Content and link
    # verification remain strict; this routine never writes original bytes.
    # All guest checks finish before the final package transaction. Freeze the
    # validated tree under a fresh path and never run package scripts here.
    final_rootfs = args.work / 'rootfs-final'
    rootfs.rename(final_rootfs)
    rootfs = final_rootfs
    permission_restore = restore_original_modes(rootfs, old_snapshot)
    (args.work / 'original-permissions.json').write_text(json.dumps(permission_restore, indent=2) + '\n')
    proxy_removal = remove_3proxy(rootfs)
    changed_directories = {str(Path(name).parent) for name in proxy_removal['removed_paths']}
    for name in changed_directories | {'/usr/lib/opkg/status'}:
        os.utime(rootfs / name.lstrip('/'), (EPOCH + 1, EPOCH + 1), follow_symlinks=False)
    require(all(not os.path.lexists(rootfs / name.lstrip('/')) for name in proxy_removal['removed_paths']),
            '3proxy cleanup did not persist before packing')
    new_root = args.work / 'root.ap'
    run([tools['mksquashfs'], rootfs, new_root, '-noappend', '-comp', 'xz', '-b', '1048576',
         '-processors', '2', '-no-progress', '-no-xattrs', '-all-root', '-mkfs-time', str(EPOCH),
         '-p', f'dev/console C {times["/dev/console"]} 0600 0 0 5 1'])
    new_snapshot = snapshot(new_root)
    with SquashFsImage.from_file(str(new_root)) as packed:
        packed_status = next(f for f in packed.root.riter() if f.path == '/usr/lib/opkg/status')
        packed_packages = {record['Package'] for record in package_records(packed.read_file(packed_status.inode).decode())}
    removed_packages_absent = not (packed_packages & {'3proxy', 'luci-theme-aurora', 'luci-i18n-argon-config-zh-cn'})
    require(removed_packages_absent, 'Removed package record still present in packed image')
    comparison = compare_original_entries(old_snapshot, new_snapshot, ['/usr/lib/opkg/status'], proxy_removal['removed_paths'])
    (firmware_dir / 'ORIGINAL-FILES-CHECK.json').write_text(json.dumps(comparison, indent=2) + '\n')
    require(not comparison['missing_paths'] and not comparison['changed_paths'] and not comparison['unremoved_paths'],
            f'Original file verification failed: missing={len(comparison["missing_paths"])}, '
            f'changed={len(comparison["changed_paths"])}; '
            f'examples={comparison["changed_paths"][:8]}; details: ORIGINAL-FILES-CHECK.json')
    assert new_snapshot['/dev/console'] == old_snapshot['/dev/console']
    metadata['version']['revision'] += f'+ap8220-ap-{VERSION}'
    metadata['ap8220_ap'] = {'version': VERSION, 'base_sha256': source_sha, 'argon': '3.1.0',
                             'default_theme': DEFAULT_THEME,
                             'themes': {name: entry['version'] for name, entry in THEMES.items()},
                             'usteer': 'experimental-qca-control', 'password_policy': password_policy,
                             'hardware_tested': False}
    meta_path = firmware_dir / 'image-metadata.json'
    meta_path.write_text(json.dumps(metadata, ensure_ascii=False, separators=(',', ':')) + '\n')
    image = firmware_dir / f'QWRT-AP8220-AP-v{VERSION}-{source_sha[:8]}-sysupgrade.bin'
    with tarfile.open(image, 'w', format=tarfile.GNU_FORMAT) as tf:
        directory = tarfile.TarInfo('sysupgrade-aliyun_ap8220')
        directory.type, directory.mode, directory.mtime = tarfile.DIRTYPE, 0o755, EPOCH
        tf.addfile(directory)
        for name, source in [('CONTROL', None), ('kernel', args.work / 'kernel'), ('root', new_root)]:
            data = source.read_bytes() if source else b'BOARD=aliyun_ap8220\n'
            ti = tarfile.TarInfo('sysupgrade-aliyun_ap8220/' + name)
            ti.size, ti.mode, ti.mtime = len(data), 0o644, EPOCH
            tf.addfile(ti, io.BytesIO(data))
    run([fwtool, '-I', meta_path, image])
    checked_meta = args.work / 'checked-metadata.json'
    run([fwtool, '-i', checked_meta, image])
    assert json.loads(checked_meta.read_text()) == metadata
    own_fwtool = next((p for p in [rootfs / 'usr/bin/fwtool', rootfs / 'sbin/fwtool', rootfs / 'bin/fwtool'] if p.is_file()), None)
    require(own_fwtool is not None, 'Original firmware lacks fwtool')
    own_checked = args.work / 'qwrts-own-checked-metadata.json'
    run([tools['qemu-aarch64-static'], '-L', rootfs, own_fwtool, '-i', own_checked, image])
    require(json.loads(own_checked.read_text()) == metadata, 'Original fwtool rejected the new image metadata')
    with tarfile.open(image, 'r:') as tf:
        kernel_sha = hashlib.sha256(tf.extractfile('sysupgrade-aliyun_ap8220/kernel').read()).hexdigest()
    assert kernel_sha == sha(args.work / 'kernel')
    result = {'version': VERSION, 'hardware_tested': False, 'publisher_signature_preserved': False,
              'base_image_sha256': source_sha, 'baseline_source_matches_user_tested_image': source_sha == BASELINE_SHA,
              'base_release': original_release, 'output_image': image.name, 'output_size': image.stat().st_size,
              'output_sha256': sha(image), 'kernel_sha256': kernel_sha,
              'kernel_byte_identical': True,
              'old_files_and_symlinks_checked': sum(v[0] != 'directory' for v in old_snapshot.values()),
              'original_directories_checked': len(directory_modes), 'original_directory_modes_preserved': True,
              'original_file_modes_preserved': True,
              'original_permission_restore_counts': {k: v for k, v in permission_restore.items() if k != 'restored'},
              'unexpected_changed_or_missing_original_paths': [], 'allowed_changed_path': '/usr/lib/opkg/status',
              'proxy_removal': proxy_removal,
              'expected_removed_original_paths': comparison['allowed_removed_paths'],
              'added_files_and_symlinks': sum(new_snapshot[p][0] != 'directory' for p in set(new_snapshot) - set(old_snapshot)),
              'uci_integration_tests': test.stdout.strip().splitlines(),
              'fwtool_metadata_crc_verified': True, 'original_firmware_fwtool_crc_verified': True,
              'first_boot_defaults_last': 'zzzz-ap8220-ap',
              'password_policy': password_policy,
              'custom_root_password_firstboot_test_passed': any('custom root hash applied once' in line for line in test.stdout.splitlines()),
              'network_migration_tests_passed': any('LuCI migration predicates clear' in line for line in test.stdout.splitlines()),
              'customization': customization,
              'default_theme': DEFAULT_THEME,
              'themes': THEMES,
              'themes_compatibility_passed': json.loads(themes_compatibility.read_text())['fail_count'] == 0,
              'theme_default_tests_passed': any('Footstrap default and two theme choices' in line for line in test.stdout.splitlines()),
              'wifi_security_tests_passed': any('psk-mixed automatic cipher' in line for line in test.stdout.splitlines()),
              'ipv6_client_tests_passed': any('lan_v6 DHCPv6 on br-lan' in line for line in test.stdout.splitlines()),
              'wifi_encryption': 'psk-mixed',
              'ipv6_client': {'interface': 'lan_v6', 'proto': 'dhcpv6', 'device': 'br-lan'},
              'librespeed': librespeed_report,
              'removed_packages_absent': removed_packages_absent,
              'account_files_byte_identical': all(old_snapshot.get(p) == new_snapshot.get(p)
                                                  for p in ['/etc/shadow', '/etc/passwd', '/etc/group']),
              'usteer_included': True, 'usteer_adaptation': 'experimental-qca-control',
              'usteer_original_library_loader_check_passed': True,
              'usteer_native_modules_test': native.stdout.strip(),
              'usteer_protocol_tests': protocol.stdout.strip().splitlines(),
              'usteer_ipc_tests_passed': ipc_test is not None,
              'usteer_ipc_tests': ipc_test.stdout.strip().splitlines() if ipc_test else [],
              'usteer_hardware_tested': False,
              'argon_syntax_checks_passed': json.loads(compatibility.read_text())['pass_count'],
              'rootfs_bytes_original': (args.work / 'root').stat().st_size,
              'rootfs_bytes_modified': new_root.stat().st_size}
    if args.download_record:
        download = json.loads(args.download_record.read_text())
        require(download['sha256'] == source_sha, 'Download record does not match input bytes')
        from download_firmware import sanitized_url
        download['source_url'] = sanitized_url(download['source_url'])
        result['download'] = download
    (firmware_dir / 'BUILD-REPORT.json').write_text(json.dumps(result, indent=2, ensure_ascii=False) + '\n')
    (firmware_dir / 'SHA256SUMS').write_text(f'{sha(image)}  {image.name}\n')
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == '__main__':
    main()
