#!/usr/bin/env bash
set -Eeuo pipefail

if [[ $EUID -ne 0 ]]; then
    echo "Run with: sudo bash scripts/cleanup_topology.sh"
    exit 1
fi

echo "WARNING: This removes the CloudForge namespaces and their processes."
echo "Gateway/backend processes running inside those namespaces will be terminated."
echo "Only named CloudForge resources and its exact host route/address are targeted."
echo
read -r -p 'Type CLOUDFORGE to confirm cleanup: ' confirmation

if [[ "$confirmation" != "CLOUDFORGE" ]]; then
    echo "Confirmation did not match. Nothing changed."
    exit 1
fi

echo "Removing experiment impairments..."
if command -v tc >/dev/null 2>&1; then
    for entry in "cf-r1 eth1" "cf-r2 eth0"; do
        read -r ns dev <<< "$entry"
        if ip netns exec "$ns" ip link show "$dev" >/dev/null 2>&1; then
            ip netns exec "$ns" tc qdisc del dev "$dev" root 2>/dev/null || true
        fi
    done
fi

echo "Removing exact CloudForge host route..."
ip route del 10.10.3.0/24 via 10.10.4.1 dev br-cf-db 2>/dev/null || true

echo "Deleting CloudForge namespaces..."
for ns in cf-client cf-gateway cf-r1 cf-r2 cf-app-a cf-app-b; do
    if ip netns list | awk '{print $1}' | grep -Fxq "$ns"; then
        ip netns del "$ns"
    fi
done

echo "Deleting remaining CloudForge host veth interfaces..."
for dev in \
    v-cf-cl v-cf-gw v-cf-r1f \
    v-cf-r2a v-cf-appa v-cf-appb \
    v-cf-db-host v-cf-r1t v-cf-r2t; do

    if ip link show dev "$dev" >/dev/null 2>&1; then
        ip link del dev "$dev"
    fi
done

echo "Removing CloudForge database bridge address..."
if ip link show dev br-cf-db >/dev/null 2>&1; then
    ip addr del 10.10.4.10/24 dev br-cf-db 2>/dev/null || true
fi

delete_bridge_if_safe() {
    local bridge="$1"
    local ports

    if ! ip link show dev "$bridge" >/dev/null 2>&1; then
        return
    fi

    ports="$(bridge link show master "$bridge" 2>/dev/null || true)"

    if [[ -n "$ports" ]]; then
        echo "WARNING: Keeping $bridge because it still has attached interfaces:"
        echo "$ports"
        return
    fi

    ip link set "$bridge" down
    ip link del dev "$bridge"
    echo "Deleted $bridge"
}

echo "Deleting CloudForge bridges..."
delete_bridge_if_safe br-cf-front
delete_bridge_if_safe br-cf-app
delete_bridge_if_safe br-cf-db

echo
echo "CloudForge cleanup finished."
echo "Remaining CloudForge namespaces:"
ip netns list | grep '^cf-' || echo "None"

echo
echo "Remaining CloudForge bridges:"
for bridge in br-cf-front br-cf-app br-cf-db; do
    ip link show dev "$bridge" >/dev/null 2>&1 && echo "$bridge"
done
