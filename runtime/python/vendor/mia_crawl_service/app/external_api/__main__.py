from __future__ import annotations

import os

import uvicorn


def main() -> None:
    uvicorn.run(
        'app.external_api.app:create_app',
        factory=True,
        host=os.getenv('MIA_EXTERNAL_API_HOST', '127.0.0.1'),
        port=int(os.getenv('MIA_EXTERNAL_API_PORT', '8080')),
        proxy_headers=True,
        forwarded_allow_ips=os.getenv('MIA_EXTERNAL_API_FORWARDED_ALLOW_IPS', '127.0.0.1'),
    )


if __name__ == '__main__':
    main()
