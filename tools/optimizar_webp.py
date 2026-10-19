"""Optimiza las fotos de la portada a WebP.

Uso:
  1) Copia tus fotos originales en  app/static/img/orig/  con estos nombres
     (cualquiera de estas extensiones: .jpg .jpeg .png .webp):

        hero-tecnico          (foto del hero)
        servicio-lavadora     servicio-secadora
        servicio-refrigeradora  servicio-congeladora
        trabajo-1  trabajo-2  trabajo-3
        fachada   equipo

  2) Ejecuta:   .venv\\Scripts\\python.exe tools\\optimizar_webp.py
  3) Revisa el resumen: el hero debe pesar menos de 200 KB y el resto menos de 100 KB.

El script recorta al centro (equivalente a object-fit: cover), no deforma la imagen.
"""

from __future__ import annotations

import os
import sys

from PIL import Image

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IMG = os.path.join(BASE, "app", "static", "img")
ORIG = os.path.join(IMG, "orig")

# (nombre destino, tamaño final, calidad webp, presupuesto en KB)
TARGETS = [
    ("hero-tecnico", (1600, 1000), 78, 200),
    ("servicio-lavadora", (800, 600), 78, 100),
    ("servicio-secadora", (800, 600), 78, 100),
    ("servicio-refrigeradora", (800, 600), 78, 100),
    ("servicio-congeladora", (800, 600), 78, 100),
    ("trabajo-1", (800, 600), 78, 100),
    ("trabajo-2", (800, 600), 78, 100),
    ("trabajo-3", (800, 600), 78, 100),
    ("fachada", (1200, 800), 78, 100),
    ("equipo", (1200, 800), 78, 100),
]

EXTENSIONES = (".jpg", ".jpeg", ".png", ".webp")


def buscar_original(nombre: str) -> str | None:
    for ext in EXTENSIONES:
        ruta = os.path.join(ORIG, nombre + ext)
        if os.path.exists(ruta):
            return ruta
    return None


def recortar_centrado(imagen: Image.Image, tamano: tuple[int, int]) -> Image.Image:
    """Escala y recorta al centro para llenar el marco sin deformar (object-fit: cover)."""
    ancho_destino, alto_destino = tamano
    ancho, alto = imagen.size
    escala = max(ancho_destino / ancho, alto_destino / alto)
    nuevo = (max(1, round(ancho * escala)), max(1, round(alto * escala)))
    imagen = imagen.resize(nuevo, Image.LANCZOS)
    izq = (nuevo[0] - ancho_destino) // 2
    arr = (nuevo[1] - alto_destino) // 2
    return imagen.crop((izq, arr, izq + ancho_destino, arr + alto_destino))


def main() -> int:
    os.makedirs(ORIG, exist_ok=True)
    faltantes = []
    hechos = []

    for nombre, tamano, calidad, presupuesto in TARGETS:
        origen = buscar_original(nombre)
        if not origen:
            faltantes.append(nombre)
            continue

        imagen = Image.open(origen).convert("RGB")
        imagen = recortar_centrado(imagen, tamano)
        destino = os.path.join(IMG, nombre + ".webp")
        imagen.save(destino, format="WEBP", quality=calidad, method=6)

        kb = os.path.getsize(destino) / 1024
        estado = "OK" if kb <= presupuesto else "PESADA"
        hechos.append((nombre, imagen.size, kb, presupuesto, estado))

    print("Archivos generados en:", IMG)
    for nombre, size, kb, presupuesto, estado in hechos:
        print(f"  {nombre:22} {size[0]}x{size[1]:<5} {kb:7.1f} KB  (tope {presupuesto} KB)  {estado}")

    if faltantes:
        print("\nSin original en img/orig (se usara el fondo celeste de respaldo):")
        for nombre in faltantes:
            print("  -", nombre)

    if not hechos:
        print("\nNo se genero ninguna imagen. Copia las fotos en:", ORIG)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
