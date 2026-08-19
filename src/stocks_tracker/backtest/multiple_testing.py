"""Que parte de lo que parece funcionar es solo el numero de veces que miraste.

El problema, con los numeros de esta aplicacion. La validacion evalua unas once
senales en cuatro horizontes. Son 44 contrastes. Si NINGUNA sirviera para nada,
al 5 % de nivel esperarias que **dos** salieran "significativas" igualmente. Y
saldrian con su t por encima de 2, su exceso positivo y su motivo bien
redactado: no hay nada en la pantalla que las distinga de una senal de verdad.

Esto no es una posibilidad teorica, es aritmetica. La probabilidad de que al
menos una de 44 pruebas independientes de un falso positivo al 5 % es
1 - 0,95^44 = 90 %. Es decir: es mas probable que salga alguna a que no salga
ninguna.

**Que se corrige y que no.** Aqui se controla la tasa de falsos
descubrimientos (FDR) con Benjamini-Hochberg, no la probabilidad de cometer un
solo error (FWER, que es lo que hace Bonferroni). La diferencia importa para lo
que estas haciendo:

- Bonferroni pregunta "¿que probabilidad hay de equivocarme aunque sea una vez?"
  y para conseguirlo exige tanto que descarta casi todo. Con 44 pruebas
  necesitarias p < 0,0011.
- Benjamini-Hochberg pregunta "de las que declare buenas, ¿que fraccion seran
  basura?". Con q = 0,10 aceptas que una de cada diez senales validadas sea
  ruido.

La segunda es la pregunta correcta cuando el resultado es una lista de
candidatas que vas a mirar, no una decision unica e irreversible. Y es menos
brutal: Bonferroni sobre datos financieros ruidosos deja la lista vacia
siempre, y una comprobacion que nunca deja pasar nada acaba desactivandose.

**Lo que esto NO arregla.** Corrige las pruebas que se REGISTRAN, no las que se
hicieron. Si se prueban veinte variantes de una senal, se deja la mejor en el
codigo y solo se valida esa, el recuento dice 1 y la correccion no sirve de
nada. Contra eso no hay estadistica que valga: hace falta no hacerlo.
"""

from __future__ import annotations

import numpy as np

# Fraccion de falsos positivos que se acepta entre las senales validadas.
# 0,10 y no 0,05: con pocos eventos por senal, un umbral mas estricto vacia la
# lista y entonces la pantalla no dice nada. Uno de cada diez es un precio
# asumible cuando lo que sale es una lista para mirar, no una orden.
FDR_Q = 0.10


def benjamini_hochberg(p_values, q: float = FDR_Q) -> tuple[np.ndarray, np.ndarray]:
    """Devuelve (sobrevive, q_valor) para cada contraste.

    El procedimiento: ordenar los p de menor a mayor, buscar el mayor rango `k`
    con p(k) <= k/m * q, y aceptar todos los contrastes hasta ese rango. El
    detalle que se olvida es ese "hasta ese rango": una vez encontrado el corte
    sobreviven TODAS las pruebas con p menor, incluso alguna que por si sola no
    cumpliera la desigualdad. Comprobar cada p contra su propio umbral por
    separado es un error frecuente y da un procedimiento distinto y mas
    conservador.

    El q-valor devuelto es el ajustado monotono, que es el que se puede leer
    como "el FDR al que esta prueba entraria".

    Los contrastes sin p (NaN) no se cuentan en `m`. Contarlos haria que anadir
    una senal sin datos endureciera el umbral de las demas, que es justo lo
    contrario de lo razonable.
    """
    p = np.asarray(p_values, dtype=float)
    sobrevive = np.zeros(len(p), dtype=bool)
    q_valores = np.full(len(p), np.nan)

    validos = np.where(np.isfinite(p))[0]
    m = len(validos)
    if m == 0:
        return sobrevive, q_valores

    orden = validos[np.argsort(p[validos], kind="stable")]
    rangos = np.arange(1, m + 1)

    # q ajustado, con el minimo acumulado desde el final para que sea monotono:
    # una prueba no puede tener un q mayor que otra con p mas alto.
    crudo = p[orden] * m / rangos
    ajustado = np.minimum.accumulate(crudo[::-1])[::-1]
    ajustado = np.minimum(ajustado, 1.0)
    q_valores[orden] = ajustado

    cumple = p[orden] <= rangos / m * q
    if cumple.any():
        corte = int(np.max(np.where(cumple)[0]))
        sobrevive[orden[: corte + 1]] = True

    return sobrevive, q_valores


def expected_false_positives(n_tests: int, alpha: float = 0.05) -> float:
    """Cuantas pruebas pasarian por azar si ninguna senal sirviera.

    Sirve para escribirlo en pantalla: "de 44 pruebas, 2 pasarian por azar" se
    entiende sin saber que es un FDR, y es el dato que hace falta para leer una
    lista de senales validadas.
    """
    return float(max(0, n_tests) * alpha)
