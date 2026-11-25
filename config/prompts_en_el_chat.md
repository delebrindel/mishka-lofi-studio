# Prompts de Mishka LoFi desde el chat (sin API key)

Cuando en un chat de este proyecto se pidan prompts / propuestas / un brief para una canción de Mishka LoFi:

1. Sigue la guía de estilo de `pipeline/config/brief_instructions.md` (caption, estructura, BPM, tonalidad,
   título y descripción de YouTube con el formato exacto).
2. Revisa los títulos ya usados que mencione la persona para no repetirlos.
3. Entrega las propuestas (3 por defecto) en UN solo bloque de código JSON, listo para copiar y pegar en
   Mishka LoFi Studio → 💡 Idea → "pega aquí el brief". Antes del bloque, una línea por propuesta con
   título · BPM · tonalidad, para elegir rápido.

Formato del bloque (un arreglo, aunque sea una sola propuesta):

```json
[
  {
    "title": "Snowfall by the Fireplace",
    "description": "Snow falling outside, a fire crackling inside, and nothing else to do tonight.\n\n\"Snowfall by the Fireplace\" is a warm, piano-led cozy LoFi instrumental with ... — made for ...\n\nStay by the fire a little longer. Mishka will keep you company. 🐾\n\n🎹 ...\n🔥 ...\n📖 Perfect for ...\n\nWelcome to Mishka LoFi.",
    "tags": ["lofi", "cozy lofi", "winter lofi", "piano lofi", "study music"],
    "scene": "Mishka lying on a quilt by a stone fireplace, snow falling behind a big wooden window, amber lamp, steaming mug.",
    "song": {
      "caption": "warm cozy winter lofi piano, soft felt piano lead, ..., fade-out ending",
      "lyrics": "[Intro - piano]\n[Main Theme - piano and Rhodes]\n[Main Theme - piano]\n[Outro - fade out]",
      "bpm": 66, "key": "F", "time_signature": 4, "duration": 210, "batch_size": 2
    }
  }
]
```

Si el chat tiene acceso a la carpeta `D:\Mishka LoFi\Automation`, en vez de pegar se puede escribir directo
`out/<slug>/brief.json`, `song.json` y `desc.txt` (mismo contenido); la propuesta aparece en la pestaña 🎵 Canción.
