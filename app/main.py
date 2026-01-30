from fastapi import FastAPI, Depends, HTTPException, status
from sqlalchemy.orm import Session
from app.database import get_db, engine
from app.models import Base
from app.schemas import (PerevalCreate, SubmitResponse, ErrorResponse, PerevalResponse, PerevalList, PerevalUpdate,
                     UpdateResponse)
from app.crud import PerevalRepository
from typing import List



Base.metadata.create_all(bind=engine)

app = FastAPI(title="FSTR Pereval API", version="1.0", description="API для работы с горными перевалами")

@app.post(
    "/submitData/",
    response_model=SubmitResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Предложить новый перевал",
    response_description="ID созданного перевала",
    responses={
        400: {"model": ErrorResponse, "description": "Некорректные данные"},
        500: {"model": ErrorResponse, "description": "Внутренняя ошибка сервера"}
    }
)
def submit_data(pereval: PerevalCreate, db: Session = Depends(get_db)):
    """
    Добавляет информацию о новом перевале в базу данных.

    - **beauty_title**: Красивое название (опционально)
    - **title**: Основное название (обязательно)
    - **user**: Данные пользователя (email, ФИО, телефон)
    - **coords**: Координаты перевала (широта, долгота, высота)
    - **level**: Уровни сложности по сезонам
    - **images**: Изображения в base64 (опционально)

    Статус перевала автоматически устанавливается в 'new'.
    """
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


@app.get("/submitData/{pereval_id}", response_model=PerevalResponse,
         response_description="Полная информация о перевале",
         responses={404:{"model": ErrorResponse, "description": "Перевал не найден"}})
def get_detail_data(pereval_id: int, db: Session = Depends(get_db)):
    """
    Возвращает полную информацию о перевале по его ID.

    Включает:
    - Основные данные перевала
    - Данные пользователя
    - Координаты
    - Уровни сложности
    - Изображения (если есть)
    - Текущий статус модерации
    """
    return PerevalRepository.get_pereval_or_404(db, pereval_id)


@app.patch("/submitData/{pereval_id}", response_model=UpdateResponse,
           summary="Обновить перевал", response_description="Результат обновления",
           responses={
               400: {"model": ErrorResponse, "description": "Некорректные данные"},
               404: {"model": ErrorResponse, "description": "Перевал не найден"}
           })
def update_data(pereval_id :int, update_data: PerevalUpdate,  db: Session = Depends(get_db)):
    """
    Обновляет информацию о перевале.

    **Ограничения:**
    - Можно редактировать только перевалы со статусом 'new'
    - Нельзя изменять данные пользователя (email, ФИО, телефон)

    **Можно обновлять:**
    - Название и описания
    - Координаты
    - Уровни сложности
    - Изображения

    Возвращает:
    - **state**: 1 - успех, 0 - ошибка
    - **message**: Описание результата
    """
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


@app.get("/submitData/", response_model=List[PerevalList],
         summary="Получить перевалы по email", response_description="Список перевалов",
         responses={400:{"model": ErrorResponse, "description": "Некорректный email"}})
def get_email_data(user__email: str, db: Session = Depends(get_db)):
    """
    Возвращает список всех перевалов, отправленных указанным пользователем.

    Для каждого перевала возвращается:
    - ID и название
    - Текущий статус
    - Дата добавления
    - Координаты (широта, долгота, высота)
    - Email пользователя
    """
    return PerevalRepository.get_perevals_by_email(db, user__email)