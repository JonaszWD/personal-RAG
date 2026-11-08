import logging

import uvicorn

from .api import create_app

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    # Binds inside the container; compose publishes it on host loopback only.
    uvicorn.run(create_app(), host="0.0.0.0", port=8000, access_log=False)  # noqa: S104
