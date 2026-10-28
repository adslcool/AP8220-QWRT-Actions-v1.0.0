#!/usr/bin/env python3
"""Real QWRT ubusd/ucode/usteerd protocol tests, with simulated hostapd sockets.

No wireless hardware, router connections, MTD access or system services. Every
bus and control socket is private. This verifies the adapter, not QCA RF behavior.
"""
import argparse
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import threading
import time

CLIENT = '02:10:20:30:40:50'


class Hostapd:
    def __init__(self, base, iface, bssid, channel, freq, ssid='Home-WiFi'):
        self.path = base / ('hostapd-' + iface) / iface
        self.path.parent.mkdir(exist_ok=True)
        self.bssid, self.ssid, self.channel, self.freq = bssid, ssid, channel, freq
        opclass = 128 if freq > 5000 else 81
        self.nr = bssid.replace(':', '') + 'ef590000' + bytes([opclass, channel, 9]).hex()
        self.neighbors = {bssid: (ssid, self.nr)}
        self.commands, self.attached = [], set()
        self.fail_set, self.missing_own, self.enabled = False, False, True
        self.signal = '-54'
        self.s = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        self.s.bind(str(self.path))
        self.s.settimeout(.1)
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self.serve, daemon=True)
        self.thread.start()

    def serve(self):
        while not self.stop_event.is_set():
            try:
                data, addr = self.s.recvfrom(65535)
            except socket.timeout:
                continue
            except OSError:
                return
            cmd = data.decode()
            self.commands.append(cmd)
            answer = 'FAIL'
            if cmd == 'STATUS':
                answer = (f'state={"ENABLED" if self.enabled else "DISABLED"}\nfreq={self.freq}\n'
                          f'channel={self.channel}\nbeacon_int=100\nbssid[0]={self.bssid}\nssid[0]={self.ssid}\n')
            elif cmd == 'SHOW_NEIGHBOR':
                answer = '\n'.join(f'{b} ssid={s.encode().hex()} nr={nr}'
                                   for b, (s, nr) in self.neighbors.items()
                                   if not (self.missing_own and b == self.bssid))
            elif cmd == 'STA-FIRST':
                answer = (f'{CLIENT}\nflags=[AUTH][ASSOC][AUTHORIZED]\n'
                          f'signal={self.signal}\next_capab=000008\nconnected_time=17\n')
            elif cmd.startswith('SET_NEIGHBOR ') and not self.fail_set:
                _, bssid, ssid, nr = cmd.split()
                self.neighbors[bssid] = (bytes.fromhex(ssid[5:]).decode(), nr[3:])
                answer = 'OK'
            elif cmd.startswith('REMOVE_NEIGHBOR '):
                bssid = cmd.split()[1]
                if bssid in self.neighbors and bssid != self.bssid:
                    del self.neighbors[bssid]
                    answer = 'OK'
            elif cmd == 'ATTACH':
                self.attached.add(addr)
                answer = 'OK'
            elif cmd.startswith('BSS_TM_REQ '):
                answer = 'OK'
            try:
                self.s.sendto((answer + '\n').encode(), addr)
            except OSError:
                pass

    def notify(self, message):
        for addr in list(self.attached):
            try:
                self.s.sendto(('<3>' + message).encode(), addr)
            except OSError:
                self.attached.discard(addr)

    def close(self):
        self.stop_event.set()
        self.thread.join(1)
        self.s.close()
        self.path.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--rootfs', type=Path, required=True)
    parser.add_argument('--qemu', type=Path, required=True)
    parser.add_argument('--usteerd', type=Path, required=True)
    args = parser.parse_args()
    if os.geteuid() != 0:
        parser.error('QWRT ubusd ACL requires root for object publishing; run this isolated test with sudo')
    root, qemu, executable = args.rootfs.resolve(), args.qemu.resolve(), args.usteerd.resolve()
    project = Path(__file__).resolve().parents[1]
    guest = [str(qemu), '-L', str(root)]
    ucode = guest + [str(root / 'usr/bin/ucode'), '-L', str(root / 'usr/lib/ucode'),
                     '-L', str(project / 'files/usr/share/ucode')]
    passed, procs, radios = [], [], []
    with tempfile.TemporaryDirectory(prefix='aq-') as tmp:
        temp = Path(tmp)
        bus_path = temp / 'bus.sock'
        logs = open(temp / 'process.log', 'w+')

        def start(command, env=None):
            p = subprocess.Popen(command, stdout=logs, stderr=logs, env=env)
            procs.append(p)
            return p

        def call(obj, method, data=None, check=True):
            r = subprocess.run(guest + [str(root / 'bin/ubus'), '-s', str(bus_path), '-t', '2',
                                       'call', obj, method, json.dumps(data or {})],
                               text=True, capture_output=True, timeout=5)
            if check and r.returncode:
                raise AssertionError((obj, method, r.stderr))
            return json.loads(r.stdout or '{}') if check else r

        def wait(condition, timeout=12):
            end = time.monotonic() + timeout
            last = None
            while time.monotonic() < end:
                try:
                    value = condition()
                    if value:
                        return value
                except (AssertionError, KeyError, subprocess.TimeoutExpired) as error:
                    last = error
                if any(p.poll() is not None for p in procs):
                    raise AssertionError('A service exited')
                time.sleep(.08)
            raise AssertionError(('condition timed out', last))

        try:
            start(guest + [str(root / 'sbin/ubusd'), '-s', str(bus_path)])
            wait(bus_path.exists)
            r0 = Hostapd(temp, 'ath0', '48:51:c5:00:00:10', 149, 5745)
            r1 = Hostapd(temp, 'ath1', '48:51:c5:00:00:11', 6, 2437)
            guest_ap = Hostapd(temp, 'ath2', '48:51:c5:00:00:12', 1, 2412, 'Guest')
            radios += [r0, r1, guest_ap]
            start(ucode + [str(project / 'files/usr/sbin/ap8220-usteer-bridge.uc'),
                           str(temp / 'runtime'), str(temp / 'hostapd-*/*'), str(bus_path)])
            wait(lambda: len(call('ap8220.usteer', 'status')['nodes']) == 3)
            status = call('hostapd.ath0', 'get_status')
            assert status['ssid'] == 'Home-WiFi' and status['bssid'] == r0.bssid
            assert status['op_class'] == 128 and status['freq'] == 5745
            sta = call('hostapd.ath0', 'get_clients')['clients'][CLIENT]
            assert sta['signal'] == -54 and sta['authorized'] and sta['extended_capabilities'] == [0, 0, 8]
            passed.append('real QWRT ubusd + ucode: three QCA control endpoints, authentic NR/identity, client RSSI/capabilities')

            daemon = start(guest + [str(executable), '-v', '-v'],
                           dict(os.environ, USTEER_UBUS_SOCKET=str(bus_path)))
            wait(lambda: call('usteer', 'local_info').get('hostapd.ath0', {}).get('ssid') == r0.ssid)
            call('usteer', 'set_config', {'local_mode': True, 'local_sta_update': 250,
                                         'aggressiveness': 1, 'band_steering_interval': 0,
                                         'link_measurement_interval': 0, 'probe_steering': False,
                                         'assoc_steering': False, 'min_snr': 0})
            wait(lambda: r1.bssid in r0.neighbors and r0.bssid in r1.neighbors)
            assert guest_ap.bssid not in r0.neighbors and set(guest_ap.neighbors) == {guest_ap.bssid}
            info = call('usteer', 'local_info')['hostapd.ath0']
            assert info['bssid'] == r0.bssid and info['n_assoc'] == 1
            clients = call('usteer', 'connected_clients')['hostapd.ath0']
            # ubus CLI prints the upstream unsigned representation of negative RSSI.
            assert clients[CLIENT]['signal'] in [-54, 2**32 - 54]
            passed.append('actual patched ARM usteer: SSID/BSSID/RSSI consumed; same-SSID NR synchronization; guest isolation')

            response = {'addr': CLIENT, 'dialog_token': 5, 'disassociation_imminent': False,
                        'abridged': True, 'validity_period': 100, 'neighbors': [r1.nr]}
            call('hostapd.ath0', 'bss_transition_request', response)
            command = next(c for c in reversed(r0.commands) if c.startswith('BSS_TM_REQ '))
            assert 'disassoc_imminent=0' in command and 'dialog_token=5' in command
            assert f'neighbor={r1.bssid},23023,81,6,9' in command
            before = len([c for c in r0.commands if c.startswith('BSS_TM_REQ ')])
            bad = dict(response, disassociation_imminent=True)
            assert call('hostapd.ath0', 'bss_transition_request', bad, check=False).returncode != 0
            assert call('hostapd.ath0', 'del_client', {'addr': CLIENT}, check=False).returncode != 0
            assert call('hostapd.ath0', 'rrm_nr_set', {'list': [[r1.bssid, r1.ssid, r1.nr + ';reboot']]}, check=False).returncode != 0
            assert before == len([c for c in r0.commands if c.startswith('BSS_TM_REQ ')])
            passed.append('advisory BTM maps correctly; forced disconnect and malformed/injected NR are rejected')

            r0.notify(f'BSS-TM-RESP {CLIENT} status_code=7 bss_termination_delay=0')
            wait(lambda: call('usteer', 'connected_clients')['hostapd.ath0'][CLIENT]
                 ['bss-transition-response']['status-code'] == 7)
            passed.append('ATTACH event -> real usteer subscriber: BTM rejection response delivered with matching blob types')

            r1.enabled = False
            wait(lambda: 'ath1' not in call('ap8220.usteer', 'status')['nodes'])
            wait(lambda: r1.bssid not in r0.neighbors)
            r1.enabled = True
            wait(lambda: 'ath1' in call('ap8220.usteer', 'status')['nodes'])
            wait(lambda: r1.bssid in r0.neighbors)
            r1.close(); radios.remove(r1)
            wait(lambda: 'ath1' not in call('ap8220.usteer', 'status')['nodes'])
            r1 = Hostapd(temp, 'ath1', '48:51:c5:00:00:21', 11, 2462)
            radios.append(r1)
            wait(lambda: call('usteer', 'local_info').get('hostapd.ath1', {}).get('bssid') == r1.bssid)
            wait(lambda: r1.bssid in r0.neighbors)
            assert '48:51:c5:00:00:11' not in r0.neighbors
            passed.append('disabled radio/socket loss withdraws nodes and stale NR; recreated socket re-discovers changed BSSID/channel')

            r0.signal = '0'
            wait(lambda: 'signal' not in call('hostapd.ath0', 'get_clients')['clients'][CLIENT])
            wait(lambda: call('usteer', 'connected_clients')['hostapd.ath0'][CLIENT]['signal'] == 255)
            passed.append('a missing RSSI sample invalidates the previous usteer signal rather than keeping a stale value')

            # Stop policy writes, then test controller failure handling deterministically.
            daemon.terminate(); daemon.wait(3); procs.remove(daemon)
            unknown_bssid = '48:51:c5:00:00:30'
            unknown_nr = unknown_bssid.replace(':', '') + 'ef590000510109'
            r0.fail_set = True
            assert call('hostapd.ath0', 'rrm_nr_set', {'list': [[unknown_bssid, r0.ssid, unknown_nr]]}, check=False).returncode != 0
            assert unknown_bssid not in r0.neighbors
            r0.missing_own = True
            wait(lambda: 'ath0' not in call('ap8220.usteer', 'status')['nodes'])
            passed.append('unknown RSSI is omitted, control-command failure is not success, missing own NR prevents publishing a fake-ready node')
            print('\n'.join('PASS: ' + item for item in passed))
        except Exception:
            logs.flush(); logs.seek(0)
            print(logs.read()[-16000:])
            raise
        finally:
            for p in reversed(procs):
                p.terminate()
            for p in reversed(procs):
                try:
                    p.wait(3)
                except subprocess.TimeoutExpired:
                    p.kill(); p.wait()
            for r in radios:
                r.close()
            logs.close()


if __name__ == '__main__':
    main()
