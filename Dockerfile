# Используем официальный легкий образ Python 3.11
FROM python:3.11-slim

# Устанавливаем переменные окружения
# Отключаем создание .pyc файлов
ENV PYTHONDONTWRITEBYTECODE=1
# Вывод логов сразу в консоль без буферизации
ENV PYTHONUNBUFFERED=1
# Добавляем src в путь поиска модулей
ENV PYTHONPATH=/app/src

# Рабочая директория внутри контейнера
WORKDIR /app

# Устанавливаем системные зависимости (если потребуются)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Копируем файл зависимостей
COPY requirements.txt .

# Обновляем pip и устанавливаем зависимости
RUN pip install --upgrade pip && pip install --no-cache-dir -r requirements.txt

# Копируем весь проект в контейнер
COPY . .

# Команда запуска бота
CMD ["python", "src/main.py"]
