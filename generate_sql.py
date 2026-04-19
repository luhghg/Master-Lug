"""
Генерує schema.sql з SQLAlchemy моделей БЕЗ підключення до БД.
Потім SQL виконується всередині Docker через psql.
"""
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable

from app.models.base import Base
import app.models  # noqa — реєструє всі моделі

dialect = postgresql.dialect()
statements = []

# 0. DROP старого (порядок важливий: спочатку залежні таблиці)
statements += [
    "DROP TABLE IF EXISTS applications CASCADE",
    "DROP TABLE IF EXISTS bot_blocked_users CASCADE",
    "DROP TABLE IF EXISTS jobs CASCADE",
    "DROP TABLE IF EXISTS registered_bots CASCADE",
    "DROP TABLE IF EXISTS users CASCADE",
    "DROP TYPE IF EXISTS applicationstatus CASCADE",
    "DROP TYPE IF EXISTS jobstatus CASCADE",
    "DROP TYPE IF EXISTS jobtype CASCADE",
    "DROP TYPE IF EXISTS botniche CASCADE",
]

# 1. Спочатку CREATE TYPE для всіх ENUM (PostgreSQL потребує їх перед таблицями)
seen_enums = {}
for table in Base.metadata.sorted_tables:
    for column in table.columns:
        col_type = column.type
        if isinstance(col_type, SAEnum) and col_type.name:
            if col_type.name not in seen_enums:
                # Використовуємо .value (lowercase) а не .name (UPPERCASE)
                if col_type.enum_class is not None:
                    seen_enums[col_type.name] = [e.value for e in col_type.enum_class]
                else:
                    seen_enums[col_type.name] = list(col_type.enums)

for type_name, values in seen_enums.items():
    vals = ", ".join(f"'{v}'" for v in values)
    statements.append(f"CREATE TYPE {type_name} AS ENUM ({vals})")

# 2. CREATE TABLE
for table in Base.metadata.sorted_tables:
    stmt = str(CreateTable(table).compile(dialect=dialect)).strip()
    statements.append(stmt)

# 3. CREATE INDEX / UNIQUE INDEX (після таблиць)
from sqlalchemy.schema import CreateIndex
for table in Base.metadata.sorted_tables:
    for index in table.indexes:
        idx_sql = str(CreateIndex(index).compile(dialect=dialect)).strip()
        statements.append(idx_sql)

sql = ";\n\n".join(statements) + ";"

with open("schema.sql", "w", encoding="utf-8") as f:
    f.write(sql)

print("✅ schema.sql згенеровано!")
print()
print("Тепер виконай в терміналі:")
print()
print("  PowerShell:")
print("  Get-Content schema.sql | docker compose exec -T db psql -U postgres -d arete_db")
