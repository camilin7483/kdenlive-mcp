# TOOLS.md — las 77 tools en 1 línea

Agrupadas por módulo real. Firmas completas: ver API.md.

## Proyecto (`kdenlive_project.ProjectStore`)

| Tool | Qué hace (1 línea) | Cuándo usar |
|---|---|---|
| `create_project` | Crea `.kdenlive` nuevo con perfil dado. | Empezar cualquier edición. |
| `open_project` | Abre un proyecto y retorna su info. | Retomar trabajo existente. |
| `save_project` | Valida y confirma el proyecto en disco. | Tras una tanda de cambios. |
| `inspect_project` | Vuelca la estructura MLT completa. | Depurar o entender un proyecto. |
| `get_project_info` | Metadatos básicos (título, perfil, fps). | Chequeo rápido antes de operar. |
| `backup_project` | Copia de respaldo del `.kdenlive`. | Antes de cambios destructivos. |

## Medios (`media_tools`)

| Tool | Qué hace | Cuándo usar |
|---|---|---|
| `scan_media` | Lista medios de una carpeta por extensiones. | Descubrir fuentes. |
| `import_media` | Registra un medio (producer + bin). | Antes de montarlo al timeline. |
| `remove_media` | Elimina un medio y sus referencias. | Limpiar fuentes no usadas. |
| `analyze_media` | Sondea con ffprobe (duración/streams). | Conocer un archivo antes de usarlo. |
| `find_silence` | Detecta tramos de silencio (no corta). | Localizar pausas para recortar. |
| `extract_audio` | Extrae el audio de un vídeo. | Trabajar solo el sonido. |
| `generate_proxy` | Proxy liviano a ancho dado. | Editar fluido con 4K pesados. |

## Timeline (`timeline_tools`)

| Tool | Qué hace | Cuándo usar |
|---|---|---|
| `create_track` | Crea track video/audio. | Preparar pistas. |
| `delete_track` | Elimina un track. | Quitar pistas sobrantes. |
| `add_clip` | Añade clip (in/out, posición). | Montar el timeline. |
| `remove_clip` | Quita un clip por índice. | Recortar contenido. |
| `move_clip` | Reordena clips del mismo track. | Reestructurar secuencia. |
| `trim_clip` | Ajusta in/out de un clip. | Afinar cortes. |
| `split_clip` | Divide un clip en un frame. | Insertar corte intermedio. |
| `duplicate_clip` | Duplica un clip tras el original. | Repetir un plano. |
| `add_transition` | Transición (dissolve por defecto). | Suavizar cambios de plano. |

## Efectos (`effects_tools`)

| Tool | Qué hace | Cuándo usar |
|---|---|---|
| `add_effect` | Efecto genérico con params dict. | Cualquier efecto MLT por nombre. |
| `remove_effect` | Quita un efecto por id. | Revertir un efecto. |
| `set_effect_parameter` | Crea/actualiza un parámetro. | Ajustar un efecto existente. |
| `add_keyframe` | Anima un parámetro en un frame. | Efectos con movimiento. |
| `add_transform` | Escala + desplazamiento. | Reencuadrar / zoom. |
| `add_blur` | Desenfoque por radio. | Ocultar o suavizar. |
| `add_sharpen` | Enfoque por cantidad. | Realzar detalle. |

## Audio (`audio_tools`)

| Tool | Qué hace | Cuándo usar |
|---|---|---|
| `set_volume` | Ganancia dB de un clip. | Nivelar diálogos. |
| `normalize_audio` | Normaliza con loudnorm EBU R128. | Master a -16 LUFS aprox. |
| `fade_in` | Fundido de entrada. | Apertura suave de clip. |
| `fade_out` | Fundido de salida. | Cierre suave de clip. |
| `remove_silence` | Recorta silencios con ffmpeg. | Compactar voz. |
| `duck_music` | Atenúa música bajo la voz (fija). | Mezcla voz+música simple. |

## Subtítulos (`subtitle_tools`)

| Tool | Qué hace | Cuándo usar |
|---|---|---|
| `create_subtitles` | Crea subs desde listas texto/frames. | Subtitular programáticamente. |
| `import_srt` | Importa un SRT al proyecto. | Reusar subs existentes. |
| `export_srt` | Exporta subs del proyecto a SRT. | Publicar o revisar texto. |
| `burn_subtitles` | Quema el SRT en el vídeo con libass (re-encode). | Entregable con subs visibles siempre. |

## Render (`render_tools`)

| Tool | Qué hace | Cuándo usar |
|---|---|---|
| `list_render_profiles` | Lista los 4 perfiles y sus args melt. | Elegir formato/calidad. |
| `set_render_profile` | Guarda el perfil en el proyecto. | Fijar preset antes de render. |
| `render_project` | Lanza melt en background. | Producir el MP4 final. |
| `get_render_status` | Estado/progreso/log del job. | Polling del render. |
| `cancel_render` | Cancela un job en curso. | Abortar render largo/erróneo. |

## QA (`qa_tools`)

| Tool | Qué hace | Cuándo usar |
|---|---|---|
| `validate_project` | Valida estructura MLT multitrack. | Antes de renderizar. |
| `check_missing_media` | Medios referenciados ausentes. | Tras mover archivos. |
| `check_timeline` | Resumen tracks/clips + problemas. | Revisar el montaje. |
| `check_audio` | Chequea audio del timeline. | Antes del master. |
| `check_render` | Inspecciona el MP4 final. | Verificar el entregable. |
| `generate_preview` | Preview rápido a baja resolución. | Vista previa sin render completo. |

## Pipeline (`edit_video`)

| Tool | Qué hace | Cuándo usar |
|---|---|---|
| `edit_video` | Edición YouTube completa en una llamada (+`narration` TTS opcional → pista VOZ). | Automatizar de punta a punta. |

## TTS (`tts_engine`, voz local Piper)

| Tool | Qué hace | Cuándo usar |
|---|---|---|
| `tts_list_voices` | Lista voces instaladas (default `es_MX-claude`). | Elegir voz antes de sintetizar. |
| `tts_speak` | Texto → WAV 48 kHz mono (con caché por hash). | Generar una locución suelta. |
| `tts_srt_to_narration` | SRT → WAV mezclado con cada bloque en su timestamp (opcional: al timeline). | Narración temporizada lista para montar. |

## Catálogo MLT (`catalog_tools`)

| Tool | Qué hace | Cuándo usar |
|---|---|---|
| `list_filters` | Catálogo real de filtros MLT (filtrable por `kind`). | Saber qué efectos existen. |
| `list_transitions` | Catálogo real de transiciones MLT. | Elegir transición por servicio. |
| `list_producers` | Catálogo real de productores MLT. | Conocer fuentes soportadas. |
| `describe_filter` | Parámetros de un filtro por su servicio. | Parametrizar un efecto. |
| `add_filter` | Añade cualquier filtro MLT validado contra el catálogo. | Efectos fuera de los atajos. |
| `add_transition_by_service` | Transición MLT arbitraria en el tractor. | Transiciones no dissolve. |

## Render HW / cola (`render_pro`)

| Tool | Qué hace | Cuándo usar |
|---|---|---|
| `detect_hwaccel` | Detecta aceleración HW real (VAAPI; QSV con preflight, False si el driver falla). | Saber si hay GPU disponible. |
| `render_hw` | Render con aceleración hardware (default `MP4-H264-VAAPI`). | Render rápido con GPU. |
| `render_queue` | Cola de renders secuenciales. | Varios entregables de una vez. |
| `render_range` | Render de un rango (`start_sec`/`duration_sec`, perfil `Preview`). | Previsualizar un tramo. |

## Audio pro (`audio_pro`)

| Tool | Qué hace | Cuándo usar |
|---|---|---|
| `loudness_measure` | Mide LUFS integrados / true peak / LRA. | Diagnosticar nivel antes del master. |
| `loudness_normalize` | Normaliza a un objetivo LUFS (default -14). | Master a norma de plataforma. |
| `apply_compressor` | Compresor dinámico (umbral + ratio). | Nivelar voz con picos. |
| `split_stems` | Separa stems voz/música(/sfx) del proyecto. | Preparar mezcla por grupos. |
| `tts_duck_auto` | Ducking automático de la música bajo la narración. | Mezcla voz+música sin pelear niveles. |
| `mix_stems` | Mezcla los stems a un master. | Cierre de la mezcla. |

## Timeline pro (`timeline_pro`)

| Tool | Qué hace | Cuándo usar |
|---|---|---|
| `add_marker` | Marca en un frame con texto y color. | Señalar capítulos/errores. |
| `list_markers` | Lista las marcas del proyecto. | Revisar el guion de marcas. |
| `remove_marker` | Elimina una marca por id. | Limpiar marcas. |
| `ripple_delete` | Borra un clip y cierra el hueco. | Recortar sin dejar silencios. |
| `slip_clip` | Desplaza el contenido del clip (in/out). | Reencuadrar en el tiempo. |
| `multicam_sync` | Sincroniza medios por audio (peak rápido o xcorr preciso). | Multicámara. |
| `nest_sequence` | Anida un proyecto hijo en un track del padre. | Secuencias reutilizables. |

## Helpers importables (no tools MCP)

77 tools verificadas. Se importan directo desde su módulo:

| Función | Qué hace | Cuándo usar |
|---|---|---|
| `apply_preset` | Preset YouTube `limpio`/`cine`/`vivo` + fundido. | Look rápido sin parametrizar. |
| `align_srt_to_audio` | Recoloca el SRT a fronteras de voz (máx. 0.5 s). | Subs desfasados del audio. |
| `auto_chapters` | Capítulos desde markers o cada 60 s. | Estructurar vídeos largos. |
| `get_thumbnail` | Frame JPG (320 px, seg. 1 por defecto). | Portada/preview rápida. |
| `cut_silence` | Corte real de silencios (`silenceremove`). | Compactar voz de verdad. |
| `sidechain_duck` | Duck real con compresor sidechain (-10 dB). | Mezcla voz+música pro. |
