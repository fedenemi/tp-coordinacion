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