#!/usr/bin/env python3
"""Download an HTTP(S) sysupgrade file, without logging query-string secrets."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import tarfile
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

MAX_BYTES = 256 * 1024 * 1024


def validate_url(url):
    if not url or any(ord(c) <= 32 for c in url):
        raise ValueError('A direct HTTP(S) firmware URL is required; whitespace is not allowed')
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme not in ['http', 'https'] or not parsed.hostname:
        raise ValueError('Only HTTP(S) direct-download URLs are supported')
    if parsed.username is not None or parsed.password is not None or parsed.fragment:
        raise ValueError('Do not use embedded login credentials or URL fragments')
    return parsed


def sanitized_url(url):
    parsed = validate_url(url)
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, '', ''))


class RedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        validate_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def download(url, output, record, expected='', max_bytes=MAX_BYTES, attempts=3):
    validate_url(url)
    expected = expected.strip().lower()
    if expected and not re.fullmatch(r'[0-9a-f]{64}', expected):
        raise ValueError('Expected SHA256 must contain exactly 64 hexadecimal characters')
    output, record = Path(output), Path(record)
    if output.exists() or record.exists():
        raise ValueError('Download output and record must not already exist')
    output.parent.mkdir(parents=True, exist_ok=True)
    record.parent.mkdir(parents=True, exist_ok=True)
    opener = urllib.request.build_opener(RedirectHandler)
    started = time.monotonic()
    last_error = None
    for attempt in range(attempts):
        temporary = None
        try:
            request = urllib.request.Request(url, headers={'User-Agent': 'AP8220-QWRT-Actions/0.2.0', 'Accept-Encoding': 'identity'})
            with opener.open(request, timeout=30) as response:
                declared = response.headers.get('Content-Length')
                if declared and int(declared) > max_bytes:
                    raise ValueError('Firmware download exceeds the size limit')
                digest, size = hashlib.sha256(), 0
                with tempfile.NamedTemporaryFile(dir=output.parent, prefix='.firmware-', suffix='.part', delete=False) as f:
                    temporary = Path(f.name)
                    while True:
                        block = response.read(1024 * 1024)
                        if not block:
                            break
                        size += len(block)
                        if size > max_bytes or time.monotonic() - started > 600:
                            raise ValueError('Firmware download exceeds its size or time limit')
                        digest.update(block)
                        f.write(block)
                actual = digest.hexdigest()
                if expected and actual != expected:
                    raise ValueError('Downloaded firmware SHA256 mismatch')
                if not tarfile.is_tarfile(temporary):
                    raise ValueError('URL did not return a sysupgrade tar image (a web/download landing page is not supported)')
                data = {'source_url': sanitized_url(url), 'sha256': actual, 'size_bytes': size,
                        'expected_sha256_supplied': bool(expected),
                        'expected_sha256_verified': bool(expected)}
                temporary.replace(output)
                record.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n')
                print(f'Downloaded {size} bytes, SHA256 {actual}')
                return data
        except urllib.error.HTTPError as error:
            last_error = f'HTTP {error.code}'
            if error.code not in [408, 429, 500, 502, 503, 504]:
                raise ValueError(f'Firmware download failed: HTTP {error.code}') from None
        except (urllib.error.URLError, TimeoutError, ConnectionError) as error:
            last_error = type(error).__name__
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        if attempt + 1 < attempts:
            time.sleep(min(2 ** attempt, 4))
    raise ValueError(f'Firmware download failed after {attempts} attempts ({last_error})')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--record', type=Path, required=True)
    args = parser.parse_args()
    url = os.environ.get('FIRMWARE_URL', '')
    if os.environ.get('GITHUB_ACTIONS') == 'true':
        escaped = url.replace('%', '%25').replace('\r', '%0D').replace('\n', '%0A')
        print('::add-mask::' + escaped, flush=True)
    try:
        download(url, args.output, args.record, os.environ.get('FIRMWARE_SHA256', ''))
    except (ValueError, OSError) as error:
        raise SystemExit(str(error)) from None


if __name__ == '__main__':
    main()
