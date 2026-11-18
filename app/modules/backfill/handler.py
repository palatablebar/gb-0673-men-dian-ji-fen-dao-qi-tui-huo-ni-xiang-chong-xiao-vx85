from fastapi import APIRouter, Request, Body
from app.engine import evaluate
from app.web import render
from app import store
router=APIRouter()
@router.get('/backfill')
def page(request:Request):
    return render(request,'backfill')
@router.post('/api/backfill')
def calculate(payload:dict=Body(default={})):
    return evaluate(payload)
