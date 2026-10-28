// SPDX-License-Identifier: ISC
import { cursor } from 'uci';
import { connect } from 'ubus';
let uci = cursor(), bus = connect();
if (!bus) die('usteer: ubus unavailable\n');
let config = {}, interfaces = [];
uci.foreach('usteer', 'usteer', s => {
	for (let name in ['syslog', 'ipv6', 'local_mode', 'load_kick_enabled', 'assoc_steering', 'probe_steering'])
		if (s[name] != null) config[name] = s[name] == '1';
	for (let name in ['debug_level', 'local_sta_update', 'remote_update_interval', 'remote_node_timeout',
		'max_neighbor_reports', 'initial_connect_delay', 'min_connect_snr', 'min_snr',
		'aggressiveness', 'roam_scan_snr', 'roam_trigger_snr', 'band_steering_interval',
		'band_steering_min_snr', 'steer_reject_timeout', 'link_measurement_interval'])
		if (s[name] != null && match(s[name], /^-?[0-9]+$/)) config[name] = int(s[name]);
	if (type(s.ssid_list) == 'array') config.ssid_list = s.ssid_list;
	for (let network in split(s.network ?? 'lan', /\s+/)) {
		let status = bus.call('network.interface.' + network, 'status', {});
		let device = status?.l3_device ?? uci.get('network', network, 'device');
		if (device && index(interfaces, device) < 0) push(interfaces, device);
	}
});
config.interfaces = interfaces;
if (bus.call('usteer', 'set_config', config) == null) die('usteer: configuration failed\n');
