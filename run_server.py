import uvicorn
from api_test import app

if __name__ == "__main__":
    uvicorn.run(
        app,
        host="127.0.0.1",
        port=8000,
        log_level="debug",
        lifespan="off"  # evita problemas de ciclo de vida en Windows
    )
