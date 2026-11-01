"""Narrow Part D router. Native API assembly mounts router without replay changes."""
from fastapi import APIRouter,HTTPException
from pydantic import BaseModel,ConfigDict,Field
from sim import product

router=APIRouter(prefix='/product',tags=['product'])
class RunRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    module:str|None=None
    preset_id:str|None=None
    river_plus_m:float|None=None
    point:tuple[float,float]|None=None
    bank_source:str|None=None
    magnitude:float|None=None
    building_index:int|None=None
    wind_direction_deg:float|None=None
    wind_speed_m_s:float|None=None
    seed:int=Field(default=0,ge=0,le=2147483647)
    replay:bool|None=None

@router.get('/catalog')
def catalog():return product.catalog()

@router.post('/run')
def run(request:RunRequest):
    try:return product.admit(request.model_dump(exclude_none=True))
    except product.LocalComputeDisabled as error:raise HTTPException(503,str(error)) from error
    except (ValueError,KeyError) as error:raise HTTPException(422,str(error)) from error

@router.get('/runs/{run_id}/status')
def status(run_id:str):
    try:return product.status(run_id)
    except ValueError as error:raise HTTPException(404,str(error)) from error
