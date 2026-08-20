"""Reescribe la referencia de regresion financiera. Acto deliberado.

Se ejecuta A MANO (`make oro`) y nunca desde los tests. Si un test pudiera
actualizar su propia referencia, la referencia dejaria de ser una referencia:
cualquier cambio se aceptaria a si mismo y el fichero pasaria a ser un sello de
goma que dice que si a todo.

Imprime lo que cambia ANTES de escribir, para que quien lo ejecuta vea si es lo
que pretendia. Un `make oro` a ciegas es lo unico que puede estropear esto.
"""

from __future__ import annotations

import sys

from stocks_tracker.core import golden


def main() -> int:
    obtenido = golden.calcular()

    if golden.ruta_referencia().exists():
        cambios = golden.diferencias(golden.cargar_referencia(), obtenido)
        if not cambios:
            print("La referencia ya estaba al dia: no cambia ningun numero.")
            return 0
        print(f"{len(cambios)} numeros financieros van a cambiar:\n")
        for linea in cambios[:60]:
            print(f"  {linea}")
        if len(cambios) > 60:
            print(f"  ... y {len(cambios) - 60} mas")
        print()
        print("Revisa que sea lo que pretendias ANTES de commitear el fichero.")
    else:
        print("No habia referencia previa. Se crea desde cero.")

    destino = golden.guardar(obtenido)
    print(f"\nEscrito {destino}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
