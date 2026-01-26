from sqlalchemy.orm import Session
from schemas import PerevalCreate
from models import PerevalAdded, User, Coords, Image, PerevalImages
import base64


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
