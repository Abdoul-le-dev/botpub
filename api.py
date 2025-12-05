from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI()


class RequestBody(BaseModel):

    text : str

@app.post('/all')
def getdata(body: RequestBody):

    return 'yes'