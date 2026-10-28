import { state, radio } from 'state';
import * as q from 'ap8220.qca';
function verify(condition, message) { if (!condition) die('FAIL: ' + message + '\n'); }
function call(name, method, args) {
	let result;
	state.objects[name].methods[method].call({ args: args ?? {}, reply: (data, code) => { result = { data, code }; } });
	return result;
}
function scan() { state.timers[0].callback(); }
export function exercise() {
	let r0 = state.radios['/var/run/hostapd-ath0/ath0'];
	let r1 = state.radios['/var/run/hostapd-ath1/ath1'];
	let guest = state.radios['/var/run/hostapd-ath2/ath2'];
	verify(length(keys(state.objects)) == 4, 'three nodes + diagnostic endpoint');
	let identity = call('hostapd.ath0', 'get_status').data;
	verify(identity.ssid == r0.ssid && identity.bssid == r0.bssid && identity.op_class == 128, 'authentic radio metadata');
	let client = call('hostapd.ath0', 'get_clients').data.clients['02:10:20:30:40:50'];
	verify(client.assoc && client.signal == -54 && client.extended_capabilities[2] == 8, 'station fields');
	verify(call('hostapd.ath0','rrm_nr_get_own').data.value[2] == r0.nr, 'own NR');
	print('PASS: original QWRT ucode interpreter: real codec/daemon with in-memory IPC fixtures; identity/client/capability fields\n');

	let entry = [r1.bssid, r1.ssid, r1.nr];
	verify(call('hostapd.ath0', 'rrm_nr_set', { list: [entry] }).code == 0, 'NR set');
	verify(r0.entries[r1.bssid][1] == r1.nr, 'control command applied');
	verify(call('hostapd.ath0', 'rrm_nr_set', { list: [[guest.bssid, guest.ssid, guest.nr]] }).code == 2, 'guest SSID denied');
	verify(call('hostapd.ath0', 'rrm_nr_set', { list: [[r1.bssid, r1.ssid, r1.nr + ';reboot']] }).code == 2, 'injection denied');
	verify(call('hostapd.ath0', 'rrm_nr_set', { list: [[r0.bssid, r0.ssid, r0.nr]] }).code == 2, 'own NR never replaced');
	print('PASS: NR synchronization methods, SSID isolation, strict MAC/hex validation, own-report protection\n');

	let request = { addr: '02:10:20:30:40:50', dialog_token: 5, validity_period: 100,
		abridged: true, disassociation_imminent: false, neighbors: [r1.nr] };
	verify(call('hostapd.ath0', 'bss_transition_request', request).code == 0, 'advisory BTM');
	let command = state.commands[-1][1];
	verify(index(command, 'disassoc_imminent=0') >= 0 && index(command, 'neighbor=' + r1.bssid + ',23023,81,6,9') >= 0, 'BTM byte order');
	request.disassociation_imminent = true;
	verify(call('hostapd.ath0', 'bss_transition_request', request).code == 8, 'forced BTM denied');
	verify(call('hostapd.ath0', 'del_client', { addr: request.addr }).code == 8, 'disconnect unsupported');
	verify(call('hostapd.ath0', 'rrm_beacon_req', { addr: request.addr }).code == 8, 'scan unsupported');
	push(r0.events[0].pending, '<3>BSS-TM-RESP 02:10:20:30:40:50 status_code=7 bss_termination_delay=0');
	state.handles[0].callback();
	verify(state.notifications[-1].method == 'bss-transition-response' && state.notifications[-1].data['status-code'] == 7, 'event relay');
	print('PASS: advisory BTM construction and response events; unsupported force/scan operations return errors\n');

	// Simulate a daemon restart by withdrawing and re-discovering the BSS.
	r0.enabled = false; scan();
	verify(!state.objects['hostapd.ath0'], 'disabled radio withdrawn');
	r0.enabled = true; scan();
	verify(state.objects['hostapd.ath0'], 'radio restored');
	verify(call('ap8220.usteer','status').data.nodes.ath0.managed_neighbors == 1, 'managed checkpoint restored');
	verify(call('hostapd.ath0','rrm_nr_set',{ list: [] }).code == 0 && !r0.entries[r1.bssid], 'stale owned NR removed after restart');
	verify(r0.entries[r0.bssid], 'own NR kept');
	print('PASS: radio loss/recreation, volatile checkpoint reconciliation, stale owned NR removal\n');

	r0.signal = '0'; scan();
	verify(call('hostapd.ath0','get_clients').data.clients['02:10:20:30:40:50'].signal == null, 'unknown RSSI omitted');
	r0.fail_set = true;
	verify(call('hostapd.ath0','rrm_nr_set',{ list: [entry] }).code == 9, 'failed hostapd command propagated');
	verify(call('ap8220.usteer','status').data.nodes.ath0.managed_neighbors == 0, 'failed write not cached');
	r0.missing_own = true; scan();
	verify(!state.objects['hostapd.ath0'], 'missing authentic NR withdrawn');
	r0.missing_own = false; scan();
	r0.timeout = true; scan();
	verify(!state.objects['hostapd.ath0'], 'control timeout withdrawn');
	print('PASS: absent RSSI, control failure, missing own report and timeout do not produce fake-ready state\n');

	let special = 'Home WiFi-\u4e2d\u6587';
	verify(q.unhex(q.tohex(special)) == special, 'UTF-8 SSID roundtrip');
	verify(!q.mac('ff:ff:ff:ff:ff:ff') && !q.hexstr('aa;reboot', 1, 255), 'invalid address/data');
	verify(q.btm_neighbor('short') == null, 'short NR');
	print('PASS: UTF-8 SSIDs, malformed reports and BTM neighbor encoding\n');
};
