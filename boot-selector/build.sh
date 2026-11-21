#!/bin/bash
# Builds the boot selector and installs it on its own partition (label SELECTOR).
# Run as root on an Ubuntu system of the same machine; uses that system's signed
# kernel and boot loader, and its Wi-Fi profile.
#
#   sudo ./build.sh [--install]
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
K=$(uname -r)
W=/var/tmp/selector-build; R=$W/root
VERSION=${VERSION:-0.1.0}
BROKERS=${BROKERS:-"192.168.1.101 192.168.1.100"}
# Optional "ip=hardware-address" pairs for brokers that address discovery does not reach.
BROKER_NEIGH=${BROKER_NEIGH:-}

export DEBIAN_FRONTEND=noninteractive
for p in busybox-static mosquitto-clients zstd cpio; do dpkg -s $p >/dev/null 2>&1 || apt-get install -y -q $p >/dev/null; done

rm -rf $W; mkdir -p $R/usr/{bin,sbin,lib,lib64} $R/{etc,proc,sys,dev,run,tmp,mnt/selector,var/run}
for d in bin sbin lib lib64; do ln -s usr/$d $R/$d; done

cp /usr/bin/busybox $R/usr/bin/busybox
file $R/usr/bin/busybox | grep -q 'statically linked' || { echo "busybox is not static"; exit 1; }
for a in $($R/usr/bin/busybox --list); do [ -e $R/usr/bin/$a ] || ln -s busybox $R/usr/bin/$a; done
# The kernel loads modules on demand by running /sbin/modprobe: it must exist at that path.
ln -s ../bin/busybox $R/usr/sbin/modprobe

copy_bin() {   # a dynamic program and the libraries it needs
  rm -f "$R/usr/bin/$(basename "$1")"   # never write through a busybox applet link
  cp "$1" $R/usr/bin/
  ldd "$1" | awk '/=>/ {print $3} /ld-linux/ {print $1}' | while read -r lib; do
    [ -e "$lib" ] || continue
    dest=$R/usr/${lib#/usr/}; dest=${dest/\/usr\/\/lib/\/usr\/lib}
    case "$lib" in /lib/*|/lib64/*) dest=$R/usr$lib ;; esac
    mkdir -p "$(dirname "$dest")"; cp -L "$lib" "$dest"
  done
}
for b in /usr/sbin/wpa_supplicant /usr/bin/mosquitto_sub /usr/bin/mosquitto_pub; do copy_bin $b; done
# The full "ip", under its own name: busybox's cannot set a neighbour entry, and
# the busybox shell runs its own applet for the plain name.
cp "$(command -v ip)" $W/ip-full; copy_bin $W/ip-full
$R/usr/bin/busybox --list >/dev/null || { echo "busybox was damaged while copying programs"; exit 1; }

# Kernel modules: Wi-Fi, its ciphers, a USB keyboard, and NVMe + FAT for the selector's own partition.
MODS="iwlmvm iwlwifi mac80211 cfg80211 libarc4 ccm cmac gcm ghash-generic ctr aes-generic sha256-generic nvme nls_iso8859-1 usbhid hid_generic"
# A name that is built into the kernel, or absent, simply contributes nothing.
for m in $MODS; do { modprobe -D $m 2>/dev/null || true; } | awk '/^insmod/ {print $2}'; done | sort -u | while read -r f; do
  rel=${f#/lib/modules/$K/}; rel=${rel#/usr/lib/modules/$K/}
  mkdir -p "$R/usr/lib/modules/$K/$(dirname "$rel")"
  case "$f" in
    *.zst) zstd -q -d "$f" -o "$R/usr/lib/modules/$K/${rel%.zst}" ;;
    *) cp "$f" "$R/usr/lib/modules/$K/$rel" ;;
  esac
done
cp /lib/modules/$K/modules.builtin /lib/modules/$K/modules.builtin.modinfo /lib/modules/$K/modules.order $R/usr/lib/modules/$K/
depmod -b $R/usr $K

mkdir -p $R/usr/lib/firmware
cp /lib/firmware/iwlwifi-ty-a0-gf-a0* $R/usr/lib/firmware/
cp /lib/firmware/regulatory.db* $R/usr/lib/firmware/ 2>/dev/null || true

# Wi-Fi credentials, taken from this system's own network profile.
python3 - "$R/etc/wpa_supplicant.conf" <<'PY'
import glob, sys, yaml
for path in sorted(glob.glob("/etc/netplan/*.yaml")):
    data = yaml.safe_load(open(path)) or {}
    for wifi in (data.get("network", {}).get("wifis") or {}).values():
        for ssid, ap in (wifi.get("access-points") or {}).items():
            psk = (ap.get("auth") or {}).get("password") or ap.get("password")
            if psk:
                open(sys.argv[1], "w").write('country=FR\nnetwork={\n  ssid="%s"\n  psk="%s"\n}\n' % (ssid, psk))
                sys.exit(0)
sys.exit("no Wi-Fi profile with a password found in /etc/netplan")
PY
chmod 600 $R/etc/wpa_supplicant.conf

cat > $R/etc/udhcpc.script <<'SH'
#!/bin/sh
case "$1" in
  bound|renew)
    ip addr flush dev $interface
    ip addr add $ip/${mask:-24} dev $interface
    [ -n "$router" ] && ip route add default via ${router%% *} dev $interface
    ;;
esac
SH
chmod +x $R/etc/udhcpc.script

cat > $R/etc/selector.conf <<CONF
VERSION=$VERSION
BROKERS="$BROKERS"
BROKER_NEIGH="$BROKER_NEIGH"
BROKER_PORT=1883
TOPIC_PREFIX=bureau/pc/boot
FALLBACK=${FALLBACK:-windows}
# System id -> description of its firmware boot entry.
LABEL_windows="Windows Boot Manager"
LABEL_ubuntu="ubuntu"
LABEL_agent="agent"
CONF
install -m 755 $HERE/init $R/init

( cd $R && find . -print0 | cpio --null -o -H newc --quiet | gzip -6 ) > $W/selector.img
cp /boot/vmlinuz-$K $W/vmlinuz
for need in nvme.ko nvme-core.ko iwlmvm.ko iwlwifi.ko mac80211.ko cfg80211.ko nls_iso8859-1.ko; do
  find $R/usr/lib/modules -name "$need" | grep -q . || { echo "missing module in the image: $need"; exit 1; }
done
echo "built: $(du -h $W/selector.img | cut -f1) initramfs, $(du -h $W/vmlinuz | cut -f1) kernel $K, $(find $R/usr/lib/modules -name '*.ko' | wc -l) modules"

[ "${1:-}" = "--install" ] || exit 0
DEV=$(blkid -L SELECTOR); [ -n "$DEV" ] || { echo "no partition labelled SELECTOR"; exit 1; }
M=$W/mnt; mkdir -p $M; mount $DEV $M
mkdir -p $M/EFI/ubuntu
# Signed loaders: the same files this system starts with.
cp /boot/efi/EFI/ubuntu/shimx64.efi /boot/efi/EFI/ubuntu/grubx64.efi /boot/efi/EFI/ubuntu/mmx64.efi $M/EFI/ubuntu/
cp $HERE/grub.cfg $M/EFI/ubuntu/grub.cfg
rm -f $M/EFI/ubuntu/grubenv; grub-editenv $M/EFI/ubuntu/grubenv create; grub-editenv $M/EFI/ubuntu/grubenv set failed=0
cp $W/vmlinuz $M/vmlinuz; cp $W/selector.img $M/selector.img
sync; find $M -type f | sed "s#$M##" | tr '\n' ' '; echo; df -h $M | tail -1
umount $M
echo "installed on $DEV"
