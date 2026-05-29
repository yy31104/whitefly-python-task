from __future__ import annotations

from pathlib import Path


def test_nginx_forwards_or_stamps_request_id():
    config = Path("nginx/nginx.conf").read_text()

    assert "map $http_x_request_id $whitefly_request_id" in config
    assert '"~^[A-Za-z0-9._:-]{1,128}$" $http_x_request_id;' in config
    assert "default $request_id;" in config
    assert '"" $request_id;' in config
    assert config.count("proxy_set_header X-Request-ID $whitefly_request_id;") == 2
    assert '"request_id":"$whitefly_request_id"' in config
    assert '"request_uri":"$request_uri"' in config
