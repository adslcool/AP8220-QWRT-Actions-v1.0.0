# Sources and provenance

## Base firmware

User-supplied file:

`QWRT-R26.10.11-ipq807x-generic-aliyun_ap8220-squashfs-nand-sysupgrade.bin`

SHA256: `516f70c7282d186993ee6216c333cca844a0ae8203a3b75e0b6e6f44e1829d30`

Its embedded release and defaults identify QWRT / QSDK 12.5 / Linux 5.4.213. The original publisher's full source tree was not supplied. This deliverable contains the AP adaptation and repacking tools, not a claim to provide that source tree.

## Argon

Upstream: https://github.com/jerrykuku/luci-theme-argon

Release: https://github.com/jerrykuku/luci-theme-argon/releases/tag/v3.1.0

Files downloaded from that release and installed unchanged:

| File | SHA256 |
|---|---|
| luci-theme-argon_3.1.0_all.ipk | 837337a26bb3d51d9634398de30ba9d64d238476d4d0834d472ade2cf97fd826 |

The theme package declares Apache-2.0. Argon 3.1.0 includes the configuration UI; the separate luci-i18n-argon-config-zh-cn package was removed in v0.5.2. Base LuCI Chinese support is inherited from the input firmware.

## fwtool

Upstream: https://github.com/openwrt/fwtool

`tools/fwtool/` contains the upstream C source and its three headers used to rebuild and check image metadata. Copyright and license notices remain in those source files. The output image was also checked with the base QWRT's own fwtool under QEMU.

## Host-side checks

- squashfs-tools 4.6.1
- qemu-user-static 8.2.2
- ShellCheck 0.9.0
- PySquashfsImage 0.9.0
- Firmware-supplied BusyBox ash, UCI, ucode, QCA hostapd configuration generator

The user subsequently reported the original QWRT repacking/AP method working on an actual device. New URL inputs do not automatically inherit that hardware validation. No router was contacted or flashed during the v0.2.0 Actions development checks.

## GitHub Actions

The workflow uses `workflow_dispatch`, GitHub-hosted Ubuntu 24.04, the repository's `GITHUB_TOKEN`, and GitHub CLI. Official action tags were resolved through the GitHub API and pinned to their commit SHA:

| Action | Pin |
|---|---|
| actions/checkout v6 | d23441a48e516b6c34aea4fa41551a30e30af803 |

Workflow syntax was checked using actionlint 1.7.12. Its Linux amd64 release archive SHA256 was `8aca8db96f1b94770f1b0d72b6dddcb1ebb8123cb3712530b08cc387b349a3d8`.

Official references:

- https://docs.github.com/actions/managing-workflow-runs/manually-running-a-workflow
- https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax
- https://cli.github.com/manual/gh_api
- https://docs.github.com/en/rest/releases/releases
- https://docs.github.com/en/rest/releases/assets
- https://github.com/actions/runner-images/blob/main/images/ubuntu/Ubuntu2404-Readme.md

## usteer QCA adaptation (v0.3.0)

- Upstream full source: https://github.com/openwrt/usteer/tree/7532e1252fb937fe34b6da05e86fd34babd91ef5 (GPL-2.0-only per OpenWrt package). Complete unmodified archive is included, with the modification in `vendor/usteer/100-qca-control-backend.patch`.
- Matching link-time headers/library sources: https://github.com/openwrt/libubox/tree/49056d17 and https://github.com/openwrt/ubus/tree/60e04048 . Original copyright/license notices remain in the included full archives. These libraries are compiled for linking only; they are not installed over the ROM libraries.
- Toolchain and authoritative SHA256 list: https://downloads.openwrt.org/releases/24.10.6/targets/qualcommax/ipq807x/sha256sums . Pinned toolchain SHA256: `7cc53103a968bb8eb87f162af48dbb223b71119fc301c76aad568a718a61f2be`.
- ucode-mod-uloop IPK: https://downloads.openwrt.org/releases/24.10.6/packages/aarch64_cortex-a53/base/ . SHA256 `2ee5554191e205aebe4e9033b686cb07c2e0b5ce16ee744554549a043af61c58` (ISC). This is the only additional upstream runtime library module. The original firmware's ucode remains unchanged.
- ucode module APIs: https://github.com/jow-/ucode/tree/5f712ffd/lib and https://ucode.mein.io/ . Native target-module loading and the actual uloop timer are tested under the supplied firmware interpreter.
- hostapd control behavior checked against the supplied QCA binary/scripts and primary source at https://android.googlesource.com/platform/external/wpa_supplicant_8/+/refs/heads/main/hostapd/ctrl_iface.c and `src/ap/neighbor_db.c`, `src/ap/ctrl_iface_ap.c`. QCA radio behavior still requires device validation.
- New bridge/config/test code uses ISC SPDX notices; the upstream usteer code retains GPL notices.

The local environment denies AF_UNIX socket creation. The real ARM ubus/usteer IPC test is supplied and mandatory on Actions, but has not completed locally. No real AP or GitHub Release was modified during v0.3.0 development. See `LOCAL-VALIDATION.json` for the exact scope of completed checks.

The v0.3.0 local repack also restores and compares all 306 original directory modes, including modes left temporary by an unsquashfs device-node extraction error. Original account files and upstream password initialization scripts remain unchanged.

## v0.3.1 Actions repair

The user-supplied `logs_108530772300.zip` records nine Release test errors at Check project code, all caused by the missing repository `.gitignore`. The firmware download and real usteer IPC checks were not reached. The failure was reproduced with a project copy omitting this optional file. After the fix, all 17 pipeline tests passed in a complete checkout without `.gitignore`, including actual loopback HTTP download tests and the new bundle regression. Required source files/directories still produce explicit errors. No firmware hardware test or remote GitHub workflow run is claimed for v0.3.1.

## v0.3.2 permission-preservation repair

The user's `logs_108546553693.zip` shows that all 17 pipeline unit tests and all seven real ARM ubus/usteer IPC groups passed on the v0.3.1 Actions runner. The build then failed original-file verification for 868 paths. The supplied firmware SHA256 matches the known baseline. All reported paths exactly match the union of 867 files with group-write bits affected by umask 0022 and one setuid executable (`/usr/bin/fusermount`) affected by non-root extraction.

Primary implementation reference: https://github.com/plougher/squashfs-tools/blob/4.6.1/squashfs-tools/unsquashfs.c . Root extraction resets umask; non-root extraction clears setuid/setgid bits. The new build restores the source permission bits for regular files as well as directories and continues strict content/link comparison.

Using the actual supplied QWRT root, a local SquashFS roundtrip reproduced exactly the same 868 mode-only differences. After applying the restoration routine and packing again, all 6,043 original entries (5,737 files/links/device nodes and 306 directories) matched, including account bytes and modes. This focused roundtrip did not install new packages or claim a complete v0.3.2 firmware/Release build. The pipeline adds a small real-SquashFS regression covering mode restoration, setuid, symlink safety, unchanged new-file modes and password-content tampering.

## v0.3.3 draft Release lookup repair

The user's `logs_108551376536.zip` records a complete successful v0.3.2 firmware build: 19 pipeline tests, seven AP profile tests, seven real ARM ubus/usteer IPC groups, 26 Argon checks, preserved kernel and original files, and byte-identical account files. The eight prepared Release assets were saved as the `ap8220-ap-release` Actions artifact. Publishing then failed after asset upload when querying the still-draft Release through `/releases/tags/{tag}`.

GitHub documents the tag endpoint as returning a published Release. Drafts are included in the authenticated Release list for users with push access. GitHub CLI likewise discovers drafts separately and fetches their details by Release ID:

- https://docs.github.com/en/rest/releases/releases#list-releases
- https://docs.github.com/en/rest/releases/releases#get-a-release-by-tag-name
- https://docs.github.com/en/rest/releases/releases#get-a-release
- https://github.com/cli/cli/blob/trunk/pkg/cmd/release/shared/fetch.go

The v0.3.3 publisher added a fully paginated Release-list fallback for pending tags and verified uploaded assets/publication by numeric Release ID. Its 23 pipeline tests passed, but the subsequent v0.5.0 run exposed a second failure: the just-created draft was not discoverable by either lookup path. The v0.5.1 change below supersedes the create/upload/edit implementation. These historical checks did not contact or publish to GitHub.

## v0.4.0 network migration and selectable AP parameters

The migration follows the three actual predicates and handlers in the supplied firmware's `/www/luci-static/resources/view/network/interfaces.js`: legacy bridge interfaces, bridge devices with ifname instead of ports, and interfaces with ifname instead of device. Tests run those original predicates in Node.js before and after applying the real shell helper with the firmware's ash/UCI. The remaining loopback ifname was not covered by the previous LAN-only cleanup. VLAN device parent ifname fields remain valid and are retained. The supplied `/etc/uci-defaults/openvpn` creates vpn0/tun0 even when its default VPN is disabled; the final AP defaults remove that interface and its default firewall references.

Dispatch inputs are read from the GitHub event JSON file. No input expression is interpolated into a shell command. Common/separate SSIDs and keys, independent channels and an optional root password are validated before generating the effective profile. Root plaintext is passed to OpenSSL on stdin; only the encoded hash is included in the firmware. The input UI is plain text, so the workflow registers masks before using password values. The generated public summary omits keys and root hashes. Wi-Fi configuration and the optional root hash have package modes 0600.

The original QWRT authentication library was tested directly using a small cross-compiled program linked dynamically to its actual musl libc (`tests/verify_password.c`). MD5-crypt matches the original password format and works; SHA256-crypt and SHA512-crypt are rejected by this firmware's crypt(), although its OpenSSL can generate them. The implementation therefore uses a random-salt MD5-crypt hash without replacing any authentication library. Tests verify the correct password, rejection of an incorrect password, UTF-8 and the 256-byte boundary. Empty root input leaves the upstream policy intact, including empty/locked/custom original shadow entries. Explicit customization changes only the root hash during first boot and preserves other accounts, aging fields and file modes.

All 30 pipeline tests pass. Thirteen real-ash/UCI profile groups pass with source defaults and also during a complete inspection build using separate SSIDs/keys, UTF-8, literal quotes/shell metacharacters, auto 2.4G channel, a selected 5G channel and an optional root hash. That build checks 26 Argon cases and all 6,043 original filesystem entries, preserving account bytes/modes and the kernel. Source modes are restored after all guest/tool checks immediately before packing; strict original-content verification remains in place.

The inspection build used `--skip-ipc-test` because this local environment does not allow AF_UNIX; the actual release gate rejected it as intended. It is not a published or delivered firmware. GitHub Actions still requires the seven real ARM ubus/usteer IPC groups on every run. No remote repository, Release or router was changed, and no hardware Wi-Fi/roaming validation is claimed. Official dispatch input reference: https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/trigger-a-workflow .

## v0.5.0 additional themes and Aurora default

Historical v0.5.0 integration: the user requested these repositories and that version included both IPKs. v0.5.2 removes Aurora and its bundled license; only Footstrap remains from this table. Its IPK bytes remain unchanged and SHA256 matches the upstream release digest.

| Theme | Release and source commit | IPK SHA256 |
|---|---|---|
| Footstrap | [v0.14.16](https://github.com/VizzleTF/luci-theme-footstrap/releases/tag/v0.14.16), `4523e6e998652523b291627819381ccb0867e371` | `f7a1cbd6f05dd884a822623b9d92349ba033643cfa600139c134eeca8797c997` |
| Aurora | [v1.4.0](https://github.com/eamonxg/luci-theme-aurora/releases/tag/v1.4.0), `84c27b66952e6fc4be60128067a6b88bfd34afb5` | `5b3f2d669ef82e575498d1b99c4b9b498088540eeff7db989f214b63d3ef8a8e` |

Both upstream projects declared Apache-2.0. Footstrap's unmodified license remains under `vendor/themes/`, and its luci-base dependency is already present in the input image. No online installer or feed registration script was run. Argon 3.1.0 remains; its separate settings translation no longer does.

In v0.5.0, first boot registered three themes and selected Aurora. The current v0.5.2 default is Footstrap with Argon as the other choice; later manual theme changes remain persistent.

The additional verifier executes the actual QWRT LuCI runtime and theme templates with the original ARM ucode/native modules under QEMU, adapting only the runtime's template/configuration filesystem paths to isolated fixtures. HTTP/session/board data use test fixtures and translation uses an identity callback. Both new themes render login, failed login, overview and network pages, without falling back. Emitted inline JavaScript is syntax checked, local assets and requested LuCI modules must exist, and required UI APIs (including Footstrap's RangeSlider) are checked in the original core. Native ucode template compilation, package shell scripts, JSON/manifest parsing and JavaScript dependency checks are also required. Argon's existing checks remain in place. Exact results and limitations are in `LOCAL-VALIDATION.json`; this does not claim browser interaction or device visual testing.

## v0.5.1 direct Release publication without Actions Artifacts

The supplied `logs_108595434523.zip` shows a complete v0.5.0 build and nine prepared assets; all seven real ARM ubus/usteer IPC groups passed. Publishing stopped with `Created draft Release was not found` after the create command. The log does not establish why GitHub's immediate tag/list queries failed to return that draft.

Creation now calls REST `POST /repos/{owner}/{repo}/releases` with JSON on stdin and uses the returned numeric ID and validated upload URL. Every binary is uploaded as raw `application/octet-stream` through `gh api --input FILE`, checked against the local size/SHA256, and then checked again through `GET /releases/{id}`. Only a complete verified draft is published with `PATCH /releases/{id}`. No tag/list rediscovery or high-level `gh release upload/edit` occurs after creation. Existing drafts are still checked against the exact tag and target commit; matching assets are reused, while partial/mismatched same-name assets may be replaced only in that draft. Published releases remain read-only on retry.

At the user's request, build, verification and publication now share one Ubuntu runner. All success/failure upload-artifact steps and the download-artifact step are removed. The job needs contents:write; checkout disables credential persistence, and only the final publisher receives GH_TOKEN as an environment variable. Failed runs retain their ordinary logs and any already-uploaded draft assets, with no Actions artifact upload. Re-running the sole job performs a fresh build.

Regression tests hide the new draft from both tag and list discovery, verify ordered ID-based upload/check/publication, reject malformed create responses and unexpected assets, resume partial uploads, and keep failures unpublished. A real GitHub CLI/local HTTP check validates JSON booleans/UTF-8 on stdin, unchanged binary bytes, PATCH and 404 handling. This local transport check does not publish to GitHub. This release-only change retains v0.5.0's firmware adaptation and upstream package bytes.

JSON and binary API calls set explicit Content-Type and Content-Length; the JSON byte count uses UTF-8. The local transport check used upstream GitHub CLI 2.103.2, with Linux amd64 archive SHA256 `62ccf747115ddc61237efffdde66a3c8f70ba249433dc64f70801089b59c59c8` verified against its official release metadata. All 36 tests passed, including that real CLI transport check; actionlint 1.7.12, ShellCheck 0.9.0 and Python syntax checks passed. No v0.5.1 firmware image or GitHub Release was produced locally.

## v0.5.2 Footstrap default, wireless auto cipher, IPv6 client and minimal Releases

At the user's request, the recipe drops Aurora and luci-i18n-argon-config-zh-cn entirely and selects Footstrap by default. Both remaining theme IPKs and the pinned uloop IPK retain their verified original bytes. The original base LuCI Chinese package remains untouched.

Both AP bands set exactly `encryption=psk-mixed`, without a forced cipher suffix. The supplied QWRT's own hostapd generator maps this to WPA/WPA2 and automatic CCMP/TKIP; tests execute that generator and check actual wpa=3, wpa_pairwise, FT-PSK and k/v fields. Network defaults add the explicit lan_v6 DHCPv6 interface on br-lan, request an address with reqaddress=try, and disable prefix delegation requests with reqprefix=no and delegate=0. The original odhcp6c and luci-proto-ipv6 are required. AP DHCP/RA servers remain disabled.

One-time migration markers let retained initialized AP profiles acquire lan_v6 and the security change without resetting SSIDs, keys, IPv4 settings or root accounts. An obsolete Aurora selection changes to Footstrap; an existing usable theme choice is retained. The real init script must launch the wireless worker if the security migration is pending, even when initialized=1. Later boots preserve manual changes.

Release preparation keeps checks and reports local to the runner. Only the firmware .bin and SHA256SUMS are public assets; release notes are passed as the Release body, and local publishing identity/notes live in a private metadata directory that is never uploaded. Tests reject extra assets, notes tampering and failed build gates, and verify that neither Wi-Fi nor root secrets appear in the description. The workflow continues using one runner with zero Actions artifact uploads, including failure paths. Input descriptions are shortened and the Wi-Fi mode choices are Chinese, with legacy shared/separate JSON values still accepted.

Current local validation passed 37 pipeline tests (including real gh/local HTTP transport), 15 real firmware ash/UCI/profile groups, and 122 theme checks (Argon 26, Footstrap 96). A complete inspection repack preserved all 6,043 original filesystem entries except the explicitly allowed opkg status update, with byte-identical kernel and account files. Aurora and the removed translation's installed files were confirmed absent. The local inspection still skips real AF_UNIX IPC because this environment forbids it; the actual release gate rejected that inspection image. GitHub Actions continues requiring real IPC before preparing a Release. No v0.5.2 GitHub Release, upstream IPv6 lease or AP hardware result is claimed.

## v0.5.3 remove only 3proxy

The offline transaction removes only 3proxy 0.9.5-4: its four package-owned data files, four opkg metadata files, dedicated S993proxy startup link, and package status paragraph. It never runs firmware removal scripts. The manifest, metadata, startup target, hard dependencies and shared ownership are validated before any deletion. Unknown layouts stop the build. SSR Plus+, microsocks, their configurations and shared libraries remain unchanged. The SSR Plus+ global HTTP/HTTPS feature requiring 3proxy is no longer available.

After every guest/configuration/theme check, the validated tree is renamed to rootfs-final, source modes are restored, and this transaction is applied last. The status file is atomically replaced. The packed SquashFS package database and all original paths are checked before publication; only validated removed paths are exempted from the missing-file check. All other file contents, modes and link targets remain strictly checked. Release preparation requires the removal result as well as the existing real IPC gate. Workflow inputs, passwords, themes, wireless security, IPv6 and two-asset Releases remain unchanged. See LOCAL-VALIDATION.json for current checks and limits.

## v1.0.0 LibreSpeed Go integration

The user supplied librespeed-go_1.1.5-r5_aarch64_cortex-a53(2).ipk. It is bundled unchanged under the canonical package filename, SHA256 f907ba56b3e4593354c368a37eecf6c7283b7a8bc25cbc67117871809ca83e5f. Its control record declares libc, aarch64_cortex-a53, Require-User librespeed:librespeed and LGPL-3.0-only; ELF inspection finds /lib/ld-musl-aarch64.so.1 with libc.so and libgcc_s.so.1. The verified compatible libraries remain those from the input QWRT firmware. Package source is feeds/packages/net/librespeed-go; upstream URL from its control metadata is https://github.com/librespeed/speedtest-go . This is the supplied package, not a claim of a newly verified upstream build.

The original config and init are retained byte-for-byte. Offline installation sets IPKG_NO_SCRIPT=1, so a dedicated defaults script creates the missing non-root identity using original QWRT group_add_next/user_add and sets the three requested UCI values. Original account entries remain unchanged. The original rc.common enables S99librespeed-go; the image also includes that link before the first boot queue is expanded. Successful defaults run only once; later manual disable/port settings persist. No root password policy changes.

The required verifier uses original firmware ash, UCI, jshn and procd shell libraries with isolated config/account files, and only mocks ubus/chown/lock side effects. It checks preserved original accounts, a non-root service identity, idempotency, native startup command and JSON generation. The actual supplied ARM backend is launched under QEMU with original ROM libraries, a loopback bind and fixed fixture coordinates; it must serve embedded HTML/JavaScript and ping/download/upload endpoints on 8989. No public network benchmark, real procd privilege switch or hardware throughput result is claimed. Existing profile/theme/IPC and strict original-image checks remain; Release preparation also requires the LibreSpeed report. Release assets remain firmware plus SHA256SUMS, with no Actions Artifacts.
