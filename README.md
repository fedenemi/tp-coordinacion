# Informe TP Coordinación

Alumno: Federico Nemirovsky  
Padrón: 108560

## Identificación de clientes

Cada MessageHandler genera un UUID al conectarse. Ese ID se agrega como campo en todos los mensajes internos (después del tipo), y todos los nodos mantienen su estado indexado por client_id. Cuando el gateway recibe un resultado del pipeline, itera sus handlers hasta encontrar el que corresponde al client_id del mensaje y le envía la respuesta al socket correcto.


## Coordinación entre nodos Sum

La cola de entrada es una work queue, por lo que solo un Sum recibe el END_OF_RECORDS. Para avisarle al resto, ese Sum publica una notificación en un exchange dedicado (sum_control_exchange) al que todos están suscritos. Al recibirla, cada Sum hace flush de sus datos hacia el Aggregator.

Podría ocurrir que la notificación de EOF se procese antes que un dato que ya estaba en el buffer del broker (race condition). Para evitar esto, el consumidor de la cola de datos y el del exchange de control comparten el mismo channel de Pika. Como el event loop procesa los callbacks secuencialmente, es imposible que se pisen, eliminando la condición de carrera por diseño.

## Coordinación entre nodos Aggregator

Cada Sum decide a qué Aggregator enviar cada fruta usando `zlib.crc32(f"{client_id}:{fruit}".encode()) % AGGREGATION_AMOUNT`. Como todos los Sum usan la misma función, los datos de una misma fruta de un mismo cliente siempre llegan al mismo Aggregator. Esto distribuye el trabajo entre todas las réplicas: frutas distintas de un mismo cliente pueden ir a Aggregators distintos.

El EOF, en cambio, se envía a todos los Aggregators. Esto es necesario porque el Joiner espera exactamente AGGREGATION_AMOUNT tops parciales por cliente antes de calcular el resultado final. Los Aggregators sin datos para ese cliente simplemente envían un top vacío.

Cada Aggregator mantiene un contador de EOFs por cliente. Recién cuando recibe el EOF de todas las instancias Sum (SUM_AMOUNT), calcula el top parcial y lo envía al Joiner.


## Escalabilidad

Agregar más nodos Sum no requiere ningún cambio en la lógica: cada instancia nueva compite por mensajes de la working queue, y el exchange de control garantiza que todos se enteren del EOF. Los Aggregators saben cuántos EOFs esperar por la variable de entorno SUM_AMOUNT.

Agregar más Aggregators redistribuye las frutas automáticamente por la función de routing. El Joiner sabe cuántos tops esperar por AGGREGATION_AMOUNT.

Agregar más clientes solo agrega entradas en los diccionarios internos de cada nodo, en donde cada entrada se libera cuando el resultado del cliente se envió.


---
---
---
---
---

# Trabajo Práctico - Coordinación

En este trabajo se busca familiarizar a los estudiantes con los desafíos de la coordinación del trabajo y el control de la complejidad en sistemas distribuidos. Para tal fin se provee un esqueleto de un sistema de control de stock de una verdulería y un conjunto de escenarios de creciente grado de complejidad y distribución que demandarán mayor sofisticación en la comunicación de las partes involucradas.

## Ejecución

`make up` : Inicia los contenedores del sistema y comienza a seguir los logs de todos ellos en un solo flujo de salida.

`make down`:   Detiene los contenedores y libera los recursos asociados.

`make logs`: Sigue los logs de todos los contenedores en un solo flujo de salida.

`make test`: Inicia los contenedores del sistema, espera a que los clientes finalicen, compara los resultados con una ejecución serial y detiene los contenederes.

`make switch`: Permite alternar rápidamente entre los archivos de docker compose de los distintos escenarios provistos.

## Elementos del sistema objetivo

![ ](./imgs/diagrama_de_robustez.jpg  "Diagrama de Robustez")
*Fig. 1: Diagrama de Robustez*

### Client

Lee un archivo de entrada y envía por TCP/IP pares (fruta, cantidad) al sistema.
Cuando finaliza el envío de datos, aguarda un top de pares (fruta, cantidad) y vuelca el resultado en un archivo de salida csv.
El criterio y tamaño del top dependen de la configuración del sistema. Por defecto se trata de un top 3 de frutas de acuerdo a la cantidad total almacenada.

### Gateway

Es el punto de entrada y salida del sistema. Intercambia mensajes con los clientes y las colas internas utilizando distintos protocolos.

### Sum
 
Recibe pares  (fruta, cantidad) y aplica la función Suma de la clase `FruitItem`. Por defecto esa suma es la canónica para los números enteros, ej:

`("manzana", 5) + ("manzana", 8) = ("manzana", 13)`

Pero su implementación podría modificarse.
Cuando se detecta el final de la ingesta de datos envía los pares (fruta, cantidad) totales a los Aggregators.

### Aggregator

Consolida los datos de las distintas instancias de Sum.
Cuando se detecta el final de la ingesta, se calcula un top parcial y se envía esa información al Joiner.

### Joiner

Recibe tops parciales de las instancias del Aggregator.
Cuando se detecta el final de la ingesta, se envía el top final hacia el gateway para ser entregado al cliente.

## Limitaciones del esqueleto provisto

La implementación base respeta la división de responsabilidades de los distintos controles y hace uso de la clase `FruitItem` como un elemento opaco, sin asumir la implementación de las funciones de Suma y Comparación.

No obstante, esta implementación no cubre los objetivos buscados tal y como es presentada. Entre sus falencias puede destactarse que:

 - No se implementa la interfaz del middleware. 
 - No se dividen los flujos de datos de los clientes más allá del Gateway, por lo que no se es capaz de resolver múltiples consultas concurrentemente.
 - No se implementan mecanismos de sincronización que permitan escalar los controles Sum y Aggregator. En particular:
   - Las instancias de Sum se dividen el trabajo, pero solo una de ellas recibe la notificación de finalización en la ingesta de datos.
   - Las instancias de Sum realizan _broadcast_ a todas las instancias de Aggregator, en lugar de agrupar los datos por algún criterio y evitar procesamiento redundante.
  - No se maneja la señal SIGTERM, con la salvedad de los clientes y el Gateway.

## Condiciones de Entrega

El código de este repositorio se agrupa en dos carpetas, una para Python y otra para Golang. Los estudiantes deberán elegir **sólo uno** de estos lenguajes y realizar una implementación que funcione correctamente ante cambios en la multiplicidad de los controles (archivo de docker compose), los archivos de entrada y las implementaciones de las funciones de Suma y Comparación del `FruitItem`.

![ ](./imgs/mutabilidad.jpg  "Mutabilidad de Elementos")
*Fig. 2: Elementos mutables e inmutables*

A modo de referencia, en la *Figura 2* se marcan en tonos oscuros los elementos que los estudiantes no deben alterar y en tonos claros aquellos sobre los que tienen libertad de decisión.
Al momento de la evaluación y ejecución de las pruebas se **descartarán** o **reemplazarán** :

- Los archivos de entrada de la carpeta `datasets`.
- El archivo docker compose principal y los de la carpeta `scenarios`.
- Todos los archivos Dockerfile.
- Todo el código del cliente.
- Todo el código del gateway, salvo `message_handler`.
- La implementación del protocolo de comunicación externo y `FruitItem`.

Redactar un breve informe explicando el modo en que se coordinan las instancias de Sum y Aggregation, así como el modo en el que el sistema escala respecto a los clientes y a la cantidad de controles.
