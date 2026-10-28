#!/usr/bin/env python3
"""Exercise firmware ash/UCI, password hashing and LuCI migration in fixtures.

No network service, device flashing or real account database is accessed.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import shlex
import subprocess
import sys
import tempfile


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--rootfs', type=Path, required=True)
    ap.add_argument('--qemu', type=Path, required=True)
    ap.add_argument('--crypt-probe', type=Path, required=True)
    args = ap.parse_args()
    rootfs, qemu = args.rootfs.resolve(), args.qemu.resolve()
    ash = [str(qemu), '-L', str(rootfs), str(rootfs / 'bin/busybox'), 'ash']
    project = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(project / 'tools'))
    import configure_profile
    class Checks(list):
        def append(self, name):
            super().append(name)
            print('PASS:', name, flush=True)

    passed = Checks()

    class Fixture:
        def __init__(self, parent, name, product='48:51:c5:12:34:56', serial=None, selected_profile=False):
            self.root = parent / name
            for d in ['etc/config', 'lib/config', 'lib/functions', 'usr/sbin',
                      'bin', 'sbin', 'tmp/uci', 'var/lock', 'etc/init.d',
                      'sys/devices/soc0', 'proc/device-tree', 'tmp/sysinfo']:
                (self.root / d).mkdir(parents=True, exist_ok=True)
            self.env = dict(os.environ, PATH=str(self.root / 'bin') + ':' + os.environ['PATH'],
                            UCI_CONFIG_DIR=str(self.root / 'etc/config'))
            self.env.pop('IPKG_INSTROOT', None)
            (self.root / 'tmp/sysinfo/board_name').write_text('aliyun,ap8220\n')
            for rel in ['lib/functions.sh', 'lib/config/uci.sh', 'lib/functions/system.sh']:
                dest = self.root / rel
                dest.write_text(self.translate((rootfs / rel).read_text()))
            rel = 'usr/sbin/ap8220-ap'
            (self.root / rel).write_text(self.translate((project / 'files' / rel).read_text()))
            wrapper = '#!/bin/sh\nexec ' + ' '.join(map(str, [qemu, '-L', rootfs, rootfs / 'sbin/uci']))
            wrapper += f' -c {self.root}/etc/config -P {self.root}/tmp/uci "$@"\n'
            self.script('bin/uci', wrapper)
            self.script('sbin/uci', wrapper)
            self.script('sbin/wifi', f'#!/bin/sh\nprintf "%s\\n" "$*" >> {self.root}/wifi-reload.log\n')
            self.script('bin/logger', '#!/bin/sh\nexit 0\n')
            self.script('bin/fw_printenv', '#!/bin/sh\nexit 1\n')
            self.script('bin/product_info', f'#!/bin/sh\ncat {self.root}/product_mac 2>/dev/null\n')
            if product is not None:
                (self.root / 'product_mac').write_text(product + '\n')
            if serial:
                (self.root / 'sys/devices/soc0/serial_number').write_text(serial + '\n')
            for name in ['dnsmasq', 'odhcpd', 'firewall', 'miniupnpd', 'upnpd']:
                self.script('etc/init.d/' + name, f'#!/bin/sh\nprintf "%s %s\\n" {name} "$1" >> {self.root}/services.log\n')
            shutil.copy2(project / 'files/etc/config/ap8220_ap', self.root / 'etc/config/ap8220_ap')
            if not selected_profile:
                for name, value in {'wifi_mode': 'shared', 'ssid': 'Home-WiFi', 'key': '12345678',
                                    'ssid_2g': '', 'key_2g': '', 'ssid_5g': 'Home-WiFi-5G', 'key_5g': '',
                                    'channel_2g': '6', 'channel_5g': '149'}.items():
                    self.uci('set', f'ap8220_ap.profile.{name}={value}')
                self.uci('commit', 'ap8220_ap')
            shutil.copy2(rootfs / 'etc/config/luci', self.root / 'etc/config/luci')
            shutil.copy2(rootfs / 'etc/config/dhcp', self.root / 'etc/config/dhcp')
            shutil.copy2(rootfs / 'etc/openwrt_release', self.root / 'etc/openwrt_release')
            (self.root / 'etc/config/system').write_text("config system\n option hostname 'QWRT'\n")
            (self.root / 'etc/shadow').write_text('root:!:0:0:99999:7:::\nnobody:*:0:0:99999:7:::\n')
            (self.root / 'etc/config/network').write_text('''config interface 'loopback'
 option ifname 'lo'
 option proto 'static'
 option ipaddr '127.0.0.1'
 option netmask '255.0.0.0'
config interface 'vpn0'
 option device 'tun0'
 option proto 'none'
config device
 option name 'br-lan'
 option type 'bridge'
 list ports 'eth1'
config device 'br_lan'
 option name 'br-lan'
 option type 'bridge'
 list ports 'eth1'
config interface 'lan'
 option device 'br-lan'
 option proto 'static'
 option ipaddr '192.168.1.1'
 option netmask '255.255.255.0'
 option macaddr 'a2:93:6f:50:4d:2f'
 option ip6assign '60'
config interface 'wan'
 option device 'eth0'
 option proto 'dhcp'
config interface 'wan6'
 option device '@wan'
 option proto 'dhcpv6'
''')
            (self.root / 'etc/config/openvpn').write_text("config openvpn 'myvpn'\n option enabled '1'\nconfig openvpn 'user_vpn'\n option enabled '1'\n")
            (self.root / 'etc/config/firewall').write_text("config zone 'lan'\n option name 'lan'\nconfig zone 'vpn'\n option network 'vpn0'\nconfig rule 'openvpn'\n option name 'openvpn'\nconfig forwarding 'vpntowan'\n option src 'vpn'\nconfig forwarding 'vpntolan'\n option src 'vpn'\nconfig forwarding 'lantovpn'\n option dest 'vpn'\n")

        def translate(self, text):
            return re.sub(r'/(?:etc|lib|bin|sbin|sys|proc|var|tmp|usr)(?=/|\b)',
                          lambda m: str(self.root) + m.group(0), text)

        def script(self, rel, text):
            p = self.root / rel
            p.write_text(text)
            p.chmod(0o755)

        def uci(self, *args, check=True):
            r = subprocess.run([str(self.root / 'bin/uci'), *args], env=self.env,
                               text=True, capture_output=True)
            if check and r.returncode:
                raise AssertionError(r.stderr)
            return r.stdout.strip()

        def run(self, action, success=True):
            r = subprocess.run([*ash, str(self.root / 'usr/sbin/ap8220-ap'), action],
                               env=self.env, text=True, capture_output=True, timeout=30)
            if success:
                assert r.returncode == 0, r.stderr
            else:
                assert r.returncode != 0, r.stderr
            return r

        def luci_migration_counts(self):
            # Run the three actual predicates from this firmware's LuCI view.
            source = (rootfs / 'www/luci-static/resources/view/network/interfaces.js').read_text()
            start = source.index('interfaceBridgeWithIfnameSections(){')
            end = source.index('handleBridgeMigration(', start)
            methods = source[start:end].rstrip(',')
            sections = {}
            for line in self.uci('-X', 'show', 'network').splitlines():
                key, _, value = line.partition('=')
                names = key.split('.')
                values = shlex.split(value)
                if len(names) == 2:
                    sections[names[1]] = {'.name': names[1], '.type': values[0]}
                elif len(names) == 3:
                    sections[names[1]][names[2]] = values[0] if len(values) == 1 else values
            script = '''const fs = require('fs'), vm = require('vm');
const data = JSON.parse(fs.readFileSync(0, 'utf8'));
const uci = { sections: (config, type) => data.sections.filter(s => s['.type'] === type) };
const view = vm.runInNewContext('({' + data.methods + '})', { uci }, { timeout: 1000 });
console.log(JSON.stringify(['interfaceBridgeWithIfnameSections', 'deviceWithIfnameSections',
  'interfaceWithIfnameSections'].map(name => view[name]().length)));
'''
            node = shutil.which('node') or os.environ.get('CODEX_PRIMARY_RUNTIME_NODE')
            assert node, 'Node.js is required to verify the actual LuCI migration predicates'
            result = subprocess.run([node, '-e', script], input=json.dumps({'sections': list(sections.values()), 'methods': methods}),
                                    text=True, capture_output=True, check=True, timeout=5)
            return json.loads(result.stdout)

        def wifi(self, second_mode='11axg', same_parent=False):
            (self.root / 'etc/config/wireless').write_text(f'''config wifi-device 'wifi0'
 option type 'qcawificfg80211'
 option hwmode '11axa'
 option channel '36'
 option htmode 'HT160'
config wifi-device 'wifi1'
 option type 'qcawificfg80211'
 option hwmode '{second_mode}'
 option channel '11'
config wifi-iface 'ath0'
 option device 'wifi0'
 option mode 'ap'
 option network 'lan'
 option ssid 'QWRT_1'
config wifi-iface 'ath1'
 option device '{'wifi0' if same_parent else 'wifi1'}'
 option mode 'ap'
 option network 'lan'
 option ssid 'QWRT_2'
''')

    with tempfile.TemporaryDirectory(prefix='ap8220-uci-test-') as temp:
        parent = Path(temp)
        f = Fixture(parent, 'factory')
        original_shadow = (f.root / 'etc/shadow').read_bytes()
        f.run('base')
        assert f.uci('get', 'network.br_lan.ports') == 'eth1 eth0'
        assert f.uci('get', 'network.lan.proto') == 'dhcp'
        assert not f.uci('-q', 'get', 'network.wan', check=False)
        assert not f.uci('-q', 'get', 'network.wan6', check=False)
        assert not f.uci('-q', 'get', 'network.vpn0', check=False)
        assert f.uci('get', 'network.loopback.device') == 'lo'
        assert f.uci('show', 'network').count(".name='br-lan'") == 1
        assert f.uci('get', 'network.br_lan.macaddr') == '48:51:c5:12:34:56'
        assert f.uci('get', 'system.@system[0].hostname') == 'AP8220-123456'
        assert f.uci('get', 'luci.main.mediaurlbase') == '/luci-static/footstrap'
        for name in ['Argon', 'Footstrap']:
            assert f.uci('get', 'luci.themes.' + name) == '/luci-static/' + name.lower()
        assert not f.uci('get', 'luci.themes.Aurora', check=False)
        for option, expected in [('proto', 'dhcpv6'), ('device', 'br-lan'), ('reqaddress', 'try'),
                                 ('reqprefix', 'no'), ('delegate', '0')]:
            assert f.uci('get', 'network.lan_v6.' + option) == expected
        assert not f.uci('get', 'network.lan_v6.ifname', check=False)
        passed.append('lan_v6 DHCPv6 on br-lan requests an address without delegating prefixes; IPv4 management retained')
        assert f.uci('get', 'dhcp.lan.ignore') == '1'
        assert f.uci('get', 'dhcp.lan.ra') == 'disabled'
        assert set((f.root / 'services.log').read_text().splitlines()) == {
            name + ' disable' for name in ['dnsmasq', 'odhcpd', 'firewall', 'miniupnpd', 'upnpd']}
        assert (f.root / 'etc/shadow').read_bytes() == original_shadow
        # Password policy is wholly inherited, including empty/locked/custom hashes.
        for i, password_field in enumerate(['', '!', '*', '$6$firmware$SENTINEL', 'USER_SET_HASH']):
            pw = Fixture(parent, 'password_' + str(i))
            shadow = f'root:{password_field}:0:0:99999:7:::\nnobody:*:0:0:99999:7:::\n'.encode()
            (pw.root / 'etc/shadow').write_bytes(shadow)
            pw.run('base')
            assert (pw.root / 'etc/shadow').read_bytes() == shadow
        passed.append('factory MAC, duplicate bridge cleanup, two ports, DHCP/RA/service shutdown; Footstrap default and two theme choices; five original password states preserved byte-for-byte')
        f.run('wifi', success=False)
        assert f.uci('get', 'ap8220_ap.core.wifi_configured') == '0'
        f.wifi()
        f.run('wait-wifi')
        assert (f.root / 'wifi-reload.log').read_text().strip() == 'reload_legacy  network_reload_legacy'
        assert f.uci('get', 'wireless.wifi0.htmode') == 'HT80'
        assert f.uci('get', 'wireless.wifi1.htmode') == 'HT20'
        for vif in ['ath0', 'ath1']:
            assert f.uci('get', f'wireless.{vif}.ssid') == 'Home-WiFi'
            assert f.uci('get', f'wireless.{vif}.encryption') == 'psk-mixed'
            assert f.uci('get', f'wireless.{vif}.ieee80211r') == '1'
            assert f.uci('get', f'wireless.{vif}.rrm_neighbor_report') == '1'
            assert f.uci('get', f'wireless.{vif}.bss_transition') == '1'
        assert f.uci('get', 'wireless.ath0.nasid') != f.uci('get', 'wireless.ath1.nasid')
        assert f.uci('get', 'ap8220_ap.core.initialized') == '1'
        passed.append('worker waits for two AP radios and stable wireless file, then applies QSDK HT20/HT80 and reloads once')

        # Use the firmware's own hostapd generator, rather than restating mappings.
        generator = (rootfs / 'lib/wifi/hostapd.sh').read_text()
        generator = generator.replace('. /lib/wifi/wifi-utils.sh', 'get_wpad_var_run() { printf /var/run; }')
        (f.root / 'hostapd-generator.sh').write_text(f.translate(generator))
        emit = f'''. {f.root}/lib/functions.sh
. {f.root}/hostapd-generator.sh
N='\n'
hostapd_set_bss_options CONF ath0
printf '%s\n' "$CONF"
'''
        r = subprocess.run([*ash, '-c', emit], env=f.env, text=True, capture_output=True)
        assert r.returncode == 0, r.stderr
        for expected in ['wpa=3', 'wpa_pairwise=CCMP TKIP', 'FT-PSK', 'mobility_domain=a1b2', 'ft_psk_generate_local=1',
                         'ft_over_ds=0', 'rrm_neighbor_report=1', 'rrm_beacon_report=1',
                         'bss_transition=1', 'nas_identifier=AP8220-4851c5123456-ath0']:
            assert expected in r.stdout, (expected, r.stdout[-2500:], r.stderr[-1000:])
        passed.append('psk-mixed automatic cipher: actual QCA hostapd emits WPA/WPA2, CCMP TKIP, FT-PSK, 11k/v and unique NAS ID')

        f.uci('set', 'wireless.ath0.ssid=User-Changed')
        f.uci('commit', 'wireless')
        f.uci('set', 'wireless.ath0.encryption=psk2')
        f.uci('commit', 'wireless')
        f.uci('set', 'luci.main.mediaurlbase=/luci-static/argon')
        f.uci('commit', 'luci')
        (f.root / 'etc/shadow').write_text('root:USER_CHANGED:0:0:99999:7:::\n')
        f.run('base')
        f.run('wifi')
        f.run('wait-wifi')
        assert f.uci('get', 'wireless.ath0.ssid') == 'User-Changed'
        assert f.uci('get', 'luci.main.mediaurlbase') == '/luci-static/argon'
        assert f.uci('get', 'wireless.ath0.encryption') == 'psk2'
        assert 'USER_CHANGED' in (f.root / 'etc/shadow').read_text()
        passed.append('ordinary restart preserves changed SSID, theme and root password')

        g = Fixture(parent, 'no_identity', product=None)
        g.run('base')
        assert g.uci('get', 'ap8220_ap.core.mac_pending') == '1'
        assert g.uci('get', 'ap8220_ap.core.mac_verified', check=False) == ''
        assert g.uci('get', 'network.br_lan.macaddr', check=False) == ''
        assert g.uci('get', 'ap8220_ap.core.initialized') == '0'
        g.uci('set', 'ap8220_ap.profile.base_mac=48:51:c5:65:43:21')
        g.uci('commit', 'ap8220_ap')
        g.run('identity')
        assert g.uci('get', 'ap8220_ap.core.mac_source') == 'manual'
        passed.append('missing factory data never freezes the old random MAC; manual identity recovery')

        h = Fixture(parent, 'serial', product=None, serial='0x12345678')
        h.run('base')
        expected_digest = hashlib.sha256(b'AP8220-QWRT-AP:0x12345678').hexdigest()[:10]
        expected_mac = '02:' + ':'.join(expected_digest[i:i+2] for i in range(0, 10, 2))
        assert h.uci('get', 'ap8220_ap.core.base_mac') == expected_mac
        assert h.uci('get', 'ap8220_ap.core.mac_source') == 'soc_serial'
        h.wifi(second_mode='unexpected')
        before = (h.root / 'etc/config/wireless').read_bytes()
        h.run('wifi', success=False)
        assert (h.root / 'etc/config/wireless').read_bytes() == before
        assert not h.uci('changes', 'wireless')
        passed.append('deterministic serial fallback is marked as local; unknown radio leaves wireless unmodified')

        j = Fixture(parent, 'same_parent')
        j.run('base')
        j.wifi(same_parent=True)
        j.run('wifi', success=False)
        assert j.uci('get', 'ap8220_ap.core.wifi_configured') == '0'
        passed.append('two AP interfaces on one radio do not pass dual-radio readiness')

        legacy = Fixture(parent, 'legacy_network')
        with (legacy.root / 'etc/config/network').open('a') as stream:
            stream.write('''config interface 'aux'
 option type 'bridge'
 option ifname 'eth2 eth3'
 option proto 'none'
 option mtu '1400'
 option macaddr '48:51:c5:11:22:33'
 option igmp_snooping '1'
config device 'old_bridge'
 option name 'br-extra'
 option type 'bridge'
 option ifname 'eth4 eth5'
config device 'vlan_parent'
 option name 'eth8.10'
 option type '8021q'
 option ifname 'eth8'
 option vid '10'
config interface 'alias'
 option ifname '@lan'
 option proto 'none'
config interface 'already_modern'
 option ifname 'eth9'
 option device 'eth10'
 option proto 'none'
''')
        assert all(count > 0 for count in legacy.luci_migration_counts())
        legacy.run('base')
        assert legacy.luci_migration_counts() == [0, 0, 0]
        assert legacy.uci('get', 'network.loopback.device') == 'lo'
        assert legacy.uci('get', 'network.alias.device') == '@lan'
        assert legacy.uci('get', 'network.already_modern.device') == 'eth10'
        assert legacy.uci('get', 'network.vlan_parent.ifname') == 'eth8'
        assert legacy.uci('get', 'network.old_bridge.ports') == 'eth4 eth5'
        assert legacy.uci('get', 'network.aux.device') == 'br-aux'
        devices = [line.split('.')[1] for line in legacy.uci('-X', 'show', 'network').splitlines() if line.endswith(".name='br-aux'")]
        assert len(devices) == 1
        for option, expected in [('ports', 'eth2 eth3'), ('mtu', '1400'), ('macaddr', '48:51:c5:11:22:33'), ('igmp_snooping', '1')]:
            assert legacy.uci('get', f'network.{devices[0]}.{option}') == expected
        assert not legacy.uci('get', 'network.vpn0', check=False)
        assert not legacy.uci('get', 'firewall.vpn', check=False)
        assert not legacy.uci('get', 'firewall.openvpn', check=False)
        assert legacy.uci('get', 'openvpn.myvpn.enabled') == '0'
        assert legacy.uci('get', 'openvpn.user_vpn.enabled') == '1'
        legacy.run('base')
        assert legacy.luci_migration_counts() == [0, 0, 0]
        passed.append('LuCI migration predicates clear; loopback/legacy bridges migrated; vpn0 and default VPN references removed')

        upgrade = Fixture(parent, 'existing_profile')
        upgrade.uci('set', 'ap8220_ap.core.base_configured=1')
        upgrade.uci('commit', 'ap8220_ap')
        upgrade.run('base')
        assert upgrade.luci_migration_counts() == [0, 0, 0]
        assert not upgrade.uci('get', 'network.vpn0', check=False)
        assert upgrade.uci('get', 'network.lan.proto') == 'static'
        assert upgrade.uci('get', 'network.lan.ipaddr') == '192.168.1.1'
        passed.append('existing initialized profiles receive network migration without resetting their management settings')

        retained = Fixture(parent, 'retained_v051')
        retained.wifi()
        for option in ['base_configured', 'wifi_configured', 'initialized']:
            retained.uci('set', 'ap8220_ap.core.' + option + '=1')
        retained.uci('commit', 'ap8220_ap')
        retained.uci('set', 'luci.themes.Aurora=/luci-static/aurora')
        retained.uci('set', 'luci.main.mediaurlbase=/luci-static/aurora')
        retained.uci('commit', 'luci')
        retained.uci('set', 'wireless.ath0.ssid=Keep-My-SSID')
        retained.uci('set', 'wireless.ath0.key=Keep-My-Password')
        retained.uci('set', 'wireless.ath0.encryption=psk2+ccmp')
        retained.uci('commit', 'wireless')
        init_script = retained.root / 'etc/init.d/ap8220-ap-init'
        init_script.write_text(retained.translate((project / 'files/etc/init.d/ap8220-ap-init').read_text()))
        launch = f'''procd_open_instance() {{ :; }}
procd_close_instance() {{ :; }}
procd_set_param() {{ printf '%s\\n' "$*"; }}
. {init_script}
start_service
'''
        worker = subprocess.run([*ash, '-c', launch], env=retained.env, text=True, capture_output=True)
        assert worker.returncode == 0 and 'wait-wifi' in worker.stdout, worker.stderr
        original_shadow = (retained.root / 'etc/shadow').read_bytes()
        retained.run('base')
        retained.run('wait-wifi')
        assert retained.uci('get', 'network.lan_v6.proto') == 'dhcpv6'
        assert retained.uci('get', 'network.lan_v6.device') == 'br-lan'
        assert retained.uci('get', 'luci.main.mediaurlbase') == '/luci-static/footstrap'
        assert not retained.uci('get', 'luci.themes.Aurora', check=False)
        assert retained.uci('get', 'network.lan.ipaddr') == '192.168.1.1'
        assert retained.uci('get', 'wireless.ath0.ssid') == 'Keep-My-SSID'
        assert retained.uci('get', 'wireless.ath0.key') == 'Keep-My-Password'
        assert retained.uci('get', 'wireless.ath0.encryption') == 'psk-mixed'
        assert (retained.root / 'etc/shadow').read_bytes() == original_shadow
        retained.uci('set', 'network.lan_v6.reqaddress=force')
        retained.uci('commit', 'network')
        retained.run('base')
        assert retained.uci('get', 'network.lan_v6.reqaddress') == 'force'
        worker = subprocess.run([*ash, '-c', launch], env=retained.env, text=True, capture_output=True)
        assert worker.returncode == 0 and not worker.stdout
        passed.append('retained initialized profile migrates removed Aurora, lan_v6 and security once while preserving SSID, key, management IPv4 and root password')

        separate = Fixture(parent, 'separate_wifi')
        values = {'wifi_mode': 'separate', 'ssid_2g': "中文 2G ' $()", 'ssid_5g': 'Home-5G',
                  'key_2g': "2G'\\$() key", 'key_5g': '5G-separate-key', 'channel_2g': '11', 'channel_5g': '44'}
        for name, value in values.items():
            separate.uci('set', f'ap8220_ap.profile.{name}={value}')
        separate.uci('commit', 'ap8220_ap')
        separate.run('base')
        separate.wifi()
        separate.run('wifi')
        for vif, band in [('ath0', '5g'), ('ath1', '2g')]:
            assert separate.uci('get', f'wireless.{vif}.ssid') == values['ssid_' + band]
            assert separate.uci('get', f'wireless.{vif}.key') == values['key_' + band]
        assert separate.uci('get', 'wireless.wifi0.channel') == '44'
        assert separate.uci('get', 'wireless.wifi1.channel') == '11'
        passed.append('separate Wi-Fi names/keys and per-band channels map by actual QCA band, including quoted UTF-8 settings')

        custom = Fixture(parent, 'custom_root')
        password = "Synthetic root ' $() 密码"
        hashed = configure_profile.password_hash(password)
        hash_path = custom.root / 'etc/ap8220-root-password.hash'
        hash_path.write_text(hashed + '\n')
        hash_path.chmod(0o600)
        shadow = custom.root / 'etc/shadow'
        shadow.chmod(0o600)
        before = shadow.read_text()
        custom.run('base')
        assert shadow.read_text() == before.replace('root:!:', 'root:' + hashed + ':', 1)
        assert shadow.stat().st_mode & 0o777 == 0o600
        assert not hash_path.exists()
        assert custom.uci('get', 'ap8220_ap.core.root_password_applied') == '1'
        # Actual target libc crypt(), rather than OpenSSL (which supports more
        # hash formats than this QWRT's reduced authentication implementation).
        command = [str(qemu), '-L', str(rootfs), str(args.crypt_probe.resolve())]
        crypto = subprocess.run(command, input=password + '\n' + hashed + '\n',
                                text=True, capture_output=True, timeout=5)
        assert crypto.returncode == 0, 'Firmware authentication library rejects the configured password format'
        wrong = subprocess.run(command, input=password + '-wrong\n' + hashed + '\n',
                               text=True, capture_output=True, timeout=5)
        assert wrong.returncode != 0, 'Firmware authentication accepted an incorrect password'
        shadow.write_text('root:USER_CHANGED:0:0:99999:7:::\nnobody:*:0:0:99999:7:::\n')
        hash_path.write_text(hashed + '\n')
        custom.run('base')
        assert 'root:USER_CHANGED:' in shadow.read_text()
        passed.append('custom root hash applied once; original account fields/mode preserved; actual firmware crypt() accepts correct/rejects wrong password; later password changes retained')

        bad = Fixture(parent, 'bad_root_hash')
        (bad.root / 'etc/ap8220-root-password.hash').write_text('not-a-password-hash\n')
        original = (bad.root / 'etc/shadow').read_bytes()
        bad.run('base', success=False)
        assert (bad.root / 'etc/shadow').read_bytes() == original
        passed.append('malformed optional root hash is rejected without changing the shadow database')

        selected = Fixture(parent, 'selected_build_settings', selected_profile=True)
        selected_hash = project / 'files/etc/ap8220-root-password.hash'
        if selected_hash.exists():
            shutil.copy2(selected_hash, selected.root / 'etc/ap8220-root-password.hash')
        selected.run('base')
        selected.wifi()
        selected.run('wifi')
        mode = selected.uci('get', 'ap8220_ap.profile.wifi_mode', check=False) or 'shared'
        ssid2 = selected.uci('get', 'ap8220_ap.profile.ssid_2g', check=False) or selected.uci('get', 'ap8220_ap.profile.ssid')
        key2 = selected.uci('get', 'ap8220_ap.profile.key_2g', check=False) or selected.uci('get', 'ap8220_ap.profile.key')
        ssid5 = (selected.uci('get', 'ap8220_ap.profile.ssid_5g', check=False) or 'Home-WiFi-5G') if mode == 'separate' else ssid2
        key5 = (selected.uci('get', 'ap8220_ap.profile.key_5g', check=False) or key2) if mode == 'separate' else key2
        for vif, ssid, key in [('ath0', ssid5, key5), ('ath1', ssid2, key2)]:
            assert selected.uci('get', f'wireless.{vif}.ssid') == ssid
            assert selected.uci('get', f'wireless.{vif}.key') == key
        assert selected.uci('get', 'wireless.wifi0.channel') == selected.uci('get', 'ap8220_ap.profile.channel_5g')
        assert selected.uci('get', 'wireless.wifi1.channel') == selected.uci('get', 'ap8220_ap.profile.channel_2g')
        if selected_hash.exists():
            assert (selected.root / 'etc/shadow').read_text().split(':')[1] == selected_hash.read_text().strip()
        passed.append('this build actual selected Wi-Fi profile and optional root hash applied successfully in isolated fixtures')

    print(f'{len(passed)} integration checks passed')


if __name__ == '__main__':
    main()
