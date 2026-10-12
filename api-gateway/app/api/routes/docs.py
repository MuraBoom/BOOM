import json

from fastapi import APIRouter
from starlette.responses import HTMLResponse

from app.core.config import settings

router = APIRouter()


@router.get("/docs", include_in_schema=False)
async def custom_swagger_ui_html() -> HTMLResponse:
    urls = [
        {"url": f"/{name}/openapi.json", "name": name.upper()}
        for name in settings.SERVICES.keys()
    ]

    urls_json = json.dumps(urls)

    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head>
    <link rel="stylesheet" type="text/css" href="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui.css" >
    <title>Микросервисы - Swagger UI</title>
    </head>
    <body>
    <div id="swagger-ui"></div>

    <script src="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui-bundle.js"> </script>
    <script src="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui-standalone-preset.js"> </script>

    <script>
    window.onload = function() {{
      const ui = SwaggerUIBundle({{
        urls: {urls_json},
        dom_id: '#swagger-ui',
        deepLinking: true,
        presets: [
          SwaggerUIBundle.presets.apis,
          SwaggerUIStandalonePreset
        ],
        plugins: [
          SwaggerUIBundle.plugins.DownloadUrl
        ],
        layout: "StandaloneLayout"
      }});
      window.ui = ui;
    }};
    </script>
    </body>
    </html>
    """
    return HTMLResponse(content=html_content)
