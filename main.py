from fastapi import FastAPI

from presentation.http.routers.health import router as health_router

app = FastAPI(title="CotaFlow API")
app.include_router(health_router)
