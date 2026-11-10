#!/bin/bash
# Zweiter Schritt auf hel (als root): Firewall des Gasts einspielen und Werkzeug installieren,
# nachdem die Grundinstallation (gast-anlegen.sh) durch ist. Wiederholbar.
set -euo pipefail
ID=220

if ! pct exec "$ID" -- grep -q "### FERTIG GRUNDLAGE" /root/grundlage.log 2>/dev/null; then
  echo "NOCH NICHT FERTIG – Protokoll:" >&2
  pct exec "$ID" -- tail -n 5 /root/grundlage.log >&2 || true
  exit 2
fi

echo "### Werkzeug (make, openssl) und Zeit"
pct exec "$ID" -- bash -c 'export DEBIAN_FRONTEND=noninteractive; apt-get -y install make openssl >/dev/null'
# Unprivilegierter LXC: Zeit kommt vom Wirt, chrony darf nicht laufen (wie CT 180)
pct exec "$ID" -- bash -c 'systemctl disable --now chrony 2>/dev/null || true'

echo "### Firewall (SSH nur vom Wirt, 8000 nur vom Rand sgr)"
pct exec "$ID" -- bash -c 'install -m 0644 /root/nftables.conf.congress /etc/nftables.conf && nft -c -f /etc/nftables.conf && nft -f /etc/nftables.conf && systemctl enable nftables >/dev/null 2>&1'
pct exec "$ID" -- bash -c 'nft list chain inet gast input | grep -E "dport|policy"; nft list chain inet gast forward | grep -E "drop|policy"'

pct exec "$ID" -- install -d -m 0755 /opt/congress
echo "### FERTIG GAST"
