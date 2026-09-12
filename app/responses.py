import json

from fastapi.responses import JSONResponse


class PrettyJSONResponse(JSONResponse):
    """Resposta JSON legível também quando consultada diretamente pelo curl."""

    def render(self, content) -> bytes:
        return json.dumps(
            content,
            ensure_ascii=False,
            allow_nan=False,
            indent=2,
            separators=(",", ": "),
        ).encode("utf-8")
