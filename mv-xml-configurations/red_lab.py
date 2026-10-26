#!/usr/bin/env python3
"""
Crea y arranca las máquinas virtuales de la práctica a partir de una imagen base
y las conecta según esta topología:

    servidor
       |  10.0.0.0/24        (br-srv-r1)
       R1
       |  10.0.1.0/24        (br-r1-r2)
       R2
     /    \\
10.0.2.0/24  10.0.3.0/24     (br-r2-r3, br-r2-r4)
   R3          R4
  /  \\        /  \\
RED1  RED2  RED3  RED4       (br-red1 ... br-red4)
 pc1   pc2   pc3   pc4       192.168.1.0/24 ... 192.168.4.0/24

El router siempre tiene la 1ª IP de cada red (.1).

Para cada máquina:
  1. Crea el disco /var/lib/libvirt/images/agr-<nombre>.qcow2 con qemu-img,
     usando agr-vm-base.qcow2 como imagen padre (backing file).
  2. Genera su XML: plantilla-<nombre>.xml (una interfaz por cada red).
  3. La define y la arranca con virsh.
  4. Genera configurar-<nombre>.sh con su configuración de red, para
     pegarlo dentro de la VM (virsh console <nombre>).

Uso:
    sudo python3 red_lab.py              # crea todas las máquinas
    sudo python3 red_lab.py r1 pc1       # crea solo esas
"""

import ipaddress
import os
import subprocess
import sys

DIR_IMAGENES = "/var/lib/libvirt/images"
IMAGEN_BASE = os.path.join(DIR_IMAGENES, "agr-vm-base.qcow2")

# Las interfaces se colocan en las ranuras PCI 3, 4, 5... de la VM, así que
# dentro de Debian se llaman ens3, ens4, ens5... (compruébalo con "ip a").
PRIMERA_RANURA = 3
PREFIJO_IFACE = "ens"

# ---------------------------------------------------------------------------
# TOPOLOGÍA
# ---------------------------------------------------------------------------

# nombre del bridge en el host -> red
REDES = {
    "br-srv-r1": "10.0.0.0/24",
    "br-r1-r2":  "10.0.1.0/24",
    "br-r2-r3":  "10.0.2.0/24",
    "br-r2-r4":  "10.0.3.0/24",
    "br-red1":   "192.168.1.0/24",
    "br-red2":   "192.168.2.0/24",
    "br-red3":   "192.168.3.0/24",
    "br-red4":   "192.168.4.0/24",
}


def ip(bridge, n):
    """IP número n de la red (n=1 -> primera IP)."""
    return str(ipaddress.ip_network(REDES[bridge])[n])


# ifaces: lista de (bridge, nº de host). La 1ª es ens3, la 2ª ens4...
# rutas:  lista de (red destino, via)
MAQUINAS = {
    "servidor": {"router": False, "ifaces": [("br-srv-r1", 2)],
                 "gateway": ip("br-srv-r1", 1), "rutas": []},
    "r1": {"router": True, "ifaces": [("br-srv-r1", 1), ("br-r1-r2", 1)],
           "gateway": None,
           "rutas": [(REDES[b], ip("br-r1-r2", 2)) for b in
                     ("br-r2-r3", "br-r2-r4", "br-red1", "br-red2", "br-red3", "br-red4")]},
    "r2": {"router": True, "ifaces": [("br-r1-r2", 2), ("br-r2-r3", 1), ("br-r2-r4", 1)],
           "gateway": ip("br-r1-r2", 1),
           "rutas": [(REDES["br-red1"], ip("br-r2-r3", 2)),
                     (REDES["br-red2"], ip("br-r2-r3", 2)),
                     (REDES["br-red3"], ip("br-r2-r4", 2)),
                     (REDES["br-red4"], ip("br-r2-r4", 2))]},
    "r3": {"router": True, "ifaces": [("br-r2-r3", 2), ("br-red1", 1), ("br-red2", 1)],
           "gateway": ip("br-r2-r3", 1), "rutas": []},
    "r4": {"router": True, "ifaces": [("br-r2-r4", 2), ("br-red3", 1), ("br-red4", 1)],
           "gateway": ip("br-r2-r4", 1), "rutas": []},
    "pc1": {"router": False, "ifaces": [("br-red1", 10)], "gateway": ip("br-red1", 1), "rutas": []},
    "pc2": {"router": False, "ifaces": [("br-red2", 10)], "gateway": ip("br-red2", 1), "rutas": []},
    "pc3": {"router": False, "ifaces": [("br-red3", 10)], "gateway": ip("br-red3", 1), "rutas": []},
    "pc4": {"router": False, "ifaces": [("br-red4", 10)], "gateway": ip("br-red4", 1), "rutas": []},
}

PLANTILLA_XML = """<domain type="kvm">
  <name>{nombre}</name>
  <memory>1048576</memory>
  <currentMemory>1048576</currentMemory>
  <vcpu>2</vcpu>
  <os>
    <type arch="x86_64">hvm</type>
    <boot dev="hd"/>
  </os>
  <features>
    <acpi/>
    <apic/>
    <pae/>
  </features>
  <clock offset="utc"/>
  <on_poweroff>destroy</on_poweroff>
  <on_reboot>restart</on_reboot>
  <on_crash>restart</on_crash>
  <devices>
    <emulator>/usr/bin/qemu-system-x86_64</emulator>
    <disk type="file" device="disk">
      <driver name="qemu" type="qcow2"/>
      <source file="{disco}"/>
      <target dev="vda" bus="virtio"/>
    </disk>
{interfaces}    <serial type="pty">
      <target port="0"/>
    </serial>
    <console type="pty">
      <target type="serial" port="0"/>
    </console>
    <input type="mouse" bus="ps2"/>
    <graphics type="vnc" port="-1" autoport="yes"/>
  </devices>
</domain>
"""

PLANTILLA_INTERFAZ = """    <interface type="bridge">
      <mac address="{mac}"/>
      <source bridge="{bridge}"/>
      <model type="virtio"/>
      <address type="pci" domain="0x0000" bus="0x00" slot="{ranura:#04x}" function="0x0"/>
    </interface>
"""


# ---------------------------------------------------------------------------
# UTILIDADES
# ---------------------------------------------------------------------------

def ejecutar(cmd, ignorar_error=False):
    """Ejecuta un comando y muestra su salida."""
    print("$ " + " ".join(cmd))
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.stdout.strip():
        print(res.stdout.strip())
    if res.returncode != 0 and not ignorar_error:
        print("ERROR: " + res.stderr.strip())
        sys.exit(1)
    return res


def dominio_existe(nombre):
    res = subprocess.run(["virsh", "dominfo", nombre], capture_output=True, text=True)
    return res.returncode == 0


def bridge_existe(nombre):
    res = subprocess.run(["ip", "link", "show", nombre], capture_output=True, text=True)
    return res.returncode == 0


def mac_para(n_vm, n_iface):
    return f"52:54:00:aa:{n_vm:02x}:{n_iface:02x}"


# ---------------------------------------------------------------------------
# PASOS
# ---------------------------------------------------------------------------

def crear_bridges():
    """Un bridge (switch) en el host por cada red, sin IP."""
    print("\n=== Bridges en el host ===")
    for bridge in REDES:
        if bridge_existe(bridge):
            print(f"{bridge} ya existe")
        else:
            ejecutar(["ip", "link", "add", "name", bridge, "type", "bridge"])
        ejecutar(["ip", "link", "set", bridge, "up"])


def eliminar_dominio(nombre):
    """Si el dominio ya existe, lo apaga y lo borra para recrearlo limpio."""
    if dominio_existe(nombre):
        print(f"El dominio {nombre} ya existe, se elimina para recrearlo")
        ejecutar(["virsh", "destroy", nombre], ignorar_error=True)  # falla si ya está apagado
        ejecutar(["virsh", "undefine", nombre])


def crear_disco(disco):
    """Crea el disco de la VM tomando la imagen base como padre."""
    if os.path.exists(disco):
        os.remove(disco)
    ejecutar(["qemu-img", "create", "-f", "qcow2",
              "-b", IMAGEN_BASE, "-F", "qcow2", disco])


def crear_xml(n_vm, nombre, disco, fichero_xml):
    interfaces = ""
    for n_if, (bridge, _) in enumerate(MAQUINAS[nombre]["ifaces"]):
        interfaces += PLANTILLA_INTERFAZ.format(
            mac=mac_para(n_vm, n_if), bridge=bridge, ranura=PRIMERA_RANURA + n_if)
    with open(fichero_xml, "w") as f:
        f.write(PLANTILLA_XML.format(nombre=nombre, disco=disco, interfaces=interfaces))
    print(f"XML generado: {fichero_xml}")


def generar_interfaces(nombre):
    datos = MAQUINAS[nombre]
    lineas = ["auto lo", "iface lo inet loopback"]
    for n_if, (bridge, host) in enumerate(datos["ifaces"]):
        net = ipaddress.ip_network(REDES[bridge])
        iface = f"{PREFIJO_IFACE}{PRIMERA_RANURA + n_if}"
        lineas += ["", f"# {bridge} ({net})",
                   f"auto {iface}",
                   f"iface {iface} inet static",
                   f"    address {net[host]}",
                   f"    netmask {net.netmask}"]
        if datos["gateway"] and ipaddress.ip_address(datos["gateway"]) in net:
            lineas.append(f"    gateway {datos['gateway']}")
        for destino, via in datos["rutas"]:
            if ipaddress.ip_address(via) in net:
                lineas.append(f"    post-up ip route add {destino} via {via}")
    return "\n".join(lineas) + "\n"


def crear_script_config(nombre, fichero):
    """Script para pegar dentro de la VM (como root) que deja su red configurada."""
    texto = "cat > /etc/network/interfaces <<'EOF'\n"
    texto += generar_interfaces(nombre)
    texto += "EOF\n"
    texto += f"echo {nombre} > /etc/hostname && hostname {nombre}\n"
    if MAQUINAS[nombre]["router"]:
        texto += "echo net.ipv4.ip_forward=1 > /etc/sysctl.d/99-router.conf\n"
        texto += "sysctl -p /etc/sysctl.d/99-router.conf\n"
    texto += "systemctl restart networking\n"
    texto += "ip -br a\n"
    with open(fichero, "w") as f:
        f.write(texto)
    print(f"Configuración de red: {fichero}")


def main():
    if os.geteuid() != 0:
        sys.exit("Ejecuta el script con sudo")

    if not os.path.exists(IMAGEN_BASE):
        sys.exit(f"No existe la imagen base {IMAGEN_BASE}")

    elegidas = sys.argv[1:] or list(MAQUINAS)
    for nombre in elegidas:
        if nombre not in MAQUINAS:
            sys.exit(f"Máquina desconocida: {nombre}. Opciones: {', '.join(MAQUINAS)}")

    crear_bridges()

    for n_vm, nombre in enumerate(MAQUINAS, start=1):
        if nombre not in elegidas:
            continue
        disco = os.path.join(DIR_IMAGENES, f"agr-{nombre}.qcow2")
        fichero_xml = f"plantilla-{nombre}.xml"

        print(f"\n=== Máquina {nombre} ===")
        eliminar_dominio(nombre)
        crear_disco(disco)
        crear_xml(n_vm, nombre, disco, fichero_xml)
        ejecutar(["virsh", "define", fichero_xml])
        ejecutar(["virsh", "start", nombre])
        crear_script_config(nombre, f"configurar-{nombre}.sh")

    print()
    ejecutar(["virsh", "list", "--all"])
    print("\nAhora, en cada máquina:")
    print("  virsh console <nombre>      (salir con Ctrl + ])")
    print("  entra como root y pega el contenido de configurar-<nombre>.sh")


if __name__ == "__main__":
    main()
