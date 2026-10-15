#!/usr/bin/env python3
"""
Crea y arranca N máquinas virtuales KVM (1-4) a partir de una imagen base.

Para cada máquina X:
  1. Crea el disco /var/lib/libvirt/images/agr-vmX.qcow2 con qemu-img,
     usando agr-vm-base.qcow2 como imagen padre (backing file).
  2. Genera su XML: plantilla-vmX.xml
  3. La define y la arranca con virsh (dominio mvX).

Uso:
    sudo python3 crear_vms.py          # crea 4 máquinas
    sudo python3 crear_vms.py 2        # crea 2 máquinas
"""

import os
import subprocess
import sys

DIR_IMAGENES = "/var/lib/libvirt/images"
IMAGEN_BASE = os.path.join(DIR_IMAGENES, "agr-vm-base.qcow2")
MAX_VMS = 4

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
    <interface type="bridge">
      <source bridge="virbr0"/>
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


def crear_xml(nombre, disco, fichero_xml):
    with open(fichero_xml, "w") as f:
        f.write(PLANTILLA_XML.format(nombre=nombre, disco=disco))
    print(f"XML generado: {fichero_xml}")


def main():
    if os.geteuid() != 0:
        sys.exit("Ejecuta el script con sudo")

    n = int(sys.argv[1]) if len(sys.argv) > 1 else MAX_VMS
    if not 1 <= n <= MAX_VMS:
        sys.exit(f"El número de máquinas debe estar entre 1 y {MAX_VMS}")

    if not os.path.exists(IMAGEN_BASE):
        sys.exit(f"No existe la imagen base {IMAGEN_BASE}")

    for i in range(1, n + 1):
        nombre = f"mv{i}"
        disco = os.path.join(DIR_IMAGENES, f"agr-vm{i}.qcow2")
        fichero_xml = f"plantilla-vm{i}.xml"

        print(f"\n=== Máquina {i}: {nombre} ===")
        eliminar_dominio(nombre)
        crear_disco(disco)
        crear_xml(nombre, disco, fichero_xml)
        ejecutar(["virsh", "define", fichero_xml])
        ejecutar(["virsh", "start", nombre])

    print()
    ejecutar(["virsh", "list", "--all"])


if __name__ == "__main__":
    main()
