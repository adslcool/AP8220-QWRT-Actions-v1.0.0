#!/usr/bin/env python3
"""Test downloading and Release failure handling without contacting GitHub."""
import contextlib
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import build
import configure_profile as profile
import download_firmware as fetch
import release
import remove_3proxy


def fake_tar():
    data = io.BytesIO()
    with tarfile.open(fileobj=data, mode='w') as archive:
        item = tarfile.TarInfo('sysupgrade-aliyun_ap8220/CONTROL')
        content = b'BOARD=aliyun_ap8220\n'
        item.size = len(content)
        archive.addfile(item, io.BytesIO(content))
    return data.getvalue()


class DownloadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.payload = fake_tar()
        payload = cls.payload

        class Handler(BaseHTTPRequestHandler):
            retries = 0

            def log_message(self, *args):
                pass

            def do_GET(self):
                if self.path.startswith('/redirect'):
                    self.send_response(302)
                    self.send_header('Location', '/ok.bin')
                    self.end_headers()
                    return
                if self.path.startswith('/retry') and Handler.retries == 0:
                    Handler.retries += 1
                    self.send_error(503)
                    return
                data = b'<html>login required</html>' if self.path.startswith('/html') else payload
                self.send_response(200)
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.url = f'http://127.0.0.1:{cls.server.server_port}'

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.output, self.record = self.directory / 'firmware.bin', self.directory / 'download.json'

    def test_direct_download_hash_and_query_redaction(self):
        digest = hashlib.sha256(self.payload).hexdigest()
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            result = fetch.download(self.url + '/ok.bin?token=private-test-token', self.output, self.record, digest)
        self.assertEqual(self.output.read_bytes(), self.payload)
        self.assertTrue(result['expected_sha256_verified'])
        self.assertNotIn('private-test-token', self.record.read_text() + output.getvalue())

    def test_redirect_and_transient_retry(self):
        fetch.download(self.url + '/redirect', self.output, self.record)
        self.assertEqual(self.output.read_bytes(), self.payload)
        self.output.unlink()
        self.record.unlink()
        fetch.download(self.url + '/retry.bin', self.output, self.record, attempts=2)
        self.assertEqual(self.output.read_bytes(), self.payload)

    def test_bad_hash_leaves_no_partial_file(self):
        with self.assertRaisesRegex(ValueError, 'SHA256 mismatch'):
            fetch.download(self.url + '/ok.bin', self.output, self.record, '0' * 64)
        self.assertFalse(self.output.exists())
        self.assertFalse(self.record.exists())
        self.assertEqual(list(self.directory.glob('*.part')), [])

    def test_html_and_oversized_download_are_rejected(self):
        with self.assertRaisesRegex(ValueError, 'sysupgrade tar'):
            fetch.download(self.url + '/html', self.output, self.record)
        with self.assertRaisesRegex(ValueError, 'size limit'):
            fetch.download(self.url + '/ok.bin', self.output, self.record, max_bytes=1024)
        self.assertFalse(self.output.exists())

    def test_non_http_and_embedded_credentials_are_rejected(self):
        for url in ['file:///etc/passwd', 'https://user:password@example.com/fw.bin', 'https://example.com/fw.bin\n', 'https://example.com/fw.bin#fragment']:
            with self.subTest(url=url), self.assertRaises(ValueError):
                fetch.validate_url(url)


class FakeGitHub:
    def __init__(self, directory, info, fail_upload=False, corrupt_digest=False, already_published=False,
                 draft_on_later_page=False, list_hides_drafts=False, fail_after_uploads=0):
        self.directory, self.info = directory, info
        self.fail_upload, self.corrupt_digest = fail_upload, corrupt_digest
        self.calls = []
        self.state = None
        self.draft_on_later_page = draft_on_later_page
        self.list_hides_drafts = list_hides_drafts
        self.fail_after_uploads = fail_after_uploads
        self.uploaded_names = []
        self.payloads = []
        if already_published:
            self.state = self.remote(False)

    def remote(self, draft):
        assets = [{'id': 1000 + index, 'name': name, 'state': 'uploaded',
                   'size': (self.directory / name).stat().st_size,
                   'digest': 'sha256:' + release.sha(self.directory / name)}
                  for index, name in enumerate(self.info['assets'])]
        if self.corrupt_digest:
            assets[0]['digest'] = 'sha256:' + '0' * 64
        return {'id': 42, 'tag_name': self.info['tag'], 'draft': draft,
                'target_commitish': self.info['commit'], 'assets': assets,
                'upload_url': f"https://uploads.github.com/repos/{self.info['repository']}/releases/42/assets{{?name,label}}",
                'html_url': f"https://github.com/{self.info['repository']}/releases/tag/{self.info['tag']}"}

    def __call__(self, argv, missing_ok=False, input_data=None):
        args = list(map(str, argv))
        self.calls.append(args)
        if args[0] != 'api':
            raise AssertionError('Publishing must use Release-ID API requests: ' + str(args))
        prefix = f"repos/{self.info['repository']}/releases"
        endpoint = args[1]
        method = args[args.index('--method') + 1] if '--method' in args else 'GET'
        if method == 'GET':
            if endpoint == f"{prefix}/tags/{self.info['tag']}":
                if self.state is not None and not self.state['draft']:
                    return json.dumps(self.state)
                # Match the real API: drafts are not returned by tag lookup,
                # even after every asset has been uploaded successfully.
                if missing_ok:
                    return None
                raise RuntimeError('GitHub CLI failed: gh: Not Found (HTTP 404)')
            if endpoint == prefix + '?per_page=100':
                if args[2:] != ['--paginate', '--slurp']:
                    raise AssertionError('Draft discovery must read all Release pages')
                pages = [[self.state] if self.state is not None and not self.list_hides_drafts else []]
                if self.draft_on_later_page:
                    older = [{'id': 100 + i, 'tag_name': f'unrelated-{i}', 'draft': False} for i in range(100)]
                    pages.insert(0, older)
                return json.dumps(pages)
            if endpoint == prefix + '/42' and self.state is not None:
                return json.dumps(self.state)
            raise AssertionError(args)
        if input_data is not None:
            if args[-2:] != ['--input', '-']:
                raise AssertionError('JSON must be passed on stdin')
            payload = json.loads(input_data)
            self.payloads.append((method, endpoint, payload))
        if method == 'POST' and endpoint == prefix:
            if self.state is not None:
                raise AssertionError('Cannot create a duplicate Release')
            assert payload['tag_name'] == self.info['tag'] and payload['target_commitish'] == self.info['commit']
            assert payload['draft'] is True and payload['prerelease'] is True
            assert payload['body'] == (self.directory / release.PRIVATE_DIR / 'RELEASE-NOTES.md').read_text()
            self.state = dict(self.remote(True), assets=[])
            return json.dumps(self.state)
        if method == 'POST' and endpoint.startswith('https://uploads.github.com/'):
            assert self.state['draft'] is True
            url = urlsplit(endpoint)
            assert url.path == f'/repos/{self.info["repository"]}/releases/42/assets'
            name = parse_qs(url.query)['name'][0]
            assert name in self.info['assets']
            assert args[args.index('--header') + 1] == 'Content-Type: application/octet-stream'
            path = Path(args[args.index('--input') + 1])
            assert path.read_bytes() == (self.directory / name).read_bytes()
            if self.fail_upload and len(self.uploaded_names) >= self.fail_after_uploads:
                raise RuntimeError('simulated upload failure')
            if any(item['name'] == name for item in self.state['assets']):
                raise AssertionError('Duplicate upload needs deletion first')
            item = next(item for item in self.remote(True)['assets'] if item['name'] == name)
            self.state['assets'].append(item)
            self.uploaded_names.append(name)
            return json.dumps(item)
        if method == 'DELETE' and endpoint.startswith(prefix + '/assets/'):
            assert self.state['draft'] is True
            asset_id = int(endpoint.rsplit('/', 1)[1])
            self.state['assets'] = [item for item in self.state['assets'] if item['id'] != asset_id]
            return ''
        if method == 'PATCH' and endpoint == prefix + '/42':
            assert {item['name'] for item in self.state['assets']} == set(self.info['assets'])
            assert payload == {'draft': False, 'make_latest': 'false'}
            self.state['draft'] = False
            return json.dumps(self.state)
        raise AssertionError(args)


def api_mutation(call, method):
    return '--method' in call and call[call.index('--method') + 1] == method


@unittest.skipUnless(shutil.which('gh'), 'GitHub CLI is required for the local HTTP transport check')
class GitHubCLITransportTests(unittest.TestCase):
    def test_real_cli_sends_json_stdin_and_unmodified_binary_file(self):
        received = []

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def reply(self, status, value):
                data = json.dumps(value).encode()
                self.send_response(status)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_POST(self):
                body = self.rfile.read(int(self.headers['Content-Length']))
                received.append((self.command, self.path, dict(self.headers), body))
                self.reply(201, {'received': True})

            do_PATCH = do_POST

            def do_GET(self):
                self.reply(404, {'message': 'Not Found'})

        with tempfile.TemporaryDirectory() as temp:
            server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                endpoint = f'http://127.0.0.1:{server.server_port}'
                binary = bytes(range(256)) * 64 + b'\x00\xff\r\n'
                asset = Path(temp) / 'payload.bin'
                asset.write_bytes(binary)
                payload = {'tag_name': 'fixture', 'draft': True, 'body': '中文 "quote" $value'}
                with patch.dict(os.environ, {'GH_TOKEN': 'synthetic-local-test-only', 'GH_CONFIG_DIR': temp}):
                    self.assertTrue(release.gh_json('POST', endpoint + '/releases', payload)['received'])
                    release.gh_run(['api', endpoint + '/assets?name=payload.bin', '--method', 'POST',
                                    '--header', 'Content-Type: application/octet-stream',
                                    '--header', f'Content-Length: {len(binary)}', '--input', asset])
                    release.gh_json('PATCH', endpoint + '/releases/42', {'draft': False, 'make_latest': 'false'})
                    self.assertIsNone(release.gh_run(['api', endpoint + '/missing'], missing_ok=True))
                self.assertEqual(json.loads(received[0][3]), payload)
                self.assertEqual(received[0][2]['Content-Type'], 'application/json; charset=utf-8')
                self.assertEqual(int(received[0][2]['Content-Length']), len(received[0][3]))
                self.assertEqual(received[1][3], binary)
                self.assertEqual(received[1][2]['Content-Type'], 'application/octet-stream')
                self.assertEqual(int(received[1][2]['Content-Length']), len(binary))
                self.assertEqual(received[2][0], 'PATCH')
                self.assertEqual(json.loads(received[2][3]), {'draft': False, 'make_latest': 'false'})
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.dist, self.output = self.root / 'dist', self.root / 'release'
        self.dist.mkdir()
        image = self.dist / 'QWRT-AP8220-unit-test-sysupgrade.bin'
        image.write_bytes(b'firmware test bytes')
        report = {'version': release.VERSION, 'kernel_byte_identical': True,
                  'fwtool_metadata_crc_verified': True, 'original_firmware_fwtool_crc_verified': True,
                  'unexpected_changed_or_missing_original_paths': [], 'output_image': image.name,
                  'output_sha256': release.sha(image), 'base_image_sha256': 'a' * 64,
                  'baseline_source_matches_user_tested_image': False,
                  'password_policy': 'inherit-input-firmware', 'account_files_byte_identical': True,
                  'network_migration_tests_passed': True,
                  'proxy_removal': {'package': '3proxy', 'absent_after': True},
                  'librespeed': {'passed': True, 'http_tests_passed': True, 'firstboot_tests_passed': True,
                                 'service_user_tests_passed': True, 'version': '1.1.5-r5',
                                 'enabled': '1', 'listen_port': '8989', 'database_type': 'none'},
                  'default_theme': 'footstrap', 'theme_default_tests_passed': True,
                  'wifi_security_tests_passed': True, 'ipv6_client_tests_passed': True, 'removed_packages_absent': True,
                  'themes_compatibility_passed': True,
                  'usteer_ipc_tests_passed': True,
                  'base_release': {'version': '25.12.2', 'revision': 'test'}}
        (self.dist / 'BUILD-REPORT.json').write_text(json.dumps(report))
        (self.dist / 'ARGON-COMPATIBILITY.json').write_text(json.dumps({'fail_count': 0, 'pass_count': 26}))
        theme_checks = {'default_theme': 'footstrap', 'fail_count': 0, 'pass_count': 30,
                        'themes': {name: {'version': item['version'], 'fail_count': 0, 'pass_count': 10,
                                         'rendered_views': [] if name == 'argon' else ['login', 'login-error', 'overview', 'network']}
                                   for name, item in release.THEMES.items()}}
        (self.dist / 'THEMES-COMPATIBILITY.json').write_text(json.dumps(theme_checks))
        (self.dist / 'image-metadata.json').write_text('{}\n')
        (self.dist / 'SHA256SUMS').write_text(f'{release.sha(image)}  {image.name}\n')
        self.env = patch.dict(os.environ, {'GITHUB_REPOSITORY': 'example/ap8220', 'GITHUB_SHA': 'b' * 40,
                                         'GITHUB_RUN_ID': '12345', 'GITHUB_RUN_ATTEMPT': '1', 'GITHUB_STEP_SUMMARY': ''})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.info = release.prepare(self.dist, self.output)

    def test_release_contains_only_firmware_and_sha256sum_assets(self):
        self.assertTrue(self.info['prerelease'])
        release.validate_assets(self.output)
        image = 'QWRT-AP8220-unit-test-sysupgrade.bin'
        self.assertEqual(self.info['assets'], [image, 'SHA256SUMS'])
        self.assertEqual({p.name for p in self.output.iterdir() if p.is_file()}, {image, 'SHA256SUMS'})
        self.assertEqual((self.output / 'SHA256SUMS').read_text(), f'{release.sha(self.output / image)}  {image}\n')
        self.assertFalse(list(self.output.rglob('*.zip')))

    def test_release_body_describes_firmware_without_password_values(self):
        report_path = self.dist / 'BUILD-REPORT.json'
        report = json.loads(report_path.read_text())
        report['customization'] = {'wifi_2g': {'ssid': 'Two|Name', 'channel': '6', 'key': 'Synthetic-WiFi-Key'},
                                   'wifi_5g': {'ssid': 'Five', 'channel': '149'}, 'root_password': 'Synthetic-Root-Key'}
        report_path.write_text(json.dumps(report))
        output = self.root / 'described-release'
        release.prepare(self.dist, output)
        notes = (output / release.PRIVATE_DIR / 'RELEASE-NOTES.md').read_text()
        for term in ['Footstrap', 'lan_v6', 'DHCPv6', 'psk-mixed', 'Two&#124;Name', 'LibreSpeed', '8989']:
            self.assertIn(term, notes)
        self.assertNotIn('Synthetic-WiFi-Key', notes)
        self.assertNotIn('Synthetic-Root-Key', notes)

    def test_local_release_notes_tampering_blocks_every_remote_call(self):
        (self.output / release.PRIVATE_DIR / 'RELEASE-NOTES.md').write_text('changed')
        with patch.object(release, 'gh_run') as gh, self.assertRaisesRegex(ValueError, 'notes checksum mismatch'):
            release.publish(self.output)
        gh.assert_not_called()

    def test_skipped_ipc_or_changed_password_files_prevent_release(self):
        report_path = self.dist / 'BUILD-REPORT.json'
        report = json.loads(report_path.read_text())
        for field in ['usteer_ipc_tests_passed', 'account_files_byte_identical', 'network_migration_tests_passed',
                      'wifi_security_tests_passed', 'ipv6_client_tests_passed', 'removed_packages_absent']:
            bad = dict(report, **{field: False})
            report_path.write_text(json.dumps(bad))
            with self.assertRaises(ValueError):
                release.prepare(self.dist, self.root / ('rejected-' + field))
        report_path.write_text(json.dumps(report))

    def test_custom_root_policy_requires_firstboot_verification_and_keeps_original_account_guard(self):
        path = self.dist / 'BUILD-REPORT.json'
        report = json.loads(path.read_text())
        report['password_policy'] = 'custom-first-boot'
        path.write_text(json.dumps(report))
        with self.assertRaisesRegex(ValueError, 'Custom root password first-boot test'):
            release.prepare(self.dist, self.root / 'custom-root-rejected')
        report['custom_root_password_firstboot_test_passed'] = True
        path.write_text(json.dumps(report))
        release.prepare(self.dist, self.root / 'custom-root-verified')
        report['account_files_byte_identical'] = False
        path.write_text(json.dumps(report))
        with self.assertRaisesRegex(ValueError, 'Password/account preservation'):
            release.prepare(self.dist, self.root / 'custom-root-changed-original')

    def test_librespeed_checks_and_requested_defaults_are_required(self):
        path = self.dist / 'BUILD-REPORT.json'
        report = json.loads(path.read_text())
        for key, value in [('passed', False), ('http_tests_passed', False), ('firstboot_tests_passed', False),
                           ('service_user_tests_passed', False), ('enabled', '0'), ('listen_port', '80'),
                           ('database_type', 'bolt'), ('version', 'unknown')]:
            bad = dict(report, librespeed=dict(report['librespeed'], **{key: value}))
            path.write_text(json.dumps(bad))
            with self.assertRaisesRegex(ValueError, 'LibreSpeed'):
                release.prepare(self.dist, self.root / ('reject-speed-' + key))
        path.write_text(json.dumps(report))

    def test_footstrap_default_and_page_render_checks_are_required(self):
        path = self.dist / 'THEMES-COMPATIBILITY.json'
        original = json.loads(path.read_text())
        for theme in ['footstrap']:
            invalid = json.loads(json.dumps(original))
            invalid['themes'][theme]['rendered_views'].remove('login')
            path.write_text(json.dumps(invalid))
            with self.assertRaisesRegex(ValueError, 'native LuCI page render checks are incomplete'):
                release.prepare(self.dist, self.root / ('missing-render-' + theme))
        path.write_text(json.dumps(original))
        report_path = self.dist / 'BUILD-REPORT.json'
        report = json.loads(report_path.read_text())
        report['default_theme'] = 'argon'
        report_path.write_text(json.dumps(report))
        with self.assertRaisesRegex(ValueError, 'Footstrap default theme initialization'):
            release.prepare(self.dist, self.root / 'wrong-default-theme')

    def test_experimental_usteer_is_prerelease_even_for_known_baseline(self):
        report_path = self.dist / 'BUILD-REPORT.json'
        report = json.loads(report_path.read_text())
        report['baseline_source_matches_user_tested_image'] = True
        report_path.write_text(json.dumps(report))
        result = release.prepare(self.dist, self.root / 'baseline-release')
        self.assertTrue(result['prerelease'])

    def test_tampered_asset_stops_before_any_github_call(self):
        (self.output / 'QWRT-AP8220-unit-test-sysupgrade.bin').write_bytes(b'tampered')
        with patch.object(release, 'gh_run') as gh:
            with self.assertRaisesRegex(ValueError, 'checksum mismatch'):
                release.publish(self.output)
            gh.assert_not_called()

    def test_upload_failure_does_not_publish(self):
        gh = FakeGitHub(self.output, self.info, fail_upload=True)
        with patch.object(release, 'gh_run', gh), self.assertRaisesRegex(RuntimeError, 'upload failure'):
            release.publish(self.output)
        self.assertFalse(any(api_mutation(c, 'PATCH') for c in gh.calls))
        self.assertTrue(gh.state['draft'])

    def test_github_digest_mismatch_does_not_publish(self):
        gh = FakeGitHub(self.output, self.info, corrupt_digest=True)
        with patch.object(release, 'gh_run', gh), self.assertRaisesRegex(ValueError, 'SHA256 differs'):
            release.publish(self.output)
        self.assertTrue(gh.state['draft'])

    def test_success_publishes_after_verifying_all_assets(self):
        gh = FakeGitHub(self.output, self.info)
        with patch.object(release, 'gh_run', gh):
            url = release.publish(self.output)
        self.assertFalse(gh.state['draft'])
        self.assertIn('/releases/tag/', url)
        last_upload = max(i for i, c in enumerate(gh.calls) if c[1].startswith('https://uploads.github.com/'))
        published = next(i for i, c in enumerate(gh.calls) if api_mutation(c, 'PATCH'))
        self.assertLess(last_upload, published)
        self.assertEqual(gh.calls[last_upload + 1], ['api', 'repos/example/ap8220/releases/42'])
        self.assertEqual(gh.calls[published + 1], ['api', 'repos/example/ap8220/releases/42'])
        self.assertEqual(gh.uploaded_names, self.info['assets'])

    def test_created_release_id_works_when_tag_and_list_hide_the_draft(self):
        # Reproduce v0.5.0's "Created draft Release was not found" path.
        gh = FakeGitHub(self.output, self.info, list_hides_drafts=True)
        with patch.object(release, 'gh_run', gh):
            release.publish(self.output)
        self.assertFalse(gh.state['draft'])
        # Discovery is permitted only before POST; creation's ID is authoritative.
        created = next(i for i, c in enumerate(gh.calls) if api_mutation(c, 'POST'))
        self.assertFalse(any('/tags/' in c[1] or '?per_page=' in c[1] for c in gh.calls[created + 1:]))

    def test_failed_upload_retry_resumes_the_same_draft(self):
        gh = FakeGitHub(self.output, self.info, fail_upload=True, fail_after_uploads=1)
        with patch.object(release, 'gh_run', gh):
            with self.assertRaisesRegex(RuntimeError, 'upload failure'):
                release.publish(self.output)
            self.assertTrue(gh.state['draft'])
            gh.fail_upload = False
            release.publish(self.output)
        self.assertFalse(gh.state['draft'])
        self.assertEqual(sum(api_mutation(c, 'POST') and not c[1].startswith('https:') for c in gh.calls), 1)
        self.assertEqual(gh.uploaded_names, self.info['assets'])

    def test_existing_draft_on_later_page_is_reused(self):
        gh = FakeGitHub(self.output, self.info, draft_on_later_page=True)
        gh.state = dict(gh.remote(True), assets=[])
        with patch.object(release, 'gh_run', gh):
            release.publish(self.output)
        self.assertFalse(gh.state['draft'])
        self.assertFalse(any(api_mutation(c, 'POST') and not c[1].startswith('https:') for c in gh.calls))

    def test_mismatched_partial_asset_is_replaced_only_in_the_draft(self):
        gh = FakeGitHub(self.output, self.info)
        gh.state = gh.remote(True)
        gh.state['assets'][0]['digest'] = 'sha256:' + '0' * 64
        with patch.object(release, 'gh_run', gh):
            release.publish(self.output)
        self.assertFalse(gh.state['draft'])
        self.assertEqual(gh.uploaded_names, [self.info['assets'][0]])
        self.assertEqual(sum(api_mutation(c, 'DELETE') for c in gh.calls), 1)

    def test_unexpected_draft_assets_are_not_deleted_or_published(self):
        gh = FakeGitHub(self.output, self.info)
        gh.state = gh.remote(True)
        gh.state['assets'][0]['name'] = 'unexpected.bin'
        with patch.object(release, 'gh_run', gh), self.assertRaisesRegex(ValueError, 'unexpected or duplicate'):
            release.publish(self.output)
        self.assertTrue(gh.state['draft'])
        self.assertFalse(any('--method' in c for c in gh.calls))

    def test_create_response_identity_is_checked_before_any_upload(self):
        for changed, message in [({'id': True}, 'Invalid GitHub Release ID'),
                                 ({'tag_name': 'wrong-tag'}, 'different Release tag'),
                                 ({'target_commitish': 'c' * 40}, 'different target commit'),
                                 ({'upload_url': 'https://example.com/upload'}, 'Unexpected Release upload URL')]:
            with self.subTest(changed=changed):
                gh = FakeGitHub(self.output, self.info)

                def malformed(argv, **kwargs):
                    result = gh(argv, **kwargs)
                    if api_mutation(argv, 'POST') and argv[1] == 'repos/example/ap8220/releases':
                        return json.dumps(dict(json.loads(result), **changed))
                    return result

                with patch.object(release, 'gh_run', malformed), self.assertRaisesRegex(ValueError, message):
                    release.publish(self.output)
                self.assertTrue(gh.state['draft'])
                self.assertEqual(gh.uploaded_names, [])

    def test_existing_draft_from_another_commit_is_rejected_before_upload(self):
        gh = FakeGitHub(self.output, self.info)
        gh.state = dict(gh.remote(True), target_commitish='c' * 40)
        with patch.object(release, 'gh_run', gh), self.assertRaisesRegex(ValueError, 'different target commit'):
            release.publish(self.output)
        self.assertTrue(gh.state['draft'])
        self.assertFalse(any('--method' in c for c in gh.calls))

    def test_draft_list_permission_failure_does_not_create_a_release(self):
        gh = FakeGitHub(self.output, self.info)

        def denied(argv, missing_ok=False):
            if argv[0] == 'api' and '?' in argv[1]:
                raise RuntimeError('GitHub CLI failed: HTTP 403')
            return gh(argv, missing_ok=missing_ok)

        with patch.object(release, 'gh_run', denied), self.assertRaisesRegex(RuntimeError, '403'):
            release.publish(self.output)
        self.assertFalse(any('--method' in c for c in gh.calls))

    def test_published_retry_only_checks_existing_assets(self):
        gh = FakeGitHub(self.output, self.info, already_published=True)
        with patch.object(release, 'gh_run', gh):
            release.publish(self.output)
        self.assertEqual(len(gh.calls), 1)

    def test_assets_from_a_different_commit_are_not_published(self):
        with patch.dict(os.environ, {'GITHUB_SHA': 'c' * 40}), patch.object(release, 'gh_run') as gh:
            with self.assertRaisesRegex(ValueError, 'different repository, commit'):
                release.publish(self.output)
            gh.assert_not_called()


class ProfileCustomizationTests(unittest.TestCase):
    def test_chinese_mode_choices_keep_shared_and_separate_behavior(self):
        self.assertEqual(profile.validate_inputs({'wifi_mode': '共用'})['wifi_mode'], 'shared')
        self.assertEqual(profile.validate_inputs({'wifi_mode': '分别'})['wifi_mode'], 'separate')

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.project = Path(self.temp.name)
        (self.project / 'files/etc/config').mkdir(parents=True)
        (self.project / 'packages').mkdir()
        self.config = self.project / 'files/etc/config/ap8220_ap'
        self.config.write_bytes((release.PROJECT / 'files/etc/config/ap8220_ap').read_bytes())

    def test_shared_and_separate_settings_and_password_fallback(self):
        shared = profile.validate_inputs({'ssid_2g': 'Shared Name', 'wifi_key_2g': 'shared-key',
                                         'ssid_5g': 'Ignored', 'wifi_key_5g': 'x'})
        self.assertEqual((shared['ssid_5g'], shared['key_5g']), ('Shared Name', 'shared-key'))
        separate = profile.validate_inputs({'wifi_mode': 'separate', 'ssid_2g': 'Two', 'ssid_5g': 'Five',
                                           'wifi_key_2g': 'two-band-key', 'channel_2g': 'auto', 'channel_5g': '44'})
        self.assertEqual((separate['ssid_5g'], separate['key_5g']), ('Five', 'two-band-key'))
        self.assertEqual(separate['channel_2g'], 'auto')

    def test_invalid_utf8_lengths_controls_and_channels_are_rejected_without_echoing_values(self):
        cases = [{'ssid_2g': '中' * 11}, {'wifi_key_2g': 'short'}, {'ssid_2g': 'name\nconfig interface bad'},
                 {'wifi_mode': 'invalid'}, {'channel_2g': '14'}, {'channel_5g': '165'},
                 {'root_password': 'private\npassword'}, {'root_password': 'x' * 257}]
        before = self.config.read_bytes()
        for inputs in cases:
            with self.subTest(field=next(iter(inputs))), self.assertRaises(ValueError) as error:
                profile.configure(self.project, inputs)
            for value in inputs.values():
                self.assertNotIn(value, str(error.exception))
        self.assertEqual(self.config.read_bytes(), before)

    def test_rendering_preserves_identity_and_safely_quotes_wifi_settings(self):
        self.config.write_text(self.config.read_text().replace("option base_mac ''", "option base_mac '48:51:c5:12:34:56'"))
        inputs = {'wifi_mode': 'separate', 'ssid_2g': "中文 ' $()", 'ssid_5g': 'Five "quoted"',
                  'wifi_key_2g': 'key $() \\ quote', 'wifi_key_5g': "other'key", 'channel_2g': '11', 'channel_5g': 'auto'}
        summary = profile.configure(self.project, inputs)
        self.assertIn("option base_mac '48:51:c5:12:34:56'", self.config.read_text())
        self.assertEqual(summary['wifi_5g']['ssid'], inputs['ssid_5g'])
        self.assertNotIn(inputs['wifi_key_2g'], json.dumps(summary))
        self.assertNotIn('root_password', summary)
        self.assertEqual(self.config.stat().st_mode & 0o777, 0o600)

    def test_root_password_is_hashed_on_stdin_and_blank_input_removes_old_override(self):
        password = "Synthetic root: ' $() 密码"
        summary = profile.configure(self.project, {'root_password': password})
        path = self.project / 'files/etc/ap8220-root-password.hash'
        hashed = path.read_text().strip()
        self.assertRegex(hashed, '^' + profile.HASH_PATTERN + '$')
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(summary['password_policy'], 'custom-first-boot')
        check = subprocess.run(['openssl', 'passwd', '-1', '-salt', hashed.split('$')[2], '-stdin'],
                               input=password + '\n', text=True, capture_output=True, check=True)
        self.assertEqual(check.stdout.strip(), hashed)
        for p in (self.project / 'files').rglob('*'):
            if p.is_file():
                self.assertNotIn(password.encode(), p.read_bytes())
        summary = profile.configure(self.project, {})
        self.assertEqual(summary['password_policy'], 'inherit-input-firmware')
        self.assertFalse(path.exists())

    def test_configuration_ipk_keeps_password_files_private(self):
        profile.configure(self.project, {'root_password': 'Synthetic-packaging-password'})
        package = build.create_config_ipk(self.project)
        with tarfile.open(package, 'r:gz') as outer:
            with tarfile.open(fileobj=io.BytesIO(outer.extractfile('./data.tar.gz').read()), mode='r:gz') as data:
                for name in ['./etc/config/ap8220_ap', './etc/ap8220-root-password.hash']:
                    self.assertEqual(data.getmember(name).mode, 0o600)

    def test_event_inputs_never_become_shell_code_or_plaintext_root_files(self):
        marker = self.project / 'unexpected-shell-execution'
        password = f'root $(touch {marker})'
        event = self.project / 'event.json'
        event.write_text(json.dumps({'inputs': {'root_password': password}}))
        result = subprocess.run([sys.executable, release.PROJECT / 'tools/configure_profile.py',
                                 '--event', event, '--project', self.project],
                                env=dict(os.environ, GITHUB_ACTIONS='false'), text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(marker.exists())
        self.assertNotIn(password, result.stdout + result.stderr)
        self.assertNotIn(password, (self.project / 'files/etc/ap8220-build-profile.json').read_text())


class OriginalFilePreservationTests(unittest.TestCase):
    def test_squashfs_permissions_roundtrip_and_content_tamper_detection(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            source, unpacked = base / 'source', base / 'unpacked'
            (source / 'etc').mkdir(parents=True)
            (source / 'usr/bin').mkdir(parents=True)
            fixture = {'etc/passwd': (b'root:original-account\n', 0o664),
                       'etc/shadow': (b'root:original-password-hash\n', 0o600),
                       'usr/bin/fusermount': (b'#!/bin/sh\nexit 0\n', 0o4755),
                       'usr/bin/script': (b'#!/bin/sh\nexit 0\n', 0o775)}
            for name, (data, mode) in fixture.items():
                (source / name).write_bytes(data)
                (source / name).chmod(mode)
            (source / 'etc').chmod(0o775)
            outside = base / 'outside'
            outside.write_bytes(b'external target must not be chmodded')
            outside.chmod(0o600)
            (source / 'usr/bin/link').symlink_to(outside)

            def squash(tree, image):
                subprocess.run(['mksquashfs', str(tree), str(image), '-noappend', '-no-progress',
                                '-no-xattrs', '-all-root', '-processors', '1'],
                               check=True, text=True, capture_output=True)

            original_image = base / 'original.squashfs'
            squash(source, original_image)
            original = build.snapshot(original_image)
            subprocess.run(['unsquashfs', '-no-progress', '-no-xattrs', '-processors', '1',
                            '-d', str(unpacked), str(original_image)],
                           check=True, text=True, capture_output=True, umask=0o022)
            if os.geteuid() != 0:
                self.assertEqual(stat.S_IMODE((unpacked / 'etc/passwd').stat().st_mode), 0o644)
                self.assertEqual(stat.S_IMODE((unpacked / 'usr/bin/fusermount').stat().st_mode), 0o755)
            # Reproduce the runner's loss on root development environments too.
            for name, entry in original.items():
                if entry[0] == 'file':
                    (unpacked / name.lstrip('/')).chmod(stat.S_IMODE(entry[1]) & ~0o6022)
            added = unpacked / 'new-ap-init'
            added.write_bytes(b'new package file')
            added.chmod(0o750)
            restored = build.restore_original_modes(unpacked, original)
            self.assertGreaterEqual(restored['modes_restored'], 3)
            self.assertEqual(stat.S_IMODE(added.stat().st_mode), 0o750)
            self.assertEqual(stat.S_IMODE(outside.stat().st_mode), 0o600)
            for name, (data, mode) in fixture.items():
                self.assertEqual((unpacked / name).read_bytes(), data)
                self.assertEqual(stat.S_IMODE((unpacked / name).stat().st_mode), mode)
            rebuilt = base / 'rebuilt.squashfs'
            squash(unpacked, rebuilt)
            diff = build.compare_original_entries(original, build.snapshot(rebuilt))
            self.assertEqual(diff['missing_paths'], [])
            self.assertEqual(diff['changed_paths'], [])
            # Restoring modes must never hide a changed password file.
            (unpacked / 'etc/shadow').write_bytes(b'root:tampered-password-hash\n')
            build.restore_original_modes(unpacked, original)
            tampered = base / 'tampered.squashfs'
            squash(unpacked, tampered)
            diff = build.compare_original_entries(original, build.snapshot(tampered))
            self.assertEqual([entry['path'] for entry in diff['changed_paths']], ['/etc/shadow'])
            self.assertEqual(diff['changed_paths'][0]['fields'], ['content'])

    def test_expected_regular_file_replaced_by_symlink_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            root = base / 'root'
            root.mkdir()
            outside = base / 'outside'
            outside.write_bytes(b'external')
            outside.chmod(0o600)
            (root / 'file').symlink_to(outside)
            with self.assertRaisesRegex(ValueError, 'path type changed'):
                build.restore_original_modes(root, {'/file': ('file', stat.S_IFREG | 0o664, 'hash')})
            self.assertEqual(stat.S_IMODE(outside.stat().st_mode), 0o600)


class ProxyRemovalTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / 'root'
        self.root.mkdir()
        self.proxy_record = 'Package: 3proxy\nVersion: 0.9.5-4\nDepends: libc\nStatus: install ok installed\n\n'
        self.other_records = ('Package: libc\nStatus: install ok installed\n\n'
                              'Package: luci-app-ssr-plus\nDepends: libc, microsocks\nStatus: install ok installed\n\n'
                              'Package: microsocks\nStatus: install ok installed\n\n')
        fixture = {name: b'3proxy data' for name in remove_3proxy.OWNED_PATHS | remove_3proxy.METADATA_PATHS}
        fixture.update({'/usr/lib/opkg/info/3proxy.list': ('\n'.join(sorted(remove_3proxy.OWNED_PATHS)) + '\n').encode(),
                        '/usr/lib/opkg/info/3proxy.control': b'Package: 3proxy\nVersion: 0.9.5-4\n',
                        '/usr/lib/opkg/status': (self.proxy_record + self.other_records).encode(),
                        '/usr/lib/opkg/info/microsocks.list': b'/usr/bin/microsocks\n',
                        '/usr/bin/microsocks': b'microsocks executable',
                        '/etc/init.d/shadowsocksr': b'original SSR Plus+ init',
                        '/etc/config/shadowsocksr': b'original SSR Plus+ configuration',
                        '/etc/passwd': b'original accounts', '/etc/shadow': b'original password hash',
                        '/etc/group': b'original groups', '/lib/libc.so': b'original shared library'})
        for name, data in fixture.items():
            file = self.path(name)
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_bytes(data)
            file.chmod(0o600 if name == '/etc/shadow' else 0o644)
        self.path('/etc/rc.d').mkdir()
        self.path('/etc/rc.d/S993proxy').symlink_to('../init.d/3proxy')

    def path(self, name):
        return self.root / name.lstrip('/')

    def snapshot(self):
        return {str(p.relative_to(self.root)): (p.lstat().st_mode,
                os.readlink(p) if p.is_symlink() else p.read_bytes())
                for p in self.root.rglob('*') if p.is_file() or p.is_symlink()}

    def assert_rejected_without_changes(self):
        before = self.snapshot()
        with self.assertRaises(ValueError):
            remove_3proxy.remove(self.root)
        self.assertEqual(before, self.snapshot())

    def test_only_owned_files_and_status_record_are_removed_and_repeat_is_safe(self):
        before = self.snapshot()
        report = remove_3proxy.remove(self.root)
        expected = remove_3proxy.OWNED_PATHS | remove_3proxy.METADATA_PATHS | {'/etc/rc.d/S993proxy'}
        self.assertEqual(set(report['removed_paths']), expected)
        self.assertEqual(self.path('/usr/lib/opkg/status').read_text(), self.other_records)
        after = self.snapshot()
        self.assertEqual(set(before) - set(after), {p.lstrip('/') for p in expected})
        self.assertEqual(set(after) - set(before), set())
        for name in after:
            if name != 'usr/lib/opkg/status':
                self.assertEqual(before[name], after[name], name)
        self.assertEqual(remove_3proxy.remove(self.root)['removed_paths'], [])

    def test_hard_dependency_blocks_removal(self):
        with self.path('/usr/lib/opkg/status').open('a') as status:
            status.write('Package: dependent\nDepends: 3proxy (>= 0.9)\nStatus: install ok installed\n\n')
        self.assert_rejected_without_changes()

    def test_manifest_cannot_delete_password_or_unknown_paths(self):
        with self.path('/usr/lib/opkg/info/3proxy.list').open('a') as manifest:
            manifest.write('/etc/shadow\n')
        self.assert_rejected_without_changes()

    def test_shared_file_ownership_blocks_removal(self):
        self.path('/usr/lib/opkg/info/microsocks.list').write_text('/usr/bin/microsocks\n/usr/bin/3proxy\n')
        self.assert_rejected_without_changes()

    def test_startup_link_to_another_service_is_rejected(self):
        link = self.path('/etc/rc.d/S993proxy')
        link.unlink()
        link.symlink_to('../init.d/shadowsocksr')
        self.assert_rejected_without_changes()

    def test_removal_exception_does_not_hide_other_missing_or_retained_paths(self):
        original = {'/usr/bin/3proxy': ('file', 0o755, 'proxy'), '/etc/shadow': ('file', 0o600, 'hash')}
        difference = build.compare_original_entries(original, {}, allowed_removals=['/usr/bin/3proxy'])
        self.assertEqual(difference['missing_paths'], ['/etc/shadow'])
        self.assertEqual(difference['removed_paths'], ['/usr/bin/3proxy'])
        difference = build.compare_original_entries(original, original, allowed_removals=['/usr/bin/3proxy'])
        self.assertEqual(difference['unremoved_paths'], ['/usr/bin/3proxy'])

    def test_atomic_status_replacement_is_safe_from_stale_open_descriptor(self):
        status = self.path('/usr/lib/opkg/status')
        with status.open('r+') as stale:
            remove_3proxy.remove(self.root)
            stale.seek(0)
            stale.write(self.proxy_record + self.other_records)
            stale.truncate()
            stale.flush()
        self.assertEqual(status.read_text(), self.other_records)

    def test_release_rejects_missing_or_failed_removal_check(self):
        fixture = ReleaseTests('test_release_contains_only_firmware_and_sha256sum_assets')
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        path = fixture.dist / 'BUILD-REPORT.json'
        report = json.loads(path.read_text())
        for value in [None, {}, {'absent_after': False}]:
            report['proxy_removal'] = value
            path.write_text(json.dumps(report))
            with self.assertRaisesRegex(ValueError, '3proxy removal'):
                release.prepare(fixture.dist, fixture.root / ('reject-' + str(len(str(value)))))


class SourceCompatibilityTests(unittest.TestCase):
    def test_incompatible_distribution_or_device_is_rejected(self):
        valid = {'supported_devices': ['aliyun,ap8220'], 'version': {'dist': 'QWRT', 'target': 'ipq807x/generic', 'board': 'aliyun_ap8220'}}
        build.validate_metadata(valid)
        for key, value in [('dist', 'OpenWrt'), ('target', 'qualcommax/ipq807x'), ('board', 'redmi_ax6')]:
            changed = json.loads(json.dumps(valid))
            changed['version'][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                build.validate_metadata(changed)


if __name__ == '__main__':
    unittest.main()
