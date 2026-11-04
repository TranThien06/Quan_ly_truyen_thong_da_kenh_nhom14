class ApiError(Exception):
    def __init__(self, status, code, message):
        self.status, self.code, self.message = status, code, message
        super().__init__(message)


def check(condition, status, code, message):
    if not condition:
        raise ApiError(status, code, message)
