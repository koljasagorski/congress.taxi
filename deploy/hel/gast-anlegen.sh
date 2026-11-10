#!/bin/bash
# Legt CT 220 „congress" auf dem Proxmox-Wirt hel an und startet die Grundinstallation.
# Läuft AUF hel als root (Aufruf siehe deploy/hel/README.md). Bricht ab, wenn die ID oder die
# Adresse belegt ist – es überschreibt nie einen bestehenden Gast.
#
# Erwartet im selben Verzeichnis: gast-grundlage.sh (aus patchletter.com/deploy/hel/) und
# nftables.conf (aus diesem Repo). Die Grundinstallation (Updates, Docker CE, SSH-Härtung) läuft
# im Gast ABGEKOPPELT mit Protokoll /root/grundlage.log – die Firewall und das Werkzeug folgen
# danach mit `gast-fertig.sh`, wenn das Protokoll „FERTIG GRUNDLAGE“ enthält.
set -euo pipefail
cd "$(dirname "$0")"

ID=220
IP=10.10.10.80
TEMPLATE=local:vztmpl/debian-13-standard_13.6-1_amd64.tar.zst

if pct status "$ID" >/dev/null 2>&1 || qm status "$ID" >/dev/null 2>&1; then
  echo "ABBRUCH: Gast $ID existiert bereits." >&2
  exit 1
fi
if ping -c1 -W1 "$IP" >/dev/null 2>&1; then
  echo "ABBRUCH: $IP antwortet bereits im Netz." >&2
  exit 1
fi
for f in gast-grundlage.sh nftables.conf; do
  [ -s "$f" ] || { echo "ABBRUCH: $f fehlt." >&2; exit 1; }
done

# 2 Kerne, 4 GB RAM-Kappe (keine Reservierung; Wirt ist überbucht, siehe README), kein Swap
# (Engpass soll im Gast zum OOM führen, nicht den Swap des Wirts belasten), 24 GB Platte
# (online mit `pct resize` vergrößerbar, nicht verkleinerbar).
pct create "$ID" "$TEMPLATE" --hostname congress \
  --description "congress.taxi – Mitfahrer-Matching 40C3 (kein patchletter-Dienst). Betrieb: Repo congress.taxi, deploy/hel/" \
  --unprivileged 1 --features nesting=1,keyctl=1 --cores 2 --memory 4096 --swap 0 \
  --rootfs local-lvm:24 --ostype debian --timezone host --onboot 1 \
  --nameserver "185.12.64.1 185.12.64.2 2a01:4ff:ff00::add:1" --searchdomain sgr.ski \
  --net0 "name=eth0,bridge=vmbr1,ip=${IP}/24,gw=10.10.10.1,ip6=2a01:4f9:3b:44ad:1::80/80,gw6=2a01:4f9:3b:44ad:1::1,type=veth" \
  --ssh-public-keys /root/.ssh/authorized_keys
pct start "$ID"

echo "### Warte auf Netz im Gast"
ok=0
for _ in $(seq 1 30); do
  if pct exec "$ID" -- ping -c1 -W1 9.9.9.9 >/dev/null 2>&1; then ok=1; break; fi
  sleep 2
done
[ "$ok" = 1 ] || { echo "FEHLER: kein IPv4 nach außen" >&2; exit 1; }
pct exec "$ID" -- ping -6 -c1 -W2 2620:fe::fe >/dev/null 2>&1 && echo "IPv6 nach außen: ok" || echo "IPv6 nach außen: FEHLT (kein Abbruch)"

pct push "$ID" gast-grundlage.sh /root/gast-grundlage.sh --perms 0700
pct push "$ID" nftables.conf /root/nftables.conf.congress --perms 0600
# Abgekoppelt starten: überlebt das Ende dieser SSH-Sitzung.
pct exec "$ID" -- bash -c 'setsid nohup bash /root/gast-grundlage.sh > /root/grundlage.log 2>&1 < /dev/null &'
echo "### Grundinstallation läuft im Gast (Protokoll: pct exec $ID -- tail /root/grundlage.log)"
pct config "$ID" | grep -vE '^(ssh-public-keys|description)' | sed -E 's/hwaddr=[0-9A-Fa-f:]+/hwaddr=<mac>/'
