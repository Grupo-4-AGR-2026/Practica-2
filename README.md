# Práctica 2

Repositorio correspondiente a la **Práctica 2 de la asignatura Administración y Gestión de Redes**, centrada en la **virtualización, automatización del despliegue y configuración de servicios e infraestructuras de red**.

La práctica aborda dos escenarios complementarios: el despliegue automatizado de un servicio web mediante **Vagrant** y la construcción de una infraestructura de red virtualizada mediante **QEMU/KVM y libvirt**. En ambos casos se busca trabajar con entornos reproducibles y automatizar, en la medida de lo posible, las tareas de administración y configuración.

## Contenido

### 1. Virtualización de servicios web con Vagrant

En la primera parte se automatiza el despliegue de una máquina virtual Linux mediante **Vagrant**, sobre la que se instala y ejecuta un servidor web desarrollado en **Node.js**.

El proceso de configuración se define mediante un `Vagrantfile`, que permite preparar el entorno de forma automática: creación de la máquina virtual, instalación de las dependencias necesarias, descarga del servidor desde un repositorio Git y puesta en marcha del servicio.

También se configura la conectividad entre el sistema anfitrión y la máquina virtual mediante **redirección de puertos**, permitiendo acceder al servidor web desde el exterior mientras este permanece ejecutándose en su puerto interno.

Finalmente, se estudian aspectos relacionados con el **consumo de recursos**, la configuración de red de la máquina virtual y las posibilidades de ampliar el entorno para alojar otros servicios.

### 2. Virtualización de infraestructura de red con QEMU/KVM

La segunda parte amplía el escenario anterior para construir una **infraestructura de red virtualizada** utilizando **QEMU/KVM** y **libvirt**.

La infraestructura está formada por varias máquinas virtuales organizadas en diferentes subredes y conectadas mediante máquinas que actúan como routers. La creación y configuración de estos elementos se automatiza mediante **scripts**, permitiendo reproducir la topología de forma sencilla.

Los routers se configuran mediante **Software Routing Suite**, estableciendo las rutas necesarias para proporcionar conectividad entre las distintas redes.

Sobre esta infraestructura se despliega un servidor web que sirve como punto de prueba para verificar la conectividad entre los diferentes segmentos de la red. Se realizan pruebas desde máquinas pertenecientes a distintas subredes para comprobar que el tráfico puede atravesar correctamente los routers hasta alcanzar el servicio.

Esta parte incluye además el diseño del **plan de direccionamiento**, la configuración de las **tablas de enrutamiento** y la comprobación de la conectividad de extremo a extremo.

## Tecnologías utilizadas

- **Vagrant** — automatización y gestión de máquinas virtuales.
- **VirtualBox** — proveedor de virtualización para la primera parte.
- **QEMU/KVM** — virtualización de la infraestructura de red.
- **libvirt** — gestión y configuración de máquinas virtuales.
- **Python y Bash** — automatización y configuración del entorno.
- **Node.js** — implementación del servidor web.
- **Linux** — sistema operativo utilizado en las máquinas virtuales.
- **Software Routing Suite** — configuración del encaminamiento.
- **XML** — definición y configuración de máquinas virtuales mediante libvirt.

## Objetivos

Los principales objetivos de la práctica son:

- Automatizar el **despliegue y configuración** de máquinas virtuales.
- Trabajar con diferentes tecnologías de virtualización.
- Comprender la configuración de **redes virtualizadas**.
- Diseñar planes de **direccionamiento IP**.
- Configurar y verificar **tablas de encaminamiento**.
- Desplegar servicios sobre infraestructuras virtualizadas.
- Comprobar la **conectividad de extremo a extremo** entre diferentes subredes.
- Familiarizarse con herramientas de automatización aplicadas a la **administración de sistemas y redes**.

## Estructura del repositorio

El repositorio contiene los diferentes **scripts, archivos de configuración, definiciones de máquinas virtuales y documentación** desarrollados durante la práctica, organizados según las distintas partes del ejercicio.

El objetivo es que el entorno pueda ser desplegado y reproducido de forma automatizada, evitando en la medida de lo posible la configuración manual de cada máquina y servicio.
