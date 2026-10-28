# AP8220 QWRT AP v1.0.0

基于用户提供的 **QWRT R26.10.11 / QSDK 12.5 / Linux 5.4.213** 二次封装。

基准原固件的二次封装方法已由用户实机确认可行。换用其他 URL 基包时，仍需验证该新基包的启动、网口和无线表现；Actions 不执行硬件测试。

源码包包含 Footstrap、Argon 安装包、本次 AP/usteer 适配源码和重打包程序。Actions 的 Release 只发布 sysupgrade 固件与 SHA256SUMS，简介在正文中；没有完整包或报告附件。它不是 QWRT 的完整源码包。

## 默认配置

| 项目 | 设置 |
|---|---|
| 主机名 | `AP8220-稳定基准MAC的后6位`，大写、无冒号 |
| 管理 IPv4 地址 | 从上级路由 DHCP 获取；到上级路由租约列表查找 |
| 管理 IPv6 | `lan_v6`，`proto=dhcpv6`、`device=br-lan`、`reqaddress=try`、`reqprefix=no`、`delegate=0` |
| 有线端口 | `eth1` + `eth0` 一起加入 `br-lan` |
| WAN | 删除独立 `wan`、`wan6` 接口 |
| VPN | 清除默认 `vpn0 → tun0` 接口及关联规则，停用默认 myvpn 配置 |
| LuCI 网络配置 | 首次启动迁移 loopback、旧桥接和其他接口的旧式 ifname 写法 |
| DHCP/RA 服务 | 关闭本机 DHCP、DHCPv6、RA、NDP 代理；IPv6 广播仍可通过二层桥转发 |
| 路由服务 | 禁用 dnsmasq、odhcpd、firewall 及已安装的 UPnP 服务的开机启动 |
| SSID | 两个频段均为 `Home-WiFi` |
| Wi-Fi 密码 | `12345678` |
| Wi-Fi 加密 | `encryption=psk-mixed`，WPA/WPA2-PSK，算法自动 |
| root 密码 | 制作时留空沿用输入固件；明确填写则首次启动应用一次 |
| 国家码 | `CN` |
| 2.4 GHz | 信道 6，QSDK `HT20`；保留原来的 AX 无线模式 |
| 5 GHz | 信道 149，QSDK `HT80`；保留原来的 AX 无线模式 |
| 漫游域 | `a1b2` |
| 漫游参数 | 802.11r、FT over-the-air、FT-PSK 本地密钥生成；11k 邻居/信标报告；11v BSS Transition 能力 |
| Web | Footstrap 0.14.16 默认，保留 Argon 3.1.0；LuCI 简体中文；无 Aurora 或 Argon 配置中文包 |
| 网页测速 | `librespeed-go 1.1.5-r5`，默认启用，HTTP 端口 `8989`，`database_type=none` |
| 时区 | Asia/Shanghai |
| usteer | 实验性 QCA 控制接口适配；默认同步邻居，主动 band steering/强制断开关闭；见 USTEER.md |

两个网口均可作为 AP 上联口。若同时将两个网口接入同一上级网络，会形成二层环路；此配置没有将两个网口配置为链路聚合。

在系统的语言和界面设置中可切换 Footstrap、Argon。普通重启保留手动选择，恢复出厂默认 Footstrap。构建检查 Footstrap 的四种页面渲染、JavaScript 语法、LuCI 模块和本地资源；浏览器实际交互及设备效果仍需实机确认。

## 制作页面的双频参数

页面选择“共用”时，两频共用 `ssid_2g` 和 `wifi_key_2g`；选择“分别”时，5G 使用 `ssid_5g` 和 `wifi_key_5g`，5G 密码留空则沿用 2.4G 密码。两个频段的信道始终独立，支持 auto。本地 JSON 仍兼容 `shared`/`separate`。频段按真实 QCA hwmode 识别。

root 密码留空不增加覆盖逻辑。明确填写时，制作端通过 stdin 生成带随机 salt、兼容原认证库的 MD5-crypt 哈希，明文不写入源码、报告和固件。此基包的实际 crypt() 不支持 SHA256/SHA512-crypt，构建会用原 libc 校验正确及错误密码。首次启动在上游密码初始化之后，仅修改 root 的密码字段，保留其他账户和权限。之后普通重启不会恢复这个默认密码；恢复出厂会重新应用它。

首次启动迁移按 LuCI 实际规则处理 interface 的 ifname、旧桥接 interface 和 bridge device 的 ifname/ports；VLAN device 的父设备 ifname 保留。默认 vpn0 由更早的 OpenVPN defaults 创建，本脚本在其后清除，并记录迁移状态，普通重启不会重复重写网络。

## 本次保留和修改的内容

- 内核 FIT 与输入固件逐字节一致。
- 仅删除 `3proxy` 软件包、所属文件和专用启动项，移除其 opkg 记录；保留 SSR Plus+、microsocks 及配置。其余原有文件内容及权限均检查保留，opkg 数据库新增六个 AP/主题/usteer/LibreSpeed 包记录。
- 新增 `luci-theme-argon`、`luci-theme-footstrap`、`ucode-mod-uloop`、修改后的 `usteer` 、`ap8220-ap-config` 和 `librespeed-go`，不安装 Aurora 或 Argon 配置中文包。
- 使用原有 `opkg` 离线安装，保留包管理记录，没有替换 qca-hostap 或安装官方 wpad。
- 重建 SquashFS 和 sysupgrade tar，使用 OpenWrt fwtool 重建并检查镜像元数据 CRC。
- 原厂/原作者的签名没有沿用。修改后的镜像没有原作者签名。
- 没有修改 U-Boot、MIBIB、DTS、UBI 分区策略或原固件的软件源。

## LibreSpeed 网页测速

访问 `http://AP的管理地址:8989/`。使用提供的原始 IPK，SHA256 为 `f907ba56b3e4593354c368a37eecf6c7283b7a8bc25cbc67117871809ca83e5f`，不替换其中的程序或启动脚本。声明依赖为 libc，实际 ELF 还使用原固件的 libgcc；构建检查两个运行库，并以原 QWRT 库实际启动 ARM 程序，验证网页和测速接口。

IPK 的原始配置是 `enabled=0`，离线安装也会跳过其 postinst 账户创建。本版在 `zzzy-ap8220-librespeed` 首次启动脚本中创建缺失的 librespeed 用户/组，保留所有现有账户和 root 密码，然后执行以下设置并启用启动项。打包时另加入 `S99librespeed-go`，保证第一次开机的启动队列也包含此服务。成功后脚本由系统默认设置运行器移除，不会在普通重启时覆盖后来手动修改的参数；恢复出厂后重新应用。

```sh
uci set librespeed-go.config.enabled='1'
uci set librespeed-go.config.listen_port='8989'
uci set librespeed-go.config.database_type='none'
uci commit librespeed-go
/etc/init.d/librespeed-go enable
```

采用数据库 none，不保存测速统计，也不需要数据库服务。本测试衡量终端到 AP 的链路吞吐；实际结果仍受终端、Wi-Fi、AP CPU 和有线回程影响。构建用本机回环验证实际后端及原生 UCI/procd 参数生成，未对真实 procd 守护进程、非 root 身份切换或设备吞吐作实机验证。

## 首次启动和恢复出厂行为

1. `/etc/uci-defaults/zzzz-ap8220-ap` 在 QWRT 原有默认设置之后、网络服务启动之前，迁移旧式网络配置、清理默认 VPN、应用可选 root 哈希和基础 AP 配置，避免上游默认设置覆盖本次配置。
2. `ap8220-ap-init` 在启动后等待 QCA 生成两个无线设备及各自的 AP 接口，并确认无线配置文件连续两次检查不变，再按实际的 `qcawificfg80211`/`qcawifi` 配置节设置 Wi-Fi，不假定无线设备叫 `radio0`、`radio1`。
3. 配置成功后设置独立的基础、无线、IPv6 和主题标记。旧配置首次迁移增加 lan_v6、把已移除的 Aurora 选择改为 Footstrap、把加密改为 psk-mixed；保留 SSID/密钥、IPv4 地址及账户。迁移完成后普通重启不会重设用户修改。
4. 恢复出厂会删除这些状态标记，随后重新应用本固件内置的默认 AP 配置。
5. 无线配置尚未生成时最多等待 180 秒；未完成会记录日志，下次启动重试。`initialized=1` 表示配置已写入，不能代替无线链路在线和漫游效果的实测。

无上级 DHCP 服务时，AP 不会自动占用 `192.168.1.1`。这种情况下需要接到有 DHCP 的网络或通过串口管理。

## MAC 地址策略

优先级依次为：

1. `ap8220_ap.profile.base_mac` 中明确指定的有效地址。
2. 原厂 `product_info` 的 `product.mac`。
3. 本脚本此前已验证并保存的基准地址。
4. U-Boot 的 `ethaddr`，排除常见占位地址和本地管理地址。
5. 有效硬件序列号派生的稳定本地地址，明确标记为 `soc_serial`。这个地址不是原厂 MAC。

不会把运行时的随机 `eth0` 或 `br-lan` 地址当作原厂 MAC。大分区布局若删除了 `product_info`，需要检查是否有有效的 U-Boot 地址或硬件序列号。

如果上述来源均不存在，基础桥接和 DHCP 客户端配置仍会完成，`mac_pending=1`，自定义无线配置暂缓。此时从机身标识或可信原厂备份取得地址后设置：

```sh
uci set ap8220_ap.profile.base_mac='48:51:c5:12:34:56'
uci commit ap8220_ap
/usr/sbin/ap8220-ap identity
/etc/init.d/network reload
/etc/init.d/ap8220-ap-init restart
```

示例中的 MAC 必须换成本机真实地址。`/usr/sbin/ap8220-ap status` 可查看实际来源；它不会输出 Wi-Fi 密码。

## 802.11k/v/r 的边界

本次使用固件自带的 QCA hostapd 配置生成脚本验证了 FT-PSK、漫游域、本地密钥生成、邻居报告、信标报告和 BSS Transition 参数能够实际生成。

v0.3.0 新增 usteer 的 QCA 控制接口适配，实现客户端信息、同 SSID 邻居同步和建议式 BTM 的接口转换。默认关闭自动 band steering、负载策略和强制断开。v0.3.2 Actions 已通过真实 ARM ubus/usteer 进程联调，无线端使用模拟控制套接字；实际 QCA 无线与多 AP 漫游仍需实机验证，不能凭 UCI 参数或进程存在判断漫游已成功。详见 [USTEER.md](USTEER.md)。802.11r 的实际互通仍取决于另一台 AP 和客户端。

两台 AP 应使用相同的 SSID、密码、加密方式和漫游域；各自的 NAS ID 由本机 MAC 和无线接口名派生。建议真机验证时将两台 AP 的 2.4 GHz 信道分别设为 1 和 11，5 GHz 选用允许且不同的信道。

## 使用固件前的分区检查

本包只有 **sysupgrade 升级包**。不能把它作为 `factory.ubi` 用 `nand write` 或 `ubiformat` 裸写。

本次没有根据机型名称推定“原厂分区和 128M 大分区都能启动”。它延续输入 QWRT 的内核与升级逻辑，需核实当前设备的 U-Boot、分区名称、活动 UBI 和剩余容量。此前其他固件的双分区启动问题也不会由首次启动脚本自动修复。

将 `tools/check-device.sh` 和固件复制到 AP 的 `/tmp`，可以执行只读检查：

```sh
sh /tmp/check-device.sh /tmp/QWRT-AP8220-AP-v0.5.3-516f70c7-sysupgrade.bin
```

检查脚本只收集型号、`/proc/mtd`、启动参数、部分启动环境和 UBI 状态，最后运行 `sysupgrade -T` 验证镜像；不会刷写。

`sysupgrade -T` 通过只说明升级检查接受该镜像，不能证明分区布局和启动参数一定正确。检查失败应先分析原因，不应以 `-F` 绕过。

若设备已确认能正常运行本包对应的原版 QWRT、分区和 U-Boot 未变化，并已核实上述信息，可在 LuCI 的“系统 → 备份/升级”上传本包固件做首次真机验证，**取消保留配置**。保留其他系统的旧 network/wireless 或旧初始化标记，会影响这套首次启动流程。

## 真机验证项目

- 在上级路由租约列表中找到 `AP8220-xxxxxx`，用 `root` 和原固件规定的密码登录。
- 两个网口分别测试上联；接入 AP 网口的电脑和 Wi-Fi 终端都应直接从主路由获得地址。
- 检查两个频段都存在 `Home-WiFi`，确认实际信道、带宽与国家码。
- 执行 `ap8220-ap status` 和 `logread -e ap8220-ap` 查看状态和 MAC 来源。
- 修改 SSID 或主题后重启，确认修改保留。
- 使用两台 AP 和实际客户端测试 FT 漫游、掉包和连接稳定性；不能仅凭 UCI 参数判断效果。

## 文件布局和重建

- Release：固件、SHA256SUMS；简介直接在发布正文中。
- `files/`：本次 AP 配置源码和首次启动脚本。
- `packages/`：Footstrap、Argon 主题和固定 uloop 包；构建时生成 usteer 和 AP 配置包。
- `vendor/usteer/`：完整 usteer 上游源码归档、匹配头文件源码和 QCA 适配补丁。
- `tools/build.py`：支持可选 SHA256 和实际兼容性检查的二次封装程序。
- `tools/check-device.sh`：设备只读检查脚本。
- `tests/test_profile.py`：使用固件自身的 BusyBox ash、UCI 和 QCA hostapd 生成脚本进行隔离配置测试。
- 本地构建的 `dist/`：镜像及制作和主题检查记录，报告不上传为 Release 附件或 Artifacts。

重建需要 Linux、Python 3.12、`squashfs-tools`、`gcc`、`qemu-user-static`、`libjson-c-dev`、`curl`、`patch`、`zstd` 和 `requirements.txt` 中的 Python 包。原始 QWRT 不重复包含在本包中。

```sh
python3 -m pip install -r requirements.txt
python3 tools/build.py \
  --input /path/to/QWRT-R26.10.11-ipq807x-generic-aliyun_ap8220-squashfs-nand-sysupgrade.bin \
  --work /path/to/a-new-work-directory
```

此前已验证基准原固件 SHA256 为（其他兼容基包也可制作，但需要相应真机验证）：

```text
516f70c7282d186993ee6216c333cca844a0ae8203a3b75e0b6e6f44e1829d30
```

重建工具不会连接或刷写设备。它会在打包前运行配置测试，并在打包后逐个比较原有文件内容、权限、软链接和内核。
