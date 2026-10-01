# Práctica 2

Repositorio de las práctica 2 realizada para la asignatura **Administración y Gestión de Redes**, centrada en la virtualización, automatización del despliegue y configuración de servicios e infraestructuras de red.

A lo largo del proyecto se trabaja con distintas tecnologías de virtualización para crear entornos reproducibles y estudiar su funcionamiento desde el punto de vista de la administración de sistemas y redes.

## Contenido

### Virtualización de servicios web con Vagrant

La primera parte consiste en automatizar mediante **Vagrant** el despliegue de una máquina virtual Linux que ejecuta un servidor web desarrollado en **Node.js**.

El entorno se configura completamente mediante el `Vagrantfile`, encargándose de preparar la máquina, instalar las dependencias necesarias, descargar el servidor desde un repositorio Git y ponerlo en funcionamiento.

También se configura la conectividad necesaria para exponer el servicio hacia el exterior mediante **redirección de puertos**, permitiendo acceder al servidor desde fuera de la máquina virtual mientras este continúa ejecutándose en su puerto interno.

Además del despliegue, se analiza el consumo de recursos de la máquina virtual y se estudian diferentes configuraciones de red y posibilidades de ampliación del entorno.

### Virtualización de infraestructura de red con QEMU/KVM

La segunda parte del proyecto amplía el escenario anterior para construir una infraestructura de red virtualizada utilizando **QEMU/KVM** y **libvirt**.

Mediante scripts se automatiza la creación y configuración de las máquinas virtuales necesarias para reproducir una topología formada por varias subredes conectadas mediante routers. Las máquinas que realizan funciones de encaminamiento se configuran con **Software Routing Suite** y las rutas necesarias para proporcionar conectividad entre las diferentes redes.

Sobre esta infraestructura se despliega un servidor web, con el objetivo de comprobar que las máquinas situadas en las distintas subredes pueden alcanzar el servicio a través de los routers.

Esta parte incluye también el diseño del plan de direccionamiento, la configuración de las tablas de enrutamiento y la realización de diferentes pruebas para verificar la conectividad de extremo a extremo.

## Tecnologías

El proyecto utiliza principalmente:

- **Vagrant** y **VirtualBox** para la virtualización de servicios.
- **QEMU/KVM** y **libvirt** para la construcción de la infraestructura de red.
- **Python** y **Bash** para la automatización.
- **Node.js** para el servidor web.
- **Linux** como sistema operativo de las máquinas virtuales.
- **XML** para la definición y configuración de las máquinas virtuales.

## Objetivo

El objetivo principal es familiarizarse con el despliegue automatizado de entornos virtualizados y con la configuración de redes sobre máquinas virtuales, combinando la administración de sistemas con conceptos de direccionamiento, encaminamiento y servicios de red.

El repositorio contiene los scripts, configuraciones y documentación desarrollados durante la práctica.
