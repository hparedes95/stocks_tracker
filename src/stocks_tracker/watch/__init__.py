"""Vigilante de mercado en vivo.

Separado de `alerts/` a proposito, porque responden a preguntas distintas:

- `alerts/` mira los datos de CIERRE una vez al dia y pregunta "¿hay algo que
  deberia revisar?". Es analisis con calma.
- `watch/` mira el precio en vivo cada minuto y pregunta "¿se esta cayendo todo
  ahora mismo?". Es vigilancia.

Mezclarlos habria contaminado el analisis con datos intradia sin consolidar,
que es justo lo que el resto del proyecto evita.
"""
