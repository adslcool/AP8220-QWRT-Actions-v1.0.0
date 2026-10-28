// SPDX-License-Identifier: ISC
// Real target LuCI runtime/templates; isolated HTTP, board and UCI fixtures.
import Runtime from 'luci.runtime';
import { cursor } from 'uci';
import { entityencode, striptags } from 'html';

const theme = getenv('AP8220_TEST_THEME');
const view = getenv('AP8220_TEST_VIEW');
const uci = cursor(getenv('AP8220_TEST_CONFIG'), getenv('AP8220_TEST_SAVE'));
const login = index(view, 'login') == 0;
const request_path = view == 'network' ? [ 'admin', 'network', 'network' ] : [ 'admin', 'status', 'overview' ];
function _(message) { return message; }
const env = {
    _,
    striptags,
    entityencode,
    uci,
    ubus: { call: (object, method) => {
        if (object != 'system' || method != 'board')
            die('Unexpected ubus request in theme render fixture');
        return { hostname: 'AP8220-THEME-TEST', rootfs_type: 'squashfs' };
    } },
    config: { main: uci.get_all('luci', 'main') ?? {}, apply: {} },
    ctx: { path: login ? [] : request_path, request_path,
           authsession: login ? null : '00000000000000000000000000000001',
           authtoken: '00000000000000000000000000000002', authuser: login ? null : 'root' },
    dispatched: { title: view == 'network' ? 'Interfaces' : 'Overview' },
    version: { luciname: 'LuCI QWRT', luciversion: 'fixture', distname: 'QWRT',
               distversion: 'R26.10.11', distrevision: 'fixture', disturl: '#' },
    dispatcher: {
        lang: 'zh-cn', build_url: (...path) => '/cgi-bin/luci/' + join('/', path),
        lookup: (path) => ({ title: path }), rollback_pending: () => null,
        load_luabridge: () => null, error500: (message) => die(message)
    },
    http: { closed: false, prepare_content: () => {}, write: (...args) => print(...args),
            getenv: (name) => ({ SCRIPT_NAME: '/cgi-bin/luci', PATH_INFO: '/admin/status/overview',
                                DOCUMENT_ROOT: '/www' })[name] },
    lua_active: false, rollback_token: null, trigger_apply: false, trigger_revert: false
};

const runtime = Runtime(env);
if (runtime.env.theme != theme || runtime.env.media_error)
    die('LuCI fell back instead of selecting requested theme');
if (login)
    runtime.render(`themes/${theme}/sysauth`, { duser: 'root', fuser: view == 'login-error' ? 'root' : null });
else {
    runtime.render('header');
    print('<div id="view">AP8220 theme render fixture</div>');
    runtime.render('footer');
}
