"""Windows IPv4 discovery using built-in commands; no administrator access needed."""
import ipaddress
import json
import re
import socket
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

MAX_HOSTS = 512
NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0


class ScanCancelled(Exception):
    pass


def check_cancel(cancel):
    if cancel.is_set():
        raise ScanCancelled()


def run_command(arguments, timeout):
    return subprocess.run(arguments, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=timeout, creationflags=NO_WINDOW)


def powershell(script, timeout=15):
    preamble = "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; $ErrorActionPreference='Stop'; "
    result = run_command(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", preamble + script], timeout)
    if result.returncode:
        raise RuntimeError("Windows network command failed: " + (result.stderr.strip() or "unknown error")[:300])
    return result.stdout.strip().lstrip("\ufeff")


def get_network_info():
    if sys.platform != "win32":
        raise RuntimeError("Network scanning in v1.0 requires Windows 10 or Windows 11.")
    script = r'''
    $c = Get-NetIPConfiguration | Where-Object {
        $_.IPv4DefaultGateway -ne $null -and $_.IPv4Address -ne $null -and
        $_.NetAdapter.Status -eq 'Up'
    } | Sort-Object { $_.NetIPv4Interface.InterfaceMetric } | Select-Object -First 1;
    if ($null -eq $c) { throw 'No active IPv4 connection with a default gateway.' };
    $a = @($c.IPv4Address)[0];
    [pscustomobject]@{
        ip=$a.IPAddress; prefix=$a.PrefixLength;
        gateway=@($c.IPv4DefaultGateway)[0].NextHop;
        interface_index=$c.InterfaceIndex; adapter=$c.InterfaceAlias;
        mac=$c.NetAdapter.MacAddress
    } | ConvertTo-Json -Compress
    '''
    value = json.loads(powershell(script))
    value["network"] = str(ipaddress.ip_network(f"{value['ip']}/{value['prefix']}", strict=False))
    value["mac"] = normalize_mac(value.get("mac", ""))
    return value


def normalize_mac(value):
    value = str(value or "").replace("-", ":").upper()
    if re.fullmatch(r"(?:[0-9A-F]{2}:){5}[0-9A-F]{2}", value) and value not in ("00:00:00:00:00:00", "FF:FF:FF:FF:FF:FF"):
        return value
    return "-"


def ping_host(ip, timeout_ms):
    try:
        result = run_command(["ping.exe", "-4", "-n", "1", "-w", str(timeout_ms), str(ip)], timeout_ms / 1000 + 2)
        # Windows can return exit code 0 for 'Destination host unreachable'.
        return result.returncode == 0 and re.search(r"\bTTL\s*=\s*\d+", result.stdout, re.I) is not None
    except subprocess.TimeoutExpired:
        return False


def get_neighbors(interface_index):
    try:
        raw = powershell(f"@(Get-NetNeighbor -AddressFamily IPv4 -InterfaceIndex {int(interface_index)} | Select-Object IPAddress,LinkLayerAddress) | ConvertTo-Json -Compress", 8)
        rows = json.loads(raw) if raw else []
        if isinstance(rows, dict):
            rows = [rows]
        return {row["IPAddress"]: normalize_mac(row.get("LinkLayerAddress")) for row in rows}
    except (RuntimeError, ValueError, subprocess.TimeoutExpired):
        return {}


def resolve_hostname(ip):
    address = str(ipaddress.IPv4Address(ip))
    try:
        result = powershell(f"Resolve-DnsName -Name '{address}' -Type PTR -DnsOnly -QuickTimeout -ErrorAction SilentlyContinue | Select-Object -First 1 -ExpandProperty NameHost", 3)
        return result.rstrip(".") or "-"
    except (RuntimeError, subprocess.TimeoutExpired):
        return "-"


def scan_network(settings, cancel, progress=lambda done, total, phase: None):
    check_cancel(cancel)
    progress(0, 0, "Detecting network")
    info = get_network_info()
    network = ipaddress.ip_network(info["network"])
    count = network.num_addresses - (2 if network.prefixlen < 31 else 0)
    if count > MAX_HOSTS:
        raise RuntimeError(f"The selected network ({network}) has {count:,} hosts. v1.0 supports at most {MAX_HOSTS} hosts per scan.")
    check_cancel(cancel)
    hosts = [str(ip) for ip in network.hosts()]
    online = {info["ip"]}
    with ThreadPoolExecutor(max_workers=32) as executor:
        jobs = {executor.submit(ping_host, ip, settings["ping_timeout_ms"]): ip for ip in hosts}
        try:
            for completed, job in enumerate(as_completed(jobs), 1):
                check_cancel(cancel)
                if job.result():
                    online.add(jobs[job])
                progress(completed, len(hosts), "Scanning devices")
        finally:
            if cancel.is_set():
                for job in jobs:
                    job.cancel()
    check_cancel(cancel)
    neighbors = get_neighbors(info["interface_index"])
    check_cancel(cancel)
    hostnames = {info["ip"]: socket.gethostname()}
    if settings["resolve_hostnames"]:
        targets = sorted(online - {info["ip"]})
        with ThreadPoolExecutor(max_workers=8) as executor:
            jobs = {executor.submit(resolve_hostname, ip): ip for ip in targets}
            try:
                for completed, job in enumerate(as_completed(jobs), 1):
                    check_cancel(cancel)
                    hostnames[jobs[job]] = job.result()
                    progress(completed, len(targets), "Resolving names")
            finally:
                if cancel.is_set():
                    for job in jobs:
                        job.cancel()
    check_cancel(cancel)
    devices = []
    for ip in sorted(online, key=ipaddress.ip_address):
        local = ip == info["ip"]
        devices.append({"ip": ip, "device_name": "This PC" if local else ("Gateway" if ip == info["gateway"] else "Unknown"),
                        "hostname": hostnames.get(ip, "-"), "mac": info["mac"] if local else neighbors.get(ip, "-")})
    return {"info": info, "devices": devices, "timestamp": datetime.now().astimezone().isoformat(timespec="seconds")}
