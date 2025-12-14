from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from database.database import get_data , get_data_users

app = FastAPI()

origins = [
    "https://fiacrekpanoutrade.com",
    "http://127.0.0.1:8000"
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,          # ou ["*"] en dev
    allow_credentials=True,
    allow_methods=["*"],            # GET, POST, OPTIONS, etc.
    allow_headers=["*"],            # Autoriser tous les headers
)

class RequestBody(BaseModel):

    text : str

class RequestData(BaseModel):
    userId: int

@app.post('/process')
async def getdata():
    data = await get_data()

    print(data)

    return data

@app.post('/user')
async def get_data_user(data:RequestData):
    print(data.userId)
    data_user = await get_data_users(data.userId)

    print(data_user)

    return data_user