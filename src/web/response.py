"""Middleware for a uniform API response format."""
import json

from starlette.types import ASGIApp, Receive, Scope, Send


class ResponseWrapperMiddleware:
    """Wrap every /api/ response in the standard format: {code, success, data, message}.

    Pure ASGI, avoiding BaseHTTPMiddleware's known streaming hang.
    """

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] != "http" or not scope.get("path", "").startswith("/api/"):
            await self.app(scope, receive, send)
            return

        status_code = 200
        response_headers: list[tuple[bytes, bytes]] = []
        body_parts: list[bytes] = []
        # SSE (text/event-stream) responses must pass through chunk by chunk:
        # buffering would turn the stream into a single response and the frontend would miss incremental events
        passthrough = False

        async def capture_send(message):
            nonlocal status_code, response_headers, passthrough
            if message["type"] == "http.response.start":
                status_code = message["status"]
                response_headers = list(message.get("headers", []))
                for key, value in response_headers:
                    if key.lower() == b"content-type" and b"text/event-stream" in value.lower():
                        passthrough = True
                        break
                if passthrough:
                    await send(message)
            elif message["type"] == "http.response.body":
                if passthrough:
                    await send(message)
                else:
                    body_parts.append(message.get("body", b""))

        await self.app(scope, receive, capture_send)

        if passthrough:
            # The streaming response has already been forwarded as it was generated
            return

        # Is it a JSON response?
        content_type = ""
        for key, value in response_headers:
            if key.lower() == b"content-type":
                content_type = value.decode()
                break

        body = b"".join(body_parts)

        if "application/json" not in content_type:
            # Not JSON: return it unchanged
            await send({"type": "http.response.start", "status": status_code, "headers": response_headers})
            await send({"type": "http.response.body", "body": body})
            return

        try:
            original_data = json.loads(body)
        except (json.JSONDecodeError, ValueError):
            await send({"type": "http.response.start", "status": status_code, "headers": response_headers})
            await send({"type": "http.response.body", "body": body})
            return

        if 200 <= status_code < 300:
            # The business layer may return success/code/message explicitly in a 2xx
            if isinstance(original_data, dict) and "success" in original_data:
                success = bool(original_data.get("success"))
                raw_code = original_data.get("code")
                try:
                    code = int(raw_code) if raw_code is not None else (0 if success else 1)
                except Exception:
                    code = 0 if success else 1
                if success and code != 0:
                    code = 0
                if (not success) and code == 0:
                    code = 1

                if success:
                    # Uniform success: message is empty
                    message = ""
                    data = original_data.get("data")
                    if data is None:
                        data = {
                            k: v
                            for k, v in original_data.items()
                            if k not in ("code", "success", "message")
                        }
                else:
                    # Uniform failure: data is empty
                    message = str(original_data.get("message") or "failed")
                    data = None

                wrapped = {
                    "code": code,
                    "success": success,
                    "data": data,
                    "message": message,
                }
            else:
                # 2xx counts as success by default
                wrapped = {"code": 0, "success": True, "data": original_data, "message": ""}
        else:
            detail = original_data.get("detail", original_data) if isinstance(original_data, dict) else original_data
            code = status_code
            message: str
            if isinstance(detail, dict):
                raw_code = detail.get("code")
                try:
                    if raw_code is not None:
                        code = int(raw_code)
                except Exception:
                    code = status_code
                message = str(
                    detail.get("message")
                    or detail.get("detail")
                    or json.dumps(detail, ensure_ascii=False)
                )
            else:
                message = detail if isinstance(detail, str) else json.dumps(detail, ensure_ascii=False)
            if code == 0:
                code = status_code if status_code != 0 else 1
            wrapped = {"code": code, "success": False, "data": None, "message": message}

        new_body = json.dumps(wrapped, ensure_ascii=False).encode()

        # Update the content-length header
        new_headers = []
        for key, value in response_headers:
            if key.lower() == b"content-length":
                new_headers.append((key, str(len(new_body)).encode()))
            else:
                new_headers.append((key, value))

        await send({"type": "http.response.start", "status": status_code, "headers": new_headers})
        await send({"type": "http.response.body", "body": new_body})
