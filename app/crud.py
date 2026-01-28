from sqlalchemy.orm import Session, joinedload
from schemas import PerevalCreate, PerevalResponse
from models import PerevalAdded, User, Coords, Image, PerevalImages
import base64
from fastapi import HTTPException


class PerevalRepository:
    @staticmethod
    def create_pereval(db:Session, pereval_data: PerevalCreate) -> PerevalAdded:
        user = db.query(User).filter(User.email == pereval_data.user.email).first()

        if not user:
            user = User(
                email=pereval_data.user.email,
                phone=pereval_data.user.phone,
                fam=pereval_data.user.fam,
                name=pereval_data.user.name,
                otc=pereval_data.user.otc
            )
            db.add(user)
            db.flush()

        coords = Coords(
            latitude=pereval_data.coords.latitude,
            longitude=pereval_data.coords.longitude,
            height=pereval_data.coords.height
        )
        db.add(coords)
        db.flush()

        pereval = PerevalAdded(
            beauty_title=pereval_data.beauty_title,
            title=pereval_data.title,
            other_titles=pereval_data.other_titles,
            connect=pereval_data.connect,
            add_time=pereval_data.add_time,
            user_id=user.id,
            coord_id=coords.id,
            level_spring=pereval_data.level.spring,
            level_summer=pereval_data.level.summer,
            level_autumn=pereval_data.level.autumn,
            level_winter=pereval_data.level.winter,
            status="new"
        )
        db.add(pereval)
        db.flush()

        for image_data in pereval_data.images:
            try:
                img_bytes = base64.b64decode(image_data.img)
            except:
                img_bytes = image_data.img.encode("utf-8")

            image = Image(
                img=img_bytes,
                title=image_data.title
            )
            db.add(image)
            db.flush()

            pereval_image = PerevalImages(
                id_pereval=pereval.id,
                id_image=image.id
            )
            db.add(pereval_image)

        db.commit()
        db.refresh(pereval)

        return pereval


    @staticmethod
    def get_pereval_or_404(db:Session, pereval_id:int) -> PerevalAdded:
        pereval = db.query(PerevalAdded).options(
            joinedload(PerevalAdded.user),
            joinedload(PerevalAdded.user),
            joinedload(PerevalAdded.coords),
            joinedload(PerevalAdded.images)
        ).filter(PerevalAdded.id == pereval_id).first()
        if not pereval:
            raise HTTPException(status_code=404, detail="Pereval not found")

        images_data = []
        for image in pereval.images:
            try:
                img_base64 = base64.b64encode(image.img).decode("utf-8")
            except:
                img_base64 = ""

            images_data.append({
                "img": img_base64,
                "title": image.title
            })

        response_data = {
            "id": pereval.id,
            "status": pereval.status,
            "date_added": pereval.date_added,
            "beauty_title": pereval.beauty_title,
            "title": pereval.title,
            "other_titles": pereval.other_titles,
            "connect": pereval.connect,
            "add_time": pereval.add_time,
            "user": {
                "email": pereval.user.email,
                "phone": pereval.user.phone,
                "fam": pereval.user.fam,
                "name": pereval.user.name,
                "otc": pereval.user.otc
            },
            "coords": {
                "latitude": pereval.coords.latitude,
                "longitude": pereval.coords.longitude,
                "height": pereval.coords.height
            },
            "level": {
                "spring": pereval.level_spring,
                "summer": pereval.level_summer,
                "autumn": pereval.level_autumn,
                "winter": pereval.level_winter
            },
            "images": images_data
        }

        return PerevalResponse(**response_data)

    @staticmethod
    def update_pereval(db: Session, pereval_id:int, update_data:dict) -> dict:
        pereval = db.query(PerevalAdded).filter(PerevalAdded.id == pereval_id).first()

        if not pereval:
            return {"state": 0, "message": "Перевал не найден"}

        if pereval.status != "new":
            return {"status": 0, "message": f"Статус записи {pereval.status}. Редактирование невозможно"}

        try:
            if "coords" in update_data:
                coords = db.query(Coords).filter(Coords.id == pereval.coord_id).first()
                if coords:
                    coords.latitude = update_data["coords"].get("latitude", coords.latitude)
                    coords.longitude = update_data["coords"].get("longitude", coords.longitude)
                    coords.height = update_data["coords"].get("height", coords.height)

            if "title" in update_data:
                pereval.title = update_data["title"]

            if "beauty_title" in update_data:
                pereval.beauty_title = update_data["beauty_title"]

            if "other_titles" in update_data:
                pereval.other_titles = update_data["other_titles"]

            if "connect" in update_data:
                pereval.connect = update_data["connect"]

            if "add_time" in update_data:
                pereval.add_time = update_data["add_time"]

            if "level" in update_data:
                level = update_data["level"]
                if "sptring" in level:
                    pereval.level_sprint = level["spring"]
                if "summer" in level:
                    pereval.level_summer = level["summer"]
                if "winter" in level:
                    pereval.level_sprint = level["winter"]
                if "autumn" in level:
                    pereval.level_summer = level["autumn"]

            if "images" in update_data:
                db.query(PerevalImages).filter(PerevalImages.id_pereval == pereval_id).delete()

                for img_data in update_data['images']:
                    img_bytes = base64.b64decode(img_data['img'])
                    image = Image(img=img_bytes, title=img_data['title'])
                    db.add(image)
                    db.flush()

                    link = PerevalImages(id_pereval=pereval_id, id_image=image.id)
                    db.add(link)

            db.commit()
            return {"state": 1, "message": "Обновлено"}

        except Exception as e:
            db.rollback()
            return {"state": 0, "message":f"Ошибка: {e}"}

    @staticmethod
    def get_perevals_by_email(db: Session, email:str) -> list:
        user = db.query(User).filter(User.email == email).first()
        if not user:
            return []

        perevals = db.query(PerevalAdded).options(
            joinedload(PerevalAdded.user),
            joinedload(PerevalAdded.coords),
            joinedload(PerevalAdded.images),
        ).filter(PerevalAdded.user_id == user.id).all()

        result = []

        for pereval in perevals:
            result.append({
                "id": pereval.id,
                "title": pereval.title,
                "status": pereval.status,
                "date_added": pereval.date_added,
                "user_email": pereval.user.email,
                "latitude": pereval.coords.latitude,
                "longitude": pereval.coords.longitude,
                "height": pereval.coords.height
            })

        return result