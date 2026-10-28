#!/usr/bin/env python3
"""Remove only 3proxy from an offline root; never run package scripts."""
import os
from pathlib import Path
import re
import stat
import tempfile

OWNED_PATHS = frozenset({
    '/etc/config/3proxy', '/etc/init.d/3proxy',
    '/lib/upgrade/keep.d/3proxy', '/usr/bin/3proxy',
})
METADATA_PATHS = frozenset('/usr/lib/opkg/info/3proxy.' + suffix
                           for suffix in ['conffiles', 'control', 'list', 'prerm'])


def require(condition, message):
    if not condition:
        raise ValueError(message)


def fields(paragraph):
    return dict(line.split(': ', 1) for line in paragraph.splitlines()
                if ': ' in line and not line.startswith((' ', '\t')))


def package_records(text):
    return [fields(part) for part in re.split(r'\n\n+', text) if 'Package' in fields(part)]


def remove(rootfs):
    root = Path(rootfs).resolve(strict=True)

    def safe(name):
        path = root / name.lstrip('/')
        require(path.parent.resolve().is_relative_to(root), 'Path escapes offline root: ' + name)
        return path

    status_path = safe('/usr/lib/opkg/status')
    require(stat.S_ISREG(status_path.lstat().st_mode), 'Package status must be a regular file')
    text = status_path.read_text()
    chunks = re.split(r'(\n\n+)', text)
    records = [(i, fields(chunks[i])) for i in range(0, len(chunks), 2)]
    matching = [(i, rec) for i, rec in records if rec.get('Package') == '3proxy']
    require(len(matching) <= 1, 'Duplicate 3proxy package records')
    paths = {name for name in OWNED_PATHS | METADATA_PATHS if os.path.lexists(safe(name))}
    info = safe('/usr/lib/opkg/info')
    require(not info.is_symlink(), 'Package info directory must not be a symlink')
    metadata = {'/usr/lib/opkg/info/' + p.name for p in info.glob('3proxy.*')}
    require(metadata <= METADATA_PATHS, 'Unknown 3proxy metadata; adapt before removing')
    rc = safe('/etc/rc.d')
    require(not rc.is_symlink(), 'Startup directory must not be a symlink')
    for link in rc.iterdir():
        if re.fullmatch(r'[SK][0-9]{2}3proxy', link.name):
            require(link.is_symlink() and os.readlink(link) == '../init.d/3proxy',
                    'Unexpected 3proxy startup target')
            paths.add('/etc/rc.d/' + link.name)
    report = {'package': '3proxy', 'version': matching[0][1].get('Version') if matching else None,
              'present_before': bool(matching), 'absent_after': True, 'removed_paths': sorted(paths),
              'preserved_optional_consumer': 'luci-app-ssr-plus', 'consumer_configuration_changed': False}
    if not matching:
        require(not paths, 'Orphan 3proxy files without a package record')
        return report
    index, record = matching[0]
    require(record.get('Status', '').endswith(' installed'), '3proxy is not cleanly installed')
    for _, other in records:
        if other.get('Package') == '3proxy' or not other.get('Status', '').endswith(' installed'):
            continue
        for key in ['Depends', 'Pre-Depends']:
            require(not re.search(r'(?:^|[\s,|])3proxy(?=\s|\(|,|\||$)', other.get(key, '')),
                    'Installed hard dependency on 3proxy: ' + other.get('Package', '?'))
    manifest = safe('/usr/lib/opkg/info/3proxy.list')
    require(manifest.is_file() and not manifest.is_symlink(), 'Missing regular 3proxy file manifest')
    require(set(manifest.read_text().splitlines()) == OWNED_PATHS, 'Unexpected 3proxy file manifest')
    control = safe('/usr/lib/opkg/info/3proxy.control')
    require(control.is_file() and not control.is_symlink() and fields(control.read_text()).get('Package') == '3proxy',
            'Invalid 3proxy control record')
    for listing in info.glob('*.list'):
        if listing.name == '3proxy.list':
            continue
        require(not listing.is_symlink(), 'Unexpected symlink package manifest')
        require(not (paths & set(listing.read_text().splitlines())), 'Shared ownership prevents 3proxy removal')
    for name in paths:
        require(stat.S_ISREG(safe(name).lstat().st_mode) or safe(name).is_symlink(),
                'Refusing to remove a non-file: ' + name)
    filtered = ''.join(part for i, part in enumerate(chunks) if i not in [index, index + 1])
    mode = stat.S_IMODE(status_path.stat().st_mode)
    # Atomic replacement prevents a stale open descriptor from rewriting this inode.
    fd, temporary = tempfile.mkstemp(prefix='.status-without-3proxy-', dir=status_path.parent)
    try:
        with os.fdopen(fd, 'w') as output:
            output.write(filtered)
            output.flush()
            os.fchmod(output.fileno(), mode)
            os.fsync(output.fileno())
        for name in sorted(paths):
            safe(name).unlink()
        os.replace(temporary, status_path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    require(status_path.read_text() == filtered, 'Package status update did not persist')
    require(all(rec.get('Package') != '3proxy' for rec in package_records(status_path.read_text())),
            '3proxy package record still present')
    require(all(not os.path.lexists(safe(name)) for name in paths), '3proxy paths still present')
    return report
