from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

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

@app.post('/all')
def getdata(body: RequestBody):

    return 'yes'