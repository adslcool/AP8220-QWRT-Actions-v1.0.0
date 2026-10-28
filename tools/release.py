#!/usr/bin/env python3
"""Assemble verified assets; publish a complete GitHub Release using gh.

The publishing path uses only the Python standard library and GitHub CLI.
It never executes programs from downloaded firmware or Release assets.
"""
import argparse
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
from urllib.parse import urlencode
from zoneinfo import ZoneInfo
from theme_manifest import DEFAULT_THEME, THEMES

PROJECT = Path(__file__).resolve().parents[1]
VERSION = (PROJECT / 'VERSION').read_text().strip()
PRIVATE_DIR = '.publish'


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def safe_name(name):
    require(isinstance(name, str) and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,180}', name), 'Unsafe asset filename')
    return name


def clean_text(value):
    return str(value).replace('`', "'").replace('|', '&#124;').replace('\r', ' ').replace('\n', ' ').replace('<', '&lt;').replace('>', '&gt;')[:220]


def prepare(dist, output):
    dist, output = Path(dist), Path(output)
    require(not output.exists() or not any(output.iterdir()), 'Release output directory must be empty')
    output.mkdir(parents=True, exist_ok=True)
    report = json.loads((dist / 'BUILD-REPORT.json').read_text())
    compatibility = json.loads((dist / 'ARGON-COMPATIBILITY.json').read_text())
    theme_compatibility = json.loads((dist / 'THEMES-COMPATIBILITY.json').read_text())
    for key in ['kernel_byte_identical', 'fwtool_metadata_crc_verified', 'original_firmware_fwtool_crc_verified']:
        require(report.get(key) is True, f'Required build check failed: {key}')
    require(report.get('version') == VERSION, 'Build report version does not match publishing tools')
    require(not report.get('unexpected_changed_or_missing_original_paths'), 'Unexpected original firmware file changes')
    require(report.get('password_policy') in ['inherit-input-firmware', 'custom-first-boot'] and
            report.get('account_files_byte_identical') is True, 'Password/account preservation checks failed')
    if report['password_policy'] == 'custom-first-boot':
        require(report.get('custom_root_password_firstboot_test_passed') is True,
                'Custom root password first-boot test is required before publishing')
    require(report.get('network_migration_tests_passed') is True, 'Network migration tests are required before publishing')
    require(isinstance(report.get('proxy_removal'), dict) and report['proxy_removal'].get('absent_after') is True,
            '3proxy removal verification is required before publishing')
    require(report.get('usteer_ipc_tests_passed') is True, 'Real usteer/ubus IPC tests are required before publishing')
    speed = report.get('librespeed', {})
    require(speed.get('passed') is True and speed.get('http_tests_passed') is True and
            speed.get('firstboot_tests_passed') is True and speed.get('service_user_tests_passed') is True and
            speed.get('version') == '1.1.5-r5' and speed.get('enabled') == '1' and
            speed.get('listen_port') == '8989' and speed.get('database_type') == 'none',
            'LibreSpeed startup/configuration/HTTP verification is required before publishing')
    require(compatibility.get('fail_count') == 0 and compatibility.get('pass_count', 0) >= 1, 'Argon compatibility check failed')
    require(report.get('default_theme') == DEFAULT_THEME and report.get('theme_default_tests_passed') is True,
            'Footstrap default theme initialization checks failed')
    for key in ['wifi_security_tests_passed', 'ipv6_client_tests_passed', 'removed_packages_absent']:
        require(report.get(key) is True, f'Required AP profile check failed: {key}')
    require(report.get('themes_compatibility_passed') is True and theme_compatibility.get('fail_count') == 0 and
            theme_compatibility.get('default_theme') == DEFAULT_THEME, 'Theme compatibility check failed')
    for name, expected in THEMES.items():
        item = theme_compatibility.get('themes', {}).get(name, {})
        require(item.get('version') == expected['version'] and item.get('fail_count') == 0 and
                item.get('pass_count', 0) > 0, f'{name} theme checks failed or missing')
        if name != 'argon':
            require({'login', 'login-error', 'overview', 'network'} <= set(item.get('rendered_views', [])),
                    f'{name} native LuCI page render checks are incomplete')
    image_name = safe_name(report['output_image'])
    require(sha(dist / image_name) == report['output_sha256'], 'Firmware SHA256 does not match its build report')
    source_sha = report['base_image_sha256']
    require(re.fullmatch(r'[0-9a-f]{64}', source_sha), 'Invalid source SHA256')
    run_id = os.environ.get('GITHUB_RUN_ID', '0')
    attempt = os.environ.get('GITHUB_RUN_ATTEMPT', '1')
    commit = os.environ.get('GITHUB_SHA', '0' * 40)
    repo = os.environ.get('GITHUB_REPOSITORY', 'local/local')
    require(run_id.isdigit() and attempt.isdigit(), 'Invalid workflow run identity')
    require(re.fullmatch(r'[0-9a-f]{40}', commit), 'Invalid source commit')
    require(re.fullmatch(r'[A-Za-z0-9-]+/[A-Za-z0-9_.-]+', repo), 'Invalid repository identity')
    now = datetime.now(ZoneInfo('Asia/Shanghai'))
    stamp = now.strftime('%Y%m%d-%H%M%S')
    tag = f'ap8220-ap-v{VERSION}-{stamp}-{run_id}-{attempt}'
    title = f'AP8220 QWRT AP v{VERSION} - {now:%Y-%m-%d %H:%M} UTC+8'
    report['workflow'] = {'repository': repo, 'commit': commit, 'run_id': run_id, 'run_attempt': attempt,
                          'url': f'https://github.com/{repo}/actions/runs/{run_id}', 'release_tag': tag}
    (dist / 'BUILD-REPORT.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    shutil.copy2(dist / image_name, output / image_name)
    (output / 'SHA256SUMS').write_text(f'{report["output_sha256"]}  {image_name}\n')
    private = output / PRIVATE_DIR
    private.mkdir()
    base = report.get('base_release', {})
    root_policy = '沿用原固件密码' if report['password_policy'] == 'inherit-input-firmware' else '首次启动设置本次指定的密码'
    selected = report.get('customization', {})
    wifi2, wifi5 = selected.get('wifi_2g', {}), selected.get('wifi_5g', {})
    notes = f'''Aliyun AP8220 有线回程 AP 定制固件 · v{VERSION}

| 项目 | 内容 |
|---|---|
| 原固件版本 | {clean_text(base.get('version', 'unknown'))} / {clean_text(base.get('revision', 'unknown'))} |
| 主题 / 语言 | Footstrap 0.14.16 默认，保留 Argon 3.1.0；LuCI 简体中文 |
| 有线网络 | LAN + WAN 桥接，上级 DHCP 分配管理地址 |
| IPv6 | lan_v6：DHCPv6 客户端，绑定 br-lan |
| 2.4G | {clean_text(wifi2.get('ssid', '按制作参数'))} · 信道 {clean_text(wifi2.get('channel', '6'))} · HT20 |
| 5G | {clean_text(wifi5.get('ssid', '按制作参数'))} · 信道 {clean_text(wifi5.get('channel', '149'))} · HT80 |
| 无线安全 | WPA/WPA2-PSK 混合，算法自动（psk-mixed） |
| 漫游 | 802.11k/v/r，实验性 usteer；关闭强制踢人 |
| root | {root_policy} |
| 网页测速 | LibreSpeed Go 1.1.5-r5，默认启动；http://AP地址:8989；数据库 none |
| 固件大小 | {(output / image_name).stat().st_size / 1048576:.2f} MiB |

已移除 3proxy、Aurora、Argon 配置中文包及默认 vpn0/tun0 接口，修复旧式 ifname 迁移提示。
保留原内核、QCA 驱动及 NSS；镜像账户文件保持原样，首次启动添加 LibreSpeed 专用账户；自定义 root 密码仅在首次启动应用。
检查通过：原文件保持、镜像 CRC、网络与密码配置、真实 ARM ubus/usteer 联调及 {theme_compatibility['pass_count']} 项主题检查。

固件 SHA256：`{report['output_sha256']}`

这是 sysupgrade 升级包；取消保留配置可完整应用新默认值。AP 真机无线、IPv6 上游分配及漫游效果仍需实测。

[构建记录](https://github.com/{repo}/actions/runs/{run_id}) · [适配源码](https://github.com/{repo}/tree/{commit})
'''
    (private / 'RELEASE-NOTES.md').write_text(notes)
    assets = sorted([image_name, 'SHA256SUMS'])
    info = {'schema': 2, 'version': VERSION, 'tag': tag, 'title': title, 'prerelease': True,
            'repository': repo, 'commit': commit, 'run_id': run_id, 'run_attempt': attempt,
            'image': image_name, 'assets': assets, 'notes_sha256': sha(private / 'RELEASE-NOTES.md'),
            'checksum_sha256': sha(output / 'SHA256SUMS')}
    (private / 'release-info.json').write_text(json.dumps(info, indent=2, ensure_ascii=False) + '\n')
    validate_assets(output)
    print(f'Release assets prepared: {tag}, {len(assets)} files')
    return info


def validate_assets(directory):
    directory = Path(directory)
    private = directory / PRIVATE_DIR
    require(private.is_dir() and not private.is_symlink(), 'Missing local publishing metadata')
    require({p.name for p in private.iterdir()} == {'release-info.json', 'RELEASE-NOTES.md'} and
            all(p.is_file() and not p.is_symlink() for p in private.iterdir()), 'Invalid local publishing metadata')
    info = json.loads((private / 'release-info.json').read_text())
    names = info.get('assets', [])
    image = safe_name(info.get('image'))
    require(info.get('schema') == 2 and image.endswith('.bin') and len(names) == 2 and
            set(names) == {image, 'SHA256SUMS'}, 'Release must contain only the firmware and SHA256SUMS')
    require(all(safe_name(name) for name in names), 'Unsafe asset name')
    actual_names = {p.name for p in directory.iterdir() if p.is_file() and not p.is_symlink()}
    require(actual_names == set(names), 'Missing or unexpected Release assets')
    require({p.name for p in directory.iterdir()} == set(names) | {PRIVATE_DIR}, 'Unexpected Release output entries')
    require(sha(private / 'RELEASE-NOTES.md') == info.get('notes_sha256'), 'Release notes checksum mismatch')
    require(sha(directory / 'SHA256SUMS') == info.get('checksum_sha256'), 'SHA256 manifest checksum mismatch')
    hashes = {}
    for line in (directory / 'SHA256SUMS').read_text().splitlines():
        digest, name = line.split('  ', 1)
        safe_name(name)
        require(re.fullmatch(r'[0-9a-f]{64}', digest) and name not in hashes, 'Invalid SHA256 manifest')
        hashes[name] = digest
    require(set(hashes) == set(names) - {'SHA256SUMS'}, 'Incomplete SHA256 manifest')
    for name, expected in hashes.items():
        require(sha(directory / name) == expected, f'Release asset checksum mismatch: {name}')
    return info


def gh_run(argv, missing_ok=False, input_data=None):
    command = ['gh', *map(str, argv)]
    result = subprocess.run(command, input=input_data, encoding='utf-8', capture_output=True)
    if result.returncode:
        if missing_ok and re.search(r'\b404\b', result.stderr):
            return None
        raise RuntimeError('GitHub CLI failed: ' + result.stderr.strip())
    return result.stdout


def gh_json(method, endpoint, payload):
    body = json.dumps(payload, ensure_ascii=False)
    return json.loads(gh_run(['api', endpoint, '--method', method,
                             '--header', 'Content-Type: application/json; charset=utf-8',
                             '--header', f'Content-Length: {len(body.encode("utf-8"))}', '--input', '-'],
                            input_data=body))


def find_release(repo, tag):
    # The tag endpoint finds published releases, not drafts with pending tags.
    # With contents:write, the paginated list also includes those drafts.
    raw = gh_run(['api', f'repos/{repo}/releases/tags/{tag}'], missing_ok=True)
    if raw is not None:
        remote = json.loads(raw)
        require(remote.get('tag_name') == tag, 'GitHub returned a different Release tag')
        return remote
    pages = json.loads(gh_run(['api', f'repos/{repo}/releases?per_page=100', '--paginate', '--slurp']))
    require(isinstance(pages, list) and all(isinstance(page, list) for page in pages),
            'Invalid GitHub Release list response')
    matches = [item for page in pages for item in page if item.get('tag_name') == tag]
    require(len(matches) <= 1, 'Multiple GitHub Releases have the requested tag')
    return matches[0] if matches else None


def get_release(repo, release_id, tag):
    require(type(release_id) is int and release_id > 0, 'Invalid GitHub Release ID')
    remote = json.loads(gh_run(['api', f'repos/{repo}/releases/{release_id}']))
    require(remote.get('id') == release_id and remote.get('tag_name') == tag,
            'GitHub returned a different Release identity')
    return remote


def remote_assets_match(remote, directory, names):
    assets = {item['name']: item for item in remote.get('assets', [])}
    require(len(assets) == len(remote.get('assets', [])) and set(assets) == set(names),
            'GitHub Release asset names do not match the completed build')
    for name in names:
        path = directory / name
        require(assets[name].get('state') == 'uploaded', f'Uploaded asset is incomplete: {name}')
        require(assets[name].get('size') == path.stat().st_size, f'Uploaded size differs: {name}')
        require(assets[name].get('digest') == 'sha256:' + sha(path), f'Uploaded SHA256 differs or is unavailable: {name}')


def upload_assets(repo, remote, directory, names):
    release_id = remote['id']
    upload_base = f'https://uploads.github.com/repos/{repo}/releases/{release_id}/assets'
    # Use the create response's URL, constrained to this repository/Release.
    require(remote.get('upload_url') == upload_base + '{?name,label}', 'Unexpected Release upload URL')
    existing = {item['name']: item for item in remote.get('assets', [])}
    require(len(existing) == len(remote.get('assets', [])) and set(existing) <= set(names),
            'Draft has unexpected or duplicate Release assets')
    for name in names:
        path = directory / name
        item = existing.get(name)
        if item is not None:
            if (item.get('state') == 'uploaded' and item.get('size') == path.stat().st_size and
                    item.get('digest') == 'sha256:' + sha(path)):
                continue
            asset_id = item.get('id')
            require(type(asset_id) is int and asset_id > 0, 'Invalid existing Release asset ID')
            # Only the validated draft may have a failed/mismatched upload replaced.
            gh_run(['api', f'repos/{repo}/releases/assets/{asset_id}', '--method', 'DELETE'])
        print(f'Uploading Release asset: {name}', flush=True)
        uploaded = json.loads(gh_run(['api', upload_base + '?' + urlencode({'name': name}),
                                     '--method', 'POST', '--header', 'Content-Type: application/octet-stream',
                                     '--header', f'Content-Length: {path.stat().st_size}',
                                     '--input', path]))
        remote_assets_match({'assets': [uploaded]}, directory, [name])


def publish(directory):
    directory = Path(directory).resolve()
    info = validate_assets(directory)
    repo, commit, run_id = (os.environ.get(key) for key in ['GITHUB_REPOSITORY', 'GITHUB_SHA', 'GITHUB_RUN_ID'])
    require(repo and commit and run_id, 'Publishing requires the GitHub Actions repository/run environment')
    require((info['repository'], info['commit'], info['run_id']) == (repo, commit, run_id),
            'Release assets belong to a different repository, commit or workflow run')
    require(info.get('version') == VERSION and re.fullmatch(
        rf'ap8220-ap-v{re.escape(VERSION)}-\d{{8}}-\d{{6}}-{re.escape(run_id)}-\d+', info.get('tag', '')),
        'Invalid Release tag')
    tag = info['tag']
    remote = find_release(repo, tag)
    if remote is not None and not remote.get('draft'):
        remote_assets_match(remote, directory, info['assets'])
        print('Release already published and assets verified: ' + remote['html_url'])
        return remote['html_url']
    if remote is None:
        # POST returns the new Release's ID. Do not rediscover it by tag/list:
        # draft visibility there caused the v0.5.0 publication failure.
        remote = gh_json('POST', f'repos/{repo}/releases',
                         {'tag_name': tag, 'target_commitish': commit, 'name': info['title'],
                          'body': (directory / PRIVATE_DIR / 'RELEASE-NOTES.md').read_text(),
                          'draft': True, 'prerelease': info['prerelease']})
    require(remote.get('tag_name') == tag, 'GitHub returned a different Release tag')
    require(remote.get('draft') is True, 'Expected a draft Release before uploading')
    require(remote.get('target_commitish') == commit, 'Existing draft belongs to a different target commit')
    release_id = remote.get('id')
    require(type(release_id) is int and release_id > 0, 'Invalid GitHub Release ID')
    print(f'Uploading to draft Release ID {release_id}: {tag}', flush=True)
    # A failed upload leaves a draft, never a publicly incomplete new Release.
    upload_assets(repo, remote, directory, info['assets'])
    remote = get_release(repo, release_id, tag)
    require(remote.get('draft') is True, 'Release was published before asset verification')
    remote_assets_match(remote, directory, info['assets'])
    gh_json('PATCH', f'repos/{repo}/releases/{release_id}',
            {'draft': False, 'make_latest': 'false' if info['prerelease'] else 'true'})
    remote = get_release(repo, release_id, tag)
    require(remote.get('draft') is False, 'Release is still a draft')
    remote_assets_match(remote, directory, info['assets'])
    url = remote['html_url']
    if os.environ.get('GITHUB_STEP_SUMMARY'):
        with Path(os.environ['GITHUB_STEP_SUMMARY']).open('a') as stream:
            stream.write(f'### AP8220 AP Release\n\n[{tag}]({url})\n')
    print('Published: ' + url)
    return url


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest='command', required=True)
    prep = sub.add_parser('prepare')
    prep.add_argument('--dist', type=Path, required=True)
    prep.add_argument('--output', type=Path, required=True)
    pub = sub.add_parser('publish')
    pub.add_argument('--directory', type=Path, required=True)
    args = parser.parse_args()
    if args.command == 'prepare':
        prepare(args.dist, args.output)
    else:
        publish(args.directory)


if __name__ == '__main__':
    main()
