import { state } from 'state';
import * as q from 'ap8220.qca';
export const AF_UNIX = 1, SOCK_DGRAM = 2, SOCK_NONBLOCK = 2048, POLLIN = 1, MSG_DONTWAIT = 64;
export function create() {
	let s = { pending: [], closed: false };
	s.bind = address => { s.local = address.path; return true; };
	s.connect = address => { s.peer = state.radios[address.path]; return s.peer != null; };
	s.send = cmd => {
		let r = s.peer, out = 'FAIL';
		if (!r) return null;
		push(state.commands, [r.iface, cmd]);
		if (r.timeout) return length(cmd);
		if (cmd == 'STATUS') out = sprintf('state=%s\nfreq=%d\nchannel=%d\nbeacon_int=100\nbssid[0]=%s', r.enabled ? 'ENABLED' : 'DISABLED', r.freq, r.channel, r.bssid);
		else if (cmd == 'SHOW_NEIGHBOR') {
			out = '';
			for (let addr, entry in r.entries) if (!r.missing_own || addr != r.bssid)
				out += addr + ' ssid=' + q.tohex(entry[0]) + ' nr=' + entry[1] + '\n';
		}
		else if (cmd == 'STA-FIRST') out = '02:10:20:30:40:50\nflags=[AUTH][ASSOC][AUTHORIZED]\nsignal=' + r.signal + '\next_capab=000008\n';
		else if (index(cmd, 'SET_NEIGHBOR ') == 0 && !r.fail_set) {
			let parts = split(cmd, ' ');
			r.entries[parts[1]] = [q.unhex(substr(parts[2], 5)), substr(parts[3], 3)]; out = 'OK';
		}
		else if (index(cmd, 'REMOVE_NEIGHBOR ') == 0) {
			let addr = split(cmd, ' ')[1];
			if (addr != r.bssid && r.entries[addr]) { delete r.entries[addr]; out = 'OK'; }
		}
		else if (cmd == 'ATTACH') { push(r.events, s); out = 'OK'; }
		else if (index(cmd, 'BSS_TM_REQ ') == 0) out = 'OK';
		push(s.pending, out + '\n'); return length(cmd);
	};
	s.recv = () => shift(s.pending) ?? null;
	s.close = () => { s.closed = true; return true; };
	return s;
};
export const poll = (ms, pair) => length(pair[0].pending) ? [[pair[0], POLLIN]] : [];
