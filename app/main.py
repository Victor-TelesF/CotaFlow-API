from fastapi import FastAPI

app = FastAPI(title="Python API")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
