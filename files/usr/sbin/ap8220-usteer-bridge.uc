#!/usr/bin/ucode
// SPDX-License-Identifier: ISC
// QSDK hostapd control sockets -> the ubus methods consumed by usteer.
// No probe/auth veto, forced disconnect, invented RSSI or load measurements.
import * as fs from 'fs';
import * as sock from 'socket';
import * as loop from 'uloop';
import * as ubus from 'ubus';
import * as q from 'ap8220.qca';

let runtime = ARGV[0] ?? '/var/run/ap8220-usteer';
let pattern = ARGV[1] ?? '/var/run/hostapd-*/*';
let bus_path = ARGV[2];
let nodes = {}, sequence = 0;
fs.mkdir(runtime, 0700);
loop.init();
let bus = ubus.connect(bus_path);
if (!bus) die('ap8220-usteer: cannot connect to ubus\n');

function control(n, command) {
	// A fresh socket prevents a timed-out reply being mistaken for the next one.
	let path = runtime + '/q' + (++sequence % 10000);
	fs.unlink(path);
	let s = sock.create(sock.AF_UNIX, sock.SOCK_DGRAM | sock.SOCK_NONBLOCK);
	if (!s) return null;
	let out = null;
	if (s.bind({ family: sock.AF_UNIX, path }) &&
	    s.connect({ family: sock.AF_UNIX, path: n.path }) && s.send(command) != null) {
		let events = sock.poll(180, [s, sock.POLLIN]);
		if (length(events) && (events[0][1] & sock.POLLIN)) out = s.recv(65535);
	}
	s.close(); fs.unlink(path);
	return out == null ? null : trim(out);
}
function ok(n, cmd) { return control(n, cmd) == 'OK'; }
function integer(v, lo, hi) { return type(v) == 'int' && v >= lo && v <= hi; }
function reply(req, result, rc) { req.reply(result ?? {}, rc ?? 0); }
function checkpoint(n) {
	let path = runtime + '/managed-' + n.iface + '.json';
	if (fs.writefile(path + '.new', sprintf('%J', n.managed)) != null) {
		fs.chmod(path + '.new', 0600); fs.rename(path + '.new', path);
	}
}

function set_neighbors(n, req) {
	let list = req.args.list;
	if (type(list) != 'array' || length(list) > 32) return reply(req, null, 2);
	let desired = {};
	for (let entry in list) {
		if (type(entry) != 'array' || length(entry) != 3 || !q.mac(entry[0]) ||
		    type(entry[1]) != 'string' || length(entry[1]) < 1 || length(entry[1]) > 32 ||
		    !q.hexstr(entry[2], 13, 255) ||
		    lc(replace(entry[0], /:/g, '')) != lc(substr(entry[2], 0, 12)) ||
		    entry[1] != n.ssid || lc(entry[0]) == n.bssid)
			return reply(req, null, 2);
		desired[lc(entry[0])] = [lc(entry[0]), entry[1], lc(entry[2])];
	}
	// Only remove entries this adapter previously installed; keep the own report.
	for (let addr, old in n.managed) {
		if (desired[addr]) continue;
		if (!ok(n, 'REMOVE_NEIGHBOR ' + addr + ' ssid=' + q.tohex(old[1])))
			return reply(req, null, 9);
		delete n.managed[addr];
		checkpoint(n);
	}
	for (let addr, entry in desired) {
		if (n.managed[addr]?.[2] == entry[2]) continue;
		if (!ok(n, 'SET_NEIGHBOR ' + addr + ' ssid=' + q.tohex(entry[1]) + ' nr=' + entry[2]))
			return reply(req, null, 9);
		n.managed[addr] = entry;
		checkpoint(n);
	}
	reply(req);
}
function transition(n, req) {
	let a = req.args;
	if (!q.mac(a.addr) || !n.clients[lc(a.addr)]?.assoc ||
	    !integer(a.validity_period ?? 100, 1, 255) ||
	    !integer(a.dialog_token ?? 1, 1, 255) ||
	    type(a.neighbors) != 'array' || length(a.neighbors) > 32)
		return reply(req, null, 2);
	// Advisory requests only; do not silently downgrade a forced-disconnect call.
	if (a.disassociation_imminent || a.disassociation_timer) return reply(req, null, 8);
	let cmd = sprintf('BSS_TM_REQ %s dialog_token=%d pref=%d abridged=%d disassoc_imminent=0 valid_int=%d',
		lc(a.addr), a.dialog_token ?? 1, length(a.neighbors) ? 1 : 0,
		a.abridged ? 1 : 0, a.validity_period ?? 100);
	for (let nr in a.neighbors) {
		let part = q.btm_neighbor(nr);
		if (!part || !n.managed[lc(join(':', map(q.bytes(substr(nr,0,12)), b => sprintf('%02x', b))))])
			return reply(req, null, 2);
		cmd += ' ' + part;
	}
	if (length(cmd) > 4000) return reply(req, null, 2);
	reply(req, null, ok(n, cmd) ? 0 : 9);
}
function publish_node(n) {
	let methods = {
		get_status: { call: req => reply(req, { freq: n.freq, channel: n.channel,
			op_class: n.op_class, beacon_interval: n.beacon_interval, bssid: n.bssid,
			ssid: n.ssid, qca_bridge: true }) },
		get_clients: { call: req => reply(req, { freq: n.freq, clients: n.clients }) },
		rrm_nr_get_own: { call: req => reply(req, { value: n.own }) },
		rrm_nr_set: { args: { list: [] }, call: req => set_neighbors(n, req) },
		notify_response: { args: { notify_response: 0 }, call: req => reply(req, { synchronous_veto: false }) },
		// Abilities are enabled by the ROM Wi-Fi profile; this does not change them.
		bss_mgmt_enable: { args: { neighbor_report: true, beacon_report: true,
			link_measurement: true, bss_transition: true }, call: req => reply(req, { configured_by: 'wireless_profile' }) },
		bss_transition_request: { args: { addr: '', dialog_token: 0,
			disassociation_imminent: true, disassociation_timer: 0,
			abridged: true, validity_period: 0, neighbors: [], mbo_reason: 0, reassoc_delay: 0 },
			call: req => transition(n, req) },
		// Explicitly unsupported, rather than reporting successful no-ops.
		del_client: { args: { addr: '', deauth: true, reason: 0, ban_time: 0 }, call: req => reply(req, null, 8) },
		rrm_beacon_req: { args: { addr: '', op_class: 0, channel: 0, duration: 0, mode: 0, ssid: '' }, call: req => reply(req, null, 8) },
		link_measurement_req: { args: { addr: '' }, call: req => reply(req, null, 8) }
	};
	n.object = bus.publish('hostapd.' + n.iface, methods);
	if (!n.object) warn('ap8220-usteer: failed to publish ' + n.iface + '\n');
	return n.object != null;
}
function event(n, text) {
	text = replace(text, /^<[0-9]+>/, '');
	let m = match(text, /^BSS-TM-RESP ([0-9a-f:]{17}).*status_code=([0-9]+)/i);
	if (m && q.mac(m[1])) n.object?.notify('bss-transition-response', { address: lc(m[1]), 'status-code': int(m[2]) });
	m = match(text, /^BSS-TM-QUERY ([0-9a-f:]{17}).*dialog_token=([0-9]+)/i);
	if (m && q.mac(m[1])) n.object?.notify('bss-transition-query', { address: lc(m[1]), 'dialog-token': int(m[2]) });
	// Connection state comes from the next complete STA enumeration. There is no
	// synchronous probe/auth interception in the unmodified QCA hostapd binary.
}
function attach(n) {
	let path = runtime + '/ev-' + n.iface;
	fs.unlink(path);
	let s = sock.create(sock.AF_UNIX, sock.SOCK_DGRAM | sock.SOCK_NONBLOCK);
	if (!s) return;
	if (!s.bind({ family: sock.AF_UNIX, path }) ||
	    !s.connect({ family: sock.AF_UNIX, path: n.path }) || s.send('ATTACH') == null) {
		s.close(); fs.unlink(path); return;
	}
	let events = sock.poll(180, [s, sock.POLLIN]);
	if (!length(events) || trim(s.recv(65535) ?? '') != 'OK') { s.close(); fs.unlink(path); return; }
	n.event_socket = s; n.event_path = path;
	n.handle = loop.handle(s, () => {
		let text = s.recv(65535, sock.MSG_DONTWAIT);
		if (text != null) event(n, text);
	}, loop.ULOOP_READ);
}
function withdraw(n) {
	n.object?.remove(); n.handle?.delete(); n.event_socket?.close();
	if (n.event_path) fs.unlink(n.event_path);
	n.object = null; n.handle = null; n.event_socket = null;
}
function update(n) {
	let raw = control(n, 'STATUS'), d = q.kv(raw);
	let bssid = lc(d['bssid[0]'] ?? d.bssid ?? ''), freq = int(d.freq), channel = int(d.channel);
	if (d.state != 'ENABLED' || !q.mac(bssid) || freq < 2412 || freq > 5900 || channel < 1) return false;
	let reports = q.neighbors(control(n, 'SHOW_NEIGHBOR'));
	let own = reports[bssid];
	if (!own) return false; // Require the authentic hostapd-generated own report.
	if (n.object && (n.bssid != bssid || n.ssid != own[1] || n.freq != freq)) {
		withdraw(n); n.managed = {};
	}
	n.bssid = bssid; n.ssid = own[1]; n.freq = freq; n.channel = channel; n.own = own;
	// Reconcile only previously managed entries that still exist in this BSS.
	if (!n.restored) {
		let saved = null;
		try { saved = json(fs.readfile(runtime + '/managed-' + n.iface + '.json') ?? '{}'); } catch (e) {}
		if (type(saved) == 'object') for (let addr, entry in saved) {
			if (addr != bssid && reports[addr]?.[1] == n.ssid && reports[addr]?.[2] == entry?.[2])
				n.managed[addr] = reports[addr];
		}
		n.restored = true;
	}
	for (let addr, entry in n.managed) if (reports[addr]?.[2] != entry[2]) delete n.managed[addr];
	n.op_class = q.bytes(own[2])[10];
	n.beacon_interval = int(d.beacon_int) || 100;
	let clients = {}, seen = {}, text = control(n, 'STA-FIRST');
	for (let i = 0; i < 256; i++) {
		if (text == null) return false;
		if (text == 'FAIL' || text == '') break;
		let sta = q.station(text);
		if (!sta || seen[sta[0]]) return false;
		seen[sta[0]] = true;
		if (sta[1].assoc) clients[sta[0]] = sta[1];
		text = control(n, 'STA-NEXT ' + sta[0]);
		if (i == 255 && text != 'FAIL' && text != '') return false;
	}
	n.clients = clients;
	if (!n.object && !publish_node(n)) return false;
	if (!n.event_socket) attach(n);
	n.updated = time();
	return true;
}
function scan() {
	let found = {};
	for (let path in fs.glob(pattern) ?? []) {
		let iface = split(path, '/')[-1];
		if (!match(iface, /^ath[0-9]+([.-][0-9]+)?$/)) continue;
		let info = fs.stat(path);
		if (info?.type != 'socket') continue;
		if (found[iface]) continue;
		found[iface] = true;
		let n = nodes[iface];
		if (n && (n.path != path || n.inode != info.inode)) {
			withdraw(n); delete nodes[iface]; n = null;
		}
		if (!n) n = nodes[iface] = { path, iface, inode: info.inode, managed: {}, clients: {} };
		if (!update(n)) { withdraw(n); delete nodes[iface]; }
	}
	for (let iface, n in nodes) if (!found[iface]) { withdraw(n); delete nodes[iface]; }
}
let diagnostic = bus.publish('ap8220.usteer', { status: { call: req => {
	let result = {};
	for (let iface, n in nodes) result[iface] = { published: n.object != null,
		bssid: n.bssid, ssid: n.ssid, freq: n.freq, updated: n.updated,
		clients: length(keys(n.clients)), managed_neighbors: length(keys(n.managed)),
		events_attached: n.event_socket != null };
	reply(req, { experimental: true, backend: 'qca-control', nodes: result,
		forced_disconnect: false, probe_auth_veto: false, beacon_requests: false });
} } });
if (!diagnostic) die('ap8220-usteer: duplicate bridge instance or ubus failure\n');
let timer;
timer = loop.timer(1, () => { scan(); timer.set(2000); });
loop.run();
