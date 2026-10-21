# X-OG Mobile H2 — Halo 2 en teléfonos MediaTek

Versión experimental basada en **X-OG Mobile** (port de xemu a Android, GPL-2.0). No está afiliada a Microsoft, Bungie ni al proyecto xemu.

## Qué añade esta versión

- **Apuntar arrastrando el dedo**: en la parte derecha de la pantalla (fuera de los botones), arrastra para mover la cámara, como un ratón. Al soltar o quedarte quieto, la cámara se detiene.
- **Apuntar con giroscopio** (opcional): gira el teléfono para afinar la puntería.
- **Compensación de zona muerta**: Halo 2 ignora movimientos pequeños del stick; esta opción los "levanta" para que la puntería fina funcione.
- **Botón "Aplicar perfil Halo 2 + MediaTek"**: Vulkan, sin animación de arranque, apuntado táctil + giroscopio.
- **Corrección**: los sticks táctiles enviaban el eje vertical invertido respecto al mando físico.
- Se instala **junto a** X-OG Mobile original (id `emu.xbox.og.h2`).

## Qué necesitas (no incluido, obtenlo de tu propia consola)

| Archivo | Qué es |
|---|---|
| MCPX ROM (`mcpx_1.0.bin`, 512 bytes) | ROM de arranque de la Xbox |
| BIOS / Flash (`Complex_4627.bin` o similar, 256 KB/1 MB) | BIOS de la Xbox |
| Imagen HDD (`.qcow2`) | Disco duro con el dashboard |
| Halo 2 en formato **XISO** | Copia de tu disco original |

## Compilar el APK (desde el teléfono, gratis)

1. Entra en https://github.com/Ashnar2602/X-OG_Mobile y pulsa **Fork**.
2. Sube a tu fork los archivos de este paquete respetando las carpetas (o pide a Claude que los suba si le das acceso a tu fork).
3. Ve a la pestaña **Actions** de tu fork, activa los workflows y ejecuta **"Compilar APK (Halo 2 MTK)"**.
4. En ~15–25 min aparece en **Releases** el archivo `XOG-Mobile-Halo2-MTK.apk`. Descárgalo e instálalo (permite "orígenes desconocidos").

## Primer arranque

1. Ajustes → **Configure system files** → importa MCPX, BIOS y HDD.
2. Ajustes → **Aplicar perfil Halo 2 + MediaTek**.
3. Inicio → **Scan folder** → elige la carpeta con tu XISO → Halo 2 → **Play game**.

## Expectativas reales en MediaTek

- **Requisitos**: Android 10+, procesador de 64 bits, Vulkan. Recomendado: Dimensity 8100 / 8200 / 9000 o superior. En Helio G-series es muy probable que vaya a menos de 10 FPS o no arranque.
- La CPU de la Xbox (x86) se traduce a ARM por software (TCG de QEMU): es el mayor cuello de botella.
- Las GPU Mali de MediaTek tienen controladores Vulkan con fallos conocidos; puede haber gráficos rotos o cierres.
- En xemu de PC Halo 2 es "jugable con problemas menores"; en Android es una prueba de concepto.
- **No instales la actualización 1.5 de Halo 2** en el HDD: provoca cuelgues aleatorios en xemu.
- La linterna en pantalla dividida causa bajones fuertes; juega en modo un jugador.

## Controles sugeridos

| Acción | Botón |
|---|---|
| Disparar | RT |
| Granada | LT |
| Saltar | A |
| Golpe cuerpo a cuerpo | B |
| Recargar / recoger | X |
| Cambiar arma | Y |
| Agacharse | L3 |
| Zoom | R3 |

Puedes mover y redimensionar los botones con el editor de controles táctiles.
