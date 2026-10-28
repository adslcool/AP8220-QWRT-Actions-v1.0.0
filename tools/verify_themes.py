#!/usr/bin/env python3
"""Check pinned themes with original ucode/LuCI and isolated server fixtures."""
import argparse
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from urllib.parse import urlsplit

from theme_manifest import DEFAULT_THEME, THEMES

VIEWS = ['login', 'login-error', 'overview', 'network']


class RenderedPage(HTMLParser):
    def __init__(self):
        super().__init__()
        self.assets, self.fields, self.scripts = [], {}, []
        self.inline = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in ['link', 'script', 'img']:
            value = attrs.get('src' if tag in ['script', 'img'] else 'href', '')
            if value.startswith('/luci-static/'):
                self.assets.append(urlsplit(value).path)
        if tag == 'input' and attrs.get('name'):
            self.fields[attrs['name']] = attrs
        if tag == 'script' and 'src' not in attrs:
            self.inline = ''

    def handle_data(self, data):
        if self.inline is not None:
            self.inline += data

    def handle_endtag(self, tag):
        if tag == 'script' and self.inline is not None:
            self.scripts.append(self.inline)
            self.inline = None


def verify(root, qemu, output, argon_output):
    project = Path(__file__).resolve().parents[1]
    node = shutil.which('node')
    if not node:
        raise ValueError('Node.js is required for theme checks')
    ucode = [str(qemu), '-L', str(root), str(root / 'usr/bin/ucode'),
             '-L', str(root / 'usr/share/ucode'), '-L', str(root / 'usr/lib/ucode')]
    subprocess.run([sys.executable, str(project / 'tools/verify_argon.py'), '--rootfs', str(root),
                    '--qemu', str(qemu), '--output', str(argon_output)], check=True)
    argon = json.loads(argon_output.read_text())
    result = {'schema': 1, 'default_theme': DEFAULT_THEME,
              'scope': 'Target ucode/LuCI template execution with isolated HTTP/board/UCI fixtures; JavaScript syntax, modules and local assets. No browser, radio or hardware test.',
              'themes': {'argon': {**argon, 'version': THEMES['argon']['version'], 'rendered_views': []}}}

    with tempfile.TemporaryDirectory(prefix='ap8220-theme-check-') as tmp:
        temporary = Path(tmp)
        config, save = temporary / 'config', temporary / 'save'
        config.mkdir()
        save.mkdir()
        # Adapt filesystem locations only; execute the actual QWRT runtime.
        original_runtime = (root / 'usr/share/ucode/luci/runtime.uc').read_text()
        if original_runtime.count('const uci = cursor();') != 1:
            raise ValueError('Unrecognized LuCI runtime UCI initialization')
        runtime = temporary / 'runtime.uc'
        adapted_runtime = original_runtime.replace('const uci = cursor();',
                           f'const uci = cursor({json.dumps(str(config))}, {json.dumps(str(save))});')
        adapted_runtime = adapted_runtime.replace("'/usr/share/ucode/luci/template'",
                           json.dumps(str(root / 'usr/share/ucode/luci/template')))
        runtime.write_text(adapted_runtime)
        harness = temporary / 'render.uc'
        harness.write_text((project / 'tests/theme_runtime.uc').read_text().replace(
                           "from 'luci.runtime'", 'from ' + json.dumps(str(runtime))))
        ui_source = (root / 'www/luci-static/resources/ui.js').read_text()
        core_source = (root / 'www/luci-static/resources/luci.js').read_text()
        for theme in (name for name in THEMES if name != 'argon'):
            passed, errors, rendered = [], [], []

            def check(name, argv=None, operation=None):
                try:
                    if operation is not None:
                        operation()
                    else:
                        command = subprocess.run(list(map(str, argv)), text=True, capture_output=True, timeout=30)
                        if command.returncode:
                            raise ValueError(command.stderr)
                    passed.append(name)
                    return True
                except (OSError, ValueError, subprocess.SubprocessError) as error:
                    errors.append({'check': name, 'error': str(error)[:4000]})
                    return False

            templates = root / 'usr/share/ucode/luci/template/themes' / theme
            for name in ['header.ut', 'footer.ut', 'sysauth.ut']:
                if not (templates / name).is_file():
                    raise ValueError(f'Missing {theme} template: {name}')
            for p in sorted(templates.rglob('*.ut')):
                check(p.relative_to(root).as_posix(), ucode + ['-T', '-c', '-o', temporary / 'compiled.uc', p])
            resources = root / 'www/luci-static/resources'
            scripts = sorted(p for p in resources.rglob('*.js') if
                p.name.startswith(('fs-', 'menu-footstrap')) or 'footstrap' in p.parts)
            scripts += sorted((root / 'www/luci-static' / theme).rglob('*.js'))
            for p in scripts:
                check(p.relative_to(root).as_posix(), [node, '--check', p])
                for requirement in re.findall(r"['\"]require ([^'\"]+)['\"]", p.read_text()):
                    module = requirement.split(' as ')[0]
                    target = resources / (module.replace('.', '/') + '.js')

                    def required_module(target=target, module=module):
                        builtin = module in ['baseclass', 'dom', 'poll', 'view', 'request'] and re.search(
                            r'\b' + module + r'[\'\"]?\s*:', core_source)
                        if not target.is_file() and not builtin:
                            raise ValueError('Missing LuCI JavaScript module: ' + target.relative_to(root).as_posix())

                    check(p.name + ': module ' + module, operation=required_module)
            widgets = ['RangeSlider', 'Select', 'Dropdown', 'menu'] if theme == 'footstrap' else ['menu', 'changes']
            for widget in widgets:
                def required_widget(widget=widget):
                    if not re.search(r'\b' + widget + r'\s*:', ui_source):
                        raise ValueError('Original ui.js lacks required API: ' + widget)
                check('original LuCI ui.' + widget, operation=required_widget)
            shell_files = [root / 'etc/uci-defaults' / ('30_luci-theme-' + theme)]
            if theme == 'footstrap':
                shell_files.append(root / 'www/cgi-bin/luci-theme-footstrap-pattern')
            for p in shell_files:
                check(p.relative_to(root).as_posix(), [qemu, '-L', root, root / 'bin/busybox', 'ash', '-n', p])
            for p in [root / 'usr/share/luci/menu.d' / ('luci-theme-' + theme + '.json'),
                      root / 'usr/share/rpcd/acl.d' / ('luci-theme-' + theme + '.json'),
                      *sorted((root / 'www/luci-static' / theme).rglob('*.json')),
                      *sorted((root / 'www/luci-static' / theme).rglob('*.webmanifest'))]:
                if p.exists():
                    check(p.relative_to(root).as_posix(), operation=lambda p=p: json.loads(p.read_text()))
            (config / 'luci').write_text(f"config core 'main'\n option mediaurlbase '/luci-static/{theme}'\n option resourcebase '/luci-static/resources'\nconfig internal 'themes'\n option {theme.title()} '/luci-static/{theme}'\n")
            (config / 'footstrap').write_text('')
            for view in VIEWS:
                def render_view(view=view):
                    env = dict(os.environ, AP8220_TEST_CONFIG=str(config), AP8220_TEST_SAVE=str(save),
                               AP8220_TEST_THEME=theme, AP8220_TEST_VIEW=view)
                    process = subprocess.run(ucode + [str(harness)], text=True, capture_output=True, env=env, timeout=30)
                    if process.returncode:
                        raise ValueError(process.stderr)
                    page = RenderedPage()
                    page.feed(process.stdout)
                    if '</html>' not in process.stdout or 'new LuCI(' not in process.stdout:
                        raise ValueError('Core LuCI initialization or complete HTML is missing')
                    if not any(path.startswith('/luci-static/' + theme + '/') for path in page.assets):
                        raise ValueError('Selected theme assets are missing from rendered page')
                    if view.startswith('login'):
                        if not {'luci_username', 'luci_password'} <= page.fields.keys():
                            raise ValueError('Login fields are missing')
                        if page.fields['luci_password'].get('value'):
                            raise ValueError('Password field must not be prefilled')
                    for asset in page.assets:
                        if not (root / 'www' / asset.lstrip('/')).is_file():
                            raise ValueError('Rendered page references missing asset: ' + asset)
                    for module in re.findall(r'L\.require\([\'\"]([^\'\"]+)[\'\"]\)', process.stdout):
                        target = resources / (module.replace('.', '/') + '.js')
                        if not target.is_file():
                            raise ValueError('Rendered page requests missing LuCI module: ' + module)
                    for i, script in enumerate(page.scripts):
                        path = temporary / f'inline-{i}.js'
                        path.write_text(script)
                        parsed = subprocess.run([node, '--check', str(path)], text=True, capture_output=True)
                        if parsed.returncode:
                            raise ValueError('Rendered inline JavaScript is invalid: ' + parsed.stderr)
                    rendered.append(view)
                check('actual QWRT runtime render: ' + view, operation=render_view)
            result['themes'][theme] = {'version': THEMES[theme]['version'], 'pass_count': len(passed),
                                       'fail_count': len(errors), 'passed': passed, 'errors': errors,
                                       'rendered_views': rendered}
    result['pass_count'] = sum(item['pass_count'] for item in result['themes'].values())
    result['fail_count'] = sum(item['fail_count'] for item in result['themes'].values())
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    for theme, item in result['themes'].items():
        print(f'{theme} compatibility: {item["pass_count"]} passed, {item["fail_count"]} failed')
    if result['fail_count']:
        raise SystemExit('Theme compatibility failed; see THEMES-COMPATIBILITY.json')
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--rootfs', type=Path, required=True)
    parser.add_argument('--qemu', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--argon-output', type=Path, required=True)
    args = parser.parse_args()
    verify(args.rootfs.resolve(), args.qemu.resolve(), args.output, args.argon_output)


if __name__ == '__main__':
    main()
