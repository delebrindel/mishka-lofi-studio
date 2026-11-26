# Mishka LoFi — instrucciones para escribir prompts

Edita este archivo libremente: es lo que Claude lee antes de convertir una idea en un brief.
(Si tienes instrucciones más completas en `mishka-music-producer`, pégalas aquí.)

## El canal
Mishka LoFi: cozy lo-fi instrumental. Mishka es una perrita spitz de ojos azul hielo, tranquila y algo melancólica.
Escenas: ventanas con lluvia o nieve, interiores de madera cálidos, lámparas ámbar, libros, mantas, plantas, chimenea.
Mood: pacífico, íntimo, nostálgico, ligeramente melancólico. Siempre instrumental.

## Caption de ACE-Step (en inglés) — guía v3
Basada en cómo se entrenó ACE-Step 1.5: sus captions de entrenamiento los escribió el "ACE-Step Captioner"
en prosa (4-5 oraciones), y los 50 ejemplos oficiales del repo siguen ese mismo estilo (~85-110 palabras).
Escribe el caption como si describieras el audio terminado:

1. Género + ánimo + producción: "A <2 adjetivos> <Christmas|winter|rainy…> lo-fi instrumental track with modern
   chillhop production, led by soft, intimate close-mic felt piano."
2. Cómo abre y qué entra: "It opens with <instrumento de la intro>, then <ritmo/bajo> enter beneath warm Rhodes
   playing <armonía consonante>."
3. (Opcional) UN acento: "A delicate music box answers the piano far in the background."
4. Mezcla, textura y atmósfera: "The mix is clean but warm, with light vinyl crackle, subtle tape saturation and an
   airy reverb, evoking <escena en pocas palabras>."
5. Cómo evoluciona y termina: "It stays sparse and laid-back throughout and ends with a gentle fade-out as the felt
   piano holds the final chord."

Reglas:
- Siempre la palabra "instrumental". Nada de BPM, tonalidad ni palabras de voz (van en sus campos).
- Armonía consonante: "consonant major-seventh / major-ninth chords that stay in key" o "soft, consonant minor
  chords that stay in key". Nunca half-diminished, jazzy, chromatic ni tension.
- Ritmo: "soft brushed drums and a round, gentle bass"; más quieto: "very soft, sparse drums".
- Ambiente (lluvia, fuego, viento, reloj) solo "faint … far in the background", máximo 2.
- La escena es UNA frase corta después de "evoking": el modelo describe audio, no imágenes.
- El instrumento de la intro y el acento deben aparecer también en la estructura (lyrics).
- Ejemplo real:
  A calm, expectant Christmas lo-fi instrumental track with modern chillhop production, led by soft, intimate close-mic felt piano. It opens with felt piano and delicate celesta sparkles, then soft brushed drums and a round, gentle bass enter beneath warm Rhodes playing consonant major-ninth chords that stay in key. The mix is clean but warm, with light vinyl crackle, subtle tape saturation and an airy reverb, evoking blue dusk over a snowy village as the first lights turn on. It stays sparse and laid-back throughout and ends with a gentle fade-out as the felt piano holds the final chord.

## Lyrics (estructura, sin letra)
SIMPLE, 4 secciones (la estructura detallada con [Instrumental]/[Bridge] repetidos se salió de tono en el A/B).
Etiquetas con instrumento, sin letra, y la salida al estilo de los ejemplos oficiales ("music fades out"):
[Intro - piano]
[Main Theme - piano and Rhodes]
[Main Theme - piano]
[Outro - piano, music fades out]
Si hay instrumento distintivo, que abra y cierre: [Intro - music box] … [Outro - music box, music fades out].

## Parámetros
- BPM 62–80 (más lento = más íntimo; por debajo de 60 ACE-Step se vuelve inestable). Compás 4/4. Duración 210–220 s. Batch 2.
- Tonalidad: SOLO tonalidades comunes, que son las más estables en ACE-Step. Mayores: C, G, D, F, A, E, Bb, Eb. Menores: Am, Em, Dm, Bm. Evita Db, Ab, Gb, Fm, Cm, Gm, F#m, C#m.
- Mayor para escenas cálidas/esperanzadas, menor para melancolía/lluvia.

## Estabilidad (para que ACE-Step no "alucine")
- Nada de BPM, tonalidad ni palabras de voz (vocals, singing, choir, voice) en el caption.
- Máximo 2 sonidos ambientales "far in the background" por canción.
- Un instrumento distintivo por canción (caja de música O campanitas O celesta), no varios.
- Etiquetas de estructura cortas: máximo 2 instrumentos por corchete ("[Main Theme - piano and Rhodes]"), sin listas.
- El caption describe lo mismo que la estructura: si la estructura dice "guitar", el caption también.
- Temporada navideña: se vale sumar un guiño navideño (campanitas suaves, acorde navideño) sin volverlo villancico.

## Título y descripción de YouTube
- Título: corto, evocador, en inglés, 2–5 palabras ("Snowfall by the Fireplace", "Quiet Raindrops", "Christmas Morning").
- Descripción, exactamente con esta forma:

  <frase-gancho de una línea que pinta la escena>

  "<Título>" is a <adjetivos>, <instrumento>-led cozy LoFi instrumental with <2–3 sonidos clave> — made for <3 momentos de uso>.

  <frase corta y cálida que invita a quedarse>. Mishka will keep you company. 🐾

  <emoji> <rasgo musical 1>
  <emoji> <rasgo de ambiente 2>
  <emoji> Perfect for <usos>

  Welcome to Mishka LoFi.

- Ejemplo real:
  Snow falling outside, a fire crackling inside, and nothing else to do tonight.

  "Snowfall by the Fireplace" is a warm, piano-led cozy LoFi instrumental with a soft felt piano, gentle fireplace crackle, and slow, relaxed drums — made for winter evenings, reading by the fire, and unwinding at home.

  Stay by the fire a little longer. Mishka will keep you company. 🐾

  🎹 Soft felt piano, warm and slow
  🔥 Fireplace crackle, snow outside the window
  📖 Perfect for winter evenings, reading, and relaxing at home

  Welcome to Mishka LoFi.
