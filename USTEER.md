# AP8220 / QSDK usteer 实验适配

v0.3.0 加入独立的 QCA hostapd 控制接口适配层，以及固定上游版本的 usteer。原有 qca-hostap、无线驱动、NSS 和内核保持原样。此版本发布为 **Pre-release**，需要 GitHub Actions 联调和实机验证。

## 实现方式

`ap8220-usteer-bridge.uc` 使用原固件的 ucode，通过 `/var/run/hostapd-*` 下的 UNIX datagram 控制套接字访问 QCA hostapd，并提供 `hostapd.ath*` ubus 对象。每两秒重新检查套接字、无线状态和客户端；无线停止、控制接口超时或缺少真实自身邻居报告时撤销对应对象。重建套接字后重新发现，避免保留失效接口。

usteer 上游固定提交为 `7532e1252fb937fe34b6da05e86fd34babd91ef5`。本版编译 QCA 控制后端，使用 ubus 返回的 SSID/BSSID 和 hostapd STA 输出的真实 RSSI，不编译依赖 mac80211 数据的 nl80211 后端。原有系统库无需升级。未知 RSSI 不填入估计值；信道负载/噪声数据目前不可用，相关策略保持关闭。

| 功能 | 本版范围 |
|---|---|
| `get_status` / `get_clients` | 读取真实 STATUS、STA-FIRST / STA-NEXT 输出；RSSI 仅在有效时提供 |
| 自身邻居报告 | 读取 SHOW_NEIGHBOR 中与本 BSSID 对应的原始报告；不伪造 |
| 邻居表同步 | usteer 下发同 SSID 邻居，通过 SET_NEIGHBOR / REMOVE_NEIGHBOR 更新；保留自身报告和非本适配层管理的项 |
| 建议式 BTM | `bss_transition_request` 转成 BSS_TM_REQ；转发实际 BSS-TM-RESP 接受/拒绝事件 |
| 自动主动引导 | 默认关闭；待实机确认 RSSI、终端能力和 BTM 效果后，再调整 band steering 参数 |
| probe/auth/assoc 同步拒绝 | 不支持。原 QCA hostapd 无相应 ubus 回调，配置为关闭 |
| 强制断开 / 踢客户端 | 不支持；请求返回“不支持”，不会假报成功 |
| beacon/link measurement 主动请求 | 本版不实现；相关策略关闭 |
| 802.11r | 延续原来的 QCA FT 配置，与此适配层独立 |

跨 AP 发现与信息交换沿用 usteer 的 LAN IPv4 UDP 16720 机制。多台 AP 需处于可互通的同一局域网，并使用相同 SSID、密码和漫游域。它不是无线 Mesh 回程或 AP 控制器。隔离的 Guest SSID 不进入 Home-WiFi 的邻居列表。

## 默认配置和服务

`files/etc/config/usteer` 默认启用 LAN 节点交换、客户端跟踪和邻居表同步。`aggressiveness=1`，强制断开、低信号踢人、主动扫描、自动 band steering 和负载踢人均关闭。

桥接服务 START=49，usteer 服务 START=50；AP 无线初始化稍后完成也可自动发现。修改 `/etc/config/usteer` 后执行：

```sh
/etc/init.d/ap8220-usteer-bridge restart
/etc/init.d/usteer restart
```

停用本功能：

```sh
uci set usteer.main.enabled='0'
uci commit usteer
/etc/init.d/usteer stop
/etc/init.d/ap8220-usteer-bridge stop
```

仅暂停服务不会改变 Wi-Fi SSID、密码或 FT 配置。适配层管理的邻居记录在 `/var/run/ap8220-usteer` 保存临时清单，以便服务重启后继续清理过期邻居。

## 真机验收

```sh
ubus call ap8220.usteer status
ubus list 'hostapd.*'
ubus call hostapd.ath0 get_status
ubus call hostapd.ath0 get_clients
ubus call hostapd.ath0 rrm_nr_get_own
ubus call usteer local_info
ubus call usteer connected_clients
ubus call usteer remote_hosts
ubus call usteer remote_info
logread -e usteer
```

接口名以实际 `ubus list` 结果为准。两台 AP 上应看到正确的 BSSID/SSID、有效客户端信号和远端节点。在各自实际控制目录执行 `hostapd_cli -p /var/run/hostapd-wifi0 -i ath0 show_neighbor`，确认出现另一台 AP 同 SSID 的邻居；不要只凭进程存在判断成功。

可通过本机 ubus 提交建议式 BTM 验证，但 `addr` 必须是当前已关联客户端，`neighbors` 必须是已同步邻居的原始 NR 十六进制内容。手机是否接受请求由终端决定；需观察 BSS-TM-RESP、实际 BSSID 和丢包。重载无线后还要确认旧对象消失、新对象和邻居表恢复。

若诊断中没有节点，先检查 hostapd 是否 ENABLED、控制套接字路径以及 SHOW_NEIGHBOR 的自身报告。没有节点时桥接服务仍可能正常运行，这不代表无线已可用。

## 构建和验证

构建使用校验 SHA256 的 OpenWrt 24.10.6 aarch64 musl 工具链，随项目携带完整 usteer 上游源码归档和补丁，以及匹配原固件的 libubox/ubus 头文件源码。构建用的临时共享库只用于链接，**不写入固件**；生成的 usteer 运行时使用输入固件原有库。

`ucode-mod-uloop` 来自 OpenWrt 24.10.6 官方包，固定哈希。原固件的 ucode、libubox、libubus 版本均有构建前检查，其他版本会停止制作，避免默默混用 ABI。

验证分为三层：

1. 原固件解释器运行协议模拟测试，覆盖输入验证、控制命令、状态恢复、失败传播；实际加载目标扩展并运行 epoll 定时器。
2. `tests/test_usteer.py` 使用真实 ARM ubusd、ucode 和修改后的 usteer，与模拟 hostapd 套接字联调，检查数据进入 usteer、邻居同步及事件订阅。原版 ubusd 的对象发布 ACL 要求 UID 0，因此非 root 构建时仅这项隔离测试通过 `sudo -n` 运行；Actions runner 支持此方式。GitHub Actions 必须通过后才能准备 Release。
3. AP8220 真机验证 QCA 驱动实际输出、多 AP 交换、BTM、无线重启及稳定性。软件测试不能替代这一步。

本地开发环境仍禁止创建 UNIX 套接字。用户提供的 `logs_108595434523.zip` 已确认 v0.5.0 在 Actions 通过了第 2 层的 7 组真实 ARM 进程联调及完整固件重建，账户文件保持检查通过；无线端为模拟 hostapd 控制套接字。随后发生的 Release 草稿查找错误在 v0.5.1 中改为直接使用创建响应的 Release ID。第 3 层的真实 QCA 无线和多 AP 漫游仍待实机验证。`--skip-ipc-test` 仅供本地检查，生成报告会标记未验证，发布程序会拒绝该输出。

## root 密码

v0.4.0 的 Actions 页面支持可选 root 密码。留空时沿用原固件，不增设密码覆盖；明确填写时仅保存原认证库支持的 MD5-crypt 哈希，最后的首次启动脚本应用一次，保留其他账户、密码老化字段和文件权限。构建直接在原 libc 下校验密码；不使用此基包认证库无法校验的 SHA256/SHA512-crypt 格式。普通重启保留用户之后的修改。

制作阶段不改原账户文件和上游密码初始化脚本，仍逐字节校验账户文件。配置测试覆盖空密码、锁定、自定义原哈希、显式设置密码、错误哈希拒绝及首次应用后的用户修改。保留旧配置时，当前密码和已有初始化标记继续生效；要应用新制作的默认 Wi-Fi/root 参数，应取消保留旧配置。
