"""Area metadata endpoints. HTTP requests never execute a builder."""
from fastapi import APIRouter,HTTPException
from pydantic import BaseModel,ConfigDict
from sim import areas
router=APIRouter(prefix='/areas',tags=['areas'])
class Selection(BaseModel):
    model_config=ConfigDict(extra='forbid')
    bbox:tuple[float,float,float,float]|None=None
    center:tuple[float,float]|None=None
    size_km:float|None=None

def invoke(call):
    try:return call()
    except ValueError as error:raise HTTPException(422,str(error)) from error
    except KeyError as error:raise HTTPException(404,'Area or model not found') from error
    except RuntimeError as error:raise HTTPException(503,str(error)) from error

@router.get('/catalog')
def catalog():return invoke(areas.catalog)
@router.get('/regions')
def regions():
    from sim.regions import public_regions
    return invoke(public_regions)
@router.post('/select')
def select(request:Selection):return invoke(lambda:areas.select_area(request.model_dump(exclude_none=True)))
@router.get('/jobs/{job_id}')
def job(job_id:str):return invoke(lambda:areas.public_status(areas.get_store().get(job_id)))
@router.get('/models/{model_id}')
def model(model_id:str):
    def read():
        result=areas.get_store().model(model_id)
        if result is None:raise KeyError(model_id)
        return result
    return invoke(read)
@router.get('/{area_id}/status')
def status(area_id:str):return invoke(lambda:areas.public_status(areas.get_store().latest(area_id)))
@router.get('/{area_id}/descriptor')
def descriptor(area_id:str):
    def read():
        value=areas.public_status(areas.get_store().latest(area_id))
        if value['status']!='ready':raise HTTPException(409,detail=value)
        return value['descriptor']
    return invoke(read)
