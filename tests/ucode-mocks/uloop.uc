import { state, radio } from 'state';
import { exercise } from 'suite';
radio('ath0', '48:51:c5:00:00:10', 149, 5745);
radio('ath1', '48:51:c5:00:00:11', 6, 2437);
radio('ath2', '48:51:c5:00:00:12', 1, 2412, 'Guest');
export const ULOOP_READ = 1;
export const init = () => true;
export const handle = (socket, callback) => {
	let h = { socket, callback, deleted: false };
	h.delete = () => { h.deleted = true; };
	push(state.handles, h); return h;
};
export const timer = (ms, callback) => {
	let t = { callback, set: () => true };
	push(state.timers, t); return t;
};
export const run = () => {
	state.timers[0].callback();
	exercise();
};
