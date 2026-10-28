import { state } from 'state';
export const mkdir = () => true;
export const glob = () => keys(state.radios);
export const stat = path => state.radios[path] ? { type: 'socket', inode: state.radios[path].ino } : null;
export const unlink = path => { delete state.files[path]; return true; };
export const readfile = path => state.files[path] ?? null;
export const writefile = (path, content) => { state.files[path] = content; return length(content); };
export const chmod = () => true;
export const rename = (a, b) => { state.files[b] = state.files[a]; delete state.files[a]; return true; };
