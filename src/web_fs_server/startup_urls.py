import socket
from typing import List


def format_http_url(host: str, port: int) -> str:
    clean_host = (host or "").strip()
    if ":" in clean_host and not clean_host.startswith("["):
        clean_host = "[%s]" % clean_host
    return "http://%s:%d/" % (clean_host, port)


def discover_local_ipv4_addresses() -> List[str]:
    addresses = set()
    addresses.add("127.0.0.1")

    try:
        host_name = socket.gethostname()
        _, _, host_ips = socket.gethostbyname_ex(host_name)
        for host_ip in host_ips:
            if host_ip:
                addresses.add(host_ip)
    except Exception:
        pass

    try:
        for item in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = item[4][0]
            if ip:
                addresses.add(ip)
    except Exception:
        pass

    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.connect(("8.8.8.8", 80))
            addresses.add(sock.getsockname()[0])
        finally:
            sock.close()
    except Exception:
        pass

    loopback = []
    regular = []
    for ip in sorted(addresses):
        if ip.startswith("127."):
            loopback.append(ip)
        else:
            regular.append(ip)
    return loopback + regular


def build_startup_urls(bind: str, port: int) -> List[str]:
    clean_bind = (bind or "").strip() or "0.0.0.0"
    if clean_bind in ("0.0.0.0", "::", "[::]"):
        return [format_http_url(ip, port) for ip in discover_local_ipv4_addresses()]
    return [format_http_url(clean_bind, port)]


# Backward-compatible alias used by server/tests
_build_startup_urls = build_startup_urls
