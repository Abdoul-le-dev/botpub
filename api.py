from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from database.database import get_data, get_data_users

app = FastAPI()

origins = [
    "http://127.0.0.1:8000",
    "http://localhost:8000",
]


app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class RequestBody(BaseModel):
    text: str

class RequestData(BaseModel):
    userId: int


@app.get("/")
def health():
    return {"status": "ok"}


@app.post('/process')
async def getdata(data: RequestBody):
    return await get_data(data.text)


@app.post('/user')
async def get_data_user(data: RequestData):
    return await get_data_users(data.userId)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)