import contextlib
from typing import Any, AsyncGenerator, List

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Integer, String, Text, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# ==========================================
# 1. НАСТРОЙКА АСИНХРОННОЙ БАЗЫ ДАННЫХ (SQLAlchemy)
# ==========================================
DATABASE_URL = "sqlite+aiosqlite:///./cookbook.db"

# Создаем асинхронный движок для SQLite
engine = create_async_engine(DATABASE_URL, echo=True)
async_session = async_sessionmaker(engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


# Модель таблицы рецептов в БД
class RecipeModel(Base):
    __tablename__ = "recipes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    cooking_time: Mapped[int] = mapped_column(Integer, nullable=False)
    views_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    ingredients: Mapped[str] = mapped_column(Text, nullable=False)  # Храним как строку через запятую
    description: Mapped[str] = mapped_column(Text, nullable=False)


# Фабрика сессий (Dependency Injection) с аннотацией типов для mypy
async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with async_session() as session:
        yield session


# Асинхронное автоматическое создание таблиц при запуске приложения
@contextlib.asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield


# ==========================================
# 2. МОДЕЛИ ВАЛИДАЦИИ ДАННЫХ (Pydantic Схемы)
# ==========================================


# Схема для создания нового рецепта (POST /recipes)
class RecipeCreate(BaseModel):
    title: str = Field(..., min_length=2, max_length=100, description="Название блюда", examples=["Блины домашние"])
    cooking_time: int = Field(..., gt=0, description="Время приготовления в минутах", examples=[15])
    ingredients: List[str] = Field(
        ..., description="Список необходимых ингредиентов", examples=[["Мука", "Молоко", "Яйца"]]
    )
    description: str = Field(
        ..., description="Текстовое описание процесса", examples=["Смешать ингредиенты и обжарить на сковороде."]
    )


# Схема для Первого Экрана (Список рецептов в таблице с сортировкой)
class RecipeListResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int = Field(..., description="Уникальный ID рецепта")
    title: str = Field(..., description="Название блюда")
    views_count: int = Field(..., description="Количество просмотров детальной страницы")
    cooking_time: int = Field(..., description="Время приготовления (в минутах)")


# Схема для Второго Экрана (Детальная информация по конкретному рецепту)
class RecipeDetailResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    title: str = Field(..., description="Название блюда")
    cooking_time: int = Field(..., description="Время приготовления (в минутах)")
    ingredients: List[str] = Field(..., description="Список ингредиентов")
    description: str = Field(..., description="Текстовое пошаговое описание рецепта")


# ==========================================
# 3. РЕАЛИЗАЦИЯ ИНТЕРФЕЙСОВ API (FastAPI Маршруты)
# ==========================================
app = FastAPI(
    title="Кулинарная книга API",
    description="Документация асинхронного сервиса кулинарных рецептов"
                " для frontend-разработчиков.",
    version="1.0.0",
    lifespan=lifespan,
)


@app.post(
    "/recipes",
    response_model=RecipeListResponse,
    status_code=201,
    summary="Создать новый рецепт",
    description="Принимает детальные данные рецепта, сохраняет в базу данных "
                "и возвращает краткую информацию с присвоенным ID.",
)
async def create_recipe(recipe: RecipeCreate, db: AsyncSession = Depends(get_db)) -> RecipeModel:
    # Объединяем список ингредиентов в одну текстовую строку для хранения в SQLite
    ingredients_str = ", ".join(recipe.ingredients)

    db_recipe = RecipeModel(
        title=recipe.title,
        cooking_time=recipe.cooking_time,
        ingredients=ingredients_str,
        description=recipe.description,
    )
    db.add(db_recipe)
    await db.commit()
    await db.refresh(db_recipe)
    return db_recipe


@app.get(
    "/recipes",
    response_model=List[RecipeListResponse],
    summary="Получить список всех рецептов (Первый экран)",
    description="Возвращает рецепты, отсортированные по убыванию просмотров "
                "(популярности). При равных просмотрах — сортирует по возрастанию времени.",
)
async def get_all_recipes(db: AsyncSession = Depends(get_db)) -> Any:
    # Сортировка по ТЗ: views_count DESC, cooking_time ASC
    query = select(RecipeModel).order_by(RecipeModel.views_count.desc(), RecipeModel.cooking_time.asc())
    result = await db.execute(query)
    return result.scalars().all()


@app.get(
    "/recipes/{recipe_id}",
    response_model=RecipeDetailResponse,
    summary="Получить детальную информацию о рецепте (Второй экран)",
    description="Ищет рецепт по ID, увеличивает счётчик просмотров "
                "(views_count) на 1 и отдаёт подробное описание с ингредиентами.",
)
async def get_recipe_detail(recipe_id: int, db: AsyncSession = Depends(get_db)) -> RecipeDetailResponse:
    query = select(RecipeModel).where(RecipeModel.id == recipe_id)
    result = await db.execute(query)
    db_recipe = result.scalar()

    if db_recipe is None:
        raise HTTPException(status_code=404, detail="Рецепт с указанным ID не найден")

    # Инкремент счётчика просмотров при открытии детального экрана
    db_recipe.views_count += 1
    await db.commit()
    await db.refresh(db_recipe)

    # Превращаем сохраненную строку ингредиентов обратно в список для фронтенда
    ingredients_list = [i.strip() for i in db_recipe.ingredients.split(",") if i.strip()]

    return RecipeDetailResponse(
        title=db_recipe.title,
        cooking_time=db_recipe.cooking_time,
        ingredients=ingredients_list,
        description=db_recipe.description,
    )


if __name__ == "__main__":
    import uvicorn

    # Запускаем локальный веб-сервер uvicorn на порту 8000 с автоперезапуском при изменении кода
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)
