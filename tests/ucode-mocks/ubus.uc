import { state } from 'state';
export function connect() {
	return { publish: (name, methods) => {
		if (state.objects[name]) return null;
		let object = { methods, name };
		object.remove = () => { delete state.objects[name]; return true; };
		object.notify = (method, data) => { push(state.notifications, { name, method, data }); return {}; };
		state.objects[name] = object;
		return object;
	} };
};
