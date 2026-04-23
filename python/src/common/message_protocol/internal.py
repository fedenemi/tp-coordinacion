import json


class MsgType:
    FRUIT_RECORD = 1
    END_OF_RECORDS = 2
    END_OF_RECORDS_NOTIF = 3
    FRUIT_TOP = 4


def serialize(fields: list) -> bytes:
    return json.dumps(fields).encode("utf-8")


def deserialize(data: bytes) -> list:
    return json.loads(data.decode("utf-8"))