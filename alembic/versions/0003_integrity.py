"""Validate legacy data, preserve ownership, then enforce the write contract.

Revision ID: 0003
Revises: 0002
"""

from collections import defaultdict
import math

from alembic import op
from email_validator import EmailNotValidError, validate_email
import sqlalchemy as sa


revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None
SEASONS = ("spring", "summer", "winter", "autumn")
TEXT = {
    "users": {"email": 254, "phone": 32, "fam": 100, "name": 100, "otc": 100},
    "pereval_added": {"title": 255, "beauty_title": 255, "other_titles": 1024, "connect": 2000},
    "images": {"title": 255},
}
REQUIRED = {
    "users": ("email", "fam", "name"),
    "coords": ("latitude", "longitude", "height"),
    "pereval_added": ("title", "date_added", "add_time", "user_id", "coord_id", "status"),
    "images": ("title", "date_added", "img"),
    "pereval_images": ("id_pereval", "id_image"),
}


def canonical_season(value):
    if value is None:
        return None
    value = value.strip().upper().translate(str.maketrans({"А": "A", "Б": "B"}))
    if value in {"N/K", "Н/К"}:
        return "n/k"
    if value in {"1A", "1B", "2A", "2B", "3A", "3B"}:
        return value
    raise ValueError()


def preflight(bind, tables):
    """No DDL/data writes until all rows pass. Report IDs/fields, never contact data."""
    problems = defaultdict(list)
    season_changes = []
    if bind.dialect.name == "postgresql":
        for table, fields in {"pereval_added": ("date_added", "add_time"),
                              "images": ("date_added",)}.items():
            for field in fields:
                if not tables[table].c[field].type.timezone:
                    raise RuntimeError(f"0003: {table}.{field} is timezone-naive; explicitly migrate its source timezone first")

    for name, fields in REQUIRED.items():
        for row in bind.execute(sa.select(tables[name])).mappings():
            for field in fields:
                if row[field] is None:
                    problems[f"{name}.{field}: null"].append(row["id"])
            for field, maximum in TEXT.get(name, {}).items():
                value = row[field]
                allowed = "\n\t" if field == "connect" else ""
                if value is not None and (not 1 <= len(value) <= maximum or not value.strip() or any(
                    (ord(char) < 32 or ord(char) == 127) and char not in allowed for char in value
                )):
                    problems[f"{name}.{field}: invalid text"].append(row["id"])
            if name == "users" and row["email"] is not None:
                try:
                    normalized = validate_email(row["email"], check_deliverability=False).normalized
                    if normalized != row["email"]:
                        raise ValueError()
                except (EmailNotValidError, ValueError):
                    problems["users.email: invalid/noncanonical email"].append(row["id"])
            if name == "coords":
                for field, low, high in (("latitude", -90, 90), ("longitude", -180, 180), ("height", 1, 10000)):
                    value = row[field]
                    if value is not None and (not math.isfinite(value) or not low <= value <= high
                                              or (field == "height" and value != int(value))):
                        problems[f"coords.{field}: invalid number"].append(row["id"])
            if name == "images" and row["img"] is not None and not row["img"]:
                problems["images.img: empty"].append(row["id"])
            if name == "pereval_added":
                for season in SEASONS:
                    field = f"level_{season}"
                    try:
                        value = canonical_season(row[field])
                        if value != row[field]:
                            season_changes.append((row["id"], field, value))
                    except ValueError:
                        problems[f"pereval_added.{field}: unknown difficulty"].append(row["id"])

    if problems:
        details = "; ".join(f"{field}: count={len(ids)}, ids={ids[:20]}" for field, ids in sorted(problems.items()))
        raise RuntimeError("0003 preflight failed; correct rows explicitly and retry. " + details)
    return season_changes


def preserve_ownership(bind, tables):
    # Keep the first owner; copy bytes/coordinates for every further reference.
    # Duplicate legacy pairs are also preserved as separate, identical photos.
    for owner_name, reference, source_name in (
        ("pereval_added", "coord_id", "coords"),
        ("pereval_images", "id_image", "images"),
    ):
        owners, source = tables[owner_name], tables[source_name]
        seen = set()
        for row in bind.execute(sa.select(owners).order_by(owners.c.id)).mappings().all():
            source_id = row[reference]
            if source_id in seen:
                data = dict(bind.execute(sa.select(source).where(source.c.id == source_id)).mappings().one())
                data.pop("id")
                copy_id = bind.execute(source.insert().values(**data)).inserted_primary_key[0]
                bind.execute(owners.update().where(owners.c.id == row["id"]).values({reference: copy_id}))
            seen.add(source_id)


def upgrade():
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        # Hold validation and transformation against concurrent legacy writers.
        bind.execute(sa.text("LOCK TABLE users, coords, pereval_added, images, pereval_images IN ACCESS EXCLUSIVE MODE"))
    metadata = sa.MetaData()
    metadata.reflect(bind, only=list(REQUIRED))
    tables = metadata.tables
    changes = preflight(bind, tables)
    for row_id, field, value in changes:
        table = tables["pereval_added"]
        bind.execute(table.update().where(table.c.id == row_id).values({field: value}))
    preserve_ownership(bind, tables)

    op.add_column("pereval_images", sa.Column("position", sa.Integer(), nullable=True))
    positions = defaultdict(int)
    links = sa.table("pereval_images", sa.column("id"), sa.column("id_pereval"), sa.column("position"))
    for row in bind.execute(sa.select(links.c.id, links.c.id_pereval).order_by(links.c.id)).mappings().all():
        bind.execute(links.update().where(links.c.id == row["id"]).values(position=positions[row["id_pereval"]]))
        positions[row["id_pereval"]] += 1

    for name, fields in REQUIRED.items():
        with op.batch_alter_table(name) as batch:
            for field in fields:
                column = tables[name].c[field]
                batch.alter_column(field, existing_type=column.type, nullable=False)
            for field, maximum in TEXT.get(name, {}).items():
                batch.alter_column(field, existing_type=sa.String(), type_=sa.String(maximum))
                batch.create_check_constraint(f"ck_{field}_length",
                                              f"length(trim({field})) >= 1 AND length({field}) <= {maximum}")
            if name == "coords":
                batch.alter_column("height", existing_type=sa.Float(), type_=sa.Integer(),
                                   postgresql_using="height::integer")
                for field, low, high in (("latitude", -90, 90), ("longitude", -180, 180), ("height", 1, 10000)):
                    batch.create_check_constraint(f"ck_{field}_range", f"{field} BETWEEN {low} AND {high}")
            if name == "pereval_added":
                batch.alter_column("status", existing_type=sa.String(), type_=sa.String(8))
                batch.create_unique_constraint("uq_pereval_added_coord_id", ["coord_id"])
                for season in SEASONS:
                    batch.create_check_constraint(f"ck_level_{season}_values",
                        f"level_{season} IN ('n/k', '1A', '1B', '2A', '2B', '3A', '3B')")
                batch.create_index("ix_pereval_added_user_id_id", ["user_id", "id"])
            if name == "images":
                batch.create_check_constraint("ck_img_nonempty", "length(img) > 0")
            if name == "pereval_images":
                batch.alter_column("position", existing_type=sa.Integer(), nullable=False)
                batch.create_unique_constraint("uq_pereval_images_id_image", ["id_image"])
                batch.create_unique_constraint("uq_pereval_image", ["id_pereval", "id_image"])
                batch.create_unique_constraint("uq_pereval_position", ["id_pereval", "position"])
                batch.create_check_constraint("ck_image_position", "position >= 0")


def downgrade():
    # Cloned legacy ownership and canonical categories deliberately remain intact.
    for name, fields in reversed(list(REQUIRED.items())):
        with op.batch_alter_table(name) as batch:
            if name == "pereval_images":
                for constraint in ("uq_pereval_images_id_image", "uq_pereval_image", "uq_pereval_position"):
                    batch.drop_constraint(constraint, type_="unique")
                batch.drop_constraint("ck_image_position", type_="check")
                batch.drop_column("position")
            if name == "images":
                batch.drop_constraint("ck_img_nonempty", type_="check")
            if name == "pereval_added":
                batch.drop_index("ix_pereval_added_user_id_id")
                batch.drop_constraint("uq_pereval_added_coord_id", type_="unique")
                for season in SEASONS:
                    batch.drop_constraint(f"ck_level_{season}_values", type_="check")
                batch.alter_column("status", existing_type=sa.String(8), type_=sa.String())
            if name == "coords":
                for field in ("latitude", "longitude", "height"):
                    batch.drop_constraint(f"ck_{field}_range", type_="check")
                batch.alter_column("height", existing_type=sa.Integer(), type_=sa.Float())
            for field, maximum in TEXT.get(name, {}).items():
                batch.drop_constraint(f"ck_{field}_length", type_="check")
                batch.alter_column(field, existing_type=sa.String(maximum), type_=sa.String())
            legacy_nonnull = {"users": {"email", "fam", "name"}, "coords": {"latitude", "longitude", "height"},
                              "images": {"img"}, "pereval_added": {"title", "status"}}
            for field in fields:
                if field not in legacy_nonnull.get(name, set()):
                    batch.alter_column(field, existing_type=sa.Integer() if field.endswith("_id") or field.startswith("id_")
                                       else sa.DateTime(timezone=True) if field in {"date_added", "add_time"} else sa.String(),
                                       nullable=True)
