// Test-only in-memory modules. No socket syscalls are used by these fixtures.
export const state = { objects: {}, radios: {}, files: {}, timers: [], handles: [], notifications: [], commands: [] };
export function radio(iface, bssid, channel, freq, ssid) {
	let nr = replace(bssid, /:/g, '') + 'ef590000' + sprintf('%02x%02x09', freq > 5000 ? 128 : 81, channel);
	let path = '/var/run/hostapd-' + iface + '/' + iface;
	let r = { path, iface, bssid, channel, freq, ssid: ssid ?? 'Home-WiFi', nr,
		enabled: true, signal: '-54', ino: length(keys(state.radios)) + 1, entries: {}, events: [] };
	r.entries[bssid] = [r.ssid, nr];
	state.radios[path] = r;
	return r;
};
