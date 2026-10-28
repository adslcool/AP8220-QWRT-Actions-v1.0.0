#!/bin/sh
# Read-only device information and optional sysupgrade validation. Never flashes.
printf '\nDevice\n'
ubus call system board 2>/dev/null
printf '\nMTD partitions\n'
cat /proc/mtd
printf '\nKernel command line\n'
cat /proc/cmdline
printf '\nBoot environment (selected keys, read only)\n'
fw_printenv bootcmd active boot_part mtdparts 2>/dev/null
printf '\nUBI devices\n'
for dev in /sys/class/ubi/ubi[0-9]*; do
	[ -f "$dev/mtd_num" ] || continue
	printf '%s: mtd=' "${dev##*/}"
	cat "$dev/mtd_num"
	for key in total_eraseblocks avail_eraseblocks bad_peb_count reserved_for_bad; do
		[ ! -r "$dev/$key" ] || { printf '%s=' "$key"; cat "$dev/$key"; }
	done
done
printf '\nUBI volumes\n'
for vol in /sys/class/ubi/ubi[0-9]*_[0-9]*; do
	[ ! -f "$vol/name" ] || { printf '%s ' "${vol##*/}"; cat "$vol/name"; }
done
if [ "$#" -gt 0 ]; then
	[ -f "$1" ] || { printf 'Image file not found\n' >&2; exit 1; }
	printf '\nImage SHA256\n'
	sha256sum "$1"
	printf '\nRead-only sysupgrade validation\n'
	sysupgrade -T "$1"
	exit $?
fi
exit 0
