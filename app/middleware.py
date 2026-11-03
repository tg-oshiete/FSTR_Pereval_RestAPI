"""Enforce the byte budget before FastAPI parses JSON or obtains a DB session."""

from app.errors import error_response, internal_error_response
from app.validation import DEFAULT_LIMITS, UploadLimits, upload_limits


class RequestPolicyMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        settings = getattr(scope["app"].state, "settings", None)
        limits = UploadLimits.from_settings(settings) if settings is not None else DEFAULT_LIMITS
        token = upload_limits.set(limits)
        response_started = False

        async def track_send(message):
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            for name, value in scope.get("headers", []):
                if name.lower() == b"content-length":
                    try:
                        declared_size = int(value)
                    except ValueError:
                        continue
                    if declared_size > limits.max_request_bytes:
                        return await error_response("PAYLOAD_TOO_LARGE")(scope, receive, send)

            chunks = []
            size = 0
            while True:
                message = await receive()
                if message["type"] == "http.disconnect":
                    return
                chunk = message.get("body", b"")
                size += len(chunk)
                if size > limits.max_request_bytes:
                    return await error_response("PAYLOAD_TOO_LARGE")(scope, receive, send)
                chunks.append(chunk)
                if not message.get("more_body", False):
                    break
            body = b"".join(chunks)
            delivered = False

            async def replay():
                nonlocal delivered
                if not delivered:
                    delivered = True
                    return {"type": "http.request", "body": body, "more_body": False}
                return await receive()

            await self.app(scope, replay, track_send)
        except Exception as exc:
            # Handle before ServerErrorMiddleware, which otherwise re-raises to
            # Uvicorn and exposes SQL/parameters in its default traceback logger.
            response = internal_error_response(exc)
            if not response_started:
                await response(scope, receive, send)
        finally:
            upload_limits.reset(token)
