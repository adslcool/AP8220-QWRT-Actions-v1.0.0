#!/usr/bin/env python3
"""Apply dispatch Wi-Fi settings and an optional hashed first-boot root password.

Read the event file rather than interpolating input into shell commands. Plain
root passwords are passed to openssl on stdin and never written to the project.
"""
import argparse
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
from theme_manifest import DEFAULT_THEME

PROJECT = Path(__file__).resolve().parents[1]
# This QWRT's reduced musl crypt() supports MD5-crypt, not SHA256/512-crypt.
# Keep the original authentication library and verify with its real crypt().
HASH_PATTERN = r'\$1\$[./A-Za-z0-9]{1,8}\$[./A-Za-z0-9]{22}'
CHANNELS_2G = ['auto', *map(str, range(1, 14))]
CHANNELS_5G = ['auto', '36', '40', '44', '48', '149', '153', '157', '161']


def require(condition, message):
    if not condition:
        raise ValueError(message)


def text_input(inputs, name, default=''):
    value = inputs.get(name, default)
    require(isinstance(value, str), f'{name}: expected text')
    return value


def valid_text(value, name, minimum, maximum):
    require(minimum <= len(value.encode('utf-8')) <= maximum,
            f'{name}: expected {minimum}–{maximum} UTF-8 bytes')
    require(not any(ord(c) < 32 or ord(c) == 127 for c in value),
            f'{name}: control characters are not supported')


def validate_inputs(inputs):
    require(isinstance(inputs, dict), 'Expected an inputs object')
    mode = text_input(inputs, 'wifi_mode', 'shared') or 'shared'
    mode = {'共用': 'shared', '分别': 'separate'}.get(mode, mode)
    require(mode in ['shared', 'separate'], 'wifi_mode: choose shared or separate')
    ssid2 = text_input(inputs, 'ssid_2g', 'Home-WiFi') or 'Home-WiFi'
    key2 = text_input(inputs, 'wifi_key_2g', '12345678') or '12345678'
    ssid5, key5 = ssid2, key2
    if mode == 'separate':
        ssid5 = text_input(inputs, 'ssid_5g', 'Home-WiFi-5G') or 'Home-WiFi-5G'
        key5 = text_input(inputs, 'wifi_key_5g') or key2
    for name, value in [('ssid_2g', ssid2), ('ssid_5g', ssid5)]:
        valid_text(value, name, 1, 32)
    for name, value in [('wifi_key_2g', key2), ('wifi_key_5g', key5)]:
        valid_text(value, name, 8, 63)
    channel2 = text_input(inputs, 'channel_2g', '6') or '6'
    channel5 = text_input(inputs, 'channel_5g', '149') or '149'
    require(channel2 in CHANNELS_2G, 'channel_2g: unsupported channel')
    require(channel5 in CHANNELS_5G, 'channel_5g: unsupported channel')
    password = text_input(inputs, 'root_password')
    if password:
        valid_text(password, 'root_password', 1, 256)
    return {'wifi_mode': mode, 'ssid_2g': ssid2, 'key_2g': key2,
            'ssid_5g': ssid5, 'key_5g': key5, 'channel_2g': channel2,
            'channel_5g': channel5, 'root_password': password}


def render_profile(template, settings):
    # Keep manually configured identity, country and roaming parameters.
    options = {k: settings[k] for k in ['wifi_mode', 'ssid_2g', 'key_2g', 'ssid_5g',
                                       'key_5g', 'channel_2g', 'channel_5g']}
    options.update(ssid=settings['ssid_2g'], key=settings['key_2g'])
    output, in_profile, count = [], False, 0

    def emit_options():
        output.extend(f'\toption {name} {shlex.quote(value)}' for name, value in options.items())

    for line in template.splitlines():
        parts = shlex.split(line, comments=True)
        if parts and parts[0] == 'config':
            if in_profile:
                emit_options()
            in_profile = parts == ['config', 'profile', 'profile']
            count += int(in_profile)
        if in_profile and len(parts) >= 2 and parts[0] == 'option' and parts[1] in options:
            continue
        output.append(line)
    if in_profile:
        emit_options()
    require(count == 1, 'Expected exactly one named profile section')
    return '\n'.join(output) + '\n'


def password_hash(password):
    result = subprocess.run(['openssl', 'passwd', '-1', '-stdin'], input=password + '\n',
                            text=True, capture_output=True)
    require(result.returncode == 0, 'Could not hash root password with openssl')
    value = result.stdout.strip()
    require(re.fullmatch(HASH_PATTERN, value) is not None, 'Unexpected root password hash format')
    return value


def configure(project, inputs):
    project = Path(project)
    settings = validate_inputs(inputs)
    profile = project / 'files/etc/config/ap8220_ap'
    rendered = render_profile(profile.read_text(), settings)
    hashed = password_hash(settings['root_password']) if settings['root_password'] else None
    summary = {'schema': 1, 'default_theme': DEFAULT_THEME, 'wifi_mode': settings['wifi_mode'],
               'wifi_2g': {'ssid': settings['ssid_2g'], 'channel': settings['channel_2g'], 'htmode': 'HT20'},
               'wifi_5g': {'ssid': settings['ssid_5g'], 'channel': settings['channel_5g'], 'htmode': 'HT80'},
               'password_policy': 'custom-first-boot' if hashed else 'inherit-input-firmware',
               'passwords_redacted': True}
    profile.write_text(rendered)
    profile.chmod(0o600)
    hash_file = project / 'files/etc/ap8220-root-password.hash'
    if hashed:
        descriptor = os.open(hash_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, 'w') as stream:
            stream.write(hashed + '\n')
        hash_file.chmod(0o600)
    else:
        hash_file.unlink(missing_ok=True)
    (project / 'files/etc/ap8220-build-profile.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2) + '\n')
    return summary


def mask_passwords(inputs):
    if os.environ.get('GITHUB_ACTIONS') != 'true':
        return
    for name in ['root_password', 'wifi_key_2g', 'wifi_key_5g']:
        value = inputs.get(name)
        if isinstance(value, str) and value:
            escaped = value.replace('%', '%25').replace('\r', '%0D').replace('\n', '%0A')
            print('::add-mask::' + escaped, flush=True)


def main():
    parser = argparse.ArgumentParser()
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--event', type=Path, help='GitHub event JSON file')
    source.add_argument('--settings', type=Path, help='Local JSON object with the same input names')
    parser.add_argument('--project', type=Path, default=PROJECT)
    args = parser.parse_args()
    data = json.loads((args.event or args.settings).read_text())
    inputs = data.get('inputs', {}) if args.event else data
    require(isinstance(inputs, dict), 'Expected an inputs object')
    mask_passwords(inputs)
    try:
        summary = configure(args.project, inputs)
    except (ValueError, OSError, subprocess.SubprocessError):
        # Suppress exception locals and untrusted input text from logs.
        raise SystemExit('Invalid AP settings or password hashing failure; check SSID/key lengths, channels and mode')
    print(f'AP settings prepared: {summary["wifi_mode"]}; root password policy: {summary["password_policy"]}')


if __name__ == '__main__':
    main()
