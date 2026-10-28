#!/usr/bin/env python3
"""Run the supplied ARM LibreSpeed/init with original QWRT ash/UCI/procd.

All writes are confined to fixtures. Only the local HTTP backend is launched;
no host procd/account database or router is accessed.
"""
import argparse
import hashlib
import http.client
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import tempfile
import time

VERSION = '1.1.5-r5'
IPK_SHA256 = 'f907ba56b3e4593354c368a37eecf6c7283b7a8bc25cbc67117871809ca83e5f'


def verify(rootfs, qemu):
    rootfs, qemu = Path(rootfs).resolve(), Path(qemu).resolve()
    project = Path(__file__).resolve().parents[1]
    passed = []

    def check(condition, name):
        if not condition:
            raise AssertionError(name)
        passed.append(name)
        print('PASS:', name, flush=True)

    package = project / 'packages/librespeed-go_1.1.5-r5_aarch64_cortex-a53.ipk'
    check(hashlib.sha256(package.read_bytes()).hexdigest() == IPK_SHA256, 'supplied LibreSpeed IPK checksum')
    check((rootfs / 'etc/rc.d/S99librespeed-go').is_symlink() and
          os.readlink(rootfs / 'etc/rc.d/S99librespeed-go') == '../init.d/librespeed-go',
          'LibreSpeed boot link is enabled in the image')
    check('Version: ' + VERSION in (rootfs / 'usr/lib/opkg/info/librespeed-go.control').read_text(),
          'installed LibreSpeed package version')
    with tempfile.TemporaryDirectory(prefix='ap8220-librespeed-') as temporary:
        fixture = Path(temporary)
        for directory in ['etc/config', 'etc/init.d', 'etc/rc.d', 'lib/config', 'lib/functions',
                          'usr/share/libubox', 'usr/bin', 'sbin', 'bin', 'var/run', 'var/lock', 'tmp/uci', 'service']:
            (fixture / directory).mkdir(parents=True, exist_ok=True)

        def translate(text):
            return re.sub(r'/(?:etc|lib|bin|sbin|var|tmp|usr)(?=/|\b)(?:/[A-Za-z0-9_.-]+)*',
                          lambda match: str(fixture) + match.group(0), text)

        def script(relative, text):
            path = fixture / relative
            path.write_text(text)
            path.chmod(0o755)

        ash = [str(qemu), '-L', str(rootfs), str(rootfs / 'bin/busybox'), 'ash']
        env = dict(os.environ, PATH=str(fixture / 'bin') + ':' + os.environ['PATH'],
                   UCI_CONFIG_DIR=str(fixture / 'etc/config'))
        env.pop('IPKG_INSTROOT', None)
        for name in ['lib/functions.sh', 'lib/config/uci.sh', 'lib/functions/service.sh',
                     'lib/functions/procd.sh', 'usr/share/libubox/jshn.sh', 'etc/rc.common']:
            (fixture / name).write_text(translate((rootfs / name).read_text()))
        for name in ['passwd', 'shadow', 'group']:
            shutil.copy2(rootfs / 'etc' / name, fixture / 'etc' / name)
        original_accounts = {name: (fixture / 'etc' / name).read_bytes() for name in ['passwd', 'shadow', 'group']}
        shutil.copy2(rootfs / 'etc/config/librespeed-go', fixture / 'etc/config/librespeed-go')
        script('service/librespeed-go', translate((rootfs / 'etc/init.d/librespeed-go').read_text()))
        script('firstboot', translate((project / 'files/etc/uci-defaults/zzzy-ap8220-librespeed').read_text()))
        command = shlex.join([str(qemu), '-L', str(rootfs), str(rootfs / 'sbin/uci')])
        wrapper = '#!/bin/sh\nexec ' + command + ' -c ' + shlex.quote(str(fixture / 'etc/config'))
        wrapper += ' -P ' + shlex.quote(str(fixture / 'tmp/uci')) + ' "$@"\n'
        script('sbin/uci', wrapper)
        script('bin/uci', wrapper)
        script('bin/jshn', '#!/bin/sh\nexec ' + shlex.join([str(qemu), '-L', str(rootfs), str(rootfs / 'usr/bin/jshn')]) + ' "$@"\n')
        script('bin/lock', '#!/bin/sh\nexit 0\n')
        script('bin/chown', '#!/bin/sh\nprintf "%s\\n" "$*" >> ' + shlex.quote(str(fixture / 'chown.log')) + '\n')
        script('bin/ubus', '#!/bin/sh\nprintf "%s\\n" "$4" > ' + shlex.quote(str(fixture / 'service.json')) + '\n')
        script('etc/init.d/librespeed-go', '#!/bin/sh\nexec ' + shlex.join(ash + [str(fixture / 'etc/rc.common'), str(fixture / 'service/librespeed-go')]) + ' "$@"\n')

        def run(command, success=True):
            result = subprocess.run(command, env=env, text=True, capture_output=True, timeout=20)
            if success and result.returncode:
                raise AssertionError(result.stdout + result.stderr)
            return result

        def uci(*args):
            return run([str(fixture / 'bin/uci'), *args]).stdout.strip()

        # Exercise the real boot runner's success/removal contract.
        run(ash + [str(fixture / 'firstboot')])
        check([uci('get', 'librespeed-go.config.' + key) for key in ['enabled', 'listen_port', 'database_type']]
              == ['1', '8989', 'none'], 'first boot applies enabled=1 / port=8989 / database=none using actual UCI')
        check((fixture / 'etc/rc.d/S99librespeed-go').is_symlink(), 'first boot uses native rc.common enable')
        for name, before in original_accounts.items():
            after = (fixture / 'etc' / name).read_bytes()
            lines = after.splitlines(keepends=True)
            check(b''.join(line for line in lines if not line.startswith(b'librespeed:')) == before,
                  'first boot preserves all original ' + name + ' entries including root')
            check(sum(line.startswith(b'librespeed:') for line in lines) == 1, 'dedicated LibreSpeed ' + name + ' entry created once')
        passwd = next(line for line in (fixture / 'etc/passwd').read_text().splitlines() if line.startswith('librespeed:')).split(':')
        group = next(line for line in (fixture / 'etc/group').read_text().splitlines() if line.startswith('librespeed:')).split(':')
        check(passwd[2] != '0' and passwd[3] == group[2] and passwd[6] == str(fixture / 'bin/false'),
              'service account has a non-root UID, matching group and disabled login')
        first_accounts = {name: (fixture / 'etc' / name).read_bytes() for name in original_accounts}
        run(ash + [str(fixture / 'firstboot')])
        check(all((fixture / 'etc' / name).read_bytes() == data for name, data in first_accounts.items()),
              'repeated defaults do not duplicate or rewrite existing accounts')

        run([str(fixture / 'etc/init.d/librespeed-go'), 'start'])
        settings_path = fixture / 'var/run/librespeed-go/settings.json'
        settings = json.loads(settings_path.read_text())
        service = json.loads((fixture / 'service.json').read_text())
        instance = service['instances']['librespeed-go']
        check(instance['user'] == 'librespeed' and instance['command'] ==
              [str(fixture / 'usr/bin/librespeed-go'), '-c', str(settings_path)],
              'original init/procd renders correct binary, JSON path and non-root service user')
        check(settings['listen_port'] == '8989' and settings['database_type'] == 'none' and 'enabled' not in settings,
              'original init converts UCI into backend settings with no database')
        check(settings['enable_tls'] is False and settings['enable_http2'] is False,
              'original init preserves JSON boolean types')
        check('librespeed ' in (fixture / 'chown.log').read_text(), 'original init assigns settings ownership to service account')

        # Limit runtime requests to loopback and avoid the automatic location API.
        # Production port/database and all backend settings remain as generated.
        settings.update(bind_address='127.0.0.1', server_lat='1', server_lng='1')
        settings_path.write_text(json.dumps(settings))
        log_path = fixture / 'backend.log'
        with log_path.open('w') as log:
            process = subprocess.Popen([str(qemu), '-L', str(rootfs), str(rootfs / 'usr/bin/librespeed-go'),
                                        '-c', str(settings_path)], env=env, cwd=fixture, stdout=log, stderr=log)
            try:
                def request(method, path, body=None):
                    connection = http.client.HTTPConnection('127.0.0.1', 8989, timeout=5)
                    try:
                        connection.request(method, path, body=body)
                        response = connection.getresponse()
                        return response.status, response.getheader('Content-Type', ''), response.read()
                    finally:
                        connection.close()

                for attempt in range(100):
                    if process.poll() is not None:
                        raise AssertionError(log_path.read_text())
                    try:
                        page = request('GET', '/')
                        break
                    except (ConnectionError, OSError):
                        time.sleep(0.1)
                else:
                    raise AssertionError('LibreSpeed did not listen on 8989: ' + log_path.read_text())
                check(page[0] == 200 and b'<html' in page[2].lower(), 'actual ARM backend serves embedded web page on port 8989')
                javascript = request('GET', '/speedtest.js')
                check(javascript[0] == 200 and b'Speedtest' in javascript[2], 'embedded speed test JavaScript is served')
                ping = request('GET', '/backend/empty')
                check(ping[0] == 200 and len(ping[2]) == 0, 'actual backend ping endpoint')
                download = request('GET', '/backend/garbage?ckSize=1')
                check(download[0] == 200 and len(download[2]) >= 1024, 'actual backend download endpoint returns data')
                upload = request('POST', '/backend/empty', b'x' * 65536)
                check(upload[0] == 200 and len(upload[2]) == 0, 'actual backend upload endpoint accepts test data')
            finally:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
        # Model subsequent boots after the successful defaults script is removed.
        (fixture / 'firstboot').unlink()
        uci('set', 'librespeed-go.config.enabled=0')
        uci('set', 'librespeed-go.config.listen_port=8990')
        uci('commit', 'librespeed-go')
        settings_path.unlink()
        run([str(fixture / 'etc/init.d/librespeed-go'), 'start'])
        check(not settings_path.exists() and uci('get', 'librespeed-go.config.listen_port') == '8990',
              'later manual disable and port changes persist without defaults rerunning')
    return {'passed': True, 'http_tests_passed': True, 'firstboot_tests_passed': True,
            'service_user_tests_passed': True, 'version': VERSION,
            'ipk_sha256': IPK_SHA256, 'enabled': '1', 'listen_port': '8989', 'database_type': 'none',
            'pass_count': len(passed), 'checks': passed, 'hardware_tested': False,
            'http_test_scope': 'Actual supplied ARM backend with original QWRT loader/libraries, loopback only; native init/UCI/procd JSON with isolated account files and mocked ubus/chown/lock side effects.'}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--rootfs', type=Path, required=True)
    parser.add_argument('--qemu', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = verify(args.rootfs, args.qemu)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
