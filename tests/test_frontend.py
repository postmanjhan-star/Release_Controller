import re

from fastapi.testclient import TestClient


def test_frontend_is_served_from_root(client: TestClient) -> None:
    response = client.get("/")

    assert response.status_code == 200
    assert "Release Controller" in response.text
    assert '<div id="root"></div>' in response.text

    script_match = re.search(r'<script[^>]+src="([^"]+\.js)"', response.text)
    assert script_match is not None
    bundle = client.get(script_match.group(1))
    assert bundle.status_code == 200
    assert "release-detail" in bundle.text


def test_frontend_bundle_is_available(client: TestClient) -> None:
    index = client.get("/")
    script_match = re.search(r'<script[^>]+src="([^"]+\.js)"', index.text)
    style_match = re.search(r'<link[^>]+href="([^"]+\.css)"', index.text)

    assert script_match is not None
    assert style_match is not None
    assert client.get(script_match.group(1)).status_code == 200
    assert client.get(style_match.group(1)).status_code == 200


def test_unknown_api_path_never_falls_through_to_frontend_html(client: TestClient) -> None:
    response = client.get("/api/v1/not-a-real-endpoint")

    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/json")
    assert response.json() == {"detail": "API endpoint not found: /api/v1/not-a-real-endpoint"}
