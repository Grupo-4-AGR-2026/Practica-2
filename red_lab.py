#!/usr/bin/env python3
"""
AGR - Virtualización de infraestructura de red con KVM (lab 3103)

Despliega los 4 routers del esquema:

                 Servidor
                    |  (br-srv-r1)
                   R1
                    |  (br-r1-r2)
                   R2
        (br-r2-r3) /   \\ (br-r2-r4)
                R3     R4
               /  \\    /  \\
           RED1 RED2 RED3 RED4      (br-red1 .. br-red4)

- Cada enlace del dibujo es un bridge de Open vSwitch en el anfitrión.
- Cada router es una copia diferencial (qcow2) de la imagen maestra en
  /var/lib/libvirt/images; la maestra nunca se modifica.
- Los XML se generan con lxml a partir de la plantilla de Moodle y se
  guardan en /etc/libvirt/qemu.

Uso (como root / con sudo):
  sudo python3 despliegue.py crear       # bridges + discos + XML + define
  sudo python3 despliegue.py arrancar    # virsh start
  sudo python3 despliegue.py consolas    # un xterm con la consola de cada router
  sudo python3 despliegue.py parar       # virsh shutdown
  sudo python3 despliegue.py destruir    # destroy + undefine + borra discos, XML y bridges
"""

import copy
import logging
import os
import subprocess
import sys

from lxml import etree

# ------------------------------ RUTAS ------------------------------
# Imagen maestra (solo se lee, nunca se modifica)
IMAGEN_MAESTRA = ("/home/alumno/AGR/Practica-2/mv-xml-configurations/"
                  "agr-vm-base.qcow2")
DIR_IMAGENES = "/var/lib/libvirt/images"   # copias de cada router
DIR_XML = "/etc/libvirt/qemu"              # XML de cada router
# -------------------------------------------------------------------

# Plantilla XML de Moodle. Los "XXX" se rellenan desde el script con lxml.
PLANTILLA_XML = """<domain type="kvm">
  <name>XXX</name>
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
    <emulator>/usr/bin/kvm</emulator>
    <disk type="file" device="disk">
      <driver name="qemu" type="qcow2"/>
      <source file="/mnt/tmp/XXX/XXX.qcow2"/>
      <target dev="vda" bus="virtio"/>
    </disk>
    <interface type="bridge">
      <source bridge="XXX"/>
      <model type="virtio"/>
    </interface>
    <serial type="pty">
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

# Topología: cada router y los bridges a los que se conecta, en orden.
# La 1ª interfaz será eth0 dentro de la VM, la 2ª eth1, etc.
ROUTERS = {
    "R1": ["br-srv-r1", "br-r1-r2"],
    "R2": ["br-r1-r2", "br-r2-r3", "br-r2-r4"],
    "R3": ["br-r2-r3", "br-red1", "br-red2"],
    "R4": ["br-r2-r4", "br-red3", "br-red4"],
}

BRIDGES = sorted({br for brs in ROUTERS.values() for br in brs})

logging.basicConfig(level=logging.DEBUG, format="[%(levelname)s] %(message)s")
log = logging.getLogger("despliegue")


def run(cmd, check=True):
    log.debug("$ %s", " ".join(cmd))
    return subprocess.run(cmd, check=check)


def rutas(vm):
    return (os.path.join(DIR_IMAGENES, f"{vm}.qcow2"),
            os.path.join(DIR_XML, f"{vm}.xml"))


# ----------------------------- RED ---------------------------------
def crear_bridges():
    for br in BRIDGES:
        run(["ovs-vsctl", "--may-exist", "add-br", br])
        run(["ip", "link", "set", br, "up"])
    print(f"Bridges OVS listos: {', '.join(BRIDGES)}")


def borrar_bridges():
    for br in BRIDGES:
        run(["ovs-vsctl", "--if-exists", "del-br", br], check=False)


# ----------------------------- DISCOS ------------------------------
def crear_disco(vm):
    disco, _ = rutas(vm)
    if os.path.exists(disco):
        log.info("%s ya existe, no se recrea", disco)
        return disco
    # Copia diferencial: los cambios del router van a su qcow2,
    # la imagen maestra queda intacta (solo se lee).
    run(["qemu-img", "create", "-f", "qcow2", "-F", "qcow2",
         "-b", IMAGEN_MAESTRA, disco])
    return disco


# ----------------------------- XML ---------------------------------
def generar_xml(vm, disco, bridges):
    raiz = etree.fromstring(PLANTILLA_XML.encode(),
                            etree.XMLParser(remove_blank_text=True))
    arbol = etree.ElementTree(raiz)

    # Nombre de la VM
    raiz.find("name").text = vm

    # Quitar uuid para que libvirt genere uno nuevo (si no, choca entre VMs)
    uuid = raiz.find("uuid")
    if uuid is not None:
        raiz.remove(uuid)

    devices = raiz.find("devices")

    # Disco -> la copia de este router
    origen = devices.find("disk[@device='disk']/source")
    origen.set("file", disco)
    driver = devices.find("disk[@device='disk']/driver")
    if driver is not None:
        driver.set("type", "qcow2")

    # Interfaces: se usa la del XML base como molde (para respetar el
    # modelo de NIC que espera la imagen) y se borran las originales.
    originales = devices.findall("interface")
    molde = originales[0] if originales else None
    for iface in originales:
        devices.remove(iface)

    for i, br in enumerate(bridges):
        if molde is not None:
            iface = copy.deepcopy(molde)
            for hijo in list(iface):
                if hijo.tag != "model":     # solo conservamos el modelo
                    iface.remove(hijo)
        else:
            iface = etree.Element("interface")
            etree.SubElement(iface, "model", type="virtio")

        iface.set("type", "bridge")
        iface.insert(0, etree.Element("source", bridge=br))
        iface.insert(1, etree.Element("virtualport", type="openvswitch"))
        # Nombre de la tap en el anfitrión, p. ej. r3-e1 (máx. 15 caracteres)
        iface.insert(2, etree.Element("target", dev=f"{vm.lower()}-e{i}"))
        devices.append(iface)

    _, ruta_xml = rutas(vm)
    arbol.write(ruta_xml, pretty_print=True, xml_declaration=False,
                encoding="utf-8")
    return ruta_xml


# ----------------------------- ÓRDENES -----------------------------
def crear():
    if os.geteuid() != 0:
        sys.exit("Ejecuta con sudo: hay que escribir en /var/lib/libvirt y /etc/libvirt.")
    if not os.path.isfile(IMAGEN_MAESTRA):
        sys.exit(f"No encuentro {IMAGEN_MAESTRA}. Revisa la ruta al principio del script.")
    os.makedirs(DIR_IMAGENES, exist_ok=True)
    os.makedirs(DIR_XML, exist_ok=True)

    crear_bridges()
    for vm, bridges in ROUTERS.items():
        disco = crear_disco(vm)
        xml = generar_xml(vm, disco, bridges)
        run(["virsh", "define", xml])
        print(f"{vm} definido: " +
              ", ".join(f"eth{i}->{br}" for i, br in enumerate(bridges)))


def arrancar():
    for vm in ROUTERS:
        run(["virsh", "start", vm], check=False)


def consolas():
    for vm in ROUTERS:
        # Popen = en segundo plano, el script no se queda bloqueado
        subprocess.Popen(["xterm", "-T", vm, "-e", f"virsh console {vm}"])


def parar():
    for vm in ROUTERS:
        run(["virsh", "shutdown", vm], check=False)


def destruir():
    for vm in ROUTERS:
        run(["virsh", "destroy", vm], check=False)
        run(["virsh", "undefine", vm], check=False)
        for f in rutas(vm):
            if os.path.exists(f):
                os.remove(f)
    borrar_bridges()


ORDENES = {"crear": crear, "arrancar": arrancar, "consolas": consolas,
           "parar": parar, "destruir": destruir}

if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] not in ORDENES:
        sys.exit(__doc__)
    ORDENES[sys.argv[1]]()
