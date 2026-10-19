#!/usr/bin/env python3
"""
red_lab.py - Monta la topología de la práctica con qemu-kvm / virsh.

    servidor
       |  10.0.0.0/24
       R1
       |  10.0.1.0/24
       R2
     /    \\
10.0.2.0/24  10.0.3.0/24
   R3          R4
  /  \\        /  \\
RED1  RED2  RED3  RED4
.1.0  .2.0  .3.0  .4.0  (192.168.X.0/24)

Cada segmento es una red aislada de libvirt (un bridge en el host, sin IP),
que hace de "switch". Cada RED va en su propio switch, así que queda
separada como si fuese su propia VLAN.

Regla de direccionamiento: el router siempre tiene la 1ª IP de la red (.1).
En los enlaces entre routers, el de arriba tiene la .1 y el de abajo la .2.

Lo que hace:
  1. Crea las redes en libvirt (persistentes, con autostart).
  2. Añade a cada VM sus interfaces, con una MAC fija.
  3. Genera para cada VM:
       - /etc/network/interfaces   (IPs, gateway y rutas estáticas)
       - /etc/systemd/network/*.link (fija el nombre eth0, eth1... según la MAC)
       - /etc/sysctl.d/99-router.conf (ip_forward=1, solo en routers)
  4. Opcional (--push): copia esos ficheros dentro de la VM con virt-copy-in
     (la VM tiene que estar apagada).

Uso:
  sudo python3 red_lab.py --dry-run     # solo muestra lo que haría
  sudo python3 red_lab.py               # crea redes, conecta y genera ficheros
  sudo python3 red_lab.py --push        # además los copia dentro de las VMs
"""

import argparse
import ipaddress
import subprocess
import sys
import tempfile
from pathlib import Path

# ---------------------------------------------------------------------------
# TOPOLOGÍA  (modifica aquí nombres de VMs o redes si lo necesitas)
# ---------------------------------------------------------------------------

SEGMENTOS = {
    # nombre       red
    "srv-r1": "10.0.0.0/24",     # servidor <-> R1
    "r1-r2":  "10.0.1.0/24",     # R1 <-> R2
    "r2-r3":  "10.0.2.0/24",     # R2 <-> R3
    "r2-r4":  "10.0.3.0/24",     # R2 <-> R4
    "red1":   "192.168.1.0/24",
    "red2":   "192.168.2.0/24",
    "red3":   "192.168.3.0/24",
    "red4":   "192.168.4.0/24",
}


def ip(segmento, n):
    """Devuelve la IP número n del segmento (n=1 -> primera IP)."""
    return str(ipaddress.ip_network(SEGMENTOS[segmento])[n])


def red(segmento):
    return SEGMENTOS[segmento]


# Las claves son los nombres de los dominios en virsh (virsh list --all).
# ifaces: lista de (segmento, nº de host). El orden marca eth0, eth1, ...
# rutas:  lista de (destino, via)
MAQUINAS = {
    "servidor": {
        "router": False,
        "ifaces": [("srv-r1", 2)],
        "gateway": ip("srv-r1", 1),
        "rutas": [],
    },
    "r1": {
        "router": True,
        "ifaces": [("srv-r1", 1), ("r1-r2", 1)],
        "gateway": None,
        "rutas": [
            (red("r2-r3"), ip("r1-r2", 2)),
            (red("r2-r4"), ip("r1-r2", 2)),
            (red("red1"), ip("r1-r2", 2)),
            (red("red2"), ip("r1-r2", 2)),
            (red("red3"), ip("r1-r2", 2)),
            (red("red4"), ip("r1-r2", 2)),
        ],
    },
    "r2": {
        "router": True,
        "ifaces": [("r1-r2", 2), ("r2-r3", 1), ("r2-r4", 1)],
        "gateway": ip("r1-r2", 1),
        "rutas": [
            (red("red1"), ip("r2-r3", 2)),
            (red("red2"), ip("r2-r3", 2)),
            (red("red3"), ip("r2-r4", 2)),
            (red("red4"), ip("r2-r4", 2)),
        ],
    },
    "r3": {
        "router": True,
        "ifaces": [("r2-r3", 2), ("red1", 1), ("red2", 1)],
        "gateway": ip("r2-r3", 1),
        "rutas": [],
    },
    "r4": {
        "router": True,
        "ifaces": [("r2-r4", 2), ("red3", 1), ("red4", 1)],
        "gateway": ip("r2-r4", 1),
        "rutas": [],
    },
    # Un equipo de prueba en cada red (si la VM no existe, se salta).
    "pc1": {"router": False, "ifaces": [("red1", 10)], "gateway": ip("red1", 1), "rutas": []},
    "pc2": {"router": False, "ifaces": [("red2", 10)], "gateway": ip("red2", 1), "rutas": []},
    "pc3": {"router": False, "ifaces": [("red3", 10)], "gateway": ip("red3", 1), "rutas": []},
    "pc4": {"router": False, "ifaces": [("red4", 10)], "gateway": ip("red4", 1), "rutas": []},
}

PREFIJO_RED = "lab-"   # nombre de la red en libvirt: lab-red1, lab-r1-r2...
PREFIJO_BR = "br-"     # nombre del bridge en el host: br-red1, br-r1-r2...

# ---------------------------------------------------------------------------
# UTILIDADES
# ---------------------------------------------------------------------------

ARGS = None


def virsh(*args):
    return ["virsh", "-c", ARGS.connect, *args]


def ejecutar(cmd, comprobar=True):
    print("  $", " ".join(cmd))
    if ARGS.dry_run:
        return ""
    r = subprocess.run(cmd, capture_output=True, text=True)
    if comprobar and r.returncode != 0:
        print(f"    ERROR: {r.stderr.strip()}")
    return r.stdout


def consulta(cmd):
    """Ejecuta un comando de solo lectura (también en dry-run)."""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True)
    except FileNotFoundError:
        return None
    return r.stdout if r.returncode == 0 else None


def mac_para(n_vm, n_iface):
    return f"52:54:00:aa:{n_vm:02x}:{n_iface:02x}"


def estado_vm(nombre):
    salida = consulta(virsh("domstate", nombre))
    return salida.strip() if salida is not None else None


# ---------------------------------------------------------------------------
# 1. REDES EN LIBVIRT
# ---------------------------------------------------------------------------

def crear_redes():
    print("\n== 1. Creando redes (switches) en libvirt ==")
    for seg in SEGMENTOS:
        nombre = PREFIJO_RED + seg
        bridge = (PREFIJO_BR + seg)[:15]   # límite de Linux: 15 caracteres
        if consulta(virsh("net-info", nombre)) is not None:
            print(f"  [ok] {nombre} ya existe")
            continue
        xml = (
            "<network>\n"
            f"  <name>{nombre}</name>\n"
            f"  <bridge name='{bridge}' stp='off' delay='0'/>\n"
            "</network>\n"
        )
        with tempfile.NamedTemporaryFile("w", suffix=".xml", delete=False) as f:
            f.write(xml)
            ruta = f.name
        ejecutar(virsh("net-define", ruta))
        ejecutar(virsh("net-start", nombre))
        ejecutar(virsh("net-autostart", nombre))


# ---------------------------------------------------------------------------
# 2. INTERFACES EN LAS VMs
# ---------------------------------------------------------------------------

def conectar_vms():
    print("\n== 2. Añadiendo interfaces a las VMs ==")
    for n_vm, (vm, datos) in enumerate(MAQUINAS.items(), start=1):
        estado = estado_vm(vm)
        if estado is None and not ARGS.dry_run:
            print(f"  [!] La VM '{vm}' no existe, me la salto")
            continue
        actuales = (consulta(virsh("domiflist", vm)) or "").lower()
        for n_if, (seg, _) in enumerate(datos["ifaces"]):
            mac = mac_para(n_vm, n_if)
            if mac in actuales:
                print(f"  [ok] {vm}: eth{n_if} ({mac}) ya conectada a {seg}")
                continue
            cmd = virsh("attach-interface", "--domain", vm, "--type", "network",
                        "--source", PREFIJO_RED + seg, "--model", "virtio",
                        "--mac", mac, "--config")
            if estado == "running":
                cmd.append("--live")
            ejecutar(cmd)


# ---------------------------------------------------------------------------
# 3. FICHEROS DE CONFIGURACIÓN
# ---------------------------------------------------------------------------

def generar_interfaces(vm, datos):
    lineas = [
        f"# /etc/network/interfaces de {vm} - generado por red_lab.py",
        "",
        "auto lo",
        "iface lo inet loopback",
    ]
    for n_if, (seg, host) in enumerate(datos["ifaces"]):
        net = ipaddress.ip_network(SEGMENTOS[seg])
        nombre = f"eth{n_if}"
        lineas += [
            "",
            f"# {seg} ({net})",
            f"auto {nombre}",
            f"iface {nombre} inet static",
            f"    address {net[host]}",
            f"    netmask {net.netmask}",
        ]
        gw = datos["gateway"]
        if gw and ipaddress.ip_address(gw) in net:
            lineas.append(f"    gateway {gw}")
        for destino, via in datos["rutas"]:
            if ipaddress.ip_address(via) in net:
                lineas.append(f"    post-up ip route add {destino} via {via}")
    return "\n".join(lineas) + "\n"


def generar_ficheros():
    print(f"\n== 3. Generando ficheros en {ARGS.salida}/ ==")
    base = Path(ARGS.salida)
    for n_vm, (vm, datos) in enumerate(MAQUINAS.items(), start=1):
        etc = base / vm / "etc"
        (etc / "network").mkdir(parents=True, exist_ok=True)
        (etc / "network" / "interfaces").write_text(generar_interfaces(vm, datos))

        (etc / "systemd" / "network").mkdir(parents=True, exist_ok=True)
        for n_if, _ in enumerate(datos["ifaces"]):
            (etc / "systemd" / "network" / f"10-eth{n_if}.link").write_text(
                "[Match]\n"
                f"MACAddress={mac_para(n_vm, n_if)}\n\n"
                "[Link]\n"
                f"Name=eth{n_if}\n"
            )

        if datos["router"]:
            (etc / "sysctl.d").mkdir(parents=True, exist_ok=True)
            (etc / "sysctl.d" / "99-router.conf").write_text("net.ipv4.ip_forward=1\n")

        print(f"  [ok] {vm}")


# ---------------------------------------------------------------------------
# 4. COPIAR DENTRO DE LAS VMs (opcional)
# ---------------------------------------------------------------------------

def copiar_a_vms():
    print("\n== 4. Copiando la configuración dentro de las VMs ==")
    for vm in MAQUINAS:
        estado = estado_vm(vm)
        if estado is None and not ARGS.dry_run:
            print(f"  [!] La VM '{vm}' no existe, me la salto")
            continue
        if estado == "running":
            print(f"  [!] {vm} está encendida: apágala (virsh shutdown {vm}) y repite --push")
            continue
        ejecutar(["virt-copy-in", "-c", ARGS.connect, "-d", vm,
                  str(Path(ARGS.salida) / vm / "etc"), "/"])


# ---------------------------------------------------------------------------

def resumen():
    print("\n== Plan de direccionamiento ==")
    for vm, datos in MAQUINAS.items():
        ips = ", ".join(
            f"eth{i}={ipaddress.ip_network(SEGMENTOS[s])[h]}/{ipaddress.ip_network(SEGMENTOS[s]).prefixlen}"
            for i, (s, h) in enumerate(datos["ifaces"])
        )
        gw = f"  gw {datos['gateway']}" if datos["gateway"] else ""
        print(f"  {vm:9} {ips}{gw}")


def main():
    global ARGS
    p = argparse.ArgumentParser(description="Monta la red de la práctica con virsh")
    p.add_argument("--dry-run", action="store_true", help="solo mostrar los comandos")
    p.add_argument("--push", action="store_true",
                   help="copiar la config dentro de las VMs (deben estar apagadas)")
    p.add_argument("--solo-ficheros", action="store_true",
                   help="solo generar los ficheros, sin tocar libvirt")
    p.add_argument("--salida", default="config_vms", help="carpeta de salida")
    p.add_argument("--connect", default="qemu:///system", help="URI de libvirt")
    ARGS = p.parse_args()

    if ARGS.dry_run:
        print("*** MODO SIMULACIÓN: no se ejecuta nada en libvirt ***")

    resumen()
    if not ARGS.solo_ficheros:
        crear_redes()
        conectar_vms()
    generar_ficheros()
    if ARGS.push:
        copiar_a_vms()

    print("\nListo. Siguientes pasos:")
    if not ARGS.push:
        print(f"  - Copia {ARGS.salida}/<vm>/etc/... dentro de cada VM, o vuelve a")
        print("    ejecutar con --push con las VMs apagadas.")
    print("  - Arranca (o reinicia) cada VM para que se apliquen los nombres eth0, eth1...")
    print("  - Prueba desde pc1:  ping 192.168.4.10   y   ping 10.0.0.2")


if __name__ == "__main__":
    sys.exit(main())
