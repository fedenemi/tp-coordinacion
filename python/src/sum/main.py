import os
import zlib
import signal
import logging

import pika

from common import message_protocol, fruit_item
from common.middleware import MessageMiddlewareExchangeRabbitMQ

ID = int(os.environ["ID"])
MOM_HOST = os.environ["MOM_HOST"]
INPUT_QUEUE = os.environ["INPUT_QUEUE"]
SUM_AMOUNT = int(os.environ["SUM_AMOUNT"])
AGGREGATION_AMOUNT = int(os.environ["AGGREGATION_AMOUNT"])
AGGREGATION_PREFIX = os.environ["AGGREGATION_PREFIX"]

_CONTROL_EXCHANGE = "sum_control_exchange"
_CONTROL_ROUTING_KEY = "sum_control_all"


class SumFilter:
    def __init__(self):

        self._conn = pika.BlockingConnection(pika.ConnectionParameters(host=MOM_HOST))
        self._ch = self._conn.channel()
        self._ch.basic_qos(prefetch_count=1)

        self._ch.queue_declare(queue=INPUT_QUEUE, durable=False)
        self._ch.basic_consume(
            queue=INPUT_QUEUE,
            on_message_callback=self._wrap(self._on_data),
        )

        self._ch.exchange_declare(exchange=_CONTROL_EXCHANGE, exchange_type="direct")
        ctrl_result = self._ch.queue_declare(queue="", exclusive=True)
        ctrl_queue = ctrl_result.method.queue
        self._ch.queue_bind(
            exchange=_CONTROL_EXCHANGE,
            queue=ctrl_queue,
            routing_key=_CONTROL_ROUTING_KEY,
        )
        self._ch.basic_consume(queue=ctrl_queue, on_message_callback=self._wrap(self._on_ctrl),)

        self._agg_exchanges = [
            MessageMiddlewareExchangeRabbitMQ(
                MOM_HOST,
                AGGREGATION_PREFIX,
                [f"{AGGREGATION_PREFIX}_{i}"],
            )
            for i in range(AGGREGATION_AMOUNT)
        ]

        # client_id -> { fruit_name -> FruitItem }
        self._sums = {}
        self._running = True
        signal.signal(signal.SIGTERM, self._on_sigterm)


    def _wrap(self, handler):
        def inner(ch, method, properties, body):
            def ack():
                ch.basic_ack(delivery_tag=method.delivery_tag)
            def nack():
                ch.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
            handler(body, ack, nack)
        return inner

    def _aggregator_for_fruit(self, client_id, fruit):
        key = f"{client_id}:{fruit}".encode()
        return zlib.crc32(key) % AGGREGATION_AMOUNT

    def _on_sigterm(self, signum, frame):
        logging.info("Sum %d: SIGTERM received", ID)
        self._running = False
        if self._ch.is_open:
            self._ch.stop_consuming()


    def _on_data(self, body, ack, nack):
        try:
            fields = message_protocol.internal.deserialize(body)
            msg_type = fields[0]

            if msg_type == message_protocol.internal.MsgType.FRUIT_RECORD:
                _, client_id, fruit, amount = fields
                if client_id not in self._sums:
                    self._sums[client_id] = {}
                bucket = self._sums[client_id]
                new = fruit_item.FruitItem(fruit, int(amount))
                bucket[fruit] = (bucket[fruit] + new) if fruit in bucket else new

            elif msg_type == message_protocol.internal.MsgType.END_OF_RECORDS:
                _, client_id = fields
                logging.info("Sum %d: EOF from queue for client %s, broadcasting notif",
                    ID, client_id)
                self._ch.basic_publish(
                    exchange=_CONTROL_EXCHANGE,
                    routing_key=_CONTROL_ROUTING_KEY,
                    body=message_protocol.internal.serialize([
                        message_protocol.internal.MsgType.END_OF_RECORDS_NOTIF,
                        client_id,
                    ]),
                )
            else:
                logging.warning("Sum %d: unknown message type %s", ID, msg_type)

            ack()

        except Exception as exc:
            logging.error("Sum %d: data handler error: %s", ID, exc)
            nack()
            if self._ch.is_open:
                self._ch.stop_consuming()

    def _on_ctrl(self, body, ack, nack):
        try:
            fields = message_protocol.internal.deserialize(body)
            if fields[0] != message_protocol.internal.MsgType.END_OF_RECORDS_NOTIF:
                logging.warning("Sum %d: unexpected control msg type", ID)
                ack()
                return

            _, client_id = fields
            self._flush(client_id)
            ack()

        except Exception as exc:
            logging.error("Sum %d: control handler error: %s", ID, exc)
            nack()
            if self._ch.is_open:
                self._ch.stop_consuming()


    def _flush(self, client_id):
        logging.info("Sum %d: flushing client %s", ID, client_id)

        for item in self._sums.pop(client_id, {}).values():
            agg_idx = self._aggregator_for_fruit(client_id, item.fruit)
            self._agg_exchanges[agg_idx].send(
                message_protocol.internal.serialize([
                    message_protocol.internal.MsgType.FRUIT_RECORD,
                    client_id,
                    item.fruit,
                    item.amount,
                ])
            )

        eof_msg = message_protocol.internal.serialize([message_protocol.internal.MsgType.END_OF_RECORDS, client_id,])
        for ex in self._agg_exchanges:
            ex.send(eof_msg)


    def start(self):
        logging.info("Sum %d: starting event loop", ID)
        try:
            self._ch.start_consuming()
        except pika.exceptions.AMQPConnectionError as exc:
            if self._running:
                logging.error("Sum %d: connection lost: %s", ID, exc)
                return 1
        finally:
            try:
                if self._ch.is_open:
                    self._ch.close()
                if self._conn.is_open:
                    self._conn.close()
                for ex in self._agg_exchanges:
                    ex.close()
            except Exception as exc:
                logging.error("Sum %d: cleanup error: %s", ID, exc)
        return 0


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [sum-%(process)d] %(levelname)s %(message)s",)
    logging.getLogger("pika").setLevel(logging.WARNING)
    return SumFilter().start()


if __name__ == "__main__":
    exit(main())