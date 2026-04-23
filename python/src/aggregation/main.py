import os
import signal
import logging

from common import message_protocol, fruit_item
from common.middleware import (
    MessageMiddlewareExchangeRabbitMQ,
    MessageMiddlewareQueueRabbitMQ,
)

ID = int(os.environ["ID"])
MOM_HOST = os.environ["MOM_HOST"]
OUTPUT_QUEUE = os.environ["OUTPUT_QUEUE"]
SUM_AMOUNT = int(os.environ["SUM_AMOUNT"])
AGGREGATION_AMOUNT = int(os.environ["AGGREGATION_AMOUNT"])
AGGREGATION_PREFIX = os.environ["AGGREGATION_PREFIX"]
TOP_SIZE = int(os.environ["TOP_SIZE"])


class AggregationFilter:
    def __init__(self):
        self._input = MessageMiddlewareExchangeRabbitMQ(MOM_HOST, AGGREGATION_PREFIX, [f"{AGGREGATION_PREFIX}_{ID}"])
        self._output = MessageMiddlewareQueueRabbitMQ(MOM_HOST, OUTPUT_QUEUE)

        self._data = {}
        self._eof_count = {}

        self._running = True
        signal.signal(signal.SIGTERM, self._on_sigterm)

    def _on_sigterm(self, signum, frame):
        logging.info("Aggregation %d: SIGTERM received", ID)
        self._running = False
        self._input.stop_consuming()

    def _on_fruit_record(self, client_id, fruit, amount):
        if client_id not in self._data:
            self._data[client_id] = {}
        bucket = self._data[client_id]
        new = fruit_item.FruitItem(fruit, int(amount))
        bucket[fruit] = (bucket[fruit] + new) if fruit in bucket else new

    def _on_eof(self, client_id):
        count = self._eof_count.get(client_id, 0) + 1
        self._eof_count[client_id] = count
        logging.info("Aggregation %d: EOF %d/%d for client %s", ID, count, SUM_AMOUNT, client_id)

        if count < SUM_AMOUNT:
            return

        # All partial sums received
        client_data = self._data.pop(client_id, {})
        del self._eof_count[client_id]

        ranked = sorted(client_data.values(), reverse=True)[:TOP_SIZE]
        partial_top = [[item.fruit, item.amount] for item in ranked]

        logging.info("Aggregation %d: partial top for client %s: %s", ID, client_id, partial_top)
        self._output.send(message_protocol.internal.serialize([message_protocol.internal.MsgType.FRUIT_TOP, client_id] + partial_top))

    def _dispatch(self, body, ack, nack):
        try:
            fields = message_protocol.internal.deserialize(body)
            msg_type = fields[0]

            if msg_type == message_protocol.internal.MsgType.FRUIT_RECORD:
                _, client_id, fruit, amount = fields
                self._on_fruit_record(client_id, fruit, amount)
            elif msg_type == message_protocol.internal.MsgType.END_OF_RECORDS:
                _, client_id = fields
                self._on_eof(client_id)
            else:
                logging.warning("Aggregation %d: unknown msg type %s", ID, msg_type)

            ack()

        except Exception as exc:
            logging.error("Aggregation %d: error: %s", ID, exc)
            nack()
            self._input.stop_consuming()

    def start(self):
        self._input.start_consuming(self._dispatch)
        try:
            self._input.close()
            self._output.close()
        except Exception as exc:
            logging.error("Aggregation %d: close error: %s", ID, exc)
        return 1 if self._running else 0


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [aggregation-%(process)d] %(levelname)s %(message)s")
    logging.getLogger("pika").setLevel(logging.WARNING)
    return AggregationFilter().start()


if __name__ == "__main__":
    exit(main())