from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from database.database import get_data

app = FastAPI()

origins = [
    "https://fiacrekpanoutrade.com",
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

@app.post('/process')
def getdata(body: RequestBody):

    

    return get_data()