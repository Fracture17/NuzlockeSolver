
class ActionInfo:
    def serialize(self):
        raise NotImplementedError

    @classmethod
    def deserialize(cls, data):
        raise NotImplementedError
