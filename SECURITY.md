# Política de Seguridad — DJNova

## Medidas incluidas por defecto

1. **Control Remoto Web con PIN (`dj_web_remote`):**
   - El servidor HTTP local (puerto `9095`) exige un **PIN numérico de acceso** en `GET /api/state` y `POST /api/action` mediante cabecera `X-DJNova-Pin` o parámetro `?pin=`.
   - Al escanear el código QR mostrado en la consola, el PIN se envía automáticamente; cualquier otro dispositivo conectado al mismo Wi-Fi recibirá `401 Unauthorized` si no conoce el PIN.
   - El tamaño máximo de cuerpo JSON está limitado a `64 KB`, las acciones están restringidas a una lista explícita (`ALLOWED_ACTIONS`) y la interfaz móvil escapa HTML para evitar XSS en nombres de pistas.
2. **Protección de credenciales y Stream Keys (`dj_radio_stream`, `dj_live_broadcast`):**
   - Las contraseñas de Icecast y *Stream Keys* de YouTube/Facebook Live se ocultan en la interfaz (`Password`) y se enmascaran en los registros (`mask_secret`).
   - Las configuraciones locales (`*.json` de usuario y archivos `.lsp`) están excluidas en `.gitignore` para evitar filtraciones accidentales en GitHub.
3. **Validación de URLs y rutas temporales privadas:**
   - Se bloquea la inyección de flags CLI en `yt-dlp` y `mpv` mediante validación de esquema (`http/https` o `rtmp/rtmps`) y separador `--`.
   - Los sockets IPC y archivos temporales (`now_playing.txt`) usan un directorio temporal privado por usuario (`0700` en Linux) o *Named Pipes* por PID en Windows en lugar de rutas predecibles en `/tmp`.

## Recomendaciones en cabina y eventos en vivo

- **Usa el código QR solo desde dispositivos de confianza.** Si sospechas que alguien vio el código o el PIN, pulsa **"Nuevo PIN"** en el diálogo de Control Remoto Web.
- **No compartas tus archivos `.json` de configuración personal** (`~/.config/DJNova/` o `%LOCALAPPDATA%\DJNova\`) si guardaste contraseñas de radio o *Stream Keys* de YouTube/Facebook.
- **Reportar vulnerabilidades:** abre un *Security Advisory* privado o un Issue sin publicar credenciales en el repositorio de GitHub.
