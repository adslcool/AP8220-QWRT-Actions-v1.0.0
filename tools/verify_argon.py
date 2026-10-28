#!/usr/bin/env python3
"""Parse Argon using the target firmware's ucode and BusyBox, without booting it."""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import tempfile


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--rootfs', type=Path, required=True)
    ap.add_argument('--qemu', type=Path, required=True)
    ap.add_argument('--output', type=Path, required=True)
    args = ap.parse_args()
    root, qemu = args.rootfs.resolve(), args.qemu.resolve()
    ucode = [str(qemu), '-L', str(root), str(root / 'usr/bin/ucode'),
             '-L', str(root / 'usr/share/ucode'), '-L', str(root / 'usr/lib/ucode')]
    passed, errors = [], []

    def check(name, argv):
        r = subprocess.run(list(map(str, argv)), text=True, capture_output=True)
        (errors if r.returncode else passed).append({'file': name, 'error': r.stderr} if r.returncode else name)

    with tempfile.TemporaryDirectory(prefix='argon-compat-') as tmp:
        temp = Path(tmp)
        templates = sorted((root / 'usr/share/ucode/luci/template/themes/argon').glob('*.ut'))
        if not templates:
            raise ValueError('No Argon templates installed')
        for p in templates:
            check(str(p.relative_to(root)), ucode + ['-T', '-c', '-o', temp / 'compiled.uc', p])
        for module in ['luci.argon-branding', 'luci.argon-palette']:
            wrapper = temp / 'module-test.uc'
            wrapper.write_text(f"import * as test from '{module}';\n")
            check(module, ucode + ['-c', '-o', temp / 'compiled.uc', wrapper])
        for p in sorted((root / 'usr/libexec/rpcd').glob('luci.argon*')):
            check(str(p.relative_to(root)), [qemu, '-L', root, root / 'bin/busybox', 'ash', '-n', p])
        node = shutil.which('node')
        if not node:
            raise ValueError('Node.js is required to check Argon JavaScript syntax')
        for p in sorted((root / 'www/luci-static/resources').rglob('*.js')):
            if 'argon' in str(p):
                check(str(p.relative_to(root)), [node, '--check', p])
    result = {'pass_count': len(passed), 'fail_count': len(errors), 'passed': passed, 'errors': errors}
    args.output.write_text(json.dumps(result, indent=2, ensure_ascii=False) + '\n')
    print(f'Argon compatibility: {len(passed)} passed, {len(errors)} failed')
    if errors:
        raise SystemExit('Argon syntax is incompatible with this firmware; see ARGON-COMPATIBILITY.json')


if __name__ == '__main__':
    main()
