// SPDX-License-Identifier: ISC
// hostapd control protocol codec. No shell commands or fabricated radio data.
function mac(s) {
	return type(s) == 'string' && match(s, /^[0-9a-f]{2}(:[0-9a-f]{2}){5}$/i) &&
	       !(int(substr(s, 0, 2), 16) & 1) && s != '00:00:00:00:00:00';
}
function hexstr(s, minbytes, maxbytes) {
	return type(s) == 'string' && length(s) % 2 == 0 &&
	       length(s) >= minbytes * 2 && length(s) <= maxbytes * 2 &&
	       (length(s) == 0 || match(s, /^[0-9a-f]+$/i));
}
function bytes(s) {
	let a = [];
	if (hexstr(s, 0, 255))
		for (let i = 0; i < length(s); i += 2) push(a, int(substr(s, i, 2), 16));
	return a;
}
function unhex(s) {
	return join('', map(bytes(s), b => chr(b)));
}
function tohex(s) {
	let h = '';
	for (let i = 0; i < length(s); i++) h += sprintf('%02x', ord(s, i));
	return h;
}
function kv(text) {
	let o = {};
	for (let line in split(text ?? '', '\n')) {
		let p = index(line, '=');
		if (p > 0) o[substr(line, 0, p)] = substr(line, p + 1);
	}
	return o;
}
function neighbors(text) {
	let o = {};
	for (let line in split(text ?? '', '\n')) {
		let m = match(line, /^([0-9a-f:]{17}) ssid=([0-9a-f]*) nr=([0-9a-f]+)( |$)/i);
		if (!m || !mac(m[1]) || !hexstr(m[2], 1, 32) || !hexstr(m[3], 13, 255)) continue;
		if (lc(replace(m[1], /:/g, '')) != lc(substr(m[3], 0, 12))) continue;
		o[lc(m[1])] = [lc(m[1]), unhex(m[2]), lc(m[3])];
	}
	return o;
}
function station(text) {
	let addr = lc(split(text ?? '', '\n')[0]);
	if (!mac(addr)) return null;
	let d = kv(text), flags = d.flags ?? '';
	let s = { assoc: index(flags, '[ASSOC]') >= 0,
		auth: index(flags, '[AUTH]') >= 0, authorized: index(flags, '[AUTHORIZED]') >= 0,
		rrm: bytes(d.rrm_enabled_capa ?? d.rrm_capab ?? '0000000000'),
		extended_capabilities: bytes(d.ext_capab ?? '') };
	let signal = int(d.signal);
	if (signal >= -127 && signal < 0) s.signal = signal;
	let connected = int(d.connected_time);
	if (d.connected_time != null && connected >= 0) s.connected_time = connected;
	return [addr, s];
}
function btm_neighbor(nr) {
	if (!hexstr(nr, 13, 255)) return null;
	let b = bytes(nr), addr = [];
	for (let i = 0; i < 6; i++) push(addr, sprintf('%02x', b[i]));
	let info = b[6] + b[7]*256 + b[8]*65536 + b[9]*16777216;
	let out = sprintf('neighbor=%s,%u,%u,%u,%u', join(':', addr), info, b[10], b[11], b[12]);
	if (length(nr) > 26) out += ',' + substr(nr, 26);
	return out;
}

export { mac, hexstr, bytes, unhex, tohex, kv, neighbors, station, btm_neighbor };
