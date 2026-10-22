#!/usr/bin/env python3
"""
red_lab.py - Crea y configura la topología de la práctica con qemu-kvm / virsh.

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
 pc1   pc2   pc3   pc4

Cada segmento es una red aislada de libvirt (un bridge en el host, sin IP),
que hace de "switch". Cada RED va en su propio switch, así que queda
separada como si fuese su propia VLAN.

Regla de direccionamiento: el router siempre tiene la 1ª IP de la red (.1).
En los enlaces entre routers, el de arriba tiene la .1 y el de abajo la .2.

Lo que hace:
  1. Crea las redes en libvirt (persistentes, con autostart).
  2. Genera para cada VM, en config_vms/<vm>/:
       - etc/network/interfaces        (IPs, gateway y rutas estáticas)
       - etc/systemd/network/*.link    (fija los nombres eth0, eth1... por MAC)
       - etc/sysctl.d/99-router.conf   (ip_forward=1, solo en routers)
       - etc/hostname y etc/hosts      (puedes hacer "ping r3" por nombre)
       - consola serie activada        (para usar "virsh console <vm>")
       - <vm>.xml                      (definición de la VM para virsh)
  3. Para cada VM:
       - crea su disco como overlay qcow2 de una imagen base
       - mete dentro la configuración del paso 2 (virt-copy-in)
       - virsh define <vm>.xml
       - virsh start <vm>

Requisitos en el host:
  sudo apt install qemu-kvm libvirt-daemon-system libvirt-clients qemu-utils libguestfs-tools

  La imagen base debe ser un Debian (o similar) ya instalado con ifupdown,
  que ninguna VM esté usando (haz una copia: cp mi-debian.qcow2 base.qcow2).
  Las VMs se crean como overlays, así que NO modifiques la base después.

Uso:
  sudo python3 red_lab.py --base /var/lib/libvirt/images/base.qcow2 --dry-run
  sudo python3 red_lab.py --base /var/lib/libvirt/images/base.qcow2
  sudo python3 red_lab.py --borrar          # elimina VMs, discos y redes del lab
"""

import argparse
import ipaddress
import os
import shutil
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


# Las claves son los nombres de las VMs en virsh.
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
    # Un equipo de prueba en cada red.
    "pc1": {"router": False, "ifaces": [("red1", 10)], "gateway": ip("red1", 1), "rutas": []},
    "pc2": {"router": False, "ifaces": [("red2", 10)], "gateway": ip("red2", 1), "rutas": []},
    "pc3": {"router": False, "ifaces": [("red3", 10)], "gateway": ip("red3", 1), "rutas": []},
    "pc4": {"router": False, "ifaces": [("red4", 10)], "gateway": ip("red4", 1), "rutas": []},
}

PREFIJO_RED = "lab-"    # nombre de la red en libvirt: lab-red1, lab-r1-r2...
PREFIJO_BR = "br-"      # nombre del bridge en el host: br-red1, br-r1-r2...
PREFIJO_DISCO = "lab-"  # nombre del disco: lab-r1.qcow2...

# ---------------------------------------------------------------------------
# UTILIDADES
# ---------------------------------------------------------------------------

ARGS = None


def virsh(*args):
    return ["virsh", "-c", ARGS.connect, *args]


def ejecutar(cmd, mostrar_error=True):
    """Ejecuta un comando que cambia algo. Devuelve True si fue bien."""
    print("  $", " ".join(cmd))
    if ARGS.dry_run:
        return True
    entorno = dict(os.environ)
    entorno.setdefault("LIBGUESTFS_BACKEND", "direct")
    r = subprocess.run(cmd, capture_output=True, text=True, env=entorno)
    if r.returncode != 0 and mostrar_error:
        print(f"    ERROR: {(r.stderr or r.stdout).strip()}")
    return r.returncode == 0


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
    """'running', 'shut off'... o None si la VM no existe."""
    salida = consulta(virsh("domstate", nombre))
    return salida.strip() if salida is not None else None


def red_existe(nombre):
    return consulta(virsh("net-info", nombre)) is not None


def ruta_disco(vm):
    return Path(ARGS.dir_discos) / f"{PREFIJO_DISCO}{vm}.qcow2"


def escribir_temporal(texto, sufijo):
    with tempfile.NamedTemporaryFile("w", suffix=sufijo, delete=False) as f:
        f.write(texto)
        return f.name


# ---------------------------------------------------------------------------
# 1. REDES EN LIBVIRT
# ---------------------------------------------------------------------------

def crear_redes():
    print("\n== 1. Creando redes (switches) en libvirt ==")
    for seg in SEGMENTOS:
        nombre = PREFIJO_RED + seg
        bridge = (PREFIJO_BR + seg)[:15]   # límite de Linux: 15 caracteres
        if red_existe(nombre):
            print(f"  [ok] {nombre} ya existe")
            ejecutar(virsh("net-start", nombre), mostrar_error=False)  # por si estaba parada
            continue
        xml = (
            "<network>\n"
            f"  <name>{nombre}</name>\n"
            f"  <bridge name='{bridge}' stp='off' delay='0'/>\n"
            "</network>\n"
        )
        ejecutar(virsh("net-define", escribir_temporal(xml, ".xml")))
        ejecutar(virsh("net-start", nombre))
        ejecutar(virsh("net-autostart", nombre))


# ---------------------------------------------------------------------------
# 2. FICHEROS DE CONFIGURACIÓN
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


def generar_hosts(vm):
    lineas = [
        "127.0.0.1\tlocalhost",
        f"127.0.1.1\t{vm}",
        "",
        "# Equipos del laboratorio",
    ]
    for otra, datos in MAQUINAS.items():
        seg, host = datos["ifaces"][0]
        lineas.append(f"{ipaddress.ip_network(SEGMENTOS[seg])[host]}\t{otra}")
    lineas += [
        "",
        "::1\tlocalhost ip6-localhost ip6-loopback",
        "ff02::1\tip6-allnodes",
        "ff02::2\tip6-allrouters",
    ]
    return "\n".join(lineas) + "\n"


def generar_xml_vm(n_vm, vm, datos):
    interfaces = ""
    for n_if, (seg, _) in enumerate(datos["ifaces"]):
        interfaces += (
            "    <interface type='network'>\n"
            f"      <mac address='{mac_para(n_vm, n_if)}'/>\n"
            f"      <source network='{PREFIJO_RED + seg}'/>\n"
            "      <model type='virtio'/>\n"
            "    </interface>\n"
        )
    firmware = " firmware='efi'" if ARGS.uefi else ""
    return f"""<domain type='kvm'>
  <name>{vm}</name>
  <memory unit='MiB'>{ARGS.ram}</memory>
  <vcpu>{ARGS.cpus}</vcpu>
  <os{firmware}>
    <type arch='x86_64' machine='q35'>hvm</type>
    <boot dev='hd'/>
  </os>
  <features>
    <acpi/>
    <apic/>
  </features>
  <cpu mode='host-passthrough'/>
  <on_poweroff>destroy</on_poweroff>
  <on_reboot>restart</on_reboot>
  <on_crash>destroy</on_crash>
  <devices>
    <disk type='file' device='disk'>
      <driver name='qemu' type='qcow2'/>
      <source file='{ruta_disco(vm)}'/>
      <target dev='vda' bus='virtio'/>
    </disk>
{interfaces}    <serial type='pty'>
      <target port='0'/>
    </serial>
    <console type='pty'>
      <target type='serial' port='0'/>
    </console>
    <graphics type='vnc' autoport='yes' listen='127.0.0.1'/>
    <video>
      <model type='vga'/>
    </video>
  </devices>
</domain>
"""


def generar_ficheros():
    print(f"\n== 2. Generando ficheros en {ARGS.salida}/ ==")
    base = Path(ARGS.salida)
    for n_vm, (vm, datos) in enumerate(MAQUINAS.items(), start=1):
        dir_vm = base / vm
        etc = dir_vm / "etc"

        (etc / "network").mkdir(parents=True, exist_ok=True)
        (etc / "network" / "interfaces").write_text(generar_interfaces(vm, datos))

        (etc / "hostname").write_text(vm + "\n")
        (etc / "hosts").write_text(generar_hosts(vm))

        (etc / "systemd" / "network").mkdir(parents=True, exist_ok=True)
        for n_if, _ in enumerate(datos["ifaces"]):
            (etc / "systemd" / "network" / f"10-eth{n_if}.link").write_text(
                "[Match]\n"
                f"MACAddress={mac_para(n_vm, n_if)}\n\n"
                "[Link]\n"
                f"Name=eth{n_if}\n"
            )

        # Activa un login en la consola serie para que funcione "virsh console"
        wants = etc / "systemd" / "system" / "getty.target.wants"
        wants.mkdir(parents=True, exist_ok=True)
        enlace = wants / "serial-getty@ttyS0.service"
        if not enlace.is_symlink():
            enlace.symlink_to("/lib/systemd/system/serial-getty@.service")

        if datos["router"]:
            (etc / "sysctl.d").mkdir(parents=True, exist_ok=True)
            (etc / "sysctl.d" / "99-router.conf").write_text("net.ipv4.ip_forward=1\n")

        (dir_vm / f"{vm}.xml").write_text(generar_xml_vm(n_vm, vm, datos))
        print(f"  [ok] {vm}")


# ---------------------------------------------------------------------------
# 3. DISCOS, DEFINICIÓN Y ARRANQUE DE LAS VMs
# ---------------------------------------------------------------------------

def comprobar_requisitos():
    if ARGS.dry_run:
        return True
    ok = True
    for prog in ("virsh", "qemu-img", "virt-copy-in"):
        if shutil.which(prog) is None:
            print(f"  [!] Falta '{prog}'. Instala: sudo apt install libvirt-clients "
                  "qemu-utils libguestfs-tools")
            ok = False
    if not ARGS.base:
        print("  [!] Indica la imagen base con --base /ruta/imagen.qcow2")
        ok = False
    elif not Path(ARGS.base).is_file():
        print(f"  [!] No existe la imagen base: {ARGS.base}")
        ok = False
    return ok


def crear_vms():
    print("\n== 3. Creando y arrancando las VMs ==")
    if not comprobar_requisitos():
        print("  No se crean las VMs. Corrige lo anterior y vuelve a ejecutar.")
        return False

    base = ARGS.base or "/ruta/a/base.qcow2"
    for vm in MAQUINAS:
        estado = estado_vm(vm)
        if estado is not None:
            print(f"  [ok] {vm} ya existe ({estado}), no la toco")
            if estado != "running":
                ejecutar(virsh("start", vm))
            continue

        disco = ruta_disco(vm)
        print(f"  -- {vm}")

        # 3a. Disco: overlay sobre la imagen base
        if disco.exists() and not ARGS.dry_run:
            print(f"     el disco {disco} ya existe, lo reutilizo")
        else:
            if not ejecutar(["qemu-img", "create", "-f", "qcow2",
                             "-b", str(Path(base).resolve()), "-F", "qcow2", str(disco)]):
                continue

        # 3b. Meter la configuración dentro del disco (la VM aún no está encendida)
        if not ejecutar(["virt-copy-in", "-a", str(disco),
                         str(Path(ARGS.salida) / vm / "etc"), "/"]):
            print(f"     [!] No se pudo copiar la config. Hazlo a mano dentro de {vm}.")

        # 3c. Definir y arrancar
        xml = Path(ARGS.salida) / vm / f"{vm}.xml"
        if ejecutar(virsh("define", str(xml))):
            ejecutar(virsh("start", vm))
    return True


# ---------------------------------------------------------------------------
# BORRADO DEL LABORATORIO
# ---------------------------------------------------------------------------

def borrar_todo():
    print("Se van a eliminar estas VMs (con sus discos) y redes:")
    print("  VMs:  ", ", ".join(MAQUINAS))
    print("  Redes:", ", ".join(PREFIJO_RED + s for s in SEGMENTOS))
    if not ARGS.dry_run and not ARGS.si:
        if input("¿Seguro? (s/N) ").strip().lower() != "s":
            print("Cancelado.")
            return

    print("\n== Borrando VMs ==")
    for vm in MAQUINAS:
        estado = estado_vm(vm)
        if estado is None and not ARGS.dry_run:
            print(f"  [-] {vm} no existe")
        else:
            if estado == "running" or ARGS.dry_run:
                ejecutar(virsh("destroy", vm), mostrar_error=False)
            if not ejecutar(virsh("undefine", vm), mostrar_error=False):
                ejecutar(virsh("undefine", vm, "--nvram"))
        disco = ruta_disco(vm)
        if disco.exists() or ARGS.dry_run:
            print(f"  $ rm {disco}")
            if not ARGS.dry_run:
                disco.unlink()

    print("\n== Borrando redes ==")
    for seg in SEGMENTOS:
        nombre = PREFIJO_RED + seg
        if red_existe(nombre) or ARGS.dry_run:
            ejecutar(virsh("net-destroy", nombre), mostrar_error=False)
            ejecutar(virsh("net-undefine", nombre))


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
    p = argparse.ArgumentParser(description="Crea la red de la práctica con virsh")
    p.add_argument("--base", help="imagen qcow2 base (Debian instalado con ifupdown)")
    p.add_argument("--dir-discos", default="/var/lib/libvirt/images",
                   help="carpeta donde se crean los discos de las VMs")
    p.add_argument("--ram", type=int, default=512, help="RAM por VM en MiB (512)")
    p.add_argument("--cpus", type=int, default=1, help="vCPUs por VM (1)")
    p.add_argument("--uefi", action="store_true",
                   help="arrancar con UEFI (si la imagen base se instaló así)")
    p.add_argument("--salida", default="config_vms", help="carpeta de ficheros generados")
    p.add_argument("--connect", default="qemu:///system", help="URI de libvirt")
    p.add_argument("--solo-ficheros", action="store_true",
                   help="solo generar los ficheros, sin tocar libvirt")
    p.add_argument("--dry-run", action="store_true", help="solo mostrar los comandos")
    p.add_argument("--borrar", action="store_true",
                   help="eliminar las VMs, sus discos y las redes del laboratorio")
    p.add_argument("--si", action="store_true", help="no pedir confirmación al borrar")
    ARGS = p.parse_args()

    if ARGS.dry_run:
        print("*** MODO SIMULACIÓN: no se ejecuta nada en libvirt ***")

    if ARGS.borrar:
        borrar_todo()
        return 0

    resumen()
    generar_ficheros()
    if ARGS.solo_ficheros:
        return 0

    crear_redes()
    if not crear_vms():
        return 1

    print("\nListo. Comprueba con:")
    print("  virsh list --all")
    print("  virsh console pc1        (salir con Ctrl + ])")
    print("  desde pc1:  ping pc4   ping servidor   traceroute 192.168.4.10")
    return 0


if __name__ == "__main__":
    sys.exit(main())
