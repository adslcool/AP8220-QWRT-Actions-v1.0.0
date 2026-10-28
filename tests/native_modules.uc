// Use the actual target extensions. No network or Unix sockets are opened.
import * as loop from 'uloop';
import * as socket from 'socket';
import * as ubus from 'ubus';
import { stat } from 'fs';
if (type(socket.AF_UNIX) != 'int' || type(ubus.connect) != 'function' || !stat('/tmp'))
	die('native extension exports are incompatible\n');
loop.init();
let fired = false;
let timer = loop.timer(1, () => { fired = true; loop.end(); });
loop.run();
if (!fired) die('target uloop timer failed\n');
print('PASS: original QWRT ucode + pinned uloop binary: native module loading and actual epoll timer\n');
