"""The errors the store raises (the web layer turns them into 403, 404, 409 and form messages)."""


class StoreError(Exception):
    pass

class NotFound(StoreError):
    pass

class Forbidden(StoreError):
    pass

class Conflict(StoreError):
    pass

class ValidationError(StoreError):
    def __init__(self, errors):
        super().__init__("; ".join(f"{k}: {v}" for k, v in errors.items()))
        self.errors = errors
