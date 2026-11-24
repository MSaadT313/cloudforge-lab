#!/usr/bin/env bash
set -Eeuo pipefail

if [[ $EUID -ne 0 ]]; then
    echo "Run with: sudo bash scripts/setup_topology.sh"
    exit 1
fi

for cmd in ip bridge sysctl; do
    command -v "$cmd" >/dev/null || {
        echo "Required command not found: $cmd"
        exit 1
    }
done

NAMESPACES=(
    cf-client cf-gateway cf-r1 cf-r2 cf-app-a cf-app-b
)

BRIDGES=(br-cf-front br-cf-app br-cf-db)

HOST_VETHS=(
    v-cf-cl v-cf-gw v-cf-r1f
    v-cf-r2a v-cf-appa v-cf-appb
    v-cf-db-host v-cf-r1t v-cf-r2t
)

echo "Checking for an existing CloudForge topology..."

for ns in "${NAMESPACES[@]}"; do
    if ip netns list | awk '{print $1}' | grep -Fxq "$ns"; then
        echo "Namespace already exists: $ns"
        exit 1
    fi
done

for dev in "${BRIDGES[@]}" "${HOST_VETHS[@]}"; do
    if ip link show dev "$dev" >/dev/null 2>&1; then
        echo "Interface already exists: $dev"
        exit 1
    fi
done

if ip route show 10.10.3.0/24 | grep -q .; then
    echo "Host route for 10.10.3.0/24 already exists. Refusing to overwrite."
    exit 1
fi

if ip -o addr show | grep -qE 'inet 10\.10\.4\.10/'; then
    echo "Host address 10.10.4.10 is already assigned. Refusing to continue."
    exit 1
fi

echo "Creating namespaces..."
for ns in "${NAMESPACES[@]}"; do
    ip netns add "$ns"
    ip -n "$ns" link set lo up
done

echo "Creating bridges..."
ip link add br-cf-front type bridge
ip link add br-cf-app type bridge
ip link add br-cf-db type bridge

ip link set br-cf-front up
ip link set br-cf-app up

ip link set br-cf-db address 02:cf:04:00:00:10
ip addr add 10.10.4.10/24 dev br-cf-db
ip link set br-cf-db up

create_bridged_veth() {
    local host_if="$1"
    local ns="$2"
    local peer_if="$3"
    local bridge="$4"
    local host_mac="$5"
    local peer_mac="$6"
    local address="$7"

    local temp_peer="p-${host_if#v-cf-}"
    ip link add "$host_if" type veth peer name "$temp_peer"
    ip link set "$host_if" address "$host_mac"
    ip link set "$host_if" master "$bridge"
    ip link set "$host_if" up

    ip link set "$temp_peer" netns "$ns"
    ip -n "$ns" link set "$temp_peer" name "$peer_if"
    ip -n "$ns" link set "$peer_if" address "$peer_mac"
    ip -n "$ns" addr add "$address" dev "$peer_if"
    ip -n "$ns" link set "$peer_if" up
}

echo "Connecting frontend network..."
create_bridged_veth v-cf-cl cf-client eth0 br-cf-front \
    42:7d:63:49:fe:a8 02:cf:01:00:00:10 10.10.1.10/24

create_bridged_veth v-cf-gw cf-gateway eth0 br-cf-front \
    12:10:9f:f1:64:11 02:cf:01:00:00:20 10.10.1.20/24

create_bridged_veth v-cf-r1f cf-r1 eth0 br-cf-front \
    ae:52:f4:67:e4:ab 02:cf:01:00:00:01 10.10.1.1/24

echo "Connecting application network..."
create_bridged_veth v-cf-r2a cf-r2 eth1 br-cf-app \
    56:3a:fd:2c:5f:e1 02:cf:03:00:00:01 10.10.3.1/24

create_bridged_veth v-cf-appa cf-app-a eth0 br-cf-app \
    ba:a5:4b:19:bd:13 02:cf:03:00:00:10 10.10.3.10/24

create_bridged_veth v-cf-appb cf-app-b eth0 br-cf-app \
    e2:76:d2:e9:b6:04 02:cf:03:00:00:11 10.10.3.11/24

echo "Connecting database network..."
create_bridged_veth v-cf-db-host cf-r2 eth2 br-cf-db \
    a2:5f:4d:3f:13:33 02:cf:04:00:00:01 10.10.4.1/24

echo "Creating direct router transit link..."
ip link add v-cf-r1t type veth peer name v-cf-r2t

ip link set v-cf-r1t netns cf-r1
ip link set v-cf-r2t netns cf-r2

ip -n cf-r1 link set v-cf-r1t name eth1
ip -n cf-r2 link set v-cf-r2t name eth0

ip -n cf-r1 link set eth1 address 02:cf:02:00:00:01
ip -n cf-r2 link set eth0 address 02:cf:02:00:00:02

ip -n cf-r1 addr add 10.10.2.1/30 dev eth1
ip -n cf-r2 addr add 10.10.2.2/30 dev eth0

ip -n cf-r1 link set eth1 up
ip -n cf-r2 link set eth0 up

echo "Configuring router forwarding and reverse-path filtering..."
for ns in cf-r1 cf-r2; do
    ip netns exec "$ns" sysctl -qw net.ipv4.ip_forward=1
    ip netns exec "$ns" sysctl -qw net.ipv4.conf.all.rp_filter=2
    ip netns exec "$ns" sysctl -qw net.ipv4.conf.default.rp_filter=2
done

echo "Installing static routes..."
ip -n cf-client route add default via 10.10.1.1
ip -n cf-gateway route add default via 10.10.1.1

ip -n cf-r1 route add 10.10.3.0/24 via 10.10.2.2 dev eth1
ip -n cf-r1 route add 10.10.4.0/24 via 10.10.2.2 dev eth1

ip -n cf-r2 route add 10.10.1.0/24 via 10.10.2.1 dev eth0

ip -n cf-app-a route add default via 10.10.3.1
ip -n cf-app-b route add default via 10.10.3.1

echo "Installing host route for PostgreSQL response traffic..."
ip route add 10.10.3.0/24 via 10.10.4.1 dev br-cf-db

echo
echo "CloudForge topology created."
echo
echo "Namespaces:"
ip netns list

echo
echo "CloudForge bridges:"
ip -d link show br-cf-front
ip -d link show br-cf-app
ip -d link show br-cf-db

echo
echo "Host route:"
ip route show 10.10.3.0/24

echo
echo "Router forwarding:"
ip netns exec cf-r1 sysctl net.ipv4.ip_forward
ip netns exec cf-r2 sysctl net.ipv4.ip_forward
