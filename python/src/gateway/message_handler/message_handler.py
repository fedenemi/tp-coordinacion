import uuid
from common import message_protocol

class MessageHandler:

    def __init__(self):
        self._client_id = str(uuid.uuid4())

    def serialize_data_message(self, message):
        fruit, amount = message
        return message_protocol.internal.serialize([
            message_protocol.internal.MsgType.FRUIT_RECORD,
            self._client_id,
            fruit,
            int(amount),
        ])

    def serialize_eof_message(self, _message):
        return message_protocol.internal.serialize([message_protocol.internal.MsgType.END_OF_RECORDS, self._client_id])

    def deserialize_result_message(self, message):
        try:
            fields = message_protocol.internal.deserialize(message)
            if (fields[0] == message_protocol.internal.MsgType.FRUIT_TOP and fields[1] == self._client_id):
                return fields[2:]
            return None
        except Exception:
            return None