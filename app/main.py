from fastapi import FastAPI, Depends, HTTPException, status
from sqlalchemy.orm import Session
from database import get_db, engine
from models import Base
from schemas import (PerevalCreate, SubmitResponse, ErrorResponse, PerevalResponse, PerevalList, PerevalUpdate,
                     UpdateResponse)
from crud import PerevalRepository
from typing import List



Base.metadata.create_all(bind=engine)

app = FastAPI(title="FSTR Pereval API", version="1.0")

@app.post(
    "/submitData/",
    response_model=SubmitResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        400: {"model": ErrorResponse, "description": "Некорректные данные"},
        500: {"model": ErrorResponse, "description": "Внутренняя ошибка сервера"}
    }
)
def submit_data(pereval: PerevalCreate, db: Session = Depends(get_db)):
    try:
        db_pereval = PerevalRepository.create_pereval(db, pereval)

        return SubmitResponse(
            id=db_pereval.id,
            message="Отправлено успешно"
        )

    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail={
                                "status":400,
                                "message": "Некорректные данные",
                                "detail": str(e)})

    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail={
                                "status":500,
                                "message": "Внутренняя ошибка сервера",
                                "detail": str(e)})


@app.get("/submitData/{pereval_id}", response_model=PerevalResponse)
def get_detail_data(pereval_id: int, db: Session = Depends(get_db)):
    return PerevalRepository.get_pereval_or_404(db, pereval_id)


@app.patch("/submitData/{pereval_id}", response_model=UpdateResponse,
           responses={
               400: {"model": ErrorResponse, "description": "Некорректные данные"},
               404: {"model": ErrorResponse, "description": "Перевал не найден"}
           })
def update_data(pereval_id :int, update_data: PerevalUpdate,  db: Session = Depends(get_db)):
    try:
        result = PerevalRepository.update_pereval(db, pereval_id, update_data)

        if result["state"] == 0:
            raise HTTPException(
                status_code=status.HTTP_204_NO_CONTENT,
                detail={
                    "status": 400,
                    "message": result["message"]
                }
            )

        return UpdateResponse(**result)

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                            detail={
                                "status": 500,
                                "message": "Внутренняя ошибка сервера",
                                "detail": str(e)
                            })


@app.get("/submitData/", response_model=List[PerevalList])
def get_email_data(user__email: str, db: Session = Depends(get_db)):
    return PerevalRepository.get_perevals_by_email(db, user__email)