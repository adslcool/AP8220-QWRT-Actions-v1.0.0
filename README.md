# AP8220 QWRT AP — GitHub Actions

手动输入原固件 URL，自动集成 Footstrap、Argon 与 AP 首次启动配置，检查通过后发布到本仓库的 **Releases**。默认主题为 **Footstrap**，LuCI 语言保持简体中文。

v1.0.0 集成用户提供的 `librespeed-go 1.1.5-r5` ARM64 IPK。首次启动设置 `enabled=1`、`listen_port=8989`、`database_type=none`，创建专用服务账户并开启自启动。访问 `http://AP的管理地址:8989/` 进行网页测速，数据在浏览器与 AP 之间传输。后续手动停用或修改端口会保留。root 密码策略不变。

v0.5.3 仅移除原固件中的 `3proxy`：清除程序、专用配置、启动项及 opkg 包记录。保留 SSR Plus+、`microsocks` 及它们的配置；SSR Plus+ 中依赖 3proxy 的全局 HTTP/HTTPS 代理功能将不可用。其余 v0.5.2 行为保持不变。

v0.5.2 使用 `encryption='psk-mixed'`，无线算法自动；新增绑定 `br-lan` 的 `lan_v6` DHCPv6 客户端。删除 Aurora 和 `luci-i18n-argon-config-zh-cn`，默认改为 Footstrap。Release 仅保留修改后的固件和 SHA256SUMS，固件简介直接显示在发布正文。制作页面改用简短中文标签和“共用 / 分别”选项。

包含 Footstrap 0.14.16 和 Argon 3.1.0，使用固定的上游原始 IPK 并校验 SHA256。检查原固件 ucode/LuCI 下的 Footstrap 登录、登录失败、概览和网络页，以及资源、脚本和模块依赖。

制作页面继续提供双频共用或分别设置的 Wi-Fi 名称、密码、独立信道，以及可选 root 密码。首次启动迁移 loopback 等接口的旧式 ifname 配置，并清除原固件创建的默认 vpn0/tun0 接口及关联规则。

root 密码留空时沿用输入固件；明确填写时，只将兼容原固件认证库的哈希加入固件，首次启动应用一次。普通重启保留之后修改的 Wi-Fi、主题和 root 密码。此前的原文件权限修复继续保留。

本项目沿用已由用户实机确认可行的 QWRT 二次封装方法。usteer 使用实验性 QCA 控制接口适配；此前的实机反馈不代表 usteer 漫游已通过设备验证。

## 使用方法

1. 将本项目内容上传到 GitHub 仓库根目录，保留 `.github/workflows/build-release.yml`，不要只上传外层 ZIP 文件。
2. 将工作流文件提交到仓库默认分支，例如 `main`。
3. 打开仓库 **Actions → AP8220 QWRT AP - Build and Release → Run workflow**。
4. 输入下面的参数，然后运行。
5. 构建及发布成功后，到本仓库 **Releases** 下载固件和 SHA256SUMS。Actions 运行摘要也会给出 Release 链接。

| 输入 | 是否必填 | 说明 |
|---|---|---|
| `firmware_url` | 是 | 原始 QWRT AP8220 `sysupgrade.bin` 的 HTTP/HTTPS 直接下载链接 |
| `firmware_sha256` | 否 | 原固件 SHA256；填写后必须匹配才会继续 |
| `wifi_mode` | 是 | 页面选择“共用”或“分别”；默认共用 |
| `ssid_2g` | 是 | 2.4G 名称；共用模式也供 5G 使用；默认 Home-WiFi |
| `wifi_key_2g` | 是 | 2.4G 密码；共用模式双频共用；默认 12345678 |
| `channel_2g` | 是 | auto 或 1–13；默认 6 |
| `ssid_5g` | 否 | 分别模式的 5G 名称；默认 Home-WiFi-5G |
| `wifi_key_5g` | 否 | 分别模式的 5G 密码；留空沿用 2.4G 密码 |
| `channel_5g` | 是 | auto、36/40/44/48/149/153/157/161；默认 149 |
| `root_password` | 否 | 留空沿用原固件；填写则首次启动设置 |

共用模式共用的是名称和密码，两个频段的信道始终分别选择。Wi-Fi 名称为 1–32 个 UTF-8 字节，密码为 8–63 个 UTF-8 字节；不接受换行和控制字符。默认国家码 CN，2.4G 为 HT20，5G 为 HT80。

Actions 的这些输入是普通文本框。脚本会屏蔽密码日志，root 明文不写入生成的文件；发布固件仍含 Wi-Fi 配置和可选 root 哈希。

需要提供能直接返回固件文件的链接，网盘分享页面、HTML 下载页面、需要登录的页面不能代替文件直链。原固件不需要提交到仓库。

已验证基准原固件：

```text
QWRT-R26.10.11-ipq807x-generic-aliyun_ap8220-squashfs-nand-sysupgrade.bin
SHA256: 516f70c7282d186993ee6216c333cca844a0ae8203a3b75e0b6e6f44e1829d30
```

工作流使用本仓库自动提供的 `GITHUB_TOKEN`，任务声明了 `contents: write`，通常不需要另建 PAT。checkout 不持久化凭据，只有最后的发布步骤设置 `GH_TOKEN`。若仓库或组织策略限制 Actions 写入 Releases，需要由仓库管理员解除相应限制。

## 流程和发布内容

`下载 URL → 校验机型和哈希 → 安装主题与 AP/usteer/LibreSpeed 包 → 配置、IPv6、无线和主题检查 → 重建镜像 → 校验附件 → 发布 Release`

同一个 runner 完成构建与发布，直接读取本次制作的附件，不使用 Actions Artifacts 中转。先创建草稿，从接口响应取得数字 Release ID，再按此 ID 上传全部附件，核对 GitHub 返回的大小和 SHA256 后才正式发布。创建草稿后不再通过标签或列表重新查找它。失败不会公开一个缺少附件的新 Release。

更新程序后应提交代码，再点击 **Run workflow** 新建运行。旧运行的 **Re-run failed jobs** 仍使用旧提交，不会自动载入修复。本版只有一个任务，重跑会重新制作固件；运行结束不保留 runner 内的临时文件。发布失败请下载该次 Actions 日志排查；若附件已上传，会保留在未公开的草稿 Release 中。

在同一 runner 内再次调用发布脚本时，可复用同标签、同提交的已有草稿，跳过大小与哈希一致的附件，仅替换该草稿中不完整或不匹配的同名附件。已公开的 Release 只校验，不修改。

每次执行使用包含版本、时间、运行编号和重试编号的唯一标签，不覆盖旧发布版。

Release 只有两个附件：

- `.bin`：可下载的 sysupgrade 升级包。
- `SHA256SUMS`：该固件的 SHA256 校验和。

固件简介放在 Release 正文，包括原包版本、网络模式、IPv6、主题、双频名称/信道、加密、root 密码策略和检查摘要，不展示密码。构建报告和发布元数据只在 runner 内供检查使用，不作为附件上传。没有 bundle ZIP，也不上传成功或失败 Artifacts。

当前含实验性 usteer 适配，所有构建均发布为 Pre-release。真实 ARM ubus/usteer 联调、LibreSpeed 启动配置及网页/上传/下载接口、网络迁移和账户文件保持检查必须通过；自定义 root 密码还要求首次启动测试通过。跳过联调的本地输出会被发布程序拒绝。Actions 不代表通过了真机验证。

## 支持范围

适配 **Aliyun AP8220 / QWRT / QSDK `ipq807x/generic` / aarch64_cortex-a53 / XZ SquashFS / opkg / QCA hostapd**。

脚本会实际检查元数据、AP8220 的 `eth1`/`eth0` 网口定义、所需包、QCA k/v/r 参数、首次启动脚本顺序和镜像结构。输入官方 OpenWrt、ImmortalWrt、错误机型、不匹配后端或已经二次封装的 AP 固件，会停止并给出原因；不会强行套用配置。

当前配方要求原包没有已安装的 Argon、Footstrap、Aurora、Argon 配置中文包、usteer、ucode-mod-uloop 或 librespeed-go，且内置所需依赖。usteer 适配还要求匹配基准固件的 ucode/libubox/libubus 版本；详细约束见 [USTEER.md](USTEER.md)。不同文件属主布局、额外设备节点或更晚默认设置脚本的基包，需针对其差异适配后再制作。

不会改写输入固件的内核、DTS、U-Boot、MIBIB 或 UBI 分区策略，也不会因 URL 换包就假定适配所有分区。

## AP 默认配置

| 项目 | 设置 |
|---|---|
| 主机名 | `AP8220-稳定MAC后6位` |
| 管理 IPv4 | 上级 DHCP 分配 |
| 管理 IPv6 | `lan_v6`，DHCPv6，设备 `br-lan`；不请求下游前缀委派 |
| 网口 | `eth1` + `eth0` 桥接至 `br-lan`，取消独立 WAN/WAN6 |
| DHCP/RA | 关闭 AP 本机的 DHCP、DHCPv6、RA 服务 |
| Wi-Fi | 两个频段均为 `Home-WiFi`，密码 `12345678` |
| 无线安全 | `psk-mixed`（WPA/WPA2-PSK），算法自动 |
| root 密码 | 默认留空，沿用输入原固件；制作时可明确指定 |
| 主题 | Footstrap 0.14.16 默认，保留 Argon 3.1.0；不含 Aurora 和 Argon 配置中文包 |
| 国家码/频道 | CN，2.4 GHz 信道 6 / HT20，5 GHz 信道 149 / HT80 |
| 网页测速 | LibreSpeed Go，默认启动，端口 `8989`，数据库 `none` |
| 漫游 | 802.11k/v/r 参数，漫游域 `a1b2`；实验性 usteer 邻居同步与建议式 BTM |
| MAC | 原厂产品信息优先；支持明确指定、可信启动环境和稳定硬件序列号回退 |

AP 配置、无线等待顺序、MAC 异常恢复和真机检查方法见 [AP-CONFIG.md](AP-CONFIG.md)。普通重启保留用户修改；恢复出厂后会重新应用内置默认设置。

在 **系统 → 系统 → 语言和界面/主题** 中可切换 Footstrap、Argon。首次启动和恢复出厂默认 Footstrap，普通重启保留手动选择。旧版本保留配置升级时，会一次性增加 lan_v6、把仍选择 Aurora 的配置切到 Footstrap，并把 QCA AP 的加密改为 psk-mixed；保留 SSID、密码、IPv4 管理地址及 root 密码。之后不反复覆盖这些设置。若要应用本次制作的全部 Wi-Fi/root 默认参数，应取消保留旧配置。

**升级时应取消保留旧配置，并先核对当前分区。sysupgrade.bin 不能作为 factory.ubi 裸写。** Actions 无法验证真实设备的启动、吞吐和漫游效果。

## 修改默认参数

在 Actions 页面选择本次 Wi-Fi 和 root 密码参数。制作前修改 `files/etc/config/ap8220_ap` 可设置国家码、漫游域、主机名前缀和手动 MAC；参数生成器保留这些手动字段。直接本地构建时，也可手动修改此文件的 Wi-Fi 字段。

root 输入留空时，不增设密码覆盖。填写时，哈希保存在只允许 root 读取的 `/etc/ap8220-root-password.hash`，最后的首次启动脚本仅替换 root 密码字段，保留其他账户、老化参数和 shadow 权限，成功后标记已应用。制作前后原账户文件仍逐字节一致。原固件已有的密码初始化脚本不修改。

此基包的系统 crypt() 已验证支持原默认密码使用的 MD5-crypt，SHA256/SHA512-crypt 无法通过系统认证。因此本版自定义 root 使用带随机 salt 的原固件兼容格式，并在原始 libc 下验证正确密码成功、错误密码失败；不替换认证库。

要应用本次制作的 Wi-Fi/root 默认值，升级时应取消保留旧配置。保留旧配置时，已设置的密码和无线初始化标记继续生效。恢复出厂会重新应用本次固件内置的默认值。

下载链接中的查询参数不会写入发布报告。主题包固定随源码提供，版本和 SHA256 见 `tools/theme_manifest.py`；构建不依赖临时软件源下载主题，不执行上游在线安装脚本。

## 本地重建和检查

Linux 需要 `gcc squashfs-tools qemu-user-static shellcheck nodejs python3-venv libjson-c-dev curl patch zstd openssl`。

```sh
python3 -m venv .venv
. .venv/bin/activate
python3 -m pip install -r requirements-dev.txt
python3 -m unittest discover -s tests -p 'test_pipeline.py' -v
python3 tools/build.py --input /path/to/original-qwrt.bin --work .work/build --output dist
```

也可以从 URL 下载后再构建：

```sh
export FIRMWARE_URL='https://example.com/original-qwrt-sysupgrade.bin'
export FIRMWARE_SHA256=''
python3 tools/download_firmware.py --output .input/firmware.bin --record .input/download.json
python3 tools/build.py --input .input/firmware.bin --download-record .input/download.json --work .work/build --output dist
```

本地 `dist`、工作目录和下载输出需使用空目录。制作工具不会连接或刷写 AP。

若要本地使用与 Actions 相同的参数，在本地 JSON 文件中填写上表同名字段，再先运行 `python3 tools/configure_profile.py --settings /path/to/settings.json`。此命令会更新项目的默认配置；root 留空会清除此前生成的可选密码哈希。不要将含明文密码的 settings.json 提交到仓库。

usteer 的实现范围、默认关闭的策略和真机验收命令见 [USTEER.md](USTEER.md)。构建会下载并校验固定的 aarch64 musl 工具链，编译随项目提供源码的 usteer；不会升级输入固件的系统库。

用户提供的 v0.5.0 Actions 日志 `logs_108595434523.zip` 已确认固件重建和 9 个发布附件准备成功，7 组真实 ARM ubus/usteer 联调通过，Aurora 为默认主题，账户文件逐字节一致。发布步骤在创建草稿后重新查找草稿时失败。无线控制端仍使用模拟套接字，AP8220 真机漫游验证待进行。

本版保留参数、密码和网络迁移测试，检查 Footstrap 默认、两个主题注册、lan_v6 和真实 QCA hostapd 的 psk-mixed 输出。发布测试覆盖仅上传两个附件、简介不含密码、草稿不可见、部分上传重试和真实 GitHub CLI 的本地 HTTP 传输。源码包内 `LOCAL-VALIDATION.json` 记录本地范围；新版本的真实 GitHub 发布仍需 Actions 验证。

若后续原文件检查失败，先查看 Actions 日志；本地复现时，`dist/ORIGINAL-FILES-CHECK.json` 会区分内容、权限、链接目标和类型差异，工作目录的 `original-permissions.json` 记录本次恢复的权限。这些失败诊断不上传为 Artifacts。
