import os
import signal
import logging

from common import message_protocol
from common.middleware import MessageMiddlewareQueueRabbitMQ

MOM_HOST = os.environ["MOM_HOST"]
INPUT_QUEUE = os.environ["INPUT_QUEUE"]
OUTPUT_QUEUE = os.environ["OUTPUT_QUEUE"]
AGGREGATION_AMOUNT = int(os.environ["AGGREGATION_AMOUNT"])
TOP_SIZE = int(os.environ["TOP_SIZE"])


class JoinFilter:
    def __init__(self):
        self._input = MessageMiddlewareQueueRabbitMQ(MOM_HOST, INPUT_QUEUE)
        self._output = MessageMiddlewareQueueRabbitMQ(MOM_HOST, OUTPUT_QUEUE)

        # client_id -> lista de resultados parciales [[fruit, amount], ...]
        self._pending = {}

        self._running = True
        signal.signal(signal.SIGTERM, self._on_sigterm)

    def _on_sigterm(self, signum, frame):
        logging.info("Join: SIGTERM received")
        self._running = False
        self._input.stop_consuming()

    def _merge(self, partial_tops):
        totals = {}
        for partial_top in partial_tops:
            for fruit, amount in partial_top:
                totals[fruit] = totals.get(fruit, 0) + amount
        ranked = sorted(totals.items(), key=lambda kv: kv[1], reverse=True)
        return [[f, a] for f, a in ranked[:TOP_SIZE]]

    def _dispatch(self, body, ack, nack):
        try:
            fields = message_protocol.internal.deserialize(body)
            if fields[0] != message_protocol.internal.MsgType.FRUIT_TOP:
                logging.warning("Join: unexpected message type %s", fields[0])
                ack()
                return

            client_id = fields[1]
            # fields[2:] -> [[fruit, amount], ...]
            partial_top = fields[2:]

            if client_id not in self._pending:
                self._pending[client_id] = []
            self._pending[client_id].append(partial_top)

            received = len(self._pending[client_id])
            logging.info("Join: partial top %d/%d for client %s",received, AGGREGATION_AMOUNT, client_id)

            if received == AGGREGATION_AMOUNT:
                final_top = self._merge(self._pending.pop(client_id))
                logging.info("Join: final top for client %s: %s", client_id, final_top)
                self._output.send(message_protocol.internal.serialize([message_protocol.internal.MsgType.FRUIT_TOP, client_id] + final_top))

            ack()

        except Exception as exc:
            logging.error("Join: error processing message: %s", exc)
            nack()
            self._input.stop_consuming()

    def start(self):
        self._input.start_consuming(self._dispatch)
        try:
            self._input.close()
            self._output.close()
        except Exception as exc:
            logging.error("Join: close error: %s", exc)
        return 0


def main():
    logging.basicConfig(level=logging.INFO,format="%(asctime)s [join-%(process)d] %(levelname)s %(message)s")
    logging.getLogger("pika").setLevel(logging.WARNING)
    return JoinFilter().start()


if __name__ == "__main__":
    exit(main())